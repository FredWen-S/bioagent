from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import settings
from app.schemas.bundle import PlanningBundle
from app.schemas.biorender_probe import ProbeCheckpoint, ProbeStatus, UiCalibrationProfile
from app.schemas.figure_spec import FigureStatus
from app.schemas.gui_action import ActionStatus, GuiAction, GuiActionResult


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

    def get_figure(self, figure_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM figures WHERE id = ?", (figure_id,)).fetchone()
        if row is None:
            return None
        record = dict(row)
        for key in ("requirement_json", "spec_json", "layout_json", "asset_plan_json"):
            record[key.removesuffix("_json")] = json.loads(record.pop(key))
        return record

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
