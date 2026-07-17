from __future__ import annotations

from app.schemas.asset_plan import AssetSearchPlan
from app.schemas.figure_spec import FigureSpec, RelationType
from app.schemas.gui_action import ActionType, BoundingBox, CoordinateSpace, GuiAction
from app.schemas.layout_spec import LayoutSpec


CONNECTOR_MAP: dict[RelationType, str] = {
    RelationType.ACTIVATION: "arrow",
    RelationType.INHIBITION: "t_bar",
    RelationType.BINDING: "line",
    RelationType.BLOCKING: "blocking_line",
    RelationType.TRANSPORT: "arrow",
    RelationType.ASSOCIATION: "line",
    RelationType.KILLING: "arrow",
    RelationType.FLOW: "arrow",
}


class GuiActionPlanner:
    def compile(
        self,
        spec: FigureSpec,
        layout: LayoutSpec,
        assets: AssetSearchPlan,
        *,
        editor_url: str = "https://app.biorender.com/",
    ) -> list[GuiAction]:
        placements = {placement.entity_id: placement for placement in layout.placements}
        entities = {entity.id: entity for entity in spec.entities}
        actions: list[GuiAction] = []

        def add(action_type: ActionType, arguments: dict, *, screenshot: bool = True) -> None:
            sequence = len(actions)
            actions.append(
                GuiAction(
                    id=f"action_{sequence:04d}_{action_type.value}",
                    figure_id=spec.id,
                    sequence=sequence,
                    action=action_type,
                    arguments=arguments,
                    requires_screenshot=screenshot,
                )
            )

        add(
            ActionType.OPEN_EDITOR,
            {"project_name": spec.title, "url": editor_url, "create_new": False},
        )
        for item in assets.items:
            placement = placements[item.entity_id]
            add(
                ActionType.SEARCH_ASSET,
                {
                    "entity_id": item.entity_id,
                    "query": item.search_terms[0],
                    "fallback_queries": item.search_terms[1:],
                    "max_queries": len(item.search_terms),
                },
            )
            add(
                ActionType.SELECT_ASSET,
                {
                    "entity_id": item.entity_id,
                    "selection_policy": "best_safe_ordinary_asset",
                },
            )
            add(
                ActionType.DRAG_ASSET,
                {
                    "entity_id": item.entity_id,
                    "target_x": placement.x,
                    "target_y": placement.y,
                    "target_width": placement.width,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, placement.x - placement.width / 2),
                        y=max(0.0, placement.y - placement.width / 2),
                        width=placement.width,
                        height=placement.width,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            add(
                ActionType.ADD_TEXT,
                {
                    "entity_id": item.entity_id,
                    "text": entities[item.entity_id].label,
                    "target_x": placement.x,
                    "target_y": min(0.96, placement.y + 0.1),
                },
            )
        for relation in spec.relations:
            add(
                ActionType.CONNECT,
                {
                    "relation_id": relation.id,
                    "source_entity_id": relation.source,
                    "target_entity_id": relation.target,
                    "connector_type": CONNECTOR_MAP[relation.type],
                    "label": relation.label,
                },
            )
        add(ActionType.CAPTURE_CANVAS, {"scope": "full_canvas"})
        add(ActionType.SAVE_PROJECT, {"mode": "biorender_autosave"})
        return actions
