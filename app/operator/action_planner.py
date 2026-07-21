from __future__ import annotations

from app.schemas.asset_plan import AssetSearchPlan
from app.schemas.figure_spec import FigureSpec, RelationType
from app.schemas.gui_action import ActionType, BoundingBox, CoordinateSpace, GuiAction
from app.schemas.layout_spec import LayoutSpec

CONNECTOR_MAP: dict[RelationType, str] = {
    RelationType.ACTIVATION: "arrow",
    RelationType.INHIBITION: "t_bar",
    RelationType.BINDING: "line",
    RelationType.BLOCKING: "t_bar",
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
            figure_key = spec.id.removeprefix("figure_")
            actions.append(
                GuiAction(
                    id=f"action_{figure_key}_{sequence:04d}_{action_type.value}",
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
            final_height = placement.height or placement.width
            staging_x = min(0.94, placement.x + 0.025)
            staging_y = min(0.94, placement.y + 0.02)
            staging_width = max(0.04, placement.width * 0.85)
            add(
                ActionType.SEARCH_ASSET,
                {
                    "entity_id": item.entity_id,
                    "logical_element_id": item.entity_id,
                    "query": item.search_terms[0],
                    "fallback_queries": item.search_terms[1:],
                    "max_queries": len(item.search_terms),
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={"max_retries": 0, "timeout_seconds": 30.0}
            )
            add(
                ActionType.SELECT_ASSET,
                {
                    "entity_id": item.entity_id,
                    "logical_element_id": item.entity_id,
                    "selection_policy": "best_safe_ordinary_asset",
                },
            )
            add(
                ActionType.DRAG_ASSET,
                {
                    "entity_id": item.entity_id,
                    "logical_element_id": item.entity_id,
                    "target_x": staging_x,
                    "target_y": staging_y,
                    "target_width": staging_width,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, staging_x - staging_width / 2),
                        y=max(0.0, staging_y - staging_width / 2),
                        width=staging_width,
                        height=staging_width,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            add(
                ActionType.MOVE_ELEMENT,
                {
                    "element_id": item.entity_id,
                    "logical_element_id": item.entity_id,
                    "element_kind": "asset",
                    "target_x": placement.x,
                    "target_y": placement.y,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, placement.x - staging_width / 2),
                        y=max(0.0, placement.y - staging_width / 2),
                        width=staging_width,
                        height=staging_width,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            add(
                ActionType.RESIZE_ELEMENT,
                {
                    "element_id": item.entity_id,
                    "logical_element_id": item.entity_id,
                    "element_kind": "asset",
                    "target_width": placement.width,
                    "target_height": final_height,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, placement.x - placement.width / 2),
                        y=max(0.0, placement.y - final_height / 2),
                        width=placement.width,
                        height=final_height,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            label_width = min(0.2, max(0.06, len(entities[item.entity_id].label) * 0.012))
            label_y = min(0.96, placement.y + final_height / 2 + 0.055)
            label_staging_x = min(0.96, placement.x + 0.012)
            label_staging_y = min(0.96, label_y + 0.012)
            label_staging_width = max(0.05, label_width * 0.88)
            label_id = f"label_{item.entity_id}"
            add(
                ActionType.ADD_TEXT,
                {
                    "entity_id": item.entity_id,
                    "element_id": label_id,
                    "logical_label_id": label_id,
                    "target_element_id": item.entity_id,
                    "text": entities[item.entity_id].label,
                    "expected_text": entities[item.entity_id].label,
                    "target_x": label_staging_x,
                    "target_y": label_staging_y,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, label_staging_x - label_staging_width / 2),
                        y=max(0.0, label_staging_y - 0.02),
                        width=label_staging_width,
                        height=0.04,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            add(
                ActionType.MOVE_ELEMENT,
                {
                    "element_id": label_id,
                    "logical_label_id": label_id,
                    "target_element_id": item.entity_id,
                    "element_kind": "label",
                    "target_x": placement.x,
                    "target_y": label_y,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, placement.x - label_staging_width / 2),
                        y=max(0.0, label_y - 0.02),
                        width=label_staging_width,
                        height=0.04,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
            add(
                ActionType.RESIZE_ELEMENT,
                {
                    "element_id": label_id,
                    "logical_label_id": label_id,
                    "target_element_id": item.entity_id,
                    "element_kind": "label",
                    "target_width": label_width,
                    "target_height": 0.04,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=max(0.0, placement.x - label_width / 2),
                        y=max(0.0, label_y - 0.02),
                        width=label_width,
                        height=0.04,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
        region_rows: dict[tuple[str, float], list[str]] = {}
        for placement in layout.placements:
            region_rows.setdefault(
                (placement.region_id, round(placement.y, 3)),
                [],
            ).append(placement.entity_id)
        for (region_id, _), element_ids in region_rows.items():
            if len(element_ids) < 2:
                continue
            add(
                ActionType.ALIGN_ELEMENTS,
                {
                    "element_ids": element_ids,
                    "logical_layout_id": f"align_{region_id}_{len(actions):04d}",
                    "alignment": "middle",
                    "region_id": region_id,
                },
            )
            if len(element_ids) >= 3:
                add(
                    ActionType.DISTRIBUTE_ELEMENTS,
                    {
                        "element_ids": element_ids,
                        "logical_layout_id": f"distribute_{region_id}_{len(actions):04d}",
                        "axis": "horizontal",
                        "region_id": region_id,
                    },
                )
        region_columns: dict[tuple[str, float], list[str]] = {}
        for placement in layout.placements:
            region_columns.setdefault(
                (placement.region_id, round(placement.x, 3)),
                [],
            ).append(placement.entity_id)
        for (region_id, _), element_ids in region_columns.items():
            if len(element_ids) < 3:
                continue
            add(
                ActionType.DISTRIBUTE_ELEMENTS,
                {
                    "element_ids": element_ids,
                    "logical_layout_id": f"distribute_{region_id}_{len(actions):04d}",
                    "axis": "vertical",
                    "region_id": region_id,
                },
            )
        for relation in spec.relations:
            source = placements[relation.source]
            target = placements[relation.target]
            margin = max(source.width, target.width) / 2 + 0.025
            left = max(0.0, min(source.x, target.x) - margin)
            top = max(0.0, min(source.y, target.y) - margin)
            width = min(1.0 - left, max(0.04, abs(target.x - source.x) + 2 * margin))
            height = min(1.0 - top, max(0.04, abs(target.y - source.y) + 2 * margin))
            add(
                ActionType.CONNECT,
                {
                    "relation_id": relation.id,
                    "logical_connector_id": relation.id,
                    "source_entity_id": relation.source,
                    "target_entity_id": relation.target,
                    "connector_type": CONNECTOR_MAP[relation.type],
                    "semantic_role": relation.type.value,
                    "direction": "source_to_target",
                    "start_anchor": "center",
                    "end_anchor": "center",
                    "expected_route": "straight",
                    "label": relation.label,
                },
            )
            actions[-1] = actions[-1].model_copy(
                update={
                    "expected_bbox": BoundingBox(
                        x=left,
                        y=top,
                        width=width,
                        height=height,
                        coordinate_space=CoordinateSpace.NORMALIZED_CANVAS,
                    )
                }
            )
        for entity in spec.entities:
            add(
                ActionType.GROUP_ELEMENTS,
                {
                    "element_ids": [entity.id, f"label_{entity.id}"],
                    "group_id": f"group_{entity.id}",
                    "logical_group_id": f"group_{entity.id}",
                },
            )
        add(
            ActionType.CAPTURE_CANVAS,
            {
                "scope": "full_canvas",
                "expected_asset_ids": [entity.id for entity in spec.entities],
                "expected_label_ids": [f"label_{entity.id}" for entity in spec.entities],
                "expected_relation_ids": [relation.id for relation in spec.relations],
                "expected_group_ids": [f"group_{entity.id}" for entity in spec.entities],
                "expected_region_ids": [region.id for region in layout.regions],
                "verify_layout_quality": True,
            },
        )
        add(
            ActionType.SAVE_PROJECT,
            {
                "mode": "biorender_autosave",
                "logical_save_id": "document_save",
                "expected_statuses": ["saved", "all changes saved"],
            },
        )
        return actions
