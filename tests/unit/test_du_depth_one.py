"""Unit tests for DuBackend's depth-1 fast path and its volume-alias
pruning (src/storops/platform/backends/du.py).

The macOS topology these exist for: `/` is the sealed System volume, the
writable Data volume is mounted at /System/Volumes/Data, and firmlinks
project the Data volume's directories into `/`. `/Users` and
`/System/Volumes/Data/Users` are therefore the same directory -- same
st_dev, same st_ino, neither a symlink -- so `du /` walks the user's home
twice. Measured on a real Mac: `storops scan /` reported /System at 95.0G
before pruning and 72.9G after, with the Data volume's own contribution
dropping from du's 309G to its actual 3.5G.
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only backend")

from storops.platform.backends import du as du_module
from storops.platform.backends.du import (
    DuBackend,
    _alias_pre_pass_descends_into,
    _alias_scan_is_warranted,
)


# --- Where the pre-pass runs at all ----------------------------------------


@pytest.mark.parametrize(
    "target,expected",
    [
        ("/", True),
        ("/System/Volumes/Data", True),
        ("/Volumes/External", True),
        ("/Users/someone", False),
        ("/Users/someone/Library/Caches", False),
        ("/tmp", False),
    ],
)
def test_alias_scan_only_runs_at_volume_roots_on_darwin(monkeypatch, target, expected):
    monkeypatch.setattr(du_module._platform, "system", lambda: "Darwin")
    assert _alias_scan_is_warranted(target) is expected


def test_alias_scan_never_runs_off_darwin(monkeypatch):
    for system in ("Linux", "Windows"):
        monkeypatch.setattr(du_module._platform, "system", lambda: system)
        assert _alias_scan_is_warranted("/") is False


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", True),  # ancestor of /System/Volumes -- must be walked through
        ("/System", True),
        ("/System/Volumes", True),  # the boundary itself, not just below it
        ("/System/Volumes/Data", True),
        ("/System/Volumes/Data/System/Library", True),
        ("/Users", False),  # by construction never holds a firmlink
        ("/System/Applications", False),
    ],
)
def test_pre_pass_stays_on_the_branch_that_can_hold_firmlinks(path, expected):
    assert _alias_pre_pass_descends_into(path) is expected


# --- Pruning arithmetic -----------------------------------------------------


class _RecordingBackend(DuBackend):
    """DuBackend whose `du -s` is replaced by a directory-size lookup, so
    the pruning logic can be tested without depending on real firmlinks
    (which only exist on a real macOS volume group)."""

    def __init__(self, sizes: dict[str, int]):
        super().__init__()
        self._sizes = sizes
        self.sized: list[str] = []

    def path_size(self, path, *, admin=False):
        from storops.core.models import Entry

        self.sized.append(path)
        size = self._sizes.get(path, 0)
        return Entry(
            full_name=path, is_folder=True, size_bytes=size, allocated_bytes=size,
            modified=None, file_count=None, folder_count=None,
        )


def test_total_size_without_aliases_is_a_single_du_call(tmp_path):
    branch = tmp_path / "branch"
    branch.mkdir()
    backend = _RecordingBackend({str(branch): 4096})

    assert backend._total_size(str(branch), set()) == 4096
    assert backend.sized == [str(branch)]


def test_total_size_never_hands_du_a_pruned_alias(tmp_path):
    """The whole point of pruning rather than subtracting afterwards: the
    duplicate subtree must never be walked, or the correction costs the
    same time it was meant to save."""
    branch = tmp_path / "branch"
    keep = branch / "keep"
    alias = branch / "alias"
    keep.mkdir(parents=True)
    alias.mkdir()
    backend = _RecordingBackend({str(keep): 1000, str(alias): 999_999})

    total = backend._total_size(str(branch), {str(alias)})

    assert total == 1000
    assert str(alias) not in backend.sized


def test_total_size_recurses_only_along_branches_holding_an_alias(tmp_path):
    branch = tmp_path / "branch"
    clean = branch / "clean"
    dirty = branch / "dirty"
    alias = dirty / "alias"
    clean.mkdir(parents=True)
    alias.mkdir(parents=True)
    backend = _RecordingBackend({str(clean): 10, str(dirty): 20, str(alias): 30})

    total = backend._total_size(str(branch), {str(alias)})

    # `clean` was sized in one `du -s` without being descended into; only
    # `dirty` (which contains the alias) was opened up.
    assert str(clean) in backend.sized
    assert str(dirty) not in backend.sized
    assert total == 10


# --- Fast path vs. the plain `du` path must agree ---------------------------


def _tree(tmp_path):
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "a.bin").write_bytes(b"x" * 20_000)
    (tmp_path / "small").mkdir()
    (tmp_path / "small" / "b.bin").write_bytes(b"y" * 1_000)
    (tmp_path / "loose.bin").write_bytes(b"z" * 5_000)
    return tmp_path


def test_depth_one_fast_path_matches_the_du_scan_it_replaces(tmp_path):
    root = _tree(tmp_path)
    backend = DuBackend()

    fast = backend.top_entries(str(root), top=10, max_depth=1, include_files=False)
    slow = backend.scan(str(root), export_folders=True, export_files=False, max_depth=1)
    slow.sort(key=lambda e: e.size_bytes, reverse=True)

    assert [e.full_name for e in fast] == [e.full_name for e in slow]
    assert all(e.is_folder for e in fast)


def test_depth_one_fast_path_includes_files_when_asked(tmp_path):
    root = _tree(tmp_path)
    backend = DuBackend()

    entries = backend.top_entries(str(root), top=10, max_depth=1, include_files=True)

    by_path = {e.full_name: e for e in entries}
    assert by_path[str(root / "loose.bin")].is_folder is False
    assert by_path[str(root / "big")].is_folder is True


def test_wide_directories_fall_back_to_a_single_du_run(tmp_path, monkeypatch):
    """One `du -s` per child stops paying for itself once the process
    spawns outnumber the work; past the threshold top_entries() must go
    back to a single `du` over the whole target."""
    for index in range(5):
        (tmp_path / f"d{index}").mkdir()
    monkeypatch.setattr(du_module, "_MAX_PARALLEL_CHILDREN", 2)
    backend = DuBackend()

    assert backend._depth_one_entries(str(tmp_path), include_files=False) is None
    # ...and top_entries still returns the same answer via the fallback.
    assert len(backend.top_entries(str(tmp_path), top=10, max_depth=1)) == 5


def test_depth_one_fast_path_declines_a_non_directory(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"x" * 10)
    backend = DuBackend()

    assert backend._depth_one_entries(str(target), include_files=True) is None
