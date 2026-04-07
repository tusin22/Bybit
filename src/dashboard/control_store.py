from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

DesiredRunState = Literal["running", "stopped"]

CONTROL_DIR = Path("runtime/control")
CONTROL_STATE_FILE = CONTROL_DIR / "control_state.json"
RUNTIME_CONFIG_FILE = CONTROL_DIR / "runtime_config.json"

DEFAULT_CONTROL_STATE: dict[str, Any] = {
    "desiredRunState": "stopped",
    "changedAt": "",
    "changedBy": "dashboard",
}

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "signalSource": "auto_analysis",
    "autoAnalysisEnabled": True,
    "symbol": "BTCUSDT",
    "interval": "60",
    "emaShort": 9,
    "emaMid": 21,
    "useVolumeFilter": True,
    "volumeMultiplier": 1.2,
    "useAdxFilter": True,
    "adxLen": 14,
    "adxThreshold": 20.0,
    "cooldownCandles": 3,
    "dryRun": True,
    "enableOrderExecution": False,
    "updatedAt": "",
    "updatedBy": "dashboard",
}


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    control_state: dict[str, Any]
    runtime_config: dict[str, Any]


def load_control_snapshot(
    *,
    control_state_file: Path = CONTROL_STATE_FILE,
    runtime_config_file: Path = RUNTIME_CONFIG_FILE,
) -> ControlSnapshot:
    control_state = _load_json_with_defaults(control_state_file, DEFAULT_CONTROL_STATE)
    runtime_config = _load_json_with_defaults(runtime_config_file, DEFAULT_RUNTIME_CONFIG)

    return ControlSnapshot(control_state=control_state, runtime_config=runtime_config)


def set_desired_run_state(
    desired_run_state: DesiredRunState,
    *,
    control_state_file: Path = CONTROL_STATE_FILE,
) -> dict[str, Any]:
    payload = _load_json_with_defaults(control_state_file, DEFAULT_CONTROL_STATE)
    payload["desiredRunState"] = desired_run_state
    payload["changedAt"] = _now_iso_utc()
    payload["changedBy"] = "dashboard"
    _write_json_atomic(control_state_file, payload)
    return payload


def save_runtime_config(
    updates: dict[str, Any],
    *,
    runtime_config_file: Path = RUNTIME_CONFIG_FILE,
) -> dict[str, Any]:
    payload = _load_json_with_defaults(runtime_config_file, DEFAULT_RUNTIME_CONFIG)

    for key, value in updates.items():
        if key in DEFAULT_RUNTIME_CONFIG:
            payload[key] = value

    payload["updatedAt"] = _now_iso_utc()
    payload["updatedBy"] = "dashboard"
    _write_json_atomic(runtime_config_file, payload)
    return payload


def _load_json_with_defaults(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except (OSError, json.JSONDecodeError):
            payload = {}

    merged = dict(defaults)
    for key in defaults:
        if key in payload:
            merged[key] = payload[key]

    return merged


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()
