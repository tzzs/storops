"""Unit tests for storops.platform.windows.scan.

WindowsNativeBackend is built entirely on os.scandir()/os.stat(), which
work the same on Linux as on Windows, so these tests run for real against
tmp_path trees on this (Linux) sandbox -- this is fully verified, not
"written but unexecuted" like the WizTree/robocopy/mklink subprocess
call sites. get_windows_scan_backend()'s dispatch logic and
WindowsCapacityProvider are also covered.
"""
from __future__ import annotations

from storops.platform.windows import scan as scan_mod
from storops.platform.windows.scan import WindowsCapacityProvider, WindowsNativeBackend


class TestWindowsNativeBackendScan:
    def test_scan_computes_sizes_recursively(self, tmp_path):
        root = tmp_path / "root"
        (root / "sub").mkdir(parents=True)
        (root / "top.txt").write_bytes(b"12345")
        (root / "sub" / "nested.txt").write_bytes(b"1234567890")

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, export_files=True, max_depth=0)

        by_name = {e.full_name: e for e in entries}
        sub_entry = by_name[str(root / "sub")]
        assert sub_entry.is_folder is True
        assert sub_entry.size_bytes == 10
        assert sub_entry.file_count == 1
        assert sub_entry.folder_count == 0

        top_entry = by_name[str(root / "top.txt")]
        assert top_entry.is_folder is False
        assert top_entry.size_bytes == 5

        # max_depth=0 means unlimited (matching WizTree's /exportmaxdepth=0
        # convention), so nested.txt is listed too -- see the next test for
        # what happens with a finite max_depth.
        nested_entry = by_name[str(root / "sub" / "nested.txt")]
        assert nested_entry.size_bytes == 10

    def test_max_depth_limits_listing_but_not_aggregation(self, tmp_path):
        root = tmp_path / "root"
        (root / "a" / "b").mkdir(parents=True)
        (root / "a" / "b" / "deep.txt").write_bytes(b"1234")

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, max_depth=1)
        names = [e.full_name for e in entries]

        assert str(root / "a") in names
        assert str(root / "a" / "b") not in names

        a_entry = next(e for e in entries if e.full_name == str(root / "a"))
        assert a_entry.size_bytes == 4  # aggregated even though listing stopped at depth 1

    def test_export_files_false_omits_file_rows(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "f.txt").write_bytes(b"x")

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, export_files=False)
        assert entries == []

    def test_name_filter_and_exclude(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        (root / "keep.log").write_bytes(b"x")
        (root / "skip.log").write_bytes(b"xx")
        (root / "other.txt").write_bytes(b"xxx")

        backend = WindowsNativeBackend()
        entries = backend.scan(
            str(root),
            export_folders=False,
            export_files=True,
            name_filter="*.log",
            name_exclude="skip.*",
        )
        names = {e.full_name for e in entries}
        assert names == {str(root / "keep.log")}

    def test_permission_error_collected_as_warning_not_raised(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        locked = root / "locked"
        locked.mkdir(parents=True)
        (root / "ok.txt").write_bytes(b"x")

        real_scandir = scan_mod.os.scandir

        def fake_scandir(path):
            if str(path) == str(locked):
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(scan_mod.os, "scandir", fake_scandir)

        backend = WindowsNativeBackend()
        # Must not raise -- a single unreadable subtree cannot abort the walk.
        entries = backend.scan(str(root), export_folders=True, export_files=True)

        warnings = backend.take_warnings()
        assert len(warnings) == 1
        assert warnings[0].code == "permission_denied"
        assert warnings[0].path == str(locked)
        # The readable sibling file is still reported.
        assert any(e.full_name == str(root / "ok.txt") for e in entries)

    def test_root_level_directories_are_aggregated_correctly(self, tmp_path):
        # Several immediate root subdirectories plus a root-level file --
        # scan() splits exactly this shape (root's immediate children)
        # across a thread pool (see _walk_root() in platform/windows/scan.py),
        # so this exercises the real parallel-merge path, not just a single
        # sequential _walk().
        root = tmp_path / "root"
        (root / "a" / "sub").mkdir(parents=True)
        (root / "b").mkdir(parents=True)
        (root / "c").mkdir(parents=True)
        (root / "root_file.txt").write_bytes(b"x" * 5)
        (root / "a" / "f1.bin").write_bytes(b"1" * 10)
        (root / "a" / "sub" / "f2.bin").write_bytes(b"2" * 20)
        (root / "b" / "f3.bin").write_bytes(b"3" * 30)
        # "c" stays empty -- an immediate subdirectory with nothing inside
        # must still show up with zeroed aggregates, not be dropped.

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, export_files=True, max_depth=0)
        by_name = {e.full_name: e for e in entries}

        a = by_name[str(root / "a")]
        assert a.size_bytes == 30  # f1.bin + sub/f2.bin
        assert a.file_count == 2
        assert a.folder_count == 1

        assert by_name[str(root / "a" / "sub")].size_bytes == 20
        assert by_name[str(root / "b")].size_bytes == 30
        c = by_name[str(root / "c")]
        assert c.size_bytes == 0
        assert c.file_count == 0
        assert by_name[str(root / "root_file.txt")].size_bytes == 5

    def test_more_root_subdirectories_than_thread_pool_workers(self, tmp_path):
        # _walk_root() uses a bounded (8-worker) pool; more root-level
        # subdirectories than that must still all be scanned (the pool
        # queues the rest), not silently dropped or deadlocked.
        root = tmp_path / "root"
        for i in range(12):
            d = root / f"dir{i:02d}"
            d.mkdir(parents=True)
            (d / "f.bin").write_bytes(bytes([i]) * (i + 1))

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, export_files=True, max_depth=0)
        by_name = {e.full_name: e for e in entries}

        assert len([e for e in entries if e.is_folder]) == 12
        for i in range(12):
            assert by_name[str(root / f"dir{i:02d}")].size_bytes == i + 1

    def test_permission_error_in_one_root_subdirectory_does_not_affect_siblings(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        locked = root / "locked"
        locked.mkdir(parents=True)
        ok_dir = root / "ok_dir"
        ok_dir.mkdir(parents=True)
        (ok_dir / "f.bin").write_bytes(b"x" * 7)

        real_scandir = scan_mod.os.scandir

        def fake_scandir(path):
            if str(path) == str(locked):
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(scan_mod.os, "scandir", fake_scandir)

        backend = WindowsNativeBackend()
        entries = backend.scan(str(root), export_folders=True, export_files=True, max_depth=0)

        warnings = backend.take_warnings()
        assert len(warnings) == 1
        assert warnings[0].path == str(locked)
        by_name = {e.full_name: e for e in entries}
        assert by_name[str(ok_dir)].size_bytes == 7
        assert by_name[str(ok_dir / "f.bin")].size_bytes == 7

    def test_parallel_root_split_matches_plain_sequential_walk(self, tmp_path):
        # _walk_root() (the parallel entry point WindowsNativeBackend.scan()
        # uses) must produce the exact same entries -- as a set, since raw
        # scandir() order is not itself a guarantee -- as calling the
        # plain sequential _walk() directly, for a tree wide/deep enough to
        # actually engage the thread pool across multiple root subdirectories.
        root = tmp_path / "root"
        for i in range(10):
            d = root / f"d{i}"
            (d / "nested").mkdir(parents=True)
            (d / f"f{i}.bin").write_bytes(b"x" * (i + 1))
            (d / "nested" / "deep.bin").write_bytes(b"y" * (i + 2))
        (root / "top.txt").write_bytes(b"z" * 3)

        def entry_key(e):
            return (e.full_name, e.is_folder, e.size_bytes, e.allocated_bytes, e.file_count, e.folder_count)

        sequential_out: list = []
        scan_mod._walk(
            str(root), depth=0, max_depth=0, export_folders=True, export_files=True,
            name_filter=None, name_exclude=None, warnings=[], out=sequential_out,
        )

        backend = WindowsNativeBackend()
        parallel_out = backend.scan(str(root), export_folders=True, export_files=True, max_depth=0)

        assert {entry_key(e) for e in parallel_out} == {entry_key(e) for e in sequential_out}
        assert len(parallel_out) == len(sequential_out)

    def test_corrupt_mtime_collected_as_warning_not_raised(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        bad = root / "bad.bin"
        bad.write_bytes(b"x")
        (root / "ok.txt").write_bytes(b"y")
        # A distinctive, exactly-representable mtime (epoch 0) so the fake
        # below can single out this one file's timestamp without touching
        # its sibling's real (current-time) mtime.
        scan_mod.os.utime(bad, (0, 0))

        real_datetime = scan_mod.datetime

        class _FakeDatetime:
            @staticmethod
            def fromtimestamp(ts, *args, **kwargs):
                if ts == 0:
                    raise OSError("Invalid argument")
                return real_datetime.fromtimestamp(ts, *args, **kwargs)

        monkeypatch.setattr(scan_mod, "datetime", _FakeDatetime)

        backend = WindowsNativeBackend()
        # Must not raise -- a single file with a corrupt/out-of-range mtime
        # cannot abort the whole walk (same principle as the permission-error
        # case above).
        entries = backend.scan(str(root), export_folders=True, export_files=True)

        warnings = backend.take_warnings()
        assert len(warnings) == 1
        assert warnings[0].code == "scan_error"
        assert warnings[0].path == str(bad)

        by_name = {e.full_name: e for e in entries}
        assert by_name[str(bad)].modified is None
        # The sibling file with a normal mtime is unaffected.
        assert by_name[str(root / "ok.txt")].modified is not None

    def test_take_warnings_resets_between_calls(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        locked = root / "locked"
        locked.mkdir(parents=True)
        real_scandir = scan_mod.os.scandir

        def fake_scandir(path):
            if str(path) == str(locked):
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(scan_mod.os, "scandir", fake_scandir)
        backend = WindowsNativeBackend()
        backend.scan(str(root))
        assert len(backend.take_warnings()) == 1
        # A second call to take_warnings() without a new scan() returns empty.
        assert backend.take_warnings() == []

    def test_top_entries_excludes_root_sorts_and_limits(self, tmp_path):
        root = tmp_path / "root"
        (root / "big").mkdir(parents=True)
        (root / "small").mkdir(parents=True)
        (root / "big" / "f.bin").write_bytes(b"x" * 100)
        (root / "small" / "f.bin").write_bytes(b"x" * 10)

        backend = WindowsNativeBackend()
        top = backend.top_entries(str(root), top=1)
        assert len(top) == 1
        assert top[0].full_name == str(root / "big")

    def test_path_size_computes_target_aggregate(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        target = root / "child"
        (target / "nested").mkdir(parents=True)
        (target / "f.bin").write_bytes(b"x" * 7)
        (target / "nested" / "g.bin").write_bytes(b"y" * 3)

        backend = WindowsNativeBackend()
        entry = backend.path_size(str(target))
        assert entry is not None
        assert entry.full_name == str(target)
        assert entry.size_bytes == 10
        assert entry.file_count == 2
        assert entry.folder_count == 1

    def test_path_size_does_not_touch_sibling_directories(self, tmp_path, monkeypatch):
        # Regression test: path_size() used to scan `target`'s *parent*
        # and search for `target` in that listing -- meaning a target
        # whose parent has other, unrelated (and possibly huge) siblings
        # paid for walking all of them too. It must now only ever touch
        # `target`'s own subtree.
        root = tmp_path / "root"
        target = root / "small"
        target.mkdir(parents=True)
        (target / "f.bin").write_bytes(b"x" * 5)
        sibling = root / "huge_sibling"
        sibling.mkdir()
        (sibling / "big.bin").write_bytes(b"y" * 1000)

        import storops.platform.windows.scan as scan_mod

        real_scandir = scan_mod.os.scandir

        def guarded_scandir(path):
            if str(path) == str(sibling) or str(path) == str(root):
                raise AssertionError(f"path_size() must not scan {path!r} -- only the target itself")
            return real_scandir(path)

        monkeypatch.setattr(scan_mod.os, "scandir", guarded_scandir)

        backend = WindowsNativeBackend()
        entry = backend.path_size(str(target))
        assert entry is not None
        assert entry.size_bytes == 5

    def test_path_size_on_a_file_stats_it_directly(self, tmp_path):
        f = tmp_path / "solo.bin"
        f.write_bytes(b"x" * 42)

        backend = WindowsNativeBackend()
        entry = backend.path_size(str(f))
        assert entry is not None
        assert entry.is_folder is False
        assert entry.size_bytes == 42

    def test_path_size_returns_none_for_missing_path(self, tmp_path):
        backend = WindowsNativeBackend()
        assert backend.path_size(str(tmp_path / "does-not-exist")) is None

    def test_advice_mentions_wiztree(self):
        backend = WindowsNativeBackend()
        advice = backend.advice()
        assert advice is not None
        assert "WizTree" in advice

    def test_name(self):
        assert WindowsNativeBackend().name == "WindowsNative"


class TestWindowsCapacityProvider:
    def test_free_space_matches_shutil_disk_usage(self, tmp_path):
        import shutil

        expected = shutil.disk_usage(str(tmp_path))
        provider = WindowsCapacityProvider()
        capacity = provider.free_space(str(tmp_path))

        assert capacity.total_bytes == expected.total
        assert capacity.free_bytes == expected.free
        assert capacity.used_bytes == expected.used
        assert capacity.volume_name is None
        assert capacity.file_system is None


class TestGetWindowsScanBackend:
    def test_wraps_in_adaptive_backend_when_wiztree_found(self, monkeypatch):
        from storops.platform.backends import wiztree as wiztree_mod

        monkeypatch.setattr(wiztree_mod, "find_wiztree", lambda: "C:\\WizTree64.exe")
        backend = scan_mod.get_windows_scan_backend()
        assert isinstance(backend, scan_mod._AdaptiveWindowsBackend)

    def test_falls_back_to_plain_native_when_wiztree_not_found(self, monkeypatch):
        # No WizTree at all -- no adaptive wrapper needed, since there is
        # no choice to make.
        from storops.platform.backends import wiztree as wiztree_mod

        monkeypatch.setattr(wiztree_mod, "find_wiztree", lambda: None)
        backend = scan_mod.get_windows_scan_backend()
        assert type(backend) is WindowsNativeBackend
        assert backend.name == "WindowsNative"


class _FakeBackend:
    """Minimal ScanBackend stand-in for testing _AdaptiveWindowsBackend's
    own routing logic in isolation from either real backend."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple] = []

    def scan(self, path, **kwargs):
        self.calls.append(("scan", path, kwargs))
        return [f"scan:{self.name}"]

    def top_entries(self, path, **kwargs):
        self.calls.append(("top_entries", path, kwargs))
        return [f"top_entries:{self.name}"]

    def path_size(self, path, **kwargs):
        self.calls.append(("path_size", path, kwargs))
        return f"path_size:{self.name}"

    def advice(self):
        return f"advice:{self.name}"

    def take_warnings(self):
        return [f"warning:{self.name}"]


class TestAdaptiveWindowsBackend:
    """_AdaptiveWindowsBackend routes each call to WizTree only for a
    whole-volume target on an elevated process; WindowsNativeBackend
    otherwise -- see its docstring in platform/windows/scan.py for the
    live measurements this rule is based on. is_admin() is monkeypatched
    throughout rather than relying on the real ambient elevation status
    of whatever machine runs these tests.
    """

    def _make(self, monkeypatch, *, admin: bool):
        wiztree = _FakeBackend("WizTree")
        native = _FakeBackend("WindowsNative")
        monkeypatch.setattr(scan_mod, "is_admin", lambda: admin)
        return scan_mod._AdaptiveWindowsBackend(wiztree, native), wiztree, native

    def test_volume_root_and_elevated_uses_wiztree(self, monkeypatch):
        backend, wiztree, native = self._make(monkeypatch, admin=True)
        result = backend.scan("C:\\", export_files=True)
        assert result == ["scan:WizTree"]
        assert wiztree.calls == [("scan", "C:\\", {"export_folders": True, "export_files": True, "max_depth": 0, "name_filter": None, "name_exclude": None, "admin": False})]
        assert native.calls == []
        assert backend.name == "WizTree"

    def test_volume_root_but_not_elevated_uses_native(self, monkeypatch):
        backend, wiztree, native = self._make(monkeypatch, admin=False)
        backend.scan("C:\\")
        assert wiztree.calls == []
        assert len(native.calls) == 1
        assert backend.name == "WindowsNative"

    def test_subdirectory_even_when_elevated_uses_native(self, monkeypatch):
        backend, wiztree, native = self._make(monkeypatch, admin=True)
        backend.scan("C:\\Users\\test")
        assert wiztree.calls == []
        assert len(native.calls) == 1
        assert backend.name == "WindowsNative"

    def test_top_entries_and_path_size_route_the_same_way(self, monkeypatch):
        backend, wiztree, native = self._make(monkeypatch, admin=True)
        backend.top_entries("C:\\", top=5)
        backend.path_size("C:\\")
        assert len(wiztree.calls) == 2
        assert native.calls == []

    def test_take_warnings_delegates_to_whichever_backend_was_last_used(self, monkeypatch):
        backend, wiztree, native = self._make(monkeypatch, admin=True)
        backend.scan("C:\\Users\\test")  # routes to native
        assert backend.take_warnings() == ["warning:WindowsNative"]

        backend.scan("C:\\")  # routes to wiztree
        assert backend.take_warnings() == ["warning:WizTree"]

    def test_advice_is_none_when_wiztree_was_actually_used(self, monkeypatch):
        backend, _, _ = self._make(monkeypatch, admin=True)
        backend.scan("C:\\")
        assert backend.advice() is None

    def test_advice_explains_the_skip_when_wiztree_available_but_not_used(self, monkeypatch):
        backend, _, _ = self._make(monkeypatch, admin=True)
        backend.scan("C:\\Users\\test")
        advice = backend.advice()
        assert advice is not None
        assert "WizTree" in advice

    def test_advice_before_any_call_defaults_to_the_skip_explanation(self, monkeypatch):
        # _last starts pointed at native, matching "no call has proven a
        # volume-root+elevated scope yet".
        backend, _, _ = self._make(monkeypatch, admin=True)
        assert backend.advice() is not None


class TestIsVolumeRoot:
    def test_drive_root_with_trailing_backslash(self):
        assert scan_mod._is_volume_root("C:\\") is True

    # A bare drive letter with no trailing separator ("D:") is deliberately
    # not covered here: ntpath.isabs("D:") is False (it's a drive-relative
    # path, not an absolute one), so ntpath.abspath("D:") resolves against
    # that drive's own remembered current directory -- real Windows,
    # per-process, per-drive state that isn't reproducible from a test.
    # Confirmed by CI: this resolved to "D:\\" (a root) on one Windows
    # machine and to something else on windows-latest's runner, failing an
    # earlier version of this test that asserted True unconditionally.

    def test_subdirectory_is_not_a_root(self):
        assert scan_mod._is_volume_root("C:\\Users") is False
        assert scan_mod._is_volume_root("C:\\Users\\test\\AppData") is False

    def test_non_windows_shaped_path_is_not_a_root(self):
        assert scan_mod._is_volume_root("/home/user") is False
