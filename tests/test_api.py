from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import app.api.main as api_module
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine


def test_plan_execute_and_read_api(tmp_path: Path) -> None:
    database = FigureDatabase(tmp_path / "api.db")
    api_module.database = database
    api_module.engine = WorkflowEngine(database)
    client = TestClient(api_module.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    planned = client.post(
        "/v1/figures/plan",
        json={
            "request": (
                "双栏对比：PD-1 与 PD-L1 结合并抑制 T 细胞；"
                "anti-PD-1 阻断后，T 细胞杀伤 Tumor cell。"
            )
        },
    )
    assert planned.status_code == 200
    figure_id = planned.json()["figure_spec"]["id"]

    executed = client.post(f"/v1/figures/{figure_id}/execute-dry-run")
    assert executed.status_code == 200
    assert executed.json()["status"] == "awaiting_confirmation"

    record = client.get(f"/v1/figures/{figure_id}")
    assert record.status_code == 200
    assert len(record.json()["actions"]) == len(planned.json()["actions"])
