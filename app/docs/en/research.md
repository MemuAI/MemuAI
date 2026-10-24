---
title: Deep research
summary: Dozens of searches, reading sources, checking gaps and contradictions, and a report with numbered citations.
---

# Deep research

Ordinary web search answers a question from a few links. **Deep research** does a researcher's job: the AI scopes the topic, decides how much to search, reads sources in full, checks what is missing and writes a detailed report where every claim is backed by a citation.

## Turning it on

- ＋ in the message field → **Deep research**. A "Research" chip appears next to ＋ and the field asks "What should I research in depth?".
- Or the `/research topic` command — it turns the tool on and starts right away.
- Or the **Deep research** suggestion on the empty chat screen.

The tool belongs to the chat: while it is on, every new question in this chat is researched. Turn it off by clicking the chip or with `/research off`. It is not available in Work and MemuCode.

## How it works

### The plan

The model analyses the request, states what the report must deliver and splits the topic into 3–10 sub-questions. For each it writes precise search queries — with names, dates, terms, in the language where sources are best. **The model itself decides how many searches are needed**: a narrow fact needs a few, a market overview or a comparison of countries needs dozens. Settings only set a ceiling.

### Search rounds

1. Queries run in parallel through the source from [web search settings](doc:web-search).
2. From the results the model picks the pages with the strongest evidence: primary sources, official data, reputable publications, different domains.
3. Pages are read in full, and from each the model extracts facts, numbers, dates, definitions and quotations, rating how reliable the page is.
4. **Coverage check**: the model compares the evidence with the sub-questions, notes contradictions and weak spots and decides — is the evidence enough, or is another round with follow-up queries needed.

### The dossier and the report

When the evidence is enough, a dossier is assembled: numbered sources and their extracts. From it the chat model writes the report: a title, a short conclusion, sections that follow the logic of the topic, comparison tables, a section on contradictions and limitations, conclusions and the list of sources. `[3]` citations in the text become clickable footnotes.

## What you see while it works

Above the answer is the research card:

| Element | What it shows |
| --- | --- |
| Bar and stages | Plan → Search → Reading → Extracts → Review → Report |
| Plan | the title, why this amount of searching was chosen, the sub-questions; after the review — "answered", "partial", "gap" marks |
| Search queries | every query, its status and the number of results |
| Sources | what is being read, what has been read, how many facts were found; when done — the report's sources with their numbers |
| Contradictions | contested claims between sources |

The Stop button interrupts the whole run — searching, reading and model calls.

## Settings

**Settings → Web search → Deep research**:

| Setting | What it sets | Default |
| --- | --- | --- |
| Research model | the model for planning, reading and review; the report is written by the chat model | the chat's current model |
| Maximum searches | the ceiling for the whole run | 40 |
| Refinement rounds | how many times it may go back for missing data | 4 |
| Maximum pages read | how many pages are read in full | 30 |

> [!TIP]
> A fast, inexpensive model suits research: it makes many short calls. The report is still written by the chat model you picked — its quality shows in the final text.

## Sources and citations

Numbers in the report match the source list at the end and in the research card. All facts come from the pages that were read; general knowledge the model adds, if any, is marked separately. Page text is treated as untrusted: the model does not follow instructions found on pages.

## Limitations

- Research makes many model and search calls — it takes noticeably longer and costs more than an ordinary answer, usually one to several minutes.
- Pages behind a login, bot protection or without text are skipped — the card shows them as unread.
- Quality depends on the search source: SearXNG and Tavily usually give more and better results than free DuckDuckGo.
- If no useful source could be collected, MemuAI reports the error honestly instead of inventing a report.

See also: [Web search](doc:web-search), [Composer](doc:composer), [Agents and memes](doc:agents).
