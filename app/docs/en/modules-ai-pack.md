---
title: AI pack: modules written by AI
summary: One file with everything an AI agent needs to write a MemuAI module; validating and importing the answer.
---

# AI pack: modules written by AI

You do not have to write a module yourself. The **AI pack** is one text file with everything any AI agent (MemuAI, MemuCode, ChatGPT, Claude and others) needs to write a working module on the first try.

```memu-widget
ai-pack
```

## What is in the pack

The pack is assembled from the code of your MemuAI version at download time, so it never goes stale:

- **instructions for the AI** — how to send the files and what is not allowed;
- **the `Memu` bridge reference** — every method with its timeout and permission, events, translation attributes, `mu-*` classes and design tokens;
- **the sandbox rules** — the module document's Content-Security-Policy, size limits and manifest fields;
- **every article of the "Modules" group** — including pages added later;
- **working examples** — notes, a mini chat with a model and a document brief — and the wizard templates;
- **common mistakes** and a **checklist** before sending;
- **the test stand** `memu-module-test.js` (in the full variant).

| Variant | Use it for |
|---|---|
| Full | An AI agent that can run code: it checks the module with the stand itself |
| Compact | A regular chat: the file is about half the size, without the stand |

The pack language (RU or EN) only changes the explanations: the module gets translations to both languages either way.

## How to use it

1. Download the pack with the button above.
2. Attach the file to an AI agent and write, for example: "Make a MemuAI module — a habit tracker with reminders. The instructions are in the file."
3. Copy the whole answer and paste it into **Settings → Modules → Import → Validate and import**.
4. If the check finds errors, press "Report for the AI" and send the report to the agent — it fixes the files.
5. When there are no errors, press "Import". The module appears in the list; open it right away or polish it in the [workbench](doc:modules-workbench).

> [!TIP]
> You can ask MemuAI itself: attach the pack to a message in a regular chat.

## Validate and import

The card on the **Import** tab takes a package in any form:

- **an AI answer** — files in code blocks, the file name above the block or in its header (`manifest.json`, `index.html`, `style.css`, `main.js`, `i18n.json`);
- **a JSON package** `.memu-module.json` (an export from MemuAI);
- **a ZIP** with the module folder — the files may sit in a sub-folder;
- **the module folder** itself.

The check uses the same rules MemuAI runs modules with: the id and the manifest, permissions and bridge calls, JavaScript syntax, sandbox restrictions (network, `eval`, external scripts), translations and limits. The result is split into **errors** (import is blocked), **warnings** and **hints**, each with its file and line number.

## The test stand

`memu-module-test.js` runs a module's `main.js` without MemuAI: inside is the real `Memu` bridge of your version and mock model answers. It needs Node.js 18 or newer, no packages.

```bash
node memu-module-test.js ./my-module --click-all
node memu-module-test.js my-module.zip --lang en --theme light
node memu-module-test.js my.memu-module.json --fail-network --json
```

Exit code `0` — no errors, `1` — errors found, `2` — the package could not be read. The same check without running code is available from the sources: `python -m app.modules_validate path`.

## If something goes wrong

- **"No manifest.json block found"** — the AI sent the files without names. Ask it to send each file as its own code block with the name above it.
- **The id is taken** — enter a new one in the "New id" field below on the same tab.
- Everything else is in [Modules: troubleshooting](doc:modules-troubleshooting).
