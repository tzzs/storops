"""StorOps unified CLI. See docs/plans/storops-v2-cross-platform-refactor.md
§2.5 for the command table and §2.7 for the exit-code contract.

Stdout/stderr discipline (§2.6/§31): in `--json` mode, stdout carries
EXACTLY one JSON document and nothing else -- all diagnostics (warnings,
dry-run confirmations, progress) go to stderr. In human mode, everything
goes to stdout. This module is the only place that calls print()/sys.exit().
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from storops.core import cleanup, migrate, risk, rules, scan
from storops.core.errors import ARGPARSE_EXIT_CODE, StoropsError, exit_code_for
from storops.core.models import IdentifyResult
from storops.output import human, json as json_out


def _default_root() -> str:
    return "C:\\" if sys.platform == "win32" else "/"


def _emit(args: argparse.Namespace, data: dict, human_text: str) -> None:
    if args.json:
        print(json_out.dump(data))
    else:
        print(human_text)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _read_json_file(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- subcommand handlers ----------------------------------------------------


def _cmd_scan(args: argparse.Namespace) -> int:
    result = scan.scan(args.path, top=args.top, include_files=args.include_files, admin=args.admin)
    for w in result.warnings:
        _warn(f"{w.code}: {w.path}: {w.message}")
    if result.backend_advice:
        _warn(result.backend_advice)
    _emit(args, json_out.scan_result_to_dict(result), human.render_scan(result))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    result = scan.inspect(args.path, top=args.top, folders_only=args.folders_only, admin=args.admin)
    for w in result.warnings:
        _warn(f"{w.code}: {w.path}: {w.message}")
    _emit(args, json_out.inspect_result_to_dict(result), human.render_inspect(result))
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.name_pattern and args.max_depth == 0:
        _warn(
            f"no --name-pattern and no --max-depth given: this exports every file under '{args.path}'. "
            f"Consider narrowing the path, adding --name-pattern, or setting --max-depth."
        )
    result = scan.search(
        args.path,
        name_pattern=args.name_pattern,
        min_size_gb=args.min_size_gb,
        older_than_days=args.older_than_days,
        folders=args.folders,
        top=args.top,
        max_depth=args.max_depth,
        admin=args.admin,
    )
    _emit(args, json_out.search_result_to_dict(result), human.render_search(result))
    return 0


def _cmd_identify(args: argparse.Namespace) -> int:
    identity = rules.identify_path(args.path)
    action = risk.recommended_action(identity)
    result = IdentifyResult(identity=identity, recommended=action, size_bytes=args.size_bytes)
    _emit(args, json_out.identify_result_to_dict(result), human.render_identify(result))
    return 0


def _cmd_cleanup_plan(args: argparse.Namespace) -> int:
    result = cleanup.plan(args.max_risk, out_file=args.out_file, admin=args.admin)
    from storops import platform as platform_pkg

    out_file = args.out_file or str(Path(platform_pkg.get_work_dir()) / "storops-cleanup-plan.json")
    if result.backend_advice:
        _warn(result.backend_advice)
    _emit(args, json_out.cleanup_plan_to_dict(result), human.render_cleanup_plan(result, out_file))
    return 0


def _cmd_cleanup_execute(args: argparse.Namespace) -> int:
    result = cleanup.execute(args.plan_file, confirm=args.confirm)
    if result is None:
        raw = _read_json_file(args.plan_file)
        approved = [i for i in raw.get("Items", []) if i.get("Approved")]
        if args.json:
            print(json_out.dump({"DryRun": True, "PlanFile": args.plan_file, "ApprovedItems": approved}))
        else:
            print(f"DRY RUN -- pass --confirm to actually delete. Approved items in '{args.plan_file}':")
            for item in approved:
                from storops.core.format import format_size

                print(f"  {format_size(item['SizeBytes']):>10}  {item.get('Application') or '(unidentified)':<18}{item['Path']}")
            if not approved:
                print("StorOps: no approved items in this plan -- nothing to do.")
        return 0
    _emit(args, json_out.cleanup_result_to_dict(result), human.render_cleanup_result(result))
    return 0


def _cmd_migrate_plan(args: argparse.Namespace) -> int:
    result = migrate.plan(args.source, args.destination, admin=args.admin)
    from storops import platform as platform_pkg

    out_file = str(Path(platform_pkg.get_work_dir()) / "storops-migrate-plan.json")
    _emit(args, json_out.migrate_plan_to_dict(result), human.render_migrate_plan(result, out_file))
    return 0


def _cmd_migrate_execute(args: argparse.Namespace) -> int:
    result = migrate.execute(args.plan_file, confirm=args.confirm, app_closed=args.app_closed)
    if result is None:
        raw = _read_json_file(args.plan_file)
        if args.json:
            print(json_out.dump({"DryRun": True, "PlanFile": args.plan_file, "Steps": raw.get("Steps", [])}))
        else:
            print(f"DRY RUN -- pass --confirm to actually migrate. Plan '{args.plan_file}':")
            print(f"  {raw.get('Source')}  ->  {raw.get('Destination')}")
            for i, step in enumerate(raw.get("Steps", []), 1):
                print(f"  {i}. {step}")
        return 0
    _emit(args, json_out.migrate_result_to_dict(result), human.render_migrate_result(result))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = migrate.verify(args.result_file)
    raw = _read_json_file(args.result_file)
    text = human.render_verify(report, raw.get("Source", "?"), raw.get("Destination", "?"))
    _emit(args, json_out.verify_report_to_dict(report), text)
    return 0 if report.passed else 1


# --- argument parser ---------------------------------------------------------


def _add_json_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON on stdout instead of a formatted table.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storops", description="Storage Operations for AI Agents.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan a drive or directory; report its largest immediate consumers.")
    p_scan.add_argument("path", nargs="?", default=None)
    p_scan.add_argument("--top", type=int, default=15)
    p_scan.add_argument("--include-files", action="store_true")
    p_scan.add_argument("--admin", action="store_true")
    _add_json_flag(p_scan)
    p_scan.set_defaults(func=_cmd_scan)

    p_inspect = sub.add_parser("inspect", help="Drill into a specific directory; report its largest immediate children.")
    p_inspect.add_argument("path")
    p_inspect.add_argument("--top", type=int, default=20)
    p_inspect.add_argument("--folders-only", action="store_true")
    p_inspect.add_argument("--admin", action="store_true")
    _add_json_flag(p_inspect)
    p_inspect.set_defaults(func=_cmd_inspect)

    p_search = sub.add_parser("search", help="Targeted search by name pattern, minimum size, and/or age.")
    p_search.add_argument("path", nargs="?", default=None)
    p_search.add_argument("--name-pattern")
    p_search.add_argument("--min-size-gb", type=float)
    p_search.add_argument("--older-than-days", type=int)
    p_search.add_argument("--folders", action="store_true")
    p_search.add_argument("--top", type=int, default=50)
    p_search.add_argument("--max-depth", type=int, default=0)
    p_search.add_argument("--admin", action="store_true")
    _add_json_flag(p_search)
    p_search.set_defaults(func=_cmd_search)

    p_identify = sub.add_parser("identify", help="Classify a single path using the deterministic rule base.")
    p_identify.add_argument("path")
    p_identify.add_argument("--size-bytes", type=int, default=None)
    _add_json_flag(p_identify)
    p_identify.set_defaults(func=_cmd_identify)

    p_cleanup = sub.add_parser("cleanup", help="Cleanup planning/execution.")
    cleanup_sub = p_cleanup.add_subparsers(dest="cleanup_command", required=True)

    p_cleanup_plan = cleanup_sub.add_parser("plan", help="Build an itemized, risk-classified cleanup plan.")
    p_cleanup_plan.add_argument("--max-risk", choices=["low", "medium", "high"], default="low")
    p_cleanup_plan.add_argument("--out-file")
    p_cleanup_plan.add_argument("--admin", action="store_true")
    _add_json_flag(p_cleanup_plan)
    p_cleanup_plan.set_defaults(func=_cmd_cleanup_plan)

    p_cleanup_exec = cleanup_sub.add_parser("execute", help="Execute an approved cleanup plan (requires --confirm).")
    p_cleanup_exec.add_argument("--plan-file", required=True)
    p_cleanup_exec.add_argument("--confirm", action="store_true")
    _add_json_flag(p_cleanup_exec)
    p_cleanup_exec.set_defaults(func=_cmd_cleanup_execute)

    p_migrate = sub.add_parser("migrate", help="Migration planning/execution.")
    migrate_sub = p_migrate.add_subparsers(dest="migrate_command", required=True)

    p_migrate_plan = migrate_sub.add_parser("plan", help="Plan how to move one migratable directory to a new location.")
    p_migrate_plan.add_argument("source")
    p_migrate_plan.add_argument("destination")
    p_migrate_plan.add_argument("--admin", action="store_true")
    _add_json_flag(p_migrate_plan)
    p_migrate_plan.set_defaults(func=_cmd_migrate_plan)

    p_migrate_exec = migrate_sub.add_parser("execute", help="Execute a migration plan (requires --confirm).")
    p_migrate_exec.add_argument("--plan-file", required=True)
    p_migrate_exec.add_argument("--confirm", action="store_true")
    p_migrate_exec.add_argument("--app-closed", action="store_true")
    _add_json_flag(p_migrate_exec)
    p_migrate_exec.set_defaults(func=_cmd_migrate_execute)

    p_verify = sub.add_parser("verify", help="Re-check a completed migration's current state.")
    p_verify.add_argument("--result-file", required=True)
    _add_json_flag(p_verify)
    p_verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Fill in the platform-dependent default root for scan/search now that
    # we know it wasn't supplied -- keeps the platform branch confined to
    # this one call site rather than scattered across command handlers.
    if getattr(args, "path", "__unset__") is None:
        args.path = _default_root()

    try:
        return args.func(args)
    except StoropsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exit_code_for(exc)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
