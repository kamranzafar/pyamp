# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
# -*- mode: python -*-
"""PyInstaller spec for PyAmp on Windows. Run via packaging/windows/build.ps1, which provides:

PYAMP_BUNDLE_LIBMPV   folder with libmpv-2.dll
PYAMP_NOTICES         THIRD_PARTY_NOTICES.md to ship next to the licence

Builds one folder with two executables sharing everything else:
  PyAmp.exe           the app (no console window)
  pyamp-console.exe   the same program as a console app, for --version, --self-test and
                      diagnostics (a windowed exe can't print or report an exit code)
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

import pyamp

sys.path.insert(0, str(Path(SPECPATH).resolve().parent))  # noqa: F821  (packaging/)
from trim_qt import without_unused_qt  # noqa: E402

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821  (SPECPATH is set by PyInstaller)
LIBMPV = Path(os.environ["PYAMP_BUNDLE_LIBMPV"])
NOTICES = Path(os.environ["PYAMP_NOTICES"])
ICON = str(ROOT / "packaging" / "windows" / "PyAmp.ico")  # from packaging/make_icons.py

# libmpv goes to _internal\libmpv\, apart from Qt's DLLs; the loader finds it there.
libmpv_binaries = [(str(dll), "libmpv") for dll in sorted(LIBMPV.glob("*.dll"))]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=libmpv_binaries,
    datas=[
        *copy_metadata("pyamp"),  # entry points: plugins are discovered through them
        *copy_metadata("keyring"),  # keyring finds its keychain backends the same way
        *collect_data_files("pyamp"),  # icons (pyamp/resources)
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "ADDITIONAL-TERMS.md"), "."),
        (str(NOTICES), "."),
    ],
    # Imported lazily (registry, plugins, platform adapters), so list them explicitly.
    hiddenimports=[
        *collect_submodules("pyamp"),
        *collect_submodules("keyring.backends"),
        "PySide6.QtMultimedia",
        "mpv",
    ],
    excludes=["jeepney", "tkinter", "pytest", "MediaPlayer", "objc"],
    noarchive=False,
)
# Leave out the parts of Qt PyAmp never uses (see packaging/trim_qt.py).
a.binaries = without_unused_qt(a.binaries)
a.datas = without_unused_qt(a.datas)

pyz = PYZ(a.pure)  # noqa: F821


def version_info() -> VSVersionInfo:
    numbers = tuple(int(part) for part in pyamp.__version__.split(".")[:3]) + (0,)
    strings = [
        StringStruct("CompanyName", "Kamran Zafar"),
        StringStruct("FileDescription", "PyAmp music player"),
        StringStruct("FileVersion", pyamp.__version__),
        StringStruct("LegalCopyright", "Copyright (C) 2026 Kamran Zafar. GPL-3.0-or-later."),
        StringStruct("ProductName", "PyAmp"),
        StringStruct("ProductVersion", pyamp.__version__),
    ]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", strings)]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


def executable(name: str, *, console: bool):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=ICON,
        version=version_info(),
    )


app = executable("PyAmp", console=False)
console = executable("pyamp-console", console=True)
coll = COLLECT(app, console, a.binaries, a.datas, name="PyAmp")  # noqa: F821
