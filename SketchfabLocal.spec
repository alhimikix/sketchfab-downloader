# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)
version = (root/'app_version.py').read_text().split("'")[1]
runtime = root / 'browser-runtime'
browser_dirs = [p for p in runtime.iterdir() if p.is_dir() and not p.name.startswith('.')]
if not any(p.name.startswith('chromium-') for p in browser_dirs):
    raise RuntimeError('Run Build-EXE.ps1 to acquire the matching browser runtime first.')
datas = [(str(root/'capture.js'),'.'), (str(root/'app.ico'),'.')]
datas += [(str(root/'animation_capture.js'),'.'),(str(root/'blend_import.py'),'.')]
datas += [(str(root/'animation_loader.js'),'.')]
datas += [(str(root/'fab_capture.js'),'.')]
datas += [(str(root/'fab_rig_capture.js'),'.')]
datas += [(str(root/'sketchfab_capture_loader.js'),'.')]
datas += [(str(root/'locales'), 'locales')]
datas += [(str(root/'LICENSE'), '.')]
for distribution in ('playwright','numpy','Pillow','certifi','pyinstaller'):
    datas += copy_metadata(distribution,recursive=True)
if sys.platform=='win32':
    datas += [(str(root/'build'/'update-helper'/'SketchfabUpdateHelper.exe'),'update-helper')]
# Keep Chromium's native directory intact on Unix. Reclassifying its ELF/Mach-O
# files pulls in partial system libraries or breaks the signed .app tree.
# The build script copies it after PyInstaller has processed Python.
if sys.platform=='win32':
    datas += [(str(p), 'browser-runtime/'+p.name) for p in browser_dirs]

a = Analysis(
    [str(root/'desktop_main.py')],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
onefile=sys.platform=='win32'
exe = EXE(
    pyz, a.scripts, a.binaries if onefile else [], a.datas if onefile else [], [],
    name='SketchfabLocal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    exclude_binaries=not onefile,
    icon=str(root/('app.icns' if sys.platform=='darwin' else 'app.ico')) if sys.platform!='linux' else None,
    version=str(root/'version_info.txt') if onefile else None,
)
if not onefile:
    collection=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SketchfabLocal')
    if sys.platform=='darwin':
        app=BUNDLE(collection,name='SketchfabLocal.app',icon=str(root/'app.icns'),
                   bundle_identifier='com.alhimikix.sketchfablocal',
                   info_plist={'CFBundleShortVersionString':version,'CFBundleVersion':version,
                               'NSHighResolutionCapable':True})
