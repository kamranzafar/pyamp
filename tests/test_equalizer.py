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

import pytest

from pyamp.core.equalizer import BANDS_HZ, FLAT, GAIN_RANGE_DB, PRESETS, Equalizer


def test_defaults_are_off_and_flat() -> None:
    eq = Equalizer()
    assert not eq.enabled
    assert eq.is_flat
    assert not eq.active
    assert len(eq.gains_db) == len(BANDS_HZ) == 10


def test_values_are_clamped() -> None:
    eq = Equalizer(preamp_db=40, gains_db=(-30.0, *(0.0,) * 8, 99.0))
    assert eq.preamp_db == GAIN_RANGE_DB
    assert eq.gains_db[0] == -GAIN_RANGE_DB
    assert eq.gains_db[-1] == GAIN_RANGE_DB


def test_gain_count_is_validated() -> None:
    with pytest.raises(ValueError, match="expected 10 band gains"):
        Equalizer(gains_db=(1.0, 2.0))


def test_helpers_return_new_values() -> None:
    eq = Equalizer().with_enabled(True).with_gain(3, 4.5).with_preamp(-2)
    assert eq.enabled
    assert eq.gains_db[3] == 4.5
    assert eq.preamp_db == -2
    assert eq.active
    assert Equalizer().with_gain(0, 20).gains_db[0] == GAIN_RANGE_DB  # still clamped


def test_enabled_but_flat_is_not_active() -> None:
    assert not Equalizer(enabled=True).active


@pytest.mark.parametrize("name", list(PRESETS))
def test_presets_are_valid_and_recognised(name: str) -> None:
    gains = PRESETS[name]
    assert len(gains) == len(BANDS_HZ)
    assert all(-GAIN_RANGE_DB <= g <= GAIN_RANGE_DB for g in gains)
    eq = Equalizer().with_preset(name)
    assert eq.gains_db == gains
    assert eq.preset_name() == name


def test_custom_gains_match_no_preset() -> None:
    assert Equalizer().with_gain(4, 1.0).preset_name() is None
    assert Equalizer(gains_db=FLAT).preset_name() == "Flat"
