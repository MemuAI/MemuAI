---
title: Work
summary: Working in a project folder: reading and changing files, access modes, Plan and Goal.
---

# Work

**Work** is the mode in which the model works with files on your computer: reads documents, writes code, creates and edits files. Access is limited to one folder — a project you chose yourself.

Open it with the **Work** button in the sidebar or the `/work` command.

## Projects

A project is a saved folder. The ＋ on the Work bar adds a folder, the list at the top switches projects. The active project is always shown on the bar; each project has its own chats.

The model sees only files inside the project folder. Links and shortcuts leading outside it are not followed.

## Access modes

The switch on the Work bar or the `/access` command:

| Mode | How changes work |
| --- | --- |
| **Ask for approval** | every file change is shown beforehand and waits for your confirmation. The default |
| **Full access** | prepared changes are applied at once. It is enabled only after a warning |

In both modes changes are visible in the chat before or after they apply — the model cannot quietly rewrite a file.

**Confirm CLI commands** (Settings → Work & agents) — when the model needs to run a command, it asks. Commands run with your Windows account's rights with the project folder as the working directory, so it is best left on.

## Plan

**Plan** (the bar button, `/plan` or `@Plan`) — before working, the model makes a verifiable plan of steps. Then it carries it out, marking each step with evidence: what was done and how it was checked. The answer is not finished until every step is closed — or until the model honestly reports a blocker.

Separate planning before every new Work request can be turned on.

## Goal

**Goal** (`/goal <goal>` or `@Goal`) — the model keeps working by itself, step by step, until the goal is reached. It stops when:

- the goal is provably reached;
- it needs your help or confirmation;
- the safe turn limit is used up (12 by default).

`/goal off` stops the goal early.

## How the model sees the project

MemuAI does not send the whole project to the model. It builds a bounded file index and passes only relevant fragments; service folders (`node_modules`, `.git`, builds) are excluded. Mention a file as `@path` to point at it directly — see [Commands](doc:commands#project-files).

## Browser and Computer Use

In Work the model may decide by itself that a task needs a browser or control of the computer. Details and safety measures: [Browser and Computer Use](doc:browser-computer).

## Preview

When the model creates a web page, it can be opened in the preview panel on the right: with a screen size choice, reload and opening in an outside browser.

## Work and MemuCode

Work suits documents, texts, small sites and scripts. For repository work — tests, git, a terminal, undoing changes — there is [MemuCode](doc:memucode).

> [!WARNING]
> Work is not a virtual machine. The boundary is the project folder; inside it, a model in Full access can change any file. Keep a copy of important projects or use git.

See also: [Scheduling](doc:schedules), [Context](doc:context).
