"""User settings and their persistence."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs

from pyamp import APP_NAME

log = logging.getLogger(__name__)


@dataclass
class Settings:
    volume: float = 0.8
    shuffle: bool = False
    repeat: str = "off"
    backend: str = "qt"
    last_directory: str = ""
    show_remaining_time: bool = False


class SettingsStore:
    """Loads and saves :class:`Settings` as JSON in the platform config directory.

    Unknown keys are ignored and missing keys fall back to defaults, so the
    file format can evolve without migrations.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(platformdirs.user_config_dir(APP_NAME.lower()))

    @property
    def settings_file(self) -> Path:
        return self.directory / "settings.json"

    @property
    def session_playlist(self) -> Path:
        """Where the playlist is saved on exit and restored on start."""
        return self.directory / "session.m3u8"

    def load(self) -> Settings:
        try:
            raw: Any = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings()
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable settings file %s: %s", self.settings_file, exc)
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        known = {f.name for f in dataclasses.fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def save(self, settings: Settings) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dataclasses.asdict(settings), indent=2)
        # Write-then-rename so a crash never leaves a half-written settings file.
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.settings_file)
