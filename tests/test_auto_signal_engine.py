from __future__ import annotations

from src.analysis.auto_signal_engine import AutoAnalysisConfig, AutoSignalEngine, ClosedCandle


def _cfg(**overrides: object) -> AutoAnalysisConfig:
    base = {
        "symbol": "BTCUSDT",
        "interval": "60",
        "ema_short": 2,
        "ema_mid": 4,
        "rsi_len": 6,
        "macd_fast": 3,
        "macd_slow": 6,
        "macd_signal": 3,
        "use_volume_filter": False,
        "volume_multiplier": 1.10,
        "adx_len": 14,
        "use_adx_filter": False,
        "adx_threshold": 14.0,
        "cooldown_candles": 1,
    }
    base.update(overrides)
    return AutoAnalysisConfig(**base)


def _candles(closes: list[float], *, volumes: list[float] | None = None) -> list[ClosedCandle]:
    if volumes is None:
        volumes = [1000.0] * len(closes)

    start = 1_700_000_000_000
    return [
        ClosedCandle(
            start_ms=start + idx * 3_600_000,
            open_price=price - 0.1,
            high_price=price + 0.3,
            low_price=price - 0.4,
            close_price=price,
            volume=volumes[idx],
            confirm=True,
        )
        for idx, price in enumerate(closes)
    ]


def test_auto_signal_long_trigger_by_ema_crossover_only() -> None:
    engine = AutoSignalEngine(config=_cfg())
    signal, reason, _ = engine.maybe_build_signal(
        candles=_candles([12, 11, 10, 9, 8, 7, 8, 10]),
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert signal is not None
    assert signal.side == "LONG"
    assert "EMA crossover" in reason


def test_auto_signal_short_trigger_by_ema_crossunder_only() -> None:
    engine = AutoSignalEngine(config=_cfg())
    signal, reason, _ = engine.maybe_build_signal(
        candles=_candles([8, 9, 10, 11, 12, 13, 12, 10]),
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert signal is not None
    assert signal.side == "SHORT"
    assert "EMA crossunder" in reason


def test_auto_signal_does_not_emit_for_unconfirmed_candle() -> None:
    engine = AutoSignalEngine(config=_cfg())
    candles = _candles([12, 11, 10, 9, 8, 7, 8, 10])
    candles[-1] = ClosedCandle(
        start_ms=candles[-1].start_ms,
        open_price=candles[-1].open_price,
        high_price=candles[-1].high_price,
        low_price=candles[-1].low_price,
        close_price=candles[-1].close_price,
        volume=candles[-1].volume,
        confirm=False,
    )

    signal, reason, _ = engine.maybe_build_signal(
        candles=candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert signal is None
    assert "não confirmado" in reason


def test_auto_signal_volume_filter_is_optional() -> None:
    closes = [
        101.173, 99.765, 100.846, 100.651, 102.581, 104.223, 104.602, 104.966,
        106.71, 104.992, 105.278, 106.932, 105.827, 104.011, 104.628, 104.127,
        102.558, 102.025, 102.182, 103.824, 102.723, 103.649,
    ]
    volumes = [1000] * 21 + [200]
    assert len(closes) >= 20

    base_candles = _candles(
        closes,
        volumes=volumes,
    )

    engine_with_filter = AutoSignalEngine(
        config=_cfg(use_volume_filter=True, volume_multiplier=1.05)
    )
    blocked_signal, blocked_reason, _ = engine_with_filter.maybe_build_signal(
        candles=base_candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    engine_without_filter = AutoSignalEngine(config=_cfg(use_volume_filter=False))
    allowed_signal, _, _ = engine_without_filter.maybe_build_signal(
        candles=base_candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert blocked_signal is None
    assert "gatilho" in blocked_reason
    assert allowed_signal is not None
    assert allowed_signal.side == "LONG"


def test_auto_signal_adx_filter_is_optional() -> None:
    closes = [
        101.173, 99.765, 100.846, 100.651, 102.581, 104.223, 104.602, 104.966,
        106.71, 104.992, 105.278, 106.932, 105.827, 104.011, 104.628, 104.127,
        102.558, 102.025, 102.182, 103.824, 102.723, 103.649,
    ]
    assert len(closes) >= 16
    candles = _candles(closes)

    engine_with_strict_adx = AutoSignalEngine(
        config=_cfg(use_adx_filter=True, adx_threshold=1000.0)
    )
    blocked_signal, blocked_reason, _ = engine_with_strict_adx.maybe_build_signal(
        candles=candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    engine_without_adx = AutoSignalEngine(config=_cfg(use_adx_filter=False))
    allowed_signal, _, _ = engine_without_adx.maybe_build_signal(
        candles=candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert blocked_signal is None
    assert "gatilho" in blocked_reason
    assert allowed_signal is not None


def test_auto_signal_blocks_reentry_on_same_candle() -> None:
    engine = AutoSignalEngine(config=_cfg())
    candles = _candles([12, 11, 10, 9, 8, 7, 8, 10])
    last_candle = candles[-1].start_ms

    signal, reason, _ = engine.maybe_build_signal(
        candles=candles,
        last_processed_candle=last_candle,
        cooldown_until_candle=None,
    )

    assert signal is None
    assert "já processado" in reason


def test_auto_signal_cooldown_blocks_exactly_next_candle_when_set_to_one() -> None:
    engine = AutoSignalEngine(config=_cfg(cooldown_candles=1))
    candles = _candles([12, 11, 10, 9, 8, 7, 8, 10])

    signal, _, cooldown_until = engine.maybe_build_signal(
        candles=candles,
        last_processed_candle=None,
        cooldown_until_candle=None,
    )

    assert signal is not None
    assert cooldown_until is not None

    next_candle = ClosedCandle(
        start_ms=candles[-1].start_ms + 3_600_000,
        open_price=10.0,
        high_price=10.3,
        low_price=9.8,
        close_price=10.2,
        volume=1000.0,
        confirm=True,
    )
    blocked_signal, blocked_reason, _ = engine.maybe_build_signal(
        candles=candles + [next_candle],
        last_processed_candle=None,
        cooldown_until_candle=cooldown_until,
    )

    candle_after_next = ClosedCandle(
        start_ms=next_candle.start_ms + 3_600_000,
        open_price=10.2,
        high_price=10.4,
        low_price=9.7,
        close_price=9.8,
        volume=1000.0,
        confirm=True,
    )
    released_signal, released_reason, _ = engine.maybe_build_signal(
        candles=candles + [next_candle, candle_after_next],
        last_processed_candle=None,
        cooldown_until_candle=cooldown_until,
    )

    assert blocked_signal is None
    assert "cooldown" in blocked_reason
    assert "cooldown" not in released_reason
    assert released_signal is None or released_signal.side in {"LONG", "SHORT"}
