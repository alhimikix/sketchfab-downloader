/* Offline protocol regression: real WASM instrumentation and allocator page tails. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
global.window = global;
global.__fabNativeFreeExport = 'free';
require('./fab_rig_capture.js');
const leb = value => {
  const result = [];
  do { const byte = value & 127; value >>>= 7; result.push(byte | (value ? 128 : 0)); } while (value);
  return result;
};
const section = (id, data) => [id, ...leb(data.length), ...data];
const name = text => [...leb(text.length), ...Buffer.from(text)];
const pieces = [Uint8Array.from([0,97,115,109,1,0,0,0]),
  Uint8Array.from(section(1,[2,0x60,2,0x7f,0x7f,0,0x60,1,0x7f,0])),
  Uint8Array.from(section(2,[2,...name('env'),...name('memory'),2,0,1,...name('env'),...name('logger'),0,0])),
  Uint8Array.from(section(3,[1,1])), Uint8Array.from(section(7,[1,...name('free'),0,1])),
  Uint8Array.from(section(10,[1,2,0,0x0b]))];
// Match the production viewer's large-module route without shipping a binary fixture.
pieces.push(Uint8Array.from([0,...leb(1024*1024+1),0]),new Uint8Array(1024*1024));
const wasm = Buffer.concat(pieces.map(p=>Buffer.from(p)));
const memory = new WebAssembly.Memory({initial:1});
const heap = new Uint8Array(memory.buffer), words = new DataView(memory.buffer);
const fakeGL = {bindBuffer() { throw Error('Observer called the original logger'); }};
async function main() {
  const {instance} = await WebAssembly.instantiate(wasm, {env:{memory,logger:function(a,b){fakeGL.bindBuffer(a,b);}}});
  const encoder = new TextEncoder();
  let count = 0;
  for (const tail of [1,9,127,511,512,1000,16383]) {
    const base = {Generator:'OpenSceneGraph 3.5.6',Version:9,'osg.Node':{Name:'',Children:[]}};
    const padding = 16384 + tail - encoder.encode(JSON.stringify(base)).length;
    base['osg.Node'].Name = 'x'.repeat(padding);
    const data = encoder.encode(JSON.stringify(base));
    assert.equal(data.length,16384+tail);
    heap.fill(0);
    heap.set(data.subarray(0,16384),1024); words.setUint32(1020,16392|3,true);
    instance.exports.free(1024);
    const size = Math.max(16,Math.ceil((tail+4)/8)*8);
    heap.set(data.subarray(16384),20000); words.setUint32(19996,size|3,true);
    instance.exports.free(20000);
    const status = __fabSourceCapture.status();
    assert.equal(status.scenes,++count,JSON.stringify(status));
    assert.deepEqual(status.errors,[]);
    assert.deepEqual(JSON.parse(__fabSourceCapture.read(count-1,0,__fabSourceCapture.size(count-1))),base);
  }
  // Split a multibyte code point across pages: decode only after full assembly.
  const base = {Generator:'OpenSceneGraph 3.5.6',Version:9,'osg.Node':{Name:'x'.repeat(16270)+'\u03b1'.repeat(50)}};
  const data = encoder.encode(JSON.stringify(base));
  assert(data.length>16384&&data.length<20000);
  heap.fill(0);heap.set(data.subarray(0,16384),1024);words.setUint32(1020,16392|3,true);instance.exports.free(1024);
  const tail=data.length-16384,size=Math.ceil((tail+4)/8)*8;
  heap.set(data.subarray(16384),20000);words.setUint32(19996,size|3,true);instance.exports.free(20000);
  assert.deepEqual(JSON.parse(__fabSourceCapture.read(count,0,__fabSourceCapture.size(count))),base);
  console.log('RIG_CAPTURE_TEST_OK');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
