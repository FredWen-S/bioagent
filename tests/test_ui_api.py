from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.ui_routes import create_ui_router
from app.schemas.figure_spec import FigureStatus
from app.schemas.ui import UiTaskInput
from app.services.figure_execution_service import (
    FigureExecutionService,
    ManagedJob,
    UiServiceError,
)
from app.storage.database import FigureDatabase


def _preset_task() -> dict[str, object]:
    return {"mode": "preset", "preset_id": "pd1", "custom": None}


def _custom_task() -> dict[str, object]:
    return {
        "mode": "custom",
        "preset_id": None,
        "custom": {
            "title": "Protein interaction",
            "research_topic": "Protein A activates Protein B",
            "notes": None,
            "layout": "auto",
            "assets": [
                {
                    "id": "protein_a",
                    "display_name": "Protein A",
                    "search_term": "protein",
                    "fallback_terms": ["molecule"],
                    "label_text": "Protein A",
                },
                {
                    "id": "protein_b",
                    "display_name": "Protein B",
                    "search_term": "receptor",
                    "fallback_terms": [],
                    "label_text": "Protein B",
                },
            ],
            "relations": [
                {"source_id": "protein_a", "target_id": "protein_b", "type": "arrow"}
            ],
        },
    }


def _parsed_plan(client: TestClient, task: dict[str, object] | None = None) -> dict[str, object]:
    response = client.post("/api/ui/plans", json={"task": task or _preset_task()})
    assert response.status_code == 200
    return response.json()


def _dry_run(client: TestClient, task: dict[str, object] | None = None) -> dict[str, object]:
    selected_task = task or _preset_task()
    plan = _parsed_plan(client, selected_task)
    response = client.post(
        "/api/ui/dry-run",
        json={"plan_id": plan["run_id"], "task": selected_task},
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def ui_service(tmp_path: Path) -> FigureExecutionService:
    service = FigureExecutionService(FigureDatabase(tmp_path / "ui.sqlite3"))
    service._login_verified = True
    service._verified_canvas = {
        "editor_url": "https://app.biorender.com/figure/disposable",
        "redacted_url": "https://app.biorender.com/.../<redacted>",
        "title": "Disposable Figure",
        "checked_at": "2026-07-20T00:00:00+00:00",
    }
    return service


@pytest.fixture
def client(ui_service: FigureExecutionService) -> TestClient:
    app = FastAPI()
    app.include_router(create_ui_router(ui_service))

    @app.exception_handler(UiServiceError)
    async def handle_service_error(_request, error: UiServiceError):  # type: ignore[no-untyped-def]
        status = 404 if error.error_code.endswith("NOT_FOUND") else 409
        if error.error_code == "EVIDENCE_ACCESS_DENIED":
            status = 403
        return JSONResponse(
            status_code=status,
            content={
                "error_code": error.error_code,
                "message": str(error),
                "details": error.details,
            },
        )

    return TestClient(app)


def test_status_and_presets_are_user_safe(client: TestClient) -> None:
    status = client.get("/api/ui/status")
    assert status.status_code == 200
    assert status.json()["ai_generate"] == "disabled_by_policy"
    assert status.json()["real_biorender_acceptance"] == "pending_manual_acceptance"

    presets = client.get("/api/ui/presets")
    assert presets.status_code == 200
    assert presets.json()["items"][0]["id"] == "pd1"
    assert presets.json()["custom_layouts"] == [
        {
            "id": "auto",
            "name": "自动布局",
            "description": "当前自定义模式使用现有线性布局能力。",
        }
    ]


def test_login_is_required_before_canvas_step(tmp_path: Path) -> None:
    service = FigureExecutionService(FigureDatabase(tmp_path / "login-gate.sqlite3"))
    workflow = service.workflow_state()
    assert workflow["state"] == "login_required"
    assert workflow["step"] == 1
    assert workflow["buttons"]["check_canvas"] is False
    with pytest.raises(UiServiceError) as raised:
        service.start_canvas_check(
            "https://app.biorender.com/figure/disposable",
            confirmed_blank=True,
        )
    assert raised.value.error_code == "LOGIN_REQUIRED"


def test_canvas_must_be_verified_before_prompt_step(tmp_path: Path) -> None:
    service = FigureExecutionService(FigureDatabase(tmp_path / "canvas-gate.sqlite3"))
    service._login_verified = True
    assert service.workflow_state()["state"] == "canvas_required"
    with pytest.raises(UiServiceError) as raised:
        service.inspect_plan(UiTaskInput.model_validate(_preset_task()))
    assert raised.value.error_code == "CANVAS_NOT_VERIFIED"


def test_prompt_must_be_parsed_before_dry_run(client: TestClient) -> None:
    response = client.post(
        "/api/ui/dry-run",
        json={"plan_id": "figure_missing", "task": _preset_task()},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "PLAN_REQUIRED"


def test_dry_run_uses_existing_workflow(client: TestClient) -> None:
    payload = _dry_run(client)
    assert payload["status"] == "awaiting_confirmation"
    assert payload["total_actions"] > 0
    assert payload["dry_run_completed"] is True
    assert payload["dry_run_confirmed"] is False
    assert payload["can_confirm_dry_run"] is True
    assert payload["real_biorender_accepted"] is False
    workflow = client.get(
        "/api/ui/workflow-state",
        params={"dry_run_id": payload["run_id"]},
    ).json()
    assert workflow["state"] == "dry_run_confirmation_required"
    assert workflow["step"] == 3
    assert workflow["buttons"]["start_live"] is False


def test_dry_run_releases_busy_and_live_is_blocked_until_confirmation(
    client: TestClient,
) -> None:
    dry_run = _dry_run(client)
    status = client.get("/api/ui/status").json()
    assert status["active_jobs"] == []

    live = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _preset_task(),
            "dry_run_id": dry_run["run_id"],
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert live.status_code == 409
    assert live.json()["error_code"] == "DRY_RUN_CONFIRMATION_REQUIRED"


def test_confirmed_dry_run_allows_live_to_enter_next_stage(
    client: TestClient,
    ui_service: FigureExecutionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dry_run = _dry_run(client)
    confirmation = client.post(
        f"/api/ui/runs/{dry_run['run_id']}/confirm-dry-run",
        json={},
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["status"] == "completed"
    assert confirmation.json()["dry_run_confirmed"] is True

    started: list[str] = []

    def record_start(figure_id: str) -> dict[str, object]:
        started.append(figure_id)
        return {
            "id": "job_test",
            "kind": "live_figure",
            "status": "queued",
            "message": "queued",
            "figure_id": figure_id,
        }

    monkeypatch.setattr(ui_service, "start_resume", record_start)
    live = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _preset_task(),
            "dry_run_id": dry_run["run_id"],
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert live.status_code == 202
    assert started == [live.json()["figure_id"]]
    assert started[0] != dry_run["run_id"]
    events = ui_service.database.list_audit_events(figure_id=started[0])
    assert events[-1]["event_type"] == "live_started_from_confirmed_dry_run"
    assert events[-1]["payload"]["dry_run_id"] == dry_run["run_id"]
    workflow = client.get(
        "/api/ui/workflow-state",
        params={"dry_run_id": dry_run["run_id"]},
    ).json()
    assert workflow["state"] == "ready_to_execute"
    assert workflow["step"] == 4
    assert workflow["buttons"]["start_live"] is True


def test_confirmed_dry_run_cannot_authorize_a_changed_task(client: TestClient) -> None:
    dry_run = _dry_run(client)
    confirmed = client.post(
        f"/api/ui/runs/{dry_run['run_id']}/confirm-dry-run",
        json={},
    )
    assert confirmed.status_code == 200

    live = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _custom_task(),
            "dry_run_id": dry_run["run_id"],
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert live.status_code == 409
    assert live.json()["error_code"] == "DRY_RUN_TASK_MISMATCH"


def test_limited_custom_figure_can_be_planned(client: TestClient) -> None:
    response = client.post("/api/ui/plans", json={"task": _custom_task()})
    assert response.status_code == 200
    payload = response.json()
    assert payload["scientific_validation_passed"] is True
    assert payload["total_elements"] >= 5


def test_multiple_runs_use_distinct_persisted_action_ids(client: TestClient) -> None:
    first = client.post("/api/ui/plans", json={"task": _preset_task()})
    second = client.post("/api/ui/plans", json={"task": _preset_task()})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run_id"] != second.json()["run_id"]


@pytest.mark.parametrize(
    "editor_url",
    [
        "http://app.biorender.com/figure/1",
        "https://example.com/figure/1",
        "https://user:password@app.biorender.com/figure/1",
    ],
)
def test_live_run_rejects_unsafe_urls(client: TestClient, editor_url: str) -> None:
    response = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": editor_url,
            "task": _preset_task(),
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert response.status_code == 422


def test_live_run_requires_both_confirmations(client: TestClient) -> None:
    response = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _preset_task(),
            "confirmed_disposable": False,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "LIVE_CONFIRMATION_REQUIRED"


def test_frontend_cannot_enable_biorender_ai(client: TestClient) -> None:
    response = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _preset_task(),
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": True,
        },
    )
    assert response.status_code == 422


def test_custom_asset_limit_is_enforced(client: TestClient) -> None:
    task = _custom_task()
    custom = task["custom"]
    assert isinstance(custom, dict)
    custom["assets"] = [
        {
            "id": f"asset_{index}",
            "display_name": f"Asset {index}",
            "search_term": "cell",
            "fallback_terms": [],
            "label_text": None,
        }
        for index in range(1, 17)
    ]
    response = client.post("/api/ui/plans", json={"task": task})
    assert response.status_code == 422


def test_unknown_run_has_stable_error(client: TestClient) -> None:
    response = client.get("/api/ui/runs/figure_missing")
    assert response.status_code == 404
    assert response.json() == {
        "error_code": "RUN_NOT_FOUND",
        "message": "未找到绘图任务。",
        "details": None,
    }


def test_evidence_preview_rejects_paths_outside_allowlist(
    client: TestClient,
    ui_service: FigureExecutionService,
    tmp_path: Path,
) -> None:
    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not a screenshot")
    with ui_service.database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO screenshots (figure_id, action_id, path, kind, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                bundle.figure_spec.id,
                None,
                str(outside),
                "test",
                "2026-07-18T00:00:00+00:00",
            ),
        )
        evidence_id = int(cursor.lastrowid)
    response = client.get(f"/api/ui/evidence/{evidence_id}")
    assert response.status_code == 403
    assert response.json()["error_code"] == "EVIDENCE_ACCESS_DENIED"


def test_resume_does_not_start_duplicate_job(ui_service: FigureExecutionService) -> None:
    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    release = threading.Event()

    def hold_job(job_id: str) -> None:
        release.wait(timeout=2)
        job = ui_service._jobs[job_id]
        ui_service._update_job(job, "stopped", "test complete")

    ui_service._run_live_job = hold_job  # type: ignore[method-assign]
    first = ui_service.start_resume(bundle.figure_spec.id)
    assert first["figure_id"] == bundle.figure_spec.id
    with pytest.raises(UiServiceError) as raised:
        ui_service.start_resume(bundle.figure_spec.id)
    assert raised.value.error_code in {"BROWSER_BUSY", "RUN_ALREADY_ACTIVE"}
    release.set()


def test_safe_stop_releases_browser_job_and_allows_retry(
    ui_service: FigureExecutionService,
) -> None:
    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    running = threading.Event()

    def stoppable_job(job_id: str) -> None:
        job = ui_service._jobs[job_id]
        ui_service._update_job(job, "running", "test running")
        running.set()
        job.stop_event.wait(timeout=2)
        ui_service._update_job(job, "stopped", "test stopped")

    ui_service._run_live_job = stoppable_job  # type: ignore[method-assign]
    job = ui_service.start_resume(bundle.figure_spec.id)
    assert running.wait(timeout=1)
    stopped = ui_service.request_job_stop(str(job["id"]))
    assert stopped["status"] == "stop_requested"
    ui_service._threads[str(job["id"])].join(timeout=2)
    assert ui_service.get_job(str(job["id"]))["status"] == "stopped"
    ui_service._ensure_browser_available()


def test_dead_job_is_reaped_instead_of_leaving_browser_busy(
    ui_service: FigureExecutionService,
) -> None:
    ui_service._jobs["dead_job"] = ManagedJob(
        id="dead_job",
        kind="manual_login",
        status="waiting_user",
        message="waiting",
    )
    status = ui_service.system_status()
    assert status["active_jobs"] == []
    assert ui_service.get_job("dead_job")["status"] == "failed"
    assert ui_service.get_job("dead_job")["error_code"] == "STALE_JOB_RECOVERED"
    ui_service._ensure_browser_available()


def test_service_restart_recovers_residual_running_figure(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "restart.sqlite3")
    first_service = FigureExecutionService(database)
    bundle = first_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    database.set_status(bundle.figure_spec.id, FigureStatus.EXECUTING)

    restarted = FigureExecutionService(database)
    summary = restarted.run_summary(bundle.figure_spec.id)
    assert summary["status"] == "paused_reconciliation"
    assert summary["can_resume"] is True
    events = database.list_audit_events(figure_id=bundle.figure_spec.id)
    assert events[-1]["event_type"] == "service_recovered_interrupted_run"
    assert events[-1]["payload"]["previous_status"] == "executing"


def test_workflow_refresh_restores_plan_and_active_job(
    ui_service: FigureExecutionService,
) -> None:
    task = UiTaskInput.model_validate(_preset_task())
    plan = ui_service.inspect_plan(task)
    workflow = ui_service.workflow_state(plan_id=str(plan["run_id"]))
    assert workflow["state"] == "prompt_parsed"
    assert workflow["step"] == 3
    assert workflow["plan_summary"]["asset_count"] > 0

    ui_service._jobs["refresh_job"] = ManagedJob(
        id="refresh_job",
        kind="live_figure",
        status="running",
        message="running",
        figure_id=str(plan["run_id"]),
    )
    ui_service._threads["refresh_job"] = threading.current_thread()
    status = ui_service.system_status()
    assert status["active_jobs"][0]["id"] == "refresh_job"
    restored = ui_service.workflow_state(run_id=str(plan["run_id"]))
    assert restored["state"] == "executing"
    assert restored["step"] == 4


def test_unknown_result_is_completed_with_manual_review(
    ui_service: FigureExecutionService,
) -> None:
    task = UiTaskInput.model_validate(_preset_task())
    bundle = ui_service.plan_task(task)
    with ui_service.database.connect() as connection:
        connection.execute(
            "UPDATE element_requirements SET status = 'unknown' WHERE figure_id = ?",
            (bundle.figure_spec.id,),
        )
    ui_service.database.set_status(bundle.figure_spec.id, FigureStatus.COMPLETED)
    workflow = ui_service.workflow_state(run_id=bundle.figure_spec.id)
    assert workflow["state"] == "completed_with_unknown"
    assert workflow["step"] == 5


def test_starting_new_workflow_does_not_reuse_old_run(
    ui_service: FigureExecutionService,
) -> None:
    old = ui_service.inspect_plan(UiTaskInput.model_validate(_preset_task()))
    ui_service.reset_workflow()
    workflow = ui_service.workflow_state()
    assert workflow["state"] == "canvas_required"
    assert workflow["step"] == 2
    assert str(old["run_id"]) not in str(workflow)
