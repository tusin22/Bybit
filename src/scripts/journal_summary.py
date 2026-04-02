from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_JOURNAL_DIR = Path("runtime/journal")


@dataclass(frozen=True, slots=True)
class JournalRow:
    file_name: str
    recorded_at: str | None
    symbol: str | None
    side: str | None
    trade_status: str
    success: bool
    entry_order_id: str | None
    final_decision_source: str | None
    cleanup_status: str | None
    monitor_status: str | None


@dataclass(frozen=True, slots=True)
class JournalSummary:
    rows: list[JournalRow]
    invalid_files: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lista e resume journals locais de execução (visão operacional rápida; "
            "sem analytics avançada)."
        )
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_JOURNAL_DIR,
        help="Diretório com journals JSON (padrão: runtime/journal).",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=10,
        help="Quantidade de journals mais recentes na listagem curta (padrão: 10).",
    )
    return parser.parse_args()


def load_journals(journal_dir: Path) -> JournalSummary:
    if not journal_dir.exists():
        return JournalSummary(rows=[], invalid_files=[])

    rows: list[JournalRow] = []
    invalid_files: list[str] = []

    for path in sorted(journal_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            invalid_files.append(path.name)
            continue

        if not isinstance(payload, dict):
            invalid_files.append(path.name)
            continue

        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        execution_ids = execution.get("ids") if isinstance(execution.get("ids"), dict) else {}
        monitor = payload.get("monitor") if isinstance(payload.get("monitor"), dict) else {}
        cleanup = payload.get("cleanup") if isinstance(payload.get("cleanup"), dict) else {}

        trade_status = _coalesce_str(summary.get("tradeStatus"), payload.get("tradeStatus")) or "unknown"
        success = _coalesce_bool(summary.get("success"), False)

        rows.append(
            JournalRow(
                file_name=path.name,
                recorded_at=_coalesce_str(summary.get("recordedAt"), payload.get("recordedAt")),
                symbol=_coalesce_str(summary.get("symbol"), _dig(payload, "signal", "symbol")),
                side=_coalesce_str(summary.get("side"), _dig(payload, "signal", "side")),
                trade_status=trade_status,
                success=success,
                entry_order_id=_coalesce_str(
                    summary.get("entryOrderId"),
                    _dig(execution_ids, "entryOrderId"),
                ),
                final_decision_source=_coalesce_str(
                    summary.get("finalDecisionSource"),
                    monitor.get("finalDecisionSource"),
                ),
                cleanup_status=_coalesce_str(summary.get("cleanupStatus"), cleanup.get("status")),
                monitor_status=_coalesce_str(summary.get("monitorStatus"), monitor.get("status")),
            )
        )

    rows.sort(key=lambda row: (row.recorded_at or "", row.file_name), reverse=True)
    return JournalSummary(rows=rows, invalid_files=invalid_files)


def _dig(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _coalesce_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _coalesce_bool(*values: Any) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
    return False


def render_summary(result: JournalSummary, *, last_n: int, journal_dir: Path) -> str:
    if not journal_dir.exists():
        return f"Diretório de journals não encontrado: {journal_dir}"

    if not result.rows and not result.invalid_files:
        return f"Nenhum journal encontrado em: {journal_dir}"

    status_counter = Counter(row.trade_status for row in result.rows)
    success_counter = Counter(row.success for row in result.rows)

    monitoring_inconclusive = status_counter.get("monitoring_inconclusive", 0)
    closed_clean = status_counter.get("closed_clean", 0)
    closed_with_failures = status_counter.get("closed_with_failures", 0)
    blocked = status_counter.get("blocked", 0)
    safe_failure = status_counter.get("safe_failure", 0)

    lines = [
        f"Diretório: {journal_dir}",
        f"Total de journals válidos: {len(result.rows)}",
        f"Arquivos inválidos/corrompidos ignorados: {len(result.invalid_files)}",
        "",
        "Totais por tradeStatus:",
    ]

    if status_counter:
        for status, count in sorted(status_counter.items()):
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- (sem journals válidos)")

    lines.extend(
        [
            "",
            f"Success=true: {success_counter.get(True, 0)}",
            f"Success=false: {success_counter.get(False, 0)}",
            "",
            f"Monitor inconclusivo: {monitoring_inconclusive}",
            f"Fechamento limpo: {closed_clean}",
            f"Fechamento com falhas: {closed_with_failures}",
            f"Blocked: {blocked}",
            f"Safe failure: {safe_failure}",
            "",
            f"Últimos {max(last_n, 0)} journals:",
        ]
    )

    if not result.rows:
        lines.append("- (nenhum journal válido para listar)")
    else:
        for row in result.rows[: max(last_n, 0)]:
            lines.append(
                "- "
                f"recordedAt={row.recorded_at or '-'} "
                f"symbol={row.symbol or '-'} "
                f"side={row.side or '-'} "
                f"tradeStatus={row.trade_status} "
                f"success={str(row.success).lower()} "
                f"entryOrderId={row.entry_order_id or '-'} "
                f"finalDecisionSource={row.final_decision_source or '-'} "
                f"cleanupStatus={row.cleanup_status or '-'} "
                f"monitorStatus={row.monitor_status or '-'}"
            )

    if result.invalid_files:
        lines.extend(
            [
                "",
                "Arquivos inválidos/corrompidos:",
                *[f"- {name}" for name in sorted(result.invalid_files)],
            ]
        )

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.last < 0:
        raise SystemExit("--last deve ser >= 0")

    summary = load_journals(args.path)
    print(render_summary(summary, last_n=args.last, journal_dir=args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
