"""
debloat_category_section.py — Collapsible category accordion for the Debloat page.

Contract (CLAUDE.md §4.1, §8.1):
    * Deliberately isolated from OptimizationCategorySection — different child
      widget type (DebloatToggle), different badge semantics ("N queued"),
      tier-aware header (⚠ on AGGRESSIVE).
    * Receives a presence_map (package_name → bool) from DebloatPage, which
      runs a single appx.bulk_detect() call for the entire catalog before
      building sections. Individual toggles never call is_installed().
    * refresh_all_from_system() issues ONE appx.bulk_detect() call for this
      section's packages — much cheaper than N individual is_installed() calls.
    * select_recommended() is additive: queues recommended non-disabled apps
      without touching the rest.
    * ZERO subprocess calls here — system ops limited to appx.bulk_detect()
      in refresh_all_from_system() only.

Visual:
    ▼ Safe Bloat (20)   · 14 queued          ← header 40px
    ┌──────────────────────┬────────────────────┐
    │  Solitaire Collection │  Xbox Game Bar ★  │  ← 2-col DebloatToggle grid
    │  Microsoft News    ★  │  Mixed Reality ★  │
    └──────────────────────┴────────────────────┘
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
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
from app.data.debloat_catalog import RiskTier
from app.system import appx
from app.ui.widgets.debloat_toggle import DebloatToggle
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager
    from app.data.debloat_catalog import AppXEntry, CategoryEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_HDR_H           = 40      # header row height (px)
_TRI_SIZE        = 10      # triangle bounding square (px)
_TRI_X           = 8       # left margin to triangle area (px)
_TOGGLE_H        = 56      # DebloatToggle is always 56px (always has description)
_CARD_H_GAP      = 6       # vertical gap between toggle rows (px)
_CARD_W_GAP      = 8       # horizontal gap between 2 columns (px)
_CONTENT_TOP     = 8       # top padding inside content area (px)
_WARN_ICON_W     = 18      # width/height of the ⚠ label (px)
_QWIDGETSIZE_MAX = 16_777_215


# ---------------------------------------------------------------------------
# Internal: clickable accordion header
# ---------------------------------------------------------------------------

class _DebloatCategoryHeader(QWidget):
    """40px clickable header: ▶/▼  [⚠]  Title (N)  · N queued."""

    clicked = Signal()

    def __init__(
        self,
        has_warning: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HDR_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._expanded  = False
        self._tri_color = "#c5c5c5"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_TRI_X + _TRI_SIZE + 8, 0, 8, 0)
        layout.setSpacing(4)

        # ⚠ tier indicator — only on AGGRESSIVE category
        self._warn_icon: QLabel | None = None
        if has_warning:
            warn_font = QFont()
            warn_font.setPointSize(9)
            self._warn_icon = QLabel("⚠", self)
            self._warn_icon.setFixedSize(_WARN_ICON_W, _WARN_ICON_W)
            self._warn_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._warn_icon.setFont(warn_font)
            self._warn_icon.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True
            )
            layout.addWidget(self._warn_icon, 0)

        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.DemiBold)

        self._title_lbl = QLabel(self)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self._title_lbl, 0)

        count_font = QFont()
        count_font.setPointSize(10)

        self._count_lbl = QLabel(self)
        self._count_lbl.setFont(count_font)
        self._count_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self._count_lbl, 0)

        self._queued_lbl = QLabel(self)
        self._queued_lbl.setFont(count_font)
        self._queued_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._queued_lbl.hide()
        layout.addWidget(self._queued_lbl, 0)

        layout.addStretch(1)

    # ---- API called by DebloatCategorySection ----------------------------

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)

    def set_count(self, total: int) -> None:
        self._count_lbl.setText(f"({total})")

    def set_queued(self, n: int, label: str) -> None:
        """Update the '· N queued' suffix. Pass n=0 to hide it."""
        if n > 0:
            self._queued_lbl.setText(label)
            self._queued_lbl.show()
        else:
            self._queued_lbl.hide()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.update()

    def apply_colors(self, theme: dict[str, str]) -> None:
        self._tri_color = theme["text_sec"]
        self._title_lbl.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        self._count_lbl.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )
        self._queued_lbl.setStyleSheet(
            f"color: {theme['accent']}; background: transparent;"
        )
        if self._warn_icon is not None:
            self._warn_icon.setStyleSheet(
                f"color: {theme['warn']}; background: transparent;"
            )
        self.update()

    # ---- Qt events -------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        t  = float(_TRI_SIZE)
        cx = float(_TRI_X) + t / 2.0
        cy = float(self.height()) / 2.0

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._tri_color))

        path = QPainterPath()
        if self._expanded:
            path.moveTo(cx - t / 2.0, cy - t / 4.0)
            path.lineTo(cx + t / 2.0, cy - t / 4.0)
            path.lineTo(cx,           cy + t / 2.0)
        else:
            path.moveTo(cx - t / 4.0, cy - t / 2.0)
            path.lineTo(cx + t / 2.0, cy)
            path.lineTo(cx - t / 4.0, cy + t / 2.0)
        path.closeSubpath()
        p.drawPath(path)

        p.end()


# ---------------------------------------------------------------------------
# DebloatCategorySection
# ---------------------------------------------------------------------------

class DebloatCategorySection(QWidget, ThemedWidget, LocalizedWidget):
    """Collapsible accordion section for one debloat risk tier.

    Header: ▼/▶ icon, optional ⚠ (AGGRESSIVE only), category name, total
    count, queued count badge. Click on the header toggles expand/collapse
    with a smooth 200ms OutCubic animation.

    All toggles receive their is_present value from the caller-supplied
    presence_map so this section never issues individual PowerShell calls
    at build time.

    Signals:
        selection_changed — emitted when any child toggle's queued state changes.
    """

    selection_changed = Signal()

    def __init__(
        self,
        category: "CategoryEntry",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        presence_map: dict[str, bool],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._category    = category
        self._expanded    = category.default_expanded
        self._has_warning = category.tier == RiskTier.AGGRESSIVE

        self._build_ui(theme_mgr, locale_mgr, presence_map)

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        self._content.setMaximumHeight(
            _QWIDGETSIZE_MAX if self._expanded else 0
        )
        self._header.set_expanded(self._expanded)

    # ---- Build -----------------------------------------------------------

    def _build_ui(
        self,
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        presence_map: dict[str, bool],
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _DebloatCategoryHeader(
            has_warning=self._has_warning, parent=self
        )
        self._header.clicked.connect(self._toggle)
        root.addWidget(self._header)

        self._content = QWidget(self)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        grid = QGridLayout(self._content)
        grid.setContentsMargins(0, _CONTENT_TOP, 0, 0)
        grid.setHorizontalSpacing(_CARD_W_GAP)
        grid.setVerticalSpacing(_CARD_H_GAP)

        self._toggles: list[DebloatToggle] = []
        for idx, app_entry in enumerate(self._category.apps):
            is_present = presence_map.get(app_entry.package_name, False)
            toggle = DebloatToggle(
                app_entry,
                theme_mgr,
                locale_mgr,
                is_present=is_present,
                parent=self._content,
            )
            toggle.toggled.connect(self._on_toggle_toggled)
            grid.addWidget(toggle, idx // 2, idx % 2)
            self._toggles.append(toggle)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        root.addWidget(self._content)

        self._anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_finished)
        self._expanding = False

    # ---- Natural height --------------------------------------------------

    def _natural_height(self) -> int:
        """Compute content area height analytically from visible toggle count."""
        visible = sum(1 for t in self._toggles if t.isVisible())
        if visible == 0:
            return 0
        rows = (visible + 1) // 2
        return _CONTENT_TOP + rows * _TOGGLE_H + max(0, rows - 1) * _CARD_H_GAP

    # ---- Expand / collapse -----------------------------------------------

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
        if self._expanding:
            self._content.setMaximumHeight(_QWIDGETSIZE_MAX)

    def is_expanded(self) -> bool:
        return self._expanded

    # ---- Queued count tracking -------------------------------------------

    def _on_toggle_toggled(self, _state: bool, _app: object) -> None:
        self._refresh_queued_count()
        self.selection_changed.emit()

    def _refresh_queued_count(self) -> None:
        n     = self.queued_count()
        label = self._locale_mgr.tr("debloat.category.n_queued", n=n)
        self._header.set_queued(n, label)

    # ---- Public API ------------------------------------------------------

    def toggles(self) -> list[DebloatToggle]:
        return list(self._toggles)

    def queued_apps(self) -> list["AppXEntry"]:
        """Apps with toggle ON (excluding disabled/already-removed)."""
        return [
            t.app_entry() for t in self._toggles
            if t.is_queued() and not t.is_disabled_state()
        ]

    def queued_count(self) -> int:
        return sum(
            1 for t in self._toggles
            if t.is_queued() and not t.is_disabled_state()
        )

    def select_all(self) -> None:
        """Queue all non-disabled visible toggles."""
        changed = False
        for toggle in self._toggles:
            if (
                toggle.isVisible()
                and not toggle.is_disabled_state()
                and not toggle.is_queued()
            ):
                toggle.set_queued(True, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_queued_count()
            self.selection_changed.emit()

    def deselect_all(self) -> None:
        """Dequeue all toggles."""
        changed = False
        for toggle in self._toggles:
            if toggle.is_queued():
                toggle.set_queued(False, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_queued_count()
            self.selection_changed.emit()

    def select_recommended(self) -> None:
        """Queue recommended apps (additive; skips already-removed)."""
        changed = False
        for toggle in self._toggles:
            if (
                toggle.app_entry().recommended
                and not toggle.is_disabled_state()
                and not toggle.is_queued()
            ):
                toggle.set_queued(True, animate=False, emit=False)
                changed = True
        if changed:
            self._refresh_queued_count()
            self.selection_changed.emit()

    def refresh_all_from_system(self) -> None:
        """Bulk re-check all app presence and update toggle states.

        Issues ONE appx.bulk_detect() call for this section's packages —
        much faster than N individual is_installed() calls after a removal run.
        """
        pkg_names = [t.app_entry().package_name for t in self._toggles]
        try:
            presence = appx.bulk_detect(pkg_names)
        except Exception as exc:
            log.warning(
                "bulk_detect failed in section %s: %s", self._category.key, exc
            )
            return

        for toggle in self._toggles:
            new_val = presence.get(toggle.app_entry().package_name, False)
            toggle.update_presence(new_val)
        self._refresh_queued_count()

    def apply_search_filter(self, query: str) -> int:
        """Show/hide toggles based on query. Returns visible count."""
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

    def mark_failed(self, app: "AppXEntry", failed: bool = True) -> None:
        """Set the failed state on the toggle matching app.key."""
        for toggle in self._toggles:
            if toggle.app_entry().key == app.key:
                toggle.set_failed(failed)
                return

    def clear_all_failed(self) -> None:
        for toggle in self._toggles:
            toggle.set_failed(False)

    # ---- Mixin hooks -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._header.apply_colors(theme)

    def refresh_locale(self) -> None:
        key = f"debloat.category.{self._category.key}"
        self._header.set_title(self._locale_mgr.tr(key))
        self._header.set_count(len(self._toggles))
        self._refresh_queued_count()
