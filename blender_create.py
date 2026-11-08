"""Create a self-contained Blender project with the locally installed Blender.

This module runs in the host Python/PyInstaller application. ``blend_import.py``
must be included as a data file beside this module in packaged builds.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from platform_support import external_process_environment
from i18n import t


class BlenderCreationError(RuntimeError):
    """Blender was unavailable or did not produce a verified project."""


class BlenderNotFoundError(BlenderCreationError):
    pass


def _executable(value) -> Path | None:
    if not value:
        return None
    candidate = Path(os.path.expandvars(str(value))).expanduser()
    if candidate.is_dir():
        candidate = candidate / ('blender.exe' if os.name == 'nt' else 'blender')
    if candidate.is_file():
        return candidate.resolve()
    found = shutil.which(str(value))
    return Path(found).resolve() if found else None


def find_blender(blender_path=None) -> Path:
    """Resolve an explicit path, environment override, PATH, or common install."""
    if blender_path:
        candidate = _executable(blender_path)
        if candidate:
            return candidate
        raise BlenderNotFoundError(t('blender.path_missing',path=blender_path))
    for variable in ('BLENDER_PATH', 'BLENDER_EXECUTABLE'):
        value = os.environ.get(variable)
        if value:
            candidate = _executable(value)
            if candidate:
                return candidate
            raise BlenderNotFoundError(t('blender.env_missing',variable=variable,path=value))
    found = shutil.which('blender')
    if found:
        return Path(found).resolve()
    candidates = []
    if os.name == 'nt':
        roots = {Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')),
                 Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)'))}
        if os.environ.get('LOCALAPPDATA'):
            roots.add(Path(os.environ['LOCALAPPDATA']) / 'Programs')
        for root in roots:
            for pattern in ('Blender Foundation/Blender*/blender.exe', 'Blender*/blender.exe',
                            'Steam/steamapps/common/Blender/blender.exe'):
                candidates.extend(root.glob(pattern))
        # MSI installations in custom folders expose their actual install path.
        try:
            import winreg
            key_name = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for flag in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ | flag) as root:
                            for i in range(winreg.QueryInfoKey(root)[0]):
                                try:
                                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                        name = str(winreg.QueryValueEx(entry, 'DisplayName')[0])
                                        if not name.lower().startswith('blender'):
                                            continue
                                        location = str(winreg.QueryValueEx(entry, 'InstallLocation')[0])
                                        candidates.append(Path(location) / 'blender.exe')
                                except OSError:
                                    continue
                    except OSError:
                        continue
        except ImportError:
            pass
    else:
        candidates.extend([Path('/Applications/Blender.app/Contents/MacOS/Blender'),
                           Path('/usr/bin/blender'), Path('/usr/local/bin/blender')])
    available = {p.resolve() for p in candidates if p.is_file()}
    if available:
        # Prefer the newest numbered install; ordering is stable on equal versions.
        return max(available, key=lambda p: (tuple(map(int, re.findall(r'\d+', p.parent.name))), str(p)))
    raise BlenderNotFoundError(t('blender.not_found'))


def _publish_new_file(source: Path, destination: Path):
    """Publish without replacing an existing user's file, including a race."""
    try:
        os.link(source, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        pass  # Filesystems without hard links still get exclusive file creation.
    created = False
    try:
        with destination.open('xb') as target, source.open('rb') as origin:
            created = True
            shutil.copyfileobj(origin, target, length=1024 * 1024)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def create_blend(output_dir: Path, log=print, blender_path=None) -> dict:
    """Import output_dir/model.glb into a new, verified model.blend.

    Returns the report saved to blender-report.json, including path, byte size,
    action/armature/NLA counts, animation ranges, packed images and warnings.
    Existing model.blend or blender-report.json files are never overwritten.
    Blender's user configuration, scripts and extensions are isolated in a
    temporary directory; no add-on installation or preference save is needed.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    source = output_dir / 'model.glb'
    destination = output_dir / 'model.blend'
    report_path = output_dir / 'blender-report.json'
    if not source.is_file():
        raise BlenderCreationError(t('blender.source_missing',path=source))
    for path in (destination, report_path):
        if path.exists():
            raise FileExistsError(t('blender.exists',path=path))
    executable = find_blender(blender_path)
    script = Path(__file__).resolve().with_name('blend_import.py')
    if not script.is_file():
        raise BlenderCreationError(t('blender.script_missing',name=script.name))
    log(t('blender.creating',name=executable.parent.name))
    # The staging directory is created here, and only it is removed on exit.
    # Keeping it on the output volume permits atomic, non-overwriting hardlinks.
    with tempfile.TemporaryDirectory(prefix='.blend-create-', dir=str(output_dir)) as staging:
        stage = Path(staging)
        stage_blend = stage / 'model.blend'
        stage_report = stage / 'blender-report.json'
        environment = external_process_environment()
        for variable, folder in [('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                                 ('BLENDER_USER_DATAFILES', 'datafiles'), ('BLENDER_USER_EXTENSIONS', 'extensions')]:
            location = stage / folder
            location.mkdir()
            environment[variable] = str(location)
        environment['PYTHONNOUSERSITE'] = '1'
        environment['PYTHONUTF8'] = '1'
        environment.pop('PYTHONPATH', None)
        environment.pop('PYTHONHOME', None)
        command = [str(executable), '--background', '--factory-startup', '--disable-autoexec',
                   '--python-exit-code', '1', '--python', str(script), '--',
                   '--source', str(source), '--output', str(stage_blend), '--report', str(stage_report)]
        try:
            completed = subprocess.run(command, cwd=str(stage), env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                timeout=300, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise BlenderCreationError(t('blender.timeout')) from exc
        except OSError as exc:
            raise BlenderCreationError(t('blender.launch_failed',error=exc)) from exc
        if completed.returncode != 0:
            tail = '\n'.join(completed.stdout.splitlines()[-18:])
            raise BlenderCreationError(t('blender.exit_failed',code=completed.returncode,tail=tail))
        if not stage_blend.is_file() or stage_blend.stat().st_size < 12:
            raise BlenderCreationError(t('blender.empty'))
        with stage_blend.open('rb') as stream:
            if stream.read(7) != b'BLENDER':
                raise BlenderCreationError(t('blender.header'))
        try:
            report = json.loads(stage_report.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise BlenderCreationError(t('blender.report')) from exc
        if report.get('verified_after_reopen') is not True:
            raise BlenderCreationError(t('blender.reopen'))
        report.update({'path': str(destination), 'saved_path': str(destination),
                       'report_path': str(report_path), 'source': str(source),
                       'blender_executable': str(executable), 'bytes': stage_blend.stat().st_size})
        stage_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        _publish_new_file(stage_blend, destination)
        _publish_new_file(stage_report, report_path)
    log(t('blender.saved',actions=report['actions'],armatures=report['armatures'],images=report['packed_images']))
    return report
