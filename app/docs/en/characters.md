---
title: Characters (roleplay chats)
summary: A built-in SillyTavern-style module — PNG/JSON character cards, Chat Completion presets, lorebooks, personas, swipes and branches.
---

# Characters

The Characters module is a lighter SillyTavern inside MemuAI. Turn it on in Settings → Modules and open it from the sidebar button. Requests go through the MemuAI Proxy, so every connected model works and keys never leave MemuAI.

![The Characters module](docs-img/en/characters.webp "The Characters module: the card list on the left, the chat with a character on the right")

## Characters and cards

- **Create.** Name, avatar, description, first message and alternate greetings, scenario. The Personality & examples and Advanced tabs hold the personality, example dialogues (`<START>`), creator's notes, tags, the main prompt and post-history overrides, the Character's Note at depth, talkativeness, a background and a lorebook.
- **Import.** The Import button or dropping files on the window opens the import dialog: a drop zone, a link field and preview cards — avatar, author, tags, card version, greetings, size in tokens, the embedded lorebook. Nothing is saved until you press Import. It takes Character Card V1/V2/V3 PNG files (`chara` and `ccv3` chunks; with both, V3 wins) and JSON from SillyTavern, chub, RisuAI, Agnai (native and V2), JanitorAI, Pygmalion and TextGen. Every field of the card is kept — other apps' extensions and the V3 fields (`nickname`, `group_only_greetings`, `creator_notes_multilingual`, `source`, dates) included — and goes back out on export. With its checkbox the embedded lorebook (`character_book`) becomes a lorebook linked to the character; without it the book stays inside the card (connect it later with "Make it a lorebook" in the character editor). Lorebooks, presets and `.jsonl` chats import through the same dialog. `.charx` files are not supported.
- **Import by link** — like SillyTavern's "Import from external URL": chub (a link or `author/character`), a chub lorebook (`lorebooks/author/name`), JanitorAI (a link or `UUID_character-…`), Pygmalion.chat (a link or UUID), AICharacterCards (a link or `AICC/author/card`), RisuRealm (a link), Perchance (a link or `Name~hash.gz`) and a direct PNG/JSON link to an allowed host. The source is recognised as you type. MemuAI downloads, not the module (the `net-import` module permission), over public HTTPS only, and asks before the first download from each site. The hosts for direct links live in Settings → Modules → Characters → ⋯ → Import sources (by default `files.catbox.moe`, `cdn.discordapp.com`, `raw.githubusercontent.com`). JanitorAI often sits behind a Cloudflare check — then download the card in a browser and drop the file.
- **Export.** "Export PNG card" saves the avatar with V2 (`chara`) and V3 (`ccv3`) data — SillyTavern, chub, RisuAI and Agnai open it; a linked lorebook is written into it as `character_book`. "Export JSON (V3)" and "Export JSON (V2)" save the card as text. Importing an exported card again gives the same character.
- The gallery has search, tags, favourites and sorting; right-click a character in the list for its menu.
- Characters also show up in the MemuAI global search (the Characters filter): a result opens the module on that character.

## Chat

- The answer appears as it is written; ++Esc++ or the Stop button stop it and keep what was written.
- **Swipes:** the right arrow on the last answer makes a new variant; every variant is kept (++Alt+←++ / ++Alt+→++).
- **Regenerate, continue, impersonate** — in the "+" menu next to the composer and in the chat menu; Regenerate and Continue are also under the last answer.
- Edit any message (double-click or the pencil), delete, move, hide from the prompt, bookmark, "branch from here" — a new chat with the history up to that message.
- The model's thoughts show in a collapsed Thoughts block; inline reasoning between `<think>` and `</think>` is recognised too.
- Attach, paste or drop images — a vision model sees them.
- **A character's chats:** any number per character; rename, duplicate, delete, export and import them as SillyTavern `.jsonl`.
- **Author's Note** — a short text in this chat's prompt: at depth, before or after the main prompt, with an interval.
- **Search** this chat or every chat of the character, the bookmark list, chat variables.
- Composer commands: `/sys`, `/send`, `/sendas`, `/continue`, `/regen`, `/swipe`, `/imp`, `/newchat`, `/note`, `/setvar`, `/hide`, `/help`.

## Model, regenerate and several variants at once

- **The model is right in the composer, bottom left.** The model button opens a searchable list (++Alt+M++): models are grouped by connection, each with a readable name and the full id under it, reasoning and vision badges, context size and a `free` tag. ++↑++ / ++↓++ and ++Enter++ pick, ++Esc++ closes. Only models enabled in MemuAI Proxy and not hidden in MemuAI are listed; if the chat's model gets disabled, the module switches to MemuAI's default model and tells you.
- **Reasoning level** — the button next to the model. It lists only the levels the chosen model really accepts (GPT-6.1 Sol, for example, has no "No reasoning" but has "Extra high"); the model's default level is marked. If the saved level is not supported, the button shows the nearest one — that is what the request will use. A model without reasoning control has no button.
- **Regenerate and Continue** are visible buttons under the last answer (++Alt+R++ / ++Alt+C++).
- **Several new variants at once.** Right-click the "next variant" arrow (or Regenerate) to open a menu: choose how many variants to start in parallel (1–5, remembered) and press Generate. Each variant has its own indicator and can be stopped; finished ones are added as swipes. ++Alt+Shift+R++ does the same without the menu. The menu can also make a left click on the arrow start that many.
- The layers button under the answer opens the same variants menu. With more than one variant it has **Compare variants** — all swipes side by side, pick the best with one click.
- An estimate of the draft's tokens is shown in the composer.

## Creating characters and lorebooks with AI

"Create with AI" is in the character gallery, "With AI" in the lorebook list and "Add with AI" in the lorebook editor. Describe an idea or name a known character ("Megumin from KonoSuba"). Turn on **web search** and the model plans queries while the module finds and reads pages (the sources are listed in the draft). Choose the model, the card language, the number of alternate greetings, a linked lorebook and an avatar (when an image model is connected).

The model drafts a full V2/V3 card: description, personality, scenario, first message and alternate greetings, example dialogues, tags and a system prompt; a lorebook gets entries with keywords. Everything can be edited before saving: each field has "regenerate" and "change with a wish", the avatar can be redrawn or uploaded. "Save character" creates the card and opens a chat; closing the window keeps the draft.

## Presets and prompt building

The prompt is built like SillyTavern Chat Completion: the prompt manager in the preset's order, markers (description, personality, scenario, persona, World Info before/after, example dialogues, chat history), in-chat prompts at depth with a role and an order, card overrides of the main prompt and post-history instructions (with `{{original}}`), squashing system messages, trimming old history to the context size, assistant prefill, continue as a prefill or with a nudge, impersonation.

Reorder the prompt manager by dragging a row (hold and pull): the mouse wheel scrolls the list while you drag, near the edge it scrolls by itself, a line shows where the prompt will go, ++Esc++ cancels.

Five presets ship: **Universal** (the default), **Storyteller**, **Engine**, **Classic** and **Megumin**. Any preset can be edited (a built-in one can be restored), saved as new, exported, and SillyTavern JSON presets can be imported.

A preset sets the context size and response length, temperature, Top P/K/A, Min P, penalties, seed, reasoning effort, names behaviour, World Info/scenario/personality formats, separators, prefills and utility prompts. SillyTavern macros work: `{{char}}`, `{{user}}`, `{{description}}`, `{{persona}}`, `{{random}}`, `{{pick}}`, `{{roll}}`, `{{time}}`, `{{date}}`, `{{idle_duration}}`, `{{lastMessage}}`, `{{getvar}}`/`{{setvar}}` and more.

The token button in the chat header opens the **inspector**: which messages go to the model, how many tokens each prompt takes, which lore entries fired and with which parameters. The count is an estimate; after an answer the provider's real count is shown.

## Lorebooks (World Info)

Lorebooks can be global (the switch in the list), per character (a field in the card), per chat (the chat menu) and per persona. An entry has keys and secondary keys with AND ANY / AND ALL / NOT ANY / NOT ALL logic, `/…/i` regular expressions, constant mode, positions (before/after char defs, top/bottom of Author's Note, before/after examples, in chat at depth with a role, outlet), order, probability, its own scan depth, case and whole-word matching, inclusion groups with weight and priority, sticky, cooldown and delay, recursion (non-recursable, prevent recursion, delay until recursion) and a budget. The "?" button on the lorebook tab explains how they work.

**Formats.** Inside, the module keeps a lorebook as a SillyTavern World Info file (`{"entries": {"0": {"key": […], "keysecondary": […], "comment", "content", "constant", "selective", "selectiveLogic", "order", "position", "disable", "excludeRecursion", "preventRecursion", "delayUntilRecursion", "probability", "useProbability", "depth", "group", "groupOverride", "groupWeight", "scanDepth", "caseSensitive", "matchWholeWords", "automationId", "role", "sticky", "cooldown", "delay", …}}}`), so a SillyTavern lorebook goes through import and export unchanged, unknown fields included. Chub lorebooks (by link or file), `character_book` from V2/V3 cards, NovelAI, Agnai and RisuAI books import too. For a `character_book` the source is kept in `originalData`, as SillyTavern does, so exporting back to `character_book` restores even the fields the module does not understand. Export: SillyTavern World Info, Character Book V2/V3 (chub, RisuAI) or Agnai.

## Personas

A persona is you in the roleplay: a name, an avatar and a description behind `{{user}}`. The description goes in the prompt, above or below the Author's Note, in chat at depth, or nowhere. A persona can be the default, locked to a character or to one chat, and have its own lorebook.

## Look and extras

- Chat style: bubbles, flat or document; avatar shape and size, message width, font and text size, density, colours of speech and actions, a global, character or chat background with dimming and panel blur.
- Quick replies — buttons above the composer; SillyTavern-style regex scripts (display only, prompt only or permanent).
- A backup of every character, chat, lorebook and preset in one JSON, and restoring from it.
- The module keeps its data as files in the MemuAI modules folder (`modules/store/characters`), not in the shared settings.

## Not included

Group chats, SillyTavern extensions, full STscript, text-completion (non Chat Completion) APIs, `.charx` files and RisuAI's embedded assets (emotions, backgrounds inside the PNG): only their `https:` links carry over.
