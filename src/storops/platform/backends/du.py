"""StorOps' last-resort Linux/macOS scan backend: the `du` that ships with
every Unix-like system. Single-threaded, one stat() syscall per entry --
noticeably slower than gdu on large trees, since there is no parallel
directory walk and no filesystem-metadata shortcut the way WizTree has on
NTFS (see docs/DESIGN.md §4a/§4b). Selected by platform/base.py's
get_scan_backend() only when gdu is not found on PATH.

This is a straight port of scripts/lib/backends/Du.psm1 -- read that file's
header comment for the full rationale on GNU-vs-BSD flag differences and
why depth-limiting is always passed natively to `du` itself rather than
"scan everything, then truncate in Python".
"""
from __future__ import annotations

import os
import subprocess
from fnmatch import fnmatch

from storops.core.errors import InvalidPathError, PermissionDeniedError
from storops.core.models import Entry, ScanWarning
from storops.core.paths import resolve_path

_DU_FALLBACK_ADVICE = (
    "Install gdu for noticeably faster scans on large directory trees: "
    "https://github.com/dundee/gdu#installation"
)


def _split_segments(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


class DuBackend:
    """ScanBackend implementation shelling out to the system `du`."""

    name = "Du"

    def __init__(self) -> None:
        self._flavor: str | None = None  # "gnu" | "bsd", cached per instance

    def _du_flavor(self) -> str:
        if self._flavor is not None:
            return self._flavor
        try:
            proc = subprocess.run(
                ["du", "--version"], capture_output=True, text=True, timeout=5
            )
            self._flavor = "gnu" if proc.returncode == 0 and "GNU coreutils" in proc.stdout else "bsd"
        except (OSError, subprocess.TimeoutExpired):
            self._flavor = "bsd"
        return self._flavor

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
        # `admin` is accepted for Protocol/signature parity with the Windows
        # backend but is a no-op here -- StorOps never silently re-execs
        # itself under sudo.
        target = resolve_path(path)
        if not os.path.exists(target):
            raise InvalidPathError(f"StorOps: '{target}' does not exist.")

        flavor = self._du_flavor()
        if flavor == "gnu":
            # -b = --apparent-size --block-size=1: logical/apparent size in
            # bytes, comparable to WizTree's "Size" column (not its "Allocated").
            args: list[str] = ["du", "-a", "-b"]
            if max_depth > 0:
                args.append(f"--max-depth={max_depth}")
        else:
            # BSD/macOS du has no portable apparent-size-in-bytes flag;
            # report 1024-byte blocks (-k) and scale below. This is
            # disk-usage, not apparent size, on this flavor -- a known,
            # documented approximation.
            #
            # BSD du's -a, -s, and -d depth are mutually exclusive
            # (`usage: du [-a | -s | -d depth]`) -- combining -a with -d
            # always fails with a usage error (exit 64), which used to be
            # misreported below as "permission denied". Unlike GNU, BSD's
            # -d depth alone reports only directory totals, never
            # individual files (confirmed against a real macOS runner --
            # dropping -a silently loses every file-level entry, which
            # broke callers that need files at a limited depth, e.g.
            # top_entries/path_size). There is no single BSD du invocation
            # that gives both file-level entries and a native depth limit,
            # so on this flavor we always run -a (every file, unbounded
            # depth) and let the depth filter below -- originally just a
            # belt-and-braces guard for GNU -- do the real depth-limiting
            # for BSD instead.
            args = ["du", "-k", "-a"]
        args.extend(["--", target])

        # stderr is discarded, matching Du.psm1's `2>$null`: a
        # permission-denied subtree during `du` is silently skipped rather
        # than surfaced as a structured warning. This is an acceptable,
        # documented v1 limitation -- see take_warnings() below.
        proc = subprocess.run(args, capture_output=True, text=True)
        raw = proc.stdout
        if proc.returncode != 0 and not raw:
            raise PermissionDeniedError(
                f"StorOps: du exited with code {proc.returncode} scanning '{target}' "
                "(permission denied on a subtree? re-run the whole command under sudo "
                "-- StorOps never self-elevates)."
            )

        root_segments = len(_split_segments(target))
        entries: list[Entry] = []

        for line in raw.splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue

            size = int(parts[0])
            if flavor != "gnu":
                size *= 1024
            entry_path = parts[1]
            if entry_path == target:
                continue

            # Belt-and-braces: du was already asked to stop at max_depth,
            # this just guards against any flavor quirk that returns deeper
            # rows.
            depth = len(_split_segments(entry_path)) - root_segments
            if max_depth != 0 and depth > max_depth:
                continue

            is_folder = os.path.isdir(entry_path)
            if (is_folder and not export_folders) or ((not is_folder) and not export_files):
                continue

            name = os.path.basename(entry_path)
            if name_filter and not fnmatch(name, name_filter):
                continue
            if name_exclude and fnmatch(name, name_exclude):
                continue

            entries.append(
                Entry(
                    full_name=entry_path,
                    is_folder=is_folder,
                    size_bytes=size,
                    allocated_bytes=size,
                    modified=None,
                    file_count=None,
                    folder_count=None,
                )
            )

        return entries

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
        return _DU_FALLBACK_ADVICE

    def take_warnings(self) -> list[ScanWarning]:
        # Matches Du.psm1's `2>$null`: du's own stderr (typically
        # "Permission denied" on unreadable subtrees) is discarded rather
        # than parsed into structured warnings. A single unreadable subtree
        # already never aborts the scan (du itself keeps going and StorOps
        # only fails on a fully-empty result -- see scan() above); this is
        # an acceptable, documented v1 limitation rather than a silent bug.
        return []
