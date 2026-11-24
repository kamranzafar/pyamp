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
"""A small client for the Subsonic API (Navidrome, Airsonic, Gonic and others).

No Qt here: the plugin's windows call it from a worker thread.

Every request logs in with a token (``t = md5(password + salt)`` with a fresh random
salt), never the password itself. Stream URLs are made *without* the login, so
playlists can save them; :meth:`SubsonicClient.authorise` adds it just before playing.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from pyamp import APP_NAME, __version__
from pyamp.core.models import Location, StreamUrl, Track

#: Token logins need API 1.13; 1.16.1 is what current servers speak.
API_VERSION = "1.16.1"
TIMEOUT_S = 15.0
MAX_BYTES = 20_000_000  # a large library's artist list is a few MB at most

type Json = dict[str, Any]


class SubsonicError(Exception):
    """The server refused a request (wrong login, missing item, ...) or isn't Subsonic."""


@dataclass(frozen=True, slots=True)
class Item:
    """Something to browse: an artist, album or playlist."""

    id: str
    name: str
    #: A short description shown next to the name: an album's year, a song count, ...
    detail: str = ""


def normalise_server(url: str) -> str:
    """The server's base URL without a trailing slash. Raises ``ValueError`` if not http(s)."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("The server address must start with http:// or https://")
    return url


class SubsonicClient:
    def __init__(
        self, server: str, username: str, password: str, *, timeout: float = TIMEOUT_S
    ) -> None:
        self.server = normalise_server(server)
        self.username = username
        self._password = password
        self._timeout = timeout

    # -- Browsing ----------------------------------------------------------- #

    def ping(self) -> None:
        """Check the address and login (raises :class:`SubsonicError` or ``OSError``)."""
        self._call("ping")

    def artists(self) -> list[Item]:
        indexes = self._call("getArtists").get("artists", {}).get("index", [])
        return [
            Item(str(a["id"]), a.get("name", ""), _count(a.get("albumCount"), "album"))
            for index in indexes
            for a in index.get("artist", [])
        ]

    def albums(self, artist_id: str) -> list[Item]:
        albums = self._call("getArtist", id=artist_id).get("artist", {}).get("album", [])
        return [_album(a) for a in albums]

    def album_tracks(self, album_id: str) -> list[Track]:
        songs = self._call("getAlbum", id=album_id).get("album", {}).get("song", [])
        return [self.track(s) for s in songs]

    def playlists(self) -> list[Item]:
        playlists = self._call("getPlaylists").get("playlists", {}).get("playlist", [])
        return [
            Item(str(p["id"]), p.get("name", ""), _count(p.get("songCount"), "song"))
            for p in playlists
        ]

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        entries = self._call("getPlaylist", id=playlist_id).get("playlist", {}).get("entry", [])
        return [self.track(s) for s in entries]

    def search(self, query: str) -> tuple[list[Item], list[Track]]:
        """Albums and songs matching ``query``."""
        result = self._call(
            "search3", query=query, artistCount=0, albumCount=50, songCount=200
        ).get("searchResult3", {})
        return (
            [_album(a) for a in result.get("album", [])],
            [self.track(s) for s in result.get("song", [])],
        )

    # -- Playing ------------------------------------------------------------ #

    def stream_url(self, song_id: str) -> StreamUrl:
        """Where a song plays from, without the login (safe to save in a playlist)."""
        query = urlencode({"id": song_id, "v": API_VERSION, "c": APP_NAME})
        return StreamUrl(f"{self.server}/rest/stream?{query}")

    def track(self, song: Mapping[str, Any]) -> Track:
        return Track(
            location=self.stream_url(str(song["id"])),
            title=song.get("title") or str(song["id"]),
            artist=song.get("artist") or "",
            album=song.get("album") or "",
            duration_ms=int(song.get("duration") or 0) * 1000,
        )

    def authorise(self, location: Location) -> Location:
        """``location`` with a fresh login added, if it's a URL on this server's API."""
        if not isinstance(location, StreamUrl) or not self._owns(location.url):
            return location
        return StreamUrl(f"{location.url}&{urlencode(self._login())}")

    def _owns(self, url: str) -> bool:
        if not url.startswith(f"{self.server}/rest/"):
            return False
        query = urlsplit(url).query
        return "?" in url and not any(p.startswith("u=") for p in query.split("&"))

    # -- Requests ----------------------------------------------------------- #

    def _login(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5((self._password + salt).encode(), usedforsecurity=False)
        return {"u": self.username, "t": token.hexdigest(), "s": salt}

    def _call(self, method: str, **params: object) -> Json:
        query = urlencode({**params, "v": API_VERSION, "c": APP_NAME, "f": "json", **self._login()})
        request = urllib.request.Request(
            f"{self.server}/rest/{method}?{query}",
            headers={"User-Agent": f"{APP_NAME}/{__version__}"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise SubsonicError("The server's reply was too large")
        try:
            body = json.loads(data)["subsonic-response"]
        except (ValueError, KeyError, TypeError):
            raise SubsonicError("That address doesn't look like a Subsonic server") from None
        if body.get("status") != "ok":
            error = body.get("error") or {}
            raise SubsonicError(error.get("message") or "The server refused the request")
        return dict(body)


def _count(value: object, noun: str) -> str:
    if not isinstance(value, int):
        return ""
    return f"{value} {noun}" + ("" if value == 1 else "s")


def _album(album: Mapping[str, Any]) -> Item:
    year = album.get("year")
    artist = album.get("artist") or ""
    detail = " · ".join(str(part) for part in (artist, year) if part)
    return Item(str(album["id"]), album.get("name") or album.get("title") or "", detail)
