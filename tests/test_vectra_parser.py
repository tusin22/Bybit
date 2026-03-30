from __future__ import annotations

from pathlib import Path

import pytest

from src.parsing.vectra_parser import SignalParseError, VectraSignalParser


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_parse_short_signal_real_case() -> None:
    parser = VectraSignalParser()

    signal = parser.parse(_load_fixture("signal_short_01.txt"))

    assert signal.symbol == "NEARUSDT"
    assert signal.side == "SHORT"
    assert signal.entry_min == 1.1627
    assert signal.entry_max == 1.1673
    assert signal.take_profits == [1.1209, 1.1114, 1.0769, 1.0721]
    assert signal.stop_loss == 1.2214


def test_parse_long_signal_equivalent_case() -> None:
    parser = VectraSignalParser()

    signal = parser.parse(_load_fixture("signal_long_01.txt"))

    assert signal.symbol == "BTCUSDT"
    assert signal.side == "LONG"
    assert signal.entry_min == 68100.0
    assert signal.entry_max == 68450.5
    assert signal.take_profits == [68990.0, 69500.5, 70125.0, 70980.0]
    assert signal.stop_loss == 67220.0


def test_parse_fails_on_empty_text() -> None:
    parser = VectraSignalParser()

    with pytest.raises(SignalParseError, match="Texto do sinal está vazio"):
        parser.parse("   \n")


def test_parse_fails_on_invalid_header() -> None:
    parser = VectraSignalParser()
    raw = """
INVALID HEADER
Entrada: 1.0 - 2.0
TP1: 2.1
TP2: 2.2
TP3: 2.3
TP4: 2.4
SL: 0.9
"""

    with pytest.raises(SignalParseError, match="cabeçalho"):
        parser.parse(raw)


def test_parse_fails_when_tp_count_is_not_four() -> None:
    parser = VectraSignalParser()
    raw = """
BTCUSDT | LONG
Entrada: 1.0 - 2.0
TP1: 2.1
TP2: 2.2
TP3: 2.3
SL: 0.9
"""

    with pytest.raises(SignalParseError, match="esperado 4"):
        parser.parse(raw)


def test_parse_fails_on_duplicate_tp() -> None:
    parser = VectraSignalParser()
    raw = """
BTCUSDT | LONG
Entrada: 1.0 - 2.0
TP1: 2.1
TP1: 2.2
TP3: 2.3
TP4: 2.4
SL: 0.9
"""

    with pytest.raises(SignalParseError, match="duplicado"):
        parser.parse(raw)


def test_parse_fails_on_inverted_entry_range() -> None:
    parser = VectraSignalParser()
    raw = """
BTCUSDT | LONG
Entrada: 2.0 - 1.0
TP1: 2.1
TP2: 2.2
TP3: 2.3
TP4: 2.4
SL: 0.9
"""

    with pytest.raises(SignalParseError, match="entry_min"):
        parser.parse(raw)


def test_parse_fails_on_inconsistent_long_signal() -> None:
    parser = VectraSignalParser()
    raw = """
BTCUSDT | LONG
Entrada: 10.0 - 11.0
TP1: 11.5
TP2: 12.0
TP3: 10.8
TP4: 13.0
SL: 9.0
"""

    with pytest.raises(SignalParseError, match="LONG inconsistente"):
        parser.parse(raw)


def test_parse_fails_on_inconsistent_short_signal() -> None:
    parser = VectraSignalParser()
    raw = """
NEARUSDT | SHORT
Entrada: 10.0 - 11.0
TP1: 9.5
TP2: 9.0
TP3: 8.5
TP4: 8.0
SL: 10.5
"""

    with pytest.raises(SignalParseError, match="SHORT inconsistente"):
        parser.parse(raw)
