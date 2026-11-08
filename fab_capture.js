/* Capture decoded WebGL2 buffers from a normally viewable Fab preview.
 * Inject before page scripts. This does not fetch model/source endpoints.
 * Call __fabGpuCapture.arm(), then wait until .status().complete.
 * .summary() is small; readBuffer(id)/readTexture(id) return base64 on demand.
 * It preserves decoded vertex/index bytes, raw shader attributes and uniforms;
 * consumers must split triangles by Attribute0.w geometry ID + miscInfo.y.
 */
(() => {
  if (!window.WebGL2RenderingContext) return;
  const prototype = WebGL2RenderingContext.prototype;
  const contexts = new WeakMap();
  const objects = new WeakMap();
  let serial = 0, frame = 0, lastFrameTime = null, armed = false, selectedFrame = null;
  let complete = false, captured = null, poseOnly = false;
  const maxBytes = 128 * 1024 * 1024;
  const objectId = value => {
    if (!value) return null;
    if (!objects.has(value)) objects.set(value, ++serial);
    return objects.get(value);
  };
  const context = gl => {
    if (!contexts.has(gl)) contexts.set(gl, {unit: 0, textures: new Map(), bound: new Map()});
    return contexts.get(gl);
  };
  const hook = (name, callback) => {
    const original = prototype[name];
    if (!original) return;
    prototype[name] = function(...args) {
      try { callback.call(this, context(this), args); }
      catch (error) { if (captured && captured.errors.length < 8) captured.errors.push(name + ': ' + error); }
      return original.apply(this, args);
    };
  };
  const nativeRAF = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = callback => nativeRAF(time => {
    if (time !== lastFrameTime) { frame++; lastFrameTime = time; }
    if (armed && selectedFrame !== null && frame !== selectedFrame && captured.draws.length) {
      complete = true;
      armed = false;
    }
    callback(time);
  });
  const asBytes = (data, offset, size) => {
    if (!ArrayBuffer.isView(data)) return null;
    return new Uint8Array(data.buffer, data.byteOffset + offset * (data.BYTES_PER_ELEMENT || 1), size);
  };
  const boundTexture = (s, target) => s.bound.get(s.unit + ':' + target);
  hook('activeTexture', function(s, [unit]) { s.unit = unit - this.TEXTURE0; });
  hook('bindTexture', function(s, [target, value]) { s.bound.set(s.unit + ':' + target, value); });
  hook('texStorage3D', function(s, [target, levels, format, width, height, depth]) {
    if (target !== this.TEXTURE_2D_ARRAY || format !== this.RGBA32F) return;
    const size = width * height * depth * 16;
    if (size > maxBytes) return;
    s.textures.set(boundTexture(s, target), {id: objectId(boundTexture(s, target)), target, width, height, depth,
      format, bytes: new Uint8Array(size)});
  });
  hook('texImage3D', function(s, [target, level, internalFormat, width, height, depth, border, format, type, data, offset = 0]) {
    if (target !== this.TEXTURE_2D_ARRAY || level !== 0 || format !== this.RGBA || type !== this.FLOAT) return;
    const size = width * height * depth * 16;
    if (size > maxBytes) return;
    const bytes = new Uint8Array(size), source = asBytes(data, offset, size);
    if (source) bytes.set(source);
    s.textures.set(boundTexture(s, target), {id: objectId(boundTexture(s, target)), target, width, height, depth,
      format: internalFormat, bytes});
  });
  hook('texSubImage3D', function(s, [target, level, x, y, z, width, height, depth, format, type, data, offset = 0]) {
    const texture = s.textures.get(boundTexture(s, target));
    if (!texture || level !== 0 || format !== this.RGBA || type !== this.FLOAT) return;
    const source = asBytes(data, offset, width * height * depth * 16);
    if (!source) return;
    for (let layer = 0; layer < depth; layer++) for (let row = 0; row < height; row++) {
      const start = (layer * height + row) * width * 16;
      const destination = (((z + layer) * texture.height + y + row) * texture.width + x) * 16;
      texture.bytes.set(source.subarray(start, start + width * 16), destination);
    }
  });
  const uniform = (gl, program, name) => {
    const location = gl.getUniformLocation(program, name);
    if (location === null) return null;
    const value = gl.getUniform(program, location);
    return ArrayBuffer.isView(value) ? Array.from(value) : value;
  };
  const uniformArray = (gl, program, name, count) => Array.from({length: count}, (_, i) => uniform(gl, program, `${name}[${i}]`));
  const buffer = (gl, object) => {
    const id = objectId(object);
    if (id === null || captured.buffers.has(id)) return id;
    const old = gl.getParameter(gl.COPY_READ_BUFFER_BINDING);
    gl.bindBuffer(gl.COPY_READ_BUFFER, object);
    try {
      const size = gl.getBufferParameter(gl.COPY_READ_BUFFER, gl.BUFFER_SIZE);
      if (size > maxBytes) throw new Error('Viewer buffer size limit exceeded: ' + size);
      const bytes = new Uint8Array(size);
      gl.getBufferSubData(gl.COPY_READ_BUFFER, 0, bytes);
      captured.buffers.set(id, {id, size, bytes});
    } finally { gl.bindBuffer(gl.COPY_READ_BUFFER, old); }
    return id;
  };
  const draw = function(s, args, kind) {
    if (!armed || complete) return;
    const gl = this, program = gl.getParameter(gl.CURRENT_PROGRAM);
    if (!program) return;
    const materialScalar = uniform(gl, program, 'uPBRScalars[44]');
    const normalLocation = gl.getAttribLocation(program, 'Attribute1');
    // Fab's shadow/depth passes omit normal3; UI/postprocess passes omit PBR.
    if (!materialScalar || normalLocation < 0 || gl.getVertexAttrib(normalLocation, gl.VERTEX_ATTRIB_ARRAY_SIZE) !== 3) return;
    if (selectedFrame === null) selectedFrame = frame;
    if (frame !== selectedFrame) return;
    const materialId = materialScalar[3];
    const vao = objectId(gl.getParameter(gl.VERTEX_ARRAY_BINDING));
    const key = JSON.stringify([vao, kind, args, materialId]);
    if (captured.keys.has(key)) return;
    captured.keys.add(key);
    const attributes = [];
    for (let i = 0; !poseOnly && i < gl.getProgramParameter(program, gl.ACTIVE_ATTRIBUTES); i++) {
      const active = gl.getActiveAttrib(program, i), location = gl.getAttribLocation(program, active.name);
      const vertexBuffer = gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_BUFFER_BINDING);
      if (!vertexBuffer) continue;
      attributes.push({name: active.name, location, buffer: buffer(gl, vertexBuffer),
        size: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_SIZE),
        type: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_TYPE),
        stride: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_STRIDE),
        offset: gl.getVertexAttribOffset(location, gl.VERTEX_ATTRIB_ARRAY_POINTER),
        normalized: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_NORMALIZED),
        integer: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_INTEGER),
        divisor: gl.getVertexAttrib(location, gl.VERTEX_ATTRIB_ARRAY_DIVISOR)});
    }
    const indexBuffer = !poseOnly && kind.startsWith('drawElements') ? buffer(gl, gl.getParameter(gl.ELEMENT_ARRAY_BUFFER_BINDING)) : null;
    const unit = uniform(gl, program, 'Texture23');
    const texture = s.textures.get(s.bound.get(unit + ':' + gl.TEXTURE_2D_ARRAY));
    if (texture && !captured.textures.has(texture.id)) captured.textures.set(texture.id, {...texture, bytes: texture.bytes.slice()});
    captured.draws.push({kind, args: args.slice(), materialId, vao, attributes, indexBuffer,
      transformTexture: texture?.id ?? null, table: uniformArray(gl, program, 'BigDataStorageU', 9),
      frameParity: uniform(gl, program, 'uScreenGlobalsScalars[5]')?.[1],
      materialScalars: poseOnly ? null : uniformArray(gl, program, 'uPBRScalars', 45)});
  };
  for (const name of ['drawElements', 'drawElementsInstanced', 'drawArrays', 'drawArraysInstanced'])
    hook(name, function(s, args) { draw.call(this, s, args, name); });
  const base64 = bytes => {
    let value = '';
    for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(value);
  };
  window.__fabGpuCapture = {
    arm(onlyPose = false) {
      poseOnly = onlyPose;
      captured = {draws: [], buffers: new Map(), textures: new Map(), keys: new Set(), errors: []};
      armed = true; complete = false; selectedFrame = null;
    },
    status() { return {armed, complete, frame: selectedFrame, draws: captured?.draws.length ?? 0}; },
    summary() {
      return !captured ? null : {frame: selectedFrame, complete, draws: captured.draws, errors: captured.errors,
        buffers: Array.from(captured.buffers.values(), ({bytes, ...rest}) => rest),
        textures: Array.from(captured.textures.values(), ({bytes, ...rest}) => ({...rest, size: bytes.length}))};
    },
    readBuffer(id, offset = 0, size = maxBytes) { return base64(captured.buffers.get(id).bytes.subarray(offset, offset + size)); },
    readTexture(id, offset = 0, size = maxBytes) { return base64(captured.textures.get(id).bytes.subarray(offset, offset + size)); },
    poseJSON() {
      const tables = new Map();
      for (const draw of captured.draws) {
        const key = JSON.stringify(draw.table);
        if (tables.has(key)) continue;
        const texture = captured.textures.get(draw.transformTexture);
        if (!texture) throw Error('Missing transform texture');
        const data = new Float32Array(texture.bytes.buffer), u = draw.table, line = u[0];
        const texel = offset => {
          const layer = Math.floor(offset / (line * line)), rest = offset % (line * line);
          const index = ((layer * texture.height + Math.floor(rest / line)) * texture.width + rest % line) * 4;
          if (index < 0 || index + 4 > data.length) throw Error('Transform table out of bounds');
          return Array.from(data.subarray(index, index + 4));
        };
        const matrices = (base, count, parity) => {
          const values = [];
          for (let i = 0; i < count; i++) for (let j = 0; j < 3; j++)
            values.push(...texel(base + (i + count * parity) * 3 + j));
          return values;
        };
        tables.set(key, {table:u, world:matrices(u[2], u[6] >> 1, draw.frameParity),
          skin:matrices(u[1], u[5] >> 1, draw.frameParity)});
      }
      return JSON.stringify(Array.from(tables.values()));
    }
  };
})();

/* Choose an already enabled preview clip in local display settings only. */
(() => {
  const selected = window.__fabPreferredClip;
  if (!selected) return;
  let data;
  Object.defineProperty(window, 'prefetchedData', {configurable:true, get:()=>data, set:value=>{
    data = value;
    for (const [path, object] of Object.entries(value)) {
      if (path.endsWith('/options') && object.animation) {
        object.animation.order = [selected];
        object.animation.speed = 1;
        object.animation.initializeWithRestPose = false;
      }
      if (path.endsWith('/doptions') && object.animations) {
        object.animations.animationList = object.animations.animationList.filter(a=>a.uid === selected && a.enabled !== false);
        object.animations.speed = 1;
        object.animations.initializeWithRestPose = false;
      }
      if (path.endsWith('/animations') && object.results)
        object.results = object.results.filter(a=>a.uid === selected && a.isEnabled !== false);
    }
  }});
})();

/* Native viewer lifecycle and its published keyboard controls. */
(() => {
  let module;
  window.__fabModelDisplayed = false;
  Object.defineProperty(window, '__dope', {configurable:true, get:()=>module, set:value=>{
    module = value;
    const callbacks = module?._registeredEvents;
    if (!callbacks) return;
    for (const event of ['onModelDisplayed', 'onModelDisplayedSSAA']) {
      let original = callbacks[event];
      const wrapper = function(...args) {
        window.__fabModelDisplayed = true;
        return original?.apply(this, args);
      };
      Object.defineProperty(callbacks, event, {configurable:true, get:()=>wrapper, set:value=>{original=value;}});
    }
  }});
  const raf = () => new Promise(resolve => requestAnimationFrame(resolve));
  const canvas = () => window.__dope?.canvas || document.querySelector('canvas');
  function key(type, code, keyCode, key, control = false) {
    canvas().dispatchEvent(new KeyboardEvent(type, {bubbles:true, cancelable:true,
      code, key, keyCode, which:keyCode, ctrlKey:control}));
  }
  async function press(code, keyCode, name, control = false) {
    canvas().focus();
    if (control) key('keydown', 'ControlLeft', 17, 'Control', true);
    key('keydown', code, keyCode, name, control);
    await raf();
    await raf();
    key('keyup', code, keyCode, name, control);
    if (control) key('keyup', 'ControlLeft', 17, 'Control', false);
    await raf();
    await raf();
  }
  window.__fabAnimationControls = {
    togglePlayback: () => press('Enter', 13, 'Enter'),
    start: () => press('ArrowUp', 38, 'ArrowUp', true),
    end: () => press('ArrowDown', 40, 'ArrowDown', true),
    step: () => press('ArrowRight', 39, 'ArrowRight', true),
    invalidate() {
      canvas().dispatchEvent(new WheelEvent('wheel', {bubbles:true, cancelable:true,
        deltaY:1, deltaMode:0, clientX:450, clientY:400}));
    }
  };
})();
