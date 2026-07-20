class OperatorError(RuntimeError):
    error_type = "operator_error"

    def __init__(self, message: str, *, screenshot_path: str | None = None) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path


class AuthenticationRequired(OperatorError):
    error_type = "authentication_required"


class UiLayoutChanged(OperatorError):
    error_type = "ui_layout_changed"


class EditorPrepareFailed(OperatorError):
    """Prepare-phase failure (open_biorender_editor) that Resume alone cannot fix.

    ``subcode`` classifies the true root cause so the UI can surface a specific
    remediation instead of a generic "failed" state. ``requested_url`` and
    ``observed_url`` preserve exactly which URL was asked for and where the
    browser ended up — critical when the failure is a silent redirect or a
    marketing home page instead of the editor.
    """

    error_type = "editor_prepare_failed"

    ALLOWED_SUBCODES = frozenset(
        {
            "navigation_timeout",
            "navigation_error",
            "redirected_off_domain",
            "redirected_to_login",
            "canvas_not_found",
            "page_closed",
            "browser_profile_locked",
            "browser_launch_failed",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        subcode: str,
        requested_url: str | None = None,
        observed_url: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        if subcode not in self.ALLOWED_SUBCODES:
            raise ValueError(
                f"unknown EditorPrepareFailed subcode {subcode!r}; "
                f"expected one of {sorted(self.ALLOWED_SUBCODES)}"
            )
        super().__init__(message, screenshot_path=screenshot_path)
        self.subcode = subcode
        self.requested_url = requested_url
        self.observed_url = observed_url

    def structured_payload(self) -> dict[str, str | None]:
        return {
            "error_type": self.error_type,
            "subcode": self.subcode,
            "requested_url": self.requested_url,
            "observed_url": self.observed_url,
            "screenshot_path": self.screenshot_path,
        }


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
