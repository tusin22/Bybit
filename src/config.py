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


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    return Settings(
        env=os.getenv("ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        dry_run=_parse_bool(os.getenv("DRY_RUN"), default=True),
    )
