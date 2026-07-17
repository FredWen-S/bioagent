from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from app.config import settings
from app.operator.biorender.calibration import BioRenderUiCalibrator
from app.operator.biorender.drag import SafeAssetDrag
from app.operator.biorender.locators import CANVAS_LOCATORS, resolve_largest_visible
from app.operator.biorender.observer import PixelDiffInsertionObserver
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.biorender.reconciliation import (
    ProbeReconciler,
    ReconciliationDecision,
)
from app.operator.biorender.search import SafeAssetSearch
from app.operator.errors import AuthenticationRequired, OperatorError, PolicyBlocked
from app.schemas.biorender_probe import (
    InsertionObservation,
    Presence,
    ProbeCheckpoint,
    ProbeStatus,
)
from app.schemas.gui_action import ActionStatus
from app.storage.database import FigureDatabase


class BioRenderSingleAssetProbe:
    def __init__(
        self,
        page: Any,
        database: FigureDatabase,
        *,
        output_dir: Path | None = None,
        policy: BioRenderPolicyGuard | None = None,
        observer: PixelDiffInsertionObserver | None = None,
    ) -> None:
        self.page = page
        self.database = database
        self.output_dir = output_dir or settings.probe_dir
        self.policy = policy or BioRenderPolicyGuard()
        self.observer = observer or PixelDiffInsertionObserver()

    def run(
        self,
        *,
        editor_url: str,
        query: str = "T cell",
        target_x: float = 0.5,
        target_y: float = 0.5,
        target_width: float = 0.14,
        resume_run_id: str | None = None,
    ) -> dict[str, Any]:
        if resume_run_id:
            run_id = resume_run_id
            stored = self.database.get_probe_run(run_id)
            if stored is None:
                raise KeyError(f"unknown probe run {run_id!r}")
            editor_url = stored["editor_url"]
            query = stored["query"]
        else:
            run_id = f"probe_{uuid.uuid4().hex[:12]}"
            self.database.create_probe_run(run_id, editor_url, query)
            stored = None

        workflow_state = ProbeStatus.EXECUTING
        last_action = "open_editor"
        self._last_action = last_action
        self.database.update_probe_run(run_id, workflow_state)
        try:
            self.page.goto(editor_url, wait_until="domcontentloaded", timeout=60_000)
            self.page.wait_for_timeout(1500)
            self._assert_authenticated()
            last_action = "calibrate_ui"
            self._last_action = last_action
            profile, profile_path = BioRenderUiCalibrator(
                self.page,
                database=self.database,
                policy=self.policy,
            ).calibrate()
            self.database.update_probe_run(
                run_id,
                ProbeStatus.EXECUTING,
                profile_version=profile.ui_profile_version,
            )
            self.database.add_audit_event(
                "ui_calibrated",
                {
                    "profile_id": profile.profile_id,
                    "ui_profile_version": profile.ui_profile_version,
                    "profile_path": str(profile_path),
                    "ai_control_count": len(profile.ai_controls),
                },
                run_id=run_id,
            )

            if stored and stored.get("checkpoint"):
                last_action = "reconcile_before_replay"
                self._last_action = last_action
                checkpoint = ProbeCheckpoint.model_validate(stored["checkpoint"])
                reconciliation = self._reconcile(
                    run_id, checkpoint, profile.ui_profile_version
                )
                if reconciliation.get("decision") != ReconciliationDecision.SAFE_TO_RETRY.value:
                    return reconciliation

            return self._search_drag_verify(
                run_id=run_id,
                query=query,
                profile_version=profile.ui_profile_version,
                profile_path=str(profile_path),
                target_x=target_x,
                target_y=target_y,
                target_width=target_width,
            )
        except AuthenticationRequired as error:
            return self._record_failure(
                run_id,
                ProbeStatus.PAUSED_AUTHENTICATION,
                error,
                workflow_state="awaiting_authentication",
                last_action=getattr(self, "_last_action", last_action),
                safe_to_resume=True,
                recommendation="Manually authenticate, reopen the same blank Figure, then resume the run.",
            )
        except PolicyBlocked as error:
            return self._record_failure(
                run_id,
                ProbeStatus.BLOCKED_BY_POLICY,
                error,
                workflow_state=workflow_state.value,
                last_action=getattr(self, "_last_action", last_action),
                safe_to_resume=False,
                recommendation="Inspect the policy screenshot; close AI/credits/subscription UI manually.",
            )
        except OperatorError as error:
            return self._record_failure(
                run_id,
                ProbeStatus.FAILED,
                error,
                workflow_state=workflow_state.value,
                last_action=getattr(self, "_last_action", last_action),
                safe_to_resume=False,
                recommendation="Inspect the saved screenshot and calibration profile before retrying.",
            )
        except Exception as error:
            return self._record_failure(
                run_id,
                ProbeStatus.FAILED,
                error,
                workflow_state=workflow_state.value,
                last_action=getattr(self, "_last_action", last_action),
                safe_to_resume=False,
                recommendation="Unexpected failure; inspect evidence and do not replay the drag automatically.",
            )

    def _search_drag_verify(
        self,
        *,
        run_id: str,
        query: str,
        profile_version: str,
        profile_path: str,
        target_x: float,
        target_y: float,
        target_width: float,
    ) -> dict[str, Any]:
        run_dir = self.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        search_id = "probe_search_asset"
        self._last_action = "search_asset"
        self.database.record_probe_action(
            run_id, search_id, ActionStatus.EXECUTING
        )
        search = SafeAssetSearch(
            self.page,
            evidence_dir=self.output_dir,
            policy=self.policy,
        ).search(query, run_id)
        self.database.record_probe_action(
            run_id,
            search_id,
            ActionStatus.EXECUTED_UNVERIFIED,
            evidence=[search.screenshot_path, search.results_screenshot_path],
        )
        self.database.add_audit_event(
            "search_executed_unverified",
            {
                "query": query,
                "candidate_count": len(search.candidates),
                "selected_candidate": search.selected.record.model_dump(mode="json"),
            },
            run_id=run_id,
        )
        # Stable result geometry and candidate safety evidence are the search observer.
        self.database.record_probe_action(
            run_id,
            search_id,
            ActionStatus.VERIFIED,
            observation_confidence=0.95,
            observation_source="dom",
            evidence=[search.screenshot_path, search.results_screenshot_path],
        )

        drag_id = "probe_drag_asset"
        self._last_action = "prepare_drag_checkpoint"
        drag = SafeAssetDrag(
            self.page,
            evidence_dir=self.output_dir,
            policy=self.policy,
        )
        prepared = drag.prepare(
            search.selected,
            run_id,
            target_x=target_x,
            target_y=target_y,
            target_width=target_width,
        )
        checkpoint = ProbeCheckpoint(
            run_id=run_id,
            profile_version=profile_version,
            editor_url=str(self.page.url),
            query=query,
            expected_bbox=prepared.expected_bbox,
            baseline_canvas_path=prepared.baseline_canvas_path,
            canvas_bbox=prepared.canvas_bbox,
            candidate=search.selected.record,
            drag_action_id=drag_id,
        )
        self.database.update_probe_run(
            run_id,
            ProbeStatus.EXECUTING,
            profile_version=profile_version,
            checkpoint=checkpoint,
        )
        self.database.record_probe_action(
            run_id,
            drag_id,
            ActionStatus.EXECUTING,
            expected_bbox=prepared.expected_bbox.model_dump(mode="json"),
            evidence=[prepared.baseline_canvas_path],
        )

        self._last_action = "drag_asset"
        after_path = drag.execute(prepared)
        checkpoint = checkpoint.model_copy(update={"after_canvas_path": after_path})
        self.database.update_probe_run(
            run_id,
            ProbeStatus.EXECUTED_UNVERIFIED,
            checkpoint=checkpoint,
        )
        self.database.record_probe_action(
            run_id,
            drag_id,
            ActionStatus.EXECUTED_UNVERIFIED,
            expected_bbox=prepared.expected_bbox.model_dump(mode="json"),
            observed_bbox=None,
            evidence=[prepared.baseline_canvas_path, after_path],
        )
        self.database.add_audit_event(
            "drag_executed_unverified",
            {
                "expected_bbox": prepared.expected_bbox.model_dump(mode="json"),
                "observed_bbox": None,
                "checkpoint": checkpoint.model_dump(mode="json"),
            },
            run_id=run_id,
        )

        self._last_action = "observe_canvas_after_drag"
        observation = self.observer.observe(
            baseline_path=prepared.baseline_canvas_path,
            current_path=after_path,
            canvas_bbox=prepared.canvas_bbox,
            expected_bbox=prepared.expected_bbox,
        )
        return self._apply_observation(
            run_id,
            drag_id,
            observation,
            checkpoint=checkpoint,
            extra={
                "profile_path": profile_path,
                "selected_candidate": search.selected.record.model_dump(mode="json"),
                "search_screenshot": search.screenshot_path,
            },
        )

    def _reconcile(
        self,
        run_id: str,
        checkpoint: ProbeCheckpoint,
        current_profile_version: str,
    ) -> dict[str, Any]:
        canvas = resolve_largest_visible(self.page, CANVAS_LOCATORS)
        self._last_action = "reconcile_canvas"
        if canvas is None:
            raise OperatorError("Canvas is unavailable during reconciliation")
        run_dir = self.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        current_path = run_dir / "canvas-reconcile-current.png"
        canvas.locator.screenshot(path=str(current_path))
        reconciler = ProbeReconciler(self.observer)
        result = reconciler.reconcile(
            checkpoint,
            current_profile_version=current_profile_version,
            current_canvas_path=str(current_path),
        )
        self.database.add_audit_event(
            "probe_reconciled",
            result.model_dump(mode="json"),
            run_id=run_id,
        )
        if result.decision == ReconciliationDecision.ALREADY_VERIFIED:
            return self._apply_observation(
                run_id,
                checkpoint.drag_action_id,
                result.observation,
                checkpoint=checkpoint,
                extra={
                    "reconciled_without_drag_replay": True,
                    "decision": ReconciliationDecision.ALREADY_VERIFIED.value,
                },
            )
        if result.decision == ReconciliationDecision.PAUSE_UNKNOWN:
            observation = result.observation
            self.database.record_probe_action(
                run_id,
                checkpoint.drag_action_id,
                ActionStatus.UNKNOWN,
                expected_bbox=checkpoint.expected_bbox.model_dump(mode="json"),
                observed_bbox=(
                    observation.observed_bbox.model_dump(mode="json")
                    if observation and observation.observed_bbox
                    else None
                ),
                observation_confidence=observation.confidence if observation else None,
                observation_source=(observation.source.value if observation else None),
                evidence=(observation.evidence_refs if observation else [str(current_path)]),
            )
            payload = {
                "run_id": run_id,
                "status": ProbeStatus.UNKNOWN.value,
                "decision": result.decision.value,
                "reason": result.reason,
                "safe_to_resume": False,
                "recommended_manual_checkpoint": str(current_path),
            }
            self.database.update_probe_run(
                run_id, ProbeStatus.UNKNOWN, result=payload
            )
            return payload
        return {
            "run_id": run_id,
            "status": ProbeStatus.EXECUTING.value,
            "decision": result.decision.value,
        }

    def _apply_observation(
        self,
        run_id: str,
        action_id: str,
        observation: InsertionObservation | None,
        *,
        checkpoint: ProbeCheckpoint,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if observation is None:
            raise RuntimeError("reconciliation produced no observation")
        common = {
            "expected_bbox": observation.expected_bbox.model_dump(mode="json"),
            "observed_bbox": (
                observation.observed_bbox.model_dump(mode="json")
                if observation.observed_bbox
                else None
            ),
            "observation": observation.model_dump(mode="json"),
            **(extra or {}),
        }
        if observation.presence == Presence.PRESENT and observation.confidence >= 0.75:
            self.database.record_probe_action(
                run_id,
                action_id,
                ActionStatus.VERIFIED,
                expected_bbox=observation.expected_bbox.model_dump(mode="json"),
                observed_bbox=(
                    observation.observed_bbox.model_dump(mode="json")
                    if observation.observed_bbox
                    else None
                ),
                observation_confidence=observation.confidence,
                observation_source=observation.source.value,
                evidence=observation.evidence_refs,
            )
            payload = {
                "run_id": run_id,
                "status": ProbeStatus.AWAITING_CONFIRMATION.value,
                "safe_to_resume": True,
                **common,
            }
            self.database.update_probe_run(
                run_id,
                ProbeStatus.AWAITING_CONFIRMATION,
                checkpoint=checkpoint,
                result=payload,
            )
            return payload
        if observation.presence == Presence.ABSENT:
            action_status = ActionStatus.FAILED
            run_status = ProbeStatus.FAILED
            safe_to_resume = True
        else:
            action_status = ActionStatus.UNKNOWN
            run_status = ProbeStatus.UNKNOWN
            safe_to_resume = False
        self.database.record_probe_action(
            run_id,
            action_id,
            action_status,
            expected_bbox=observation.expected_bbox.model_dump(mode="json"),
            observed_bbox=None,
            observation_confidence=observation.confidence,
            observation_source=observation.source.value,
            evidence=observation.evidence_refs,
        )
        payload = {
            "run_id": run_id,
            "status": run_status.value,
            "safe_to_resume": safe_to_resume,
            "recommended_manual_checkpoint": observation.evidence_refs[-1]
            if observation.evidence_refs
            else None,
            **common,
        }
        self.database.update_probe_run(
            run_id,
            run_status,
            checkpoint=checkpoint,
            result=payload,
        )
        return payload

    def _record_failure(
        self,
        run_id: str,
        status: ProbeStatus,
        error: Exception,
        *,
        workflow_state: str,
        last_action: str,
        safe_to_resume: bool,
        recommendation: str,
    ) -> dict[str, Any]:
        run_dir = self.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = run_dir / "failure.png"
        try:
            self.page.screenshot(path=str(screenshot_path), full_page=True)
            screenshot_value: str | None = str(screenshot_path)
        except Exception:
            screenshot_value = None
        payload = {
            "run_id": run_id,
            "status": status.value,
            "error_type": getattr(error, "error_type", type(error).__name__),
            "message": str(error),
            "workflow_state": workflow_state,
            "last_action": last_action,
            "screenshot_path": screenshot_value,
            "recommended_manual_checkpoint": recommendation,
            "safe_to_resume": safe_to_resume,
        }
        self.database.update_probe_run(run_id, status, error=payload, result=payload)
        self.database.add_audit_event(
            "probe_paused_or_failed", payload, run_id=run_id
        )
        return payload

    def _assert_authenticated(self) -> None:
        try:
            password_visible = self.page.locator("input[type='password']").count() > 0
        except Exception:
            password_visible = False
        if password_visible or re.search(
            r"(?:login|log-in|sign-in|signin)", str(self.page.url), re.IGNORECASE
        ):
            raise AuthenticationRequired(
                "BioRender requires manual authentication; credentials are never entered by the agent"
            )
