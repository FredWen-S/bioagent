from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from app.cli import cmd_verify_live_figure
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.biorender.search import SafeAssetSearch
from app.operator.errors import PolicyBlocked
from app.operator.playwright_live import LivePlaywrightOperator
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import (
    ActionStatus,
    ActionType,
    BoundingBox,
    GuiAction,
    GuiActionResult,
)
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine

PD1_REQUEST = (
    "制作双栏对比：未经治疗时 PD-1/PD-L1 结合并抑制 T 细胞；"
    "anti-PD-1 treatment 阻断相互作用，T 细胞杀伤 Tumor cell。"
)


def fixture_url() -> str:
    return (
        Path(__file__).resolve().parent
        / "fixtures"
        / "biorender_editor.html"
    ).as_uri()


def test_real_chromium_searches_multiple_assets_and_blocks_ai_dialog(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(fixture_url())
        search = SafeAssetSearch(
            page,
            evidence_dir=tmp_path / "search-evidence",
        )
        for index, query in enumerate(
            ("T cell", "Tumor cell", "PD-1 receptor", "Anti-PD-1 antibody")
        ):
            outcome = search.search(query, f"query-{index}", max_attempts=2)
            assert outcome.selected.record.draggable is True
            assert outcome.selected.record.in_results_region is True
            assert outcome.selected.record.rejected_reasons == []

        page.get_by_role("button", name="BioRender AI Generate").click()
        with pytest.raises(PolicyBlocked, match="ai_credit_confirmation"):
            BioRenderPolicyGuard().assert_page_safe(page)
        browser.close()


def test_full_pd1_workflow_uses_real_chromium_fixture(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "browser-integration.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(PD1_REQUEST, editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )

    status = engine.execute(bundle.figure_spec.id, operator)

    assert status == FigureStatus.AWAITING_CONFIRMATION
    states = database.action_states(bundle.figure_spec.id)
    assert states
    assert {state["status"] for state in states} == {"verified"}
    assert len(database.list_editor_elements(bundle.figure_spec.id, kind="asset")) == 9
    assert len(database.list_editor_elements(bundle.figure_spec.id, kind="label")) == 9
    assert len(database.list_editor_elements(bundle.figure_spec.id, kind="connector")) == 5
    assert database.get_verifications(bundle.figure_spec.id)[-1]["payload"][
        "visual_verification_performed"
    ] is True
    assert list((tmp_path / "evidence" / bundle.figure_spec.id).glob("*.png"))


def test_real_chromium_connector_group_and_save_workflow(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "small-workflow.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan("T cell → Tumor cell", editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )

    status = engine.execute(bundle.figure_spec.id, operator)

    assert status == FigureStatus.AWAITING_CONFIRMATION
    assert {
        state["status"] for state in database.action_states(bundle.figure_spec.id)
    } == {"verified"}
    connectors = database.list_editor_elements(
        bundle.figure_spec.id, kind="connector"
    )
    assert len(connectors) == 1
    assert connectors[0]["payload"]["connector_type"] == "arrow"
    grouped = database.list_editor_elements(bundle.figure_spec.id, kind="asset")
    assert all(item["payload"].get("group_id") for item in grouped)
    assert cmd_verify_live_figure(
        Namespace(database=str(database.path), run_id=bundle.figure_spec.id)
    ) == 0


def test_real_chromium_rotates_asset_and_edits_label(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "transform.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan("T cell → Tumor cell", editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )
    try:
        first_entity_actions = [
            action
            for action in bundle.actions
            if action.action == ActionType.OPEN_EDITOR
            or action.arguments.get("entity_id") == "step_1"
            or action.arguments.get("element_id") == "step_1"
        ]
        for action in first_entity_actions:
            result = operator.execute(action)
            assert result.status == ActionStatus.VERIFIED
            database.record_action_result(bundle.figure_spec.id, result)

        asset = database.get_editor_element(bundle.figure_spec.id, "step_1")
        label = database.get_editor_element(bundle.figure_spec.id, "label_step_1")
        assert asset is not None
        assert label is not None

        rotate = GuiAction(
            id="action_rotate_test",
            figure_id=bundle.figure_spec.id,
            sequence=100,
            action=ActionType.ROTATE_ELEMENT,
            arguments={
                "element_id": "step_1",
                "element_kind": "asset",
                "target_degrees": 30,
            },
            expected_bbox=BoundingBox.model_validate(asset["bbox"]),
        )
        rotated = operator.execute(rotate)
        assert rotated.status == ActionStatus.VERIFIED
        assert rotated.metadata["observed_degrees"] == pytest.approx(30, abs=6)

        edit = GuiAction(
            id="action_edit_label_test",
            figure_id=bundle.figure_spec.id,
            sequence=101,
            action=ActionType.EDIT_TEXT,
            arguments={"element_id": "label_step_1", "text": "B cell"},
            expected_bbox=BoundingBox.model_validate(label["bbox"]),
        )
        edited = operator.execute(edit)
        assert edited.status == ActionStatus.VERIFIED
        assert edited.metadata["entered_text"] == "B cell"

        current_label = database.get_editor_element(
            bundle.figure_spec.id, "label_step_1"
        )
        assert current_label is not None
        label_bbox = BoundingBox.model_validate(current_label["bbox"])
        moved_bbox = BoundingBox(
            coordinate_space=label_bbox.coordinate_space,
            x=label_bbox.x + 24,
            y=label_bbox.y + 18,
            width=label_bbox.width,
            height=label_bbox.height,
        )
        move_label = GuiAction(
            id="action_move_label_test",
            figure_id=bundle.figure_spec.id,
            sequence=102,
            action=ActionType.MOVE_ELEMENT,
            arguments={"element_id": "label_step_1", "element_kind": "label"},
            expected_bbox=moved_bbox,
        )
        moved = operator.execute(move_label)
        assert moved.status == ActionStatus.VERIFIED

        moved_label = database.get_editor_element(
            bundle.figure_spec.id, "label_step_1"
        )
        assert moved_label is not None
        observed_moved_bbox = BoundingBox.model_validate(moved_label["bbox"])
        resized_bbox = BoundingBox(
            coordinate_space=observed_moved_bbox.coordinate_space,
            x=observed_moved_bbox.x,
            y=observed_moved_bbox.y,
            width=observed_moved_bbox.width + 28,
            height=observed_moved_bbox.height + 12,
        )
        resize_label = GuiAction(
            id="action_resize_label_test",
            figure_id=bundle.figure_spec.id,
            sequence=103,
            action=ActionType.RESIZE_ELEMENT,
            arguments={"element_id": "label_step_1", "element_kind": "label"},
            expected_bbox=resized_bbox,
        )
        resized = operator.execute(resize_label)
        assert resized.status == ActionStatus.VERIFIED, resized.model_dump(mode="json")
        assert resized.metadata["geometry_only_verification"] is True
    finally:
        operator.close()


def test_real_chromium_distributes_three_assets(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "distribution.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan(
        "T cell → Tumor cell → Antibody",
        editor_url=fixture_url(),
    )
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )
    try:
        setup_types = {
            ActionType.OPEN_EDITOR,
            ActionType.SEARCH_ASSET,
            ActionType.SELECT_ASSET,
            ActionType.DRAG_ASSET,
            ActionType.MOVE_ELEMENT,
            ActionType.RESIZE_ELEMENT,
        }
        for action in bundle.actions:
            if action.action not in setup_types:
                continue
            if action.action in {
                ActionType.MOVE_ELEMENT,
                ActionType.RESIZE_ELEMENT,
            } and action.arguments.get("element_kind") != "asset":
                continue
            result = operator.execute(action)
            assert result.status == ActionStatus.VERIFIED
            database.record_action_result(bundle.figure_spec.id, result)

        distribution = next(
            action
            for action in bundle.actions
            if action.action == ActionType.DISTRIBUTE_ELEMENTS
        )
        distributed = operator.execute(distribution)
        assert distributed.status == ActionStatus.VERIFIED
        assets = sorted(
            database.list_editor_elements(
                bundle.figure_spec.id,
                kind="asset",
            ),
            key=lambda item: item["bbox"]["x"],
        )
        assert len(assets) == 3
        gaps = [
            assets[index + 1]["bbox"]["x"]
            - assets[index]["bbox"]["x"]
            - assets[index]["bbox"]["width"]
            for index in range(2)
        ]
        assert abs(gaps[0] - gaps[1]) <= 10
    finally:
        operator.close()


def test_live_operator_captures_policy_block_evidence(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "policy.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan("T cell → Tumor cell", editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )
    try:
        opened = operator.execute(bundle.actions[0])
        assert opened.status == ActionStatus.VERIFIED
        operator.page.get_by_text("BioRender AI Generate", exact=True).click()

        capture = GuiAction(
            id="action_policy_capture_test",
            figure_id=bundle.figure_spec.id,
            sequence=200,
            action=ActionType.CAPTURE_CANVAS,
            arguments={},
        )
        with pytest.raises(PolicyBlocked) as error:
            operator.execute(capture)

        assert error.value.screenshot_path is not None
        assert Path(error.value.screenshot_path).exists()
    finally:
        operator.close()


def test_real_chromium_reconciliation_suppresses_duplicate_insert(
    tmp_path: Path,
) -> None:
    database = FigureDatabase(tmp_path / "recovery.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan("T cell → Tumor cell", editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )
    try:
        actions = bundle.actions
        open_action = actions[0]
        first_search = next(
            action
            for action in actions
            if action.action == ActionType.SEARCH_ASSET
        )
        first_select = next(
            action
            for action in actions
            if action.action == ActionType.SELECT_ASSET
        )
        first_drag = next(
            action
            for action in actions
            if action.action == ActionType.DRAG_ASSET
        )
        for action in (open_action, first_search, first_select):
            result = operator.execute(action)
            database.record_action_result(bundle.figure_spec.id, result)

        inserted = operator.execute(first_drag)
        assert inserted.status == ActionStatus.VERIFIED
        assert operator.page.locator(
            "[data-testid='canvas-element-asset']"
        ).count() == 1

        state = database.action_state(first_drag.id)
        assert state is not None
        assert state["status"] == "executing"
        checkpoint_result = GuiActionResult.model_validate(state["result"])
        reconciled = operator.reconcile(first_drag, checkpoint_result)

        assert reconciled.status == ActionStatus.VERIFIED
        assert reconciled.metadata["replayed"] is False
        assert operator.page.locator(
            "[data-testid='canvas-element-asset']"
        ).count() == 1
    finally:
        operator.close()


def test_real_chromium_reconciles_label_connector_and_group_without_replay(
    tmp_path: Path,
) -> None:
    database = FigureDatabase(tmp_path / "element-recovery.db")
    engine = WorkflowEngine(database)
    bundle = engine.plan("T cell → Tumor cell", editor_url=fixture_url())
    operator = LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        database=database,
        headed=False,
    )
    targets_seen: set[ActionType] = set()
    try:
        for action in bundle.actions:
            result = operator.execute(action)
            assert result.status == ActionStatus.VERIFIED
            is_target = (
                action.action == ActionType.ADD_TEXT
                and ActionType.ADD_TEXT not in targets_seen
            ) or (
                action.action == ActionType.CONNECT
                and ActionType.CONNECT not in targets_seen
            ) or (
                action.action == ActionType.GROUP_ELEMENTS
                and ActionType.GROUP_ELEMENTS not in targets_seen
            )
            if not is_target:
                database.record_action_result(bundle.figure_spec.id, result)
                continue

            if action.action == ActionType.ADD_TEXT:
                selector = "[data-testid='canvas-element-label']"
                identity_before = operator.page.locator(selector).count()
            elif action.action == ActionType.CONNECT:
                selector = "[data-testid='canvas-element-connector']"
                identity_before = operator.page.locator(selector).count()
            else:
                selector = "[data-group-id]"
                identity_before = operator.page.locator(selector).count()

            state = database.action_state(action.id)
            assert state is not None
            assert state["status"] == "executing"
            checkpoint_result = GuiActionResult.model_validate(state["result"])
            reconciled = operator.reconcile(action, checkpoint_result)

            assert reconciled.status == ActionStatus.VERIFIED
            assert reconciled.metadata["replayed"] is False
            assert reconciled.metadata["reconciliation_source"] == (
                "persisted_element_state"
            )
            assert operator.page.locator(selector).count() == identity_before
            database.record_action_result(bundle.figure_spec.id, reconciled)
            targets_seen.add(action.action)
            if targets_seen == {
                ActionType.ADD_TEXT,
                ActionType.CONNECT,
                ActionType.GROUP_ELEMENTS,
            }:
                break

        assert targets_seen == {
            ActionType.ADD_TEXT,
            ActionType.CONNECT,
            ActionType.GROUP_ELEMENTS,
        }
    finally:
        operator.close()
