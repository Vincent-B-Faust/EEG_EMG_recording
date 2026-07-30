from __future__ import annotations

import itertools
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture
def make_config(tmp_path: Path) -> Callable[..., Path]:
    counter = itertools.count()

    def _make_config(
        *,
        channels: list[dict[str, Any]] | None = None,
        backend: str = "fake",
        device: str = "synthetic",
        sample_rate_hz: float = 200.0,
        block_duration_s: float = 0.02,
        duration_s: float | None = 0.1,
        terminal_config: str = "DIFF",
        csv_enabled: bool = False,
        csv_decimation: int = 1,
        control_dir: Path | None = None,
        parent_session_dir: Path | None = None,
        start_trigger_enabled: bool = False,
        start_trigger_source: str = "",
        analog_start_match_enabled: bool = False,
        analog_start_match_channel: str = "",
    ) -> Path:
        configured_channels = channels or [
            {
                "name": "Mouse01_EEG",
                "mouse_id": "Mouse01",
                "role": "EEG",
                "physical_channel": "ai0",
                "enabled": True,
                "amplifier_gain": 1000,
            },
            {
                "name": "Mouse01_EMG",
                "mouse_id": "Mouse01",
                "role": "EMG",
                "physical_channel": "ai1",
                "enabled": True,
                "amplifier_gain": 1000,
            },
        ]
        raw = {
            "project": {
                "output_root": str(tmp_path / "runs"),
                "session_name": "pytest_sleep",
                "mouse_ids": ["Mouse01"],
            },
            "acquisition": {
                "backend": backend,
                "device": device,
                "sample_rate_hz": sample_rate_hz,
                "block_duration_s": block_duration_s,
                "duration_s": duration_s,
                "terminal_config": terminal_config,
                "input_range_v": [-10.0, 10.0],
                "read_timeout_s": 1.0,
                "start_trigger": {
                    "enabled": start_trigger_enabled,
                    "source": start_trigger_source,
                    "edge": "rising",
                },
                "channels": configured_channels,
            },
            "preview": {
                "enabled": False,
                "history_s": 2.0,
                "refresh_hz": 10.0,
                "display_units": "input_uv",
                "filter_display_only": True,
                "notch_hz": 50.0,
                "eeg_bandpass_hz": [0.5, 45.0],
                "emg_bandpass_hz": [10.0, 90.0],
            },
            "storage": {
                "flush_interval_s": 0.01,
                "hdf5_compression": "lzf",
                "csv_enabled": csv_enabled,
                "csv_decimation": csv_decimation,
            },
            "sync": {
                "control_dir": str(control_dir) if control_dir else None,
                "parent_session_dir": str(parent_session_dir) if parent_session_dir else None,
                "analog_threshold_v": 2.5,
                "analog_edge": "rising",
                "analog_start_match_enabled": analog_start_match_enabled,
                "analog_start_match_channel": analog_start_match_channel,
            },
        }
        path = tmp_path / f"config_{next(counter)}.yaml"
        path.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return path

    return _make_config
