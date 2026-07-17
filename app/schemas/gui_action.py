from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionType(StrEnum):
    OPEN_EDITOR = "open_biorender_editor"
    SEARCH_ASSET = "search_asset"
    SELECT_ASSET = "select_asset_candidate"
    DRAG_ASSET = "drag_selected_asset"
    ADD_TEXT = "add_text"
    CONNECT = "connect_elements"
    MOVE_ELEMENT = "move_element"
    CAPTURE_CANVAS = "capture_canvas"
    SAVE_PROJECT = "save_project"


class ActionStatus(StrEnum):
    PLANNED = "planned"
    EXECUTING = "executing"
    EXECUTED_UNVERIFIED = "executed_unverified"
    VERIFIED = "verified"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    SKIPPED = "skipped"
    PAUSED = "paused"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CoordinateSpace(StrEnum):
    NORMALIZED_CANVAS = "normalized_canvas"
    CANVAS_PIXELS = "canvas_pixels"
    VIEWPORT_PIXELS = "viewport_pixels"


class ObservationSource(StrEnum):
    DOM = "dom"
    ACCESSIBILITY = "accessibility"
    SCREENSHOT_PIXEL_DIFF = "screenshot_pixel_diff"
    VISION_MODEL = "vision_model"
    BACKEND_OBJECT = "backend_object"
    MANUAL = "manual"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    coordinate_space: CoordinateSpace


class GuiAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^action_[a-zA-Z0-9_-]{3,80}$")
    figure_id: str
    sequence: int = Field(ge=0)
    action: ActionType
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=3)
    requires_screenshot: bool = True
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    expected_bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def high_risk_requires_approval(self) -> "GuiAction":
        if self.risk_level == RiskLevel.HIGH and not self.requires_approval:
            raise ValueError("high-risk actions must require explicit approval")
        return self


class GuiActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    status: ActionStatus
    attempt: int = Field(ge=1)
    error_type: str | None = None
    message: str | None = None
    screenshot_path: str | None = None
    expected_bbox: BoundingBox | None = None
    observed_bbox: BoundingBox | None = None
    observation_confidence: float | None = Field(default=None, ge=0, le=1)
    observation_source: ObservationSource | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def observed_geometry_requires_evidence(self) -> "GuiActionResult":
        if self.observed_bbox is not None:
            if self.observation_confidence is None or self.observation_source is None:
                raise ValueError(
                    "observed_bbox requires observation_confidence and observation_source"
                )
        return self
