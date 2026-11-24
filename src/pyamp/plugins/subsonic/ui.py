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
"""The Subsonic plugin's windows: the server dialog and the library browser.

Requests run on a worker thread (:class:`Requests`) and their results come back to the
main thread through a queued Qt signal, so the windows never freeze on a slow server.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyamp.core.models import Track, format_duration
from pyamp.plugins.subsonic.client import Item, SubsonicClient, SubsonicError, normalise_server

log = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_MS = 3000


def describe(error: BaseException) -> str:
    """A short message for the user."""
    if isinstance(error, SubsonicError | ValueError):
        return str(error)
    if isinstance(error, OSError):
        reason = getattr(error, "reason", None) or error.strerror or error
        return f"Could not reach the server: {reason}"
    return f"Something went wrong: {error}"


class _Task(QRunnable):
    def __init__(
        self,
        call: Callable[[], Any],
        done: Callable[[Any, BaseException | None], None],
        signals: Requests,
    ) -> None:
        super().__init__()
        self._call, self._done, self._signals = call, done, signals

    def run(self) -> None:  # on the worker thread
        try:
            result = self._call()
        except Exception as exc:
            self._signals.finished.emit(self._done, None, exc)
        else:
            self._signals.finished.emit(self._done, result, None)


class Requests(QObject):
    """Runs server requests off the main thread; ``done`` is called back on it."""

    finished = Signal(object, object, object)  # (done, result, error)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self.finished.connect(self._deliver)

    def run(
        self, call: Callable[[], Any], done: Callable[[Any, BaseException | None], None]
    ) -> None:
        self._pool.start(_Task(call, done, self))

    def _deliver(
        self, done: Callable[[Any, BaseException | None], None], result: Any, error: Any
    ) -> None:  # a method slot, so it runs on this object's (the main) thread
        done(result, error)

    def wait(self) -> bool:
        return self._pool.waitForDone(SHUTDOWN_TIMEOUT_MS)


class ServerDialog(QDialog):
    """Asks for the server and login, and checks them before accepting."""

    def __init__(
        self,
        server: str = "",
        username: str = "",
        *,
        has_keychain: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Subsonic Server")
        self.client: SubsonicClient | None = None
        self._requests = Requests(self)

        self.server = QLineEdit(server)
        self.server.setPlaceholderText("https://music.example.com")
        self.username = QLineEdit(username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("Remember the password in the system keychain")
        self.remember.setChecked(has_keychain)
        self.remember.setEnabled(has_keychain)
        if not has_keychain:
            self.remember.setToolTip("No keychain is available; you'll be asked each session")
        self.status = QLabel()
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Server:", self.server)
        form.addRow("Username:", self.username)
        form.addRow("Password:", self.password)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.connect_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.connect_button.setText("Connect")
        self.buttons.accepted.connect(self._connect)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.remember)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)
        (self.password if server and username else self.server).setFocus()

    def _connect(self) -> None:
        try:
            client = SubsonicClient(
                normalise_server(self.server.text()),
                self.username.text().strip(),
                self.password.text(),
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.connect_button.setEnabled(False)
        self.status.setText("Connecting…")
        self._requests.run(client.ping, lambda _, error: self._connected(client, error))

    def _connected(self, client: SubsonicClient, error: BaseException | None) -> None:
        self.connect_button.setEnabled(True)
        if error is not None:
            self.status.setText(describe(error))
            return
        self.client = client
        self.accept()

    def done(self, result: int) -> None:
        self._requests.wait()
        super().done(result)


# Item data roles for the library tree.
_KIND = Qt.ItemDataRole.UserRole
_ID = Qt.ItemDataRole.UserRole + 1
_LOADED = Qt.ItemDataRole.UserRole + 2


class BrowserWindow(QWidget):
    """Artists → albums, and playlists, on the left; their songs on the right."""

    def __init__(
        self,
        client: SubsonicClient,
        add: Callable[[list[Track], bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"Subsonic — {client.server}")
        self.resize(820, 520)
        self._client = client
        self._add = add  # (tracks, play)
        self._requests = Requests(self)
        self._songs_request = 0  # only the latest request may fill the song list
        self._tracks: list[Track] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search songs and albums…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._search)

        self.library = QTreeWidget()
        self.library.setHeaderHidden(True)
        self.library.itemExpanded.connect(self._expand)
        self.library.currentItemChanged.connect(lambda item, _: self._open(item))
        self._artists = self._branch("Artists", "artists")
        self._playlists = self._branch("Playlists", "playlists")
        self._results: QTreeWidgetItem | None = None

        self.songs = QTreeWidget()
        self.songs.setHeaderLabels(["#", "Title", "Artist", "Album", "Time"])
        self.songs.setRootIsDecorated(False)
        self.songs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.songs.itemDoubleClicked.connect(lambda *_: self._send(play=True))

        splitter = QSplitter()
        splitter.addWidget(self.library)
        splitter.addWidget(self.songs)
        splitter.setSizes([260, 560])

        self.status = QLabel()
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(lambda: self._send(play=False))
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(lambda: self._send(play=True))
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.add_button)
        bottom.addWidget(self.play_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)

    def _branch(self, title: str, kind: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self.library, [title])
        item.setData(0, _KIND, kind)
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    # -- Library tree ------------------------------------------------------- #

    def _expand(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _LOADED):
            return
        item.setData(0, _LOADED, True)
        kind = item.data(0, _KIND)
        if kind == "artists":
            self._fill(item, self._client.artists, "artist", expandable=True)
        elif kind == "artist":
            self._fill(item, lambda: self._client.albums(item.data(0, _ID)), "album")
        elif kind == "playlists":
            self._fill(item, self._client.playlists, "playlist")

    def _fill(
        self,
        parent: QTreeWidgetItem,
        call: Callable[[], list[Item]],
        kind: str,
        *,
        expandable: bool = False,
    ) -> None:
        self.status.setText("Loading…")

        def done(items: list[Item] | None, error: BaseException | None) -> None:
            if error is not None or items is None:
                parent.setData(0, _LOADED, False)  # let the user try again
                self.status.setText(describe(error) if error else "")
                return
            for entry in items:
                label = f"{entry.name}  ({entry.detail})" if entry.detail else entry.name
                child = QTreeWidgetItem(parent, [label])
                child.setData(0, _KIND, kind)
                child.setData(0, _ID, entry.id)
                if expandable:
                    child.setChildIndicatorPolicy(
                        QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
                    )
            if not items:
                parent.setChildIndicatorPolicy(
                    QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator
                )
            self.status.setText("")

        self._requests.run(call, done)

    def _open(self, item: QTreeWidgetItem | None) -> None:
        if item is None:
            return
        kind, item_id = item.data(0, _KIND), item.data(0, _ID)
        if kind == "album":
            self._load_songs(lambda: self._client.album_tracks(item_id))
        elif kind == "playlist":
            self._load_songs(lambda: self._client.playlist_tracks(item_id))

    # -- Songs -------------------------------------------------------------- #

    def _search(self) -> None:
        query = self.search.text().strip()
        if not query:
            return
        self._songs_request += 1
        request = self._songs_request
        self.status.setText("Searching…")

        def done(
            result: tuple[list[Item], list[Track]] | None, error: BaseException | None
        ) -> None:
            if request != self._songs_request:
                return
            if error is not None or result is None:
                self.status.setText(describe(error) if error else "")
                return
            albums, tracks = result
            self._show_results(query, albums)
            self._show_songs(tracks)

        self._requests.run(lambda: self._client.search(query), done)

    def _show_results(self, query: str, albums: list[Item]) -> None:
        if self._results is not None:
            index = self.library.indexOfTopLevelItem(self._results)
            self.library.takeTopLevelItem(index)
        self._results = QTreeWidgetItem([f"Albums matching “{query}”"])
        self.library.insertTopLevelItem(0, self._results)
        for entry in albums:
            label = f"{entry.name}  ({entry.detail})" if entry.detail else entry.name
            child = QTreeWidgetItem(self._results, [label])
            child.setData(0, _KIND, "album")
            child.setData(0, _ID, entry.id)
        self._results.setExpanded(True)

    def _load_songs(self, call: Callable[[], list[Track]]) -> None:
        self._songs_request += 1
        request = self._songs_request
        self.status.setText("Loading…")

        def done(tracks: list[Track] | None, error: BaseException | None) -> None:
            if request != self._songs_request:
                return  # the user has moved on to another album
            if error is not None or tracks is None:
                self.status.setText(describe(error) if error else "")
                return
            self._show_songs(tracks)

        self._requests.run(call, done)

    def _show_songs(self, tracks: list[Track]) -> None:
        self._tracks = tracks
        self.songs.clear()
        for n, track in enumerate(tracks, start=1):
            time = format_duration(track.duration_ms) if track.duration_ms else ""
            row = QTreeWidgetItem([str(n), track.title, track.artist, track.album, time])
            row.setData(0, _ID, n - 1)
            self.songs.addTopLevelItem(row)
        for column in range(self.songs.columnCount()):
            self.songs.resizeColumnToContents(column)
        self.status.setText(f"{len(tracks)} songs" if len(tracks) != 1 else "1 song")

    def _send(self, *, play: bool) -> None:
        """Add the selected songs (or all of them, if none are selected) to the playlist."""
        rows = sorted(self.songs.indexOfTopLevelItem(i) for i in self.songs.selectedItems())
        tracks = [self._tracks[r] for r in rows] if rows else list(self._tracks)
        if tracks:
            self._add(tracks, play)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._requests.wait()
        super().closeEvent(event)
