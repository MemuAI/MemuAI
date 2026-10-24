---
title: Glossary
summary: MemuAI and language-model terms in plain words.
---

# Glossary

**Accent** — the colour of buttons, links and active elements. See [Appearance](doc:appearance).

**Account** — one connection to a model service in MemuAI Proxy: a cookie, a browser sign-in or a key. See [Accounts](doc:accounts).

**Agents** — helpers that do parts of a task in parallel in Work and MemuCode while the main model does its own part; they start only when you ask. See [Agents and memes](doc:agents).

**API** — the way programs talk to each other. The Proxy serves an OpenAI-format API. See [Proxy API](doc:proxy-api).

**Artifact** — a page, diagram or note the MemuCode agent creates apart from the project. See [MemuCode](doc:memucode).

**Companion** — the animated character of MemuAI Live and Quick Ask that talks with the answer's voice. See [Companion](doc:companion).

**Computer Use** — the skill with which a model controls the mouse and keyboard. See [Browser and Computer Use](doc:browser-computer).

**Context compression** — replacing older messages with a structured summary when a conversation nears the window limit.

**Context window** — how much text a model sees at once. See [Context](doc:context).

**Cookie** — the string a website uses to know you are signed in. The Proxy works with services' web versions through it.

**Detector** — the Proxy tab that estimates which model really answers. See [Model detector](doc:model-detector).

**DFY (Do For You)** — an agent that does tasks by itself in its own browser: books, orders, fills in forms and asks for approval before irreversible steps. See [Do For You](doc:dfy).

**Engine (llama.cpp)** — the program that runs local models. See [Local models](doc:local-models).

**GGUF** — the file format of local models for llama.cpp.

**Goal** — a Work mode where the model keeps working until the goal is reached. See [Work](doc:work).

**Hallucination** — a confident but made-up answer. Web search and asking for sources reduce it.

**Hugging Face** — the largest catalogue of open models; local models are downloaded from it.

**Live2D** — a technology for "animated" 2D drawings: the character is drawn in pieces and moved by parameters. A model is a folder with `*.model3.json`, `.moc3` and textures. See [Your own character models](doc:live2d-models).

**MCP (Model Context Protocol)** — a protocol for connecting external tools to the MemuCode agent.

**Meme** — your own assistant with a name, instructions and chats. See [Agents and memes](doc:agents).

**MemuAI Live** — a voice conversation with the model. See [Voice](doc:voice).

**Mention** — `@Web`, `@Browser`, `@file` in a message: a hint to the model. See [Commands](doc:commands).

**Module** — your own app inside MemuAI in HTML, CSS and JavaScript. See [Modules](doc:modules).

**Plan** — a Work mode where the model first makes a verifiable plan.

**Prefix** — the start of a model name that marks the service: `genspark-`, `kiro-`, `local-`.

**Profile** — a separate set of chats, memory and tasks. See [Profiles](doc:profiles).

**Projector (mmproj)** — an extra file that lets a local model see images.

**Provider** — the service models come through: Genspark, Kiro, Antigravity and others.

**Quantisation** — compressing model weights to make them smaller: `Q4_K_M`, `Q8_0` and so on. The smaller the number, the lighter the model and the lower the quality.

**Reasoning (thinking)** — the model's deliberation before answering; shown as a collapsed block.

**Sandbox** — the restricted environment in which MemuCode runs project commands.

**Skill** — a ready instruction for a typical job that the model picks by itself.

**SSE** — a way to deliver an answer in pieces while the model writes it.

**System prompt** — an instruction that sets the model's behaviour for the whole conversation.

**Temporary chat** — a conversation MemuAI does not save and in which the model does not see memory. See [Chats](doc:chats).

**Theme variant** — a ready palette of one mode: Nord, Mocha, Paper and others. See [Appearance](doc:appearance).

**Token** — a unit of text for a model, roughly part of a word. Limits and costs are counted in tokens.

**Access token** — a secret string for signing in to a service (Hugging Face, GitHub). Not to be confused with a text token; stored only on the computer.

**Undo** — returning files changed by a MemuCode session to their original state.

**VRAM** — graphics card memory. A model that fits entirely in VRAM runs fastest.

**VRM** — a format for 3D characters (a single `.vrm` file), for example from VRoid Studio. See [Your own character models](doc:live2d-models).

**Whisper** — the family of speech recognition models MemuAI runs locally.

**Work** — the mode for working with the files of a chosen folder. See [Work](doc:work).
