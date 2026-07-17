from __future__ import annotations

from typing import Protocol

from app.schemas.gui_action import GuiAction, GuiActionResult


class GuiOperator(Protocol):
    def execute(self, action: GuiAction, attempt: int = 1) -> GuiActionResult: ...

    def close(self) -> None: ...

