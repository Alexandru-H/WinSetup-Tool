"""
category_section.py — Collapsible category accordion for the Apps page.

Contract (CLAUDE.md §4.1, §8.1):
    * CategorySection composes a set of AppCard widgets into a collapsible
      section with an animated header. It has NO installer logic.
    * The header draws its own ▶/▼ triangle in paintEvent for crisp
      rendering at all DPI levels. No icon file dependency.
    * Expand/collapse is driven by a QPropertyAnimation on the content
      widget's maximumHeight (0 → natural, natural → 0), 200 ms OutCubic.
      The content widget is never destroyed — only hidden via max-height.
    * Natural height is computed analytically (rows × card_h + gaps +
      top_padding) to avoid calling sizeHint() before the widget is shown.
    * selection_changed is emitted (once, aggregated) after any AppCard
      toggle so parent pages can update footer state without connecting to
      every individual card.
    * ALL strings (category name, count badges, "selected" label) go
      through i18n. Category names resolve via `apps.category.<key>`.
      The "· N selected" suffix uses key `apps.category.n_selected`.

Visual:
    ▼ Browsers (9)  · 3 selected       ← header, 40 px tall
    ┌──────────────┬──────────────┐
    │ □ Chrome ★   │ □ Firefox    │    ← content, 2-col AppCard grid
    │ □ Brave      │ □ Opera      │
    └──────────────┴──────────────┘

    ▶ Gaming (12)                       ← collapsed
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.ui.widgets.app_card import AppCard
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager
    from app.data.apps_catalog import AppEntry, CategoryEntry

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_HDR_H         = 40     # header row height (px)
_TRI_SIZE      = 10     # triangle icon bounding square (px)
_TRI_X         = 8      # left margin to triangle (px)
_CARD_H        = 44     # must match AppCard._CARD_H
_CARD_H_GAP    = 6      # vertical spacing between card rows
_CARD_W_GAP    = 8      # horizontal spacing between 2 columns
_CONTENT_TOP   = 8      # top padding inside content area
_QWIDGETSIZE_MAX = 16_777_215   # Qt's maximum widget dimension


# ---------------------------------------------------------------------------
# Internal: clickable category header
# ---------------------------------------------------------------------------

class _CategoryHeader(QWidget):
    """40 px tall clickable header row.

    Draws the ▶/▼ triangle via paintEvent; title, count, and selected-count
    are QLabel children positioned by QHBoxLayout. The left margin of the
    layout skips the triangle drawing area.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HDR_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._expanded  = False
        self._tri_color = "#c5c5c5"   # text_sec

        # Layout: left margin skips triangle area (pad + size + gap)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_TRI_X + _TRI_SIZE + 8, 0, 8, 0)
        layout.setSpacing(4)

        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.DemiBold)

        self._title_lbl = QLabel()
        self._title_lbl.setFont(title_font)
        self._title_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self._title_lbl, 0)

        count_font = QFont()
        count_font.setPointSize(10)

        self._count_lbl = QLabel()
        self._count_lbl.setFont(count_font)
        self._count_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self._count_lbl, 0)

        self._sel_lbl = QLabel()
        self._sel_lbl.setFont(count_font)
        self._sel_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._sel_lbl.hide()
        layout.addWidget(self._sel_lbl, 0)

        layout.addStretch(1)   # pushes everything to the left

    # ---- API (called by CategorySection) --------------------------------

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_count(self, total: int) -> None:
        self._count_lbl.setText(f"({total})")

    def set_selected(self, n: int, label: str) -> None:
        """Update the "· N selected" suffix. Pass n=0 to hide it."""
        if n > 0:
            # label already contains the formatted "· N <word>" string
            self._sel_lbl.setText(label)
            self._sel_lbl.show()
        else:
            self._sel_lbl.hide()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.update()   # repaint triangle

    def apply_colors(self, theme: dict[str, str]) -> None:
        self._tri_color = theme["text_sec"]
        self._title_lbl.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        self._count_lbl.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )
        self._sel_lbl.setStyleSheet(
            f"color: {theme['accent']}; background: transparent;"
        )
        self.update()

    # ---- Qt events ------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Triangle centred vertically in the header
        t = float(_TRI_SIZE)
        cx = float(_TRI_X) + t / 2.0
        cy = float(self.height()) / 2.0

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._tri_color))

        path = QPainterPath()
        if self._expanded:
            # ▼  pointing down
            path.moveTo(cx - t / 2.0, cy - t / 4.0)
            path.lineTo(cx + t / 2.0, cy - t / 4.0)
            path.lineTo(cx,           cy + t / 2.0)
        else:
            # ▶  pointing right
            path.moveTo(cx - t / 4.0, cy - t / 2.0)
            path.lineTo(cx + t / 2.0, cy)
            path.lineTo(cx - t / 4.0, cy + t / 2.0)
        path.closeSubpath()
        p.drawPath(path)

        p.end()


# ---------------------------------------------------------------------------
# CategorySection
# ---------------------------------------------------------------------------

class CategorySection(QWidget, ThemedWidget, LocalizedWidget):
    """Collapsible section containing a 2-column grid of AppCard widgets.

    Header shows: ▼/▶ icon, category name, total count, selected count.
    Click on the header row toggles expand/collapse with smooth animation.

    Signals:
        selection_changed — emitted (once, aggregated) whenever any child
            AppCard toggles. Parent pages use this to update footer state.
    """

    selection_changed = Signal()

    def __init__(
        self,
        category: "CategoryEntry",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._category = category
        self._expanded = category.default_expanded

        # Build UI before wiring theme/locale so initial colors apply once
        self._build_ui(theme_mgr)

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        # Apply initial expand state without animation (widget not shown yet)
        self._content.setMaximumHeight(
            _QWIDGETSIZE_MAX if self._expanded else 0
        )
        self._header.set_expanded(self._expanded)

    # ---- Build ----------------------------------------------------------

    def _build_ui(self, theme_mgr: "ThemeManager") -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = _CategoryHeader(self)
        self._header.clicked.connect(self._toggle)
        root.addWidget(self._header)

        # Content area (animated via maximumHeight)
        self._content = QWidget(self)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        # 2-column grid of AppCards
        grid = QGridLayout(self._content)
        grid.setContentsMargins(0, _CONTENT_TOP, 0, 0)
        grid.setHorizontalSpacing(_CARD_W_GAP)
        grid.setVerticalSpacing(_CARD_H_GAP)

        self._cards: list[AppCard] = []
        for idx, app in enumerate(self._category.apps):
            card = AppCard(app, theme_mgr, self._content)
            card.toggled.connect(self._on_card_toggled)
            grid.addWidget(card, idx // 2, idx % 2)
            self._cards.append(card)

        # Make both columns equal width
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        root.addWidget(self._content)

        # Animation on content maximumHeight
        self._anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._expanding = False

    # ---- Natural height calculation ------------------------------------

    def _natural_height(self) -> int:
        """Compute content area's expected height from visible card count.

        Uses the same spacing constants as the grid layout so the animation
        always targets the exact final height rather than an approximation.
        """
        visible = sum(1 for c in self._cards if c.isVisible())
        if visible == 0:
            return 0
        rows = (visible + 1) // 2
        return _CONTENT_TOP + rows * _CARD_H + max(0, rows - 1) * _CARD_H_GAP

    # ---- Expand / collapse ---------------------------------------------

    def _toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._expanding = expanded
        self._header.set_expanded(expanded)

        if not animate:
            self._content.setMaximumHeight(
                _QWIDGETSIZE_MAX if expanded else 0
            )
            return

        self._anim.stop()
        if expanded:
            self._anim.setStartValue(0)
            self._anim.setEndValue(self._natural_height())
        else:
            # Start from actual painted height, not max-height constant
            self._anim.setStartValue(self._content.height())
            self._anim.setEndValue(0)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        # After expand animation, remove the ceiling so the content widget
        # can resize freely (e.g. after search filter changes card count).
        if self._expanding:
            self._content.setMaximumHeight(_QWIDGETSIZE_MAX)

    def is_expanded(self) -> bool:
        return self._expanded

    # ---- Selection helpers ---------------------------------------------

    def _on_card_toggled(self, _checked: bool) -> None:
        """Aggregate handler — update the header badge and propagate up."""
        self._refresh_selected_count()
        self.selection_changed.emit()

    def _refresh_selected_count(self) -> None:
        n = self.selected_count()
        # The "· N selected" text is built here so _CategoryHeader stays
        # dumb and locale-unaware.
        label_text = self._locale_mgr.tr(
            "apps.category.n_selected", n=n
        )
        self._header.set_selected(n, label_text)

    def cards(self) -> list[AppCard]:
        return list(self._cards)

    def selected_apps(self) -> list["AppEntry"]:
        return [c.app_entry() for c in self._cards if c.is_checked()]

    def selected_count(self) -> int:
        return sum(1 for c in self._cards if c.is_checked())

    def select_all(self) -> None:
        changed = False
        for card in self._cards:
            if card.isVisible() and not card.is_checked():
                card.set_checked(True, emit=False)
                changed = True
        if changed:
            self._refresh_selected_count()
            self.selection_changed.emit()

    def deselect_all(self) -> None:
        changed = False
        for card in self._cards:
            if card.is_checked():
                card.set_checked(False, emit=False)
                changed = True
        if changed:
            self._refresh_selected_count()
            self.selection_changed.emit()

    def select_recommended(self) -> None:
        """Check only apps where AppEntry.recommended is True."""
        changed = False
        for card in self._cards:
            want = card.app_entry().recommended
            if card.is_checked() != want:
                card.set_checked(want, emit=False)
                changed = True
        if changed:
            self._refresh_selected_count()
            self.selection_changed.emit()

    def mark_failed(self, app: "AppEntry", failed: bool = True) -> None:
        for card in self._cards:
            if card.app_entry().winget_id == app.winget_id:
                card.set_failed(failed)
                return

    def clear_all_failed(self) -> None:
        for card in self._cards:
            card.set_failed(False)

    def apply_search_filter(self, query: str) -> int:
        """Show/hide cards based on query. Returns the count of visible cards.

        Empty query shows all cards. The caller (AppsPage) uses the return
        value to decide whether to hide the entire section.
        """
        if not query:
            for card in self._cards:
                card.setVisible(True)
            return len(self._cards)

        visible = 0
        for card in self._cards:
            match = card.matches_query(query)
            card.setVisible(match)
            if match:
                visible += 1
        return visible

    # ---- Mixin hooks ---------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._header.apply_colors(theme)
        # Cards wire their own themeChanged — no need to forward manually

    def refresh_locale(self) -> None:
        # Translate category name from "apps.category.<key>"
        key = f"apps.category.{self._category.key}"
        self._header.set_title(self._locale_mgr.tr(key))
        self._header.set_count(len(self._cards))
        # Refresh the selected-count suffix if any selection exists
        self._refresh_selected_count()
