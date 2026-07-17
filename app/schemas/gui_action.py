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
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    PAUSED = "paused"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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
    observed_bbox: tuple[int, int, int, int] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

