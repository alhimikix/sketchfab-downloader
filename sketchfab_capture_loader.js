/* Observe Geometry exports after Webpack evaluates their module factories.
 * Runtime method names remain stable even when the shipped source uses
 * computed-property obfuscation instead of literal drawGeometry calls.
 */
(() => {
  const geometries = window.__localExportGeometries ||= new Set();
  const marker = Symbol('localGeometryObserver');
  const queues = new WeakSet(), factories = new WeakMap();
  window.__localExportHookCount = 0;
  window.__localExportHookErrors = [];
  function inspect(value, depth = 0, seen = new Set()) {
    if (!value || (typeof value !== 'object' && typeof value !== 'function') || seen.has(value)) return;
    seen.add(value);
    if (typeof value === 'function') {
      const prototype = value.prototype;
      if (!prototype || typeof prototype.getVertexAttributeList !== 'function') return;
      const method = typeof prototype.drawImplementation === 'function' ? 'drawImplementation' : 'generateDrawCommand';
      const original = prototype[method];
      if (typeof original !== 'function' || original[marker]) return;
      const observer = function(...args) {
        geometries.add(this);
        return Reflect.apply(original, this, args);
      };
      observer[marker] = true;
      prototype[method] = observer;
      window.__localExportHookCount++;
      return;
    }
    if (depth > 1) return;
    const keys = Object.keys(value);
    if (keys.length > 256) return;
    for (const key of keys) {
      try { inspect(value[key], depth + 1, seen); } catch {}
    }
  }
  function payload(value) {
    const modules = value?.[1];
    if (!modules || typeof modules !== 'object') return;
    for (const key of Object.keys(modules)) {
      const factory = modules[key];
      if (typeof factory !== 'function') continue;
      if (factories.has(factory)) { modules[key] = factories.get(factory); continue; }
      const wrapped = function(module, exports, ...other) {
        const result = Reflect.apply(factory, this, [module, exports, ...other]);
        try {
          inspect(module?.exports);
          if (module?.exports !== exports) inspect(exports);
        } catch (error) {
          if (__localExportHookErrors.length < 4) __localExportHookErrors.push(String(error));
        }
        return result;
      };
      factories.set(factory, wrapped); factories.set(wrapped, wrapped);
      modules[key] = wrapped;
    }
  }
  function install(queue) {
    if (!Array.isArray(queue) || queues.has(queue)) return;
    queues.add(queue);
    for (const item of queue) payload(item);
    let implementation = queue.push;
    Object.defineProperty(queue, 'push', {configurable:true, get:()=>{
      // Webpack captures the previous push as its parent callback. Each getter
      // must retain that implementation rather than recurse into a later one.
      const previous = implementation;
      return function(...items) {
        for (const item of items) payload(item);
        return Reflect.apply(previous, this, items);
      };
    }, set:value=>{implementation = value;}});
  }
  let queue = window.webpackChunksketchfab;
  install(queue);
  Object.defineProperty(window, 'webpackChunksketchfab', {configurable:true, get:()=>queue,
    set:value=>{queue = value; install(value);}});
})();
