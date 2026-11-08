"""Runtime paths and desktop integration for supported operating systems."""
import os
from pathlib import Path
import subprocess
import sys


def resource_root():
    return Path(__file__).resolve().parent


def executable_directory():
    path=Path(sys.executable).resolve().parent
    if sys.platform=='darwin' and path.name=='MacOS' and path.parent.name=='Contents':
        return path.parent.parent.parent
    return path


def settings_directory():
    if sys.platform=='win32':
        if getattr(sys,'frozen',False):return executable_directory()
        return resource_root()
    if sys.platform=='darwin':return Path.home()/'Library'/'Application Support'/'SketchfabLocal'
    return Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'sketchfab-local'


def default_download_directory():
    if sys.platform=='win32':
        return (executable_directory() if getattr(sys,'frozen',False) else resource_root())/'downloads'
    return Path.home()/'Downloads'/'SketchfabLocal'


def open_path(path):
    path=str(Path(path).resolve())
    if sys.platform=='win32':os.startfile(path)
    else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',path],
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          env=external_process_environment())


def open_url(url):
    if not url.startswith('https://github.com/'):
        raise ValueError('Only GitHub HTTPS links can be opened')
    if sys.platform=='win32':os.startfile(url)
    else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',url],
                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          env=external_process_environment())


def external_process_environment():
    environment=os.environ.copy()
    if getattr(sys,'frozen',False):
        for variable in ('LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'):
            original=environment.pop(variable+'_ORIG',None)
            if original is None:environment.pop(variable,None)
            else:environment[variable]=original
    return environment


def configure_bundled_runtime():
    if not getattr(sys,'frozen',False):return
    root=resource_root()
    os.environ['PLAYWRIGHT_NODEJS_PATH']=str(root/'playwright'/'driver'/('node.exe' if sys.platform=='win32' else 'node'))
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(root/'browser-runtime')
    os.environ['PLAYWRIGHT_SKIP_BROWSER_GC']='1'
