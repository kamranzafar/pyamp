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

"""The dialog that adds a radio station or changes one: a name and a URL."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QVBoxLayout, QWidget

from pyamp.library.radio_stations import StationError, check_name, check_url
from pyamp.ui.dialog_parts import form_layout, heading, set_status, status_label


class RadioStationDialog(QDialog):
    """Asks for a station's name and URL; ``save(name, url)`` is called on OK, and a
    :class:`StationError` it raises (e.g. the name is taken) keeps the dialog open."""

    def __init__(
        self,
        *,
        save: Callable[[str, str], object],
        name: str = "",
        url: str = "",
        editing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Station" if editing else "Add Station")
        self.setMinimumWidth(480)
        self._save = save
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("e.g. Radio Paradise")
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://… (a stream, or a station's .pls or .m3u)")
        self.status = status_label()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Save" if editing else "Add")

        form = form_layout()
        form.addRow("Name", self.name)
        form.addRow("URL", self.url)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.addWidget(heading("Radio Station"))
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        self.name.textChanged.connect(self._changed)
        self.url.textChanged.connect(self._changed)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        (self.url if name and not url else self.name).setFocus()
        self._changed()

    def problem(self) -> str:
        """What's wrong with the station as typed, for the user; "" if nothing is."""
        try:
            check_name(self.name.text())
            check_url(self.url.text())
        except StationError as exc:
            return str(exc)
        return ""

    def accept(self) -> None:
        if message := self.problem():
            set_status(self.status, message, "problem")
            return
        try:
            self._save(self.name.text(), self.url.text())
        except StationError as exc:
            set_status(self.status, str(exc), "problem")
            return
        super().accept()

    def _changed(self) -> None:
        message = self.problem()
        self.ok_button.setEnabled(not message)
        set_status(self.status, message, "problem" if message else "")
