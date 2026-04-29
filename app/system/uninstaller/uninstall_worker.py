"""Worker that uninstalls a sequence of apps and scans for leftovers.

Per-app flow:
    1. emit app_started(idx, total, app)
    2. run_uninstall(app)
    3. on success: find_leftovers(app)
       → if items found: clear event, emit leftovers_found(app, items),
         WAIT on _continue_event (page shows dialog, then sets it)
    4. emit app_finished(idx, total, app, success)
    5. after all apps: emit batch_finished(successes, failures)

Pause/resume: threading.Event (_continue_event) blocks the worker
thread while the UI shows the leftover dialog. The page calls
continue_after_leftovers() (or sets the event directly) to unblock.
cancel() also sets the event so a pending wait never hangs forever.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.core.logging_setup import get_logger
from app.system.uninstaller import audit, find_leftovers, run_uninstall, terminate_current
from app.system.uninstaller.scanner import InstalledApp

log = get_logger(__name__)


class UninstallWorker(QObject):
    """Sequentially uninstalls apps on a background thread."""

    app_started     = Signal(int, int, object)       # idx, total, InstalledApp
    leftovers_found = Signal(object, object)          # InstalledApp, list[LeftoverItem]
    app_finished    = Signal(int, int, object, bool)  # idx, total, app, success
    batch_finished  = Signal(int, int)                # successes, failures
    cancelled       = Signal(int, int)                # successes, attempted

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_requested: bool = False
        self._continue_event = threading.Event()

    @Slot("QVariantList")
    def start_uninstall(self, apps: Sequence[InstalledApp]) -> None:
        """Iterate apps, uninstall each, pause for leftover dialog on success.

        Runs synchronously on the worker thread. Do not call from the UI thread.
        """
        self._cancel_requested = False
        total = len(apps)
        successes = 0
        failures = 0

        for idx, app in enumerate(apps, start=1):
            if self._cancel_requested:
                log.info("Uninstall cancelled before app %d/%d", idx, total)
                self.cancelled.emit(successes, idx - 1)
                return

            log.info("Uninstalling %d/%d: %s", idx, total, app.display_name)
            audit.write_event(
                "uninstall_start",
                app.display_name,
                publisher=app.publisher,
                version=app.version,
            )
            self.app_started.emit(idx, total, app)

            try:
                ok = run_uninstall(app)
            except Exception as e:
                log.exception("Worker exception on %s: %s", app.display_name, e)
                ok = False

            if ok:
                successes += 1
                audit.write_event(
                    "uninstall_done",
                    app.display_name,
                    publisher=app.publisher,
                )
                try:
                    items = find_leftovers(app)
                    if items:
                        # clear → emit → wait: order is critical.
                        # Clearing BEFORE emit prevents a race where the page
                        # sets the event before the worker reaches wait().
                        self._continue_event.clear()
                        self.leftovers_found.emit(app, items)
                        # Block worker thread until page calls continue_after_leftovers().
                        # 5-minute safety timeout in case the UI somehow hangs.
                        resumed = self._continue_event.wait(timeout=300)
                        if not resumed:
                            log.warning(
                                "Leftover wait timed out for %s — continuing.",
                                app.display_name,
                            )
                except Exception as e:
                    log.warning(
                        "Leftover scan failed for %s: %s", app.display_name, e
                    )
            else:
                failures += 1
                audit.write_event(
                    "uninstall_failed",
                    app.display_name,
                    publisher=app.publisher,
                )

            self.app_finished.emit(idx, total, app, ok)

        log.info(
            "Uninstall batch complete: %d successes, %d failures.",
            successes, failures,
        )
        self.batch_finished.emit(successes, failures)

    @Slot()
    def continue_after_leftovers(self) -> None:
        """Resume the worker loop after the leftover dialog closes."""
        self._continue_event.set()

    @Slot()
    def cancel(self) -> None:
        """Request cancellation. Thread-safe — sets flag, kills subprocess,
        and unblocks any pending leftover wait."""
        log.info("Uninstall cancel requested.")
        self._cancel_requested = True
        terminate_current()
        self._continue_event.set()  # unblock if paused on leftover dialog
