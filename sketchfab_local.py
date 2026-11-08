"""Local model and animation capture. Run without arguments for the desktop UI."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from urllib.parse import urlparse
from platform_support import resource_root, executable_directory, default_download_directory, external_process_environment
from app_version import VERSION
from i18n import t, set_language
from https_support import ssl_context

BUNDLE_ROOT = resource_root()
# Bundled resources are extracted to a temporary directory. User files must
# remain beside the executable, outside that disposable runtime directory.
ROOT = executable_directory() if getattr(sys,'frozen',False) else BUNDLE_ROOT
DEFAULT_URL = 'https://sketchfab.com/3d-models/elderwood-lillia-skin-moth-league-of-legends-3d39ab2d22514d598d2aa34544a5416f'


def model_uid(value):
    value = value.strip()
    if re.fullmatch(r'[0-9a-fA-F]{32}',value):
        return value.lower()
    parsed = urlparse(value)
    if parsed.scheme != 'https' or parsed.hostname not in ('sketchfab.com','www.sketchfab.com'):
        raise ValueError(t('export.invalid_url'))
    match = re.search(r'([0-9a-fA-F]{32})(?:/embed)?/?$',parsed.path)
    if not match:
        raise ValueError(t('export.missing_uid'))
    return match.group(1).lower()


def model_source(value):
    if urlparse(value.strip()).hostname in ('fab.com', 'www.fab.com'):
        from fab_source import fab_reference
        return 'fab', fab_reference(value)[1]
    return 'sketchfab', model_uid(value)


def fetch_metadata(uid):
    request = urllib.request.Request(f'https://api.sketchfab.com/v3/models/{uid}',headers={'User-Agent':'SketchfabLocalExport/1.0'})
    with urllib.request.urlopen(request,timeout=30,context=ssl_context()) as response:
        return json.load(response)


def prefetch_data(document,uid):
    for block in re.findall(r'<!--(.*?)-->',document,flags=re.S):
        block = html.unescape(block)
        if block.startswith('{') and f'/i/models/{uid}' in block:
            return json.loads(block)
    raise RuntimeError(t('export.missing_metadata'))


def browser_channel():
    bundled = BUNDLE_ROOT / 'browser-runtime'
    if bundled.is_dir() and any(bundled.glob('chromium-*')):
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(bundled)
        return 'chromium'
    candidates = [
        ('chrome',Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe'),
        ('chrome',Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe'),
        ('msedge',Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'),
        ('msedge',Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Microsoft/Edge/Application/msedge.exe')]
    for channel,path in candidates:
        if path.is_file():
            return channel
    # Source installs may use Playwright's normal per-user Chromium cache.
    return 'chromium'


def capture_model(uid, output_dir, log=print, timeout=90, headed=False):
    from playwright.sync_api import sync_playwright
    hook = re.compile(r'(\b[A-Za-z_$][\w$]*\.drawGeometry\(this\);)')
    hook_count = 0
    network_errors = []
    # Configure Playwright's environment before its Node driver starts.
    channel = browser_channel()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel,headless=not headed,env=external_process_environment())
        try:
            context = browser.new_context(viewport={'width':1200,'height':1000},service_workers='block')
            page = context.new_page()
            page.add_init_script((BUNDLE_ROOT/'sketchfab_capture_loader.js').read_text(encoding='utf-8'))
            def instrument(route):
                nonlocal hook_count
                try:
                    response = route.fetch(timeout=30000)
                    body = response.text()
                    body,count = hook.subn(r'\1window.__localExportGeometries.add(this);',body)
                    hook_count += count
                    route.fulfill(response=response,body=body)
                except Exception as exc:
                    network_errors.append(str(exc)[:300])
                    route.abort()
            page.route('https://static.sketchfab.com/**/*.js',instrument)
            url = f'https://sketchfab.com/models/{uid}/embed?autostart=1&preload=1&animation_autoplay=0'
            response = page.goto(url,wait_until='domcontentloaded',timeout=timeout*1000)
            if not response or response.status != 200:
                raise RuntimeError(t('export.viewer_http',status=response.status if response else 'unknown'))
            prefetch = prefetch_data(response.text(),uid)
            log(t('export.viewer_opened'))
            start = time.monotonic()
            stable_since = start
            last_signature = None
            while time.monotonic()-start < timeout:
                page.wait_for_timeout(1000)
                signature = page.evaluate('''() => [...window.__localExportGeometries].filter(g=>g.getVertexAttributeList?.().Vertex?.getItemSize()===3).map(g=>[g.getInstanceID(),g.getVertexAttributeList().Vertex.getElements()?.length]).sort().toString()''')
                if signature != last_signature:
                    stable_since = time.monotonic()
                    last_signature = signature
                if signature and time.monotonic()-stable_since >= 5 and time.monotonic()-start >= 10:
                    break
            else:
                runtime_hooks = page.evaluate('window.__localExportHookCount || 0')
                raise RuntimeError(t('export.timeout',timeout=timeout,hooks=hook_count+runtime_hooks,errors=network_errors[:2]))
            animations=prefetch.get(f'/i/models/{uid}/animations?optimized=1',{}).get('results',[])
            if animations:
                loader=(BUNDLE_ROOT/'animation_loader.js').read_text(encoding='utf-8')
                for animation in animations:
                    if not animation.get('isEnabled',True):continue
                    log(t('export.loading_animation',name=animation['name']))
                    page.evaluate(loader,animation['uid'])
                    page.wait_for_function('(uid)=>!!window.__localAnimationManager._instanceAnimations[uid]',arg=animation['uid'],timeout=timeout*1000)
            # Send large numeric buffers as a JSON string. Playwright's recursive
            # object-value transport expands each scalar and stalls on large scenes.
            log(t('export.transfer'))
            capture_script = (BUNDLE_ROOT/'capture.js').read_text(encoding='utf-8')
            animation_script=(BUNDLE_ROOT/'animation_capture.js').read_text(encoding='utf-8')
            capture_script='() => ('+animation_script+')(('+capture_script+')())'
            size = page.evaluate('() => { window.__localCaptureJSON = JSON.stringify((' + capture_script + ')()); return window.__localCaptureJSON.length; }')
            chunks=[]
            for offset in range(0,size,512*1024):
                chunks.append(page.evaluate('(offset) => window.__localCaptureJSON.slice(offset,offset+524288)',offset))
            page.evaluate('delete window.__localCaptureJSON')
            capture = json.loads(''.join(chunks))
            page.screenshot(path=str(output_dir/'source-viewer.png'))
            capture['hookCount'] = hook_count + page.evaluate('window.__localExportHookCount || 0')
            return capture,prefetch
        finally:
            browser.close()


def run_export(url, destination, log=print, timeout=90, headed=False):
    from exporter import prepare_meshes, validate_export_counts, write_glb, write_obj
    from materials import build_materials
    provider, uid = model_source(url)
    log(t('export.reading'))
    if provider == 'fab':
        from fab_source import resolve
        metadata = resolve(url)
        uid = metadata['previewUid']
    else:
        metadata = fetch_metadata(uid)
    label = re.sub(r'[^\w-]+','-',metadata.get('name',uid),flags=re.ASCII).strip('-')[:70] or uid
    base = Path(destination).expanduser().resolve()
    base.mkdir(parents=True,exist_ok=True)
    directory = base/f'{label}-{uid[:8]}'
    if directory.exists():
        directory = base/f'{label}-{uid[:8]}-{datetime.now().strftime("%Y%m%d-%H%M%S")}-{time.time_ns()%1000000:06d}'
    directory.mkdir()
    warnings=[]
    try:
        log(metadata['name'])
        if provider == 'fab':
            from fab_viewer import capture_preview
            capture, prefetch = capture_preview(metadata, directory, log, timeout, headed)
        else:
            capture,prefetch = capture_model(uid,directory,log,timeout,headed)
        (directory/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        # Save model-only metadata, not browser account/session state.
        model_prefetch = {k:v for k,v in prefetch.items() if k.startswith(f'/i/models/{uid}')}
        (directory/'viewer-data.json').write_text(json.dumps(model_prefetch,ensure_ascii=False,indent=2),encoding='utf-8')
        (directory/'geometry.json').write_text(json.dumps(capture,separators=(',',':')),encoding='utf-8')
        warnings.extend(capture.get('warnings', []))
        surface_count = sum(any(p['mode'] in (4,5,6) for p in m['primitives']) for m in capture['meshes'])
        log(t('export.geometry',count=surface_count))
        mats = build_materials(prefetch,uid,directory,log)
        (directory/'materials.json').write_text(json.dumps(mats,ensure_ascii=False,indent=2),encoding='utf-8')
        warnings.extend(mats.get('warnings',[]))
        meshes = prepare_meshes(capture,mats['material_ids'],warnings)
        count = sum(len(m['faces']) for m in meshes)
        expected = metadata.get('faceCount')
        geometry_validation,count_warnings=validate_export_counts(capture,meshes,expected)
        warnings.extend(count_warnings)
        scene=capture.get('scene')
        clips=scene.get('animations',[]) if scene else []
        animation_meta=prefetch.get(f'/i/models/{uid}/animations?optimized=1',{}).get('results',[])
        if metadata.get('animationCount'):
            loaded={clip['uid'] for clip in clips}
            missing=[a['name'] for a in animation_meta if a.get('isEnabled',True) and a['uid'] not in loaded]
            if missing:raise RuntimeError(t('export.missing_animations',names=', '.join(missing)))
            names={a['uid']:a['name'] for a in animation_meta}
            for clip in clips:clip['name']=names.get(clip['uid'],clip['name'])
            from animation_export import filter_unbound_channels
            filter_unbound_channels(scene,warnings)
        author = metadata.get('user',{}).get('displayName','Unknown author')
        source_url = metadata.get('viewerUrl') or f'https://sketchfab.com/models/{uid}'
        attribution = f'{metadata["name"]} | {author} | {source_url}'
        log(t('export.writing'))
        needs_scene = bool(scene and (clips or any('skin' in mesh or 'morph' in mesh for mesh in capture['meshes'])))
        if needs_scene:
            from animation_export import prepare_animated_meshes
            if clips:log(t('export.animations',count=len(clips)))
            animated_meshes=prepare_animated_meshes(capture,mats['material_ids'],warnings)
            glb=write_glb(animated_meshes,mats,directory,attribution,scene=scene)
        else:glb = write_glb(meshes,mats,directory,attribution)
        write_obj(meshes,mats,directory,attribution)
        from blender_create import create_blend
        log(t('export.blender'))
        blend_report=create_blend(directory,log)
        report = {'model':metadata['name'],'uid':uid,'provider':provider,'source':source_url,'author':author,
                  'createdUtc':datetime.now(timezone.utc).isoformat(),'format':'animated viewer geometry' if clips else 'rigged viewer geometry' if needs_scene else 'static viewer geometry',
                  'animations':[{'name':c['name'],'duration':c['duration'],'channels':len(c['channels']),
                      'sourceChannels':c.get('sourceChannelCount'),'defaultResetChannels':c.get('defaultResetChannelCount',0),
                      'unboundChannels':c.get('unboundChannelCount',0)} for c in clips],
                  'animationBake':capture.get('animationBake'),
                  'rigPreservation':capture.get('rigPreservation'),
                  'blend':blend_report,
                  'meshes':len(meshes),'verticesWithUVSplits':sum(len(m['POSITION']) for m in meshes),
                  'triangles':count,'expectedTriangles':expected,'geometryValidation':geometry_validation,'materials':len(mats['materials']),
                  'images':len(mats.get('images',[])),'glbBytes':glb.stat().st_size,
                  'sha256':hashlib.sha256(glb.read_bytes()).hexdigest(),'warnings':warnings,
                  'notes':['Viewer geometry is optimized and is not the original authoring file.',
                           'OBJ materials support fewer effects than GLB. Lighting/postprocessing are not embedded.']}
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (directory/'SOURCE.txt').write_text(attribution+'\n\n'+json.dumps(metadata.get('license',{}),ensure_ascii=False,indent=2)+'\n\nOptimized viewer export; no license is granted by this tool.\n',encoding='utf-8')
        log(t('export.done',count=count,parts=len(meshes),path=glb))
        for warning in warnings:
            log(t('export.note',warning=warning))
        return directory
    except Exception as exc:
        (directory/'error.txt').write_text(str(exc),encoding='utf-8')
        raise


def gui(receipt=None,language=None):
    from downloader_ui import launch
    launch(receipt=receipt,language=language)


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='Export public Sketchfab/Fab previews to GLB/OBJ/BLEND')
    parser.add_argument('--version',action='version',version=VERSION)
    parser.add_argument('--language',choices=('en','ru'),help='Interface and exporter language')
    parser.add_argument('--update-receipt',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--self-test',metavar='REPORT',help='Run an offline packaged-runtime smoke test')
    parser.add_argument('--self-test-update',metavar='REPORT',help=argparse.SUPPRESS)
    parser.add_argument('url',nargs='?',help='Sketchfab URL/UID or Fab listing/preview URL; omitted opens GUI')
    parser.add_argument('--output',default=str(default_download_directory()))
    parser.add_argument('--timeout',type=int,default=90)
    parser.add_argument('--show-browser',action='store_true')
    args=parser.parse_args()
    if args.language:set_language(args.language)
    if args.self_test_update:
        from updater_smoke_test import run_update_smoke_test
        return run_update_smoke_test(Path(args.self_test_update))
    if args.self_test:
        from runtime_self_test import run_self_test
        result=run_self_test(Path(args.self_test))
        if result==0 and args.update_receipt:
            from updater_helper import write_json
            write_json(args.update_receipt,{'version':VERSION})
        return result
    if args.url:
        try:run_export(args.url,args.output,lambda msg:print(msg,flush=True),args.timeout,args.show_browser)
        except Exception as exc:
            print(f'ERROR: {exc}',file=sys.stderr);return 1
    else:gui(args.update_receipt,args.language)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
