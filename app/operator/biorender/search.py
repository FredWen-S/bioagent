from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.operator.biorender.locators import (
    CANDIDATE_SELECTORS,
    SEARCH_INPUT_LOCATORS,
    SEARCH_RESULTS_LOCATORS,
    bounding_box,
    is_inside,
    locator_text,
    resolve_first_visible,
)
from app.operator.biorender.policy_guard import BioRenderPolicyGuard
from app.operator.errors import CandidateIdentityUnclear, SearchNoResult, UiLayoutChanged
from app.schemas.biorender_probe import AssetCandidateRecord


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


class SafeAssetSearch:
    def __init__(
        self,
        page: Any,
        *,
        evidence_dir: Path,
        policy: BioRenderPolicyGuard | None = None,
    ) -> None:
        self.page = page
        self.evidence_dir = evidence_dir
        self.policy = policy or BioRenderPolicyGuard()

    def search(self, query: str, run_id: str) -> SearchOutcome:
        self.policy.assert_page_safe(self.page)
        self.policy.assert_query_allowed(query)
        search = resolve_first_visible(self.page, SEARCH_INPUT_LOCATORS)
        if search is None:
            raise UiLayoutChanged("BioRender asset search input could not be re-located")
        self.policy.assert_target_allowed(search.locator)
        search.locator.click()
        search.locator.fill(query)

        results, candidate_locator = self._wait_for_stable_results()
        self.policy.assert_page_safe(self.page)
        results_bbox = bounding_box(results.locator)
        if results_bbox is None:
            raise UiLayoutChanged("Search results region has no observable bounding box")

        run_dir = self.evidence_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = run_dir / "search-results-full.png"
        results_path = run_dir / "search-results-region.png"
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
        )

    def _wait_for_stable_results(self) -> tuple[Any, Any]:
        previous_signature: tuple | None = None
        stable_rounds = 0
        for _ in range(20):
            results = resolve_first_visible(self.page, SEARCH_RESULTS_LOCATORS)
            if results is not None:
                candidates = results.locator.locator(", ".join(CANDIDATE_SELECTORS))
                signature = self._candidate_signature(candidates)
                if signature and signature == previous_signature:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                if stable_rounds >= 2:
                    return results, candidates
                previous_signature = signature
            self.page.wait_for_timeout(300)
        if previous_signature:
            raise CandidateIdentityUnclear(
                "BioRender search results did not become geometrically stable"
            )
        raise SearchNoResult("BioRender search returned no observable asset candidates")

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
        known_asset_card = "asset" in data_testid.casefold() or "search-result" in data_testid.casefold()
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

        fingerprint_text = f"{text}|{data_testid}|{query.casefold()}"
        candidate_id = "candidate_" + hashlib.sha256(
            fingerprint_text.encode("utf-8")
        ).hexdigest()[:16]
        score = 0.0
        score += 3.0 if draggable else 0
        score += 2.0 if known_asset_card else 0
        score += 1.0 if has_thumbnail else 0
        score += 1.0 if query.casefold() in text.casefold() else 0
        return (
            AssetCandidateRecord(
                candidate_id=candidate_id,
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
