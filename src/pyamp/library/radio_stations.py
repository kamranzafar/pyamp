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

"""The radio stations the library's Radio section lists, kept in ``radio_stations.json``.

A station is a name and the URL it was added with: the stream itself, or the ``.pls`` or
``.m3u`` playlist a station publishes (kept as it is, so the station keeps working when
the stream inside moves). No Qt here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from pyamp import APP_NAME, files
from pyamp.core.models import StreamUrl
from pyamp.library.saved_playlists import MAX_NAME_LENGTH

log = logging.getLogger(__name__)

FILE_NAME = "radio_stations.json"
FORMAT_VERSION = 1


class StationError(Exception):
    """A station can't be saved as it is; the message is meant for the user."""


@dataclass(frozen=True, slots=True)
class Station:
    name: str
    url: StreamUrl


def default_file() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME.lower())) / FILE_NAME


def check_name(name: str) -> str:
    """The name, trimmed, if a station can have it."""
    name = name.strip()
    if not name:
        raise StationError("A station needs a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise StationError(f"Names can be at most {MAX_NAME_LENGTH} characters.")
    if any(ord(c) < 32 for c in name):
        raise StationError("Names can't contain control characters.")
    return name


def check_url(text: str) -> StreamUrl:
    """The URL, trimmed, if a station can play it."""
    try:
        return StreamUrl(text.strip())
    except ValueError:
        raise StationError("Enter an http:// or https:// address.") from None


class RadioStations:
    """``radio_stations.json``: ``{"version": 1, "stations": [{"name", "url"}, ...]}``.
    Names compare case-insensitively.

    An entry this version can't read is left out of :meth:`stations`, but kept in the
    file when others are saved.
    """

    def __init__(self, file: Path | None = None) -> None:
        self.file = file or default_file()

    def stations(self) -> list[Station]:
        """Every station, by name."""
        stations = []
        for entry in self._read():
            try:
                stations.append(Station(str(entry["name"]), StreamUrl(str(entry["url"]))))
            except (KeyError, ValueError) as exc:
                log.warning("Skipping a radio station PyAmp can't read (%r): %s", entry, exc)
        return sorted(stations, key=lambda s: s.name.casefold())

    def get(self, name: str) -> Station | None:
        wanted = name.strip().casefold()
        return next((s for s in self.stations() if s.name.casefold() == wanted), None)

    def save(self, name: str, url: str, replacing: str | None = None) -> Station:
        """Add a station, or replace the one called ``replacing`` (which may rename it)."""
        station = Station(check_name(name), check_url(url))
        old = (replacing or "").strip().casefold()
        wanted = station.name.casefold()
        raw = self._read()
        if any(_name(entry).casefold() == wanted != old for entry in raw):
            raise StationError(f'There is already a station called "{station.name}".')
        kept = [e for e in raw if _name(e).casefold() not in {old, wanted}]
        self._write([*kept, {"name": station.name, "url": station.url.url}])
        return station

    def delete(self, name: str) -> None:
        wanted = name.strip().casefold()
        self._write([e for e in self._read() if _name(e).casefold() != wanted])

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(files.read_text(self.file))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable radio stations %s: %s", self.file, exc)
            return []
        entries = raw.get("stations") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            log.warning("Ignoring unreadable radio stations %s", self.file)
            return []
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)]

    def _write(self, entries: list[dict[str, object]]) -> None:
        payload = {"version": FORMAT_VERSION, "stations": entries}
        files.write_text(self.file, json.dumps(payload, indent=2, ensure_ascii=False))


def _name(entry: dict[str, object]) -> str:
    return str(entry.get("name", ""))
