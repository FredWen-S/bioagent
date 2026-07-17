from __future__ import annotations

from pathlib import Path

from app.schemas.biorender_probe import InsertionObservation, Presence
from app.schemas.gui_action import BoundingBox, CoordinateSpace, ObservationSource


class PixelDiffInsertionObserver:
    """Verifies canvas insertion from measured pixel change near the expected target."""

    def observe(
        self,
        *,
        baseline_path: str,
        current_path: str,
        canvas_bbox: BoundingBox,
        expected_bbox: BoundingBox,
    ) -> InsertionObservation:
        evidence = [baseline_path, current_path]
        if not Path(baseline_path).exists() or not Path(current_path).exists():
            return self._unknown(
                expected_bbox, evidence, "baseline or current canvas screenshot is missing"
            )
        try:
            from PIL import Image, ImageChops
        except ImportError:
            return self._unknown(
                expected_bbox,
                evidence,
                "Pillow is required for screenshot pixel-diff observation",
            )
        try:
            before = Image.open(baseline_path).convert("RGB")
            after = Image.open(current_path).convert("RGB")
        except Exception as error:
            return self._unknown(
                expected_bbox, evidence, f"could not decode screenshot evidence: {error}"
            )
        if before.size != after.size:
            return self._unknown(
                expected_bbox,
                evidence,
                f"canvas screenshot size changed from {before.size} to {after.size}",
            )
        difference = ImageChops.difference(before, after).convert("L")
        mask = difference.point(lambda value: 255 if value > 18 else 0)
        histogram = mask.histogram()
        total_changed = sum(histogram[1:])

        local_x = expected_bbox.x - canvas_bbox.x
        local_y = expected_bbox.y - canvas_bbox.y
        margin_x = expected_bbox.width * 0.35
        margin_y = expected_bbox.height * 0.35
        crop_box = (
            max(0, int(local_x - margin_x)),
            max(0, int(local_y - margin_y)),
            min(before.width, int(local_x + expected_bbox.width + margin_x)),
            min(before.height, int(local_y + expected_bbox.height + margin_y)),
        )
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            return self._unknown(expected_bbox, evidence, "expected target crop is invalid")
        target_mask = mask.crop(crop_box)
        target_histogram = target_mask.histogram()
        target_changed = sum(target_histogram[1:])
        target_area = max(1, target_mask.width * target_mask.height)
        target_ratio = target_changed / target_area
        minimum_target_change = max(80, int(target_area * 0.012))

        if target_changed >= minimum_target_change:
            diff_bbox = target_mask.getbbox()
            observed = None
            if diff_bbox is not None:
                observed = BoundingBox(
                    x=canvas_bbox.x + crop_box[0] + diff_bbox[0],
                    y=canvas_bbox.y + crop_box[1] + diff_bbox[1],
                    width=max(1, diff_bbox[2] - diff_bbox[0]),
                    height=max(1, diff_bbox[3] - diff_bbox[1]),
                    coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
                )
            confidence = min(0.98, 0.78 + min(0.2, target_ratio * 2))
            return InsertionObservation(
                presence=Presence.PRESENT,
                confidence=confidence,
                expected_bbox=expected_bbox,
                observed_bbox=observed,
                source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
                evidence_refs=evidence,
                diagnostics=[
                    f"target_changed_pixels={target_changed}",
                    f"target_change_ratio={target_ratio:.6f}",
                    f"total_changed_pixels={total_changed}",
                ],
            )
        negligible = max(20, int(before.width * before.height * 0.00005))
        if total_changed <= negligible:
            return InsertionObservation(
                presence=Presence.ABSENT,
                confidence=0.95,
                expected_bbox=expected_bbox,
                observed_bbox=None,
                source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
                evidence_refs=evidence,
                diagnostics=[f"total_changed_pixels={total_changed}"],
            )
        return self._unknown(
            expected_bbox,
            evidence,
            "canvas changed, but the change could not be localized to the expected target",
        )

    @staticmethod
    def _unknown(
        expected_bbox: BoundingBox,
        evidence: list[str],
        message: str,
    ) -> InsertionObservation:
        return InsertionObservation(
            presence=Presence.UNKNOWN,
            confidence=0.0,
            expected_bbox=expected_bbox,
            observed_bbox=None,
            source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
            evidence_refs=evidence,
            diagnostics=[message],
        )
