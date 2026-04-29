"""Windows service & network tweaks (4 tweaks)."""

from __future__ import annotations

from app.core.logging_setup import get_logger
from app.system.tweaks.registry_helpers import HKLM, get_dword, set_dword
from app.system.tweaks.service_helpers import disable_and_stop, get_service_start_type

log = get_logger(__name__)


# ── disable_diagtrack ─────────────────────────────────────────────────────────

def apply_disable_diagtrack() -> bool:
    log.info("Applying: disable_diagtrack")
    return disable_and_stop("DiagTrack")


def check_disable_diagtrack() -> bool:
    return get_service_start_type("DiagTrack") == "disabled"


# ── disable_insider_service ───────────────────────────────────────────────────

def apply_disable_insider_service() -> bool:
    log.info("Applying: disable_insider_service")
    return disable_and_stop("wisvc")


def check_disable_insider_service() -> bool:
    return get_service_start_type("wisvc") == "disabled"


# ── disable_remote_registry ───────────────────────────────────────────────────

def apply_disable_remote_registry() -> bool:
    log.info("Applying: disable_remote_registry")
    return disable_and_stop("RemoteRegistry")


def check_disable_remote_registry() -> bool:
    return get_service_start_type("RemoteRegistry") == "disabled"


# ── disable_ipv6 ──────────────────────────────────────────────────────────────
# Registry-based (not a service). 0xFF disables all IPv6 components.
_PATH_IPV6 = r"SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters"


def apply_disable_ipv6() -> bool:
    log.info("Applying: disable_ipv6")
    return set_dword(HKLM, _PATH_IPV6, "DisabledComponents", 0xFF)


def check_disable_ipv6() -> bool:
    return get_dword(HKLM, _PATH_IPV6, "DisabledComponents") == 0xFF
