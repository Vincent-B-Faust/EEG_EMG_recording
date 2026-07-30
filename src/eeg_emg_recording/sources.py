from __future__ import annotations

import math
import time
from typing import Any, Protocol

import numpy as np

from .config import physical_channel_name
from .devices import list_devices, resolve_device_name
from .models import ChannelConfig, DataBlock, RecordingConfig, SourceTiming


class AnalogSource(Protocol):
    timing: SourceTiming

    def start(self) -> SourceTiming: ...

    def read(self, samples_per_channel: int) -> DataBlock: ...

    def close(self) -> None: ...

    def info(self) -> dict[str, Any]: ...


def build_source(config: RecordingConfig) -> AnalogSource:
    if config.acquisition.backend == "fake":
        return FakeAnalogSource(config)
    return NidaqAnalogSource(config)


class NidaqAnalogSource:
    def __init__(self, config: RecordingConfig):
        self.config = config
        self.channels = config.acquisition.active_channels
        self._task: Any | None = None
        self._reader: Any | None = None
        self._device_name = ""
        self._resolved_channels: list[str] = []
        self.timing = SourceTiming(0, 0, config.acquisition.sample_rate_hz)

    def start(self) -> SourceTiming:
        import nidaqmx
        from nidaqmx.constants import AcquisitionType, Edge
        from nidaqmx.stream_readers import AnalogMultiChannelReader

        acquisition = self.config.acquisition
        self._device_name = resolve_device_name(acquisition.device, list_devices())
        self._task = nidaqmx.Task(new_task_name="EEG_EMG_recording")
        try:
            for index, channel in enumerate(self.channels):
                physical = physical_channel_name(self._device_name, channel.physical_channel)
                self._resolved_channels.append(physical)
                self._task.ai_channels.add_ai_voltage_chan(
                    physical,
                    name_to_assign_to_channel=f"signal_{index + 1:02d}",
                    terminal_config=_terminal_configuration(channel.terminal_config or acquisition.terminal_config),
                    min_val=channel.input_min_v if channel.input_min_v is not None else acquisition.input_min_v,
                    max_val=channel.input_max_v if channel.input_max_v is not None else acquisition.input_max_v,
                )
            self._task.timing.cfg_samp_clk_timing(
                rate=acquisition.sample_rate_hz,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=max(
                    acquisition.block_size * 100,
                    int(round(acquisition.sample_rate_hz * 60.0)),
                ),
            )
            if acquisition.start_trigger_enabled:
                edge = Edge.FALLING if acquisition.start_trigger_edge == "falling" else Edge.RISING
                trigger_source = _resolve_terminal(self._device_name, acquisition.start_trigger_source)
                self._task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    trigger_source=trigger_source,
                    trigger_edge=edge,
                )
            self._reader = AnalogMultiChannelReader(self._task.in_stream)
            start_wall_ns = time.time_ns()
            start_monotonic_ns = time.perf_counter_ns()
            self._task.start()
            actual_rate = float(self._task.timing.samp_clk_rate)
            self.timing = SourceTiming(start_wall_ns, start_monotonic_ns, actual_rate)
            return self.timing
        except Exception:
            self.close()
            raise

    def read(self, samples_per_channel: int) -> DataBlock:
        if self._reader is None:
            raise RuntimeError("NI-DAQmx source has not been started")
        count = int(samples_per_channel)
        data = np.empty((len(self.channels), count), dtype=np.float64)
        timeout = max(
            0.1,
            min(self.config.acquisition.read_timeout_s, 1.0)
            if self.config.acquisition.start_trigger_enabled
            else max(self.config.acquisition.read_timeout_s, count / self.timing.sample_rate_hz * 3.0),
        )
        try:
            samples_read = self._reader.read_many_sample(
                data,
                number_of_samples_per_channel=count,
                timeout=timeout,
            )
        except Exception as exc:
            if self.config.acquisition.start_trigger_enabled and getattr(exc, "error_code", None) == -200284:
                return DataBlock(
                    values=data[:, :0],
                    read_end_wall_ns=time.time_ns(),
                    read_end_monotonic_ns=time.perf_counter_ns(),
                )
            raise
        samples_read = int(samples_read if samples_read is not None else count)
        return DataBlock(
            values=data[:, :samples_read],
            read_end_wall_ns=time.time_ns(),
            read_end_monotonic_ns=time.perf_counter_ns(),
        )

    def close(self) -> None:
        if self._task is not None:
            try:
                self._task.stop()
            except Exception:
                pass
            try:
                self._task.close()
            except Exception:
                pass
        self._task = None
        self._reader = None

    def info(self) -> dict[str, Any]:
        return {
            "type": "nidaqmx",
            "device": self._device_name,
            "physical_channels": list(self._resolved_channels),
            "terminal_config": self.config.acquisition.terminal_config,
            "hardware_start_trigger": {
                "enabled": self.config.acquisition.start_trigger_enabled,
                "source": self.config.acquisition.start_trigger_source,
                "edge": self.config.acquisition.start_trigger_edge,
            },
        }


class FakeAnalogSource:
    def __init__(self, config: RecordingConfig):
        self.config = config
        self.channels = config.acquisition.active_channels
        self.sample_index = 0
        self._next_read_monotonic = 0.0
        self._rng = np.random.default_rng(20260730)
        self.timing = SourceTiming(0, 0, config.acquisition.sample_rate_hz)

    def start(self) -> SourceTiming:
        self.sample_index = 0
        self._next_read_monotonic = time.perf_counter()
        self.timing = SourceTiming(
            start_wall_ns=time.time_ns(),
            start_monotonic_ns=time.perf_counter_ns(),
            sample_rate_hz=self.config.acquisition.sample_rate_hz,
        )
        return self.timing

    def read(self, samples_per_channel: int) -> DataBlock:
        count = int(samples_per_channel)
        rate = self.timing.sample_rate_hz
        target = self._next_read_monotonic + count / rate
        delay = target - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        self._next_read_monotonic = target
        sample_ids = np.arange(count, dtype=np.float64) + self.sample_index
        t = sample_ids / rate
        rows = [self._waveform(channel, t, index) for index, channel in enumerate(self.channels)]
        self.sample_index += count
        return DataBlock(
            values=np.asarray(rows, dtype=np.float64),
            read_end_wall_ns=time.time_ns(),
            read_end_monotonic_ns=time.perf_counter_ns(),
        )

    def _waveform(self, channel: ChannelConfig, t: np.ndarray, index: int) -> np.ndarray:
        role = channel.role.upper()
        noise = self._rng.normal(0.0, 0.003, size=t.size)
        if role == "EEG":
            slow = 0.030 * np.sin(2 * math.pi * 2.2 * t + index)
            spindle = 0.018 * np.sin(2 * math.pi * 12.0 * t) * (np.sin(2 * math.pi * 0.08 * t) > 0.65)
            return slow + spindle + noise
        if role == "EMG":
            envelope = 0.3 + 0.7 * (np.sin(2 * math.pi * 0.11 * t + index) > 0.75)
            return envelope * (0.035 * np.sin(2 * math.pi * 85.0 * t) + noise * 2.0)
        if role == "SYNC":
            phase = np.mod(t, 10.0)
            return np.where(phase < 0.05, 5.0, 0.0)
        return 0.02 * np.sin(2 * math.pi * (5.0 + index * 3.0) * t) + noise

    def close(self) -> None:
        return

    def info(self) -> dict[str, Any]:
        return {
            "type": "fake",
            "device": "synthetic",
            "physical_channels": [channel.physical_channel for channel in self.channels],
        }


def _terminal_configuration(value: str) -> Any:
    from nidaqmx.constants import TerminalConfiguration

    key = str(value).strip().upper()
    mapping = {
        "DIFF": TerminalConfiguration.DIFF,
        "DIFFERENTIAL": TerminalConfiguration.DIFF,
        "RSE": TerminalConfiguration.RSE,
        "NRSE": TerminalConfiguration.NRSE,
    }
    if hasattr(TerminalConfiguration, "PSEUDO_DIFF"):
        mapping["PSEUDODIFFERENTIAL"] = TerminalConfiguration.PSEUDO_DIFF
    if key not in mapping:
        raise ValueError(f"Unsupported NI terminal configuration: {value}")
    return mapping[key]


def _resolve_terminal(device_name: str, value: str) -> str:
    text = str(value).strip()
    if not text:
        return text
    text = text.replace("{device}", device_name)
    if text.lower().startswith("auto/"):
        text = f"{device_name}/{text.split('/', 1)[1]}"
    if not text.startswith("/"):
        text = f"/{text}"
    return text
