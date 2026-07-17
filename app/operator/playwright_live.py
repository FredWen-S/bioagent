from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.operator.errors import (
    AuthenticationRequired,
    DragDropFailed,
    SearchNoResult,
    UiLayoutChanged,
    UnsupportedLiveAction,
)
from app.operator.safety import ActionSafetyPolicy
from app.schemas.gui_action import ActionStatus, ActionType, GuiAction, GuiActionResult


class LivePlaywrightOperator:
    """Conservative Phase 0 BioRender adapter.

    Supported live actions are deliberately limited to opening an editor, searching,
    selecting a result, dragging one asset, capturing evidence, and observing autosave.
    Text and connectors require a UI calibration recording before they are enabled.
    """

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        evidence_dir: Path | None = None,
        headed: bool = True,
    ) -> None:
        self.profile_dir = profile_dir or settings.session_dir / "biorender-profile"
        self.evidence_dir = evidence_dir or settings.screenshot_dir
        self.headed = headed
        self.policy = ActionSafetyPolicy()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._selected_candidate: Any = None
        self._selected_entity_id: str | None = None
        self._entity_boxes: dict[str, tuple[int, int, int, int]] = {}

    @staticmethod
    def require_playwright() -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Live mode requires the optional browser dependencies: "
                "pip install -e '.[browser]' and playwright install chromium"
            ) from error
        return sync_playwright

    def start(self) -> None:
        if self._page is not None:
            return
        sync_playwright = self.require_playwright()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=not self.headed,
            viewport={"width": 1440, "height": 1000},
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def execute(self, action: GuiAction, attempt: int = 1) -> GuiActionResult:
        self.policy.check(action)
        self.start()
        dispatch = {
            ActionType.OPEN_EDITOR: self._open_editor,
            ActionType.SEARCH_ASSET: self._search_asset,
            ActionType.SELECT_ASSET: self._select_asset,
            ActionType.DRAG_ASSET: self._drag_asset,
            ActionType.CAPTURE_CANVAS: self._capture_canvas,
            ActionType.SAVE_PROJECT: self._observe_autosave,
        }
        handler = dispatch.get(action.action)
        if handler is None:
            raise UnsupportedLiveAction(
                f"{action.action.value} is not enabled in live Phase 0; calibrate this UI action first"
            )
        metadata = handler(action)
        screenshot_path = self._screenshot(action)
        return GuiActionResult(
            action_id=action.id,
            status=ActionStatus.SUCCEEDED,
            attempt=attempt,
            message=f"Live action {action.action.value} completed.",
            screenshot_path=str(screenshot_path),
            observed_bbox=metadata.pop("observed_bbox", None),
            metadata={"mode": "live", "evidence_kind": "screenshot", **metadata},
        )

    def _open_editor(self, action: GuiAction) -> dict[str, Any]:
        page = self._page
        page.goto(action.arguments["url"], wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        if self._authentication_visible():
            self._screenshot(action, suffix="authentication-required")
            raise AuthenticationRequired(
                "BioRender requires manual login. Run the browser-login command, authenticate in "
                "the visible window, then resume this figure. The agent never enters credentials."
            )
        if self._canvas_locator() is None and action.arguments.get("create_new"):
            new_button = self._first_visible(
                [
                    "button:has-text('New Figure')",
                    "button:has-text('Create New')",
                    "a:has-text('New Figure')",
                    "[data-testid*='new-figure']",
                ]
            )
            if new_button is not None:
                new_button.click(timeout=10_000)
                page.wait_for_timeout(1200)
                blank = self._first_visible(
                    [
                        "button:has-text('Blank')",
                        "[data-testid*='blank']",
                        "text=/blank figure/i",
                    ]
                )
                if blank is not None:
                    blank.click(timeout=10_000)
                    page.wait_for_timeout(1500)
        if self._canvas_locator() is None:
            raise UiLayoutChanged(
                "No BioRender canvas was detected. Open a blank Figure manually and pass its editor URL."
            )
        return {"url": page.url, "title": page.title()}

    def _search_asset(self, action: GuiAction) -> dict[str, Any]:
        search = self._first_visible(
            [
                "input[placeholder*='search' i]",
                "[role='searchbox']",
                "input[type='search']",
                "[data-testid*='search'] input",
            ]
        )
        if search is None:
            raise UiLayoutChanged("BioRender asset search input was not found")
        queries = [action.arguments["query"], *action.arguments.get("fallback_queries", [])]
        queries = queries[: int(action.arguments.get("max_queries", 5))]
        for query in queries:
            search.click()
            search.fill(query)
            self._page.wait_for_timeout(1200)
            candidates = self._candidate_locator()
            if candidates is not None and candidates.count() > 0:
                return {"selected_query": query, "candidate_count": candidates.count()}
        raise SearchNoResult(f"No visible BioRender asset result for queries: {queries}")

    def _select_asset(self, action: GuiAction) -> dict[str, Any]:
        candidates = self._candidate_locator()
        index = int(action.arguments["candidate_index"])
        if candidates is None or candidates.count() <= index:
            raise SearchNoResult(f"Asset candidate index {index} is unavailable")
        candidate = candidates.nth(index)
        candidate.scroll_into_view_if_needed()
        if not candidate.is_visible():
            raise SearchNoResult(f"Asset candidate index {index} is not visible")
        self._selected_candidate = candidate
        self._selected_entity_id = str(action.arguments["entity_id"])
        box = candidate.bounding_box()
        return {"candidate_index": index, "source_bbox": box}

    def _drag_asset(self, action: GuiAction) -> dict[str, Any]:
        if self._selected_candidate is None:
            raise DragDropFailed("No asset candidate is selected")
        source = self._selected_candidate.bounding_box()
        canvas_locator = self._canvas_locator()
        canvas = canvas_locator.bounding_box() if canvas_locator is not None else None
        if source is None or canvas is None:
            raise DragDropFailed("Source asset or canvas bounding box is unavailable")
        target_x = canvas["x"] + canvas["width"] * float(action.arguments["target_x"])
        target_y = canvas["y"] + canvas["height"] * float(action.arguments["target_y"])
        source_x = source["x"] + source["width"] / 2
        source_y = source["y"] + source["height"] / 2
        self._page.mouse.move(source_x, source_y)
        self._page.mouse.down()
        self._page.mouse.move(target_x, target_y, steps=20)
        self._page.mouse.up()
        self._page.wait_for_timeout(900)
        width = max(40, int(canvas["width"] * float(action.arguments.get("target_width", 0.12))))
        observed = (
            int(target_x - width / 2),
            int(target_y - width / 2),
            width,
            width,
        )
        entity_id = str(action.arguments["entity_id"])
        self._entity_boxes[entity_id] = observed
        self._selected_candidate = None
        self._selected_entity_id = None
        return {"observed_bbox": observed, "coordinate_mode": "normalized_canvas"}

    def _capture_canvas(self, action: GuiAction) -> dict[str, Any]:
        canvas = self._canvas_locator()
        if canvas is None:
            raise UiLayoutChanged("BioRender canvas disappeared before verification capture")
        return {"scope": action.arguments.get("scope", "full_canvas")}

    def _observe_autosave(self, action: GuiAction) -> dict[str, Any]:
        self._page.wait_for_timeout(1000)
        return {
            "save_mode": action.arguments.get("mode", "biorender_autosave"),
            "note": "No export, overwrite, publish, or sharing action was invoked.",
        }

    def _authentication_visible(self) -> bool:
        page = self._page
        return bool(
            re.search(r"(?:login|log-in|sign-in|signin)", page.url, re.IGNORECASE)
            or page.locator("input[type='password']").count() > 0
        )

    def _canvas_locator(self) -> Any | None:
        return self._first_visible(
            [
                "[data-testid*='canvas']",
                ".konvajs-content",
                "main canvas",
                "canvas",
            ]
        )

    def _candidate_locator(self) -> Any | None:
        selectors = [
            "[data-testid*='asset-card']",
            "[data-testid*='search-result']",
            "[draggable='true']",
            "[class*='asset'] img",
        ]
        for selector in selectors:
            locator = self._page.locator(selector)
            if locator.count() > 0:
                return locator
        return None

    def _first_visible(self, selectors: list[str]) -> Any | None:
        for selector in selectors:
            locator = self._page.locator(selector)
            count = min(locator.count(), 20)
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        return None

    def _screenshot(self, action: GuiAction, suffix: str | None = None) -> Path:
        figure_dir = self.evidence_dir / action.figure_id
        figure_dir.mkdir(parents=True, exist_ok=True)
        name = f"{action.sequence:04d}_{action.id}"
        if suffix:
            name += f"_{suffix}"
        path = figure_dir / f"{name}.png"
        self._page.screenshot(path=str(path), full_page=True)
        return path

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._context = None
        self._playwright = None
        self._page = None

    @classmethod
    def manual_login(cls, url: str = "https://app.biorender.com/") -> None:
        operator = cls(headed=True)
        try:
            operator.start()
            operator._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            print("Complete BioRender login manually in the visible browser window.")
            input("After the dashboard/editor is visible, press Enter here to preserve the session: ")
        finally:
            operator.close()

