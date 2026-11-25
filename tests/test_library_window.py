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
"""The library window: browsing, searching, playing and managing folders."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QItemSelectionModel
from PySide6.QtWidgets import QFileDialog

from pyamp.config import Settings
from pyamp.core.events import EventBus, LibraryChanged
from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, FileStamp, Group, GroupKind
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.scanner import LibraryScanner, ScanSummary
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.library_window import ALL_TRACKS, LibraryWindow, group_label
from pyamp.ui.skin import DEFAULT_SKIN
from tests.test_background_loader import wait_until
from tests.test_metadata import write_wav

STAMP = FileStamp(1, 2)


class Calls:
    def __init__(self) -> None:
        self.played: list[tuple[list[str], int]] = []
        self.added: list[list[str]] = []

    def play(self, tracks: Sequence[Track], start: int) -> None:
        self.played.append(([t.title for t in tracks], start))

    def add(self, tracks: Sequence[Track]) -> None:
        self.added.append([t.title for t in tracks])


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    yield opened
    opened.close()


@pytest.fixture
def library(
    qt_app: QCoreApplication, catalog: Catalog, bus: EventBus
) -> Iterator[BackgroundLibraryScanner]:
    background = BackgroundLibraryScanner(
        catalog, LibraryScanner(catalog, MutagenMetadataReader()), bus
    )
    yield background
    background.shutdown()


@pytest.fixture
def music(catalog: Catalog, tmp_path: Path) -> Path:
    """A library of two artists in one folder (the files needn't exist)."""
    folder = tmp_path / "music"
    catalog.add_source(folder)
    entries = [
        Track(folder / "1.mp3", "Teardrop", artist="Massive Attack", album="Mezzanine"),
        Track(folder / "2.mp3", "Angel", artist="Massive Attack", album="Mezzanine"),
        Track(folder / "3.mp3", "Glory Box", artist="Portishead", album="Dummy", genre="Trip Hop"),
    ]
    catalog.put((t, STAMP) for t in entries)
    return folder


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def window(
    library: BackgroundLibraryScanner, bus: EventBus, calls: Calls
) -> Iterator[LibraryWindow]:
    window = LibraryWindow(
        library, bus, Settings(), DEFAULT_SKIN, play_tracks=calls.play, add_tracks=calls.add
    )
    yield window
    window.close()


def current_text(window: LibraryWindow) -> str:
    item = window.sidebar.currentItem()
    assert item is not None
    return item.text(0)


def titles(window: LibraryWindow) -> list[str]:
    return [t.title for t in window.model.tracks()]


def children(window: LibraryWindow, kind: GroupKind) -> list[str]:
    category = window.category(kind)
    items = (category.child(i) for i in range(category.childCount()))
    return [item.text(0) for item in items if item is not None]


def test_an_empty_library_says_how_to_start(window: LibraryWindow) -> None:
    assert window.model.rowCount() == 0
    assert window.status.text() == "Add a folder of music to start your library"


def test_shows_every_track_and_the_sidebar(
    music: Path, window: LibraryWindow, bus: EventBus
) -> None:
    bus.publish(LibraryChanged())  # e.g. a scan finished
    assert titles(window) == ["Angel", "Teardrop", "Glory Box"]
    assert current_text(window) == ALL_TRACKS
    assert children(window, GroupKind.ARTIST) == ["Massive Attack (2)", "Portishead (1)"]
    assert children(window, GroupKind.ALBUM) == [
        "Dummy — Portishead (1)",
        "Mezzanine — Massive Attack (2)",
    ]
    assert children(window, GroupKind.GENRE) == ["Trip Hop (1)"]
    assert children(window, GroupKind.FOLDER) == ["music (3)"]
    assert window.status.text() == "3 tracks · 0:00"


def test_picking_a_group_shows_its_tracks(music: Path, window: LibraryWindow) -> None:
    window.refresh()
    window.select_group(Group(GroupKind.ARTIST, "Portishead"))
    assert titles(window) == ["Glory Box"]
    assert window.selected_group() == Group(GroupKind.ARTIST, "Portishead", count=1)
    window.select_group(None)
    assert len(titles(window)) == 3


def test_refreshing_keeps_the_selection_and_what_is_open(
    music: Path, window: LibraryWindow, catalog: Catalog
) -> None:
    window.refresh()
    window.category(GroupKind.ARTIST).setExpanded(True)
    window.select_group(Group(GroupKind.ARTIST, "Massive Attack"))
    catalog.put([(Track(music / "4.mp3", "Risingson", artist="Massive Attack"), STAMP)])
    window.refresh()
    assert window.category(GroupKind.ARTIST).isExpanded()
    assert not window.category(GroupKind.ALBUM).isExpanded()
    assert window.selected_group() == Group(GroupKind.ARTIST, "Massive Attack", count=3)
    assert "Risingson" in titles(window)


def test_a_group_that_is_gone_falls_back_to_all_tracks(
    music: Path, window: LibraryWindow, catalog: Catalog
) -> None:
    window.refresh()
    window.select_group(Group(GroupKind.GENRE, "Trip Hop"))
    catalog.remove([music / "3.mp3"])
    window.refresh()
    assert window.selected_group() is None
    assert titles(window) == ["Angel", "Teardrop"]


def test_search_waits_for_typing_to_pause(
    music: Path, window: LibraryWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.refresh()
    window.search.setText("tear")
    assert len(titles(window)) == 3  # not yet
    wait_until(lambda: titles(window) == ["Teardrop"])
    window.select_group(Group(GroupKind.ARTIST, "Portishead"))
    assert titles(window) == []  # searches within the group
    window.search.setText("")
    window.search.returnPressed.emit()  # Enter searches at once
    assert titles(window) == ["Glory Box"]


def test_playing_plays_the_tracks_shown(music: Path, window: LibraryWindow, calls: Calls) -> None:
    window.refresh()
    window.select_group(Group(GroupKind.ARTIST, "Massive Attack"))
    window.play_from(1)
    window.play_from(99)  # not a row: ignored
    assert calls.played == [(["Angel", "Teardrop"], 1)]


def test_the_track_menu_plays_and_adds(music: Path, window: LibraryWindow, calls: Calls) -> None:
    window.refresh()
    menu = window.table_menu(-1)  # below the last track, nothing selected
    play, add = menu.actions()
    assert not play.isEnabled()
    assert not add.isEnabled()

    window.table.selectRow(0)
    flags = QItemSelectionModel.SelectionFlag
    window.table.selectionModel().select(window.model.index(2, 0), flags.Select | flags.Rows)
    play, add = window.table_menu(2).actions()
    play.trigger()
    add.trigger()
    assert calls.played == [(["Angel", "Teardrop", "Glory Box"], 2)]
    assert calls.added == [["Angel", "Glory Box"]]


def test_folders_can_be_removed_from_the_sidebar(
    music: Path, window: LibraryWindow, catalog: Catalog
) -> None:
    window.refresh()
    assert window.sidebar_menu(window.category(GroupKind.ARTIST).child(0)) is None
    assert window.sidebar_menu(None) is None
    menu = window.sidebar_menu(window.category(GroupKind.FOLDER).child(0))
    assert menu is not None
    (remove,) = menu.actions()
    remove.trigger()
    assert catalog.sources() == []
    assert titles(window) == []  # refreshed


def test_adding_a_folder_scans_it(
    window: LibraryWindow,
    library: BackgroundLibraryScanner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "new"
    folder.mkdir()
    write_wav(folder / "song.wav")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(folder))
    window.add_folder()
    assert "Scanning…" in window.status.text()
    library.progress.emit(1234)
    assert window.status.text().endswith("Scanning… 1,234 files checked")
    assert window._settings.last_directory == str(folder)
    wait_until(lambda: titles(window) == ["song"])
    assert "Scanning" not in window.status.text()

    window.add_folder()  # again
    assert window.status.text() == "Already in the library"


def test_adding_files(
    window: LibraryWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song = write_wav(tmp_path / "one.wav")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: ([str(song)], ""))
    window.add_files()
    wait_until(lambda: titles(window) == ["one"])
    assert window._settings.last_directory == str(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: ([], ""))
    window.add_files()  # cancelled
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: "")
    window.add_folder()  # cancelled
    assert titles(window) == ["one"]


def test_missing_folders_are_shown_after_a_scan(
    music: Path, window: LibraryWindow, library: BackgroundLibraryScanner
) -> None:
    window.refresh()
    library.finished.emit(ScanSummary(unchanged=3, missing_sources=(music,)))
    assert window.status.text() == "3 tracks · 0:00 · Not found: music"


@pytest.mark.parametrize(
    ("group", "label"),
    [
        (Group(GroupKind.ARTIST, "", count=2), "Unknown Artist (2)"),
        (Group(GroupKind.ALBUM, "Dummy", "Portishead", 11), "Dummy — Portishead (11)"),
        (Group(GroupKind.ALBUM, "Dummy", "", 1), "Dummy (1)"),
        (Group(GroupKind.GENRE, "Rock", count=5), "Rock (5)"),
        (Group(GroupKind.FOLDER, "/music/rock", count=5), "rock (5)"),
        (Group(GroupKind.FOLDER, "/", count=5), "/ (5)"),
    ],
)
def test_group_labels(group: Group, label: str) -> None:
    assert group_label(group) == label
