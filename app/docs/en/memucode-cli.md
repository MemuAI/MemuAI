---
title: MemuCode CLI
summary: MemuCode in your terminal: a coding agent with /model, /usage, /rewind, /goal, permission modes, scheduled tasks, other tools' sessions, session cost and decision models.
---

# MemuCode CLI

**MemuCode CLI** is the same coding agent as [MemuCode](doc:memucode), in a command line. It reads and edits project files, runs tests and builds in a sandbox, searches the web, drives a browser and the desktop, remembers conversations and can undo any edit. Models come from [MemuAI Proxy](doc:proxy), so the same accounts work as in the app — and you can connect APIs directly if you like.

## Install and run

### From the installer

The full MemuAI install offers an optional **MemuCode CLI** checkbox. When selected, the program lives in its own `MemuCode` folder next to MemuAI, the Start menu gets a shortcut and the folder is added to your user `PATH`. Open a new terminal in a project folder and run:

```
memucode
```

Rerun MemuAI Setup and uncheck **MemuCode CLI** to remove only its executable and `PATH` entry. Your chats, skills and settings remain in your profile.

### From source

```
python memucode.py
```

The `/install` command (or `python memucode.py install`) adds a short `memucode` command to your user `PATH`; `memucode uninstall` removes that command. Use Setup to remove an installed executable. It needs the `prompt_toolkit`, `rich` and `anthropic` packages from `requirements.txt`.

## First run

1. MemuCode looks for a running MemuAI Proxy. If it does not answer, MemuCode starts it (the `proxy_autostart` setting turns this off).
2. The first time, you pick a colour theme. Use the arrow keys or mouse wheel to preview each theme before confirming.
3. For a new folder MemuCode asks whether you trust it: project hooks, MCP servers from `.mcp.json` and project settings only work in a trusted folder.
4. The default model comes from the MemuAI settings; change it with `/model` or ++Alt+P++.

To check everything at once, run `/doctor` (or `memucode doctor` without the interface): terminal, Proxy, models, shell, git, sandbox, browser, MCP, prices, decision model, microphone.

## Input

| What you type | What happens |
| --- | --- |
| plain text | a task for the agent |
| `/command` | a MemuCode command; the list opens right away |
| `@path` | mention a file or folder — its content is attached to the request |
| `@agent-name` | hand the task to a sub-agent |
| `!command` | run it in the terminal without the model; the output joins the conversation. Not run when sent from the Telegram Mini App: ask the agent in a normal message there |
| `#rule` | save a rule to a memory file |
| `&task` | run the task in the background without blocking the conversation |

While the agent works you can keep typing: messages queue up and are sent when it finishes a step. ++↑++ brings a queued message back for editing. Commands that only show or set something (`/skills`, `/model`, `/usage`, `/permissions`, `/schedule`…) run at once and never go to the model or interrupt the work; commands that start a new conversation or change the folder wait until the agent is done.

`@` suggests only files and folders **of the project** (and folders added with `/add-dir`); it never scans the whole disk. In a very large folder (a user profile, a drive root) the index is built within a time and size budget and says so. `/cd` with a folder that does not exist offers to create it (the default answer is No); `/cd path --new` creates it without asking.

Images: ++Alt+V++ (or ++Ctrl+V++) pastes an image from the clipboard. Drag a file into the terminal or use `/attach <path>`: images become `[Image #1]`, text files become `[File #1: name]`. A long text paste collapses into `[Pasted text #1 +40 lines]` in the input only: it expands when sent, and the conversation shows the whole pasted text. A pasted path to a folder, or to a large or binary file, stays plain path text — with no error message.

++Ctrl+A++ selects all the text in the input, ++Ctrl+C++ copies the selection, ++Ctrl+X++ cuts it. A backslash at the end of a line starts a new line, but not after a Windows path or a command: `/cd C:\Projects\` is sent as it is.

Command suggestions appear at once as you type; ++Esc++ closes menus and settings without a delay. MemuCode makes no sounds: a quiet Windows notification tells you a task is done. The terminal bell is the `notifications` = `bell` setting; `off` turns notifications off altogether.

`/effort` sets the depth of reasoning. The list shows only the current model's levels — the same as in MemuAI (GLM-5.3 has no "Off", Gemini 3.7 Flash no "Minimal", for example), with a line on what the model supports; a typed level the model lacks (`/effort minimal`) is saved and sent as the nearest one — the command names which. If the chosen model does not support levels, the value is saved but has no effect on it — the command says so plainly. Details: [Models → Thinking](doc:models).

The input stays at the bottom of the terminal. Scroll the conversation and pickers with the mouse wheel or PageUp/PageDown. Drag to select text in the conversation; it is copied when you release the mouse, with the character count shown beside the input. Command arguments have Tab suggestions, including folder paths for `/cd`.

### Shortcuts

| Keys | Action |
| --- | --- |
| ++Enter++ | send |
| ++Shift+Enter++, ++Alt+Enter++, ++Ctrl+J++ or `\` at the end of a line | new line |
| ++Shift+Tab++ | switch the permission mode |
| ++Esc++ | stop the agent |
| ++Esc++ ++Esc++ | rewind to an earlier message (`/rewind`) |
| ++Ctrl+C++ | stop; twice on an empty line — exit |
| ++Ctrl+R++ | search the prompt history |
| ++Ctrl+G++ | write the prompt in an external editor |
| ++Ctrl+O++ | verbose tool output |
| ++Ctrl+T++ | the agent's todo list |
| ++Alt+P++ / ++Alt+T++ | pick a model / toggle thinking |
| ++Ctrl+L++ | clear the screen |
| ++PageUp++ / ++PageDown++ | scroll the conversation |
| ++?++ on an empty line | keyboard help |

++Shift+Enter++ works in Windows Terminal and the classic Windows console out of the box. For the VS Code terminal run `/terminal-setup`. `/vim` turns on Vim mode.

## Permission modes

| Mode | Behaviour |
| --- | --- |
| Default | file edits and commands that change the system wait for approval; reading and searching run right away |
| Accept edits | file edits without questions, commands with approval |
| Plan | research only: the agent prepares a plan and asks you to approve it |
| Auto | safe actions run on their own, risky ones ask; the risk is judged by the decision model, the fast model or built-in rules |
| Bypass | everything runs without approval — only for sandboxes and throwaway machines |

Switch with ++Shift+Tab++, `/mode` (or `/permissions`, whose first row is the mode picker) or the `--permission-mode` flag. The choice is shown in the status line and in a short notice with what the mode means, and **it applies to every MemuCode window**: a mode chosen in one session is taken by the others within a second and by new ones at start (`permission_sync` turns this off; a window started with `--permission-mode` keeps its own). Plan mode is a state of one task and is never shared. Dangerous commands (formatting disks, shutting the computer down and the like) never run in any mode.

In **Auto** a short list of unmistakable cases always asks, whatever the judge says: `git push`, `git reset --hard`, publishing a package, `sudo`, changes to system settings, sending data out with `curl -d`, running a downloaded script, reading or copying secrets (`.env`, `~/.ssh`, `.aws`), deleting whole folders other than build leftovers (`node_modules`, `dist`…), and writing to `.env`, key files, `.git`, CI workflows or MemuCode's own settings. A rule you wrote yourself (`Bash(git push:*)`) still wins over this list.

### Rules

In an approval prompt you can answer "yes, and don't ask again" — MemuCode suggests a rule. Rules use the Claude Code syntax:

| Rule | What it allows or denies |
| --- | --- |
| `Bash(npm test:*)` | commands that start with `npm test` |
| `Edit(src/**)` | edits inside `src` |
| `WebFetch(domain:docs.python.org)` | reading pages of that site |
| `mcp__github` | every tool of the `github` MCP server |

Compound commands (`a && b`, `a | b`) are checked part by part. Deny rules win over allow rules. List and edit them with `/permissions`.

## Commands

The full list is `/help`; `/help command` shows details. The main groups:

| Group | Commands |
| --- | --- |
| Session | `/clear`, `/resume`, `/continue`, `/dashboard`, `/resume-claude`, `/resume-codex`, `/resume-grok`, `/resume-gemini`, `/resume-agy`, `/resume-opencode`, `/resume-cursor`, `/rename`, `/branch`, `/rewind`, `/undo`, `/export`, `/copy`, `/todos`, `/auto-continue` |
| Models | `/model`, `/fast`, `/small-model`, `/vision`, `/effort`, `/think`, `/web`, `/advisor` |
| Context and memory | `/compact`, `/context`, `/memory`, `/add`, `/image`, `/paste` |
| Working with code | `/init`, `/plan`, `/goal`, `/review`, `/security-review`, `/test`, `/fix`, `/explain`, `/refactor`, `/commit`, `/pr`, `/diff`, `/run` |
| Agents and tools | `/background`, `/tasks`, `/agents`, `/schedule`, `/skills`, `/mcp`, `/browser`, `/computer`, `/search`, `/fetch`, `/decide`, `/loop`, `/tools` |
| Settings | `/settings`, `/permissions`, `/add-dir`, `/cd`, `/theme`, `/color`, `/lang`, `/vim`, `/output-style`, `/statusline`, `/proxy`, `/import`, `/decisions`, `/hooks`, `/sandbox`, `/notifications`, `/terminal-setup`, `/keybindings`, `/install` |
| Diagnostics | `/usage`, `/cost`, `/stats`, `/status`, `/doctor`, `/bug`, `/release-notes` |
| General | `/help`, `/help-ask`, `/btw`, `/voice`, `/exit` |

A few commands worth knowing:

- `/btw <question>` — a quick side question; the agent is not interrupted and the answer stays out of the history.
- `/goal <goal>` — the agent keeps working step by step until the goal is met or it hits a blocker; `/goal status`, `pause`, `clear`.
- `/background <task>` — a separate agent works in parallel and reports back with a note; `/tasks` lists background jobs and shells.
- `/advisor` — a second, stronger model the agent consults at hard spots.
- `/model auto` — you name several models per kind of request (simple, everyday, hard), say in a few words what each is good at, and for every request MemuCode picks the level and then the model whose remark fits best; see [Choosing models](#choosing-models).
- `/schedule` — a prompt MemuCode runs by itself at a time; see [Scheduled tasks](#scheduled-tasks).
- `/dashboard` — every earlier session in one list: MemuCode's own in all projects and, on tabs, those of the other tools; see [Sessions of other tools](#sessions-of-other-tools).
- `/import` — bring MCP servers, rules, instructions, skills, commands and sessions over from Claude Code, Codex, Grok, Gemini CLI, Antigravity, OpenCode and Cursor.
- `/skills` — a manager: create (the model can draft it from a description), edit, switch off and delete skills right in the terminal.

Custom commands are Markdown files in the project's `.memucode/commands/` or your personal `commands` folder; files from `.claude/commands/` work too. `$ARGUMENTS` and `$1`…`$9` are expanded.

## Models and connections

By default every model goes through MemuAI Proxy: `/model` lists the models of all your accounts with marks for vision, thinking and context size. Each row is a readable name as in MemuAI (your rename, otherwise the Proxy catalog name), with the full id muted on the right; search works by name ("opus 4.6") and by id. After a pick MemuCode prints "Model: Claude Opus 4.6 (aws9-…) · context …". Proxy models use tools in a text format, so any model works.

`/proxy add` connects an API directly — Anthropic, OpenAI, OpenRouter or any compatible endpoint (`/proxy remove` removes a connection, `/proxy` shows the Proxy status and the connections). Such models are named `@connection:model` and use native tools; with Anthropic, prompt caching, adaptive thinking and effort levels (`/effort`) are on as well. Keys from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY` are picked up automatically. The old `/login` and `/logout` are gone.

`/model` searches by provider and by name: `/model antigravity` lists that provider's models, `/model glm` lists every GLM whoever serves it, `/model glm 4.6` narrows it. One match is taken at once, several open the picker already filtered.

## Images and the image model

Paste a picture (++Alt+V++), attach it with `/image <path>` or mention it with `@`. MemuCode then decides by itself, without asking:

1. **Does the main model see pictures?** It looks at four sources, surest first: what a real request has already shown (a picture went through or the model refused — remembered in `vision-support.json`, three weeks for a refusal and half a year for a success), what the model list says (OpenRouter publishes input modalities), what the name says (Claude, Gemini, GPT-4o and newer, Qwen-VL see; DeepSeek, GPT-OSS, Llama 3.x do not) and the provider's flag. When it does not know it **sends the picture**, and if the model refuses it remembers that and describes the picture instead.
2. **It does not — MemuCode switches to the image model.** That model looks at the picture *for the task the main model is working on* (it is given your request), copies all visible text verbatim and returns a description; the main model gets it as text instead of the picture. The description is made once and kept in the session, so `/resume` and the next requests do not repeat it. The transcript shows one line: "◆ model cannot see images — … described them".

If the main model needs a detail the description lacks, it calls `Read` on the picture file with a `question`, and the image model answers exactly that.

The image model is chosen with `/vision` and is **shared with MemuAI** (the setting "Image analysis model"): choose it in one program and the other has it. With none chosen the best connected model with vision is used (a fast one, not a "thinking" one).

| Command | What it does |
| --- | --- |
| `/vision` | shows what happens to pictures right now and opens the image model picker (only models that can see are listed) |
| `/vision <model>` | picks it at once |
| `/vision auto` | clear the choice: pick automatically |
| `/vision on`, `/vision off` | turn descriptions on or off (off: the main model only gets a note that there was an image) |
| `/vision test` | **a real check**: sends a picture with a random code to the main model and to the image model and sees whether they read it; the result is remembered |

The same model serves **Browser** and **Computer Use** when they have none of their own (`/settings` → `browser_model`, `computer_model`) and the main model is blind. If the chosen model suddenly refuses pictures, the next one with vision takes over.

## Choosing models

Three places let you name **several models** and say what each is good at:

- **`/model auto`** — three levels: simple requests, everyday work, hard work. Tick one or more models per level. A request is scored for difficulty (by a decision model when one is connected, otherwise by a keyword heuristic) and goes to the matching level; if that level holds several models, the one whose remark fits the request best is chosen.
- **`/agents`** — the models parallel agents may use. The model that starts an agent can name one (`Task` has a `model` argument), or the decision model chooses from the agent's task, or "always the first".
- **`/model notes`** — your remarks, one line per model ("fast, good at CSS", "slow but careful with algorithms"). They are kept for good, shown in every model list, read by whoever has to choose and known to the main model (they are part of its instructions).

In every multi-choice list ++Tab++ ticks the row (in a list with a search box Space is a letter), ++Enter++ confirms.

## Scheduled tasks

`/schedule` opens the task list; the quick form is `/schedule daily 09:00 -- check the failing tests and tell me what broke`. When: `in 20m`, `at 15:30`, `every 2h`, `daily 09:00`, `weekdays 09:00`, `mon,fri 18:30`, or a cron line `cron 0 9 * * 1-5`. Each task has:

- a **folder** (the project it was created in), the **model** and the **rights**: read only, edit the project's files (default), Auto, or everything. Nobody can answer a question while it runs, so whatever the mode does not allow is refused and the report says so;
- a **place to run**: *in this window* (while a MemuCode window is open, nothing is installed in the system) or *in the Windows Task Scheduler* (runs with MemuCode closed while you are signed in to Windows; a missed run starts when the computer next wakes). The scheduler entry lives under `\MemuCode\<id>` and is removed with the task;
- results: the answer is saved in `schedule\results\<id>\<time>.md` in the MemuCode folder, the conversation is saved like any other (`/resume`), a notification is shown, and `/schedule log <id>` prints the last result.

Also: `/schedule list`, `run|pause|resume|delete <id>`, and `memucode schedule list` / `memucode schedule run <id>` outside the interface (this is what the Task Scheduler starts).

## Continue after the limits: /auto-continue

The answer stopped because the model's limits ran out and the key does not work for now? `/auto-continue` sends “continue” into this conversation by itself at the time you choose — no need to sit at the terminal.

| Command | What it does |
| --- | --- |
| `/auto-continue 18:30` | send at 18:30 (tomorrow if that time has passed today); also `6:30pm`, `tomorrow 09:00`, `2026-10-12 09:00` |
| `/auto-continue +2h`, `in 45m`, `1h30m` | after that long |
| `/auto-continue auto` | when the provider lifts the limit: the time comes from its answer (`Retry-After` and similar headers, or text like “try again in 1h2m”); if it named none — in 15 minutes, then with pauses |
| `/auto-continue 18:30 finish the tests` | send your own text instead of “continue” |
| `/auto-continue status` · `cancel` · `now` | what is set · cancel it · send right away |
| `/auto-continue` | a menu: “When the limit resets”, in 15 min … 5 h, tomorrow at 09:00, other time |

- The rate-limit error itself suggests the command and the reset time: “The limit resets at 18:30 (in 1:12:00). /auto-continue auto sends “continue” by itself then”.
- The countdown is in the status line — “◷ continue in 14:05”; click it for details.
- If the limit is still there at that time, it tries again: at the time the provider names, otherwise after 5, 10, 20, 40, 60 minutes. How many retries — `--retries N` (4 by default, `--no-retry` for none, for good — `/config set auto_continue.retries 6`).
- `--fallback MODEL` (or the `auto_continue.fallback_model` setting): from the second try MemuCode switches to the fallback model and says how to switch back (`/model …`).
- **Keep the window open**: nothing is installed in the system. The plan is saved with the conversation — if the window was closed, open the conversation again (`/resume`, `memucode -c`) and the countdown resumes; if the time passed while MemuCode was closed, the message goes a minute after opening (with a warning; `/auto-continue cancel` stops it). For work with the window closed use `/schedule` with the Windows Task Scheduler.
- ++Esc++ during the automatically sent request cancels the auto-continue. An error that waiting does not cure (a bad key, a missing model) stops it with an explanation.

## Questions about MemuCode itself: /help-ask

MemuCode knows itself: it has a built-in reference skill `memucode-guide` — every command, key, mode (plan, auto…), command-line flag, setting, folder, sessions, agents, skills, MCP, cost and Telegram. The reference is built from the program itself (the command registry, the key list, the flag parser and the settings), so it always matches it.

- Just ask in a normal message: “How do I turn on planning in MemuCode CLI?”, “what are your keyboard shortcuts?”, “what does /compact do?” — MemuCode recognises a question about itself, attaches the reference to that answer (the line “◆ Using the built-in MemuCode guide”) and answers with the exact commands (`/plan` or ++Shift+Tab++) instead of guessing at start-up flags.
- `/help-ask <question>` asks with the reference for sure; `/help <several words>` does the same. With no model selected (or with `--offline`) the matching lines of the reference are shown.
- The reference can be switched off in `/skills`, like any built-in skill.

## Sessions of other tools

MemuCode reads, read-only, the sessions of **Claude Code, Codex, Grok, Gemini CLI, Antigravity (agy), OpenCode and Cursor** found on this computer. Nothing in their folders is ever changed or locked.

- `/resume-claude`, `/resume-codex`, `/resume-grok`, `/resume-gemini`, `/resume-agy`, `/resume-opencode`, `/resume-cursor` — pick a session from a list (those of this project first) or give its ID: `/resume-claude 3f2a9c add a regression test`. MemuCode cannot truly resume another program's session, so it does what a colleague would: writes a **hand-over note** (what was asked, how it ended, the files touched, the last commands and the last exchange), puts it at the head of a new conversation as *untrusted data* and asks the model to check the real state (`git status`, the files) before carrying on. Text inside the note, including anything that looks like an instruction, is never an instruction.
- `/dashboard` — all sessions: a tab for MemuCode's own (every project) and one for each other tool; open, continue, import, rename, export or delete.
- `/import` — a wizard: it shows what can be taken (MCP servers, permission rules, instructions such as `CLAUDE.md`/`AGENTS.md`, skills, commands, agents, sessions), you tick what you want, see a summary, and only then is anything written. Existing things are never overwritten, secrets are masked on screen, and rules and sessions are opt-in. Claude Code skills, commands and agents need no import — MemuCode reads `~/.claude` itself.

Cursor is read from its documented formats and was not checked against a real Cursor profile; if it finds nothing or looks odd, tell us.

## Cost and /usage

`/usage` shows what the session spent: tokens per model (input, output, cache reads and writes), time, requests, the **estimated cost**, today's and the last 30 days' totals and your account limits from the Proxy. The cost also shows in the status line.

Prices are looked up in this order:

1. your prices from `/cost set`;
2. the MemuAI "Model catalog" module, when installed and loaded;
3. the public OpenRouter price list (refreshed daily in the background, `/cost refresh`);
4. a built-in table of popular models.

For subscription accounts (Genspark, Kiro, Antigravity, Gemini, Grok through the Proxy) you see the **API-price equivalent** — what the same work would cost at developer rates; what you actually spend there is subscription quota. Direct APIs show the actual cost, and OpenRouter reports the exact amount itself. `/cost prices` shows which price MemuCode used for each model and where it came from. The same estimate appears in the **MemuCode statistics** card in the app.

## Decision models

A *decision model* writes no text: it answers typed questions — yes or no, a choice among options, a score on a scale — with numbers in a fraction of a second. MemuCode asks it six kinds of question:

- **Auto mode** — how risky is this command or edit;
- **`/model auto`** — how hard is this request;
- **`/agents`** — which model should take an agent's task;
- **skill hint** — does one of the skills fit (MemuCode tells the model and shows a line);
- **`/goal`** — has the goal been reached;
- **offer a plan** — is this a big task (a dialog offers plan mode, at most once in five turns, not in Auto, with "don't suggest again").

Questions about one request are asked together in one call per model, with a few seconds' limit: a slow or failing decision model never holds the work up.

`/decisions` is the menu: **Connect…** (OpenRouter — one key, every decision model; TypeSafe directly — Jev; your own compatible endpoint; or a chat model that answers in the same format), **Which model decides…**, **A model for each check…**, **Backup models…**, **What to use it for…**, **Remarks about decision models…**, **Test the connection**, **Turn off**. With OpenRouter the list is read live from `openrouter.ai` (no key needed to read it, cached for a day, a built-in list offline), so new models — Jev, Kev 4B, Span-01 and Span-01 Lite, Solar Decide, Mercury Decide, Tev1 4B Experimental and whatever vendors add next, including future ChatGPT, Claude or Gemini ones — appear without an update. Search by name, maker or "free". An OpenRouter connection you already added in `/proxy add` supplies the key.

Choose a lighter, free model for light checks (the skill hint) and a careful one for the risk check of Auto. Backups are asked in order when the chosen model fails (not for a bad key — that is the same for every model). A model built for one kind of question (Span: yes/no; Tev1: pick one) is not asked another kind, and choosing it for a check that needs more asks first. `/decisions test` tries every model in use one by one. Turning it off keeps the settings (`/decisions on` brings them back).

Ask a decision model yourself with `/decide text ? question`, or with options: `/decide text ? question | option 1 | option 2`. What a decision model is, how to read its probabilities and what it cannot do — see [Decision models (Decisions)](doc:decisions).

## Sessions, rewind and compaction

Every conversation is saved in the MemuAI data folder: `-c` continues the latest one, `-r` or `/resume` opens a searchable list. On exit, MemuCode prints its session UID and the exact `memucode --resume <UID>` command. The history is only appended to and never erased.

Before each answer MemuCode records the state of the files. `/rewind` (or ++Esc++ ++Esc++) takes the code, the conversation or both back to a chosen message; `/undo` reverts the edits of the last answer; `/branch` forks the conversation to try another way.

As the context fills up, MemuCode first folds old large tool results, and at the threshold (85% by default) summarises the history. `/compact <what to focus on>` does it by hand, `/context` shows what fills the window. With Anthropic the repeating part of the request is cached; repeated reads of unchanged files are replaced with short references.

## Memory, skills and agents

- **Memory** — `MEMUCODE.md` files (and `AGENTS.md`, `CLAUDE.md`) in the project plus your personal `MEMUCODE.md`. `@path` includes other files. `/init` studies the project and writes `MEMUCODE.md`, `#rule` appends a rule, `/memory` opens the files.
- **Skills** — MemuCode's built-in skills, `.memucode/skills/<name>/SKILL.md`, `.claude/skills/` and personal ones. The agent loads a matching one itself; `/skill-name` runs one by hand. `/skills` is the manager: **create** (write it yourself in the terminal's own editor, or describe it and MemuCode drafts it for you to edit; nothing hands you off to another program), **edit**, **switch off**, **delete** (to a trash from which it can be restored; built-in skills can only be switched off). The model knows how skills work, where they live and how to delete one — it has a `SkillManage` tool, and deleting always asks.
- **Sub-agents** — built-in `general-purpose`, `explore`, `plan`, `reviewer`, `tester` and your own from `.memucode/agents/`. `/agents` lists and creates them. The agent starts them on its own only when you asked ("use agents", "in parallel", "in the background"): that is the `subagents` setting = `ask`. `auto` lets it decide, `off` forbids it; change it in `/settings`.
- **Output styles** — `/output-style`: default, explanatory, learning, concise or your own.
- **Hooks** — commands on agent events (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop` and more) in the Claude Code format; `/hooks` shows what is connected.

## MCP

MemuCode connects Model Context Protocol servers from the MemuAI MCP settings (shared with MemuCode in the app) and from the `.mcp.json` of a trusted project — started as a command or reached over HTTP. Server tools are named `mcp__server__tool`. `/mcp` lists, adds, toggles and restarts them; `memucode mcp` does the same without the interface.

## Without the interface

For scripts and CI:

```
memucode -p "find and fix the failing test" --output-format json
git diff | memucode -p "write a commit message"
```

`--output-format stream-json` emits events line by line. Every tool call of a model is read leniently: a stray quote, a raw line break or a trailing comma in the JSON of a call is repaired and reported, and big files go in `@content:N` blocks, so a long file no longer fails with "not valid JSON". Other flags: `-m` model, `--permission-mode`, `--allowedTools`, `--disallowedTools`, `--add-dir`, `--cwd`, `--max-turns`, `--system-prompt`, `--append-system-prompt`, `--effort`. Without the interface, actions that need approval are denied unless rules or the mode allow them. Subcommands: `doctor`, `models`, `sessions`, `mcp`, `config`, `schedule`, `install`, `uninstall`, `version`.

## Thinking and progress

While a model works the status line says what it is doing: connecting, waiting for the first token, thinking, writing, preparing a tool call (with the size of a big block being written). A model that streams its reasoning shows **Thinking…** — click it (or ++Alt+R++) to read the reasoning and click again to close; a model that does not stream says so instead of staying silent.

**Speed (`tok/s`)** is shown only when it can be measured honestly: text has to arrive piece by piece for at least a second and a half. The speed is taken over the last seconds of *this* request. A model that holds the whole answer back and releases it in one piece gets no speed — dividing such a piece by the time before it would be made up (it used to give thousands of tokens per second).

**Unreadable tool calls.** When a model writes a call with a mistake (a bracket not closed, a comma where `:` belongs, an `@content` reference without its block, a stray entry in the list), MemuCode reads it anyway, and the transcript shows a calm line "Resent a tool call" instead of a red error. If a call truly cannot be read, the model is told exactly what is wrong and how to write it right, after the third such attempt in a row it is asked bluntly to simplify, and after the sixth the work stops with a clear reason instead of burning the limits.

## Settings

`/settings` opens every setting with a list of values. Settings are layered, each one overriding the previous:

| File | Purpose |
| --- | --- |
| `%APPDATA%\MemuAI\memucode\settings.json` | your personal settings |
| `.memucode/settings.json` | shared project settings, safe to commit |
| `.memucode/settings.local.json` | your settings for this project, added to `.gitignore` automatically |
| command-line flags | this run only |

API keys and connections live only in the personal file: project files that contain them are ignored with a warning. From the command line: `memucode config list`, `get`, `set`, `unset`.

## Safety

- Commands run in a sandbox — a Windows job object with a memory limit; secrets from environment variables do not reach it. Stopping the agent ends the whole process tree.
- The agent works only in the project folder and folders from `/add-dir`; going outside asks first.
- Keys, tokens and cookies are never printed or written to logs, and `/settings` masks them.
- The `/bug` report is saved to "Downloads\MemuAI": keys are masked and no conversation text goes in.

## Troubleshooting

| Problem | What to do |
| --- | --- |
| "The Proxy is not responding" | start MemuAI or run `/proxy`; to work without the Proxy — `/proxy add` |
| ++Shift+Enter++ sends the message | `/terminal-setup`; ++Alt+Enter++ and `\` at the end of a line always work |
| no colours or broken borders | use Windows Terminal; `/theme` → an ANSI variant for old consoles |
| a model's cost is unknown | `/cost refresh` or `/cost set` |
| `memucode` is not found | open a new terminal after installing or run `/install` |
| the model's limits ran out | `/auto-continue auto` (or `18:30`, `+2h`) — MemuCode continues by itself; keep the window open |

For detailed diagnostics run `/doctor`. See also: [MemuCode](doc:memucode), [MemuAI Proxy](doc:proxy), [Browser and computer](doc:browser-computer), [Troubleshooting](doc:troubleshooting).
