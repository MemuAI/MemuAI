---
title: Quick Ask in Explorer and other programs
summary: Alt+Space over File Explorer — AI sees the open folder and the selected files, reads, searches, renames and sorts them with a preview and undo. Plus VS Code, Word/Excel, terminals and window tools — currency, eyedropper, text from the screen without AI, notes.
---

# Quick Ask in Explorer and other programs

Open [Quick Ask](doc:quick-ask) (++Alt+Space++) over a **File Explorer** window and it knows where you are: a chip like “Folder: Downloads · selected 3 files” appears under the input. While the chip is on, every question goes with that folder: AI can list the files, read them (text, PDF, DOCX), search their contents and **propose changes** — create, edit, rename, move or delete files.

Everything stays **strictly inside that folder**: going up (`..`), absolute paths and links (symlinks, junctions) are refused. System folders (Windows, Program Files, ProgramData), drive roots and folders without write access are read only.

## One-click actions

Action buttons sit under the chip (once a conversation is going they hide behind the arrow on the chip):

| Button | What happens |
|---|---|
| Summarize selection | AI reads the selected files and summarizes each in 1–3 sentences |
| Explain the folder | Purpose, structure, key files; for a project — the stack and how to run it |
| Rename | You give a rule (“date first, no spaces”) — AI proposes a “before → after” table |
| Sort into folders | By type (default) or by your rule; a tree of the new folders is shown |
| Transform text | Translate, fix typos, CSV → Markdown… The result is saved as new files next to the originals |
| Create a file | For example, a README describing the folder |
| Duplicates | No AI: compared by content (size → hash), you choose which copy to keep |
| Search in files | No AI: names and text inside files; ++Enter++ — show in Explorer, ++Shift+Enter++ — open, ++Tab++ — ask AI |

Or just ask in your own words: “which of these files are older than a year?”, “merge these notes into one summary”, “rename the photos by date”.

## Changes: preview, confirmation, undo

Whatever AI wants to change arrives as **one card** under the answer: text edits as a diff (green added, red removed), renames as a table, sorting as a folder tree. The access mode is chosen in settings:

- **Ask first** (default) — nothing changes until you press “Apply” (++Alt+Enter++).
- **Full** — new files, edits, renames and moves are applied at once. **Deleting and overwriting a whole file always wait for your click.**

After applying, the card offers “Undo”: files come back as they were (deleted and overwritten files are kept in a backup). The last 12 change sets can also be undone from settings. If a file changed after the preview, the change is not applied — ask again.

> [!NOTE]
> Deleting from Quick Ask does not free the space at once: the files stay in the MemuAI backup until 12 newer change sets push them out. Files over 512 MB are not deleted from here — delete them in Explorer.

## Other programs

| Where the window is opened | What Quick Ask sees |
|---|---|
| The desktop | The Desktop folder |
| VS Code, Cursor, Windsurf, VSCodium | The open file (its text goes straight into the context) and the project folder — with the same tools as for Explorer |
| Notepad++, Sublime Text, Notepad, JetBrains | The open file when its path is in the title or in the program's command line |
| Word, Excel, PowerPoint | The open document — **read only**: the text, the sheet (up to 400 rows × 40 columns) or the slides are read when you ask |
| A terminal (cmd, PowerShell, Windows Terminal) | The terminal's folder. PowerShell does not report `cd`, so it may be the folder it started in — the chip says so |

**Per-app prompts** are your own buttons for specific programs: e.g. “Reply to the email” over Outlook or “Formula help” over Excel. The selected text goes along with the prompt. They are set up in the section below.

## Window tools

In ++Ctrl+K++ → “Tools”:

- **Currency under the input** — type “100 usd to eur”, “€50 to $”, “2.5k eur in rub”, “exchange rate usd”: the result appears at once, without ++Enter++ and without a model. Click to copy. Rates come from open.er-api.com, are refreshed every 6 hours and work offline with the last rate.
- **Eyedropper** — the colour under the pointer at ++Alt+Space++, or “Pick on screen”: point and press ++Space++. HEX, RGB, HSL, CMYK — a click copies.
- **Text from the screen without AI** — select a region and Windows' built-in OCR returns the text: copy, ask AI, save to a note. Languages are those installed in Windows Settings → Time & language.
- **Explain the error on screen** — a screenshot and the question “what is this error and how to fix it”. When the selected text looks like an error (a traceback, `npm ERR!`, `Exception`…), an “Explain the error” button appears under the input.
- **Note to MemuNote** — a dated line in “Quick notes.md” (Documents\MemuAI Notes), opened in MemuNote. The “To note” button is also under answers. The phrase “note: …” still writes into the “Notes” chat ([commands](doc:quick-commands)).
- **Latest downloads** — the newest files from Downloads; ++Enter++ makes the file the question's context (AI reads, renames, moves it), ++Shift+Enter++ — show in Explorer.

## Settings

Settings → Quick Ask → “Explorer, programs and tools”: the folder from Explorer, the access mode, hidden files, recent changes with undo, editors and terminals, Office documents, per-app prompts, all window tools and the notes folder. The settings live in `quick_apps.json`.

## Security

- The folder is determined by the app itself at ++Alt+Space++; the window page knows only an opaque context id and cannot substitute another path.
- Programs and scripts (`.exe`, `.bat`, `.ps1`, `.lnk`…) are never launched from Quick Ask.
- File text is data for AI, not commands: the model is told not to follow instructions found in it, and every change still goes through the card.
