---
title: Quick Ask (Alt+Space)
summary: A personal assistant above every program — hotkey, Ctrl+K actions, chats, the browser page, MemuAI Proxy models or your own endpoint, screenshots and window position.
---

# Quick Ask

Press ++Alt+Space++ in any program and a small MemuAI window with an input field appears. Ask, get a short answer, press ++Esc++ — and you are back where you were working. It works even when the main MemuAI window is in the tray.

![The Quick Ask window](docs-img/en/quick-ask.webp "A quick question and answer on top of other windows; the model and actions are at the bottom")

Quick Ask is for simple questions that need the fastest possible answer: a fact, a command, a translation, a formula, "what is this error on my screen". Longer work is easier in a regular chat — **Open in MemuAI** moves the whole conversation there.

## How to use it

The window is simple: one field at the top (with screenshot and send next to it), context chips under it (page, selection, attachments) and **one list** — suggestions, actions, models or chats; after a question the answer takes its place. At the bottom: the model chip, the pin (keep the window), New chat, Chats, the mic (when voice is on) and **Actions Ctrl K** — everything else (web search, file, settings, plugin buttons) lives there, with search and key hints. On the first open the window shows a short tip once.

| Keys | Action |
| --- | --- |
| ++Enter++ | send (while an answer is coming, ++Enter++ does not interrupt it — the window suggests waiting or pressing ++Esc++) |
| ++Shift+Enter++ | new line |
| ++Esc++ | one step back: close the actions, models or chats list → a DFY question → stop the answer → close the window. While the window is open, ++Esc++ closes it from any other program too |
| ++↑++ / ++↓++ in an empty field | recent questions |
| ++Ctrl+N++ | new chat (the current one stays in Quick Ask history) |
| ++Ctrl+H++ | Quick Ask chats: search, open, delete (++Del++) |
| ++Ctrl+K++ | actions: everything secondary, searchable |
| ++Ctrl+Enter++ | [do it for me](doc:dfy) — hand the request to the Do For You agent |
| ++Ctrl+Shift+Enter++ | open the conversation in MemuAI |
| ++Ctrl+P++ | pin or unpin the window (see [Pinning the window](#pinning-the-window)) |
| ++Alt+R++ | auto / fast / smart model |
| ++Ctrl+Shift+A++ | ask the advanced model again |
| ++Alt+1++…++Alt+9++ | list rows by number (e.g. “Brief” for a page) |
| ++Ctrl+Alt+arrows++ | dock the window to a screen edge |
| ++Ctrl+Shift+C++ | copy the answer |
| ++Ctrl+Shift+S++ | attach a screenshot |
| ++Alt+M++ | choose the model for this window |
| ++Tab++ | put the calculator result into the field |

Under the answer, on the left you see who answered and how fast; on the right — **Smarter** (ask the smart model), **Copy**, **Insert** (pastes the answer into the program you came from, via the clipboard and ++Ctrl+V++), **Retry** and **Open in MemuAI**. If you come back within five minutes, the last conversation is still there.

The window never disappears on its own: clicking another window does **not** close or hide it — the window and your panels and buttons on the screen stay **on top** of other windows, so you can check something in another program. Everything closes together and only explicitly — ++Esc++ (in the window, or in any other program while it is open), ++Alt+Space++ again or **Hide the window** in **Actions**.

If you would rather it did not cover your work, choose **Step back** in **Settings → Quick Ask → Behaviour → Click in another program**: when you switch to another program, an unpinned window then **steps back** together with its panels and buttons — it stays open; click it, use ++Alt+Tab++ or press ++Alt+Space++ to bring it forward.

### Pinning the window

The pin is **always visible** in the bottom bar of the window; ++Ctrl+P++ and **Pin the window** in **Actions** do the same. A pinned window is marked with an accent pin and a strip at the top, and behaves like this:

- it stays **on top of every window**, even while you work in another program;
- ++Esc++ pressed in another program does **not** close it;
- it opens in the same place, and the conversation is not reset after five minutes;
- if it was open when MemuAI quit, it **comes back after a restart**.

++Esc++ and ++Alt+Space++ inside the window itself still hide it. Click the pin again (or ++Ctrl+P++) to unpin.

While the field is empty, the window suggests recent questions and a few ideas: "what's on my screen?", the weather, a translation.

## Quick Ask chats

Quick Ask has its own history: every conversation is saved as a separate chat (each profile has its own list). The ✎ button or ++Ctrl+N++ ends the current chat and starts a new one; the clock button or ++Ctrl+H++ opens the list with search — pick a chat to continue it, or delete it.

These chats **never appear** in MemuAI's history. A conversation reaches the main window only explicitly — with **Open in MemuAI**. History and chats can be cleared in **Settings → Quick Ask → History and chats**.

## The browser page

Open a page in Chrome, Edge, Firefox, Yandex Browser, Opera, Vivaldi or Brave and call Quick Ask — a **Page: title** chip appears in the input together with **Summarize**, **Key points**, **Translate** and **Ask about the page**. You can also just type "summarize this page" or "what is this article about".

How it works: the window remembers which program it was called from and reads the tab's address through Windows accessibility (UI Automation) — no screenshots; opening the window is never delayed by it. The page is then loaded by its address, like a web search does. If the site serves something other than what you see (a login, a paywall, a personal page, or the download is blocked), MemuAI reads the visible text of the tab instead. The × on the chip leaves the page out of this question. Turn the feature off in **Settings → Quick Ask → Browser page**.

## Hotkey

The default is ++Alt+Space++. Change it in **Settings → Quick Ask**: click the hotkey field and press the new combination. Any number of modifiers works (even ++Ctrl+Alt+Shift+Win+K++), and ++F1++…++F24++ work without modifiers (the window warns that the key then stops working in other programs). While the field listens, MemuAI's current hotkey is switched off, so you can press it again — ++Alt+Space++ included. Not accepted: a plain key without a modifier, ++Shift++ with a letter, ++Ctrl+C++, ++Ctrl+V++ and other text-editing shortcuts.

The honest trade-off: in Windows ++Alt+Space++ opens the window menu (Move, Size, Close). While MemuAI runs, the combination opens Quick Ask in every program instead; the window menu is still available by right-clicking a title bar. If that bothers you, choose another combination such as ++Ctrl+Alt+Space++ or ++Alt+Q++.

If another program already uses the combination (often PowerToys Run, Flow Launcher, translators or game overlays), Windows will not give it to MemuAI. Settings show this at once, suggest free alternatives and keep your previous working hotkey. MemuAI's own registration is never reported as a conflict. Some programs grab keys before Windows does — then the hotkey "registers" but the window does not open; MemuAI warns you only when such a program is actually running.

## Where models come from: MemuAI Proxy or your own endpoint

**Settings → Quick Ask → Where models come from** offers two options:

- **MemuAI Proxy** (default) — like MemuAI chats: models and keys are set up in Proxy.
- **Own endpoint** — any OpenAI-compatible server: Ollama, LM Studio, OpenRouter, OpenAI or your own gateway. Enter the address (usually ending with `/v1`), an API key if needed and the model — **Refresh** fetches the list from the server, or type a name. The key is stored encrypted for your Windows account and sent only to that address. **Test connection** says plainly what is wrong: no answer, wrong key, no such model. The smart model and “the model sees images” for your endpoint are under “Smart model and images”. The endpoint serves both answers and “ask AI” command steps.

## Models: fast and for advanced tasks

By default Quick Ask uses the app's default model. You can pick its own model in **Settings → Quick Ask**, or for the current window with the model button at the bottom or ++Alt+M++. Make the main one a fast, cheap model: DeepSeek Flash, Gemini Flash or a local one.

**The model for advanced tasks** is a second, stronger one. Long reasoning, code, multi-step tasks, analysing long documents and pages go to it: some requests are recognised up front, and the fast model can hand a request over itself when it sees it is too hard. Under the answer you see which model answered (⚡ fast or ✦ advanced), and **Smarter** (++Ctrl+Shift+A++) asks the advanced one again. The mode — **Auto**, **Always fast** or **Always advanced** — is set in Settings and switched in the window (++Alt+R++). A model picked manually in the window is used as is.

Pictures and screenshots are seen by the same model that answers: Quick Ask has no separate image model (the main chat has one). For screenshots and images to work, choose a model that understands both text and images — they are marked 👁. If the selected model has no vision, the window says so plainly and offers to pick another.

The answer style is **Brief**, **Balanced** or **Detailed**. Quick Ask has its own short system prompt: the answer on the first line, no preamble, no unnecessary formatting.

## Personality and instructions

In **Settings → Quick Ask → Personality and instructions** choose the assistant's character (concise, coach, teacher, witty…), give it a name and write instructions: what it should know about you and how to answer — your city, currency, programming language, tone. The personality library is shared with MemuAI Live: a preset saved there (the **Library…** button) appears here too. The choice is separate, though — Live stays a conversation partner, Quick Ask stays an assistant for getting things done. By default there is no personality: a neutral assistant answers directly.

## Screenshots

The camera button attaches a screenshot of the monitor the window is on (with ++Shift++ — of all monitors). The model can also take one itself when you ask about what is on screen: "what is this error?", "translate the text in this window".

MemuAI windows never appear on the screenshot — neither Quick Ask nor the main window. On Windows 10 version 2004 and newer this uses the system's capture exclusion; the windows do not flicker or lose focus. On older versions DWM hides MemuAI windows for an instant.

Turn on **Attach a screenshot on open** to have the screen under the cursor attached right away. You can also paste an image with ++Ctrl+V++ or drop a file onto the window. A dropped text file (code, notes, a log, CSV — up to 400 KB) becomes context for the next question.

## Web search

It works like in the main chat and uses the source from **Settings → Web search**. When a question clearly needs fresh data (weather, exchange rates, news), the search runs at once, and weather, time and currency rates appear as a ready card even before the model answers. The globe button turns search off for the current window.

## Instant answers

Simple arithmetic and unit conversion are computed by the window itself, without a model or the network, while you type: `2^10 + 15%`, `15% of 2400`, `√2*3`, `5 km in miles`, `72 F to C`, `3 GB in MB`. The result appears at the right of the input: click to copy, ++Tab++ to put it into the field.

## Selected text

Turn on **Selected text** and, when the window opens, it takes the text selected in the active program and offers **Explain**, **Translate**, **Fix**, **Summarize** or **Reply**. MemuAI presses ++Ctrl+C++ for you and immediately restores the previous clipboard contents. If the clipboard holds an image or files, nothing is done so they are not lost. The feature is on by default; you can turn it off in Settings.

## Position and several monitors

Drag the window by the strip at the top, the orb left of the input or the empty part of the bottom bar. Near a screen edge or corner it docks (a hint shows where), and docked to the bottom it grows upwards. The spot is remembered per monitor: on the laptop the window can live in the top-right corner and on the external display at the bottom centre. Double-click the strip to put the window back to the default spot; ++Ctrl+Alt+arrows++ dock it to an edge.

**Monitor** — where the window (and your panels and buttons on the screen) opens: **With the mouse cursor** (default), **With the active window** (the monitor that holds most of the window you work in) or **Primary**. A remembered spot is applied on the chosen monitor: a window docked to the top-right corner opens in the top-right corner of the other monitor too. If the window is open on one monitor and you press the hotkey on another, it moves there. A pinned window stays where you left it.

**Where it opens**: "Where I left it" (default), centred in the upper third, near the cursor, at any edge or in any corner. Each monitor's scaling, a taskbar on any edge and monitors left of or above the primary one are all taken into account. The window grows with the answer but never leaves the work area: a long answer scrolls inside.

Drag the left or right edge of the window to change its width — MemuAI remembers it.

## Panels and quick actions on the screen

The MemuAI panels (weather, to-dos, notes, timers, PC, AI panels — the same as in MemuAI, updated with them) and your **quick actions** — round buttons and multi-step routines — sit **anywhere on the screen** around the Quick Ask window. They appear with the window on the monitor it opened on and hide with it. Around them everything works as usual: a click next to a panel reaches the program under it.

- **Dragging** — hold the left mouse button on a panel or a button and move: the item follows the cursor at once, no arrange mode needed (a plain click stays a click). Items stick to the screen edges and to each other — guide lines appear; hold ++Alt++ to place freely. A dashed outline shows where the item will land. ++Esc++ while dragging puts it back.
- **Size** — pull the grip in the bottom-right corner (it appears on hover) or pick S / M / L / W in the menu. When a panel's content does not fit the chosen size, the panel compacts itself (tighter spacing, no source line, smaller numbers) and the menu offers **Fit the size to the content**.
- **Menu** — right-click an item, its ⋯ button, ++Shift+F10++ or the menu key: size, refresh, options, open in MemuAI, arrange, undo, remove. The menu closes with a click anywhere, ++Esc++ and after an action.
- **Undo** — ++Ctrl+Z++ undoes a move, a resize or a removal, ++Ctrl+Shift+Z++ (or ++Ctrl+Y++) redoes it.
- **Ctrl+B** hides or shows the panels and buttons; **Ctrl+E** or a long press on an item — **arrange everything**: the screen dims a little, items get ✕ (remove) and resize handles on their edges, the bar on top has undo, redo, "Add" and "Done". From the keyboard: ++Tab++ selects an item, ++Alt+arrows++ (plain arrows while arranging) move it by 8 px, with ++Shift++ by 32, ++Ctrl+arrows++ resize, ++Delete++ removes, ++Esc++ finishes.
- Panels and buttons never go under the Quick Ask window, nor — while there is room — where its answer grows, and never overlap.
- The arrangement is remembered **per monitor**. On a monitor where you have not arranged anything yet, MemuAI takes your latest arrangement and fits it: what was at an edge stays at the edge; on a smaller screen the distances shrink, big panels get smaller, and an item with no room at all is not shown for now (the window tells how many did not fit).
- A button runs only on a click (not in the first 0.3 s after it appeared), and everything that needs your confirmation — the first run of a button with risky steps, dangerous steps, an AI answer — appears in the Quick Ask window.
- To add a panel or a button: ++Ctrl+K++ → **New quick button**, the gear next to "Panels" under the input, or **Add** while arranging. Settings — **Settings → Quick Ask → Panels and quick actions** ("On the screen when Quick Ask opens": show, hide or as last time).

## Appearance

The window follows MemuAI's design, accent and light or dark theme. On Windows 11 22H2 and newer it uses the system glass effect (acrylic or mica); elsewhere MemuAI's own translucent card; switch it in **Glass effect**. Windows itself rounds the window's corners (system rounding on Windows 11, the window shape on Windows 10) and the card uses exactly that radius, so there are no light corners around it, with or without the glass effect. If animations are reduced in **Appearance** or "reduce motion" is on in Windows, the window animates less too.

## Screen region, clipboard, snippets, stickers

- **Ask about a screen region** — ++Ctrl+Shift+X++, right-click the camera or ++Ctrl+K++. The screen freezes: drag a rectangle (++Enter++ — the whole screen, ++Esc++ — cancel). The region is attached to the question and **Extract text**, **Copy text** (the recognised text goes straight to the clipboard), **Translate** and **Explain** appear above the list (++Alt+1++…++Alt+4++) — or just type your own question. Needs a model that sees images. Settings can give it a hotkey that works from any program.
- **Clipboard history** — ++Alt+V++. Off until you turn it on. Keeps text only and only on this computer; never records what password managers copy, anything apps mark “not for history”, or strings that look like passwords and keys. ++Enter++ pastes an item back into the program, ++Tab++ attaches it to the question, ++Ctrl+P++ pins it. Pause and clear are there too.
- **Snippets** — type `;` and a keyword: `;date` pastes the date right into the program you were typing in. Snippet text understands `{date}`, `{date+1}`, `{time}`, `{weekday}`, `{clipboard}`, `{selection}`, `{input}` and `{cursor}` (where the caret ends up). **AI templates** (`;no`, `;formal`, `;short`, `;fix` and your own) process the text typed after the keyword, the selected or the copied text: `;no Can you help me move on Saturday?`. Manage them via ++Ctrl+K++ → **Snippets** or in Settings.
- **Stickers** — the **Sticker** button under an answer (++Ctrl+Shift+P++) pins it as a small window on top of all windows. Move it, resize it, change its colour, edit it with a double click; open stickers come back after MemuAI restarts. The list is in ++Ctrl+K++ → **Stickers**.
- **Documents** — drop or paste a PDF, DOCX or a long text file (up to 15 MB): its text becomes the question's context (“summarize”, “what are the deadlines in the contract?”). For very long documents the model sees the beginning and the end. Read a scan without a text layer via **Screen region**.

Everything is turned on and set up in **Settings → Quick Ask → Features → Clipboard, snippets and stickers**.

## Explorer, programs and tools

++Alt+Space++ over File Explorer — AI works with the open folder and the selected files (with a preview and undo of changes); over VS Code, Word, Excel and terminals — with the open file or document. Plus currency under the input, an eyedropper, text from the screen without AI and notes to MemuNote — see [Quick Ask in Explorer and other programs](doc:quick-explorer).

## Wake word

Say **“Hey Memu”** — the window opens already listening. You can say the command in the same breath: “Hey Memu, what's the weather?”. The recognised text goes out as a normal question: commands like “volume 30” run at once, the model answers the rest. ++Esc++ cancels, the mic button finishes and sends.

- It works **offline**: audio is processed on this computer only (Vosk, a model of about 45 MB is downloaded once). While the wake word listens, the MemuAI tray icon shows a red dot.
- Any phrase of 2–4 words works, nothing to train. A word the model does not know (like “Memu”) is caught by sound, and the settings say so; ordinary words are the most reliable.
- It never triggers while MemuAI speaks, during MemuAI Live or voice input, or while the computer is locked (the microphone is released then).
- False triggers — lower **Sensitivity**; it does not hear you — raise it and press **Test**: the bar shows the sound level and the line below shows the phrase it heard.

Turn it on in **Settings → Quick Ask → Features → Wake word** (off by default).

## Extensions

When you ask it to **do** something on a website ("book…", "order…"), the window offers to hand it to the [Do For You](doc:dfy) agent — or press ++Ctrl+Enter++. The task keeps running when the window is hidden.

Quick Ask is extended by plugins: commands and system actions, voice and the animated companion add their buttons, panels and tools to this same window. A plugin gets at most one footer slot — other buttons go to **Actions** (++Ctrl+K++). Features settings are separate sub-pages under **Settings → Quick Ask → Features**; everything rare there is behind “Advanced”.

## Troubleshooting

- **The window does not open.** Check the status in **Settings → Quick Ask**. "Hotkey taken" — choose another. "Possible conflict" — turn the combination off in the named program.
- **It opened on the wrong monitor.** Check the **Monitor** setting: by default the window opens where the mouse cursor is; you can choose the active window's monitor or the primary one.
- **The panels and buttons are not visible.** Press ++Ctrl+B++ in the window; check "On the screen when Quick Ask opens". On a small monitor some items may not fit — arrange them again (++Ctrl+E++).
- **"The model cannot see images".** Choose a model marked 👁 (for your own endpoint set “The model sees images: Yes” if you are sure).
- **Your own endpoint does not answer.** Press **Test connection**: the address must point to an OpenAI-compatible API (usually `…/v1`) and a local server must be running.
- **No page chip.** The browser must be the active window when you press the hotkey, and **Browser page** must be on. If the address could not be read, the chip shows only the title and MemuAI reads the visible text of the tab.
- **The hotkey cannot be set.** Click the field again and press the whole combination without releasing the modifiers; ++Esc++ cancels.
- **In a browser.** Quick Ask is a feature of the MemuAI Windows app; in a browser only the settings page is available.
