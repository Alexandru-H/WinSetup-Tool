"""
install_worker.py — Qt worker that drives winget installs on a background thread.

Contract (CLAUDE.md §4.1, §4.4; Delivery 4B-1):
    * Two QObject workers: InstallWorker (per-item batch loop) and
      BootstrapWorker (one-shot bootstrap). Both expect to be moved to a
      QThread by the page that owns them, then invoked through queued
      connections (QMetaObject.invokeMethod with Qt.QueuedConnection).
    * The workers know NOTHING about the UI. They emit Qt signals; the
      page slots translate those into ActionFooter updates.
    * Cancellation is two-pronged: a cooperative flag stops the loop
      between apps, and winget.terminate_current() kills the running
      subprocess so the user does not have to wait for the current
      install to finish.
    * Only PySide6 imports allowed here are QObject, Signal, Slot — this
      module stays UI-agnostic so it can be reused by Optimizations,
      Debloat, and Uninstall workers in later deliveries.

Signal flow (InstallWorker, per app):
    app_started(idx, total, app)
        ->app_progress(idx, total, app, ProgressEvent)   [N times]
        ->app_finished(idx, total, app, success)
    ... after the last app:
        batch_finished(successes, failures)              # normal completion
    OR (on cancel before/between apps):
        cancelled(successes_so_far, total_attempted)
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.core.logging_setup import get_logger
from app.data.apps_catalog import AppEntry
from app.system import winget
from app.system.winget import ProgressEvent

log = get_logger(__name__)


class InstallWorker(QObject):
    """Installs a sequence of apps via winget on a worker thread."""

    # Per-app lifecycle
    app_started   = Signal(int, int, AppEntry)
    app_progress  = Signal(int, int, AppEntry, object)
    app_finished  = Signal(int, int, AppEntry, bool)

    # Batch lifecycle
    batch_finished = Signal(int, int)   # successes, failures
    cancelled      = Signal(int, int)   # successes_so_far, total_attempted

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_requested: bool = False

    @Slot(list)
    def start_install(self, apps: Sequence[AppEntry]) -> None:
        """Iterate ``apps`` and call winget.install_app for each.

        Must be invoked via a queued connection from the UI thread — this
        method runs synchronously and blocks the worker thread until the
        whole batch completes (or cancellation is honored).
        """
        self._cancel_requested = False
        total = len(apps)
        successes = 0
        failures = 0

        for idx, app in enumerate(apps, start=1):
            if self._cancel_requested:
                log.info(
                    "Install cancelled before app %d/%d.", idx, total
                )
                self.cancelled.emit(successes, idx - 1)
                return

            log.info("Installing %d/%d: %s", idx, total, app.winget_id)
            self.app_started.emit(idx, total, app)

            def on_progress(ev: ProgressEvent, _idx=idx, _app=app) -> None:
                self.app_progress.emit(_idx, total, _app, ev)

            try:
                ok = winget.install_app(app.winget_id, on_progress)
            except Exception as exc:   # noqa: BLE001
                log.exception(
                    "Worker exception on %s: %s", app.winget_id, exc
                )
                ok = False

            if ok:
                successes += 1
            else:
                failures += 1

            self.app_finished.emit(idx, total, app, ok)

            # If cancel arrived during the install, the subprocess was
            # killed; honor the flag now without starting the next app.
            if self._cancel_requested:
                log.info(
                    "Install cancelled after app %d/%d.", idx, total
                )
                self.cancelled.emit(successes, idx)
                return

        log.info(
            "Batch complete: %d succeeded, %d failed.", successes, failures
        )
        self.batch_finished.emit(successes, failures)

    @Slot()
    def cancel(self) -> None:
        """Request cancellation.

        Sets the cooperative flag (stops the loop between apps) AND
        terminates the currently-running subprocess so the active install
        is interrupted immediately.
        """
        log.info("Cancel requested.")
        self._cancel_requested = True
        winget.terminate_current()


class BootstrapWorker(QObject):
    """Runs winget.bootstrap_winget on a worker thread.

    Separate from InstallWorker because lifecycle is different — one-shot
    operation with no per-item iteration.
    """

    progress = Signal(object)   # ProgressEvent
    finished = Signal(bool)     # success

    @Slot()
    def start(self) -> None:
        try:
            ok = winget.bootstrap_winget(on_progress=self.progress.emit)
        except Exception as exc:   # noqa: BLE001
            log.exception("Bootstrap exception: %s", exc)
            ok = False
        self.finished.emit(ok)
