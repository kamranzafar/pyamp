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

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import StreamUrl, Track
from pyamp.library.loader import TrackLoader
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.library.remote import fetch_text


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


# -- Streams and remote playlists ----------------------------------------------------- #


class Server:
    """A tiny local web server: no internet needed. Serves ``files`` by path."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}
        self.requests: list[str] = []
        files, requests = self.files, self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(self.path)
                body, content_type = files.get(self.path, (b"", ""))
                if not content_type:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def server() -> Iterator[Server]:
    server = Server()
    yield server
    server.close()


def test_a_stream_url_becomes_a_track_without_reading_tags(
    loader: TrackLoader, reader: RecordingReader
) -> None:
    url = StreamUrl("http://radio.example/groove-salad")
    assert loader.load([url]) == [Track.for_stream(url)]
    assert reader.read_paths == []


def test_a_stations_pls_is_downloaded_and_expanded(loader: TrackLoader, server: Server) -> None:
    server.files["/groove.pls"] = (
        b"[playlist]\nFile1=http://ice1.example/groove\nTitle1=SomaFM: Groove Salad\n"
        b"File2=http://ice2.example/groove\nNumberOfEntries=2\n",
        "audio/x-scpls",
    )
    tracks = loader.load([StreamUrl(f"{server.url}/groove.pls")])
    assert [(str(t.location), t.title) for t in tracks] == [
        ("http://ice1.example/groove", "SomaFM: Groove Salad"),
        ("http://ice2.example/groove", "groove"),
    ]


def test_a_remote_playlist_cannot_point_at_local_files(
    loader: TrackLoader, server: Server, tmp_path: Path
) -> None:
    secret = touch(tmp_path / "private.mp3")
    server.files["/list.m3u"] = (f"{secret}\nhttp://ok.example/live\n".encode(), "audio/mpegurl")
    tracks = loader.load([StreamUrl(f"{server.url}/list.m3u")])
    # The path is taken as a URL on the playlist's own server, never as a local file.
    assert all(isinstance(t.location, StreamUrl) for t in tracks)
    assert str(tracks[0].location).startswith(server.url)
    assert str(tracks[1].location) == "http://ok.example/live"


def test_a_missing_remote_playlist_adds_nothing(loader: TrackLoader, server: Server) -> None:
    assert loader.load([StreamUrl(f"{server.url}/gone.pls")]) == []
    assert server.requests == ["/gone.pls"]


def test_remote_playlists_nest_only_so_deep(loader: TrackLoader, server: Server) -> None:
    server.files["/a.m3u"] = (f"{server.url}/b.m3u\n".encode(), "audio/mpegurl")
    server.files["/b.m3u"] = (f"{server.url}/a.m3u\n".encode(), "audio/mpegurl")  # a loop
    assert loader.load([StreamUrl(f"{server.url}/a.m3u")]) == []
    assert len(server.requests) == 2


def test_local_playlists_keep_their_stations(
    loader: TrackLoader, reader: RecordingReader, tmp_path: Path
) -> None:
    song = touch(tmp_path / "song.mp3")
    playlist = tmp_path / "mix.m3u8"
    playlist.write_text(f"#EXTM3U\n{song}\n#EXTINF:-1,Radio\nhttps://radio.example/live\n")
    tracks = loader.load([playlist])
    assert [t.location for t in tracks] == [song, StreamUrl("https://radio.example/live")]
    assert tracks[1].title == "Radio"
    assert reader.read_paths == [song]


def test_fetch_text_limits_size(server: Server) -> None:
    server.files["/huge.m3u"] = (b"x" * 2000, "audio/mpegurl")
    with pytest.raises(ValueError, match="larger than"):
        fetch_text(f"{server.url}/huge.m3u", max_bytes=1000)
    with pytest.raises(ValueError):
        fetch_text("ftp://example.com/x.m3u")
