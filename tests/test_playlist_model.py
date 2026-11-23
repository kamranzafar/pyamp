from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from pyamp.core.events import EventBus
from pyamp.core.playlist import Playlist
from pyamp.ui.playlist_model import PlaylistModel
from pyamp.ui.skin import DEFAULT_SKIN
from tests.conftest import make_tracks


class SignalLog:
    def __init__(self, model: PlaylistModel) -> None:
        self.inserted: list[tuple[int, int]] = []
        self.resets = 0
        model.rowsInserted.connect(lambda _parent, first, last: self.inserted.append((first, last)))
        model.modelReset.connect(self._on_reset)

    def _on_reset(self) -> None:
        self.resets += 1


def test_appends_are_incremental_inserts(
    qt_app: QCoreApplication, bus: EventBus, playlist: Playlist
) -> None:
    model = PlaylistModel(playlist, bus, DEFAULT_SKIN)
    log = SignalLog(model)
    playlist.add(make_tracks(3))
    playlist.add(make_tracks(2))
    assert log.inserted == [(0, 2), (3, 4)]
    assert log.resets == 0
    assert model.rowCount() == 5


def test_removal_resets_model(qt_app: QCoreApplication, bus: EventBus, playlist: Playlist) -> None:
    playlist.add(make_tracks(3))
    model = PlaylistModel(playlist, bus, DEFAULT_SKIN)
    log = SignalLog(model)
    playlist.remove([0])
    assert log.resets == 1
    assert model.rowCount() == 2
