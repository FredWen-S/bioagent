from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    runtime_dir: Path = ROOT_DIR / "runtime"
    database_path: Path = ROOT_DIR / "runtime" / "agent.db"
    screenshot_dir: Path = ROOT_DIR / "runtime" / "screenshots"
    session_dir: Path = ROOT_DIR / "runtime" / "sessions"
    calibration_dir: Path = ROOT_DIR / "output" / "playwright" / "calibration"
    probe_dir: Path = ROOT_DIR / "output" / "playwright" / "probes"
    max_action_retries: int = 2
    max_repair_rounds: int = 3
    max_element_moves: int = 3
    live_mode: bool = False

    def ensure_directories(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self.probe_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
