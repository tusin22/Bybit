from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.analysis.auto_signal_engine import ClosedCandle
from src.config import Settings
from src.main_auto import AutoAnalysisRunner


@dataclass
class _FakePlan:
    symbol: str = "BTCUSDT"


class _FakeRouter:
    def enrich_with_bybit_validation(self, signal):
        return signal


class _FakePlanner:
    def __init__(self, settings):
        self.settings = settings

    def build_plan(self, signal):
        return _FakePlan(symbol=signal.symbol)


class _FakeExecutor:
    def __init__(self, settings, execution_client, private_ws_monitor=None):
        self.calls = 0
        self.flags = (settings.dry_run, settings.enable_order_execution)

    def execute_entry(self, plan):
        self.calls += 1
        raise AssertionError("execute_entry não deve ser chamado neste teste")

    def set_runtime_flags(self, *, dry_run: bool, enable_order_execution: bool) -> None:
        self.flags = (dry_run, enable_order_execution)


class _FakeJournal:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir

    def write(self, symbol: str, journal_payload: dict):
        return self.base_dir / f"{symbol}.json"


class _FakeStateStore:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.last_state = None

    def save(self, *, state):
        self.last_state = state


class _FakeFeed:
    created: list["_FakeFeed"] = []

    def __init__(self, *, testnet: bool, symbol: str, interval: str):
        self.testnet = testnet
        self.symbol = symbol
        self.interval = interval
        self.stop_called = False
        self.subscribe_count = 0
        _FakeFeed.created.append(self)

    def bootstrap_closed_candles(self, *, limit: int = 300):
        return []

    def subscribe_closed_kline(self, *, on_closed_candle):
        self.subscribe_count += 1

    def stop(self):
        self.stop_called = True


class _FakeEngine:
    def __init__(self):
        self.calls = 0

    def maybe_build_signal(self, *, candles, last_processed_candle, cooldown_until_candle):
        self.calls += 1
        return None, "Sem gatilho técnico fechado.", cooldown_until_candle


class _DummyClient:
    def __init__(self, *args, **kwargs):
        pass


def _settings() -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        dry_run=True,
        telegram_api_id=0,
        telegram_api_hash="",
        telegram_session_name="",
        telegram_source_chat="",
        bybit_api_key="",
        bybit_api_secret="",
        bybit_testnet=True,
        enable_order_execution=False,
        execution_sizing_mode="fixed_notional_usdt",
        execution_fixed_notional_usdt=25.0,
        execution_fixed_qty=0.0,
        tp1_percent=50.0,
        tp2_percent=20.0,
        tp3_percent=20.0,
        tp4_percent=10.0,
        signal_source="auto_analysis",
        auto_analysis_enabled=True,
    )


def _build_runner(monkeypatch, tmp_path: Path) -> AutoAnalysisRunner:
    control_dir = tmp_path / "runtime" / "control"
    monkeypatch.setattr("src.main_auto.CONTROL_STATE_FILE", control_dir / "control_state.json")
    monkeypatch.setattr("src.main_auto.RUNTIME_CONFIG_FILE", control_dir / "runtime_config.json")
    monkeypatch.setattr("src.main_auto.load_settings", _settings)
    monkeypatch.setattr("src.main_auto.validate_settings_for_signal_source", lambda settings: None)
    monkeypatch.setattr("src.main_auto.configure_logging", lambda level: None)
    monkeypatch.setattr("src.main_auto.SignalRouter", lambda bybit_client: _FakeRouter())
    monkeypatch.setattr("src.main_auto.ExecutionPlanner", _FakePlanner)
    monkeypatch.setattr("src.main_auto.TradeExecutor", _FakeExecutor)
    monkeypatch.setattr("src.main_auto.ExecutionJournalService", _FakeJournal)
    monkeypatch.setattr("src.main_auto.AutoAnalysisStateStore", _FakeStateStore)
    monkeypatch.setattr("src.main_auto.BybitMarketFeed", _FakeFeed)
    monkeypatch.setattr("src.main_auto.BybitReadOnlyClient", _DummyClient)
    monkeypatch.setattr("src.main_auto.BybitExecutionClient", _DummyClient)
    _FakeFeed.created.clear()
    return AutoAnalysisRunner()


def test_backend_stopped_does_not_analyze(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    runner._engine = _FakeEngine()
    runner._candles = []

    runner._control_state_file.parent.mkdir(parents=True, exist_ok=True)
    runner._control_state_file.write_text(json.dumps({"desiredRunState": "stopped"}), encoding="utf-8")
    runner._reload_runtime_controls()

    candle = ClosedCandle(1, 1, 1, 1, 1, 1, True)
    runner._handle_closed_candle(candle)

    assert runner._engine.calls == 0
    assert runner._state_store.last_state.effectiveRunState == "stopped"


def test_without_control_state_file_runner_starts_stopped(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)

    assert runner._desired_run_state == "stopped"
    assert runner._effective_run_state == "stopped"


def test_backend_resumed_returns_to_analysis(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    runner._engine = _FakeEngine()
    runner._candles = []

    runner._control_state_file.parent.mkdir(parents=True, exist_ok=True)
    runner._control_state_file.write_text(json.dumps({"desiredRunState": "running"}), encoding="utf-8")
    runner._reload_runtime_controls()

    candle = ClosedCandle(1, 1, 1, 1, 1, 1, True)
    runner._handle_closed_candle(candle)

    assert runner._engine.calls == 1


def test_signal_source_telegram_blocks_main_auto_analysis(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    runner._engine = _FakeEngine()
    runner._candles = []

    runner._control_state_file.parent.mkdir(parents=True, exist_ok=True)
    runner._control_state_file.write_text(json.dumps({"desiredRunState": "running"}), encoding="utf-8")
    runner._runtime_config_file.write_text(
        json.dumps({"signalSource": "telegram", "autoAnalysisEnabled": True}),
        encoding="utf-8",
    )
    runner._reload_runtime_controls()
    assert runner._effective_run_state == "stopped"
    assert "signalSource" in str(runner._stop_reason)

    candle = ClosedCandle(1, 1, 1, 1, 1, 1, True)
    runner._handle_closed_candle(candle)

    assert runner._effective_run_state == "stopped"
    assert runner._state_store.last_state.effectiveRunState == "stopped"


def test_auto_analysis_disabled_blocks_main_auto_analysis(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    runner._engine = _FakeEngine()
    runner._candles = []

    runner._control_state_file.parent.mkdir(parents=True, exist_ok=True)
    runner._control_state_file.write_text(json.dumps({"desiredRunState": "running"}), encoding="utf-8")
    runner._runtime_config_file.write_text(
        json.dumps({"signalSource": "auto_analysis", "autoAnalysisEnabled": False}),
        encoding="utf-8",
    )
    runner._reload_runtime_controls()
    assert runner._effective_run_state == "stopped"
    assert "autoAnalysisEnabled" in str(runner._stop_reason)

    candle = ClosedCandle(1, 1, 1, 1, 1, 1, True)
    runner._handle_closed_candle(candle)

    assert runner._effective_run_state == "stopped"
    assert runner._state_store.last_state.effectiveRunState == "stopped"


def test_runtime_config_valid_overrides_base_config(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)

    runner._runtime_config_file.parent.mkdir(parents=True, exist_ok=True)
    runner._runtime_config_file.write_text(
        json.dumps(
            {
                "signalSource": "auto_analysis",
                "autoAnalysisEnabled": True,
                "symbol": "ETHUSDT",
                "interval": "15",
                "emaShort": 8,
                "emaMid": 21,
                "useVolumeFilter": True,
                "volumeMultiplier": 1.2,
                "useAdxFilter": True,
                "adxLen": 10,
                "adxThreshold": 20.0,
                "cooldownCandles": 2,
                "dryRun": False,
                "enableOrderExecution": True,
                "updatedAt": "2026-04-07T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    runner._reload_runtime_controls()

    assert runner._effective_runtime["symbol"] == "ETHUSDT"
    assert runner._effective_runtime["interval"] == "15"
    assert runner._executor.flags == (False, True)


def test_runtime_config_invalid_keeps_last_valid(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    runner._runtime_config_file.parent.mkdir(parents=True, exist_ok=True)
    runner._runtime_config_file.write_text(json.dumps({"symbol": "ETHUSDT", "interval": "15"}), encoding="utf-8")
    runner._reload_runtime_controls()

    runner._runtime_config_file.write_text("{bad", encoding="utf-8")
    runner._reload_runtime_controls()

    assert runner._effective_runtime["symbol"] == "ETHUSDT"
    assert runner._last_config_reload_status == "invalid_kept_last_valid"


def test_symbol_or_interval_change_refreshes_feed(monkeypatch, tmp_path: Path) -> None:
    runner = _build_runner(monkeypatch, tmp_path)
    initial_feed = _FakeFeed.created[0]

    runner._runtime_config_file.parent.mkdir(parents=True, exist_ok=True)
    runner._runtime_config_file.write_text(
        json.dumps({"symbol": "ETHUSDT", "interval": "15"}),
        encoding="utf-8",
    )
    runner._reload_runtime_controls()

    assert initial_feed.stop_called is True
    assert len(_FakeFeed.created) == 2
    assert _FakeFeed.created[-1].subscribe_count == 1
