---
title: Composer
summary: Sending, the «+» tools menu, attachments, pasting and dropping files, the context menu, spelling and mentions.
---

# Composer

The field at the bottom of the window is more than a place for text. Files and pictures are attached here, and commands, dictation and the voice conversation start from here.

## Sending

By default ++Enter++ sends and ++Shift+Enter++ adds a line. If you prefer the opposite, choose ++Ctrl+Enter++ in **Settings → General → Sending messages** — then ++Enter++ adds a line.

While the model answers, the send button becomes "Stop".

## The «+» menu

The ＋ button in the lower-left corner of the field gathers everything you can add to a message or turn on for the answer. The menu is searchable: start typing an item's name; ++↑++ ++↓++ and ++Enter++ pick, ++Esc++ closes. The `/tools` command opens it too.

![The composer «+» menu](docs-img/en/composer-plus.webp "The «+» menu: attachments, Google Drive, screenshot, sketch, table and answer tools — with search")

| Section | Items |
| --- | --- |
| Add | Photos and files · [Add from Google Drive](doc:sync#add-files-from-google-drive) · Paste from clipboard · Screenshot · Sketch · [Table](doc:tables-diagrams#your-own-table-in-a-message) |
| Answer tools | Create an image · Web search · Deep research · Reasoning · Visualization · Agents (Work and MemuCode only) |
| Actions | Live conversation · Dictation · AI skills · Chat instructions · Schedule a task · Temporary chat |

Answer tools are switches of this chat: they are saved with it and do not change your general settings. An enabled tool shows as a chip next to ＋, and a dot on ＋ reminds you something is on. Clicking a chip turns the tool off. An unavailable item is disabled and says why — for example, "Create an image" without an image model in Proxy.

- **Screenshot** — the screen dims: drag to select an area, click a window to capture it whole, or press ++Enter++ for the whole screen. ++Esc++ or the right mouse button cancel. The picture is attached right away.
- **Deep research** — dozens of searches, reading sources and a cited report. Details: [Deep research](doc:research).
- **Visualization** — the answer must include a chart, diagram or interactive model.
- **Agents** — lets the model start [parallel agents](doc:agents) in this chat. The switch exists only in Work and MemuCode; regular chat has no agents.
- **Chat instructions** — role, tone and format for the current conversation only (the same as `/system`).

The "⋯" next to web search in the menu picks the search source.

### Sketch

**"+" → Sketch** opens a canvas for a quick drawing: a diagram, an arrow on a screenshot, notes on a photo. The finished picture is attached to the message as an ordinary image — remove it with the cross like any attachment.

| Tool | Key | What it does |
| --- | --- | --- |
| Pen | ++P++ | a freehand line |
| Marker | ++M++ | a wide translucent line — to highlight a spot |
| Eraser | ++E++ | erases what is drawn |
| Line, Arrow | ++L++, ++A++ | a straight line and an arrow |
| Rectangle, Ellipse | ++R++, ++O++ | an outline; with ++Shift++ — a square and a circle |
| Text | ++T++ | click the canvas and type; ++Enter++ places the text, ++Shift+Enter++ starts a new line |
| Insert an image | ++I++ | a file from disk; an image can also be dropped onto the canvas or pasted (++Ctrl+V++) |
| Move | ++V++ | drags any object; the corner of the selected object resizes it, ++Delete++ removes it |

Next to them are seven colours and a custom one, four thicknesses, **Undo** (++Ctrl+Z++), **Redo** (++Ctrl+Y++) and **Clear**. Everything can be undone: a stroke, a move, a deletion, a clear.

**Attach** (or ++Ctrl+Enter++) adds the drawing to the message. **Cancel** or ++Esc++ close the window, but the sketch is not lost: open Sketch again and it is still there until you attach it.

## Attachments

**Photos and files** in the ＋ menu opens a file picker. Files and pictures can also be:

- pasted from the clipboard (++Ctrl+V++), a screenshot for example;
- dragged into the MemuAI window;
- picked on Google Drive — **Add from Google Drive** (see [Sync](doc:sync#add-files-from-google-drive)).

| Type | What happens to it |
| --- | --- |
| PNG, JPEG, GIF, WebP images | go to the model as pictures — see [Images](doc:images) |
| PDF, DOCX | the text is extracted on your computer and passed to the model |
| Text and code: `.txt`, `.md`, `.json`, `.csv`, `.py`, `.js`, `.ts`, `.html`, `.css`, `.sql`, `.yaml` and more | passed as text |

A file's contents reach the model separately from your message and are marked as reference material. If a file says "ignore previous instructions", the model will not obey it.

Attachments show as cards above the field; the cross on a card removes the file. Their size counts towards the [context meter](doc:context).

**Video** is taken apart frame by frame (about one frame every two seconds) and, if enabled, with the soundtrack transcribed. Who watches the frames is set in **Settings → Images & video → Handling video**: ask every time, the current model, or a separate **Video recognition model** that prepares an answer to your request for the chat model.

## Commands and mentions

- ++/++ at the start of a message opens the [command](doc:commands) list: new chat, image, search, retry and more.
- ++@++ opens mentions: `@Web`, `@Browser`, `@Computer`, `@Plan`, `@Goal`. In Work and MemuCode the project's files are here too: `@index.html`, `@src/app.js`.

The list filters as you type; pick with the arrows and ++Enter++ or with a click.

### Documentation articles: `@!`

Type `@!` and a list of the articles of this documentation opens; keep typing a title to narrow it. The chosen article is inserted as `@!web-search`, and its text is attached to the request: the model answers from the documentation rather than from guesses. Up to four articles per message.

The model can point at an article too: `@!` with its name in an answer becomes a button that opens the article in Settings → Documentation. The model can read the documentation on its own while the **MemuAI Guide** skill is on — see [Memory and prompts](doc:memory).

## Context menu

Right-clicking the field opens a menu that always fits in the window:

- undo, redo, cut, copy, paste, **paste as plain text**, select all, clear the field;
- transformations of the selection: UPPERCASE, lowercase, Sentence case, trim extra spaces, wrap in quotes, format as code;
- how many characters and words are selected;
- quick actions: attach files, dictate, MemuAI Live, send;
- for a misspelled word — suggestions, "Remove repeated word", "Add to dictionary", "Ignore word";
- turning spell check on and its language.

## Spell checking

Turn it on in **Settings → General → Spell checking**. Misspelled words are underlined, and suggestions, "Add to dictionary" and "Ignore word" appear in the context menu. The dictionaries installed in Windows are used; the language is chosen in the same place.

Underlines always belong to the text that is in the box right now: after sending, clearing, inserting a command or moving to another chat or the home screen, old marks disappear at once.

## Voice

Next to the send button are the microphone and MemuAI Live:

- the **microphone** records speech, recognises it and puts the text in the field so you can check it before sending;
- **MemuAI Live** is a voice conversation with the model.

Both buttons can be hidden. Details: [Voice and MemuAI Live](doc:voice).

## Long messages

The field grows with the text up to a sensible height, then it scrolls. A long message you sent is collapsed in the feed so it does not take over the screen.

See also: [Commands](doc:commands), [Images](doc:images), [Context](doc:context).

## Attachment cards

Cards show the full filename and size. Click anywhere on a card to preview it; removal has its own button. The preview can download the original file. Image aspect uses a themed, keyboard-accessible menu. Image generation and deep research do not remain active together as hidden modes.
