from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROLE_COLORS = {
    "EEG": "#20A4F3",
    "EMG": "#F26430",
    "EOG": "#8E5BD9",
    "ECG": "#32A852",
    "SYNC": "#E6B800",
    "AUX": "#7A8793",
}


@dataclass(slots=True)
class ChannelConfig:
    name: str
    physical_channel: str
    role: str = "EEG"
    mouse_id: str = ""
    enabled: bool = True
    amplifier_gain: float = 1000.0
    amplifier_model: str = "WPI DAM50"
    amplifier_serial: str = ""
    amplifier_mode: str = "AC"
    hardware_highpass_hz: float | None = None
    hardware_lowpass_hz: float | None = None
    color: str = ""
    terminal_config: str | None = None
    input_min_v: float | None = None
    input_max_v: float | None = None

    @property
    def display_color(self) -> str:
        return self.color or ROLE_COLORS.get(self.role.upper(), ROLE_COLORS["AUX"])

    @property
    def input_uv_per_output_v(self) -> float:
        return 1_000_000.0 / self.amplifier_gain if self.amplifier_gain else 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["input_uv_per_output_v"] = self.input_uv_per_output_v
        data["display_color"] = self.display_color
        return data


@dataclass(slots=True)
class AcquisitionConfig:
    backend: str = "nidaqmx"
    device: str = "auto"
    sample_rate_hz: float = 1000.0
    block_duration_s: float = 0.1
    duration_s: float | None = None
    terminal_config: str = "DIFF"
    input_min_v: float = -10.0
    input_max_v: float = 10.0
    read_timeout_s: float = 10.0
    channels: list[ChannelConfig] = field(default_factory=list)
    start_trigger_enabled: bool = False
    start_trigger_source: str = ""
    start_trigger_edge: str = "rising"

    @property
    def active_channels(self) -> list[ChannelConfig]:
        return [channel for channel in self.channels if channel.enabled]

    @property
    def block_size(self) -> int:
        return max(1, int(round(self.sample_rate_hz * self.block_duration_s)))


@dataclass(slots=True)
class PreviewConfig:
    enabled: bool = True
    history_s: float = 10.0
    refresh_hz: float = 10.0
    display_units: str = "input_uv"
    auto_scale: bool = True
    notch_hz: float | None = 50.0
    notch_q: float = 30.0
    filter_display_only: bool = True
    eeg_bandpass_hz: tuple[float, float] | None = (0.5, 45.0)
    emg_bandpass_hz: tuple[float, float] | None = (10.0, 200.0)
    window_width: int = 1200
    window_height: int = 720


@dataclass(slots=True)
class StorageConfig:
    flush_interval_s: float = 1.0
    hdf5_compression: str | None = "lzf"
    csv_enabled: bool = False
    csv_decimation: int = 10


@dataclass(slots=True)
class SyncConfig:
    control_dir: Path | None = None
    parent_session_dir: Path | None = None
    analog_threshold_v: float = 2.5
    analog_edge: str = "rising"
    analog_start_match_enabled: bool = False
    analog_start_match_channel: str = ""
    analog_start_match_window_s: float = 1.0


@dataclass(slots=True)
class RecordingConfig:
    config_path: Path
    raw: dict[str, Any]
    output_root: Path
    session_name: str
    mouse_ids: list[str]
    acquisition: AcquisitionConfig
    preview: PreviewConfig
    storage: StorageConfig
    sync: SyncConfig

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.raw)
        data["_resolved"] = {
            "config_path": str(self.config_path),
            "output_root": str(self.output_root),
            "control_dir": str(self.sync.control_dir) if self.sync.control_dir else None,
            "parent_session_dir": str(self.sync.parent_session_dir) if self.sync.parent_session_dir else None,
        }
        return data


@dataclass(frozen=True, slots=True)
class SourceTiming:
    start_wall_ns: int
    start_monotonic_ns: int
    sample_rate_hz: float


@dataclass(frozen=True, slots=True)
class DataBlock:
    values: Any
    read_end_wall_ns: int
    read_end_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class PreviewUpdate:
    block: Any
    samples_written: int
    elapsed_s: float
    sample_rate_hz: float
    channels: tuple[ChannelConfig, ...]
    session_dir: Path
