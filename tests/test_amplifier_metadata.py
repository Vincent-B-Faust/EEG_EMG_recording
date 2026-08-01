from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import h5py
import numpy as np
import pytest
import yaml

from eeg_emg_recording.config import load_config, save_config
from eeg_emg_recording.preview import display_values
from eeg_emg_recording.recorder import run_recording


def _configured_channel(**overrides: object) -> dict[str, object]:
    channel: dict[str, object] = {
        "name": "Mouse01_EEG",
        "mouse_id": "Mouse01",
        "role": "EEG",
        "physical_channel": "ai0",
        "enabled": True,
        "amplifier_gain": 2000,
        "amplifier_model": "WPI DAM50",
        "amplifier_serial": "DAM50-TEST-01",
        "amplifier_mode": "AC",
        "hardware_filter_enabled": True,
        "hardware_highpass_hz": 0.1,
        "hardware_lowpass_hz": 300.0,
        "hardware_notch_enabled": True,
        "hardware_notch_hz": 50.0,
        "amplifier_settings_verified": True,
        "amplifier_notes": "Front-panel settings checked before acquisition",
        "terminal_config": "RSE",
        "input_min_v": -2.0,
        "input_max_v": 2.0,
    }
    channel.update(overrides)
    return channel


def test_amplifier_fields_parse_and_saved_config_expands_defaults(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config = load_config(make_config(channels=[_configured_channel()]))
    channel = config.acquisition.active_channels[0]

    assert channel.amplifier_model == "WPI DAM50"
    assert channel.amplifier_serial == "DAM50-TEST-01"
    assert channel.amplifier_gain == 2000
    assert channel.amplifier_mode == "AC"
    assert channel.hardware_filter_enabled is True
    assert channel.hardware_highpass_hz == 0.1
    assert channel.hardware_lowpass_hz == 300.0
    assert channel.hardware_notch_enabled is True
    assert channel.hardware_notch_hz == 50.0
    assert channel.amplifier_settings_verified is True
    assert channel.amplifier_notes.startswith("Front-panel")
    assert channel.input_uv_per_output_v == 500.0
    displayed, units = display_values(
        np.asarray([0.001]),
        channel,
        config.acquisition.sample_rate_hz,
        config,
    )
    assert displayed.tolist() == [0.5]
    assert units == "uV (derived)"

    minimal = load_config(
        make_config(
            channels=[
                {
                    "name": "Mouse01_EMG",
                    "role": "EMG",
                    "physical_channel": "ai0",
                    "enabled": True,
                    "amplifier_gain": 1000,
                }
            ]
        )
    )
    saved_path = save_config(minimal, tmp_path / "expanded.yaml")
    saved = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
    saved_channel = saved["acquisition"]["channels"][0]
    assert saved_channel["amplifier_model"] == "WPI DAM50"
    assert saved_channel["hardware_filter_enabled"] is True
    assert saved_channel["hardware_notch_enabled"] is False
    assert saved_channel["amplifier_settings_verified"] is False
    assert saved_channel["amplifier_notes"] == ""


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        (
            {"hardware_notch_enabled": True, "hardware_notch_hz": None},
            "hardware_notch_hz is required",
        ),
        (
            {"hardware_highpass_hz": 300.0, "hardware_lowpass_hz": 100.0},
            "hardware_highpass_hz must be below",
        ),
        (
            {"input_min_v": -2.0, "input_max_v": None},
            "input_min_v and input_max_v must be set together",
        ),
    ),
)
def test_invalid_amplifier_metadata_is_rejected(
    make_config: Callable[..., Path],
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(make_config(channels=[_configured_channel(**overrides)]))


def test_recording_persists_amplifier_metadata_without_converting_raw_voltage(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        channels=[_configured_channel()],
        sample_rate_hz=200,
        block_duration_s=0.02,
        duration_s=0.08,
    )
    session_dir = tmp_path / "amplifier_metadata_session"
    result = run_recording(config_path, session_dir=session_dir, preview=False)

    config_used = yaml.safe_load(
        (session_dir / "config_used.yaml").read_text(encoding="utf-8")
    )
    snapshot = config_used["acquisition"]["channels"][0]
    assert snapshot["amplifier_serial"] == "DAM50-TEST-01"
    assert snapshot["hardware_notch_enabled"] is True
    assert snapshot["amplifier_settings_verified"] is True

    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    amplifier = metadata["amplifier_settings"][0]
    assert amplifier["settings_source"] == "manual_user_entry"
    assert amplifier["gain"] == 2000
    assert amplifier["daq_input_range_v"] == [-2.0, 2.0]
    assert amplifier["settings_verified_against_hardware"] is True
    assert metadata["signal_storage"]["stored_units"] == "V"
    assert metadata["signal_storage"]["derived_input_values_stored"] is False

    with h5py.File(result.signals_path, "r") as handle:
        raw = handle["/signals/output_voltage_v"][:]
        dataset = handle["/signals/output_voltage_v"]
        amplifier_json = json.loads(handle.attrs["amplifier_settings_json"])
        grouped_json = json.loads(
            handle["/metadata/amplifiers"].attrs["configuration_json"]
        )
        assert dataset.attrs["units"] == "V"
        assert bool(dataset.attrs["is_raw_daq_measurement"]) is True
        assert bool(dataset.attrs["derived_input_values_stored"]) is False
        assert amplifier_json[0]["serial"] == "DAM50-TEST-01"
        assert grouped_json == amplifier_json
        assert "/signals/derived_input_uv" not in handle

    assert raw.shape == (16, 1)
    assert np.max(np.abs(raw)) < 1.0
    assert np.max(np.abs(raw * 1_000_000.0 / 2000.0)) > 1.0
