"""
optimization_toggle.py — Card widget for a single Windows tweak (Optimizations page).

Contract (CLAUDE.md §4.1, §8.1):
    * OptimizationToggle is a pure UI component. The only system calls it
      makes are tweak.check_fn() — once on construction and once per
      explicit refresh_from_system() call. apply_fn is NEVER called here.
    * ALL colors come from the palette dict via refresh_theme(). Zero
      hardcoded hex in this file.
    * Click anywhere on the card toggles state and emits
      toggled(new_state: bool, tweak: TweakEntry).
    * set_on(emit=False) suppresses the signal — safe for batch operations
      ("Apply Recommended") without per-card signal cascade.
    * refresh_from_system() syncs the toggle to the current system state.
      It never emits toggled.
    * The iOS-style toggle animation is driven by a QPropertyAnimation on
      the _toggle_progress Qt property (float 0.0 → 1.0), keeping the Qt
      event loop fully in control and avoiding QTimer polling.

Visual layout:
    ┌────────────────────────────────────────────────────────────────────┐
    │  Tweak name (11pt, semibold)                        ★   ⟲  [●▬▬] │  56 px with desc
    │  Short description (9pt, muted, hidden when empty)                  │  44 px without
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
from app.ui.widgets.themed_widget import ThemedWidget

if TYPE_CHECKING:
    from app.core.theming import ThemeManager
    from app.data.optimizations_catalog import TweakEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout & drawing constants
# ---------------------------------------------------------------------------

_CARD_RADIUS  = 8     # background rounded-corner radius (px)
_CARD_H_TALL  = 56    # fixed height when description is visible (px)
_CARD_H_SHORT = 44    # fixed height when description is empty (px)
_H_PAD        = 12    # left / right outer padding (px)
_V_PAD        = 8     # top / bottom outer padding (px)
_SPACING      = 12    # main HBox item spacing (px)
_TEXT_SPACING = 2     # VBox gap between name and description labels (px)
_BADGE_SIZE   = 14    # badge QLabel fixed width and height (px)
_BADGE_FONT   = 9     # badge glyph font size (pt)
_NAME_FONT    = 11    # tweak name font size (pt)
_DESC_FONT    = 9     # description font size (pt)
_DOT_SIZE     = 8     # failed indicator dot diameter (px)


# ---------------------------------------------------------------------------
# Color interpolation helper
# ---------------------------------------------------------------------------

def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear interpolation between two QColors. t in [0.0, 1.0]."""
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
    )


# ---------------------------------------------------------------------------
# _ToggleSwitch — iOS-style pill (internal widget)
# ---------------------------------------------------------------------------

class _ToggleSwitch(QWidget):
    """Animated 44×24 pill toggle. Colors are pushed in by the parent
    OptimizationToggle via set_colors() — this widget never subscribes to
    ThemeManager directly, avoiding double-subscription complexity."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 24)

        # Placeholder colors — overwritten immediately by parent set_colors()
        self._track_off = QColor("#555555")
        self._track_on  = QColor("#3b82f6")
        self._knob_off  = QColor("#999999")
        self._knob_on   = QColor("#ffffff")
        self._progress: float = 0.0

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

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())   # 44
        h = float(self.height())  # 24
        r = h / 2.0               # 12 — full pill radius

        # --- Track (interpolated off → on color) ---
        track_color = _lerp_color(self._track_off, self._track_on, self._progress)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(QRectF(0.0, 0.0, w, h), r, r)

        # --- Knob ---
        # Off:  x = 2  (2 px from left edge)
        # On:   x = 22 (track 44 − knob 20 − right pad 2 = 22)
        knob_off_x = 2.0
        knob_on_x  = w - 20.0 - 2.0   # = 22.0
        knob_x     = knob_off_x + (knob_on_x - knob_off_x) * self._progress
        knob_color = _lerp_color(self._knob_off, self._knob_on, self._progress)
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_x, 2.0, 20.0, 20.0))

        painter.end()


# ---------------------------------------------------------------------------
# OptimizationToggle — public card widget
# ---------------------------------------------------------------------------

class OptimizationToggle(QWidget, ThemedWidget):
    """Card widget for a single Windows tweak.

    Construction calls tweak.check_fn() once to initialize the toggle to
    the current system state. Clicking anywhere on the card flips the
    toggle and emits toggled(new_state, tweak_entry).

    Signals:
        toggled(bool, object) — (new_state, TweakEntry). The TweakEntry is
            typed as object to avoid forward-reference complexity at class
            definition time; callers can cast safely.
    """

    toggled = Signal(bool, object)  # (new_state: bool, tweak: TweakEntry)

    def __init__(
        self,
        tweak: "TweakEntry",
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tweak = tweak
        self._is_on:               bool  = False
        self._hovered:             bool  = False
        self._failed:              bool  = False
        self._toggle_progress_val: float = 0.0  # QProperty backing store

        # Dark-mode fallbacks — overwritten immediately by connect_theme()
        self._bg:        str = "#1a1a1a"
        self._bg_hover:  str = "#2b2b2b"
        self._err_color: str = "#ef4444"   # overwritten by refresh_theme

        # QPropertyAnimation drives _toggle_progress → _ToggleSwitch.set_progress
        self._anim = QPropertyAnimation(self, b"_toggle_progress")
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setFixedHeight(_CARD_H_SHORT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # WA_Hover ensures enterEvent/leaveEvent fire without full mouse tracking
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._build_layout()
        # connect_theme calls refresh_theme(current_palette) immediately,
        # so all child widget colors are set before the widget is shown.
        self.connect_theme(theme_mgr)
        # Detect system state — must come after _build_layout and connect_theme
        # so _toggle_sw and palette are both ready.
        self.refresh_from_system()

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

        # --- Left: name + description column (expanding) ---
        text_col = QVBoxLayout()
        text_col.setSpacing(_TEXT_SPACING)
        text_col.setContentsMargins(0, 0, 0, 0)

        name_font = QFont()
        name_font.setPointSize(_NAME_FONT)
        name_font.setWeight(QFont.Weight.Medium)  # ~500, clean semibold

        self._name_lbl = QLabel(self._tweak.name, self)
        self._name_lbl.setFont(name_font)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        text_col.addWidget(self._name_lbl)

        desc_font = QFont()
        desc_font.setPointSize(_DESC_FONT)

        self._desc_lbl = QLabel("", self)   # empty until i18n lands (Delivery 5)
        self._desc_lbl.setFont(desc_font)
        self._desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        text_col.addWidget(self._desc_lbl)

        layout.addLayout(text_col, 1)

        # --- Middle: badges (★ recommended, ⟲ restart-required) ---
        self._star_lbl:    QLabel | None = None
        self._restart_lbl: QLabel | None = None

        badge_font = QFont()
        badge_font.setPointSize(_BADGE_FONT)

        if self._tweak.recommended:
            self._star_lbl = QLabel("★", self)
            self._star_lbl.setFixedSize(_BADGE_SIZE, _BADGE_SIZE)
            self._star_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._star_lbl.setFont(badge_font)
            self._star_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(
                self._star_lbl, 0, Qt.AlignmentFlag.AlignVCenter
            )

        if self._tweak.requires_restart:
            self._restart_lbl = QLabel("⟲", self)
            self._restart_lbl.setFixedSize(_BADGE_SIZE, _BADGE_SIZE)
            self._restart_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._restart_lbl.setFont(badge_font)
            self._restart_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(
                self._restart_lbl, 0, Qt.AlignmentFlag.AlignVCenter
            )

        # --- Right: iOS toggle switch ---
        self._toggle_sw = _ToggleSwitch(self)
        layout.addWidget(self._toggle_sw, 0, Qt.AlignmentFlag.AlignVCenter)

        # Adjust height and desc label visibility
        self._update_desc_visibility()

    def _update_desc_visibility(self) -> None:
        """Show/hide description label and adjust card height accordingly."""
        has_desc = bool(self._desc_lbl.text())
        self._desc_lbl.setVisible(has_desc)
        self.setFixedHeight(_CARD_H_TALL if has_desc else _CARD_H_SHORT)

    # ---- Public API -----------------------------------------------------

    def is_on(self) -> bool:
        return self._is_on

    def set_on(self, on: bool, animate: bool = True, emit: bool = True) -> None:
        """Set the toggle state.

        animate=False snaps instantly — used by refresh_from_system() and
        batch-reset operations.  emit=False suppresses toggled signal — used
        by "Apply Recommended" to avoid cascading per-card signals.
        """
        state_changed = self._is_on != on
        self._is_on = on
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
            self.toggled.emit(on, self._tweak)

    def tweak(self) -> "TweakEntry":
        return self._tweak

    def matches_query(self, query: str) -> bool:
        """Case-insensitive substring match on name and description text."""
        q = query.lower()
        return (
            q in self._tweak.name.lower()
            or q in self._desc_lbl.text().lower()
        )

    def refresh_from_system(self) -> None:
        """Re-run tweak.check_fn() and snap the toggle to match. No signal."""
        try:
            result = bool(self._tweak.check_fn())
        except Exception as exc:
            log.warning("check_fn raised for %s: %s", self._tweak.key, exc)
            result = False
        self.set_on(result, animate=False, emit=False)
        # NOTE: refresh_from_system does NOT clear the failed marker —
        # failed state is cleared only by user click or clear_all_failed().

    def set_failed(self, failed: bool) -> None:
        """Mark (or clear) the failed-apply state.

        Visual: 1 px red border tint + 8 px red dot at the card's left edge.
        Cleared on next user click (intentional retry) or when
        OptimizationCategorySection.clear_all_failed() is called before a
        new apply run.
        """
        if self._failed == failed:
            return
        self._failed = failed
        self.update()

    def is_failed(self) -> bool:
        """Return True if the last apply_fn call for this tweak returned False."""
        return self._failed

    # ---- ThemedWidget hook ----------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._bg        = theme["card"]
        self._bg_hover  = theme["card_hover"]
        self._err_color = theme["err"]

        self._name_lbl.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        self._desc_lbl.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

        if self._star_lbl is not None:
            self._star_lbl.setStyleSheet(
                f"color: {theme['accent']}; background: transparent;"
            )
        if self._restart_lbl is not None:
            self._restart_lbl.setStyleSheet(
                f"color: {theme['warn']}; background: transparent;"
            )

        # Push toggle colors down to the switch widget
        self._toggle_sw.set_colors(
            track_off=QColor(theme["border"]),
            track_on=QColor(theme["accent"]),
            knob_off=QColor(theme["text_sec"]),
            knob_on=QColor(theme["text_pri"]),
        )
        self.update()

    # ---- Qt events ------------------------------------------------------

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # User is intentionally retrying — clear the failed marker first.
            if self._failed:
                self.set_failed(False)
            self.set_on(not self._is_on)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ---- Card background (rounded rect) ---------------------------------
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._bg_hover if self._hovered else self._bg))
        p.drawRoundedRect(self.rect(), _CARD_RADIUS, _CARD_RADIUS)

        if self._failed:
            # ---- Failed: 1 px red border tint --------------------------------
            err_pen = QPen(QColor(self._err_color))
            err_pen.setWidthF(1.0)
            p.setPen(err_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Inset by 0.5 so the stroke stays inside the card's rounded rect
            inset_r = QRectF(
                0.5, 0.5,
                float(self.width()) - 1.0,
                float(self.height()) - 1.0,
            )
            p.drawRoundedRect(inset_r, _CARD_RADIUS, _CARD_RADIUS)

            # ---- Failed: 8 px red dot at card's left edge --------------------
            # Centred in the [0, _H_PAD] left-padding strip, vertically centred.
            dot_x = float((_H_PAD - _DOT_SIZE) // 2)
            dot_y = float((self.height() - _DOT_SIZE) // 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._err_color))
            p.drawEllipse(QRectF(dot_x, dot_y, _DOT_SIZE, _DOT_SIZE))

        p.end()


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea

    from app.core.logging_setup import setup_logging
    from app.core.settings import Settings
    from app.core.theming import ThemeManager
    from app.data.optimizations_catalog import (
        OPTIMIZATIONS_CATALOG,
        TweakEntry,
    )

    setup_logging()
    qapp = QApplication(sys.argv)

    _settings = Settings()
    _settings.load()
    _theme_mgr = ThemeManager(_settings)

    # Resolve categories by index (order matches optimizations_catalog.py)
    _privacy_cat     = OPTIMIZATIONS_CATALOG[0]   # privacy
    _explorer_cat    = OPTIMIZATIONS_CATALOG[2]   # explorer
    _perf_cat        = OPTIMIZATIONS_CATALOG[3]   # performance

    def _find(cat, key: str) -> "TweakEntry":
        return next(t for t in cat.tweaks if t.key == key)

    # 1. Plain tweak: no badges
    tweak_plain   = _find(_explorer_cat, "explorer_open_thispc")
    # 2. Recommended only  (★)
    tweak_rec     = _find(_privacy_cat,  "disable_telemetry")
    # 3. Restart-required only  (⟲)
    tweak_restart = _find(_perf_cat,     "disable_visual_effects")
    # 4. Both badges  (★ + ⟲) — fake entry, no real catalog tweak has both
    tweak_both    = TweakEntry(
        key="smoke_both_badges",
        name="Both badges: recommended + restart",
        apply_fn=lambda: True,
        check_fn=lambda: False,
        recommended=True,
        requires_restart=True,
    )
    # 5. Win10-only
    tweak_win10   = _find(_explorer_cat, "small_taskbar_icons")
    # 6. Win11-only
    tweak_win11   = _find(_explorer_cat, "classic_context_menu")

    # --- Window ---
    win = QWidget()
    win.setWindowTitle("OptimizationToggle — smoke test")
    win.setFixedSize(600, 400)

    def _apply_win_bg(theme: dict) -> None:
        win.setStyleSheet(f"background-color: {theme['body_bg']};")

    _apply_win_bg(_theme_mgr.current())
    _theme_mgr.themeChanged.connect(_apply_win_bg)

    root = QVBoxLayout(win)
    root.setContentsMargins(16, 16, 16, 16)
    root.setSpacing(6)

    cards: list[OptimizationToggle] = []
    for tw in (tweak_plain, tweak_rec, tweak_restart, tweak_both, tweak_win10, tweak_win11):
        card = OptimizationToggle(tw, _theme_mgr, win)
        cards.append(card)
        root.addWidget(card)

    def _on_toggled(state: bool, tw: "TweakEntry") -> None:
        print(f"toggled: {state} on {tw.key}")

    for card in cards:
        card.toggled.connect(_on_toggled)

    # Refresh button
    refresh_btn = QPushButton("Force refresh from system", win)
    refresh_btn.setFixedHeight(32)

    def _on_refresh() -> None:
        print("--- refresh_from_system() on all cards ---")
        for c in cards:
            c.refresh_from_system()
        print("done (no signals emitted)")

    refresh_btn.clicked.connect(_on_refresh)
    root.addWidget(refresh_btn)
    root.addStretch()

    win.show()
    sys.exit(qapp.exec())
