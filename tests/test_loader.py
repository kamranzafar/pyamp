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

from __future__ import annotations

from pathlib import Path

import pytest

from pyamp.core.models import Track
from pyamp.library.loader import TrackLoader
from pyamp.library.playlist_formats import PlaylistFormatRegistry


class RecordingReader:
    def __init__(self) -> None:
        self.read_paths: list[Path] = []

    def read(self, path: Path) -> Track:
        self.read_paths.append(path)
        return Track.from_path(path)


@pytest.fixture
def reader() -> RecordingReader:
    return RecordingReader()


@pytest.fixture
def loader(reader: RecordingReader) -> TrackLoader:
    return TrackLoader(reader, PlaylistFormatRegistry.default())


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_folder_is_scanned_recursively_sorted_and_filtered(
    tmp_path: Path, loader: TrackLoader
) -> None:
    b = touch(tmp_path / "b.mp3")
    a = touch(tmp_path / "a.FLAC")
    nested = touch(tmp_path / "disc 2" / "c.ogg")
    touch(tmp_path / "cover.jpg")
    touch(tmp_path / "notes.txt")
    (tmp_path / "fake.mp3").mkdir()  # a directory with an audio extension is not a file
    assert list(loader.discover([tmp_path])) == [a, b, nested]


def test_single_files_keep_given_order_and_skip_non_audio(
    tmp_path: Path, loader: TrackLoader
) -> None:
    z = touch(tmp_path / "z.mp3")
    a = touch(tmp_path / "a.wav")
    txt = touch(tmp_path / "readme.txt")
    missing = tmp_path / "missing.mp3"
    assert list(loader.discover([z, txt, missing, a])) == [z, a]


def test_playlist_entries_are_expanded_and_missing_ones_skipped(
    tmp_path: Path, loader: TrackLoader
) -> None:
    present = touch(tmp_path / "music" / "one.mp3")
    playlist = tmp_path / "list.m3u"
    playlist.write_text("music/one.mp3\nmusic/gone.mp3\n")
    assert list(loader.discover([playlist])) == [present]


def test_unreadable_playlist_is_skipped(
    tmp_path: Path, loader: TrackLoader, caplog: pytest.LogCaptureFixture
) -> None:
    song = touch(tmp_path / "song.mp3")
    assert list(loader.discover([tmp_path / "nope.m3u", song])) == [song]
    assert "Could not read playlist" in caplog.text


def test_load_reads_metadata_for_every_discovered_file(
    tmp_path: Path, loader: TrackLoader, reader: RecordingReader
) -> None:
    paths = [touch(tmp_path / "1.mp3"), touch(tmp_path / "2.mp3")]
    assert loader.load([tmp_path]) == [Track.from_path(p) for p in paths]
    assert reader.read_paths == paths


def test_iter_load_is_lazy(tmp_path: Path, loader: TrackLoader, reader: RecordingReader) -> None:
    touch(tmp_path / "1.mp3")
    touch(tmp_path / "2.mp3")
    tracks = loader.iter_load([tmp_path])
    assert reader.read_paths == []
    next(tracks)
    assert len(reader.read_paths) == 1
