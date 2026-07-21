from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.operator.biorender.locators import (
    ASSET_PANEL_ENTRY_LOCATORS,
    ASSET_PANEL_LOCATORS,
    CANDIDATE_SELECTORS,
    SEARCH_EMPTY_LOCATORS,
    SEARCH_INPUT_LOCATORS,
    SEARCH_RATE_LIMIT_LOCATORS,
    SEARCH_RESULTS_LOCATORS,
    ResolvedLocator,
    bounding_box,
    is_inside,
    locator_for_spec,
    locator_text,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.errors import (
    CandidateIdentityUnclear,
    SafeStopRequested,
    SearchActionFailed,
)
from app.schemas.biorender_probe import AssetCandidateRecord, LocatorEvidence


@dataclass(slots=True)
class RuntimeCandidate:
    record: AssetCandidateRecord
    locator: Any
    score: float


@dataclass(slots=True)
class SearchOutcome:
    selected: RuntimeCandidate
    candidates: list[AssetCandidateRecord]
    screenshot_path: str
    results_screenshot_path: str
    diagnostics: dict[str, Any]


class SafeAssetSearch:
    def __init__(
        self,
        page: Any,
        *,
        evidence_dir: Path,
        policy: BioRenderPolicyGuard | None = None,
        stop_requested: Callable[[], bool] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.page = page
        self.evidence_dir = evidence_dir
        self.policy = policy or BioRenderPolicyGuard()
        self.stop_requested = stop_requested or (lambda: False)
        self.timeout_seconds = timeout_seconds
        self._diagnostics: dict[str, Any] = {}
        self._last_operation = "not_started"

    def search(
        self,
        query: str,
        run_id: str,
        *,
        max_attempts: int = 2,
        deadline: float | None = None,
    ) -> SearchOutcome:
        if not 1 <= max_attempts <= 2:
            raise ValueError("max_attempts must be between 1 and 2")
        deadline = deadline or (time.monotonic() + self.timeout_seconds)
        last_error: SearchActionFailed | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return self._search_once(query, run_id, attempt=attempt, deadline=deadline)
            except SearchActionFailed as error:
                last_error = error
                if (
                    attempt >= max_attempts
                    or not error.retryable
                    or self._remaining_ms(deadline) <= 0
                ):
                    raise
                self._last_operation = "recovery_backoff"
                self._interruptible_wait(500 * attempt, deadline)
        assert last_error is not None
        raise last_error

    def _search_once(
        self,
        query: str,
        run_id: str,
        *,
        attempt: int,
        deadline: float,
    ) -> SearchOutcome:
        self._diagnostics = {"query": query, "attempt": attempt}
        self._check_page_state()
        self.policy.assert_query_allowed(query)
        search = self._wait_for_search_ui(deadline)
        self.policy.assert_target_allowed(search.locator)
        try:
            self._last_operation = "click_search_input"
            self._call(search.locator.click, timeout=self._remaining_ms(deadline))
            self._last_operation = "fill_search_input"
            self._call(search.locator.fill, query, timeout=self._remaining_ms(deadline))
            self._diagnostics["fill_executed"] = True
            self._last_operation = "press_enter"
            self._call(search.locator.press, "Enter", timeout=self._remaining_ms(deadline))
            self._diagnostics["enter_executed"] = True
        except SafeStopRequested:
            raise
        except Exception as error:
            raise self._failure(
                "search_submit_failed",
                f"BioRender search submission failed: {error}",
                retryable=True,
            ) from error

        results, candidate_locator = self._wait_for_stable_results(deadline)
        results_bbox = bounding_box(results.locator)
        if results_bbox is None:
            raise self._failure(
                "search_results_timeout",
                "BioRender search results region has no observable bounding box.",
                retryable=True,
            )

        run_dir = self.evidence_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = run_dir / f"search-results-full-attempt-{attempt}.png"
        results_path = run_dir / f"search-results-region-attempt-{attempt}.png"
        self.page.screenshot(path=str(screenshot_path), full_page=True)
        results.locator.screenshot(path=str(results_path))

        runtime_candidates: list[RuntimeCandidate] = []
        records: list[AssetCandidateRecord] = []
        count = min(candidate_locator.count(), 100)
        for index in range(count):
            candidate = candidate_locator.nth(index)
            record, score = self._inspect_candidate(
                candidate, index, query, results_bbox, str(results_path)
            )
            records.append(record)
            if not record.rejected_reasons:
                runtime_candidates.append(
                    RuntimeCandidate(record=record, locator=candidate, score=score)
                )

        if not runtime_candidates:
            raise CandidateIdentityUnclear(
                "No candidate could be proven to be an ordinary draggable asset; "
                f"review {results_path}"
            )
        runtime_candidates.sort(key=lambda item: (-item.score, item.record.ordinal))
        return SearchOutcome(
            selected=runtime_candidates[0],
            candidates=records,
            screenshot_path=str(screenshot_path),
            results_screenshot_path=str(results_path),
            diagnostics={**self._diagnostics, "last_operation": self._last_operation},
        )

    def _wait_for_search_ui(self, deadline: float) -> Any:
        panel_entry_clicked = False
        last_panel_diagnostics: list[dict[str, Any]] = []
        last_search_diagnostics: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            self._check_page_state()
            panel, last_panel_diagnostics = self._resolve_with_diagnostics(
                ASSET_PANEL_LOCATORS
            )
            search, last_search_diagnostics = self._resolve_with_diagnostics(
                SEARCH_INPUT_LOCATORS
            )
            self._diagnostics.update(
                {
                    "asset_panel_found": panel is not None,
                    "asset_panel_entry_clicked": panel_entry_clicked,
                    "asset_panel_locator_candidates": last_panel_diagnostics,
                    "search_input_locator_candidates": last_search_diagnostics,
                    "search_input_found": search is not None,
                    "fill_executed": False,
                    "enter_executed": False,
                    "results_region_found": False,
                }
            )
            if panel is not None and search is not None:
                enabled = self._locator_flag(search.locator, "is_enabled", default=True)
                editable = self._locator_flag(search.locator, "is_editable", default=True)
                self._diagnostics["search_input_enabled"] = enabled
                self._diagnostics["search_input_editable"] = editable
                if not enabled or not editable:
                    raise self._failure(
                        "search_input_not_editable",
                        "BioRender search input is visible but is not enabled/editable.",
                        retryable=True,
                    )
                self._last_operation = "search_input_ready"
                return search
            if panel is None and not panel_entry_clicked:
                entry, entry_diagnostics = self._resolve_with_diagnostics(
                    ASSET_PANEL_ENTRY_LOCATORS
                )
                self._diagnostics["asset_panel_entry_locator_candidates"] = entry_diagnostics
                if entry is not None:
                    try:
                        self._last_operation = "click_asset_panel_entry"
                        self._call(
                            entry.locator.click,
                            timeout=min(1000, self._remaining_ms(deadline)),
                        )
                        panel_entry_clicked = True
                        self._diagnostics["asset_panel_entry_clicked"] = True
                    except Exception as error:
                        self._diagnostics["asset_panel_entry_click_error"] = str(error)
            self._last_operation = "wait_for_search_input"
            self._interruptible_wait(100, deadline)
        self._diagnostics["asset_panel_locator_candidates"] = last_panel_diagnostics
        self._diagnostics["search_input_locator_candidates"] = last_search_diagnostics
        raise self._failure(
            "search_ui_not_found",
            "BioRender asset panel/search input did not become usable within 30 seconds.",
            retryable=False,
        )

    def _wait_for_stable_results(self, deadline: float) -> tuple[Any, Any]:
        previous_signature: tuple | None = None
        stable_rounds = 0
        while time.monotonic() < deadline:
            self._check_page_state()
            rate_limited, rate_diagnostics = self._resolve_with_diagnostics(
                SEARCH_RATE_LIMIT_LOCATORS
            )
            if rate_limited is not None:
                self._diagnostics["rate_limit_locator_candidates"] = rate_diagnostics
                raise self._failure(
                    "search_rate_limited",
                    "BioRender search is rate limited (429/Too Many Requests).",
                    retryable=False,
                )
            empty, empty_diagnostics = self._resolve_with_diagnostics(SEARCH_EMPTY_LOCATORS)
            if empty is not None:
                self._diagnostics["empty_result_locator_candidates"] = empty_diagnostics
                raise self._failure(
                    "search_no_results",
                    "BioRender search returned an explicit empty result state.",
                    retryable=False,
                )
            results, result_diagnostics = self._resolve_with_diagnostics(
                SEARCH_RESULTS_LOCATORS
            )
            self._diagnostics["results_locator_candidates"] = result_diagnostics
            if results is not None:
                self._diagnostics["results_region_found"] = True
                candidates = results.locator.locator(", ".join(CANDIDATE_SELECTORS))
                signature = self._candidate_signature(candidates)
                if signature and signature == previous_signature:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 2:
                    self._last_operation = "search_results_stable"
                    return results, candidates
                previous_signature = signature
            self._last_operation = "wait_for_search_results"
            self._interruptible_wait(100, deadline)
        raise self._failure(
            "search_results_timeout",
            "BioRender search produced no stable result region or explicit empty "
            "state within 30 seconds.",
            retryable=True,
        )

    def _resolve_with_diagnostics(self, specs: tuple[Any, ...]) -> tuple[Any, list[dict[str, Any]]]:
        diagnostics: list[dict[str, Any]] = []
        selected = None
        contexts = [("page", self.page)]
        for index, frame in enumerate(getattr(self.page, "frames", [])[1:], start=1):
            contexts.append((f"frame[{index}]", frame))
        for context_name, context in contexts:
            for spec in specs:
                entry = {
                    "context": context_name,
                    "strategy": spec.strategy,
                    "query": spec.query,
                    "count": 0,
                    "visible_count": 0,
                    "bbox_count": 0,
                    "error": None,
                }
                try:
                    locator = locator_for_spec(context, spec)
                    entry["count"] = min(locator.count(), 50)
                    for item_index in range(entry["count"]):
                        candidate = locator.nth(item_index)
                        if not candidate.is_visible():
                            continue
                        entry["visible_count"] += 1
                        if candidate.bounding_box() is None:
                            continue
                        entry["bbox_count"] += 1
                        if selected is None:
                            selected = ResolvedLocator(
                                locator=candidate,
                                evidence=LocatorEvidence(
                                    strategy=spec.strategy,
                                    query=spec.query,
                                    confidence=spec.confidence,
                                ),
                            )
                except Exception as error:
                    entry["error"] = f"{type(error).__name__}: {error}"
                diagnostics.append(entry)
        return selected, diagnostics

    def _check_page_state(self) -> None:
        if self.stop_requested():
            raise SafeStopRequested("Safe Stop interrupted the active search wait.")
        try:
            if self.page.is_closed():
                raise self._failure("page_closed", "BioRender page was closed.", retryable=False)
        except AttributeError:
            pass
        url = str(getattr(self.page, "url", "")).casefold()
        if any(marker in url for marker in ("/login", "/sign-in", "/signin")):
            raise self._failure(
                "redirected_to_login",
                "BioRender redirected to login during asset search.",
                retryable=False,
            )

    def _interruptible_wait(self, milliseconds: int, deadline: float) -> None:
        remaining = min(milliseconds, self._remaining_ms(deadline))
        while remaining > 0:
            self._check_page_state()
            chunk = min(100, remaining)
            self.page.wait_for_timeout(chunk)
            remaining -= chunk

    @staticmethod
    def _locator_flag(locator: Any, method: str, *, default: bool) -> bool:
        check = getattr(locator, method, None)
        if not callable(check):
            return default
        try:
            return bool(check())
        except Exception:
            return False

    @staticmethod
    def _call(method: Any, *args: Any, timeout: int) -> Any:
        try:
            return method(*args, timeout=max(1, timeout))
        except TypeError:
            return method(*args)

    @staticmethod
    def _remaining_ms(deadline: float) -> int:
        return max(0, round((deadline - time.monotonic()) * 1000))

    def _failure(
        self,
        subcode: str,
        message: str,
        *,
        retryable: bool,
    ) -> SearchActionFailed:
        return SearchActionFailed(
            message,
            subcode=subcode,
            retryable=retryable,
            diagnostics={
                **self._diagnostics,
                "last_operation": self._last_operation,
            },
        )

    @staticmethod
    def _candidate_signature(candidates: Any) -> tuple:
        try:
            count = min(candidates.count(), 100)
        except Exception:
            return ()
        signature: list[tuple] = []
        for index in range(count):
            candidate = candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
                box = candidate.bounding_box()
                if box:
                    signature.append(
                        (
                            index,
                            round(box["x"], 1),
                            round(box["y"], 1),
                            round(box["width"], 1),
                            round(box["height"], 1),
                        )
                    )
            except Exception:
                continue
        return tuple(signature)

    def _inspect_candidate(
        self,
        locator: Any,
        ordinal: int,
        query: str,
        results_bbox: Any,
        screenshot_path: str,
    ) -> tuple[AssetCandidateRecord, float]:
        rejected: list[str] = []
        evidence: list[str] = []
        text = locator_text(locator)
        box = bounding_box(locator)
        if box is None:
            box = results_bbox.model_copy(update={"width": 1.0, "height": 1.0})
            rejected.append("candidate has no observable bounding box")
        in_results = is_inside(box, results_bbox)
        if in_results:
            evidence.append("inside calibrated search results region")
        else:
            rejected.append("candidate is outside search results region")

        draggable_value = (locator.get_attribute("draggable") or "").casefold()
        draggable = draggable_value == "true"
        if draggable:
            evidence.append("explicit draggable=true")
        else:
            rejected.append("drag origin is not explicitly draggable")

        data_testid = locator.get_attribute("data-testid") or ""
        known_asset_card = (
            "asset" in data_testid.casefold() or "search-result" in data_testid.casefold()
        )
        try:
            has_thumbnail = locator.locator("img, svg, canvas").count() > 0
        except Exception:
            has_thumbnail = False
        if known_asset_card:
            evidence.append("asset/search-result data-testid")
        if has_thumbnail:
            evidence.append("visual thumbnail descendant")
        if not known_asset_card and not has_thumbnail:
            rejected.append("ordinary asset card evidence is insufficient")
        try:
            self.policy.assert_target_allowed(locator, candidate_context=True)
        except Exception as error:
            rejected.append(str(error))

        accessible_name = ""
        for attribute in ("aria-label", "title", "data-label"):
            try:
                value = locator.get_attribute(attribute) or ""
            except Exception:
                value = ""
            if value:
                accessible_name = value.strip()
                break
        if not accessible_name:
            accessible_name = text.split("|")[0].strip()
        thumbnail_signature = None
        if has_thumbnail:
            thumbnail_signature = hashlib.sha256(
                f"thumbnail|{accessible_name}|{data_testid}".encode()
            ).hexdigest()[:20]
        fingerprint_text = "|".join(
            (
                accessible_name.casefold(),
                text.casefold(),
                data_testid.casefold(),
                draggable_value,
                thumbnail_signature or "no-thumbnail",
            )
        )
        dom_fingerprint = hashlib.sha256(
            fingerprint_text.encode("utf-8")
        ).hexdigest()[:24]
        candidate_id = f"assetfp_{dom_fingerprint}"
        score = 0.0
        score += 3.0 if draggable else 0
        score += 2.0 if known_asset_card else 0
        score += 1.0 if has_thumbnail else 0
        score += 1.0 if query.casefold() in text.casefold() else 0
        return (
            AssetCandidateRecord(
                candidate_id=candidate_id,
                accessible_name=accessible_name[:500],
                dom_fingerprint=dom_fingerprint,
                thumbnail_fingerprint=thumbnail_signature,
                ordinal=ordinal,
                text=text[:1000],
                bbox=box,
                draggable=draggable,
                in_results_region=in_results,
                ordinary_asset_evidence=evidence,
                rejected_reasons=rejected,
                screenshot_path=screenshot_path,
            ),
            score,
        )
