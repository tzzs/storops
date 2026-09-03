"""Orchestration for the Read-tier scan/inspect/search capabilities.
Ports scripts/scan.ps1 / scripts/inspect.ps1 / scripts/search.ps1 -- see
docs/plans/storops-v2-cross-platform-refactor.md §2.5/§2.6.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from storops import platform as platform_pkg
from storops.core import risk, rules
from storops.core.models import (
    Entry,
    InspectResult,
    InspectRow,
    ScanResult,
    ScanRow,
    SearchResult,
    SearchRow,
)
from storops.core.paths import resolve_path


def _scan_row(entry: Entry) -> ScanRow:
    identity = rules.identify_path(entry.full_name)
    return ScanRow(
        path=entry.full_name,
        is_folder=entry.is_folder,
        size_bytes=entry.size_bytes,
        application=identity.application,
        category=identity.category,
        confidence=identity.confidence,
        cleanup_risk=identity.cleanup_risk,
    )


def _inspect_row(entry: Entry) -> InspectRow:
    identity = rules.identify_path(entry.full_name)
    action = risk.recommended_action(identity)
    return InspectRow(
        path=entry.full_name,
        is_folder=entry.is_folder,
        size_bytes=entry.size_bytes,
        application=identity.application,
        category=identity.category,
        confidence=identity.confidence,
        cleanup_risk=identity.cleanup_risk,
        recommended=action.action,
    )


def scan(path: str, *, top: int = 15, include_files: bool = False, admin: bool = False) -> ScanResult:
    normalized = resolve_path(path)
    backend = platform_pkg.get_scan_backend()

    try:
        capacity = platform_pkg.get_capacity_provider().free_space(normalized)
    except Exception:
        capacity = None

    entries = backend.top_entries(normalized, top=top, max_depth=1, admin=admin, include_files=include_files)
    rows = tuple(_scan_row(e) for e in entries)
    return ScanResult(
        scanned_path=normalized,
        drive=capacity,
        entries=rows,
        backend=backend.name,
        backend_advice=backend.advice(),
        warnings=tuple(backend.take_warnings()),
    )


def inspect(path: str, *, top: int = 20, folders_only: bool = False, admin: bool = False) -> InspectResult:
    normalized = resolve_path(path)
    backend = platform_pkg.get_scan_backend()
    entries = backend.top_entries(
        normalized, top=top, max_depth=1, admin=admin, include_files=not folders_only
    )
    rows = tuple(_inspect_row(e) for e in entries)
    return InspectResult(
        inspected_path=normalized,
        entries=rows,
        backend=backend.name,
        backend_advice=backend.advice(),
        warnings=tuple(backend.take_warnings()),
    )


def search(
    path: str,
    *,
    name_pattern: str | None = None,
    min_size_gb: float | None = None,
    older_than_days: int | None = None,
    folders: bool = False,
    top: int = 50,
    max_depth: int = 0,
    admin: bool = False,
) -> SearchResult:
    normalized = resolve_path(path)
    backend = platform_pkg.get_scan_backend()

    all_entries = backend.scan(
        normalized,
        export_folders=folders,
        export_files=True,
        max_depth=max_depth,
        name_filter=name_pattern,
        admin=admin,
    )

    filtered = list(all_entries)
    if min_size_gb:
        min_bytes = min_size_gb * (1024**3)
        filtered = [e for e in filtered if e.size_bytes >= min_bytes]
    if older_than_days:
        cutoff = datetime.now() - timedelta(days=older_than_days)
        filtered = [e for e in filtered if e.modified and e.modified < cutoff]

    filtered.sort(key=lambda e: e.size_bytes, reverse=True)
    top_entries = filtered[:top]

    rows = []
    for e in top_entries:
        identity = rules.identify_path(e.full_name)
        action = risk.recommended_action(identity)
        rows.append(
            SearchRow(
                path=e.full_name,
                is_folder=e.is_folder,
                size_bytes=e.size_bytes,
                modified=e.modified,
                application=identity.application,
                category=identity.category,
                cleanup_risk=identity.cleanup_risk,
                recommended=action.action,
            )
        )

    return SearchResult(
        searched_path=normalized,
        name_pattern=name_pattern,
        match_count=len(filtered),
        returned_count=len(rows),
        entries=tuple(rows),
        backend=backend.name,
        backend_advice=backend.advice(),
        warnings=tuple(backend.take_warnings()),
    )
