from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Callable

import h5py
import numpy as np

from eeg_emg_recording.config import load_config
from eeg_emg_recording.models import DataBlock, SourceTiming
from eeg_emg_recording.recorder import Recorder
from eeg_emg_recording.recorder import run_recording
from eeg_emg_recording.storage import export_csv, inspect_session


def test_fake_recording_writes_incremental_hdf5(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    channels = [
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
        {
            "name": "Thermal_SYNC",
            "role": "SYNC",
            "physical_channel": "ai2",
            "enabled": True,
            "amplifier_gain": 1,
        },
    ]
    config_path = make_config(
        channels=channels,
        sample_rate_hz=240,
        block_duration_s=0.025,
        duration_s=0.1,
    )
    session_dir = tmp_path / "fake_hdf5_session"

    result = run_recording(
        config_path,
        session_dir=session_dir,
        preview=False,
    )

    assert result.status == "completed"
    assert result.stop_reason == "duration_complete"
    assert result.samples_written == 24
    with h5py.File(result.signals_path, "r") as handle:
        dataset = handle["/signals/output_voltage_v"]
        values = dataset[:]
        assert dataset.shape == (24, 3)
        assert dataset.dtype == np.dtype("<f4")
        assert handle.attrs["sample_rate_hz"] == 240
        assert handle.attrs["samples_written"] == 24
        assert handle.attrs["status"] == "completed"
    assert np.isfinite(values).all()
    assert np.ptp(values[:, 0]) > 0
    assert np.max(values[:, 2]) == 5.0

    metadata = inspect_session(session_dir)
    assert metadata["hdf5_shape"] == [24, 3]
    assert metadata["source"]["type"] == "fake"
    assert metadata["files"]["dataset"] == "/signals/output_voltage_v"
    assert (session_dir / "config_used.yaml").is_file()
    assert (session_dir / "block_index.csv").is_file()
    assert (session_dir / "events.csv").is_file()


def test_csv_export_preserves_sample_indices_and_channel_order(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        sample_rate_hz=100,
        block_duration_s=0.04,
        duration_s=0.12,
        csv_enabled=True,
        csv_decimation=4,
    )
    session_dir = tmp_path / "csv_session"
    result = run_recording(config_path, session_dir=session_dir, preview=False)

    online_csv = session_dir / "signals_decimated.csv"
    with online_csv.open("r", newline="", encoding="utf-8") as handle:
        online_rows = list(csv.reader(handle))
    assert online_rows[0] == [
        "sample_index",
        "time_s",
        "wall_time_ns",
        "Mouse01_EEG",
        "Mouse01_EMG",
    ]
    assert [int(row[0]) for row in online_rows[1:]] == [0, 4, 8]

    output = export_csv(result.session_dir, decimation=3, chunk_rows=12)
    with output.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == online_rows[0]
    assert [int(row[0]) for row in rows[1:]] == [0, 3, 6, 9]
    assert [float(row[1]) for row in rows[1:]] == [0.0, 0.03, 0.06, 0.09]

    metadata = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["samples_written"] == 12


def test_actual_source_rate_controls_duration_and_time_axis(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        sample_rate_hz=100,
        block_duration_s=0.04,
        duration_s=0.1,
    )
    config = load_config(config_path)

    class ActualRateSource:
        def __init__(self) -> None:
            self.timing = SourceTiming(1_000_000_000, 2_000_000_000, 80.0)

        def start(self) -> SourceTiming:
            return self.timing

        def read(self, samples_per_channel: int) -> DataBlock:
            values = np.ones((2, samples_per_channel), dtype=float)
            return DataBlock(values, 1_100_000_000, 2_100_000_000)

        def close(self) -> None:
            return

        def info(self) -> dict[str, str]:
            return {"type": "actual-rate-test"}

    session_dir = tmp_path / "actual_rate"
    result = Recorder(
        config,
        session_dir=session_dir,
        source=ActualRateSource(),
    ).run()

    assert result.samples_written == 8
    with h5py.File(result.signals_path, "r") as handle:
        assert handle.attrs["sample_rate_hz"] == 80.0
        assert handle.attrs["requested_sample_rate_hz"] == 100.0
        assert handle.attrs["committed_samples"] == 8
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["sample_rate_hz"] == 80.0
    assert metadata["duration_s"] == 0.1


def test_decimation_is_global_and_uncommitted_tail_is_ignored(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        sample_rate_hz=100,
        block_duration_s=0.03,
        duration_s=0.11,
        csv_enabled=True,
        csv_decimation=4,
    )
    session_dir = tmp_path / "global_decimation"
    result = run_recording(config_path, session_dir=session_dir, preview=False)

    with (session_dir / "signals_decimated.csv").open(
        "r", newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.reader(handle))
    assert [int(row[0]) for row in rows[1:]] == [0, 4, 8]

    with h5py.File(result.signals_path, "r+") as handle:
        dataset = handle["/signals/output_voltage_v"]
        dataset.resize((15, dataset.shape[1]))
        dataset[11:15, :] = 0.0
        handle.flush()

    inspected = inspect_session(session_dir)
    assert inspected["hdf5_shape"] == [11, 2]
    assert inspected["hdf5_stored_shape"] == [15, 2]
    output = export_csv(session_dir, decimation=4, chunk_rows=5)
    with output.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert [int(row[0]) for row in rows[1:]] == [0, 4, 8]


def test_stop_while_waiting_for_hardware_trigger_closes_cleanly(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config_path = make_config(
        duration_s=None,
        start_trigger_enabled=True,
        start_trigger_source="/Dev1/PFI0",
    )
    config = load_config(config_path)
    stop_event = threading.Event()
    stop_event.set()

    class WaitingForTriggerSource:
        def __init__(self) -> None:
            self.timing = SourceTiming(1_000_000_000, 2_000_000_000, 100.0)
            self.closed = False

        def start(self) -> SourceTiming:
            return self.timing

        def read(self, samples_per_channel: int) -> DataBlock:
            time.sleep(0.01)
            return DataBlock(
                np.empty((2, 0), dtype=float),
                time.time_ns(),
                time.perf_counter_ns(),
            )

        def close(self) -> None:
            self.closed = True

        def info(self) -> dict[str, str]:
            return {"type": "waiting-for-trigger-test"}

    source = WaitingForTriggerSource()
    result = Recorder(
        config,
        session_dir=tmp_path / "trigger_cancel",
        stop_event=stop_event,
        source=source,
    ).run()

    assert result.status == "stopped"
    assert result.samples_written == 0
    assert source.closed is True
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "stopped"
    with (result.session_dir / "events.csv").open(
        "r", newline="", encoding="utf-8"
    ) as handle:
        event_names = {row["event_name"] for row in csv.DictReader(handle)}
    assert "hardware_trigger_wait_cancelled" in event_names


def test_storage_close_failure_marks_session_failed(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    config = load_config(
        make_config(
            sample_rate_hz=100,
            block_duration_s=0.02,
            duration_s=0.04,
        )
    )
    recorder = Recorder(config, session_dir=tmp_path / "close_failure")
    close_resources = recorder.writer._close_resources

    def fail_after_close() -> list[str]:
        return [*close_resources(), "simulated close failure"]

    recorder.writer._close_resources = fail_after_close
    result = recorder.run()

    assert result.status == "failed"
    assert result.stop_reason == "writer_close_failed"
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    assert metadata["status"] == "failed"
    assert metadata["stop_reason"] == "storage_close_failed"
    complete = json.loads(
        (result.session_dir / "control" / "complete.json").read_text(encoding="utf-8")
    )
    assert complete["state"] == "failed"
