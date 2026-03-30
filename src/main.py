from __future__ import annotations

import asyncio
import logging

from src.config import load_settings
from src.telegram.listener import TelegramSignalListener
from src.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


async def _run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)

    if not settings.dry_run:
        LOGGER.warning(
            "DRY_RUN está desativado, mas esta fase ainda opera sem integração de ordens."
        )

    listener = TelegramSignalListener(settings=settings)
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
