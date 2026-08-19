from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from eeg_emg_recording.diagnostics import analyze_session


def test_analysis_accepts_sample_first_storage_and_writes_traceable_outputs(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    sample_rate_hz = 1000.0
    time_s = np.arange(2000) / sample_rate_hz
    values = np.column_stack(
        (
            0.2 * np.sin(2 * np.pi * 50 * time_s),
            0.05 * np.sin(2 * np.pi * 50 * time_s + 0.2),
        )
    ).astype("<f4")
    with h5py.File(session / "signals.h5", "w") as handle:
        handle.create_dataset("/signals/output_voltage_v", data=values)
    metadata = {
        "sample_rate_hz": sample_rate_hz,
        "channels": [
            {"name": "EEG", "role": "EEG", "amplifier_gain": 1000, "color": "#20A4F3"},
            {"name": "EMG", "role": "EMG", "amplifier_gain": 1000, "color": "#F26430"},
        ],
        "amplifier_settings": [
            {"daq_input_range_v": [-10.0, 10.0]},
            {"daq_input_range_v": [-10.0, 10.0]},
        ],
        "files": {"dataset": "/signals/output_voltage_v"},
    }
    (session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    output = tmp_path / "analysis"

    report = analyze_session(session, output_dir=output, start_s=0.25, end_s=1.75)

    assert report["channels"][0]["samples"] == 1500
    assert report["parameters"]["start_s"] == 0.25
    assert report["parameters"]["end_s"] == 1.75
    assert report["channels"][0]["line_band_power_fraction_0p5_to_max"] > 0.8
    assert report["channels"][0]["warning_line_noise"] is True
    assert report["cross_channel_coherence"][0]["coherence_at_line_frequency"] > 0.99
    assert (output / "signal_quality.json").is_file()
    assert (output / "signal_quality.csv").is_file()
    assert (output / "waveforms_and_psd.png").is_file()
    assert (output / "waveform_zoom_1s.png").is_file()
    assert (output / "amplitude_distributions.png").is_file()
    assert (output / "cross_channel_coherence.png").is_file()
