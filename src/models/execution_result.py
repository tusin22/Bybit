from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ExecutionResult:
    symbol: str
    category: str
    side: str
    order_attempted: bool
    order_sent: bool
    order_confirmed: bool
    blocked_by_dry_run: bool
    blocked_by_execution_flag: bool
    blocked_by_testnet_guard: bool
    blocked_reason: str | None
    confirmation_status: str
    bybit_response_summary: dict[str, object]
    client_order_context: str | None
    success: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
