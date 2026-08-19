from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec
from typing import Any

from .config import physical_channel_name
from .models import RecordingConfig


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    name: str
    product_type: str
    serial_number: str
    simulated: bool
    analog_input_channels: tuple[str, ...]
    digital_input_lines: tuple[str, ...]
    digital_output_lines: tuple[str, ...]
    max_single_channel_rate_hz: float | None = None
    max_multi_channel_rate_hz: float | None = None
    voltage_ranges_v: tuple[float, ...] = ()

    @property
    def supports_analog_input(self) -> bool:
        return bool(self.analog_input_channels)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_devices() -> list[DeviceInfo]:
    if find_spec("nidaqmx") is None:
        return []
    import nidaqmx.system

    result: list[DeviceInfo] = []
    system = nidaqmx.system.System.local()
    for device in system.devices:
        result.append(
            DeviceInfo(
                name=str(device.name),
                product_type=str(_safe_get(device, "product_type", "")),
                # Some NI-DAQmx/device combinations block indefinitely while
                # querying optional capability properties even though channel
                # enumeration and acquisition work normally. Keep the default
                # discovery path limited to properties required for recording.
                serial_number="",
                simulated=bool(_safe_get(device, "is_simulated", False)),
                analog_input_channels=tuple(_channel_names(_safe_get(device, "ai_physical_chans", ()))),
                digital_input_lines=tuple(_channel_names(_safe_get(device, "di_lines", ()))),
                digital_output_lines=tuple(_channel_names(_safe_get(device, "do_lines", ()))),
                max_single_channel_rate_hz=None,
                max_multi_channel_rate_hz=None,
                voltage_ranges_v=(),
            )
        )
    return result


def resolve_device_name(requested: str, devices: list[DeviceInfo] | None = None) -> str:
    devices = list_devices() if devices is None else devices
    requested = str(requested or "auto").strip()
    analog_devices = [device for device in devices if device.supports_analog_input]
    if requested.casefold() == "auto":
        if len(analog_devices) == 1:
            return analog_devices[0].name
        usb6001 = [device for device in analog_devices if "USB-6001" in device.product_type.upper()]
        if len(usb6001) == 1:
            return usb6001[0].name
        if len(analog_devices) > 1:
            available = ", ".join(f"{item.name} ({item.product_type})" for item in analog_devices)
            raise RuntimeError(
                "More than one NI analog-input device is available. "
                f"Set acquisition.device explicitly: {available}"
            )
        raise RuntimeError(_no_analog_device_message(devices))
    for device in devices:
        if device.name.casefold() == requested.casefold():
            if not device.supports_analog_input:
                raise RuntimeError(
                    f"NI device {device.name} ({device.product_type}) has no analog input channels. "
                    + _no_analog_device_message(devices)
                )
            return device.name
    available = ", ".join(f"{item.name} ({item.product_type})" for item in devices) or "none"
    raise RuntimeError(f"NI device {requested!r} was not found. Devices visible to NI-DAQmx: {available}")


def validate_hardware(config: RecordingConfig, devices: list[DeviceInfo] | None = None) -> dict[str, Any]:
    if config.acquisition.backend == "fake":
        return {
            "backend": "fake",
            "device": "synthetic",
            "channels": [channel.physical_channel for channel in config.acquisition.active_channels],
            "warnings": [],
        }
    devices = list_devices() if devices is None else devices
    device_name = resolve_device_name(config.acquisition.device, devices)
    device = next(item for item in devices if item.name.casefold() == device_name.casefold())
    available = {name.casefold() for name in device.analog_input_channels}
    resolved_channels = [
        physical_channel_name(device_name, channel.physical_channel)
        for channel in config.acquisition.active_channels
    ]
    missing = [name for name in resolved_channels if name.casefold() not in available]
    if missing:
        raise RuntimeError(
            f"Analog input channel(s) not available on {device_name}: {', '.join(missing)}. "
            f"Available channels: {', '.join(device.analog_input_channels) or 'none'}"
        )
    warnings: list[str] = []
    aggregate_rate = config.acquisition.sample_rate_hz * len(resolved_channels)
    product_upper = device.product_type.upper()
    if "USB-6001" in product_upper and aggregate_rate > 20_000:
        raise RuntimeError(
            f"USB-6001 aggregate analog-input rate would be {aggregate_rate:g} samples/s "
            f"({len(resolved_channels)} channels x {config.acquisition.sample_rate_hz:g} Hz), above its 20 kS/s limit."
        )
    if device.max_multi_channel_rate_hz and config.acquisition.sample_rate_hz > device.max_multi_channel_rate_hz:
        warnings.append(
            f"Requested per-channel rate {config.acquisition.sample_rate_hz:g} Hz is above the reported "
            f"multi-channel rate {device.max_multi_channel_rate_hz:g} Hz."
        )
    if "USB-6001" in product_upper:
        for channel_cfg, channel in zip(config.acquisition.active_channels, resolved_channels):
            terminal_mode = (channel_cfg.terminal_config or config.acquisition.terminal_config).upper()
            if terminal_mode != "DIFF":
                continue
            suffix = channel.rsplit("/", 1)[-1].lower()
            if suffix.startswith("ai") and suffix[2:].isdigit() and int(suffix[2:]) >= 4:
                raise RuntimeError(
                    f"{channel} cannot be configured as an independent USB-6001 differential channel. "
                    "Use ai0..ai3; NI-DAQmx automatically pairs them with ai4..ai7."
                )
    return {
        "backend": "nidaqmx",
        "device": device.to_dict(),
        "resolved_channels": resolved_channels,
        "aggregate_samples_per_second": aggregate_rate,
        "warnings": warnings,
    }


def format_device_report(devices: list[DeviceInfo] | None = None) -> list[str]:
    devices = list_devices() if devices is None else devices
    if not devices:
        return ["No NI-DAQmx devices were detected."]
    lines: list[str] = []
    for device in devices:
        simulated = " simulated" if device.simulated else ""
        lines.append(f"{device.name}: {device.product_type}{simulated}")
        lines.append(f"  AI: {', '.join(device.analog_input_channels) or 'none'}")
        if device.digital_input_lines:
            lines.append(f"  DI: {', '.join(device.digital_input_lines)}")
        if device.digital_output_lines:
            lines.append(f"  DO: {', '.join(device.digital_output_lines)}")
        rates = []
        if device.max_single_channel_rate_hz:
            rates.append(f"single={device.max_single_channel_rate_hz:g} Hz")
        if device.max_multi_channel_rate_hz:
            rates.append(f"multi={device.max_multi_channel_rate_hz:g} Hz")
        if rates:
            lines.append(f"  AI rates: {', '.join(rates)}")
    if not any(device.supports_analog_input for device in devices):
        lines.append("No analog-input device is currently available.")
    return lines


def _channel_names(collection: Any) -> list[str]:
    try:
        return [str(channel.name) for channel in collection]
    except Exception:
        return []


def _safe_get(obj: Any, name: str, default: Any) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _no_analog_device_message(devices: list[DeviceInfo]) -> str:
    visible = ", ".join(f"{item.name} ({item.product_type})" for item in devices) or "none"
    return (
        "No NI analog-input device is visible to NI-DAQmx. "
        f"Currently visible: {visible}. Connect the USB-6001, confirm it in NI MAX, then refresh."
    )
