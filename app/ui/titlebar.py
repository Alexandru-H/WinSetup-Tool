"""
DWM titlebar theme sync (CLAUDE.md §6.3).

Windows' title bar color is controlled by DWM, not by Qt. We call
DwmSetWindowAttribute with DWMWA_USE_IMMERSIVE_DARK_MODE (20) to flip it
between dark and light so the chrome stays aligned with the app theme.

Why both branches exist: switching the attribute back to 0 on light theme
is required — the title bar does NOT revert on its own after being set
to 1. Every call must pass the correct value for the current theme.

Timing note: DWM silently ignores the call on a window that has never
been shown. Call `connect_titlebar_to_theme` after the first show() —
for example from showEvent via QTimer.singleShot(0, ...) in
main_window.py — so the HWND is already mapped.

Non-Windows platforms are no-ops; the module is safe to import anywhere.
"""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from app.core.logging_setup import get_logger

if TYPE_CHECKING:
    from app.core.theming import ThemeManager

log = get_logger(__name__)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DARK_THEME_NAME = "dark"


def apply_titlebar_theme(window: QWidget, dark: bool) -> None:
    """Ask DWM to paint the title bar dark (True) or light (False)."""
    if sys.platform != "win32":
        return

    hwnd = int(window.winId())
    # DWM expects a pointer to a 4-byte BOOL. Passing the int explicitly
    # (not just 1/0) keeps the "both branches explicit" guarantee visible.
    value = ctypes.c_int(1 if dark else 0)

    try:
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except (AttributeError, OSError) as exc:
        log.warning(
            "DwmSetWindowAttribute unavailable (%s) — titlebar theme unchanged.",
            exc,
        )
        return

    if hr != 0:
        # HRESULT != S_OK: DWM rejected the call (window not yet shown on
        # some builds, attribute unsupported on old Win10, etc.).
        log.warning(
            "DwmSetWindowAttribute returned HRESULT=0x%08x — titlebar may "
            "not reflect the requested theme.",
            hr & 0xFFFFFFFF,
        )


def connect_titlebar_to_theme(window: QWidget, theme_mgr: ThemeManager) -> None:
    """Keep window's titlebar in sync with theme_mgr; applies the current theme now."""

    def _update(*_: object) -> None:
        # themeChanged emits the palette dict, but we don't need it —
        # current_name() tells us whether the new theme is dark or light.
        apply_titlebar_theme(
            window,
            dark=theme_mgr.current_name() == _DARK_THEME_NAME,
        )

    theme_mgr.themeChanged.connect(_update)
    _update()
