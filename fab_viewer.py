"""Capture a public Fab preview in an isolated, bundled browser."""
from __future__ import annotations

import base64
import json
import hashlib
import gzip
import io
import re
import time
import urllib.request
from urllib.parse import urlparse

from fab_source import get_public, prefetched, normalized_prefetch, expand_dotted
from fab_geometry import reconstruct
from i18n import t
from platform_support import resource_root, external_process_environment
from https_support import ssl_context


def capture_source_script(route):
    response = route.fetch(timeout=30000)
    body = response.text()
    match = re.search(r'\._free\s*=\s*[\w$]+\.([\w$]+)', body)
    if match:
        body = 'window.__fabNativeFreeExport = ' + json.dumps(match[1]) + ';\n' + body
    route.fulfill(response=response, body=body)


def read_source_scene(page):
    status = page.evaluate('window.__fabSourceCapture.status()')
    if status['scenes'] != 1 or status['errors']:
        raise ValueError(t('fab.rig_missing'))
    size = page.evaluate('__fabSourceCapture.size(0)')
    chunks = [page.evaluate('(offset) => __fabSourceCapture.read(0,offset,524288)', offset)
              for offset in range(0, size, 512 * 1024)]
    return json.loads(''.join(chunks))


def animation_buffers(uid, enabled, output_dir, log):
    result = {}
    directory = output_dir / 'animation-data'
    directory.mkdir(exist_ok=True)
    for animation in enabled:
        log(t('export.loading_animation', name=animation['name']))
        url = animation['url']
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'media.fab.com' or not parsed.path.startswith('/model_previews/' + uid + '/animations/'):
            raise ValueError(t('fab.unsupported', feature='unexpected preview animation host/path'))
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.fab.com/'})
        with urllib.request.urlopen(request, timeout=45, context=ssl_context()) as response:
            if urlparse(response.url).hostname != 'media.fab.com':
                raise ValueError(t('fab.unsupported', feature='unexpected animation redirect'))
            data = response.read(64 * 1024 * 1024 + 1)
        if data.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError(t('fab.unsupported', feature='preview animation larger than 64 MiB'))
        filename = parsed.path.rsplit('/', 1)[-1].removesuffix('.gz')
        result[filename] = data
        (directory / (animation['uid'] + '.bin')).write_bytes(data)
    return result


def read_bytes(page, kind, description):
    result = bytearray()
    for offset in range(0, description['size'], 512 * 1024):
        encoded = page.evaluate('([kind,id,offset]) => window.__fabGpuCapture[kind](id,offset,524288)',
                                [kind, description['id'], offset])
        result.extend(base64.b64decode(encoded, validate=True))
    if len(result) != description['size']:
        raise ValueError(t('fab.invalid_buffer'))
    return bytes(result)


def capture_frame(page, timeout):
    page.evaluate('window.__fabGpuCapture.arm()')
    # Static/paused previews render on demand. A tiny camera wheel event requests
    # a frame; reconstruction uses world matrices and excludes the camera.
    page.mouse.move(450, 400)
    page.mouse.wheel(0, 1)
    page.wait_for_function('window.__fabGpuCapture.status().complete', timeout=timeout * 1000)
    return page.evaluate('window.__fabGpuCapture.summary()')


def capture_preview(metadata, output_dir, log=print, timeout=90, headed=False):
    from playwright.sync_api import sync_playwright
    from sketchfab_local import browser_channel
    uid = metadata['previewUid']
    prefix = '/i/previews/' + uid
    channel = browser_channel()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel, headless=not headed, env=external_process_environment())
        try:
            context = browser.new_context(viewport={'width': 1200, 'height': 1000}, service_workers='block')
            page = context.new_page()
            scripts = [resource_root() / name for name in ('fab_rig_capture.js', 'fab_capture.js')]
            page.add_init_script('\n'.join(path.read_text(encoding='utf-8') for path in scripts))
            page.route('https://static.fab.com/**/*.js', capture_source_script)
            response = page.goto('https://www.fab.com/dope/' + uid, wait_until='domcontentloaded', timeout=timeout * 1000)
            if not response or response.status != 200:
                raise RuntimeError(t('export.viewer_http', status=response.status if response else 'unknown'))
            data = prefetched(response.text())
            options = data.get(prefix + '/doptions') or get_public(prefix + '/doptions')
            options['materials'] = {key: expand_dotted(value) for key, value in options.get('materials', {}).items()}
            textures = data.get(prefix + '/textures') or get_public(prefix + '/textures')
            animations = data.get(prefix + '/animations') or get_public(prefix + '/animations')
            preview = data.get(prefix, {})
            if preview.get('hasDoptions') is False or not options.get('materials'):
                legacy = data.get(prefix + '/options') or get_public(prefix + '/options')
                options['_legacyOptions'] = legacy
                vertex_color = legacy.get('shading', {}).get('vertexColor', {})
                options['materials'] = {key: {'name': value.get('name', key),
                    'basecolor': value.get('channels', {}).get('AlbedoPBR', {}),
                    'vertexcolor': {'enabled': vertex_color.get('enable', False),
                        'colorspace': vertex_color.get('colorSpace', 'srgb'), 'usealpha': vertex_color.get('useAlpha', False)}}
                    for key, value in legacy.get('materials', {}).items()}
            args = data.get('dopeArgs', {})
            if args.get('listingId') and '/dope/' in metadata['viewerUrl']:
                listing = get_public('/i/listings/' + args['listingId'])
                metadata.update(name=listing.get('title', preview.get('name', uid)),
                                user={'displayName': listing.get('user', {}).get('sellerName', 'Unknown author')},
                                license=listing.get('licenses', []),
                                viewerUrl='https://www.fab.com/listings/' + args['listingId'])
            log(t('fab.preview', uid=uid))
            if len(metadata.get('availablePreviews', [])) > 1:
                log(t('fab.first_preview', count=len(metadata['availablePreviews'])))
            log(t('export.viewer_opened'))
            start, stable_since, last = time.monotonic(), time.monotonic(), None
            summary = None
            while time.monotonic() - start < timeout:
                remaining = max(1, timeout - (time.monotonic() - start))
                summary = capture_frame(page, remaining)
                signature = [(d['vao'], d['materialId'], d['args'], d['attributes']) for d in summary['draws']]
                if signature != last:
                    stable_since, last = time.monotonic(), signature
                if summary['draws'] and time.monotonic() - stable_since >= 3 and time.monotonic() - start >= 10:
                    break
                page.wait_for_timeout(1000)
            else:
                raise RuntimeError(t('fab.timeout', timeout=timeout))
            log(t('export.transfer'))
            total = sum(d['size'] for group in ('buffers', 'textures') for d in summary[group])
            if total > 256 * 1024 * 1024:
                raise ValueError(t('fab.unsupported', feature='decoded preview larger than 256 MiB'))
            buffers = {d['id']: read_bytes(page, 'readBuffer', d) for d in summary['buffers']}
            texture_data = {d['id']: read_bytes(page, 'readTexture', d) for d in summary['textures']}
            (output_dir / 'fab-capture.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
            (output_dir / 'fab-options.json').write_text(json.dumps(options, indent=2), encoding='utf-8')
            capture = reconstruct(summary, buffers, texture_data, options)
            enabled = [a for a in animations.get('results', []) if a.get('isEnabled', True)]
            if enabled or any('_fabSkin' in mesh for mesh in capture['meshes']):
                from fab_native_rig import preserve_rig
                from fab_native_animation import decode_animations
                document = read_source_scene(page)
                text = json.dumps(document, ensure_ascii=False, separators=(',', ':'))
                (output_dir / 'fab-native-scene.json').write_text(text, encoding='utf-8')
                blobs = animation_buffers(uid, enabled, output_dir, log) if enabled else {}
                try:
                    clips = decode_animations(document, enabled, blobs)
                except (ValueError, KeyError) as error:
                    raise ValueError(t('fab.native_decode_failed', detail=str(error))) from error
                capture = preserve_rig(capture, document, clips)
                capture['rigPreservation']['sourceSceneSha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
                log(t('fab.native_rig', count=capture['rigPreservation']['boneCount']))
            metadata['animationCount'] = len(enabled)
            page.screenshot(path=str(output_dir / 'source-viewer.png'))
            # Public model metadata only; never persist browser storage/cookies.
            (output_dir / 'fab-preview.json').write_text(json.dumps({
                'previewUid': uid, 'listingUrl': metadata['viewerUrl'], 'options': options,
                'animations': animations, 'textures': textures}, ensure_ascii=False, indent=2), encoding='utf-8')
            return capture, normalized_prefetch(uid, options, textures, animations)
        finally:
            browser.close()
