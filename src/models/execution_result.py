from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ConfirmationStatus = Literal[
    "not_sent",
    "pending_confirmation",
    "confirmed",
    "rejected",
    "cancelled",
    "not_found",
    "timeout",
]

StopLossStatus = Literal[
    "not_attempted",
    "configured",
    "failed",
]

TakeProfitStatus = Literal[
    "not_attempted",
    "all_configured",
    "partial",
    "failed",
]

CleanupStatus = Literal[
    "not_attempted",
    "position_not_closed_in_window",
    "not_needed",
    "cancelled_all",
    "partial",
    "failed",
]

ExecutionMonitorStatus = Literal[
    "not_started",
    "started_position_closed_cleanup_done",
    "started_window_expired",
    "started_failed_with_safe_fallback",
]


@dataclass(slots=True)
class ExecutionResult:
    symbol: str
    category: str
    side: str
    entry_status: ConfirmationStatus
    order_attempted: bool
    order_sent: bool
    order_confirmed: bool
    stop_loss_attempted: bool
    stop_loss_configured: bool
    stop_loss_status: StopLossStatus
    stop_loss_reason: str | None
    take_profit_attempted: bool
    take_profit_status: TakeProfitStatus
    take_profit_attempted_count: int
    take_profit_accepted_count: int
    take_profit_failed_count: int
    take_profit_failures: list[dict[str, object]]
    registered_take_profit_orders: list[dict[str, object]]
    take_profit_reconciliation_summary: dict[str, object]
    cleanup_attempted: bool
    cleanup_status: CleanupStatus
    cleanup_position_exists: bool | None
    cleanup_position_closed_within_window: bool
    cleanup_window_attempts: int
    cleanup_remaining_registered_tp_count: int
    cleanup_missing_registered_tp_count: int
    cleanup_found_count: int
    cleanup_cancelled_count: int
    cleanup_failed_count: int
    cleanup_failure_reasons: list[dict[str, object]]
    monitor_started: bool
    monitor_websocket_started: bool
    monitor_websocket_connected: bool
    monitor_websocket_authenticated: bool
    monitor_websocket_subscribed: bool
    monitor_rest_fallback_used: bool
    monitor_attempts: int
    monitor_position_closed_within_window: bool
    monitor_cleanup_completed_within_window: bool
    monitor_remaining_execution_orders: list[dict[str, object]]
    monitor_status: ExecutionMonitorStatus
    monitor_final_decision_source: str | None
    monitor_final_decision_reason: str | None
    blocked_by_dry_run: bool
    blocked_by_execution_flag: bool
    blocked_by_testnet_guard: bool
    blocked_reason: str | None
    confirmation_status: ConfirmationStatus
    confirmation_reason: str | None
    bybit_response_summary: dict[str, object]
    stop_loss_response_summary: dict[str, object]
    take_profit_response_summaries: list[dict[str, object]]
    client_order_context: str | None
    success: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
