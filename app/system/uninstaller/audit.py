"""Append-only audit log of uninstall and cleanup operations.

Format: one JSON object per line (JSONL), UTF-8.
Location: <user_data_root>/uninstall_history.log

Each line:
    {"ts": "2026-04-28T15:30:42", "action": "uninstall_done",
     "app": "Notepad++", "publisher": "Notepad++ Team",
     "result": "success"}
    {"ts": "...", "action": "cleanup_folder",
     "app": "Notepad++", "path": "...", "size_kb": 12,
     "result": "success"}

Recognised action values:
    uninstall_start, uninstall_done, uninstall_failed,
    cleanup_folder, cleanup_file, cleanup_regkey, cleanup_failed.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import paths
from app.core.logging_setup import get_logger

log = get_logger(__name__)


def _audit_path() -> Path:
    """Return path to uninstall_history.log in the user data root."""
    base = paths.CONFIG_DIR.parent   # e.g. %LOCALAPPDATA%\WinSetupTool\
    base.mkdir(parents=True, exist_ok=True)
    return base / "uninstall_history.log"


def write_event(action: str, app_name: str, **fields: Any) -> None:
    """Append a single event to the audit log.

    Never raises — audit failures must not interrupt uninstall flow.
    """
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "app": app_name,
        **fields,
    }
    try:
        path = _audit_path()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Audit write failed: %s", e)


def read_recent(limit: int = 50) -> list[dict]:
    """Return the last *limit* events. Returns [] on any error."""
    try:
        path = _audit_path()
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        events = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
    except Exception as e:
        log.warning("Audit read failed: %s", e)
        return []
