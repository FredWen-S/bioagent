from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.cli import cmd_calibrate_ui, cmd_phase0_search_drag
from app.operator.biorender.calibration import BioRenderUiCalibrator
from app.operator.biorender.locators import (
    CANVAS_LOCATORS,
    CANDIDATE_SELECTORS,
    INTERACTIVE_SELECTOR,
    MODAL_SELECTOR,
    SEARCH_RESULTS_LOCATORS,
)
from app.operator.biorender.observer import PixelDiffInsertionObserver
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.biorender.probe import BioRenderSingleAssetProbe
from app.operator.biorender.reconciliation import (
    ProbeReconciler,
    ReconciliationDecision,
)
from app.operator.biorender.search import SafeAssetSearch
from app.operator.dry_run import DryRunOperator
from app.operator.errors import CalibrationFailed, PolicyBlocked
from app.schemas.biorender_probe import (
    AssetCandidateRecord,
    InsertionObservation,
    Presence,
    ProbeCheckpoint,
)
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
from tests.mocks.fake_playwright import FakeElement, FakeLocator, FakePage


def viewport_bbox(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(
        x=x,
        y=y,
        width=width,
        height=height,
        coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
    )


def test_ai_control_is_rejected_but_ordinary_generate_text_is_not() -> None:
    guard = BioRenderPolicyGuard()
    assert guard.classify_text("Generate legend labels") is None
    with pytest.raises(PolicyBlocked, match="biorender_ai_control"):
        guard.assert_target_allowed(FakeLocator([FakeElement(text="BioRender AI Generate")]))
    with pytest.raises(PolicyBlocked, match="biorender_ai_control"):
        guard.assert_query_allowed("Create a figure with AI")


def test_ordinary_asset_search_is_allowed_and_candidate_is_proven(tmp_path: Path) -> None:
    search_input = FakeElement(
        bbox={"x": 10, "y": 20, "width": 250, "height": 36},
        attrs={"role": "searchbox", "accessible_name": "Search assets"},
    )
    candidate = FakeElement(
        text="T cell",
        bbox={"x": 20, "y": 100, "width": 100, "height": 100},
        attrs={"draggable": "true", "data-testid": "asset-card-t-cell"},
        has_thumbnail=True,
    )
    candidate_selector = ", ".join(CANDIDATE_SELECTORS)
    results = FakeElement(
        bbox={"x": 10, "y": 70, "width": 300, "height": 700},
        children={candidate_selector: [candidate]},
    )
    page = FakePage(
        selector_map={
            "searchbox-fixture": [search_input],
            SEARCH_RESULTS_LOCATORS[0].query: [results],
            INTERACTIVE_SELECTOR: [],
            MODAL_SELECTOR: [],
        }
    )
    outcome = SafeAssetSearch(page, evidence_dir=tmp_path).search("T cell", "probe_safe")
    assert search_input.filled_value == "T cell"
    assert outcome.selected.record.draggable is True
    assert outcome.selected.record.in_results_region is True
    assert outcome.selected.record.rejected_reasons == []


def test_expected_bbox_is_never_automatically_observed() -> None:
    expected = viewport_bbox(100, 120, 80, 80)
    result = GuiActionResult(
        action_id="action_drag",
        status=ActionStatus.EXECUTED_UNVERIFIED,
        attempt=1,
        expected_bbox=expected,
    )
    assert result.expected_bbox == expected
    assert result.observed_bbox is None
    assert result.observation_confidence is None


def test_live_execution_semantics_include_executed_unverified() -> None:
    assert ActionStatus.EXECUTED_UNVERIFIED.value == "executed_unverified"
    assert ActionStatus.VERIFIED.value == "verified"
    assert ActionStatus.UNKNOWN.value == "unknown"
    assert ActionStatus.BLOCKED_BY_POLICY.value == "blocked_by_policy"


def test_pixel_observer_confirms_real_change_near_expected_target(tmp_path: Path) -> None:
    before_path = tmp_path / "before.png"
    after_path = tmp_path / "after.png"
    Image.new("RGB", (200, 200), "white").save(before_path)
    after = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(after).rectangle((60, 60, 105, 105), fill="blue")
    after.save(after_path)
    canvas = viewport_bbox(100, 200, 200, 200)
    expected = viewport_bbox(160, 260, 45, 45)

    observation = PixelDiffInsertionObserver().observe(
        baseline_path=str(before_path),
        current_path=str(after_path),
        canvas_bbox=canvas,
        expected_bbox=expected,
    )

    assert observation.presence == Presence.PRESENT
    assert observation.observed_bbox is not None
    assert observation.source == ObservationSource.SCREENSHOT_PIXEL_DIFF
    assert observation.confidence >= 0.75


def test_observer_confirmation_updates_probe_action_to_verified(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "probe.db")
    database.create_probe_run("probe_verify", "https://example/editor", "T cell")
    candidate = AssetCandidateRecord(
        candidate_id="candidate_safe",
        ordinal=2,
        text="T cell",
        bbox=viewport_bbox(10, 10, 50, 50),
        draggable=True,
        in_results_region=True,
        ordinary_asset_evidence=["draggable"],
    )
    checkpoint = ProbeCheckpoint(
        run_id="probe_verify",
        profile_version="ui-test",
        editor_url="https://example/editor",
        query="T cell",
        expected_bbox=viewport_bbox(100, 100, 60, 60),
        baseline_canvas_path=str(tmp_path / "before.png"),
        canvas_bbox=viewport_bbox(0, 0, 500, 500),
        candidate=candidate,
        drag_action_id="probe_drag_asset",
    )
    observation = InsertionObservation(
        presence=Presence.PRESENT,
        confidence=0.9,
        expected_bbox=checkpoint.expected_bbox,
        observed_bbox=viewport_bbox(102, 104, 58, 57),
        source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
        evidence_refs=["before.png", "after.png"],
    )
    runner = BioRenderSingleAssetProbe(FakePage(), database, output_dir=tmp_path)
    outcome = runner._apply_observation(
        "probe_verify",
        "probe_drag_asset",
        observation,
        checkpoint=checkpoint,
    )
    assert outcome["status"] == "awaiting_confirmation"
    actions = database.probe_actions("probe_verify")
    assert actions[0]["status"] == "verified"
    assert actions[0]["observed_bbox"] != actions[0]["expected_bbox"]


def test_observer_returns_unknown_without_real_evidence(tmp_path: Path) -> None:
    observation = PixelDiffInsertionObserver().observe(
        baseline_path=str(tmp_path / "missing-before.png"),
        current_path=str(tmp_path / "missing-after.png"),
        canvas_bbox=viewport_bbox(0, 0, 500, 500),
        expected_bbox=viewport_bbox(100, 100, 60, 60),
    )
    assert observation.presence == Presence.UNKNOWN
    assert observation.observed_bbox is None


class StaticObserver:
    def __init__(self, observation: InsertionObservation) -> None:
        self.observation = observation
        self.calls = 0

    def observe(self, **kwargs):
        del kwargs
        self.calls += 1
        return self.observation


def checkpoint_fixture(tmp_path: Path) -> ProbeCheckpoint:
    expected = viewport_bbox(100, 100, 60, 60)
    return ProbeCheckpoint(
        run_id="probe_recovery",
        profile_version="ui-test",
        editor_url="https://example/editor",
        query="T cell",
        expected_bbox=expected,
        baseline_canvas_path=str(tmp_path / "before.png"),
        canvas_bbox=viewport_bbox(0, 0, 500, 500),
        candidate=AssetCandidateRecord(
            candidate_id="candidate_safe",
            ordinal=0,
            text="T cell",
            bbox=viewport_bbox(10, 10, 50, 50),
            draggable=True,
            in_results_region=True,
        ),
        drag_action_id="probe_drag_asset",
    )


def test_recovery_existing_asset_suppresses_drag_replay(tmp_path: Path) -> None:
    checkpoint = checkpoint_fixture(tmp_path)
    observer = StaticObserver(
        InsertionObservation(
            presence=Presence.PRESENT,
            confidence=0.92,
            expected_bbox=checkpoint.expected_bbox,
            observed_bbox=viewport_bbox(104, 102, 55, 56),
            source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
        )
    )
    result = ProbeReconciler(observer).reconcile(
        checkpoint,
        current_profile_version="ui-test",
        current_canvas_path="current.png",
    )
    assert result.decision == ReconciliationDecision.ALREADY_VERIFIED
    assert "suppressed" in result.reason
    assert observer.calls == 1


def test_recovery_unknown_observation_pauses(tmp_path: Path) -> None:
    checkpoint = checkpoint_fixture(tmp_path)
    observer = StaticObserver(
        InsertionObservation(
            presence=Presence.UNKNOWN,
            confidence=0.2,
            expected_bbox=checkpoint.expected_bbox,
            source=ObservationSource.SCREENSHOT_PIXEL_DIFF,
        )
    )
    result = ProbeReconciler(observer).reconcile(
        checkpoint,
        current_profile_version="ui-test",
        current_canvas_path="current.png",
    )
    assert result.decision == ReconciliationDecision.PAUSE_UNKNOWN


def test_calibration_missing_search_input_saves_evidence_then_fails(tmp_path: Path) -> None:
    results = FakeElement(bbox={"x": 0, "y": 0, "width": 300, "height": 800})
    canvas = FakeElement(bbox={"x": 320, "y": 80, "width": 1000, "height": 700})
    page = FakePage(
        selector_map={
            SEARCH_RESULTS_LOCATORS[0].query: [results],
            CANVAS_LOCATORS[0].query: [canvas],
            INTERACTIVE_SELECTOR: [],
            MODAL_SELECTOR: [],
            "input[type='password']": [],
        }
    )
    with pytest.raises(CalibrationFailed) as captured:
        BioRenderUiCalibrator(page, output_dir=tmp_path).calibrate()
    assert captured.value.profile_path is not None
    assert Path(captured.value.profile_path).exists()


def test_dry_run_does_not_observe_or_modify_live_ui(tmp_path: Path) -> None:
    action = GuiAction(
        id="action_dry_run_probe",
        figure_id="figure_dry_run",
        sequence=0,
        action=ActionType.DRAG_ASSET,
        arguments={"target_x": 0.5, "target_y": 0.5},
        expected_bbox=viewport_bbox(100, 100, 50, 50),
    )
    result = DryRunOperator(evidence_dir=tmp_path).execute(action)
    assert result.status == ActionStatus.SUCCEEDED
    assert result.observed_bbox is None
    assert result.metadata["mode"] == "dry-run"


@pytest.mark.parametrize("command", [cmd_calibrate_ui, cmd_phase0_search_drag])
def test_live_commands_refuse_without_confirm_live(command) -> None:
    args = argparse.Namespace(confirm_live=False)
    with pytest.raises(SystemExit, match="--confirm-live"):
        command(args)


@pytest.mark.parametrize(
    "modal_text",
    [
        "Use 1 AI credit to Generate Figure?",
        "Upgrade now to unlock this asset",
    ],
)
def test_ai_credit_or_subscription_modal_stops_page(modal_text: str) -> None:
    modal = FakeElement(
        text=modal_text,
        bbox={"x": 300, "y": 200, "width": 500, "height": 300},
    )
    page = FakePage(selector_map={MODAL_SELECTOR: [modal]})
    with pytest.raises(PolicyBlocked):
        BioRenderPolicyGuard().assert_page_safe(page)


def test_sqlite_v2_migration_adds_observation_columns(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE gui_actions (
            id TEXT PRIMARY KEY,
            figure_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error_type TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    database = FigureDatabase(path)
    with database.connect() as migrated:
        columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(gui_actions)")
        }
    assert {
        "expected_bbox_json",
        "observed_bbox_json",
        "observation_confidence",
        "observation_source",
    }.issubset(columns)
