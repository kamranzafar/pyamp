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
"""Animations and the visualiser only run while there's something to show someone."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor, QFont

from pyamp.app import Application
from pyamp.core.events import AudioFrame, SpectrumUpdated
from pyamp.ui.skin import DEFAULT_SKIN
from pyamp.ui.widgets.marquee import MarqueeLabel
from pyamp.ui.widgets.spectrum import SpectrumWidget
from tests.test_background_loader import wait_until
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


def test_marquee_ticks_only_while_scrolling_and_shown(qt_app: QCoreApplication) -> None:
    label = MarqueeLabel(QColor("white"), QFont())
    label.resize(100, 20)
    label.show()
    label.set_text("Short")
    assert not label._timer.isActive()
    label.set_text("A title much, much too long to fit in a hundred pixels at all")
    assert label._timer.isActive()
    label.hide()
    assert not label._timer.isActive()
    label.show()
    assert label._timer.isActive()
    label.resize(4000, 20)  # now it fits
    assert not label._timer.isActive()
    label.close()


def test_spectrum_animates_only_until_the_bars_settle(qt_app: QCoreApplication) -> None:
    widget = SpectrumWidget(4, DEFAULT_SKIN)
    widget.show()
    assert not widget.animating  # silence: no timer at all
    widget.set_levels([1.0, 0.5, 0.2, 0.0])
    assert widget.animating
    widget.set_levels([0.0] * 4)
    wait_until(lambda: not widget.animating, timeout=10)  # fell to zero, then stopped
    widget.hide()
    widget.set_levels([1.0] * 4)
    assert not widget.animating  # nobody can see it
    widget.show()
    assert widget.animating
    widget.close()


def test_hiding_the_window_pauses_the_visualiser(app: Application) -> None:  # noqa: F811
    window = app.window
    assert window.seen
    published: list[SpectrumUpdated] = []
    app.bus.subscribe(SpectrumUpdated, published.append)
    frame = AudioFrame(np.ones(2048, dtype=np.float32), 44_100)

    window.hide()  # closed to the menu bar / tray
    assert not window.seen
    app.bus.publish(frame)
    assert not any(any(e.levels) for e in published)

    window.show()
    window.setWindowState(Qt.WindowState.WindowMinimized)
    QCoreApplication.processEvents()
    assert not window.seen

    window.setWindowState(Qt.WindowState.WindowNoState)
    window.show()
    QCoreApplication.processEvents()
    assert window.seen
    app.bus.publish(frame)
    assert any(published[-1].levels)
