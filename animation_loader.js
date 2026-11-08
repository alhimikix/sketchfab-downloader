(uid) => {
  let root;
  for(const g of window.__localExportGeometries){let n=g;const seen=new Set();while(n&&!seen.has(n)){seen.add(n);if(n.getName?.()==='Scene - RootModel'){root=n;break}n=n._parents?.[0]}if(root)break}
  if(!root)throw new Error('Animated scene is not ready.');
  const manager=(root._updateCallbacks||[]).find(c=>c._instanceAnimations);
  if(!manager)throw new Error('Animation manager was not found.');
  window.__localAnimationManager=manager;
  if(manager._instanceAnimations[uid])return true;
  const candidates=[],seen=new Set();let n=root;
  for(let i=0;n&&i<40;i++,n=n._parents?.[0]){
    candidates.push(n,...(n._updateCallbacks||[]));
  }
  for(let i=0;i<candidates.length&&i<300;i++){
    const object=candidates[i];if(!object||seen.has(object))continue;seen.add(object);
    if(typeof object.getFeatures==='function'){
      const animation=object.getFeatures()?.animation;
      if(animation?.getModel){animation.getModel().set('currentUID',uid);return true}
    }
    for(const key of ['_viewer','_viewerWebGL','_viewerOSGJS','_view3d','_scene','scene'])
      if(object[key])candidates.push(object[key]);
  }
  throw new Error('Could not load an additional animation clip.');
}
