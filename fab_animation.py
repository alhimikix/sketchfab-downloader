"""Bake visible Fab motion to a synthetic, flat glTF deformation rig."""
from __future__ import annotations

import copy
import math
import numpy as np

from i18n import t


def unpack_matrices(values):
    values = np.asarray(values, dtype=np.float64).reshape(-1, 4, 3)
    result = np.repeat(np.eye(4)[None], len(values), axis=0)
    result[:, :3, :] = values.transpose(0, 2, 1)
    return result


def pose_tables(raw):
    return {tuple(item['table']): {'world': unpack_matrices(item['world']), 'skin': unpack_matrices(item['skin'])} for item in raw}


def decompose(matrix):
    translation = matrix[:3, 3]
    linear = matrix[:3, :3]
    scale = np.linalg.norm(linear, axis=0)
    if np.any(scale < 1e-10) or not np.all(np.isfinite(matrix)):
        raise ValueError(t('fab.unsupported', feature='singular animation transform'))
    if np.linalg.det(linear) < 0:
        scale[0] *= -1
    rotation = linear / scale
    u, _, v = np.linalg.svd(rotation)
    rotation = u @ v
    if not np.allclose(rotation * scale, linear, atol=1e-5, rtol=1e-4):
        raise ValueError(t('fab.unsupported', feature='animated shear'))
    # Stable conversion of a rotation matrix to glTF xyzw quaternion.
    trace = np.trace(rotation)
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        quaternion = np.array([(rotation[2, 1] - rotation[1, 2]) / s,
            (rotation[0, 2] - rotation[2, 0]) / s, (rotation[1, 0] - rotation[0, 1]) / s, s / 4])
    else:
        i = int(np.argmax(np.diag(rotation)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(max(0, 1 + rotation[i, i] - rotation[j, j] - rotation[k, k])) * 2
        quaternion = np.zeros(4)
        quaternion[i] = s / 4
        quaternion[j] = (rotation[i, j] + rotation[j, i]) / s
        quaternion[k] = (rotation[i, k] + rotation[k, i]) / s
        quaternion[3] = (rotation[k, j] - rotation[j, k]) / s
    quaternion /= np.linalg.norm(quaternion)
    return {'translation': translation.tolist(), 'rotation': quaternion.tolist(), 'scale': scale.tolist()}


def animated_capture(capture, clips):
    """Keep source local vertices; each synthetic joint stores world * skin."""
    result = copy.deepcopy(capture)
    scene = {'nodes': [], 'roots': [], 'animations': []}
    bindings = []
    identity = np.eye(4).ravel(order='F').tolist()
    for mesh in result['meshes']:
        geometry, table = mesh['geometryID'], tuple(mesh['_fabTable'])
        node = len(scene['nodes'])
        mesh['node'] = node
        scene['nodes'].append({'name': mesh['name'], 'parent': None})
        scene['roots'].append(node)
        skin = mesh.get('_fabSkin')
        if skin:
            attrs = copy.deepcopy(mesh['attributes'])
            mesh['animatedAttributes'] = attrs
            attrs['Vertex']['data'] = skin['bindPositions']
            attrs['Normal']['data'] = skin['bindNormals']
            if skin['bindTangents'] is not None:
                attrs['Tangent']['data'] = skin['bindTangents']
            joints = np.asarray(skin['joints'], dtype=np.int64)
            weights = np.asarray(skin['weights'], dtype=np.float64)
            bones = sorted(map(int, np.unique(joints[weights > 0])))
            unweighted = weights.sum(axis=1) == 0
            if np.any(unweighted):
                bones.append(-1)
            palette = {bone: i for i, bone in enumerate(bones)}
            remapped = np.zeros_like(joints)
            for bone in bones:
                if bone >= 0:
                    remapped[joints == bone] = palette[bone]
            remapped[unweighted, 0] = palette.get(-1, 0)
            weights[unweighted, 0] = 1
            attrs['Bones'] = {'size': 4, 'data': remapped.ravel().tolist()}
            attrs['Weights'] = {'size': 4, 'data': weights.ravel().tolist()}
            nodes = []
            for bone in bones:
                index = len(scene['nodes'])
                target = f'fab-{geometry}-joint-{bone}-{mesh["sourceID"]}'
                scene['nodes'].append({'name': target, 'target': target, 'parent': None})
                scene['roots'].append(index)
                nodes.append(index)
                bindings.append((index, table, geometry, bone))
            mesh['skin'] = {'joints': nodes, 'inverseBind': [identity] * len(nodes), 'matrixFromSkeletonToGeometry': identity}
        else:
            target = f'fab-{geometry}-object-{mesh["sourceID"]}'
            scene['nodes'][node]['target'] = target
            bindings.append((node, table, geometry, None))
    # Give importers an explicit common bind-space root. Without it Blender
    # guesses each flat joint's first animated pose as its rest transform and
    # retargets blended vertices, changing subsequent deformation.
    for mesh in result['meshes']:
        if 'skin' in mesh:
            mesh['skin']['skeleton'] = len(scene['nodes'])
    for clip_index, clip in enumerate(clips):
        animation = {'uid': clip['uid'], 'name': clip['name'], 'duration': clip['duration'], 'firstKeyTime': 0, 'channels': []}
        poses = [pose_tables(frame) for frame in clip['frames']]
        for index, table, geometry, bone in bindings:
            values = []
            for pose in poses:
                current = pose[table]
                matrix = current['world'][geometry]
                if bone is not None and bone >= 0:
                    matrix = matrix @ current['skin'][bone]
                values.append(decompose(matrix))
            if clip_index == 0:
                scene['nodes'][index]['trs'] = values[0]
            for path in ('translation', 'rotation', 'scale'):
                data = np.asarray([value[path] for value in values])
                if path == 'rotation':
                    for i in range(1, len(data)):
                        if np.dot(data[i - 1], data[i]) < 0:
                            data[i] *= -1
                times = clip['times']
                if np.allclose(data, data[0], atol=1e-7):
                    times = [times[0], times[-1]]
                    data = data[[0, -1]]
                animation['channels'].append({'target': scene['nodes'][index]['target'], 'path': path,
                    'times': times, 'values': data.ravel().tolist()})
        scene['animations'].append(animation)
    result['scene'] = scene
    result['animationBake'] = {'fps': 30, 'rig': 'synthetic flat world-space deformation joints',
        'originalBoneNames': False, 'originalHierarchy': False}
    return result
