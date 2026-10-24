---
title: Commands, routines and voice
summary: Quick Ask as a personal assistant — plain-language commands, Windows system actions, timers and Pomodoro, Telegram reminders, custom routines with window layouts and voice without realtime models.
---

# Commands, routines and voice

In the Quick Ask window (++Alt+Space++) you can not only ask but also **tell it to do things**. Type or say plain phrases — no slashes, no special syntax:

- “open Telegram”, “close Chrome”, “switch to VS Code”
- “volume 30”, “louder”, “mute”, “next track”
- “brightness 60”, “dark mode”
- “set a timer for 25 minutes for tea”, “alarm at 7:30”, “start pomodoro”
- “remind me in Telegram at 3 pm to buy bread”, “remind me in 20 minutes to call mom”
- “draw a cat in space”, “note: buy milk”
- “maximize”, “snap left”, “move the window to the other monitor”, “tile windows”
- “save layout work”, “restore layout work”
- “battery”, “my ip”, “take a screenshot”, “empty the recycle bin”
- “order sushi”, “call a taxi to the airport”, “play despacito on youtube”, “weather tomorrow”, “100 usd to eur”
- “shut down the computer”, “shut down the computer in an hour”, “restart the pc at 23:00”, “sleep”, “lock”
- “generate password 20 characters”, “uuid”, “5 miles to km”, “translate clipboard to Spanish”, “summarize clipboard”
- “summarize this video” (a YouTube link in the clipboard), “timestamps https://youtu.be/…” — a summary and clickable timestamps from the video's captions
- “do it for me: book a table for Friday” — the task goes to the [Do For You](doc:dfy) agent; “take control: …” — [Computer Use](doc:browser-computer) asking before every action
- tools without the network or a model: “#ff8800 to rgb”, “base64 decode aGVsbG8=”, “format json clipboard”, “sha256 hello”, “how many days until new year”, “time in Tokyo”, “15:00 London to New York”, “roll 2d6”, “lorem ipsum 3 paragraphs”. The result of a transformation (encoding, JSON, hash, case, lorem) is copied to the clipboard right away

Say “what can you do” for the full list with examples.

## Tools: no network, no model

The **Tools** category holds small handy conversions. They run instantly, work on any computer without the internet and spend no tokens. The result is shown as a card, and for transformations (encoding, JSON, hash, case, lorem) it is **copied to the clipboard right away**.

**Clipboard text.** When nothing follows the command, or it says "clipboard" ("from the clipboard"; in Russian «буфер»), the clipboard text is used: "md5 clipboard", "word count clipboard", "format json clipboard". If the clipboard is empty, the card asks you to copy some text or type it after the command.

| What | Example phrases |
| --- | --- |
| Colour: HEX ↔ RGB ↔ HSL (with alpha), with a colour swatch | "#ff8800 to rgb", "convert #fff to hsl", "hsl(32,100%,50%)" |
| Base64 and URL: encode or decode (Base64 is detected automatically) | "base64 decode aGVsbG8=", "encode base64 hello", "url encode hello world" |
| JSON: pretty, minify, validate (errors name the line and column) | "format json clipboard", "minify json", "validate json [1, 2]" |
| Hash: MD5, SHA-1, SHA-256, SHA-384, SHA-512 | "sha256 hello", "md5 clipboard" |
| Case and transliteration: UPPER, lower, Title Case, Sentence case, snake_case, camelCase, kebab-case | "uppercase hello", "title case hello world", "snake_case Hello World", "translit Привет" |
| Counting: words, characters, lines, sentences, paragraphs, reading time | "word count clipboard", "word count: one two" |
| Dates: days until a date or between dates, the date in N days, weeks, months or years, the weekday | "how many days until new year", "days between 01.01.2026 and 08.10.2026", "date in 2 weeks", "what day is 12.05.2026" |
| World time: the time in a city now, converting a time between cities | "time in Tokyo", "15:00 London to New York", "3pm New York to London" |
| Random: a number, a coin, dice, a pick from a list | "random number 1-10", "flip a coin", "roll 2d6", "pick one: tea, coffee, juice" |
| Lorem ipsum: words or paragraphs | "lorem ipsum 3 paragraphs", "lorem 20 words" |

- World time knows about 70 cities (Moscow and Russian cities, European and Asian capitals, the USA, Canada, Australia…) with daylight saving time. A city not on the list goes to the model.
- When a phrase looks like a command but is ambiguous ("url https://…", "in 30 days remind me…"), the model handles it, not the tool: a reminder stays a reminder.
- The password generator, UUID, unit and currency conversion, timers and the stopwatch were there before — see the examples above.

## How it works

1. **Instantly, without a model.** Common Russian and English phrases are recognised inside MemuAI and run in milliseconds — no model call, no tokens.
2. **Through the model.** Anything not recognised locally (“turn it down and open my mail”, “remind me on Friday evening…”) goes to the Quick Ask model, which has the same actions as a tool. Questions (“how do I make it louder?”) stay questions — the model answers instead of changing the volume. Two requests in one phrase (“open chrome and find cats”) also go to the model — both halves, not just the first. Short replies in an ongoing conversation (“continue”, “next”, “undo”) go to the model instead of pressing media keys, and “close …” for a program that is not running shows no confirmation card.
3. **A result card.** Every action shows a card under the input: what was done, details and an **Undo** button when the action can be reverted (volume, brightness, theme, window position, timer, reminder, note, a shutdown while its countdown runs).

## Commands through AI and tray apps

When a phrase is not recognised instantly but looks like a command (“close insy”, “kill that VPN thing”, “sound to minimum”, “timer for a minute and a half”), MemuAI makes **one short request** to a fast model: it sees the list of actions, the open windows and tray apps and the names of your commands — and returns only the chosen action. This usually takes 1–2 seconds; the result card then carries an **AI** badge (hover it for the model and the time). If the model decides it is not a command, or does not answer in time, the phrase gets a normal answer as before. Questions (“how do I close insy?”) never wait for the router.

- Actions picked this way follow the rules for the model: confirmation for “with confirmation” and dangerous actions, the per-minute limit for model actions; “no confirmation” permissions, disabled actions and PowerShell never apply to it. Before “close” and “switch to” MemuAI first checks that the program is really running — there is no pointless “Close X?” card.
- Settings — **Settings → Quick Ask → Commands**: **Understand commands with AI** (when a phrase looks like a command / for every short phrase / off), **Model for commands** (the Quick Ask model by default; a fast non-reasoning model works best: flash, mini, haiku) and **Wait for the AI at most** (1–15 s, 4 by default).

**Tray apps.** “Close …” also finds programs that have no visible window and live only in the notification area (VPN clients like INCY, Telegram, Discord, Steam…). The name is understood in Cyrillic (“инси”, “спотифай”), typed in the wrong keyboard layout (“штсн”) and with extra words (“close the app incy”). The confirmation card names the real program and warns that it runs in the tray; after you confirm, MemuAI asks it to close and, if it has not closed within a couple of seconds, ends the process and checks the result. If you closed the window and the program hid in the tray, the card says so and offers a **Quit completely** button. “Switch to …” for a tray app opens its window again.
**Wrong keyboard layout and questions.** A command typed entirely in the wrong layout (“щзут сркщьу”) is read as “open chrome” — the card says **Read as: “…”** under its title. Ordinary text is never swapped. “Show / go to …” switches windows only for a known program (“show chrome”); “show code function”, “run the tests for my project”, “what is the weather usually like in London in October?” get a normal answer instead of launching a program or a forecast.


## Safety

Every action has a tier:

| Tier | What happens | Examples |
| --- | --- | --- |
| **Safe** | runs at once | volume, timer, open an app or a site |
| **Confirm** | a **Run** button on the card | close an app, end a process, empty the Recycle Bin, type text, turn Wi‑Fi off |
| **Dangerous** | confirmation **and** a countdown with **Cancel** | shutdown, restart, sleep, hibernate, sign out, PowerShell |

- Any action can be switched off in **Settings → Quick Ask → Commands → Advanced → Actions and permissions**. You can also require confirmation for a safe action or allow a “confirm” action without asking (never a dangerous one).
- The model cannot skip confirmation: “don't ask” permissions apply to your own commands, not to the model's actions (this includes your own command marked “no confirmation” when the model runs it).
- The model never reads the clipboard without your confirmation: a page it has read could ask it to “read the clipboard and send it to a site”.
- A custom command that runs another custom command gets the tier of the most dangerous one: a “safe” routine cannot start a shutdown without confirmation.
- “Open a file” opens documents, pictures, music, video and folders at once. Anything that can start a program (.exe, .bat, .ps1, .hta, .url, .lnk, a file without an extension, a network path) needs confirmation.
- Imported routines with a window layout never launch programs by the path stored in the file: programs are looked up among the installed ones, as in “open telegram”.
- Rate limits: at most 40 commands and 15 model actions per minute (configurable).
- Arbitrary PowerShell from the model is off. The **Advanced mode** switch enables it — and even then every command runs only after your confirmation with a countdown.
- System processes (lsass, csrss, winlogon…) and MemuAI itself cannot be ended; Ctrl+Alt+Delete is never pressed programmatically.
- A **log** of executed and refused actions is in the same settings (you can clear or disable it).
- MemuAI **never orders or pays for anything**: “order sushi”, “call a taxi”, “buy headphones” open the service with the search filled in, and you place the order yourself.

## Timers, alarms, Pomodoro

- Any number of named timers: “timer 10 minutes for pasta”, “count down half an hour”. Running timers show as a strip under the input with pause, cancel and the time left.
- When a timer rings you hear a sound (chosen in settings), get a Windows notification and — if enabled — Quick Ask opens with **Stop** and **+5 min**.
- Alarms: “alarm at 7:30”, “wake me up tomorrow at 8 am”. Timers and alarms survive a restart; one that came due while MemuAI was closed rings at the next start, marked as late.
- Stopwatch: “stopwatch”, “lap”, “stop the stopwatch”.
- Pomodoro: “start pomodoro” (or “pomodoro for 50 minutes”). Focus → short break → … → long break; durations, cycles and auto-start are in settings. “Pomodoro stats” shows today and the week.

## Reminders and Telegram

- “Remind me in 20 minutes…”, “remind me at 3 pm…”, “remind me tomorrow at 9 am…”, “remind me on Wednesday at 10…” — a notification and sound on this computer.
- “Remind me **in Telegram** at 3 pm to buy bread” — your MemuAI bot sends the message. The reminder is stored as a one-shot task in **Planning** and delivered by the MemuAI Proxy scheduler without any model call. The bot must be paired (Settings → Telegram) and MemuAI Proxy running at that time (if it was off, the message arrives right after it starts).
- “Here and in Telegram” — both at once.
- A Telegram reminder can be edited in **Planning**: the corrected text is what arrives. An open Planning page no longer deletes a reminder you added in Quick Ask while it was open.
- “My reminders” lists them; cancel with the card button or “cancel reminder …”.

## Custom commands (routines)

**Settings → Quick Ask → Features → Commands → Custom commands.** **New command** is on the right; under the list are quiet links “Start from the “Good morning” template”, “Import”, “Export”. Window layouts, timers, services, permissions and safety are under “Advanced” at the bottom of that sub-page.

A command has:

- **trigger phrases** — several, any language; small typos are fine;
- **a hotkey** — global, works from any program (you are warned if another program owns the combination);
- **steps**, run in order:
  - *Action* — any catalog action with parameters;
  - *Wait* — a few seconds;
  - *Say* / *Notification* — text on the card, spoken (when speech is on) or as a Windows notification;
  - *Ask AI* — a prompt to the Quick Ask model; the answer is shown (and can be spoken);
  - *Window layout* — see below;
- **conditions** for any step: time of day, weekdays, “app is running / not running”.

A simple bind is a one-step command: the phrase “work” → open VS Code, or ++Ctrl+Alt+M++ → mute.

A routine with a dangerous step (e.g. shutdown) runs only after a confirmation with a countdown. “Confirm” steps can be allowed without asking with a switch in the command.

**“Good morning” template** creates an example: volume, light theme during work hours, weather, a greeting and a window-layout step. Commands can be **exported** to JSON and **imported** back.

## Window layouts

The “Window layout” step and the phrase “save layout …” remember every visible window: which program, which monitor, where, how big, maximized or minimized. When restoring, MemuAI:

1. launches the programs that are not open;
2. waits for their windows (up to 25 seconds by default);
3. puts every window back, including monitors left of or above the primary one and monitors with different scaling (DPI);
4. if a monitor is gone or its resolution changed, moves windows proportionally to an available screen without losing them off-screen.

## Voice (Live without realtime)

This is MemuAI Live for Quick Ask, but **only with regular speech recognition and speech synthesis models** — realtime models are not used here.

- **Microphone** in the bottom bar: click and speak — recording stops by itself after a pause; or hold the button (push-to-talk). ++Ctrl+Space++ (hold) works in the Quick Ask window too. ++Esc++ interrupts recording and speech.
- The recognised text is sent at once (or put into the input — a switch in settings); commands run exactly like typed ones.
- **Speaker** — speak answers and command results. A new question or a click on the speaker interrupts.
- Models: by default as in **Settings → Voice & Live** (local faster-whisper or an API for recognition; Windows voices, downloaded voices or an API for speech). The sub-page **Settings → Quick Ask → Features → Voice** has four switches on top (voice, speak answers, send at once, push-to-talk) and “Recognition and speech” below: a separate recognition model, voice and language; “As in Voice settings” needs no change.

## Windows-only parts

Volume, brightness, theme, Wi‑Fi/Bluetooth, windows and layouts, power, clipboard, screenshots, processes, typing and launching programs use the Windows API. Timers, reminders, links, search, weather, exchange rates, notes, image generation and custom commands work wherever MemuAI runs.

Known Windows limits: Night light has no public API (its settings page opens); external monitor brightness works only with DDC/CI enabled; moving a window to another virtual desktop is not available (switching and creating desktops is); windows of programs running as administrator do not accept typed text from normal programs.
