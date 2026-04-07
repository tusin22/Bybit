from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.dashboard.control_store import load_control_snapshot, save_runtime_config, set_desired_run_state
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
    st.title("Bybit Auto Analysis - Painel de Controle Local")
    st.caption("Controle/configuração local no dashboard. A análise de sinais e execução continuam no backend.")

    _render_control_panel()

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


def _render_control_panel() -> None:
    st.subheader("Controle local do bot")

    snapshot = load_control_snapshot()
    control_state = snapshot.control_state
    runtime_config = snapshot.runtime_config

    desired_state = control_state.get("desiredRunState", "stopped")
    changed_at = control_state.get("changedAt", "-")

    c1, c2, c3 = st.columns([1, 1, 2])
    if c1.button("▶️ Play", use_container_width=True):
        control_state = set_desired_run_state("running")
        st.success("Estado desejado atualizado para running.")

    if c2.button("⏹️ Stop", use_container_width=True):
        control_state = set_desired_run_state("stopped")
        st.success("Estado desejado atualizado para stopped.")

    desired_state = control_state.get("desiredRunState", desired_state)
    changed_at = control_state.get("changedAt", changed_at)
    c3.metric("Estado desejado", desired_state)
    st.caption(f"Última mudança: {changed_at} (dashboard)")

    with st.form("runtime-config-form"):
        st.markdown("#### Configuração básica (persistida localmente)")
        signal_source = st.selectbox("signalSource", options=["telegram", "auto_analysis"], index=0 if runtime_config.get("signalSource") == "telegram" else 1)
        auto_analysis_enabled = st.checkbox("autoAnalysisEnabled", value=bool(runtime_config.get("autoAnalysisEnabled", True)))
        symbol = st.text_input("symbol", value=str(runtime_config.get("symbol", "BTCUSDT")))
        interval = st.text_input("interval", value=str(runtime_config.get("interval", "60")))
        ema_short = st.number_input("emaShort", min_value=1, step=1, value=int(runtime_config.get("emaShort", 9)))
        ema_mid = st.number_input("emaMid", min_value=1, step=1, value=int(runtime_config.get("emaMid", 21)))
        use_volume_filter = st.checkbox("useVolumeFilter", value=bool(runtime_config.get("useVolumeFilter", True)))
        volume_multiplier = st.number_input("volumeMultiplier", min_value=0.0, step=0.1, value=float(runtime_config.get("volumeMultiplier", 1.2)))
        use_adx_filter = st.checkbox("useAdxFilter", value=bool(runtime_config.get("useAdxFilter", True)))
        adx_len = st.number_input("adxLen", min_value=1, step=1, value=int(runtime_config.get("adxLen", 14)))
        adx_threshold = st.number_input("adxThreshold", min_value=0.0, step=0.5, value=float(runtime_config.get("adxThreshold", 20.0)))
        cooldown_candles = st.number_input("cooldownCandles", min_value=0, step=1, value=int(runtime_config.get("cooldownCandles", 3)))
        dry_run = st.checkbox("dryRun", value=bool(runtime_config.get("dryRun", True)))
        enable_order_execution = st.checkbox("enableOrderExecution", value=bool(runtime_config.get("enableOrderExecution", False)))

        submitted = st.form_submit_button("Salvar configuração")

    if submitted:
        saved = save_runtime_config(
            {
                "signalSource": signal_source,
                "autoAnalysisEnabled": auto_analysis_enabled,
                "symbol": symbol,
                "interval": interval,
                "emaShort": int(ema_short),
                "emaMid": int(ema_mid),
                "useVolumeFilter": use_volume_filter,
                "volumeMultiplier": float(volume_multiplier),
                "useAdxFilter": use_adx_filter,
                "adxLen": int(adx_len),
                "adxThreshold": float(adx_threshold),
                "cooldownCandles": int(cooldown_candles),
                "dryRun": dry_run,
                "enableOrderExecution": enable_order_execution,
            }
        )
        st.success("Configuração básica salva em runtime/control/runtime_config.json")
        _show_saved_config(saved)


def _show_saved_config(saved: dict[str, Any]) -> None:
    st.caption(f"updatedAt: {saved.get('updatedAt', '-')}")


if __name__ == "__main__":
    main()
