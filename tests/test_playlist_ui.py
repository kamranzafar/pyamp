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
"""The playlist manager's UI, driven through the real window (offscreen)."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QMimeData, QModelIndex, Qt, QUrl
from PySide6.QtGui import QStatusTipEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QToolButton

from pyamp.app import Application
from pyamp.audio import registry
from pyamp.config import Settings, SettingsStore
from pyamp.core.models import StreamUrl, Track
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.ui.jump_dialog import matches, search_text
from pyamp.ui.playlist_model import ROWS_MIME
from pyamp.ui.track_drag import TrackDrag
from tests.conftest import FakeBackend
from tests.test_background_loader import make_files, wait_until


@pytest.fixture
def app(
    qt_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Application]:
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
    store = SettingsStore(tmp_path / "config")
    application = Application(args, store, SavedPlaylists(tmp_path / "playlists"))
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    yield application
    application.loader.shutdown()
    application.window.close()


def add_songs(app: Application, folder: Path, *names: str) -> list[Track]:
    folder.mkdir(parents=True, exist_ok=True)
    tracks = []
    for name in names:
        path = folder / f"{name}.mp3"
        path.write_bytes(b"")
        tracks.append(Track(location=path, title=name))
    app.playlist.add(tracks)
    return tracks


def titles(app: Application) -> list[str]:
    return [t.title for t in app.playlist]


def answer_input(monkeypatch: pytest.MonkeyPatch, text: str, ok: bool = True) -> None:
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (text, ok))


# -- Selector --------------------------------------------------------------------- #


def test_selector_lists_playlists_and_switches(app: Application, tmp_path: Path) -> None:
    combo = app.window._selector.combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["Default"]
    app.playlists.new("Running")
    wait_until(lambda: not app.playlists.loading)
    assert [combo.itemText(i) for i in range(combo.count())] == ["Default", "Running"]
    assert combo.currentText() == "Running"

    combo.textActivated.emit("Default")
    wait_until(lambda: not app.playlists.loading)
    assert app.playlists.active == "Default"


# -- Menu actions -------------------------------------------------------------------- #


def test_new_rename_delete_via_dialogs(app: Application, monkeypatch: pytest.MonkeyPatch) -> None:
    answer_input(monkeypatch, "Jazz")
    app.window.new_playlist()
    assert app.playlists.active == "Jazz"

    answer_input(monkeypatch, "Smooth jazz")
    app.window.rename_playlist()
    assert app.playlists.active == "Smooth jazz"

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    answer_input(monkeypatch, "default")  # taken
    app.window.rename_playlist()
    assert app.playlists.active == "Smooth jazz"
    assert "already a playlist" in warnings[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    app.window.delete_playlist()
    assert app.playlists.names == ["Default"]


def test_cancelled_dialogs_change_nothing(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_input(monkeypatch, "Ignored", ok=False)
    app.window.new_playlist()
    app.window.rename_playlist()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    app.window.delete_playlist()
    assert app.playlists.names == ["Default"]
    assert app.playlists.active == "Default"


def test_sort_and_dedupe_menu_actions(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "b", "c", "a")
    sort_menu = app.window._sort_menu
    by_title = next(a for a in sort_menu.actions() if a.text() == "&Title")
    by_title.trigger()  # Qt passes "checked" to slots: must not break sorting
    assert titles(app) == ["a", "b", "c"]
    next(a for a in sort_menu.actions() if a.text() == "&Reverse Order").trigger()
    assert titles(app) == ["c", "b", "a"]

    app.playlist.add([app.playlist[0]])
    app.window._dedupe_action.trigger()
    assert titles(app) == ["c", "b", "a"]
    assert "Removed 1 duplicate" in app.window.statusBar().currentMessage()


def test_the_status_bar_keeps_showing_the_playlist(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "a", "b")
    window = app.window
    assert window.summary.text() == "2 tracks · 0:00"
    # Hovering a button or menu item doesn't put its label in the status bar.
    button = window.findChildren(QToolButton)[0]
    before = window.statusBar().currentMessage()
    QApplication.sendEvent(button, QStatusTipEvent("Previous"))
    QApplication.sendEvent(button, QStatusTipEvent(""))  # and leaving it
    assert window.statusBar().currentMessage() == before
    window.statusBar().showMessage("Exported mix.m3u8", 50)  # a message shows for a while
    wait_until(lambda: window.statusBar().currentMessage() == "")
    assert window.summary.text() == "2 tracks · 0:00"  # and the playlist is still there


def test_context_menu_offers_editing_actions(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "a")
    texts = [a.text() for a in app.window._context_menu().actions() if a.text()]
    assert texts == [
        "&Play",
        "&Remove Selected",
        "&Sort by",
        "Remove D&uplicates",
        "&Jump to Track…",
    ]


# -- Drag & drop ----------------------------------------------------------------------- #


def drag_rows(app: Application, rows: list[int], to_row: int) -> None:
    model = app.window._playlist_view.model()
    data = model.mimeData([model.index(r, 0) for r in rows])
    assert data.hasFormat(ROWS_MIME)
    assert model.dropMimeData(data, Qt.DropAction.MoveAction, to_row, 0, QModelIndex())
    QCoreApplication.processEvents()  # the move happens right after the drop


def test_dragging_rows_reorders_and_keeps_them_selected(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "a", "b", "c", "d")
    drag_rows(app, [0, 1], 4)
    assert titles(app) == ["c", "d", "a", "b"]
    assert app.window._playlist_view.selected_rows() == [2, 3]


def test_dropping_files_from_outside_adds_them(app: Application, tmp_path: Path) -> None:
    songs = make_files(tmp_path / "finder", 2)
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(p)) for p in songs])
    model = app.window._playlist_view.model()
    assert model.dropMimeData(data, Qt.DropAction.CopyAction, -1, 0, QModelIndex())
    wait_until(lambda: len(app.playlist) == 2)


def test_dropping_tracks_from_the_library_adds_them_with_their_tags(
    app: Application, tmp_path: Path
) -> None:
    stream = Track.for_stream(StreamUrl("https://music.example/rest/stream?id=1"), "Teardrop")
    local = Track(tmp_path / "angel.mp3", "Angel", artist="Massive Attack")
    model = app.window._playlist_view.model()
    assert model.dropMimeData(
        TrackDrag([stream, local]), Qt.DropAction.CopyAction, -1, 0, QModelIndex()
    )
    assert list(app.playlist) == [stream, local]  # as they are: streams too, nothing re-read
    assert app.playlist.current_index == 0  # there was nothing to play: plays the first
    app.window.add_tracks([local])
    assert list(app.playlist) == [stream, local, local]
    assert app.playlist.current_index == 0


def test_reorder_is_auto_saved(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "a", "b")
    drag_rows(app, [1], 0)
    app.playlists.save_now()
    saved = SavedPlaylists(tmp_path / "playlists")
    assert [p.stem for p in saved.read_paths("Default")] == ["b", "a"]


# -- Jump to track ----------------------------------------------------------------------- #


def test_matches_need_every_word() -> None:
    tracks = [
        Track(location=Path("/m/01-intro.mp3"), title="Intro", artist="Band"),
        Track(location=Path("/m/02.mp3"), title="Blue Train", artist="Coltrane"),
        Track(location=Path("/m/03.mp3"), title="Train Song", artist="Band"),
    ]
    assert matches(tracks, "train") == [1, 2]
    assert matches(tracks, "band TRAIN") == [2]
    assert matches(tracks, "01-intro") == [0]  # file names count too
    assert matches(tracks, "") == [0, 1, 2]
    assert matches(tracks, "", limit=2) == [0, 1]
    assert matches(tracks, "a", limit=1) == [0]  # stops at the first match
    texts = [search_text(t) for t in tracks]
    assert matches(tracks, "band TRAIN", texts=texts) == [2]


def test_jump_dialog_plays_the_chosen_track(app: Application, tmp_path: Path) -> None:
    add_songs(app, tmp_path / "m", "alpha", "beta", "gamma")
    dialog = app.window.show_jump_dialog()
    dialog.query.setText("gam")
    assert dialog.results.count() == 1
    dialog._jump()
    assert app.playlist.current_index == 2
    assert not dialog.isVisible()
