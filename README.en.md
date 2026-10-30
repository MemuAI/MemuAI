# MemuAI — one app for talking to AI, with an entire ecosystem (MemuAI Proxy, MemuCode, MemuCode CLI, MemuWork, MemuNote, MemuFiles, MemuMusic, and other modules)

[Русский](README.md) · **English**

[Download](https://github.com/MemuAI/MemuAI/releases) · [Report a bug](https://github.com/MemuAI/MemuAI/issues) · [Source code](https://github.com/MemuAI/MemuAI)

- 🚨 Disclaimer: using Genspark, Kiro, Antigravity, Gemini Web, and Grok accounts is entirely **AT YOUR OWN RISK**. Using these accounts may violate the companies' privacy policies. This project is provided solely for educational purposes. The project authors are not responsible for account bans or other restrictions imposed by third-party companies.

# What is this, anyway?

The idea behind MemuAI is to bring AI services together in one place. In a single app, you can chat with AI, add your own sub2api accounts and manage API keys, create your own apps, work with files and documents, and much more.

**This project was made by AI** — with help from multiple AIs, in fact.

## What's included

| App | What it's for |
| --- | --- |
| **MemuAI** | The main chat app: models, files, memory, search, voice conversations, and projects. |
| **MemuAI Proxy** | Connects accounts and models, provides a shared API for MemuAI and other programs, and tracks costs and limits. |
| **MemuCode CLI** | An AI agent in your terminal: explores projects, edits files, and runs commands with the permissions you choose. |
| **MemuFiles** | A file manager with tabs, previews, archive support, and an AI assistant. |
| **MemuNote** | An editor for text, code, and documents, with an AI assistant, spreadsheets, presentations, and image tools. |
| **MemuMusic** | A music library and player with playlists, cover art, lyrics, an equalizer, and a full-screen mode. |
| **Modules** | A collection of apps inside MemuAI, from a translator to characters. You can create your own modules and customize the existing ones. |

You usually only need to set up Proxy once. After that, its models become available throughout the suite. If you only need an API for your own programs, Proxy can be installed separately, as can MemuCode CLI, MemuFiles, and MemuNote. MemuMusic is a module and can be removed just like the other modules.

# Main features

## MemuAI Proxy: one place for AI

<img width="1920" height="1032" alt="MemuAI Proxy interface" src="https://github.com/user-attachments/assets/1052020d-d7e8-451a-9e15-5ca55b3149c5" />

### Working with APIs

- Add any provider that uses API keys. Several sub2api integrations are also supported: Genspark, Kiro, Antigravity, Gemini Web, and Grok.
- API key pools: add multiple API keys to each account and automatically switch between them when an error occurs.
- Quota planner: automatically analyzes account usage and estimates when your limits will run out at the current pace. You can add both accounts and API keys, specifying their costs and pricing.
- Composite models: create custom models that contain other models and switch between them under specific conditions, such as the number of tokens.
- Local models: install local models directly through the interface. The local model wizard helps you choose a suitable model based on your computer's hardware.
- Public access: connect MemuAI Proxy to a public address so it can be available to everyone at any time.
- Model detector: advanced checks to determine whether the model served by an API is actually the one the provider claims it is.

## MemuAI: chatting with AI

<img width="1600" height="950" alt="MemuAI interface" src="https://github.com/user-attachments/assets/e5a8ef40-7df1-46c3-bc3f-a1f65b92bd8b" />

### Built-in tools

- Web search through Kiro, Bing, DuckDuckGo, and other services, plus a deep research mode.
- Create videos, images, spreadsheets, visualizations (like in ChatGPT), product cards, and more.
- Visualizations 4.0: branching flows, sortable rankings, comparisons, entity cards with a details panel and interactive tools; copied as readable text.
- Repository module catalogue: inspect and download source before installation, install, update, roll back and switch sources. Contribute a module through a pull request.
- MemuDB: local SQLite and DuckDB databases, SQL, tables, import/export and AI proposals with change previews and confirmed writes.
- Integrations with Google, Notion, GitHub, and Telegram accounts.
- A dedicated Chrome extension for summarizing pages, managing tabs, or simply chatting with AI in your browser.
- Full MCP support.
- Create panels to keep any information you need.
- Skills support.
- A flexible scheduling system: run actions at specific times, in response to events, or under custom conditions.

### Customization

- Create your own system prompts or edit the existing ones.
- Enable or disable almost any feature to suit your needs.

### Memeses, profiles, and memory

- Create your own profiles and protect them with passwords.
- Each profile has its own memory, collected by AI. You can turn this off.
- Memeses are custom AI agents: give them instructions, restrict their tools, and more.

### Design and appearance

- More than 8 interface designs and 12 accent colors are available out of the box, along with light and dark themes.
- Light and dark themes come in variations such as Graphite, Twilight, Midnight, OLED Black, and more.
- Theme editor: customize MemuAI's interface elements in a dedicated editor, with plenty of tools for adjusting different settings.
- Create entirely custom themes and share them as `.css` files.

### Access from anywhere through Telegram

- Connect a Telegram bot and chat with AI directly in Telegram.
- Mini App support: use all the main features through a convenient interface inside Telegram. You can use Cloudflare Tunnel or your own tunnel and address to access the Mini App.

### Quick Access and Live

- Reach your AI at any time with a keyboard shortcut.
- The AI can work with files and your screen, answer questions, and use custom commands or buttons you create for different events.
- Live: talk to AI in real time. Connect a Live model, such as Gemini Live, or use separate models for speech recognition and text-to-speech. Local models can be downloaded directly in MemuAI, and APIs are supported too.
- A Live2D AI companion is available for both Quick Access and Live mode.

### Do For You and other features

- Do For You (DFY) has its own browser where it can carry out tasks for you: sign in to websites, place orders, look up information, monitor changes, and more. All data is passed through a special secure form, without either the AI or MemuAI having access to it. DFY can connect to a separate machine to work with files, or to your own browser through the extension.
- Links and a command (`memuai://`, `memu`): control MemuAI from other applications.
- Browser: the AI has a built-in browser for checking whether websites work and for other tasks.
- Computer Use: the AI can control your computer in real time.
- Custom models for image and video understanding. If your API model cannot understand images or videos, you can connect another model that can. It assists the main model with those materials without replacing it.
- Data import and export: import chats or information about yourself from MemuAI, or load a data export from another AI service (ChatGPT, Gemini, Claude, etc.) to give the AI context about you.
- Detailed documentation is available for every feature.

### Modules

- Create your own apps inside MemuAI and install them as standalone applications. Modules communicate with MemuAI and are built with HTML, CSS, JavaScript, and a manifest.
- There are 10 built-in modules available by default:
  - Translator: straightforward text translation with a glossary.
  - MemuAI Studio: a module for creating videos, music, voiceovers, and transcriptions.
  - MemuAI Design: a module for designing websites and apps.
  - Benchmarks: brings together the main benchmarks (arena.ai, Artificial Analysis, LiveBench, SimpleBench, EQ-Bench, and others), alongside the project's own MemuAI Index benchmark.
  - Model Catalog: a list of AI models sourced from OpenRouter.
  - New API Models: notifications about new models through OpenRouter, with support for the OpenAI, Gemini, Anthropic, xAI, Mistral, Groq, DeepSeek APIs, and many others.
  - Characters: an alternative to character.ai and, in particular, SillyTavern, with PNG and JSON character imports, lorebook support, an attractive interface, and convenient settings.
  - MemuMusic: a music library and player with link downloads, local files, lyrics, and Telegram Mini App synchronization. The built-in module offers AI lyrics search and optional recommendations, with separate model choices in settings. The standalone MemuMusic app works independently and has no AI features.
  - MemuLearn: an AI academy for building courses from books or topics, with lesson notes, exams, and FSRS-5 flashcards.
  - MemuDB: a local SQLite and DuckDB database workbench with table browsing, a SQL editor, and safe AI change previews.
  - Anyone can create their own modules and share them with others.

### Team

- Build a team of models with separate roles and a communication manager, using a template or your own composition.
- The manager curates user context, while the lead assigns work and reviews the outcome. Regular chats and Work include an activity view and per-request spending controls.

- “Customize sidebar” controls every optional section and individual module shortcuts, sharing their visibility and location settings with the Modules page.

### MemuAI Work

- The part of MemuAI built for working with files, documents, and more.
- Can edit files and work with large documents and other projects.

### MemuCode (deprecated)

- The part of MemuAI built for coding.
- It is no longer actively developed; MemuCode CLI is recommended instead.

## MemuCode CLI

**A terminal-based code editor, similar in many ways to Claude Code. To use it, just type `memucode` in your terminal.**

<img width="1913" height="1000" alt="MemuCode CLI interface" src="https://github.com/user-attachments/assets/d4b19c6e-5d32-465d-b9fb-77be7985311f" />

- More than 60 commands.
- Built-in support for Decisions models: a model can decide which main model to use at a given moment, approve commands, and more.
- Context optimization: when the token count gets large, the context is automatically compressed to keep work going.
- Agents: choose specific models as sub-agents. The main model can then call on them to work on particular features under its direction.
- Automatic session cost calculation.
- And much more...

## MemuNote

**An app for working with documents, with AI support.**

- Significantly faster than Windows Notepad, Notion, and others, although still slower than Notepad++ and probably Obsidian.
- Edit code with autocompletion and run it directly in the app, including Python scripts.
- Support for almost every file type, from `.txt` and `.md` to `.word` and others, with formatting options. Presentations are supported too.
- Presentations: themes and starter decks, transitions, editable charts, images, AI slide reviews, and PPTX/PDF export.
- Image support: open images and edit them with AI or by hand. MemuNote includes most basic editing tools, although many other features are still missing.
- AI assistant: ask questions, get help, format documents, and more.

## MemuFiles

**A simple, convenient, and fast alternative to Windows File Explorer.**

- Can be set as your default file manager, at your own risk. This can always be undone.
- Tabs, a pleasant and customizable design, and plenty of tools.
- Much faster than Windows File Explorer, especially on Windows 11 — sometimes several times faster.
- Convenient previews for all kinds of files.
- An AI assistant for working with files: delete or organize files, or get information about files and folders. Actions are handled safely and require user confirmation.
- Plenty of other useful everyday features.

This covers most of the features, but certainly not all of them. You can learn more by [downloading a release](https://github.com/MemuAI/MemuAI/releases) or exploring the code yourself. MemuAI also includes built-in documentation with clear explanations of its features.

---

## Installation

MemuAI is designed for **64-bit Windows 10 and Windows 11**. The interface requires **Microsoft Edge WebView2 Runtime**. Local models need additional disk space and suitable hardware.

1. Open [Releases](https://github.com/MemuAI/MemuAI/releases) and download `MemuAI-Setup-<version>.exe`, if a build has been published.
2. Install the components you need and launch MemuAI.
3. Open MemuAI Proxy and add an account, your own API, or a local model.
4. Return to MemuAI, choose a model, and start chatting.

In the MemuAI installer, you can select which specific programs will be installed. MemuAI Proxy is required for installation, while additional applications such as MemuAI, MemuCode CLI, MemuNote and MemuFiles - are downloaded optionally. Additionally, other details can be modified in the installer.

### Automatic updates

MemuAI checks GitHub Releases on launch and every 15 minutes. Updates download in the background; once an update is ready, a blue install button appears next to the Proxy status. After you confirm, MemuAI closes and opens the installer.

You can manage this in **Settings → About → Updates**. Automatic checks can be disabled; the Check button still works when automatic checks are off. Connection and download errors are reported separately from the message that your version is up to date.

## Where the models come from

MemuAI uses your own connections. The project supports Antigravity, Kiro, Gemini Web, Genspark, Grok, custom APIs, and local models. Connection presets are available for many API services.

You can use your own keys, accounts, or models running on your computer. MemuAI does not provide subscriptions to third-party services: model availability, pricing, and quotas depend on your connection. Some integrations use unofficial service interfaces and may need updates when those services change.

Proxy can refresh the model catalog, show request statistics, and account for custom pricing. The quota planner lets you set an API account balance and track estimated spending.

## Where your data is stored

Chats, settings, and data for the main suite are stored locally, usually in `%APPDATA%\MemuAI`. When you use a cloud model, your prompt and the materials you select for it are sent to the corresponding provider. Local models let you process requests on your own computer; web search and external integrations still use the internet.

## Running from source

You need Git, 64-bit Python, and WebView2 Runtime. The commands below are intended for PowerShell on Windows.

```powershell
git clone https://github.com/MemuAI/MemuAI.git
cd MemuAI
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start Proxy:

```powershell
.\.venv\Scripts\python.exe main.py
```

In another terminal, from the same folder, start MemuAI:

```powershell
.\.venv\Scripts\python.exe memuai.py
```

For MemuCode CLI:

```powershell
.\.venv\Scripts\python.exe memucode.py
```

Run `build_installer.bat` to build the installer, or `build_proxy_installer.bat` to build the standalone Proxy installer.

OAuth credentials are not included in the public source code. If your connection requires an OAuth client, read the [setup guide](OAUTH_SETUP.md) first.

## Bugs and suggestions

If something isn't working, open an [Issue](https://github.com/MemuAI/MemuAI/issues). The most useful details are your MemuAI version, the app or section involved, the steps that led to the problem, and what you expected to happen. Screenshots and error messages help too — just remove any keys, tokens, and private conversations first.

Suggestions for making the app easier to use are welcome too. Specific examples are especially helpful: what you were trying to do, where you got stuck, and how that workflow could work better.

## Thanks

Thank you to everyone who tries MemuAI, notices bugs, and reports them. Your feedback helps identify what really needs work.

This project builds on the work of the authors of FastAPI, pywebview, llama.cpp, faster-whisper, Piper, Telethon, yt-dlp, and other libraries. Many thanks to [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api) as well.

Third-party components are distributed under their respective licenses; the relevant notices are retained in the project.
