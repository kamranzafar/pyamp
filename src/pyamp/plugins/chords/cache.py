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

"""Chord charts found before, kept in the user's cache folder so a song is only analysed
once: a file by its path, size and modification time; a song from a server by its address
(without the login details some servers add to every request)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import platformdirs

from pyamp import APP_NAME
from pyamp.core.models import StreamUrl
from pyamp.plugins.chords.analysis import VERSION, ChordChart

log = logging.getLogger(__name__)

#: Charts kept at most; the least recently used go first.
CACHE_LIMIT = 2000
#: A stream address's parameters that change from one request to the next (Subsonic's
#: login: user, password, token, salt; client and protocol version), left out of its key.
VOLATILE_PARAMETERS = frozenset({"u", "p", "t", "s", "v", "c"})


def stream_key(url: StreamUrl) -> str:
    """A song stream's address without the parameters that change between requests."""
    parts = urlparse(url.url)
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in VOLATILE_PARAMETERS
    )
    return urlunparse(parts._replace(query=urlencode(kept), fragment=""))


def default_directory() -> Path:
    return Path(platformdirs.user_cache_dir(APP_NAME.lower())) / "chords"


class ChordCache:
    """Charts found before, one small JSON file each."""

    def __init__(self, directory: Path | None = None, limit: int = CACHE_LIMIT) -> None:
        self.directory = directory or default_directory()
        self._limit = limit

    def _file(self, location: Path | StreamUrl) -> Path | None:
        if isinstance(location, StreamUrl):
            key = f"stream|{stream_key(location)}|{VERSION}"
        else:
            try:
                info = location.stat()
            except OSError:
                return None
            key = f"{location.resolve()}|{info.st_size}|{info.st_mtime_ns}|{VERSION}"
        return self.directory / f"{hashlib.sha1(key.encode()).hexdigest()}.json"

    def load(self, location: Path | StreamUrl) -> ChordChart | None:
        file = self._file(location)
        if file is None:
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        with contextlib.suppress(OSError):
            file.touch()  # recently used: kept longer
        return ChordChart.from_json(data) if isinstance(data, dict) else None

    def save(self, location: Path | StreamUrl, chart: ChordChart) -> None:
        file = self._file(location)
        if file is None:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(chart.to_json()), encoding="utf-8")
        except OSError as exc:
            log.info("Couldn't keep the chords for %s: %s", location.name, exc)
            return
        self._prune()

    def _prune(self) -> None:
        try:
            files = list(self.directory.glob("*.json"))
            if len(files) <= self._limit:
                return
            files.sort(key=lambda f: f.stat().st_mtime)
        except OSError:
            return
        for old in files[: len(files) - self._limit]:
            with contextlib.suppress(OSError):
                old.unlink()
