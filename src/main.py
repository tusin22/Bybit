from __future__ import annotations

import asyncio
import logging

from src.bybit import BybitClientError, BybitReadOnlyClient
from src.config import load_settings
from src.parsing.vectra_parser import VectraSignalParser
from src.services.signal_router import SignalRouter
from src.telegram.listener import TelegramSignalListener
from src.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


class RoutedSignalParser:
    """Adapter para manter listener existente e validar sinal via Bybit read-only."""

    def __init__(self, router: SignalRouter) -> None:
        self._parser = VectraSignalParser()
        self._router = router

    def parse(self, raw_text: str):
        signal = self._parser.parse(raw_text)

        try:
            return self._router.enrich_with_bybit_validation(signal)
        except BybitClientError as exc:
            signal.entry_eligible = False
            signal.entry_validation_reason = (
                "Falha ao validar sinal na Bybit (read-only): " f"{exc}"
            )
            return signal


async def _run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)

    if not settings.dry_run:
        LOGGER.warning(
            "DRY_RUN está desativado, mas esta fase ainda opera sem integração de ordens."
        )

    bybit_client = BybitReadOnlyClient(
        api_key=settings.bybit_api_key,
        api_secret=settings.bybit_api_secret,
        testnet=settings.bybit_testnet,
    )
    signal_router = SignalRouter(bybit_client=bybit_client)

    listener = TelegramSignalListener(
        settings=settings,
        parser_factory=lambda: RoutedSignalParser(router=signal_router),
    )
    await listener.run()
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except ValueError as exc:
        configure_logging("INFO")
        LOGGER.error(
            "Erro de configuração: %s. Verifique as variáveis obrigatórias no .env.",
            exc,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
