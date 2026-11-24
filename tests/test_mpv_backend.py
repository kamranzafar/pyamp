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

"""Drives real libmpv (with the null audio output), so it runs only where libmpv is installed."""

from __future__ import annotations

import threading
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.core.equalizer import Equalizer
from pyamp.core.events import EventBus, PlaybackStateChanged, TrackChanged
from pyamp.core.models import PlaybackState, ReplayGainMode, StreamUrl, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from tests.conftest import BackendRecorder as Recorder
from tests.radio import RadioStation

try:
    from pyamp.audio.mpv_backend import EQ_LABEL, MpvAudioBackend, equalizer_graph
except OSError as exc:  # no libmpv on this machine
    pytest.skip(f"libmpv not available: {exc}", allow_module_level=True)


def wait_until(condition: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        QCoreApplication.processEvents()
        time.sleep(0.005)


def write_wav(path: Path, seconds: float) -> Path:
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return path


@pytest.fixture
def backend(qt_app: QCoreApplication) -> Iterator[MpvAudioBackend]:
    backend = MpvAudioBackend(ao="null")
    yield backend
    backend.close()


@pytest.fixture
def recorder(backend: MpvAudioBackend) -> Recorder:
    recorder = Recorder()
    backend.set_listener(recorder)
    return recorder


def test_plays_and_reports_duration(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 1.5))
    backend.play()
    wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
    wait_until(lambda: 1500 in recorder.values("duration"))
    wait_until(lambda: any(isinstance(p, int) and p > 0 for p in recorder.values("position")))


def test_pause_and_resume(backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 3))
    backend.play()
    wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
    backend.pause()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.PAUSED)
    backend.play()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.PLAYING)


def test_seek(backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 5))
    backend.play()
    wait_until(lambda: 5000 in recorder.values("duration"))
    backend.seek(3000)
    wait_until(lambda: any(isinstance(p, int) and p >= 3000 for p in recorder.values("position")))


def test_end_of_track_without_queue(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 0.3))
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.STOPPED)
    assert "advanced" not in recorder.names()


def test_gapless_advance_to_queued_track(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    first = write_wav(tmp_path / "first.wav", 0.4)
    second = write_wav(tmp_path / "second.wav", 0.4)
    backend.load(first)
    backend.queue_next(second)
    backend.play()

    wait_until(lambda: "end" in recorder.names())
    assert recorder.values("advanced") == [second]
    # The transition was gapless: no end and no stop between the two tracks.
    names = recorder.names()
    advanced_at = names.index("advanced")
    assert "end" not in names[:advanced_at]
    states_before = [v for n, v in recorder.events[:advanced_at] if n == "state"]
    assert PlaybackState.STOPPED not in states_before[states_before.index(PlaybackState.PLAYING) :]


def test_queue_can_be_replaced_and_cleared(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 0.4))
    backend.queue_next(write_wav(tmp_path / "b.wav", 0.3))
    third = write_wav(tmp_path / "c.wav", 0.3)
    backend.queue_next(third)
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    assert recorder.values("advanced") == [third]

    recorder.events.clear()
    backend.load(write_wav(tmp_path / "d.wav", 0.3))
    backend.queue_next(write_wav(tmp_path / "e.wav", 0.3))
    backend.queue_next(None)
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    assert recorder.values("advanced") == []


def test_stop(backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 5))
    backend.play()
    wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
    backend.stop()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.STOPPED)
    QCoreApplication.processEvents()
    assert "end" not in recorder.names()


def test_unplayable_file_reports_error(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"this is not audio" * 100)
    backend.load(broken)
    backend.play()
    wait_until(lambda: "error" in recorder.names())


def test_callbacks_run_on_the_main_thread(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 0.3))
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    assert recorder.threads == {threading.get_ident()}
    assert "audio" not in recorder.names()  # mpv provides no PCM: flat visualiser


def test_player_plays_a_playlist_gaplessly(
    backend: MpvAudioBackend, bus: EventBus, tmp_path: Path
) -> None:
    playlist = Playlist(bus)
    player = Player(backend, playlist, bus)
    tracks = [Track.from_path(write_wav(tmp_path / f"{i}.wav", 0.3)) for i in range(3)]
    playlist.add(tracks)
    changes: list[int | None] = []
    states: list[PlaybackState] = []
    bus.subscribe(TrackChanged, lambda e: changes.append(e.index))
    bus.subscribe(PlaybackStateChanged, lambda e: states.append(e.state))

    player.play()
    wait_until(lambda: changes == [0, 1, 2] and player.state is PlaybackState.STOPPED)
    # Playing continued across both track boundaries without stopping in between.
    assert states == [PlaybackState.PLAYING, PlaybackState.STOPPED]


def test_replay_gain_mode_is_passed_to_mpv(backend: MpvAudioBackend) -> None:
    mpv_handle: Any = backend._mpv
    # python-mpv reads mpv's "no" back as False.
    assert mpv_handle.replaygain is False
    assert mpv_handle.replaygain_clip is False  # i.e. mpv prevents clipping
    expected: list[tuple[ReplayGainMode, object]] = [
        (ReplayGainMode.TRACK, "track"),
        (ReplayGainMode.ALBUM, "album"),
        (ReplayGainMode.OFF, False),
    ]
    for mode, value in expected:
        backend.set_replay_gain_mode(mode)
        assert mpv_handle.replaygain == value


# -- Equaliser ------------------------------------------------------------------- #


def eq_filters(backend: MpvAudioBackend) -> list[dict[str, Any]]:
    mpv_handle: Any = backend._mpv
    return [f for f in mpv_handle.af if f.get("label") == EQ_LABEL]


def eq_graph_in_mpv(backend: MpvAudioBackend) -> str:
    [entry] = eq_filters(backend)
    graph: str = entry["params"]["graph"]
    return graph


def test_equalizer_graph() -> None:
    graph = equalizer_graph(Equalizer(preamp_db=-3.0).with_gain(0, 6.0))
    assert graph.startswith("volume@pre=volume=-3.0dB,equalizer@b0=f=60:t=q:w=1.41:g=6.0,")
    assert graph.endswith("equalizer@b9=f=16000:t=q:w=1.41:g=0.0")
    assert graph.count("equalizer@") == 10


def test_equalizer_is_added_and_removed(backend: MpvAudioBackend) -> None:
    eq = Equalizer(enabled=True).with_preset("Rock")
    backend.set_equalizer(eq)
    assert eq_graph_in_mpv(backend) == equalizer_graph(eq)

    backend.set_equalizer(eq.with_enabled(False))
    assert eq_filters(backend) == []

    backend.set_equalizer(eq)
    backend.set_equalizer(Equalizer(enabled=True))  # flat: nothing to do, so no filter
    assert eq_filters(backend) == []


def test_changes_while_idle_rebuild_the_filter(backend: MpvAudioBackend) -> None:
    eq = Equalizer(enabled=True).with_gain(0, 3.0)
    backend.set_equalizer(eq)
    changed = eq.with_gain(1, 4.0)
    backend.set_equalizer(changed)  # nothing playing, so it can't be retuned live
    assert eq_graph_in_mpv(backend) == equalizer_graph(changed)


def test_changes_while_playing_keep_playback_going(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.load(write_wav(tmp_path / "a.wav", 1.5))
    backend.play()
    wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
    eq = Equalizer(enabled=True)
    for step in range(6):  # like someone adjusting sliders during playback
        eq = eq.with_gain(step, step + 1.0).with_preamp(-step / 2)
        backend.set_equalizer(eq)
        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert eq_graph_in_mpv(backend) == equalizer_graph(eq)
    wait_until(lambda: "end" in recorder.names())
    assert recorder.values("error") == []


def test_unchanged_equalizer_is_not_reapplied(backend: MpvAudioBackend) -> None:
    eq = Equalizer(enabled=True).with_preset("Pop")
    backend.set_equalizer(eq)
    before = backend._mpv.af
    backend.set_equalizer(Equalizer(enabled=True).with_preset("Pop"))  # equal value
    assert backend._mpv.af == before


def test_playback_completes_with_equalizer_on(
    backend: MpvAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.set_equalizer(Equalizer(enabled=True, preamp_db=-6.0).with_preset("Vocal"))
    backend.load(write_wav(tmp_path / "a.wav", 0.5))
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    assert recorder.values("error") == []


def test_this_libmpv_has_the_equaliser_filters() -> None:
    from pyamp.audio import mpv_backend  # noqa: PLC0415

    assert mpv_backend.missing_eq_filters() == ()


def test_missing_filters_are_reported_not_applied(
    backend: MpvAudioBackend, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pyamp.audio import mpv_backend  # noqa: PLC0415

    # Simulate a libmpv whose ffmpeg was built without the volume filter.
    monkeypatch.setattr(mpv_backend, "missing_eq_filters", lambda: ("volume",))
    backend.set_equalizer(Equalizer(enabled=True).with_preset("Rock"))
    assert eq_filters(backend) == []
    [message] = recorder.values("error")
    assert "lacks the volume filter" in str(message)
    assert "--libmpv bundled" in str(message)


# -- Streams ------------------------------------------------------------------------------ #


def test_plays_a_radio_stream_and_reports_its_song(
    backend: MpvAudioBackend, recorder: Recorder
) -> None:
    with RadioStation(title="Massive Attack - Teardrop") as station:
        backend.load(StreamUrl(f"{station.url}/live"))
        backend.play()
        wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
        wait_until(lambda: "Massive Attack - Teardrop" in recorder.values("stream_title"))
        assert recorder.values("error") == []
