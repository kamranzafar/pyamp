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
"""The library window: every media source in one sidebar, their tracks in one table.

The local library comes first, then sources from plugins (a Subsonic server, ...). All
calls to sources run on worker threads (:class:`BackgroundCalls`), so a slow server
never freezes the window; only the latest request for tracks fills the table.

Under the sidebar is the picked track's artwork (or the first one listed), which its
source finds: a file's own picture, or a server's cover.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Hashable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QHideEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPen,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QTableView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pyamp.config import Settings
from pyamp.core.events import EventBus, MediaSourcesChanged
from pyamp.core.models import Track, format_duration
from pyamp.library.metadata import AUDIO_EXTENSIONS
from pyamp.library.playlists_source import PlaylistsSource
from pyamp.library.scanner import ScanSummary
from pyamp.library.sources import MediaSource, MediaSources, Node, SourceError
from pyamp.ui.background_calls import BackgroundCalls
from pyamp.ui.dialog_parts import plain_button
from pyamp.ui.library_model import COLUMNS, LibraryModel
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.skin import Skin
from pyamp.ui.track_drag import dragged_tracks
from pyamp.ui.widgets.artwork_view import ArtworkView

log = logging.getLogger(__name__)

#: The search box waits for typing to pause this long.
SEARCH_DELAY_MS = 200
LOADING = "Loading…"
#: Worker threads per source: enough to list a node while its tracks load.
CALLS_PER_SOURCE = 2
#: The artwork waits for the picked track to settle this long (e.g. arrowing down).
ARTWORK_DELAY_MS = 120
#: Pictures kept, so moving between an album's songs doesn't ask for its cover again.
ARTWORK_CACHE = 64

# Sidebar item data: the source an item belongs to, its node (None for the source's own
# item) and whether what's inside it has been asked for.
_SOURCE = Qt.ItemDataRole.UserRole
_NODE = Qt.ItemDataRole.UserRole + 1
_LOADED = Qt.ItemDataRole.UserRole + 2

type PlayTracks = Callable[[Sequence[Track], int], None]
#: (tracks, playlist name or None for a new one, name to suggest) -> what happened.
type AddToPlaylist = Callable[[Sequence[Track], str | None, str], str | None]
#: Where an item is: its source's position in the sidebar, then node keys from the top.
type _Path = tuple[int, tuple[Hashable, ...]]


class LibraryWindow(QDialog):
    def __init__(
        self,
        library: BackgroundLibraryScanner,
        sources: MediaSources,
        bus: EventBus,
        settings: Settings,
        skin: Skin,
        *,
        play_tracks: PlayTracks,
        add_to_playlist: AddToPlaylist,
        playlist_names: Callable[[], Sequence[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._sources = sources
        self._settings = settings
        self._play_tracks = play_tracks
        self._add_to_playlist = add_to_playlist
        self._playlist_names = playlist_names
        #: Each source's own worker threads, so a slow server only ever delays itself.
        self._calls: dict[int, tuple[MediaSource, BackgroundCalls]] = {}
        #: The threads of sources that have gone, kept until their last call returns.
        self._retired: list[BackgroundCalls] = []
        self._checked = 0
        self._error = ""
        #: Closed after being open: the table is empty, so the library holds no tracks.
        self._closed = False
        self._tracks_request = 0
        #: Bumped when a source's part of the sidebar is rebuilt: late replies are dropped.
        self._generation: dict[int, int] = {}
        self._expanded: set[tuple[int, tuple[Hashable, ...]]] = set()
        self._known: set[int] = set()
        #: An item to select again once the sidebar has loaded down to it.
        self._restore: tuple[int, tuple[Hashable, ...]] | None = None
        #: The source the table's tracks came from (it finds their artwork).
        self._tracks_source: MediaSource | None = None
        self._artwork_request = 0
        self._artwork_cache: OrderedDict[tuple[int, object], bytes | None] = OrderedDict()
        self.setWindowTitle("Library")
        self.setObjectName("central")
        self.setStyleSheet(_extra_style(skin))  # the rest is the application's
        self.resize(900, 560)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.sidebar = _Sidebar(self._drop_target)
        self.sidebar.tracks_dropped.connect(self._drop_tracks)
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setExpandsOnDoubleClick(False)  # a click opens; a second shouldn't close
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.model = LibraryModel(self)
        self.table = _track_table(self.model)
        self.artwork = ArtworkView()
        self.status = QLabel()
        self.add_folder_button = plain_button("Add Folder…")
        self.add_files_button = plain_button("Add Files…")
        self.rescan_button = plain_button("Rescan")
        self._search_delay = QTimer(self)
        self._search_delay.setSingleShot(True)
        self._search_delay.setInterval(SEARCH_DELAY_MS)
        self._artwork_delay = QTimer(self)
        self._artwork_delay.setSingleShot(True)
        self._artwork_delay.setInterval(ARTWORK_DELAY_MS)
        self._layout()
        self._connect(library)
        bus.subscribe(MediaSourcesChanged, self._on_sources_changed)
        self.rebuild()

    def _connect(self, library: BackgroundLibraryScanner) -> None:
        self._search_delay.timeout.connect(self.show_tracks)
        self.search.textChanged.connect(self._search_delay.start)
        self.search.returnPressed.connect(self.show_tracks)
        self.sidebar.currentItemChanged.connect(self._on_picked)
        self.sidebar.itemExpanded.connect(self._on_expanded)
        self.sidebar.itemCollapsed.connect(lambda item: self._expanded.discard(_path(item)))
        self.sidebar.customContextMenuRequested.connect(self._show_sidebar_menu)
        self.table.doubleClicked.connect(lambda index: self.play_from(index.row()))
        self._artwork_delay.timeout.connect(self.show_artwork)
        self.table.selectionModel().currentRowChanged.connect(self._artwork_delay.start)
        self.model.modelReset.connect(self._artwork_delay.start)
        self.table.customContextMenuRequested.connect(self._show_table_menu)
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(self._play_current)
        self.add_folder_button.clicked.connect(self.add_folder)
        self.add_files_button.clicked.connect(self.add_files)
        self.rescan_button.clicked.connect(library.scan)
        library.progress.connect(self._on_progress)
        library.finished.connect(self._on_scan_finished)

    # -- The sidebar --------------------------------------------------------------- #

    def rebuild(self) -> None:
        """Show every source again (e.g. one was added), keeping what was open and picked."""
        self._restore = self._restore or _location(self.sidebar.currentItem())
        self._retire_calls()
        self._artwork_cache.clear()
        self._generation = {i: self._generation.get(i, 0) + 1 for i in range(len(self._sources))}
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self.sidebar.blockSignals(False)
        tops = []
        for position, source in enumerate(self._sources):
            item = QTreeWidgetItem(self.sidebar, [source.name])
            item.setData(0, _SOURCE, source)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            tops.append(item)
            if id(source) not in self._known:  # new sources start open
                self._known.add(id(source))
                self._expanded.add((position, ()))
        for position, item in enumerate(tops):
            if (position, ()) in self._expanded:
                item.setExpanded(True)  # loads its roots
        self._restore_selection()

    def reload(self, source: MediaSource) -> None:
        """Ask ``source`` for everything again (e.g. a scan changed the local library)."""
        position = next((i for i, s in enumerate(self._sources) if s is source), None)
        item = self._top(position) if position is not None else None
        if position is None or item is None:
            return
        self._artwork_cache.clear()  # e.g. a rescan found new covers
        current = _location(self.sidebar.currentItem())
        if current is not None and current[0] == position:
            self._restore = current
        self._generation[position] = self._generation.get(position, 0) + 1
        self.sidebar.blockSignals(True)
        item.takeChildren()
        if self._restore is not None and self._restore[0] == position:
            self.sidebar.setCurrentItem(item)
        self.sidebar.blockSignals(False)
        item.setData(0, _LOADED, False)
        if item.isExpanded():
            self._load(item)
        self._restore_selection()

    def selected(self) -> tuple[MediaSource, Node | None] | None:
        """The source, and the node in it (None: the whole source), picked in the sidebar."""
        item = self.sidebar.currentItem()
        if item is None:
            return None
        node = item.data(0, _NODE)
        return item.data(0, _SOURCE), node if isinstance(node, Node) else None

    def sidebar_item(self, source: int, *keys: Hashable) -> QTreeWidgetItem | None:
        """The loaded sidebar item of the source at ``source``, then nodes by key."""
        item = self._top(source)
        for key in keys:
            if item is None:
                return None
            item = next((c for c in _children(item) if _key(c) == key), None)
        return item

    def _calls_for(self, source: MediaSource) -> BackgroundCalls:
        entry = self._calls.get(id(source))
        if entry is None:
            entry = self._calls[id(source)] = (
                source,
                BackgroundCalls(self, threads=CALLS_PER_SOURCE),
            )
        return entry[1]

    def _retire_calls(self) -> None:
        """Let go of the threads of sources that have gone, without waiting for them."""
        current = {id(source) for source in self._sources}
        for key in [k for k in self._calls if k not in current]:
            _, calls = self._calls.pop(key)
            calls.drop_waiting()
            self._retired.append(calls)
        for calls in [c for c in self._retired if not c.busy]:
            self._retired.remove(calls)
            calls.deleteLater()

    def _top(self, position: int) -> QTreeWidgetItem | None:
        return self.sidebar.topLevelItem(position)

    def _on_sources_changed(self, event: MediaSourcesChanged) -> None:
        if event.source is None:
            self.rebuild()
        else:
            self.reload(event.source)  # type: ignore[arg-type]

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        self._expanded.add(_path(item))
        if not item.data(0, _LOADED):
            self._load(item)

    def _load(self, item: QTreeWidgetItem) -> None:
        source: MediaSource = item.data(0, _SOURCE)
        node = item.data(0, _NODE)
        position = _path(item)[0]
        generation = self._generation.get(position, 0)
        item.setData(0, _LOADED, True)
        placeholder = QTreeWidgetItem(item, [LOADING])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)

        def done(nodes: list[Node] | None, error: BaseException | None) -> None:
            if self._generation.get(position, 0) != generation:
                return  # that part of the sidebar has been rebuilt since
            item.removeChild(placeholder)
            if error is not None or nodes is None:
                item.setData(0, _LOADED, False)  # expanding it again tries again
                self._show_error(error)
                self._give_up_restoring(item)
                return
            self._add_children(item, source, nodes)

        call = source.roots if node is None else (lambda: source.children(node))
        self._calls_for(source).run(call, done)

    def _add_children(self, item: QTreeWidgetItem, source: MediaSource, nodes: list[Node]) -> None:
        for node in nodes:
            child = QTreeWidgetItem(item, [node.label])
            child.setData(0, _SOURCE, source)
            child.setData(0, _NODE, node)
            child.setToolTip(0, node.tooltip)
            if node.expandable:
                child.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        if not nodes and item.data(0, _NODE) is not None:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicator)
        for child in _children(item):
            if _path(child) in self._expanded:
                child.setExpanded(True)  # as it was: loads what's inside it
        self._restore_selection()

    def _restore_selection(self) -> None:
        """Select the remembered item once it's loaded, or the whole source if it's gone."""
        if self._restore is None:
            if self.sidebar.currentItem() is None and (first := self._top(0)) is not None:
                self._pick(first)
            return
        position, keys = self._restore
        top = self._top(position)
        if top is None:  # its source is gone
            self._restore = None
            self._restore_selection()
            return
        item = top
        for key in keys:
            if not item.data(0, _LOADED) or _is_loading(item):
                item.setExpanded(True)  # loads it, if it isn't loading already
                return  # try again when it has loaded
            child = next((c for c in _children(item) if _key(c) == key), None)
            if child is None:  # it's gone: show the whole source instead
                item = top
                break
            item = child
        self._restore = None
        self._pick(item)

    def _give_up_restoring(self, failed: QTreeWidgetItem) -> None:
        """``failed`` couldn't load: stop waiting for an item inside it."""
        position, keys = _path(failed)
        restore = self._restore
        if restore is not None and restore[0] == position and restore[1][: len(keys)] == keys:
            self._restore = None
            if (top := self._top(position)) is not None:
                self._pick(top)

    def _pick(self, item: QTreeWidgetItem) -> None:
        if self.sidebar.currentItem() is item:
            self.show_tracks()  # it was picked quietly while the sidebar was rebuilt
        else:
            self.sidebar.setCurrentItem(item)

    # -- The tracks ---------------------------------------------------------------- #

    def _on_picked(self, item: QTreeWidgetItem | None) -> None:
        """Show what's picked, and open it: clicking an artist shows its albums."""
        node = item.data(0, _NODE) if item is not None else None
        if item is not None and isinstance(node, Node) and node.expandable:
            item.setExpanded(True)
        self.show_tracks()

    def show_tracks(self) -> None:
        """Ask the picked source for the picked node's tracks matching the search."""
        self._search_delay.stop()
        self._tracks_request += 1  # and a reply still on its way is dropped
        request = self._tracks_request
        picked = self.selected()
        if picked is None or self._closed:
            self.model.set_tracks([])
            self._update_status()
            return
        source, node = picked
        if node is not None and not node.has_tracks:  # e.g. Artists: pick one of them
            self._error = ""
            self.model.set_tracks([])
            self.status.setText(f"Pick something in “{node.name}”")
            return
        search = self.search.text()
        self.status.setText(LOADING)

        def done(tracks: list[Track] | None, error: BaseException | None) -> None:
            if request != self._tracks_request:
                return  # the user has picked something else since
            if error is not None or tracks is None:
                self.model.set_tracks([])
                self._show_error(error)
                return
            self._error = ""
            self._tracks_source = source
            self.model.set_tracks(tracks)
            self._update_status()

        self._calls_for(source).run(lambda: source.tracks(node, search), done)

    # -- The artwork --------------------------------------------------------------- #

    def artwork_track(self) -> Track | None:
        """The track whose artwork is shown: the table's current one, else the first."""
        row = self.table.currentIndex().row()
        if 0 <= row < self.model.rowCount():
            return self.model.track(row)
        return self.model.track(0) if self.model.rowCount() else None

    def show_artwork(self) -> None:
        """Show :meth:`artwork_track`'s picture, asking its source for it if need be."""
        self._artwork_delay.stop()
        self._artwork_request += 1  # and a reply still on its way is dropped
        request = self._artwork_request
        track, source = self.artwork_track(), self._tracks_source
        if track is None or source is None:
            self.artwork.show_image(None)
            return
        key = (id(source), track.artwork or track.location)
        if key in self._artwork_cache:
            self._artwork_cache.move_to_end(key)
            self.artwork.show_image(self._artwork_cache[key])
            return

        def done(data: bytes | None, error: BaseException | None) -> None:
            if error is not None:
                log.debug("No artwork for %s", track.location, exc_info=error)
            elif source is self._tracks_source:  # not a source since rebuilt
                self._artwork_cache[key] = data
                while len(self._artwork_cache) > ARTWORK_CACHE:
                    self._artwork_cache.popitem(last=False)
            if request == self._artwork_request:
                self.artwork.show_image(data if error is None else None)

        self._calls_for(source).run(lambda: source.artwork(track), done)

    def _show_error(self, error: BaseException | None) -> None:
        if isinstance(error, SourceError):
            self._error = str(error)
        else:
            log.error("Library source failed", exc_info=error)
            self._error = "Something went wrong; see the log for details"
        self._update_status()

    def _update_status(self) -> None:
        tracks = self.model.tracks()
        picked = self.selected()
        if self._error:
            text = self._error
        elif not tracks and picked is not None and picked[1] is None and not self.search.text():
            text = picked[0].hint
        else:
            total = format_duration(sum(t.duration_ms for t in tracks))
            text = f"{len(tracks):,} tracks · {total}"
        if self._library.busy:
            text += f" · Scanning… {self._checked:,} files checked"
        self.status.setText(text)

    # -- User actions ------------------------------------------------------------ #

    def play_from(self, row: int) -> None:
        """Play the tracks shown, starting with the one in ``row``."""
        if 0 <= row < self.model.rowCount():
            self._play_tracks(self.model.tracks(), row)

    def add_selected(self, playlist: str | None) -> None:
        """Add the selected tracks to ``playlist`` (None: a new one)."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if rows:
            self._add([self.model.track(row) for row in rows], playlist, "")

    def add_node(self, item: QTreeWidgetItem, playlist: str | None) -> None:
        """Add the tracks of a sidebar item (an album, an artist, ...) to ``playlist``."""
        source: MediaSource = item.data(0, _SOURCE)
        node = item.data(0, _NODE)
        if not isinstance(node, Node):
            return
        self.status.setText(LOADING)

        def done(tracks: list[Track] | None, error: BaseException | None) -> None:
            if error is not None or tracks is None:
                self._show_error(error)
            else:
                self._add(tracks, playlist, node.name)

        self._calls_for(source).run(lambda: source.tracks(node), done)

    def _add(self, tracks: Sequence[Track], playlist: str | None, suggested: str) -> None:
        message = self._add_to_playlist(tracks, playlist, suggested)
        self.status.setText(message or "")
        if message is None:
            self._update_status()

    def _drop_target(self, item: QTreeWidgetItem) -> str | None:
        """The saved playlist that tracks dropped on ``item`` go into ("" for a new one,
        dropped on Playlists itself); None if they can't be dropped there."""
        if not isinstance(item.data(0, _SOURCE), PlaylistsSource):
            return None
        node = item.data(0, _NODE)
        return str(node.key) if isinstance(node, Node) else ""

    def _drop_tracks(self, tracks: list[Track], playlist: str) -> None:
        self._add(tracks, playlist or None, "")

    def _playlist_menu(self, parent: QMenu, add: Callable[[str | None], object]) -> QMenu:
        """Add to Playlist ▸ every saved playlist, then New Playlist…"""
        menu = parent.addMenu("&Add to Playlist")
        for name in self._playlist_names():
            menu.addAction(name.replace("&", "&&")).triggered.connect(
                lambda _=False, name=name: add(name)
            )
        menu.addSeparator()
        menu.addAction("&New Playlist…").triggered.connect(lambda: add(None))
        return menu

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add Folder to Library", self._settings.last_directory
        )
        if folder:
            self._add_sources([Path(folder)])

    def add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Files to Library",
            self._settings.last_directory,
            f"Audio files ({patterns})",
        )
        self._add_sources([Path(f) for f in files])

    def _add_sources(self, paths: list[Path]) -> None:
        if not paths:
            return
        first = paths[0]
        self._settings.last_directory = str(first if first.is_dir() else first.parent)
        if not self._library.add_sources(paths):
            self.status.setText("Already in the library")
            return
        self._checked = 0
        self._update_status()

    def _play_current(self) -> None:
        self.play_from(self.table.currentIndex().row())

    def table_menu(self, row: int) -> QMenu:
        """The menu for a right-click on ``row`` (-1: below the tracks)."""
        menu = QMenu(self)
        play = menu.addAction("&Play")
        play.setEnabled(0 <= row < self.model.rowCount())
        play.triggered.connect(lambda: self.play_from(row))
        add = self._playlist_menu(menu, self.add_selected)
        add.setEnabled(self.table.selectionModel().hasSelection())
        return menu

    def sidebar_menu(self, item: QTreeWidgetItem | None) -> QMenu | None:
        """The menu for a right-click in the sidebar: Add to Playlist, if the item has tracks,
        and its source's commands for it (e.g. removing a folder, or on a source's name,
        removing a server)."""
        source: MediaSource | None = item.data(0, _SOURCE) if item is not None else None
        if item is None or source is None:  # nothing, or a "Loading…" placeholder
            return None
        data = item.data(0, _NODE)
        node = data if isinstance(data, Node) else None
        actions = source.actions(node)
        has_tracks = node is not None and node.has_tracks
        if not actions and not has_tracks:
            return None
        menu = QMenu(self)
        if has_tracks:
            self._playlist_menu(menu, lambda playlist: self.add_node(item, playlist))
            if actions:
                menu.addSeparator()
        for action in actions:
            menu.addAction(action.text).triggered.connect(action.run)
        return menu

    def _show_table_menu(self, pos: QPoint) -> None:
        menu = self.table_menu(self.table.indexAt(pos).row())
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _show_sidebar_menu(self, pos: QPoint) -> None:
        menu = self.sidebar_menu(self.sidebar.itemAt(pos))
        if menu is not None:
            menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    # -- Scanning --------------------------------------------------------------- #

    def _on_progress(self, checked: int) -> None:
        self._checked = checked
        self._update_status()

    def _on_scan_finished(self, summary: ScanSummary) -> None:
        self._checked = 0
        self._update_status()
        if summary.missing_sources:
            missing = ", ".join(path.name or str(path) for path in summary.missing_sources)
            self.status.setText(f"{self.status.text()} · Not found: {missing}")

    # -- Open and closed ----------------------------------------------------------- #

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._closed:
            self._closed = False
            self.show_tracks()

    def hideEvent(self, event: QHideEvent) -> None:
        """Let go of the tracks while the window is closed (a big library's take tens of MB);
        they're asked for again when it opens."""
        super().hideEvent(event)
        self._closed = True
        self.show_tracks()  # empties the table

    # -- Layout ------------------------------------------------------------------- #

    def _layout(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        browse = QSplitter(Qt.Orientation.Vertical)
        browse.setChildrenCollapsible(False)
        browse.addWidget(self.sidebar)
        browse.addWidget(self.artwork)
        browse.setStretchFactor(0, 1)
        browse.setSizes([300, 220])
        left_layout.addWidget(browse)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 660])
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        for button in (self.add_folder_button, self.add_files_button, self.rescan_button):
            bottom.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)


class _Sidebar(QTreeWidget):
    """The sources and what's in them. Tracks dragged from PyAmp (e.g. the table) can be
    dropped onto a saved playlist, or onto Playlists itself for a new one; nothing else
    is dropped here."""

    #: These tracks were dropped for this playlist ("" for a new one). Sent once the
    #: drop is over, as adding may ask for a name.
    tracks_dropped = Signal(list, str)

    def __init__(self, drop_target: Callable[[QTreeWidgetItem], str | None]) -> None:
        super().__init__()
        self._drop_target = drop_target
        self._hovered: QTreeWidgetItem | None = None
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if dragged_tracks(event.mimeData()):
            event.acceptProposedAction()  # each move then says whether it can land there
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        item = self.itemAt(event.position().toPoint())
        target = self._target(item, event)
        self._hover(item if target is not None else None)
        if target is None:
            event.ignore()
        else:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._hover(None)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        target = self._target(self.itemAt(event.position().toPoint()), event)
        self._hover(None)
        if target is None:
            event.ignore()
            return
        tracks = dragged_tracks(event.mimeData())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        QTimer.singleShot(0, lambda: self.tracks_dropped.emit(tracks, target))

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        item = self._hovered
        if item is None or item.treeWidget() is not self:  # gone meanwhile
            return
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().highlight().color(), 2))
        painter.drawRoundedRect(self.visualItemRect(item).adjusted(1, 1, -1, -1), 3, 3)

    def _target(self, item: QTreeWidgetItem | None, event: QDropEvent) -> str | None:
        if item is None or not dragged_tracks(event.mimeData()):
            return None
        return self._drop_target(item)

    def _hover(self, item: QTreeWidgetItem | None) -> None:
        if item is not self._hovered:
            self._hovered = item
            self.viewport().update()


def _children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    return [child for i in range(item.childCount()) if (child := item.child(i)) is not None]


def _key(item: QTreeWidgetItem) -> Hashable:
    node = item.data(0, _NODE)
    return node.key if isinstance(node, Node) else None


def _is_loading(item: QTreeWidgetItem) -> bool:
    """Whether what's inside ``item`` has been asked for but hasn't come back yet."""
    return any(c.text(0) == LOADING and c.data(0, _NODE) is None for c in _children(item))


def _path(item: QTreeWidgetItem) -> _Path:
    """Where ``item`` is: its source's position, then the keys of the nodes above it."""
    keys: list[Hashable] = []
    while (parent := item.parent()) is not None:
        keys.append(_key(item))
        item = parent
    tree = item.treeWidget()
    position = tree.indexOfTopLevelItem(item) if tree is not None else -1
    return position, tuple(reversed(keys))


def _location(item: QTreeWidgetItem | None) -> _Path | None:
    return _path(item) if item is not None else None


def _track_table(model: LibraryModel) -> QTableView:
    table = QTableView()
    table.setModel(model)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setDragEnabled(True)
    table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 4)
    header = table.horizontalHeader()
    header.setMinimumSectionSize(table.fontMetrics().horizontalAdvance(" 000 "))
    for number, column in enumerate(COLUMNS):
        if column.width is None:  # the title takes the room the others leave
            header.setSectionResizeMode(number, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(number, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(number, table.fontMetrics().horizontalAdvance(column.width))
    table.setSortingEnabled(True)
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)  # album order until a click
    return table


def _extra_style(skin: Skin) -> str:
    return f"""
    QLabel#artwork {{
        background: {skin.panel}; color: {skin.text_dim}; border: 1px solid {skin.border};
    }}
    """
