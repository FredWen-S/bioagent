from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app import __version__
from app.api.ui_routes import create_ui_router
from app.operator.dry_run import DryRunOperator
from app.planner.figure_planner import UnsupportedScientificRequest
from app.schemas.bundle import PlanningBundle
from app.schemas.figure_spec import FigureStatus
from app.services.figure_execution_service import FigureExecutionService, UiServiceError
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
ui_service = FigureExecutionService(database)
ui_root = Path(__file__).resolve().parents[1] / "static" / "ui"
app = FastAPI(
    title="BioRender GUI Agent",
    version=__version__,
    description="Plan-first, auditable BioRender automation MVP. Live execution is opt-in.",
)
app.mount("/ui-assets", StaticFiles(directory=ui_root, check_dir=False), name="ui-assets")
app.include_router(create_ui_router(ui_service))


@app.exception_handler(UiServiceError)
async def handle_ui_service_error(
    _request: Request,
    error: UiServiceError,
) -> JSONResponse:
    if error.error_code.endswith("NOT_FOUND"):
        status_code = 404
    elif error.error_code in {
        "LIVE_CONFIRMATION_REQUIRED",
        "MANUAL_LOGIN_CONFIRMATION_REQUIRED",
        "RUN_ALREADY_ACTIVE",
        "RUN_NOT_ACTIVE",
        "BROWSER_BUSY",
        "LOGIN_WINDOW_NOT_OPEN",
    }:
        status_code = 409
    elif error.error_code == "EVIDENCE_ACCESS_DENIED":
        status_code = 403
    else:
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": error.error_code,
            "message": str(error),
            "details": error.details,
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    if not request.url.path.startswith("/api/ui/"):
        return JSONResponse(status_code=422, content={"detail": error.errors()})
    details = [
        {
            "field": ".".join(str(part) for part in item["loc"] if part != "body"),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error_code": "INVALID_REQUEST",
            "message": "提交内容不符合要求，请检查表单。",
            "details": details,
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_ui_error(request: Request, error: Exception) -> JSONResponse:
    if not request.url.path.startswith("/api/ui/"):
        raise error
    return JSONResponse(
        status_code=500,
        content={
            "error_code": "INTERNAL_ERROR",
            "message": "服务暂时无法完成请求，请查看本机服务日志。",
            "details": None,
        },
    )


@app.middleware("http")
async def add_ui_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    if request.url.path == "/ui" or request.url.path.startswith("/ui-assets/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/ui", include_in_schema=False, response_class=FileResponse)
def graphical_ui() -> FileResponse:
    return FileResponse(ui_root / "index.html", headers={"Cache-Control": "no-store"})


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
