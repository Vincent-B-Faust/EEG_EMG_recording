from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "environment_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("environment_bootstrap", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


def make_environment(prefix: Path, *, conda: bool = True) -> Path:
    prefix.mkdir(parents=True)
    python = prefix / "python.exe"
    python.write_bytes(b"")
    if conda:
        (prefix / "conda-meta").mkdir()
    else:
        (prefix / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    return prefix


def settings(tmp_path: Path, **overrides):
    values = {
        "project_root": tmp_path / "project",
        "mode": "auto",
        "shared_environment": "thermocamera_public",
        "explicit_prefix": None,
        "standalone_prefix": tmp_path / "project" / ".runtime" / "envs" / "eeg_emg_recording",
        "conda_executable": None,
        "search_roots": (),
        "python_version": "3.10",
    }
    values.update(overrides)
    return bootstrap.Settings(**values)


def test_settings_default_standalone_is_on_project_path(tmp_path: Path):
    project = tmp_path / "portable-project"
    result = bootstrap.load_settings({}, project_root=project)

    assert result.mode == "auto"
    assert result.standalone_prefix == (
        project / ".runtime" / "envs" / "eeg_emg_recording"
    ).resolve()
    assert result.standalone_prefix.is_relative_to(project.resolve())


def test_explicit_prefix_has_priority_over_registered_shared_environment(tmp_path: Path):
    explicit = make_environment(tmp_path / "custom" / "recorder")
    shared = make_environment(tmp_path / "another-disk" / "thermocamera_public")
    installation = bootstrap.CondaInstallation(
        executable=tmp_path / "conda" / "Scripts" / "conda.exe",
        environments=(shared,),
    )

    result = bootstrap.resolve_environment(
        settings(tmp_path, explicit_prefix=explicit),
        [installation],
        environ={},
    )

    assert result is not None
    assert result.prefix == explicit.resolve()
    assert result.source == "configured-prefix"


def test_auto_mode_finds_registered_shared_environment_at_arbitrary_path(tmp_path: Path):
    shared = make_environment(tmp_path / "external-volume" / "envs" / "thermocamera_public")
    conda = tmp_path / "external-volume" / "Anaconda3" / "Scripts" / "conda.exe"
    installation = bootstrap.CondaInstallation(executable=conda, environments=(shared,))

    result = bootstrap.resolve_environment(settings(tmp_path), [installation], environ={})

    assert result is not None
    assert result.prefix == shared.resolve()
    assert result.conda_executable == conda
    assert result.source == "conda-registry"


def test_auto_mode_uses_existing_project_standalone_when_shared_is_absent(tmp_path: Path):
    standalone = make_environment(tmp_path / "portable" / "eeg_env", conda=False)

    result = bootstrap.resolve_environment(
        settings(tmp_path, standalone_prefix=standalone),
        [],
        environ={},
    )

    assert result is not None
    assert result.prefix == standalone.resolve()
    assert result.backend == "venv"
    assert result.source == "project-standalone"


def test_shared_mode_does_not_silently_select_standalone(tmp_path: Path):
    standalone = make_environment(tmp_path / "portable" / "eeg_env")

    result = bootstrap.resolve_environment(
        settings(tmp_path, mode="shared", standalone_prefix=standalone),
        [],
        environ={},
    )

    assert result is None


def test_search_root_discovers_conda_distribution_without_drive_assumption(
    tmp_path: Path,
):
    search_root = tmp_path / "software"
    conda = search_root / "Miniconda3" / "Scripts" / "conda.exe"
    conda.parent.mkdir(parents=True)
    conda.write_bytes(b"")
    configured = settings(tmp_path, search_roots=(search_root,))

    candidates = bootstrap.discover_conda_candidates(
        configured,
        environ={},
        drive_roots=(),
    )

    assert conda.resolve() in candidates


def test_independent_conda_creation_uses_project_prefix_not_environment_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    standalone = tmp_path / "portable-project" / ".runtime" / "envs" / "eeg_emg_recording"
    conda = tmp_path / "toolchain" / "Scripts" / "conda.exe"
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        command = list(command)
        commands.append(command)
        prefix = Path(command[command.index("--prefix") + 1])
        make_environment(prefix)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(bootstrap, "_run", fake_run)
    result = bootstrap._create_environment(
        settings(tmp_path, standalone_prefix=standalone),
        [bootstrap.CondaInstallation(executable=conda, environments=())],
    )

    assert result.prefix == standalone.resolve()
    assert "--prefix" in commands[0]
    assert commands[0][commands[0].index("--prefix") + 1] == str(standalone)
    assert "--name" not in commands[0]
    assert "-n" not in commands[0]


def test_environment_variable_configuration_has_no_username_or_drive_dependency(
    tmp_path: Path,
):
    project = tmp_path / "repo"
    explicit = tmp_path / "shared" / "thermocamera_public"
    conda = tmp_path / "toolchain" / "Scripts" / "conda.exe"
    result = bootstrap.load_settings(
        {
            "EEG_EMG_ENV_MODE": "standalone",
            "EEG_EMG_ENV_PREFIX": str(explicit),
            "EEG_EMG_CONDA_EXE": str(conda),
            "EEG_EMG_ENV_SEARCH_ROOTS": str(tmp_path / "one")
            + os.pathsep
            + str(tmp_path / "two"),
            "EEG_EMG_PYTHON_VERSION": "3.11",
        },
        project_root=project,
    )

    assert result.explicit_prefix == explicit.resolve()
    assert result.conda_executable == conda.resolve()
    assert len(result.search_roots) == 2
    assert result.python_version == "3.11"


@pytest.mark.parametrize(
    "filename",
    [
        "install_shared_env.cmd",
        "install_environment.cmd",
        "start_gui.cmd",
        "run_fake_demo.cmd",
        "list_ni_devices.cmd",
        "check_environment.cmd",
    ],
)
def test_windows_entry_points_delegate_to_environment_bootstrap(filename: str):
    content = (ROOT / filename).read_text(encoding="utf-8").lower()

    assert "environment_bootstrap.cmd" in content or "install_shared_env.cmd" in content
    assert "conda run" not in content
    assert "c:\\users\\" not in content


def test_invalid_environment_mode_is_rejected(tmp_path: Path):
    with pytest.raises(bootstrap.BootstrapError, match="ENV_MODE"):
        bootstrap.load_settings(
            {"EEG_EMG_ENV_MODE": "sometimes"},
            project_root=tmp_path,
        )
