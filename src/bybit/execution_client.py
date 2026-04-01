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


@dataclass(slots=True)
class BybitSetTradingStopRequest:
    category: str
    symbol: str
    stop_loss: str
    position_idx: int


class BybitExecutionClient:
    """Cliente Bybit V5 para envio e confirmação REST de ordens (escopo: linear)."""

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

    def set_trading_stop(self, *, request: BybitSetTradingStopRequest) -> dict[str, object]:
        if not self._has_auth:
            raise BybitExecutionClientError(
                "Credenciais Bybit ausentes: BYBIT_API_KEY e BYBIT_API_SECRET são obrigatórios para execução."
            )

        if request.category != "linear":
            raise BybitExecutionClientError(
                "Categoria inválida para esta fase de execução: apenas linear é suportada."
            )

        response = self._http.set_trading_stop(
            category=request.category,
            symbol=request.symbol,
            tpslMode="Full",
            stopLoss=request.stop_loss,
            positionIdx=request.position_idx,
        )
        self._assert_success(response, operation="set_trading_stop")
        return response

    def get_open_orders(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None,
        order_link_id: str | None,
    ) -> dict[str, object]:
        if not self._has_auth:
            raise BybitExecutionClientError(
                "Credenciais Bybit ausentes: BYBIT_API_KEY e BYBIT_API_SECRET são obrigatórios para execução."
            )

        query: dict[str, object] = {
            "category": category,
            "symbol": symbol,
            "openOnly": 0,
            "limit": 1,
        }
        if order_id:
            query["orderId"] = order_id
        elif order_link_id:
            query["orderLinkId"] = order_link_id

        response = self._http.get_open_orders(**query)
        self._assert_success(response, operation="get_open_orders")
        return response

    def get_order_history(
        self,
        *,
        category: str,
        symbol: str,
        order_id: str | None,
        order_link_id: str | None,
    ) -> dict[str, object]:
        if not self._has_auth:
            raise BybitExecutionClientError(
                "Credenciais Bybit ausentes: BYBIT_API_KEY e BYBIT_API_SECRET são obrigatórios para execução."
            )

        query: dict[str, object] = {
            "category": category,
            "symbol": symbol,
            "limit": 1,
        }
        if order_id:
            query["orderId"] = order_id
        elif order_link_id:
            query["orderLinkId"] = order_link_id

        response = self._http.get_order_history(**query)
        self._assert_success(response, operation="get_order_history")
        return response

    @staticmethod
    def extract_first_order(response: dict[str, object]) -> dict[str, object] | None:
        result = response.get("result")
        if not isinstance(result, dict):
            return None

        raw_list = result.get("list")
        if not isinstance(raw_list, list) or not raw_list:
            return None

        first = raw_list[0]
        if not isinstance(first, dict):
            return None

        return first

    @staticmethod
    def _assert_success(response: dict[str, object], *, operation: str) -> None:
        ret_code = response.get("retCode")
        if ret_code != 0:
            ret_msg = response.get("retMsg", "")
            raise BybitExecutionClientError(
                f"Falha Bybit em {operation}: retCode={ret_code} retMsg={ret_msg}"
            )
