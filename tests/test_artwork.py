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
"""Finding a music file's picture: in its tags, else a cover image beside it."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mutagen
import pytest
from mutagen._vorbis import VCommentDict
from mutagen.flac import Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4Cover, MP4Tags
from mutagen.wave import WAVE

from pyamp.library import artwork
from pyamp.library.artwork import embedded_artwork, file_artwork, folder_artwork
from tests.test_metadata import add_frames, fake_mutagen_file, write_wav

FRONT, BACK = b"front-cover-bytes", b"back-cover-bytes"


def picture(kind: int, data: bytes) -> Any:
    flac_picture: Any = Picture()
    flac_picture.type, flac_picture.data, flac_picture.mime = kind, data, "image/jpeg"
    return flac_picture


def test_the_front_cover_in_id3_tags_is_preferred(tmp_path: Path) -> None:
    song = write_wav(tmp_path / "song.wav")
    back = APIC(encoding=3, mime="image/jpeg", type=4, desc="back", data=BACK)
    front = APIC(encoding=3, mime="image/jpeg", type=3, desc="front", data=FRONT)
    add_frames(song, WAVE, back, front)
    assert embedded_artwork(song) == FRONT


def test_mp4_covers(monkeypatch: pytest.MonkeyPatch) -> None:
    tags: Any = MP4Tags()
    tags["covr"] = [MP4Cover(FRONT, imageformat=MP4Cover.FORMAT_JPEG)]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags))
    assert embedded_artwork(Path("song.m4a")) == FRONT


def test_flac_pictures(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = SimpleNamespace(tags=None, pictures=[picture(4, BACK), picture(3, FRONT)])
    fake_mutagen_file(monkeypatch, audio)
    assert embedded_artwork(Path("song.flac")) == FRONT


def test_ogg_pictures(monkeypatch: pytest.MonkeyPatch) -> None:
    tags: Any = VCommentDict()
    block = base64.b64encode(picture(3, FRONT).write()).decode()
    tags["metadata_block_picture"] = ["not base64!", block]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags))
    assert embedded_artwork(Path("song.opus")) == FRONT


def test_files_without_a_picture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert embedded_artwork(write_wav(tmp_path / "plain.wav")) is None
    assert embedded_artwork(tmp_path / "missing.mp3") is None
    (tmp_path / "junk.mp3").write_bytes(b"not music")
    assert embedded_artwork(tmp_path / "junk.mp3") is None

    def broken(*_: object, **__: object) -> None:
        raise mutagen.MutagenError("bad file")

    monkeypatch.setattr(mutagen, "File", broken)
    assert embedded_artwork(tmp_path / "plain.wav") is None


def test_a_cover_image_beside_the_music(tmp_path: Path) -> None:
    album = tmp_path / "album"
    album.mkdir()
    song = write_wav(album / "song.wav")
    assert file_artwork(song) is None
    (album / "back.jpg").write_bytes(BACK)  # not a cover's name
    assert file_artwork(song) is None
    (album / "Folder.PNG").write_bytes(b"folder")
    assert file_artwork(song) == b"folder"  # any case
    (album / "cover.jpg").write_bytes(FRONT)
    assert file_artwork(song) == FRONT  # "cover" first
    add_frames(song, WAVE, APIC(encoding=3, mime="image/png", type=3, desc="", data=b"own"))
    assert file_artwork(song) == b"own"  # the song's own picture before the folder's
    assert folder_artwork(tmp_path / "missing") is None


def test_huge_pictures_are_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artwork, "MAX_ARTWORK_BYTES", 4)
    (tmp_path / "cover.jpg").write_bytes(b"too big")
    (tmp_path / "folder.jpg").write_bytes(b"ok")
    assert folder_artwork(tmp_path) == b"ok"
    song = write_wav(tmp_path / "song.wav")
    add_frames(song, WAVE, APIC(encoding=3, mime="image/png", type=3, desc="", data=b"too big"))
    assert embedded_artwork(song) is None
