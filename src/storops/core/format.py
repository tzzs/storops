"""Human-readable formatting helpers. Ports Common.psm1's Format-StorOpsSize."""
from __future__ import annotations

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(num_bytes: float) -> str:
    """e.g. 93763223245 -> "87.32 GB". Deliberately simple (binary/1024-
    based) since that is what WizTree itself reports.
    """
    value = float(num_bytes)
    unit_index = 0
    while abs(value) >= 1024 and unit_index < len(_UNITS) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{round(value)} {_UNITS[unit_index]}"
    return f"{value:.2f} {_UNITS[unit_index]}"
