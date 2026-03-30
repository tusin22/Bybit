from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging

from src.bybit.execution_client import BybitExecutionClient, BybitOrderRequest
from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.models.execution_result import ExecutionResult

LOGGER = logging.getLogger(__name__)


class TradeExecutionError(ValueError):
    """Erro explícito para tentativa insegura ou inválida de execução."""


@dataclass(frozen=True, slots=True)
class _ExecutionFlags:
    dry_run: bool
    enable_order_execution: bool
    bybit_testnet: bool


class TradeExecutor:
    """Executa entrada de ordem market na Bybit testnet com proteções explícitas."""

    def __init__(
        self,
        *,
        settings: Settings,
        execution_client: BybitExecutionClient,
    ) -> None:
        self._flags = _ExecutionFlags(
            dry_run=settings.dry_run,
            enable_order_execution=settings.enable_order_execution,
            bybit_testnet=settings.bybit_testnet,
        )
        self._execution_client = execution_client

    def execute_entry(self, *, plan: ExecutionPlan) -> ExecutionResult:
        if self._flags.dry_run:
            return self._blocked_result(
                plan=plan,
                reason="Execução bloqueada por proteção: DRY_RUN=true.",
                blocked_by_dry_run=True,
            )

        if not self._flags.enable_order_execution:
            return self._blocked_result(
                plan=plan,
                reason="Execução bloqueada por proteção: ENABLE_ORDER_EXECUTION=false.",
                blocked_by_execution_flag=True,
            )

        if not self._flags.bybit_testnet:
            return self._blocked_result(
                plan=plan,
                reason="Execução bloqueada por proteção: BYBIT_TESTNET=false nesta fase.",
                blocked_by_testnet_guard=True,
            )

        if not plan.eligible:
            reason = plan.ineligibility_reason or "ExecutionPlan inelegível para execução."
            return self._blocked_result(
                plan=plan,
                reason=reason,
            )

        self._validate_critical_data(plan=plan)
        client_order_context = f"entry-{plan.symbol.lower()}-{uuid.uuid4().hex[:12]}"
        order_qty = _format_qty(plan.planned_quantity, qty_step=plan.qty_step)

        LOGGER.info(
            "Preparando envio de ordem de entrada. symbol=%s category=%s planned_quantity=%s instrument_qty_step=%s serialized_qty=%s",
            plan.symbol,
            plan.category,
            plan.planned_quantity,
            plan.qty_step,
            order_qty,
        )

        response = self._execution_client.place_entry_market_order(
            order=BybitOrderRequest(
                category=plan.category,
                symbol=plan.symbol,
                side=plan.planned_entry_side,
                qty=order_qty,
                position_idx=0,
                order_link_id=client_order_context,
            )
        )

        summary = _build_response_summary(response)
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            order_attempted=True,
            order_sent=True,
            order_confirmed=False,
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status="pending_confirmation",
            bybit_response_summary=summary,
            client_order_context=client_order_context,
            success=False,
        )

    def _validate_critical_data(self, *, plan: ExecutionPlan) -> None:
        if not plan.symbol.strip():
            raise TradeExecutionError("Plano inválido para execução: symbol ausente.")
        if plan.category != "linear":
            raise TradeExecutionError(
                "Plano inválido para execução: category deve ser linear nesta fase."
            )
        if plan.planned_entry_side not in {"Buy", "Sell"}:
            raise TradeExecutionError(
                "Plano inválido para execução: planned_entry_side deve ser Buy ou Sell."
            )
        if plan.planned_quantity <= 0:
            raise TradeExecutionError(
                "Plano inválido para execução: planned_quantity deve ser > 0."
            )

    def _blocked_result(
        self,
        *,
        plan: ExecutionPlan,
        reason: str,
        blocked_by_dry_run: bool = False,
        blocked_by_execution_flag: bool = False,
        blocked_by_testnet_guard: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            order_attempted=False,
            order_sent=False,
            order_confirmed=False,
            blocked_by_dry_run=blocked_by_dry_run,
            blocked_by_execution_flag=blocked_by_execution_flag,
            blocked_by_testnet_guard=blocked_by_testnet_guard,
            blocked_reason=reason,
            confirmation_status="not_sent",
            bybit_response_summary={},
            client_order_context=None,
            success=False,
        )


def _format_qty(quantity: float, *, qty_step: str | None) -> str:
    decimal_quantity = Decimal(str(quantity))
    if qty_step is None:
        return format(decimal_quantity.normalize(), "f")

    try:
        decimal_step = Decimal(qty_step)
    except (InvalidOperation, ValueError) as exc:
        raise TradeExecutionError(f"qty_step inválido para serialização final: {qty_step}") from exc

    if decimal_step <= 0:
        raise TradeExecutionError(f"qty_step inválido para serialização final: {qty_step}")

    return format(decimal_quantity.quantize(decimal_step), "f")


def _build_response_summary(response: dict[str, object]) -> dict[str, object]:
    result = response.get("result")
    result_dict = result if isinstance(result, dict) else {}

    return {
        "retCode": response.get("retCode"),
        "retMsg": response.get("retMsg"),
        "orderId": result_dict.get("orderId"),
        "orderLinkId": result_dict.get("orderLinkId"),
        "requestAccepted": response.get("retCode") == 0,
    }
