from __future__ import annotations

from app.planner.figure_planner import ScientificFigurePlanner
from app.planner.requirement_parser import RequirementParser
from app.schemas.figure_spec import FigureSpec
from app.verifier.scientific_guard import ScientificValidityGuard


def test_guard_detects_missing_anti_pd1_blocking_relation() -> None:
    request = (
        "PD-1 与 PD-L1 结合并抑制 T 细胞；加入 anti-PD-1 后阻断该过程，"
        "恢复 T 细胞对肿瘤的杀伤。"
    )
    requirement = RequirementParser().parse(request)
    valid_spec = ScientificFigurePlanner().plan(requirement)
    payload = valid_spec.model_dump(mode="json")
    payload["relations"] = [
        relation
        for relation in payload["relations"]
        if relation["type"] != "blocking"
    ]
    broken_spec = FigureSpec.model_validate(payload)

    result = ScientificValidityGuard().validate(broken_spec, requirement)

    assert result.passed is False
    assert any(issue.type == "missing_required_relation" for issue in result.issues)

