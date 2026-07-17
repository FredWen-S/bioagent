from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import settings
from app.schemas.bundle import PlanningBundle
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
                    id, figure_id, sequence, action_type, payload_json, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        action.id,
                        action.figure_id,
                        action.sequence,
                        action.action.value,
                        action.model_dump_json(),
                        ActionStatus.PENDING.value,
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
                SELECT id, sequence, action_type, status, attempts, error_type, result_json
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
            result.append(item)
        return result

    def pending_actions(self, figure_id: str) -> list[GuiAction]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM gui_actions
                WHERE figure_id = ? AND status != ? ORDER BY sequence
                """,
                (figure_id, ActionStatus.SUCCEEDED.value),
            ).fetchall()
        return [GuiAction.model_validate_json(row["payload_json"]) for row in rows]

    def mark_action_running(self, action_id: str, attempt: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE gui_actions SET status = ?, attempts = ?, updated_at = ? WHERE id = ?
                """,
                (ActionStatus.RUNNING.value, attempt, _now(), action_id),
            )

    def record_action_result(self, figure_id: str, result: GuiActionResult) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE gui_actions
                SET status = ?, attempts = ?, result_json = ?, error_type = ?, updated_at = ?
                WHERE id = ? AND figure_id = ?
                """,
                (
                    result.status.value,
                    result.attempt,
                    result.model_dump_json(),
                    result.error_type,
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

