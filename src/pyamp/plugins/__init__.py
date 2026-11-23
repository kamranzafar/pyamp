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

"""Plugin API.

A plugin is any object implementing :class:`Plugin`. It is registered under the
``pyamp.plugins`` entry-point group of an installed package::

    [project.entry-points."pyamp.plugins"]
    my-plugin = "my_package.plugin:MyPlugin"

On activation it receives a :class:`PluginContext` with the event bus, player
and playlist, which is everything needed to build scrobblers, notifications,
remote controls, extra backends, and so on.
"""

from pyamp.plugins.base import Plugin, PluginContext
from pyamp.plugins.manager import PluginManager

__all__ = ["Plugin", "PluginContext", "PluginManager"]
