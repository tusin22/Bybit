from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

LOGGER = logging.getLogger(__name__)


class BybitPrivateWsMonitorError(Exception):
    """Erro explícito para falhas no monitor websocket privado da execução atual."""


@dataclass(frozen=True, slots=True)
class PrivateWsOrderEvent:
    order_id: str | None
    order_link_id: str | None
    order_status: str | None


@dataclass(frozen=True, slots=True)
class PrivateWsWindowResult:
    started: bool
    connected: bool
    authenticated: bool
    subscribed: bool
    events_received: int
    position_closed_confirmed: bool
    reason: str | None
    matched_order_events: list[PrivateWsOrderEvent]
    decision_source: str
    position_decision: str


class BybitPrivateExecutionWsMonitor:
    """Monitor curto da execução atual via websocket privado Bybit V5."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        testnet: bool,
    ) -> None:
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._testnet = testnet

    def run_window(
        self,
        *,
        symbol: str,
        category: str,
        side: str,
        entry_order_id: str | None,
        entry_order_link_id: str | None,
        registered_tp_orders: list[dict[str, object]],
        max_attempts: int,
        interval_seconds: float,
    ) -> PrivateWsWindowResult:
        if category != "linear":
            raise BybitPrivateWsMonitorError(
                "Categoria inválida para monitor websocket nesta fase: apenas linear é suportada."
            )

        if side not in {"Buy", "Sell"}:
            raise BybitPrivateWsMonitorError(
                "Lado inválido para monitor websocket nesta fase: expected Buy/Sell."
            )

        if not self._api_key or not self._api_secret:
            raise BybitPrivateWsMonitorError(
                "Credenciais Bybit ausentes para websocket privado: BYBIT_API_KEY e BYBIT_API_SECRET são obrigatórios."
            )

        from pybit.unified_trading import WebSocket

        tracked_ids = {
            value
            for value in [entry_order_id, entry_order_link_id]
            if isinstance(value, str) and value.strip()
        }
        for tp in registered_tp_orders:
            order_id = tp.get("orderId")
            order_link_id = tp.get("orderLinkId")
            if isinstance(order_id, str) and order_id.strip():
                tracked_ids.add(order_id)
            if isinstance(order_link_id, str) and order_link_id.strip():
                tracked_ids.add(order_link_id)

        side_for_position = side
        events_received = 0
        matched_order_events: list[PrivateWsOrderEvent] = []
        position_decision = "inconclusive"
        condition = threading.Condition()

        connected = False
        authenticated = False
        subscribed = False

        def _on_order(message: dict[str, object]) -> None:
            nonlocal events_received
            payloads = message.get("data")
            if not isinstance(payloads, list):
                return

            with condition:
                for item in payloads:
                    if not isinstance(item, dict):
                        continue
                    if item.get("category") != category:
                        continue
                    if item.get("symbol") != symbol:
                        continue

                    order_id = _as_optional_string(item.get("orderId"))
                    order_link_id = _as_optional_string(item.get("orderLinkId"))
                    if order_id not in tracked_ids and order_link_id not in tracked_ids:
                        continue

                    events_received += 1
                    matched_order_events.append(
                        PrivateWsOrderEvent(
                            order_id=order_id,
                            order_link_id=order_link_id,
                            order_status=_as_optional_string(item.get("orderStatus")),
                        )
                    )
                condition.notify_all()

        def _on_position(message: dict[str, object]) -> None:
            nonlocal events_received, position_decision
            payloads = message.get("data")
            if not isinstance(payloads, list):
                return

            with condition:
                for item in payloads:
                    if not isinstance(item, dict):
                        continue
                    if item.get("category") != category:
                        continue
                    if item.get("symbol") != symbol:
                        continue
                    if _as_optional_string(item.get("side")) != side_for_position:
                        continue

                    size = _as_optional_string(item.get("size"))
                    if size is None:
                        continue

                    try:
                        size_decimal = Decimal(size)
                    except (InvalidOperation, ValueError):
                        continue

                    events_received += 1
                    position_decision = _resolve_position_decision_from_size(size_decimal)
                condition.notify_all()

        try:
            ws = WebSocket(
                testnet=self._testnet,
                channel_type="private",
                api_key=self._api_key,
                api_secret=self._api_secret,
            )
            connected = True
            authenticated = True

            ws.order_stream(callback=_on_order)
            ws.position_stream(callback=_on_position)
            subscribed = True

            timeout_seconds = max_attempts * interval_seconds
            end_time = time.monotonic() + timeout_seconds

            while time.monotonic() < end_time:
                if position_decision == "position_closed":
                    return PrivateWsWindowResult(
                        started=True,
                        connected=connected,
                        authenticated=authenticated,
                        subscribed=subscribed,
                        events_received=events_received,
                        position_closed_confirmed=True,
                        reason="position_closed_via_private_ws",
                        matched_order_events=matched_order_events,
                        decision_source="websocket_position",
                        position_decision=position_decision,
                    )

                if matched_order_events and position_decision == "inconclusive":
                    return PrivateWsWindowResult(
                        started=True,
                        connected=connected,
                        authenticated=authenticated,
                        subscribed=subscribed,
                        events_received=events_received,
                        position_closed_confirmed=False,
                        reason="private_ws_order_only_without_position_confirmation",
                        matched_order_events=matched_order_events,
                        decision_source="websocket_order_complementary_only",
                        position_decision=position_decision,
                    )

                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    break

                with condition:
                    condition.wait(timeout=min(interval_seconds, remaining))

            return PrivateWsWindowResult(
                started=True,
                connected=connected,
                authenticated=authenticated,
                subscribed=subscribed,
                events_received=events_received,
                position_closed_confirmed=False,
                reason="private_ws_window_expired_without_final_position_event",
                matched_order_events=matched_order_events,
                decision_source="websocket_position_inconclusive",
                position_decision=position_decision,
            )
        except Exception as exc:  # noqa: BLE001
            raise BybitPrivateWsMonitorError(f"Falha no monitor websocket privado: {exc}") from exc
        finally:
            if "ws" in locals():
                exit_method = getattr(ws, "exit", None)
                if callable(exit_method):
                    try:
                        exit_method()
                    except Exception:  # noqa: BLE001
                        LOGGER.debug("Falha ao encerrar websocket privado de monitor.", exc_info=True)


def _as_optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _resolve_position_decision_from_size(size: Decimal) -> str:
    """
    Regra de fonte de verdade do monitor websocket:
    - position (size <= 0) confirma fechamento final.
    - position (size > 0) confirma posição ainda aberta.
    - order nunca fecha posição sozinho; é apenas telemetria complementar.
    """
    if size <= 0:
        return "position_closed"
    return "position_open"
