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
