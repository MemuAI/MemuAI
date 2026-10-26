---
title: Built-in modules
summary: Translator, Studio, Design, Benchmarks, Model catalog, Characters, MemuMusic, MemuLearn and MemuDB: what they do and how they work inside.
---

# Built-in modules

Ten modules ship with MemuAI and are **disabled by default**. They use the same bridge as your code and hide no capabilities.

## Translator

- Two columns: source text and the result.
- A language list, auto-detection and one-click swap.
- A MemuAI model picker and your own translation instructions.
- Image and document attachments; PDF and DOCX are parsed the way the chat parses them.
- Modes: precise, literary, formal, and a short summary in another language.
- A glossary of terms injected into the instructions.
- A history of recent translations, result copying and speaking the translation aloud.
- Text, settings and history survive closing.
- A "keep in chat" flag appends the translation to chat `module-translator`.

The translator's instructions live in its `manifest.json` and can be rewritten entirely.

## MemuAI Studio

A window with five sections:

- **Images** — generate from a description, add references, pick a model and aspect, download the result.
- **Video** — generate a clip and show the provider's error instead of a placeholder.
- **Audio** — speak text with Windows voices, a downloaded model or a TTS API.
- **Transcription** — recognize an audio file, copy the text, send it on to a chat.

Everything goes through `Memu.image`, `Memu.video`, `Memu.speak` and `Memu.transcribe`. Stream errors are parsed and shown as the provider's own text.

- **Session images** — a strip of every image made since the window opened (up to 24). Click one to open the viewer with its prompt: download, use as a reference or "Reuse prompt" to put your original text back. The strip lives in the window's memory: download what you need before closing.
- **Stop** — while working, "Create" becomes "Stop" (images, video, transcription). A late answer is ignored and finished images stay. A generation the provider has already accepted cannot be cancelled — it may still finish and be billed.
- **Enhance** — AI expands the image or video prompt (scene, light, composition, camera motion) with the first connected text model; "Undo" restores your text.
- **References** can be dropped onto the Images tab or pasted from the clipboard (++Ctrl+V++); photos over 1.5 MB are scaled down to 1600 px.

### Music and producer

**Music** is Studio's fifth section: an idea workspace, sound settings, an AI producer and recordings from the current session. Choose a **MemuAI Proxy** music model; its account supplies the connection and key. To connect a service, open Proxy → Accounts → Add → Music API. Schemas cover ElevenLabs, MiniMax and a custom REST API. Studio needs no address or key fields. A saved legacy Studio connection can be explicitly imported from Proxy.

Describe an idea, choose a ready style or ask “Find a style with AI”, supply lyrics or choose instrumental, and set tempo and duration. API fields depend on the chosen model; BPM and some duration settings are prompt hints. Play, seek, download or remove a session recording, or reuse its idea. Download tracks you want to keep before closing the module. Stop and closing cancel local waiting; a service may continue a job it already accepted.

The **Producer** is a separate conversation with a selected MemuAI text model. Discuss an idea, lyrics or structure; “Apply suggestion” copies fields into the form. Music generation is a separate button. For a custom service, configure the request template and result paths in its Proxy account according to that service's documentation.

## MemuAI Design

The equivalent of a design service, but inside MemuAI:

- a request and optional images become a finished website;
- built-in design systems: strict, warm, technical, minimal;
- preview at three screen sizes: desktop, tablet, phone;
- refinement in one request that keeps the decisions already made;
- code view with copying;
- **live preview** — the page appears while the model is still answering, and a pill at the bottom shows the stage (styles, markup, interactivity), size, time and a Stop button. A cut-off answer (token limit, dropped connection) is shown as a warning, not a silent success;
- **versions** — every generation and refinement is kept (the last 12): ↶ ↷ buttons (++Ctrl+Z++, ++Ctrl+Shift+Z++) and a "Version N of M" list with time and the request text. Going back never deletes anything; editing the code by hand changes the current version;
- images can be dropped onto the left panel or pasted from the clipboard; large photos are scaled down before sending;
- ++Ctrl+S++ saves the HTML, ++Esc++ stops the generation, closes the version list or leaves Focus mode;
- **a package for another AI**: the prompt, images, code and a README are saved as files, and "Package ready" appears only after a real save.

The design instruction is stored in the module's `manifest.json`. Replace it for a different kind of task.

## Benchmarks

Every public AI model leaderboard in one window: **arena.ai** (all arenas and categories: text, WebDev, vision, documents, search, images, video, agents), **Artificial Analysis** (the intelligence index and individual tests, speed, price), **LiveBench**, **SimpleBench** and **EQ-Bench**. Data is loaded from the sites through the bridge and cached for 6 hours; "Refresh" loads it again. The "Data updated …" line in the header refers to what is on screen now — the overview boards or the open board — not to boards closed long ago.

The **Agents** arena has a format of its own: instead of an Elo rating it shows the average improvement over five behaviour signals (confirmed success, praise versus complaint, steerability, recovery after failed commands, hallucinated tools). The overall ranking is shown by default, and each signal is a category of the board.

- Model search and the "My models" switch — only what is connected in MemuAI.
- Clicking a model opens its placements in every loaded ranking.
- SWE-bench, Aider Polyglot, IFBench, τ²-Bench and Terminal-Bench Hard were removed as outdated or unreliable.

### Change notifications

Select individual boards and categories in notifications, choose the interval, enable monitoring and save. The first successful check establishes a quiet baseline; subsequent checks report new models, changed scores and removed rows. MemuAI Index and personal runs are not external monitoring sources.

Checks run in **MemuAI Proxy**, even with the module closed, while Proxy runs. Restarts reuse the saved baseline. Source failures appear in status and preserve the baseline. MemuAI and Telegram notifications are available (Telegram needs a configured bot). Permissions `background`, `web` and `notify` are required; disabling the module stops checks. Benchmarks remains at version **1.0**.

### MemuAI Index

A composite ranking of its own. Sites use incompatible scales, so each board is first turned into a **percentile** (0 is last place, 100 is first; of a model's variants the best one counts). The percentiles are then averaged with weights:

| Source | Weight |
| --- | --- |
| arena.ai · Text | 22% |
| arena.ai · WebDev | 12% |
| arena.ai · Vision | 6% |
| Artificial Analysis · Intelligence Index | 26% |
| LiveBench · Global average | 18% |
| SimpleBench | 10% |
| EQ-Bench | 6% |

A virtual source with weight 0.25 and value 50 is added to the sum: a model found in only a couple of rankings is pulled toward the middle and cannot overtake one that is consistently strong everywhere. Only models found in at least two sources are listed; the "Sources" column shows coverage, and "How the index is calculated" explains the method and lists sources that did not load. Individual Artificial Analysis tests are not part of the index — they are already inside the intelligence index.

### Personal arena

Public leaderboards test the official APIs, while behind the same name in Proxy there may be another version. The personal arena runs up to six of your models through eight tasks: logic, arithmetic, letters in a word, strict format (checked automatically), code, editing, explanation and honesty (graded by a judge model). The judge sees the answers without names and in random order; if it is competing itself, the module warns about inflated scores. The result is a 0–100 score; clicking a cell shows the answer. The last three runs are kept.

## Model catalog

The catalog combines OpenRouter and Models.dev public lists, including API versions and variants. Exact IDs and explicit canonical IDs are merged; similar names are not. Coverage is much broader than OpenRouter alone, but does not guarantee every existing model.

Prices per million tokens, context, maximum output, modalities, supported parameters, date added and knowledge cutoff depend on available data. Each card names its source and pricing provider. OpenRouter takes priority for overlapping entries; additional models prefer their direct provider when available. Unknown prices are never treated as free.

- Filters: what the model accepts and produces, minimum context, price, capabilities (tools, reasoning, strict JSON, web search), developers, hiding models that are being retired.
- Sorting: newest, cheapest, most expensive, by context, by name.
- Quick selections above results cover reasoning, image input, image generation, tools and free models. Cards / List changes the presentation; narrow windows open filters in a separate panel.
- The model card — description, available prices (cache, reasoning, web search), parameters, "Open source" and "Copy ID".
- Tick "Compare" on up to three models to open a comparison table; the best values are highlighted.
- "My models" keeps only models already connected in MemuAI.

### Benchmarks in details and comparison

Click anywhere on a card to see public benchmark scores and MemuAI Index. Choose boards; each score shows rank, source, retrieval time and the tested variant. If several variants are available, choose one from the list: reasoning mode or run version can change the result. A best published variant selected by default is explicitly marked.

Model comparison includes a table of selected boards below prices and capabilities. Best values are highlighted within each board according to its scale direction. A missing score stays missing rather than becoming zero. Loading errors and stale data are marked; “Refresh benchmarks” reloads sources. The source code and index method are shared with the Benchmarks module, which does not need to be opened separately. Catalog matches model names but shows the published run variant, without promising an identical result through your Proxy.

The list is cached for 6 hours. Legacy OpenRouter-only caches refresh automatically. If one source fails, its previous data is retained and an error is shown; the other source continues working. Equal displayed comparison values are ties. The module sends nothing to models and needs no API keys for the catalog.

## New API models

Presets: OpenRouter, OpenAI, Google/Gemini, Anthropic/Claude, xAI/Grok, Mistral, Groq and DeepSeek. Custom sources accept an HTTPS endpoint, authentication method and JSON array, ID and name paths. Keys stay in Proxy, outside module code and remembered fields. A blank input keeps the saved key; removal is explicit. Changing an endpoint does not forward the old key to another server.

1. Enable the module in module settings.
2. Select sources and supply keys for authenticated APIs.
3. For OpenRouter select all families or specific ones, such as Gemini + OpenAI or Claude. The complete developer list loads immediately from the public API without a key or a monitoring run. Search finds any family; “All” includes future families. Refreshing the list preserves your filter.
4. Choose the interval, enable background monitoring and save. “Check now” also works while background monitoring is paused.

The first check quietly establishes a baseline. Later notifications cover new selected models only. History, errors and the last check time appear in the module. Checks continue after closing it while Proxy runs. Disabling the module stops them. Telegram uses the configured bot; Windows follows system notification settings. Permissions `background`, `web` and `notify` are required. Model catalog remains at version **1.0**.

## Editing them

Any built-in module opens with the "Code" button, exactly like your own. Changes are stored outside the installation, and "Reset to shipped" restores the original. Any of them can be disabled — MemuAI keeps working normally.


## Characters

SillyTavern-style roleplay: PNG/JSON cards (V2/V3), Chat Completion presets with a prompt manager, lorebooks, personas, swipes, branches and the Author's Note. See [Characters](doc:characters). It uses `Memu.complete`, `Memu.store` and `Memu.asset`.

## MemuMusic

A Spotify-like music library: local files and downloads by link or search (YouTube, YouTube Music, SoundCloud, Bandcamp; Spotify/Apple Music/Deezer/Yandex by matching), playlists and smart mixes, synced lyrics, an equalizer, statistics, widgets and a remote in the Telegram Mini App. The module needs the `music` permission — see [Module permissions](doc:modules-permissions).

### Online search

Round 41. **Search** («Find … online») and the search field on the **Download** screen show the same results:

- **Where a track comes from** — the source's badge and name, the channel (a check mark means verified), duration, views or plays, year. Labels: «Official audio», «Music video», «Lyric video», «Live», «Cover», «Remix», «Slowed», «Sped up», «Karaoke», «30 s preview» (SoundCloud gives only a clip of subscription tracks), **E** — explicit.
- **Source** — a YouTube Music / YouTube / SoundCloud switch (remembered). YouTube Music is the fastest and gives the artist, album and duration right away; SoundCloud searches longer (up to ~20 s).
- **«Originals only»** hides covers, remixes, slowed and karaoke versions.
- **«In library»** — the track is already downloaded (the same link, or the same title, artist and duration): «Play» instead of «Download».
- **Clicking a track** opens the **Track info** sheet: the cover (high resolution from iTunes when found), album, year, genre, channel and followers, publish date, views and likes, the best and all available qualities (codec, kbps, kHz, size), the description (collapsed). Actions: **Play** (downloads to the library and starts it), **Download to library**, **Add to queue** (after the download), **To playlist**, **Copy link**, **Open** on the source, **Find lyrics** and **Similar tracks** (the YouTube Music radio or SoundCloud recommendations). Right-click or «⋯» on a row opens the same menu.
- Details load only when the sheet opens and are cached (search for 10 minutes, details for 30). If YouTube asks to confirm you are not a bot, the sheet shows what is known without it; the browser to sign in with is chosen in MemuMusic Settings → Downloads.

### Lyrics

Round 41 (3.7). The **Lyrics** tab of Now Playing lights the line being sung — the way Apple Music and Spotify do:

- **A line fills only while it is sung.** Where the sung part ends comes from the source when it knows (Musixmatch and NetEase word timing, LRC with word stamps `<mm:ss.xx>` and an end stamp); otherwise it is estimated from the syllables and this song's own singing rate. A line no longer crawls through an instrumental break up to the next stamp.
- **«•••» in breaks.** When the next line is 4.5 s or more away (and in the intro), the sung line stays lit as sung and three dots appear: they fill during the break and pop right before the next line.
- **Mode** — MemuMusic Settings → Downloads → Lyrics → **Lyrics sync**: «By word» (smart fill), «By line» (the whole line lights up, no fill) or «Off» (plain scrollable text; clicking a line still seeks). It is stored on the server and applies at once.
- **Mark a line end.** In the lyrics editor («Edit» above the lyrics, or the Lyrics tab of «Edit info») **Line end** inserts an empty stamp of the current time `[mm:ss.xx]` after the line with the cursor — «the singing ends here, a pause follows». You can type such an empty line yourself too.
- The track's lyrics offset (−/+, keys `[` `]`), «By line» tap sync and the general offset from Settings work as before. With «light effects» and `prefers-reduced-motion` the line lights without the fill and the dots do not breathe. The highlight is updated per frame only while the Lyrics tab is open, the window is visible and music plays.

## MemuLearn

Round 40. An AI academy: a course from any source plus smart flashcards. Enable the module in the module settings — then the MemuAI home screen can show the **Review today** panel, and the Telegram Mini App gets a **Trainer** tab.

- **Create a course** from a topic ("Python basics" — the AI drafts a syllabus), a book or docs (PDF, DOCX, EPUB, TXT, Markdown — the course follows the text), a MemuNote file, or a ready Anki `.apkg` deck / `.csv` table (no AI).
- **A course** is a tree of modules and lessons. Opening a course selects the first unfinished lesson in order. For a lesson the AI writes a summary (Markdown, KaTeX formulas, code) and makes flashcards; when the summary exists but there are no cards yet, the lesson offers **Create cards** right away. "Prepare all lessons" goes lesson by lesson and stops on a button.
- **Cards** — Basic and Cloze (`{{c1::…}}`), search and editing, your own cards. Scheduling is **FSRS-5** with the default parameters, computed on this computer, offline. Grades: Again, Hard, Good, Easy, plus undo of the last grade.
- **Exam** — the AI writes tickets (open questions and coding tasks), grades the answers, gives a score and explains the mistakes, and adds cards for the weak spots on its own.
- **Statistics** — day streak, reviews over 30 days, the 14-day workload forecast, cards by state. **Settings** — new cards and reviews per day, interval fuzz, model, cards per lesson, light effects.
- **Import and export** — `.apkg` and `.csv` both ways.

The module needs `learn`, `chat`, `models`, `storage`, `download`, `notify`. MemuAI stores the courses (`app/learn/`); the module works through `Memu.learn.call(op, args)` — see [Module permissions](doc:modules-permissions).

## MemuDB

A local database workbench for SQLite databases and DuckDB analytical files with a schema explorer, SQL editor and AI assistant. See [MemuDB](doc:memudb). The module needs `database`, `models` and `storage` — see [Module permissions](doc:modules-permissions).
