from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.operator.safety import ActionSafetyPolicy
from app.schemas.gui_action import ActionStatus, GuiAction, GuiActionResult


class DryRunOperator:
    """Executes the finite action plan without touching BioRender."""

    def __init__(self, evidence_dir: Path | None = None) -> None:
        self.evidence_dir = evidence_dir or settings.screenshot_dir
        self.policy = ActionSafetyPolicy()

    def execute(self, action: GuiAction, attempt: int = 1) -> GuiActionResult:
        self.policy.check(action)
        figure_dir = self.evidence_dir / action.figure_id
        figure_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = figure_dir / f"{action.sequence:04d}_{action.id}.dry-run.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "mode": "dry-run",
                    "action": action.model_dump(mode="json"),
                    "note": "No browser, account, BioRender AI, export, or paid action was used.",
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return GuiActionResult(
            action_id=action.id,
            status=ActionStatus.SIMULATED,
            attempt=attempt,
            message="Action simulated successfully.",
            screenshot_path=str(evidence_path),
            expected_bbox=action.expected_bbox,
            metadata={
                "mode": "dry-run",
                "evidence_kind": "action_manifest",
                "simulation_status": "simulated",
                "policy_status": "policy_allowed",
                "live_execution_status": "planned",
            },
        )

    def close(self) -> None:
        return None
