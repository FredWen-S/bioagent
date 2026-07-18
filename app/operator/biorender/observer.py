from __future__ import annotations

import math
from pathlib import Path
from typing import Any

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


class GeometryObserver:
    """Verifies an observed editor-object box against a viewport-space target."""

    def observe(
        self,
        *,
        expected_bbox: BoundingBox,
        observed_bbox: BoundingBox | None,
        evidence_refs: list[str],
        position_tolerance: float = 0.22,
        size_tolerance: float = 0.25,
    ) -> InsertionObservation:
        if observed_bbox is None:
            return InsertionObservation(
                presence=Presence.UNKNOWN,
                confidence=0.0,
                expected_bbox=expected_bbox,
                source=ObservationSource.DOM,
                evidence_refs=evidence_refs,
                diagnostics=["no observable selection or editor-object bounding box"],
            )
        expected_center = (
            expected_bbox.x + expected_bbox.width / 2,
            expected_bbox.y + expected_bbox.height / 2,
        )
        observed_center = (
            observed_bbox.x + observed_bbox.width / 2,
            observed_bbox.y + observed_bbox.height / 2,
        )
        center_distance = math.dist(expected_center, observed_center)
        position_limit = max(
            8.0,
            max(expected_bbox.width, expected_bbox.height) * position_tolerance,
        )
        width_error = abs(observed_bbox.width - expected_bbox.width) / max(
            1.0, expected_bbox.width
        )
        height_error = abs(observed_bbox.height - expected_bbox.height) / max(
            1.0, expected_bbox.height
        )
        diagnostics = [
            f"center_distance={center_distance:.3f}",
            f"position_limit={position_limit:.3f}",
            f"width_error={width_error:.6f}",
            f"height_error={height_error:.6f}",
        ]
        if (
            center_distance <= position_limit
            and width_error <= size_tolerance
            and height_error <= size_tolerance
        ):
            confidence = max(
                0.76,
                0.99
                - min(0.12, center_distance / max(position_limit, 1.0) * 0.08)
                - min(0.08, max(width_error, height_error) * 0.2),
            )
            return InsertionObservation(
                presence=Presence.PRESENT,
                confidence=confidence,
                expected_bbox=expected_bbox,
                observed_bbox=observed_bbox,
                source=ObservationSource.DOM,
                evidence_refs=evidence_refs,
                diagnostics=diagnostics,
            )
        return InsertionObservation(
            presence=Presence.UNKNOWN,
            confidence=0.35,
            expected_bbox=expected_bbox,
            observed_bbox=observed_bbox,
            source=ObservationSource.DOM,
            evidence_refs=evidence_refs,
            diagnostics=[
                *diagnostics,
                "observed geometry is outside the allowed target tolerance",
            ],
        )


def _center(box: BoundingBox) -> tuple[float, float]:
    return (box.x + box.width / 2, box.y + box.height / 2)


def _intersection_area(first: BoundingBox, second: BoundingBox) -> float:
    width = max(
        0.0,
        min(first.x + first.width, second.x + second.width) - max(first.x, second.x),
    )
    height = max(
        0.0,
        min(first.y + first.height, second.y + second.height) - max(first.y, second.y),
    )
    return width * height


def _point_in_box(point: tuple[float, float], box: BoundingBox, margin: float = 0) -> bool:
    return (
        box.x - margin <= point[0] <= box.x + box.width + margin
        and box.y - margin <= point[1] <= box.y + box.height + margin
    )


def _segment_intersects_box(
    start: tuple[float, float],
    end: tuple[float, float],
    box: BoundingBox,
) -> bool:
    if _point_in_box(start, box) or _point_in_box(end, box):
        return True
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    p = (-dx, dx, -dy, dy)
    q = (
        start[0] - box.x,
        box.x + box.width - start[0],
        start[1] - box.y,
        box.y + box.height - start[1],
    )
    lower, upper = 0.0, 1.0
    for coefficient, distance in zip(p, q, strict=True):
        if coefficient == 0:
            if distance < 0:
                return False
            continue
        ratio = distance / coefficient
        if coefficient < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


class LabelAssociationObserver:
    """Checks exact text and associates duplicate labels by target proximity."""

    def observe(
        self,
        *,
        expected_text: str,
        observed_text: str | None,
        label_bbox: BoundingBox | None,
        target_element_id: str,
        target_bbox: BoundingBox | None,
        asset_boxes: dict[str, BoundingBox],
        canvas_bbox: BoundingBox,
        truncated: bool | None,
    ) -> dict[str, Any]:
        exact_text = observed_text == expected_text
        inside_canvas = bool(
            label_bbox
            and label_bbox.x >= canvas_bbox.x - 2
            and label_bbox.y >= canvas_bbox.y - 2
            and label_bbox.x + label_bbox.width <= canvas_bbox.x + canvas_bbox.width + 2
            and label_bbox.y + label_bbox.height <= canvas_bbox.y + canvas_bbox.height + 2
        )
        nearest_id = None
        target_distance = None
        if label_bbox is not None and asset_boxes:
            label_center = _center(label_bbox)
            distances = {
                element_id: math.dist(label_center, _center(box))
                for element_id, box in asset_boxes.items()
            }
            nearest_id = min(distances, key=distances.get)
            target_distance = distances.get(target_element_id)
        proximity_limit = None
        proximity_ok = False
        if target_bbox is not None and target_distance is not None:
            proximity_limit = max(90.0, target_bbox.height * 1.7)
            proximity_ok = target_distance <= proximity_limit
        associated = nearest_id == target_element_id and proximity_ok
        confidence = 0.0
        if exact_text and label_bbox is not None:
            confidence = 0.72
            if associated:
                confidence += 0.2
            if inside_canvas and truncated is False:
                confidence += 0.07
        passed = bool(
            exact_text
            and associated
            and inside_canvas
            and truncated is False
        )
        return {
            "passed": passed,
            "expected_text": expected_text,
            "observed_text": observed_text,
            "text_exact": exact_text,
            "target_element_id": target_element_id,
            "nearest_element_id": nearest_id,
            "target_distance": target_distance,
            "proximity_limit": proximity_limit,
            "association_confidence": min(0.99, confidence),
            "inside_canvas": inside_canvas,
            "truncated": truncated,
            "observation_channels": ["dom", "accessibility"],
            "ocr_used": False,
        }


class ConnectorGeometryObserver:
    """Validates a connector's observed endpoints, direction, type, and collisions."""

    TYPE_ALIASES = {
        "inhibition": "t_bar",
        "blocking_line": "t_bar",
        "t-bar": "t_bar",
    }

    def observe(
        self,
        *,
        expected_type: str,
        observed_type: str | None,
        source_id: str,
        target_id: str,
        source_bbox: BoundingBox,
        target_bbox: BoundingBox,
        observed_start: tuple[float, float] | None,
        observed_end: tuple[float, float] | None,
        unrelated_boxes: dict[str, BoundingBox],
        label_boxes: dict[str, BoundingBox],
    ) -> dict[str, Any]:
        normalized_expected = self.TYPE_ALIASES.get(expected_type, expected_type)
        normalized_observed = self.TYPE_ALIASES.get(
            observed_type or "", observed_type or ""
        )
        type_verified = normalized_observed == normalized_expected
        source_distance = None
        target_distance = None
        start_verified = False
        end_verified = False
        direction_verified = False
        if observed_start is not None and observed_end is not None:
            source_center = _center(source_bbox)
            target_center = _center(target_bbox)
            source_distance = math.dist(observed_start, source_center)
            target_distance = math.dist(observed_end, target_center)
            reverse_source = math.dist(observed_end, source_center)
            reverse_target = math.dist(observed_start, target_center)
            source_limit = max(24.0, max(source_bbox.width, source_bbox.height) * 0.7)
            target_limit = max(24.0, max(target_bbox.width, target_bbox.height) * 0.7)
            start_verified = source_distance <= source_limit
            end_verified = target_distance <= target_limit
            direction_verified = (
                source_distance + target_distance < reverse_source + reverse_target
            )
        unrelated_collisions: list[str] = []
        label_collisions: list[str] = []
        if observed_start is not None and observed_end is not None:
            unrelated_collisions = [
                element_id
                for element_id, box in unrelated_boxes.items()
                if element_id not in {source_id, target_id}
                and _segment_intersects_box(observed_start, observed_end, box)
            ]
            label_collisions = [
                element_id
                for element_id, box in label_boxes.items()
                if _segment_intersects_box(observed_start, observed_end, box)
            ]
        route_verified = observed_start is not None and observed_end is not None
        passed = bool(
            route_verified
            and type_verified
            and start_verified
            and end_verified
            and direction_verified
            and not unrelated_collisions
            and not label_collisions
        )
        return {
            "passed": passed,
            "expected_type": normalized_expected,
            "observed_type": normalized_observed or None,
            "type_verified": type_verified,
            "source_element_id": source_id,
            "target_element_id": target_id,
            "observed_start": list(observed_start) if observed_start else None,
            "observed_end": list(observed_end) if observed_end else None,
            "source_distance": source_distance,
            "target_distance": target_distance,
            "start_anchor_verified": start_verified,
            "end_anchor_verified": end_verified,
            "direction_verified": direction_verified,
            "route_verified": route_verified,
            "unrelated_element_collisions": unrelated_collisions,
            "label_collisions": label_collisions,
            "semantic_verification": "dom_route_and_type" if route_verified else "unavailable",
        }


class LayoutQualityObserver:
    """Computes obvious layout defects from observed element geometry."""

    def observe(
        self,
        *,
        canvas_bbox: BoundingBox,
        elements: list[dict[str, Any]],
        layout: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        boxes = {
            item["element_id"]: BoundingBox.model_validate(item["bbox"])
            for item in elements
        }
        by_kind: dict[str, dict[str, BoundingBox]] = {}
        for item in elements:
            by_kind.setdefault(item["kind"], {})[item["element_id"]] = boxes[
                item["element_id"]
            ]
        assets = by_kind.get("asset", {})
        labels = by_kind.get("label", {})
        connectors = by_kind.get("connector", {})
        element_records = {item["element_id"]: item for item in elements}

        out_of_bounds = [
            element_id
            for element_id, box in boxes.items()
            if box.x < canvas_bbox.x - 2
            or box.y < canvas_bbox.y - 2
            or box.x + box.width > canvas_bbox.x + canvas_bbox.width + 2
            or box.y + box.height > canvas_bbox.y + canvas_bbox.height + 2
        ]
        asset_ids = sorted(assets)
        overlaps: list[list[str]] = []
        for index, first_id in enumerate(asset_ids):
            for second_id in asset_ids[index + 1 :]:
                area = _intersection_area(assets[first_id], assets[second_id])
                minimum = min(
                    assets[first_id].width * assets[first_id].height,
                    assets[second_id].width * assets[second_id].height,
                )
                if minimum and area / minimum > 0.05:
                    overlaps.append([first_id, second_id])

        label_collisions: list[list[str]] = []
        label_association_failures: list[str] = []
        label_truncated: list[str] = []
        for label_id, label_box in labels.items():
            payload = element_records[label_id].get("payload") or {}
            target_id = payload.get("target_element_id") or payload.get("entity_id")
            verification = element_records[label_id].get("verification") or {}
            if not verification.get("association", {}).get("passed", False):
                label_association_failures.append(label_id)
            if verification.get("association", {}).get("truncated") is not False:
                label_truncated.append(label_id)
            for asset_id, asset_box in assets.items():
                if asset_id == target_id:
                    continue
                if _intersection_area(label_box, asset_box) > 2:
                    label_collisions.append([label_id, asset_id])

        connector_collisions: list[dict[str, Any]] = []
        connector_unverified: list[str] = []
        for connector_id in connectors:
            verification = element_records[connector_id].get("verification") or {}
            connector = verification.get("connector", {})
            if not connector.get("route_verified") or not connector.get("type_verified"):
                connector_unverified.append(connector_id)
            collisions = [
                *connector.get("unrelated_element_collisions", []),
                *connector.get("label_collisions", []),
            ]
            if collisions:
                connector_collisions.append(
                    {"connector_id": connector_id, "collisions": collisions}
                )

        placements = {
            item["entity_id"]: item for item in layout.get("placements", [])
        }
        rows: dict[tuple[str, float], list[BoundingBox]] = {}
        columns: dict[tuple[str, float], list[BoundingBox]] = {}
        placement_deviations: list[float] = []
        for entity_id, placement in placements.items():
            box = assets.get(entity_id)
            if box is None:
                continue
            expected_center = (
                canvas_bbox.x + float(placement["x"]) * canvas_bbox.width,
                canvas_bbox.y + float(placement["y"]) * canvas_bbox.height,
            )
            placement_deviations.append(math.dist(_center(box), expected_center))
            rows.setdefault(
                (str(placement["region_id"]), round(float(placement["y"]), 3)),
                [],
            ).append(box)
            columns.setdefault(
                (str(placement["region_id"]), round(float(placement["x"]), 3)),
                [],
            ).append(box)
        alignment_deviation = max(
            (
                max(box.y + box.height / 2 for box in row)
                - min(box.y + box.height / 2 for box in row)
                for row in rows.values()
                if len(row) >= 2
            ),
            default=0.0,
        )
        spacing_deviations: list[float] = []
        for row in rows.values():
            if len(row) >= 3:
                ordered = sorted(row, key=lambda box: box.x)
                gaps = [
                    ordered[index + 1].x
                    - (ordered[index].x + ordered[index].width)
                    for index in range(len(ordered) - 1)
                ]
                spacing_deviations.append(max(gaps) - min(gaps))
        for column in columns.values():
            if len(column) >= 3:
                ordered = sorted(column, key=lambda box: box.y)
                gaps = [
                    ordered[index + 1].y
                    - (ordered[index].y + ordered[index].height)
                    for index in range(len(ordered) - 1)
                ]
                spacing_deviations.append(max(gaps) - min(gaps))
        spacing_deviation = max(spacing_deviations, default=0.0)

        regions = {item["id"]: item for item in layout.get("regions", [])}
        entity_regions = {
            item["id"]: item.get("region_id") for item in spec.get("entities", [])
        }
        region_violations: list[str] = []
        for entity_id, box in assets.items():
            region = regions.get(entity_regions.get(entity_id))
            if region is None:
                continue
            region_box = BoundingBox(
                x=canvas_bbox.x + float(region["x"]) * canvas_bbox.width,
                y=canvas_bbox.y + float(region["y"]) * canvas_bbox.height,
                width=float(region["width"]) * canvas_bbox.width,
                height=float(region["height"]) * canvas_bbox.height,
                coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
            )
            if not _point_in_box(_center(box), region_box):
                region_violations.append(entity_id)

        z_order_issues: list[str] = []
        z_order_unknown: list[str] = []
        foreground_z = [
            (element_records[element_id].get("payload") or {}).get("z_index")
            for element_id in [*assets, *labels]
        ]
        foreground_z = [value for value in foreground_z if isinstance(value, int)]
        for connector_id in connectors:
            connector_z = (
                element_records[connector_id].get("payload") or {}
            ).get("z_index")
            if not isinstance(connector_z, int) or not foreground_z:
                z_order_unknown.append(connector_id)
            elif connector_z > min(foreground_z):
                z_order_issues.append(connector_id)

        max_placement_deviation = max(placement_deviations, default=0.0)
        passed = bool(
            not out_of_bounds
            and not overlaps
            and not label_collisions
            and not label_association_failures
            and not label_truncated
            and not connector_collisions
            and not connector_unverified
            and not region_violations
            and not z_order_issues
            and not z_order_unknown
            and alignment_deviation <= 8
            and spacing_deviation <= 8
        )
        return {
            "passed": passed,
            "overlap_count": len(overlaps),
            "overlaps": overlaps,
            "out_of_bounds_count": len(out_of_bounds),
            "out_of_bounds": out_of_bounds,
            "alignment_deviation": alignment_deviation,
            "spacing_deviation": spacing_deviation,
            "max_placement_deviation": max_placement_deviation,
            "label_collision_count": len(label_collisions),
            "label_collisions": label_collisions,
            "label_association_failure_count": len(label_association_failures),
            "label_association_failures": label_association_failures,
            "label_truncated_count": len(label_truncated),
            "connector_collision_count": len(connector_collisions),
            "connector_collisions": connector_collisions,
            "connector_unverified_count": len(connector_unverified),
            "connector_unverified": connector_unverified,
            "region_violation_count": len(region_violations),
            "region_violations": region_violations,
            "z_order_issue_count": len(z_order_issues),
            "z_order_issues": z_order_issues,
            "z_order_unknown_count": len(z_order_unknown),
            "z_order_unknown": z_order_unknown,
        }
