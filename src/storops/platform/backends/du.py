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
import platform as _platform
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatch
from typing import Iterator

from storops.core.errors import InvalidPathError, PermissionDeniedError
from storops.core.models import Entry, ScanWarning
from storops.core.paths import resolve_path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    # This module is POSIX-only at runtime (platform/base.py never selects
    # it on Windows), but the test suite imports it everywhere in order to
    # collect its skip markers -- a hard import here would turn every
    # Windows CI run into a collection error rather than a set of skips.
    fcntl = None  # type: ignore[assignment]

_DU_FALLBACK_ADVICE = (
    "Install gdu for noticeably faster scans on large directory trees: "
    "https://github.com/dundee/gdu#installation"
)


def _split_segments(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").split("/") if p]


# --- Depth-1 child sizing, with volume-alias pruning ------------------------

# Beyond this many immediate children, sizing each one with its own `du -s`
# costs more in process spawns than the parallelism buys back, so
# top_entries() falls back to a single `du -d 1` over the whole target.
_MAX_PARALLEL_CHILDREN = 512

# Measured sweet spot for concurrent `du -s` processes on a real macOS home
# directory: 8 workers took 99.9s down to 66.0s, 16 gave nothing back (70.7s)
# -- the walk is bound by filesystem metadata I/O, not by CPU.
_DU_CONCURRENCY = 8

# macOS' firmlinked directories all live under this one mount, so the alias
# pre-pass only ever walks toward and inside it. Everything big gets pruned
# at its entry point (a firmlink is recognized before it is descended into),
# which is what keeps the pre-pass to well under a second on a full disk.
_DARWIN_DATA_VOLUME = "/System/Volumes"

# Guards against a pathological tree, not tuning knobs.
_ALIAS_SCAN_MAX_DEPTH = 12
_ALIAS_SCAN_BUDGET = 50_000

# Darwin fcntl(2) command: write the file descriptor's canonical path into
# the supplied buffer. Stable since OS X 10.5 (sys/fcntl.h), and the only
# thing that reports a firmlink's real identity -- os.path.realpath() does
# not resolve them, and neither st_dev (identical across a volume group)
# nor a symlink check can see them at all.
_F_GETPATH = 50
_F_GETPATH_BUFSIZE = 1024


def _canonical_path(path: str) -> str | None:
    """The kernel's own canonical path for `path`, or None if it cannot be
    determined (no permission, not Darwin, path vanished mid-scan).
    """
    if fcntl is None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return None
    try:
        raw = fcntl.fcntl(fd, _F_GETPATH, b"\0" * _F_GETPATH_BUFSIZE)
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    return raw.split(b"\0", 1)[0].decode(errors="replace") or None


def _alias_scan_is_warranted(target: str) -> bool:
    """True where the filesystem is known to expose the same directory
    under more than one path below `target`.

    This is macOS' APFS volume-group topology, and only it: `/` is the
    sealed, read-only System volume, and the writable Data volume is
    mounted at /System/Volumes/Data and *also* projected into `/` by
    firmlinks. So `/Users` and `/System/Volumes/Data/Users` are literally
    the same directory -- same st_dev, same st_ino, neither one a symlink
    -- and a plain `du /` walks the user's entire home twice and reports
    a total to match. `du -x` cannot help: a volume group reports one
    st_dev throughout, so there is no device boundary to stop at.

    Restricted to the volume roots rather than run on every scan: a scan
    of e.g. a home directory has nothing for the pre-pass to find.
    """
    if _platform.system() != "Darwin":
        return False
    head = target.rstrip("/") or "/"
    return head == "/" or os.path.dirname(head) in ("/Volumes", _DARWIN_DATA_VOLUME)


def _alias_pre_pass_descends_into(path: str) -> bool:
    """Keep the pre-pass on the one branch that can contain firmlinks:
    /System/Volumes and everything under it (plus the ancestors needed to
    reach it). Without this the walk would wander into the user's home
    looking for aliases that, by construction, are never there.
    """
    head = path.rstrip("/") or "/"
    # "/" is its own separator, so the usual head + "/" would build "//".
    prefix = head if head == "/" else head + "/"
    return (
        head == _DARWIN_DATA_VOLUME
        or head.startswith(_DARWIN_DATA_VOLUME + "/")
        or _DARWIN_DATA_VOLUME.startswith(prefix)
    )


def _alias_paths(target: str) -> set[str]:
    """Directories under `target` that the kernel reports as living at a
    different canonical path -- i.e. a second route to content already
    counted under that canonical path, which `du` would otherwise walk and
    add in twice.

    An alias is never descended into, so the expensive subtrees
    (/System/Volumes/Data/Users and friends) are recognized and dropped at
    their entry point rather than walked.
    """
    aliases: set[str] = set()
    frontier = deque([(target.rstrip("/") or "/", 0)])
    budget = _ALIAS_SCAN_BUDGET

    while frontier:
        current, depth = frontier.popleft()
        if depth >= _ALIAS_SCAN_MAX_DEPTH:
            continue
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if budget <= 0:
                        return aliases
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    budget -= 1
                    canonical = _canonical_path(entry.path)
                    if canonical is not None and canonical != entry.path:
                        aliases.add(entry.path)
                        continue
                    if _alias_pre_pass_descends_into(entry.path):
                        frontier.append((entry.path, depth + 1))
        except OSError:
            continue

    return aliases


class DuBackend:
    """ScanBackend implementation shelling out to the system `du`."""

    name = "Du"

    # core/cleanup.py sizes every probe path it found; each one is an
    # independent `du -s` subprocess here, so they are safe to run
    # concurrently (no shared mutable state on this instance -- _flavor is
    # write-once-with-the-same-value, and take_warnings() is a constant).
    # Backends that keep per-call state (e.g. the Windows native backend's
    # self._warnings) must NOT set this.
    path_size_is_concurrent = True

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

    def _du_args(self, target: str, *, dirs_only: bool, max_depth: int) -> list[str]:
        """Build the `du` command line for this flavor.

        `dirs_only` (the caller wants directories and no file-level rows --
        i.e. every scan()/inspect() call, since both go through
        top_entries(include_files=False)) is the case worth special-casing:
        without `-a`, BOTH flavors report directories only AND accept a
        native depth limit, so du prints tens of rows instead of millions
        and StorOps never has to classify or even look at a single file.
        On a real macOS home directory that is the difference between
        2.4M output rows / ~2.6GB peak RSS and ~75 rows / a few MB, for
        byte-identical results.

        With `-a` the two flavors diverge. GNU takes `-a` and
        `--max-depth` together. BSD's are mutually exclusive
        (`usage: du [-a | -s | -d depth]`) -- combining them always fails
        with a usage error (exit 64), and `-d depth` alone reports only
        directory totals, never individual files (confirmed against a real
        macOS runner), so a caller that needs files at a limited depth
        gets `-a` at unbounded depth here and the depth filter in scan()
        does the real depth-limiting instead.
        """
        if self._du_flavor() == "gnu":
            # -b = --apparent-size --block-size=1: logical/apparent size in
            # bytes, comparable to WizTree's "Size" column (not its "Allocated").
            args = ["du", "-b"]
            if not dirs_only:
                args.append("-a")
            if max_depth > 0:
                args.append(f"--max-depth={max_depth}")
        else:
            # BSD/macOS du has no portable apparent-size-in-bytes flag;
            # report 1024-byte blocks (-k) and scale in scan(). This is
            # disk-usage, not apparent size, on this flavor -- a known,
            # documented approximation.
            args = ["du", "-k"]
            if dirs_only:
                if max_depth > 0:
                    args.extend(["-d", str(max_depth)])
            else:
                args.append("-a")
        args.extend(["--", target])
        return args

    @staticmethod
    def _du_lines(args: list[str]) -> Iterator[str]:
        """Stream `du`'s stdout line by line.

        Streaming rather than subprocess.run(capture_output=True) is the
        point: a `du -a` over a large home directory emits hundreds of MB
        of text, and capturing it whole (plus the list splitlines() builds
        from it) was measured at ~2.6GB peak RSS for a scan that returns
        15 rows. StorOps is most likely to be run on a machine that is
        already out of disk and under memory pressure, so holding the
        whole listing in memory is exactly the wrong trade.

        stderr is discarded, matching Du.psm1's `2>$null`: a
        permission-denied subtree during `du` is silently skipped rather
        than surfaced as a structured warning -- see take_warnings().
        """
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                yield line
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()

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
        dirs_only = export_folders and not export_files
        args = self._du_args(target, dirs_only=dirs_only, max_depth=max_depth)

        # Depth is limited natively except on BSD-with-`-a` (see _du_args);
        # computing a row's depth means splitting its whole path into
        # segments, which is real per-row work at scale, so it is skipped
        # whenever du already did the limiting.
        filter_depth = max_depth if (max_depth > 0 and not dirs_only and flavor != "gnu") else 0
        root_segments = len(_split_segments(target)) if filter_depth else 0
        scale = 1 if flavor == "gnu" else 1024

        # In `-a` mode du emits a directory only AFTER everything inside
        # it, so by the time a directory's own row arrives it has already
        # been recorded here as some child's parent -- that makes the
        # os.path.isdir() stat below unnecessary for every non-empty
        # directory. (An *empty* directory is never any row's parent and
        # is indistinguishable from a file in du's output, so those still
        # get stat'ed; they are a rounding error next to the file rows.)
        known_dirs: set[str] = set()
        entries: list[Entry] = []
        saw_any_row = False

        for line in self._du_lines(args):
            size_text, tab, entry_path = line.partition("\t")
            if not tab:
                continue
            saw_any_row = True
            entry_path = entry_path.rstrip("\n")

            if not dirs_only:
                parent = entry_path.rpartition("/")[0]
                if parent:
                    known_dirs.add(parent)

            if entry_path == target:
                continue

            if filter_depth:
                depth = len(_split_segments(entry_path)) - root_segments
                if depth > filter_depth:
                    continue

            # Cheapest discriminators first: the name filters are pure
            # string work, while is_folder can still cost a stat().
            if name_filter or name_exclude:
                name = entry_path.rpartition("/")[2]
                if name_filter and not fnmatch(name, name_filter):
                    continue
                if name_exclude and fnmatch(name, name_exclude):
                    continue

            if dirs_only:
                is_folder = True
            elif entry_path in known_dirs:
                is_folder = True
            else:
                is_folder = os.path.isdir(entry_path)
            if (is_folder and not export_folders) or ((not is_folder) and not export_files):
                continue

            size = int(size_text) * scale
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

        if not saw_any_row:
            raise PermissionDeniedError(
                f"StorOps: du produced no output scanning '{target}' "
                "(permission denied on a subtree? re-run the whole command under sudo "
                "-- StorOps never self-elevates)."
            )

        return entries

    def _entry_size(self, dir_entry: os.DirEntry) -> int:
        """Size of one already-scandir'd file, in the same units this
        flavor's `du` reports: apparent bytes for GNU `-b`, allocated
        bytes (st_blocks is always 512-byte units, regardless of -k) for
        BSD. Mixing the two would make a directory listing's file rows
        incomparable with its folder rows.
        """
        try:
            stat_result = dir_entry.stat(follow_symlinks=False)
        except OSError:
            return 0
        if self._du_flavor() == "gnu":
            return stat_result.st_size
        return stat_result.st_blocks * 512

    def _total_size(self, path: str, aliases: set[str]) -> int:
        """`du -s path`, except where a pruned alias lives inside `path`:
        then sum `path`'s non-alias children instead, so `du` is never
        handed the duplicate subtree in the first place. Correcting the
        total by subtracting the alias afterwards would give the same
        number but none of the speed -- du would still have walked it.

        The recursion only expands along branches that actually contain an
        alias (nine directories under `/` on macOS, all within four levels),
        so in practice this is one `du -s` per child plus a shallow detour.
        """
        prefix = path.rstrip("/") + "/"
        if not any(alias.startswith(prefix) for alias in aliases):
            sized = self.path_size(path)
            return sized.size_bytes if sized else 0

        total = 0
        try:
            with os.scandir(path) as it:
                for child in it:
                    if child.path in aliases:
                        continue
                    try:
                        is_dir = child.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    total += self._total_size(child.path, aliases) if is_dir else self._entry_size(child)
        except OSError:
            return 0
        return total

    def _depth_one_entries(self, target: str, *, include_files: bool) -> list[Entry] | None:
        """Size each of `target`'s immediate children independently, or
        None to tell top_entries() to fall back to a single `du` run.

        Two things this buys over `du -d 1 <target>`, which is otherwise
        exactly equivalent:

        * Concurrency. Each child is its own `du -s` process, so the walk
          is no longer serialized through one single-threaded `du`;
          measured 99.9s -> 66s on a real macOS home directory.
        * A place to prune volume aliases. `du` has no way to be told "you
          have already counted that directory under another name", and on
          macOS it has to be told -- see _alias_scan_is_warranted().

        Falls back (returns None) when the target is not a directory or has
        so many children that per-child process spawns would cost more than
        the concurrency returns.

        Known trade-off: one `du` run counts a hardlinked file once no
        matter how many of its links it meets, and N independent runs
        cannot. Two top-level children sharing hardlinked content therefore
        each count it in full here. Checked against `du -d 1` on a real
        macOS home directory, the top-15 ordering was identical and every
        size matched to within the churn of the live caches being written
        during the runs -- cross-child hardlinks are rare enough in
        practice to be worth the ~1.5x, and are the reason this is a
        depth-1-only path rather than the default everywhere.
        """
        if not os.path.isdir(target):
            return None
        try:
            with os.scandir(target) as it:
                children = list(it)
        except OSError:
            return None

        directories: list[str] = []
        files: list[Entry] = []
        for child in children:
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                directories.append(child.path)
            elif include_files:
                size = self._entry_size(child)
                files.append(
                    Entry(
                        full_name=child.path,
                        is_folder=False,
                        size_bytes=size,
                        allocated_bytes=size,
                        modified=None,
                        file_count=None,
                        folder_count=None,
                    )
                )

        if len(directories) > _MAX_PARALLEL_CHILDREN:
            return None

        aliases = _alias_paths(target) if _alias_scan_is_warranted(target) else set()
        directories = [d for d in directories if d not in aliases]

        sizes: list[int] = []
        if directories:
            workers = min(_DU_CONCURRENCY, len(directories))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                sizes = list(pool.map(lambda d: self._total_size(d, aliases), directories))

        entries = files
        for directory, size in zip(directories, sizes):
            entries.append(
                Entry(
                    full_name=directory,
                    is_folder=True,
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
        entries: list[Entry] | None = None
        if max_depth == 1:
            entries = self._depth_one_entries(resolve_path(path), include_files=include_files)
        if entries is None:
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
        """Aggregate size for `path` itself via `du -s` (summary: one
        total for exactly this target, no per-file enumeration) -- NOT by
        scanning `path`'s parent directory with `-a` and searching for
        `path` in that listing, which this used to do. That meant `du`
        itself walked every unrelated sibling on disk too whenever the
        parent happened to be large, for no benefit (see
        platform/windows/scan.py's WindowsNativeBackend.path_size(),
        which had the identical anti-pattern and was measured costing
        ~76s/~2.7M stat() calls for a single storops cleanup plan run on
        a real machine, walking most of a drive to size two directories).
        """
        target = resolve_path(path)
        if not os.path.exists(target):
            return None

        flavor = self._du_flavor()
        args = ["du", "-s", "-b" if flavor == "gnu" else "-k", "--", target]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0 and not proc.stdout:
            return None

        line = next((l for l in proc.stdout.splitlines() if l), None)
        if not line:
            return None
        parts = line.split("\t", 1)
        if len(parts) < 2:
            return None

        size = int(parts[0])
        if flavor != "gnu":
            size *= 1024
        return Entry(
            full_name=target,
            is_folder=os.path.isdir(target),
            size_bytes=size,
            allocated_bytes=size,
            modified=None,
            file_count=None,
            folder_count=None,
        )

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
