from __future__ import annotations

from dataclasses import dataclass


class BybitExecutionClientError(Exception):
    """Erro explícito para falhas na execução transacional na Bybit."""


@dataclass(slots=True)
class BybitOrderRequest:
    category: str
    symbol: str
    side: str
    qty: str
    position_idx: int
    order_link_id: str | None = None


class BybitExecutionClient:
    """Cliente Bybit V5 para envio de ordens (escopo inicial: entrada market em linear)."""

    def __init__(self, *, api_key: str, api_secret: str, testnet: bool) -> None:
        from pybit.unified_trading import HTTP

        self._has_auth = bool(api_key.strip() and api_secret.strip())
        if self._has_auth:
            self._http = HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)
        else:
            self._http = HTTP(testnet=testnet)

    def place_entry_market_order(self, *, order: BybitOrderRequest) -> dict[str, object]:
        if not self._has_auth:
            raise BybitExecutionClientError(
                "Credenciais Bybit ausentes: BYBIT_API_KEY e BYBIT_API_SECRET são obrigatórios para execução."
            )

        if order.category != "linear":
            raise BybitExecutionClientError(
                "Categoria inválida para esta fase de execução: apenas linear é suportada."
            )

        request_payload: dict[str, object] = {
            "category": order.category,
            "symbol": order.symbol,
            "side": order.side,
            "orderType": "Market",
            "qty": order.qty,
            "positionIdx": order.position_idx,
        }
        if order.order_link_id:
            request_payload["orderLinkId"] = order.order_link_id

        response = self._http.place_order(**request_payload)
        self._assert_success(response, operation="place_order")
        return response

    @staticmethod
    def _assert_success(response: dict[str, object], *, operation: str) -> None:
        ret_code = response.get("retCode")
        if ret_code != 0:
            ret_msg = response.get("retMsg", "")
            raise BybitExecutionClientError(
                f"Falha Bybit em {operation}: retCode={ret_code} retMsg={ret_msg}"
            )
