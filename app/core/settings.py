"""
Settings store — JSON file at CONFIG_DIR/settings.json.

Contract (CLAUDE.md §7.2):
    * Settings(): construct, then call load() once.
    * get(key, default) / set(key, value): in-memory access.
    * set() does NOT persist — callers batch their changes, then call save()
      themselves. This keeps save() calls explicit and allows batching several
      related tweaks into a single disk write.
    * save() is atomic: writes to <path>.tmp and os.replace()s into place,
      so a crash mid-write cannot corrupt the existing settings file.
    * load() is fault-tolerant: missing file, unreadable file, or corrupt
      JSON all yield DEFAULTS with a log warning — the app still starts.
    * Forward-compatibility: keys present in the file but unknown to DEFAULTS
      are preserved on load and round-tripped on save.

The Settings instance is constructed once in main.py and passed explicitly
to consumers — no module-level singleton.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging_setup import get_logger
from app.core.paths import settings_file

log = get_logger(__name__)

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "locale": "ro",
    "accent": "default_dark",
    "first_run": True,
    # Wallpaper page — empty folder means "fall back to
    # wallpaper.default_wallpapers_folder()" at first launch.
    "wallpaper_folder": "",
    "wallpaper_fit_mode": "fill",
}


class Settings:
    """In-memory settings with explicit load/save against settings.json."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = DEFAULTS.copy()

    # ------------------------------------------------------------------ load

    def load(self) -> None:
        """Read settings from disk, merging with DEFAULTS. Never raises."""
        path = settings_file()

        if not path.exists():
            log.info(
                "Settings file not found at %s — using defaults (first run).",
                path,
            )
            self._data = DEFAULTS.copy()
            return

        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "Could not read settings file %s (%s) — falling back to defaults.",
                path,
                exc,
            )
            self._data = DEFAULTS.copy()
            return

        if not isinstance(loaded, dict):
            log.warning(
                "Settings file %s does not contain a JSON object — "
                "falling back to defaults.",
                path,
            )
            self._data = DEFAULTS.copy()
            return

        # Overlay disk values onto defaults: new DEFAULTS keys added in a
        # future version fill themselves in automatically, and unknown keys
        # already on disk pass through untouched.
        merged: dict[str, Any] = DEFAULTS.copy()
        merged.update(loaded)
        self._data = merged

    # ------------------------------------------------------------------ save

    def save(self) -> bool:
        """Persist settings atomically. Returns True on success, False on OSError."""
        path = settings_file()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            payload = json.dumps(self._data, indent=2, ensure_ascii=False)
            with tmp_path.open("w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, path)
            log.debug("Settings saved to %s", path)
            return True
        except OSError as exc:
            log.error("Could not save settings to %s (%s).", path, exc)
            # Best-effort cleanup so failed writes don't leave .tmp litter.
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            return False

    # --------------------------------------------------------------- access

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """In-memory update. Caller must invoke save() to persist."""
        self._data[key] = value

    # ----------------------------------------------------------------- reset

    def reset_to_defaults(self) -> bool:
        """Replace all in-memory values with DEFAULTS and persist.

        Forward-compat keys from disk are intentionally DROPPED here —
        "reset" means "go back to factory settings", which cannot preserve
        user overrides. Returns the save() result.
        """
        self._data = DEFAULTS.copy()
        ok = self.save()
        if ok:
            log.info("Settings reset to defaults.")
        else:
            log.error("Settings reset to defaults in memory, but save() failed.")
        return ok
