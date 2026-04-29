"""
Sidebar with carve-out active-item effect (CLAUDE.md §9).

Visual concept:
    * The sidebar and the content area share one color: panel_bg.
    * Around the sidebar, the body is the DWM acrylic layer.
    * The active nav item looks like a tab that "opens" into the content
      area. We achieve that by subtracting two carve regions from the
      sidebar panel path — one immediately above and one immediately
      below the active item — each with an 18px quarter-circle inner
      corner. The subtracted regions become transparent holes so the
      acrylic shows through, giving the tab a flush-with-content feel.

Single-paint architecture:
    * Sidebar.paintEvent builds ONE QPainterPath:
          rounded_rect(w, h, r=8)   -   top_bite   -   bottom_bite
      and fills it with panel_bg. No child overlay, no compositing tricks.
    * active_y is a Qt Property on Sidebar itself so QPropertyAnimation
      can slide the carve smoothly between nav items.

Z-order:
    Sidebar paint (panel_bg - carve bites)
  < NavButtons (super().paintEvent renders them on top)

Run standalone:
    python -m app.ui.sidebar
"""

from __future__ import annotations

import sys

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget

from app.core.i18n import LocaleManager
from app.core.theming import ThemeManager
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear interpolation between two colors (alpha ignored — RGB only)."""
    t = max(0.0, min(1.0, t))
    r = int(round(c1.red()   * (1.0 - t) + c2.red()   * t))
    g = int(round(c1.green() * (1.0 - t) + c2.green() * t))
    b = int(round(c1.blue()  * (1.0 - t) + c2.blue()  * t))
    return QColor(r, g, b)


# ==========================================================================
# NavButton
# ==========================================================================

class NavButton(QPushButton, ThemedWidget, LocalizedWidget):
    """A single sidebar entry: localized text (+ optional icon later).

    Hover behaviour (subtle — no big coloured rectangle):
        * Text colour fades from ``text_sec`` (idle) to ``text_pri`` (hovered).
        * A small accent-coloured dot appears on the left, fading in with
          the hover progress.
    Active buttons keep ``text_pri`` as text colour and pin the accent dot
    at full opacity — a persistent leading indicator (Windows 11 pattern),
    complementing the carve-out surrounding the row.
    """

    FIXED_HEIGHT = 42
    _LEFT_PADDING = 24

    # Hover animation (see class docstring).
    _HOVER_IN_MS = 120
    _HOVER_OUT_MS = 150

    # Dot indicator — 4px accent circle, centred vertically at x=12.
    _DOT_DIAMETER = 4
    _DOT_CENTER_X = 12

    def __init__(
        self,
        route: str,
        label_key: str,
        theme_mgr: ThemeManager,
        locale_mgr: LocaleManager,
        icon_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.route = route
        self.label_key = label_key
        self.icon_name = icon_name  # reserved for future SVG hook-up
        self._active = False
        self._theme: dict[str, str] = {}
        self._hover_progress: float = 0.0

        self.setFixedHeight(self.FIXED_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)

        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)   # DemiBold == SemiBold (CSS 600)
        self.setFont(font)

        # Hover anim target: the hover_progress Qt property below.
        self._hover_anim = QPropertyAnimation(self, b"hover_progress", self)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # connect_theme applies an initial refresh_theme() which in turn
        # calls _apply_stylesheet — so _theme is populated before any paint.
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    # ---- Qt property (drives the hover animation) -----------------------

    def _get_hover_progress(self) -> float:
        return self._hover_progress

    def _set_hover_progress(self, value: float) -> None:
        v = float(value)
        if v == self._hover_progress:
            return
        self._hover_progress = v
        # Text colour is interpolated inside the stylesheet; dot opacity
        # lives in paintEvent. Both need refresh on every anim tick.
        self._apply_stylesheet()
        self.update()

    hover_progress = Property(float, _get_hover_progress, _set_hover_progress)

    # ---- Public state ---------------------------------------------------

    def setActive(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        # Entering active state: freeze hover_progress so the dot disappears
        # immediately and text_pri locks in. Leaving active: reset to 0.0
        # and let the next enterEvent animate in if the cursor is on top.
        self._hover_anim.stop()
        self._hover_progress = 0.0
        self._apply_stylesheet()
        self.update()

    def isActive(self) -> bool:
        return self._active

    # ---- Mixin hooks ----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self._apply_stylesheet()
        self.update()   # dot colour may have changed too

    def refresh_locale(self) -> None:
        self.setText(self._locale_mgr.tr(self.label_key))

    # ---- Hover events ---------------------------------------------------

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        if self._active:
            return
        self._hover_anim.stop()
        self._hover_anim.setDuration(self._HOVER_IN_MS)
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._active:
            return
        self._hover_anim.stop()
        self._hover_anim.setDuration(self._HOVER_OUT_MS)
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()

    # ---- Painting (dot on top of the stylesheet-drawn text) -------------

    def paintEvent(self, event) -> None:
        super().paintEvent(event)   # QPushButton draws text via the QSS
        if not self._theme:
            return
        if not self._active and self._hover_progress <= 0.0:
            return
        # Active: dot is pinned at full opacity. Inactive: dot fades in/out
        # with the hover animation.
        opacity = 1.0 if self._active else self._hover_progress
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(opacity)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._theme["accent"]))
        d = self._DOT_DIAMETER
        cx = self._DOT_CENTER_X
        cy = self.height() / 2.0
        p.drawEllipse(int(cx - d / 2), int(cy - d / 2), d, d)
        p.end()

    # ---- Internal -------------------------------------------------------

    def _apply_stylesheet(self) -> None:
        if not self._theme:
            return
        if self._active:
            # Active row: full-strength primary text. No hover, no dot.
            color_str = self._theme["text_pri"]
        else:
            # Inactive row: lerp text_sec -> text_pri by hover_progress so
            # the text brightens subtly as the cursor approaches. No
            # background rect — the dot + colour shift carry the hover
            # signal on their own.
            c1 = QColor(self._theme["text_sec"])
            c2 = QColor(self._theme["text_pri"])
            c = _lerp_color(c1, c2, self._hover_progress)
            color_str = f"rgb({c.red()}, {c.green()}, {c.blue()})"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {color_str};
                text-align: left;
                padding-left: {self._LEFT_PADDING}px;
                border: none;
                border-radius: 0px;
            }}
            """
        )


# ==========================================================================
# Sidebar
# ==========================================================================

class Sidebar(QFrame, ThemedWidget):
    """Fixed-width sidebar with nav buttons and an animated carve shape."""

    WIDTH = 220
    _V_PADDING = 24
    _SPACING = 4
    _ANIM_MS = 150
    _OUTER_RADIUS = 8    # exterior rounded corners of the sidebar card
    _CARVE_RADIUS = 18   # quarter-circle of the right-side carve bites
    _INNER_RADIUS = 6    # small rounded corners on the active item's left side

    navigationRequested = Signal(str)

    def __init__(
        self,
        theme_mgr: ThemeManager,
        locale_mgr: LocaleManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_mgr = theme_mgr
        self._locale_mgr = locale_mgr
        self._buttons: dict[str, NavButton] = {}
        self._active_route: str | None = None
        self._first_show_done = False

        # Carve geometry lives directly on Sidebar — no child overlay.
        # active_y is exposed as a Qt Property so QPropertyAnimation can
        # slide the carve between nav items.
        self._active_y: int = 0
        self._active_h: int = NavButton.FIXED_HEIGHT
        self._panel_bg: QColor = QColor("#000000")   # set via refresh_theme

        self.setFixedWidth(self.WIDTH)
        self.setObjectName("sidebar_panel")

        # Translucent backing so the carve bites (subtracted from the
        # panel path) are genuinely transparent — the DWM acrylic of the
        # main window shows through them.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, self._V_PADDING, 0, self._V_PADDING)
        self._layout.setSpacing(self._SPACING)
        self._layout.addStretch(1)   # keeps buttons clustered at the top

        self._anim = QPropertyAnimation(self, b"active_y", self)
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.connect_theme(theme_mgr)

    # ---- Qt property (drives the carve animation) ----------------------

    def _get_active_y(self) -> int:
        return self._active_y

    def _set_active_y(self, value: int) -> None:
        v = int(value)
        if v == self._active_y:
            return
        self._active_y = v
        self.update()

    active_y = Property(int, _get_active_y, _set_active_y)

    # ---- Public API -----------------------------------------------------

    def add_nav_button(
        self,
        route: str,
        label_key: str,
        icon_name: str | None = None,
        spacer_before: bool = False,
    ) -> NavButton:
        """Append a button; returns it so callers can hold a reference if needed.

        ``spacer_before=True`` inserts a 16px vertical gap above the new
        button (still before the trailing stretch). Used to separate the
        Settings entry from the main nav group.
        """
        if route in self._buttons:
            raise ValueError(f"Sidebar already has a button for route {route!r}.")
        button = NavButton(
            route,
            label_key,
            self._theme_mgr,
            self._locale_mgr,
            icon_name=icon_name,
            parent=self,
        )
        button.clicked.connect(lambda _=False, r=route: self.set_active(r))
        # Insert before the trailing stretch so the stretch stays last.
        # The spacer (if requested) goes above the new button.
        if spacer_before:
            self._layout.insertSpacing(self._layout.count() - 1, 16)
        self._layout.insertWidget(self._layout.count() - 1, button)
        self._buttons[route] = button
        return button

    def set_active(self, route: str, animate: bool = True) -> None:
        """Activate a route: slide the carve shape and flag the button."""
        if route not in self._buttons:
            return
        target_button = self._buttons[route]

        # Force layout so .geometry() is accurate even if called before
        # the first paint.
        self._layout.activate()
        target_y = target_button.geometry().top()
        target_h = target_button.height() or NavButton.FIXED_HEIGHT

        if target_h != self._active_h:
            self._active_h = target_h
            self.update()

        self._anim.stop()
        if animate and self._active_route is not None:
            self._anim.setStartValue(self._active_y)
            self._anim.setEndValue(target_y)
            self._anim.start()
        else:
            # First activation or explicit no-animation: snap instantly.
            self.active_y = target_y

        for r, btn in self._buttons.items():
            btn.setActive(r == route)

        self._active_route = route
        self.navigationRequested.emit(route)

    def active_route(self) -> str | None:
        return self._active_route

    # ---- Mixin hook -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._panel_bg = QColor(theme["panel_bg"])
        self.update()

    # ---- Painting -------------------------------------------------------

    def paintEvent(self, event) -> None:
        # Single-path paint: outer rounded rect minus two carve bites.
        # The subtracted regions are literal holes in the sidebar's fill
        # path, so they stay transparent all the way down to the DWM
        # acrylic of the main window — no compositing tricks needed.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._panel_bg)

        w = float(self.width())
        h = float(self.height())
        r_out = float(self._OUTER_RADIUS)
        r_carve = float(self._CARVE_RADIUS)
        ay = float(self._active_y)
        ah = float(self._active_h)

        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.0, 0.0, w, h), r_out, r_out)

        # --- Top bite: punch out the region above the active item.
        # User pseudo-code geometry:  (0,0) → (w, 0) → (w, ay - r) →
        # quad through (w, ay) to (w - r, ay) → (0, ay) → close.
        # The quad gives the active item's top-right its rounded corner.
        if ay > 0:
            top = QPainterPath()
            top.moveTo(0.0, 0.0)
            top.lineTo(w, 0.0)
            # Clamp vertical so we never go negative if ay < r_carve.
            top.lineTo(w, max(0.0, ay - r_carve))
            top.quadTo(w, ay, w - r_carve, ay)
            top.lineTo(0.0, ay)
            top.closeSubpath()
            panel = panel.subtracted(top)

        # --- Bottom bite: punch out the region below the active item,
        # with a quad at the top-right giving the active item's bottom-
        # right its rounded corner.
        bot_start = ay + ah
        if bot_start < h:
            bot = QPainterPath()
            bot.moveTo(0.0, bot_start)
            bot.lineTo(w - r_carve, bot_start)
            bot.quadTo(w, bot_start, w, min(h, bot_start + r_carve))
            bot.lineTo(w, h)
            bot.lineTo(0.0, h)
            bot.closeSubpath()
            panel = panel.subtracted(bot)

        # --- Small rounded corners on the LEFT side of the active item
        # (symmetric with the 18px carve on the right). Subtract two tiny
        # quarter-circle bites at the top-left and bottom-left of the
        # active strip so the "tab" reads as rounded on all four sides.
        r_inner = float(self._INNER_RADIUS)

        if ay > 0:
            tl = QPainterPath()
            tl.moveTo(0.0, ay)
            tl.lineTo(r_inner, ay)
            tl.quadTo(0.0, ay, 0.0, ay + r_inner)
            tl.closeSubpath()
            panel = panel.subtracted(tl)

        if bot_start < h:
            bl = QPainterPath()
            bl.moveTo(0.0, bot_start - r_inner)
            bl.quadTo(0.0, bot_start, r_inner, bot_start)
            bl.lineTo(0.0, bot_start)
            bl.closeSubpath()
            panel = panel.subtracted(bl)

        p.drawPath(panel)
        p.end()

        # NavButton children paint on top via Qt's normal child pass.
        super().paintEvent(event)

    # ---- Qt events ------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Button positions are stable across width changes, but let's
        # keep active_y in sync defensively if layout reflowed vertically.
        if self._active_route is not None:
            btn = self._buttons[self._active_route]
            self._active_y = btn.geometry().top()
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._first_show_done:
            return
        self._first_show_done = True
        # Layout geometries aren't final inside showEvent — defer one tick.
        QTimer.singleShot(0, self._select_first_item)

    def _select_first_item(self) -> None:
        if self._active_route is not None or not self._buttons:
            return
        first_route = next(iter(self._buttons))
        self.set_active(first_route, animate=False)


# ==========================================================================
# Standalone dev demo — run with:  python -m app.ui.sidebar
# ==========================================================================

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel

    from app.core.logging_setup import setup_logging
    from app.core.settings import Settings

    setup_logging()
    _app = QApplication(sys.argv)

    _settings = Settings()
    _settings.load()
    _theme = ThemeManager(_settings)
    _locale = LocaleManager(_settings)

    _window = QWidget()
    _window.setWindowTitle("Sidebar demo")
    _window.resize(1000, 700)
    _window.setStyleSheet(f"background: {_theme.current()['body_bg']};")

    _h = QHBoxLayout(_window)
    _h.setContentsMargins(0, 0, 0, 0)
    _h.setSpacing(0)

    _sidebar = Sidebar(_theme, _locale)
    for _route, _key in (
        ("home", "nav.home"),
        ("apps", "nav.apps"),
        ("wallpaper", "nav.wallpaper"),
        ("optimizations", "nav.optimizations"),
        ("debloat", "nav.debloat"),
        ("uninstall", "nav.uninstall"),
    ):
        _sidebar.add_nav_button(_route, _key)
    _sidebar.navigationRequested.connect(lambda r: print(f"route -> {r}"))

    _content = QLabel("Content area (panel_bg)")
    _content.setAlignment(Qt.AlignmentFlag.AlignCenter)
    _content.setStyleSheet(
        f"background: {_theme.current()['panel_bg']}; "
        f"color: {_theme.current()['text_pri']}; font-size: 16pt;"
    )

    _h.addWidget(_sidebar)
    _h.addWidget(_content, 1)

    _window.show()
    sys.exit(_app.exec())
