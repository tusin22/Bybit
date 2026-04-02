from __future__ import annotations

import json
from pathlib import Path

from src.scripts.journal_summary import load_journals, render_summary


def _write_journal(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_render_summary_for_empty_directory(tmp_path) -> None:
    result = load_journals(tmp_path)

    rendered = render_summary(result, last_n=5, journal_dir=tmp_path)

    assert rendered == f"Nenhum journal encontrado em: {tmp_path}"


def test_render_summary_counts_by_trade_status_and_success(tmp_path) -> None:
    _write_journal(
        tmp_path / "20260402T100000000000Z_btc_a.json",
        {
            "recordedAt": "2026-04-02T10:00:00+00:00",
            "tradeStatus": "closed_clean",
            "summary": {
                "tradeStatus": "closed_clean",
                "success": True,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entryOrderId": "e1",
                "finalDecisionSource": "websocket_position",
                "cleanupStatus": "cancelled_all",
                "monitorStatus": "started_position_closed_cleanup_done",
            },
        },
    )
    _write_journal(
        tmp_path / "20260402T110000000000Z_eth_b.json",
        {
            "recordedAt": "2026-04-02T11:00:00+00:00",
            "tradeStatus": "monitoring_inconclusive",
            "summary": {
                "tradeStatus": "monitoring_inconclusive",
                "success": False,
                "symbol": "ETHUSDT",
                "side": "SELL",
                "entryOrderId": "e2",
                "finalDecisionSource": "rest_fallback",
                "cleanupStatus": "position_not_closed_in_window",
                "monitorStatus": "started_window_expired",
            },
        },
    )
    _write_journal(
        tmp_path / "20260402T120000000000Z_sol_c.json",
        {
            "recordedAt": "2026-04-02T12:00:00+00:00",
            "tradeStatus": "safe_failure",
            "summary": {
                "tradeStatus": "safe_failure",
                "success": False,
                "symbol": "SOLUSDT",
                "side": "BUY",
                "entryOrderId": None,
                "finalDecisionSource": None,
                "cleanupStatus": "not_attempted",
                "monitorStatus": "not_started",
            },
        },
    )

    result = load_journals(tmp_path)
    rendered = render_summary(result, last_n=2, journal_dir=tmp_path)

    assert "Total de journals válidos: 3" in rendered
    assert "- closed_clean: 1" in rendered
    assert "- monitoring_inconclusive: 1" in rendered
    assert "- safe_failure: 1" in rendered
    assert "Success=true: 1" in rendered
    assert "Success=false: 2" in rendered
    assert "Monitor inconclusivo: 1" in rendered
    assert "Fechamento limpo: 1" in rendered
    assert "Fechamento com falhas: 0" in rendered
    assert "Blocked: 0" in rendered
    assert "Safe failure: 1" in rendered


def test_load_journals_orders_latest_first(tmp_path) -> None:
    _write_journal(
        tmp_path / "old.json",
        {
            "recordedAt": "2026-04-01T08:00:00+00:00",
            "summary": {"tradeStatus": "blocked", "success": False, "symbol": "BTCUSDT", "side": "BUY"},
        },
    )
    _write_journal(
        tmp_path / "new.json",
        {
            "recordedAt": "2026-04-02T08:00:00+00:00",
            "summary": {"tradeStatus": "closed_clean", "success": True, "symbol": "ETHUSDT", "side": "SELL"},
        },
    )

    result = load_journals(tmp_path)

    assert [row.file_name for row in result.rows] == ["new.json", "old.json"]


def test_invalid_json_file_does_not_break_summary(tmp_path) -> None:
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    _write_journal(
        tmp_path / "valid.json",
        {
            "recordedAt": "2026-04-02T12:00:00+00:00",
            "summary": {
                "tradeStatus": "blocked",
                "success": False,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entryOrderId": "e1",
                "finalDecisionSource": "guard",
                "cleanupStatus": "not_attempted",
                "monitorStatus": "not_started",
            },
        },
    )

    result = load_journals(tmp_path)
    rendered = render_summary(result, last_n=5, journal_dir=tmp_path)

    assert len(result.rows) == 1
    assert result.invalid_files == ["broken.json"]
    assert "Arquivos inválidos/corrompidos ignorados: 1" in rendered
    assert "- broken.json" in rendered


def test_load_journals_reads_current_schema_fields(tmp_path) -> None:
    _write_journal(
        tmp_path / "schema.json",
        {
            "recordedAt": "2026-04-02T09:00:00+00:00",
            "signal": {"symbol": "BTCUSDT", "side": "BUY"},
            "execution": {"ids": {"entryOrderId": "abc-123"}},
            "monitor": {"status": "started_window_expired", "finalDecisionSource": "rest_fallback"},
            "cleanup": {"status": "position_not_closed_in_window"},
            "summary": {
                "tradeStatus": "monitoring_inconclusive",
                "success": False,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "entryOrderId": "abc-123",
                "finalDecisionSource": "rest_fallback",
                "cleanupStatus": "position_not_closed_in_window",
                "monitorStatus": "started_window_expired",
            },
        },
    )

    result = load_journals(tmp_path)
    row = result.rows[0]

    assert row.recorded_at == "2026-04-02T09:00:00+00:00"
    assert row.symbol == "BTCUSDT"
    assert row.side == "BUY"
    assert row.trade_status == "monitoring_inconclusive"
    assert row.success is False
    assert row.entry_order_id == "abc-123"
    assert row.final_decision_source == "rest_fallback"
    assert row.cleanup_status == "position_not_closed_in_window"
    assert row.monitor_status == "started_window_expired"
