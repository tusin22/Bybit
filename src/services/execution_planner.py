from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP

from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.models.signal import Signal


class ExecutionPlanningError(ValueError):
    """Erro explícito para regras inválidas de planejamento."""


@dataclass(frozen=True, slots=True)
class _SizingConfig:
    mode: str
    fixed_notional_usdt: float
    fixed_qty: float


class ExecutionPlanner:
    """Converte Signal validado em plano operacional sem enviar ordens."""

    def __init__(self, *, settings: Settings) -> None:
        self._sizing = _SizingConfig(
            mode=settings.execution_sizing_mode,
            fixed_notional_usdt=settings.execution_fixed_notional_usdt,
            fixed_qty=settings.execution_fixed_qty,
        )

    def build_plan(self, *, signal: Signal) -> ExecutionPlan:
        planned_side = "Buy" if signal.side == "LONG" else "Sell"
        reference_price = signal.current_price or 0.0

        tick_size = signal.instrument_tick_size
        qty_step = signal.instrument_qty_step
        instrument_status = signal.instrument_status

        if instrument_status != "Trading":
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason="Instrumento inelegível: status diferente de Trading.",
            )

        if tick_size is None or qty_step is None:
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason="Metadado crítico ausente: tickSize e/ou qtyStep.",
            )

        if signal.current_price is None:
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason="Preço de referência ausente para planejamento.",
            )

        if signal.entry_eligible is not True:
            reason = signal.entry_validation_reason or (
                "Entrada inelegível: signal.entry_eligible deve ser True."
            )
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason=reason,
            )

        normalized_entry_min = normalize_entry_price(
            value=signal.entry_min,
            tick_size=tick_size,
            side=signal.side,
        )
        normalized_entry_max = normalize_entry_price(
            value=signal.entry_max,
            tick_size=tick_size,
            side=signal.side,
        )
        normalized_stop_loss = normalize_stop_price(
            value=signal.stop_loss,
            tick_size=tick_size,
            side=signal.side,
        )
        normalized_tps = [
            normalize_take_profit_price(
                value=tp,
                tick_size=tick_size,
                side=signal.side,
            )
            for tp in signal.take_profits
        ]

        try:
            raw_qty = self._calculate_quantity(reference_price=signal.current_price)
        except ExecutionPlanningError as exc:
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason=str(exc),
            )

        planned_quantity = normalize_quantity(value=raw_qty, qty_step=qty_step)
        if planned_quantity <= 0:
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason="Quantidade planejada inválida após normalização (<= 0).",
            )

        if normalized_entry_min > normalized_entry_max:
            return self._ineligible_plan(
                signal=signal,
                planned_side=planned_side,
                reference_price=reference_price,
                reason="Faixa de entrada normalizada inválida: min maior que max.",
            )

        return ExecutionPlan(
            symbol=signal.symbol,
            category="linear",
            planned_entry_side=planned_side,
            reference_price=signal.current_price,
            normalized_entry_min=normalized_entry_min,
            normalized_entry_max=normalized_entry_max,
            normalized_stop_loss=normalized_stop_loss,
            normalized_take_profits=normalized_tps,
            operational_intent=signal.operational_intent,
            planned_quantity=planned_quantity,
            tick_size=tick_size,
            qty_step=qty_step,
            instrument_status=instrument_status,
            eligible=True,
            ineligibility_reason=None,
        )

    def _calculate_quantity(self, *, reference_price: float) -> float:
        if self._sizing.mode == "fixed_qty":
            if self._sizing.fixed_qty <= 0:
                raise ExecutionPlanningError(
                    "Sizing fixed_qty inválido: valor deve ser positivo."
                )
            return self._sizing.fixed_qty

        if self._sizing.mode == "fixed_notional_usdt":
            if self._sizing.fixed_notional_usdt <= 0:
                raise ExecutionPlanningError(
                    "Sizing fixed_notional_usdt inválido: valor deve ser positivo."
                )
            if reference_price <= 0:
                raise ExecutionPlanningError(
                    "Preço de referência inválido para sizing por notional."
                )
            return self._sizing.fixed_notional_usdt / reference_price

        raise ExecutionPlanningError(
            "EXECUTION_SIZING_MODE inválido: use fixed_notional_usdt ou fixed_qty."
        )

    def _ineligible_plan(
        self,
        *,
        signal: Signal,
        planned_side: str,
        reference_price: float,
        reason: str,
    ) -> ExecutionPlan:
        tick_size = signal.instrument_tick_size
        qty_step = signal.instrument_qty_step

        normalized_entry_min = (
            normalize_entry_price(
                value=signal.entry_min,
                tick_size=tick_size,
                side=signal.side,
            )
            if tick_size
            else signal.entry_min
        )
        normalized_entry_max = (
            normalize_entry_price(
                value=signal.entry_max,
                tick_size=tick_size,
                side=signal.side,
            )
            if tick_size
            else signal.entry_max
        )
        normalized_stop_loss = (
            normalize_stop_price(
                value=signal.stop_loss,
                tick_size=tick_size,
                side=signal.side,
            )
            if tick_size
            else signal.stop_loss
        )
        normalized_tps = [
            normalize_take_profit_price(
                value=tp,
                tick_size=tick_size,
                side=signal.side,
            )
            if tick_size
            else tp
            for tp in signal.take_profits
        ]

        return ExecutionPlan(
            symbol=signal.symbol,
            category="linear",
            planned_entry_side=planned_side,
            reference_price=reference_price,
            normalized_entry_min=normalized_entry_min,
            normalized_entry_max=normalized_entry_max,
            normalized_stop_loss=normalized_stop_loss,
            normalized_take_profits=normalized_tps,
            operational_intent=signal.operational_intent,
            planned_quantity=0.0,
            tick_size=tick_size,
            qty_step=qty_step,
            instrument_status=signal.instrument_status,
            eligible=False,
            ineligibility_reason=reason,
        )


def _to_decimal(value: float | str, *, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionPlanningError(f"{field_name} inválido: {value}.") from exc

    if decimal_value <= 0:
        raise ExecutionPlanningError(f"{field_name} deve ser positivo: {value}.")

    return decimal_value


def _normalize_by_tick(
    *,
    value: float,
    tick_size: str,
    rounding: str,
) -> float:
    tick = _to_decimal(tick_size, field_name="tickSize")
    decimal_value = Decimal(str(value))
    normalized = (decimal_value / tick).to_integral_value(rounding=rounding) * tick
    return float(normalized)


def normalize_entry_price(*, value: float, tick_size: str, side: str) -> float:
    rounding = ROUND_DOWN if side == "LONG" else ROUND_UP
    return _normalize_by_tick(value=value, tick_size=tick_size, rounding=rounding)


def normalize_stop_price(*, value: float, tick_size: str, side: str) -> float:
    rounding = ROUND_UP if side == "LONG" else ROUND_DOWN
    return _normalize_by_tick(value=value, tick_size=tick_size, rounding=rounding)


def normalize_take_profit_price(*, value: float, tick_size: str, side: str) -> float:
    rounding = ROUND_DOWN if side == "LONG" else ROUND_UP
    return _normalize_by_tick(value=value, tick_size=tick_size, rounding=rounding)


def normalize_price(*, value: float, tick_size: str) -> float:
    """Compatibilidade temporária: manter helper genérico para usos legados."""
    normalized = _normalize_by_tick(
        value=value,
        tick_size=tick_size,
        rounding=ROUND_DOWN,
    )
    return float(normalized)


def normalize_quantity(*, value: float, qty_step: str) -> float:
    step = _to_decimal(qty_step, field_name="qtyStep")
    decimal_value = Decimal(str(value))
    normalized = (decimal_value / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(normalized)
