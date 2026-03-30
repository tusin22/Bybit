from __future__ import annotations

from dataclasses import dataclass



class BybitClientError(Exception):
    """Erro explícito para falhas em consultas read-only da Bybit."""


@dataclass(slots=True)
class InstrumentInfo:
    symbol: str
    category: str
    raw: dict[str, object]


class BybitReadOnlyClient:
    """Cliente Bybit V5 somente leitura (sem envio de ordens)."""

    def __init__(self, *, api_key: str, api_secret: str, testnet: bool) -> None:
        from pybit.unified_trading import HTTP

        self._http = HTTP(api_key=api_key, api_secret=api_secret, testnet=testnet)

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
        return InstrumentInfo(symbol=symbol, category=category, raw=raw)

    @staticmethod
    def _assert_success(response: dict[str, object], *, operation: str) -> None:
        ret_code = response.get("retCode")
        if ret_code != 0:
            ret_msg = response.get("retMsg", "")
            raise BybitClientError(
                f"Falha Bybit em {operation}: retCode={ret_code} retMsg={ret_msg}"
            )
