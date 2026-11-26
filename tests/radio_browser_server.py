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
"""A pretend Radio Browser (radio-browser.info) on localhost, for the Radio Directory
plugin's tests (no internet).

It answers the requests the plugin makes, from three stations: two good ones and one
with no usable stream. Like the real one, it can sort, filter by country, genre or
language, and search by name; it counts plays, and serves a station logo.
"""

from __future__ import annotations

import http.server
import json
import threading
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

LOGO = b"\x89PNG\r\n\x1a\nlogo"


class RadioBrowserServer:
    def __init__(self) -> None:
        #: (path, query) of each request, and the User-Agent each came with.
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.agents: list[str] = []
        self.plays: list[str] = []
        self.broken = False  # answer every request with an error
        self.garbled = False  # answer with something that isn't JSON
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                path = unquote(parts.path)
                server.requests.append((path, query))
                server.agents.append(self.headers.get("User-Agent", ""))
                if server.broken:
                    self.send_error(503)
                    return
                if path == "/logo.png":
                    self._send(LOGO, "image/png")
                    return
                if server.garbled:
                    self._send(b"<html>Bad gateway</html>", "text/html")
                    return
                answer = server.answer(path, query)
                if answer is None:
                    self.send_error(404)
                    return
                self._send(json.dumps(answer).encode(), "application/json")

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
        self.stations: list[dict[str, Any]] = [
            {"stationuuid": "u-jazz", "name": "Jazz FM", "url": "http://jazz.example/pls",
             "url_resolved": "http://jazz.example/live", "favicon": f"{self.url}/logo.png",
             "tags": "jazz,smooth jazz", "country": "United Kingdom", "countrycode": "GB",
             "language": "english", "codec": "MP3", "bitrate": 128, "votes": 10,
             "clickcount": 5},
            {"stationuuid": "u-news", "name": " Radio  Paris ", "url": "http://paris.example/a",
             "url_resolved": "", "favicon": "", "tags": "news", "country": "France",
             "countrycode": "FR", "language": "french,english", "codec": "aac",
             "bitrate": 0, "votes": 5, "clickcount": 20},
            {"stationuuid": "u-bad", "name": "Nowhere", "url": "ftp://nowhere",
             "url_resolved": "", "tags": "", "country": "", "countrycode": "", "language": "",
             "codec": "", "bitrate": 0, "votes": 1, "clickcount": 1},
        ]  # fmt: skip
        threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    def answer(self, path: str, query: dict[str, str]) -> Any:
        match path.removeprefix("/json/").split("/"):
            case ["stations", "search"]:
                return self._search(query)
            case ["countries"]:
                return [
                    {"name": "France", "iso_3166_1": "FR", "stationcount": 1},
                    {"name": "United Kingdom", "iso_3166_1": "GB", "stationcount": 1200},
                    {"name": "Unknown", "iso_3166_1": "", "stationcount": 3},
                ]
            case ["tags"]:
                return [{"name": "jazz", "stationcount": 1}, {"name": "", "stationcount": 9}]
            case ["languages"]:
                return [{"name": "english", "iso_639": "en", "stationcount": 2}]
            case ["url", uuid]:
                self.plays.append(uuid)
                return {"ok": True, "message": "retrieved station url"}
        return None

    def _search(self, query: dict[str, str]) -> list[dict[str, Any]]:
        found = list(self.stations)
        if "name" in query:
            found = [s for s in found if query["name"].lower() in s["name"].lower()]
        if "countrycode" in query:
            found = [s for s in found if s["countrycode"] == query["countrycode"]]
        if "tag" in query:
            found = [s for s in found if query["tag"] in s["tags"].split(",")]
        if "language" in query:
            found = [s for s in found if query["language"] in s["language"].split(",")]
        order = query.get("order", "name")
        found.sort(key=lambda s: s[order], reverse=query.get("reverse") == "true")
        return found[: int(query.get("limit", "100000"))]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> RadioBrowserServer:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()
