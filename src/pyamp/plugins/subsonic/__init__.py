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
"""Subsonic plugin: browse and play your music from a Subsonic-compatible server
(Navidrome, Airsonic, Gonic, ...) in PyAmp's library window, as one of its sources.

Songs are added to playlists as streams with their tags and length. Their URLs carry no
login, so saved playlists hold no secrets; the plugin adds a fresh login as each one plays.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QWidget

from pyamp.plugins.base import PluginContext
from pyamp.plugins.subsonic.client import SubsonicClient
from pyamp.plugins.subsonic.credentials import Account, AccountStore
from pyamp.plugins.subsonic.source import SubsonicSource
from pyamp.plugins.subsonic.ui import ServerDialog

MENU = "&Library"


class SubsonicPlugin:
    name = "subsonic"

    def __init__(self, accounts: AccountStore | None = None) -> None:
        self._accounts = accounts or AccountStore()
        self._context: PluginContext | None = None
        self.client: SubsonicClient | None = None
        self.source: SubsonicSource | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        account = self._accounts.load()
        # Only look in the keychain if the user keeps the password there: reading it
        # can make the system ask for permission.
        if account is not None and self._accounts.remembers_password():
            password = self._accounts.password(account)
            if password is not None:
                self._use(SubsonicClient(account.server, account.username, password))
        ui = context.ui()
        if ui is not None:
            ui.add_menu_action(MENU, "Subsonic &Server…", self.show_server_dialog)

    def deactivate(self) -> None:
        self._remove_source()
        if self._context is not None:
            self._context.player.set_location_resolver(None)
        self.client = None

    # -- Menu commands ------------------------------------------------------ #

    def show_server_dialog(self) -> bool:
        """Ask for the server and login; True once connected."""
        account = self._accounts.load()
        was_remembered = self._accounts.remembers_password()
        dialog = ServerDialog(
            account.server if account else "",
            account.username if account else "",
            has_keychain=self._accounts.has_keychain(),
            remember=was_remembered,
            parent=self._parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.client is None:
            return False
        client = dialog.client
        chosen = Account(client.server, client.username)
        remember = dialog.remember.isChecked()
        # The keychain is only touched where a password is, or is to be, kept.
        if was_remembered and account is not None and account != chosen:
            self._accounts.forget_password(account)
        self._accounts.save(chosen, remember_password=remember)
        if remember:
            self._accounts.save_password(chosen, dialog.password.text())
        elif was_remembered:
            self._accounts.forget_password(chosen)
        self._use(client)
        return True

    # -- Internals ---------------------------------------------------------- #

    def _use(self, client: SubsonicClient) -> None:
        """Play from ``client``'s server, and show it in the library window."""
        assert self._context is not None
        self.client = client
        self._context.player.set_location_resolver(client.authorise)
        self._remove_source()  # the old server's
        if self._context.sources is not None:
            self.source = SubsonicSource(client)
            self._context.sources.add(self.source)

    def _remove_source(self) -> None:
        if self.source is not None and self._context is not None and self._context.sources:
            self._context.sources.remove(self.source)
        self.source = None

    def _parent(self) -> QWidget | None:
        ui = self._context.ui() if self._context else None
        parent = ui.dialog_parent() if ui else None
        return parent if isinstance(parent, QWidget) else None
