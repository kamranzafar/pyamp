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

"""The "LCD" panel: status, elapsed time, scrolling title and visualiser."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QMouseEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QWidget

from pyamp.core.events import (
    EventBus,
    PlaybackError,
    PlaybackStateChanged,
    PositionChanged,
    SpectrumUpdated,
    TrackChanged,
)
from pyamp.core.models import PlaybackState, format_duration
from pyamp.ui.skin import Skin
from pyamp.ui.widgets.marquee import MarqueeLabel
from pyamp.ui.widgets.spectrum import SpectrumWidget

_STATUS_GLYPHS = {
    PlaybackState.PLAYING: "▶",
    PlaybackState.PAUSED: "❚❚",
    PlaybackState.STOPPED: "■",
}
BLINK_MS = 500


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self.clicked.emit()
        super().mousePressEvent(ev)


class LcdPanel(QFrame):
    def __init__(
        self,
        bus: EventBus,
        skin: Skin,
        band_count: int,
        *,
        show_remaining: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("lcdPanel")
        self._state = PlaybackState.STOPPED
        self._position_ms = 0
        self._duration_ms = 0
        self._show_remaining = show_remaining

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        big = QFont(mono)
        big.setPointSize(26)
        big.setBold(True)
        lcd_style = f"color: {skin.lcd}; background: transparent;"

        self._status = QLabel(_STATUS_GLYPHS[self._state])
        self._status.setStyleSheet(lcd_style)
        self._time = ClickableLabel()
        self._time.setFont(big)
        self._time.setStyleSheet(lcd_style)
        self._time.setToolTip("Click to toggle elapsed / remaining time")
        self._time.clicked.connect(self._toggle_time_mode)
        # The time blinks while paused. Keep its space reserved when hidden, or the
        # layout collapses and re-expands around it and the whole panel jumps.
        policy = self._time.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._time.setSizePolicy(policy)
        self._title = MarqueeLabel(QColor(skin.lcd), mono)
        self._spectrum = SpectrumWidget(band_count, skin)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(10)
        layout.addWidget(self._status, 0, 0)
        layout.addWidget(self._time, 0, 1)
        layout.addWidget(self._title, 0, 2)
        layout.addWidget(self._spectrum, 1, 0, 1, 3)
        layout.setColumnStretch(2, 1)

        # Winamp blinks the time display while paused.
        self._blink = QTimer(self)
        self._blink.setInterval(BLINK_MS)
        self._blink.timeout.connect(lambda: self._time.setVisible(not self._time.isVisible()))

        bus.subscribe(TrackChanged, self._on_track)
        bus.subscribe(PlaybackStateChanged, self._on_state)
        bus.subscribe(PositionChanged, self._on_position)
        bus.subscribe(SpectrumUpdated, lambda e: self._spectrum.set_levels(e.levels))
        bus.subscribe(PlaybackError, lambda e: self._title.set_text(f"Error: {e.message}"))
        self._render_time()

    @property
    def show_remaining(self) -> bool:
        return self._show_remaining

    def _toggle_time_mode(self) -> None:
        self._show_remaining = not self._show_remaining
        self._render_time()

    def _on_track(self, event: TrackChanged) -> None:
        if event.track is None or event.index is None:
            self._title.set_text("")
            return
        length = f" ({format_duration(event.track.duration_ms)})" if event.track.duration_ms else ""
        self._title.set_text(f"{event.index + 1}. {event.track.display_title}{length}")

    def _on_state(self, event: PlaybackStateChanged) -> None:
        self._state = event.state
        self._status.setText(_STATUS_GLYPHS[event.state])
        if event.state is PlaybackState.PAUSED:
            self._blink.start()
        else:
            self._blink.stop()
            self._time.setVisible(True)
        self._render_time()

    def _on_position(self, event: PositionChanged) -> None:
        self._position_ms, self._duration_ms = event.position_ms, event.duration_ms
        self._render_time()

    def _render_time(self) -> None:
        if self._state is PlaybackState.STOPPED:
            self._time.setText(" 0:00")
        elif self._show_remaining and self._duration_ms:
            self._time.setText("-" + format_duration(self._duration_ms - self._position_ms))
        else:
            self._time.setText(" " + format_duration(self._position_ms))
