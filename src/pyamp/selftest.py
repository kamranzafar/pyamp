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
"""``pyamp --self-test``: check that this installation (e.g. a packaged app) really works.

Runs headless and silently (offscreen Qt, mpv's null audio output) and prints one
line per check. CI runs it on the built macOS app; users can run it when the mpv
backend doesn't seem to work.
"""

from __future__ import annotations

import os
import tempfile
import time
import traceback
import wave
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from pyamp.core.equalizer import Equalizer
from pyamp.core.models import Location, PlaybackState, ReplayGainMode

#: How long the mpv playback check may take before it counts as failed.
PLAYBACK_TIMEOUT_S = 20.0

type Check = Callable[[], str]
"""A check returns a short detail line, or raises if it failed."""


class _Recorder:
    """A minimal BackendListener that remembers what happened."""

    def __init__(self) -> None:
        self.states: list[PlaybackState] = []
        self.advanced: list[Location] = []
        self.errors: list[str] = []
        self.ended = False

    def on_state_changed(self, state: PlaybackState) -> None:
        self.states.append(state)

    def on_position_changed(self, position_ms: int) -> None:
        pass

    def on_duration_changed(self, duration_ms: int) -> None:
        pass

    def on_end_of_track(self) -> None:
        self.ended = True

    def on_track_advanced(self, location: Location) -> None:
        self.advanced.append(location)

    def on_stream_title(self, title: str) -> None:
        pass

    def on_error(self, message: str) -> None:
        self.errors.append(message)

    def on_audio_info(self, bitrate: int, sample_rate: int) -> None:
        pass

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        pass


def _ensure_qt_app() -> None:
    from PySide6.QtCore import QCoreApplication  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    if QCoreApplication.instance() is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QApplication([])


def _write_wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * seconds))
    return path


def check_libmpv() -> str:
    from pyamp.audio.mpv_backend import LIBMPV_SOURCE  # noqa: PLC0415

    return str(LIBMPV_SOURCE)


def check_qt_backend() -> str:
    from pyamp.audio.qt_backend import QtAudioBackend  # noqa: PLC0415

    backend = QtAudioBackend()
    backend.close()
    return "QtMultimedia player created"


def check_mpv_playback() -> str:
    """Equaliser on, two tracks queued gaplessly, played to the end (silently)."""
    from PySide6.QtCore import QCoreApplication  # noqa: PLC0415

    from pyamp.audio.mpv_backend import MpvAudioBackend  # noqa: PLC0415

    recorder = _Recorder()
    backend = MpvAudioBackend(ao="null")
    backend.set_listener(recorder)
    try:
        with tempfile.TemporaryDirectory(prefix="pyamp-selftest-") as tmp:
            first = _write_wav(Path(tmp) / "first.wav", 0.3)
            second = _write_wav(Path(tmp) / "second.wav", 0.3)
            backend.set_equalizer(Equalizer(enabled=True, preamp_db=-3.0).with_preset("Rock"))
            backend.load(first)
            backend.queue_next(second)
            backend.play()
            deadline = time.monotonic() + PLAYBACK_TIMEOUT_S
            while not recorder.ended and not recorder.errors:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"no end of playback after {PLAYBACK_TIMEOUT_S:.0f} s")
                QCoreApplication.processEvents()
                time.sleep(0.01)
    finally:
        backend.close()
    if recorder.errors:
        raise RuntimeError(f"playback error: {recorder.errors[0]}")
    if recorder.advanced != [second]:
        raise RuntimeError(f"expected a gapless advance to {second.name}, got {recorder.advanced}")
    return "equaliser on, gapless advance, end of playback"


def check_replay_gain() -> str:
    from pyamp.audio.mpv_backend import MpvAudioBackend  # noqa: PLC0415

    backend = MpvAudioBackend(ao="null")
    try:
        for mode in (ReplayGainMode.TRACK, ReplayGainMode.ALBUM, ReplayGainMode.OFF):
            backend.set_replay_gain_mode(mode)
    finally:
        backend.close()
    return "track, album and off modes accepted"


def check_track_analysis() -> str:
    """The visualiser's file analysis (used with mpv): decode a 1 kHz tone, find its band."""
    from PySide6.QtCore import QCoreApplication  # noqa: PLC0415

    from pyamp.audio.track_analysis import FPS, TrackAnalyser  # noqa: PLC0415

    analyser = TrackAnalyser()
    try:
        with tempfile.TemporaryDirectory(prefix="pyamp-selftest-") as tmp:
            path = Path(tmp) / "tone.wav"
            rate, hz = 44100, 1000
            t = np.arange(rate) / rate
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes((np.sin(2 * np.pi * hz * t) * 12000).astype("<i2").tobytes())
            timeline = analyser.analyse(path)
            deadline = time.monotonic() + PLAYBACK_TIMEOUT_S
            while not timeline.finished.is_set():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"no analysis after {PLAYBACK_TIMEOUT_S:.0f} s")
                QCoreApplication.processEvents()
                time.sleep(0.01)
    finally:
        analyser.shutdown()
    if abs(len(timeline.rows) - FPS) > 2:
        raise RuntimeError(f"expected about {FPS} rows for 1 s of audio, got {len(timeline.rows)}")
    row = timeline.row_at(0.5)
    assert row is not None
    edges = np.geomspace(40.0, 16_000.0, len(row) + 1)
    expected = int(np.searchsorted(edges, hz) - 1)
    loudest = int(np.argmax(row))
    if abs(loudest - expected) > 1:
        raise RuntimeError(f"1 kHz tone peaked in band {loudest}, expected about {expected}")
    return f"{len(timeline.rows)} rows, 1 kHz in band {loudest}"


def check_icons() -> str:
    """PyAmp's icons are SVGs: drawing them needs Qt's SVG plugins (kept in the builds)."""
    from pyamp.ui.icons import app_icon, menubar_icon  # noqa: PLC0415

    for name, icon in (("app", app_icon()), ("menu bar", menubar_icon())):
        image = icon.pixmap(64, 64).toImage()
        if image.isNull() or all(
            image.pixelColor(x, y).alpha() == 0 for x in range(0, 64, 4) for y in range(0, 64, 4)
        ):
            raise RuntimeError(f"the {name} icon didn't draw (is Qt's SVG support missing?)")
    return "app and menu bar icons drawn"


CHECKS: Sequence[tuple[str, Check]] = (
    ("icons", check_icons),
    ("libmpv", check_libmpv),
    ("qt backend", check_qt_backend),
    ("mpv playback", check_mpv_playback),
    ("replaygain", check_replay_gain),
    ("visualiser analysis", check_track_analysis),
)


def run_self_test(
    checks: Sequence[tuple[str, Check]] = CHECKS, out: Callable[[str], object] = print
) -> bool:
    """Run every check, report each one, and return whether all passed."""
    _ensure_qt_app()
    passed = True
    for name, check in checks:
        try:
            detail = check()
        except Exception as exc:
            passed = False
            out(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
            if os.environ.get("PYAMP_SELFTEST_TRACEBACK"):
                out(traceback.format_exc())
        else:
            out(f"[ ok ] {name}: {detail}")
    out("self-test passed" if passed else "self-test FAILED")
    return passed
