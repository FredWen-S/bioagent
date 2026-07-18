from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.operator.biorender.locators import (
    CANVAS_LOCATORS,
    bounding_box,
    resolve_largest_visible,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.biorender.search import RuntimeCandidate
from app.operator.errors import DragDropFailed, UiLayoutChanged
from app.schemas.gui_action import BoundingBox, CoordinateSpace


@dataclass(slots=True)
class PreparedDrag:
    candidate: RuntimeCandidate
    canvas_locator: Any
    canvas_bbox: BoundingBox
    expected_bbox: BoundingBox
    baseline_canvas_path: str
    after_canvas_path: str


class SafeAssetDrag:
    def __init__(
        self,
        page: Any,
        *,
        evidence_dir: Path,
        policy: BioRenderPolicyGuard | None = None,
    ) -> None:
        self.page = page
        self.evidence_dir = evidence_dir
        self.policy = policy or BioRenderPolicyGuard()

    def prepare(
        self,
        candidate: RuntimeCandidate,
        run_id: str,
        *,
        target_x: float,
        target_y: float,
        target_width: float,
        evidence_stem: str = "asset",
    ) -> PreparedDrag:
        if not 0 <= target_x <= 1 or not 0 <= target_y <= 1:
            raise ValueError("target coordinates must be normalized to 0..1")
        if not 0 < target_width <= 0.5:
            raise ValueError("target width must be normalized to 0..0.5")
        self.policy.assert_page_safe(self.page)
        canvas = resolve_largest_visible(self.page, CANVAS_LOCATORS)
        if canvas is None:
            raise UiLayoutChanged("BioRender canvas could not be re-located before drag")
        canvas_bbox = bounding_box(canvas.locator)
        if canvas_bbox is None:
            raise UiLayoutChanged("BioRender canvas has no observable bounding box")
        width = canvas_bbox.width * target_width
        height = width
        expected = BoundingBox(
            x=canvas_bbox.x + canvas_bbox.width * target_x - width / 2,
            y=canvas_bbox.y + canvas_bbox.height * target_y - height / 2,
            width=width,
            height=height,
            coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
        )
        if (
            expected.x < canvas_bbox.x
            or expected.y < canvas_bbox.y
            or expected.x + expected.width > canvas_bbox.x + canvas_bbox.width
            or expected.y + expected.height > canvas_bbox.y + canvas_bbox.height
        ):
            raise DragDropFailed("Expected asset bounding box would extend outside the canvas")
        run_dir = self.evidence_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(
            character if character.isalnum() or character in {"-", "_"} else "_"
            for character in evidence_stem
        )[:120]
        baseline = run_dir / f"{safe_stem}-canvas-before-drag.png"
        after = run_dir / f"{safe_stem}-canvas-after-drag.png"
        canvas.locator.screenshot(path=str(baseline))
        return PreparedDrag(
            candidate=candidate,
            canvas_locator=canvas.locator,
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
            baseline_canvas_path=str(baseline),
            after_canvas_path=str(after),
        )

    def execute(self, prepared: PreparedDrag) -> str:
        self.policy.assert_page_safe(self.page)
        self.policy.assert_target_allowed(
            prepared.candidate.locator, candidate_context=True
        )
        try:
            if not prepared.candidate.locator.is_visible():
                raise DragDropFailed("Selected asset candidate is no longer visible")
            source = prepared.candidate.locator.bounding_box()
        except DragDropFailed:
            raise
        except Exception as error:
            raise DragDropFailed("Selected asset candidate became stale") from error
        if source is None:
            raise DragDropFailed("Selected asset candidate has no drag origin")
        target_x = prepared.expected_bbox.x + prepared.expected_bbox.width / 2
        target_y = prepared.expected_bbox.y + prepared.expected_bbox.height / 2
        source_x = source["x"] + source["width"] / 2
        source_y = source["y"] + source["height"] / 2
        self.page.mouse.move(source_x, source_y)
        self.page.mouse.down()
        self.page.mouse.move(target_x, target_y, steps=24)
        self.page.mouse.up()
        self.page.wait_for_timeout(1200)
        prepared.canvas_locator.screenshot(path=prepared.after_canvas_path)
        self.policy.assert_page_safe(self.page)
        return prepared.after_canvas_path
