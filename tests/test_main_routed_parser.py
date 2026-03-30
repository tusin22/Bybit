from __future__ import annotations

from src.bybit.execution_client import BybitExecutionClientError
from src.main import RoutedSignalParser
from src.models.execution_plan import ExecutionPlan
from src.models.signal import Signal


class FakeRouter:
    def enrich_with_bybit_validation(self, signal: Signal) -> Signal:
        signal.entry_eligible = True
        signal.entry_validation_reason = "ok"
        signal.current_price = 64000.0
        signal.instrument_status = "Trading"
        signal.instrument_tick_size = "0.10"
        signal.instrument_qty_step = "0.001"
        return signal


class FakePlanner:
    def build_plan(self, *, signal: Signal) -> ExecutionPlan:
        return ExecutionPlan(
            symbol=signal.symbol,
            category="linear",
            planned_entry_side="Buy",
            reference_price=64000.0,
            normalized_entry_min=63900.0,
            normalized_entry_max=64100.0,
            normalized_stop_loss=63000.0,
            normalized_take_profits=[65000.0, 66000.0, 67000.0, 68000.0],
            operational_intent=signal.operational_intent,
            planned_quantity=86.9,
            tick_size="0.10",
            qty_step="0.001",
            instrument_status="Trading",
            eligible=True,
            ineligibility_reason=None,
        )


class RejectingExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        raise BybitExecutionClientError("Falha Bybit em place_order: retCode=10001 retMsg=Qty invalid")


class FakeSignalParser:
    def parse(self, raw_text: str) -> Signal:
        return Signal(
            symbol="BTCUSDT",
            side="LONG",
            entry_min=63900.0,
            entry_max=64100.0,
            take_profits=[65000.0, 66000.0, 67000.0, 68000.0],
            stop_loss=63000.0,
            raw_text=raw_text,
        )


def test_routed_signal_parser_handles_bybit_rejection_without_raising() -> None:
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=RejectingExecutor(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, Signal)
    assert result.entry_eligible is False
    assert "callback mantido ativo" in (result.entry_validation_reason or "")
