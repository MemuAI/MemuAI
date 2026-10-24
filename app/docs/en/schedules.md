---
title: Planning
summary: Jobs MemuAI does on its own — on time, on an event and only under your conditions: an AI answer, a reminder, Do For You, a Quick Access command, MemuCode, Work.
---

# Planning

A scheduled task is a job MemuAI does by itself at the right moment. For example: collect a news digest every morning, remind you to take a break at 16:00 — but only if Dota 2 is running, make a day plan when the computer starts, check an order status in the browser every Monday.

![The Planning window](docs-img/en/schedules.webp "Planning: create a task from a sentence, and the task list with each next run")

Everything is in the **Planning** section of the sidebar.

## How a task is built

Every task is a **rule made of four blocks**. You see them in the section's help card and in the builder:

| Block | What it sets | Example |
| --- | --- | --- |
| **1. When** | a time, days, an interval or an event; any of them starts the task | every day at 16:00; when dota2.exe starts |
| **2. Only if** | conditions checked right before running (optional) | a program is running; MemuAI is open |
| **3. Do** | what to run and where to report the result | a reminder to Telegram |
| **4. Until** | dates and limits (optional) | until October 31; at most 10 times |

Under each task MemuAI writes the rule in plain words: “Every day at 16:00, only if dota2.exe is running” — and when the next run is or what the task is waiting for.

## How to create a task

- **In words.** Type a sentence into **Create from a sentence**, for example “tomorrow from 6 to 10 every half hour while MemuAI is open, collect AI news”, and press **Draft**. The model drafts the rule and MemuAI shows the summary, the next runs and warnings. Press **Create the task** or **Edit in the builder**.
- **In the builder** — **New task** → a blank rule or a template. The blocks are put together with the mouse; the summary and the next runs are always visible at the bottom.
- **In chat.** “Send me the weather forecast every day at 9:00”. The model shows a suggestion card; the task appears only after you press **Add**.
- **With the command** `/schedule Name | prompt | every N min`.
- **From Quick Access** (Alt+Space) — “schedule an AI news digest every weekday at 9:00” (more below).
- **From the phone** — in the Telegram bot's [Mini App](doc:telegram-miniapp) (more below).

Nothing is created without your confirmation.

## From the phone and from Quick Access

You can create a task in words outside the MemuAI window too. In both cases the model only **drafts** the task — it appears after you press the button.

**Mini App (phone).** In the Mini App, open **Settings → Planning**. At the top there is the **Create from a sentence** field with example chips: tap an example or type your own sentence and press **Draft the task**. The **Check the task** sheet opens:

- the rule in plain words and the kind of action (model answer, reminder, Do For You…);
- the name and the task text — you can correct them right there;
- the **next runs** (up to five) or the event that starts the task;
- warnings — for example, that the task runs at the same time as another one;
- the **Result in Telegram** switch (on by default).

Press **Create** — or **Cancel**, and nothing is saved. Below is the task list with the same statuses as on the computer, the **All / Active / Paused** tabs, a pause switch and **▶ Run now** (a test run). Tapping a task opens a menu: run, pause or resume, delete. Conditions, limits and other fine tuning are changed in Planning on the computer. MemuAI Proxy has to be running on the computer for the tasks to run.

**Quick Access (Alt+Space).** Start the sentence with “schedule…”, “remind me every…”, “send me every…”, “every day…”, “on weekdays…” — for example, “schedule an AI news digest every weekday at 9:00”. The Quick Access model drafts the rule and a **New task: …** card appears with the summary, what will be done, the next three runs and warnings. The buttons:

| Button | What it does |
| --- | --- |
| **Create the task** | saves the task; then an **Open Planning** button appears |
| **Edit in MemuAI** | opens the builder with this draft — you can add conditions and limits |
| **Cancel** | saves nothing |

A one-off reminder like “remind me at 15:00 to call” is created by Quick Access itself, without a model (see [Quick Access commands](doc:quick-commands)). If the sentence does not look like a schedule, the question simply goes to the model.

## When to run

| Option | How it works |
| --- | --- |
| Once | at a specific date and time |
| On days at times | one or more times a day, every day, on weekdays, weekends or chosen days |
| Every N minutes | repeats; can be limited to a “from … to …” window, also past midnight (22:00–02:00), and to weekdays |
| Once in a time window | once between “from” and “to” — at the first moment the conditions hold |
| Every month | on a chosen day or on the last day of the month |
| Event | the computer starts, MemuAI Proxy starts, the MemuAI window opens or closes, a program starts or exits, a window gets focus, you step away or come back, the internet appears or is lost, the charger is plugged in, the battery is low, files change in a folder, another task finishes |

An event can have a delay: “2 minutes after the computer starts”.

**Chains.** The “Another task finished” event runs a task after another one — after any finish, only after success or only after a failure. MemuAI does not save a chain that loops back (A after B, B after A).

## Only if

Conditions are checked at the moment of the run. You can require **all conditions** or **any of them**.

| Condition | Example |
| --- | --- |
| A program is (not) running | dota2.exe; the **From running** button lists the programs |
| MemuAI is open | the MemuAI window is open |
| You are at the computer / away | by mouse and keyboard activity within N minutes |
| Active window | part of the window title or the program name |
| Internet is available | a connection check |
| AC / battery, battery level | for laptops |
| Time and days | for example only from 9:00 to 18:00 on weekdays |

> [!TIP]
> The program name is the name of its file: Dota 2 is `dota2.exe`, Steam is `steam.exe`. Case and `.exe` do not matter. If the game is not running yet, start it and press **Refresh** in the program list.

If the conditions do not hold at that moment, the task **skips this time** by default. In the Until block → **Fine tuning** you can choose **wait until they hold** — for at most N minutes.

## Calendar, weather, sun and other conditions

Besides the conditions above, **Only if** has:

| Condition | Example |
| --- | --- |
| Calendar | a workday, a day off (weekend or holiday), a public holiday, a shortened pre-holiday day, the first or last workday of the week or month, “the 2nd Tuesday of the month”, days of the month, months, an even week, a yearly period |
| Daylight / dark | by sunrise and sunset in the chosen city, with a margin in minutes |
| Weather | temperature, feels like, wind, chance of rain, cloud cover, humidity, it is raining or snowing, clear sky — Open-Meteo data, refreshed every 20 minutes |
| Wi-Fi network | connected to the “Home” network or not on Wi-Fi at all |
| Result of the previous run | the previous run of this or another task succeeded, failed or contains a text |
| Group | “A and (B or C)”: inside a group — all conditions or any of them |

Every condition has a **NOT** button (the condition must not hold) and **on / off**: a switched-off condition stays in the rule but is not checked — handy to disable it for a while without deleting it.

**Russia's production calendar.** For Russia workdays follow the official moves of days off, working Saturdays and shortened pre-holiday days. The current year is built in; later years MemuAI downloads from isdayoff.ru (xmlcalendar.ru as a fallback) and keeps on the computer. Offline, the official holidays without moves are used. Other countries use their official public holidays.

**More “When” options:** sunrise or sunset (earlier or later by N minutes), “every month” by weekday (“the last Friday”) or by workdays (“the 3rd workday”, “the last workday of the month”), **The weather changed** — the task runs the moment rain or snow starts, the sky clears or the temperature, wind or rain crosses a value. So that a value hovering around the threshold does not run the task again and again, there is “again — not sooner than N hours”.

**Fine tuning** in the Until block: **quiet hours** (a run waits until they end), **at most N runs a day**, **on failure retry** N times — the pause between tries doubles.

The task text may use `{{date}}`, `{{time}}`, `{{weekday}}`, `{{run_count}}`, `{{last_result}}` — they are filled in at run time.

The **Check the conditions now** button shows every condition: whether it holds and why (3 °C now, today is a pre-holiday day, sunset at 17:42).

**Ready-made scenarios.** **New task → Scenario gallery**: a weekly wrap-up on the last workday, “take an umbrella”, “rain started”, frost warning, an evening review after sunset, a report on the last workday of the month, a reminder before a holiday, retry on failure and more. A scenario opens in the builder — just pick the city and adjust the text.

**On the phone.** In the [Mini App](doc:telegram-miniapp) tap a task → **Conditions and check**: switch each condition on or off, set **NOT**, flip “all / any” and check the conditions now. New conditions are added in MemuAI on the computer.

## What to do

| Action | What it does |
| --- | --- |
| AI answer | the model does the job: a digest, a check, a report; can be bound to a Work project |
| Reminder | exactly your text arrives, no model |
| Do For You | the agent does the chore in a browser — see [Do For You](doc:dfy) |
| Quick Access command | your routine runs — see [Commands](doc:quick-commands) |
| MemuCode | the coding agent does the task in a project folder — see [MemuCode CLI](doc:memucode-cli) |

**Work.** A task can work in a [Work](doc:work) project. In **Full access** mode file changes are applied at once, in **Ask** mode they wait for approval in the task history (the **Apply Work changes** button).

**MemuCode.** Permissions are chosen in the task: “read-only, plan”, “change nothing that needs approval” or “may edit files”. Every run is a normal MemuCode session: the `memucode -r ID` command from the history continues it in the terminal.

**Do For You and commands** run in the MemuAI window, so they work only while it is open. If the Do For You agent needs your answer (a payment confirmation, a code), you get a notification and can answer right in Planning. Dangerous command steps (shutdown, sleep) still ask for confirmation in the Quick Access window.

## Until

- **Start not before** and **End after** — the dates the rule is active.
- **At most N runs** — after the N-th run the task finishes.
- **Stop after N failures in a row** — so a broken task does not run forever.

**Fine tuning:**

- what to do if the conditions do not hold at that moment — skip or wait;
- if the computer was off at that time — run once when it is back (if late by no more than N hours) or skip. By default clock tasks catch up once, repeating and event tasks do not;
- **random delay** — so many tasks do not start in the same second;
- **minimum pause between runs** — useful for events that may repeat often (a window gets focus, a program restarts).

## Task list and history

Each task in the list has a switch (pause), **▶ Run now** — a test run that skips the conditions and does not use up the run limit, **History**, **Edit** and the **⋯** menu (duplicate, discuss in chat, delete).

The status under a task tells what is happening: “Next run: today at 16:00”, “Waiting until: dota2.exe is running”, “Waiting for the event”, “Paused”, “Finished”.

**History** shows every run marked **test**, **late** and **on event**. A test run also counts as the task finishing, which makes it easy to check a whole chain.

The builder warns about problems: the task runs at the same time as another one, runs more often than every 10 minutes (many model calls), the rule has no future runs, a chain points to a deleted task.

## Result notifications

When a task has run, MemuAI can tell you:

| Channel | Where to turn it on |
| --- | --- |
| Windows notification | **Settings → Notifications** |
| Telegram | in the task; the bot is set up in **Settings → Telegram** |
| Email | the “Email” option in the task; delivery is set up in **Settings → Integrations → Email for scheduled tasks** |

Details — [Notifications and remote access](doc:notifications). Tasks can be viewed, switched and run from the phone in the [Mini App](doc:telegram-miniapp).

## Important

> [!IMPORTANT]
> Tasks are run by **MemuAI Proxy**. While it runs — even if the MemuAI window is closed — the schedule works. If the Proxy is off or the computer sleeps, the task runs at the next opportunity (or is skipped, per the catch-up rule). For the “computer starts” event and tasks right after you sign in to Windows, turn on Proxy autostart with Windows.

The “you are at the computer” and “active window” conditions and the list of windowed programs work on Windows. Conditions are checked every 15–30 seconds and only when enabled tasks need them — the scheduler does not load the computer.

Tasks and results belong to the current [profile](doc:profiles). In a [temporary chat](doc:chats) the model does not create tasks.

See also: [Work](doc:work), [Commands](doc:commands), [Scheduled messages](doc:scheduled-messages).

## Discuss a task with AI

The task's **⋯** menu has **Discuss in chat**: a new chat opens with the task description, where the model helps improve it. Creating a task from chat still needs the “Add” press on the suggestion.

## Telegram Use and the Chrome extension

Under **Do → Telegram Use / Chrome**, choose a tool and enter its arguments. Read personal-account messages, send a reply, read a page or manage tabs. Simple arguments have form fields; nested objects and lists can be edited as JSON.

For read the last 50 messages → summarize → send to Saved Messages, choose **Steps**: add **Telegram Use / Chrome**, **Ask a model**, then another integration step. The first result is `{{step1.result}}`; the model answer is `{{step2.result}}`. Pass these to later steps using the existing variables and conditions.

These actions need the **MemuAI window open**, the integration connected and reading enabled. A task you save authorizes scheduled reading; sending/deleting messages or changing tabs requires a separate scheduled-write permission. Runs do not ask for interactive approval: a disabled permission ends the task with a clear error. A test run reads available data and only previews sending/deleting messages or changing tabs. If unattended Chrome DFY needs a sign-in, code or other missing user input, the run finishes with an error instead of waiting for a form.
