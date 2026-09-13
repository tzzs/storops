"""Regression tests for migrate.execute()'s destination==source guard.

plan() refuses to generate a plan whose destination equals its source, but
execute() re-reads the plan JSON from disk, so a hand-edited or otherwise
tampered plan file can still pair a directory with itself. Without the guard,
an *empty* source directory slips past execute()'s "destination must be
empty" check, the copy degenerates to a no-op whose verification trivially
matches (0 files == 0 files), and shutil.rmtree() then deletes the directory
as its own destination.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from storops.core.errors import UnsupportedOperationError
from storops.core.migrate import execute as migrate_execute


def _write_plan(tmp_path: Path, source: Path, destination: Path) -> Path:
    plan_file = tmp_path / "storops-migrate-plan.json"
    plan_file.write_text(
        json.dumps({"Source": str(source), "Destination": str(destination)}),
        encoding="utf-8",
    )
    return plan_file


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
