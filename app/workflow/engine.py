from __future__ import annotations

from collections.abc import Callable

from app.operator.action_planner import GuiActionPlanner
from app.operator.base import GuiOperator
from app.operator.errors import AuthenticationRequired, OperatorError, PolicyBlocked
from app.operator.safety import ActionSafetyPolicy, UnsafeActionError
from app.planner.asset_search_planner import AssetSearchPlanner
from app.planner.figure_planner import ScientificFigurePlanner
from app.planner.layout_planner import LayoutPlanner
from app.planner.requirement_parser import RequirementParser
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import FigureSpec, FigureStatus, Requirement
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

    def plan(
        self,
        request_text: str,
        *,
        editor_url: str = "https://app.biorender.com/",
    ) -> PlanningBundle:
        requirement = self.requirement_parser.parse(request_text)
        spec = self.figure_planner.plan(requirement)
        return self.plan_spec(requirement, spec, editor_url=editor_url)

    def plan_spec(
        self,
        requirement: Requirement,
        spec: FigureSpec,
        *,
        editor_url: str = "https://app.biorender.com/",
    ) -> PlanningBundle:
        """Compile and persist an already validated scientific input graph."""
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

    def execute(
        self,
        figure_id: str,
        operator: GuiOperator,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> FigureStatus:
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
            for action in self.database.list_actions(figure_id):
                if stop_requested is not None and stop_requested():
                    self.database.add_audit_event(
                        "safe_stop_requested",
                        {
                            "before_action_id": action.id,
                            "message": "Execution paused safely between GUI actions.",
                        },
                        figure_id=figure_id,
                    )
                    self.database.set_status(
                        figure_id,
                        FigureStatus.PAUSED_APPROVAL,
                    )
                    return FigureStatus.PAUSED_APPROVAL
                state = self.database.action_state(action.id)
                if state is None:
                    raise KeyError(f"unknown action {action.id!r}")
                if state["status"] in {
                    ActionStatus.SUCCEEDED.value,
                    ActionStatus.VERIFIED.value,
                }:
                    continue
                if state["status"] == ActionStatus.BLOCKED_BY_POLICY.value:
                    self.database.set_status(figure_id, FigureStatus.BLOCKED)
                    return FigureStatus.BLOCKED

                previous_payload = state.get("result")
                checkpoint = (
                    previous_payload.get("metadata", {}).get("checkpoint")
                    if isinstance(previous_payload, dict)
                    else None
                )
                if (
                    state["status"]
                    in {
                        ActionStatus.EXECUTING.value,
                        ActionStatus.EXECUTED_UNVERIFIED.value,
                        ActionStatus.UNKNOWN.value,
                    }
                    and checkpoint
                ):
                    reconcile = getattr(operator, "reconcile", None)
                    if reconcile is None:
                        self.database.set_status(
                            figure_id, FigureStatus.PAUSED_RECONCILIATION
                        )
                        return FigureStatus.PAUSED_RECONCILIATION
                    previous_result = GuiActionResult.model_validate(previous_payload)
                    try:
                        reconciled = reconcile(action, previous_result)
                    except AuthenticationRequired as error:
                        reconciled = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.PAUSED,
                            attempt=max(1, int(state["attempts"] or 0)),
                            error_type=error.error_type,
                            message=str(error),
                        )
                        self.database.record_action_result(figure_id, reconciled)
                        self.database.set_status(
                            figure_id, FigureStatus.PAUSED_AUTHENTICATION
                        )
                        return FigureStatus.PAUSED_AUTHENTICATION
                    except PolicyBlocked as error:
                        screenshot_path = error.screenshot_path
                        reconciled = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.BLOCKED_BY_POLICY,
                            attempt=max(1, int(state["attempts"] or 0)),
                            error_type=error.error_type,
                            message=str(error),
                            screenshot_path=screenshot_path,
                            expected_bbox=action.expected_bbox,
                            evidence_refs=[screenshot_path] if screenshot_path else [],
                            metadata={
                                "mode": "live",
                                "evidence_kind": "policy_block_during_reconciliation",
                            },
                        )
                        self.database.record_action_result(figure_id, reconciled)
                        self.database.set_status(figure_id, FigureStatus.BLOCKED)
                        return FigureStatus.BLOCKED
                    except Exception as error:
                        screenshot_path = getattr(error, "screenshot_path", None)
                        reconciled = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.UNKNOWN,
                            attempt=max(1, int(state["attempts"] or 0)),
                            error_type=getattr(
                                error,
                                "error_type",
                                type(error).__name__,
                            ),
                            message=str(error),
                            screenshot_path=screenshot_path,
                            expected_bbox=action.expected_bbox,
                            evidence_refs=[screenshot_path] if screenshot_path else [],
                            metadata={
                                "mode": "live",
                                "evidence_kind": "reconciliation_failure",
                                "safe_to_retry": False,
                            },
                        )
                        self.database.record_action_result(figure_id, reconciled)
                        self.database.set_status(
                            figure_id, FigureStatus.PAUSED_RECONCILIATION
                        )
                        return FigureStatus.PAUSED_RECONCILIATION
                    self.database.record_action_result(figure_id, reconciled)
                    if reconciled.status == ActionStatus.VERIFIED:
                        continue
                    if not (
                        reconciled.status == ActionStatus.FAILED
                        and reconciled.metadata.get("safe_to_retry") is True
                    ):
                        self.database.set_status(
                            figure_id, FigureStatus.PAUSED_RECONCILIATION
                        )
                        return FigureStatus.PAUSED_RECONCILIATION

                try:
                    self.safety.check(action)
                except UnsafeActionError as error:
                    blocked = GuiActionResult(
                        action_id=action.id,
                        status=ActionStatus.BLOCKED_BY_POLICY,
                        attempt=max(1, int(state["attempts"] or 0)),
                        error_type="blocked_by_policy",
                        message=str(error),
                        expected_bbox=action.expected_bbox,
                    )
                    self.database.record_action_result(figure_id, blocked)
                    self._update_element_statuses(
                        action,
                        ActionStatus.BLOCKED_BY_POLICY,
                    )
                    self.database.add_audit_event(
                        "blocked_by_policy",
                        {
                            "action_id": action.id,
                            "error_type": "blocked_by_policy",
                            "message": str(error),
                            "checkpoint": None,
                        },
                        figure_id=figure_id,
                    )
                    self.database.set_status(figure_id, FigureStatus.BLOCKED)
                    return FigureStatus.BLOCKED
                succeeded = False
                last_result: GuiActionResult | None = None
                for attempt in range(1, action.max_retries + 2):
                    self.database.mark_action_running(action.id, attempt)
                    self._update_element_statuses(action, ActionStatus.EXECUTING)
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
                        screenshot_path = error.screenshot_path
                        current_state = self.database.action_state(action.id)
                        current_result = (
                            current_state.get("result")
                            if isinstance(current_state, dict)
                            else None
                        )
                        checkpoint = (
                            current_result.get("metadata", {}).get("checkpoint")
                            if isinstance(current_result, dict)
                            else None
                        )
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.BLOCKED_BY_POLICY,
                            attempt=attempt,
                            error_type=error.error_type,
                            message=str(error),
                            screenshot_path=screenshot_path,
                            expected_bbox=action.expected_bbox,
                            evidence_refs=[
                                screenshot_path
                            ]
                            if screenshot_path
                            else [],
                            metadata={
                                "mode": "live",
                                "evidence_kind": "policy_block",
                                "checkpoint": checkpoint,
                                "safe_to_retry": False,
                            },
                        )
                        self.database.add_audit_event(
                            "blocked_by_policy",
                            {
                                "action_id": action.id,
                                "error_type": error.error_type,
                                "message": str(error),
                                "screenshot_path": screenshot_path,
                                "checkpoint": checkpoint,
                            },
                            figure_id=figure_id,
                        )
                    except OperatorError as error:
                        screenshot_path = error.screenshot_path
                        last_result = GuiActionResult(
                            action_id=action.id,
                            status=ActionStatus.FAILED,
                            attempt=attempt,
                            error_type=error.error_type,
                            message=str(error),
                            screenshot_path=screenshot_path,
                            expected_bbox=action.expected_bbox,
                            evidence_refs=[
                                screenshot_path
                            ]
                            if screenshot_path
                            else [],
                            metadata={"mode": "live", "evidence_kind": "failure"},
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
                    self._update_element_statuses(action, last_result.status)
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
                        self.database.set_status(figure_id, FigureStatus.BLOCKED)
                        return FigureStatus.BLOCKED
                if not succeeded:
                    self.database.set_status(figure_id, FigureStatus.FAILED)
                    return FigureStatus.FAILED
        finally:
            operator.close()

        states = self.database.action_states(figure_id)
        visually_verified = bool(states) and all(
            state["status"] == ActionStatus.VERIFIED.value for state in states
        )
        self.database.set_status(figure_id, FigureStatus.VERIFYING)
        self.database.add_verification(
            figure_id,
            "execution_completeness",
            True,
            {
                "all_actions_succeeded": True,
                "visual_verification_performed": visually_verified,
                "reason": (
                    "Every live action supplied observer evidence."
                    if visually_verified
                    else (
                        "Dry-run execution proves plan completion only; it does not "
                        "establish visual or scientific correctness."
                    )
                ),
            },
        )
        self.database.set_status(figure_id, FigureStatus.AWAITING_CONFIRMATION)
        return FigureStatus.AWAITING_CONFIRMATION

    @staticmethod
    def _logical_element_ids(action: object) -> list[str]:
        arguments = getattr(action, "arguments", {})
        keys = (
            "logical_element_id",
            "logical_label_id",
            "logical_connector_id",
            "logical_group_id",
            "logical_layout_id",
            "logical_save_id",
        )
        return list(
            dict.fromkeys(
                str(arguments[key])
                for key in keys
                if arguments.get(key)
            )
        )

    def _update_element_statuses(
        self,
        action: object,
        action_status: ActionStatus,
    ) -> None:
        logical_ids = self._logical_element_ids(action)
        if not logical_ids:
            return
        action_type = getattr(action, "action", None)
        if action_status == ActionStatus.EXECUTING:
            status = (
                "searching"
                if getattr(action_type, "value", "") == "search_asset"
                else "executing"
            )
        elif action_status == ActionStatus.VERIFIED:
            if getattr(action_type, "value", "") == "search_asset":
                status = "searching"
            elif getattr(action_type, "value", "") == "select_asset_candidate":
                status = "candidate_selected"
            else:
                status = "verified"
        elif action_status == ActionStatus.SUCCEEDED:
            status = "executed_unverified"
        elif action_status == ActionStatus.BLOCKED_BY_POLICY:
            status = "blocked_by_policy"
        elif action_status == ActionStatus.UNKNOWN:
            status = "unknown"
        elif action_status == ActionStatus.FAILED:
            status = "failed"
        elif action_status == ActionStatus.EXECUTED_UNVERIFIED:
            status = "executed_unverified"
        else:
            status = action_status.value
        for logical_id in logical_ids:
            self.database.update_element_requirement_status(
                action.figure_id,
                logical_id,
                status,
            )

    def confirm(self, figure_id: str) -> FigureStatus:
        record = self.database.get_figure(figure_id)
        if record is None:
            raise KeyError(f"unknown figure {figure_id!r}")
        if FigureStatus(record["status"]) != FigureStatus.AWAITING_CONFIRMATION:
            raise ValueError("a figure can only be completed after awaiting user confirmation")
        self.database.set_status(figure_id, FigureStatus.COMPLETED)
        return FigureStatus.COMPLETED
