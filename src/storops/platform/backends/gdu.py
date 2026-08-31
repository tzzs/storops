"""StorOps' preferred Linux/macOS scan backend: gdu
(https://github.com/dundee/gdu), a Go disk-usage analyzer that walks
directories with a goroutine pool instead of one file at a time -- the
closest cross-platform equivalent to WizTree's speed advantage that is
achievable without reading filesystem metadata directly (no ext4/APFS has
a public, stable MFT-like read path -- see docs/DESIGN.md §4a/§4b).
Selected automatically by platform/base.py's get_scan_backend() when `gdu`
is on PATH (or $STOROPS_GDU_PATH is set); falls back to backends/du.py
otherwise.

UNVERIFIED, matching backends/Gdu.psm1's own caveat: authored without a
live gdu install to test against (this sandbox has neither a Windows
machine nor a gdu binary available). The JSON export shape parsed below
follows gdu's own documented dump format -- a
[schemaVersion, flags, rootNode] array where each node carries
name/asize/dsize/isDir/files -- but this is the one thing here that should
be double-checked against `gdu --help` and a real
`gdu -n -o out.json <path>` run before relying on it in production, the
same caveat WizTree.psm1 carries for its own CLI assumptions.

gdu always builds the *whole* tree before exporting (no native
depth-limit flag the way WizTree's /exportmaxdepth or du's --max-depth
provide) -- max_depth is applied by StorOps after the fact, by simply not
recursing/emitting past it (see _flatten_node below).
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


def _flatten_node(
    node: dict,
    parent_path: str,
    depth: int,
    *,
    max_depth: int,
    export_folders: bool,
    export_files: bool,
) -> list[Entry]:
    """Flatten one gdu tree node's children into StorOps Entry objects,
    recursing only as far as max_depth allows (0 = unlimited). depth == 1
    means "immediate child of the scanned root", matching WizTree's
    /exportmaxdepth "relative to scanned target" semantics.
    """
    results: list[Entry] = []
    full_path = os.path.join(parent_path, node.get("name", ""))
    is_dir = bool(node.get("isDir"))
    children = node.get("files") or []

    if max_depth == 0 or depth <= max_depth:
        if (is_dir and export_folders) or (not is_dir and export_files):
            file_count = None
            folder_count = None
            if is_dir and children:
                file_count = sum(1 for c in children if not c.get("isDir"))
                folder_count = sum(1 for c in children if c.get("isDir"))
            asize = int(node.get("asize", 0))
            dsize = node.get("dsize")
            results.append(
                Entry(
                    full_name=full_path,
                    is_folder=is_dir,
                    size_bytes=asize,
                    allocated_bytes=int(dsize) if dsize is not None else asize,
                    modified=None,  # gdu's JSON export does not carry mtimes
                    file_count=file_count,
                    folder_count=folder_count,
                )
            )

    if is_dir and children and (max_depth == 0 or depth < max_depth):
        for child in children:
            results.extend(
                _flatten_node(
                    child,
                    full_path,
                    depth + 1,
                    max_depth=max_depth,
                    export_folders=export_folders,
                    export_files=export_files,
                )
            )

    return results


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

            # [schemaVersion, flags, rootNode] -- see this module's docstring.
            root_node = raw[2]

            entries: list[Entry] = []
            for child in root_node.get("files") or []:
                entries.extend(
                    _flatten_node(
                        child,
                        target,
                        1,
                        max_depth=max_depth,
                        export_folders=export_folders,
                        export_files=export_files,
                    )
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
