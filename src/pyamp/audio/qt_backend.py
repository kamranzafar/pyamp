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

"""Audio backend built on QtMultimedia (Adapter pattern).

Needs no system libraries beyond what ships with PySide6. Decoded PCM is
tapped with ``QAudioBufferOutput`` (Qt >= 6.8) to feed the visualiser.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import (
    QAudioBuffer,
    QAudioBufferOutput,
    QAudioDevice,
    QAudioFormat,
    QAudioOutput,
    QMediaDevices,
    QMediaMetaData,
    QMediaPlayer,
    QtAudio,
)

from pyamp.audio.backend import AudioBackend
from pyamp.audio.qt_audio_env import avoid_incomplete_pipewire
from pyamp.core.models import Location, OutputDevice, PlaybackState, StreamUrl


def _device_id(device: QAudioDevice) -> str:
    """The device's id as text: CoreAudio's UID on macOS, as mpv names it too."""
    return bytes(device.id().data()).decode("utf-8", "replace")


_STATE_MAP = {
    QMediaPlayer.PlaybackState.StoppedState: PlaybackState.STOPPED,
    QMediaPlayer.PlaybackState.PlayingState: PlaybackState.PLAYING,
    QMediaPlayer.PlaybackState.PausedState: PlaybackState.PAUSED,
}

# sample format -> (numpy dtype, offset, scale) to map raw samples into [-1, 1]
_SAMPLE_FORMATS: dict[QAudioFormat.SampleFormat, tuple[type[np.generic], float, float]] = {
    QAudioFormat.SampleFormat.UInt8: (np.uint8, 128.0, 128.0),
    QAudioFormat.SampleFormat.Int16: (np.int16, 0.0, 32768.0),
    QAudioFormat.SampleFormat.Int32: (np.int32, 0.0, 2147483648.0),
    QAudioFormat.SampleFormat.Float: (np.float32, 0.0, 1.0),
}


def audio_buffer_to_mono(buffer: QAudioBuffer) -> NDArray[np.float32] | None:
    """Convert an interleaved ``QAudioBuffer`` into mono float32 samples."""
    fmt = buffer.format()
    spec = _SAMPLE_FORMATS.get(fmt.sampleFormat())
    channels = fmt.channelCount()
    if spec is None or channels <= 0 or buffer.frameCount() == 0:
        return None
    dtype, offset, scale = spec
    raw = np.frombuffer(buffer.constData(), dtype=dtype, count=buffer.sampleCount())
    samples = (raw.astype(np.float32) - offset) / scale
    if channels == 1:
        return samples  # already mono: nothing to average (the analyser asks for mono)
    return samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)


class QtAudioBackend(AudioBackend):
    name: ClassVar[str] = "qt"
    provides_audio_frames: ClassVar[bool] = True  # QAudioBufferOutput taps the PCM
    supports_output_devices: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__()
        # Must happen before Qt initialises its audio backend (first player/output).
        avoid_incomplete_pipewire()
        self._player = QMediaPlayer()
        self._output = QAudioOutput()
        self._tap = QAudioBufferOutput()
        self._player.setAudioOutput(self._output)
        self._linear_volume = 1.0
        self._error_reported = False
        self._gain = 1.0
        self._bitrate = 0  # of the playing audio: from metadata, and from the PCM tap
        self._sample_rate = 0
        self._player.setAudioBufferOutput(self._tap)
        self._device_id: str | None = None  # None: follow the system default
        self._media_devices = QMediaDevices()  # its signal needs an instance kept alive
        self._media_devices.audioOutputsChanged.connect(self._on_outputs_changed)

        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)
        self._player.metaDataChanged.connect(self._on_metadata)
        self._tap.audioBufferReceived.connect(self._on_audio_buffer)

    # -- AudioBackend ------------------------------------------------------- #

    def load(self, location: Location) -> None:
        self._error_reported = False
        self._set_audio_info(bitrate=0, sample_rate=0)  # until the new source reports
        if isinstance(location, StreamUrl):
            self._player.setSource(QUrl(location.url))
        else:
            self._player.setSource(QUrl.fromLocalFile(str(location)))

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._player.stop()

    def seek(self, position_ms: int) -> None:
        self._player.setPosition(position_ms)

    def set_volume(self, volume: float) -> None:
        # Human loudness perception is logarithmic; Qt expects a linear gain.
        self._linear_volume = QtAudio.convertVolume(
            volume,
            QtAudio.VolumeScale.LogarithmicVolumeScale,
            QtAudio.VolumeScale.LinearVolumeScale,
        )
        self._apply_output_volume()

    def set_track_gain(self, factor: float) -> None:
        self._gain = factor
        self._apply_output_volume()

    @property
    def output_volume(self) -> float:
        return float(self._output.volume())

    def _apply_output_volume(self) -> None:
        # QAudioOutput can't go above 1.0, so ReplayGain can attenuate but not boost here.
        self._output.setVolume(min(self._linear_volume * self._gain, 1.0))

    def output_devices(self) -> list[OutputDevice]:
        return [OutputDevice(_device_id(d), d.description()) for d in QMediaDevices.audioOutputs()]

    def set_output_device(self, device_id: str | None) -> None:
        self._device_id = device_id
        device = next(
            (d for d in QMediaDevices.audioOutputs() if _device_id(d) == device_id),
            QMediaDevices.defaultAudioOutput(),
        )
        if device != self._output.device():  # setting it at all restarts the output
            self._output.setDevice(device)

    def close(self) -> None:
        self._player.setSource(QUrl())

    # -- Qt signal handlers ------------------------------------------------- #

    def _on_outputs_changed(self) -> None:
        # QAudioOutput stays on the device it was given, so when following the system
        # default, move to the new default (the player does that via set_output_device).
        self._listener.on_output_devices_changed()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if state is QMediaPlayer.PlaybackState.StoppedState:
            self._set_audio_info(bitrate=0, sample_rate=0)
        self._listener.on_state_changed(_STATE_MAP[state])

    # Look the listener up on every call: it may be replaced after construction.
    def _on_position(self, position_ms: int) -> None:
        self._listener.on_position_changed(position_ms)

    def _on_duration(self, duration_ms: int) -> None:
        self._listener.on_duration_changed(duration_ms)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status is QMediaPlayer.MediaStatus.EndOfMedia:
            self._listener.on_end_of_track()

    def _on_metadata(self) -> None:
        bitrate = self._player.metaData().value(QMediaMetaData.Key.AudioBitRate)
        self._set_audio_info(bitrate=int(bitrate) if isinstance(bitrate, int) else 0)
        # For streams, Qt's FFmpeg backend reports the station's current title here.
        if self._player.source().isLocalFile():
            return
        title = self._player.metaData().stringValue(QMediaMetaData.Key.Title)
        if title:
            self._listener.on_stream_title(title)

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        # A stream that drops makes Qt's FFmpeg backend repeat the error many times;
        # report it once per track.
        if error is not QMediaPlayer.Error.NoError and not self._error_reported:
            self._error_reported = True
            self._listener.on_error(message or error.name)

    def _on_audio_buffer(self, buffer: QAudioBuffer) -> None:
        rate = buffer.format().sampleRate()
        if rate != self._sample_rate:
            self._set_audio_info(sample_rate=rate)
        samples = audio_buffer_to_mono(buffer)
        if samples is not None:
            self._listener.on_audio_frame(samples, rate)

    def _set_audio_info(
        self, *, bitrate: int | None = None, sample_rate: int | None = None
    ) -> None:
        old = (self._bitrate, self._sample_rate)
        if bitrate is not None:
            self._bitrate = bitrate
        if sample_rate is not None:
            self._sample_rate = sample_rate
        if (self._bitrate, self._sample_rate) != old:
            self._listener.on_audio_info(self._bitrate, self._sample_rate)
