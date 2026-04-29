"""
apply_worker.py — Qt worker that applies Windows tweaks on a background thread.

Contract (CLAUDE.md §4.1, §4.4):
    * ApplyWorker is a QObject moved to a QThread by OptimizationsPage.
      The page connects signals and invokes start_apply() via
      QMetaObject.invokeMethod with a QueuedConnection.
    * The worker iterates tweaks in order.  For each it emits
      tweak_started, calls tweak.apply_fn(), then emits tweak_finished.
    * After all tweaks (or cancel), emits batch_finished / cancelled with
      success/failure counts and a restart_required flag.
    * Cancellation is COOPERATIVE: _cancel_requested is set by cancel(),
      but the currently-running apply_fn is allowed to finish naturally.
      Registry writes take <1 ms; service operations take ≤1 s — this is
      acceptable latency.
    * apply_fn calls are executed with try/except: an uncaught exception
      is treated as a failure (logged at ERROR), never propagated.
    * Only PySide6.QtCore imports are allowed (QObject, Signal, Slot) —
      this module is UI-agnostic so it can be reused by future workers.

Also exports schedule_restart() — a module-level utility kept here so
that CLAUDE.md §4.1 (no subprocess in app/ui/) is satisfied.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.core.logging_setup import get_logger
from app.data.optimizations_catalog import TweakEntry

log = get_logger(__name__)

try:
    _NO_WINDOW: int = subprocess.CREATE_NO_WINDOW
except AttributeError:
    _NO_WINDOW = 0  # non-Windows fallback


# ---------------------------------------------------------------------------
# ApplyWorker
# ---------------------------------------------------------------------------

class ApplyWorker(QObject):
    """Applies a sequence of TweakEntries via their apply_fn callbacks.

    Designed to run on a QThread.  All UI interaction happens through
    Qt signals — this class holds zero widget references.

    Signal summary:
        tweak_started(idx, total, tweak)         — before apply_fn call
        tweak_finished(idx, total, tweak, ok)    — after apply_fn returns
        batch_finished(successes, failures, restart_required)
        cancelled(successes_so_far, attempted, restart_required)
    """

    # Per-tweak lifecycle
    tweak_started  = Signal(int, int, object)        # idx, total, TweakEntry
    tweak_finished = Signal(int, int, object, bool)  # idx, total, TweakEntry, success

    # Batch lifecycle
    batch_finished = Signal(int, int, bool)  # successes, failures, restart_required
    cancelled      = Signal(int, int, bool)  # successes_so_far, attempted, restart_required

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel_requested: bool = False

    @Slot(list)
    def start_apply(self, tweaks: Sequence[TweakEntry]) -> None:
        """Iterate tweaks and call apply_fn on each.

        Runs on the worker thread via a QueuedConnection from the UI thread.
        """
        self._cancel_requested = False
        total          = len(tweaks)
        successes      = 0
        failures       = 0
        restart_needed = False

        for idx, tweak in enumerate(tweaks, start=1):
            if self._cancel_requested:
                log.info(
                    "Apply cancelled before tweak %d/%d (%s)",
                    idx, total, tweak.key,
                )
                self.cancelled.emit(successes, idx - 1, restart_needed)
                return

            log.info("Applying %d/%d: %s", idx, total, tweak.key)
            self.tweak_started.emit(idx, total, tweak)

            try:
                ok = bool(tweak.apply_fn())
            except Exception as exc:
                log.exception("apply_fn raised for %s: %s", tweak.key, exc)
                ok = False

            if ok:
                successes += 1
                if tweak.requires_restart:
                    restart_needed = True
                    log.debug(
                        "  %s: success  (restart required)", tweak.key
                    )
                else:
                    log.debug("  %s: success", tweak.key)
            else:
                failures += 1
                log.error("  %s: FAILED", tweak.key)

            self.tweak_finished.emit(idx, total, tweak, ok)

        log.info(
            "Apply batch complete: %d success, %d failed, restart=%s",
            successes, failures, restart_needed,
        )
        self.batch_finished.emit(successes, failures, restart_needed)

    @Slot()
    def cancel(self) -> None:
        """Request cooperative cancellation.

        The currently-running apply_fn completes naturally; the loop exits
        before the next tweak starts.
        """
        log.info("Cancel requested on ApplyWorker.")
        self._cancel_requested = True


# ---------------------------------------------------------------------------
# System-level utility (kept here so app/ui/ never calls subprocess directly)
# ---------------------------------------------------------------------------

def schedule_restart(delay_seconds: int = 5) -> bool:
    """Schedule a Windows restart via shutdown.exe.

    Args:
        delay_seconds: seconds before the restart is initiated (default 5).

    Returns:
        True if the shutdown command was dispatched without error.
    """
    try:
        subprocess.Popen(
            [
                "shutdown", "/r",
                "/t", str(delay_seconds),
                "/c", "WinSetupTool: restart required by optimization",
            ],
            creationflags=_NO_WINDOW,
        )
        log.info("Restart scheduled in %d second(s).", delay_seconds)
        return True
    except Exception as exc:
        log.error("Failed to schedule restart: %s", exc)
        return False
