"""
WinSetup Tool — entry point.

Wires the core pipeline (paths → logging → settings → managers) and hands
control to MainWindow. Kept deliberately thin per CLAUDE.md §12: all UI
composition lives in app.ui.main_window; system calls live in app.system.

Startup order matters:
    1. QT_ENABLE_HIGHDPI_SCALING env var — set before any Qt import (§6.4).
    2. setup_logging() — first, so every later line can log.
    3. Settings.load() — so managers can read theme/locale defaults.
    4. QGuiApplication.setHighDpiScaleFactorRoundingPolicy — imperative
       call, must happen before QApplication() is constructed.
    5. QApplication → managers → MainWindow → exec.
Everything after setup_logging is wrapped in a try/except so any fatal
error is logged to file before the process dies.
"""

from __future__ import annotations

import os
import sys

# High-DPI flag (§6.4) MUST be set before any Qt module is imported.
# Scale-factor rounding policy is set imperatively below, just before
# QApplication() — the env-var form is ignored in current PySide6.
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from app.core.elevation import is_elevated, relaunch_as_admin
from app.core.i18n import LocaleManager
from app.core.logging_setup import get_logger, setup_logging
from app.core.paths import IS_FROZEN, IS_PORTABLE, resource
from app.core.settings import Settings
from app.core.theming import ThemeManager
from app.ui.main_window import MainWindow

# pywinstyles is Windows-only and optional. Import guard keeps the app
# runnable on platforms without the package (dev on non-Windows, frozen
# builds where the wheel is missing, etc.). When unavailable we skip the
# acrylic effect and log the fact — no functional loss.
try:
    import pywinstyles

    _PYWINSTYLES_AVAILABLE = True
except ImportError:
    pywinstyles = None   # type: ignore[assignment]
    _PYWINSTYLES_AVAILABLE = False


def main() -> int:
    setup_logging()
    log = get_logger(__name__)

    # Auto-elevate frozen builds. In dev mode we just warn and continue —
    # no relaunch, no loop, developer manages their own shell privileges.
    if not is_elevated():
        if IS_FROZEN:
            log.info("Not running as admin — attempting auto-elevation via UAC.")
            if relaunch_as_admin():
                return 0   # elevated copy launched; exit non-elevated process
            log.error("Admin privileges denied or relaunch failed. Exiting.")
            return 1
        else:
            log.warning(
                "Running in dev mode without admin. Registry/service ops will "
                "fail. Run your terminal as admin for full functionality."
            )

    try:
        log.info(
            "WinSetup Tool starting — Python %s, frozen=%s, portable=%s",
            sys.version.split()[0],
            IS_FROZEN,
            IS_PORTABLE,
        )

        settings = Settings()
        settings.load()
        log.info(
            "Loaded settings: theme=%r, locale=%r, first_run=%s",
            settings.get("theme"),
            settings.get("locale"),
            settings.get("first_run"),
        )

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication(sys.argv)

        icon_path = resource("icons/app.ico")
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

        theme_mgr = ThemeManager(settings)
        locale_mgr = LocaleManager(settings)

        log.info(
            "Managers initialized: theme=%r, locale=%r",
            theme_mgr.current_name(),
            locale_mgr.current(),
        )
        log.info("is_elevated=%s", is_elevated())

        window = MainWindow(settings, theme_mgr, locale_mgr)
        window.show()

        # Acrylic must be applied AFTER show() — the backing HWND has to
        # exist before pywinstyles can flip the DWM attributes. Same
        # timing constraint as the DWM titlebar sync (§6.3).
        if sys.platform == "win32" and _PYWINSTYLES_AVAILABLE:
            try:
                pywinstyles.apply_style(window, "acrylic")
                log.info("Applied acrylic window style via pywinstyles.")
            except Exception as exc:   # noqa: BLE001 — pywinstyles surface varies
                log.warning("Could not apply acrylic style: %s", exc)
        elif sys.platform == "win32":
            log.info(
                "pywinstyles not installed — running with solid body_bg. "
                "Install via `pip install -r requirements.txt` to enable "
                "the acrylic effect."
            )
        else:
            log.info(
                "Non-Windows platform (%s) — acrylic disabled, solid body_bg.",
                sys.platform,
            )

        return app.exec()
    except Exception:
        log.exception("Fatal error during startup")
        return 1


if __name__ == "__main__":
    sys.exit(main())
