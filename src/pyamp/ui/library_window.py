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
"""The library window: browse by artist, album, genre or folder, search, and play."""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
from pyamp.core.events import EventBus, LibraryChanged
from pyamp.core.models import Track, format_duration
from pyamp.library.catalog import Group, GroupKind
from pyamp.library.metadata import AUDIO_EXTENSIONS
from pyamp.library.scanner import ScanSummary
from pyamp.ui.library_model import COLUMNS, LibraryModel
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.skin import Skin

#: The search box waits for typing to pause this long.
SEARCH_DELAY_MS = 200
ALL_TRACKS = "All Tracks"
CATEGORIES = (
    (GroupKind.ARTIST, "Artists"),
    (GroupKind.ALBUM, "Albums"),
    (GroupKind.GENRE, "Genres"),
    (GroupKind.FOLDER, "Folders"),
)
_GROUP_ROLE = Qt.ItemDataRole.UserRole

type PlayTracks = Callable[[Sequence[Track], int], None]
type AddTracks = Callable[[Sequence[Track]], None]


def group_label(group: Group) -> str:
    match group.kind:
        case GroupKind.ARTIST:
            name = group.name or "Unknown Artist"
        case GroupKind.ALBUM:
            name = f"{group.name} — {group.artist}" if group.artist else group.name
        case GroupKind.FOLDER:
            name = Path(group.name).name or group.name
        case GroupKind.GENRE:
            name = group.name
    return f"{name} ({group.count})"


class LibraryWindow(QDialog):
    def __init__(
        self,
        library: BackgroundLibraryScanner,
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
        self._settings = settings
        self._play_tracks = play_tracks
        self._add_tracks = add_tracks
        self._checked = 0
        self._all_tracks: QTreeWidgetItem  # set by refresh(), below
        self._categories: dict[GroupKind, QTreeWidgetItem] = {}
        self._groups: list[QTreeWidgetItem] = []
        self.setWindowTitle("Library")
        self.setObjectName("central")
        self.setStyleSheet(skin.stylesheet() + _extra_style(skin))
        self.resize(900, 560)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.model = LibraryModel(self)
        self.table = _track_table(self.model)
        self.status = QLabel()
        self.add_folder_button = QPushButton("Add Folder…")
        self.add_files_button = QPushButton("Add Files…")
        self.rescan_button = QPushButton("Rescan")
        self._layout()

        self._search_delay = QTimer(self)
        self._search_delay.setSingleShot(True)
        self._search_delay.setInterval(SEARCH_DELAY_MS)
        self._search_delay.timeout.connect(self._show_tracks)
        self.search.textChanged.connect(self._search_delay.start)
        self.search.returnPressed.connect(self._show_tracks)
        self.sidebar.currentItemChanged.connect(self._show_tracks)
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
        bus.subscribe(LibraryChanged, lambda _: self.refresh())
        self.refresh()

    # -- What's shown ----------------------------------------------------------- #

    def refresh(self) -> None:
        """Reload the sidebar and the tracks, keeping what was selected and expanded."""
        selected = self.selected_group()
        expanded = {kind for kind, item in self._categories.items() if item.isExpanded()}
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        self._all_tracks = QTreeWidgetItem(self.sidebar, [ALL_TRACKS])
        self._categories = {}
        self._groups = []
        for kind, title in CATEGORIES:
            category = QTreeWidgetItem(self.sidebar, [title])
            category.setFlags(category.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self._categories[kind] = category
            for group in self._library.catalog.groups(kind):
                item = QTreeWidgetItem(category, [group_label(group)])
                item.setData(0, _GROUP_ROLE, group)
                item.setToolTip(0, group.name)
                self._groups.append(item)
            category.setExpanded(kind in expanded)
        self.select_group(selected)
        self.sidebar.blockSignals(False)
        self._show_tracks()

    def selected_group(self) -> Group | None:
        """The artist, album, genre or folder picked in the sidebar (None: all tracks)."""
        item = self.sidebar.currentItem()
        group = item.data(0, _GROUP_ROLE) if item is not None else None
        return group if isinstance(group, Group) else None

    def select_group(self, group: Group | None) -> None:
        """Show ``group`` (None, or one that's gone: all tracks), as if picked in the sidebar."""
        item = next(
            (i for i in self._groups if group is not None and _same(i.data(0, _GROUP_ROLE), group)),
            self._all_tracks,
        )
        self.sidebar.setCurrentItem(item)

    def category(self, kind: GroupKind) -> QTreeWidgetItem:
        """The sidebar's Artists, Albums, Genres or Folders item."""
        return self._categories[kind]

    def _show_tracks(self) -> None:
        self._search_delay.stop()
        tracks = self._library.catalog.tracks(self.search.text(), self.selected_group())
        self.model.set_tracks(tracks)
        self._update_status()

    def _update_status(self) -> None:
        tracks = self.model.tracks()
        if not self._library.catalog.sources():
            text = "Add a folder of music to start your library"
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
        """The menu for a right-click in the sidebar: folders can be removed."""
        group = item.data(0, _GROUP_ROLE) if item is not None else None
        if not isinstance(group, Group) or group.kind is not GroupKind.FOLDER:
            return None
        menu = QMenu(self)
        remove = menu.addAction("&Remove from Library")
        remove.setToolTip("The files stay where they are")
        remove.triggered.connect(lambda: self._library.remove_source(Path(group.name)))
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


def _same(group: object, other: Group) -> bool:
    """The same artist, album, genre or folder, whatever its track count now."""
    return isinstance(group, Group) and (group.kind, group.name, group.artist) == (
        other.kind,
        other.name,
        other.artist,
    )


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
