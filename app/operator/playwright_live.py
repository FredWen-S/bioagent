from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.operator.biorender.calibration import BioRenderUiCalibrator
from app.operator.biorender.drag import SafeAssetDrag
from app.operator.biorender.locators import (
    ALIGN_TOOL_LOCATORS,
    CANVAS_LOCATORS,
    CONNECTOR_TOOL_LOCATORS,
    DISTRIBUTE_TOOL_LOCATORS,
    GROUP_TOOL_LOCATORS,
    RESIZE_HANDLE_LOCATORS,
    ROTATE_HANDLE_LOCATORS,
    SAVE_STATUS_LOCATORS,
    SELECTED_OBJECT_LOCATORS,
    TEXT_TOOL_LOCATORS,
    bounding_box,
    is_inside,
    resolve_first_visible,
    resolve_largest_visible,
)
from app.operator.biorender.observer import (
    ConnectorGeometryObserver,
    GeometryObserver,
    LabelAssociationObserver,
    LayoutQualityObserver,
    PixelDiffInsertionObserver,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.biorender.search import RuntimeCandidate, SafeAssetSearch
from app.operator.errors import (
    AuthenticationRequired,
    CandidateIdentityUnclear,
    DragDropFailed,
    OperatorError,
    PolicyBlocked,
    SearchNoResult,
    UiLayoutChanged,
    UnsupportedLiveAction,
)
from app.operator.safety import ActionSafetyPolicy
from app.schemas.biorender_probe import InsertionObservation, Presence
from app.schemas.gui_action import (
    ActionStatus,
    ActionType,
    BoundingBox,
    CoordinateSpace,
    GuiAction,
    GuiActionResult,
    ObservationSource,
)
from app.storage.database import FigureDatabase


@dataclass(slots=True)
class LiveActionEvidence:
    status: ActionStatus
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_bbox: BoundingBox | None = None
    observation_confidence: float | None = None
    observation_source: ObservationSource | None = None
    evidence_refs: list[str] = field(default_factory=list)


class LivePlaywrightOperator:
    """BioRender GUI adapter with evidence-gated live actions.

    Every mutating action stores a canvas baseline before the mouse or keyboard
    mutation. A result becomes verified only after a DOM/accessibility observation
    and/or a localized screenshot observation supports the expected result.
    """

    mutating_actions = frozenset(
        {
            ActionType.DRAG_ASSET,
            ActionType.MOVE_ELEMENT,
            ActionType.RESIZE_ELEMENT,
            ActionType.ROTATE_ELEMENT,
            ActionType.ADD_TEXT,
            ActionType.EDIT_TEXT,
            ActionType.CONNECT,
            ActionType.GROUP_ELEMENTS,
            ActionType.ALIGN_ELEMENTS,
            ActionType.DISTRIBUTE_ELEMENTS,
        }
    )

    def __init__(
        self,
        *,
        profile_dir: Path | None = None,
        evidence_dir: Path | None = None,
        database: FigureDatabase | None = None,
        headed: bool = True,
    ) -> None:
        self.profile_dir = profile_dir or settings.session_dir / "biorender-profile"
        self.evidence_dir = evidence_dir or settings.live_figure_dir
        self.database = database
        self.headed = headed
        self.policy = ActionSafetyPolicy()
        self.biorender_policy = BioRenderPolicyGuard()
        self.pixel_observer = PixelDiffInsertionObserver()
        self.geometry_observer = GeometryObserver()
        self.label_observer = LabelAssociationObserver()
        self.connector_observer = ConnectorGeometryObserver()
        self.layout_observer = LayoutQualityObserver()
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._selected_entity_id: str | None = None
        self._selected_query: str | None = None
        self._safe_candidate: RuntimeCandidate | None = None
        self._runtime_locators: dict[tuple[str, str], Any] = {}
        self._profile_versions: dict[str, str] = {}
        self._current_figure_id: str | None = None
        self._attempt = 1

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
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
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
        self._attempt = attempt
        try:
            if action.action != ActionType.OPEN_EDITOR:
                self._ensure_editor_open(action.figure_id)
            self.biorender_policy.assert_page_safe(self._page)
            dispatch = {
                ActionType.OPEN_EDITOR: self._open_editor,
                ActionType.SEARCH_ASSET: self._search_asset,
                ActionType.SELECT_ASSET: self._select_asset,
                ActionType.DRAG_ASSET: self._drag_asset,
                ActionType.MOVE_ELEMENT: self._move_element,
                ActionType.RESIZE_ELEMENT: self._resize_element,
                ActionType.ROTATE_ELEMENT: self._rotate_element,
                ActionType.ADD_TEXT: self._add_text,
                ActionType.EDIT_TEXT: self._edit_text,
                ActionType.CONNECT: self._connect,
                ActionType.GROUP_ELEMENTS: self._group_elements,
                ActionType.ALIGN_ELEMENTS: self._align_elements,
                ActionType.DISTRIBUTE_ELEMENTS: self._distribute_elements,
                ActionType.CAPTURE_CANVAS: self._capture_canvas,
                ActionType.SAVE_PROJECT: self._observe_autosave,
            }
            handler = dispatch.get(action.action)
            if handler is None:
                raise UnsupportedLiveAction(
                    f"{action.action.value} is not enabled by the live operator"
                )
            evidence = handler(action)
            self.biorender_policy.assert_page_safe(self._page)
            screenshot_path = self._screenshot(action)
            refs = list(dict.fromkeys([*evidence.evidence_refs, str(screenshot_path)]))
            return GuiActionResult(
                action_id=action.id,
                status=evidence.status,
                attempt=attempt,
                message=evidence.message,
                screenshot_path=str(screenshot_path),
                expected_bbox=action.expected_bbox,
                observed_bbox=evidence.observed_bbox,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=refs,
                metadata={
                    "mode": "live",
                    "evidence_kind": "screenshot",
                    **evidence.metadata,
                },
            )
        except OperatorError as error:
            suffix = (
                "blocked-by-policy"
                if isinstance(error, PolicyBlocked)
                else f"failed-{error.error_type}"
            )
            try:
                path = self._screenshot(action, suffix=suffix)
                error.screenshot_path = str(path)
            except Exception:
                pass
            raise

    def reconcile(
        self,
        action: GuiAction,
        previous_result: GuiActionResult,
    ) -> GuiActionResult:
        """Reconcile a checkpoint before any replay of a mutating action."""
        self.start()
        self._ensure_editor_open(action.figure_id)
        checkpoint = previous_result.metadata.get("checkpoint")
        if not isinstance(checkpoint, dict):
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.FAILED,
                attempt=max(1, previous_result.attempt),
                error_type="checkpoint_missing",
                message=(
                    "No mutation checkpoint exists; replay is allowed only before "
                    "a GUI mutation."
                ),
                expected_bbox=previous_result.expected_bbox or action.expected_bbox,
                metadata={"safe_to_retry": True, "reconciled": True},
            )
        saved_profile = checkpoint.get("ui_profile_version")
        current_profile = self._profile_versions.get(action.figure_id)
        if saved_profile and current_profile and saved_profile != current_profile:
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.UNKNOWN,
                attempt=max(1, previous_result.attempt),
                error_type="ui_profile_changed",
                message="UI profile changed; the saved checkpoint cannot be replayed safely.",
                expected_bbox=previous_result.expected_bbox or action.expected_bbox,
                metadata={"safe_to_retry": False, "reconciled": True},
            )
        baseline_path = checkpoint.get("baseline_canvas_path")
        expected_payload = checkpoint.get("expected_bbox")
        if not baseline_path or not expected_payload:
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.UNKNOWN,
                attempt=max(1, previous_result.attempt),
                error_type="checkpoint_incomplete",
                message="Mutation checkpoint is incomplete; manual reconciliation is required.",
                expected_bbox=previous_result.expected_bbox or action.expected_bbox,
                metadata={"safe_to_retry": False, "reconciled": True},
            )
        canvas, canvas_bbox = self._canvas()
        current_path = self._canvas_screenshot(
            action, canvas, suffix="reconcile-current"
        )
        expected = BoundingBox.model_validate(expected_payload)
        persisted = self._reconcile_persisted_state(
            action,
            previous_result,
            checkpoint,
            canvas_bbox,
            current_path,
        )
        if persisted is not None:
            return persisted
        observation = self.pixel_observer.observe(
            baseline_path=str(baseline_path),
            current_path=str(current_path),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        if observation.presence == Presence.PRESENT and observation.confidence >= 0.75:
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.VERIFIED,
                attempt=max(1, previous_result.attempt),
                message=(
                    "Checkpoint reconciled: the mutation is already observable; "
                    "replay suppressed."
                ),
                screenshot_path=str(current_path),
                expected_bbox=previous_result.expected_bbox or action.expected_bbox,
                observed_bbox=observation.observed_bbox,
                observation_confidence=observation.confidence,
                observation_source=observation.source,
                evidence_refs=observation.evidence_refs,
                metadata={
                    "reconciled": True,
                    "replayed": False,
                    "safe_to_retry": False,
                    "checkpoint": checkpoint,
                },
            )
        if observation.presence == Presence.ABSENT and observation.confidence >= 0.9:
            return GuiActionResult(
                action_id=action.id,
                status=ActionStatus.FAILED,
                attempt=max(1, previous_result.attempt),
                error_type="reconciled_absent",
                message="Checkpoint reconciled: mutation is confidently absent; one retry is safe.",
                screenshot_path=str(current_path),
                expected_bbox=previous_result.expected_bbox or action.expected_bbox,
                observation_confidence=observation.confidence,
                observation_source=observation.source,
                evidence_refs=observation.evidence_refs,
                metadata={
                    "reconciled": True,
                    "replayed": False,
                    "safe_to_retry": True,
                    "checkpoint": checkpoint,
                },
            )
        return GuiActionResult(
            action_id=action.id,
            status=ActionStatus.UNKNOWN,
            attempt=max(1, previous_result.attempt),
            error_type="reconciliation_unknown",
            message=(
                "Current canvas cannot be reconciled with enough confidence; "
                "replay suppressed."
            ),
            screenshot_path=str(current_path),
            expected_bbox=previous_result.expected_bbox or action.expected_bbox,
            observed_bbox=observation.observed_bbox,
            observation_confidence=observation.confidence,
            observation_source=observation.source,
            evidence_refs=observation.evidence_refs,
            metadata={
                "reconciled": True,
                "replayed": False,
                "safe_to_retry": False,
                "checkpoint": checkpoint,
            },
        )

    def _reconcile_persisted_state(
        self,
        action: GuiAction,
        previous_result: GuiActionResult,
        checkpoint: dict[str, Any],
        canvas_bbox: BoundingBox,
        current_path: Path,
    ) -> GuiActionResult | None:
        """Prefer element facts over a non-specific screenshot difference.

        A successful GUI mutation can be interrupted after its element record is
        committed but before its action result is written. In that case replaying
        the mouse/keyboard action would duplicate content. This method accepts only
        action-specific, persisted evidence; otherwise reconciliation falls back to
        the conservative pixel observer.
        """
        if self.database is None:
            return None
        observed_bbox: BoundingBox | None = None
        confidence = 0.95
        reason: str | None = None
        element_id = str(
            action.arguments.get("element_id")
            or action.arguments.get("logical_element_id")
            or action.arguments.get("logical_label_id")
            or action.arguments.get("logical_connector_id")
            or ""
        )
        record = (
            self.database.get_editor_element(action.figure_id, element_id)
            if element_id
            else None
        )

        if action.action == ActionType.DRAG_ASSET and record is not None:
            verification = record.get("verification") or {}
            insertion = verification.get("insertion") or {}
            if record.get("kind") == "asset" and insertion.get("passed") is True:
                reason = "persisted asset insertion evidence"
        elif action.action in {
            ActionType.MOVE_ELEMENT,
            ActionType.RESIZE_ELEMENT,
            ActionType.ROTATE_ELEMENT,
        } and record is not None:
            observed_bbox = BoundingBox.model_validate(record["bbox"])
            if action.action == ActionType.ROTATE_ELEMENT:
                expected_rotation = float(action.arguments.get("target_degrees", 0.0))
                observed_rotation = float(
                    (record.get("payload") or {}).get("rotation_degrees", -10_000.0)
                )
                if abs(observed_rotation - expected_rotation) <= 2.0:
                    reason = "persisted rotation observation"
            else:
                expected_box = self._expected_viewport_bbox(
                    action,
                    canvas_bbox,
                    fallback=observed_bbox,
                )
                geometry = self.geometry_observer.observe(expected_box, observed_bbox)
                if geometry.passed:
                    reason = "persisted geometry observation"
                    confidence = geometry.confidence
        elif action.action in {ActionType.ADD_TEXT, ActionType.EDIT_TEXT}:
            if record is not None and record.get("kind") == "label":
                payload = record.get("payload") or {}
                expected_text = str(
                    action.arguments.get("expected_text")
                    or action.arguments.get("text")
                    or ""
                )
                if (
                    payload.get("observed_text") == expected_text
                    and (record.get("verification") or {})
                    .get("association", {})
                    .get("passed")
                    is True
                ):
                    reason = "persisted exact label and association evidence"
        elif action.action == ActionType.CONNECT and record is not None:
            connector = (record.get("verification") or {}).get("connector") or {}
            if record.get("kind") == "connector" and connector.get("passed") is True:
                reason = "persisted connector endpoint and type evidence"
        elif action.action == ActionType.GROUP_ELEMENTS:
            group_id = str(action.arguments.get("group_id", ""))
            members = [
                self.database.get_editor_element(action.figure_id, str(member_id))
                for member_id in action.arguments.get("element_ids", [])
            ]
            if members and all(
                member is not None
                and (member.get("payload") or {}).get("group_id") == group_id
                for member in members
            ):
                boxes = [
                    BoundingBox.model_validate(member["bbox"])
                    for member in members
                    if member is not None
                ]
                observed_bbox = self._union_bbox(boxes)
                reason = "persisted common group identity"
        elif action.action in {
            ActionType.ALIGN_ELEMENTS,
            ActionType.DISTRIBUTE_ELEMENTS,
        }:
            boxes = [
                self._element_bbox(action.figure_id, str(member_id))
                for member_id in action.arguments.get("element_ids", [])
            ]
            if action.action == ActionType.ALIGN_ELEMENTS and self._aligned(
                boxes, str(action.arguments.get("alignment", "middle"))
            ):
                reason = "persisted aligned geometry"
            elif action.action == ActionType.DISTRIBUTE_ELEMENTS and self._distributed(
                boxes, str(action.arguments.get("axis", "horizontal"))
            ):
                reason = "persisted distributed geometry"
            if reason:
                observed_bbox = self._union_bbox(boxes)
        elif action.action == ActionType.CAPTURE_CANVAS:
            layout = self.database.get_editor_element(action.figure_id, "layout_quality")
            if layout is not None and (
                layout.get("verification") or {}
            ).get("layout", {}).get("passed") is True:
                record = layout
                reason = "persisted full-canvas layout verification"
        elif action.action == ActionType.SAVE_PROJECT:
            saved = self.database.get_editor_element(action.figure_id, "document_save")
            save_verification = (
                (saved.get("verification") or {}).get("save") if saved else None
            )
            if saved is not None and save_verification and save_verification.get("passed"):
                record = saved
                reason = "persisted visible autosave confirmation"

        if reason is None:
            return None
        if observed_bbox is None and record is not None:
            observed_bbox = BoundingBox.model_validate(record["bbox"])
        evidence = [str(current_path)]
        if record is not None:
            evidence.extend(str(value) for value in record.get("evidence_refs") or [])
        return GuiActionResult(
            action_id=action.id,
            status=ActionStatus.VERIFIED,
            attempt=max(1, previous_result.attempt),
            message=f"Checkpoint reconciled from {reason}; replay suppressed.",
            screenshot_path=str(current_path),
            expected_bbox=previous_result.expected_bbox or action.expected_bbox,
            observed_bbox=observed_bbox,
            observation_confidence=confidence,
            observation_source=ObservationSource.DOM,
            evidence_refs=list(dict.fromkeys(evidence)),
            metadata={
                "reconciled": True,
                "replayed": False,
                "safe_to_retry": False,
                "reconciliation_source": "persisted_element_state",
                "checkpoint": checkpoint,
            },
        )

    def _open_editor(self, action: GuiAction) -> LiveActionEvidence:
        page = self._page
        page.goto(action.arguments["url"], wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        if self._authentication_visible():
            raise AuthenticationRequired(
                "BioRender requires manual login. Run browser-login, authenticate in the "
                "visible window, then resume. The agent never enters credentials."
            )
        if self._canvas_locator() is None:
            raise UiLayoutChanged(
                "No BioRender canvas was detected. Open a disposable blank Figure manually "
                "and pass its complete editor URL."
            )
        profile, profile_path = BioRenderUiCalibrator(
            page,
            database=self.database,
            policy=self.biorender_policy,
        ).calibrate()
        self._profile_versions[action.figure_id] = profile.ui_profile_version
        self._current_figure_id = action.figure_id
        canvas, canvas_bbox = self._canvas()
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message="BioRender editor and canvas were observed.",
            observed_bbox=canvas_bbox,
            observation_confidence=0.98,
            observation_source=ObservationSource.DOM,
            evidence_refs=[profile.screenshot_path],
            metadata={
                "url": page.url,
                "title": page.title(),
                "profile_id": profile.profile_id,
                "ui_profile_version": profile.ui_profile_version,
                "profile_path": str(profile_path),
                "canvas_locator_observed": canvas is not None,
            },
        )

    def _search_asset(self, action: GuiAction) -> LiveActionEvidence:
        queries = [action.arguments["query"], *action.arguments.get("fallback_queries", [])]
        queries = queries[: int(action.arguments.get("max_queries", 5))]
        failures: list[str] = []
        for query in queries:
            try:
                outcome = SafeAssetSearch(
                    self._page,
                    evidence_dir=self.evidence_dir,
                    policy=self.biorender_policy,
                ).search(query, f"{action.figure_id}/{action.id}", max_attempts=2)
                self._safe_candidate = outcome.selected
                self._selected_entity_id = str(action.arguments["entity_id"])
                self._selected_query = query
                return LiveActionEvidence(
                    status=ActionStatus.VERIFIED,
                    message=f"Ordinary asset search was observed for query {query!r}.",
                    observed_bbox=outcome.selected.record.bbox,
                    observation_confidence=0.95,
                    observation_source=ObservationSource.DOM,
                    evidence_refs=[
                        outcome.screenshot_path,
                        outcome.results_screenshot_path,
                    ],
                    metadata={
                        "selected_query": query,
                        "candidate_count": len(outcome.candidates),
                        "selected_candidate": outcome.selected.record.model_dump(mode="json"),
                        "query_failures": failures,
                    },
                )
            except (SearchNoResult, CandidateIdentityUnclear, UiLayoutChanged) as error:
                failures.append(f"{query}: {error}")
        raise SearchNoResult(
            f"No proven ordinary BioRender asset for queries {queries}: {failures}"
        )

    def _select_asset(self, action: GuiAction) -> LiveActionEvidence:
        if self._safe_candidate is None:
            raise SearchNoResult("No policy-verified ordinary asset candidate is selected")
        self.biorender_policy.assert_target_allowed(
            self._safe_candidate.locator, candidate_context=True
        )
        self._safe_candidate.locator.scroll_into_view_if_needed()
        try:
            self._safe_candidate.locator.hover()
        except Exception:
            pass
        observed = bounding_box(self._safe_candidate.locator)
        if observed is None:
            raise CandidateIdentityUnclear("Selected asset lost its observable geometry")
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message="Ordinary draggable asset candidate was selected without activating it.",
            observed_bbox=observed,
            observation_confidence=0.96,
            observation_source=ObservationSource.DOM,
            metadata={
                "candidate_index": self._safe_candidate.record.ordinal,
                "candidate_id": self._safe_candidate.record.candidate_id,
                "entity_id": action.arguments["entity_id"],
            },
        )

    def _drag_asset(self, action: GuiAction) -> LiveActionEvidence:
        if self._safe_candidate is None:
            raise DragDropFailed("No policy-verified ordinary asset candidate is selected")
        before_count = self._observable_canvas_object_count("asset")
        drag = SafeAssetDrag(
            self._page,
            evidence_dir=self.evidence_dir,
            policy=self.biorender_policy,
        )
        prepared = drag.prepare(
            self._safe_candidate,
            action.figure_id,
            target_x=float(action.arguments["target_x"]),
            target_y=float(action.arguments["target_y"]),
            target_width=float(action.arguments.get("target_width", 0.12)),
            evidence_stem=action.id,
        )
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=prepared.baseline_canvas_path,
            expected_bbox=prepared.expected_bbox,
            payload={
                "entity_id": action.arguments["entity_id"],
                "candidate_id": self._safe_candidate.record.candidate_id,
            },
        )
        drag.execute(prepared)
        after_count = self._observable_canvas_object_count("asset")
        if before_count is None and after_count is not None and self.database is not None:
            before_count = len(
                self.database.list_editor_elements(action.figure_id, kind="asset")
            )
        selected = self._selected_or_last_object()
        observed = bounding_box(selected) if selected is not None else None
        if selected is not None:
            self._runtime_locators[
                (action.figure_id, str(action.arguments["entity_id"]))
            ] = selected
        self._clear_selection(prepared.canvas_locator, prepared.canvas_bbox)
        after_path = self._canvas_screenshot(
            action, prepared.canvas_locator, suffix="after-drag-clean"
        )
        pixel = self.pixel_observer.observe(
            baseline_path=prepared.baseline_canvas_path,
            current_path=str(after_path),
            canvas_bbox=prepared.canvas_bbox,
            expected_bbox=prepared.expected_bbox,
        )
        observed = observed or pixel.observed_bbox
        evidence = self._mutation_evidence(
            action,
            pixel=pixel,
            expected_bbox=prepared.expected_bbox,
            observed_bbox=observed,
            require_geometry=False,
            evidence_refs=[prepared.baseline_canvas_path, str(after_path)],
            checkpoint=checkpoint,
            success_message="Asset insertion is observable in the target canvas region.",
        )
        count_delta = (
            after_count - before_count
            if before_count is not None and after_count is not None
            else None
        )
        count_verified = count_delta == 1 if count_delta is not None else None
        evidence.metadata.update(
            {
                "canvas_asset_count_before": before_count,
                "canvas_asset_count_after": after_count,
                "canvas_asset_count_delta": count_delta,
                "count_verification": count_verified,
            }
        )
        if count_verified is False:
            evidence.status = ActionStatus.UNKNOWN
            evidence.message = (
                "Asset insertion changed the observable canvas asset count by an "
                "unexpected amount."
            )
        if evidence.status == ActionStatus.VERIFIED and evidence.observed_bbox is not None:
            candidate = self._safe_candidate.record
            self._persist_element(
                action.figure_id,
                str(action.arguments["entity_id"]),
                "asset",
                evidence.observed_bbox,
                {
                    "logical_element_id": action.arguments["entity_id"],
                    "figure_element_id": self._weak_figure_element_id(
                        action.figure_id,
                        str(action.arguments["entity_id"]),
                        candidate.candidate_id,
                    ),
                    "candidate_id": candidate.candidate_id,
                    "accessible_name": candidate.accessible_name,
                    "dom_fingerprint": candidate.dom_fingerprint,
                    "thumbnail_fingerprint": candidate.thumbnail_fingerprint,
                    "search_query": self._selected_query,
                    "candidate_text": candidate.text,
                    "identity_strength": "weak",
                },
                expected_bbox=prepared.expected_bbox,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=evidence.evidence_refs,
                verification={
                    "insertion": {
                        "passed": True,
                        "count_verification": count_verified,
                        "observed_bbox_source": (
                            evidence.observation_source.value
                            if evidence.observation_source
                            else None
                        ),
                    }
                },
            )
        self._safe_candidate = None
        self._selected_entity_id = None
        self._selected_query = None
        return evidence

    def _move_element(self, action: GuiAction) -> LiveActionEvidence:
        element_id = str(action.arguments["element_id"])
        canvas, canvas_bbox = self._canvas()
        existing_record = self._element_record(action.figure_id, element_id)
        group_id = (
            (existing_record or {}).get("payload", {}).get("group_id")
            if existing_record
            else None
        )
        group_records = (
            [
                item
                for item in self.database.list_editor_elements(action.figure_id)
                if (item.get("payload") or {}).get("group_id") == group_id
            ]
            if self.database is not None and group_id
            else []
        )
        group_before = {
            item["element_id"]: self._element_bbox(
                action.figure_id,
                item["element_id"],
            )
            for item in group_records
        }
        current_before = self._element_bbox(action.figure_id, element_id)
        planned = self._expected_viewport_bbox(action, canvas_bbox)
        expected = BoundingBox(
            x=planned.x + planned.width / 2 - current_before.width / 2,
            y=planned.y + planned.height / 2 - current_before.height / 2,
            width=current_before.width,
            height=current_before.height,
            coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
        )
        baseline = self._canvas_screenshot(action, canvas, suffix="before-move")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_id": element_id, "operation": "move"},
        )
        locator, current = self._select_element(action.figure_id, element_id)
        source_x = current.x + current.width / 2
        source_y = current.y + current.height / 2
        target_x = expected.x + expected.width / 2
        target_y = expected.y + expected.height / 2
        self._page.mouse.move(source_x, source_y)
        self._page.mouse.down()
        self._page.mouse.move(target_x, target_y, steps=20)
        self._page.mouse.up()
        self._page.wait_for_timeout(700)
        observed = bounding_box(locator) if locator is not None else None
        if observed is None:
            selected = self._selected_or_last_object()
            observed = bounding_box(selected) if selected is not None else None
            if selected is not None:
                self._runtime_locators[(action.figure_id, element_id)] = selected
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-move")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        evidence = self._mutation_evidence(
            action,
            pixel=pixel,
            expected_bbox=expected,
            observed_bbox=observed,
            require_geometry=True,
            evidence_refs=[str(baseline), str(after)],
            checkpoint=checkpoint,
            success_message="Element position change was observed.",
        )
        if evidence.status == ActionStatus.VERIFIED and evidence.observed_bbox is not None:
            kind = str(action.arguments.get("element_kind", "asset"))
            verification: dict[str, Any] = {
                "position": evidence.metadata.get("geometry_observation", {})
            }
            if len(group_before) >= 2:
                main_before = group_before[element_id]
                expected_delta = (
                    evidence.observed_bbox.x - main_before.x,
                    evidence.observed_bbox.y - main_before.y,
                )
                group_after: dict[str, BoundingBox] = {}
                member_deltas: dict[str, list[float]] = {}
                for member_id, before_box in group_before.items():
                    member_locator = self._direct_element_locator(
                        action.figure_id,
                        member_id,
                    )
                    after_box = (
                        bounding_box(member_locator)
                        if member_locator is not None
                        else None
                    )
                    if after_box is None:
                        continue
                    group_after[member_id] = after_box
                    member_deltas[member_id] = [
                        after_box.x - before_box.x,
                        after_box.y - before_box.y,
                    ]
                group_move_verified = len(group_after) == len(group_before) and all(
                    abs(delta[0] - expected_delta[0]) <= 5
                    and abs(delta[1] - expected_delta[1]) <= 5
                    for delta in member_deltas.values()
                )
                evidence.metadata["group_move"] = {
                    "group_id": group_id,
                    "passed": group_move_verified,
                    "expected_delta": list(expected_delta),
                    "member_deltas": member_deltas,
                }
                verification["group_move"] = evidence.metadata["group_move"]
                if not group_move_verified:
                    evidence.status = ActionStatus.UNKNOWN
                    evidence.message = (
                        "Grouped element moved, but all group members did not share "
                        "the same observed displacement."
                    )
                    return evidence
                for member_id, after_box in group_after.items():
                    if member_id == element_id:
                        continue
                    member_record = self._element_record(
                        action.figure_id,
                        member_id,
                    )
                    if member_record is None:
                        continue
                    self._persist_element(
                        action.figure_id,
                        member_id,
                        str(member_record["kind"]),
                        after_box,
                        verification={"group_move": evidence.metadata["group_move"]},
                        observation_confidence=evidence.observation_confidence,
                        observation_source=evidence.observation_source,
                        evidence_refs=evidence.evidence_refs,
                    )
            payload: dict[str, Any] = {}
            if kind == "label":
                association = self._verify_existing_label(
                    action.figure_id,
                    element_id,
                    locator,
                    evidence.observed_bbox,
                    canvas_bbox,
                )
                evidence.metadata["label_association"] = association
                verification["association"] = association
                payload.update(
                    {
                        "observed_text": association["observed_text"],
                        "association_confidence": association[
                            "association_confidence"
                        ],
                    }
                )
                if not association["passed"]:
                    evidence.status = ActionStatus.UNKNOWN
                    evidence.message = (
                        "Label moved, but its exact text and target association "
                        "could not be verified."
                    )
                    return evidence
            self._persist_element(
                action.figure_id,
                element_id,
                kind,
                evidence.observed_bbox,
                payload,
                expected_bbox=expected,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=evidence.evidence_refs,
                verification=verification,
            )
        return evidence

    def _resize_element(self, action: GuiAction) -> LiveActionEvidence:
        element_id = str(action.arguments["element_id"])
        canvas, canvas_bbox = self._canvas()
        expected = self._expected_viewport_bbox(action, canvas_bbox)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-resize")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_id": element_id, "operation": "resize"},
        )
        locator, current = self._select_element(action.figure_id, element_id)
        handle = resolve_first_visible(self._page, RESIZE_HANDLE_LOCATORS)
        if handle is None:
            # Some editors replace or clear the transform selection after a
            # preceding drag. Re-identify the nearest visible canvas object from
            # verified geometry and select it through the GUI before declaring
            # UI drift.
            refreshed = self._closest_canvas_element(current)
            if refreshed is not None:
                self.biorender_policy.assert_target_allowed(refreshed)
                refreshed.click(timeout=5_000)
                locator = refreshed.element_handle() or refreshed
                self._runtime_locators[(action.figure_id, element_id)] = locator
                current = bounding_box(locator) or current
            else:
                self._page.mouse.click(
                    current.x + min(12.0, current.width / 4),
                    current.y + min(12.0, current.height / 4),
                )
            self._page.wait_for_timeout(250)
            handle = resolve_first_visible(self._page, RESIZE_HANDLE_LOCATORS)
        if handle is None:
            selection_debug = self._page.locator(
                "[data-selected='true']"
            ).evaluate_all(
                "els => els.map(el => ({testid: el.dataset.testid, "
                "text: el.dataset.labelText || el.dataset.concept || '', "
                "box: el.getBoundingClientRect().toJSON()}))"
            )
            raise UiLayoutChanged(
                "No observable ordinary resize handle appeared after selecting the "
                f"element; current={current.model_dump(mode='json')}; "
                f"selected={selection_debug}"
            )
        self.biorender_policy.assert_target_allowed(handle.locator)
        handle_box = bounding_box(handle.locator)
        if handle_box is None:
            raise UiLayoutChanged("Resize handle has no observable geometry")
        start_x = handle_box.x + handle_box.width / 2
        start_y = handle_box.y + handle_box.height / 2
        delta_x = expected.x + expected.width - (current.x + current.width)
        delta_y = expected.y + expected.height - (current.y + current.height)
        self._page.mouse.move(start_x, start_y)
        self._page.mouse.down()
        self._page.mouse.move(start_x + delta_x, start_y + delta_y, steps=18)
        self._page.mouse.up()
        self._page.wait_for_timeout(700)
        observed = bounding_box(locator) if locator is not None else None
        if observed is None:
            selected = self._selected_or_last_object()
            observed = bounding_box(selected) if selected is not None else None
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-resize")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        evidence = self._mutation_evidence(
            action,
            pixel=pixel,
            expected_bbox=expected,
            observed_bbox=observed,
            require_geometry=True,
            allow_geometry_only=(
                observed is not None
                and (
                    abs(observed.width - current.width) >= 3
                    or abs(observed.height - current.height) >= 3
                )
            ),
            evidence_refs=[str(baseline), str(after)],
            checkpoint=checkpoint,
            success_message="Element size change was observed.",
        )
        if evidence.status == ActionStatus.VERIFIED and evidence.observed_bbox is not None:
            kind = str(action.arguments.get("element_kind", "asset"))
            verification = {
                "size": evidence.metadata.get("geometry_observation", {})
            }
            payload: dict[str, Any] = {}
            if kind == "label":
                association = self._verify_existing_label(
                    action.figure_id,
                    element_id,
                    locator,
                    evidence.observed_bbox,
                    canvas_bbox,
                )
                evidence.metadata["label_association"] = association
                verification["association"] = association
                payload.update(
                    {
                        "observed_text": association["observed_text"],
                        "association_confidence": association[
                            "association_confidence"
                        ],
                    }
                )
                if not association["passed"]:
                    evidence.status = ActionStatus.UNKNOWN
                    evidence.message = (
                        "Label resized, but its exact text and target association "
                        "could not be verified."
                    )
                    return evidence
            self._persist_element(
                action.figure_id,
                element_id,
                kind,
                evidence.observed_bbox,
                payload,
                expected_bbox=expected,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=evidence.evidence_refs,
                verification=verification,
            )
        return evidence

    def _rotate_element(self, action: GuiAction) -> LiveActionEvidence:
        element_id = str(action.arguments["element_id"])
        target_degrees = float(action.arguments.get("target_degrees", 0))
        canvas, canvas_bbox = self._canvas()
        expected = self._expected_viewport_bbox(action, canvas_bbox)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-rotate")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={
                "element_id": element_id,
                "operation": "rotate",
                "target_degrees": target_degrees,
            },
        )
        locator, current = self._select_element(action.figure_id, element_id)
        handle = resolve_first_visible(self._page, ROTATE_HANDLE_LOCATORS)
        if handle is None:
            raise UnsupportedLiveAction(
                "The current BioRender UI exposes no observable ordinary rotate handle"
            )
        self.biorender_policy.assert_target_allowed(handle.locator)
        handle_box = bounding_box(handle.locator)
        if handle_box is None:
            raise UiLayoutChanged("Rotate handle has no observable geometry")
        center_x = current.x + current.width / 2
        center_y = current.y + current.height / 2
        radius = max(30.0, current.height / 2 + 24.0)
        radians = math.radians(target_degrees - 90.0)
        target_x = center_x + radius * math.cos(radians)
        target_y = center_y + radius * math.sin(radians)
        self._page.mouse.move(
            handle_box.x + handle_box.width / 2,
            handle_box.y + handle_box.height / 2,
        )
        self._page.mouse.down()
        self._page.mouse.move(target_x, target_y, steps=24)
        self._page.mouse.up()
        self._page.wait_for_timeout(700)
        observed_degrees = None
        if locator is not None:
            try:
                observed_degrees = float(locator.get_attribute("data-rotation") or "")
            except (TypeError, ValueError):
                observed_degrees = None
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-rotate")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        if (
            observed_degrees is None
            or abs((observed_degrees - target_degrees + 180) % 360 - 180) > 6
            or pixel.presence != Presence.PRESENT
        ):
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message="Rotation was attempted, but its angle could not be verified.",
                observed_bbox=bounding_box(locator) if locator is not None else None,
                observation_confidence=0.35,
                observation_source=ObservationSource.DOM,
                evidence_refs=[str(baseline), str(after)],
                metadata={"checkpoint": checkpoint, "target_degrees": target_degrees},
            )
        observed = bounding_box(locator)
        if observed is not None:
            kind = str(action.arguments.get("element_kind", "asset"))
            self._persist_element(
                action.figure_id,
                element_id,
                kind,
                observed,
                {"rotation_degrees": observed_degrees},
                expected_bbox=expected,
                observation_confidence=min(0.95, pixel.confidence),
                observation_source=ObservationSource.DOM,
                evidence_refs=[str(baseline), str(after)],
                verification={
                    "rotation": {
                        "passed": True,
                        "target_degrees": target_degrees,
                        "observed_degrees": observed_degrees,
                    }
                },
            )
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message="Element rotation angle and canvas change were observed.",
            observed_bbox=observed,
            observation_confidence=min(0.95, pixel.confidence),
            observation_source=ObservationSource.DOM,
            evidence_refs=[str(baseline), str(after)],
            metadata={
                "checkpoint": checkpoint,
                "target_degrees": target_degrees,
                "observed_degrees": observed_degrees,
            },
        )

    def _add_text(self, action: GuiAction) -> LiveActionEvidence:
        text = str(action.arguments["text"])
        element_id = str(
            action.arguments.get("element_id")
            or f"label_{action.arguments['entity_id']}"
        )
        canvas, canvas_bbox = self._canvas()
        before_count = self._observable_canvas_object_count("label")
        expected = self._expected_viewport_bbox(action, canvas_bbox)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-label")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_id": element_id, "operation": "add_text", "text": text},
        )
        tool = resolve_first_visible(self._page, TEXT_TOOL_LOCATORS)
        if tool is None:
            raise UiLayoutChanged("No observable ordinary Text tool was found")
        self.biorender_policy.assert_target_allowed(tool.locator)
        tool.locator.click(timeout=5_000)
        self._page.mouse.click(
            expected.x,
            expected.y,
        )
        self._page.wait_for_timeout(250)
        editor = self._active_text_editor()
        if editor is None:
            raise UiLayoutChanged(
                "Text tool did not expose an observable editable field; label cannot be verified"
            )
        editor.fill(text)
        entered = self._editor_value(editor)
        if entered != text:
            raise UiLayoutChanged("Editable label value does not match the requested text")
        editor.press("Escape")
        self._page.wait_for_timeout(500)
        after_count = self._observable_canvas_object_count("label")
        if before_count is None and after_count is not None and self.database is not None:
            before_count = len(
                self.database.list_editor_elements(action.figure_id, kind="label")
            )
        selected = self._selected_or_last_object()
        observed = bounding_box(selected) if selected is not None else None
        observed_text, truncated = self._observable_label_text(selected)
        if selected is not None:
            self._runtime_locators[(action.figure_id, element_id)] = selected
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-label")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        evidence = self._mutation_evidence(
            action,
            pixel=pixel,
            expected_bbox=expected,
            observed_bbox=observed,
            require_geometry=True,
            evidence_refs=[str(baseline), str(after)],
            checkpoint=checkpoint,
            success_message=f"Label {text!r} was entered and remains observable on the canvas.",
            size_tolerance=0.9,
        )
        evidence.metadata["entered_text"] = entered
        target_element_id = str(
            action.arguments.get("target_element_id")
            or action.arguments.get("entity_id")
        )
        target_record = self._element_record(action.figure_id, target_element_id)
        asset_boxes = self._element_boxes(action.figure_id, "asset")
        association = self.label_observer.observe(
            expected_text=text,
            observed_text=observed_text,
            label_bbox=observed,
            target_element_id=target_element_id,
            target_bbox=(
                BoundingBox.model_validate(target_record["bbox"])
                if target_record is not None
                else None
            ),
            asset_boxes=asset_boxes,
            canvas_bbox=canvas_bbox,
            truncated=truncated,
        )
        count_delta = (
            after_count - before_count
            if before_count is not None and after_count is not None
            else None
        )
        count_verified = count_delta == 1 if count_delta is not None else None
        evidence.metadata.update(
            {
                "observed_text": observed_text,
                "label_association": association,
                "canvas_label_count_before": before_count,
                "canvas_label_count_after": after_count,
                "canvas_label_count_delta": count_delta,
                "count_verification": count_verified,
            }
        )
        if not association["passed"] or count_verified is False:
            evidence.status = ActionStatus.UNKNOWN
            evidence.message = (
                "Label was entered, but exact text, target association, truncation, "
                "or count verification failed."
            )
        if evidence.status == ActionStatus.VERIFIED and evidence.observed_bbox is not None:
            figure_element_id = self._weak_figure_element_id(
                action.figure_id,
                element_id,
                f"{target_element_id}|{text}",
            )
            self._persist_element(
                action.figure_id,
                element_id,
                "label",
                evidence.observed_bbox,
                {
                    "logical_label_id": element_id,
                    "figure_element_id": figure_element_id,
                    "target_element_id": target_element_id,
                    "expected_text": text,
                    "observed_text": observed_text,
                    "association_confidence": association[
                        "association_confidence"
                    ],
                },
                expected_bbox=expected,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=evidence.evidence_refs,
                verification={"association": association},
            )
        return evidence

    def _edit_text(self, action: GuiAction) -> LiveActionEvidence:
        element_id = str(action.arguments["element_id"])
        text = str(action.arguments["text"])
        canvas, canvas_bbox = self._canvas()
        locator, current = self._select_element(action.figure_id, element_id)
        expected = self._expected_viewport_bbox(action, canvas_bbox, fallback=current)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-edit-label")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_id": element_id, "operation": "edit_text", "text": text},
        )
        if locator is not None:
            locator.dblclick()
        else:
            self._page.mouse.dblclick(
                current.x + current.width / 2,
                current.y + current.height / 2,
            )
        editor = self._active_text_editor()
        if editor is None:
            raise UiLayoutChanged("Selected label did not expose an editable field")
        editor.fill(text)
        entered = self._editor_value(editor)
        editor.press("Escape")
        self._page.wait_for_timeout(500)
        observed = bounding_box(locator) if locator is not None else None
        observed_text, truncated = self._observable_label_text(locator)
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-edit-label")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        evidence = self._mutation_evidence(
            action,
            pixel=pixel,
            expected_bbox=expected,
            observed_bbox=observed,
            require_geometry=True,
            evidence_refs=[str(baseline), str(after)],
            checkpoint=checkpoint,
            success_message=f"Edited label {text!r} is observable.",
            size_tolerance=0.9,
        )
        evidence.metadata["entered_text"] = entered
        record = self._element_record(action.figure_id, element_id)
        target_element_id = str(
            action.arguments.get("target_element_id")
            or (record or {}).get("payload", {}).get("target_element_id")
            or ""
        )
        target_record = (
            self._element_record(action.figure_id, target_element_id)
            if target_element_id
            else None
        )
        association = self.label_observer.observe(
            expected_text=text,
            observed_text=observed_text,
            label_bbox=observed,
            target_element_id=target_element_id,
            target_bbox=(
                BoundingBox.model_validate(target_record["bbox"])
                if target_record is not None
                else None
            ),
            asset_boxes=self._element_boxes(action.figure_id, "asset"),
            canvas_bbox=canvas_bbox,
            truncated=truncated,
        )
        evidence.metadata.update(
            {"observed_text": observed_text, "label_association": association}
        )
        if entered != text or not association["passed"]:
            evidence.status = ActionStatus.UNKNOWN
            evidence.message = (
                "Edited label text or target association could not be verified."
            )
        if evidence.status == ActionStatus.VERIFIED and evidence.observed_bbox is not None:
            self._persist_element(
                action.figure_id,
                element_id,
                "label",
                evidence.observed_bbox,
                {
                    "expected_text": text,
                    "observed_text": observed_text,
                    "target_element_id": target_element_id,
                    "association_confidence": association[
                        "association_confidence"
                    ],
                },
                expected_bbox=expected,
                observation_confidence=evidence.observation_confidence,
                observation_source=evidence.observation_source,
                evidence_refs=evidence.evidence_refs,
                verification={"association": association},
            )
        return evidence

    def _connect(self, action: GuiAction) -> LiveActionEvidence:
        relation_id = str(action.arguments["relation_id"])
        connector_type = str(action.arguments.get("connector_type", "line"))
        specs = CONNECTOR_TOOL_LOCATORS.get(connector_type)
        if specs is None:
            raise UnsupportedLiveAction(f"Unsupported ordinary connector type: {connector_type}")
        source_id = str(action.arguments["source_entity_id"])
        target_id = str(action.arguments["target_entity_id"])
        source = self._element_bbox(action.figure_id, source_id)
        target = self._element_bbox(action.figure_id, target_id)
        requested_start, requested_end = self._connector_anchor_points(source, target)
        canvas, canvas_bbox = self._canvas()
        before_count = self._observable_canvas_object_count("connector")
        expected = self._expected_viewport_bbox(action, canvas_bbox)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-connector")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={
                "relation_id": relation_id,
                "source_entity_id": source_id,
                "target_entity_id": target_id,
                "connector_type": connector_type,
            },
        )
        tool = resolve_first_visible(self._page, specs)
        if tool is None:
            raise UiLayoutChanged(
                f"No observable ordinary connector tool was found for {connector_type!r}"
            )
        self.biorender_policy.assert_target_allowed(tool.locator)
        tool.locator.click(timeout=5_000)
        self._page.mouse.move(*requested_start)
        self._page.mouse.down()
        self._page.mouse.move(*requested_end, steps=24)
        self._page.mouse.up()
        self._page.wait_for_timeout(700)
        after_count = self._observable_canvas_object_count("connector")
        if before_count is None and after_count is not None and self.database is not None:
            before_count = len(
                self.database.list_editor_elements(
                    action.figure_id,
                    kind="connector",
                )
            )
        selected = self._selected_or_last_connector()
        observed = bounding_box(selected) if selected is not None else None
        observed_type, observed_start, observed_end = self._observable_connector_route(
            selected
        )
        if selected is not None:
            self._runtime_locators[(action.figure_id, relation_id)] = selected
        self._clear_selection(canvas, canvas_bbox)
        after = self._canvas_screenshot(action, canvas, suffix="after-connector")
        pixel = self.pixel_observer.observe(
            baseline_path=str(baseline),
            current_path=str(after),
            canvas_bbox=canvas_bbox,
            expected_bbox=expected,
        )
        verified_bbox = observed or pixel.observed_bbox
        geometry_ok = (
            verified_bbox is not None
            and is_inside(verified_bbox, expected, tolerance=18)
        )
        connector_verification = self.connector_observer.observe(
            expected_type=connector_type,
            observed_type=observed_type,
            source_id=source_id,
            target_id=target_id,
            source_bbox=source,
            target_bbox=target,
            observed_start=observed_start,
            observed_end=observed_end,
            unrelated_boxes=self._element_boxes(action.figure_id, "asset"),
            label_boxes=self._element_boxes(action.figure_id, "label"),
        )
        count_delta = (
            after_count - before_count
            if before_count is not None and after_count is not None
            else None
        )
        count_verified = count_delta == 1 if count_delta is not None else None
        screenshot_verified = (
            pixel.presence == Presence.PRESENT and pixel.confidence >= 0.75
        )
        dom_verified = bool(
            observed is not None
            and connector_verification["passed"]
            and count_verified is not False
        )
        if (
            not (screenshot_verified or dom_verified)
            or not geometry_ok
            or not connector_verification["passed"]
            or count_verified is False
        ):
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message="Connector action executed, but type/geometry could not be verified.",
                observed_bbox=verified_bbox,
                observation_confidence=min(pixel.confidence, 0.45),
                observation_source=(
                    ObservationSource.DOM if observed is not None else pixel.source
                ),
                evidence_refs=[str(baseline), str(after)],
                metadata={
                    "checkpoint": checkpoint,
                    "connector_type": connector_type,
                    "geometry_inside_expected_corridor": geometry_ok,
                    "connector_verification": connector_verification,
                    "canvas_connector_count_before": before_count,
                    "canvas_connector_count_after": after_count,
                    "canvas_connector_count_delta": count_delta,
                    "count_verification": count_verified,
                },
            )
        assert verified_bbox is not None
        observation_confidence = (
            max(0.93, min(0.96, pixel.confidence))
            if dom_verified
            else min(0.96, pixel.confidence)
        )
        self._persist_element(
            action.figure_id,
            relation_id,
            "connector",
            verified_bbox,
            {
                "connector_type": connector_type,
                "source_entity_id": source_id,
                "target_entity_id": target_id,
                "semantic_role": action.arguments.get("semantic_role"),
                "direction": action.arguments.get("direction", "source_to_target"),
                "start_anchor": action.arguments.get("start_anchor", "center"),
                "end_anchor": action.arguments.get("end_anchor", "center"),
                "observed_route": {
                    "start": list(observed_start) if observed_start else None,
                    "end": list(observed_end) if observed_end else None,
                },
                "observed_connector_type": observed_type,
                "figure_element_id": self._weak_figure_element_id(
                    action.figure_id,
                    relation_id,
                    f"{source_id}|{target_id}|{connector_type}",
                ),
                "label": action.arguments.get("label"),
            },
            expected_bbox=expected,
            observation_confidence=observation_confidence,
            observation_source=(
                ObservationSource.DOM if observed is not None else pixel.source
            ),
            evidence_refs=[str(baseline), str(after)],
            verification={"connector": connector_verification},
        )
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message=f"Ordinary {connector_type} connector is observable between both elements.",
            observed_bbox=verified_bbox,
            observation_confidence=observation_confidence,
            observation_source=(
                ObservationSource.DOM if observed is not None else pixel.source
            ),
            evidence_refs=[str(baseline), str(after)],
            metadata={
                "checkpoint": checkpoint,
                "connector_type": connector_type,
                "source_entity_id": source_id,
                "target_entity_id": target_id,
                "connector_verification": connector_verification,
                "canvas_connector_count_before": before_count,
                "canvas_connector_count_after": after_count,
                "canvas_connector_count_delta": count_delta,
                "count_verification": count_verified,
                "screenshot_verification": screenshot_verified,
                "dom_route_verification": dom_verified,
            },
        )

    def _group_elements(self, action: GuiAction) -> LiveActionEvidence:
        element_ids = [str(value) for value in action.arguments["element_ids"]]
        canvas, canvas_bbox = self._canvas()
        expected = self._union_bbox(
            [self._element_bbox(action.figure_id, value) for value in element_ids]
        )
        baseline = self._canvas_screenshot(action, canvas, suffix="before-group")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_ids": element_ids, "operation": "group"},
        )
        locators = self._multi_select(action.figure_id, element_ids)
        tool = resolve_first_visible(self._page, GROUP_TOOL_LOCATORS)
        if tool is None:
            raise UiLayoutChanged("No observable ordinary Group control was found")
        self.biorender_policy.assert_target_allowed(tool.locator)
        tool.locator.click(timeout=5_000)
        self._page.wait_for_timeout(500)
        group_values: list[str] = []
        for locator in locators:
            try:
                value = locator.get_attribute("data-group-id")
            except Exception:
                value = None
            if value:
                group_values.append(value)
        ungroup_visible = self._visible_control(r"^ungroup$|取消组合|取消分组")
        verified = (
            len(group_values) == len(element_ids) and len(set(group_values)) == 1
        ) or ungroup_visible
        after = self._canvas_screenshot(action, canvas, suffix="after-group")
        if not verified:
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message=(
                    "Group was requested, but no group identity or Ungroup control "
                    "was observed."
                ),
                observation_confidence=0.25,
                observation_source=ObservationSource.ACCESSIBILITY,
                evidence_refs=[str(baseline), str(after)],
                metadata={"checkpoint": checkpoint, "element_ids": element_ids},
            )
        observed_group_id = (
            group_values[0]
            if group_values
            else str(action.arguments.get("group_id", f"group_{action.id}"))
        )
        logical_group_id = str(
            action.arguments.get("logical_group_id")
            or action.arguments.get("group_id")
            or observed_group_id
        )
        for element_id in element_ids:
            record = self._element_record(action.figure_id, element_id)
            if record is not None:
                payload = dict(record.get("payload") or {})
                payload["group_id"] = logical_group_id
                payload["observed_group_id"] = observed_group_id
                self._persist_element(
                    action.figure_id,
                    element_id,
                    str(record["kind"]),
                    BoundingBox.model_validate(record["bbox"]),
                    payload,
                )
        self._persist_element(
            action.figure_id,
            logical_group_id,
            "group",
            expected,
            {
                "figure_element_id": self._weak_figure_element_id(
                    action.figure_id,
                    logical_group_id,
                    observed_group_id,
                ),
                "member_ids": element_ids,
                "observed_group_id": observed_group_id,
            },
            observation_confidence=0.94,
            observation_source=ObservationSource.ACCESSIBILITY,
            evidence_refs=[str(baseline), str(after)],
            verification={"group": {"passed": True, "member_ids": element_ids}},
        )
        self._clear_selection(canvas, canvas_bbox)
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message="Selected elements are observed as one group.",
            observed_bbox=expected,
            observation_confidence=0.94,
            observation_source=ObservationSource.ACCESSIBILITY,
            evidence_refs=[str(baseline), str(after)],
            metadata={
                "checkpoint": checkpoint,
                "element_ids": element_ids,
                "group_id": logical_group_id,
                "observed_group_id": observed_group_id,
            },
        )

    def _align_elements(self, action: GuiAction) -> LiveActionEvidence:
        element_ids = [str(value) for value in action.arguments["element_ids"]]
        alignment = str(action.arguments.get("alignment", "middle"))
        specs = ALIGN_TOOL_LOCATORS.get(alignment)
        if specs is None:
            raise UnsupportedLiveAction(f"Unsupported alignment: {alignment}")
        canvas, canvas_bbox = self._canvas()
        before_boxes = [self._element_bbox(action.figure_id, value) for value in element_ids]
        expected = self._union_bbox(before_boxes)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-align")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_ids": element_ids, "operation": "align", "alignment": alignment},
        )
        locators = self._multi_select(action.figure_id, element_ids)
        tool = resolve_first_visible(self._page, specs)
        if tool is None:
            raise UiLayoutChanged(f"No observable {alignment!r} alignment control was found")
        self.biorender_policy.assert_target_allowed(tool.locator)
        tool.locator.click(timeout=5_000)
        self._page.wait_for_timeout(500)
        after_boxes = [
            bounding_box(locator) or self._element_bbox(action.figure_id, element_id)
            for locator, element_id in zip(locators, element_ids, strict=True)
        ]
        verified = self._aligned(after_boxes, alignment)
        after = self._canvas_screenshot(action, canvas, suffix="after-align")
        if not verified:
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message=f"Elements are not observably aligned as {alignment!r}.",
                observation_confidence=0.35,
                observation_source=ObservationSource.DOM,
                evidence_refs=[str(baseline), str(after)],
                metadata={"checkpoint": checkpoint, "element_ids": element_ids},
            )
        for element_id, box in zip(element_ids, after_boxes, strict=True):
            record = self._element_record(action.figure_id, element_id)
            self._persist_element(
                action.figure_id,
                element_id,
                str(record["kind"]) if record else "asset",
                box,
                dict(record.get("payload") or {}) if record else {},
            )
        self._clear_selection(canvas, canvas_bbox)
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message=f"Elements are observably aligned as {alignment!r}.",
            observed_bbox=self._union_bbox(after_boxes),
            observation_confidence=0.94,
            observation_source=ObservationSource.DOM,
            evidence_refs=[str(baseline), str(after)],
            metadata={
                "checkpoint": checkpoint,
                "element_ids": element_ids,
                "alignment": alignment,
            },
        )

    def _distribute_elements(self, action: GuiAction) -> LiveActionEvidence:
        element_ids = [str(value) for value in action.arguments["element_ids"]]
        axis = str(action.arguments.get("axis", "horizontal"))
        if len(element_ids) < 3:
            raise UnsupportedLiveAction("Distribution requires at least three elements")
        specs = DISTRIBUTE_TOOL_LOCATORS.get(axis)
        if specs is None:
            raise UnsupportedLiveAction(f"Unsupported distribution axis: {axis}")
        canvas, canvas_bbox = self._canvas()
        before_boxes = [self._element_bbox(action.figure_id, value) for value in element_ids]
        expected = self._union_bbox(before_boxes)
        baseline = self._canvas_screenshot(action, canvas, suffix="before-distribute")
        checkpoint = self._write_checkpoint(
            action,
            baseline_path=str(baseline),
            expected_bbox=expected,
            payload={"element_ids": element_ids, "operation": "distribute", "axis": axis},
        )
        locators = self._multi_select(action.figure_id, element_ids)
        tool = resolve_first_visible(self._page, specs)
        if tool is None:
            raise UiLayoutChanged(f"No observable {axis!r} distribution control was found")
        self.biorender_policy.assert_target_allowed(tool.locator)
        tool.locator.click(timeout=5_000)
        self._page.wait_for_timeout(500)
        after_boxes = [
            bounding_box(locator) or self._element_bbox(action.figure_id, element_id)
            for locator, element_id in zip(locators, element_ids, strict=True)
        ]
        verified = self._distributed(after_boxes, axis)
        after = self._canvas_screenshot(action, canvas, suffix="after-distribute")
        if not verified:
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message=f"Elements are not observably distributed on the {axis} axis.",
                observation_confidence=0.35,
                observation_source=ObservationSource.DOM,
                evidence_refs=[str(baseline), str(after)],
                metadata={"checkpoint": checkpoint, "element_ids": element_ids},
            )
        for element_id, box in zip(element_ids, after_boxes, strict=True):
            record = self._element_record(action.figure_id, element_id)
            self._persist_element(
                action.figure_id,
                element_id,
                str(record["kind"]) if record else "asset",
                box,
                dict(record.get("payload") or {}) if record else {},
            )
        self._clear_selection(canvas, canvas_bbox)
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message=f"Elements are observably distributed on the {axis} axis.",
            observed_bbox=self._union_bbox(after_boxes),
            observation_confidence=0.94,
            observation_source=ObservationSource.DOM,
            evidence_refs=[str(baseline), str(after)],
            metadata={
                "checkpoint": checkpoint,
                "element_ids": element_ids,
                "axis": axis,
            },
        )

    def _capture_canvas(self, action: GuiAction) -> LiveActionEvidence:
        canvas, canvas_bbox = self._canvas()
        path = self._canvas_screenshot(action, canvas, suffix="final-canvas")
        expected = {
            "asset": set(action.arguments.get("expected_asset_ids", [])),
            "label": set(action.arguments.get("expected_label_ids", [])),
            "connector": set(action.arguments.get("expected_relation_ids", [])),
        }
        missing: dict[str, list[str]] = {}
        observed_counts: dict[str, int] = {}
        for kind, expected_ids in expected.items():
            if self.database is None:
                observed_ids = {
                    element_id
                    for (figure_id, element_id) in self._runtime_locators
                    if figure_id == action.figure_id
                }
            else:
                observed_ids = {
                    item["element_id"]
                    for item in self.database.list_editor_elements(
                        action.figure_id, kind=kind
                    )
                    if item["status"] == "verified"
                }
            observed_counts[kind] = len(observed_ids)
            absent = sorted(expected_ids - observed_ids)
            if absent:
                missing[kind] = absent
        group_failures: list[str] = []
        layout_metrics: dict[str, Any] = {"passed": False, "reason": "database unavailable"}
        if self.database is not None:
            elements = self.database.list_editor_elements(action.figure_id)
            for item in elements:
                locator = self._direct_element_locator(
                    action.figure_id,
                    item["element_id"],
                )
                z_index = self._locator_z_index(locator) if locator is not None else None
                if z_index is not None:
                    self._persist_element(
                        action.figure_id,
                        item["element_id"],
                        str(item["kind"]),
                        BoundingBox.model_validate(item["bbox"]),
                        {"z_index": z_index},
                    )
            elements = self.database.list_editor_elements(action.figure_id)
            expected_groups = action.arguments.get("expected_group_ids", [])
            for group_id in expected_groups:
                members = [
                    item["element_id"]
                    for item in elements
                    if (item.get("payload") or {}).get("group_id") == group_id
                ]
                if len(members) != 2:
                    group_failures.append(str(group_id))
            record = self.database.get_figure(action.figure_id)
            if record is not None:
                layout_metrics = self.layout_observer.observe(
                    canvas_bbox=canvas_bbox,
                    elements=elements,
                    layout=record["layout"],
                    spec=record["spec"],
                )
                self.database.add_verification(
                    action.figure_id,
                    "layout_quality",
                    bool(layout_metrics["passed"]),
                    layout_metrics,
                )
                self._persist_element(
                    action.figure_id,
                    "layout_quality",
                    "layout_state",
                    canvas_bbox,
                    {"metrics": layout_metrics},
                    observation_confidence=0.95,
                    observation_source=ObservationSource.DOM,
                    evidence_refs=[str(path)],
                    verification={"layout": layout_metrics},
                )
                for region_id in action.arguments.get("expected_region_ids", []):
                    self.database.update_element_requirement_status(
                        action.figure_id,
                        f"region_{region_id}",
                        (
                            "verified"
                            if not layout_metrics["region_violations"]
                            else "unknown"
                        ),
                    )
                self.database.update_element_requirement_status(
                    action.figure_id,
                    "layout_z_order",
                    (
                        "verified"
                        if not layout_metrics["z_order_issues"]
                        and not layout_metrics["z_order_unknown"]
                        else "unknown"
                    ),
                )
        if missing or group_failures or not layout_metrics.get("passed", False):
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message=(
                    "Final canvas element inventory, grouping, or layout quality "
                    "could not be fully verified."
                ),
                observed_bbox=canvas_bbox,
                observation_confidence=0.4,
                observation_source=ObservationSource.DOM,
                evidence_refs=[str(path)],
                metadata={
                    "missing": missing,
                    "observed_counts": observed_counts,
                    "group_failures": group_failures,
                    "layout_metrics": layout_metrics,
                },
            )
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message="Final canvas contains the required verified assets, labels, and connectors.",
            observed_bbox=canvas_bbox,
            observation_confidence=0.95,
            observation_source=ObservationSource.DOM,
            evidence_refs=[str(path)],
            metadata={
                "missing": {},
                "observed_counts": observed_counts,
                "group_failures": [],
                "layout_metrics": layout_metrics,
            },
        )

    def _observe_autosave(self, action: GuiAction) -> LiveActionEvidence:
        status = None
        observed_text = ""
        for _ in range(40):
            self.biorender_policy.assert_page_safe(self._page)
            candidate = resolve_first_visible(self._page, SAVE_STATUS_LOCATORS)
            if candidate is not None:
                try:
                    observed_text = candidate.locator.inner_text(timeout=1000).strip()
                except Exception:
                    observed_text = ""
                normalized = observed_text.casefold()
                if re.search(r"\ball changes saved\b|^saved$|^已保存$|所有更改已保存", normalized):
                    status = candidate
                    break
            self._page.wait_for_timeout(250)
        if status is None:
            return LiveActionEvidence(
                status=ActionStatus.UNKNOWN,
                message=(
                    "No completed BioRender autosave status was observed; a fixed "
                    "delay is not treated as proof of saving."
                ),
                observation_confidence=0.0,
                observation_source=ObservationSource.ACCESSIBILITY,
                metadata={
                    "save_mode": action.arguments.get("mode", "biorender_autosave"),
                    "last_observed_save_text": observed_text,
                    "export_invoked": False,
                },
            )
        self.biorender_policy.assert_target_allowed(status.locator)
        observed_bbox = bounding_box(status.locator)
        evidence_path = self._screenshot(action, suffix="save-status-observed")
        observed_at = datetime.now(UTC).isoformat()
        profile_fingerprint = self._profile_versions.get(action.figure_id)
        if observed_bbox is not None:
            self._persist_element(
                action.figure_id,
                "document_save",
                "save_state",
                observed_bbox,
                {
                    "figure_element_id": self._weak_figure_element_id(
                        action.figure_id,
                        "document_save",
                        f"{self._page.url}|{profile_fingerprint}",
                    ),
                    "save_status": observed_text,
                    "observation_time": observed_at,
                    "document_url": self._page.url,
                    "revision_profile_fingerprint": profile_fingerprint,
                    "export_invoked": False,
                    "download_invoked": False,
                    "share_invoked": False,
                },
                observation_confidence=0.98,
                observation_source=ObservationSource.ACCESSIBILITY,
                evidence_refs=[str(evidence_path)],
                verification={
                    "save": {
                        "passed": True,
                        "observed_status": observed_text,
                        "observed_at": observed_at,
                    }
                },
            )
            if self.database is not None:
                self.database.add_audit_event(
                    "figure_autosave_observed",
                    {
                        "save_status": observed_text,
                        "observation_time": observed_at,
                        "document_url": self._page.url,
                        "profile_fingerprint": profile_fingerprint,
                        "evidence_path": str(evidence_path),
                    },
                    figure_id=action.figure_id,
                )
        return LiveActionEvidence(
            status=ActionStatus.VERIFIED,
            message=(
                "BioRender autosave status is observable; no export or sharing "
                "action was used."
            ),
            observed_bbox=observed_bbox,
            observation_confidence=0.98,
            observation_source=ObservationSource.ACCESSIBILITY,
            evidence_refs=[str(evidence_path)],
            metadata={
                "save_mode": action.arguments.get("mode", "biorender_autosave"),
                "export_invoked": False,
                "download_invoked": False,
                "share_invoked": False,
                "save_status": observed_text,
                "observation_time": observed_at,
                "document_url": self._page.url,
                "revision_profile_fingerprint": profile_fingerprint,
                "save_status_locator": status.evidence.model_dump(mode="json"),
            },
        )

    def _write_checkpoint(
        self,
        action: GuiAction,
        *,
        baseline_path: str,
        expected_bbox: BoundingBox,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoint = {
            "action_id": action.id,
            "action_type": action.action.value,
            "ui_profile_version": self._profile_versions.get(action.figure_id),
            "baseline_canvas_path": baseline_path,
            "expected_bbox": expected_bbox.model_dump(mode="json"),
            "payload": payload,
        }
        if self.database is not None:
            self.database.record_action_result(
                action.figure_id,
                GuiActionResult(
                    action_id=action.id,
                    status=ActionStatus.EXECUTING,
                    attempt=self._attempt,
                    message="Mutation checkpoint saved before GUI interaction.",
                    screenshot_path=baseline_path,
                    expected_bbox=action.expected_bbox,
                    evidence_refs=[baseline_path],
                    metadata={
                        "mode": "live",
                        "evidence_kind": "checkpoint",
                        "checkpoint": checkpoint,
                    },
                ),
            )
        return checkpoint

    def _mutation_evidence(
        self,
        action: GuiAction,
        *,
        pixel: InsertionObservation,
        expected_bbox: BoundingBox,
        observed_bbox: BoundingBox | None,
        require_geometry: bool,
        evidence_refs: list[str],
        checkpoint: dict[str, Any],
        success_message: str,
        allow_geometry_only: bool = False,
        size_tolerance: float = 0.3,
    ) -> LiveActionEvidence:
        refs = list(dict.fromkeys([*evidence_refs, *pixel.evidence_refs]))
        geometry = self.geometry_observer.observe(
            expected_bbox=expected_bbox,
            observed_bbox=observed_bbox,
            evidence_refs=refs,
            size_tolerance=size_tolerance,
        )
        pixel_ok = pixel.presence == Presence.PRESENT and pixel.confidence >= 0.75
        geometry_ok = geometry.presence == Presence.PRESENT and geometry.confidence >= 0.75
        if (pixel_ok or (allow_geometry_only and geometry_ok)) and (
            geometry_ok or not require_geometry
        ):
            observed = geometry.observed_bbox or pixel.observed_bbox
            return LiveActionEvidence(
                status=ActionStatus.VERIFIED,
                message=success_message,
                observed_bbox=observed,
                observation_confidence=min(
                    pixel.confidence,
                    geometry.confidence if geometry_ok else pixel.confidence,
                ),
                observation_source=(
                    ObservationSource.DOM if geometry_ok else pixel.source
                ),
                evidence_refs=refs,
                metadata={
                    "checkpoint": checkpoint,
                    "pixel_observation": pixel.model_dump(mode="json"),
                    "geometry_observation": geometry.model_dump(mode="json"),
                    "geometry_only_verification": not pixel_ok,
                },
            )
        return LiveActionEvidence(
            status=ActionStatus.UNKNOWN,
            message=(
                "GUI action executed, but required pixel and geometry observations "
                "did not both verify the result."
            ),
            observed_bbox=observed_bbox or pixel.observed_bbox,
            observation_confidence=min(
                pixel.confidence,
                geometry.confidence if require_geometry else pixel.confidence,
            ),
            observation_source=(
                ObservationSource.DOM if observed_bbox is not None else pixel.source
            ),
            evidence_refs=refs,
            metadata={
                "checkpoint": checkpoint,
                "pixel_observation": pixel.model_dump(mode="json"),
                "geometry_observation": geometry.model_dump(mode="json"),
                "safe_to_retry": False,
            },
        )

    def _ensure_editor_open(self, figure_id: str) -> None:
        if self._current_figure_id == figure_id and self._canvas_locator() is not None:
            return
        if self.database is None:
            if self._canvas_locator() is None:
                raise UiLayoutChanged(
                    "A live page is not open and no database is available to restore its URL"
                )
            self._current_figure_id = figure_id
            return
        open_action = next(
            (
                action
                for action in self.database.list_actions(figure_id)
                if action.action == ActionType.OPEN_EDITOR
            ),
            None,
        )
        if open_action is None:
            raise UiLayoutChanged("Figure has no persisted open-editor action")
        self._open_editor(open_action)

    def _canvas(self) -> tuple[Any, BoundingBox]:
        resolved = resolve_largest_visible(self._page, CANVAS_LOCATORS)
        if resolved is None:
            raise UiLayoutChanged("BioRender canvas could not be re-located")
        box = bounding_box(resolved.locator)
        if box is None:
            raise UiLayoutChanged("BioRender canvas has no observable bounding box")
        return resolved.locator, box

    def _canvas_locator(self) -> Any | None:
        resolved = resolve_largest_visible(self._page, CANVAS_LOCATORS)
        return resolved.locator if resolved else None

    def _observable_canvas_object_count(self, kind: str) -> int | None:
        selectors = {
            "asset": (
                "[data-testid*='canvas-element-asset'], "
                "[data-agent-kind='asset']"
            ),
            "label": (
                "[data-testid*='canvas-element-label'], "
                "[data-agent-kind='label']"
            ),
            "connector": (
                "[data-testid*='canvas-element-connector'], "
                "[data-agent-kind='connector']"
            ),
        }
        selector = selectors.get(kind)
        if selector is None:
            return None
        try:
            count = self._page.locator(selector).count()
        except Exception:
            return None
        return count if count > 0 else None

    @staticmethod
    def _weak_figure_element_id(
        figure_id: str,
        logical_element_id: str,
        identity_seed: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{figure_id}|{logical_element_id}|{identity_seed}".encode()
        ).hexdigest()[:20]
        return f"weak_{digest}"

    @staticmethod
    def _locator_z_index(locator: Any) -> int | None:
        try:
            value = locator.evaluate(
                "el => getComputedStyle(el).zIndex === 'auto' "
                "? 0 : Number(getComputedStyle(el).zIndex)"
            )
            return int(value) if value is not None else None
        except Exception:
            return None

    def _expected_viewport_bbox(
        self,
        action: GuiAction,
        canvas_bbox: BoundingBox,
        *,
        fallback: BoundingBox | None = None,
    ) -> BoundingBox:
        expected = action.expected_bbox
        if expected is None:
            if fallback is not None:
                return fallback
            raise UiLayoutChanged(f"Action {action.id!r} has no expected geometry")
        if expected.coordinate_space == CoordinateSpace.VIEWPORT_PIXELS:
            return expected
        if expected.coordinate_space == CoordinateSpace.CANVAS_PIXELS:
            return expected.model_copy(
                update={
                    "x": canvas_bbox.x + expected.x,
                    "y": canvas_bbox.y + expected.y,
                    "coordinate_space": CoordinateSpace.VIEWPORT_PIXELS,
                }
            )
        return BoundingBox(
            x=canvas_bbox.x + expected.x * canvas_bbox.width,
            y=canvas_bbox.y + expected.y * canvas_bbox.height,
            width=expected.width * canvas_bbox.width,
            height=expected.height * canvas_bbox.height,
            coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
        )

    def _element_record(self, figure_id: str, element_id: str) -> dict[str, Any] | None:
        if self.database is None:
            return None
        return self.database.get_editor_element(figure_id, element_id)

    def _element_bbox(self, figure_id: str, element_id: str) -> BoundingBox:
        locator = self._direct_element_locator(figure_id, element_id)
        observed = bounding_box(locator) if locator is not None else None
        if observed is not None:
            return observed
        record = self._element_record(figure_id, element_id)
        if record is None:
            raise UiLayoutChanged(
                f"No verified editor geometry exists for element {element_id!r}"
            )
        return BoundingBox.model_validate(record["bbox"])

    def _element_boxes(self, figure_id: str, kind: str) -> dict[str, BoundingBox]:
        if self.database is None:
            return {
                element_id: box
                for (candidate_figure_id, element_id), locator in self._runtime_locators.items()
                if candidate_figure_id == figure_id
                and (box := bounding_box(locator)) is not None
            }
        return {
            item["element_id"]: BoundingBox.model_validate(item["bbox"])
            for item in self.database.list_editor_elements(figure_id, kind=kind)
            if item["status"] == "verified"
        }

    def _direct_element_locator(self, figure_id: str, element_id: str) -> Any | None:
        cached = self._runtime_locators.get((figure_id, element_id))
        if cached is not None:
            try:
                if cached.is_visible() and cached.bounding_box() is not None:
                    return cached
            except Exception:
                self._runtime_locators.pop((figure_id, element_id), None)
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", element_id)
        selectors = (
            f"[data-agent-id='{safe_id}'], "
            f"[data-entity-id='{safe_id}'], "
            f"[data-label-id='{safe_id}'], "
            f"[data-relation-id='{safe_id}']"
        )
        locator = self._page.locator(selectors)
        count = min(locator.count(), 20)
        for index in range(count):
            candidate = locator.nth(index)
            if candidate.is_visible() and candidate.bounding_box() is not None:
                self._runtime_locators[(figure_id, element_id)] = candidate
                return candidate
        return None

    def _closest_canvas_element(self, expected: BoundingBox) -> Any | None:
        candidates = self._page.locator(
            "[data-testid*='canvas-element'], [data-agent-id], "
            "[data-entity-id], [data-label-id], [data-relation-id]"
        )
        best: tuple[float, Any] | None = None
        for index in range(min(candidates.count(), 250)):
            candidate = candidates.nth(index)
            observed = bounding_box(candidate)
            if observed is None:
                continue
            center_delta = abs(
                observed.x + observed.width / 2 - expected.x - expected.width / 2
            ) + abs(
                observed.y + observed.height / 2 - expected.y - expected.height / 2
            )
            size_delta = abs(observed.width - expected.width) + abs(
                observed.height - expected.height
            )
            score = center_delta + 0.35 * size_delta
            if best is None or score < best[0]:
                best = (score, candidate)
        return best[1] if best is not None and best[0] <= 90 else None

    def _select_element(self, figure_id: str, element_id: str) -> tuple[Any | None, BoundingBox]:
        self.biorender_policy.assert_page_safe(self._page)
        locator = self._direct_element_locator(figure_id, element_id)
        current = self._element_bbox(figure_id, element_id)
        if locator is not None:
            self.biorender_policy.assert_target_allowed(locator)
            locator.click(timeout=5_000)
        else:
            self._page.mouse.click(
                current.x + current.width / 2,
                current.y + current.height / 2,
            )
        self._page.wait_for_timeout(250)
        selected = self._selected_or_last_object()
        if selected is not None:
            self._runtime_locators[(figure_id, element_id)] = selected
            locator = selected
            selected_box = bounding_box(selected)
            if selected_box is not None:
                current = selected_box
        return locator, current

    def _multi_select(self, figure_id: str, element_ids: list[str]) -> list[Any]:
        locators: list[Any] = []
        for index, element_id in enumerate(element_ids):
            locator = self._direct_element_locator(figure_id, element_id)
            box = self._element_bbox(figure_id, element_id)
            modifiers = ["Shift"] if index else []
            if locator is not None:
                self.biorender_policy.assert_target_allowed(locator)
                locator.click(timeout=5_000, modifiers=modifiers)
                locators.append(locator)
            else:
                if modifiers:
                    self._page.keyboard.down("Shift")
                self._page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
                if modifiers:
                    self._page.keyboard.up("Shift")
                selected = self._selected_or_last_object()
                if selected is None:
                    raise UiLayoutChanged(
                        f"Element {element_id!r} could not be observed after selection"
                    )
                self._runtime_locators[(figure_id, element_id)] = selected
                locators.append(selected)
        self._page.wait_for_timeout(250)
        return locators

    def _selected_or_last_object(self) -> Any | None:
        selected = resolve_largest_visible(self._page, SELECTED_OBJECT_LOCATORS)
        if selected is None:
            return None
        try:
            return selected.locator.element_handle() or selected.locator
        except Exception:
            return selected.locator

    def _selected_or_last_connector(self) -> Any | None:
        selected = self._selected_or_last_object()
        if selected is not None:
            try:
                signature = " ".join(
                    filter(
                        None,
                        (
                            selected.get_attribute("data-connector-type"),
                            selected.get_attribute("data-relation-id"),
                            selected.get_attribute("data-testid"),
                            selected.get_attribute("class"),
                        ),
                    )
                ).casefold()
            except Exception:
                signature = ""
            if re.search(r"connector|relation|arrow|t[_ -]?bar", signature):
                return selected
        candidates = self._page.locator(
            "[data-testid*='canvas-element-connector'], "
            "[data-connector-type], [data-relation-id]"
        )
        for index in range(min(candidates.count(), 250) - 1, -1, -1):
            candidate = candidates.nth(index)
            try:
                if candidate.is_visible() and candidate.bounding_box() is not None:
                    return candidate
            except Exception:
                continue
        return None

    def _active_text_editor(self) -> Any | None:
        candidates = self._page.locator(
            "textarea:visible, [contenteditable='true']:visible, "
            "input[type='text']:visible"
        )
        count = min(candidates.count(), 50)
        for index in range(count - 1, -1, -1):
            candidate = candidates.nth(index)
            try:
                role = (candidate.get_attribute("role") or "").casefold()
                placeholder = (candidate.get_attribute("placeholder") or "").casefold()
                if role == "searchbox" or "search" in placeholder:
                    continue
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    @staticmethod
    def _observable_label_text(locator: Any | None) -> tuple[str | None, bool | None]:
        if locator is None:
            return None, None
        observed_text = None
        for attribute in ("data-label-text", "aria-label", "data-label"):
            try:
                value = locator.get_attribute(attribute)
            except Exception:
                value = None
            if value:
                observed_text = str(value).strip()
                break
        if observed_text is None:
            try:
                observed_text = locator.inner_text(timeout=1000).strip()
            except Exception:
                observed_text = None
        try:
            truncated = bool(
                locator.evaluate(
                    "el => {"
                    "const style = getComputedStyle(el);"
                    "const probe = document.createElement('span');"
                    "probe.textContent = el.getAttribute('data-label-text') "
                    "|| el.innerText || el.textContent || '';"
                    "probe.style.cssText = 'position:fixed;visibility:hidden;'"
                    "+ 'white-space:pre;width:max-content;height:max-content;';"
                    "probe.style.font = style.font;"
                    "probe.style.letterSpacing = style.letterSpacing;"
                    "document.body.appendChild(probe);"
                    "const measured = probe.getBoundingClientRect();"
                    "probe.remove();"
                    "const availableWidth = el.clientWidth "
                    "- parseFloat(style.paddingLeft || 0) "
                    "- parseFloat(style.paddingRight || 0);"
                    "const availableHeight = el.clientHeight "
                    "- parseFloat(style.paddingTop || 0) "
                    "- parseFloat(style.paddingBottom || 0);"
                    "return measured.width > availableWidth + 2 "
                    "|| measured.height > availableHeight + 3;"
                    "}"
                )
            )
        except Exception:
            truncated = None
        return observed_text, truncated

    @staticmethod
    def _connector_anchor_points(
        source: BoundingBox,
        target: BoundingBox,
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        source_center = (
            source.x + source.width / 2,
            source.y + source.height / 2,
        )
        target_center = (
            target.x + target.width / 2,
            target.y + target.height / 2,
        )
        dx = target_center[0] - source_center[0]
        dy = target_center[1] - source_center[1]
        if abs(dx) >= abs(dy):
            if dx >= 0:
                return (
                    (source.x + source.width - 3, source_center[1]),
                    (target.x + 3, target_center[1]),
                )
            return (
                (source.x + 3, source_center[1]),
                (target.x + target.width - 3, target_center[1]),
            )
        # A vertical center route would pass through labels placed below the
        # elements. Use the right-side anchors, which remain inside both assets.
        return (
            (source.x + source.width - 3, source_center[1]),
            (target.x + target.width - 3, target_center[1]),
        )

    @staticmethod
    def _observable_connector_route(
        locator: Any | None,
    ) -> tuple[str | None, tuple[float, float] | None, tuple[float, float] | None]:
        if locator is None:
            return None, None, None
        observed_type = None
        for attribute in ("data-connector-type", "data-type", "aria-label"):
            try:
                value = locator.get_attribute(attribute)
            except Exception:
                value = None
            if value:
                observed_type = str(value).strip().casefold().replace(" ", "_")
                break
        try:
            route = locator.evaluate(
                """el => {
                    const parent = el.offsetParent;
                    if (!parent) return null;
                    const parentRect = parent.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    const matrix = new DOMMatrixReadOnly(style.transform === 'none'
                        ? 'matrix(1,0,0,1,0,0)' : style.transform);
                    const startX = parentRect.left + parent.clientLeft + el.offsetLeft;
                    const startY = parentRect.top + parent.clientTop + el.offsetTop
                        + el.offsetHeight / 2;
                    return {
                        startX,
                        startY,
                        endX: startX + el.offsetWidth * matrix.a,
                        endY: startY + el.offsetWidth * matrix.b,
                    };
                }"""
            )
        except Exception:
            route = None
        if not isinstance(route, dict):
            return observed_type, None, None
        try:
            start = (float(route["startX"]), float(route["startY"]))
            end = (float(route["endX"]), float(route["endY"]))
        except (KeyError, TypeError, ValueError):
            return observed_type, None, None
        return observed_type, start, end

    def _verify_existing_label(
        self,
        figure_id: str,
        element_id: str,
        locator: Any | None,
        observed_bbox: BoundingBox,
        canvas_bbox: BoundingBox,
    ) -> dict[str, Any]:
        record = self._element_record(figure_id, element_id) or {}
        payload = record.get("payload") or {}
        expected_text = str(
            payload.get("expected_text") or payload.get("observed_text") or ""
        )
        target_element_id = str(
            payload.get("target_element_id") or payload.get("entity_id") or ""
        )
        target_record = (
            self._element_record(figure_id, target_element_id)
            if target_element_id
            else None
        )
        observed_text, truncated = self._observable_label_text(locator)
        return self.label_observer.observe(
            expected_text=expected_text,
            observed_text=observed_text,
            label_bbox=observed_bbox,
            target_element_id=target_element_id,
            target_bbox=(
                BoundingBox.model_validate(target_record["bbox"])
                if target_record is not None
                else None
            ),
            asset_boxes=self._element_boxes(figure_id, "asset"),
            canvas_bbox=canvas_bbox,
            truncated=truncated,
        )

    @staticmethod
    def _editor_value(editor: Any) -> str:
        try:
            return editor.input_value()
        except Exception:
            try:
                return editor.inner_text()
            except Exception:
                return ""

    def _persist_element(
        self,
        figure_id: str,
        element_id: str,
        kind: str,
        bbox: BoundingBox,
        payload: dict[str, Any] | None = None,
        *,
        expected_bbox: BoundingBox | None = None,
        observation_confidence: float | None = None,
        observation_source: ObservationSource | None = None,
        evidence_refs: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        if self.database is not None:
            existing = self.database.get_editor_element(figure_id, element_id)
            merged_payload = dict(existing.get("payload") or {}) if existing else {}
            merged_payload.update(payload or {})
            merged_payload.setdefault("logical_element_id", element_id)
            locator = self._direct_element_locator(figure_id, element_id)
            z_index = self._locator_z_index(locator) if locator is not None else None
            if z_index is not None:
                merged_payload["z_index"] = z_index
            figure_element_id = merged_payload.get("figure_element_id")
            self.database.upsert_editor_element(
                figure_id,
                element_id,
                kind,
                bbox,
                payload=merged_payload,
                status="verified",
                figure_element_id=(
                    str(figure_element_id) if figure_element_id is not None else None
                ),
                expected_bbox=expected_bbox,
                observation_confidence=observation_confidence,
                observation_source=(
                    observation_source.value if observation_source else None
                ),
                evidence_refs=evidence_refs,
                verification=verification,
            )

    def _canvas_screenshot(self, action: GuiAction, canvas: Any, *, suffix: str) -> Path:
        figure_dir = self.evidence_dir / action.figure_id
        figure_dir.mkdir(parents=True, exist_ok=True)
        path = figure_dir / f"{action.sequence:04d}_{action.id}_{suffix}.png"
        canvas.screenshot(path=str(path))
        return path

    def _clear_selection(self, canvas: Any, canvas_bbox: BoundingBox) -> None:
        try:
            self._page.keyboard.press("Escape")
            self._page.mouse.click(canvas_bbox.x + 8, canvas_bbox.y + 8)
            self._page.wait_for_timeout(120)
        except Exception:
            pass

    def _authentication_visible(self) -> bool:
        page = self._page
        return bool(
            re.search(r"(?:login|log-in|sign-in|signin)", page.url, re.IGNORECASE)
            or page.locator("input[type='password']").count() > 0
        )

    def _visible_control(self, pattern: str) -> bool:
        try:
            locator = self._page.get_by_role("button", name=re.compile(pattern, re.IGNORECASE))
            return any(locator.nth(index).is_visible() for index in range(min(locator.count(), 20)))
        except Exception:
            return False

    @staticmethod
    def _union_bbox(boxes: list[BoundingBox]) -> BoundingBox:
        if not boxes:
            raise ValueError("at least one bounding box is required")
        left = min(box.x for box in boxes)
        top = min(box.y for box in boxes)
        right = max(box.x + box.width for box in boxes)
        bottom = max(box.y + box.height for box in boxes)
        return BoundingBox(
            x=left,
            y=top,
            width=max(1.0, right - left),
            height=max(1.0, bottom - top),
            coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
        )

    @staticmethod
    def _aligned(boxes: list[BoundingBox], alignment: str, tolerance: float = 5.0) -> bool:
        if len(boxes) < 2:
            return True
        if alignment == "left":
            values = [box.x for box in boxes]
        elif alignment == "right":
            values = [box.x + box.width for box in boxes]
        elif alignment == "center":
            values = [box.x + box.width / 2 for box in boxes]
        else:
            values = [box.y + box.height / 2 for box in boxes]
        return max(values) - min(values) <= tolerance

    @staticmethod
    def _distributed(
        boxes: list[BoundingBox],
        axis: str,
        tolerance: float = 6.0,
    ) -> bool:
        if len(boxes) < 3:
            return False
        if axis == "horizontal":
            ordered = sorted(boxes, key=lambda box: box.x)
            gaps = [
                ordered[index + 1].x - (ordered[index].x + ordered[index].width)
                for index in range(len(ordered) - 1)
            ]
        else:
            ordered = sorted(boxes, key=lambda box: box.y)
            gaps = [
                ordered[index + 1].y - (ordered[index].y + ordered[index].height)
                for index in range(len(ordered) - 1)
            ]
        return max(gaps) - min(gaps) <= tolerance

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
        self._current_figure_id = None

    @property
    def page(self) -> Any:
        self.start()
        return self._page

    @classmethod
    def manual_login(cls, url: str = "https://app.biorender.com/") -> None:
        operator = cls(headed=True)
        try:
            operator.start()
            operator._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            print("Complete BioRender login manually in the visible browser window.")
            input(
                "After the dashboard/editor is visible, press Enter here to "
                "preserve the session: "
            )
        finally:
            operator.close()
