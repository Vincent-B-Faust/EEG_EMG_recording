from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .alignment import align_to_thermal_session
from .config import apply_overrides, load_config
from .control import publish_event, request_stop
from .devices import format_device_report, list_devices, validate_hardware
from .preview import OpenCvPreview
from .recorder import Recorder
from .storage import export_csv, inspect_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eeg-emg-recording",
        description="NI-DAQmx EEG/EMG sleep recording with live preview and thermal synchronization.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    gui = sub.add_parser("gui", help="Open the standalone recorder control window.")
    gui.add_argument("--config", default=None)

    devices = sub.add_parser("list-devices", help="List NI-DAQmx devices and channels.")
    devices.add_argument("--json", action="store_true")

    validate = sub.add_parser("validate", help="Validate YAML and currently connected NI hardware.")
    validate.add_argument("--config", required=True)
    validate.add_argument("--config-only", action="store_true")

    record = sub.add_parser("record", help="Run one standalone or synchronized recording.")
    record.add_argument("--config", required=True)
    record.add_argument("--session-dir", default=None, help="Use this exact empty session directory.")
    record.add_argument("--out-dir", default=None, help="Override project.output_root.")
    duration_group = record.add_mutually_exclusive_group()
    duration_group.add_argument("--duration-s", type=float, default=None)
    duration_group.add_argument(
        "--until-stop",
        action="store_true",
        help="Ignore the configured duration and record until a local or control stop request.",
    )
    record.add_argument("--fake", action="store_true")
    preview_group = record.add_mutually_exclusive_group()
    preview_group.add_argument("--preview", action="store_true")
    preview_group.add_argument("--no-preview", action="store_true")
    record.add_argument("--control-dir", default=None)
    record.add_argument("--parent-session", default=None)

    inspect = sub.add_parser("inspect", help="Inspect a completed or interrupted session.")
    inspect.add_argument("--session", required=True)
    inspect.add_argument("--json", action="store_true")

    export = sub.add_parser("export-csv", help="Export HDF5 voltage data to CSV.")
    export.add_argument("--session", required=True)
    export.add_argument("--output", default=None)
    export.add_argument("--decimation", type=int, default=1)

    align = sub.add_parser("align", help="Align one EEG/EMG session to a thermal session.")
    align.add_argument("--session", required=True)
    align.add_argument("--thermal-session", default=None)
    align.add_argument("--output", default=None)

    stop = sub.add_parser("stop", help="Request a synchronized recorder to stop cleanly.")
    stop.add_argument("--control-dir", required=True)
    stop.add_argument("--reason", default="external_request")

    event = sub.add_parser("event", help="Publish a timestamped synchronization event.")
    event.add_argument("--control-dir", required=True)
    event.add_argument("--name", required=True)
    event.add_argument("--source", default="external")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "gui":
            from .gui import launch_gui

            return launch_gui(args.config)
        if args.command == "list-devices":
            devices = list_devices()
            if args.json:
                print(json.dumps([device.to_dict() for device in devices], ensure_ascii=False, indent=2))
            else:
                for line in format_device_report(devices):
                    print(line)
            return 0
        if args.command == "validate":
            config = load_config(args.config)
            report = {"config": "valid"}
            if not args.config_only:
                report["hardware"] = validate_hardware(config)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.command == "record":
            return _record(args)
        if args.command == "inspect":
            metadata = inspect_session(args.session)
            if args.json:
                print(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
            else:
                print(f"Session: {metadata.get('session_dir')}")
                print(f"Status: {metadata.get('status')} ({metadata.get('stop_reason')})")
                print(f"Duration: {float(metadata.get('duration_s', 0.0)):.3f} s")
                print(f"Samples: {metadata.get('samples_written')}")
                print(f"Data shape: {metadata.get('hdf5_shape')}")
            return 0
        if args.command == "export-csv":
            path = export_csv(
                args.session,
                args.output,
                decimation=args.decimation,
            )
            print(f"CSV: {path}")
            return 0
        if args.command == "align":
            path = align_to_thermal_session(
                args.session,
                args.thermal_session,
                output=args.output,
            )
            print(f"Alignment: {path}")
            return 0
        if args.command == "stop":
            print(request_stop(args.control_dir, reason=args.reason))
            return 0
        if args.command == "event":
            print(publish_event(args.control_dir, args.name, source=args.source))
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _record(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    preview_override = True if args.preview else False if args.no_preview else None
    config = apply_overrides(
        config,
        output_root=args.out_dir,
        duration_s=args.duration_s,
        duration_unbounded=bool(args.until_stop),
        backend="fake" if args.fake else None,
        preview=preview_override,
        control_dir=args.control_dir,
        parent_session_dir=args.parent_session,
    )
    stop_event = threading.Event()
    recorder_holder: list[Recorder] = []

    def stop_handler(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            try:
                signal.signal(signal_value, stop_handler)
            except (OSError, ValueError):
                pass

    preview = OpenCvPreview(config) if config.preview.enabled else None
    recorder = Recorder(
        config,
        session_dir=args.session_dir,
        stop_event=stop_event,
        preview_callback=preview,
    )
    recorder_holder.append(recorder)
    try:
        result = recorder.run()
    finally:
        if preview is not None:
            preview.close()
    print(f"Session: {result.session_dir}")
    print(f"Status: {result.status} ({result.stop_reason})")
    print(f"Samples: {result.samples_written}")
    print(f"Signals: {result.signals_path}")
    return 0 if result.status in {"completed", "stopped", "interrupted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
