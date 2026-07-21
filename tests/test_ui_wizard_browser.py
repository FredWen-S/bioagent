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


def test_dry_run_confirmation_state_survives_polling_reload_and_confirmation() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    fingerprint = "a" * 64
    plan_fingerprint = "b" * 64
    dry_summary = {
        "target_canvas": {
            "redacted_url": "https://app.biorender.com/.../<redacted>",
            "confirmed_test_canvas": True,
        },
        "task": {
            "figure_title": "PD-1 mechanism",
            "asset_count": 2,
            "label_count": 2,
            "connection_count": 1,
            "total_action_count": 5,
        },
        "planned_actions": {
            "search_assets": ["T cell"],
            "insert_assets": ["t_cell"],
            "add_labels": ["T cell"],
            "add_connections": ["t_cell → pd1"],
            "adjust_layout": True,
        },
        "safety_limits": {
            "biorender_ai_generate": "forbidden",
            "export": "forbidden",
            "share": "forbidden",
            "purchase": "forbidden",
            "other_figures": "not_modified",
            "real_biorender_modified": False,
        },
        "result": {
            "policy_check_passed": True,
            "blocked_action_count": 0,
            "warning_count": 0,
            "manual_review_items": [],
            "can_enter_live_run": True,
        },
        "evidence": {
            "screenshot_note": "安全预演不打开真实页面，因此不产生真实画布截图。",
            "audit_event": {
                "event_type": "dry_run_completed",
                "created_at": "2026-07-21T00:00:00+00:00",
            },
            "recent_logs": [
                {"sequence": 1, "action_type": "search_asset", "status": "succeeded"}
            ],
        },
        "dry_run_actions": [
            {
                "sequence": 1,
                "action_id": "action_search",
                "action_type": "search_asset",
                "status": "succeeded",
                "risk_level": "low",
                "blocked": False,
            }
        ],
    }
    awaiting = {
        "state": "dry_run_confirmation_required",
        "step": 4,
        "reason": "安全预演已完成，未操作真实 BioRender 页面。",
        "next_action": "查看并确认预演结果",
        "buttons": {"confirm_dry_run": False, "start_live": False},
        "dry_run_id": "figure_plan_1",
        "dry_run_completed": True,
        "dry_run_failed": False,
        "dry_run_confirmed": False,
        "can_confirm_dry_run": True,
        "task_fingerprint": fingerprint,
        "plan_fingerprint": plan_fingerprint,
        "source_plan_id": "figure_plan_1",
        "dry_run_summary": dry_summary,
    }
    confirmed = {
        **awaiting,
        "state": "ready_to_execute",
        "step": 4,
        "dry_run_confirmed": True,
        "can_confirm_dry_run": False,
        "buttons": {"confirm_dry_run": False, "start_live": True},
    }
    current_workflow = awaiting
    stale_once = False

    def route_request(route: Route) -> None:
        nonlocal current_workflow, stale_once
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
            if stale_once:
                stale_once = False
                route.fulfill(
                    json={
                        "state": "prompt_parsed",
                        "step": 3,
                        "reason": "旧轮询结果",
                        "buttons": {"run_dry_run": True},
                    }
                )
            else:
                route.fulfill(json=current_workflow)
        elif path == "/api/ui/runs/figure_plan_1/confirm-dry-run":
            current_workflow = confirmed
            route.fulfill(
                json={
                    "dry_run_id": "figure_plan_1",
                    "status": "confirmed",
                    "dry_run_completed": True,
                    "dry_run_confirmed": True,
                    "can_confirm_dry_run": False,
                    "task_fingerprint": fingerprint,
                    "plan_fingerprint": plan_fingerprint,
                    "source_plan_id": "figure_plan_1",
                    "summary": dry_summary,
                }
            )
        else:
            route.fulfill(status=404, json={"error_code": "NOT_FOUND"})

    saved = {
        "step": 4,
        "taskMode": "prompt",
        "prompt": "T cell activates Tumor cell",
        "canvasUrl": "https://app.biorender.com/figure/disposable",
        "blankCanvasConfirmed": True,
        "canvasVerified": True,
        "planId": "figure_plan_1",
        "taskFingerprint": fingerprint,
        "planFingerprint": plan_fingerprint,
        "planCanvasUrl": "https://app.biorender.com/figure/disposable",
        "dryRunId": "figure_plan_1",
        "dryRunFingerprint": plan_fingerprint,
        "runId": None,
        "currentJobId": None,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            context.add_init_script(
                "localStorage.setItem('biorender-guided-workflow-v1', "
                f"{json.dumps(json.dumps(saved))});"
            )
            page = context.new_page()
            page.route("**/*", route_request)
            page.goto("http://wizard.test/ui")
            page.locator("#dry-run-review").wait_for(state="visible")
            assert page.locator("#confirm-dry-run").is_enabled()
            assert "可以确认" in page.locator("#dry-run-confirm-reason").text_content()
            assert "PD-1 mechanism" in page.locator("#dry-title").text_content()
            assert "不产生真实画布截图" in page.locator("#dry-screenshot-note").text_content()

            stale_once = True
            page.locator("#refresh-status").click()
            page.wait_for_timeout(100)
            assert page.locator("#confirm-dry-run").is_enabled()
            assert page.locator("#execution-state").text_content() == "预演待确认"

            page.reload()
            page.locator("#dry-run-review").wait_for(state="visible")
            assert page.locator("#confirm-dry-run").is_enabled()

            page.locator("#confirm-dry-run").click()
            page.wait_for_function("!document.getElementById('start-live').disabled")
            assert page.locator("#start-live").is_enabled()

            page.reload()
            page.wait_for_function("!document.getElementById('start-live').disabled")
            assert page.locator("#start-live").is_enabled()

            current_workflow = awaiting
            page.reload()
            page.locator("#confirm-dry-run").wait_for(state="visible")
            page.evaluate(
                """
                () => {
                  const input = document.getElementById('prompt-input');
                  input.value = 'T cell activates Tumor cell, modified';
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            assert page.locator("#confirm-dry-run").is_disabled()
            assert "当前 Prompt 已修改" in page.locator(
                "#dry-run-confirm-reason"
            ).text_content()

            page.reload()
            page.locator("#confirm-dry-run").wait_for(state="visible")
            page.evaluate(
                """
                () => {
                  const input = document.getElementById('editor-url');
                  input.value = 'https://app.biorender.com/figure/another-disposable';
                  input.dispatchEvent(new Event('input', { bubbles: true }));
                }
                """
            )
            assert page.locator("#confirm-dry-run").is_disabled()
            assert "当前画布已变化" in page.locator(
                "#dry-run-confirm-reason"
            ).text_content()
        finally:
            context.close()
            browser.close()


def test_live_failure_evidence_is_not_replaced_by_dry_run_polling() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")
    live_id = "figure_live_1"
    dry_id = "figure_dry_1"
    evidence_calls = 0
    live_summary = {
        "run_id": live_id,
        "status": "failed",
        "friendly_status": "执行失败",
        "failure_subcode": "search_ui_not_found",
        "can_resume": False,
        "resume_blocked_reason": "搜索能力准备失败；请修复后重新开始。",
        "completed_actions": 1,
        "total_actions": 94,
        "progress_percent": 1,
        "recent_logs": [
            {
                "action_type": "search_asset",
                "status": "failed",
                "message": "Live search input was not found",
            }
        ],
        "steps": [],
        "needs_review_elements": 0,
    }
    stale_dry_workflow = {
        "state": "ready_to_execute",
        "step": 4,
        "dry_run_id": dry_id,
        "dry_run_completed": True,
        "dry_run_confirmed": True,
        "can_confirm_dry_run": False,
        "buttons": {"start_live": True},
        "dry_run_summary": {
            "target_canvas": {"confirmed_test_canvas": True},
            "task": {"figure_title": "Dry report"},
            "planned_actions": {},
            "result": {"policy_check_passed": True, "can_enter_live_run": True},
            "evidence": {
                "screenshot_note": "安全预演不产生真实画布截图。",
                "recent_logs": [
                    {"action_type": "search_asset", "status": "simulated"}
                ],
            },
            "dry_run_actions": [],
        },
    }

    def route_request(route: Route) -> None:
        nonlocal evidence_calls
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
                    "verified_canvas": {"figure_identifier": "redacted"},
                    "active_jobs": [],
                }
            )
        elif path == "/api/version":
            route.fulfill(json={"git_commit": "test", "git_branch": "test"})
        elif path == "/api/ui/workflow-state":
            route.fulfill(json=stale_dry_workflow)
        elif path == f"/api/ui/runs/{live_id}":
            route.fulfill(json=live_summary)
        elif path == f"/api/ui/runs/{live_id}/elements":
            route.fulfill(json={"items": []})
        elif path == f"/api/ui/runs/{live_id}/evidence":
            evidence_calls += 1
            route.fulfill(
                json={
                    "items": [
                        {
                            "id": 99,
                            "kind": "failure",
                            "name": "live-failure.png",
                            "is_image": True,
                            "preview_url": (
                                "data:image/png;base64,"
                                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0l"
                                "EQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                            ),
                        }
                    ]
                }
            )
        else:
            route.fulfill(status=404, json={"error_code": "NOT_FOUND"})

    saved = {
        "step": 4,
        "taskMode": "preset",
        "canvasUrl": "https://app.biorender.com/figure/disposable",
        "blankCanvasConfirmed": True,
        "canvasVerified": True,
        "planId": dry_id,
        "dryRunId": dry_id,
        "runId": live_id,
        "currentJobId": None,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            context.add_init_script(
                "localStorage.setItem('biorender-guided-workflow-v1', "
                f"{json.dumps(json.dumps(saved))});"
            )
            page = context.new_page()
            page.route("**/*", route_request)
            page.goto("http://wizard.test/ui")
            page.locator("#execution-evidence img").wait_for(state="visible")
            assert "Live search input was not found" in page.locator(
                "#recent-logs"
            ).text_content()
            assert live_id in page.locator("#evidence-source").text_content()
            assert "search_ui_not_found" in page.locator(
                "#failure-subcode"
            ).text_content()
            assert page.locator("#start-live").text_content() == "修复后重试"
            page.evaluate(
                "window.__liveEvidenceImage = document.querySelector('#execution-evidence img')"
            )
            previous_calls = evidence_calls
            with page.expect_response(
                lambda response: urlparse(response.url).path
                == f"/api/ui/runs/{live_id}/evidence"
            ):
                page.locator("#refresh-status").click()
            assert evidence_calls > previous_calls
            assert page.evaluate(
                "window.__liveEvidenceImage === document.querySelector('#execution-evidence img')"
            )
            assert "Live search input was not found" in page.locator(
                "#recent-logs"
            ).text_content()
            assert "安全预演不产生真实截图" not in page.locator(
                "#execution-evidence"
            ).text_content()
        finally:
            context.close()
            browser.close()


def test_prompt_and_canvas_changes_invalidate_old_dry_run_with_reason() -> None:
    javascript = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "当前 Prompt 已修改，请重新预演。" in javascript
    assert "当前画布已变化，请重新检查画布、解析需求并重新预演。" in javascript
    assert "找不到 dry_run_id；请重新执行安全预演。" in javascript
    assert "预演失败，不能确认。" in javascript
