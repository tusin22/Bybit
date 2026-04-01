from __future__ import annotations

import pytest

from src.bybit.execution_client import BybitExecutionClientError
from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.services.trade_executor import TradeExecutionError, TradeExecutor, _format_qty


class FakeExecutionClient:
    def __init__(
        self,
        *,
        open_orders_responses: list[dict[str, object]] | None = None,
        order_history_responses: list[dict[str, object]] | None = None,
        stop_loss_error: str | None = None,
        tp_fail_indexes: set[int] | None = None,
        positions_response: dict[str, object] | None = None,
        positions_responses: list[dict[str, object]] | None = None,
        open_orders_for_symbol_response: dict[str, object] | None = None,
        cancel_fail_order_ids: set[str] | None = None,
        open_order_lookup: dict[str, dict[str, object] | None] | None = None,
    ) -> None:
        self.calls = 0
        self.last_order = None
        self.open_orders_calls = 0
        self.order_history_calls = 0
        self.stop_loss_calls = 0
        self.last_stop_loss_request = None
        self.tp_calls = 0
        self.tp_requests: list[object] = []
        self.position_calls = 0
        self.open_orders_for_symbol_calls = 0
        self.cancel_calls = 0
        self.cancelled_order_ids: list[str] = []
        self._open_orders_responses = open_orders_responses or []
        self._order_history_responses = order_history_responses or []
        self._stop_loss_error = stop_loss_error
        self._tp_fail_indexes = tp_fail_indexes or set()
        self._positions_response = positions_response or {"retCode": 0, "retMsg": "OK", "result": {"list": []}}
        self._positions_responses = positions_responses or []
        self._open_orders_for_symbol_response = open_orders_for_symbol_response or {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"list": []},
        }
        self._cancel_fail_order_ids = cancel_fail_order_ids or set()
        self._open_order_lookup = open_order_lookup or {}

    def place_entry_market_order(self, *, order):
        self.calls += 1
        self.last_order = order
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "orderId": "abc-123",
                "orderLinkId": order.order_link_id,
            },
        }

    def place_reduce_only_limit_order(self, *, request):
        self.tp_calls += 1
        self.tp_requests.append(request)
        if self.tp_calls in self._tp_fail_indexes:
            raise BybitExecutionClientError(f"tp failed at index {self.tp_calls}")

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "orderId": f"tp-{self.tp_calls}",
                "orderLinkId": request.order_link_id,
            },
        }

    def set_trading_stop(self, *, request):
        self.stop_loss_calls += 1
        self.last_stop_loss_request = request
        if self._stop_loss_error is not None:
            raise BybitExecutionClientError(self._stop_loss_error)
        return {"retCode": 0, "retMsg": "OK", "result": {}}

    def get_open_orders(self, *, category, symbol, order_id, order_link_id):
        self.open_orders_calls += 1
        lookup_key = order_id or order_link_id
        if lookup_key in self._open_order_lookup:
            order = self._open_order_lookup[lookup_key]
            if order is None:
                return _empty_order_list_response()
            return _order_list_response(order)
        if self._open_orders_responses:
            return self._open_orders_responses.pop(0)
        return _empty_order_list_response()

    def get_order_history(self, *, category, symbol, order_id, order_link_id):
        self.order_history_calls += 1
        if self._order_history_responses:
            return self._order_history_responses.pop(0)
        return _empty_order_list_response()

    def get_positions(self, *, category, symbol):
        self.position_calls += 1
        if self._positions_responses:
            return self._positions_responses.pop(0)
        return self._positions_response

    def get_open_orders_for_symbol(self, *, category, symbol, limit):
        self.open_orders_for_symbol_calls += 1
        return self._open_orders_for_symbol_response

    def cancel_order(self, *, category, symbol, order_id, order_link_id):
        self.cancel_calls += 1
        order_id_or_link = order_id or order_link_id or ""
        if order_id_or_link in self._cancel_fail_order_ids:
            raise BybitExecutionClientError(f"cancel failed for {order_id_or_link}")
        self.cancelled_order_ids.append(order_id_or_link)
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": order_id, "orderLinkId": order_link_id}}

    @staticmethod
    def extract_first_order(response: dict[str, object]) -> dict[str, object] | None:
        result = response.get("result")
        if not isinstance(result, dict):
            return None

        order_list = result.get("list")
        if not isinstance(order_list, list) or not order_list:
            return None

        first = order_list[0]
        if not isinstance(first, dict):
            return None

        return first

    @staticmethod
    def extract_order_list(response: dict[str, object]) -> list[dict[str, object]]:
        result = response.get("result")
        if not isinstance(result, dict):
            return []
        order_list = result.get("list")
        if not isinstance(order_list, list):
            return []
        return [item for item in order_list if isinstance(item, dict)]


def _empty_order_list_response() -> dict[str, object]:
    return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}


def _order_list_response(order: dict[str, object]) -> dict[str, object]:
    return {"retCode": 0, "retMsg": "OK", "result": {"list": [order]}}


def _settings(
    *,
    dry_run: bool,
    enable_order_execution: bool,
    bybit_testnet: bool = True,
    tp1_percent: float = 50.0,
    tp2_percent: float = 20.0,
    tp3_percent: float = 20.0,
    tp4_percent: float = 10.0,
) -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        dry_run=dry_run,
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_name="session",
        telegram_source_chat="@chat",
        bybit_api_key="",
        bybit_api_secret="",
        bybit_testnet=bybit_testnet,
        enable_order_execution=enable_order_execution,
        execution_sizing_mode="fixed_notional_usdt",
        execution_fixed_notional_usdt=25.0,
        execution_fixed_qty=0.0,
        tp1_percent=tp1_percent,
        tp2_percent=tp2_percent,
        tp3_percent=tp3_percent,
        tp4_percent=tp4_percent,
    )


def _eligible_plan(*, side: str = "Buy", qty: float = 0.1, eligible: bool = True) -> ExecutionPlan:
    return ExecutionPlan(
        symbol="BTCUSDT",
        category="linear",
        planned_entry_side=side,
        reference_price=64000.0,
        normalized_entry_min=63900.0,
        normalized_entry_max=64100.0,
        normalized_stop_loss=63000.0,
        normalized_take_profits=[65000.0, 66000.0, 67000.0, 68000.0],
        operational_intent="open_long" if side == "Buy" else "open_short",
        planned_quantity=qty,
        tick_size="0.10",
        qty_step="0.001",
        min_order_qty="0.001",
        min_notional_value="5",
        instrument_status="Trading",
        eligible=eligible,
        ineligibility_reason=None if eligible else "fora da estratégia",
    )


def test_execute_entry_blocks_when_dry_run_is_true() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=True, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_attempted is False
    assert result.entry_status == "not_sent"
    assert result.blocked_by_dry_run is True
    assert result.confirmation_status == "not_sent"
    assert result.take_profit_status == "not_attempted"
    assert client.calls == 0


def test_execute_entry_blocks_when_plan_is_not_eligible() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan(eligible=False))

    assert result.order_attempted is False
    assert result.blocked_reason == "fora da estratégia"
    assert result.confirmation_status == "not_sent"
    assert result.take_profit_status == "not_attempted"
    assert client.tp_calls == 0


def test_execute_entry_confirms_and_sends_all_take_profits_successfully() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Filled",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan(side="Buy", qty=0.1))

    assert result.order_confirmed is True
    assert result.entry_status == "confirmed"
    assert result.stop_loss_status == "configured"
    assert result.take_profit_status == "all_configured"
    assert result.take_profit_attempted_count == 4
    assert result.take_profit_accepted_count == 4
    assert result.take_profit_failed_count == 0
    assert len(result.take_profit_response_summaries) == 4
    assert result.take_profit_reconciliation_summary["decision"] == "exact_distribution_after_normalization"
    assert result.success is True
    assert result.cleanup_status == "not_needed"
    assert len(result.registered_take_profit_orders) == 4
    assert client.tp_calls == 4
    assert all(request.side == "Sell" for request in client.tp_requests)
    assert all(request.position_idx == 0 for request in client.tp_requests)


def test_execute_entry_confirms_and_has_partial_take_profit_failures() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Filled",
                }
            )
        ],
        tp_fail_indexes={2, 4},
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan(side="Sell", qty=0.1))

    assert result.order_confirmed is True
    assert result.take_profit_status == "partial"
    assert result.take_profit_attempted_count == 4
    assert result.take_profit_accepted_count == 2
    assert result.take_profit_failed_count == 2
    assert len(result.take_profit_failures) == 2
    assert result.take_profit_reconciliation_summary["sumAfter"] <= result.take_profit_reconciliation_summary["plannedQuantity"]
    assert result.success is False
    assert result.cleanup_status == "not_needed"
    assert all(request.side == "Buy" for request in client.tp_requests)


def test_execute_entry_does_not_send_take_profits_when_entry_is_not_confirmed() -> None:
    pending = _order_list_response(
        {
            "orderId": "abc-123",
            "orderLinkId": "entry-btc",
            "orderStatus": "Created",
        }
    )
    client = FakeExecutionClient(open_orders_responses=[pending, pending, pending, pending])
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_confirmed is False
    assert result.take_profit_status == "not_attempted"
    assert result.take_profit_attempted_count == 0
    assert result.success is False
    assert result.cleanup_status == "not_attempted"
    assert client.tp_calls == 0


def test_execute_entry_keeps_success_true_when_tp_not_attempted_by_valid_flow_condition() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "New",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_confirmed is True
    assert result.stop_loss_attempted is False
    assert result.take_profit_attempted is False
    assert result.success is True
    assert result.cleanup_status == "not_attempted"


def test_execute_entry_fails_fast_for_invalid_tp_percent_sum() -> None:
    client = FakeExecutionClient()

    with pytest.raises(TradeExecutionError, match="soma"):
        TradeExecutor(
            settings=_settings(
                dry_run=False,
                enable_order_execution=True,
                tp1_percent=40,
                tp2_percent=20,
                tp3_percent=20,
                tp4_percent=10,
            ),
            execution_client=client,
        )


def test_execute_entry_fails_for_tp_quantity_that_becomes_zero_after_normalization() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Filled",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )
    plan = _eligible_plan(qty=0.001)

    with pytest.raises(TradeExecutionError, match="Quantidade parcial inválida"):
        executor.execute_entry(plan=plan)


def test_tp_reconciliation_allocates_residual_to_last_tp_when_possible() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Filled",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan(qty=0.123))

    assert result.take_profit_reconciliation_summary["decision"] == "allocated_to_last_tp"
    assert result.take_profit_reconciliation_summary["allocatedToLastTp"] == 0.002
    assert result.take_profit_reconciliation_summary["sumAfter"] == 0.123
    assert result.take_profit_reconciliation_summary["sumAfter"] <= result.take_profit_reconciliation_summary["plannedQuantity"]


def test_tp_reconciliation_logs_unallocatable_residual_below_qty_step() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Filled",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan(qty=0.1204))

    assert result.take_profit_reconciliation_summary["decision"] == "residual_below_qty_step_not_allocated"
    assert result.take_profit_reconciliation_summary["sumAfter"] <= result.take_profit_reconciliation_summary["plannedQuantity"]
    assert result.take_profit_reconciliation_summary["residualAfter"] > 0
    assert result.take_profit_reconciliation_summary["residualAfter"] < 0.001
    for summary in result.take_profit_response_summaries:
        assert float(summary["tpQty"]) > 0


def test_callback_stays_alive_in_routed_parser_when_tp_fails() -> None:
    from src.main import RoutedSignalParser
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
            return _eligible_plan(qty=0.1)

    class ExplodingTpExecutor:
        def execute_entry(self, *, plan: ExecutionPlan):
            raise TradeExecutionError("Quantidade parcial inválida para TP após normalização por qtyStep")

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

    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=ExplodingTpExecutor(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, Signal)
    assert result.entry_eligible is False
    assert "callback mantido ativo" in (result.entry_validation_reason or "")


def test_cleanup_does_not_cancel_when_position_still_open() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[_order_list_response({"orderId": "abc-123", "orderLinkId": "entry-btc", "orderStatus": "Filled"})],
        positions_response={"retCode": 0, "retMsg": "OK", "result": {"list": [{"side": "Buy", "size": "0.1"}]}},
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.cleanup_attempted is True
    assert result.cleanup_status == "position_not_closed_in_window"
    assert result.cleanup_position_closed_within_window is False
    assert result.cleanup_cancelled_count == 0
    assert client.cancel_calls == 0


def test_cleanup_cancels_pending_tps_when_position_is_closed() -> None:
    tp_lookup = {
        "tp-1": {"orderId": "tp-1", "orderLinkId": "tp1-btcusdt-abc", "orderStatus": "New"},
        "tp-2": {"orderId": "tp-2", "orderLinkId": "tp2-btcusdt-def", "orderStatus": "PartiallyFilled"},
    }
    client = FakeExecutionClient(
        open_orders_responses=[_order_list_response({"orderId": "abc-123", "orderLinkId": "entry-btc", "orderStatus": "Filled"})],
        positions_responses=[
            {"retCode": 0, "retMsg": "OK", "result": {"list": [{"side": "Buy", "size": "0.1"}]}},
            {"retCode": 0, "retMsg": "OK", "result": {"list": [{"side": "", "size": "0"}]}},
        ],
        open_order_lookup=tp_lookup,
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.cleanup_status == "cancelled_all"
    assert result.cleanup_position_closed_within_window is True
    assert result.cleanup_found_count == 2
    assert result.cleanup_cancelled_count == 2
    assert result.cleanup_failed_count == 0


def test_cleanup_handles_partial_cancel_failures() -> None:
    tp_lookup = {
        "tp-1": {"orderId": "tp-1", "orderLinkId": "tp1-btcusdt-abc", "orderStatus": "New"},
        "tp-2": {"orderId": "tp-2", "orderLinkId": "tp2-btcusdt-def", "orderStatus": "New"},
    }
    client = FakeExecutionClient(
        open_orders_responses=[_order_list_response({"orderId": "abc-123", "orderLinkId": "entry-btc", "orderStatus": "Filled"})],
        positions_response={"retCode": 0, "retMsg": "OK", "result": {"list": [{"side": "", "size": "0"}]}},
        open_order_lookup=tp_lookup,
        cancel_fail_order_ids={"tp-2"},
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.cleanup_status == "partial"
    assert result.cleanup_found_count == 2
    assert result.cleanup_cancelled_count == 1
    assert result.cleanup_failed_count == 1
    assert len(result.cleanup_failure_reasons) == 1


def test_cleanup_handles_registered_orders_that_already_disappeared() -> None:
    tp_lookup = {
        "tp-1": None,
        "tp-2": {"orderId": "tp-2", "orderLinkId": "tp2-btcusdt-def", "orderStatus": "New"},
    }
    client = FakeExecutionClient(
        open_orders_responses=[_order_list_response({"orderId": "abc-123", "orderLinkId": "entry-btc", "orderStatus": "Filled"})],
        positions_response={"retCode": 0, "retMsg": "OK", "result": {"list": [{"side": "", "size": "0"}]}},
        open_order_lookup=tp_lookup,
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.cleanup_status == "cancelled_all"
    assert result.cleanup_remaining_registered_tp_count == 1
    assert result.cleanup_missing_registered_tp_count >= 1
    assert result.cleanup_cancelled_count == 1


def test_callback_stays_alive_in_routed_parser_when_cleanup_fails_safely() -> None:
    from src.main import RoutedSignalParser
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
            return _eligible_plan(qty=0.1)

    class ExplodingCleanupExecutor:
        def execute_entry(self, *, plan: ExecutionPlan):
            raise BybitExecutionClientError("Falha Bybit em cancel_order: retCode=1000 retMsg=error")

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

    parser = RoutedSignalParser(
        router=FakeRouter(),
        planner=FakePlanner(),
        executor=ExplodingCleanupExecutor(),
    )
    parser._parser = FakeSignalParser()

    result = parser.parse("BTCUSDT LONG")

    assert isinstance(result, Signal)
    assert result.entry_eligible is False
    assert "callback mantido ativo" in (result.entry_validation_reason or "")


def test_format_qty_serializes_without_float_noise_using_qty_step() -> None:
    assert _format_qty(86.90000000000001, qty_step="0.001") == "86.900"
