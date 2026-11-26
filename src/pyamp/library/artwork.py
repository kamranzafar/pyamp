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
"""Finding a music file's picture: the one in its tags, else a cover image beside it.

No Qt here: the library window asks for it on a worker thread, and shows what it gets.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Tags

log = logging.getLogger(__name__)
_Picture: Any = Picture  # not typed

#: Pictures bigger than this are left alone.
MAX_ARTWORK_BYTES = 10 * 1024 * 1024
#: A picture's type, in ID3 and FLAC alike, when it's the front cover.
FRONT_COVER = 3
#: Images beside the music that hold its cover, best first (any case).
COVER_NAMES = ("cover", "folder", "front", "album")
COVER_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def file_artwork(path: Path) -> bytes | None:
    """``path``'s picture: the one in its tags, else a cover image in its folder."""
    return embedded_artwork(path) or folder_artwork(path.parent)


def embedded_artwork(path: Path) -> bytes | None:
    """The picture in ``path``'s tags (the front cover, if it says which), if any."""
    try:
        audio = mutagen.File(path)
    except (mutagen.MutagenError, OSError) as exc:
        log.debug("Could not read the picture in %s: %s", path, exc)
        return None
    if audio is None:
        return None
    pictures = _pictures(audio)
    best = sorted(pictures, key=lambda typed: typed[0] != FRONT_COVER)  # stable
    return next((data for _, data in best if 0 < len(data) <= MAX_ARTWORK_BYTES), None)


def folder_artwork(folder: Path) -> bytes | None:
    """The cover image in ``folder`` (``cover.jpg``, ``Folder.png``, ...), if any."""
    try:
        images = {
            p.name.casefold(): p
            for p in folder.iterdir()
            if p.suffix.casefold() in COVER_SUFFIXES and p.is_file()
        }
    except OSError:
        return None
    for name in COVER_NAMES:
        for suffix in COVER_SUFFIXES:
            image = images.get(f"{name}{suffix}")
            if image is not None and (data := _read(image)):
                return data
    return None


def _pictures(audio: Any) -> list[tuple[int, bytes]]:
    """Each picture in ``audio``'s tags, as (type, data); the type is 0 if unknown."""
    tags = audio.tags
    if isinstance(tags, ID3):
        return _id3_pictures(tags)
    if isinstance(tags, MP4Tags):
        return _mp4_pictures(tags)
    flac_pictures: Iterable[Picture] = getattr(audio, "pictures", None) or []
    pictures = [(picture.type, bytes(picture.data)) for picture in flac_pictures]
    if tags is not None and hasattr(tags, "get") and not pictures:
        pictures = [p for p in map(_vorbis_picture, tags.get("metadata_block_picture", [])) if p]
    return pictures


def _id3_pictures(tags: Any) -> list[tuple[int, bytes]]:  # Any: mutagen isn't typed
    return [(frame.type, bytes(frame.data)) for frame in tags.getall("APIC")]


def _mp4_pictures(tags: Any) -> list[tuple[int, bytes]]:
    return [(FRONT_COVER, bytes(cover)) for cover in tags.get("covr", [])]


def _vorbis_picture(value: str) -> tuple[int, bytes] | None:
    """A picture as Ogg Vorbis and Opus tags hold it: a FLAC picture block, in base64."""
    try:
        picture = _Picture(base64.b64decode(value))
    except (binascii.Error, ValueError, mutagen.MutagenError):
        return None
    return picture.type, bytes(picture.data)


def _read(image: Path) -> bytes | None:
    try:
        if image.stat().st_size > MAX_ARTWORK_BYTES:
            return None
        return image.read_bytes() or None
    except OSError:
        return None
