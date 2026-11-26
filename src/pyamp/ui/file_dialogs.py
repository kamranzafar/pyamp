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

"""File and folder pickers that belong to the window that asks.

Each opens window-modal, which on macOS makes the native panel a sheet on that window:
it slides from the window's title bar, and when it closes the window still has the focus
(an application-modal panel hands it to the main window). Elsewhere it's the platform's
usual dialog over the window. The choice comes back through ``done``, called once with
nothing if the dialog was cancelled.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget

type Picked = Callable[[list[Path]], object]


def choose_files(
    parent: QWidget, title: str, directory: str, name_filter: str, done: Picked
) -> QFileDialog:
    """Existing files: ``done`` gets them, or [] if cancelled."""
    dialog = _dialog(parent, title, directory, name_filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    return _open(dialog, done)


def choose_file(
    parent: QWidget, title: str, directory: str, name_filter: str, done: Picked
) -> QFileDialog:
    """One existing file: ``done`` gets [it], or [] if cancelled."""
    dialog = _dialog(parent, title, directory, name_filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    return _open(dialog, done)


def choose_folder(parent: QWidget, title: str, directory: str, done: Picked) -> QFileDialog:
    """A folder: ``done`` gets [it], or [] if cancelled."""
    dialog = _dialog(parent, title, directory, "")
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly)
    return _open(dialog, done)


def choose_save_file(
    parent: QWidget, title: str, suggested: str, name_filter: str, done: Picked
) -> QFileDialog:
    """Where to save, starting from ``suggested``: ``done`` gets [it], or [] if cancelled
    (the dialog has asked before replacing a file)."""
    dialog = _dialog(parent, title, suggested, name_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    return _open(dialog, done)


def _dialog(parent: QWidget, title: str, directory: str, name_filter: str) -> QFileDialog:
    dialog = QFileDialog(parent, title, directory, name_filter)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)  # a sheet on macOS
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    return dialog


def _open(dialog: QFileDialog, done: Picked) -> QFileDialog:
    def finished(result: int) -> None:
        accepted = result == QDialog.DialogCode.Accepted.value
        done([Path(f) for f in dialog.selectedFiles()] if accepted else [])

    dialog.finished.connect(finished)
    dialog.open()
    return dialog
