from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.operator.dry_run import DryRunOperator
from app.storage.database import FigureDatabase
from app.workflow.engine import WorkflowEngine


def _read_request(value: str) -> str:
    path = Path(value)
    return path.read_text(encoding="utf-8") if path.exists() else value


def _database(path: str | None) -> FigureDatabase:
    return FigureDatabase(Path(path)) if path else FigureDatabase()


def cmd_plan(args: argparse.Namespace) -> int:
    engine = WorkflowEngine(_database(args.database))
    bundle = engine.plan(_read_request(args.request), editor_url=args.editor_url)
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
    engine = WorkflowEngine(database)
    request_path = Path(__file__).resolve().parent.parent / "examples" / "pd1_request.txt"
    bundle = engine.plan(request_path.read_text(encoding="utf-8"))
    status = engine.execute(bundle.figure_spec.id, DryRunOperator())
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
    from app.operator.playwright_live import LivePlaywrightOperator
    from app.operator.biorender.calibration import BioRenderUiCalibrator
    from app.operator.errors import CalibrationFailed

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


def cmd_phase0_probe(args: argparse.Namespace) -> int:
    """Backward-compatible alias for the verified Phase 0 command."""
    if not hasattr(args, "resume_run"):
        args.resume_run = None
    if not hasattr(args, "target_width"):
        args.target_width = 0.14
    return cmd_phase0_search_drag(args)


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
