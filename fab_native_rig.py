"""Retain the preview's named OSG skeleton, bind spaces and joint hierarchy."""
from __future__ import annotations

import copy
import numpy as np

from i18n import t
from animation_export import ANIMATION_PATHS

NODE_TYPES = {'osg.Node', 'osg.MatrixTransform', 'osgAnimation.Skeleton', 'osgAnimation.Bone',
              'osg.Geometry', 'osgAnimation.RigGeometry'}
IDENTITY = np.eye(4)


def matrix(value):
    result = np.asarray(value, dtype=np.float64).reshape(4, 4, order='F')
    if not np.all(np.isfinite(result)):
        raise ValueError(t('fab.invalid_buffer'))
    return result


def values(obj):
    return {item['Name']: item.get('Value') for item in obj.get('UserDataContainer', {}).get('Values', [])}


def source_scene(document):
    """Resolve OSG object references and retain all bones, including end joints."""
    registry = {}
    def collect(value):
        if isinstance(value, dict):
            if 'UniqueID' in value:
                uid = value['UniqueID']
                if len(value) > len(registry.get(uid, {})):
                    registry[uid] = value
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(document)
    def resolve(obj):
        return registry.get(obj.get('UniqueID'), obj)
    nodes, rigs, geometries, seen = [], [], [], {}
    def visit(wrapper, parent=None, skeleton=None):
        for kind, original in wrapper.items():
            if kind not in NODE_TYPES or not isinstance(original, dict):
                continue
            obj = resolve(original)
            key = (kind, obj.get('UniqueID', id(obj)))
            if key in seen:
                if nodes[seen[key]]['parent'] != parent:
                    raise ValueError(t('fab.unsupported', feature='scene nodes with multiple parents'))
                return
            source = None
            if kind == 'osgAnimation.RigGeometry':
                source = resolve(next(iter(obj['SourceGeometry'].values())))
            elif kind == 'osg.Geometry':
                source = obj
            if source is not None:
                modes = [next(iter(p.values())).get('Mode') for p in source.get('PrimitiveSetList', [])]
                if str(values(obj).get('wireframe', '0')) == '1' or modes and not any(m in (4, 5, 6, 'TRIANGLES', 'TRIANGLE_STRIP', 'TRIANGLE_FAN') for m in modes):
                    return
            index = len(nodes)
            seen[key] = index
            if kind == 'osgAnimation.Skeleton':
                skeleton = index
            local = matrix(obj.get('Matrix', IDENTITY.ravel(order='F')))
            world = local if parent is None else matrix(nodes[parent]['world']) @ local
            node = {'name': obj.get('Name', ''), 'parent': parent, 'sourceID': obj.get('UniqueID'),
                    'type': kind, 'skeleton': skeleton, 'matrix': local.ravel(order='F').tolist(),
                    'world': world.ravel(order='F').tolist()}
            if 'InvBindMatrixInSkeletonSpace' in obj:
                node['inverseBind'] = matrix(obj['InvBindMatrixInSkeletonSpace']).ravel(order='F').tolist()
            callbacks = [resolve(next(iter(callback.values()))) for callback in obj.get('UpdateCallbacks', [])]
            callback = next((c for c in callbacks if 'StackedTransforms' in c), None)
            if callback:
                node['target'] = callback.get('Name')
                trs = {}
                order = []
                for wrapper in callback['StackedTransforms']:
                    transform_kind, transform = next(iter(wrapper.items()))
                    transform = resolve(transform)
                    specification = {'osgAnimation.StackedTranslate': ('Translate', 'translation'),
                        'osgAnimation.StackedQuaternion': ('Quaternion', 'rotation'),
                        'osgAnimation.StackedScale': ('Scale', 'scale'),
                        'osgAnimation.StackedMatrix': ('Matrix', 'bindmatrix')}.get(transform_kind)
                    if specification is None:
                        raise ValueError(t('fab.unsupported', feature=transform_kind))
                    field, path = specification
                    order.append(path)
                    trs[path] = transform[field]
                if order != sorted(order, key=['bindmatrix', 'translation', 'rotation', 'scale'].index) or len(set(order)) != len(order):
                    raise ValueError(t('fab.unsupported', feature='stacked transform order'))
                node['trs'] = trs
            nodes.append(node)
            if kind == 'osgAnimation.RigGeometry':
                rigs.append({'node': index, 'skeleton': skeleton, 'boneMap': obj.get('BoneMap', {}),
                             'sourceGeometryID': source.get('UniqueID'), 'name': source.get('Name', ''),
                             'rigIndex': values(obj).get('rigIndex')})
            elif kind == 'osg.Geometry':
                vertex = resolve(source.get('VertexAttributeList', {}).get('Vertex', {}))
                arrays = vertex.get('Array', {})
                count = next(iter(arrays.values())).get('Size') if arrays else None
                geometries.append({'node': index, 'vertices': count, 'name': source.get('Name', '')})
            for child in obj.get('Children', []):
                visit(child, index, skeleton)
    visit(document)
    bones = [i for i, node in enumerate(nodes) if node['type'] == 'osgAnimation.Bone']
    if len(bones) > 65535 or any('inverseBind' not in nodes[i] for i in bones):
        raise ValueError(t('fab.unsupported', feature='incomplete or oversized source bind palette'))
    slots, palettes, cache = [], [], {}
    for rig in rigs:
        skeleton = rig['skeleton']
        if skeleton is None:
            raise ValueError(t('fab.unsupported', feature='a rig outside a skeleton'))
        names = {}
        for i in bones:
            if nodes[i]['skeleton'] != skeleton:
                continue
            name = nodes[i]['name']
            if name in names:
                raise ValueError(t('fab.unsupported', feature='ambiguous bone names inside one skeleton'))
            names[name] = i
        ordered = sorted(rig['boneMap'].items(), key=lambda item: item[1])
        if [value for _, value in ordered] != list(range(len(ordered))):
            raise ValueError(t('fab.unsupported', feature='noncontiguous original bone palette'))
        indices = [names[name] for name, _ in ordered]
        c = np.linalg.inv(matrix(nodes[skeleton]['world'])) @ matrix(nodes[rig['node']]['world'])
        key = (skeleton, tuple(indices), tuple(np.round(c.ravel(), 9)))
        if key not in cache:
            palette = {'base': len(slots), 'bones': indices, 'skeleton': skeleton,
                       'geometryToSkeleton': c.ravel(order='F').tolist()}
            cache[key] = len(palettes)
            palettes.append(palette)
            for bone in indices:
                if 'inverseBind' not in nodes[bone]:
                    raise ValueError(t('fab.unsupported', feature='missing source inverse bind matrix'))
                slots.append({'bone': bone, 'palette': len(palettes) - 1})
        rig['palette'] = cache[key]
    return {'nodes': nodes, 'bones': bones, 'rigs': rigs, 'palettes': palettes, 'slots': slots, 'geometries': geometries}


def preserve_rig(capture, document, clips):
    source = source_scene(document)
    result = copy.deepcopy(capture)
    nodes = copy.deepcopy(source['nodes'])
    # Geometry is attached below separate source nodes so material splits do
    # not replace or detach any original skeleton node.
    scene = {'nodes': nodes, 'roots': [i for i, n in enumerate(nodes) if n['parent'] is None], 'animations': clips}
    targets = {}
    for node in nodes:
        if node.get('target'):
            if node['target'] in targets:
                raise ValueError(t('fab.unsupported', feature='ambiguous animation target names'))
            targets[node['target']] = node
    animated_paths = set()
    for clip in clips:
        clip['sourceChannelCount'] = len(clip['channels'])
        for channel in clip['channels']:
            path = ANIMATION_PATHS.get(channel['path'])
            if path is None or channel['target'] not in targets:
                raise ValueError(t('fab.unsupported', feature='unbound or unsupported source animation channel'))
            animated_paths.add((channel['target'], path))
    for clip in clips:
        present = {(c['target'], ANIMATION_PATHS[c['path']]) for c in clip['channels']}
        clip['defaultResetChannelCount'] = 0
        for target, path in sorted(animated_paths - present):
            default = targets[target].get('trs', {}).get(path)
            if default is None:
                raise ValueError(t('fab.unsupported', feature='missing animation default transform'))
            # Blender retains unkeyed values from the previous action. Native
            # playback resets absent channels to their source defaults instead.
            # Explicit constant tracks preserve that behavior when switching clips.
            clip['channels'].append({'target': target, 'path': path, 'type': 'quat' if path == 'rotation' else 'vec3',
                'times': [clip['firstKeyTime'], clip['lastKeyTime']], 'values': list(default) * 2})
            clip['defaultResetChannelCount'] += 1
    for mesh in result['meshes']:
        skin = mesh.get('_fabSkin')
        if skin is None:
            candidates = [g for g in source['geometries'] if g['vertices'] == mesh.get('sourceVertexCount')]
            if len(candidates) > 1:
                world = matrix(mesh['matrices'][0])
                candidates = [g for g in candidates if np.allclose(matrix(nodes[g['node']]['world']), world, atol=1e-5, rtol=1e-5)]
            if len(candidates) != 1:
                raise ValueError(t('fab.unsupported', feature='ambiguous rigid mesh attachment in source hierarchy'))
            mesh['node'] = len(nodes)
            nodes.append({'name': mesh['name'], 'parent': candidates[0]['node']})
            continue
        if mesh['_fabTable'][5] // 2 != len(source['slots']):
            raise ValueError(t('fab.unsupported', feature='source/GPU bone palette size mismatch'))
        joints = np.asarray(skin['joints'], dtype=np.int64)
        weights = np.asarray(skin['weights'], dtype=np.float64)
        if np.any(weights.sum(axis=1) <= 0):
            raise ValueError(t('fab.unsupported', feature='unweighted skinned vertices'))
        active = np.unique(joints[weights > 0])
        if np.any(active < 0) or np.any(active >= len(source['slots'])):
            raise ValueError(t('fab.invalid_buffer'))
        palette_ids = {source['slots'][int(index)]['palette'] for index in active}
        if len(palette_ids) != 1:
            raise ValueError(t('fab.unsupported', feature='a mesh spanning different source rig palettes'))
        palette = source['palettes'][palette_ids.pop()]
        skeleton = palette['skeleton']
        bones = [i for i in source['bones'] if nodes[i]['skeleton'] == skeleton]
        remap = {index: i for i, index in enumerate(bones)}
        local = np.zeros_like(joints)
        for index in active:
            local[joints == index] = remap[source['slots'][int(index)]['bone']]
        attrs = copy.deepcopy(mesh['attributes'])
        attrs['Vertex']['data'] = skin['bindPositions']
        attrs['Normal']['data'] = skin['bindNormals']
        if skin['bindTangents'] is not None:
            attrs['Tangent']['data'] = skin['bindTangents']
        attrs['Bones'] = {'size': 4, 'data': local.ravel().tolist()}
        attrs['Weights'] = {'size': 4, 'data': weights.ravel().tolist()}
        mesh['animatedAttributes'] = attrs
        mesh['node'] = len(nodes)
        nodes.append({'name': mesh['name'], 'parent': skeleton})
        mesh['skin'] = {'joints': bones, 'inverseBind': [nodes[i]['inverseBind'] for i in bones],
                        'matrixFromGeometryToSkeleton': palette['geometryToSkeleton'], 'skeleton': skeleton}
    result['scene'] = scene
    result['rigPreservation'] = {'representation': 'original preview skeleton', 'boneCount': len(source['bones']),
        'originalBoneNames': True, 'originalHierarchy': True, 'originalInverseBindMatrices': True,
        'sourceAnimationChannels': True, 'animations': len(clips)}
    return result
