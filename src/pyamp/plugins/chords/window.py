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

"""The Chords window: the chord playing now, the next ones, and a strip of the song's
chords scrolling past as it plays (click one to go there)."""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pyamp.plugins.chords.analysis import Chord, ChordChart
from pyamp.ui import file_dialogs
from pyamp.ui.dialog_parts import (
    StatusState,
    heading,
    inset,
    plain_button,
    set_status,
    status_label,
)
from pyamp.ui.skin import DEFAULT_SKIN, Skin

#: How often the chords follow the playback while the window shows.
TICK_MS = 40
#: The strip's scale, and where on it "now" is (a fraction of its width).
PIXELS_PER_SECOND = 48.0
NOW_AT = 1 / 3
#: Chords shown after the current one.
UPCOMING = 3
#: Transposing goes at most this far either way.
MAX_TRANSPOSE = 11
#: The size of the chord playing now.
BIG_POINTS = 44
#: Shown for the chord when there's none (silence, before the song, nothing clear).
NO_CHORD = "\N{EN DASH}"


class ChordStrip(QWidget):
    """The song's chords as blocks on a time line that scrolls under a fixed "now"."""

    seek_requested = Signal(float)  # seconds

    def __init__(self, skin: Skin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._skin = skin
        self._chart: ChordChart | None = None
        self._position = 0.0
        #: Whether chords show their bass note (C/E).
        self.show_bass = True
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_chart(self, chart: ChordChart | None) -> None:
        self._chart = chart
        self.update()

    def set_position(self, seconds: float) -> None:
        if seconds != self._position:
            self._position = seconds
            self.update()

    def seconds_at(self, x: float) -> float:
        return self._position + (x - self.width() * NOW_AT) / PIXELS_PER_SECOND

    def mousePressEvent(self, event: QMouseEvent) -> None:
        chart = self._chart
        index = chart.index_at(self.seconds_at(event.position().x())) if chart else None
        if chart is not None and index is not None:
            self.seek_requested.emit(chart.segments[index].start)
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        skin = self._skin
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(skin.panel))
        chart = self._chart
        now_x = self.width() * NOW_AT
        if chart is not None:
            current = chart.index_at(self._position)
            first, last = self.seconds_at(0), self.seconds_at(self.width())
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            for index, segment in enumerate(chart.segments):
                if segment.end < first or segment.start > last or segment.chord is None:
                    continue
                x0 = now_x + (segment.start - self._position) * PIXELS_PER_SECOND
                x1 = now_x + (segment.end - self._position) * PIXELS_PER_SECOND
                block = QRectF(x0 + 1, 6, max(x1 - x0 - 2, 1), self.height() - 12)
                playing = index == current
                painter.setPen(QPen(QColor(skin.border), 1))
                painter.setBrush(QColor(skin.selection if playing else skin.inset))
                painter.drawRoundedRect(block, 3, 3)
                painter.setPen(QColor(skin.lcd if playing else skin.text))
                room = block.adjusted(4, 0, -2, 0)
                painter.drawText(
                    room,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    strip_label(chart, segment.chord, room.width(), metrics, bass=self.show_bass),
                )
        painter.setPen(QPen(QColor(skin.accent), 2))
        painter.drawLine(round(now_x), 0, round(now_x), self.height())
        painter.end()


def strip_label(
    chart: ChordChart, chord: Chord, width: float, metrics: QFontMetrics, *, bass: bool
) -> str:
    """A chord's name as it fits in ``width``: its bass note goes first (C/E becomes C),
    then the name is cut short."""
    name = chart.chord_name(chord, bass=bass)
    if bass and chord.bass is not None and metrics.horizontalAdvance(name) > width:
        name = chart.chord_name(chord, bass=False)
    return metrics.elidedText(name, Qt.TextElideMode.ElideRight, max(int(width), 0))


def _blend(colour: str, into: str, amount: float) -> str:
    """``colour`` faded ``amount`` of the way into ``into`` (both #rrggbb)."""
    a, b = QColor(colour), QColor(into)
    mixed = QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )
    return mixed.name()


class ChordWindow(QDialog):
    """Follows the playback with ``position`` (seconds) and goes elsewhere with ``seek``."""

    #: Shown (True) or hidden: the chords are only looked for while it can be seen.
    visibility_changed = Signal(bool)
    #: The "Bass notes" box was ticked (True) or cleared.
    show_bass_changed = Signal(bool)

    def __init__(
        self,
        position: Callable[[], float],
        seek: Callable[[float], None],
        skin: Skin = DEFAULT_SKIN,
        parent: QWidget | None = None,
        *,
        show_bass: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chords")
        self.setModal(False)
        self.resize(520, 300)
        self._position = position
        self._chart: ChordChart | None = None  # as found, before transposing
        self._shown: ChordChart | None = None  # as shown
        self._transpose = 0
        self._title = ""
        self._current: int | None = -1  # the segment shown (-1: none yet)
        self._show_bass = show_bass
        # The bass note (the "/E" of C/E): half the chord's size, a little dimmer.
        self._bass_style = (
            f"font-size: {BIG_POINTS // 2}pt; color: {_blend(skin.lcd, skin.panel, 0.4)}"
        )

        self._track = QLabel()
        self._track.setWordWrap(True)
        self._status = status_label()

        panel = self._panel(skin, seek)
        controls = self._controls()

        layout = QVBoxLayout(self)
        layout.addWidget(heading("Chords"))
        layout.addWidget(self._track)
        layout.addWidget(panel, 1)
        layout.addLayout(controls)
        layout.addWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)
        self.show_chart(None)

    def _panel(self, skin: Skin, seek: Callable[[float], None]) -> QFrame:
        """The chord now, the next ones, and the strip."""
        self.chord_label = QLabel(NO_CHORD)
        self.chord_label.setTextFormat(Qt.TextFormat.RichText)
        big = QFont(self.font())
        big.setPointSize(BIG_POINTS)
        big.setBold(True)
        self.chord_label.setFont(big)
        self.chord_label.setStyleSheet(f"color: {skin.lcd}; background: transparent;")
        self.next_label = QLabel()
        self.next_label.setStyleSheet(f"color: {skin.text_dim}; background: transparent;")
        self.strip = ChordStrip(skin)
        self.strip.show_bass = self._show_bass
        self.strip.seek_requested.connect(seek)

        panel = inset()
        panel.setStyleSheet(f"QFrame#inset {{ background: {skin.panel}; }}")
        panel_layout = QVBoxLayout(panel)
        panel_layout.addWidget(self.chord_label)
        panel_layout.addWidget(self.next_label)
        panel_layout.addWidget(self.strip)

        return panel

    def _controls(self) -> QHBoxLayout:
        """The key, transposing, and the chord sheet's buttons."""
        self.key_label = QLabel()
        self._down = plain_button("\N{MINUS SIGN}")
        self._down.setToolTip("Transpose down a semitone")
        self._down.clicked.connect(self._transpose_down)
        self.transpose_label = QLabel()
        self.transpose_label.setMinimumWidth(28)
        self.transpose_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._up = plain_button("+")
        self._up.setToolTip("Transpose up a semitone")
        self._up.clicked.connect(self._transpose_up)
        self.copy_button = plain_button("Copy")
        self.copy_button.setToolTip("Copy the chord sheet")
        self.copy_button.clicked.connect(self.copy_sheet)
        self.save_button = plain_button("Save…")
        self.save_button.setToolTip("Save the chord sheet as a text file")
        self.save_button.clicked.connect(self.save_sheet)
        self.bass_box = QCheckBox("Bass notes")
        self.bass_box.setToolTip("Show the note in the bass when it isn't the root (C/E)")
        self.bass_box.setChecked(self._show_bass)
        self.bass_box.toggled.connect(self.set_show_bass)

        controls = QHBoxLayout()
        controls.addWidget(self.key_label)
        controls.addStretch()
        controls.addWidget(self.bass_box)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Transpose"))
        controls.addWidget(self._down)
        controls.addWidget(self.transpose_label)
        controls.addWidget(self._up)
        controls.addSpacing(12)
        controls.addWidget(self.copy_button)
        controls.addWidget(self.save_button)

        return controls

    # -- What's shown --------------------------------------------------------- #

    @property
    def chart(self) -> ChordChart | None:
        """The chart as shown (transposed)."""
        return self._shown

    @property
    def transpose(self) -> int:
        return self._transpose

    @property
    def show_bass(self) -> bool:
        return self._show_bass

    def set_show_bass(self, show: bool) -> None:
        """Show chords with their bass note (C/E), or without (C), the sheet too."""
        if show == self._show_bass:
            return
        self._show_bass = show
        self.bass_box.setChecked(show)
        self.strip.show_bass = show
        self.strip.update()
        self._current = -1
        self.tick()
        self.show_bass_changed.emit(show)

    def show_track(self, title: str) -> None:
        """A new track: its chart comes later (:meth:`show_chart`), untransposed."""
        self._title = title
        self._track.setText(title)
        self._transpose = 0
        self.show_chart(None)

    def show_status(self, text: str, state: StatusState = "") -> None:
        set_status(self._status, text, state)

    def show_chart(self, chart: ChordChart | None) -> None:
        self._chart = chart
        self._refresh()

    def set_transpose(self, semitones: int) -> None:
        self._transpose = max(-MAX_TRANSPOSE, min(MAX_TRANSPOSE, semitones))
        self._refresh()

    def _transpose_down(self) -> None:
        self.set_transpose(self._transpose - 1)

    def _transpose_up(self) -> None:
        self.set_transpose(self._transpose + 1)

    def _refresh(self) -> None:
        chart = self._chart.transposed(self._transpose) if self._chart else None
        self._shown = chart
        has_chords = chart is not None and any(s.chord for s in chart.segments)
        self.strip.set_chart(chart)
        self.key_label.setText(f"Key: {chart.key.name()}" if chart and chart.key else "")
        self.transpose_label.setText(f"{self._transpose:+d}" if self._transpose else "0")
        for widget in (self._down, self._up, self.copy_button, self.save_button):
            widget.setEnabled(has_chords)
        self._current = -1
        self.tick()
        self._run_timer()

    def tick(self) -> None:
        """Follow the playback: the chord now, the next few, the strip."""
        chart = self._shown
        seconds = self._position()
        self.strip.set_position(seconds)
        index = chart.index_at(seconds) if chart else None
        if index == self._current:
            return
        self._current = index
        if chart is None or index is None:
            self.chord_label.setText(NO_CHORD)
            self.next_label.setText("")
            return
        chord = chart.segments[index].chord
        self.chord_label.setText(self._big_name(chart, chord))
        upcoming = [
            chart.chord_name(s.chord, bass=self._show_bass)
            for s in chart.segments[index + 1 :]
            if s.chord
        ][:UPCOMING]
        self.next_label.setText(("then  " + "  ·  ".join(upcoming)) if upcoming else "")

    def _big_name(self, chart: ChordChart, chord: Chord | None) -> str:
        """The chord now, as rich text: its bass note smaller and dimmer (C/E)."""
        if chord is None:
            return NO_CHORD
        name = html.escape(chart.chord_name(chord, bass=False))
        bass = chord.bass_name(flats=chart.flats) if self._show_bass else None
        if bass is None:
            return name
        return f'{name}<span style="{self._bass_style}">/{html.escape(bass)}</span>'

    def _run_timer(self) -> None:
        if self.isVisible() and self._shown is not None:
            self._timer.start()
        else:
            self._timer.stop()

    def showEvent(self, event: object) -> None:
        super().showEvent(event)  # type: ignore[arg-type]
        self._run_timer()
        self.visibility_changed.emit(True)

    def hideEvent(self, event: object) -> None:
        super().hideEvent(event)  # type: ignore[arg-type]
        self._run_timer()
        self.visibility_changed.emit(False)

    # -- The chord sheet ------------------------------------------------------ #

    def sheet(self) -> str:
        return self._shown.sheet(self._title, bass=self._show_bass) if self._shown else ""

    def copy_sheet(self) -> None:
        if self._shown is not None:
            QGuiApplication.clipboard().setText(self.sheet())
            self.show_status("Chord sheet copied.", "count")

    def save_sheet(self) -> None:
        if self._shown is None:
            return
        text = self.sheet()
        suggested = f"{_file_name(self._title) or 'Chords'} - chords.txt"
        file_dialogs.choose_save_file(
            self,
            "Save Chord Sheet",
            str(Path.home() / suggested),
            "Text files (*.txt)",
            lambda picked: self._write(picked, text),
        )

    def _write(self, picked: list[Path], text: str) -> None:
        if not picked:
            return
        try:
            picked[0].write_text(text, encoding="utf-8")
        except OSError as exc:
            self.show_status(f"Couldn't save the chord sheet: {exc.strerror or exc}", "problem")
            return
        self.show_status(f"Saved {picked[0].name}.", "count")


def _file_name(title: str) -> str:
    """``title`` without the characters file systems don't allow."""
    return "".join(c for c in title if c not in '<>:"/\\|?*').strip()
