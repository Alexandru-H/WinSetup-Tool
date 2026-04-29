"""Privacy & telemetry tweaks for Windows (8 tweaks, all recommended)."""

from __future__ import annotations

from app.core.logging_setup import get_logger
from app.system.tweaks.registry_helpers import (
    HKCU, HKLM,
    get_dword, set_dword,
    get_string, set_string,
)

log = get_logger(__name__)


# ── disable_telemetry ────────────────────────────────────────────────────────
_PATH_TELEMETRY = r"SOFTWARE\Policies\Microsoft\Windows\DataCollection"


def apply_disable_telemetry() -> bool:
    log.info("Applying: disable_telemetry")
    return set_dword(HKLM, _PATH_TELEMETRY, "AllowTelemetry", 0)


def check_disable_telemetry() -> bool:
    return get_dword(HKLM, _PATH_TELEMETRY, "AllowTelemetry") == 0


# ── disable_cortana ──────────────────────────────────────────────────────────
_PATH_CORTANA = r"SOFTWARE\Policies\Microsoft\Windows\Windows Search"


def apply_disable_cortana() -> bool:
    log.info("Applying: disable_cortana")
    return set_dword(HKLM, _PATH_CORTANA, "AllowCortana", 0)


def check_disable_cortana() -> bool:
    return get_dword(HKLM, _PATH_CORTANA, "AllowCortana") == 0


# ── disable_bing_search ──────────────────────────────────────────────────────
_PATH_SEARCH = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Search"


def apply_disable_bing_search() -> bool:
    log.info("Applying: disable_bing_search")
    return set_dword(HKCU, _PATH_SEARCH, "BingSearchEnabled", 0)


def check_disable_bing_search() -> bool:
    return get_dword(HKCU, _PATH_SEARCH, "BingSearchEnabled") == 0


# ── disable_web_search ───────────────────────────────────────────────────────
_PATH_EXPLORER_POLICY = r"SOFTWARE\Policies\Microsoft\Windows\Explorer"


def apply_disable_web_search() -> bool:
    log.info("Applying: disable_web_search")
    return set_dword(HKCU, _PATH_EXPLORER_POLICY, "DisableSearchBoxSuggestions", 1)


def check_disable_web_search() -> bool:
    return get_dword(HKCU, _PATH_EXPLORER_POLICY, "DisableSearchBoxSuggestions") == 1


# ── disable_advertising_id ───────────────────────────────────────────────────
_PATH_AD_ID = r"SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo"


def apply_disable_advertising_id() -> bool:
    log.info("Applying: disable_advertising_id")
    return set_dword(HKCU, _PATH_AD_ID, "Enabled", 0)


def check_disable_advertising_id() -> bool:
    return get_dword(HKCU, _PATH_AD_ID, "Enabled") == 0


# ── disable_activity_history ─────────────────────────────────────────────────
_PATH_ACTIVITY = r"SOFTWARE\Policies\Microsoft\Windows\System"


def apply_disable_activity_history() -> bool:
    log.info("Applying: disable_activity_history")
    return set_dword(HKLM, _PATH_ACTIVITY, "PublishUserActivities", 0)


def check_disable_activity_history() -> bool:
    return get_dword(HKLM, _PATH_ACTIVITY, "PublishUserActivities") == 0


# ── disable_location ─────────────────────────────────────────────────────────
_PATH_LOCATION = (
    r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\location"
)


def apply_disable_location() -> bool:
    log.info("Applying: disable_location")
    return set_string(HKLM, _PATH_LOCATION, "Value", "Deny")


def check_disable_location() -> bool:
    return get_string(HKLM, _PATH_LOCATION, "Value") == "Deny"


# ── disable_feedback ─────────────────────────────────────────────────────────
_PATH_FEEDBACK = r"SOFTWARE\Microsoft\Siuf\Rules"


def apply_disable_feedback() -> bool:
    log.info("Applying: disable_feedback")
    return set_dword(HKCU, _PATH_FEEDBACK, "NumberOfSIUFInPeriod", 0)


def check_disable_feedback() -> bool:
    return get_dword(HKCU, _PATH_FEEDBACK, "NumberOfSIUFInPeriod") == 0
