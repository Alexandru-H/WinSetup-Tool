"""
optimizations_page.py — Windows Optimizations page.

Contract (CLAUDE.md §4.1, §8.1):
    * Layout mirrors AppsPage: top bar (search + 3 action buttons) →
      scrollable list of OptimizationCategorySection widgets. The global
      ActionFooter lives on MainWindow; this page drives it via signals.
    * Selection semantics: "active" = toggle is ON (tweak applied / to-apply).
    * Batch actions:
        Enable All       — turn ON every visible toggle
        Disable All      — turn OFF every toggle
        ★ Recommended    — additive; only turns ON recommended tweaks
    * Footer state tracks active count: IDLE at 0, READY when > 0.
    * footer_action_label_requested(str) lets this page request "Apply (N)"
      instead of the default "Install (N)" text on the ActionFooter button.
    * Apply logic runs on a QThread via ApplyWorker.  Cancellation is
      cooperative (flag-based; current apply_fn completes naturally).
    * After batch completion, toggles are refreshed from the live system
      via refresh_all_from_system() so the UI reflects the true final state.
    * Restart prompt is shown (non-modal) when any successful tweak had
      requires_restart=True.  User must explicitly click "Restart now".
    * ALL colours from palette dict; ALL user-visible strings via i18n.
    * ZERO subprocess / registry calls here (system ops live in app/system/).

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
from app.data.optimizations_catalog import OPTIMIZATIONS_CATALOG
from app.system.apply_worker import ApplyWorker, schedule_restart
from app.ui.styles.scrollbar_style import build_scrollbar_qss
from app.ui.widgets.action_footer import ActionFooter
from app.ui.widgets.optimization_category_section import OptimizationCategorySection
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager
    from app.data.optimizations_catalog import TweakEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_TOPBAR_H           = 56    # top bar fixed height (px)
_TOPBAR_RADIUS      = 16    # search / button pill radius (px)
_SEARCH_H           = 32    # search input height (px)
_BTN_H              = 32    # action button height (px)
_SEARCH_DEBOUNCE_MS = 200   # debounce delay for live search filtering
_SCROLL_BOTTOM_PAD  = 80    # bottom padding clears the floating footer


# ---------------------------------------------------------------------------
# Internal QSS helpers (mirrors AppsPage for visual consistency)
# ---------------------------------------------------------------------------

def _outline_btn_qss(theme: dict[str, str]) -> str:
    """QSS for outline-style action buttons (accent border, hover fill)."""
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
# OptimizationsPage
# ---------------------------------------------------------------------------

class OptimizationsPage(QWidget, ThemedWidget, LocalizedWidget):
    """Windows optimizations page — browse, search, enable/disable tweaks.

    The global ActionFooter is owned by MainWindow.  This page drives it
    exclusively through the five signals below.  MainWindow wires them at
    construction via _wire_action_page().

    Signals:
        footer_state_requested(State, int)         → ActionFooter.set_state
        footer_progress_requested(int, object)     → ActionFooter.set_progress
        footer_item_requested(object, object, object) → ActionFooter.set_current_item
        footer_failed_count_requested(int)         → ActionFooter.set_failed_count
        footer_action_label_requested(str)         → ActionFooter.set_action_label
    """

    # ---- Signals for the global ActionFooter ----------------------------
    footer_state_requested        = Signal(object, int)            # (State, total)
    footer_progress_requested     = Signal(int, object)            # (current, percent|None)
    footer_item_requested         = Signal(object, object, object) # (name, speed, stage)
    footer_failed_count_requested = Signal(int)
    footer_action_label_requested = Signal(str)                    # "Apply" override

    def __init__(
        self,
        settings:    "Settings",
        theme_mgr:   "ThemeManager",
        locale_mgr:  "LocaleManager",
        parent:      QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings   = settings
        self._theme_mgr  = theme_mgr
        self._locale_mgr = locale_mgr

        # Local mirror of the global footer state — lets _on_apply_clicked
        # dispatch correctly without a direct reference to the footer widget.
        self._footer_state: ActionFooter.State = ActionFooter.State.IDLE

        # Debounce timer — fires _apply_search 200ms after typing stops
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)

        # Apply worker plumbing — created on demand, torn down after batch
        self._apply_thread:         QThread | None      = None
        self._apply_worker:         ApplyWorker | None  = None
        self._current_apply_total:  int                 = 0
        self._failed_tweaks:        list                = []   # list[TweakEntry]

        self._sections: list[OptimizationCategorySection] = []

        self._build_ui()
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

        # Emit initial IDLE footer state (no-op until MainWindow connects signals)
        self._update_footer()

    # ---- Public API for MainWindow --------------------------------------

    def is_action_page(self) -> bool:
        """Declares that this page needs the global ActionFooter."""
        return True

    def handle_footer_action(self) -> None:
        """Called by MainWindow when the global footer button is clicked."""
        self._on_apply_clicked()

    def sync_footer(self) -> None:
        """Re-emit current state AND request the 'Apply' button label.

        Called by MainWindow whenever this page becomes visible so the global
        footer reflects the current active-tweak count and shows 'Apply (N)'
        rather than the default 'Install (N)'.
        """
        self.footer_action_label_requested.emit("Apply")
        self._update_footer()

    # ---- Build ----------------------------------------------------------

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

        # --- Search input with floating magnifier glyph ------------------
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

        # Magnifier icon floated inside the input via text-margin trick
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

        # --- Action buttons ----------------------------------------------
        self._btn_enable_all = QPushButton(bar)
        self._btn_enable_all.setFixedHeight(_BTN_H)
        self._btn_enable_all.clicked.connect(self.enable_all)
        layout.addWidget(self._btn_enable_all, 0)

        self._btn_disable_all = QPushButton(bar)
        self._btn_disable_all.setFixedHeight(_BTN_H)
        self._btn_disable_all.setEnabled(False)   # enabled when count > 0
        self._btn_disable_all.clicked.connect(self.disable_all)
        layout.addWidget(self._btn_disable_all, 0)

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
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll = scroll

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_layout = QVBoxLayout(inner)
        # 16px sides, 0 top, 80px bottom (clears the floating footer)
        inner_layout.setContentsMargins(16, 0, 16, _SCROLL_BOTTOM_PAD)
        inner_layout.setSpacing(6)

        for cat in OPTIMIZATIONS_CATALOG:
            section = OptimizationCategorySection(
                cat, self._theme_mgr, self._locale_mgr, inner
            )
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

    def active_count(self) -> int:
        """Total number of toggles currently ON across all sections."""
        return sum(s.active_count() for s in self._sections)

    def active_tweaks(self) -> list["TweakEntry"]:
        """All TweakEntries whose toggle is currently ON."""
        return [t for s in self._sections for t in s.active_tweaks()]

    def enable_all(self) -> None:
        """Turn ON all visible toggles (across all visible sections)."""
        for s in self._sections:
            if s.isVisible():
                s.select_all()

    def disable_all(self) -> None:
        """Turn OFF all toggles (regardless of visibility)."""
        for s in self._sections:
            s.deselect_all()

    def select_recommended(self) -> None:
        """Additive: turn ON recommended tweaks; leave others unchanged."""
        for s in self._sections:
            s.select_recommended()

    def _on_selection_changed(self) -> None:
        self._update_footer()
        self._update_button_states()

    def _update_footer(self) -> None:
        S = ActionFooter.State
        # Don't override an in-flight apply operation
        if self._footer_state == S.RUNNING:
            return
        n = self.active_count()
        self._set_footer_state(S.READY if n > 0 else S.IDLE, total=n)

    def _update_button_states(self) -> None:
        n = self.active_count()
        self._btn_disable_all.setEnabled(n > 0)
        self._apply_btn_qss()

    # ---- Search --------------------------------------------------------

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
                # Restore default expand state when search is cleared
                if section.is_expanded() != section._category.default_expanded:
                    section.set_expanded(
                        section._category.default_expanded, animate=False
                    )
        log.debug("Optimizations search filter applied: %r", query)

    # ---- Apply dispatcher + worker flow --------------------------------

    def _on_apply_clicked(self) -> None:
        """Dispatch footer click based on current footer state."""
        S = ActionFooter.State
        state = self._footer_state

        if state in (S.READY, S.DONE):
            self._start_apply()
        elif state == S.RUNNING:
            self._confirm_cancel()
        # IDLE: button disabled; defensive no-op.

    def _start_apply(self) -> None:
        tweaks = self.active_tweaks()
        if not tweaks:
            return

        # Clear previous failures before a fresh run
        self._failed_tweaks.clear()
        for section in self._sections:
            section.clear_all_failed()

        self._current_apply_total = len(tweaks)

        # Footer → RUNNING
        self._set_footer_state(ActionFooter.State.RUNNING, len(tweaks))

        # Build worker and move to its own thread
        self._apply_thread = QThread(self)
        self._apply_worker = ApplyWorker()
        self._apply_worker.moveToThread(self._apply_thread)

        self._apply_worker.tweak_started.connect(self._on_tweak_started)
        self._apply_worker.tweak_finished.connect(self._on_tweak_finished)
        self._apply_worker.batch_finished.connect(self._on_batch_finished)
        self._apply_worker.cancelled.connect(self._on_apply_cancelled)

        self._apply_thread.start()

        QMetaObject.invokeMethod(
            self._apply_worker,
            "start_apply",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG("QVariantList", list(tweaks)),
        )
        log.info("Apply batch dispatched: %d tweak(s).", len(tweaks))

    def _on_tweak_started(self, idx: int, total: int, tweak: object) -> None:
        self.footer_progress_requested.emit(idx, None)
        self.footer_item_requested.emit(tweak.name, None, "Applying")  # type: ignore[union-attr]

    def _on_tweak_finished(
        self, idx: int, total: int, tweak: object, success: bool
    ) -> None:
        log.info(
            "Tweak %d/%d %s: %s",
            idx, total, "OK" if success else "FAILED", tweak.key,  # type: ignore[union-attr]
        )
        if not success:
            self._failed_tweaks.append(tweak)
            for section in self._sections:
                section.mark_failed(tweak, True)  # type: ignore[arg-type]

    def _on_batch_finished(
        self, successes: int, failures: int, restart_needed: bool
    ) -> None:
        log.info(
            "Apply batch done: %d ok, %d failed, restart=%s",
            successes, failures, restart_needed,
        )
        self._set_footer_state(
            ActionFooter.State.DONE, self._current_apply_total
        )
        self.footer_failed_count_requested.emit(failures)

        # Refresh toggles to reflect the true post-apply system state
        for section in self._sections:
            section.refresh_all_from_system()

        self._cleanup_apply_thread()

        # Restart prompt only when at least one successful tweak needs it
        if restart_needed and successes > 0:
            self._prompt_restart()

    def _on_apply_cancelled(
        self, successes: int, attempted: int, restart_needed: bool
    ) -> None:
        failures = attempted - successes
        log.info(
            "Apply cancelled after %d/%d tweaks (%d ok, %d failed, restart=%s)",
            attempted, self._current_apply_total,
            successes, failures, restart_needed,
        )
        self._set_footer_state(
            ActionFooter.State.DONE, self._current_apply_total
        )
        self.footer_failed_count_requested.emit(failures)

        for section in self._sections:
            section.refresh_all_from_system()

        self._cleanup_apply_thread()

        # Prompt even on cancel if some tweaks went through and need restart
        if restart_needed and successes > 0:
            self._prompt_restart()

    def _cleanup_apply_thread(self) -> None:
        if self._apply_thread is not None:
            self._apply_thread.quit()
            self._apply_thread.wait(3000)
            self._apply_thread = None
            self._apply_worker = None

    def _confirm_cancel(self) -> None:
        if self._apply_worker is None:
            return

        tr = self._locale_mgr.tr
        box = QMessageBox(self)
        box.setWindowTitle(tr("optimizations.cancel_dialog.title"))
        box.setText(tr("optimizations.cancel_dialog.text"))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setIcon(QMessageBox.Icon.Warning)

        if box.exec() == QMessageBox.StandardButton.Yes:
            log.info("User confirmed cancel.")
            if self._apply_worker is not None:
                self._apply_worker.cancel()
            # Worker emits `cancelled` → _on_apply_cancelled → footer DONE.

    def _prompt_restart(self) -> None:
        """Show a non-modal restart suggestion after a successful apply."""
        tr = self._locale_mgr.tr
        box = QMessageBox(self)
        box.setWindowTitle(tr("optimizations.restart_dialog.title"))
        box.setText(tr("optimizations.restart_dialog.text"))

        restart_btn = box.addButton(
            tr("optimizations.restart_dialog.restart_now"),
            QMessageBox.ButtonRole.AcceptRole,
        )
        box.addButton(
            tr("optimizations.restart_dialog.later"),
            QMessageBox.ButtonRole.RejectRole,
        )
        box.setIcon(QMessageBox.Icon.Information)

        box.exec()
        if box.clickedButton() == restart_btn:
            log.info("User chose restart now.")
            self._initiate_restart()
        else:
            log.info("User postponed restart.")

    def _initiate_restart(self) -> None:
        """Delegate restart scheduling to app/system — never calls subprocess here."""
        schedule_restart(delay_seconds=5)

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
        self._btn_enable_all.setStyleSheet(qss)
        self._btn_disable_all.setStyleSheet(qss)
        self._btn_recommended.setStyleSheet(qss)

    def refresh_locale(self) -> None:
        tr = self._locale_mgr.tr
        self._search.setPlaceholderText(tr("optimizations.search.placeholder"))
        self._btn_enable_all.setText(tr("optimizations.actions.enable_all"))
        self._btn_disable_all.setText(tr("optimizations.actions.disable_all"))
        self._btn_recommended.setText(tr("optimizations.actions.select_recommended"))


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
    win.setWindowTitle("OptimizationsPage — smoke test (no global footer)")
    win.resize(900, 700)

    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout
    layout = _QVBoxLayout(win)
    layout.setContentsMargins(0, 0, 0, 0)

    page = OptimizationsPage(_settings, _theme_mgr, _locale_mgr, win)
    layout.addWidget(page)

    def _apply_bg(theme: dict) -> None:
        win.setStyleSheet(f"background-color: {theme['panel_bg']};")

    _apply_bg(_theme_mgr.current())
    _theme_mgr.themeChanged.connect(_apply_bg)

    win.show()

    # Manual inspection checklist (no automated assertions):
    #   1. Privacy and Bloatware sections should start EXPANDED.
    #   2. Explorer, Performance, Services should start COLLAPSED.
    #   3. Each toggle shows real system state (check_fn called on load).
    #   4. Clicking a toggle changes state + updates "N active" badge.
    #   5. Search "telemetry" → only "Disable telemetry" visible in Privacy.
    #   6. Click ★ Recommended → 15 toggles turn on (catalog data).
    #   7. "Disable All" button enabled only when count > 0.

    sys.exit(qapp.exec())
