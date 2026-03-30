from __future__ import annotations

from src.bybit.validators import validate_entry_window
from src.models.signal import Signal


def _build_signal() -> Signal:
    return Signal(
        symbol="BTCUSDT",
        side="LONG",
        entry_min=100.0,
        entry_max=110.0,
        take_profits=[120.0, 130.0, 140.0, 150.0],
        stop_loss=90.0,
        raw_text="BTCUSDT | LONG",
    )


def test_validate_entry_window_eligible_when_price_inside_range() -> None:
    signal = _build_signal()

    result = validate_entry_window(signal=signal, current_price=105.0)

    assert result.eligible is True
    assert "dentro da faixa" in result.reason


def test_validate_entry_window_not_eligible_when_price_outside_range() -> None:
    signal = _build_signal()

    result = validate_entry_window(signal=signal, current_price=95.0)

    assert result.eligible is False
    assert "fora da faixa" in result.reason
