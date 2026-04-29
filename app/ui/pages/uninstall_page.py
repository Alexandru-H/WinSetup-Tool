"""Uninstall page — tabular list of installed apps with batch uninstall.

Layout:
    Top bar (fixed 48px):
        [Search input ——————] [Refresh] [☑ Clean leftovers]
    Table (expanding):
        QTableView with InstalledAppsModel + QSortFilterProxyModel.
        Column 0 is checkbox; columns 1–5 are read-only sortable text.
    Status label (18px):
        Shows "Scanning...", "Found N apps", or last error.
    [Global ActionFooter floats below in body acrylic — owned by MainWindow]

Workflow:
    showEvent  → deferred background scan (QThread)
    User checks rows → footer state READY, button label "Uninstall (N)"
    Click Uninstall → UninstallWorker runs on QThread
    Per app → footer progress updated; on success worker pauses while
    LeftoverConfirmDialog is shown; page handles deletion then resumes
    worker; table refreshes after full batch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.system.uninstaller import audit, remove_leftover, scan_installed_apps
from app.system.uninstaller.scanner import InstalledApp
from app.system.uninstaller.uninstall_worker import UninstallWorker
from app.ui.widgets.leftover_dialog import LeftoverConfirmDialog
from app.ui.styles.scrollbar_style import build_scrollbar_qss
from app.ui.widgets.action_footer import ActionFooter
from app.ui.widgets.checkbox_delegate import ThemedCheckboxDelegate
from app.ui.widgets.installed_apps_model import (
    COL_CHECK,
    COL_INSTALLED,
    COL_NAME,
    COL_PUBLISHER,
    COL_SIZE,
    COL_VERSION,
    InstalledAppsModel,
)

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Background scan worker (private)
# ---------------------------------------------------------------------------

class _ScanWorker(QObject):
    """Runs scan_installed_apps() on a background thread."""

    finished = Signal(list)

    @Slot()
    def run(self) -> None:
        try:
            apps = scan_installed_apps()
        except Exception as e:
            log.exception("App scan failed: %s", e)
            apps = []
        self.finished.emit(apps)


# ---------------------------------------------------------------------------
# UninstallPage
# ---------------------------------------------------------------------------

class UninstallPage(QWidget):
    """Action page — tabular uninstall with search, sort, and batch flow."""

    # Signals consumed by MainWindow._wire_action_page().
    footer_state_requested        = Signal(object, int)
    footer_progress_requested     = Signal(int, object)
    footer_item_requested         = Signal(object, object, object)
    footer_failed_count_requested = Signal(int)
    footer_action_label_requested = Signal(str)

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

        self._model = InstalledAppsModel(self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)

        self._footer_state: ActionFooter.State = ActionFooter.State.IDLE
        self._current_total: int = 0

        self._worker_thread: QThread | None = None
        self._worker: UninstallWorker | None = None

        self._scan_thread: QThread | None = None
        self._scan_worker: _ScanWorker | None = None

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)

        self._build_ui()
        self._model.selection_changed.connect(self._on_selection_changed)
        self._refresh_btn.clicked.connect(self._refresh_apps)
        self._search.textChanged.connect(lambda _: self._search_timer.start())

        self._apply_theme(theme_mgr.current())
        theme_mgr.themeChanged.connect(self._apply_theme)

        # Defer initial scan so the window can paint first.
        QTimer.singleShot(50, self._refresh_apps)

    def is_action_page(self) -> bool:
        return True

    def sync_footer(self) -> None:
        """Re-sync label and state when MainWindow switches to this page."""
        self.footer_action_label_requested.emit("Uninstall")
        self._emit_footer_state()

    # ---- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 8)
        root.setSpacing(10)

        # Top bar
        top = QHBoxLayout()
        top.setSpacing(10)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            self._locale_mgr.tr("uninstall.search.placeholder")
        )
        self._search.setFixedHeight(32)
        top.addWidget(self._search, stretch=1)

        self._refresh_btn = QPushButton(
            self._locale_mgr.tr("uninstall.actions.refresh")
        )
        self._refresh_btn.setFixedHeight(32)
        self._refresh_btn.setMinimumWidth(100)
        top.addWidget(self._refresh_btn)

        self._clean_cb = QCheckBox(
            self._locale_mgr.tr("uninstall.actions.clean_leftovers")
        )
        self._clean_cb.setChecked(True)
        top.addWidget(self._clean_cb)

        root.addLayout(top)

        # Table
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setDefaultSectionSize(28)

        hdr = self._table.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setStretchLastSection(False)

        hdr.setSectionResizeMode(COL_CHECK,     QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_NAME,      QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_PUBLISHER, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(COL_INSTALLED, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_SIZE,      QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(COL_VERSION,   QHeaderView.ResizeMode.Interactive)

        self._table.setColumnWidth(COL_CHECK,     36)
        self._table.setColumnWidth(COL_PUBLISHER, 190)
        self._table.setColumnWidth(COL_INSTALLED, 100)
        self._table.setColumnWidth(COL_SIZE,      90)
        self._table.setColumnWidth(COL_VERSION,   120)

        self._check_delegate = ThemedCheckboxDelegate(
            self._theme_mgr, self._table
        )
        self._table.setItemDelegateForColumn(COL_CHECK, self._check_delegate)

        self._table.sortByColumn(COL_NAME, Qt.SortOrder.AscendingOrder)
        root.addWidget(self._table, stretch=1)

        # Status label
        self._status = QLabel("")
        self._status.setObjectName("uninstallStatus")
        root.addWidget(self._status)

    # ---- Theme ----------------------------------------------------------

    def _apply_theme(self, palette: dict) -> None:
        bg       = palette["card"]
        text     = palette["text_pri"]
        text_sec = palette["text_sec"]
        accent   = palette["accent"]
        border   = palette["border"]
        hover    = palette["card_hover"]
        panel    = palette["panel_bg"]

        qss = f"""
        QLineEdit {{
            background: {bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 16px;
            padding: 0 14px;
            font-size: 11pt;
        }}
        QLineEdit:focus {{
            border: 1px solid {accent};
        }}
        QLineEdit::placeholder {{ color: {text_sec}; }}
        QPushButton {{
            background: transparent;
            color: {accent};
            border: 1px solid {accent};
            border-radius: 16px;
            padding: 0 14px;
            font-size: 11pt;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:disabled {{
            color: {text_sec};
            border-color: {text_sec};
        }}
        QCheckBox {{
            color: {text};
            spacing: 8px;
            font-size: 11pt;
        }}
        QTableView {{
            background: {bg};
            color: {text};
            border: 1px solid {border};
            border-radius: 8px;
            gridline-color: transparent;
            selection-background-color: {hover};
            selection-color: {text};
            font-size: 10pt;
        }}
        QTableView::item {{
            padding: 4px 8px;
            border: none;
        }}
        QTableView::item:selected {{
            background: {hover};
            color: {text};
        }}
        QHeaderView::section {{
            background: {panel};
            color: {text_sec};
            border: none;
            border-bottom: 1px solid {border};
            padding: 6px 8px;
            font-size: 10pt;
            font-weight: 600;
        }}
        QHeaderView::section:hover {{ color: {text}; }}
        QLabel#uninstallStatus {{
            color: {text_sec};
            font-size: 9pt;
        }}
        """
        qss += build_scrollbar_qss(palette)
        self.setStyleSheet(qss)

    # ---- Background scan ------------------------------------------------

    def _refresh_apps(self) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return  # already scanning
        self._status.setText(
            self._locale_mgr.tr("uninstall.status.scanning")
        )
        self._refresh_btn.setEnabled(False)

        self._scan_thread = QThread(self)
        self._scan_worker = _ScanWorker()
        self._scan_worker.moveToThread(self._scan_thread)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_thread.start()

    def _on_scan_done(self, apps: list) -> None:
        self._model.set_apps(apps)
        self._status.setText(
            self._locale_mgr.tr("uninstall.status.found", n=len(apps))
        )
        self._refresh_btn.setEnabled(True)
        self._scan_thread = None
        self._scan_worker = None

    # ---- Search ---------------------------------------------------------

    def _apply_search(self) -> None:
        self._proxy.setFilterFixedString(self._search.text().strip())

    # ---- Selection / footer state ---------------------------------------

    def _on_selection_changed(self) -> None:
        if self._footer_state in (
            ActionFooter.State.RUNNING,
            ActionFooter.State.BOOTSTRAPPING,
            ActionFooter.State.WINGET_MISSING,
        ):
            return
        self._emit_footer_state()

    def _emit_footer_state(self) -> None:
        n = self._model.checked_count()
        new_state = (
            ActionFooter.State.READY if n > 0 else ActionFooter.State.IDLE
        )
        self.footer_state_requested.emit(new_state, n)
        self._footer_state = new_state

    # ---- Uninstall flow -------------------------------------------------

    def handle_footer_action(self) -> None:
        """Called by MainWindow when the global footer button is clicked."""
        if self._footer_state in (
            ActionFooter.State.READY,
            ActionFooter.State.DONE,
        ):
            self._start_uninstall()
        elif self._footer_state == ActionFooter.State.RUNNING:
            self._confirm_cancel()

    def _start_uninstall(self) -> None:
        apps = self._model.checked_apps()
        if not apps:
            return

        self._current_total = len(apps)
        self.footer_state_requested.emit(ActionFooter.State.RUNNING, len(apps))
        self._footer_state = ActionFooter.State.RUNNING

        self._worker_thread = QThread(self)
        self._worker = UninstallWorker()
        self._worker.moveToThread(self._worker_thread)

        self._worker.app_started.connect(self._on_app_started)
        self._worker.leftovers_found.connect(self._on_leftovers_found)
        self._worker.app_finished.connect(self._on_app_finished)
        self._worker.batch_finished.connect(self._on_batch_finished)
        self._worker.cancelled.connect(self._on_cancelled)

        self._worker_thread.start()
        QMetaObject.invokeMethod(
            self._worker,
            "start_uninstall",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG("QVariantList", list(apps)),
        )
        log.info("Uninstall batch dispatched: %d app(s).", len(apps))

    def _on_app_started(self, idx: int, total: int, app: object) -> None:
        self.footer_progress_requested.emit(idx, None)
        self.footer_item_requested.emit(
            getattr(app, "display_name", str(app)), None, "Uninstalling"
        )

    def _on_leftovers_found(self, app: InstalledApp, items: list) -> None:
        """Show leftover dialog; worker is paused waiting for continue signal."""
        if not self._clean_cb.isChecked():
            log.info(
                "Leftover cleanup disabled — skipping for %s",
                app.display_name,
            )
            self._tell_worker_to_continue()
            return

        if not items:
            self._tell_worker_to_continue()
            return

        log.info(
            "Showing leftover dialog for %s (%d items).",
            app.display_name, len(items),
        )

        dlg = LeftoverConfirmDialog(
            app=app,
            items=items,
            theme_mgr=self._theme_mgr,
            locale_mgr=self._locale_mgr,
            parent=self,
        )
        dlg.exec()

        to_delete = dlg.chosen_items()
        if to_delete:
            ok_count = 0
            fail_count = 0
            for item in to_delete:
                ok = remove_leftover(item)
                action = f"cleanup_{item.kind}"
                if ok:
                    ok_count += 1
                    audit.write_event(
                        action,
                        app.display_name,
                        path=item.path,
                        size_bytes=item.size_bytes,
                        result="success",
                    )
                else:
                    fail_count += 1
                    audit.write_event(
                        "cleanup_failed",
                        app.display_name,
                        path=item.path,
                        kind=item.kind,
                        result="failed",
                    )
            log.info(
                "Leftover cleanup for %s: %d ok, %d failed.",
                app.display_name, ok_count, fail_count,
            )
        else:
            audit.write_event(
                "cleanup_skipped", app.display_name,
                items_available=len(items),
            )

        self._tell_worker_to_continue()

    def _tell_worker_to_continue(self) -> None:
        """Unblock the worker thread so it advances to the next app."""
        if self._worker is None:
            return
        try:
            self._worker._continue_event.set()
        except AttributeError:
            log.warning("Worker has no _continue_event — possible race.")

    def _on_app_finished(
        self, idx: int, total: int, app: object, success: bool
    ) -> None:
        app_name = getattr(app, "display_name", str(app))
        log.info(
            "App %d/%d %s: %s",
            idx, total, "uninstalled" if success else "FAILED", app_name,
        )

    def _on_batch_finished(self, successes: int, failures: int) -> None:
        log.info(
            "Uninstall batch done: %d ok, %d failed.", successes, failures
        )
        self.footer_state_requested.emit(
            ActionFooter.State.DONE, self._current_total
        )
        self._footer_state = ActionFooter.State.DONE
        self.footer_failed_count_requested.emit(failures)
        self._refresh_apps()
        self._cleanup_worker_thread()

    def _on_cancelled(self, successes: int, attempted: int) -> None:
        failures = attempted - successes
        log.info(
            "Uninstall cancelled: %d ok, %d attempted.", successes, attempted
        )
        self.footer_state_requested.emit(
            ActionFooter.State.DONE, self._current_total
        )
        self._footer_state = ActionFooter.State.DONE
        self.footer_failed_count_requested.emit(failures)
        self._refresh_apps()
        self._cleanup_worker_thread()

    def _cleanup_worker_thread(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
            self._worker_thread = None
            self._worker = None

    def _confirm_cancel(self) -> None:
        if self._worker is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(
            self._locale_mgr.tr("uninstall.cancel_dialog.title")
        )
        box.setText(
            self._locale_mgr.tr("uninstall.cancel_dialog.text")
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setIcon(QMessageBox.Icon.Warning)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self._worker.cancel()

    # ---- Lifecycle ------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.footer_action_label_requested.emit("Uninstall")
