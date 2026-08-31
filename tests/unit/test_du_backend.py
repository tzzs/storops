"""Unit tests for src/storops/platform/backends/du.py.

Mixes two styles per the task brief: real subprocess integration tests
against the actual `du` binary (guaranteed present on any Linux/macOS CI
runner, and definitely present in this sandbox) for end-to-end confidence,
plus subprocess.run-mocked tests for exercising the GNU/BSD parsing branches
deterministically regardless of which `du` flavor happens to be installed
on the machine running the tests.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only backend")

from storops.core.errors import InvalidPathError, PermissionDeniedError
from storops.core.models import Entry
from storops.platform.backends.du import DuBackend, _DU_FALLBACK_ADVICE


# --- Real `du` integration tests -------------------------------------------


class TestDuBackendIntegration:
    def _make_tree(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.txt").write_bytes(b"x" * 1000)
        (tmp_path / "b.txt").write_bytes(b"y" * 500)
        return tmp_path

    def test_scan_finds_top_level_entries(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        entries = backend.scan(str(root), export_folders=True, export_files=True, max_depth=1)

        names = {e.full_name for e in entries}
        assert str(root / "sub") in names
        assert str(root / "b.txt") in names
        for entry in entries:
            assert isinstance(entry, Entry)
            assert entry.size_bytes >= 0

    def test_scan_excludes_files_when_export_files_false(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        entries = backend.scan(str(root), export_folders=True, export_files=False, max_depth=1)

        assert all(e.is_folder for e in entries)

    def test_top_entries_sorted_descending(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        top = backend.top_entries(str(root), top=5, max_depth=1, include_files=True)

        sizes = [e.size_bytes for e in top]
        assert sizes == sorted(sizes, reverse=True)

    def test_top_entries_respects_top_limit(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        top = backend.top_entries(str(root), top=1, max_depth=1, include_files=True)
        assert len(top) == 1

    def test_path_size_finds_the_file(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        entry = backend.path_size(str(root / "b.txt"))
        assert entry is not None
        assert entry.full_name == str(root / "b.txt")
        assert entry.size_bytes == 500

    def test_path_size_returns_none_for_missing_path(self, tmp_path):
        backend = DuBackend()
        assert backend.path_size(str(tmp_path / "does-not-exist")) is None

    def test_scan_raises_invalid_path_error_for_missing_path(self, tmp_path):
        backend = DuBackend()
        with pytest.raises(InvalidPathError):
            backend.scan(str(tmp_path / "does-not-exist"))

    def test_name_filter_and_exclude(self, tmp_path):
        root = self._make_tree(tmp_path)
        (root / "c.log").write_bytes(b"z" * 10)
        backend = DuBackend()

        only_txt = backend.scan(
            str(root), export_folders=False, export_files=True, max_depth=1, name_filter="*.txt"
        )
        assert {e.full_name for e in only_txt} == {str(root / "b.txt")}

        excl_txt = backend.scan(
            str(root),
            export_folders=False,
            export_files=True,
            max_depth=1,
            name_exclude="*.txt",
        )
        assert {e.full_name for e in excl_txt} == {str(root / "c.log")}


# --- Backend metadata --------------------------------------------------------


def test_name_and_advice():
    backend = DuBackend()
    assert backend.name == "Du"
    assert backend.advice() == _DU_FALLBACK_ADVICE
    assert "gdu" in backend.advice()


def test_take_warnings_is_empty_list():
    # Matches the PowerShell version's `2>$null` behavior: permission-denied
    # subtrees during `du` are silently skipped rather than surfaced as
    # structured warnings -- an acceptable, documented v1 limitation.
    backend = DuBackend()
    assert backend.take_warnings() == []


# --- GNU/BSD flavor + parsing, with subprocess.run mocked -------------------


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_du_flavor_detects_gnu(monkeypatch):
    backend = DuBackend()

    def fake_run(args, **kwargs):
        assert args == ["du", "--version"]
        return _FakeCompleted(returncode=0, stdout="du (GNU coreutils) 9.1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert backend._du_flavor() == "gnu"
    # cached after first call
    assert backend._flavor == "gnu"


def test_du_flavor_detects_bsd(monkeypatch):
    backend = DuBackend()

    def fake_run(args, **kwargs):
        return _FakeCompleted(returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert backend._du_flavor() == "bsd"


def test_du_flavor_falls_back_to_bsd_when_du_missing(monkeypatch):
    backend = DuBackend()

    def fake_run(args, **kwargs):
        raise FileNotFoundError("du not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert backend._du_flavor() == "bsd"


def test_scan_parses_bsd_style_kilobyte_output(monkeypatch, tmp_path):
    """Force the BSD parsing branch (size in 1024-byte blocks, scaled up)
    regardless of the real `du` installed on the test runner, using real
    files on disk so os.path.isdir() checks succeed naturally."""
    (tmp_path / "sub").mkdir()

    backend = DuBackend()
    backend._flavor = "bsd"  # skip flavor probing

    def fake_run(args, **kwargs):
        assert "-k" in args
        # BSD `du -a -k` output: <blocks>\t<path>
        stdout = f"4\t{tmp_path / 'sub'}\n8\t{tmp_path}\n"
        return _FakeCompleted(returncode=0, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    entries = backend.scan(str(tmp_path), export_folders=True, export_files=True, max_depth=1)
    assert len(entries) == 1
    assert entries[0].full_name == str(tmp_path / "sub")
    assert entries[0].size_bytes == 4 * 1024  # scaled from 1024-byte blocks


def test_scan_raises_permission_denied_when_du_fails_with_no_output(monkeypatch, tmp_path):
    backend = DuBackend()
    backend._flavor = "gnu"

    def fake_run(args, **kwargs):
        return _FakeCompleted(returncode=1, stdout="", stderr="du: Permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(PermissionDeniedError):
        backend.scan(str(tmp_path))
