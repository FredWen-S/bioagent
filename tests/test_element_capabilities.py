from __future__ import annotations

from argparse import Namespace
from collections import Counter
from pathlib import Path

import pytest

from app.cli import cmd_inspect_elements, cmd_verify_live_figure
from app.operator.biorender.observer import (
    ConnectorGeometryObserver,
    LabelAssociationObserver,
    LayoutQualityObserver,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.errors import PolicyBlocked
from app.schemas.gui_action import BoundingBox, CoordinateSpace
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine

PD1_REQUEST = (
    "制作双栏对比：未经治疗时 PD-1/PD-L1 结合并抑制 T 细胞；"
    "anti-PD-1 treatment 阻断相互作用，T 细胞杀伤 Tumor cell。"
)


def box(x: float, y: float, width: float, height: float) -> BoundingBox:
    return BoundingBox(
        x=x,
        y=y,
        width=width,
        height=height,
        coordinate_space=CoordinateSpace.VIEWPORT_PIXELS,
    )


def test_pd1_has_element_level_requirements_and_typed_actions(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "elements.db")
    bundle = WorkflowEngine(database).plan(PD1_REQUEST)
    requirements = database.list_element_requirements(bundle.figure_spec.id)

    assert len(bundle.actions) == 94
    assert Counter(item["kind"] for item in requirements) == {
        "asset": 9,
        "label": 9,
        "connector": 5,
        "group": 9,
        "alignment": 4,
        "distribution": 1,
        "region": 2,
        "z_order": 1,
        "save_state": 1,
    }
    logical_ids = [item["logical_element_id"] for item in requirements]
    assert len(logical_ids) == len(set(logical_ids))

    action_counts = Counter(action.action.value for action in bundle.actions)
    assert action_counts["search_asset"] == 9
    assert action_counts["drag_selected_asset"] == 9
    assert action_counts["add_text"] == 9
    assert action_counts["move_element"] == 18
    assert action_counts["resize_element"] == 18
    assert action_counts["connect_elements"] == 5
    assert action_counts["group_elements"] == 9
    assert action_counts["distribute_elements"] == 1

    connector_requirements = {
        item["logical_element_id"]: item["requirement"]
        for item in requirements
        if item["kind"] == "connector"
    }
    assert connector_requirements["t_cell_inhibition_before"]["connector_type"] == "t_bar"
    assert connector_requirements["antibody_blocks_pd1_after"]["connector_type"] == "t_bar"
    assert connector_requirements["t_cell_killing_after"]["connector_type"] == "arrow"


def test_duplicate_label_text_is_associated_by_target_proximity() -> None:
    observer = LabelAssociationObserver()
    canvas = box(0, 0, 800, 600)
    assets = {
        "t_cell_before": box(100, 100, 80, 80),
        "t_cell_after": box(500, 100, 80, 80),
    }
    result = observer.observe(
        expected_text="T cell",
        observed_text="T cell",
        label_bbox=box(505, 185, 70, 24),
        target_element_id="t_cell_after",
        target_bbox=assets["t_cell_after"],
        asset_boxes=assets,
        canvas_bbox=canvas,
        truncated=False,
    )

    assert result["passed"] is True
    assert result["nearest_element_id"] == "t_cell_after"
    assert result["association_confidence"] >= 0.9


@pytest.mark.parametrize(
    ("expected_type", "observed_type", "passed"),
    [("arrow", "arrow", True), ("inhibition", "t_bar", True), ("t_bar", "arrow", False)],
)
def test_connector_semantics_require_type_and_direction(
    expected_type: str,
    observed_type: str,
    passed: bool,
) -> None:
    result = ConnectorGeometryObserver().observe(
        expected_type=expected_type,
        observed_type=observed_type,
        source_id="source",
        target_id="target",
        source_bbox=box(100, 100, 80, 80),
        target_bbox=box(300, 100, 80, 80),
        observed_start=(140, 140),
        observed_end=(340, 140),
        unrelated_boxes={},
        label_boxes={},
    )

    assert result["passed"] is passed
    assert result["direction_verified"] is True


def test_connector_wrong_endpoint_and_label_collision_are_rejected() -> None:
    result = ConnectorGeometryObserver().observe(
        expected_type="arrow",
        observed_type="arrow",
        source_id="source",
        target_id="target",
        source_bbox=box(100, 100, 80, 80),
        target_bbox=box(300, 100, 80, 80),
        observed_start=(340, 140),
        observed_end=(140, 140),
        unrelated_boxes={},
        label_boxes={"label_other": box(210, 125, 50, 30)},
    )

    assert result["passed"] is False
    assert result["direction_verified"] is False
    assert result["label_collisions"] == ["label_other"]


def test_layout_quality_reports_overlap_bounds_and_unknown_z_order() -> None:
    elements = [
        {
            "element_id": "a",
            "kind": "asset",
            "bbox": box(80, 80, 90, 90).model_dump(mode="json"),
            "payload": {"z_index": 2},
            "verification": {},
        },
        {
            "element_id": "b",
            "kind": "asset",
            "bbox": box(120, 100, 90, 90).model_dump(mode="json"),
            "payload": {"z_index": 2},
            "verification": {},
        },
        {
            "element_id": "outside",
            "kind": "asset",
            "bbox": box(760, 560, 80, 80).model_dump(mode="json"),
            "payload": {"z_index": 2},
            "verification": {},
        },
        {
            "element_id": "relation",
            "kind": "connector",
            "bbox": box(140, 130, 170, 5).model_dump(mode="json"),
            "payload": {},
            "verification": {
                "connector": {"route_verified": True, "type_verified": True}
            },
        },
    ]
    result = LayoutQualityObserver().observe(
        canvas_bbox=box(0, 0, 800, 600),
        elements=elements,
        layout={"placements": [], "regions": []},
        spec={"entities": []},
    )

    assert result["passed"] is False
    assert result["overlap_count"] == 1
    assert result["out_of_bounds_count"] == 1
    assert result["z_order_unknown"] == ["relation"]


@pytest.mark.parametrize(
    "text",
    ("Upgrade", "Subscribe", "Purchase", "Use 1 AI Credits", "Create with AI"),
)
def test_policy_guard_blocks_paid_and_ai_targets(text: str) -> None:
    finding = BioRenderPolicyGuard.classify_text(text, candidate_context=True)
    assert finding is not None
    assert finding.blocking is True


def test_element_inspection_is_read_only_and_verify_requires_real_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "inspect.db"
    database = FigureDatabase(database_path)
    bundle = WorkflowEngine(database).plan(PD1_REQUEST)
    args = Namespace(database=str(database_path), run_id=bundle.figure_spec.id)

    assert cmd_inspect_elements(args) == 0
    inspect_output = capsys.readouterr().out
    assert '"total": 41' in inspect_output
    assert '"observed_records": 0' in inspect_output
    assert cmd_verify_live_figure(args) == 2
    verify_output = capsys.readouterr().out
    assert '"passed": false' in verify_output
    assert '"read_only": true' in verify_output


def test_assert_query_allowed_never_falls_back_to_ai() -> None:
    with pytest.raises(PolicyBlocked):
        BioRenderPolicyGuard().assert_query_allowed("Generate Figure with AI")
