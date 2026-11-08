"""Standalone, offline update installer. This module has no third-party imports."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

PROTOCOL = 1
STAGE_PREFIX = '.sketchfab-update-'


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding='utf-8')
    temporary.replace(path)


def child_environment():
    env = os.environ.copy()
    for key in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
        original = env.pop(key + '_ORIG', None)
        if original is not None:
            env[key] = original
        elif getattr(sys, 'frozen', False):
            env.pop(key, None)
    for key in ('PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def wait_for_parent(pid, timeout=90):
    if pid <= 0:
        return
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            try:
                if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                    raise TimeoutError('Parent application is still running')
            finally:
                kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(.2)
    raise TimeoutError('Parent application is still running')


def validate_manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    stage = path.parent
    target = Path(data['target']).absolute()
    prepared = Path(data['prepared']).absolute()
    if data.get('protocol') != PROTOCOL or not stage.name.startswith(STAGE_PREFIX):
        raise ValueError('Invalid update manifest')
    if stage.parent != target.parent.resolve() or target.is_symlink():
        raise ValueError('The update must be staged beside the installed application')
    if not prepared.resolve().is_relative_to(stage) or prepared.is_symlink():
        raise ValueError('Invalid prepared application path')
    if not target.exists() or not prepared.exists():
        raise ValueError('The installed or prepared application is missing')
    kind = data['kind']
    if kind not in ('windows', 'macos', 'linux'):
        raise ValueError('Invalid update type')
    if kind == 'windows' and (target.suffix.lower() != '.exe' or not prepared.is_file()):
        raise ValueError('Expected a Windows executable')
    if kind == 'macos' and (target.suffix != '.app' or not (prepared / 'Contents/MacOS/SketchfabLocal').is_file()):
        raise ValueError('Expected a macOS application')
    if kind == 'linux' and not (prepared / 'SketchfabLocal').is_file():
        raise ValueError('Expected a Linux application directory')
    data.update(target=target, prepared=prepared, stage=stage)
    return data


def application_executable(target, kind):
    if kind == 'windows':
        return target
    if kind == 'macos':
        return target / 'Contents/MacOS/SketchfabLocal'
    return target / 'SketchfabLocal'


def preserve_user_files(target, prepared, kind):
    if kind != 'linux':
        return
    for item in target.iterdir():
        if item.name in ('SketchfabLocal', '_internal'):
            continue
        destination = prepared / item.name
        if destination.exists() or destination.is_symlink():
            raise FileExistsError('A user file conflicts with the update: ' + item.name)
        if item.is_symlink():
            destination.symlink_to(os.readlink(item), target_is_directory=item.is_dir())
        elif item.is_dir():
            shutil.copytree(item, destination, symlinks=True)
        else:
            shutil.copy2(item, destination)


def launch_application(target, kind, receipt=None, self_test=None):
    command = [str(application_executable(target, kind))]
    if receipt:
        command.extend(['--update-receipt', str(receipt)])
    if self_test:
        command.extend(['--self-test', str(self_test)])
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return subprocess.Popen(command, cwd=target.parent, env=child_environment(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)


def wait_for_receipt(receipt, version, process, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if json.loads(receipt.read_text(encoding='utf-8')).get('version') == version:
                return
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            raise RuntimeError('New application exited before confirming startup')
        time.sleep(.2)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    raise TimeoutError('New application did not confirm startup')


def replace_with_retry(source, target, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.3)


def install(path, launcher=launch_application, receipt_waiter=wait_for_receipt):
    data = validate_manifest(path)
    target, prepared, stage, kind = (data[k] for k in ('target', 'prepared', 'stage', 'kind'))
    backup = target.with_name(target.name + '.previous-' + uuid.uuid4().hex)
    result = Path(data['result'])
    moved = installed = False
    failure = 'update.install_failed'
    try:
        wait_for_parent(int(data['parent_pid']))
        preserve_user_files(target, prepared, kind)
        replace_with_retry(target, backup)
        moved = True
        replace_with_retry(prepared, target)
        installed = True
        failure = 'update.start_failed'
        receipt = stage / 'started.json'
        process = launcher(target, kind, receipt, stage / 'installed-smoke.json' if data.get('self_test') else None)
        receipt_waiter(receipt, data['version'], process)
        if data.get('self_test') and process.wait(timeout=60) != 0:
            raise RuntimeError('Installed application smoke test failed')
    except Exception:
        try:
            if installed:
                replace_with_retry(target, stage / 'failed-application')
            if moved:
                replace_with_retry(backup, target)
        except Exception:
            write_json(result, {'status': 'error', 'key': 'update.recovery_needed', 'backup': str(backup)})
            return False
        write_json(result, {'status': 'error', 'key': failure, 'stage': str(stage)})
        if moved:
            try:launcher(target, kind)
            except OSError:pass
        return False
    # The new app has acknowledged startup: reporting/cleanup failures must
    # never roll it back while it is already running.
    try:
        write_json(stage / 'completed.json', {'target': str(target), 'backup': str(backup)})
        write_json(result, {'status': 'success', 'version': data['version'], 'stage': str(stage)})
    except OSError:
        pass
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        return 0
    if not args.manifest:
        parser.error('--manifest is required')
    return 0 if install(args.manifest) else 1


if __name__ == '__main__':
    raise SystemExit(main())
