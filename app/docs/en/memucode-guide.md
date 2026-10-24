---
title: Working effectively with MemuCode CLI
summary: A practical guide to MemuCode CLI: project memory, phrasing tasks, plan and accept-edits modes, iteration, parallel sub-agents, /goal, skills, permissions, cost and headless mode.
---

# Working effectively with MemuCode CLI

[MemuCode CLI](doc:memucode-cli) is the reference for commands, shortcuts and settings. This guide is about something else: how to organise the work so the agent delivers its best without burning your nerves or your tokens. The techniques scale from a one-off edit to long goals and CI.

## Start with project memory

The first thing to do in a new project is `/init`: MemuCode studies the code and writes `MEMUCODE.md`, the instructions for agents (`AGENTS.md` and `CLAUDE.md` are read too). Memory then grows on the fly: `#rule` appends a rule straight from the conversation, `/memory` opens the files for editing.

Keep rules short and concrete — they go into every request:

| Bad | Good |
| --- | --- |
| "Write quality code" | "Tests: pytest, run with `python -m pytest tests/`" |
| "Follow best practices" | "Absolute imports only, from the `app/` package root" |
| "Don't break anything" | "Never touch migrations in `alembic/versions/` unless asked" |

`@path` inside `MEMUCODE.md` can include other files — for example a shared code style.

> [!TIP]
> Memory is not a second README. A rule earns its place only if the agent already got it wrong without one: "UI copy in English", "run `ruff check` before committing". Prune stale entries with `/memory` from time to time.

## Phrase tasks as outcomes

Describe what should come out, not the sequence of steps — the agent finds its own path, and you can check the result against criteria.

| Weak | Strong |
| --- | --- |
| "make it nice" | "login form: client-side email validation, the error under the field, Enter submits" |
| "fix the tests" | "`/test`: all failures come from the new date format; fix them so pytest is green" |
| "add export" | "a CSV export button next to the filter; separator `;`; verify on an empty table" |

Give context up front so the agent does not search blind:

- `@path` — a file or folder is attached to the request; `@agent-name` hands the task to a specific sub-agent;
- ++Alt+V++ (or ++Ctrl+V++) — a clipboard screenshot: layout bugs and mockups are cheaper shown than described; an image path pasted into the terminal becomes an attachment too;
- a long text paste (a log, a stack trace) collapses into `[Pasted text #1 +40 lines]` and expands when sent — do not be afraid to paste whole logs;
- ++Ctrl+G++ — write a big prompt in an external editor;
- `!command` — run a command yourself, without the model: the output joins the conversation as context.

## Modes: plan first, edit second

Switch modes with ++Shift+Tab++ or `/permissions`. The point is to choose deliberately:

| Mode | When to use it |
| --- | --- |
| Plan (`/plan <task>`) | a big or non-obvious task: the agent only researches the code and proposes a plan for approval — fixing a plan is cheaper than redoing edits |
| Default | one-off tasks; every edit and command needs approval |
| Accept edits (`acceptEdits`) | a long series of edits in familiar code: files change without questions, commands still ask |
| Auto | you trust the decision model: safe actions run on their own, risky ones ask |
| Bypass (`bypassPermissions`) | **only** containers, sandboxes and throwaway machines — for example headless CI runs |

A typical rhythm for a big task: plan → approve → `acceptEdits` → work → `/diff` and `/review` before the commit.

## Iterate in short steps

One agent answer — one reviewable portion of work. Rollback stays cheap and the context stays lean.

- `/diff` — uncommitted changes and this session's edits; look at them instead of taking their word;
- ++Esc++ — stop the agent before it goes the wrong way; ++Esc++ ++Esc++ — `/rewind`: take the code, the conversation or both back to a chosen message;
- `/undo` — revert the file changes of the last answer; `/branch` — fork the conversation to try another way without losing this one;
- `/context` — what fills the window; `/compact <what to focus on>` — summarise the history by hand before it compacts itself (automatically at the 85% threshold);
- `/clear` — a task unrelated to the previous one: start a new conversation instead of dragging everything through one.

> [!TIP]
> When an edit goes wrong, rewinding (++Esc++ ++Esc++) and rephrasing the task is cheaper than "fixing up" a bad result with follow-up messages.

## Parallel work

While the main conversation is busy, hand independent work to the background:

- `/subtask <task>` (also `/background`, `/bg`, or the `&task` prefix) — a separate agent works in parallel and reports back with a note in the conversation;
- `/tasks` — the list of background jobs and shells: output, stop;
- the agent launches sub-agents itself with the `Task` tool: several calls in one turn run in parallel, so "investigate three modules" is cheaper as one message than as three;
- long commands (servers, builds, test runs) go to the background through `Bash` with `run_in_background`, and the agent reads their output with `BashOutput` — the terminal stays free.

The classic pattern: you and the main model finish a feature while `&write the docs section for the new commands` works alongside — by the time the edits land, the documentation is ready.

### The sub-agent model

`/agents` picks **one model for all sub-agents** (or inheriting the main one). If your background tasks are simple — code searches, drafts, documentation — put a cheap fast model there: the main conversation keeps its strong model while the background gets cheaper. For complex sub-agents (`reviewer`, a parallel refactor) keep a strong one.

## Long goals: /goal

`/goal <goal>` — the agent works step by step and keeps continuing itself until the goal is met or it hits a blocker (up to 10 continuations by default; `--max N` changes the limit). Phrase goals so they can be verified: not "improve coverage" but "all tests green and `app/core` coverage is at least 80%". Controls: `/goal status`, `/goal pause`, `/goal continue`, `/goal clear`. The decision model (`/decisions`) checks whether the goal is actually met, so a vague "looks done" will not pass.

## Ask without interrupting

`/btw <question>` — a quick side question: the agent is not interrupted, and the answer stays out of the history and does not eat context. "/btw which Python version does the project use?" is exactly the case.

## Permissions and rules

Frequent approval prompts are cured by rules: in an approval window answer "yes, and don't ask again" — MemuCode suggests a rule. List and edit them with `/permissions`.

| Rule | Effect |
| --- | --- |
| `Bash(npm test:*)` | allows commands starting with `npm test` |
| `Edit(src/**)` | allows edits inside `src` |
| `WebFetch(domain:docs.python.org)` | allows reading that site |
| `mcp__github` | allows every tool of the `github` MCP server |

Start with tests and builds (`Bash(pytest:*)`, `Bash(npm run build:*)`) and the folders the agent edits all the time. Deny rules win over allow rules, and compound commands are checked part by part.

## Skills and custom commands

Repeated playbooks are saved, not retyped:

- **Skills** — `.memucode/skills/<name>/SKILL.md` in the project, personal ones, or `.claude/skills/`. The agent loads a matching skill itself; `/skills` lists, shows and creates them; `/skill-name` runs one by hand. Good skills: "release", "DB migration", "open a PR with our template".
- **Custom commands** — Markdown files in `.memucode/commands/` (`.claude/commands/` works too), invoked as `/file-name`. `$ARGUMENTS` and `$1`…`$9` are expanded in the text: a `fix-issue.md` reading "Find issue #$1 and fix it" becomes `/fix-issue 42`.

## MCP: external tools

An MCP server is worth it when the agent needs an external service — GitHub, a database, an issue tracker. Add one with `/mcp` → "＋ Add a server" (a stdio command or an HTTP URL), or without the interface:

```
memucode mcp add <name> -- <command> [args…]
```

Server tools are named `mcp__server__tool`; allow them with a rule right away. A project's `.mcp.json` only works in a trusted folder.

## Model and thinking depth

`/model` (++Alt+P++) picks the model, `/effort` (`auto`, `minimal`, `low`, `medium`, `high`, `max`) sets how deeply it reasons, and ++Alt+T++ turns thinking on and off. The rule is simple: light edits and questions — low depth, architecture and hard bugs — high.

How this reaches the models through MemuAI Proxy:

- **Claude from Antigravity** think on a token budget, and `/effort` sets it: `low` — 2048, `medium` — 8192, `high` — 16384, `max` — 32000, `minimal` turns thinking off;
- **Gemini** carries its thinking level in the model name — pick `…-low`, `…-medium` or `…-high` (3.1 Pro has two levels: `-low` and `-high`); Gemini 3.7 and 3.8 Flash have three levels (`…-flash-low`, `-medium`, `-high`), and the name without a level follows `/effort` or leaves the choice to Google;
- the answer comes in the language of your message whatever you pick — the interface stays English while the conversation follows your language.

More about the model names is in [Accounts](doc:accounts).

## Cost and tokens

- `/usage` — tokens per model, time, requests, the session's estimated cost, today's and 30-day spend, account limits;
- `/cost` — cost per model; `/cost prices` — where each price came from, `/cost set` — your own prices;
- clicking the footer metric cycles it: cost → tokens → duration → tool calls → off. Keep the number you actually watch in view.

Saving without losing quality: `/model auto` (simple requests go to the fast model), a cheap sub-agent model (`/agents`), `/compact` before the automatic summary, and short sessions instead of one endless one.

## Headless and CI

Without the interface MemuCode is an ordinary scripting tool:

```
memucode -p "find and fix the failing test" --output-format json
git diff | memucode -p "write a commit message"
memucode -c -p "continue: add tests for the new cases"
```

`--output-format stream-json` emits events line by line — handy for pipeline logs. `memucode -c` continues the project's latest conversation: several CI steps can work as one session. Useful flags: `-m` (model), `--permission-mode`, `--allowedTools`/`--disallowedTools`, `--max-turns`, `--append-system-prompt`. Remember: without the interface, actions needing approval are denied — write permission rules in advance or (in a container) use `--permission-mode bypassPermissions`.

## The key shortcuts

| Keys | Action |
| --- | --- |
| ++Shift+Tab++ | switch the permission mode |
| ++Esc++ | stop the agent |
| ++Esc++ ++Esc++ | rewind to an earlier message (`/rewind`) |
| ++Ctrl+T++ | the agent's todo list |
| ++Ctrl+O++ | verbose tool output |
| ++Ctrl+R++ | search the prompt history |
| ++Ctrl+G++ | write the prompt in an external editor |
| ++Alt+V++ / ++Ctrl+V++ | paste an image from the clipboard |
| ++Alt+P++ / ++Alt+T++ | pick a model / toggle thinking |
| ++?++ on an empty line | keyboard help |

The full table is in [MemuCode CLI](doc:memucode-cli).

## Common beginner mistakes

| Mistake | Do this instead |
| --- | --- |
| "Make it nice" with no criteria | describe the outcome and how to verify it |
| One giant session for everything | `/clear` between unrelated tasks, `/compact` in a long one |
| Accepting edits sight unseen | `/diff` after each notable step, `/review` before committing |
| Checking code by eye | make the agent run the tests: `/test`, or a "run pytest after edits" rule in `MEMUCODE.md` |
| Patching up a failed edit with more messages | ++Esc++ ++Esc++ and rephrase the task |
| A big task straight into edits | plan first: `/plan` or Plan mode |
| Doing everything sequentially | independent work goes to `/subtask`, research to parallel `Task` calls |
| Bypass mode on a working machine | it is only for containers and throwaway environments |
| Hoarding rules in `MEMUCODE.md` for years | prune it: every line costs tokens in every request |

See also: [MemuCode CLI](doc:memucode-cli), [MemuCode](doc:memucode).
