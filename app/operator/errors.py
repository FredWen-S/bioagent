class OperatorError(RuntimeError):
    error_type = "operator_error"


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

