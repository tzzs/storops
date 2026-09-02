"""Unit tests for storops.platform.backends.wiztree.

Originally written to be the highest-value tests possible without a real
Windows machine + WizTree install: locking down (a) the exact argument
list passed to subprocess.run (catches argument-order/typo bugs) and (b)
the CSV parser against hand-written fixtures, including one with a
non-English (Chinese) header row to prove the structural header-detection
trick works without matching English header text. Since verified live
against a real WizTree 4.32 install -- see the module docstring and
test_real_export_banner_line_and_extra_drive_columns below.
"""
from __future__ import annotations

import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

from storops.core.errors import StoropsError
from storops.platform.backends import wiztree as wiztree_mod


class TestFindWizTree:
    def test_env_var_pointing_at_file(self, tmp_path, monkeypatch):
        exe = tmp_path / "MyWizTree.exe"
        exe.write_text("")
        monkeypatch.setenv("STOROPS_WIZTREE_PATH", str(exe))
        assert wiztree_mod.find_wiztree() == str(exe)

    def test_env_var_pointing_at_directory(self, tmp_path, monkeypatch):
        (tmp_path / "WizTree64.exe").write_text("")
        monkeypatch.setenv("STOROPS_WIZTREE_PATH", str(tmp_path))
        assert wiztree_mod.find_wiztree() == str(tmp_path / "WizTree64.exe")

    def test_env_var_set_but_not_found_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STOROPS_WIZTREE_PATH", str(tmp_path / "nope"))
        assert wiztree_mod.find_wiztree() is None

    def test_falls_back_to_path(self, monkeypatch):
        monkeypatch.delenv("STOROPS_WIZTREE_PATH", raising=False)
        monkeypatch.setattr(
            wiztree_mod.shutil,
            "which",
            lambda name: "/usr/bin/WizTree64.exe" if name == "WizTree64.exe" else None,
        )
        assert wiztree_mod.find_wiztree() == "/usr/bin/WizTree64.exe"

    def test_falls_back_to_well_known_paths(self, tmp_path, monkeypatch):
        monkeypatch.delenv("STOROPS_WIZTREE_PATH", raising=False)
        monkeypatch.setattr(wiztree_mod.shutil, "which", lambda name: None)
        # Isolate this tier from whatever WizTree registry entry may or may
        # not genuinely exist on the machine running this test.
        monkeypatch.setattr(wiztree_mod, "_find_wiztree_via_registry", lambda: None)
        program_files = tmp_path / "ProgramFiles"
        (program_files / "WizTree").mkdir(parents=True)
        (program_files / "WizTree" / "WizTree.exe").write_text("")
        monkeypatch.setenv("ProgramFiles", str(program_files))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert wiztree_mod.find_wiztree() == str(program_files / "WizTree" / "WizTree.exe")

    def test_nothing_found_returns_none(self, monkeypatch):
        monkeypatch.delenv("STOROPS_WIZTREE_PATH", raising=False)
        monkeypatch.setattr(wiztree_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(wiztree_mod, "_find_wiztree_via_registry", lambda: None)
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert wiztree_mod.find_wiztree() is None

    def test_registry_tier_used_when_path_and_well_known_folders_miss(self, tmp_path, monkeypatch):
        # find_wiztree()'s priority order: env var > PATH > registry >
        # well-known folders. An install the installer put on a
        # non-conventional drive/folder (confirmed live: a real WizTree
        # install at "D:\apps\wiztree\", found by neither PATH nor any
        # %ProgramFiles%/%LOCALAPPDATA% guess) is exactly what step 3
        # exists for.
        monkeypatch.delenv("STOROPS_WIZTREE_PATH", raising=False)
        monkeypatch.setattr(wiztree_mod.shutil, "which", lambda name: None)
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        install_dir = tmp_path / "apps" / "wiztree"
        install_dir.mkdir(parents=True)
        (install_dir / "WizTree64.exe").write_text("")
        monkeypatch.setattr(wiztree_mod, "_find_wiztree_via_registry", lambda: str(install_dir / "WizTree64.exe"))

        assert wiztree_mod.find_wiztree() == str(install_dir / "WizTree64.exe")

    def test_path_tier_wins_over_registry(self, monkeypatch):
        monkeypatch.delenv("STOROPS_WIZTREE_PATH", raising=False)
        monkeypatch.setattr(wiztree_mod.shutil, "which", lambda name: "C:\\OnPath\\WizTree64.exe" if name == "WizTree64.exe" else None)
        monkeypatch.setattr(wiztree_mod, "_find_wiztree_via_registry", lambda: "D:\\FromRegistry\\WizTree64.exe")
        assert wiztree_mod.find_wiztree() == "C:\\OnPath\\WizTree64.exe"


class TestFindWizTreeViaRegistry:
    """_find_wiztree_via_registry()'s own logic, isolated from the real
    Windows registry via monkeypatched _read_uninstall_entries() so these
    run identically regardless of what is actually installed on the
    machine executing the tests -- and, via the autouse fixture below,
    regardless of which platform runs them: on a non-Windows CI runner
    `import winreg` itself raises (winreg doesn't exist there), which
    _find_wiztree_via_registry() treats as "nothing found" and returns
    None *before ever calling* _read_uninstall_entries(). Without a fake
    winreg to get past that import, every test here that expects None
    back would pass for the wrong reason (the import failing, not the
    mocked lookup logic being exercised) -- caught by actually running
    this suite on Linux, not just Windows.
    """

    @pytest.fixture(autouse=True)
    def _fake_winreg_importable(self, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "winreg",
            types.SimpleNamespace(HKEY_LOCAL_MACHINE=object(), HKEY_CURRENT_USER=object()),
        )

    def test_matches_display_name_case_insensitively_and_version_agnostic(self):
        entries = [
            ("Some Other App", "C:\\Other"),
            ("WizTree v4.32", "D:\\apps\\wiztree\\"),
            (None, None),
            ("", "C:\\Blank"),
        ]
        assert wiztree_mod._wiztree_install_locations(entries) == ["D:\\apps\\wiztree\\"]

    def test_entry_with_no_install_location_is_skipped(self):
        entries = [("WizTree v4.32", None)]
        assert wiztree_mod._wiztree_install_locations(entries) == []

    def test_checks_all_three_registry_roots(self, tmp_path, monkeypatch):
        # Three roots are consulted: HKLM native, HKLM WOW6432Node (32-bit
        # redirection), HKCU (a per-user, not "for all users", install).
        # A match found only in the last one checked must still work.
        install_dir = tmp_path / "wiztree"
        install_dir.mkdir()
        (install_dir / "WizTree.exe").write_text("")

        calls: list[tuple[object, str]] = []

        def fake_read(hive, subkey):
            calls.append((hive, subkey))
            if len(calls) == 3:
                return [("WizTree v4.32", str(install_dir))]
            return []

        monkeypatch.setattr(wiztree_mod, "_read_uninstall_entries", fake_read)
        assert wiztree_mod._find_wiztree_via_registry() == str(install_dir / "WizTree.exe")
        assert len(calls) == 3
        # The two HKLM lookups share one hive value and use the documented
        # native-then-WOW6432Node subkey order; HKCU uses a different hive.
        assert calls[0][0] == calls[1][0]
        assert calls[2][0] != calls[0][0]
        assert calls[0][1] == wiztree_mod._UNINSTALL_SUBKEYS[0]
        assert calls[1][1] == wiztree_mod._UNINSTALL_SUBKEYS[1]
        assert calls[2][1] == wiztree_mod._UNINSTALL_SUBKEYS[0]

    def test_install_location_recorded_but_exe_missing_is_not_a_match(self, tmp_path, monkeypatch):
        # A stale/broken uninstall entry (e.g. the folder was moved/deleted
        # by hand instead of via the uninstaller) must not be reported as
        # found -- find_wiztree() falls through to its next tier instead.
        monkeypatch.setattr(wiztree_mod, "_read_uninstall_entries", lambda hive, subkey: [("WizTree v4.32", str(tmp_path / "gone"))])
        assert wiztree_mod._find_wiztree_via_registry() is None

    def test_no_matching_entry_anywhere_returns_none(self, monkeypatch):
        monkeypatch.setattr(wiztree_mod, "_read_uninstall_entries", lambda hive, subkey: [("Notepad++", "C:\\Notepad++")])
        assert wiztree_mod._find_wiztree_via_registry() is None


class _FakeRegKey:
    """Minimal winreg-key-handle stand-in: a context manager wrapping
    either a list of child subkey names (the top-level Uninstall key) or a
    dict of value-name -> value (one uninstall entry)."""

    def __init__(self, subkey_names=None, values=None):
        self.subkey_names = subkey_names or []
        self.values = values or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class TestReadUninstallEntries:
    """_read_uninstall_entries()'s own winreg call sequence, verified
    against a fake `winreg` module injected via sys.modules -- winreg
    doesn't exist on non-Windows platforms, so this keeps the test (and
    module import) collectible everywhere despite exercising the exact
    OpenKey/QueryInfoKey/EnumKey/QueryValueEx sequence used against the
    real registry (confirmed live on a real Windows machine separately).
    """

    def _install_fake_winreg(self, monkeypatch, top_key: _FakeRegKey, entries: dict[str, _FakeRegKey]) -> None:
        def query_value_ex(key: _FakeRegKey, name: str):
            if name not in key.values:
                raise OSError(f"no such value: {name}")
            return (key.values[name], 1)

        fake = types.SimpleNamespace(
            OpenKey=lambda hive, subkey: entries[subkey] if subkey in entries else top_key,
            QueryInfoKey=lambda key: (len(key.subkey_names), 0, 0),
            EnumKey=lambda key, i: key.subkey_names[i],
            QueryValueEx=query_value_ex,
        )
        monkeypatch.setitem(sys.modules, "winreg", fake)

    def test_reads_display_name_and_install_location_per_entry(self, monkeypatch):
        top = _FakeRegKey(subkey_names=["WizTree_is1", "Other_is1"])
        entries = {
            "WizTree_is1": _FakeRegKey(values={"DisplayName": "WizTree v4.32", "InstallLocation": "D:\\apps\\wiztree\\"}),
            "Other_is1": _FakeRegKey(values={"DisplayName": "Other App"}),  # no InstallLocation value at all
        }
        self._install_fake_winreg(monkeypatch, top, entries)

        result = wiztree_mod._read_uninstall_entries(hive=1, subkey="Uninstall")
        assert result == [
            ("WizTree v4.32", "D:\\apps\\wiztree\\"),
            ("Other App", None),
        ]

    def test_missing_key_returns_empty_list_not_an_error(self, monkeypatch):
        def raise_open_key(hive, subkey):
            raise OSError("key not found")

        monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace(OpenKey=raise_open_key))
        assert wiztree_mod._read_uninstall_entries(hive=1, subkey="Uninstall") == []

    def test_one_malformed_entry_does_not_abort_the_rest(self, monkeypatch):
        # An entry with no DisplayName at all (QueryValueEx raises) must be
        # skipped, not crash the whole scan of sibling entries.
        top = _FakeRegKey(subkey_names=["Broken_is1", "WizTree_is1"])
        entries = {
            "Broken_is1": _FakeRegKey(values={}),
            "WizTree_is1": _FakeRegKey(values={"DisplayName": "WizTree v4.32", "InstallLocation": "D:\\apps\\wiztree\\"}),
        }
        self._install_fake_winreg(monkeypatch, top, entries)

        result = wiztree_mod._read_uninstall_entries(hive=1, subkey="Uninstall")
        assert result == [("WizTree v4.32", "D:\\apps\\wiztree\\")]


class TestParseWizTreeCsv:
    def test_english_header(self, tmp_path):
        csv_path = tmp_path / "export.csv"
        csv_path.write_text(
            "File Name,Size,Allocated,Modified,Attributes,Files,Folders\n"
            "C:\\Users\\test\\AppData\\,120,128,2024-01-01 10:00:00,D,10,2\n"
            "C:\\Users\\test\\file.txt,50,50,2024-01-02 11:00:00,A,,\n",
            encoding="utf-8",
        )
        entries = wiztree_mod.parse_wiztree_csv(str(csv_path))
        assert len(entries) == 2

        folder, file_entry = entries
        assert folder.is_folder is True
        assert folder.full_name == "C:\\Users\\test\\AppData"
        assert folder.size_bytes == 120
        assert folder.allocated_bytes == 128
        assert folder.file_count == 10
        assert folder.folder_count == 2
        assert folder.modified == datetime(2024, 1, 1, 10, 0, 0)

        assert file_entry.is_folder is False
        assert file_entry.full_name == "C:\\Users\\test\\file.txt"
        assert file_entry.size_bytes == 50
        assert file_entry.file_count is None
        assert file_entry.folder_count is None

    def test_localized_chinese_header_detected_structurally(self, tmp_path):
        """Proves header detection never matches English header text --
        WizTree localizes the header row to the OS display language.
        """
        csv_path = tmp_path / "export_zh.csv"
        csv_path.write_text(
            "文件名称,大小,分配,修改时间,属性,文件,文件夹\n"
            "C:\\Users\\test\\AppData\\,120,128,2024-01-01 10:00:00,D,10,2\n",
            encoding="utf-8",
        )
        entries = wiztree_mod.parse_wiztree_csv(str(csv_path))
        assert len(entries) == 1
        assert entries[0].full_name == "C:\\Users\\test\\AppData"
        assert entries[0].size_bytes == 120
        assert entries[0].is_folder is True

    def test_no_data_rows_after_header(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("File Name,Size,Allocated,Modified,Attributes,Files,Folders\n", encoding="utf-8")
        assert wiztree_mod.parse_wiztree_csv(str(csv_path)) == []

    def test_no_header_raises(self, tmp_path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("just,1\nsome,2\n", encoding="utf-8")
        with pytest.raises(StoropsError):
            wiztree_mod.parse_wiztree_csv(str(csv_path))

    def test_quoted_filename_with_comma_parsed_correctly(self, tmp_path):
        csv_path = tmp_path / "quoted.csv"
        csv_path.write_text(
            'File Name,Size,Allocated,Modified,Attributes,Files,Folders\n'
            '"C:\\Users\\test\\a, b.txt",42,42,2024-01-01 00:00:00,A,,\n',
            encoding="utf-8",
        )
        entries = wiztree_mod.parse_wiztree_csv(str(csv_path))
        assert len(entries) == 1
        assert entries[0].full_name == "C:\\Users\\test\\a, b.txt"
        assert entries[0].size_bytes == 42

    def test_real_export_banner_line_and_extra_drive_columns(self, tmp_path):
        # A real (unregistered) WizTree install prepends a donation-nag
        # banner line with no comma at all, and localizes the header to the
        # OS display language (zh-CN here) -- both already covered above.
        # New here: the *first* data row (the scanned root itself) carries
        # extra trailing DRIVECAPACITY/FREESPACE/USEDSPACE/RESERVEDSPACE
        # columns beyond the fixed 7, observed on a real export -- must not
        # break parsing of that row or any row after it.
        csv_path = tmp_path / "real_shaped.csv"
        csv_path.write_text(
            "生成由 WizTree 4.32 2026/9/3 3:32:54 (您可以通过捐赠隐藏此信息)\n"
            "文件名称,大小,分配,修改时间,属性,文件,文件夹,DRIVECAPACITY,FREESPACE,USEDSPACE,RESERVEDSPACE\n"
            '"C:\\Windows\\System32\\drivers\\",176843272,177795072,2026/09/03 01:43:32,0,621,11,'
            "321153658880,42366251008,278787407872,0\n"
            '"C:\\Windows\\System32\\drivers\\Netwfw10.dat",15597900,15601664,2025/06/17 23:12:16,32,0,0\n',
            encoding="utf-8",
        )
        entries = wiztree_mod.parse_wiztree_csv(str(csv_path))
        assert len(entries) == 2
        root, file_entry = entries
        assert root.is_folder is True
        assert root.size_bytes == 176843272
        assert root.file_count == 621
        assert root.folder_count == 11
        assert root.modified == datetime(2026, 9, 3, 1, 43, 32)
        assert file_entry.modified == datetime(2025, 6, 17, 23, 12, 16)


class TestParseDatetimeFastPath:
    """_parse_datetime()'s regex+datetime() fast path must accept exactly
    what the strptime loop it sits in front of accepts, for all 5
    _DATETIME_FORMATS -- including the two "/"-separated formats that share
    one regex shape and are only disambiguated by which interpretation
    produces valid month/day values (mirrors trying %m/%d/%Y then %d/%m/%Y
    in order). Anything the fast regexes don't recognize (e.g. non-zero-
    padded fields, which strptime accepts but this fast path does not
    attempt) must still fall through to the original strptime loop.
    """

    def test_iso_dash_format(self):
        assert wiztree_mod._parse_datetime("2024-01-15 10:30:00") == datetime(2024, 1, 15, 10, 30, 0)

    def test_iso_slash_format(self):
        # The format a real zh-CN WizTree install actually emits.
        assert wiztree_mod._parse_datetime("2024/01/15 10:30:00") == datetime(2024, 1, 15, 10, 30, 0)

    def test_us_month_day_year_unambiguous(self):
        # Day 25 can't be a month -- %m/%d/%Y succeeds on the first try.
        assert wiztree_mod._parse_datetime("03/25/2024 10:30:00") == datetime(2024, 3, 25, 10, 30, 0)

    def test_eu_day_month_year_falls_through_from_us_interpretation(self):
        # %m/%d/%Y would read this as month=25 (invalid) and must fall
        # through to %d/%m/%Y (day=25, month=3) -- same disambiguation
        # order as the pre-existing strptime loop.
        assert wiztree_mod._parse_datetime("25/03/2024 10:30:00") == datetime(2024, 3, 25, 10, 30, 0)

    def test_iso_t_separator_format(self):
        assert wiztree_mod._parse_datetime("2024-01-15T10:30:00") == datetime(2024, 1, 15, 10, 30, 0)

    def test_non_zero_padded_falls_back_to_strptime(self):
        # No fast regex matches single-digit fields -- must still work via
        # the strptime fallback (strptime itself accepts these).
        assert wiztree_mod._parse_datetime("2024-1-5 9:5:3") == datetime(2024, 1, 5, 9, 5, 3)

    def test_unparseable_garbage_returns_none(self):
        assert wiztree_mod._parse_datetime("not a date") is None

    def test_empty_string_returns_none(self):
        assert wiztree_mod._parse_datetime("") is None


class TestWizTreeBackendSubprocessArgs:
    """Locks down the exact argument list passed to subprocess.run for a
    WizTree export -- never shell=True, target path a plain list element.
    """

    def _fake_run_writing_csv(self, captured):
        def fake_run(args, capture_output=True, timeout=None):
            captured["args"] = args
            captured["timeout"] = timeout
            export_arg = next(a for a in args if a.startswith("/export="))
            csv_path = export_arg.split("=", 1)[1]
            Path(csv_path).write_text(
                "File Name,Size,Allocated,Modified,Attributes,Files,Folders\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        return fake_run

    def test_scan_invokes_expected_args(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(wiztree_mod.subprocess, "run", self._fake_run_writing_csv(captured))

        backend = wiztree_mod.WizTreeBackend("C:\\WizTree\\WizTree64.exe", timeout_seconds=120)
        entries = backend.scan(
            "C:\\Users\\test",
            export_folders=True,
            export_files=True,
            max_depth=2,
            name_filter="*.tmp",
            name_exclude="*.log",
            admin=True,
        )

        assert entries == []
        args = captured["args"]
        assert args[0] == "C:\\WizTree\\WizTree64.exe"
        assert args[1] == wiztree_mod.resolve_path("C:\\Users\\test")
        assert "/exportfolders=1" in args
        assert "/exportfiles=1" in args
        assert "/exportmaxdepth=2" in args
        assert "/sortby=1" in args
        assert "/admin=1" in args
        assert "/filter=*.tmp" in args
        assert "/filterexclude=*.log" in args
        assert any(a.startswith("/export=") for a in args)
        assert captured["timeout"] == 120

    def test_scan_omits_optional_filters_when_not_given(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(wiztree_mod.subprocess, "run", self._fake_run_writing_csv(captured))

        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        backend.scan("C:\\Users\\test")

        args = captured["args"]
        assert not any(a.startswith("/filter=") for a in args)
        assert not any(a.startswith("/filterexclude=") for a in args)
        assert "/exportfolders=1" in args
        assert "/exportfiles=0" in args
        assert "/admin=0" in args

    def test_scan_never_uses_shell(self, monkeypatch):
        # subprocess.run must be called with a list of args, not shell=True.
        captured: dict = {}

        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            export_arg = next(a for a in args[0] if a.startswith("/export="))
            csv_path = export_arg.split("=", 1)[1]
            Path(csv_path).write_text(
                "File Name,Size,Allocated,Modified,Attributes,Files,Folders\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(wiztree_mod.subprocess, "run", fake_run)
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        backend.scan("C:\\Users\\test")

        assert isinstance(captured["args"][0], list)
        assert captured["kwargs"].get("shell", False) is False

    def test_timeout_raises_storops_error(self, monkeypatch):
        def fake_run(args, capture_output=True, timeout=None):
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

        monkeypatch.setattr(wiztree_mod.subprocess, "run", fake_run)
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe", timeout_seconds=5)
        with pytest.raises(StoropsError):
            backend.scan("C:\\Users\\test")

    def test_missing_csv_after_run_raises(self, monkeypatch):
        def fake_run(args, capture_output=True, timeout=None):
            # WizTree "ran" but never produced the export file.
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"error")

        monkeypatch.setattr(wiztree_mod.subprocess, "run", fake_run)
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        with pytest.raises(StoropsError):
            backend.scan("C:\\Users\\test")

    def test_top_entries_excludes_scanned_root_and_sorts(self, monkeypatch):
        def fake_run(args, capture_output=True, timeout=None):
            export_arg = next(a for a in args if a.startswith("/export="))
            csv_path = export_arg.split("=", 1)[1]
            target = args[1]
            Path(csv_path).write_text(
                "File Name,Size,Allocated,Modified,Attributes,Files,Folders\n"
                f"{target}\\,999,999,2024-01-01 00:00:00,D,1,1\n"
                f"{target}\\small\\,10,10,2024-01-01 00:00:00,D,0,0\n"
                f"{target}\\big\\,500,500,2024-01-01 00:00:00,D,0,0\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        monkeypatch.setattr(wiztree_mod.subprocess, "run", fake_run)
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        top = backend.top_entries("C:\\Users\\test", top=5)
        assert [e.size_bytes for e in top] == [500, 10]


class TestWizTreeBackendMisc:
    def test_advice_is_none(self):
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        assert backend.advice() is None

    def test_take_warnings_empty_and_clears(self):
        backend = wiztree_mod.WizTreeBackend("WizTree64.exe")
        assert backend.take_warnings() == []

    def test_name(self):
        assert wiztree_mod.WizTreeBackend("WizTree64.exe").name == "WizTree"
