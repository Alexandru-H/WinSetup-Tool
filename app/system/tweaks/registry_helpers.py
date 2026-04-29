"""Registry helpers for Windows tweaks.

Wraps winreg with safe defaults and consistent error handling. All
functions log at appropriate levels and return bool (success) so
tweaks can chain results without try/except boilerplate.
"""

from __future__ import annotations

import winreg

from app.core.logging_setup import get_logger

log = get_logger(__name__)

# Hive shortcuts
HKCU = winreg.HKEY_CURRENT_USER
HKLM = winreg.HKEY_LOCAL_MACHINE


def set_dword(hive: int, path: str, name: str, value: int) -> bool:
    """Create or update a DWORD value. Creates the path if missing."""
    try:
        with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
        return True
    except OSError as e:
        log.error("set_dword failed (%s\\%s = %d): %s", path, name, value, e)
        return False


def get_dword(hive: int, path: str, name: str) -> int | None:
    """Read a DWORD value. Returns None if path or name doesn't exist."""
    try:
        with winreg.OpenKeyEx(hive, path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return int(value)
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("get_dword failed (%s\\%s): %s", path, name, e)
        return None


def set_string(hive: int, path: str, name: str, value: str) -> bool:
    """Create or update a REG_SZ value."""
    try:
        with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        return True
    except OSError as e:
        log.error("set_string failed (%s\\%s = %r): %s", path, name, value, e)
        return False


def get_string(hive: int, path: str, name: str) -> str | None:
    """Read a REG_SZ value. Returns None if path or name doesn't exist."""
    try:
        with winreg.OpenKeyEx(hive, path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def delete_value(hive: int, path: str, name: str) -> bool:
    """Delete a single value from a key. Returns True if deleted or already absent."""
    try:
        with winreg.OpenKeyEx(hive, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
        return True
    except FileNotFoundError:
        return True   # Already gone — treat as success
    except OSError as e:
        log.error("delete_value failed (%s\\%s): %s", path, name, e)
        return False


def create_key(hive: int, path: str) -> bool:
    """Create an empty registry key (and all intermediate keys)."""
    try:
        with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE):
            pass
        return True
    except OSError as e:
        log.error("create_key failed (%s): %s", path, e)
        return False


def key_exists(hive: int, path: str) -> bool:
    """Return True if the registry key exists (regardless of values)."""
    try:
        with winreg.OpenKeyEx(hive, path, 0, winreg.KEY_READ):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False
