"""Executed by Blender, not the host interpreter. See blender_create.py."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import sys

import bpy
from mathutils import Quaternion, Vector


def glb_metadata(path):
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b'glTF':
            raise RuntimeError('Input is not a binary glTF file')
        version, length = struct.unpack('<II', header[4:])
        if version != 2 or length != path.stat().st_size:
            raise RuntimeError('Invalid GLB version or length')
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise RuntimeError('GLB has no JSON chunk')
        size, kind = struct.unpack('<II', chunk)
        if kind != 0x4E4F534A or size > length - 20:
            raise RuntimeError('GLB JSON chunk is invalid')
        return json.loads(stream.read(size))


def animation_owners():
    """All imported animation ID owners, including morphs and material nodes."""
    seen = set()
    for collection_name in ('objects', 'shape_keys', 'materials', 'cameras', 'lights', 'worlds', 'scenes'):
        for datablock in getattr(bpy.data, collection_name, ()):
            for owner in (datablock, getattr(datablock, 'node_tree', None)):
                if owner is not None and owner.as_pointer() not in seen:
                    seen.add(owner.as_pointer())
                    if getattr(owner, 'animation_data', None):
                        yield owner


def action_curves(action):
    """Support both legacy and Blender 4.4+ slotted actions."""
    seen = set()
    for curve in getattr(action, 'fcurves', ()):
        seen.add(curve.as_pointer())
        yield curve
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for channelbag in getattr(strip, 'channelbags', ()):
                for curve in channelbag.fcurves:
                    if curve.as_pointer() not in seen:
                        seen.add(curve.as_pointer())
                        yield curve


def inventory():
    actions = []
    for action in bpy.data.actions:
        curves = list(action_curves(action))
        actions.append({'name': action.name, 'frame_range': list(action.frame_range),
                        'slots': len(getattr(action, 'slots', ())), 'curves': len(curves),
                        'keyframes': sum(len(curve.keyframe_points) for curve in curves),
                        'fake_user': action.use_fake_user})
    owners = []
    for owner in animation_owners():
        data = owner.animation_data
        tracks = []
        for track in data.nla_tracks:
            tracks.append({'name': track.name, 'mute': track.mute,
                'strips': [{'name': strip.name, 'action': strip.action.name if strip.action else None,
                            'action_slot': getattr(getattr(strip, 'action_slot', None), 'identifier', None),
                            'frame_range': [strip.frame_start, strip.frame_end]}
                           for strip in track.strips]})
        owners.append({'name': owner.name, 'type': owner.id_type,
                       'action': data.action.name if data.action else None,
                       'action_slot': getattr(getattr(data, 'action_slot', None), 'identifier', None),
                       'tracks': tracks})
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    armatures = [obj for obj in bpy.data.objects if obj.type == 'ARMATURE']
    images = [image for image in bpy.data.images if image.type == 'IMAGE']
    return {'objects': len(bpy.data.objects), 'meshes': len(meshes),
            'vertices': sum(len(obj.data.vertices) for obj in meshes),
            'polygons': sum(len(obj.data.polygons) for obj in meshes),
            'materials': len(bpy.data.materials), 'images': len(images),
            'packed_images': sum(bool(image.packed_file or image.packed_files) for image in images),
            'armatures': len(armatures), 'bones': sum(len(obj.data.bones) for obj in armatures),
            'shape_keys': sum(max(0, len(keys.key_blocks) - 1) for keys in bpy.data.shape_keys),
            'actions': len(actions), 'action_details': actions, 'animation_owners': owners,
            'nla_tracks': sum(len(owner['tracks']) for owner in owners),
            'nla_strips': sum(len(track['strips']) for owner in owners for track in owner['tracks']),
            'timeline': {'start': bpy.context.scene.frame_start, 'end': bpy.context.scene.frame_end,
                         'current': bpy.context.scene.frame_current, 'fps': bpy.context.scene.render.fps,
                         'fps_base': bpy.context.scene.render.fps_base}}


def verify_original_skeleton(source):
    expected={i:node for i,node in enumerate(source['nodes'])
              if node.get('extras',{}).get('sourceNodeType')=='osgAnimation.Bone'}
    if not expected:return None
    parents={child:i for i,node in enumerate(source['nodes'])for child in node.get('children',[])}
    actual={}
    for obj in bpy.data.objects:
        if obj.type!='ARMATURE':continue
        for bone in obj.data.bones:
            if bone.get('sourceNodeType')=='osgAnimation.Bone':
                actual[bone.get('sourceNodeID')]=bone
    for index,node in expected.items():
        uid=node['extras']['sourceNodeID'];bone=actual.get(uid)
        if bone is None or bone.get('sourceBoneName')!=node['name']:
            raise RuntimeError('An original preview bone or its full name was lost during Blender import')
        if len(node['name'].encode('utf-8'))<=63 and bone.name!=node['name']:
            raise RuntimeError('Blender changed an original preview bone name')
        parent=parents.get(index)
        while parent is not None and parent not in expected:parent=parents.get(parent)
        expected_parent=expected[parent]['extras']['sourceNodeID']if parent is not None else None
        parent_bone=bone.parent
        while parent_bone and parent_bone.get('sourceNodeType')!='osgAnimation.Bone':parent_bone=parent_bone.parent
        actual_parent=parent_bone.get('sourceNodeID')if parent_bone else None
        if actual_parent!=expected_parent:raise RuntimeError('Blender changed the original preview bone hierarchy')
    if len(actual)!=len(expected):raise RuntimeError('Original preview bone count changed during Blender import')
    return {'bones':len(expected),'names_preserved':True,'parents_preserved':True,
            'full_names_property':'sourceBoneName'}


def select_animation(source):
    # The core glTF importer already activates the first clip and stashes every
    # imported clip as muted NLA tracks. Keep those assignments when present.
    owners = list(animation_owners())
    if not any(owner.animation_data.action for owner in owners):
        names = [animation.get('name') for animation in source.get('animations', [])]
        available = [track.name for owner in owners for track in owner.animation_data.nla_tracks if track.strips]
        selected = next((name for name in names if name in available), available[0] if available else None)
        if selected:
            for owner in owners:
                for track in owner.animation_data.nla_tracks:
                    if track.name == selected and track.strips and track.strips[0].action:
                        strip = track.strips[0]
                        owner.animation_data.action = strip.action
                        if hasattr(strip, 'action_slot') and hasattr(owner.animation_data, 'action_slot'):
                            owner.animation_data.action_slot = strip.action_slot
                        break
    # A fake user retains even an unassigned clip when reopening the saved file.
    for action in bpy.data.actions:
        action.use_fake_user = True
    ranges = [list(action.frame_range) for action in bpy.data.actions
              if all(math.isfinite(value) for value in action.frame_range)]
    scene = bpy.context.scene
    if ranges:
        scene.frame_start = max(-1048574, math.floor(min(pair[0] for pair in ranges)))
        scene.frame_end = max(scene.frame_start + 1, min(1048574, math.ceil(max(pair[1] for pair in ranges))))
    else:
        scene.frame_start, scene.frame_end = 1, 250
    scene.frame_set(scene.frame_start)


def prepare_viewport():
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
    points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    if points:
        low = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
        high = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
        center = (low + high) * 0.5
        diameter = max((high - low).length, 0.001)
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    space = area.spaces.active
                    space.shading.type = 'MATERIAL'
                    space.clip_start = max(diameter / 10000, 0.00001)
                    space.clip_end = max(diameter * 100, 100)
                    space.region_3d.view_location = center
                    space.region_3d.view_distance = diameter * 1.2
                    space.region_3d.view_rotation = Quaternion((0.8733, 0.4545, 0.0832, 0.1518)).normalized()
                    space.region_3d.view_perspective = 'PERSP'
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    selected = armatures[0] if armatures else (meshes[0] if meshes else None)
    if selected:
        selected.select_set(True)
        bpy.context.view_layer.objects.active = selected


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(arguments)
    source_path, output_path, report_path = args.source.resolve(), args.output.resolve(), args.report.resolve()
    if output_path.exists() or report_path.exists():
        raise FileExistsError('Blender output already exists; refusing to replace it')
    source = glb_metadata(source_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.context.scene.render.fps = 30
    bpy.context.scene.render.fps_base = 1.0
    importer_options = {'filepath': str(source_path)}
    available_options = bpy.ops.import_scene.gltf.get_rna_type().properties.keys()
    for key, value in [('import_pack_images', True), ('merge_vertices', False), ('import_shading', 'NORMALS'),
                       ('import_scene_as_collection', True), ('import_select_created_objects', True)]:
        if key in available_options:
            importer_options[key] = value
    if any(node.get('extras',{}).get('sourceNodeType')=='osgAnimation.Bone' for node in source['nodes']):
        for key,value in [('bone_heuristic','BLENDER'),('guess_original_bind_pose',True)]:
            if key in available_options:importer_options[key]=value
    status = bpy.ops.import_scene.gltf(**importer_options)
    if 'FINISHED' not in status:
        raise RuntimeError(f'glTF importer did not complete: {status}')
    original_skeleton=verify_original_skeleton(source)
    for image in bpy.data.images:
        if image.type == 'IMAGE':
            if not image.size[0] or not image.size[1]:
                raise RuntimeError(f'Image failed to load: {image.name}')
            image.pack()
    select_animation(source)
    prepare_viewport()
    before = inventory()
    expected_shapes=sum(max((len(p.get('targets',[])) for p in mesh['primitives']),default=0)
                        for mesh in source['meshes'])
    if before['shape_keys']<expected_shapes:
        raise RuntimeError('Some GLB morph targets were lost during Blender import')
    nonempty_clips = [animation for animation in source.get('animations', []) if animation.get('channels')]
    if nonempty_clips and not before['actions']:
        raise RuntimeError('GLB contains animations, but Blender imported no actions')
    warnings = []
    if len(nonempty_clips) > before['actions']:
        warnings.append('There are fewer imported actions than nonempty GLB clips; inspect animation names in the report.')
    if before['images'] != before['packed_images']:
        raise RuntimeError('Some model images could not be packed into the Blender file')
    status = bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False, compress=False)
    if 'FINISHED' not in status or not output_path.is_file() or output_path.stat().st_size < 12:
        raise RuntimeError('Blender did not save a nonempty project')
    bpy.ops.wm.open_mainfile(filepath=str(output_path), load_ui=False)
    after = inventory()
    if verify_original_skeleton(source)!=original_skeleton:
        raise RuntimeError('Original preview skeleton verification failed after reopening')
    if before != after:
        changed = [key for key in before if before[key] != after[key]]
        raise RuntimeError(f'Project verification failed after reopening: {changed}')
    report = dict(after, blender_version=bpy.app.version_string, source=str(source_path),
                  path=str(output_path), verified_after_reopen=True,
                  source_animations=len(source.get('animations', [])),
                  source_animation_names=[animation.get('name', f'Animation {index + 1}')
                                          for index, animation in enumerate(source.get('animations', []))],
                  warnings=warnings)
    report['original_skeleton']=original_skeleton
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('BLENDER_EXPORT_OK', json.dumps({'actions': after['actions'], 'armatures': after['armatures'],
                                         'meshes': after['meshes'], 'packed_images': after['packed_images']}), flush=True)


if __name__ == '__main__':
    main()
