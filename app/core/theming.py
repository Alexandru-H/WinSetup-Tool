"""
Theme manager — palette store and broadcast point for theme switches.

Contract (CLAUDE.md §7.3, §8.3):
    * ThemeManager(QObject) holds both the currently active theme mode
      (dark/light) AND the currently active accent — together they define
      the palette dict that is broadcast to widgets.
    * THEME_ACCENTS is a module-level registry of 9 accent keys → hex.
      The accent overrides `panel_bg` in the active palette, so the sidebar
      and content-stack backgrounds reflect the user's chosen color.
    * themeChanged(dict) fires on every successful mode OR accent switch,
      carrying a COPY of the resulting palette.
    * DARK and LIGHT are module-level base palette constants. Do NOT mutate
      them; current() composes a fresh dict each call.
    * This manager deliberately does NOT touch QApplication, QPalette,
      stylesheets, or animations. Those belong to the widgets (and to the
      main window's fade transition, §8.2).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from app.core.logging_setup import get_logger
from app.core.settings import Settings

log = get_logger(__name__)


def _shift_lightness(hex_color: str, factor: float) -> str:
    """Scale each RGB channel of a hex colour by ``factor``.

    ``factor > 1`` lightens, ``< 1`` darkens. Clamped to [0, 255] per
    channel. Used by _compose_palette to derive ``card`` from the
    accent-driven ``panel_bg`` so the card surface always reads as a
    layered step off the panel, whatever the user picks as accent.
    """
    c = QColor(hex_color)
    r = min(255, max(0, int(c.red()   * factor)))
    g = min(255, max(0, int(c.green() * factor)))
    b = min(255, max(0, int(c.blue()  * factor)))
    return QColor(r, g, b).name()


# --------------------------------------------------------------------------
# Palettes — base dark/light, with panel_bg set to the "default" accent for
# each mode. set_accent() dynamically overrides panel_bg at broadcast time.
# --------------------------------------------------------------------------

DARK: dict[str, str] = {
    "body_bg":    "#020208",
    "panel_bg":   "#202020",   # default_dark (Win11 dark surface)
    "card":       "#1a1a1a",   # overwritten dynamically in _compose_palette
    "card_hover": "#2b2b2b",   # derived in _compose_palette; fallback only
    "accent":     "#3b82f6",
    "accent_h":   "#2563eb",
    "text_pri":   "#ffffff",
    "text_sec":   "#c5c5c5",   # Win11 secondary label on dark surface
    "border":     "#22223c",
    "ok":         "#22c55e",
    "warn":       "#f59e0b",
    "err":        "#ef4444",
}

LIGHT: dict[str, str] = {
    "body_bg":    "#c0c4ca",
    "panel_bg":   "#f3f3f3",   # default_light (Win11 light surface)
    "card":       "#ffffff",   # overwritten dynamically in _compose_palette
    "card_hover": "#e8e8e8",   # derived in _compose_palette; fallback only
    "accent":     "#3b82f6",
    "accent_h":   "#2563eb",
    "text_pri":   "#1a1a1a",   # near-black — strong contrast on light surface
    "text_sec":   "#5a5a5a",   # Win11 secondary label on light surface
    "border":     "#cbd5e1",
    "ok":         "#16a34a",
    "warn":       "#d97706",
    "err":        "#dc2626",
}

SUPPORTED_THEMES: tuple[str, ...] = ("dark", "light")
FALLBACK_THEME: str = "dark"

# 9 accents laid out in a 3×3 grid on the Settings page. The two "default_*"
# entries match the panel_bg baked into DARK/LIGHT, so switching mode
# without re-picking an accent keeps a coherent look.
THEME_ACCENTS: dict[str, str] = {
    "default_dark":  "#202020",
    "default_light": "#f3f3f3",
    "navy":          "#1C2E4A",
    "slate":         "#989BB0",
    "orchid":        "#DA70D6",
    "mint":          "#B8ECC6",
    "beige":         "#DFC9B1",
    "coral":         "#F76772",
    "lavender":      "#E5DBE6",
}

FALLBACK_ACCENT: str = "default_dark"

_PALETTES: dict[str, dict[str, str]] = {
    "dark": DARK,
    "light": LIGHT,
}


class ThemeManager(QObject):
    """Holds the active (mode, accent) pair and broadcasts palette changes."""

    themeChanged = Signal(dict)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

        # --- theme mode -------------------------------------------------
        requested_mode = settings.get("theme", FALLBACK_THEME)
        if requested_mode not in SUPPORTED_THEMES:
            log.warning(
                "Settings theme %r is not supported — falling back to %r.",
                requested_mode,
                FALLBACK_THEME,
            )
            requested_mode = FALLBACK_THEME
        self._current: str = requested_mode

        # --- accent -----------------------------------------------------
        requested_accent = settings.get("accent", FALLBACK_ACCENT)
        if requested_accent not in THEME_ACCENTS:
            log.warning(
                "Settings accent %r is not a known accent key — "
                "falling back to %r.",
                requested_accent,
                FALLBACK_ACCENT,
            )
            requested_accent = FALLBACK_ACCENT
        self._accent: str = requested_accent

    # ---------------------------------------------------------------- build

    def _compose_palette(self) -> dict[str, str]:
        """Fresh dict = base mode palette with accent-driven panel_bg + card + card_hover.

        All three surface tokens are derived from the accent hex so they stay
        coherent regardless of which accent the user picks — no hardcoded value
        can accidentally clash with navy, orchid, coral, etc.

        Dark mode factors (applied to panel_bg):
            card       × 0.82  → 18 % darker  (recessed surface)
            card_hover × 1.10  → 10 % lighter (lifted / hovered surface)

        Light mode factors:
            card       × 1.08  →  8 % lighter (elevated card on panel)
            card_hover × 0.94  →  6 % darker  (subtle press / hover shadow)
        """
        palette = _PALETTES[self._current].copy()
        accent_hex = THEME_ACCENTS[self._accent]
        palette["panel_bg"] = accent_hex
        if self._current == "dark":
            palette["card"]       = _shift_lightness(accent_hex, 0.82)
            palette["card_hover"] = _shift_lightness(accent_hex, 1.10)
        else:
            palette["card"]       = _shift_lightness(accent_hex, 1.08)
            palette["card_hover"] = _shift_lightness(accent_hex, 0.94)
        return palette

    # ------------------------------------------------------------------- api

    def current(self) -> dict[str, str]:
        """Return the active palette (copy) with accent-driven panel_bg."""
        return self._compose_palette()

    def current_name(self) -> str:
        return self._current

    def current_accent(self) -> str:
        return self._accent

    def available(self) -> list[str]:
        return list(SUPPORTED_THEMES)

    def available_accents(self) -> dict[str, str]:
        """Accent registry (copy) — UI builds the swatch grid from this."""
        return THEME_ACCENTS.copy()

    def set_theme(self, name: str) -> None:
        """Switch mode (dark/light), persist, emit themeChanged."""
        if name == self._current:
            return
        if name not in SUPPORTED_THEMES:
            log.error(
                "Cannot switch to unknown theme %r (supported: %s).",
                name,
                ", ".join(SUPPORTED_THEMES),
            )
            return

        self._current = name
        self._settings.set("theme", name)
        self._settings.save()
        log.info("Theme switched to %r.", name)
        self.themeChanged.emit(self._compose_palette())

    def set_accent(self, accent_key: str) -> None:
        """Switch accent color, persist, emit themeChanged."""
        if accent_key == self._accent:
            return
        if accent_key not in THEME_ACCENTS:
            log.error(
                "Cannot switch to unknown accent %r (known: %s).",
                accent_key,
                ", ".join(THEME_ACCENTS),
            )
            return

        self._accent = accent_key
        self._settings.set("accent", accent_key)
        self._settings.save()
        log.info(
            "Accent switched to %r (%s).",
            accent_key,
            THEME_ACCENTS[accent_key],
        )
        self.themeChanged.emit(self._compose_palette())
