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
from fnmatch import translate
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


_default_rules_dir_cache: Path | None = None


def _default_rules_dir() -> Path:
    # Memoized: Path.resolve()/.is_dir() measured ~0.3ms/call on Windows
    # (filesystem-filter/AV overhead on every stat) -- identify_path() is
    # called once per scanned path with no explicit rules_dir, so a batch
    # of ~1200 paths used to pay this on every single call for a result
    # that can never change (__file__'s location, and therefore the repo's
    # rules/ directory, is fixed for the life of the process).
    global _default_rules_dir_cache
    if _default_rules_dir_cache is not None:
        return _default_rules_dir_cache

    # Development / editable-install layout: <repo-root>/rules, walking up
    # from src/storops/core/rules.py (parents[3] == repo root).
    dev_candidate = Path(__file__).resolve().parents[3] / "rules"
    if dev_candidate.is_dir():
        _default_rules_dir_cache = dev_candidate
        return _default_rules_dir_cache
    # Installed-package layout: rules/ shipped as package data next to the
    # storops package (see pyproject.toml [tool.setuptools.package-data]).
    packaged_candidate = Path(__file__).resolve().parent.parent / "rules"
    if packaged_candidate.is_dir():
        _default_rules_dir_cache = packaged_candidate
        return _default_rules_dir_cache
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
            "%HOME%": home,
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


_matcher_cache: dict[str, tuple[re.Pattern[str], str | None]] = {}
_matcher_cache_tokens: dict[str, str | None] | None = None


def _sync_matcher_cache(tokens: dict[str, str | None]) -> None:
    """Drop all cached per-pattern matchers when the token table has
    changed since the last check (compared by value, not identity).

    Called once per identify_path()/_pattern_matches() entry rather than
    once per pattern -- env vars/HOME/platform can change independently of
    the rule files (notably in tests that monkeypatch Path.home()/
    os.environ/_platform.system()/_platform_tokens itself and expect
    identify_path() to reflect that on the very next call, with no
    load_rules(force=True) in between), so this must run somewhere on
    every call, but doing it per-pattern (~150 times per path) rather than
    per-path measurably added up over a large batch.
    """
    global _matcher_cache_tokens
    if tokens != _matcher_cache_tokens:
        _matcher_cache.clear()
        _matcher_cache_tokens = tokens


def _matcher_for(pattern: str) -> tuple[re.Pattern[str], str | None]:
    """Compiled regex + optional bare-directory prefix for one rule pattern,
    memoized by the raw pattern string. Caller must have already called
    _sync_matcher_cache() for the current token table.

    Rule patterns (~150 across rules/*.yaml) are static once loaded, so
    re-expanding tokens, re-normalizing, and re-translating a pattern to a
    regex for every path checked against it is pure waste -- identify_path()
    on a batch of ~1200 paths used to redo all of that ~150 times per path
    (once per pattern), which dominated its runtime.
    """
    cached = _matcher_cache.get(pattern)
    if cached is not None:
        return cached

    expanded = normalize_separators(expand_pattern_tokens(pattern))
    bare_prefix = expanded[: -len("/*")] if expanded.endswith("/*") else None
    matcher = (re.compile(translate(expanded)), bare_prefix)
    _matcher_cache[pattern] = matcher
    return matcher


def _matches(candidate: str, pattern: str) -> bool:
    """True if `candidate` (an already normalize_separators()-ed path) is
    described by `pattern`. See _pattern_matches()'s docstring for the
    bare-directory special case this also applies. Caller must have
    already called _sync_matcher_cache() for the current token table.
    """
    regex, bare_prefix = _matcher_for(pattern)
    if regex.match(candidate):
        return True
    return bare_prefix is not None and candidate == bare_prefix


def _pattern_matches(normalized_path: str, pattern: str) -> bool:
    """True if `normalized_path` is described by `pattern`.

    A "%TOKEN%/sub/*"-shaped pattern is authored to mean "this directory,
    and everything inside it" (see rules/README.md's own examples, and
    SKILL.md/README.md's migrate-plan examples, which pass the bare
    top-level directory itself -- e.g. "...\\.lmstudio\\models" -- as the
    thing to migrate). But plain fnmatch only ever matches candidates
    strictly INSIDE that directory for a trailing "/*": there is nothing
    left for the "*" to consume against a candidate with no trailing
    separator, so the bare directory itself never matches on its own.
    That silently broke `storops migrate plan` and `storops cleanup plan`
    for exactly the common "top-level folder scan/inspect just surfaced"
    case -- found via end-to-end testing, not by inspection; the original
    PowerShell version's `-like` operator has the identical false-negative
    for the identical reason, so this was never Python-specific. Fixed
    here, once, for every caller (identify_path, and therefore scan/
    inspect/search/identify/cleanup/migrate all get it for free) rather
    than working around it at each call site.

    A thin, self-contained wrapper around _matches() -- identify_path()'s
    hot loop calls _sync_matcher_cache()/_matches() directly with a
    candidate it already computed once for the whole rule base, rather
    than through here, since this function's job is being a simple
    single-pattern predicate rather than the batch-matching fast path.
    """
    _sync_matcher_cache(_platform_tokens())
    return _matches(normalize_separators(normalized_path), pattern)


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


def identity_from_rule(rule: Rule, path: str, matched_pattern: str | None = None) -> PathIdentity:
    """Build a PathIdentity directly from an already-known rule, without
    re-searching the rule base. Used by callers (core/cleanup.py's probe
    logic) that already know exactly which rule a path came from and must
    NOT re-derive it via identify_path() -- see that function's docstring
    for why a naive re-search is actively wrong for a probe path.
    """
    return PathIdentity(
        path=path,
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
        matched_pattern=matched_pattern,
    )


def identify_path(path: str | os.PathLike[str], *, rules_dir: Path | str | None = None) -> PathIdentity:
    """The core "identify" capability: classify a single path against the
    rule base. Never guesses -- an unmatched path comes back Category
    "unknown", CleanupRisk "critical" (StorOps never assumes an unknown
    path is safe to touch). Mirrors Identify.psm1's Get-StorOpsPathIdentity.

    NOTE: only meaningful for a path that is itself a *match target* (i.e.
    something a pattern's wildcard portion would expand to cover) -- a
    directory named exactly like a pattern's fixed prefix, with nothing
    after it, will legitimately NOT match a "%TOKEN%/sub/*"-shaped pattern
    (there is nothing for the trailing `*` to consume). This is correct
    fnmatch behavior, not a bug in this function -- see identity_from_rule()
    above for the case where a caller already knows which rule produced a
    directory (e.g. by stripping a pattern's own trailing "/*" to get a
    probe path) and must not fall into this trap by re-searching.
    """
    normalized = resolve_path(path)
    # Computed/synced once per call rather than once per (rule, pattern)
    # pair -- see _matcher_for()'s docstring for why that redundancy used
    # to dominate this function's cost on a large batch of paths.
    candidate = normalize_separators(normalized)
    _sync_matcher_cache(_platform_tokens())
    for rule in load_rules(rules_dir):
        for pattern in rule.path_patterns:
            if _matches(candidate, pattern):
                return identity_from_rule(rule, normalized, pattern)
    return _unknown_identity(normalized)
