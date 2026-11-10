from __future__ import annotations

from copy import deepcopy
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
    hardware_filter_enabled: bool = True
    hardware_notch_enabled: bool = False
    hardware_notch_hz: float | None = None
    amplifier_settings_verified: bool = False
    amplifier_notes: str = ""
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

    def to_config_dict(self) -> dict[str, Any]:
        return asdict(self)

    def amplifier_metadata(self) -> dict[str, Any]:
        return {
            "channel_name": self.name,
            "model": self.amplifier_model,
            "serial": self.amplifier_serial,
            "gain": self.amplifier_gain,
            "coupling_mode": self.amplifier_mode,
            "hardware_filter_enabled": self.hardware_filter_enabled,
            "hardware_highpass_hz": self.hardware_highpass_hz,
            "hardware_lowpass_hz": self.hardware_lowpass_hz,
            "hardware_notch_enabled": self.hardware_notch_enabled,
            "hardware_notch_hz": self.hardware_notch_hz,
            "settings_verified_against_hardware": self.amplifier_settings_verified,
            "notes": self.amplifier_notes,
            "settings_source": "manual_user_entry",
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.to_config_dict()
        data["input_uv_per_output_v"] = self.input_uv_per_output_v
        data["display_color"] = self.display_color
        data["raw_output_units"] = "V"
        data["derived_input_units"] = "uV"
        data["derived_input_formula"] = (
            "derived_input_uv = output_voltage_v * 1e6 / amplifier_gain"
        )
        data["derived_input_values_stored"] = False
        data["amplifier_settings_source"] = "manual_user_entry"
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
    coupling: str = "AC"
    y_range: tuple[float, float] | None = None
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

    def to_config_dict(self) -> dict[str, Any]:
        data = deepcopy(self.raw)
        acquisition = data.setdefault("acquisition", {})
        acquisition["channels"] = [
            channel.to_config_dict() for channel in self.acquisition.channels
        ]
        return data

    def to_dict(self) -> dict[str, Any]:
        data = self.to_config_dict()
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
