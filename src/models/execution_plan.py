from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ExecutionPlan:
    symbol: str
    category: str
    planned_entry_side: str
    reference_price: float
    normalized_entry_min: float
    normalized_entry_max: float
    normalized_stop_loss: float
    normalized_take_profits: list[float]
    operational_intent: str
    planned_quantity: float
    tick_size: str | None
    qty_step: str | None
    min_order_qty: str | None
    min_notional_value: str | None
    instrument_status: str | None
    eligible: bool
    ineligibility_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
