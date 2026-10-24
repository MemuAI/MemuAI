---
title: Accounts and providers
summary: Which services can be connected, what goes into each field and how to check a connection.
---

# Accounts and providers

MemuAI's models come from your accounts. Accounts are added in **MemuAI Proxy** — open its window from the tray or with the Proxy status button at the bottom of the MemuAI sidebar.

## Adding an account

1. Proxy → **Accounts** → **＋ Add account**.
2. Pick the provider.
3. Fill in the fields following the hints below them.
4. Press **Check**. A green "working" mark means all is well.

A few seconds later the new account's models appear in the MemuAI model list.

> [!IMPORTANT]
> Cookies and tokens are access to your account. MemuAI keeps them only on this computer, never shows them in the interface and never writes them to logs. Do not send them to anyone, including a model in a chat.

## Providers

| Provider | Model prefix | How it connects | What it gives |
| --- | --- | --- | --- |
| Genspark | `genspark-` | the `session_id` cookie | GPT, Claude, Gemini, DeepSeek, Kimi and more; web search; images |
| Kiro | `kiro-` | browser sign-in, Builder ID or a token file | Claude models |
| Antigravity | `antigravity-` | Google sign-in | Gemini, Claude, gpt-oss |
| Gemini Web | `geminiweb-` | two Google cookies or a sign-in window (guest — Flash only) | the account's picker models (3.8 Flash, 3.5 Flash-Lite, 3.1 Pro, 4 Argon) |
| Grok | `grok-` | the `sso` cookie | Grok models |
| OpenCode | `opencode-` | Free with a gateway install button, Zen/Go key, or your opencode2api | active models for the selected service; tools |
| Custom API | the connection name | Base URL and key | any service with an OpenAI-style API |
| Local models | `local-` | created automatically | GGUF models on your computer |

Genspark, Kiro, Antigravity, Gemini Web and Grok connect through the unofficial protocols of their web versions. They can stop working when the service changes something on its side; use your own accounts at your own risk.

## Getting a cookie

A cookie is the string a website uses to know you are signed in. The general way:

1. Open the service's website in a browser and sign in.
2. Press ++F12++ — the developer tools open.
3. The **Application** tab (**Storage** in Firefox) → **Cookies** → the site address.
4. Find the name you need and copy the whole value.

### Genspark

The `session_id` cookie on `genspark.ai`. You can paste just the value or the whole cookie line — the rest is dropped.

### Grok

The `sso` cookie on `grok.com`. The easiest way: ++F12++ → **Network** → reload the page → the first request → **Request Headers** → the `cookie:` line → copy the part from `sso=` to the first `;`.

### Gemini Web

Only two cookies from `gemini.google.com` are needed: `__Secure-1PSID` and `__Secure-1PSIDTS`. In desktop MemuAI the easiest way is **Sign in with Google in a window** — the cookies are filled in automatically. Without cookies guest mode works (Flash only), but Google often limits it with a captcha or error 1096. With several Google accounts, set the **account number** — the digit in `gemini.google.com/u/<N>/app`. The sign-in refreshes itself every ~10 minutes (the Keep the sign-in fresh setting).

**Models** are the ones in your account's Gemini app model picker (MemuAI reads them from gemini.google.com): currently `gemini-3.8-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-pro` and `gemini-4-argon` (Google AI Pro/Ultra only). Models the account lacks are hidden from the catalog. **Thinking level** (Low / Medium / High, like the app's Thinking level) — the chat level picker, `reasoning_effort` in the request or the `@effort=high` suffix; the default is in the provider settings. **Check connection** lists the account's models. Chats created by the Proxy are deleted from your Gemini history after the answer (the Delete proxy chats from history setting).

**Images, files and video as input.** Attach a picture, a PDF/document or a video to the message — the Proxy uploads it to Gemini the way the web client does. OpenAI `image_url` parts (data: or an http(s) link), `file`/`input_file`, `video_url` and Anthropic images (Claude Code) all work. Attachments of the last message count (up to 10 files, up to 30 MB by link, otherwise up to 100 MB).

**Drawing and video.** Ask in the chat «Draw …» — Gemini draws a picture (Imagen / «Nano Banana»), and on accounts with Veo — «Make a video …». Google's links to these files do not open without signing in, so the Proxy downloads them with the account cookies (pictures at full size) and serves them at its own `…/v1/geminiweb/media/…` address; the MemuAI chat shows the picture and keeps it in the profile files. A video takes up to a few minutes — the Proxy waits. For apps there is an OpenAI-compatible `POST /v1/images/generations` (a Gemini Web model, `prompt`, `n` up to 4, `response_format: b64_json`); when the account cannot draw, the answer is a 422 error with Gemini's text. When the client runs on another device, set **Proxy address for images and videos** in the provider settings.

**Limits.** The ✦ button on the account card and the Account limits step of Check connection show what Gemini's «Usage limits» window shows: the plan, the 5-hour and weekly windows, the Pro / Flash Thinking counters and extra features — with reset times. When Google does not return them, that is shown there too. When a limit is used up (error 1037 or a «you've reached your limit» reply) the Proxy returns a clear 429 with the reset time — composite models move on to the next candidate.

**Thinking level: what is known for sure.** High turns on «extended thinking» (as in the open Gemini-API client), Low uses the lighter depth. Open implementations have no separate Low/Medium/High field, so Medium and Low are the best approximation, not a copy of the app's switch.

## Browser sign-in

### Kiro

- **Sign in with the browser** opens the Kiro sign-in page. The return trip needs port 3128 free: close Kiro IDE if it is open.
- **Builder ID** signs in without a local port: confirm the code on the AWS site.
- Or paste the contents of `kiro-auth-token.json` from `%USERPROFILE%\.aws\sso\cache\` if Kiro IDE is already signed in.

### Antigravity

**Sign in with the browser** → choose a Google account. The Project ID is found automatically.

**Models and thinking levels.** Google encodes the thinking level in the model id, and does so inconsistently (Claude Opus has `-thinking` in its id, Sonnet does not, though both are "Thinking"). So the Proxy catalogue uses one naming scheme:

| Model | What it is |
| --- | --- |
| `antigravity-gemini-3.6-flash-high`, `-medium`, `-low` | Gemini 3.6 Flash, three thinking levels |
| `antigravity-gemini-3.1-pro-high`, `-low` | Gemini 3.1 Pro, two levels |
| `antigravity-gemini-3.8-flash-high`, `-medium`, `-low` (and the same three for `3.7`) | the newest Flash models at a chosen thinking level |
| `antigravity-gemini-3.7-flash`, `antigravity-gemini-3.8-flash` | the same models without a level in the name: Google or the client's `/effort` picks the depth |
| `antigravity-claude-opus-4-6-thinking`, `antigravity-claude-sonnet-4-6-thinking` | Claude with thinking |
| `antigravity-gpt-oss-120b-medium` | GPT-OSS 120B |

The old names (`claude-sonnet-4-6`, `gemini-pro-agent` and the other Google ids) keep working. On the first start of the new version the catalogue is moved to these names once: models you switched off stay off, stray duplicates are removed. Google has retired Gemini 3.5 Flash — the server still lists it and answers with a canned "no longer available" phrase; the Proxy shows that as an error instead of a model answer.

**Claude thinking** is switched on by a token budget: 8192 by default, changed by the provider setting "Claude thinking budget" (0 turns it off) and by the client's `/effort` level (low 2048, medium 8192, high 16384, max 32000). The train of thought arrives in a separate `reasoning_content` field. For Gemini the level comes from the model name (`-low`, `-medium`, `-high`). Gemini 3.7 and 3.8 Flash have one Google id for every level, so the Proxy sends the level as a separate request field (`thinkingLevel`): a name with a level fixes it, and for a name without one (`antigravity-gemini-3.8-flash`) `/effort` applies (minimal and low — Low, medium — Medium, high and max — High); without it Google picks the depth. The thought summary can be turned off with "Show Gemini thoughts".

**Limits.** They are counted separately for Gemini and for Claude + GPT and show on the account card with the reset time and the plan ("Google AI Pro"). Google reports one window per group — a rolling ~5 hours; **there is no weekly counter in the API for such accounts**, so the Proxy does not invent one. Instead:

- if Google itself reports a long window (it happens with free accounts), it is shown as a "week" row with its reset date;
- the "last 7 days (estimate)" row is the sum of the 5-hour windows spent, from samples (the limits page and requests to the model); it is a lower bound, not a percentage of a limit;
- if Google answered "quota exhausted" while the 5-hour counter is almost full, a note appears — most likely the plan's weekly limit was hit.

If Google asks to verify the account ("Verify your account"), the limits window shows a warning with a link; sign-in keeps working meanwhile.

**Instability.** The Antigravity service itself is sometimes unstable: it answers 429 "Resource has been exhausted", 503, or refuses by region. The Proxy tries both Google hosts, remembers which one answered, waits for the pause Google suggests (up to 8 seconds), refreshes the token once on a 401 and moves on to the next account; Google's real error text is visible in the log. The Proxy checks the client version against Antigravity's own auto-update address (it can be set by hand in the provider settings, which turns the check off).

## OpenCode and opencode2api

Proxy → **Accounts → Add account → OpenCode**. Choose a connection:

- **Free models · install gateway** is the simple option without a website or registration. Press **Install gateway**, wait for **Ready**, then **Add account** and **Check**. MemuAI downloads the separate opencode2api executable (~4 MB, GPL-3.0), verifies SHA-256, preserves its license and runs it on `127.0.0.1` in the background. No Node.js, Docker, API key or fixed port is needed. Proxy stops its own process on exit. Automatic setup supports Windows x64/ARM64.
- **Zen · your own API key** is paid API access. Use **Sign in to OpenCode** or [opencode.ai/auth](https://opencode.ai/auth). Sign in, select a workspace, create an API key and paste it. Paid models require a funded Zen balance. There is no need to find Console on the home page.
- **Go · subscription** uses the same login, with an active [Go / Go Plus](https://opencode.ai/go) subscription in that workspace. Paste its API key. The quota endpoint verifies entitlement and the separate Go catalogue supplies models.
- **My opencode2api gateway** is advanced, for an already running external server. For [jasonxu114514](https://github.com/jasonxu114514/opencode2api), the API is normally `http://127.0.0.1:8080/v1`; use a key from `server_keys` in its `config.json`. Port **8081 is the management UI**, not the API. For [TiaraBasori](https://github.com/TiaraBasori/OpenCode2API/blob/main/README.en.md), the API is normally `http://127.0.0.1:10000/v1`; use `API_KEY`. It requires a separate OpenCode runtime and Node.js or Docker. This is the **gateway's local key**, not a Zen/Go key. Select Chat Completions or Responses. MemuAI does not start your external server automatically.

Free access is not unlimited. **429 / Free usage limit reached** means public Free was refused; it does not prove personal quota exhaustion. Network or third-party client restrictions are possible, and the exact cause and reset time are unknown. Another Free entry or reinstall creates no separate quota. A successful catalog check verifies the model list, not generation.

Saving loads models automatically. **Check** and **Model catalogue → Refresh** reload them manually. With periodic catalogue updates enabled, OpenCode is refreshed at least every 15 minutes. Requests only use accounts that advertise the selected model. Network failures preserve the last valid catalogue; a successful empty list removes models that are no longer offered.

Native Zen/Go connections select Chat Completions, Responses, Anthropic Messages or Gemini for each model. Free uses the managed gateway. Streaming, images, tools, cancellation and reported token usage are supported where the model allows them. This connection does not add speech recognition or media generation.

The Go account's **Limits** action shows the current, weekly and monthly windows and reset times returned by the API. These windows are shared by a subscription; do not sum its keys. Unknown numeric limits for Free, Console/Zen and third-party gateways are left unknown. Model card prices are API reference rates, not the charge against included Go usage.

## Custom API

Works with OpenAI, OpenRouter, DeepSeek, Groq, Together, Mistral, local Ollama and LM Studio, and any other service with an OpenAI-style API.

| Field | What to enter |
| --- | --- |
| Name | becomes the model prefix: Latin letters, digits, hyphens. For example `openrouter` |
| Base URL | the API root, usually ending in `/v1`: `https://openrouter.ai/api/v1` |
| API key | the service key; for several keys use the "Key pool", one per line |
| Extra headers | JSON, if the service needs headers of its own |
| Models | comma-separated; empty — the list is requested from the service |

Presets for popular services are offered when adding.

## Music models

In **Add account → Music API**, choose ElevenLabs, MiniMax or a custom REST API schema. “Apply address and schema” fills the address, music IDs and request format; put the key in the usual account field. Choose a unique prefix and save. Models appear in **MemuAI Studio → Music** and `/v1/models` with the `music` capability; ordinary text chat does not offer them.

For a custom API, enter exact IDs under “Music generation models”, then configure the JSON template and response paths in the expandable section. Additional headers and the account's key pool are also used for music. Supported results are raw audio, JSON with URL/base64/hex, and asynchronous polling on the same API. MiniMax's preset requires music API access granted by the service; choosing a schema does not grant access.

If you have a legacy Studio connection, the account picker offers “Import saved Studio music connection”. The key transfers on the server without being returned to the page; repeating the import does not create a duplicate. The old connection remains for compatibility.

## After connecting

- **Card order is priority.** The top account is used first, the others when it fails. Drag the cards to reorder them.
- **Several accounts of one service** work together: when one runs out of quota, the request moves on to the next.
- **Notes and tags** help keep track: "work", "reserve", "until the 30th".
- **Checks** run from the button or automatically on the schedule set in the Proxy settings.

## When an account does not work

| Sign | Cause | What to do |
| --- | --- | --- |
| invalid, 401 | the cookie expired or you signed out in the browser | copy the cookie again |
| a 429 under "Last error" | the service quota is used up | wait or add a second account (the account keeps its place in the queue meanwhile) |
| 403 mentioning captcha | the service wants a browser check | open the site in a browser, pass the check, refresh the cookie |
| port 3128 is busy | Kiro sign-in | close Kiro IDE or use Builder ID |

The **Ask AI** button on the card opens the [AI companion](doc:proxy-companion) with the error attached.

See also: [MemuAI Proxy](doc:proxy), [Models](doc:models), [Local models](doc:local-models).

## Gemini Web cookies by hand

1. Open `gemini.google.com`, sign in and send any message.
2. F12 → **Application** (Chrome/Edge) or **Storage** (Firefox) → Cookies → `https://gemini.google.com`, filter by `1PSID`.
3. Copy the `__Secure-1PSID` and `__Secure-1PSIDTS` values (domain `.google.com`) and paste them into Google cookies.

The field accepts any format: the Cookie header from Network or “Copy as cURL”, Application table rows (tab-separated), Cookie-Editor/EditThisCookie JSON, `cookies.txt`, “name value” pairs, or just the two values. What was recognized and what is wrong shows right under the field: no `__Secure-1PSID`, a cookie from another domain (e.g. `accounts.google.com`), a value truncated with “…”, an expired cookie. **Check connection** walks the steps cookies → Gemini page → sign-in token → test request → models and tells how to fix each failed step. Do not sign out in the browser after copying — signing out invalidates the cookies.

Antigravity preserves an explicit High/Medium/Low depth. If the server rejects it, Proxy reports an error instead of silently reducing depth. Thought summary visibility and reasoning depth are separate settings. When Google returns `thoughtsTokenCount`, Proxy chat shows reasoning token usage separately.


### Free: catalog and generation are separate checks

Free is anonymous public access, not the account signed in on the website. A 429 does not prove personal quota exhaustion. Duplicating a Free entry creates no new quota. The service may require the official OpenCode client (403); the gateway can return a final 429 after another route was rejected.

Check reads the catalog without starting the gateway. For an explicit tiny test, open the saved connection settings → **Test Free generation**. Repeating within a minute returns the cached result; paid accounts are never used as a fallback.

Another actual account: **Add account → OpenCode → Zen/Go** → sign in as the intended user at opencode.ai/auth → paste that workspace API key. Keys from one workspace may share limits. Anonymous Free is unrelated to those website logins.
