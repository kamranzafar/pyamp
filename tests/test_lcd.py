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

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QRect

from pyamp.core.events import EventBus, PlaybackStateChanged
from pyamp.core.models import PlaybackState
from pyamp.ui.skin import DEFAULT_SKIN
from pyamp.ui.widgets.lcd import LcdPanel


def test_pause_blink_does_not_shift_layout(qt_app: QCoreApplication, bus: EventBus) -> None:
    """Regression: hiding the blinking time label used to collapse the layout,
    making the whole panel jump twice a second while paused."""
    panel = LcdPanel(bus, DEFAULT_SKIN, band_count=10)
    panel.resize(440, 100)
    panel.show()
    bus.publish(PlaybackStateChanged(PlaybackState.PAUSED))

    def geometries() -> list[QRect]:
        QCoreApplication.processEvents()  # let the layout settle
        return [panel._title.geometry(), panel._spectrum.geometry()]

    shown = geometries()
    panel._time.setVisible(False)  # what the blink timer does
    assert geometries() == shown
