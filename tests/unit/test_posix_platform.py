"""Unit tests for src/storops/platform/posix.py -- PosixCapacityProvider,
ShutilCopyEngine, SymlinkLinkEngine. Runs on any POSIX CI runner (Linux or
macOS); skipped on Windows since these classes are never exercised there
(platform/base.py's factories only import this module off the Windows
branch).
"""
from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only module")

from storops.core.errors import StoropsError
from storops.core.models import Capacity
from storops.platform.posix import PosixCapacityProvider, ShutilCopyEngine, SymlinkLinkEngine


class TestPosixCapacityProvider:
    def test_free_space_returns_capacity(self, tmp_path):
        provider = PosixCapacityProvider()
        capacity = provider.free_space(str(tmp_path))

        assert isinstance(capacity, Capacity)
        assert capacity.total_bytes > 0
        assert capacity.free_bytes >= 0
        assert capacity.used_bytes >= 0
        # Matches the original PowerShell version, which also left
        # FileSystem=None / no volume name on POSIX.
        assert capacity.volume_name is None
        assert capacity.file_system is None
        assert capacity.drive  # non-empty best-effort mount point

    def test_mount_point_is_an_ancestor_of_the_path(self, tmp_path):
        provider = PosixCapacityProvider()
        mount_point = provider._mount_point(str(tmp_path))
        assert str(tmp_path).startswith(mount_point) or mount_point == str(tmp_path)


class TestShutilCopyEngine:
    def test_copy_succeeds(self, tmp_path):
        source = tmp_path / "src"
        (source / "nested").mkdir(parents=True)
        (source / "a.txt").write_text("hello")
        (source / "nested" / "b.txt").write_text("world")
        destination = tmp_path / "dst"

        engine = ShutilCopyEngine()
        assert engine.kind == "shutil"
        engine.copy(str(source), str(destination))

        assert (destination / "a.txt").read_text() == "hello"
        assert (destination / "nested" / "b.txt").read_text() == "world"

    def test_copy_raises_storops_error_on_failure(self, tmp_path):
        engine = ShutilCopyEngine()
        missing_source = tmp_path / "does-not-exist"
        destination = tmp_path / "dst"

        with pytest.raises(StoropsError):
            engine.copy(str(missing_source), str(destination))

    def test_copy_raises_storops_error_when_destination_exists(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.txt").write_text("hello")
        destination = tmp_path / "dst"
        destination.mkdir()

        engine = ShutilCopyEngine()
        with pytest.raises(StoropsError):
            engine.copy(str(source), str(destination))


class TestSymlinkLinkEngine:
    def test_create_and_verify_round_trip(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        old_path = tmp_path / "link"

        engine = SymlinkLinkEngine()
        assert engine.kind == "symlink"
        engine.create(str(old_path), str(target))

        assert os.path.islink(str(old_path))
        assert engine.verify(str(old_path), str(target)) is True

    def test_verify_false_for_wrong_target(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        old_path = tmp_path / "link"

        engine = SymlinkLinkEngine()
        engine.create(str(old_path), str(target))

        assert engine.verify(str(old_path), str(other)) is False

    def test_verify_false_for_nonexistent_link(self, tmp_path):
        engine = SymlinkLinkEngine()
        assert engine.verify(str(tmp_path / "nope"), str(tmp_path)) is False
