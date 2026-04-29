"""
Windows UAC elevation helpers.

Contract:
    * is_elevated() -> bool
        True when the current process has administrator privileges.
        Never raises — any OS/API failure yields False.
    * relaunch_as_admin() -> bool
        Requests Windows to re-launch the current process elevated via a
        UAC prompt. Returns True on successful launch of the elevated
        child (the caller is expected to exit afterwards). Returns False
        if the user cancelled UAC or the API call failed. This function
        does NOT call sys.exit — that decision belongs to main.py.

Pure stdlib + ctypes — no Qt, no dialogs. The "continue without admin?"
question belongs in the UI layer.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys

from app.core.logging_setup import get_logger
from app.core.paths import IS_FROZEN

log = get_logger(__name__)

# ShellExecuteW returns a pseudo-HINSTANCE: any value > 32 means success,
# any value <= 32 is a documented error code (e.g. 5 = user cancelled UAC /
# access denied, 2 = file not found, 3 = path not found).
_SHELL_EXEC_SUCCESS_THRESHOLD = 32
_SW_SHOWNORMAL = 1
_RUNAS_VERB = "runas"


def is_elevated() -> bool:
    """True if the current process is running with administrator rights."""
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError) as exc:
        log.warning("IsUserAnAdmin() failed (%s) — assuming not elevated.", exc)
        return False
    log.debug("is_elevated() -> %s", elevated)
    return elevated


def relaunch_as_admin() -> bool:
    """
    Re-launch this process with a UAC prompt.

    Returns True if the elevated child was successfully started (the caller
    should then exit its non-elevated process). Returns False if the user
    declined the UAC prompt or the API call failed.
    """
    exe = sys.executable
    if IS_FROZEN:
        # sys.executable is already our target; only pass extra CLI args,
        # not argv[0] (which is the exe itself).
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        # Dev mode: python.exe re-runs the whole command line. sys.argv[0]
        # is the script path and must be passed to the interpreter.
        params = subprocess.list2cmdline(sys.argv)

    log.info("Requesting UAC relaunch: %s %s", exe, params)

    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None,             # hwnd
            _RUNAS_VERB,      # lpOperation
            exe,              # lpFile
            params,           # lpParameters
            None,             # lpDirectory — inherit parent CWD
            _SW_SHOWNORMAL,   # nShowCmd
        )
    except (AttributeError, OSError) as exc:
        log.error("ShellExecuteW raised (%s) — relaunch aborted.", exc)
        return False

    if result > _SHELL_EXEC_SUCCESS_THRESHOLD:
        log.info("UAC relaunch accepted (ShellExecuteW returned %s).", result)
        return True

    log.warning(
        "UAC relaunch rejected or failed (ShellExecuteW returned %s — "
        "5 = user cancelled, 2/3 = file/path not found).",
        result,
    )
    return False
