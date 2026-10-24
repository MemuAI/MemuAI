---
title: Interface
summary: The sidebar, the top bar, the message feed, the composer and the settings panel.
---

# Interface

The MemuAI window is a normal Windows window: resize it, maximise it, snap it to a screen edge with Snap Layouts. The interface adapts down to 420×360 pixels.

![MemuAI chat window](docs-img/en/interface.webp "The sidebar with sections and chats on the left, the model picker on top, the feed in the middle and the composer below")

## Sidebar

Navigation is on the left. Drag its right edge to change the width; ++Ctrl+B++ hides it.

| Element | What it is |
| --- | --- |
| New chat, temporary chat | start a conversation; a temporary one is not saved — see [Chats](doc:chats) |
| Chat, Work, MemuCode | three modes: plain conversation, [working with files](doc:work), [a coding agent](doc:memucode) |
| Scheduling | [scheduled tasks](doc:schedules) |
| AI skills | ready-made techniques the model uses by itself |
| Pinned | chats pinned from their menu |
| Folders | your chat folders; drag a chat onto a folder to move it |
| Memes | your [assistants](doc:agents) with their own instructions and chats |
| Chats | the conversation history, newest first |
| Proxy status | the dot and caption at the bottom: whether MemuAI Proxy is running |
| Profile card | name and avatar; click to open the [profile](doc:profiles) |

The order of Folders, Memes and Chats changes by dragging their headers or with the ⇅ button.

Every chat has a menu (⋯ or right-click): pin, rename, mark unread, export, copy, delete.

## Top bar

| Element | What it does |
| --- | --- |
| MemuAI menu | application commands: file, edit, view, help |
| Back, forward | move between recently opened chats and screens (++Alt+←++, ++Alt+→++) |
| Sidebar | hide or show the sidebar |
| Search | filters the chat list; ++Enter++ or ++Ctrl+K++ — [search everywhere](doc:search): chats, memory, files, settings |
| Model picker | the current chat's model — see [Models](doc:models) |
| Context meter | how much of the model's context window the conversation uses — see [Context](doc:context) |
| Focus | a distraction-free mode (++Ctrl+Shift+F++) |
| Theme | switch between light and dark |

Work and MemuCode add a bar of their own below the top bar: project, access mode, plan, goal.

## Message feed

Model answers are Markdown: headings, lists, task lists (`- [ ]` and `- [x]` show as ☐ and ☑), tables, formulas, highlighted code with a copy button. While an answer is still being typed, an unclosed code block already shows as code rather than raw ``` characters. Below an answer are the model name and actions: copy, retry, read aloud, edit. All text can be selected.

Images — yours and generated ones — open in a viewer: the mouse wheel zooms, and there are "fit", download and open-in-another-program.

A long user message is collapsed; the button below it expands the full text.

## Composer

The conversation lives on a raised, rounded "canvas" while the sidebar blends into the window background. At the bottom is the message field: on the left the ＋ button with attachments and answer tools (enabled ones show as chips next to it), on the right the microphone, MemuAI Live and send. Details: [Composer](doc:composer). ++/++ at the start opens [commands](doc:commands), ++@++ opens mentions.

## Settings panel

++Ctrl+,++ or the menu entry. On the left — search and 19 pages in five groups, on the right — the page itself. Only the settings people change often are visible at first; rare ones sit in collapsed **Advanced** blocks, and long lists (voices, models, integrations) open as sub-pages with a "Settings › Page › Sub-page" trail at the top.

| Group | Page | What is there |
| --- | --- | --- |
| Basics | General | language, sending messages, spell checking, chat titles, starting with Windows |
| | Appearance | theme and its variant, accent colour, design, font, animations — see [Appearance](doc:appearance) |
| | Notifications | Windows notifications and sound |
| AI | Models | the default model, reasoning, visible models, prompt caching |
| | Chat & replies | reply style, your instructions, visualizations and product cards, long chats and the context window |
| | Web search | web search, the external source, deep research |
| | Images & video | how pictures and video reach models without vision, the recognition model |
| | Voice & Live | dictation, reading aloud, MemuAI Live, realtime Live models, the companion |
| | Work & agents | file access, AI skills (Browser, Computer Use), the agent model and count |
| Everywhere | Quick Ask | the Alt+Space window — see [Quick Ask](doc:quick-ask) |
| | Panels | cards on the home screen and in the sidebar |
| | Telegram | the bot and the Mini App |
| | Integrations | GitHub, Google Drive, Google Calendar, email for scheduled tasks |
| You and your data | Profile & memory | name, about you, avatar, PIN, memory |
| | System prompts | the prompt library for advanced users |
| | Data | importing and exporting chats, memes and information about you; the danger zone (deleting history) |
| MemuAI | Modules | [your own apps](doc:modules) inside MemuAI |
| | Documentation | this help |
| | About | version, updates and [keyboard shortcuts](doc:shortcuts) |

**Search** (++Ctrl+F++ or ++/++ inside settings) finds any setting in English or Russian, forgives typos and the wrong keyboard layout: ++↑++/++↓++ pick a result, ++Enter++ opens the page, sub-page or Advanced block and highlights the row. ++Esc++ first clears the search, then leaves a sub-page, then closes the window; ++Alt+←++ goes back. A changed setting shows ↺ on hover to restore its default; some sections have "Reset section". In a narrow window the page list folds behind the ☰ button.

Every change is saved immediately — "Saved ✓" flashes in the header.

## Context menus

Right-click works almost everywhere: on a message, a chat, a model, the composer, a file in MemuCode, documentation text. In the composer the menu has text editing, transformations (case, cleanup), counters and spelling fixes.

## Tooltips

Hover over any icon button — after half a second a tooltip appears with its name and shortcut, if there is one. From the keyboard the tooltip appears when you move with ++Tab++.
