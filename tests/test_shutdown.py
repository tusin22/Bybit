from __future__ import annotations

import asyncio

from src.config import Settings
from src.main import main
from src.telegram.listener import TelegramSignalListener


class FakeClient:
    def __init__(self) -> None:
        self.disconnected = False

    async def start(self) -> None:
        return None

    async def get_input_entity(self, _source_chat_ref):
        return "chat"

    def on(self, *_args, **_kwargs):
        def _decorator(handler):
            return handler

        return _decorator

    async def run_until_disconnected(self) -> None:
        raise asyncio.CancelledError()

    def is_connected(self) -> bool:
        return not self.disconnected

    async def disconnect(self) -> None:
        self.disconnected = True


def _build_settings() -> Settings:
    return Settings(
        env="test",
        log_level="INFO",
        dry_run=True,
        telegram_api_id=123,
        telegram_api_hash="hash",
        telegram_session_name="session",
        telegram_source_chat="@channel",
        bybit_api_key="",
        bybit_api_secret="",
        bybit_testnet=True,
        enable_order_execution=False,
        execution_sizing_mode="fixed_notional_usdt",
        execution_fixed_notional_usdt=25.0,
        execution_fixed_qty=0.0,
    )


def test_listener_shutdown_disconnects_client_on_cancelled_error() -> None:
    listener = TelegramSignalListener(settings=_build_settings())
    fake_client = FakeClient()
    listener._client = fake_client

    asyncio.run(listener.run())

    assert fake_client.disconnected is True


def test_main_returns_zero_on_keyboard_interrupt(monkeypatch) -> None:
    def _raise_keyboard_interrupt(_coroutine):
        _coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("src.main.asyncio.run", _raise_keyboard_interrupt)

    assert main() == 0
