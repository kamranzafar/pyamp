from __future__ import annotations

from pathlib import Path

from pyamp.config import Settings, SettingsStore


def test_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(volume=0.3, shuffle=True))
    loaded = store.load()
    assert loaded.volume == 0.3
    assert loaded.shuffle is True


def test_missing_or_corrupt_file_gives_defaults(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    assert store.load() == Settings()
    store.settings_file.write_text("{not json")
    assert store.load() == Settings()


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.settings_file.write_text('{"volume": 0.5, "from_the_future": 1}')
    assert store.load().volume == 0.5
