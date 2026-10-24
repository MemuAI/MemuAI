# Third-party components of the MemuAI companion

| Component | Version | License | Where |
|---|---|---|---|
| three.js | 0.169.0 | MIT — `LICENSE-three.txt` | `vendor/memu-three-vrm.min.js` |
| @pixiv/three-vrm (+ core, materials-mtoon, springbone, node-constraint, v0compat) | 3.5.5 | MIT — `LICENSE-three-vrm.txt` | `vendor/memu-three-vrm.min.js` |
| Sample model HairSample_Female (VRoid Project, pixiv Inc.) | — | CC0 1.0 — `../models/sample/LICENSE.md` | `models/sample/sample.vrm` |

`vendor/memu-three-vrm.min.js` is a single esbuild IIFE bundle (`window.MemuThreeVRM`)
so the app works offline and under a CSP without `unsafe-eval` or import maps.
The bundle keeps every upstream licence comment at its end. Rebuild recipe:

```sh
npm i three@0.169.0 @pixiv/three-vrm@3.5.5 esbuild
cat > entry.js <<'JS'
import { SkinnedMesh, WebGLRenderer, Scene, PerspectiveCamera, DirectionalLight, AmbientLight, Object3D, Vector3, Box3, SRGBColorSpace } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
const THREE = { SkinnedMesh, WebGLRenderer, Scene, PerspectiveCamera, DirectionalLight, AmbientLight, Object3D, Vector3, Box3, SRGBColorSpace };
window.MemuThreeVRM = { THREE, GLTFLoader, VRMLoaderPlugin, VRMUtils, versions: { three: '0.169.0', threeVrm: '3.5.5' } };
JS
npx esbuild entry.js --bundle --minify --format=iife --target=es2020 --legal-comments=eof --outfile=memu-three-vrm.min.js
```

## Live2D — not shipped

Live2D Cubism Core is proprietary software of Live2D Inc. (Live2D Proprietary
Software License Agreement). An application into which users load their own
models is an "Expandable Application" in Live2D's terms, and publishing one
needs Live2D's review and a separate agreement — even for individuals and
small businesses. MemuAI therefore contains **no** Live2D code: neither the
Cubism Core nor the Cubism Framework that `pixi-live2d-display` embeds.

Live2D support is optional. Only after the user ticks the consent box in the
companion settings does MemuAI download, into the user's data folder
(`memuai/companion/runtime/`):

* `live2dcubismcore.min.js` — from the official address
  https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js
  (checked for the Live2D marker; Live2D updates it in place, so no hash);
* `pixi.min.js` (pixi.js 6.5.10, MIT), `unsafe-eval.min.js` (@pixi/unsafe-eval
  6.5.10, MIT — despite the name it *removes* pixi's need for `eval`),
  `cubism4.min.js` (pixi-live2d-display 0.4.0, MIT, embeds the Live2D Cubism
  Framework under the Live2D Open Software License) — from jsDelivr, pinned
  by SHA-256.

Terms: https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html,
https://www.live2d.com/en/sdk/license/expandable/

## Not shipped either

The Neuro-sama model is not freely redistributable and is not included. Users
import only models they have rights to (.vrm, Live2D .zip or folder).
