"""Performance & power tweaks for Windows (6 tweaks)."""

from __future__ import annotations

import subprocess

from app.core.logging_setup import get_logger
from app.system.tweaks.registry_helpers import (
    HKCU, HKLM,
    get_dword, set_dword,
)
from app.system.tweaks.service_helpers import disable_and_stop, get_service_start_type

log = get_logger(__name__)

try:
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    _NO_WINDOW = 0  # Non-Windows fallback

_GUID_HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


# ── power_high_performance ────────────────────────────────────────────────────

def apply_power_high_performance() -> bool:
    log.info("Applying: power_high_performance")
    try:
        r = subprocess.run(
            ["powercfg", "/setactive", _GUID_HIGH_PERF],
            capture_output=True, text=True, timeout=15,
            creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            return True
        log.error("power_high_performance failed: rc=%d %s", r.returncode, r.stderr.strip())
        return False
    except Exception as e:
        log.error("power_high_performance: %s", e)
        return False


def check_power_high_performance() -> bool:
    try:
        r = subprocess.run(
            ["powercfg", "/getactivescheme"],
            capture_output=True, text=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
        return _GUID_HIGH_PERF in r.stdout.lower()
    except Exception:
        return False


# ── disable_visual_effects ────────────────────────────────────────────────────
_PATH_VISUAL_FX = r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"


def apply_disable_visual_effects() -> bool:
    log.info("Applying: disable_visual_effects")
    return set_dword(HKCU, _PATH_VISUAL_FX, "VisualFXSetting", 2)


def check_disable_visual_effects() -> bool:
    return get_dword(HKCU, _PATH_VISUAL_FX, "VisualFXSetting") == 2


# ── disable_hibernation ───────────────────────────────────────────────────────
_PATH_POWER = r"SYSTEM\CurrentControlSet\Control\Power"


def apply_disable_hibernation() -> bool:
    log.info("Applying: disable_hibernation")
    try:
        r = subprocess.run(
            ["powercfg", "/hibernate", "off"],
            capture_output=True, text=True, timeout=15,
            creationflags=_NO_WINDOW,
        )
        if r.returncode == 0:
            return True
        log.error("disable_hibernation failed: rc=%d %s", r.returncode, r.stderr.strip())
        return False
    except Exception as e:
        log.error("disable_hibernation: %s", e)
        return False


def check_disable_hibernation() -> bool:
    return get_dword(HKLM, _PATH_POWER, "HibernateEnabled") == 0


# ── disable_sysmain ───────────────────────────────────────────────────────────

def apply_disable_sysmain() -> bool:
    log.info("Applying: disable_sysmain")
    return disable_and_stop("SysMain")


def check_disable_sysmain() -> bool:
    return get_service_start_type("SysMain") == "disabled"


# ── disable_xbox_gamebar ──────────────────────────────────────────────────────
_PATH_GAMEBAR = r"SOFTWARE\Microsoft\GameBar"


def apply_disable_xbox_gamebar() -> bool:
    log.info("Applying: disable_xbox_gamebar")
    ok1 = set_dword(HKCU, _PATH_GAMEBAR, "AppCaptureEnabled", 0)
    ok2 = set_dword(HKCU, _PATH_GAMEBAR, "AllowGameDVR", 0)
    return ok1 and ok2


def check_disable_xbox_gamebar() -> bool:
    capture = get_dword(HKCU, _PATH_GAMEBAR, "AppCaptureEnabled")
    dvr     = get_dword(HKCU, _PATH_GAMEBAR, "AllowGameDVR")
    return capture == 0 and dvr == 0


# ── gpu_hw_scheduling ─────────────────────────────────────────────────────────
_PATH_GFX = r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers"


def apply_gpu_hw_scheduling() -> bool:
    log.info("Applying: gpu_hw_scheduling")
    return set_dword(HKLM, _PATH_GFX, "HwSchMode", 2)


def check_gpu_hw_scheduling() -> bool:
    return get_dword(HKLM, _PATH_GFX, "HwSchMode") == 2
