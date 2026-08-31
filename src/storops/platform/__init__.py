"""Platform Abstraction layer -- see base.py for the Protocol contracts and
the factory functions (`get_scan_backend`, `get_capacity_provider`,
`get_copy_engine`, `get_link_engine`, `is_admin`). This is the ONLY place
in StorOps that branches on the running platform; core/ and cli.py never
do (see docs/plans/storops-v2-cross-platform-refactor.md §2.3).
"""
from storops.platform.base import (
    get_capacity_provider,
    get_copy_engine,
    get_link_engine,
    get_scan_backend,
    get_work_dir,
    guess_process_running,
    is_admin,
)

__all__ = [
    "get_capacity_provider",
    "get_copy_engine",
    "get_link_engine",
    "get_scan_backend",
    "get_work_dir",
    "guess_process_running",
    "is_admin",
]
