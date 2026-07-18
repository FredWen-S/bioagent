class OperatorError(RuntimeError):
    error_type = "operator_error"

    def __init__(self, message: str, *, screenshot_path: str | None = None) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path


class AuthenticationRequired(OperatorError):
    error_type = "authentication_required"


class UiLayoutChanged(OperatorError):
    error_type = "ui_layout_changed"


class SearchNoResult(OperatorError):
    error_type = "search_no_result"


class DragDropFailed(OperatorError):
    error_type = "drag_drop_failed"


class UnsupportedLiveAction(OperatorError):
    error_type = "unsupported_live_action"


class CalibrationFailed(OperatorError):
    error_type = "ui_calibration_failed"

    def __init__(self, message: str, *, profile_path: str | None = None) -> None:
        super().__init__(message)
        self.profile_path = profile_path


class PolicyBlocked(OperatorError):
    error_type = "blocked_by_policy"


class CandidateIdentityUnclear(OperatorError):
    error_type = "candidate_identity_unclear"


class ObservationUncertain(OperatorError):
    error_type = "observation_unknown"


class ReconciliationRequired(OperatorError):
    error_type = "reconciliation_required"


class UnexpectedModal(OperatorError):
    error_type = "unexpected_modal"
