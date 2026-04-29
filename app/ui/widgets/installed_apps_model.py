"""Table model for the installed apps list.

Columns:
    0: checkbox (Qt.CheckStateRole) — selection toggle
    1: Name (DisplayRole)
    2: Publisher
    3: Installed (ISO date or —)
    4: Size (formatted as "12.3 MB" or "—")
    5: Version

Sorting works on any column except 0. Sort state does not affect the
checked set — checked state is keyed by registry_key (stable across
sorts and filtered views), not by row index.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from app.system.uninstaller.scanner import InstalledApp

# Column index constants — used by UninstallPage to configure column widths.
COL_CHECK     = 0
COL_NAME      = 1
COL_PUBLISHER = 2
COL_INSTALLED = 3
COL_SIZE      = 4
COL_VERSION   = 5
COL_COUNT     = 6


def _format_size(kb: int) -> str:
    if kb <= 0:
        return "—"
    if kb < 1024:
        return f"{kb} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.2f} GB"


class InstalledAppsModel(QAbstractTableModel):
    """Table model for installed apps. Checked set keyed by registry_key."""

    HEADERS = ("", "Name", "Publisher", "Installed", "Size", "Version")

    selection_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._apps: list[InstalledApp] = []
        self._checked_keys: set[str] = set()

    # ---- Data management ------------------------------------------------

    def set_apps(self, apps: Iterable[InstalledApp]) -> None:
        self.beginResetModel()
        self._apps = list(apps)
        valid = {a.registry_key for a in self._apps}
        self._checked_keys &= valid
        self.endResetModel()
        self.selection_changed.emit()

    def apps(self) -> list[InstalledApp]:
        return list(self._apps)

    def checked_apps(self) -> list[InstalledApp]:
        return [a for a in self._apps if a.registry_key in self._checked_keys]

    def checked_count(self) -> int:
        return sum(1 for a in self._apps if a.registry_key in self._checked_keys)

    def clear_checks(self) -> None:
        if not self._checked_keys:
            return
        self._checked_keys.clear()
        if self._apps:
            top = self.index(0, COL_CHECK)
            bot = self.index(len(self._apps) - 1, COL_CHECK)
            self.dataChanged.emit(top, bot, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()

    # ---- Qt model interface ---------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._apps)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else COL_COUNT

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole and section < COL_COUNT:
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COL_CHECK:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._apps):
            return None
        app = self._apps[row]

        if col == COL_CHECK:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if app.registry_key in self._checked_keys
                    else Qt.CheckState.Unchecked
                )
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            match col:
                case c if c == COL_NAME:      return app.display_name
                case c if c == COL_PUBLISHER: return app.publisher or "—"
                case c if c == COL_INSTALLED: return app.install_date or "—"
                case c if c == COL_SIZE:      return _format_size(app.estimated_size_kb)
                case c if c == COL_VERSION:   return app.version or "—"

        if role == Qt.ItemDataRole.ToolTipRole and col == COL_NAME:
            tip = app.display_name
            if app.install_location:
                tip += f"\nLocation: {app.install_location}"
            if app.winget_id and not app.winget_id.startswith("ARP"):
                tip += f"\nWinget ID: {app.winget_id}"
            return tip

        # UserRole carries raw values for sort proxy comparisons.
        if role == Qt.ItemDataRole.UserRole:
            match col:
                case c if c == COL_NAME:      return app.display_name.lower()
                case c if c == COL_PUBLISHER: return app.publisher.lower()
                case c if c == COL_INSTALLED: return app.install_date or ""
                case c if c == COL_SIZE:      return app.estimated_size_kb
                case c if c == COL_VERSION:   return app.version or ""
        return None

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or index.column() != COL_CHECK:
            return False
        if role != Qt.ItemDataRole.CheckStateRole:
            return False
        row = index.row()
        if row >= len(self._apps):
            return False
        app = self._apps[row]

        # Qt passes int (2) or Qt.CheckState.Checked depending on platform.
        checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)

        if checked:
            self._checked_keys.add(app.registry_key)
        else:
            self._checked_keys.discard(app.registry_key)

        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()
        return True
