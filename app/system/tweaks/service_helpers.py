"""Service helpers for Windows tweaks.

Wraps subprocess calls to sc.exe for service management. All functions
log and return bool (success).
"""

from __future__ import annotations

import subprocess

from app.core.logging_setup import get_logger

log = get_logger(__name__)

try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    CREATE_NO_WINDOW = 0   # Non-Windows fallback


def stop_service(name: str) -> bool:
    """Stop a service. Returns True on success or if already stopped."""
    try:
        result = subprocess.run(
            ["sc", "stop", name],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        # 1062 = ERROR_SERVICE_NOT_ACTIVE — already stopped, treat as success
        if result.returncode == 0 or "1062" in result.stdout:
            return True
        log.warning(
            "stop_service %s: rc=%d %s", name, result.returncode,
            result.stdout.strip(),
        )
        return False
    except Exception as e:
        log.error("stop_service %s: %s", name, e)
        return False


def disable_service(name: str) -> bool:
    """Set service start type to Disabled."""
    try:
        result = subprocess.run(
            ["sc", "config", name, "start=", "disabled"],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True
        log.warning("disable_service %s: rc=%d", name, result.returncode)
        return False
    except Exception as e:
        log.error("disable_service %s: %s", name, e)
        return False


def get_service_start_type(name: str) -> str | None:
    """Return 'auto', 'manual', 'disabled', or None if service not found."""
    try:
        result = subprocess.run(
            ["sc", "qc", name],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.upper()
        if "DISABLED" in out:
            return "disabled"
        if "AUTO_START" in out:
            return "auto"
        if "DEMAND_START" in out:
            return "manual"
        return None
    except Exception as e:
        log.warning("get_service_start_type %s: %s", name, e)
        return None


def is_service_running(name: str) -> bool:
    """Return True if the service is currently in RUNNING state."""
    try:
        result = subprocess.run(
            ["sc", "query", name],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and "RUNNING" in result.stdout.upper()
    except Exception:
        return False


def disable_and_stop(name: str) -> bool:
    """Convenience: disable the service AND stop it if running."""
    ok_disable = disable_service(name)
    ok_stop    = stop_service(name)
    return ok_disable and ok_stop
