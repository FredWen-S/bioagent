from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "app" / "static" / "ui"


def test_prompt_parsed_step_four_survives_polling_reload_and_valid_rollbacks() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    workflow = {
        "state": "prompt_parsed",
        "step": 3,
        "reason": "需求已解析，可以进入执行步骤。",
        "next_action": "进入执行步骤",
        "next_block_reason": "",
        "buttons": {"parse_prompt": True, "run_dry_run": True},
        "plan_summary": {
            "asset_count": 2,
            "label_count": 2,
            "relation_count": 1,
            "layout_description": "线性布局",
            "risks": [],
            "supported": True,
        },
    }
    workflow_requests = 0

    def route_request(route: Route) -> None:
        nonlocal workflow_requests
        path = urlparse(route.request.url).path
        if path == "/ui":
            route.fulfill(status=200, content_type="text/html", body=html)
        elif path == "/ui-assets/app.js":
            route.fulfill(status=200, content_type="application/javascript", body=javascript)
        elif path == "/ui-assets/styles.css":
            route.fulfill(status=200, content_type="text/css", body=stylesheet)
        elif path == "/api/ui/status":
            route.fulfill(
                json={
                    "backend": "normal",
                    "database": "normal",
                    "browser_login": "verified",
                    "verified_canvas": {
                        "figure_identifier": "redacted",
                        "title": "BioRender Figure",
                    },
                    "active_jobs": [],
                }
            )
        elif path == "/api/version":
            route.fulfill(
                json={
                    "git_commit": "test",
                    "git_branch": "test",
                    "build_time": "test",
                    "static_root": "redacted",
                }
            )
        elif path == "/api/ui/workflow-state":
            workflow_requests += 1
            route.fulfill(json=workflow)
        else:
            route.fulfill(status=404, json={"error_code": "NOT_FOUND"})

    saved = {
        "step": 3,
        "taskMode": "prompt",
        "prompt": "T cell activates Tumor cell",
        "canvasUrl": "https://app.biorender.com/illustrations/redacted",
        "blankCanvasConfirmed": True,
        "canvasVerified": True,
        "planId": "figure_plan_1",
        "planFingerprint": "fingerprint-1",
        "dryRunId": None,
        "dryRunFingerprint": None,
        "runId": None,
        "currentJobId": None,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            context.add_init_script(
                "if (!localStorage.getItem('biorender-guided-workflow-v1')) "
                "localStorage.setItem('biorender-guided-workflow-v1', "
                f"{json.dumps(json.dumps(saved))});"
            )
            page = context.new_page()
            page.route("**/*", route_request)
            page.goto("http://wizard.test/ui")
            page.get_by_text("需求解析通过").wait_for()
            page.get_by_role("button", name="下一步").click()
            assert page.locator("#step-title").text_content() == "执行任务"

            for _ in range(3):
                with page.expect_response(
                    lambda response: urlparse(response.url).path
                    == "/api/ui/workflow-state"
                ):
                    page.locator("#refresh-status").click()
                assert page.locator("#step-title").text_content() == "执行任务"

            page.reload()
            page.wait_for_function(
                "document.getElementById('step-title')?.textContent === '执行任务'"
            )
            assert page.locator("#step-title").text_content() == "执行任务"

            page.evaluate(
                """
                () => {
                  const input = document.getElementById('prompt-input');
                  input.value += ' modified';
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            assert page.locator("#step-title").text_content() == "指定绘图需求"

            workflow.update(
                {
                    "state": "prompt_required",
                    "step": 3,
                    "reason": "计划已失效，请重新解析。",
                    "next_action": "解析需求",
                    "next_block_reason": "请先解析绘图需求",
                    "buttons": {"parse_prompt": True},
                    "plan_summary": None,
                }
            )
            page.evaluate(
                """
                () => {
                  const saved = JSON.parse(localStorage.getItem('biorender-guided-workflow-v1'));
                  saved.step = 4;
                  saved.planId = 'figure_plan_1';
                  localStorage.setItem('biorender-guided-workflow-v1', JSON.stringify(saved));
                }
                """
            )
            page.reload()
            page.locator("#step-title").wait_for()
            assert page.locator("#step-title").text_content() == "指定绘图需求"
        finally:
            context.close()
            browser.close()
