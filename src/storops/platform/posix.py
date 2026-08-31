"""Linux/macOS platform mechanics: capacity, copy, and link engines.

See platform/base.py's module docstring for why Linux and macOS share this
one module instead of two near-duplicate files -- shutil.disk_usage,
shutil.copytree, and os.symlink all behave identically on both. Only
core/rules.py's %TOKEN% table (see core/rules.py's _platform_tokens)
actually differs between Linux and macOS.
"""
from __future__ import annotations

import os
import shutil

from storops.core.errors import StoropsError
from storops.core.models import Capacity


class PosixCapacityProvider:
    """CapacityProvider for Linux/macOS via stdlib shutil.disk_usage.

    No need to shell out to `df` -- shutil.disk_usage() already wraps the
    statvfs(2)/GetDiskFreeSpaceExW syscall on every platform Python supports.
    """

    def free_space(self, path: str) -> Capacity:
        usage = shutil.disk_usage(path)
        return Capacity(
            drive=self._mount_point(path),
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_bytes=usage.used,
            volume_name=None,
            # Matches the original PowerShell version, which also left
            # FileSystem=None on POSIX (no cheap stdlib-only way to read the
            # filesystem type without shelling out to `mount`/`diskutil`).
            file_system=None,
        )

    @staticmethod
    def _mount_point(path: str) -> str:
        """Best-effort mount point for `path`, without shelling out.

        Walks up from the resolved path comparing st_dev, stopping at the
        first ancestor whose device differs from its parent's (i.e. the
        first ancestor is where the actual mount boundary is) or at the
        filesystem root. Deliberately not over-engineered -- this is only
        used to fill in the `drive` display field, not for any decision
        logic.
        """
        current = os.path.abspath(path)
        try:
            dev = os.stat(current).st_dev
        except OSError:
            return current
        while True:
            parent = os.path.dirname(current)
            if parent == current:
                return current
            try:
                parent_dev = os.stat(parent).st_dev
            except OSError:
                return current
            if parent_dev != dev:
                return current
            current = parent


class ShutilCopyEngine:
    """CopyEngine for Linux/macOS via stdlib shutil.copytree."""

    kind = "shutil"

    def copy(self, source: str, destination: str) -> None:
        try:
            shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=False)
        except OSError as exc:
            raise StoropsError(
                f"StorOps: failed to copy '{source}' to '{destination}': {exc}"
            ) from exc


class SymlinkLinkEngine:
    """LinkEngine for Linux/macOS via stdlib os.symlink."""

    kind = "symlink"

    def create(self, old_path: str, target: str) -> None:
        os.symlink(target, old_path, target_is_directory=True)

    def verify(self, old_path: str, expected_target: str) -> bool:
        return os.path.islink(old_path) and os.path.realpath(old_path) == os.path.realpath(
            expected_target
        )
