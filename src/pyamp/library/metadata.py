"""Reading track metadata from audio files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import mutagen

from pyamp.core.models import Track

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".wma"}
)


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


class MetadataReader(Protocol):
    def read(self, path: Path) -> Track: ...


class MutagenMetadataReader:
    """Reads tags via mutagen. Unreadable files still produce a usable Track."""

    def read(self, path: Path) -> Track:
        try:
            audio = mutagen.File(path, easy=True)
        except (mutagen.MutagenError, OSError) as exc:
            log.debug("Could not read tags from %s: %s", path, exc)
            audio = None
        if audio is None:
            return Track.from_path(path)

        tags: Any = audio.tags or {}

        def first(key: str) -> str:
            try:
                values = tags.get(key)
            except (KeyError, ValueError):
                return ""
            return str(values[0]).strip() if values else ""

        info = getattr(audio, "info", None)
        length = getattr(info, "length", 0) or 0
        return Track(
            path=path,
            title=first("title") or path.stem,
            artist=first("artist"),
            album=first("album"),
            duration_ms=int(length * 1000),
        )
