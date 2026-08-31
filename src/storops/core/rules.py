"""StorOps' rule engine: loads rules/*.yaml and matches paths against them,
so the agent never has to guess what a path is from its name alone (see
docs/DESIGN.md §3.3).

`_read_rule_file`/`_parse_item`/`_to_scalar` below are a small reader for
the specific YAML subset documented in rules/README.md -- NOT a
general-purpose YAML parser (no PyYAML dependency; see docs/plans/
storops-v2-cross-platform-refactor.md §2.8 for why). Do not extend the
rule files beyond that subset without extending this reader to match.
This is a straight port of scripts/lib/Identify.psm1's reader; keep the
two in sync until the PowerShell version is retired (§2.10).
"""
from __future__ import annotations

import os
import platform as _platform
import re
from fnmatch import fnmatch
from pathlib import Path

from storops.core.models import PathIdentity, Rule
from storops.core.paths import normalize_separators, resolve_path

_TOP_ITEM_RE = re.compile(r"^-\s?(.*)$")
_FIELD_RE = re.compile(r"^  (\S[^:]*):\s?(.*)$")
_FOLDED_START_RE = re.compile(r"^>[-+]?$")
_FOLDED_LINE_RE = re.compile(r"^ {4}\S")
_LIST_ITEM_RE = re.compile(r"^\s+-\s?(.*)$")
_CONTINUATION_RE = re.compile(r"^\s")

# Match precedence: windows/linux/macos.yaml first (a critical system match
# wins outright and short-circuits), then ai-models, applications, caches.
# All three platform files are always loaded regardless of the current
# platform -- only the current platform's %TOKEN% set actually expands
# (see _platform_tokens), so the other two files' rules simply never match
# a real path. Mirrors Identify.psm1's Get-StorOpsRules.
_RULE_FILE_ORDER = (
    "windows.yaml",
    "linux.yaml",
    "macos.yaml",
    "ai-models.yaml",
    "applications.yaml",
    "caches.yaml",
)

_UNKNOWN_IDENTITY_CONSEQUENCE = (
    "Unrecognized path: StorOps has no rule for it and treats it as "
    "not-safe-to-touch by default."
)


def _to_scalar(text: str) -> object:
    t = text.strip()
    if not t:
        return None
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].replace("\\\\", "\\")
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1].replace("''", "'")
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    try:
        if re.fullmatch(r"-?\d+", t):
            return int(t)
        return float(t)
    except ValueError:
        return t


def _parse_item(lines: list[str]) -> dict[str, object]:
    obj: dict[str, object] = {}
    i, n = 0, len(lines)
    while i < n:
        m = _FIELD_RE.match(lines[i])
        if not m:
            raise ValueError(f"StorOps rule parser: could not parse field line: {lines[i]!r}")
        key, rest = m.group(1).strip(), m.group(2)
        i += 1
        if _FOLDED_START_RE.match(rest):
            text_lines: list[str] = []
            while i < n and _FOLDED_LINE_RE.match(lines[i]):
                text_lines.append(lines[i][4:].rstrip())
                i += 1
            obj[key] = " ".join(text_lines).strip()
        elif rest == "":
            items: list[object] = []
            while i < n and _LIST_ITEM_RE.match(lines[i]):
                items.append(_to_scalar(_LIST_ITEM_RE.match(lines[i]).group(1)))
                i += 1
            obj[key] = items
        else:
            obj[key] = _to_scalar(rest)
    return obj


def _read_rule_file(path: Path) -> list[dict[str, object]]:
    raw_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") and line.strip() != ""
    ]
    rules: list[dict[str, object]] = []
    i, n = 0, len(raw_lines)
    while i < n:
        m = _TOP_ITEM_RE.match(raw_lines[i])
        if not m:
            raise ValueError(
                f"StorOps rule parser ({path}): expected a top-level '- ' item, got: {raw_lines[i]!r}"
            )
        item_lines = ["  " + m.group(1)]
        i += 1
        while i < n and _CONTINUATION_RE.match(raw_lines[i]):
            item_lines.append(raw_lines[i])
            i += 1
        rules.append(_parse_item(item_lines))
    return rules


def _build_rule(d: dict[str, object]) -> Rule:
    for required in ("id", "category", "cleanup_risk"):
        if required not in d:
            raise ValueError(f"StorOps rule parser: rule missing required field '{required}': {d!r}")
    return Rule(
        id=str(d["id"]),
        application=d.get("application"),  # type: ignore[arg-type]
        category=str(d["category"]),
        path_patterns=tuple(d.get("path_patterns") or []),  # type: ignore[arg-type]
        cleanup_risk=str(d["cleanup_risk"]),
        confidence=float(d.get("confidence", 0.5)),  # type: ignore[arg-type]
        owner=str(d.get("owner") or "user"),
        purpose=d.get("purpose"),  # type: ignore[arg-type]
        deletable=bool(d.get("deletable", False)),
        migratable=bool(d.get("migratable", False)),
        migration_method=d.get("migration_method"),  # type: ignore[arg-type]
        migration_config_hint=d.get("migration_config_hint"),  # type: ignore[arg-type]
        migration_requires_app_closed=bool(d.get("migration_requires_app_closed", False)),
        cleanup_consequence=d.get("cleanup_consequence"),  # type: ignore[arg-type]
        notes=d.get("notes"),  # type: ignore[arg-type]
    )


def _default_rules_dir() -> Path:
    # Development / editable-install layout: <repo-root>/rules, walking up
    # from src/storops/core/rules.py (parents[3] == repo root).
    dev_candidate = Path(__file__).resolve().parents[3] / "rules"
    if dev_candidate.is_dir():
        return dev_candidate
    # Installed-package layout: rules/ shipped as package data next to the
    # storops package (see pyproject.toml [tool.setuptools.package-data]).
    packaged_candidate = Path(__file__).resolve().parent.parent / "rules"
    if packaged_candidate.is_dir():
        return packaged_candidate
    raise FileNotFoundError(
        "StorOps: could not locate the rules/ directory (checked repo-root "
        f"'{dev_candidate}' and packaged '{packaged_candidate}')."
    )


_rule_cache: list[Rule] | None = None
_rule_cache_dir: Path | None = None


def load_rules(rules_dir: Path | str | None = None, *, force: bool = False) -> list[Rule]:
    global _rule_cache, _rule_cache_dir
    directory = Path(rules_dir).resolve() if rules_dir else _default_rules_dir()

    if not force and _rule_cache is not None and _rule_cache_dir == directory:
        return _rule_cache

    all_rules: list[Rule] = []
    for filename in _RULE_FILE_ORDER:
        file_path = directory / filename
        if file_path.is_file():
            for raw in _read_rule_file(file_path):
                all_rules.append(_build_rule(raw))

    _rule_cache = all_rules
    _rule_cache_dir = directory
    return all_rules


def _platform_tokens() -> dict[str, str | None]:
    """Platform-dependent %TOKEN% -> value table. Mirrors
    Identify.psm1's Expand-StorOpsPatternTokens exactly (same token names,
    same platform grouping, same fallback-when-env-var-unset defaults).
    """
    system = _platform.system()
    home = str(Path.home())

    if system == "Windows":
        return {
            "%USERPROFILE%": os.environ.get("USERPROFILE"),
            "%LOCALAPPDATA%": os.environ.get("LOCALAPPDATA"),
            "%APPDATA%": os.environ.get("APPDATA"),
            "%PROGRAMDATA%": os.environ.get("ProgramData"),
            "%PROGRAMFILES%": os.environ.get("ProgramFiles"),
            "%PROGRAMFILES(X86)%": os.environ.get("ProgramFiles(x86)"),
            "%TEMP%": os.environ.get("TEMP"),
            "%SYSTEMROOT%": os.environ.get("SystemRoot"),
        }
    if system == "Darwin":
        return {
            "%HOME%": home,
            "%CACHES%": str(Path(home) / "Library" / "Caches"),
            "%APP_SUPPORT%": str(Path(home) / "Library" / "Application Support"),
            "%XDG_CACHE_HOME%": os.environ.get("XDG_CACHE_HOME") or str(Path(home) / ".cache"),
            "%TMPDIR%": os.environ.get("TMPDIR") or "/tmp",
        }
    # Linux and anything else POSIX-like.
    return {
        "%HOME%": home,
        "%XDG_CACHE_HOME%": os.environ.get("XDG_CACHE_HOME") or str(Path(home) / ".cache"),
        "%XDG_CONFIG_HOME%": os.environ.get("XDG_CONFIG_HOME") or str(Path(home) / ".config"),
        "%XDG_DATA_HOME%": os.environ.get("XDG_DATA_HOME") or str(Path(home) / ".local" / "share"),
        "%TMPDIR%": os.environ.get("TMPDIR") or "/tmp",
    }


def expand_pattern_tokens(pattern: str) -> str:
    result = pattern
    for token, value in _platform_tokens().items():
        if value:
            result = result.replace(token, value)
    return result


def _pattern_matches(normalized_path: str, pattern: str) -> bool:
    expanded = expand_pattern_tokens(pattern)
    return fnmatch(normalize_separators(normalized_path), normalize_separators(expanded))


def _unknown_identity(normalized_path: str) -> PathIdentity:
    return PathIdentity(
        path=normalized_path,
        application=None,
        category="unknown",
        confidence=0.0,
        owner=None,
        purpose=None,
        deletable=False,
        migratable=False,
        migration_method=None,
        migration_hint=None,
        requires_app_closed=False,
        cleanup_risk="critical",
        consequence=_UNKNOWN_IDENTITY_CONSEQUENCE,
        notes=None,
        matched_rule_id=None,
        matched_pattern=None,
    )


def identify_path(path: str | os.PathLike[str], *, rules_dir: Path | str | None = None) -> PathIdentity:
    """The core "identify" capability: classify a single path against the
    rule base. Never guesses -- an unmatched path comes back Category
    "unknown", CleanupRisk "critical" (StorOps never assumes an unknown
    path is safe to touch). Mirrors Identify.psm1's Get-StorOpsPathIdentity.
    """
    normalized = resolve_path(path)
    for rule in load_rules(rules_dir):
        for pattern in rule.path_patterns:
            if _pattern_matches(normalized, pattern):
                return PathIdentity(
                    path=normalized,
                    application=rule.application,
                    category=rule.category,
                    confidence=rule.confidence,
                    owner=rule.owner,
                    purpose=rule.purpose,
                    deletable=rule.deletable,
                    migratable=rule.migratable,
                    migration_method=rule.migration_method,
                    migration_hint=rule.migration_config_hint,
                    requires_app_closed=rule.migration_requires_app_closed,
                    cleanup_risk=rule.cleanup_risk,
                    consequence=rule.cleanup_consequence,
                    notes=rule.notes,
                    matched_rule_id=rule.id,
                    matched_pattern=pattern,
                )
    return _unknown_identity(normalized)
