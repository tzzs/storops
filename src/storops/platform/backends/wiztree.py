"""Windows preferred ScanBackend: shells out to WizTree's CLI export and
parses the resulting CSV.

Ports the behavior (not the language) of the old scripts/lib/backends/
WizTree.psm1. CAVEAT carried forward unchanged from that module's own
comment: this was authored without access to a real Windows machine with
WizTree installed, so the CLI flags below come from WizTree's published
CLI docs (diskanalyzer.com/guide), not a live test run. In particular
whether `/exportmaxdepth` counts depth relative to the scanned target or
the drive root is assumed ("relative to the scanned target") but not
verified -- see WizTree.psm1's matching note.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import TYPE_CHECKING

from storops.core.errors import StoropsError
from storops.core.models import Entry, ScanWarning
from storops.core.paths import resolve_path

if TYPE_CHECKING:
    from storops.platform.base import ScanBackend

# Columns are always in this order in a WizTree CLI export, regardless of
# which optional export flags were passed. Folder rows end "File Name"
# with a trailing backslash.
_FIXED_HEADER = ("File Name", "Size", "Allocated", "Modified", "Attributes", "Files", "Folders")

_INT_RE = re.compile(r"^-?\d+$")

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)


def find_wiztree() -> str | None:
    """Locate the WizTree CLI executable, in priority order:

    1. $STOROPS_WIZTREE_PATH (a full path to the exe, or a directory to
       search within it for WizTree64.exe/WizTree.exe).
    2. WizTree64.exe / WizTree.exe on PATH.
    3. Well-known install locations under %ProgramFiles%,
       %ProgramFiles(x86)%, %LOCALAPPDATA%\\WizTree.

    Returns None (rather than raising) when nothing is found -- unlike the
    old PowerShell Get-StorOpsWizTreePath, which threw. The Python caller
    (get_windows_scan_backend()) treats "not found" as "fall back to
    WindowsNativeBackend", not a hard error -- see §2.13.
    """
    env_path = os.environ.get("STOROPS_WIZTREE_PATH")
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        if os.path.isdir(env_path):
            for name in ("WizTree64.exe", "WizTree.exe"):
                candidate = os.path.join(env_path, name)
                if os.path.isfile(candidate):
                    return candidate
        return None

    for name in ("WizTree64.exe", "WizTree.exe"):
        found = shutil.which(name)
        if found:
            return found

    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        for name in ("WizTree64.exe", "WizTree.exe"):
            candidate = os.path.join(root, "WizTree", name)
            if os.path.isfile(candidate):
                return candidate

    return None


def _find_header_index(lines: list[str]) -> int:
    """WizTree's CLI localizes the header row's text to the OS display
    language (e.g. Chinese Windows exports "文件名称,大小,..." instead of
    "File Name,Size,..."), so header detection deliberately never matches
    against English header text. Instead: the header row is the first
    line with >=2 naively-comma-split fields whose second field is NOT a
    plain integer (every data row's second field is the numeric Size,
    which a text header never is). This is a structural, language-
    independent trick -- same one the (already-fixed) PowerShell version
    uses.
    """
    for index, line in enumerate(lines):
        fields = line.split(",")
        if len(fields) >= 2:
            second = fields[1].strip().strip('"')
            if not _INT_RE.match(second):
                return index
    return -1


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip().strip('"')
    if not value:
        return None
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    value = value.strip().strip('"')
    if not value:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def parse_wiztree_csv(csv_path: str) -> list[Entry]:
    """Parse a WizTree CLI CSV export into Entry objects.

    Field splitting for the actual data rows uses Python's `csv` module
    (so quoted filenames containing commas are handled correctly) -- only
    the header-row *detection* above uses a naive split, matching the
    (language-independent) structural trick.
    """
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        raw_lines = fh.read().splitlines()

    header_index = _find_header_index(raw_lines)
    if header_index < 0:
        raise StoropsError(f"'{csv_path}' does not look like a WizTree export (no header row found).")

    data_lines = raw_lines[header_index + 1 :]
    entries: list[Entry] = []
    for fields in csv.reader(data_lines):
        if not fields:
            continue
        row = dict(zip(_FIXED_HEADER, fields))
        name = row.get("File Name", "")
        if not name:
            continue

        is_folder = name.endswith("\\")
        full_name = name.rstrip("\\") if is_folder else name

        size_bytes = _parse_int(row.get("Size"))
        allocated_bytes = _parse_int(row.get("Allocated"))
        modified = _parse_datetime(row["Modified"]) if row.get("Modified") else None
        file_count = _parse_int(row["Files"]) if row.get("Files") not in (None, "") else None
        folder_count = _parse_int(row["Folders"]) if row.get("Folders") not in (None, "") else None

        entries.append(
            Entry(
                full_name=full_name,
                is_folder=is_folder,
                size_bytes=size_bytes,
                allocated_bytes=allocated_bytes,
                modified=modified,
                file_count=file_count,
                folder_count=folder_count,
            )
        )

    return entries


def _run_export(
    exe: str,
    target: str,
    *,
    export_folders: bool,
    export_files: bool,
    max_depth: int,
    sort_by: int,
    admin: bool,
    name_filter: str | None,
    name_exclude: str | None,
    timeout_seconds: int,
) -> str:
    """Invoke the WizTree CLI export and return the path to the CSV it
    produced. Never uses shell=True; every argument (including the target
    path) is passed as a discrete list element to subprocess.run, never
    interpolated into a shell string.
    """
    fd, csv_path = tempfile.mkstemp(prefix="storops-wiztree-", suffix=".csv")
    os.close(fd)
    try:
        os.remove(csv_path)  # WizTree must create it fresh; an empty file left behind can confuse export
    except OSError:
        pass

    args = [
        exe,
        target,
        f"/export={csv_path}",
        f"/exportfolders={1 if export_folders else 0}",
        f"/exportfiles={1 if export_files else 0}",
        f"/exportmaxdepth={max_depth}",
        f"/sortby={sort_by}",
        f"/admin={1 if admin else 0}",
    ]
    if name_filter:
        args.append(f"/filter={name_filter}")
    if name_exclude:
        args.append(f"/filterexclude={name_exclude}")

    try:
        subprocess.run(args, capture_output=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise StoropsError(
            f"WizTree did not finish scanning '{target}' within {timeout_seconds} seconds."
        ) from exc

    if not os.path.isfile(csv_path):
        raise StoropsError(
            f"WizTree exited without producing an export at '{csv_path}'. "
            f"Confirm '{target}' exists and is readable (try admin=True for a full MFT scan)."
        )

    return csv_path


class WizTreeBackend:
    """ScanBackend backed by the WizTree CLI. Preferred backend on
    Windows -- see get_windows_scan_backend() in platform/windows/scan.py
    for the fallback-to-native logic when WizTree isn't installed.
    """

    name = "WizTree"

    def __init__(self, exe_path: str, *, timeout_seconds: int = 300) -> None:
        self._exe = exe_path
        self._timeout_seconds = timeout_seconds
        self._warnings: list[ScanWarning] = []

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
        self._warnings = []
        target = resolve_path(path)
        csv_path = _run_export(
            self._exe,
            target,
            export_folders=export_folders,
            export_files=export_files,
            max_depth=max_depth,
            sort_by=1,
            admin=admin,
            name_filter=name_filter,
            name_exclude=name_exclude,
            timeout_seconds=self._timeout_seconds,
        )
        try:
            return parse_wiztree_csv(csv_path)
        finally:
            try:
                os.remove(csv_path)
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
        target = resolve_path(path)
        entries = self.scan(
            target,
            export_folders=True,
            export_files=include_files,
            max_depth=max_depth,
            admin=admin,
        )
        filtered = [e for e in entries if e.full_name != target]
        return sorted(filtered, key=lambda e: e.size_bytes, reverse=True)[:top]

    def path_size(self, path: str, *, admin: bool = False) -> Entry | None:
        target = resolve_path(path)
        if not os.path.exists(target):
            return None
        parent = os.path.dirname(target)
        if not parent or parent == target:
            return None
        entries = self.scan(parent, export_folders=True, export_files=True, max_depth=1, admin=admin)
        for entry in entries:
            if entry.full_name == target:
                return entry
        return None

    def advice(self) -> str | None:
        # WizTree is the recommended backend -- nothing to suggest.
        return None

    def take_warnings(self) -> list[ScanWarning]:
        warnings, self._warnings = self._warnings, []
        return warnings
