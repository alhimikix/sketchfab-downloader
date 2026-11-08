"""Offline release smoke test: GUI, bundled resources, Python libs and browser."""
import json
from pathlib import Path
import sys
import traceback


def run_self_test(report_path):
    from app_version import VERSION
    report={'version':VERSION,'platform':sys.platform,'frozen':bool(getattr(sys,'frozen',False))}
    report_path=Path(report_path).resolve();report_path.parent.mkdir(parents=True,exist_ok=True)
    def stage(name):
        report['stage']=name
        report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    stage('imports')
    import faulthandler
    if sys.stderr is not None:
        faulthandler.dump_traceback_later(45,repeat=True)
    try:
        import numpy as np
        from PIL import Image
        import tkinter as tk
        from platform_support import resource_root, external_process_environment
        from sketchfab_local import browser_channel
        from playwright.sync_api import sync_playwright
        from playwright._impl._driver import compute_driver_executable
        from exporter import triangles
        root=resource_root()
        import certifi
        from https_support import ssl_context
        assert ssl_context().cert_store_stats()['x509_ca']>0
        if report['frozen']:assert Path(certifi.where()).resolve().is_relative_to(root)
        report['tlsCertificatesBundled']=True
        for name in ('capture.js','animation_capture.js','animation_loader.js','sketchfab_capture_loader.js','fab_capture.js','fab_rig_capture.js','blend_import.py'):
            assert (root/name).is_file(),f'Missing resource: {name}'
        np.testing.assert_array_equal(triangles([0,1,2,3],5),[[0,1,2],[2,1,3]])
        image=Image.new('RGBA',(1,1),(10,20,30,40));assert image.getpixel((0,0))[3]==40
        stage('tkinter')
        from downloader_ui import Downloader
        from i18n import CATALOGS, set_language, t
        for language in ('en','ru'):
            app=Downloader(auto_updates=False,language=language,persist_settings=False)
            app.withdraw();app.show_updates();app.update_window.withdraw();app.update()
            assert app.download_button.cget('text')==CATALOGS[language]['ui.download']
            app.language_var.set(CATALOGS['en' if language=='ru' else 'ru']['language.name'])
            app.change_language();app.update_idletasks()
            assert app.download_button.cget('text')==t('ui.download')
            app.destroy()
        set_language('en')
        report['languages']=['en','ru']
        from updater_helper import child_environment
        import subprocess
        helper=root/'update-helper'/('SketchfabUpdateHelper.exe' if sys.platform=='win32' else 'SketchfabUpdateHelper')
        if report['frozen']:
            subprocess.run([str(helper),'--self-test'],env=child_environment(),timeout=60,check=True,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        report['anonymousUpdates']=True
        channel=browser_channel()
        driver,entry=compute_driver_executable()
        assert Path(driver).is_file() and Path(entry).is_file(),'Missing Playwright driver'
        if report['frozen']:assert Path(driver).resolve().is_relative_to(root),'External Node runtime was selected'
        stage('playwright-driver')
        with sync_playwright() as pw:
            stage('chromium-launch')
            browser=pw.chromium.launch(channel=channel,headless=True,env=external_process_environment())
            try:
                stage('browser-page')
                page=browser.new_page();page.set_content('<title>Sketchfab Local offline test</title><canvas id="scene"></canvas>')
                assert page.title()=='Sketchfab Local offline test'
                assert page.evaluate('2+2')==4
                page.evaluate((root/'fab_capture.js').read_text(encoding='utf-8'))
                page.evaluate((root/'fab_rig_capture.js').read_text(encoding='utf-8'))
                page.evaluate((root/'sketchfab_capture_loader.js').read_text(encoding='utf-8'))
                assert page.evaluate('__localExportGeometries instanceof Set && __localExportHookCount === 0')
                report['sketchfabModuleObserver']=True
                assert page.evaluate('typeof __fabGpuCapture.arm === "function" && typeof __fabAnimationControls.step === "function"')
                from fab_source import fab_reference
                from fab_geometry import TransformTable
                from fab_animation import decompose
                from fab_native_rig import source_scene
                from fab_native_animation import decode_animations
                assert source_scene({'osg.Node':{}})['bones']==[]
                assert decode_animations({},[],{})==[]
                report['originalFabRigRuntime']=True
                assert decompose(np.eye(4))['rotation']==[0,0,0,1]
                assert fab_reference('https://fab.com/dope/12345678-1234-1234-1234-123456789abc')[0]=='dope'
                report['fabPreviewRuntime']=True
                report['browser']=browser.version
                stage('webgl')
                report['webglAvailable']=page.evaluate('!!document.getElementById("scene").getContext("webgl2")')
            finally:
                stage('browser-close')
                browser.close()
        report['success']=True
    except Exception:
        report.update(success=False,error=traceback.format_exc())
    if sys.stderr is not None:faulthandler.cancel_dump_traceback_later()
    report['stage']='finished'
    report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return 0 if report['success'] else 1
