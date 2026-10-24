---
title: MemuNote — AI notepad
summary: The light editor of the MemuAI suite — tabs, huge files, Markdown with tables, Word documents, PDF and CSV, with AI through MemuAI Proxy. Without the Proxy it stays a fast notepad.
---

# MemuNote — AI notepad

**MemuNote** is a separate small program of the MemuAI suite: a fast Notepad++-class editor with tabs, a Markdown and Word document editor, a PDF and CSV viewer. AI is built right into the text: improve, translate, shorten, ask about the file, search the web. AI works through [MemuAI Proxy](doc:proxy); when the Proxy is not running, every editor feature still works, just without AI.

## Installing

MemuNote is an optional component.

![MemuNote in Settings](docs-img/en/memunote.webp "The MemuNote page in Settings: install status, the Open button and what it can do")

- **In the MemuAI Setup**, turn on the **MemuNote** switch on the "What to install" step.
- **Later, from MemuAI.** If the switch was off, the Setup leaves MemuNote in the program folder as an archive (`Packages\MemuNote.zip`). Open **Settings → MemuNote** and press **Install MemuNote** — no internet needed. The same page has **Open**, **Update** (when a new MemuAI version brought a new package) and **Remove**.

After installing, MemuNote is in the Start menu. Removing packs the program back into its archive, so it comes back with one click; your files and MemuNote settings are not deleted. Updating MemuAI keeps MemuNote installed.

## Opening files

- **Ctrl+O** — the open dialog; **Ctrl+P** — quick open of a tab, a recent file or a full path.
- Drag files from Explorer into the window.
- **Open with → MemuNote** in Explorer: turn it on in MemuNote settings (**Files → In the "Open with" menu**) or in **MemuAI Settings → MemuNote**. Default apps are not changed.
- Starting `MemuNote.exe file.txt` again opens the file as a new tab in the window that is already open.

Supported: text and logs, Markdown, DOCX, ODT, RTF, PDF (view, text, AI questions), CSV/TSV (a table with sorting and a filter), JSON (formatting and validation), HTML (preview without scripts), images and source code in almost every language with syntax highlighting.

## Tabs, session and recovery

- Tabs can be dragged, pinned and closed with the middle button; **Ctrl+Shift+T** reopens a closed one.
- **Ctrl+\\** splits the window into two panes — edit one file in two places or two files side by side.
- The next start reopens the same tabs. Unsaved text is written in the background and comes back even after a crash or a shutdown.
- When another program changes a file, MemuNote reloads it by itself, and with unsaved edits it shows a bar with a choice.

## Large files

Files bigger than the threshold in the settings (16 MB by default) open in **fast mode**: no syntax highlighting, spellcheck or minimap. Logs and dumps of hundreds of megabytes open in seconds, find and replace work across the whole file. The limit is 1 GB.

The encoding is detected automatically (UTF-8, UTF-16, Windows-1251, KOI8-R, CP866 and others); change it in the status bar — reopen the file with another encoding or save it in another one. Line endings (CRLF/LF) are kept as they were.

## Markdown

By default Markdown opens in the **live mode**: headings, bold and italic, links, images, lists, tasks, quotes and tables are shown formatted right in the editor, and the markup (`**`, `#`, `|`…) appears only where the caret is — at the element or on the line you are editing. The file itself does not change: exactly the text you typed is saved.

- A task checkbox is ticked with a click (one tick is one **Ctrl+Z** undo step).
- A table is drawn as a grid; a click on a cell puts the caret into that cell of the source, a ` ``` ` code block opens when the caret is inside.
- **Ctrl+click** on a link opens it. Pictures from the internet are not loaded (only local files next to the document).
- A very large Markdown file (over 3 MB) opens in the source mode.

Switch the mode with the buttons on the toolbar (live mode, source only, source and preview, preview only) or in **Settings → Files → Default Markdown preview**. In "source and preview" a double click in the preview jumps to the line; **Ctrl+Shift+V** toggles the preview. The formatting bar: bold, italic, headings, lists, tasks, quotes, links, tables. **Tools → Align Markdown table** lines up the columns.

When you apply formatting to a plain `.txt`, MemuNote asks whether to turn it into Markdown: a `.md` file is created next to it and the original `.txt` stays untouched. You can keep the `.txt` or format the text with AI right away.

## Running files

Scripts run right from MemuNote: Python, JavaScript/TypeScript (Node.js, tsx/Deno/Bun), PowerShell, `.bat`/`.cmd`, Bash, Ruby, PHP, Perl, Lua, R, Go, Java (single file), Dart, Julia, Swift, VBScript, AutoHotkey and more.

- **F5** or the **Run** button on the toolbar — the file is saved and runs from disk; **Shift+F5** — stop (child processes too); **Ctrl+`** — show or hide the output panel. In plain text F5 still inserts the date and time, like in Notepad.
- **The ▾ menu next to the button**: **Run with arguments…**, **Choose interpreter…** (your own path to python.exe, node.exe and so on for this file type) and **Ask before running again**.
- **The output panel** shows the program's coloured output, progress bars, the exit code and the run time. The line at the bottom is the **program input**: Enter sends the line, ↑/↓ — history, **Ctrl+D** closes the input, **Ctrl+C** stops. A click on an error reference like `File "x.py", line 12` or `x.js:12:5` jumps to that line.
- **Before the first run** of a file MemuNote asks for confirmation: the program runs with your permissions, like a double-click. You can tick "Don't ask again for this file". Files downloaded from the internet carry a warning and are confirmed every time.
- When the needed program is missing (Python is not installed, for example), a window offers a **Download** link and a **Choose path…** button.
- Up to 6 programs can run at once. Arguments are passed without a shell; for `.bat`/`.cmd` the characters `& | < > ^ % !` are not allowed in the path and arguments — cmd.exe would treat them as commands.

## Code autocomplete

While you type code a list appears: the language's keywords and built-in functions, snippets (such as `def`, `for`, `class`) and names already used in the file. It works for Python, JavaScript/TypeScript, HTML, CSS, SQL, Go, Rust, Java, C/C++, C#, PHP, Ruby, Lua, PowerShell, Bash, Batch, Kotlin, Swift, Dart, YAML and more. **Ctrl+Space** opens the list by hand, **Enter** or **Tab** inserts, **Esc** closes. Turn it off in **Settings → Editor → Code autocomplete**.

**AI suggestions** in code (the Proxy is needed) — grey continuation text at the caret, **Tab** accepts it. Turn them on with the **AI suggestions** button in the status bar or in **Settings → AI → AI suggestions while typing**.

## Spell checking

Mistakes get a red wavy underline; Russian and English dictionaries are built in, no internet needed. Each word is checked with the dictionary of its own alphabet, so mixed-language text is not underlined as a whole.

- **Right-click** an underlined word — corrections, **Add to dictionary** or **Ignore in this document**.
- In code only comments are checked; in Markdown code, links, addresses and HTML are skipped.
- The **Spelling** button in the status bar turns checking on and off for the current document; for all of them — **Settings → Editor → Spell checking**.
- Your dictionary is the file `%APPDATA%\MemuAI\memunote\dictionary.txt` (one word per line); you can edit it by hand.

On the first start the dictionaries load in the background for a few seconds — the underlines appear when loading finishes.

## Word, ODT and RTF documents

Documents open like in Word: a page, styles, fonts and sizes, colour and highlight, alignment, lists and tasks, tables, images, links, page breaks. Saving goes back to the same format; before overwriting DOCX/ODT MemuNote warns that complex layout (headers, footnotes, fields, macros) may be simplified and offers to save a copy. The **To Markdown** button creates a Markdown version of the document.

**Export** of any file: PDF, HTML, DOCX, ODT, RTF, Markdown, text. **Ctrl+P** prints.

## AI in MemuNote

MemuAI Proxy has to be running (it starts together with MemuAI). The dot in the status bar shows whether AI is connected.

- **Ctrl+J** (or **Ctrl+Enter**) — an AI command at the cursor: improve, fix mistakes, rewrite, shorten, expand, simplify, change the tone, translate, continue, make a list or a table, format as Markdown, comment or fix code — or your own command in words. The result comes as a card: **Accept**, **Insert below**, **Retry** or **Discard**.
- **Ctrl+Shift+L** — the AI panel on the side: questions about the file (the AI sees the open file and the selection), **Web** — search the internet before answering, **Memory** — facts from [MemuAI memory](doc:memory). An answer can be inserted, replace the selection or open in a new tab.
- **Search the web for the selection** — search results with links you can insert into the text.
- **AI suggestions while typing** (off by default): grey text at the end of the line, **Tab** accepts. In code the **AI suggestions** button in the status bar turns them on quickly.
- A PDF has the **Extract text** and **Ask AI** buttons.

The model is chosen in the AI panel or in MemuNote settings; by default it is the same as in MemuAI. MemuNote requests are marked in MemuAI as the `memunote` service.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+N / Ctrl+O / Ctrl+S | New tab / open / save |
| Ctrl+W, Ctrl+Shift+T | Close tab, reopen closed |
| Ctrl+F, Ctrl+H, Ctrl+G | Find, replace, go to line |
| Ctrl+D, Alt+click | Next occurrence, multiple cursors |
| Alt+Z | Word wrap |
| Ctrl+\\ | Split view |
| Ctrl+J, Ctrl+Shift+L | AI command, AI panel |
| F5, Shift+F5, Ctrl+` | Run the file, stop, output panel |
| Ctrl+Space | Autocomplete |
| Ctrl+Shift+P | Command palette |
| F1 | All shortcuts |

Shortcuts work in any keyboard layout: **Ctrl+S** saves even with the Russian layout on (the press is read by the physical key). Latin layouts such as Dvorak and AZERTY keep their own letters, and AltGr characters (ą, €…) type as usual.

## Settings

**Ctrl+,** in MemuNote: language and theme (the same as MemuAI by default — colours and accent come from its appearance), session restore, autosave, font and size, word wrap, line numbers, minimap, indentation, encoding and line endings of new files, backup on save, the large-file threshold, the Markdown preview mode (live by default), spell checking, code autocomplete, AI features, the Proxy address.

MemuNote data lives in the MemuAI data folder: `%APPDATA%\MemuAI\memunote` (settings, session, drafts of unsaved tabs, your `dictionary.txt`).

## Troubleshooting

- **AI offline** — start MemuAI or MemuAI Proxy and click the dot in the status bar. If the Proxy listens on a non-standard address, enter it in **MemuNote settings → AI → MemuAI Proxy address**.
- **No "Install" button in MemuAI settings** — the package was not found: run the MemuAI Setup again and turn MemuNote on.
- **The file opened as gibberish** — click the encoding in the status bar → **Reopen** and pick the right one.
- **F5 says "… not found"** — install the needed program (Python, Node.js…) or point to it: the ▾ menu next to **Run** → **Choose interpreter…**.
- **The window does not open** — like MemuAI, MemuNote needs the Microsoft Edge WebView2 Runtime.
