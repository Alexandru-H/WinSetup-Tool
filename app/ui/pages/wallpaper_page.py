"""
wallpaper_page.py — browse + preview + apply desktop wallpapers.

Layout (CLAUDE.md §4, §8.1):

    ┌──────────────────────────────────────────────────────────────┐
    │ Wallpaper                                                    │
    │ Alege o imagine din galerie                                  │
    │                                                              │
    │  Folder:  [C:\\Users\\Alex\\Pictures]           [Browse...]  │
    │  Fit:     [Fill  ▼]                                          │
    │                                                              │
    │  Status line (green on success, red on error, idle grey)     │
    │                                                              │
    │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                         │
    │  │ IMG1 │ │ IMG2 │ │ IMG3 │ │ IMG4 │                         │
    │  │  ✓   │ │      │ │      │ │      │    scroll ↓             │
    │  └──────┘ └──────┘ └──────┘ └──────┘                         │
    │  ┌──────┐ ┌──────┐ ┌──────┐                                  │
    │  │ IMG5 │ │ IMG6 │ │ IMG7 │                                  │
    │  └──────┘ └──────┘ └──────┘                                  │
    └──────────────────────────────────────────────────────────────┘

Threading (CLAUDE.md §4.4):
    * ``ThumbnailLoaderWorker`` runs on a dedicated QThread. It turns
      disk paths into ``QImage`` thumbnails (200×120 crop-centered,
      smooth-scaled). QPixmap is GUI-thread-only; QImage is a plain
      memory buffer and survives queued connections, so the worker
      returns QImage and the main thread converts to QPixmap in the
      slot before painting.
    * ``WallpaperApplyWorker`` runs on a second, separate QThread so
      an in-flight thumbnail load never blocks an apply.
    * Both threads start on the first ``showEvent`` and shut down on
      ``QApplication.aboutToQuit``.

Cancellation model:
    * Every folder load carries an integer ``token``. The UI stores
      the latest ``_current_token``; signals arriving with a stale
      token are discarded silently. This beats trying to interrupt
      the worker mid-loop — we just race ahead and drop leftovers.
    * During an apply the UI sets ``_applying = True`` and ignores
      subsequent card clicks until the worker replies. Visual
      feedback: the active click becomes the status line's
      "Applying…" message.

Settings keys:
    * ``wallpaper_folder`` (str)   — last folder the user browsed.
      Empty string means "use default_wallpapers_folder()".
    * ``wallpaper_fit_mode`` (str) — one of ``set_wallpaper``'s valid
      modes. Unknown values are normalised to ``"fill"``.

Hot-swap:
    * ``refresh_theme`` re-styles title/subtitle/controls/status/empty
      label. Cards refresh themselves via their own theme connection.
    * ``refresh_locale`` rewrites the title, subtitle, button labels,
      fit dropdown entries, and status (if currently in an "idle"
      state — a stale "Applied" line wouldn't usefully translate
      mid-operation).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QObject,
    QThread,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.system import wallpaper as wp
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

log = get_logger(__name__)


# =========================================================================
# Constants
# =========================================================================

_THUMB_W = 200
_THUMB_H = 120
_CARD_W = 210
_CARD_H = 160          # thumb 120 + caption ~20 + vertical padding
_CARD_RADIUS = 12
_CARD_BORDER_ACTIVE = 2

_GRID_SPACING = 12
_GRID_MIN_COLS = 1
_GRID_MAX_COLS = 6

_OUTER_MARGIN = 32
_HEADER_TO_CONTROLS = 20
_CONTROLS_TO_GRID = 16
_STATUS_CLEAR_MS = 3000

# Order of entries in the Fit dropdown. Keys must exist in
# app.system.wallpaper._FIT_MODES; translation keys are
# wallpaper.fit.<key>.
_FIT_ORDER: tuple[str, ...] = (
    "fill", "fit", "stretch", "tile", "center", "span",
)


# =========================================================================
# Fonts
# =========================================================================

def _title_font() -> QFont:
    f = QFont()
    f.setPointSize(24)
    f.setWeight(QFont.Weight.DemiBold)
    return f


def _subtitle_font() -> QFont:
    f = QFont()
    f.setPointSize(13)
    return f


def _body_font() -> QFont:
    f = QFont()
    f.setPointSize(12)
    return f


def _caption_font() -> QFont:
    f = QFont()
    f.setPointSize(10)
    return f


# =========================================================================
# Thumbnail generation helper
# =========================================================================

def _make_thumbnail(path: str) -> QImage:
    """Load ``path`` into a 200×120 crop-centered, smoothly scaled QImage.

    Returns a null QImage (``img.isNull()``) on any failure — the UI
    uses that as a signal to paint a fallback placeholder.
    """
    img = QImage(path)
    if img.isNull():
        return QImage()

    scaled = img.scaled(
        _THUMB_W,
        _THUMB_H,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    if scaled.isNull():
        return QImage()

    x = max(0, (scaled.width() - _THUMB_W) // 2)
    y = max(0, (scaled.height() - _THUMB_H) // 2)
    return scaled.copy(x, y, _THUMB_W, _THUMB_H)


def _normalize_path(path: str) -> str:
    """Lower-case resolved path string — NTFS is case-insensitive, so
    comparing raw strings from ``list_images`` vs ``get_current_wallpaper``
    otherwise misses the active card on casing differences."""
    try:
        return str(Path(path).resolve(strict=False)).lower()
    except (OSError, ValueError):
        return path.lower()


# =========================================================================
# ThumbnailLoaderWorker — QImage generator
# =========================================================================

class ThumbnailLoaderWorker(QObject):
    """Batch-generate thumbnails on a background thread.

    Driven by a queued signal from the page (``loadRequested``) so the
    worker is always called from its own event loop. Emits one
    ``thumbnailReady`` per successful thumbnail, then ``loadingFinished``.

    ``token`` echoes the value the caller passed in so the UI can
    discard signals from superseded loads without tearing the thread
    down.
    """

    thumbnailReady = Signal(int, str, QImage)
    loadingFinished = Signal(int)

    @Slot(int, list)
    def load_thumbnails(self, token: int, paths: list) -> None:
        count_ok = 0
        count_fail = 0
        for raw_path in paths:
            path = str(raw_path)
            try:
                qimg = _make_thumbnail(path)
            except Exception as exc:   # noqa: BLE001
                log.warning(
                    "Thumbnail generation crashed for %s: %s", path, exc
                )
                qimg = QImage()

            if qimg.isNull():
                count_fail += 1
            else:
                count_ok += 1

            self.thumbnailReady.emit(token, path, qimg)

        log.info(
            "ThumbnailLoader: token=%d ok=%d fail=%d total=%d",
            token, count_ok, count_fail, len(paths),
        )
        self.loadingFinished.emit(token)


# =========================================================================
# WallpaperApplyWorker — one-shot set_wallpaper call
# =========================================================================

class WallpaperApplyWorker(QObject):
    """Runs ``wallpaper.set_wallpaper`` off the GUI thread.

    The SPI call is fast (< 100 ms) most of the time but Windows is
    allowed to take its time on a cold shell; keeping it off the GUI
    thread is cheap insurance against a click-to-repaint hitch.

    Emits ``wallpaperApplied(success, path)``. On failure, ``path``
    is still echoed back so the UI knows which card to reset.
    """

    wallpaperApplied = Signal(bool, str)

    @Slot(str, str)
    def apply(self, path: str, fit_mode: str) -> None:
        try:
            ok = wp.set_wallpaper(path, fit_mode)
        except Exception as exc:   # noqa: BLE001
            log.exception("set_wallpaper crashed for %s (%s)", path, fit_mode)
            ok = False
            _ = exc
        self.wallpaperApplied.emit(bool(ok), path)


# =========================================================================
# WallpaperCard — one thumbnail + filename + active-state border
# =========================================================================

class WallpaperCard(QFrame, ThemedWidget):
    """A single wallpaper tile: rounded card, thumbnail, elided caption.

    Paint pipeline (no QSS for the background — avoids the QFrame /
    paintEvent rendering order gotcha):
        1. Rounded ``card`` or ``card_hover`` fill in paintEvent.
        2. 2 px accent rounded border when ``_active`` is True.
    Caption colour is applied via a scoped stylesheet on the inner
    QLabel so theme swaps repaint the text with the new palette.
    """

    clicked = Signal(str)

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(_CARD_W, _CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._path = path
        self._active = False
        self._hovering = False
        self._theme: dict[str, str] = {}
        self._has_thumbnail = False

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(4)

        self._thumb = QLabel(self)
        self._thumb.setFixedSize(_THUMB_W, _THUMB_H)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root.addWidget(self._thumb, 0, Qt.AlignmentFlag.AlignHCenter)

        self._caption = QLabel(Path(path).stem, self)
        self._caption.setFont(_caption_font())
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._caption.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        # Elide via setText-on-resize would be nicer, but "Picture (42).jpg"
        # rarely overflows 200 px so plain ellipsis via QSS is enough.
        self._caption.setStyleSheet("background: transparent;")
        root.addWidget(self._caption)

        self.connect_theme(theme_mgr)

    # ---- Public API -----------------------------------------------------

    def path(self) -> str:
        return self._path

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            self._has_thumbnail = False
            self._thumb.clear()
        else:
            self._has_thumbnail = True
            self._thumb.setPixmap(pixmap)

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self.update()

    # ---- Mixin hook -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        caption_color = theme["text_pri"] if self._active else theme["text_sec"]
        self._caption.setStyleSheet(
            f"color: {caption_color}; background: transparent;"
        )
        # Thumbnail placeholder text colour while the worker hasn't
        # delivered a pixmap yet.
        if not self._has_thumbnail:
            self._thumb.setStyleSheet(
                f"color: {theme['text_sec']}; background: transparent;"
            )
        self.update()

    # ---- Paint ----------------------------------------------------------

    def paintEvent(self, event) -> None:   # noqa: ARG002
        if not self._theme:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = self._theme["card_hover"] if self._hovering else self._theme["card"]
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(self.rect(), _CARD_RADIUS, _CARD_RADIUS)

        if self._active:
            pen = QPen(QColor(self._theme["accent"]))
            pen.setWidth(_CARD_BORDER_ACTIVE)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Inset by half the stroke so the border sits INSIDE the card,
            # not straddling the edge (which clips against siblings).
            inset = _CARD_BORDER_ACTIVE / 2
            rect = self.rect().adjusted(
                int(inset), int(inset), -int(inset), -int(inset)
            )
            p.drawRoundedRect(
                rect, _CARD_RADIUS - 1, _CARD_RADIUS - 1
            )
        p.end()

    # ---- Mouse / hover --------------------------------------------------

    def enterEvent(self, event) -> None:   # noqa: ARG002, N802
        self._hovering = True
        self.update()

    def leaveEvent(self, event) -> None:   # noqa: ARG002, N802
        self._hovering = False
        self.update()

    def mousePressEvent(self, event) -> None:   # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._path)
        super().mousePressEvent(event)


# =========================================================================
# WallpaperPage
# =========================================================================

class WallpaperPage(QWidget, ThemedWidget, LocalizedWidget):
    """Folder picker + fit selector + scrollable thumbnail grid."""

    # Signals driving the two workers. Declared at the class level so
    # PySide6 registers them during metaclass setup.
    _loadRequested = Signal(int, list)   # token, [path, ...]
    _applyRequested = Signal(str, str)   # path, fit_mode

    def __init__(
        self,
        settings: "Settings",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme_mgr = theme_mgr
        self._locale_mgr = locale_mgr

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # ---- Load-tracking state ----------------------------------------
        self._current_token: int = 0
        self._current_folder: Path = self._initial_folder()
        self._fit_mode: str = self._initial_fit_mode()

        self._cards: list[WallpaperCard] = []
        self._card_by_norm_path: dict[str, WallpaperCard] = {}
        self._active_card: WallpaperCard | None = None

        self._grid_cols: int = 0
        self._applying: bool = False

        # ---- Status-line state ------------------------------------------
        self._status_state: str = "idle"       # idle | applying | success | error
        self._status_text: str = ""
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._reset_status_to_idle)

        # ---- UI build ---------------------------------------------------
        self._build_ui()

        # ---- Workers ----------------------------------------------------
        self._setup_workers()

        # ---- Lifecycle flags --------------------------------------------
        self._started = False

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_workers)

        # ---- Mixin wiring (initial refresh_* runs once) -----------------
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    # ---- Initial-state helpers ------------------------------------------

    def _initial_folder(self) -> Path:
        stored = str(self._settings.get("wallpaper_folder", "") or "").strip()
        if stored:
            candidate = Path(stored)
            if candidate.is_dir():
                return candidate
            log.info(
                "Stored wallpaper_folder %r no longer exists — using default.",
                stored,
            )
        return wp.default_wallpapers_folder()

    def _initial_fit_mode(self) -> str:
        stored = str(self._settings.get("wallpaper_fit_mode", "fill") or "fill")
        stored = stored.strip().lower()
        if stored not in _FIT_ORDER:
            log.warning(
                "Invalid stored fit mode %r — resetting to 'fill'.", stored
            )
            stored = "fill"
            self._settings.set("wallpaper_fit_mode", stored)
            self._settings.save()
        return stored

    # ---- UI build --------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            _OUTER_MARGIN, _OUTER_MARGIN,
            _OUTER_MARGIN, _OUTER_MARGIN,
        )
        root.setSpacing(6)

        # --- Title -------------------------------------------------------
        self._title = QLabel(self)
        self._title.setFont(_title_font())
        root.addWidget(self._title)

        self._subtitle = QLabel(self)
        self._subtitle.setFont(_subtitle_font())
        root.addWidget(self._subtitle)

        root.addSpacing(_HEADER_TO_CONTROLS)

        # --- Controls row 1: Folder picker -------------------------------
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(10)

        self._folder_label = QLabel(self)
        self._folder_label.setFont(_body_font())
        self._folder_label.setFixedWidth(70)

        self._folder_edit = QLineEdit(self)
        self._folder_edit.setFont(_body_font())
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setText(str(self._current_folder))
        self._folder_edit.setMinimumHeight(32)

        self._browse_btn = QPushButton(self)
        self._browse_btn.setFont(_body_font())
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.setMinimumHeight(32)
        self._browse_btn.clicked.connect(self._on_browse_clicked)

        folder_row.addWidget(self._folder_label, 0)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(self._browse_btn, 0)
        root.addLayout(folder_row)

        # --- Controls row 2: Fit mode ------------------------------------
        fit_row = QHBoxLayout()
        fit_row.setContentsMargins(0, 0, 0, 0)
        fit_row.setSpacing(10)

        self._fit_label = QLabel(self)
        self._fit_label.setFont(_body_font())
        self._fit_label.setFixedWidth(70)

        self._fit_combo = QComboBox(self)
        self._fit_combo.setFont(_body_font())
        self._fit_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fit_combo.setMinimumHeight(32)
        self._fit_combo.setMinimumWidth(160)
        for key in _FIT_ORDER:
            # Display text is filled in by refresh_locale; store the
            # mode key as userData so we're immune to label changes.
            self._fit_combo.addItem(key, userData=key)
        # Pre-select the stored mode.
        idx = _FIT_ORDER.index(self._fit_mode)
        self._fit_combo.setCurrentIndex(idx)
        self._fit_combo.currentIndexChanged.connect(self._on_fit_changed)

        fit_row.addWidget(self._fit_label, 0)
        fit_row.addWidget(self._fit_combo, 0)
        fit_row.addStretch(1)
        root.addLayout(fit_row)

        root.addSpacing(10)

        # --- Status line -------------------------------------------------
        self._status = QLabel(self)
        self._status.setFont(_body_font())
        self._status.setMinimumHeight(20)
        root.addWidget(self._status)

        root.addSpacing(_CONTROLS_TO_GRID)

        # --- Scroll area with grid + empty-state label --------------------
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._scroll_body = QWidget()
        self._scroll_body.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        body_layout = QVBoxLayout(self._scroll_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # Grid container
        self._grid_widget = QWidget(self._scroll_body)
        self._grid_widget.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_GRID_SPACING)
        self._grid.setVerticalSpacing(_GRID_SPACING)
        self._grid.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        body_layout.addWidget(self._grid_widget)

        # Empty-state label (shown when no images in the folder)
        self._empty_label = QLabel(self._scroll_body)
        self._empty_label.setFont(_body_font())
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setVisible(False)
        body_layout.addWidget(self._empty_label, 0, Qt.AlignmentFlag.AlignHCenter)

        body_layout.addStretch(1)
        self._scroll.setWidget(self._scroll_body)
        root.addWidget(self._scroll, 1)

    # ---- Worker setup ----------------------------------------------------

    def _setup_workers(self) -> None:
        # Loader
        self._loader_thread = QThread(self)
        self._loader = ThumbnailLoaderWorker()
        self._loader.moveToThread(self._loader_thread)
        self._loadRequested.connect(self._loader.load_thumbnails)
        self._loader.thumbnailReady.connect(self._on_thumbnail_ready)
        self._loader.loadingFinished.connect(self._on_loading_finished)

        # Applier
        self._apply_thread = QThread(self)
        self._applier = WallpaperApplyWorker()
        self._applier.moveToThread(self._apply_thread)
        self._applyRequested.connect(self._applier.apply)
        self._applier.wallpaperApplied.connect(self._on_wallpaper_applied)

    # ---- Mixin hooks -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        # Title / subtitle
        self._title.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        self._subtitle.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

        # Row labels
        for lbl in (self._folder_label, self._fit_label):
            lbl.setStyleSheet(
                f"color: {theme['text_pri']}; background: transparent;"
            )

        # Folder line edit: card-ish input, not a hard outline
        self._folder_edit.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {theme['card']};
                color: {theme['text_pri']};
                border: 1px solid {theme['border']};
                border-radius: 6px;
                padding: 4px 10px;
                selection-background-color: {theme['accent']};
            }}
            QLineEdit:read-only {{
                color: {theme['text_sec']};
            }}
            """
        )

        # Browse button — accent on hover
        self._browse_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {theme['card']};
                color: {theme['text_pri']};
                border: 1px solid {theme['border']};
                border-radius: 6px;
                padding: 4px 16px;
            }}
            QPushButton:hover {{
                background-color: {theme['card_hover']};
                border-color: {theme['accent']};
            }}
            QPushButton:pressed {{
                background-color: {theme['accent']};
                color: {theme['text_pri']};
                border-color: {theme['accent']};
            }}
            """
        )

        # Fit combo
        self._fit_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: {theme['card']};
                color: {theme['text_pri']};
                border: 1px solid {theme['border']};
                border-radius: 6px;
                padding: 4px 10px;
            }}
            QComboBox:hover {{
                border-color: {theme['accent']};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 22px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {theme['card']};
                color: {theme['text_pri']};
                border: 1px solid {theme['border']};
                selection-background-color: {theme['accent']};
                selection-color: {theme['text_pri']};
                outline: none;
            }}
            """
        )

        # Empty label
        self._empty_label.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

        # Status line — re-apply with current state so colour tracks theme
        self._apply_status_style()

    def refresh_locale(self) -> None:
        tr = self._locale_mgr.tr
        self._title.setText(tr("wallpaper.title"))
        self._subtitle.setText(tr("wallpaper.subtitle"))
        self._folder_label.setText(tr("wallpaper.folder_label"))
        self._browse_btn.setText(tr("wallpaper.browse_button"))
        self._fit_label.setText(tr("wallpaper.fit_label"))
        self._empty_label.setText(tr("wallpaper.no_images"))

        # Fit combo — rewrite item text without changing selection.
        # Disconnect briefly so setItemText doesn't fire currentIndexChanged
        # (setItemText doesn't, but setCurrentIndex does; staying defensive).
        for i, key in enumerate(_FIT_ORDER):
            self._fit_combo.setItemText(i, tr(f"wallpaper.fit.{key}"))

        # Status line only gets re-translated if it's currently idle —
        # a translated "Applied" on an unrelated flip would be confusing.
        if self._status_state == "idle":
            self._status_text = tr("wallpaper.status.idle")
            self._apply_status_style()
        elif self._status_state == "applying":
            self._status_text = tr("wallpaper.status.applying")
            self._apply_status_style()

    # ---- Status line helpers --------------------------------------------

    def _set_status(self, state: str, text: str) -> None:
        self._status_state = state
        self._status_text = text
        self._apply_status_style()

    def _apply_status_style(self) -> None:
        theme = self._theme_mgr.current()
        if self._status_state == "success":
            color = theme["ok"]
        elif self._status_state == "error":
            color = theme["err"]
        elif self._status_state == "applying":
            color = theme["accent"]
        else:
            color = theme["text_sec"]
        self._status.setStyleSheet(
            f"color: {color}; background: transparent;"
        )
        self._status.setText(self._status_text)

    def _reset_status_to_idle(self) -> None:
        self._set_status("idle", self._locale_mgr.tr("wallpaper.status.idle"))

    # ---- Folder loading --------------------------------------------------

    def _load_folder(self, folder: Path) -> None:
        """Enumerate the folder, rebuild the grid with placeholder cards,
        and dispatch the thumbnail batch to the loader thread."""
        self._current_folder = folder
        self._folder_edit.setText(str(folder))

        self._current_token += 1
        token = self._current_token

        self._clear_grid()

        images = wp.list_images(folder)
        if not images:
            self._empty_label.setVisible(True)
            self._grid_widget.setVisible(False)
            log.info("No wallpapers found in %s", folder)
            return

        self._empty_label.setVisible(False)
        self._grid_widget.setVisible(True)

        # Build cards first (fast — purely Python + Qt construction),
        # then ask the worker to generate thumbnails.
        for img in images:
            card = WallpaperCard(self._theme_mgr, str(img), self._grid_widget)
            card.clicked.connect(self._on_card_clicked)
            self._cards.append(card)
            self._card_by_norm_path[_normalize_path(str(img))] = card

        self._reflow_grid()
        self._sync_active_from_registry()

        paths = [str(p) for p in images]
        log.info(
            "WallpaperPage: loading %d thumbnails from %s (token=%d).",
            len(paths), folder, token,
        )
        self._loadRequested.emit(token, paths)

    def _clear_grid(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        self._card_by_norm_path.clear()
        self._active_card = None
        self._grid_cols = 0

    def _reflow_grid(self) -> None:
        """Place cards in the grid at the column count that fits the
        current viewport width. Called on (re)load and on resize."""
        viewport_w = self._scroll.viewport().width()
        usable = max(0, viewport_w)
        # (N cards) × (card_w + spacing) − spacing ≤ usable
        card_stride = _CARD_W + _GRID_SPACING
        cols = max(
            _GRID_MIN_COLS,
            min(_GRID_MAX_COLS, (usable + _GRID_SPACING) // card_stride),
        )
        cols = int(cols)

        if cols == self._grid_cols and self._cards:
            return
        self._grid_cols = cols

        # Remove every widget from the grid without destroying them,
        # then re-add at the new positions.
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                break

        for i, card in enumerate(self._cards):
            row, col = divmod(i, cols)
            self._grid.addWidget(card, row, col)

    def _sync_active_from_registry(self) -> None:
        """Mark whichever card matches the OS-current wallpaper."""
        current = wp.get_current_wallpaper()
        if not current:
            return
        key = _normalize_path(current)
        card = self._card_by_norm_path.get(key)
        if card is not None:
            self._mark_active(card)

    def _mark_active(self, new_card: WallpaperCard | None) -> None:
        if self._active_card is new_card:
            # Still force a refresh_theme so caption colour re-picks.
            return
        if self._active_card is not None:
            self._active_card.set_active(False)
            # Caption colour is bound to active state — re-run its
            # themed refresh so text_pri drops back to text_sec.
            self._active_card.refresh_theme(self._theme_mgr.current())
        self._active_card = new_card
        if new_card is not None:
            new_card.set_active(True)
            new_card.refresh_theme(self._theme_mgr.current())

    # ---- Control handlers ------------------------------------------------

    def _on_browse_clicked(self) -> None:
        start_dir = str(self._current_folder)
        title = self._locale_mgr.tr("wallpaper.pick_folder")
        chosen = QFileDialog.getExistingDirectory(
            self, title, start_dir
        )
        if not chosen:
            return
        folder = Path(chosen)
        if not folder.is_dir():
            log.warning("Picked folder is not a directory: %s", folder)
            return
        self._settings.set("wallpaper_folder", str(folder))
        self._settings.save()
        self._load_folder(folder)

    def _on_fit_changed(self, index: int) -> None:
        if index < 0 or index >= len(_FIT_ORDER):
            return
        key = str(self._fit_combo.itemData(index) or _FIT_ORDER[index])
        if key not in _FIT_ORDER:
            return
        if key == self._fit_mode:
            return
        self._fit_mode = key
        self._settings.set("wallpaper_fit_mode", key)
        self._settings.save()
        log.info("Wallpaper fit mode → %s (not re-applied).", key)

    def _on_card_clicked(self, path: str) -> None:
        if self._applying:
            return
        self._applying = True
        self._status_timer.stop()
        self._set_status(
            "applying", self._locale_mgr.tr("wallpaper.status.applying")
        )
        self._applyRequested.emit(path, self._fit_mode)

    # ---- Worker slots ----------------------------------------------------

    @Slot(int, str, QImage)
    def _on_thumbnail_ready(
        self, token: int, path: str, qimg: QImage
    ) -> None:
        if token != self._current_token:
            # Stale signal from a superseded folder load; drop silently.
            return
        card = self._card_by_norm_path.get(_normalize_path(path))
        if card is None:
            return
        if qimg.isNull():
            # Leave the thumbnail blank; caption still shows the filename.
            return
        card.set_thumbnail(QPixmap.fromImage(qimg))

    @Slot(int)
    def _on_loading_finished(self, token: int) -> None:
        if token != self._current_token:
            return
        log.info(
            "WallpaperPage: thumbnails ready for token=%d (%d cards).",
            token, len(self._cards),
        )

    @Slot(bool, str)
    def _on_wallpaper_applied(self, ok: bool, path: str) -> None:
        self._applying = False
        if ok:
            self._set_status(
                "success",
                self._locale_mgr.tr("wallpaper.status.success"),
            )
            card = self._card_by_norm_path.get(_normalize_path(path))
            if card is not None:
                self._mark_active(card)
        else:
            self._set_status(
                "error",
                self._locale_mgr.tr(
                    "wallpaper.status.error",
                    msg=Path(path).name or path,
                ),
            )
        self._status_timer.start(_STATUS_CLEAR_MS)

    # ---- Lifecycle -------------------------------------------------------

    def showEvent(self, event) -> None:   # noqa: N802
        super().showEvent(event)
        if self._started:
            return
        self._started = True

        log.info("WallpaperPage: starting loader + apply threads.")
        self._loader_thread.start()
        self._apply_thread.start()

        # Initial status text + initial folder load.
        self._set_status(
            "idle", self._locale_mgr.tr("wallpaper.status.idle")
        )
        self._load_folder(self._current_folder)

    def resizeEvent(self, event) -> None:   # noqa: N802
        super().resizeEvent(event)
        self._reflow_grid()

    def _shutdown_workers(self) -> None:
        if not self._started:
            return
        log.info("WallpaperPage: shutting down loader + apply threads.")
        for thread in (self._loader_thread, self._apply_thread):
            thread.quit()
            if not thread.wait(3000):
                log.warning(
                    "WallpaperPage: worker thread did not exit within 3 s — "
                    "forcing terminate."
                )
                thread.terminate()
                thread.wait(1000)
