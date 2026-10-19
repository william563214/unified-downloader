# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onefile for Tauri externalBin sidecar.

from pathlib import Path

block_cipher = None
# SPECPATH is the directory that contains this spec file (packaging/)
root = Path(SPECPATH).resolve().parent / "core"

a = Analysis(
    [str(root / "udl" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "udl",
        "udl.cli",
        "udl.router",
        "udl.events",
        "udl.download.http",
        "udl.postprocess",
        "udl.postprocess.ffmpeg",
        "udl.extractors.base",
        "udl.extractors.gofile",
        "udl.extractors.bunkr",
        "udl.extractors.iwara",
        "udl.extractors.iwara_xversion",
        "udl.extractors.bilibili",
        "udl.extractors.bilibili.client",
        "udl.extractors.bilibili.extractor",
        "udl.extractors.bilibili.ids",
        "udl.extractors.bilibili.quality",
        "udl.extractors.bilibili.wbi",
        "bs4",
        "requests",
        "soupsieve",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="udl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
