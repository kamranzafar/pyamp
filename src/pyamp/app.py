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
from pyamp.audio.backend import AudioBackend
from pyamp.audio.registry import UnknownBackendError, available_backends, create_backend
from pyamp.config import Settings, SettingsStore
from pyamp.core.events import EventBus
from pyamp.core.models import RepeatMode
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.dsp.spectrum import SpectrumService
from pyamp.library.loader import TrackLoader
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.plugins import PluginContext, PluginManager
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob
from pyamp.ui.main_window import MainWindow, UiServices
from pyamp.ui.skin import DEFAULT_SKIN

log = logging.getLogger(__name__)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pyamp", description=f"{APP_NAME} music player")
    parser.add_argument("paths", nargs="*", type=Path, help="files, folders or playlists to play")
    parser.add_argument("--backend", choices=available_backends(), help="audio backend")
    parser.add_argument("--no-plugins", action="store_true", help="don't load plugins")
    parser.add_argument("--log-level", default="WARNING", help="e.g. DEBUG, INFO, WARNING")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


class Application:
    """Owns the long-lived services and the session lifecycle."""

    def __init__(self, args: argparse.Namespace, store: SettingsStore | None = None) -> None:
        self._store = store or SettingsStore()
        self.settings = self._store.load()

        self.bus = EventBus()
        self.playlist = Playlist(self.bus)
        self.player = Player(self._create_backend(args.backend), self.playlist, self.bus)
        self.spectrum = SpectrumService(self.bus)
        self.playlist_formats = PlaylistFormatRegistry.default()
        self.loader = BackgroundTrackLoader(
            TrackLoader(MutagenMetadataReader(), self.playlist_formats)
        )
        self.plugins = PluginManager(PluginContext(self.bus, self.player, self.playlist))
        self._restore_job: LoadJob | None = None

        self.window = MainWindow(
            UiServices(
                bus=self.bus,
                player=self.player,
                playlist=self.playlist,
                loader=self.loader,
                playlist_formats=self.playlist_formats,
                settings=self.settings,
                spectrum_bands=self.spectrum.band_count,
            ),
            DEFAULT_SKIN,
        )
        # Restore preferences *after* the UI exists, so widgets receive the events.
        self._apply_settings(self.settings)
        if not args.no_plugins:
            self.plugins.load_entry_points()

    def start(self, paths: Sequence[Path]) -> None:
        self.window.show()
        if paths:
            self.window.add_paths(paths, autoplay=True)
        elif self._store.session_playlist.exists():
            self._restore_job = self.window.add_paths(
                [self._store.session_playlist], autoplay=False
            )

    def shutdown(self) -> None:
        # Quitting while the session is still being restored would save a truncated
        # playlist over the complete one, so in that case keep the file as it is.
        restore_incomplete = self._restore_job is not None and not self._restore_job.done
        self.loader.shutdown()
        self.settings.volume = self.player.volume
        self.settings.shuffle = self.player.shuffle
        self.settings.repeat = self.player.repeat.value
        try:
            self._store.save(self.settings)
            if restore_incomplete:
                log.info("Session restore still in progress; keeping saved playlist")
            else:
                self.playlist_formats.write(self._store.session_playlist, list(self.playlist))
        except OSError:
            log.exception("Could not save session")
        self.plugins.shutdown()
        self.player.shutdown()

    def _create_backend(self, requested: str | None) -> AudioBackend:
        name = requested or self.settings.backend
        try:
            return create_backend(name)
        except UnknownBackendError:
            log.warning(
                "Unknown backend %r in settings; falling back to %r", name, Settings.backend
            )
            return create_backend(Settings.backend)

    def _apply_settings(self, settings: Settings) -> None:
        self.player.set_volume(settings.volume)
        self.player.set_shuffle(settings.shuffle)
        try:
            self.player.set_repeat(RepeatMode(settings.repeat))
        except ValueError:
            self.player.set_repeat(RepeatMode.OFF)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName(APP_NAME)
    qt_app.setApplicationVersion(__version__)

    app = Application(args)
    qt_app.aboutToQuit.connect(app.shutdown)
    app.start(args.paths)
    return qt_app.exec()
