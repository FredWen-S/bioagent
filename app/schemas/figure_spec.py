from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LayoutType(StrEnum):
    LINEAR = "linear"
    TWO_PANEL_COMPARISON = "two_panel_comparison"
    RADIAL = "radial"


class EntityCategory(StrEnum):
    CELL = "cell"
    PROTEIN = "protein"
    THERAPEUTIC = "therapeutic"
    ORGANELLE = "organelle"
    MOLECULE = "molecule"
    PROCESS = "process"
    LABEL = "label"
    GENERIC = "generic"


class RelationType(StrEnum):
    ACTIVATION = "activation"
    INHIBITION = "inhibition"
    BINDING = "binding"
    BLOCKING = "blocking"
    TRANSPORT = "transport"
    ASSOCIATION = "association"
    KILLING = "killing"
    FLOW = "flow"


class FigureStatus(StrEnum):
    CREATED = "created"
    PLANNED = "planned"
    VALIDATED = "validated"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    PAUSED_AUTHENTICATION = "paused_authentication"
    PAUSED_APPROVAL = "paused_approval"
    PAUSED_RECONCILIATION = "paused_reconciliation"
    BLOCKED = "blocked"
    FAILED = "failed"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    purpose: Literal["mechanism_figure", "experimental_workflow", "comparison", "other"]
    audience: Literal["research_presentation", "manuscript", "teaching", "other"]
    orientation: Literal["left_to_right", "top_to_bottom", "center_radial"]
    complexity: Literal["low", "medium", "high"] = "medium"
    required_sections: list[str] = Field(default_factory=list, max_length=6)
    preferred_language: Literal["English", "Chinese"] = "English"
    source_text: str = Field(min_length=1)


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    concept: str = Field(min_length=1, max_length=120)
    category: EntityCategory
    label: str = Field(min_length=1, max_length=120)
    region_id: str | None = None
    required: bool = True
    aliases: list[str] = Field(default_factory=list, max_length=8)


class Relation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    source: str
    target: str
    type: RelationType
    label: str | None = Field(default=None, max_length=120)
    region_id: str | None = None
    required: bool = True


class FigureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^figure_[a-zA-Z0-9_-]{3,64}$")
    title: str = Field(min_length=1, max_length=160)
    layout_type: LayoutType
    entities: list[Entity] = Field(min_length=1, max_length=15)
    relations: list[Relation] = Field(default_factory=list, max_length=30)
    required_concepts: list[str] = Field(default_factory=list, max_length=20)
    scientific_assumptions: list[str] = Field(default_factory=list, max_length=20)
    schema_version: Literal["1.0"] = "1.0"

    @model_validator(mode="after")
    def validate_graph(self) -> FigureSpec:
        entity_ids = [entity.id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity ids must be unique")
        relation_ids = [relation.id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("relation ids must be unique")
        known = set(entity_ids)
        for relation in self.relations:
            if relation.source not in known or relation.target not in known:
                raise ValueError(
                    f"relation {relation.id!r} references undefined entity: "
                    f"{relation.source!r} -> {relation.target!r}"
                )
            if relation.source == relation.target:
                raise ValueError(f"relation {relation.id!r} cannot connect an entity to itself")
        return self
