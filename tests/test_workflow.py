from __future__ import annotations

from pathlib import Path

from app.operator.dry_run import DryRunOperator
from app.operator.errors import AuthenticationRequired, PolicyBlocked
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, GuiAction, GuiActionResult
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine

PD1_REQUEST = (
    "制作双栏对比：未经治疗时 PD-1/PD-L1 结合并抑制 T 细胞；"
    "anti-PD-1 treatment 阻断相互作用，T 细胞杀伤 Tumor cell。"
)


class AuthenticationPauseOperator:
    def execute(self, action: GuiAction, attempt: int = 1):
        raise AuthenticationRequired("manual login required")

    def close(self) -> None:
        return None


class PolicyBlockOperator:
    def __init__(self, screenshot_path: Path) -> None:
        self.screenshot_path = screenshot_path
        self.calls = 0

    def execute(self, action: GuiAction, attempt: int = 1) -> GuiActionResult:
        self.calls += 1
        if self.calls == 1:
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.VERIFIED,
                attempt=attempt,
                message="editor opened",
            )
        raise PolicyBlocked(
            "AI credits dialog detected",
            screenshot_path=str(self.screenshot_path),
        )

    def close(self) -> None:
        return None


def test_dry_run_is_persisted_and_requires_confirmation(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "agent.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(PD1_REQUEST)

    assert database.get_figure(bundle.figure_spec.id)["status"] == "validated"
    status = engine.execute(
        bundle.figure_spec.id,
        DryRunOperator(evidence_dir=tmp_path / "evidence"),
    )

    assert status == FigureStatus.AWAITING_CONFIRMATION
    states = database.action_states(bundle.figure_spec.id)
    assert len(states) == len(bundle.actions)
    assert {state["status"] for state in states} == {"succeeded"}
    assert all(Path(state["result"]["screenshot_path"]).exists() for state in states)
    verifications = database.get_verifications(bundle.figure_spec.id)
    assert verifications[-1]["payload"]["visual_verification_performed"] is False

    assert engine.confirm(bundle.figure_spec.id) == FigureStatus.COMPLETED


def test_authentication_pause_can_resume_from_first_unfinished_action(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "agent.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(PD1_REQUEST)

    paused = engine.execute(bundle.figure_spec.id, AuthenticationPauseOperator())
    assert paused == FigureStatus.PAUSED_AUTHENTICATION
    assert database.action_states(bundle.figure_spec.id)[0]["status"] == "paused"

    resumed = engine.execute(
        bundle.figure_spec.id,
        DryRunOperator(evidence_dir=tmp_path / "evidence"),
    )
    assert resumed == FigureStatus.AWAITING_CONFIRMATION
    assert {state["status"] for state in database.action_states(bundle.figure_spec.id)} == {
        "succeeded"
    }


def test_policy_block_persists_evidence_audit_and_element_state(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "policy.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(PD1_REQUEST)
    screenshot = tmp_path / "policy-block.png"
    screenshot.write_bytes(b"evidence")

    status = engine.execute(
        bundle.figure_spec.id,
        PolicyBlockOperator(screenshot),
    )

    assert status == FigureStatus.BLOCKED
    blocked_state = database.action_states(bundle.figure_spec.id)[1]
    assert blocked_state["status"] == "blocked_by_policy"
    assert blocked_state["result"]["screenshot_path"] == str(screenshot)
    requirements = database.list_element_requirements(bundle.figure_spec.id)
    assert next(
        item for item in requirements if item["logical_element_id"] == "t_cell_before"
    )["status"] == "blocked_by_policy"
    audit = database.list_audit_events(figure_id=bundle.figure_spec.id)
    assert audit[-1]["event_type"] == "blocked_by_policy"
    assert audit[-1]["payload"]["screenshot_path"] == str(screenshot)
