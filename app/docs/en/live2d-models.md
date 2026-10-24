---
title: Your own character models (Live2D and VRM)
summary: In plain words — what a Live2D model is, where to get one legally, which files you need and how to make it the companion in MemuAI Live and Quick Ask.
---

# Your own character models

The companion in MemuAI Live and Quick Ask can look like anyone: instead of the built-in girl Memu you can use your own character. This page walks you through where to find one, what to download and how to add it. The other companion settings are on the [Companion](doc:companion) page.

## What a Live2D model is

**Live2D** is an "animated drawing". An artist draws the character as an ordinary 2D illustration, only in pieces: the eyes, brows, mouth, hair and arms separately. Then, in the Live2D Cubism program, the pieces are tied together with "strings" called parameters. The "mouth open" parameter stretches the mouth, the "head turn" parameter shifts the face and hair a little. That is how a flat drawing can blink, breathe, turn and talk — it is how most 2D VTubers look.

A finished model is not one picture but **a folder of several files**: the main description file (`*.model3.json`), the model's "mechanics" (`*.moc3`), textures (`.png` images) and, optionally, motions, facial expressions and hair physics. MemuAI takes the whole folder and moves the parameters itself: it opens the mouth along with the voice, turns the head towards the cursor and smiles when the answer is happy.

## Live2D or VRM

MemuAI understands two kinds of models.

| | Live2D | VRM |
| --- | --- | --- |
| Looks like | a flat drawing that moves | a 3D figure |
| Files | a folder or a `.zip` with `*.model3.json`, `.moc3` and textures | a single `.vrm` file |
| Versions | Cubism 3, 4 and 5 | VRM 0.x and 1.0 |
| What else you need | download the **Live2D runtime** once (see below) | nothing, works right away |
| Where to look | official Live2D samples, BOOTH, nizima | VRoid Hub, BOOTH, your own model from VRoid Studio |

The built-in **Memu** model is a VRM. Old **Cubism 2** Live2D models (the main file is `*.model.json`, without the 3, and the mechanics file is `.moc`) are not supported.

## Where to find models

A character model is someone else's work: it always has an author and terms of use. Before downloading, read what is allowed: personal use is usually fine, while reselling, re-uploading or using it in commercial projects is not. A companion on your own computer only needs personal use.

### Official Live2D samples

The easiest and safest start. On the [Live2D Sample Data](https://www.live2d.com/en/learn/sample/) page Live2D gives away about twenty models for free: Hiyori, Haru, Mark, Epsilon, Natori, Rice and others. Click a model, accept the terms and download the archive.

The terms are in the [Free Material License Agreement](https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html) and the [Terms of Use for Sample Data](https://www.live2d.com/en/learn/sample/model-terms/). In short: individuals and small companies may use Live2D's original characters, some of them have extra rules (for example, Hiyori's design must not be changed), and "collaboration" characters (Natori, Tsumiki) must not be altered, redistributed or used commercially. Hatsune Miku and Unity-chan follow their owners' guidelines.

For a first try, **Hiyori Momose** and **Haru** are good picks: ordinary waist-up models with standard parameters, and Haru also has facial expressions.

### BOOTH

[BOOTH](https://booth.pm/) is a Japanese marketplace for artists' work with many Live2D models, including free ones. Search for `Live2D 無料` ("free") or `Live2D モデル 無料配布` and look for the price "¥0". Descriptions are usually in Japanese — run them through a translator and find the terms section (利用規約). Downloading, even a free model, usually requires signing in with a pixiv account.

Many VTuber models are made for VTube Studio — they work in MemuAI too: they are ordinary Cubism 3–5 models, and the extra `*.vtube.json` file is simply not used.

### nizima

[nizima](https://nizima.com/) is the official model store run by Live2D Inc. itself. It mostly sells paid models with clear terms: each model's page says where and how it may be used. Buyers download an ordinary folder with a `*.model3.json`.

### VRoid Hub (for VRM)

If you would like a 3D character, have a look at [VRoid Hub](https://hub.vroid.com/). A model can only be downloaded if its author enabled "Allow download"; the terms of use are shown on the model's page. You can also build your own VRM character for free in [VRoid Studio](https://vroid.com/en/studio) and export it as `.vrm`.

### What to avoid

- **"Leaked" models of famous VTubers and game characters.** They belong to someone else, even when they are freely posted on GitHub or Discord. The Neuro-sama model, for example, is not freely distributed.
- **GitHub collections with no authors or licences.** If it is unclear who made a model and what you may do with it, better skip it.
- **`.exe` files and "model installers".** A model is always just images and `.json`/`.moc3`/`.vrm` files; there is nothing to run.

## Which files you need

A downloaded archive often contains extras: the editor source (`.cmo3`), Photoshop layers (`.psd`), instructions. MemuAI needs only the folder with the **ready-to-run** model — usually called `runtime` or named after the model. It is easy to spot: it contains a `*.model3.json` file.

```text
my_model/                     ← import this folder
├── my_model.model3.json      ← main file: the model's "table of contents"
├── my_model.moc3             ← the model's mechanics (required)
├── my_model.physics3.json    ← hair and clothes physics (optional)
├── my_model.pose3.json       ← which arms to show (optional)
├── my_model.cdi3.json        ← parameter names (optional)
├── my_model.4096/
│   ├── texture_00.png        ← textures (required)
│   └── texture_01.png
├── expressions/
│   ├── smile.exp3.json       ← facial expressions (optional)
│   └── sad.exp3.json
└── motions/
    ├── idle_01.motion3.json  ← motions (optional)
    └── tap_body.motion3.json
```

The main `*.model3.json` file is a text "table of contents": it records where the `.moc3`, textures, motions and expressions are. So do not rename or rearrange the files — the model would no longer find them.

On import MemuAI checks that every required file is present and refuses suspicious archives. Limits:

| What | Limit |
| --- | --- |
| a `.vrm` file, `.zip` archive or folder | up to 200 MB |
| an archive once unpacked | up to 400 MB |
| files in an archive | up to 3000 |
| models in the library | up to 60 |

## How to add a model

### Step 1. Open the companion settings

The model is chosen in the same block and applies to both MemuAI Live and Quick Ask:

- for MemuAI Live — **Settings → Voice & Live → Companion**;
- for Quick Ask — **Settings → Quick Ask → Features → Companion**.

Turn on the switch at the top of the block, or the character will not appear.

### Step 2. Install the Live2D runtime

Skip this step for VRM. For Live2D, expand **Advanced → Live2D runtime**, read the terms behind the links, tick the consent box and press **Download the Live2D runtime**. You do this once, and it needs the internet.

Why the extra step: Live2D models are shown by the Cubism Core library of Live2D Inc. It cannot be built into MemuAI without a separate agreement, so MemuAI downloads it from Live2D's official site only after you agree. More in the [Live2D runtime](doc:companion#the-live2d-runtime) section.

### Step 3. Import the model

There are two buttons under the character preview:

- **Import a Live2D folder** — pick the folder that contains the `*.model3.json` (the folder itself, not a file inside it). MemuAI packs and checks it. This is the most convenient way.
- **Import .vrm or .zip** — pick a `.vrm` file or a `.zip` archive with a Live2D model.

A few seconds later a new card appears under "Model". If something is wrong, MemuAI says what is missing — the messages are explained in [When something is wrong](#when-something-is-wrong).

> [!TIP]
> If an archive holds **several models** (a pair of characters, for example), MemuAI takes the one whose `*.model3.json` is closest to the archive root. To choose a specific one, unpack the archive and import that model's folder.

### Step 4. Select and adjust

Click the card — the character changes in the preview and everywhere the companion is on. Then:

- **Framing**: "Waist up" shows the upper part of the character, "Full body" shows all of it. MemuAI measures the character itself, not the model's canvas (there is often a lot of empty space around the drawing), and leaves margins: in "Waist up" the sides and sleeves are not cut off. If a gesture or motion reaches past the frame, the frame grows smoothly.
- **Size** — a slider from 60 to 200 %, plus the corner or ++Ctrl++ + wheel over the character.
- The **Wave**, **Nod**, **Happy**, **Sad**, **Surprise** and **Say a line** buttons test the model without sending anything.

Models are stored in `%APPDATA%\MemuAI\memuai\companion\models\`, one folder per model. The cross on a card removes the model from the library.

## How the model comes alive

MemuAI does not draw animation itself — it moves the same "string" parameters the artist set up. So the result depends on how the model was made.

| What happens | Where it comes from | What the model needs |
| --- | --- | --- |
| breathing, blinking, hair physics | the Live2D runtime from the model's data | the `ParamBreath` parameter, the `EyeBlink` group in `model3.json`, a `physics3.json` file |
| calm movement while the character is idle | motions from the `Idle` group | `*.motion3.json` files in the `Idle` group (`idle` or `Idle_1` work too) |
| looking at the cursor | MemuAI turns the head, eyes and body | the standard `ParamAngleX/Y`, `ParamEyeBallX/Y`, `ParamBodyAngleX` parameters |
| lips following the voice | MemuAI opens the mouth from the loudness and sound of speech | the `ParamMouthOpenY` and `ParamMouthForm` parameters |
| emotions of the answer | a facial expression with a matching name, plus brows and smiling eyes | expressions named like `smile`, `happy`, `sad`, `angry`, `surprise`; the `ParamEyeLSmile`, `ParamBrow…` parameters |
| nod, wave, thinking | MemuAI tilts and turns the head and body itself | the standard `ParamAngleX/Y/Z`, `ParamBodyAngleZ` parameters |
| reaction to a click on the character | one "touch" motion, then back to `Idle`. Motions from files play **only on a click** — the nod, wave and thinking pose use parameters, so an arm never gets "stuck" raised | a `TapBody` group (or another with `Tap`/`Flick` in its name) **and** an `Idle` group |

More on each row:

- **Lips.** In MemuAI Live the mouth follows the real sound of the speech, or the text when there is no sound. Live2D models with Motion-sync work like any other: MemuAI drives the mouth itself.
- **Emotions.** The answer is tagged with one emotion: joy, sadness, anger, surprise, calm or thinking. For joy MemuAI looks for an expression whose name contains `happy`, `smile`, `joy`, `fun` or `f01`; for sadness `sad`, `sorrow`, `cry`; for anger `angry`, `mad`; for surprise `surprise`, `shock`; for calm `relax`, `calm`; for thinking `think`. Without a matching expression the emotion still shows in the brows and eyes — if the model has the standard parameters.
- **Gaze.** Works when **Advanced → Follow the cursor** is on: the character turns its head and eyes towards the cursor. The direction is taken from the character's face: a cursor below the eyes makes it look down, above — up. On Windows the character follows the cursor **across the whole screen**, even when the mouse is outside the MemuAI window. With the switch off it looks straight ahead.
- **Reduced motion.** If reduced animation is on in Windows or MemuAI, the character barely sways.

Almost every model built on Live2D's template (the Live2D samples and most VTuber models) uses the standard parameter names, so everything works without setup.

## When something is wrong

| What you see or what MemuAI says | Why | What to do |
| --- | --- | --- |
| "The Live2D runtime is not installed" | the Cubism Core library has not been downloaded | **Advanced → Live2D runtime → Download the Live2D runtime** |
| "No *.model3.json file in the archive" / "The folder has no *.model3.json file" | the wrong folder was picked, or it is not Live2D | find the folder with `*.model3.json` (often `runtime`) and import it |
| "This is a Cubism 2 model" | an old-format model, `*.model.json` + `.moc` | not supported; look for a Cubism 3+ version or re-save the model in a modern Cubism Editor |
| "A file the model refers to is missing" | files were renamed or lost, or the archive has Japanese file names in an old encoding | unpack the archive (for example with 7-Zip) and use **Import a Live2D folder** — names are kept correctly that way |
| "The archive is password-protected" / "compressed with an unsupported method" | a password-protected `.zip`, or a `.rar` or `.7z` | unpack it and import the folder |
| "Larger than 200 MB" | `.cmo3` and `.psd` sources are next to the model | import only the folder with the `*.model3.json` |
| an empty spot or the orb instead of the character | the companion is off, WebGL is missing or the model failed to load | check the switch for this place; read the message in the preview |
| a black rectangle around the character on the desktop | Windows or the driver does not support a transparent window | choose **Inside the Quick Ask window** |
| only the head is visible or the character is cut off | the model has unusual proportions or no `Head` area for the face | set **Framing** to "Full body" and lower **Size** |
| an arm is raised and never comes down | the model has no idle motion group (`Idle`, `idle`, `Idle_1`) | without it MemuAI plays no motions from files at all; add an `Idle` group in Cubism Editor or pick another model |
| the character is too small | the model has a lot of empty space around it | **Framing** "Waist up", a larger **Size**, or drag the corner |
| two pairs of arms at once | the model lost its `pose3.json` file (it picks which arms to show) | download the whole model again and import the folder as it is |
| the lips do not move | **Lip sync with the voice** or spoken answers are off; the model has a non-standard mouth parameter | turn both on; compare with the Hiyori or Haru sample |
| no facial emotions | the model has no expressions with recognisable names | that is fine: brows and eyes still react if the model has the standard parameters |
| the character stutters | a heavy model with large textures | **Low power** or **30 FPS** |

## FAQ

**Can I use a photo or an ordinary picture?** No. A picture first has to be cut into pieces and assembled in Live2D Cubism Editor, which has a free version. That is an artist's job, not one button.

**Are 3D models from VRChat, MMD (`.pmx`) or `.fbx` supported?** No, only VRM. Many models can be converted to VRM in Unity with the UniVRM plug-in, but only if the model's licence allows it.

**I have an old Cubism 2 model. What now?** Ask the author for a Cubism 3+ version — its main file is called `*.model3.json`. MemuAI will not open the old format.

**Do I need a paid version of Live2D?** No. Using ready-made models costs nothing: the Live2D runtime downloads for free, and its terms are behind the links next to the **Download the Live2D runtime** button.

**Is my model sent to the internet?** No. The model is stored and shown only on your computer. MemuAI goes online once — for the Live2D runtime, and only after you agree.

**The model I picked in one place shows up in the other — is that intended?** Yes. The model choice is shared by MemuAI Live and Quick Ask; only the companion itself and where it is shown are set separately for each place.

**Can I share a model together with my MemuAI build or theme?** Only if the model's licence allows redistribution. The Live2D samples and most BOOTH models may not be redistributed — share a link to the author's page instead.
