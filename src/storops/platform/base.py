"""Platform Abstraction: Protocol contracts + factory functions.

Business logic in core/ depends ONLY on these Protocols, never on a
concrete Linux/macOS/Windows class directly. sys.platform / platform.
system() branching happens ONLY inside the four get_*() factory functions
below and inside is_admin() -- every other module in this codebase must
consume whatever these return rather than re-deriving "which OS am I on"
itself. See docs/plans/storops-v2-cross-platform-refactor.md §2.3.

Design note (deviation from the plan doc's illustrative directory tree,
recorded here per this project's own "actual code > example structure"
rule): the plan doc sketched separate platform/linux.py and
platform/macos.py modules. Implementing them showed that the Linux and
macOS mechanics for capacity/copy/link are byte-for-byte identical
(shutil.disk_usage / shutil.copytree / os.symlink behave the same on
both) -- the only real Linux-vs-macOS difference already lives in
core/rules.py's token-expansion table. Splitting capacity/copy/link into
two near-duplicate files would have been exactly the kind of unjustified
abstraction Prompt §33 and this project's own CLAUDE-style guidance warn
against, so both are served by the single platform/posix.py module
instead; only Windows gets its own subpackage, because Windows is where
the mechanics (WizTree/robocopy/Junction/ctypes) are genuinely different.
"""
from __future__ import annotations

import ctypes
import os
import platform as _platform
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from storops.core.models import Capacity, Entry, ScanWarning


class ScanBackend(Protocol):
    name: str  # "WizTree" | "Gdu" | "Du" | "WindowsNative" -- echoed into JSON as `Backend`

    def scan(
        self,
        path: str,
        *,
        export_folders: bool = True,
        export_files: bool = False,
        max_depth: int = 0,
        name_filter: str | None = None,
        name_exclude: str | None = None,
        admin: bool = False,
    ) -> list[Entry]: ...

    def top_entries(
        self,
        path: str,
        *,
        top: int = 20,
        max_depth: int = 1,
        admin: bool = False,
        include_files: bool = False,
    ) -> list[Entry]: ...

    def path_size(self, path: str, *, admin: bool = False) -> Entry | None: ...

    def advice(self) -> str | None:
        """None when this is the recommended backend for the platform;
        otherwise a short human-readable suggestion (e.g. "install gdu").
        Echoed into JSON as `BackendAdvice` -- see docs/plans/... §1.4 point 7.
        """
        ...

    def take_warnings(self) -> list[ScanWarning]:
        """Return and clear the non-fatal problems (e.g. permission denied
        on a subtree) collected during the most recent scan()/top_entries()/
        path_size() call. A single unreadable subtree must never abort an
        entire scan (docs/plans/... §2.6/§14) -- implementations collect
        these instead of raising, and reset the list at the start of each
        call. Default (no warnings supported by this backend): return [].
        """
        return []


class CapacityProvider(Protocol):
    def free_space(self, path: str) -> Capacity: ...


class CopyEngine(Protocol):
    kind: str  # "robocopy" | "shutil"

    def copy(self, source: str, destination: str) -> None:
        """Copy `source` (a directory) to `destination`. Must raise
        storops.core.errors.StoropsError (or a subclass) on failure;
        callers are responsible for verifying file-count/byte-count
        afterwards via core.copystats.dir_stats -- this method's job is
        only the copy itself.
        """
        ...


class LinkEngine(Protocol):
    kind: str  # "junction" (Windows) | "symlink" (Linux/macOS)

    def create(self, old_path: str, target: str) -> None:
        """Create a link at `old_path` pointing at `target`, replacing
        whatever (nothing -- the caller has already removed the verified-
        copied original) was there.
        """
        ...

    def verify(self, old_path: str, expected_target: str) -> bool:
        """True if `old_path` exists and resolves to `expected_target`."""
        ...


def _system() -> str:
    return _platform.system()  # "Windows" | "Darwin" | "Linux" | ...


def get_scan_backend() -> ScanBackend:
    if _system() == "Windows":
        from storops.platform.windows.scan import get_windows_scan_backend

        return get_windows_scan_backend()

    from storops.platform.backends import du as du_backend
    from storops.platform.backends import gdu as gdu_backend

    if gdu_backend.is_available():
        return gdu_backend.GduBackend()
    return du_backend.DuBackend()


def get_capacity_provider() -> CapacityProvider:
    if _system() == "Windows":
        from storops.platform.windows.scan import WindowsCapacityProvider

        return WindowsCapacityProvider()
    from storops.platform.posix import PosixCapacityProvider

    return PosixCapacityProvider()


def get_copy_engine() -> CopyEngine:
    if _system() == "Windows":
        from storops.platform.windows.copy import RobocopyEngine

        return RobocopyEngine()
    from storops.platform.posix import ShutilCopyEngine

    return ShutilCopyEngine()


def get_link_engine() -> LinkEngine:
    if _system() == "Windows":
        from storops.platform.windows.link import JunctionLinkEngine

        return JunctionLinkEngine()
    from storops.platform.posix import SymlinkLinkEngine

    return SymlinkLinkEngine()


def get_work_dir() -> str:
    """Per-user scratch directory for generated plan/result JSON files.
    Mirrors Common.psm1's Get-StorOpsWorkDir. Created on demand.
    """
    system = _system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        directory = Path(base) / "StorOps"
    elif system == "Darwin":
        directory = Path.home() / "Library" / "Application Support" / "StorOps"
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        directory = Path(base) / "storops"
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def guess_process_running(application_name: str | None) -> str:
    """Best-effort, non-authoritative process-name probe -- mirrors
    migrate-plan.ps1's own disclaimer: this is a hint only.
    migrate-execute.ps1's -AppClosed / --app-closed flag is always
    required whenever a plan's requires_app_closed is true, regardless of
    what this returns.
    """
    if not application_name:
        return "unknown"

    candidates = [application_name, application_name.replace(" ", "")]
    try:
        if _system() == "Windows":
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {candidates[0]}*"],
                capture_output=True, text=True, timeout=5,
            )
            found = candidates[0].lower() in out.stdout.lower()
        else:
            pgrep = shutil.which("pgrep")
            if not pgrep:
                return "unknown"
            found = False
            for name in candidates:
                result = subprocess.run([pgrep, "-if", name], capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    found = True
                    break
        return "likely running" if found else "not detected"
    except Exception:
        return "unknown"


def is_admin() -> bool:
    """Windows: local Administrator check via the shell32 API (stdlib
    ctypes, no pywin32 -- see docs/plans/... §2.11a). Linux/macOS: root
    check via os.geteuid(). StorOps never requires this and never
    self-elevates; it is only used to annotate scan results / migration
    plans, matching the original Test-StorOpsIsAdmin.
    """
    if _system() == "Windows":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0
