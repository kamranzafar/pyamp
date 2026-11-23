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
"""The menu bar / system tray icon and its menu."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from pyamp.app import Application
from pyamp.audio import registry
from pyamp.config import Settings, SettingsStore
from pyamp.core.events import EventBus, SavedPlaylistsChanged, TrackChanged
from pyamp.core.models import PlaybackState, RepeatMode, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.ui import main_window, menu_bar
from pyamp.ui.menu_bar import MenuBarIcon
from tests.conftest import FakeBackend, make_tracks
from tests.test_background_loader import wait_until


class Calls:
    def __init__(self) -> None:
        self.switched: list[str] = []
        self.shown = 0
        self.quit = 0


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def icon(qt_app: QCoreApplication, bus: EventBus, player: Player, calls: Calls) -> MenuBarIcon:
    def show() -> None:
        calls.shown += 1

    def quit_app() -> None:
        calls.quit += 1

    return MenuBarIcon(
        bus, player, switch_playlist=calls.switched.append, show_window=show, quit_app=quit_app
    )


def test_shows_what_is_playing(icon: MenuBarIcon, bus: EventBus) -> None:
    assert icon.title.text() == "Not playing"
    assert not icon.subtitle.isVisible()
    track = Track(Path("/m/a.mp3"), "Teardrop", artist="Massive Attack", album="Mezzanine")
    bus.publish(TrackChanged(track, 0))
    assert icon.title.text() == "Teardrop"
    assert icon.subtitle.text() == "Massive Attack — Mezzanine"
    assert icon.subtitle.isVisible()
    assert icon.icon.toolTip() == "Massive Attack - Teardrop - PyAmp"
    bus.publish(TrackChanged(Track.from_path(Path("/m/untagged.mp3")), 1))
    assert icon.title.text() == "untagged"
    assert not icon.subtitle.isVisible()
    bus.publish(TrackChanged(None, None))
    assert icon.title.text() == "Not playing"
    assert icon.icon.toolTip() == "PyAmp"


def test_long_titles_are_shortened(icon: MenuBarIcon, bus: EventBus) -> None:
    bus.publish(TrackChanged(Track(Path("/m/x.mp3"), "x" * 200), 0))
    assert len(icon.title.text()) <= 48
    assert icon.title.text().endswith("…")


def test_play_pause_follows_and_controls_playback(
    icon: MenuBarIcon, player: Player, playlist: Playlist
) -> None:
    playlist.add(make_tracks(3))
    assert icon.play_pause.text() == "Play"
    assert not icon.stop.isEnabled()
    icon.play_pause.trigger()
    assert player.state is PlaybackState.PLAYING
    assert icon.play_pause.text() == "Pause"
    assert icon.stop.isEnabled()
    icon.next.trigger()
    assert playlist.current_index == 1
    icon.previous.trigger()
    assert playlist.current_index == 0
    icon.play_pause.trigger()
    assert player.state is PlaybackState.PAUSED
    assert icon.play_pause.text() == "Play"
    icon.stop.trigger()
    assert player.state is PlaybackState.STOPPED


def test_shuffle_and_repeat(icon: MenuBarIcon, player: Player) -> None:
    icon.shuffle.trigger()
    assert player.shuffle
    assert icon.shuffle.isChecked()
    player.set_shuffle(False)  # changed elsewhere: the menu follows
    assert not icon.shuffle.isChecked()
    assert icon.repeat.text() == "Repeat: Off"
    icon.repeat.trigger()
    assert player.repeat is RepeatMode.ALL
    assert icon.repeat.text() == "Repeat: All"


def test_playlists_submenu_switches(icon: MenuBarIcon, bus: EventBus, calls: Calls) -> None:
    bus.publish(SavedPlaylistsChanged(("Default", "Morning"), "Default"))
    actions = icon.playlists.actions()
    assert [a.text() for a in actions] == ["Default", "Morning"]
    assert [a.isChecked() for a in actions] == [True, False]
    actions[1].trigger()
    assert calls.switched == ["Morning"]
    bus.publish(SavedPlaylistsChanged(("Default", "Evening", "Morning"), "Morning"))
    assert [a.text() for a in icon.playlists.actions()] == ["Default", "Evening", "Morning"]
    assert [a.isChecked() for a in icon.playlists.actions()] == [False, False, True]


def test_show_and_quit(icon: MenuBarIcon, calls: Calls) -> None:
    by_text = {a.text(): a for a in icon.menu.actions()}
    by_text["Show PyAmp"].trigger()
    by_text["Quit PyAmp"].trigger()
    assert (calls.shown, calls.quit) == (1, 1)


def test_click_shows_window_except_on_macos(
    icon: MenuBarIcon, calls: Calls, monkeypatch: pytest.MonkeyPatch
) -> None:
    trigger = icon.icon.ActivationReason.Trigger
    monkeypatch.setattr(menu_bar, "IS_MACOS", False)
    icon.icon.activated.emit(trigger)
    assert calls.shown == 1
    monkeypatch.setattr(menu_bar, "IS_MACOS", True)  # there, a click opens the menu
    icon.icon.activated.emit(trigger)
    assert calls.shown == 1


# -- Through the real window ------------------------------------------------------------ #


@pytest.fixture
def app(
    qt_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Application]:
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")
    monkeypatch.setattr(MenuBarIcon, "available", staticmethod(lambda: True))
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
    application = Application(
        args, SettingsStore(tmp_path / "config"), SavedPlaylists(tmp_path / "playlists")
    )
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    yield application
    application.loader.shutdown()
    application.window._quitting = True
    application.window.close()


@pytest.fixture(params=[True, False], ids=["macos", "other"])
def on_macos(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    monkeypatch.setattr(main_window, "IS_MACOS", request.param)
    return bool(request.param)


def test_icon_is_shown_and_can_be_hidden(app: Application, on_macos: bool) -> None:
    window = app.window
    assert window.menu_bar_icon.visible
    window.show_icon_action.trigger()
    assert not window.menu_bar_icon.visible
    assert app.settings.show_in_menu_bar is False
    # Without the icon, only macOS (the Dock) has a way back to a hidden window.
    assert window.keep_running_action.isEnabled() is on_macos
    assert window.keeps_running_when_closed is on_macos


def test_closing_hides_the_window_by_default(app: Application, on_macos: bool) -> None:
    window = app.window
    assert app.settings.keep_running_when_closed
    window.close()
    assert not window.isVisible()
    assert window.menu_bar_icon.visible  # still running, still playing
    window.show_and_raise()
    assert window.isVisible()


def test_closing_can_quit_instead(app: Application) -> None:
    window = app.window
    window.keep_running_action.trigger()
    assert app.settings.keep_running_when_closed is False
    assert not window.keeps_running_when_closed


def test_dock_click_brings_a_closed_window_back(app: Application) -> None:
    window = app.window
    active = Qt.ApplicationState.ApplicationActive
    window.close()
    window._on_app_state(Qt.ApplicationState.ApplicationInactive)
    assert not window.isVisible()
    window._on_app_state(active)  # what macOS reports for a Dock icon click
    assert window.isVisible()
    window.hide()  # hidden some other way: activation doesn't force it open
    window._on_app_state(active)
    assert not window.isVisible()


def test_quit_quits_even_when_closing_would_hide(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    quits: list[bool] = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quits.append(True)))
    app.window.quit()
    assert quits == [True]
    assert not app.window.isVisible()
    assert not app.window.menu_bar_icon.visible
