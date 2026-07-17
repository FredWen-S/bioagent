from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.asset_plan import AssetSearchPlan
from app.schemas.figure_spec import FigureSpec, FigureStatus, Requirement
from app.schemas.gui_action import GuiAction
from app.schemas.layout_spec import LayoutSpec
from app.schemas.verification import ScientificValidation


class PlanningBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: Requirement
    figure_spec: FigureSpec
    asset_plan: AssetSearchPlan
    layout_spec: LayoutSpec
    scientific_validation: ScientificValidation
    actions: list[GuiAction]
    status: FigureStatus

