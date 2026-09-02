"""Unit tests for storops.core.copystats.dir_stats().

No test file existed for this module before -- these lock in current
behavior (a plain, silently-degrading recursive walk mirroring the
original PowerShell Get-StorOpsDirStats helper) before parallelizing the
implementation to address the same "single-threaded Windows filesystem
walk is slow" class of issue already fixed for platform/windows/scan.py's
_walk(), since dir_stats() is what migrate execute/verify use to compare
pre/post-copy directory contents and can hit the exact same large
AI-model-cache directories this project targets.
"""
from __future__ import annotations

import os

from storops.core.copystats import _walk_stats, dir_stats


def _make_tree(tmp_path):
    (tmp_path / "a" / "nested").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    (tmp_path / "top.txt").write_bytes(b"x" * 5)
    (tmp_path / "a" / "f1.bin").write_bytes(b"1" * 10)
    (tmp_path / "a" / "nested" / "f2.bin").write_bytes(b"2" * 20)
    (tmp_path / "b" / "f3.bin").write_bytes(b"3" * 30)
    return tmp_path


def test_counts_files_and_sums_sizes_recursively(tmp_path):
    root = _make_tree(tmp_path)
    stats = dir_stats(str(root))
    assert stats.file_count == 4
    assert stats.size_bytes == 5 + 10 + 20 + 30


def test_empty_directory_returns_zero(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    stats = dir_stats(str(root))
    assert stats.file_count == 0
    assert stats.size_bytes == 0


def test_nonexistent_directory_returns_zero(tmp_path):
    stats = dir_stats(str(tmp_path / "does-not-exist"))
    assert stats.file_count == 0
    assert stats.size_bytes == 0


def test_many_root_subdirectories_all_counted(tmp_path):
    for i in range(12):
        d = tmp_path / f"dir{i}"
        d.mkdir()
        (d / "f.bin").write_bytes(b"x" * (i + 1))
    stats = dir_stats(str(tmp_path))
    assert stats.file_count == 12
    assert stats.size_bytes == sum(range(1, 13))


def test_permission_denied_root_subdirectory_is_silently_skipped(tmp_path, monkeypatch):
    # dir_stats() has no warnings mechanism (unlike platform/windows/scan.py's
    # _walk()) -- a subtree it can't read is meant to silently under-count,
    # matching os.walk(onerror=lambda e: None)'s existing swallow-and-move-on
    # behavior. Must not raise either way.
    root = tmp_path / "root"
    locked = root / "locked"
    locked.mkdir(parents=True)
    (root / "ok.txt").write_bytes(b"x" * 7)

    import storops.core.copystats as copystats_mod

    real_scandir = copystats_mod.os.scandir

    def fake_scandir(path):
        if str(path) == str(locked):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(copystats_mod.os, "scandir", fake_scandir)

    stats = dir_stats(str(root))
    assert stats.file_count == 1
    assert stats.size_bytes == 7


def test_symlinked_directory_is_not_recursed_into(tmp_path):
    # Matches os.walk()'s own default (followlinks=False): a symlink to a
    # directory is not descended into, and (since dir_stats() only ever
    # counts *files*) contributes nothing at all -- not even as a "file".
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "inside.bin").write_bytes(b"x" * 100)

    root = tmp_path / "root"
    root.mkdir()
    (root / "top.txt").write_bytes(b"y" * 3)
    os.symlink(real_dir, root / "link_to_real", target_is_directory=True)

    stats = dir_stats(str(root))
    assert stats.file_count == 1
    assert stats.size_bytes == 3


def test_symlinked_file_counted_via_lstat_not_target_size(tmp_path):
    # Deliberate existing choice: os.lstat() on a symlinked file reports
    # the link's own size, not the target's -- preserved as-is.
    target = tmp_path / "target.bin"
    target.write_bytes(b"x" * 999)

    root = tmp_path / "root"
    root.mkdir()
    link_path = root / "link.bin"
    os.symlink(target, link_path)

    stats = dir_stats(str(root))
    assert stats.file_count == 1
    assert stats.size_bytes == os.lstat(str(link_path)).st_size
    assert stats.size_bytes != 999


def test_more_root_subdirectories_than_thread_pool_workers(tmp_path):
    # dir_stats() splits the root's immediate subdirectories across a
    # bounded (8-worker) thread pool -- more subdirectories than that must
    # still all be counted (the pool queues the rest), not dropped.
    for i in range(12):
        d = tmp_path / f"dir{i:02d}"
        d.mkdir()
        (d / "f.bin").write_bytes(bytes([i]) * (i + 1))

    stats = dir_stats(str(tmp_path))
    assert stats.file_count == 12
    assert stats.size_bytes == sum(range(1, 13))


def test_parallel_split_matches_plain_sequential_walk(tmp_path):
    # dir_stats() (parallel at the root) must produce the exact same
    # aggregate as _walk_stats() (the plain sequential os.walk()-based
    # algorithm it's built on) called once on the whole tree, for a tree
    # wide/deep enough to actually engage the thread pool.
    for i in range(10):
        d = tmp_path / f"d{i}" / "nested"
        d.mkdir(parents=True)
        (tmp_path / f"d{i}" / f"f{i}.bin").write_bytes(b"x" * (i + 1))
        (d / "deep.bin").write_bytes(b"y" * (i + 2))
    (tmp_path / "top.txt").write_bytes(b"z" * 3)

    parallel = dir_stats(str(tmp_path))
    sequential_files, sequential_size = _walk_stats(str(tmp_path))

    assert parallel.file_count == sequential_files
    assert parallel.size_bytes == sequential_size
