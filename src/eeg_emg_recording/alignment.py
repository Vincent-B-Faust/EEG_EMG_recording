from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .control import atomic_write_json, read_json


def align_to_thermal_session(
    eeg_session_dir: str | Path,
    thermal_session_dir: str | Path | None = None,
    *,
    output: str | Path | None = None,
) -> Path:
    eeg_session = Path(eeg_session_dir).expanduser().resolve()
    metadata = read_json(eeg_session / "metadata.json")
    thermal_session = (
        Path(thermal_session_dir).expanduser().resolve()
        if thermal_session_dir is not None
        else _parent_session_from_metadata(metadata)
    )
    sample_rate = float(metadata["sample_rate_hz"])
    events = _load_events(eeg_session / "events.csv")
    start_event = _find_event(events, "thermal_experiment_start")
    pulse_event = _find_event(events, "thermal_hardware_sync_pulse")
    thermal_metadata = _load_thermal_metadata(thermal_session) if thermal_session else {}
    sync_metadata = metadata.get("sync") if isinstance(metadata.get("sync"), dict) else {}
    software_start_sample: int | None = None
    matched_sync_event: dict[str, str] | None = None
    if start_event is not None and start_event.get("sample_index") not in (None, ""):
        start_sample = int(start_event["sample_index"])
        software_start_sample = start_sample
        thermal_start_wall_ns = _optional_int(start_event.get("wall_time_ns"))
        method = "shared_monotonic_control_event"
        sync_edges = [
            row
            for row in events
            if row.get("source") == "analog_sync"
            and row.get("event_name") in {"rising_edge", "falling_edge"}
            and row.get("sample_index") not in (None, "")
            and _event_channel(row).casefold()
            == str(sync_metadata.get("analog_start_match_channel", "")).casefold()
        ]
        reference_event = None
        if bool(sync_metadata.get("analog_start_match_enabled", False)):
            reference_event = (
                pulse_event
                if pulse_event is not None and pulse_event.get("sample_index") not in (None, "")
                else start_event
            )
        if sync_edges and reference_event is not None:
            reference_sample = int(reference_event["sample_index"])
            candidate = min(
                sync_edges,
                key=lambda row: abs(int(row["sample_index"]) - reference_sample),
            )
            distance_samples = abs(int(candidate["sample_index"]) - reference_sample)
            match_window_s = float(sync_metadata.get("analog_start_match_window_s", 1.0))
            if distance_samples <= int(round(sample_rate * match_window_s)):
                matched_sync_event = candidate
                correction_samples = int(candidate["sample_index"]) - reference_sample
                start_sample = software_start_sample + correction_samples
                method = (
                    "hardware_sync_pulse_corrected"
                    if reference_event is pulse_event
                    else "analog_sync_edge_near_software_event"
                )
    else:
        thermal_start_wall_ns = _thermal_start_wall_ns(thermal_metadata)
        if thermal_start_wall_ns is None:
            raise FileNotFoundError(
                "No thermal_experiment_start event or thermal recording start timestamp was found."
            )
        start_sample = int(
            round(
                (thermal_start_wall_ns - int(metadata["start_wall_ns"]))
                * sample_rate
                / 1_000_000_000
            )
        )
        method = "wall_clock_metadata"
    result = {
        "schema": "aki_eeg_emg_thermal_alignment_v1",
        "method": method,
        "eeg_session_dir": str(eeg_session),
        "thermal_session_dir": str(thermal_session) if thermal_session else None,
        "sample_rate_hz": sample_rate,
        "thermal_experiment_start_sample_index": start_sample,
        "software_event_sample_index": software_start_sample,
        "thermal_experiment_start_eeg_time_s": start_sample / sample_rate,
        "thermal_experiment_start_wall_ns": thermal_start_wall_ns,
        "formula": f"thermal_time_s = (sample_index - {start_sample}) / {sample_rate:g}",
        "quality": (
            "Dedicated analog SYNC edge matched to the same-computer software start event."
            if method in {"analog_sync_edge_near_software_event", "hardware_sync_pulse_corrected"}
            else "Same-computer monotonic timestamp mapped to the NI sample timeline."
            if method == "shared_monotonic_control_event"
            else "Wall-clock fallback; use a wired TTL sync channel for sub-millisecond alignment."
        ),
        "matched_analog_sync_event": matched_sync_event,
    }
    output_path = (
        Path(output).expanduser().resolve()
        if output is not None
        else eeg_session / "thermal_alignment.json"
    )
    atomic_write_json(output_path, result)
    return output_path


def _load_events(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _find_event(events: list[dict[str, str]], event_name: str) -> dict[str, str] | None:
    for row in events:
        if str(row.get("event_name", "")).strip() == event_name:
            return row
    return None


def _event_channel(event: dict[str, str]) -> str:
    try:
        details = json.loads(event.get("details_json", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(details, dict):
        return ""
    return str(details.get("channel", "")).strip()


def _parent_session_from_metadata(metadata: dict[str, Any]) -> Path | None:
    value = metadata.get("parent_session_dir")
    if value in (None, "", "None"):
        return None
    return Path(str(value)).expanduser().resolve()


def _load_thermal_metadata(session_dir: Path | None) -> dict[str, Any]:
    if session_dir is None:
        return {}
    candidates = [
        session_dir / "recording_metadata.json",
        *sorted(session_dir.glob("*recording_metadata.json")),
        session_dir / "session_info.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            return read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return {}


def _thermal_start_wall_ns(metadata: dict[str, Any]) -> int | None:
    for key in ("experiment_start_wall_ns", "record_start_wall_ns"):
        value = _optional_int(metadata.get(key))
        if value is not None:
            return value
    for key in ("experiment_start_wall", "record_start_wall", "start_wall"):
        value = metadata.get(key)
        if value not in (None, ""):
            return int(round(float(value) * 1_000_000_000))
    return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        return int(float(text))
