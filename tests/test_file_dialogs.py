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

"""File pickers that open as sheets on the window that asks, and answer through a callback."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QWidget

from pyamp.ui import file_dialogs


@pytest.fixture
def window(qt_app: QCoreApplication) -> Iterator[QWidget]:
    made = QWidget()
    made.show()
    yield made
    made.close()


@pytest.fixture
def picked() -> list[list[Path]]:
    return []


Chooser = Callable[[QWidget, list[list[Path]]], QFileDialog]

CHOOSERS: dict[str, Chooser] = {
    "files": lambda w, out: file_dialogs.choose_files(w, "Add", "", "Audio (*.mp3)", out.append),
    "file": lambda w, out: file_dialogs.choose_file(w, "Import", "", "Lists (*.m3u)", out.append),
    "folder": lambda w, out: file_dialogs.choose_folder(w, "Add Folder", "", out.append),
    "save": lambda w, out: file_dialogs.choose_save_file(w, "Export", "", "", out.append),
}


@pytest.mark.parametrize("kind", CHOOSERS)
def test_it_belongs_to_the_window_that_asks(
    window: QWidget, picked: list[list[Path]], kind: str
) -> None:
    dialog = CHOOSERS[kind](window, picked)
    assert dialog.parent() is window
    assert dialog.windowModality() is Qt.WindowModality.WindowModal  # a sheet on macOS
    assert dialog.isVisible()
    assert picked == []  # nothing until it's answered
    dialog.reject()


@pytest.mark.parametrize("kind", CHOOSERS)
def test_cancelling_answers_with_nothing(
    window: QWidget, picked: list[list[Path]], kind: str
) -> None:
    dialog = CHOOSERS[kind](window, picked)
    dialog.reject()
    assert picked == [[]]


def test_modes() -> None:
    QApplication.instance() or QApplication([])
    parent = QWidget()
    done: list[list[Path]] = []
    files = CHOOSERS["files"](parent, done)
    assert files.fileMode() is QFileDialog.FileMode.ExistingFiles
    one = CHOOSERS["file"](parent, done)
    assert one.fileMode() is QFileDialog.FileMode.ExistingFile
    folder = CHOOSERS["folder"](parent, done)
    assert folder.fileMode() is QFileDialog.FileMode.Directory
    save = CHOOSERS["save"](parent, done)
    assert save.acceptMode() is QFileDialog.AcceptMode.AcceptSave
    for dialog in (files, one, folder, save):
        dialog.reject()


def test_accepting_answers_with_the_chosen_paths(
    window: QWidget, picked: list[list[Path]], tmp_path: Path
) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"")
    dialog = file_dialogs.choose_files(window, "Add", str(tmp_path), "", picked.append)
    dialog.selectFile(str(song))
    dialog.done(QDialog.DialogCode.Accepted.value)
    assert picked == [[song]]
