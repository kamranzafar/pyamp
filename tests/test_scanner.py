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
"""Scanning the library's sources: incremental, cancellable, safe with unplugged drives."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from mutagen.wave import WAVE

from pyamp.core.models import Track
from pyamp.library import metadata
from pyamp.library import scanner as scanner_module
from pyamp.library.catalog import Catalog
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.scanner import LibraryScanner, ScanSummary
from tests.test_metadata import add_id3, write_wav


class CountingReader:
    """The real tag reader, counting which files it was asked to read."""

    def __init__(self) -> None:
        self.read_paths: list[Path] = []
        self._reader = MutagenMetadataReader()

    def read(self, path: Path) -> Track:
        self.read_paths.append(path)
        return self._reader.read(path)


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    yield opened
    opened.close()


@pytest.fixture
def reader() -> CountingReader:
    return CountingReader()


@pytest.fixture
def scanner(catalog: Catalog, reader: CountingReader) -> LibraryScanner:
    return LibraryScanner(catalog, reader)


@pytest.fixture
def music(tmp_path: Path) -> Path:
    """A folder with two albums, a cover image and an empty subfolder."""
    folder = tmp_path / "music"
    for name in ("Album A/01.wav", "Album A/02.wav", "Album B/01.wav"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        write_wav(folder / name)
    (folder / "Album A" / "cover.jpg").write_bytes(b"jpeg")
    (folder / "Empty").mkdir()
    return folder


def titles(catalog: Catalog) -> list[str]:
    return [t.title for t in catalog.tracks()]


def touch_later(path: Path) -> None:
    """Make sure a rewritten file's modification time differs, even on coarse clocks."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


def test_first_scan_reads_every_audio_file(
    catalog: Catalog, scanner: LibraryScanner, reader: CountingReader, music: Path
) -> None:
    catalog.add_source(music)
    assert scanner.scan() == ScanSummary(added=3)
    assert len(reader.read_paths) == 3
    assert sorted((t.location for t in catalog.tracks()), key=str) == [
        music / "Album A/01.wav",
        music / "Album A/02.wav",
        music / "Album B/01.wav",
    ]


def test_tags_are_indexed(catalog: Catalog, scanner: LibraryScanner, music: Path) -> None:
    add_id3(music / "Album B/01.wav", WAVE, title="Teardrop", artist="Massive Attack")
    catalog.add_source(music)
    scanner.scan()
    assert [t.title for t in catalog.tracks("massive")] == ["Teardrop"]


def test_rescan_only_reads_new_and_changed_files(
    catalog: Catalog, scanner: LibraryScanner, reader: CountingReader, music: Path
) -> None:
    catalog.add_source(music)
    scanner.scan()
    reader.read_paths.clear()
    changed = music / "Album A/02.wav"
    add_id3(changed, WAVE, title="Retitled")
    touch_later(changed)
    new = write_wav(music / "Album B/02.wav")

    assert scanner.scan() == ScanSummary(added=1, updated=1, unchanged=2)
    assert sorted(reader.read_paths) == [changed, new]
    assert "Retitled" in titles(catalog)


def test_an_unchanged_library_reads_nothing(
    catalog: Catalog, scanner: LibraryScanner, reader: CountingReader, music: Path
) -> None:
    catalog.add_source(music)
    scanner.scan()
    reader.read_paths.clear()
    assert scanner.scan() == ScanSummary(unchanged=3)
    assert reader.read_paths == []


def test_deleted_files_are_forgotten(
    catalog: Catalog, scanner: LibraryScanner, music: Path
) -> None:
    catalog.add_source(music)
    scanner.scan()
    shutil.rmtree(music / "Album A")
    assert scanner.scan() == ScanSummary(removed=2, unchanged=1)
    assert [t.location for t in catalog.tracks()] == [music / "Album B/01.wav"]


def test_tracks_of_a_missing_source_are_kept(
    catalog: Catalog, scanner: LibraryScanner, music: Path, tmp_path: Path
) -> None:
    single = write_wav(tmp_path / "single.wav")
    (tmp_path / "other").mkdir()
    deleted = write_wav(tmp_path / "other" / "deleted.wav")
    for source in (music, single, tmp_path / "other"):
        catalog.add_source(source)
    scanner.scan()
    unplugged = tmp_path / "unplugged"
    music.rename(unplugged)  # like a drive that isn't connected
    single.unlink()  # a single file that's gone may be on such a drive too
    deleted.unlink()  # but a file gone from a folder that's there was deleted

    summary = scanner.scan()
    assert summary == ScanSummary(removed=1, missing_sources=(music, single))
    assert len(catalog) == 4
    unplugged.rename(music)
    assert scanner.scan() == ScanSummary(unchanged=3, missing_sources=(single,))


def test_single_files_can_be_sources(
    catalog: Catalog, scanner: LibraryScanner, tmp_path: Path
) -> None:
    song = write_wav(tmp_path / "song.wav")
    (tmp_path / "notes.txt").write_text("not music")
    catalog.add_source(song)
    catalog.add_source(tmp_path / "notes.txt")
    assert scanner.scan() == ScanSummary(added=1)
    assert titles(catalog) == ["song"]


def test_a_cancelled_scan_keeps_what_it_read_and_removes_nothing(
    catalog: Catalog, scanner: LibraryScanner, music: Path
) -> None:
    catalog.add_source(music)
    scanner.scan()
    shutil.rmtree(music / "Album B")
    write_wav(music / "Album A/03.wav")
    checks = iter([False, False, True])  # stop before the third file

    summary = scanner.scan(should_stop=lambda: next(checks))
    assert summary == ScanSummary(unchanged=2, cancelled=True)
    assert len(catalog) == 3  # Album B/01 is only forgotten by a full scan
    assert scanner.scan() == ScanSummary(added=1, removed=1, unchanged=2)


def test_tracks_are_written_in_batches(
    catalog: Catalog, scanner: LibraryScanner, music: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scanner_module, "BATCH_SIZE", 2)
    batches: list[int] = []
    put = catalog.put

    def counting_put(entries: list[tuple[Track, object]]) -> None:
        batches.append(len(entries))
        put(entries)  # type: ignore[arg-type]

    monkeypatch.setattr(catalog, "put", counting_put)
    catalog.add_source(music)
    scanner.scan()
    assert batches == [2, 1]


def test_files_that_vanish_mid_scan_are_skipped(
    catalog: Catalog, scanner: LibraryScanner, music: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog.add_source(music)
    real = metadata.audio_files

    def listing_then_delete(folder: Path) -> list[Path]:
        files = real(folder)
        files[0].unlink()  # deleted between listing the folder and reading it
        return files

    monkeypatch.setattr("pyamp.library.scanner.audio_files", listing_then_delete)
    assert scanner.scan() == ScanSummary(added=2)
