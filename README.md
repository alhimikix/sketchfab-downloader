# Sketchfab Local

A native desktop app for exporting publicly viewable **Sketchfab and Fab 3D previews** to GLB, static OBJ, textures and a Blender project. Supported animations are included in GLB and `.blend`. Exports use optimized viewer geometry and may differ from the author's original files. Support depends on the scene's materials and animation features. The tool does not grant rights to model content.

## Download

Get a build from [Releases](https://github.com/alhimikix/sketchfab-downloader/releases/latest). The repository and release downloads are public. A GitHub account or token is not required.

| Platform | Release asset | Run |
| --- | --- | --- |
| Windows x64 | `SketchfabLocal-<version>-windows-x64.exe` | Open the portable EXE |
| macOS Apple Silicon | `SketchfabLocal-<version>-macos-arm64.zip` | Extract and open `SketchfabLocal.app` |
| macOS Intel | `SketchfabLocal-<version>-macos-x64.zip` | Extract and open `SketchfabLocal.app` |
| Linux x64 | `SketchfabLocal-<version>-linux-x64.tar.gz` | Extract the whole folder; run `SketchfabLocal/SketchfabLocal` |

Python, Python packages, Chromium and the update installer are bundled. Internet access is required for model downloads and updates. **Blender must be installed separately** for `.blend` creation. Set `BLENDER_PATH` to its executable if discovery fails. GLB/OBJ files are saved before Blender starts and remain available if Blender fails.

macOS requires macOS 14+. Builds use ad hoc signing and are not notarized by Apple; launch confirmation in Privacy & Security may be required. Windows builds do not have a commercial code-signing certificate. Linux builds target Ubuntu 22.04+ and compatible x64 distributions with glibc 2.35+ and a desktop session; minimal systems may need Chromium/Tk system libraries.

## Use the app

Paste a model URL, choose a save folder and click **Download model**. Expand **Details** for progress and diagnostics. Results include source attribution and export reports.

Accepted links include Sketchfab model pages, Fab `/listings/<uuid>` pages and direct Fab `/dope/<uuid>` preview links. A Fab listing with multiple 3D previews exports the first processed preview in gallery order; use a direct preview link to select another. Image-only galleries have no exportable 3D scene.

Sketchfab capture observes geometry exports after their viewer modules load. It supports both classic draw methods and the command-generation method used when the draw dispatcher is obfuscated. This fixes the `Hooks=0` timeout seen with Laika; timeout diagnostics include both runtime and legacy observer counts.

Sketchfab morph targets (shape keys) retain their source names, geometry and linear weight animations in GLB and Blender, including models that combine facial morphs with a skeleton. Original shape buffers are captured before GPU slot remapping; independently timed scalar tracks are combined without losing their key times. Missing shape tracks reset to source defaults when switching clips. OBJ remains static. Live validation of Low-poly-dacthyr preserved all 30 clips and 11 eye shapes; 990 shape-weight comparisons matched after reopening the Blender project. Fab morph targets remain unsupported.

Choose English or Russian in the header. Changes apply immediately and are remembered. The initial language follows the system locale, with English as the fallback. `--language en` or `--language ru` overrides the saved language for that launch.

Windows keeps settings and the default `downloads` folder beside the EXE. On macOS/Linux, downloads default to `~/Downloads/SketchfabLocal`; settings use the user's application configuration directory.

## Fab preview export

Fab support exports the scene displayed in its public 3D viewer. It does not download original marketplace asset packages. No Fab account, marketplace purchase, 3DRipper account or external conversion service is used. The app opens an isolated Chromium session and reads decoded WebGL color-pass buffers, transforms and public material/texture metadata. Repeated material passes are filtered to avoid duplicate geometry.

Fab exports preserve the **original preview skeleton**: bone names, parent hierarchy and inverse bind matrices, including bones without vertex weights. The app observes transient decoded OSG input pages before the viewer releases them, resolves its shared bone palettes and retains the skeleton's coordinate spaces. It does not substitute a synthetic flat rig when source data is unavailable. `report.json` records the result under `rigPreservation`; `fab-native-scene.json` retains the captured source scene.

Animations use their **original preview keyframes and times**, including supported compressed vector/quaternion channels. They are not sampled at a fixed frame rate. Constant tracks reset omitted transform properties to their source defaults when switching Blender actions. Static rigged previews keep their skeleton too; OBJ remains static. Blender import verifies original names and parent links both before saving and after reopening. Full names are also stored in the `sourceBoneName` bone property, including names longer than Blender's display-name limit.

Both current Fab material options and migrated Sketchfab-style options are supported. Base color, metallic/roughness, opacity, normal and emissive textures use the existing material converter. Material features outside glTF's supported mapping produce export notes or an explicit error.

Current limits: WebGL2 is required; morph-target deformation, nontrivial GPU instancing and unsupported source animation codecs are rejected. Native rig capture currently requires one source scene per preview. Decoded geometry is limited to 128 MiB per buffer and 256 MiB per frame; source scene capture to 32 MiB; each preview animation buffer to 64 MiB. Source animation arrays are limited to two million items. A viewer update can change its allocator or buffer layout; validation rejects incomplete data rather than silently replacing the skeleton. Lighting, environment, postprocessing and authoring files are not embedded. Bones or authoring controls removed when the platform prepared the preview cannot be recovered from that preview.

Live validation used the public previews of [Bank With Broken Wall](https://www.fab.com/listings/e149a15c-c505-4dc9-83fe-39fce88fd15f), [Shy Dancing](https://www.fab.com/listings/ce939784-2957-4a27-bf57-aee36a4137d9) and [Mini Simple Character](https://www.fab.com/listings/51c66a88-a022-4fca-b1a6-57c3540fb855). The rig checks cover all 60 Shy bones, all 28 Mini bones, five Mini clips, unweighted joints, legacy options, textures and Blender project reopening. Bone names/parents were compared with the decoded source scene; poses at zero and one second were compared with the live viewer. Model content is not included in this repository. Offline tests use synthetic scenes and a generated WASM fixture, including small allocator tails and split UTF-8 characters.

The public [3DRipper Fab page](https://3dripper.online/ru/fab-downloader) and its shipped frontend show a server-assisted preview conversion flow. Its paid backend was not inspected. This app implements its own viewer capture and does not depend on that service or copy its converter code.

## Updates

Version 1.4.0 checks public GitHub Releases without authentication. Install it manually once if an older version asks for a token; subsequent public versions can update themselves without one.

1. Open **Updates** in the header.
2. Click **Check for updates**.
3. When a newer stable release is available, click **Download, install and restart**.

The app uses anonymous GitHub requests. It does not read GitHub tokens from environment variables or credential stores. There are no token fields or credential-store dependencies.

Automatic startup checking is enabled by default and can be disabled in the update window. Installation always requires the update button; checking alone does not replace the app. If GitHub limits requests from your network, wait and retry or use **Open release page** to download the package manually.

The updater selects the native package from this repository's latest stable release, checks its GitHub SHA-256 digest and exact size, extracts beside the installed app and runs an offline startup check. Downloads require HTTPS and GitHub-owned download hosts. No GitHub credentials are sent with update checks or downloads.

HTTPS uses system trust together with a bundled Mozilla CA certificate store, so builds do not depend on a separately installed Python certificate bundle.

The bundled installer waits for the app to exit, replaces it, restarts it and waits for a startup acknowledgement. Failed installation/startup triggers restoration of the previous version. A previous-app backup remains until a later successful launch cleans it up. Windows replaces only the EXE; Linux preserves additional top-level user files. Settings and model download folders are preserved. The application directory must be writable; the updater does not request administrator privileges. Update source checkouts with Git instead.

## CI and releases

Pushes to `main` and pull requests run tests on Windows, Linux and macOS. **Release** builds four native packages, extracts them and tests the actual distributables offline. Checks cover both GUI languages, resources, HTTPS certificates, Chromium/WebGL and an isolated installation through the bundled updater. Unit tests cover anonymous requests, ignored environment credentials, interrupted installation, startup rollback, checksums, unsafe archives and missing translations.

Tags such as `v1.6.1` publish all four packages and SHA-256 files after every platform passes. Releases remain drafts until all assets upload. Match the tag to `app_version.py`; update the Windows resource in `version_info.txt` as well. macOS bundle versions follow `app_version.py`. Edit `RELEASE_NOTES.md` for the release.

For preview builds, use **Actions → Release → Run workflow** on `main`. Packages and reports appear in run artifacts. To publish:

```sh
git tag -a v1.6.1 -m "Sketchfab Local 1.6.1"
git push origin v1.6.1
```

The repository, releases and CI runs are public. CI publishes with the built-in `GITHUB_TOKEN`; no extra publishing secret is needed. That CI token is not included in the app. Public updates and package downloads require no authentication.

## Run from source

Use Python 3.14 with Tk (`python3-tk` on many Linux systems).

```sh
python -m venv .venv
# Activate the environment using your shell's activation command.
python -m pip install -r requirements.txt
python -m playwright install chromium --no-shell
python desktop_main.py
```

CLI: `python desktop_main.py "MODEL_URL" --output "OUTPUT_FOLDER" --language en`. Blender is still required for project creation.

## Build and test

Build on the target OS and architecture. Dependencies are pinned in `requirements-build.txt`.

```sh
python -m pip install -r requirements-build.txt
python -m unittest discover -v
python scripts/build_release.py --target windows-x64
python scripts/smoke_release.py --target windows-x64
```

Other targets: `linux-x64`, `macos-x64`, `macos-arm64`. On headless Linux, prefix smoke tests with `xvfb-run -a`; install browser libraries with `python -m playwright install-deps chromium`. Packages go to `release-assets/`. Windows also provides `Build-EXE.ps1`.

Messages use keys in `locales/en.json` and `locales/ru.json`, with matching keys and format placeholders. Russian text is confined to the Russian catalog. Progress events use stable keys rather than translated prefixes. Models, downloaded resources, credentials, settings and build environments are excluded from Git.

## License

The application source code is licensed under the [MIT License](LICENSE), copyright 2026 alhimikix. Downloaded models, textures and animations retain their authors' licenses. Bundled third-party software retains its own licenses and notices; the project's MIT license does not replace them.
