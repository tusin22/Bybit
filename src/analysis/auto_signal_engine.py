from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.models.signal import Signal


@dataclass(frozen=True, slots=True)
class ClosedCandle:
    start_ms: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    confirm: bool


@dataclass(frozen=True, slots=True)
class AutoAnalysisConfig:
    symbol: str
    interval: str
    ema_short: int
    ema_mid: int
    rsi_len: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    use_volume_filter: bool
    volume_multiplier: float
    adx_len: int
    use_adx_filter: bool
    adx_threshold: float
    cooldown_candles: int


@dataclass(slots=True)
class AutoDecision:
    side: str
    reason: str


class AutoSignalEngine:
    def __init__(self, *, config: AutoAnalysisConfig) -> None:
        self._cfg = config

    def maybe_build_signal(
        self,
        *,
        candles: list[ClosedCandle],
        last_processed_candle: int | None,
        cooldown_until_candle: int | None,
    ) -> tuple[Signal | None, str, int | None]:
        if not candles:
            return None, "Sem candles disponíveis.", cooldown_until_candle

        current = candles[-1]
        if not current.confirm:
            return None, "Candle ainda não confirmado.", cooldown_until_candle

        if last_processed_candle == current.start_ms:
            return None, "Sinal ignorado: candle já processado.", cooldown_until_candle

        if cooldown_until_candle is not None and current.start_ms <= cooldown_until_candle:
            return None, "Sinal ignorado: cooldown ativo.", cooldown_until_candle

        decision = self._evaluate(candles)
        if decision is None:
            return None, "Sem gatilho técnico fechado.", cooldown_until_candle

        entry = current.close_price
        gap = max(entry * 0.001, 1.0)
        # Observação desta fase:
        # SL/TP abaixo são provisórios para encaixe no executor atual e auditoria do fluxo.
        # Não representam paridade 1:1 com a lógica dinâmica de break-even/step stop do Pine.
        if decision.side == "LONG":
            signal = Signal(
                symbol=self._cfg.symbol,
                side="LONG",
                entry_min=entry * 0.999,
                entry_max=entry * 1.001,
                take_profits=[entry + gap, entry + 2 * gap, entry + 3 * gap, entry + 4 * gap],
                stop_loss=entry - 2 * gap,
                raw_text=f"AUTO_ANALYSIS {self._cfg.symbol} {decision.side} {self._cfg.interval}",
                origin="auto_analysis",
            )
        else:
            signal = Signal(
                symbol=self._cfg.symbol,
                side="SHORT",
                entry_min=entry * 0.999,
                entry_max=entry * 1.001,
                take_profits=[entry - gap, entry - 2 * gap, entry - 3 * gap, entry - 4 * gap],
                stop_loss=entry + 2 * gap,
                raw_text=f"AUTO_ANALYSIS {self._cfg.symbol} {decision.side} {self._cfg.interval}",
                origin="auto_analysis",
            )

        # Semântica: cooldown=1 bloqueia exatamente o próximo candle fechado.
        next_cooldown = current.start_ms + self._cfg.cooldown_candles * self._interval_ms()
        return signal, decision.reason, next_cooldown

    def _evaluate(self, candles: list[ClosedCandle]) -> AutoDecision | None:
        closes = [c.close_price for c in candles]
        highs = [c.high_price for c in candles]
        lows = [c.low_price for c in candles]
        volumes = [c.volume for c in candles]

        min_len = self._cfg.ema_mid + 2
        if self._cfg.use_volume_filter:
            min_len = max(min_len, 20)
        if self._cfg.use_adx_filter:
            min_len = max(min_len, self._cfg.adx_len + 2)
        if len(closes) < min_len:
            return None

        ema_short = _ema_series(closes, self._cfg.ema_short)
        ema_mid = _ema_series(closes, self._cfg.ema_mid)
        # MACD/RSI ficam calculados apenas para observabilidade futura nesta fase.
        _macd_line: list[float] = []
        _macd_signal: list[float] = []
        _rsi: list[float] = []
        if len(closes) >= self._cfg.macd_slow + self._cfg.macd_signal + 2:
            _macd_line = [
                a - b
                for a, b in zip(
                    _ema_series(closes, self._cfg.macd_fast),
                    _ema_series(closes, self._cfg.macd_slow),
                    strict=True,
                )
            ]
            _macd_signal = _ema_series(_macd_line, self._cfg.macd_signal)
        if len(closes) >= self._cfg.rsi_len + 2:
            _rsi = _rsi_series(closes, self._cfg.rsi_len)
        adx = _adx_series(highs, lows, closes, self._cfg.adx_len)

        if len(ema_short) < 2 or len(ema_mid) < 2:
            return None

        long_trigger = (
            ema_short[-2] <= ema_mid[-2]
            and ema_short[-1] > ema_mid[-1]
        )
        short_trigger = (
            ema_short[-2] >= ema_mid[-2]
            and ema_short[-1] < ema_mid[-1]
        )

        if self._cfg.use_volume_filter and len(volumes) >= 20:
            avg_volume = sum(volumes[-20:-1]) / 19
            if volumes[-1] < avg_volume * self._cfg.volume_multiplier:
                return None

        if self._cfg.use_adx_filter and adx is not None and adx < self._cfg.adx_threshold:
            return None

        if long_trigger:
            return AutoDecision(side="LONG", reason="EMA crossover long fechado")
        if short_trigger:
            return AutoDecision(side="SHORT", reason="EMA crossunder short fechado")
        return None

    def _interval_ms(self) -> int:
        return int(self._cfg.interval) * 60_000


def _ema_series(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    out: list[float] = []
    ema = values[0]
    for value in values:
        ema = (value * alpha) + (ema * (1 - alpha))
        out.append(ema)
    return out


def _rsi_series(values: list[float], period: int) -> list[float]:
    gains = [0.0]
    losses = [0.0]
    for idx in range(1, len(values)):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    rsi = [50.0] * len(values)

    for idx in range(period + 1, len(values)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi[idx] = 100 - (100 / (1 + rs))
    return rsi


def _adx_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> float | None:
    if len(closes) <= period + 1:
        return None

    tr_list: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)

    atr = sum(tr_list[:period]) / period
    pdm = sum(plus_dm[:period]) / period
    mdm = sum(minus_dm[:period]) / period

    dx_values: list[float] = []
    for i in range(period, len(tr_list)):
        atr = ((atr * (period - 1)) + tr_list[i]) / period
        pdm = ((pdm * (period - 1)) + plus_dm[i]) / period
        mdm = ((mdm * (period - 1)) + minus_dm[i]) / period
        plus_di = 100 * (pdm / atr) if atr > 0 else 0.0
        minus_di = 100 * (mdm / atr) if atr > 0 else 0.0
        denom = plus_di + minus_di
        dx = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0
        dx_values.append(dx)

    if not dx_values:
        return None
    return sum(dx_values[-period:]) / min(period, len(dx_values))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
