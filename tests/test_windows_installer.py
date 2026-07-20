from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALLER = SCRIPTS / "install_windows.ps1"
STARTER = SCRIPTS / "start_web_ui.ps1"
UNINSTALLER = SCRIPTS / "uninstall_local_env.ps1"
HARNESS = Path(__file__).with_name("windows_installer_harness.ps1")
POWERSHELL = shutil.which("powershell.exe")


pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is required")


def run_powershell_file(script: Path, *arguments: str, cwd: Path | None = None):
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *arguments,
        ],
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


def run_harness(mode: str, *arguments: str):
    return run_powershell_file(
        HARNESS,
        "-Mode",
        mode,
        "-InstallerPath",
        str(INSTALLER),
        *arguments,
    )


def test_installer_entry_points_and_required_switches_exist():
    assert (ROOT / "Install-BioAgent.cmd").is_file()
    assert (ROOT / "Start-BioAgent.cmd").is_file()
    assert INSTALLER.is_file()
    assert STARTER.is_file()
    assert UNINSTALLER.is_file()

    source = INSTALLER.read_text(encoding="utf-8-sig")
    for switch in (
        "Developer",
        "RunTests",
        "RecreateVenv",
        "SkipBrowserInstall",
        "AllowWingetInstall",
        "PythonPath",
        "PipIndexUrl",
        "Proxy",
        "NonInteractive",
    ):
        assert f"${switch}" in source


def test_project_root_resolution_supports_spaces_and_unicode(tmp_path: Path):
    project = tmp_path / "Bio Agent 空格"
    script_dir = project / "scripts"
    script_dir.mkdir(parents=True)
    copied_installer = script_dir / INSTALLER.name
    shutil.copy2(INSTALLER, copied_installer)

    result = run_powershell_file(
        HARNESS,
        "-Mode",
        "root",
        "-InstallerPath",
        str(copied_installer),
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == project.resolve()


def test_supported_python_versions_are_explicit():
    result = run_harness("versions")
    assert result.returncode == 0, result.stderr
    versions = json.loads(result.stdout.strip())
    assert versions == {
        "python311": True,
        "python312": True,
        "python313": False,
        "python2": False,
    }


def test_python_path_probe_accepts_supported_interpreter_when_available():
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        pytest.skip("test runner is not a supported installer Python")
    result = run_harness("python-probe", "-Value", sys.executable)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip())["Supported"] is True


def test_python_path_probe_rejects_invalid_file(tmp_path: Path):
    invalid = tmp_path / "not-python.exe"
    invalid.write_text("not an executable", encoding="ascii")
    result = run_harness("python-probe", "-Value", str(invalid))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "null"


def test_cleanup_path_guard_blocks_sibling(tmp_path: Path):
    project = tmp_path / "project"
    child = project / ".venv"
    sibling = tmp_path / "project-other" / ".venv"
    child.mkdir(parents=True)
    sibling.mkdir(parents=True)

    allowed = run_harness(
        "path-guard", "-ProjectRootOverride", str(project), "-Value", str(child)
    )
    blocked = run_harness(
        "path-guard", "-ProjectRootOverride", str(project), "-Value", str(sibling)
    )
    assert allowed.stdout.strip() == "allowed"
    assert blocked.stdout.strip() == "blocked"


def test_existing_venv_is_recognized_in_unicode_project_path(tmp_path: Path):
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        pytest.skip("test runner is not a supported installer Python")
    project = tmp_path / "项目 空格"
    venv_python = project / ".venv" / "Scripts" / "python.exe"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(project / ".venv")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    result = run_harness(
        "venv-probe",
        "-ProjectRootOverride",
        str(project),
        "-Value",
        str(venv_python),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_checked_command_reports_native_failure():
    result = run_harness("command-failure")
    assert result.returncode == 0, result.stderr
    assert "failed-as-expected" in result.stdout
    assert "unexpected-success" not in result.stdout


def test_environment_file_is_not_overwritten(tmp_path: Path):
    (tmp_path / ".env.example").write_text("EXAMPLE=1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("USER_VALUE=keep\n", encoding="utf-8")
    result = run_harness("env-file", "-ProjectRootOverride", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".env").read_text(encoding="utf-8-sig") == "USER_VALUE=keep\n"


def test_installer_failure_creates_log_and_returns_nonzero(tmp_path: Path):
    project = tmp_path / "installer project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(INSTALLER, scripts / INSTALLER.name)
    (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")

    result = run_powershell_file(
        scripts / INSTALLER.name,
        "-PythonPath",
        str(project / "missing-python.exe"),
        "-NonInteractive",
    )
    assert result.returncode != 0
    logs = list((project / "output" / "install").glob("install-*.log"))
    assert len(logs) == 1
    assert "失败步骤" in logs[0].read_text(encoding="utf-8-sig")


def test_start_script_fails_when_venv_is_missing(tmp_path: Path):
    project = tmp_path / "start project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(STARTER, scripts / STARTER.name)

    result = run_powershell_file(scripts / STARTER.name, "-NoBrowser")
    assert result.returncode != 0
    assert "Install-BioAgent.cmd" in result.stdout


def test_start_script_detects_an_occupied_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = run_harness(
            "start-port",
            "-StartScriptPath",
            str(STARTER),
            "-Value",
            str(listener.getsockname()[1]),
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_uninstall_is_preview_only_and_preserves_user_data(tmp_path: Path):
    project = tmp_path / "cleanup project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(UNINSTALLER, scripts / UNINSTALLER.name)
    venv_marker = project / ".venv" / "marker.txt"
    database = project / "runtime" / "agent.db"
    evidence = project / "output" / "playwright" / "evidence.png"
    profile = project / "runtime" / "sessions" / "biorender-profile" / "state"
    for item in (venv_marker, database, evidence, profile):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("keep", encoding="ascii")

    preview = run_powershell_file(scripts / UNINSTALLER.name)
    assert preview.returncode == 0, preview.stderr
    assert venv_marker.exists()

    cleanup = run_powershell_file(scripts / UNINSTALLER.name, "-ConfirmCleanup")
    assert cleanup.returncode == 0, cleanup.stderr
    assert not venv_marker.exists()
    assert database.exists()
    assert evidence.exists()
    assert profile.exists()


def test_installer_recreate_and_skip_browser_guards_are_present():
    source = INSTALLER.read_text(encoding="utf-8-sig")
    assert "elseif (-not $RecreateVenv)" in source
    assert "if ($SkipBrowserInstall)" in source
    assert '"install", "chromium"' in source
    assert "Remove-Item -LiteralPath $safeVenvPath" in source
