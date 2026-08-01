from __future__ import annotations

import csv
import logging
import platform
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .config import apply_overrides, load_config
from .control import ControlDirectory, atomic_write_json
from .models import ChannelConfig, DataBlock, PreviewUpdate, RecordingConfig, SourceTiming
from .sources import AnalogSource, build_source
from .storage import SessionWriter, create_session_dir


PreviewCallback = Callable[[PreviewUpdate], bool | None]


@dataclass(frozen=True, slots=True)
class RunResult:
    session_dir: Path
    metadata_path: Path
    signals_path: Path
    status: str
    stop_reason: str
    samples_written: int


def run_recording(
    config_path: str | Path,
    *,
    session_dir: str | Path | None = None,
    output_root: str | Path | None = None,
    duration_s: float | None = None,
    backend: str | None = None,
    preview: bool | None = None,
    control_dir: str | Path | None = None,
    parent_session_dir: str | Path | None = None,
    stop_event: threading.Event | None = None,
    preview_callback: PreviewCallback | None = None,
) -> RunResult:
    config = load_config(config_path)
    config = apply_overrides(
        config,
        output_root=output_root,
        duration_s=duration_s,
        backend=backend,
        preview=preview,
        control_dir=control_dir,
        parent_session_dir=parent_session_dir,
    )
    return Recorder(
        config,
        session_dir=session_dir,
        stop_event=stop_event,
        preview_callback=preview_callback,
    ).run()


class Recorder:
    def __init__(
        self,
        config: RecordingConfig,
        *,
        session_dir: str | Path | None = None,
        stop_event: threading.Event | None = None,
        preview_callback: PreviewCallback | None = None,
        source: AnalogSource | None = None,
    ):
        self.config = config
        self.session_dir = create_session_dir(config, exact_path=session_dir)
        self.stop_event = stop_event or threading.Event()
        self.preview_callback = preview_callback
        self.source = source or build_source(config)
        control_path = config.sync.control_dir or (self.session_dir / "control")
        self.control = ControlDirectory(control_path)
        self.writer = SessionWriter(self.session_dir, config)
        self.logger = _build_logger(self.session_dir)
        self._analog_edges = _AnalogEdgeDetector(
            config.acquisition.active_channels,
            threshold_v=config.sync.analog_threshold_v,
            edge=config.sync.analog_edge,
        )
        self._quality = _SignalQualityMonitor(config)
        self._last_live_preview_s = float("-inf")
        self._live_preview_last_error = ""

    def request_stop(self) -> None:
        self.stop_event.set()

    def run(self) -> RunResult:
        status = "failed"
        stop_reason = "initialization_failed"
        exception_data: dict[str, Any] | None = None
        writer_open = False
        preview_last_error = ""
        expected_samples: int | None = None
        acquisition_pump: _AcquisitionPump | None = None
        try:
            self.control.initialize()
            self.logger.info("EEG/EMG session: %s", self.session_dir)
            self.logger.info(
                "Channels: %s",
                ", ".join(
                    f"{channel.name}({channel.role}:{channel.physical_channel})"
                    for channel in self.config.acquisition.active_channels
                ),
            )
            timing = self.source.start()
            expected_samples = _expected_samples(self.config, timing.sample_rate_hz)
            self.writer.open(timing, self.source.info())
            writer_open = True
            trigger_enabled = self.config.acquisition.start_trigger_enabled
            if trigger_enabled:
                self.writer.record_event(
                    "daq_task_armed",
                    source="eeg_emg",
                    wall_time_ns=timing.start_wall_ns,
                    details={
                        "trigger_source": self.config.acquisition.start_trigger_source,
                        "armed_monotonic_ns": timing.start_monotonic_ns,
                    },
                )
            else:
                self.writer.record_event(
                    "recording_started",
                    source="eeg_emg",
                    sample_index=0,
                    wall_time_ns=timing.start_wall_ns,
                    monotonic_ns=timing.start_monotonic_ns,
                )
            self.control.mark_ready(
                session_dir=str(self.session_dir),
                sample_rate_hz=timing.sample_rate_hz,
                channels=[channel.name for channel in self.config.acquisition.active_channels],
                armed_for_trigger=trigger_enabled,
            )
            acquisition_pump = _AcquisitionPump(
                self.source,
                block_size=self.config.acquisition.block_size,
                buffer_blocks=max(
                    100,
                    int(round(60.0 / self.config.acquisition.block_duration_s)),
                ),
            )
            acquisition_pump.start()
            stop_reason = "duration_complete" if expected_samples is not None else "external_stop"
            first_block = True
            last_progress = time.monotonic()
            stop_target_samples: int | None = expected_samples
            external_stop_seen = False
            local_stop_seen = False
            if expected_samples is not None:
                status = "completed"
            while True:
                if not external_stop_seen:
                    requested = self.control.stop_request()
                    if requested is not None:
                        external_stop_seen = True
                        stop_reason = str(requested.get("reason", "external_request"))
                        status = (
                            "completed"
                            if stop_reason in {"duration_complete", "thermal_duration_complete"}
                            else "stopped"
                        )
                        self._record_control_payload_event("stop_requested", requested)
                        requested_target = (
                            0
                            if trigger_enabled and first_block
                            else self._stop_target_from_payload(requested)
                        )
                        if trigger_enabled and first_block:
                            self.writer.record_event(
                                "hardware_trigger_wait_cancelled",
                                source="eeg_emg",
                                sample_index=0,
                                details={"reason": stop_reason},
                            )
                        stop_target_samples = (
                            requested_target
                            if stop_target_samples is None
                            else min(stop_target_samples, requested_target)
                        )
                if self.stop_event.is_set() and not local_stop_seen:
                    local_stop_seen = True
                    status = "stopped"
                    stop_reason = "local_stop_request"
                    requested_target = (
                        0
                        if trigger_enabled and first_block
                        else self.writer.samples_written + self.config.acquisition.block_size
                    )
                    if trigger_enabled and first_block:
                        self.writer.record_event(
                            "hardware_trigger_wait_cancelled",
                            source="eeg_emg",
                            sample_index=0,
                            details={"reason": stop_reason},
                        )
                    stop_target_samples = (
                        requested_target
                        if stop_target_samples is None
                        else min(stop_target_samples, requested_target)
                    )
                if (
                    stop_target_samples is not None
                    and self.writer.samples_written >= stop_target_samples
                ):
                    break
                block = acquisition_pump.get(timeout_s=0.25)
                if block is None:
                    continue
                values = np.asarray(block.values)
                if values.size == 0:
                    continue
                if first_block and trigger_enabled:
                    timing = _estimated_trigger_timing(block, timing.sample_rate_hz, values.shape[1])
                    self.writer.set_sample_origin(timing)
                    self.writer.record_event(
                        "hardware_trigger_received",
                        source="eeg_emg",
                        sample_index=0,
                        wall_time_ns=timing.start_wall_ns,
                        monotonic_ns=timing.start_monotonic_ns,
                        details={"timing_quality": "estimated_from_first_completed_block"},
                    )
                first_block = False
                if stop_target_samples is not None:
                    remaining = stop_target_samples - self.writer.samples_written
                    values = values[:, : max(0, remaining)]
                    if values.shape[1] == 0:
                        continue
                sample_start, _sample_end = self.writer.write_block(
                    values,
                    read_end_wall_ns=block.read_end_wall_ns,
                    read_end_monotonic_ns=block.read_end_monotonic_ns,
                )
                self._quality.observe(values)
                for event in self._analog_edges.observe(values, sample_start):
                    self.writer.record_event(
                        event["event_name"],
                        source="analog_sync",
                        sample_index=event["sample_index"],
                        value=event["value"],
                        details={"channel": event["channel"]},
                    )
                self._consume_control_events()
                self._write_live_preview(
                    values,
                    sample_rate_hz=timing.sample_rate_hz,
                )
                if self.preview_callback is not None:
                    try:
                        keep_running = self.preview_callback(
                            PreviewUpdate(
                                block=values,
                                samples_written=self.writer.samples_written,
                                elapsed_s=self.writer.samples_written / timing.sample_rate_hz,
                                sample_rate_hz=timing.sample_rate_hz,
                                channels=tuple(self.config.acquisition.active_channels),
                                session_dir=self.session_dir,
                            )
                        )
                        if keep_running is False:
                            status = "stopped"
                            stop_reason = "preview_stop_request"
                            stop_target_samples = self.writer.samples_written
                            break
                    except Exception as exc:
                        message = f"{type(exc).__name__}: {exc}"
                        if message != preview_last_error:
                            self.logger.warning("Preview callback failed; recording continues: %s", message)
                            preview_last_error = message
                if time.monotonic() - last_progress >= 10.0:
                    self.logger.info(
                        "Recorded %.1f s (%d samples)",
                        self.writer.samples_written / timing.sample_rate_hz,
                        self.writer.samples_written,
                    )
                    self.control.write_status(
                        "recording",
                        session_dir=str(self.session_dir),
                        samples_written=self.writer.samples_written,
                        elapsed_s=self.writer.samples_written / timing.sample_rate_hz,
                    )
                    last_progress = time.monotonic()
            if status == "failed":
                status = "completed" if expected_samples is not None else "stopped"
            self._consume_control_events()
            self.writer.record_event(
                "recording_stopped",
                source="eeg_emg",
                sample_index=self.writer.samples_written,
                wall_time_ns=self.writer.sample_wall_ns(self.writer.samples_written),
                monotonic_ns=time.perf_counter_ns(),
                details={"status": status, "reason": stop_reason},
            )
        except KeyboardInterrupt:
            status = "interrupted"
            stop_reason = "keyboard_interrupt"
            self.logger.info("Recording interrupted by keyboard")
        except Exception as exc:
            status = "failed"
            stop_reason = "exception"
            exception_data = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            self.logger.exception("EEG/EMG recording failed")
            if not writer_open:
                _write_early_failure(self.session_dir, self.config, exception_data)
            raise
        finally:
            cleanup_errors: list[str] = []
            if acquisition_pump is not None:
                try:
                    acquisition_pump.stop()
                    acquisition_pump.join(timeout_s=2.0)
                except Exception as exc:
                    cleanup_errors.append(f"acquisition pump: {type(exc).__name__}: {exc}")
            try:
                self.source.close()
            except Exception as exc:
                cleanup_errors.append(f"source close: {type(exc).__name__}: {exc}")
            if cleanup_errors and exception_data is None:
                status = "failed"
                stop_reason = "cleanup_failed"
                exception_data = {
                    "type": "CleanupError",
                    "message": "; ".join(cleanup_errors),
                }
            if writer_open:
                try:
                    self.writer.set_signal_quality(self._quality.summary())
                    self.writer.close(
                        status=status,
                        stop_reason=stop_reason,
                        exception=exception_data,
                    )
                except Exception as exc:
                    cleanup_errors.append(f"writer close: {type(exc).__name__}: {exc}")
                    status = "failed"
                    stop_reason = "writer_close_failed"
            try:
                self.control.mark_complete(
                    status,
                    session_dir=str(self.session_dir),
                    stop_reason=stop_reason,
                    samples_written=self.writer.samples_written,
                )
            except Exception as exc:
                cleanup_errors.append(f"control close: {type(exc).__name__}: {exc}")
            if cleanup_errors:
                self.logger.error("Cleanup errors: %s", "; ".join(cleanup_errors))
                atomic_write_json(
                    self.session_dir / "cleanup_errors.json",
                    {"errors": cleanup_errors, "status": status, "stop_reason": stop_reason},
                )
            _close_logger(self.logger)
        return RunResult(
            session_dir=self.session_dir,
            metadata_path=self.writer.metadata_path,
            signals_path=self.writer.h5_path,
            status=status,
            stop_reason=stop_reason,
            samples_written=self.writer.samples_written,
        )

    def _consume_control_events(self) -> None:
        for payload in self.control.poll_events():
            self._record_control_payload_event(
                str(payload.get("event_name", "external_event")),
                payload,
            )

    def _write_live_preview(
        self,
        block: np.ndarray,
        *,
        sample_rate_hz: float,
    ) -> None:
        if not self.control.enabled or self.control.path is None:
            return
        now_s = time.monotonic()
        refresh_hz = max(
            0.5,
            min(5.0, float(self.config.preview.refresh_hz)),
        )
        if now_s - self._last_live_preview_s < 1.0 / refresh_hz:
            return
        self._last_live_preview_s = now_s
        values = np.asarray(block, dtype=float)
        point_count = min(400, values.shape[1])
        indices = np.linspace(
            0,
            values.shape[1] - 1,
            point_count,
        ).astype(int)
        traces = {}
        for channel_index, channel in enumerate(
            self.config.acquisition.active_channels
        ):
            output_v = values[channel_index, indices]
            traces[channel.name] = {
                "role": channel.role,
                "output_v": output_v.tolist(),
                "input_uv_derived": (
                    output_v * channel.input_uv_per_output_v
                ).tolist(),
            }
        try:
            atomic_write_json(
                self.control.path / "live_preview.json",
                {
                    "schema": "aki_eeg_emg_live_preview_v1",
                    "wall_time_ns": time.time_ns(),
                    "monotonic_ns": time.perf_counter_ns(),
                    "session_dir": str(self.session_dir),
                    "samples_written": self.writer.samples_written,
                    "elapsed_s": self.writer.samples_written
                    / float(sample_rate_hz),
                    "sample_rate_hz": float(sample_rate_hz),
                    "traces": traces,
                },
            )
            self._live_preview_last_error = ""
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if message != self._live_preview_last_error:
                self.logger.warning(
                    "Could not update live preview telemetry: %s",
                    message,
                )
                self._live_preview_last_error = message

    def _record_control_payload_event(self, event_name: str, payload: dict[str, Any]) -> None:
        monotonic_ns = _optional_int(payload.get("monotonic_ns"))
        wall_time_ns = _optional_int(payload.get("wall_time_ns"))
        clock_host = str(payload.get("clock_host", "")).strip()
        sample_index = None
        if monotonic_ns is not None and (not clock_host or clock_host == platform.node()):
            sample_index = self.writer.sample_index_for_monotonic(monotonic_ns)
        elif wall_time_ns is not None:
            sample_index = self.writer.sample_index_for_wall(wall_time_ns)
        self.writer.record_event(
            event_name,
            source=str(payload.get("source", "external_control")),
            sample_index=sample_index,
            wall_time_ns=wall_time_ns,
            monotonic_ns=monotonic_ns if sample_index is not None and clock_host == platform.node() else None,
            details=payload.get("details") if isinstance(payload.get("details"), dict) else payload,
        )

    def _stop_target_from_payload(self, payload: dict[str, Any]) -> int:
        monotonic_ns = _optional_int(payload.get("monotonic_ns"))
        wall_time_ns = _optional_int(payload.get("wall_time_ns"))
        clock_host = str(payload.get("clock_host", "")).strip()
        if monotonic_ns is not None and (not clock_host or clock_host == platform.node()):
            return self.writer.sample_index_for_monotonic(monotonic_ns)
        if wall_time_ns is not None:
            return self.writer.sample_index_for_wall(wall_time_ns)
        return self.writer.samples_written + self.config.acquisition.block_size


class _AcquisitionPump:
    def __init__(
        self,
        source: AnalogSource,
        *,
        block_size: int,
        buffer_blocks: int,
    ):
        self.source = source
        self.block_size = int(block_size)
        self.queue: queue.Queue[DataBlock] = queue.Queue(maxsize=max(2, int(buffer_blocks)))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="eeg-emg-acquisition",
            daemon=True,
        )
        self.error: BaseException | None = None
        self.finished = threading.Event()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def join(self, *, timeout_s: float) -> None:
        self.thread.join(timeout=max(0.0, float(timeout_s)))
        if self.thread.is_alive():
            raise TimeoutError("Analog acquisition thread did not stop before timeout")

    def get(self, *, timeout_s: float) -> DataBlock | None:
        try:
            return self.queue.get(timeout=max(0.01, float(timeout_s)))
        except queue.Empty:
            if self.error is not None:
                raise RuntimeError("Analog acquisition thread failed") from self.error
            if self.finished.is_set() and self.queue.empty():
                raise RuntimeError("Analog acquisition stopped unexpectedly")
            return None

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                block = self.source.read(self.block_size)
                if np.asarray(block.values).size == 0:
                    continue
                while not self.stop_event.is_set():
                    try:
                        self.queue.put(block, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except BaseException as exc:
            self.error = exc
        finally:
            self.finished.set()


class _AnalogEdgeDetector:
    def __init__(self, channels: list[ChannelConfig], *, threshold_v: float, edge: str):
        self.channels = channels
        self.threshold_v = float(threshold_v)
        self.edge = str(edge).lower()
        self.indices = [index for index, channel in enumerate(channels) if channel.role.upper() == "SYNC"]
        self.last_states = {index: False for index in self.indices}

    def observe(self, block: np.ndarray, sample_start: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for index in self.indices:
            high = np.asarray(block[index], dtype=float) >= self.threshold_v
            previous = self.last_states[index]
            for local_index, state_value in enumerate(high):
                state = bool(state_value)
                rising = state and not previous
                falling = previous and not state
                matched = falling if self.edge == "falling" else rising
                if matched:
                    events.append(
                        {
                            "event_name": f"{self.edge}_edge",
                            "sample_index": sample_start + local_index,
                            "value": float(block[index, local_index]),
                            "channel": self.channels[index].name,
                        }
                    )
                previous = state
            self.last_states[index] = previous
        return events


class _SignalQualityMonitor:
    def __init__(self, config: RecordingConfig):
        self.channels = config.acquisition.active_channels
        acquisition = config.acquisition
        self.input_min = np.asarray(
            [
                channel.input_min_v
                if channel.input_min_v is not None
                else acquisition.input_min_v
                for channel in self.channels
            ],
            dtype=float,
        )
        self.input_max = np.asarray(
            [
                channel.input_max_v
                if channel.input_max_v is not None
                else acquisition.input_max_v
                for channel in self.channels
            ],
            dtype=float,
        )
        self.minimum = np.full(len(self.channels), np.inf, dtype=float)
        self.maximum = np.full(len(self.channels), -np.inf, dtype=float)
        self.clipped = np.zeros(len(self.channels), dtype=np.int64)
        self.nonfinite = np.zeros(len(self.channels), dtype=np.int64)
        self.samples = 0

    def observe(self, block: np.ndarray) -> None:
        values = np.asarray(block, dtype=float)
        if values.size == 0:
            return
        self.samples += values.shape[1]
        finite = np.isfinite(values)
        self.nonfinite += np.sum(~finite, axis=1)
        safe = np.where(finite, values, np.nan)
        with np.errstate(all="ignore"):
            block_min = np.nanmin(safe, axis=1)
            block_max = np.nanmax(safe, axis=1)
        self.minimum = np.fmin(self.minimum, block_min)
        self.maximum = np.fmax(self.maximum, block_max)
        margin = (self.input_max - self.input_min) * 0.01
        clipped = (values <= (self.input_min + margin)[:, None]) | (
            values >= (self.input_max - margin)[:, None]
        )
        self.clipped += np.sum(clipped & finite, axis=1)

    def summary(self) -> dict[str, Any]:
        channel_rows = []
        for index, channel in enumerate(self.channels):
            clip_fraction = (
                float(self.clipped[index]) / self.samples if self.samples else 0.0
            )
            channel_rows.append(
                {
                    "name": channel.name,
                    "minimum_output_v": _finite_or_none(self.minimum[index]),
                    "maximum_output_v": _finite_or_none(self.maximum[index]),
                    "clipped_samples": int(self.clipped[index]),
                    "clipped_fraction": clip_fraction,
                    "nonfinite_samples": int(self.nonfinite[index]),
                    "input_range_v": [
                        float(self.input_min[index]),
                        float(self.input_max[index]),
                    ],
                    "warning": bool(self.clipped[index] or self.nonfinite[index]),
                }
            )
        return {
            "samples_checked_per_channel": self.samples,
            "channels": channel_rows,
            "definition": "Clipping is within 1% of the configured NI input range.",
        }


def _expected_samples(config: RecordingConfig, sample_rate_hz: float) -> int | None:
    duration = config.acquisition.duration_s
    if duration is None:
        return None
    return max(1, int(round(duration * float(sample_rate_hz))))


def _estimated_trigger_timing(block: Any, sample_rate_hz: float, sample_count: int) -> SourceTiming:
    duration_ns = int(round(max(0, sample_count - 1) * 1_000_000_000 / sample_rate_hz))
    return SourceTiming(
        start_wall_ns=int(block.read_end_wall_ns) - duration_ns,
        start_monotonic_ns=int(block.read_end_monotonic_ns) - duration_ns,
        sample_rate_hz=sample_rate_hz,
    )


def _write_early_failure(
    session_dir: Path,
    config: RecordingConfig,
    exception_data: dict[str, Any],
) -> None:
    path = session_dir / "metadata.json"
    atomic_write_json(
        path,
        {
            "schema": "aki_eeg_emg_recording_v1",
            "status": "failed",
            "stop_reason": "initialization_failed",
            "session_dir": str(session_dir),
            "exception": exception_data,
            "channels": [channel.to_dict() for channel in config.acquisition.active_channels],
            "sample_rate_hz": config.acquisition.sample_rate_hz,
            "end_wall_ns": time.time_ns(),
        },
    )


def _build_logger(session_dir: Path) -> logging.Logger:
    logger = logging.getLogger(f"eeg_emg_recording.{id(session_dir)}.{time.time_ns()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(session_dir / "recording.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _close_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        try:
            handler.flush()
            handler.close()
        finally:
            logger.removeHandler(handler)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None
