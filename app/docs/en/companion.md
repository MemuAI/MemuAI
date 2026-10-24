---
title: Companion (animated character)
summary: An animated character for MemuAI Live and Quick Ask — VRM and Live2D, lip sync, emotions, gestures, a separate desktop window.
---

# Companion

The companion is an animated character that talks to you in place of the MemuAI Live orb and beside Quick Ask answers. It breathes, blinks, follows the cursor (on Windows across the whole screen, not only over the window), moves its lips with the voice, shows the emotion of the answer and makes simple gestures: waves when opened, looks thoughtful while the model is thinking, nods when the answer starts.

The companion is off by default. It is switched on separately for each place:

- **MemuAI Live** — **Settings → Voice & Live → Companion**;
- **Quick Ask** — **Settings → Quick Ask → Features → Companion**: the switch and “Where to show it” on top, then “Character” (preview, model, import), size and framing; lip sync, emotions, gestures, frame rate, low power and the Live2D runtime are under “Advanced”.

The other options (model, size, emotions and so on) are shared by both places.

## Where it appears

| Place | Option | What it looks like |
| --- | --- | --- |
| MemuAI Live | **In the Live panel** | the character takes the orb's place in the Live window |
| MemuAI Live | **Over the MemuAI window** | the character floats over the window: drag it anywhere, resize it by the corner or with ++Ctrl++ + wheel |
| Quick Ask | **Inside the Quick Ask window** | a small character above the answer |
| Quick Ask | **Separate desktop window** | a transparent window above every program (experimental, see below) |
| Quick Ask | **Both** | both at once |

If the character cannot be shown (the graphics card has no WebGL, the model file is broken), MemuAI Live simply shows its usual orb — the conversation does not depend on it.

## Models

One model is included — **Memu (HairSample_Female)**, a VRoid Studio sample under the CC0 licence. Add your own with the buttons below the preview:

| Format | How to add | What it needs |
| --- | --- | --- |
| VRM 0.x and 1.0 (`.vrm`) | **Import .vrm or .zip** | nothing, works straight away |
| Live2D Cubism 3–5 (`.zip`) | **Import .vrm or .zip** | an archive with `*.model3.json`, its `.moc3` file and textures; needs the Live2D runtime |
| Live2D Cubism 3–5 (folder) | **Import a Live2D folder** | the same, without packing it into an archive |

Models are kept in the data folder: `%APPDATA%\MemuAI\memuai\companion\models\`. Every file is checked before import: a VRM must be a real VRM file without links to outside addresses, a Live2D archive must have no paths outside its folder, no links and no "zip bombs". Limits: files up to 200 MB, an unpacked archive up to 400 MB. Click a card to choose the model; the cross removes it from the library (the built-in model cannot be removed).

Use only models you have the rights to. The Neuro-sama model is not freely distributable, so MemuAI does not include it.

Where to find models legally, which files from a downloaded archive you need and how a model moves — step by step on the [Your own character models](doc:live2d-models) page.

## The Live2D runtime

Live2D models run on the **Live2D Cubism Core** library. It is a proprietary library of Live2D Inc., and Live2D's licence classes programs that load arbitrary user models as "Expandable Applications", which need a separate agreement with Live2D to be distributed. That is why MemuAI **contains nothing** from Live2D.

To use Live2D models, open **Companion → Live2D runtime**, read the terms behind the links, tick the consent box and press **Download the Live2D runtime**. MemuAI downloads Cubism Core from Live2D's official site and the free pixi.js 6.5.10 and pixi-live2d-display 0.4.0 libraries (their checksums are pinned) into `companion\runtime\` in your data. **Remove the Live2D runtime** deletes those files. VRM models do not need the Live2D runtime and always work offline.

## Voice, lips and emotions

- **Lip sync with the voice.** The mouth follows the real audio: loudness and spectrum are matched to vowels. Realtime Live models use their audio stream, classic Live uses the spoken answer. Without audio the lips follow the text.
- **Emotions from the answer.** In classic Live MemuAI can ask the model to start its reply with a hidden tag such as `[emotion:happy]`. The tag is removed before display and speech — you never see or hear it. Without a tag the emotion is guessed from the answer's words and emoji. Available: neutral, happy, sad, angry, surprised, relaxed, thinking. Turn off **Ask the model for emotion tags** if you do not want the model's instructions changed.
- **Speech bubble** shows what the character says when it is apart from the answer text (over the window or in the separate window).
- **Character from the personality** — liveliness and a light smile follow the chosen Live personality.

The buttons below the preview (**Wave**, **Nod**, **Happy**, **Sad**, **Surprise**, **Say a line**) let you try the model without sending anything.

**Framing and gestures.** The frame is fitted to the character itself, with margins, so that a wave and the thinking pose (a hand at the chin) fit entirely. For VRM models MemuAI "tries on" these gestures when loading and leaves room for them; for Live2D the "Waist up" frame never cuts off the sides, and if a motion reaches past the frame, the frame grows smoothly.

## The separate desktop window

With **Separate desktop window** the character lives in its own transparent window in the bottom-right corner of the screen, above other programs. The window never takes focus and has no taskbar button, and clicks on the empty space around the character go to the windows below. Drag the character to move it; the **−** and **+** buttons in the corner change its size (or ++Ctrl++ + wheel), the pin keeps it there, “back to the window” docks it into Quick Ask and **×** hides the window. The position is remembered for each monitor.

From the Quick Ask window you can simply **drag the character out**: inside the window it snaps left, centre or right, and dragged past the window edge it becomes a separate desktop window right under the cursor. Inside the window, resize it by the corner or ++Ctrl++ + wheel.

This is experimental: transparency depends on Windows and the graphics driver. If you see a black rectangle instead of a transparent background, or the window gets in the way, choose **Inside the Quick Ask window**.

## Performance

- **Frame rate** — 30 or 60 frames per second. While the character is hidden (Live minimised, window closed) the animation stops completely.
- **Low power** — no antialiasing, standard pixel density and at most 24 frames per second. Useful on a laptop running on battery.
- **Reduced motion.** If Windows animations are turned off or MemuAI is set to reduce motion, the character moves much more calmly and does not wave.
- **Compatibility mode.** Some graphics drivers "lose the WebGL context" while animating the model. After two such failures the companion switches to compatibility mode by itself (the skeleton is computed on the CPU) — slightly slower, but it works.

## If something is wrong

| What you see | What to do |
| --- | --- |
| the orb instead of the character | check that the companion is on for this place; very old graphics cards may have no WebGL |
| a Live2D model does not load | install the Live2D runtime (section above); check that the archive contains `*.model3.json`; the full table is in [Your own character models](doc:live2d-models#when-something-is-wrong) |
| an import error saying the file is plain glTF or not a VRM/GLB | it is a plain `.glb` without VRM data or a broken file; export the model from VRoid Studio again |
| an import error about a Cubism 2 model | only Cubism 3–5 (`*.model3.json`) is supported; re-save older `*.model.json` models in a current Cubism Editor |
| the lips do not move | turn on **Lip sync with the voice** and spoken answers in [Voice and MemuAI Live](doc:voice) |
| the character stutters | turn on **Low power** or choose 30 FPS |

For Quick Ask itself, see [Quick Ask](doc:quick-ask).
