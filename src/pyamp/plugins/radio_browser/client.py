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
"""A client for Radio Browser (radio-browser.info): a free directory of internet radio
stations, kept by its users. No account or key is needed.

Radio Browser runs on a few mirrors and asks programs to spread their requests between
them: the client finds them by DNS, takes them in random order, and moves on to the next
when one fails. It says it's PyAmp (and its version), and nothing else.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import threading
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from pyamp import APP_NAME, __version__
from pyamp.core.models import StreamUrl, Track
from pyamp.library.remote import open_url

log = logging.getLogger(__name__)

#: Resolves to every mirror (several addresses); each one's own name is found from it.
API_HOST = "all.api.radio-browser.info"
TIMEOUT_S = 10.0
#: The most of an answer read: a list of LIMIT stations is a few hundred KB.
MAX_BYTES = 8_000_000
#: The most of a station's logo read.
MAX_LOGO_BYTES = 512_000
#: Stations per list, and the most countries, genres or languages listed.
LIMIT = 200
CATEGORY_LIMIT = 100
#: How many stations' ids are remembered (to report plays of them).
KNOWN_STATIONS = 20_000


class RadioBrowserError(Exception):
    """The directory couldn't be reached, or its answer couldn't be read."""


@dataclass(frozen=True, slots=True)
class Category:
    """A country, genre or language, and how many stations it has."""

    #: What stations are asked for by: a country's code, or the genre's or language's name.
    key: str
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class Station:
    uuid: str
    name: str
    url: StreamUrl
    favicon: str = ""
    tags: str = ""
    country: str = ""
    codec: str = ""
    bitrate: int = 0

    @property
    def track(self) -> Track:
        """As a live stream: the station's name, its country and format (shown in the
        Album column), its tags as the genre, and its logo as the artwork."""
        quality = f"{self.codec} {self.bitrate} kbps".strip() if self.bitrate else self.codec
        about = " · ".join(part for part in (self.country, quality) if part)
        return Track(
            location=self.url, title=self.name, album=about, genre=self.tags, artwork=self.favicon
        )


def find_servers() -> list[str]:
    """The mirrors' addresses, in random order (as Radio Browser asks)."""
    try:
        found = socket.getaddrinfo(API_HOST, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        log.debug("Can't look up Radio Browser's mirrors: %s", exc)
        return [f"https://{API_HOST}"]
    names: set[str] = set()
    for *_, address in found:
        try:
            names.add(socket.gethostbyaddr(str(address[0]))[0])
        except OSError:
            continue
    servers = [f"https://{name}" for name in names]
    random.shuffle(servers)
    return servers or [f"https://{API_HOST}"]


class RadioBrowserClient:
    """Asks Radio Browser for stations. Thread-safe: the library calls it from its
    worker threads."""

    def __init__(
        self, servers: Callable[[], list[str]] = find_servers, *, timeout: float = TIMEOUT_S
    ) -> None:
        self._find_servers = servers
        self._timeout = timeout
        self._lock = threading.Lock()
        self._servers: list[str] | None = None  # found when first needed
        self._ids: dict[str, str] = {}  # stream URL -> station id, of stations listed

    # -- Stations ------------------------------------------------------------- #

    def top_voted(self) -> list[Station]:
        return self._stations(order="votes")

    def most_played(self) -> list[Station]:
        return self._stations(order="clickcount")

    def by_country(self, code: str) -> list[Station]:
        return self._stations(order="votes", countrycode=code)

    def by_genre(self, tag: str) -> list[Station]:
        return self._stations(order="votes", tag=tag, tagExact="true")

    def by_language(self, language: str) -> list[Station]:
        return self._stations(order="votes", language=language, languageExact="true")

    def search(self, name: str) -> list[Station]:
        return self._stations(order="clickcount", name=name)

    # -- Categories ----------------------------------------------------------- #

    def countries(self) -> list[Category]:
        return [
            Category(code, _text(r.get("name")) or code, _int(r.get("stationcount")))
            for r in self._categories("countries")
            if (code := _text(r.get("iso_3166_1")))
        ]

    def genres(self) -> list[Category]:
        return [
            Category(name, name, _int(r.get("stationcount")))
            for r in self._categories("tags", limit=CATEGORY_LIMIT)
            if (name := _text(r.get("name")))
        ]

    def languages(self) -> list[Category]:
        return [
            Category(name, name.title(), _int(r.get("stationcount")))
            for r in self._categories("languages", limit=CATEGORY_LIMIT)
            if (name := _text(r.get("name")))
        ]

    # -- Plays and logos ------------------------------------------------------ #

    def station_id(self, url: StreamUrl) -> str | None:
        """The id of a listed station that plays ``url`` (None if none was listed)."""
        with self._lock:
            return self._ids.get(url.url)

    def count_play(self, station_id: str) -> None:
        """Tell Radio Browser the station is being played: its "most played" list counts
        these. Errors are only logged: the station plays anyway."""
        try:
            self._get(f"url/{quote(station_id, safe='')}")
        except RadioBrowserError as exc:
            log.debug("Couldn't count a play of %s: %s", station_id, exc)

    def logo(self, url: str) -> bytes | None:
        """A station's logo (any http(s) address); None if it can't be had."""
        if not url.lower().startswith(("http://", "https://")):
            return None
        try:
            data = _fetch(url, self._timeout, MAX_LOGO_BYTES)
        except (OSError, ValueError) as exc:
            log.debug("No logo at %s: %s", url, exc)
            return None
        return data or None

    # -- Internals ------------------------------------------------------------ #

    def _stations(self, **query: str) -> list[Station]:
        rows = self._get(
            "stations/search",
            {**query, "reverse": "true", "hidebroken": "true", "limit": str(LIMIT)},
        )
        stations = [s for s in map(_station, rows) if s is not None]
        with self._lock:
            if len(self._ids) > KNOWN_STATIONS:
                self._ids.clear()
            self._ids.update((s.url.url, s.uuid) for s in stations)
        return stations

    def _categories(self, kind: str, **extra: object) -> list[Mapping[str, Any]]:
        query = {"order": "stationcount", "reverse": "true", "hidebroken": "true"}
        query.update({k: str(v) for k, v in extra.items()})
        return self._get(kind, query)

    def _get(self, path: str, query: Mapping[str, str] | None = None) -> list[Mapping[str, Any]]:
        """GET /json/``path`` from a mirror, moving on to the next if one fails."""
        suffix = f"/json/{path}" + (f"?{urlencode(query)}" if query else "")
        servers = self._mirrors()
        error: Exception | None = None
        for server in servers:
            try:
                answer = json.loads(_fetch(server + suffix, self._timeout, MAX_BYTES))
            except (OSError, ValueError) as exc:
                log.debug("Radio Browser mirror %s failed: %s", server, exc)
                error = exc
                self._move_to_back(server)
                continue
            if isinstance(answer, dict):  # e.g. a play counted
                return [answer]
            if not isinstance(answer, list):
                raise RadioBrowserError("Radio Browser sent something unexpected")
            return [row for row in answer if isinstance(row, dict)]
        raise RadioBrowserError(describe(error) if error else "No Radio Browser server found")

    def _mirrors(self) -> list[str]:
        with self._lock:
            if self._servers is None:
                self._servers = [s.rstrip("/") for s in self._find_servers()] or [
                    f"https://{API_HOST}"
                ]
            return list(self._servers)

    def _move_to_back(self, server: str) -> None:
        with self._lock:
            if self._servers and server in self._servers:
                self._servers.remove(server)
                self._servers.append(server)


def describe(error: BaseException) -> str:
    """A short message for the user."""
    if isinstance(error, RadioBrowserError):
        return str(error)
    if isinstance(error, ValueError):
        return f"Radio Browser sent something unreadable: {error}"
    if isinstance(error, OSError):
        reason = getattr(error, "reason", None) or error.strerror or error
        return f"Could not reach Radio Browser: {reason}"
    return f"Something went wrong: {error}"


def _fetch(url: str, timeout: float, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"{APP_NAME}/{__version__}", "Accept": "application/json, image/*"},
    )
    with open_url(request, timeout) as response:
        data = bytes(response.read(max_bytes + 1))
    if len(data) > max_bytes:
        raise ValueError(f"more than {max_bytes} bytes")
    return data


def _station(row: Mapping[str, Any]) -> Station | None:
    """A station from the directory; None if it has no usable stream."""
    for field in ("url_resolved", "url"):
        try:
            url = StreamUrl(_text(row.get(field)))
        except ValueError:
            continue
        break
    else:
        return None
    uuid = _text(row.get("stationuuid"))
    if not uuid:
        return None
    tags = ", ".join(t.strip() for t in _text(row.get("tags")).split(",") if t.strip())
    return Station(
        uuid=uuid,
        name=_text(row.get("name")) or url.default_title,
        url=url,
        favicon=_text(row.get("favicon")),
        tags=tags,
        country=_text(row.get("country")),
        codec=_text(row.get("codec")).upper(),
        bitrate=_int(row.get("bitrate")),
    )


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0
