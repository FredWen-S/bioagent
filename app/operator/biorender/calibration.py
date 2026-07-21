from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.operator.biorender.locators import (
    ASSET_PANEL_LOCATORS,
    CANVAS_LOCATORS,
    EDITOR_CHROME_LOCATORS,
    SEARCH_INPUT_LOCATORS,
    SEARCH_RESULTS_LOCATORS,
    LocatorSpec,
    ResolvedLocator,
    bounding_box,
    locator_for_spec,
    resolve_first_visible,
    resolve_largest_visible,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.errors import CalibrationFailed
from app.schemas.biorender_probe import (
    CalibratedRegion,
    CalibrationStatus,
    LocatorEvidence,
    LocatorMatchDiagnostic,
    UiAnchorDiagnostic,
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
        search, search_anchor = self._inspect_anchor(
            "search_input", SEARCH_INPUT_LOCATORS, required=False
        )
        results, results_anchor = self._inspect_anchor(
            "search_results_region", SEARCH_RESULTS_LOCATORS, required=False
        )
        if results is None and search is not None:
            results = self._infer_results_region(search.locator)
            if results is not None:
                results_anchor.matched = True
                results_anchor.selected_locator = results.evidence
                results_anchor.message = "inferred from visible search input ancestor"
        _editor_chrome, editor_anchor = self._inspect_anchor(
            "editor_chrome", EDITOR_CHROME_LOCATORS, largest=True
        )
        asset_panel, asset_anchor = self._inspect_anchor(
            "asset_panel",
            ASSET_PANEL_LOCATORS + SEARCH_RESULTS_LOCATORS,
            largest=True,
        )
        if asset_panel is None and results is not None:
            asset_panel = results
            asset_anchor.matched = True
            asset_anchor.selected_locator = results.evidence
            asset_anchor.message = "visible search results region accepted as asset panel"
        canvas, canvas_anchor = self._inspect_anchor(
            "canvas", CANVAS_LOCATORS, largest=True
        )
        domain_anchor = self._domain_anchor()
        anchors = [
            domain_anchor,
            editor_anchor,
            asset_anchor,
            canvas_anchor,
            search_anchor,
            results_anchor,
        ]
        modals = self.policy.scan_modals(self.page)
        ai_controls = self.policy.scan_ai_controls(self.page)
        missing_anchors = [
            anchor.name for anchor in anchors if anchor.required and not anchor.matched
        ]
        diagnostics = [f"missing required UI anchor: {name}" for name in missing_anchors]
        if modals:
            diagnostics.append("visible modal blocks calibration: " + modals[0].classification)
        if self._visible_password_input():
            diagnostics.append("authentication input is visible")

        status = CalibrationStatus.VALID
        if modals and any(modal.classification != "unknown_modal" for modal in modals):
            status = CalibrationStatus.BLOCKED_BY_POLICY
        elif diagnostics:
            status = CalibrationStatus.INVALID
        editor_loaded = not missing_anchors and not modals
        signature = {
            "viewport": [viewport.width, viewport.height],
            "anchors": [anchor.model_dump(mode="json") for anchor in anchors],
        }
        profile_version = (
            "ui-"
            + hashlib.sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        )
        profile = UiCalibrationProfile(
            profile_id=profile_id,
            ui_profile_version=profile_version,
            created_at=timestamp.isoformat(),
            url=self._redacted_url(str(self.page.url)),
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
            missing_anchors=missing_anchors,
            anchor_diagnostics=anchors,
        )
        profile_path = run_dir / "profile.json"
        profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        if self.database is not None:
            self.database.save_calibration_profile(profile, str(profile_path))
        if status != CalibrationStatus.VALID:
            raise CalibrationFailed(
                "; ".join(diagnostics) or "BioRender UI calibration was blocked",
                profile_path=str(profile_path),
                missing_anchors=missing_anchors,
                anchor_diagnostics=[anchor.model_dump(mode="json") for anchor in anchors],
            )
        return profile, profile_path

    def _domain_anchor(self) -> UiAnchorDiagnostic:
        parsed = urlparse(str(self.page.url))
        hostname = (parsed.hostname or "").casefold()
        matched = hostname == "biorender.com" or hostname.endswith(".biorender.com")
        local_fixture = (
            parsed.scheme == "file" and Path(parsed.path).name == "biorender_editor.html"
        )
        matched = matched or local_fixture
        return UiAnchorDiagnostic(
            name="biorender_domain",
            matched=matched,
            candidates=[
                LocatorMatchDiagnostic(
                    strategy="url",
                    query="https://*.biorender.com/*",
                    count=1,
                    visible_count=1 if matched else 0,
                    bbox_count=1 if matched else 0,
                    matched=matched,
                )
            ],
            message=(
                "local integration-test fixture"
                if local_fixture
                else f"observed host: {hostname or '<missing>'}"
            ),
        )

    @staticmethod
    def _redacted_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}/<redacted>"
        if parsed.scheme == "file":
            return "file:///<redacted>"
        return "<redacted>"

    def _inspect_anchor(
        self,
        name: str,
        specs: tuple[LocatorSpec, ...],
        *,
        required: bool = True,
        largest: bool = False,
    ) -> tuple[ResolvedLocator | None, UiAnchorDiagnostic]:
        diagnostics: list[LocatorMatchDiagnostic] = []
        for spec in specs:
            try:
                locator = locator_for_spec(self.page, spec)
                count = min(locator.count(), 50)
                visible_count = 0
                bbox_count = 0
                for index in range(count):
                    candidate = locator.nth(index)
                    if not candidate.is_visible():
                        continue
                    visible_count += 1
                    if candidate.bounding_box() is not None:
                        bbox_count += 1
                diagnostics.append(
                    LocatorMatchDiagnostic(
                        strategy=spec.strategy,
                        query=spec.query,
                        count=count,
                        visible_count=visible_count,
                        bbox_count=bbox_count,
                        matched=bbox_count > 0,
                    )
                )
            except Exception as error:
                diagnostics.append(
                    LocatorMatchDiagnostic(
                        strategy=spec.strategy,
                        query=spec.query,
                        count=0,
                        visible_count=0,
                        bbox_count=0,
                        matched=False,
                        error=f"{type(error).__name__}: {error}",
                    )
                )
        resolved = (
            resolve_largest_visible(self.page, specs)
            if largest
            else resolve_first_visible(self.page, specs)
        )
        return resolved, UiAnchorDiagnostic(
            name=name,
            required=required,
            matched=resolved is not None,
            selected_locator=resolved.evidence if resolved else None,
            candidates=diagnostics,
        )

    def _visible_password_input(self) -> bool:
        try:
            locator = self.page.locator("input[type='password']")
            for index in range(min(locator.count(), 50)):
                candidate = locator.nth(index)
                if candidate.is_visible() and candidate.bounding_box() is not None:
                    return True
        except Exception:
            return False
        return False

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
                if (
                    ancestor.count()
                    and ancestor.first.is_visible()
                    and ancestor.first.bounding_box()
                ):
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
