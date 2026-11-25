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
"""Brings the library's index up to date with the files on disk."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, FileStamp, is_within
from pyamp.library.metadata import MetadataReader, audio_files, is_audio_file

log = logging.getLogger(__name__)

#: Tracks are written to the index this many at a time, so a cancelled scan keeps
#: what it has read and the index is never locked for long.
BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class ScanSummary:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    #: Sources that weren't there (e.g. an unplugged drive). Their tracks are kept.
    missing_sources: tuple[Path, ...] = ()
    #: Stopped before the end: nothing was removed, as not every file was seen.
    cancelled: bool = False


def _never() -> bool:
    return False


class LibraryScanner:
    """Synchronous and toolkit-agnostic, like :class:`pyamp.library.loader.TrackLoader`.

    A rescan only reads the tags of files that are new or have changed (by size and
    modification time), and forgets files that have gone.
    """

    def __init__(self, catalog: Catalog, reader: MetadataReader) -> None:
        self._catalog = catalog
        self._reader = reader

    def scan(self, should_stop: Callable[[], bool] = _never) -> ScanSummary:
        """Scan every source; ``should_stop`` is checked before each file."""
        known = self._catalog.stamps()
        sources = self._catalog.sources()
        missing = tuple(source for source in sources if not source.exists())
        for source in missing:
            log.info("Library source %s isn't there; keeping its tracks", source)
        available = [source for source in sources if source not in missing]

        seen: set[Path] = set()
        changed: list[tuple[Track, FileStamp]] = []
        added = updated = 0
        for path in _audio_files(available):
            if should_stop():
                self._catalog.put(changed)
                unchanged = len(seen) - added - updated
                return ScanSummary(added, updated, 0, unchanged, missing, cancelled=True)
            stamp = _stamp(path)
            if stamp is None:
                continue
            seen.add(path)
            before = known.get(path)
            if before == stamp:
                continue
            changed.append((self._reader.read(path), stamp))
            if before is None:
                added += 1
            else:
                updated += 1
            if len(changed) >= BATCH_SIZE:
                self._catalog.put(changed)
                changed.clear()
        self._catalog.put(changed)

        gone = [
            path
            for path in known
            if path not in seen and not any(is_within(path, source) for source in missing)
        ]
        self._catalog.remove(gone)
        unchanged = len(seen) - added - updated
        return ScanSummary(added, updated, len(gone), unchanged, missing)


def _audio_files(sources: Iterable[Path]) -> Iterator[Path]:
    for source in sources:
        if source.is_dir():
            yield from audio_files(source)
        elif is_audio_file(source):
            yield source


def _stamp(path: Path) -> FileStamp | None:
    try:
        return FileStamp.of(path)
    except OSError as exc:  # gone or unreadable since it was listed
        log.debug("Skipping %s: %s", path, exc)
        return None
