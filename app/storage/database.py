from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas.biorender_probe import ProbeCheckpoint, ProbeStatus, UiCalibrationProfile
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, BoundingBox, GuiAction, GuiActionResult

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS figures (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    requirement_json TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    asset_plan_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS figure_entities (
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted INTEGER NOT NULL DEFAULT 0,
    actual_bbox_json TEXT,
    move_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (figure_id, entity_id)
);

CREATE TABLE IF NOT EXISTS figure_relations (
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    relation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    drawn INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (figure_id, relation_id)
);

CREATE TABLE IF NOT EXISTS gui_actions (
    id TEXT PRIMARY KEY,
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error_type TEXT,
    expected_bbox_json TEXT,
    observed_bbox_json TEXT,
    observation_confidence REAL,
    observation_source TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (figure_id, sequence)
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    action_id TEXT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    verification_type TEXT NOT NULL,
    passed INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_profiles (
    profile_id TEXT PRIMARY KEY,
    ui_profile_version TEXT NOT NULL,
    editor_url TEXT NOT NULL,
    status TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    profile_path TEXT NOT NULL,
    screenshot_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS probe_runs (
    id TEXT PRIMARY KEY,
    editor_url TEXT NOT NULL,
    query TEXT NOT NULL,
    profile_version TEXT,
    status TEXT NOT NULL,
    checkpoint_json TEXT,
    result_json TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS probe_actions (
    run_id TEXT NOT NULL REFERENCES probe_runs(id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    status TEXT NOT NULL,
    expected_bbox_json TEXT,
    observed_bbox_json TEXT,
    observation_confidence REAL,
    observation_source TEXT,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, action_id)
);

CREATE TABLE IF NOT EXISTS editor_elements (
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    element_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    figure_element_id TEXT,
    expected_bbox_json TEXT,
    bbox_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    observation_confidence REAL,
    observation_source TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    verification_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (figure_id, element_id)
);

CREATE TABLE IF NOT EXISTS element_requirements (
    figure_id TEXT NOT NULL REFERENCES figures(id) ON DELETE CASCADE,
    logical_element_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scientific_role TEXT NOT NULL,
    requirement_json TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (figure_id, logical_element_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    figure_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gui_actions_figure_status
ON gui_actions(figure_id, status, sequence);

CREATE INDEX IF NOT EXISTS idx_editor_elements_figure_kind
ON editor_elements(figure_id, kind);

CREATE INDEX IF NOT EXISTS idx_element_requirements_figure_kind
ON element_requirements(figure_id, kind);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FigureDatabase:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or settings.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_v2(connection)

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(gui_actions)").fetchall()
        }
        additions = {
            "expected_bbox_json": "TEXT",
            "observed_bbox_json": "TEXT",
            "observation_confidence": "REAL",
            "observation_source": "TEXT",
        }
        for column, column_type in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE gui_actions ADD COLUMN {column} {column_type}"
                )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (2, _now()),
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (3, _now()),
        )
        editor_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(editor_elements)").fetchall()
        }
        editor_additions = {
            "figure_element_id": "TEXT",
            "expected_bbox_json": "TEXT",
            "observation_confidence": "REAL",
            "observation_source": "TEXT",
            "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "verification_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, column_type in editor_additions.items():
            if column not in editor_columns:
                connection.execute(
                    f"ALTER TABLE editor_elements ADD COLUMN {column} {column_type}"
                )
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (4, _now()),
        )

    def save_bundle(self, bundle: PlanningBundle) -> None:
        now = _now()
        spec = bundle.figure_spec
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO figures (
                    id, title, status, requirement_json, spec_json, layout_json,
                    asset_plan_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    status=excluded.status,
                    requirement_json=excluded.requirement_json,
                    spec_json=excluded.spec_json,
                    layout_json=excluded.layout_json,
                    asset_plan_json=excluded.asset_plan_json,
                    updated_at=excluded.updated_at
                """,
                (
                    spec.id,
                    spec.title,
                    bundle.status.value,
                    bundle.requirement.model_dump_json(),
                    spec.model_dump_json(),
                    bundle.layout_spec.model_dump_json(),
                    bundle.asset_plan.model_dump_json(),
                    now,
                    now,
                ),
            )
            connection.execute("DELETE FROM figure_entities WHERE figure_id = ?", (spec.id,))
            connection.execute("DELETE FROM figure_relations WHERE figure_id = ?", (spec.id,))
            connection.execute("DELETE FROM gui_actions WHERE figure_id = ?", (spec.id,))
            connection.execute(
                "DELETE FROM element_requirements WHERE figure_id = ?", (spec.id,)
            )
            connection.executemany(
                """
                INSERT INTO figure_entities (figure_id, entity_id, payload_json)
                VALUES (?, ?, ?)
                """,
                [
                    (spec.id, entity.id, entity.model_dump_json())
                    for entity in spec.entities
                ],
            )
            connection.executemany(
                """
                INSERT INTO figure_relations (figure_id, relation_id, payload_json)
                VALUES (?, ?, ?)
                """,
                [
                    (spec.id, relation.id, relation.model_dump_json())
                    for relation in spec.relations
                ],
            )
            connection.executemany(
                """
                INSERT INTO gui_actions (
                    id, figure_id, sequence, action_type, payload_json, status,
                    expected_bbox_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        action.id,
                        action.figure_id,
                        action.sequence,
                        action.action.value,
                        action.model_dump_json(),
                        ActionStatus.PLANNED.value,
                        (
                            action.expected_bbox.model_dump_json()
                            if action.expected_bbox is not None
                            else None
                        ),
                        now,
                    )
                    for action in bundle.actions
                ],
            )
            connection.executemany(
                """
                INSERT INTO element_requirements (
                    figure_id, logical_element_id, kind, scientific_role,
                    requirement_json, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                self._element_requirement_rows(bundle, now),
            )
            connection.execute(
                """
                INSERT INTO verification_results (
                    figure_id, verification_type, passed, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    spec.id,
                    "scientific_guard",
                    int(bundle.scientific_validation.passed),
                    bundle.scientific_validation.model_dump_json(),
                    now,
                ),
            )

    @staticmethod
    def _element_requirement_rows(
        bundle: PlanningBundle,
        now: str,
    ) -> list[tuple[str, str, str, str, str, str, str]]:
        spec = bundle.figure_spec
        actions = bundle.actions

        def action_payloads(predicate: Any) -> list[dict[str, Any]]:
            return [
                {
                    "action_id": action.id,
                    "action_type": action.action.value,
                    "sequence": action.sequence,
                    "expected_bbox": (
                        action.expected_bbox.model_dump(mode="json")
                        if action.expected_bbox is not None
                        else None
                    ),
                }
                for action in actions
                if predicate(action)
            ]

        rows: list[tuple[str, str, str, str, str, str, str]] = []
        assets = {item.entity_id: item for item in bundle.asset_plan.items}
        placements = {
            placement.entity_id: placement
            for placement in bundle.layout_spec.placements
        }
        for entity in spec.entities:
            item = assets[entity.id]
            required_actions = action_payloads(
                lambda action, entity_id=entity.id: (
                    action.arguments.get("entity_id") == entity_id
                    or (
                        action.arguments.get("element_id") == entity_id
                        and action.arguments.get("element_kind") == "asset"
                    )
                )
            )
            expected = {
                "logical_element_id": entity.id,
                "concept": entity.concept,
                "label": entity.label,
                "region_id": entity.region_id,
                "search_query": item.search_terms[0],
                "fallback_queries": item.search_terms[1:],
                "placement": placements[entity.id].model_dump(mode="json"),
                "required_actions": required_actions,
                "identity_policy": "weak_fingerprint_not_result_ordinal",
            }
            rows.append(
                (
                    spec.id,
                    entity.id,
                    "asset",
                    entity.concept,
                    json.dumps(expected, ensure_ascii=False),
                    "planned",
                    now,
                )
            )
            label_id = f"label_{entity.id}"
            label_actions = action_payloads(
                lambda action, value=label_id: action.arguments.get("element_id") == value
            )
            label_expected = {
                "logical_label_id": label_id,
                "target_element_id": entity.id,
                "expected_text": entity.label,
                "required_actions": label_actions,
                "association_rule": "exact_text_and_nearest_expected_target",
            }
            rows.append(
                (
                    spec.id,
                    label_id,
                    "label",
                    f"Label for {entity.concept}",
                    json.dumps(label_expected, ensure_ascii=False),
                    "planned",
                    now,
                )
            )
        for relation in spec.relations:
            connector_action = next(
                action
                for action in actions
                if action.arguments.get("relation_id") == relation.id
            )
            connector_actions = action_payloads(
                lambda action, value=relation.id: action.arguments.get("relation_id")
                == value
            )
            connector_expected = {
                "logical_connector_id": relation.id,
                "source_element_id": relation.source,
                "target_element_id": relation.target,
                "semantic_role": relation.type.value,
                "connector_type": connector_action.arguments["connector_type"],
                "direction": "source_to_target",
                "start_anchor": connector_action.arguments["start_anchor"],
                "end_anchor": connector_action.arguments["end_anchor"],
                "expected_route": connector_action.arguments["expected_route"],
                "required_actions": connector_actions,
            }
            rows.append(
                (
                    spec.id,
                    relation.id,
                    "connector",
                    relation.label or relation.type.value,
                    json.dumps(connector_expected, ensure_ascii=False),
                    "planned",
                    now,
                )
            )
        for action in actions:
            if action.action.value == "group_elements":
                logical_id = str(action.arguments["group_id"])
                kind = "group"
                role = f"Group {', '.join(action.arguments['element_ids'])}"
            elif action.action.value in {"align_elements", "distribute_elements"}:
                logical_id = str(action.arguments["logical_layout_id"])
                kind = "alignment" if action.action.value == "align_elements" else "distribution"
                role = action.action.value
            else:
                continue
            rows.append(
                (
                    spec.id,
                    logical_id,
                    kind,
                    role,
                    json.dumps(
                        {
                            **action.arguments,
                            "required_actions": action_payloads(
                                lambda candidate, value=action.id: candidate.id == value
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    "planned",
                    now,
                )
            )
        for region in bundle.layout_spec.regions:
            rows.append(
                (
                    spec.id,
                    f"region_{region.id}",
                    "region",
                    region.title or region.id,
                    json.dumps(region.model_dump(mode="json"), ensure_ascii=False),
                    "planned",
                    now,
                )
            )
        rows.extend(
            [
                (
                    spec.id,
                    "layout_z_order",
                    "z_order",
                    "Connectors behind assets and labels",
                    json.dumps(
                        {"rule": "connector_z_index_not_above_asset_or_label"},
                        ensure_ascii=False,
                    ),
                    "planned",
                    now,
                ),
                (
                    spec.id,
                    "document_save",
                    "save_state",
                    "BioRender editor autosave confirmation",
                    json.dumps(
                        {
                            "allowed": "visible autosave status",
                            "forbidden": ["export", "download", "share", "publish"],
                        },
                        ensure_ascii=False,
                    ),
                    "planned",
                    now,
                ),
            ]
        )
        return rows

    def get_figure(self, figure_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM figures WHERE id = ?", (figure_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("requirement_json", "spec_json", "layout_json", "asset_plan_json"):
            record[key.removesuffix("_json")] = json.loads(record.pop(key))
        return record

    def list_figures(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, status, created_at, updated_at
                FROM figures ORDER BY updated_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_actions(self, figure_id: str) -> list[GuiAction]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM gui_actions WHERE figure_id = ? ORDER BY sequence",
                (figure_id,),
            ).fetchall()
        return [GuiAction.model_validate_json(row["payload_json"]) for row in rows]

    def action_states(self, figure_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, sequence, action_type, status, attempts, error_type, result_json,
                       expected_bbox_json, observed_bbox_json, observation_confidence,
                       observation_source
                FROM gui_actions WHERE figure_id = ? ORDER BY sequence
                """,
                (figure_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if item["result_json"]:
                item["result"] = json.loads(item.pop("result_json"))
            else:
                item.pop("result_json")
            for bbox_key in ("expected_bbox_json", "observed_bbox_json"):
                output_key = bbox_key.removesuffix("_json")
                item[output_key] = json.loads(item.pop(bbox_key)) if item[bbox_key] else None
            result.append(item)
        return result

    def action_state(self, action_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, figure_id, sequence, action_type, status, attempts, error_type,
                       result_json, expected_bbox_json, observed_bbox_json,
                       observation_confidence, observation_source
                FROM gui_actions WHERE id = ?
                """,
                (action_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json")) if item["result_json"] else None
        for bbox_key in ("expected_bbox_json", "observed_bbox_json"):
            output_key = bbox_key.removesuffix("_json")
            item[output_key] = json.loads(item.pop(bbox_key)) if item[bbox_key] else None
        return item

    def pending_actions(self, figure_id: str) -> list[GuiAction]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM gui_actions
                WHERE figure_id = ? AND status NOT IN (?, ?, ?, ?, ?)
                ORDER BY sequence
                """,
                (
                    figure_id,
                    ActionStatus.SUCCEEDED.value,
                    ActionStatus.VERIFIED.value,
                    ActionStatus.EXECUTED_UNVERIFIED.value,
                    ActionStatus.UNKNOWN.value,
                    ActionStatus.BLOCKED_BY_POLICY.value,
                ),
            ).fetchall()
        return [GuiAction.model_validate_json(row["payload_json"]) for row in rows]

    def mark_action_running(self, action_id: str, attempt: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE gui_actions SET status = ?, attempts = ?, updated_at = ? WHERE id = ?
                """,
                (ActionStatus.EXECUTING.value, attempt, _now(), action_id),
            )

    def record_action_result(self, figure_id: str, result: GuiActionResult) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE gui_actions
                SET status = ?, attempts = ?, result_json = ?, error_type = ?,
                    expected_bbox_json = COALESCE(?, expected_bbox_json),
                    observed_bbox_json = ?, observation_confidence = ?,
                    observation_source = ?, updated_at = ?
                WHERE id = ? AND figure_id = ?
                """,
                (
                    result.status.value,
                    result.attempt,
                    result.model_dump_json(),
                    result.error_type,
                    (
                        result.expected_bbox.model_dump_json()
                        if result.expected_bbox is not None
                        else None
                    ),
                    (
                        result.observed_bbox.model_dump_json()
                        if result.observed_bbox is not None
                        else None
                    ),
                    result.observation_confidence,
                    result.observation_source.value if result.observation_source else None,
                    _now(),
                    result.action_id,
                    figure_id,
                ),
            )
            if result.screenshot_path:
                connection.execute(
                    """
                    INSERT INTO screenshots (figure_id, action_id, path, kind, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        figure_id,
                        result.action_id,
                        result.screenshot_path,
                        result.metadata.get("evidence_kind", "screenshot"),
                        _now(),
                    ),
                )

    def save_calibration_profile(
        self,
        profile: UiCalibrationProfile,
        profile_path: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO calibration_profiles (
                    profile_id, ui_profile_version, editor_url, status, profile_json,
                    profile_path, screenshot_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.profile_id,
                    profile.ui_profile_version,
                    profile.url,
                    profile.status.value,
                    profile.model_dump_json(),
                    profile_path,
                    profile.screenshot_path,
                    profile.created_at,
                ),
            )

    def latest_calibration_profile(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT profile_id, ui_profile_version, status, profile_json,
                       profile_path, screenshot_path, created_at
                FROM calibration_profiles ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["profile"] = json.loads(item.pop("profile_json"))
        return item

    def list_screenshots(self, figure_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, action_id, path, kind, created_at
                FROM screenshots WHERE figure_id = ? ORDER BY id DESC
                """,
                (figure_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_screenshot(self, screenshot_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, figure_id, action_id, path, kind, created_at
                FROM screenshots WHERE id = ?
                """,
                (screenshot_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_probe_run(
        self,
        run_id: str,
        editor_url: str,
        query: str,
        status: ProbeStatus = ProbeStatus.PLANNED,
    ) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO probe_runs (
                    id, editor_url, query, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, editor_url, query, status.value, now, now),
            )

    def update_probe_run(
        self,
        run_id: str,
        status: ProbeStatus,
        *,
        profile_version: str | None = None,
        checkpoint: ProbeCheckpoint | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE probe_runs SET
                    status = ?,
                    profile_version = COALESCE(?, profile_version),
                    checkpoint_json = COALESCE(?, checkpoint_json),
                    result_json = COALESCE(?, result_json),
                    error_json = COALESCE(?, error_json),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    profile_version,
                    checkpoint.model_dump_json() if checkpoint else None,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                    _now(),
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown probe run {run_id!r}")

    def get_probe_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM probe_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("checkpoint_json", "result_json", "error_json"):
            output_key = key.removesuffix("_json")
            record[output_key] = json.loads(record.pop(key)) if record[key] else None
        return record

    def record_probe_action(
        self,
        run_id: str,
        action_id: str,
        status: ActionStatus,
        *,
        expected_bbox: dict[str, Any] | None = None,
        observed_bbox: dict[str, Any] | None = None,
        observation_confidence: float | None = None,
        observation_source: str | None = None,
        evidence: list[str] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO probe_actions (
                    run_id, action_id, status, expected_bbox_json, observed_bbox_json,
                    observation_confidence, observation_source, evidence_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, action_id) DO UPDATE SET
                    status=excluded.status,
                    expected_bbox_json=COALESCE(excluded.expected_bbox_json, expected_bbox_json),
                    observed_bbox_json=excluded.observed_bbox_json,
                    observation_confidence=excluded.observation_confidence,
                    observation_source=excluded.observation_source,
                    evidence_json=excluded.evidence_json,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    action_id,
                    status.value,
                    json.dumps(expected_bbox) if expected_bbox else None,
                    json.dumps(observed_bbox) if observed_bbox else None,
                    observation_confidence,
                    observation_source,
                    json.dumps(evidence or []),
                    _now(),
                ),
            )

    def probe_actions(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM probe_actions WHERE run_id = ? ORDER BY updated_at",
                (run_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("expected_bbox_json", "observed_bbox_json", "evidence_json"):
                output_key = key.removesuffix("_json")
                item[output_key] = json.loads(item.pop(key)) if item[key] else None
            results.append(item)
        return results

    def upsert_editor_element(
        self,
        figure_id: str,
        element_id: str,
        kind: str,
        bbox: BoundingBox,
        *,
        payload: dict[str, Any] | None = None,
        status: str = "verified",
        figure_element_id: str | None = None,
        expected_bbox: BoundingBox | None = None,
        observation_confidence: float | None = None,
        observation_source: str | None = None,
        evidence_refs: list[str] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT payload_json, evidence_json, verification_json
                FROM editor_elements WHERE figure_id = ? AND element_id = ?
                """,
                (figure_id, element_id),
            ).fetchone()
            merged_payload = json.loads(existing["payload_json"]) if existing else {}
            merged_payload.update(payload or {})
            merged_evidence = json.loads(existing["evidence_json"]) if existing else []
            merged_evidence = list(
                dict.fromkeys([*merged_evidence, *(evidence_refs or [])])
            )
            merged_verification = (
                json.loads(existing["verification_json"]) if existing else {}
            )
            merged_verification.update(verification or {})
            connection.execute(
                """
                INSERT INTO editor_elements (
                    figure_id, element_id, kind, figure_element_id,
                    expected_bbox_json, bbox_json, payload_json, status,
                    observation_confidence, observation_source, evidence_json,
                    verification_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(figure_id, element_id) DO UPDATE SET
                    kind=excluded.kind,
                    figure_element_id=COALESCE(
                        excluded.figure_element_id, editor_elements.figure_element_id
                    ),
                    expected_bbox_json=COALESCE(
                        excluded.expected_bbox_json, editor_elements.expected_bbox_json
                    ),
                    bbox_json=excluded.bbox_json,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    observation_confidence=COALESCE(
                        excluded.observation_confidence,
                        editor_elements.observation_confidence
                    ),
                    observation_source=COALESCE(
                        excluded.observation_source, editor_elements.observation_source
                    ),
                    evidence_json=excluded.evidence_json,
                    verification_json=excluded.verification_json,
                    updated_at=excluded.updated_at
                """,
                (
                    figure_id,
                    element_id,
                    kind,
                    figure_element_id,
                    expected_bbox.model_dump_json() if expected_bbox else None,
                    bbox.model_dump_json(),
                    json.dumps(merged_payload, ensure_ascii=False),
                    status,
                    observation_confidence,
                    observation_source,
                    json.dumps(merged_evidence, ensure_ascii=False),
                    json.dumps(merged_verification, ensure_ascii=False),
                    _now(),
                ),
            )
            connection.execute(
                """
                UPDATE element_requirements SET status = ?, updated_at = ?
                WHERE figure_id = ? AND logical_element_id = ?
                """,
                (status, _now(), figure_id, element_id),
            )

    def get_editor_element(
        self,
        figure_id: str,
        element_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT element_id, kind, figure_element_id, expected_bbox_json,
                       bbox_json, payload_json, status, observation_confidence,
                       observation_source, evidence_json, verification_json, updated_at
                FROM editor_elements WHERE figure_id = ? AND element_id = ?
                """,
                (figure_id, element_id),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["bbox"] = json.loads(item.pop("bbox_json"))
        item["expected_bbox"] = (
            json.loads(item.pop("expected_bbox_json"))
            if item["expected_bbox_json"]
            else None
        )
        item["payload"] = json.loads(item.pop("payload_json"))
        item["evidence_refs"] = json.loads(item.pop("evidence_json"))
        item["verification"] = json.loads(item.pop("verification_json"))
        return item

    def list_editor_elements(
        self,
        figure_id: str,
        *,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            """
            SELECT element_id, kind, figure_element_id, expected_bbox_json,
                   bbox_json, payload_json, status, observation_confidence,
                   observation_source, evidence_json, verification_json, updated_at
            FROM editor_elements WHERE figure_id = ?
            """
        )
        parameters: tuple[Any, ...] = (figure_id,)
        if kind is not None:
            query += " AND kind = ?"
            parameters = (figure_id, kind)
        query += " ORDER BY element_id"
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["bbox"] = json.loads(item.pop("bbox_json"))
            item["expected_bbox"] = (
                json.loads(item.pop("expected_bbox_json"))
                if item["expected_bbox_json"]
                else None
            )
            item["payload"] = json.loads(item.pop("payload_json"))
            item["evidence_refs"] = json.loads(item.pop("evidence_json"))
            item["verification"] = json.loads(item.pop("verification_json"))
            results.append(item)
        return results

    def list_element_requirements(
        self,
        figure_id: str,
        *,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT logical_element_id, kind, scientific_role, requirement_json, "
            "status, updated_at FROM element_requirements WHERE figure_id = ?"
        )
        parameters: tuple[Any, ...] = (figure_id,)
        if kind is not None:
            query += " AND kind = ?"
            parameters = (figure_id, kind)
        query += " ORDER BY kind, logical_element_id"
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["requirement"] = json.loads(item.pop("requirement_json"))
            results.append(item)
        return results

    def update_element_requirement_status(
        self,
        figure_id: str,
        logical_element_id: str,
        status: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE element_requirements SET status = ?, updated_at = ?
                WHERE figure_id = ? AND logical_element_id = ?
                """,
                (status, _now(), figure_id, logical_element_id),
            )

    def add_audit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        figure_id: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    run_id, figure_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    figure_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    _now(),
                ),
            )

    def list_audit_events(
        self,
        *,
        figure_id: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events WHERE 1 = 1"
        parameters: list[Any] = []
        if figure_id is not None:
            query += " AND figure_id = ?"
            parameters.append(figure_id)
        if run_id is not None:
            query += " AND run_id = ?"
            parameters.append(run_id)
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            results.append(item)
        return results

    def set_status(self, figure_id: str, status: FigureStatus) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE figures SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), figure_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown figure {figure_id!r}")

    def add_verification(
        self,
        figure_id: str,
        verification_type: str,
        passed: bool,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_results (
                    figure_id, verification_type, passed, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (figure_id, verification_type, int(passed), json.dumps(payload), _now()),
            )

    def get_verifications(self, figure_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT verification_type, passed, payload_json, created_at
                FROM verification_results WHERE figure_id = ? ORDER BY id
                """,
                (figure_id,),
            ).fetchall()
        return [
            {
                "verification_type": row["verification_type"],
                "passed": bool(row["passed"]),
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
