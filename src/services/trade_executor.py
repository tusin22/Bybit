from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging

from src.bybit.execution_client import BybitExecutionClient, BybitOrderRequest
from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.models.execution_result import ConfirmationStatus, ExecutionResult

LOGGER = logging.getLogger(__name__)

_MAX_CONFIRMATION_ATTEMPTS = 4
_CONFIRMATION_INTERVAL_SECONDS = 0.35
_PENDING_STATUSES = {"Created", "Untriggered", "Triggered"}
_CONFIRMED_STATUSES = {"New", "PartiallyFilled", "Filled"}
_REJECTED_STATUSES = {"Rejected"}
_CANCELLED_STATUSES = {"Cancelled", "Deactivated", "PartiallyFilledCanceled"}


class TradeExecutionError(ValueError):
    """Erro explícito para tentativa insegura ou inválida de execução."""


@dataclass(frozen=True, slots=True)
class _ExecutionFlags:
    dry_run: bool
    enable_order_execution: bool
    bybit_testnet: bool


@dataclass(frozen=True, slots=True)
class _ConfirmationState:
    status: ConfirmationStatus
    reason: str | None


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
        confirmation = self._confirm_order_with_polling(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            order_id=_as_optional_string(summary.get("orderId")),
            order_link_id=_as_optional_string(summary.get("orderLinkId")),
        )

        summary["confirmationReason"] = confirmation.reason
        order_confirmed = confirmation.status == "confirmed"

        LOGGER.info(
            "Confirmação pós-ACK concluída. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s confirmation_status=%s reason=%s",
            plan.symbol,
            plan.category,
            plan.planned_entry_side,
            summary.get("orderId"),
            summary.get("orderLinkId"),
            confirmation.status,
            confirmation.reason,
        )

        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            order_attempted=True,
            order_sent=True,
            order_confirmed=order_confirmed,
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status=confirmation.status,
            confirmation_reason=confirmation.reason,
            bybit_response_summary=summary,
            client_order_context=client_order_context,
            success=order_confirmed,
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

    def _confirm_order_with_polling(
        self,
        *,
        symbol: str,
        category: str,
        side: str,
        order_id: str | None,
        order_link_id: str | None,
    ) -> _ConfirmationState:
        seen_snapshot = False
        seen_pending = False

        for attempt in range(1, _MAX_CONFIRMATION_ATTEMPTS + 1):
            snapshot = self._fetch_order_snapshot(
                category=category,
                symbol=symbol,
                order_id=order_id,
                order_link_id=order_link_id,
            )

            if snapshot is None:
                LOGGER.info(
                    "Snapshot de confirmação não encontrado. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s attempt=%s/%s",
                    symbol,
                    category,
                    side,
                    order_id,
                    order_link_id,
                    attempt,
                    _MAX_CONFIRMATION_ATTEMPTS,
                )
            else:
                seen_snapshot = True
                status = _as_optional_string(snapshot.get("orderStatus"))
                mapped_status = _map_confirmation_status(status)
                rejection_reason = _as_optional_string(snapshot.get("rejectReason"))
                cancel_type = _as_optional_string(snapshot.get("cancelType"))

                LOGGER.info(
                    "Snapshot de confirmação recebido. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s order_status=%s mapped_status=%s reject_reason=%s cancel_type=%s attempt=%s/%s",
                    symbol,
                    category,
                    side,
                    snapshot.get("orderId") or order_id,
                    snapshot.get("orderLinkId") or order_link_id,
                    status,
                    mapped_status,
                    rejection_reason,
                    cancel_type,
                    attempt,
                    _MAX_CONFIRMATION_ATTEMPTS,
                )

                if mapped_status == "confirmed":
                    return _ConfirmationState(status="confirmed", reason="orderStatus confirmado via REST")

                if mapped_status == "rejected":
                    reason = rejection_reason or "Ordem rejeitada conforme orderStatus da Bybit."
                    return _ConfirmationState(status="rejected", reason=reason)

                if mapped_status == "cancelled":
                    reason = cancel_type or "Ordem cancelada conforme orderStatus da Bybit."
                    return _ConfirmationState(status="cancelled", reason=reason)

                seen_pending = True

            if attempt < _MAX_CONFIRMATION_ATTEMPTS:
                time.sleep(_CONFIRMATION_INTERVAL_SECONDS)

        if not seen_snapshot:
            return _ConfirmationState(
                status="not_found",
                reason="Ordem não encontrada em open orders e history dentro da janela de confirmação.",
            )

        if seen_pending:
            return _ConfirmationState(
                status="timeout",
                reason="Timeout aguardando transição de orderStatus para estado final confirmado/rejeitado/cancelado.",
            )

        return _ConfirmationState(
            status="pending_confirmation",
            reason="ACK recebido, porém sem confirmação conclusiva nesta fase.",
        )

    def _fetch_order_snapshot(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None,
        order_link_id: str | None,
    ) -> dict[str, object] | None:
        open_orders = self._execution_client.get_open_orders(
            category=category,
            symbol=symbol,
            order_id=order_id,
            order_link_id=order_link_id,
        )
        open_snapshot = self._execution_client.extract_first_order(open_orders)
        if open_snapshot is not None:
            return open_snapshot

        history = self._execution_client.get_order_history(
            category=category,
            symbol=symbol,
            order_id=order_id,
            order_link_id=order_link_id,
        )
        return self._execution_client.extract_first_order(history)

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
            confirmation_reason=reason,
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


def _map_confirmation_status(order_status: str | None) -> ConfirmationStatus:
    if order_status in _CONFIRMED_STATUSES:
        return "confirmed"
    if order_status in _REJECTED_STATUSES:
        return "rejected"
    if order_status in _CANCELLED_STATUSES:
        return "cancelled"
    if order_status in _PENDING_STATUSES:
        return "pending_confirmation"
    return "pending_confirmation"


def _as_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
