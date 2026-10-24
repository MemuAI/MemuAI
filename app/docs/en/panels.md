---
title: Panels
summary: Live cards on the home screen, in the sidebar and in the top bar — weather, rates, topic news, Claude/Codex/Antigravity limits, PC performance, tasks, notes, clocks, timers, calendar, GitHub and custom panels built by the model.
---

# Panels

A panel is a small live card that refreshes itself on a schedule: weather, a currency rate, crypto, stocks, RSS, a countdown, sports, topic news, AI subscription limits, PC load, MemuAI Proxy statistics, Google Calendar events, GitHub notifications, or anything you ask the model to gather. Some panels need no network at all: tasks, a note, world clocks, Pomodoro and timers. The main places for panels:

![Home panels](docs-img/en/panels.webp "Home panels: tasks, a countdown, a note and a clock")

- **Home screen** — under the new-chat composer, in four sizes: **small** (¼ width, the main value), **medium** (½), **large** (½ width with more detail: forecast, chart, more rows) and **wide** (full width). Cards pack tightly with no holes: neighbours of different heights stack under each other.
- **Sidebar** — a compact version in the “Panels” group.
- **Top bar** — a mini chip with the value (“48 % CPU”, “85.48 ₽”); whatever does not fit folds into “+N”.

Five more places — above the greeting, above the message box, the left and right columns, the sidebar bottom — are described in [Layout](#layout). One panel can sit in several places at once. Reorder by dragging; change places and size in the card’s ⋯ menu or in **Settings → Panels**. The menu also opens with a right click on the card (or ++Shift+F10++): places and size change right in it without closing it; it closes with a click anywhere, ++Esc++ or scrolling.

## Layout

Panels have eight places in total — they are shown on a small sketch of the window (the “place map”) in the panel’s create and customize dialog and in the right-click menu → **More places…**:

| Place | Where | Look |
| --- | --- | --- |
| Top bar | the window title bar | chips |
| Above the greeting | start screen, above the greeting | cards or chips |
| Above the message box | start screen, right above the box | chips or cards |
| Below the message box | the classic card grid | cards |
| Left column, right column | at the edges of the start screen, 200–480 px wide | cards or chips |
| Sidebar | the “Panels” group | cards or chips |
| Sidebar bottom | above the profile at the bottom of the sidebar | chips or cards |

A chip of a panel without a number (a note, a text panel) shows its first line. Start-screen places can also stay visible **inside chats** (the “In chats too” toggle). In a narrow window the columns fold into a strip of chips and in a very narrow one they hide; the right column hides while a site preview is open. Start-screen panels are not shown in MemuCode, Work and temporary chats.

### Layout mode

The **Layout** button above the panels (or right click on a panel → **Edit layout**, or **Settings → Panels**) turns on layout mode. Every place is outlined with a dashed frame and a label, the whole start screen scrolls and nothing is squeezed.

- **Drag** — a card can be moved to any place; the target is highlighted and the screen scrolls by itself near the edges.
- **Size** — the card under the pointer gets a small **S · M · L · W** bar and a corner handle: drag the width along the grid columns (or through preset widths in a strip). A column’s width is changed with the handle on its edge; a double click on the handle restores the default width.
- **⚙** on a card — name, place map, size, “All settings…” and delete; **×** removes the panel from this place only.
- **＋** in every place opens the gallery right for it; each place has view toggles (**Cards** / **Chips**) and “In chats too”.
- **Undo / Redo** — buttons on the mode bar and ++Ctrl+Z++ / ++Ctrl+Shift+Z++ (or ++Ctrl+Y++). **Reset** restores the default places and views (the new places move below the message box) — and this can be undone too.
- **Keyboard**: arrows move the card within its place, the “+” and “−” keys change its size, ++Delete++ removes it from the place, ++Esc++ or **Done** exits.

Outside the mode, dragging between places works too and shows a notification with an “Undo” button; ++Ctrl+Z++ undoes the move for a couple of minutes afterwards unless the cursor is in a text field.

Panel places take no room for scrollbars: a thin thumb appears at the edge on hover or scrolling, can be dragged with the mouse and fades out after a second, and the list edges fade softly when there is more to scroll.

## Adding a panel

1. Click **＋ Panel** above the home-screen cards (or in the sidebar “Panels” group).
2. Pick a template in the gallery, fill in a couple of fields and click **Add**.
3. Or ask in a normal chat: “make a panel with the bitcoin price” or “a panel with Rust news every morning” — the model calls the panels tool and a preview appears right in the answer.

The card appears **already filled**: built-in sources answer within seconds. When a build takes longer (a news feed, a custom AI panel) the card shows its stages — “Planning sources → Reading sources → Merging duplicates → Ranking significance and credibility → Writing a short digest” — and fills in as soon as everything is ready.

## What to show

Every panel’s **Customize…** window has a “What to show” block: weather fields (feels like, wind, humidity, pressure, UV, visibility, sunrise and sunset, precipitation), an hourly or daily forecast, extra currencies and the chart period for a rate, the period for crypto and stocks, the number of entries and descriptions for RSS and feeds, the countdown format, limit windows and so on. On the right is a live preview of the card as it will look after saving. Rare settings (units, precision, the panel’s own model) are tucked into **Advanced**.

## Refresh cadence

Choose **every minute, 5, 15, 30 minutes, 1, 3, 6, 12 hours, daily at HH:MM** or **manual only** — or set your own: “every N seconds/minutes/hours/days”, **at specific times** (say 08:00 and 19:00) on chosen weekdays, or “only on some days and hours” (a window like 09:00–18:00 on weekdays; a window across midnight works too). Next to the choice the window explains why this type has its floor. Each type has an honest floor — more often the source does not change or starts refusing:

| Type | At most |
| --- | --- |
| PC performance, AI limits, crypto, stocks | once a minute |
| RSS | every 5 minutes |
| Weather, central-bank rate | every 15 minutes |
| News, sports, web page, custom AI panel | every 15 minutes (each refresh runs a search and the model) |
| Countdown | hourly |

If a source answers “too many requests” (HTTP 429) the panel waits as long as its `Retry-After` asks, and the last values stay on the card marked “stale”. Transient failures retry with a growing pause; “walls” only you can clear (sign-in needed, missing key, the page moved) stop automatic refreshes and say what to do.

- **Manual only** — a panel with this rhythm does not refresh by itself even after a failure: the old data stays and the card says to press “Retry”. The exception is the very first build, while there is no data yet: MemuAI retries it itself.
- **Editing during a refresh.** If you change the title, “What to show” or the parameters (a city, a feed address) while the panel is refreshing, your changes are not rolled back: the refresh with the old parameters is discarded and a new one starts right away. A panel's own title (a countdown's too) is never overwritten by a refresh.
- **Refresh** pressed during a running refresh simply waits for it.
- Closing MemuAI in the middle of building a panel is not counted as a failure: the build continues on the next start.

## Topic news

The feed is built from primary sources, not from random sites in the interface language:

- **Source plan** depends on the topic. For the ready-made topics — AI, tech, science, crypto, security, space, gaming — it is a vetted list: official company blogs, Hacker News, Reddit, specialist media and a few precise search queries. For global topics the sources are in English, so the release of the day is not missed. For a custom topic the model proposes the plan and feed addresses are verified by reading them.
- **Duplicates are merged**: one story from several places is one entry, and confirmation by several sites ranks it higher.
- **Significance and credibility ranking**: source authority, community response, freshness; “corporate filler” (customer stories, webinars) goes down, rumours are flagged. When a model is available through MemuAI Proxy it refines the ranking and writes a short summary in your language.
- **Diversity**: no more than about 40 % of the feed about one company or from one site.
- **Already shown stories do not repeat.** To start over, choose **Rebuild from scratch** in the ⋯ menu.

**Details** (or “N more” when there are more entries than fit) opens the whole list: link, source, a short summary and a “why it is here” line explaining why the entry made the feed. How many entries the card shows depends on its size.

X/Twitter is not read: it has no reliable open access, and the panel honestly skips it.

## AI limits

The “AI limits” panel shows **Claude**, **Codex**, **Antigravity**, **Kiro** and **OpenCode** accounts connected through Proxy, estimated **API balances**, and **quota planner** accounts. Subscriptions show windows published by the service: percent used, reset time and whether spending outpaces elapsed time. You can add several accounts, each with its own name (“Work”, “Personal”).

Where the data comes from:

- **Automatically** — from files already on the PC: Claude Code (`~/.claude`), Claude Desktop, Codex (`~/.codex/auth.json`), a running Antigravity. Other apps’ files are only read and never modified, so the CLI stays signed in.
- **A custom config folder** — for a second account of the same service (like `CLAUDE_CONFIG_DIR` or `CODEX_HOME`).
- **A pasted token** — stored apart from panels, encrypted with your Windows account (DPAPI) and sent only to the service it belongs to. The token is never returned to the window.
- **Proxy account** — choose Kiro, OpenCode, API balance or quota planner, then select a connected account. No key needs to be pasted again; a separate button refreshes the list. For an API balance, first add the account and its starting balance in Proxy's planner.

API balances are estimates based on requests recorded by Proxy, not a live provider billing balance. External spending and requests with unknown prices may be missing; the warning and unknown request count appear beside the values. A planning end date does not mean the balance will refill. OpenCode Go displays published API windows; Free/Zen/gateways may provide no numeric quotas. Check times, errors and stale data are labelled explicitly.

The account’s **Test** button immediately shows its limit windows or a clear reason why there is no data. Antigravity reports quotas only while it is running. With no data the card says so; numbers are never invented.

## PC performance

A local panel with no drivers or third-party tools. In “What to show” you pick and drag into order: CPU (total and every core), memory and the page file, the GPU and its memory, disks (the main one or all), network (total or each adapter, in bytes or bits), disk throughput, temperatures and fans, battery, uptime and the busiest processes. Each metric can be a ring, a bar, a number or a chart; charts keep 30 seconds to 15 minutes of history.

**Live mode** (off, 1, 2 or 5 s) updates the values right in the window while the card is visible; a minimised window polls nothing. The chosen cadence is for the mini chip and history. What the system does not report (CPU temperatures and fan speeds on Windows without vendor drivers) is honestly shown as “—” or “no data”.

## Tasks and other local panels

- **Tasks (To-Do).** Several coloured lists; a task has a due date and time, a priority (!1–!3), tags, notes, subtasks and a repeat (daily, weekdays, weekly, monthly, yearly — a finished repeating task moves to its next date). Quick add understands “Buy bread tomorrow 18:00 !1 #home”, “fri”, “25.10”, “every week”. **Remind** sets the same reminder as “remind me…” in Quick Ask — a sound and a Windows notification or Telegram, chosen there; if the Telegram bot is not paired, the reminder rings on this PC instead. Keyboard: Space — check, Enter/F2 — edit, Delete — delete (with Undo), Alt+↑/↓ — reorder, ←/→ — subtasks. Tasks are stored only on this PC.
- **Note** — free text; `[ ]` and `[x]` lines become checkboxes.
- **Clock** — local time and world clocks with the hour difference.
- **Pomodoro and timers** — the same as in Quick Ask: start, pause, +5/+15/+25 minutes.
- **MemuAI Proxy**, **Google Calendar** and **GitHub** — Proxy request statistics, upcoming events and notifications or review requests. Calendar and GitHub are connected in **Settings → Integrations**; without a connection the card says what to do.

## Custom panels by the model

The **Custom AI panel** template (or a request in chat) asks the model to find the data and build the card. Ideas are suggested under the field; grey ghost text is accepted with **Tab** or **→**. The panel’s model: its own model (“Customize… → Advanced”) → “Model for AI panels” in **Settings → Panels** → the model of the chat where it was created → the MemuAI default model. Honesty rules:

- the model never writes values from memory — only what it found in search results and pages;
- the card footer lists only sources the model cited and where the values were actually found (no random search links);
- a list entry keeps its link only if that link was in the gathered material.

## Card look

Cards follow the selected MemuAI design (Classic, Aurora, Focus, Grid, Glass, Soft, Terminal, Brutal) and the light or dark theme: their own fonts, rings, bars and backgrounds. Loading, refreshing and “walls” are shown right on the card. Animations are off when the system asks to reduce motion.

## Where it is stored

The current profile’s panels are in `memuai/panels.json` in the data folder, tasks and notes in `panels-local.json`, pasted tokens separately in `panels-secrets.json`. Up to 40 panels. More about the data folder — [Where data is stored](doc:data).
