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

import json
from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.fail import Keyring as FailKeyring
from PySide6.QtWidgets import QDialog, QMenu

from pyamp.app import Application
from pyamp.core.models import StreamUrl
from pyamp.plugins.subsonic import SubsonicPlugin
from pyamp.plugins.subsonic.client import SubsonicClient
from pyamp.plugins.subsonic.credentials import KEYRING_SERVICE, Account, AccountStore
from pyamp.plugins.subsonic.source import ALBUM, ARTIST, ARTISTS, PLAYLIST, PLAYLISTS, SIGN_IN
from pyamp.plugins.subsonic.ui import ServerDialog
from tests.conftest import FakeBackend
from tests.subsonic_server import PASSWORD, USERNAME, SubsonicServer
from tests.test_background_loader import wait_until
from tests.test_library_window import click, item, labels, pick, settle, titles
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}
        self.reads = 0  # each can make the system ask for permission

    def get_password(self, service: str, username: str) -> str | None:
        self.reads += 1
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
def second_server() -> Iterator[SubsonicServer]:
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
    accounts.put(account)
    keychain.set_password(KEYRING_SERVICE, account.key, PASSWORD)
    return account


def client(plugin: SubsonicPlugin, number: int = 0) -> SubsonicClient:
    """The client of the plugin's ``number``th server (signed in)."""
    signed_in = plugin.sources[number].client
    assert signed_in is not None
    return signed_in


def answer_dialog(
    monkeypatch: pytest.MonkeyPatch, server: str, password: str, *, remember: bool = True
) -> list[ServerDialog]:
    """Make the server dialog fill itself in and press Connect instead of waiting."""
    shown: list[ServerDialog] = []

    def run(dialog: ServerDialog) -> int:
        shown.append(dialog)
        dialog.remember_at_first = dialog.remember.isChecked()  # type: ignore[attr-defined]
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
    assert plugin.sources == []
    menu = next(a.menu() for a in app.window.menuBar().actions() if a.text() == "&Library")
    assert isinstance(menu, QMenu)
    # After PyAmp's own library items; the server is browsed in the library window.
    assert [a.text() for a in menu.actions()][-1] == "Add Subsonic &Server…"
    assert "Subsonic" not in " ".join(a.text() for a in menu.actions()[:-1])
    assert list(app.sources) == [
        app.local_source,
        app.playlists_source,
        app.smart_source,
    ]  # no server yet


def test_connects_and_remembers_the_password_in_the_keychain(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url + "/", PASSWORD)
    assert plugin.add_server()
    assert client(plugin).server == server.url
    assert accounts.accounts() == [Account(server.url, USERNAME)]
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
    assert not plugin.add_server()
    assert shown[0].status.text() == "Wrong username or password"
    assert shown[0].result() != QDialog.DialogCode.Accepted
    assert plugin.sources == []
    assert accounts.accounts() == []
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
    assert plugin.add_server()
    assert keychain.passwords == {}  # and an earlier one is forgotten
    assert not accounts.remembers_password(Account(server.url, USERNAME))


def test_not_remembering_is_remembered_and_keeps_out_of_the_keychain(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = Account(server.url, USERNAME)
    accounts.put(account, remember_password=False)
    plugin = start(app, accounts)  # as PyAmp starts
    assert keychain.reads == 0  # so the system doesn't ask for anything
    assert plugin.sources[0].client is None  # the password is asked for again this session
    shown = answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.edit_server(account)  # Sign In…
    assert client(plugin).server == server.url
    assert shown[0].remember_at_first is False  # type: ignore[attr-defined]  # as left
    assert keychain.reads == 0
    assert keychain.passwords == {}


def test_remembering_is_the_default(accounts: AccountStore, server: SubsonicServer) -> None:
    account = Account(server.url, USERNAME)
    assert accounts.remembers_password(account)  # nothing saved yet
    accounts.directory.mkdir(parents=True, exist_ok=True)
    accounts.file.write_text(
        json.dumps({"servers": [{"server": server.url, "username": USERNAME}]})
    )
    assert accounts.remembers_password(account)  # saved without the choice
    accounts.file.write_text("[1, 2]")
    assert accounts.accounts() == []
    accounts.file.write_text('{"servers": [{"server": "x"}, 3]}')
    assert accounts.accounts() == []


def test_an_earlier_single_server_file_is_read_as_a_list(accounts: AccountStore) -> None:
    accounts.directory.mkdir(parents=True, exist_ok=True)
    old = {"server": "https://a.example", "username": "alice", "remember_password": False}
    accounts.file.write_text(json.dumps(old))  # as PyAmp wrote it with one server
    first = Account("https://a.example", "alice")
    assert accounts.accounts() == [first]
    assert not accounts.remembers_password(first)
    second = Account("https://b.example", "bob")
    accounts.put(second)
    assert json.loads(accounts.file.read_text()) == {
        "servers": [
            {**old},
            {"server": "https://b.example", "username": "bob", "remember_password": True},
        ]
    }


def test_servers_keep_their_order_when_edited_and_removed(accounts: AccountStore) -> None:
    a, b, c = (Account(f"https://{n}.example", n) for n in "abc")
    for account in (a, b, c):
        accounts.put(account)
    edited = Account("https://b2.example", "b")
    accounts.put(edited, remember_password=False, replacing=b)
    assert accounts.accounts() == [a, edited, c]
    assert not accounts.remembers_password(edited)
    accounts.put(a, replacing=c)  # edited into one already there: no duplicate
    assert accounts.accounts() == [a, edited]
    accounts.remove(a)
    accounts.remove(a)
    assert accounts.accounts() == [edited]


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
        assert plugin.add_server()
        assert not shown[0].remember.isEnabled()
        assert client(plugin).server == server.url
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
    tracks = client(plugin).album_tracks("al1")
    app.window.play_from_library(tracks, 0)
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
    app.window.play_from_library(client(plugin).album_tracks("al1"), 0)
    app.playlists.save_now()
    assert app.playlists.active is not None
    text = app.playlists._saved.path(app.playlists.active).read_text()
    assert "/rest/stream?id=s1" in text
    assert "#EXTINF:330,Massive Attack - Teardrop" in text
    for secret in ("u=", "t=", "s=", PASSWORD):
        assert secret not in text


def test_the_server_is_a_source_in_the_library(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    (source,) = plugin.sources
    assert list(app.sources) == [app.local_source, app.playlists_source, app.smart_source, source]
    assert source.name == f"Subsonic — {server.url.removeprefix('http://')}"
    answer_dialog(monkeypatch, server.url, PASSWORD)
    assert plugin.add_server()  # adding it again replaces it, in its place
    assert list(app.sources) == [
        app.local_source,
        app.playlists_source,
        app.smart_source,
        *plugin.sources,
    ]
    assert len(plugin.sources) == 1
    plugin.deactivate()
    assert list(app.sources) == [app.local_source, app.playlists_source, app.smart_source]


def test_servers_are_added_alongside_each_other(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    second_server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, second_server.url, PASSWORD)
    assert plugin.add_server()
    first, second = plugin.sources
    assert list(app.sources) == [
        app.local_source,
        app.playlists_source,
        app.smart_source,
        first,
        second,
    ]
    assert [client(plugin, n).server for n in (0, 1)] == [server.url, second_server.url]
    assert accounts.accounts() == [
        Account(server.url, USERNAME),
        Account(second_server.url, USERNAME),
    ]
    assert len(keychain.passwords) == 2


def test_each_song_plays_with_its_own_servers_login(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    second_server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    remember(accounts, keychain, second_server)
    plugin = start(app, accounts)
    songs = [client(plugin, n).album_tracks("al1")[0] for n in (0, 1)]
    app.window.play_from_library(songs, 0)
    app.player.next()
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    first, second = backend.loaded[-2:]
    assert isinstance(first, StreamUrl) and isinstance(second, StreamUrl)
    assert first.url.startswith(f"{server.url}/rest/stream?id=s1&") and "&u=alice&t=" in first.url
    assert second.url.startswith(f"{second_server.url}/rest/stream?id=s1&")
    assert "&u=alice&t=" in second.url


def test_a_server_is_edited_in_its_place(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    second_server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = remember(accounts, keychain, server)
    other = Account("https://elsewhere.example", "bob")
    accounts.put(other, remember_password=False)
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, second_server.url, PASSWORD)
    assert plugin.edit_server(old)  # moved to another address
    moved = Account(second_server.url, USERNAME)
    assert accounts.accounts() == [moved, other]
    assert keychain.passwords == {(KEYRING_SERVICE, moved.key): PASSWORD}  # the old one gone
    assert client(plugin).server == second_server.url
    assert list(app.sources)[3:] == plugin.sources  # still first of the servers
    assert plugin.sources[0].name == f"Subsonic — {second_server.url.removeprefix('http://')}"


def test_a_server_is_removed_from_its_menu_in_the_library(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    second_server: SubsonicServer,
) -> None:
    first = remember(accounts, keychain, server)
    remember(accounts, keychain, second_server)
    plugin = start(app, accounts)
    song = client(plugin).album_tracks("al1")[0]  # from the server to remove
    window = app.window.show_library()
    settle(window)
    menu = window.sidebar_menu(window.sidebar_item(3))  # right-click on the server's name
    assert menu is not None
    assert [a.text() for a in menu.actions()] == [
        "&Edit Server…",
        "&Forget Password",
        "&Remove Server",
    ]
    menu.actions()[2].trigger()
    settle(window)
    assert accounts.accounts() == [Account(second_server.url, USERNAME)]
    assert (KEYRING_SERVICE, first.key) not in keychain.passwords
    assert [s.client.server for s in plugin.sources if s.client] == [second_server.url]
    assert list(app.sources)[3:] == plugin.sources
    assert window.sidebar.topLevelItemCount() == 4
    app.playlist.add([song])
    app.player.play_index(len(app.playlist) - 1)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    assert backend.loaded[-1] == song.location  # its login isn't added any more


def test_forget_a_servers_password(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    second_server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = remember(accounts, keychain, server)
    other = remember(accounts, keychain, second_server)
    plugin = start(app, accounts)
    window = app.window.show_library()
    settle(window)
    menu = window.sidebar_menu(window.sidebar_item(3))
    assert menu is not None
    forget = next(a for a in menu.actions() if a.text() == "&Forget Password")
    forget.trigger()
    settle(window)
    assert (KEYRING_SERVICE, account.key) not in keychain.passwords  # out of the keychain
    assert (KEYRING_SERVICE, other.key) in keychain.passwords  # the other server's kept
    assert not accounts.remembers_password(account)
    assert accounts.accounts() == [account, other]  # the server stays, in its place
    signed_out, signed_in = plugin.sources
    assert signed_out.client is None  # and is signed out
    assert list(app.sources)[3:] == [signed_out, signed_in]
    assert [a.text for a in signed_out.actions(None)] == ["Sign &In…", "&Remove Server"]
    reads = keychain.reads
    answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.edit_server(account)  # Sign In…, still not remembering it
    assert keychain.reads == reads and (KEYRING_SERVICE, account.key) not in keychain.passwords
    assert [a.text for a in plugin.sources[0].actions(None)] == ["&Edit Server…", "&Remove Server"]


def test_nothing_to_forget_without_a_saved_password(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.add_server()
    assert [a.text for a in plugin.sources[0].actions(None)] == ["&Edit Server…", "&Remove Server"]


def test_a_server_without_its_password_is_shown_to_sign_in_to(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = Account(server.url, USERNAME)
    accounts.put(account, remember_password=False)
    plugin = start(app, accounts)
    window = app.window.show_library()
    settle(window)
    pick(window, 3)
    assert window.status.text() == SIGN_IN
    menu = window.sidebar_menu(window.sidebar_item(3))
    assert menu is not None
    sign_in, remove = menu.actions()
    assert (sign_in.text(), remove.text()) == ("Sign &In…", "&Remove Server")
    answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    sign_in.trigger()
    settle(window)
    assert client(plugin).server == server.url
    assert labels(window, 3) == ["Artists", "Playlists"]
    assert keychain.passwords == {}  # still not remembered, as chosen


def test_servers_on_one_host_are_told_apart_by_username(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
) -> None:
    for name in ("alice", "bob"):
        accounts.put(Account("https://music.example", name), remember_password=False)
    accounts.put(Account("https://other.example", "alice"), remember_password=False)
    plugin = start(app, accounts)
    assert [s.name for s in plugin.sources] == [
        "Subsonic — alice@music.example",
        "Subsonic — bob@music.example",
        "Subsonic — other.example",
    ]
    window = app.window.show_library()
    settle(window)
    plugin.remove_server(Account("https://music.example", "bob"))
    settle(window)
    shown = [window.sidebar.topLevelItem(n).text(0) for n in (3, 4)]  # type: ignore[union-attr]
    assert shown == ["Subsonic — music.example", "Subsonic — other.example"]
    assert keychain.reads == 0  # none remembered: the keychain is never asked


def test_browse_an_album_in_the_library_and_play_it(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    assert labels(window, 3) == ["Artists", "Playlists"]
    assert labels(window, 3, (ARTISTS, "")) == ["Massive Attack (1 album)"]
    assert labels(window, 3, (ARTISTS, ""), (ARTIST, "ar1")) == [
        "Mezzanine (Massive Attack · 1998)"
    ]
    pick(window, 3, (ARTISTS, ""), (ARTIST, "ar1"), (ALBUM, "al1"))
    assert titles(window) == ["Teardrop", "Angel"]  # album order: by track number
    assert window.model.data(window.model.index(0, 6)) == "5:30"

    window.play_from(1)
    assert app.playlists.active == "Library"
    assert [t.title for t in app.playlist] == ["Teardrop", "Angel"]
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    played = backend.loaded[-1]
    assert isinstance(played, StreamUrl)
    assert "&u=alice&t=" in played.url  # the plugin added a login to play it


def test_search_the_server_and_its_playlists(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    pick(window, 3)
    assert titles(window) == []  # nothing to list until you search
    window.search.setText("tear")
    window.search.returnPressed.emit()
    settle(window)
    assert titles(window) == ["Teardrop"]
    window.search.setText("")
    pick(window, 3, (PLAYLISTS, ""), (PLAYLIST, "p1"))
    assert titles(window) == ["Angel"]


def test_server_errors_are_shown_in_the_library(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    client(plugin)._password = "changed on the server"
    window = app.window.show_library()
    settle(window)
    item(window, 3, (ARTISTS, "")).setExpanded(True)
    settle(window)
    assert window.status.text() == "Wrong username or password"


def test_deactivate_stops_adding_logins(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    track = client(plugin).album_tracks("al1")[0]
    plugin.deactivate()
    app.playlist.add([track])
    app.player.play_index(0)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    assert backend.loaded[-1] == track.location


def test_clicking_an_artist_shows_its_albums_and_songs(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    artists = item(window, 3, (ARTISTS, ""))
    click(window, artists)
    artist = item(window, 3, (ARTISTS, ""), (ARTIST, "ar1"))
    click(window, artist)
    assert artist.isExpanded()
    assert artist.child(0) is not None
    assert artist.child(0).text(0) == "Mezzanine (Massive Attack · 1998)"  # type: ignore[union-attr]
    assert titles(window) == ["Teardrop", "Angel"]  # every album's songs
