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
"""Library tracks as a sortable Qt table (Adapter pattern), draggable into the playlist."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QUrl,
)

from pyamp.core.models import Track, format_duration
from pyamp.library.catalog import album_order

type _Index = QModelIndex | QPersistentModelIndex


def _number(value: int) -> str:
    return str(value) if value else ""


@dataclass(frozen=True, slots=True)
class _Column:
    header: str
    text: Callable[[Track], str]
    sort_key: Callable[[Track], Any]
    numeric: bool = False
    #: Starting width, as sample text (the column can be resized). None: takes the rest.
    width: str | None = "MMMMMMMMMM"


COLUMNS = (
    _Column(
        "#",
        lambda t: _number(t.track_number),
        lambda t: (t.disc_number, t.track_number),
        numeric=True,
        width=" 000 ",
    ),
    _Column("Title", lambda t: t.title, lambda t: t.title.casefold(), width=None),
    _Column("Artist", lambda t: t.artist, lambda t: t.artist.casefold()),
    _Column("Album", lambda t: t.album, album_order),
    _Column("Year", lambda t: _number(t.year), lambda t: t.year, numeric=True, width=" 0000 "),
    _Column("Genre", lambda t: t.genre, lambda t: t.genre.casefold(), width="MMMMMM"),
    _Column(
        "Time",
        lambda t: format_duration(t.duration_ms) if t.duration_ms else "",
        lambda t: t.duration_ms,
        numeric=True,
        width=" 00:00:00 ",
    ),
)


class LibraryModel(QAbstractTableModel):
    """The tracks the library window shows, in their source's order until a column is sorted
    (the local library gives them in album order)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._given: list[Track] = []  # in the source's order
        self._tracks: list[Track] = []  # as shown
        self._sort: tuple[int, Qt.SortOrder] = (-1, Qt.SortOrder.AscendingOrder)

    def set_tracks(self, tracks: Sequence[Track]) -> None:
        """Show ``tracks``, in the order given unless the user has sorted by a column."""
        self.beginResetModel()
        self._given = list(tracks)
        self._apply_sort()
        self.endResetModel()

    def tracks(self) -> list[Track]:
        """The tracks, in the order shown."""
        return list(self._tracks)

    def track(self, row: int) -> Track:
        return self._tracks[row]

    # -- QAbstractTableModel ---------------------------------------------------- #

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column = COLUMNS[index.column()]
        track = self._tracks[index.row()]
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return column.text(track)
            case Qt.ItemDataRole.TextAlignmentRole if column.numeric:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            case Qt.ItemDataRole.ToolTipRole:
                return str(track.location)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section].header
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """Sort by a column; a negative column means the order the tracks came in."""
        self.layoutAboutToBeChanged.emit()
        self._sort = (column, order)
        self._apply_sort()
        self.layoutChanged.emit()

    def _apply_sort(self) -> None:
        column, order = self._sort
        self._tracks = list(self._given)
        if 0 <= column < len(COLUMNS):  # stable: ties stay in the order they came in
            descending = order is Qt.SortOrder.DescendingOrder
            self._tracks.sort(key=COLUMNS[column].sort_key, reverse=descending)

    # -- Dragging into the playlist (or anywhere that takes files) --------------- #

    def flags(self, index: _Index) -> Qt.ItemFlag:
        flags = super().flags(index)
        return flags | Qt.ItemFlag.ItemIsDragEnabled if index.isValid() else flags

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, indexes: Sequence[QModelIndex]) -> QMimeData:
        """The dragged tracks' files. Streams (e.g. from a server) aren't dragged: as a
        bare URL they'd lose their tags; Add to Playlist keeps them."""
        rows = sorted({index.row() for index in indexes if index.isValid()})
        locations = [self._tracks[row].location for row in rows]
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(p)) for p in locations if isinstance(p, Path)])
        return data
