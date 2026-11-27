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

"""``pyamp --list-output-devices``: the devices each backend can play through.

Shows the ids PyAmp saves for a chosen device, and whether the backends agree on them
(if they do, a chosen device stays chosen when you change backend).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pyamp.audio.backend import AudioBackend
from pyamp.audio.registry import BackendUnavailableError, create_backend
from pyamp.core.models import OutputDevice

BUILT_IN = ("mpv", "qt")


def device_report(
    names: Iterable[str] = BUILT_IN,
    create: Callable[[str], AudioBackend] = create_backend,
) -> str:
    lines: list[str] = []
    listed: dict[str, list[OutputDevice]] = {}
    for name in names:
        try:
            backend = create(name)
        except BackendUnavailableError as exc:
            lines += [f"{name}: not available ({exc.reason})", ""]
            continue
        try:
            if not backend.supports_output_devices:
                lines += [f"{name}: always plays through the system default", ""]
                continue
            devices = backend.output_devices()
        finally:
            backend.close()
        listed[name] = devices
        lines.append(f"{name}: {len(devices)} device(s)")
        lines += [f"  {d.name}\n    id: {d.id}" for d in devices]
        lines.append("")
    lines.append(_agreement(listed))
    return "\n".join(lines)


def _agreement(listed: dict[str, list[OutputDevice]]) -> str:
    if len(listed) < 2:
        return "Only one backend lists devices here, so there's nothing to compare."
    ids = {name: {d.id for d in devices} for name, devices in listed.items()}
    first, *others = ids.values()
    if all(other == first for other in others):
        return "The backends use the same ids: a chosen device stays chosen across backends."
    lines = ["The backends name some devices differently:"]
    every = set().union(*ids.values())
    for device_id in sorted(every):
        missing = [name for name, found in ids.items() if device_id not in found]
        if missing:
            lines.append(f"  {device_id}  (not listed by {', '.join(missing)})")
    return "\n".join(lines)
