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

from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mutagen
import pytest
from mutagen._vorbis import VCommentDict
from mutagen.aiff import AIFF
from mutagen.asf import ASFTags, ASFUnicodeAttribute  # type: ignore[attr-defined]
from mutagen.id3 import RVA2, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX
from mutagen.mp4 import MP4FreeForm, MP4Tags
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE

from pyamp.core.models import ReplayGain, Track
from pyamp.library.metadata import MutagenMetadataReader, audio_files, is_audio_file

reader = MutagenMetadataReader()


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return path


def write_aiff(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    """Minimal 16-bit mono AIFF (the stdlib ``aifc`` module was removed in Python 3.13)."""
    frames = int(rate * seconds)
    exponent = rate.bit_length() - 1
    rate_80bit = struct.pack(">HQ", 16383 + exponent, rate << (63 - exponent))
    comm = struct.pack(">hIh", 1, frames, 16) + rate_80bit
    ssnd = struct.pack(">II", 0, 0) + b"\0\0" * frames
    body = b"AIFF"
    for chunk_id, data in ((b"COMM", comm), (b"SSND", ssnd)):
        body += chunk_id + struct.pack(">I", len(data)) + data
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return path


def add_id3(path: Path, file_type: Any, **frames: str) -> None:
    classes: dict[str, Any] = {"title": TIT2, "artist": TPE1, "album": TALB}
    audio = file_type(path)
    audio.add_tags()
    for key, text in frames.items():
        audio.tags.add(classes[key](encoding=3, text=text))
    audio.save()


def fake_mutagen_file(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    monkeypatch.setattr(mutagen, "File", lambda path, **_: result)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.mp3", True), ("B.FLAC", True), ("c.Opus", True), ("d.txt", False), ("noext", False)],
)
def test_is_audio_file(name: str, expected: bool) -> None:
    assert is_audio_file(Path(name)) is expected


def test_audio_files_are_found_in_subfolders_in_order(tmp_path: Path) -> None:
    for name in ("b/2.mp3", "b/1.flac", "a.ogg", "notes.txt", "c/cover.jpg"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).touch()
    (tmp_path / "folder.mp3").mkdir()  # a folder, despite its name
    assert audio_files(tmp_path) == [
        tmp_path / "a.ogg",
        tmp_path / "b/1.flac",
        tmp_path / "b/2.mp3",
    ]


def test_untagged_wav_uses_file_name_and_real_duration(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "My Song.wav", seconds=1.5)
    assert reader.read(path) == Track(location=path, title="My Song", duration_ms=1500)


def test_unreadable_file_still_gives_a_track(tmp_path: Path) -> None:
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"definitely not audio")
    assert reader.read(path) == Track.from_path(path)


def test_missing_file_gives_a_track(tmp_path: Path) -> None:
    path = tmp_path / "gone.flac"
    assert reader.read(path) == Track.from_path(path)


def test_mutagen_error_gives_a_track(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: Path, **_: object) -> None:
        raise mutagen.MutagenError("corrupt")

    monkeypatch.setattr(mutagen, "File", boom)
    assert reader.read(Path("/x/y.mp3")) == Track.from_path(Path("/x/y.mp3"))


def test_tags_are_mapped_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = SimpleNamespace(
        tags={"title": [" Title "], "artist": ["Artist"], "album": ["Album", "Other"]},
        info=SimpleNamespace(length=61.25),
    )
    fake_mutagen_file(monkeypatch, audio)
    path = Path("/m/file.mp3")
    assert reader.read(path) == Track(
        location=path, title="Title", artist="Artist", album="Album", duration_ms=61_250
    )


def test_missing_tags_and_info_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=None))
    path = Path("/m/file.mp3")
    assert reader.read(path) == Track(location=path, title="file")


def test_empty_tag_values_fall_back_to_file_name(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = SimpleNamespace(tags={"title": [], "artist": [""]}, info=SimpleNamespace(length=None))
    fake_mutagen_file(monkeypatch, audio)
    assert reader.read(Path("/m/file.mp3")) == Track(location=Path("/m/file.mp3"), title="file")


def test_tag_lookup_errors_are_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    class PickyTags:
        def get(self, key: str) -> list[str]:
            if key == "title":
                return ["Ok"]
            raise ValueError(key)

    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=PickyTags(), info=None))
    assert reader.read(Path("/m/f.mp3")) == Track(location=Path("/m/f.mp3"), title="Ok")


@pytest.mark.parametrize(("writer", "file_type"), [(write_wav, WAVE), (write_aiff, AIFF)])
def test_id3_tags_in_wav_and_aiff_are_read(
    tmp_path: Path, writer: Callable[[Path], Path], file_type: Any
) -> None:
    path = writer(tmp_path / f"file.{file_type.__name__.lower()}")
    add_id3(path, file_type, title=" Real Title ", artist="Artist", album="Album")
    assert reader.read(path) == Track(
        location=path, title="Real Title", artist="Artist", album="Album", duration_ms=1000
    )


def test_id3_without_title_falls_back_to_file_name(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "untitled.wav")
    add_id3(path, WAVE, artist="Artist")
    expected = Track(location=path, title="untitled", artist="Artist", duration_ms=1000)
    assert reader.read(path) == expected


# -- ReplayGain ------------------------------------------------------------------ #


def add_frames(path: Path, file_type: Any, *frames: Any) -> None:
    audio = file_type(path)
    if audio.tags is None:
        audio.add_tags()
    for frame in frames:
        audio.tags.add(frame)
    audio.save()


def txxx(desc: str, value: str) -> Any:
    return TXXX(encoding=3, desc=desc, text=[value])


@pytest.mark.parametrize(("writer", "file_type"), [(write_wav, WAVE), (write_aiff, AIFF)])
def test_id3_txxx_replaygain(
    tmp_path: Path, writer: Callable[[Path], Path], file_type: Any
) -> None:
    path = writer(tmp_path / f"rg.{file_type.__name__.lower()}")
    add_frames(
        path,
        file_type,
        TIT2(encoding=3, text="Song"),
        txxx("REPLAYGAIN_TRACK_GAIN", "-6.54 dB"),
        txxx("REPLAYGAIN_TRACK_PEAK", "0.988"),
        txxx("replaygain_album_gain", "-7.10 dB"),  # lower case is common too
        txxx("REPLAYGAIN_ALBUM_PEAK", "1.000"),
    )
    track = reader.read(path)
    assert track.title == "Song"
    assert track.replay_gain == ReplayGain(
        track_gain=-6.54, track_peak=0.988, album_gain=-7.1, album_peak=1.0
    )


def test_id3_rva2_fallback_and_txxx_priority(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "rva2.wav")
    add_frames(
        path,
        WAVE,
        RVA2(desc="track", channel=1, gain=-4.5, peak=0.5),
        RVA2(desc="album", channel=1, gain=-5.0, peak=0.75),
        txxx("REPLAYGAIN_ALBUM_GAIN", "-9 dB"),  # TXXX wins over RVA2
    )
    gains = reader.read(path).replay_gain
    assert gains is not None
    assert gains.track_gain == pytest.approx(-4.5)
    assert gains.track_peak == pytest.approx(0.5, abs=1e-3)
    assert gains.album_gain == -9.0


def test_untagged_file_has_no_replaygain(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "plain.wav")
    add_frames(path, WAVE, TIT2(encoding=3, text="Song"))
    assert reader.read(path).replay_gain is None


def test_vorbis_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = VCommentDict()
    tags["TITLE"] = ["Song"]
    tags["ARTIST"] = ["Band"]
    tags["REPLAYGAIN_TRACK_GAIN"] = ["+2.30 dB"]
    tags["REPLAYGAIN_TRACK_PEAK"] = ["0.5"]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=SimpleNamespace(length=10)))
    track = reader.read(Path("/m/song.flac"))
    assert (track.title, track.artist) == ("Song", "Band")
    assert track.replay_gain == ReplayGain(track_gain=2.3, track_peak=0.5)


def test_opus_r128_gains_are_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    audio: Any = OggOpus.__new__(OggOpus)  # an OggOpus without parsing a file
    audio.tags = VCommentDict()
    audio.tags["R128_TRACK_GAIN"] = ["-2560"]  # -10 dB at -23 LUFS, Q7.8
    audio.tags["R128_ALBUM_GAIN"] = ["256"]  # +1 dB
    audio.info = SimpleNamespace(length=3)
    fake_mutagen_file(monkeypatch, audio)
    gains = reader.read(Path("/m/song.opus")).replay_gain
    assert gains == ReplayGain(track_gain=-5.0, album_gain=6.0)  # +5 dB to the -18 LUFS reference


def test_mp4_freeform_atoms(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = MP4Tags()
    tags["\xa9nam"] = ["Song"]
    tags["\xa9alb"] = ["Record"]
    tags["----:com.apple.iTunes:replaygain_track_gain"] = [MP4FreeForm(b"-8.25 dB")]
    tags["----:com.apple.iTunes:REPLAYGAIN_ALBUM_GAIN"] = [MP4FreeForm(b"-9.00 dB")]
    tags["----:com.apple.iTunes:iTunNORM"] = [MP4FreeForm(b"ignored")]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    track = reader.read(Path("/m/song.m4a"))
    assert (track.title, track.album) == ("Song", "Record")
    assert track.replay_gain == ReplayGain(track_gain=-8.25, album_gain=-9.0)


def test_asf_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = ASFTags()
    tags["Title"] = [ASFUnicodeAttribute("Song")]
    tags["Author"] = [ASFUnicodeAttribute("Band")]
    tags["replaygain_track_gain"] = [ASFUnicodeAttribute("-3.5 dB")]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    track = reader.read(Path("/m/song.wma"))
    assert (track.title, track.artist) == ("Song", "Band")
    assert track.replay_gain == ReplayGain(track_gain=-3.5)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("-6.54 dB", -6.54), ("+1.5dB", 1.5), ("\N{MINUS SIGN}3.25 dB", -3.25), ("0.988", 0.988)],
)
def test_gain_values_are_parsed_leniently(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags={"replaygain_track_gain": [raw]}))
    gains = reader.read(Path("/m/f.flac")).replay_gain
    assert gains is not None
    assert gains.track_gain == pytest.approx(expected)


def test_junk_gain_values_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = {"replaygain_track_gain": ["loud"], "replaygain_track_peak": [""]}
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags))
    assert reader.read(Path("/m/f.flac")).replay_gain is None


# -- Album artist, genre, year, track and disc numbers ------------------------------- #

ALBUM_FIELDS = {
    "album_artist": "Various Artists",
    "genre": "Trip Hop",
    "year": 1998,
    "track_number": 3,
    "disc_number": 2,
}


def album_fields(track: Track) -> dict[str, object]:
    return {name: getattr(track, name) for name in ALBUM_FIELDS}


def test_id3_album_fields(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "album.wav")
    add_frames(
        path,
        WAVE,
        TPE2(encoding=3, text="Various Artists"),
        TCON(encoding=3, text="Trip Hop"),
        TDRC(encoding=3, text="1998-04-20"),
        TRCK(encoding=3, text="3/11"),
        TPOS(encoding=3, text="2/2"),
    )
    assert album_fields(reader.read(path)) == ALBUM_FIELDS


def test_id3v1_genre_numbers_become_names(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "genre.wav")
    add_frames(path, WAVE, TCON(encoding=3, text="(17)"))
    assert reader.read(path).genre == "Rock"


def test_mp4_album_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = MP4Tags()
    tags["aART"] = ["Various Artists"]
    tags["\xa9gen"] = ["Trip Hop"]
    tags["\xa9day"] = ["1998-04-20T07:00:00Z"]
    tags["trkn"] = [(3, 11)]
    tags["disk"] = [(2, 2)]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    assert album_fields(reader.read(Path("/m/song.m4a"))) == ALBUM_FIELDS


def test_asf_album_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = ASFTags()
    tags["WM/AlbumArtist"] = [ASFUnicodeAttribute("Various Artists")]
    tags["WM/Genre"] = [ASFUnicodeAttribute("Trip Hop")]
    tags["WM/Year"] = [ASFUnicodeAttribute("1998")]
    tags["WM/TrackNumber"] = [ASFUnicodeAttribute("3")]
    tags["WM/PartOfSet"] = [ASFUnicodeAttribute("2/2")]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    assert album_fields(reader.read(Path("/m/song.wma"))) == ALBUM_FIELDS


@pytest.mark.parametrize("album_artist_key", ["ALBUMARTIST", "ALBUM ARTIST"])
def test_vorbis_album_fields(monkeypatch: pytest.MonkeyPatch, album_artist_key: str) -> None:
    tags = VCommentDict()
    tags[album_artist_key] = ["Various Artists"]
    tags["GENRE"] = ["Trip Hop"]
    tags["DATE"] = ["1998"]
    tags["TRACKNUMBER"] = ["03"]
    tags["DISCNUMBER"] = ["2"]
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    assert album_fields(reader.read(Path("/m/song.flac"))) == ALBUM_FIELDS


@pytest.mark.parametrize(
    ("raw", "year", "number"),
    [("", 0, 0), ("unknown", 0, 0), ("c. 1975", 1975, 0), ("20030", 0, 20030), (" 7 ", 0, 7)],
)
def test_years_and_numbers_are_parsed_leniently(
    monkeypatch: pytest.MonkeyPatch, raw: str, year: int, number: int
) -> None:
    tags = {"date": [raw], "tracknumber": [raw]}
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=tags, info=None))
    track = reader.read(Path("/m/f.flac"))
    assert (track.year, track.track_number) == (year, number)
