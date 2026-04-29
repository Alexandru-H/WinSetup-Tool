"""
remove_worker.py — Qt worker that removes AppX packages on a background thread.

Contract (CLAUDE.md §4.1, §4.4):
    * RemoveWorker is a QObject moved to a QThread by DebloatPage.
      The page connects signals and invokes start_remove() via
      QMetaObject.invokeMethod with a QueuedConnection.
    * The worker iterates AppXEntry items in order. For each it emits
      app_started, calls appx.remove_package(), then emits app_finished.
    * After all apps (or cancel), emits batch_finished / cancelled with
      success and failure counts.
    * Cancellation is COOPERATIVE: _cancel_requested is set by cancel();
      the currently-running PowerShell call is allowed to finish naturally.
      AppX removals take seconds each, not minutes — acceptable latency.
    * remove_package calls are wrapped in try/except: exceptions are treated
      as failures (logged at ERROR), never propagated.
    * Only PySide6.QtCore imports allowed (QObject, Signal, Slot) —
      this module is UI-agnostic.

Signal summary:
    app_started(idx, total, AppXEntry)         — before remove_package call
    app_finished(idx, total, AppXEntry, ok)    — after remove_package returns
    batch_finished(successes, failures)
    cancelled(successes_so_far, attempted)
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.core.logging_setup import get_logger
from app.data.debloat_catalog import AppXEntry
from app.system import appx

log = get_logger(__name__)


class RemoveWorker(QObject):
    """Removes a sequence of AppXEntries via appx.remove_package().

    Designed to run on a QThread. Holds zero widget references.

    Signal summary:
        app_started(idx, total, app)        — before remove_package call
        app_finished(idx, total, app, ok)   — after remove_package returns
        batch_finished(successes, failures)
        cancelled(successes_so_far, attempted)
    """

    app_started    = Signal(int, int, object)        # idx, total, AppXEntry
    app_finished   = Signal(int, int, object, bool)  # idx, total, AppXEntry, success
    batch_finished = Signal(int, int)                # successes, failures
    cancelled      = Signal(int, int)                # successes_so_far, attempted

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_requested: bool = False

    @Slot("QVariantList")
    def start_remove(self, apps: Sequence[AppXEntry]) -> None:
        """Iterate apps and call remove_package on each.

        Runs on the worker thread via a QueuedConnection from the UI thread.
        """
        self._cancel_requested = False
        total     = len(apps)
        successes = 0
        failures  = 0

        for idx, app_entry in enumerate(apps, start=1):
            if self._cancel_requested:
                log.info(
                    "Remove cancelled before app %d/%d (%s)",
                    idx, total, app_entry.key,
                )
                self.cancelled.emit(successes, idx - 1)
                return

            log.info(
                "Removing %d/%d: %s (%s)",
                idx, total, app_entry.key, app_entry.package_name,
            )
            self.app_started.emit(idx, total, app_entry)

            try:
                ok = bool(appx.remove_package(app_entry.package_name))
            except Exception as exc:
                log.exception(
                    "remove_package raised for %s: %s", app_entry.key, exc
                )
                ok = False

            if ok:
                successes += 1
                log.debug("  %s: removed", app_entry.key)
            else:
                failures += 1
                log.error("  %s: FAILED", app_entry.key)

            self.app_finished.emit(idx, total, app_entry, ok)

        log.info(
            "Remove batch complete: %d success, %d failed",
            successes, failures,
        )
        self.batch_finished.emit(successes, failures)

    @Slot()
    def cancel(self) -> None:
        """Request cooperative cancellation.

        The currently-running remove_package call completes naturally before
        the loop exits.
        """
        log.info("Cancel requested on RemoveWorker.")
        self._cancel_requested = True
