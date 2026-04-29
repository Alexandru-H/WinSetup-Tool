"""
Segmented control — two-option pill selector with animated selection.

Used for the Dark/Light theme and EN/RO locale toggles in the app header.

Contract:
    * Exactly 2 options. ValueError at construction otherwise.
    * selectionChanged(str) fires ONLY on user interaction. External code
      can call set_selection(key, animate=…) without causing a feedback
      loop when the same widget is connected to a manager's signal.
    * Pure custom paint on a single QFrame; no child widgets.
    * pill_x is a Qt Property so QPropertyAnimation can slide it.

Run standalone:
    python -m app.ui.widgets.segmented
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame

from app.core.logging_setup import get_logger
from app.ui.widgets.themed_widget import ThemedWidget

if TYPE_CHECKING:
    from app.core.theming import ThemeManager

log = get_logger(__name__)


class SegmentedControl(QFrame, ThemedWidget):
    """Two-option pill selector with a smooth slide animation."""

    # Geometry
    _WIDTH = 180
    _HEIGHT = 32
    _PADDING = 2
    _PILL_WIDTH = 87
    _PILL_HEIGHT = 28
    _CONTAINER_RADIUS = 16
    _PILL_RADIUS = 14
    # Animation
    _ANIM_MS = 200
    # Active text stays white on both themes (pill is always accent-colored).
    _ACTIVE_TEXT_COLOR = "#ffffff"

    selectionChanged = Signal(str)

    def __init__(
        self,
        options: list[tuple[str, str]],
        theme_mgr: ThemeManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if len(options) != 2:
            raise ValueError(
                f"SegmentedControl requires exactly 2 options, got {len(options)}."
            )
        self._options: list[tuple[str, str]] = list(options)
        self._current_index: int = 0
        self._pill_x: int = self._pill_x_for(0)
        self._theme: dict[str, str] = {}

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        font = QFont()
        font.setPointSize(10)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)

        self._anim = QPropertyAnimation(self, b"pill_x", self)
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.connect_theme(theme_mgr)

    # ---- Qt property (drives the animation) -----------------------------

    def _get_pill_x(self) -> int:
        return self._pill_x

    def _set_pill_x(self, value: int) -> None:
        if value == self._pill_x:
            return
        self._pill_x = int(value)
        self.update()

    pill_x = Property(int, _get_pill_x, _set_pill_x)

    def _pill_x_for(self, index: int) -> int:
        if index == 0:
            return self._PADDING
        return self._WIDTH - self._PADDING - self._PILL_WIDTH

    # ---- Public API -----------------------------------------------------

    def current(self) -> str:
        return self._options[self._current_index][0]

    def set_selection(self, key: str, animate: bool = True) -> None:
        """Set the active option by key — does NOT emit selectionChanged.

        Callers (main_window) use this to sync the widget to external
        state without creating a feedback loop when the widget's
        signal is itself wired back to the manager.
        """
        for i, (k, _label) in enumerate(self._options):
            if k == key:
                if i != self._current_index:
                    self._apply_index(i, animate=animate)
                return
        log.warning(
            "SegmentedControl.set_selection: key %r not in options (%s).",
            key,
            [k for k, _ in self._options],
        )

    # ---- Internal -------------------------------------------------------

    def _apply_index(self, index: int, animate: bool) -> None:
        self._current_index = index
        target_x = self._pill_x_for(index)
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._pill_x)
            self._anim.setEndValue(target_x)
            self._anim.start()
        else:
            self.pill_x = target_x   # through setter -> update()

    # ---- Mixin hook -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.update()

    # ---- Mouse ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x = event.position().x()
        index = 0 if x < self.width() / 2 else 1
        if index == self._current_index:
            return   # no-op, no signal, no animation
        self._apply_index(index, animate=True)
        self.selectionChanged.emit(self._options[index][0])

    # ---- Paint ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        if not self._theme:
            # First paint can beat refresh_theme on some platforms.
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Container — 1px theme["border"] stroke around a card-colored fill.
        # The stroke gives the control definition on top of light card bgs;
        # the half-pixel inset keeps the stroke fully inside the widget.
        border_pen = QPen(QColor(self._theme["border"]))
        border_pen.setWidth(1)
        p.setPen(border_pen)
        p.setBrush(QColor(self._theme["card"]))
        container_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(
            container_rect,
            self._CONTAINER_RADIUS,
            self._CONTAINER_RADIUS,
        )

        # Pill — no border, solid accent fill.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._theme["accent"]))
        pill = QRect(
            self._pill_x,
            self._PADDING,
            self._PILL_WIDTH,
            self._PILL_HEIGHT,
        )
        p.drawRoundedRect(pill, self._PILL_RADIUS, self._PILL_RADIUS)

        # Labels — the option the pill currently overlaps gets the active
        # text color; the other stays muted. The midpoint crossing during
        # animation gives a crisp color flip that tracks the pill.
        mid = self.width() / 2
        pill_center = self._pill_x + self._PILL_WIDTH / 2
        left_is_active = pill_center < mid

        half_w = self.width() / 2
        left_rect = QRectF(0, 0, half_w, self.height())
        right_rect = QRectF(half_w, 0, half_w, self.height())

        active = QColor(self._ACTIVE_TEXT_COLOR)
        inactive = QColor(self._theme["text_sec"])

        p.setPen(active if left_is_active else inactive)
        p.drawText(left_rect, Qt.AlignmentFlag.AlignCenter, self._options[0][1])

        p.setPen(inactive if left_is_active else active)
        p.drawText(right_rect, Qt.AlignmentFlag.AlignCenter, self._options[1][1])

        p.end()


# ==========================================================================
# Standalone dev demo — run with:  python -m app.ui.widgets.segmented
# ==========================================================================

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    from app.core.logging_setup import setup_logging
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

    setup_logging()
    _app = QApplication(sys.argv)

    _settings = Settings()
    _settings.load()
    _theme = ThemeManager(_settings)

    _window = QWidget()
    _window.setWindowTitle("SegmentedControl demo")
    _window.resize(500, 120)
    _window.setStyleSheet(f"background: {_theme.current()['body_bg']};")

    _layout = QHBoxLayout(_window)
    _layout.setContentsMargins(30, 30, 30, 30)
    _layout.setSpacing(30)

    _theme_seg = SegmentedControl([("dark", "Dark"), ("light", "Light")], _theme)
    _theme_seg.selectionChanged.connect(lambda k: print(f"theme -> {k}"))
    _theme_seg.set_selection(_theme.current_name(), animate=False)

    _locale_seg = SegmentedControl([("ro", "RO"), ("en", "EN")], _theme)
    _locale_seg.selectionChanged.connect(lambda k: print(f"locale -> {k}"))

    _layout.addWidget(_theme_seg)
    _layout.addWidget(_locale_seg)
    _layout.addStretch(1)

    _window.show()
    sys.exit(_app.exec())
