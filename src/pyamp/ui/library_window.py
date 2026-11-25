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
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Hashable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
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
from pyamp.library.scanner import ScanSummary
from pyamp.library.sources import MediaSource, MediaSources, Node, SourceError
from pyamp.ui.background_calls import BackgroundCalls
from pyamp.ui.library_model import COLUMNS, LibraryModel
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.skin import Skin

log = logging.getLogger(__name__)

#: The search box waits for typing to pause this long.
SEARCH_DELAY_MS = 200
LOADING = "Loading…"
#: Worker threads per source: enough to list a node while its tracks load.
CALLS_PER_SOURCE = 2

# Sidebar item data: the source an item belongs to, its node (None for the source's own
# item) and whether what's inside it has been asked for.
_SOURCE = Qt.ItemDataRole.UserRole
_NODE = Qt.ItemDataRole.UserRole + 1
_LOADED = Qt.ItemDataRole.UserRole + 2

type PlayTracks = Callable[[Sequence[Track], int], None]
type AddTracks = Callable[[Sequence[Track]], None]
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
        add_tracks: AddTracks,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._sources = sources
        self._settings = settings
        self._play_tracks = play_tracks
        self._add_tracks = add_tracks
        #: Each source's own worker threads, so a slow server only ever delays itself.
        self._calls: dict[int, tuple[MediaSource, BackgroundCalls]] = {}
        #: The threads of sources that have gone, kept until their last call returns.
        self._retired: list[BackgroundCalls] = []
        self._checked = 0
        self._error = ""
        self._tracks_request = 0
        #: Bumped when a source's part of the sidebar is rebuilt: late replies are dropped.
        self._generation: dict[int, int] = {}
        self._expanded: set[tuple[int, tuple[Hashable, ...]]] = set()
        self._known: set[int] = set()
        #: An item to select again once the sidebar has loaded down to it.
        self._restore: tuple[int, tuple[Hashable, ...]] | None = None
        self.setWindowTitle("Library")
        self.setObjectName("central")
        self.setStyleSheet(skin.stylesheet() + _extra_style(skin))
        self.resize(900, 560)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setExpandsOnDoubleClick(False)  # a click opens; a second shouldn't close
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.model = LibraryModel(self)
        self.table = _track_table(self.model)
        self.status = QLabel()
        self.add_folder_button = QPushButton("Add Folder…")
        self.add_files_button = QPushButton("Add Files…")
        self.rescan_button = QPushButton("Rescan")
        self._search_delay = QTimer(self)
        self._search_delay.setSingleShot(True)
        self._search_delay.setInterval(SEARCH_DELAY_MS)
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
        self._tracks_request += 1
        request = self._tracks_request
        picked = self.selected()
        if picked is None:
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
            self.model.set_tracks(tracks)
            self._update_status()

        self._calls_for(source).run(lambda: source.tracks(node, search), done)

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

    def add_selected(self) -> None:
        """Add the selected tracks to the open playlist."""
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if rows:
            self._add_tracks([self.model.track(row) for row in rows])

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
        add = menu.addAction("&Add to Playlist")
        add.setEnabled(self.table.selectionModel().hasSelection())
        add.triggered.connect(self.add_selected)
        return menu

    def sidebar_menu(self, item: QTreeWidgetItem | None) -> QMenu | None:
        """The menu for a right-click in the sidebar: the node's commands, if it has any."""
        node = item.data(0, _NODE) if item is not None else None
        if item is None or not isinstance(node, Node):
            return None
        source: MediaSource = item.data(0, _SOURCE)
        actions = source.actions(node)
        if not actions:
            return None
        menu = QMenu(self)
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

    # -- Layout ------------------------------------------------------------------- #

    def _layout(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.sidebar)
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
    QTreeWidget, QLineEdit {{
        background: {skin.panel}; color: {skin.playlist_text};
        border: 1px solid {skin.border};
        selection-background-color: {skin.selection}; selection-color: {skin.playlist_text};
    }}
    QHeaderView::section {{
        background: {skin.window}; color: {skin.text_dim};
        border: 0; border-bottom: 1px solid {skin.border}; padding: 2px 4px;
    }}
    QPushButton {{
        background: {skin.window}; color: {skin.text};
        border: 1px solid {skin.border}; padding: 2px 10px;
    }}
    QPushButton:hover {{ color: {skin.accent}; }}
    """
