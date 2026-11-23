"""Exposes the domain :class:`Playlist` to Qt's model/view framework (Adapter pattern)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtGui import QColor

from pyamp.core.events import EventBus, PlaylistChanged, TrackChanged
from pyamp.core.models import format_duration
from pyamp.core.playlist import Playlist
from pyamp.ui.skin import Skin

type _Index = QModelIndex | QPersistentModelIndex

TITLE_COLUMN, TIME_COLUMN = 0, 1


class PlaylistModel(QAbstractTableModel):
    def __init__(
        self, playlist: Playlist, bus: EventBus, skin: Skin, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._playlist = playlist
        self._current_color = QColor(skin.playlist_current)
        self._current_row: int | None = None
        # The row count the *views* know about. Qt requires the model's visible
        # shape to change only between begin*/end* calls, but the playlist has
        # already changed by the time we hear about it, so we keep our own snapshot.
        self._rows = len(playlist)
        bus.subscribe(PlaylistChanged, self._on_playlist_changed)
        bus.subscribe(TrackChanged, self._on_track_changed)

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else self._rows

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else 2

    def data(  # noqa: PLR0911 - one return per role is the idiomatic Qt shape
        self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if not index.isValid():
            return None
        row, column = index.row(), index.column()
        track = self._playlist[row]
        match role:
            case Qt.ItemDataRole.DisplayRole if column == TITLE_COLUMN:
                return f"{row + 1}. {track.display_title}"
            case Qt.ItemDataRole.DisplayRole if column == TIME_COLUMN:
                return format_duration(track.duration_ms) if track.duration_ms else ""
            case Qt.ItemDataRole.TextAlignmentRole if column == TIME_COLUMN:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            case Qt.ItemDataRole.ForegroundRole if row == self._playlist.current_index:
                return self._current_color
            case Qt.ItemDataRole.ToolTipRole:
                return str(track.path)
        return None

    def _on_playlist_changed(self, event: PlaylistChanged) -> None:
        if event.appended_from == self._rows and len(self._playlist) > self._rows:
            # Cheap incremental insert: views only lay out the new rows.
            self.beginInsertRows(QModelIndex(), self._rows, len(self._playlist) - 1)
            self._rows = len(self._playlist)
            self.endInsertRows()
            return
        self.beginResetModel()
        self._rows = len(self._playlist)
        self._current_row = self._playlist.current_index
        self.endResetModel()

    def _on_track_changed(self, event: TrackChanged) -> None:
        for row in {self._current_row, event.index} - {None}:
            assert row is not None
            if row < self.rowCount():
                self.dataChanged.emit(self.index(row, 0), self.index(row, 1))
        self._current_row = event.index
