import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from animation_export import prepare_animated_meshes
from exporter import prepare_meshes
from fab_animation import animated_capture, decompose
from fab_geometry import reconstruct
from fab_source import fab_reference, normalize_materials, resolve
from materials import build_materials
from sketchfab_local import model_source


class FabTests(unittest.TestCase):
    def test_links_accept_localized_listings_and_direct_previews_only(self):
        uid = '12345678-1234-1234-1234-123456789abc'
        self.assertEqual(fab_reference('https://www.fab.com/ru/listings/' + uid + '?x=1'), ('listings', uid))
        self.assertEqual(model_source('https://fab.com/dope/' + uid), ('fab', uid))
        for url in ('http://fab.com/dope/' + uid, 'https://fab.com.attacker.test/dope/' + uid,
                    'https://user@fab.com/dope/' + uid, 'https://fab.com/profile/' + uid):
            with self.assertRaises(ValueError, msg=url):
                fab_reference(url)

    def test_listing_selects_first_processed_model_and_rejects_image_only(self):
        listing = {'title': 'Test', 'medias': [
            {'type': 'image', 'previewUid': 'image'},
            {'type': 'model', 'previewUid': 'later', 'position': 5, 'processingStatus': 'succeeded'},
            {'type': 'model', 'previewUid': 'busy', 'position': 0, 'processingStatus': 'processing'},
            {'type': 'model', 'previewUid': 'first', 'position': 2, 'processingStatus': 'succeeded'}]}
        url = 'https://fab.com/listings/12345678-1234-1234-1234-123456789abc'
        with patch('fab_source.get_public', return_value=listing):
            self.assertEqual(resolve(url)['availablePreviews'], ['first', 'later'])
        with patch('fab_source.get_public', return_value={'medias': []}):
            with self.assertRaises(ValueError):
                resolve(url)

    def test_dotted_material_options_preserve_uvs_normal_orientation_and_roughness(self):
        source = {'materials': {'test': {'name': 'Test', 'basecolor.enabled': True,
            'basecolor.color': [.1, .2, .3], 'basecolor.factor': .5, 'basecolor.texture.uri': 'tex',
            'basecolor.texture.uv': 1, 'basecolor.texture.transform.scale': [2, 3],
            'normal.enabled': True, 'normal.opengl': False, 'roughness.enabled': True,
            'roughness.invert': True, 'roughness.factor': .2}}}
        material = normalize_materials(source)['test']
        self.assertEqual(material['channels']['AlbedoPBR']['texture'], {'uid': 'tex', 'texCoordUnit': 1})
        self.assertEqual(material['channels']['AlbedoPBR']['UVTransforms']['scale'], [2, 3])
        self.assertTrue(material['channels']['NormalMap']['flipY'])
        material['channels']['AlbedoPBR'].pop('texture')
        with TemporaryDirectory() as folder:
            result = build_materials({'/i/models/test': {'options': {'materials': {'test': material}}}}, 'test', Path(folder))
        self.assertAlmostEqual(result['materials'][0]['pbrMetallicRoughness']['roughnessFactor'], .8)

    def test_color_pass_material_replays_do_not_duplicate_triangles(self):
        position = np.array([[0, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0],
                             [0, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype='<f4')
        position[3:, 3] = np.array([1, 1, 1], dtype='<u4').view('<f4')
        buffers = {1: position.tobytes(), 2: np.array([[0, 0, 1]] * 6, dtype='<f4').tobytes(),
                   3: np.arange(6, dtype='<u4').tobytes()}
        data = np.zeros((64, 4), dtype='<f4')
        for index, translation in enumerate(([10, 0, 0], [0, 20, 0])):
            matrix = np.eye(4); matrix[:3, 3] = translation
            data[index * 3:index * 3 + 3] = matrix[:3].T.ravel().reshape(3, 4)
        data[16:18, 1] = [2, 3]
        attributes = [{'name': name, 'buffer': buffer, 'size': size, 'type': 5126,
                       'stride': 0, 'offset': 0, 'divisor': 0, 'normalized': False, 'integer': False}
                      for name, buffer, size in [('Attribute0', 1, 4), ('Attribute1', 2, 3)]]
        draws = []
        for material in (2, 3, 2):
            scalars = [[0, 0, 0, 0] for _ in range(45)]; scalars[20] = [.2, .3, .4, .5]
            draws.append({'vao': 1, 'kind': 'drawElements', 'args': [4, 6, 5125, 0],
                'attributes': attributes, 'indexBuffer': 3, 'transformTexture': 4,
                'table': [8, 0, 0, 12, 16, 0, 4, 2, 2], 'frameParity': 0,
                'materialId': material, 'materialScalars': scalars})
        options = {'materials': {key: {'name': key, 'basecolor': {'color': [.2, .3, .4]}} for key in ('a', 'b')}}
        summary = {'draws': draws, 'textures': [{'id': 4, 'width': 8, 'height': 8, 'depth': 1}], 'errors': []}
        capture = reconstruct(summary, buffers, {4: data.tobytes()}, options)
        self.assertEqual(capture['audit']['renderableTriangles'], 2)
        meshes = prepare_meshes(capture, {'a': 0, 'b': 1}, basis_transform=np.eye(4))
        self.assertEqual([mesh['material'] for mesh in meshes], [0, 1])
        np.testing.assert_allclose(meshes[0]['POSITION'][0], [10, 0, 0])
        np.testing.assert_allclose(meshes[1]['POSITION'][0], [0, 20, 0])
        broken = copy.deepcopy(summary); broken['draws'][0]['materialScalars'][20][0] = .9
        with self.assertRaises(ValueError):
            reconstruct(broken, buffers, {4: data.tobytes()}, options)

    def test_baked_rig_keeps_bind_vertices_and_world_times_skin_transform(self):
        matrix = np.eye(4); matrix[:3, 3] = [2, 3, 4]
        trs = decompose(matrix)
        self.assertEqual(trs['translation'], [2, 3, 4])
        self.assertEqual(trs['rotation'], [0, 0, 0, 1])
        shear = matrix.copy(); shear[0, 1] = .5
        with self.assertRaises(ValueError):
            decompose(shear)
        identity = np.eye(4)
        table = [8, 0, 6, 12, 16, 2, 2, 1, 1]
        attrs = {'Vertex': {'size': 3, 'data': [9, 9, 9] * 3}, 'Normal': {'size': 3, 'data': [0, 0, 1] * 3}}
        mesh = {'sourceID': 0, 'name': 'test', 'material': 'a', 'geometryID': 0, '_fabTable': table,
                'attributes': attrs, 'matrices': [identity.ravel().tolist()],
                'primitives': [{'mode': 4, 'indices': [0, 1, 2]}], '_fabSkin': {
                    'joints': [[0, 0, 0, 0]] * 3, 'weights': [[1, 0, 0, 0]] * 3,
                    'bindPositions': [0, 0, 0, 1, 0, 0, 0, 1, 0], 'bindNormals': [0, 0, 1] * 3, 'bindTangents': None}}
        raw_pose = {'table': table, 'world': identity[:3].T.ravel().tolist(), 'skin': matrix[:3].T.ravel().tolist()}
        capture = animated_capture({'meshes': [mesh]}, [{'uid': 'clip', 'name': 'Test', 'duration': 1,
            'times': [0, 1], 'frames': [[raw_pose], [raw_pose]]}])
        self.assertEqual(capture['meshes'][0]['attributes']['Vertex']['data'], attrs['Vertex']['data'])
        prepared = prepare_animated_meshes(capture, {'a': 0}, [])
        np.testing.assert_allclose(prepared[0]['POSITION'][0], [0, 0, 0])
        joint = capture['scene']['nodes'][prepared[0]['skin']['joints'][0]]
        self.assertEqual(joint['trs']['translation'], [2, 3, 4])
        self.assertEqual(prepared[0]['skin']['skeleton'], len(capture['scene']['nodes']))
        from exporter import write_glb
        import json, struct
        with TemporaryDirectory() as folder:
            glb = write_glb(prepared, {'materials': [{}]}, Path(folder), 'Synthetic test', scene=capture['scene']).read_bytes()
        size = struct.unpack_from('<I', glb, 12)[0]
        document = json.loads(glb[20:20 + size])
        self.assertEqual(document['skins'][0]['skeleton'], document['scenes'][0]['nodes'][0])


if __name__ == '__main__':
    unittest.main()
