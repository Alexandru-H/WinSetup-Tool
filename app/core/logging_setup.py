"""
Root logger bootstrap — the very first thing main.py calls.

Contract (CLAUDE.md §3, §14):
    * RotatingFileHandler at LOGS_DIR/winsetup.log (5 MB x 3 backups).
    * DEBUG level in dev, INFO level in frozen builds.
    * Console handler (stdout) only in dev; frozen builds log to file only,
      with a stderr safety net if the file handler cannot be created.
    * setup_logging() is idempotent — safe to call multiple times.
    * File-handler failure is caught: the app keeps running, using whatever
      console/stderr handler is available.

All callers should use get_logger(__name__); no module should call
logging.basicConfig or attach its own handlers to the root logger.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.paths import IS_FROZEN, LOGS_DIR

_LOG_FILENAME = "winsetup.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

_configured: bool = False


def setup_logging() -> None:
    """Configure the root logger. Idempotent."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    level = logging.INFO if IS_FROZEN else logging.DEBUG
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    # Console handler — dev only. Frozen builds stay silent on stdout.
    if not IS_FROZEN:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    # File handler — best effort. A locked file, a full disk, or unusual
    # permissions must not take the app down.
    log_path = LOGS_DIR / _LOG_FILENAME
    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # Ensure something still catches logs when file output is unavailable,
        # especially in frozen mode where the dev console handler is absent.
        if not root.handlers:
            fallback = logging.StreamHandler(sys.stderr)
            fallback.setLevel(level)
            fallback.setFormatter(formatter)
            root.addHandler(fallback)
        logging.getLogger(__name__).warning(
            "File logging disabled at %s (%s) — continuing without file output.",
            log_path,
            exc,
        )

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Thin wrapper over logging.getLogger — keeps imports uniform across the app."""
    return logging.getLogger(name)
