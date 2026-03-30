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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
