import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import subprocess
import numpy as np
from playwright._impl._driver import compute_driver_executable
from platform_support import external_process_environment
from animation_export import append_animations, prepare_animated_meshes
from exporter import write_glb


class MorphTests(unittest.TestCase):
    def test_capture_uses_original_buffers_and_defaults_not_live_gpu_slots(self):
        node,_=compute_driver_executable()
        result=subprocess.run([str(node),str(Path(__file__).with_name('test_morph_capture.js'))],
            capture_output=True,text=True,timeout=30,env=external_process_environment())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('MORPH_CAPTURE_TEST_OK',result.stdout)

    def doc(self):
        return {'nodes':[{'mesh':0}], 'meshes':[{'weights':[.25,.75], 'extras':{'morphBindings':[
            {'target':'left','targetID':3,'path':'0'}, {'target':'right','targetID':4,'path':'1'}]}}]}

    def export(self, channels, duration=3):
        doc=self.doc(); arrays=[]
        def accessor(data,**kwargs):arrays.append(np.asarray(data));return len(arrays)-1
        append_animations(doc,{'nodes':[{}],'animations':[{'name':'Face','firstKeyTime':10,
            'duration':duration,'channels':channels}]},accessor)
        return doc,arrays

    def test_scalar_tracks_use_union_times_and_flat_scalar_output(self):
        doc,arrays=self.export([
            {'target':'left','targetID':3,'path':'0','type':2,'times':[10,11,11,13],'values':[0,.3,.5,1]},
            {'target':'right','targetID':4,'path':'1','type':2,'times':[10,12,13],'values':[1,0,.5]}])
        self.assertEqual(doc['animations'][0]['channels'],[{'sampler':0,'target':{'node':0,'path':'weights'}}])
        np.testing.assert_array_equal(arrays[0],[0,1,2,3])
        self.assertEqual(arrays[1].ndim,1)
        np.testing.assert_allclose(arrays[1].reshape(-1,2),[[0,1],[.5,.5],[.75,0],[1,.5]])

    def test_omitted_shapes_reset_to_defaults_and_hold_single_key(self):
        _,arrays=self.export([{'target':'left','targetID':3,'path':'0','times':[11],'values':[1]}])
        np.testing.assert_array_equal(arrays[0],[0,1,3])
        np.testing.assert_allclose(arrays[1].reshape(-1,2),[[1,.75]]*3)
        _,arrays=self.export([])
        np.testing.assert_allclose(arrays[1].reshape(-1,2),[[.25,.75]]*2)

    def test_missing_bindings_and_unsupported_interpolation_fail(self):
        for channel in [dict(target='left',targetID=9,path='0',times=[10],values=[0]),
                        dict(target='left',targetID=3,path='0',type=5,times=[10],values=[0])]:
            with self.assertRaises(ValueError):self.export([channel])

    def test_absolute_targets_become_deltas_and_named_glb_shapes(self):
        base=[0,0,0,1,0,0,0,1,0]; target=[0,0,2,1,0,0,0,1,0]
        g={'sourceID':1,'node':0,'name':'Face','material':'1','attributes':{'Vertex':{'size':3,'data':base}},
           'primitives':[{'mode':4,'indices':[0,1,2]}],'matrices':[np.eye(4).ravel().tolist()],
           'morph':{'targets':[{'Vertex':{'size':3,'data':target}}], 'names':['Blink'], 'weights':[0],
                    'bindings':[{'target':'Blink','path':'0','targetID':3}]}}
        meshes=prepare_animated_meshes({'meshes':[g]},{'1':0},[])
        np.testing.assert_array_equal(meshes[0]['morph_targets'][0]['POSITION'],[[0,0,2],[0,0,0],[0,0,0]])
        with TemporaryDirectory() as temp:
            path=write_glb(meshes,{'materials':[{}]},Path(temp),'test',
                scene={'nodes':[{'name':'Face','parent':None}],'roots':[0],'animations':[]})
            raw=path.read_bytes();size=int.from_bytes(raw[12:16],'little');doc=json.loads(raw[20:20+size])
        mesh=doc['meshes'][0]
        self.assertEqual(mesh['weights'],[0]);self.assertEqual(mesh['extras']['targetNames'],['Blink'])
        self.assertEqual(len(mesh['primitives'][0]['targets']),1)
        self.assertEqual(doc['accessors'][mesh['primitives'][0]['targets'][0]['POSITION']]['count'],3)


if __name__=='__main__':unittest.main()
