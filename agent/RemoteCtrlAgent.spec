# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all("cv2")
numpy_datas, numpy_binaries, numpy_hiddenimports = collect_all("numpy")

hiddenimports = sorted(set(cv2_hiddenimports + numpy_hiddenimports + [
    "cv2",
    "numpy",
    "numpy.core",
    "numpy.core.multiarray",
    "numpy._core",
    "numpy._core.multiarray",
]))

a = Analysis(
    [r'D:\Project\MMT\agent\remotectrl_agent\__main__.py'],
    pathex=[],
    binaries=cv2_binaries + numpy_binaries,
    datas=cv2_datas + numpy_datas,
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
    a.binaries,
    a.datas,
    [],
    name='RemoteCtrlAgent',
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
)
