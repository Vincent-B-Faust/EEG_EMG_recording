from __future__ import annotations

import os
import queue
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np

from .config import config_from_gui_dict, load_config, save_config
from .devices import format_device_report, list_devices, validate_hardware
from .models import ChannelConfig, PreviewUpdate, ROLE_COLORS
from .preview import (
    RollingSignalBuffer,
    decimate_for_width,
    display_values,
    filter_for_preview,
    robust_range,
)
from .recorder import Recorder
from .gui_form_lock import FormLock


def launch_gui(config_path: str | Path | None = None) -> int:
    import tkinter as tk

    root = tk.Tk()
    RecorderGui(root, config_path=config_path)
    root.mainloop()
    return 0


class RecorderGui:
    def __init__(self, root: Any, *, config_path: str | Path | None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.root.title("EEG-EMG Sleep Recording")
        self.root.geometry("1380x900")
        self.root.minsize(1080, 720)
        default = Path(__file__).resolve().parents[2] / "configs" / "ni_usb6001_dam50.yaml"
        self.config_path = Path(config_path).expanduser().resolve() if config_path else default
        self.config = load_config(self.config_path)
        self._preview_config = self.config
        self.channel_rows: list[_ChannelRow] = []
        self.preview_queue: queue.Queue[PreviewUpdate] = queue.Queue(maxsize=3)
        self.result_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.recorder: Recorder | None = None
        self.latest_session: Path | None = None
        self.preview_buffer: RollingSignalBuffer | None = None
        self.latest_preview_values: np.ndarray | None = None
        self.latest_preview_update: PreviewUpdate | None = None
        self._close_when_done = False
        self._run_active = False
        self._form_lock = FormLock()
        self._setup_roots = []
        self._build_style()
        self._build_variables()
        self._build_layout()
        self._load_into_fields(self.config)
        self._refresh_devices(silent=True)
        self.root.after(50, self._poll_queues)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_style(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except self.tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
        style.configure("Status.TLabel", font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))

    def _build_variables(self) -> None:
        tk = self.tk
        self.path_var = tk.StringVar(value=str(self.config_path))
        self.output_var = tk.StringVar()
        self.session_var = tk.StringVar()
        self.mouse_var = tk.StringVar()
        self.backend_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.rate_var = tk.StringVar()
        self.block_var = tk.StringVar()
        self.duration_var = tk.StringVar()
        self.terminal_var = tk.StringVar()
        self.range_var = tk.StringVar()
        self.units_var = tk.StringVar()
        self.history_var = tk.StringVar()
        self.notch_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.hardware_var = tk.StringVar(value="")

    def _build_layout(self) -> None:
        ttk = self.ttk
        root = self.root
        toolbar = ttk.Frame(root, padding=(12, 10, 12, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="EEG-EMG Sleep Recording", style="Title.TLabel").pack(side="left")
        ttk.Button(toolbar, text="Open data folder", command=self._open_latest_folder).pack(side="right", padx=(6, 0))
        self.stop_button = ttk.Button(toolbar, text="Stop", command=self._stop, state="disabled")
        self.stop_button.pack(side="right", padx=(6, 0))
        self.start_button = ttk.Button(toolbar, text="Start recording", command=self._start, style="Primary.TButton")
        self.start_button.pack(side="right", padx=(6, 0))
        for label, command in (("Save config", self._save), ("Open config", self._choose_config)):
            button = ttk.Button(toolbar, text=label, command=command)
            button.pack(side="right", padx=(6, 0))
            self._setup_roots.append(button)

        separator = ttk.Separator(root)
        separator.pack(fill="x")
        body = ttk.Panedwindow(root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=10)
        controls = ttk.Frame(body, width=460)
        self._setup_roots.append(controls)
        preview = ttk.Frame(body)
        body.add(controls, weight=0)
        body.add(preview, weight=1)
        self._build_controls(controls)
        self._build_preview(preview)

        status = ttk.Frame(root, padding=(12, 5, 12, 8))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(status, textvariable=self.hardware_var, style="Status.TLabel").pack(side="right")

    def _build_controls(self, parent: Any) -> None:
        ttk = self.ttk
        fields = ttk.Frame(parent)
        fields.pack(fill="x", padx=(0, 12))
        row = 0

        def add(label: str, variable: Any, *, values: tuple[str, ...] | None = None) -> Any:
            nonlocal row
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if values is None:
                widget = ttk.Entry(fields, textvariable=variable, width=30)
            else:
                widget = ttk.Combobox(fields, textvariable=variable, values=values, state="readonly", width=27)
            widget.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
            row += 1
            return widget

        add("Output root", self.output_var)
        add("Session name", self.session_var)
        add("Mouse IDs", self.mouse_var)
        add("Backend", self.backend_var, values=("nidaqmx", "fake"))
        self.device_box = add("NI device", self.device_var, values=("auto",))
        device_buttons = ttk.Frame(fields)
        device_buttons.grid(row=row, column=1, sticky="e", pady=(0, 4))
        ttk.Button(device_buttons, text="Refresh NI", command=self._refresh_devices).pack(side="right")
        row += 1
        add("Sample rate (Hz)", self.rate_var)
        add("Block duration (s)", self.block_var)
        add("Duration (s; blank = manual)", self.duration_var)
        add("Terminal mode", self.terminal_var, values=("DIFF", "RSE", "NRSE", "PSEUDODIFFERENTIAL"))
        add("Input range (V)", self.range_var)
        add("Preview units", self.units_var, values=("input_uv", "output_v"))
        add("Preview history (s)", self.history_var)
        add("Display notch (Hz)", self.notch_var)
        fields.columnconfigure(1, weight=1)

        channel_header = ttk.Frame(parent)
        channel_header.pack(fill="x", padx=(0, 12), pady=(14, 4))
        ttk.Label(channel_header, text="Channels", font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Button(channel_header, text="+ EMG", command=lambda: self._add_channel("EMG")).pack(side="right", padx=(4, 0))
        ttk.Button(channel_header, text="+ EEG", command=lambda: self._add_channel("EEG")).pack(side="right", padx=(4, 0))
        ttk.Button(channel_header, text="Remove", command=self._remove_channel).pack(side="right", padx=(4, 0))
        self.channel_frame = ttk.Frame(parent)
        self.channel_frame.pack(fill="both", expand=True, padx=(0, 12))
        columns = ttk.Frame(self.channel_frame)
        columns.pack(fill="x", pady=(0, 2))
        for column, (label, width) in enumerate(
            (
                ("", 2),
                ("Role", 6),
                ("Mouse", 9),
                ("Name", 13),
                ("NI input", 9),
                ("Gain", 7),
                ("", 11),
            )
        ):
            ttk.Label(columns, text=label, width=width).grid(
                row=0,
                column=column,
                padx=2,
                sticky="w",
            )

    def _build_preview(self, parent: Any) -> None:
        ttk = self.ttk
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(header, text="Live preview", font=("Segoe UI Semibold", 11)).pack(side="left")
        self.preview_status = ttk.Label(header, text="")
        self.preview_status.pack(side="right")
        self.preview_canvas = self.tk.Canvas(
            parent,
            background="#FAFBFC",
            highlightthickness=1,
            highlightbackground="#D5DCE2",
        )
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _event: self._draw_preview())

    def _load_into_fields(self, config: Any) -> None:
        self.output_var.set(str(config.output_root))
        self.session_var.set(config.session_name)
        self.mouse_var.set(", ".join(config.mouse_ids))
        acquisition = config.acquisition
        self.backend_var.set(acquisition.backend)
        self.device_var.set(acquisition.device)
        self.rate_var.set(f"{acquisition.sample_rate_hz:g}")
        self.block_var.set(f"{acquisition.block_duration_s:g}")
        self.duration_var.set("" if acquisition.duration_s is None else f"{acquisition.duration_s:g}")
        self.terminal_var.set(acquisition.terminal_config)
        self.range_var.set(f"{acquisition.input_min_v:g}, {acquisition.input_max_v:g}")
        self.units_var.set(config.preview.display_units)
        self.history_var.set(f"{config.preview.history_s:g}")
        self.notch_var.set("" if config.preview.notch_hz is None else f"{config.preview.notch_hz:g}")
        for row in self.channel_rows:
            row.destroy()
        self.channel_rows.clear()
        for channel in acquisition.channels:
            self._append_channel_row(channel)

    def _append_channel_row(self, channel: ChannelConfig) -> None:
        row = _ChannelRow(self.channel_frame, channel, len(self.channel_rows))
        row.frame.pack(fill="x", pady=2)
        self.channel_rows.append(row)

    def _add_channel(self, role: str) -> None:
        index = len(self.channel_rows)
        mouse = self.mouse_var.get().split(",")[0].strip()
        channel = ChannelConfig(
            name=f"{mouse + '_' if mouse else ''}{role}{index + 1}",
            role=role,
            mouse_id=mouse,
            physical_channel=f"ai{index}",
            amplifier_gain=1000.0,
        )
        self._append_channel_row(channel)

    def _remove_channel(self) -> None:
        if len(self.channel_rows) <= 1:
            return
        row = self.channel_rows.pop()
        row.destroy()

    def _choose_config(self) -> None:
        from tkinter import filedialog, messagebox

        if self._run_active:
            return

        value = filedialog.askopenfilename(
            title="Open EEG/EMG configuration",
            initialdir=str(self.config_path.parent),
            filetypes=(("YAML", "*.yaml *.yml"), ("All files", "*.*")),
        )
        if not value:
            return
        try:
            new_path = Path(value).resolve()
            new_config = load_config(new_path)
            self.config_path, self.config = new_path, new_config
            self.latest_preview_values = None
            self.latest_preview_update = None
            self.preview_buffer = None
            self.path_var.set(str(self.config_path))
            self._load_into_fields(self.config)
            self._draw_preview()
            self.status_var.set(f"Loaded {self.config_path.name}")
        except Exception as exc:
            messagebox.showerror("Config error", str(exc))

    def _save(self) -> bool:
        from tkinter import messagebox

        if self._run_active:
            self.status_var.set("Settings are locked until recording finishes / 记录结束前设置锁定")
            return False

        try:
            config = self._config_from_fields()
            save_config(config, self.config_path)
            self.config = config
            self.status_var.set(f"Saved {self.config_path.name}")
            return True
        except Exception as exc:
            messagebox.showerror("Config error", str(exc))
            return False

    def _config_from_fields(self) -> Any:
        raw = deepcopy(self.config.raw)
        project = raw.setdefault("project", {})
        acquisition = raw.setdefault("acquisition", {})
        preview = raw.setdefault("preview", {})
        project["output_root"] = self.output_var.get().strip()
        project["session_name"] = self.session_var.get().strip()
        project["mouse_ids"] = [
            item.strip() for item in self.mouse_var.get().split(",") if item.strip()
        ]
        acquisition["backend"] = self.backend_var.get().strip()
        acquisition["device"] = self.device_var.get().strip() or "auto"
        acquisition["sample_rate_hz"] = float(self.rate_var.get())
        acquisition["block_duration_s"] = float(self.block_var.get())
        duration = self.duration_var.get().strip()
        acquisition["duration_s"] = float(duration) if duration else None
        acquisition["terminal_config"] = self.terminal_var.get().strip()
        range_parts = [float(item.strip()) for item in self.range_var.get().split(",")]
        if len(range_parts) != 2:
            raise ValueError("Input range must contain two values, for example -10, 10")
        acquisition["input_range_v"] = range_parts
        acquisition["channels"] = [row.to_dict() for row in self.channel_rows]
        preview["enabled"] = True
        preview["display_units"] = self.units_var.get().strip()
        preview["history_s"] = float(self.history_var.get())
        notch = self.notch_var.get().strip()
        preview["notch_hz"] = float(notch) if notch else None
        return config_from_gui_dict(raw, self.config_path)

    def _refresh_devices(self, *, silent: bool = False) -> None:
        from tkinter import messagebox

        try:
            devices = list_devices()
            analog = [device.name for device in devices if device.supports_analog_input]
            values = tuple(dict.fromkeys(("auto", self.device_var.get(), *analog)))
            self.device_box.configure(values=values)
            self.hardware_var.set(
                f"NI analog: {', '.join(analog)}" if analog else "NI analog: not detected"
            )
            if not silent:
                messagebox.showinfo("NI-DAQmx devices", "\n".join(format_device_report(devices)))
        except Exception as exc:
            self.hardware_var.set("NI hardware check failed")
            if not silent:
                messagebox.showerror("NI-DAQmx error", str(exc))

    def _start(self) -> None:
        from tkinter import messagebox

        if self._run_active or self._close_when_done or (self.worker is not None and self.worker.is_alive()):
            return
        try:
            config = self._config_from_fields()
            if config.acquisition.backend == "nidaqmx":
                validate_hardware(config)
            save_config(config, self.config_path)
        except Exception as exc:
            messagebox.showerror("Cannot start", str(exc))
            return
        self.config = config
        self._preview_config = config
        self.stop_event = threading.Event()
        self.preview_buffer = RollingSignalBuffer(
            len(config.acquisition.active_channels),
            config.acquisition.sample_rate_hz,
            config.preview.history_s,
        )
        self.latest_preview_values = None
        self.latest_preview_update = None
        self._draw_preview()
        while not self.preview_queue.empty():
            try:
                self.preview_queue.get_nowait()
            except queue.Empty:
                break
        self._run_active = True
        self._form_lock.acquire(self._setup_roots)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Starting recorder...")
        self.worker = threading.Thread(target=self._record_worker, args=(config,), daemon=False)
        try:
            self.worker.start()
        except Exception as exc:
            self._recording_finished()
            messagebox.showerror("Cannot start worker", str(exc))

    def _record_worker(self, config: Any) -> None:
        try:
            self.recorder = Recorder(
                config,
                stop_event=self.stop_event,
                preview_callback=self._queue_preview,
            )
            self.latest_session = self.recorder.session_dir
            self.result_queue.put(("started", self.latest_session))
            result = self.recorder.run()
            self.result_queue.put(("result", result))
        except Exception as exc:
            self.result_queue.put(("error", exc))
        finally:
            self.recorder = None

    def _queue_preview(self, update: PreviewUpdate) -> bool:
        try:
            while True:
                self.preview_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.preview_queue.put_nowait(update)
        except queue.Full:
            pass
        return not bool(self.stop_event and self.stop_event.is_set())

    def _stop(self) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
            self.status_var.set("Stopping after the current acquisition block...")
            self.stop_button.configure(state="disabled")

    def _poll_queues(self) -> None:
        preview_changed = False
        try:
            while True:
                update = self.preview_queue.get_nowait()
                self.latest_preview_update = update
                preview_changed = True
                if self.preview_buffer is not None:
                    self.latest_preview_values = self.preview_buffer.append(update.block)
        except queue.Empty:
            pass
        try:
            while True:
                kind, payload = self.result_queue.get_nowait()
                if kind == "started":
                    if not (self.stop_event and self.stop_event.is_set()):
                        self.status_var.set(f"Recording: {payload}")
                elif kind == "result":
                    self.status_var.set(
                        f"{payload.status}: {payload.samples_written:,} samples -> {payload.session_dir}"
                    )
                    self._recording_finished()
                elif kind == "error":
                    from tkinter import messagebox

                    self.status_var.set(f"Failed: {payload}")
                    self._recording_finished()
                    messagebox.showerror("Recording failed", str(payload))
        except queue.Empty:
            pass
        if self._run_active and self.worker is not None and not self.worker.is_alive():
            self._recording_finished()
        if preview_changed:
            self._draw_preview()
        if self._close_when_done and not (self.worker and self.worker.is_alive()):
            self.root.destroy()
            return
        self.root.after(50, self._poll_queues)

    def _recording_finished(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return  # Result may reach Tk before the worker's finally block exits.
        self._run_active = False
        self._form_lock.release()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.stop_event = None

    def _draw_preview(self) -> None:
        canvas = self.preview_canvas
        canvas.delete("all")
        width = max(200, canvas.winfo_width())
        height = max(200, canvas.winfo_height())
        if self.latest_preview_values is None or self.latest_preview_update is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Waiting for signal",
                fill="#7A8793",
                font=("Segoe UI", 13),
            )
            return
        update = self.latest_preview_update
        values = self.latest_preview_values
        channels = list(update.channels)
        count = max(1, len(channels))
        left = 155
        right = 18
        top = 14
        footer = 30
        gap = 8
        row_height = max(42, (height - top - footer - gap * (count - 1)) / count)
        for index, channel in enumerate(channels):
            y0 = top + index * (row_height + gap)
            y1 = min(height - footer, y0 + row_height)
            raw = np.asarray(values[index], dtype=float)
            display, units = display_values(raw, channel, update.sample_rate_hz, self._preview_config)
            filtered = filter_for_preview(
                display,
                channel.role,
                update.sample_rate_hz,
                self._preview_config,
            )
            low, high = robust_range(filtered, auto_scale=True)
            color = channel.display_color
            canvas.create_rectangle(left, y0, width - right, y1, outline="#D5DCE2")
            zero_y = _tk_value_to_y(0.0, low, high, y0 + 5, y1 - 5)
            canvas.create_line(left, zero_y, width - right, zero_y, fill="#E7EBEE")
            trace = decimate_for_width(filtered, max(20, width - left - right))
            if trace.size >= 2:
                points: list[float] = []
                x_span = width - right - left
                for point_index, value in enumerate(trace):
                    x = left + point_index * x_span / max(1, trace.size - 1)
                    y = _tk_value_to_y(float(value), low, high, y0 + 5, y1 - 5)
                    points.extend((x, y))
                canvas.create_line(*points, fill=color, width=1, smooth=False)
            current = float(display[-1]) if display.size else float("nan")
            peak_to_peak = float(np.ptp(display)) if display.size else float("nan")
            mouse = f" / {channel.mouse_id}" if channel.mouse_id else ""
            canvas.create_text(
                10,
                y0 + 16,
                text=f"{channel.name} [{channel.role}]{mouse}",
                anchor="w",
                fill=color,
                font=("Segoe UI Semibold", 9),
            )
            canvas.create_text(
                10,
                y0 + 37,
                text=f"{current:+.2f} {units}  p-p {peak_to_peak:.2f}",
                anchor="w",
                fill="#52585E",
                font=("Segoe UI", 8),
            )
        self.preview_status.configure(
            text=f"{update.elapsed_s:,.1f} s | {update.samples_written:,} samples | {update.sample_rate_hz:g} Hz"
        )

    def _open_latest_folder(self) -> None:
        target = self.latest_session or self.config.output_root
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def _on_close(self) -> None:
        from tkinter import messagebox

        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("Stop recording", "Stop the active recording and close?"):
                return
            self._close_when_done = True
            self._stop()
            return
        self.root.destroy()


class _ChannelRow:
    def __init__(self, parent: Any, channel: ChannelConfig, index: int):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.frame = ttk.Frame(parent)
        self.enabled = tk.BooleanVar(value=channel.enabled)
        self.mouse = tk.StringVar(value=channel.mouse_id)
        self.role = tk.StringVar(value=channel.role)
        self.name = tk.StringVar(value=channel.name)
        self.physical = tk.StringVar(value=channel.physical_channel)
        self.gain = tk.StringVar(value=f"{channel.amplifier_gain:g}")
        self.amplifier_model = tk.StringVar(value=channel.amplifier_model)
        self.amplifier_serial = tk.StringVar(value=channel.amplifier_serial)
        self.amplifier_mode = tk.StringVar(value=channel.amplifier_mode)
        self.hardware_highpass_hz = tk.StringVar(
            value=_optional_number_text(channel.hardware_highpass_hz)
        )
        self.hardware_lowpass_hz = tk.StringVar(
            value=_optional_number_text(channel.hardware_lowpass_hz)
        )
        self.hardware_filter_enabled = tk.BooleanVar(
            value=channel.hardware_filter_enabled
        )
        self.hardware_notch_enabled = tk.BooleanVar(
            value=channel.hardware_notch_enabled
        )
        self.hardware_notch_hz = tk.StringVar(
            value=_optional_number_text(channel.hardware_notch_hz)
        )
        self.amplifier_settings_verified = tk.BooleanVar(
            value=channel.amplifier_settings_verified
        )
        self.amplifier_notes = channel.amplifier_notes
        self.terminal_config = tk.StringVar(value=channel.terminal_config or "")
        self.input_min_v = tk.StringVar(value=_optional_number_text(channel.input_min_v))
        self.input_max_v = tk.StringVar(value=_optional_number_text(channel.input_max_v))
        self.color = channel.color
        self.summary_var = tk.StringVar()
        ttk.Checkbutton(self.frame, variable=self.enabled).grid(row=0, column=0, padx=(0, 3))
        ttk.Combobox(
            self.frame,
            textvariable=self.role,
            values=tuple(ROLE_COLORS),
            state="readonly",
            width=5,
        ).grid(row=0, column=1, padx=2)
        ttk.Entry(self.frame, textvariable=self.mouse, width=9).grid(row=0, column=2, padx=2)
        ttk.Entry(self.frame, textvariable=self.name, width=13).grid(row=0, column=3, padx=2)
        ttk.Entry(self.frame, textvariable=self.physical, width=9).grid(row=0, column=4, padx=2)
        ttk.Entry(self.frame, textvariable=self.gain, width=7).grid(row=0, column=5, padx=2)
        ttk.Button(
            self.frame,
            text="Amplifier...",
            command=self._edit_amplifier,
            width=11,
        ).grid(row=0, column=6, padx=(4, 0))
        ttk.Label(
            self.frame,
            textvariable=self.summary_var,
            foreground="#626A70",
        ).grid(row=1, column=2, columnspan=5, padx=2, pady=(1, 3), sticky="w")
        for variable in (
            self.gain,
            self.amplifier_model,
            self.amplifier_mode,
            self.hardware_highpass_hz,
            self.hardware_lowpass_hz,
            self.hardware_notch_hz,
        ):
            variable.trace_add("write", lambda *_args: self._update_summary())
        for variable in (
            self.hardware_filter_enabled,
            self.hardware_notch_enabled,
            self.amplifier_settings_verified,
        ):
            variable.trace_add("write", lambda *_args: self._update_summary())
        for variable in (self.gain, self.physical, self.role):
            variable.trace_add(
                "write",
                lambda *_args: self.amplifier_settings_verified.set(False),
            )
        self._update_summary()

    def _edit_amplifier(self) -> None:
        from tkinter import messagebox

        tk = self.tk
        ttk = self.ttk
        owner = self.frame.winfo_toplevel()
        window = tk.Toplevel(owner)
        window.title(f"Amplifier settings - {self.name.get().strip() or 'channel'}")
        window.transient(owner)
        window.resizable(False, False)

        text_values = {
            "model": tk.StringVar(value=self.amplifier_model.get()),
            "serial": tk.StringVar(value=self.amplifier_serial.get()),
            "gain": tk.StringVar(value=self.gain.get()),
            "mode": tk.StringVar(value=self.amplifier_mode.get()),
            "highpass": tk.StringVar(value=self.hardware_highpass_hz.get()),
            "lowpass": tk.StringVar(value=self.hardware_lowpass_hz.get()),
            "notch_hz": tk.StringVar(value=self.hardware_notch_hz.get()),
            "terminal": tk.StringVar(value=self.terminal_config.get() or "INHERIT"),
            "input_min": tk.StringVar(value=self.input_min_v.get()),
            "input_max": tk.StringVar(value=self.input_max_v.get()),
        }
        filter_enabled = tk.BooleanVar(value=self.hardware_filter_enabled.get())
        notch_enabled = tk.BooleanVar(value=self.hardware_notch_enabled.get())
        verified = tk.BooleanVar(value=self.amplifier_settings_verified.get())
        for variable in (
            text_values["model"],
            text_values["serial"],
            text_values["gain"],
            text_values["mode"],
            text_values["highpass"],
            text_values["lowpass"],
            text_values["notch_hz"],
            text_values["terminal"],
            text_values["input_min"],
            text_values["input_max"],
            filter_enabled,
            notch_enabled,
        ):
            variable.trace_add("write", lambda *_args: verified.set(False))

        content = ttk.Frame(window, padding=14)
        content.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            content,
            text=(
                "Metadata only. Enter the physical amplifier settings after adjusting "
                "the hardware. This software does not control the DAM50, and HDF5 "
                "always stores unmodified DAQ output volts."
            ),
            wraplength=520,
            foreground="#4D5962",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        row = 1

        def add_field(
            label: str,
            variable: Any,
            *,
            values: tuple[str, ...] | None = None,
        ) -> None:
            nonlocal row
            ttk.Label(content, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if values is None:
                widget = ttk.Entry(content, textvariable=variable, width=38)
            else:
                widget = ttk.Combobox(
                    content,
                    textvariable=variable,
                    values=values,
                    state="readonly",
                    width=35,
                )
            widget.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
            row += 1

        add_field("Amplifier model", text_values["model"])
        add_field("Serial / asset ID", text_values["serial"])
        add_field("Physical gain (x)", text_values["gain"])
        add_field(
            "Coupling / mode",
            text_values["mode"],
            values=("AC", "DC", "UNKNOWN", "OTHER"),
        )
        ttk.Checkbutton(
            content,
            text="Hardware filter enabled",
            variable=filter_enabled,
        ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=4)
        row += 1
        add_field("Hardware high-pass (Hz; blank = unknown)", text_values["highpass"])
        add_field("Hardware low-pass (Hz; blank = unknown)", text_values["lowpass"])
        ttk.Checkbutton(
            content,
            text="Hardware notch enabled",
            variable=notch_enabled,
        ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=4)
        row += 1
        add_field("Hardware notch (Hz; blank = none)", text_values["notch_hz"])
        add_field(
            "DAQ terminal override",
            text_values["terminal"],
            values=("INHERIT", "DIFF", "RSE", "NRSE", "PSEUDODIFFERENTIAL"),
        )
        add_field("DAQ input minimum (V; blank = global)", text_values["input_min"])
        add_field("DAQ input maximum (V; blank = global)", text_values["input_max"])
        ttk.Checkbutton(
            content,
            text="Confirmed against the physical hardware",
            variable=verified,
        ).grid(row=row, column=1, sticky="w", padx=(12, 0), pady=4)
        row += 1
        ttk.Label(content, text="Notes").grid(row=row, column=0, sticky="nw", pady=4)
        notes = tk.Text(content, width=38, height=4, wrap="word")
        notes.insert("1.0", self.amplifier_notes)
        notes.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=4)
        row += 1

        buttons = ttk.Frame(content)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(
            side="right",
            padx=(6, 0),
        )

        def save_and_close() -> None:
            try:
                gain = _gui_float(text_values["gain"].get(), "Physical gain")
                highpass = _gui_optional_float(
                    text_values["highpass"].get(),
                    "Hardware high-pass",
                )
                lowpass = _gui_optional_float(
                    text_values["lowpass"].get(),
                    "Hardware low-pass",
                )
                notch_hz = _gui_optional_float(
                    text_values["notch_hz"].get(),
                    "Hardware notch",
                )
                input_min = _gui_optional_float(
                    text_values["input_min"].get(),
                    "DAQ input minimum",
                )
                input_max = _gui_optional_float(
                    text_values["input_max"].get(),
                    "DAQ input maximum",
                )
                if gain <= 0:
                    raise ValueError("Physical gain must be greater than zero")
                if highpass is not None and highpass < 0:
                    raise ValueError("Hardware high-pass cannot be negative")
                if lowpass is not None and lowpass <= 0:
                    raise ValueError("Hardware low-pass must be greater than zero")
                if (
                    filter_enabled.get()
                    and highpass is not None
                    and lowpass is not None
                    and highpass >= lowpass
                ):
                    raise ValueError("Hardware high-pass must be below low-pass")
                if notch_hz is not None and notch_hz <= 0:
                    raise ValueError("Hardware notch must be greater than zero")
                if notch_enabled.get() and notch_hz is None:
                    raise ValueError(
                        "Enter the hardware notch frequency or disable hardware notch"
                    )
                if (input_min is None) != (input_max is None):
                    raise ValueError(
                        "Set both DAQ input minimum and maximum, or leave both blank"
                    )
                if (
                    input_min is not None
                    and input_max is not None
                    and input_min >= input_max
                ):
                    raise ValueError("DAQ input minimum must be below maximum")
            except ValueError as exc:
                messagebox.showerror("Amplifier settings", str(exc), parent=window)
                return

            self.amplifier_model.set(text_values["model"].get().strip())
            self.amplifier_serial.set(text_values["serial"].get().strip())
            self.gain.set(f"{gain:g}")
            self.amplifier_mode.set(text_values["mode"].get().strip().upper())
            self.hardware_filter_enabled.set(bool(filter_enabled.get()))
            self.hardware_highpass_hz.set(_optional_number_text(highpass))
            self.hardware_lowpass_hz.set(_optional_number_text(lowpass))
            self.hardware_notch_enabled.set(bool(notch_enabled.get()))
            self.hardware_notch_hz.set(_optional_number_text(notch_hz))
            self.terminal_config.set(
                ""
                if text_values["terminal"].get() == "INHERIT"
                else text_values["terminal"].get()
            )
            self.input_min_v.set(_optional_number_text(input_min))
            self.input_max_v.set(_optional_number_text(input_max))
            self.amplifier_settings_verified.set(bool(verified.get()))
            self.amplifier_notes = notes.get("1.0", "end-1c").strip()
            self._update_summary()
            window.destroy()

        ttk.Button(buttons, text="Apply", command=save_and_close).pack(side="right")
        content.columnconfigure(1, weight=1)
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.wait_visibility()
        window.grab_set()

    def to_dict(self) -> dict[str, Any]:
        data = {
            "enabled": bool(self.enabled.get()),
            "mouse_id": self.mouse.get().strip(),
            "role": self.role.get().strip().upper(),
            "name": self.name.get().strip(),
            "physical_channel": self.physical.get().strip(),
            "amplifier_gain": _gui_float(self.gain.get(), "Physical gain"),
            "amplifier_model": self.amplifier_model.get().strip(),
            "amplifier_serial": self.amplifier_serial.get().strip(),
            "amplifier_mode": self.amplifier_mode.get().strip().upper(),
            "hardware_filter_enabled": bool(self.hardware_filter_enabled.get()),
            "hardware_highpass_hz": _gui_optional_float(
                self.hardware_highpass_hz.get(),
                "Hardware high-pass",
            ),
            "hardware_lowpass_hz": _gui_optional_float(
                self.hardware_lowpass_hz.get(),
                "Hardware low-pass",
            ),
            "hardware_notch_enabled": bool(self.hardware_notch_enabled.get()),
            "hardware_notch_hz": _gui_optional_float(
                self.hardware_notch_hz.get(),
                "Hardware notch",
            ),
            "amplifier_settings_verified": bool(
                self.amplifier_settings_verified.get()
            ),
            "amplifier_notes": self.amplifier_notes,
            "terminal_config": self.terminal_config.get().strip() or None,
            "input_min_v": _gui_optional_float(
                self.input_min_v.get(),
                "DAQ input minimum",
            ),
            "input_max_v": _gui_optional_float(
                self.input_max_v.get(),
                "DAQ input maximum",
            ),
        }
        if self.color:
            data["color"] = self.color
        return data

    def _update_summary(self) -> None:
        model = self.amplifier_model.get().strip() or "Unspecified amplifier"
        mode = self.amplifier_mode.get().strip() or "UNKNOWN"
        gain = self.gain.get().strip() or "?"
        if self.hardware_filter_enabled.get():
            highpass = self.hardware_highpass_hz.get().strip() or "?"
            lowpass = self.hardware_lowpass_hz.get().strip() or "?"
            filter_text = f"filter {highpass}-{lowpass} Hz"
        else:
            filter_text = "filter bypassed"
        notch_text = (
            f", notch {self.hardware_notch_hz.get().strip() or '?'} Hz"
            if self.hardware_notch_enabled.get()
            else ""
        )
        verified = (
            "confirmed"
            if self.amplifier_settings_verified.get()
            else "not confirmed"
        )
        self.summary_var.set(
            f"{model} | {mode} | {gain}x | {filter_text}{notch_text} | {verified}"
        )

    def destroy(self) -> None:
        self.frame.destroy()


def _optional_number_text(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _gui_float(value: str, label: str) -> float:
    try:
        result = float(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not np.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _gui_optional_float(value: str, label: str) -> float | None:
    text = str(value).strip()
    return None if not text else _gui_float(text, label)


def _tk_value_to_y(value: float, low: float, high: float, top: float, bottom: float) -> float:
    fraction = (value - low) / max(high - low, 1e-12)
    return bottom - float(np.clip(fraction, 0.0, 1.0)) * (bottom - top)
