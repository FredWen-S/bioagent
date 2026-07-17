from __future__ import annotations

import re

from app.schemas.figure_spec import Requirement


class RequirementParser:
    """Deterministic MVP parser.

    This parser deliberately handles only stable task constraints. Scientific entity
    extraction lives in ``ScientificFigurePlanner`` and can later be replaced by an
    LLM provider that returns the same strict schema.
    """

    def parse(self, text: str) -> Requirement:
        cleaned = " ".join(text.strip().split())
        if not cleaned:
            raise ValueError("figure request cannot be empty")
        lowered = cleaned.casefold()

        if any(token in lowered for token in ("双栏", "对比", "before", "after", "control")):
            orientation = "left_to_right"
            purpose = "comparison"
            sections = self._comparison_sections(lowered)
        elif any(token in lowered for token in ("中心", "辐射", "microenvironment")):
            orientation = "center_radial"
            purpose = "mechanism_figure"
            sections = ["main"]
        elif any(token in lowered for token in ("流程", "workflow", "protocol", "methods")):
            orientation = "left_to_right"
            purpose = "experimental_workflow"
            sections = ["main"]
        else:
            orientation = "left_to_right"
            purpose = "mechanism_figure"
            sections = ["main"]

        language = "Chinese" if self._contains_cjk(cleaned) else "English"
        complexity = "high" if len(cleaned) > 360 else "medium" if len(cleaned) > 100 else "low"
        title = self._title_for(lowered, purpose)
        return Requirement(
            title=title,
            purpose=purpose,
            audience="research_presentation",
            orientation=orientation,
            complexity=complexity,
            required_sections=sections,
            preferred_language=language,
            source_text=cleaned,
        )

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]", text))

    @staticmethod
    def _comparison_sections(lowered: str) -> list[str]:
        if "pd-1" in lowered or "pd1" in lowered:
            return ["without_treatment", "anti_pd1_treatment"]
        if "control" in lowered or "对照" in lowered:
            return ["control", "treatment"]
        return ["before", "after"]

    @staticmethod
    def _title_for(lowered: str, purpose: str) -> str:
        if ("pd-1" in lowered or "pd1" in lowered) and (
            "pd-l1" in lowered or "pdl1" in lowered
        ):
            return "PD-1/PD-L1 Immune Checkpoint Blockade"
        titles = {
            "experimental_workflow": "Experimental Workflow",
            "comparison": "Scientific Comparison",
            "mechanism_figure": "Scientific Mechanism",
        }
        return titles.get(purpose, "Scientific Figure")

