"""Dataclass -> JSON-serializable dict conversion.

Field NAMES are deliberately PascalCase (not the dataclasses' own
snake_case) so existing `-Json`/`--json` consumers of the PowerShell v1
CLI see an unchanged schema -- see docs/plans/
storops-v2-cross-platform-refactor.md §2.4/§2.6. The only new field is
`Warnings` (additive, ignorable by old consumers).

This module is the only place that should ever call `print()` with JSON
content -- stdout discipline (§2.6/§31: stdout = machine-readable result,
stderr = logs/warnings) is enforced by cli.py calling `dump()` exactly
once per `--json` invocation and nothing else writing to stdout in that
code path.
"""
from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Any

from storops.core.models import (
    Capacity,
    CleanupItem,
    CleanupPlan,
    CleanupResult,
    CleanupResultItem,
    IdentifyResult,
    InspectResult,
    MigratePlan,
    MigrateResult,
    PathIdentity,
    RecommendedAction,
    ScanResult,
    ScanWarning,
    SearchResult,
    VerifyReport,
)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _capacity_to_dict(c: Capacity | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "Drive": c.drive,
        "TotalBytes": c.total_bytes,
        "FreeBytes": c.free_bytes,
        "UsedBytes": c.used_bytes,
        "VolumeName": c.volume_name,
        "FileSystem": c.file_system,
    }


def _warnings_to_list(warnings: tuple[ScanWarning, ...]) -> list[dict[str, Any]]:
    return [{"Path": w.path, "Code": w.code, "Message": w.message} for w in warnings]


def _identity_to_dict(identity: PathIdentity) -> dict[str, Any]:
    return {
        "Path": identity.path,
        "Application": identity.application,
        "Category": identity.category,
        "Confidence": identity.confidence,
        "Owner": identity.owner,
        "Purpose": identity.purpose,
        "Deletable": identity.deletable,
        "Migratable": identity.migratable,
        "MigrationMethod": identity.migration_method,
        "MigrationHint": identity.migration_hint,
        "RequiresAppClosed": identity.requires_app_closed,
        "CleanupRisk": identity.cleanup_risk,
        "Consequence": identity.consequence,
        "Notes": identity.notes,
        "MatchedRuleId": identity.matched_rule_id,
        "MatchedPattern": identity.matched_pattern,
    }


def _action_to_dict(action: RecommendedAction) -> dict[str, Any]:
    return {"Action": action.action, "Reason": action.reason}


def scan_result_to_dict(result: ScanResult) -> dict[str, Any]:
    return {
        "ScannedPath": result.scanned_path,
        "Drive": _capacity_to_dict(result.drive),
        "Entries": [
            {
                "Path": e.path,
                "IsFolder": e.is_folder,
                "SizeBytes": e.size_bytes,
                "Application": e.application,
                "Category": e.category,
                "Confidence": e.confidence,
                "CleanupRisk": e.cleanup_risk,
            }
            for e in result.entries
        ],
        "Backend": result.backend,
        "BackendAdvice": result.backend_advice,
        "Warnings": _warnings_to_list(result.warnings),
    }


def inspect_result_to_dict(result: InspectResult) -> dict[str, Any]:
    return {
        "InspectedPath": result.inspected_path,
        "Entries": [
            {
                "Path": e.path,
                "IsFolder": e.is_folder,
                "SizeBytes": e.size_bytes,
                "Application": e.application,
                "Category": e.category,
                "Confidence": e.confidence,
                "CleanupRisk": e.cleanup_risk,
                "Recommended": e.recommended,
            }
            for e in result.entries
        ],
        "Backend": result.backend,
        "BackendAdvice": result.backend_advice,
        "Warnings": _warnings_to_list(result.warnings),
    }


def search_result_to_dict(result: SearchResult) -> dict[str, Any]:
    return {
        "SearchedPath": result.searched_path,
        "NamePattern": result.name_pattern,
        "MatchCount": result.match_count,
        "ReturnedCount": result.returned_count,
        "Entries": [
            {
                "Path": e.path,
                "IsFolder": e.is_folder,
                "SizeBytes": e.size_bytes,
                "Modified": _dt(e.modified),
                "Application": e.application,
                "Category": e.category,
                "CleanupRisk": e.cleanup_risk,
                "Recommended": e.recommended,
            }
            for e in result.entries
        ],
        "Backend": result.backend,
        "BackendAdvice": result.backend_advice,
        "Warnings": _warnings_to_list(result.warnings),
    }


def identify_result_to_dict(result: IdentifyResult) -> dict[str, Any]:
    return {
        "Identity": _identity_to_dict(result.identity),
        "Recommended": _action_to_dict(result.recommended),
        "SizeBytes": result.size_bytes,
    }


def _cleanup_item_to_dict(item: CleanupItem) -> dict[str, Any]:
    return {
        "Id": item.id,
        "Path": item.path,
        "Application": item.application,
        "Category": item.category,
        "SizeBytes": item.size_bytes,
        "Risk": item.risk,
        "Consequence": item.consequence,
        "Action": item.action,
        "Approved": item.approved,
    }


def cleanup_plan_to_dict(plan: CleanupPlan) -> dict[str, Any]:
    return {
        "GeneratedAt": plan.generated_at,
        "MaxRisk": plan.max_risk,
        "Items": [_cleanup_item_to_dict(i) for i in plan.items],
        "TotalReclaimableBytes": plan.total_reclaimable_bytes,
        "TotalCandidateBytes": plan.total_candidate_bytes,
        "Backend": plan.backend,
        "BackendAdvice": plan.backend_advice,
    }


def _cleanup_result_item_to_dict(item: CleanupResultItem) -> dict[str, Any]:
    return {"Path": item.path, "SizeBytes": item.size_bytes, "Status": item.status, "Detail": item.detail}


def cleanup_result_to_dict(result: CleanupResult) -> dict[str, Any]:
    return {
        "PlanFile": result.plan_file,
        "ExecutedAt": result.executed_at,
        "Results": [_cleanup_result_item_to_dict(i) for i in result.results],
        "ReclaimedBytes": result.reclaimed_bytes,
    }


def migrate_plan_to_dict(plan: MigratePlan) -> dict[str, Any]:
    return {
        "GeneratedAt": plan.generated_at,
        "Source": plan.source,
        "Destination": plan.destination,
        "Application": plan.application,
        "Category": plan.category,
        "SizeBytes": plan.size_bytes,
        "Risk": plan.risk,
        "RequiresAppClosed": plan.requires_app_closed,
        "ProcessGuess": plan.process_guess,
        "Method": plan.method,
        "MigrationHint": plan.migration_hint,
        "Steps": list(plan.steps),
        "Backend": plan.backend,
        "BackendAdvice": plan.backend_advice,
    }


def migrate_result_to_dict(result: MigrateResult) -> dict[str, Any]:
    return {
        "PlanFile": result.plan_file,
        "ExecutedAt": result.executed_at,
        "Source": result.source,
        "Destination": result.destination,
        "Method": result.method,
        "PreCopy": {"FileCount": result.pre_copy.file_count, "SizeBytes": result.pre_copy.size_bytes},
        "PostCopy": {"FileCount": result.post_copy.file_count, "SizeBytes": result.post_copy.size_bytes},
        "Verified": result.verified,
        "SourceRemoved": result.source_removed,
        # Preserved as "JunctionCreated" for v1 back-compat (that field only
        # ever existed for the Windows Junction path); on Linux/macOS it
        # now also carries the symlink-creation outcome -- see
        # LinkEngine.kind for which one actually happened (`Method` field).
        "JunctionCreated": result.link_created,
        "Status": result.status,
        "Detail": result.detail,
    }


def verify_report_to_dict(report: VerifyReport) -> dict[str, Any]:
    return {
        "Pass": report.passed,
        "Checks": [{"Check": c.check, "Pass": c.passed, "Detail": c.detail} for c in report.checks],
    }


def dump(data: dict[str, Any]) -> str:
    """Serialize to a stable, indented JSON string. The ONLY function in
    this codebase that should be handed to `print()` for a --json command.
    """
    return _json.dumps(data, indent=2, ensure_ascii=False, default=str)
