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

"""The "LCD" panel: status, elapsed time, scrolling title and visualiser."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QMouseEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QWidget

from pyamp.core.events import (
    AudioInfoChanged,
    EventBus,
    PlaybackError,
    PlaybackStateChanged,
    PositionChanged,
    SpectrumUpdated,
    StreamTitleChanged,
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


def audio_info_text(info: AudioInfoChanged) -> str:
    """Winamp-style ``320 kbps · 44.1 kHz`` (whichever parts are known)."""
    parts = []
    if info.bitrate_kbps:
        parts.append(f"{info.bitrate_kbps} kbps")
    if info.sample_rate_hz:
        parts.append(f"{round(info.sample_rate_hz / 1000, 1):g} kHz")
    return " · ".join(parts)


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
        self._track_label = ""
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
        small = QFont(mono)
        small.setPointSizeF(max(mono.pointSizeF() * 0.8, 8.0))
        self._audio_info = QLabel()
        self._audio_info.setFont(small)
        self._audio_info.setStyleSheet(lcd_style)
        self._audio_info.setToolTip("Bitrate and sample rate of what's playing")
        self._spectrum = SpectrumWidget(band_count, skin)
        self._lay_out()

        # Winamp blinks the time display while paused.
        self._blink = QTimer(self)
        self._blink.setInterval(BLINK_MS)
        self._blink.timeout.connect(lambda: self._time.setVisible(not self._time.isVisible()))

        bus.subscribe(TrackChanged, self._on_track)
        bus.subscribe(StreamTitleChanged, self._on_stream_title)
        bus.subscribe(PlaybackStateChanged, self._on_state)
        bus.subscribe(PositionChanged, self._on_position)
        bus.subscribe(AudioInfoChanged, lambda e: self._audio_info.setText(audio_info_text(e)))
        bus.subscribe(SpectrumUpdated, lambda e: self._spectrum.set_levels(e.levels))
        bus.subscribe(PlaybackError, lambda e: self._title.set_text(f"Error: {e.message}"))
        self._render_time()

    def _lay_out(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)
        # The big time spans the title and the small audio line under it.
        layout.addWidget(self._status, 0, 0, 2, 1)
        layout.addWidget(self._time, 0, 1, 2, 1)
        layout.addWidget(self._title, 0, 2)
        layout.addWidget(self._audio_info, 1, 2)
        layout.addWidget(self._spectrum, 2, 0, 1, 3)
        layout.setColumnStretch(2, 1)

    @property
    def show_remaining(self) -> bool:
        return self._show_remaining

    def _toggle_time_mode(self) -> None:
        self._show_remaining = not self._show_remaining
        self._render_time()

    def _on_track(self, event: TrackChanged) -> None:
        self._track_label = ""
        if event.track is None or event.index is None:
            self._title.set_text("")
            return
        length = f" ({format_duration(event.track.duration_ms)})" if event.track.duration_ms else ""
        self._track_label = f"{event.index + 1}. {event.track.display_title}"
        self._title.set_text(f"{self._track_label}{length}")

    def _on_stream_title(self, event: StreamTitleChanged) -> None:
        if self._track_label:  # "3. Groove Salad — Artist - Song"
            self._title.set_text(f"{self._track_label} — {event.title}")

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
