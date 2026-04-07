from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from pybit.unified_trading import HTTP, WebSocket

from src.analysis.auto_signal_engine import ClosedCandle

LOGGER = logging.getLogger(__name__)


class BybitMarketFeedError(RuntimeError):
    pass


@dataclass(slots=True)
class AutoAnalysisState:
    symbol: str
    interval: str
    lastClosedCandleTime: int | None
    lastPrice: float | None
    analyzerStatus: str
    lastSignalSide: str | None
    lastSignalReason: str | None
    lastExecutionAttempted: bool
    lastExecutionTradeStatus: str | None
    cooldownUntilCandle: int | None
    openPositionDetected: bool
    updatedAt: str


class AutoAnalysisStateStore:
    def __init__(self, *, state_file: Path) -> None:
        self._state_file = state_file

    def save(self, *, state: AutoAnalysisState) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class BybitMarketFeed:
    def __init__(self, *, testnet: bool, symbol: str, interval: str) -> None:
        self._http = HTTP(testnet=testnet)
        self._ws = WebSocket(channel_type="linear", testnet=testnet)
        self._symbol = symbol
        self._interval = interval

    def bootstrap_closed_candles(self, *, limit: int = 300) -> list[ClosedCandle]:
        response = self._http.get_kline(
            category="linear",
            symbol=self._symbol,
            interval=self._interval,
            limit=limit,
        )
        ret_code = response.get("retCode")
        if ret_code != 0:
            raise BybitMarketFeedError(f"Falha get_kline retCode={ret_code} retMsg={response.get('retMsg')}")

        result = response.get("result")
        records = result.get("list", []) if isinstance(result, dict) else []
        candles: list[ClosedCandle] = []
        for row in reversed(records):
            if not isinstance(row, list) or len(row) < 6:
                continue
            candles.append(
                ClosedCandle(
                    start_ms=int(row[0]),
                    open_price=float(row[1]),
                    high_price=float(row[2]),
                    low_price=float(row[3]),
                    close_price=float(row[4]),
                    volume=float(row[5]),
                    confirm=True,
                )
            )
        return candles

    def subscribe_closed_kline(self, *, on_closed_candle: Callable[[ClosedCandle], None]) -> None:
        def _callback(message: dict[str, object]) -> None:
            data = message.get("data")
            if not isinstance(data, list):
                return
            for item in data:
                if not isinstance(item, dict):
                    continue
                confirm = bool(item.get("confirm"))
                candle = ClosedCandle(
                    start_ms=int(item.get("start", 0)),
                    open_price=float(item.get("open", 0.0)),
                    high_price=float(item.get("high", 0.0)),
                    low_price=float(item.get("low", 0.0)),
                    close_price=float(item.get("close", 0.0)),
                    volume=float(item.get("volume", 0.0)),
                    confirm=confirm,
                )
                if candle.confirm:
                    on_closed_candle(candle)

        LOGGER.info("Iniciando stream público de kline. symbol=%s interval=%s", self._symbol, self._interval)
        self._ws.kline_stream(interval=int(self._interval), symbol=self._symbol, callback=_callback)

    def stop(self) -> None:
        try:
            self._ws.exit()
        except Exception:  # pragma: no cover
            pass
