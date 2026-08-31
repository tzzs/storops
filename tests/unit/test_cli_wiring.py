"""In-process CLI tests against a fake platform layer -- validates cli.py's
wiring (argument parsing -> core orchestration -> output rendering) without
needing the real Linux/macOS/Windows platform implementations to exist yet.
"""
from __future__ import annotations

import json

import pytest

from storops import cli, platform as platform_pkg
from storops.core.models import Capacity, Entry


class FakeScanBackend:
    name = "Fake"

    def __init__(self, entries):
        self._entries = entries

    def scan(self, path, **kw):
        return list(self._entries)

    def top_entries(self, path, *, top=20, max_depth=1, admin=False, include_files=False):
        return sorted(self._entries, key=lambda e: e.size_bytes, reverse=True)[:top]

    def path_size(self, path, *, admin=False):
        return self._entries[0] if self._entries else None

    def advice(self):
        return None

    def take_warnings(self):
        return []


class FakeCapacityProvider:
    def free_space(self, path):
        return Capacity(drive="/dev/fake", total_bytes=1000, free_bytes=400, used_bytes=600)


def _patch_backend(monkeypatch, entries):
    monkeypatch.setattr(platform_pkg, "get_scan_backend", lambda: FakeScanBackend(entries))
    monkeypatch.setattr(platform_pkg, "get_capacity_provider", lambda: FakeCapacityProvider())
    # cli.py imports these names into storops.core.scan's module namespace at
    # import time via `from storops import platform as platform_pkg`, so
    # patching the storops.platform module object (as above) is sufficient --
    # storops.core.scan does the same `import ... as platform_pkg`, so both
    # see the same patched module.


def test_scan_json_stdout_is_pure_json(monkeypatch, capsys, tmp_path):
    entries = [Entry(full_name=str(tmp_path / "a"), is_folder=True, size_bytes=500, allocated_bytes=500)]
    _patch_backend(monkeypatch, entries)

    exit_code = cli.main(["scan", str(tmp_path), "--json"])
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["Entries"][0]["SizeBytes"] == 500
    assert data["Backend"] == "Fake"


def test_scan_human_mode(monkeypatch, capsys, tmp_path):
    entries = [Entry(full_name=str(tmp_path / "a"), is_folder=True, size_bytes=500, allocated_bytes=500)]
    _patch_backend(monkeypatch, entries)

    exit_code = cli.main(["scan", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "consumers under" in out


def test_cleanup_execute_dry_run_json_only(monkeypatch, capsys, tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps({"Items": [{"Path": "/x", "SizeBytes": 10, "Approved": True, "Application": "X"}]}),
        encoding="utf-8",
    )
    exit_code = cli.main(["cleanup", "execute", "--plan-file", str(plan_file), "--json"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["DryRun"] is True
    assert len(data["ApprovedItems"]) == 1


def test_invalid_max_risk_is_argparse_error(capsys):
    # argparse itself calls sys.exit() on a parse error (before our code
    # ever gets control), so this surfaces as SystemExit(2), not a normal
    # return value -- matches ARGPARSE_EXIT_CODE in core/errors.py.
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["cleanup", "plan", "--max-risk", "extreme"])
    assert exc_info.value.code == 2
