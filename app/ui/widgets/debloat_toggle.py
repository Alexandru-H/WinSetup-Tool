"""
debloat_toggle.py — Card widget for a single AppX package (Debloat page).

Contract (CLAUDE.md §4.1, §8.1):
    * Deliberately separate from OptimizationToggle (deliberate isolation).
    * Always 56 px tall — every AppXEntry has a description line.
    * Uses a pre-computed is_present flag from page-level bulk detection
      (appx.bulk_detect) to avoid N individual PowerShell calls at build time.
    * DISABLED state when is_present=False: forbidden cursor, locked toggle,
      description replaced with "Already removed" (i18n key).
    * FAILED state: 1 px red border + 8 px dot at left edge.  Only shown on
      enabled (present) cards.  Cleared on user click or clear_all_failed().
    * ★ badge: recommended apps.  ⚠ badge: description starts with "WARNING:".
    * Click interaction: disabled cards ignore all clicks.

Visual layout (56 px):
    ┌────────────────────────────────────────────────────────────────────┐
    │  App name (11pt semibold)                          ★  ⚠  [●▬▬]  │
    │  Short description (9pt, muted)                                    │
    └────────────────────────────────────────────────────────────────────┘
     ←12→  name/desc VBox (expanding)  ←12→  badges  ←12→  toggle  ←12→
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.system import appx
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager
    from app.data.debloat_catalog import AppXEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout & drawing constants
# ---------------------------------------------------------------------------

_CARD_RADIUS  = 8
_CARD_H       = 56    # fixed — every entry has a description
_H_PAD        = 12
_V_PAD        = 8
_SPACING      = 12
_TEXT_SPACING = 2
_BADGE_SIZE   = 14
_BADGE_FONT   = 9
_NAME_FONT    = 11
_DESC_FONT    = 9
_DOT_SIZE     = 8     # failed-indicator dot diameter (px)


# ---------------------------------------------------------------------------
# Color interpolation helper
# ---------------------------------------------------------------------------

def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear interpolation between two QColors.  t in [0.0, 1.0]."""
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
    )


# ---------------------------------------------------------------------------
# _ToggleSwitch — iOS-style animated pill (internal; mirrors OptToggle's)
# ---------------------------------------------------------------------------

class _ToggleSwitch(QWidget):
    """Animated 44×24 pill toggle with opacity support for disabled state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self._track_off = QColor("#555555")
        self._track_on  = QColor("#3b82f6")
        self._knob_off  = QColor("#999999")
        self._knob_on   = QColor("#ffffff")
        self._progress: float = 0.0
        self._opacity:  float = 1.0

    def set_colors(
        self,
        track_off: QColor,
        track_on:  QColor,
        knob_off:  QColor,
        knob_on:   QColor,
    ) -> None:
        self._track_off = track_off
        self._track_on  = track_on
        self._knob_off  = knob_off
        self._knob_on   = knob_on
        self.update()

    def set_progress(self, p: float) -> None:
        self._progress = max(0.0, min(1.0, p))
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)

        w = float(self.width())   # 44
        h = float(self.height())  # 24
        r = h / 2.0               # 12

        track_color = _lerp_color(self._track_off, self._track_on, self._progress)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(QRectF(0.0, 0.0, w, h), r, r)

        knob_off_x = 2.0
        knob_on_x  = w - 20.0 - 2.0
        knob_x     = knob_off_x + (knob_on_x - knob_off_x) * self._progress
        knob_color = _lerp_color(self._knob_off, self._knob_on, self._progress)
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_x, 2.0, 20.0, 20.0))

        painter.end()


# ---------------------------------------------------------------------------
# DebloatToggle — public card widget
# ---------------------------------------------------------------------------

class DebloatToggle(QWidget, ThemedWidget, LocalizedWidget):
    """Card widget for a single AppX package to remove.

    Receives a pre-computed `is_present` value from the page-level bulk
    detection rather than calling is_installed() individually, keeping page
    construction to a single PowerShell round-trip regardless of catalog size.

    Disabled state (is_present=False):
        * Cursor: ForbiddenCursor
        * Toggle: locked at OFF, 35% opacity
        * Name label: text_sec color
        * Description: "Already removed" (i18n key)
        * All mouse clicks silently ignored

    Failed state (is_present=True, failed=True):
        * 1 px red border inset + 8 px dot at left edge
        * Cleared on user click (retry intent) or clear_all_failed()

    Signals:
        toggled(bool, object)  — (queued_for_removal: bool, AppXEntry)
    """

    toggled = Signal(bool, object)  # (queued_for_removal, AppXEntry)

    def __init__(
        self,
        app_entry: "AppXEntry",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        is_present: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_entry  = app_entry
        self._is_present = is_present
        self._is_queued:           bool  = False
        self._hovered:             bool  = False
        self._failed:              bool  = False
        self._toggle_progress_val: float = 0.0

        # Theme color cache — overwritten on first refresh_theme()
        self._bg:        str = "#1a1a1a"
        self._bg_hover:  str = "#2b2b2b"
        self._err_color: str = "#ef4444"

        # Pre-compute clean description (strip "WARNING: " prefix for display)
        raw_desc = app_entry.description
        self._clean_desc:  str  = (
            raw_desc[len("WARNING: "):] if raw_desc.startswith("WARNING: ") else raw_desc
        )
        self._has_warning: bool = raw_desc.startswith("WARNING:")

        # QPropertyAnimation on _toggle_progress drives _ToggleSwitch.set_progress
        self._anim = QPropertyAnimation(self, b"_toggle_progress")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setFixedHeight(_CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._build_layout()
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        # Finalise disabled visual state (cursor, opacity, desc text)
        if not self._is_present:
            self._apply_disabled_visuals()

    # ---- Qt property (required for QPropertyAnimation) ------------------

    def _get_toggle_progress(self) -> float:
        return self._toggle_progress_val

    def _set_toggle_progress(self, value: float) -> None:
        self._toggle_progress_val = value
        self._toggle_sw.set_progress(value)

    _toggle_progress = Property(float, _get_toggle_progress, _set_toggle_progress)

    # ---- Layout construction --------------------------------------------

    def _build_layout(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_H_PAD, _V_PAD, _H_PAD, _V_PAD)
        layout.setSpacing(_SPACING)

        # Left: name + description column (expanding)
        text_col = QVBoxLayout()
        text_col.setSpacing(_TEXT_SPACING)
        text_col.setContentsMargins(0, 0, 0, 0)

        name_font = QFont()
        name_font.setPointSize(_NAME_FONT)
        name_font.setWeight(QFont.Weight.Medium)

        self._name_lbl = QLabel(self._app_entry.name, self)
        self._name_lbl.setFont(name_font)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        text_col.addWidget(self._name_lbl)

        desc_font = QFont()
        desc_font.setPointSize(_DESC_FONT)

        self._desc_lbl = QLabel(self._clean_desc, self)
        self._desc_lbl.setFont(desc_font)
        self._desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        text_col.addWidget(self._desc_lbl)

        layout.addLayout(text_col, 1)

        # Badges: ★ recommended, ⚠ warning
        badge_font = QFont()
        badge_font.setPointSize(_BADGE_FONT)

        self._star_lbl: QLabel | None = None
        self._warn_lbl: QLabel | None = None

        if self._app_entry.recommended:
            self._star_lbl = QLabel("★", self)
            self._star_lbl.setFixedSize(_BADGE_SIZE, _BADGE_SIZE)
            self._star_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._star_lbl.setFont(badge_font)
            self._star_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(self._star_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        if self._has_warning:
            self._warn_lbl = QLabel("⚠", self)
            self._warn_lbl.setFixedSize(_BADGE_SIZE, _BADGE_SIZE)
            self._warn_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._warn_lbl.setFont(badge_font)
            self._warn_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(self._warn_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        # iOS-style toggle switch (right)
        self._toggle_sw = _ToggleSwitch(self)
        layout.addWidget(self._toggle_sw, 0, Qt.AlignmentFlag.AlignVCenter)

    # ---- Disabled-state helpers -----------------------------------------

    def _apply_disabled_visuals(self) -> None:
        """Apply cursor, opacity, and description for the disabled state."""
        self.setCursor(Qt.CursorShape.ForbiddenCursor)
        self._anim.stop()
        self._set_toggle_progress(0.0)
        self._toggle_sw.set_opacity(0.35)

    def _update_desc_text(self) -> None:
        """Set description label based on current presence state + locale."""
        if not self._is_present:
            self._desc_lbl.setText(
                self._locale_mgr.tr("debloat.card.already_removed")
            )
        else:
            self._desc_lbl.setText(self._clean_desc)

    # ---- Public API -----------------------------------------------------

    def is_queued(self) -> bool:
        """True if the user has queued this app for removal."""
        return self._is_queued

    def set_queued(
        self, on: bool, animate: bool = True, emit: bool = True
    ) -> None:
        """Set the queued-for-removal state.

        Disabled cards (already removed) silently ignore this call.
        emit=False suppresses the toggled signal — used by bulk operations.
        """
        if not self._is_present:
            return
        state_changed = self._is_queued != on
        self._is_queued = on
        target = 1.0 if on else 0.0

        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._toggle_progress_val)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._anim.stop()
            self._set_toggle_progress(target)

        if emit and state_changed:
            self.toggled.emit(on, self._app_entry)

    def app_entry(self) -> "AppXEntry":
        return self._app_entry

    def is_disabled_state(self) -> bool:
        """True if the app is already absent from the system."""
        return not self._is_present

    def update_presence(self, is_present: bool) -> None:
        """Update the presence/disabled state.

        Called by DebloatCategorySection.refresh_all_from_system() after
        the worker reports completion.
        """
        if self._is_present == is_present:
            return
        self._is_present = is_present
        if not is_present and self._is_queued:
            # Was queued, now confirmed removed — dequeue silently
            self._is_queued = False
        if not is_present:
            self._apply_disabled_visuals()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._toggle_sw.set_opacity(1.0)
        self._update_desc_text()
        # Re-apply theme colors (name color depends on is_present)
        self.refresh_theme(self._theme_mgr.current())
        self.update()

    def matches_query(self, query: str) -> bool:
        """Case-insensitive substring match on app name and clean description."""
        q = query.lower()
        return q in self._app_entry.name.lower() or q in self._clean_desc.lower()

    def set_failed(self, failed: bool) -> None:
        """Mark (or clear) the failed-apply state.  Only visible on enabled cards."""
        if self._failed == failed:
            return
        self._failed = failed
        self.update()

    def is_failed(self) -> bool:
        return self._failed

    def refresh_from_system(self) -> None:
        """Re-check is_installed and update state.

        NOTE: prefer DebloatCategorySection.refresh_all_from_system() for bulk
        refresh after a removal run — it issues a single PS call for the whole
        section.  This method is here for standalone/testing use.
        """
        try:
            present = appx.is_installed(self._app_entry.package_name)
        except Exception as exc:
            log.warning(
                "is_installed raised for %s: %s", self._app_entry.key, exc
            )
            present = self._is_present
        self.update_presence(present)

    # ---- Mixin hooks ---------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._bg        = theme["card"]
        self._bg_hover  = theme["card_hover"]
        self._err_color = theme["err"]

        # Name: muted when disabled (already removed)
        name_color = theme["text_sec"] if not self._is_present else theme["text_pri"]
        self._name_lbl.setStyleSheet(
            f"color: {name_color}; background: transparent;"
        )
        self._desc_lbl.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

        if self._star_lbl is not None:
            # Star badge also muted when disabled
            star_color = (
                theme["text_sec"] if not self._is_present else theme["accent"]
            )
            self._star_lbl.setStyleSheet(
                f"color: {star_color}; background: transparent;"
            )

        if self._warn_lbl is not None:
            self._warn_lbl.setStyleSheet(
                f"color: {theme['warn']}; background: transparent;"
            )

        self._toggle_sw.set_colors(
            track_off=QColor(theme["border"]),
            track_on=QColor(theme["accent"]),
            knob_off=QColor(theme["text_sec"]),
            knob_on=QColor(theme["text_pri"]),
        )
        self.update()

    def refresh_locale(self) -> None:
        self._update_desc_text()

    # ---- Qt events ------------------------------------------------------

    def enterEvent(self, event) -> None:
        if self._is_present:
            self._hovered = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if not self._is_present:
            # Disabled — silently absorb the click
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # User intentionally interacting — clear failed marker first
            if self._failed:
                self.set_failed(False)
            self.set_queued(not self._is_queued)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Card background
        p.setPen(Qt.PenStyle.NoPen)
        bg = self._bg_hover if (self._hovered and self._is_present) else self._bg
        p.setBrush(QColor(bg))
        p.drawRoundedRect(self.rect(), _CARD_RADIUS, _CARD_RADIUS)

        # Failed state visuals (only on enabled cards)
        if self._failed and self._is_present:
            # 1 px red border (inset 0.5 px so it stays inside rounded corners)
            err_pen = QPen(QColor(self._err_color))
            err_pen.setWidthF(1.0)
            p.setPen(err_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            inset_r = QRectF(
                0.5, 0.5,
                float(self.width()) - 1.0,
                float(self.height()) - 1.0,
            )
            p.drawRoundedRect(inset_r, _CARD_RADIUS, _CARD_RADIUS)

            # 8 px red dot centred in the left-padding strip
            dot_x = float((_H_PAD - _DOT_SIZE) // 2)
            dot_y = float((self.height() - _DOT_SIZE) // 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._err_color))
            p.drawEllipse(QRectF(dot_x, dot_y, _DOT_SIZE, _DOT_SIZE))

        p.end()
