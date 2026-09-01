"""Windows scan backends + capacity provider.

WizTree (NTFS MFT direct-read) is the preferred backend on Windows -- see
storops.platform.backends.wiztree. This module provides:

- `get_windows_scan_backend()`: the factory storops.platform.base.
  get_scan_backend() calls on Windows. Tries to locate WizTree; falls back
  to `WindowsNativeBackend` when it isn't installed.
- `WindowsNativeBackend`: a NET-NEW capability (the old PowerShell v1 tool
  had no fallback at all -- no WizTree meant the tool was simply unusable
  on Windows, see docs/plans/storops-v2-cross-platform-refactor.md
  §1.6.3/§2.13). Built on `os.scandir()`/`os.stat()` only -- zero
  third-party/external-binary dependency, at the cost of being roughly the
  same speed class as Linux `du` (a full stat() walk), not WizTree's
  MFT-direct-read speed.
- `WindowsCapacityProvider`: `shutil.disk_usage()` -- stdlib already wraps
  `GetDiskFreeSpaceExW`, no ctypes/pywin32 needed (§2.11a).
"""
from __future__ import annotations

import fnmatch
import os
import shutil
from datetime import datetime
from typing import TYPE_CHECKING

from storops.core.models import Capacity, Entry, ScanWarning
from storops.core.paths import resolve_path

if TYPE_CHECKING:
    from storops.platform.base import ScanBackend


def get_windows_scan_backend() -> "ScanBackend":
    """Factory consumed by storops.platform.base.get_scan_backend() on
    Windows. Prefers WizTree; falls back to the native os.scandir backend
    when WizTree cannot be located.
    """
    from storops.platform.backends.wiztree import WizTreeBackend, find_wiztree

    exe = find_wiztree()
    if exe:
        return WizTreeBackend(exe)
    return WindowsNativeBackend()


def _allocated_bytes(st: os.stat_result) -> int:
    """Best-effort "size on disk". POSIX exposes `st_blocks` (512-byte
    units) but Windows' os.stat() does not -- there is no stdlib-only way
    to get NTFS's cluster-rounded allocation size the way WizTree's
    "Allocated" column does (that would need `GetCompressedFileSizeW` via
    ctypes). Fall back to the logical size, which is close enough for a
    "no third-party tool" fallback path and is explicitly a slower/rougher
    backend than WizTree already (§2.13).
    """
    blocks = getattr(st, "st_blocks", None)
    if blocks is not None:
        return int(blocks) * 512
    return int(st.st_size)


def _name_matches(name: str, name_filter: str | None, name_exclude: str | None) -> bool:
    lowered = name.lower()
    if name_filter and not fnmatch.fnmatch(lowered, name_filter.lower()):
        return False
    if name_exclude and fnmatch.fnmatch(lowered, name_exclude.lower()):
        return False
    return True


def _walk(
    path: str,
    *,
    depth: int,
    max_depth: int,
    export_folders: bool,
    export_files: bool,
    name_filter: str | None,
    name_exclude: str | None,
    warnings: list[ScanWarning],
    out: list[Entry],
) -> tuple[int, int, int, int, datetime | None]:
    """Recursively stat the contents of directory `path`.

    Always fully recurses (to compute correct aggregate directory sizes --
    mirroring WizTree's own "the tool always knows the true size, only the
    *export listing* is depth-limited" behavior), but only appends Entry
    rows to `out` for children within `max_depth` levels of the original
    scan root (`max_depth == 0` means unlimited, matching WizTree's
    `/exportmaxdepth=0` convention).

    A directory this process cannot read raises PermissionError from
    os.scandir()/DirEntry.stat() -- collected into `warnings` as a
    ScanWarning rather than aborting the whole walk (core/models.
    ScanWarning; see docs/plans/...§2.6/§14: one unreadable subtree must
    never abort an entire scan).

    Returns this directory's own (size_bytes, allocated_bytes, file_count,
    folder_count, most-recent-modified) aggregated over its full subtree.
    """
    total_size = 0
    total_allocated = 0
    file_count = 0
    folder_count = 0
    latest_mtime: datetime | None = None

    try:
        children = list(os.scandir(path))
    except PermissionError as exc:
        warnings.append(ScanWarning(path=path, code="permission_denied", message=str(exc)))
        return 0, 0, 0, 0, None
    except OSError as exc:
        warnings.append(ScanWarning(path=path, code="scan_error", message=str(exc)))
        return 0, 0, 0, 0, None

    child_depth = depth + 1
    within_depth = max_depth == 0 or child_depth <= max_depth

    for child in children:
        try:
            is_dir = child.is_dir(follow_symlinks=False)
        except OSError as exc:
            warnings.append(ScanWarning(path=child.path, code="scan_error", message=str(exc)))
            continue

        if is_dir:
            folder_count += 1
            sub_size, sub_alloc, sub_files, sub_folders, sub_mtime = _walk(
                child.path,
                depth=child_depth,
                max_depth=max_depth,
                export_folders=export_folders,
                export_files=export_files,
                name_filter=name_filter,
                name_exclude=name_exclude,
                warnings=warnings,
                out=out,
            )
            total_size += sub_size
            total_allocated += sub_alloc
            file_count += sub_files
            folder_count += sub_folders
            if sub_mtime and (latest_mtime is None or sub_mtime > latest_mtime):
                latest_mtime = sub_mtime
            if within_depth and export_folders and _name_matches(child.name, name_filter, name_exclude):
                out.append(
                    Entry(
                        full_name=child.path,
                        is_folder=True,
                        size_bytes=sub_size,
                        allocated_bytes=sub_alloc,
                        modified=sub_mtime,
                        file_count=sub_files,
                        folder_count=sub_folders,
                    )
                )
        else:
            try:
                st = child.stat(follow_symlinks=False)
            except PermissionError as exc:
                warnings.append(ScanWarning(path=child.path, code="permission_denied", message=str(exc)))
                continue
            except OSError as exc:
                warnings.append(ScanWarning(path=child.path, code="scan_error", message=str(exc)))
                continue

            file_count += 1
            total_size += st.st_size
            allocated = _allocated_bytes(st)
            total_allocated += allocated
            try:
                mtime = datetime.fromtimestamp(st.st_mtime)
            except (OSError, OverflowError, ValueError) as exc:
                warnings.append(ScanWarning(path=child.path, code="scan_error", message=str(exc)))
                mtime = None
            if mtime is not None and (latest_mtime is None or mtime > latest_mtime):
                latest_mtime = mtime
            if within_depth and export_files and _name_matches(child.name, name_filter, name_exclude):
                out.append(
                    Entry(
                        full_name=child.path,
                        is_folder=False,
                        size_bytes=st.st_size,
                        allocated_bytes=allocated,
                        modified=mtime,
                    )
                )

    return total_size, total_allocated, file_count, folder_count, latest_mtime


class WindowsNativeBackend:
    """Zero-dependency Windows scan fallback used when WizTree is not
    installed. Net-new capability vs. the PowerShell v1 tool -- see module
    docstring. Deliberately simple (`os.scandir` recursion with a depth
    limit); no attempt is made to match WizTree's MFT-read speed.
    """

    name = "WindowsNative"

    def __init__(self) -> None:
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
        # `admin` has no meaning for this backend: there is no MFT-direct-
        # read/elevation path in a pure os.scandir() walk, so it is
        # accepted (to satisfy the ScanBackend Protocol) and ignored.
        self._warnings = []
        target = resolve_path(path)
        out: list[Entry] = []
        _walk(
            target,
            depth=0,
            max_depth=max_depth,
            export_folders=export_folders,
            export_files=export_files,
            name_filter=name_filter,
            name_exclude=name_exclude,
            warnings=self._warnings,
            out=out,
        )
        return out

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
        )
        return sorted(entries, key=lambda e: e.size_bytes, reverse=True)[:top]

    def path_size(self, path: str, *, admin: bool = False) -> Entry | None:
        target = resolve_path(path)
        if not os.path.exists(target):
            return None
        parent = os.path.dirname(target)
        if not parent or parent == target:
            return None
        entries = self.scan(parent, export_folders=True, export_files=True, max_depth=1)
        for entry in entries:
            if entry.full_name == target:
                return entry
        return None

    def advice(self) -> str | None:
        return (
            "WizTree was not found -- using a slower native scan (os.scandir). "
            "Install WizTree (https://diskanalyzer.com/) for much faster NTFS "
            "MFT-based scans, or set $env:STOROPS_WIZTREE_PATH if it's already "
            "installed somewhere non-standard."
        )

    def take_warnings(self) -> list[ScanWarning]:
        warnings, self._warnings = self._warnings, []
        return warnings


class WindowsCapacityProvider:
    """CapacityProvider via stdlib `shutil.disk_usage()` -- already wraps
    `GetDiskFreeSpaceExW` on Windows (§2.11a); no ctypes/pywin32 needed.
    """

    def free_space(self, path: str) -> Capacity:
        target = resolve_path(path)
        drive = os.path.splitdrive(target)[0] or target
        usage = shutil.disk_usage(target)
        # volume_name/file_system: no trivial stdlib way to get these on
        # Windows (that would need GetVolumeInformationW via ctypes, or
        # pywin32/wmi) -- left None per §2.11a's decision not to add
        # either dependency for this.
        return Capacity(
            drive=drive,
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_bytes=usage.used,
            volume_name=None,
            file_system=None,
        )
