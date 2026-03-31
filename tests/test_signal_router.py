from __future__ import annotations

from src.bybit.client import InstrumentInfo
from src.models.signal import Signal
from src.services.signal_router import SignalRouter


class FakeBybitClient:
    def __init__(self, *, price: float) -> None:
        self._price = price
        self.instrument_calls = 0
        self.ticker_calls = 0

    def get_instrument_info(self, *, symbol: str, category: str = "linear") -> InstrumentInfo:
        self.instrument_calls += 1
        return InstrumentInfo(
            symbol=symbol,
            category=category,
            status="Trading",
            tick_size="0.10",
            qty_step="0.001",
            min_order_qty="0.001",
            min_notional_value="5",
            raw={"symbol": symbol},
        )

    def get_last_price(self, *, symbol: str, category: str = "linear") -> float:
        self.ticker_calls += 1
        return self._price


def _build_signal(side: str = "LONG") -> Signal:
    if side == "SHORT":
        return Signal(
            symbol="BTCUSDT",
            side=side,
            entry_min=100.0,
            entry_max=110.0,
            take_profits=[95.0, 92.0, 90.0, 88.0],
            stop_loss=115.0,
            raw_text=f"BTCUSDT | {side}",
        )

    return Signal(
        symbol="BTCUSDT",
        side=side,
        entry_min=100.0,
        entry_max=110.0,
        take_profits=[120.0, 130.0, 140.0, 150.0],
        stop_loss=90.0,
        raw_text=f"BTCUSDT | {side}",
    )


def test_signal_router_marks_signal_as_entry_eligible() -> None:
    fake_client = FakeBybitClient(price=103.0)
    router = SignalRouter(bybit_client=fake_client)
    signal = _build_signal(side="LONG")

    enriched = router.enrich_with_bybit_validation(signal)

    assert enriched.entry_eligible is True
    assert enriched.current_price == 103.0
    assert enriched.operational_intent == "open_long"
    assert enriched.instrument_status == "Trading"
    assert enriched.instrument_tick_size == "0.10"
    assert enriched.instrument_qty_step == "0.001"
    assert enriched.instrument_min_order_qty == "0.001"
    assert enriched.instrument_min_notional_value == "5"
    assert fake_client.instrument_calls == 1
    assert fake_client.ticker_calls == 1


def test_signal_router_marks_signal_as_not_eligible_for_late_entry() -> None:
    fake_client = FakeBybitClient(price=112.0)
    router = SignalRouter(bybit_client=fake_client)
    signal = _build_signal(side="SHORT")

    enriched = router.enrich_with_bybit_validation(signal)

    assert enriched.entry_eligible is False
    assert "fora da faixa" in (enriched.entry_validation_reason or "")
    assert enriched.operational_intent == "open_short"
