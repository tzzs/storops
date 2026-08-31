"""Windows CopyEngine: wraps robocopy.exe.

Ports the behavior of the robocopy invocation in the old
scripts/migrate-execute.ps1 (same flag set, same exit-code convention).
See docs/plans/storops-v2-cross-platform-refactor.md §2.14.
"""
from __future__ import annotations

import shutil
import subprocess

from storops.core.errors import BackendNotFoundError, StoropsError

# /E             copy subdirectories, including empty ones
# /COPY:DAT      copy Data, Attributes, Timestamps (not ACLs/owner/audit --
#                matches migrate-execute.ps1, no elevation required)
# /R:2 /W:2      2 retries, 2s apart, on a failed file (don't hang forever
#                on a locked file)
# /MT:8          8-thread multithreaded copy
# /NFL /NDL /NP  no file list / no dir list / no per-file progress percentage
# /NJH /NJS      no job header / no job summary
# (all of the above copied verbatim from scripts/migrate-execute.ps1)
_ROBOCOPY_ARGS = ("/E", "/COPY:DAT", "/R:2", "/W:2", "/MT:8", "/NFL", "/NDL", "/NP", "/NJH", "/NJS")


class RobocopyEngine:
    """CopyEngine backed by Windows' built-in robocopy.exe. Verification
    (pre/post file-count + byte-count comparison) is the caller's job --
    see core.copystats.dir_stats and the CopyEngine Protocol docstring in
    platform/base.py; this class's only job is the copy itself.
    """

    kind = "robocopy"

    def copy(self, source: str, destination: str) -> None:
        exe = shutil.which("robocopy.exe") or shutil.which("robocopy")
        if not exe:
            raise BackendNotFoundError("robocopy.exe not found on PATH -- it ships with Windows")

        result = subprocess.run(
            [exe, source, destination, *_ROBOCOPY_ARGS],
            capture_output=True,
        )

        # robocopy's exit-code convention is unusual: 0-7 are all SUCCESS
        # variants (bit flags for files-copied / extra-files-at-destination
        # / mismatched-files), only >=8 means at least one real failure.
        # See scripts/migrate-execute.ps1's matching comment -- this is the
        # same convention, ported verbatim.
        if result.returncode >= 8:
            stderr = (result.stderr or b"").decode(errors="replace").strip()
            stdout = (result.stdout or b"").decode(errors="replace").strip()
            detail = stderr or stdout
            message = (
                f"robocopy failed copying '{source}' to '{destination}' "
                f"(exit code {result.returncode}). Nothing was removed; "
                f"inspect the destination and retry."
            )
            if detail:
                message = f"{message} {detail}"
            raise StoropsError(message)
