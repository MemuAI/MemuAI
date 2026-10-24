---
title: Images, video, audio
summary: Media generation, provider errors, and how to show them.
---

# Images, video, audio

## Images

```javascript
const result = await Memu.image({
  prompt: "A coffee shop by the sea, warm light, evening",
  model: "genspark-image/fal-ai/nano-banana",
  aspect: "16:9",
  images: []          // optional references as data:image/...
});
result.urls.forEach((url) => {
  const img = new Image();
  img.src = url;                      // already a data URL the sandbox can show
  stage.appendChild(img);
});
```

The answer is `{ urls, ms }`. Each URL arrives embedded as a data URL, which is why the sandbox can display it — a plain external address would not work.

`aspect`: `auto`, `16:9`, `1:1`, `9:16`, `4:3`, `3:2`.

## Video

```javascript
const result = await Memu.video({
  prompt: "A wave breaking on rocks, slow motion",
  model: "auto",
  aspect: "16:9",
  images: [reference]
});
```

It runs through the same Genspark pipeline as images. Video takes a long time to generate, so keep a visible status and timer in the interface. The bridge timeout for video is 30 minutes.

## Audio

```javascript
const speech = await Memu.speak({ text: "Good morning" });
const audio = new Audio(speech.audio);   // data:audio/...
audio.play();
```

The synthesis route comes from MemuAI settings: local Windows voices, a downloaded model or a TTS API. A module does not need to know which.

```javascript
const text = await Memu.transcribe({ audio: dataUrl });
// { text, postprocessed, postprocess_error }
```

The audio file travels as a data URL, limited to 35 MB, and recognition uses the settings from the Voice & Live section.

## Provider errors

Generation streams, and **the error arrives as its own frame even though the HTTP response is successful**. The bridge already parses those frames and rejects the promise with the provider's real text:

```javascript
try {
  const result = await Memu.image({ prompt: "..." });
  if (!result.urls.length) {
    status.textContent = "The provider returned no image";
  }
} catch (error) {
  status.textContent = error.message;
}
```

If the stream ends before `status: done`, the bridge reports that too — an empty list never becomes a silent success.

> Never substitute a placeholder image for a result. The person must see what the provider actually answered.

## Music API

Connect music APIs in **MemuAI Proxy** accounts, together with keys, models and request schemas. A module chooses a model and sends parameters through the bridge:

```javascript
const { models } = await Memu.music({ action: "models" });
const request_id = crypto.randomUUID();
const result = await Memu.music({
  action: "generate", request_id, model: models[0].id,
  prompt: "Evening piano", style: "ambient", duration: 60,
  tempo: 90, lyrics: "", instrumental: true
});
// result.audio is a data URL; result.mime, result.model, result.provider
// await Memu.music({ action: "cancel", request_id });
```

Permission `media` and an enabled module are required. `action: "config"` returns the same model list; `configure` directs the user to Proxy settings. A saved legacy connection remains only for old-code compatibility and explicit import into Proxy; new modules should provide `model`.

In the Proxy account, JSON templates substitute `{{field_name}}`; numbers and flags retain their type when the placeholder occupies a whole value. The API can return audio, JSON with URL/base64/hex or a polled job (up to ten minutes). Polling URLs with `{id}` must use the same API origin. Audio is limited to 24 MB; private addresses and forwarding credentials between origins are rejected. A blank result path disables that variant. Cancellation stops MemuAI's waiting but does not guarantee cancellation of a provider job already accepted.
