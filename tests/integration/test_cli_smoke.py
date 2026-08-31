"""Black-box CLI tests: real subprocess invocations of `python -m storops`.
Only exercises commands that don't need a platform scan backend (identify
is pure core/rules.py + core/risk.py) -- scan/inspect/search/cleanup/
migrate end-to-end coverage lives in tests/platform/ once the platform
layer lands, plus tests/unit/test_cli_wiring.py for the mocked-backend path.
"""
from __future__ import annotations

import json
import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "storops", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_help_exits_zero():
    result = _run("--help")
    assert result.returncode == 0
    assert "storops" in result.stdout


def test_no_command_exits_with_argparse_code():
    result = _run()
    assert result.returncode == 2  # argparse's own exit code for missing required subcommand


def test_identify_json_stdout_is_pure_json(tmp_path):
    target = tmp_path / "some_unknown_thing"
    target.write_text("x")
    result = _run("identify", str(target), "--json")
    assert result.returncode == 0
    # stdout must be ONLY the JSON document -- no warnings/log lines mixed in.
    data = json.loads(result.stdout)
    assert data["Identity"]["Category"] == "unknown"
    assert data["Identity"]["CleanupRisk"] == "critical"
    assert data["Recommended"]["Action"] == "CHECK"


def test_identify_human_mode_is_readable(tmp_path):
    target = tmp_path / "some_unknown_thing"
    target.write_text("x")
    result = _run("identify", str(target))
    assert result.returncode == 0
    assert "Recommended action: CHECK" in result.stdout


def test_identify_nonexistent_path_still_returns_unknown(tmp_path):
    # identify never requires the path to exist -- matches v1 behavior.
    result = _run("identify", str(tmp_path / "does" / "not" / "exist"), "--json")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["Identity"]["Category"] == "unknown"
