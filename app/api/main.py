from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app import __version__
from app.operator.dry_run import DryRunOperator
from app.planner.figure_planner import UnsupportedScientificRequest
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import FigureStatus
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=10_000)
    editor_url: HttpUrl = HttpUrl("https://app.biorender.com/")


class StatusResponse(BaseModel):
    figure_id: str
    status: FigureStatus


database = FigureDatabase()
engine = WorkflowEngine(database)
app = FastAPI(
    title="BioRender GUI Agent",
    version=__version__,
    description="Plan-first, auditable BioRender automation MVP. Live execution is opt-in.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/v1/figures/plan", response_model=PlanningBundle)
def plan_figure(payload: PlanRequest) -> PlanningBundle:
    try:
        return engine.plan(payload.request, editor_url=str(payload.editor_url))
    except UnsupportedScientificRequest as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/figures/{figure_id}/execute-dry-run", response_model=StatusResponse)
def execute_dry_run(figure_id: str) -> StatusResponse:
    try:
        status = engine.execute(figure_id, DryRunOperator())
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return StatusResponse(figure_id=figure_id, status=status)


@app.post("/v1/figures/{figure_id}/confirm", response_model=StatusResponse)
def confirm_figure(figure_id: str) -> StatusResponse:
    try:
        status = engine.confirm(figure_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return StatusResponse(figure_id=figure_id, status=status)


@app.get("/v1/figures/{figure_id}")
def get_figure(figure_id: str) -> dict:
    record = database.get_figure(figure_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown figure {figure_id!r}")
    record["actions"] = database.action_states(figure_id)
    record["verifications"] = database.get_verifications(figure_id)
    return record

