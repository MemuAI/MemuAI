---
title: Discord and WhatsApp
summary: Your private MemuAI bot in Discord and WhatsApp — chats, models, settings and pairing with a code.
---

# Discord and WhatsApp

Besides Telegram, MemuAI answers in **Discord** and **WhatsApp**. All three messengers live on one page: **Settings → Messengers**, tabs **Telegram · Discord · WhatsApp**. The dot on a tab shows the state: green — connected, yellow — waiting to pair or starting, red — an error.

The bots run inside MemuAI Proxy on your computer. Answers are produced by the same server-side jobs as in the Telegram Mini App and are saved into your ordinary MemuAI chats — you see them in the desktop window.

> [!IMPORTANT]
> Until the bot is paired it answers nobody. After pairing it answers only you (and the people in “Additional users”). Tokens stay on the computer, never reach the logs and are not copied by sync.

## What the bot can do

| Command | What it does |
|---|---|
| `/menu` | The main menu with buttons |
| `/new` | A new chat |
| `/chats` | Your MemuAI chats (with search: `/chats report`) |
| `/model` | Pick a model (with search: `/model gpt`) |
| `/settings` | Web search, reasoning, personality, bot language |
| `/temp` | A temporary chat: kept out of your MemuAI chats |
| `/regen` | Answer again |
| `/stop` | Stop the answer |
| `/status` | What is selected now: chat, model, toggles |
| `/help` | A hint |

Under the answers there are **Regenerate**, **New chat** and **Menu** buttons. Lists of chats, models and personalities are paged with buttons. The commands also have Russian names: `/меню`, `/новый`, `/чаты`, `/модель`, `/настройки`, `/стоп`, `/статус`.

## Discord

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application** → the **Bot** section → **Reset Token** and copy the token.
2. In MemuAI: **Settings → Messengers → Discord**, paste the token and press **Test connection**. The **Add to my account** and **Add to a server** links appear.
3. Add the bot to yourself. For “Add to my account” turn on **Installation → User Install** in the portal. You can also simply add the bot to a server of yours — then you can message it directly.
4. Turn on **Discord bot**, press **Get a pairing code** and send `/pair CODE` to the bot in a direct message.

The bot works in direct messages only; on servers it politely refuses. No special permissions or Message Content Intent are needed. The answer is typed as it is generated — the message updates every 1–2 seconds.

## WhatsApp

There are two ways to connect — pick one with the switch at the top of the tab.

### WAHA on this computer

[WAHA](https://waha.devlike.pro/) is a WhatsApp HTTP API that runs locally. Nothing is exposed to the internet.

1. Start WAHA: `docker run -it -p 3000:3000 devlikeapro/waha`.
2. Open `http://localhost:3000/dashboard`, start a session and scan the QR in WhatsApp → **Linked devices**.
3. In MemuAI choose **WAHA**, check the address, the API key (if set) and the session name.
4. Enter **the chat for MemuAI**: your own number (the chat with yourself) or your personal number when WAHA runs on a separate bot number.
5. Turn on **WhatsApp bot** and press **Test connection**.

MemuAI reads only that chat, no pairing code is needed. WhatsApp has no buttons for ordinary accounts, so choices arrive as a numbered list — answer with a number.

### WhatsApp Cloud API

Meta's official way for business numbers. Messages arrive at the MemuAI Proxy webhook, so it must be reachable over HTTPS.

1. In [Meta for Developers](https://developers.facebook.com/apps) create a **Business** app → **WhatsApp** → **API Setup**.
2. Copy a permanent token (System User), the **Phone number ID** and the **App secret** (App settings → Basic) into MemuAI.
3. Expose MemuAI Proxy over HTTPS, for example `cloudflared tunnel --url http://127.0.0.1:PORT`.
4. In WhatsApp → **Configuration** set the Callback URL `https://your-address/messengers/whatsapp/webhook` and the **Verify token** — the **Create** button in MemuAI makes one. Subscribe to the `messages` field.
5. Turn on **WhatsApp bot**, press **Get a pairing code** and send the code to the bot's number from your WhatsApp.

Every webhook request is checked with the App secret signature (`X-Hub-Signature-256`): requests without a valid signature are refused. Lists of up to 10 items arrive as an interactive menu, actions as buttons.

## If something does not work

- **MemuAI Proxy is not running** — the bots run inside the Proxy; start it and the bot connects by itself.
- **Token rejected** — reset the token in the Developer Portal and paste the new one; no restart is needed.
- **Waiting for Meta** — check the HTTPS tunnel and the webhook subscription to `messages`.
- **WAHA is not answering** — check that the container runs and the session is `WORKING`.
- Unpair the bot with **Unpair** on the owner card; after that a new code is needed.
