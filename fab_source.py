"""Public Fab preview resolution and material metadata normalization."""
from __future__ import annotations

import html
import json
import re
import urllib.request
from urllib.parse import urlparse

from https_support import ssl_context
from i18n import t

UUID = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'


def fab_reference(value):
    parsed = urlparse(value.strip())
    if parsed.scheme != 'https' or parsed.hostname not in ('fab.com', 'www.fab.com') or parsed.username or parsed.password:
        raise ValueError(t('export.invalid_url'))
    match = re.fullmatch(r'(?:/[a-z]{2}(?:-[A-Za-z]{2})?)?/(listings|dope)/(' + UUID + r')/?', parsed.path)
    if not match:
        raise ValueError(t('export.invalid_url'))
    return match.group(1), match.group(2).lower()


def get_public(path):
    request = urllib.request.Request('https://www.fab.com' + path, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.fab.com/', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=45, context=ssl_context()) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError(t('fab.metadata_large'))
    return json.loads(raw)


def prefetched(document):
    match = re.search(r'<script\b[^>]*\bid=["\']js-json-data-prefetched-data["\'][^>]*>(.*?)</script>', document, re.S)
    if not match:
        raise ValueError(t('export.missing_metadata'))
    # JSON script text is normally raw; HTML entity escaping is used by some pages.
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return json.loads(html.unescape(match.group(1)))


def resolve(value):
    kind, uid = fab_reference(value)
    listing = None
    if kind == 'listings':
        listing = get_public('/i/listings/' + uid)
        previews = sorted((m for m in listing.get('medias', [])
                           if m.get('type') == 'model' and m.get('previewUid')
                           and m.get('processingStatus') == 'succeeded'), key=lambda m: m.get('position', 0))
        if not previews:
            raise ValueError(t('fab.no_preview'))
        preview_uid = previews[0]['previewUid']
    else:
        preview_uid = uid
        previews = [{'previewUid': uid}]
    metadata = {'name': (listing or {}).get('title', preview_uid), 'uid': preview_uid,
                'provider': 'fab', 'viewerUrl': 'https://www.fab.com/' + kind + '/' + uid,
                'user': {'displayName': (listing or {}).get('user', {}).get('sellerName', 'Unknown author')},
                'license': (listing or {}).get('licenses', []),
                'previewUid': preview_uid, 'availablePreviews': [m['previewUid'] for m in previews]}
    return metadata


CHANNELS = {'basecolor': 'AlbedoPBR', 'roughness': 'RoughnessPBR', 'metalness': 'MetalnessPBR',
            'occlusion': 'AOPBR', 'normal': 'NormalMap', 'emission': 'EmitColor', 'opacity': 'Opacity',
            'cutout': 'AlphaMask', 'sheen': 'Sheen', 'sheenroughness': 'SheenRoughness',
            'specularlevel': 'SpecularF0', 'clearcoat': 'ClearCoat', 'anisotropy': 'Anisotropy'}


def expand_dotted(original):
    """Fab doptions serializes material properties as literal dotted keys."""
    result = {}
    for key, value in original.items():
        parts = key.split('.')
        parent = result
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        parent[parts[-1]] = value
    return result


def normalize_materials(options):
    result = {}
    for uid, original in options.get('materials', {}).items():
        original = expand_dotted(original)
        channels = {}
        for source, target in CHANNELS.items():
            channel = original.get(source)
            if not isinstance(channel, dict):
                continue
            converted = {key: value for key, value in channel.items() if key not in ('enabled', 'texture')}
            converted['enable'] = channel.get('enabled', False)
            texture = channel.get('texture') or {}
            if texture.get('uri'):
                converted['texture'] = {'uid': texture['uri'], 'texCoordUnit': texture.get('uv') or 0}
                for a, b in [('wraps', 'wrapS'), ('wrapt', 'wrapT'), ('mag', 'magFilter'), ('min', 'minFilter')]:
                    if texture.get(a) is not None:
                        converted['texture'][b] = texture[a]
                transform = texture.get('transform') or {}
                converted['UVTransforms'] = {'scale': transform.get('scale') or [1, 1],
                                             'offset': transform.get('offset') or [0, 0],
                                             'rotation': transform.get('rotation') or 0}
            if source == 'normal':
                converted['flipY'] = channel.get('opengl') is False
            if source == 'sheen':
                converted['colorFactor'] = channel.get('color', [1, 1, 1])
            channels[target] = converted
        result[uid] = {'name': original.get('name', uid), 'channels': channels,
                       'cullFace': 'DISABLE' if original.get('doublesided') else 'BACK'}
    return result


def normalized_prefetch(uid, options, textures, animations):
    model_options = options.get('_legacyOptions') or {'materials': normalize_materials(options)}
    return {'/i/models/' + uid: {'options': model_options},
            '/i/models/' + uid + '/textures?optimized=1': textures,
            '/i/models/' + uid + '/animations?optimized=1': animations}
