# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — ONEFILE (portable executable).

Build: pyinstaller WinSetupTool.spec --noconfirm
Output: dist/WinSetupTool.exe (single file, ~50-80 MB)

Tradeoffs:
- Single file, easy to share — copy and run anywhere
- Slower startup (~3-5s) because PyInstaller extracts to a temp dir
  on each run
- Actual size with Python 3.13 + PySide6: ~130 MB
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect bundled resources (translations, images, data JSONs)
datas = []
datas += [('app/resources', 'app/resources')]

# Collect any data files PySide6 ships (translation .qm files, plugins)
datas += collect_data_files('PySide6', includes=['*.qm', 'plugins/**/*'])

# Hidden imports — modules PyInstaller's static analysis may miss
hiddenimports = []
hiddenimports += collect_submodules('app')
hiddenimports += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'shiboken6',
    'pywinstyles',
    'psutil',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['installer/runtime_hook_pyside6.py'],
    excludes=[
        # Trim PyInstaller bloat — these modules aren't used
        'PySide6.QtNetwork',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'tkinter',
    ],
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
    name='WinSetupTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX often triggers AV false positives — disabled
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # No console window — pure GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x64',
    codesign_identity=None,
    entitlements_file=None,
    icon='Furina_icon.ico',
    manifest='installer/manifest.xml',
    version='installer/version_info.txt',
)
