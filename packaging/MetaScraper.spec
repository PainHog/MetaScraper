# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec that builds a single-file, windowed MetaScraper.exe.

Build (on Windows, from the repo root):

    pip install .[build]
    pyinstaller packaging/MetaScraper.spec

The result is dist/MetaScraper.exe — a standalone app that needs no Python
install. FFmpeg is NOT bundled; users install it separately (or point the app
at ffprobe.exe).
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller; the repo root is one level up.
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# python-docx ships a default .docx template as package data; make sure it and
# openpyxl's data come along.
datas = collect_data_files("docx") + collect_data_files("openpyxl")
hiddenimports = collect_submodules("openpyxl")

a = Analysis(
    [os.path.join(ROOT, "packaging", "metascraper_gui_launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MetaScraper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,          # windowed app (no console pops up)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
