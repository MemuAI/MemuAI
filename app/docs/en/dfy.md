---
title: Do For You — the AI does it for you
summary: An agent for bookings, orders, forms and servers — an isolated browser or connected Chrome, approvals, schedules, taking control, proxies and protected data.
---

# Do For You

**Do For You (DFY)** is an agent that does not just advise but acts: books a table, orders an item, signs up on a site, fills in a form, cancels a subscription. By default it works in MemuAI's own browser, looks at the page (a screenshot plus a list of buttons and fields), takes one action at a time and shows every step. You can separately opt in to using your Chrome through the extension.

In ordinary interactive tasks, DFY **stops and asks you** before paying, booking, sending or signing up. Chrome schedules without dialogs require a separate advance permission; see "DFY in your Chrome" below. It never sees passwords or cards: the vault holds encrypted values, and the model only knows a mask such as "Visa •••• 4242".

## How to start it

- **From a chat.** Just ask: "book a table at …", "order …". The chat model decides an action is needed and hands the task to DFY together with the conversation context. A task card appears in the message.
- **With the command** `/dfy <task>` in the input — straight away, without the chat model. See [Commands](doc:commands).
- **From Quick Ask** (++Alt+Space++): ++Ctrl+Enter++, the **Do it for me** item in **Actions** (++Ctrl+K++) or the offer that appears under the input for requests like "book…", "order…". The task keeps running when the window is hidden, and a notification arrives when it finishes. See [Quick Ask](doc:quick-ask).

## What you see while it works

The task card shows the status (**Running**, **Needs you**, **Done**, **Failed**, **Cancelled**), the latest steps, a fresh screenshot of the page and a **Stop** button. **All steps** expands the full list.

The screenshot in the card is the page **after** the latest action: every step shows what the browser looked like once the agent had clicked, typed or opened the page (not what it was looking at before). The picture updates in place, without flicker.

When the agent needs something, the status becomes **Needs you** and the card shows a question:

| What it asks | How to answer |
| --- | --- |
| a clarification ("what time?", "which of the three?") | type an answer or pick an option |
| approval of an irreversible step ("Pay 24.00 €?") | **Approve** or **Decline** |
| a login, a card, a phone, an email, an address, a passport, company details | fill in the fields right in the card or pick a saved value ("Use …"); **Remember for future tasks** keeps them in the vault; you can also refuse (**Don't provide**) |
| a code from a text message or email | the form shows where the code was sent (say, "SMS → +7 •••• ••67"). Type or paste the digits — a full code is sent by itself; a timer shows how much longer the agent waits (10 minutes). **No code — send again** asks the site for a new one, **Cancel** refuses |

The form shows every field the site asks for (five for a card, up to ten for personal data), and the first field gets the focus. Fields are checked before sending: card number, CVC, tax ID, email; a phone needs its country code (+1…, +44…, +49…) and is formatted as you type. A Russian number written as `8 916…` is read as `+7 916…`. If a field is wrong, the form stays open and highlights the mistake. A payment approval shows the total when the site displays it.

The [Quick Ask](doc:quick-ask) window has the same forms: **Skip** refuses, **Remember in the encrypted vault** saves the data, and ++Enter++ sends the answer. Passwords and card and document numbers are hidden; their values never reach the chat, the history or the log — they go straight to the vault. A one-time code is never written to disk at all.

## Settings

**Settings → AI → Do For You**:

| Setting | What it does |
| --- | --- |
| **Enable DFY** | off — tasks do not start from a chat, with `/dfy` or from Quick Ask |
| **DFY model** | the model that drives the browser. Only **vision** models are listed: the agent looks at page screenshots. "Default Vision model" is the one chosen for images in the settings, or the first vision model if that one cannot see images |
| **Step limit** | how many browser actions one task may take (5–80, 30 by default). Questions to you do not count |
| **Confirmations** | "Before irreversible steps" (default) or "Every action" — ask before every click and input |
| **Show the browser window** | off — the browser runs in the background and the steps are in the card; on — you see the real window |

If there are no vision models at all, add one in [MemuAI Proxy](doc:proxy). If the Proxy is wrong and a model does see images, turn on “Vision” by hand: **MemuAI Proxy → Models → “Everything about this model” → “Set manually”**, then pick the model in **Settings → Do For You**.

If the saved model stops seeing images or disappears from the Proxy, it stays in the list as one “— no vision” line with a warning and a **Default** button — so you can see what is saved now. Changing the model applies at once; the settings page is not redrawn.

## The DFY browser

DFY has its own Microsoft Edge profile, separate from your everyday browser. Cookies and site logins in it **persist** between tasks: once signed in, the agent is still signed in next time. **Clear sessions** in the "DFY browser" section signs out of every site and deletes the profile's cookies, cache and storage.

The agent does not open addresses of the computer itself (`localhost`, local files, internal browser pages and private networks) and goes back if a site tries to lead it there. A site's pop-ups (alert, confirm, "leave this page?") no longer freeze the browser: the agent answers them itself — messages and "leave this page?" are accepted, yes/no questions and input prompts are declined, so it never silently agrees to anything irreversible. The agent sees the pop-up's text and can ask you if declining blocks the task.

Links and forms a site opens in a new tab are opened in the same tab, and extra tabs are closed. The agent can also fill fields inside embedded frames (such as a bank's payment form). If the model or the Proxy fails briefly, the step is retried once.

## DFY in your Chrome

Connect [Chrome · MemuAI](doc:integrations), then enable **Settings → Integrations → Chrome · MemuAI → DFY in my Chrome**. Before each new task, choose **Separate DFY browser** or **My Chrome**; the choice applies to that run only. Cancelling creates no task. Chrome runs use separate background tabs with existing site sign-ins. Your current tab is not switched; Chrome displays its standard debugger notice. Keep MemuAI and the connected Chrome open. A lost connection stops the task and closes its tabs; other tabs are kept.

With **DFY in my Chrome** off, the separate DFY browser is used by default without a question. Clearing the separate DFY profile does not erase Chrome's cookies or sign-ins. The DFY proxy setting below applies to MemuAI's separate browser; the extension uses Chrome's browser/system network settings.

For [scheduled tasks](doc:schedules), also enable **Scheduled actions without prompts**. This preauthorizes saved tasks to run without approval dialogs. DFY has its own opt-in; the tab control setting governs ordinary `chrome_*` tools. Ordinary interactive tasks keep their confirmation policy.

**Settings → Do For You → Browser for scheduled tasks** sets the schedule default. Each task and DFY flow step can override it. Scheduled runs never ask where to execute; missing permissions or an unavailable chosen browser produce an error without switching to another browser.

## Take control

Sometimes a step is better done by you: an “I'm not a robot” check, signing in with a QR code or an app, a choice on a tricky page. Press **Take control** on the task card (in [Quick Ask](doc:quick-ask) — **Take control** on the card):

- The AI pauses at once (if it is in the middle of a click, it finishes that one step) and does nothing while you are in control. The task status is **You're in control**.
- The DFY browser opens in a big window over MemuAI (Quick Ask grows to its maximum size). Click, scroll and type — any keyboard layout works; ++Ctrl+V++ pastes your clipboard, ++Ctrl+C++ copies the page selection. The bar has **Back**, **Forward**, **Reload** and the address field (++Ctrl+L++).
- ++Esc++ goes to the page (closes the site's dialogs) and does **not** close the takeover window, so control never goes back by accident. **Minimize** hides the window but you keep control: the card and a pill at the bottom of the screen show **You're in control** with **Open window** and **Return control to AI**.
- **Log** shows what the AI will get: “Clicked button «Sign in»”, “Typed into field «Email»: «…»”, “Opened …”, “Scrolled down ~1200 px”.
- **Return control to AI** — the AI gets this log and a few key screenshots (start, new pages, end) and continues from the current page without redoing your steps. If it was waiting for an answer to a question (other than a payment confirmation), the question counts as done — you did it yourself.

The AI can also ask you to take over — for a CAPTCHA or a sign-in a person has to do: the card shows a question with **Take control** and **Skip**.

> [!NOTE]
> What you type into password, code and card fields never reaches the log — it says “••• (hidden)”, and such fields are covered with dots in the screenshots. If a site then shows the password in the page address, MemuAI removes it from everything the AI sees.

After 10 minutes without any action a warning with **I'm still here** appears a minute before the end, then control goes back to the AI by itself. You can take control as many times as you like.

The **You're in control** pill at the bottom of the screen belongs to its chat: it is hidden in other chats and comes back when you return. If the task is stopped (**Stop** in the card, Quick Ask or Telegram), finishes, or control goes back to the AI on the timer, the pill and the takeover window close by themselves.

## Proxy

**Settings → Do For You → Proxy**: the DFY browser can reach the internet only through your proxy — HTTP, HTTPS or SOCKS5, with or without a login. Turn on **Use a proxy**, set the type, address and port (or paste it whole: `socks5://user:pass@host:1080`), the username and password, and press **Save**. **Check** shows the external IP that sites see and the response time.

- **All** DFY browser traffic goes through the proxy: pages, images, downloads, WebSocket. If the proxy is down, sites do not open — DFY never falls back to a direct connection.
- Site names are resolved by the proxy (like `socks5h`), so your DNS server never learns which sites DFY opens. WebRTC does not reveal your real address.
- The proxy username and password are encrypted like other secrets and never shown to the AI. The password field is empty after saving: leaving it empty keeps the saved password, **Forget login** deletes it.
- A new proxy takes effect from DFY's next step (the browser restarts by itself; site logins are kept). Remote machines have their own proxy setting each — see [Proxy and a browser on the machine](#proxy-and-a-browser-on-the-machine).

## On a server or another computer

DFY works not only in a browser but in a whole machine: a Linux server, another PC or this computer. It runs commands, reads and edits files, downloads and deletes, installs and removes packages, lists and stops processes, sets up services — from an ordinary chat request such as "install nginx on prod-web and check that it answers".

### Connect a machine

**Settings → Do For You → Remote machines → Add a machine**:

- **Agent (recommended).** Copy the single line from the form and paste it into the server's terminal (Linux, macOS; only `python3` is needed, the server needs no internet). The line saves the agent to `~/memuai_remote_agent.py` and starts it; the agent prints a **pairing code** `MEMU1-…` — paste it into the form. To keep the agent running after a reboot, use the **systemd** option from the same form: it installs the `memuai-agent` service and prints the code. You can also **download** the agent as a file and copy it with `scp`.
- **Over SSH, no install.** Enter the address, user and port; sign in with a key, ssh-agent or the SSH password (stored encrypted like the other secrets). Nothing is installed on the server: for every step MemuAI starts the agent over SSH and closes it right away.
- **This computer** — a switch in the same section. It is off by default: DFY does not touch your PC until you allow it explicitly.

**Check** shows whether the machine is online, its OS and the agent version. The agent listens on port `8765`; if the server is behind NAT or you do not want to open a port, run the agent on `127.0.0.1` and connect through an SSH tunnel (`ssh -L 8765:127.0.0.1:8765 user@server`).

### Give a task to a machine

- Under the chat input and in [Quick Ask](doc:quick-ask) there is a **DFY:** picker — "This browser", "This computer", machines by name and **"<machine> — browser"**. The choice is remembered.
- You can name the machine in the text: `/dfy @prod-web clean up old logs`, or just ask in the chat — the model hands the task to the right machine.
- The task card shows the machine chip, every step and the live command output with **Copy output** and the exit code.

### Protection

- The agent **never** touches itself, its folder, the MemuAI folder or its data: it will not delete, overwrite or move them or stop its own process — even when the agent runs on the same machine as MemuAI. The real path is checked (links are resolved), and deleting a parent folder is refused too.
- Dangerous steps — recursive deletes, overwriting a file, removing a package, killing a process, reboot, disk operations — are shown to you with the exact command and run only after **Allow**. Catastrophic commands (`rm -rf /`, formatting the system disk, a fork bomb) and system folders (`/`, `/etc`, `C:\Windows`) are refused outright.
- An allow-list of folders: start the agent with `--root /srv` (repeat it for more) — DFY's file work stays inside those folders; the machine list shows them as "directories: …".
- Requests to the agent are signed with the key from the pairing code (HMAC with a timestamp — an intercepted request cannot be replayed); the key itself never goes over the network. To encrypt the traffic, start the agent with `--tls-cert`/`--tls-key` or work through an SSH tunnel.
- DFY never writes passwords and tokens into commands in plain text: it takes them from the secret vault, and they do not show up in the output.

### Proxy and a browser on the machine

- **The machine's proxy** (the **Proxy** button of a machine) is set separately from this PC's proxy. DFY's commands, package installs and downloads on the machine go through it (`HTTP_PROXY`/`ALL_PROXY`; with SOCKS5 the proxy resolves names); other programs on the machine are not affected. **Check from the machine** shows the external IP the machine itself goes out with.
- **A browser on the machine** (the "<machine> — browser" target): the agent starts Chromium, Chrome or Edge on the machine itself and DFY drives it like its own browser — sites see the machine's IP and network. **Take control** works, and the machine's proxy applies to this browser too. It needs a machine paired with the agent code (not SSH) with a browser installed (for example `sudo apt install chromium`); the proxy for this browser must be one without a login (allow the machine's IP on the proxy instead).

## The secret vault

The **Personal data and secrets** section on the same page keeps what DFY may type in for you, in two groups:

- **Personal data** — phone, email, last, first and middle name and birth date, shipping and billing addresses (recipient, country, region, city, street, house, flat, postcode; there can be several), a passport or other ID, company details (name and Russian tax IDs: INN, KPP, OGRN);
- **Logins and payments** — site logins, payment cards and other secrets.

A phone is kept in international format, and the agent can type it the way a site asks: with or without the country code, digits only. The same goes for the rest: the full name in one line or in parts, the birth date whole or as separate day, month and year, a card's expiry as MM/YY. Dates are accepted as DD.MM.YYYY. The list shows only a mask such as "+7 •••• ••67". One-time codes never appear in the list.

- Values are encrypted for your Windows account and never leave the computer.
- The model sees only a mask; MemuAI puts the real value straight into the field on the page and covers the field before a screenshot.
- A login is typed only on the site it was saved for.
- A card number the model tries to write as plain text is refused.

You can fill the vault in advance (**Add**) or wait for the agent's question — it offers to save the data. **Edit** changes an entry (an empty field keeps the saved value), **Delete** removes it.

## Safety

> [!IMPORTANT]
> DFY acts on your behalf on real websites. Read approval questions carefully: they say exactly what will be clicked, and the total when it is known.

- Buttons such as "Pay", "Book", "Send", "Sign up" or "Delete" are held by MemuAI's code itself, even if the model forgot to ask. This covers submitting a form with ++Enter++ and clicking a point on the screen too. If a button has no readable text (an icon, or a button inside a payment frame) and the action looks like a payment or a booking, DFY asks as well.
- Page text is untrusted data for the agent: it does not follow instructions from a site that conflict with your task.
- You can stop a task at any moment with **Stop**.

## If it does not work

| What you see | What to do |
| --- | --- |
| "The saved model no longer supports images" or "No vision models" | pick a vision model under **DFY model** or press **Default** |
| the agent hit the step limit or got stuck on one page | raise the **Step limit** or split the task. If the agent repeats the same action and the page does not change, from the third time it is told to try another way, and on the sixth the task ends with an error instead of burning the whole limit |
| the verification code did not arrive or time ran out | press **No code — send again**; if no code is entered within 10 minutes, the agent asks the site for a new one itself |
| a site asks to sign in again | the login may have expired; answer the agent's question or check the login in the vault |
| "The task was interrupted: MemuAI restarted" | start the task again — it does not continue after a restart |
| "The DFY browser did not start" | DFY uses Microsoft Edge (Chrome if Edge is missing). Close any Edge window that has the DFY profile open and try again |
| Sites do not open through the proxy, or a proxy error in Settings | press **Check**: “rejected the login or password” — fix them; “unreachable” — the address, the port or the proxy itself; turn **Use a proxy** off to go direct |
| The takeover window closed by itself | 10 minutes without any action — control went back to the AI; press **Take control** again |
| The "You're in control" pill disappeared | the task was stopped or finished, or you are in another chat — the pill shows only in its task's chat |
| A machine is "not responding" or "No connection to the machine" | check that the agent is running (`systemctl status memuai-agent` or its terminal window) and port `8765` is open or the SSH tunnel is up; if the agent was reinstalled, add the machine again with the new code |
| "Bad signature" | the clocks of the machine and this PC differ by more than 5 minutes — turn on time sync (`timedatectl set-ntp true`) |
| "No Chromium/Chrome/Edge on the machine" | install a browser on the machine (`sudo apt install chromium`) or pick the "This browser" target |
| A step on the machine is "refused" | the agent protects itself, MemuAI and system folders — rewording the step will not help; do it yourself if you are sure |

Other ways to give a model a browser and a computer are on the [Browser and Computer Use](doc:browser-computer) page.
