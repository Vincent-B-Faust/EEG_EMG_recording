from __future__ import annotations

import math
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

import cv2
import numpy as np

from .models import ChannelConfig, PreviewUpdate, RecordingConfig


class RollingSignalBuffer:
    def __init__(self, channel_count: int, sample_rate_hz: float, history_s: float):
        self.max_samples = max(10, int(round(sample_rate_hz * history_s)))
        self.values = np.empty((channel_count, 0), dtype=np.float32)

    def append(self, block: np.ndarray) -> np.ndarray:
        values = np.asarray(block, dtype=np.float32)
        if self.values.size == 0:
            self.values = values[:, -self.max_samples :].copy()
        else:
            self.values = np.concatenate((self.values, values), axis=1)[:, -self.max_samples :]
        return self.values


class OpenCvPreview:
    def __init__(self, config: RecordingConfig, *, stop_callback: Callable[[], None] | None = None):
        self.config = config
        self.stop_callback = stop_callback
        self.window_name = "EEG-EMG Sleep Recording"
        self.buffer = RollingSignalBuffer(
            len(config.acquisition.active_channels),
            config.acquisition.sample_rate_hz,
            config.preview.history_s,
        )
        self._last_render_s = -math.inf
        self._render_period_s = 1.0 / max(config.preview.refresh_hz, 1.0)

    def __call__(self, update: PreviewUpdate) -> bool:
        values = self.buffer.append(update.block)
        if update.elapsed_s - self._last_render_s < self._render_period_s:
            return True
        self._last_render_s = update.elapsed_s
        canvas = render_preview_canvas(
            values,
            channels=list(update.channels),
            sample_rate_hz=update.sample_rate_hz,
            config=self.config,
            width=self.config.preview.window_width,
            height=self.config.preview.window_height,
            elapsed_s=update.elapsed_s,
            samples_written=update.samples_written,
        )
        cv2.imshow(self.window_name, canvas)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            if self.stop_callback is not None:
                self.stop_callback()
            return False
        return True

    def close(self) -> None:
        try:
            cv2.destroyWindow(self.window_name)
        except cv2.error:
            pass


def render_preview_canvas(
    raw_values: np.ndarray,
    *,
    channels: list[ChannelConfig],
    sample_rate_hz: float,
    config: RecordingConfig,
    width: int,
    height: int,
    elapsed_s: float,
    samples_written: int,
) -> np.ndarray:
    width = max(640, int(width))
    height = max(300, int(height))
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    top = 24
    footer = 34
    left = 190
    right = 18
    gap = 8
    count = max(1, len(channels))
    row_height = max(52, (height - top - footer - gap * (count - 1)) // count)
    plot_width = max(100, width - left - right)
    for index, channel in enumerate(channels):
        y0 = top + index * (row_height + gap)
        y1 = min(height - footer - 1, y0 + row_height)
        raw = np.asarray(raw_values[index], dtype=np.float64)
        display, units = display_values(raw, channel, sample_rate_hz, config)
        filtered = filter_for_preview(display, channel.role, sample_rate_hz, config)
        low, high = robust_range(filtered, auto_scale=config.preview.auto_scale)
        color = hex_to_bgr(channel.display_color)
        cv2.rectangle(canvas, (left, y0), (width - right, y1), (213, 219, 224), 1)
        zero_y = _value_to_y(0.0, low, high, y0 + 5, y1 - 5)
        cv2.line(canvas, (left, zero_y), (width - right, zero_y), (226, 230, 233), 1, cv2.LINE_AA)
        if filtered.size >= 2:
            decimated = decimate_for_width(filtered, plot_width)
            xs = np.linspace(left + 1, width - right - 1, decimated.size).astype(np.int32)
            ys = np.asarray(
                [_value_to_y(float(value), low, high, y0 + 5, y1 - 5) for value in decimated],
                dtype=np.int32,
            )
            points = np.column_stack((xs, ys))
            cv2.polylines(canvas, [points], False, color, 1, cv2.LINE_AA)
        current = float(display[-1]) if display.size else float("nan")
        peak_to_peak = float(np.ptp(display)) if display.size else float("nan")
        mouse = f"  {channel.mouse_id}" if channel.mouse_id else ""
        cv2.putText(
            canvas,
            f"{channel.name} [{channel.role}]{mouse}",
            (12, y0 + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            color,
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"{current:+.2f} {units}  p-p {peak_to_peak:.2f}",
            (12, y0 + 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (82, 88, 94),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"{high:+.2g}",
            (width - right - 58, y0 + 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (116, 121, 126),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"{low:+.2g}",
            (width - right - 58, y1 - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.36,
            (116, 121, 126),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        canvas,
        f"{elapsed_s:,.1f} s    {samples_written:,} samples    {sample_rate_hz:g} Hz    Q / Esc stop",
        (12, height - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (62, 68, 74),
        1,
        cv2.LINE_AA,
    )
    return canvas


def display_values(
    output_voltage_v: np.ndarray,
    channel: ChannelConfig,
    sample_rate_hz: float,
    config: RecordingConfig,
) -> tuple[np.ndarray, str]:
    values = np.asarray(output_voltage_v, dtype=np.float64)
    if config.preview.display_units == "input_uv":
        return values * channel.input_uv_per_output_v, "uV"
    return values, "V"


def filter_for_preview(
    values: np.ndarray,
    role: str,
    sample_rate_hz: float,
    config: RecordingConfig,
) -> np.ndarray:
    signal = np.asarray(values, dtype=np.float64)
    if not config.preview.filter_display_only or signal.size < max(30, int(sample_rate_hz * 0.5)):
        return signal
    if find_spec("scipy") is None:
        return signal
    try:
        from scipy import signal as scipy_signal

        filtered = scipy_signal.detrend(signal, type="linear")
        notch_hz = config.preview.notch_hz
        if notch_hz and 0 < notch_hz < sample_rate_hz / 2:
            for harmonic in range(1, 4):
                target = notch_hz * harmonic
                if target >= sample_rate_hz / 2:
                    break
                b, a = scipy_signal.iirnotch(target, config.preview.notch_q, fs=sample_rate_hz)
                filtered = scipy_signal.filtfilt(b, a, filtered)
        bandpass = (
            config.preview.emg_bandpass_hz
            if role.upper() == "EMG"
            else config.preview.eeg_bandpass_hz
            if role.upper() in {"EEG", "EOG"}
            else None
        )
        if bandpass and bandpass[1] < sample_rate_hz / 2:
            sos = scipy_signal.butter(2, bandpass, btype="bandpass", fs=sample_rate_hz, output="sos")
            filtered = scipy_signal.sosfiltfilt(sos, filtered)
        return np.asarray(filtered, dtype=np.float64)
    except Exception:
        return signal


def robust_range(values: np.ndarray, *, auto_scale: bool) -> tuple[float, float]:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return -1.0, 1.0
    if auto_scale:
        low, high = np.percentile(finite, [1.0, 99.0])
    else:
        low, high = float(np.min(finite)), float(np.max(finite))
    center = (float(low) + float(high)) / 2.0
    half = max((float(high) - float(low)) * 0.65, abs(center) * 0.05, 1e-9)
    return center - half, center + half


def decimate_for_width(values: np.ndarray, width: int) -> np.ndarray:
    signal = np.asarray(values)
    target = max(2, int(width) * 2)
    if signal.size <= target:
        return signal
    step = max(1, int(math.ceil(signal.size / target)))
    return signal[::step]


def hex_to_bgr(value: str) -> tuple[int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        return 243, 164, 32
    try:
        red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return 243, 164, 32
    return blue, green, red


def _value_to_y(value: float, low: float, high: float, top: int, bottom: int) -> int:
    fraction = (value - low) / max(high - low, 1e-12)
    return int(round(bottom - np.clip(fraction, 0.0, 1.0) * (bottom - top)))
