"""Custom item delegate that paints CheckStateRole cells with theme colors.

Without this delegate QTableView uses the OS native checkbox style —
on Windows that renders orange/system-blue inconsistent with the app theme.

Usage:
    delegate = ThemedCheckboxDelegate(theme_mgr, parent=table)
    table.setItemDelegateForColumn(col_index, delegate)

Visual spec:
    Unchecked: 18×18 rounded square, 1.5 px border in text_sec, transparent fill.
    Checked:   18×18 rounded square, filled with accent, white checkmark inside.

The delegate registers on themeChanged and triggers a viewport repaint so
checked state reflects the new palette without a scan refresh.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
)

from app.core.logging_setup import get_logger

log = get_logger(__name__)

_BOX  = 18   # checkbox square side length in px
_BRAD = 4    # border-radius for the rounded square


class ThemedCheckboxDelegate(QStyledItemDelegate):
    """Paints checkbox cells using theme accent / text_sec colors."""

    def __init__(self, theme_mgr, parent=None) -> None:
        super().__init__(parent)
        self._palette: dict = theme_mgr.current()
        theme_mgr.themeChanged.connect(self._on_theme_changed)

    def _on_theme_changed(self, palette: dict) -> None:
        self._palette = palette
        view = self.parent()
        if isinstance(view, QTableView):
            view.viewport().update()

    # ---- Paint ----------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        painter.save()

        # Background: match the row's selection state to keep column 0
        # visually consistent with columns 1-5.
        if option.state & option.state.State_Selected:
            painter.fillRect(option.rect, QColor(self._palette["card_hover"]))
        # Unselected cells are transparent — the table's base background shows.

        # Checkbox geometry: centered in the cell.
        cx = option.rect.x() + (option.rect.width()  - _BOX) // 2
        cy = option.rect.y() + (option.rect.height() - _BOX) // 2
        box = QRect(cx, cy, _BOX, _BOX)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        is_checked = (
            index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        )

        path = QPainterPath()
        path.addRoundedRect(box, _BRAD, _BRAD)

        if is_checked:
            painter.fillPath(path, QColor(self._palette["accent"]))
            # White checkmark — three control points relative to box origin.
            painter.setPen(
                QPen(
                    QColor("#ffffff"), 2.0,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            check = QPainterPath()
            check.moveTo(cx + 4,  cy + 9)
            check.lineTo(cx + 8,  cy + 13)
            check.lineTo(cx + 14, cy + 6)
            painter.drawPath(check)
        else:
            painter.setPen(QPen(QColor(self._palette["text_sec"]), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.restore()

    # ---- Size hint ------------------------------------------------------

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        return QSize(_BOX + 12, _BOX + 8)

    # ---- Click handling -------------------------------------------------

    def editorEvent(
        self,
        event: QEvent,
        model,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> bool:
        """Toggle check state on any left-click in the cell.

        The default QStyledItemDelegate only toggles when the click lands
        inside the indicator rect. Expanding to the full cell improves UX —
        users don't need to aim precisely at the 18 px box.
        """
        if event.type() not in (
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            return False

        if not (index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False

        if isinstance(event, QMouseEvent):
            if event.button() != Qt.MouseButton.LeftButton:
                return False

        current = index.data(Qt.ItemDataRole.CheckStateRole)
        new_state = (
            Qt.CheckState.Unchecked
            if current == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        return model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
