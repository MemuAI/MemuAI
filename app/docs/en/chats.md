---
title: Chats
summary: New and temporary chats, folders, pinning, search, retrying and editing messages, export.
---

# Chats

Every conversation is a separate chat with its own history, model and settings. Chats are stored on your computer and open from the list in the sidebar.

## A new chat

**New chat** (++Ctrl+N++) opens a clean conversation. It joins the list only after the first message: empty chats do not pile up however many times you press the button.

A new chat uses the default model from **Settings → Models** or the one chosen last.

## A temporary chat

A **temporary chat** (++Ctrl+Shift+T++) is not saved in the history. In it the model does not see your profile and memory, remembers nothing and creates no scheduled tasks. A "Temporary chat" bar is shown at the top.

**Save chat** on that bar turns the conversation into a normal one — from then on it is saved and works like any other.

> [!NOTE]
> "Temporary" means MemuAI keeps nothing. Messages still go to the selected model's service, as in a normal chat. A fully local conversation is only possible with a [local model](doc:local-models).

## The chat list

The list shows the chats of the current mode: conversations in Chat, sessions of the active project in Work and MemuCode. Work on different projects therefore never mixes.

| Action | How |
| --- | --- |
| Find a chat | the field above the list filters chats by title and text; ++Ctrl+K++ — [search everything](doc:search), straight to the message |
| Pin | chat menu → Pin; pinned chats stay on top. History keeps up to 200 chats: when it overflows the oldest ones are removed, but pinned chats never are |
| Rename | chat menu → Rename |
| Move to a folder | drag the chat onto a folder in "Folders" (back out of a folder — onto the general list). While you drag, the mouse wheel scrolls the list, and near its top or bottom edge (or past it) the list scrolls by itself — faster the closer you are to the edge. ++Esc++ cancels |
| Mark unread | chat menu |
| Delete | chat menu → Delete; the confirmation can be turned off in settings |

Folders are created with the ＋ next to the "Folders" heading. Deleting a folder keeps its chats — they return to the main list.

### Chat titles

A new chat is named by the chat's own model — in the same request that answers the first message, without a separate call. **Settings → General → Chat titles**:

| Setting | What it does |
| --- | --- |
| The AI names the chat | on by default. "Start of the first message" brings back the old behaviour |
| Update the title when the topic changes | if the conversation moves to another subject, the AI renames the chat so the title covers both. Off by default |

A chat you renamed yourself is never touched by the AI.

## Messages

| Action | Where |
| --- | --- |
| Copy an answer | the button below it or the right-click menu |
| Retry an answer | the button below it or `/retry`; the currently selected model is used. The previous answer stays as a version |
| Edit your message | the button below it; "Save and resend" starts a new branch, the old one stays |
| Stop generating | the "Stop" button in place of "Send", or ++Esc++ |
| Read aloud | answer menu → Read aloud — see [Voice](doc:voice) |

### Versions and branches

"Retry" does not erase the answer, it adds a new **version**: "‹ 2/3 ›" appears below the answer, the arrows flip through the variants. Editing your message and resending it starts a **branch** — the previous message with all the answers after it stays, and "‹ 1/2 ›" appears below the message.

| Action | Where |
| --- | --- |
| Switch the version | the "‹ ›" arrows below the message; ++Ctrl+Shift+←++ / ++Ctrl+Shift+→++ |
| Branch map | the button next to the arrows, answer menu → Branch map or ++Ctrl+Shift+B++: the conversation tree, a click on a message switches the chat to its branch |
| Fork chat from here | the answer menu or the button below your message — a new chat with the history up to that point |

The model, history compression, the token counter and export see only the branch on screen. ++Ctrl+K++ search also finds text in other versions — such results are marked "other version". Telegram and the Mini App show the active branch and the version switcher of the last answer.

While a model answers, an animation and a timer are shown. "Stop" cancels the request to the service, not just the indicator.

When the model sends its reasoning, it appears as a collapsed block above the answer.

### What the tools did

When the model used tools for an answer, a **Tools** block appears above it. Each row says in plain words what happened, with its duration and outcome (a check mark or an error):

| Tool | What the row shows, collapsed and expanded |
| --- | --- |
| Documentation article | "Read the article “Quick Access”", its summary, sections and a button that opens it |
| Web search | the query, the number of results, the pages found with links and snippets |
| Opened page | the page title and address, how much text was read |
| Memory | "Saved to memory: …" — which facts were saved or updated |
| Files and folders | the file path, matches, a file list, "File exists / No such file" |
| Commands (Work, MemuCode) | the command, its exit code, output and errors |
| Computer | disk space, system info, working folder and so on |

Click a row to expand it: the call parameters, links, the error text and the tool's technical name. Expanded rows stay open while the answer is still being written. Rows of older chats are readable too, just without result details.

The internal markup a model uses to call tools (call blocks, reasoning tags, control tokens) never reaches the answer: not while it is typed, not in the saved history, not when copied, not in Telegram or Quick Access. When a model writes a call in its own format, MemuAI still runs it as a regular tool call. Code examples that contain such tags inside code blocks are kept as they are.

### Canvas: documents, code and pages as a separate item

When an answer is a document, code, an HTML page, a table or an email, the model puts it into the **Canvas** — a panel to the right of the chat. The chat keeps a short card ("Document · v1 created", an Open button) instead of the long text.

- **Edits in place.** Follow-up requests ("make the intro shorter", "add a dark theme") change the same item: the model sends only edits of the right places. The card shows "v2 edited +3 −3".
- **Versions.** The list at the top of the panel: who edited (AI or you), when, what waits for review. Open an older version and press "Restore this version".
- **Diff.** The tab shows the changes against the previous version with word highlighting. Accept or reject each AI edit separately, or use "Accept all" and "Reject all".
- **Your own edits.** On the Code tab (Text for documents and emails) edit right in the panel; "Save version" or Ctrl+S creates your version, and the model continues from it.
- **Preview.** HTML pages and JavaScript run in a sandbox with no network and no access to MemuAI or your files; console.log output and errors appear in the Console. Documents are shown formatted, tables as a grid with CSV and Excel export, emails with To and Subject.
- **Actions** (buttons and the "…" menu): copy, download to Downloads/MemuAI, Open in MemuNote, Save to the Work project (the active project), Make a module (pages and JS), Remove from Canvas.

Drag the panel edge to resize it; in a narrow window the canvas opens over the chat. When it is closed, the Canvas button at the top right opens it again. When you switch between answer versions, the canvas shows the version of the item that belongs to that branch.

**Canvas 2.0.**
- **Several files in one item.** Ask for "a site with index.html, style.css and app.js" and the canvas shows file tabs. Each file is edited on its own (double-click a tab to rename, "+" for a new file, × to delete); Download gives the project as a .zip, one .html page or the current file. The preview assembles the files itself; `fetch("data.json")` works without network.
- **Preview errors.** When the page fails (a script error, an unhandled promise, `console.error`), a bar above the preview shows the error and where it happened (`app.js:12` — click to open the line in the editor). "Fix with AI" sends the error and the code line to the chat; the model fixes it with a targeted edit and the preview runs again.
- **Diagrams (Mermaid).** Flowcharts, sequence diagrams, mind maps and more are drawn offline. In a flowchart drag a block onto another one to link them, double-click to rename, "+ Block" to add one. The Code tab is an editor with a live preview. Download: SVG, PNG, .mmd.
- **Slides.** A presentation is plain text (`---` between slides, `#` / `##` titles, `- ` bullets, `Notes:` for speaker notes), shown by the MemuNote engine with a theme picker; Download → PowerPoint (.pptx).
- **Link with MemuNote.** After "Open in MemuNote" the item is linked to the file: save in MemuNote and a new "your edit" version appears in the canvas (on your next message or when you return to the window); the model's edits in the chat are written to the file and MemuNote reloads it (with unsaved edits MemuNote shows a warning instead).

## Export

Chat menu → **Export** (or ++Ctrl+Shift+S++):

| Format | For |
| --- | --- |
| Markdown (.md) | reading and keeping; formatting is preserved |
| JSON (.json) | moving to another program or back into MemuAI |
| Copy text | pasting the whole conversation somewhere |

Files are saved to `Downloads\MemuAI`. All chats at once are exported in **Settings → Data** — see [Moving data](doc:transfer).

## An instruction for one chat

`/system` sets an instruction that applies only to the current chat: "answer in English", "you are an editor, fix the style". Instructions for every chat are set in **Settings → Chat & replies → Your instructions**.

See also: [Models](doc:models), [Composer](doc:composer), [Commands](doc:commands).
