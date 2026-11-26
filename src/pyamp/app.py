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

"""Application entry point and composition root.

This is the only module that knows about every concrete class. It builds the
object graph (dependency injection by hand, no framework), restores the user's
session and saves it again on exit. Everything else receives its
collaborators through constructors, which is what makes the pieces testable
and swappable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pyamp import APP_NAME, __version__
from pyamp.audio import libmpv_loader
from pyamp.audio.backend import AudioBackend
from pyamp.audio.registry import (
    BackendUnavailableError,
    UnknownBackendError,
    available_backends,
    create_backend,
)
from pyamp.audio.track_analysis import AnalysedSpectrum, TrackAnalyser
from pyamp.config import Settings, SettingsStore
from pyamp.core.equalizer import Equalizer
from pyamp.core.events import (
    EventBus,
    LibraryChanged,
    SavedPlaylistEdited,
    SavedPlaylistsChanged,
)
from pyamp.core.models import RepeatMode, ReplayGainMode
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.dsp.spectrum import SpectrumService
from pyamp.library.catalog import Catalog
from pyamp.library.loader import TrackLoader
from pyamp.library.local_source import LocalSource
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.library.playlists_source import PlaylistsSource
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.library.scanner import LibraryScanner
from pyamp.library.sources import MediaSources
from pyamp.plugins import PluginContext, PluginManager
from pyamp.ui.background_loader import BackgroundTrackLoader
from pyamp.ui.icons import app_icon
from pyamp.ui.library_scan import BackgroundLibraryScanner
from pyamp.ui.main_window import MainWindow, UiServices
from pyamp.ui.playlist_session import PlaylistSession
from pyamp.ui.skin import DEFAULT_SKIN

log = logging.getLogger(__name__)

#: The library is rescanned this long after start, once the music has started.
LIBRARY_SCAN_DELAY_MS = 5000
#: Played through when the chosen backend can't be: QtMultimedia ships with PyAmp.
FALLBACK_BACKEND = "qt"


def apply_libmpv_preference(requested: str | None, settings: Settings) -> None:
    """Use ``--libmpv`` if given (and remember it), else the saved preference."""
    if requested:
        settings.libmpv = requested
    try:
        libmpv_loader.set_preference(settings.libmpv)
    except ValueError:
        log.warning("Unknown libmpv preference %r; using 'auto'", settings.libmpv)
        settings.libmpv = "auto"
        libmpv_loader.set_preference("auto")


def version_text() -> str:
    """The version, plus which libmpv the mpv backend would use (loaded only when asked)."""
    apply_libmpv_preference(None, SettingsStore().load())
    preference = libmpv_loader.get_preference()
    try:
        from pyamp.audio.mpv_backend import LIBMPV_SOURCE  # noqa: PLC0415
    except OSError as exc:
        libmpv = f"not available ({exc})"
    else:
        libmpv = str(LIBMPV_SOURCE)
    return f"pyamp {__version__}\nlibmpv: {libmpv}\nlibmpv preference: {preference}"


class _VersionAction(argparse.Action):
    def __call__(self, parser: argparse.ArgumentParser, *_: object) -> None:
        print(version_text())
        parser.exit()


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pyamp", description=f"{APP_NAME} music player")
    parser.add_argument("paths", nargs="*", type=Path, help="files, folders or playlists to play")
    parser.add_argument("--backend", choices=available_backends(), help="audio backend")
    parser.add_argument("--no-plugins", action="store_true", help="don't load plugins")
    parser.add_argument(
        "--libmpv",
        choices=libmpv_loader.PREFERENCES,
        help="which libmpv the mpv backend prefers: the system's or PyAmp's own "
        "(default auto: system on Linux, bundled elsewhere; remembered)",
    )
    parser.add_argument("--log-level", default="WARNING", help="e.g. DEBUG, INFO, WARNING")
    parser.add_argument(
        "--self-test", action="store_true", help="check this installation works, then exit"
    )
    parser.add_argument(
        "--version", action=_VersionAction, nargs=0, help="show version and libmpv, then exit"
    )
    return parser.parse_args(argv)


class Application:
    """Owns the long-lived services and the session lifecycle."""

    def __init__(
        self,
        args: argparse.Namespace,
        store: SettingsStore | None = None,
        saved_playlists: SavedPlaylists | None = None,
        catalog: Catalog | None = None,
    ) -> None:
        self._store = store or SettingsStore()
        self.settings = self._store.load()
        apply_libmpv_preference(args.libmpv, self.settings)  # before the backend loads

        self.bus = EventBus()
        self.playlist = Playlist(self.bus)
        self.player = Player(self._create_backend(args.backend), self.playlist, self.bus)
        self.spectrum = SpectrumService(self.bus)
        # Backends that can't report the audio they play (mpv) get their visualiser
        # levels from analysing the file separately.
        self.track_analyser: TrackAnalyser | None = None
        self.analysed_spectrum: AnalysedSpectrum | None = None
        if not self.player.provides_audio_frames:
            self.track_analyser = TrackAnalyser(self.spectrum.band_count)
            self.analysed_spectrum = AnalysedSpectrum(
                self.bus, self.track_analyser, self.spectrum.band_count, streams=self.player
            )
        self.playlist_formats = PlaylistFormatRegistry.default()
        self.loader = BackgroundTrackLoader(
            TrackLoader(MutagenMetadataReader(), self.playlist_formats)
        )
        self.catalog = catalog or Catalog()
        self.library = BackgroundLibraryScanner(
            self.catalog, LibraryScanner(self.catalog, MutagenMetadataReader()), self.bus
        )
        # The library window's sources: this computer first, then any from plugins.
        self.sources = MediaSources(self.bus)
        self.local_source = LocalSource(self.catalog, self.library.remove_source)
        self.sources.add(self.local_source)
        self.bus.subscribe(LibraryChanged, lambda _: self.sources.changed(self.local_source))
        self.plugins = PluginManager(
            PluginContext(
                self.bus,
                self.player,
                self.playlist,
                window_handle=self._window_handle,
                ui=lambda: getattr(self, "window", None),
                sources=self.sources,
            )
        )
        self.playlists = PlaylistSession(
            self.playlist,
            saved := saved_playlists or SavedPlaylists(),
            self.loader,
            self.bus,
            settings=self.settings,
            legacy_session=self._store.session_playlist,  # migrated once into "Default"
        )

        self.window = MainWindow(
            UiServices(
                bus=self.bus,
                player=self.player,
                playlist=self.playlist,
                loader=self.loader,
                playlist_formats=self.playlist_formats,
                settings=self.settings,
                spectrum_bands=self.spectrum.band_count,
                playlists=self.playlists,
                library=self.library,
                sources=self.sources,
                backends=tuple(available_backends()),
            ),
            DEFAULT_SKIN,
        )
        self.window.visibility_changed.connect(self._set_visualiser_active)
        # The saved playlists, after this computer in the library; their commands use the
        # window's dialogs.
        self.playlists_source = PlaylistsSource(
            saved,
            self.catalog,
            open_playlist=self.window.switch_playlist,
            rename_playlist=self.window.rename_saved_playlist,
            delete_playlist=self.window.delete_saved_playlist,
        )
        self.sources.add(self.playlists_source)
        # Shown again when playlists come and go, or one's tracks change (e.g. the
        # player's edits to the open one are saved).
        for event in (SavedPlaylistsChanged, SavedPlaylistEdited):
            self.bus.subscribe(event, lambda _: self.sources.changed(self.playlists_source))
        # Restore preferences *after* the UI exists, so widgets receive the events.
        self._apply_settings(self.settings)
        if not args.no_plugins:
            self.plugins.load_entry_points()

    def start(self, paths: Sequence[Path]) -> None:
        self.window.show()
        self.playlists.start()  # opens the playlist that was open last
        if paths:
            self.window.add_paths(paths, autoplay=True)
        self.library.scan_later(LIBRARY_SCAN_DELAY_MS)

    def shutdown(self) -> None:
        # Saves the open playlist, unless it's still loading (then its file is kept).
        self.playlists.shutdown()
        self.loader.shutdown()
        self.library.shutdown()
        self.catalog.close()
        self.settings.volume = self.player.volume
        self.settings.shuffle = self.player.shuffle
        self.settings.repeat = self.player.repeat.value
        self.settings.replay_gain = self.player.replay_gain.value
        eq = self.player.equalizer
        self.settings.equalizer_enabled = eq.enabled
        self.settings.equalizer_preamp = eq.preamp_db
        self.settings.equalizer_gains = list(eq.gains_db)
        try:
            self._store.save(self.settings)
        except OSError:
            log.exception("Could not save session")
        self.plugins.shutdown()
        if self.analysed_spectrum is not None and self.track_analyser is not None:
            self.analysed_spectrum.shutdown()
            self.track_analyser.shutdown()
        self.player.shutdown()

    def _set_visualiser_active(self, seen: bool) -> None:
        """The visualiser only works while the window can be seen (saves battery)."""
        self.spectrum.set_active(seen)
        if self.analysed_spectrum is not None:
            self.analysed_spectrum.set_active(seen)

    def _window_handle(self) -> int | None:
        """The main window's native handle (plugins are loaded after the window exists)."""
        window = getattr(self, "window", None)
        return int(window.winId()) if window is not None else None

    def _create_backend(self, requested: str | None) -> AudioBackend:
        """Create the requested backend and remember its name, so ``--backend`` sticks.

        If it can't be used, play through :data:`FALLBACK_BACKEND` instead. An unknown
        name is forgotten; an unavailable one (e.g. libmpv missing) is kept, so it's
        tried again next time.
        """
        name = requested or self.settings.backend
        try:
            backend = create_backend(name)
        except UnknownBackendError:
            log.warning("Unknown backend %r; falling back to %r", name, FALLBACK_BACKEND)
            self.settings.backend = FALLBACK_BACKEND
        except BackendUnavailableError as exc:
            log.warning("%s; falling back to %r", exc, FALLBACK_BACKEND)
            self.settings.backend = name
        else:
            self.settings.backend = name
            return backend
        return create_backend(FALLBACK_BACKEND)

    def _apply_settings(self, settings: Settings) -> None:
        self.player.set_volume(settings.volume)
        self.player.set_shuffle(settings.shuffle)
        try:
            self.player.set_repeat(RepeatMode(settings.repeat))
        except ValueError:
            self.player.set_repeat(RepeatMode.OFF)
        try:
            self.player.set_replay_gain(ReplayGainMode(settings.replay_gain))
        except ValueError:
            self.player.set_replay_gain(ReplayGainMode.OFF)
        try:
            equalizer = Equalizer(
                enabled=bool(settings.equalizer_enabled),
                preamp_db=float(settings.equalizer_preamp),
                gains_db=tuple(float(g) for g in settings.equalizer_gains),
            )
        except (TypeError, ValueError):
            log.warning("Ignoring malformed equaliser settings")
            equalizer = Equalizer()
        self.player.set_equalizer(equalizer)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.self_test:
        from pyamp.selftest import run_self_test  # noqa: PLC0415  (only needed here)

        apply_libmpv_preference(args.libmpv, SettingsStore().load())
        return 0 if run_self_test() else 1
    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName(APP_NAME)
    qt_app.setWindowIcon(app_icon())  # window, Dock (when run from source) and taskbar
    qt_app.setApplicationVersion(__version__)
    # One look for every window and dialog, message boxes and plugins' included. Set
    # before any window exists, so nothing has to be restyled.
    qt_app.setStyleSheet(DEFAULT_SKIN.stylesheet())

    app = Application(args)
    qt_app.aboutToQuit.connect(app.shutdown)
    app.start(args.paths)
    return qt_app.exec()
