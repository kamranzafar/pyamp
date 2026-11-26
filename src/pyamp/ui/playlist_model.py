# SPDX-License-Identifier: GPL-3.0-or-later
#
# PyAmp - a lightweight, Winamp-inspired music player
# Copyright (C) 2026 Kamran Zafar
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Additional terms apply under section 7 of the GNU GPL; see ADDITIONAL-TERMS.md.

"""Exposes the domain :class:`Playlist` to Qt's model/view framework (Adapter pattern)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor

from pyamp.core.events import EventBus, PlaylistChanged, TrackChanged
from pyamp.core.models import Location, StreamUrl, format_duration, parse_location
from pyamp.core.playlist import Playlist
from pyamp.ui.skin import Skin
from pyamp.ui.track_drag import TRACKS_MIME, dragged_tracks

type _Index = QModelIndex | QPersistentModelIndex

TITLE_COLUMN, TIME_COLUMN = 0, 1
#: Rows being dragged within the playlist (private to PyAmp).
ROWS_MIME = "application/x-pyamp-rows"


def dropped_locations(urls: Iterable[QUrl]) -> list[Location]:
    """Files and http(s) streams from a drop (e.g. from Finder, or a link from a browser)."""
    locations: list[Location] = []
    for url in urls:
        if url.isLocalFile():
            locations.append(Path(url.toLocalFile()))
        elif isinstance(location := parse_location(url.toString()), StreamUrl):
            locations.append(location)
    return locations


class PlaylistModel(QAbstractTableModel):
    #: Rows the dragged tracks ended up at after a move (to keep them selected).
    rows_moved = Signal(list)
    #: Files dropped onto the list from outside (e.g. Finder).
    files_dropped = Signal(list)
    #: Tracks dropped from another PyAmp window (e.g. the library's), tags and all.
    tracks_dropped = Signal(list)

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
                if track.duration_ms:
                    return format_duration(track.duration_ms)
                return "LIVE" if track.is_stream else ""
            case Qt.ItemDataRole.TextAlignmentRole if column == TIME_COLUMN:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            case Qt.ItemDataRole.ForegroundRole if row == self._playlist.current_index:
                return self._current_color
            case Qt.ItemDataRole.ToolTipRole:
                return str(track.location)
        return None

    # -- Drag & drop ---------------------------------------------------------- #

    def flags(self, index: _Index) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled  # drops land *between* rows
        return super().flags(index) | Qt.ItemFlag.ItemIsDragEnabled

    def supportedDropActions(self) -> Qt.DropAction:
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def mimeTypes(self) -> list[str]:
        return [ROWS_MIME, TRACKS_MIME, "text/uri-list"]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        rows = sorted({i.row() for i in indexes if i.isValid()})
        data = QMimeData()
        data.setData(ROWS_MIME, ",".join(map(str, rows)).encode())
        return data

    def dropMimeData(
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: _Index,
    ) -> bool:
        if row < 0:  # dropped onto a row, or below the last one
            row = parent.row() if parent.isValid() else len(self._playlist)
        if data.hasFormat(ROWS_MIME):
            raw = bytes(data.data(ROWS_MIME).data()).decode()
            rows = [int(r) for r in raw.split(",") if r]
            # Qt is still handling the drop: reorder (and reset the model) right after it.
            QTimer.singleShot(0, lambda: self.rows_moved.emit(self._playlist.move(rows, row)))
            return True
        if tracks := dragged_tracks(data):
            self.tracks_dropped.emit(tracks)
            return True
        if data.hasUrls():
            paths = dropped_locations(data.urls())
            if paths:
                self.files_dropped.emit(paths)
                return True
        return False

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
