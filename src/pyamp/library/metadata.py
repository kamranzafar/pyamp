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

"""Reading track metadata from audio files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import mutagen
from mutagen.id3 import ID3

from pyamp.core.models import Track

log = logging.getLogger(__name__)

# Our tag names -> ID3 frame IDs, for formats where mutagen's easy mode doesn't apply.
_ID3_FRAMES = {"title": "TIT2", "artist": "TPE1", "album": "TALB"}

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".wma"}
)


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


class MetadataReader(Protocol):
    def read(self, path: Path) -> Track: ...


class MutagenMetadataReader:
    """Reads tags via mutagen. Unreadable files still produce a usable Track."""

    def read(self, path: Path) -> Track:
        try:
            audio = mutagen.File(path, easy=True)
        except (mutagen.MutagenError, OSError) as exc:
            log.debug("Could not read tags from %s: %s", path, exc)
            audio = None
        if audio is None:
            return Track.from_path(path)

        tags: Any = audio.tags or {}
        # easy=True only wraps ID3 for MP3-like formats. WAV and AIFF still hand back raw
        # ID3 tags keyed by frame ID, so translate our keys for those.
        raw_id3 = isinstance(tags, ID3)

        def first(key: str) -> str:
            try:
                if raw_id3:
                    frame = tags.get(_ID3_FRAMES[key])
                    values = frame.text if frame is not None else None
                else:
                    values = tags.get(key)
            except (KeyError, ValueError):
                return ""
            return str(values[0]).strip() if values else ""

        info = getattr(audio, "info", None)
        length = getattr(info, "length", 0) or 0
        return Track(
            path=path,
            title=first("title") or path.stem,
            artist=first("artist"),
            album=first("album"),
            duration_ms=int(length * 1000),
        )
