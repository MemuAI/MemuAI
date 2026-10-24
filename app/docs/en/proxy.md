---
title: MemuAI Proxy
summary: What the Proxy does, how its panel is organised and which settings affect reliability.
---

# MemuAI Proxy

**MemuAI Proxy** is the second program of the bundle. It stores your provider accounts, builds a shared model catalogue from them and serves it through an OpenAI-compatible API. The MemuAI chat also works through the Proxy: every message goes to it, and it picks an account and forwards the request to the provider.

Usually the Proxy is set up once — you add accounts — and after that you only use MemuAI.

## Where it runs

The Proxy lives in the system tray next to the clock. MemuAI starts it when it is not running yet. Double-click the tray icon to open the window; closing the window hides it back to the tray and the work continues.

To stop the Proxy completely: tray → **Shut down**, or **Settings → System → Shut down**. Without the Proxy the MemuAI chat cannot answer and scheduled tasks do not run.

Only one instance runs at a time. Starting it again just shows the open window.

## Tabs

| Tab | What is there |
| --- | --- |
| Home | status, the API address, request statistics and "Restart services" |
| Accounts | provider connections: adding, checking, order, notes, tags |
| Model catalog | every model of every account: enabling and disabling, capability badges, refreshing the lists |
| Local models | GGUF models on your computer — see [Local models](doc:local-models) |
| Model detector | which model really answers, and the visual test — see [Model detector](doc:model-detector) |
| Public access | a separate address for other people and devices, with its own keys and limits — see [Public access](doc:proxy-public) |
| Chat | a plain conversation with any Proxy model without system prompts, memory or tools — to check an account or model quickly |
| Settings | sections on the left: general, routing, providers, web search, access & security, network, integrations, reliability, logs & statistics, system; search across every setting |
| Logs | the request log and the server log |

At the bottom of the sidebar is the **AI companion**, which explains errors. Details: [Proxy AI companion](doc:proxy-companion).

## Statistics and cost

The **Statistics** card on Home shows requests, success rate, response time and tokens for the chosen period (today, 24 hours, 7 and 30 days, all time — 35 days of history are kept) and the **cost at API prices** — in total and for every model and provider.

- Prices come from the model catalogue (OpenRouter / Models.dev, refreshed daily), otherwise from a built-in table of typical family prices; your own price goes into `pricing-overrides.json` in the data folder (`memuai`). The tooltip of each amount shows the price per 1M tokens and where it came from. Cached input tokens are priced at the cache rate.
- For **subscription** providers (Genspark, Kiro, Antigravity, Gemini Web, Grok) this is the **API-equivalent value** (tag “API equiv.”): nothing is charged per token; the amount shows what the same work would cost through the official API. For your own API keys it estimates the bill; when the provider reports the cost of a request itself (OpenRouter), that amount is shown (tag “billed”).
- When a model has no price anywhere, it reads **“price unknown”** instead of a misleading “$0”, and the total is marked “≥”.
- Statistics recorded before Round 20 kept tokens per whole hour only. For those hours the tokens are shared between models by request count, and such sums are marked “≈”.
- The **Cost** chart switch shows spending per hour or day.

## API balances and custom tariffs

Open **Quota planner → API balance → Add account**. Choose an existing Proxy account, enter its current USD balance and press **Save**. You do not need to enter the key again. **Plan balance until** is optional; it sets a date for calculating the daily spending allowance.

**Configure** shows the account's models and recognised names — for example, `api-claude-opus-5.5` is Claude Opus 5.5. Base prices come from the OpenRouter catalogue and the built-in vendor tariff table. **Refresh prices** downloads the catalogue again while preserving your settings.

Expand a model and enable **Use custom tariff**. Prices are **USD per 1M tokens**, with separate input, output, cache-read and cache-write rates. Search locates a model; the percentage discount applies only to filtered models with known base prices. Turning off **Use separate cache rates** charges cached tokens at the normal input rate; it does not disable the external service's automatic caching. An empty custom cache rate also means the normal input rate.

An account tariff takes priority over the shared `pricing-overrides.json` and public prices. Proxy uses the tariff of **the account that actually answered**, including fallback to a reserve key. The same cost reaches MemuAI, MemuCode and statistics. A direct CLI connection `@api:model` also uses the tariff of the pool named `api` when every account in that pool has an unambiguous rate. One key's price is never substituted for another key with a different tariff. The shared `pricing-overrides.json` remains available for models without account tariffs.

**Remaining balance is an estimate, not the provider's live balance.** The planner subtracts successful requests through this Proxy after you enter the balance. Direct CLI connections and other apps that bypass Proxy do not reduce it. Fees, separate image/audio/video generation and interrupted answers may also be missing. Requests without a known price are listed separately: they are not treated as free, and the displayed balance may be overstated. A pace estimate appears after three requests and one hour of observation; reconcile it with the provider dashboard.

Editing tariffs keeps already observed spending. Entering a new current balance starts a new tracking period and preserves tariffs; leave the field empty when changing only prices. **Remove from planner** removes balance tracking and tariffs while keeping the Proxy account itself.

## Accounts

An account is one connection to a provider: a cookie, a browser sign-in or an API key. The providers and what goes into each field are listed in [Accounts](doc:accounts).

**Card order is priority.** The top card is used first. Change the order by dragging a card (by the ≡ handle or any empty spot) or with the ▲ ▼ arrows. While you drag, the list scrolls with the mouse wheel and by itself near the top and bottom edge of the window; a blue bar shows where the card will land (before or after the card under the pointer). ++Esc++ cancels the move. A selected group moves together.

**Selection.** A click on an empty spot of a card selects it, ++Ctrl+++click adds or removes a card, ++Shift+++click selects a range, ++Ctrl+A++ selects everything shown. **Select multiple** turns on checkboxes; in that mode a plain click ticks a card. While anything is selected, a bar at the top shows "N of M selected" with the bulk actions — enable, disable, check, reserve, normal priority, delete — plus **Select all** and **Cancel**. **Cancel**, ++Esc++ or a click on empty space clears the selection and leaves selection mode. Right-click opens the menu for the selected cards.

Each card has a status:

| Status | Meaning | What to do |
| --- | --- | --- |
| working | the last request or check succeeded | nothing |
| invalid | the provider rejected the sign-in data | refresh the cookie or sign in again |
| unchecked | no requests yet | press "Check" |
| disabled | the account is switched off | enable it if needed |

A limit error (429) or a temporary provider failure **does not switch the account off or send it on a "cooldown"**: the reason stays on the card ("Last error") and in the log, and the account keeps its place in the queue. Within one request the Proxy simply moves on to the next account. The only exception is "invalid": that marks an account whose sign-in the provider itself rejected; it is skipped until you refresh the data (or a background check confirms the sign-in).

A failing account has an **Ask AI** button that opens the companion with the error already attached.

The key is copied where it lives: **Edit** (✎) → next to the key field (and other secret fields) are **show** and **copy**. It copies what is in the field now; an empty field shows "No key set".

### Ready-made connections

**＋ Add account** → **Ready-made connections** — more than 100 services with the address filled in. The most popular are tiles on top (OpenRouter, OpenAI, Anthropic, Google AI Studio, DeepSeek, xAI, Mistral, Groq, Qwen, Kimi, Z.ai, Together, YandexGPT, NVIDIA, Cerebras, Ollama, LM Studio, Hugging Face); **Show all services** opens the rest in alphabetical order with search (by name, country or model, in English and Russian), a region filter (USA, Europe, China, Russia, Asia, aggregators, this PC) and a "Has free access" checkbox.

A key-based service form has a **▶ Video guide** button next to **Where to get a key** and **Docs**: half a minute on getting a key on the site and adding the account (OpenRouter as the example).

After choosing a service, paste the key (**Get a key** opens the right page). When the address needs an identifier of yours — a Yandex Cloud folder ID, a Cloudflare account ID, a Vertex AI project and region, an AWS Bedrock region, an Azure resource name, an MWS GPT project — it has its own field and goes into the address and model names automatically. Local servers (Ollama, LM Studio, llama.cpp, vLLM, Jan, LocalAI, KoboldCpp and others) need no key. Services that take the key outside `Authorization: Bearer` (Azure uses `api-key`) are set up by Proxy itself. Every ready-made connection is an ordinary Custom API account: change anything under **Edit**.

## Settings

Sections on the left, one section at a time on the right: what people change most comes first, the rare options wait under **Advanced**. Everything saves on its own; the top right shows "Saving…", "Saved" or the error. **Search settings** (++/++ or ++Ctrl+F++) looks through every section in Russian and English, opens Advanced by itself and points to settings on other tabs — public access, for example, or catalogue models by name. A changed setting has a dot; the ↺ button next to it restores the default. **General** holds the language, the panel theme (dark, light or system) and the window behaviour; restart, statistics reset, log clearing and shutdown are under **System**.

## How an account is chosen

**Settings → Routing → How to pick an account**:

| Mode | Behaviour |
| --- | --- |
| take turns | requests rotate across working accounts |
| strictly in order | always the top working account, the next one only on error |
| verified first | accounts whose status is "working" first, then the rest in order |
| least recently used | the account that has been idle the longest |

If an account answers with an error, the Proxy tries the next one by itself. Accounts marked "reserve" are used only when the normal ones are exhausted.

## Model catalogue

The **Model catalog** page in the left panel (it used to be in Settings) lists every model of every account. A disabled model disappears from both MemuAI and the API. "Refresh from servers" pulls fresh lists from the providers. "Disable by words" (under Advanced in the catalogue) hides every model whose name contains, for example, `preview` or `audio`.

**Choosing models when an account is added.** When a new account brings 30 models or more, the Proxy asks right away which ones to keep on. All are ticked; "Disable all" and the search help to keep only what you need (the buttons act on what is currently shown). Disabled models are not deleted — enable them later right here.

**Selection in the list** works like Windows Explorer: a click selects a row, a second click on it clears it; ++Ctrl++ adds and removes rows, ++Shift++ selects a range; a box can be dragged with the mouse, and at the edge the list scrolls by itself without losing what is already selected. ++Ctrl+A++ selects all shown, ++Esc++ clears, ++Delete++ deletes the selected.

A model's API name is the provider prefix plus the model name: `genspark-claude-sonnet-5`, `kiro-claude-sonnet-4-5`, `local-qwen3-8b-q4-k-m`.

## Reliability

**Settings → Reliability** (the first two are visible at once, the rest are under Advanced):

| Parameter | Meaning |
| --- | --- |
| Wait for the answer to start | how long to wait for the answer to start before moving to another account (90 s) |
| Retry passes | how many times to walk the whole list looking for a working account |
| Whole request limit | the limit for the whole request |
| Silence in a stream | how long a pause in the middle of an answer is tolerated |
| Pause between attempts | the pause before a retry |
| Keep the connection alive | pings to the client (SSE keep-alive) while the model thinks, so long answers are not cut |

Every five minutes the Proxy closes stale connections by itself. If answers still stop coming — for example after a laptop wakes up — press **Home → Restart services**: it rebuilds the connections without closing the program.

## Logs

The **Logs** tab shows every request: model, provider, account, time, tokens and processing stages. All text can be selected and copied; a record can be copied whole, and the log can be exported as JSON or TXT. A failed request has an **Ask AI** button for the companion.

New records have **Show prompt** and **Copy prompt** buttons for the incoming messages, roles and tool context. Text is saved in full regardless of length; viewer notices are excluded from the copied prompt. Images, audio and video appear as placeholders. Enable **Settings → Logs & statistics → Advanced → Save stage contents** to use this feature. Older records may have no prompt or one truncated by an earlier version; the missing part cannot be recovered from the log.

The second tab, **Server log**, holds the Proxy's own service messages with a level filter. Look there when something broke as a whole rather than in one request.

Secret fields and known connection keys are masked in the log. An arbitrary secret you write in a message can remain part of its prompt; turn off payload saving for that conversation.

## Data

Everything is stored in `%APPDATA%\MemuAI`: `config.json` (settings), `accounts.json` (accounts), `models.json` (catalogue), logs and local model files. More in [Where data is stored](doc:data).

See also: [Proxy API](doc:proxy-api), [Accounts](doc:accounts).
