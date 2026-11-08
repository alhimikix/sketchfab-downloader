const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const capture = eval('(' + fs.readFileSync(path.join(__dirname, 'animation_capture.js'), 'utf8') + ')');
const attr = data => ({getItemSize:()=>3, getElements:()=>data});
const initial = attr([0,0,1,1,0,0,0,1,0]);
const target = {getVertexAttributeList:()=>({Vertex:{
  getElements:()=>[99,99,99], getInitialBufferArray:()=>initial
}})};
const morph = {getMorphTargets:()=>[target]};
const root = {getName:()=>'Scene - RootModel', getInstanceID:()=>1, children:[]};
const geometry = {getName:()=>'Face', getInstanceID:()=>2, getVertexAttributeList:()=>({}),
  getSourceGeometry:()=>morph, _parents:[root], children:[]};
root.children.push(geometry);
root._updateCallbacks = [{_morphs:[morph], _targetNames:['Blink'],
  _targets:[{id:3,defaultValue:.25,value:1}], _weights:[1,0,0,0]}];
global.window = {__localExportGeometries:new Set([geometry])};
const result = capture({meshes:[{sourceID:2,primitives:[{mode:4}]}]});
assert.deepEqual(result.meshes[0].morph.targets[0].Vertex.data,initial.getElements());
assert.deepEqual(result.meshes[0].morph.weights,[.25]);
assert.deepEqual(result.meshes[0].morph.bindings,[{target:'Blink',targetID:3,path:'0'}]);
assert.equal(result.meshes[0].node,1);
console.log('MORPH_CAPTURE_TEST_OK');
