from __future__ import annotations

from pathlib import Path

from app.operator.dry_run import DryRunOperator
from app.operator.errors import AuthenticationRequired
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import GuiAction
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

