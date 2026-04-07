from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_STATE_FILE = Path("runtime/state/auto_analysis_state.json")
DEFAULT_JOURNAL_DIR = Path("runtime/journal")


@dataclass(frozen=True, slots=True)
class JournalLoadResult:
    journals: list[dict[str, Any]]
    invalid_files: list[str]


@dataclass(frozen=True, slots=True)
class DailyMetrics:
    total_signals: int
    total_ignored_signals: int
    total_execution_attempts: int
    total_confirmed_executions: int
    total_blocked_or_rejected: int
    long_signals: int
    short_signals: int


def load_auto_analysis_state(state_file: Path = DEFAULT_STATE_FILE) -> dict[str, Any]:
    if not state_file.exists():
        return {}

    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Falha ao ler state JSON (%s): %s", state_file, exc)
        return {}

    if not isinstance(payload, dict):
        LOGGER.warning("State JSON inválido (esperado objeto): %s", state_file)
        return {}

    return payload


def load_journals_safe(journal_dir: Path = DEFAULT_JOURNAL_DIR, *, max_rows: int = 200) -> JournalLoadResult:
    if not journal_dir.exists():
        return JournalLoadResult(journals=[], invalid_files=[])

    journals: list[dict[str, Any]] = []
    invalid_files: list[str] = []

    for path in sorted(journal_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Ignorando journal inválido %s: %s", path.name, exc)
            invalid_files.append(path.name)
            continue

        if not isinstance(payload, dict):
            LOGGER.warning("Ignorando journal não-objeto: %s", path.name)
            invalid_files.append(path.name)
            continue

        payload["_file_name"] = path.name
        journals.append(payload)

        if len(journals) >= max_rows:
            break

    journals.sort(key=lambda item: (_coalesce_str(item.get("recordedAt")) or "", _coalesce_str(item.get("_file_name")) or ""), reverse=True)
    return JournalLoadResult(journals=journals, invalid_files=sorted(invalid_files))


def calculate_daily_metrics(journals: list[dict[str, Any]], *, target_day: date | None = None) -> DailyMetrics:
    day = target_day or datetime.now(timezone.utc).date()

    filtered = [item for item in journals if _journal_date(item) == day]
    side_counter = Counter(_extract_side(item) for item in filtered)

    total_execution_attempts = sum(1 for item in filtered if _execution_attempted(item))
    total_confirmed_executions = sum(1 for item in filtered if _trade_status(item) in {"closed_clean", "closed_with_failures", "monitoring_inconclusive", "protected", "entry_confirmed"})
    total_blocked_or_rejected = sum(1 for item in filtered if _trade_status(item) in {"blocked", "safe_failure"})

    return DailyMetrics(
        total_signals=len(filtered),
        total_ignored_signals=sum(1 for item in filtered if _trade_status(item) == "blocked"),
        total_execution_attempts=total_execution_attempts,
        total_confirmed_executions=total_confirmed_executions,
        total_blocked_or_rejected=total_blocked_or_rejected,
        long_signals=side_counter.get("LONG", 0),
        short_signals=side_counter.get("SHORT", 0),
    )


def _journal_date(payload: dict[str, Any]) -> date | None:
    raw = _coalesce_str(payload.get("recordedAt"))
    if raw is None:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    return parsed.astimezone(timezone.utc).date()


def _execution_attempted(payload: dict[str, Any]) -> bool:
    execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    return bool(result.get("order_attempted"))


def _trade_status(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    status = _coalesce_str(summary.get("tradeStatus"), payload.get("tradeStatus"))
    return status or "unknown"


def _extract_side(payload: dict[str, Any]) -> str | None:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
    side = _coalesce_str(summary.get("side"), signal.get("side"))
    return side.upper() if side else None


def _coalesce_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
