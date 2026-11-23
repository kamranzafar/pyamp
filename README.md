# PyAmp

A lightweight, Winamp-inspired music player for local files, written in Python.

## Running

```bash
uv sync
uv run pyamp                      # restores your last playlist
uv run pyamp ~/Music/album/       # files, folders or .m3u/.pls playlists
uv run pyamp --log-level INFO     # see plugin activity, e.g. "Now playing: ..."
```

| Key            | Action                     |
|----------------|----------------------------|
| `Z X C V B`    | Prev / Play / Pause / Stop / Next (classic Winamp) |
| `←` `→`        | Seek -/+ 5 s               |
| `S` / `R`      | Shuffle / cycle repeat (off → all → one) |
| `Enter`, double-click | Play selected track |
| `Delete`       | Remove selected tracks     |
| Click the time | Toggle elapsed / remaining |

Drag and drop files or folders onto the window to add them.

## Development

```bash
uv run pytest          # tests (no audio device or display needed)
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # strict type checking
```

## Architecture

```
src/pyamp/
├── app.py              Composition root: builds the object graph, owns session lifecycle
├── config.py           Settings dataclass + JSON persistence (atomic writes)
├── core/               Domain logic. NO Qt imports allowed here.
│   ├── models.py       Track, PlaybackState, RepeatMode (immutable value objects)
│   ├── events.py       EventBus + event types
│   ├── play_order.py   Sequential / Shuffle strategies
│   ├── playlist.py     Playlist model with a "current" cursor
│   └── player.py       Player facade: transport, volume, modes
├── audio/              Sound output
│   ├── backend.py      AudioBackend ABC + BackendListener protocol
│   ├── registry.py     Backend factory with lazy imports
│   └── qt_backend.py   QtMultimedia adapter (+ PCM tap for the visualiser)
├── dsp/spectrum.py     FFT analyser (pure) + SpectrumService (bus adapter)
├── library/            Getting tracks in
│   ├── metadata.py     Tag reading via mutagen
│   ├── playlist_formats.py  M3U / PLS read & write
│   └── loader.py       Files/folders/playlists → Tracks
├── plugins/            Entry-point based plugin system + example plugin
└── ui/                 PySide6 front-end
    ├── skin.py         All colours in one place (seam for .wsz skins)
    ├── actions.py      Playback commands as QActions
    ├── background_loader.py  Non-blocking track loading (worker thread)
    ├── playlist_model.py  Playlist → QAbstractTableModel adapter
    ├── main_window.py
    └── widgets/        LCD panel, marquee, spectrum, sliders, playlist view
```

### Dependency rule

```
ui ──► core ◄── audio
 │      ▲  ▲
 └──► library   dsp, plugins
```

`core` depends on nothing in the app except the `AudioBackend` interface.
That is why the whole player can be tested with a `FakeBackend` (see
`tests/conftest.py`), and why a terminal UI could reuse it unchanged.

### Patterns used

| Pattern | Where | Why |
|---|---|---|
| **Observer** | `core/events.py` | Player/playlist publish events; UI, DSP and plugins subscribe. No back-references. |
| **Facade** | `core/player.py` | One simple API over playlist + backend for UI, shortcuts and plugins. |
| **Adapter** | `audio/qt_backend.py`, `ui/playlist_model.py` | Wrap third-party APIs (QtMultimedia, Qt model/view) behind our own interfaces. |
| **Strategy** | `core/play_order.py`, `library/playlist_formats.py` | Swap play order or file format without touching callers. |
| **Factory / Registry** | `audio/registry.py`, `PlaylistFormatRegistry` | Choose implementations by name/extension; lazy imports keep optional deps optional. |
| **Command** | `ui/actions.py` | One `QAction` drives the button, menu item and shortcut. |
| **Null Object** | `audio/backend.py` (`_NullListener`) | Backends never need `if listener is not None`. |
| **Dependency Injection** | `app.py` | Everything receives collaborators via constructors; only `app.py` knows concrete classes. |

### Threading model

All events are published on the Qt main thread (Qt delivers multimedia signals
there), so the event bus is synchronous and lock-free. Work that runs off the
main thread must send its results back with a queued Qt signal before touching
the playlist or the bus.

`ui/background_loader.py` is the reference example. Folders and playlists are
scanned and their tags read on a single worker thread (FIFO, so add order is
preserved). Tracks stream back in batches, so a huge folder fills the list
progressively and playback starts with the first batch. Jobs can be cancelled
(e.g. *Clear* while loading), and batches already in flight are discarded.
The playlist model applies appends as incremental `beginInsertRows`, so the
main-thread cost per batch stays constant regardless of playlist size.

## Writing a plugin

```python
from pyamp.core.events import TrackChanged
from pyamp.plugins import PluginContext


class MyScrobbler:
    name = "my-scrobbler"

    def activate(self, ctx: PluginContext) -> None:
        self._unsub = ctx.bus.subscribe(TrackChanged, self.on_track)

    def deactivate(self) -> None:
        self._unsub()

    def on_track(self, event: TrackChanged) -> None: ...
```

Register it in your package's `pyproject.toml`:

```toml
[project.entry-points."pyamp.plugins"]
my-scrobbler = "my_package:MyScrobbler"
```

See `src/pyamp/plugins/now_playing.py` for a working example.

## Roadmap ideas

- Process-pool tag reading for very large libraries (mutagen is pure Python, so the
  worker thread still competes for the GIL)
- `mpv` backend for gapless playback and wider format support
- Equaliser, ReplayGain
- Frameless, pixel-skinned window + classic `.wsz` skin loader
- Media keys / MPRIS / macOS Now Playing integration
- Internet radio (playlist formats currently skip URLs)

## License

Copyright (C) 2026 Kamran Zafar

PyAmp is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version. See
[LICENSE](LICENSE) for the full text.
