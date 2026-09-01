"""StorOps' unified error hierarchy and CLI exit-code mapping.

See docs/plans/storops-v2-cross-platform-refactor.md §2.7. The PowerShell
v1 CLI had no exit-code convention at all (any unhandled exception fell
through to PowerShell's default non-zero code) -- this is a net-new
capability, not a migration, so there is no existing behavior to preserve.
"""
from __future__ import annotations

ARGPARSE_EXIT_CODE = 2


class StoropsError(Exception):
    """Base class for all StorOps errors. Exit code 1 unless overridden."""

    exit_code = 1


class InvalidPathError(StoropsError):
    exit_code = 1


class PermissionDeniedError(StoropsError):
    exit_code = 3


class BackendNotFoundError(StoropsError):
    """Raised when the platform's scan backend (WizTree/gdu/du) cannot be located."""

    exit_code = 4


class UnsupportedOperationError(StoropsError):
    """Raised for an operation with no implementation on the current platform."""

    exit_code = 4


class CriticalPathError(StoropsError):
    """Raised by assert_not_critical -- refuses to operate on a CRITICAL-risk path."""

    exit_code = 5


class StalePlanError(StoropsError):
    """Raised when a plan file no longer matches the live state it was generated from."""

    exit_code = 1


class VerificationFailedError(StoropsError):
    exit_code = 6


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, StoropsError):
        return exc.exit_code
    return 1
