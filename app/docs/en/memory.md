---
title: Memory and prompts
summary: Memory shared by every service — sources, confidence, the inbox, rules, «Only for» labels, trash and transfer; the assistant's personality, your instructions and the prompt library.
---

# Memory and prompts

## Memory

MemuAI remembers lasting facts about you: what you do, which language you code in, what you prefer in answers. Memory is shared by every service — the normal chat, Work, MemuCode (in the window and in the [terminal](doc:memucode-cli)), [Quick Access](doc:quick-ask), [Telegram and the Mini App](doc:telegram-miniapp), [Do For You](doc:dfy) and [MemuNote](doc:memunote). Each [profile](doc:profiles) has its own separate memory.

A request does not get every fact: it gets the pinned ones and those most relevant to your question, within the "Memory per request" setting. So a large memory does not make each message more expensive.

### How facts get into memory

- **The model itself.** When you state something lasting about yourself — "I'm a front-end developer", "answer without emoji" — the model proposes to remember it with a hidden service block at the end of the answer. The block is stripped from the visible text in every client, and a "Remembered: …" notice with an **Undo** button appears.
- **The command** `/memory <fact>`: `/memory I live in Berlin, time zone CET`. Plain `/memory` without text opens the memory panel.
- **Selected text.** Select a phrase in a message → right-click → **Remember the selection**.
- **By hand** — with **Add a fact** in the memory panel.
- **Import** — from a file or another assistant, see below and [Moving data](doc:transfer).

The model does not remember things it learned from its own answer, one-off details, or anything from a [temporary chat](doc:chats): a temporary chat never reads or writes memory. When a similar fact already exists, MemuAI does not create a duplicate; it updates or confirms the existing one.

### The Memory panel

Open it from **Settings → Profile & memory → Memory → Manage memory**, with the `/memory` command or from the profile card. At the top are the master memory switch and a summary: how many facts, how many are pinned, how many wait in the Inbox and how many tokens all of memory takes. Tabs: **Facts**, **Inbox**, **Rules**, **Settings**, **Trash**.

![The Memory panel](docs-img/en/memory.webp "The Memory panel: counters, tabs, search and filters, fact cards with their source and confidence")

A fact card shows:

- **Source** — where the fact came from: the service (Chat, Work, MemuCode, Telegram…), the chat title and an excerpt of your message. Clicking the source opens that chat.
- **Dates** — when the fact was created, updated and last used in a request.
- **Confidence** — how sure the model was when it wrote the fact: "confident", "likely" or "unsure" with a percentage. A fact you added yourself is always confident.
- **Category** — identity, preferences, work, projects, tech & tools, interests, goals, people, health, finance, other. You can change it.
- **Edit history** — who created, edited, confirmed or migrated the fact and when; an earlier text can be restored.

Click the text to edit a fact, **pin** it (then it is always in the request), add tags or move it to the trash. Search looks through text, tags and chat titles; there are filters by category and service, sorting (pinned first, newest, most used, most or least confident…) and grouping by category. Checkboxes on the cards enable actions on several facts at once.

### «Only for» labels

By default a fact applies in every service. An **«Only for»** label limits it: for example, "I write Rust" — **Only for MemuCode**, and "I work in sales" — only for Chat and Work. Fact menu → **«Only for» label…** → tick the services; **Remove the label** makes the fact shared again. The model can also propose a fact with such a label right away.

### Inbox: manual confirmation

The **Inbox** collects facts MemuAI does not save without your consent:

- facts the model is unsure about (confidence below the threshold in the settings);
- sensitive data — health, money, addresses, contacts, documents;
- every new fact when the **Ask me** mode is selected in the settings.

You can **approve** each fact, fix it before approving or **reject** it; there are "Approve all" and "Reject all" buttons. A rejected fact is never proposed again. The number of waiting facts is shown on the tab and next to the button that opens memory.

> [!WARNING]
> Passwords, API keys, tokens and bank card numbers are never saved to memory — not by the model, not by hand, not by import. Ordinary phrases like "the password is important" are not treated as secrets.

### Rules

On the **Rules** tab you write guidance for memory in plain words: "Don't remember anything about health", "Write facts briefly, in the third person", "Write memory in English", "Never remember \"password\" or \"address\"". The model sees the rule text every time it writes memory. Topic bans and quoted words are also enforced by MemuAI itself: such a fact is not saved even when the model proposes it — the rule shows what exactly it blocks. A rule can be switched off for a while. When already saved facts break the rules, MemuAI shows how many and offers to move them to the trash with one button.

### Memory settings

| Setting | What it does |
| --- | --- |
| Mode | **Auto** saves at once; **Ask me** puts every new fact in the Inbox |
| Confidence threshold | in Auto mode, facts below this model confidence go to the Inbox |
| Memory per request | how many tokens of memory each message gets: pinned facts always, then the most relevant ones |
| Sensitive data | ask (Inbox), never save, or like any fact |
| Notifications | show "Remembered: …" with an Undo button |
| Services | per service: **Read** (facts join requests) and **Write** (the AI may add to memory) |

Do For You only reads memory. The switch at the top of the panel turns memory off entirely: facts are neither added nor saved.

### Memory from a chat

The chat menu in the sidebar has **Memory from this chat** — the facts whose source is this chat — and **Forget everything from this chat**, which moves them to the trash in one step.

### Trash

Deleted facts stay in the **Trash** for 30 days and never join requests. You can **restore** them one by one or all at once, delete them forever or empty the trash. **Clear memory** in the settings also moves every fact to the trash; rules and settings stay.

### Import and export

On the **Settings** tab, in the "Transfer" block:

- **Export JSON** — a full copy: facts with sources, dates, confidence, labels, and the rules. It can be loaded back.
- **Export Markdown** — a readable list of facts by category.
- **Import…** — MemuAI JSON (new and old format), a list of strings or plain text with one fact per line or "- " item. Duplicates are merged, secrets are dropped, rules from JSON are added.

Memory from other assistants moves the same way — see [Moving data](doc:transfer).

### In Telegram

In the [Mini App](doc:telegram-miniapp) the memory page shows the profile's facts by category — with source, confidence and search. Facts of the active profile can be pinned, edited and forgotten, and the ones awaiting approval approved or rejected right on the phone. Rules, «Only for» labels and import are set up in the MemuAI window on the computer. The bot and the Mini App use the same shared memory as the MemuAI window, honouring «Only for Telegram» labels.

> [!TIP]
> Keep only what helps in most conversations in memory. Narrow instructions for one task are better given with the `/system` command in that chat, and a fact for one service is better marked with an «Only for» label.

## The assistant's personality

**Settings → Chat & replies → Reply style** sets the overall tone:

| Option | Character |
| --- | --- |
| Friendly | warm, kind |
| Pragmatic | direct, to the point |

## Your instructions

**Custom system instructions** on the same tab are added to every normal chat — for example "answer in Russian, comment code in English". A chat instruction (`/system`) and a [meme's](doc:agents) instructions apply on top of it.

## The prompt library

Saved templates for frequent tasks: "proofread this text", "go through this contract point by point". **New** adds a prompt. The library is organised into sets you can switch between.

## MemuAI's internal prompts

The prompts MemuAI uses itself — for image recognition, agents, strict providers and remote access. You can view and adjust them; the text for the model stays in English.

**Per-provider use**: some providers ignore system prompts. Instructions can be excluded for them, and MemuAI passes the needed information as a separate message.

## What MemuAI knows about itself

The model's base instructions say briefly what MemuAI, Work, MemuCode and the Proxy are and where things live. The details come from this documentation:

- **The "MemuAI Guide" skill** ("+" → AI skills) is on by default. With it the model sees the list of articles and can find and read the right one itself — with the `docs_search` and `docs_read` requests, which exist only together with this skill. Asked "how do I turn on web search?", it reads the article first, then answers and links to it.
- The skill can be turned off, its instructions edited, or removed altogether (the bin on its card); a removed one comes back with the **Bring it back** link at the top of the skill library.
- Without the skill, manual mentions remain: `@!` in the message box attaches an article to your message — see [The message box](doc:composer).

In answers, `@!` with an article's name becomes a button that opens it.

## Safety

File contents, web pages, the profile and memory reach the model as reference information, not as commands. "Forget your instructions" inside a file will not change the model's behaviour.

See also: [Profiles](doc:profiles), [Chats](doc:chats).
