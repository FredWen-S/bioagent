from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.ui_routes import create_ui_router
from app.operator.errors import CalibrationFailed, SearchActionFailed
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, GuiActionResult
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


def _prepare_guided_dry_run(
    service: FigureExecutionService,
) -> tuple[UiTaskInput, dict[str, object], dict[str, object]]:
    editor_url = "https://app.biorender.com/figure/disposable-test"
    service._login_verified = True
    service._verified_canvas = {
        "editor_url": editor_url,
        "title": "Disposable test figure",
        "figure_identifier": "disposable-test",
    }
    task = UiTaskInput.model_validate(_preset_task())
    plan = service.inspect_plan(task)
    dry_run = service.execute_planned_dry_run(str(plan["run_id"]), task)
    return task, plan, dry_run


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


def test_completed_dry_run_has_stable_confirmable_response_and_summary(
    ui_service: FigureExecutionService,
) -> None:
    _task, plan, dry_run = _prepare_guided_dry_run(ui_service)

    assert dry_run["dry_run_id"] == plan["run_id"]
    assert dry_run["status"] == "awaiting_confirmation"
    assert dry_run["dry_run_completed"] is True
    assert dry_run["dry_run_failed"] is False
    assert dry_run["dry_run_confirmed"] is False
    assert dry_run["can_confirm_dry_run"] is True
    assert dry_run["task_fingerprint"] == plan["task_fingerprint"]
    assert dry_run["plan_fingerprint"] == plan["plan_fingerprint"]
    assert dry_run["source_plan_id"] == plan["run_id"]
    summary = dry_run["summary"]
    assert summary["target_canvas"]["confirmed_test_canvas"] is True
    assert summary["task"]["figure_title"]
    assert summary["task"]["total_action_count"] > 0
    assert summary["planned_actions"]["search_assets"]
    assert summary["planned_actions"]["insert_assets"]
    assert summary["planned_actions"]["add_labels"]
    assert summary["planned_actions"]["add_connections"]
    assert summary["safety_limits"] == {
        "biorender_ai_generate": "forbidden",
        "export": "forbidden",
        "share": "forbidden",
        "purchase": "forbidden",
        "other_figures": "not_modified",
        "real_biorender_modified": False,
    }
    assert summary["result"]["policy_check_passed"] is True
    assert summary["evidence"]["audit_event"]["event_type"] == "dry_run_completed"
    assert "不产生真实画布截图" in summary["evidence"]["screenshot_note"]
    assert summary["dry_run_actions"]
    assert {item["status"] for item in summary["dry_run_actions"]} == {"simulated"}
    assert {item["policy_status"] for item in summary["dry_run_actions"]} == {
        "policy_allowed"
    }
    assert {item["live_execution_status"] for item in summary["dry_run_actions"]} == {
        "planned"
    }


def test_workflow_recovers_dry_run_from_plan_id_without_local_dry_run_id(
    ui_service: FigureExecutionService,
) -> None:
    _task, plan, dry_run = _prepare_guided_dry_run(ui_service)

    workflow = ui_service.workflow_state(plan_id=str(plan["run_id"]))

    assert workflow["state"] == "dry_run_confirmation_required"
    assert workflow["dry_run_id"] == dry_run["dry_run_id"]
    assert workflow["dry_run_completed"] is True
    assert workflow["can_confirm_dry_run"] is True
    assert workflow["buttons"]["confirm_dry_run"] is True
    assert workflow["dry_run_summary"]["task"]["total_action_count"] > 0


def test_failed_dry_run_is_not_recorded_as_completed(
    ui_service: FigureExecutionService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor_url = "https://app.biorender.com/figure/disposable-test"
    ui_service._login_verified = True
    ui_service._verified_canvas = {"editor_url": editor_url, "title": "Test"}
    task = UiTaskInput.model_validate(_preset_task())
    plan = ui_service.inspect_plan(task)
    monkeypatch.setattr(
        ui_service,
        "execute_dry_run",
        lambda _figure_id: FigureStatus.FAILED,
    )

    dry_run = ui_service.execute_planned_dry_run(str(plan["run_id"]), task)
    workflow = ui_service.workflow_state(plan_id=str(plan["run_id"]))

    assert dry_run["status"] == "failed"
    assert dry_run["dry_run_completed"] is False
    assert dry_run["dry_run_failed"] is True
    assert dry_run["can_confirm_dry_run"] is False
    assert workflow["state"] == "dry_run_failed"
    assert workflow["buttons"]["confirm_dry_run"] is False
    event_types = {
        event["event_type"]
        for event in ui_service.database.list_audit_events(
            figure_id=str(plan["run_id"])
        )
    }
    assert "dry_run_failed" in event_types
    assert "dry_run_completed" not in event_types


def test_dry_run_confirmation_rejects_fingerprint_mismatch(
    client: TestClient,
    ui_service: FigureExecutionService,
) -> None:
    _task, plan, dry_run = _prepare_guided_dry_run(ui_service)

    response = client.post(
        f"/api/ui/runs/{dry_run['dry_run_id']}/confirm-dry-run",
        json={
            "task_fingerprint": "0" * 64,
            "plan_fingerprint": plan["plan_fingerprint"],
            "source_plan_id": plan["run_id"],
            "editor_url": "https://app.biorender.com/figure/disposable-test",
        },
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "DRY_RUN_FINGERPRINT_MISMATCH"


def test_confirmed_dry_run_unlocks_live_and_survives_service_refresh(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persistent-confirmation.sqlite3"
    service = FigureExecutionService(FigureDatabase(database_path))
    task, plan, dry_run = _prepare_guided_dry_run(service)
    confirmed = service.confirm_dry_run(
        str(dry_run["dry_run_id"]),
        task_fingerprint=str(plan["task_fingerprint"]),
        plan_fingerprint=str(plan["plan_fingerprint"]),
        source_plan_id=str(plan["run_id"]),
        editor_url="https://app.biorender.com/figure/disposable-test",
    )
    assert confirmed["dry_run_confirmed"] is True

    refreshed = FigureExecutionService(FigureDatabase(database_path))
    refreshed._login_verified = True
    refreshed._verified_canvas = {
        "editor_url": "https://app.biorender.com/figure/disposable-test",
        "title": "Disposable test figure",
    }
    workflow = refreshed.workflow_state(plan_id=str(plan["run_id"]))

    assert workflow["state"] == "ready_to_execute"
    assert workflow["dry_run_confirmed"] is True
    assert workflow["buttons"]["start_live"] is True
    metadata = refreshed._dry_run_metadata(str(dry_run["dry_run_id"]))
    assert metadata["confirmed_test_canvas"] is True
    assert metadata["real_biorender_accepted"] is True


def test_unresolved_connection_blocks_live_and_requires_manual_review(
    ui_service: FigureExecutionService,
) -> None:
    _task, _plan, dry_run = _prepare_guided_dry_run(ui_service)
    original = ui_service.database.list_actions(str(dry_run["dry_run_id"]))
    changed = []
    connection_changed = False
    for action in original:
        if action.action.value == "connect_elements" and not connection_changed:
            changed.append(
                action.model_copy(
                    update={
                        "arguments": {
                            **action.arguments,
                            "source_entity_id": None,
                            "target_entity_id": None,
                            "source_id": None,
                            "target_id": None,
                            "source_element_id": None,
                            "target_element_id": None,
                        }
                    }
                )
            )
            connection_changed = True
        else:
            changed.append(action)
    ui_service.database.list_actions = lambda _figure_id: changed  # type: ignore[method-assign]

    summary = ui_service._dry_run_summary(str(dry_run["dry_run_id"]))

    assert "? → ?" in summary["result"]["unresolved_connections"]
    assert "连接端点未解析：? → ?" in summary["result"]["manual_review_items"]
    assert summary["result"]["can_enter_live_run"] is False


def test_explicit_live_run_state_takes_precedence_over_source_dry_run(
    ui_service: FigureExecutionService,
) -> None:
    task, plan, dry_run = _prepare_guided_dry_run(ui_service)
    ui_service.confirm_dry_run(
        str(dry_run["dry_run_id"]),
        task_fingerprint=str(plan["task_fingerprint"]),
        plan_fingerprint=str(plan["plan_fingerprint"]),
        source_plan_id=str(plan["run_id"]),
        editor_url="https://app.biorender.com/figure/disposable-test",
    )
    live = ui_service.plan_task(task)
    ui_service.database.add_audit_event(
        "live_started_from_confirmed_dry_run",
        {"dry_run_id": dry_run["dry_run_id"], "plan_id": plan["run_id"]},
        figure_id=live.figure_spec.id,
    )

    workflow = ui_service.workflow_state(
        plan_id=str(plan["run_id"]),
        dry_run_id=str(dry_run["dry_run_id"]),
        run_id=live.figure_spec.id,
    )

    assert workflow["active_run_id"] == live.figure_spec.id
    assert workflow["current_workflow_state"] != "ready_to_execute"
    assert workflow["buttons"]["start_live"] is False


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


def test_search_preparation_failure_is_not_resumable(
    ui_service: FigureExecutionService,
) -> None:
    class SearchFailureOperator:
        def execute(self, action, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            if action.action.value != "search_asset":
                return GuiActionResult(
                    action_id=action.id,
                    status=ActionStatus.VERIFIED,
                    attempt=1,
                    message="prepared",
                )
            raise SearchActionFailed(
                "search box missing",
                subcode="search_ui_not_found",
                diagnostics={"last_operation": "wait_for_search_input"},
                retryable=False,
            )

        def close(self) -> None:
            return None

    bundle = ui_service.plan_task(UiTaskInput.model_validate(_preset_task()))
    status = ui_service.engine.execute(bundle.figure_spec.id, SearchFailureOperator())
    payload = ui_service.run_summary(bundle.figure_spec.id)

    assert status == FigureStatus.FAILED
    assert payload["failure_subcode"] == "search_ui_not_found"
    assert payload["can_resume"] is False
    assert "搜索能力准备失败" in payload["resume_blocked_reason"]


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
