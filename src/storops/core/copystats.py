"""Directory statistics used to verify a migration's copy step.

Deliberately platform-agnostic (plain os.walk/os.scandir, no scan backend
involved) -- this mirrors the original PowerShell Get-StorOpsDirStats
helper in migrate-execute.ps1/verify.ps1, which also never used
WizTree/gdu/du for this: verifying "did the copy produce the same file
count and byte total" only needs a plain recursive stat walk, on any
platform.

dir_stats() is called on both the source and destination of every
migration (migrate execute) and again on re-verification (storops
verify) -- exactly the large AI-model-cache directories this project
targets, so it is the same class of "single-threaded Windows filesystem
walk is slow" problem already addressed for platform/windows/scan.py's
_walk(): the scan root's immediate subdirectories are split across a
thread pool, each then walked sequentially by the unchanged os.walk()-
based algorithm below. See _walk() there for why only the top level is
split (a worker must never submit more work to this same bounded pool).
"""
from __future__ import annotations

import concurrent.futures
import os

from storops.core.models import DirStats

_DEFAULT_PARALLEL_WORKERS = 8


def _walk_stats(dir_path: str) -> tuple[int, int]:
    """Sequential (file_count, total_size_bytes) over dir_path's full
    subtree. A directory this process cannot read is silently skipped
    (onerror=lambda e: None) rather than raising -- dir_stats() has no
    warnings mechanism (unlike platform/windows/scan.py's ScanBackend),
    so an unreadable subtree simply under-counts, matching the original
    single-walk implementation's behavior exactly.
    """
    file_count = 0
    total_size = 0
    for root, _dirs, files in os.walk(dir_path, onerror=lambda e: None):
        for name in files:
            full = os.path.join(root, name)
            try:
                total_size += os.lstat(full).st_size
            except OSError:
                continue
            file_count += 1
    return file_count, total_size


def dir_stats(dir_path: str, *, max_workers: int = _DEFAULT_PARALLEL_WORKERS) -> DirStats:
    """(file_count, size_bytes) for everything under dir_path, recursively.

    Splits dir_path's immediate subdirectories across a thread pool (each
    walked by the sequential _walk_stats()); files directly under
    dir_path are stat-ed inline. Classification mirrors os.walk()'s own
    default behavior exactly, since this replaces what used to be a
    single os.walk(dir_path) call: a symlink is classified as a directory
    by following it (matching os.walk()'s own is_dir() call), but -- like
    os.walk()'s default followlinks=False -- a symlinked directory is
    never descended into, and since dir_stats() only ever counts *files*
    (folders are never counted, symlinked or not), it contributes nothing
    at all. A symlink to a file is counted like any other file, via
    lstat() (the link's own size, not its target's) -- same as the
    original os.lstat() call in a plain os.walk() loop.
    """
    try:
        children = list(os.scandir(dir_path))
    except OSError:
        return DirStats(file_count=0, size_bytes=0)

    file_count = 0
    total_size = 0
    futures: list[concurrent.futures.Future[tuple[int, int]]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for child in children:
            try:
                is_dir = child.is_dir(follow_symlinks=True)
                is_symlink = child.is_symlink()
            except OSError:
                continue

            if is_dir:
                if not is_symlink:
                    futures.append(pool.submit(_walk_stats, child.path))
                continue

            try:
                total_size += child.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            file_count += 1

        for future in futures:
            sub_files, sub_size = future.result()
            file_count += sub_files
            total_size += sub_size

    return DirStats(file_count=file_count, size_bytes=total_size)
