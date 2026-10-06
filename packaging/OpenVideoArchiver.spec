# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(os.environ["OVA_BUILD_ROOT"]).resolve()
ICON = Path(os.environ["OVA_ICON_PATH"]).resolve()
VERSION_FILE = Path(os.environ["OVA_VERSION_FILE"]).resolve()

datas = []
binaries = []
hiddenimports = []

for package in ("yt_dlp", "curl_cffi", "yt_dlp_ejs"):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hidden
    except Exception:
        pass

try:
    hiddenimports += collect_submodules("yt_dlp_plugins", on_error="ignore")
except Exception:
    pass

for distribution in ("yt-dlp", "curl-cffi", "yt-dlp-ejs"):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "ova_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenVideoArchiver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(VERSION_FILE),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OpenVideoArchiver",
)
