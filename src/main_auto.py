from __future__ import annotations

import logging
import time
from pathlib import Path

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

        self._engine = AutoSignalEngine(
            config=AutoAnalysisConfig(
                symbol=self._settings.auto_analysis_symbol,
                interval=self._settings.auto_analysis_interval,
                ema_short=self._settings.auto_analysis_ema_short,
                ema_mid=self._settings.auto_analysis_ema_mid,
                rsi_len=self._settings.auto_analysis_rsi_len,
                macd_fast=self._settings.auto_analysis_macd_fast,
                macd_slow=self._settings.auto_analysis_macd_slow,
                macd_signal=self._settings.auto_analysis_macd_signal,
                use_volume_filter=self._settings.auto_analysis_use_volume_filter,
                volume_multiplier=self._settings.auto_analysis_volume_multiplier,
                adx_len=self._settings.auto_analysis_adx_len,
                use_adx_filter=self._settings.auto_analysis_use_adx_filter,
                adx_threshold=self._settings.auto_analysis_adx_threshold,
                cooldown_candles=self._settings.auto_analysis_cooldown_candles,
            )
        )
        self._feed = BybitMarketFeed(
            testnet=self._settings.bybit_testnet,
            symbol=self._settings.auto_analysis_symbol,
            interval=self._settings.auto_analysis_interval,
        )
        self._candles: list[ClosedCandle] = []
        self._last_processed_candle: int | None = None
        self._cooldown_until_candle: int | None = None
        self._last_price: float | None = None

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
                time.sleep(1.0)
        except KeyboardInterrupt:
            LOGGER.info("Encerrando auto_analysis...")
            self._feed.stop()
            self._write_state(analyzer_status="stopped")
            return 0

    def _handle_closed_candle(self, candle: ClosedCandle) -> None:
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
            updatedAt=_utc_now_iso(),
        )
        self._state_store.save(state=state)


def main() -> int:
    runner = AutoAnalysisRunner()
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
