from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.gui_action import BoundingBox, ObservationSource


class CalibrationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    BLOCKED_BY_POLICY = "blocked_by_policy"


class ProbeStatus(StrEnum):
    PLANNED = "planned"
    EXECUTING = "executing"
    EXECUTED_UNVERIFIED = "executed_unverified"
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    FAILED = "failed"
    PAUSED_AUTHENTICATION = "paused_authentication"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED_PROBE = "completed_probe"


class Presence(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class LocatorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: str
    query: str
    confidence: float = Field(ge=0, le=1)


class CalibratedRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    found: bool
    bbox: BoundingBox | None = None
    locator: LocatorEvidence | None = None
    diagnostics: list[str] = Field(default_factory=list)


class VisibleModal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    bbox: BoundingBox | None = None
    classification: str
    blocking: bool


class UiCalibrationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    profile_id: str
    ui_profile_version: str
    created_at: str
    url: str
    viewport: BoundingBox
    editor_loaded: bool
    status: CalibrationStatus
    search_input: CalibratedRegion
    search_results_region: CalibratedRegion
    canvas: CalibratedRegion
    visible_modals: list[VisibleModal] = Field(default_factory=list)
    ai_controls: list[CalibratedRegion] = Field(default_factory=list)
    screenshot_path: str
    diagnostics: list[str] = Field(default_factory=list)


class AssetCandidateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    ordinal: int = Field(ge=0)
    text: str
    bbox: BoundingBox
    draggable: bool
    in_results_region: bool
    ordinary_asset_evidence: list[str] = Field(default_factory=list)
    rejected_reasons: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None


class InsertionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presence: Presence
    confidence: float = Field(ge=0, le=1)
    expected_bbox: BoundingBox
    observed_bbox: BoundingBox | None = None
    source: ObservationSource
    evidence_refs: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class ProbeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    profile_version: str
    editor_url: str
    query: str
    expected_bbox: BoundingBox
    baseline_canvas_path: str
    canvas_bbox: BoundingBox
    candidate: AssetCandidateRecord
    drag_action_id: str
    after_canvas_path: str | None = None
