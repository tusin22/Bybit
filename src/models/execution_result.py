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


@dataclass(slots=True)
class ExecutionResult:
    symbol: str
    category: str
    side: str
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
    take_profit_reconciliation_summary: dict[str, object]
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
