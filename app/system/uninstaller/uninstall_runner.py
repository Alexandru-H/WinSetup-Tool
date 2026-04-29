"""Execute an app's UninstallString and wait for completion.

Strategy:
    1. Prefer QuietUninstallString when available (/S, /silent, etc.).
    2. Fall back to UninstallString.
    3. MsiExec strings are handled via shell=True after patching
       /I → /X and appending /quiet /norestart.
    4. Other strings are parsed with shlex; shell=True is the fallback
       for strings that shlex cannot split.
    5. Waits for process exit with configurable timeout.

Cancellation: terminate_current() can be called from any thread to
kill the running subprocess — same pattern as the winget worker.
"""

from __future__ import annotations

import shlex
import subprocess
import threading

from app.core.logging_setup import get_logger
from app.system.uninstaller.scanner import InstalledApp

log = get_logger(__name__)

try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    CREATE_NO_WINDOW = 0

_current_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def _build_command(app: InstalledApp) -> list[str] | str | None:
    """Choose and normalise the best uninstall command.

    Returns a list (for direct Popen) or a string (for shell=True),
    or None if neither uninstall string is set.
    """
    cmd_str = (app.quiet_uninstall_string or app.uninstall_string).strip()
    if not cmd_str:
        return None

    # MsiExec: patch /I{GUID} → /X{GUID} and add silent flags.
    if "msiexec" in cmd_str.lower():
        cmd_str = cmd_str.replace("/I{", "/X{").replace("/i{", "/X{")
        lower = cmd_str.lower()
        if "/quiet" not in lower and "/qn" not in lower:
            cmd_str += " /quiet /norestart"
        return cmd_str  # shell=True branch

    # Generic exe: parse with shlex; fall back to shell=True on error.
    try:
        return shlex.split(cmd_str, posix=False)
    except ValueError:
        return cmd_str


def run_uninstall(app: InstalledApp, timeout_sec: int = 300) -> bool:
    """Execute the uninstall command. Returns True when exit code is 0.

    Note: some uninstallers return non-zero on partial success or
    "already uninstalled". Callers should verify by re-scanning.
    """
    global _current_proc

    cmd = _build_command(app)
    if cmd is None:
        log.error("No UninstallString for: %s", app.display_name)
        return False

    log.info("Running uninstall for: %s", app.display_name)
    log.debug("Command: %r", cmd)

    try:
        if isinstance(cmd, str):
            proc = subprocess.Popen(
                cmd, shell=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                creationflags=CREATE_NO_WINDOW,
            )

        with _lock:
            _current_proc = proc

        try:
            rc = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            log.warning("Uninstall timed out: %s", app.display_name)
            proc.kill()
            return False
        finally:
            with _lock:
                _current_proc = None

        log.info("Uninstall completed (rc=%d): %s", rc, app.display_name)
        return rc == 0

    except Exception as e:
        log.exception("Uninstall exception for %s: %s", app.display_name, e)
        with _lock:
            _current_proc = None
        return False


def terminate_current() -> None:
    """Kill the currently-running uninstall subprocess. Thread-safe."""
    with _lock:
        if _current_proc is not None and _current_proc.poll() is None:
            log.info("Terminating uninstall subprocess.")
            try:
                _current_proc.terminate()
                try:
                    _current_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _current_proc.kill()
            except Exception as e:
                log.warning("Failed to terminate subprocess: %s", e)
