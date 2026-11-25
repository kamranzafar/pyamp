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
"""The library window: sources in the sidebar, browsing, searching, playing, managing."""

from __future__ import annotations

import threading
import time
from collections.abc import Hashable, Iterator, Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QItemSelectionModel, QTimer
from PySide6.QtWidgets import QFileDialog, QTreeWidgetItem

from pyamp.config import Settings
from pyamp.core.events import EventBus, LibraryChanged
from pyamp.core.models import StreamUrl, Track
from pyamp.library.catalog import Catalog, FileStamp, Group, GroupKind
from pyamp.library.local_source import LocalSource
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.scanner import LibraryScanner, ScanSummary
from pyamp.library.sources import MediaSources, Node, NodeAction, SourceError, matches
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.library_window import LOADING, LibraryWindow
from pyamp.ui.skin import DEFAULT_SKIN
from tests.test_background_loader import wait_until
from tests.test_metadata import write_wav

STAMP = FileStamp(1, 2)
ARTISTS = Group(GroupKind.ARTIST, "")  # only its kind matters as a key
MASSIVE = Group(GroupKind.ARTIST, "Massive Attack")
PORTISHEAD = Group(GroupKind.ARTIST, "Portishead")


class Calls:
    def __init__(self) -> None:
        self.played: list[tuple[list[str], int]] = []
        self.added: list[list[str]] = []

    def play(self, tracks: Sequence[Track], start: int) -> None:
        self.played.append(([t.title for t in tracks], start))

    def add(self, tracks: Sequence[Track]) -> None:
        self.added.append([t.title for t in tracks])


def stream(title: str) -> Track:
    return Track.for_stream(StreamUrl(f"https://server.example/{title}"), title)


class FakeServer:
    """A source like a server: slow when the test says so, and able to fail."""

    hint = "Search the server"

    def __init__(self, name: str = "Server") -> None:
        self.name = name
        self.gate = threading.Event()
        self.gate.set()
        self.reached = threading.Event()  # a call has reached the server
        self.failing: set[Hashable] = set()
        self.surprise: set[Hashable] = set()  # fail with something that isn't a SourceError
        self.albums = {
            "a1": [stream("Teardrop"), stream("Angel")],
            "a2": [stream("Roads")],
        }

    def _check(self, key: Hashable) -> None:
        self.reached.set()
        self.gate.wait(timeout=5)
        if key in self.failing:
            raise SourceError("The server is down")
        if key in self.surprise:
            raise RuntimeError("bug")

    def roots(self) -> list[Node]:
        return [Node("albums", "Albums", expandable=True, has_tracks=False)]

    def children(self, node: Node) -> list[Node]:
        self._check(node.key)
        return [Node(key, key, str(len(songs))) for key, songs in self.albums.items()]

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        self._check(node.key if node else None)
        if node is None:
            songs = [t for album in self.albums.values() for t in album]
            return [t for t in songs if search and matches(t, search)]
        return [t for t in self.albums.get(str(node.key), []) if matches(t, search)]

    def actions(self, node: Node) -> list[NodeAction]:
        return []


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
def sources(catalog: Catalog, library: BackgroundLibraryScanner, bus: EventBus) -> MediaSources:
    """As the app sets them up: this computer, refreshed when the library changes."""
    sources = MediaSources(bus)
    local = LocalSource(catalog, library.remove_source)
    sources.add(local)
    bus.subscribe(LibraryChanged, lambda _: sources.changed(local))
    return sources


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
    library: BackgroundLibraryScanner, sources: MediaSources, bus: EventBus, calls: Calls
) -> Iterator[LibraryWindow]:
    window = LibraryWindow(
        library,
        sources,
        bus,
        Settings(),
        DEFAULT_SKIN,
        play_tracks=calls.play,
        add_tracks=calls.add,
    )
    settle(window)
    yield window
    window.close()


def items(parent: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    return [child for i in range(parent.childCount()) if (child := parent.child(i)) is not None]


def loading(window: LibraryWindow) -> bool:
    pending = []
    for i in range(window.sidebar.topLevelItemCount()):
        top = window.sidebar.topLevelItem(i)
        stack = [top] if top is not None else []
        while stack:
            item = stack.pop()
            pending += [c for c in items(item) if c.text(0) == LOADING]
            stack += items(item)
    return bool(pending) or window.status.text() == LOADING


def settle(window: LibraryWindow) -> None:
    """Wait for the sidebar and the table to have heard back from their sources."""
    wait_until(lambda: not loading(window))


def item(window: LibraryWindow, source: int, *keys: Hashable) -> QTreeWidgetItem:
    """The sidebar item at ``keys``, opening (and loading) the way to it."""
    found = window.sidebar_item(source)
    assert found is not None
    for depth in range(len(keys)):
        found.setExpanded(True)
        settle(window)
        found = window.sidebar_item(source, *keys[: depth + 1])
        assert found is not None, keys[: depth + 1]
    return found


def pick(window: LibraryWindow, source: int, *keys: Hashable) -> None:
    window.sidebar.setCurrentItem(item(window, source, *keys))
    settle(window)


def labels(window: LibraryWindow, source: int, *keys: Hashable) -> list[str]:
    parent = item(window, source, *keys)
    parent.setExpanded(True)
    settle(window)
    return [child.text(0) for child in items(parent)]


def titles(window: LibraryWindow) -> list[str]:
    return [t.title for t in window.model.tracks()]


def refresh(window: LibraryWindow, bus: EventBus) -> None:
    bus.publish(LibraryChanged())  # as after a scan
    settle(window)


# -- This computer -------------------------------------------------------------------- #


def test_an_empty_library_says_how_to_start(window: LibraryWindow) -> None:
    assert window.model.rowCount() == 0
    assert window.status.text() == LocalSource.hint


def test_shows_every_track_and_how_to_browse_them(
    music: Path, window: LibraryWindow, bus: EventBus
) -> None:
    refresh(window, bus)
    top = window.sidebar_item(0)
    assert top is not None
    assert top.text(0) == "This Computer"
    assert window.sidebar.currentItem() is top  # the whole source: every track
    assert titles(window) == ["Angel", "Teardrop", "Glory Box"]
    assert labels(window, 0) == ["Artists", "Albums", "Genres", "Folders"]
    assert labels(window, 0, GroupKind.ARTIST) == ["Massive Attack (2)", "Portishead (1)"]
    assert labels(window, 0, GroupKind.ALBUM) == [
        "Dummy — Portishead (1)",
        "Mezzanine — Massive Attack (2)",
    ]
    assert labels(window, 0, GroupKind.FOLDER) == ["music (3)"]
    assert window.status.text() == "3 tracks · 0:00"


def test_picking_an_artist_shows_its_tracks(
    music: Path, window: LibraryWindow, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 0, GroupKind.ARTIST, PORTISHEAD)
    assert titles(window) == ["Glory Box"]
    picked = window.selected()
    assert picked is not None
    assert picked[1] is not None
    assert picked[1].key == PORTISHEAD


def test_refreshing_keeps_the_selection_and_what_is_open(
    music: Path, window: LibraryWindow, catalog: Catalog, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 0, GroupKind.ARTIST, MASSIVE)
    catalog.put([(Track(music / "4.mp3", "Risingson", artist="Massive Attack"), STAMP)])
    refresh(window, bus)
    wait_until(lambda: "Risingson" in titles(window))
    picked = window.selected()
    assert picked is not None and picked[1] is not None
    assert picked[1].key == MASSIVE
    assert item(window, 0, GroupKind.ARTIST, MASSIVE).text(0) == "Massive Attack (3)"
    albums = window.sidebar_item(0, GroupKind.ALBUM)
    assert albums is not None and not albums.isExpanded()


def test_a_group_that_is_gone_falls_back_to_the_whole_source(
    music: Path, window: LibraryWindow, catalog: Catalog, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 0, GroupKind.GENRE, Group(GroupKind.GENRE, "Trip Hop"))
    catalog.remove([music / "3.mp3"])
    refresh(window, bus)
    assert window.sidebar.currentItem() is window.sidebar_item(0)
    assert titles(window) == ["Angel", "Teardrop"]


def test_search_waits_for_typing_to_pause(
    music: Path, window: LibraryWindow, bus: EventBus
) -> None:
    refresh(window, bus)
    window.search.setText("tear")
    assert len(titles(window)) == 3  # not yet
    wait_until(lambda: titles(window) == ["Teardrop"])
    pick(window, 0, GroupKind.ARTIST, PORTISHEAD)
    assert titles(window) == []  # searches within what's picked
    window.search.setText("")
    window.search.returnPressed.emit()  # Enter searches at once
    settle(window)
    assert titles(window) == ["Glory Box"]


def test_playing_plays_the_tracks_shown(
    music: Path, window: LibraryWindow, calls: Calls, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 0, GroupKind.ARTIST, MASSIVE)
    window.play_from(1)
    window.play_from(99)  # not a row: ignored
    assert calls.played == [(["Angel", "Teardrop"], 1)]


def test_the_track_menu_plays_and_adds(
    music: Path, window: LibraryWindow, calls: Calls, bus: EventBus
) -> None:
    refresh(window, bus)
    play, add = window.table_menu(-1).actions()  # below the last track, nothing selected
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
    music: Path, window: LibraryWindow, catalog: Catalog, bus: EventBus
) -> None:
    refresh(window, bus)
    assert window.sidebar_menu(item(window, 0, GroupKind.ARTIST, MASSIVE)) is None
    assert window.sidebar_menu(window.sidebar_item(0)) is None
    assert window.sidebar_menu(None) is None
    menu = window.sidebar_menu(
        item(window, 0, GroupKind.FOLDER, Group(GroupKind.FOLDER, str(music)))
    )
    assert menu is not None
    (remove,) = menu.actions()
    remove.trigger()
    settle(window)
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
    settle(window)
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
    music: Path, window: LibraryWindow, library: BackgroundLibraryScanner, bus: EventBus
) -> None:
    refresh(window, bus)
    library.finished.emit(ScanSummary(unchanged=3, missing_sources=(music,)))
    assert window.status.text() == "3 tracks · 0:00 · Not found: music"


# -- Other sources ---------------------------------------------------------------------- #


@pytest.fixture
def server(sources: MediaSources, window: LibraryWindow) -> FakeServer:
    server = FakeServer()
    sources.add(server)
    settle(window)
    return server


def test_sources_from_plugins_come_after_this_computer(
    music: Path, window: LibraryWindow, sources: MediaSources, server: FakeServer
) -> None:
    names = [window.sidebar.topLevelItem(i).text(0) for i in range(2)]  # type: ignore[union-attr]
    assert names == ["This Computer", "Server"]
    assert labels(window, 1, "albums") == ["a1 (2)", "a2 (1)"]
    sources.remove(server)
    settle(window)
    assert window.sidebar.topLevelItemCount() == 1


def test_a_source_change_keeps_the_selection_elsewhere(
    music: Path, window: LibraryWindow, sources: MediaSources, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 0, GroupKind.ARTIST, PORTISHEAD)
    sources.add(FakeServer())
    settle(window)
    assert titles(window) == ["Glory Box"]


def test_a_slow_source_never_blocks_the_window(window: LibraryWindow, server: FakeServer) -> None:
    server.gate.clear()
    item(window, 1).setExpanded(True)  # loads the server's roots
    settle(window)
    albums = window.sidebar_item(1, "albums")
    assert albums is not None
    albums.setExpanded(True)
    assert [c.text(0) for c in items(albums)] == [LOADING]
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    wait_until(lambda: len(ticks) >= 5)  # the window's event loop kept running
    timer.stop()
    server.gate.set()
    settle(window)
    assert [c.text(0) for c in items(albums)] == ["a1 (2)", "a2 (1)"]


def test_picking_a_source_lists_its_tracks(window: LibraryWindow, server: FakeServer) -> None:
    pick(window, 1, "albums", "a1")
    assert titles(window) == ["Angel", "Teardrop"]  # album order: by title, no tags
    pick(window, 1)
    assert titles(window) == []
    assert window.status.text() == "Search the server"
    window.search.setText("roads")
    window.search.returnPressed.emit()
    settle(window)
    assert titles(window) == ["Roads"]


def test_only_the_latest_pick_fills_the_table(window: LibraryWindow, server: FakeServer) -> None:
    first, second = item(window, 1, "albums", "a1"), item(window, 1, "albums", "a2")
    server.gate.clear()  # both requests wait at the server
    window.sidebar.setCurrentItem(first)
    window.sidebar.setCurrentItem(second)
    server.gate.set()
    settle(window)
    QCoreApplication.processEvents()
    assert titles(window) == ["Roads"]


def test_errors_are_shown_and_can_be_retried(window: LibraryWindow, server: FakeServer) -> None:
    server.failing.add("albums")
    albums = item(window, 1, "albums")
    albums.setExpanded(True)
    settle(window)
    assert window.status.text() == "The server is down"
    assert items(albums) == []
    server.failing.clear()
    albums.setExpanded(False)
    albums.setExpanded(True)  # try again
    settle(window)
    assert len(items(albums)) == 2


def test_unexpected_errors_are_logged(
    window: LibraryWindow, server: FakeServer, caplog: pytest.LogCaptureFixture
) -> None:
    item(window, 1, "albums", "a1")
    server.surprise.add("a1")
    pick(window, 1, "albums", "a1")
    assert window.status.text() == "Something went wrong; see the log for details"
    assert "Library source failed" in caplog.text
    assert titles(window) == []


def test_the_selection_falls_back_when_its_source_goes(
    music: Path, window: LibraryWindow, sources: MediaSources, server: FakeServer, bus: EventBus
) -> None:
    refresh(window, bus)
    pick(window, 1, "albums", "a1")
    sources.remove(server)
    settle(window)
    assert window.sidebar.currentItem() is window.sidebar_item(0)
    assert titles(window) == ["Angel", "Teardrop", "Glory Box"]


def test_a_reload_that_fails_falls_back_to_the_whole_source(
    window: LibraryWindow, sources: MediaSources, server: FakeServer
) -> None:
    pick(window, 1, "albums", "a1")
    server.failing.add("albums")
    sources.changed(server)  # e.g. the server went away
    settle(window)
    assert window.sidebar.currentItem() is window.sidebar_item(1)
    assert titles(window) == []


def test_with_no_sources_there_is_nothing_to_show(
    window: LibraryWindow, sources: MediaSources
) -> None:
    (local,) = list(sources)
    sources.remove(local)
    settle(window)
    assert window.sidebar.topLevelItemCount() == 0
    assert window.selected() is None
    window.show_tracks()
    assert window.model.rowCount() == 0


def test_a_stuck_server_never_delays_the_other_sources(
    music: Path, window: LibraryWindow, server: FakeServer, bus: EventBus
) -> None:
    refresh(window, bus)
    portishead = item(window, 0, GroupKind.ARTIST, PORTISHEAD)  # listed before the trouble
    item(window, 1).setExpanded(True)
    settle(window)
    server.gate.clear()  # the server stops answering
    albums = window.sidebar_item(1, "albums")
    assert albums is not None
    albums.setExpanded(True)  # one call stuck at the server...
    window.sidebar.setCurrentItem(item(window, 1))
    window.search.returnPressed.emit()  # ...and two more: more than it has threads
    window.sidebar.setCurrentItem(portishead)
    wait_until(lambda: titles(window) == ["Glory Box"], timeout=2)  # not after the server
    server.gate.set()
    settle(window)


def test_a_removed_server_lets_go_of_its_threads(
    window: LibraryWindow, sources: MediaSources, server: FakeServer
) -> None:
    item(window, 1).setExpanded(True)
    settle(window)
    server.gate.clear()
    albums = window.sidebar_item(1, "albums")
    assert albums is not None
    server.reached.clear()
    albums.setExpanded(True)
    assert server.reached.wait(timeout=2)  # running, and stuck at the server
    started = time.monotonic()
    sources.remove(server)
    assert time.monotonic() - started < 0.5  # didn't wait for the server
    assert len(window._retired) == 1
    server.gate.set()
    wait_until(lambda: not window._retired[0].busy)
    window.rebuild()
    assert window._retired == []
