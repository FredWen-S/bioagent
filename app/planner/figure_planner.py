from __future__ import annotations

import re
import uuid

from app.schemas.figure_spec import (
    Entity,
    EntityCategory,
    FigureSpec,
    LayoutType,
    Relation,
    RelationType,
    Requirement,
)


class UnsupportedScientificRequest(ValueError):
    """Raised when deterministic planning would invent scientific content."""


class ScientificFigurePlanner:
    """Produces a strict figure graph without performing any GUI actions."""

    def plan(self, requirement: Requirement) -> FigureSpec:
        lowered = requirement.source_text.casefold()
        figure_id = self._figure_id(requirement.source_text)
        if self._is_pd1_request(lowered):
            return self._plan_pd1(requirement, figure_id)
        if "->" in requirement.source_text or "→" in requirement.source_text:
            return self._plan_explicit_flow(requirement, figure_id)
        raise UnsupportedScientificRequest(
            "The deterministic MVP only plans the bundled PD-1/PD-L1 mechanism or an explicit "
            "A -> B -> C flow. Provide a FigureSpec or configure an LLM planner "
            "for other mechanisms."
        )

    @staticmethod
    def _figure_id(text: str) -> str:
        del text  # IDs are intentionally unique so repeated requests create new audit records.
        return f"figure_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _is_pd1_request(lowered: str) -> bool:
        has_pd1 = "pd-1" in lowered or "pd1" in lowered
        has_pdl1 = "pd-l1" in lowered or "pdl1" in lowered
        return has_pd1 and has_pdl1

    @staticmethod
    def _plan_pd1(requirement: Requirement, figure_id: str) -> FigureSpec:
        before = "without_treatment"
        after = "anti_pd1_treatment"
        entities = [
            Entity(
                id="t_cell_before",
                concept="T cell",
                category="cell",
                label="T cell",
                region_id=before,
            ),
            Entity(
                id="tumor_cell_before",
                concept="Tumor cell",
                category="cell",
                label="Tumor cell",
                region_id=before,
            ),
            Entity(
                id="pd1_before",
                concept="PD-1 receptor",
                category="protein",
                label="PD-1",
                region_id=before,
            ),
            Entity(
                id="pdl1_before",
                concept="PD-L1 ligand",
                category="protein",
                label="PD-L1",
                region_id=before,
            ),
            Entity(
                id="t_cell_after",
                concept="T cell",
                category="cell",
                label="T cell",
                region_id=after,
            ),
            Entity(
                id="tumor_cell_after",
                concept="Tumor cell",
                category="cell",
                label="Tumor cell",
                region_id=after,
            ),
            Entity(
                id="pd1_after",
                concept="PD-1 receptor",
                category="protein",
                label="PD-1",
                region_id=after,
            ),
            Entity(
                id="pdl1_after",
                concept="PD-L1 ligand",
                category="protein",
                label="PD-L1",
                region_id=after,
            ),
            Entity(
                id="antibody_after",
                concept="Anti-PD-1 antibody",
                category="therapeutic",
                label="Anti-PD-1",
                region_id=after,
            ),
        ]
        relations = [
            Relation(
                id="pd1_pdl1_binding_before",
                source="pd1_before",
                target="pdl1_before",
                type="binding",
                label="PD-1 binds PD-L1",
                region_id=before,
            ),
            Relation(
                id="t_cell_inhibition_before",
                source="tumor_cell_before",
                target="t_cell_before",
                type="inhibition",
                label="T-cell inhibition",
                region_id=before,
            ),
            Relation(
                id="antibody_blocks_pd1_after",
                source="antibody_after",
                target="pd1_after",
                type="blocking",
                label="Blocks PD-1",
                region_id=after,
            ),
            Relation(
                id="pd1_pdl1_blocked_after",
                source="pd1_after",
                target="pdl1_after",
                type="blocking",
                label="Interaction blocked",
                region_id=after,
            ),
            Relation(
                id="t_cell_killing_after",
                source="t_cell_after",
                target="tumor_cell_after",
                type="killing",
                label="Tumor killing",
                region_id=after,
            ),
        ]
        return FigureSpec(
            id=figure_id,
            title=requirement.title,
            layout_type=LayoutType.TWO_PANEL_COMPARISON,
            entities=entities,
            relations=relations,
            required_concepts=["T cell", "Tumor cell", "PD-1", "PD-L1", "Anti-PD-1"],
            scientific_assumptions=[
                "The request explicitly states PD-1/PD-L1-mediated T-cell inhibition.",
                "The request explicitly states restoration of tumor killing after "
                "anti-PD-1 treatment.",
            ],
        )

    @staticmethod
    def _plan_explicit_flow(requirement: Requirement, figure_id: str) -> FigureSpec:
        parts = [
            re.sub(r"^[\s\d.]+|[\s.;,，。]+$", "", part)
            for part in re.split(r"\s*(?:->|→)\s*", requirement.source_text)
        ]
        parts = [part for part in parts if part]
        if not 2 <= len(parts) <= 15:
            raise UnsupportedScientificRequest(
                "an explicit flow must contain between 2 and 15 nodes"
            )
        entities = [
            Entity(
                id=f"step_{index + 1}",
                concept=part[:120],
                category=EntityCategory.PROCESS,
                label=part[:120],
                region_id="main",
            )
            for index, part in enumerate(parts)
        ]
        relations = [
            Relation(
                id=f"flow_{index + 1}",
                source=entities[index].id,
                target=entities[index + 1].id,
                type=RelationType.FLOW,
                region_id="main",
            )
            for index in range(len(entities) - 1)
        ]
        return FigureSpec(
            id=figure_id,
            title=requirement.title,
            layout_type=LayoutType.LINEAR,
            entities=entities,
            relations=relations,
            required_concepts=[entity.concept for entity in entities],
            scientific_assumptions=[
                "Relations were copied from the user's explicit arrow sequence."
            ],
        )
