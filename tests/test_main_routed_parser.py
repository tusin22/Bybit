from __future__ import annotations

from src.bybit.execution_client import BybitExecutionClientError
from src.main import RoutedSignalParser
from src.models.execution_plan import ExecutionPlan
from src.models.execution_result import ExecutionResult
from src.models.signal import Signal


class FakeRouter:
    def enrich_with_bybit_validation(self, signal: Signal) -> Signal:
        signal.entry_eligible = True
        signal.entry_validation_reason = "ok"
        signal.current_price = 64000.0
        signal.instrument_status = "Trading"
        signal.instrument_tick_size = "0.10"
        signal.instrument_qty_step = "0.001"
        signal.instrument_min_order_qty = "0.001"
        signal.instrument_min_notional_value = "5"
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
            min_order_qty="0.001",
            min_notional_value="5",
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


class RecordingJournalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def write(self, *, symbol: str, journal_payload: dict[str, object]):
        self.calls.append({"symbol": symbol, "payload": journal_payload})
        return "/tmp/runtime/journal/mock.json"


class RaisingJournalService:
    def write(self, *, symbol: str, journal_payload: dict[str, object]):
        raise OSError("disk full")


class ConfirmationFailingExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        raise BybitExecutionClientError("Falha Bybit em get_order_history: retCode=10006 retMsg=system busy")


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


def test_routed_signal_parser_keeps_callback_alive_when_confirmation_fails() -> None:
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=ConfirmationFailingExecutor(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, Signal)
    assert result.entry_eligible is False
    assert "callback mantido ativo" in (result.entry_validation_reason or "")


class StopLossFailingButSafeExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            entry_status="confirmed",
            order_attempted=True,
            order_sent=True,
            order_confirmed=True,
            stop_loss_attempted=True,
            stop_loss_configured=False,
            stop_loss_status="failed",
            stop_loss_reason="Falha Bybit em set_trading_stop: retCode=110011 retMsg=SL invalid",
            take_profit_attempted=True,
            take_profit_status="partial",
            take_profit_attempted_count=4,
            take_profit_accepted_count=2,
            take_profit_failed_count=2,
            take_profit_failures=[{"tpIndex": 2, "reason": "tp fail"}],
            registered_take_profit_orders=[{"tpIndex": 1, "orderId": "tp-1", "orderLinkId": "tp1-btcusdt-abc"}],
            take_profit_reconciliation_summary={},
            cleanup_attempted=True,
            cleanup_status="partial",
            cleanup_position_exists=False,
            cleanup_position_closed_within_window=True,
            cleanup_window_attempts=2,
            cleanup_remaining_registered_tp_count=2,
            cleanup_missing_registered_tp_count=0,
            cleanup_found_count=2,
            cleanup_cancelled_count=1,
            cleanup_failed_count=1,
            cleanup_failure_reasons=[{"orderId": "tp-2", "reason": "cancel failed"}],
            monitor_started=True,
            monitor_websocket_started=True,
            monitor_websocket_connected=True,
            monitor_websocket_authenticated=True,
            monitor_websocket_subscribed=True,
            monitor_websocket_execution_stream_subscribed=True,
            monitor_websocket_execution_events_relevant_count=2,
            monitor_websocket_execution_fill_summary={"eventsCount": 2, "hasPartialOrTotalFill": True},
            monitor_rest_fallback_used=False,
            monitor_attempts=2,
            monitor_position_closed_within_window=True,
            monitor_cleanup_completed_within_window=False,
            monitor_remaining_execution_orders=[{"tpIndex": 2, "orderId": "tp-2", "orderStatus": "New"}],
            monitor_status="started_failed_with_safe_fallback",
            monitor_final_decision_source="websocket_position",
            monitor_final_decision_reason="position_closed_via_private_ws",
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status="confirmed",
            confirmation_reason="orderStatus confirmado via REST",
            bybit_response_summary={"orderId": "abc-123", "orderLinkId": "entry-btc", "successReason": "ok"},
            stop_loss_response_summary={"requestAccepted": False},
            take_profit_response_summaries=[],
            client_order_context="entry-btc",
            success=False,
        )


def test_routed_signal_parser_keeps_callback_alive_when_stop_loss_fails() -> None:
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=StopLossFailingButSafeExecutor(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, ExecutionResult)
    assert result.order_confirmed is True
    assert result.stop_loss_status == "failed"


def test_routed_signal_parser_sets_closed_with_failures_when_position_closed_with_failures() -> None:
    journal = RecordingJournalService()
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=StopLossFailingButSafeExecutor(),
        journal_service=journal,
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, ExecutionResult)
    assert len(journal.calls) == 1
    payload = journal.calls[0]["payload"]
    assert payload["status"] == "completed"
    assert payload["tradeStatus"] == "closed_with_failures"
    assert payload["rawText"] == "BTCUSDT LONG"
    assert payload["plan"] is not None
    assert payload["execution"]["result"] is not None
    assert payload["execution"]["ids"]["entryOrderId"] == "abc-123"
    assert payload["summary"]["tradeStatus"] == "closed_with_failures"
    assert payload["summary"]["monitorStatus"] == "started_failed_with_safe_fallback"


def test_routed_signal_parser_writes_partial_journal_on_safe_failure() -> None:
    journal = RecordingJournalService()
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=RejectingExecutor(),
        journal_service=journal,
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, Signal)
    assert len(journal.calls) == 1
    payload = journal.calls[0]["payload"]
    assert payload["status"] == "safe_failure"
    assert payload["tradeStatus"] == "safe_failure"
    assert payload["plan"] is not None
    assert payload["execution"]["result"] is None
    assert payload["errors"][0]["stage"] == "execution"


def test_routed_signal_parser_keeps_callback_alive_when_journal_write_fails() -> None:
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=StopLossFailingButSafeExecutor(),
        journal_service=RaisingJournalService(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, ExecutionResult)
    assert result.order_confirmed is True


class BlockedExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            entry_status="not_sent",
            order_attempted=False,
            order_sent=False,
            order_confirmed=False,
            stop_loss_attempted=False,
            stop_loss_configured=False,
            stop_loss_status="not_attempted",
            stop_loss_reason=None,
            take_profit_attempted=False,
            take_profit_status="not_attempted",
            take_profit_attempted_count=0,
            take_profit_accepted_count=0,
            take_profit_failed_count=0,
            take_profit_failures=[],
            registered_take_profit_orders=[],
            take_profit_reconciliation_summary={},
            cleanup_attempted=False,
            cleanup_status="not_attempted",
            cleanup_position_exists=None,
            cleanup_position_closed_within_window=False,
            cleanup_window_attempts=0,
            cleanup_remaining_registered_tp_count=0,
            cleanup_missing_registered_tp_count=0,
            cleanup_found_count=0,
            cleanup_cancelled_count=0,
            cleanup_failed_count=0,
            cleanup_failure_reasons=[],
            monitor_started=False,
            monitor_websocket_started=False,
            monitor_websocket_connected=False,
            monitor_websocket_authenticated=False,
            monitor_websocket_subscribed=False,
            monitor_websocket_execution_stream_subscribed=False,
            monitor_websocket_execution_events_relevant_count=0,
            monitor_websocket_execution_fill_summary={},
            monitor_rest_fallback_used=False,
            monitor_attempts=0,
            monitor_position_closed_within_window=False,
            monitor_cleanup_completed_within_window=False,
            monitor_remaining_execution_orders=[],
            monitor_status="not_started",
            monitor_final_decision_source=None,
            monitor_final_decision_reason="blocked_by_protection",
            blocked_by_dry_run=True,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason="DRY_RUN ativo",
            confirmation_status="not_sent",
            confirmation_reason="order not sent",
            bybit_response_summary={},
            stop_loss_response_summary={},
            take_profit_response_summaries=[],
            client_order_context=None,
            success=False,
        )


def test_routed_signal_parser_sets_trade_status_blocked() -> None:
    journal = RecordingJournalService()
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=BlockedExecutor(),
        journal_service=journal,
    )
    parser._parser = FakeSignalParser()

    parser.parse("BTCUSDT LONG")

    payload = journal.calls[0]["payload"]
    assert payload["tradeStatus"] == "blocked"
    assert payload["summary"]["tradeStatus"] == "blocked"


class ClosedCleanExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            entry_status="confirmed",
            order_attempted=True,
            order_sent=True,
            order_confirmed=True,
            stop_loss_attempted=True,
            stop_loss_configured=True,
            stop_loss_status="configured",
            stop_loss_reason=None,
            take_profit_attempted=True,
            take_profit_status="all_configured",
            take_profit_attempted_count=4,
            take_profit_accepted_count=4,
            take_profit_failed_count=0,
            take_profit_failures=[],
            registered_take_profit_orders=[],
            take_profit_reconciliation_summary={},
            cleanup_attempted=True,
            cleanup_status="cancelled_all",
            cleanup_position_exists=False,
            cleanup_position_closed_within_window=True,
            cleanup_window_attempts=1,
            cleanup_remaining_registered_tp_count=0,
            cleanup_missing_registered_tp_count=0,
            cleanup_found_count=0,
            cleanup_cancelled_count=0,
            cleanup_failed_count=0,
            cleanup_failure_reasons=[],
            monitor_started=True,
            monitor_websocket_started=True,
            monitor_websocket_connected=True,
            monitor_websocket_authenticated=True,
            monitor_websocket_subscribed=True,
            monitor_websocket_execution_stream_subscribed=False,
            monitor_websocket_execution_events_relevant_count=0,
            monitor_websocket_execution_fill_summary={},
            monitor_rest_fallback_used=False,
            monitor_attempts=1,
            monitor_position_closed_within_window=True,
            monitor_cleanup_completed_within_window=True,
            monitor_remaining_execution_orders=[],
            monitor_status="started_position_closed_cleanup_done",
            monitor_final_decision_source="websocket_position",
            monitor_final_decision_reason="closed_and_cleaned",
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status="confirmed",
            confirmation_reason="confirmed",
            bybit_response_summary={"orderId": "entry-1", "orderLinkId": "entry-link", "successReason": "ok"},
            stop_loss_response_summary={},
            take_profit_response_summaries=[],
            client_order_context="entry-link",
            success=True,
        )


def test_routed_signal_parser_sets_trade_status_closed_clean() -> None:
    journal = RecordingJournalService()
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=ClosedCleanExecutor(),
        journal_service=journal,
    )
    parser._parser = FakeSignalParser()

    parser.parse("BTCUSDT LONG")

    payload = journal.calls[0]["payload"]
    assert payload["tradeStatus"] == "closed_clean"
    assert payload["summary"]["tradeStatus"] == "closed_clean"


class MonitoringInconclusiveExecutor:
    def execute_entry(self, *, plan: ExecutionPlan):
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            entry_status="confirmed",
            order_attempted=True,
            order_sent=True,
            order_confirmed=True,
            stop_loss_attempted=True,
            stop_loss_configured=True,
            stop_loss_status="configured",
            stop_loss_reason=None,
            take_profit_attempted=True,
            take_profit_status="all_configured",
            take_profit_attempted_count=4,
            take_profit_accepted_count=4,
            take_profit_failed_count=0,
            take_profit_failures=[],
            registered_take_profit_orders=[],
            take_profit_reconciliation_summary={},
            cleanup_attempted=True,
            cleanup_status="position_not_closed_in_window",
            cleanup_position_exists=True,
            cleanup_position_closed_within_window=False,
            cleanup_window_attempts=2,
            cleanup_remaining_registered_tp_count=0,
            cleanup_missing_registered_tp_count=0,
            cleanup_found_count=0,
            cleanup_cancelled_count=0,
            cleanup_failed_count=0,
            cleanup_failure_reasons=[],
            monitor_started=True,
            monitor_websocket_started=True,
            monitor_websocket_connected=True,
            monitor_websocket_authenticated=True,
            monitor_websocket_subscribed=True,
            monitor_websocket_execution_stream_subscribed=False,
            monitor_websocket_execution_events_relevant_count=0,
            monitor_websocket_execution_fill_summary={},
            monitor_rest_fallback_used=True,
            monitor_attempts=3,
            monitor_position_closed_within_window=False,
            monitor_cleanup_completed_within_window=False,
            monitor_remaining_execution_orders=[],
            monitor_status="started_failed_with_safe_fallback",
            monitor_final_decision_source="rest_fallback",
            monitor_final_decision_reason="window_expired_without_close_confirmation",
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status="confirmed",
            confirmation_reason="confirmed",
            bybit_response_summary={"orderId": "entry-2", "orderLinkId": "entry-link-2", "successReason": "ok"},
            stop_loss_response_summary={},
            take_profit_response_summaries=[],
            client_order_context="entry-link-2",
            success=False,
        )


def test_routed_signal_parser_sets_trade_status_monitoring_inconclusive_when_not_closed() -> None:
    journal = RecordingJournalService()
    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=MonitoringInconclusiveExecutor(),
        journal_service=journal,
    )
    parser._parser = FakeSignalParser()

    parser.parse("BTCUSDT LONG")

    payload = journal.calls[0]["payload"]
    assert payload["tradeStatus"] == "monitoring_inconclusive"
    assert payload["summary"]["tradeStatus"] == "monitoring_inconclusive"
