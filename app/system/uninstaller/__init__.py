"""Uninstaller subsystem.

Public API:
    scan_installed_apps()   -> list[InstalledApp]
    find_leftovers(app)     -> list[LeftoverItem]
    remove_leftover(item)   -> bool
    run_uninstall(app)      -> bool
    terminate_current()
    audit.write_event(action, app_name, **fields)
    audit.read_recent(limit) -> list[dict]
"""

from __future__ import annotations

from app.system.uninstaller.scanner import InstalledApp, scan_installed_apps
from app.system.uninstaller.leftover_scanner import (
    LeftoverItem,
    find_leftovers,
    remove_leftover,
)
from app.system.uninstaller.uninstall_runner import run_uninstall, terminate_current
from app.system.uninstaller import audit

__all__ = [
    "InstalledApp",
    "scan_installed_apps",
    "LeftoverItem",
    "find_leftovers",
    "remove_leftover",
    "run_uninstall",
    "terminate_current",
    "audit",
]
