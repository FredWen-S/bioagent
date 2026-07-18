from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.operator.action_planner import GuiActionPlanner
from app.operator.safety import ActionSafetyPolicy, UnsafeActionError
from app.planner.asset_search_planner import AssetSearchPlanner
from app.planner.figure_planner import ScientificFigurePlanner, UnsupportedScientificRequest
from app.planner.layout_planner import LayoutPlanner
from app.planner.requirement_parser import RequirementParser
from app.schemas.figure_spec import Entity, FigureSpec, Relation
from app.schemas.gui_action import ActionType, GuiAction
from app.verifier.scientific_guard import ScientificValidityGuard

PD1_REQUEST = """
制作一张双栏机制图。左侧表示未经治疗时，肿瘤细胞上的 PD-L1 与 T 细胞上的 PD-1
结合，从而抑制 T 细胞。右侧表示加入抗 PD-1 抗体后，PD-1/PD-L1 相互作用被阻断，
T 细胞恢复对肿瘤细胞的杀伤。
"""


def test_pd1_request_generates_valid_grounded_bundle() -> None:
    requirement = RequirementParser().parse(PD1_REQUEST)
    spec = ScientificFigurePlanner().plan(requirement)
    validation = ScientificValidityGuard().validate(spec, requirement)
    layout = LayoutPlanner().plan(spec)
    assets = AssetSearchPlanner().plan(spec)
    actions = GuiActionPlanner().compile(spec, layout, assets)

    assert spec.layout_type.value == "two_panel_comparison"
    assert len(spec.entities) == 9
    assert len(spec.relations) == 5
    assert validation.passed is True
    assert {region.id for region in layout.regions} == {
        "without_treatment",
        "anti_pd1_treatment",
    }
    assert len(layout.placements) == len(spec.entities)
    assert all(0 <= placement.x <= 1 and 0 <= placement.y <= 1 for placement in layout.placements)
    assert len(assets.items) == len(spec.entities)
    assert all(1 <= len(item.search_terms) <= 5 for item in assets.items)
    assert actions[0].action.value == "open_biorender_editor"
    assert actions[-1].action.value == "save_project"
    assert all(action.sequence == index for index, action in enumerate(actions))
    action_types = {action.action for action in actions}
    assert {
        ActionType.SEARCH_ASSET,
        ActionType.DRAG_ASSET,
        ActionType.MOVE_ELEMENT,
        ActionType.RESIZE_ELEMENT,
        ActionType.ADD_TEXT,
        ActionType.CONNECT,
        ActionType.GROUP_ELEMENTS,
        ActionType.ALIGN_ELEMENTS,
        ActionType.CAPTURE_CANVAS,
        ActionType.SAVE_PROJECT,
    } <= action_types
    policy = ActionSafetyPolicy()
    for action in actions:
        policy.check(action)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ai_generate", True),
        ("menu", "Create with AI"),
        ("dialog", "Use 1 AI credit to Generate Figure"),
        ("operation", "AI Edit"),
    ],
)
def test_action_policy_hard_blocks_biorender_ai(key: str, value: object) -> None:
    action = GuiAction(
        id=f"action_unsafe_{key}",
        figure_id="figure_policy",
        sequence=0,
        action=ActionType.CAPTURE_CANVAS,
        arguments={key: value},
    )

    with pytest.raises(UnsafeActionError, match="forbidden"):
        ActionSafetyPolicy().check(action)


def test_figure_spec_rejects_undefined_relation_reference() -> None:
    with pytest.raises(ValidationError, match="undefined entity"):
        FigureSpec(
            id="figure_invalid",
            title="Invalid",
            layout_type="linear",
            entities=[Entity(id="node_a", concept="A", category="process", label="A")],
            relations=[
                Relation(
                    id="bad_relation",
                    source="node_a",
                    target="missing_node",
                    type="flow",
                )
            ],
        )


def test_unstructured_unknown_mechanism_is_not_invented() -> None:
    requirement = RequirementParser().parse("画一张复杂而正确的未知信号通路图")
    with pytest.raises(UnsupportedScientificRequest, match="deterministic MVP"):
        ScientificFigurePlanner().plan(requirement)


def test_explicit_arrow_flow_is_supported_without_llm() -> None:
    requirement = RequirementParser().parse("Sample → Centrifugation → Supernatant")
    spec = ScientificFigurePlanner().plan(requirement)
    assert [entity.label for entity in spec.entities] == [
        "Sample",
        "Centrifugation",
        "Supernatant",
    ]
    assert len(spec.relations) == 2


def test_repeated_requests_create_distinct_figure_ids() -> None:
    requirement = RequirementParser().parse(PD1_REQUEST)
    first = ScientificFigurePlanner().plan(requirement)
    second = ScientificFigurePlanner().plan(requirement)
    assert first.id != second.id
