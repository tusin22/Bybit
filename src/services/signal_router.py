from __future__ import annotations

from src.bybit.client import BybitReadOnlyClient
from src.bybit.validators import validate_entry_window
from src.models.signal import Signal


class SignalRouter:
    """Valida sinais com dados read-only da Bybit antes de qualquer execução futura."""

    def __init__(self, *, bybit_client: BybitReadOnlyClient) -> None:
        self._bybit_client = bybit_client

    def enrich_with_bybit_validation(self, signal: Signal) -> Signal:
        instrument = self._bybit_client.get_instrument_info(symbol=signal.symbol)
        current_price = self._bybit_client.get_last_price(symbol=signal.symbol)
        validation = validate_entry_window(signal=signal, current_price=current_price)

        signal.current_price = current_price
        signal.entry_eligible = validation.eligible
        signal.entry_validation_reason = validation.reason
        signal.instrument_status = instrument.status
        signal.instrument_tick_size = instrument.tick_size
        signal.instrument_qty_step = instrument.qty_step
        return signal
