from __future__ import annotations

import json
from datetime import date, datetime, timezone

from src.dashboard.data import calculate_daily_metrics, load_auto_analysis_state, load_journals_safe


def test_load_auto_analysis_state_returns_empty_for_broken_json(tmp_path) -> None:
    state_file = tmp_path / "auto_analysis_state.json"
    state_file.write_text("{broken", encoding="utf-8")

    assert load_auto_analysis_state(state_file) == {}


def test_load_journals_safe_ignores_corrupted_and_orders_latest_first(tmp_path) -> None:
    (tmp_path / "broken.json").write_text("{oops", encoding="utf-8")
    (tmp_path / "old.json").write_text(json.dumps({"recordedAt": "2026-04-06T10:00:00+00:00"}), encoding="utf-8")
    (tmp_path / "new.json").write_text(json.dumps({"recordedAt": "2026-04-07T10:00:00+00:00"}), encoding="utf-8")

    result = load_journals_safe(tmp_path)

    assert [row["_file_name"] for row in result.journals] == ["new.json", "old.json"]
    assert result.invalid_files == ["broken.json"]


def test_calculate_daily_metrics_counts_expected_fields() -> None:
    today = date(2026, 4, 7)
    journals = [
        {
            "recordedAt": datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc).isoformat(),
            "summary": {"tradeStatus": "blocked", "side": "LONG"},
            "execution": {"result": {"order_attempted": False}},
            "signal": {"side": "LONG"},
        },
        {
            "recordedAt": datetime(2026, 4, 7, 10, 0, tzinfo=timezone.utc).isoformat(),
            "summary": {"tradeStatus": "closed_clean", "side": "SHORT"},
            "execution": {"result": {"order_attempted": True}},
            "signal": {"side": "SHORT"},
        },
        {
            "recordedAt": datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc).isoformat(),
            "summary": {"tradeStatus": "safe_failure", "side": "LONG"},
            "execution": {"result": {"order_attempted": True}},
            "signal": {"side": "LONG"},
        },
    ]

    result = calculate_daily_metrics(journals, target_day=today)

    assert result.total_signals == 2
    assert result.total_ignored_signals == 1
    assert result.total_execution_attempts == 1
    assert result.total_confirmed_executions == 1
    assert result.total_blocked_or_rejected == 1
    assert result.long_signals == 1
    assert result.short_signals == 1
