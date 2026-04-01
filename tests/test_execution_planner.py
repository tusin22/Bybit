from __future__ import annotations

from src.config import Settings
from src.models.signal import Signal
from src.services.execution_planner import (
    ExecutionPlanner,
    normalize_entry_price,
    normalize_quantity,
    normalize_stop_price,
    normalize_take_profit_price,
)


def _settings(
    *,
    mode: str = "fixed_notional_usdt",
    fixed_notional_usdt: float = 100.0,
    fixed_qty: float = 0.01,
) -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        dry_run=True,
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_name="session",
        telegram_source_chat="@chat",
        bybit_api_key="",
        bybit_api_secret="",
        bybit_testnet=True,
        enable_order_execution=False,
        execution_sizing_mode=mode,
        execution_fixed_notional_usdt=fixed_notional_usdt,
        execution_fixed_qty=fixed_qty,
        tp1_percent=50.0,
        tp2_percent=20.0,
        tp3_percent=20.0,
        tp4_percent=10.0,
    )


def _validated_signal(*, side: str = "LONG") -> Signal:
    signal = Signal(
        symbol="BTCUSDT",
        side=side,
        entry_min=64001.13,
        entry_max=64088.89,
        take_profits=[64150.13, 64200.13, 64300.13, 64400.13],
        stop_loss=63899.99,
        raw_text="BTCUSDT | LONG",
    )
    signal.current_price = 64050.12
    signal.entry_eligible = True
    signal.entry_validation_reason = "Preço atual dentro da faixa de entrada do sinal."
    signal.instrument_status = "Trading"
    signal.instrument_tick_size = "0.10"
    signal.instrument_qty_step = "0.001"
    signal.instrument_min_order_qty = "0.001"
    signal.instrument_min_notional_value = "5"
    return signal


def test_build_plan_with_fixed_notional_usdt() -> None:
    planner = ExecutionPlanner(
        settings=_settings(
            mode="fixed_notional_usdt",
            fixed_notional_usdt=100.0,
        )
    )
    signal = _validated_signal(side="LONG")

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is True
    assert plan.category == "linear"
    assert plan.planned_entry_side == "Buy"
    assert plan.operational_intent == "open_long"
    assert plan.normalized_entry_min == 64001.1
    assert plan.normalized_stop_loss == 63900.0
    assert plan.planned_quantity > 0
    assert plan.planned_quantity == 0.001
    assert plan.min_order_qty == "0.001"
    assert plan.min_notional_value == "5"


def test_build_plan_ineligible_when_instrument_status_is_not_trading() -> None:
    planner = ExecutionPlanner(settings=_settings())
    signal = _validated_signal(side="SHORT")
    signal.instrument_status = "Settling"

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "status diferente" in (plan.ineligibility_reason or "")
    assert plan.operational_intent == "open_short"


def test_build_plan_ineligible_when_critical_metadata_is_missing() -> None:
    planner = ExecutionPlanner(settings=_settings())
    signal = _validated_signal()
    signal.instrument_tick_size = None

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "Metadado crítico" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_when_below_min_order_qty() -> None:
    planner = ExecutionPlanner(
        settings=_settings(mode="fixed_qty", fixed_qty=0.005)
    )
    signal = _validated_signal()
    signal.instrument_min_order_qty = "0.01"

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "Quantidade abaixo de minOrderQty" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_when_below_min_notional_value() -> None:
    planner = ExecutionPlanner(
        settings=_settings(mode="fixed_qty", fixed_qty=0.001)
    )
    signal = _validated_signal()
    signal.instrument_min_notional_value = "100"

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "Valor nocional abaixo de minNotionalValue" in (plan.ineligibility_reason or "")


def test_build_plan_eligible_when_meets_minimums() -> None:
    planner = ExecutionPlanner(
        settings=_settings(mode="fixed_qty", fixed_qty=0.002)
    )
    signal = _validated_signal()
    signal.instrument_min_order_qty = "0.0001"
    signal.instrument_min_notional_value = "20"

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is True
    assert plan.ineligibility_reason is None


def test_build_plan_ineligible_when_min_notional_is_missing() -> None:
    planner = ExecutionPlanner(settings=_settings())
    signal = _validated_signal()
    signal.instrument_min_notional_value = None

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "Metadado crítico ausente" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_when_min_order_qty_is_missing() -> None:
    planner = ExecutionPlanner(settings=_settings())
    signal = _validated_signal()
    signal.instrument_min_order_qty = None

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "Metadado crítico ausente" in (plan.ineligibility_reason or "")


def test_normalize_helpers_round_down_to_tick_and_step() -> None:
    assert normalize_entry_price(value=123.987, tick_size="0.05", side="LONG") == 123.95
    assert normalize_entry_price(value=123.987, tick_size="0.05", side="SHORT") == 124.0
    assert normalize_stop_price(value=123.987, tick_size="0.05", side="LONG") == 124.0
    assert normalize_stop_price(value=123.987, tick_size="0.05", side="SHORT") == 123.95
    assert (
        normalize_take_profit_price(value=123.987, tick_size="0.05", side="LONG")
        == 123.95
    )
    assert (
        normalize_take_profit_price(value=123.987, tick_size="0.05", side="SHORT")
        == 124.0
    )
    assert normalize_quantity(value=0.98765, qty_step="0.001") == 0.987


def test_build_plan_ineligible_when_entry_eligible_is_none() -> None:
    planner = ExecutionPlanner(settings=_settings())
    signal = _validated_signal()
    signal.entry_eligible = None
    signal.entry_validation_reason = None

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "deve ser True" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_for_invalid_sizing_mode() -> None:
    planner = ExecutionPlanner(settings=_settings(mode="invalid_mode"))
    signal = _validated_signal()

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "EXECUTION_SIZING_MODE inválido" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_for_non_positive_fixed_qty() -> None:
    planner = ExecutionPlanner(settings=_settings(mode="fixed_qty", fixed_qty=0))
    signal = _validated_signal()

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "fixed_qty inválido" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_for_non_positive_fixed_notional() -> None:
    planner = ExecutionPlanner(
        settings=_settings(mode="fixed_notional_usdt", fixed_notional_usdt=0)
    )
    signal = _validated_signal()

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "fixed_notional_usdt inválido" in (plan.ineligibility_reason or "")


def test_build_plan_ineligible_when_quantity_turns_zero_after_normalization() -> None:
    planner = ExecutionPlanner(
        settings=_settings(mode="fixed_notional_usdt", fixed_notional_usdt=1.0)
    )
    signal = _validated_signal()
    signal.instrument_qty_step = "1"

    plan = planner.build_plan(signal=signal)

    assert plan.eligible is False
    assert "normalização (<= 0)" in (plan.ineligibility_reason or "")
