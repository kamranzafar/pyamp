# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
# -*- mode: python -*-
"""PyInstaller spec for PyAmp.app. Run via packaging/macos/build.sh, which provides:

PYAMP_BUNDLE_LIBMPV   folder with libmpv.dylib and the libraries shipped with it
PYAMP_NOTICES         THIRD_PARTY_NOTICES.md to put in Contents/Resources
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

import pyamp

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821  (SPECPATH is set by PyInstaller)
LIBMPV = Path(os.environ["PYAMP_BUNDLE_LIBMPV"])
NOTICES = Path(os.environ["PYAMP_NOTICES"])

# libmpv and its own ffmpeg/libass go to Contents/Frameworks/libmpv/, apart from Qt's
# libraries; the loader finds them there (see pyamp/audio/libmpv_loader.py).
libmpv_binaries = [(str(lib), "libmpv") for lib in sorted(LIBMPV.glob("*.dylib"))]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=libmpv_binaries,
    datas=[
        *copy_metadata("pyamp"),  # entry points: plugins are discovered through them
        *collect_data_files("pyamp"),  # icons (pyamp/resources)
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "ADDITIONAL-TERMS.md"), "."),
        (str(NOTICES), "."),
    ],
    # Imported lazily (registry, plugins, platform adapters), so list them explicitly.
    hiddenimports=[
        *collect_submodules("pyamp"),
        "PySide6.QtMultimedia",
        "MediaPlayer",
        "mpv",
    ],
    excludes=["jeepney", "tkinter", "pytest"],  # jeepney is Linux-only (MPRIS)
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PyAmp",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PyAmp")  # noqa: F821

app = BUNDLE(  # noqa: F821
    coll,
    name="PyAmp.app",
    icon=str(ROOT / "packaging" / "macos" / "PyAmp.icns"),  # from packaging/make_icons.py
    bundle_identifier="org.pyamp.PyAmp",
    version=pyamp.__version__,
    info_plist={
        "CFBundleName": "PyAmp",
        "CFBundleDisplayName": "PyAmp",
        "CFBundleShortVersionString": pyamp.__version__,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Copyright (C) 2026 Kamran Zafar. GPL-3.0-or-later.",
        "LSApplicationCategoryType": "public.app-category.music",
    },
)
