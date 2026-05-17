# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent

block_cipher = None

a = Analysis(
    [str(ROOT / 'host' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / 'host' / 'webroot'),  'host/webroot'),
        (str(ROOT / 'host' / 'bundled' / 'llama-server'), 'llama-server'),
    ],
    hiddenimports=[
        'zeroconf',
        'zeroconf._utils',
        'zeroconf._utils.ipaddress',
        'zeroconf._utils.net',
        'zeroconf._handlers',
        'zeroconf._handlers.answers',
        'pydantic',
        'pydantic_core',
        'pydantic.v1',
        'flask',
        'flask.json',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.routing',
        'psutil',
        'requests',
        'shared',
        'shared.protocol',
        'shared.versioning',
        'shared.logging_setup',
        'shared.sysreport',
        'shared.mdns',
        'host',
        'host.config',
        'host.coordinator',
        'host.llama_manager',
        'host.api',
        'host.alerts',
        'host.model_scanner',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL'],
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
    name='llamagrid-host',
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
    icon=str(ROOT / 'build' / 'icon_host.ico') if os.path.isfile(str(ROOT / 'build' / 'icon_host.ico')) else None,
    version=str(ROOT / 'build' / 'version_info.txt') if os.path.isfile(str(ROOT / 'build' / 'version_info.txt')) else None,
)
