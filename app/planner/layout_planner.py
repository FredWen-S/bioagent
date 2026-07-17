from __future__ import annotations

import math

from app.schemas.figure_spec import Entity, FigureSpec, LayoutType
from app.schemas.layout_spec import LayoutSpec, Placement, Region


class LayoutPlanner:
    def plan(self, spec: FigureSpec) -> LayoutSpec:
        if spec.layout_type == LayoutType.TWO_PANEL_COMPARISON:
            return self._two_panel(spec)
        if spec.layout_type == LayoutType.RADIAL:
            return self._radial(spec)
        return self._linear(spec)

    def _two_panel(self, spec: FigureSpec) -> LayoutSpec:
        region_ids = self._ordered_regions(spec)
        if len(region_ids) != 2:
            raise ValueError("two-panel layout requires exactly two entity regions")
        regions = [
            Region(id=region_ids[0], title=self._humanize(region_ids[0]), x=0.04, y=0.1, width=0.44, height=0.84),
            Region(id=region_ids[1], title=self._humanize(region_ids[1]), x=0.52, y=0.1, width=0.44, height=0.84),
        ]
        placements: list[Placement] = []
        for region in regions:
            entities = [entity for entity in spec.entities if entity.region_id == region.id]
            placements.extend(self._grid(entities, region))
        return LayoutSpec(
            figure_id=spec.id,
            layout_type=spec.layout_type,
            regions=regions,
            placements=placements,
        )

    @staticmethod
    def _grid(entities: list[Entity], region: Region) -> list[Placement]:
        columns = 2 if len(entities) > 2 else max(1, len(entities))
        rows = math.ceil(len(entities) / columns)
        placements: list[Placement] = []
        for index, entity in enumerate(entities):
            column = index % columns
            row = index // columns
            x = region.x + region.width * ((column + 0.5) / columns)
            y = region.y + 0.08 + (region.height - 0.16) * ((row + 0.5) / rows)
            width = min(0.14, region.width * 0.34)
            placements.append(
                Placement(
                    entity_id=entity.id,
                    region_id=region.id,
                    x=round(x, 4),
                    y=round(y, 4),
                    width=round(width, 4),
                )
            )
        return placements

    @staticmethod
    def _linear(spec: FigureSpec) -> LayoutSpec:
        region = Region(id="main", title=None, x=0.04, y=0.12, width=0.92, height=0.76)
        count = len(spec.entities)
        placements = [
            Placement(
                entity_id=entity.id,
                region_id="main",
                x=round(region.x + region.width * ((index + 0.5) / count), 4),
                y=0.5,
                width=round(min(0.14, 0.68 / count), 4),
            )
            for index, entity in enumerate(spec.entities)
        ]
        return LayoutSpec(
            figure_id=spec.id,
            layout_type=spec.layout_type,
            regions=[region],
            placements=placements,
        )

    @staticmethod
    def _radial(spec: FigureSpec) -> LayoutSpec:
        region = Region(id="main", title=None, x=0.05, y=0.08, width=0.9, height=0.84)
        placements: list[Placement] = []
        for index, entity in enumerate(spec.entities):
            if index == 0:
                x, y = 0.5, 0.5
            else:
                angle = 2 * math.pi * (index - 1) / max(1, len(spec.entities) - 1)
                x = 0.5 + 0.32 * math.cos(angle)
                y = 0.5 + 0.3 * math.sin(angle)
            placements.append(
                Placement(
                    entity_id=entity.id,
                    region_id="main",
                    x=round(x, 4),
                    y=round(y, 4),
                    width=0.12,
                )
            )
        return LayoutSpec(
            figure_id=spec.id,
            layout_type=spec.layout_type,
            regions=[region],
            placements=placements,
        )

    @staticmethod
    def _ordered_regions(spec: FigureSpec) -> list[str]:
        result: list[str] = []
        for entity in spec.entities:
            if entity.region_id and entity.region_id not in result:
                result.append(entity.region_id)
        return result

    @staticmethod
    def _humanize(value: str) -> str:
        return value.replace("_", " ").title()

