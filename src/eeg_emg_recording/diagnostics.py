from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from scipy.integrate import trapezoid
from scipy.signal import coherence, welch


def analyze_session(
    session_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    line_frequency_hz: float = 50.0,
    line_bandwidth_hz: float = 2.0,
    max_frequency_hz: float = 100.0,
    amplifier_output_limit_v: float = 8.0,
    headroom_warning_fraction: float = 0.9,
    start_s: float = 0.0,
    end_s: float | None = None,
    line_power_warning_fraction: float = 0.1,
) -> dict[str, Any]:
    """Analyze one completed recording without modifying its raw data.

    A new, timestamped directory is used by default so repeated diagnostics remain
    traceable. An explicitly supplied output directory must not already exist.
    """

    session = Path(session_dir).resolve()
    metadata_path = session / "metadata.json"
    signals_path = session / "signals.h5"
    if not metadata_path.is_file() or not signals_path.is_file():
        raise FileNotFoundError("Session must contain metadata.json and signals.h5")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    sample_rate_hz = float(metadata["sample_rate_hz"])
    channels = list(metadata["channels"])
    dataset_name = str(metadata.get("files", {}).get("dataset", "/signals/output_voltage_v"))
    with h5py.File(signals_path, "r") as handle:
        values = np.asarray(handle[dataset_name], dtype=np.float64)
    if values.ndim == 2 and values.shape[1] == len(channels):
        values = values.T
    if values.ndim != 2 or values.shape[0] != len(channels):
        raise ValueError(
            f"Unexpected signal shape {values.shape}; expected channel-first data for {len(channels)} channels"
        )

    total_samples = values.shape[1]
    start_index = max(0, int(round(float(start_s) * sample_rate_hz)))
    end_index = total_samples if end_s is None else min(total_samples, int(round(float(end_s) * sample_rate_hz)))
    if start_index >= end_index:
        raise ValueError(
            f"Analysis window is empty: start_s={start_s:g}, end_s={end_s}, duration={total_samples / sample_rate_hz:g}"
        )
    values = values[:, start_index:end_index]

    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = session / f"analysis_{stamp}"
    else:
        destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False)

    summaries: list[dict[str, Any]] = []
    spectra: list[tuple[np.ndarray, np.ndarray, float]] = []
    for index, channel in enumerate(channels):
        signal_v = values[index]
        gain = float(channel.get("amplifier_gain") or 1.0)
        signal_uv = signal_v * 1e6 / gain
        centered_v = signal_v - np.mean(signal_v)
        nperseg = min(signal_v.size, max(256, int(round(sample_rate_hz * 4.0))))
        frequencies, psd_v = welch(
            centered_v,
            fs=sample_rate_hz,
            nperseg=nperseg,
            detrend="linear",
            scaling="density",
        )
        psd_uv = psd_v * (1e6 / gain) ** 2
        spectra.append((frequencies, psd_uv, gain))
        analysis_band = (frequencies >= 0.5) & (frequencies <= max_frequency_hz)
        line_band = (
            (frequencies >= line_frequency_hz - line_bandwidth_hz / 2.0)
            & (frequencies <= line_frequency_hz + line_bandwidth_hz / 2.0)
        )
        total_power = _band_power(frequencies, psd_v, analysis_band)
        line_power = _band_power(frequencies, psd_v, line_band)
        positive_band = analysis_band & (frequencies > 0)
        dominant_hz = (
            float(frequencies[positive_band][np.argmax(psd_v[positive_band])])
            if np.any(positive_band)
            else None
        )
        abs_peak_v = float(np.max(np.abs(signal_v)))
        ni_range = channel.get("input_max_v") or metadata.get("amplifier_settings", [{}])[index].get(
            "daq_input_range_v", [-10.0, 10.0]
        )[1]
        ni_range = abs(float(ni_range))
        line_power_fraction = line_power / total_power if total_power > 0 else None
        row = {
            "channel": str(channel.get("name", f"channel_{index + 1}")),
            "role": str(channel.get("role", "")),
            "samples": int(signal_v.size),
            "sample_rate_hz": sample_rate_hz,
            "gain": gain,
            "mean_output_v": float(np.mean(signal_v)),
            "minimum_output_v": float(np.min(signal_v)),
            "maximum_output_v": float(np.max(signal_v)),
            "peak_to_peak_output_v": float(np.ptp(signal_v)),
            "demeaned_rms_output_v": float(np.sqrt(np.mean(centered_v**2))),
            "minimum_input_uv": float(np.min(signal_uv)),
            "maximum_input_uv": float(np.max(signal_uv)),
            "peak_to_peak_input_uv": float(np.ptp(signal_uv)),
            "demeaned_rms_input_uv": float(np.sqrt(np.mean((signal_uv - np.mean(signal_uv)) ** 2))),
            "amplifier_headroom_used_fraction": abs_peak_v / amplifier_output_limit_v,
            "samples_beyond_amplifier_limit": int(np.count_nonzero(np.abs(signal_v) >= amplifier_output_limit_v)),
            "fraction_beyond_amplifier_limit": float(np.mean(np.abs(signal_v) >= amplifier_output_limit_v)),
            "samples_near_ni_limit": int(np.count_nonzero(np.abs(signal_v) >= ni_range * 0.99)),
            "fraction_near_ni_limit": float(np.mean(np.abs(signal_v) >= ni_range * 0.99)),
            "line_frequency_hz": line_frequency_hz,
            "line_band_power_fraction_0p5_to_max": line_power_fraction,
            "dominant_frequency_hz": dominant_hz,
            "warning_amplifier_headroom": abs_peak_v >= amplifier_output_limit_v * headroom_warning_fraction,
            "warning_ni_clipping": abs_peak_v >= ni_range * 0.99,
            "warning_line_noise": line_power_fraction is not None
            and line_power_fraction >= line_power_warning_fraction,
        }
        summaries.append(row)

    pairwise = _pairwise_coherence(values, channels, sample_rate_hz, line_frequency_hz)
    report = {
        "schema": "aki_eeg_emg_signal_diagnostics_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "session_dir": str(session),
        "source_metadata": str(metadata_path),
        "source_signals": str(signals_path),
        "parameters": {
            "line_frequency_hz": line_frequency_hz,
            "line_bandwidth_hz": line_bandwidth_hz,
            "max_frequency_hz": max_frequency_hz,
            "amplifier_output_limit_v": amplifier_output_limit_v,
            "headroom_warning_fraction": headroom_warning_fraction,
            "start_s": start_index / sample_rate_hz,
            "end_s": end_index / sample_rate_hz,
            "source_total_duration_s": total_samples / sample_rate_hz,
            "line_power_warning_fraction": line_power_warning_fraction,
        },
        "channels": summaries,
        "cross_channel_coherence": pairwise,
    }
    (destination / "signal_quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (destination / "signal_quality.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    _plot_overview(destination, values, channels, sample_rate_hz, spectra, line_frequency_hz, max_frequency_hz)
    report["output_dir"] = str(destination)
    report["plots"] = [
        str(destination / "waveforms_and_psd.png"),
        str(destination / "waveform_zoom_1s.png"),
        str(destination / "amplitude_distributions.png"),
    ]
    if len(channels) > 1:
        report["plots"].append(str(destination / "cross_channel_coherence.png"))
    return report


def _band_power(frequencies: np.ndarray, psd: np.ndarray, mask: np.ndarray) -> float:
    if np.count_nonzero(mask) < 2:
        return 0.0
    return float(trapezoid(psd[mask], frequencies[mask]))


def _plot_overview(
    destination: Path,
    values: np.ndarray,
    channels: list[dict[str, Any]],
    sample_rate_hz: float,
    spectra: list[tuple[np.ndarray, np.ndarray, float]],
    line_frequency_hz: float,
    max_frequency_hz: float,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    time_s = np.arange(values.shape[1], dtype=np.float64) / sample_rate_hz
    figure, axes = plt.subplots(len(channels), 2, figsize=(14, 4 * len(channels)), squeeze=False)
    for index, channel in enumerate(channels):
        gain = float(channel.get("amplifier_gain") or 1.0)
        signal_uv = values[index] * 1e6 / gain
        name = str(channel.get("name", f"channel_{index + 1}"))
        axes[index, 0].plot(time_s, signal_uv, linewidth=0.55, color=channel.get("color", None))
        axes[index, 0].set(title=f"{name} waveform", xlabel="Time (s)", ylabel="Estimated amplifier input (uV)")
        axes[index, 0].grid(alpha=0.2)
        frequencies, psd_uv, _gain = spectra[index]
        shown = (frequencies >= 0.1) & (frequencies <= max_frequency_hz)
        axes[index, 1].semilogy(frequencies[shown], np.maximum(psd_uv[shown], np.finfo(float).tiny))
        axes[index, 1].axvline(line_frequency_hz, color="crimson", linestyle="--", linewidth=1, label=f"{line_frequency_hz:g} Hz")
        axes[index, 1].set(title=f"{name} Welch PSD", xlabel="Frequency (Hz)", ylabel="PSD (uV^2/Hz)")
        axes[index, 1].grid(alpha=0.2)
        axes[index, 1].legend()
    figure.suptitle("EEG/EMG bring-up diagnostics (raw data; display conversion uses recorded gain)")
    figure.tight_layout()
    figure.savefig(destination / "waveforms_and_psd.png", dpi=180)
    plt.close(figure)

    zoom_samples = min(values.shape[1], max(1, int(round(sample_rate_hz))))
    figure, axes = plt.subplots(len(channels), 1, figsize=(12, 3.5 * len(channels)), squeeze=False)
    for index, channel in enumerate(channels):
        gain = float(channel.get("amplifier_gain") or 1.0)
        signal_uv = values[index, :zoom_samples] * 1e6 / gain
        name = str(channel.get("name", f"channel_{index + 1}"))
        axes[index, 0].plot(time_s[:zoom_samples], signal_uv, linewidth=0.8, color=channel.get("color", None))
        axes[index, 0].set(title=f"{name} first-second zoom", xlabel="Time (s)", ylabel="Estimated amplifier input (uV)")
        axes[index, 0].grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(destination / "waveform_zoom_1s.png", dpi=180)
    plt.close(figure)

    figure, axes = plt.subplots(len(channels), 1, figsize=(10, 3.5 * len(channels)), squeeze=False)
    for index, channel in enumerate(channels):
        gain = float(channel.get("amplifier_gain") or 1.0)
        signal_uv = values[index] * 1e6 / gain
        name = str(channel.get("name", f"channel_{index + 1}"))
        axes[index, 0].hist(signal_uv, bins=100, color=channel.get("color", None), alpha=0.85)
        axes[index, 0].set(title=f"{name} amplitude distribution", xlabel="Estimated amplifier input (uV)", ylabel="Samples")
        axes[index, 0].grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(destination / "amplitude_distributions.png", dpi=180)
    plt.close(figure)

    if len(channels) > 1:
        figure, axis = plt.subplots(figsize=(10, 5))
        for left in range(len(channels) - 1):
            for right in range(left + 1, len(channels)):
                frequencies, coherence_values = coherence(
                    values[left] - np.mean(values[left]),
                    values[right] - np.mean(values[right]),
                    fs=sample_rate_hz,
                    nperseg=min(values.shape[1], max(256, int(round(sample_rate_hz * 4.0)))),
                )
                shown = (frequencies >= 0.1) & (frequencies <= max_frequency_hz)
                axis.plot(
                    frequencies[shown],
                    coherence_values[shown],
                    label=f"{channels[left].get('name')} vs {channels[right].get('name')}",
                )
        axis.axvline(line_frequency_hz, color="crimson", linestyle="--", linewidth=1)
        axis.set(title="Cross-channel magnitude-squared coherence", xlabel="Frequency (Hz)", ylabel="Coherence", ylim=(0, 1.02))
        axis.grid(alpha=0.2)
        axis.legend()
        figure.tight_layout()
        figure.savefig(destination / "cross_channel_coherence.png", dpi=180)
        plt.close(figure)


def _pairwise_coherence(
    values: np.ndarray,
    channels: list[dict[str, Any]],
    sample_rate_hz: float,
    line_frequency_hz: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for left in range(len(channels) - 1):
        for right in range(left + 1, len(channels)):
            frequencies, coherence_values = coherence(
                values[left] - np.mean(values[left]),
                values[right] - np.mean(values[right]),
                fs=sample_rate_hz,
                nperseg=min(values.shape[1], max(256, int(round(sample_rate_hz * 4.0)))),
            )
            line_index = int(np.argmin(np.abs(frequencies - line_frequency_hz)))
            second_index = int(np.argmin(np.abs(frequencies - 2.0 * line_frequency_hz)))
            results.append(
                {
                    "left_channel": str(channels[left].get("name", f"channel_{left + 1}")),
                    "right_channel": str(channels[right].get("name", f"channel_{right + 1}")),
                    "coherence_at_line_frequency": float(coherence_values[line_index]),
                    "coherence_at_second_harmonic": float(coherence_values[second_index]),
                }
            )
    return results
