# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent

block_cipher = None

a = Analysis(
    [str(ROOT / 'peer' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'peer' / 'bundled' / 'rpc-server'), 'rpc-server'),
        (str(ROOT / 'peer' / 'bundled' / 'nssm.exe'), '.'),
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'zeroconf',
        'zeroconf._utils',
        'zeroconf._utils.ipaddress',
        'zeroconf._utils.net',
        'zeroconf._handlers',
        'zeroconf._handlers.answers',
        'pydantic',
        'pydantic_core',
        'psutil',
        'requests',
        'winreg',
        'shared',
        'shared.protocol',
        'shared.versioning',
        'shared.logging_setup',
        'shared.sysreport',
        'shared.mdns',
        'peer',
        'peer.config',
        'peer.agent',
        'peer.gui',
        'peer.installer',
        'peer.service',
        'peer.updater',
        'peer.host_discovery',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['flask', 'jinja2', 'matplotlib', 'numpy', 'scipy', 'PyQt5', 'PySide6', 'PySide2', 'PyQt6'],
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
    name='llamagrid-peer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'build' / 'icon_peer.ico') if os.path.isfile(str(ROOT / 'build' / 'icon_peer.ico')) else None,
    version=str(ROOT / 'build' / 'version_info.txt') if os.path.isfile(str(ROOT / 'build' / 'version_info.txt')) else None,
)
