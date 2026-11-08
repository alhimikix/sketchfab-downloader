"""Exercise the bundled installer against an isolated temporary application."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

from app_version import VERSION
from platform_support import resource_root
from updater import installed_application
from updater_helper import PROTOCOL, STAGE_PREFIX, child_environment, write_json


def run_update_smoke_test(report_path):
    report = {'version': VERSION, 'platform': sys.platform, 'success': False}
    try:
        original, kind = installed_application()
        with tempfile.TemporaryDirectory(prefix='Sketchfab updater smoke ') as temp:
            root = Path(temp)
            stage = root / (STAGE_PREFIX + 'fixture');stage.mkdir()
            target = root / ('Installed app.exe' if kind == 'windows' else 'Installed app.app' if kind == 'macos' else 'Installed app')
            prepared = stage / ('payload.exe' if kind == 'windows' else 'payload.app' if kind == 'macos' else 'payload')
            if kind == 'windows':
                target.write_bytes(b'previous app fixture')
                shutil.copy2(original, prepared)
            else:
                target.mkdir()
                (target / 'user-file.txt').write_text('Keep this user file', encoding='utf-8')
                shutil.copytree(original, prepared, symlinks=True)
            helper_name = 'SketchfabUpdateHelper' + ('.exe' if kind == 'windows' else '')
            helper = stage / helper_name
            shutil.copy2(resource_root() / 'update-helper' / helper_name, helper)
            manifest = stage / 'manifest.json'
            write_json(manifest, {'protocol': PROTOCOL, 'target': str(target), 'prepared': str(prepared),
                                 'kind': kind, 'parent_pid': 0, 'version': VERSION,
                                 'result': str(root / 'result.json'), 'self_test': True})
            completed = subprocess.run([str(helper), '--manifest', str(manifest)],
                                       env=child_environment(), timeout=240,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result = json.loads((root / 'result.json').read_text(encoding='utf-8'))
            assert completed.returncode == 0 and result['status'] == 'success', result
            installed = json.loads((stage / 'installed-smoke.json').read_text(encoding='utf-8'))
            assert installed['success'] and installed['frozen'] and installed['version'] == VERSION
            if kind == 'linux':
                assert (target / 'user-file.txt').read_text() == 'Keep this user file'
            assert len(list(root.glob(target.name + '.previous-*'))) == 1
            report.update(success=True, installedVersion=installed['version'], preservedUserFiles=True)
    except Exception:
        report['error'] = traceback.format_exc()
    write_json(report_path, report)
    return 0 if report['success'] else 1
