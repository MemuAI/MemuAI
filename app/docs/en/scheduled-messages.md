---
title: Scheduled messages
summary: A message goes out at the time you choose or when you are back online — like in Telegram.
---

# Scheduled messages

Write a message now and send it later: in the morning, after a meeting or every weekday. MemuAI keeps everything that was in the composer and sends the message at the right moment.

## How to schedule

Open the "Send later" menu in one of these ways:

- **right-click** the send button;
- **press and hold** the send button;
- ++Ctrl+Shift+Enter++ in the composer (the combination can be changed in [keyboard shortcuts](doc:shortcuts)).

The menu has ready choices — in an hour, tonight, tomorrow morning or afternoon, Monday morning — and **Pick date & time…**. There you can type the time or say it in words: "tomorrow at 15:00", "in 2 hours", "in 30 minutes".

Along with the text, MemuAI keeps the attachments, the chosen model and the toggles: web search, reasoning, deep research, image mode.

> [!NOTE]
> A temporary chat cannot schedule: it is not saved anywhere. The menu offers to make the chat permanent.

## Repeat and "when online"

- **Repeat** — every day, on weekdays or once a week. After sending, the message queues itself for the next time.
- **When online** — the message goes out as soon as the internet and MemuAI Proxy are available. Handy when you write offline.
- **Notify in Telegram** — the bot sends a notification when the message is sent (if [Telegram](doc:telegram) is connected).

## The scheduled list

A strip above the composer lists the scheduled messages of the current chat: change the time, edit the text, send now or cancel. **All scheduled** shows the messages of every chat of the profile.

## Who sends

| Situation | What happens |
|---|---|
| The MemuAI window is open, the chat is open | The message goes out exactly like a typed one |
| The window is open, another regular chat | MemuAI Proxy sends it, the answer appears in that chat |
| The MemuAI window is closed | MemuAI Proxy sends regular-chat messages in the background |
| A Work chat or an image model | The window is needed: MemuAI opens the chat and sends when you are not typing |

The queue is stored on disk in the profile, so it survives a restart of the computer.

## Missed messages

If the time came while neither MemuAI nor MemuAI Proxy was running, the message is late. The setting decides what happens:

- **Send** — on the next start, with a notice (the default);
- **Ask** — the message waits for your decision in the list;
- **Skip** — the message is not sent, you get a notice.

## Settings

**Settings → Chat & replies → Scheduled messages**:

- *If the time passed while MemuAI was closed* — send, ask or skip;
- *Send while the window is closed* — let MemuAI Proxy send in the background;
- *Notify about background sends* — a Windows notification about a background or late send;
- *Scheduled messages list* — every chat at once.
