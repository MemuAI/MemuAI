---
title: Notifications and remote access
summary: Windows notifications and sound, controlling MemuAI through a Telegram bot, and emailing task results.
---

# Notifications and remote access

## Completion notifications

**Settings → Notifications**. While the model answers you can switch to another window — MemuAI tells you when the answer is ready.

| Setting | What it does |
| --- | --- |
| Windows notification | a notification in the screen corner when an answer finishes |
| Sound | a short tone after an answer |
| Sound variant | Gentle, Crystal, Pop, Warm chord; the ▶ button plays a sample |
| Volume | 0 to 100 % |

Tones do not overlap when several answers finish at once.

## The Telegram bot

**Settings → Telegram**. The bot is MemuAI on your phone: the same chats, models, profiles and agents, live answers, voice messages, photos and files. It connects in a minute with a QR code or a one-time code — no Chat ID hunting. Notifications about finished answers, tasks, Work and modules are configured per category, with quiet hours.

All the details: [Telegram bot](doc:telegram).

## Email for scheduled tasks

The same section sets up emailing the results of [scheduled tasks](doc:schedules) through your own SMTP server. Mail is sent only for tasks with "Email" ticked.

| Field | Example |
| --- | --- |
| Recipient | `me@example.com`; several — separated by commas |
| SMTP | `smtp.gmail.com` |
| Port | 587 |
| Username | your address |
| Password | an app password — Gmail and most providers will not accept the normal one |
| Sender | optional when it matches the username |
| Connection security | STARTTLS (port 587), SSL/TLS (port 465) or none |

The password is stored only on this computer.

When the security mode contradicts the port (STARTTLS on 465 or SSL/TLS on 587), MemuAI follows the port instead of waiting for an answer the server never sends. An unreachable server fails with a readable error on a timeout (20 seconds per connection step).

See also: [Telegram bot](doc:telegram), [Scheduling](doc:schedules), [Profiles](doc:profiles).
