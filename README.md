# PyAmp

A lightweight, Winamp-inspired music player for local files, written in Python.

## Installing on macOS

Download `PyAmp-<version>-macos-<arch>.dmg` from the
[latest *Package (macOS)* workflow run](../../actions/workflows/package-macos.yml)
(`x86_64` for Intel Macs, `arm64` for Apple Silicon) and drag **PyAmp** to
Applications. libmpv is bundled, so the `mpv` backend, the equaliser and gapless
playback work without installing anything else.

The app isn't notarised yet, so the first time macOS will refuse to open it:
right-click PyAmp → **Open** → **Open**. If something doesn't work, run
`/Applications/PyAmp.app/Contents/MacOS/PyAmp --self-test` and include the output
in a bug report.

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

### Audio backends

| Backend | Default | Gapless | Equaliser | Visualiser | Needs |
|---|---|---|---|---|---|
| `qt` (QtMultimedia) | yes | no | no | yes | nothing extra |
| `mpv` (libmpv) | | **yes** | **yes** | flat | `brew install mpv` or `apt install libmpv2` |

```bash
uv run pyamp --backend mpv   # remembered for next time; --backend qt switches back
```

mpv plays more formats and moves between tracks without a gap. It doesn't expose
the decoded audio, so the spectrum visualiser stays flat with it. If libmpv isn't
installed, PyAmp warns and uses `qt`.

#### Using a libmpv that isn't installed system-wide

If your package manager can't provide libmpv (e.g. Homebrew on an Intel Mac, which
builds it from source), put a prebuilt libmpv **folder** where PyAmp looks for one:

| Where | Path |
|---|---|
| Environment variable | `PYAMP_LIBMPV=/path/to/folder` (or the library file itself) |
| User folder (macOS) | `~/Library/Application Support/pyamp/libmpv/` |
| User folder (Linux) | `~/.local/share/pyamp/libmpv/` |
| User folder (Windows) | `%LOCALAPPDATA%\pyamp\pyamp\libmpv\` |

The folder holds libmpv plus the libraries shipped with it (ffmpeg, ...); PyAmp loads
those first, so libmpv uses them rather than different system versions. Packaged
releases bundle libmpv the same way.

**Which libmpv wins** when there's both a system one and PyAmp's own (bundled or in
the user folder) is set by `--libmpv` (remembered, like `--backend`):

| `--libmpv` | Order |
|---|---|
| `auto` (default) | **Linux:** system first. **macOS/Windows:** PyAmp's own first (the tested build) |
| `system` | the system libmpv, then PyAmp's own |
| `bundled` | PyAmp's own, then the system libmpv |

A copy that is missing or fails to load is skipped in favour of the next one;
`PYAMP_LIBMPV` always wins. `uv run pyamp --version` shows which libmpv is used and
the preference. If a libmpv's ffmpeg lacks the filters the equaliser needs, PyAmp
says so (and suggests `--libmpv bundled`) instead of failing silently. On macOS, [media-kit's builds](https://github.com/media-kit/libmpv-darwin-build/releases)
work: take `libmpv-libs_…_macos-<arch>-audio-encodersgpl` (the other flavours lack the
filters the equaliser needs). They expect libass from the system (`brew install libass`).

### ReplayGain

**Playback → ReplayGain** evens out loudness using the ReplayGain tags written by
taggers such as foobar2000, beets, loudgain or Picard:

- **Track**: every track at the same loudness.
- **Album**: albums at the same loudness, keeping the dynamics between their tracks
  (falls back to the track gain for untagged albums).
- **Off** (default).

Tags are read from MP3/WAV/AIFF (ID3 `TXXX` or `RVA2`), FLAC/Ogg (Vorbis comments),
Opus (`R128_*`), MP4 and WMA. Gain is capped so the track's peak never clips. With
the `qt` backend ReplayGain can only make tracks quieter, never louder (QtMultimedia's
volume tops out at 100%). The `mpv` backend applies it itself, exactly at gapless
track boundaries.

### Equaliser

**Playback → Equaliser…** (`G`) opens a 10-band graphic equaliser (60 Hz – 16 kHz,
±12 dB) with a preamp and presets. It needs the `mpv` backend: QtMultimedia gives
no access to the audio stream, so with `qt` the window is shown but disabled.
Changes apply when you release or pause a slider (mpv rebuilds its filter chain
for each change, which can cause a tiny audible blip), and the settings are
remembered. Boosting bands can clip loud tracks; lower the preamp if you hear
distortion.

### Media keys and system controls

PyAmp works with your keyboard's media keys, headphone buttons and the system's
"Now Playing" controls, even when its window is in the background:

- **macOS**: Control Center and lock screen widget, media keys, AirPods/Bluetooth buttons.
- **Linux**: MPRIS, so desktop media widgets, media keys and `playerctl -p pyamp` work.

This is the `media-session` plugin; `--no-plugins` turns it off.

## Development

```bash
uv run pytest          # tests (no audio device or display needed)
uv run pytest --cov    # tests with a coverage report
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # strict type checking
uv run pre-commit install  # run ruff and mypy on every commit
packaging/macos/build.sh   # build dist/PyAmp.app and a .dmg (macOS)
```

`build.sh` downloads the libmpv pinned in `packaging/macos/libmpv.lock` (checked
against its SHA-256), bundles it with PyInstaller and makes a disk image. Licences of
bundled components are listed in `packaging/THIRD_PARTY_NOTICES.md`, which ships
inside the app.

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
│   ├── equalizer.py    10-band equaliser model and presets
│   └── player.py       Player facade: transport, volume, modes
├── audio/              Sound output
│   ├── backend.py      AudioBackend ABC + BackendListener protocol
│   ├── registry.py     Backend factory with lazy imports
│   ├── qt_backend.py   QtMultimedia adapter (+ PCM tap for the visualiser)
│   └── mpv_backend.py  libmpv adapter with gapless playback
├── dsp/spectrum.py     FFT analyser (pure) + SpectrumService (bus adapter)
├── library/            Getting tracks in
│   ├── metadata.py     Tag reading via mutagen
│   ├── playlist_formats.py  M3U / PLS read & write
│   └── loader.py       Files/folders/playlists → Tracks
├── media_session/      OS media controls (used by the media-session plugin)
│   ├── session.py      Player ⇄ NowPlaying snapshots and OS commands (pure)
│   ├── macos.py        MediaPlayer framework: Now Playing + remote commands
│   └── mpris.py        MPRIS over D-Bus (jeepney), served on the Qt main thread
├── plugins/            Entry-point based plugin system, now-playing log, media session
└── ui/                 PySide6 front-end
    ├── skin.py         All colours in one place (seam for .wsz skins)
    ├── actions.py      Playback commands as QActions
    ├── equalizer_window.py  The equaliser window
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
| **Adapter** | `audio/qt_backend.py`, `audio/mpv_backend.py`, `ui/playlist_model.py`, `media_session/macos.py`, `media_session/mpris.py` | Wrap third-party APIs (QtMultimedia, Qt model/view) behind our own interfaces. |
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

libmpv reports events on its own thread. `audio/mpv_backend.py` only records
state there and emits signals on a main-thread `QObject`, so the player never
sees a call from mpv's thread.

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
- Feed the visualiser when using the `mpv` backend
- Frameless, pixel-skinned window + classic `.wsz` skin loader
- Windows media controls (SMTC) and album artwork in system media widgets
- Internet radio (playlist formats currently skip URLs)

## License

Copyright (C) 2026 Kamran Zafar

PyAmp is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version. See
[LICENSE](LICENSE) for the full text.
