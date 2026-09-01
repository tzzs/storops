"""Black-box CLI tests: real subprocess invocations of `python -m storops`.
Only exercises commands that don't need a platform scan backend (identify
is pure core/rules.py + core/risk.py) -- scan/inspect/search/cleanup/
migrate end-to-end coverage lives in tests/platform/ once the platform
layer lands, plus tests/unit/test_cli_wiring.py for the mocked-backend path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "storops", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _env_without_os_temp_vars() -> dict[str, str]:
    """A subprocess env with TEMP/TMP/USERPROFILE removed.

    pytest's own tmp_path fixture is always rooted under the real OS temp
    directory. On Windows that collides with rules/caches.yaml's
    "windows-temp" rule (`%TEMP%\\*` / `%USERPROFILE%\\AppData\\Local\\
    Temp\\*`), which then legitimately -- correctly -- classifies these
    "arbitrary unknown path" test fixtures as os-temp instead of unknown.
    Linux/macOS have no such catch-all rule, so this only matters on
    Windows. Stripping these vars from the *subprocess's* environment
    (not the actual fixture location) makes the rule engine unable to
    expand %TEMP%/%USERPROFILE%, so the pattern can no longer match the
    real path -- letting these tests assert "unknown" everywhere, as
    intended, without depending on which OS temp dir pytest happened to
    pick.
    """
    env = dict(os.environ)
    for key in ("TEMP", "TMP", "USERPROFILE"):
        env.pop(key, None)
    return env


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
    result = _run("identify", str(target), "--json", env=_env_without_os_temp_vars())
    assert result.returncode == 0
    # stdout must be ONLY the JSON document -- no warnings/log lines mixed in.
    data = json.loads(result.stdout)
    assert data["Identity"]["Category"] == "unknown"
    assert data["Identity"]["CleanupRisk"] == "critical"
    assert data["Recommended"]["Action"] == "CHECK"


def test_identify_human_mode_is_readable(tmp_path):
    target = tmp_path / "some_unknown_thing"
    target.write_text("x")
    result = _run("identify", str(target), env=_env_without_os_temp_vars())
    assert result.returncode == 0
    assert "Recommended action: CHECK" in result.stdout


def test_identify_nonexistent_path_still_returns_unknown(tmp_path):
    # identify never requires the path to exist -- matches v1 behavior.
    result = _run(
        "identify", str(tmp_path / "does" / "not" / "exist"), "--json", env=_env_without_os_temp_vars()
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["Identity"]["Category"] == "unknown"
