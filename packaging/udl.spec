# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onefile for Tauri externalBin sidecar.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
# SPECPATH is the directory that contains this spec file (packaging/)
root = Path(SPECPATH).resolve().parent / "core"

# OS keyring backends differ by platform; collect all so macOS/Windows
# release sidecars do not miss WinVault / Keychain modules.
_keyring_mods = collect_submodules("keyring")
_jaraco_mods = collect_submodules("jaraco")
_qrcode_mods = collect_submodules("qrcode")
# Pillow plugins used when rendering QR PNG for bilibili login
_pil_mods = collect_submodules("PIL")
_udl_mods = collect_submodules("udl")

a = Analysis(
    [str(root / "udl" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "bs4",
        "requests",
        "soupsieve",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
        "socks",
        "secretstorage",
        "jeepney",
        # explicit keyring platform entry points (also in collect_submodules)
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
        "keyring.backends.kwallet",
        "keyring.backends.chainer",
        "keyring.backends.fail",
        "keyring.backends.null",
        "qrcode",
        "PIL",
        "PIL.Image",
        "PIL.PngImagePlugin",
    ]
    + _udl_mods
    + _keyring_mods
    + _jaraco_mods
    + _qrcode_mods
    + _pil_mods,
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
