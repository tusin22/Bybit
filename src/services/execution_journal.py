from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class ExecutionJournalError(RuntimeError):
    """Erro explícito ao persistir journal local de execução."""


class ExecutionJournalService:
    """Persistência local de journal estruturado por execução."""

    def __init__(
        self,
        *,
        base_dir: Path,
        id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))

    def write(
        self,
        *,
        symbol: str,
        journal_payload: dict[str, object],
    ) -> Path:
        self._base_dir.mkdir(parents=True, exist_ok=True)

        execution_id = self._id_factory()
        timestamp = self._now_factory().astimezone(timezone.utc)
        timestamp_key = timestamp.strftime("%Y%m%dT%H%M%S%fZ")
        safe_symbol = _sanitize_for_filename(symbol)
        file_name = f"{timestamp_key}_{safe_symbol}_{execution_id}.json"
        file_path = self._base_dir / file_name

        payload = {
            "executionId": execution_id,
            "recordedAt": timestamp.isoformat(),
            **journal_payload,
        }

        try:
            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise ExecutionJournalError(f"Falha ao persistir journal: {exc}") from exc

        return file_path


def _sanitize_for_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    if not cleaned:
        return "unknown"
    return cleaned.lower()
