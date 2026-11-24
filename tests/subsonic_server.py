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
"""A pretend Subsonic server on localhost, for the Subsonic plugin's tests (no internet).

It checks every request's token login (``t == md5(password + s)``) the way real servers
do, and serves a tiny library: one artist with one album of two songs, and a playlist.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit

from tests.radio import tone_wav

USERNAME = "alice"
PASSWORD = "s3cret"

SONGS = [
    {"id": "s1", "title": "Teardrop", "artist": "Massive Attack", "album": "Mezzanine",
     "duration": 330, "track": 1},
    {"id": "s2", "title": "Angel", "artist": "Massive Attack", "album": "Mezzanine",
     "duration": 379, "track": 2},
]  # fmt: skip
ALBUM = {"id": "al1", "name": "Mezzanine", "artist": "Massive Attack", "year": 1998}
ARTIST = {"id": "ar1", "name": "Massive Attack", "albumCount": 1}


class SubsonicServer:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []  # (method, query)
        audio = tone_wav(2.0)
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                query = {k: v[-1] for k, v in parse_qs(url.query).items()}
                method = url.path.removeprefix("/rest/")
                server.requests.append((method, query))
                if url.path.startswith("/website/"):  # a web page, not an API reply
                    self._send(b"<html>hello</html>", "text/html")
                elif not url.path.startswith("/rest/"):
                    self.send_error(404)
                elif not server.logged_in(query):
                    self._json({"status": "failed", "error": {"code": 40,
                                "message": "Wrong username or password"}})  # fmt: skip
                elif method == "stream":
                    self._send(audio, "audio/wav")
                else:
                    self._json({"status": "ok", **server.reply(method, query)})

            def _json(self, body: dict[str, Any]) -> None:
                data = json.dumps({"subsonic-response": {"version": "1.16.1", **body}}).encode()
                self._send(data, "application/json")

            def _send(self, data: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    @staticmethod
    def logged_in(query: dict[str, str]) -> bool:
        expected = hashlib.md5((PASSWORD + query.get("s", "")).encode()).hexdigest()
        return query.get("u") == USERNAME and query.get("t") == expected and "s" in query

    @staticmethod
    def reply(method: str, query: dict[str, str]) -> dict[str, Any]:  # noqa: PLR0911
        match method:
            case "ping":
                return {}
            case "getArtists":
                return {"artists": {"index": [{"name": "M", "artist": [ARTIST]}]}}
            case "getArtist" if query.get("id") == ARTIST["id"]:
                return {"artist": {**ARTIST, "album": [ALBUM]}}
            case "getAlbum" if query.get("id") == ALBUM["id"]:
                return {"album": {**ALBUM, "song": SONGS}}
            case "getPlaylists":
                return {"playlists": {"playlist": [{"id": "p1", "name": "Mix", "songCount": 1}]}}
            case "getPlaylist" if query.get("id") == "p1":
                return {"playlist": {"id": "p1", "name": "Mix", "entry": SONGS[1:]}}
            case "search3":
                hits = [
                    s for s in SONGS if query.get("query", "").lower() in str(s["title"]).lower()
                ]
                return {"searchResult3": {"album": [ALBUM] if hits else [], "song": hits}}
        return {"status": "failed", "error": {"code": 70, "message": "Not found"}}

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> SubsonicServer:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()
