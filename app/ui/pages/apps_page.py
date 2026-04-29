"""
apps_page.py — Apps installation page.

Contract (CLAUDE.md §4.1, §8.1, §10):
    * Layout: top bar (search + 3 selection buttons) → scrollable list of
      CategorySection widgets. ActionFooter lives on MainWindow as a global
      floating widget; AppsPage drives it via signals.
    * Search is debounced 200 ms via QTimer so live keystrokes don't thrash
      the filter loop across 28 categories.
    * Selection state is owned here: selected_count() aggregates across all
      CategorySection children. Footer state (IDLE / READY) mirrors the count
      and is refreshed on every selection_changed signal.
    * All colors come from the palette; all user-visible strings go through
      i18n. No hardcoded hex, no hardcoded English strings (button labels in
      ActionFooter are hardcoded English by spec agreement — not our concern).

Global footer API (wired by MainWindow):
    footer_state_requested(State, int)        → ActionFooter.set_state
    footer_progress_requested(int, object)    → ActionFooter.set_progress
    footer_item_requested(object, object, object) → ActionFooter.set_current_item

Layout diagram (expands to right panel stack size):

    ┌─ top bar (56 px) ───────────────────────────────────────────┐
    │  [🔍 search _______]  [Select All] [Deselect] [★ Recommend] │
    └─────────────────────────────────────────────────────────────┘
    ┌─ QScrollArea (expanding) ───────────────────────────────────┐
    │  ▼ Browsers (9)              CategorySection                 │
    │  │ □ Chrome ★  │ □ Firefox │ 2-col AppCard grid             │
    │  ▼ Communication (10)        expanded                        │
    │  ▶ Email (3)                 collapsed                       │
    │  ...                                                         │
    └─────────────────────────────────────────────────────────────┘
    [Footer floats on body acrylic BELOW the right panel — not here]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont
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
from app.data.apps_catalog import APPS_CATALOG
from app.system import winget
from app.system.install_worker import BootstrapWorker, InstallWorker
from app.ui.styles.scrollbar_style import build_scrollbar_qss
from app.ui.widgets.action_footer import ActionFooter
from app.ui.widgets.category_section import CategorySection
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager
    from app.data.apps_catalog import AppEntry

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_TOPBAR_H           = 56    # top bar fixed height (px)
_TOPBAR_RADIUS      = 16    # search / button pill radius (px)
_SEARCH_H           = 32    # search input height (px)
_BTN_H              = 32    # selection button height (px)
_SEARCH_DEBOUNCE_MS = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _outline_btn_qss(theme: dict[str, str]) -> str:
    """QSS for outline-style selection buttons (accent border, filled on hover)."""
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
    """QSS for the search QLineEdit."""
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
# AppsPage
# ---------------------------------------------------------------------------

class AppsPage(QWidget, ThemedWidget, LocalizedWidget):
    """Apps installation page — browse, search, select, and install.

    The ActionFooter is a global widget owned by MainWindow. AppsPage
    controls it exclusively via the three signals below. MainWindow wires
    those signals to the footer's setter methods on construction.
    """

    # ---- Signals for the global ActionFooter ----------------------------
    # Emitted whenever AppsPage wants to change footer state / progress.
    # MainWindow connects these to self._global_footer.set_*(…).
    footer_state_requested        = Signal(object, int)           # (State, total)
    footer_progress_requested     = Signal(int, object)           # (current, percent|None)
    footer_item_requested         = Signal(object, object, object)# (name, speed, stage)
    footer_failed_count_requested = Signal(int)                   # failure count after batch

    def __init__(
        self,
        settings: "Settings",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings   = settings
        self._theme_mgr  = theme_mgr
        self._locale_mgr = locale_mgr

        # Local mirror of the global footer state — lets _on_install_clicked
        # dispatch correctly without holding a direct reference to the footer
        # widget (which lives on MainWindow).
        self._footer_state: ActionFooter.State = ActionFooter.State.IDLE

        # Search debounce timer — fires _apply_search after 200 ms idle
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)

        self._sections: list[CategorySection] = []

        # Install/bootstrap worker plumbing — created on demand, torn down
        # in _cleanup_*_thread when the operation finishes.
        self._install_thread:   QThread | None = None
        self._install_worker:   InstallWorker | None = None
        self._bootstrap_thread: QThread | None = None
        self._bootstrap_worker: BootstrapWorker | None = None
        self._current_install_total: int = 0
        self._failed_apps: list["AppEntry"] = []

        self._build_ui()
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        # Emit initial footer state (no-op until MainWindow connects the signal)
        self._update_footer()

    # ---- Public API for MainWindow ---------------------------------------

    def is_action_page(self) -> bool:
        """Declares that this page needs the global ActionFooter."""
        return True

    def handle_footer_action(self) -> None:
        """Called by MainWindow when the global footer button is clicked."""
        self._on_install_clicked()

    def sync_footer(self) -> None:
        """Re-emit current footer state — called by MainWindow when this page
        becomes visible so the global footer reflects the current selection."""
        self._update_footer()

    # ---- Build ----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())
        root.addWidget(self._build_scroll(), 1)
        # No ActionFooter here — it lives on MainWindow as a global widget.

    def _build_topbar(self) -> QWidget:
        bar = QWidget(self)
        bar.setFixedHeight(_TOPBAR_H)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # Search input with a magnifier glyph overlaid as a left QLabel
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

        # Magnifier icon floated left inside the input via text margin trick
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

        self._btn_deselect = QPushButton(bar)
        self._btn_deselect.setFixedHeight(_BTN_H)
        self._btn_deselect.setEnabled(False)
        self._btn_deselect.clicked.connect(self.deselect_all)
        layout.addWidget(self._btn_deselect, 0)

        self._btn_recommended = QPushButton(bar)
        self._btn_recommended.setFixedHeight(_BTN_H)
        self._btn_recommended.clicked.connect(self.select_recommended)
        layout.addWidget(self._btn_recommended, 0)

        return bar

    def _build_scroll(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll = scroll

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        # 16 px sides, 0 top, 16 px bottom (footer no longer overlays the panel)
        inner_layout.setContentsMargins(16, 0, 16, 16)
        inner_layout.setSpacing(6)

        for cat in APPS_CATALOG:
            section = CategorySection(cat, self._theme_mgr, self._locale_mgr, inner)
            section.selection_changed.connect(self._on_selection_changed)
            inner_layout.addWidget(section)
            self._sections.append(section)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ---- Footer state bridge -------------------------------------------

    def _set_footer_state(
        self, state: ActionFooter.State, total: int = 0
    ) -> None:
        """Update local state mirror AND emit to the global footer."""
        self._footer_state = state
        self.footer_state_requested.emit(state, total)

    # ---- Selection management ------------------------------------------

    def selected_apps(self) -> list["AppEntry"]:
        return [
            app
            for section in self._sections
            for app in section.selected_apps()
        ]

    def selected_count(self) -> int:
        return sum(s.selected_count() for s in self._sections)

    def select_all(self) -> None:
        for s in self._sections:
            if s.isVisible():
                s.select_all()

    def deselect_all(self) -> None:
        for s in self._sections:
            s.deselect_all()

    def select_recommended(self) -> None:
        """Deselect everything first, then check only recommended apps."""
        self.deselect_all()
        for s in self._sections:
            s.select_recommended()

    def _on_selection_changed(self) -> None:
        self._update_footer()
        self._update_button_states()

    def _update_footer(self) -> None:
        S = ActionFooter.State
        # Don't override an in-flight operation. RUNNING/BOOTSTRAPPING own
        # the footer until the worker finishes; WINGET_MISSING waits for the
        # user to either click "Install winget" or change selection.
        if self._footer_state in (
            S.RUNNING, S.BOOTSTRAPPING, S.WINGET_MISSING
        ):
            return
        n = self.selected_count()
        self._set_footer_state(S.READY if n > 0 else S.IDLE, total=n)

    def _update_button_states(self) -> None:
        n = self.selected_count()
        self._btn_deselect.setEnabled(n > 0)
        self._apply_btn_qss()

    # ---- Search --------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
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
        log.debug("Search filter applied: %r", query)

    # ---- Install dispatcher --------------------------------------------

    def _on_install_clicked(self) -> None:
        """Dispatch based on current footer state (mirrored in _footer_state)."""
        S = ActionFooter.State
        state = self._footer_state

        if state in (S.READY, S.DONE):
            status = winget.detect_winget()
            if status != winget.WingetStatus.AVAILABLE:
                log.warning("Winget not available: %s", status)
                self._set_footer_state(S.WINGET_MISSING, total=0)
                return
            self._start_install()

        elif state == S.RUNNING:
            self._confirm_cancel()

        elif state == S.WINGET_MISSING:
            self._start_bootstrap()

        # IDLE / BOOTSTRAPPING: button disabled; defensive no-op.

    # ---- Install flow --------------------------------------------------

    def _start_install(self) -> None:
        apps = self.selected_apps()
        if not apps:
            return

        self._current_install_total = len(apps)
        self._failed_apps.clear()
        for section in self._sections:
            section.clear_all_failed()
        self._set_footer_state(ActionFooter.State.RUNNING, total=len(apps))

        self._install_thread = QThread(self)
        self._install_worker = InstallWorker()
        self._install_worker.moveToThread(self._install_thread)

        self._install_worker.app_started.connect(self._on_app_started)
        self._install_worker.app_progress.connect(self._on_app_progress)
        self._install_worker.app_finished.connect(self._on_app_finished)
        self._install_worker.batch_finished.connect(self._on_batch_finished)
        self._install_worker.cancelled.connect(self._on_install_cancelled)

        self._install_thread.start()

        QMetaObject.invokeMethod(
            self._install_worker,
            "start_install",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG("QVariantList", list(apps)),
        )
        log.info("Install batch dispatched: %d app(s).", len(apps))

    def _on_app_started(
        self, idx: int, total: int, app: "AppEntry"
    ) -> None:
        self.footer_progress_requested.emit(idx, 0)
        self.footer_item_requested.emit(app.name, None, "Pending")

    def _on_app_progress(
        self,
        idx: int,
        total: int,
        app: "AppEntry",
        ev: "winget.ProgressEvent",
    ) -> None:
        self.footer_progress_requested.emit(idx, ev.percent)
        self.footer_item_requested.emit(app.name, ev.speed, ev.stage.value)

    def _on_app_finished(
        self, idx: int, total: int, app: "AppEntry", success: bool
    ) -> None:
        log.info(
            "App %d/%d %s: %s",
            idx, total, "succeeded" if success else "failed", app.winget_id,
        )
        if not success:
            self._failed_apps.append(app)
            for section in self._sections:
                section.mark_failed(app, True)

    def _on_batch_finished(self, successes: int, failures: int) -> None:
        log.info("Batch done: %d succeeded, %d failed.", successes, failures)
        self._set_footer_state(
            ActionFooter.State.DONE, total=self._current_install_total
        )
        self.footer_failed_count_requested.emit(failures)
        self._cleanup_install_thread()

    def _on_install_cancelled(self, successes: int, attempted: int) -> None:
        failures = attempted - successes
        log.info(
            "Install cancelled after %d/%d apps (%d succeeded).",
            attempted, self._current_install_total, successes,
        )
        self._set_footer_state(
            ActionFooter.State.DONE, total=self._current_install_total
        )
        self.footer_failed_count_requested.emit(failures)
        self._cleanup_install_thread()

    def _cleanup_install_thread(self) -> None:
        if self._install_thread is not None:
            self._install_thread.quit()
            self._install_thread.wait(3000)
            self._install_thread = None
            self._install_worker = None

    # ---- Cancel flow ---------------------------------------------------

    def _confirm_cancel(self) -> None:
        if self._install_worker is None:
            return

        tr = self._locale_mgr.tr
        box = QMessageBox(self)
        box.setWindowTitle(tr("apps.cancel_dialog.title"))
        box.setText(tr("apps.cancel_dialog.text"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setIcon(QMessageBox.Icon.Warning)

        if box.exec() == QMessageBox.StandardButton.Yes:
            log.info("User confirmed cancel.")
            if self._install_worker is not None:
                self._install_worker.cancel()

    # ---- Bootstrap flow ------------------------------------------------

    def _start_bootstrap(self) -> None:
        self._set_footer_state(ActionFooter.State.BOOTSTRAPPING, total=0)
        self.footer_item_requested.emit(None, None, "Starting bootstrap...")

        self._bootstrap_thread = QThread(self)
        self._bootstrap_worker = BootstrapWorker()
        self._bootstrap_worker.moveToThread(self._bootstrap_thread)

        self._bootstrap_worker.progress.connect(self._on_bootstrap_progress)
        self._bootstrap_worker.finished.connect(self._on_bootstrap_finished)

        self._bootstrap_thread.start()
        QMetaObject.invokeMethod(
            self._bootstrap_worker,
            "start",
            Qt.ConnectionType.QueuedConnection,
        )
        log.info("Bootstrap dispatched.")

    def _on_bootstrap_progress(self, ev: "winget.ProgressEvent") -> None:
        self.footer_progress_requested.emit(0, ev.percent)
        self.footer_item_requested.emit("winget", ev.speed, ev.stage.value)

    def _on_bootstrap_finished(self, success: bool) -> None:
        S = ActionFooter.State
        if success:
            log.info("Bootstrap succeeded — winget now available.")
            n = self.selected_count()
            self._set_footer_state(S.READY if n > 0 else S.IDLE, total=n)
        else:
            log.error("Bootstrap failed.")
            self._set_footer_state(S.WINGET_MISSING, total=0)
            tr = self._locale_mgr.tr
            QMessageBox.warning(
                self,
                tr("apps.bootstrap_failed.title"),
                tr("apps.bootstrap_failed.text"),
            )
        self._cleanup_bootstrap_thread()

    def _cleanup_bootstrap_thread(self) -> None:
        if self._bootstrap_thread is not None:
            self._bootstrap_thread.quit()
            self._bootstrap_thread.wait(3000)
            self._bootstrap_thread = None
            self._bootstrap_worker = None

    # ---- Mixin hooks ---------------------------------------------------

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
        self._btn_deselect.setStyleSheet(qss)
        self._btn_recommended.setStyleSheet(qss)

    def refresh_locale(self) -> None:
        tr = self._locale_mgr.tr
        self._search.setPlaceholderText(tr("apps.search.placeholder"))
        self._btn_select_all.setText(tr("apps.actions.select_all"))
        self._btn_deselect.setText(tr("apps.actions.deselect_all"))
        self._btn_recommended.setText(tr("apps.actions.select_recommended"))


# ---------------------------------------------------------------------------
# Standalone smoke test
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
    win.setWindowTitle("AppsPage — smoke test (no global footer)")
    win.resize(900, 700)

    layout = QVBoxLayout(win)
    layout.setContentsMargins(0, 0, 0, 0)

    page = AppsPage(_settings, _theme_mgr, _locale_mgr, win)
    layout.addWidget(page)

    def _apply_bg(theme: dict) -> None:
        win.setStyleSheet(f"background-color: {theme['panel_bg']};")

    _apply_bg(_theme_mgr.current())
    _theme_mgr.themeChanged.connect(_apply_bg)

    win.show()
    sys.exit(qapp.exec())
