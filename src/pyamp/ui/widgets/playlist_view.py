"""The playlist table."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

from pyamp.ui.playlist_model import TIME_COLUMN, TITLE_COLUMN, PlaylistModel


class PlaylistView(QTableView):
    track_activated = Signal(int)
    remove_requested = Signal(list)  # list[int]

    def __init__(self, model: PlaylistModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 2)
        header = self.horizontalHeader()
        header.hide()
        header.setSectionResizeMode(TITLE_COLUMN, QHeaderView.ResizeMode.Stretch)
        # Fixed width: ResizeToContents measures every row on every change (O(n) per update).
        header.setSectionResizeMode(TIME_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(TIME_COLUMN, self.fontMetrics().horizontalAdvance(" 00:00:00 "))
        # Not using `activated`: its trigger differs per platform. Be explicit instead.
        self.doubleClicked.connect(lambda index: self.track_activated.emit(index.row()))

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.selectionModel().selectedRows()})

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if rows := self.selected_rows():
                self.remove_requested.emit(rows)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.currentIndex().isValid():
                self.track_activated.emit(self.currentIndex().row())
        else:
            super().keyPressEvent(event)
