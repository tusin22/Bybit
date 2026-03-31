from __future__ import annotations

import pytest

from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.services.trade_executor import TradeExecutionError, TradeExecutor, _format_qty


class FakeExecutionClient:
    def __init__(
        self,
        *,
        open_orders_responses: list[dict[str, object]] | None = None,
        order_history_responses: list[dict[str, object]] | None = None,
    ) -> None:
        self.calls = 0
        self.last_order = None
        self.open_orders_calls = 0
        self.order_history_calls = 0
        self._open_orders_responses = open_orders_responses or []
        self._order_history_responses = order_history_responses or []

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

    def get_open_orders(self, *, category, symbol, order_id, order_link_id):
        self.open_orders_calls += 1
        if self._open_orders_responses:
            return self._open_orders_responses.pop(0)
        return _empty_order_list_response()

    def get_order_history(self, *, category, symbol, order_id, order_link_id):
        self.order_history_calls += 1
        if self._order_history_responses:
            return self._order_history_responses.pop(0)
        return _empty_order_list_response()

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


def _empty_order_list_response() -> dict[str, object]:
    return {"retCode": 0, "retMsg": "OK", "result": {"list": []}}


def _order_list_response(order: dict[str, object]) -> dict[str, object]:
    return {"retCode": 0, "retMsg": "OK", "result": {"list": [order]}}


def _settings(
    *,
    dry_run: bool,
    enable_order_execution: bool,
    bybit_testnet: bool = True,
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
    )


def _eligible_plan() -> ExecutionPlan:
    return ExecutionPlan(
        symbol="BTCUSDT",
        category="linear",
        planned_entry_side="Buy",
        reference_price=64000.0,
        normalized_entry_min=63900.0,
        normalized_entry_max=64100.0,
        normalized_stop_loss=63000.0,
        normalized_take_profits=[65000.0, 66000.0, 67000.0, 68000.0],
        operational_intent="open_long",
        planned_quantity=0.001,
        tick_size="0.10",
        qty_step="0.001",
        min_order_qty="0.001",
        min_notional_value="5",
        instrument_status="Trading",
        eligible=True,
        ineligibility_reason=None,
    )


def test_execute_entry_blocks_when_dry_run_is_true() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=True, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_attempted is False
    assert result.blocked_by_dry_run is True
    assert result.confirmation_status == "not_sent"
    assert client.calls == 0


def test_execute_entry_blocks_when_execution_flag_is_false() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=False),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_attempted is False
    assert result.blocked_by_execution_flag is True
    assert result.confirmation_status == "not_sent"
    assert client.calls == 0


def test_execute_entry_blocks_when_testnet_guard_is_not_satisfied() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(
            dry_run=False,
            enable_order_execution=True,
            bybit_testnet=False,
        ),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_attempted is False
    assert result.blocked_by_testnet_guard is True
    assert "BYBIT_TESTNET=false" in (result.blocked_reason or "")
    assert result.confirmation_status == "not_sent"
    assert client.calls == 0


def test_execute_entry_blocks_when_plan_is_not_eligible() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )
    ineligible_plan = _eligible_plan()
    ineligible_plan.eligible = False
    ineligible_plan.ineligibility_reason = "fora da estratégia"

    result = executor.execute_entry(plan=ineligible_plan)

    assert result.order_attempted is False
    assert result.blocked_reason == "fora da estratégia"
    assert result.confirmation_status == "not_sent"
    assert client.calls == 0


def test_execute_entry_sends_market_order_and_confirms_after_ack() -> None:
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

    assert result.order_attempted is True
    assert result.order_sent is True
    assert result.order_confirmed is True
    assert result.confirmation_status == "confirmed"
    assert result.success is True
    assert result.bybit_response_summary["orderId"] == "abc-123"
    assert result.bybit_response_summary["requestAccepted"] is True
    assert client.calls == 1
    assert client.last_order.position_idx == 0


def test_execute_entry_marks_timeout_when_pending_never_resolves() -> None:
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

    assert result.order_sent is True
    assert result.order_confirmed is False
    assert result.confirmation_status == "timeout"
    assert "Timeout" in (result.confirmation_reason or "")


def test_execute_entry_marks_not_found_when_order_is_missing() -> None:
    client = FakeExecutionClient(
        open_orders_responses=[
            _empty_order_list_response(),
            _empty_order_list_response(),
            _empty_order_list_response(),
            _empty_order_list_response(),
        ],
        order_history_responses=[
            _empty_order_list_response(),
            _empty_order_list_response(),
            _empty_order_list_response(),
            _empty_order_list_response(),
        ],
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.order_sent is True
    assert result.order_confirmed is False
    assert result.confirmation_status == "not_found"


def test_execute_entry_marks_cancelled_from_order_snapshot() -> None:
    client = FakeExecutionClient(
        order_history_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Cancelled",
                    "cancelType": "CancelByUser",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.confirmation_status == "cancelled"
    assert result.order_confirmed is False


def test_execute_entry_marks_rejected_from_order_snapshot() -> None:
    client = FakeExecutionClient(
        order_history_responses=[
            _order_list_response(
                {
                    "orderId": "abc-123",
                    "orderLinkId": "entry-btc",
                    "orderStatus": "Rejected",
                    "rejectReason": "EC_QTY_LESS_THAN_MIN_QTY",
                }
            )
        ]
    )
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )

    result = executor.execute_entry(plan=_eligible_plan())

    assert result.confirmation_status == "rejected"
    assert "EC_QTY_LESS_THAN_MIN_QTY" in (result.confirmation_reason or "")


def test_execute_entry_fails_clearly_when_critical_data_is_missing() -> None:
    client = FakeExecutionClient()
    executor = TradeExecutor(
        settings=_settings(dry_run=False, enable_order_execution=True),
        execution_client=client,
    )
    invalid_plan = _eligible_plan()
    invalid_plan.symbol = ""

    with pytest.raises(TradeExecutionError, match="symbol ausente"):
        executor.execute_entry(plan=invalid_plan)

    assert client.calls == 0


def test_format_qty_serializes_without_float_noise_using_qty_step() -> None:
    assert _format_qty(86.90000000000001, qty_step="0.001") == "86.900"
