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
"""The media library's index: the folders and files the user added, and their tags.

Kept in one SQLite file (the standard library's :mod:`sqlite3`). PyAmp only reads the
music files; nothing here changes or moves them. No Qt here: the index is brought up
to date by :class:`pyamp.library.scanner.LibraryScanner`, on a worker thread.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import platformdirs

from pyamp import APP_NAME
from pyamp.core.models import ReplayGain, Track

log = logging.getLogger(__name__)

FILE_NAME = "library.sqlite3"

#: One script per schema version: ``_MIGRATIONS[n]`` takes version n to n + 1.
_MIGRATIONS = (
    """
    CREATE TABLE sources (path TEXT PRIMARY KEY) WITHOUT ROWID;
    CREATE TABLE tracks (
        path TEXT PRIMARY KEY,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL,
        title TEXT NOT NULL,
        artist TEXT NOT NULL,
        album TEXT NOT NULL,
        album_artist TEXT NOT NULL,
        genre TEXT NOT NULL,
        year INTEGER NOT NULL,
        track_number INTEGER NOT NULL,
        disc_number INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        track_gain REAL,
        track_peak REAL,
        album_gain REAL,
        album_peak REAL,
        -- Case-folded, for grouping and browsing: an album's artist is its album
        -- artist, else its artist.
        artist_key TEXT NOT NULL,
        album_key TEXT NOT NULL,
        genre_key TEXT NOT NULL,
        search_text TEXT NOT NULL
    );
    CREATE INDEX tracks_by_artist ON tracks (artist_key, album_key);
    CREATE INDEX tracks_by_genre ON tracks (genre_key);
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)


class _TrackRow(NamedTuple):
    """A track as :data:`_SELECT` returns it."""

    path: str
    title: str
    artist: str
    album: str
    album_artist: str
    genre: str
    year: int
    track_number: int
    disc_number: int
    duration_ms: int
    track_gain: float | None
    track_peak: float | None
    album_gain: float | None
    album_peak: float | None


_SELECT = f"SELECT {', '.join(_TrackRow._fields)} FROM tracks"
_KEY_COLUMNS = ("artist_key", "album_key", "genre_key", "search_text")
_STORED_COLUMNS = (*_TrackRow._fields, "mtime_ns", "size", *_KEY_COLUMNS)
#: The artist that groups a track: its album artist, else its artist.
_GROUP_ARTIST = "CASE WHEN album_artist != '' THEN album_artist ELSE artist END"
_PUT = (
    f"INSERT OR REPLACE INTO tracks ({', '.join(_STORED_COLUMNS)})"
    f" VALUES ({', '.join('?' * len(_STORED_COLUMNS))})"
)


def default_path() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME.lower())) / FILE_NAME


def normalise(path: Path) -> Path:
    """Absolute, without ``..`` or doubled separators (symlinks are left alone)."""
    return Path(os.path.abspath(path))


def is_within(path: Path, source: Path) -> bool:
    """Whether ``path`` is ``source`` itself or somewhere inside it."""
    return path.is_relative_to(source)


@dataclass(frozen=True, slots=True)
class FileStamp:
    """Tells a changed file from an unchanged one without reading it."""

    mtime_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> FileStamp:
        stat = path.stat()
        return cls(stat.st_mtime_ns, stat.st_size)


class GroupKind(StrEnum):
    ARTIST = "artist"
    ALBUM = "album"
    GENRE = "genre"
    FOLDER = "folder"


@dataclass(frozen=True, slots=True)
class Group:
    """An artist, album, genre or folder of the library, to browse by.

    Artists are album artists where tracks have one (so a compilation is one entry),
    and names that differ only in case are one group.
    """

    kind: GroupKind
    #: The artist, album title or genre as tagged (empty: untagged), or a source's path.
    name: str
    #: Albums only: whose album it is.
    artist: str = ""
    #: How many tracks it has.
    count: int = 0


class CatalogError(Exception):
    """The library file exists but this version of PyAmp can't use it."""


class Catalog:
    """The library's folders and files, and a track for every audio file in them.

    Thread-safe: one connection, used under a lock, so a scanner on a worker thread and
    the UI on the main thread can share it. Each call is its own transaction.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()
        self._lock = threading.Lock()
        self._db = self._open()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- Sources: what the user added ------------------------------------------------ #

    def sources(self) -> list[Path]:
        """The folders and files in the library, in path order."""
        with self._lock:
            return self._sources()

    def add_source(self, path: Path) -> bool:
        """Add a folder or a file; False if the library already has it (or a folder it's in).

        Folders and files already in the library that are inside a new folder are
        replaced by it. Their tracks are read again only if they have changed.
        """
        path = normalise(path)
        with self._lock, self._db:
            current = self._sources()
            if any(is_within(path, source) for source in current):
                return False
            inside = [(str(source),) for source in current if is_within(source, path)]
            self._db.executemany("DELETE FROM sources WHERE path = ?", inside)
            self._db.execute("INSERT INTO sources (path) VALUES (?)", (str(path),))
        return True

    def remove_source(self, path: Path) -> None:
        """Forget a folder or file, and the tracks no other source brings in."""
        path = normalise(path)
        with self._lock, self._db:
            self._db.execute("DELETE FROM sources WHERE path = ?", (str(path),))
            remaining = self._sources()
            orphans = [
                (str(track),)
                for track in self._track_paths()
                if not any(is_within(track, source) for source in remaining)
            ]
            self._db.executemany("DELETE FROM tracks WHERE path = ?", orphans)

    # -- Tracks ---------------------------------------------------------------------- #

    def __len__(self) -> int:
        with self._lock:
            (count,) = self._db.execute("SELECT COUNT(*) FROM tracks").fetchone()
        return int(count)

    def stamps(self) -> dict[Path, FileStamp]:
        """Each indexed file and how it looked when its tags were read."""
        with self._lock:
            rows = self._db.execute("SELECT path, mtime_ns, size FROM tracks").fetchall()
        return {Path(path): FileStamp(mtime_ns, size) for path, mtime_ns, size in rows}

    def put(self, entries: Iterable[tuple[Track, FileStamp]]) -> None:
        """Add or replace the tracks of these files."""
        rows = [_to_row(track, stamp) for track, stamp in entries]
        with self._lock, self._db:
            self._db.executemany(_PUT, rows)

    def remove(self, paths: Iterable[Path]) -> None:
        rows = [(str(path),) for path in paths]
        with self._lock, self._db:
            self._db.executemany("DELETE FROM tracks WHERE path = ?", rows)

    def tracks(self, search: str = "", group: Group | None = None) -> list[Track]:
        """The tracks in ``group`` (default: all) matching ``search``, in album order.

        Every word of ``search`` must match somewhere in the title, artist, album,
        album artist, genre or file name, ignoring case.
        """
        conditions = [_LIKE] * len(words := search.casefold().split())
        params = [f"%{_escape_like(word)}%" for word in words]
        if group is not None:
            condition, group_params = _group_condition(group)
            conditions.append(condition)
            params.extend(group_params)
        query = f"{_SELECT} WHERE {' AND '.join(conditions)}" if conditions else _SELECT
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return sorted((_from_row(_TrackRow._make(row)) for row in rows), key=album_order)

    def groups(self, kind: GroupKind) -> list[Group]:
        """Every artist, album, genre or folder with its track count, by name.

        Untagged tracks make an artist without a name; albums and genres only list
        tagged ones.
        """
        if kind is GroupKind.FOLDER:
            return [
                Group(kind, str(source), count=self._count(Group(kind, str(source))))
                for source in self.sources()
            ]
        with self._lock:
            rows = self._db.execute(_GROUP_QUERIES[kind]).fetchall()
        groups = [Group(kind, name, artist, count) for name, artist, count in rows]
        return sorted(groups, key=lambda g: (g.name.casefold(), g.artist.casefold()))

    def _count(self, group: Group) -> int:
        condition, params = _group_condition(group)
        with self._lock:
            (count,) = self._db.execute(
                f"SELECT COUNT(*) FROM tracks WHERE {condition}", params
            ).fetchone()
        return int(count)

    # -- Storage --------------------------------------------------------------------- #

    def _sources(self) -> list[Path]:
        rows = self._db.execute("SELECT path FROM sources ORDER BY path").fetchall()
        return [Path(path) for (path,) in rows]

    def _track_paths(self) -> list[Path]:
        return [Path(path) for (path,) in self._db.execute("SELECT path FROM tracks")]

    def _open(self) -> sqlite3.Connection:
        """Open the library, setting aside (not deleting) a file that can't be read."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._connect()
        except (sqlite3.DatabaseError, CatalogError) as exc:
            aside = self.path.with_name(f"{self.path.name}.unusable")
            log.warning("Starting a new library; kept the old one as %s: %s", aside, exc)
            self.path.replace(aside)
            return self._connect()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)  # guarded by self._lock
        try:
            (version,) = db.execute("PRAGMA user_version").fetchone()
            if version > SCHEMA_VERSION:
                raise CatalogError(f"made by a newer PyAmp (schema {version})")
            for number, script in enumerate(_MIGRATIONS[version:], start=version + 1):
                db.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
        except BaseException:
            db.close()
            raise
        return db


def album_order(track: Track) -> tuple[str, str, int, int, str, str]:
    """Sort key: by album artist (or artist), album, disc and track number, ignoring case."""
    return (
        (track.album_artist or track.artist).casefold(),
        track.album.casefold(),
        track.disc_number,
        track.track_number,
        track.title.casefold(),
        str(track.location),
    )


_LIKE = "search_text LIKE ? ESCAPE '\\'"
_GROUP_QUERIES = {
    GroupKind.ARTIST: f"SELECT MIN({_GROUP_ARTIST}), '', COUNT(*) FROM tracks GROUP BY artist_key",
    GroupKind.ALBUM: f"SELECT MIN(album), MIN({_GROUP_ARTIST}), COUNT(*) FROM tracks"
    " WHERE album_key != '' GROUP BY artist_key, album_key",
    GroupKind.GENRE: "SELECT MIN(genre), '', COUNT(*) FROM tracks"
    " WHERE genre_key != '' GROUP BY genre_key",
}


def _group_condition(group: Group) -> tuple[str, list[str]]:
    """A WHERE condition (and its parameters) for the tracks in ``group``."""
    match group.kind:
        case GroupKind.ARTIST:
            return "artist_key = ?", [group.name.casefold()]
        case GroupKind.ALBUM:
            return "artist_key = ? AND album_key = ?", [
                group.artist.casefold(),
                group.name.casefold(),
            ]
        case GroupKind.GENRE:
            return "genre_key = ?", [group.name.casefold()]
        case GroupKind.FOLDER:
            inside = f"{_escape_like(os.path.join(group.name, ''))}%"  # ends in a separator
            return "(path = ? OR path LIKE ? ESCAPE '\\')", [group.name, inside]


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_text(track: Track) -> str:
    """What searches look in: title, artists, album, genre and file name, case-folded."""
    fields = (track.title, track.artist, track.album, track.album_artist, track.genre)
    return "\n".join((*fields, track.name)).casefold()


def _to_row(track: Track, stamp: FileStamp) -> tuple[object, ...]:
    """The values of :data:`_STORED_COLUMNS` for a track."""
    if not isinstance(track.location, Path):
        raise ValueError(f"only files belong in the library, not {track.location}")
    gain = track.replay_gain or ReplayGain()
    row = _TrackRow(
        path=str(track.location),
        title=track.title,
        artist=track.artist,
        album=track.album,
        album_artist=track.album_artist,
        genre=track.genre,
        year=track.year,
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration_ms=track.duration_ms,
        track_gain=gain.track_gain,
        track_peak=gain.track_peak,
        album_gain=gain.album_gain,
        album_peak=gain.album_peak,
    )
    keys = (
        (track.album_artist or track.artist).casefold(),
        track.album.casefold(),
        track.genre.casefold(),
        search_text(track),
    )
    return (*row, stamp.mtime_ns, stamp.size, *keys)


def _from_row(row: _TrackRow) -> Track:
    replay_gain = ReplayGain(row.track_gain, row.track_peak, row.album_gain, row.album_peak)
    return Track(
        location=Path(row.path),
        title=row.title,
        artist=row.artist,
        album=row.album,
        album_artist=row.album_artist,
        genre=row.genre,
        year=row.year,
        track_number=row.track_number,
        disc_number=row.disc_number,
        duration_ms=row.duration_ms,
        replay_gain=None if replay_gain == ReplayGain() else replay_gain,
    )
