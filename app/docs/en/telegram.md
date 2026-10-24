---
title: Telegram bot
summary: MemuAI on your phone — QR pairing, live answers, voice, files, chats, models and notifications.
---

# Telegram bot

The bot is your MemuAI in Telegram: the same chats, models, profiles and agents as on the computer. Whatever you write to the bot appears in MemuAI, and the other way round.

> [!NOTE]
> The bot runs through MemuAI Proxy on your computer (Bot API long polling): nothing is exposed to the internet. While the Proxy is off or the computer sleeps, the bot is silent.

## Connect in a minute

1. In Telegram open **@BotFather**, send `/newbot` and follow the prompts. At the end it gives you a **bot token**.
2. In MemuAI: **Settings → Telegram**, paste the token into step **1**. The bot's name appears under the field — the token is valid.
3. Press **Connect Telegram** (step **2**). A QR code and a code like `K7QM-3XRP` appear.
4. Scan the QR with your phone camera (or press **Open in Telegram**) and tap **Start** — the bot pairs itself. You can also just send the code to the bot.

The **▶ Video guide** button next to step **1** shows the same as a short clip (silent, with Russian and English captions).

The code works once, for 10 minutes, and is burned after 8 wrong attempts. The status at the top of the panel tells you what is really going on: “Connected · @bot”, “Online · waiting to pair”, “Reconnecting…” (with the reason and the next attempt), “Bot is running elsewhere”, “Token rejected”, “MemuAI Proxy is not running” (with a “Start Proxy” button) or “The bot is not responding”. **Restart bot** at the bottom of the panel restarts Telegram polling with a fresh connection in a few seconds.

> [!IMPORTANT]
> The bot answers only the paired chat (and users from “Additional users”). Everyone else gets one polite refusal and nothing more. The token is stored only on the computer and never reaches the logs.

The old way still works: send `/id` to the bot and paste the number into **Advanced → Paired Chat ID**.

## Using it

| To do | How |
| --- | --- |
| Ask something | just write — the answer streams live |
| Stop an answer | the ⏹ button on the draft, **⏹ Stop** or `/stop` |
| Speak | send a voice message, a video note or an audio file — the local Whisper model on the computer (or the speech service chosen in [Voice](doc:voice) settings) transcribes it and MemuAI answers; with **Show the transcript before sending** the text comes first with a **Send** button |
| Photos & files | photos go to vision models; PDF, DOCX and text files are read |
| Fix a question | edit your last message — MemuAI answers again |
| Under an answer | one row: **↻ Retry**, **🤖 model** (switch), **⋯** — continue, read aloud, new chat, menu |

Three buttons are always at the bottom: **💬 Chats**, **➕ New chat**, **☰ Menu**. The menu is one card that changes in place: model, chats, agents, temporary chat, **⚙️ Settings** and **⋯ More** (profiles, prompt, Work, help). Every screen has “‹ Back”. When the MemuAI Mini App is available, open it with the menu button left of the input field or the **📱 Open MemuAI** button in the menu card.

### Chats

**💬 Chats** — the history from the computer, newest first, pinned on top, with **🔎 Search** over titles and text. A chat card shows the last answer, **📜 History**, **✏️ Rename**, **📌 Pin**, **🗑 Delete**. The selected chat continues with your next message.

**🕶 Temporary chat** (`/temp`) saves nothing.

### Models, profiles, agents, prompts

- **🤖 Model** — favourites (pinned in MemuAI), recent ones and all models by provider; **🔎 Find a model** by part of its name. Models carry readable names as in MemuAI ("Claude Opus 4.6 Thinking · aws9-claude-opus-4.6-thinking"), and the "Now:" line shows both the name and the full id.
- **🧠 Reasoning** — on the model screen, when the current model reasons, a row of buttons offers **its own** levels (GLM-5.3, for example: "Auto, Low, High, Max", no "Off"); the chosen one is marked ✓. The level is saved to the same "Reasoning by model" setting as in MemuAI and applies to the bot's answers and the computer's. Details: [Models → Thinking](doc:models).
- **👤 Profiles** — each with its own chats and memory. A PIN-protected profile asks for the PIN; the PIN message is deleted at once, and 5 wrong PINs lock entry for 5 minutes.
- **🎭 Agents** — your [memes](doc:agents) with their own persona and model.
- **📝 Prompt** — a system prompt for Telegram only (the MemuAI one by default).
- **🗂 Work** — switch the active project and list its files.

## Commands

| Command | What it does |
| --- | --- |
| `/new` | a new chat |
| `/chats [query]` | chats; with a query — search |
| `/models`, `/model name` | choose a model |
| `/image …`, `/video …` | create a picture or a video from a description — see [Pictures and video in the chat](doc:media#in-telegram) |
| `/temp` | temporary chat on/off |
| `/stop` | stop the answer |
| `/retry` | regenerate the last answer |
| `/agents`, `/profiles`, `/prompts`, `/more` | agents, profiles, prompt, “More” |
| `/work` | Work projects |
| `/web` | a one-time link to open the [Mini App](doc:telegram-miniapp#in-a-regular-browser) in a regular browser |
| `/settings` | bot settings |
| `/menu` | main menu |
| `/help` | help |
| `/id` | show the Chat ID |

The Telegram command menu lists the main ones: `/new /chats /models /menu /temp /image /video /stop /panels /web /settings /help`; the other commands still work. The menu is translated into Russian and English following the Telegram language.

## Settings

**Settings → Telegram** (some can also be changed in the bot: `/settings` — tap an item and pick a value). Changes made in the bot or the Mini App show up in an open MemuAI window right away — no restart needed.

| Setting | Meaning |
| --- | --- |
| How replies appear | **Live draft** (default) — text appears as it is generated; **Message edits** — for older clients; **Whole at the end** |
| Default model | the bot's model; the main MemuAI model by default |
| Bot language | Russian or English: menus, buttons and commands |
| Messages in AI context | 20, 40, 60 or 100 |
| Transcribe voice messages | uses the speech recognition from [Voice](doc:voice) settings |
| Reply with voice | no / to voice messages / always — read aloud with your MemuAI voice |
| Notifications | off / silent / with sound |
| Categories | answers in MemuAI, scheduled tasks, Work and MemuCode, modules |
| Quiet hours | notifications arrive silently during these hours |
| Additional users | Telegram user ids that may also use the bot in their own private chat |

## Notifications

When an answer finishes in MemuAI on the computer, the bot sends its beginning with **💬 Open chat** and **↩️ Reply** buttons. Results of [scheduled tasks](doc:schedules) arrive in full. Modules with their own Telegram switch (e.g. “New API models”) notify even when notifications are off — silently.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| “MemuAI Proxy is not running” | start MemuAI Proxy (button right in the panel) |
| “Reconnecting…” | no connection to Telegram (PC sleep, VPN, network) — the bot retries by itself; the reason and next attempt are shown |
| “The bot is not responding” | press **Restart bot** |
| “Token rejected” | copy the token from @BotFather again (or `/revoke` and use the new one) |
| “Bot is running elsewhere” | another program or computer uses this token; if the panel says “on this computer”, close the second MemuAI Proxy (the bot always runs once per computer) |
| The bot is silent in a chat | check the chat is paired; pair again if needed |
| A voice message is not transcribed | download a speech model in **Settings → Voice & Live** |

See also: [Notifications](doc:notifications), [Profiles](doc:profiles), [Agents](doc:agents).
