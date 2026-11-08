// Executed in the isolated browser after the public viewer has loaded.
() => {
  const rendered = [...window.__localExportGeometries];
  const roots = new Set();
  for (const g of rendered) {
    const seen = new Set();
    const stack = [g];
    while (stack.length) {
      const n = stack.pop();
      if (seen.has(n)) continue;
      seen.add(n);
      if (n.getName?.() === 'Scene - RootModel') roots.add(n);
      else stack.push(...(n._parents || []));
    }
  }
  if (!roots.size) throw new Error('Model scene root not found; viewer may have changed.');
  const meshes = [], seen = new Set();
  const audit = {surfaceInstances:0, renderableTriangles:0, source:'live-viewer-scene'};
  const countTriangles = (primitive, vertexCount) => {
    const ids = primitive.indices, mode = primitive.mode;
    if (![4,5,6].includes(mode)) return 0;
    if (mode===4 && ids.length%3) throw new Error('Incomplete TRIANGLES index buffer.');
    for (const index of ids) {
      if (!Number.isInteger(index) || index<0 || index>=vertexCount)
        throw new Error('Invalid vertex index in viewer geometry.');
    }
    let count=0;
    for (let i=0; i+2<ids.length; i += mode===4 ? 3 : 1) {
      const a=mode===6 ? ids[0] : ids[i], b=ids[i+1], c=ids[i+2];
      if (a!==b && b!==c && c!==a) count++;
    }
    return count;
  };
  for (const root of roots) {
    const walk = (node, path, material) => {
      if(node._isBoneBox)return;
      const ss = node.getStateSet?.();
      if (ss?._materialID !== undefined) material = String(ss._materialID);
      const name = node.getName?.();
      if (name && name !== 'Scene - RootModel') path = [...path, name];
      const attrs = node.getVertexAttributeList?.();
      if (attrs?.Vertex && !seen.has(node)) {
        seen.add(node);
        const attributes = {};
        for (const [key, value] of Object.entries(attrs)) {
          const a = value.getElements?.();
          if (a) attributes[key] = {size: value.getItemSize(), data: Array.from(a)};
        }
        const primitives = node.getPrimitiveSetList().map(p => {
          const indices = p.getIndices?.()?.getElements();
          const first = p.getFirst?.() || 0;
          const count = p.getCount();
          // DrawElements offset is bytes; DrawArrays first is a vertex offset.
          const start = indices ? first / indices.BYTES_PER_ELEMENT : first;
          if (!Number.isInteger(start) || !Number.isInteger(count) || start<0 || count<0 ||
              (indices && start+count>indices.length))
            throw new Error('Incomplete viewer primitive buffer.');
          return {mode:p.getMode(), indices:indices ? Array.from(indices.slice(start,start+count)) : Array.from({length:count},(_,i)=>start+i)};
        });
        const matrices = node.getWorldMatrices(root).map(m=>Array.from(m));
        if (primitives.some(p=>[4,5,6].includes(p.mode))) {
          if (!attributes.Vertex || attributes.Vertex.size!==3 || !attributes.Vertex.data.length ||
              attributes.Vertex.data.length%3 || !matrices.length)
            throw new Error('Surface geometry has not fully loaded.');
          const count=primitives.reduce((sum,p)=>sum+countTriangles(p,attributes.Vertex.data.length/3),0);
          if (count) audit.surfaceInstances+=matrices.length;
          audit.renderableTriangles+=count*matrices.length;
        }
        meshes.push({sourceID:node.getInstanceID(),name:path.join(' / ') || `Mesh ${meshes.length+1}`, material, attributes, primitives, matrices});
      }
      for (const child of node.children || []) walk(child,path,material);
    };
    walk(root,[],null);
  }
  return {meshes, renderedGeometryCount:rendered.length, sceneRootCount:roots.size, audit};
}
