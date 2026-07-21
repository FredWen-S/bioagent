from typing import Any


class OperatorError(RuntimeError):
    error_type = "operator_error"

    def __init__(self, message: str, *, screenshot_path: str | None = None) -> None:
        super().__init__(message)
        self.screenshot_path = screenshot_path


class AuthenticationRequired(OperatorError):
    error_type = "authentication_required"


class EditorPrepareFailed(OperatorError):
    """Raised while the BioRender editor is being prepared but never becomes ready.

    The ``subcode`` distinguishes the observed failure mode so callers can react
    differently: a genuine timeout waiting for the canvas is classified as
    ``canvas_not_found``, while redirects/closures are surfaced as their own
    subcodes and are NOT collapsed into ``canvas_not_found``.
    """

    error_type = "editor_prepare_failed"

    VALID_SUBCODES = frozenset(
        {
            "canvas_not_found",
            "redirected_to_login",
            "redirected_off_domain",
            "page_closed",
            "navigation_timeout",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        subcode: str,
        metadata: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        if subcode not in self.VALID_SUBCODES:
            raise ValueError(
                f"unknown EditorPrepareFailed subcode: {subcode!r}; "
                f"expected one of {sorted(self.VALID_SUBCODES)}"
            )
        super().__init__(message, screenshot_path=screenshot_path)
        self.subcode = subcode
        self.metadata: dict[str, Any] = dict(metadata or {})
        if screenshot_path is not None:
            self.metadata.setdefault("screenshot_path", screenshot_path)


class UiLayoutChanged(OperatorError):
    error_type = "ui_layout_changed"


class SearchNoResult(OperatorError):
    error_type = "search_no_result"


class SearchActionFailed(OperatorError):
    VALID_SUBCODES = frozenset(
        {
            "search_ui_not_found",
            "search_input_not_editable",
            "search_submit_failed",
            "search_results_timeout",
            "search_no_results",
            "search_rate_limited",
            "page_closed",
            "redirected_to_login",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        subcode: str,
        diagnostics: dict[str, Any] | None = None,
        retryable: bool = False,
        screenshot_path: str | None = None,
    ) -> None:
        if subcode not in self.VALID_SUBCODES:
            raise ValueError(f"unknown search failure subcode: {subcode!r}")
        super().__init__(message, screenshot_path=screenshot_path)
        self.error_type = subcode
        self.subcode = subcode
        self.diagnostics = dict(diagnostics or {})
        self.retryable = retryable


class SafeStopRequested(OperatorError):
    error_type = "safe_stop_requested"


class DragDropFailed(OperatorError):
    error_type = "drag_drop_failed"


class UnsupportedLiveAction(OperatorError):
    error_type = "unsupported_live_action"


class CalibrationFailed(OperatorError):
    error_type = "ui_calibration_failed"

    def __init__(
        self,
        message: str,
        *,
        profile_path: str | None = None,
        missing_anchors: list[str] | None = None,
        anchor_diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.profile_path = profile_path
        self.missing_anchors = list(missing_anchors or [])
        self.anchor_diagnostics = list(anchor_diagnostics or [])


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
