---
title: Pictures and video in the chat
summary: How the AI decides to answer with text, a picture or a video; the image, video and decisions models; autoprompt; the /image and /video commands; generation in Telegram.
---

# Pictures and video in the chat

Just ask in an ordinary chat — “draw a cat astronaut”, “show me what Shrek looks like”, “make a short clip of waves at sunset” — and MemuAI works out that the answer is a picture or a video, not text. You do not need to switch the chat model.

## Setup

Everything is in **Settings → Models → Images, video and decisions**:

![Image and video settings](docs-img/en/media.webp "Models → Images, video and decisions: the image and video models, the decisions model and Autoprompt")

| Setting | What it does |
| --- | --- |
| Image model | draws when a picture is asked for in the chat |
| Video model | creates clips right in the chat |
| Decisions model | an optional fast model that decides: text, picture or video |
| Autoprompt | the chat model writes a detailed prompt before generating |
| Look up appearance on the web | for autoprompt: find how a character, building or product looks |

The image and video models have three choices:

- **Automatic** — the first available model (models pinned in the model list come first);
- **Off — only by command** — the AI never generates on its own, but `/image` and `/video` still work;
- a specific model.

When there are no models, the section tells you where to get them: a Genspark account (AI Image / AI Video) or your own OpenAI-compatible connection in MemuAI Proxy — with an image model (gpt-image, FLUX, Imagen, Seedream, Gemini image) or a video model (Sora-style `/videos`, Veo in Google AI Studio). If a model name is not recognised, list it in the connection's “Image generation models” or “Video generation models” field.

## How the AI decides

**Without a decisions model** the chat model decides itself: it knows which generators are set up and, when you really ask for a new picture or clip, writes one sentence about what it is creating plus a hidden task for the generator. The result appears under its message.

**With a decisions model** most messages need no call at all: fast rules recognise an ordinary question or a clear “draw …” straight away. Only ambiguous ones (“show me the Eiffel Tower”) go to the small model, which answers with one word in a split second. Pick a fast model — they are at the top of the list; **Test speed** runs three examples and shows the average time and accuracy.

Tables, charts, diagrams and code are drawn by MemuAI itself as text — “draw a table of expenses” does not start the generator. Questions about pictures (“what is in the photo?”, “how do I draw a horse?”) get a text answer too.

> [!TIP]
> The decisions model's instruction can be edited in the settings section **Internal prompt contracts → Media router** (see [Memory](doc:memory#memuai-s-internal-prompts); in your own prompt set — the original set is read-only). The model's answer must stay one word: image, video or text.

## The /image and /video commands

- `/image <description>` (or `/img`) — creates a picture right away, with no decision step;
- `/video <description>` — creates a video right away;
- **＋ → Create an image / Create a video** puts the command at the start of the message box — add the description and send.

The command also works with ++Shift+Enter++ before the text (a multi-line description). Pictures attached to the message go to the generator as references. The command uses the chosen model, and when “Off” is selected it still uses the first available one: you asked explicitly.

The format follows the words of the request: “vertical video”, “for stories” — 9:16, “avatar”, “logo” — 1:1, “desktop wallpaper” — 16:9, or simply “16:9”.

## The result

Under the answer there is the picture (a click opens the viewer, see [Images](doc:images)) or a video player with a **Download** button. Videos are saved as profile files and are included in [sync](doc:sync) together with the chats.

Pictures are kept as profile files too. Many services (e.g. `wanx2.1-t2i-turbo`, `grok-imagine-image-2.0`, `gpt-imagine-2.5` via DashScope and similar) return the result as a temporary link that stops working after about a day — MemuAI downloads the picture right after it is generated, so it stays in the chat, downloads, export, Telegram and the Mini App for good. Old chats switch to files by themselves when opened, while the link still works; when it no longer does, an **Image expired** card with a **Regenerate** button replaces the broken icon.

Below it is the collapsed **Prompt** card (or **Enhanced prompt** when autoprompt worked) with the model and what made the decision:

| Button | What it does |
| --- | --- |
| Edit and regenerate | edit the prompt and generate a new version |
| Another variation | the same prompt — one more result in the same answer |
| Copy | the prompt to the clipboard |

Stop a generation with **Stop** in the card, ++Esc++ or the Stop button. “Retry” under the answer generates again. Service errors are shown in plain words (e.g. “The API key was rejected (HTTP 401)”); when a model or a connection is missing, with an **Open model settings** button.

When a generation fails, right under the error there is an **Another model** row: pick any image (or video) model from the shared list and press **Try again with this model** — the request goes to exactly that model. Settings stay as they are. The list has every generation model of every connection, including ones the provider marks as image models (e.g. `wanx2.1-t2i-turbo`).

## Autoprompt

With **Autoprompt** on, the chat model rewrites your request into a detailed prompt: appearance, composition, light, and for video the camera movement and the action. What you asked for is kept word for word, and “quality” words like “8k, masterpiece” are never added. When the request names a specific character, building or product, the model may first look up how it looks on the web (the “Look up appearance on the web” switch). The sources are shown in the prompt card.

Follow-ups are understood from the context: “now in blue”, “make it night” change the previous picture instead of drawing from scratch.

## In Telegram

The [Telegram bot](doc:telegram) uses the same settings:

- `/image …` and `/video …` — create right away; a photo sent with the command as its caption becomes the reference;
- an ordinary “draw …” message is handled as in the window: by the decisions model or the chat model;
- the result arrives as a photo or video (a big file as a document) with the prompt in the caption and a **🔁 Another one** button; **⏹ Stop** is there while it is generating;
- pictures and videos are saved in the MemuAI chat and show up on the computer.

Autoprompt in the bot works without the web lookup.

See also: [Models](doc:models), [Images](doc:images), [Composer](doc:composer).
