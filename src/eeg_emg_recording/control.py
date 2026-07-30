from __future__ import annotations

import json
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Any


class ControlDirectory:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser().resolve() if path else None
        self.events_dir = self.path / "events" if self.path else None
        self._seen_events: set[str] = set()
        self.run_id = uuid.uuid4().hex

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def initialize(self) -> None:
        if not self.enabled:
            return
        assert self.path is not None and self.events_dir is not None
        if self.events_dir.exists() and any(self.events_dir.iterdir()):
            archive = self.path / f"events_archive_{time.time_ns()}"
            os.replace(self.events_dir, archive)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        for name in ("ready.json", "complete.json", "stop.request.json"):
            try:
                (self.path / name).unlink()
            except FileNotFoundError:
                pass
        self.write_status("initializing")

    def write_status(self, state: str, **payload: Any) -> None:
        if not self.enabled:
            return
        assert self.path is not None
        data = {
            "protocol": "aki_multi_recording_v1",
            "state": state,
            "wall_time_ns": time.time_ns(),
            "monotonic_ns": time.perf_counter_ns(),
            "pid": os.getpid(),
            "run_id": self.run_id,
            "clock_host": platform.node(),
            **payload,
        }
        atomic_write_json(self.path / "status.json", data)

    def mark_ready(self, **payload: Any) -> None:
        if not self.enabled:
            return
        assert self.path is not None
        data = {
            "protocol": "aki_multi_recording_v1",
            "state": "ready",
            "wall_time_ns": time.time_ns(),
            "monotonic_ns": time.perf_counter_ns(),
            "pid": os.getpid(),
            "run_id": self.run_id,
            "clock_host": platform.node(),
            **payload,
        }
        atomic_write_json(self.path / "ready.json", data)
        self.write_status("recording", **payload)

    def mark_complete(self, state: str, **payload: Any) -> None:
        if not self.enabled:
            return
        assert self.path is not None
        data = {
            "protocol": "aki_multi_recording_v1",
            "state": state,
            "wall_time_ns": time.time_ns(),
            "monotonic_ns": time.perf_counter_ns(),
            "pid": os.getpid(),
            "run_id": self.run_id,
            "clock_host": platform.node(),
            **payload,
        }
        atomic_write_json(self.path / "complete.json", data)
        self.write_status(state, **payload)

    def stop_request(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        assert self.path is not None
        return read_json_if_exists(self.path / "stop.request.json")

    def poll_events(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        assert self.events_dir is not None
        result: list[dict[str, Any]] = []
        for path in sorted(self.events_dir.glob("event_*.json")):
            key = path.name.casefold()
            if key in self._seen_events:
                continue
            try:
                payload = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            self._seen_events.add(key)
            payload["_event_file"] = path.name
            result.append(payload)
        return result


def publish_event(
    control_dir: str | Path,
    event_name: str,
    *,
    source: str,
    wall_time_ns: int | None = None,
    monotonic_ns: int | None = None,
    **details: Any,
) -> Path:
    root = Path(control_dir).expanduser().resolve()
    events_dir = root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    wall_time_ns = int(wall_time_ns if wall_time_ns is not None else time.time_ns())
    monotonic_ns = int(monotonic_ns if monotonic_ns is not None else time.perf_counter_ns())
    payload = {
        "protocol": "aki_multi_recording_v1",
        "event_name": str(event_name),
        "source": str(source),
        "wall_time_ns": wall_time_ns,
        "monotonic_ns": monotonic_ns,
        "clock_host": platform.node(),
        "details": details,
    }
    filename = f"event_{wall_time_ns}_{uuid.uuid4().hex[:10]}.json"
    path = events_dir / filename
    atomic_write_json(path, payload)
    return path


def request_stop(control_dir: str | Path, *, reason: str = "external_request", **details: Any) -> Path:
    root = Path(control_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "stop.request.json"
    atomic_write_json(
        path,
        {
            "protocol": "aki_multi_recording_v1",
            "reason": reason,
            "wall_time_ns": time.time_ns(),
            "monotonic_ns": time.perf_counter_ns(),
            "details": details,
        },
    )
    return path


def atomic_write_json(path: str | Path, data: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, default=_json_default)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, output)
    return output


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def read_json_if_exists(path: str | Path) -> dict[str, Any] | None:
    try:
        return read_json(path)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    return str(value)
