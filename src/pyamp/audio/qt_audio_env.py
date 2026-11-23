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
"""Work around a QtMultimedia crash on Linux systems with an incomplete PipeWire.

If the PipeWire client library is installed but its ``client.conf`` is not (e.g. a
minimal install that pulled in libpipewire as a dependency of something else),
``pw_context_new`` fails and Qt 6.12's PipeWire audio backend segfaults in
``QAudioOutput()`` instead of falling back. Qt's PulseAudio backend works in that
state, so PyAmp selects it, unless the user already chose one via
``QT_AUDIO_BACKEND``.
"""

from __future__ import annotations

import ctypes.util
import logging
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path

log = logging.getLogger(__name__)

ENV_VAR = "QT_AUDIO_BACKEND"
FALLBACK_BACKEND = "pulseaudio"


def pipewire_config_dirs(env: Mapping[str, str]) -> list[Path]:
    """Where libpipewire looks for ``client.conf`` (see pipewire's conf.c)."""
    dirs = []
    if env.get("PIPEWIRE_CONFIG_DIR"):
        dirs.append(Path(env["PIPEWIRE_CONFIG_DIR"]))
    config_home = env.get("XDG_CONFIG_HOME") or (
        str(Path(env["HOME"]) / ".config") if env.get("HOME") else None
    )
    if config_home:
        dirs.append(Path(config_home) / "pipewire")
    dirs += [Path("/etc/pipewire"), Path("/usr/share/pipewire"), Path("/usr/local/share/pipewire")]
    return dirs


def pipewire_is_incomplete(
    env: Mapping[str, str],
    find_library: Callable[[str], str | None] = ctypes.util.find_library,
    exists: Callable[[Path], bool] = Path.is_file,
) -> bool:
    """libpipewire is installed but none of its config directories has client.conf."""
    if find_library("pipewire-0.3") is None:
        return False  # no PipeWire at all: Qt skips its PipeWire backend
    return not any(exists(d / "client.conf") for d in pipewire_config_dirs(env))


def avoid_incomplete_pipewire(
    env: MutableMapping[str, str] = os.environ,
    platform: str = sys.platform,
    incomplete: Callable[[Mapping[str, str]], bool] = pipewire_is_incomplete,
) -> bool:
    """Select Qt's PulseAudio backend if PipeWire would crash Qt. Returns whether it did."""
    if not platform.startswith("linux") or env.get(ENV_VAR) or not incomplete(env):
        return False
    log.warning(
        "PipeWire's client library is installed without its configuration (client.conf); "
        "using Qt's %s audio backend to avoid a crash. Installing PipeWire properly "
        "(e.g. the pipewire-bin package) or setting %s overrides this.",
        FALLBACK_BACKEND,
        ENV_VAR,
    )
    env[ENV_VAR] = FALLBACK_BACKEND
    return True
