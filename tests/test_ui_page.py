from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import app
from app.services.figure_execution_service import FigureExecutionService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ui_page_loads_with_required_safety_language() -> None:
    response = TestClient(app).get("/ui")
    assert response.status_code == 200
    assert "BioRender 科研绘图助手" in response.text
    assert "BioRender AI：已禁用" in response.text
    assert "安全预演" in response.text
    assert "真实执行" in response.text
    assert "真实 BioRender 线上验收尚未完成" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_ui_page_is_five_step_wizard_and_exposes_runtime_version() -> None:
    client = TestClient(app)
    page = client.get("/ui")
    version = client.get("/api/version")

    assert page.status_code == 200
    assert page.text.count('data-step-panel="') == 5
    assert "当前步骤 1 / 5" in page.text
    assert "运行版本" in page.text
    assert version.status_code == 200
    assert set(("git_commit", "git_branch", "build_time", "static_files")) <= set(
        version.json()
    )


def test_live_execution_is_disabled_in_initial_markup() -> None:
    html = (PROJECT_ROOT / "app" / "static" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="start-live"' in html
    assert 'id="start-live" class="button button-danger" type="button" disabled' in html
    assert 'id="run-dry-run"' in html
    assert 'id="confirm-dry-run"' in html
    assert "我已确认使用可丢弃的空白 Figure" in html


def test_unknown_state_is_not_mapped_to_success() -> None:
    assert FigureExecutionService.friendly_status("unknown") == "需要人工检查"
    assert FigureExecutionService.friendly_status("unknown") != "已确认"


def test_ui_does_not_copy_operator_or_shell_out_to_cli() -> None:
    routes = (PROJECT_ROOT / "app" / "api" / "ui_routes.py").read_text(encoding="utf-8")
    service = (
        PROJECT_ROOT / "app" / "services" / "figure_execution_service.py"
    ).read_text(encoding="utf-8")
    cli = (PROJECT_ROOT / "app" / "cli.py").read_text(encoding="utf-8")
    javascript = (PROJECT_ROOT / "app" / "static" / "ui" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "subprocess" not in routes
    assert "shell=True" not in routes
    assert "LivePlaywrightOperator" not in routes
    assert "WorkflowEngine" in service
    assert "FigureExecutionService" in cli
    assert "innerHTML" not in javascript
