from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas.biorender_probe import LocatorEvidence
from app.schemas.gui_action import BoundingBox, CoordinateSpace


@dataclass(frozen=True, slots=True)
class LocatorSpec:
    strategy: str
    query: str
    role: str | None = None
    confidence: float = 0.8


@dataclass(slots=True)
class ResolvedLocator:
    locator: Any
    evidence: LocatorEvidence


SEARCH_INPUT_LOCATORS = (
    LocatorSpec("role", r"search|搜索", role="searchbox", confidence=0.98),
    LocatorSpec("label", r"search|搜索", confidence=0.95),
    LocatorSpec("css", "input[placeholder*='search' i]", confidence=0.9),
    LocatorSpec("css", "input[type='search']", confidence=0.86),
    LocatorSpec("css", "[data-testid*='search'] input", confidence=0.84),
    LocatorSpec("css", "[role='searchbox']", confidence=0.83),
    LocatorSpec("css", "[contenteditable='true'][aria-label*='search' i]", confidence=0.82),
    LocatorSpec("css", "[contenteditable='true'][data-placeholder*='search' i]", confidence=0.8),
    LocatorSpec("css", "[placeholder*='search' i]", confidence=0.78),
)

ASSET_PANEL_ENTRY_LOCATORS = (
    LocatorSpec("role", r"^search$|icons? and templates|搜索", role="button", confidence=0.96),
    LocatorSpec("label", r"^search$|icons? and templates|搜索", confidence=0.93),
    LocatorSpec("css", "[data-testid*='search'][role='button']", confidence=0.9),
    LocatorSpec("css", "[aria-label='Icons and templates' i]", confidence=0.88),
)

SEARCH_EMPTY_LOCATORS = (
    LocatorSpec("text", r"no results|no assets|未找到|没有结果", confidence=0.94),
    LocatorSpec("css", "[data-testid*='no-results']", confidence=0.92),
    LocatorSpec("css", "[data-testid*='empty-state']", confidence=0.86),
)

SEARCH_RATE_LIMIT_LOCATORS = (
    LocatorSpec("text", r"429|too many requests|rate limit|请求过多|稍后再试", confidence=0.98),
    LocatorSpec("css", "[data-testid*='rate-limit']", confidence=0.95),
)

SEARCH_RESULTS_LOCATORS = (
    LocatorSpec("css", "[data-testid*='search-results']", confidence=0.96),
    LocatorSpec("css", "[data-testid*='asset-results']", confidence=0.94),
    LocatorSpec("css", "[role='listbox']", confidence=0.82),
    LocatorSpec("css", "[class*='search-results']", confidence=0.76),
    LocatorSpec("css", "[data-testid*='library-panel']", confidence=0.72),
)

EDITOR_CHROME_LOCATORS = (
    LocatorSpec("css", "[data-testid*='editor-root']", confidence=0.98),
    LocatorSpec("css", "[data-testid*='illustration-editor']", confidence=0.97),
    LocatorSpec("css", "[aria-label*='illustration editor' i]", confidence=0.94),
    LocatorSpec("css", "[role='toolbar']", confidence=0.9),
    LocatorSpec("css", "main", confidence=0.72),
)

ASSET_PANEL_LOCATORS = (
    LocatorSpec("css", "[data-testid*='asset-panel']", confidence=0.98),
    LocatorSpec("css", "[data-testid*='library-panel']", confidence=0.97),
    LocatorSpec(
        "css",
        "[aria-label='Icons and templates' i] .workbench-block",
        confidence=0.96,
    ),
    LocatorSpec(
        "css",
        "[aria-label*='图标'][aria-label*='模板'] .workbench-block",
        confidence=0.95,
    ),
    LocatorSpec("css", "[data-testid*='library']", confidence=0.92),
    LocatorSpec("css", "[aria-label*='asset' i]", confidence=0.9),
    LocatorSpec("css", "[aria-label*='library' i]", confidence=0.88),
    LocatorSpec("css", "aside", confidence=0.8),
    LocatorSpec("css", "[role='complementary']", confidence=0.78),
)

CANVAS_LOCATORS = (
    LocatorSpec("css", "[data-testid*='canvas-container']", confidence=0.97),
    LocatorSpec("css", "[data-testid*='canvas']", confidence=0.92),
    LocatorSpec("css", ".konvajs-content", confidence=0.9),
    LocatorSpec("css", "main canvas", confidence=0.82),
    LocatorSpec("css", "canvas", confidence=0.68),
)

CLEAR_SELECTION_LOCATORS = (
    LocatorSpec("css", "[data-testid='canvas-container']", confidence=0.9),
    LocatorSpec("css", "[data-testid*='canvas']", confidence=0.8),
)

SELECTED_OBJECT_LOCATORS = (
    LocatorSpec("css", "[data-testid*='canvas-element'][data-selected='true']", confidence=0.98),
    LocatorSpec("css", "[data-testid*='selection-box']", confidence=0.94),
    LocatorSpec("css", "[class*='selection-box']", confidence=0.8),
    LocatorSpec("css", "[class*='transformer']", confidence=0.75),
)

RESIZE_HANDLE_LOCATORS = (
    LocatorSpec("css", "[data-testid*='resize-handle']", confidence=0.98),
    LocatorSpec("label", r"resize|调整大小|缩放", confidence=0.92),
    LocatorSpec("css", "[class*='resize-handle']", confidence=0.78),
    LocatorSpec("css", "[class*='transformer'] [class*='bottom-right']", confidence=0.72),
)

ROTATE_HANDLE_LOCATORS = (
    LocatorSpec("css", "[data-testid*='rotate-handle']", confidence=0.98),
    LocatorSpec("label", r"rotate|旋转", confidence=0.92),
    LocatorSpec("css", "[class*='rotate-handle']", confidence=0.78),
)

TEXT_TOOL_LOCATORS = (
    LocatorSpec("role", r"^(?:text|add text|文字|文本)$", role="button", confidence=0.98),
    LocatorSpec("label", r"^(?:text|add text|文字|文本)$", confidence=0.94),
    LocatorSpec("css", "[data-testid*='text-tool']", confidence=0.92),
)

CONNECTOR_TOOL_LOCATORS: dict[str, tuple[LocatorSpec, ...]] = {
    "arrow": (
        LocatorSpec("role", r"arrow|箭头", role="button", confidence=0.96),
        LocatorSpec("label", r"arrow|箭头", confidence=0.92),
        LocatorSpec("css", "[data-testid*='arrow-tool']", confidence=0.9),
    ),
    "line": (
        LocatorSpec("role", r"^(?:line|connector|连接线|直线)$", role="button", confidence=0.94),
        LocatorSpec("label", r"line|connector|连接线|直线", confidence=0.9),
        LocatorSpec("css", "[data-testid*='line-tool']", confidence=0.88),
    ),
    "t_bar": (
        LocatorSpec("role", r"inhibition|inhibit|t[- ]?bar|抑制", role="button", confidence=0.96),
        LocatorSpec("label", r"inhibition|inhibit|t[- ]?bar|抑制", confidence=0.92),
        LocatorSpec("css", "[data-testid*='inhibition-tool']", confidence=0.9),
    ),
    "blocking_line": (
        LocatorSpec(
            "role", r"inhibition|blocking|block|t[- ]?bar|阻断", role="button", confidence=0.94
        ),
        LocatorSpec("label", r"inhibition|blocking|block|t[- ]?bar|阻断", confidence=0.9),
        LocatorSpec("css", "[data-testid*='inhibition-tool']", confidence=0.88),
    ),
}

GROUP_TOOL_LOCATORS = (
    LocatorSpec("role", r"^group$|分组|组合", role="button", confidence=0.96),
    LocatorSpec("label", r"^group$|分组|组合", confidence=0.92),
    LocatorSpec("css", "[data-testid*='group']", confidence=0.86),
)

ALIGN_TOOL_LOCATORS: dict[str, tuple[LocatorSpec, ...]] = {
    "left": (
        LocatorSpec("role", r"align left|左对齐", role="button", confidence=0.94),
        LocatorSpec("label", r"align left|左对齐", confidence=0.9),
    ),
    "center": (
        LocatorSpec("role", r"align center|水平居中", role="button", confidence=0.94),
        LocatorSpec("label", r"align center|水平居中", confidence=0.9),
    ),
    "middle": (
        LocatorSpec("role", r"align middle|垂直居中", role="button", confidence=0.94),
        LocatorSpec("label", r"align middle|垂直居中", confidence=0.9),
    ),
    "right": (
        LocatorSpec("role", r"align right|右对齐", role="button", confidence=0.94),
        LocatorSpec("label", r"align right|右对齐", confidence=0.9),
    ),
}

DISTRIBUTE_TOOL_LOCATORS: dict[str, tuple[LocatorSpec, ...]] = {
    "horizontal": (
        LocatorSpec(
            "role",
            r"distribute horizontally|horizontal distribution|水平分布",
            role="button",
            confidence=0.94,
        ),
        LocatorSpec(
            "label",
            r"distribute horizontally|horizontal distribution|水平分布",
            confidence=0.9,
        ),
    ),
    "vertical": (
        LocatorSpec(
            "role",
            r"distribute vertically|vertical distribution|垂直分布",
            role="button",
            confidence=0.94,
        ),
        LocatorSpec(
            "label",
            r"distribute vertically|vertical distribution|垂直分布",
            confidence=0.9,
        ),
    ),
}

SAVE_STATUS_LOCATORS = (
    LocatorSpec("text", r"all changes saved|saved|已保存|所有更改已保存", confidence=0.94),
    LocatorSpec("css", "[data-testid*='save-status']", confidence=0.9),
    LocatorSpec("css", "[aria-label*='saved' i]", confidence=0.86),
)

CANDIDATE_SELECTORS = (
    "[data-testid*='asset-card']",
    "[data-testid*='search-result']",
    "[draggable='true']",
)

MODAL_SELECTOR = "[role='dialog'], [aria-modal='true'], [class*='modal']"
INTERACTIVE_SELECTOR = "button, a, [role='button'], [role='menuitem']"


def locator_for_spec(page: Any, spec: LocatorSpec) -> Any:
    if spec.strategy == "role":
        return page.get_by_role(spec.role, name=re.compile(spec.query, re.IGNORECASE))
    if spec.strategy == "label":
        return page.get_by_label(re.compile(spec.query, re.IGNORECASE))
    if spec.strategy == "text":
        return page.get_by_text(re.compile(spec.query, re.IGNORECASE))
    return page.locator(spec.query)


def resolve_first_visible(page: Any, specs: tuple[LocatorSpec, ...]) -> ResolvedLocator | None:
    for spec in specs:
        try:
            locator = locator_for_spec(page, spec)
            count = min(locator.count(), 50)
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible() and candidate.bounding_box() is not None:
                    return ResolvedLocator(
                        locator=candidate,
                        evidence=LocatorEvidence(
                            strategy=spec.strategy,
                            query=spec.query,
                            confidence=spec.confidence,
                        ),
                    )
        except Exception:
            continue
    return None


def resolve_largest_visible(page: Any, specs: tuple[LocatorSpec, ...]) -> ResolvedLocator | None:
    best: tuple[float, ResolvedLocator] | None = None
    for spec in specs:
        try:
            locator = locator_for_spec(page, spec)
            count = min(locator.count(), 50)
            for index in range(count):
                candidate = locator.nth(index)
                box = candidate.bounding_box() if candidate.is_visible() else None
                if not box:
                    continue
                area = float(box["width"]) * float(box["height"])
                resolved = ResolvedLocator(
                    locator=candidate,
                    evidence=LocatorEvidence(
                        strategy=spec.strategy,
                        query=spec.query,
                        confidence=spec.confidence,
                    ),
                )
                if best is None or area > best[0]:
                    best = (area, resolved)
        except Exception:
            continue
    return best[1] if best else None


def bounding_box(locator: Any) -> BoundingBox | None:
    try:
        box = locator.bounding_box()
    except Exception:
        return None
    if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
        return None
    return BoundingBox(
        x=float(box["x"]),
        y=float(box["y"]),
        width=float(box["width"]),
        height=float(box["height"]),
        coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
    )


def is_inside(child: BoundingBox, parent: BoundingBox, tolerance: float = 3.0) -> bool:
    return (
        child.x >= parent.x - tolerance
        and child.y >= parent.y - tolerance
        and child.x + child.width <= parent.x + parent.width + tolerance
        and child.y + child.height <= parent.y + parent.height + tolerance
    )


def locator_text(locator: Any) -> str:
    parts: list[str] = []
    try:
        text = locator.inner_text(timeout=1000).strip()
        if text:
            parts.append(text)
    except Exception:
        pass
    for attribute in ("aria-label", "title", "data-testid", "data-label"):
        try:
            value = locator.get_attribute(attribute)
            if value:
                parts.append(f"{attribute}={value}")
        except Exception:
            continue
    return " | ".join(parts)
