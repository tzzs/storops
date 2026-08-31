"""Plan-tier migrate-plan + Write-tier migrate-execute + verify orchestration.
Ports scripts/migrate-plan.ps1 / scripts/migrate-execute.ps1 / scripts/verify.ps1.

Sequence is copy-then-verify-then-remove-original, never a plain move: a
cross-volume move is a copy+delete under the hood anyway, so doing the
copy and size/count verification explicitly means an interrupted or
partial copy is caught before the original is ever touched. Verification
failure must never trigger auto-deletion of originals.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from storops import platform as platform_pkg
from storops.core import risk, rules
from storops.core.copystats import dir_stats
from storops.core.errors import StalePlanError, UnsupportedOperationError
from storops.core.models import MigratePlan, MigrateResult, VerifyCheck, VerifyReport
from storops.core.paths import resolve_path


def plan(source: str, destination: str, *, admin: bool = False) -> MigratePlan:
    src = resolve_path(source)
    if not os.path.isdir(src):
        raise UnsupportedOperationError(
            f"StorOps: '{src}' does not exist or is not a directory. migrate plan only plans directory migrations."
        )
    dest = resolve_path(destination)
    if dest == src:
        raise UnsupportedOperationError(f"StorOps: destination is the same as the source ('{src}').")
    if os.path.exists(dest) and os.listdir(dest):
        raise UnsupportedOperationError(f"StorOps: destination '{dest}' already exists and is not empty. Pick an empty or new destination.")

    identity = rules.identify_path(src)
    risk.assert_not_critical(identity)

    if not identity.migratable:
        app = identity.application or "unknown"
        raise UnsupportedOperationError(
            f"StorOps: '{src}' (identified as {app}'s {identity.category}) is not marked migratable. "
            f"Run `storops identify` for the full classification; StorOps will not invent a migration "
            f"path for an unsupported category."
        )
    if identity.migration_method == "none":
        raise UnsupportedOperationError(f"StorOps: '{src}' is explicitly marked migration_method 'none' -- this data is not meant to be relocated.")

    backend = platform_pkg.get_scan_backend()
    sized = backend.path_size(src, admin=admin)
    size_bytes = sized.size_bytes if sized else 0

    link_engine = platform_pkg.get_link_engine()
    use_link = identity.migration_method not in ("app-config", "manual")
    method = link_engine.kind if use_link else (identity.migration_method or "manual")

    process_guess = platform_pkg.guess_process_running(identity.application)

    steps: list[str] = []
    if identity.requires_app_closed:
        app = identity.application or "the owning application"
        steps.append(f"Close {app} completely (process check: {process_guess} -- verify yourself, this is a best-effort guess).")
    steps.append(f"Copy '{src}' to '{dest}' (verified copy, original left in place until verification passes).")
    steps.append("Verify the copy: file count and total size at destination must match the source.")
    if use_link:
        steps.append(f"Remove the now-redundant original at '{src}'.")
        steps.append(f"Create a {link_engine.kind} at '{src}' pointing to '{dest}'.")
        steps.append(f"Verify the {link_engine.kind} resolves and lists the same top-level contents as the destination.")
        steps.append(f"Start {identity.application or 'the application'} and confirm it works normally through the link.")
    else:
        steps.append(f"Apply the app's own relocation setting: {identity.migration_hint}")
        steps.append(f"Start {identity.application or 'the application'} and confirm it now reads/writes at '{dest}'.")
        steps.append(f"Once confirmed, remove the now-redundant original at '{src}'.")
    steps.append("Run `storops verify` against the migration result file to record the outcome.")

    migrate_plan = MigratePlan(
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(),
        source=src,
        destination=dest,
        application=identity.application,
        category=identity.category,
        size_bytes=size_bytes,
        risk=identity.cleanup_risk,
        requires_app_closed=identity.requires_app_closed,
        process_guess=process_guess,
        method=method,
        migration_hint=identity.migration_hint,
        steps=tuple(steps),
        backend=backend.name,
        backend_advice=backend.advice(),
    )

    out_file = Path(platform_pkg.get_work_dir()) / "storops-migrate-plan.json"
    from storops.output.json import migrate_plan_to_dict

    out_file.write_text(json.dumps(migrate_plan_to_dict(migrate_plan), indent=2, ensure_ascii=False), encoding="utf-8")
    return migrate_plan


def execute(plan_file: str, *, confirm: bool = False, app_closed: bool = False) -> MigrateResult | None:
    """Returns None (dry-run preview only) when confirm=False."""
    if not os.path.isfile(plan_file):
        raise FileNotFoundError(f"StorOps: plan file '{plan_file}' does not exist. Run `storops migrate plan` first.")

    raw = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    source, destination = raw["Source"], raw["Destination"]

    if not os.path.isdir(source):
        raise StalePlanError(f"StorOps: source '{source}' no longer exists or is not a directory -- the plan is stale. Re-run `storops migrate plan`.")

    identity = rules.identify_path(source)
    risk.assert_not_critical(identity)
    if not identity.migratable:
        raise StalePlanError(f"StorOps: '{source}' is no longer classified as migratable -- the plan is stale. Re-run `storops migrate plan`.")

    if raw.get("RequiresAppClosed") and not app_closed:
        app = identity.application or "the owning application"
        raise UnsupportedOperationError(f"StorOps: this plan requires {app} to be fully closed before executing. Close it, then re-run with --app-closed.")

    if not confirm:
        return None

    if os.path.exists(destination) and os.listdir(destination):
        raise StalePlanError(f"StorOps: destination '{destination}' already exists and is not empty -- the plan is stale. Re-run `storops migrate plan` with a clean destination.")
    dest_parent = os.path.dirname(destination)
    if dest_parent and not os.path.isdir(dest_parent):
        os.makedirs(dest_parent, exist_ok=True)

    copy_engine = platform_pkg.get_copy_engine()
    link_engine = platform_pkg.get_link_engine()
    method = raw.get("Method", link_engine.kind)

    pre_stats = dir_stats(source)
    copy_engine.copy(source, destination)
    post_stats = dir_stats(destination)
    verified = post_stats.file_count == pre_stats.file_count and post_stats.size_bytes == pre_stats.size_bytes

    status: str
    detail: str | None
    source_removed = False
    link_created = False

    if not verified:
        status = "verification-failed"
        detail = (
            f"File count/size mismatch after copy (source: {pre_stats.file_count} files/{pre_stats.size_bytes} bytes, "
            f"destination: {post_stats.file_count} files/{post_stats.size_bytes} bytes). Original left untouched at '{source}'."
        )
    else:
        try:
            shutil.rmtree(source)
            source_removed = True
            status, detail = "", None
        except OSError as exc:
            status = "copy-ok-source-not-removed"
            detail = f"Copy verified, but the original at '{source}' could not be removed ({exc}). Remove it manually once you've confirmed the migration is working."

        if source_removed and method == link_engine.kind:
            try:
                link_engine.create(source, destination)
                link_created = link_engine.verify(source, destination)
                if not link_created:
                    status = f"{link_engine.kind}-verification-failed"
                    detail = f"{link_engine.kind.capitalize()} was created at '{source}' but did not verify as pointing to '{destination}'. Inspect manually."
            except OSError as exc:
                status = f"{link_engine.kind}-failed"
                detail = f"Data was copied and verified, and the original was removed, but creating the {link_engine.kind} at '{source}' failed: {exc}."

        if not status:
            status = "succeeded"
            detail = (
                f"Migrated and relinked via {link_engine.kind}. {identity.application} can keep using '{source}' unchanged."
                if method == link_engine.kind
                else f"Migrated. Apply the app's relocation setting now: {raw.get('MigrationHint')}"
            )

    result = MigrateResult(
        plan_file=plan_file,
        executed_at=datetime.now(timezone.utc).astimezone().isoformat(),
        source=source,
        destination=destination,
        method=method,
        pre_copy=pre_stats,
        post_copy=post_stats,
        verified=verified,
        source_removed=source_removed,
        link_created=link_created,
        status=status,
        detail=detail,
    )

    result_file = Path(platform_pkg.get_work_dir()) / "storops-migrate-result.json"
    from storops.output.json import migrate_result_to_dict

    result_file.write_text(json.dumps(migrate_result_to_dict(result), indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def verify(result_file: str) -> VerifyReport:
    if not os.path.isfile(result_file):
        raise FileNotFoundError(f"StorOps: result file '{result_file}' does not exist.")

    raw = json.loads(Path(result_file).read_text(encoding="utf-8"))
    checks: list[VerifyCheck] = []

    target_accessible = os.path.isdir(raw["Destination"])
    checks.append(VerifyCheck(check="target-accessible", passed=target_accessible, detail=f"Destination '{raw['Destination']}'"))

    post_copy = raw.get("PostCopy", {})
    if target_accessible:
        current = dir_stats(raw["Destination"])
        count_matches = current.file_count == post_copy.get("FileCount")
        size_matches = current.size_bytes == post_copy.get("SizeBytes")
        checks.append(VerifyCheck(check="file-count-matches", passed=count_matches, detail=f"expected {post_copy.get('FileCount')}, found {current.file_count}"))
        checks.append(VerifyCheck(check="total-size-matches", passed=size_matches, detail=f"expected {post_copy.get('SizeBytes')}, found {current.size_bytes}"))
    else:
        checks.append(VerifyCheck(check="file-count-matches", passed=False, detail="destination not accessible"))
        checks.append(VerifyCheck(check="total-size-matches", passed=False, detail="destination not accessible"))

    link_engine = platform_pkg.get_link_engine()
    if raw.get("Method") == link_engine.kind:
        source_exists = os.path.exists(raw["Source"])
        checks.append(VerifyCheck(check=f"source-is-{link_engine.kind}", passed=source_exists, detail=f"Source '{raw['Source']}' should exist as a {link_engine.kind}"))
        if source_exists:
            link_ok = link_engine.verify(raw["Source"], raw["Destination"])
            checks.append(VerifyCheck(check=f"{link_engine.kind}-works", passed=link_ok, detail=f"resolves to destination: {link_ok}"))
        else:
            checks.append(VerifyCheck(check=f"{link_engine.kind}-works", passed=False, detail=f"source path does not exist -- {link_engine.kind} is missing"))
    else:
        source_cleared = not os.path.exists(raw["Source"])
        checks.append(
            VerifyCheck(
                check="source-cleared",
                passed=source_cleared,
                detail=(
                    f"Original at '{raw['Source']}' is gone, as expected"
                    if source_cleared
                    else f"Original still present at '{raw['Source']}' -- remove it once the app is confirmed working from '{raw['Destination']}'"
                ),
            )
        )

    overall_pass = all(c.passed for c in checks)
    report = VerifyReport(passed=overall_pass, checks=tuple(checks))

    raw["LastVerification"] = {
        "VerifiedAt": datetime.now(timezone.utc).astimezone().isoformat(),
        "Pass": overall_pass,
        "Checks": [{"Check": c.check, "Pass": c.passed, "Detail": c.detail} for c in checks],
    }
    Path(result_file).write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    if not overall_pass:
        # Callers decide whether to raise/exit non-zero; core itself does
        # not treat "verification failed" as an exception -- it's a valid,
        # fully-reported outcome, matching v1's verify.ps1 (exit 0 either
        # way, the JSON `Pass` field carries the result).
        pass
    return report
