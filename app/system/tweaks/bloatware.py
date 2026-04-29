"""Bloatware & content delivery tweaks for Windows (7 tweaks)."""

from __future__ import annotations

from app.core.logging_setup import get_logger
from app.system.tweaks.registry_helpers import (
    HKCU, HKLM,
    get_dword, set_dword,
)

log = get_logger(__name__)


# ── disable_consumer_features ────────────────────────────────────────────────
_PATH_CLOUD_CONTENT = r"SOFTWARE\Policies\Microsoft\Windows\CloudContent"


def apply_disable_consumer_features() -> bool:
    log.info("Applying: disable_consumer_features")
    return set_dword(HKLM, _PATH_CLOUD_CONTENT, "DisableWindowsConsumerFeatures", 1)


def check_disable_consumer_features() -> bool:
    return get_dword(HKLM, _PATH_CLOUD_CONTENT, "DisableWindowsConsumerFeatures") == 1


# ── disable_lock_screen_ads ──────────────────────────────────────────────────
_PATH_CDM = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"


def apply_disable_lock_screen_ads() -> bool:
    log.info("Applying: disable_lock_screen_ads")
    return set_dword(HKCU, _PATH_CDM, "RotatingLockScreenOverlayEnabled", 0)


def check_disable_lock_screen_ads() -> bool:
    return get_dword(HKCU, _PATH_CDM, "RotatingLockScreenOverlayEnabled") == 0


# ── disable_start_suggestions ────────────────────────────────────────────────

def apply_disable_start_suggestions() -> bool:
    log.info("Applying: disable_start_suggestions")
    return set_dword(HKCU, _PATH_CDM, "SubscribedContent-338388Enabled", 0)


def check_disable_start_suggestions() -> bool:
    return get_dword(HKCU, _PATH_CDM, "SubscribedContent-338388Enabled") == 0


# ── disable_settings_suggestions ─────────────────────────────────────────────

def apply_disable_settings_suggestions() -> bool:
    log.info("Applying: disable_settings_suggestions")
    return set_dword(HKCU, _PATH_CDM, "SubscribedContent-338393Enabled", 0)


def check_disable_settings_suggestions() -> bool:
    return get_dword(HKCU, _PATH_CDM, "SubscribedContent-338393Enabled") == 0


# ── disable_news_widgets ──────────────────────────────────────────────────────
_PATH_FEEDS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds"


def apply_disable_news_widgets() -> bool:
    log.info("Applying: disable_news_widgets")
    return set_dword(HKCU, _PATH_FEEDS, "ShellFeedsTaskbarViewMode", 2)


def check_disable_news_widgets() -> bool:
    return get_dword(HKCU, _PATH_FEEDS, "ShellFeedsTaskbarViewMode") == 2


# ── disable_tips ──────────────────────────────────────────────────────────────

def apply_disable_tips() -> bool:
    log.info("Applying: disable_tips")
    return set_dword(HKCU, _PATH_CDM, "SubscribedContent-338389Enabled", 0)


def check_disable_tips() -> bool:
    return get_dword(HKCU, _PATH_CDM, "SubscribedContent-338389Enabled") == 0


# ── disable_spotlight ─────────────────────────────────────────────────────────

def apply_disable_spotlight() -> bool:
    log.info("Applying: disable_spotlight")
    return set_dword(HKCU, _PATH_CDM, "RotatingLockScreenEnabled", 0)


def check_disable_spotlight() -> bool:
    return get_dword(HKCU, _PATH_CDM, "RotatingLockScreenEnabled") == 0
