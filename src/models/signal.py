from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class Signal:
    symbol: str
    side: str
    entry_min: float
    entry_max: float
    take_profits: list[float]
    stop_loss: float
    raw_text: str
    operational_intent: str = ""
    origin: str = "telegram"
    entry_eligible: bool | None = None
    entry_validation_reason: str | None = None
    current_price: float | None = None
    instrument_status: str | None = None
    instrument_tick_size: str | None = None
    instrument_qty_step: str | None = None
    instrument_min_order_qty: str | None = None
    instrument_min_notional_value: str | None = None
    instrument_max_leverage: str | None = None

    def __post_init__(self) -> None:
        normalized_side = self.side.strip().upper()
        if normalized_side not in {"LONG", "SHORT"}:
            raise ValueError("side inválido: use LONG ou SHORT")

        self.side = normalized_side
        self.operational_intent = (
            "open_long" if self.side == "LONG" else "open_short"
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
