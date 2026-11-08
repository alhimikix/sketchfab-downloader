"""Static viewer geometry -> portable GLB and OBJ. No Blender dependency."""
from __future__ import annotations
import copy
import json
import re
import struct
from pathlib import Path
import numpy as np
from i18n import t


def triangles(indices, mode):
    ids = np.asarray(indices, dtype=np.uint32)
    if mode == 4:
        if len(ids) % 3:
            raise ValueError('Triangle index count is not divisible by three')
        faces = ids.reshape(-1, 3)
    elif mode == 5:
        if len(ids) < 3:
            return np.empty((0, 3), dtype=np.uint32)
        faces = np.column_stack((ids[:-2], ids[1:-1], ids[2:])).copy()
        faces[1::2] = faces[1::2, [1, 0, 2]]
    elif mode == 6:
        faces = np.column_stack((np.full(max(0, len(ids)-2), ids[0]), ids[1:-1], ids[2:])) if len(ids) else np.empty((0,3),dtype=np.uint32)
    else:
        raise ValueError(f'Unsupported primitive mode {mode}; only triangle meshes are exported')
    return faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]


def unit_vectors(values):
    lengths = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(lengths < 1e-12):
        raise ValueError('Zero length normal/tangent in source geometry')
    return values / lengths


def prepare_meshes(capture, material_ids, warnings=None, basis_transform=None):
    result = []
    # Sketchfab scene is Z-up. glTF is Y-up, right handed.
    basis = np.array([[1,0,0,0], [0,0,1,0], [0,-1,0,0], [0,0,0,1]], dtype=np.float64)
    if basis_transform is not None:basis=np.asarray(basis_transform,dtype=np.float64)
    for source in capture['meshes']:
        attrs = source['attributes']
        if attrs['Vertex']['size'] != 3:
            continue
        vertices = np.asarray(attrs['Vertex']['data'],dtype=np.float64).reshape(-1,3)
        if not len(vertices):
            raise ValueError('Empty geometry')
        # The viewer adds line meshes for its wireframe inspector under the
        # same scene root; only source triangle surfaces belong in this export.
        parts = [triangles(p['indices'],p['mode']) for p in source['primitives'] if p['mode'] in (4,5,6)]
        faces = np.concatenate(parts) if parts else np.empty((0,3),dtype=np.uint32)
        if not len(faces):
            continue
        if faces.max() >= len(vertices):
            raise ValueError(f'Out-of-range vertex index in {source["name"]}')
        material = material_ids.get(str(source.get('material')))
        if material is None:
            raise ValueError(f'Unknown material {source.get("material")} in {source["name"]}')
        for instance, matrix in enumerate(source['matrices']):
            transform = basis @ np.asarray(matrix,dtype=np.float64).reshape(4,4,order='F')
            rotation = transform[:3,:3]
            positions = vertices @ rotation.T + transform[:3,3]
            mesh = {'sourceID':source.get('sourceID'),'name':source['name'] + (f' [{instance+1}]' if instance else ''), 'material':material,
                    'POSITION':positions.astype('<f4'), 'faces':faces.copy()}
            if 'Color' in attrs:
                color = np.asarray(attrs['Color']['data'], dtype='<f4').reshape(-1, attrs['Color']['size'])
                if color.shape[1] not in (3, 4) or len(color) != len(vertices):
                    raise ValueError('Invalid vertex color attribute')
                mesh['COLOR_0'] = color
            if np.linalg.det(rotation) < 0:
                mesh['faces'] = mesh['faces'][:,[0,2,1]]
            if 'Normal' in attrs:
                normals = np.asarray(attrs['Normal']['data']).reshape(-1,3)
                mesh['NORMAL'] = unit_vectors(normals @ np.linalg.inv(rotation)).astype('<f4')
            for key, value in attrs.items():
                if key.startswith('TexCoord'):
                    uv = np.asarray(value['data'],dtype='<f4').reshape(-1,value['size'])[:,:2].copy()
                    uv[:,1] = 1 - uv[:,1]
                    mesh['TEXCOORD_' + key[8:]] = uv
            uv_sets=[int(key[9:]) for key in mesh if key.startswith('TEXCOORD_')]
            if uv_sets:
                # glTF requires contiguous UV attribute indices. The viewer
                # can supply only UV1; keep it and fill unused lower slots.
                reference=mesh['TEXCOORD_'+str(min(uv_sets))]
                for uv_index in range(max(uv_sets)+1):
                    mesh.setdefault('TEXCOORD_'+str(uv_index),reference.copy())
            if 'Tangent' in attrs and 'NORMAL' in mesh:
                tangent = np.asarray(attrs['Tangent']['data'],dtype=np.float64).reshape(-1,4)
                xyz = tangent[:,:3] @ rotation.T
                normal = mesh['NORMAL'].astype(np.float64)
                xyz -= np.sum(xyz*normal,axis=1,keepdims=True)*normal
                if np.any(np.linalg.norm(xyz,axis=1)<1e-10) or np.any(tangent[:,3]==0):
                    # Collinear source normals/tangents cannot form a basis.
                    # Omitting this optional accessor lets glTF consumers derive
                    # tangents from the UVs when a normal map requires them.
                    if warnings is not None:
                        warnings.append(t('export.bad_tangents',name=mesh['name']))
                else:
                    xyz = unit_vectors(xyz)
                    w = np.sign(tangent[:,3]) * np.sign(np.linalg.det(rotation))
                    mesh['TANGENT'] = np.column_stack((xyz,w)).astype('<f4')
            for key,value in mesh.items():
                if isinstance(value,np.ndarray) and not np.all(np.isfinite(value)):
                    raise ValueError(f'Nonfinite {key} in {source["name"]}')
                if key in ('NORMAL','TANGENT') or key.startswith('TEXCOORD_'):
                    if len(value) != len(vertices):
                        raise ValueError(f'Attribute count mismatch for {key}')
            result.append(mesh)
    if not result:
        raise ValueError('No triangle meshes found')
    return result


def validate_export_counts(capture, meshes, expected):
    """Check export against viewer geometry; API counts are source metadata.

    The processed viewer scene may legitimately differ from the public model
    counter. No arbitrary percentage tolerance is used to hide lost surfaces.
    """
    audit = capture.get('audit')
    if audit is None:
        # Allows recovery of captures saved by 1.0.0 before it aborted.
        source_count=source_meshes=0
        for source in capture['meshes']:
            count=sum(len(triangles(p['indices'],p['mode'])) for p in source['primitives'] if p['mode'] in (4,5,6))
            instances=len(source['matrices'])
            if count and not instances:
                raise ValueError('Surface geometry has no world transform')
            source_count+=count*instances
            if count:source_meshes+=instances
        audit={'surfaceInstances':source_meshes,'renderableTriangles':source_count,'source':'saved-viewer-scene'}
    count=sum(len(mesh['faces']) for mesh in meshes)
    if count!=audit['renderableTriangles'] or len(meshes)!=audit['surfaceInstances']:
        raise ValueError(t('export.incomplete',scene_count=audit['renderableTriangles'],
                           scene_parts=audit['surfaceInstances'],count=count,parts=len(meshes)))
    result=dict(audit,exportedTriangles=count,exportedSurfaceInstances=len(meshes),matchesViewer=True,
                metadataTriangles=expected,metadataMatches=(count==expected) if expected is not None else None)
    warnings=[]
    if expected is not None and count!=expected:
        warnings.append(t('export.count_difference',expected=expected,count=count))
    return result,warnings


def write_glb(meshes, materials, output_dir, attribution, scene=None):
    doc = {'asset':{'version':'2.0','generator':'Sketchfab Local Export','copyright':attribution},
           'scene':0,'scenes':[{'nodes':[]}], 'nodes':[], 'meshes':[], 'bufferViews':[], 'accessors':[]}
    for key in ('materials','images','textures','samplers','extensionsUsed'):
        if materials.get(key):
            doc[key] = copy.deepcopy(materials[key])
    if scene:
        from animation_export import scene_nodes
        doc['nodes'],doc['scenes'][0]['nodes']=scene_nodes(scene)
    binary = bytearray()
    def view(data, target=None):
        binary.extend(b'\0' * (-len(binary)%4))
        entry = {'buffer':0,'byteOffset':len(binary),'byteLength':len(data)}
        if target:
            entry['target'] = target
        binary.extend(data)
        doc['bufferViews'].append(entry)
        return len(doc['bufferViews'])-1
    def accessor(data, bounds=False, indices=False, component_type=None, kind=None, buffer_target=None):
        component_type=component_type or (5125 if indices else 5126)
        data = np.ascontiguousarray(data,dtype={5125:'<u4',5126:'<f4',5123:'<u2'}[component_type])
        n = 1 if data.ndim == 1 else data.shape[1]
        a = {'bufferView':view(data.tobytes(),buffer_target if buffer_target is not None else (34963 if indices else 34962)),'componentType':component_type,
             'count':len(data), 'type':kind or ('SCALAR' if n==1 else f'VEC{n}')}
        if bounds:
            a['min'] = np.atleast_1d(data.min(axis=0)).tolist()
            a['max'] = np.atleast_1d(data.max(axis=0)).tolist()
        doc['accessors'].append(a)
        return len(doc['accessors'])-1
    for mesh in meshes:
        attributes = {key:accessor(value,bounds=key=='POSITION',component_type=5123 if key=='JOINTS_0' else 5126) for key,value in mesh.items()
                      if key in ('POSITION','NORMAL','TANGENT','JOINTS_0','WEIGHTS_0','COLOR_0') or key.startswith('TEXCOORD_')}
        primitive = {'attributes':attributes,'indices':accessor(mesh['faces'].ravel(),indices=True), 'material':mesh['material'],'mode':4}
        index = len(doc['meshes'])
        gltf_mesh={'name':mesh['name'],'primitives':[primitive]}
        if mesh.get('morph_targets'):
            primitive['targets']=[{key:accessor(value,bounds=key=='POSITION') for key,value in target.items()} for target in mesh['morph_targets']]
            gltf_mesh['weights']=mesh['morph_weights']
            gltf_mesh['extras']={'targetNames':mesh['morph_names'],
                'morphBindings':mesh['morph_bindings']}
        doc['meshes'].append(gltf_mesh)
        if scene:
            node=doc['nodes'][mesh['scene_node']];node['mesh']=index
            if 'skin'in mesh:
                from animation_export import append_skin
                node['skin']=append_skin(doc,mesh,accessor)
                # Skin vertices are placed by global joint matrices. Parent
                # transforms are already represented by the bind matrices.
                for parent in doc['nodes']:
                    if mesh['scene_node'] in parent.get('children',[]):
                        parent['children'].remove(mesh['scene_node'])
                        if not parent['children']:parent.pop('children')
                for key in ('matrix','translation','rotation','scale'):node.pop(key,None)
                doc['scenes'][0]['nodes'].append(mesh['scene_node'])
        else:
            doc['nodes'].append({'name':mesh['name'],'mesh':index})
            doc['scenes'][0]['nodes'].append(index)
    if scene:
        from animation_export import append_animations
        append_animations(doc,scene,accessor)
    for img in doc.get('images',[]):
        filename = output_dir / img.pop('uri')
        img['bufferView'] = view(filename.read_bytes())
        img['mimeType'] = 'image/png' if filename.suffix.lower()=='.png' else 'image/jpeg'
    doc['buffers'] = [{'byteLength':len(binary)}]
    header = json.dumps(doc,separators=(',',':'),ensure_ascii=True).encode()
    header += b' ' * (-len(header)%4)
    binary.extend(b'\0' * (-len(binary)%4))
    glb = struct.pack('<4sII',b'glTF',2,28+len(header)+len(binary))
    glb += struct.pack('<I4s',len(header),b'JSON') + header
    glb += struct.pack('<I4s',len(binary),b'BIN\0') + binary
    target = output_dir/'model.glb'
    target.write_bytes(glb)
    return target


def write_obj(meshes, materials, output_dir, attribution):
    def texture(info):
        t = materials['textures'][info['index']]
        return materials['images'][t['source']]['uri']
    mtl = ['# '+attribution]
    for i,mat in enumerate(materials['materials']):
        pbr = mat.get('pbrMetallicRoughness',{})
        color = pbr.get('baseColorFactor',[1,1,1,1])
        mtl += [f'newmtl material_{i}', 'Kd '+' '.join(map(str,color[:3])), 'd '+str(color[3]), 'illum 2',
                'Pr '+str(pbr.get('roughnessFactor',1)), 'Pm '+str(pbr.get('metallicFactor',0))]
        if pbr.get('baseColorTexture'):
            mtl.append('map_Kd '+texture(pbr['baseColorTexture']))
            if mat.get('alphaMode') in ('MASK','BLEND'):
                mtl.append('map_d -imfchan a '+texture(pbr['baseColorTexture']))
        if mat.get('normalTexture'):
            mtl.append('norm '+texture(mat['normalTexture']))
        if mat.get('emissiveTexture'):
            mtl += ['Ke '+' '.join(map(str,mat.get('emissiveFactor',[1,1,1]))), 'map_Ke '+texture(mat['emissiveTexture'])]
        mtl.append('')
    (output_dir/'model.mtl').write_text('\n'.join(mtl),encoding='utf-8')
    with (output_dir/'model.obj').open('w',encoding='utf-8',newline='\n') as out:
        out.write('# '+attribution+'\nmtllib model.mtl\n')
        vi=ti=ni=1
        for i,mesh in enumerate(meshes):
            name = re.sub(r'[^\w.-]+','_',mesh['name'])
            out.write(f'o {i+1}_{name}\nusemtl material_{mesh["material"]}\n')
            for xyz in mesh['POSITION']:
                out.write('v %.9g %.9g %.9g\n'%tuple(xyz))
            uv = mesh.get('TEXCOORD_0')
            normals = mesh.get('NORMAL')
            if uv is not None:
                for u,v in uv:
                    out.write('vt %.9g %.9g\n'%(u,1-v))
            if normals is not None:
                for n in normals:
                    out.write('vn %.9g %.9g %.9g\n'%tuple(n))
            for face in mesh['faces']:
                refs=[]
                for idx in map(int,face):
                    refs.append(f'{idx+vi}/{idx+ti}/{idx+ni}' if uv is not None and normals is not None else
                                f'{idx+vi}//{idx+ni}' if normals is not None else f'{idx+vi}/{idx+ti}' if uv is not None else str(idx+vi))
                out.write('f '+' '.join(refs)+'\n')
            vi+=len(mesh['POSITION'])
            ti+=len(uv) if uv is not None else 0
            ni+=len(normals) if normals is not None else 0
