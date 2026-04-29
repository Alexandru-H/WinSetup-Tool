"""
winget.py — Windows Package Manager detection, bootstrap, and install driver.

Contract (CLAUDE.md §4.1, §4.7):
    * ZERO imports from PySide6 or any UI module. This module is testable in
      a headless environment.
    * Detection (detect_winget, winget_version) is strictly read-only and has
      no observable side effects.
    * bootstrap_winget modifies the OS (installs MSIX packages). Requires admin
      elevation — caller is responsible for ensuring it before calling.
    * install_app streams parsed ProgressEvent objects via an on_progress
      callback. The callback runs on the caller's thread; keep it lightweight.
    * All subprocess calls use CREATE_NO_WINDOW (or 0 on non-Windows) to
      suppress console flash in GUI context.
    * Internet calls use stdlib urllib only — no third-party requests.

Bootstrap strategy (hybrid):
    1. GitHub API   — query microsoft/winget-cli latest release (10 s timeout).
       On failure (timeout, rate-limit, parse error) fall through to step 2.
    2. Hardcoded fallback URLs (module-level constants; bump at each release).
    Install order is mandatory: VCLibs 14 ->UI.Xaml 2.8 ->App Installer.

Run standalone for a smoke test (detection + optional install):
    python -m app.system.winget
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.logging_setup import get_logger
from app.core.paths import CONFIG_DIR as _CONFIG_DIR

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Platform constants
# ---------------------------------------------------------------------------

# Suppress the CMD flash that appears whenever a GUI app spawns a subprocess.
# On non-Windows the attribute doesn't exist, so fall back to 0 (no-op).
_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Cache dir for bootstrap downloads — sits alongside config/ and logs/ under
# the user-data root (CONFIG_DIR.parent is _USER_DATA_ROOT).
_BOOTSTRAP_CACHE: Path = _CONFIG_DIR.parent / "cache" / "winget_bootstrap"

# Winget exit codes that are not genuine failures from a UX standpoint.
# 0x8A15002B = 2316632107 — observed "no applicable upgrade" code (winget 1.x)
# 0x8A150109 = 2316632329 — alternative "no applicable upgrade" in some builds
# Both mean the app is already at the latest version.
BENIGN_EXIT_CODES: frozenset[int] = frozenset({0, 0x8A15002B, 0x8A150109})

# Tracks the currently-running install subprocess so terminate_current() can
# kill it from another thread (UI thread on Cancel click). Guarded by a lock
# because install_app runs on a worker thread while terminate_current is
# called from the UI thread.
_current_proc: subprocess.Popen | None = None
_current_proc_lock = threading.Lock()


def terminate_current() -> None:
    """Kill the currently-running install subprocess, if any.

    Safe to call from any thread. No-op when no install is in flight.
    """
    with _current_proc_lock:
        proc = _current_proc

    if proc is None or proc.poll() is not None:
        return

    log.info("Terminating winget subprocess (pid=%d).", proc.pid)
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            log.warning("Subprocess didn't terminate in 2s — killing.")
            proc.kill()
    except Exception as exc:   # noqa: BLE001
        log.warning("Failed to terminate subprocess: %s", exc)

# ---------------------------------------------------------------------------
# Hardcoded fallback URLs
# Bumped manually at each WinSetupV2 release. See:
# https://github.com/microsoft/winget-cli/releases
# ---------------------------------------------------------------------------

FALLBACK_WINGET_URL: str = (
    "https://github.com/microsoft/winget-cli/releases/download/"
    "v1.10.390/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
)
FALLBACK_VCLIBS_URL: str = (
    "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx"
)
FALLBACK_UIXAML_URL: str = (
    "https://github.com/microsoft/microsoft-ui-xaml/releases/download/"
    "v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx"
)

# ---------------------------------------------------------------------------
# Public enums and dataclass
# ---------------------------------------------------------------------------

class WingetStatus(Enum):
    AVAILABLE = "available"   # binary found, --version succeeds
    MISSING   = "missing"     # binary not on PATH
    BROKEN    = "broken"      # binary found but --version fails / times out


class InstallStage(Enum):
    PENDING           = "Pending"
    DOWNLOADING       = "Downloading"
    VERIFYING         = "Verifying"
    INSTALLING        = "Installing"
    DONE              = "Done"
    ALREADY_INSTALLED = "Already installed"
    FAILED            = "Failed"


@dataclass(frozen=True)
class ProgressEvent:
    stage:    InstallStage
    speed:    str | None    # e.g. "12.4 MB/s" — None outside download phase
    percent:  int | None    # 0–100, None when unknown
    raw_line: str           # unparsed winget line, preserved for logs


# ---------------------------------------------------------------------------
# Progress parsing helpers (internal)
# ---------------------------------------------------------------------------

_UNIT_BYTES: dict[str, int] = {
    "B":  1,
    "KB": 1_024,
    "MB": 1_024 ** 2,
    "GB": 1_024 ** 3,
}

# "12.4 MB/s"  or  "512 KB/s"
_RE_SPEED = re.compile(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)/s", re.IGNORECASE)

# "98.5 MB / 102.3 MB"  (downloaded / total — present on progress bar lines)
_RE_XFER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)\s*/\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)",
    re.IGNORECASE,
)


def _parse_progress(line: str) -> ProgressEvent | None:
    """Translate one raw winget output line into a ProgressEvent, or None.

    Processing order matters — failure/done checks come before the generic
    "downloading" check to avoid mis-classifying partial matches.
    """
    lower = line.lower()

    # --- Terminal states (checked first) --------------------------------
    if "installer failed" in lower or "0x80" in lower:
        return ProgressEvent(InstallStage.FAILED, None, None, line.rstrip())

    if "successfully installed" in lower:
        return ProgressEvent(InstallStage.DONE, None, 100, line.rstrip())

    if "already installed" in lower:
        return ProgressEvent(InstallStage.ALREADY_INSTALLED, None, 100, line.rstrip())

    # --- In-progress states ---------------------------------------------
    if "starting package install" in lower or "starting install" in lower:
        return ProgressEvent(InstallStage.INSTALLING, None, None, line.rstrip())

    if "verified installer hash" in lower or "verifying" in lower:
        return ProgressEvent(InstallStage.VERIFYING, None, None, line.rstrip())

    # --- Progress bar line (contains "X MB / Y MB") --------------------
    m_xfer = _RE_XFER.search(line)
    if m_xfer:
        dl_val, dl_unit, tot_val, tot_unit = m_xfer.groups()
        pct: int | None = None
        try:
            dl_bytes  = float(dl_val)  * _UNIT_BYTES[dl_unit.upper()]
            tot_bytes = float(tot_val) * _UNIT_BYTES[tot_unit.upper()]
            if tot_bytes > 0:
                pct = min(100, int((dl_bytes / tot_bytes) * 100))
        except (KeyError, ValueError, ZeroDivisionError):
            pass

        speed: str | None = None
        m_spd = _RE_SPEED.search(line)
        if m_spd:
            speed = f"{m_spd.group(1)} {m_spd.group(2).upper()}/s"

        log.debug("winget progress: pct=%s speed=%s line=%r", pct, speed, line.rstrip())
        return ProgressEvent(InstallStage.DOWNLOADING, speed, pct, line.rstrip())

    # --- "Downloading ..." header (before the progress bar appears) ----
    if "downloading" in lower:
        return ProgressEvent(InstallStage.DOWNLOADING, None, None, line.rstrip())

    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_winget() -> WingetStatus:
    """Probe whether winget is present and functional.

    Returns AVAILABLE, MISSING, or BROKEN. Never raises.
    """
    if not shutil.which("winget"):
        log.info("detect_winget: binary not found on PATH ->MISSING.")
        return WingetStatus.MISSING

    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log.warning("detect_winget: `winget --version` timed out ->BROKEN.")
        return WingetStatus.BROKEN
    except OSError as exc:
        log.warning("detect_winget: OSError ->BROKEN: %s", exc)
        return WingetStatus.BROKEN

    stdout = (result.stdout or "").strip()
    if result.returncode == 0 and stdout.startswith("v"):
        log.info("detect_winget: AVAILABLE (%s).", stdout)
        return WingetStatus.AVAILABLE

    log.warning(
        "detect_winget: exit=%d stdout=%r ->BROKEN.",
        result.returncode, stdout,
    )
    return WingetStatus.BROKEN


def winget_version() -> str | None:
    """Return the installed winget version string (e.g. ``'v1.10.390'``).

    Returns None if winget is absent or non-functional. Never raises.
    """
    if not shutil.which("winget"):
        return None
    try:
        result = subprocess.run(
            ["winget", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    ver = (result.stdout or "").strip()
    return ver if (result.returncode == 0 and ver) else None


# ---------------------------------------------------------------------------
# Bootstrap internals
# ---------------------------------------------------------------------------

def _fetch_latest_winget_url() -> str | None:
    """Query the GitHub API for the latest App Installer msixbundle URL.

    Returns the download URL on success, None on any failure (network
    error, rate-limit, JSON parse error, no matching asset).  Caller
    should fall back to FALLBACK_WINGET_URL.
    """
    api = "https://api.github.com/repos/microsoft/winget-cli/releases/latest"
    try:
        req = urllib.request.Request(
            api,
            headers={
                "Accept":     "application/vnd.github+json",
                "User-Agent": "WinSetupTool/2",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data: dict = json.loads(resp.read().decode())
    except Exception as exc:   # noqa: BLE001 — wide catch by design (network surface)
        log.warning("GitHub API call failed: %s — will use fallback URL.", exc)
        return None

    for asset in data.get("assets", []):
        name: str = asset.get("name", "")
        if name.endswith(".msixbundle"):
            url: str = asset.get("browser_download_url", "")
            log.info("GitHub API: latest msixbundle ->%s", url)
            return url or None

    log.warning("GitHub API: release found but no .msixbundle asset — using fallback.")
    return None


def _download_file(
    url: str,
    dest: Path,
    label: str,
    on_progress: Callable[[ProgressEvent], None] | None,
) -> bool:
    """Download ``url`` to ``dest``.

    Emits DOWNLOADING ProgressEvents via ``on_progress`` if provided.
    Returns True when the downloaded file is non-empty, False on any error.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s from %s", label, url)

    try:
        def _hook(block: int, block_size: int, total: int) -> None:
            if on_progress is None or total <= 0:
                return
            downloaded = block * block_size
            pct = min(100, int((downloaded / total) * 100))
            on_progress(ProgressEvent(
                InstallStage.DOWNLOADING, None, pct,
                f"Downloading {label}: {downloaded // 1024} KB / {total // 1024} KB",
            ))

        urllib.request.urlretrieve(url, str(dest), reporthook=_hook)
    except (urllib.error.URLError, OSError) as exc:
        log.error("Download failed for %s: %s", label, exc)
        return False

    size = dest.stat().st_size
    if size == 0:
        log.error("Download of %s produced an empty file.", label)
        return False

    log.info("Downloaded %s (%.1f KB).", label, size / 1024)
    return True


def _run_add_appx(path: Path, label: str) -> bool:
    """Run ``Add-AppxPackage`` for a single .appx or .msixbundle.

    Returns True on exit code 0, False otherwise (error logged at ERROR).
    """
    cmd = f"Add-AppxPackage -Path '{path}'"
    log.info("Installing %s …", label)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command", cmd],
            capture_output=True, text=True, timeout=120,
            creationflags=_CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log.error("Add-AppxPackage timed out for %s (>120 s).", label)
        return False
    except OSError as exc:
        log.error("OSError running Add-AppxPackage for %s: %s", label, exc)
        return False

    if result.returncode == 0:
        log.info("%s installed OK.", label)
        if result.stdout.strip():
            log.info("stdout: %s", result.stdout.strip())
        return True

    log.error(
        "Add-AppxPackage failed for %s (exit=%d).\nstdout: %s\nstderr: %s",
        label, result.returncode,
        result.stdout.strip(), result.stderr.strip(),
    )
    return False


def _cleanup_bootstrap(cache_dir: Path) -> None:
    """Remove bootstrap temp files. Best-effort — never raises."""
    try:
        shutil.rmtree(cache_dir, ignore_errors=True)
        log.info("bootstrap_winget: temp dir cleaned up.")
    except Exception as exc:   # noqa: BLE001
        log.debug("bootstrap_winget: cleanup warning: %s", exc)


# ---------------------------------------------------------------------------
# Bootstrap (public)
# ---------------------------------------------------------------------------

def bootstrap_winget(
    on_progress: Callable[[ProgressEvent], None] | None = None,
) -> bool:
    """Install winget on machines that lack it (IoT LTSC, etc.).

    Requires admin elevation. Caller must verify with elevation.is_admin()
    before calling — this function does NOT re-launch or prompt.

    Steps:
        1. Resolve App Installer URL via GitHub API (fallback to constant).
        2. Download VCLibs + UI.Xaml + App Installer to a temp cache.
        3. Install in the mandatory sequence; abort on first failure.
        4. Verify with detect_winget().

    Returns True if winget is functional after the call, False otherwise.
    """
    log.info("bootstrap_winget: starting (cache=%s).", _BOOTSTRAP_CACHE)

    winget_url = _fetch_latest_winget_url() or FALLBACK_WINGET_URL
    log.info("bootstrap_winget: App Installer URL ->%s", winget_url)

    vclibs_path      = _BOOTSTRAP_CACHE / "vclibs.appx"
    uixaml_path      = _BOOTSTRAP_CACHE / "uixaml.appx"
    appinstaller_path = _BOOTSTRAP_CACHE / "appinstaller.msixbundle"

    packages = [
        (FALLBACK_VCLIBS_URL, vclibs_path,       "VCLibs 14"),
        (FALLBACK_UIXAML_URL, uixaml_path,       "UI.Xaml 2.8"),
        (winget_url,          appinstaller_path,  "App Installer"),
    ]

    # --- Download phase --------------------------------------------------
    for url, dest, label in packages:
        if not _download_file(url, dest, label, on_progress):
            log.error("bootstrap_winget: download failed for %s — aborting.", label)
            _cleanup_bootstrap(_BOOTSTRAP_CACHE)
            return False

    # --- Install phase (VCLibs ->UI.Xaml ->App Installer) ---------------
    install_steps = [
        (vclibs_path,       "VCLibs 14"),
        (uixaml_path,       "UI.Xaml 2.8"),
        (appinstaller_path, "App Installer"),
    ]
    for path, label in install_steps:
        if on_progress is not None:
            on_progress(ProgressEvent(
                InstallStage.INSTALLING, None, None, f"Installing {label}",
            ))
        if not _run_add_appx(path, label):
            log.error("bootstrap_winget: install failed for %s — aborting.", label)
            _cleanup_bootstrap(_BOOTSTRAP_CACHE)
            return False

    # --- Verify ----------------------------------------------------------
    status = detect_winget()
    _cleanup_bootstrap(_BOOTSTRAP_CACHE)

    if status == WingetStatus.AVAILABLE:
        ver = winget_version()
        log.info("bootstrap_winget: success — winget %s is now available.", ver)
        if on_progress is not None:
            on_progress(ProgressEvent(InstallStage.DONE, None, 100, "winget ready"))
        return True

    log.error(
        "bootstrap_winget: packages installed but detect_winget ->%s.", status
    )
    return False


# ---------------------------------------------------------------------------
# Single-app install with progress streaming
# ---------------------------------------------------------------------------

def install_app(
    winget_id: str,
    on_progress: Callable[[ProgressEvent], None],
) -> bool:
    """Run ``winget install --id <winget_id>`` and stream parsed progress.

    The on_progress callback is called synchronously on the caller's thread
    for each recognized output line plus a guaranteed final DONE or FAILED
    event.

    Returns True if winget exits with code 0, False otherwise.
    """
    if detect_winget() != WingetStatus.AVAILABLE:
        log.error(
            "install_app: winget not available — cannot install %s.", winget_id
        )
        on_progress(ProgressEvent(
            InstallStage.FAILED, None, None,
            f"winget not available ({winget_id})",
        ))
        return False

    cmd = [
        "winget", "install",
        "--id", winget_id,
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    log.info("install_app: %s — launching: %s", winget_id, " ".join(cmd))
    on_progress(ProgressEvent(InstallStage.PENDING, None, None,
                              f"Starting install for {winget_id}"))

    global _current_proc

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError as exc:
        log.error("install_app: failed to start winget: %s", exc)
        on_progress(ProgressEvent(InstallStage.FAILED, None, None, str(exc)))
        return False

    assert proc.stdout is not None  # guaranteed by stdout=PIPE

    with _current_proc_lock:
        _current_proc = proc

    last_stage: InstallStage | None = None
    try:
        for raw_line in proc.stdout:
            log.debug("winget[%s] ->%r", winget_id, raw_line.rstrip())
            evt = _parse_progress(raw_line)
            if evt is not None:
                on_progress(evt)
                last_stage = evt.stage

        returncode = proc.wait()
    finally:
        with _current_proc_lock:
            _current_proc = None

    # Guarantee a terminal event regardless of what the parser saw.
    # returncode == 0                     → clean install
    # returncode == 0x8A150109            → already at latest version (benign)
    # anything else                       → genuine failure
    _terminal = (InstallStage.DONE, InstallStage.ALREADY_INSTALLED, InstallStage.FAILED)

    if last_stage not in _terminal:
        if returncode == 0:
            log.info("install_app: %s done (exit=0).", winget_id)
            on_progress(ProgressEvent(InstallStage.DONE, None, 100,
                                      "Installation complete"))
        elif returncode in BENIGN_EXIT_CODES:
            log.info(
                "install_app: %s already at latest version (exit=0x%08X).",
                winget_id, returncode,
            )
            on_progress(ProgressEvent(InstallStage.ALREADY_INSTALLED, None, 100,
                                      "App already at latest version"))
        else:
            log.error("install_app: %s failed (exit=%d).", winget_id, returncode)
            on_progress(ProgressEvent(InstallStage.FAILED, None, None,
                                      f"winget exited {returncode}"))
    elif returncode not in BENIGN_EXIT_CODES and last_stage == InstallStage.DONE:
        # Rare: winget printed "Successfully installed" but process exited non-zero.
        # Trust the exit code — emit a correcting FAILED event.
        log.warning(
            "install_app: %s — output said DONE but exit=%d; overriding to FAILED.",
            winget_id, returncode,
        )
        on_progress(ProgressEvent(InstallStage.FAILED, None, None,
                                  f"exit code {returncode} after reported success"))
        return False

    return returncode in BENIGN_EXIT_CODES


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from app.core.logging_setup import setup_logging
    setup_logging()

    print("=" * 60)
    print("app.system.winget — smoke test")
    print("=" * 60)

    print("\n[1] Detection")
    _status = detect_winget()
    print(f"    detect_winget()  ->{_status}")

    print("\n[2] Version")
    _ver = winget_version()
    print(f"    winget_version() ->{_ver}")

    print("\n[3] Bootstrap")
    print("    SKIPPED — destructive; run manually on an LTSC machine only.")

    print("\n[4] Install (7-Zip)")
    if _status != WingetStatus.AVAILABLE:
        print(f"    SKIPPED — winget not available ({_status}).")
    else:
        print("    Installing 7zip.7zip — progress events:")

        def _cb(evt: ProgressEvent) -> None:
            pct = f"{evt.percent}%" if evt.percent is not None else "  -"
            spd = evt.speed or "          -"
            print(f"    {evt.stage.value:<12}  {pct:<5}  {spd}")

        _ok = install_app("7zip.7zip", _cb)
        print(f"\n    install_app() ->{'OK' if _ok else 'FAILED'}")

    print("\n" + "=" * 60)
