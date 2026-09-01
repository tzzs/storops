"""Core data models shared across StorOps' platform and CLI layers.

Field names are deliberately 1:1 with the PowerShell v1 JSON output (only
PascalCase -> snake_case), and JSON serialization (see output/json.py)
re-emits them as PascalCase -- existing `-Json`/`--json` consumers should
not need to change a single field name. See docs/plans/
storops-v2-cross-platform-refactor.md §2.4/§2.6 for the design rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Entry:
    """One scanned filesystem entry (file or folder)."""

    full_name: str
    is_folder: bool
    size_bytes: int
    allocated_bytes: int
    modified: datetime | None = None
    file_count: int | None = None
    folder_count: int | None = None


@dataclass(frozen=True)
class Capacity:
    """Total/used/free capacity for the volume/filesystem containing a path."""

    drive: str
    total_bytes: int
    free_bytes: int
    used_bytes: int
    volume_name: str | None = None
    file_system: str | None = None


@dataclass(frozen=True)
class Rule:
    """One parsed rules/*.yaml entry. See rules/README.md for the schema."""

    id: str
    application: str | None
    category: str
    path_patterns: tuple[str, ...]
    cleanup_risk: str
    confidence: float = 0.5
    owner: str = "user"
    purpose: str | None = None
    deletable: bool = False
    migratable: bool = False
    migration_method: str | None = None
    migration_config_hint: str | None = None
    migration_requires_app_closed: bool = False
    cleanup_consequence: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PathIdentity:
    """Result of classifying a single path against the rule base."""

    path: str
    application: str | None
    category: str
    confidence: float
    owner: str | None
    purpose: str | None
    deletable: bool
    migratable: bool
    migration_method: str | None
    migration_hint: str | None
    requires_app_closed: bool
    cleanup_risk: str  # low | medium | high | critical
    consequence: str | None
    notes: str | None
    matched_rule_id: str | None
    matched_pattern: str | None


@dataclass(frozen=True)
class RecommendedAction:
    action: str  # KEEP | DELETE | MOVE | CHECK
    reason: str


@dataclass(frozen=True)
class ScanWarning:
    """A non-fatal problem encountered during a scan (e.g. permission denied).

    New in the Python rewrite -- see docs/plans/... §2.6: a single
    unreadable subtree must never abort an entire scan; it is collected
    here and surfaced structurally instead.
    """

    path: str
    code: str
    message: str


@dataclass(frozen=True)
class CleanupItem:
    id: str | None
    path: str
    application: str | None
    category: str
    size_bytes: int
    risk: str
    consequence: str | None
    action: str
    approved: bool


@dataclass(frozen=True)
class CleanupPlan:
    generated_at: str
    max_risk: str
    items: tuple[CleanupItem, ...]
    total_reclaimable_bytes: int
    total_candidate_bytes: int
    backend: str
    backend_advice: str | None


@dataclass(frozen=True)
class CleanupResultItem:
    path: str
    size_bytes: int
    status: str  # deleted | skipped | failed
    detail: str | None


@dataclass(frozen=True)
class CleanupResult:
    plan_file: str
    executed_at: str
    results: tuple[CleanupResultItem, ...]
    reclaimed_bytes: int


@dataclass(frozen=True)
class MigratePlan:
    generated_at: str
    source: str
    destination: str
    application: str | None
    category: str
    size_bytes: int
    risk: str
    requires_app_closed: bool
    process_guess: str
    method: str  # "junction" (Windows) | "symlink" (Linux/macOS) | rule's migration_method
    migration_hint: str | None
    steps: tuple[str, ...]
    backend: str
    backend_advice: str | None


@dataclass(frozen=True)
class DirStats:
    file_count: int
    size_bytes: int


@dataclass(frozen=True)
class MigrateResult:
    plan_file: str
    executed_at: str
    source: str
    destination: str
    method: str
    pre_copy: DirStats
    post_copy: DirStats
    verified: bool
    source_removed: bool
    link_created: bool
    status: str
    detail: str | None


@dataclass(frozen=True)
class VerifyCheck:
    check: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class VerifyReport:
    passed: bool
    checks: tuple[VerifyCheck, ...]


# --- Command result models (returned by core/scan.py orchestration, ------
# --- consumed by cli.py / output/{json,human}.py) -------------------------


@dataclass(frozen=True)
class ScanRow:
    path: str
    is_folder: bool
    size_bytes: int
    application: str | None
    category: str
    confidence: float
    cleanup_risk: str


@dataclass(frozen=True)
class ScanResult:
    scanned_path: str
    drive: Capacity | None
    entries: tuple[ScanRow, ...]
    backend: str
    backend_advice: str | None
    warnings: tuple[ScanWarning, ...] = ()


@dataclass(frozen=True)
class InspectRow:
    path: str
    is_folder: bool
    size_bytes: int
    application: str | None
    category: str
    confidence: float
    cleanup_risk: str
    recommended: str


@dataclass(frozen=True)
class InspectResult:
    inspected_path: str
    entries: tuple[InspectRow, ...]
    backend: str
    backend_advice: str | None
    warnings: tuple[ScanWarning, ...] = ()


@dataclass(frozen=True)
class SearchRow:
    path: str
    is_folder: bool
    size_bytes: int
    modified: datetime | None
    application: str | None
    category: str


@dataclass(frozen=True)
class SearchResult:
    searched_path: str
    name_pattern: str | None
    match_count: int
    returned_count: int
    entries: tuple[SearchRow, ...]
    backend: str
    backend_advice: str | None
    warnings: tuple[ScanWarning, ...] = ()


@dataclass(frozen=True)
class IdentifyResult:
    identity: PathIdentity
    recommended: RecommendedAction
    size_bytes: int | None
