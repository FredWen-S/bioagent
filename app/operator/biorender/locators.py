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
)

SEARCH_RESULTS_LOCATORS = (
    LocatorSpec("css", "[data-testid*='search-results']", confidence=0.96),
    LocatorSpec("css", "[data-testid*='asset-results']", confidence=0.94),
    LocatorSpec("css", "[role='listbox']", confidence=0.82),
    LocatorSpec("css", "[class*='search-results']", confidence=0.76),
    LocatorSpec("css", "[data-testid*='library-panel']", confidence=0.72),
)

CANVAS_LOCATORS = (
    LocatorSpec("css", "[data-testid*='canvas-container']", confidence=0.97),
    LocatorSpec("css", "[data-testid*='canvas']", confidence=0.92),
    LocatorSpec("css", ".konvajs-content", confidence=0.9),
    LocatorSpec("css", "main canvas", confidence=0.82),
    LocatorSpec("css", "canvas", confidence=0.68),
)

CANDIDATE_SELECTORS = (
    "[data-testid*='asset-card']",
    "[data-testid*='search-result']",
    "[draggable='true']",
)

MODAL_SELECTOR = "[role='dialog'], [aria-modal='true'], [class*='modal']"
INTERACTIVE_SELECTOR = "button, a, [role='button'], [role='menuitem']"


def resolve_first_visible(page: Any, specs: tuple[LocatorSpec, ...]) -> ResolvedLocator | None:
    for spec in specs:
        try:
            if spec.strategy == "role":
                locator = page.get_by_role(
                    spec.role, name=re.compile(spec.query, re.IGNORECASE)
                )
            elif spec.strategy == "label":
                locator = page.get_by_label(re.compile(spec.query, re.IGNORECASE))
            elif spec.strategy == "text":
                locator = page.get_by_text(re.compile(spec.query, re.IGNORECASE))
            else:
                locator = page.locator(spec.query)
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
            if spec.strategy == "role":
                locator = page.get_by_role(
                    spec.role, name=re.compile(spec.query, re.IGNORECASE)
                )
            elif spec.strategy == "label":
                locator = page.get_by_label(re.compile(spec.query, re.IGNORECASE))
            elif spec.strategy == "text":
                locator = page.get_by_text(re.compile(spec.query, re.IGNORECASE))
            else:
                locator = page.locator(spec.query)
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
