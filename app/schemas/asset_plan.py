from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AssetMatchStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    DEGRADED = "degraded_asset"
    NEEDS_HUMAN = "needs_human_selection"


class AssetSearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    concept: str
    search_terms: list[str] = Field(min_length=1, max_length=5)
    preferred_visual_features: list[str] = Field(default_factory=list, max_length=6)
    status: AssetMatchStatus = AssetMatchStatus.PENDING
    selected_query: str | None = None
    selected_candidate_id: str | None = None


class AssetSearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_id: str
    items: list[AssetSearchItem] = Field(min_length=1, max_length=15)
