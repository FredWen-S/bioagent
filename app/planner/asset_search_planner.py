from __future__ import annotations

from app.schemas.asset_plan import AssetSearchItem, AssetSearchPlan
from app.schemas.figure_spec import Entity, FigureSpec


class AssetSearchPlanner:
    _fallbacks: dict[str, list[str]] = {
        "t cell": ["T cell", "CD8 T cell", "T lymphocyte", "immune cell"],
        "tumor cell": ["tumor cell", "cancer cell", "malignant cell", "cell"],
        "pd-1 receptor": ["PD-1 receptor", "PD-1", "cell surface receptor", "receptor"],
        "pd-l1 ligand": ["PD-L1 ligand", "PD-L1", "membrane ligand", "protein"],
        "anti-pd-1 antibody": [
            "anti-PD-1 antibody",
            "monoclonal antibody",
            "antibody",
            "Y antibody",
        ],
    }

    def plan(self, spec: FigureSpec) -> AssetSearchPlan:
        return AssetSearchPlan(
            figure_id=spec.id,
            items=[self._item_for(entity) for entity in spec.entities],
        )

    def _item_for(self, entity: Entity) -> AssetSearchItem:
        base = self._fallbacks.get(entity.concept.casefold(), [])
        terms = self._unique([entity.concept, *entity.aliases, *base])[:5]
        if entity.category.value == "cell":
            features = ["simple scientific illustration", "consistent cell perspective"]
        elif entity.category.value == "protein":
            features = ["simple membrane protein", "easy to connect and label"]
        else:
            features = ["simple scientific icon", "minimal extra structures"]
        return AssetSearchItem(
            entity_id=entity.id,
            concept=entity.concept,
            search_terms=terms,
            preferred_visual_features=features,
        )

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            key = item.casefold().strip()
            if key and key not in seen:
                seen.add(key)
                result.append(item.strip())
        return result
