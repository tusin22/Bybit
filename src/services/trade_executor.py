from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from src.bybit.execution_client import (
    BybitExecutionClient,
    BybitExecutionClientError,
    BybitOrderRequest,
    BybitReduceOnlyLimitOrderRequest,
    BybitSetTradingStopRequest,
)
from src.bybit.private_execution_ws import (
    BybitPrivateExecutionWsMonitor,
    BybitPrivateWsMonitorError,
)
from src.config import Settings
from src.models.execution_plan import ExecutionPlan
from src.models.execution_result import ConfirmationStatus, ExecutionResult, TakeProfitStatus

LOGGER = logging.getLogger(__name__)

_MAX_CONFIRMATION_ATTEMPTS = 4
_CONFIRMATION_INTERVAL_SECONDS = 0.35
_PENDING_STATUSES = {"Created", "Untriggered", "Triggered"}
_CONFIRMED_STATUSES = {"New", "PartiallyFilled", "Filled"}
_REJECTED_STATUSES = {"Rejected"}
_CANCELLED_STATUSES = {"Cancelled", "Deactivated", "PartiallyFilledCanceled"}
_ONE_WAY_POSITION_IDX = 0
_EXECUTION_MONITOR_MAX_ATTEMPTS = 4
_EXECUTION_MONITOR_INTERVAL_SECONDS = 0.4
_OPEN_POSITION_SIDES = {"Buy", "Sell"}
_ACTIVE_ORDER_STATUSES = {"Created", "Untriggered", "Triggered", "New", "PartiallyFilled"}


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
    order_status: str | None


@dataclass(frozen=True, slots=True)
class _TakeProfitDistribution:
    percents: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class _TpQuantityReconciliation:
    quantities_before: list[float]
    quantities_after: list[float]
    planned_quantity: float
    sum_before: float
    sum_after: float
    residual_before: float
    residual_after: float
    allocated_to_last_tp: float
    decision: str


@dataclass(frozen=True, slots=True)
class _CleanupResult:
    attempted: bool
    status: str
    position_exists: bool | None
    position_closed_within_window: bool
    window_attempts: int
    remaining_registered_tp_count: int
    missing_registered_tp_count: int
    found_count: int
    cancelled_count: int
    failed_count: int
    failures: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class _ExecutionMonitorResult:
    started: bool
    websocket_started: bool
    websocket_connected: bool
    websocket_authenticated: bool
    websocket_subscribed: bool
    rest_fallback_used: bool
    attempts: int
    position_closed_within_window: bool
    cleanup_completed_within_window: bool
    remaining_execution_orders: list[dict[str, object]]
    status: str
    cleanup_status: str
    cleanup_found_count: int
    cleanup_cancelled_count: int
    cleanup_failed_count: int
    cleanup_missing_registered_tp_count: int
    cleanup_failures: list[dict[str, object]]


class TradeExecutor:
    """Executa entrada de ordem market na Bybit testnet com proteções explícitas."""

    def __init__(
        self,
        *,
        settings: Settings,
        execution_client: BybitExecutionClient,
        private_ws_monitor: BybitPrivateExecutionWsMonitor | None = None,
    ) -> None:
        self._flags = _ExecutionFlags(
            dry_run=settings.dry_run,
            enable_order_execution=settings.enable_order_execution,
            bybit_testnet=settings.bybit_testnet,
        )
        self._tp_distribution = _TakeProfitDistribution(
            percents=(
                settings.tp1_percent,
                settings.tp2_percent,
                settings.tp3_percent,
                settings.tp4_percent,
            )
        )
        self._validate_take_profit_distribution(self._tp_distribution.percents)
        self._execution_client = execution_client
        self._private_ws_monitor = private_ws_monitor

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
                position_idx=_ONE_WAY_POSITION_IDX,
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

        sl_response_summary: dict[str, object] = {}
        sl_attempted = False
        sl_configured = False
        sl_status = "not_attempted"
        sl_reason: str | None = None

        if (
            order_confirmed
            and _is_position_ready_for_stop_loss(confirmation.order_status)
            and plan.eligible
            and _has_normalized_stop_loss(plan.normalized_stop_loss)
        ):
            sl_attempted = True
            normalized_stop_loss = _format_price(plan.normalized_stop_loss)
            try:
                sl_response = self._execution_client.set_trading_stop(
                    request=BybitSetTradingStopRequest(
                        category=plan.category,
                        symbol=plan.symbol,
                        stop_loss=normalized_stop_loss,
                        position_idx=_ONE_WAY_POSITION_IDX,
                    )
                )
                sl_response_summary = _build_stop_loss_summary(sl_response)
                sl_configured = True
                sl_status = "configured"
                sl_reason = None
            except BybitExecutionClientError as exc:
                sl_response_summary = {
                    "requestAccepted": False,
                    "error": str(exc),
                }
                sl_configured = False
                sl_status = "failed"
                sl_reason = str(exc)

            LOGGER.info(
                "Configuração de stop loss pós-confirmação concluída. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s normalized_stop_loss=%s positionIdx=%s stop_loss_status=%s reason=%s",
                plan.symbol,
                plan.category,
                plan.planned_entry_side,
                summary.get("orderId"),
                summary.get("orderLinkId"),
                normalized_stop_loss,
                _ONE_WAY_POSITION_IDX,
                sl_status,
                sl_reason,
            )
        else:
            LOGGER.info(
                "Configuração de stop loss não acionada. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s normalized_stop_loss=%s positionIdx=%s confirmation_status=%s order_status=%s plan_eligible=%s",
                plan.symbol,
                plan.category,
                plan.planned_entry_side,
                summary.get("orderId"),
                summary.get("orderLinkId"),
                plan.normalized_stop_loss,
                _ONE_WAY_POSITION_IDX,
                confirmation.status,
                confirmation.order_status,
                plan.eligible,
            )

        tp_response_summaries: list[dict[str, object]] = []
        tp_failures: list[dict[str, object]] = []
        tp_attempted = False
        tp_reconciliation_summary: dict[str, object] = {}

        if order_confirmed and _is_position_ready_for_stop_loss(confirmation.order_status) and plan.eligible:
            tp_attempted = True
            tp_reconciliation = self._calculate_tp_quantities(
                symbol=plan.symbol,
                category=plan.category,
                planned_quantity=plan.planned_quantity,
                qty_step=plan.qty_step,
            )
            partial_quantities = tp_reconciliation.quantities_after
            tp_reconciliation_summary = {
                "plannedQuantity": tp_reconciliation.planned_quantity,
                "sumBefore": tp_reconciliation.sum_before,
                "sumAfter": tp_reconciliation.sum_after,
                "residualBefore": tp_reconciliation.residual_before,
                "residualAfter": tp_reconciliation.residual_after,
                "allocatedToLastTp": tp_reconciliation.allocated_to_last_tp,
                "decision": tp_reconciliation.decision,
            }

            if len(plan.normalized_take_profits) != 4:
                raise TradeExecutionError(
                    "Plano inválido para TPs: normalized_take_profits deve conter 4 níveis."
                )

            tp_side = "Sell" if plan.planned_entry_side == "Buy" else "Buy"

            for index, (tp_price, tp_quantity) in enumerate(
                zip(plan.normalized_take_profits, partial_quantities, strict=True),
                start=1,
            ):
                order_link_id = f"tp{index}-{plan.symbol.lower()}-{uuid.uuid4().hex[:10]}"
                qty = _format_qty(tp_quantity, qty_step=plan.qty_step)
                price = _format_price(tp_price)
                try:
                    response_tp = self._execution_client.place_reduce_only_limit_order(
                        request=BybitReduceOnlyLimitOrderRequest(
                            category=plan.category,
                            symbol=plan.symbol,
                            side=tp_side,
                            qty=qty,
                            price=price,
                            position_idx=_ONE_WAY_POSITION_IDX,
                            order_link_id=order_link_id,
                        )
                    )
                    summary_tp = _build_take_profit_summary(
                        response=response_tp,
                        tp_index=index,
                        tp_price=price,
                        tp_qty=qty,
                    )
                    tp_response_summaries.append(summary_tp)
                    LOGGER.info(
                        "Envio de TP concluído. symbol=%s category=%s side=%s entryOrderId=%s entryOrderLinkId=%s tp_index=%s tp_price=%s tp_qty=%s reduceOnly=%s result=%s",
                        plan.symbol,
                        plan.category,
                        tp_side,
                        summary.get("orderId"),
                        summary.get("orderLinkId"),
                        index,
                        price,
                        qty,
                        True,
                        "accepted",
                    )
                except BybitExecutionClientError as exc:
                    failure = {
                        "tpIndex": index,
                        "reason": str(exc),
                        "price": price,
                        "qty": qty,
                    }
                    tp_failures.append(failure)
                    tp_response_summaries.append(
                        {
                            "tpIndex": index,
                            "tpPrice": price,
                            "tpQty": qty,
                            "requestAccepted": False,
                            "error": str(exc),
                        }
                    )
                    LOGGER.error(
                        "Falha no envio de TP. symbol=%s category=%s side=%s entryOrderId=%s entryOrderLinkId=%s tp_index=%s tp_price=%s tp_qty=%s reduceOnly=%s reason=%s",
                        plan.symbol,
                        plan.category,
                        tp_side,
                        summary.get("orderId"),
                        summary.get("orderLinkId"),
                        index,
                        price,
                        qty,
                        True,
                        exc,
                    )
        else:
            LOGGER.info(
                "Configuração de TPs não acionada. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s confirmation_status=%s order_status=%s plan_eligible=%s",
                plan.symbol,
                plan.category,
                plan.planned_entry_side,
                summary.get("orderId"),
                summary.get("orderLinkId"),
                confirmation.status,
                confirmation.order_status,
                plan.eligible,
            )

        tp_attempted_count = len(tp_response_summaries)
        tp_failed_count = len(tp_failures)
        tp_accepted_count = tp_attempted_count - tp_failed_count
        tp_status = _resolve_tp_status(
            attempted=tp_attempted,
            attempted_count=tp_attempted_count,
            failed_count=tp_failed_count,
        )
        registered_tp_orders = _collect_registered_tp_orders(tp_response_summaries)
        cleanup = _CleanupResult(
            attempted=False,
            status="not_attempted",
            position_exists=None,
            position_closed_within_window=False,
            window_attempts=0,
            remaining_registered_tp_count=0,
            missing_registered_tp_count=0,
            found_count=0,
            cancelled_count=0,
            failed_count=0,
            failures=[],
        )
        execution_monitor = _ExecutionMonitorResult(
            started=False,
            websocket_started=False,
            websocket_connected=False,
            websocket_authenticated=False,
            websocket_subscribed=False,
            rest_fallback_used=False,
            attempts=0,
            position_closed_within_window=False,
            cleanup_completed_within_window=False,
            remaining_execution_orders=[],
            status="not_started",
            cleanup_status="not_attempted",
            cleanup_found_count=0,
            cleanup_cancelled_count=0,
            cleanup_failed_count=0,
            cleanup_missing_registered_tp_count=0,
            cleanup_failures=[],
        )
        if order_confirmed and registered_tp_orders:
            execution_monitor = self._run_execution_monitor_window(
                symbol=plan.symbol,
                category=plan.category,
                side=plan.planned_entry_side,
                entry_order_id=_as_optional_string(summary.get("orderId")),
                entry_order_link_id=_as_optional_string(summary.get("orderLinkId")),
                registered_tp_orders=registered_tp_orders,
            )
            cleanup = self._build_cleanup_from_monitor(execution_monitor)

        success, success_reason = _evaluate_overall_success(
            order_confirmed=order_confirmed,
            sl_attempted=sl_attempted,
            sl_configured=sl_configured,
            tp_attempted=tp_attempted,
            tp_status=tp_status,
            cleanup_status=cleanup.status,
        )
        summary["successReason"] = success_reason
        LOGGER.info(
            "Resultado final de execução. symbol=%s category=%s side=%s orderId=%s orderLinkId=%s success=%s reason=%s",
            plan.symbol,
            plan.category,
            plan.planned_entry_side,
            summary.get("orderId"),
            summary.get("orderLinkId"),
            success,
            success_reason,
        )

        return ExecutionResult(
            symbol=plan.symbol,
            category=plan.category,
            side=plan.planned_entry_side,
            entry_status=confirmation.status,
            order_attempted=True,
            order_sent=True,
            order_confirmed=order_confirmed,
            stop_loss_attempted=sl_attempted,
            stop_loss_configured=sl_configured,
            stop_loss_status=sl_status,
            stop_loss_reason=sl_reason,
            take_profit_attempted=tp_attempted,
            take_profit_status=tp_status,
            take_profit_attempted_count=tp_attempted_count,
            take_profit_accepted_count=tp_accepted_count,
            take_profit_failed_count=tp_failed_count,
            take_profit_failures=tp_failures,
            registered_take_profit_orders=registered_tp_orders,
            take_profit_reconciliation_summary=tp_reconciliation_summary,
            cleanup_attempted=cleanup.attempted,
            cleanup_status=cleanup.status,
            cleanup_position_exists=cleanup.position_exists,
            cleanup_position_closed_within_window=cleanup.position_closed_within_window,
            cleanup_window_attempts=cleanup.window_attempts,
            cleanup_remaining_registered_tp_count=cleanup.remaining_registered_tp_count,
            cleanup_missing_registered_tp_count=cleanup.missing_registered_tp_count,
            cleanup_found_count=cleanup.found_count,
            cleanup_cancelled_count=cleanup.cancelled_count,
            cleanup_failed_count=cleanup.failed_count,
            cleanup_failure_reasons=cleanup.failures,
            monitor_started=execution_monitor.started,
            monitor_websocket_started=execution_monitor.websocket_started,
            monitor_websocket_connected=execution_monitor.websocket_connected,
            monitor_websocket_authenticated=execution_monitor.websocket_authenticated,
            monitor_websocket_subscribed=execution_monitor.websocket_subscribed,
            monitor_rest_fallback_used=execution_monitor.rest_fallback_used,
            monitor_attempts=execution_monitor.attempts,
            monitor_position_closed_within_window=execution_monitor.position_closed_within_window,
            monitor_cleanup_completed_within_window=execution_monitor.cleanup_completed_within_window,
            monitor_remaining_execution_orders=execution_monitor.remaining_execution_orders,
            monitor_status=execution_monitor.status,
            blocked_by_dry_run=False,
            blocked_by_execution_flag=False,
            blocked_by_testnet_guard=False,
            blocked_reason=None,
            confirmation_status=confirmation.status,
            confirmation_reason=confirmation.reason,
            bybit_response_summary=summary,
            stop_loss_response_summary=sl_response_summary,
            take_profit_response_summaries=tp_response_summaries,
            client_order_context=client_order_context,
            success=success,
        )

    def _run_execution_monitor_window(
        self,
        *,
        symbol: str,
        category: str,
        side: str,
        entry_order_id: str | None,
        entry_order_link_id: str | None,
        registered_tp_orders: list[dict[str, object]],
    ) -> _ExecutionMonitorResult:
        ws_started = self._private_ws_monitor is not None
        ws_connected = False
        ws_authenticated = False
        ws_subscribed = False
        rest_fallback_used = False

        if self._private_ws_monitor is not None:
            LOGGER.info(
                "Monitor websocket privado da execução iniciado. symbol=%s category=%s side=%s entryOrderId=%s entryOrderLinkId=%s registered_tps=%s",
                symbol,
                category,
                side,
                entry_order_id,
                entry_order_link_id,
                len(registered_tp_orders),
            )
            try:
                ws_result = self._private_ws_monitor.run_window(
                    symbol=symbol,
                    category=category,
                    side=side,
                    entry_order_id=entry_order_id,
                    entry_order_link_id=entry_order_link_id,
                    registered_tp_orders=registered_tp_orders,
                    max_attempts=_EXECUTION_MONITOR_MAX_ATTEMPTS,
                    interval_seconds=_EXECUTION_MONITOR_INTERVAL_SECONDS,
                )
                ws_connected = ws_result.connected
                ws_authenticated = ws_result.authenticated
                ws_subscribed = ws_result.subscribed
                LOGGER.info(
                    "Monitor websocket privado finalizado. symbol=%s category=%s side=%s connected=%s authenticated=%s subscribed=%s events_received=%s reason=%s position_closed=%s",
                    symbol,
                    category,
                    side,
                    ws_result.connected,
                    ws_result.authenticated,
                    ws_result.subscribed,
                    ws_result.events_received,
                    ws_result.reason,
                    ws_result.position_closed_confirmed,
                )
                if ws_result.position_closed_confirmed:
                    return self._cleanup_after_position_closed(
                        symbol=symbol,
                        category=category,
                        side=side,
                        entry_order_id=entry_order_id,
                        entry_order_link_id=entry_order_link_id,
                        registered_tp_orders=registered_tp_orders,
                        attempts_used=_EXECUTION_MONITOR_MAX_ATTEMPTS,
                        websocket_started=ws_started,
                        websocket_connected=ws_connected,
                        websocket_authenticated=ws_authenticated,
                        websocket_subscribed=ws_subscribed,
                        rest_fallback_used=False,
                    )
                rest_fallback_used = True
                LOGGER.warning(
                    "Fallback REST acionado: websocket sem confirmação final dentro da janela. symbol=%s category=%s side=%s",
                    symbol,
                    category,
                    side,
                )
            except BybitPrivateWsMonitorError as exc:
                rest_fallback_used = True
                LOGGER.warning(
                    "Fallback REST acionado: falha no websocket privado. symbol=%s category=%s side=%s reason=%s",
                    symbol,
                    category,
                    side,
                    exc,
                )

        attempts_used = 0
        position_closed_within_window = False
        LOGGER.info(
            "Monitor curto REST da execução iniciado. symbol=%s category=%s side=%s entryOrderId=%s entryOrderLinkId=%s registered_tps=%s max_attempts=%s interval_seconds=%s",
            symbol,
            category,
            side,
            entry_order_id,
            entry_order_link_id,
            len(registered_tp_orders),
            _EXECUTION_MONITOR_MAX_ATTEMPTS,
            _EXECUTION_MONITOR_INTERVAL_SECONDS,
        )
        for attempt in range(1, _EXECUTION_MONITOR_MAX_ATTEMPTS + 1):
            attempts_used = attempt
            try:
                position_exists = self._has_open_position(symbol=symbol, category=category)
            except BybitExecutionClientError as exc:
                return _ExecutionMonitorResult(
                    started=True,
                    websocket_started=ws_started,
                    websocket_connected=ws_connected,
                    websocket_authenticated=ws_authenticated,
                    websocket_subscribed=ws_subscribed,
                    rest_fallback_used=rest_fallback_used,
                    attempts=attempts_used,
                    position_closed_within_window=False,
                    cleanup_completed_within_window=False,
                    remaining_execution_orders=[],
                    status="started_failed_with_safe_fallback",
                    cleanup_status="failed",
                    cleanup_found_count=0,
                    cleanup_cancelled_count=0,
                    cleanup_failed_count=1,
                    cleanup_missing_registered_tp_count=0,
                    cleanup_failures=[{"reason": str(exc), "step": "position_check"}],
                )
            if position_exists is False:
                position_closed_within_window = True
                break
            if attempt < _EXECUTION_MONITOR_MAX_ATTEMPTS:
                time.sleep(_EXECUTION_MONITOR_INTERVAL_SECONDS)

        if not position_closed_within_window:
            remaining_orders = self._fetch_remaining_registered_orders(
                category=category,
                symbol=symbol,
                registered_tp_orders=registered_tp_orders,
            )
            return _ExecutionMonitorResult(
                started=True,
                websocket_started=ws_started,
                websocket_connected=ws_connected,
                websocket_authenticated=ws_authenticated,
                websocket_subscribed=ws_subscribed,
                rest_fallback_used=rest_fallback_used,
                attempts=attempts_used,
                position_closed_within_window=False,
                cleanup_completed_within_window=False,
                remaining_execution_orders=remaining_orders,
                status="started_window_expired",
                cleanup_status="position_not_closed_in_window",
                cleanup_found_count=len(remaining_orders),
                cleanup_cancelled_count=0,
                cleanup_failed_count=0,
                cleanup_missing_registered_tp_count=0,
                cleanup_failures=[],
            )

        return self._cleanup_after_position_closed(
            symbol=symbol,
            category=category,
            side=side,
            entry_order_id=entry_order_id,
            entry_order_link_id=entry_order_link_id,
            registered_tp_orders=registered_tp_orders,
            attempts_used=attempts_used,
            websocket_started=ws_started,
            websocket_connected=ws_connected,
            websocket_authenticated=ws_authenticated,
            websocket_subscribed=ws_subscribed,
            rest_fallback_used=rest_fallback_used,
        )

    def _cleanup_after_position_closed(
        self,
        *,
        symbol: str,
        category: str,
        side: str,
        entry_order_id: str | None,
        entry_order_link_id: str | None,
        registered_tp_orders: list[dict[str, object]],
        attempts_used: int,
        websocket_started: bool,
        websocket_connected: bool,
        websocket_authenticated: bool,
        websocket_subscribed: bool,
        rest_fallback_used: bool,
    ) -> _ExecutionMonitorResult:
        failures: list[dict[str, object]] = []
        remaining_tp_orders: list[dict[str, object]] = []
        missing_registered_tp_count = 0
        for tp_order in registered_tp_orders:
            snapshot = self._fetch_order_snapshot(
                category=category,
                symbol=symbol,
                order_id=_as_optional_string(tp_order.get("orderId")),
                order_link_id=_as_optional_string(tp_order.get("orderLinkId")),
            )
            if snapshot is None:
                missing_registered_tp_count += 1
                continue
            remaining_tp_orders.append(snapshot)

        found_count = len(remaining_tp_orders)
        cancelled_count = 0
        if found_count == 0:
            return _ExecutionMonitorResult(
                started=True,
                websocket_started=websocket_started,
                websocket_connected=websocket_connected,
                websocket_authenticated=websocket_authenticated,
                websocket_subscribed=websocket_subscribed,
                rest_fallback_used=rest_fallback_used,
                attempts=attempts_used,
                position_closed_within_window=True,
                cleanup_completed_within_window=True,
                remaining_execution_orders=[],
                status="started_position_closed_cleanup_done",
                cleanup_status="not_needed",
                cleanup_found_count=0,
                cleanup_cancelled_count=0,
                cleanup_failed_count=0,
                cleanup_missing_registered_tp_count=missing_registered_tp_count,
                cleanup_failures=[],
            )

        for order in remaining_tp_orders:
            order_id = _as_optional_string(order.get("orderId"))
            order_link_id = _as_optional_string(order.get("orderLinkId"))
            try:
                self._execution_client.cancel_order(
                    category=category,
                    symbol=symbol,
                    order_id=order_id,
                    order_link_id=order_link_id,
                )
                cancelled_count += 1
            except BybitExecutionClientError as exc:
                failures.append({"orderId": order_id, "orderLinkId": order_link_id, "reason": str(exc)})

        failed_count = len(failures)
        cleanup_status = "cancelled_all"
        if failed_count > 0 and cancelled_count > 0:
            cleanup_status = "partial"
        elif failed_count > 0:
            cleanup_status = "failed"

        remaining_after_cleanup = self._fetch_remaining_registered_orders(
            category=category,
            symbol=symbol,
            registered_tp_orders=registered_tp_orders,
        )
        cleanup_completed = cleanup_status in {"cancelled_all", "not_needed"}
        return _ExecutionMonitorResult(
            started=True,
            websocket_started=websocket_started,
            websocket_connected=websocket_connected,
            websocket_authenticated=websocket_authenticated,
            websocket_subscribed=websocket_subscribed,
            rest_fallback_used=rest_fallback_used,
            attempts=attempts_used,
            position_closed_within_window=True,
            cleanup_completed_within_window=cleanup_completed,
            remaining_execution_orders=remaining_after_cleanup,
            status=("started_position_closed_cleanup_done" if cleanup_completed else "started_failed_with_safe_fallback"),
            cleanup_status=cleanup_status,
            cleanup_found_count=found_count,
            cleanup_cancelled_count=cancelled_count,
            cleanup_failed_count=failed_count,
            cleanup_missing_registered_tp_count=missing_registered_tp_count,
            cleanup_failures=failures,
        )

    def _build_cleanup_from_monitor(self, monitor: _ExecutionMonitorResult) -> _CleanupResult:
        return _CleanupResult(
            attempted=True,
            status=monitor.cleanup_status,
            position_exists=(
                None if monitor.cleanup_status == "failed" else not monitor.position_closed_within_window
            ),
            position_closed_within_window=monitor.position_closed_within_window,
            window_attempts=monitor.attempts,
            remaining_registered_tp_count=len(monitor.remaining_execution_orders),
            missing_registered_tp_count=monitor.cleanup_missing_registered_tp_count,
            found_count=monitor.cleanup_found_count,
            cancelled_count=monitor.cleanup_cancelled_count,
            failed_count=monitor.cleanup_failed_count,
            failures=monitor.cleanup_failures,
        )

    def _fetch_remaining_registered_orders(
        self,
        *,
        category: str,
        symbol: str,
        registered_tp_orders: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        remaining_orders: list[dict[str, object]] = []
        for tp_order in registered_tp_orders:
            snapshot = self._fetch_order_snapshot(
                category=category,
                symbol=symbol,
                order_id=_as_optional_string(tp_order.get("orderId")),
                order_link_id=_as_optional_string(tp_order.get("orderLinkId")),
            )
            if snapshot is None:
                continue
            order_status = _as_optional_string(snapshot.get("orderStatus"))
            if order_status not in _ACTIVE_ORDER_STATUSES:
                continue
            remaining_orders.append(
                {
                    "tpIndex": tp_order.get("tpIndex"),
                    "orderId": _as_optional_string(snapshot.get("orderId")),
                    "orderLinkId": _as_optional_string(snapshot.get("orderLinkId")),
                    "orderStatus": order_status,
                }
            )
        return remaining_orders

    def _has_open_position(self, *, symbol: str, category: str) -> bool:
        response = self._execution_client.get_positions(category=category, symbol=symbol)
        positions = self._execution_client.extract_position_list(response)

        for position in positions:
            position_side = _as_optional_string(position.get("side"))
            size = _as_optional_string(position.get("size"))
            if position_side not in _OPEN_POSITION_SIDES:
                continue
            if size is None:
                continue
            try:
                if Decimal(size) > 0:
                    return True
            except (InvalidOperation, ValueError):
                continue

        return False


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

    def _validate_take_profit_distribution(self, percents: tuple[float, float, float, float]) -> None:
        if any(percent < 0 for percent in percents):
            raise TradeExecutionError("Configuração inválida de TPs: percentuais não podem ser negativos.")

        total = sum(percents)
        if abs(total - 100.0) > 1e-9:
            raise TradeExecutionError(
                "Configuração inválida de TPs: a soma de TP1_PERCENT, TP2_PERCENT, "
                f"TP3_PERCENT e TP4_PERCENT deve ser 100. Recebido: {total}."
            )

    def _calculate_tp_quantities(
        self,
        *,
        symbol: str,
        category: str,
        planned_quantity: float,
        qty_step: str | None,
    ) -> _TpQuantityReconciliation:
        if qty_step is None:
            raise TradeExecutionError("Plano inválido para TPs: qty_step ausente.")

        try:
            planned_quantity_decimal = Decimal(str(planned_quantity))
            qty_step_decimal = Decimal(qty_step)
        except (InvalidOperation, ValueError) as exc:
            raise TradeExecutionError("Plano inválido para TPs: planned_quantity/qty_step inválidos.") from exc

        if planned_quantity_decimal <= 0 or qty_step_decimal <= 0:
            raise TradeExecutionError("Plano inválido para TPs: planned_quantity e qty_step devem ser positivos.")

        quantities: list[Decimal] = []
        for index, percent in enumerate(self._tp_distribution.percents, start=1):
            raw_qty = planned_quantity_decimal * Decimal(str(percent)) / Decimal("100")
            normalized_qty = (raw_qty / qty_step_decimal).to_integral_value(rounding=ROUND_DOWN) * qty_step_decimal
            if normalized_qty <= 0:
                raise TradeExecutionError(
                    "Quantidade parcial inválida para TP após normalização por qtyStep: "
                    f"tp_index={index} raw_qty={raw_qty} normalized_qty={normalized_qty} qty_step={qty_step}."
                )
            quantities.append(normalized_qty)

        quantities_before = [float(value) for value in quantities]
        sum_before = sum(quantities, Decimal("0"))
        residual_before = planned_quantity_decimal - sum_before
        if sum_before > planned_quantity_decimal:
            raise TradeExecutionError(
                "Quantidade parcial inválida para TPs: soma antes da reconciliação excede planned_quantity."
            )

        allocated_to_last_tp = Decimal("0")
        decision = "exact_distribution_after_normalization"
        if residual_before >= qty_step_decimal:
            allocatable_residual = (
                (residual_before / qty_step_decimal).to_integral_value(rounding=ROUND_DOWN)
                * qty_step_decimal
            )
            if allocatable_residual > 0:
                quantities[-1] = quantities[-1] + allocatable_residual
                allocated_to_last_tp = allocatable_residual
                decision = "allocated_to_last_tp"

        sum_after = sum(quantities, Decimal("0"))
        residual_after = planned_quantity_decimal - sum_after
        if sum_after > planned_quantity_decimal:
            raise TradeExecutionError(
                "Quantidade parcial inválida para TPs: soma após reconciliação excede planned_quantity."
            )

        if residual_after >= qty_step_decimal:
            decision = "unreconciled_residual_due_to_qty_step_or_distribution"
        elif decision != "allocated_to_last_tp" and residual_after > 0:
            decision = "residual_below_qty_step_not_allocated"

        quantities_after = [float(value) for value in quantities]
        if any(value <= 0 for value in quantities_after):
            raise TradeExecutionError(
                "Quantidade parcial inválida para TPs: parcela <= 0 após reconciliação."
            )

        LOGGER.info(
            "Reconciliação de quantidades de TP. symbol=%s category=%s planned_quantity=%s qtyStep=%s tp_quantities_before=%s tp_quantities_after=%s residual_before=%s residual_after=%s decision=%s",
            symbol,
            category,
            float(planned_quantity_decimal),
            qty_step,
            quantities_before,
            quantities_after,
            float(residual_before),
            float(residual_after),
            decision,
        )

        return _TpQuantityReconciliation(
            quantities_before=quantities_before,
            quantities_after=quantities_after,
            planned_quantity=float(planned_quantity_decimal),
            sum_before=float(sum_before),
            sum_after=float(sum_after),
            residual_before=float(residual_before),
            residual_after=float(residual_after),
            allocated_to_last_tp=float(allocated_to_last_tp),
            decision=decision,
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
                    return _ConfirmationState(status="confirmed", reason="orderStatus confirmado via REST", order_status=status)

                if mapped_status == "rejected":
                    reason = rejection_reason or "Ordem rejeitada conforme orderStatus da Bybit."
                    return _ConfirmationState(status="rejected", reason=reason, order_status=status)

                if mapped_status == "cancelled":
                    reason = cancel_type or "Ordem cancelada conforme orderStatus da Bybit."
                    return _ConfirmationState(status="cancelled", reason=reason, order_status=status)

                seen_pending = True

            if attempt < _MAX_CONFIRMATION_ATTEMPTS:
                time.sleep(_CONFIRMATION_INTERVAL_SECONDS)

        if not seen_snapshot:
            return _ConfirmationState(
                status="not_found",
                reason="Ordem não encontrada em open orders e history dentro da janela de confirmação.",
                order_status=None,
            )

        if seen_pending:
            return _ConfirmationState(
                status="timeout",
                reason="Timeout aguardando transição de orderStatus para estado final confirmado/rejeitado/cancelado.",
                order_status=None,
            )

        return _ConfirmationState(
            status="pending_confirmation",
            reason="ACK recebido, porém sem confirmação conclusiva nesta fase.",
            order_status=None,
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
            monitor_rest_fallback_used=False,
            monitor_attempts=0,
            monitor_position_closed_within_window=False,
            monitor_cleanup_completed_within_window=False,
            monitor_remaining_execution_orders=[],
            monitor_status="not_started",
            blocked_by_dry_run=blocked_by_dry_run,
            blocked_by_execution_flag=blocked_by_execution_flag,
            blocked_by_testnet_guard=blocked_by_testnet_guard,
            blocked_reason=reason,
            confirmation_status="not_sent",
            confirmation_reason=reason,
            bybit_response_summary={},
            stop_loss_response_summary={},
            take_profit_response_summaries=[],
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


def _format_price(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def _has_normalized_stop_loss(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _is_position_ready_for_stop_loss(order_status: str | None) -> bool:
    return order_status in {"PartiallyFilled", "Filled"}


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


def _build_stop_loss_summary(response: dict[str, object]) -> dict[str, object]:
    return {
        "retCode": response.get("retCode"),
        "retMsg": response.get("retMsg"),
        "requestAccepted": response.get("retCode") == 0,
    }


def _build_take_profit_summary(
    *,
    response: dict[str, object],
    tp_index: int,
    tp_price: str,
    tp_qty: str,
) -> dict[str, object]:
    result = response.get("result")
    result_dict = result if isinstance(result, dict) else {}
    return {
        "tpIndex": tp_index,
        "tpPrice": tp_price,
        "tpQty": tp_qty,
        "retCode": response.get("retCode"),
        "retMsg": response.get("retMsg"),
        "orderId": result_dict.get("orderId"),
        "orderLinkId": result_dict.get("orderLinkId"),
        "requestAccepted": response.get("retCode") == 0,
    }


def _collect_registered_tp_orders(tp_summaries: list[dict[str, object]]) -> list[dict[str, object]]:
    registered: list[dict[str, object]] = []
    for summary in tp_summaries:
        if summary.get("requestAccepted") is not True:
            continue
        order_id = _as_optional_string(summary.get("orderId"))
        order_link_id = _as_optional_string(summary.get("orderLinkId"))
        if order_id is None and order_link_id is None:
            continue
        registered.append(
            {
                "tpIndex": summary.get("tpIndex"),
                "orderId": order_id,
                "orderLinkId": order_link_id,
            }
        )
    return registered


def _resolve_tp_status(
    *,
    attempted: bool,
    attempted_count: int,
    failed_count: int,
) -> TakeProfitStatus:
    if not attempted:
        return "not_attempted"
    if attempted_count == 0:
        return "failed"
    if failed_count == 0:
        return "all_configured"
    if failed_count < attempted_count:
        return "partial"
    return "failed"


def _evaluate_overall_success(
    *,
    order_confirmed: bool,
    sl_attempted: bool,
    sl_configured: bool,
    tp_attempted: bool,
    tp_status: TakeProfitStatus,
    cleanup_status: str,
) -> tuple[bool, str]:
    if not order_confirmed:
        return False, "entrada_nao_confirmada"

    if sl_attempted and not sl_configured:
        return False, "stop_loss_falhou"

    if tp_attempted and tp_status != "all_configured":
        return False, "take_profits_com_falha"

    if cleanup_status in {"failed", "partial"}:
        return False, "limpeza_de_ordens_com_falha"

    if not tp_attempted:
        return True, "entrada_confirmada_sem_tentativa_de_tp_por_fluxo_valido"

    return True, "entrada_confirmada_com_stop_loss_e_take_profits_ok"


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
