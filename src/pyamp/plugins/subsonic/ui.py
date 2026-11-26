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
"""The Subsonic plugin's server dialog.

The connection is checked on a worker thread, so the dialog never freezes on a slow
server. The server's music is browsed in PyAmp's library window (see ``source.py``).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from pyamp.plugins.subsonic.client import SubsonicClient, describe, normalise_server
from pyamp.ui.background_calls import BackgroundCalls
from pyamp.ui.dialog_parts import form_layout, heading, separator, set_status, status_label


class ServerDialog(QDialog):
    """Asks for the server and login, and checks them before accepting."""

    def __init__(
        self,
        server: str = "",
        username: str = "",
        *,
        has_keychain: bool = True,
        remember: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Subsonic Server")
        self.setMinimumWidth(460)
        self.client: SubsonicClient | None = None
        self._calls = BackgroundCalls(self)

        self.server = QLineEdit(server)
        self.server.setPlaceholderText("https://music.example.com")
        self.username = QLineEdit(username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("Remember the password in the system keychain")
        self.remember.setChecked(has_keychain and remember)  # as the user left it
        self.remember.setEnabled(has_keychain)
        if not has_keychain:
            self.remember.setToolTip("No keychain is available; you'll be asked each session")
        self.status = status_label()

        form = form_layout()
        form.addRow("Server:", self.server)
        form.addRow("Username:", self.username)
        form.addRow("Password:", self.password)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.connect_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.connect_button.setText("Connect")
        self.buttons.accepted.connect(self._connect)
        self.buttons.rejected.connect(self.reject)

        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        layout.addWidget(heading("Subsonic Server"))
        layout.addLayout(form)
        layout.addWidget(self.remember)
        layout.addSpacing(6)
        layout.addWidget(separator())
        layout.addLayout(footer)
        (self.password if server and username else self.server).setFocus()

    def _connect(self) -> None:
        try:
            client = SubsonicClient(
                normalise_server(self.server.text()),
                self.username.text().strip(),
                self.password.text(),
            )
        except ValueError as exc:
            set_status(self.status, str(exc), "problem")
            return
        self.connect_button.setEnabled(False)
        set_status(self.status, "Connecting…", "busy")
        self._calls.run(client.ping, lambda _, error: self._connected(client, error))

    def _connected(self, client: SubsonicClient, error: BaseException | None) -> None:
        self.connect_button.setEnabled(True)
        if error is not None:
            set_status(self.status, describe(error), "problem")
            return
        self.client = client
        self.accept()

    def done(self, result: int) -> None:
        self._calls.wait()
        super().done(result)
