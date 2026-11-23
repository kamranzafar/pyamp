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
from __future__ import annotations

import math

import pytest

from pyamp.core.models import ReplayGain, ReplayGainMode

TRACK = ReplayGainMode.TRACK
ALBUM = ReplayGainMode.ALBUM


def db(factor: float) -> float:
    return 20 * math.log10(factor)


def test_off_and_untagged_leave_volume_alone() -> None:
    assert ReplayGain(track_gain=-6).factor(ReplayGainMode.OFF) == 1.0
    assert ReplayGain().factor(TRACK) == 1.0
    assert ReplayGain().factor(ALBUM) == 1.0


def test_track_gain_in_db() -> None:
    assert db(ReplayGain(track_gain=-6.0).factor(TRACK)) == pytest.approx(-6.0)
    assert ReplayGain(track_gain=-6.0206).factor(TRACK) == pytest.approx(0.5, abs=1e-4)


def test_album_mode_uses_album_gain_and_falls_back_to_track() -> None:
    gains = ReplayGain(track_gain=-3.0, album_gain=-8.0)
    assert db(gains.factor(ALBUM)) == pytest.approx(-8.0)
    assert db(gains.factor(TRACK)) == pytest.approx(-3.0)
    assert db(ReplayGain(track_gain=-3.0).factor(ALBUM)) == pytest.approx(-3.0)


def test_preamp_is_added() -> None:
    assert db(ReplayGain(track_gain=-6.0).factor(TRACK, preamp_db=2.0)) == pytest.approx(-4.0)


def test_peak_prevents_clipping() -> None:
    # +6 dB would double the level, but the loudest sample is already at 0.8.
    gains = ReplayGain(track_gain=6.0, track_peak=0.8)
    assert gains.factor(TRACK) == pytest.approx(1 / 0.8)
    # Attenuation is never limited by the peak.
    assert db(ReplayGain(track_gain=-6.0, track_peak=0.99).factor(TRACK)) == pytest.approx(-6.0)


def test_album_peak_is_used_with_album_gain() -> None:
    gains = ReplayGain(track_gain=0.0, track_peak=0.5, album_gain=6.0, album_peak=0.9)
    assert gains.factor(ALBUM) == pytest.approx(1 / 0.9)
