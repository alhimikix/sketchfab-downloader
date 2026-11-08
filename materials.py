"""Convert viewer material metadata and public image resources to glTF 2.0.

Images remain in their ordinary top-left raster orientation. The geometry
exporter must convert the viewer's bottom-left UVs with v = 1 - v.
Requires Pillow and numpy. No account data or browser profile is used.
"""
from __future__ import annotations

import io
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from i18n import t
from https_support import ssl_context


class MaterialError(RuntimeError):
    pass


def bake_opacity(base_image, opacity_image, channel):
    """Pack linear opacity into straight (unpremultiplied) base-color RGBA."""
    if base_image is None:
        base_image=Image.new('RGBA',opacity_image.size,(255,255,255,255))
    base=base_image.convert('RGBA')
    # Keep the more detailed of the two maps; never enlarge just one channel
    # independently of the final RGBA texture.
    size=max([base.size,opacity_image.size],key=lambda s:s[0]*s[1])
    base=base.resize(size,Image.Resampling.LANCZOS)
    pixel_channel=channel.get('texture',{}).get('channel')
    if pixel_channel is None:
        pixel_channel='A' if channel.get('texture',{}).get('internalFormat')=='ALPHA' else 'R'
    pixel_channel=str(pixel_channel).upper()
    if pixel_channel not in ('R','G','B','A'):raise MaterialError(f'Unsupported opacity channel: {pixel_channel}')
    opacity=opacity_image.convert('RGBA').getchannel(pixel_channel).resize(size,Image.Resampling.LANCZOS)
    if channel.get('invert',False):opacity=ImageOps.invert(opacity)
    # The explicit opacity channel defines coverage. Existing base-color alpha
    # is not multiplied a second time (it may be the very same source channel).
    base.putalpha(opacity)
    return base


def same_texture_mapping(a,b):
    def mapping(ch):
        tex=ch.get('texture',{});uv=ch.get('UVTransforms',{})
        return (tex.get('texCoordUnit',0),tex.get('wrapS','REPEAT'),tex.get('wrapT','REPEAT'),
                tuple(uv.get('scale',[1,1])),tuple(uv.get('offset',[0,0])),uv.get('rotation',0))
    return mapping(a)==mapping(b)


def select_texture_variant(images):
    raster=[im for im in images if urllib.parse.urlparse(im.get('url','')).path.lower().endswith(('.png','.jpg','.jpeg'))]
    supported=[im for im in raster if not im.get('pk') or (int(im['width'])%8==0 and int(im['height'])%8==0)]
    if not supported:
        raise MaterialError('No decodable PNG/JPEG texture variant is available')
    return max(supported,key=lambda im:(int(im['width'])*int(im['height']),im.get('size',0)))


def decode_viewer_image(image: Image.Image, key: int, version: int = 1) -> Image.Image:
    """Undo v1 viewer image layout, matching its shipped texture upload shader.

    The shader walks 8x8 blocks in diagonal zigzag order, rotates/reflects each
    block according to its ordinal modulo four, and samples ordinal - key.
    This operates on decoded pixel bytes; it is not a lossy image re-encode.
    """
    if not key:
        return image.copy()
    if version != 1:
        raise MaterialError(f"Unsupported viewer image layout version: {version}")
    width, height = image.size
    if width % 8 or height % 8:
        raise MaterialError(f"Protected texture dimensions are not multiples of 8: {image.size}")
    mode = 'RGBA' if 'A' in image.getbands() else 'RGB'
    pixels = np.array(image.convert(mode))
    rows, cols = height // 8, width // 8
    coords = []
    for diagonal in range(rows + cols - 1):
        xs = list(range(max(0, diagonal - rows + 1), min(cols - 1, diagonal) + 1))
        if diagonal % 2:
            xs.reverse()
        coords.extend((diagonal - x, x) for x in xs)
    ys, xs = np.asarray(coords).T
    tiles = pixels.reshape(rows, 8, cols, 8, pixels.shape[2]).transpose(0, 2, 1, 3, 4)
    ordered = tiles[ys, xs].copy()
    # Physical -> shader ordinal within each block.
    ordered[1::4] = ordered[1::4, :, ::-1].copy()
    ordered[2::4] = ordered[2::4].swapaxes(1, 2).copy()
    ordered[3::4] = np.rot90(ordered[3::4], -1, axes=(1, 2)).copy()
    ordered = np.roll(ordered, int(key) % len(coords), axis=0)
    # Shader ordinal -> physical coordinates of the output block.
    ordered[1::4] = ordered[1::4, :, ::-1].copy()
    ordered[2::4] = ordered[2::4].swapaxes(1, 2).copy()
    ordered[3::4] = np.rot90(ordered[3::4], 1, axes=(1, 2)).copy()
    tiles[ys, xs] = ordered
    result = tiles.transpose(0, 2, 1, 3, 4).reshape(height, width, pixels.shape[2])
    return Image.fromarray(result)


def _safe_name(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', value).strip('._')[:90] or 'texture'


def _download(url: str, destination: Path) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'https' or not ((parsed.hostname or '').endswith('.sketchfab.com') or parsed.hostname == 'media.fab.com'):
        raise MaterialError(f"Unexpected texture source host: {parsed.hostname}")
    if destination.exists():
        data = destination.read_bytes()
        try:
            with Image.open(io.BytesIO(data)) as check:
                check.verify()
            return data
        except Exception:
            pass
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(request, timeout=45, context=ssl_context()) as response:
            data = response.read(64 * 1024 * 1024 + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise MaterialError(f"Unable to download texture {destination.name}: {exc}") from exc
    if len(data) > 64 * 1024 * 1024:
        raise MaterialError(f"Texture exceeds 64 MiB: {destination.name}")
    try:
        with Image.open(io.BytesIO(data)) as check:
            check.verify()
    except Exception as exc:
        raise MaterialError(f"Texture is not a valid image: {destination.name}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + '.part')
    partial.write_bytes(data)
    partial.replace(destination)
    return data


def build_materials(prefetch: dict, uid: str, output_dir: Path, log=print) -> dict:
    """Return glTF material arrays, IDs, warnings and texture provenance.

    `images[].uri` is relative to output_dir. `material_ids` accepts material
    UUIDs and stateSetID strings. Downloaded originals are retained below
    textures/source; decoded and derived maps are ordinary PNG files.
    """
    output_dir = Path(output_dir)
    texture_dir = output_dir / 'textures'
    texture_dir.mkdir(parents=True, exist_ok=True)
    model = prefetch['/i/models/' + uid]
    texture_records = prefetch.get('/i/models/' + uid + '/textures?optimized=1', {})
    records = texture_records.get('results', []) if isinstance(texture_records, dict) else texture_records
    texture_lookup = {item['uid']: item for item in records}
    result = {'materials': [], 'images': [], 'textures': [], 'samplers': [],
              'material_ids': {}, 'warnings': [], 'texture_sources': [], 'extensionsUsed': []}
    source_cache: dict[str, tuple[Path, Image.Image]] = {}
    image_indices: dict[str, int] = {}
    sampler_indices: dict[str, int] = {}
    texture_indices: dict[tuple[int, int], int] = {}

    def warning(text):
        if text not in result['warnings']:
            result['warnings'].append(text)

    def extension(name):
        if name not in result['extensionsUsed']:
            result['extensionsUsed'].append(name)

    def source(channel):
        tex_uid = channel.get('texture', {}).get('uid')
        if not tex_uid:
            return None
        if tex_uid in source_cache:
            return source_cache[tex_uid]
        record = texture_lookup.get(tex_uid)
        if not record:
            raise MaterialError(f"Texture metadata is missing: {tex_uid}")
        compatible = [im for im in record.get('images', [])
                      if urllib.parse.urlparse(im.get('url', '')).path.lower().endswith(('.png', '.jpg', '.jpeg'))]
        if not compatible:
            raise MaterialError(f"No PNG/JPEG variant for texture: {record.get('name', tex_uid)}")
        variant = select_texture_variant(compatible)
        largest=max(compatible,key=lambda im:int(im['width'])*int(im['height']))
        if variant is not largest:
            warning(t('material.compatible',name=record.get('name',tex_uid),width=variant['width'],height=variant['height']))
        source_ext = Path(urllib.parse.urlparse(variant['url']).path).suffix.lower()
        name = _safe_name(Path(record.get('name', tex_uid)).stem) + '_' + tex_uid[:8]
        original_path = texture_dir / 'source' / (name + '_' + variant.get('uid','image')[:8] + source_ext)
        log(t('material.texture',name=record.get('name',tex_uid),width=variant['width'],height=variant['height']))
        raw = _download(variant['url'], original_path)
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.size != (int(variant['width']), int(variant['height'])):
                raise MaterialError(f"Texture dimensions differ from metadata: {record.get('name')}")
            image = decode_viewer_image(opened, int(variant.get('pk') or 0), int(variant.get('pv') or 1))
        decoded_path = texture_dir / (name + '.png')
        image.save(decoded_path)
        source_cache[tex_uid] = (decoded_path, image)
        result['texture_sources'].append({'uid': tex_uid, 'name': record.get('name'), 'url': variant['url'],
            'width': image.width, 'height': image.height, 'pk': variant.get('pk'), 'pv': variant.get('pv'),
            'source': original_path.relative_to(output_dir).as_posix(),
            'decoded': decoded_path.relative_to(output_dir).as_posix()})
        return source_cache[tex_uid]

    def texture_info(path, channel):
        rel = Path(path).relative_to(output_dir).as_posix()
        if rel not in image_indices:
            image_indices[rel] = len(result['images'])
            result['images'].append({'uri': rel, 'name': Path(path).stem})
        enum = {'NEAREST': 9728, 'LINEAR': 9729, 'NEAREST_MIPMAP_NEAREST': 9984,
                'LINEAR_MIPMAP_NEAREST': 9985, 'NEAREST_MIPMAP_LINEAR': 9986, 'LINEAR_MIPMAP_LINEAR': 9987,
                'CLAMP_TO_EDGE': 33071, 'MIRRORED_REPEAT': 33648, 'REPEAT': 10497}
        tex = channel.get('texture', {})
        sampler = {k: (tex[k] if isinstance(tex.get(k), int) and tex[k] in enum.values() else enum.get(tex.get(k), default)) for k, default in
                   [('magFilter', 9729), ('minFilter', 9987), ('wrapS', 10497), ('wrapT', 10497)]}
        key = json.dumps(sampler, sort_keys=True)
        if key not in sampler_indices:
            sampler_indices[key] = len(result['samplers'])
            result['samplers'].append(sampler)
        pair = (image_indices[rel], sampler_indices[key])
        if pair not in texture_indices:
            texture_indices[pair] = len(result['textures'])
            result['textures'].append({'source': pair[0], 'sampler': pair[1]})
        info = {'index': texture_indices[pair], 'texCoord': int(tex.get('texCoordUnit', 0))}
        transform = channel.get('UVTransforms', {})
        scale = transform.get('scale', [1, 1])
        offset = transform.get('offset', [0, 0])
        angle = float(transform.get('rotation', 0))
        if scale != [1, 1] or offset != [0, 0] or angle:
            # Conjugate source transform by the geometry exporter's v -> 1-v.
            extension('KHR_texture_transform')
            info['extensions'] = {'KHR_texture_transform': {
                'scale': scale, 'rotation': -angle,
                'offset': [offset[0] - scale[1] * math.sin(angle),
                           1 - offset[1] - scale[1] * math.cos(angle)]}}
        return info

    def enabled(channels, name):
        channel = channels.get(name, {})
        return channel if channel.get('enable', False) else {}

    for material_uid, material in model.get('options', {}).get('materials', {}).items():
        if not isinstance(material, dict) or 'channels' not in material:
            continue
        channels = material['channels']
        name = material.get('name', material_uid)
        pbr = {'metallicFactor': 0.0, 'roughnessFactor': 1.0}
        converted = {'name': name, 'pbrMetallicRoughness': pbr,
                     'doubleSided': material.get('cullFace', 'DISABLE') == 'DISABLE',
                     'extras': {'sourceMaterialID': material_uid, 'sourceStateSetID': material.get('stateSetID')}}
        albedo = enabled(channels, 'AlbedoPBR') or enabled(channels, 'DiffuseColor')
        color = albedo.get('color', [1, 1, 1])
        factor = float(albedo.get('factor', 1))
        pbr['baseColorFactor'] = [min(1.0, max(0.0, float(c) * factor)) for c in color[:3]] + [1.0]
        albedo_im=source(albedo)
        if albedo_im:
            pbr['baseColorTexture'] = texture_info(albedo_im[0], albedo)

        rough = enabled(channels, 'RoughnessPBR')
        metal = enabled(channels, 'MetalnessPBR')
        ao = enabled(channels, 'AOPBR')
        pbr['roughnessFactor'] = float(rough.get('factor', 1))
        pbr['metallicFactor'] = float(metal.get('factor', 0))
        rough_im, metal_im, ao_im = source(rough), source(metal), source(ao)
        if rough.get('invert') and not rough_im:
            pbr['roughnessFactor'] = 1 - pbr['roughnessFactor']
        if metal.get('invert') and not metal_im:
            pbr['metallicFactor'] = 1 - pbr['metallicFactor']
        if rough_im or metal_im:
            reference_channel = rough if rough_im else metal
            for ch, im in [(rough, rough_im), (metal, metal_im)]:
                if im and (ch.get('UVTransforms', {}) != reference_channel.get('UVTransforms', {})
                           or ch.get('texture', {}).get('texCoordUnit', 0) != reference_channel.get('texture', {}).get('texCoordUnit', 0)):
                    raise MaterialError(f"Different roughness/metallic UV mappings in {name}; packing would lose fidelity")
            size = max([im[1].size for im in (rough_im, metal_im) if im], key=lambda sz: sz[0] * sz[1])
            def grey(im, channel):
                pixels = im[1].convert('RGB').getchannel('R').resize(size, Image.Resampling.LANCZOS) if im else Image.new('L', size, 255)
                return ImageOps.invert(pixels) if im and channel.get('invert') else pixels
            packed = Image.merge('RGB', (Image.new('L', size, 255), grey(rough_im, rough), grey(metal_im, metal)))
            packed_path = texture_dir / (_safe_name(name) + '_' + material_uid[:8] + '_metallicRoughness.png')
            packed.save(packed_path)
            pbr['metallicRoughnessTexture'] = texture_info(packed_path, reference_channel)
        if ao_im:
            converted['occlusionTexture'] = dict(texture_info(ao_im[0], ao), strength=float(ao.get('factor', 1)))

        normal = enabled(channels, 'NormalMap')
        if (normal_im := source(normal)):
            path, pixels = normal_im
            if normal.get('flipY', False):
                pixels = pixels.convert('RGB')
                r, g, b = pixels.split()
                path = path.with_name(path.stem + '_OpenGL.png')
                Image.merge('RGB', (r, ImageOps.invert(g), b)).save(path)
            converted['normalTexture'] = dict(texture_info(path, normal), scale=float(normal.get('factor', 1)))

        emissive = enabled(channels, 'EmitColor')
        if emissive and float(emissive.get('factor', 0)) > 0:
            strength = float(emissive.get('factor', 1))
            emit_color = emissive.get('color', [1, 1, 1])
            converted['emissiveFactor'] = [min(1.0, max(0.0, float(x))) for x in emit_color[:3]]
            if (im := source(emissive)):
                converted['emissiveTexture'] = texture_info(im[0], emissive)
            extension('KHR_materials_emissive_strength')
            converted.setdefault('extensions', {})['KHR_materials_emissive_strength'] = {'emissiveStrength': strength}
            if emissive.get('type', 'additive') != 'additive':
                warning(f'{name}: multiplicative emission is approximated by glTF additive emission.')

        sheen = enabled(channels, 'Sheen')
        if sheen:
            extension('KHR_materials_sheen')
            sheen_rough = enabled(channels, 'SheenRoughness')
            spec = {'sheenColorFactor': [min(1.0, max(0.0, float(x) * float(sheen.get('factor', 1))))
                                         for x in sheen.get('colorFactor', [1, 1, 1])],
                    'sheenRoughnessFactor': float(sheen_rough.get('factor', 1))}
            if (im := source(sheen)):
                spec['sheenColorTexture'] = texture_info(im[0], sheen)
            converted.setdefault('extensions', {})['KHR_materials_sheen'] = spec
            if sheen_rough.get('texture'):
                warning(f'{name}: the separate sheen roughness texture is not represented.')

        opacity = enabled(channels, 'Opacity')
        alpha_mask = enabled(channels, 'AlphaMask')
        if opacity or alpha_mask:
            channel = opacity or alpha_mask
            converted['alphaMode'] = 'MASK' if alpha_mask or channel.get('type') == 'alphaMask' else 'BLEND'
            if converted['alphaMode'] == 'MASK':
                converted['alphaCutoff'] = float(channel.get('cutoff', 0.5))
            pbr['baseColorFactor'][3] = float(opacity.get('factor', 1))
            if channel.get('texture'):
                if albedo_im and not same_texture_mapping(albedo,channel):
                    raise MaterialError(t('material.opacity_uv',name=name))
                opacity_im=source(channel)
                packed=bake_opacity(albedo_im[1] if albedo_im else None,opacity_im[1],channel)
                packed_path=texture_dir/(_safe_name(name)+'_'+material_uid[:8]+'_baseColorAlpha.png')
                packed.save(packed_path)
                pbr['baseColorTexture']=texture_info(packed_path,albedo if albedo_im else channel)
        for unsupported in ['Displacement', 'BumpMap', 'ClearCoat', 'Anisotropy', 'SubsurfaceScattering', 'SubsurfaceTranslucency']:
            if enabled(channels, unsupported):
                warning(f'{name}: {unsupported} is not represented in this glTF material.')
        # Shipped viewer shader: f0 = 0.08 * getMaterialF0(). Khronos uses
        # dielectric_f0 = 0.04 * specularColor, so the conversion is 2*factor.
        specular = enabled(channels, 'SpecularF0')
        if specular and float(specular.get('factor', 0.5)) != 0.5:
            extension('KHR_materials_specular')
            converted.setdefault('extensions', {})['KHR_materials_specular'] = {
                'specularColorFactor': [2 * float(specular['factor'])] * 3}
        if specular.get('texture'):
            warning(f'{name}: the separate dielectric reflectance texture is not represented.')
        if material.get('shadeless') or model.get('options', {}).get('shading', {}).get('type') == 'shadeless':
            converted.pop('emissiveFactor', None)
            converted.pop('emissiveTexture', None)
            converted['extensions'] = {'KHR_materials_unlit': {}}
            extension('KHR_materials_unlit')
        index = len(result['materials'])
        result['material_ids'][material_uid] = index
        if material.get('stateSetID') is not None:
            result['material_ids'][str(material['stateSetID'])] = index
        result['materials'].append(converted)
    # A scene-level unlit override can remove previously assembled extensions.
    # Keep the declaration in sync with extensions present in the final result.
    used = set()
    def collect_extensions(value):
        if isinstance(value, dict):
            used.update(value.get('extensions', {}))
            for child in value.values(): collect_extensions(child)
        elif isinstance(value, list):
            for child in value: collect_extensions(child)
    collect_extensions(result['materials'])
    result['extensionsUsed'] = [name for name in result['extensionsUsed'] if name in used]
    return result
