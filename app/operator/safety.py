from __future__ import annotations

from app.schemas.gui_action import ActionType, GuiAction


class UnsafeActionError(ValueError):
    pass


class ActionSafetyPolicy:
    """Allow-list policy: unmodeled BioRender actions cannot reach the operator."""

    allowed_actions = frozenset(ActionType)
    forbidden_argument_keys = frozenset(
        {
            "password",
            "mfa_code",
            "delete_project",
            "export",
            "publish",
            "share",
            "invite",
            "upgrade_subscription",
            "use_biorender_ai",
        }
    )

    def check(self, action: GuiAction, *, approved: bool = False) -> None:
        if action.action not in self.allowed_actions:
            raise UnsafeActionError(f"action {action.action!r} is not allow-listed")
        forbidden = self.forbidden_argument_keys.intersection(action.arguments)
        if forbidden:
            raise UnsafeActionError(f"forbidden GUI arguments: {sorted(forbidden)}")
        if action.requires_approval and not approved:
            raise UnsafeActionError(f"action {action.id!r} requires explicit approval")

