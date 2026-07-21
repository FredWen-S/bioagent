from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BUILD_TIME = os.getenv("BIOAGENT_BUILD_TIME") or datetime.now(UTC).isoformat()


def _git_value(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def runtime_version_info(static_root: Path) -> dict[str, object]:
    commit = os.getenv("BIOAGENT_GIT_COMMIT") or _git_value("rev-parse", "HEAD")
    branch = os.getenv("BIOAGENT_GIT_BRANCH") or _git_value("branch", "--show-current")
    dirty = bool(_git_value("status", "--porcelain"))
    return {
        "git_commit": commit or "unknown",
        "git_branch": branch or "unknown",
        "git_dirty": dirty,
        "build_time": BUILD_TIME,
        "repository_root": str(REPOSITORY_ROOT),
        "process_working_directory": str(Path.cwd().resolve()),
        "python_executable": sys.executable,
        "static_root": str(static_root.resolve()),
        "static_files": {
            "html": str((static_root / "index.html").resolve()),
            "javascript": str((static_root / "app.js").resolve()),
            "stylesheet": str((static_root / "styles.css").resolve()),
        },
    }
