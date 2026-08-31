"""Validates core/scan.py's orchestration against a fake ScanBackend, so
this contract is locked down before any real platform backend exists.
Real backends (gdu/du/WizTree/native) are covered separately in
tests/platform/ and by whichever suite the platform-layer implementation
ships with.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from storops import platform as platform_pkg
from storops.core import scan
from storops.core.models import Capacity, Entry, ScanWarning


class FakeBackend:
    name = "Fake"

    def __init__(self, entries: list[Entry], advice: str | None = None, warnings: list[ScanWarning] | None = None):
        self._entries = entries
        self._advice = advice
        self._warnings = warnings or []

    def scan(self, path, *, export_folders=True, export_files=False, max_depth=0, name_filter=None, name_exclude=None, admin=False):
        return list(self._entries)

    def top_entries(self, path, *, top=20, max_depth=1, admin=False, include_files=False):
        return sorted(self._entries, key=lambda e: e.size_bytes, reverse=True)[:top]

    def path_size(self, path, *, admin=False):
        return self._entries[0] if self._entries else None

    def advice(self):
        return self._advice

    def take_warnings(self):
        w, self._warnings = self._warnings, []
        return w


class FakeCapacityProvider:
    def free_space(self, path):
        return Capacity(drive="/dev/fake", total_bytes=1000, free_bytes=400, used_bytes=600)


def _entries():
    return [
        Entry(full_name="/x/a", is_folder=True, size_bytes=300, allocated_bytes=300),
        Entry(full_name="/x/b", is_folder=True, size_bytes=100, allocated_bytes=100, modified=datetime.now() - timedelta(days=400)),
        Entry(full_name="/x/c.txt", is_folder=False, size_bytes=50, allocated_bytes=50, modified=datetime.now()),
    ]


def test_scan_orders_by_size_and_reports_backend(monkeypatch):
    monkeypatch.setattr(platform_pkg, "get_scan_backend", lambda: FakeBackend(_entries(), advice="install gdu"))
    monkeypatch.setattr(platform_pkg, "get_capacity_provider", lambda: FakeCapacityProvider())

    result = scan.scan("/x", top=2)
    assert result.scanned_path == "/x"
    assert [e.size_bytes for e in result.entries] == [300, 100]
    assert result.backend == "Fake"
    assert result.backend_advice == "install gdu"
    assert result.drive is not None and result.drive.total_bytes == 1000


def test_inspect_attaches_recommended_action(monkeypatch):
    monkeypatch.setattr(platform_pkg, "get_scan_backend", lambda: FakeBackend(_entries()))
    result = scan.inspect("/x")
    assert all(row.recommended in {"KEEP", "DELETE", "MOVE", "CHECK"} for row in result.entries)


def test_search_filters_by_min_size_and_age(monkeypatch):
    monkeypatch.setattr(platform_pkg, "get_scan_backend", lambda: FakeBackend(_entries()))
    result = scan.search("/x", min_size_gb=0, older_than_days=100)
    # Only entry "b" is both old enough and has a Modified timestamp set.
    assert result.match_count == 1
    assert result.entries[0].path == "/x/b"


def test_scan_carries_backend_warnings_through(monkeypatch):
    warnings = [ScanWarning(path="/x/locked", code="permission_denied", message="denied")]
    monkeypatch.setattr(platform_pkg, "get_scan_backend", lambda: FakeBackend(_entries(), warnings=warnings))
    monkeypatch.setattr(platform_pkg, "get_capacity_provider", lambda: FakeCapacityProvider())
    result = scan.scan("/x")
    assert len(result.warnings) == 1
    assert result.warnings[0].code == "permission_denied"
