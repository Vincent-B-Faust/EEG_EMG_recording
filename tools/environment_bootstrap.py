#!/usr/bin/env python
"""Locate or provision the Python environment used by the Windows launchers.

This module intentionally uses only the Python standard library.  It can run
from a system Python, an active Conda environment, or a Conda base interpreter
before the recorder dependencies have been installed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHARED_ENVIRONMENT = "thermocamera_public"
DEFAULT_STANDALONE_NAME = "eeg_emg_recording"
REQUIRED_IMPORTS = ("numpy", "h5py", "cv2", "yaml", "scipy", "nidaqmx", "tkinter")


class BootstrapError(RuntimeError):
    """Raised when an environment cannot be located or provisioned."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    mode: str
    shared_environment: str
    explicit_prefix: Path | None
    standalone_prefix: Path
    conda_executable: Path | None
    search_roots: tuple[Path, ...]
    python_version: str


@dataclass(frozen=True)
class CondaInstallation:
    executable: Path
    environments: tuple[Path, ...]


@dataclass(frozen=True)
class EnvironmentResolution:
    prefix: Path
    python: Path
    source: str
    backend: str
    conda_executable: Path | None = None


def _env_path(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    return Path(os.path.expandvars(os.path.expanduser(value.strip()))).resolve()


def _split_paths(value: str | None) -> tuple[Path, ...]:
    if not value:
        return ()
    paths: list[Path] = []
    for item in value.split(os.pathsep):
        path = _env_path(item)
        if path is not None:
            paths.append(path)
    return tuple(paths)


def load_settings(
    environ: Mapping[str, str] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Settings:
    env = os.environ if environ is None else environ
    mode = env.get("EEG_EMG_ENV_MODE", "auto").strip().lower()
    if mode not in {"auto", "shared", "standalone"}:
        raise BootstrapError(
            "EEG_EMG_ENV_MODE must be auto, shared, or standalone; "
            f"received {mode!r}."
        )

    root = project_root.resolve()
    standalone = _env_path(env.get("EEG_EMG_STANDALONE_PREFIX"))
    if standalone is None:
        standalone = root / ".runtime" / "envs" / DEFAULT_STANDALONE_NAME

    return Settings(
        project_root=root,
        mode=mode,
        shared_environment=(
            env.get("EEG_EMG_SHARED_ENV_NAME", DEFAULT_SHARED_ENVIRONMENT).strip()
            or DEFAULT_SHARED_ENVIRONMENT
        ),
        explicit_prefix=_env_path(env.get("EEG_EMG_ENV_PREFIX")),
        standalone_prefix=standalone.resolve(),
        conda_executable=_env_path(env.get("EEG_EMG_CONDA_EXE")),
        search_roots=_split_paths(env.get("EEG_EMG_ENV_SEARCH_ROOTS")),
        python_version=env.get("EEG_EMG_PYTHON_VERSION", "3.10").strip() or "3.10",
    )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _deduplicate(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _path_key(path)
        if key not in seen:
            result.append(path)
            seen.add(key)
    return result


def windows_drive_roots() -> tuple[Path, ...]:
    """Return mounted Windows drive roots without assuming a system drive."""
    if os.name != "nt":
        return (Path(Path.cwd().anchor or "/"),)
    mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    return tuple(Path(f"{chr(65 + index)}:\\") for index in range(26) if mask & (1 << index))


def _registry_python_roots() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []

    roots: list[Path] = []
    hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    keys = (
        r"SOFTWARE\Python\ContinuumAnalytics",
        r"SOFTWARE\WOW6432Node\Python\ContinuumAnalytics",
        r"SOFTWARE\Python\PythonCore",
        r"SOFTWARE\WOW6432Node\Python\PythonCore",
    )
    for hive in hives:
        for key_name in keys:
            try:
                with winreg.OpenKey(hive, key_name) as root_key:
                    child_count = winreg.QueryInfoKey(root_key)[0]
                    for index in range(child_count):
                        child_name = winreg.EnumKey(root_key, index)
                        for suffix in ("", r"InstallPath"):
                            child_path = f"{key_name}\\{child_name}"
                            if suffix:
                                child_path += f"\\{suffix}"
                            try:
                                with winreg.OpenKey(hive, child_path) as child_key:
                                    value, _ = winreg.QueryValueEx(child_key, "")
                                if value:
                                    roots.append(Path(value))
                            except OSError:
                                continue
            except OSError:
                continue
    return roots


def _common_install_roots(
    environ: Mapping[str, str],
    drive_roots: Sequence[Path],
) -> list[Path]:
    roots: list[Path] = []
    for variable in ("CONDA_PREFIX", "USERPROFILE", "LOCALAPPDATA", "PROGRAMDATA", "ProgramFiles"):
        value = environ.get(variable)
        if value:
            roots.append(Path(value))

    distribution_names = ("Miniconda3", "Anaconda3", "miniconda3", "anaconda3")
    expanded: list[Path] = []
    for root in roots:
        expanded.append(root)
        expanded.extend(root / name for name in distribution_names)

    for drive in drive_roots:
        expanded.extend(drive / name for name in distribution_names)
        expanded.extend(drive / "ProgramData" / name for name in distribution_names)
        users = drive / "Users"
        if users.is_dir():
            try:
                for user_dir in users.iterdir():
                    if user_dir.is_dir():
                        expanded.extend(user_dir / name for name in distribution_names)
            except OSError:
                pass
    return expanded


def _conda_paths_from_root(root: Path) -> tuple[Path, ...]:
    return (
        root / "Scripts" / "conda.exe",
        root / "condabin" / "conda.bat",
        root / "bin" / "conda",
    )


def discover_conda_candidates(
    settings: Settings,
    environ: Mapping[str, str] | None = None,
    drive_roots: Sequence[Path] | None = None,
) -> list[Path]:
    env = os.environ if environ is None else environ
    drives = windows_drive_roots() if drive_roots is None else tuple(drive_roots)
    candidates: list[Path] = []

    if settings.conda_executable is not None:
        candidates.append(settings.conda_executable)
    for variable in ("CONDA_EXE", "MAMBA_EXE"):
        value = env.get(variable)
        if value:
            candidates.append(Path(value))
    for command in ("conda.exe", "conda", "mamba.exe", "micromamba.exe"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    roots = _common_install_roots(env, drives)
    roots.extend(_registry_python_roots())
    for search_root in settings.search_roots:
        roots.append(search_root)
        roots.extend(
            search_root / name
            for name in ("Miniconda3", "Anaconda3", "miniconda3", "anaconda3")
        )
    roots.extend((Path(sys.prefix), Path(sys.executable).resolve().parent))
    for root in roots:
        candidates.extend(_conda_paths_from_root(root))
        if root.name.lower() in {"scripts", "condabin", "bin"}:
            candidates.extend(_conda_paths_from_root(root.parent))

    return [path.resolve() for path in _deduplicate(candidates) if path.is_file()]


def _conda_command(executable: Path, *arguments: str) -> list[str]:
    command = [str(executable), *arguments]
    if os.name == "nt" and executable.suffix.lower() in {".bat", ".cmd"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command]
    return command


def _run(
    command: Sequence[str],
    *,
    capture_output: bool = False,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
        env=None if env is None else dict(env),
    )


def query_conda_installation(executable: Path) -> CondaInstallation | None:
    try:
        result = _run(
            _conda_command(executable, "env", "list", "--json"),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        environments = tuple(
            _deduplicate(Path(item).resolve() for item in payload.get("envs", []) if item)
        )
        return CondaInstallation(executable=executable.resolve(), environments=environments)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def discover_conda_installations(candidates: Sequence[Path]) -> list[CondaInstallation]:
    installations: list[CondaInstallation] = []
    seen_environment_sets: set[tuple[str, ...]] = set()
    for executable in candidates:
        installation = query_conda_installation(executable)
        if installation is None:
            continue
        signature = tuple(sorted(_path_key(path) for path in installation.environments))
        if signature in seen_environment_sets:
            continue
        seen_environment_sets.add(signature)
        installations.append(installation)
    return installations


def _registered_environment_paths(environ: Mapping[str, str]) -> list[Path]:
    paths: list[Path] = []
    user_profile = environ.get("USERPROFILE") or environ.get("HOME")
    if not user_profile:
        return paths
    registry = Path(user_profile) / ".conda" / "environments.txt"
    try:
        for line in registry.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value:
                paths.append(Path(value).resolve())
    except OSError:
        pass
    return paths


def environment_python(prefix: Path) -> Path | None:
    candidates = (prefix / "python.exe", prefix / "bin" / "python")
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _environment_backend(prefix: Path) -> str:
    if (prefix / "conda-meta").is_dir():
        return "conda"
    if (prefix / "pyvenv.cfg").is_file():
        return "venv"
    return "python-prefix"


def _candidate_environment_paths(
    settings: Settings,
    installations: Sequence[CondaInstallation],
    environ: Mapping[str, str],
) -> list[tuple[Path, str, Path | None]]:
    candidates: list[tuple[Path, str, Path | None]] = []
    if settings.explicit_prefix is not None:
        candidates.append((settings.explicit_prefix, "configured-prefix", None))

    for installation in installations:
        for prefix in installation.environments:
            candidates.append((prefix, "conda-registry", installation.executable))
    for prefix in _registered_environment_paths(environ):
        candidates.append((prefix, "user-conda-registry", None))

    active_prefix = _env_path(environ.get("CONDA_PREFIX"))
    if active_prefix is not None:
        candidates.append((active_prefix, "active-conda-prefix", None))

    roots: list[Path] = list(settings.search_roots)
    for installation in installations:
        executable = installation.executable
        conda_root = executable.parent.parent if executable.parent.name.lower() in {"scripts", "condabin", "bin"} else executable.parent
        roots.append(conda_root)
    for root in _deduplicate(roots):
        candidates.extend(
            (
                (root, "configured-search-root", None),
                (root / settings.shared_environment, "search-root", None),
                (root / "envs" / settings.shared_environment, "search-root", None),
                (root / ".conda" / "envs" / settings.shared_environment, "search-root", None),
            )
        )

    candidates.append((settings.standalone_prefix, "project-standalone", None))
    deduplicated: list[tuple[Path, str, Path | None]] = []
    seen: set[str] = set()
    for prefix, source, conda_executable in candidates:
        key = _path_key(prefix)
        if key not in seen:
            deduplicated.append((prefix, source, conda_executable))
            seen.add(key)
    return deduplicated


def resolve_environment(
    settings: Settings,
    installations: Sequence[CondaInstallation],
    environ: Mapping[str, str] | None = None,
) -> EnvironmentResolution | None:
    env = os.environ if environ is None else environ
    candidates = _candidate_environment_paths(settings, installations, env)

    if settings.explicit_prefix is not None:
        explicit = next((item for item in candidates if item[1] == "configured-prefix"), None)
        if explicit is not None:
            python = environment_python(explicit[0])
            if python is not None:
                return EnvironmentResolution(
                    prefix=explicit[0].resolve(),
                    python=python,
                    source=explicit[1],
                    backend=_environment_backend(explicit[0]),
                    conda_executable=explicit[2],
                )
        return None

    def select(predicate) -> EnvironmentResolution | None:
        for prefix, source, conda_executable in candidates:
            if not predicate(prefix, source):
                continue
            python = environment_python(prefix)
            if python is not None:
                return EnvironmentResolution(
                    prefix=prefix.resolve(),
                    python=python,
                    source=source,
                    backend=_environment_backend(prefix),
                    conda_executable=conda_executable,
                )
        return None

    if settings.mode in {"auto", "shared"}:
        shared_name = settings.shared_environment.casefold()
        shared = select(lambda prefix, _source: prefix.name.casefold() == shared_name)
        if shared is not None:
            return shared
        if settings.mode == "shared":
            return None

    return select(lambda prefix, _source: _path_key(prefix) == _path_key(settings.standalone_prefix))


def _python_version(python: Path) -> tuple[int, int] | None:
    try:
        result = _run(
            [str(python), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            return None
        major, minor = result.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _minimum_version(settings: Settings) -> tuple[int, int]:
    try:
        major, minor = settings.python_version.split(".", 1)
        return int(major), int(minor)
    except ValueError as exc:
        raise BootstrapError(
            f"EEG_EMG_PYTHON_VERSION must look like 3.10, received {settings.python_version!r}."
        ) from exc


def _check_imports(python: Path) -> tuple[bool, str]:
    modules = ",".join(REQUIRED_IMPORTS)
    script = (
        "import importlib.util,sys; "
        f"mods={modules!r}.split(','); "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "print(','.join(missing)); sys.exit(1 if missing else 0)"
    )
    try:
        result = _run([str(python), "-c", script], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, result.stdout.strip() or result.stderr.strip()


def _check_package(python: Path) -> tuple[bool, str]:
    try:
        result = _run(
            [str(python), "-c", "import eeg_emg_recording"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return result.returncode == 0, result.stderr.strip()


def _create_environment(
    settings: Settings,
    installations: Sequence[CondaInstallation],
) -> EnvironmentResolution:
    if settings.mode == "shared" and settings.explicit_prefix is None:
        raise BootstrapError(
            f"Shared environment {settings.shared_environment!r} was not found. "
            "Use EEG_EMG_ENV_MODE=auto to allow a project-local fallback, or set "
            "EEG_EMG_ENV_PREFIX to the exact shared environment path."
        )

    prefix = settings.explicit_prefix or settings.standalone_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    conda = installations[0].executable if installations else None
    if conda is not None:
        print(f"Creating standalone Conda environment: {prefix}", flush=True)
        result = _run(
            _conda_command(
                conda,
                "create",
                "--yes",
                "--prefix",
                str(prefix),
                f"python={settings.python_version}",
                "pip",
            )
        )
        if result.returncode != 0:
            raise BootstrapError(f"Conda environment creation failed with exit code {result.returncode}.")
        backend = "conda"
    else:
        required = _minimum_version(settings)
        current = sys.version_info[:2]
        if current < required:
            raise BootstrapError(
                f"Conda was not found and bootstrap Python {current[0]}.{current[1]} is older "
                f"than required Python {required[0]}.{required[1]}. Configure "
                "EEG_EMG_CONDA_EXE or EEG_EMG_BOOTSTRAP_PYTHON."
            )
        print(f"Conda was not found; creating standalone venv: {prefix}", flush=True)
        result = _run([sys.executable, "-m", "venv", str(prefix)])
        if result.returncode != 0:
            raise BootstrapError(f"venv creation failed with exit code {result.returncode}.")
        backend = "venv"

    python = environment_python(prefix)
    if python is None:
        raise BootstrapError(f"Environment was created but Python was not found under {prefix}.")
    return EnvironmentResolution(
        prefix=prefix.resolve(),
        python=python,
        source="new-project-standalone" if settings.explicit_prefix is None else "new-configured-prefix",
        backend=backend,
        conda_executable=conda,
    )


def _install_project(settings: Settings, resolution: EnvironmentResolution) -> None:
    version = _python_version(resolution.python)
    minimum = _minimum_version(settings)
    if version is None or version < minimum:
        found = "unknown" if version is None else f"{version[0]}.{version[1]}"
        raise BootstrapError(
            f"Environment Python is {found}; this project requires Python {minimum[0]}.{minimum[1]}+."
        )
    print(f"Installing EEG-EMG recorder into: {resolution.prefix}", flush=True)
    result = _run(
        [
            str(resolution.python),
            "-m",
            "pip",
            "install",
            "-e",
            f"{settings.project_root}[nidaq,test]",
        ]
    )
    if result.returncode != 0:
        raise BootstrapError(f"Package installation failed with exit code {result.returncode}.")


def select_compatible_environment(
    settings: Settings,
    installations: Sequence[CondaInstallation],
) -> EnvironmentResolution:
    resolution = resolve_environment(settings, installations)
    if resolution is None:
        resolution = _create_environment(settings, installations)

    version = _python_version(resolution.python)
    minimum = _minimum_version(settings)
    if version is None or version < minimum:
        is_shared_auto_selection = (
            settings.mode == "auto"
            and settings.explicit_prefix is None
            and resolution.prefix.name.casefold() == settings.shared_environment.casefold()
        )
        if is_shared_auto_selection:
            print(
                f"Shared environment Python is too old; using standalone fallback at "
                f"{settings.standalone_prefix}.",
                flush=True,
            )
            standalone_settings = Settings(
                project_root=settings.project_root,
                mode="standalone",
                shared_environment=settings.shared_environment,
                explicit_prefix=None,
                standalone_prefix=settings.standalone_prefix,
                conda_executable=settings.conda_executable,
                search_roots=settings.search_roots,
                python_version=settings.python_version,
            )
            resolution = resolve_environment(standalone_settings, installations)
            if resolution is None:
                resolution = _create_environment(standalone_settings, installations)
        else:
            found = "unknown" if version is None else f"{version[0]}.{version[1]}"
            raise BootstrapError(
                f"Selected environment Python is {found}; Python {minimum[0]}.{minimum[1]}+ is required."
            )

    selected_version = _python_version(resolution.python)
    if selected_version is None or selected_version < minimum:
        found = (
            "unknown"
            if selected_version is None
            else f"{selected_version[0]}.{selected_version[1]}"
        )
        raise BootstrapError(
            f"Standalone environment Python is {found}; Python {minimum[0]}.{minimum[1]}+ is required."
        )
    return resolution


def ensure_environment(
    settings: Settings,
    installations: Sequence[CondaInstallation],
) -> EnvironmentResolution:
    resolution = select_compatible_environment(settings, installations)

    imports_ok, _ = _check_imports(resolution.python)
    package_ok, _ = _check_package(resolution.python)
    if not imports_ok or not package_ok:
        _install_project(settings, resolution)
        imports_ok, details = _check_imports(resolution.python)
        if not imports_ok:
            raise BootstrapError(f"Dependencies are still unavailable after installation: {details}")
        package_ok, details = _check_package(resolution.python)
        if not package_ok:
            raise BootstrapError(f"Project is still not importable after installation: {details}")
    return resolution


def _discover(settings: Settings) -> tuple[list[Path], list[CondaInstallation]]:
    candidates = discover_conda_candidates(settings)
    return candidates, discover_conda_installations(candidates)


def _resolution_payload(resolution: EnvironmentResolution | None) -> dict[str, object] | None:
    if resolution is None:
        return None
    payload = asdict(resolution)
    return {key: str(value) if isinstance(value, Path) else value for key, value in payload.items()}


def command_doctor(settings: Settings, *, as_json: bool) -> int:
    candidates, installations = _discover(settings)
    resolution = resolve_environment(settings, installations)
    imports_ok = False
    import_details = "environment not found"
    package_ok = False
    package_details = "environment not found"
    version: tuple[int, int] | None = None
    if resolution is not None:
        version = _python_version(resolution.python)
        imports_ok, import_details = _check_imports(resolution.python)
        package_ok, package_details = _check_package(resolution.python)

    payload = {
        "project_root": str(settings.project_root),
        "mode": settings.mode,
        "shared_environment": settings.shared_environment,
        "configured_prefix": str(settings.explicit_prefix) if settings.explicit_prefix else None,
        "standalone_prefix": str(settings.standalone_prefix),
        "conda_candidates": [str(path) for path in candidates],
        "conda_installations": [
            {
                "executable": str(item.executable),
                "environments": [str(path) for path in item.environments],
            }
            for item in installations
        ],
        "selected_environment": _resolution_payload(resolution),
        "python_version": list(version) if version else None,
        "dependencies_available": imports_ok,
        "dependency_details": import_details,
        "project_importable": package_ok,
        "project_import_details": package_details,
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("EEG-EMG environment self-check")
        print(f"  Project: {settings.project_root}")
        print(f"  Mode: {settings.mode}")
        print(f"  Conda installations found: {len(installations)}")
        if resolution is None:
            print("  Selected environment: not found")
            print(f"  Standalone fallback: {settings.standalone_prefix}")
        else:
            print(f"  Selected environment: {resolution.prefix}")
            print(f"  Source/backend: {resolution.source} / {resolution.backend}")
            print(f"  Python: {resolution.python}")
            print(f"  Dependencies: {'ready' if imports_ok else 'missing'}")
            if import_details:
                print(f"  Details: {import_details}")
            print(f"  Project import: {'ready' if package_ok else 'missing'}")
            if package_details:
                print(f"  Project details: {package_details}")
    minimum = _minimum_version(settings)
    return (
        0
        if resolution is not None
        and version is not None
        and version >= minimum
        and imports_ok
        and package_ok
        else 1
    )


def command_install(settings: Settings) -> int:
    _candidates, installations = _discover(settings)
    resolution = select_compatible_environment(settings, installations)
    _install_project(settings, resolution)
    print(f"Environment ready: {resolution.prefix}")
    result = _run([str(resolution.python), "-m", "eeg_emg_recording", "--version"])
    return result.returncode


def command_run(settings: Settings, arguments: Sequence[str]) -> int:
    command_arguments = list(arguments)
    if command_arguments and command_arguments[0] == "--":
        command_arguments.pop(0)
    if not command_arguments:
        raise BootstrapError("run requires a Python command after --")

    _candidates, installations = _discover(settings)
    resolution = ensure_environment(settings, installations)
    print(
        f"Environment: {resolution.prefix} ({resolution.source}, {resolution.backend})",
        flush=True,
    )
    child_env = dict(os.environ)
    source_dir = str(settings.project_root / "src")
    old_pythonpath = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = source_dir if not old_pythonpath else source_dir + os.pathsep + old_pythonpath
    return _run([str(resolution.python), *command_arguments], env=child_env).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="Report Conda and environment discovery results.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    subparsers.add_parser("install", help="Locate or create an environment and install this project.")
    run = subparsers.add_parser("run", help="Run a Python command in the resolved environment.")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "doctor":
            return command_doctor(settings, as_json=bool(args.json))
        if args.command == "install":
            return command_install(settings)
        if args.command == "run":
            return command_run(settings, args.arguments)
        raise BootstrapError(f"Unknown command: {args.command}")
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
