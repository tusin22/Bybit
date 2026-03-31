from __future__ import annotations

import asyncio
import logging

from src.bybit import (
    BybitClientError,
    BybitExecutionClient,
    BybitExecutionClientError,
    BybitReadOnlyClient,
)
from src.config import load_settings
from src.parsing.vectra_parser import VectraSignalParser
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
    ) -> None:
        self._parser = VectraSignalParser()
        self._router = router
        self._planner = planner
        self._executor = executor

    def parse(self, raw_text: str):
        signal = self._parser.parse(raw_text)

        try:
            enriched_signal = self._router.enrich_with_bybit_validation(signal)
            plan = self._planner.build_plan(signal=enriched_signal)
            result = self._executor.execute_entry(plan=plan)
            if result.order_sent:
                LOGGER.info(
                    "Fluxo de execução concluído após confirmação pós-ACK. status=%s reason=%s",
                    result.confirmation_status,
                    result.confirmation_reason,
                )
            else:
                LOGGER.info("Tentativa de execução não enviada: %s", result.blocked_reason)
            return result
        except BybitClientError as exc:
            signal.entry_eligible = False
            signal.entry_validation_reason = (
                "Falha ao validar sinal na Bybit (read-only): " f"{exc}"
            )
            return signal
        except (BybitExecutionClientError, TradeExecutionError) as exc:
            LOGGER.error(
                "Falha segura ao executar ordem de entrada: %s | symbol=%s category=%s planned_quantity=%s instrument_qty_step=%s",
                exc,
                plan.symbol,
                plan.category,
                plan.planned_quantity,
                plan.qty_step,
            )
            signal.entry_eligible = False
            signal.entry_validation_reason = (
                "Falha ao executar ordem na Bybit; callback mantido ativo: " f"{exc}"
            )
            return signal


async def _run() -> int:
    settings = load_settings()
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
    trade_executor = TradeExecutor(settings=settings, execution_client=bybit_exec_client)

    listener = TelegramSignalListener(
        settings=settings,
        parser_factory=lambda: RoutedSignalParser(
            router=signal_router,
            planner=execution_planner,
            executor=trade_executor,
        ),
    )
    await listener.run()
    return 0


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
