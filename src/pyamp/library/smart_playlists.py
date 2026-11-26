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

"""Smart playlists: saved rules that pick tracks from the library, and where they're kept.

A smart playlist holds no tracks; :meth:`pyamp.library.catalog.Catalog.smart_tracks`
finds what matches its rules each time it's listed. They are kept in one JSON file, so
no music file and no saved (``.m3u8``) playlist is touched. No Qt here.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import platformdirs

from pyamp import APP_NAME, files
from pyamp.library.saved_playlists import MAX_NAME_LENGTH, PlaylistNameError

log = logging.getLogger(__name__)

FILE_NAME = "smart_playlists.json"
FORMAT_VERSION = 1
#: Bounds that keep values sensible (and inside SQLite's 64-bit integers).
MAX_NUMBER = 1_000_000
MAX_DAYS = 36_500
MAX_LIMIT = 100_000


class FieldKind(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    FILE_TYPE = "file_type"
    FOLDER = "folder"
    DATE = "date"


class RuleField(StrEnum):
    TITLE = "title"
    ARTIST = "artist"
    ALBUM = "album"
    ALBUM_ARTIST = "album_artist"
    GENRE = "genre"
    YEAR = "year"
    LENGTH = "length"  # seconds
    FILE_TYPE = "file_type"  # the file name's extension, e.g. "flac"
    FOLDER = "folder"
    ADDED = "added"  # days

    @property
    def kind(self) -> FieldKind:
        return _FIELD_KINDS[self]

    @property
    def label(self) -> str:
        return _FIELD_LABELS[self]


class Operator(StrEnum):
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    IS = "is"
    IS_NOT = "is_not"
    STARTS_WITH = "starts_with"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    BETWEEN = "between"
    IN_FOLDER = "in"
    NOT_IN_FOLDER = "not_in"
    IN_LAST = "in_last"
    NOT_IN_LAST = "not_in_last"

    @property
    def label(self) -> str:
        return _OPERATOR_LABELS[self]


class SortOrder(StrEnum):
    ALBUM = "album"
    RANDOM = "random"
    TITLE = "title"
    ARTIST = "artist"
    YEAR = "year"
    NEWEST = "newest"
    LONGEST = "longest"
    SHORTEST = "shortest"

    @property
    def label(self) -> str:
        return _SORT_LABELS[self]


_FIELD_KINDS = {
    RuleField.TITLE: FieldKind.TEXT,
    RuleField.ARTIST: FieldKind.TEXT,
    RuleField.ALBUM: FieldKind.TEXT,
    RuleField.ALBUM_ARTIST: FieldKind.TEXT,
    RuleField.GENRE: FieldKind.TEXT,
    RuleField.YEAR: FieldKind.NUMBER,
    RuleField.LENGTH: FieldKind.NUMBER,
    RuleField.FILE_TYPE: FieldKind.FILE_TYPE,
    RuleField.FOLDER: FieldKind.FOLDER,
    RuleField.ADDED: FieldKind.DATE,
}
_FIELD_LABELS = {
    RuleField.TITLE: "Title",
    RuleField.ARTIST: "Artist",
    RuleField.ALBUM: "Album",
    RuleField.ALBUM_ARTIST: "Album artist",
    RuleField.GENRE: "Genre",
    RuleField.YEAR: "Year",
    RuleField.LENGTH: "Length",
    RuleField.FILE_TYPE: "File type",
    RuleField.FOLDER: "Folder",
    RuleField.ADDED: "Date added",
}
#: The operators each kind of field can use, the first being the default.
OPERATORS: Mapping[FieldKind, tuple[Operator, ...]] = {
    FieldKind.TEXT: (
        Operator.CONTAINS,
        Operator.NOT_CONTAINS,
        Operator.IS,
        Operator.IS_NOT,
        Operator.STARTS_WITH,
    ),
    FieldKind.NUMBER: (
        Operator.IS,
        Operator.IS_NOT,
        Operator.AT_LEAST,
        Operator.AT_MOST,
        Operator.BETWEEN,
    ),
    FieldKind.FILE_TYPE: (Operator.IS, Operator.IS_NOT),
    FieldKind.FOLDER: (Operator.IN_FOLDER, Operator.NOT_IN_FOLDER),
    FieldKind.DATE: (Operator.IN_LAST, Operator.NOT_IN_LAST),
}
_OPERATOR_LABELS = {
    Operator.CONTAINS: "contains",
    Operator.NOT_CONTAINS: "doesn't contain",
    Operator.IS: "is",
    Operator.IS_NOT: "is not",
    Operator.STARTS_WITH: "starts with",
    Operator.AT_LEAST: "is at least",
    Operator.AT_MOST: "is at most",
    Operator.BETWEEN: "is between",
    Operator.IN_FOLDER: "is in",
    Operator.NOT_IN_FOLDER: "is not in",
    Operator.IN_LAST: "is in the last",
    Operator.NOT_IN_LAST: "is not in the last",
}
_SORT_LABELS = {
    SortOrder.ALBUM: "Album order",
    SortOrder.RANDOM: "Random",
    SortOrder.TITLE: "Title",
    SortOrder.ARTIST: "Artist",
    SortOrder.YEAR: "Year (oldest first)",
    SortOrder.NEWEST: "Most recently added",
    SortOrder.LONGEST: "Longest first",
    SortOrder.SHORTEST: "Shortest first",
}
#: Text operators that need something to look for ("is" may be empty: untagged).
_NEEDS_TEXT = {Operator.CONTAINS, Operator.NOT_CONTAINS, Operator.STARTS_WITH}


@dataclass(frozen=True, slots=True)
class Rule:
    """One condition, e.g. genre contains "jazz", or year is between 1990 and 1999.

    ``value`` is text for text, file type and folder fields and a whole number for the
    others (seconds for length, days for date added); ``to`` is the upper bound of
    "between".
    """

    field: RuleField
    operator: Operator
    value: str | int = ""
    to: int = 0


@dataclass(frozen=True, slots=True)
class SmartPlaylist:
    name: str
    rules: tuple[Rule, ...] = ()
    #: Whether a track must match every rule, or any one. No rules match everything.
    match_all: bool = True
    sort: SortOrder = SortOrder.ALBUM
    #: The most tracks it lists; 0 for no limit.
    limit: int = 0


def check_name(name: str) -> str:
    """The name, trimmed, if a smart playlist can have it."""
    name = name.strip()
    if not name:
        raise PlaylistNameError("A smart playlist needs a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise PlaylistNameError(f"Names can be at most {MAX_NAME_LENGTH} characters.")
    if any(ord(c) < 32 for c in name):
        raise PlaylistNameError("Names can't contain control characters.")
    return name


def problem(playlist: SmartPlaylist) -> str:
    """What's wrong with ``playlist``, for the user; "" if it can be saved."""
    try:
        check_name(playlist.name)
    except PlaylistNameError as exc:
        return str(exc)
    if not 0 <= playlist.limit <= MAX_LIMIT:
        return f"The limit must be between 0 and {MAX_LIMIT}."
    for number, rule in enumerate(playlist.rules, start=1):
        if message := rule_problem(rule):
            return f"Rule {number}: {message}"
    return ""


def rule_problem(rule: Rule) -> str:
    """What's wrong with ``rule``, for the user; "" if nothing is."""
    kind = rule.field.kind
    if rule.operator not in OPERATORS[kind]:
        return f'{rule.field.label} can\'t use "{rule.operator.label}".'
    if kind in (FieldKind.NUMBER, FieldKind.DATE):
        return _number_problem(rule)
    if not isinstance(rule.value, str):
        return "expects text."
    if not rule.value.strip() and (kind is not FieldKind.TEXT or rule.operator in _NEEDS_TEXT):
        return "needs something to match."
    return ""


def _number_problem(rule: Rule) -> str:
    lowest, highest = (1, MAX_DAYS) if rule.field.kind is FieldKind.DATE else (0, MAX_NUMBER)
    unit = "a number of days" if rule.field.kind is FieldKind.DATE else "a number"
    if not isinstance(rule.value, int) or not lowest <= rule.value <= highest:
        return f"expects {unit} from {lowest} to {highest}."
    if rule.operator is Operator.BETWEEN and not rule.value <= rule.to <= highest:
        return "the second number must be at least the first."
    return ""


def file_type(value: str) -> str:
    """A file type as rules compare it: "FLAC", ".flac" and "flac" are all "flac"."""
    return value.strip().lstrip(".").casefold()


# -- Storage ------------------------------------------------------------------------- #


def default_file() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME.lower())) / FILE_NAME


class SmartPlaylists:
    """``smart_playlists.json``: ``{"version": 1, "playlists": [...]}``. Names compare
    case-insensitively.

    A playlist this version can't read (e.g. one made with a rule a newer PyAmp added)
    is left out of :meth:`names`, but kept in the file when others are saved.
    """

    def __init__(self, file: Path | None = None) -> None:
        self.file = file or default_file()

    def names(self) -> list[str]:
        return sorted((p.name for p in self._playlists()), key=str.casefold)

    def get(self, name: str) -> SmartPlaylist | None:
        wanted = name.strip().casefold()
        return next((p for p in self._playlists() if p.name.casefold() == wanted), None)

    def save(self, playlist: SmartPlaylist, replacing: str | None = None) -> None:
        """Add ``playlist``, or replace the one called ``replacing`` (which may rename it)."""
        if message := problem(playlist):
            raise PlaylistNameError(message)  # the dialog checks first; this is a guard
        name = check_name(playlist.name)
        playlist = replace(playlist, name=name)
        old = (replacing or "").strip().casefold()
        raw = self._read()
        if any(_name(entry).casefold() == name.casefold() != old for entry in raw):
            raise PlaylistNameError(f'There is already a smart playlist called "{name}".')
        kept = [e for e in raw if _name(e).casefold() not in {old, name.casefold()}]
        self._write([*kept, to_json(playlist)])

    def unused_name(self, base: str) -> str:
        """``base``, or "base 2", "base 3"... if a smart playlist already has it."""
        taken = {_name(entry).casefold() for entry in self._read()}
        name, n = base, 1
        while name.casefold() in taken:
            n += 1
            name = f"{base} {n}"
        return name

    def delete(self, name: str) -> None:
        wanted = name.strip().casefold()
        self._write([e for e in self._read() if _name(e).casefold() != wanted])

    def _playlists(self) -> list[SmartPlaylist]:
        playlists = []
        for entry in self._read():
            try:
                playlists.append(from_json(entry))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning(
                    "Skipping a smart playlist PyAmp can't read (%r): %s", _name(entry), exc
                )
        return playlists

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(files.read_text(self.file))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable smart playlists %s: %s", self.file, exc)
            return []
        entries = raw.get("playlists") if isinstance(raw, dict) else None
        if not isinstance(entries, list):
            log.warning("Ignoring unreadable smart playlists %s", self.file)
            return []
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str)]

    def _write(self, entries: list[dict[str, object]]) -> None:
        payload = {"version": FORMAT_VERSION, "playlists": entries}
        files.write_text(self.file, json.dumps(payload, indent=2, ensure_ascii=False))


def _name(entry: Mapping[str, object]) -> str:
    return str(entry.get("name", ""))


def to_json(playlist: SmartPlaylist) -> dict[str, object]:
    return {
        "name": playlist.name,
        "match": "all" if playlist.match_all else "any",
        "rules": [
            {"field": r.field.value, "operator": r.operator.value, "value": r.value, "to": r.to}
            for r in playlist.rules
        ],
        "sort": playlist.sort.value,
        "limit": playlist.limit,
    }


def from_json(entry: Mapping[str, object]) -> SmartPlaylist:
    """The playlist ``entry`` describes; ValueError, TypeError or KeyError if it can't be
    read (e.g. a rule this version doesn't know)."""
    rules = entry.get("rules", [])
    if not isinstance(rules, list):
        raise TypeError("rules must be a list")
    match = entry.get("match", "all")
    if match not in ("all", "any"):
        raise ValueError(f"unknown match {match!r}")
    playlist = SmartPlaylist(
        name=str(entry["name"]),
        rules=tuple(_rule(rule) for rule in rules),
        match_all=match == "all",
        sort=SortOrder(str(entry.get("sort", SortOrder.ALBUM))),
        limit=_int(entry.get("limit", 0)),
    )
    if message := problem(playlist):
        raise ValueError(message)
    return playlist


def _rule(raw: object) -> Rule:
    if not isinstance(raw, dict):
        raise TypeError("a rule must be an object")
    field_ = RuleField(raw["field"])
    value = raw.get("value", "")
    if field_.kind in (FieldKind.NUMBER, FieldKind.DATE):
        value = _int(value)
    elif not isinstance(value, str):
        raise TypeError(f"{field_.label} expects text")
    return Rule(field_, Operator(raw["operator"]), value, _int(raw.get("to", 0)))


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected a whole number, not {value!r}")
    return value
