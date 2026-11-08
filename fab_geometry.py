"""Reconstruct Fab's decoded color pass, excluding repeated material passes."""
from __future__ import annotations

import numpy as np

from exporter import triangles, unit_vectors
from i18n import t

TYPES = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5124: '<i4', 5125: '<u4', 5126: '<f4'}


def attribute(descriptor, buffers):
    if descriptor.get('divisor'):
        raise ValueError(t('fab.unsupported', feature='instanced attributes'))
    dtype = np.dtype(TYPES[descriptor['type']])
    raw = buffers[descriptor['buffer']]
    width, offset = descriptor['size'], descriptor['offset']
    stride = descriptor['stride'] or width * dtype.itemsize
    if stride < width * dtype.itemsize or offset < 0 or len(raw) < offset + width * dtype.itemsize:
        raise ValueError(t('fab.invalid_buffer'))
    count = 1 + (len(raw) - offset - width * dtype.itemsize) // stride
    values = np.ndarray((count, width), dtype=dtype, buffer=raw, offset=offset, strides=(stride, dtype.itemsize)).copy()
    if descriptor.get('normalized') and dtype.kind in 'iu':
        values = values.astype(np.float64) / np.iinfo(dtype).max
        if dtype.kind == 'i':
            values = np.maximum(values, -1)
    return values


class TransformTable:
    def __init__(self, draw, textures, descriptors):
        self.table = list(map(int, draw['table']))
        self.parity = int(draw['frameParity'])
        self.descriptor = descriptors[draw['transformTexture']]
        self.data = np.frombuffer(textures[draw['transformTexture']], dtype='<f4').reshape(-1, 4)
        if self.parity not in (0, 1) or self.table[0] <= 0:
            raise ValueError(t('fab.invalid_buffer'))

    def texel(self, offset):
        line = self.table[0]
        layer, rest = divmod(offset, line * line)
        row, column = divmod(rest, line)
        d = self.descriptor
        if layer < 0 or layer >= d['depth'] or row >= d['height'] or column >= d['width']:
            raise ValueError(t('fab.invalid_buffer'))
        return self.data[(layer * d['height'] + row) * d['width'] + column]

    def matrix(self, base, index):
        values = np.concatenate([self.texel(base + index * 3 + i) for i in range(3)])
        matrix = np.eye(4)
        matrix[:3, :] = values.reshape(4, 3).T
        if not np.all(np.isfinite(matrix)):
            raise ValueError(t('fab.invalid_buffer'))
        return matrix

    def world(self, geometry):
        if geometry >= self.table[8]:
            raise ValueError(t('fab.invalid_buffer'))
        return self.matrix(self.table[2], geometry + (self.table[6] >> 1) * self.parity)

    def skin(self, bone):
        if bone < 0 or bone >= self.table[5] >> 1:
            raise ValueError(t('fab.invalid_buffer'))
        return self.matrix(self.table[1], bone + (self.table[5] >> 1) * self.parity)

    def material(self, geometry):
        return int(self.texel(self.table[4] + geometry)[1])


def material_uid(draw, options):
    items = list(options.get('materials', {}).items())
    index = int(draw['materialId']) - 2
    if index < 0 or index >= len(items):
        raise ValueError(t('fab.unsupported', feature='material table layout'))
    uid, material = items[index]
    base = material.get('basecolor', {})
    expected = np.asarray(base.get('color', [1, 1, 1])) * base.get('factor', 1)
    actual = np.asarray(draw['materialScalars'][20][:3])
    if not np.allclose(expected, actual, atol=2e-5):
        raise ValueError(t('fab.unsupported', feature='material ID mapping'))
    return uid


def reconstruct(summary, buffers, textures, options):
    if summary.get('errors'):
        raise ValueError(t('fab.unsupported', feature='; '.join(summary['errors'])))
    descriptions = {d['id']: d for d in summary['textures']}
    meshes, seen, cache = [], set(), {}
    for draw in summary['draws']:
        args = draw['args']
        if 'Instanced' in draw['kind'] and args[-1] != 1:
            raise ValueError(t('fab.unsupported', feature='instanced drawing'))
        if args[0] not in (4, 5, 6):
            continue
        attrs = {}
        for d in draw['attributes']:
            key = tuple(d.items())
            if key not in cache:
                cache[key] = attribute(d, buffers)
            attrs[d['name']] = cache[key]
        if any(key in attrs for key in ('Attribute13', 'Attribute14')):
            raise ValueError(t('fab.unsupported', feature='morph-target deformation'))
        position = attrs['Attribute0']
        if position.shape[1] != 4 or position.dtype != np.dtype('<f4'):
            raise ValueError(t('fab.unsupported', feature='position/geometry-ID layout'))
        geometry_ids = position[:, 3].copy().view('<u4') & 0x7fffffff
        if draw['kind'].startswith('drawElements'):
            dtype = np.dtype(TYPES[args[2]])
            raw = buffers[draw['indexBuffer']]
            if args[3] < 0 or args[3] + args[1] * dtype.itemsize > len(raw):
                raise ValueError(t('fab.invalid_buffer'))
            indices = np.frombuffer(raw, dtype=dtype, offset=args[3], count=args[1])
        else:
            indices = np.arange(args[1], args[1] + args[2], dtype=np.uint32)
        faces = triangles(indices, args[0])
        if not len(faces):
            continue
        if faces.max() >= len(position):
            raise ValueError(t('fab.invalid_buffer'))
        table = TransformTable(draw, textures, descriptions)
        material = material_uid(draw, options)
        ids = geometry_ids[faces]
        if np.any(ids != ids[:, :1]):
            raise ValueError(t('fab.unsupported', feature='mixed geometry IDs in a triangle'))
        for geometry in np.unique(ids[:, 0]):
            geometry = int(geometry)
            if table.material(geometry) != int(draw['materialId']):
                continue
            part = faces[ids[:, 0] == geometry]
            # The same color pass can be replayed for transparency/selection.
            key = (draw['vao'], geometry, material, draw['kind'], tuple(args[:4]))
            if key in seen:
                continue
            seen.add(key)
            used, inverse = np.unique(part, return_inverse=True)
            source_attrs = {'Vertex': {'size': 3, 'data': position[used, :3].astype(np.float64)}}
            for name, target in [('Attribute1', 'Normal'), ('Attribute2', 'Tangent'), ('Attribute3', 'Color')]:
                if name in attrs:
                    source_attrs[target] = {'size': attrs[name].shape[1], 'data': attrs[name][used].astype(np.float64)}
            if 'Color' in source_attrs:
                settings = options['materials'][material].get('vertexcolor', {})
                if not settings.get('enabled', True):
                    source_attrs.pop('Color')
                else:
                    color = source_attrs['Color']['data']
                    if settings.get('colorspace', 'srgb') == 'srgb':
                        rgb = color[:, :3]
                        color[:, :3] = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
                    if color.shape[1] == 4 and not settings.get('usealpha', False):
                        color[:, 3] = 1
            for slot in range(4, 11):
                if f'Attribute{slot}' not in attrs:
                    continue
                uv = attrs[f'Attribute{slot}'][used]
                for pair in range(uv.shape[1] // 2):
                    source_attrs[f'TexCoord{(slot - 4) * 2 + pair}'] = {'size': 2, 'data': uv[:, pair * 2:pair * 2 + 2]}
            skin = None
            if 'Attribute11' in attrs or 'Attribute12' in attrs:
                if 'Attribute11' not in attrs or 'Attribute12' not in attrs:
                    raise ValueError(t('fab.invalid_buffer'))
                joints, weights = attrs['Attribute11'][used], attrs['Attribute12'][used]
                if joints.shape[1] != 4 or weights.shape[1] != 4 or np.any(weights < 0):
                    raise ValueError(t('fab.invalid_buffer'))
                # Match the viewer's identity fallback for entirely unweighted vertices.
                skin = {'joints': joints.copy(), 'weights': weights.copy(), 'geometry': geometry}
                matrices = np.zeros((len(used), 4, 4))
                for bone in np.unique(joints[weights > 0]):
                    matrices += (weights * (joints == bone)).sum(axis=1)[:, None, None] * table.skin(int(bone))
                matrices[weights.sum(axis=1) == 0] = np.eye(4)
                source_attrs['Vertex']['data'] = np.einsum('nij,nj->ni', matrices[:, :3, :3], source_attrs['Vertex']['data']) + matrices[:, :3, 3]
                for target in ('Normal', 'Tangent'):
                    if target in source_attrs:
                        values = source_attrs[target]['data']
                        values[:, :3] = unit_vectors(np.einsum('nij,nj->ni', matrices[:, :3, :3], values[:, :3]))
            mesh = {'name': f'Geometry {geometry} - {options["materials"][material].get("name", material)}',
                    'material': material, 'sourceID': len(meshes), 'geometryID': geometry,
                    'sourceVertexCount': int(np.count_nonzero(geometry_ids == geometry)),
                    '_fabTable': draw['table'],
                    'attributes': {k: {'size': v['size'], 'data': v['data'].ravel().tolist()} for k, v in source_attrs.items()},
                    'primitives': [{'mode': 4, 'indices': inverse.ravel().tolist()}],
                    'matrices': [table.world(geometry).ravel(order='F').tolist()]}
            if skin is not None:
                mesh['_fabSkin'] = {'joints': skin['joints'].tolist(), 'weights': skin['weights'].tolist(),
                                    'bindPositions': position[used, :3].ravel().tolist(),
                                    'bindNormals': attrs.get('Attribute1', np.empty((0, 3)))[used].ravel().tolist(),
                                    'bindTangents': attrs['Attribute2'][used].ravel().tolist() if 'Attribute2' in attrs else None}
            meshes.append(mesh)
    if not meshes:
        raise ValueError(t('fab.no_geometry'))
    return {'meshes': meshes, 'provider': 'fab', 'audit': {'source': 'fab-webgl-color-pass',
            'surfaceInstances': len(meshes), 'renderableTriangles': sum(len(m['primitives'][0]['indices']) // 3 for m in meshes)}}
