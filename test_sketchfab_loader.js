const assert = require('node:assert/strict');
global.window = global;
class LegacyGeometry {
  getVertexAttributeList() { return {}; }
  drawImplementation(state) { return state + this.offset; }
}
const initial = [[0],{legacy(module,exports){Object.defineProperty(exports,'Z',{enumerable:true,get:()=>LegacyGeometry});return 'factory-result';}}];
global.webpackChunksketchfab = [initial];
require('./sketchfab_capture_loader.js');
const queue = global.webpackChunksketchfab;
const installed = {};
function runtime(parent,data) {
  Object.assign(installed,data[1]);
  if (parent) parent(data);
}
queue.forEach(runtime.bind(null,null));
// Match Webpack's real parent-push handoff; a mutable closure would recurse.
queue.push = runtime.bind(null,queue.push.bind(queue));
const legacyModule = {exports:{}};
assert.equal(installed.legacy(legacyModule,legacyModule.exports),'factory-result');
const legacy = new legacyModule.exports.Z();legacy.offset=3;
assert.equal(legacy.drawImplementation(7),10);
assert(__localExportGeometries.has(legacy));
class ProtectedGeometry {
  getVertexAttributeList() { return {}; }
  generateDrawCommand(state) { return this.offset * state; }
  a23b95b7(state) { return this.generateDrawCommand(state); }
}
queue.push([[1],{protected(module,exports){exports.Z=ProtectedGeometry;}}]);
const protectedModule={exports:{}};installed.protected(protectedModule,protectedModule.exports);
const geometry=new protectedModule.exports.Z();geometry.offset=4;
assert.equal(geometry.a23b95b7(7),28);
assert(__localExportGeometries.has(geometry));
class RigGeometry extends LegacyGeometry {}
queue.push([[2],{rig(module,exports){exports.Z=RigGeometry;}}]);
const rigModule={exports:{}};installed.rig(rigModule,rigModule.exports);
const rig=new rigModule.exports.Z();rig.offset=5;
assert.equal(rig.drawImplementation(2),7);assert(__localExportGeometries.has(rig));
const sentinel=new TypeError('Original factory failure');
queue.push([[3],{throws(){throw sentinel;},lazy(module,exports){Object.defineProperty(exports,'value',{enumerable:true,get(){throw sentinel;}});return 9;}}]);
assert.throws(()=>installed.throws({exports:{}},{}),error=>error===sentinel);
const lazy={exports:{}};assert.equal(installed.lazy(lazy,lazy.exports),9);
assert.throws(()=>lazy.exports.value,error=>error===sentinel);
assert.equal(__localExportHookCount,2);
assert.equal(__localExportGeometries.size,3);
assert.deepEqual(__localExportHookErrors,[]);
console.log('SKETCHFAB_LOADER_TEST_OK');
