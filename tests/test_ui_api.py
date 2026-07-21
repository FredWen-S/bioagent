from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.ui_routes import create_ui_router
from app.operator.errors import CalibrationFailed
from app.schemas.figure_spec import FigureStatus
from app.schemas.ui import UiTaskInput
from app.services.figure_execution_service import FigureExecutionService, UiServiceError
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


@pytest.fixture
def ui_service(tmp_path: Path) -> FigureExecutionService:
    return FigureExecutionService(FigureDatabase(tmp_path / "ui.sqlite3"))


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


def test_dry_run_requires_an_existing_plan(client: TestClient) -> None:
    response = client.post("/api/ui/dry-run", json={"task": _preset_task()})
    assert response.status_code == 422


def test_plan_endpoint_enforces_login_and_canvas_gate(client: TestClient) -> None:
    response = client.post("/api/ui/plans", json={"task": _custom_task()})
    assert response.status_code == 409
    assert response.json()["error_code"] == "LOGIN_REQUIRED"


def test_multiple_plans_use_distinct_persisted_action_ids(
    ui_service: FigureExecutionService,
) -> None:
    task = UiTaskInput.model_validate(_preset_task())
    first = ui_service.plan_task(task)
    second = ui_service.plan_task(task)

    assert first.figure_spec.id != second.figure_spec.id


def test_run_summary_exposes_wizard_lineage_and_resume_fields(
    ui_service: FigureExecutionService,
) -> None:
    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    payload = ui_service.run_summary(bundle.figure_spec.id)

    assert {
        "source_dry_run_id",
        "source_plan_id",
        "dry_run_gate",
        "prepare_failure",
        "resume_blocked_reason",
        "task_fingerprint",
    } <= payload.keys()


def test_calibration_failure_is_not_resumable(
    ui_service: FigureExecutionService,
) -> None:
    class CalibrationFailureOperator:
        def execute(self, _action, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise CalibrationFailed(
                "missing required UI anchor: asset_panel",
                profile_path="runtime/calibration/profile.json",
                missing_anchors=["asset_panel"],
            )

        def close(self) -> None:
            return None

    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    status = ui_service.engine.execute(
        bundle.figure_spec.id,
        CalibrationFailureOperator(),
    )
    payload = ui_service.run_summary(bundle.figure_spec.id)

    assert status == FigureStatus.FAILED
    assert payload["can_resume"] is False
    assert payload["resume_blocked_reason"]
    assert payload["prepare_failure"]["subcode"] == "ui_calibration_failed"
    assert payload["prepare_failure"]["missing_anchors"] == ["asset_panel"]


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
            "dry_run_id": "figure_confirmed_dry_run",
            "confirmed_disposable": False,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "LIVE_CONFIRMATION_REQUIRED"


def test_live_run_rejects_plan_only_without_dry_run_id(client: TestClient) -> None:
    response = client.post(
        "/api/ui/live-runs",
        json={
            "editor_url": "https://app.biorender.com/figure/disposable",
            "task": _preset_task(),
            "plan_id": "figure_parsed_plan",
            "confirmed_disposable": True,
            "confirm_live": True,
            "enable_biorender_ai": False,
        },
    )

    assert 400 <= response.status_code < 500
    assert response.status_code == 422


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
