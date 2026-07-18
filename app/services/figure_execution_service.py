from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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
from app.schemas.ui import CustomFigureInput, UiTaskInput
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine

PD1_REQUEST_PATH = Path(__file__).resolve().parents[2] / "examples" / "pd1_request.txt"

FINAL_JOB_STATES = frozenset({"completed", "failed", "blocked", "stopped"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class UiServiceError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details


@dataclass(slots=True)
class ManagedJob:
    id: str
    kind: str
    status: str
    message: str
    figure_id: str | None = None
    error_code: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    result: dict[str, Any] | None = None
    input_data: dict[str, Any] = field(default_factory=dict, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "figure_id": self.figure_id,
            "error_code": self.error_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
        assert task.custom is not None
        requirement, spec = self._custom_spec(task.custom)
        return self.engine.plan_spec(requirement, spec, editor_url=editor_url)

    def execute_dry_run(self, figure_id: str) -> FigureStatus:
        return self.engine.execute(figure_id, DryRunOperator())

    def plan_and_execute_dry_run(self, task: UiTaskInput) -> dict[str, Any]:
        bundle = self.plan_task(task)
        status = self.execute_dry_run(bundle.figure_spec.id)
        return self.run_summary(bundle.figure_spec.id, status_override=status)

    def execute_live_sync(self, figure_id: str) -> FigureStatus:
        return self.engine.execute(figure_id, self._new_live_operator())

    def start_live(self, task: UiTaskInput, editor_url: str) -> dict[str, Any]:
        self._ensure_browser_available()
        bundle = self.plan_task(task, editor_url=editor_url)
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
                target=self._run_live_job,
                args=(job.id,),
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
            job.stop_event.set()
            job.status = "stop_requested"
            job.message = "已请求安全停止，将在当前 GUI 动作结束后暂停。"
            job.updated_at = datetime.now(UTC).isoformat()
            return job.public()

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

    def start_manual_login(self) -> dict[str, Any]:
        self._ensure_browser_available()
        self._login_complete.clear()
        job = ManagedJob(
            id=f"login_job_{uuid.uuid4().hex[:12]}",
            kind="manual_login",
            status="queued",
            message="正在打开人工登录窗口。",
        )
        self._start_thread(job, self._run_login_job)
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
            "browser_login": "waiting_user" if login_job else "not_verified",
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
            "can_resume": status
            in {
                FigureStatus.PAUSED_AUTHENTICATION.value,
                FigureStatus.PAUSED_APPROVAL.value,
                FigureStatus.PAUSED_RECONCILIATION.value,
                FigureStatus.FAILED.value,
            },
            "can_stop": self._active_job_for_figure(figure_id) is not None,
            "real_biorender_accepted": False,
        }

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
            self._update_job(
                job,
                "failed",
                "界面校准失败，请查看校准证据。",
                error_code=getattr(error, "error_type", type(error).__name__),
            )
        finally:
            operator.close()

    def _run_login_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        self._update_job(job, "running", "正在打开人工登录窗口。")
        operator = self._new_live_operator()
        try:
            operator.page.goto(
                "https://app.biorender.com/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            self._update_job(
                job,
                "waiting_user",
                "请在浏览器中手动登录，完成后返回本页面确认。",
            )
            self._login_complete.wait()
            self._update_job(job, "completed", "登录窗口已关闭，会话已保存在本机。")
        except Exception as error:
            self._update_job(
                job,
                "failed",
                "无法打开人工登录窗口。",
                error_code=type(error).__name__,
            )
        finally:
            operator.close()

    def _start_thread(
        self,
        job: ManagedJob,
        target: Callable[[str], None],
    ) -> None:
        with self._lock:
            self._jobs[job.id] = job
            thread = threading.Thread(
                target=target,
                args=(job.id,),
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
        result: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            job.status = status
            job.message = message
            job.error_code = error_code
            job.updated_at = datetime.now(UTC).isoformat()
            if result is not None:
                job.result = result

    def _ensure_browser_available(self) -> None:
        with self._lock:
            active = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.kind in {"live_figure", "calibration", "manual_login"}
                    and job.status not in FINAL_JOB_STATES
                ),
                None,
            )
            if active is not None:
                raise UiServiceError(
                    "BROWSER_BUSY",
                    "浏览器正在执行其他任务，请等待或安全停止后再试。",
                )

    def _active_job_for_figure(self, figure_id: str) -> ManagedJob | None:
        with self._lock:
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
            ("plan", "检查任务", set()),
            ("calibrate", "校准 BioRender 界面", {"open_biorender_editor"}),
            ("policy", "检查安全策略", set()),
            ("search", "搜索素材", {"search_asset", "select_asset_candidate"}),
            (
                "place",
                "放置素材",
                {"drag_selected_asset", "move_element", "resize_element", "rotate_element"},
            ),
            ("labels", "添加标签", {"add_text", "edit_text"}),
            ("connect", "添加连接关系", {"connect_elements"}),
            (
                "layout",
                "调整布局",
                {"group_elements", "align_elements", "distribute_elements"},
            ),
            ("verify", "验证结果", {"capture_canvas"}),
            ("confirm", "等待用户确认", {"save_project"}),
        ]
        result: list[dict[str, str]] = []
        started = any(item["status"] != "planned" for item in actions)
        for key, label, action_types in groups:
            relevant = [item for item in actions if item["action_type"] in action_types]
            if key == "plan":
                status = "completed"
            elif key == "policy":
                if any(item["status"] == "blocked_by_policy" for item in actions):
                    status = "blocked"
                else:
                    status = "completed" if started else "waiting"
            elif key == "confirm" and figure_status == "awaiting_confirmation":
                status = "needs_review"
            else:
                status = self._step_status(relevant)
            result.append({"key": key, "label": label, "status": status})
        return result

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
