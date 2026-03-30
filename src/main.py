from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import load_settings
from src.parsing.vectra_parser import SignalParseError, VectraSignalParser
from src.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


def main() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)

    fixture_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "signal_short_01.txt"
    LOGGER.info("Iniciando dry-run. fixture=%s dry_run=%s", fixture_path, settings.dry_run)

    raw_text = fixture_path.read_text(encoding="utf-8")
    parser = VectraSignalParser()

    try:
        signal = parser.parse(raw_text)
    except SignalParseError as exc:
        LOGGER.error("Falha ao parsear sinal: %s", exc)
        return 1

    print(json.dumps(signal.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
