"""Extract the public release package and test that exact distributable."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app_version import VERSION


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='SketchfabLocal release smoke ') as tmp:
        folder=Path(tmp)
        assets=[p for p in (ROOT/'release-assets').glob('SketchfabLocal-'+VERSION+'-'+args.target+'*') if p.suffix!='.sha256']
        if len(assets)!=1:raise RuntimeError(f'Expected one release package, got {assets}')
        asset=assets[0]
        if sys.platform=='win32':executable=asset
        elif sys.platform=='darwin':
            subprocess.run(['ditto','-x','-k',str(asset),str(folder)],check=True)
            executable=folder/'SketchfabLocal.app'/'Contents'/'MacOS'/'SketchfabLocal'
        else:
            with tarfile.open(asset) as archive:archive.extractall(folder,filter='data')
            executable=folder/'SketchfabLocal'/'SketchfabLocal'
        report=ROOT/('smoke-'+args.target+'.json')
        environment=os.environ.copy()
        # Invalid host overrides prove the frozen bootstrap selects its own
        # Node/browser and does not use a developer Python installation.
        environment.update(PYTHONHOME=str(folder/'no-python'),PYTHONPATH=str(folder/'no-modules'),
                           PYTHONNOUSERSITE='1',PLAYWRIGHT_NODEJS_PATH=str(folder/'no-node'),
                           PLAYWRIGHT_BROWSERS_PATH=str(folder/'no-browser'))
        try:
            process=subprocess.run([str(executable),'--self-test',str(report)],cwd=folder,env=environment,timeout=180)
        finally:
            if report.is_file():print(report.read_text(encoding='utf-8'),flush=True)
        if process.returncode:raise SystemExit(process.returncode)
        result=json.loads(report.read_text(encoding='utf-8'))
        if not result.get('success') or not result.get('frozen'):raise RuntimeError('Release smoke test failed')
        update_report=ROOT/('smoke-updater-'+args.target+'.json')
        try:
            process=subprocess.run([str(executable),'--self-test-update',str(update_report)],cwd=folder,env=environment,timeout=300)
        finally:
            if update_report.is_file():print(update_report.read_text(encoding='utf-8'),flush=True)
        if process.returncode:raise SystemExit(process.returncode)
        if not json.loads(update_report.read_text(encoding='utf-8')).get('success'):
            raise RuntimeError('Packaged installer smoke test failed')


if __name__=='__main__':main()
