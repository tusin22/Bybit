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


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"Variável obrigatória ausente ou vazia: {name}")
    return value.strip()


def _parse_int_env(name: str) -> int:
    value = _require_env(name)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Variável {name} deve ser um inteiro válido.") from exc


def load_settings() -> Settings:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    return Settings(
        env=os.getenv("ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), default=True),
        telegram_api_id=_parse_int_env("TELEGRAM_API_ID"),
        telegram_api_hash=_require_env("TELEGRAM_API_HASH"),
        telegram_session_name=_require_env("TELEGRAM_SESSION_NAME"),
        telegram_source_chat=_require_env("TELEGRAM_SOURCE_CHAT"),
    )
