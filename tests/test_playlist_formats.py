from __future__ import annotations

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
    assert registry.read(playlist_file) == [tmp_path / "music" / "a.mp3", Path("/abs/b c.mp3")]


def test_pls_orders_by_entry_number(tmp_path: Path) -> None:
    playlist_file = tmp_path / "list.pls"
    playlist_file.write_text("[playlist]\nFile2=/b.mp3\nFile1=/a.mp3\nNumberOfEntries=2\n")
    assert registry.read(playlist_file) == [Path("/a.mp3"), Path("/b.mp3")]


def test_unsupported_extension() -> None:
    with pytest.raises(UnsupportedPlaylistError):
        registry.for_path(Path("x.xspf"))
