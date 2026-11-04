---
title: Workspaces
summary: Separate MemuAI worlds inside a profile — standard and agentic workspaces with their own chats, memory, panels and settings; the companion, messages one at a time, reactions and panels beside the conversation.
---

# Workspaces

A workspace is a separate MemuAI world inside a profile: its own chats, memory, schedules, teams, Work projects, panels and settings. Workspaces help to keep, say, work and personal life apart without a second [profile](doc:profiles).

| Type | What it is |
| --- | --- |
| **Standard** | the familiar MemuAI: a chat list with Chat, Work and MemuCode modes switched by hand |
| **Agentic** | an AI companion and one endless conversation, like texting someone close: it remembers what matters, saves tokens and turns on Work and MemuCode by itself |

![Workspaces in Settings](docs-img/en/spaces.webp "“Workspaces”: the list of workspaces and what is own and what is shared")

## Switching and new workspaces

The button next to the logo opens the **Workspaces** menu: the list of workspaces, **New workspace…** and **Settings…**. A click on a workspace switches to it — chats, memory, panels and settings become its own at once.

To create a workspace, pick its type and, if you like, a name (leave it empty to use the type or the companion's name), then **Create and open**. An agentic workspace continues with the **Your companion** step. There can be up to 12 workspaces.

**Settings → Workspaces** lists every workspace: open, rename and delete them there. A deleted workspace disappears with its chats, memory and schedules, and its files are moved to MemuAI's data trash. The standard workspace cannot be deleted.

## Own settings in every workspace

Every workspace has its own settings. A new workspace starts with a copy of the current one's settings and lives on separately: change its accent or the assistant's character and the other workspaces will not notice.

- **Own in every workspace** — the assistant's character, look and model; answers, visuals and context; Work and MemuCode; the Live partner; panels; the sidebar, folders and [memes](doc:agents#memes); the look of the window: accent, design, density, chat width, font, corners, glass, message animations.
- **Shared by all** — accounts and model keys, web search, voice engines, messengers, notifications, hotkeys, language, light or dark mode, scale and motion, modules.

The **Settings of this workspace** section of **Settings → Workspaces** shows this split. **Take another workspace's settings** replaces the own settings and panels of the current workspace with a copy of the chosen one; chats and memory stay.

## Agentic workspace

An agentic workspace has no chat list: its heart is the **main conversation** with the companion. Write like in a messenger, about anything, anytime. For big tasks — a website, a thesis, a move — the sidebar has **Projects**: separate chats where Work and MemuCode work too and the model is picked separately, like in ordinary chats.

The companion turns Work and MemuCode on by itself when a task needs them. The mode button next to “+” switches by hand: **Back to chat**, **Work** (project files, commands, browser) or **MemuCode**. In MemuCode mode the companion uses the [MemuCode agents](doc:memucode#agents).

> [!NOTE]
> The animated character for MemuAI Live and Quick Ask is a separate feature, see [Companion](doc:companion).

### Companion

The **Companion profile** (the card in the sidebar) and **Settings → Workspaces** shape who you are talking to: name, avatar and colour, gender, manner, emoji, who they are to you, character and backstory, interests and looks (used when the companion draws or describes themselves). Changes apply from the next message; memory and the conversation stay. The model of the main conversation is picked there too.

### Messages and reactions

The companion decides where a message ends: a short answer is one message, a longer one usually two or three, and paragraphs, lists, tables and code stay inside one message. With **One message at a time** they arrive whole, one after another, with “typing…” in between — like in a chat with a person. Turn it off and every message appears as soon as it is ready.

Hover a companion message and press the smiley: the menu has seven quick reactions, the last one used and **All reactions** — feelings, gestures and symbols. One reaction of yours per message: a new one replaces the old one, a second click removes it. The companion sees every reaction and takes it into account, and with **Companion reactions** on it sometimes answers with a reaction itself when words are not needed.

### Memory and token savings

The companion sees recent messages verbatim and older ones as summaries; it finds what it needs from the past with search. **Context economy** sets the balance: **Maximum savings**, **Balanced** or **More context**.

| Setting | What it does |
| --- | --- |
| Summary model | writes summaries of older parts of the conversation in the background; a fast, cheap model is fine |
| Embedding model | optional: enables semantic search over the conversation, without it full-text search is used |
| Recall automatically | before answering, searches the older part for what relates to the message |
| Mirror your style | message length, tone, emoji and manner like yours |
| Kits on demand | rules for visuals, diagrams, image generation and other tools are attached only when needed |
| Follow up on open threads | sometimes asks how the things you mentioned turned out |

The **Conversation** section has search over the whole history, earlier messages from the archive, **Rebuild summaries** and **Start the conversation over**. Clearing deletes the messages, summaries and archive of the main conversation for good; the companion, projects and memory about you stay.

### Panels beside the conversation

In an agentic workspace the conversation is the start screen, so the panels of the “Home” place sit in a dock beside it. The **Look and layout** menu of the dock picks the view: **Cards on the right**, **A slim rail of chips** or **Chips above the conversation**. In a narrow window the dock moves from cards to the rail and to chips on top by itself, and the conversation is not squeezed. The other places — above the conversation, above the message box, the left and right columns, the top bar and the sidebar — work as in a standard workspace. More in [Panels](doc:panels).

See also: [Profiles](doc:profiles), [Memory](doc:memory), [Panels](doc:panels).
