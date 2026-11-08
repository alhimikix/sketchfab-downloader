// Add native scene hierarchy, skin bindings and decoded keyframe channels.
(capture) => {
  const roots=new Set();
  for(const g of window.__localExportGeometries){let n=g;const seen=new Set();while(n&&!seen.has(n)){seen.add(n);if(n.getName?.()==='Scene - RootModel'){roots.add(n);break}n=n._parents?.[0]}}
  const list=[],ids=new Map(),parent=new Map();
  const visit=(n,p=null)=>{if(n._isBoneBox)return;if(ids.has(n))return;ids.set(n,list.length);list.push(n);parent.set(n,p);for(const child of n.children||[])visit(child,n)};
  for(const root of roots)visit(root);
  const nodes=list.map(n=>{
    const callbacks=n._updateCallbacks||[],cb=callbacks.find(c=>c._stackedTransforms);
    const trs={};if(cb)for(const st of cb._stackedTransforms){const v=st._target?.defaultValue;if(v)trs[st.getName?.()||st._name]=Array.from(v)}
    return {name:n.getName?.()||'',parent:ids.get(parent.get(n))??null,sourceID:n.getInstanceID(),matrix:n.getMatrix?Array.from(n.getMatrix()):null,target:cb?.getName?.()||cb?._name||null,trs,
      inverseBind:n._invBindInSkeletonSpace?Array.from(n._invBindInSkeletonSpace):null};
  });
  const geometries=new Map();for(const n of list)if(n.getVertexAttributeList)geometries.set(n.getInstanceID(),n);
  // UpdateMorph can remap its four GPU slots and merge additional shapes on
  // the CPU. Read source target geometries, never those transient GPU slots.
  const morphBindings=new Map();
  for(const node of list)for(const cb of node._updateCallbacks||[]){
    if(!cb._morphs?.length||!cb._targetNames?.length)continue;
    for(const morph of cb._morphs){
      const targets=morph.getMorphTargets();
      if(targets.length!==cb._targets.length)throw new Error('Morph target binding count mismatch.');
      morphBindings.set(morph,{names:Array.from(cb._targetNames),weights:cb._targets.map(t=>t.defaultValue),
        bindings:cb._targets.map((t,i)=>({target:cb._targetNames[i],targetID:t.id,path:String(i)})),
        targets:targets.map(target=>Object.fromEntries(Object.entries(target.getVertexAttributeList()).map(([key,value])=>{
          const original=value.getInitialBufferArray?.()||value;
          return [key,{size:original.getItemSize(),data:Array.from(original.getElements())}];
        })))});
    }
  }
  for(const mesh of capture.meshes){
    if(!mesh.primitives.some(p=>[4,5,6].includes(p.mode)))continue;
    const n=geometries.get(mesh.sourceID);if(!n)continue;mesh.node=ids.get(n);
    const morph=n.getMorphTargets?n:n.getSourceGeometry?.();
    if(morph?.getMorphTargets?.().length){
      const binding=morphBindings.get(morph);
      if(!binding)throw new Error('Morph geometry has no source animation binding.');
      mesh.morph=binding;
    }
    if(n._boneNameID&&Object.keys(n._boneNameID).length){
      const bones=n._rigTransformImplementation?._bones;
      if(!bones?.length)throw new Error('Skin bone palette is not initialized.');
      mesh.skin={joints:bones.map(b=>ids.get(b)),inverseBind:bones.map(b=>Array.from(b._invBindInSkeletonSpace)),matrixFromSkeletonToGeometry:Array.from(n._matrixFromSkeletonToGeometry)};
      if(mesh.skin.joints.some(j=>j===undefined))throw new Error('A skin joint is outside the captured scene.');
    }
  }
  const clips=new Map();
  for(const root of roots)for(const cb of root._updateCallbacks||[]){
    for(const [uid,a] of Object.entries(cb._instanceAnimations||{})){
      clips.set(uid,{uid,name:uid,duration:a.duration,firstKeyTime:a.firstKeyTime,channels:a.channels.map(instance=>{
        const c=instance.channel;return {target:c.target,path:c.name,type:c.type,targetID:instance.targetID,times:Array.from(c.times),values:Array.from(c.keys)};
      })});
    }
  }
  capture.scene={nodes,roots:[...roots].map(r=>ids.get(r)),animations:[...clips.values()]};
  return capture;
}
