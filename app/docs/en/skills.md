---
title: AI skills
summary: Ready-made playbooks for the model — built-in, your own and from the catalog. One library for MemuAI, MemuFiles, MemuNote, MemuCode, MemuCode CLI and modules; for each skill you choose where it works.
---

# AI skills

A skill is a playbook for a typical job: code review, debugging, working through a document, writing, home-screen panels. The model sees one short line per enabled skill and loads the full text by itself when a task fits, so a skill costs almost no tokens until it is needed.

The skills window opens with **AI skills** in the sidebar, from the composer's "+" menu or with ++Ctrl+5++.

## Window tabs

| Tab | What is there |
| --- | --- |
| Built-in | skills that ship with MemuAI: turn them off, edit or remove them — the menu item "Restore removed built-ins" brings removed ones back |
| Mine | your skills: **New skill**, "Import from a file" (`SKILL.md`, `.zip`, `.json`) and "Import from a link" (GitHub or `.md`) |
| Catalog | skills from open repositories: read the text before you add a skill |

The window menu also has "Enable all", "Disable all" and "Export my skills" — your skills as one `.zip` in the Agent Skills format.

## One library for every app

MemuFiles, MemuNote, MemuCode, MemuCode CLI and modules take skills from the same library as the MemuAI chat. Turn a skill off in the window and it is off everywhere; edit its text and the new version works in every app at once. MemuAI Proxy uses no skills: it only forwards requests to models.

The coding agent also has skills of its own (the "MemuCode skills" menu item) — see [MemuCode](doc:memucode#skills).

## Where a skill works

A skill's page has a **Where it works** block with a switch for every place. By default:

| Place | Which skills work |
| --- | --- |
| MemuAI chat | all |
| MemuFiles (AI assistant) and MemuNote | all but "Panels" and "MemuAI Guide" |
| MemuCode and MemuCode CLI | the same minus four that MemuCode has its own version of: "Code Review", "Debugging & Root Cause", "Security Review", "Commits & Changelogs" |
| Modules MemuMeet, MemuLearn, MemuAI Studio and MemuAI Design | as in MemuFiles |
| Other modules | none: switch a skill on for a module by hand |

"Panels" and "MemuAI Guide" work only in the MemuAI chat: their playbooks call tools that exist only there (creating panels, reading the documentation). Instead of switches they show a lock and the reason.

A place you chose by hand is marked with a dot, and **Defaults** brings back the original set. For example, to keep a coding skill to MemuCode and MemuCode CLI, switch off the MemuAI chat, MemuFiles and MemuNote for it.

## Every skill for one place

The **Where skills work** menu item lists the apps and modules with a "Skills working here" counter and **All** and **None** buttons — one click removes, say, every skill from MemuCode CLI. The modules listed are the ones that call models; a module that is off in the modules menu is marked.
