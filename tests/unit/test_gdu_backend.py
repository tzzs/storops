"""Unit tests for src/storops/platform/backends/gdu.py.

Two layers, mirroring test_du_backend.py's structure:

1. Parsing-logic tests against a REAL captured `gdu -n -o out.json <dir>`
   export (from a real `gdu 5.25.0` install, `apt install gdu` on Debian,
   2026-09-01) -- these run unconditionally, on any machine, regardless of
   whether `gdu` itself is installed on the test runner, because they lock
   in the exact verified JSON shape as a fixture rather than depending on
   the binary being present. This is what actually caught the original
   parsing bug (an earlier draft read `raw[2]`, a small metadata object,
   instead of `raw[3]`, the real tree -- and assumed nested objects with a
   "files" key instead of gdu's real nested-arrays shape) -- see gdu.py's
   module docstring for the full story.
2. Real subprocess integration tests against the actual `gdu` binary when
   present, skipped otherwise (gdu is an optional accelerator, not
   guaranteed on every CI runner the way `du` is).
"""
from __future__ import annotations

import json
import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only backend")

from storops.core.models import Entry
from storops.platform.backends.gdu import GduBackend, _process_dir, is_available

# A real `gdu -n -o out.json <dir>` export, captured verbatim against a
# tree shaped like:
#   root/
#     a/f1.bin       (100 KiB apparent, 100 KiB allocated)
#     b/f2.bin       (200 KiB apparent, 200 KiB allocated)
#     b/nested/f3.bin (50 KiB apparent, 52 KiB allocated -- deliberately
#                      different, to prove asize vs dsize aren't conflated)
_REAL_GDU_EXPORT = [
    1,
    2,
    {"progname": "gdu", "progver": "5.25.0-1+b8", "timestamp": 1788212681},
    [
        {"name": "/tmp/gdu-fixture-root", "mtime": 1788212681},
        [
            {"name": "b", "mtime": 1788212681},
            {"name": "f2.bin", "asize": 204800, "dsize": 204800, "mtime": 1788212681},
            [
                {"name": "nested", "mtime": 1788212681},
                {"name": "f3.bin", "asize": 51200, "dsize": 53248, "mtime": 1788212681},
            ],
        ],
        [
            {"name": "a", "mtime": 1788212681},
            {"name": "f1.bin", "asize": 102400, "dsize": 102400, "mtime": 1788212681},
        ],
    ],
]


# --- Parsing-logic tests against the captured real export -------------------


class TestProcessDirAgainstRealGduExport:
    def test_depth_1_aggregates_directory_sizes_correctly(self):
        tree = _REAL_GDU_EXPORT[3]
        collected: list[Entry] = []
        total_asize, total_dsize, files, folders = _process_dir(
            tree, "/tmp/gdu-fixture-root", 0, max_depth=1, export_folders=True, export_files=False, collected=collected
        )

        by_name = {e.full_name: e for e in collected}
        assert set(by_name) == {"/tmp/gdu-fixture-root/b", "/tmp/gdu-fixture-root/a"}

        # "b" has no asize/dsize of its own in gdu's export (directory
        # nodes never do) -- must be the sum of f2.bin (204800) + the
        # nested f3.bin (51200) = 256000, not 0 and not just the direct child.
        b = by_name["/tmp/gdu-fixture-root/b"]
        assert b.size_bytes == 256000
        assert b.allocated_bytes == 204800 + 53248
        assert b.file_count == 1  # f2.bin only -- direct children, not recursive
        assert b.folder_count == 1  # "nested"

        a = by_name["/tmp/gdu-fixture-root/a"]
        assert a.size_bytes == 102400
        assert a.file_count == 1
        assert a.folder_count == 0

        # Root-level totals must also add up across the whole subtree.
        assert total_asize == 256000 + 102400
        assert total_dsize == (204800 + 53248) + 102400

    def test_export_files_true_also_surfaces_files_at_the_right_depth(self):
        tree = _REAL_GDU_EXPORT[3]
        collected: list[Entry] = []
        _process_dir(
            tree, "/tmp/gdu-fixture-root", 0, max_depth=1, export_folders=True, export_files=True, collected=collected
        )
        # At max_depth=1, "b/f2.bin" is depth 2 and must NOT be emitted,
        # even though export_files=True -- only b itself (depth 1) should be.
        names = {e.full_name for e in collected}
        assert "/tmp/gdu-fixture-root/b/f2.bin" not in names
        assert "/tmp/gdu-fixture-root/b" in names

    def test_max_depth_zero_is_unlimited_and_reaches_the_deepest_file(self):
        tree = _REAL_GDU_EXPORT[3]
        collected: list[Entry] = []
        _process_dir(
            tree, "/tmp/gdu-fixture-root", 0, max_depth=0, export_folders=True, export_files=True, collected=collected
        )
        names = {e.full_name for e in collected}
        assert "/tmp/gdu-fixture-root/b/nested/f3.bin" in names
        deep = next(e for e in collected if e.full_name == "/tmp/gdu-fixture-root/b/nested/f3.bin")
        assert deep.is_folder is False
        assert deep.size_bytes == 51200
        assert deep.allocated_bytes == 53248  # proves asize != dsize is preserved, not conflated

    def test_export_folders_false_omits_directories(self):
        tree = _REAL_GDU_EXPORT[3]
        collected: list[Entry] = []
        _process_dir(
            tree, "/tmp/gdu-fixture-root", 0, max_depth=0, export_folders=False, export_files=True, collected=collected
        )
        assert all(not e.is_folder for e in collected)


def test_full_scan_pipeline_parses_the_real_export(monkeypatch, tmp_path):
    """End-to-end through GduBackend.scan()'s own JSON-loading/tempfile
    plumbing, with subprocess.run mocked to just write the captured real
    export -- this is the highest-value regression test here: it would
    have caught the original raw[2]-vs-raw[3] bug even without a real gdu
    binary available."""
    import storops.platform.backends.gdu as gdu_module

    root = tmp_path / "gdu-fixture-root"
    root.mkdir()

    def fake_run(args, **kwargs):
        out_file = args[args.index("-o") + 1]
        export = json.loads(json.dumps(_REAL_GDU_EXPORT))
        export[3][0]["name"] = str(root)
        with open(out_file, "w", encoding="utf-8") as fh:
            json.dump(export, fh)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(gdu_module.subprocess, "run", fake_run)
    monkeypatch.setattr(gdu_module.shutil, "which", lambda name: "/usr/bin/gdu")

    backend = GduBackend()
    entries = backend.top_entries(str(root), top=10, max_depth=1)
    sizes = {e.full_name.split("/")[-1]: e.size_bytes for e in entries}
    assert sizes == {"b": 256000, "a": 102400}


# --- is_available() ----------------------------------------------------------


def test_is_available_respects_env_var(monkeypatch, tmp_path):
    fake_gdu = tmp_path / "gdu"
    fake_gdu.write_text("#!/bin/sh\n")
    fake_gdu.chmod(0o755)
    monkeypatch.setenv("STOROPS_GDU_PATH", str(fake_gdu))
    assert is_available() is True


def test_is_available_false_when_env_var_points_nowhere(monkeypatch):
    monkeypatch.setenv("STOROPS_GDU_PATH", "/definitely/not/a/real/path/gdu")
    assert is_available() is False


# --- Real gdu binary integration (skipped if gdu isn't installed) -----------


@pytest.mark.skipif(shutil.which("gdu") is None, reason="gdu binary not installed on this runner")
class TestGduBackendRealBinaryIntegration:
    def _make_tree(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "f1.bin").write_bytes(b"x" * 1000)
        (tmp_path / "b" / "nested").mkdir(parents=True)
        (tmp_path / "b" / "f2.bin").write_bytes(b"y" * 2000)
        (tmp_path / "b" / "nested" / "f3.bin").write_bytes(b"z" * 500)
        return tmp_path

    def test_scan_aggregates_nested_directory_sizes(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = GduBackend()

        entries = backend.top_entries(str(root), top=10, max_depth=1)
        by_name = {e.full_name: e for e in entries}

        assert by_name[str(root / "a")].size_bytes == 1000
        # "b" must include its nested subdirectory's file too.
        assert by_name[str(root / "b")].size_bytes == 2000 + 500

    def test_path_size_on_a_nested_directory(self, tmp_path):
        root = self._make_tree(tmp_path)
        backend = GduBackend()

        entry = backend.path_size(str(root / "b" / "nested"))
        assert entry is not None
        assert entry.size_bytes == 500

    def test_name_filter(self, tmp_path):
        root = self._make_tree(tmp_path)
        (root / "a" / "note.txt").write_bytes(b"hi")
        backend = GduBackend()

        entries = backend.scan(
            str(root), export_folders=False, export_files=True, max_depth=0, name_filter="*.bin"
        )
        assert all(e.full_name.endswith(".bin") for e in entries)
        assert len(entries) == 3

    def test_name_and_advice(self):
        backend = GduBackend()
        assert backend.name == "Gdu"
        assert backend.advice() is None  # gdu is the recommended backend -- no advice needed
