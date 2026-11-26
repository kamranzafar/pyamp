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
"""DLNA/UPnP plugin: find the media servers on the local network (a NAS, MiniDLNA,
Jellyfin, Plex, ...) and browse and play their music in PyAmp's library window, each
one of its sources.

Servers are searched for as PyAmp starts and on Library → Refresh DLNA Servers. Every
server found is listed, unless hidden from the right-click menu on its name; one the
search can't reach (another subnet, a network that blocks it) can be added by address.
Songs play from the plain addresses the server gives; there's no login.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from pyamp.library.sources import NodeAction
from pyamp.plugins.base import PluginContext
from pyamp.plugins.dlna import ssdp
from pyamp.plugins.dlna.client import (
    ContentDirectory,
    DlnaError,
    MediaServer,
    describe,
    describe_error,
)
from pyamp.plugins.dlna.settings import DlnaSettings
from pyamp.plugins.dlna.source import DlnaSource
from pyamp.ui.background_calls import BackgroundCalls

log = logging.getLogger(__name__)

MENU = "&Library"
#: Device descriptions are read this many at a time.
DESCRIBERS = 8
#: Where well-known servers keep their device description, for adding one by host:
#: some (MiniDLNA) only answer searches sent to everyone, not one sent to them.
KNOWN_DESCRIPTIONS = (
    (8200, "/rootDesc.xml"),  # MiniDLNA (ReadyMedia)
    (32469, "/DeviceDescription.xml"),  # Plex
    (5001, "/description/fetch"),  # Universal Media Server
)

type Discover = Callable[[], list[str]]
type SearchHost = Callable[[str], list[str]]
type Describe = Callable[[str], MediaServer]


def search_host(host: str) -> list[str]:
    """The media servers that answer a search sent straight to ``host``."""
    return ssdp.discover(target=(host, ssdp.SSDP_ADDRESS[1]))


def known_descriptions(address: str) -> list[str]:
    """Where a server at ``address`` (``host`` or ``host:port``) may keep its device
    description, if it's a well-known one."""
    host, port = host_and_port(address)
    if port:
        return [f"http://{host}:{port}{path}" for _, path in KNOWN_DESCRIPTIONS]
    return [f"http://{host}:{number}{path}" for number, path in KNOWN_DESCRIPTIONS]


def host_and_port(address: str) -> tuple[str, str]:
    """``host:port`` split (the port "" if there's none)."""
    host, colon, port = address.rpartition(":")
    return (host, port) if colon and port.isdigit() else (address, "")


class DlnaPlugin:
    name = "dlna"

    def __init__(
        self,
        settings: DlnaSettings | None = None,
        *,
        discover: Discover = ssdp.discover,
        search_host: SearchHost = search_host,
        describe: Describe = describe,
    ) -> None:
        """``discover``, ``search_host`` and ``describe`` reach the network (on a worker
        thread); tests pass their own."""
        self._settings = settings or DlnaSettings()
        self._discover = discover
        self._search_host = search_host
        self._describe = describe
        self._context: PluginContext | None = None
        self._calls: BackgroundCalls | None = None
        self._servers: dict[str, DlnaSource] = {}  # by unique device name, in order found
        self._refreshing = False
        self._session = 0  # results from before a deactivate are dropped

    @property
    def sources(self) -> list[DlnaSource]:
        """The servers in the library, in the order they were found."""
        return list(self._servers.values())

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    def activate(self, context: PluginContext) -> None:
        if context.sources is None:
            return  # no library to show servers in
        self._context = context
        self._calls = BackgroundCalls(threads=2)
        ui = context.ui()
        if ui is not None:
            ui.add_menu_separator(MENU)
            ui.add_menu_action(MENU, "Refresh &DLNA Servers", self.refresh)
            ui.add_menu_action(MENU, "Add DLNA Server by &Address…", self.add_server)
            ui.add_menu_action(MENU, "Show &Hidden DLNA Servers", self.show_hidden)
        self.refresh()

    def deactivate(self) -> None:
        self._session += 1
        self._refreshing = False
        for udn in list(self._servers):
            self._hide(udn)
        if self._calls is not None:
            self._calls.drop_waiting()
            self._calls.wait()  # a search under way ends within seconds
            self._calls = None
        self._context = None

    # -- Commands ----------------------------------------------------------- #

    def refresh(self) -> None:
        """Search the network for servers again (unless a search is under way)."""
        if self._calls is None or self._refreshing:
            return
        self._refreshing = True
        session, added = self._session, self._settings.added()
        self._calls.run(
            lambda: self._find(added),
            lambda servers, error: self._found(session, servers, error),
        )

    def add_server(self) -> None:
        """Ask for a server's address, and add it."""
        address, ok = QInputDialog.getText(
            self._parent(),
            "Add DLNA Server",
            "The server's address: its name or IP address, or the address of its\n"
            "device description (e.g. http://192.168.1.10:8200/rootDesc.xml).",
        )
        if ok and address.strip():
            self.add_server_at(address.strip())

    def add_server_at(self, address: str) -> None:
        """Add the server at ``address``: its host (or ``host:port``), or the address of
        its device description. A host is sent a search and, as some servers don't
        answer one sent to them alone, tried at well-known servers' description
        addresses. Says so in a message box if there's no server there."""
        if self._calls is None:
            return
        session = self._session
        self._calls.run(
            lambda: self._server_at(address),
            lambda server, error: self._added(session, server, error),
        )

    def show_hidden(self) -> None:
        self._settings.show_all()
        self.refresh()

    def refresh_server(self, udn: str) -> None:
        """Have the library window browse the server again."""
        source = self._servers.get(udn)
        if source is not None and self._context is not None and self._context.sources:
            self._context.sources.changed(source)

    def hide_server(self, udn: str) -> None:
        self._settings.hide(udn)
        self._hide(udn)

    def remove_server(self, udn: str) -> None:
        """Forget a server added by address (and keep it out if a search finds it)."""
        source = self._servers.get(udn)
        if source is not None:
            self._settings.remove(source.directory.server.location)
        self.hide_server(udn)

    # -- Worker thread ------------------------------------------------------ #

    def _find(self, added: list[str]) -> list[MediaServer]:
        """The servers that answer a search, and those added by address that answer."""
        locations = [*self._discover(), *added]
        servers: dict[str, MediaServer] = {}
        for server in self._describe_all(locations):
            if server is not None:
                servers.setdefault(server.udn, server)
        return list(servers.values())

    def _describe_all(self, locations: list[str]) -> list[MediaServer | None]:
        """Each location's server (None where there's none), read in parallel."""
        unique = list(dict.fromkeys(locations))
        with ThreadPoolExecutor(DESCRIBERS, thread_name_prefix="pyamp-dlna") as pool:
            return list(pool.map(self._try_describe, unique))

    def _try_describe(self, location: str) -> MediaServer | None:
        try:
            return self._describe(location)
        except (DlnaError, OSError, ValueError) as exc:
            log.info("Skipping the DLNA device at %s: %s", location, describe_error(exc))
            return None

    def _server_at(self, address: str) -> MediaServer:
        if "://" in address:
            return self._describe(address)
        host, _ = host_and_port(address)
        locations = [*self._search_host(host), *known_descriptions(address)]
        for server in self._describe_all(locations):
            if server is not None:
                return server
        raise DlnaError(f"No media server answered at {address}")

    # -- Main thread -------------------------------------------------------- #

    def _found(
        self, session: int, servers: list[MediaServer] | None, error: BaseException | None
    ) -> None:
        if session != self._session:
            return  # deactivated meanwhile
        self._refreshing = False
        if error is not None or servers is None:
            log.warning("Searching for DLNA servers failed: %s", error)
            return
        hidden = set(self._settings.hidden())
        for server in servers:
            if server.udn not in hidden:
                self._show(server)

    def _added(self, session: int, server: MediaServer | None, error: BaseException | None) -> None:
        if session != self._session:
            return
        if server is None:
            message = describe_error(error) if error else "No media server answered"
            QMessageBox.warning(self._parent(), "Add DLNA Server", message)
            return
        self._settings.add(server.location)
        self._settings.show(server.udn)
        self._show(server, again=True)  # with Remove Server, if it was found before

    def _show(self, server: MediaServer, *, again: bool = False) -> None:
        """List ``server`` in the library, in place of its earlier self (e.g. at an old
        address); an unchanged server is left as it is, open folders and all, unless
        ``again``."""
        old = self._servers.get(server.udn)
        if old is not None and old.directory.server == server and not again:
            return
        source = DlnaSource(ContentDirectory(server), "", self._commands(server))
        self._servers = {
            udn: source if udn == server.udn else shown for udn, shown in self._servers.items()
        }
        self._servers.setdefault(server.udn, source)
        self._rename()
        sources = self._context.sources if self._context else None
        if sources is None:
            return
        if old is not None:
            sources.replace(old, source)
        else:
            sources.add(source)

    def _hide(self, udn: str) -> None:
        source = self._servers.pop(udn, None)
        self._rename()  # before the library shows the others again
        if source is not None and self._context is not None and self._context.sources:
            self._context.sources.remove(source)

    def _commands(self, server: MediaServer) -> list[NodeAction]:
        udn = server.udn
        refresh = NodeAction("Re&fresh", lambda: self.refresh_server(udn))
        if server.location in self._settings.added():
            return [refresh, NodeAction("&Remove Server", lambda: self.remove_server(udn))]
        return [refresh, NodeAction("&Hide Server", lambda: self.hide_server(udn))]

    def _rename(self) -> None:
        """Name each server as it names itself, with its address where two share a name."""
        names = Counter(s.directory.server.name for s in self._servers.values())
        for source in self._servers.values():
            server = source.directory.server
            where = f" ({server.host})" if names[server.name] > 1 else ""
            source.name = f"DLNA — {server.name}{where}"

    def _parent(self) -> QWidget | None:
        ui = self._context.ui() if self._context else None
        parent = ui.dialog_parent() if ui else None
        return parent if isinstance(parent, QWidget) else None
