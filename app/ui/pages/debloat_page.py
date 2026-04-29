"""
debloat_page.py — AppX Bloatware Removal page.

Contract (CLAUDE.md §4.1, §8.1):
    * Layout mirrors OptimizationsPage: top bar (search + 3 action buttons) →
      scrollable list of DebloatCategorySection widgets. The global ActionFooter
      lives on MainWindow; this page drives it via signals.
    * Detection: one appx.bulk_detect() call at construction covers all 44
      catalog apps in a single PowerShell round-trip. On IoT LTSC, all packages
      are absent so all toggles start disabled ("Already removed") — expected.
    * Action semantics: "Remove" (footer button) queues selected apps for
      removal via RemoveWorker on a QThread.
    * After worker finishes: sections refresh from system so newly-removed apps
      switch to the disabled/Already-removed state automatically.
    * footer_action_label_requested emits "Remove" so the ActionFooter button
      reads "Remove (N)" instead of the default "Install (N)".
    * ALL colours from palette dict; ALL user-visible strings via i18n.
    * ZERO subprocess / registry calls here.

Global footer signals (wired by MainWindow._wire_action_page):
    footer_state_requested(State, int)
    footer_progress_requested(int, object)
    footer_item_requested(object, object, object)
    footer_failed_count_requested(int)
    footer_action_label_requested(str)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Q_ARG, QMetaObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.data.debloat_catalog import DEBLOAT_CATALOG
from app.system import appx
from app.system.remove_worker import RemoveWorker
from app.ui.styles.scrollbar_style import build_scrollbar_qss
from app.ui.widgets.action_footer import ActionFooter
from app.ui.widgets.debloat_category_section import DebloatCategorySection
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager
    from app.data.debloat_catalog import AppXEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_TOPBAR_H           = 56
_TOPBAR_RADIUS      = 16
_SEARCH_H           = 32
_BTN_H              = 32
_SEARCH_DEBOUNCE_MS = 200
_SCROLL_BOTTOM_PAD  = 80


# ---------------------------------------------------------------------------
# Internal QSS helpers (mirrors OptimizationsPage for visual consistency)
# ---------------------------------------------------------------------------

def _outline_btn_qss(theme: dict[str, str]) -> str:
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {theme['accent']};
            border: 1px solid {theme['accent']};
            border-radius: {_TOPBAR_RADIUS}px;
            padding: 0 14px;
            font-size: 11pt;
        }}
        QPushButton:hover {{
            background-color: {theme['card_hover']};
        }}
        QPushButton:disabled {{
            color: {theme['text_sec']};
            border-color: {theme['text_sec']};
        }}
    """


def _search_qss(theme: dict[str, str]) -> str:
    return f"""
        QLineEdit {{
            background-color: {theme['card']};
            color: {theme['text_pri']};
            border: 1px solid {theme['border']};
            border-radius: {_TOPBAR_RADIUS}px;
            padding-left: 32px;
            font-size: 11pt;
        }}
        QLineEdit:focus {{
            border: 1px solid {theme['accent']};
        }}
        QLineEdit::placeholder {{
            color: {theme['text_sec']};
        }}
    """


# ---------------------------------------------------------------------------
# DebloatPage
# ---------------------------------------------------------------------------

class DebloatPage(QWidget, ThemedWidget, LocalizedWidget):
    """AppX removal page — browse, search, queue apps for removal.

    The global ActionFooter is owned by MainWindow. This page drives it
    exclusively through the five signals below. MainWindow wires them at
    construction via _wire_action_page().

    Signals:
        footer_state_requested(State, int)
        footer_progress_requested(int, object)
        footer_item_requested(object, object, object)
        footer_failed_count_requested(int)
        footer_action_label_requested(str)
    """

    footer_state_requested        = Signal(object, int)
    footer_progress_requested     = Signal(int, object)
    footer_item_requested         = Signal(object, object, object)
    footer_failed_count_requested = Signal(int)
    footer_action_label_requested = Signal(str)

    def __init__(
        self,
        settings:   "Settings",
        theme_mgr:  "ThemeManager",
        locale_mgr: "LocaleManager",
        parent:     QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings   = settings
        self._theme_mgr  = theme_mgr
        self._locale_mgr = locale_mgr

        self._footer_state: ActionFooter.State = ActionFooter.State.IDLE

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)

        self._remove_thread:        QThread | None      = None
        self._remove_worker:        RemoveWorker | None = None
        self._current_remove_total: int                 = 0
        self._failed_apps:          list                = []

        self._sections: list[DebloatCategorySection] = []

        # Single bulk detect for all catalog apps — one PowerShell round-trip
        all_pkgs = [
            app.package_name
            for cat in DEBLOAT_CATALOG
            for app in cat.apps
        ]
        try:
            self._presence_map: dict[str, bool] = appx.bulk_detect(all_pkgs)
            log.info(
                "DebloatPage: bulk_detect complete — %d/%d packages present.",
                sum(1 for v in self._presence_map.values() if v),
                len(all_pkgs),
            )
        except Exception as exc:
            log.warning("DebloatPage: bulk_detect failed: %s — defaulting all to False.", exc)
            self._presence_map = {pkg: False for pkg in all_pkgs}

        self._build_ui()
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        self._update_footer()

    # ---- Public API for MainWindow ----------------------------------------

    def is_action_page(self) -> bool:
        return True

    def handle_footer_action(self) -> None:
        """Called by MainWindow when the global footer button is clicked."""
        self._on_remove_clicked()

    def sync_footer(self) -> None:
        """Re-emit current state and request the 'Remove' button label.

        Called by MainWindow whenever this page becomes visible so the global
        footer shows 'Remove (N)' rather than the default 'Install (N)'.
        """
        self.footer_action_label_requested.emit("Remove")
        self._update_footer()

    # ---- Build -----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())
        root.addWidget(self._build_scroll(), 1)

    def _build_topbar(self) -> QWidget:
        bar = QWidget(self)
        bar.setFixedHeight(_TOPBAR_H)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # Search input with floating magnifier glyph
        search_wrap = QWidget(bar)
        search_wrap.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        sw_layout = QHBoxLayout(search_wrap)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(0)

        self._search = QLineEdit(search_wrap)
        self._search.setFixedHeight(_SEARCH_H)
        self._search.setMaxLength(100)
        self._search.textChanged.connect(self._on_search_text_changed)
        sw_layout.addWidget(self._search)

        self._search.setTextMargins(20, 0, 0, 0)
        self._search_icon = QLabel("🔍", search_wrap)
        self._search_icon.setFixedSize(20, _SEARCH_H)
        self._search_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._search_icon.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._search_icon.setFont(QFont("Segoe UI Emoji", 11))
        self._search_icon.move(8, 0)
        self._search_icon.raise_()

        layout.addWidget(search_wrap, 1)

        self._btn_select_all = QPushButton(bar)
        self._btn_select_all.setFixedHeight(_BTN_H)
        self._btn_select_all.clicked.connect(self.select_all)
        layout.addWidget(self._btn_select_all, 0)

        self._btn_deselect_all = QPushButton(bar)
        self._btn_deselect_all.setFixedHeight(_BTN_H)
        self._btn_deselect_all.setEnabled(False)
        self._btn_deselect_all.clicked.connect(self.deselect_all)
        layout.addWidget(self._btn_deselect_all, 0)

        self._btn_recommended = QPushButton(bar)
        self._btn_recommended.setFixedHeight(_BTN_H)
        self._btn_recommended.clicked.connect(self.select_recommended)
        layout.addWidget(self._btn_recommended, 0)

        return bar

    def _build_scroll(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll = scroll

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(16, 0, 16, _SCROLL_BOTTOM_PAD)
        inner_layout.setSpacing(6)

        for cat in DEBLOAT_CATALOG:
            section = DebloatCategorySection(
                cat,
                self._theme_mgr,
                self._locale_mgr,
                self._presence_map,
                inner,
            )
            section.selection_changed.connect(self._on_selection_changed)
            inner_layout.addWidget(section)
            self._sections.append(section)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ---- Footer state bridge ---------------------------------------------

    def _set_footer_state(self, state: ActionFooter.State, total: int = 0) -> None:
        self._footer_state = state
        self.footer_state_requested.emit(state, total)

    # ---- Selection management --------------------------------------------

    def queued_count(self) -> int:
        return sum(s.queued_count() for s in self._sections)

    def queued_apps(self) -> list["AppXEntry"]:
        return [app for s in self._sections for app in s.queued_apps()]

    def select_all(self) -> None:
        """Queue all non-disabled visible toggles across all visible sections."""
        for s in self._sections:
            if s.isVisible():
                s.select_all()

    def deselect_all(self) -> None:
        """Dequeue all toggles."""
        for s in self._sections:
            s.deselect_all()

    def select_recommended(self) -> None:
        """Additive: queue recommended non-disabled apps; leave others unchanged."""
        for s in self._sections:
            s.select_recommended()

    def _on_selection_changed(self) -> None:
        self._update_footer()
        self._update_button_states()

    def _update_footer(self) -> None:
        S = ActionFooter.State
        if self._footer_state == S.RUNNING:
            return
        n = self.queued_count()
        self._set_footer_state(S.READY if n > 0 else S.IDLE, total=n)

    def _update_button_states(self) -> None:
        n = self.queued_count()
        self._btn_deselect_all.setEnabled(n > 0)
        self._apply_btn_qss()

    # ---- Search ----------------------------------------------------------

    def _on_search_text_changed(self, _text: str) -> None:
        self._search_timer.stop()
        self._search_timer.start(_SEARCH_DEBOUNCE_MS)

    def _apply_search(self) -> None:
        query = self._search.text().strip()
        for section in self._sections:
            visible_count = section.apply_search_filter(query)
            if query:
                section.setVisible(visible_count > 0)
                if visible_count > 0 and not section.is_expanded():
                    section.set_expanded(True, animate=False)
            else:
                section.setVisible(True)
                if section.is_expanded() != section._category.default_expanded:
                    section.set_expanded(
                        section._category.default_expanded, animate=False
                    )
        log.debug("Debloat search filter applied: %r", query)

    # ---- Remove dispatcher + worker flow ---------------------------------

    def _on_remove_clicked(self) -> None:
        """Dispatch footer click based on current state."""
        S = ActionFooter.State
        state = self._footer_state

        if state in (S.READY, S.DONE):
            self._start_remove()
        elif state == S.RUNNING:
            self._confirm_cancel()

    def _start_remove(self) -> None:
        apps = self.queued_apps()
        if not apps:
            return

        self._failed_apps.clear()
        for section in self._sections:
            section.clear_all_failed()

        self._current_remove_total = len(apps)
        self._set_footer_state(ActionFooter.State.RUNNING, len(apps))

        self._remove_thread = QThread(self)
        self._remove_worker = RemoveWorker()
        self._remove_worker.moveToThread(self._remove_thread)

        self._remove_worker.app_started.connect(self._on_app_started)
        self._remove_worker.app_finished.connect(self._on_app_finished)
        self._remove_worker.batch_finished.connect(self._on_batch_finished)
        self._remove_worker.cancelled.connect(self._on_cancelled)

        self._remove_thread.start()

        QMetaObject.invokeMethod(
            self._remove_worker,
            "start_remove",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG("QVariantList", list(apps)),
        )
        log.info("Remove batch dispatched: %d app(s).", len(apps))

    def _on_app_started(self, idx: int, total: int, app: object) -> None:
        self.footer_progress_requested.emit(idx, None)
        self.footer_item_requested.emit(
            app.name, None, "Removing"  # type: ignore[union-attr]
        )

    def _on_app_finished(
        self, idx: int, total: int, app: object, success: bool
    ) -> None:
        log.info(
            "App %d/%d %s: %s",
            idx, total, "removed" if success else "FAILED",
            app.key,  # type: ignore[union-attr]
        )
        if not success:
            self._failed_apps.append(app)
            for section in self._sections:
                section.mark_failed(app, True)  # type: ignore[arg-type]

    def _on_batch_finished(self, successes: int, failures: int) -> None:
        log.info("Remove batch done: %d ok, %d failed.", successes, failures)
        self._set_footer_state(
            ActionFooter.State.DONE, self._current_remove_total
        )
        self.footer_failed_count_requested.emit(failures)

        # Refresh: successfully removed apps switch to disabled "Already removed"
        for section in self._sections:
            section.refresh_all_from_system()

        self._cleanup_thread()

    def _on_cancelled(self, successes: int, attempted: int) -> None:
        failures = attempted - successes
        log.info(
            "Remove cancelled after %d/%d apps (%d ok, %d failed)",
            attempted, self._current_remove_total, successes, failures,
        )
        self._set_footer_state(
            ActionFooter.State.DONE, self._current_remove_total
        )
        self.footer_failed_count_requested.emit(failures)

        for section in self._sections:
            section.refresh_all_from_system()

        self._cleanup_thread()

    def _cleanup_thread(self) -> None:
        if self._remove_thread is not None:
            self._remove_thread.quit()
            self._remove_thread.wait(3000)
            self._remove_thread = None
            self._remove_worker = None

    def _confirm_cancel(self) -> None:
        if self._remove_worker is None:
            return

        tr = self._locale_mgr.tr
        box = QMessageBox(self)
        box.setWindowTitle(tr("debloat.cancel_dialog.title"))
        box.setText(tr("debloat.cancel_dialog.text"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setIcon(QMessageBox.Icon.Warning)

        if box.exec() == QMessageBox.StandardButton.Yes:
            log.info("User confirmed cancel.")
            if self._remove_worker is not None:
                self._remove_worker.cancel()

    # ---- Mixin hooks -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._search.setStyleSheet(_search_qss(theme))
        self._apply_btn_qss()
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }\n"
            + build_scrollbar_qss(theme)
        )

    def _apply_btn_qss(self) -> None:
        theme = self._theme_mgr.current()
        qss = _outline_btn_qss(theme)
        self._btn_select_all.setStyleSheet(qss)
        self._btn_deselect_all.setStyleSheet(qss)
        self._btn_recommended.setStyleSheet(qss)

    def refresh_locale(self) -> None:
        tr = self._locale_mgr.tr
        self._search.setPlaceholderText(tr("debloat.search.placeholder"))
        self._btn_select_all.setText(tr("debloat.actions.select_all"))
        self._btn_deselect_all.setText(tr("debloat.actions.deselect_all"))
        self._btn_recommended.setText(tr("debloat.actions.select_recommended"))


# ---------------------------------------------------------------------------
# Standalone smoke test  (python -m app.ui.pages.debloat_page)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from PySide6.QtWidgets import QApplication

    from app.core.i18n import LocaleManager
    from app.core.logging_setup import setup_logging
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

    setup_logging()
    qapp = QApplication(sys.argv)

    _settings = Settings()
    _settings.load()
    _theme_mgr  = ThemeManager(_settings)
    _locale_mgr = LocaleManager(_settings)

    win = QWidget()
    win.setWindowTitle("DebloatPage — smoke test (no global footer)")
    win.resize(1000, 750)

    from PySide6.QtWidgets import QVBoxLayout as _QVL
    _layout = _QVL(win)
    _layout.setContentsMargins(0, 0, 0, 0)

    page = DebloatPage(_settings, _theme_mgr, _locale_mgr, win)
    _layout.addWidget(page)

    def _apply_bg(theme: dict) -> None:
        win.setStyleSheet(f"background-color: {theme['panel_bg']};")

    _apply_bg(_theme_mgr.current())
    _theme_mgr.themeChanged.connect(_apply_bg)

    win.show()

    # Manual checklist:
    #   1. 3 sections render (Safe, Moderate, Aggressive).
    #   2. Safe is expanded by default; Moderate and Aggressive collapsed.
    #   3. Aggressive header shows ⚠ in warn color.
    #   4. On IoT LTSC: all toggles disabled ("Already removed") — expected.
    #   5. Search filters visible cards; sections auto-expand.
    #   6. ★ Recommended button queues non-disabled recommended apps.
    #   7. Theme switch: all colors adapt, ⚠ stays visible in both themes.

    sys.exit(qapp.exec())
