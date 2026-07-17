from __future__ import annotations

from app.operator.action_planner import GuiActionPlanner
from app.operator.base import GuiOperator
from app.operator.errors import AuthenticationRequired, OperatorError, PolicyBlocked
from app.operator.safety import ActionSafetyPolicy, UnsafeActionError
from app.planner.asset_search_planner import AssetSearchPlanner
from app.planner.figure_planner import ScientificFigurePlanner
from app.planner.layout_planner import LayoutPlanner
from app.planner.requirement_parser import RequirementParser
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, GuiActionResult
from app.storage.database import FigureDatabase
from app.verifier.scientific_guard import ScientificValidityGuard


class WorkflowEngine:
    def __init__(self, database: FigureDatabase | None = None) -> None:
        self.database = database or FigureDatabase()
        self.requirement_parser = RequirementParser()
        self.figure_planner = ScientificFigurePlanner()
        self.asset_planner = AssetSearchPlanner()
        self.layout_planner = LayoutPlanner()
        self.guard = ScientificValidityGuard()
        self.action_planner = GuiActionPlanner()
        self.safety = ActionSafetyPolicy()

    def plan(self, request_text: str, *, editor_url: str = "https://app.biorender.com/") -> PlanningBundle:
        requirement = self.requirement_parser.parse(request_text)
        spec = self.figure_planner.plan(requirement)
        validation = self.guard.validate(spec, requirement)
        assets = self.asset_planner.plan(spec)
        layout = self.layout_planner.plan(spec)
        actions = self.action_planner.compile(spec, layout, assets, editor_url=editor_url)
        status = FigureStatus.VALIDATED if validation.passed else FigureStatus.BLOCKED
        bundle = PlanningBundle(
            requirement=requirement,
            figure_spec=spec,
            asset_plan=assets,
            layout_spec=layout,
            scientific_validation=validation,
            actions=actions,
            status=status,
        )
        self.database.save_bundle(bundle)
        return bundle

    def execute(self, figure_id: str, operator: GuiOperator) -> FigureStatus:
        record = self.database.get_figure(figure_id)
        if record is None:
            raise KeyError(f"unknown figure {figure_id!r}")
        status = FigureStatus(record["status"])
        if status == FigureStatus.BLOCKED:
            return status
        if status == FigureStatus.COMPLETED:
            return status

        self.database.set_status(figure_id, FigureStatus.EXECUTING)
        try:
            for action in self.database.pending_actions(figure_id):
                self.safety.check(action)
                succeeded = False
                last_result: GuiActionResult | None = None
                for attempt in range(1, action.max_retries + 2):
                    self.database.mark_action_running(action.id, attempt)
                    try:
                        last_result = operator.execute(action, attempt)
                    except AuthenticationRequired as error:
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.PAUSED,
                            attempt=attempt,
                            error_type=error.error_type,
                            message=str(error),
                        )
                        self.database.record_action_result(figure_id, last_result)
                        self.database.set_status(figure_id, FigureStatus.PAUSED_AUTHENTICATION)
                        return FigureStatus.PAUSED_AUTHENTICATION
                    except PolicyBlocked as error:
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.BLOCKED_BY_POLICY,
                            attempt=attempt,
                            error_type=error.error_type,
                            message=str(error),
                            expected_bbox=action.expected_bbox,
                        )
                    except OperatorError as error:
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.FAILED,
                            attempt=attempt,
                            error_type=error.error_type,
                            message=str(error),
                            expected_bbox=action.expected_bbox,
                        )
                    except Exception as error:  # preserve evidence and stop safely
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.FAILED,
                            attempt=attempt,
                            error_type=type(error).__name__,
                            message=str(error),
                            expected_bbox=action.expected_bbox,
                        )
                    self.database.record_action_result(figure_id, last_result)
                    if last_result.status in {ActionStatus.SUCCEEDED, ActionStatus.VERIFIED}:
                        succeeded = True
                        break
                    if last_result.status in {
                        ActionStatus.EXECUTED_UNVERIFIED,
                        ActionStatus.UNKNOWN,
                    }:
                        self.database.set_status(
                            figure_id, FigureStatus.PAUSED_RECONCILIATION
                        )
                        return FigureStatus.PAUSED_RECONCILIATION
                    if last_result.status == ActionStatus.BLOCKED_BY_POLICY:
                        self.database.set_status(figure_id, FigureStatus.PAUSED_APPROVAL)
                        return FigureStatus.PAUSED_APPROVAL
                if not succeeded:
                    self.database.set_status(figure_id, FigureStatus.FAILED)
                    return FigureStatus.FAILED
        except UnsafeActionError:
            self.database.set_status(figure_id, FigureStatus.PAUSED_APPROVAL)
            return FigureStatus.PAUSED_APPROVAL
        finally:
            operator.close()

        self.database.set_status(figure_id, FigureStatus.VERIFYING)
        self.database.add_verification(
            figure_id,
            "execution_completeness",
            True,
            {
                "all_actions_succeeded": True,
                "visual_verification_performed": False,
                "reason": "Dry-run/operator execution alone cannot establish visual or scientific correctness.",
            },
        )
        self.database.set_status(figure_id, FigureStatus.AWAITING_CONFIRMATION)
        return FigureStatus.AWAITING_CONFIRMATION

    def confirm(self, figure_id: str) -> FigureStatus:
        record = self.database.get_figure(figure_id)
        if record is None:
            raise KeyError(f"unknown figure {figure_id!r}")
        if FigureStatus(record["status"]) != FigureStatus.AWAITING_CONFIRMATION:
            raise ValueError("a figure can only be completed after awaiting user confirmation")
        self.database.set_status(figure_id, FigureStatus.COMPLETED)
        return FigureStatus.COMPLETED
