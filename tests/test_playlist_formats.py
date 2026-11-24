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

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyamp.core.models import Location, StreamUrl, Track
from pyamp.library.playlist_formats import (
    M3UFormat,
    PlaylistFormatRegistry,
    PLSFormat,
    UnsupportedPlaylistError,
)

registry = PlaylistFormatRegistry.default()


def locations(path: Path) -> list[Location]:
    return [entry.location for entry in registry.read(path)]


@pytest.mark.parametrize("name", ["list.m3u8", "list.pls"])
def test_round_trip_with_files_and_streams(tmp_path: Path, name: str) -> None:
    tracks = [
        Track(location=tmp_path / "a.mp3", title="A", artist="X", duration_ms=61_000),
        Track(location=tmp_path / "sub dir" / "b.flac", title="B"),
        Track.for_stream(StreamUrl("https://radio.example/live?q=1"), "Groove Salad"),
        Track.for_stream(StreamUrl("https://music.example/rest/stream?id=7"), "Song", 185_000),
    ]
    playlist_file = tmp_path / name
    registry.write(playlist_file, tracks)
    entries = registry.read(playlist_file)
    assert [e.location for e in entries] == [t.location for t in tracks]
    assert [e.title for e in entries] == ["X - A", "B", "Groove Salad", "Song"]
    # Lengths come back (whole seconds), so a server's tracks stay seekable after a reload.
    assert [e.duration_ms for e in entries] == [61_000, 0, 0, 185_000]


def test_m3u_resolves_relative_paths_and_keeps_urls(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.m3u"
    playlist_file.write_text(
        "#EXTM3U\nmusic/a.mp3\n#EXTINF:-1,Radio One\nhttp://radio.example/stream\n\n"
        "rtsp://unsupported/x\nfile:///abs/b%20c.mp3\n"
    )
    root = Path(tmp_path.anchor)  # "/", or the drive on Windows ("C:\\")
    entries = registry.read(playlist_file)
    assert [e.location for e in entries] == [
        tmp_path / "music" / "a.mp3",
        StreamUrl("http://radio.example/stream"),
        root / "abs" / "b c.mp3",
    ]
    assert entries[1].title == "Radio One"
    assert entries[0].title is None


def test_pls_station_with_titles() -> None:
    text = (
        "[playlist]\nnumberofentries=2\nFile1=http://ice1.example/groove\nTitle1=Groove Salad"
        "\nLength1=-1\nFile2=http://ice2.example/groove\nVersion=2\n"
    )
    entries = PLSFormat().parse(text, StreamUrl("http://station.example/groove.pls"))
    assert [(str(e.location), e.title) for e in entries] == [
        ("http://ice1.example/groove", "Groove Salad"),
        ("http://ice2.example/groove", None),
    ]


def test_remote_playlist_entries_resolve_against_its_url() -> None:
    entries = M3UFormat().parse(
        "live.mp3\n/other/x.aac\n", StreamUrl("http://h.example/r/list.m3u")
    )
    assert [str(e.location) for e in entries] == [
        "http://h.example/r/live.mp3",
        "http://h.example/other/x.aac",
    ]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file URLs have drive letters")
def test_m3u_reads_windows_file_urls(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.m3u"
    playlist_file.write_text("file:///C:/Music/a%20b.mp3\nfile:///D:/x.flac\n")
    assert locations(playlist_file) == [Path("C:/Music/a b.mp3"), Path("D:/x.flac")]


def test_pls_orders_by_entry_number(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.pls"
    playlist_file.write_text("[playlist]\nFile2=/b.mp3\nFile1=/a.mp3\nNumberOfEntries=2\n")
    root = Path(tmp_path.anchor)
    assert locations(playlist_file) == [root / "a.mp3", root / "b.mp3"]


def test_unsupported_extension() -> None:
    with pytest.raises(UnsupportedPlaylistError):
        registry.for_path(Path("x.xspf"))
