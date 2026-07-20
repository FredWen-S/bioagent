from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.schemas.ui import (
    UiCalibrationRequest,
    UiCanvasCheckRequest,
    UiDryRunRequest,
    UiEditorUrlRequest,
    UiLiveRunRequest,
    UiLoginRequest,
    UiPlanRequest,
    UiResumeRequest,
)
from app.services.figure_execution_service import FigureExecutionService, UiServiceError


def _require_live_confirmation(confirmed_disposable: bool, confirm_live: bool) -> None:
    if not confirmed_disposable or not confirm_live:
        raise UiServiceError(
            "LIVE_CONFIRMATION_REQUIRED",
            "开始真实操作前，需要确认使用可丢弃的空白 Figure。",
        )


def create_ui_router(service: FigureExecutionService) -> APIRouter:
    router = APIRouter(prefix="/api/ui", tags=["graphical-ui"])

    @router.get("/status")
    def ui_status() -> dict[str, object]:
        return service.system_status()

    @router.get("/workflow-state")
    def workflow_state(
        plan_id: str | None = None,
        dry_run_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, object]:
        return service.workflow_state(
            plan_id=plan_id,
            dry_run_id=dry_run_id,
            run_id=run_id,
        )

    @router.post("/workflow/reset")
    def reset_workflow() -> dict[str, object]:
        return service.reset_workflow()

    @router.get("/presets")
    def ui_presets() -> dict[str, object]:
        return {
            "items": [
                {
                    "id": "pd1",
                    "name": "PD-1 / PD-L1 机制图",
                    "description": "用于验证完整的素材、标签、连接器和布局流程。",
                    "online_acceptance": "pending_manual_acceptance",
                }
            ],
            "custom_layouts": [
                {
                    "id": "auto",
                    "name": "自动布局",
                    "description": "当前自定义模式使用现有线性布局能力。",
                }
            ],
        }

    @router.post("/check-url")
    def check_editor_url(payload: UiEditorUrlRequest) -> dict[str, object]:
        return {
            "valid": True,
            "message": "URL 格式有效；是否可编辑将在校准时确认。",
            "redacted_url": service.redact_url(payload.editor_url),
        }

    @router.post("/canvas/check", status_code=202)
    def check_canvas(payload: UiCanvasCheckRequest) -> dict[str, object]:
        return service.start_canvas_check(
            payload.editor_url,
            confirmed_blank=payload.confirmed_blank,
        )

    @router.post("/plans")
    def inspect_plan(payload: UiPlanRequest) -> dict[str, object]:
        return service.inspect_plan(payload.task)

    @router.post("/dry-run")
    def start_dry_run(payload: UiDryRunRequest) -> dict[str, object]:
        return service.execute_planned_dry_run(payload.plan_id, payload.task)

    @router.post("/runs/{run_id}/confirm-dry-run")
    def confirm_dry_run(run_id: str) -> dict[str, object]:
        return service.confirm_dry_run(run_id)

    @router.post("/calibrate", status_code=202)
    def start_calibration(payload: UiCalibrationRequest) -> dict[str, object]:
        _require_live_confirmation(payload.confirmed_disposable, payload.confirm_live)
        return service.start_calibration(payload.editor_url)

    @router.post("/login/open", status_code=202)
    def open_login(payload: UiLoginRequest) -> dict[str, object]:
        if not payload.confirm_manual_login:
            raise UiServiceError(
                "MANUAL_LOGIN_CONFIRMATION_REQUIRED",
                "请确认将由您本人在浏览器中完成登录。",
            )
        return service.start_manual_login()

    @router.post("/login/complete")
    def complete_login() -> dict[str, object]:
        return service.complete_manual_login()

    @router.post("/live-runs", status_code=202)
    def start_live_run(payload: UiLiveRunRequest) -> dict[str, object]:
        _require_live_confirmation(payload.confirmed_disposable, payload.confirm_live)
        return service.start_live(
            payload.task,
            payload.editor_url,
            plan_id=payload.plan_id,
            dry_run_id=payload.dry_run_id,
        )

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        return service.get_job(job_id)

    @router.post("/jobs/{job_id}/stop", status_code=202)
    def stop_job(job_id: str) -> dict[str, object]:
        return service.request_job_stop(job_id)

    @router.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        return service.run_summary(run_id)

    @router.get("/runs/{run_id}/elements")
    def get_elements(run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "items": service.element_summary(run_id)}

    @router.get("/runs/{run_id}/evidence")
    def get_evidence(run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "items": service.evidence_summary(run_id)}

    @router.post("/runs/{run_id}/resume", status_code=202)
    def resume_run(run_id: str, payload: UiResumeRequest) -> dict[str, object]:
        _require_live_confirmation(payload.confirmed_disposable, payload.confirm_live)
        return service.start_resume(run_id)

    @router.post("/runs/{run_id}/verify")
    def verify_run(run_id: str) -> dict[str, object]:
        return service.verify_run(run_id)

    @router.post("/runs/{run_id}/stop", status_code=202)
    def stop_run(run_id: str) -> dict[str, object]:
        return service.request_safe_stop(run_id)

    @router.get("/evidence/{evidence_id}", response_class=FileResponse)
    def preview_evidence(evidence_id: str) -> FileResponse:
        return FileResponse(
            service.evidence_path(evidence_id),
            headers={"Cache-Control": "no-store"},
        )

    return router
