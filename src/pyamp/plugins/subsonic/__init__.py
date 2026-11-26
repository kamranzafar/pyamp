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
"""Subsonic plugin: browse and play your music from Subsonic-compatible servers
(Navidrome, Airsonic, Gonic, ...) in PyAmp's library window, each one of its sources.

Add as many servers as you like (Library → Add Subsonic Server…); each is edited, signed
in to or removed from the right-click menu on its name in the library.

Songs are added to playlists as streams with their tags and length. Their URLs carry no
login, so saved playlists hold no secrets; the plugin adds a fresh login, from the
server the URL points at, as each one plays.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

from PySide6.QtWidgets import QDialog, QWidget

from pyamp.core.events import Unsubscribe
from pyamp.core.models import Location
from pyamp.library.sources import NodeAction
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
        self._servers: dict[Account, SubsonicSource] = {}  # in the order added
        self._stop_resolving: Unsubscribe | None = None

    @property
    def sources(self) -> list[SubsonicSource]:
        """The servers in the library, in the order they were added."""
        return list(self._servers.values())

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._stop_resolving = context.player.add_location_resolver(self._authorise)
        for account in self._accounts.accounts():
            client = None
            # Only look in the keychain if the user keeps the password there: reading it
            # can make the system ask for permission.
            if self._accounts.remembers_password(account):
                password = self._accounts.password(account)
                if password is not None:
                    client = SubsonicClient(account.server, account.username, password)
            self._show(account, client)  # without a password: shown, to sign in to
        ui = context.ui()
        if ui is not None:
            ui.add_menu_separator(MENU)
            ui.add_menu_action(MENU, "Add Subsonic &Server…", self.add_server)

    def deactivate(self) -> None:
        for account in list(self._servers):
            self._hide(account)
        if self._stop_resolving is not None:
            self._stop_resolving()
            self._stop_resolving = None

    # -- Commands ----------------------------------------------------------- #

    def add_server(self) -> bool:
        """Ask for a server and login, and add it; True once connected."""
        return self._ask(None)

    def edit_server(self, account: Account) -> bool:
        """Ask again for ``account``'s server and login (also to sign in); True once
        connected."""
        return self._ask(account)

    def remove_server(self, account: Account) -> None:
        # The keychain is only touched where a password is kept.
        if self._accounts.remembers_password(account):
            self._accounts.forget_password(account)
        self._accounts.remove(account)
        self._hide(account)

    # -- Internals ---------------------------------------------------------- #

    def _ask(self, account: Account | None) -> bool:
        remembered = {a: self._accounts.remembers_password(a) for a in self._accounts.accounts()}
        dialog = ServerDialog(
            account.server if account else "",
            account.username if account else "",
            has_keychain=self._accounts.has_keychain(),
            remember=remembered.get(account, True) if account else True,
            parent=self._parent(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.client is None:
            return False
        client = dialog.client
        chosen = Account(client.server, client.username)
        remember = dialog.remember.isChecked()
        # The keychain is only touched where a password is, or is to be, kept.
        if account is not None and account != chosen and remembered.get(account):
            self._accounts.forget_password(account)
        self._accounts.put(chosen, remember_password=remember, replacing=account)
        if remember:
            self._accounts.save_password(chosen, dialog.password.text())
        elif remembered.get(chosen):
            self._accounts.forget_password(chosen)
        if account is not None and account != chosen:
            self._hide(chosen)  # it was there already: edited into the other one
        self._show(chosen, client, replacing=account)
        return True

    def _show(
        self, account: Account, client: SubsonicClient | None, replacing: Account | None = None
    ) -> None:
        """Show ``account``'s server in the library (in place of ``replacing``'s, or of an
        earlier source of its own)."""
        commands = [
            NodeAction(
                "Sign &In…" if client is None else "&Edit Server…",
                lambda: self.edit_server(account),
            ),
            NodeAction("&Remove Server", lambda: self.remove_server(account)),
        ]
        source = SubsonicSource(client, "", commands)
        old: SubsonicSource | None = None
        servers: dict[Account, SubsonicSource] = {}
        for known, shown in self._servers.items():  # in its place, keeping the order
            if old is None and known in (account, replacing):
                old = shown
                servers[account] = source
            else:
                servers[known] = shown
        servers.setdefault(account, source)
        self._servers = servers
        self._rename()
        sources = self._context.sources if self._context else None
        if sources is None:
            return
        if old is not None:
            sources.replace(old, source)
        else:
            sources.add(source)

    def _hide(self, account: Account) -> None:
        source = self._servers.pop(account, None)
        self._rename()  # before the library shows the others again
        if source is not None and self._context is not None and self._context.sources:
            self._context.sources.remove(source)

    def _rename(self) -> None:
        """Name each server by its address, with the username where two share one."""
        hosts = Counter(urlsplit(a.server).netloc for a in self._servers)
        for account, source in self._servers.items():
            host = urlsplit(account.server).netloc
            who = f"{account.username}@" if hosts[host] > 1 else ""
            source.name = f"Subsonic — {who}{host}"

    def _authorise(self, location: Location) -> Location:
        """``location`` with a login from the server it's on (as it is if it's on none)."""
        for source in self._servers.values():
            if source.client is not None:
                authorised = source.client.authorise(location)
                if authorised != location:
                    return authorised
        return location

    def _parent(self) -> QWidget | None:
        ui = self._context.ui() if self._context else None
        parent = ui.dialog_parent() if ui else None
        return parent if isinstance(parent, QWidget) else None
