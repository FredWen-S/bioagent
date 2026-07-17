from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.operator.biorender.observer import PixelDiffInsertionObserver
from app.schemas.biorender_probe import InsertionObservation, Presence, ProbeCheckpoint


class ReconciliationDecision(StrEnum):
    ALREADY_VERIFIED = "already_verified"
    SAFE_TO_RETRY = "safe_to_retry"
    PAUSE_UNKNOWN = "pause_unknown"


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReconciliationDecision
    observation: InsertionObservation | None = None
    reason: str


class ProbeReconciler:
    def __init__(self, observer: PixelDiffInsertionObserver | None = None) -> None:
        self.observer = observer or PixelDiffInsertionObserver()

    def reconcile(
        self,
        checkpoint: ProbeCheckpoint,
        *,
        current_profile_version: str,
        current_canvas_path: str,
    ) -> ReconciliationResult:
        if current_profile_version != checkpoint.profile_version:
            return ReconciliationResult(
                decision=ReconciliationDecision.PAUSE_UNKNOWN,
                reason=(
                    "saved UI calibration profile does not match the current editor; "
                    "old coordinates and locators cannot be trusted"
                ),
            )
        observation = self.observer.observe(
            baseline_path=checkpoint.baseline_canvas_path,
            current_path=current_canvas_path,
            canvas_bbox=checkpoint.canvas_bbox,
            expected_bbox=checkpoint.expected_bbox,
        )
        if observation.presence == Presence.PRESENT and observation.confidence >= 0.75:
            return ReconciliationResult(
                decision=ReconciliationDecision.ALREADY_VERIFIED,
                observation=observation,
                reason="target asset is already observable; drag replay is suppressed",
            )
        if observation.presence == Presence.ABSENT and observation.confidence >= 0.9:
            return ReconciliationResult(
                decision=ReconciliationDecision.SAFE_TO_RETRY,
                observation=observation,
                reason="target asset is confidently absent; one drag retry is safe",
            )
        return ReconciliationResult(
            decision=ReconciliationDecision.PAUSE_UNKNOWN,
            observation=observation,
            reason="current canvas cannot be reconciled with enough confidence",
        )

