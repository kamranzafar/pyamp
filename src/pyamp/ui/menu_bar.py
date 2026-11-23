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
"""PyAmp in the menu bar (macOS) or system tray (Linux, Windows).

The icon's menu shows what's playing and offers the basic controls, so PyAmp can
be driven without its window, which may even be closed (see
``Settings.keep_running_in_menu_bar``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from pyamp import APP_NAME
from pyamp.core.events import (
    EventBus,
    PlaybackStateChanged,
    PlayModeChanged,
    SavedPlaylistsChanged,
    TrackChanged,
)
from pyamp.core.models import PlaybackState, RepeatMode, Track
from pyamp.core.player import Player
from pyamp.ui.icons import app_icon, menubar_icon

IS_MACOS = sys.platform == "darwin"
#: What the menu bar is called on this platform, for menu labels.
PLACE = "Menu Bar" if IS_MACOS else "System Tray"
_REPEAT = {RepeatMode.OFF: "Off", RepeatMode.ALL: "All", RepeatMode.ONE: "One"}
_MAX_LABEL = 48


def _elide(text: str) -> str:
    return text if len(text) <= _MAX_LABEL else text[: _MAX_LABEL - 1].rstrip() + "…"


class MenuBarIcon(QObject):
    def __init__(
        self,
        bus: EventBus,
        player: Player,
        *,
        switch_playlist: Callable[[str], None],
        show_window: Callable[[], None],
        quit_app: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = player
        self._switch_playlist = switch_playlist
        self._show_window = show_window
        self._track: Track | None = None

        self.menu = QMenu()
        self.title = self._label("Not playing")
        self.subtitle = self._label("")
        self.subtitle.setVisible(False)
        self.menu.addSeparator()
        self.play_pause = self._item("Play", self._toggle_play)
        self.next = self._item("Next", player.next)
        self.previous = self._item("Previous", player.previous)
        self.stop = self._item("Stop", player.stop)
        self.menu.addSeparator()
        self.shuffle = self._item("Shuffle", lambda: player.set_shuffle(not player.shuffle))
        self.shuffle.setCheckable(True)
        self.repeat = self._item("Repeat", lambda: player.set_repeat(player.repeat.cycle()))
        self.playlists = self.menu.addMenu("Playlists")
        self._playlist_group = QActionGroup(self)
        self.menu.addSeparator()
        self._item(f"Show {APP_NAME}", show_window)
        self._item(f"Quit {APP_NAME}", quit_app)

        # The template icon suits the macOS menu bar; trays elsewhere get the colour icon.
        self.icon = QSystemTrayIcon(menubar_icon() if IS_MACOS else app_icon(), self)
        self.icon.setContextMenu(self.menu)
        self.icon.setToolTip(APP_NAME)
        self.icon.activated.connect(self._on_activated)

        bus.subscribe(TrackChanged, self._on_track)
        bus.subscribe(PlaybackStateChanged, self._on_state)
        bus.subscribe(PlayModeChanged, self._on_modes)
        bus.subscribe(SavedPlaylistsChanged, self._on_playlists)
        self._on_state(PlaybackStateChanged(player.state))
        self._on_modes(PlayModeChanged(player.shuffle, player.repeat))

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    @property
    def visible(self) -> bool:
        return self.icon.isVisible()

    def set_visible(self, visible: bool) -> None:
        self.icon.setVisible(visible and self.available())

    # -- Menu ------------------------------------------------------------------ #

    def _label(self, text: str) -> QAction:
        action = self.menu.addAction(text)
        action.setEnabled(False)
        return action

    def _item(self, text: str, slot: Callable[[], object]) -> QAction:
        action = self.menu.addAction(text)
        # A closure without parameters: Qt passes "checked" to slots that accept one.
        action.triggered.connect(lambda: slot())  # noqa: PLW0108
        return action

    def _toggle_play(self) -> None:
        if self._player.state is PlaybackState.PLAYING:
            self._player.pause()
        else:
            self._player.play()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # On macOS a click opens the menu; tray icons elsewhere show the window.
        if not IS_MACOS and reason is QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    # -- Events ------------------------------------------------------------------ #

    def _on_track(self, event: TrackChanged) -> None:
        self._track = event.track
        track = event.track
        if track is None:
            self.title.setText("Not playing")
            self.subtitle.setVisible(False)
            self.icon.setToolTip(APP_NAME)
            return
        self.title.setText(_elide(track.title))
        details = " — ".join(part for part in (track.artist, track.album) if part)
        self.subtitle.setText(_elide(details))
        self.subtitle.setVisible(bool(details))
        self.icon.setToolTip(f"{track.display_title} - {APP_NAME}")

    def _on_state(self, event: PlaybackStateChanged) -> None:
        playing = event.state is PlaybackState.PLAYING
        self.play_pause.setText("Pause" if playing else "Play")
        self.stop.setEnabled(event.state is not PlaybackState.STOPPED)

    def _on_modes(self, event: PlayModeChanged) -> None:
        self.shuffle.setChecked(event.shuffle)
        self.repeat.setText(f"Repeat: {_REPEAT[event.repeat]}")

    def _on_playlists(self, event: SavedPlaylistsChanged) -> None:
        self.playlists.clear()
        for action in self._playlist_group.actions():
            self._playlist_group.removeAction(action)
        for name in event.names:
            action = self.playlists.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == event.active)
            action.setActionGroup(self._playlist_group)
            action.triggered.connect(lambda _checked=False, n=name: self._switch_playlist(n))
        self.playlists.setEnabled(bool(event.names))
