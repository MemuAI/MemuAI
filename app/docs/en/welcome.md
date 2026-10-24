---
title: Welcome
summary: What MemuAI and MemuAI Proxy are, how they fit together and how to use this documentation.
---

# Welcome to MemuAI

MemuAI is a Windows desktop application that brings together the models of all your AI accounts — Claude, GPT, Gemini, Grok and others — and models that run right on your computer. Conversations, project files, images, voice, memory and schedules live in one window.

![MemuAI main window](docs-img/en/home.webp "A new chat: starter ideas, the composer and the Home panels")

## Two programs

| Program | What it does |
| --- | --- |
| **MemuAI** | the chat window: conversations, Work, MemuCode, modules, settings |
| **MemuAI Proxy** | the background core in the tray: stores accounts, picks the model and account, serves an API for other programs |

The easiest way to think of it: the Proxy is the engine with the accounts, MemuAI is the cockpit you drive it from. MemuAI starts the Proxy by itself; the Proxy needs setting up once, when you add accounts. Details: [MemuAI Proxy](doc:proxy).

## Where to start

1. [Installation](doc:install) — install the program.
2. [Accounts](doc:accounts) — connect at least one service or a local model.
3. [Interface](doc:interface) — where everything is in the window.
4. [Chats](doc:chats) and [Models](doc:models) — the first conversation.

## What MemuAI can do

| Section | In short |
| --- | --- |
| [Chat](doc:chats) | conversations with Markdown, formulas, code, attachments and images |
| [Web search](doc:web-search) | current information from the internet |
| [Memory](doc:memory) | lasting facts about you that the model remembers across chats |
| [Agents](doc:agents) | helpers that do parts of a task in parallel in Work and MemuCode; your own "meme" assistants |
| [Voice](doc:voice) | dictation, reading answers aloud and the MemuAI Live voice conversation |
| [Work](doc:work) | the AI reads and changes files in a chosen folder |
| [MemuCode](doc:memucode) | an agent for programmers: repository, terminal, reversible changes |
| [Scheduling](doc:schedules) | tasks the model runs on a schedule |
| [Modules](doc:modules) | your own apps inside MemuAI in HTML, CSS and JavaScript |
| [Themes](doc:themes) | the look, entirely your way |
| [Local models](doc:local-models) | GGUF models on your own computer, offline |

## Using this documentation

- Search — the field at the top left or the ++/++ key. Arrows pick a result, ++Enter++ opens it.
- ++Alt+←++ and ++Alt+→++ go back and forward through pages you have read.
- The page contents are on the right; the current section lights up as you scroll.
- All text can be selected and copied. Right-clicking text opens a menu: copy, search the documentation for the selection, ask MemuAI, copy a link to the section.
- The ⋯ menu in the page header copies the page link or Markdown and saves the page as a file.

> [!TIP]
> Didn't find an answer? Select the unclear passage and choose **Ask MemuAI about this** — the question with the quote appears in the composer.

## Data and privacy

Chats, settings, memory and accounts are stored on your computer in `%APPDATA%\MemuAI`. Messages go only to the service whose model is selected, and local models send nothing anywhere. Details: [Where data is stored](doc:data).
