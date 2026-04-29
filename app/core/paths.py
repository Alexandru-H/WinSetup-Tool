"""
Single source of truth for every filesystem path used by WinSetup Tool.

No other module is allowed to resolve paths via __file__, sys._MEIPASS, or
ad-hoc environment hacks — they must import from here.

Contract (CLAUDE.md §7.1):
    IS_FROZEN, IS_PORTABLE   : bool flags for the current runtime.
    RESOURCES_DIR            : read-only bundled assets.
    CONFIG_DIR, LOGS_DIR     : writable user data.
    resource(name)           : resolve an asset by relative path.
    settings_file()          : absolute path of the settings JSON.

Rules baked in (CLAUDE.md §6.1 / §6.2):
    * Asset fallback order is _MEIPASS -> exe dir -> script dir.
    * Writable-dir resolution is a strict 3-branch decision:
          1. portable -> <exe_dir>/config      (if <exe_dir>/portable.txt)
          2. frozen   -> %LOCALAPPDATA%/WinSetupTool/config
          3. dev      -> <project_root>/config
      sys._MEIPASS (read-only) and %TEMP% are never used for writable data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "WinSetupTool"


# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

IS_FROZEN: bool = bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path:
    """Directory of the running executable (frozen) or project root (dev)."""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    # This file lives at <root>/app/core/paths.py — parents[2] is <root>.
    return Path(__file__).resolve().parents[2]


_EXE_DIR: Path = _exe_dir()

# Portable mode is opt-in: user drops an empty `portable.txt` next to the exe.
IS_PORTABLE: bool = (_EXE_DIR / "portable.txt").exists()


# ---------------------------------------------------------------------------
# Read-only resources
# ---------------------------------------------------------------------------

def _resources_dir() -> Path:
    """Resolve the bundled assets root with _MEIPASS -> exe -> script fallback."""
    # 1. PyInstaller onefile extracts data under sys._MEIPASS at startup.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "app" / "resources"
        if candidate.exists():
            return candidate
        flat = Path(meipass) / "resources"
        if flat.exists():
            return flat

    # 2. Next to the executable (onedir builds or manual layouts).
    beside_exe = _EXE_DIR / "app" / "resources"
    if beside_exe.exists():
        return beside_exe

    # 3. Dev fallback: <root>/app/resources.
    return Path(__file__).resolve().parents[1] / "resources"


RESOURCES_DIR: Path = _resources_dir()


# ---------------------------------------------------------------------------
# Writable directories (config + logs)
# ---------------------------------------------------------------------------

def _user_data_root() -> Path:
    """Parent folder for config/ and logs/ — resolved per §6.2."""
    # 1. Portable: everything lives next to the exe.
    if IS_PORTABLE:
        return _EXE_DIR

    # 2. Frozen: per-user AppData, namespaced by APP_NAME.
    if IS_FROZEN:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / APP_NAME
        # Defensive fallbacks — LOCALAPPDATA should always be set on Windows,
        # but we refuse to lose data if the env is unusual.
        roaming = os.environ.get("APPDATA")
        if roaming:
            return Path(roaming) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME

    # 3. Dev: project root (gitignored `config/` and `logs/` folders).
    return _EXE_DIR


_USER_DATA_ROOT: Path = _user_data_root()

CONFIG_DIR: Path = _USER_DATA_ROOT / "config"
LOGS_DIR: Path = _USER_DATA_ROOT / "logs"

# Create writable dirs eagerly so downstream modules (settings, logging) can
# write immediately without each one re-implementing mkdir logic. Read-only
# RESOURCES_DIR is never created.
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def resource(name: str) -> Path:
    """Resolve a bundled asset by relative path, e.g. resource('icons/home.svg')."""
    return RESOURCES_DIR / name


def settings_file() -> Path:
    """Absolute path of the settings JSON (may not exist yet)."""
    return CONFIG_DIR / "settings.json"
