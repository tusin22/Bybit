from __future__ import annotations

import logging
import json
import time
from pathlib import Path
from typing import Any

from src.analysis.auto_signal_engine import AutoAnalysisConfig, AutoSignalEngine, ClosedCandle
from src.analysis.bybit_market_feed import AutoAnalysisState, AutoAnalysisStateStore, BybitMarketFeed
from src.bybit import BybitExecutionClient, BybitReadOnlyClient
from src.config import load_settings, validate_settings_for_signal_source
from src.main import _build_journal_summary, _utc_now_iso
from src.models.execution_result import ExecutionResult
from src.models.signal import Signal
from src.services.execution_journal import ExecutionJournalService
from src.services.execution_planner import ExecutionPlanner
from src.services.signal_router import SignalRouter
from src.services.trade_executor import TradeExecutor
from src.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)
CONTROL_STATE_FILE = Path("runtime/control/control_state.json")
RUNTIME_CONFIG_FILE = Path("runtime/control/runtime_config.json")



class AutoAnalysisRunner:
    def __init__(self) -> None:
        self._settings = load_settings()
        validate_settings_for_signal_source(self._settings)
        configure_logging(self._settings.log_level)

        self._router = SignalRouter(
            bybit_client=BybitReadOnlyClient(
                api_key=self._settings.bybit_api_key,
                api_secret=self._settings.bybit_api_secret,
                testnet=self._settings.bybit_testnet,
            )
        )
        self._planner = ExecutionPlanner(settings=self._settings)
        self._exec_client = BybitExecutionClient(
            api_key=self._settings.bybit_api_key,
            api_secret=self._settings.bybit_api_secret,
            testnet=self._settings.bybit_testnet,
        )
        self._executor = TradeExecutor(
            settings=self._settings,
            execution_client=self._exec_client,
            private_ws_monitor=None,
        )
        self._journal = ExecutionJournalService(base_dir=Path("runtime/journal"))
        self._state_store = AutoAnalysisStateStore(state_file=Path("runtime/state/auto_analysis_state.json"))
        self._control_state_file = CONTROL_STATE_FILE
        self._runtime_config_file = RUNTIME_CONFIG_FILE

        self._desired_run_state = "stopped"
        self._effective_run_state = "stopped"
        self._config_source = ".env"
        self._config_updated_at: str | None = None
        self._last_config_reload_at: str | None = None
        self._last_config_reload_status = "not_loaded"
        self._stop_reason: str | None = "Parado por padrão seguro até leitura de controle runtime."
        self._effective_runtime = {
            "signalSource": self._settings.signal_source,
            "autoAnalysisEnabled": self._settings.auto_analysis_enabled,
            "symbol": self._settings.auto_analysis_symbol,
            "interval": self._settings.auto_analysis_interval,
            "emaShort": self._settings.auto_analysis_ema_short,
            "emaMid": self._settings.auto_analysis_ema_mid,
            "useVolumeFilter": self._settings.auto_analysis_use_volume_filter,
            "volumeMultiplier": self._settings.auto_analysis_volume_multiplier,
            "useAdxFilter": self._settings.auto_analysis_use_adx_filter,
            "adxLen": self._settings.auto_analysis_adx_len,
            "adxThreshold": self._settings.auto_analysis_adx_threshold,
            "cooldownCandles": self._settings.auto_analysis_cooldown_candles,
            "dryRun": self._settings.dry_run,
            "enableOrderExecution": self._settings.enable_order_execution,
        }
        self._engine = AutoSignalEngine(config=self._build_engine_config())
        self._feed = BybitMarketFeed(
            testnet=self._settings.bybit_testnet,
            symbol=str(self._effective_runtime["symbol"]),
            interval=str(self._effective_runtime["interval"]),
        )
        self._candles: list[ClosedCandle] = []
        self._last_processed_candle: int | None = None
        self._cooldown_until_candle: int | None = None
        self._last_price: float | None = None
        self._reload_runtime_controls()

    def run(self) -> int:
        self._candles = self._feed.bootstrap_closed_candles(limit=300)
        if self._candles:
            self._last_price = self._candles[-1].close_price
        self._write_state(analyzer_status="running")

        def _on_closed_candle(candle: ClosedCandle) -> None:
            self._candles.append(candle)
            self._candles = self._candles[-500:]
            self._last_price = candle.close_price
            self._handle_closed_candle(candle)

        self._feed.subscribe_closed_kline(on_closed_candle=_on_closed_candle)
        try:
            while True:
                self._reload_runtime_controls()
                time.sleep(1.0)
        except KeyboardInterrupt:
            LOGGER.info("Encerrando auto_analysis...")
            self._feed.stop()
            self._write_state(analyzer_status="stopped")
            return 0

    def _handle_closed_candle(self, candle: ClosedCandle) -> None:
        if self._effective_run_state == "stopped":
            self._write_state(
                analyzer_status="stopped",
                last_signal_reason=self._stop_reason or "Parado por comando do painel local.",
            )
            return

        signal, reason, next_cooldown = self._engine.maybe_build_signal(
            candles=self._candles,
            last_processed_candle=self._last_processed_candle,
            cooldown_until_candle=self._cooldown_until_candle,
        )
        self._cooldown_until_candle = next_cooldown
        if signal is None:
            self._write_journal_ignored(candle=candle, reason=reason)
            self._write_state(analyzer_status="idle", last_signal_reason=reason)
            return

        protection_reason = self._check_operational_protections(symbol=signal.symbol)
        if protection_reason:
            self._last_processed_candle = candle.start_ms
            self._write_journal_ignored(candle=candle, reason=protection_reason, signal=signal)
            self._write_state(analyzer_status="blocked", last_signal_side=signal.side, last_signal_reason=protection_reason)
            return

        enriched = self._router.enrich_with_bybit_validation(signal)
        plan = self._planner.build_plan(signal=enriched)
        result = self._executor.execute_entry(plan=plan)
        self._last_processed_candle = candle.start_ms
        self._write_trade_journal(signal=enriched, result=result, reason=reason, candle=candle)
        self._write_state(
            analyzer_status="executed",
            last_signal_side=signal.side,
            last_signal_reason=reason,
            last_execution_attempted=result.order_attempted,
            last_execution_trade_status=_build_journal_summary(signal=enriched, result=result, journal_status="completed", safe_failure_reason=None)["tradeStatus"],
            open_position_detected=False,
        )

    def _check_operational_protections(self, *, symbol: str) -> str | None:
        if not self._settings.auto_analysis_single_position_only:
            return None

        try:
            positions = self._exec_client.extract_position_list(
                self._exec_client.get_positions(category="linear", symbol=symbol)
            )
            has_open_position = any(float(p.get("size", 0)) > 0 for p in positions)
            if has_open_position:
                return "Sinal ignorado: posição aberta detectada no símbolo."

            open_orders = self._exec_client.extract_order_list(
                self._exec_client.get_open_orders_for_symbol(category="linear", symbol=symbol, limit=50)
            )
            active = [o for o in open_orders if str(o.get("orderStatus", "")) in {"New", "Created", "PartiallyFilled", "Untriggered", "Triggered"}]
            if active:
                return "Sinal ignorado: execução/cleanup em andamento no símbolo (ordens ativas)."
        except Exception as exc:
            return f"Sinal ignorado por proteção: falha ao verificar posição/ordens ({exc})."
        return None

    def _write_journal_ignored(self, *, candle: ClosedCandle, reason: str, signal: Signal | None = None) -> None:
        payload = {
            "journalVersion": 2,
            "recordedAt": _utc_now_iso(),
            "status": "completed",
            "tradeStatus": "blocked",
            "source": "auto_analysis",
            "rawText": signal.raw_text if signal else f"AUTO_ANALYSIS {self._settings.auto_analysis_symbol}",
            "signal": signal.to_dict() if signal else None,
            "plan": None,
            "execution": {"result": None, "ids": {"entryOrderId": None, "entryOrderLinkId": None, "registeredTakeProfits": []}},
            "monitor": {"status": "not_started", "websocketStarted": False, "restFallbackUsed": False, "finalDecisionSource": "auto_analysis", "finalDecisionReason": reason},
            "cleanup": {"attempted": False, "status": "not_attempted", "remainingRegisteredTpCount": 0},
            "errors": [{"stage": "auto_analysis", "type": "signal_ignored", "message": reason}],
            "summary": {"tradeStatus": "blocked", "success": False, "successOrFailureReason": reason, "symbol": self._settings.auto_analysis_symbol, "side": signal.side if signal else None},
            "autoAnalysis": {"closedCandleStart": candle.start_ms, "reason": reason},
        }
        self._journal.write(symbol=self._settings.auto_analysis_symbol, journal_payload=payload)

    def _write_trade_journal(self, *, signal: Signal, result: ExecutionResult, reason: str, candle: ClosedCandle) -> None:
        summary = _build_journal_summary(signal=signal, result=result, journal_status="completed", safe_failure_reason=None)
        payload = {
            "journalVersion": 2,
            "recordedAt": _utc_now_iso(),
            "status": "completed",
            "tradeStatus": summary["tradeStatus"],
            "source": "auto_analysis",
            "rawText": signal.raw_text,
            "signal": signal.to_dict(),
            "plan": None,
            "execution": {
                "result": result.to_dict(),
                "ids": {
                    "entryOrderId": summary["entryOrderId"],
                    "entryOrderLinkId": summary["entryOrderLinkId"],
                    "registeredTakeProfits": result.registered_take_profit_orders,
                },
            },
            "monitor": {
                "status": summary["monitorStatus"],
                "websocketStarted": result.monitor_websocket_started,
                "restFallbackUsed": result.monitor_rest_fallback_used,
                "finalDecisionSource": summary["finalDecisionSource"],
                "finalDecisionReason": result.monitor_final_decision_reason,
            },
            "cleanup": {
                "attempted": result.cleanup_attempted,
                "status": summary["cleanupStatus"],
                "remainingRegisteredTpCount": result.cleanup_remaining_registered_tp_count,
            },
            "errors": [],
            "summary": summary,
            "autoAnalysis": {"closedCandleStart": candle.start_ms, "reason": reason},
        }
        self._journal.write(symbol=signal.symbol, journal_payload=payload)

    def _build_engine_config(self) -> AutoAnalysisConfig:
        return AutoAnalysisConfig(
            symbol=str(self._effective_runtime["symbol"]),
            interval=str(self._effective_runtime["interval"]),
            ema_short=int(self._effective_runtime["emaShort"]),
            ema_mid=int(self._effective_runtime["emaMid"]),
            rsi_len=self._settings.auto_analysis_rsi_len,
            macd_fast=self._settings.auto_analysis_macd_fast,
            macd_slow=self._settings.auto_analysis_macd_slow,
            macd_signal=self._settings.auto_analysis_macd_signal,
            use_volume_filter=bool(self._effective_runtime["useVolumeFilter"]),
            volume_multiplier=float(self._effective_runtime["volumeMultiplier"]),
            adx_len=int(self._effective_runtime["adxLen"]),
            use_adx_filter=bool(self._effective_runtime["useAdxFilter"]),
            adx_threshold=float(self._effective_runtime["adxThreshold"]),
            cooldown_candles=int(self._effective_runtime["cooldownCandles"]),
        )

    def _reload_runtime_controls(self) -> None:
        self._last_config_reload_at = _utc_now_iso()
        self._reload_control_state()
        self._reload_runtime_config()
        self._resolve_operational_state()

    def _reload_control_state(self) -> None:
        if not self._control_state_file.exists():
            self._desired_run_state = "stopped"
            return
        try:
            payload = json.loads(self._control_state_file.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("control_state.json deve conter um objeto.")
            desired = payload.get("desiredRunState")
            if desired not in {"running", "stopped"}:
                raise ValueError("desiredRunState inválido (use running/stopped).")
            self._desired_run_state = str(desired)
        except Exception as exc:
            self._desired_run_state = "stopped"
            LOGGER.warning("Falha ao recarregar control_state.json; aplicando fallback seguro em stopped. reason=%s", exc)

    def _reload_runtime_config(self) -> None:
        if not self._runtime_config_file.exists():
            self._last_config_reload_status = "ok_env_fallback"
            return

        try:
            payload = json.loads(self._runtime_config_file.read_text(encoding="utf-8"))
            runtime_config = self._validate_runtime_config(payload)
        except Exception as exc:
            self._last_config_reload_status = "invalid_kept_last_valid"
            LOGGER.warning("runtime_config.json inválido; mantendo última configuração válida. reason=%s", exc)
            return

        previous_symbol = str(self._effective_runtime["symbol"])
        previous_interval = str(self._effective_runtime["interval"])
        self._effective_runtime.update(runtime_config)
        self._config_source = "runtime/control/runtime_config.json"
        self._config_updated_at = str(payload.get("updatedAt") or _utc_now_iso())
        self._engine = AutoSignalEngine(config=self._build_engine_config())
        self._executor.set_runtime_flags(
            dry_run=bool(self._effective_runtime["dryRun"]),
            enable_order_execution=bool(self._effective_runtime["enableOrderExecution"]),
        )
        self._last_config_reload_status = "ok_runtime_config"

        current_symbol = str(self._effective_runtime["symbol"])
        current_interval = str(self._effective_runtime["interval"])
        if current_symbol != previous_symbol or current_interval != previous_interval:
            self._refresh_feed()

    def _resolve_operational_state(self) -> None:
        if self._desired_run_state != "running":
            self._effective_run_state = "stopped"
            self._stop_reason = "Parado por comando do painel local."
            return

        signal_source = str(self._effective_runtime["signalSource"]).strip().lower()
        if signal_source != "auto_analysis":
            self._effective_run_state = "stopped"
            self._stop_reason = "Parado: signalSource diferente de auto_analysis no runtime."
            return

        if not bool(self._effective_runtime["autoAnalysisEnabled"]):
            self._effective_run_state = "stopped"
            self._stop_reason = "Parado: autoAnalysisEnabled=false no runtime."
            return

        self._effective_run_state = "running"
        self._stop_reason = None

    def _refresh_feed(self) -> None:
        try:
            self._feed.stop()
        except Exception:
            pass
        self._feed = BybitMarketFeed(
            testnet=self._settings.bybit_testnet,
            symbol=str(self._effective_runtime["symbol"]),
            interval=str(self._effective_runtime["interval"]),
        )
        self._candles = self._feed.bootstrap_closed_candles(limit=300)
        self._last_processed_candle = None
        self._cooldown_until_candle = None
        if self._candles:
            self._last_price = self._candles[-1].close_price

        def _on_closed_candle(candle: ClosedCandle) -> None:
            self._candles.append(candle)
            self._candles = self._candles[-500:]
            self._last_price = candle.close_price
            self._handle_closed_candle(candle)

        self._feed.subscribe_closed_kline(on_closed_candle=_on_closed_candle)
        LOGGER.info(
            "Feed do auto_analysis reiniciado após mudança de symbol/interval. symbol=%s interval=%s",
            self._effective_runtime["symbol"],
            self._effective_runtime["interval"],
        )

    def _validate_runtime_config(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("runtime_config.json deve conter um objeto.")

        parsed: dict[str, Any] = {}
        parsed["signalSource"] = str(payload.get("signalSource", self._effective_runtime["signalSource"])).strip().lower()
        parsed["autoAnalysisEnabled"] = bool(payload.get("autoAnalysisEnabled", self._effective_runtime["autoAnalysisEnabled"]))
        parsed["symbol"] = str(payload.get("symbol", self._effective_runtime["symbol"])).strip().upper()
        parsed["interval"] = str(payload.get("interval", self._effective_runtime["interval"])).strip()
        parsed["emaShort"] = int(payload.get("emaShort", self._effective_runtime["emaShort"]))
        parsed["emaMid"] = int(payload.get("emaMid", self._effective_runtime["emaMid"]))
        parsed["useVolumeFilter"] = bool(payload.get("useVolumeFilter", self._effective_runtime["useVolumeFilter"]))
        parsed["volumeMultiplier"] = float(payload.get("volumeMultiplier", self._effective_runtime["volumeMultiplier"]))
        parsed["useAdxFilter"] = bool(payload.get("useAdxFilter", self._effective_runtime["useAdxFilter"]))
        parsed["adxLen"] = int(payload.get("adxLen", self._effective_runtime["adxLen"]))
        parsed["adxThreshold"] = float(payload.get("adxThreshold", self._effective_runtime["adxThreshold"]))
        parsed["cooldownCandles"] = int(payload.get("cooldownCandles", self._effective_runtime["cooldownCandles"]))
        parsed["dryRun"] = bool(payload.get("dryRun", self._effective_runtime["dryRun"]))
        parsed["enableOrderExecution"] = bool(payload.get("enableOrderExecution", self._effective_runtime["enableOrderExecution"]))

        if parsed["interval"] == "":
            raise ValueError("interval vazio.")
        if parsed["emaShort"] <= 0 or parsed["emaMid"] <= 0:
            raise ValueError("emaShort/emaMid devem ser > 0.")
        return parsed

    def _write_state(
        self,
        *,
        analyzer_status: str,
        last_signal_side: str | None = None,
        last_signal_reason: str | None = None,
        last_execution_attempted: bool = False,
        last_execution_trade_status: str | None = None,
        open_position_detected: bool = False,
    ) -> None:
        state = AutoAnalysisState(
            symbol=self._settings.auto_analysis_symbol,
            interval=self._settings.auto_analysis_interval,
            lastClosedCandleTime=self._last_processed_candle,
            lastPrice=self._last_price,
            analyzerStatus=analyzer_status,
            lastSignalSide=last_signal_side,
            lastSignalReason=last_signal_reason,
            lastExecutionAttempted=last_execution_attempted,
            lastExecutionTradeStatus=last_execution_trade_status,
            cooldownUntilCandle=self._cooldown_until_candle,
            openPositionDetected=open_position_detected,
            desiredRunState=self._desired_run_state,
            effectiveRunState=self._effective_run_state,
            configSource=self._config_source,
            configUpdatedAt=self._config_updated_at,
            lastConfigReloadAt=self._last_config_reload_at,
            lastConfigReloadStatus=self._last_config_reload_status,
            stopReason=self._stop_reason,
            updatedAt=_utc_now_iso(),
        )
        self._state_store.save(state=state)


def main() -> int:
    runner = AutoAnalysisRunner()
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
