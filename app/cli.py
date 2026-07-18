from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.schemas.figure_spec import FigureStatus
from app.services.figure_execution_service import FigureExecutionService
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine


def _read_request(value: str) -> str:
    path = Path(value)
    return path.read_text(encoding="utf-8") if path.exists() else value


def _database(path: str | None) -> FigureDatabase:
    return FigureDatabase(Path(path)) if path else FigureDatabase()


def cmd_plan(args: argparse.Namespace) -> int:
    service = FigureExecutionService(_database(args.database))
    bundle = service.plan_prompt(_read_request(args.request), editor_url=args.editor_url)
    payload = bundle.model_dump(mode="json")
    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Wrote plan to {args.output}")
    else:
        print(output)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    database = _database(args.database)
    service = FigureExecutionService(database)
    bundle = service.plan_prompt(service.pd1_request())
    status = service.execute_dry_run(bundle.figure_spec.id)
    print(
        json.dumps(
            {
                "figure_id": bundle.figure_spec.id,
                "status": status.value,
                "entities": len(bundle.figure_spec.entities),
                "relations": len(bundle.figure_spec.relations),
                "actions": len(bundle.actions),
                "database": str(database.path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    database = _database(args.database)
    record = database.get_figure(args.figure_id)
    if record is None:
        raise SystemExit(f"Unknown figure: {args.figure_id}")
    record["actions"] = database.action_states(args.figure_id)
    record["verifications"] = database.get_verifications(args.figure_id)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def cmd_inspect_elements(args: argparse.Namespace) -> int:
    """Print the planned/observed lifecycle of every logical element."""
    database = _database(args.database)
    record = database.get_figure(args.run_id)
    if record is None:
        raise SystemExit(f"Unknown figure: {args.run_id}")
    observed = {
        item["element_id"]: item
        for item in database.list_editor_elements(args.run_id)
    }
    requirements = database.list_element_requirements(args.run_id)
    rows = []
    for requirement in requirements:
        item = observed.get(requirement["logical_element_id"])
        rows.append(
            {
                **requirement,
                "observed": item is not None,
                "figure_element_id": item.get("figure_element_id") if item else None,
                "observed_bbox": item.get("bbox") if item else None,
                "observation_source": (
                    item.get("observation_source") if item else None
                ),
                "observation_confidence": (
                    item.get("observation_confidence") if item else None
                ),
                "verification": item.get("verification") if item else None,
                "evidence_refs": item.get("evidence_refs") if item else [],
            }
        )
    print(
        json.dumps(
            {
                "figure_id": args.run_id,
                "figure_status": record["status"],
                "requirements": rows,
                "summary": {
                    "total": len(rows),
                    "verified": sum(row["status"] == "verified" for row in rows),
                    "unknown": sum(row["status"] == "unknown" for row in rows),
                    "blocked_by_policy": sum(
                        row["status"] == "blocked_by_policy" for row in rows
                    ),
                    "observed_records": len(observed),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_verify_live_figure(args: argparse.Namespace) -> int:
    """Read persisted evidence without modifying or opening the editor."""
    database = _database(args.database)
    record = database.get_figure(args.run_id)
    if record is None:
        raise SystemExit(f"Unknown figure: {args.run_id}")
    requirements = database.list_element_requirements(args.run_id)
    elements = database.list_editor_elements(args.run_id)
    counts = {
        kind: sum(
            item["kind"] == kind and item["status"] == "verified"
            for item in elements
        )
        for kind in ("asset", "label", "connector", "group")
    }
    layout = next(
        (item for item in elements if item["element_id"] == "layout_quality"),
        None,
    )
    save = next(
        (item for item in elements if item["element_id"] == "document_save"),
        None,
    )
    required_counts = {
        kind: sum(item["kind"] == kind for item in requirements)
        for kind in ("asset", "label", "connector", "group")
    }
    inventory_passed = all(
        counts[kind] >= minimum for kind, minimum in required_counts.items()
    )
    layout_passed = bool(
        layout and (layout.get("verification") or {}).get("layout", {}).get("passed")
    )
    save_passed = bool(
        save and (save.get("verification") or {}).get("save", {}).get("passed")
    )
    requirements_passed = bool(requirements) and all(
        item["status"] == "verified" for item in requirements
    )
    passed = inventory_passed and layout_passed and save_passed and requirements_passed
    document_url = (save.get("payload") or {}).get("document_url") if save else None
    environment = (
        "local_compatibility_editor"
        if isinstance(document_url, str) and document_url.startswith("file:")
        else "live_editor_evidence"
    )
    print(
        json.dumps(
            {
                "figure_id": args.run_id,
                "passed": passed,
                "read_only": True,
                "environment": environment,
                "inventory": counts,
                "required_inventory": required_counts,
                "layout_passed": layout_passed,
                "save_passed": save_passed,
                "all_element_requirements_verified": requirements_passed,
                "document_url": document_url,
                "note": (
                    "This reports persisted evidence only; it does not convert a local "
                    "compatibility-editor run into real BioRender acceptance."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


def cmd_confirm(args: argparse.Namespace) -> int:
    engine = WorkflowEngine(_database(args.database))
    status = engine.confirm(args.figure_id)
    print(json.dumps({"figure_id": args.figure_id, "status": status.value}, indent=2))
    return 0


def cmd_browser_login(args: argparse.Namespace) -> int:
    from app.operator.playwright_live import LivePlaywrightOperator

    LivePlaywrightOperator.manual_login(args.url)
    return 0


def _require_live_confirmation(args: argparse.Namespace, operation: str) -> None:
    if not args.confirm_live:
        raise SystemExit(
            f"{operation} uses a live BioRender editor. Re-run with --confirm-live after "
            "opening a disposable blank Figure and reviewing the command."
        )


def cmd_calibrate_ui(args: argparse.Namespace) -> int:
    _require_live_confirmation(args, "UI calibration")
    from app.operator.biorender.calibration import BioRenderUiCalibrator
    from app.operator.errors import CalibrationFailed
    from app.operator.playwright_live import LivePlaywrightOperator

    database = _database(args.database)
    operator = LivePlaywrightOperator(headed=True)
    try:
        page = operator.page
        page.goto(args.editor_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        profile, profile_path = BioRenderUiCalibrator(
            page, database=database
        ).calibrate()
        print(
            json.dumps(
                {
                    "status": profile.status.value,
                    "profile_id": profile.profile_id,
                    "ui_profile_version": profile.ui_profile_version,
                    "profile_path": str(profile_path),
                    "screenshot_path": profile.screenshot_path,
                    "ai_controls_recorded": len(profile.ai_controls),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except CalibrationFailed as error:
        profile_payload = None
        if error.profile_path and Path(error.profile_path).exists():
            try:
                profile_payload = json.loads(
                    Path(error.profile_path).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                profile_payload = None
        print(
            json.dumps(
                {
                    "status": (
                        profile_payload.get("status", "invalid")
                        if profile_payload
                        else "invalid"
                    ),
                    "error_type": error.error_type,
                    "message": str(error),
                    "workflow_state": "calibrating_ui",
                    "last_action": "calibrate_ui",
                    "profile_path": error.profile_path,
                    "screenshot_path": (
                        profile_payload.get("screenshot_path") if profile_payload else None
                    ),
                    "recommended_manual_checkpoint": (
                        "Open the calibration screenshot, close blocking UI, and verify the "
                        "search/results/canvas regions before retrying."
                    ),
                    "safe_to_resume": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    finally:
        operator.close()


def cmd_phase0_search_drag(args: argparse.Namespace) -> int:
    _require_live_confirmation(args, "Phase 0 search-and-drag probe")
    if not args.resume_run and not args.editor_url:
        raise SystemExit("--editor-url is required unless --resume-run is provided")
    from app.operator.biorender.probe import BioRenderSingleAssetProbe
    from app.operator.playwright_live import LivePlaywrightOperator

    database = _database(args.database)
    operator = LivePlaywrightOperator(headed=True)
    try:
        outcome = BioRenderSingleAssetProbe(
            operator.page,
            database,
        ).run(
            editor_url=args.editor_url or "",
            query=args.query,
            target_x=args.target_x,
            target_y=args.target_y,
            target_width=args.target_width,
            resume_run_id=args.resume_run,
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if outcome.get("status") in {
            "awaiting_confirmation",
            "completed_probe",
        } else 2
    finally:
        operator.close()


def cmd_live_search_asset(args: argparse.Namespace) -> int:
    """Search and inspect ordinary candidates without changing the canvas."""
    _require_live_confirmation(args, "Live ordinary-asset search probe")
    from app.operator.errors import OperatorError
    from app.operator.playwright_live import LivePlaywrightOperator
    from app.schemas.gui_action import ActionType, GuiAction

    probe_id = f"search_probe_{uuid4().hex[:12]}"
    operator = LivePlaywrightOperator(headed=True)
    try:
        opened = operator.execute(
            GuiAction(
                id="action_search_probe_open",
                figure_id=probe_id,
                sequence=0,
                action=ActionType.OPEN_EDITOR,
                arguments={
                    "url": args.editor_url,
                    "project_name": "ordinary asset search probe",
                    "create_new": False,
                },
            )
        )
        searched = operator.execute(
            GuiAction(
                id="action_search_probe_search",
                figure_id=probe_id,
                sequence=1,
                action=ActionType.SEARCH_ASSET,
                arguments={
                    "entity_id": "search_probe_asset",
                    "logical_element_id": "search_probe_asset",
                    "query": args.query,
                    "fallback_queries": [],
                    "max_queries": 1,
                },
            )
        )
        print(
            json.dumps(
                {
                    "probe_id": probe_id,
                    "status": searched.status.value,
                    "canvas_modified": False,
                    "editor": opened.metadata,
                    "search": searched.metadata,
                    "evidence_refs": searched.evidence_refs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except OperatorError as error:
        print(
            json.dumps(
                {
                    "probe_id": probe_id,
                    "status": (
                        "blocked_by_policy"
                        if error.error_type == "blocked_by_policy"
                        else "failed"
                    ),
                    "error_type": error.error_type,
                    "message": str(error),
                    "screenshot_path": error.screenshot_path,
                    "canvas_modified": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    finally:
        operator.close()


def cmd_phase0_probe(args: argparse.Namespace) -> int:
    """Backward-compatible alias for the verified Phase 0 command."""
    if not hasattr(args, "resume_run"):
        args.resume_run = None
    if not hasattr(args, "target_width"):
        args.target_width = 0.14
    return cmd_phase0_search_drag(args)


def cmd_live_figure(args: argparse.Namespace) -> int:
    _require_live_confirmation(args, "Full Figure live workflow")
    if not args.resume_figure and not args.editor_url:
        raise SystemExit("--editor-url is required unless --resume-figure is provided")
    database = _database(args.database)
    service = FigureExecutionService(database)
    if args.resume_figure:
        figure_id = args.resume_figure
        record = database.get_figure(figure_id)
        if record is None:
            raise SystemExit(f"Unknown figure: {figure_id}")
    else:
        request_value = args.request
        if request_value is None:
            request_value = str(
                Path(__file__).resolve().parent.parent
                / "examples"
                / "pd1_request.txt"
            )
        bundle = service.plan_prompt(
            _read_request(request_value),
            editor_url=args.editor_url,
        )
        figure_id = bundle.figure_spec.id
    status = service.execute_live_sync(figure_id)
    states = database.action_states(figure_id)
    output = {
        "figure_id": figure_id,
        "status": status.value,
        "verified_actions": sum(
            state["status"] == "verified" for state in states
        ),
        "total_actions": len(states),
        "last_action": states[-1] if states else None,
        "evidence_dir": str(settings.live_figure_dir / figure_id),
        "database": str(database.path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if status in {
        FigureStatus.AWAITING_CONFIRMATION,
        FigureStatus.COMPLETED,
    } else 2


def cmd_resume_live_figure(args: argparse.Namespace) -> int:
    args.resume_figure = args.run_id
    args.editor_url = None
    args.request = None
    return cmd_live_figure(args)


def cmd_web_ui(args: argparse.Namespace) -> int:
    """Start the loopback-only graphical control panel."""
    import uvicorn

    url = f"http://127.0.0.1:{args.port}/ui"
    print(f"BioRender GUI Agent Web UI:\n{url}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="biorender-agent")
    parser.add_argument("--database", help="Override the SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Create and persist a strict figure plan")
    plan.add_argument("request", help="Request text or path to a UTF-8 text file")
    plan.add_argument("--editor-url", default="https://app.biorender.com/")
    plan.add_argument("--output", help="Write the planning bundle as JSON")
    plan.set_defaults(func=cmd_plan)

    demo = subparsers.add_parser("demo", help="Plan and dry-run the bundled PD-1 example")
    demo.set_defaults(func=cmd_demo)

    status = subparsers.add_parser("status", help="Show persisted state and action evidence")
    status.add_argument("figure_id")
    status.set_defaults(func=cmd_status)

    inspect_elements = subparsers.add_parser(
        "inspect-elements",
        help="READ ONLY: inspect element requirements and persisted observations",
    )
    inspect_elements.add_argument("--run-id", required=True)
    inspect_elements.set_defaults(func=cmd_inspect_elements)

    verify_live = subparsers.add_parser(
        "verify-live-figure",
        help="READ ONLY: verify planned inventory, layout, and save evidence",
    )
    verify_live.add_argument("--run-id", required=True)
    verify_live.set_defaults(func=cmd_verify_live_figure)

    confirm = subparsers.add_parser("confirm", help="Record the user's final confirmation")
    confirm.add_argument("figure_id")
    confirm.set_defaults(func=cmd_confirm)

    login = subparsers.add_parser(
        "browser-login", help="Open the persistent profile for manual BioRender login"
    )
    login.add_argument("--url", default="https://app.biorender.com/")
    login.set_defaults(func=cmd_browser_login)

    calibrate = subparsers.add_parser(
        "calibrate-ui",
        help="LIVE: calibrate BioRender search/results/canvas regions and save evidence",
    )
    calibrate.add_argument("--editor-url", required=True)
    calibrate.add_argument("--confirm-live", action="store_true")
    calibrate.set_defaults(func=cmd_calibrate_ui)

    search_drag = subparsers.add_parser(
        "phase0-search-drag",
        help="LIVE: safely search, drag, observe, and reconcile one ordinary asset",
    )
    search_drag.add_argument("--editor-url")
    search_drag.add_argument("--query", default="T cell")
    search_drag.add_argument("--target-x", type=float, default=0.5)
    search_drag.add_argument("--target-y", type=float, default=0.5)
    search_drag.add_argument("--target-width", type=float, default=0.14)
    search_drag.add_argument("--resume-run")
    search_drag.add_argument("--confirm-live", action="store_true")
    search_drag.set_defaults(func=cmd_phase0_search_drag)

    live_search = subparsers.add_parser(
        "live-search-asset",
        help="LIVE: search and inspect one ordinary asset without changing the canvas",
    )
    live_search.add_argument("--editor-url", required=True)
    live_search.add_argument("--query", default="T cell")
    live_search.add_argument("--confirm-live", action="store_true")
    live_search.set_defaults(func=cmd_live_search_asset)

    phase0 = subparsers.add_parser(
        "phase0-probe",
        help="LIVE: search and drag one asset into a disposable blank Figure",
    )
    phase0.add_argument("--editor-url", default="https://app.biorender.com/")
    phase0.add_argument("--query", default="T cell")
    phase0.add_argument("--target-x", type=float, default=0.5)
    phase0.add_argument("--target-y", type=float, default=0.5)
    phase0.add_argument("--target-width", type=float, default=0.14)
    phase0.add_argument("--resume-run")
    phase0.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that the command will modify a BioRender Figure",
    )
    phase0.set_defaults(func=cmd_phase0_probe)

    live_figure = subparsers.add_parser(
        "live-figure",
        help="LIVE: execute or safely resume a fully observed Figure workflow",
    )
    live_figure.add_argument(
        "--request",
        help="Request text or UTF-8 file; defaults to the bundled PD-1 fixture",
    )
    live_figure.add_argument("--editor-url")
    live_figure.add_argument("--resume-figure")
    live_figure.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that the command will modify a disposable BioRender Figure",
    )
    live_figure.set_defaults(func=cmd_live_figure)

    resume_live = subparsers.add_parser(
        "resume-live-figure",
        help="LIVE: reconcile and resume an interrupted Figure by run ID",
    )
    resume_live.add_argument("--run-id", required=True)
    resume_live.add_argument(
        "--confirm-live",
        action="store_true",
        help="Acknowledge that resume may apply minimal repairs to the Figure",
    )
    resume_live.set_defaults(func=cmd_resume_live_figure)

    web_ui = subparsers.add_parser(
        "web-ui",
        help="Start the local graphical control panel at http://127.0.0.1:8000/ui",
    )
    web_ui.add_argument("--port", type=int, choices=range(1024, 65536), default=8000)
    web_ui.set_defaults(func=cmd_web_ui)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
