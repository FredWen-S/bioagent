from __future__ import annotations

from pathlib import Path

from app.operator.dry_run import DryRunOperator
from app.operator.errors import (
    AuthenticationRequired,
    EditorPrepareFailed,
    PolicyBlocked,
)
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, ActionType, GuiAction, GuiActionResult
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


class EditorPrepareFailsOperator:
    """Operator that fails the very first action (open_biorender_editor).

    Mirrors what happens on a real browser when the URL redirects off
    biorender.com, times out, or lands on a page with no canvas.
    """

    def __init__(self, subcode: str = "redirected_off_domain") -> None:
        self.subcode = subcode
        self.calls = 0

    def execute(self, action: GuiAction, attempt: int = 1) -> GuiActionResult:
        self.calls += 1
        assert action.action == ActionType.OPEN_EDITOR, (
            "prepare must be the very first action attempted"
        )
        raise EditorPrepareFailed(
            "canvas never appeared",
            subcode=self.subcode,
            requested_url=str(action.arguments.get("url", "")),
            observed_url="https://www.biorender.com/marketing",
        )

    def close(self) -> None:
        return None


def test_editor_prepare_failure_is_recorded_with_structured_metadata(
    tmp_path: Path,
) -> None:
    """Prepare failure must produce a FAILED figure with the structured
    ``editor_prepare_failure`` payload, and an ``editor_prepare_failed``
    audit event so operators can debug without re-running Playwright.
    """
    database = FigureDatabase(tmp_path / "prepare.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(PD1_REQUEST)

    operator = EditorPrepareFailsOperator(subcode="redirected_off_domain")
    status = engine.execute(bundle.figure_spec.id, operator)

    assert status == FigureStatus.FAILED
    # Only the prepare action was attempted; retries all failed with the same
    # environmental error, and no downstream element action started.
    states = database.action_states(bundle.figure_spec.id)
    prepare_state = next(
        state for state in states if state["action_type"] == "open_biorender_editor"
    )
    assert prepare_state["status"] == "failed"
    result_metadata = prepare_state["result"]["metadata"]
    assert result_metadata["safe_to_retry"] is False
    payload = result_metadata["editor_prepare_failure"]
    assert payload["subcode"] == "redirected_off_domain"
    assert payload["requested_url"]
    assert payload["observed_url"] == "https://www.biorender.com/marketing"
    downstream = [
        state for state in states if state["action_type"] != "open_biorender_editor"
    ]
    assert all(state["status"] == "planned" for state in downstream), (
        "no element action must run once prepare fails"
    )

    audit = database.list_audit_events(figure_id=bundle.figure_spec.id)
    prepare_events = [
        event for event in audit if event["event_type"] == "editor_prepare_failed"
    ]
    assert prepare_events, "prepare failure must emit an audit event"
    assert prepare_events[-1]["payload"]["subcode"] == "redirected_off_domain"


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
