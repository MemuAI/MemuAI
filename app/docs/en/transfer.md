---
title: Moving data
summary: Exporting and importing chats, bringing history from ChatGPT, Claude and Gemini, transferring information about you and memes.
---

# Moving data

**Settings → Data** gathers everything needed to move: from another assistant to MemuAI, from one computer to another, or just for a backup.

## Chats

**Export all chats** saves the current profile's entire history as one JSON file in `Downloads\MemuAI`.

**Import chats** loads history from a file. Supported:

| Source | Which file |
| --- | --- |
| ChatGPT | `conversations.json` from the data export (Settings → Data controls → Export data) |
| Claude | `conversations.json` from the export (Settings → Privacy → Export data) |
| Gemini | a chat export in JSON |
| MemuAI | the file saved with the button above, or a single chat exported as JSON |

Before loading, MemuAI shows how many chats it found. By default they are added to the existing ones; ticking **Replace all current chats** clears the history first. Imported chats join the main list and work like any other.

A single chat is exported from its menu — see [Chats](doc:chats#export).

## Information about you

An assistant you have used for a long time knows a lot about you. **Import information about yourself** moves it into MemuAI [memory](doc:memory):

1. Copy the ready-made prompt from the import window.
2. Send it to that assistant — ChatGPT, Claude or another.
3. Paste its answer back into MemuAI.
4. Choose a processing model — it splits the answer into separate facts.
5. Review the facts, remove what you do not want and save.

If no model is available, MemuAI parses the answer itself in a simpler way.

## Memes

[Memes](doc:agents) travel as `*.memu-agent.json` files: **Export memes** and **Import memes**. On import MemuAI always asks for a model: the original may not exist on the new computer.

## Modules and themes

- Modules move as `*.memu-module.json` files from **Settings → Modules** — see [Workbench](doc:modules-workbench).
- Custom themes are `.css` files: export and import in **Settings → Appearance** — see [Custom themes](doc:themes).

## Moving everything

To move everything — accounts, settings, profiles, Proxy models — copy the `%APPDATA%\MemuAI` folder to the new computer while MemuAI and the Proxy are closed there. Details: [Where data is stored](doc:data).

> [!WARNING]
> This folder holds the cookies and tokens of your accounts. Move it only to your own computer and never put it in a shared cloud folder.

See also: [Where data is stored](doc:data).
