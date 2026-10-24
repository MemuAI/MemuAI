---
title: Telegram Mini App
summary: The full MemuAI on your phone right inside Telegram — chats, agents, MemuCode, modules and settings.
---

# Telegram Mini App

The Mini App is MemuAI as an app inside Telegram: the chat list with folders and search, live answers with Markdown and code highlighting, agents (memes), modules, settings and MemuCode CLI sessions. Everything stays on your computer; the phone only shows and controls it.

> [!NOTE]
> The Mini App runs inside MemuAI Proxy on the computer. While Proxy is off or the computer sleeps, the app does not open — just like the [Telegram bot](doc:telegram).

## What you need

- A connected [Telegram bot](doc:telegram): the bot token and a paired account.
- MemuAI Proxy running.
- For the quick tunnel — access to GitHub once (`cloudflared` is downloaded, about 55 MB) and to Cloudflare.

## Turning it on

1. **Settings → Telegram**, below the bot panel — the **Mini App — MemuAI in Telegram** card.
2. Switch on **Enable the Mini App**.
3. Keep the **Cloudflare** address (default) or choose **Own URL**.
4. Wait until the status reads **Online**: a public address appears and the bot updates its menu button itself.

The status in the card shows what is happening: “Downloading cloudflared…”, “Starting the tunnel…”, “Online”, “MemuAI Proxy is not running”, “Bot not paired”.

## How to open it

- The **menu button** left of the input field in the chat with the bot — the main way.
- The **Open MemuAI** button in a bot message (an inline button).

- In a **regular browser** (another PC, a tablet without Telegram) — with a confirmation from Telegram, see [In a regular browser](#in-a-regular-browser).

> [!WARNING]
> Telegram opens the bot's regular keyboard buttons (at the bottom of the screen) without signed launch data — the app then asks you to use the menu button.

## What it does

### Chats

The same chats as on the computer: pinned, folders, search, rename, delete. The answer is generated on the computer as a background job — you can close the Mini App or lose the connection and the answer still finishes and is saved. There is a model picker, web search, thinking, attachments (images, PDF, DOCX, text files) and a temporary chat that is not saved and uses no memory. Chat titles and memory facts work as in MemuAI.

The model list works as in MemuAI: a readable name, badges (vision, "vision?" — unknown, reasoning, free) and the full id in small type. The **Thinking** chip opens the levels of the current model only — "Auto" and whatever it accepts (a model without reasoning has no chip); the chosen level shows on the chip itself and is sent with the message. Switching the model picks the level saved for it in MemuAI ("Reasoning by model"), otherwise "Auto". Details: [Models → Thinking](doc:models).

**Search** in the chat list also finds text inside messages — word forms, typos and the wrong keyboard layout included, like [search across MemuAI](doc:search) on the computer. Below the chats it shows matches in memory, prompts, scheduled tasks, characters, panels, MemuCode and the documentation; a tap opens the passage with the words highlighted. When nothing is found, the Mini App suggests a corrected query. You can **edit** your own question — the answer comes again.

Features that need the computer (Work, tools, skills, Browser, Computer Use, image generation, Live voice) are not offered in the Mini App.

### Voice input

The microphone button in the chat and MemuCode input: tap to record, tap again to stop; long-press for **Upload an audio file**. Audio files attached with the paperclip are transcribed too. Speech is recognised by the local Whisper model on the computer (or by the speech service, if one is chosen in the voice settings). Without a downloaded model the button is dimmed and explains how to install one (**MemuAI → Settings → Voice & Live**). With **Show the transcript before sending** on, the text lands in the input first.

### Agents

The list of agents (memes), creating and editing them by hand or from a description with AI, the avatar, model, default search and thinking, allowed capabilities. A chat with an agent starts from its card.

### MemuCode

Full MemuCode from the phone:

- **Open in a terminal** — MemuCode CLI sessions running right now: the answer streams as it is written, running tools and the todo list are visible. You can send a task or a `/command` (with auto-complete), press **Esc** and answer a permission question — with the exact tool call. Dangerous actions and “don't ask again” need a separate confirmation.
- **Recent sessions** open on the phone just like in a terminal: when a session is not running anywhere, MemuAI starts a hidden MemuCode on the computer (`memucode remote-host`), so `/commands`, skills, `/model`, permission questions and the queue all work. The session ends with **Close** or by itself after a while idle.
- **New session** — pick a folder (recent projects, browse, new folder), a model, the permission mode (**Safe**, **Edits**, **Plan**, **Bypass**) and the first task.
- **Files** — a read-only view of the project folder with syntax highlighting, images and `git status` changes. Sessions can be renamed and deleted.

Control is switched off with **MemuCode control** in the settings card, or with `MEMUCODE_NO_REMOTE=1` for a single terminal.

### Modules

Modules open right inside Telegram and are switched on or off with a toggle. Actions that need the computer (files, media, downloads, external links, background, web) work only in MemuAI on the computer.

### Settings

Profile (switching to a protected profile needs its PIN), theme and colours, language, models and answers, personality and instructions, memory, search, context, Telegram and computer notifications. **Theme and colours** offers the MemuAI palette or **Telegram colours**, and light/dark “like Telegram” or “like MemuAI”. Settings are stored in MemuAI on the computer.

## In a regular browser

The Mini App can be opened at its address in any browser — on another PC or a tablet, for example. Signing in always needs a confirmation from your Telegram:

1. **QR code.** The browser shows a QR code and a short code. In the Mini App on your phone open **Sign in on another device** in its settings, scan the QR code or type the code, check which browser asks (address, browser, time) and confirm.
2. **Link from the bot.** Send `/web` to the bot — you get a one-time link valid for 5 minutes.

Codes are valid for 5 minutes and work once. The session lives in this tab only unless **Keep me signed in on this device (12 h)** is ticked. **Reset sessions** on the computer signs every browser out at once. Browser sign-in is switched off with **Browser sign-in** in the Mini App card.

On tablets and computers the Mini App shows the list and the chat side by side, understands keyboard shortcuts and lets you resize the list column.

## Address: quick tunnel, own tunnel or own URL

| Mode | How it works |
| --- | --- |
| Quick (Cloudflare) | a `trycloudflare.com` tunnel without sign-up; the address changes on every start — the bot updates the menu button at once (and checks it took) and the menu card |
| Own tunnel | a named Cloudflare Tunnel on your domain — a permanent address. Cloudflare dashboard → **Networking → Tunnels** → **Create a tunnel** → name → **Create Tunnel** → copy the install command (it contains the token); then the **Routes** tab → **Add route** → **Published application**: a subdomain (e.g. `memu.example.com`) and the Service URL `http://127.0.0.1:8641`. Paste the address and the token into MemuAI (the whole `cloudflared service install …` line works too) |
| Own URL | ngrok, your own reverse proxy or a VPS — any HTTPS address with a valid certificate that forwards to `http://127.0.0.1:8641` (the Mini App port) |

**The address goes to Telegram only after a check.** When the tunnel is connected, MemuAI checks that the new name already exists in public DNS (via 1.1.1.1 and 8.8.8.8) and that MemuAI itself answers there. Until then the status says “Checking the address…” or “Waiting for DNS…”. If the address never shows up in DNS, the tunnel restarts for a new one.

A quick tunnel cannot “start forever”: no address in 45 s or no connection in 75 s gives a clear reason in the status and a new attempt (2 → 4 → … → 120 s); after 6 failures in a row the status is “Cannot start” with a **Retry now** button. The “Auto” protocol switches from QUIC (UDP is often blocked) to HTTP/2 after the first failure; protocol and IP version can be chosen in “If it does not open (VPN, blocking)”.

A Cloudflare quick tunnel has no availability guarantee and may buffer long streams, so answers are read in short segments with polling as the fallback.

## Security

- Sign-in only with signed Telegram launch data (`initData`, HMAC-checked with the bot token) and only for the paired account or users on the bot's access list.
- **Sign-in lifetime** limits the age of the launch data (1 hour to 7 days); a session in the app lasts 12 hours.
- **Reset sessions** issues a new secret — every window open on a phone has to sign in again. Unpairing the account in the bot also closes access.
- Only the Mini App with a whitelist of operations is exposed; the Proxy API and the computer's secrets (tokens, keys) are not reachable through it. Sign-in attempts and requests are rate-limited.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| “Open MemuAI from the button in the Telegram bot” | open it with the menu button or an inline button; in a regular browser sign in with the QR code or the `/web` link |
| “Telegram sent no sign-in signature” | you used a regular keyboard button — use the menu button |
| “This Telegram account is not paired” | pair the account in the Telegram panel or add it to the access list |
| Status “MemuAI Proxy is not running” | start MemuAI Proxy |
| “Server IP address could not be found” in Telegram | the button pointed at an old address or one not yet in DNS. MemuAI now publishes the address only after a DNS check and updates the menu button; close and reopen the Mini App. If your ISP blocks `trycloudflare.com`, use “Own tunnel” or “Own URL” |
| Endless loading with a VPN | the VPN interferes with the tunnel: choose HTTP/2 or exclude Telegram from the VPN; “Own tunnel” is more reliable |
| Tunnel “Cannot start” / “Reconnecting…” | the reason is shown under the status; check access to GitHub and Cloudflare, try HTTP/2, press **Retry now** or use your own URL |
| “Session expired” | close the Mini App and open it again |
