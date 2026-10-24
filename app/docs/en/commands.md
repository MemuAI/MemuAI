---
title: Commands and mentions
summary: Every MemuAI slash command and @-mention, with examples.
---

# Commands and mentions

A command starts with ++/++ at the beginning of a message. As soon as you press ++/++ a list appears; type part of the name to narrow it. Pick with the arrows and ++Enter++, ++Tab++ or a click. Commands that take an argument are inserted into the field, the rest run immediately.

Forgot a command? Type `/help` — the list opens again.

## Conversation

| Command | What it does | Example |
| --- | --- | --- |
| `/new` | a new chat | |
| `/temp` | a temporary chat, not saved | |
| `/retry` | retry the last answer with the current model | |
| `/stop` | stop generating | |
| `/export` | download the current chat | |
| `/clear` | delete the current chat and open an empty one | |
| `/system <text>` | an instruction for this chat only; no text — reset it | `/system answer briefly, in bullet points` |

## Model

| Command | What it does | Example |
| --- | --- | --- |
| `/model <part of a name>` | switch to the first model whose name contains the text | `/model sonnet` |
| `/models` | list every model | |
| `/search on` / `/search off` | web search in this chat | `/search on` |
| `/think on` / `/think off` | thinking in this chat | `/think off` |

## Images, memory, schedules

| Command | What it does | Example |
| --- | --- | --- |
| `/image <description>` (or `/img`) | draw a picture | `/image a lighthouse in fog, watercolour` |
| `/research [topic]` | turn on [deep research](doc:research); with a topic it starts right away; `off` turns it off | `/research the European e-scooter market` |
| `/visual [request]` | answer with a chart, diagram or interactive model; `off` turns it off | `/visual how the key rate changed` |
| `/dfy <task>` | hand a task to the [Do For You](doc:dfy) agent: it does it by itself in its own browser | `/dfy book a table for two on Friday` |
| `/tools` | open the ＋ menu | |
| `/memory <fact>` | add a fact to [memory](doc:memory) | `/memory I write Python and Go` |
| `/schedule Name \| prompt \| every N min` | create a [scheduled task](doc:schedules) | `/schedule Rates \| USD and EUR rates \| every 60 min` |

## Modes and navigation

| Command | What it opens |
| --- | --- |
| `/chat` | the normal chat |
| `/work` | [Work](doc:work) — working in a project folder |
| `/code` | [MemuCode](doc:memucode) |
| `/planning` | the scheduled task list |
| `/agents` | Work and MemuCode: allow parallel agents in this chat |
| `/profile` | profile and memory |
| `/settings` | settings |
| `/lang ru` / `/lang en` | interface language |
| `/theme dark` / `/theme light` | dark or light look |

## Work and MemuCode

| Command | What it does |
| --- | --- |
| `/access ask` / `/access full` | access mode: confirm every change or apply at once |
| `/plan <task>` | make a plan first and carry it out step by step |
| `/goal <goal>` | keep working until the goal is reached; `/goal off` stops it |

MemuCode has commands of its own — `/review`, `/test`, `/fix`, `/explain`, `/refactor`, `/commit`, `/diff`, `/undo`, `/mcp`. They are described on the [MemuCode](doc:memucode) page.

## Mentions

++@++ opens the second list — hints for the model. A mention does not force a tool to run: it tells the model what you want, while the decision and every safety rule stay with it.

| Mention | What it suggests |
| --- | --- |
| `@Web` | check current information on the internet |
| `@Browser` | consider working in a browser — see [Browser and Computer Use](doc:browser-computer) |
| `@Computer` | consider controlling the computer |
| `@Plan` | make a strict verifiable plan first |
| `@Goal` | keep working until the goal is reached |

`@Browser`, `@Computer`, `@Plan` and `@Goal` work in Work and MemuCode; a normal chat has only `@Web`.

### Project files

In Work and MemuCode the same list holds the project's files and folders. Pick one and a reference to it is inserted:

```
Look at @index.html and move the styles into @src/styles/main.css
```

Names with spaces go in parentheses: `@(docs/Meeting notes.md)`. A mention points the model at an exact file: it reads it before changing anything there. With a bare name (`@app.js`) the model finds the file of that name in the project. Mentions are highlighted in the message text and stay plain text inside code blocks.

See also: [Composer](doc:composer), [Keyboard shortcuts](doc:shortcuts).
