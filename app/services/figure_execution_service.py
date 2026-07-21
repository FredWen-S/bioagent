from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.operator.dry_run import DryRunOperator
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import (
    Entity,
    EntityCategory,
    FigureSpec,
    FigureStatus,
    LayoutType,
    Relation,
    RelationType,
    Requirement,
)
from app.schemas.gui_action import ActionType
from app.schemas.ui import CustomFigureInput, UiTaskInput
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine

PD1_REQUEST_PATH = Path(__file__).resolve().parents[2] / "examples" / "pd1_request.txt"

FINAL_JOB_STATES = frozenset({"completed", "failed", "blocked", "stopped"})
BROWSER_JOB_KINDS = frozenset(
    {"live_figure", "calibration", "canvas_check", "manual_login"}
)
INTERRUPTED_FIGURE_STATES = frozenset(
    {
        FigureStatus.EXECUTING.value,
        FigureStatus.VERIFYING.value,
        FigureStatus.REPAIRING.value,
    }
)
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
LOGIN_STARTUP_WAIT_SECONDS = 15.0
LOGIN_WINDOW_POLL_SECONDS = 0.25

logger = logging.getLogger(__name__)


class UiServiceError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        diagnostic_hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details
        self.diagnostic_hint = diagnostic_hint


@dataclass(slots=True)
class ManagedJob:
    id: str
    kind: str
    status: str
    message: str
    figure_id: str | None = None
    error_code: str | None = None
    diagnostic_hint: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    result: dict[str, Any] | None = None
    input_data: dict[str, Any] = field(default_factory=dict, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    state_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        started = datetime.fromisoformat(self.created_at)
        finished = datetime.now(UTC)
        if self.status in FINAL_JOB_STATES:
            finished = datetime.fromisoformat(self.updated_at)
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "figure_id": self.figure_id,
            "error_code": self.error_code,
            "diagnostic_hint": self.diagnostic_hint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "elapsed_seconds": max(0, round((finished - started).total_seconds())),
            "result": self.result,
        }


class FigureExecutionService:
    """Small application layer shared by CLI and the graphical control panel."""

    def __init__(
        self,
        database: FigureDatabase | None = None,
        *,
        live_operator_factory: Callable[[FigureDatabase], Any] | None = None,
    ) -> None:
        self.database = database or FigureDatabase()
        self.engine = WorkflowEngine(self.database)
        self._live_operator_factory = live_operator_factory
        self._jobs: dict[str, ManagedJob] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._login_complete = threading.Event()
        self._login_verified = False
        self._verified_canvas: dict[str, str] | None = None
        self._recover_interrupted_runs()

    @staticmethod
    def pd1_request() -> str:
        return PD1_REQUEST_PATH.read_text(encoding="utf-8")

    def plan_prompt(
        self,
        request_text: str,
        *,
        editor_url: str = "https://app.biorender.com/",
    ) -> PlanningBundle:
        return self.engine.plan(request_text, editor_url=editor_url)

    def plan_task(
        self,
        task: UiTaskInput,
        *,
        editor_url: str = "https://app.biorender.com/",
    ) -> PlanningBundle:
        if task.mode == "preset":
            return self.plan_prompt(self.pd1_request(), editor_url=editor_url)
        if task.mode == "prompt":
            assert task.prompt is not None
            return self.plan_prompt(task.prompt, editor_url=editor_url)
        assert task.custom is not None
        requirement, spec = self._custom_spec(task.custom)
        return self.engine.plan_spec(requirement, spec, editor_url=editor_url)

    @staticmethod
    def plan_summary(bundle: PlanningBundle) -> dict[str, Any]:
        spec = bundle.figure_spec
        layout_names = {
            LayoutType.LINEAR: "从左到右的线性布局",
            LayoutType.TWO_PANEL_COMPARISON: "左右双栏对比布局",
            LayoutType.RADIAL: "中心放射布局",
        }
        risks = [issue.message for issue in bundle.scientific_validation.issues]
        return {
            "asset_count": len(spec.entities),
            "label_count": sum(bool(entity.label) for entity in spec.entities),
            "relation_count": len(spec.relations),
            "layout_description": layout_names.get(
                spec.layout_type,
                str(spec.layout_type),
            ),
            "risks": risks,
            "supported": bundle.scientific_validation.passed,
        }

    def execute_dry_run(self, figure_id: str) -> FigureStatus:
        return self.engine.execute(figure_id, DryRunOperator())

    def inspect_plan(self, task: UiTaskInput) -> dict[str, Any]:
        self._require_login_verified()
        if not self._verified_canvas:
            raise UiServiceError(
                "CANVAS_NOT_VERIFIED",
                "请先在第二步检查目标画布。",
            )
        bundle = self.plan_task(task)
        fingerprint = self._task_fingerprint(task)
        self.database.add_audit_event(
            "ui_plan_parsed",
            {
                "task_fingerprint": fingerprint,
                "summary": self.plan_summary(bundle),
            },
            figure_id=bundle.figure_spec.id,
        )
        return {
            **self.run_summary(bundle.figure_spec.id),
            "task_fingerprint": fingerprint,
            "task_summary": self.plan_summary(bundle),
            "scientific_validation_passed": bundle.scientific_validation.passed,
            "validation_issues": [
                item.model_dump(mode="json")
                for item in bundle.scientific_validation.issues
            ],
        }

    def execute_planned_dry_run(
        self,
        plan_id: str,
        task: UiTaskInput,
    ) -> dict[str, Any]:
        self._require_parsed_plan(plan_id, task)
        status = self.execute_dry_run(plan_id)
        self.database.add_audit_event(
            "dry_run_completed",
            {
                "figure_status": status.value,
                "task_fingerprint": self._task_fingerprint(task),
                "real_biorender_modified": False,
            },
            figure_id=plan_id,
        )
        return self.run_summary(plan_id, status_override=status)

    def plan_and_execute_dry_run(self, task: UiTaskInput) -> dict[str, Any]:
        """Compatibility helper for non-UI callers that still need a one-shot dry run."""
        bundle = self.plan_task(task)
        self.database.add_audit_event(
            "ui_plan_parsed",
            {
                "task_fingerprint": self._task_fingerprint(task),
                "summary": self.plan_summary(bundle),
            },
            figure_id=bundle.figure_spec.id,
        )
        return self.execute_planned_dry_run(bundle.figure_spec.id, task)

    def confirm_dry_run(self, figure_id: str) -> dict[str, Any]:
        metadata = self._dry_run_metadata(figure_id)
        if not metadata["completed"]:
            raise UiServiceError(
                "DRY_RUN_NOT_READY",
                "该任务不是已完成的安全预演，无法确认。",
            )
        record = self.database.get_figure(figure_id)
        if record is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到绘图任务。")
        if metadata["confirmed"] and record["status"] == FigureStatus.COMPLETED.value:
            return self.run_summary(figure_id)
        try:
            self.engine.confirm(figure_id)
        except ValueError as error:
            raise UiServiceError(
                "DRY_RUN_NOT_READY",
                "安全预演尚未进入等待确认状态，请重新执行预演。",
            ) from error
        self.database.add_audit_event(
            "dry_run_confirmed",
            {
                "task_fingerprint": metadata["task_fingerprint"],
                "real_biorender_accepted": False,
            },
            figure_id=figure_id,
        )
        return self.run_summary(figure_id)

    def execute_live_sync(self, figure_id: str) -> FigureStatus:
        return self.engine.execute(figure_id, self._new_live_operator())

    def start_live(
        self,
        task: UiTaskInput,
        editor_url: str,
        *,
        plan_id: str | None,
        dry_run_id: str,
    ) -> dict[str, Any]:
        self._require_login_verified()
        self._require_verified_canvas(editor_url)
        self._require_confirmed_dry_run(dry_run_id, task)
        self._ensure_browser_available()
        bundle = self.plan_task(task, editor_url=editor_url)
        self.database.add_audit_event(
            "live_started_from_confirmed_dry_run",
            {"plan_id": plan_id, "dry_run_id": dry_run_id},
            figure_id=bundle.figure_spec.id,
        )
        return self.start_resume(bundle.figure_spec.id)

    def start_resume(self, figure_id: str) -> dict[str, Any]:
        self._ensure_browser_available()
        record = self.database.get_figure(figure_id)
        if record is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到要继续的任务。")
        with self._lock:
            if self._active_job_for_figure(figure_id) is not None:
                raise UiServiceError("RUN_ALREADY_ACTIVE", "该任务已经在运行。")
            job = ManagedJob(
                id=f"job_{uuid.uuid4().hex[:12]}",
                kind="live_figure",
                status="queued",
                message="等待启动浏览器绘图任务。",
                figure_id=figure_id,
            )
            self._jobs[job.id] = job
            thread = threading.Thread(
                target=self._run_job_target,
                args=(job.id, self._run_live_job),
                name=f"biorender-live-{job.id}",
                daemon=True,
            )
            self._threads[job.id] = thread
            thread.start()
        return job.public()

    def request_safe_stop(self, figure_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._active_job_for_figure(figure_id)
            if job is None:
                raise UiServiceError("RUN_NOT_ACTIVE", "当前任务没有正在运行的后台操作。")
            return self._request_job_stop_locked(job)

    def request_job_stop(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._reap_stale_jobs_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise UiServiceError("JOB_NOT_FOUND", "未找到后台任务。")
            if job.status in FINAL_JOB_STATES:
                return job.public()
            return self._request_job_stop_locked(job)

    def start_calibration(self, editor_url: str) -> dict[str, Any]:
        self._ensure_browser_available()
        job = ManagedJob(
            id=f"calibration_job_{uuid.uuid4().hex[:12]}",
            kind="calibration",
            status="queued",
            message="等待打开 BioRender 并校准界面。",
            input_data={"editor_url": editor_url},
        )
        self._start_thread(job, self._run_calibration_job)
        return job.public()

    def start_canvas_check(
        self,
        editor_url: str,
        *,
        confirmed_blank: bool,
    ) -> dict[str, Any]:
        self._require_login_verified()
        if not confirmed_blank:
            raise UiServiceError(
                "BLANK_CANVAS_CONFIRMATION_REQUIRED",
                "请确认这是可测试的空白画布。",
            )
        self._ensure_browser_available()
        job = ManagedJob(
            id=f"canvas_job_{uuid.uuid4().hex[:12]}",
            kind="canvas_check",
            status="queued",
            message="等待检查 BioRender 画布。",
            input_data={"editor_url": editor_url},
        )
        self._start_thread(job, self._run_canvas_check_job)
        return job.public()

    def start_manual_login(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_browser_available()
            self._login_complete.clear()
            self._login_verified = False
            job = ManagedJob(
                id=f"login_job_{uuid.uuid4().hex[:12]}",
                kind="manual_login",
                status="queued",
                message="正在打开人工登录窗口。",
            )
            self._start_thread(job, self._run_login_job)
        self._wait_for_manual_login_startup(job)
        if job.status == "failed":
            raise UiServiceError(
                job.error_code or "PLAYWRIGHT_LAUNCH_FAILED",
                job.message,
                diagnostic_hint=job.diagnostic_hint,
            )
        return job.public()

    def complete_manual_login(self) -> dict[str, Any]:
        with self._lock:
            job = next(
                (
                    item
                    for item in self._jobs.values()
                    if item.kind == "manual_login" and item.status == "waiting_user"
                ),
                None,
            )
            if job is None:
                raise UiServiceError("LOGIN_WINDOW_NOT_OPEN", "当前没有等待确认的登录窗口。")
            self._login_complete.set()
            job.message = "正在保存浏览器登录会话。"
            job.updated_at = datetime.now(UTC).isoformat()
            return job.public()

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            self._reap_stale_jobs_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise UiServiceError("JOB_NOT_FOUND", "未找到后台任务。")
            return job.public()

    def system_status(self) -> dict[str, Any]:
        try:
            with self.database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
            database_status = "normal"
        except Exception:
            database_status = "unavailable"
        with self._lock:
            self._reap_stale_jobs_locked()
            active_jobs = [
                job.public()
                for job in self._jobs.values()
                if job.status not in FINAL_JOB_STATES
            ]
            login_job = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.kind == "manual_login" and job.status not in FINAL_JOB_STATES
                ),
                None,
            )
        latest_calibration = self.database.latest_calibration_profile()
        calibration_evidence = None
        if latest_calibration:
            calibration_path = self._safe_evidence_path(
                Path(latest_calibration["screenshot_path"])
            )
            if calibration_path is not None and calibration_path.is_file():
                calibration_evidence = {
                    "id": "calibration-latest",
                    "kind": "calibration",
                    "name": calibration_path.name,
                    "created_at": latest_calibration["created_at"],
                    "is_image": True,
                    "preview_url": "/api/ui/evidence/calibration-latest",
                }
        return {
            "backend": "normal",
            "database": database_status,
            "browser_login": (
                "waiting_user"
                if login_job
                else ("verified" if self._login_verified else "not_verified")
            ),
            "verified_canvas": (
                {
                    key: value
                    for key, value in self._verified_canvas.items()
                    if key != "editor_url"
                }
                if self._verified_canvas
                else None
            ),
            "calibration": (
                latest_calibration["status"] if latest_calibration else "not_calibrated"
            ),
            "calibration_evidence": calibration_evidence,
            "active_jobs": active_jobs,
            "recent_runs": [
                {
                    **record,
                    "friendly_status": self.friendly_status(record["status"]),
                }
                for record in self.database.list_figures(limit=10)
            ],
            "local_compatibility_editor": "verified",
            "real_biorender_acceptance": "pending_manual_acceptance",
            "ai_generate": "disabled_by_policy",
            "ai_credits": "not_intentionally_used",
            "latest_confirmed_dry_run": self._latest_confirmed_dry_run(),
        }

    def reset_workflow(self) -> dict[str, object]:
        """Forget the selected canvas for a new guided task without touching runs."""
        self._verified_canvas = None
        return {"ok": True, "message": "已开始新任务；请重新指定目标画布。"}

    def workflow_state(
        self,
        *,
        plan_id: str | None = None,
        dry_run_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the backend-owned state that drives the five-step UI."""
        with self._lock:
            self._reap_stale_jobs_locked()
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.status not in FINAL_JOB_STATES
                ),
                None,
            )
        state = "login_required"
        step = 1
        reason = "请先由您本人在 BioRender 官方页面完成登录。"
        next_action = "打开 BioRender 登录页面"
        if active is not None:
            if active.kind == "manual_login":
                state = "login_checking"
                step = 1
                reason = active.message
                next_action = "完成登录后检查状态"
            elif active.kind == "canvas_check":
                state = "canvas_validating"
                step = 2
                reason = active.message
                next_action = "等待画布检查完成"
            elif active.kind == "live_figure":
                state = "stop_requested" if active.status == "stop_requested" else "executing"
                step = 4
                reason = active.message
                next_action = "等待执行完成，或安全停止"
        elif not self._login_verified:
            pass
        elif not self._verified_canvas:
            state = "canvas_required"
            step = 2
            reason = "登录已确认，请指定并检查可测试的空白画布。"
            next_action = "输入 Figure URL 并检查画布"
        elif run_id:
            summary = self.run_summary(run_id)
            status = str(summary["status"])
            step = 5 if status in {
                "awaiting_confirmation", "completed", "failed", "blocked",
                "paused_authentication", "paused_approval", "paused_reconciliation",
            } else 4
            if status in {"executing", "verifying", "repairing"}:
                state = "verifying" if status == "verifying" else "executing"
                step = 4
                reason = str(summary["friendly_status"])
                next_action = "等待任务完成"
            elif status in {"paused_authentication", "paused_approval", "paused_reconciliation"}:
                state = "paused"
                step = 4
                reason = str(summary["friendly_status"])
                next_action = "安全检查后继续未完成任务"
            elif status == "blocked":
                state = "blocked_by_policy"
                reason = "任务被安全策略阻止，不能把它标记为成功。"
                next_action = "查看运行详情或开始新任务"
            elif status == "failed":
                state = "failed"
                reason = "执行失败，请查看运行详情。"
                next_action = "重新验证、继续任务或开始新任务"
            elif summary["needs_review_elements"] > 0:
                state = "completed_with_unknown"
                reason = "任务已结束，但有元素需要人工检查。"
                next_action = "重新验证结果"
            else:
                state = "completed"
                reason = "任务已结束，结果已通过当前证据验证。"
                next_action = "查看完成结果"
        elif dry_run_id:
            metadata = self._dry_run_metadata(dry_run_id)
            if metadata["completed"] and not metadata["confirmed"]:
                state = "dry_run_confirmation_required"
                step = 3
                reason = "安全预演已完成，未操作真实 BioRender 页面。"
                next_action = "确认预演结果并继续"
            elif metadata["confirmed"]:
                state = "ready_to_execute"
                step = 4
                reason = "预演结果已确认，开始执行前会再次提示将修改真实画布。"
                next_action = "开始执行"
        elif plan_id:
            state = "prompt_parsed"
            step = 3
            reason = "需求已解析，可以进入执行步骤。"
            next_action = "进入执行步骤"
        else:
            state = "prompt_required"
            step = 3
            reason = "画布已检查，请选择预设或输入科研绘图需求。"
            next_action = "解析需求"
        step3_phase = {
            "prompt_required": "parse_required",
            "prompt_parsed": "parsed",
            "dry_run_confirmation_required": "confirmation_required",
            "ready_to_execute": "confirmed",
        }.get(state)
        next_block_reason = {
            "prompt_required": "请先解析绘图需求",
            "prompt_parsed": "",
            "dry_run_confirmation_required": "请先确认安全预演结果",
        }.get(state, "")
        return {
            "state": state,
            "step": step,
            "reason": reason,
            "next_action": next_action,
            "step3_phase": step3_phase,
            "next_block_reason": next_block_reason,
            "refresh_recoverable": bool(active or plan_id or dry_run_id or run_id),
            "buttons": {
                "open_login": state == "login_required",
                "check_login": state == "login_checking",
                "check_canvas": state == "canvas_required",
                "parse_prompt": state in {"prompt_required", "prompt_parsed"},
                "run_dry_run": state == "prompt_parsed",
                "confirm_dry_run": state == "dry_run_confirmation_required",
                "start_live": state == "ready_to_execute",
                "safe_stop": state
                in {"login_checking", "canvas_validating", "executing", "stop_requested"},
                "resume": state == "paused",
                "verify": state in {"completed", "completed_with_unknown", "failed"},
            },
            "plan_summary": (
                self._plan_metadata(plan_id)["summary"] if plan_id else None
            ),
        }

    def run_summary(
        self,
        figure_id: str,
        *,
        status_override: FigureStatus | None = None,
    ) -> dict[str, Any]:
        record = self.database.get_figure(figure_id)
        if record is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到绘图任务。")
        actions = self.database.action_states(figure_id)
        requirements = self.database.list_element_requirements(figure_id)
        status = status_override.value if status_override else record["status"]
        status_counts = self._status_counts(requirements)
        action_counts = self._status_counts(actions)
        dry_run = self._dry_run_metadata(figure_id)
        source = self._live_source_metadata(figure_id)
        prepare_failure = self._prepare_failure_metadata(figure_id, actions)
        current_action = self._current_action(figure_id, actions)
        can_resume, resume_blocked_reason = self._resume_availability(
            status,
            current_action=current_action,
            prepare_failure=prepare_failure,
        )
        # A live run inherits its dry-run gate from the source dry run, so the
        # UI can display the real linkage instead of "task_fingerprint=null".
        effective_dry_run_completed = dry_run["completed"] or bool(
            source.get("dry_run_completed")
        )
        effective_dry_run_confirmed = dry_run["confirmed"] or bool(
            source.get("dry_run_confirmed")
        )
        effective_task_fingerprint = (
            dry_run["task_fingerprint"] or source.get("task_fingerprint")
        )
        return {
            "run_id": figure_id,
            "title": record["title"],
            "status": status,
            "friendly_status": self.friendly_status(status),
            "total_elements": len(requirements),
            "verified_elements": status_counts.get("verified", 0),
            "needs_review_elements": status_counts.get("unknown", 0),
            "failed_elements": status_counts.get("failed", 0),
            "policy_blocked_elements": status_counts.get("blocked_by_policy", 0),
            "total_actions": len(actions),
            "verified_actions": action_counts.get("verified", 0),
            "progress_percent": self._progress_percent(actions),
            "steps": self._progress_steps(actions, status),
            "save_status": self._save_status(figure_id),
            "can_resume": can_resume,
            "resume_blocked_reason": resume_blocked_reason,
            "can_stop": self._active_job_for_figure(figure_id) is not None,
            "run_mode": "dry_run" if dry_run["completed"] else "live_or_plan",
            "dry_run_completed": effective_dry_run_completed,
            "dry_run_confirmed": effective_dry_run_confirmed,
            "can_confirm_dry_run": (
                dry_run["completed"]
                and not dry_run["confirmed"]
                and status == FigureStatus.AWAITING_CONFIRMATION.value
            ),
            "real_biorender_accepted": False,
            "task_fingerprint": effective_task_fingerprint,
            "source_dry_run_id": source.get("dry_run_id"),
            "source_plan_id": source.get("plan_id"),
            "dry_run_gate": source.get("gate"),
            "prepare_failure": prepare_failure,
            "completed_at": (
                record["updated_at"]
                if status in {"completed", "awaiting_confirmation", "failed", "blocked"}
                else None
            ),
            "current_action": current_action,
            "completed_actions": sum(
                item["status"] in {"verified", "succeeded"} for item in actions
            ),
            "recent_logs": [
                {
                    "status": item["status"],
                    "action_type": item["action_type"],
                    "message": item.get("error_type") or item["status"],
                }
                for item in actions[-5:]
            ],
        }

    # ------------------------------------------------------------------ helpers

    def _live_source_metadata(self, figure_id: str) -> dict[str, Any]:
        """Return the dry-run / plan lineage recorded when this Live Run started.

        A live run is a fresh figure, so its own ``figure_id`` has no
        ``dry_run_completed`` audit event. The linkage lives on a
        ``live_started_from_confirmed_dry_run`` (or ``live_started_from_plan``)
        event pointing to the source dry-run / plan figure. Surface that
        linkage — otherwise the UI shows an unrelated, always-false gate.
        """
        gate: str | None = None
        source_dry_run_id: str | None = None
        source_plan_id: str | None = None
        for event in self.database.list_audit_events(figure_id=figure_id):
            if event["event_type"] == "live_started_from_confirmed_dry_run":
                gate = "confirmed_dry_run"
                payload = event.get("payload") or {}
                source_dry_run_id = payload.get("dry_run_id") or source_dry_run_id
                source_plan_id = payload.get("plan_id") or source_plan_id
            elif event["event_type"] == "live_started_from_plan":
                gate = gate or "plan_only"
                payload = event.get("payload") or {}
                source_plan_id = payload.get("plan_id") or source_plan_id
        source_meta: dict[str, Any] = {
            "gate": gate,
            "dry_run_id": source_dry_run_id,
            "plan_id": source_plan_id,
            "dry_run_completed": False,
            "dry_run_confirmed": False,
            "task_fingerprint": None,
        }
        if source_dry_run_id:
            dry = self._dry_run_metadata(source_dry_run_id)
            source_meta["dry_run_completed"] = dry["completed"]
            source_meta["dry_run_confirmed"] = dry["confirmed"]
            source_meta["task_fingerprint"] = dry["task_fingerprint"]
        elif source_plan_id:
            plan = self._plan_metadata(source_plan_id)
            source_meta["task_fingerprint"] = plan.get("task_fingerprint")
        return source_meta

    @staticmethod
    def _prepare_failure_metadata(
        figure_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Extract the structured prepare-phase failure from action metadata.

        The Playwright operator raises ``EditorPrepareFailed`` with a
        ``subcode`` and captured URLs when ``open_biorender_editor`` cannot
        reach the editor (redirect, timeout, canvas missing, browser closed).
        The workflow engine stashes that payload into the action result's
        ``metadata.editor_prepare_failure``; surface it here so callers do not
        have to walk audit events to reason about the failure.
        """
        del figure_id  # kept for future audit-event fallback
        prepare_action = next(
            (
                item
                for item in actions
                if item.get("action_type") == "open_biorender_editor"
            ),
            None,
        )
        if prepare_action is None:
            return None
        if prepare_action.get("status") != "failed":
            return None
        result = prepare_action.get("result") or {}
        metadata = result.get("metadata") if isinstance(result, dict) else None
        if not isinstance(metadata, dict):
            metadata = {}
        payload = metadata.get("editor_prepare_failure")
        if isinstance(payload, dict):
            return {
                "action_id": prepare_action.get("id"),
                "error_type": result.get("error_type"),
                "message": result.get("message"),
                "subcode": payload.get("subcode"),
                "requested_url": payload.get("requested_url"),
                "observed_url": payload.get("observed_url"),
                "screenshot_path": payload.get("screenshot_path"),
                "attempt": prepare_action.get("attempts"),
            }
        calibration_payload = metadata.get("ui_calibration_failure")
        if isinstance(calibration_payload, dict):
            return {
                "action_id": prepare_action.get("id"),
                "error_type": calibration_payload.get("error_type")
                or "ui_calibration_failed",
                "message": result.get("message"),
                "subcode": "ui_calibration_failed",
                "requested_url": None,
                "observed_url": None,
                "screenshot_path": result.get("screenshot_path"),
                "profile_path": calibration_payload.get("profile_path"),
                "missing_anchors": calibration_payload.get("missing_anchors") or [],
                "anchor_diagnostics": calibration_payload.get("anchor_diagnostics") or [],
                "attempt": prepare_action.get("attempts"),
            }
        # Fallback: prepare failed but no structured payload was captured.
        return {
            "action_id": prepare_action.get("id"),
            "error_type": result.get("error_type") or "operator_error",
            "message": result.get("message"),
            "subcode": None,
            "requested_url": None,
            "observed_url": None,
            "screenshot_path": result.get("screenshot_path"),
            "attempt": prepare_action.get("attempts"),
        }

    @staticmethod
    def _resume_availability(
        status: str,
        *,
        current_action: dict[str, Any] | None,
        prepare_failure: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        """Decide whether the Resume button is safe to offer.

        The previous rule marked every ``FAILED`` run resumable, which meant a
        prepare-phase failure — where the browser never reached the editor —
        would silently invite the user to press Resume and immediately
        re-fail. Now a Resume is only offered when a step-boundary pause
        happened, or when a Live Run failed *after* the editor was already
        prepared (so the environment is at least reachable).
        """
        paused_states = {
            FigureStatus.PAUSED_AUTHENTICATION.value,
            FigureStatus.PAUSED_APPROVAL.value,
            FigureStatus.PAUSED_RECONCILIATION.value,
        }
        if status in paused_states:
            return True, None
        if status != FigureStatus.FAILED.value:
            return False, None
        if prepare_failure is not None:
            subcode = prepare_failure.get("subcode")
            reason_by_subcode = {
                "navigation_timeout": (
                    "打开 BioRender 编辑器超时，请检查网络与 URL 后重新开始任务，而不是继续。"
                ),
                "navigation_error": "Playwright 无法打开该 Figure URL，请检查后重新开始任务。",
                "redirected_off_domain": (
                    "该 URL 被跳转出 BioRender 官方域名，Resume 无法修复；请更换 Figure URL。"
                ),
                "redirected_to_login": "浏览器会话失效，需要重新登录后再新开任务，而不是继续。",
                "canvas_not_found": "未检测到画布，请确认 Figure URL 指向可编辑画布后重新开始。",
                "page_closed": "浏览器窗口被关闭，请重新登录并新开任务。",
                "browser_profile_locked": "浏览器 Profile 被占用，请关闭其他实例后重新开始。",
                "browser_launch_failed": "Chromium 无法启动，请检查 Playwright 安装后重新开始。",
                "ui_calibration_failed": (
                    "BioRender 界面校准失败；在校准策略更新或重新校准成功前不能继续。"
                ),
            }
            return (
                False,
                reason_by_subcode.get(
                    str(subcode) if subcode is not None else "",
                    "准备阶段失败，Resume 无法修复浏览器环境；请修正后重新开始任务。",
                ),
            )
        current_type = (
            (current_action or {}).get("action_type") if current_action else None
        )
        if current_type == ActionType.OPEN_EDITOR.value:
            return (
                False,
                "准备阶段失败，Resume 无法修复浏览器环境；请修正后重新开始任务。",
            )
        return True, None

    def element_summary(self, figure_id: str) -> list[dict[str, Any]]:
        if self.database.get_figure(figure_id) is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到绘图任务。")
        observed = {
            item["element_id"]: item
            for item in self.database.list_editor_elements(figure_id)
        }
        result: list[dict[str, Any]] = []
        for requirement in self.database.list_element_requirements(figure_id):
            item = observed.get(requirement["logical_element_id"])
            result.append(
                {
                    "name": requirement["scientific_role"],
                    "type": self.friendly_kind(requirement["kind"]),
                    "status": requirement["status"],
                    "friendly_status": self.friendly_status(requirement["status"]),
                    "verified": requirement["status"] == "verified",
                    "message": self._element_message(requirement, item),
                    "details": {
                        "logical_element_id": requirement["logical_element_id"],
                        "observation_source": (
                            item.get("observation_source") if item else None
                        ),
                        "observation_confidence": (
                            item.get("observation_confidence") if item else None
                        ),
                    },
                }
            )
        return result

    def evidence_summary(self, figure_id: str) -> list[dict[str, Any]]:
        if self.database.get_figure(figure_id) is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到绘图任务。")
        result: list[dict[str, Any]] = []
        for item in self.database.list_screenshots(figure_id):
            path = Path(item["path"])
            image = self._safe_evidence_path(path) is not None
            result.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "name": path.name,
                    "created_at": item["created_at"],
                    "is_image": image,
                    "preview_url": (
                        f"/api/ui/evidence/{item['id']}" if image else None
                    ),
                }
            )
        return result

    def evidence_path(self, evidence_id: str) -> Path:
        if evidence_id == "calibration-latest":
            calibration = self.database.latest_calibration_profile()
            item = (
                {"path": calibration["screenshot_path"]}
                if calibration is not None
                else None
            )
        elif evidence_id.isdecimal():
            item = self.database.get_screenshot(int(evidence_id))
        else:
            item = None
        if item is None:
            raise UiServiceError("EVIDENCE_NOT_FOUND", "未找到截图证据。")
        path = self._safe_evidence_path(Path(item["path"]))
        if path is None:
            raise UiServiceError("EVIDENCE_ACCESS_DENIED", "证据文件不在允许目录中。")
        if not path.exists() or not path.is_file():
            raise UiServiceError("EVIDENCE_NOT_FOUND", "截图证据文件不存在。")
        return path

    def verify_run(self, figure_id: str) -> dict[str, Any]:
        summary = self.run_summary(figure_id)
        requirements = self.database.list_element_requirements(figure_id)
        elements = self.database.list_editor_elements(figure_id)
        required = {
            kind: sum(item["kind"] == kind for item in requirements)
            for kind in ("asset", "label", "connector", "group")
        }
        observed = {
            kind: sum(
                item["kind"] == kind and item["status"] == "verified"
                for item in elements
            )
            for kind in required
        }
        layout = next(
            (item for item in elements if item["element_id"] == "layout_quality"),
            None,
        )
        saved = next(
            (item for item in elements if item["element_id"] == "document_save"),
            None,
        )
        inventory_passed = all(observed[kind] >= count for kind, count in required.items())
        layout_passed = bool(
            layout
            and (layout.get("verification") or {}).get("layout", {}).get("passed")
        )
        save_passed = bool(
            saved and (saved.get("verification") or {}).get("save", {}).get("passed")
        )
        passed = bool(
            inventory_passed
            and layout_passed
            and save_passed
            and requirements
            and all(item["status"] == "verified" for item in requirements)
        )
        return {
            **summary,
            "verification_passed": passed,
            "required_inventory": required,
            "verified_inventory": observed,
            "layout_passed": layout_passed,
            "save_passed": save_passed,
            "message": (
                "持久化证据满足当前计划，仍需用户检查科研表达。"
                if passed
                else "部分证据尚未满足，请检查元素状态和截图。"
            ),
        }

    @staticmethod
    def redact_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/.../<redacted>"

    @staticmethod
    def friendly_status(status: str) -> str:
        return {
            "created": "已创建",
            "planned": "等待执行",
            "validated": "方案已检查",
            "searching": "正在搜索",
            "candidate_selected": "已选择普通素材",
            "executing": "正在操作",
            "executed_unverified": "已操作，正在确认",
            "verifying": "正在验证",
            "verified": "已确认",
            "succeeded": "预演已完成",
            "unknown": "需要人工检查",
            "failed": "操作失败",
            "blocked": "已被安全策略阻止",
            "blocked_by_policy": "已被安全策略阻止",
            "paused_authentication": "等待人工登录",
            "paused_approval": "已安全暂停",
            "paused_reconciliation": "需要人工检查",
            "awaiting_confirmation": "等待人工确认",
            "completed": "用户已确认",
        }.get(status, status)

    @staticmethod
    def friendly_kind(kind: str) -> str:
        return {
            "asset": "素材",
            "label": "标签",
            "connector": "连接关系",
            "group": "分组",
            "alignment": "对齐",
            "distribution": "分布",
            "region": "画布区域",
            "z_order": "图层顺序",
            "save_state": "保存状态",
        }.get(kind, kind)

    @staticmethod
    def _task_fingerprint(task: UiTaskInput) -> str:
        payload = json.dumps(
            task.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _dry_run_metadata(self, figure_id: str) -> dict[str, Any]:
        completed_event = None
        confirmed_event = None
        for event in self.database.list_audit_events(figure_id=figure_id):
            if event["event_type"] == "dry_run_completed":
                completed_event = event
            elif event["event_type"] == "dry_run_confirmed":
                confirmed_event = event
        completed_payload = completed_event["payload"] if completed_event else {}
        return {
            "completed": completed_event is not None,
            "confirmed": confirmed_event is not None,
            "task_fingerprint": completed_payload.get("task_fingerprint"),
        }

    def _plan_metadata(self, figure_id: str) -> dict[str, Any]:
        parsed_event = next(
            (
                event
                for event in reversed(
                    self.database.list_audit_events(figure_id=figure_id)
                )
                if event["event_type"] == "ui_plan_parsed"
            ),
            None,
        )
        payload = parsed_event["payload"] if parsed_event else {}
        return {
            "parsed": parsed_event is not None,
            "task_fingerprint": payload.get("task_fingerprint"),
            "summary": payload.get("summary"),
        }

    def _latest_confirmed_dry_run(self) -> dict[str, Any] | None:
        for record in self.database.list_figures(limit=100):
            metadata = self._dry_run_metadata(record["id"])
            if metadata["confirmed"] and record["status"] == FigureStatus.COMPLETED.value:
                return {
                    "run_id": record["id"],
                    "title": record["title"],
                    "confirmed_at": record["updated_at"],
                    "task_fingerprint": metadata["task_fingerprint"],
                }
        return None

    def _require_login_verified(self) -> None:
        if not self._login_verified:
            raise UiServiceError(
                "LOGIN_REQUIRED",
                "请先在第一步完成人工登录并检查状态。",
            )

    def _require_verified_canvas(self, editor_url: str) -> None:
        if not self._verified_canvas:
            raise UiServiceError(
                "CANVAS_NOT_VERIFIED",
                "请先在第二步检查目标画布。",
            )
        if self._verified_canvas.get("editor_url") != editor_url:
            raise UiServiceError(
                "CANVAS_NOT_VERIFIED",
                "Figure URL 已更改，请重新检查画布。",
            )

    def _require_parsed_plan(self, plan_id: str, task: UiTaskInput) -> None:
        record = self.database.get_figure(plan_id)
        if record is None:
            raise UiServiceError("PLAN_REQUIRED", "请先解析当前绘图需求。")
        metadata = self._plan_metadata(plan_id)
        if not metadata["parsed"]:
            raise UiServiceError("PLAN_REQUIRED", "请先解析当前绘图需求。")
        if metadata["task_fingerprint"] != self._task_fingerprint(task):
            raise UiServiceError(
                "PLAN_TASK_MISMATCH",
                "绘图需求已更改，请重新解析后再运行安全预演。",
            )
        if record["status"] == FigureStatus.BLOCKED.value:
            raise UiServiceError(
                "PLAN_NOT_EXECUTABLE",
                "当前需求包含无法支持或被安全策略阻止的内容，请返回修改。",
            )

    def _require_confirmed_dry_run(
        self,
        dry_run_id: str | None,
        task: UiTaskInput,
    ) -> None:
        if not dry_run_id:
            raise UiServiceError(
                "DRY_RUN_CONFIRMATION_REQUIRED",
                "开始真实操作前，请先完成并确认安全预演结果。",
            )
        record = self.database.get_figure(dry_run_id)
        if record is None:
            raise UiServiceError("RUN_NOT_FOUND", "未找到对应的安全预演。")
        metadata = self._dry_run_metadata(dry_run_id)
        if not metadata["confirmed"] or record["status"] != FigureStatus.COMPLETED.value:
            raise UiServiceError(
                "DRY_RUN_CONFIRMATION_REQUIRED",
                "安全预演尚未验收。请打开预演结果并点击“确认预演结果”。",
            )
        if metadata["task_fingerprint"] != self._task_fingerprint(task):
            raise UiServiceError(
                "DRY_RUN_TASK_MISMATCH",
                "绘图任务已更改，请对当前任务重新执行并确认安全预演。",
            )

    def _recover_interrupted_runs(self) -> None:
        for record in self.database.list_figures(limit=100):
            if record["status"] not in INTERRUPTED_FIGURE_STATES:
                continue
            self.database.set_status(record["id"], FigureStatus.PAUSED_RECONCILIATION)
            self.database.add_audit_event(
                "service_recovered_interrupted_run",
                {
                    "previous_status": record["status"],
                    "recovered_status": FigureStatus.PAUSED_RECONCILIATION.value,
                },
                figure_id=record["id"],
            )

    def _new_live_operator(self) -> Any:
        if self._live_operator_factory is not None:
            return self._live_operator_factory(self.database)
        from app.operator.playwright_live import LivePlaywrightOperator

        return LivePlaywrightOperator(database=self.database, headed=True)

    def _run_live_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        self._update_job(job, "running", "正在使用浏览器执行绘图任务。")
        try:
            status = self.engine.execute(
                job.figure_id or "",
                self._new_live_operator(),
                stop_requested=job.stop_event.is_set,
            )
            if status == FigureStatus.PAUSED_APPROVAL and job.stop_event.is_set():
                self._update_job(job, "stopped", "任务已在动作边界安全暂停。")
            elif status == FigureStatus.BLOCKED:
                self._update_job(job, "blocked", "任务已被安全策略阻止。")
            else:
                self._update_job(
                    job,
                    "completed",
                    self.friendly_status(status.value),
                    result={"figure_status": status.value},
                )
        except Exception as error:
            self._update_job(
                job,
                "failed",
                "后台绘图任务失败，请查看运行详情。",
                error_code=type(error).__name__,
            )

    def _run_calibration_job(self, job_id: str) -> None:
        from app.operator.biorender.calibration import BioRenderUiCalibrator

        job = self._jobs[job_id]
        editor_url = str(job.input_data["editor_url"])
        self._update_job(job, "running", "正在打开 BioRender 并校准界面。")
        operator = self._new_live_operator()
        try:
            page = operator.page
            page.goto(editor_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            profile, _ = BioRenderUiCalibrator(page, database=self.database).calibrate()
            if job.stop_event.is_set():
                self._update_job(job, "stopped", "界面校准已安全停止。")
            else:
                self._update_job(
                    job,
                    "completed",
                    "界面校准完成。",
                    result={
                        "status": profile.status.value,
                        "profile_id": profile.profile_id,
                        "screenshot_available": bool(profile.screenshot_path),
                    },
                )
        except Exception as error:
            missing_anchors = list(getattr(error, "missing_anchors", []) or [])
            profile_path = getattr(error, "profile_path", None)
            self._update_job(
                job,
                "failed",
                (
                    "界面校准失败，缺少锚点：" + ", ".join(missing_anchors)
                    if missing_anchors
                    else "界面校准失败，请查看校准证据。"
                ),
                error_code=getattr(error, "error_type", type(error).__name__),
                diagnostic_hint=(
                    f"校准 profile：{profile_path}" if profile_path else None
                ),
                result={
                    "can_resume": False,
                    "missing_anchors": missing_anchors,
                    "profile_path": profile_path,
                    "anchor_diagnostics": list(
                        getattr(error, "anchor_diagnostics", []) or []
                    ),
                },
            )
        finally:
            operator.close()

    def _run_canvas_check_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        editor_url = str(job.input_data["editor_url"])
        self._update_job(job, "running", "正在检查 BioRender 画布。")
        operator = self._new_live_operator()
        try:
            page = operator.page
            page.goto(editor_url, wait_until="domcontentloaded", timeout=60_000)
            title = str(page.title() or "BioRender Figure").strip()[:160]
            final_url = str(page.url)
            parsed = urlparse(final_url)
            hostname = (parsed.hostname or "").casefold()
            if hostname != "biorender.com" and not hostname.endswith(".biorender.com"):
                raise UiServiceError(
                    "CANVAS_ACCESS_FAILED",
                    "画布检查后离开了 BioRender 官方域名。",
                )
            if any(part in parsed.path.casefold() for part in ("login", "signin")):
                raise UiServiceError(
                    "LOGIN_REQUIRED",
                    "BioRender 登录状态已失效，请返回第一步重新登录。",
                )
            if job.stop_event.is_set():
                self._verified_canvas = None
                self._update_job(job, "stopped", "画布检查已安全停止。")
                return
            checked_at = datetime.now(UTC).isoformat()
            figure_identifier = next(
                (part for part in reversed(parsed.path.split("/")) if part),
                "BioRender Figure",
            )[:120]
            self._verified_canvas = {
                "editor_url": editor_url,
                "redacted_url": self.redact_url(editor_url),
                "figure_identifier": figure_identifier,
                "title": title,
                "checked_at": checked_at,
            }
            self._update_job(
                job,
                "completed",
                "画布检查通过。",
                result={
                    "canvas_verified": True,
                    "redacted_url": self.redact_url(editor_url),
                    "figure_identifier": figure_identifier,
                    "title": title,
                    "checked_at": checked_at,
                },
            )
        except Exception as error:
            self._verified_canvas = None
            self._update_job(
                job,
                "failed",
                "画布检查失败，请确认登录状态和 Figure URL。",
                error_code=getattr(error, "error_code", type(error).__name__),
            )
        finally:
            operator.close()

    def _run_login_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        self._update_job(job, "running", "正在打开人工登录窗口。")
        operator: Any | None = None
        try:
            operator = self._new_live_operator()
            page = operator.page
            page.goto(
                "https://app.biorender.com/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            self._update_job(
                job,
                "waiting_user",
                "请在浏览器中手动登录，完成后返回本页面确认。",
            )
            while not self._login_complete.is_set():
                is_closed = getattr(page, "is_closed", None)
                if callable(is_closed) and is_closed():
                    raise UiServiceError(
                        "LOGIN_WINDOW_CLOSED",
                        "人工登录窗口已被关闭，浏览器任务已释放。",
                        diagnostic_hint="可以重新点击“打开 BioRender 登录页面”。",
                    )
                wait_for_timeout = getattr(page, "wait_for_timeout", None)
                if callable(wait_for_timeout):
                    wait_for_timeout(int(LOGIN_WINDOW_POLL_SECONDS * 1000))
                else:
                    self._login_complete.wait(LOGIN_WINDOW_POLL_SECONDS)
            if job.stop_event.is_set():
                self._update_job(job, "stopped", "人工登录任务已安全停止。")
            else:
                current_url = str(page.url)
                parsed = urlparse(current_url)
                hostname = (parsed.hostname or "").casefold()
                if (
                    hostname != "biorender.com"
                    and not hostname.endswith(".biorender.com")
                ) or any(part in parsed.path.casefold() for part in ("login", "signin")):
                    raise UiServiceError(
                        "LOGIN_REQUIRED",
                        "尚未确认 BioRender 登录状态，请完成登录后再检查。",
                        diagnostic_hint="请在打开的 Chromium 中完成登录后再次检查。",
                    )
                self._login_verified = True
                self._update_job(job, "completed", "登录状态已确认，浏览器任务已释放。")
        except Exception as error:
            error_code, message, diagnostic_hint = self._manual_login_error(error)
            logger.exception(
                "Manual login job %s failed with %s",
                job.id,
                error_code,
            )
            self._update_job(
                job,
                "failed",
                message,
                error_code=error_code,
                diagnostic_hint=diagnostic_hint,
            )
        finally:
            if operator is not None:
                try:
                    operator.close()
                except Exception:
                    logger.exception("Failed to close manual login operator for %s", job.id)

    def _wait_for_manual_login_startup(self, job: ManagedJob) -> None:
        deadline = monotonic() + LOGIN_STARTUP_WAIT_SECONDS
        while True:
            with self._lock:
                if job.status == "waiting_user" or job.status in FINAL_JOB_STATES:
                    return
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return
                job.state_event.clear()
            job.state_event.wait(min(remaining, LOGIN_WINDOW_POLL_SECONDS))

    @staticmethod
    def _manual_login_error(error: Exception) -> tuple[str, str, str]:
        if isinstance(error, UiServiceError):
            return (
                error.error_code,
                str(error),
                error.diagnostic_hint or "请查看后端异常日志后重试。",
            )

        raw_message = str(error)
        normalized = raw_message.casefold()
        if any(
            marker in normalized
            for marker in (
                "target page, context or browser has been closed",
                "target closed",
                "browser has been closed",
                "browser disconnected",
            )
        ):
            return (
                "LOGIN_WINDOW_CLOSED",
                "人工登录窗口已被关闭，浏览器任务已释放。",
                "可以重新点击“打开 BioRender 登录页面”。",
            )
        if isinstance(error, ImportError) or "optional browser dependencies" in normalized:
            return (
                "PLAYWRIGHT_NOT_INSTALLED",
                "启动 Web UI 的 Python 环境未安装 Playwright。",
                "请在启动 Web UI 的同一 Python 环境安装项目 browser 依赖。",
            )
        if "executable doesn't exist" in normalized or (
            "playwright install" in normalized and "chromium" in normalized
        ):
            return (
                "CHROMIUM_NOT_INSTALLED",
                "Chromium 未安装，无法打开人工登录窗口。",
                "请在启动 Web UI 的同一 Python 环境运行："
                "python -m playwright install chromium",
            )
        if any(
            marker in normalized
            for marker in (
                "processsingleton",
                "profile in use",
                "user data directory is already in use",
                "cannot create a process singleton",
            )
        ):
            return (
                "BROWSER_PROFILE_IN_USE",
                "浏览器 Profile 被其他项目进程占用。",
                "请安全停止本项目旧登录任务，关闭其 Playwright 窗口后重试。",
            )
        return (
            "PLAYWRIGHT_LAUNCH_FAILED",
            "Playwright 无法启动人工登录 Chromium。",
            "请确认 Web UI 运行于可创建桌面窗口的用户会话，并查看后端异常日志。",
        )

    def _start_thread(
        self,
        job: ManagedJob,
        target: Callable[[str], None],
    ) -> None:
        with self._lock:
            self._jobs[job.id] = job
            thread = threading.Thread(
                target=self._run_job_target,
                args=(job.id, target),
                name=f"biorender-{job.kind}-{job.id}",
                daemon=True,
            )
            self._threads[job.id] = thread
            thread.start()

    def _update_job(
        self,
        job: ManagedJob,
        status: str,
        message: str,
        *,
        error_code: str | None = None,
        diagnostic_hint: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            job.status = status
            job.message = message
            job.error_code = error_code
            job.diagnostic_hint = diagnostic_hint
            job.updated_at = datetime.now(UTC).isoformat()
            if result is not None:
                job.result = result
            job.state_event.set()

    def _run_job_target(
        self,
        job_id: str,
        target: Callable[[str], None],
    ) -> None:
        try:
            target(job_id)
        except BaseException as error:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None and job.status not in FINAL_JOB_STATES:
                    self._update_job(
                        job,
                        "stopped" if job.stop_event.is_set() else "failed",
                        (
                            "后台任务已安全停止。"
                            if job.stop_event.is_set()
                            else "后台任务异常退出，已释放浏览器任务锁。"
                        ),
                        error_code=type(error).__name__,
                        diagnostic_hint="请查看后端异常日志后重试。",
                    )

    def _request_job_stop_locked(self, job: ManagedJob) -> dict[str, Any]:
        job.stop_event.set()
        job.status = "stop_requested"
        job.message = "已请求安全停止，将在当前 GUI 动作结束后暂停。"
        job.diagnostic_hint = None
        job.updated_at = datetime.now(UTC).isoformat()
        job.state_event.set()
        if job.kind == "manual_login":
            self._login_complete.set()
        return job.public()

    def _reap_stale_jobs_locked(self) -> None:
        for job in self._jobs.values():
            if job.status in FINAL_JOB_STATES:
                continue
            thread = self._threads.get(job.id)
            if thread is not None and thread.is_alive():
                continue
            job.status = "stopped" if job.stop_event.is_set() else "failed"
            job.message = (
                "后台任务已安全停止，浏览器任务锁已释放。"
                if job.stop_event.is_set()
                else "检测到后台任务已退出，浏览器任务锁已自动释放。"
            )
            job.error_code = None if job.stop_event.is_set() else "STALE_JOB_RECOVERED"
            job.diagnostic_hint = (
                None if job.stop_event.is_set() else "可以重新启动该任务。"
            )
            job.updated_at = datetime.now(UTC).isoformat()
            job.state_event.set()
            if job.figure_id:
                record = self.database.get_figure(job.figure_id)
                if record and record["status"] in INTERRUPTED_FIGURE_STATES:
                    self.database.set_status(
                        job.figure_id,
                        FigureStatus.PAUSED_RECONCILIATION,
                    )
                    self.database.add_audit_event(
                        "stale_job_recovered",
                        {"job_id": job.id, "previous_status": record["status"]},
                        figure_id=job.figure_id,
                    )

    def _ensure_browser_available(self) -> None:
        with self._lock:
            self._reap_stale_jobs_locked()
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.kind in BROWSER_JOB_KINDS
                    and job.status not in FINAL_JOB_STATES
                ),
                None,
            )
            if active is not None:
                if active.kind == "manual_login":
                    raise UiServiceError(
                        "LOGIN_JOB_ALREADY_ACTIVE",
                        "已有人工登录任务正在运行。",
                        diagnostic_hint="请使用现有登录窗口，或先安全停止后再重试。",
                    )
                raise UiServiceError(
                    "BROWSER_BUSY",
                    "浏览器正在执行其他任务，请等待或安全停止后再试。",
                    diagnostic_hint=f"当前任务：{active.kind}（{active.id}）。",
                )

    def _active_job_for_figure(self, figure_id: str) -> ManagedJob | None:
        with self._lock:
            self._reap_stale_jobs_locked()
            return next(
                (
                    job
                    for job in self._jobs.values()
                    if job.figure_id == figure_id and job.status not in FINAL_JOB_STATES
                ),
                None,
            )

    @staticmethod
    def _custom_spec(custom: CustomFigureInput) -> tuple[Requirement, FigureSpec]:
        figure_id = f"figure_{uuid.uuid4().hex[:12]}"
        entities = [
            Entity(
                id=asset.id,
                concept=asset.search_term,
                category=EntityCategory.GENERIC,
                label=asset.label_text or asset.display_name,
                aliases=asset.fallback_terms,
                region_id="main",
            )
            for asset in custom.assets
        ]
        relation_types = {
            "line": RelationType.ASSOCIATION,
            "arrow": RelationType.FLOW,
            "inhibition": RelationType.INHIBITION,
        }
        relations = [
            Relation(
                id=f"relation_{index + 1}",
                source=relation.source_id,
                target=relation.target_id,
                type=relation_types[relation.type],
                region_id="main",
            )
            for index, relation in enumerate(custom.relations)
        ]
        source_text = " ".join(
            value for value in (custom.research_topic, custom.notes) if value
        )
        requirement = Requirement(
            title=custom.title,
            purpose="mechanism_figure",
            audience="research_presentation",
            orientation="left_to_right",
            complexity=("medium" if len(custom.assets) > 6 else "low"),
            required_sections=["main"],
            preferred_language=(
                "Chinese"
                if any("\u3400" <= character <= "\u9fff" for character in source_text)
                else "English"
            ),
            source_text=source_text,
        )
        spec = FigureSpec(
            id=figure_id,
            title=custom.title,
            layout_type=LayoutType.LINEAR,
            entities=entities,
            relations=relations,
            required_concepts=[entity.concept for entity in entities],
            scientific_assumptions=[
                "Entities, search terms, labels, and relations were explicitly provided "
                "through the limited graphical form."
            ],
        )
        return requirement, spec

    @staticmethod
    def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in items:
            status = str(item["status"])
            result[status] = result.get(status, 0) + 1
        return result

    @staticmethod
    def _progress_percent(actions: list[dict[str, Any]]) -> int:
        if not actions:
            return 0
        done = sum(
            item["status"] in {"verified", "succeeded", "blocked_by_policy", "failed"}
            for item in actions
        )
        return round(done / len(actions) * 100)

    def _progress_steps(
        self,
        actions: list[dict[str, Any]],
        figure_status: str,
    ) -> list[dict[str, str]]:
        groups: list[tuple[str, str, set[str]]] = [
            ("prepare", "准备浏览器", {"open_biorender_editor"}),
            ("policy", "检查安全策略", set()),
            ("search", "搜索素材", {"search_asset", "select_asset_candidate"}),
            (
                "insert",
                "插入素材",
                {"drag_selected_asset", "move_element", "resize_element", "rotate_element"},
            ),
            ("labels", "添加标签", {"add_text", "edit_text"}),
            ("connect", "添加连接关系", {"connect_elements"}),
            (
                "layout",
                "调整布局",
                {"group_elements", "align_elements", "distribute_elements"},
            ),
            ("save", "等待保存", {"save_project"}),
            ("verify", "验证结果", {"capture_canvas"}),
        ]
        result: list[dict[str, str]] = []
        started = any(item["status"] != "planned" for item in actions)
        for key, label, action_types in groups:
            relevant = [item for item in actions if item["action_type"] in action_types]
            if key == "policy":
                if any(item["status"] == "blocked_by_policy" for item in actions):
                    status = "blocked"
                else:
                    status = "completed" if started else "waiting"
            else:
                status = self._step_status(relevant)
            result.append({"key": key, "label": label, "status": status})
        return result

    def _current_action(
        self,
        figure_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        pending = next(
            (
                item
                for item in actions
                if item["status"] not in {"verified", "succeeded", "skipped"}
            ),
            None,
        )
        if pending is None:
            return None
        planned = {
            action.id: action
            for action in self.database.list_actions(figure_id)
        }.get(str(pending["id"]))
        return {
            "action_type": pending["action_type"],
            "status": pending["status"],
            "element": (
                planned.arguments.get("logical_element_id")
                if planned is not None
                else None
            ),
        }

    @staticmethod
    def _step_status(actions: list[dict[str, Any]]) -> str:
        if not actions:
            return "waiting"
        statuses = {str(item["status"]) for item in actions}
        if "blocked_by_policy" in statuses:
            return "blocked"
        if "unknown" in statuses:
            return "needs_review"
        if "failed" in statuses:
            return "failed"
        if statuses & {"executing", "running", "executed_unverified"}:
            return "running"
        if statuses <= {"verified", "succeeded"}:
            return "completed"
        return "waiting"

    def _save_status(self, figure_id: str) -> str:
        item = self.database.get_editor_element(figure_id, "document_save")
        if item is None:
            return "尚未观察到保存完成"
        return str((item.get("payload") or {}).get("save_status") or "已观察到保存状态")

    def _safe_evidence_path(self, path: Path) -> Path | None:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return None
        roots = (
            settings.screenshot_dir,
            settings.calibration_dir,
            settings.probe_dir,
            settings.live_figure_dir,
        )
        if resolved.suffix.casefold() not in IMAGE_SUFFIXES:
            return None
        for root in roots:
            try:
                resolved.relative_to(root.resolve(strict=False))
                return resolved
            except ValueError:
                continue
        return None

    @staticmethod
    def _element_message(
        requirement: dict[str, Any],
        observed: dict[str, Any] | None,
    ) -> str:
        status = str(requirement["status"])
        if status == "verified" and observed is not None:
            return "已通过观察证据确认。"
        if status == "unknown":
            return "无法可靠识别，请检查截图。"
        if status == "blocked_by_policy":
            return "安全策略已阻止该操作。"
        if status == "failed":
            return "操作失败，请查看运行详情。"
        return "等待执行或验证。"
