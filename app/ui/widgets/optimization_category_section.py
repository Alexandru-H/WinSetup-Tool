"""
optimization_category_section.py — Collapsible category accordion for the
Optimizations page.

Contract (CLAUDE.md §4.1, §8.1):
    * Mirrors CategorySection structurally (same 40px header, same 2-column
      grid, same 200ms OutCubic animation) but uses OptimizationToggle
      instead of AppCard.  Deliberately NOT subclassed from CategorySection.
    * "Selected count" semantics → "active count" (toggle.is_on()).
    * select_recommended() is ADDITIVE: turns ON recommended tweaks without
      touching non-recommended ones (differs from AppsPage behaviour).
    * Win-version filtering: tweaks with win10_only / win11_only are excluded
      on incompatible Windows builds.  An empty section renders without error.
    * ZERO apply_fn calls here — pure UI.  apply_fn belongs to Delivery 4.
    * ALL colours from palette dict; all user-visible strings via i18n.

Visual:
    ▼ Privacy (8)   · 6 active          ← header 40 px
    ┌────────────────────┬───────────────────┐
    │ Disable telemetry  │ Disable Cortana ★ │  ← 2-col OptimizationToggle grid
    │ Disable Bing ★     │ Disable web search │
    └────────────────────┴───────────────────┘
"""

from __future__ import annotations

import platform
import sys
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
from app.ui.widgets.optimization_toggle import OptimizationToggle
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager
    from app.data.optimizations_catalog import CategoryEntry, TweakEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_HDR_H           = 40     # header row height (px)
_TRI_SIZE        = 10     # triangle bounding square (px)
_TRI_X           = 8      # left margin to triangle area (px)
_TOGGLE_H        = 44     # OptimizationToggle short height; used for natural-height calc
_CARD_H_GAP      = 6      # vertical spacing between toggle rows (px)
_CARD_W_GAP      = 8      # horizontal spacing between 2 columns (px)
_CONTENT_TOP     = 8      # top padding inside content area (px)
_QWIDGETSIZE_MAX = 16_777_215   # Qt maximum widget dimension


# ---------------------------------------------------------------------------
# Windows-version detection (module-level; evaluated once at import)
# ---------------------------------------------------------------------------

def _windows_major() -> int:
    """Return 10 or 11 based on Windows build number.  Returns 0 on non-Windows."""
    if sys.platform != "win32":
        return 0
    try:
        build = int(platform.version().split(".")[-1])
        return 11 if build >= 22000 else 10
    except (ValueError, IndexError):
        return 10  # safe fallback


_CURRENT_WIN: int = _windows_major()


# ---------------------------------------------------------------------------
# Internal: clickable category header
# ---------------------------------------------------------------------------

class _OptimCategoryHeader(QWidget):
    """40 px tall clickable header: ▶/▼  Title (N)  · N active.

    Draws the ▶/▼ triangle via paintEvent for crisp DPI rendering.
    Title, count, and active-count badge are QLabel children in an
    HBoxLayout; the layout's left margin skips the triangle area.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HDR_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._expanded  = False
        self._tri_color = "#c5c5c5"   # text_sec — overwritten by apply_colors()

        # Left margin skips the triangle drawing area (pad + size + gap)
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

        self._active_lbl = QLabel()
        self._active_lbl.setFont(count_font)
        self._active_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._active_lbl.hide()
        layout.addWidget(self._active_lbl, 0)

        layout.addStretch(1)   # push everything to the left

    # ---- API (called by OptimizationCategorySection) --------------------

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_count(self, total: int) -> None:
        self._count_lbl.setText(f"({total})")

    def set_active(self, n: int, label: str) -> None:
        """Update the '· N active' suffix.  Pass n=0 to hide it."""
        if n > 0:
            self._active_lbl.setText(label)
            self._active_lbl.show()
        else:
            self._active_lbl.hide()

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
        self._active_lbl.setStyleSheet(
            f"color: {theme['accent']}; background: transparent;"
        )
        self.update()

    # ---- Qt events ------------------------------------------------------

    def mousePressEvent(self, event) -> None:   # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Triangle centred vertically in the header
        t  = float(_TRI_SIZE)
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
# OptimizationCategorySection
# ---------------------------------------------------------------------------

class OptimizationCategorySection(QWidget, ThemedWidget, LocalizedWidget):
    """Collapsible section containing a 2-column grid of OptimizationToggle.

    Header: ▼/▶ icon, category name, total applicable tweak count, active
    count badge.  Click on the header row toggles expand/collapse with a
    smooth 200ms OutCubic animation.

    Win-version filtering is applied at construction time: tweaks whose
    win10_only or win11_only flag is incompatible with the detected Windows
    build are silently excluded.  An empty resulting section still renders
    (zero-height content area) without error.

    Signals:
        selection_changed — emitted (once, aggregated) whenever any child
            toggle's state changes.  Parent page uses this to update footer.
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

        # Filter out OS-incompatible tweaks once at construction
        self._applicable_tweaks: list["TweakEntry"] = [
            t for t in category.tweaks
            if not (t.win10_only and _CURRENT_WIN != 10)
            and not (t.win11_only and _CURRENT_WIN != 11)
        ]

        self._build_ui(theme_mgr)

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        # Set initial expand state without animation (widget not shown yet)
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
        self._header = _OptimCategoryHeader(self)
        self._header.clicked.connect(self._toggle)
        root.addWidget(self._header)

        # Content area (animated via maximumHeight)
        self._content = QWidget(self)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        # 2-column grid of OptimizationToggle
        grid = QGridLayout(self._content)
        grid.setContentsMargins(0, _CONTENT_TOP, 0, 0)
        grid.setHorizontalSpacing(_CARD_W_GAP)
        grid.setVerticalSpacing(_CARD_H_GAP)

        self._toggles: list[OptimizationToggle] = []
        for idx, tweak in enumerate(self._applicable_tweaks):
            toggle = OptimizationToggle(tweak, theme_mgr, self._content)
            toggle.toggled.connect(self._on_toggle_toggled)
            grid.addWidget(toggle, idx // 2, idx % 2)
            self._toggles.append(toggle)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        root.addWidget(self._content)

        # Animation on content maximumHeight
        self._anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._expanding = False

    # ---- Natural height ------------------------------------------------

    def _natural_height(self) -> int:
        """Analytically compute content area height from visible toggle count.

        Uses _TOGGLE_H (44px short height) as the row height constant.
        This matches the actual height for all toggles in Delivery 3 (no
        descriptions yet).  Mixed-height rows are addressed in Delivery 5.
        """
        visible = sum(1 for t in self._toggles if t.isVisible())
        if visible == 0:
            return 0
        rows = (visible + 1) // 2
        return _CONTENT_TOP + rows * _TOGGLE_H + max(0, rows - 1) * _CARD_H_GAP

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
            self._anim.setStartValue(self._content.height())
            self._anim.setEndValue(0)
        self._anim.start()

    def _on_anim_finished(self) -> None:
        # After expand animation, release the ceiling so the content widget
        # can resize freely (e.g. search filter changes visible count).
        if self._expanding:
            self._content.setMaximumHeight(_QWIDGETSIZE_MAX)

    def is_expanded(self) -> bool:
        return self._expanded

    # ---- Active-count tracking -----------------------------------------

    def _on_toggle_toggled(self, _state: bool, _tweak: object) -> None:
        """Aggregate handler — update header badge and propagate up."""
        self._refresh_active_count()
        self.selection_changed.emit()

    def _refresh_active_count(self) -> None:
        n = self.active_count()
        label_text = self._locale_mgr.tr(
            "optimizations.category.n_active", n=n
        )
        self._header.set_active(n, label_text)

    # ---- Public API ----------------------------------------------------

    def toggles(self) -> list[OptimizationToggle]:
        """Return all OptimizationToggle widgets in this section."""
        return list(self._toggles)

    def active_tweaks(self) -> list["TweakEntry"]:
        """Return TweakEntries whose toggle is currently ON."""
        return [t.tweak() for t in self._toggles if t.is_on()]

    def active_count(self) -> int:
        """Number of toggles currently ON."""
        return sum(1 for t in self._toggles if t.is_on())

    def select_all(self) -> None:
        """Turn all visible toggles ON (batch, no per-toggle signal cascade)."""
        changed = False
        for toggle in self._toggles:
            if toggle.isVisible() and not toggle.is_on():
                toggle.set_on(True, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_active_count()
            self.selection_changed.emit()

    def deselect_all(self) -> None:
        """Turn all toggles OFF (batch, no per-toggle signal cascade)."""
        changed = False
        for toggle in self._toggles:
            if toggle.is_on():
                toggle.set_on(False, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_active_count()
            self.selection_changed.emit()

    def select_recommended(self) -> None:
        """Turn ON recommended tweaks only.

        ADDITIVE — does NOT turn off non-recommended tweaks that are already ON.
        Differs from AppsPage's select_recommended which calls deselect_all first.
        """
        changed = False
        for toggle in self._toggles:
            if toggle.tweak().recommended and not toggle.is_on():
                toggle.set_on(True, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_active_count()
            self.selection_changed.emit()

    def refresh_all_from_system(self) -> None:
        """Re-detect every toggle's state from the live system registry/services.

        Useful after Apply completes or when navigating back to the page.
        Does NOT emit selection_changed — callers should call _update_footer()
        explicitly if they need to sync the footer afterward.
        """
        for toggle in self._toggles:
            toggle.refresh_from_system()
        self._refresh_active_count()

    def mark_failed(self, tweak: "TweakEntry", failed: bool = True) -> None:
        """Set the failed state on the toggle whose key matches tweak.key."""
        for toggle in self._toggles:
            if toggle.tweak().key == tweak.key:
                toggle.set_failed(failed)
                return

    def clear_all_failed(self) -> None:
        """Clear the failed state on every toggle in this section."""
        for toggle in self._toggles:
            toggle.set_failed(False)

    def apply_search_filter(self, query: str) -> int:
        """Show/hide toggles based on query string.

        Returns the count of visible (matching) toggles.
        Empty query shows all toggles and returns len(self._toggles).
        """
        if not query:
            for toggle in self._toggles:
                toggle.setVisible(True)
            return len(self._toggles)

        visible = 0
        for toggle in self._toggles:
            match = toggle.matches_query(query)
            toggle.setVisible(match)
            if match:
                visible += 1
        return visible

    # ---- Mixin hooks ---------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._header.apply_colors(theme)
        # Toggles wire their own themeChanged — no need to forward manually

    def refresh_locale(self) -> None:
        key = f"optimizations.category.{self._category.key}"
        self._header.set_title(self._locale_mgr.tr(key))
        self._header.set_count(len(self._toggles))
        self._refresh_active_count()
