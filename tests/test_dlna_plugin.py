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
"""The DLNA plugin in the real app (offscreen), against pretend servers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QWidget

from pyamp.app import Application
from pyamp.core.events import MediaSourcesChanged
from pyamp.core.models import StreamUrl
from pyamp.plugins.dlna import DlnaPlugin, host_and_port, known_descriptions, ssdp
from pyamp.plugins.dlna.client import MediaServer, describe
from pyamp.plugins.dlna.settings import DlnaSettings
from tests.conftest import FakeBackend
from tests.dlna_server import UDN, DlnaServer
from tests.test_background_loader import wait_until
from tests.test_library_window import labels, pick, settle, titles
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


@pytest.fixture
def server() -> Iterator[DlnaServer]:
    with DlnaServer() as server:
        yield server


@pytest.fixture
def other() -> Iterator[DlnaServer]:
    with DlnaServer("Study PC", "uuid:study") as server:
        yield server


@pytest.fixture
def settings(tmp_path: Path) -> DlnaSettings:
    return DlnaSettings(tmp_path / "dlna")


def start(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    found: Callable[[], list[str]],
    **network: Callable[[str], object],
) -> DlnaPlugin:
    """Start the plugin with ``found`` as what a network search finds, and wait for it."""
    plugin = DlnaPlugin(settings, discover=found, **network)  # type: ignore[arg-type]
    app.plugins.register(plugin)
    assert plugin in app.plugins.active_plugins
    wait_until(lambda: not plugin.refreshing)
    return plugin


def servers(app: Application) -> list[str]:  # noqa: F811
    return [source.name for source in list(app.sources)[2:]]  # after This Computer, Playlists


def menu_texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions()]


def test_finds_servers_on_the_network_as_it_starts(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    plugin = start(app, settings, lambda: ssdp.discover(timeout=0.3, target=server.ssdp_target))
    assert servers(app) == ["DLNA — Living Room NAS"]
    assert list(app.sources)[2:] == plugin.sources
    library = next(a.menu() for a in app.window.menuBar().actions() if a.text() == "&Library")
    assert isinstance(library, QMenu)
    assert menu_texts(library)[-3:] == [
        "Refresh &DLNA Servers",
        "Add DLNA Server by &Address…",
        "Show &Hidden DLNA Servers",
    ]
    assert not settings.file.exists()  # nothing to remember


def test_browse_a_server_and_play_its_songs(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    start(app, settings, lambda: [server.location])
    window = app.window.show_library()
    settle(window)
    assert labels(window, 2) == ["Music (1)", "Videos", "All Songs"]
    pick(window, 2, "1", "11", "al1")
    assert titles(window) == ["Teardrop", "Angel"]
    window.play_from(0)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    assert backend.loaded[-1] == StreamUrl(f"{server.url}/media/s1.mp3")  # as it is: no login


def test_servers_with_one_name_are_told_apart(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    with DlnaServer("Living Room NAS", "uuid:twin") as twin:
        start(app, settings, lambda: [server.location, twin.location, server.location])
        assert servers(app) == [
            f"DLNA — Living Room NAS ({server.url.removeprefix('http://')})",
            f"DLNA — Living Room NAS ({twin.url.removeprefix('http://')})",
        ]  # the same server found twice is listed once


def test_devices_that_arent_media_servers_are_skipped(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    start(
        app, settings, lambda: [f"{server.url}/nothing.xml", "file:///etc/hosts", server.location]
    )
    assert servers(app) == ["DLNA — Living Room NAS"]


def test_hide_a_server_and_show_it_again(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
    other: DlnaServer,
) -> None:
    plugin = start(app, settings, lambda: [server.location, other.location])
    window = app.window.show_library()
    settle(window)
    menu = window.sidebar_menu(window.sidebar_item(2))  # right-click on its name
    assert menu is not None
    assert menu_texts(menu) == ["Re&fresh", "&Hide Server"]
    menu.actions()[1].trigger()
    settle(window)
    assert servers(app) == ["DLNA — Study PC"]
    assert settings.hidden() == [UDN]
    plugin.refresh()
    wait_until(lambda: not plugin.refreshing)
    assert servers(app) == ["DLNA — Study PC"]  # stays hidden
    plugin.show_hidden()
    wait_until(lambda: not plugin.refreshing)
    assert servers(app) == ["DLNA — Study PC", "DLNA — Living Room NAS"]
    assert settings.hidden() == []


def test_refreshing_a_server_has_it_browsed_again(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    plugin = start(app, settings, lambda: [server.location])
    changed: list[MediaSourcesChanged] = []
    app.bus.subscribe(MediaSourcesChanged, changed.append)
    plugin.refresh_server(UDN)
    plugin.refresh_server("uuid:unknown")
    assert changed == [MediaSourcesChanged(plugin.sources[0])]


def test_a_server_found_again_unchanged_is_left_as_it_is(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    plugin = start(app, settings, lambda: [server.location])
    before = plugin.sources[0]
    plugin.refresh()
    plugin.refresh()  # ignored: one search at a time
    wait_until(lambda: not plugin.refreshing)
    assert plugin.sources == [before]  # its open folders stay open


def test_a_server_at_a_new_address_stays_in_its_place(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
    other: DlnaServer,
) -> None:
    moved = False

    def describe_moving(location: str) -> MediaServer:
        described = describe(location)
        if moved and described.udn == UDN:  # as if DHCP gave it another address
            return replace(described, location=f"{location}?moved")
        return described

    plugin = start(
        app, settings, lambda: [server.location, other.location], describe=describe_moving
    )
    first = plugin.sources[0]
    moved = True
    plugin.refresh()
    wait_until(lambda: not plugin.refreshing)
    assert plugin.sources[0] is not first
    assert plugin.sources[0].directory.server.location.endswith("?moved")
    assert servers(app) == ["DLNA — Living Room NAS", "DLNA — Study PC"]


def test_add_a_server_by_its_address_and_remove_it(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, settings, list)  # the search finds nothing
    asked: list[str] = []

    def get_text(parent: QWidget | None, title: str, label: str) -> tuple[str, bool]:
        asked.append(title)
        return f"  {server.location} ", True

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    plugin.add_server()
    wait_until(lambda: len(plugin.sources) == 1)
    assert asked == ["Add DLNA Server"]
    assert settings.added() == [server.location]
    window = app.window.show_library()
    settle(window)
    menu = window.sidebar_menu(window.sidebar_item(2))
    assert menu is not None
    assert menu_texts(menu) == ["Re&fresh", "&Remove Server"]
    plugin.refresh()  # found again, by its address
    wait_until(lambda: not plugin.refreshing)
    assert servers(app) == ["DLNA — Living Room NAS"]
    menu.actions()[1].trigger()
    assert servers(app) == []
    assert settings.added() == []
    assert settings.hidden() == [UDN]  # and not listed if a search finds it


def test_add_a_server_by_its_host_name(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    hosts: list[str] = []

    def search_host(host: str) -> list[str]:
        hosts.append(host)
        return ["http://127.0.0.1:9/not-there.xml", server.location]

    plugin = start(app, settings, list, search_host=search_host)
    plugin.add_server_at(server.host)  # host:port
    wait_until(lambda: len(plugin.sources) == 1)
    assert hosts == ["127.0.0.1"]  # the search goes to the host
    assert settings.added() == [server.location]


def test_a_server_that_doesnt_answer_its_own_search_is_found_where_its_kind_keeps_it(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
) -> None:
    with DlnaServer("k2-stream", description_path="/rootDesc.xml") as minidlna:
        plugin = start(app, settings, list, search_host=lambda host: [])  # as MiniDLNA
        plugin.add_server_at(minidlna.host)
        wait_until(lambda: len(plugin.sources) == 1)
        assert servers(app) == ["DLNA — k2-stream"]
        assert settings.added() == [minidlna.location]


def test_where_well_known_servers_keep_their_description() -> None:
    assert known_descriptions("192.168.1.11") == [
        "http://192.168.1.11:8200/rootDesc.xml",
        "http://192.168.1.11:32469/DeviceDescription.xml",
        "http://192.168.1.11:5001/description/fetch",
    ]
    assert known_descriptions("nas:9000")[0] == "http://nas:9000/rootDesc.xml"
    assert host_and_port("nas:9000") == ("nas", "9000")
    assert host_and_port("nas") == ("nas", "")
    assert host_and_port("nas:abc") == ("nas:abc", "")


def test_adding_a_found_server_by_address_unhides_it_and_makes_it_removable(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    settings.hide(UDN)
    plugin = start(app, settings, lambda: [server.location])
    assert plugin.sources == []
    plugin.add_server_at(server.location)
    wait_until(lambda: len(plugin.sources) == 1)
    assert settings.hidden() == []
    assert [a.text for a in plugin.sources[0].actions(None)] == ["Re&fresh", "&Remove Server"]


def test_no_server_at_an_address_is_said(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []

    def warning(parent: QWidget | None, title: str, text: str) -> None:
        warnings.append(text)

    monkeypatch.setattr(QMessageBox, "warning", warning)
    plugin = start(app, settings, list, search_host=lambda host: [])
    plugin.add_server_at("127.0.0.1:9")  # nothing there
    plugin.add_server_at(f"{server.url}/nothing.xml")
    wait_until(lambda: len(warnings) == 2)
    assert "No media server answered at 127.0.0.1:9" in warnings
    assert any(w.startswith("Could not reach the server") for w in warnings)
    assert plugin.sources == []
    assert settings.added() == []


def test_deactivate_removes_the_servers_and_drops_a_late_search(
    app: Application,  # noqa: F811
    settings: DlnaSettings,
    server: DlnaServer,
) -> None:
    plugin = start(app, settings, lambda: [server.location])
    assert servers(app) == ["DLNA — Living Room NAS"]

    def slow_search() -> list[str]:
        time.sleep(0.2)
        return [server.location]

    plugin._discover = slow_search
    plugin.refresh()
    plugin.deactivate()  # waits for the search, and forgets it
    QCoreApplication.processEvents()  # where its result would be delivered
    assert servers(app) == []
    assert not plugin.refreshing
    plugin.refresh()  # does nothing once deactivated
    plugin.add_server_at(server.location)
    assert plugin.sources == []


def test_settings_keep_order_and_no_duplicates(settings: DlnaSettings) -> None:
    assert (settings.hidden(), settings.added()) == ([], [])
    settings.hide("uuid:a")
    settings.hide("uuid:b")
    settings.hide("uuid:a")
    settings.add("http://nas/d.xml")
    settings.add("http://nas/d.xml")
    assert (settings.hidden(), settings.added()) == (["uuid:a", "uuid:b"], ["http://nas/d.xml"])
    settings.show("uuid:a")
    settings.remove("http://nas/d.xml")
    assert (settings.hidden(), settings.added()) == (["uuid:b"], [])
    settings.show_all()
    assert settings.hidden() == []


@pytest.mark.parametrize(
    "text",
    ["not json", "[1, 2]", '{"hidden": "uuid:a", "added": {}}', '{"hidden": [1, null]}'],
)
def test_unreadable_settings_are_ignored(settings: DlnaSettings, text: str) -> None:
    settings.directory.mkdir(parents=True)
    settings.file.write_text(text)
    assert (settings.hidden(), settings.added()) == ([], [])
