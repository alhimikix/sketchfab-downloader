/* Preserve the viewer's transient OSG JSON stream before its input pages are freed.
 * The observer reads public preview data; the renderer keeps its original code
 * and allocation behavior apart from one callback at the allocator entry.
 */
(() => {
  const nativeInstantiate = WebAssembly.instantiate.bind(WebAssembly);
  const decoder = new TextDecoder('utf-8', {fatal:true});
  const maxSceneBytes = 32 * 1024 * 1024;
  let memory = null, state = null, totalBytes = 0;
  const scenes = [], errors = [];
  const fail = message => { if (errors.length < 4) errors.push(String(message)); };
  function observe(pointer) {
    if (!memory || pointer < 16 || pointer % 8) return;
    const heap = new Uint8Array(memory.buffer);
    if (pointer >= heap.length - 4) return;
    const header = new DataView(memory.buffer).getUint32(pointer - 4, true);
    const size = (header & 0xfffffff8) - 8;
    // The stream reader uses full 16 KiB pages and one exact-sized tail.
    // dlmalloc lets the final allocation use the following prev_foot word.
    if (size < 8 || size > 16384) return;
    const length = size === 16384 ? size : size + 4;
    if (pointer + length > heap.length) return;
    const bytes = heap.subarray(pointer, pointer + length);
    let first = bytes.subarray(0, Math.min(512, bytes.length));
    const zero = first.indexOf(0);
    if (zero >= 0) first = first.subarray(0, zero);
    if (!first.length) return;
    if (first.some(v => v < 32 && v !== 9 && v !== 10 && v !== 13)) return;
    let prefix;
    try { prefix = new TextDecoder().decode(first); } catch { return; }
    const root = /^\s*\{/.test(prefix) && /"Generator"\s*:\s*"OpenSceneGraph/.test(prefix);
    if (root) state = {chunks:[], size:0, depth:0, string:false, escape:false};
    if (!state) return;
    if (totalBytes + bytes.length > maxSceneBytes) { fail('OSG source capture exceeds 32 MiB'); state = null; return; }
    const before = {depth:state.depth, string:state.string, escape:state.escape};
    let end = bytes.length;
    for (let i = 0; i < bytes.length; i++) {
      const value = bytes[i];
      if (state.string) {
        if (state.escape) state.escape = false;
        else if (value === 92) state.escape = true;
        else if (value === 34) state.string = false;
      } else if (value === 34) state.string = true;
      else if (value === 123 || value === 91) state.depth++;
      else if (value === 125 || value === 93) {
        state.depth--;
        if (state.depth === 0) { end = i + 1; break; }
      }
    }
    if (size !== 16384 && (state.depth !== 0 || state.string)) {
      Object.assign(state, before); return;
    }
    state.chunks.push(bytes.slice(0, end)); state.size += end; totalBytes += end;
    if (state.depth !== 0 || state.string) return;
    const all = concatenate(state.chunks);
    try {
      const text = decoder.decode(all), document = JSON.parse(text);
      if (document['osg.Node'] || document['osg.MatrixTransform'] || document['osgAnimation.Skeleton']) scenes.push(text);
    } catch (error) { fail(error); }
    state = null;
  }
  const read = (bytes, cursor) => {
    let value = 0, shift = 0, byte;
    do { byte = bytes[cursor.i++]; value += (byte & 127) * 2 ** shift; shift += 7;
      if (shift > 35 || cursor.i > bytes.length) throw Error('Invalid WASM LEB');
    } while (byte & 128);
    return value;
  };
  const encode = value => {
    const result = [];
    do { const byte = value & 127; value = Math.floor(value / 128); result.push(byte | (value ? 128 : 0)); } while (value);
    return Uint8Array.from(result);
  };
  function concatenate(parts) {
    const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
    let offset = 0; for (const part of parts) { result.set(part, offset); offset += part.length; }
    return result;
  }
  const string = (bytes, cursor) => {
    const size = read(bytes, cursor), value = decoder.decode(bytes.subarray(cursor.i, cursor.i + size));
    cursor.i += size; return value;
  };
  function instrument(input, imports) {
    const bytes = new Uint8Array(input), sections = [], types = [], functions = [], imported = [];
    let freeIndex = null, logger = null;
    const top = {i:8};
    while (top.i < bytes.length) {
      const begin = top.i, id = bytes[top.i++], size = read(bytes, top), start = top.i, end = start + size;
      if (end > bytes.length) throw Error('Invalid WASM section');
      sections.push({id, begin, start, end}); top.i = end;
    }
    for (const section of sections) {
      const c = {i:section.start}, count = read(bytes, c);
      if (section.id === 1) for (let i = 0; i < count; i++) {
        if (bytes[c.i++] !== 0x60) throw Error('Unsupported WASM function type');
        const n = read(bytes, c), input = Array.from(bytes.subarray(c.i, c.i + n)); c.i += n;
        const m = read(bytes, c), output = Array.from(bytes.subarray(c.i, c.i + m)); c.i += m;
        types.push({input, output});
      }
      if (section.id === 2) for (let i = 0; i < count; i++) {
        const module = string(bytes, c), name = string(bytes, c), kind = bytes[c.i++];
        if (kind === 0) {
          const type = read(bytes, c), fn = imports[module]?.[name];
          const signature = types[type]; imported.push({module, name, type});
          if (!logger && fn && fn.toString().includes('.bindBuffer(') && signature.input.length === 2
              && signature.input.every(v => v === 127) && !signature.output.length)
            logger = {module, name, fn, index:imported.length - 1};
        } else if (kind === 1 || kind === 2) {
          if (kind === 1) c.i++;
          const flags = read(bytes, c); read(bytes, c); if (flags & 1) read(bytes, c);
        } else if (kind === 3) c.i += 2;
        else if (kind === 4) { read(bytes, c); read(bytes, c); }
        else throw Error('Unsupported WASM import');
      }
      if (section.id === 3) for (let i = 0; i < count; i++) functions.push(read(bytes, c));
      if (section.id === 7) for (let i = 0; i < count; i++) {
        const name = string(bytes, c), kind = bytes[c.i++], index = read(bytes, c);
        if (kind === 0 && name === window.__fabNativeFreeExport) freeIndex = index;
      }
    }
    if (freeIndex === null) return input;
    if (!logger) throw Error('Viewer allocator observation import not found');
    const signature = types[functions[freeIndex - imported.length]];
    if (signature.input.join() !== '127' || signature.output.length) throw Error('Unexpected allocator signature');
    const prefix = concatenate([Uint8Array.from([0x20,0,0x41,0x80,0x80,0x80,0x80,0x78,0x73,0x41,0,0x10]), encode(logger.index)]);
    const code = sections.find(s => s.id === 10), c = {i:code.start}, count = read(bytes, c), parts = [encode(count)];
    for (let i = 0; i < count; i++) {
      const size = read(bytes, c), start = c.i, end = start + size; c.i = end;
      if (i !== freeIndex - imported.length) { parts.push(encode(size), bytes.subarray(start, end)); continue; }
      const local = {i:start}, declarations = read(bytes, local);
      for (let j = 0; j < declarations; j++) { read(bytes, local); local.i++; }
      const body = concatenate([bytes.subarray(start, local.i), prefix, bytes.subarray(local.i, end)]);
      parts.push(encode(body.length), body);
    }
    const body = concatenate(parts);
    imports[logger.module][logger.name] = function(target, buffer) {
      if (target >= 0) return logger.fn(target, buffer);
      try { observe((target ^ 0x80000000) >>> 0); } catch (error) { fail(error); }
    };
    return concatenate([bytes.subarray(0, code.begin), Uint8Array.of(10), encode(body.length), body, bytes.subarray(code.end)]);
  }
  WebAssembly.instantiate = async function(bytes, imports) {
    for (const group of Object.values(imports || {})) for (const value of Object.values(group))
      if (value instanceof WebAssembly.Memory) memory = value;
    let input = bytes;
    if (!(bytes instanceof WebAssembly.Module) && bytes.byteLength > 1000000 && window.__fabNativeFreeExport) {
      try { input = instrument(bytes, imports); } catch (error) { fail(error); }
    }
    return nativeInstantiate(input, imports);
  };
  WebAssembly.instantiateStreaming = async function(response, imports) {
    return WebAssembly.instantiate(await (await response).arrayBuffer(), imports);
  };
  window.__fabSourceCapture = {
    status: () => ({scenes:scenes.length, errors, bytes:totalBytes, pending:state?.size || 0}),
    size: index => scenes[index]?.length || 0,
    read: (index, offset, size) => scenes[index].slice(offset, offset + size)
  };
})();
