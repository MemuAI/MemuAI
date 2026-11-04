---
title: MemuCode
summary: An agent for repositories: file tree, editor, changes, a sandboxed terminal, permissions approved in the chat, undo, skills and MCP.
---

# MemuCode

**MemuCode** is the mode for programmers. Unlike [Work](doc:work), it is built around a repository: the model runs tests and builds, looks at git, shows changes line by line, and every edit can be undone.

Open it with the **MemuCode** button in the sidebar or the `/code` command.

## The screen

| Part | What is there |
| --- | --- |
| Top bar | project, permissions, plan, starting and stopping processes, sandbox state |
| Left, above the chats | the project file tree with a filter and the git branch |
| Centre | the chat with the agent |
| Right | a panel with sections: file, changes, artifacts, terminal, tasks, undo, skills |

Both side panels hide and come back with the buttons on the top bar. The right panel can span the full width with "Expand the panel" in its header.

### Keys

| Keys | Action |
| --- | --- |
| ++Ctrl+P++ | find a file, command or skill |
| ++Ctrl+Shift+E++ | the project tree |
| ++Ctrl+Shift+D++ | the right panel |
| ++Ctrl+Backquote++ | the terminal |
| ++Alt+1++ … ++Alt+7++ | sections of the right panel |

## Files

Clicking a file in the tree opens it on the right with syntax highlighting. The file header has buttons to copy the path or text, mention the file in the prompt (`@path`), show it in File Explorer, and **Edit**.

### Editing by hand

**Edit** (the ++E++ key, or ++Alt++ + double-click on a line) turns the viewer into an editor:

| Keys | Action |
| --- | --- |
| ++Ctrl+S++ | save |
| ++Ctrl+Z++ / ++Ctrl+Y++ | undo / redo |
| ++Ctrl+F++ / ++Ctrl+H++ | find / find and replace |
| ++Ctrl+G++ | go to line |
| ++Ctrl+/++ | toggle comment |
| ++Ctrl+D++ | duplicate line |
| ++Alt+↑++ / ++Alt+↓++ | move line |
| ++Tab++ / ++Shift+Tab++ | indent / outdent |

The editor has its own context menu with all of these. The file's encoding and line endings are preserved. If a character is added that Windows-1251 cannot hold, such a file is saved as UTF-8 so nothing is lost.

If the agent or another program changed the file while you were editing, the editor shows a conflict: **Keep my version** or **Take the disk version**. Manual edits also go into [undo](#changes-and-undo), marked "your edit".

## Permissions

The permissions button on the top bar (or the `/access` command) sets what the agent does without asking you:

| Level | File edits | Commands |
| --- | --- | --- |
| **Ask first** (default) | wait for your decision | wait for your decision |
| **Auto-edit** | apply at once and can be undone in Undo | wait for your decision |
| **Full access** | apply at once | run at once |

Read-only commands — `git status`, `git diff`, `ls` and the like — run at every level. `/access ask`, `/access edits` and `/access full` switch the level straight away, without the chooser.

### Approving in the chat

When the agent needs permission, the turn pauses and a card appears in the chat: the diff for an edit, or the command and where it will run. You can answer with:

- **Apply** (**Delete** for a removal) or **Run** — once;
- **Allow edits in this session** / **Allow commands in this session** — such actions run without asking until the session ends, and the permissions button shows a “+”;
- **Reject** — the agent gets a refusal and carries on without that action;
- or just write in the chat what to do instead: the message rejects the action and reaches the agent as an instruction.

Stopping the answer cancels a pending action too. If the window reloads while a card is waiting, the card is marked “Not decided” — nothing runs.

## MemuCode settings

**Settings → MemuCode** gathers everything about the MemuCode window:

- **Permissions** — the same three levels as on the bar;
- **Agent** — the model for MemuCode (by default the one used in chats), plan before edits for new sessions, automatic checks (the agent runs tests and linters after editing; when off, it only names the command that would verify the change), project rules and the step limit — how many tool rounds the agent may take in one answer (6 to 50, 20 by default);
- **Editor and changes** — code font size, wrapping long lines, the diff view (unified or split), and whether a file the agent changes opens in the right panel; a live sample shows the result at once;
- **Terminal** — clearing the command history;
- **Tools** — skills, MCP servers, agents and shortcuts.

### Project rules

MemuCode reads the same instruction files as MemuCode CLI: `MEMUCODE.md`, `AGENTS.md` or `CLAUDE.md` in the project (also in `.memucode/` and `.claude/`), in the folders above the project, your personal `MEMUCODE.md`, `*.local.md` files and the files they include with `@file`. The agent gets them at the start of every answer: build and test commands, code style, conventions. The `/memory` command shows which files are in effect, and `/init` studies the project and writes a `MEMUCODE.md`. When the rules disagree with your request, the request wins. Turn it off with the **Project rules** switch in the settings.

## Terminal and sandbox

Project commands — `npm test`, `python -m pytest`, `git status` — run in the project folder inside a **sandbox**: a separate Windows job object with memory (2 GB) and time limits. No secrets reach its environment. **Stop** ends every sandbox process at once.

Dangerous commands — formatting disks, shutting the computer down and the like — never run in any mode.

The **Terminal** section is your console in the same sandbox: ++↑++ / ++↓++ browse the command history. Long processes — a dev server, a build — the agent starts under **Tasks**, without blocking the conversation.

## Changes and undo

- **Changes** — uncommitted changes in the git working tree: unified or split view, copying the diff.
- **Undo** — everything the current session changed. Before the first edit of each file MemuCode keeps a copy, so undo works even without git and without commits. Undo selected files or everything at once.

`/diff` and `/undo` open the same sections.

## Artifacts

Pages, SVG diagrams, notes and data the agent creates in a session are kept apart from the project — in **Artifacts**, with versions. Open them, download them or write them into the project as files — writing counts as an edit and follows the [permissions](#permissions).

## MemuCode commands

| Command | What it does |
| --- | --- |
| `/review` | review the current changes by severity |
| `/test` | find and run the project's tests, triage failures |
| `/fix <description>` | reproduce a defect, find the cause and fix it minimally |
| `/explain <file or function>` | walk through the code |
| `/refactor <what>` | rewrite while keeping behaviour |
| `/plan <task>` | plan the work before editing |
| `/init` | study the project and write `MEMUCODE.md` for agents |
| `/security-review` | check the changes for vulnerabilities, without edits |
| `/commit` | write a commit message from the real diff and commit |
| `/pr` | prepare and open a pull request |
| `/diff` | what the session changed, undo |
| `/undo` | return files to their state before the edits |
| `/compact [topic]` | compress the history: what matters stays, the context is freed; the topic sets the focus |
| `/context` | what fills the model's window |
| `/clear` | start a new session — the old one stays in `/resume` |
| `/resume` | open an earlier session of this project |
| `/rename <name>` | name the current session |
| `/copy [N]` | copy the last answer (`/copy 2` — its second code block) |
| `/agents` | MemuCode agents, their models and this chat's permission |
| `/skills` | skills: your own and from the library |
| `/memory` | the agent's instruction files: `MEMUCODE.md`, `AGENTS.md` and others |
| `/mcp` | connect external tool servers |
| `/access` | choose the permission level |
| `/status` | model, access, project, instructions, MCP |
| `/stats` | statistics of sessions, tokens and activity |

**Your MemuCode CLI commands** — Markdown files in `<project>/.memucode/commands` and in the personal `memucode\commands` folder (and in the Claude Code folders) — work in the window too: `/name arguments` expands the same way as in the terminal (`$ARGUMENTS`, `$1`…). When there is no command with that name, the skill with that name runs. A model override in your own command (`model:`) is not applied in the window yet — the session's model writes the answer.

## Agents

MemuCode has the same agents as MemuCode CLI: built-in ones (`general-purpose`, `explore`, `plan`, `reviewer`, `tester`), personal ones (`memucode\agents` in the MemuAI data folder) and project ones (`<project>/.memucode/agents`). Agents in the Claude Code format (`~/.claude/agents`, `<project>/.claude/agents`) are visible too, read-only. An agent is a Markdown file: the header holds the name, description, tools, model and colour, the body holds the role. A personal or project agent named like a built-in one replaces it — in the window and in the CLI alike.

The **Agents** tab of the right panel (or `/agents`) shows every agent type and lets you create, edit and delete your own, pick the agents' models and their number. The **Agents in this chat without asking** switch lets the AI start agents by itself; otherwise agents start only when you ask for them in a message ("run agents", "in parallel", "in the background"). The main model picks a fitting agent type for each subtask; the running agents and their progress show on the same tab.

In an [agentic workspace](doc:spaces#agentic-workspace) the companion uses exactly these MemuCode agents in MemuCode mode.

## Skills

A skill is a ready instruction for a typical job: review, testing, debugging, refactoring, commits, security review, performance and more. The agent picks the right skill by itself. In the ++Ctrl+P++ palette the ++#++ prefix searches skills only and ++/++ commands only.

Your own skills:

- per project — `<project>/.memuai/skills/<name>/SKILL.md`, which override the built-in ones;
- personal — in the MemuAI data folder, the **Skills** section of the right panel.

## MCP

The MCP button on the bar (or `/mcp`) connects external tool servers over the Model Context Protocol: databases, issue trackers, documentation. Their tools become available to the agent.

## Statistics

The MemuCode home page has a **MemuCode statistics** card; the `/stats` command opens it in a separate panel. Only MemuCode sessions count: sessions and messages, input and output tokens, active days, the busiest hour, the favourite model and a heat map by day. The token volume is compared with books and other measures (pages, time to read aloud) — a button picks another random comparison. The data stays local in the profile's `memucode-stats.json`; when a provider reports no token counts, they are estimated from the text length.

## Saving context

The agent receives the file tree and requests exact line ranges. Repeated reads of unchanged fragments are replaced with short references — a counter on the bar shows how many characters were kept out of the context.

The start of the request does not change from turn to turn: the system instruction, the project context and the rules stay in place, so the provider takes them from its cache instead of processing them again. Older tool rounds of a long session fold into short reports while the latest stay whole; `/compact` compresses the whole history with the same summary as MemuCode CLI, and `/context` shows what fills the model's window.

See also: [Work](doc:work), [Commands](doc:commands), [Context](doc:context).
