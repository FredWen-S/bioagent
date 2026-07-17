from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.operator.biorender.locators import (
    CANVAS_LOCATORS,
    SEARCH_INPUT_LOCATORS,
    SEARCH_RESULTS_LOCATORS,
    ResolvedLocator,
    bounding_box,
    resolve_first_visible,
    resolve_largest_visible,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.errors import CalibrationFailed
from app.schemas.biorender_probe import (
    CalibratedRegion,
    CalibrationStatus,
    LocatorEvidence,
    UiCalibrationProfile,
)
from app.schemas.gui_action import BoundingBox, CoordinateSpace
from app.storage.database import FigureDatabase


class BioRenderUiCalibrator:
    def __init__(
        self,
        page: Any,
        *,
        database: FigureDatabase | None = None,
        output_dir: Path | None = None,
        policy: BioRenderPolicyGuard | None = None,
    ) -> None:
        self.page = page
        self.database = database
        self.output_dir = output_dir or settings.calibration_dir
        self.policy = policy or BioRenderPolicyGuard()

    def calibrate(self) -> tuple[UiCalibrationProfile, Path]:
        profile_id = f"calibration_{uuid.uuid4().hex[:12]}"
        timestamp = datetime.now(UTC)
        run_dir = self.output_dir / timestamp.strftime("%Y%m%d") / profile_id
        run_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = run_dir / "calibration.png"
        self.page.screenshot(path=str(screenshot_path), full_page=True)

        viewport_size = getattr(self.page, "viewport_size", None) or {}
        viewport = BoundingBox(
            x=0,
            y=0,
            width=float(viewport_size.get("width", 0) or 1),
            height=float(viewport_size.get("height", 0) or 1),
            coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
        )
        search = resolve_first_visible(self.page, SEARCH_INPUT_LOCATORS)
        results = resolve_first_visible(self.page, SEARCH_RESULTS_LOCATORS)
        if results is None and search is not None:
            results = self._infer_results_region(search.locator)
        canvas = resolve_largest_visible(self.page, CANVAS_LOCATORS)
        modals = self.policy.scan_modals(self.page)
        ai_controls = self.policy.scan_ai_controls(self.page)
        diagnostics: list[str] = []
        if search is None:
            diagnostics.append("required search input was not found")
        if results is None:
            diagnostics.append("required search results region was not found")
        if canvas is None:
            diagnostics.append("required canvas region was not found")
        if modals:
            diagnostics.append(
                "visible modal blocks calibration: " + modals[0].classification
            )
        try:
            if self.page.locator("input[type='password']").count() > 0:
                diagnostics.append("authentication input is visible")
        except Exception:
            pass

        status = CalibrationStatus.VALID
        if modals and any(
            modal.classification != "unknown_modal" for modal in modals
        ):
            status = CalibrationStatus.BLOCKED_BY_POLICY
        elif diagnostics:
            status = CalibrationStatus.INVALID
        editor_loaded = search is not None and canvas is not None and not modals
        signature = {
            "viewport": [viewport.width, viewport.height],
            "search": search.evidence.model_dump() if search else None,
            "results": results.evidence.model_dump() if results else None,
            "canvas": canvas.evidence.model_dump() if canvas else None,
        }
        profile_version = "ui-" + hashlib.sha256(
            json.dumps(signature, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        profile = UiCalibrationProfile(
            profile_id=profile_id,
            ui_profile_version=profile_version,
            created_at=timestamp.isoformat(),
            url=str(self.page.url),
            viewport=viewport,
            editor_loaded=editor_loaded,
            status=status,
            search_input=self._region("search_input", search),
            search_results_region=self._region("search_results_region", results),
            canvas=self._region("canvas", canvas),
            visible_modals=modals,
            ai_controls=ai_controls,
            screenshot_path=str(screenshot_path),
            diagnostics=diagnostics,
        )
        profile_path = run_dir / "profile.json"
        profile_path.write_text(
            profile.model_dump_json(indent=2), encoding="utf-8"
        )
        if self.database is not None:
            self.database.save_calibration_profile(profile, str(profile_path))
        if status != CalibrationStatus.VALID:
            raise CalibrationFailed(
                "; ".join(diagnostics) or "BioRender UI calibration was blocked",
                profile_path=str(profile_path),
            )
        return profile, profile_path

    @staticmethod
    def _region(name: str, resolved: Any | None) -> CalibratedRegion:
        if resolved is None:
            return CalibratedRegion(
                name=name,
                found=False,
                diagnostics=[f"{name} not found"],
            )
        return CalibratedRegion(
            name=name,
            found=True,
            bbox=bounding_box(resolved.locator),
            locator=resolved.evidence,
        )

    @staticmethod
    def _infer_results_region(search_locator: Any) -> ResolvedLocator | None:
        ancestor_queries = (
            "xpath=ancestor::*[self::aside or @role='complementary' or "
            "contains(@data-testid,'library')][1]",
            "xpath=ancestor::*[contains(@class,'sidebar') or contains(@class,'panel')][1]",
        )
        for query in ancestor_queries:
            try:
                ancestor = search_locator.locator(query)
                if ancestor.count() and ancestor.first.is_visible() and ancestor.first.bounding_box():
                    return ResolvedLocator(
                        locator=ancestor.first,
                        evidence=LocatorEvidence(
                            strategy="ancestor",
                            query=query,
                            confidence=0.68,
                        ),
                    )
            except Exception:
                continue
        return None
