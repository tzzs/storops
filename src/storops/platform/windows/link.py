"""Windows LinkEngine: NTFS Junction via `mklink /J`.

Per docs/plans/storops-v2-cross-platform-refactor.md §2.11a's confirmed
decision: shell out to Windows' own built-in `mklink /J` command -- the
same tier of dependency as robocopy.exe, zero third-party install, far
less error-prone than a hand-rolled ctypes DeviceIoControl(FSCTL_SET_
REPARSE_POINT) REPARSE_DATA_BUFFER struct packing. Ports the behavior of
the Junction creation/verification block near the bottom of the old
scripts/migrate-execute.ps1.
"""
from __future__ import annotations

import os
import subprocess

from storops.core.errors import StoropsError


class JunctionLinkEngine:
    """LinkEngine backed by `cmd /c mklink /J`."""

    kind = "junction"

    def create(self, old_path: str, target: str) -> None:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", old_path, target],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            message = (
                f"mklink /J failed creating a junction at '{old_path}' -> "
                f"'{target}' (exit code {result.returncode})."
            )
            if detail:
                message = f"{message} {detail}"
            raise StoropsError(message)

    def verify(self, old_path: str, expected_target: str) -> bool:
        """Best-effort verification only -- see caveat below.

        `os.path.islink()` does NOT reliably detect NTFS junctions the way
        it detects symlinks: a junction is a distinct reparse-point tag
        (IO_REPARSE_TAG_MOUNT_POINT vs. IO_REPARSE_TAG_SYMLINK), and
        Python's stdlib `os.path.islink()`/`os.readlink()` are not
        guaranteed to recognize or resolve it the same way. An exact check
        would need to read the reparse point's tag and target via ctypes
        (`DeviceIoControl` + `FSCTL_GET_REPARSE_POINT`, parsing a
        `REPARSE_DATA_BUFFER`) -- deliberately NOT implemented here, per
        §2.11a's decision to keep the Windows adapter stdlib/shell-only in
        the MVP; a ctypes-based exact check is a documented follow-up, not
        a blocker.

        Pragmatic stdlib-only heuristic instead: `old_path` exists as a
        directory, `expected_target` exists as a directory, and their
        top-level directory listings match. This is NOT a byte-exact
        reparse-point check (it could be fooled by two unrelated
        directories that happen to share the same top-level child names),
        but it is a reasonable signal that the junction resolves somewhere
        sane without adding a ctypes dependency.
        """
        if not os.path.isdir(old_path) or not os.path.isdir(expected_target):
            return False
        try:
            old_listing = set(os.listdir(old_path))
            target_listing = set(os.listdir(expected_target))
        except OSError:
            return False
        return old_listing == target_listing
