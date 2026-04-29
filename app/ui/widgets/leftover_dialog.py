"""Modal dialog for reviewing and confirming leftover deletion.

Shown by UninstallPage after each successful uninstall when leftovers
are found AND the "Clean leftovers" checkbox is ON.

Layout:
    Header: "Leftovers found for <App Name>"
    Subtitle: "N items detected. Review and select what to remove."
    Scrollable list of LeftoverRow widgets (checkbox + path + size)
    Footer: [Skip All]  [Delete Selected (N)]

Behavior:
    All items checked by default — user unchecks to keep.
    "Delete Selected" accepts the dialog; page handles actual removal.
    "Skip All" and the window-close (X) button both accept without deletion.
    Dialog is modal — blocks UninstallPage UI until closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from app.core.logging_setup import get_logger
from app.system.uninstaller.leftover_scanner import LeftoverItem
from app.system.uninstaller.scanner import InstalledApp
from app.ui.styles.scrollbar_style import build_scrollbar_qss

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    if n <= 0:
        return "—"
    if n < 1024:
        return f"{n} B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.2f} GB"


_KIND_LABEL = {
    "folder": "Folder",
    "file":   "File",
    "regkey": "Registry key",
}


def _truncate(path: str, limit: int = 72) -> str:
    if len(path) <= limit:
        return path
    parts = path.replace("/", "\\").split("\\")
    if len(parts) <= 3:
        return path[: limit - 3] + "..."
    return f"{parts[0]}\\...\\{'\\'.join(parts[-2:])}"


# ---------------------------------------------------------------------------
# Row widget
# ---------------------------------------------------------------------------

class _LeftoverRow(QFrame):
    """Single selectable row: [☑] path / description  size [⚠]"""

    toggled = Signal()

    def __init__(self, item: LeftoverItem, parent=None) -> None:
        super().__init__(parent)
        self._item = item
        self.setObjectName("leftoverRow")
        self.setFixedHeight(50)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)

        self._cb = QCheckBox()
        self._cb.setChecked(True)
        self._cb.toggled.connect(lambda _: self.toggled.emit())
        layout.addWidget(self._cb)

        # Text column
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        self._path_lbl = QLabel(_truncate(item.path))
        self._path_lbl.setObjectName("leftoverPath")
        self._path_lbl.setToolTip(item.path)
        col.addWidget(self._path_lbl)

        kind = _KIND_LABEL.get(item.kind, item.kind)
        self._sub_lbl = QLabel(f"{kind} · {item.description}")
        self._sub_lbl.setObjectName("leftoverSub")
        col.addWidget(self._sub_lbl)

        layout.addLayout(col, stretch=1)

        self._size_lbl = QLabel(_fmt_size(item.size_bytes))
        self._size_lbl.setObjectName("leftoverSize")
        self._size_lbl.setMinimumWidth(72)
        self._size_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._size_lbl)

        if item.risky:
            warn = QLabel("⚠")
            warn.setObjectName("leftoverRisky")
            warn.setFixedWidth(20)
            warn.setToolTip(
                "This key has multiple sub-keys — review before deleting."
            )
            layout.addWidget(warn)

    def is_checked(self) -> bool:
        return self._cb.isChecked()

    def item(self) -> LeftoverItem:
        return self._item


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class LeftoverConfirmDialog(QDialog):
    """Shows leftovers after a successful uninstall; returns user's choices."""

    def __init__(
        self,
        app: InstalledApp,
        items: list[LeftoverItem],
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._app        = app
        self._items      = list(items)
        self._theme_mgr  = theme_mgr
        self._locale_mgr = locale_mgr
        self._rows: list[_LeftoverRow] = []
        self._chosen: list[LeftoverItem] = []   # filled on accept via delete

        self.setWindowTitle(locale_mgr.tr("uninstall.leftover_dialog.title"))
        self.setModal(True)
        self.setMinimumSize(660, 440)

        self._build_ui()
        self._apply_theme(theme_mgr.current())
        theme_mgr.themeChanged.connect(self._apply_theme)

    # ---- Build ----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        lm = self._locale_mgr

        # Header
        title = QLabel(
            lm.tr("uninstall.leftover_dialog.header", app=self._app.display_name)
        )
        title.setObjectName("dlgTitle")
        root.addWidget(title)

        subtitle = QLabel(
            lm.tr("uninstall.leftover_dialog.subtitle", n=len(self._items))
        )
        subtitle.setObjectName("dlgSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Scrollable item list
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(4, 4, 4, 4)
        list_layout.setSpacing(4)

        for item in self._items:
            row = _LeftoverRow(item)
            row.toggled.connect(self._refresh_delete_btn)
            self._rows.append(row)
            list_layout.addWidget(row)

        list_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setWidget(list_widget)
        root.addWidget(self._scroll, stretch=1)

        # Footer buttons
        foot = QHBoxLayout()
        foot.setSpacing(10)
        foot.addStretch(1)

        self._skip_btn = QPushButton(
            lm.tr("uninstall.leftover_dialog.skip")
        )
        self._skip_btn.setMinimumWidth(110)
        self._skip_btn.clicked.connect(self._on_skip)
        foot.addWidget(self._skip_btn)

        self._del_btn = QPushButton()
        self._del_btn.setObjectName("primaryBtn")
        self._del_btn.setMinimumWidth(170)
        self._del_btn.clicked.connect(self._on_delete)
        foot.addWidget(self._del_btn)

        root.addLayout(foot)
        self._refresh_delete_btn()

    # ---- Theme ----------------------------------------------------------

    def _apply_theme(self, p: dict) -> None:
        qss = f"""
        QDialog {{
            background: {p["panel_bg"]};
        }}
        QLabel#dlgTitle {{
            color: {p["text_pri"]};
            font-size: 14pt;
            font-weight: 600;
        }}
        QLabel#dlgSubtitle {{
            color: {p["text_sec"]};
            font-size: 10pt;
        }}
        QScrollArea {{
            background: transparent;
            border: 1px solid {p["border"]};
            border-radius: 8px;
        }}
        QFrame#leftoverRow {{
            background: {p["card"]};
            border-radius: 6px;
        }}
        QFrame#leftoverRow:hover {{
            background: {p["card_hover"]};
        }}
        QLabel#leftoverPath {{
            color: {p["text_pri"]};
            font-size: 10pt;
        }}
        QLabel#leftoverSub {{
            color: {p["text_sec"]};
            font-size: 9pt;
        }}
        QLabel#leftoverSize {{
            color: {p["text_pri"]};
            font-size: 10pt;
            font-weight: 600;
        }}
        QLabel#leftoverRisky {{
            color: {p["warn"]};
            font-size: 11pt;
        }}
        QCheckBox {{
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1.5px solid {p["text_sec"]};
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background: {p["accent"]};
            border: 1.5px solid {p["accent"]};
        }}
        QPushButton {{
            background: transparent;
            color: {p["text_pri"]};
            border: 1px solid {p["border"]};
            border-radius: 16px;
            padding: 6px 18px;
            font-size: 10pt;
        }}
        QPushButton:hover {{
            background: {p["card_hover"]};
        }}
        QPushButton#primaryBtn {{
            background: {p["accent"]};
            color: {p["text_pri"]};
            border: 1px solid {p["accent"]};
            font-weight: 600;
        }}
        QPushButton#primaryBtn:hover {{
            background: {p["accent_h"]};
            border-color: {p["accent_h"]};
        }}
        QPushButton#primaryBtn:disabled {{
            background: {p["border"]};
            border-color: {p["border"]};
            color: {p["text_sec"]};
        }}
        """
        qss += build_scrollbar_qss(p)
        self.setStyleSheet(qss)

    # ---- State ----------------------------------------------------------

    def _refresh_delete_btn(self) -> None:
        n = sum(1 for r in self._rows if r.is_checked())
        lm = self._locale_mgr
        if n > 0:
            self._del_btn.setText(
                lm.tr("uninstall.leftover_dialog.delete_n", n=n)
            )
            self._del_btn.setEnabled(True)
        else:
            self._del_btn.setText(lm.tr("uninstall.leftover_dialog.delete"))
            self._del_btn.setEnabled(False)

    # ---- Actions --------------------------------------------------------

    def _on_skip(self) -> None:
        self._chosen = []
        log.info("User skipped leftover cleanup for %s.", self._app.display_name)
        self.accept()

    def _on_delete(self) -> None:
        self._chosen = [r.item() for r in self._rows if r.is_checked()]
        log.info(
            "User chose to delete %d leftover items for %s.",
            len(self._chosen), self._app.display_name,
        )
        self.accept()

    def closeEvent(self, event) -> None:
        # X button = skip
        self._chosen = []
        super().closeEvent(event)

    # ---- Public API -----------------------------------------------------

    def chosen_items(self) -> list[LeftoverItem]:
        """Items the user chose to delete. Empty if Skip All / X / no selection."""
        return list(self._chosen)
