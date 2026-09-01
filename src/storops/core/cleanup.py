"""Plan-tier cleanup-plan + Write-tier cleanup-execute orchestration.
Ports scripts/cleanup-plan.ps1 / scripts/cleanup-execute.ps1.

Candidate discovery is deliberately narrow, matching v1: only
deletable:true rules whose path_patterns are of the form "%TOKEN%/.../*"
are probed. Patterns using a drive-relative wildcard (e.g. "?:/...",
"*/...") or a filename glob (e.g. "thumbcache_*.db") are not auto-probed --
StorOps does not guess at expanding those; they remain reachable via
identify()/search() for a targeted, user-directed look.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from storops import platform as platform_pkg
from storops.core import risk, rules
from storops.core.errors import CriticalPathError
from storops.core.models import CleanupItem, CleanupPlan, CleanupResult, CleanupResultItem
from storops.core.paths import resolve_path


def _probe_path(pattern: str) -> str | None:
    """A deletable rule's path_pattern is only usable as a concrete probe
    target when it's an env-token-rooted directory wildcard
    ("%TOKEN%/.../*"). Anything else is skipped (see module docstring).
    Accepts either `\\*` or `/*` as the trailing wildcard, since rule
    patterns are historically authored with `\\` (see core/paths.py).
    """
    if not pattern.startswith("%"):
        return None
    if "%" not in pattern[1:]:
        return None
    token_end = pattern.find("%", 1)
    if token_end < 0:
        return None
    normalized = pattern.replace("\\", "/")
    if not normalized.endswith("/*"):
        return None
    stripped = normalized[: -len("/*")]
    return rules.expand_pattern_tokens(stripped)


def plan(max_risk: str = "low", *, out_file: str | None = None, admin: bool = False) -> CleanupPlan:
    backend = platform_pkg.get_scan_backend()
    deletable_rules = [r for r in rules.load_rules() if r.deletable]

    probes: dict[str, object] = {}
    for rule in deletable_rules:
        for pattern in rule.path_patterns:
            probe = _probe_path(pattern)
            if not probe:
                continue
            probes.setdefault(probe, rule)

    items: list[CleanupItem] = []
    for probe_path, rule in probes.items():
        if not os.path.exists(probe_path):
            continue
        sized = backend.path_size(probe_path, admin=admin)
        if not sized or sized.size_bytes <= 0:
            continue

        # Defense in depth first: an independent full re-search might find
        # this probe path ALSO matches something more specific/dangerous
        # (e.g. a critical-system rule) than the deletable `rule` it was
        # derived from -- that must win. Only if the full search comes back
        # "unknown" do we fall back to identity_from_rule(rule, ...): a
        # bare probe path (the pattern's trailing "/*" already stripped off
        # to get here) will legitimately never re-match that same pattern
        # via fnmatch (nothing left for the "*" to consume against an exact
        # directory with no trailing separator) -- that is expected, not a
        # sign the rule doesn't apply; we already know it does, `rule` is
        # exactly where this probe path came from. See core/rules.py's
        # identify_path()/identity_from_rule() docstrings.
        identity = rules.identify_path(probe_path)
        if identity.category == "unknown":
            identity = rules.identity_from_rule(rule, identity.path)
        action = risk.recommended_action(identity)
        if action.action != "DELETE":
            continue

        approved = risk.within_limit(identity.cleanup_risk, max_risk)
        items.append(
            CleanupItem(
                id=identity.matched_rule_id,
                path=identity.path,
                application=identity.application,
                category=identity.category,
                size_bytes=sized.size_bytes,
                risk=identity.cleanup_risk,
                consequence=identity.consequence,
                action="DELETE",
                approved=approved,
            )
        )

    total_reclaimable = sum(i.size_bytes for i in items if i.approved)
    total_candidate = sum(i.size_bytes for i in items)

    cleanup_plan = CleanupPlan(
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(),
        max_risk=max_risk,
        items=tuple(items),
        total_reclaimable_bytes=total_reclaimable,
        total_candidate_bytes=total_candidate,
        backend=backend.name,
        backend_advice=backend.advice(),
    )

    target_file = out_file or str(Path(platform_pkg.get_work_dir()) / "storops-cleanup-plan.json")
    _write_plan_json(cleanup_plan, target_file)
    return cleanup_plan


def _write_plan_json(cleanup_plan: CleanupPlan, out_file: str) -> None:
    from storops.output.json import cleanup_plan_to_dict

    Path(out_file).write_text(
        json.dumps(cleanup_plan_to_dict(cleanup_plan), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def execute(plan_file: str, *, confirm: bool = False) -> CleanupResult | None:
    """Returns None (dry-run preview only) when confirm=False, matching
    cleanup-execute.ps1's DRY RUN behavior -- callers should print the
    would-delete list themselves in that case by loading the plan file.
    """
    if not os.path.isfile(plan_file):
        raise FileNotFoundError(f"StorOps: plan file '{plan_file}' does not exist. Run `storops cleanup plan` first.")

    raw = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    approved_items = [item for item in raw.get("Items", []) if item.get("Approved")]

    if not confirm or not approved_items:
        return None

    results: list[CleanupResultItem] = []
    for item in approved_items:
        path = item["Path"]
        size_bytes = item.get("SizeBytes", 0)

        if not os.path.exists(path):
            results.append(CleanupResultItem(path=path, size_bytes=size_bytes, status="skipped", detail="path no longer exists"))
            continue

        identity = rules.identify_path(path)
        try:
            risk.assert_not_critical(identity)
        except CriticalPathError as exc:
            results.append(CleanupResultItem(path=path, size_bytes=size_bytes, status="skipped", detail=f"refused: {exc}"))
            continue

        action = risk.recommended_action(identity)
        if action.action != "DELETE":
            results.append(
                CleanupResultItem(
                    path=path,
                    size_bytes=size_bytes,
                    status="skipped",
                    detail=f"no longer recommended for deletion (now: {action.action}) -- path may have changed since the plan was generated",
                )
            )
            continue

        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as exc:
            results.append(CleanupResultItem(path=path, size_bytes=size_bytes, status="failed", detail=f"delete failed (possibly in use): {exc}"))
            continue

        if os.path.exists(path):
            results.append(CleanupResultItem(path=path, size_bytes=size_bytes, status="failed", detail="path still present after delete -- treating as unverified/failed"))
        else:
            results.append(CleanupResultItem(path=path, size_bytes=size_bytes, status="deleted", detail=None))

    reclaimed = sum(r.size_bytes for r in results if r.status == "deleted")
    result = CleanupResult(
        plan_file=plan_file,
        executed_at=datetime.now(timezone.utc).astimezone().isoformat(),
        results=tuple(results),
        reclaimed_bytes=reclaimed,
    )

    result_file = Path(platform_pkg.get_work_dir()) / "storops-cleanup-result.json"
    from storops.output.json import cleanup_result_to_dict

    result_file.write_text(json.dumps(cleanup_result_to_dict(result), indent=2, ensure_ascii=False), encoding="utf-8")
    return result
