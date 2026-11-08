import copy
from pathlib import Path
import struct
import subprocess
import unittest

import numpy as np

from fab_native_animation import decode_channel, read_array
from fab_native_rig import preserve_rig, source_scene


def identity():return np.eye(4).ravel(order='F').tolist()


def transform(x):
    result=np.eye(4);result[0,3]=x;return result.ravel(order='F').tolist()


def callback(name,x):
    return [{'osgAnimation.UpdateBone':{'Name':name,'StackedTransforms':[
        {'osgAnimation.StackedTranslate':{'Translate':[x,0,0]}},
        {'osgAnimation.StackedQuaternion':{'Quaternion':[0,0,0,1]}},
        {'osgAnimation.StackedScale':{'Scale':[1,1,1]}}]}}]


def original_scene():
    tip={'UniqueID':5,'Name':'Tip','Matrix':transform(1),'InvBindMatrixInSkeletonSpace':transform(-1),
         'UpdateCallbacks':callback('tip-target',1)}
    bone={'UniqueID':4,'Name':'Root','Matrix':identity(),'InvBindMatrixInSkeletonSpace':identity(),
          'UpdateCallbacks':callback('root-target',0),'Children':[{'osgAnimation.Bone':tip}]}
    rig={'UniqueID':3,'BoneMap':{'Tip':1,'Root':0},'SourceGeometry':{'osg.Geometry':{
        'UniqueID':30,'Name':'surface','PrimitiveSetList':[{'DrawElementsUInt':{'Mode':'TRIANGLES'}}]}}}
    return {'Generator':'Synthetic scene','osg.Node':{'UniqueID':1,'Children':[{'osgAnimation.Skeleton':{
        'UniqueID':2,'Name':'Rig','Children':[{'osgAnimation.RigGeometry':rig},{'osgAnimation.Bone':bone}]}}]}}


class NativeFabTests(unittest.TestCase):
    def test_stream_capture_handles_partial_allocator_pages_and_utf8(self):
        from playwright._impl._driver import compute_driver_executable
        from platform_support import external_process_environment
        node,_=compute_driver_executable()
        result=subprocess.run([str(node),str(Path(__file__).with_name('test_fab_rig_capture.js'))],
            capture_output=True,text=True,timeout=30,env=external_process_environment())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('RIG_CAPTURE_TEST_OK',result.stdout)

    def test_original_bones_parents_bind_and_clip_defaults_are_retained(self):
        document=original_scene();source=source_scene(document)
        self.assertEqual([source['nodes'][i]['name']for i in source['bones']],['Root','Tip'])
        self.assertEqual(len(source['slots']),2)
        mesh={'sourceID':0,'name':'surface','material':'a','geometryID':0,'_fabTable':[8,0,0,0,0,4,2,1,1],
              'attributes':{'Vertex':{'size':3,'data':[9]*9},'Normal':{'size':3,'data':[0,0,1]*3}},
              'primitives':[{'mode':4,'indices':[0,1,2]}],'matrices':[identity()],
              '_fabSkin':{'joints':[[0,0,0,0],[1,0,0,0],[1,0,0,0]],'weights':[[1,0,0,0]]*3,
                'bindPositions':[0,0,0,1,0,0,0,1,0],'bindNormals':[0,0,1]*3,'bindTangents':None}}
        clips=[{'uid':'move','name':'Move','firstKeyTime':0,'lastKeyTime':1,'duration':1,'channels':[
            {'target':'tip-target','path':'translate','times':[0,1],'values':[1,0,0,2,0,0]}]},
            {'uid':'turn','name':'Turn','firstKeyTime':0,'lastKeyTime':1,'duration':1,'channels':[
            {'target':'tip-target','path':'rotate','times':[0,1],'values':[0,0,0,1,0,0,1,0]}]}]
        capture=preserve_rig({'meshes':[mesh]},document,clips)
        nodes=capture['scene']['nodes'];skin=capture['meshes'][0]['skin']
        self.assertEqual([nodes[i]['name']for i in skin['joints']],['Root','Tip'])
        self.assertEqual(nodes[skin['joints'][1]]['parent'],skin['joints'][0])
        self.assertEqual(skin['inverseBind'][1],transform(-1))
        self.assertEqual(capture['meshes'][0]['animatedAttributes']['Vertex']['data'],mesh['_fabSkin']['bindPositions'])
        reset=next(c for c in capture['scene']['animations'][1]['channels']if c['path']=='translation')
        self.assertEqual(reset['values'],[1,0,0,1,0,0])
        self.assertEqual(capture['scene']['animations'][1]['sourceChannelCount'],1)
        self.assertTrue(capture['rigPreservation']['originalHierarchy'])
        static=preserve_rig({'meshes':[mesh]},document,[])
        self.assertEqual(static['scene']['animations'],[])
        self.assertEqual(len(static['meshes'][0]['skin']['joints']),2)
        attached=copy.deepcopy(mesh);attached.pop('_fabSkin');attached['sourceVertexCount']=3
        attached['sourceID']=1;attached['name']='rigid accessory'
        original=original_scene()
        tip=original['osg.Node']['Children'][0]['osgAnimation.Skeleton']['Children'][1]['osgAnimation.Bone']['Children'][0]['osgAnimation.Bone']
        tip['Children']=[{'osg.Geometry':{'UniqueID':90,'Name':'accessory','PrimitiveSetList':[{'DrawElementsUInt':{'Mode':'TRIANGLES'}}],
            'VertexAttributeList':{'Vertex':{'Array':{'Float32Array':{'Size':3}},'ItemSize':3}}}}]
        fixed=preserve_rig({'meshes':[mesh,attached]},original,[])
        leaf=fixed['scene']['nodes'][fixed['meshes'][1]['node']]
        attachment=fixed['scene']['nodes'][leaf['parent']]
        self.assertEqual(attachment['name'],'accessory')
        self.assertEqual(fixed['scene']['nodes'][attachment['parent']]['name'],'Tip')
        broken=copy.deepcopy(mesh);broken['_fabTable'][5]=6
        with self.assertRaises(ValueError):preserve_rig({'meshes':[broken]},document,[])

    def test_packed_vec3_and_quaternion_decode_known_residuals(self):
        def descriptor(kind,size,width,offset,encoding=None):
            meta={'File':'clip.bin','Size':size,'Offset':offset}
            if encoding:meta['Encoding']=encoding
            return {'Array':{kind:meta},'ItemSize':width}
        def channel(metadata,key):
            return {'Name':'rotate','TargetName':'tip-target','KeyFrames':{
                'Key':key,'Time':descriptor('Float32Array',1,1,8)},'UserDataContainer':{
                'Values':[{'Name':k,'Value':str(v)}for k,v in metadata.items()]}}
        vec=channel({'channel_mode':21,'ox':1,'oy':2,'oz':3,'ot':0,'bx':0,'by':0,'bz':0,
            'hx':.5,'hy':.25,'hz':.5},descriptor('Uint16Array',1,3,0,'varint'))
        blob=bytes([2,4,6])+bytes(5)+struct.pack('<f',.25)
        result=decode_channel('osgAnimation.Vec3LerpChannelCompressedPacked',vec,{'clip.bin':blob},{})
        np.testing.assert_allclose(result['times'],[0,.25])
        np.testing.assert_allclose(result['values'],[[1,2,3],[2,3,6]])
        quat=channel({'channel_mode':28,'ox':0,'oy':0,'oz':0,'ow':1,'ot':0,'epsilon':.25,'nphi':720},
                     descriptor('Uint16Array',1,3,0,'varint'))
        blob=bytes([0,0,255,255,1])+bytes(3)+struct.pack('<f',1)
        result=decode_channel('osgAnimation.QuatSlerpChannelCompressedPacked',quat,{'clip.bin':blob},{})
        np.testing.assert_allclose(result['values'],[[0,0,0,1],[0,0,1,0]],atol=1e-6)
        np.testing.assert_array_equal(result['times'],[0,1])
        with self.assertRaises(ValueError):
            read_array(descriptor('Uint16Array',1,1,0,'varint'),{'clip.bin':b'\x80'},{})
        with self.assertRaises(ValueError):
            read_array(descriptor('Float32Array',5,4,8),{'clip.bin':b'\0'*8},{})


if __name__=='__main__':unittest.main()
