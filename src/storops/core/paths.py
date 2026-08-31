"""Path normalization helpers shared by the rule engine and platform layer.

Deliberately built on `os.path`/`pathlib` only -- never manual string
concatenation with a literal `\\` or `/`, and never `.resolve()` (which
would silently follow symlinks; StorOps wants the literal requested path
for rule matching and critical-path safety checks, matching the original
PowerShell `Resolve-StorOpsPath`'s "normalize without following links or
requiring existence" behavior).
"""
from __future__ import annotations

import os


def resolve_path(path: str | os.PathLike[str]) -> str:
    """Normalize to an absolute path without resolving symlinks or requiring existence."""
    return os.path.abspath(os.fspath(path))


def normalize_separators(path: str) -> str:
    """Canonicalize a path/pattern to forward-slash, lowercase form for matching.

    rules/*.yaml patterns are authored with a literal `\\` (historically,
    since the original PowerShell reader matched via `-like`, which is
    separator-agnostic on Windows only). The Python matcher normalizes both
    the pattern and the candidate path through this function before
    comparing, so a pattern never needs a separate Linux/macOS variant just
    to account for `/` vs `\\` -- see docs/plans/
    storops-v2-cross-platform-refactor.md §1.6.5 and §2.15.
    """
    return path.replace("\\", "/").lower()
