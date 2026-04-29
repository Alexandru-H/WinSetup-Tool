"""
Main application window — stitches Phase 1 together.

Layout (CLAUDE.md §9):

    QMainWindow
     └─ central widget (body_bg / DWM acrylic)
         └─ QHBoxLayout (8px margins all sides, spacing 0)
             ├─ Sidebar (carve-out nav, panel_bg, fixed 220 wide)
             └─ RightPanel (panel_bg QStackedWidget, optional bottom carves)
         [floating over central, below RightPanel on action pages:]
         └─ ActionFooter (72px pill, parented to _central, absolute position)

Design intent:
    * MainWindow is intentionally NOT a ThemedWidget subclass. The window
      is a container, not a themed leaf: it re-applies its OWN stylesheets
      (body_bg on central, panel_bg on the content stack) and drives the
      fade animation. Child widgets are themed/localized through their own
      mixin connections — MainWindow does not micro-manage them.
    * Theme mode / accent / locale controls live on the Settings page.
    * Titlebar DWM sync is wired on the first show() (DWM ignores the call
      before the HWND is mapped — see titlebar.py).
    * The global ActionFooter is parented to _central and positioned
      absolutely via setGeometry. It is shown only on action pages (Apps,
      and in future Optimizations / Debloat / Uninstall). On action pages
      the central layout's bottom margin is expanded to 104px, shortening
      the RightPanel so the footer can float in the gap below it.

Fade transition on theme switch (§8.2):
    Fade the central widget to 0.55 opacity (160ms, OutCubic), re-apply
    MainWindow's own stylesheets, then fade back to 1.0 (200ms, InCubic).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.core.paths import resource
from app.ui.pages.apps_page import AppsPage
from app.ui.pages.debloat_page import DebloatPage
from app.ui.pages.home_page import HomePage
from app.ui.pages.optimizations_page import OptimizationsPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.uninstall_page import UninstallPage
from app.ui.pages.wallpaper_page import WallpaperPage
from app.ui.sidebar import Sidebar
from app.ui.titlebar import connect_titlebar_to_theme
from app.ui.widgets.action_footer import ActionFooter
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

log = get_logger(__name__)

_MAIN_ROUTES: tuple[str, ...] = (
    "home",
    "apps",
    "wallpaper",
    "optimizations",
    "debloat",
    "uninstall",
)
_SETTINGS_ROUTE: str = "settings"

# Bottom margin reserved for the floating footer (16 gap + 72 footer + 16 pad).
_FOOTER_BOTTOM_MARGIN = 104


# ==========================================================================
# RightPanel — content stack with optional bottom-corner carve-outs
# ==========================================================================

class RightPanel(QStackedWidget):
    """QStackedWidget that paints panel_bg with optional bottom-corner carves.

    Two modes (toggled via setCarveBottomCorners):
        False (default) — all 4 corners rounded at _EXTERIOR_RADIUS (8px).
                          Used on non-action pages (Home, Wallpaper, Settings).
        True            — top corners remain 8px; bottom-left and bottom-right
                          get 18px inward concave arcs, matching the sidebar's
                          carve vocabulary exactly (same quadTo technique,
                          same _CARVE_RADIUS constant).

    The panel_bg colour is read at paint time from a getter lambda so it
    always reflects the latest theme without needing an explicit signal
    connection or a stored QColor.
    """

    _EXTERIOR_RADIUS = 8    # rounded top corners (and bottom when not carving)
    _CARVE_RADIUS    = 18   # inward arc radius on carved bottom corners

    def __init__(self, panel_bg_getter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel_bg_getter = panel_bg_getter
        self._carve_bottom: bool = False

    def setCarveBottomCorners(self, enabled: bool) -> None:
        if self._carve_bottom == enabled:
            return
        self._carve_bottom = enabled
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        w   = float(self.width())
        h   = float(self.height())
        r   = float(self._EXTERIOR_RADIUS)
        r_c = float(self._CARVE_RADIUS)

        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0.0, 0.0, w, h), r, r)

        if self._carve_bottom:
            # Bottom-left carve: inward quarter-circle arc using quadTo,
            # matching the sidebar's _CARVE_RADIUS / quadTo technique.
            # Subtracts the triangular corner region (left edge + bottom edge
            # + concave arc back to start).
            bl = QPainterPath()
            bl.moveTo(0.0, h - r_c)
            bl.lineTo(0.0, h)
            bl.lineTo(r_c, h)
            bl.quadTo(0.0, h, 0.0, h - r_c)
            bl.closeSubpath()
            panel = panel.subtracted(bl)

            # Bottom-right carve: mirror of bottom-left.
            br = QPainterPath()
            br.moveTo(w - r_c, h)
            br.lineTo(w, h)
            br.lineTo(w, h - r_c)
            br.quadTo(w, h, w - r_c, h)
            br.closeSubpath()
            panel = panel.subtracted(br)

        p.fillPath(panel, QColor(self._panel_bg_getter()))
        p.end()

        # Let QStackedWidget draw its current child page on top.
        super().paintEvent(event)


# ==========================================================================
# Placeholder page
# ==========================================================================

class _PlaceholderPage(QWidget, ThemedWidget, LocalizedWidget):
    """Centered QLabel(tr(key)). Hot-swaps theme (text color) and locale."""

    _FONT_PT = 18

    def __init__(
        self,
        label_key: str,
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label_key = label_key

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(self._FONT_PT)
        self._label.setFont(font)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label, 1)

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._label.setStyleSheet(f"color: {theme['text_pri']};")

    def refresh_locale(self) -> None:
        self._label.setText(self._locale_mgr.tr(self._label_key))


# ==========================================================================
# MainWindow
# ==========================================================================

class MainWindow(QMainWindow):
    """Top-level window — sidebar, content stack, theme-change fade."""

    _DEFAULT_WIDTH  = 1280
    _DEFAULT_HEIGHT = 780
    _MIN_WIDTH      = 1100
    _MIN_HEIGHT     = 680

    _FADE_OUT_MS       = 160
    _FADE_IN_MS        = 200
    _FADE_MIN_OPACITY  = 0.55

    _CARD_MARGIN  = 8
    _CARD_SPACING = 0
    _CARD_RADIUS  = 8

    # Gap between panel bottom and footer top (body acrylic visible here).
    _FOOTER_GAP   = 16
    # Horizontal inset so the pill footer is narrower than the full panel.
    _FOOTER_INSET = 0

    def __init__(
        self,
        settings: "Settings",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
    ) -> None:
        super().__init__()
        self._settings   = settings
        self._theme_mgr  = theme_mgr
        self._locale_mgr = locale_mgr

        self._pending_theme:    dict[str, str] | None = None
        self._titlebar_wired:   bool = False
        self._current_page_key: str | None = None

        self.resize(self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT)
        self.setMinimumSize(self._MIN_WIDTH, self._MIN_HEIGHT)
        self._refresh_window_title()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        icon_path = resource("icons/app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._wire_signals()
        self._apply_window_stylesheets(theme_mgr.current())
        # Ensure initial layout matches the default page (no footer/carve).
        self._update_layout_for_page(_MAIN_ROUTES[0])

    # ---- Build ----------------------------------------------------------

    def _build_ui(self) -> None:
        self._central = QWidget()
        self._central.setObjectName("central")
        self.setCentralWidget(self._central)

        m = self._CARD_MARGIN
        self._central_margins = (m, m, m, m)   # stored for dynamic restoration

        self._central_layout = QHBoxLayout(self._central)
        self._central_layout.setContentsMargins(*self._central_margins)
        self._central_layout.setSpacing(self._CARD_SPACING)

        # --- Sidebar ---------------------------------------------------------
        self._sidebar = Sidebar(self._theme_mgr, self._locale_mgr)
        for route in _MAIN_ROUTES:
            self._sidebar.add_nav_button(route, f"nav.{route}")
        self._sidebar.add_nav_button(
            _SETTINGS_ROUTE,
            f"nav.{_SETTINGS_ROUTE}",
            spacer_before=True,
        )

        # --- RightPanel (QStackedWidget with optional bottom-corner carves) --
        self._stack = RightPanel(
            lambda: self._theme_mgr.current()["panel_bg"]
        )
        self._stack.setObjectName("content_stack")

        self._pages: dict[str, QWidget] = {}
        for route in _MAIN_ROUTES:
            if route == "home":
                page: QWidget = HomePage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            elif route == "apps":
                page = AppsPage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            elif route == "wallpaper":
                page = WallpaperPage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            elif route == "optimizations":
                page = OptimizationsPage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            elif route == "debloat":
                page = DebloatPage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            elif route == "uninstall":
                page = UninstallPage(
                    self._settings, self._theme_mgr, self._locale_mgr,
                )
            else:
                page = _PlaceholderPage(
                    f"nav.{route}", self._theme_mgr, self._locale_mgr,
                )
            self._pages[route] = page
            self._stack.addWidget(page)

        settings_page = SettingsPage(
            self._settings, self._theme_mgr, self._locale_mgr,
        )
        self._pages[_SETTINGS_ROUTE] = settings_page
        self._stack.addWidget(settings_page)

        self._central_layout.addWidget(self._sidebar, 0)
        self._central_layout.addWidget(self._stack, 1)

        # --- Global ActionFooter ---------------------------------------------
        # Parented to _central so its setGeometry coordinates are in the same
        # space as _stack.geometry(). It floats over the body acrylic below
        # the panel on action pages; hidden on non-action pages.
        self._global_footer = ActionFooter(
            self._theme_mgr, self._locale_mgr, parent=self._central
        )
        self._global_footer.setVisible(False)

        # Wire all action pages → global footer via unified helper.
        for _pg in self._pages.values():
            if hasattr(_pg, "is_action_page") and _pg.is_action_page():
                self._wire_action_page(_pg)

        # --- Fade effect (theme switch) --------------------------------------
        self._opacity_effect = QGraphicsOpacityEffect(self._central)
        self._opacity_effect.setOpacity(1.0)
        self._central.setGraphicsEffect(self._opacity_effect)

        self._fade_out = QPropertyAnimation(
            self._opacity_effect, b"opacity", self
        )
        self._fade_out.setDuration(self._FADE_OUT_MS)
        self._fade_out.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_out.finished.connect(self._after_fade_out)

        self._fade_in = QPropertyAnimation(
            self._opacity_effect, b"opacity", self
        )
        self._fade_in.setDuration(self._FADE_IN_MS)
        self._fade_in.setEasingCurve(QEasingCurve.Type.InCubic)

    # ---- Action-page footer wiring helper -------------------------------

    def _wire_action_page(self, page: QWidget) -> None:
        """Connect an action page's footer signals to the global ActionFooter.

        Called for every page that returns True from is_action_page().
        The helper uses hasattr guards so pages can omit optional signals
        (e.g. footer_action_label_requested) without breaking the wiring.
        """
        page.footer_state_requested.connect(self._global_footer.set_state)
        page.footer_progress_requested.connect(self._global_footer.set_progress)
        page.footer_item_requested.connect(self._global_footer.set_current_item)
        page.footer_failed_count_requested.connect(
            self._global_footer.set_failed_count
        )
        if hasattr(page, "footer_action_label_requested"):
            page.footer_action_label_requested.connect(
                self._global_footer.set_action_label
            )

    # ---- Wiring ---------------------------------------------------------

    def _wire_signals(self) -> None:
        self._sidebar.navigationRequested.connect(self._on_nav)
        self._theme_mgr.themeChanged.connect(self._on_theme_changed)
        self._locale_mgr.localeChanged.connect(self._refresh_window_title)
        # Footer button dispatches to whichever action page is active.
        self._global_footer.action_clicked.connect(self._dispatch_footer_click)

    # ---- Navigation -----------------------------------------------------

    def _on_nav(self, route: str) -> None:
        page = self._pages.get(route)
        if page is None:
            log.warning(
                "Navigation requested unknown route %r — ignoring.", route
            )
            return
        self._stack.setCurrentWidget(page)
        self._update_layout_for_page(route)

    # ---- Footer layout management ---------------------------------------

    def _update_layout_for_page(self, page_key: str) -> None:
        """Switch panel carving and footer visibility for the given page."""
        if page_key == self._current_page_key:
            return
        self._current_page_key = page_key
        page = self._pages.get(page_key)
        is_action = (
            page is not None
            and hasattr(page, "is_action_page")
            and page.is_action_page()
        )

        self._stack.setCarveBottomCorners(is_action)
        self._global_footer.setVisible(is_action)

        m = self._central_margins
        if is_action:
            # Shrink panel height to leave room for the floating footer.
            # 16 (gap) + 72 (footer) + 16 (bottom pad) = 104px bottom margin.
            self._central_layout.setContentsMargins(m[0], m[1], m[2], _FOOTER_BOTTOM_MARGIN)
        else:
            self._central_layout.setContentsMargins(*m)

        # Reset the action button label to default before the new page can
        # customise it via sync_footer().  OptimizationsPage.sync_footer()
        # will immediately re-emit "Apply"; AppsPage doesn't override it,
        # so the footer reverts to the default "Install" label.
        self._global_footer.set_action_label("")
        # Re-sync footer display state when this page becomes visible.
        if is_action and hasattr(page, "sync_footer"):
            page.sync_footer()

        # Defer positioning one tick so Qt finishes the layout pass first.
        QTimer.singleShot(0, self._reposition_global_footer)

    def _reposition_global_footer(self) -> None:
        """Absolute-position the footer below the right panel with a 16px gap."""
        if not self._global_footer.isVisible():
            return
        geom = self._stack.geometry()   # in _central coordinates
        x = geom.left()  + self._FOOTER_INSET
        y = geom.bottom() + self._FOOTER_GAP
        w = geom.width()  - 2 * self._FOOTER_INSET
        h = self._global_footer.height()
        self._global_footer.setGeometry(x, y, w, h)
        self._global_footer.raise_()

    def _dispatch_footer_click(self) -> None:
        """Route footer button clicks to the currently active action page."""
        page = self._pages.get(self._current_page_key)
        if page is not None and hasattr(page, "handle_footer_action"):
            page.handle_footer_action()

    # ---- Theme change ---------------------------------------------------

    def _on_theme_changed(self, theme: dict[str, str]) -> None:
        self._pending_theme = theme
        self._fade_out.stop()
        self._fade_in.stop()
        self._fade_out.setStartValue(self._opacity_effect.opacity())
        self._fade_out.setEndValue(self._FADE_MIN_OPACITY)
        self._fade_out.start()

    def _after_fade_out(self) -> None:
        if self._pending_theme is not None:
            self._apply_window_stylesheets(self._pending_theme)
            self._pending_theme = None
        self._fade_in.setStartValue(self._opacity_effect.opacity())
        self._fade_in.setEndValue(1.0)
        QTimer.singleShot(0, self._fade_in.start)

    def _apply_window_stylesheets(self, theme: dict[str, str]) -> None:
        text_sec = theme["text_sec"]
        text_pri = theme["text_pri"]

        self._central.setStyleSheet(
            f"""
            #central {{ background: transparent; }}

            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 0px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {text_sec};
                min-height: 30px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {text_pri};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                background: transparent;
                height: 0px;
                border: none;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}

            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 0px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {text_sec};
                min-width: 30px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {text_pri};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                background: transparent;
                width: 0px;
                border: none;
            }}
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
            }}
            """
        )
        self._sidebar.update()
        self._stack.update()

    # ---- Locale change --------------------------------------------------

    def _refresh_window_title(self, *_args: object) -> None:
        self.setWindowTitle(self._locale_mgr.tr("app.title"))

    # ---- Qt events ------------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Defer one tick so the layout finishes repositioning children first.
        QTimer.singleShot(0, self._reposition_global_footer)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._titlebar_wired:
            return
        self._titlebar_wired = True
        QTimer.singleShot(
            0, lambda: connect_titlebar_to_theme(self, self._theme_mgr)
        )
