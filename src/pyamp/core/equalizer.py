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
"""A 10-band graphic equaliser model (pure: no Qt, no audio).

Backends that can filter audio (see ``AudioBackend.supports_equalizer``) turn an
:class:`Equalizer` into actual filters. Preset values are PyAmp's own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Band centre frequencies in Hz: the classic 10-band layout.
BANDS_HZ: tuple[int, ...] = (60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000)
#: Gains and preamp are limited to plus or minus this many dB.
GAIN_RANGE_DB = 12.0

FLAT: tuple[float, ...] = (0.0,) * len(BANDS_HZ)

PRESETS: dict[str, tuple[float, ...]] = {
    "Flat": FLAT,
    "Bass Boost": (7.0, 6.0, 4.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "Treble Boost": (0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 3.5, 5.5, 6.0, 6.5),
    "Bass & Treble": (6.0, 4.5, 2.0, 0.0, -1.5, 0.0, 2.0, 4.5, 5.0, 5.5),
    "Vocal": (-2.5, -1.5, 0.0, 2.5, 4.0, 4.0, 2.5, 0.0, -1.0, -1.5),
    "Rock": (4.5, 3.0, 1.0, -1.5, -2.0, 0.5, 2.5, 4.0, 4.5, 4.5),
    "Pop": (-1.0, 1.0, 3.0, 4.0, 3.0, 0.5, -0.5, -0.5, -1.0, -1.0),
    "Electronic": (5.5, 4.5, 1.5, 0.0, -1.5, 1.0, 0.5, 2.5, 4.0, 5.0),
    "Classical": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -2.5, -3.0, -3.0, -4.0),
    "Small Speakers": (5.0, 4.0, 3.0, 1.0, 0.0, -0.5, 1.0, 2.5, 3.5, 3.0),
}


def _clamp(db: float) -> float:
    return max(-GAIN_RANGE_DB, min(float(db), GAIN_RANGE_DB))


@dataclass(frozen=True, slots=True)
class Equalizer:
    enabled: bool = False
    preamp_db: float = 0.0
    gains_db: tuple[float, ...] = FLAT

    def __post_init__(self) -> None:
        gains = tuple(self.gains_db)
        if len(gains) != len(BANDS_HZ):
            raise ValueError(f"expected {len(BANDS_HZ)} band gains, got {len(gains)}")
        object.__setattr__(self, "gains_db", tuple(_clamp(g) for g in gains))
        object.__setattr__(self, "preamp_db", _clamp(self.preamp_db))

    @property
    def is_flat(self) -> bool:
        """True if it wouldn't change the sound (so backends can skip filtering)."""
        return self.preamp_db == 0.0 and all(g == 0.0 for g in self.gains_db)

    @property
    def active(self) -> bool:
        return self.enabled and not self.is_flat

    def with_gain(self, band: int, db: float) -> Equalizer:
        gains = list(self.gains_db)
        gains[band] = db
        return replace(self, gains_db=tuple(gains))

    def with_preamp(self, db: float) -> Equalizer:
        return replace(self, preamp_db=db)

    def with_preset(self, name: str) -> Equalizer:
        return replace(self, gains_db=PRESETS[name])

    def with_enabled(self, enabled: bool) -> Equalizer:
        return replace(self, enabled=enabled)

    def preset_name(self) -> str | None:
        """The preset these gains match, if any."""
        return next((n for n, gains in PRESETS.items() if gains == self.gains_db), None)
