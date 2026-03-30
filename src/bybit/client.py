from __future__ import annotations

from dataclasses import dataclass


class BybitClientError(Exception):
    """Erro explícito para falhas em consultas read-only da Bybit."""


@dataclass(slots=True)
class InstrumentInfo:
    symbol: str
    category: str
    status: str | None
    tick_size: str | None
    qty_step: str | None
    raw: dict[str, object]


class BybitReadOnlyClient:
    """Cliente Bybit V5 somente leitura (sem envio de ordens)."""

    def __init__(self, *, api_key: str, api_secret: str, testnet: bool) -> None:
        from pybit.unified_trading import HTTP

        has_auth = bool(api_key.strip() and api_secret.strip())
        if has_auth:
            self._http = HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)
        else:
            # Endpoints de market usados nesta fase são públicos.
            self._http = HTTP(testnet=testnet)

    def get_last_price(self, *, symbol: str, category: str = "linear") -> float:
        response = self._http.get_tickers(category=category, symbol=symbol)
        self._assert_success(response, operation="get_tickers")

        result = response.get("result", {})
        items = result.get("list", []) if isinstance(result, dict) else []
        if not items:
            raise BybitClientError(
                f"Bybit get_tickers retornou lista vazia para {symbol}."
            )

        raw_last_price = items[0].get("lastPrice")
        if raw_last_price is None:
            raise BybitClientError(
                f"Campo lastPrice ausente no get_tickers para {symbol}."
            )

        try:
            return float(raw_last_price)
        except (TypeError, ValueError) as exc:
            raise BybitClientError(
                f"Valor lastPrice inválido em get_tickers para {symbol}: {raw_last_price}."
            ) from exc

    def get_instrument_info(
        self,
        *,
        symbol: str,
        category: str = "linear",
    ) -> InstrumentInfo:
        response = self._http.get_instruments_info(category=category, symbol=symbol)
        self._assert_success(response, operation="get_instruments_info")

        result = response.get("result", {})
        items = result.get("list", []) if isinstance(result, dict) else []
        if not items:
            raise BybitClientError(
                f"Bybit get_instruments_info retornou lista vazia para {symbol}."
            )

        raw = items[0]
        price_filter = raw.get("priceFilter", {})
        lot_size_filter = raw.get("lotSizeFilter", {})

        tick_size = None
        if isinstance(price_filter, dict):
            tick_size = price_filter.get("tickSize")

        qty_step = None
        if isinstance(lot_size_filter, dict):
            qty_step = lot_size_filter.get("qtyStep")

        status = raw.get("status")

        return InstrumentInfo(
            symbol=symbol,
            category=category,
            status=status if isinstance(status, str) else None,
            tick_size=tick_size if isinstance(tick_size, str) else None,
            qty_step=qty_step if isinstance(qty_step, str) else None,
            raw=raw,
        )

    @staticmethod
    def _assert_success(response: dict[str, object], *, operation: str) -> None:
        ret_code = response.get("retCode")
        if ret_code != 0:
            ret_msg = response.get("retMsg", "")
            raise BybitClientError(
                f"Falha Bybit em {operation}: retCode={ret_code} retMsg={ret_msg}"
            )
