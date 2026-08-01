from __future__ import annotations

from copy import deepcopy
from math import isfinite
from pathlib import Path
from typing import Any

import yaml

from .models import (
    AcquisitionConfig,
    ChannelConfig,
    PreviewConfig,
    RecordingConfig,
    StorageConfig,
    SyncConfig,
)


def load_config(path: str | Path) -> RecordingConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be a YAML mapping: {config_path}")
    return parse_config(raw, config_path=config_path)


def parse_config(raw: dict[str, Any], *, config_path: str | Path) -> RecordingConfig:
    config_path = Path(config_path).expanduser().resolve()
    data = deepcopy(raw)
    project = _mapping(data, "project")
    acquisition_raw = _mapping(data, "acquisition")
    preview_raw = _mapping(data, "preview")
    storage_raw = _mapping(data, "storage")
    sync_raw = _mapping(data, "sync")

    output_root = _resolve_path(
        project.get("output_root", "../runs"),
        base_dir=config_path.parent,
    )
    session_name = str(project.get("session_name") or "sleep_recording").strip()
    mouse_ids = _string_list(project.get("mouse_ids", project.get("mouse_id", [])))
    channels = _parse_channels(acquisition_raw.get("channels", []))
    voltage_range = acquisition_raw.get("input_range_v", [-10.0, 10.0])
    input_min_v, input_max_v = _pair(voltage_range, (-10.0, 10.0))
    trigger = acquisition_raw.get("start_trigger")
    trigger = trigger if isinstance(trigger, dict) else {}

    acquisition = AcquisitionConfig(
        backend=str(acquisition_raw.get("backend", "nidaqmx")).strip().lower(),
        device=str(acquisition_raw.get("device", "auto")).strip() or "auto",
        sample_rate_hz=float(acquisition_raw.get("sample_rate_hz", 1000.0)),
        block_duration_s=float(acquisition_raw.get("block_duration_s", 0.1)),
        duration_s=_optional_float(acquisition_raw.get("duration_s")),
        terminal_config=str(acquisition_raw.get("terminal_config", "DIFF")).strip().upper(),
        input_min_v=float(input_min_v),
        input_max_v=float(input_max_v),
        read_timeout_s=float(acquisition_raw.get("read_timeout_s", 10.0)),
        channels=channels,
        start_trigger_enabled=bool(trigger.get("enabled", False)),
        start_trigger_source=str(trigger.get("source", "")).strip(),
        start_trigger_edge=str(trigger.get("edge", "rising")).strip().lower(),
    )
    preview = PreviewConfig(
        enabled=bool(preview_raw.get("enabled", True)),
        history_s=float(preview_raw.get("history_s", 10.0)),
        refresh_hz=float(preview_raw.get("refresh_hz", 10.0)),
        display_units=str(preview_raw.get("display_units", "input_uv")).strip().lower(),
        auto_scale=bool(preview_raw.get("auto_scale", True)),
        notch_hz=_optional_float(preview_raw.get("notch_hz", 50.0)),
        notch_q=float(preview_raw.get("notch_q", 30.0)),
        filter_display_only=bool(preview_raw.get("filter_display_only", True)),
        eeg_bandpass_hz=_optional_pair(preview_raw.get("eeg_bandpass_hz", [0.5, 45.0])),
        emg_bandpass_hz=_optional_pair(preview_raw.get("emg_bandpass_hz", [10.0, 200.0])),
        window_width=int(preview_raw.get("window_width", 1200)),
        window_height=int(preview_raw.get("window_height", 720)),
    )
    compression = storage_raw.get("hdf5_compression", "lzf")
    storage = StorageConfig(
        flush_interval_s=float(storage_raw.get("flush_interval_s", 1.0)),
        hdf5_compression=None if compression in (None, "", "none", "None") else str(compression),
        csv_enabled=bool(storage_raw.get("csv_enabled", False)),
        csv_decimation=max(1, int(storage_raw.get("csv_decimation", 10))),
    )
    sync = SyncConfig(
        control_dir=_resolve_optional_path(sync_raw.get("control_dir"), config_path.parent),
        parent_session_dir=_resolve_optional_path(sync_raw.get("parent_session_dir"), config_path.parent),
        analog_threshold_v=float(sync_raw.get("analog_threshold_v", 2.5)),
        analog_edge=str(sync_raw.get("analog_edge", "rising")).strip().lower(),
        analog_start_match_enabled=bool(sync_raw.get("analog_start_match_enabled", False)),
        analog_start_match_channel=str(sync_raw.get("analog_start_match_channel", "")).strip(),
        analog_start_match_window_s=float(sync_raw.get("analog_start_match_window_s", 1.0)),
    )
    config = RecordingConfig(
        config_path=config_path,
        raw=data,
        output_root=output_root,
        session_name=session_name,
        mouse_ids=mouse_ids,
        acquisition=acquisition,
        preview=preview,
        storage=storage,
        sync=sync,
    )
    validate_config(config)
    return config


def validate_config(config: RecordingConfig) -> None:
    acquisition = config.acquisition
    if acquisition.backend not in {"nidaqmx", "fake"}:
        raise ValueError("acquisition.backend must be 'nidaqmx' or 'fake'")
    if acquisition.sample_rate_hz <= 0:
        raise ValueError("acquisition.sample_rate_hz must be greater than zero")
    if acquisition.block_duration_s <= 0:
        raise ValueError("acquisition.block_duration_s must be greater than zero")
    if acquisition.duration_s is not None and acquisition.duration_s <= 0:
        raise ValueError("acquisition.duration_s must be empty/null or greater than zero")
    if acquisition.input_min_v >= acquisition.input_max_v:
        raise ValueError("acquisition.input_range_v must be [minimum, maximum]")
    if acquisition.terminal_config not in {"DIFF", "RSE", "NRSE", "PSEUDODIFFERENTIAL"}:
        raise ValueError("terminal_config must be DIFF, RSE, NRSE, or PSEUDODIFFERENTIAL")
    channels = acquisition.active_channels
    if not channels:
        raise ValueError("At least one acquisition channel must be enabled")
    names = [channel.name.casefold() for channel in channels]
    if len(names) != len(set(names)):
        raise ValueError("Enabled channel names must be unique")
    physical = [channel.physical_channel.casefold() for channel in channels]
    if len(physical) != len(set(physical)):
        raise ValueError("Enabled physical channels must be unique")
    for channel in channels:
        if not channel.name:
            raise ValueError("Every enabled channel requires a name")
        if not channel.physical_channel:
            raise ValueError(f"Channel {channel.name!r} requires physical_channel")
        if not isfinite(channel.amplifier_gain) or channel.amplifier_gain <= 0:
            raise ValueError(f"Channel {channel.name!r} amplifier_gain must be greater than zero")
        if channel.amplifier_mode not in {"AC", "DC", "UNKNOWN", "OTHER"}:
            raise ValueError(
                f"Channel {channel.name!r} amplifier_mode must be AC, DC, UNKNOWN, or OTHER"
            )
        if (
            channel.hardware_highpass_hz is not None
            and (
                not isfinite(channel.hardware_highpass_hz)
                or channel.hardware_highpass_hz < 0
            )
        ):
            raise ValueError(
                f"Channel {channel.name!r} hardware_highpass_hz must be finite and "
                "cannot be negative"
            )
        if (
            channel.hardware_lowpass_hz is not None
            and (
                not isfinite(channel.hardware_lowpass_hz)
                or channel.hardware_lowpass_hz <= 0
            )
        ):
            raise ValueError(
                f"Channel {channel.name!r} hardware_lowpass_hz must be finite and "
                "greater than zero"
            )
        if (
            channel.hardware_filter_enabled
            and channel.hardware_highpass_hz is not None
            and channel.hardware_lowpass_hz is not None
            and channel.hardware_highpass_hz >= channel.hardware_lowpass_hz
        ):
            raise ValueError(
                f"Channel {channel.name!r} hardware_highpass_hz must be below "
                "hardware_lowpass_hz"
            )
        if (
            channel.hardware_notch_hz is not None
            and (
                not isfinite(channel.hardware_notch_hz)
                or channel.hardware_notch_hz <= 0
            )
        ):
            raise ValueError(
                f"Channel {channel.name!r} hardware_notch_hz must be finite and "
                "greater than zero"
            )
        if channel.hardware_notch_enabled and channel.hardware_notch_hz is None:
            raise ValueError(
                f"Channel {channel.name!r} hardware_notch_hz is required when "
                "hardware_notch_enabled is true"
            )
        if (channel.input_min_v is None) != (channel.input_max_v is None):
            raise ValueError(
                f"Channel {channel.name!r} input_min_v and input_max_v must be set together"
            )
        if (
            channel.input_min_v is not None
            and channel.input_max_v is not None
            and (
                not isfinite(channel.input_min_v)
                or not isfinite(channel.input_max_v)
                or channel.input_min_v >= channel.input_max_v
            )
        ):
            raise ValueError(
                f"Channel {channel.name!r} input range must be finite and "
                "input_min_v must be below input_max_v"
            )
        if (
            channel.terminal_config is not None
            and channel.terminal_config.upper()
            not in {"DIFF", "RSE", "NRSE", "PSEUDODIFFERENTIAL"}
        ):
            raise ValueError(
                f"Channel {channel.name!r} terminal_config must be DIFF, RSE, NRSE, "
                "or PSEUDODIFFERENTIAL"
            )
    if acquisition.start_trigger_enabled and not acquisition.start_trigger_source:
        raise ValueError("start_trigger.source is required when the start trigger is enabled")
    if config.sync.analog_start_match_enabled:
        sync_channel = config.sync.analog_start_match_channel.casefold()
        if not sync_channel:
            raise ValueError(
                "sync.analog_start_match_channel is required when analog start matching is enabled"
            )
        matches = [
            channel
            for channel in channels
            if channel.name.casefold() == sync_channel
            and channel.role.upper() == "SYNC"
        ]
        if len(matches) != 1:
            raise ValueError(
                "sync.analog_start_match_channel must name one enabled channel with role SYNC"
            )
    if config.preview.display_units not in {"output_v", "input_uv"}:
        raise ValueError("preview.display_units must be output_v or input_uv")


def save_config(config: RecordingConfig | dict[str, Any], path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = config.to_config_dict() if isinstance(config, RecordingConfig) else config
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        yaml.safe_dump(raw, handle, sort_keys=False, allow_unicode=True)
    return output


def apply_overrides(
    config: RecordingConfig,
    *,
    output_root: str | Path | None = None,
    duration_s: float | None = None,
    duration_unbounded: bool = False,
    backend: str | None = None,
    preview: bool | None = None,
    control_dir: str | Path | None = None,
    parent_session_dir: str | Path | None = None,
) -> RecordingConfig:
    raw = deepcopy(config.raw)
    if output_root is not None:
        _mapping(raw, "project")["output_root"] = str(Path(output_root).expanduser().resolve())
    if duration_unbounded:
        _mapping(raw, "acquisition")["duration_s"] = None
    elif duration_s is not None:
        _mapping(raw, "acquisition")["duration_s"] = float(duration_s)
    if backend is not None:
        _mapping(raw, "acquisition")["backend"] = str(backend)
    if preview is not None:
        _mapping(raw, "preview")["enabled"] = bool(preview)
    if control_dir is not None:
        _mapping(raw, "sync")["control_dir"] = str(Path(control_dir).expanduser().resolve())
    if parent_session_dir is not None:
        _mapping(raw, "sync")["parent_session_dir"] = str(Path(parent_session_dir).expanduser().resolve())
    return parse_config(raw, config_path=config.config_path)


def physical_channel_name(device: str, value: str) -> str:
    text = str(value).strip().lstrip("/")
    if text.lower().startswith("auto/"):
        text = text.split("/", 1)[1]
    if "/" not in text:
        return f"{device}/{text}"
    prefix, suffix = text.split("/", 1)
    if prefix.lower() in {"auto", "{device}", "device"}:
        return f"{device}/{suffix}"
    return text


def config_from_gui_dict(raw: dict[str, Any], config_path: str | Path) -> RecordingConfig:
    return parse_config(raw, config_path=config_path)


def _parse_channels(raw: Any) -> list[ChannelConfig]:
    if not isinstance(raw, list):
        raise ValueError("acquisition.channels must be a YAML list")
    channels: list[ChannelConfig] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"acquisition.channels[{index}] must be a mapping")
        role = str(item.get("role", "EEG")).strip().upper() or "EEG"
        name = str(item.get("name") or f"Channel{index}").strip()
        channels.append(
            ChannelConfig(
                name=name,
                physical_channel=str(item.get("physical_channel") or item.get("channel") or "").strip(),
                role=role,
                mouse_id=str(item.get("mouse_id", "")).strip(),
                enabled=bool(item.get("enabled", True)),
                amplifier_gain=float(item.get("amplifier_gain", item.get("gain", 1000.0))),
                amplifier_model=str(item.get("amplifier_model", "WPI DAM50")).strip(),
                amplifier_serial=str(item.get("amplifier_serial", "")).strip(),
                amplifier_mode=str(item.get("amplifier_mode", "AC")).strip().upper(),
                hardware_highpass_hz=_optional_float(item.get("hardware_highpass_hz")),
                hardware_lowpass_hz=_optional_float(item.get("hardware_lowpass_hz")),
                hardware_filter_enabled=_as_bool(
                    item.get("hardware_filter_enabled", True)
                ),
                hardware_notch_enabled=_as_bool(
                    item.get("hardware_notch_enabled", False)
                ),
                hardware_notch_hz=_optional_float(item.get("hardware_notch_hz")),
                amplifier_settings_verified=_as_bool(
                    item.get("amplifier_settings_verified", False)
                ),
                amplifier_notes=str(item.get("amplifier_notes", "")).strip(),
                color=str(item.get("color", "")).strip(),
                terminal_config=_optional_upper_text(item.get("terminal_config")),
                input_min_v=_optional_float(item.get("input_min_v")),
                input_max_v=_optional_float(item.get("input_max_v")),
            )
        )
    return channels


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        value = {}
        data[key] = value
    return value


def _resolve_path(value: Any, *, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _resolve_optional_path(value: Any, base_dir: Path) -> Path | None:
    if value in (None, "", "None", "null"):
        return None
    return _resolve_path(value, base_dir=base_dir)


def _optional_float(value: Any) -> float | None:
    if value in (None, "", "None", "null"):
        return None
    return float(value)


def _optional_text(value: Any) -> str | None:
    if value in (None, "", "None", "null"):
        return None
    return str(value).strip()


def _optional_upper_text(value: Any) -> str | None:
    text = _optional_text(value)
    return text.upper() if text else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0", "", "none", "null"}:
            return False
    return bool(value)


def _pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return default
    return float(value[0]), float(value[1])


def _optional_pair(value: Any) -> tuple[float, float] | None:
    if value in (None, "", "None", "null"):
        return None
    low, high = _pair(value, (0.0, 0.0))
    return (low, high) if 0 < low < high else None


def _string_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]
