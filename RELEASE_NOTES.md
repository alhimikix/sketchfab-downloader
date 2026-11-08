Sketchfab Local 1.6.2 fixes `Unsupported animation channel: 0` and preserves Sketchfab facial morph animations alongside the original skeleton.

- Export named morph targets to GLB and Blender shape keys, including all 11 eye shapes and 30 clips from Low-poly-dacthyr.
- Read original target buffers instead of the viewer's remapped GPU slots or temporary CPU blends.
- Combine scalar weight tracks at their original key times, retaining independent timing and resetting omitted shapes to source defaults in each clip.
- Validate shape-key retention when importing Blender projects. A reopened Dracthyr project passed 990 shape-weight comparisons with no difference.

The Laika loading fix from 1.6.1 is included:

- Observe loaded geometry classes after Webpack evaluates their modules, including viewer builds with an obfuscated draw dispatcher.
- Preserve classic draw-method support, generated draw-command return values, inherited rig geometry and Webpack's parent-push callback.
- Count both runtime and legacy observers in timeout diagnostics.

Laika was exported successfully to GLB/OBJ and a reopened Blender project with packed textures. Regression tests cover protected/classic geometry, queued modules, runtime handoff and factory errors.

Original Fab skeleton and keyframe support from 1.6.0 is included:

- Keep original bone names, parent links, inverse bind matrices and unweighted/end bones in GLB and Blender projects.
- Decode original preview animation channels and key times, including supported packed quaternion and vector codecs. The synthetic 30 fps rig has been replaced.
- Preserve static rigged previews too. Reset absent transform channels to source defaults when switching Blender actions.
- Verify original bone names/hierarchy before saving and after reopening the `.blend`. Full names also remain in the `sourceBoneName` bone property.
- Retain Sketchfab support, Fab textures/materials, English/Russian UI and anonymous GitHub updates with SHA-256 verification and rollback.

Live checks preserved all 60 Shy Dancing bones and all 28 Mini Simple Character bones, including animated unweighted joints. All 424 source channels were compared with the shipped codec; sampled poses were checked against the live Fab viewer. Exported Blender projects were reopened and checked for matching names, parents and deformation. Offline tests cover synthetic rigs, compressed keys, incomplete buffers, allocator-page tails, UTF-8 boundaries and packaged resources.

This preserves the skeleton available in the public 3D preview; authoring controls removed during preview processing are unavailable. Original marketplace packages and 3DRipper's backend are not used. Fab morph targets, nontrivial GPU instancing, multiple source scenes and unsupported source codecs are rejected explicitly. Fab source capture is limited to 32 MiB and each preview animation buffer to 64 MiB. Blender must be installed separately for `.blend` creation.

## Downloads

- **Windows x64:** `windows-x64.exe` — portable single-file app.
- **macOS Apple Silicon:** `macos-arm64.zip` — extract the `.app`.
- **macOS Intel:** `macos-x64.zip` — extract the `.app`.
- **Linux x64:** `linux-x64.tar.gz` — extract and run `SketchfabLocal/SketchfabLocal`.

Python, Chromium, dependencies and the update installer are included. macOS builds are ad hoc signed and not notarized. Linux requires Ubuntu 22.04+ / glibc 2.35+. Downloads and update checks require no GitHub account or token. Install through **Updates → Check for updates** or download a package below.

Application source is licensed under MIT. Models and bundled third-party software retain their respective licenses.
