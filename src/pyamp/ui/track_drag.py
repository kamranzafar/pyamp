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
"""Dragging tracks from one PyAmp window to another, tags and all.

A drag of local files can only say where they are, and a stream's bare URL loses its
title and artist; within PyAmp the tracks themselves go with the drag instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl

from pyamp.core.models import Track

#: Marks a drag of PyAmp's own tracks (they're in :attr:`TrackDrag.tracks`).
TRACKS_MIME = "application/x-pyamp-tracks"


class TrackDrag(QMimeData):
    """``tracks`` being dragged, with their files' addresses for other apps."""

    def __init__(self, tracks: Sequence[Track]) -> None:
        super().__init__()
        self.tracks = list(tracks)
        self.setData(TRACKS_MIME, b"")
        files = [track.location for track in self.tracks if isinstance(track.location, Path)]
        self.setUrls([QUrl.fromLocalFile(str(path)) for path in files])


def dragged_tracks(data: QMimeData) -> list[Track]:
    """The tracks a drag from PyAmp carries ([] for any other drag)."""
    return list(data.tracks) if isinstance(data, TrackDrag) else []
