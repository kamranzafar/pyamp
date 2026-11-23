"""Turns arbitrary user input (files, folders, playlist files) into Tracks."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from pyamp.core.models import Track
from pyamp.library.metadata import MetadataReader, is_audio_file
from pyamp.library.playlist_formats import PlaylistFormatRegistry

log = logging.getLogger(__name__)


class TrackLoader:
    """Synchronous and toolkit-agnostic.

    Holds no mutable state, so it is safe to call from a worker thread. For
    non-blocking use in the GUI, see :mod:`pyamp.ui.background_loader`.
    """

    def __init__(self, reader: MetadataReader, playlist_formats: PlaylistFormatRegistry) -> None:
        self._reader = reader
        self._formats = playlist_formats

    def load(self, paths: Iterable[Path]) -> list[Track]:
        """Expand folders and playlist files, then read metadata for every audio file."""
        return list(self.iter_load(paths))

    def iter_load(self, paths: Iterable[Path]) -> Iterator[Track]:
        """Like :meth:`load`, but lazily: one track at a time, so callers can stream or stop."""
        for path in self.discover(paths):
            yield self._reader.read(path)

    def discover(self, paths: Iterable[Path]) -> Iterator[Path]:
        for path in paths:
            if path.is_dir():
                yield from sorted(p for p in path.rglob("*") if p.is_file() and is_audio_file(p))
            elif self._formats.supports(path):
                try:
                    yield from (p for p in self._formats.read(path) if p.is_file())
                except OSError as exc:
                    log.warning("Could not read playlist %s: %s", path, exc)
            elif path.is_file() and is_audio_file(path):
                yield path
