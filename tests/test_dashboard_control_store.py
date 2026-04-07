from __future__ import annotations

import json

from src.dashboard.control_store import (
    DEFAULT_CONTROL_STATE,
    DEFAULT_RUNTIME_CONFIG,
    load_control_snapshot,
    save_runtime_config,
    set_desired_run_state,
)


def test_load_control_snapshot_creates_defaults_without_files(tmp_path) -> None:
    control_state_file = tmp_path / "control_state.json"
    runtime_config_file = tmp_path / "runtime_config.json"

    snapshot = load_control_snapshot(
        control_state_file=control_state_file,
        runtime_config_file=runtime_config_file,
    )

    assert snapshot.control_state == DEFAULT_CONTROL_STATE
    assert snapshot.runtime_config == DEFAULT_RUNTIME_CONFIG


def test_load_control_snapshot_recovers_from_invalid_json(tmp_path) -> None:
    control_state_file = tmp_path / "control_state.json"
    runtime_config_file = tmp_path / "runtime_config.json"
    control_state_file.write_text("{bad", encoding="utf-8")
    runtime_config_file.write_text("[1,2,3]", encoding="utf-8")

    snapshot = load_control_snapshot(
        control_state_file=control_state_file,
        runtime_config_file=runtime_config_file,
    )

    assert snapshot.control_state == DEFAULT_CONTROL_STATE
    assert snapshot.runtime_config == DEFAULT_RUNTIME_CONFIG


def test_set_desired_run_state_writes_safe_json(tmp_path) -> None:
    control_state_file = tmp_path / "runtime" / "control" / "control_state.json"

    saved = set_desired_run_state("running", control_state_file=control_state_file)

    assert saved["desiredRunState"] == "running"
    assert saved["changedBy"] == "dashboard"
    assert saved["changedAt"]

    disk = json.loads(control_state_file.read_text(encoding="utf-8"))
    assert disk == saved


def test_save_runtime_config_writes_safe_json(tmp_path) -> None:
    runtime_config_file = tmp_path / "runtime" / "control" / "runtime_config.json"

    saved = save_runtime_config(
        {
            "signalSource": "telegram",
            "autoAnalysisEnabled": False,
            "symbol": "ETHUSDT",
            "interval": "15",
            "emaShort": 8,
            "emaMid": 20,
            "useVolumeFilter": False,
            "volumeMultiplier": 1.0,
            "useAdxFilter": False,
            "adxLen": 10,
            "adxThreshold": 18.5,
            "cooldownCandles": 1,
            "dryRun": True,
            "enableOrderExecution": False,
        },
        runtime_config_file=runtime_config_file,
    )

    assert saved["signalSource"] == "telegram"
    assert saved["symbol"] == "ETHUSDT"
    assert saved["updatedBy"] == "dashboard"
    assert saved["updatedAt"]

    disk = json.loads(runtime_config_file.read_text(encoding="utf-8"))
    assert disk == saved
