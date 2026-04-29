"""
Settings page — Appearance / Language / System / About.

Design:
    * Four card-style QFrame sections stacked vertically inside a
      QScrollArea (so future additions don't blow past the viewport).
    * Appearance = theme-mode segmented control + 3×3 accent color grid.
    * Language   = locale segmented control.
    * System     = portable-mode placeholder (disabled; real support lives
                   in a later phase) + reset-to-defaults button.
    * About      = version, description, GitHub link.

The page is a ThemedWidget + LocalizedWidget so the whole block hot-swaps
theme colors and translated text without a restart. Section frames, row
labels, hint labels, and buttons re-skin themselves in refresh_theme;
every translatable label is rebuilt in refresh_locale.

Sub-widget AccentSwatch is an internal QFrame: 60×60 rounded square that
paints its solid accent color and, when active, a 2px `accent`-colored
stroke to indicate selection. Tooltips pick up locale changes via the
LocalizedWidget mixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.ui.widgets.segmented import SegmentedControl
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

log = get_logger(__name__)

# 3×3 ordering of the accent keys. The first column of the first row is
# "default_dark" so the eye lands on the charcoal square when the page opens.
_SWATCH_ORDER: tuple[tuple[str, ...], ...] = (
    ("default_dark",  "default_light", "navy"),
    ("slate",         "orchid",        "mint"),
    ("beige",         "coral",         "lavender"),
)

_GITHUB_URL = "https://github.com"


# ==========================================================================
# AccentSwatch
# ==========================================================================

class AccentSwatch(QFrame, ThemedWidget, LocalizedWidget):
    """A single 60×60 rounded color square that emits its accent key on click."""

    SIZE = 60
    RADIUS = 12
    _IDLE_BORDER_PX = 1
    _ACTIVE_BORDER_PX = 2

    clicked = Signal(str)

    def __init__(
        self,
        accent_key: str,
        color_hex: str,
        theme_mgr: ThemeManager,
        locale_mgr: LocaleManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._accent_key = accent_key
        self._color = QColor(color_hex)
        self._active = False
        self._theme: dict[str, str] = {}

        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    # ---- Public ---------------------------------------------------------

    def accent_key(self) -> str:
        return self._accent_key

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.update()

    # ---- Mixin hooks ----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.update()

    def refresh_locale(self) -> None:
        self.setToolTip(self._locale_mgr.tr(f"accent.{self._accent_key}"))

    # ---- Input ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._accent_key)
            return
        super().mousePressEvent(event)

    # ---- Paint ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Every swatch gets a permanent 1px border (theme["border"]) for
        # definition; the active one upgrades to a 2px accent stroke.
        border_px = (
            self._ACTIVE_BORDER_PX if self._active else self._IDLE_BORDER_PX
        )
        # Shrink by half the border width so the stroke sits entirely
        # inside the widget rect (avoids AA clipping at the edge).
        inset = border_px / 2
        rect = self.rect().adjusted(
            int(inset), int(inset), -int(inset), -int(inset)
        )

        p.setBrush(self._color)
        if self._theme:
            border_color = (
                self._theme["accent"] if self._active else self._theme["border"]
            )
            pen = QPen(QColor(border_color))
            pen.setWidth(border_px)
            p.setPen(pen)
        else:
            p.setPen(Qt.PenStyle.NoPen)

        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.end()


# ==========================================================================
# SettingsPage
# ==========================================================================

class SettingsPage(QWidget, ThemedWidget, LocalizedWidget):
    """Central settings surface — sections + controls + live re-skin."""

    _PADDING = 32
    _SECTION_SPACING = 24
    _SECTION_INNER_PADDING = 20
    _SECTION_RADIUS = 12
    _SECTION_TITLE_PT = 14
    _ROW_LABEL_PT = 11
    _BODY_PT = 10
    _GRID_SPACING = 10

    def __init__(
        self,
        settings: Settings,
        theme_mgr: ThemeManager,
        locale_mgr: LocaleManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme_mgr = theme_mgr
        self._locale_mgr = locale_mgr

        # Registries populated during _build_ui, used by refresh_* hooks.
        self._section_frames: list[QFrame] = []
        self._section_titles: list[tuple[QLabel, str]] = []
        self._row_labels: list[tuple[QLabel, str]] = []
        self._hint_labels: list[tuple[QLabel, str]] = []
        self._body_labels: list[tuple[QLabel, str]] = []
        self._swatches: dict[str, AccentSwatch] = {}

        self._build_ui()
        self._wire_signals()

        # Initial sync of the live controls to current state.
        self._seg_theme.set_selection(theme_mgr.current_name(), animate=False)
        self._seg_locale.set_selection(locale_mgr.current(), animate=False)
        self._refresh_swatch_active()

        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    # ---- Build ----------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Transparent viewport so the content stack's panel_bg shows through.
        self._scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(self._scroll, 1)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._scroll.setWidget(content)

        sections = QVBoxLayout(content)
        sections.setContentsMargins(
            self._PADDING, self._PADDING, self._PADDING, self._PADDING
        )
        sections.setSpacing(self._SECTION_SPACING)

        sections.addWidget(self._build_appearance_section())
        sections.addWidget(self._build_language_section())
        sections.addWidget(self._build_system_section())
        sections.addWidget(self._build_about_section())
        sections.addStretch(1)

    # ---- Helpers --------------------------------------------------------

    def _make_section_frame(self, object_name: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName(object_name)
        frame.setFrameShape(QFrame.Shape.NoFrame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(
            self._SECTION_INNER_PADDING,
            self._SECTION_INNER_PADDING,
            self._SECTION_INNER_PADDING,
            self._SECTION_INNER_PADDING,
        )
        layout.setSpacing(14)
        self._section_frames.append(frame)
        return frame, layout

    def _make_section_title(self, key: str) -> QLabel:
        label = QLabel()
        font = QFont()
        font.setPointSize(self._SECTION_TITLE_PT)
        font.setWeight(QFont.Weight.Medium)
        label.setFont(font)
        self._section_titles.append((label, key))
        return label

    def _make_row_label(self, key: str) -> QLabel:
        label = QLabel()
        font = QFont()
        font.setPointSize(self._ROW_LABEL_PT)
        label.setFont(font)
        self._row_labels.append((label, key))
        return label

    def _make_hint_label(self, key: str) -> QLabel:
        label = QLabel()
        font = QFont()
        font.setPointSize(self._BODY_PT)
        label.setFont(font)
        label.setWordWrap(True)
        self._hint_labels.append((label, key))
        return label

    def _make_body_label(self, key: str) -> QLabel:
        label = QLabel()
        font = QFont()
        font.setPointSize(self._BODY_PT)
        label.setFont(font)
        label.setWordWrap(True)
        self._body_labels.append((label, key))
        return label

    # ---- Sections -------------------------------------------------------

    def _build_appearance_section(self) -> QFrame:
        frame, layout = self._make_section_frame("settings_appearance")
        layout.addWidget(self._make_section_title("settings.section.appearance"))

        # --- row: theme mode --------------------------------------------
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.setSpacing(12)
        theme_row.addWidget(self._make_row_label("settings.theme_mode"))
        theme_row.addStretch(1)

        self._seg_theme = SegmentedControl(
            [("dark", "Dark"), ("light", "Light")],
            self._theme_mgr,
        )
        theme_row.addWidget(self._seg_theme, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(theme_row)

        # --- row: accent grid -------------------------------------------
        layout.addWidget(self._make_row_label("settings.accent_color"))

        grid_wrap = QHBoxLayout()
        grid_wrap.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setSpacing(self._GRID_SPACING)
        grid.setContentsMargins(0, 0, 0, 0)

        accents = self._theme_mgr.available_accents()
        for r, row in enumerate(_SWATCH_ORDER):
            for c, key in enumerate(row):
                hex_color = accents.get(key)
                if hex_color is None:
                    log.warning(
                        "Accent key %r missing from THEME_ACCENTS — skipping.",
                        key,
                    )
                    continue
                swatch = AccentSwatch(
                    key,
                    hex_color,
                    self._theme_mgr,
                    self._locale_mgr,
                )
                self._swatches[key] = swatch
                grid.addWidget(swatch, r, c)

        grid_wrap.addLayout(grid)
        grid_wrap.addStretch(1)
        layout.addLayout(grid_wrap)

        return frame

    def _build_language_section(self) -> QFrame:
        frame, layout = self._make_section_frame("settings_language")
        layout.addWidget(self._make_section_title("settings.section.language"))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self._make_row_label("settings.interface_lang"))
        row.addStretch(1)

        self._seg_locale = SegmentedControl(
            [("ro", "RO"), ("en", "EN")],
            self._theme_mgr,
        )
        row.addWidget(self._seg_locale, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)

        return frame

    def _build_system_section(self) -> QFrame:
        frame, layout = self._make_section_frame("settings_system")
        layout.addWidget(self._make_section_title("settings.section.system"))

        # --- row: portable mode (disabled placeholder) ------------------
        portable_row = QHBoxLayout()
        portable_row.setContentsMargins(0, 0, 0, 0)
        portable_row.setSpacing(12)
        portable_row.addWidget(self._make_row_label("settings.portable_mode"))
        portable_row.addStretch(1)

        # Real toggle widget will replace this (app/ui/widgets/toggle.py,
        # planned). For now we show a visually muted disabled checkbox
        # so the row has a consistent right-edge affordance.
        self._portable_toggle = QCheckBox()
        self._portable_toggle.setEnabled(False)
        self._portable_toggle.setCursor(Qt.CursorShape.ForbiddenCursor)
        portable_row.addWidget(
            self._portable_toggle, 0, Qt.AlignmentFlag.AlignRight
        )
        layout.addLayout(portable_row)

        layout.addWidget(self._make_hint_label("settings.portable_hint"))

        # --- row: reset button ------------------------------------------
        self._reset_btn = QPushButton()
        self._reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reset_btn.setObjectName("settings_reset_btn")
        self._reset_btn.clicked.connect(self._on_reset_clicked)

        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 8, 0, 0)
        reset_row.addStretch(1)
        reset_row.addWidget(self._reset_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(reset_row)

        return frame

    def _build_about_section(self) -> QFrame:
        frame, layout = self._make_section_frame("settings_about")
        layout.addWidget(self._make_section_title("settings.section.about"))

        layout.addWidget(self._make_body_label("settings.about.version"))
        layout.addWidget(self._make_body_label("settings.about.description"))

        self._github_btn = QPushButton()
        self._github_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._github_btn.setObjectName("settings_github_btn")
        self._github_btn.setFlat(True)
        self._github_btn.clicked.connect(self._on_github_clicked)

        github_row = QHBoxLayout()
        github_row.setContentsMargins(0, 4, 0, 0)
        github_row.addWidget(self._github_btn, 0, Qt.AlignmentFlag.AlignLeft)
        github_row.addStretch(1)
        layout.addLayout(github_row)

        return frame

    # ---- Wiring ---------------------------------------------------------

    def _wire_signals(self) -> None:
        # Theme mode round-trip (set_selection is emission-free, no loop).
        self._seg_theme.selectionChanged.connect(self._theme_mgr.set_theme)
        self._theme_mgr.themeChanged.connect(self._on_theme_mgr_changed)

        # Accent selection — all swatches share the same slot.
        for swatch in self._swatches.values():
            swatch.clicked.connect(self._theme_mgr.set_accent)

        # Locale round-trip.
        self._seg_locale.selectionChanged.connect(self._locale_mgr.set_locale)
        self._locale_mgr.localeChanged.connect(
            lambda code: self._seg_locale.set_selection(code, animate=True)
        )

    def _on_theme_mgr_changed(self, _theme: dict[str, str]) -> None:
        """Keep segmented + swatch-active in sync on external mode/accent changes."""
        self._seg_theme.set_selection(
            self._theme_mgr.current_name(), animate=True
        )
        self._refresh_swatch_active()

    def _refresh_swatch_active(self) -> None:
        current = self._theme_mgr.current_accent()
        for key, swatch in self._swatches.items():
            swatch.set_active(key == current)

    # ---- Button handlers ------------------------------------------------

    def _on_reset_clicked(self) -> None:
        confirm_text = self._locale_mgr.tr("settings.reset_confirm")
        reply = QMessageBox.question(
            self,
            self._locale_mgr.tr("settings.reset"),
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok = self._settings.reset_to_defaults()
        if not ok:
            QMessageBox.warning(
                self,
                self._locale_mgr.tr("settings.reset"),
                "Could not save settings file — check logs.",
            )
            return

        # A restart is the cleanest way to rebuild managers/signal graphs
        # from the fresh defaults. We surface the requirement explicitly
        # rather than attempting a mid-flight hot reset.
        QMessageBox.information(
            self,
            self._locale_mgr.tr("settings.reset"),
            "Settings have been reset. Please restart the app for all "
            "changes to take effect.",
        )

    def _on_github_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(_GITHUB_URL))

    # ---- Mixin hooks ----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        card = theme["card"]
        card_hover = theme["card_hover"]
        text_pri = theme["text_pri"]
        text_sec = theme["text_sec"]
        accent = theme["accent"]
        accent_h = theme["accent_h"]
        err = theme["err"]

        # Each section frame — card bg, rounded corners, no border.
        for frame in self._section_frames:
            frame.setStyleSheet(
                f"QFrame#{frame.objectName()} {{"
                f"  background: {card};"
                f"  border: none;"
                f"  border-radius: {self._SECTION_RADIUS}px;"
                f"}}"
            )

        for label, _ in self._section_titles:
            label.setStyleSheet(f"color: {text_sec}; background: transparent;")
        for label, _ in self._row_labels:
            label.setStyleSheet(f"color: {text_pri}; background: transparent;")
        for label, _ in self._hint_labels:
            label.setStyleSheet(f"color: {text_sec}; background: transparent;")
        for label, _ in self._body_labels:
            label.setStyleSheet(f"color: {text_pri}; background: transparent;")

        # Reset button: neutral by default, muted-red on hover.
        self._reset_btn.setStyleSheet(
            f"""
            QPushButton#settings_reset_btn {{
                background: transparent;
                color: {text_pri};
                border: 1px solid {text_sec};
                border-radius: 8px;
                padding: 6px 16px;
            }}
            QPushButton#settings_reset_btn:hover {{
                background: {card_hover};
                color: {err};
                border-color: {err};
            }}
            """
        )

        # GitHub button: accent-colored text link with a hover shade.
        self._github_btn.setStyleSheet(
            f"""
            QPushButton#settings_github_btn {{
                background: transparent;
                color: {accent};
                border: none;
                padding: 2px 0px;
                text-align: left;
            }}
            QPushButton#settings_github_btn:hover {{
                color: {accent_h};
            }}
            """
        )

    def refresh_locale(self) -> None:
        for label, key in self._section_titles:
            label.setText(self._locale_mgr.tr(key))
        for label, key in self._row_labels:
            label.setText(self._locale_mgr.tr(key))
        for label, key in self._hint_labels:
            label.setText(self._locale_mgr.tr(key))
        for label, key in self._body_labels:
            label.setText(self._locale_mgr.tr(key))
        self._reset_btn.setText(self._locale_mgr.tr("settings.reset"))
        self._github_btn.setText(self._locale_mgr.tr("settings.about.github"))
