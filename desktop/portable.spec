# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable build.

Produces a one-folder distribution: an executable plus its runtime beside it.
One-folder rather than one-file on purpose - a one-file build unpacks ~200 MB
to a temporary directory on every start, which makes launching slow and
confuses antivirus software.

The bundle carries:
  * the FastAPI backend and its dependencies,
  * the built web UI in ``web/`` (the launcher points PEO_STATIC_DIR at it),
  * the material and printer databases, which are JSON data files read at
    runtime and therefore invisible to PyInstaller's import analysis.

Build with:  pyinstaller desktop/portable.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).parent

datas = [
    # The built web UI. Fails loudly at build time if the frontend was not
    # built, which is better than shipping a bundle that serves nothing.
    (str(ROOT / "frontend" / "dist"), "web"),
    # JSON databases loaded by path at runtime.
    (str(ROOT / "backend" / "app" / "materials" / "data"), "app/materials/data"),
    (str(ROOT / "backend" / "app" / "printing" / "data"), "app/printing/data"),
]
binaries = []
hiddenimports = [
    # uvicorn resolves its implementation classes by string name.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "app.main",
]

# trimesh imports its exchange loaders and optional backends lazily, and ships
# data files; scipy, shapely and rtree carry compiled extensions that the
# import graph alone does not reveal.
for package in ("trimesh", "scipy", "shapely", "rtree", "manifold3d", "fast_simplification"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

datas += collect_data_files("networkx")

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Dropping the plotting and notebook stacks that scipy/trimesh only touch
    # in optional code paths keeps the download roughly a third smaller.
    excludes=[
        "matplotlib",
        "tkinter",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
        "PyQt5",
        "PySide2",
        "PySide6",
        "pyglet",
        "PIL.ImageQt",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="print-engineering-optimizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="print-engineering-optimizer",
)
