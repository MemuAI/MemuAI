---
title: Bridge reference
summary: Every method, argument and answer in one table.
---

# Bridge reference

## Requests

| Method | Arguments | Answer |
| --- | --- | --- |
| `Memu.request` | `{model, instructions, text, images, documents, history, keep_in_chat, thinking, web_search, temperature}` | `{text, model, images, documents, keep_in_chat, chat}` |
| `Memu.stream` | the same plus `{onDelta, onStatus, onDone, onError}` | a promise with `cancel()` |
| `Memu.models` | — | `[{id, name, provider, image, vision, thinking, web_search}]` |
| `Memu.history` | — | `{id, title, messages}` |

## Media

| Method | Arguments | Answer |
| --- | --- | --- |
| `Memu.image` | `{prompt, model, aspect, images}` | `{urls, ms}` |
| `Memu.video` | `{prompt, model, aspect, images}` | `{urls, ms}` |
| `Memu.speak` | `{text}` | `{audio, mime, spoken_text}` |
| `Memu.music` | `{action: "models"}` or `{action: "generate", request_id, model, prompt, style, lyrics, duration, tempo, instrumental}`; cancel with `{action: "cancel", request_id}` | `{models}` or `{audio, mime, model, provider}` |
| `Memu.transcribe` | `{audio}` | `{text, postprocessed, postprocess_error}` |
| `Memu.record.start` / `stop` / `cancel` | — | `start`: `{recording, started}`; `stop`: `{audio, mime, bytes, ms}` |

## Data

| Method | Arguments | Answer |
| --- | --- | --- |
| `Memu.extract` | `{name, data}` | `{name, text, chars, truncated}` |
| `Memu.download` | `{name, text}` or `{url, name}` | `{path, name, bytes}` |
| `Memu.handoff` | `{prompt, images, code, files}` | `{prompt, images, files, readme, saved, paths}` |
| `Memu.copy` | `text` | `{ok}` |
| `Memu.open` | `{url}` | `{ok}` |
| `Memu.notify` | `{title, text}` | `{ok}` |
| `Memu.search` | `{query, limit}` | `{results, count}` |
| `Memu.fetch` | `{url, limit}` | `{text, title, url}` |

## Environment

| Method | Arguments | Answer |
| --- | --- | --- |
| `Memu.save` | `{key: value}` | the full saved state |
| `Memu.routes` | — | `{routes}` |
| `Memu.env()` | — | language, theme, accent, density, scale, platform |
| `Memu.resize` | `{width, height}` | the new window policy |
| `Memu.log` | `text, level` | — |
| `Memu.close` | — | — |

## Events

| Event | Data |
| --- | --- |
| `theme` | `{theme, accent_name, tokens, surface, density}` |
| `host-event` | an arbitrary host event |
| `reload` | `{reason}` |

## Stream statuses

`queued` — accepted; `working` — the provider is processing; `streaming` — text is flowing; `done` — finished.

## Log levels

`info`, `warn`, `error`. Level `error` reaches the MemuAI log.

## Timeouts (milliseconds)

| Action | Timeout |
| --- | --- |
| `request` | 300 000 |
| `stream` | unlimited |
| `image` | 900 000 |
| `video` | 1 800 000 |
| `speak` | 180 000 |
| `music` | 700 000 |
| `monitor` | 180 000 |
| `transcribe` | 600 000 |
| `extract` | 120 000 |
| `download` | 180 000 |
| `search`, `fetch` | 60 000 |
| `save`, `models` | 20 000 |
| the rest | 5 000–30 000 |
