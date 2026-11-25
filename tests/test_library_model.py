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
"""The library's track table: columns, sorting and dragging files out."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt

from pyamp.core.models import Track
from pyamp.ui.library_model import COLUMNS, LibraryModel

TRACKS = [
    Track(Path("/m/b.mp3"), "Beta", artist="Zed", album="Two", year=2001, track_number=2),
    Track(Path("/m/a.mp3"), "alpha", artist="Ann", album="One", duration_ms=61_000),
    Track(Path("/m/c.mp3"), "Gamma", artist="Zed", album="Two", track_number=1, genre="Pop"),
]


@pytest.fixture
def model(qt_app: QCoreApplication) -> LibraryModel:
    model = LibraryModel()
    model.set_tracks(TRACKS)
    return model


def column(name: str) -> int:
    return [c.header for c in COLUMNS].index(name)


def row_texts(model: LibraryModel, row: int) -> list[str]:
    return [model.data(model.index(row, c)) for c in range(model.columnCount())]


def test_shows_tracks_in_album_order(model: LibraryModel) -> None:
    assert [model.headerData(c, Qt.Orientation.Horizontal) for c in range(7)] == [
        "#",
        "Title",
        "Artist",
        "Album",
        "Year",
        "Genre",
        "Time",
    ]
    assert [t.title for t in model.tracks()] == ["alpha", "Gamma", "Beta"]
    assert row_texts(model, 0) == ["", "alpha", "Ann", "One", "", "", "1:01"]
    assert row_texts(model, 2) == ["2", "Beta", "Zed", "Two", "2001", "", ""]
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) == "/m/a.mp3"


def test_numbers_are_right_aligned(model: LibraryModel) -> None:
    right = model.data(model.index(0, column("Year")), Qt.ItemDataRole.TextAlignmentRole)
    assert right & Qt.AlignmentFlag.AlignRight
    assert model.data(model.index(0, column("Title")), Qt.ItemDataRole.TextAlignmentRole) is None


def test_columns_sort_and_ties_stay_in_album_order(model: LibraryModel) -> None:
    model.sort(column("Artist"), Qt.SortOrder.DescendingOrder)
    assert [t.title for t in model.tracks()] == ["Gamma", "Beta", "alpha"]
    model.sort(column("Title"))
    assert [t.title for t in model.tracks()] == ["alpha", "Beta", "Gamma"]  # ignoring case
    model.set_tracks(list(reversed(TRACKS)))  # new tracks keep the user's sort
    assert [t.title for t in model.tracks()] == ["alpha", "Beta", "Gamma"]
    model.sort(-1)
    assert [t.title for t in model.tracks()] == ["alpha", "Gamma", "Beta"]


def test_dragged_tracks_are_files(model: LibraryModel) -> None:
    assert model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsDragEnabled
    assert model.mimeTypes() == ["text/uri-list"]
    data = model.mimeData([model.index(2, 0), model.index(0, 1), model.index(0, 0)])
    assert [Path(url.toLocalFile()) for url in data.urls()] == [Path("/m/a.mp3"), Path("/m/b.mp3")]


def test_nothing_for_invalid_indexes(model: LibraryModel) -> None:
    assert model.data(QModelIndex()) is None
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0
    assert model.headerData(0, Qt.Orientation.Vertical) is None
