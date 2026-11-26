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

"""Main window: composes the widgets and wires user intents to services."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QHideEvent,
    QKeySequence,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pyamp import APP_NAME
from pyamp.config import Settings
from pyamp.core.events import (
    EventBus,
    PlaybackError,
    PlaylistChanged,
    StreamTitleChanged,
    TrackChanged,
)
from pyamp.core.models import Location, StreamUrl, Track, format_duration, parse_location
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist, SortKey
from pyamp.library.metadata import AUDIO_EXTENSIONS
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.library.saved_playlists import PlaylistNameError
from pyamp.library.sources import MediaSources
from pyamp.ui.about_dialog import AboutDialog
from pyamp.ui.actions import PlayerActions, create_player_actions
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob
from pyamp.ui.equalizer_window import EqualizerWindow
from pyamp.ui.jump_dialog import JumpDialog
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.library_window import LibraryWindow
from pyamp.ui.menu_bar import IS_MACOS, PLACE, MenuBarIcon
from pyamp.ui.playlist_model import PlaylistModel, dropped_locations
from pyamp.ui.playlist_session import PlaylistSession
from pyamp.ui.skin import Skin
from pyamp.ui.track_drag import dragged_tracks
from pyamp.ui.widgets.lcd import LcdPanel
from pyamp.ui.widgets.playlist_selector import PlaylistSelector
from pyamp.ui.widgets.playlist_view import PlaylistView
from pyamp.ui.widgets.sliders import SeekSlider, VolumeSlider

WINDOW_WIDTH = 460
STATUS_TIMEOUT_MS = 5000
#: The saved playlist the library plays into, so it never changes the user's others.
LIBRARY_PLAYLIST = "Library"
LIBRARY_MENU = "&Library"  # plugins with a library (Subsonic) add to it too

#: How the Playback → Audio Backend menu names backends (others by their own name).
BACKEND_LABELS = {"qt": "Qt Multimedia", "mpv": "mpv"}

SORT_OPTIONS = (
    ("&Title", SortKey.TITLE),
    ("&Artist", SortKey.ARTIST),
    ("Al&bum", SortKey.ALBUM),
    ("&File Name", SortKey.FILENAME),
    ("&Duration", SortKey.DURATION),
)


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
    playlists: PlaylistSession
    library: BackgroundLibraryScanner
    sources: MediaSources
    #: Audio backends that can be chosen (Playback → Audio Backend), e.g. ("mpv", "qt").
    backends: tuple[str, ...] = ()


class MainWindow(QMainWindow):
    #: Whether the window can be seen (shown and not minimised); the visualiser pauses
    #: its work while it can't.
    visibility_changed = Signal(bool)

    def __init__(self, services: UiServices, skin: Skin) -> None:
        super().__init__()
        self._seen = False
        self._s = services
        self._skin = skin
        self._equalizer_window: EqualizerWindow | None = None
        self._library_window: LibraryWindow | None = None
        self._jump_dialog: JumpDialog | None = None
        self._about_dialog: AboutDialog | None = None
        self._plugin_menus: dict[str, QMenu] = {}
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
        model = PlaylistModel(services.playlist, services.bus, skin)
        model.files_dropped.connect(self.add_paths)
        model.tracks_dropped.connect(self.add_tracks)
        self._playlist_view = PlaylistView(model)
        self._playlist_view.track_activated.connect(services.player.play_index)
        self._playlist_view.remove_requested.connect(services.playlist.remove)
        self._playlist_view.customContextMenuRequested.connect(self._show_context_menu)
        self._selector = PlaylistSelector(services.bus)
        self._selector.switch_requested.connect(self.switch_playlist)
        self._selector.new_requested.connect(self.new_playlist)

        self._autoplay_jobs: set[LoadJob] = set()
        services.loader.tracks_loaded.connect(self._on_tracks_loaded)
        services.loader.job_finished.connect(self._on_load_finished)

        self._quitting = False
        self._window_track_label = ""
        self._hidden_by_close = False
        app = QApplication.instance()
        if isinstance(app, QApplication):
            # A Dock icon click while hidden brings the window back.
            app.applicationStateChanged.connect(self._on_app_state)
        self.menu_bar_icon = MenuBarIcon(
            services.bus,
            services.player,
            switch_playlist=self.switch_playlist,
            show_window=self.show_and_raise,
            quit_app=self.quit,
            parent=self,
        )
        self.menu_bar_icon.set_visible(services.settings.show_in_menu_bar)

        self._build_layout()
        self._build_menus()

        services.bus.subscribe(TrackChanged, self._on_track_changed)
        services.bus.subscribe(StreamTitleChanged, self._on_stream_title)
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
        layout.addWidget(self._selector)
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
        self._add_menu_action(file_menu, "Add &URL…", self.add_url, "Ctrl+U")
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "&Import Playlist…", self._import_playlist, "Ctrl+L")
        self._add_menu_action(
            file_menu, "&Export Playlist…", self._export_playlist, QKeySequence.StandardKey.Save
        )
        file_menu.addSeparator()
        self._add_menu_action(file_menu, "&Quit", self.quit, QKeySequence.StandardKey.Quit)

        play_menu = menu_bar.addMenu("&Playback")
        play_menu.addActions(list(self._actions.transport))
        play_menu.addSeparator()
        play_menu.addActions([self._actions.seek_back, self._actions.seek_forward])
        play_menu.addSeparator()
        play_menu.addActions([self._actions.shuffle, self._actions.repeat])
        replay_gain_menu = play_menu.addMenu("Replay&Gain")
        replay_gain_menu.addActions(list(self._actions.replay_gain.values()))
        self._add_menu_action(play_menu, "&Equaliser…", self.show_equalizer, "G")
        self._build_backend_menu(play_menu)

        list_menu = menu_bar.addMenu("P&laylist")
        self._add_menu_action(list_menu, "&New Playlist…", self.new_playlist, "Ctrl+N")
        self._add_menu_action(list_menu, "Re&name Playlist…", self.rename_playlist, "F2")
        self._add_menu_action(list_menu, "&Delete Playlist…", self.delete_playlist)
        list_menu.addSeparator()
        self._sort_menu = list_menu.addMenu("&Sort by")
        for label, key in SORT_OPTIONS:
            self._add_menu_action(self._sort_menu, label, self._sorter(key))
        self._sort_menu.addSeparator()
        self._add_menu_action(self._sort_menu, "&Reverse Order", self._s.playlist.reverse)
        self._dedupe_action = self._add_menu_action(
            list_menu, "Remove D&uplicates", self.remove_duplicates
        )
        list_menu.addSeparator()
        self._jump_action = self._add_menu_action(
            list_menu, "&Jump to Track…", self.show_jump_dialog, "J"
        )
        self._remove_action = self._add_menu_action(
            list_menu, "&Remove Selected", self._remove_selected
        )
        self._add_menu_action(list_menu, "&Clear", self._clear_playlist)

        self._build_library_menu(menu_bar.addMenu(LIBRARY_MENU))

        self._build_view_menu(menu_bar.addMenu("&View"))

        help_menu = self._help_menu = menu_bar.addMenu("&Help")
        self.about_action = self._add_menu_action(help_menu, f"&About {APP_NAME}", self.show_about)
        # macOS shows it as "About PyAmp" in the application menu, where users look for it.
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)

    def _build_library_menu(self, menu: QMenu) -> None:
        self._plugin_menus[LIBRARY_MENU] = menu
        self._add_menu_action(menu, "Show &Library", self.show_library, "Alt+L")
        menu.addSeparator()
        self._add_menu_action(
            menu, "Add &Folder to Library…", lambda: self.show_library().add_folder()
        )
        self._add_menu_action(
            menu, "Add F&iles to Library…", lambda: self.show_library().add_files()
        )
        self._add_menu_action(menu, "&Rescan Library", self._s.library.scan)
        menu.addSeparator()

    # -- PluginUi ----------------------------------------------------------- #

    def add_menu_action(
        self,
        menu: str,
        text: str,
        triggered: Callable[[], object],
        shortcut: str | None = None,
    ) -> None:
        """Add a plugin's menu item, to its own menu just before Help."""
        target = self._plugin_menus.get(menu)
        if target is None:
            target = QMenu(menu, self)
            self.menuBar().insertMenu(self._help_menu.menuAction(), target)
            self._plugin_menus[menu] = target
        self._add_menu_action(target, text, triggered, shortcut)

    def add_menu_separator(self, menu: str) -> None:
        target = self._plugin_menus.get(menu)
        if target is None:
            return  # a menu not made yet: its first item needs nothing above it
        actions = target.actions()
        if actions and not actions[-1].isSeparator():
            target.addSeparator()

    def dialog_parent(self) -> QWidget:
        return self

    def _front(self) -> QWidget:
        """Where to show a dialog: the window in front (e.g. the library), else this one."""
        return QApplication.activeWindow() or self

    def show_about(self) -> AboutDialog:
        if self._about_dialog is None:
            self._about_dialog = AboutDialog(self._skin, parent=self)
        self._about_dialog.show()
        self._about_dialog.raise_()
        self._about_dialog.activateWindow()
        return self._about_dialog

    def _build_view_menu(self, view_menu: QMenu) -> None:
        self.show_icon_action = QAction(f"Show Icon in {PLACE}", self)
        self.show_icon_action.setCheckable(True)
        self.show_icon_action.setChecked(self.menu_bar_icon.visible)
        self.show_icon_action.toggled.connect(self._set_menu_bar_icon)
        self.keep_running_action = QAction(
            "Keep Running When Window Is Closed"
            if IS_MACOS
            else f"Keep Running in {PLACE} When Closed",
            self,
        )
        self.keep_running_action.setCheckable(True)
        self.keep_running_action.setChecked(self._s.settings.keep_running_when_closed)
        self.keep_running_action.toggled.connect(self._set_keep_running)
        view_menu.addActions([self.show_icon_action, self.keep_running_action])
        if not MenuBarIcon.available():
            self.show_icon_action.setEnabled(False)
            self.show_icon_action.setToolTip(f"This desktop has no {PLACE.lower()}")
        self._sync_view_menu()

    def _build_backend_menu(self, parent: QMenu) -> None:
        """Choose the audio backend; it's set up as PyAmp starts, so it applies then."""
        self.backend_menu = parent.addMenu("Audio &Backend")
        group = QActionGroup(self)
        in_use = self._s.player.backend_name
        self.backend_actions: dict[str, QAction] = {}
        for name in self._s.backends:
            label = BACKEND_LABELS.get(name, name)
            action = QAction(f"{label} (in use)" if name == in_use else label, group)
            action.setCheckable(True)
            action.setChecked(name == self._s.settings.backend)
            action.triggered.connect(lambda _=False, name=name: self._choose_backend(name))
            self.backend_menu.addAction(action)
            self.backend_actions[name] = action
        self.backend_menu.addSeparator()
        note = self.backend_menu.addAction("Takes effect the next time PyAmp starts")
        note.setEnabled(False)

    def _choose_backend(self, name: str) -> None:
        if name == self._s.settings.backend:
            return
        self._s.settings.backend = name  # saved on quit, used on the next start
        label = BACKEND_LABELS.get(name, name)
        if name == self._s.player.backend_name:
            message = f"PyAmp will keep using {label}"
        else:
            message = f"PyAmp will use {label} the next time it starts"
        self.statusBar().showMessage(message, STATUS_TIMEOUT_MS)

    def _set_menu_bar_icon(self, shown: bool) -> None:
        self._s.settings.show_in_menu_bar = shown
        self.menu_bar_icon.set_visible(shown)
        self._sync_view_menu()

    def _set_keep_running(self, keep: bool) -> None:
        self._s.settings.keep_running_when_closed = keep

    def _sync_view_menu(self) -> None:
        # Elsewhere the tray icon is the only way back to a hidden window; macOS has the Dock.
        self.keep_running_action.setEnabled(IS_MACOS or self.menu_bar_icon.visible)

    def _sorter(self, key: SortKey) -> Callable[[], None]:
        # A closure without parameters: Qt passes "checked" to slots that accept one.
        def sort() -> None:
            self._s.playlist.sort(key)

        return sort

    def _context_menu(self) -> QMenu:
        menu = QMenu(self)
        rows = self._playlist_view.selected_rows()
        play = menu.addAction("&Play")
        play.setEnabled(bool(rows))
        play.triggered.connect(lambda: self._s.player.play_index(rows[0]))
        menu.addAction(self._remove_action)
        menu.addSeparator()
        menu.addMenu(self._sort_menu)
        menu.addAction(self._dedupe_action)
        menu.addAction(self._jump_action)
        return menu

    def _show_context_menu(self, pos: QPoint) -> None:
        self._context_menu().exec(self._playlist_view.viewport().mapToGlobal(pos))

    # -- Saved playlists ------------------------------------------------------ #

    def switch_playlist(self, name: str) -> None:
        self._autoplay_jobs.clear()
        self._s.playlists.switch(name)

    def new_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "New Playlist", "Name:", text="New playlist")
        if ok:
            self._autoplay_jobs.clear()
            self._try(lambda: self._s.playlists.new(name), "New Playlist")

    def rename_playlist(self) -> None:
        """Rename the open playlist (a slot: takes no arguments)."""
        self.rename_saved_playlist(self._s.playlists.active or "")

    def delete_playlist(self) -> None:
        """Delete the open playlist (a slot: takes no arguments)."""
        self.delete_saved_playlist(self._s.playlists.active or "")

    def rename_saved_playlist(self, current: str) -> None:
        name, ok = QInputDialog.getText(self._front(), "Rename Playlist", "Name:", text=current)
        if ok and name.strip() != current:
            self._try(lambda: self._s.playlists.rename(name, current), "Rename Playlist")

    def delete_saved_playlist(self, name: str) -> None:
        answer = QMessageBox.question(
            self._front(),
            "Delete Playlist",
            f'Delete the playlist "{name}"?\n\nOnly the playlist is deleted; '
            "the music files stay where they are.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            if name.casefold() == (self._s.playlists.active or "").casefold():
                self._autoplay_jobs.clear()
            self._s.playlists.delete(name)

    def remove_duplicates(self) -> None:
        removed = self._s.playlist.remove_duplicates()
        noun = "duplicate" if removed == 1 else "duplicates"
        self.statusBar().showMessage(f"Removed {removed} {noun}", STATUS_TIMEOUT_MS)

    def show_jump_dialog(self) -> JumpDialog:
        dialog = JumpDialog(list(self._s.playlist), self._skin, parent=self)
        dialog.jump_requested.connect(self._s.player.play_index)
        dialog.open()
        self._jump_dialog = dialog
        return dialog

    def _try(self, action: Callable[[], object], title: str) -> None:
        try:
            action()
        except PlaylistNameError as exc:
            QMessageBox.warning(self, title, str(exc))

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

    def show_library(self) -> LibraryWindow:
        """Open the library window (one instance, reused)."""
        if self._library_window is None:
            self._library_window = LibraryWindow(
                self._s.library,
                self._s.sources,
                self._s.bus,
                self._s.settings,
                self._skin,
                play_tracks=self.play_from_library,
                add_to_playlist=self.add_to_playlist,
                playlist_names=lambda: self._s.playlists.names,
                parent=self,
            )
        self._library_window.show()
        self._library_window.raise_()
        self._library_window.activateWindow()
        return self._library_window

    def play_from_library(self, tracks: Sequence[Track], start: int) -> None:
        """Play ``tracks`` from ``start``, in the "Library" playlist."""
        self._autoplay_jobs.clear()
        self._s.playlists.open_tracks(LIBRARY_PLAYLIST, tracks)
        self._s.player.play_index(start)

    def add_to_playlist(
        self, tracks: Sequence[Track], name: str | None, suggested: str = ""
    ) -> str | None:
        """Add ``tracks`` to the saved playlist ``name``, or to a new one (None: ask for its
        name, suggesting ``suggested``). Returns what happened, for the user."""
        if name is None:
            text, ok = QInputDialog.getText(
                self._front(), "New Playlist", "Name:", text=suggested or "New playlist"
            )
            if not ok:
                return None
            try:
                name = self._s.playlists.create(text)
            except PlaylistNameError as exc:
                QMessageBox.warning(self._front(), "New Playlist", str(exc))
                return None
        added = self._s.playlists.add_to(name, tracks)
        noun = "track" if added == 1 else "tracks"
        message = f"Added {added} {noun} to {name}"
        if skipped := len(tracks) - added:
            message += f" ({skipped} already in it)"
        self.statusBar().showMessage(message, STATUS_TIMEOUT_MS)
        return message

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

    def add_paths(self, paths: Iterable[Location], *, autoplay: bool | None = None) -> LoadJob:
        """Queue files/folders/playlists/streams for loading in the background.

        Returns immediately.

        ``autoplay=None`` means "start playing if there is nothing to play yet".
        """
        if autoplay is None:
            autoplay = len(self._s.playlist) == 0 and not self._s.loader.busy
        job = self._s.loader.submit(paths)
        if autoplay:
            self._autoplay_jobs.add(job)
        self._update_summary()
        return job

    def add_tracks(self, tracks: Sequence[Track]) -> None:
        """Add tracks PyAmp already knows (e.g. dragged from the library) as they are,
        tags and all, starting the first if there was nothing to play."""
        autoplay = len(self._s.playlist) == 0 and not self._s.loader.busy
        first_new = len(self._s.playlist)
        self._s.playlist.add(tracks)
        if autoplay and tracks:
            self._s.player.play_index(first_new)

    def add_url(self) -> None:
        """Add a stream: internet radio (or a station's .pls/.m3u), or an audio file URL."""
        text, ok = QInputDialog.getText(
            self, "Add URL", "Stream or playlist URL (http:// or https://):"
        )
        if not ok or not text.strip():
            return
        location = parse_location(text)
        if not isinstance(location, StreamUrl):
            QMessageBox.warning(self, "Add URL", "Enter an http:// or https:// address.")
            return
        self.add_paths([location])

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

    def _import_playlist(self) -> None:
        """Import a playlist file as a new saved playlist (named after the file)."""
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Import Playlist",
            self._s.settings.last_directory,
            self._s.playlist_formats.file_dialog_filter(),
        )
        if file:
            path = Path(file)
            self._autoplay_jobs.clear()
            self._try(lambda: self._s.playlists.new(path.stem), "Import Playlist")
            self._add_from_dialog([path])

    def _export_playlist(self) -> None:
        file, _ = QFileDialog.getSaveFileName(
            self,
            "Export Playlist",
            str(
                Path(self._s.settings.last_directory)
                / f"{self._s.playlists.active or 'playlist'}.m3u8"
            ),
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
            QMessageBox.warning(self, "Export Playlist", f"Could not export playlist:\n{exc}")
            return
        self.statusBar().showMessage(f"Exported {path.name}", STATUS_TIMEOUT_MS)

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
            self._window_track_label = f"{event.index + 1}. {event.track.display_title}"
            self.setWindowTitle(f"{self._window_track_label} - {APP_NAME}")
            self._playlist_view.scrollTo(self._playlist_view.model().index(event.index, 0))

    def _on_stream_title(self, event: StreamTitleChanged) -> None:
        self.setWindowTitle(f"{event.title} — {self._window_track_label} - {APP_NAME}")

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
        playlist = self._s.playlist
        total = format_duration(playlist.total_duration_ms)
        loading = " · loading…" if self._s.loader.busy else ""
        self.statusBar().showMessage(f"{len(playlist)} tracks · {total}{loading}")

    # -- Qt overrides ------------------------------------------------------- #

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() or dragged_tracks(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        if tracks := dragged_tracks(event.mimeData()):
            event.acceptProposedAction()
            self.add_tracks(tracks)
            return
        paths = dropped_locations(event.mimeData().urls())
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def show_and_raise(self) -> None:
        self._hidden_by_close = False
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def keeps_running_when_closed(self) -> bool:
        if not self._s.settings.keep_running_when_closed:
            return False
        return IS_MACOS or self.menu_bar_icon.visible

    def quit(self) -> None:
        """Quit PyAmp, even if closing the window would only hide it."""
        self._quitting = True
        self.menu_bar_icon.set_visible(False)
        self.close()
        QApplication.quit()

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        # macOS reports a Dock icon click as the app becoming active, even if it already was.
        if state == Qt.ApplicationState.ApplicationActive and self._hidden_by_close:
            self.show_and_raise()

    @property
    def seen(self) -> bool:
        return self._seen

    def _sync_seen(self) -> None:
        seen = self.isVisible() and not self.isMinimized()
        if seen != self._seen:
            self._seen = seen
            self.visibility_changed.emit(seen)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_seen()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._sync_seen()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_seen()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._s.settings.show_remaining_time = self._lcd.show_remaining
        if self.keeps_running_when_closed and not self._quitting:
            # Music keeps playing; the Dock or menu bar icon brings the window back. The
            # system quitting PyAmp (Dock menu, logout) still works: Qt only refuses to quit
            # while a window is visible, and this one no longer is.
            event.ignore()
            self.hide()
            self._hidden_by_close = True
            return
        super().closeEvent(event)
