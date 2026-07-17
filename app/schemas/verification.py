from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: IssueSeverity
    type: str
    message: str
    entity_id: str | None = None
    relation_id: str | None = None


class ScientificValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[VerificationIssue] = Field(default_factory=list)


class VisualVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage: float = Field(ge=0, le=1)
    layout_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    repair_required: bool
    issues: list[VerificationIssue] = Field(default_factory=list)

