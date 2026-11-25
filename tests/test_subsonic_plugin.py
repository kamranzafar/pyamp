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
"""The Subsonic plugin in the real app (offscreen), against a pretend server."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.fail import Keyring as FailKeyring
from PySide6.QtWidgets import QDialog, QMenu, QTreeWidget, QTreeWidgetItem

from pyamp.app import Application
from pyamp.core.models import StreamUrl
from pyamp.plugins.subsonic import SubsonicPlugin
from pyamp.plugins.subsonic.credentials import KEYRING_SERVICE, Account, AccountStore
from pyamp.plugins.subsonic.ui import ServerDialog
from tests.conftest import FakeBackend
from tests.subsonic_server import PASSWORD, USERNAME, SubsonicServer
from tests.test_background_loader import wait_until
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


@pytest.fixture
def keychain() -> Iterator[MemoryKeyring]:
    before = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(before)


@pytest.fixture
def server() -> Iterator[SubsonicServer]:
    with SubsonicServer() as server:
        yield server


@pytest.fixture
def accounts(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "subsonic")


def start(app: Application, accounts: AccountStore) -> SubsonicPlugin:  # noqa: F811
    plugin = SubsonicPlugin(accounts)
    app.plugins.register(plugin)
    assert plugin in app.plugins.active_plugins
    return plugin


def remember(accounts: AccountStore, keychain: MemoryKeyring, server: SubsonicServer) -> Account:
    account = Account(server.url, USERNAME)
    accounts.save(account)
    keychain.set_password(KEYRING_SERVICE, account.key, PASSWORD)
    return account


def answer_dialog(
    monkeypatch: pytest.MonkeyPatch, server: str, password: str, *, remember: bool = True
) -> list[ServerDialog]:
    """Make the server dialog fill itself in and press Connect instead of waiting."""
    shown: list[ServerDialog] = []

    def run(dialog: ServerDialog) -> int:
        shown.append(dialog)
        dialog.server.setText(server)
        dialog.username.setText(USERNAME)
        dialog.password.setText(password)
        dialog.remember.setChecked(remember)
        dialog.connect_button.click()
        wait_until(dialog.connect_button.isEnabled)
        return dialog.result()

    monkeypatch.setattr(ServerDialog, "exec", run)
    return shown


def test_adds_its_menu_and_waits_to_be_set_up(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
) -> None:
    plugin = start(app, accounts)
    assert plugin.client is None
    menu = next(a.menu() for a in app.window.menuBar().actions() if a.text() == "&Library")
    assert isinstance(menu, QMenu)
    # After PyAmp's own library items.
    assert [a.text() for a in menu.actions()][-2:] == ["Subsonic &Browser…", "Subsonic &Server…"]


def test_connects_and_remembers_the_password_in_the_keychain(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url + "/", PASSWORD)
    assert plugin.show_server_dialog()
    assert plugin.client is not None
    assert accounts.load() == Account(server.url, USERNAME)
    assert PASSWORD not in accounts.file.read_text()
    assert keychain.passwords == {(KEYRING_SERVICE, f"{USERNAME}@{server.url}"): PASSWORD}


def test_a_wrong_password_keeps_the_dialog_open(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    shown = answer_dialog(monkeypatch, server.url, "wrong")
    assert not plugin.show_server_dialog()
    assert shown[0].status.text() == "Wrong username or password"
    assert shown[0].result() != QDialog.DialogCode.Accepted
    assert plugin.client is None
    assert accounts.load() is None
    assert keychain.passwords == {}


def test_not_remembering_the_password(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.show_server_dialog()
    assert keychain.passwords == {}  # and an earlier one is forgotten


def test_without_a_keychain_the_password_lasts_the_session(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        plugin = start(app, accounts)
        shown = answer_dialog(monkeypatch, server.url, PASSWORD, remember=True)
        assert plugin.show_server_dialog()
        assert not shown[0].remember.isEnabled()
        assert plugin.client is not None
        assert accounts.password(Account(server.url, USERNAME)) is None
    finally:
        keyring.set_keyring(before)


def test_a_remembered_account_is_used_at_start_and_plays_with_a_login(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    tracks = plugin.client.album_tracks("al1")
    plugin.add_tracks(tracks, play=True)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    played = backend.loaded[-1]
    assert isinstance(played, StreamUrl)
    assert played.url.startswith(tracks[0].location.url + "&u=alice&t=")  # type: ignore[union-attr]
    assert app.playlist.current_track == tracks[0]


def test_saved_playlists_hold_no_login(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    plugin.add_tracks(plugin.client.album_tracks("al1"), play=True)
    app.playlists.save_now()
    assert app.playlists.active is not None
    text = app.playlists._saved.path(app.playlists.active).read_text()
    assert "/rest/stream?id=s1" in text
    assert "#EXTINF:330,Massive Attack - Teardrop" in text
    for secret in ("u=", "t=", "s=", PASSWORD):
        assert secret not in text


def child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    found = item.child(index)
    assert found is not None
    return found


def children(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
    return [child(item, i) for i in range(item.childCount())]


def top(tree: QTreeWidget, index: int) -> QTreeWidgetItem:
    found = tree.topLevelItem(index)
    assert found is not None
    return found


def tops(tree: QTreeWidget) -> list[QTreeWidgetItem]:
    return [top(tree, i) for i in range(tree.topLevelItemCount())]


def test_browse_an_album_and_add_it(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    browser = plugin.show_browser()
    assert browser is not None

    artists = top(browser.library, 0)
    artists.setExpanded(True)
    wait_until(lambda: artists.childCount() == 1)
    (artist,) = children(artists)
    assert artist.text(0) == "Massive Attack  (1 album)"
    artist.setExpanded(True)
    wait_until(lambda: artist.childCount() == 1)
    (album,) = children(artist)
    browser.library.setCurrentItem(album)
    wait_until(lambda: browser.songs.topLevelItemCount() == 2)
    assert top(browser.songs, 0).text(4) == "5:30"

    top(browser.songs, 1).setSelected(True)
    browser.add_button.click()
    assert [(t.title, t.duration_ms) for t in app.playlist] == [("Angel", 379_000)]
    browser.songs.clearSelection()
    browser.play_button.click()  # nothing selected: the whole album
    assert [t.title for t in app.playlist] == ["Angel", "Teardrop", "Angel"]
    assert app.playlist.current_index == 1


def test_search_and_playlists(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    browser = start(app, accounts).show_browser()
    assert browser is not None
    browser.search.setText("tear")
    browser.search.returnPressed.emit()
    wait_until(lambda: browser.songs.topLevelItemCount() == 1)
    assert top(browser.songs, 0).text(1) == "Teardrop"
    results = top(browser.library, 0)
    assert [c.text(0) for c in children(results)] == ["Mezzanine  (Massive Attack · 1998)"]

    playlists = next(t for t in tops(browser.library) if t.text(0) == "Playlists")
    playlists.setExpanded(True)
    wait_until(lambda: playlists.childCount() == 1)
    browser.library.setCurrentItem(child(playlists, 0))
    wait_until(
        lambda: browser.songs.topLevelItemCount() == 1 and top(browser.songs, 0).text(1) == "Angel"
    )


def test_deactivate_stops_adding_logins(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    track = plugin.client.album_tracks("al1")[0]
    plugin.deactivate()
    app.playlist.add([track])
    app.player.play_index(0)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    assert backend.loaded[-1] == track.location
