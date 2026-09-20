"""Unit tests for src/storops/platform/backends/du.py.

Mixes two styles per the task brief: real subprocess integration tests
against the actual `du` binary (guaranteed present on any Linux/macOS CI
runner, and definitely present in this sandbox) for end-to-end confidence,
plus subprocess.run-mocked tests for exercising the GNU/BSD parsing branches
deterministically regardless of which `du` flavor happens to be installed
on the machine running the tests.
"""
from __future__ import annotations

import io
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
        if backend._du_flavor() == "gnu":
            assert entry.size_bytes == 500
        else:
            # BSD du has no apparent-size flag: -k reports disk usage
            # rounded up to the filesystem's allocation block size (e.g.
            # 4096 on APFS), not the literal byte count -- a documented
            # approximation on this flavor (see du.py's BSD branch).
            assert entry.size_bytes >= 500
            assert entry.size_bytes % 1024 == 0

    def test_path_size_on_a_directory_sums_its_subtree(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = DuBackend()

        entry = backend.path_size(str(root / "sub"))
        assert entry is not None
        assert entry.is_folder is True
        if backend._du_flavor() == "gnu":
            assert entry.size_bytes == 1000
        else:
            assert entry.size_bytes >= 1000

    def test_path_size_does_not_touch_sibling_directories(self, tmp_path):
        # Regression test: path_size() used to shell out to `du` against
        # `target`'s *parent* and search for `target` in that output --
        # meaning `du` itself walked every unrelated sibling on disk too
        # whenever the parent happened to be large. It must now only ever
        # target `path` itself.
        root = tmp_path / "root"
        target = root / "small"
        target.mkdir(parents=True)
        (target / "f.bin").write_bytes(b"x" * 5)
        sibling = root / "huge_sibling"
        sibling.mkdir()
        (sibling / "big.bin").write_bytes(b"y" * 1_000_000)

        backend = DuBackend()
        entry = backend.path_size(str(target))
        assert entry is not None
        if backend._du_flavor() == "gnu":
            assert entry.size_bytes == 5

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


class _FakePopen:
    """Minimal stand-in for subprocess.Popen as scan() uses it: an
    iterable text-mode stdout plus wait()/close()."""

    def __init__(self, stdout: str):
        self.stdout = io.StringIO(stdout)
        self.returncode = 0

    def wait(self):
        return self.returncode


def _fake_popen_returning(stdout, *, recorder=None):
    def factory(args, **kwargs):
        if recorder is not None:
            recorder.append(args)
        return _FakePopen(stdout)

    return factory


def test_scan_parses_bsd_style_kilobyte_output(monkeypatch, tmp_path):
    """Force the BSD parsing branch (size in 1024-byte blocks, scaled up)
    regardless of the real `du` installed on the test runner, using real
    files on disk so os.path.isdir() checks succeed naturally."""
    (tmp_path / "sub").mkdir()

    backend = DuBackend()
    backend._flavor = "bsd"  # skip flavor probing

    args_seen: list[list[str]] = []
    # BSD `du -a -k` output: <blocks>\t<path>
    stdout = f"4\t{tmp_path / 'sub'}\n8\t{tmp_path}\n"
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_returning(stdout, recorder=args_seen))

    entries = backend.scan(str(tmp_path), export_folders=True, export_files=True, max_depth=1)
    assert "-k" in args_seen[0]
    assert len(entries) == 1
    assert entries[0].full_name == str(tmp_path / "sub")
    assert entries[0].size_bytes == 4 * 1024  # scaled from 1024-byte blocks


def test_scan_raises_permission_denied_when_du_produces_no_output(monkeypatch, tmp_path):
    backend = DuBackend()
    backend._flavor = "gnu"

    monkeypatch.setattr(subprocess, "Popen", _fake_popen_returning(""))

    with pytest.raises(PermissionDeniedError):
        backend.scan(str(tmp_path))


# --- macOS/BSD flag selection (the expensive `-a` is opt-in) ----------------


def _args_for(monkeypatch, backend, tmp_path, **scan_kwargs):
    args_seen: list[list[str]] = []
    stdout = f"8\t{tmp_path}\n"
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_returning(stdout, recorder=args_seen))
    backend.scan(str(tmp_path), **scan_kwargs)
    return args_seen[0]


def test_bsd_directory_only_scan_skips_dash_a_and_limits_depth_natively(monkeypatch, tmp_path):
    """The scan/inspect hot path (top_entries(include_files=False)) must
    not ask BSD du for file-level rows: `-a` there is unbounded-depth by
    necessity (BSD's -a and -d are mutually exclusive), which on a real
    macOS home directory meant millions of rows and ~2.6GB peak RSS to
    produce ~15 rows of output.
    """
    backend = DuBackend()
    backend._flavor = "bsd"

    args = _args_for(
        monkeypatch, backend, tmp_path, export_folders=True, export_files=False, max_depth=1
    )

    assert "-a" not in args
    assert args[args.index("-d") + 1] == "1"


def test_bsd_file_level_scan_still_uses_dash_a_without_depth_flag(monkeypatch, tmp_path):
    backend = DuBackend()
    backend._flavor = "bsd"

    args = _args_for(
        monkeypatch, backend, tmp_path, export_folders=True, export_files=True, max_depth=2
    )

    assert "-a" in args
    assert "-d" not in args  # mutually exclusive with -a on BSD; filtered in Python


def test_gnu_directory_only_scan_skips_dash_a(monkeypatch, tmp_path):
    backend = DuBackend()
    backend._flavor = "gnu"

    args = _args_for(
        monkeypatch, backend, tmp_path, export_folders=True, export_files=False, max_depth=1
    )

    assert "-a" not in args
    assert "--max-depth=1" in args


def test_non_empty_directories_are_classified_without_stat(monkeypatch, tmp_path):
    """In `-a` mode du emits a directory after its contents, so a
    directory is already known to be one by the time its own row arrives
    -- no os.path.isdir() call needed. Proven by pointing the backend at
    paths that do not exist on disk: isdir() would report False for all
    of them.
    """
    backend = DuBackend()
    backend._flavor = "gnu"
    ghost = tmp_path / "ghost"
    stdout = f"10\t{ghost / 'inner' / 'f.bin'}\n20\t{ghost / 'inner'}\n30\t{ghost}\n40\t{tmp_path}\n"
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_returning(stdout))

    entries = backend.scan(str(tmp_path), export_folders=True, export_files=True)

    by_path = {e.full_name: e.is_folder for e in entries}
    assert by_path[str(ghost)] is True
    assert by_path[str(ghost / "inner")] is True
    assert by_path[str(ghost / "inner" / "f.bin")] is False
