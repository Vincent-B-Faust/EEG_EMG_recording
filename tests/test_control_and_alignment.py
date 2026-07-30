from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from eeg_emg_recording.alignment import align_to_thermal_session
from eeg_emg_recording.control import publish_event, request_stop
from eeg_emg_recording.recorder import RunResult, run_recording


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out while waiting for recorder state")


def test_external_control_event_and_stop_are_persisted(
    tmp_path: Path,
    make_config: Callable[..., Path],
) -> None:
    control_dir = tmp_path / "shared_control"
    session_dir = tmp_path / "controlled_session"
    config_path = make_config(
        duration_s=None,
        block_duration_s=0.01,
        control_dir=control_dir,
    )
    results: list[RunResult] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(
                run_recording(
                    config_path,
                    session_dir=session_dir,
                    preview=False,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="pytest-eeg-recorder", daemon=True)
    thread.start()
    _wait_until(lambda: (control_dir / "ready.json").is_file())

    publish_event(
        control_dir,
        "thermal_experiment_start",
        source="thermocamera",
        phase="formal_recording",
    )
    _wait_until(
        lambda: (session_dir / "events.csv").is_file()
        and "thermal_experiment_start"
        in (session_dir / "events.csv").read_text(encoding="utf-8")
    )
    request_stop(control_dir, reason="pytest_external_stop")
    thread.join(timeout=5.0)

    assert not thread.is_alive()
    assert errors == []
    assert len(results) == 1
    result = results[0]
    assert result.status == "stopped"
    assert result.stop_reason == "pytest_external_stop"
    assert result.samples_written > 0

    with (session_dir / "events.csv").open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    thermal_event = next(
        row for row in rows if row["event_name"] == "thermal_experiment_start"
    )
    assert thermal_event["source"] == "thermocamera"
    assert int(thermal_event["sample_index"]) >= 0
    assert any(row["event_name"] == "stop_requested" for row in rows)

    complete = json.loads((control_dir / "complete.json").read_text(encoding="utf-8"))
    assert complete["state"] == "stopped"
    assert complete["stop_reason"] == "pytest_external_stop"


def test_alignment_prefers_shared_control_event(tmp_path: Path) -> None:
    eeg_session = tmp_path / "eeg_session"
    thermal_session = tmp_path / "thermal_session"
    eeg_session.mkdir()
    thermal_session.mkdir()
    start_wall_ns = 1_800_000_000_000_000_000
    event_wall_ns = start_wall_ns + 250_000_000
    (eeg_session / "metadata.json").write_text(
        json.dumps(
            {
                "sample_rate_hz": 1000,
                "start_wall_ns": start_wall_ns,
                "parent_session_dir": str(thermal_session),
            }
        ),
        encoding="utf-8",
    )
    with (eeg_session / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "event_name",
                "source",
                "sample_index",
                "wall_time_ns",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "event_name": "thermal_experiment_start",
                "source": "thermocamera",
                "sample_index": 250,
                "wall_time_ns": event_wall_ns,
            }
        )

    output = align_to_thermal_session(eeg_session)
    alignment = json.loads(output.read_text(encoding="utf-8"))

    assert alignment["method"] == "shared_monotonic_control_event"
    assert alignment["thermal_experiment_start_sample_index"] == 250
    assert alignment["thermal_experiment_start_eeg_time_s"] == pytest.approx(0.25)
    # The current alignment API parses CSV timestamps through float, so accept
    # sub-microsecond quantization while requiring the sample mapping to be exact.
    assert abs(alignment["thermal_experiment_start_wall_ns"] - event_wall_ns) < 1000
    assert alignment["formula"] == "thermal_time_s = (sample_index - 250) / 1000"


def test_alignment_falls_back_to_thermal_wall_clock(tmp_path: Path) -> None:
    eeg_session = tmp_path / "eeg_wall_fallback"
    thermal_session = tmp_path / "thermal_wall_fallback"
    eeg_session.mkdir()
    thermal_session.mkdir()
    start_wall_ns = 1_800_000_000_000_000_000
    thermal_start_wall_ns = start_wall_ns + 125_000_000
    (eeg_session / "metadata.json").write_text(
        json.dumps(
            {
                "sample_rate_hz": 2000,
                "start_wall_ns": start_wall_ns,
                "parent_session_dir": str(thermal_session),
            }
        ),
        encoding="utf-8",
    )
    (eeg_session / "events.csv").write_text(
        "event_name,source,sample_index,wall_time_ns\n",
        encoding="utf-8",
    )
    (thermal_session / "recording_metadata.json").write_text(
        json.dumps({"experiment_start_wall_ns": thermal_start_wall_ns}),
        encoding="utf-8",
    )

    output = align_to_thermal_session(eeg_session)
    alignment = json.loads(output.read_text(encoding="utf-8"))

    assert alignment["method"] == "wall_clock_metadata"
    assert alignment["thermal_experiment_start_sample_index"] == 250
    assert alignment["thermal_experiment_start_eeg_time_s"] == pytest.approx(0.125)


def test_alignment_uses_only_configured_sync_channel(tmp_path: Path) -> None:
    eeg_session = tmp_path / "eeg_dedicated_sync"
    eeg_session.mkdir()
    (eeg_session / "metadata.json").write_text(
        json.dumps(
            {
                "sample_rate_hz": 1000,
                "start_wall_ns": 1_800_000_000_000_000_000,
                "sync": {
                    "analog_start_match_enabled": True,
                    "analog_start_match_channel": "Thermal_SYNC",
                    "analog_start_match_window_s": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    fields = [
        "event_name",
        "source",
        "sample_index",
        "wall_time_ns",
        "details_json",
    ]
    with (eeg_session / "events.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "event_name": "thermal_experiment_start",
                "source": "thermocamera",
                "sample_index": 100,
                "wall_time_ns": 1_800_000_000_100_000_000,
                "details_json": "{}",
            }
        )
        writer.writerow(
            {
                "event_name": "thermal_hardware_sync_pulse",
                "source": "thermocamera",
                "sample_index": 101,
                "wall_time_ns": 1_800_000_000_101_000_000,
                "details_json": "{}",
            }
        )
        writer.writerow(
            {
                "event_name": "rising_edge",
                "source": "analog_sync",
                "sample_index": 101,
                "wall_time_ns": "",
                "details_json": json.dumps({"channel": "Unrelated_SYNC"}),
            }
        )
        writer.writerow(
            {
                "event_name": "rising_edge",
                "source": "analog_sync",
                "sample_index": 104,
                "wall_time_ns": "",
                "details_json": json.dumps({"channel": "Thermal_SYNC"}),
            }
        )

    output = align_to_thermal_session(eeg_session)
    alignment = json.loads(output.read_text(encoding="utf-8"))

    assert alignment["method"] == "hardware_sync_pulse_corrected"
    assert alignment["thermal_experiment_start_sample_index"] == 103
    assert json.loads(
        alignment["matched_analog_sync_event"]["details_json"]
    )["channel"] == "Thermal_SYNC"
