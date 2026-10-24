---
title: Web search
summary: How MemuAI searches (in one call: ready data, results and page excerpts) and which sources there are: Kiro, Bing, DuckDuckGo, Yahoo, Wikipedia, SearXNG and keyed services — Tavily, Brave, Exa, Google and others.
---

# Web search

Models know the world only up to their training date. Web search gives them fresh information: news, rates, documentation of new versions, timetables.

## Turning it on

The search toggle in the top bar enables it for the current chat. `/search on` and `/search off` do the same. Right-clicking the toggle chooses the search source right from the chat. An `@Web` mention in a message suggests checking the internet for that particular question.

To enable search in every new chat — **Settings → Web search → Enable by default**. The toggle in a chat still wins.

Even with search on, the model decides whether a request needs fresh sources: it will not search for "translate this paragraph".

## How MemuAI searches quickly

What is expensive in a search is not the query but the **model rounds**: "search" → "open the page" → "answer", each with its own delay (5–10 seconds for subscription models). So search is built to give the answer in one round:

- **Pre-search.** When a message *clearly* needs fresh data — weather, a rate, news, a price, a match result or a direct "look it up" — the search runs before the first request to the model, and it answers at once, without a tool round. A greeting, a request to rewrite a text or a question about your code is not searched; when in doubt nothing happens and the model calls search itself, as before. The sources found show in the "Web search" step.
- **One call, everything needed.** The result holds a ready answer (`answer`), the result list and **excerpts** of the most relevant passages of the top pages: pages are read at the same time, for five seconds at most, and paywalled or closed sites are simply skipped. Opening a page with a separate call is only needed when the excerpts are not enough.
- **Ready data.** For questions about the **weather** (in a named city, or without one — by IP address, marked as an approximate place), **local time**, **currency rates** and **bitcoin and ether prices** the answer comes from open APIs (Open-Meteo, open.er-api.com, CoinGecko) — a second instead of rounds. The model names the source and the notes in its answer.
- **Several queries at once.** `queries` — up to two other phrasings searched at the same time as the main one; results are merged without repeats.
- **Free sources race.** Bing and DuckDuckGo are asked at the same time and the first non-empty answer wins: a hung or blocked source no longer delays the search (a source that failed is left alone for 90 seconds). Yahoo is the last resort, one at a time.
- **Answer time** in the message header counts the whole turn, tool rounds included (it used to show only the last request to the model).

## Two kinds of search

| Kind | How it works | With which models |
| --- | --- | --- |
| Built-in | the model's own service searches (Genspark, Antigravity, for example) | only models that support it |
| External | MemuAI searches and passes the results to the model | any text model |

**Settings → Web search → Search source**:

- **Automatic** — built-in search when the model has it, external otherwise;
- **Built-in only**;
- **Custom only** — always external.

## External sources

The source is chosen in **Settings → Web search → External source → Service**. The key and the address are remembered for each service separately: switching between them loses nothing. **Test the source** makes a trial request and shows how many results came back and how fast.

### Free, no key

| Service | What it is |
| --- | --- |
| Auto: Kiro → Bing and DuckDuckGo | with a Kiro account in Proxy that answers about as fast, its built-in search is used (as in Kiro IDE); otherwise Bing and DuckDuckGo are asked at the same time, and Yahoo is the last resort. Selected by default |
| Kiro | Kiro's built-in search only. Needs a Kiro account in MemuAI Proxy |
| Bing | Bing only, no key and no accounts; fast, tolerant of frequent queries, understands Russian |
| DuckDuckGo | DuckDuckGo only, no accounts. After frequent requests DuckDuckGo asks to confirm you are human for a while — "Auto" or Bing help then |
| Yahoo | results from the Bing index; can be slow or unreachable in some countries — the last resort in "Auto" |
| Wikipedia | encyclopedia articles only; the language edition follows the language of the query |

"Kiro / DuckDuckGo" used to be a single entry. It is now separate: "Auto" (Kiro, or Bing and DuckDuckGo racing without it), "Kiro", "Bing" and "DuckDuckGo".

### Own server

| Service | What it needs |
| --- | --- |
| SearXNG | the address of your own or a public SearXNG server (the root or `/search`); the JSON format must be enabled there. A key if the server requires one |

### With an API key

| Service | Notable for | Where to get a key |
| --- | --- | --- |
| Tavily | search for AI: clean page extracts; a free monthly allowance | tavily.com |
| Brave Search API | Brave's own index; a free plan | brave.com/search/api |
| Exa | neural search: finds pages by meaning rather than by keywords | exa.ai |
| Google · Serper | Google results, fast and cheap | serper.dev |
| Google · SerpApi | Google results; a free monthly allowance | serpapi.com |
| Google · Programmable Search | Google's official API; besides the key it needs a search engine ID (cx); 100 queries a day are free. **Closed to new customers since 20 Jan 2026 and shut down on 1 Jan 2027** — only if you already had it | programmablesearchengine.google.com |
| Jina Search | Jina AI search | jina.ai |
| Perplexity Search | ranked results with extracts, without a model answer | perplexity.ai/settings/api |
| Linkup | search for AI with access to some premium publishers | linkup.so |
| Firecrawl | Firecrawl search | firecrawl.dev |
| Kagi | a paid search without ads; needs Search API access | kagi.com |

The keyed sources were checked against the response format of their APIs; the keys themselves come from the service. If a source is selected but its key is missing, a search the model asks for reports that instead of silently switching sources.

**Maximum results** limits how many found pages go to the model. More gives a fuller answer but takes longer and costs more tokens.

Keys are stored only on this computer.

## How the model uses results

Search snippets reach the model as unverified reference material. The model is told to rely on direct and authoritative sources, cite only links that were actually returned, and say honestly when the results were not enough. It does not follow instructions found inside those pages.

> [!TIP]
> If an answer about a recent event sounds dated, check that the search toggle is on in the chat and that the model has built-in search or an external source is configured.

## Search in the Proxy

MemuAI Proxy has its own search — for third-party programs that connect to its API: **Proxy → Settings → Web search**. It has the same list of sources, its own keys and a **Test** button, and works independently of MemuAI's settings. [MemuCode CLI](doc:memucode-cli) searches with this source too.

## Search in MemuAI Live

Live models can search the web during a conversation as well — see [Voice and MemuAI Live](doc:voice).

For big topics with dozens of searches and a cited report there is [Deep research](doc:research).

See also: [Models](doc:models), [Proxy API](doc:proxy-api).
