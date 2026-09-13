"""Windows scan backends + capacity provider.

WizTree (NTFS MFT direct-read) is WindowsNativeBackend's higher-tier
sibling on Windows -- see storops.platform.backends.wiztree. This module
provides:

- `get_windows_scan_backend()`: the factory storops.platform.base.
  get_scan_backend() calls on Windows. Locates WizTree if possible, but
  only ever actually routes a given call to it for a whole-volume target
  on an elevated process -- see `_AdaptiveWindowsBackend` below for why;
  everything else uses `WindowsNativeBackend`, including every call when
  WizTree cannot be located at all.
- `WindowsNativeBackend`: a NET-NEW capability (the old PowerShell v1 tool
  had no fallback at all -- no WizTree meant the tool was simply unusable
  on Windows, see docs/plans/storops-v2-cross-platform-refactor.md
  §1.6.3/§2.13). Built on `os.scandir()`/`os.stat()` only -- zero
  third-party/external-binary dependency. Despite the module docstring
  history here previously assuming this was categorically slower than
  WizTree, live measurement (below) found the opposite for anything short
  of a full-volume scan.
- `WindowsCapacityProvider`: `shutil.disk_usage()` -- stdlib already wraps
  `GetDiskFreeSpaceExW`, no ctypes/pywin32 needed (§2.11a).
"""
from __future__ import annotations

import fnmatch
import os
import queue
import shutil
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from storops.core.models import Capacity, Entry, ScanWarning
from storops.core.paths import resolve_path
from storops.platform.base import is_admin

if TYPE_CHECKING:
    from storops.platform.base import ScanBackend


def _is_volume_root(path: str) -> bool:
    """True if `path` names a drive's root ("C:\\") rather than any
    subdirectory of it. See _AdaptiveWindowsBackend's docstring for why
    this is the one scope WizTree's CLI export was actually measured to
    win at.

    Uses `ntpath` explicitly rather than `os.path`/core.paths.resolve_path
    -- both are aliases for ntpath's own functions when genuinely running
    on Windows (the only platform this module is ever used on for real),
    but resolve to posixpath's very different drive-less semantics when
    this module's tests run on a non-Windows CI runner (as
    test_windows_scan.py's own module docstring notes they do), which
    would make a path like "C:\\" silently fail to parse as a root at all.
    """
    import ntpath

    resolved = ntpath.abspath(path)
    drive, tail = ntpath.splitdrive(resolved)
    return bool(drive) and tail in ("", "\\", "/")


class _AdaptiveWindowsBackend:
    """Routes each call to WizTree only for a whole-volume target on an
    elevated process; WindowsNativeBackend otherwise.

    This project's own earlier assumption -- WizTree's NTFS MFT direct
    read categorically beats a per-file stat() walk -- turned out to be
    wrong in the way that actually matters for this tool: WizTree's CLI
    export (the only interface storops uses -- it never drives the GUI)
    reads the *entire* volume's file record table regardless of how small
    the requested target is, so that fixed cost is only amortized when
    the target approaches the whole volume. Measured live on a real
    install (D:\\apps\\wiztree, WizTree 4.32), against this session's
    already-parallelized WindowsNativeBackend._walk_root():

        target scope           condition          WizTree vs native
        System32 (~2% of vol)  elevated, admin=1  4.2x SLOWER
        %LOCALAPPDATA% (~25%)  elevated, admin=1  2.5x SLOWER
        %LOCALAPPDATA% (~25%)  not elevated       2.1x SLOWER
        System32 (~2%)         not elevated        9.9x SLOWER
        whole C:\\ (100%)       elevated, admin=1  1.09x faster
        whole C:\\ (100%)       elevated, admin=0  1.36x faster

    Two more things fell out of that same data, both reflected above:
    admin=1 (the flag this backend still passes for a volume-root scan,
    since it's WizTree's documented way to request the MFT path and it
    was never slower on the elevated runs) bought no measurable speedup
    over admin=0 once the *process* was already elevated -- elevation of
    the calling process, not the flag, seems to be what actually matters,
    though WizTree is closed-source and this isn't confirmed from its
    side. And a non-elevated whole-volume scan was never measured (WizTree
    cannot self-elevate via UAC -- passing admin=1 from a non-elevated
    process just fails outright, confirmed live), so that combination
    conservatively still routes to native rather than assuming it would
    also win.
    """

    def __init__(self, wiztree: "ScanBackend", native: "WindowsNativeBackend") -> None:
        self._wiztree = wiztree
        self._native = native
        self._last: "ScanBackend" = native

    def _pick(self, path: str) -> "ScanBackend":
        # _is_volume_root() does its own ntpath-based resolution (see its
        # docstring) rather than the platform-generic resolve_path() --
        # deliberately not pre-resolving `path` here first.
        self._last = self._wiztree if (_is_volume_root(path) and is_admin()) else self._native
        return self._last

    @property
    def name(self) -> str:
        return self._last.name

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
        return self._pick(path).scan(
            path,
            export_folders=export_folders,
            export_files=export_files,
            max_depth=max_depth,
            name_filter=name_filter,
            name_exclude=name_exclude,
            admin=admin,
        )

    def top_entries(
        self,
        path: str,
        *,
        top: int = 20,
        max_depth: int = 1,
        admin: bool = False,
        include_files: bool = False,
    ) -> list[Entry]:
        return self._pick(path).top_entries(
            path, top=top, max_depth=max_depth, admin=admin, include_files=include_files
        )

    def path_size(self, path: str, *, admin: bool = False) -> Entry | None:
        return self._pick(path).path_size(path, admin=admin)

    def advice(self) -> str | None:
        if self._last is self._wiztree:
            return None
        if self._wiztree is not None:
            return (
                "Using the native scan for this target -- WizTree's CLI export only "
                "measurably wins for a whole-drive scan (e.g. 'C:\\') on an elevated "
                "process; for anything narrower it reads the entire volume's file "
                "table for no benefit, which loses to the native scan by 2x-10x, so "
                "it's skipped here even though WizTree is installed."
            )
        return self._native.advice()

    def take_warnings(self) -> list[ScanWarning]:
        return self._last.take_warnings()


def get_windows_scan_backend() -> "ScanBackend":
    """Factory consumed by storops.platform.base.get_scan_backend() on
    Windows. See _AdaptiveWindowsBackend's docstring for the (measured,
    not assumed) rule governing when a call actually gets routed to
    WizTree rather than WindowsNativeBackend.
    """
    from storops.platform.backends.wiztree import WizTreeBackend, find_wiztree

    exe = find_wiztree()
    if not exe:
        return WindowsNativeBackend()
    return _AdaptiveWindowsBackend(WizTreeBackend(exe), WindowsNativeBackend())


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


def _stat_file_child(
    child: os.DirEntry,
    *,
    export_files: bool,
    name_filter: str | None,
    name_exclude: str | None,
) -> tuple[tuple[int, int, datetime | None, Entry | None] | None, ScanWarning | None]:
    """Stat one non-directory scandir entry. Returns (info, warning):
    `info` is (size, allocated, mtime, export-entry-or-None) on success
    and None when the stat itself failed -- caller must skip this child
    entirely (no size/count contribution), matching a directory that
    raises PermissionError/OSError. `warning` describes any non-fatal
    problem hit along the way (a failed stat, an unreadable timestamp)
    and can be non-None even when `info` is not. Returned rather than
    appended to a caller-owned list so both _walk() and the parallel walk
    (see _walk_root()) can position the warning exactly where this child
    sits in their own output ordering. Shared by both so they apply
    identical file-handling logic.
    """
    try:
        st = child.stat(follow_symlinks=False)
    except PermissionError as exc:
        return None, ScanWarning(path=child.path, code="permission_denied", message=str(exc))
    except OSError as exc:
        return None, ScanWarning(path=child.path, code="scan_error", message=str(exc))

    allocated = _allocated_bytes(st)
    try:
        mtime = datetime.fromtimestamp(st.st_mtime)
    except (OSError, OverflowError, ValueError) as exc:
        mtime = None
        warning = ScanWarning(path=child.path, code="scan_error", message=str(exc))
    else:
        warning = None

    entry = None
    if export_files and _name_matches(child.name, name_filter, name_exclude):
        entry = Entry(
            full_name=child.path,
            is_folder=False,
            size_bytes=st.st_size,
            allocated_bytes=allocated,
            modified=mtime,
        )
    return (st.st_size, allocated, mtime, entry), warning


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
            info, warning = _stat_file_child(
                child,
                export_files=export_files,
                name_filter=name_filter,
                name_exclude=name_exclude,
            )
            if warning is not None:
                warnings.append(warning)
            if info is None:
                continue
            size, allocated, mtime, entry = info
            file_count += 1
            total_size += size
            total_allocated += allocated
            if mtime is not None and (latest_mtime is None or mtime > latest_mtime):
                latest_mtime = mtime
            if within_depth and entry is not None:
                out.append(entry)

    return total_size, total_allocated, file_count, folder_count, latest_mtime


_DEFAULT_PARALLEL_WORKERS = 8
_MAX_PARALLEL_WORKERS = 64
_WORKERS_ENV_VAR = "STOROPS_SCAN_WORKERS"
# Walk-bookkeeping locks, sharded by node identity: one global lock made
# every completion of the whole scan serialize on it (measured: an
# 8-worker warm scan ran SLOWER than 4 workers). 16 shards keep
# concurrent completions mostly off each other's cache lines while every
# node's fields are still mutated under exactly one lock (its own shard),
# and no site ever holds two shards at once, so there is no lock-order to
# deadlock on.
_LOCK_SHARDS = 16
_LOCK_SHARD_MASK = _LOCK_SHARDS - 1


def _resolve_max_workers() -> int:
    """Worker count for the parallel walk: $env:STOROPS_SCAN_WORKERS when
    set, else 8. A parseable value is clamped to 1..64 (a spinning-disk
    user can set 1-2 to avoid seek thrashing; more than 64 threads of
    directory stat-ing helps no filesystem); an unparsable value falls
    back to the default rather than erroring -- a typo in an env var must
    never turn every scan into a crash.
    """
    raw = os.environ.get(_WORKERS_ENV_VAR, "").strip()
    if raw:
        try:
            return min(max(int(raw), 1), _MAX_PARALLEL_WORKERS)
        except ValueError:
            pass
    return _DEFAULT_PARALLEL_WORKERS


class _WalkConfig:
    """The walk parameters every task of one scan shares, held once
    instead of copied into each per-directory node."""

    __slots__ = ("max_depth", "export_folders", "export_files", "name_filter", "name_exclude")

    def __init__(
        self,
        *,
        max_depth: int,
        export_folders: bool,
        export_files: bool,
        name_filter: str | None,
        name_exclude: str | None,
    ) -> None:
        self.max_depth = max_depth
        self.export_folders = export_folders
        self.export_files = export_files
        self.name_filter = name_filter
        self.name_exclude = name_exclude


class _DirNode:
    """One directory visited by the parallel walk (see _walk_root()).

    Pure bookkeeping -- the scandir/stat work happens in _process_node().
    `slots` records this directory's children in os.scandir() order; each
    child deposits its finalized rows/warnings/aggregates on itself, and
    the parent folds its slots in scandir order only once its whole
    subtree is done, which is what makes the parallel walk emit rows in
    exactly the sequential _walk()'s order no matter which thread finished
    what first.
    """

    __slots__ = (
        "path",
        "name",
        "depth",
        "parent",
        "slots",
        "pending",
        "discovery_done",
        "finished",
        "own_size_bytes",
        "own_allocated_bytes",
        "own_file_count",
        "own_mtime",
        "size_bytes",
        "allocated_bytes",
        "file_count",
        "folder_count",
        "mtime",
        "out_buffer",
        "warnings_buffer",
        "done_event",
    )

    def __init__(self, path: str, name: str, depth: int, parent: "_DirNode | None") -> None:
        self.path = path
        # Only ever read to decide whether this node's own Entry matches
        # name_filter -- meaningless for the root (depth 0 emits no row).
        self.name = name
        self.depth = depth
        self.parent = parent
        self.slots: list[tuple] = []
        # Subdirectories discovered but not yet finalized: set once to the
        # full count before the first one is enqueued, then decremented
        # under the walk lock -- see _process_node()/_try_finalize().
        self.pending = 0
        self.discovery_done = False
        self.finished = False
        # This directory's OWN contribution (direct files only), split out
        # from the folded totals so the discovery task can accumulate it
        # in plain locals exactly the way _walk() does, and _finalize_one()
        # never has to re-iterate the file slots for aggregates.
        self.own_size_bytes = 0
        self.own_allocated_bytes = 0
        self.own_file_count = 0
        self.own_mtime: datetime | None = None
        self.size_bytes = 0
        self.allocated_bytes = 0
        self.file_count = 0
        self.folder_count = 0
        self.mtime: datetime | None = None
        self.out_buffer: list[Entry] = []
        self.warnings_buffer: list[ScanWarning] = []
        # Set when the root node finalizes; only the root uses this.
        self.done_event: threading.Event | None = None


def _finalize_one(node: "_DirNode", config: _WalkConfig) -> None:
    """Lay `node`'s rows and warnings out in os.scandir() order, fold its
    finalized subdirectories' rows/aggregates in, and append node's own
    Entry exactly the way _walk() does (post-order: after its subtree's
    rows). Caller must hold the walk lock.

    Aggregates were already accumulated where they were cheap: direct
    files during discovery (task-local, exactly like _walk()'s locals --
    see _process_node()), subdirectories as they completed. This pass
    therefore only extends buffers (memcpy-fast) and re-derives nothing
    per file, which is what keeps the parallel machinery from showing up
    on a warm metadata cache. Mirrors _walk()'s behavior case for case: a
    subdirectory that could not be read still counts as one folder with
    zeroed aggregates, a file whose stat failed contributes only its
    warning, and every row is gated on max_depth/export flags/name
    filtering the same way.
    """
    total_size = node.own_size_bytes
    total_allocated = node.own_allocated_bytes
    file_count = node.own_file_count
    folder_count = 0  # dir children are counted per slot below (1 + their subtree)
    latest_mtime = node.own_mtime
    out: list[Entry] = []
    warnings_out = node.warnings_buffer  # a scandir-failure warning stays first
    # Gate for node's own Entry vs. gate for its FILE rows: the file rows
    # sit one level deeper (in _walk()'s terms, a file child is at
    # node.depth + 1 while node's own Entry is appended by its PARENT,
    # governed by node's depth). Keeping them distinct is what makes a
    # finite max_depth export exactly what the sequential walk exports.
    within_depth = config.max_depth == 0 or node.depth <= config.max_depth
    children_within_depth = config.max_depth == 0 or node.depth + 1 <= config.max_depth

    for slot in node.slots:
        kind = slot[0]
        if kind == "file":
            _, entry, warning = slot
            if warning is not None:
                warnings_out.append(warning)
            if children_within_depth and entry is not None:
                out.append(entry)
        elif kind == "dir":
            sub = slot[1]
            total_size += sub.size_bytes
            total_allocated += sub.allocated_bytes
            file_count += sub.file_count
            folder_count += 1 + sub.folder_count
            if sub.mtime is not None and (latest_mtime is None or sub.mtime > latest_mtime):
                latest_mtime = sub.mtime
            out.extend(sub.out_buffer)
            warnings_out.extend(sub.warnings_buffer)
        else:  # "warn": a child whose is_dir() check itself failed
            warnings_out.append(slot[1])
    node.slots.clear()  # children's buffers are folded in now; drop the tree early

    if (
        node.depth >= 1
        and within_depth
        and config.export_folders
        and _name_matches(node.name, config.name_filter, config.name_exclude)
    ):
        out.append(
            Entry(
                full_name=node.path,
                is_folder=True,
                size_bytes=total_size,
                allocated_bytes=total_allocated,
                modified=latest_mtime,
                file_count=file_count,
                folder_count=folder_count,
            )
        )

    node.size_bytes = total_size
    node.allocated_bytes = total_allocated
    node.file_count = file_count
    node.folder_count = folder_count
    node.mtime = latest_mtime
    node.out_buffer = out
    node.finished = True


def _try_finalize(node: "_DirNode", config: _WalkConfig, locks: list[threading.Lock]) -> None:
    """Finalize `node` if its discovery is complete and every subdirectory
    it enqueued has finalized, then cascade completion upward -- each
    ancestor that becomes ready finalizes too, all within this one call.

    A directory is ready exactly when `discovery_done` is True (its own
    task finished discovering children) and `pending` is 0 (every enqueued
    subdirectory has finalized). Both transitions are evaluated under the
    node's own shard lock, so each directory finalizes exactly once no
    matter which of the two sides gets there first. `locks` is the walk's
    shard list; a node's bookkeeping is always mutated under that node's
    own shard, and this function must be called WITHOUT holding any of
    them (it takes and releases shards internally, more than once).

    The lock covers only the readiness check and the `finished` claim --
    the potentially long row-buffer fold in _finalize_one() runs OUTSIDE
    it, on the one thread that claimed the node (exclusive by
    construction: a node becomes ready exactly once, and the claim flag
    is what makes that one transition owned by a single thread). Holding
    the lock across the folds serialized every completion of the whole
    scan onto one lock, which showed up as an 8-worker warm scan being
    measurably SLOWER than 4 workers; claim-then-fold keeps critical
    sections at counter-and-flag cost. For the same reason this function
    must never be called while the caller holds the walk lock.

    The cascade is crash-atomic: if folding a node's slots raises for any
    reason, a scan_error warning is recorded in its buffer and the
    cascade still continues -- every completion credits its parent, so
    the root event always fires and _walk_root() always returns. A
    degraded scan beats a scan that never returns.
    """
    claimed = False
    while True:
        if not claimed:
            with locks[id(node) & _LOCK_SHARD_MASK]:
                if node.finished or not node.discovery_done or node.pending > 0:
                    return
                node.finished = True  # claim: this thread alone will fold it
        try:
            _finalize_one(node, config)
        except Exception as exc:  # pragma: no cover - defensive
            node.warnings_buffer.append(
                ScanWarning(path=node.path, code="scan_error", message=f"internal scan error: {exc}")
            )
        parent = node.parent
        if parent is None:
            # The root is done: every descendant has folded into it.
            if node.done_event is not None:
                node.done_event.set()
            return
        with locks[id(parent) & _LOCK_SHARD_MASK]:
            parent.pending -= 1
            if parent.finished or parent.pending > 0 or not parent.discovery_done:
                return
            parent.finished = True  # claim parent's fold for this thread
        claimed = True
        node = parent


def _process_node(
    node: "_DirNode",
    config: _WalkConfig,
    work_queue: "queue.Queue[_DirNode]",
    locks: list[threading.Lock],
    children: "list[os.DirEntry] | None" = None,
) -> None:
    """One unit of parallel work: scandir `node` (or consume the
    pre-fetched `children` -- the walk root is scandir'ed once by
    _walk_root() to preserve its early-exit-on-unreadable-root behavior),
    stat its file children, hand its subdirectory children to the queue,
    then mark discovery complete and try to finalize it.

    Runs as a small work-stealing loop over an inline stack, with a
    width-aware dispatch per directory:

    - 2+ subdirectories: all of them go on the shared queue (real
      parallelism opportunity), and the worker steals one back only to
      keep itself busy;
    - exactly 1 subdirectory: the chain continues on the worker's own
      stack with no queue interaction at all -- a chain has no parallelism
      opportunity, and routing every directory through the queue measured
      at ~0.5s of put/get plus thread-handoff cost per 100k directories
      on a warm metadata cache (a ~25% regression on repeat scans);
    - 0 subdirectories (leaf): finalized immediately after discovery
      publishes -- one short shard acquisition to claim it, fold outside
      the lock.

    Output ordering is unaffected by any of this: rows fold structurally
    through slots, never by completion order (see _walk_root()).

    `node.pending` is set to the full subdirectory count before the first
    one is handed out, so a fast-finishing child can never observe a
    half-discovered parent as ready; `discovery_done` closes the other
    half of the readiness condition (see _try_finalize()). Direct-file
    aggregates accumulate in plain task locals exactly like _walk()'s and
    are published in the same lock acquisition as the enqueue, so
    _finalize_one() never re-iterates file slots for numbers.
    """
    stack: "list[tuple[_DirNode, list[os.DirEntry] | None]]" = [(node, children)]
    while stack:
        node, children = stack.pop()
        if children is None:
            try:
                children = list(os.scandir(node.path))
            except PermissionError as exc:
                node.warnings_buffer.append(
                    ScanWarning(path=node.path, code="permission_denied", message=str(exc))
                )
                children = []
            except OSError as exc:
                node.warnings_buffer.append(
                    ScanWarning(path=node.path, code="scan_error", message=str(exc))
                )
                children = []

        child_depth = node.depth + 1
        subs: list[_DirNode] = []
        own_size = 0
        own_allocated = 0
        own_files = 0
        own_mtime: datetime | None = None
        for child in children:
            try:
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError as exc:
                node.slots.append(
                    ("warn", ScanWarning(path=child.path, code="scan_error", message=str(exc)))
                )
                continue

            if is_dir:
                sub = _DirNode(child.path, child.name, child_depth, node)
                subs.append(sub)
                node.slots.append(("dir", sub))
            else:
                info, warning = _stat_file_child(
                    child,
                    export_files=config.export_files,
                    name_filter=config.name_filter,
                    name_exclude=config.name_exclude,
                )
                if info is None:
                    node.slots.append(("warn", warning))
                    continue
                size, allocated, mtime, entry = info
                own_files += 1
                own_size += size
                own_allocated += allocated
                if mtime is not None and (own_mtime is None or mtime > own_mtime):
                    own_mtime = mtime
                node.slots.append(("file", entry, warning))

        with locks[id(node) & _LOCK_SHARD_MASK]:
            node.own_size_bytes = own_size
            node.own_allocated_bytes = own_allocated
            node.own_file_count = own_files
            node.own_mtime = own_mtime
            node.pending = len(subs)
            node.discovery_done = True

        if not subs:
            # A leaf is ready the moment discovery closes -- finalize it
            # right here. _try_finalize() takes the walk lock itself for
            # the readiness claim; its fold runs lock-free (never call it
            # while holding the lock).
            _try_finalize(node, config, locks)

        if len(subs) > 1:
            for sub in subs:
                work_queue.put(sub)
            try:
                stolen = work_queue.get_nowait()
            except queue.Empty:
                stolen = None
            if stolen is not None:
                stack.append((stolen, None))
        elif len(subs) == 1:
            stack.append((subs[0], None))


def _worker_loop(
    work_queue: "queue.Queue[_DirNode]",
    config: _WalkConfig,
    locks: list[threading.Lock],
) -> None:
    """Pull directories off the queue until a None sentinel. Never blocks
    on another task's completion -- that is the whole design (see
    _walk_root()'s docstring), so this loop must stay this simple."""
    while True:
        node = work_queue.get()
        if node is None:
            return
        try:
            _process_node(node, config, work_queue, locks)
        except Exception as exc:  # pragma: no cover - defensive
            # A task that dies must never leave its ancestors waiting
            # forever: record what happened and close this node's
            # discovery (no further children will be enqueued from it),
            # then finalize whatever state it reached -- _try_finalize()'s
            # cascade is crash-atomic, so this always propagates
            # completion upward. Subdirectories already enqueued, if any,
            # are independent tasks that run to completion regardless.
            # The walk lock must not be held across _try_finalize().
            with locks[id(node) & _LOCK_SHARD_MASK]:
                if not node.finished:
                    node.warnings_buffer.append(
                        ScanWarning(
                            path=node.path,
                            code="scan_error",
                            message=f"internal scan error: {exc}",
                        )
                    )
                    node.discovery_done = True
            _try_finalize(node, config, locks)


def _walk_root(
    path: str,
    *,
    max_depth: int,
    export_folders: bool,
    export_files: bool,
    name_filter: str | None,
    name_exclude: str | None,
    warnings: list[ScanWarning],
    out: list[Entry],
    max_workers: int | None = None,
) -> tuple[int, int, int, int, datetime | None]:
    """Entry point for WindowsNativeBackend.scan()/path_size(): a
    work-queue parallel walk over `path`'s whole subtree.

    Shape: the scan root -- and then every subdirectory discovered under
    it -- is put on a shared work queue; each of `max_workers` threads
    repeatedly pops a directory, stats its file children, and enqueues its
    subdirectory children (see _process_node()). Parallelism is therefore
    NOT limited to the scan root's immediate subdirectories: this used to
    be a root-level-only ThreadPoolExecutor split, which left a scan
    dominated by one huge child (e.g. C:\\Users on a `C:\\` scan) running
    start-to-finish on a single thread and capped the speedup at that
    child's share of the tree.

    Deadlock-freedom by construction: a worker NEVER blocks on another
    task's completion. (The previous future-based shape had exactly that
    hazard: a bounded pool whose workers .result() on child futures while
    no free worker remains to run them.) Results flow the opposite way to
    work instead -- each finished directory folds itself into a slot on
    its parent and decrements the parent's outstanding-children counter
    under one lock, so completion propagates bottom-up with nobody
    waiting.

    Output equivalence with the sequential _walk(): rows and warnings come
    out in exactly _walk()'s DFS order despite running on many threads,
    because ordering is structural, not temporal -- every directory's
    children sit in `slots` in os.scandir() order, each child deposits its
    finished rows there, and a parent folds its slots (then appends its
    own Entry, matching _walk()'s post-order) only once its whole subtree
    is done. Aggregates fold the same way (integer adds and an mtime max
    are order-independent), so a parallel scan is bit-identical to the
    sequential one; TestWorkQueueWalk in test_windows_scan.py holds that
    line.

    GIL note: os.scandir()/DirEntry.stat() release the GIL for their
    underlying syscall, and on Windows each call already pays real
    per-call latency (filesystem-filter/AV interception) independent of
    CPU work, so a scan is mostly threads waiting on I/O -- exactly the
    case a thread pool helps with despite the GIL.

    `max_workers` defaults to $env:STOROPS_SCAN_WORKERS (see
    _resolve_max_workers()); 1 degenerates to the plain sequential _walk()
    with no threading overhead at all.

    Like _walk(): a directory this process cannot read is collected into
    `warnings` as a ScanWarning rather than aborting the walk (one
    unreadable subtree must never abort an entire scan), full recursion
    always happens so aggregate sizes stay correct, and only children
    within `max_depth` levels of the root become Entry rows (max_depth==0
    unlimited).
    """
    try:
        children = list(os.scandir(path))
    except PermissionError as exc:
        warnings.append(ScanWarning(path=path, code="permission_denied", message=str(exc)))
        return 0, 0, 0, 0, None
    except OSError as exc:
        warnings.append(ScanWarning(path=path, code="scan_error", message=str(exc)))
        return 0, 0, 0, 0, None

    if max_workers is None:
        max_workers = _resolve_max_workers()
    if max_workers <= 1:
        # No threading at all: identical output to the parallel path by
        # construction (the parallel walk's contract is to match _walk()
        # exactly), and the sequential walk skips all per-directory
        # bookkeeping overhead.
        return _walk(
            path,
            depth=0,
            max_depth=max_depth,
            export_folders=export_folders,
            export_files=export_files,
            name_filter=name_filter,
            name_exclude=name_exclude,
            warnings=warnings,
            out=out,
        )

    config = _WalkConfig(
        max_depth=max_depth,
        export_folders=export_folders,
        export_files=export_files,
        name_filter=name_filter,
        name_exclude=name_exclude,
    )
    locks = [threading.Lock() for _ in range(_LOCK_SHARDS)]
    work_queue: queue.Queue[_DirNode] = queue.Queue()
    root = _DirNode(path, "", 0, None)
    root.done_event = threading.Event()

    workers = [
        threading.Thread(
            target=_worker_loop,
            args=(work_queue, config, locks),
            name=f"storops-scan-{i}",
            daemon=True,
        )
        for i in range(max_workers)
    ]
    for worker in workers:
        worker.start()
    work_queue.put(root)
    root.done_event.wait()  # set only once every descendant has finalized
    for _ in workers:
        work_queue.put(None)
    for worker in workers:
        worker.join()

    out.extend(root.out_buffer)
    warnings.extend(root.warnings_buffer)
    return (
        root.size_bytes,
        root.allocated_bytes,
        root.file_count,
        root.folder_count,
        root.mtime,
    )


class WindowsNativeBackend:
    """Zero-dependency Windows scan fallback used when WizTree is not
    installed. Net-new capability vs. the PowerShell v1 tool -- see module
    docstring. A parallel `os.scandir()` work-queue walk over the whole
    scanned subtree (see _walk_root()); live measurement found it beats
    WizTree's CLI export for anything short of a whole-volume scan (see
    _AdaptiveWindowsBackend), so it is the default engine on Windows.
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
        _walk_root(
            target,
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
        """Aggregate size/count for `path` itself, computed by walking
        `path`'s own subtree directly (via _walk_root(), so this still
        gets the same root-level parallel split) -- NOT by scanning its
        parent directory and searching for `path` in that listing, which
        this used to do. That meant probing a directory whose *parent*
        happens to be huge (e.g. "C:\\Windows\\Temp", parent "C:\\Windows")
        walked every unrelated sibling too, real-world measured at ~2.7M
        stat() calls (~76s) for a single storops cleanup plan run -- most
        of a full drive's worth of work to size two small directories.
        """
        target = resolve_path(path)
        if not os.path.exists(target):
            return None

        self._warnings = []
        if not os.path.isdir(target):
            try:
                st = os.stat(target, follow_symlinks=False)
            except OSError as exc:
                self._warnings.append(ScanWarning(path=target, code="scan_error", message=str(exc)))
                return None
            try:
                mtime = datetime.fromtimestamp(st.st_mtime)
            except (OSError, OverflowError, ValueError):
                mtime = None
            return Entry(
                full_name=target,
                is_folder=False,
                size_bytes=st.st_size,
                allocated_bytes=_allocated_bytes(st),
                modified=mtime,
            )

        out: list[Entry] = []
        size, allocated, file_count, folder_count, mtime = _walk_root(
            target,
            max_depth=0,
            export_folders=False,
            export_files=False,
            name_filter=None,
            name_exclude=None,
            warnings=self._warnings,
            out=out,
        )
        return Entry(
            full_name=target,
            is_folder=True,
            size_bytes=size,
            allocated_bytes=allocated,
            modified=mtime,
            file_count=file_count,
            folder_count=folder_count,
        )

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
