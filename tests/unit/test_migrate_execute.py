"""Unit tests for migrate.execute(): the destination==source guard plus the
full copy-verify-remove-relink flow around fake platform engines.

The guard tests pin down a Socket-audit finding: plan() refuses to generate a
plan whose destination equals its source, but execute() re-reads the plan JSON
from disk, so a hand-edited or otherwise tampered plan file can still pair a
directory with itself. Without the guard, an *empty* source directory slips
past execute()'s "destination must be empty" check, the copy degenerates to a
no-op whose verification trivially matches (0 files == 0 files), and
shutil.rmtree() then deletes the directory as its own destination.

The flow tests drive execute() against the real rules engine (a custom rules
dir classifying the source as migratable FakeApp data) and fake copy/link
engines, covering the status matrix of the result file: succeeded (linked and
manual), verification-failed, copy-ok-source-not-removed, plus the stale-plan
and dry-run gates.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from storops import platform as platform_pkg
from storops.core import rules
from storops.core.errors import StalePlanError, UnsupportedOperationError
from storops.core.migrate import execute as migrate_execute


class _PartialCopyEngine:
    """CopyEngine stand-in: copies like shutil.copytree but can silently drop
    one file from the destination, so the post-copy count/size verification
    has something real to catch."""

    kind = "shutil"

    def __init__(self, *, drop_one_file: bool = False):
        self.drop_one_file = drop_one_file
        self.copies: list[tuple[str, str]] = []

    def copy(self, source: str, destination: str) -> None:
        self.copies.append((source, destination))
        shutil.copytree(source, destination)
        if self.drop_one_file:
            files = sorted(path for path in Path(destination).rglob("*") if path.is_file())
            files[-1].unlink()


class _FakeLinkEngine:
    kind = "fakelink"

    def __init__(self):
        self.created: list[tuple[str, str]] = []

    def create(self, old_path: str, target: str) -> None:
        self.created.append((old_path, target))

    def verify(self, old_path: str, expected_target: str) -> bool:
        return True


_FAKEAPP_RULE = textwrap.dedent(
    """\
    - id: fake-app
      application: FakeApp
      category: ai-model-weights
      path_patterns:
        - "%HOME%/.fakeapp/models/*"
      migratable: true
      migration_method: app-config
      cleanup_risk: high
    """
)


def _migratable_source(tmp_path: Path, monkeypatch) -> Path:
    """A source directory the real rules engine classifies as migratable
    FakeApp data (high cleanup risk, which assert_not_critical allows)."""
    home = tmp_path / "home"
    source = home / ".fakeapp" / "models"
    source.mkdir(parents=True)
    (source / "weights.bin").write_bytes(b"w" * 128)

    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "ai-models.yaml").write_text(_FAKEAPP_RULE, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(rules, "_default_rules_dir", lambda: rules_dir)
    return source


def _isolate_platform(tmp_path: Path, monkeypatch, copy_engine, link_engine) -> None:
    monkeypatch.setattr(platform_pkg, "get_copy_engine", lambda: copy_engine)
    monkeypatch.setattr(platform_pkg, "get_link_engine", lambda: link_engine)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(platform_pkg, "get_work_dir", lambda: str(work_dir))


def _write_plan(tmp_path: Path, source: Path, destination: Path, **extra) -> Path:
    plan = {"Source": str(source), "Destination": str(destination)}
    plan.update(extra)
    plan_file = tmp_path / "storops-migrate-plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    return plan_file


# --- destination==source guard ----------------------------------------------


def test_execute_rejects_plan_whose_destination_equals_source(tmp_path):
    src = tmp_path / "models"
    src.mkdir()  # empty on purpose: the non-empty-destination check must not be what saves us
    plan_file = _write_plan(tmp_path, src, src)

    with pytest.raises(UnsupportedOperationError, match="destination equals the source"):
        migrate_execute(str(plan_file), confirm=True)


def test_execute_rejects_destination_equal_after_path_normalization(tmp_path):
    # Same directory spelled with a trailing separator -- resolve_path()
    # normalizes both sides before comparing.
    src = tmp_path / "models"
    src.mkdir()
    plan_file = _write_plan(tmp_path, src, Path(str(src) + os.sep))

    with pytest.raises(UnsupportedOperationError, match="destination equals the source"):
        migrate_execute(str(plan_file), confirm=True)


@pytest.mark.skipif(
    sys.platform not in ("win32", "darwin"),
    reason="only case-insensitive filesystems make the upper-cased path the same directory",
)
def test_execute_rejects_destination_equal_case_insensitively(tmp_path):
    src = tmp_path / "models"
    src.mkdir()
    plan_file = _write_plan(tmp_path, src, Path(str(src).upper()))

    with pytest.raises(UnsupportedOperationError, match="destination equals the source"):
        migrate_execute(str(plan_file), confirm=True)


# --- flow: the copy-verify-remove-relink status matrix -----------------------


def test_execute_happy_path_copies_removes_and_relinks(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    destination = tmp_path / "pool" / "models"
    copy_engine = _PartialCopyEngine()
    link_engine = _FakeLinkEngine()
    _isolate_platform(tmp_path, monkeypatch, copy_engine, link_engine)
    plan_file = _write_plan(tmp_path, source, destination)

    result = migrate_execute(str(plan_file), confirm=True)

    assert result.status == "succeeded"
    assert result.verified is True
    assert result.source_removed is True
    assert result.link_created is True
    assert copy_engine.copies == [(str(source), str(destination))]
    assert link_engine.created == [(str(source), str(destination))]
    assert (destination / "weights.bin").read_bytes() == b"w" * 128
    assert not source.exists()

    saved = json.loads((tmp_path / "work" / "storops-migrate-result.json").read_text(encoding="utf-8"))
    assert saved["Status"] == "succeeded"
    assert saved["Verified"] is True
    assert saved["SourceRemoved"] is True
    assert saved["JunctionCreated"] is True


def test_execute_manual_method_records_hint_and_skips_link(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    destination = tmp_path / "pool" / "models"
    copy_engine = _PartialCopyEngine()
    link_engine = _FakeLinkEngine()
    _isolate_platform(tmp_path, monkeypatch, copy_engine, link_engine)
    plan_file = _write_plan(
        tmp_path,
        source,
        destination,
        Method="manual",
        MigrationHint="point FakeApp at the new models directory",
    )

    result = migrate_execute(str(plan_file), confirm=True)

    assert result.status == "succeeded"
    assert result.source_removed is True
    assert result.link_created is False
    assert link_engine.created == []
    assert "point FakeApp at the new models directory" in result.detail


def test_execute_verification_failure_leaves_source_intact(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    destination = tmp_path / "pool" / "models"
    copy_engine = _PartialCopyEngine(drop_one_file=True)
    _isolate_platform(tmp_path, monkeypatch, copy_engine, _FakeLinkEngine())
    plan_file = _write_plan(tmp_path, source, destination)

    result = migrate_execute(str(plan_file), confirm=True)

    assert result.status == "verification-failed"
    assert result.verified is False
    assert result.source_removed is False
    assert "Original left untouched" in result.detail
    assert source.exists()
    assert (source / "weights.bin").exists()


def test_execute_dry_run_copies_nothing(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    copy_engine = _PartialCopyEngine()
    _isolate_platform(tmp_path, monkeypatch, copy_engine, _FakeLinkEngine())
    plan_file = _write_plan(tmp_path, source, tmp_path / "pool" / "models")

    assert migrate_execute(str(plan_file), confirm=False) is None
    assert copy_engine.copies == []
    assert source.exists()
    assert not (tmp_path / "work" / "storops-migrate-result.json").exists()


def test_execute_requires_app_closed_until_attested(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    copy_engine = _PartialCopyEngine()
    _isolate_platform(tmp_path, monkeypatch, copy_engine, _FakeLinkEngine())
    plan_file = _write_plan(tmp_path, source, tmp_path / "pool" / "models", RequiresAppClosed=True)

    with pytest.raises(UnsupportedOperationError, match="--app-closed"):
        migrate_execute(str(plan_file), confirm=True, app_closed=False)

    # Once attested, execution proceeds (dry-run here, the copy flow itself is
    # covered by the happy-path test).
    assert migrate_execute(str(plan_file), confirm=False, app_closed=True) is None


def test_execute_rejects_stale_plan_when_source_vanished(tmp_path):
    # The isdir gate fires before any rules lookup, so no engine isolation
    # is needed: a plan whose source is gone must be called stale.
    plan_file = _write_plan(
        tmp_path,
        tmp_path / "home" / ".fakeapp" / "gone",
        tmp_path / "pool" / "models",
    )

    with pytest.raises(StalePlanError, match="no longer exists"):
        migrate_execute(str(plan_file), confirm=True)


def test_execute_rejects_stale_plan_when_destination_not_empty(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    destination = tmp_path / "pool" / "models"
    destination.mkdir(parents=True)
    (destination / "leftover.bin").write_bytes(b"x")
    _isolate_platform(tmp_path, monkeypatch, _PartialCopyEngine(), _FakeLinkEngine())
    plan_file = _write_plan(tmp_path, source, destination)

    with pytest.raises(StalePlanError, match="not empty"):
        migrate_execute(str(plan_file), confirm=True)


def test_execute_reports_source_not_removed_when_rmtree_fails(tmp_path, monkeypatch):
    source = _migratable_source(tmp_path, monkeypatch)
    destination = tmp_path / "pool" / "models"
    _isolate_platform(tmp_path, monkeypatch, _PartialCopyEngine(), _FakeLinkEngine())
    plan_file = _write_plan(tmp_path, source, destination)

    def _locked(path, *args, **kwargs):
        raise OSError("directory in use")

    monkeypatch.setattr("shutil.rmtree", _locked)

    result = migrate_execute(str(plan_file), confirm=True)

    assert result.status == "copy-ok-source-not-removed"
    assert result.source_removed is False
    assert "could not be removed" in result.detail
    assert source.exists()
