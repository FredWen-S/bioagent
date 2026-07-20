from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, Route, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "playwright"
BASE_URL = "http://127.0.0.1:8765/ui"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def json_response(route: Route, payload: dict[str, object], status: int = 200) -> None:
    route.fulfill(
        status=status,
        content_type="application/json; charset=utf-8",
        body=json.dumps(payload, ensure_ascii=False),
    )


def run_summary(status: str, *, dry_run: bool = False) -> dict[str, object]:
    needs_review = 1 if status == "completed_with_unknown" else 0
    workflow_status = "completed" if status == "completed_with_unknown" else status
    return {
        "run_id": "figure_dry_001" if dry_run else "figure_live_001",
        "title": "PD-1 / PD-L1 mechanism",
        "status": workflow_status,
        "friendly_status": "已安全停止" if status == "paused_approval" else "用户已确认",
        "total_elements": 5,
        "verified_elements": 4 if needs_review else 5,
        "needs_review_elements": needs_review,
        "failed_elements": 0,
        "policy_blocked_elements": 0,
        "total_actions": 9,
        "verified_actions": 9 if workflow_status == "completed" else 4,
        "completed_actions": 9 if workflow_status == "completed" else 4,
        "progress_percent": 100 if workflow_status == "completed" else 44,
        "steps": [
            {"key": key, "label": label, "status": "completed"}
            for key, label in (
                ("prepare", "准备浏览器"),
                ("policy", "检查安全策略"),
                ("search", "搜索素材"),
                ("insert", "插入素材"),
                ("labels", "添加标签"),
                ("connect", "添加连接"),
                ("layout", "调整布局"),
                ("save", "等待保存"),
                ("verify", "验证结果"),
            )
        ],
        "save_status": "已观察到自动保存",
        "can_resume": status == "paused_approval",
        "can_stop": status == "executing",
        "run_mode": "dry_run" if dry_run else "live_or_plan",
        "dry_run_completed": dry_run,
        "dry_run_confirmed": dry_run and workflow_status == "completed",
        "can_confirm_dry_run": dry_run and workflow_status == "awaiting_confirmation",
        "real_biorender_accepted": False,
        "task_fingerprint": "test-fingerprint",
        "completed_at": "2026-07-20T12:00:00+08:00",
        "current_action": None,
        "recent_logs": [
            {"status": "verified", "action_type": "save_project", "message": "verified"}
        ],
    }


class MockApi:
    def __init__(self) -> None:
        self.login = "not_verified"
        self.canvas: dict[str, str] | None = None
        self.job: dict[str, object] | None = None
        self.plan = False
        self.dry = False
        self.dry_confirmed = False
        self.run_status: str | None = None
        self.resume_polls = 0

    def workflow(self, query: dict[str, list[str]]) -> dict[str, object]:
        if self.job and self.job["status"] not in {"completed", "failed", "blocked", "stopped"}:
            kind = self.job["kind"]
            if kind == "manual_login":
                return self._workflow("login_checking", 1, "请完成登录")
            if kind == "canvas_check":
                return self._workflow("canvas_validating", 2, "正在检查画布")
            return self._workflow("executing", 4, "正在执行真实任务")
        if self.login != "verified":
            return self._workflow("login_required", 1, "请先登录")
        if not self.canvas:
            return self._workflow("canvas_required", 2, "请检查空白画布")
        if self.run_status:
            if self.run_status == "paused_approval":
                return self._workflow("paused", 4, "任务已安全停止")
            if self.run_status == "completed_with_unknown":
                return self._workflow("completed_with_unknown", 5, "有元素需要人工检查")
            return self._workflow("completed", 5, "任务已完成")
        if self.dry and not self.dry_confirmed:
            return self._workflow("dry_run_confirmation_required", 3, "预演未操作真实页面")
        if self.dry_confirmed:
            return self._workflow("ready_to_execute", 4, "可以开始执行")
        if self.plan or query.get("plan_id"):
            result = self._workflow("prompt_parsed", 3, "需求已解析")
            result["plan_summary"] = self.plan_summary()
            return result
        return self._workflow("prompt_required", 3, "请输入绘图需求")

    @staticmethod
    def _workflow(name: str, step: int, reason: str) -> dict[str, object]:
        return {
            "state": name,
            "step": step,
            "reason": reason,
            "next_action": "完成当前步骤",
            "refresh_recoverable": True,
            "buttons": {},
            "plan_summary": None,
        }

    @staticmethod
    def plan_summary() -> dict[str, object]:
        return {
            "asset_count": 3,
            "label_count": 3,
            "relation_count": 2,
            "layout_description": "从左到右的线性布局",
            "risks": [],
            "supported": True,
        }

    def status(self) -> dict[str, object]:
        active = []
        if self.job and self.job["status"] not in {"completed", "failed", "blocked", "stopped"}:
            active = [self.job]
        return {
            "backend": "normal",
            "database": "normal",
            "browser_login": self.login,
            "verified_canvas": self.canvas,
            "active_jobs": active,
            "recent_runs": [],
            "ai_generate": "disabled_by_policy",
        }

    def handle(self, route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        if path == "/api/ui/evidence/1":
            route.fulfill(status=200, content_type="image/png", body=PNG)
            return
        if path == "/api/ui/status":
            json_response(route, self.status())
            return
        if path == "/api/ui/workflow-state":
            json_response(route, self.workflow(parse_qs(parsed.query)))
            return
        if path == "/api/ui/login/open":
            self.login = "waiting_user"
            self.job = self.make_job("login_job", "manual_login", "waiting_user")
            json_response(route, self.job, 202)
            return
        if path == "/api/ui/login/complete":
            self.login = "verified"
            assert self.job
            self.job["status"] = "completed"
            self.job["message"] = "登录已确认"
            json_response(route, self.job)
            return
        if path == "/api/ui/canvas/check":
            self.canvas = {
                "redacted_url": "https://app.biorender.com/.../<redacted>",
                "figure_identifier": "test-blank",
                "title": "Test Blank Figure",
                "checked_at": "2026-07-20T11:00:00+08:00",
            }
            self.job = self.make_job("canvas_job", "canvas_check", "completed")
            self.job["result"] = {"canvas_verified": True, **self.canvas}
            json_response(route, self.job, 202)
            return
        if path == "/api/ui/plans":
            self.plan = True
            json_response(
                route,
                {
                    **run_summary("validated"),
                    "run_id": "figure_plan_001",
                    "run_mode": "live_or_plan",
                    "task_fingerprint": "test-fingerprint",
                    "task_summary": self.plan_summary(),
                    "scientific_validation_passed": True,
                    "validation_issues": [],
                },
            )
            return
        if path == "/api/ui/dry-run":
            self.dry = True
            json_response(route, run_summary("awaiting_confirmation", dry_run=True))
            return
        if path.endswith("/confirm-dry-run"):
            self.dry_confirmed = True
            json_response(route, run_summary("completed", dry_run=True))
            return
        if path == "/api/ui/live-runs":
            self.run_status = "executing"
            self.job = self.make_job("live_job", "live_figure", "running", "figure_live_001")
            json_response(route, self.job, 202)
            return
        if path.endswith("/stop"):
            self.run_status = "paused_approval"
            assert self.job
            self.job["status"] = "stopped"
            self.job["message"] = "任务已安全停止"
            json_response(route, self.job, 202)
            return
        if path.endswith("/resume"):
            self.run_status = "executing"
            self.resume_polls = 0
            self.job = self.make_job("resume_job", "live_figure", "running", "figure_live_001")
            json_response(route, self.job, 202)
            return
        if path.endswith("/verify"):
            result = run_summary("completed_with_unknown")
            result["verification_passed"] = False
            json_response(route, result)
            return
        if path == "/api/ui/workflow/reset":
            self.canvas = None
            self.plan = False
            self.dry = False
            self.dry_confirmed = False
            self.run_status = None
            self.job = None
            json_response(route, {"ok": True})
            return
        if path.startswith("/api/ui/jobs/"):
            assert self.job
            if self.job["id"] == "resume_job":
                self.resume_polls += 1
                if self.resume_polls >= 2:
                    self.job["status"] = "completed"
                    self.job["message"] = "任务已完成"
                    self.run_status = "completed_with_unknown"
            json_response(route, self.job)
            return
        if path.endswith("/elements"):
            json_response(
                route,
                {
                    "run_id": "figure_live_001",
                    "items": [
                        {
                            "name": "PD-1",
                            "type": "素材",
                            "status": "verified",
                            "friendly_status": "已确认",
                            "message": "已验证",
                        },
                        {
                            "name": "PD-L1",
                            "type": "素材",
                            "status": "unknown",
                            "friendly_status": "需要人工检查",
                            "message": "请检查截图",
                        },
                    ],
                },
            )
            return
        if path.endswith("/evidence"):
            json_response(
                route,
                {
                    "run_id": "figure_live_001",
                    "items": [
                        {
                            "id": 1,
                            "kind": "final",
                            "name": "final.png",
                            "is_image": True,
                            "preview_url": "/api/ui/evidence/1",
                        }
                    ],
                },
            )
            return
        if path.startswith("/api/ui/runs/"):
            if "dry" in path:
                status = "completed" if self.dry_confirmed else "awaiting_confirmation"
                json_response(route, run_summary(status, dry_run=True))
            else:
                json_response(route, run_summary(self.run_status or "executing"))
            return
        json_response(route, {"message": f"Unhandled mock route: {path}"}, 404)

    @staticmethod
    def make_job(
        job_id: str,
        kind: str,
        status: str,
        figure_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": job_id,
            "kind": kind,
            "status": status,
            "message": status,
            "figure_id": figure_id,
            "error_code": None,
            "created_at": "2026-07-20T11:00:00+08:00",
            "updated_at": "2026-07-20T11:00:05+08:00",
            "elapsed_seconds": 5,
            "result": None,
        }


def assert_layout(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )
    visible = page.locator(".bottom-actions button:visible")
    boxes = [visible.nth(index).bounding_box() for index in range(visible.count())]
    actual = [box for box in boxes if box]
    for index, first in enumerate(actual):
        for second in actual[index + 1 :]:
            overlap_x = min(
                first["x"] + first["width"], second["x"] + second["width"]
            ) - max(first["x"], second["x"])
            overlap_y = min(
                first["y"] + first["height"], second["y"] + second["height"]
            ) - max(first["y"], second["y"])
            assert overlap_x <= 0 or overlap_y <= 0


def run() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    mock = MockApi()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.route("**/api/ui/**", mock.handle)
        page.goto(BASE_URL, wait_until="networkidle")
        page.get_by_role("button", name="打开 BioRender 登录页面").click()
        page.get_by_role("button", name="我已完成登录，检查状态").click()
        expect(page.get_by_role("button", name="下一步")).to_be_enabled()
        page.get_by_role("button", name="下一步").click()
        page.get_by_label("BioRender Figure URL").fill("https://app.biorender.com/figure/test-blank")
        page.get_by_label("我已确认使用可丢弃的空白 Figure").check()
        page.get_by_role("button", name="检查画布").click()
        expect(page.get_by_role("button", name="下一步")).to_be_enabled()
        page.get_by_role("button", name="下一步").click()
        page.get_by_label("Prompt 输入").check()
        page.get_by_label("用自然语言描述希望绘制的科研图").fill(
            "绘制 PD-1 与 PD-L1 结合并抑制 T 细胞活化的机制图"
        )
        page.get_by_role("button", name="解析需求").click()
        page.get_by_text("系统解析后的任务摘要").wait_for()
        page.get_by_role("button", name="运行安全预演").click()
        page.get_by_text("安全预演已完成，未操作真实 BioRender 页面。", exact=True).wait_for()
        page.get_by_role("button", name="确认预演结果并继续").click()
        page.get_by_role("button", name="开始执行").click()
        page.get_by_role("button", name="安全停止").click()
        page.get_by_role("button", name="继续上次任务").wait_for()
        page.get_by_role("button", name="继续上次任务").click()
        page.reload(wait_until="networkidle")
        page.locator("#step-title").get_by_text("查看完成结果", exact=True).wait_for(timeout=10_000)
        page.get_by_text("已完成但需要人工检查", exact=True).first.wait_for()
        assert page.get_by_text("unknown 元素不能视为成功").is_visible()
        assert_layout(page)
        page.screenshot(path=OUTPUT / "guided-ui-desktop.png", full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="networkidle")
        page.locator("#step-title").get_by_text("查看完成结果", exact=True).wait_for()
        assert_layout(page)
        page.screenshot(path=OUTPUT / "guided-ui-mobile.png", full_page=True)
        browser.close()


if __name__ == "__main__":
    run()
