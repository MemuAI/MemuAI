---
title: MemuCode
summary: An agent for repositories: file tree, editor, changes, a sandboxed terminal, undo, skills and MCP.
---

# MemuCode

**MemuCode** is the mode for programmers. Unlike [Work](doc:work), it is built around a repository: the model runs tests and builds, looks at git, shows changes line by line, and every edit can be undone.

Open it with the **MemuCode** button in the sidebar or the `/code` command. The same agent also runs in a terminal — [MemuCode CLI](doc:memucode-cli).

## The screen

| Part | What is there |
| --- | --- |
| Top bar | project, access mode, plan, starting and stopping processes, sandbox state |
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

## Access modes

| Mode | Behaviour |
| --- | --- |
| Ask | every file change waits for approval; only read commands work in the terminal |
| Full access | edits and commands run without asking |

Switch on the bar or with `/access`.

## Terminal and sandbox

Project commands — `npm test`, `python -m pytest`, `git status` — run in the project folder inside a **sandbox**: a separate Windows job object with memory (2 GB) and time limits. No secrets reach its environment. **Stop** ends every sandbox process at once.

Dangerous commands — formatting disks, shutting the computer down and the like — never run in any mode.

The **Terminal** section is your console in the same sandbox: ++↑++ / ++↓++ browse the command history. Long processes — a dev server, a build — the agent starts under **Tasks**, without blocking the conversation.

## Changes and undo

- **Changes** — uncommitted changes in the git working tree: unified or split view, copying the diff.
- **Undo** — everything the current session changed. Before the first edit of each file MemuCode keeps a copy, so undo works even without git and without commits. Undo selected files or everything at once.

`/diff` and `/undo` open the same sections.

## Artifacts

Pages, SVG diagrams, notes and data the agent creates in a session are kept apart from the project — in **Artifacts**, with versions. Open them, download them or write them into the project as files (writing needs Full access).

## MemuCode commands

| Command | What it does |
| --- | --- |
| `/review` | review the current changes by severity |
| `/test` | find and run the project's tests, triage failures |
| `/fix <description>` | reproduce a defect, find the cause and fix it minimally |
| `/explain <file or function>` | walk through the code |
| `/refactor <what>` | rewrite while keeping behaviour |
| `/plan <task>` | plan the work before editing |
| `/commit` | write a commit message from the real diff |
| `/diff` | what the session changed |
| `/undo` | return files to their state before the edits |
| `/mcp` | connect external tool servers |

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

See also: [Work](doc:work), [Commands](doc:commands), [Context](doc:context).
