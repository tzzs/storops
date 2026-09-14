"""Unit tests for migrate.verify()'s post-migration report.

verify() re-reads the result JSON that execute() wrote, re-measures the
destination with dir_stats(), and appends a LastVerification block back into
the result file. These tests hand-craft result files so the report logic is
covered independently of a real copy/link engine (the engines have their own
unit tests under tests/unit/test_windows_*.py / test_posix_platform.py;
execute()'s flow is covered in test_migrate_execute.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from storops import platform as platform_pkg
from storops.core.copystats import dir_stats
from storops.core.migrate import verify


class _FakeLinkEngine:
    kind = "fakelink"

    def __init__(self, verify_result: bool = True):
        self.verify_result = verify_result

    def create(self, old_path: str, target: str) -> None: ...

    def verify(self, old_path: str, expected_target: str) -> bool:
        return self.verify_result


def _write_result(tmp_path: Path, payload: dict) -> Path:
    result_file = tmp_path / "storops-migrate-result.json"
    result_file.write_text(json.dumps(payload), encoding="utf-8")
    return result_file


def _populated_destination(tmp_path: Path) -> tuple[Path, dict]:
    destination = tmp_path / "pool" / "models"
    destination.mkdir(parents=True)
    (destination / "a.bin").write_bytes(b"a" * 100)
    (destination / "b.bin").write_bytes(b"b" * 40)
    stats = dir_stats(str(destination))
    return destination, {"FileCount": stats.file_count, "SizeBytes": stats.size_bytes}


def test_verify_passes_and_writes_last_verification_back(tmp_path):
    destination, post_copy = _populated_destination(tmp_path)
    result_file = _write_result(
        tmp_path,
        {"Source": str(tmp_path / "gone"), "Destination": str(destination), "PostCopy": post_copy},
    )

    report = verify(str(result_file))

    assert report.passed is True
    assert {c.check: c.passed for c in report.checks} == {
        "target-accessible": True,
        "file-count-matches": True,
        "total-size-matches": True,
        "source-cleared": True,
    }

    saved = json.loads(result_file.read_text(encoding="utf-8"))
    assert saved["LastVerification"]["Pass"] is True
    assert len(saved["LastVerification"]["Checks"]) == 4
    assert saved["LastVerification"]["VerifiedAt"]


def test_verify_fails_when_destination_missing(tmp_path):
    result_file = _write_result(
        tmp_path,
        {
            "Source": str(tmp_path / "gone"),
            "Destination": str(tmp_path / "pool" / "missing"),
            "PostCopy": {"FileCount": 2, "SizeBytes": 140},
        },
    )

    report = verify(str(result_file))

    assert report.passed is False
    checks = {c.check: c.passed for c in report.checks}
    assert checks["target-accessible"] is False
    assert checks["file-count-matches"] is False
    assert checks["total-size-matches"] is False
    assert checks["source-cleared"] is True

    saved = json.loads(result_file.read_text(encoding="utf-8"))
    assert saved["LastVerification"]["Pass"] is False


def test_verify_fails_when_file_count_drifts(tmp_path):
    destination, post_copy = _populated_destination(tmp_path)
    drifted = dict(post_copy, FileCount=post_copy["FileCount"] + 1)
    result_file = _write_result(
        tmp_path,
        {"Source": str(tmp_path / "gone"), "Destination": str(destination), "PostCopy": drifted},
    )

    report = verify(str(result_file))

    assert report.passed is False
    checks = {c.check: c.passed for c in report.checks}
    assert checks["target-accessible"] is True
    assert checks["file-count-matches"] is False
    assert checks["total-size-matches"] is True


def test_verify_link_method_reports_link_health(tmp_path, monkeypatch):
    destination, post_copy = _populated_destination(tmp_path)
    source = tmp_path / "home" / ".fakeapp" / "models"
    source.mkdir(parents=True)  # stand-in for the link site execute() creates
    result_file = _write_result(
        tmp_path,
        {
            "Source": str(source),
            "Destination": str(destination),
            "PostCopy": post_copy,
            "Method": "fakelink",
        },
    )

    monkeypatch.setattr(platform_pkg, "get_link_engine", lambda: _FakeLinkEngine())
    report = verify(str(result_file))
    checks = {c.check: c.passed for c in report.checks}
    assert checks["source-is-fakelink"] is True
    assert checks["fakelink-works"] is True
    assert report.passed is True

    monkeypatch.setattr(platform_pkg, "get_link_engine", lambda: _FakeLinkEngine(verify_result=False))
    broken = verify(str(result_file))
    broken_checks = {c.check: c.passed for c in broken.checks}
    assert broken_checks["source-is-fakelink"] is True
    assert broken_checks["fakelink-works"] is False
    assert broken.passed is False


def test_verify_missing_result_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify(str(tmp_path / "absent.json"))
