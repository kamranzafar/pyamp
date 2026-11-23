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

from __future__ import annotations

import struct
import wave
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mutagen
import pytest
from mutagen.aiff import AIFF
from mutagen.id3 import TALB, TIT2, TPE1
from mutagen.wave import WAVE

from pyamp.core.models import Track
from pyamp.library.metadata import MutagenMetadataReader, is_audio_file

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
    monkeypatch.setattr(mutagen, "File", lambda path, easy: result)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.mp3", True), ("B.FLAC", True), ("c.Opus", True), ("d.txt", False), ("noext", False)],
)
def test_is_audio_file(name: str, expected: bool) -> None:
    assert is_audio_file(Path(name)) is expected


def test_untagged_wav_uses_file_name_and_real_duration(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "My Song.wav", seconds=1.5)
    assert reader.read(path) == Track(path=path, title="My Song", duration_ms=1500)


def test_unreadable_file_still_gives_a_track(tmp_path: Path) -> None:
    path = tmp_path / "broken.mp3"
    path.write_bytes(b"definitely not audio")
    assert reader.read(path) == Track.from_path(path)


def test_missing_file_gives_a_track(tmp_path: Path) -> None:
    path = tmp_path / "gone.flac"
    assert reader.read(path) == Track.from_path(path)


def test_mutagen_error_gives_a_track(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(path: Path, easy: bool) -> None:
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
        path=path, title="Title", artist="Artist", album="Album", duration_ms=61_250
    )


def test_missing_tags_and_info_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=None))
    path = Path("/m/file.mp3")
    assert reader.read(path) == Track(path=path, title="file")


def test_empty_tag_values_fall_back_to_file_name(monkeypatch: pytest.MonkeyPatch) -> None:
    audio = SimpleNamespace(tags={"title": [], "artist": [""]}, info=SimpleNamespace(length=None))
    fake_mutagen_file(monkeypatch, audio)
    assert reader.read(Path("/m/file.mp3")) == Track(path=Path("/m/file.mp3"), title="file")


def test_tag_lookup_errors_are_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    class PickyTags:
        def get(self, key: str) -> list[str]:
            if key == "title":
                return ["Ok"]
            raise ValueError(key)

    fake_mutagen_file(monkeypatch, SimpleNamespace(tags=PickyTags(), info=None))
    assert reader.read(Path("/m/f.mp3")) == Track(path=Path("/m/f.mp3"), title="Ok")


@pytest.mark.parametrize(("writer", "file_type"), [(write_wav, WAVE), (write_aiff, AIFF)])
def test_id3_tags_in_wav_and_aiff_are_read(
    tmp_path: Path, writer: Callable[[Path], Path], file_type: Any
) -> None:
    path = writer(tmp_path / f"file.{file_type.__name__.lower()}")
    add_id3(path, file_type, title=" Real Title ", artist="Artist", album="Album")
    assert reader.read(path) == Track(
        path=path, title="Real Title", artist="Artist", album="Album", duration_ms=1000
    )


def test_id3_without_title_falls_back_to_file_name(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "untitled.wav")
    add_id3(path, WAVE, artist="Artist")
    expected = Track(path=path, title="untitled", artist="Artist", duration_ms=1000)
    assert reader.read(path) == expected
