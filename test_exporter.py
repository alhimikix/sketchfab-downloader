import unittest
import numpy as np
from exporter import triangles, validate_export_counts, prepare_meshes
from materials import build_materials, select_texture_variant, bake_opacity, same_texture_mapping
from PIL import Image
from pathlib import Path
from tempfile import TemporaryDirectory
from sketchfab_local import model_uid


class ExportTests(unittest.TestCase):
    def test_quaternion_channel_is_exported_as_normalized_rotation(self):
        from animation_export import append_animations
        scene={'nodes':[{'target':'engine'}],'animations':[{'name':'gear','channels':[
            {'target':'engine','path':'quaternion','times':[0,1],'values':[0,0,0,2,0,0,1,1]}]}]}
        arrays=[]
        def accessor(data,**kwargs):arrays.append(np.asarray(data));return len(arrays)-1
        doc={};append_animations(doc,scene,accessor)
        self.assertEqual(doc['animations'][0]['channels'][0]['target']['path'],'rotation')
        np.testing.assert_allclose(np.linalg.norm(arrays[1],axis=1),1)

    def test_bind_matrix_stays_before_animated_quaternion(self):
        from animation_export import scene_nodes
        bind=np.eye(4);bind[0,3]=12
        scene={'nodes':[{'name':'engine','parent':None,'target':'engine','trs':{
            'bindmatrix':bind.ravel(order='F').tolist(),'quaternion':[0,0,0,2]},'matrix':None}],'roots':[0]}
        nodes,roots=scene_nodes(scene)
        self.assertEqual(nodes[0]['rotation'],[0,0,0,1])
        self.assertEqual(nodes[1]['matrix'][12],12)
        self.assertEqual(nodes[1]['children'],[0])
        self.assertEqual(nodes[roots[0]]['children'],[1])
    def test_clip_with_source_offset_starts_at_zero(self):
        from animation_export import append_animations
        scene={'nodes':[{'target':'bone'}],'animations':[{'name':'Idle','firstKeyTime':10,'channels':[
            {'target':'bone','path':'translate','times':[10,11],'values':[0,0,0,1,0,0]}]}]}
        arrays=[]
        def accessor(data,**kwargs):arrays.append(np.asarray(data));return len(arrays)-1
        result={};append_animations(result,scene,accessor)
        np.testing.assert_array_equal(arrays[0],[0,1])
    def test_uv1_without_uv0_stays_available_with_contiguous_attributes(self):
        g={'name':'uv1-only','material':'1','attributes':{
            'Vertex':{'size':3,'data':[0,0,0,1,0,0,0,1,0]},
            'TexCoord1':{'size':2,'data':[.1,.2,.3,.4,.5,.6]}},
            'primitives':[{'mode':4,'indices':[0,1,2]}],'matrices':[np.eye(4).ravel().tolist()]}
        mesh=prepare_meshes({'meshes':[g]},{'1':0})[0]
        np.testing.assert_allclose(mesh['TEXCOORD_1'],[[.1,.8],[.3,.6],[.5,.4]])
        np.testing.assert_array_equal(mesh['TEXCOORD_0'],mesh['TEXCOORD_1'])
    def test_only_explicitly_unbound_animation_controls_are_removed(self):
        from animation_export import filter_unbound_channels
        scene={'animations':[{'name':'clip','channels':[{'target':'control','targetID':-1},{'target':'bone','targetID':0},{'target':'legacy'}]}]}
        warnings=[];filter_unbound_channels(scene,warnings)
        self.assertEqual([c['target'] for c in scene['animations'][0]['channels']],['bone','legacy'])
        self.assertEqual(scene['animations'][0]['unboundChannelCount'],1)
        self.assertEqual(len(warnings),1)
    def test_opacity_is_linear_alpha_without_premultiplying_color(self):
        base=Image.new('RGBA',(2,1),(100,150,200,17))
        opacity=Image.new('RGB',(2,1));opacity.putdata([(0,0,0),(128,255,255)])
        result=bake_opacity(base,opacity,{'texture':{'internalFormat':'LUMINANCE'}})
        self.assertEqual(list(result.getdata()),[(100,150,200,0),(100,150,200,128)])
        inverted=bake_opacity(base,opacity,{'invert':True})
        self.assertEqual(list(inverted.getchannel('A').getdata()),[255,127])

    def test_alpha_channel_and_opacity_without_color_texture(self):
        opacity=Image.new('RGBA',(1,1),(10,20,30,64))
        result=bake_opacity(None,opacity,{'texture':{'internalFormat':'ALPHA'}})
        self.assertEqual(result.getpixel((0,0)),(255,255,255,64))
        self.assertTrue(same_texture_mapping({'texture':{'texCoordUnit':1}}, {'texture':{'texCoordUnit':1},'UVTransforms':{'scale':[1,1]}}))
        self.assertFalse(same_texture_mapping({'texture':{'texCoordUnit':1}}, {'texture':{'texCoordUnit':0}}))
    def test_collinear_tangents_do_not_block_valid_surface(self):
        capture={'meshes':[{'name':'mirror','material':'1','attributes':{
            'Vertex':{'size':3,'data':[0,0,0,1,0,0,0,1,0]},
            'Normal':{'size':3,'data':[0,0,1]*3},
            'Tangent':{'size':4,'data':[0,0,1,1]*3}},
            'primitives':[{'mode':4,'indices':[0,1,2]}],
            'matrices':[np.eye(4).ravel().tolist()]}]}
        warnings=[]
        meshes=prepare_meshes(capture,{'1':0},warnings)
        self.assertEqual(len(meshes[0]['faces']),1)
        self.assertNotIn('TANGENT',meshes[0])
        self.assertEqual(len(warnings),1)
    def test_protected_non_aligned_original_uses_processed_viewer_variant(self):
        images=[{'url':'https://media.sketchfab.com/original.jpeg','width':268,'height':172,'pk':208750},
                {'url':'https://media.sketchfab.com/processed.jpeg','width':256,'height':128,'pk':208750},
                {'url':'https://media.sketchfab.com/thumb.jpeg','width':64,'height':32,'pk':None}]
        self.assertIs(select_texture_variant(images),images[1])
        images[0]['pk']=None
        self.assertIs(select_texture_variant(images),images[0])
    def test_strip_winding_survives_degenerate_connectors(self):
        result=triangles([0,1,2,3,3,4,4,5,6,7],5)
        np.testing.assert_array_equal(result,[[0,1,2],[2,1,3],[4,5,6],[6,5,7]])

    def test_triangle_fan(self):
        np.testing.assert_array_equal(triangles([4,5,6,7],6),[[4,5,6],[4,6,7]])

    def test_uid_restricts_network_target(self):
        uid='3d39ab2d22514d598d2aa34544a5416f'
        self.assertEqual(model_uid(f'https://sketchfab.com/models/{uid}/embed?autostart=1'),uid)
        with self.assertRaises(ValueError):model_uid(f'https://sketchfab.com.evil.invalid/models/{uid}')

    def test_source_metadata_difference_does_not_block_complete_viewer_scene(self):
        capture={'audit':{'surfaceInstances':1,'renderableTriangles':2,'source':'live-viewer-scene'}}
        meshes=[{'faces':np.asarray([[0,1,2],[1,3,2]])}]
        report,warnings=validate_export_counts(capture,meshes,10)
        self.assertTrue(report['matchesViewer'])
        self.assertFalse(report['metadataMatches'])
        self.assertEqual(len(warnings),1)

    def test_lost_surface_still_blocks_export(self):
        capture={'audit':{'surfaceInstances':2,'renderableTriangles':2,'source':'live-viewer-scene'}}
        with self.assertRaises(ValueError):
            validate_export_counts(capture,[{'faces':np.asarray([[0,1,2],[1,3,2]])}],2)

    def test_lost_triangle_still_blocks_even_if_api_count_matches(self):
        capture={'audit':{'surfaceInstances':1,'renderableTriangles':3,'source':'live-viewer-scene'}}
        with self.assertRaises(ValueError):
            validate_export_counts(capture,[{'faces':np.asarray([[0,1,2],[1,3,2]])}],2)

    def test_global_unlit_mode_without_per_material_flag(self):
        uid='0'*32
        prefetch={'/i/models/'+uid:{'options':{'shading':{'type':'shadeless'},'materials':{
            'material':{'name':'toon','stateSetID':1,'shadeless':False,'channels':{
                'AlbedoPBR':{'enable':True,'color':[0.2,0.4,0.6],'factor':1},
                'EmitColor':{'enable':True,'color':[0,0,0],'factor':1}}}}}}}
        with TemporaryDirectory() as folder:
            result=build_materials(prefetch,uid,Path(folder),lambda _:None)
        self.assertEqual(result['materials'][0]['extensions'],{'KHR_materials_unlit':{}})
        self.assertEqual(result['extensionsUsed'],['KHR_materials_unlit'])


if __name__=='__main__':unittest.main()
