from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from eeg_emg_recording.config import load_config
from eeg_emg_recording.devices import DeviceInfo, validate_hardware


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_configs_are_valid_and_explain_channel_capacity() -> None:
    hardware = load_config(PROJECT_ROOT / "configs" / "ni_usb6001_dam50.yaml")
    active = hardware.acquisition.active_channels

    assert hardware.acquisition.backend == "nidaqmx"
    assert hardware.acquisition.terminal_config == "DIFF"
    assert [channel.physical_channel for channel in active] == ["ai0", "ai1"]
    assert [channel.role for channel in active] == ["EEG", "EMG"]
    assert len({channel.physical_channel for channel in active}) == len(active)
    assert hardware.acquisition.sample_rate_hz * len(active) <= 20_000

    fake = load_config(PROJECT_ROOT / "configs" / "fake_demo.yaml")
    assert fake.acquisition.backend == "fake"
    assert len(fake.acquisition.active_channels) == 5
    assert {channel.role for channel in fake.acquisition.active_channels} == {
        "EEG",
        "EMG",
        "SYNC",
    }


def test_arbitrary_channel_count_preserves_enabled_order(
    make_config: Callable[..., Path],
) -> None:
    channels = [
        {
            "name": f"Signal_{index}",
            "mouse_id": f"Mouse{index // 2 + 1:02d}",
            "role": ("EEG", "EMG", "AUX")[index % 3],
            "physical_channel": f"ai{index}",
            "enabled": index != 3,
            "amplifier_gain": 100 * (index + 1),
        }
        for index in range(7)
    ]
    config = load_config(make_config(channels=channels))

    assert len(config.acquisition.channels) == 7
    assert [channel.name for channel in config.acquisition.active_channels] == [
        "Signal_0",
        "Signal_1",
        "Signal_2",
        "Signal_4",
        "Signal_5",
        "Signal_6",
    ]
    assert config.acquisition.active_channels[-1].amplifier_gain == 700


def test_usb6001_diff_pairs_and_aggregate_rate_are_enforced(
    make_config: Callable[..., Path],
) -> None:
    device = DeviceInfo(
        name="Dev1",
        product_type="NI USB-6001",
        serial_number="TEST",
        simulated=True,
        analog_input_channels=tuple(f"Dev1/ai{index}" for index in range(8)),
        digital_input_lines=(),
        digital_output_lines=(),
        max_single_channel_rate_hz=20_000,
        max_multi_channel_rate_hz=20_000,
    )
    four_dam50_outputs = [
        {
            "name": f"DAM50_{index + 1}",
            "role": "EEG" if index % 2 == 0 else "EMG",
            "physical_channel": f"ai{index}",
            "enabled": True,
            "amplifier_gain": 1000,
        }
        for index in range(4)
    ]

    at_limit = load_config(
        make_config(
            channels=four_dam50_outputs,
            backend="nidaqmx",
            device="Dev1",
            sample_rate_hz=5000,
        )
    )
    report = validate_hardware(at_limit, [device])
    assert report["aggregate_samples_per_second"] == 20_000
    assert report["resolved_channels"] == [
        "Dev1/ai0",
        "Dev1/ai1",
        "Dev1/ai2",
        "Dev1/ai3",
    ]

    over_limit = load_config(
        make_config(
            channels=four_dam50_outputs,
            backend="nidaqmx",
            device="Dev1",
            sample_rate_hz=5001,
        )
    )
    with pytest.raises(RuntimeError, match="20 kS/s"):
        validate_hardware(over_limit, [device])

    invalid_diff_channel = load_config(
        make_config(
            channels=[
                {
                    "name": "Invalid_DIFF",
                    "role": "EEG",
                    "physical_channel": "ai4",
                    "enabled": True,
                    "amplifier_gain": 1000,
                }
            ],
            backend="nidaqmx",
            device="Dev1",
        )
    )
    with pytest.raises(RuntimeError, match=r"ai0\.\.ai3"):
        validate_hardware(invalid_diff_channel, [device])


def test_analog_start_matching_requires_named_sync_channel(
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        analog_start_match_enabled=True,
        analog_start_match_channel="Missing_SYNC",
    )

    with pytest.raises(ValueError, match="must name one enabled channel"):
        load_config(config_path)
