"""Build the current OS/architecture, then package a GitHub Release asset."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app_version import VERSION


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--target',required=True,choices=['windows-x64','linux-x64','macos-x64','macos-arm64'])
    parser.add_argument('--skip-browser-install',action='store_true')
    args=parser.parse_args()
    architecture='arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
    system={'win32':'windows','darwin':'macos','linux':'linux'}[sys.platform]
    if args.target!=system+'-'+architecture:raise SystemExit('The target must match the native runner architecture')
    environment=os.environ.copy();environment['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'browser-runtime')
    if not args.skip_browser_install:
        subprocess.run([sys.executable,'-m','playwright','install','chromium','--no-shell'],cwd=ROOT,env=environment,check=True)
    if sys.platform=='darwin':
        from PIL import Image
        with Image.open(ROOT/'app.ico') as image:
            image.convert('RGBA').resize((1024,1024)).save(ROOT/'app.icns')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--noconsole',
                    '--name','SketchfabUpdateHelper','--distpath',str(ROOT/'build'/'update-helper'),
                    '--workpath',str(ROOT/'build'/'helper-work'),'--specpath',str(ROOT/'build'/'helper-spec'),
                    str(ROOT/'updater_helper.py')],cwd=ROOT,env=environment,check=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm',str(ROOT/'SketchfabLocal.spec')],cwd=ROOT,env=environment,check=True)
    assets=ROOT/'release-assets';assets.mkdir(exist_ok=True)
    stem=f'SketchfabLocal-{VERSION}-{args.target}'
    if sys.platform=='win32':
        asset=assets/(stem+'.exe');shutil.copy2(ROOT/'dist'/'SketchfabLocal.exe',asset)
    elif sys.platform=='darwin':
        app=ROOT/'dist'/'SketchfabLocal.app'
        destination=app/'Contents'/'Resources'/'browser-runtime'
        subprocess.run(['ditto',str(ROOT/'browser-runtime'),str(destination)],check=True)
        (app/'Contents'/'Frameworks'/'browser-runtime').symlink_to('../Resources/browser-runtime',target_is_directory=True)
        helper=app/'Contents'/'Resources'/'update-helper';helper.mkdir()
        shutil.copy2(ROOT/'build'/'update-helper'/'SketchfabUpdateHelper',helper/'SketchfabUpdateHelper')
        (app/'Contents'/'Frameworks'/'update-helper').symlink_to('../Resources/update-helper',target_is_directory=True)
        # Re-seal the outer app after adding the untouched vendor browser.
        subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
        asset=assets/(stem+'.zip')
        subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(app),str(asset)],check=True)
    else:
        shutil.copytree(ROOT/'browser-runtime',ROOT/'dist'/'SketchfabLocal'/'_internal'/'browser-runtime',
                        dirs_exist_ok=True,symlinks=True)
        helper=ROOT/'dist'/'SketchfabLocal'/'_internal'/'update-helper';helper.mkdir()
        shutil.copy2(ROOT/'build'/'update-helper'/'SketchfabUpdateHelper',helper/'SketchfabUpdateHelper')
        asset=assets/(stem+'.tar.gz')
        with tarfile.open(asset,'w:gz') as archive:
            archive.add(ROOT/'dist'/'SketchfabLocal',arcname='SketchfabLocal')
    digest=hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_name(asset.name+'.sha256').write_text(digest+'  '+asset.name+'\n',encoding='utf-8')
    print('RELEASE_ASSET',asset,flush=True)


if __name__=='__main__':main()
