"""Directory statistics used to verify a migration's copy step.

Deliberately platform-agnostic (plain os.walk, no scan backend involved) --
this mirrors the original PowerShell Get-StorOpsDirStats helper in
migrate-execute.ps1/verify.ps1, which also never used WizTree/gdu/du for
this: verifying "did the copy produce the same file count and byte total"
only needs a plain recursive stat walk, on any platform.
"""
from __future__ import annotations

import os

from storops.core.models import DirStats


def dir_stats(dir_path: str) -> DirStats:
    file_count = 0
    total_size = 0
    for _root, _dirs, files in os.walk(dir_path, onerror=lambda e: None):
        for name in files:
            full = os.path.join(_root, name)
            try:
                total_size += os.lstat(full).st_size
            except OSError:
                continue
            file_count += 1
    return DirStats(file_count=file_count, size_bytes=total_size)
