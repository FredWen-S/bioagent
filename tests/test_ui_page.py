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
    assert "系统解析后的任务摘要" in response.text
    assert "真实执行" in response.text
    assert "真实 BioRender 线上验收尚未完成" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_live_execution_is_disabled_in_initial_markup() -> None:
    html = (PROJECT_ROOT / "app" / "static" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="start-live"' in html
    assert 'id="start-live" class="button button-danger" type="button" disabled' in html
    assert "我已确认使用可丢弃的空白 Figure" in html
    assert "查看任务摘要后即可进入执行步骤" in html


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
    assert "plan_id" in javascript
    assert "/confirm-dry-run" not in javascript
    assert "/jobs/${encodeURIComponent(state.currentJobId)}/stop" in javascript


def test_wizard_has_five_steps_and_backend_driven_state_copy() -> None:
    html = (PROJECT_ROOT / "app" / "static" / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    javascript = (PROJECT_ROOT / "app" / "static" / "ui" / "app.js").read_text(
        encoding="utf-8"
    )
    assert html.count("data-step-panel=") == 5
    assert html.count("data-step-indicator=") == 5
    assert "当前步骤" in html
    assert "下一步：" in javascript
    assert "/api/ui/workflow-state" in javascript
    assert "localStorage" in javascript
    assert "completed_with_unknown" in javascript
    assert "diagnostic_hint" in javascript
    assert 'byId("login-message").textContent = message' in javascript
    assert "next_block_reason" in javascript
    assert "需求解析通过" in javascript
    assert "查看任务摘要后即可进入执行步骤" in javascript
    assert "解析需求" in html and "查看任务摘要" in html and "进入执行" in html
    assert "运行安全预演" not in html
    assert "确认预演结果" not in html
    assert 'id="next-step-hint"' in html
