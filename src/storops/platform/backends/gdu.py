"""StorOps' preferred Linux/macOS scan backend: gdu
(https://github.com/dundee/gdu), a Go disk-usage analyzer that walks
directories with a goroutine pool instead of one file at a time -- the
closest cross-platform equivalent to WizTree's speed advantage that is
achievable without reading filesystem metadata directly (no ext4/APFS has
a public, stable MFT-like read path -- see docs/DESIGN.md §4a/§4b).
Selected automatically by platform/base.py's get_scan_backend() when `gdu`
is on PATH (or $STOROPS_GDU_PATH is set); falls back to backends/du.py
otherwise.

Verified 2026-09-01 against a real `gdu 5.25.0` install (`apt install gdu`
on Debian) -- the JSON shape assumed by an earlier draft of this module
(and by the PowerShell Gdu.psm1 it was ported from) was wrong in two
ways, found by actually running `gdu -n -o out.json <path>` and reading
the real output rather than trusting the documented/guessed shape:

  1. The tree is `raw[3]`, not `raw[2]` (`raw[2]` is a small metadata
     object -- progname/progver/timestamp -- not the root node). The
     earlier code read `raw[2]` and got that metadata object instead,
     which has no "files" key, so every scan silently returned an empty
     result no matter what was actually on disk.
  2. The tree is nested **arrays**, not nested **objects with a "files"
     key**: `[dirInfo, child1, child2, ...]` where each child is either a
     plain dict (a file: `{"name", "asize", "dsize", "mtime"}`) or
     another such array (a subdirectory). There is no `isDir` boolean
     anywhere -- directory-ness is `isinstance(child, list)`. Directory
     entries also do NOT carry their own `asize`/`dsize` -- StorOps has
     to recursively sum a directory's descendant files itself (see
     _process_dir below), gdu does not pre-aggregate them in the export.

gdu always builds the *whole* tree before exporting (no native
depth-limit flag the way WizTree's /exportmaxdepth or du's --max-depth
provide) -- so directory sizes are always computed by summing the full
subtree regardless of max_depth; max_depth only controls which depths get
turned into emitted Entry objects (see _process_dir's `collected` param).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from fnmatch import fnmatch

from storops.core.errors import BackendNotFoundError, InvalidPathError, StoropsError
from storops.core.models import Entry, ScanWarning
from storops.core.paths import resolve_path

_GDU_INSTALL_HELP = (
    "StorOps could not locate gdu. Install it (e.g. `brew install gdu`, "
    "`apt install gdu`, or see https://github.com/dundee/gdu#installation), "
    "or point StorOps at an existing binary via the STOROPS_GDU_PATH "
    "environment variable. StorOps will fall back to the slower system "
    "`du` if gdu is unavailable."
)


def is_available() -> bool:
    """True if gdu can be resolved via $STOROPS_GDU_PATH or PATH.

    Used by platform/base.py's get_scan_backend() to decide between
    GduBackend and the du.py fallback -- mirrors ScanBackend.psm1's
    selection check.
    """
    env_path = os.environ.get("STOROPS_GDU_PATH")
    if env_path:
        return os.path.isfile(env_path)
    return shutil.which("gdu") is not None


def _resolve_gdu_path() -> str:
    env_path = os.environ.get("STOROPS_GDU_PATH")
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        raise BackendNotFoundError(
            f"StorOps: $STOROPS_GDU_PATH is set to '{env_path}' but that file was not found."
        )
    found = shutil.which("gdu")
    if found:
        return found
    raise BackendNotFoundError(_GDU_INSTALL_HELP)


def _process_dir(
    dir_array: list,
    dir_path: str,
    depth: int,
    *,
    max_depth: int,
    export_folders: bool,
    export_files: bool,
    collected: list[Entry],
) -> tuple[int, int, int, int]:
    """Process one gdu directory array `[dirInfo, child1, child2, ...]`,
    recursively summing descendant sizes (directory nodes never carry their
    own asize/dsize in gdu's export -- see module docstring) and appending
    an Entry to `collected` for each child within `max_depth` (0 =
    unlimited). `depth` is this directory's own depth relative to the scan
    root (root itself = depth 0, so its direct children are emitted at
    depth 1 -- matching WizTree's /exportmaxdepth "relative to scanned
    target" semantics and backends/du.py's convention).

    Returns (total_asize, total_dsize, direct_file_count, direct_folder_count)
    for THIS directory -- i.e. the aggregate size of everything under it,
    and how many immediate file/folder children it has (not recursive
    counts) -- so the caller can build an accurate Entry for it.
    """
    total_asize = 0
    total_dsize = 0
    direct_file_count = 0
    direct_folder_count = 0

    for child in dir_array[1:]:
        if isinstance(child, list):
            child_info = child[0] if child else {}
            child_name = child_info.get("name", "")
            child_path = os.path.join(dir_path, child_name)
            child_asize, child_dsize, child_files, child_folders = _process_dir(
                child,
                child_path,
                depth + 1,
                max_depth=max_depth,
                export_folders=export_folders,
                export_files=export_files,
                collected=collected,
            )
            total_asize += child_asize
            total_dsize += child_dsize
            direct_folder_count += 1
            if export_folders and (max_depth == 0 or depth + 1 <= max_depth):
                collected.append(
                    Entry(
                        full_name=child_path,
                        is_folder=True,
                        size_bytes=child_asize,
                        allocated_bytes=child_dsize,
                        modified=None,  # gdu's JSON export carries mtime per-node, not surfaced here (v1 parity: same as WizTree/du backends' folder rows)
                        file_count=child_files,
                        folder_count=child_folders,
                    )
                )
        else:
            child_name = child.get("name", "")
            child_path = os.path.join(dir_path, child_name)
            asize = int(child.get("asize", 0))
            dsize = int(child.get("dsize", asize))
            total_asize += asize
            total_dsize += dsize
            direct_file_count += 1
            if export_files and (max_depth == 0 or depth + 1 <= max_depth):
                collected.append(
                    Entry(
                        full_name=child_path,
                        is_folder=False,
                        size_bytes=asize,
                        allocated_bytes=dsize,
                        modified=None,
                        file_count=None,
                        folder_count=None,
                    )
                )

    return total_asize, total_dsize, direct_file_count, direct_folder_count


class GduBackend:
    """ScanBackend implementation shelling out to the `gdu` binary."""

    name = "Gdu"

    def scan(
        self,
        path: str,
        *,
        export_folders: bool = True,
        export_files: bool = False,
        max_depth: int = 0,
        name_filter: str | None = None,
        name_exclude: str | None = None,
        admin: bool = False,
    ) -> list[Entry]:
        # `admin` is accepted for Protocol/signature parity with the
        # Windows backend but is a no-op here -- StorOps never silently
        # re-execs itself under sudo. If a scan hits Permission Denied
        # subtrees, re-run the whole command under sudo yourself.
        exe = _resolve_gdu_path()
        target = resolve_path(path)
        if not os.path.exists(target):
            raise InvalidPathError(f"StorOps: '{target}' does not exist.")

        fd, out_file = tempfile.mkstemp(suffix=".json", prefix="storops-gdu-")
        os.close(fd)
        os.remove(out_file)  # gdu must create it fresh

        try:
            # -n: no ANSI color codes in any incidental output. -o: write
            # the JSON export and exit instead of opening the interactive TUI.
            try:
                subprocess.run(
                    [exe, "-n", "-o", out_file, target],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except subprocess.TimeoutExpired as exc:
                raise StoropsError(
                    f"StorOps: gdu did not finish scanning '{target}' within 300 seconds."
                ) from exc

            if not os.path.isfile(out_file):
                raise StoropsError(
                    f"StorOps: gdu exited without producing an export for '{target}'. "
                    "Confirm the path exists and is readable (re-run the whole command "
                    "under sudo for permission-denied subtrees -- StorOps never self-elevates)."
                )

            with open(out_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)

            # [schemaVersion, someFlag, metaInfo, tree] -- tree is
            # [rootDirInfo, child1, child2, ...]; see module docstring.
            tree = raw[3]
            if not isinstance(tree, list) or not tree:
                raise StoropsError(
                    f"StorOps: gdu's JSON export for '{target}' did not have the expected "
                    f"[schemaVersion, flags, meta, [rootInfo, ...children]] shape."
                )

            entries: list[Entry] = []
            _process_dir(
                tree,
                target,
                0,
                max_depth=max_depth,
                export_folders=export_folders,
                export_files=export_files,
                collected=entries,
            )

            # gdu has no native per-file name filter (only directory-exclude
            # via -i, not used here); apply name_filter/name_exclude
            # client-side against each entry's leaf name, matching the
            # PowerShell version's approach.
            if name_filter:
                entries = [e for e in entries if fnmatch(os.path.basename(e.full_name), name_filter)]
            if name_exclude:
                entries = [
                    e for e in entries if not fnmatch(os.path.basename(e.full_name), name_exclude)
                ]

            return entries
        finally:
            if os.path.isfile(out_file):
                try:
                    os.remove(out_file)
                except OSError:
                    pass

    def top_entries(
        self,
        path: str,
        *,
        top: int = 20,
        max_depth: int = 1,
        admin: bool = False,
        include_files: bool = False,
    ) -> list[Entry]:
        entries = self.scan(
            path,
            export_folders=True,
            export_files=include_files,
            max_depth=max_depth,
            admin=admin,
        )
        entries.sort(key=lambda e: e.size_bytes, reverse=True)
        return entries[:top]

    def path_size(self, path: str, *, admin: bool = False) -> Entry | None:
        normalized = resolve_path(path)
        if not os.path.exists(normalized):
            return None
        parent = os.path.dirname(normalized)
        if not parent or parent == normalized:
            return None

        for entry in self.scan(
            parent, export_folders=True, export_files=True, max_depth=1, admin=admin
        ):
            if entry.full_name == normalized:
                return entry
        return None

    def advice(self) -> str | None:
        # gdu is the recommended backend for this platform -- nothing to
        # suggest.
        return None

    def take_warnings(self) -> list[ScanWarning]:
        # gdu's own stderr (permission-denied subtrees etc.) is not parsed
        # into structured warnings in v1 -- same documented limitation as
        # backends/du.py's take_warnings().
        return []
