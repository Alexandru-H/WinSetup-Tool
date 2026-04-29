"""Windows Explorer & shell tweaks (7 tweaks)."""

from __future__ import annotations

from app.core.logging_setup import get_logger
from app.system.tweaks.registry_helpers import (
    HKCU,
    create_key, key_exists,
    get_dword, set_dword,
)

log = get_logger(__name__)

_PATH_ADVANCED        = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_PATH_PERSONALIZE     = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_PATH_EXPLORER_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\Explorer"


# ── show_extensions ───────────────────────────────────────────────────────────

def apply_show_extensions() -> bool:
    log.info("Applying: show_extensions")
    return set_dword(HKCU, _PATH_ADVANCED, "HideFileExt", 0)


def check_show_extensions() -> bool:
    return get_dword(HKCU, _PATH_ADVANCED, "HideFileExt") == 0


# ── show_hidden_files ─────────────────────────────────────────────────────────

def apply_show_hidden_files() -> bool:
    log.info("Applying: show_hidden_files")
    return set_dword(HKCU, _PATH_ADVANCED, "Hidden", 1)


def check_show_hidden_files() -> bool:
    return get_dword(HKCU, _PATH_ADVANCED, "Hidden") == 1


# ── dark_mode_default ─────────────────────────────────────────────────────────

def apply_dark_mode_default() -> bool:
    log.info("Applying: dark_mode_default")
    ok1 = set_dword(HKCU, _PATH_PERSONALIZE, "AppsUseLightTheme", 0)
    ok2 = set_dword(HKCU, _PATH_PERSONALIZE, "SystemUsesLightTheme", 0)
    return ok1 and ok2


def check_dark_mode_default() -> bool:
    apps   = get_dword(HKCU, _PATH_PERSONALIZE, "AppsUseLightTheme")
    system = get_dword(HKCU, _PATH_PERSONALIZE, "SystemUsesLightTheme")
    return apps == 0 and system == 0


# ── classic_context_menu (Win 11 only) ───────────────────────────────────────
_PATH_CLASSIC_CTX = (
    r"Software\Classes\CLSID"
    r"\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
)


def apply_classic_context_menu() -> bool:
    log.info("Applying: classic_context_menu")
    return create_key(HKCU, _PATH_CLASSIC_CTX)


def check_classic_context_menu() -> bool:
    return key_exists(HKCU, _PATH_CLASSIC_CTX)


# ── disable_search_suggestions ────────────────────────────────────────────────

def apply_disable_search_suggestions() -> bool:
    log.info("Applying: disable_search_suggestions")
    return set_dword(HKCU, _PATH_EXPLORER_POLICY, "DisableSearchBoxSuggestions", 1)


def check_disable_search_suggestions() -> bool:
    return get_dword(HKCU, _PATH_EXPLORER_POLICY, "DisableSearchBoxSuggestions") == 1


# ── small_taskbar_icons (Win 10 only) ────────────────────────────────────────

def apply_small_taskbar_icons() -> bool:
    log.info("Applying: small_taskbar_icons")
    return set_dword(HKCU, _PATH_ADVANCED, "TaskbarSmallIcons", 1)


def check_small_taskbar_icons() -> bool:
    return get_dword(HKCU, _PATH_ADVANCED, "TaskbarSmallIcons") == 1


# ── explorer_open_thispc ──────────────────────────────────────────────────────

def apply_explorer_open_thispc() -> bool:
    log.info("Applying: explorer_open_thispc")
    return set_dword(HKCU, _PATH_ADVANCED, "LaunchTo", 1)


def check_explorer_open_thispc() -> bool:
    return get_dword(HKCU, _PATH_ADVANCED, "LaunchTo") == 1
