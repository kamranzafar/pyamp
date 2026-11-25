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

"""Reading track metadata from audio files.

Each file is parsed once, without mutagen's "easy" wrappers: those hide the
ReplayGain tags most taggers write (ID3 ``TXXX`` frames, MP4 freeform atoms).
Tags are read per tag family instead (ID3, MP4, ASF, Vorbis comments).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

import mutagen
from mutagen.asf import ASFTags
from mutagen.id3 import ID3
from mutagen.mp4 import MP4Tags
from mutagen.oggopus import OggOpus

from pyamp.core.models import ReplayGain, Track

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".wma"}
)

#: Our field names -> tag keys, per tag family.
_ID3_FRAMES = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "genre": "TCON",
    "year": "TDRC",  # mutagen upgrades ID3v2.3's TYER to it
    "track_number": "TRCK",
    "disc_number": "TPOS",
}
_MP4_KEYS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "album_artist": "aART",
    "genre": "\xa9gen",
    "year": "\xa9day",
}
#: MP4 keeps track and disc numbers as (number, total) pairs.
_MP4_NUMBER_KEYS = {"track_number": "trkn", "disc_number": "disk"}
_ASF_KEYS = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "album_artist": "WM/AlbumArtist",
    "genre": "WM/Genre",
    "year": "WM/Year",
    "track_number": "WM/TrackNumber",
    "disc_number": "WM/PartOfSet",
}
#: Vorbis comment names, most common first (they have no fixed spelling).
_VORBIS_KEYS = {
    "title": ("title",),
    "artist": ("artist",),
    "album": ("album",),
    "album_artist": ("albumartist", "album artist", "album_artist"),
    "genre": ("genre",),
    "year": ("date", "year"),
    "track_number": ("tracknumber",),
    "disc_number": ("discnumber",),
}

#: Opus R128 gains are relative to -23 LUFS; ReplayGain uses -18 LUFS.
_R128_TO_REPLAYGAIN_DB = 5.0
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_LEADING_INT = re.compile(r"\s*(\d+)")  # "3/12" -> 3
_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")  # "2003-05-01" -> 2003


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def audio_files(folder: Path) -> list[Path]:
    """The audio files in ``folder`` and its subfolders, in path order."""
    return sorted(p for p in folder.rglob("*") if p.is_file() and is_audio_file(p))


class MetadataReader(Protocol):
    def read(self, path: Path) -> Track: ...


class MutagenMetadataReader:
    """Reads tags via mutagen. Unreadable files still produce a usable Track."""

    def read(self, path: Path) -> Track:
        try:
            audio = mutagen.File(path)
        except (mutagen.MutagenError, OSError) as exc:
            log.debug("Could not read tags from %s: %s", path, exc)
            audio = None
        if audio is None:
            return Track.from_path(path)

        fields, gains = _read_tags(audio.tags)
        info = getattr(audio, "info", None)
        length = getattr(info, "length", 0) or 0
        return Track(
            location=path,
            title=fields.get("title") or path.stem,
            artist=fields.get("artist", ""),
            album=fields.get("album", ""),
            album_artist=fields.get("album_artist", ""),
            genre=fields.get("genre", ""),
            year=_year(fields.get("year")),
            track_number=_leading_int(fields.get("track_number")),
            disc_number=_leading_int(fields.get("disc_number")),
            duration_ms=int(length * 1000),
            replay_gain=_replay_gain(gains, opus=isinstance(audio, OggOpus)),
        )


# -- Tag families ------------------------------------------------------------ #

type Fields = dict[str, str]
"""Our field name (title, artist, album, album_artist, ...) -> raw value"""
type Gains = dict[str, str]
"""lower-case ReplayGain/R128 tag name (e.g. ``replaygain_track_gain``) -> raw value"""


def _read_tags(tags: Any) -> tuple[Fields, Gains]:
    if not tags:
        return {}, {}
    if isinstance(tags, ID3):
        return _id3(tags)
    if isinstance(tags, MP4Tags):
        return _mp4(tags)
    if isinstance(tags, ASFTags):
        return _asf(tags)
    return _vorbis(tags)


def _id3(tags: Any) -> tuple[Fields, Gains]:
    fields: Fields = {}
    for field, frame_id in _ID3_FRAMES.items():
        frame = tags.get(frame_id)
        if frame is not None:
            # TCON's genres turns ID3v1 numbers like "(17)" into names.
            fields[field] = _first(frame.genres if frame_id == "TCON" else frame.text)
    gains: Gains = {}
    for frame in tags.getall("TXXX"):
        if _is_gain_key(frame.desc.lower()) and frame.text:
            gains[frame.desc.lower()] = str(frame.text[0])
    # Older taggers use RVA2 frames instead; TXXX wins if both exist.
    for frame in tags.getall("RVA2"):
        kind = frame.desc.lower()
        if kind in {"track", "album"} and frame.channel == 1:  # master volume
            gains.setdefault(f"replaygain_{kind}_gain", str(frame.gain))
            gains.setdefault(f"replaygain_{kind}_peak", str(frame.peak))
    return _clean(fields), gains


def _mp4(tags: Any) -> tuple[Fields, Gains]:
    fields = {field: _first(tags.get(key)) for field, key in _MP4_KEYS.items()}
    for field, key in _MP4_NUMBER_KEYS.items():
        pairs = tags.get(key)
        if pairs:
            fields[field] = str(pairs[0][0])
    gains: Gains = {}
    for key, values in tags.items():
        name = key.split(":", 2)[-1].lower()  # "----:com.apple.iTunes:replaygain_track_gain"
        if key.startswith("----:") and _is_gain_key(name) and values:
            gains[name] = bytes(values[0]).decode("utf-8", "replace")
    return _clean(fields), gains


def _asf(tags: Any) -> tuple[Fields, Gains]:
    fields = {field: _first(tags.get(key)) for field, key in _ASF_KEYS.items()}
    gains = {k.lower(): _first(v) for k, v in tags.items() if _is_gain_key(k.lower())}
    return _clean(fields), gains


def _vorbis(tags: Any) -> tuple[Fields, Gains]:
    """Vorbis comments (FLAC, Ogg Vorbis/Opus/FLAC) and anything else dict-like."""

    def get(key: str) -> str:
        try:
            return _first(tags.get(key))
        except (KeyError, ValueError):
            return ""

    fields = {
        field: next((value for key in keys if (value := get(key))), "")
        for field, keys in _VORBIS_KEYS.items()
    }
    try:
        # .keys() is needed: iterating a Vorbis comment dict yields (key, value) pairs.
        keys = [k for k in tags.keys() if _is_gain_key(k.lower())]  # noqa: SIM118
    except (AttributeError, TypeError):
        keys = []
    return _clean(fields), {k.lower(): get(k) for k in keys}


# -- Helpers ------------------------------------------------------------------ #


def _first(values: Iterable[Any] | None) -> str:
    for value in values or ():
        return str(value).strip()
    return ""


def _clean(fields: Fields) -> Fields:
    return {k: v for k, v in fields.items() if v}


def _is_gain_key(name: str) -> bool:
    return name.startswith(("replaygain_", "r128_"))


def _number(raw: str | None) -> float | None:
    """Parse ``"-6.54 dB"``, ``"0.988"`` or ``"\u22126.5 dB"``; None if there is no number."""
    if not raw:
        return None
    match = _NUMBER.search(raw.replace("\u2212", "-"))  # tolerate a Unicode minus sign
    return float(match.group()) if match else None


def _leading_int(raw: str | None) -> int:
    """The number a track or disc tag starts with (``"3/12"`` -> 3); 0 if none."""
    match = _LEADING_INT.match(raw or "")
    return int(match.group(1)) if match else 0


def _year(raw: str | None) -> int:
    """The year in a date tag (``"2003"``, ``"2003-05-01"``); 0 if none."""
    match = _YEAR.search(raw or "")
    return int(match.group(1)) if match else 0


def _replay_gain(gains: Mapping[str, str], *, opus: bool) -> ReplayGain | None:
    track_gain = _number(gains.get("replaygain_track_gain"))
    album_gain = _number(gains.get("replaygain_album_gain"))
    if opus:
        # Opus files carry R128 gains (Q7.8 fixed point) instead of ReplayGain tags.
        r128_track = _number(gains.get("r128_track_gain"))
        r128_album = _number(gains.get("r128_album_gain"))
        if track_gain is None and r128_track is not None:
            track_gain = r128_track / 256 + _R128_TO_REPLAYGAIN_DB
        if album_gain is None and r128_album is not None:
            album_gain = r128_album / 256 + _R128_TO_REPLAYGAIN_DB
    replay_gain = ReplayGain(
        track_gain=track_gain,
        track_peak=_number(gains.get("replaygain_track_peak")),
        album_gain=album_gain,
        album_peak=_number(gains.get("replaygain_album_peak")),
    )
    return None if replay_gain == ReplayGain() else replay_gain
