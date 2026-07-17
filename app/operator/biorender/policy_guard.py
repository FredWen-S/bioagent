from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.operator.biorender.locators import INTERACTIVE_SELECTOR, MODAL_SELECTOR, bounding_box
from app.operator.errors import PolicyBlocked, UnexpectedModal
from app.schemas.biorender_probe import CalibratedRegion, LocatorEvidence, VisibleModal


AI_CONTROL_PATTERNS = (
    re.compile(r"\bbiorender\s+ai\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:a\s+)?(?:figure\s+)?with\s+ai\b", re.IGNORECASE),
    re.compile(r"\bgenerate\s+(?:a\s+)?figure\b", re.IGNORECASE),
    re.compile(r"\bgenerate\s+with\s+ai\b", re.IGNORECASE),
    re.compile(r"\bai\s+(?:generate|generator|edit|assistant)\b", re.IGNORECASE),
)

AI_CREDIT_PATTERNS = (
    re.compile(r"\bai\s+credits?\b", re.IGNORECASE),
    re.compile(r"\bcredits?\s+(?:will\s+be|are)\s+used\b", re.IGNORECASE),
    re.compile(r"\buse\s+\d*\s*ai\s+credits?\b", re.IGNORECASE),
)

SUBSCRIPTION_PATTERNS = (
    re.compile(r"\bupgrade\s+(?:now|to|your)\b", re.IGNORECASE),
    re.compile(r"\bsubscribe\b", re.IGNORECASE),
    re.compile(r"\bpurchase\b", re.IGNORECASE),
    re.compile(r"\bunlock\s+(?:premium|this\s+asset|asset)\b", re.IGNORECASE),
)

TEMPLATE_PATTERNS = (
    re.compile(r"\btemplate\b", re.IGNORECASE),
    re.compile(r"模板"),
)


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    classification: str
    text: str
    blocking: bool


class BioRenderPolicyGuard:
    """Contextual denylist for BioRender AI, credits, templates, and paid flows."""

    def __init__(self) -> None:
        self.last_ai_controls: list[CalibratedRegion] = []

    @staticmethod
    def classify_text(text: str, *, candidate_context: bool = False) -> PolicyFinding | None:
        normalized = " ".join(text.split())
        if any(pattern.search(normalized) for pattern in AI_CREDIT_PATTERNS):
            return PolicyFinding("ai_credit_confirmation", normalized, True)
        if any(pattern.search(normalized) for pattern in AI_CONTROL_PATTERNS):
            return PolicyFinding("biorender_ai_control", normalized, True)
        if any(pattern.search(normalized) for pattern in SUBSCRIPTION_PATTERNS):
            return PolicyFinding("subscription_or_purchase", normalized, True)
        if candidate_context and any(
            pattern.search(normalized) for pattern in TEMPLATE_PATTERNS
        ):
            return PolicyFinding("template_entry", normalized, True)
        return None

    def scan_ai_controls(self, page: Any) -> list[CalibratedRegion]:
        findings: list[CalibratedRegion] = []
        try:
            controls = page.locator(INTERACTIVE_SELECTOR)
            count = min(controls.count(), 250)
        except Exception:
            return findings
        for index in range(count):
            control = controls.nth(index)
            try:
                if not control.is_visible():
                    continue
                text = self._control_text(control)
            except Exception:
                continue
            finding = self.classify_text(text)
            if finding and finding.classification in {
                "biorender_ai_control",
                "ai_credit_confirmation",
            }:
                findings.append(
                    CalibratedRegion(
                        name=f"ai_control_{index}",
                        found=True,
                        bbox=bounding_box(control),
                        locator=LocatorEvidence(
                            strategy="interactive_scan",
                            query=text[:240],
                            confidence=0.95,
                        ),
                        diagnostics=[finding.classification],
                    )
                )
        return findings

    def assert_query_allowed(self, query: str) -> None:
        finding = self.classify_text(query)
        if finding:
            raise PolicyBlocked(
                f"Search query is denied by BioRender policy: "
                f"{finding.classification}: {finding.text[:200]}"
            )

    def scan_modals(self, page: Any) -> list[VisibleModal]:
        modals: list[VisibleModal] = []
        try:
            locator = page.locator(MODAL_SELECTOR)
            count = min(locator.count(), 50)
        except Exception:
            return modals
        for index in range(count):
            modal = locator.nth(index)
            try:
                if not modal.is_visible():
                    continue
                text = self._control_text(modal)
            except Exception:
                continue
            finding = self.classify_text(text)
            classification = finding.classification if finding else "unknown_modal"
            modals.append(
                VisibleModal(
                    text=text[:1000],
                    bbox=bounding_box(modal),
                    classification=classification,
                    blocking=True,
                )
            )
        return modals

    def assert_page_safe(self, page: Any) -> None:
        # Passive, known AI controls are recorded and fenced; only a targeted AI
        # interaction or a confirmation/credits modal blocks the whole page.
        self.last_ai_controls = self.scan_ai_controls(page)
        modals = self.scan_modals(page)
        if not modals:
            return
        first = modals[0]
        if first.classification in {
            "biorender_ai_control",
            "ai_credit_confirmation",
            "subscription_or_purchase",
        }:
            raise PolicyBlocked(
                f"Blocked BioRender dialog detected: {first.classification}: {first.text[:200]}"
            )
        raise UnexpectedModal(f"Unknown visible modal blocks safe interaction: {first.text[:200]}")

    def assert_target_allowed(self, locator: Any, *, candidate_context: bool = False) -> None:
        text = self._control_text(locator)
        finding = self.classify_text(text, candidate_context=candidate_context)
        if finding:
            raise PolicyBlocked(
                f"Interaction target is denied by BioRender policy: "
                f"{finding.classification}: {finding.text[:200]}"
            )

    @staticmethod
    def _control_text(locator: Any) -> str:
        parts: list[str] = []
        try:
            value = locator.inner_text(timeout=1000)
            if value:
                parts.append(value)
        except Exception:
            pass
        for attribute in ("aria-label", "title", "data-testid", "data-label"):
            try:
                value = locator.get_attribute(attribute)
                if value:
                    parts.append(f"{attribute}={value}")
            except Exception:
                continue
        return " | ".join(parts)
