from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    env: str
    log_level: str
    dry_run: bool
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str
    telegram_source_chat: str
    bybit_api_key: str
    bybit_api_secret: str
    bybit_testnet: bool
    enable_order_execution: bool
    execution_sizing_mode: str
    execution_fixed_notional_usdt: float
    execution_fixed_qty: float
    tp1_percent: float
    tp2_percent: float
    tp3_percent: float
    tp4_percent: float
    signal_source: str = "telegram"
    leverage: int = 10
    auto_analysis_enabled: bool = False
    auto_analysis_symbol: str = "BTCUSDT"
    auto_analysis_interval: str = "60"
    auto_analysis_ema_short: int = 5
    auto_analysis_ema_mid: int = 35
    auto_analysis_rsi_len: int = 14
    auto_analysis_macd_fast: int = 12
    auto_analysis_macd_slow: int = 26
    auto_analysis_macd_signal: int = 9
    auto_analysis_use_volume_filter: bool = False
    auto_analysis_volume_multiplier: float = 1.05
    auto_analysis_adx_len: int = 14
    auto_analysis_use_adx_filter: bool = False
    auto_analysis_adx_threshold: float = 14.0
    auto_analysis_single_position_only: bool = True
    auto_analysis_cooldown_candles: int = 1



def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Variável obrigatória ausente ou vazia: {name}")
    return value.strip()



def _parse_float_env(name: str, *, default: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ValueError(f"Variável obrigatória ausente ou vazia: {name}")
        return default

    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Variável {name} deve ser um número válido.") from exc



def _parse_int_env(name: str, *, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            value = _require_env(name)
        else:
            return default
    else:
        value = raw.strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Variável {name} deve ser um inteiro válido.") from exc



def _validate_tp_distribution(tp_percents: tuple[float, float, float, float]) -> None:
    if any(percent < 0 for percent in tp_percents):
        raise ValueError("Configuração inválida de TPs: percentuais não podem ser negativos.")

    total = sum(tp_percents)
    if abs(total - 100.0) > 1e-9:
        raise ValueError(
            "Configuração inválida de TPs: a soma de TP1_PERCENT, TP2_PERCENT, "
            f"TP3_PERCENT e TP4_PERCENT deve ser 100. Recebido: {total}."
        )



def load_settings() -> Settings:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    tp1_percent = _parse_float_env("TP1_PERCENT", default=50.0)
    tp2_percent = _parse_float_env("TP2_PERCENT", default=20.0)
    tp3_percent = _parse_float_env("TP3_PERCENT", default=20.0)
    tp4_percent = _parse_float_env("TP4_PERCENT", default=10.0)
    _validate_tp_distribution((tp1_percent, tp2_percent, tp3_percent, tp4_percent))

    return Settings(
        env=os.getenv("ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), default=True),
        telegram_api_id=_parse_int_env("TELEGRAM_API_ID", default=0),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip(),
        telegram_session_name=os.getenv("TELEGRAM_SESSION_NAME", "").strip(),
        telegram_source_chat=os.getenv("TELEGRAM_SOURCE_CHAT", "").strip(),
        bybit_api_key=os.getenv("BYBIT_API_KEY", "").strip(),
        bybit_api_secret=os.getenv("BYBIT_API_SECRET", "").strip(),
        bybit_testnet=_parse_bool(os.getenv("BYBIT_TESTNET"), default=True),
        enable_order_execution=_parse_bool(
            os.getenv("ENABLE_ORDER_EXECUTION"),
            default=False,
        ),
        execution_sizing_mode=os.getenv(
            "EXECUTION_SIZING_MODE",
            "fixed_notional_usdt",
        ).strip(),
        execution_fixed_notional_usdt=_parse_float_env(
            "EXECUTION_FIXED_NOTIONAL_USDT",
            default=25.0,
        ),
        execution_fixed_qty=_parse_float_env("EXECUTION_FIXED_QTY", default=0.0),
        tp1_percent=tp1_percent,
        tp2_percent=tp2_percent,
        tp3_percent=tp3_percent,
        tp4_percent=tp4_percent,
        signal_source=os.getenv("SIGNAL_SOURCE", "telegram").strip().lower(),
        leverage=_parse_int_env("LEVERAGE", default=10),
        auto_analysis_enabled=_parse_bool(os.getenv("AUTO_ANALYSIS_ENABLED"), default=False),
        auto_analysis_symbol=os.getenv("AUTO_ANALYSIS_SYMBOL", "BTCUSDT").strip().upper(),
        auto_analysis_interval=os.getenv("AUTO_ANALYSIS_INTERVAL", "60").strip(),
        auto_analysis_ema_short=_parse_int_env("AUTO_ANALYSIS_EMA_SHORT", default=5),
        auto_analysis_ema_mid=_parse_int_env("AUTO_ANALYSIS_EMA_MID", default=35),
        auto_analysis_rsi_len=_parse_int_env("AUTO_ANALYSIS_RSI_LEN", default=14),
        auto_analysis_macd_fast=_parse_int_env("AUTO_ANALYSIS_MACD_FAST", default=12),
        auto_analysis_macd_slow=_parse_int_env("AUTO_ANALYSIS_MACD_SLOW", default=26),
        auto_analysis_macd_signal=_parse_int_env("AUTO_ANALYSIS_MACD_SIGNAL", default=9),
        auto_analysis_use_volume_filter=_parse_bool(os.getenv("AUTO_ANALYSIS_USE_VOLUME_FILTER"), default=False),
        auto_analysis_volume_multiplier=_parse_float_env("AUTO_ANALYSIS_VOLUME_MULTIPLIER", default=1.05),
        auto_analysis_adx_len=_parse_int_env("AUTO_ANALYSIS_ADX_LEN", default=14),
        auto_analysis_use_adx_filter=_parse_bool(os.getenv("AUTO_ANALYSIS_USE_ADX_FILTER"), default=False),
        auto_analysis_adx_threshold=_parse_float_env("AUTO_ANALYSIS_ADX_THRESHOLD", default=14.0),
        auto_analysis_single_position_only=_parse_bool(os.getenv("AUTO_ANALYSIS_SINGLE_POSITION_ONLY"), default=True),
        auto_analysis_cooldown_candles=_parse_int_env("AUTO_ANALYSIS_COOLDOWN_CANDLES", default=1),
    )



def validate_settings_for_signal_source(settings: Settings) -> None:
    if settings.signal_source not in {"telegram", "auto_analysis"}:
        raise ValueError("SIGNAL_SOURCE inválido: use telegram ou auto_analysis.")

    if settings.signal_source == "telegram":
        if settings.telegram_api_id <= 0:
            raise ValueError("TELEGRAM_API_ID obrigatório para SIGNAL_SOURCE=telegram.")
        if not settings.telegram_api_hash:
            raise ValueError("TELEGRAM_API_HASH obrigatório para SIGNAL_SOURCE=telegram.")
        if not settings.telegram_session_name:
            raise ValueError("TELEGRAM_SESSION_NAME obrigatório para SIGNAL_SOURCE=telegram.")
        if not settings.telegram_source_chat:
            raise ValueError("TELEGRAM_SOURCE_CHAT obrigatório para SIGNAL_SOURCE=telegram.")

    if settings.signal_source == "auto_analysis":
        if not settings.auto_analysis_enabled:
            raise ValueError("AUTO_ANALYSIS_ENABLED precisa estar true para SIGNAL_SOURCE=auto_analysis.")
        if settings.auto_analysis_symbol != "BTCUSDT":
            raise ValueError("AUTO_ANALYSIS_SYMBOL fixo nesta fase: BTCUSDT.")
