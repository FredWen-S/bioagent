"""Regression tests for the BioRender editor readiness wait.

The old code slept ~1.5s then declared ``canvas_not_found``, which caused
``open_biorender_editor`` to fail while the loading placeholder was still
showing. The current contract is: wait up to a configurable timeout
(30s in production), poll cheaply, and return as soon as the canvas
appears. Distinct failure modes must remain distinct classifications.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.operator.errors import EditorPrepareFailed
from app.operator.playwright_live import (
    CANVAS_LOCATORS,
    DEFAULT_EDITOR_READY_TIMEOUT_SECONDS,
    LivePlaywrightOperator,
)
from app.schemas.gui_action import ActionType, GuiAction
from tests.mocks.fake_playwright import FakeElement, FakePage

CANVAS_SELECTOR = CANVAS_LOCATORS[0].query


def _canvas_element() -> FakeElement:
    return FakeElement(
        bbox={"x": 320, "y": 80, "width": 1000, "height": 700},
    )


def _open_editor_action(url: str = "https://app.biorender.com/editor/fake") -> GuiAction:
    return GuiAction(
        id="action_open_editor_fake",
        figure_id="figure_editor_ready",
        sequence=0,
        action=ActionType.OPEN_EDITOR,
        arguments={"url": url},
    )


class VirtualClock:
    """Deterministic monotonic clock advanced only by explicit sleep calls."""

    def __init__(self) -> None:
        self.now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


def _install_virtual_clock(operator: LivePlaywrightOperator) -> VirtualClock:
    clock = VirtualClock()
    operator._clock = clock.read
    operator._sleep = clock.sleep
    return clock


def _build_operator(
    tmp_path: Path,
    *,
    timeout: float = 30.0,
    poll: float = 0.1,
    diagnostic: float = 5.0,
) -> LivePlaywrightOperator:
    return LivePlaywrightOperator(
        profile_dir=tmp_path / "profile",
        evidence_dir=tmp_path / "evidence",
        headed=False,
        editor_ready_timeout_seconds=timeout,
        editor_ready_poll_interval_seconds=poll,
        editor_ready_diagnostic_interval_seconds=diagnostic,
    )


class _ProgrammableFakePage(FakePage):
    """FakePage that mutates state per poll based on caller-supplied hooks."""

    def __init__(
        self,
        *,
        base_selectors: dict[str, list[FakeElement]] | None = None,
        canvas_appears_after: float | None = None,
        on_poll: Any = None,
        clock: VirtualClock | None = None,
        url: str = "https://app.biorender.com/editor/fake",
    ) -> None:
        super().__init__(selector_map=dict(base_selectors or {}), url=url)
        self.selector_map.setdefault(CANVAS_SELECTOR, [])
        self._canvas_appears_after = canvas_appears_after
        self._on_poll = on_poll
        self._clock = clock
        self.poll_counter = 0

    def _tick(self) -> None:
        self.poll_counter += 1
        if self._on_poll is not None:
            self._on_poll(self, self.poll_counter)
        if self._canvas_appears_after is not None and self._clock is not None:
            if self._clock.read() >= self._canvas_appears_after:
                self.selector_map[CANVAS_SELECTOR] = [_canvas_element()]

    def locator(self, selector: str):  # type: ignore[override]
        # Any poll goes through locator(); count only canvas checks so we can
        # advance the state machine at each observable readiness probe.
        if selector == CANVAS_SELECTOR:
            self._tick()
        return super().locator(selector)


def test_canvas_available_immediately_returns_without_sleeping(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=30.0, poll=0.5)
    clock = _install_virtual_clock(operator)
    page = _ProgrammableFakePage(
        base_selectors={CANVAS_SELECTOR: [_canvas_element()]},
        clock=clock,
    )
    operator._page = page
    summary = operator._wait_for_editor_ready(
        _open_editor_action(),
        requested_url=page.url,
        timeout_seconds=30.0,
    )
    assert summary["wait_elapsed_seconds"] == pytest.approx(0.0, abs=1e-9)
    assert summary["loading_indicator_observed"] is False
    assert summary["observed_url"] == page.url
    # No sleep should have been consumed on the first successful poll.
    assert clock.now == pytest.approx(0.0, abs=1e-9)


def test_canvas_appears_after_1_5_seconds_still_succeeds_within_30(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=30.0, poll=0.1)
    clock = _install_virtual_clock(operator)
    page = _ProgrammableFakePage(
        canvas_appears_after=1.5,
        clock=clock,
    )
    operator._page = page
    summary = operator._wait_for_editor_ready(
        _open_editor_action(),
        requested_url=page.url,
        timeout_seconds=30.0,
    )
    # Canvas appears at 1.5s ± one poll interval. The important assertion is
    # that we did NOT fail (the old code would have) and that we did NOT
    # wait anywhere near the 30s ceiling.
    assert 1.4 <= summary["wait_elapsed_seconds"] <= 3.0
    assert summary["wait_elapsed_seconds"] < 30.0


def test_canvas_never_appears_raises_canvas_not_found_after_timeout(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=0.5, poll=0.05)
    clock = _install_virtual_clock(operator)
    # Loading indicator remains visible for the full wait to prove it is
    # recorded in metadata and does NOT get reclassified as anything else.
    loading = FakeElement(
        bbox={"x": 0, "y": 0, "width": 200, "height": 200},
        attrs={"data-testid": "canvas-loading-placeholder"},
    )
    page = _ProgrammableFakePage(
        base_selectors={
            "[data-testid*='loading'], [data-testid*='skeleton'], "
            "[data-testid*='placeholder'], [aria-busy='true'], "
            "[class*='loading' i], [class*='spinner' i], "
            "[class*='skeleton' i], [class*='placeholder' i]": [loading],
        },
        clock=clock,
    )
    operator._page = page
    with pytest.raises(EditorPrepareFailed) as captured:
        operator._wait_for_editor_ready(
            _open_editor_action(),
            requested_url=page.url,
            timeout_seconds=0.5,
        )
    error = captured.value
    assert error.subcode == "canvas_not_found"
    assert error.metadata["timeout_seconds"] == 0.5
    assert error.metadata["requested_url"] == page.url
    assert error.metadata["observed_url"] == page.url
    assert error.metadata["loading_indicator_observed"] is True
    # screenshot_path is a str path when the shot was captured; it may be
    # None if evidence_dir was unavailable, but the key must always exist.
    assert "screenshot_path" in error.metadata


def test_redirect_to_login_is_reported_distinctly(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=1.0, poll=0.05)
    clock = _install_virtual_clock(operator)

    def redirect_after_two_polls(page: _ProgrammableFakePage, count: int) -> None:
        if count >= 2:
            page.url = "https://app.biorender.com/login?next=/editor/fake"

    page = _ProgrammableFakePage(
        on_poll=redirect_after_two_polls,
        clock=clock,
    )
    operator._page = page
    with pytest.raises(EditorPrepareFailed) as captured:
        operator._wait_for_editor_ready(
            _open_editor_action(),
            requested_url="https://app.biorender.com/editor/fake",
            timeout_seconds=1.0,
        )
    error = captured.value
    assert error.subcode == "redirected_to_login"
    assert error.metadata["observed_url"].endswith("/login?next=/editor/fake")
    # Login redirects must NOT be collapsed into canvas_not_found.
    assert error.subcode != "canvas_not_found"


def test_page_closed_during_wait_is_reported_distinctly(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=1.0, poll=0.05)
    clock = _install_virtual_clock(operator)

    def close_after_two_polls(page: _ProgrammableFakePage, count: int) -> None:
        if count >= 2:
            page.closed = True

    page = _ProgrammableFakePage(
        on_poll=close_after_two_polls,
        clock=clock,
    )
    operator._page = page
    with pytest.raises(EditorPrepareFailed) as captured:
        operator._wait_for_editor_ready(
            _open_editor_action(),
            requested_url=page.url,
            timeout_seconds=1.0,
        )
    error = captured.value
    assert error.subcode == "page_closed"
    assert error.subcode != "canvas_not_found"


def test_off_domain_redirect_is_reported_distinctly(tmp_path: Path) -> None:
    operator = _build_operator(tmp_path, timeout=1.0, poll=0.05)
    clock = _install_virtual_clock(operator)

    def redirect_off_domain(page: _ProgrammableFakePage, count: int) -> None:
        if count >= 2:
            page.url = "https://malicious.example.com/attack"

    page = _ProgrammableFakePage(
        on_poll=redirect_off_domain,
        clock=clock,
    )
    operator._page = page
    with pytest.raises(EditorPrepareFailed) as captured:
        operator._wait_for_editor_ready(
            _open_editor_action(),
            requested_url="https://app.biorender.com/editor/fake",
            timeout_seconds=1.0,
        )
    assert captured.value.subcode == "redirected_off_domain"


def test_default_production_timeout_is_thirty_seconds() -> None:
    assert DEFAULT_EDITOR_READY_TIMEOUT_SECONDS == 30.0
    operator = LivePlaywrightOperator(headed=False)
    assert operator._editor_ready_timeout_seconds == 30.0


def test_editor_ready_timeout_override_via_action_arguments(tmp_path: Path) -> None:
    """Callers can pass a small timeout via action.arguments for tests."""
    operator = _build_operator(tmp_path, timeout=30.0, poll=0.05)
    clock = _install_virtual_clock(operator)
    page = _ProgrammableFakePage(clock=clock)
    operator._page = page
    action = GuiAction(
        id="action_open_editor_override",
        figure_id="figure_override",
        sequence=0,
        action=ActionType.OPEN_EDITOR,
        arguments={
            "url": "https://app.biorender.com/editor/fake",
            "editor_ready_timeout_seconds": 0.3,
        },
    )
    with pytest.raises(EditorPrepareFailed) as captured:
        operator._open_editor(action)
    error = captured.value
    assert error.subcode == "canvas_not_found"
    assert error.metadata["timeout_seconds"] == 0.3
    # A 0.3s virtual timeout must not stretch anywhere near 30s.
    assert clock.now <= 1.0
