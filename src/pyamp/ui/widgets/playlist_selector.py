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
"""The row above the playlist: which saved playlist is open, and a "new" button."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from pyamp.core.events import EventBus, SavedPlaylistsChanged
from pyamp.ui.dialog_parts import ComboBox


class PlaylistSelector(QWidget):
    switch_requested = Signal(str)
    new_requested = Signal()

    def __init__(self, bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.combo = ComboBox()
        self.combo.setToolTip("Saved playlists")
        self.combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.new_button = QToolButton()
        self.new_button.setText("+")
        self.new_button.setToolTip("New playlist (Ctrl+N)")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Playlist"))
        layout.addWidget(self.combo, stretch=1)
        layout.addWidget(self.new_button)

        self.combo.textActivated.connect(self.switch_requested)
        self.new_button.clicked.connect(self.new_requested)
        bus.subscribe(SavedPlaylistsChanged, self._show)

    def _show(self, event: SavedPlaylistsChanged) -> None:
        self.combo.blockSignals(True)  # repopulating isn't a user choice
        self.combo.clear()
        self.combo.addItems(list(event.names))
        self.combo.setCurrentText(event.active)
        self.combo.blockSignals(False)
