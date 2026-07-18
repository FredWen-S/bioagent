from __future__ import annotations

import re

from app.schemas.figure_spec import FigureSpec, RelationType, Requirement
from app.schemas.verification import IssueSeverity, ScientificValidation, VerificationIssue


class ScientificValidityGuard:
    """Performs structural and request-grounding checks, not scientific peer review."""

    def validate(self, spec: FigureSpec, requirement: Requirement) -> ScientificValidation:
        issues: list[VerificationIssue] = []
        terms = self._entity_terms(spec)
        for concept in spec.required_concepts:
            if self._normalize(concept) not in terms:
                issues.append(
                    VerificationIssue(
                        severity=IssueSeverity.HIGH,
                        type="missing_required_entity",
                        message=f"Required concept {concept!r} is not represented by an entity.",
                    )
                )

        connected = {relation.source for relation in spec.relations} | {
            relation.target for relation in spec.relations
        }
        for entity in spec.entities:
            if entity.required and entity.category.value != "label" and entity.id not in connected:
                issues.append(
                    VerificationIssue(
                        severity=IssueSeverity.MEDIUM,
                        type="isolated_required_entity",
                        entity_id=entity.id,
                        message=f"Required entity {entity.id!r} has no scientific relation.",
                    )
                )

        pair_types: dict[frozenset[str], set[RelationType]] = {}
        for relation in spec.relations:
            pair_types.setdefault(frozenset((relation.source, relation.target)), set()).add(
                relation.type
            )
        for pair, types in pair_types.items():
            if RelationType.ACTIVATION in types and RelationType.INHIBITION in types:
                issues.append(
                    VerificationIssue(
                        severity=IssueSeverity.HIGH,
                        type="contradictory_relation",
                        message=f"Entity pair {sorted(pair)} is both activated and inhibited.",
                    )
                )

        lowered = requirement.source_text.casefold()
        if ("anti-pd-1" in lowered or "anti pd-1" in lowered) and not any(
            relation.type == RelationType.BLOCKING for relation in spec.relations
        ):
            issues.append(
                VerificationIssue(
                    severity=IssueSeverity.HIGH,
                    type="missing_required_relation",
                    message="Anti-PD-1 blocking relationship is missing.",
                )
            )
        if ("抑制" in lowered or "inhibit" in lowered) and not any(
            relation.type == RelationType.INHIBITION for relation in spec.relations
        ):
            issues.append(
                VerificationIssue(
                    severity=IssueSeverity.HIGH,
                    type="missing_required_relation",
                    message="The request states inhibition, but no inhibition relation exists.",
                )
            )
        return ScientificValidation(
            passed=not any(issue.severity == IssueSeverity.HIGH for issue in issues),
            issues=issues,
        )

    @classmethod
    def _entity_terms(cls, spec: FigureSpec) -> set[str]:
        terms: set[str] = set()
        for entity in spec.entities:
            terms.add(cls._normalize(entity.concept))
            terms.add(cls._normalize(entity.label))
            terms.update(cls._normalize(alias) for alias in entity.aliases)
        return terms

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())
