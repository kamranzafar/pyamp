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
"""The QtMultimedia backend: conversions and signal mapping everywhere, playback where
there is an audio output device (CI provides a silent one on Linux)."""

from __future__ import annotations

import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioFormat,
    QMediaDevices,
    QMediaPlayer,
    QtAudio,
)

from pyamp.audio.qt_backend import QtAudioBackend, audio_buffer_to_mono
from pyamp.core.models import PlaybackState, StreamUrl
from tests.conftest import BackendRecorder as Recorder
from tests.radio import RadioStation

Fmt = QAudioFormat.SampleFormat


def buffer(
    samples: np.ndarray, sample_format: QAudioFormat.SampleFormat, channels: int
) -> QAudioBuffer:
    fmt = QAudioFormat()
    fmt.setSampleFormat(sample_format)
    fmt.setChannelCount(channels)
    fmt.setSampleRate(8000)
    return QAudioBuffer(samples.tobytes(), fmt)


def wait_until(condition: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        QCoreApplication.processEvents()
        time.sleep(0.005)


def write_wav(path: Path, seconds: float, tone: bool = False) -> Path:
    rate = 8000
    t = np.arange(int(rate * seconds)) / rate
    samples = (np.sin(2 * np.pi * 440 * t) * 8000 if tone else np.zeros_like(t)).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return path


# -- PCM conversion (feeds the visualiser) ------------------------------------------- #


def test_int16_stereo_is_averaged_to_mono() -> None:
    stereo = np.array([32767, -32768, 16384, 16384], dtype="<i2")  # two frames
    mono = audio_buffer_to_mono(buffer(stereo, Fmt.Int16, channels=2))
    assert mono is not None
    assert mono.dtype == np.float32
    np.testing.assert_allclose(mono, [-0.5 / 32768, 0.5], atol=1e-6)


@pytest.mark.parametrize(
    ("samples", "sample_format", "expected"),
    [
        (np.array([0, 128, 255], dtype=np.uint8), Fmt.UInt8, [-1.0, 0.0, 127 / 128]),
        (np.array([-(2**31), 0, 2**30], dtype="<i4"), Fmt.Int32, [-1.0, 0.0, 0.5]),
        (np.array([-0.25, 0.0, 1.0], dtype="<f4"), Fmt.Float, [-0.25, 0.0, 1.0]),
    ],
)
def test_sample_formats_map_into_minus_one_to_one(
    samples: np.ndarray, sample_format: QAudioFormat.SampleFormat, expected: list[float]
) -> None:
    mono = audio_buffer_to_mono(buffer(samples, sample_format, channels=1))
    assert mono is not None
    np.testing.assert_allclose(mono, expected, atol=1e-6)


def test_unusable_buffers_give_nothing() -> None:
    assert audio_buffer_to_mono(buffer(np.zeros(4, "<i2"), Fmt.Unknown, 1)) is None
    assert audio_buffer_to_mono(buffer(np.zeros(0, "<i2"), Fmt.Int16, 1)) is None


# -- Backend without playing anything ----------------------------------------------- #


@pytest.fixture
def backend(qt_app: QCoreApplication) -> Iterator[QtAudioBackend]:
    backend = QtAudioBackend()
    yield backend
    backend.close()


@pytest.fixture
def recorder(backend: QtAudioBackend) -> Recorder:
    recorder = Recorder()
    backend.set_listener(recorder)
    return recorder


def test_volume_is_perceptual_and_replaygain_scales_it(backend: QtAudioBackend) -> None:
    backend.set_volume(1.0)
    assert backend.output_volume == pytest.approx(1.0)
    backend.set_volume(0.5)
    expected = QtAudio.convertVolume(
        0.5, QtAudio.VolumeScale.LogarithmicVolumeScale, QtAudio.VolumeScale.LinearVolumeScale
    )
    assert backend.output_volume == pytest.approx(expected, abs=1e-3)
    assert expected < 0.5  # quieter than linear: perceptual volume
    backend.set_track_gain(0.5)
    assert backend.output_volume == pytest.approx(expected * 0.5, abs=1e-3)


def test_replaygain_cannot_boost_above_full_volume(backend: QtAudioBackend) -> None:
    backend.set_volume(1.0)
    backend.set_track_gain(2.0)
    assert backend.output_volume == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("qt_state", "state"),
    [
        (QMediaPlayer.PlaybackState.PlayingState, PlaybackState.PLAYING),
        (QMediaPlayer.PlaybackState.PausedState, PlaybackState.PAUSED),
        (QMediaPlayer.PlaybackState.StoppedState, PlaybackState.STOPPED),
    ],
)
def test_playback_states_are_translated(
    backend: QtAudioBackend,
    recorder: Recorder,
    qt_state: QMediaPlayer.PlaybackState,
    state: PlaybackState,
) -> None:
    backend._on_playback_state(qt_state)
    assert recorder.values("state") == [state]


def test_only_end_of_media_ends_the_track(backend: QtAudioBackend, recorder: Recorder) -> None:
    for status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
        backend._on_media_status(status)
    assert recorder.names() == []
    backend._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert recorder.names() == ["end"]


def test_errors_are_reported_with_a_message(backend: QtAudioBackend, recorder: Recorder) -> None:
    backend._on_error(QMediaPlayer.Error.NoError, "")
    backend._on_error(QMediaPlayer.Error.FormatError, "Unsupported format")
    backend._on_error(QMediaPlayer.Error.FormatError, "Unsupported format")  # repeated: once
    backend.load(Path("/m/next.mp3"))  # a new track may report its own error
    backend._on_error(QMediaPlayer.Error.ResourceError, "")  # no message: the error's name
    assert recorder.values("error") == ["Unsupported format", "ResourceError"]


def test_audio_buffers_feed_the_visualiser(backend: QtAudioBackend, recorder: Recorder) -> None:
    backend._on_audio_buffer(buffer(np.zeros(16, "<i2"), Fmt.Int16, 1))
    backend._on_audio_buffer(buffer(np.zeros(4, "<i2"), Fmt.Unknown, 1))  # skipped
    assert recorder.names() == ["audio"]


# -- Real playback (needs an audio output device) ------------------------------------ #


@pytest.fixture
def needs_audio_output(qt_app: QCoreApplication) -> None:
    if not QMediaDevices.audioOutputs():
        pytest.skip("no audio output device")


@pytest.mark.usefixtures("needs_audio_output")
def test_plays_a_file_to_the_end(
    backend: QtAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    backend.set_volume(0.0)  # silent, even on a real sound card
    backend.load(write_wav(tmp_path / "a.wav", 1.0, tone=True))
    backend.play()
    wait_until(lambda: "end" in recorder.names())
    assert PlaybackState.PLAYING in recorder.values("state")
    durations = [d for d in recorder.values("duration") if isinstance(d, int) and d]
    assert durations
    assert abs(durations[-1] - 1000) <= 50
    assert "audio" in recorder.names()  # the PCM tap fed the visualiser
    assert recorder.values("error") == []


@pytest.mark.usefixtures("needs_audio_output")
def test_pause_resume_and_seek(backend: QtAudioBackend, recorder: Recorder, tmp_path: Path) -> None:
    backend.set_volume(0.0)
    backend.load(write_wav(tmp_path / "long.wav", 5.0))
    backend.play()
    wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
    backend.pause()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.PAUSED)
    backend.seek(3000)
    wait_until(lambda: any(isinstance(p, int) and p >= 3000 for p in recorder.values("position")))
    backend.play()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.PLAYING)
    backend.stop()
    wait_until(lambda: recorder.values("state")[-1] is PlaybackState.STOPPED)


@pytest.mark.usefixtures("needs_audio_output")
def test_unplayable_file_reports_an_error(
    backend: QtAudioBackend, recorder: Recorder, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio at all" * 64)
    backend.load(broken)
    backend.play()
    wait_until(lambda: bool(recorder.values("error")))


@pytest.mark.usefixtures("needs_audio_output")
def test_plays_a_stream(backend: QtAudioBackend, recorder: Recorder) -> None:
    backend.set_volume(0.0)
    with RadioStation() as station:
        backend.load(StreamUrl(f"{station.url}/plain.wav"))
        backend.play()
        wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
        wait_until(lambda: "audio" in recorder.names())  # decoded PCM reaches the visualiser
        backend.stop()
    assert recorder.values("error") == []


@pytest.mark.usefixtures("needs_audio_output")
def test_a_failing_stream_reports_one_error(backend: QtAudioBackend, recorder: Recorder) -> None:
    backend.set_volume(0.0)
    with RadioStation(seconds=1.0) as station:  # the station "goes off air" after 1 s
        backend.load(StreamUrl(f"{station.url}/live"))
        backend.play()
        wait_until(lambda: PlaybackState.PLAYING in recorder.values("state"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        backend.stop()
    assert len(recorder.values("error")) <= 1
