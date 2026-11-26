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

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QItemSelectionModel, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QAction, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QTreeWidgetItem

from pyamp import app as app_module
from pyamp.app import Application, parse_args
from pyamp.audio import libmpv_loader, registry
from pyamp.audio.registry import BackendUnavailableError
from pyamp.config import Settings, SettingsStore
from pyamp.core.equalizer import Equalizer
from pyamp.core.models import RepeatMode, ReplayGainMode, StreamUrl, Track
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.ui.library_window import LibraryWindow
from tests.conftest import FakeBackend, FakeGaplessBackend
from tests.test_background_loader import wait_until


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real audio device: 'fake' is registered, the default and the fallback."""
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")
    monkeypatch.setattr(app_module, "FALLBACK_BACKEND", "fake")


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path)


def build(store: SettingsStore, **overrides: object) -> Application:
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return Application(args, store, SavedPlaylists(store.directory / "playlists"))


@pytest.fixture
def cleanup(qt_app: QCoreApplication) -> Iterator[list[Application]]:
    apps: list[Application] = []
    yield apps
    for application in apps:
        application.loader.shutdown()
        application.library.shutdown()
        application.window.close()


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.paths == []
    assert args.backend is None
    assert args.no_plugins is False
    assert args.log_level == "WARNING"


def test_version_shows_libmpv_status(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app_module, "version_text", lambda: "pyamp 9.9\nlibmpv: somewhere (user folder)"
    )
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "pyamp 9.9\nlibmpv: somewhere (user folder)\n"


def test_version_text_reports_missing_libmpv(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def no_libmpv(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyamp.audio.mpv_backend":
            raise OSError("Cannot find libmpv")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_libmpv)
    text = app_module.version_text()
    assert text.startswith("pyamp ")
    assert "libmpv: not available (Cannot find libmpv)" in text


def test_parse_args_paths_and_flags() -> None:
    args = parse_args(["a.mp3", "dir", "--no-plugins", "--log-level", "INFO"])
    assert args.paths == [Path("a.mp3"), Path("dir")]
    assert args.no_plugins is True
    assert args.log_level == "INFO"


def test_saved_settings_are_applied_on_startup(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(volume=0.25, shuffle=True, repeat="one", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.volume == pytest.approx(0.25)
    assert application.player.shuffle is True
    assert application.player.repeat is RepeatMode.ONE


def test_replay_gain_setting_is_restored_and_saved(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(replay_gain="album", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.replay_gain is ReplayGainMode.ALBUM
    application.player.set_replay_gain(ReplayGainMode.TRACK)
    application.shutdown()
    assert store.load().replay_gain == "track"


def test_invalid_replay_gain_setting_falls_back_to_off(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(replay_gain="loudest", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.replay_gain is ReplayGainMode.OFF


def test_equalizer_setting_is_restored_and_saved(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    gains = [3.0, 2.0, 1.0, 0.0, -1.0, -2.0, 0.0, 1.0, 2.0, 3.0]
    store.save(
        Settings(
            equalizer_enabled=True, equalizer_preamp=-3.0, equalizer_gains=gains, backend="fake"
        )
    )
    application = build(store)
    cleanup.append(application)
    assert application.player.equalizer == Equalizer(True, -3.0, tuple(gains))

    application.player.set_equalizer(Equalizer().with_preset("Vocal"))
    application.shutdown()
    saved = store.load()
    assert saved.equalizer_enabled is False
    assert tuple(saved.equalizer_gains) == Equalizer().with_preset("Vocal").gains_db


@pytest.mark.parametrize("gains", [[1.0, 2.0], ["loud"] * 10, None])
def test_malformed_equalizer_setting_falls_back_to_flat(
    store: SettingsStore, cleanup: list[Application], gains: object
) -> None:
    store.save(Settings(equalizer_enabled=True, equalizer_gains=gains, backend="fake"))  # type: ignore[arg-type]
    application = build(store)
    cleanup.append(application)
    assert application.player.equalizer == Equalizer()


def test_equalizer_window_is_opened_from_the_menu_and_reused(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    application = build(store)
    cleanup.append(application)
    titles = [a.text() for a in application.window.findChildren(QAction)]
    assert "&Equaliser\u2026" in titles
    first = application.window.show_equalizer()
    assert application.window.show_equalizer() is first
    assert first.isVisible()
    first.close()


def test_libmpv_preference_from_flag_is_remembered(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libmpv_loader, "_state", {"preference": "auto"})
    application = build(store, libmpv="system")
    cleanup.append(application)
    assert libmpv_loader.get_preference() == "system"
    application.shutdown()
    assert store.load().libmpv == "system"


def test_invalid_libmpv_setting_falls_back_to_auto(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libmpv_loader, "_state", {"preference": "auto"})
    store.save(Settings(libmpv="newest", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert libmpv_loader.get_preference() == "auto"
    assert application.settings.libmpv == "auto"


def test_invalid_repeat_setting_falls_back_to_off(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(repeat="sideways", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.repeat is RepeatMode.OFF


def test_unknown_backend_in_settings_falls_back_to_default(
    store: SettingsStore, cleanup: list[Application], caplog: pytest.LogCaptureFixture
) -> None:
    store.save(Settings(backend="gone"))
    application = build(store, backend=None)
    cleanup.append(application)
    assert "Unknown backend 'gone'" in caplog.text
    assert application.player.backend_name == "fake"
    assert application.settings.backend == "fake"  # an unknown name is forgotten


def test_unavailable_backend_falls_back_to_default(
    store: SettingsStore,
    cleanup: list[Application],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str) -> FakeBackend:
        if name == "mpv":
            raise BackendUnavailableError("mpv", "Cannot find libmpv")
        return FakeBackend()

    monkeypatch.setattr(app_module, "create_backend", unavailable)
    application = build(store, backend="mpv")
    cleanup.append(application)
    assert "'mpv' is unavailable" in caplog.text
    assert application.player.backend_name == "fake"
    assert application.settings.backend == "mpv"  # tried again once libmpv is there


def test_mpv_is_the_default_backend() -> None:
    assert Settings.__dataclass_fields__["backend"].default == "mpv"


def test_chosen_backend_is_remembered(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry._registered, "other", FakeGaplessBackend)
    application = build(store, backend="other")
    cleanup.append(application)
    application.shutdown()
    assert store.load().backend == "other"  # the registry key, not the class's name


def test_shutdown_saves_settings_and_playlist(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    application.player.set_volume(0.4)
    application.player.set_shuffle(True)
    application.player.set_repeat(RepeatMode.ALL)
    tracks = [Track.from_path(tmp_path / "a.mp3"), Track.from_path(tmp_path / "b.mp3")]
    application.playlist.add(tracks)

    application.shutdown()

    saved = store.load()
    assert saved.volume == pytest.approx(0.4)
    assert saved.shuffle is True
    assert saved.repeat == "all"
    assert saved.active_playlist == "Default"
    playlists = SavedPlaylists(store.directory / "playlists")
    assert playlists.read_paths("Default") == [t.location for t in tracks]


def test_upgrade_keeps_the_old_session_playlist(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"")
    store.directory.mkdir(parents=True, exist_ok=True)
    store.session_playlist.write_text(f"#EXTM3U\n{song}\n")  # as older versions saved it
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: len(application.playlist) == 1)
    assert application.playlists.active == "Default"
    assert application.playlist[0].location == song


def test_files_given_on_the_command_line_are_added_to_the_open_playlist(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    song = tmp_path / "cli.mp3"
    song.write_bytes(b"")
    application = build(store)
    cleanup.append(application)
    application.start([song])
    wait_until(lambda: len(application.playlist) == 1 and not application.playlists.loading)
    application.shutdown()
    assert SavedPlaylists(store.directory / "playlists").read_paths("Default") == [song]


def test_shutdown_survives_unwritable_config_dir(
    tmp_path: Path, cleanup: list[Application], caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    application = build(SettingsStore(blocker))
    cleanup.append(application)
    application.shutdown()  # must not raise
    assert "Could not save session" in caplog.text


# -- The media library --------------------------------------------------------------- #


def test_library_menu_and_window(store: SettingsStore, cleanup: list[Application]) -> None:
    application = build(store)
    cleanup.append(application)
    window = application.window
    library = next(a.menu() for a in window.menuBar().actions() if a.text() == "&Library")
    assert isinstance(library, QMenu)
    texts = [a.text() for a in library.actions() if not a.isSeparator()]
    assert texts == [
        "Show &Library",
        "Add &Folder to Library…",
        "Add F&iles to Library…",
        "&Rescan Library",
    ]
    assert library.actions()[0].shortcut().toString() == "Alt+L"
    library.actions()[0].trigger()
    assert window.show_library() is window.show_library()  # one window, reused
    assert window.show_library().isVisible()


def test_playing_from_the_library_leaves_other_playlists_alone(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    mine = Track.from_path(tmp_path / "mine.mp3")
    application.playlist.add([mine])
    tracks = [Track.from_path(tmp_path / f"{n}.mp3") for n in "abc"]

    application.window.play_from_library(tracks, 1)
    assert application.playlists.active == "Library"
    assert list(application.playlist) == tracks
    assert application.playlist.current_index == 1
    saved = SavedPlaylists(store.directory / "playlists")
    assert saved.read_paths("Default") == [mine.location]


def test_adding_from_the_library_to_playlists(
    store: SettingsStore,
    cleanup: list[Application],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    window = application.window
    a, b = (Track.from_path(tmp_path / f"{n}.mp3") for n in "ab")

    assert window.add_to_playlist([a], "Default") == "Added 1 track to Default"
    assert list(application.playlist) == [a]  # the open playlist, in place
    assert window.add_to_playlist([a, b], "default") == "Added 1 track to default (1 already in it)"

    monkeypatch.setattr(QInputDialog, "getText", lambda *_, **kw: (f"{kw['text']} 1", True))
    assert window.add_to_playlist([b], None, "Mezzanine") == "Added 1 track to Mezzanine 1"
    saved = SavedPlaylists(store.directory / "playlists")
    assert saved.read_paths("Mezzanine 1") == [b.location]
    assert application.playlists.active == "Default"  # still open; nothing was switched

    warnings: list[str] = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *_, **kw: ("bad/name", True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    assert window.add_to_playlist([b], None) is None
    assert warnings and "can't" in warnings[0]
    monkeypatch.setattr(QInputDialog, "getText", lambda *_, **kw: ("", False))
    assert window.add_to_playlist([b], None) is None  # cancelled


def test_the_library_lists_the_saved_playlists(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    library = application.window.show_library()
    assert [s.name for s in application.sources] == ["This Computer", "Playlists"]
    playlists = library.sidebar_item(1)
    assert playlists is not None
    wait_until(
        lambda: (
            [playlists.child(i).text(0) for i in range(playlists.childCount())]  # type: ignore[union-attr]
            == ["Default (0)"]
        )
    )
    application.window.add_to_playlist([Track.from_path(tmp_path / "a.mp3")], "Default")
    wait_until(lambda: playlists.childCount() == 1 and playlists.child(0).text(0) == "Default (1)")  # type: ignore[union-attr]


def test_the_library_follows_edits_to_the_open_playlist(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    a, b = (Track(tmp_path / f"{name}.mp3", name) for name in "ab")
    application.window.add_tracks([a, b])
    library = application.window.show_library()
    playlists = library.sidebar_item(1)
    assert playlists is not None
    wait_until(
        lambda: (default := playlists.child(0)) is not None and default.text(0) == "Default (2)"
    )
    default = playlists.child(0)
    assert default is not None
    library.sidebar.setCurrentItem(default)
    wait_until(lambda: [t.title for t in library.model.tracks()] == ["a", "b"])

    application.playlist.remove([0])  # in the player
    wait_until(
        lambda: (default := playlists.child(0)) is not None and default.text(0) == "Default (1)"
    )
    wait_until(lambda: [t.title for t in library.model.tracks()] == ["b"])  # still picked


def drag_onto(library: LibraryWindow, item: QTreeWidgetItem | None, data: QMimeData) -> bool:
    """Drag ``data`` over ``item`` in the library's sidebar and drop it, as a mouse would
    (the drop is added once Qt is done with it); whether the sidebar took it."""
    assert item is not None
    sidebar = library.sidebar
    point = sidebar.visualItemRect(item).center()
    copy, left = Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton
    none = Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(point, copy, data, left, none)
    sidebar.dragEnterEvent(enter)
    move = QDragMoveEvent(point, copy, data, left, none)
    sidebar.dragMoveEvent(move)
    drop = QDropEvent(QPointF(point), copy, data, left, none)
    sidebar.dropEvent(drop)
    QCoreApplication.processEvents()
    return enter.isAccepted() and move.isAccepted() and drop.isAccepted()


def drag_rows(library: LibraryWindow, *rows: int) -> QMimeData:
    """Select ``rows`` in the library's table and start dragging them."""
    model, selection = library.model, library.table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.clearSelection()
    for row in rows:
        selection.select(model.index(row, 0), flags)
    return model.mimeData([model.index(row, 0) for row in rows])


@pytest.mark.filterwarnings("ignore:Function.*is marked as deprecated:DeprecationWarning")
def test_tracks_are_dragged_onto_a_playlist_in_the_library(
    store: SettingsStore,
    cleanup: list[Application],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = build(store)
    cleanup.append(application)
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    library = application.window.show_library()
    playlists = library.sidebar_item(1)
    assert playlists is not None
    wait_until(
        lambda: (default := playlists.child(0)) is not None and default.text(0) == "Default (0)"
    )
    stream = Track(StreamUrl("https://music.example/rest/stream?id=s1"), "Teardrop",
                   artist="Massive Attack", duration_ms=330_000)  # fmt: skip
    local = Track(tmp_path / "angel.mp3", "Angel", artist="Massive Attack")
    library.model.set_tracks([stream, local])

    dragged = drag_rows(library, 0, 1)
    assert dragged.urls() == [QUrl.fromLocalFile(str(local.location))]  # to the player: files
    assert drag_onto(library, playlists.child(0), dragged)  # onto "Default"
    assert list(application.playlist) == [stream, local]  # with their tags, streams too

    monkeypatch.setattr(QInputDialog, "getText", lambda *_, **kw: ("Mix", True))
    assert drag_onto(library, playlists, drag_rows(library, 1))  # onto Playlists: a new one
    assert "Mix" in application.playlists.names

    before = list(application.playlist)
    assert not drag_onto(library, library.sidebar_item(0), drag_rows(library, 0))  # This Computer
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile(str(tmp_path / "other.mp3"))])  # from Finder
    assert not drag_onto(library, playlists.child(0), files)
    assert list(application.playlist) == before


def test_the_library_is_scanned_after_start_and_closed_on_quit(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_module, "LIBRARY_SCAN_DELAY_MS", 10)
    application = build(store)
    cleanup.append(application)
    finished: list[object] = []
    application.library.finished.connect(finished.append)
    application.start([])
    assert finished == []  # not while starting up
    wait_until(lambda: bool(finished))
    application.shutdown()
    with pytest.raises(sqlite3.ProgrammingError):
        application.catalog.sources()  # closed
