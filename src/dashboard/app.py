from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.dashboard.data import calculate_daily_metrics, load_auto_analysis_state, load_journals_safe

STATE_FILE = Path("runtime/state/auto_analysis_state.json")
JOURNAL_DIR = Path("runtime/journal")

STATE_FIELDS: list[tuple[str, str]] = [
    ("status geral do bot", "analyzerStatus"),
    ("symbol", "symbol"),
    ("interval", "interval"),
    ("último candle fechado", "lastClosedCandleTime"),
    ("lastPrice", "lastPrice"),
    ("analyzerStatus", "analyzerStatus"),
    ("lastSignalSide", "lastSignalSide"),
    ("lastSignalReason", "lastSignalReason"),
    ("lastExecutionAttempted", "lastExecutionAttempted"),
    ("lastExecutionTradeStatus", "lastExecutionTradeStatus"),
    ("openPositionDetected", "openPositionDetected"),
    ("updatedAt", "updatedAt"),
]


def main() -> None:
    st.set_page_config(page_title="Bybit Auto Analysis Dashboard", layout="wide")
    st.title("Bybit Auto Analysis - Observabilidade Local")
    st.caption("Somente leitura. Este dashboard não envia ordens e não altera configuração.")

    state = load_auto_analysis_state(STATE_FILE)
    journal_result = load_journals_safe(JOURNAL_DIR, max_rows=200)
    metrics = calculate_daily_metrics(journal_result.journals)

    st.subheader("Status geral")
    cols = st.columns(3)
    for idx, (label, key) in enumerate(STATE_FIELDS):
        value = state.get(key, "-") if state else "-"
        cols[idx % 3].metric(label=label, value=str(value))

    st.subheader("Métricas do dia (UTC)")
    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("Total sinais", metrics.total_signals)
    m2.metric("Sinais ignorados", metrics.total_ignored_signals)
    m3.metric("Tentativas execução", metrics.total_execution_attempts)
    m4.metric("Execuções confirmadas", metrics.total_confirmed_executions)
    m5.metric("Bloqueadas/recusadas", metrics.total_blocked_or_rejected)
    m6.metric("LONG", metrics.long_signals)
    m7.metric("SHORT", metrics.short_signals)

    st.subheader("Journals mais recentes")
    rows = []
    for item in journal_result.journals[:30]:
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        rows.append(
            {
                "recordedAt": item.get("recordedAt", "-"),
                "symbol": summary.get("symbol") or (item.get("signal") or {}).get("symbol", "-"),
                "side": summary.get("side") or (item.get("signal") or {}).get("side", "-"),
                "tradeStatus": summary.get("tradeStatus") or item.get("tradeStatus", "-"),
                "success": summary.get("success", False),
                "file": item.get("_file_name", "-"),
            }
        )

    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("Nenhum journal válido encontrado.")

    if journal_result.invalid_files:
        st.warning(f"Arquivos inválidos/corrompidos ignorados: {len(journal_result.invalid_files)}")
        st.caption(", ".join(journal_result.invalid_files[:15]))


if __name__ == "__main__":
    main()
