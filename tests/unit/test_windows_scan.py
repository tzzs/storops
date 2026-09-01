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

    def test_path_size_finds_entry_via_parent_scan(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        target = root / "child"
        target.mkdir()
        (target / "f.bin").write_bytes(b"x" * 7)

        backend = WindowsNativeBackend()
        entry = backend.path_size(str(target))
        assert entry is not None
        assert entry.full_name == str(target)
        assert entry.size_bytes == 7

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
    def test_uses_wiztree_when_found(self, monkeypatch):
        from storops.platform.backends import wiztree as wiztree_mod

        monkeypatch.setattr(wiztree_mod, "find_wiztree", lambda: "C:\\WizTree64.exe")
        backend = scan_mod.get_windows_scan_backend()
        assert backend.name == "WizTree"

    def test_falls_back_to_native_when_wiztree_not_found(self, monkeypatch):
        from storops.platform.backends import wiztree as wiztree_mod

        monkeypatch.setattr(wiztree_mod, "find_wiztree", lambda: None)
        backend = scan_mod.get_windows_scan_backend()
        assert backend.name == "WindowsNative"
