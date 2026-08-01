from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from eeg_emg_recording.recorder import run_recording


def test_recorder_publishes_atomic_live_preview(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    control_dir = tmp_path / "control"
    session_dir = tmp_path / "session"
    config_path = make_config(
        duration_s=0.12,
        block_duration_s=0.02,
        control_dir=control_dir,
    )

    result = run_recording(
        config_path,
        session_dir=session_dir,
        preview=False,
    )

    payload = json.loads(
        (control_dir / "live_preview.json").read_text(encoding="utf-8")
    )
    assert result.status == "completed"
    assert payload["schema"] == "aki_eeg_emg_live_preview_v1"
    assert payload["sample_rate_hz"] == 200.0
    assert payload["samples_written"] > 0
    assert set(payload["traces"]) == {"Mouse01_EEG", "Mouse01_EMG"}
    assert payload["traces"]["Mouse01_EEG"]["role"] == "EEG"
    assert payload["traces"]["Mouse01_EEG"]["input_uv_derived"]
