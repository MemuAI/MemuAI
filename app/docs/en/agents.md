---
title: Agents and memes
summary: Agents that do parts of a task in parallel in Work and MemuCode, and memes — your own assistants with instructions and chats of their own.
---

# Agents and memes

MemuAI has two different mechanisms that are easy to confuse:

- **agents** — helpers that do parts of one task in parallel, in Work and MemuCode;
- **memes** — your own assistants with a name, instructions and a chat history of their own.

## Agents in Work and MemuCode

An agent is a separate worker with a task of its own. It runs in the background while the main model does its own part: one agent studies how authorization works, another finds every place the old function is called, a third reads the tests — and meanwhile the main model writes code and collects their reports as they come in.

**Regular chat has no agents.** The chat used to split a request between "specialist agents" on its own; that made answers slower and more expensive where agents were not needed, and it was removed. For a large topic with dozens of sources the chat has [Deep research](doc:research).

### When agents start

Only when you asked for them:

- in words — "use agents", "do this in parallel", "run a background task";
- with the **Agents** switch in the "+" menu (it exists only in Work and MemuCode) or the `/agents` command — then the model may start agents in this chat when it sees fit.

Without such a request an attempt to start an agent is refused, and the model does the work itself.

### What it looks like

The answer shows an agents card: each agent's task, state and what it is doing right now. The main model does not wait for them: it keeps working and receives the reports as they are ready, and once everyone is done it brings the results together. **Stop** stops the answer and every agent.

### What agents may do

| | |
| --- | --- |
| Read project files, search the code, look at the structure | always |
| Search the web | when web search is on in the chat |
| Change files and run commands | only with the "Full access" mode |

Up to four agents work at once, no more than 24 per conversation; each takes at most 16 steps. An agent cannot start agents of its own.

### Settings

**Settings → Work & agents → Agents**:

| Setting | What it does |
| --- | --- |
| Model of the agents | one model for every agent; the chat's model by default. A fast inexpensive model is enough for code search and reading files |
| Agents at once | from 1 to 4. More finish sooner and spend more requests |

In [MemuCode CLI](doc:memucode-cli) the same rule is the `subagents` setting: `ask` — only on request (the default), `auto` — the model decides, `off` — never.

## Memes

A meme is an assistant with a role: "Text editor", "Legal adviser", "English teacher". Each meme has its own instructions and its own chats in the **Memes** section of the sidebar.

### Creating one

The ＋ next to the "Memes" heading opens a four-step wizard:

1. **Name and instructions** — what it is called, a description, what to do and how.
2. **Model and modes** — the meme's model (or the chat's current one), web search on from the start, thinking mode, "Stay strictly on the agent's topic".
3. **Available capabilities** — what the meme is allowed to do.
4. **Review and confirm** — a summary before saving.

**Create with AI** fills the wizard from a short description: "a helper for preparing for analyst job interviews".

### Capabilities

| Capability | What it allows |
| --- | --- |
| Planning and schedules | creating scheduled tasks |
| Telegram work messages | sending notifications through the bot |
| Local tools | using MemuAI's built-in tools |
| Integrations | GitHub, Google Drive, Google Calendar |
| Files and images | accepting attachments |
| Long-term memory | seeing and adding to your memory |
| AI skills | using ready-made techniques |

A disabled capability is unavailable to the meme even when asked directly.

### A meme's chats

A new chat with a meme starts from its row in the sidebar. The meme's instructions have the highest priority. If a meme has a fixed model or search mode, they cannot be changed in its chats — a note appears in place of the toggle.

The order of "Folders", "Memes" and "Chats" changes by dragging their headers or with the ⇅ button. A meme's avatar is picked at random from twelve drawings and palettes — it can be re-rolled.

### Moving memes

Memes can be exported to a `*.memu-agent.json` file and imported on another computer: **Settings → Data → Memes**. On import MemuAI always asks for a model again — the original may not exist on the other computer.

### In Telegram

With the [Telegram bot](doc:notifications) set up, the `/agents` command there lists your memes, and you can talk to them straight from Telegram.

See also: [Memory and prompts](doc:memory), [Work](doc:work).
