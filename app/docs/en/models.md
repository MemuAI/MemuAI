---
title: Models
summary: Choosing a model, pinning, the default model, custom names, thinking and image models.
---

# Models

The MemuAI model list holds every enabled model of all your accounts in MemuAI Proxy. The model is chosen per chat and can be changed in the middle of a conversation.

## Choosing a model

The button with the model name in the top bar (++Ctrl+M++) opens the list. At the top is a search field: type part of a name, matches are highlighted, ++↑++ ++↓++ and ++Enter++ pick one.

Every model is shown with a **readable name** and, under it in small type, its full id: “Claude Opus 4.6 Thinking” and `aws9-claude-opus-4.6-thinking`. MemuAI builds the name itself, for new models too: it drops the connection prefix (`aws9-`, `openai/`), writes versions as `4.6`, spells brands the usual way (GPT, GLM, Qwen, DeepSeek), shows a snapshot date separately and `:free` as a **free** badge. A name of your own (see “Rename”) always wins. The same model can be available through several services — each is its own row, grouped by connection.

The list is the same everywhere a model is picked — the chat, Settings, the card of a failed generation: search by name, id and account, the **Vision**, **Reason**, **Free** and **Generation** filters, “Pinned” and “Recent” sections. Badges on the right: an eye — vision (see below), a brain — reasoning, a wrench — tools, “Images”/“Video” — generation models. Models switched off in MemuAI Proxy are not shown anywhere.

The list refreshes by itself after you add an account in the Proxy and when you return to the MemuAI window.

The same readable names (and your renames) appear in every other window too, with the full id beside them in muted monospace: in [Quick Ask](doc:quick-ask) (++Alt+M++; search works by name and by id), in the [Telegram](doc:telegram) bot and Mini App, in MemuCode's `/model` ([MemuCode](doc:memucode-cli)), in MemuNote, and in the model catalog and model card of [MemuAI Proxy](doc:proxy).

## The model menu

Right-click a model in the list:

| Action | What it does |
| --- | --- |
| Pin / Unpin | pinned models stay at the top of the list |
| Make default | this model is chosen for new chats |
| Rename | your own label instead of the technical name; double-clicking the model button does the same |
| Vision: … | set by hand whether the model sees images, or test it (see below) |
| Hide from the list | the model disappears from the list; bring it back in **Settings → Models** |

Your label exists only in MemuAI; requests to the service still use the real name.

**Automatic pinning** (Settings → Models) keeps one main chat model of each connected service at the top. If you unpin such a model by hand, MemuAI remembers it and does not pin it again.

## What a model can do

Models differ. The toggles in the top bar appear only when the current model supports them:

| Capability | How you see it | More |
| --- | --- | --- |
| Web search | the search toggle | [Web search](doc:web-search) |
| Thinking | the thinking toggle and depth choice | below |
| Vision | the model accepts images | [Images](doc:images) |
| Image and video generation | drawing models are marked in the list | [Pictures and video in the chat](doc:media) |

## Thinking

Reasoning models (Claude, GPT-5 and newer, Gemini 3, DeepSeek, Qwen, GLM, Kimi and others) can "think" before answering. The depth is a level:

| Level | When it fits |
| --- | --- |
| Auto | the model decides — nothing is sent and the provider's default applies; this is the default |
| Off | no reasoning — faster and cheaper (only where the model allows it) |
| Minimal, Low | quick short answers |
| Medium | everyday tasks |
| High, Extra high, Max | hard reasoning, code, maths; the answer takes longer |

**Every model offers only its real levels.** Models accept different sets, and a service rejects an unsupported level with an error. So the **Thinking** chip in the composer shows exactly what the chosen model can do, and next to "Auto" what the model does by itself ("Auto · medium"). Examples:

| Model | Levels in MemuAI |
| --- | --- |
| Gemini 3.7 / 3.8 Flash | Low, Medium, High |
| Gemini 3.5 / 3.6 Flash | Minimal, Low, Medium, High |
| GPT-6.1 Sol | Low … Max (no "Off" and no "Minimal") |
| GPT-5.2 and newer | Off, Low … Extra high |
| Claude Opus / Sonnet 4.6 | Off, Low, Medium, High, Max; "…-thinking" models have no "Off" |
| DeepSeek V3.1 / V4 | Off, Low, High, Max |
| GLM-5.x | Low, High, Max — reasoning cannot be switched off |
| Qwen3, Kimi | Off / On (or several levels where the model supports them) |
| GPT-4o and other non-reasoning models | no chip |

For an unknown new model the set is guessed from its family; if a model sends reasoning but had no levels, the common "Low / Medium / High" set appears. If a service still rejects a level, MemuAI repeats the request without it, remembers the rejection and stops offering that level.

A saved level the new model does not have is not lost: the model gets its **nearest** one (for example, "Minimal" on Gemini 3.7 Flash becomes "Low"). The level is changed in a chat with the thinking chip. Defaults are set in **Settings → Models**: **Reasoning effort** — the general one (each model gets its nearest supported level) and **Reasoning by model** — a level for particular models, each with only its own choices. The chat setting always wins. The same levels appear in [Telegram](doc:telegram) (level buttons on the model screen, a chip in the Mini App) and [MemuCode](doc:memucode-cli) (`/effort`).

**Setting it by hand.** If a service serves a model with different levels than expected, open its card in MemuAI Proxy (**Model catalog** → the ⓘ button) → **Reasoning levels**: it shows what clients offer, the provider default, where that is known from and how the level is sent to the API. **Set by hand** — tick the levels, allow "Off", pick how to send it; **Back to automatic** — drop the manual setting; **Forget the API rejections** — offer again the levels a service once rejected.

A model's reasoning appears as a collapsed block above the answer.

## The default model

**Settings → Models → Default model** — the model a new chat starts with. "First available" takes the first model in the list.

## Images, video and decisions

**Settings → Models → Images, video and decisions** picks the image model, the video model (automatic, “only by command” or a specific one) and an optional fast **decisions model** that decides whether to answer with text, a picture or a video. **Autoprompt** is there too. The chat model stays the same. Details in [Pictures and video in the chat](doc:media).

## Does a model see images

Every chat model has one answer to this question, the same in every window. The eye next to the name (in the list and in the top bar):

| Badge | Means | The tooltip says where it comes from |
| --- | --- | --- |
| Eye (green) | sees images | tested with a request, set by you, the provider or the model family |
| Crossed-out eye | cannot see | e.g. “this provider cannot pass images” |
| Eye with a question mark | unknown | no information — the model can be tested |

The answer is taken in order: your test → your setting → the provider and the catalogue → the model family. Set it by hand in the model menu (**Vision: …** → “Automatic” / “Sees images” / “Cannot see images”) or on the model card in MemuAI Proxy — it is the same setting. **Test now** sends the model one tiny picture (uses one request) and remembers the result.

When you attach a picture to a model that cannot see it (or when that is unknown), a note with **Switch to …** and **Test the model** appears above the message box. On sending, MemuAI offers:

| Option | What happens |
| --- | --- |
| Switch to a vision model | MemuAI suggests the best vision model (you can pick another); the message goes to it with the picture and the chat continues with it |
| Describe the picture with a helper | a helper vision model privately describes the picture, the current model receives the description |
| Test the model now | one real request; if the model sees images, the message goes directly |
| Send anyway | the picture goes as is — the model may ignore it |

A model that sees images gets the picture at once, without questions. The “Describe with another model / Remove images” buttons under an error appear only when the error is about the picture — an ordinary service failure shows “Try again”.

## How images are sent

Some services silently ignore attached pictures. **Settings → Images & video → Sending images** decides what to do:

| Option | What happens |
| --- | --- |
| Ask every time | vision models get the picture at once; for the others — the choice described above |
| Send directly | the picture goes to the model as is |
| Private vision bridge | the "image recognition model" first describes each picture and the chat model receives the description. Works with any model, though small details can be lost |

## Local models

Models with the `local-` prefix run on your computer through [Local models](doc:local-models) in the Proxy. The first answer after choosing one can take up to a minute while the model loads into memory.

## The model in a meme chat

A [meme](doc:agents) can have its own fixed model. In its chats the model cannot be changed — a lock note appears in place of the picker.

See also: [Accounts](doc:accounts), [Context](doc:context).
