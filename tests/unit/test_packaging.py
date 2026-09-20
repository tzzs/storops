"""Guards on pyproject.toml's distribution metadata.

These exist because the packaging bugs they cover are invisible to every
other test in this suite: CI installs the project with `pip install -e .`,
which leaves the repo-root `rules/` directory reachable via
core/rules.py's development-layout candidate. A real `pip install storops`
does not -- and shipped a wheel with no rule files at all, so every single
command failed with "could not locate the rules/ directory".
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.9/3.10 runners
    import pytest

    tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _declared_packages() -> list[str]:
    return _pyproject()["tool"]["setuptools"]["packages"]


def test_rules_directory_is_mapped_into_the_wheel():
    setuptools_config = _pyproject()["tool"]["setuptools"]
    assert setuptools_config["package-dir"]["storops.rules"] == "rules"
    assert "storops.rules" in setuptools_config["packages"]
    assert setuptools_config["package-data"]["storops.rules"] == ["*.yaml"]


def test_every_rule_file_the_engine_loads_is_shipped():
    from storops.core.rules import _RULE_FILE_ORDER

    for filename in _RULE_FILE_ORDER:
        assert (REPO_ROOT / "rules" / filename).is_file(), filename


def test_declared_packages_cover_every_package_under_src():
    """`packages.find` had to be replaced by an explicit list to map
    `storops.rules` in from outside `src/` (see pyproject.toml's comment).
    That trades discovery for a list that can silently go stale, so a new
    subpackage that nobody remembers to add here -- and would therefore be
    missing from the wheel -- fails right here instead.
    """
    on_disk = {
        ".".join(path.parent.relative_to(SRC).parts)
        for path in SRC.rglob("__init__.py")
    }
    missing = on_disk - set(_declared_packages())
    assert not missing, f"not listed in pyproject.toml [tool.setuptools].packages: {sorted(missing)}"


def test_requires_python_floor_matches_the_running_interpreter_support():
    """StorOps must stay installable on a stock macOS `python3` (3.9) and
    RHEL 9 (3.9); raising this floor is a deliberate decision, not a
    drive-by.
    """
    assert _pyproject()["project"]["requires-python"] == ">=3.9"
