"""Windows preferred ScanBackend: shells out to WizTree's CLI export and
parses the resulting CSV.

Ports the behavior (not the language) of the old scripts/lib/backends/
WizTree.psm1. Originally authored without access to a real Windows machine
with WizTree installed, so the CLI flags came from WizTree's published CLI
docs (diskanalyzer.com/guide), not a live test run -- since verified
end-to-end against a real WizTree 4.32 install: find_wiztree() ->
_run_export() -> parse_wiztree_csv() round-trips correctly, including two
real-world shapes no hand-written fixture had covered before: an
unregistered install's localized donation-nag banner line ahead of the
(also-localized) header row, and the scanned root's data row carrying
extra trailing DRIVECAPACITY/FREESPACE/USEDSPACE/RESERVEDSPACE columns
beyond the fixed 7 (see test_real_export_banner_line_and_extra_drive_columns).
Still unverified: whether `/exportmaxdepth` counts depth relative to the
scanned target or the drive root (assumed "relative to the scanned
target" -- see WizTree.psm1's matching note), and admin=True's actual
MFT-direct-read speedup (WizTree does not self-elevate via UAC when
/admin=1 is passed to a non-elevated process -- it just fails outright;
confirmed live). Without elevation, a WizTree CLI export was measured
*slower* than platform.windows.scan.WindowsNativeBackend on this same
machine for a directory in the tens/hundreds of thousands of entries --
see README.md's "Admin privileges are optional but recommended" note.
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
_IDX_NAME, _IDX_SIZE, _IDX_ALLOCATED, _IDX_MODIFIED, _IDX_ATTRIBUTES, _IDX_FILES, _IDX_FOLDERS = range(7)


def _field(fields: list[str], index: int) -> str | None:
    """fields[index], or None past the end -- a data row is normally
    exactly len(_FIXED_HEADER) long, but the *first* row of a scan-root
    export carries extra trailing DRIVECAPACITY/FREESPACE/... columns
    (observed on a real WizTree install), and any row could in principle
    be short/malformed, so this never raises IndexError either way.
    """
    return fields[index] if index < len(fields) else None

_INT_RE = re.compile(r"^-?\d+$")

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
)

# Fast-path patterns mirroring _DATETIME_FORMATS 1:1 (group order maps each
# capture to (year, month, day, hour, minute, second)) -- datetime.strptime()
# profiles as the dominant cost of parsing a large WizTree export: CPython's
# _strptime() calls locale.getlocale() on every single invocation to resolve
# month/weekday names, even though none of these formats use any (they're
# plain zero-padded numbers). Measured on a synthetic 1.5M-row export sized
# to a real `C:\` scan: ~13s with strptime, ~1s with this regex + datetime()
# fast path -- and a real machine's locale determines which of the 5 formats
# actually matches, so on a non-US-format Windows install (confirmed live:
# a zh-CN install here exports "%Y/%m/%d %H:%M:%S", format #2) every row
# already pays for one failed strptime attempt before the one that succeeds.
# Only matches the exact zero-padded shape WizTree always emits; anything
# else falls through to the strptime loop below unchanged, so no input that
# loop ever accepted stops being accepted -- this is purely a fast path in
# front of it, not a replacement.
def _ymd_order(g: tuple[str, ...]) -> tuple[str, str, str, str, str, str]:
    return g


def _dmy_swap_mdy(g: tuple[str, ...]) -> tuple[str, str, str, str, str, str]:
    return g[2], g[0], g[1], g[3], g[4], g[5]


def _dmy_swap_dmy(g: tuple[str, ...]) -> tuple[str, str, str, str, str, str]:
    return g[2], g[1], g[0], g[3], g[4], g[5]


_FAST_DATETIME_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$"), _ymd_order),
    (re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})$"), _ymd_order),
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})$"), _dmy_swap_mdy),
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})$"), _dmy_swap_dmy),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$"), _ymd_order),
)


def _try_fast_datetime(value: str) -> datetime | None:
    """Regex + datetime() equivalent of trying each _DATETIME_FORMATS entry
    in order via strptime -- same fallthrough-on-ValueError semantics
    (needed since the two "/"-separated formats share one regex shape and
    are only disambiguated by which interpretation produces valid month/day
    values, exactly like trying %m/%d/%Y then %d/%m/%Y today).
    """
    for pattern, to_ymd in _FAST_DATETIME_PATTERNS:
        m = pattern.match(value)
        if not m:
            continue
        y, mo, d, h, mi, s = to_ymd(m.groups())
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        except ValueError:
            continue
    return None


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

    fast = _try_fast_datetime(value)
    if fast is not None:
        return fast

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
        name = _field(fields, _IDX_NAME)
        if not name:
            continue

        is_folder = name.endswith("\\")
        full_name = name.rstrip("\\") if is_folder else name

        size_bytes = _parse_int(_field(fields, _IDX_SIZE))
        allocated_bytes = _parse_int(_field(fields, _IDX_ALLOCATED))
        modified_raw = _field(fields, _IDX_MODIFIED)
        modified = _parse_datetime(modified_raw) if modified_raw else None
        files_raw = _field(fields, _IDX_FILES)
        file_count = _parse_int(files_raw) if files_raw not in (None, "") else None
        folders_raw = _field(fields, _IDX_FOLDERS)
        folder_count = _parse_int(folders_raw) if folders_raw not in (None, "") else None

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
