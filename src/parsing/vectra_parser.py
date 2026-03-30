from __future__ import annotations

import re
from dataclasses import dataclass

from src.models.signal import Signal


class SignalParseError(ValueError):
    """Erro específico para falhas de parsing de sinal."""


@dataclass(frozen=True, slots=True)
class _RegexBlock:
    name: str
    pattern: re.Pattern[str]


_HEADER_BLOCK = _RegexBlock(
    name="header",
    pattern=re.compile(
        r"^\s*(?P<symbol>[A-Z0-9]+)\s*\|\s*(?P<side>LONG|SHORT)\b[^\n]*$",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
)

_ENTRY_BLOCK = _RegexBlock(
    name="entry",
    pattern=re.compile(
        r"^\s*Entrada\s*:\s*(?P<entry_min>\d+(?:\.\d+)?)\s*[\-–]\s*(?P<entry_max>\d+(?:\.\d+)?)\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
)

_STOP_LOSS_BLOCK = _RegexBlock(
    name="stop_loss",
    pattern=re.compile(
        r"^\s*SL\s*:\s*(?P<stop_loss>\d+(?:\.\d+)?)\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
)

_TAKE_PROFIT_BLOCK = _RegexBlock(
    name="take_profit",
    pattern=re.compile(
        r"^\s*TP(?P<index>[1-4])\s*:\s*(?P<value>\d+(?:\.\d+)?)\s*$",
        flags=re.IGNORECASE | re.MULTILINE,
    ),
)


class VectraSignalParser:
    """Parser para formato de sinais Vectra nesta fase inicial."""

    def parse(self, raw_text: str) -> Signal:
        if not raw_text or not raw_text.strip():
            raise SignalParseError("Texto do sinal está vazio.")

        header_match = _HEADER_BLOCK.pattern.search(raw_text)
        if header_match is None:
            raise SignalParseError(
                "Não foi possível extrair cabeçalho (symbol | LONG/SHORT)."
            )

        entry_match = _ENTRY_BLOCK.pattern.search(raw_text)
        if entry_match is None:
            raise SignalParseError(
                "Não foi possível extrair faixa de entrada no formato 'Entrada: min - max'."
            )

        sl_match = _STOP_LOSS_BLOCK.pattern.search(raw_text)
        if sl_match is None:
            raise SignalParseError("Não foi possível extrair stop loss (SL).")

        tp_matches = list(_TAKE_PROFIT_BLOCK.pattern.finditer(raw_text))
        if len(tp_matches) != 4:
            raise SignalParseError(
                f"Quantidade inválida de take profits: esperado 4, recebido {len(tp_matches)}."
            )

        tp_values_by_index: dict[int, float] = {}
        for match in tp_matches:
            idx = int(match.group("index"))
            value = float(match.group("value"))
            if idx in tp_values_by_index:
                raise SignalParseError(f"Take profit duplicado encontrado: TP{idx}.")
            tp_values_by_index[idx] = value

        expected_indices = [1, 2, 3, 4]
        if sorted(tp_values_by_index.keys()) != expected_indices:
            raise SignalParseError("Take profits inválidos: esperado TP1, TP2, TP3 e TP4.")

        symbol = header_match.group("symbol").upper()
        side = header_match.group("side").upper()

        entry_min = float(entry_match.group("entry_min"))
        entry_max = float(entry_match.group("entry_max"))
        if entry_min > entry_max:
            raise SignalParseError(
                f"Faixa de entrada inválida: entry_min ({entry_min}) > entry_max ({entry_max})."
            )

        stop_loss = float(sl_match.group("stop_loss"))
        take_profits = [tp_values_by_index[i] for i in expected_indices]

        if side == "LONG":
            if any(tp <= entry_max for tp in take_profits):
                raise SignalParseError(
                    "Sinal LONG inconsistente: todos os take profits devem ser maiores que entry_max."
                )
            if stop_loss >= entry_min:
                raise SignalParseError(
                    "Sinal LONG inconsistente: stop_loss deve ser menor que entry_min."
                )

        if side == "SHORT":
            if any(tp >= entry_min for tp in take_profits):
                raise SignalParseError(
                    "Sinal SHORT inconsistente: todos os take profits devem ser menores que entry_min."
                )
            if stop_loss <= entry_max:
                raise SignalParseError(
                    "Sinal SHORT inconsistente: stop_loss deve ser maior que entry_max."
                )

        return Signal(
            symbol=symbol,
            side=side,
            entry_min=entry_min,
            entry_max=entry_max,
            take_profits=take_profits,
            stop_loss=stop_loss,
            raw_text=raw_text,
        )
