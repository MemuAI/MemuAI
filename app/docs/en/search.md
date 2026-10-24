---
title: Search everything
summary: Ctrl+K searches chats, Work and MemuCode, project files, memory, prompts, skills, schedules, panels, modules, settings and documentation at once.
---

# Search everything

++Ctrl+K++ (or **Menu → View → Search everything**) opens the search window on top of MemuAI. Results appear as you type: the index lives on your computer and updates itself when chats, memory or projects change, so even thousands of chats are searched without a delay. Nothing is sent to the internet. ++Ctrl+K++ also works over open dialogs (settings, memory, export): search opens above them, ++Esc++ goes back to the dialog, and picking a chat or a file closes it.

![The Search everything window](docs-img/en/search.webp "One query covers chats, documentation, memory and more; the preview is on the right")

The field above the chat list still just filters the list. Press ++Enter++ in it to take the same query to search everything.

## What it searches

| Section | What is found |
| --- | --- |
| Chats, Work, MemuCode | titles and message text, Telegram chats included |
| Projects and files | saved Work projects, file names and their text |
| Memory | the facts MemuAI remembers about you |
| Prompts and skills | system prompts and the AI skills library |
| Schedules | scheduled tasks |
| Panels and modules | home screen panels and installed modules |
| Quick Access | the question history of the ++Alt+Space++ window |
| Settings | settings pages and individual rows |
| Commands | app actions: new chat, theme, focus mode… |
| Documentation | the articles of this guide |

Individual settings rows become searchable after you open the settings once — until then their pages are found.

## Writing a query

Mistakes are fine: search understands word forms (“chats” finds “chat”), ё and е, one- or two-letter typos and the wrong keyboard layout — “ghbdtn” finds “привет”. The last word is matched as a word start, so results show up after the first letters.

| Query | What it does |
| --- | --- |
| `"exact phrase"` | the words in exactly this order |
| `-word` | leave out results with this word |
| `in:work`, `in:code`, `in:telegram`, `in:docs`, `in:memory`, `in:files` | search one section |
| `from:me`, `from:ai`, `from:deepseek` | only your messages, the AI's answers or the answers of one model |
| `model:claude` | answers of this model |
| `folder:Work`, `project:site` | chats of a folder or a project |
| `is:pinned` | pinned chats only |
| `date:today`, `date:week`, `date:month`, `date:year` | within a period |
| `after:2026-09-01`, `before:2026-10-01`, `date:2026-05..2026-06` | after or before a date, or a range |

Operators work without words too: `is:pinned` lists every pinned chat, newest first.

## Sections and filters

The section buttons under the field narrow the search (++Tab++ and ++Shift+Tab++ switch them, ++Alt+1++…++Alt+9++ pick one by number). **Filters** set a period, the message author, a model, a folder and pinned; active filters show under the field, and ++Backspace++ in an empty field removes the last one.

## Results and preview

Results are grouped by section with the matches highlighted. A chat shows its best matching messages — each can be opened on its own. The preview on the right shows the neighbouring messages, the file lines or the article section.

| Keys | Action |
| --- | --- |
| ++↑++ ++↓++ | select a result |
| ++Enter++ | open |
| ++Ctrl+Enter++ | the extra action: copy the text or show the file in its folder |
| ++Ctrl+P++ | hide or show the preview |
| ++Esc++ | clear the query, a second time — close |

A chat opens at the found message: it is highlighted, the words are marked, and a “found in this chat” bar with arrows (++F3++ and ++Shift+F3++) steps between the matches. A project file opens in MemuCode at its line, a setting on its page with the row highlighted, an article at its section.

## Recent searches and options

An empty window shows recent searches, recent chats and quick actions. The **⋯** button at the bottom:

- **Remember recent searches** — turn it off and the query history is not kept.
- **Search inside Work project files** — file text is indexed within limits: a few thousand files per project, large and binary files by name only, service folders (`node_modules`, `.git` and the like) skipped.
- **Rebuild the index** — build the index again if something is not found.

## For modules and the Mini App

Modules can add their own sources: in the window — `MemuSearch.registerProvider(...)`, on the server — `register_provider(...)` from `app/search_index.py`. The Telegram [Mini App](doc:telegram-miniapp) uses the same index without project files and modules: the search box in the chat list finds messages by word forms and despite typos, and shows matches in memory, prompts, schedules, characters, panels and the documentation below.
