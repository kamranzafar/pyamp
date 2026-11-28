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

"""Chords plugin: View → Chords… shows the chords of the song that's playing.

The chords come from the same decoded audio as the visualiser (the app's
:class:`~pyamp.audio.track_analysis.AudioFeed`), so nothing is decoded or fetched twice: a
file's are found within a few seconds; a song from a server's as it buffers; a station's
as it plays. They're kept, so a song is at once the next time. The window shows the chord
playing, the next few, and a strip of chords that scrolls as the song plays (click one to
go there). The chords can be transposed, and copied or saved as a chord sheet. A chord
with a note other than its root held in the bass shows it, as C/E (the "Bass notes" box
turns that off).

Only while the window is open is anything analysed for it.
"""

from __future__ import annotations

from collections.abc import Callable

from pyamp.core.events import TrackChanged, Unsubscribe
from pyamp.core.models import StreamUrl, Track
from pyamp.plugins.base import PluginContext
from pyamp.plugins.chords.cache import ChordCache
from pyamp.plugins.chords.settings import ChordSettings
from pyamp.plugins.chords.tracker import ChordTracker
from pyamp.plugins.chords.window import ChordWindow
from pyamp.ui.dialog_parts import StatusState

MENU = "&View"


class ChordsPlugin:
    name = "chords"

    def __init__(
        self,
        cache: Callable[[], ChordCache] = ChordCache,
        settings: Callable[[], ChordSettings] = ChordSettings,
    ) -> None:
        self._make_cache = cache
        self._make_settings = settings
        self._context: PluginContext | None = None
        self._tracker: ChordTracker | None = None
        self.window: ChordWindow | None = None
        self._track: Track | None = None
        self._stop_listening: Unsubscribe | None = None

    def activate(self, context: PluginContext) -> None:
        self._context = context
        self._track = context.playlist.current_track
        self._stop_listening = context.bus.subscribe(TrackChanged, self._on_track)
        ui = context.ui()
        if ui is not None:
            ui.add_menu_separator(MENU)
            ui.add_menu_action(MENU, "&Chords…", self.show, "K")

    def deactivate(self) -> None:
        if self._stop_listening is not None:
            self._stop_listening()
            self._stop_listening = None
        if self._tracker is not None:
            self._tracker.shutdown()
            self._tracker = None
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
            self.window = None
        self._context = None

    def show(self) -> ChordWindow | None:
        context = self._context
        if context is None:
            return None
        if self.window is None:
            ui = context.ui()
            feed, player = context.feed, context.player
            settings = self._make_settings()
            self.window = ChordWindow(
                feed.position if feed is not None else lambda: player.position_ms / 1000,
                lambda seconds: player.seek(round(seconds * 1000)),
                parent=ui.dialog_parent() if ui is not None else None,  # type: ignore[arg-type]
                show_bass=settings.show_bass,
            )
            self.window.show_bass_changed.connect(settings.set_show_bass)
            if feed is not None:
                self._tracker = ChordTracker(feed, self._make_cache())
                self._tracker.changed.connect(self._refresh)
            self.window.visibility_changed.connect(self._on_visibility)
            self.window.show_track(self._title())
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        return self.window

    def _title(self) -> str:
        return self._track.display_title if self._track is not None else ""

    def _on_visibility(self, visible: bool) -> None:
        """Chords are only looked for while they can be seen."""
        if self._tracker is not None:
            if visible:
                self._tracker.start()
            else:
                self._tracker.stop()
        self._refresh()

    def _on_track(self, event: TrackChanged) -> None:
        self._track = event.track
        if self.window is not None:
            self.window.show_track(self._title())  # the tracker has followed it already
            self._refresh()

    def _refresh(self) -> None:
        window, tracker = self.window, self._tracker
        if window is None:
            return
        window.show_chart(tracker.chart if tracker is not None else None)
        text, state = self._status()
        window.show_status(text, state)

    def _status(self) -> tuple[str, StatusState]:
        tracker, track, context = self._tracker, self._track, self._context
        if track is None:
            return "Nothing is playing.", ""
        if tracker is None or context is None or context.feed is None:
            return "Chords can't be found here: there's no audio analysis.", "problem"
        streamed = isinstance(track.location, StreamUrl)
        if tracker.state == "nothing" and streamed and not context.feed.follows_streams:
            return "Chords of streams need the mpv audio backend.", ""
        if tracker.state == "done":
            chart = tracker.chart
            changes = sum(1 for s in chart.segments if s.chord) if chart is not None else 0
            if changes:
                return f"{changes} chord changes found.", "count"
        return _MESSAGES.get((tracker.state, streamed), ("", ""))


#: What the window says for the tracker's state, for a file (False) or a stream (True).
_MESSAGES: dict[tuple[str, bool], tuple[str, StatusState]] = {
    ("nothing", True): ("This stream can't be followed for its chords.", ""),
    ("finding", False): ("Finding the chords…", "busy"),
    ("finding", True): ("Finding the chords as the song comes in…", "busy"),
    ("listening", True): ("Following the station's chords as they play…", "busy"),
    ("done", False): ("No chords found in this track.", ""),
    ("done", True): ("No chords found in this track.", ""),
    ("failed", False): ("This track couldn't be analysed.", "problem"),
    ("failed", True): ("This track couldn't be analysed.", "problem"),
}
