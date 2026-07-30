from __future__ import annotations

import csv
import json
import math
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import h5py
import numpy as np
import yaml

from .control import atomic_write_json, read_json
from .models import ChannelConfig, RecordingConfig, SourceTiming


SCHEMA = "aki_eeg_emg_recording_v1"


class StorageCloseError(RuntimeError):
    pass


class SessionWriter:
    def __init__(self, session_dir: str | Path, config: RecordingConfig):
        self.session_dir = Path(session_dir).expanduser().resolve()
        self.config = config
        self.channels = config.acquisition.active_channels
        self.sample_rate_hz = config.acquisition.sample_rate_hz
        self.h5_path = self.session_dir / "signals.h5"
        self.block_index_path = self.session_dir / "block_index.csv"
        self.events_path = self.session_dir / "events.csv"
        self.metadata_path = self.session_dir / "metadata.json"
        self.csv_path = self.session_dir / "signals_decimated.csv" if config.storage.csv_enabled else None
        self.samples_written = 0
        self.blocks_written = 0
        self.timing = SourceTiming(0, 0, self.sample_rate_hz)
        self._h5: h5py.File | None = None
        self._signals: h5py.Dataset | None = None
        self._block_handle: Any | None = None
        self._block_writer: csv.DictWriter[str] | None = None
        self._event_handle: Any | None = None
        self._event_writer: csv.DictWriter[str] | None = None
        self._csv_handle: Any | None = None
        self._csv_writer: csv.writer | None = None
        self._last_flush_monotonic = time.monotonic()
        self._metadata: dict[str, Any] = {}
        self._closed = False

    def open(self, timing: SourceTiming, source_info: dict[str, Any]) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.timing = timing
        self.sample_rate_hz = float(timing.sample_rate_hz)
        try:
            self._write_config_snapshot()
            chunk_rows = max(
                self.config.acquisition.block_size,
                min(int(round(self.sample_rate_hz * 10.0)), 100_000),
            )
            self._h5 = h5py.File(self.h5_path, "w", libver="latest")
            signal_group = self._h5.create_group("signals")
            self._signals = signal_group.create_dataset(
                "output_voltage_v",
                shape=(0, len(self.channels)),
                maxshape=(None, len(self.channels)),
                chunks=(chunk_rows, len(self.channels)),
                dtype="<f4",
                compression=self.config.storage.hdf5_compression,
                shuffle=bool(self.config.storage.hdf5_compression),
            )
            self._h5.attrs["schema"] = SCHEMA
            self._h5.attrs["sample_rate_hz"] = self.sample_rate_hz
            self._h5.attrs["requested_sample_rate_hz"] = self.config.acquisition.sample_rate_hz
            self._h5.attrs["start_wall_ns"] = timing.start_wall_ns
            self._h5.attrs["start_monotonic_ns"] = timing.start_monotonic_ns
            self._h5.attrs["channels_json"] = json.dumps([channel.to_dict() for channel in self.channels], ensure_ascii=False)
            self._h5.attrs["samples_written"] = 0
            self._h5.attrs["committed_samples"] = 0
            self._open_block_index()
            self._open_events()
            self._open_decimated_csv()
            self._metadata = {
            "schema": SCHEMA,
            "status": "recording",
            "session_dir": str(self.session_dir),
            "start_wall_ns": timing.start_wall_ns,
            "start_wall": timing.start_wall_ns / 1_000_000_000,
            "start_time": _format_ns(timing.start_wall_ns),
            "start_monotonic_ns": timing.start_monotonic_ns,
            "sample_rate_hz": self.sample_rate_hz,
            "block_size": self.config.acquisition.block_size,
            "channels": [channel.to_dict() for channel in self.channels],
            "source": source_info,
            "files": {
                "signals_hdf5": self.h5_path.name,
                "dataset": "/signals/output_voltage_v",
                "block_index": self.block_index_path.name,
                "events": self.events_path.name,
                "decimated_csv": self.csv_path.name if self.csv_path else None,
                "config": "config_used.yaml",
            },
            "samples_written": 0,
            "blocks_written": 0,
            "time_mapping": {
                "sample_time_s": "sample_index / sample_rate_hz",
                "wall_time_ns": "start_wall_ns + round(sample_index * 1e9 / sample_rate_hz)",
                "amplifier_input_uv": "output_voltage_v * input_uv_per_output_v",
            },
            "parent_session_dir": str(self.config.sync.parent_session_dir) if self.config.sync.parent_session_dir else None,
            "control_dir": str(self.config.sync.control_dir) if self.config.sync.control_dir else None,
            "sync": {
                "analog_threshold_v": self.config.sync.analog_threshold_v,
                "analog_edge": self.config.sync.analog_edge,
                "analog_start_match_enabled": self.config.sync.analog_start_match_enabled,
                "analog_start_match_channel": self.config.sync.analog_start_match_channel,
                "analog_start_match_window_s": self.config.sync.analog_start_match_window_s,
            },
        }
            self.flush(force=True)
        except Exception:
            self._close_resources()
            raise

    def set_sample_origin(self, timing: SourceTiming) -> None:
        self.timing = timing
        if self._h5 is not None:
            self._h5.attrs["start_wall_ns"] = timing.start_wall_ns
            self._h5.attrs["start_monotonic_ns"] = timing.start_monotonic_ns
        self._metadata["start_wall_ns"] = timing.start_wall_ns
        self._metadata["start_wall"] = timing.start_wall_ns / 1_000_000_000
        self._metadata["start_time"] = _format_ns(timing.start_wall_ns)
        self._metadata["start_monotonic_ns"] = timing.start_monotonic_ns

    def set_signal_quality(self, summary: dict[str, Any]) -> None:
        self._metadata["signal_quality"] = summary

    def write_block(
        self,
        block: np.ndarray,
        *,
        read_end_wall_ns: int,
        read_end_monotonic_ns: int,
    ) -> tuple[int, int]:
        if self._signals is None:
            raise RuntimeError("SessionWriter is not open")
        values = np.asarray(block, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] != len(self.channels):
            raise ValueError(
                f"Expected block shape ({len(self.channels)}, samples), got {values.shape}"
            )
        row_values = values.T
        start = self.samples_written
        end = start + row_values.shape[0]
        self._signals.resize((end, len(self.channels)))
        self._signals[start:end, :] = row_values
        block_start_wall_ns = self.sample_wall_ns(start)
        block_end_wall_ns = self.sample_wall_ns(end)
        assert self._block_writer is not None
        self._block_writer.writerow(
            {
                "block_index": self.blocks_written,
                "sample_start": start,
                "sample_end_exclusive": end,
                "sample_count": end - start,
                "block_start_time_s": start / self.sample_rate_hz,
                "block_start_wall_ns": block_start_wall_ns,
                "block_end_wall_ns": block_end_wall_ns,
                "read_end_wall_ns": int(read_end_wall_ns),
                "read_end_monotonic_ns": int(read_end_monotonic_ns),
            }
        )
        self._write_decimated_rows(row_values, start)
        self.samples_written = end
        self.blocks_written += 1
        if self._h5 is not None:
            self._h5.attrs["samples_written"] = self.samples_written
            self._h5.attrs["blocks_written"] = self.blocks_written
        self.flush()
        return start, end

    def record_event(
        self,
        event_name: str,
        *,
        source: str,
        sample_index: int | None = None,
        wall_time_ns: int | None = None,
        monotonic_ns: int | None = None,
        value: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if sample_index is None and monotonic_ns is not None:
            sample_index = self.sample_index_for_monotonic(monotonic_ns)
        if wall_time_ns is None and sample_index is not None:
            wall_time_ns = self.sample_wall_ns(sample_index)
        assert self._event_writer is not None
        self._event_writer.writerow(
            {
                "event_name": event_name,
                "source": source,
                "sample_index": "" if sample_index is None else int(sample_index),
                "time_s": "" if sample_index is None else float(sample_index) / self.sample_rate_hz,
                "wall_time_ns": "" if wall_time_ns is None else int(wall_time_ns),
                "wall_time": "" if wall_time_ns is None else _format_ns(int(wall_time_ns)),
                "monotonic_ns": "" if monotonic_ns is None else int(monotonic_ns),
                "value": "" if value is None else float(value),
                "details_json": json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            }
        )
        if self._event_handle is not None:
            self._event_handle.flush()

    def sample_index_for_monotonic(self, monotonic_ns: int) -> int:
        delta_ns = int(monotonic_ns) - self.timing.start_monotonic_ns
        return max(0, int(round(delta_ns * self.sample_rate_hz / 1_000_000_000)))

    def sample_index_for_wall(self, wall_time_ns: int) -> int:
        delta_ns = int(wall_time_ns) - self.timing.start_wall_ns
        return max(0, int(round(delta_ns * self.sample_rate_hz / 1_000_000_000)))

    def sample_wall_ns(self, sample_index: int) -> int:
        return self.timing.start_wall_ns + int(round(int(sample_index) * 1_000_000_000 / self.sample_rate_hz))

    def flush(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_flush_monotonic < self.config.storage.flush_interval_s:
            return
        if self._h5 is not None:
            self._h5.flush()
        for handle in (self._block_handle, self._event_handle, self._csv_handle):
            if handle is not None:
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
        if self._h5 is not None:
            self._h5.attrs["committed_samples"] = self.samples_written
            self._h5.flush()
        self._metadata["samples_written"] = self.samples_written
        self._metadata["blocks_written"] = self.blocks_written
        self._metadata["elapsed_s"] = self.samples_written / self.sample_rate_hz
        self._metadata["last_update_wall_ns"] = time.time_ns()
        atomic_write_json(self.metadata_path, self._metadata)
        self._last_flush_monotonic = now

    def close(
        self,
        *,
        status: str,
        stop_reason: str,
        exception: dict[str, Any] | None = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        requested_status = status
        requested_stop_reason = stop_reason
        end_wall_ns = time.time_ns()
        self._metadata.update(
            {
                "status": status,
                "stop_reason": stop_reason,
                "end_wall_ns": end_wall_ns,
                "end_wall": end_wall_ns / 1_000_000_000,
                "end_time": _format_ns(end_wall_ns),
                "samples_written": self.samples_written,
                "blocks_written": self.blocks_written,
                "duration_s": self.samples_written / self.sample_rate_hz,
                "runtime_s": (
                    end_wall_ns - int(self._metadata.get("start_wall_ns", end_wall_ns))
                )
                / 1_000_000_000,
                "exception": exception,
            }
        )
        close_errors: list[str] = []
        if self._h5 is not None:
            try:
                self._h5.attrs["status"] = status
                self._h5.attrs["stop_reason"] = stop_reason
                self._h5.attrs["samples_written"] = self.samples_written
                self._h5.attrs["committed_samples"] = self.samples_written
                self._h5.attrs["end_wall_ns"] = end_wall_ns
                self._h5.flush()
            except Exception as exc:
                close_errors.append(f"HDF5 final flush: {type(exc).__name__}: {exc}")
        close_errors.extend(self._close_resources())
        if close_errors:
            message = "; ".join(close_errors)
            self._metadata.update(
                {
                    "status": "failed",
                    "stop_reason": "storage_close_failed",
                    "exception": {
                        "type": "StorageCloseError",
                        "message": message,
                        "requested_status": requested_status,
                        "requested_stop_reason": requested_stop_reason,
                    },
                }
            )
        try:
            atomic_write_json(self.metadata_path, self._metadata)
        except Exception as exc:
            close_errors.append(f"metadata final write: {type(exc).__name__}: {exc}")
        if close_errors:
            raise StorageCloseError("; ".join(close_errors))

    def _close_resources(self) -> list[str]:
        errors: list[str] = []
        for handle in (self._block_handle, self._event_handle, self._csv_handle):
            if handle is not None:
                try:
                    handle.flush()
                except Exception as exc:
                    errors.append(f"sidecar flush: {type(exc).__name__}: {exc}")
                try:
                    handle.close()
                except Exception as exc:
                    errors.append(f"sidecar close: {type(exc).__name__}: {exc}")
        self._block_handle = None
        self._event_handle = None
        self._csv_handle = None
        if self._h5 is not None:
            try:
                self._h5.close()
            except Exception as exc:
                errors.append(f"HDF5 close: {type(exc).__name__}: {exc}")
            self._h5 = None
            self._signals = None
        return errors

    def _write_config_snapshot(self) -> None:
        path = self.session_dir / "config_used.yaml"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(self.config.raw, handle, sort_keys=False, allow_unicode=True)

    def _open_block_index(self) -> None:
        fields = [
            "block_index",
            "sample_start",
            "sample_end_exclusive",
            "sample_count",
            "block_start_time_s",
            "block_start_wall_ns",
            "block_end_wall_ns",
            "read_end_wall_ns",
            "read_end_monotonic_ns",
        ]
        self._block_handle = self.block_index_path.open("w", newline="", encoding="utf-8")
        self._block_writer = csv.DictWriter(self._block_handle, fieldnames=fields)
        self._block_writer.writeheader()

    def _open_events(self) -> None:
        fields = [
            "event_name",
            "source",
            "sample_index",
            "time_s",
            "wall_time_ns",
            "wall_time",
            "monotonic_ns",
            "value",
            "details_json",
        ]
        self._event_handle = self.events_path.open("w", newline="", encoding="utf-8")
        self._event_writer = csv.DictWriter(self._event_handle, fieldnames=fields)
        self._event_writer.writeheader()

    def _open_decimated_csv(self) -> None:
        if self.csv_path is None:
            return
        self._csv_handle = self.csv_path.open("w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_handle)
        self._csv_writer.writerow(
            ["sample_index", "time_s", "wall_time_ns", *[channel.name for channel in self.channels]]
        )

    def _write_decimated_rows(self, row_values: np.ndarray, start_index: int) -> None:
        if self._csv_writer is None:
            return
        stride = self.config.storage.csv_decimation
        end_index = start_index + row_values.shape[0]
        first_sample = start_index + ((-start_index) % stride)
        for sample_index in range(first_sample, end_index, stride):
            local_index = sample_index - start_index
            self._csv_writer.writerow(
                [
                    sample_index,
                    sample_index / self.sample_rate_hz,
                    self.sample_wall_ns(sample_index),
                    *[float(item) for item in row_values[local_index]],
                ]
            )


def create_session_dir(config: RecordingConfig, exact_path: str | Path | None = None) -> Path:
    if exact_path is not None:
        path = Path(exact_path).expanduser().resolve()
        if path.exists() and any(path.iterdir()):
            raise FileExistsError(f"Session directory is not empty: {path}")
        path.mkdir(parents=True, exist_ok=True)
        return path
    root = config.output_root
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    subject = "-".join(config.mouse_ids) if config.mouse_ids else config.session_name
    slug = _slug(subject)[:60] or "sleep_recording"
    for _ in range(20):
        path = root / f"session_{timestamp}_{slug}_{uuid.uuid4().hex[:8]}"
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"Could not create a unique session directory under {root}")


def inspect_session(session_dir: str | Path) -> dict[str, Any]:
    session = Path(session_dir).expanduser().resolve()
    metadata = read_json(session / "metadata.json")
    h5_path = session / str(metadata.get("files", {}).get("signals_hdf5", "signals.h5"))
    with h5py.File(h5_path, "r") as handle:
        dataset = handle["/signals/output_voltage_v"]
        committed = _committed_rows(handle, dataset, metadata)
        metadata["hdf5_shape"] = [committed, dataset.shape[1]]
        metadata["hdf5_stored_shape"] = list(dataset.shape)
        metadata["committed_samples"] = committed
        metadata["hdf5_dtype"] = str(dataset.dtype)
    return metadata


def export_csv(
    session_dir: str | Path,
    output: str | Path | None = None,
    *,
    decimation: int = 1,
    chunk_rows: int = 100_000,
) -> Path:
    session = Path(session_dir).expanduser().resolve()
    metadata = read_json(session / "metadata.json")
    channels = metadata.get("channels", [])
    names = [str(item.get("name", f"ch{index + 1}")) for index, item in enumerate(channels)]
    sample_rate = float(metadata["sample_rate_hz"])
    start_wall_ns = int(metadata["start_wall_ns"])
    output_path = Path(output).expanduser().resolve() if output else session / "signals_export.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    decimation = max(1, int(decimation))
    h5_path = session / str(metadata.get("files", {}).get("signals_hdf5", "signals.h5"))
    with h5py.File(h5_path, "r") as handle, output_path.open("w", newline="", encoding="utf-8") as csv_handle:
        dataset = handle["/signals/output_voltage_v"]
        committed = _committed_rows(handle, dataset, metadata)
        writer = csv.writer(csv_handle)
        writer.writerow(["sample_index", "time_s", "wall_time_ns", *names])
        for block_start in range(0, committed, chunk_rows):
            block_end = min(committed, block_start + chunk_rows)
            values = dataset[block_start:block_end, :]
            first_sample = block_start + ((-block_start) % decimation)
            for sample_index in range(first_sample, block_end, decimation):
                local_index = sample_index - block_start
                wall_ns = start_wall_ns + int(round(sample_index * 1_000_000_000 / sample_rate))
                writer.writerow(
                    [
                        sample_index,
                        sample_index / sample_rate,
                        wall_ns,
                        *[float(item) for item in values[local_index]],
                    ]
                )
    return output_path


def iter_voltage_blocks(session_dir: str | Path, *, chunk_rows: int = 100_000) -> Iterable[np.ndarray]:
    session = Path(session_dir).expanduser().resolve()
    metadata = read_json(session / "metadata.json")
    h5_path = session / str(metadata.get("files", {}).get("signals_hdf5", "signals.h5"))
    with h5py.File(h5_path, "r") as handle:
        dataset = handle["/signals/output_voltage_v"]
        committed = _committed_rows(handle, dataset, metadata)
        for start in range(0, committed, chunk_rows):
            yield np.asarray(dataset[start : min(start + chunk_rows, committed), :])


def _committed_rows(
    handle: h5py.File,
    dataset: h5py.Dataset,
    metadata: dict[str, Any],
) -> int:
    value = handle.attrs.get(
        "committed_samples",
        metadata.get("samples_written", dataset.shape[0]),
    )
    return max(0, min(int(value), int(dataset.shape[0])))


def _slug(value: str) -> str:
    result = []
    for char in str(value).strip():
        if char.isalnum() or char in {"-", "_"}:
            result.append(char)
        elif char.isspace():
            result.append("_")
    return "".join(result).strip("-_")


def _format_ns(value: int) -> str:
    return datetime.fromtimestamp(int(value) / 1_000_000_000).isoformat(timespec="microseconds")
