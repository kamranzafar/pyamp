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

from pyamp.core.models import Track
from pyamp.library.playlist_formats import PlaylistFormatRegistry, UnsupportedPlaylistError

registry = PlaylistFormatRegistry.default()


@pytest.mark.parametrize("name", ["list.m3u8", "list.pls"])
def test_round_trip(tmp_path: Path, name: str) -> None:
    tracks = [
        Track(path=tmp_path / "a.mp3", title="A", artist="X", duration_ms=61_000),
        Track(path=tmp_path / "sub dir" / "b.flac", title="B"),
    ]
    playlist_file = tmp_path / name
    registry.write(playlist_file, tracks)
    assert registry.read(playlist_file) == [t.path for t in tracks]


def test_m3u_resolves_relative_paths_and_skips_urls(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.m3u"
    playlist_file.write_text(
        "#EXTM3U\nmusic/a.mp3\nhttp://radio.example/stream\n\nfile:///abs/b%20c.mp3\n"
    )
    root = Path(tmp_path.anchor)  # "/", or the drive on Windows ("C:\\")
    assert registry.read(playlist_file) == [tmp_path / "music" / "a.mp3", root / "abs" / "b c.mp3"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file URLs have drive letters")
def test_m3u_reads_windows_file_urls(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.m3u"
    playlist_file.write_text("file:///C:/Music/a%20b.mp3\nfile:///D:/x.flac\n")
    assert registry.read(playlist_file) == [Path("C:/Music/a b.mp3"), Path("D:/x.flac")]


def test_pls_orders_by_entry_number(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.pls"
    playlist_file.write_text("[playlist]\nFile2=/b.mp3\nFile1=/a.mp3\nNumberOfEntries=2\n")
    root = Path(tmp_path.anchor)
    assert registry.read(playlist_file) == [root / "a.mp3", root / "b.mp3"]


def test_unsupported_extension() -> None:
    with pytest.raises(UnsupportedPlaylistError):
        registry.for_path(Path("x.xspf"))
