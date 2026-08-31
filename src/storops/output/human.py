"""Human-readable (non--json) rendering for each CLI command's result.
Everything here writes to stdout via the returned string -- cli.py is
responsible for the actual print() call, keeping this module a pure
formatter (easier to unit test, and keeps the "who prints to stdout"
answer in exactly one place).
"""
from __future__ import annotations

from storops.core.format import format_size
from storops.core.models import (
    CleanupPlan,
    CleanupResult,
    IdentifyResult,
    InspectResult,
    MigratePlan,
    MigrateResult,
    ScanResult,
    SearchResult,
)
from storops.core.models import VerifyReport


def render_scan(result: ScanResult) -> str:
    lines: list[str] = []
    if result.drive:
        d = result.drive
        lines.append(
            f"{d.drive}  Total: {format_size(d.total_bytes)}  "
            f"Used: {format_size(d.used_bytes)}  Free: {format_size(d.free_bytes)}"
        )
        lines.append("")
    lines.append(f"Top {len(result.entries)} consumers under {result.scanned_path}:")
    for row in result.entries:
        label = row.application or "(unidentified)"
        risk_tag = f" [{row.cleanup_risk}]" if row.cleanup_risk in ("high", "critical") else ""
        lines.append(f"{format_size(row.size_bytes):>10}  {label:<22}{risk_tag:<12}{row.path}")
    for w in result.warnings:
        lines.append(f"warning: {w.code}: {w.path}: {w.message}")
    return "\n".join(lines)


def render_inspect(result: InspectResult) -> str:
    lines = [f"Contents of {result.inspected_path}:"]
    for row in result.entries:
        label = row.application or "(unidentified)"
        lines.append(f"{format_size(row.size_bytes):>10}  {row.recommended:<8} {label:<22}{row.cleanup_risk:<8}{row.path}")
    if not result.entries:
        lines.append("(empty, or below the reporting threshold)")
    for w in result.warnings:
        lines.append(f"warning: {w.code}: {w.path}: {w.message}")
    return "\n".join(lines)


def render_search(result: SearchResult) -> str:
    lines = [f"Found {result.match_count} match(es) under {result.searched_path} (showing top {result.returned_count}):"]
    for row in result.entries:
        label = row.application or "(unidentified)"
        mod_label = row.modified.strftime("%Y-%m-%d") if row.modified else "?"
        lines.append(f"{format_size(row.size_bytes):>10}  {mod_label:<10} {label:<18}{row.path}")
    return "\n".join(lines)


def render_identify(result: IdentifyResult) -> str:
    identity = result.identity
    lines = [identity.path]
    if result.size_bytes:
        lines.append(f"  Size:        {format_size(result.size_bytes)}")
    lines.append(f"  Application: {identity.application or '(unidentified)'}")
    lines.append(f"  Category:    {identity.category}")
    lines.append(f"  Confidence:  {identity.confidence}")
    lines.append(f"  Owner:       {identity.owner or 'n/a'}")
    if identity.purpose:
        lines.append(f"  Purpose:     {identity.purpose}")
    lines.append(f"  Deletable:   {identity.deletable}")
    lines.append(f"  Migratable:  {identity.migratable}")
    if identity.migratable:
        lines.append(f"  Migration:   {identity.migration_method} -- {identity.migration_hint}")
        lines.append(f"  App closed:  {identity.requires_app_closed}")
    lines.append(f"  Cleanup risk: {identity.cleanup_risk}")
    if identity.consequence:
        lines.append(f"  Consequence: {identity.consequence}")
    if identity.notes:
        lines.append(f"  Notes:       {identity.notes}")
    lines.append("")
    lines.append(f"  Recommended action: {result.recommended.action} -- {result.recommended.reason}")
    return "\n".join(lines)


def render_cleanup_plan(plan: CleanupPlan, out_file: str) -> str:
    lines = [f"StorOps cleanup plan (maxRisk={plan.max_risk})", ""]
    for tier in ("low", "medium", "high"):
        tier_items = [i for i in plan.items if i.risk == tier]
        if not tier_items:
            continue
        lines.append(f"[{tier} risk]")
        for item in tier_items:
            mark = "[x]" if item.approved else "[ ]"
            label = item.application or "(unidentified)"
            lines.append(f"{mark} {format_size(item.size_bytes):>10}  {label:<18}{item.path}")
            if item.consequence and tier != "low":
                lines.append(f"      -> {item.consequence}")
        lines.append("")
    if not plan.items:
        lines.append("(no deletable candidates found)")
    lines.append(f"Plan saved to: {out_file}")
    lines.append(
        f"Reclaimable (approved, <= {plan.max_risk} risk): "
        f"{format_size(plan.total_reclaimable_bytes)} of {format_size(plan.total_candidate_bytes)} candidate total"
    )
    lines.append("")
    lines.append(f"Review the plan, then run: storops cleanup execute --plan-file '{out_file}' --confirm")
    return "\n".join(lines)


def render_cleanup_result(result: CleanupResult) -> str:
    lines = [""]
    for r in result.results:
        lines.append(f"{r.status:<8} {format_size(r.size_bytes):>10}  {r.path}")
        if r.detail:
            lines.append(f"         -> {r.detail}")
    lines.append("")
    lines.append(f"Reclaimed: {format_size(result.reclaimed_bytes)}")
    lines.append(f"Result log: {result.plan_file}")
    return "\n".join(lines)


def render_migrate_plan(plan: MigratePlan, out_file: str) -> str:
    lines = [
        "StorOps migration plan",
        f"  Application: {plan.application or '(unidentified)'}",
        f"  Source:      {plan.source}",
        f"  Destination: {plan.destination}",
        f"  Size:        {format_size(plan.size_bytes)}",
        f"  Risk:        {plan.risk}",
        f"  Method:      {plan.method}",
    ]
    if plan.requires_app_closed:
        lines.append(f"  App must be closed before executing (process check: {plan.process_guess})")
    lines.append("")
    lines.append("Steps:")
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"  {i}. {step}")
    lines.append("")
    lines.append(f"Plan saved to: {out_file}")
    app_closed_flag = " --app-closed" if plan.requires_app_closed else ""
    lines.append(f"Review the plan, then run: storops migrate execute --plan-file '{out_file}' --confirm{app_closed_flag}")
    return "\n".join(lines)


def render_migrate_result(result: MigrateResult) -> str:
    lines = [f"Status: {result.status}", result.detail or "", "", f"Result log: {result.plan_file}"]
    lines.append(f"Verify later with: storops verify --result-file '{result.plan_file}'")
    return "\n".join(lines)


def render_verify(report: VerifyReport, source: str, destination: str) -> str:
    lines = [f"StorOps verification: {source} -> {destination}"]
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{mark}] {check.check:<22}{check.detail}")
    lines.append("")
    lines.append("Overall: PASS" if report.passed else "Overall: FAIL -- do not remove any remaining original data until this is resolved.")
    return "\n".join(lines)
