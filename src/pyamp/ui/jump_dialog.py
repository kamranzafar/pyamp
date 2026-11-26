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
"""Jump to Track (Winamp's J): type part of a name, pick a match, play it."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from pyamp.core.models import Track

MAX_MATCHES = 500


def search_text(track: Track) -> str:
    """What a search looks in: the title (with artist) and the file name, case-folded."""
    return f"{track.display_title} {track.name}".casefold()


def matches(
    tracks: Sequence[Track],
    query: str,
    *,
    limit: int | None = None,
    texts: Sequence[str] | None = None,
) -> list[int]:
    """Indices of tracks whose title or file name contains every word of ``query``.

    Stops after ``limit`` matches. ``texts`` are the tracks' :func:`search_text`, if
    already worked out (worth it when searching the same tracks on every keystroke).
    """
    words = query.casefold().split()
    if not words:
        return list(range(len(tracks) if limit is None else min(limit, len(tracks))))
    found = []
    haystacks = texts if texts is not None else map(search_text, tracks)
    for index, haystack in enumerate(haystacks):
        if all(word in haystack for word in words):
            found.append(index)
            if len(found) == limit:
                break
    return found


class JumpDialog(QDialog):
    """Emits ``jump_requested(index)`` with the playlist index to play, then closes."""

    jump_requested = Signal(int)

    def __init__(self, tracks: Sequence[Track], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tracks = tracks
        self._texts: list[str] | None = None  # worked out on the first search
        self.setWindowTitle("Jump to Track")
        self.resize(420, 360)

        self.query = QLineEdit()
        self.query.setPlaceholderText("Type to search titles and file names")
        self.query.setClearButtonEnabled(True)
        self.results = QListWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.query)
        layout.addWidget(self.results)

        self.query.textChanged.connect(self._refresh)
        self.query.installEventFilter(self)  # arrow keys move through the results
        self.results.itemActivated.connect(self._jump)
        self._refresh("")

    def _refresh(self, text: str) -> None:
        self.results.clear()
        if text.strip() and self._texts is None:
            self._texts = [search_text(t) for t in self._tracks]
        for index in matches(self._tracks, text, limit=MAX_MATCHES, texts=self._texts):
            item = QListWidgetItem(f"{index + 1}. {self._tracks[index].display_title}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _jump(self, item: QListWidgetItem | None = None) -> None:
        item = item or self.results.currentItem()
        if item is not None:
            self.jump_requested.emit(int(item.data(Qt.ItemDataRole.UserRole)))
            self.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.query
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self.results.keyPressEvent(event)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._jump()
                return True
        return super().eventFilter(watched, event)
