from __future__ import annotations

from dataclasses import dataclass

from src.models.signal import Signal


@dataclass(frozen=True, slots=True)
class EntryValidationResult:
    eligible: bool
    reason: str


def validate_entry_window(*, signal: Signal, current_price: float) -> EntryValidationResult:
    if signal.entry_min <= current_price <= signal.entry_max:
        return EntryValidationResult(
            eligible=True,
            reason="Preço atual dentro da faixa de entrada do sinal.",
        )

    return EntryValidationResult(
        eligible=False,
        reason=(
            "Preço atual fora da faixa de entrada do sinal "
            f"({signal.entry_min} - {signal.entry_max})."
        ),
    )
