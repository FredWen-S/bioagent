from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.figure_spec import LayoutType


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stay_on_canvas(self) -> Region:
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError(f"region {self.id!r} extends outside the normalized canvas")
        return self


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    region_id: str
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=0.5)
    height: float | None = Field(default=None, gt=0, le=0.5)


class LayoutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    figure_id: str
    orientation: Literal["landscape", "portrait"] = "landscape"
    layout_type: LayoutType
    regions: list[Region] = Field(min_length=1, max_length=8)
    placements: list[Placement] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def validate_references(self) -> LayoutSpec:
        region_ids = {region.id for region in self.regions}
        placement_ids = [placement.entity_id for placement in self.placements]
        if len(placement_ids) != len(set(placement_ids)):
            raise ValueError("each entity may have only one placement")
        for placement in self.placements:
            if placement.region_id not in region_ids:
                raise ValueError(
                    f"placement for {placement.entity_id!r} references unknown region "
                    f"{placement.region_id!r}"
                )
        return self

