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

"""Main window: composes the widgets and wires user intents to services."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pyamp import APP_NAME
from pyamp.config import Settings
from pyamp.core.events import EventBus, PlaybackError, PlaylistChanged, TrackChanged
from pyamp.core.models import Track, format_duration
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.library.metadata import AUDIO_EXTENSIONS
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.ui.actions import PlayerActions, create_player_actions
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob
from pyamp.ui.equalizer_window import EqualizerWindow
from pyamp.ui.playlist_model import PlaylistModel
from pyamp.ui.skin import Skin
from pyamp.ui.widgets.lcd import LcdPanel
from pyamp.ui.widgets.playlist_view import PlaylistView
from pyamp.ui.widgets.sliders import SeekSlider, VolumeSlider

WINDOW_WIDTH = 460
STATUS_TIMEOUT_MS = 5000


@dataclass(frozen=True, slots=True)
class UiServices:
    """Everything the window depends on, injected by the composition root."""

    bus: EventBus
    player: Player
    playlist: Playlist
    loader: BackgroundTrackLoader
    playlist_formats: PlaylistFormatRegistry
    settings: Settings
    spectrum_bands: int


class MainWindow(QMainWindow):
    def __init__(self, services: UiServices, skin: Skin) -> None:
        super().__init__()
        self._s = services
        self._skin = skin
        self._equalizer_window: EqualizerWindow | None = None
        self.setWindowTitle(APP_NAME)
        self.setStyleSheet(skin.stylesheet())
        self.setAcceptDrops(True)
        self.resize(WINDOW_WIDTH, 520)
        self.setMinimumWidth(WINDOW_WIDTH)

        self._actions = create_player_actions(self, services.player, services.bus)
        self._lcd = LcdPanel(
            services.bus,
            skin,
            services.spectrum_bands,
            show_remaining=services.settings.show_remaining_time,
        )
        self._seek = SeekSlider(services.bus)
        self._seek.seek_requested.connect(services.player.seek)
        self._volume = VolumeSlider(services.bus, services.player.volume)
        self._volume.volume_requested.connect(services.player.set_volume)
        self._playlist_view = PlaylistView(PlaylistModel(services.playlist, services.bus, skin))
        self._playlist_view.track_activated.connect(services.player.play_index)
        self._playlist_view.remove_requested.connect(services.playlist.remove)

        self._autoplay_jobs: set[LoadJob] = set()
        services.loader.tracks_loaded.connect(self._on_tracks_loaded)
        services.loader.job_finished.connect(self._on_load_finished)

        self._build_layout()
        self._build_menus()

        services.bus.subscribe(TrackChanged, self._on_track_changed)
        services.bus.subscribe(PlaylistChanged, lambda _: self._update_summary())
        services.bus.subscribe(
            PlaybackError,
            lambda e: self.statusBar().showMessage(f"Error: {e.message}", STATUS_TIMEOUT_MS),
        )
        self._update_summary()

    # -- Construction ------------------------------------------------------- #

    def _build_layout(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        layout.addWidget(self._lcd)
        layout.addWidget(self._seek)
        layout.addLayout(self._build_transport(self._actions))
        layout.addWidget(self._playlist_view, stretch=1)
        self.setCentralWidget(central)

    def _build_transport(self, actions: PlayerActions) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        for action in (*actions.transport, None, actions.shuffle, actions.repeat):
            if action is None:
                row.addSpacing(8)
                continue
            button = QToolButton()
            button.setDefaultAction(action)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(button)
        eject = QToolButton()
        eject.setText("⏏")
        eject.setToolTip("Add files (Ctrl+O)")
        eject.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        eject.clicked.connect(self._add_files)
        row.addWidget(eject)
        row.addStretch(1)
        row.addWidget(self._volume)
        return row

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self._add_menu_action(
            file_menu, "Add &Files…", self._add_files, QKeySequence.StandardKey.Open
        )
        self._add_menu_action(file_menu, "Add F&older…", self._add_folder, "Ctrl+Shift+O")
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "&Open Playlist…", self._open_playlist, "Ctrl+L")
        self._add_menu_action(
            file_menu, "&Save Playlist As…", self._save_playlist, QKeySequence.StandardKey.Save
        )
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        play_menu = menu_bar.addMenu("&Playback")
        play_menu.addActions(list(self._actions.transport))
        play_menu.addSeparator()
        play_menu.addActions([self._actions.seek_back, self._actions.seek_forward])
        play_menu.addSeparator()
        play_menu.addActions([self._actions.shuffle, self._actions.repeat])
        replay_gain_menu = play_menu.addMenu("Replay&Gain")
        replay_gain_menu.addActions(list(self._actions.replay_gain.values()))
        self._add_menu_action(play_menu, "&Equaliser…", self.show_equalizer, "G")

        list_menu = menu_bar.addMenu("P&laylist")
        self._add_menu_action(list_menu, "&Remove Selected", self._remove_selected)
        self._add_menu_action(list_menu, "&Clear", self._clear_playlist)

    def show_equalizer(self) -> EqualizerWindow:
        """Open the equaliser window (one instance, reused)."""
        if self._equalizer_window is None:
            self._equalizer_window = EqualizerWindow(
                self._s.player, self._s.bus, self._skin, parent=self
            )
        self._equalizer_window.show()
        self._equalizer_window.raise_()
        self._equalizer_window.activateWindow()
        return self._equalizer_window

    def _add_menu_action(
        self,
        menu: QMenu,
        text: str,
        slot: Callable[[], object],
        shortcut: QKeySequence.StandardKey | str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # -- User intents ------------------------------------------------------- #

    def add_paths(self, paths: Iterable[Path], *, autoplay: bool | None = None) -> LoadJob:
        """Queue files/folders/playlists for loading in the background. Returns immediately.

        ``autoplay=None`` means "start playing if there is nothing to play yet".
        """
        if autoplay is None:
            autoplay = len(self._s.playlist) == 0 and not self._s.loader.busy
        job = self._s.loader.submit(paths)
        if autoplay:
            self._autoplay_jobs.add(job)
        self._update_summary()
        return job

    def _add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add Files", self._s.settings.last_directory, f"Audio files ({patterns})"
        )
        self._add_from_dialog([Path(f) for f in files])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add Folder", self._s.settings.last_directory
        )
        self._add_from_dialog([Path(folder)] if folder else [])

    def _open_playlist(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Open Playlist",
            self._s.settings.last_directory,
            self._s.playlist_formats.file_dialog_filter(),
        )
        if file:
            self._s.player.stop()
            self._clear_playlist()
            self._add_from_dialog([Path(file)])

    def _save_playlist(self) -> None:
        file, _ = QFileDialog.getSaveFileName(
            self,
            "Save Playlist",
            self._s.settings.last_directory,
            self._s.playlist_formats.file_dialog_filter(),
        )
        if not file:
            return
        path = Path(file)
        if not path.suffix:
            path = path.with_suffix(".m3u8")
        try:
            self._s.playlist_formats.write(path, list(self._s.playlist))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Save Playlist", f"Could not save playlist:\n{exc}")
            return
        self.statusBar().showMessage(f"Saved {path.name}", STATUS_TIMEOUT_MS)

    def _add_from_dialog(self, paths: list[Path]) -> None:
        if paths:
            first = paths[0]
            self._s.settings.last_directory = str(first if first.is_dir() else first.parent)
            self.add_paths(paths)

    def _clear_playlist(self) -> None:
        # Cancel in-flight loads first, or their remaining batches would refill the list.
        self._s.loader.cancel_all()
        self._autoplay_jobs.clear()
        self._s.playlist.clear()
        self._update_summary()

    def _remove_selected(self) -> None:
        self._s.playlist.remove(self._playlist_view.selected_rows())

    # -- Event handlers ----------------------------------------------------- #

    def _on_track_changed(self, event: TrackChanged) -> None:
        if event.track is None or event.index is None:
            self.setWindowTitle(APP_NAME)
        else:
            self.setWindowTitle(f"{event.index + 1}. {event.track.display_title} - {APP_NAME}")
            self._playlist_view.scrollTo(self._playlist_view.model().index(event.index, 0))

    def _on_tracks_loaded(self, job: LoadJob, tracks: list[Track]) -> None:
        first_new = len(self._s.playlist)
        self._s.playlist.add(tracks)
        if job in self._autoplay_jobs:  # start on the first batch, don't wait for the rest
            self._autoplay_jobs.discard(job)
            self._s.player.play_index(first_new)

    def _on_load_finished(self, job: LoadJob) -> None:
        self._autoplay_jobs.discard(job)
        if job.loaded == 0:
            self.statusBar().showMessage("No playable files found", STATUS_TIMEOUT_MS)
        else:
            self._update_summary()

    def _update_summary(self) -> None:
        tracks = list(self._s.playlist)
        total = sum(t.duration_ms for t in tracks)
        loading = " · loading…" if self._s.loader.busy else ""
        self.statusBar().showMessage(f"{len(tracks)} tracks · {format_duration(total)}{loading}")

    # -- Qt overrides ------------------------------------------------------- #

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._s.settings.show_remaining_time = self._lcd.show_remaining
        super().closeEvent(event)
