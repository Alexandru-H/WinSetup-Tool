"""
app_card.py — Single-app checkbox card for the Apps page.

Contract (CLAUDE.md §4.1, §8.1):
    * AppCard is a pure UI component. Zero install logic, zero system
      calls, zero QThread usage. Pages own any worker that does work.
    * The entire card surface is clickable — clicking anywhere on the
      card toggles the checked state and emits toggled(bool). This
      removes the need for a precise click target on a small checkbox.
    * Colors come exclusively from the palette dict via refresh_theme().
      Zero hardcoded hex in this file (checkmark white is the one
      intentional exception — white is white on any accent).
    * App names are brand names and are NOT translated; AppCard has no
      LocalizedWidget dependency.
    * The checkbox is drawn in paintEvent. The name QLabel and optional
      star QLabel are child widgets managed by QHBoxLayout. The layout's
      left margin accounts for the checkbox drawing area so the labels
      never overlap the custom checkbox.

Visual layout (fixed height 44 px):
    ┌─────────────────────────────────────────────────────┐
    │  [☐/☑ 20×20]  App Name label ──────────────  [★ ?] │
    │  ←12→ ←20→ ←10→               layout expands  ←12→ │
    └─────────────────────────────────────────────────────┘
    Unchecked:  2 px border (text_sec), transparent fill, 4 px radius.
    Checked:    filled accent, white checkmark via QPainterPath.
    Hover:      background swaps card → card_hover.
    Failed:     red dot (8 px) in the checkbox→name gap; 1 px err border.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.core.logging_setup import get_logger
from app.ui.widgets.themed_widget import ThemedWidget

if TYPE_CHECKING:
    from app.core.theming import ThemeManager
    from app.data.apps_catalog import AppEntry

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Layout & drawing constants
# ---------------------------------------------------------------------------

_CARD_H       = 44    # fixed card height (px)
_CARD_RADIUS  = 8     # background rounded-corner radius (px)
_CB_SIZE      = 20    # checkbox square size (px)
_CB_RADIUS    = 4     # checkbox corner radius (px)
_CB_BORDER    = 2     # unchecked border stroke width (px)
_CB_X         = 12    # left margin to checkbox left edge (px)
_CB_SPACING   = 10    # gap between checkbox right edge and name label (px)
_RIGHT_PAD    = 12    # right outer padding (px)
_DOT_SIZE     = 8     # failed indicator dot diameter (px)


# ---------------------------------------------------------------------------
# AppCard
# ---------------------------------------------------------------------------

class AppCard(QWidget, ThemedWidget):
    """Single-app card with checkbox, name, and optional recommended star.

    The whole card is clickable — clicking anywhere toggles the checked
    state and emits toggled(checked).

    Signals:
        toggled(bool) — emitted when the user clicks the card.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        app_entry: "AppEntry",
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry   = app_entry
        self._checked = False
        self._hovered = False
        self._failed  = False

        # Palette cache — avoids repeated dict lookups inside paintEvent.
        # Values are set in refresh_theme() before the first paint.
        self._bg:        str = "#1a1a1a"
        self._bg_hover:  str = "#2b2b2b"
        self._cb_border: str = "#c5c5c5"   # text_sec → unchecked box stroke
        self._cb_fill:   str = "#3b82f6"   # accent   → checked box fill
        self._err_color: str = "#ef4444"   # err      → failure dot + border

        self.setFixedHeight(_CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # WA_Hover ensures enterEvent/leaveEvent fire even without mouse
        # tracking (hover events use a different path than move events).
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._build_layout()
        # connect_theme() calls refresh_theme(current_palette) immediately
        self.connect_theme(theme_mgr)

    # ---- Layout construction --------------------------------------------

    def _build_layout(self) -> None:
        """QHBoxLayout whose left margin skips the painted checkbox area."""
        layout = QHBoxLayout(self)
        # Left: outer padding (12) + checkbox width (20) + gap (10) = 42 px
        layout.setContentsMargins(
            _CB_X + _CB_SIZE + _CB_SPACING,
            0, _RIGHT_PAD, 0,
        )
        layout.setSpacing(8)

        name_font = QFont()
        name_font.setPointSize(11)

        self._name_lbl = QLabel(self._entry.name)
        self._name_lbl.setFont(name_font)
        self._name_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self._name_lbl, 1)   # stretches to fill

        # Star label — only for recommended apps
        if self._entry.recommended:
            self._star_lbl: QLabel | None = QLabel("★")
            self._star_lbl.setFixedSize(14, 14)
            self._star_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            star_font = QFont()
            star_font.setPointSize(9)
            self._star_lbl.setFont(star_font)
            self._star_lbl.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(self._star_lbl, 0)
        else:
            self._star_lbl = None

    # ---- Public API -----------------------------------------------------

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool, emit: bool = True) -> None:
        """Set the checked state programmatically.

        ``emit=False`` lets parent widgets batch-check/uncheck cards
        without triggering cascading selection_changed signals per card.
        The caller is responsible for emitting its own aggregate signal
        after the batch completes.
        """
        if self._checked == checked:
            return
        self._checked = checked
        self.update()       # schedule repaint of checkbox area
        if emit:
            self.toggled.emit(checked)

    def set_failed(self, failed: bool) -> None:
        if self._failed == failed:
            return
        self._failed = failed
        self.update()

    def is_failed(self) -> bool:
        return self._failed

    def app_entry(self) -> "AppEntry":
        return self._entry

    def matches_query(self, query: str) -> bool:
        """Case-insensitive substring match against the app's display name."""
        return query.lower() in self._entry.name.lower()

    # ---- ThemedWidget hook ----------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._bg        = theme["card"]
        self._bg_hover  = theme["card_hover"]
        self._cb_border = theme["text_sec"]
        self._cb_fill   = theme["accent"]
        self._err_color = theme["err"]

        self._name_lbl.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        if self._star_lbl is not None:
            self._star_lbl.setStyleSheet(
                f"color: {theme['accent']}; background: transparent;"
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
            if self._failed:
                self.set_failed(False)
            self.set_checked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ---- Card background (rounded rect) -----------------------------
        bg = self._bg_hover if self._hovered else self._bg
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(self.rect(), _CARD_RADIUS, _CARD_RADIUS)

        # ---- Failed state: subtle err border ----------------------------
        if self._failed:
            err_pen = QPen(QColor(self._err_color))
            err_pen.setWidthF(1.0)
            p.setPen(err_pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Inset by 0.5 so the stroke stays inside the rounded rect
            inset_r = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            p.drawRoundedRect(inset_r, _CARD_RADIUS, _CARD_RADIUS)
            p.setPen(Qt.PenStyle.NoPen)

        # ---- Checkbox (vertically centered on the card) -----------------
        cb_y   = (self.height() - _CB_SIZE) // 2
        cb_x   = float(_CB_X)
        cb_y_f = float(cb_y)
        cb_rect = QRectF(cb_x, cb_y_f, _CB_SIZE, _CB_SIZE)

        if self._checked:
            # Filled accent square
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._cb_fill))
            p.drawRoundedRect(cb_rect, _CB_RADIUS, _CB_RADIUS)

            # White checkmark path (points relative to checkbox top-left):
            #   (4,11) → (9,16) → (16,7)   gives a clean proportional mark
            pen = QPen(QColor("#ffffff"))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            path = QPainterPath()
            path.moveTo(cb_x + 4,  cb_y_f + 11)
            path.lineTo(cb_x + 9,  cb_y_f + 16)
            path.lineTo(cb_x + 16, cb_y_f + 7)
            p.drawPath(path)

        else:
            # Empty box: 2 px border, inset by half stroke to stay inside rect
            pen = QPen(QColor(self._cb_border))
            pen.setWidthF(float(_CB_BORDER))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            inset = _CB_BORDER / 2.0
            inset_rect = QRectF(
                cb_rect.x() + inset,
                cb_rect.y() + inset,
                cb_rect.width()  - _CB_BORDER,
                cb_rect.height() - _CB_BORDER,
            )
            p.drawRoundedRect(inset_rect, _CB_RADIUS, _CB_RADIUS)

        # ---- Failed state: red dot between checkbox and name ------------
        if self._failed:
            dot_x = float(_CB_X + _CB_SIZE + (_CB_SPACING - _DOT_SIZE) // 2)
            dot_y = float((self.height() - _DOT_SIZE) // 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self._err_color))
            p.drawEllipse(QRectF(dot_x, dot_y, _DOT_SIZE, _DOT_SIZE))

        p.end()
