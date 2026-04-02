from __future__ import annotations

import json
from datetime import datetime, timezone

from src.services.execution_journal import ExecutionJournalService


def test_write_persists_utf8_json_with_predictable_path(tmp_path) -> None:
    fixed_now = datetime(2026, 4, 2, 12, 30, 45, 123456, tzinfo=timezone.utc)
    service = ExecutionJournalService(
        base_dir=tmp_path / "runtime" / "journal",
        id_factory=lambda: "exec001",
        now_factory=lambda: fixed_now,
    )

    path = service.write(
        symbol="BTCUSDT",
        journal_payload={"status": "completed", "rawText": "BTC LONG"},
    )

    assert path.name == "20260402T123045123456Z_btcusdt_exec001.json"
    content = json.loads(path.read_text(encoding="utf-8"))
    assert content["executionId"] == "exec001"
    assert content["recordedAt"] == fixed_now.isoformat()
    assert content["status"] == "completed"


def test_write_generates_unique_names_without_collision(tmp_path) -> None:
    ids = iter(["exec-a", "exec-b"])
    fixed_now = datetime(2026, 4, 2, 12, 30, 45, tzinfo=timezone.utc)
    service = ExecutionJournalService(
        base_dir=tmp_path / "runtime" / "journal",
        id_factory=lambda: next(ids),
        now_factory=lambda: fixed_now,
    )

    first = service.write(symbol="BTCUSDT", journal_payload={"status": "completed"})
    second = service.write(symbol="BTCUSDT", journal_payload={"status": "completed"})

    assert first != second
    assert first.exists()
    assert second.exists()
