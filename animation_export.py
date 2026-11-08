"""Preserve native hierarchy, weighted skin bindings and keyframe tracks."""
import copy
import numpy as np
from i18n import t
from exporter import prepare_meshes

ANIMATION_PATHS={'translate':'translation','translation':'translation','position':'translation',
                 'rotate':'rotation','rotation':'rotation','quaternion':'rotation',
                 'scale':'scale','scaling':'scale'}

def filter_unbound_channels(scene,warnings):
    """The viewer explicitly marks pruned, unused rig controls with ID -1."""
    for clip in scene['animations']:
        channels=clip['channels']
        ignored=[c for c in channels if c.get('targetID')==-1]
        if ignored:
            clip['channels']=[c for c in channels if c.get('targetID')!=-1]
            clip['unboundChannelCount']=len(ignored)
            warnings.append(t('animation.unbound',name=clip['name'],count=len(ignored)))


def prepare_animated_meshes(capture,material_ids,warnings):
    source=copy.copy(capture)
    source['meshes']=[dict(g,attributes=g.get('animatedAttributes',g['attributes']),matrices=[np.eye(4).ravel().tolist()]) for g in capture['meshes']]
    meshes=prepare_meshes(source,material_ids,warnings,basis_transform=np.eye(4))
    original={g['sourceID']:g for g in source['meshes']}
    for mesh in meshes:
        g=original[mesh['sourceID']]
        mesh['scene_node']=g['node']
        if 'morph' in g:
            morph=g['morph']
            if not (len(morph['targets'])==len(morph['names'])==len(morph['weights'])==len(morph['bindings'])):
                raise ValueError('Morph target binding count mismatch')
            targets=[]
            for source_target in morph['targets']:
                target={}
                for source_key,path in [('Vertex','POSITION'),('Normal','NORMAL'),('Tangent','TANGENT')]:
                    if source_key not in source_target or path not in mesh:continue
                    attr=source_target[source_key]
                    value=np.asarray(attr['data'],dtype=np.float64).reshape(-1,attr['size'])[:,:3]
                    base=np.asarray(g['attributes'][source_key]['data'],dtype=np.float64).reshape(-1,g['attributes'][source_key]['size'])[:,:3]
                    if value.shape!=base.shape or not np.all(np.isfinite(value)):
                        raise ValueError('Invalid morph target attribute')
                    target[path]=(value-base).astype('<f4')
                if 'POSITION' not in target:raise ValueError('Morph target positions are missing')
                targets.append(target)
            mesh['morph_targets']=targets
            mesh['morph_names']=morph['names']
            mesh['morph_weights']=morph['weights']
            mesh['morph_bindings']=morph['bindings']
        if 'skin' in g:
            mesh['skin']=g['skin']
            joints=np.asarray(g['attributes']['Bones']['data']).reshape(-1,4)
            weights=np.asarray(g['attributes']['Weights']['data'],dtype=np.float64).reshape(-1,4)
            if np.any(joints!=np.floor(joints)) or np.any(joints<0) or np.max(joints)>=len(g['skin']['joints']):
                raise ValueError('Skin joint indices do not match the bone palette')
            total=weights.sum(axis=1,keepdims=True)
            if np.any(total<=0) or np.any(weights<0):raise ValueError('Invalid skin weights')
            joints=joints.copy()
            joints[weights==0]=0
            mesh['JOINTS_0']=joints.astype('<u2')
            mesh['WEIGHTS_0']=(weights/total).astype('<f4')
    return meshes


def scene_nodes(scene):
    nodes=[]
    for source in scene['nodes']:
        n={'name':source['name']}
        if source.get('type'):
            n['extras']={'sourceNodeType':source['type'],'sourceNodeID':source.get('sourceID')}
            if source['type']=='osgAnimation.Bone':n['extras']['sourceBoneName']=source['name']
        trs=source.get('trs',{})
        if trs:
            for key,raw in trs.items():
                if key=='bindmatrix':continue
                path=ANIMATION_PATHS.get(key)
                if path is None:raise ValueError(f'Unsupported transform component: {key}')
                if path:
                    value=np.asarray(raw,dtype=np.float64)
                    if path=='rotation':value=value/np.linalg.norm(value)
                    n[path]=value.tolist()
        elif source.get('matrix') is not None:n['matrix']=source['matrix']
        nodes.append(n)
    prefixes={}
    for i,source in enumerate(scene['nodes']):
        bind=source.get('trs',{}).get('bindmatrix')
        if bind is not None:
            # The viewer evaluates bindmatrix * T * R * S. glTF cannot keep a
            # static matrix and animated TRS on one node, so preserve the
            # bind transform as a fixed parent (including hinge/pivot offsets).
            if len(bind)!=16:raise ValueError('Invalid bind matrix')
            prefixes[i]=len(nodes)
            nodes.append({'name':source['name']+' bind','matrix':bind,'children':[i]})
    for i,source in enumerate(scene['nodes']):
        if source['parent'] is not None:nodes[source['parent']].setdefault('children',[]).append(prefixes.get(i,i))
    conversion=len(nodes)
    nodes.append({'name':'Z-up to glTF Y-up','matrix':[1,0,0,0,0,0,-1,0,0,1,0,0,0,0,0,1],'children':[prefixes.get(i,i) for i in scene['roots']]})
    return nodes,[conversion]


def append_skin(doc,mesh,accessor):
    source=mesh['skin']
    transform=np.asarray(source.get('matrixFromGeometryToSkeleton',source.get('matrixFromSkeletonToGeometry'))).reshape(4,4,order='F')
    matrices=[]
    for original in source['inverseBind']:
        inverse=np.asarray(original).reshape(4,4,order='F')@transform
        matrices.append(inverse.ravel(order='F'))
    skin={'joints':source['joints'],'inverseBindMatrices':accessor(np.asarray(matrices),kind='MAT4',buffer_target=0)}
    if 'skeleton' in source:skin['skeleton']=source['skeleton']
    skins=doc.setdefault('skins',[]);skins.append(skin)
    return len(skins)-1


def append_animations(doc,scene,accessor):
    target_map={}
    for i,n in enumerate(scene['nodes']):
        if n.get('target'):target_map[n['target']]=i
    animations=[]
    morph_nodes=[]
    for index,node in enumerate(doc.get('nodes',[])):
        if 'mesh' not in node:continue
        mesh=doc['meshes'][node['mesh']]
        if mesh.get('extras',{}).get('morphBindings'):
            morph_nodes.append((index,mesh))
    for clip in scene['animations']:
        animation={'name':clip['name'],'channels':[],'samplers':[]}
        origin=clip.get('firstKeyTime',min((min(c['times']) for c in clip['channels'] if c['times']),default=0))
        morph_channels=[]
        for channel in clip['channels']:
            if str(channel['path']).isdigit():
                if not any(any(morph_binding_matches(binding,channel) for binding in mesh['extras']['morphBindings']) for _,mesh in morph_nodes):
                    raise ValueError(f'Morph animation target is missing: {channel["target"]}')
                morph_channels.append(channel)
                continue
            if channel['path'] not in ANIMATION_PATHS:raise ValueError(f'Unsupported animation channel: {channel["path"]}')
            node=target_map.get(channel['target'])
            if node is None:raise ValueError(f'Animation target is missing: {channel["target"]}')
            path=ANIMATION_PATHS[channel['path']];width=4 if path=='rotation' else 3
            times=np.asarray(np.asarray(channel['times'],dtype=np.float64)-origin,dtype='<f4')
            values=np.asarray(channel['values'],dtype='<f4').reshape(-1,width)
            if len(times)!=len(values) or not len(times):raise ValueError('Animation key count mismatch')
            # The viewer permits duplicate time stamps; glTF requires strictly
            # increasing inputs. The last value at a time stamp is the key.
            keep=np.r_[times[1:]!=times[:-1],True]
            times,values=times[keep],values[keep]
            if np.any(np.diff(times)<=0):raise ValueError('Animation times are not increasing')
            if times[0]>0:times=np.r_[np.float32(0),times];values=np.vstack((values[0],values))
            if path=='rotation':
                values=values/np.linalg.norm(values,axis=1,keepdims=True)
                for i in range(1,len(values)):
                    if np.dot(values[i-1],values[i])<0:values[i]*=-1
            sampler=len(animation['samplers'])
            animation['samplers'].append({'input':accessor(times,bounds=True,buffer_target=0),'output':accessor(values,buffer_target=0),'interpolation':'LINEAR'})
            animation['channels'].append({'sampler':sampler,'target':{'node':node,'path':path}})
        for node,mesh in morph_nodes:
            tracks={}
            for index,binding in enumerate(mesh['extras']['morphBindings']):
                channels=[c for c in morph_channels if morph_binding_matches(binding,c)]
                if len(channels)>1:raise ValueError('Duplicate morph animation binding')
                if not channels:continue
                channel=channels[0]
                if channel.get('type',2)!=2:raise ValueError('Unsupported morph animation interpolation')
                times=np.asarray(channel['times'],dtype=np.float64)-origin
                values=np.asarray(channel['values'],dtype=np.float64)
                if values.ndim!=1 or len(times)!=len(values) or not len(times) or not np.all(np.isfinite(times)) or not np.all(np.isfinite(values)):
                    raise ValueError('Invalid morph animation keys')
                keep=np.r_[times[1:]!=times[:-1],True];times,values=times[keep],values[keep]
                if times[0]<0 or np.any(np.diff(times)<=0):raise ValueError('Morph animation times are not increasing')
                tracks[index]=(times,values)
            # Include defaults in every clip so switching actions resets shapes
            # that are animated only by a different clip. Union all key times:
            # each scalar curve remains exactly piecewise linear.
            end=max(float(clip.get('duration',0)),max((float(t[-1]) for t,_ in tracks.values()),default=0))
            times=np.unique(np.concatenate(([0,end],*[t for t,_ in tracks.values()]))).astype('<f4')
            values=np.tile(np.asarray(mesh['weights'],dtype='<f4'),(len(times),1))
            for index,(keys,weights) in tracks.items():values[:,index]=np.interp(times,keys,weights)
            sampler=len(animation['samplers'])
            animation['samplers'].append({'input':accessor(times,bounds=True,buffer_target=0),
                'output':accessor(values.ravel(),buffer_target=0),'interpolation':'LINEAR'})
            animation['channels'].append({'sampler':sampler,'target':{'node':node,'path':'weights'}})
        if animation['channels']:animations.append(animation)
    if animations:doc['animations']=animations


def morph_binding_matches(binding,channel):
    if binding['path']!=str(channel['path']):return False
    if binding.get('targetID') is not None and channel.get('targetID') is not None:
        return binding['targetID']==channel['targetID']
    return binding['target']==channel['target']
