from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.bybit import (
    BybitClientError,
    BybitExecutionClient,
    BybitExecutionClientError,
    BybitReadOnlyClient,
)
from src.bybit.private_execution_ws import BybitPrivateExecutionWsMonitor
from src.config import load_settings, validate_settings_for_signal_source
from src.models.execution_plan import ExecutionPlan
from src.models.execution_result import ExecutionResult, TradeStatus
from src.models.signal import Signal
from src.parsing.vectra_parser import VectraSignalParser
from src.services.execution_journal import ExecutionJournalService
from src.services.execution_planner import ExecutionPlanner
from src.services.signal_router import SignalRouter
from src.services.trade_executor import TradeExecutionError, TradeExecutor
from src.telegram.listener import TelegramSignalListener
from src.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


class RoutedSignalParser:
    """Adapter que parseia, valida, planeja e executa entrada conforme flags de proteção."""

    def __init__(
        self,
        router: SignalRouter,
        planner: ExecutionPlanner,
        executor: TradeExecutor,
        journal_service: ExecutionJournalService | None = None,
    ) -> None:
        self._parser = VectraSignalParser()
        self._router = router
        self._planner = planner
        self._executor = executor
        self._journal_service = journal_service

    def parse(self, raw_text: str):
        signal: Signal | None = None
        plan: ExecutionPlan | None = None
        result: ExecutionResult | None = None
        safe_failure_reason: str | None = None
        journal_status = "unknown"
        flow_errors: list[dict[str, str]] = []

        try:
            signal = self._parser.parse(raw_text)
            enriched_signal = self._router.enrich_with_bybit_validation(signal)
            plan = self._planner.build_plan(signal=enriched_signal)
            result = self._executor.execute_entry(plan=plan)
            journal_status = "completed"

            if result.order_sent:
                LOGGER.info(
                    "Fluxo de execução concluído após confirmação pós-ACK. entry_status=%s reason=%s stop_loss_status=%s stop_loss_reason=%s take_profit_status=%s tp_attempted=%s tp_accepted=%s tp_failed=%s registered_tps=%s cleanup_status=%s cleanup_attempted=%s cleanup_position_closed_within_window=%s cleanup_remaining_registered_tps=%s cleanup_missing_registered_tps=%s cleanup_cancelled=%s cleanup_failed=%s monitor_started=%s monitor_ws_started=%s monitor_ws_connected=%s monitor_ws_authenticated=%s monitor_ws_subscribed=%s monitor_ws_execution_subscribed=%s monitor_ws_execution_events=%s monitor_ws_execution_partial_or_total_fill=%s monitor_rest_fallback_used=%s monitor_attempts=%s monitor_position_closed=%s monitor_cleanup_completed=%s monitor_status=%s monitor_decision_source=%s monitor_decision_reason=%s monitor_remaining_orders=%s",
                    result.entry_status,
                    result.confirmation_reason,
                    result.stop_loss_status,
                    result.stop_loss_reason,
                    result.take_profit_status,
                    result.take_profit_attempted_count,
                    result.take_profit_accepted_count,
                    result.take_profit_failed_count,
                    len(result.registered_take_profit_orders),
                    result.cleanup_status,
                    result.cleanup_attempted,
                    result.cleanup_position_closed_within_window,
                    result.cleanup_remaining_registered_tp_count,
                    result.cleanup_missing_registered_tp_count,
                    result.cleanup_cancelled_count,
                    result.cleanup_failed_count,
                    result.monitor_started,
                    result.monitor_websocket_started,
                    result.monitor_websocket_connected,
                    result.monitor_websocket_authenticated,
                    result.monitor_websocket_subscribed,
                    result.monitor_websocket_execution_stream_subscribed,
                    result.monitor_websocket_execution_events_relevant_count,
                    result.monitor_websocket_execution_fill_summary.get("hasPartialOrTotalFill", False),
                    result.monitor_rest_fallback_used,
                    result.monitor_attempts,
                    result.monitor_position_closed_within_window,
                    result.monitor_cleanup_completed_within_window,
                    result.monitor_status,
                    result.monitor_final_decision_source,
                    result.monitor_final_decision_reason,
                    len(result.monitor_remaining_execution_orders),
                )
            else:
                LOGGER.info("Tentativa de execução não enviada: %s", result.blocked_reason)
            return result
        except BybitClientError as exc:
            journal_status = "safe_failure"
            safe_failure_reason = f"Falha ao validar sinal na Bybit (read-only): {exc}"
            flow_errors.append({"stage": "validation", "type": type(exc).__name__, "message": str(exc)})
            if signal is None:
                raise
            signal.entry_eligible = False
            signal.entry_validation_reason = safe_failure_reason
            return signal
        except (BybitExecutionClientError, TradeExecutionError) as exc:
            journal_status = "safe_failure"
            safe_failure_reason = f"Falha ao executar ordem na Bybit; callback mantido ativo: {exc}"
            flow_errors.append({"stage": "execution", "type": type(exc).__name__, "message": str(exc)})
            if signal is None or plan is None:
                raise
            LOGGER.error(
                "Falha segura ao executar ordem de entrada: %s | symbol=%s category=%s planned_quantity=%s instrument_qty_step=%s",
                exc,
                plan.symbol,
                plan.category,
                plan.planned_quantity,
                plan.qty_step,
            )
            signal.entry_eligible = False
            signal.entry_validation_reason = safe_failure_reason
            return signal
        finally:
            self._try_write_journal(
                raw_text=raw_text,
                signal=signal,
                plan=plan,
                result=result,
                journal_status=journal_status,
                safe_failure_reason=safe_failure_reason,
                flow_errors=flow_errors,
            )

    def _try_write_journal(
        self,
        *,
        raw_text: str,
        signal: Signal | None,
        plan: ExecutionPlan | None,
        result: ExecutionResult | None,
        journal_status: str,
        safe_failure_reason: str | None,
        flow_errors: list[dict[str, str]],
    ) -> None:
        if self._journal_service is None:
            return

        symbol = signal.symbol if signal is not None else "unknown"
        summary = _build_journal_summary(
            signal=signal,
            result=result,
            journal_status=journal_status,
            safe_failure_reason=safe_failure_reason,
        )
        payload: dict[str, object] = {
            "journalVersion": 2,
            "recordedAt": _utc_now_iso(),
            "status": journal_status,
            "tradeStatus": summary["tradeStatus"],
            "rawText": raw_text,
            "signal": signal.to_dict() if signal is not None else None,
            "plan": plan.to_dict() if plan is not None else None,
            "execution": {
                "result": result.to_dict() if result is not None else None,
                "ids": {
                    "entryOrderId": summary["entryOrderId"],
                    "entryOrderLinkId": summary["entryOrderLinkId"],
                    "registeredTakeProfits": (
                        result.registered_take_profit_orders if result is not None else []
                    ),
                },
            },
            "monitor": {
                "status": summary["monitorStatus"],
                "websocketStarted": result.monitor_websocket_started if result is not None else False,
                "restFallbackUsed": result.monitor_rest_fallback_used if result is not None else False,
                "finalDecisionSource": summary["finalDecisionSource"],
                "finalDecisionReason": (
                    result.monitor_final_decision_reason if result is not None else safe_failure_reason
                ),
            },
            "cleanup": {
                "attempted": result.cleanup_attempted if result is not None else False,
                "status": summary["cleanupStatus"],
                "remainingRegisteredTpCount": (
                    result.cleanup_remaining_registered_tp_count if result is not None else 0
                ),
            },
            "errors": flow_errors,
            "summary": summary,
        }

        try:
            journal_path = self._journal_service.write(symbol=symbol, journal_payload=payload)
            LOGGER.info(
                "Journal de execução gravado. path=%s trade_status=%s",
                journal_path,
                summary["tradeStatus"],
            )
        except Exception as exc:  # pragma: no cover - proteção defensiva de callback
            LOGGER.error("Falha ao gravar journal de execução. reason=%s", exc)


def _build_journal_summary(
    *,
    signal: Signal | None,
    result: ExecutionResult | None,
    journal_status: str,
    safe_failure_reason: str | None,
) -> dict[str, object]:
    trade_status = _resolve_trade_status(
        result=result,
        journal_status=journal_status,
        signal=signal,
    )

    if result is not None:
        success_or_failure_reason = (
            result.bybit_response_summary.get("successReason")
            or result.monitor_final_decision_reason
            or result.confirmation_reason
            or result.stop_loss_reason
            or result.blocked_reason
            or "Sem motivo detalhado informado pelo executor."
        )
    else:
        success_or_failure_reason = safe_failure_reason or "Fluxo encerrado sem ExecutionResult."

    return {
        "tradeStatus": trade_status,
        "success": result.success if result is not None else False,
        "successOrFailureReason": success_or_failure_reason,
        "symbol": signal.symbol if signal is not None else None,
        "side": signal.side if signal is not None else None,
        "entryOrderId": result.bybit_response_summary.get("orderId") if result is not None else None,
        "entryOrderLinkId": (
            result.bybit_response_summary.get("orderLinkId") if result is not None else None
        ),
        "finalDecisionSource": (
            result.monitor_final_decision_source if result is not None else None
        ),
        "cleanupStatus": result.cleanup_status if result is not None else "not_attempted",
        "monitorStatus": result.monitor_status if result is not None else "not_started",
    }


def _resolve_trade_status(
    *,
    result: ExecutionResult | None,
    journal_status: str,
    signal: Signal | None,
) -> TradeStatus:
    """Normaliza o resultado final em um conjunto pequeno e previsível para auditoria.

    Regras conservadoras:
    - `safe_failure`: exceções tratadas no fluxo principal (validação/execução), sem ExecutionResult final.
    - `blocked`: execução sem envio de ordem (`order_sent=False`) ou sinal explicitamente não elegível sem resultado.
    - `entry_sent`: ACK de envio sem confirmação final.
    - `entry_confirmed`: confirmação de entrada sem proteção completa/fechamento.
    - `protected`: entrada confirmada com SL configurado e TPs integralmente aceitos.
    - `monitoring_inconclusive`: monitor iniciado, mas sem conclusão confiável da janela.
    - `closed_clean`: posição fechada na janela e cleanup concluído sem falhas.
    - `closed_with_failures`: posição fechada, porém cleanup parcial/falho ou sucesso global falso.
    """
    if journal_status == "safe_failure":
        return "safe_failure"

    if result is None:
        if signal is not None and signal.entry_eligible is False:
            return "blocked"
        return "safe_failure"

    if not result.order_sent:
        return "blocked"

    if result.order_sent and not result.order_confirmed:
        return "entry_sent"

    if result.monitor_position_closed_within_window:
        if result.success and result.cleanup_status in {"cancelled_all", "not_needed"}:
            return "closed_clean"
        return "closed_with_failures"

    if result.monitor_started and result.monitor_status in {
        "started_window_expired",
        "started_failed_with_safe_fallback",
    }:
        return "monitoring_inconclusive"

    if result.stop_loss_status == "configured" and result.take_profit_status == "all_configured":
        return "protected"

    return "entry_confirmed"


async def _run() -> int:
    settings = load_settings()
    validate_settings_for_signal_source(settings)
    configure_logging(settings.log_level)

    bybit_read_client = BybitReadOnlyClient(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        testnet=settings.bybit_testnet,
    )
    bybit_exec_client = BybitExecutionClient(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        testnet=settings.bybit_testnet,
    )

    if settings.dry_run:
        LOGGER.info("Proteção ativa: DRY_RUN=true. Ordem de entrada não será enviada.")
    if not settings.enable_order_execution:
        LOGGER.info(
            "Proteção ativa: ENABLE_ORDER_EXECUTION=false. Execução permanecerá bloqueada."
        )
    if settings.enable_order_execution and not settings.bybit_testnet:
        LOGGER.warning(
            "Proteção ativa: ENABLE_ORDER_EXECUTION=true com BYBIT_TESTNET=false. "
            "Envio será bloqueado nesta fase."
        )

    signal_router = SignalRouter(bybit_client=bybit_read_client)
    execution_planner = ExecutionPlanner(settings=settings)
    ws_monitor = BybitPrivateExecutionWsMonitor(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        testnet=settings.bybit_testnet,
    )
    trade_executor = TradeExecutor(
        settings=settings,
        execution_client=bybit_exec_client,
        private_ws_monitor=ws_monitor,
    )
    journal_service = ExecutionJournalService(base_dir=Path("runtime/journal"))

    listener = TelegramSignalListener(
        settings=settings,
        parser_factory=lambda: RoutedSignalParser(
            router=signal_router,
            planner=execution_planner,
            executor=trade_executor,
            journal_service=journal_service,
        ),
    )
    await listener.run()
    return 0


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        configure_logging("INFO")
        LOGGER.info("Encerrando bot...")
        return 0
    except ValueError as exc:
        configure_logging("INFO")
        LOGGER.error(
            "Erro de configuração: %s. Verifique as variáveis obrigatórias no .env.",
            exc,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
