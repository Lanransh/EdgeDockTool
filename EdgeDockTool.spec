# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt 6 uses the Windows-provided ICU forwarder. Build environments may expose
# unrelated ICU binaries on PATH; bundling those shadows the system DLL and
# makes PySide6.QtCore fail with ERROR_PROC_NOT_FOUND on launch.
a.binaries = [
    entry
    for entry in a.binaries
    if not Path(entry[0]).name.lower().startswith(("icuuc", "icudt", "icuin"))
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='EdgeDockTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['EdgeDockTool.ico'],
)
