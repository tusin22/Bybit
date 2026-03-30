from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from telethon import TelegramClient, events
from telethon.errors import RPCError

from src.config import Settings
from src.parsing.vectra_parser import SignalParseError, VectraSignalParser

LOGGER = logging.getLogger(__name__)


class TelegramSignalListener:
    """Listener read-only para receber mensagens e tentar parsear sinais."""

    def __init__(
        self,
        settings: Settings,
        parser_factory: Callable[[], VectraSignalParser] = VectraSignalParser,
    ) -> None:
        self._settings = settings
        self._parser_factory = parser_factory
        self._parser = parser_factory()

        self._client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    async def run(self) -> None:
        LOGGER.info(
            "Iniciando listener Telegram em dry-run. source_chat=%s dry_run=%s",
            self._settings.telegram_source_chat,
            self._settings.dry_run,
        )

        await self._client.start()

        source_chat_ref: str | int = self._settings.telegram_source_chat.strip()
        try:
            source_chat_ref = int(source_chat_ref)
        except ValueError:
            pass

        try:
            source_chat_entity = await self._client.get_input_entity(source_chat_ref)
        except (ValueError, TypeError, RPCError) as exc:
            raise ValueError(
                "Não foi possível resolver TELEGRAM_SOURCE_CHAT. "
                "Use @username público ou ID numérico válido."
            ) from exc

        LOGGER.info("Chat/canal de origem resolvido com sucesso.")

        @self._client.on(events.NewMessage(chats=source_chat_entity))
        async def _handle_new_message(event: events.NewMessage.Event) -> None:
            raw_text = event.raw_text
            if not raw_text or not raw_text.strip():
                LOGGER.info("Mensagem ignorada: texto vazio.")
                return

            try:
                signal = self._parser.parse(raw_text)
            except SignalParseError as exc:
                LOGGER.warning("Mensagem ignorada por parsing inválido: %s", exc)
                return

            LOGGER.info(
                "Sinal parseado com sucesso (dry-run): %s",
                json.dumps(signal.to_dict(), ensure_ascii=False),
            )

        LOGGER.info("Cliente Telegram conectado. Aguardando novas mensagens...")
        try:
            await self._client.run_until_disconnected()
        except asyncio.CancelledError:
            LOGGER.info("Encerrando bot...")
        finally:
            if self._client.is_connected():
                await self._client.disconnect()
            LOGGER.info("Cliente Telegram desconectado.")
