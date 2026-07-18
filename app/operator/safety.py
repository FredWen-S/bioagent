from __future__ import annotations

import re

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
            "upgrade",
            "subscribe",
            "purchase",
            "download",
            "template_generate",
            "use_biorender_ai",
            "ai_generate",
            "ai_edit",
            "ai_credits",
            "create_with_ai",
            "generate_figure",
        }
    )
    forbidden_value_patterns = (
        re.compile(r"\bbiorender\s+ai\b", re.IGNORECASE),
        re.compile(r"\bcreate\s+(?:a\s+)?(?:figure\s+)?with\s+ai\b", re.IGNORECASE),
        re.compile(r"\bgenerate\s+(?:a\s+)?figure\b", re.IGNORECASE),
        re.compile(r"\bai\s+(?:generate|edit|assistant|credits?)\b", re.IGNORECASE),
        re.compile(r"\b(?:upgrade|subscribe|purchase)\b", re.IGNORECASE),
        re.compile(r"\b(?:export|download|publish|share)\b", re.IGNORECASE),
        re.compile(r"\b(?:generate|create)\s+(?:from\s+)?template\b", re.IGNORECASE),
    )

    def check(self, action: GuiAction, *, approved: bool = False) -> None:
        if action.action not in self.allowed_actions:
            raise UnsafeActionError(f"action {action.action!r} is not allow-listed")
        forbidden = self.forbidden_argument_keys.intersection(action.arguments)
        if forbidden:
            raise UnsafeActionError(f"forbidden GUI arguments: {sorted(forbidden)}")
        for value in self._strings(action.arguments):
            if any(pattern.search(value) for pattern in self.forbidden_value_patterns):
                raise UnsafeActionError(
                    "BioRender AI Generate/AI credits content is forbidden by policy"
                )
        if action.requires_approval and not approved:
            raise UnsafeActionError(f"action {action.id!r} requires explicit approval")

    @classmethod
    def _strings(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [
                item
                for nested in value.values()
                for item in cls._strings(nested)
            ]
        if isinstance(value, (list, tuple, set)):
            return [item for nested in value for item in cls._strings(nested)]
        return []
