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

"""Seek and volume sliders bound to the event bus."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QSlider, QStyle, QWidget

from pyamp.core.events import EventBus, PositionChanged, TrackChanged, VolumeChanged

VOLUME_STEPS = 100


class JumpSlider(QSlider):
    """A slider that jumps straight to the clicked position (instead of paging)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() is Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(ev.position().x()), self.width()
            )
            self.setValue(value)
        super().mousePressEvent(ev)


class SeekSlider(JumpSlider):
    """Follows the playback position and emits :attr:`seek_requested` when the user seeks."""

    seek_requested = Signal(int)

    def __init__(self, bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 0)
        self.setToolTip("Seek")
        self.sliderReleased.connect(lambda: self.seek_requested.emit(self.value()))
        bus.subscribe(PositionChanged, self._on_position)
        bus.subscribe(TrackChanged, lambda _: self._set_length(0))

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        super().mousePressEvent(ev)
        # A click that is not followed by a drag still has to seek.
        if not self.isSliderDown():
            self.seek_requested.emit(self.value())

    def _on_position(self, event: PositionChanged) -> None:
        if self.isSliderDown():
            return  # don't fight the user while they drag
        self.blockSignals(True)
        self._set_length(event.duration_ms)
        self.setValue(event.position_ms)
        self.blockSignals(False)

    def _set_length(self, duration_ms: int) -> None:
        # Nothing to seek in (live stream, or no track): disabled, and the skin hides the knob.
        self.setRange(0, duration_ms)
        self.setEnabled(duration_ms > 0)


class VolumeSlider(JumpSlider):
    """Emits :attr:`volume_requested` with a 0.0-1.0 value."""

    volume_requested = Signal(float)

    def __init__(self, bus: EventBus, initial: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, VOLUME_STEPS)
        self.setValue(round(initial * VOLUME_STEPS))
        self.setFixedWidth(90)
        self.setToolTip("Volume")
        self.valueChanged.connect(lambda v: self.volume_requested.emit(v / VOLUME_STEPS))
        bus.subscribe(VolumeChanged, self._on_volume)

    def _on_volume(self, event: VolumeChanged) -> None:
        self.blockSignals(True)
        self.setValue(round(event.volume * VOLUME_STEPS))
        self.blockSignals(False)
