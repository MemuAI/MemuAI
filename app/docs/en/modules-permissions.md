---
title: Module permissions
summary: What each permission unlocks and why the sandbox exists.
---

# Module permissions

Permissions are listed in `permissions`. When the list is absent the module gets all of them except `net-import`, `music`, `learn` and `database`, so packages created before permissions existed keep working.

| Permission | Unlocks | Methods |
| --- | --- | --- |
| `chat` | model requests | `request`, `stream`, `history` |
| `media` | media generation | `image`, `video`, `speak`, `transcribe`, `record`, `music` |
| `storage` | memory between runs | `save` |
| `models` | the model list | `models` |
| `files` | document parsing | `extract` |
| `download` | writing to Downloads | `download`, `handoff` |
| `notify` | notifications | `notify` |
| `external` | opening in the system | `open` |
| `web` | network through MemuAI | `search`, `fetch` |
| `background` | background jobs in Proxy | `monitor` |
| `clipboard` | the clipboard | `copy` |
| `chat` | a full message array | `complete` |
| `storage` | a large file store | `store.*` |
| `net-import` | downloading a character card or a lorebook by link through MemuAI (declared explicitly) | `fetchImport` |
| `music` | the MemuMusic library: tracks, playlists, downloading by link, lyrics, the player (declared explicitly) | `music.call` |
| `learn` | MemuLearn courses, cards and reviews (declared explicitly) | `learn.call` |
| `database` | user-selected SQLite databases through the protected host (declared explicitly) | `database.call` |

No permission is needed for `routes`, `env`, `resize`, `log`, `close` and `host` — those are the environment.

## Import by link (`net-import`)

The only way a module gets anything from the internet by address. It is never part of the implicit «all permissions»: a module has to list `net-import` itself. What it may download is narrow — a character card or a lorebook from the sources of the `net_import` manifest block (chub, a chub lorebook, JanitorAI, Pygmalion, AICharacterCards, RisuRealm, Perchance and direct links to allowed hosts), see [Import by link](doc:modules-bridge-app).

- MemuAI downloads, not the module: public HTTPS addresses on port 443 only, every DNS answer and the connected address checked (no localhost, LAN, link-local), every redirect re-checked, size limits and timeouts.
- Before the first download from a site MemuAI asks the person: **Only this time**, **Always for this site** or **Don't allow**. «Always» is remembered for this module.
- Settings → Modules → ⋯ → **Import sources** shows which sites no longer ask and the allow list of hosts for direct links (like `whitelistImportDomains` in SillyTavern). Only the person changes it; a module cannot.

## The music library (`music`)

Round 37. `Memu.music.call(op, args)` → a Promise with the result; the same operations as the built-in MemuMusic module
(`library.list`, `library.get`, `library.like`, `playlists.list`, `playlist.create`, `download.start`, `download.jobs`, `search.online`,
`lyrics.get`, `player.state`, `player.command` and more).
`Memu.music(body)` still means music generation (permission `media`).

- Never part of the implicit «all permissions»: a module lists `music` itself.
- Every track carries tokenised `stream` and `cover` URLs: put them straight into `<audio src>` and `<img src>` — for a module with this
  permission the document's security policy allows exactly these MemuAI addresses (`/api/pg/music/`), the rest of the network stays closed.
- Audio is served with seeking (HTTP Range). MemuAI downloads and searches (yt-dlp); the module never goes online itself.

## Courses and cards (`learn`)

Round 40. `Memu.learn.call(op, args)` → a Promise with the result; the operations match the built-in MemuLearn module
(`overview`, `course.create`, `course.get`, `node.get`, `card.list`, `card.add`, `review.queue`, `review.answer`, `review.undo`, `stats`,
`exam.list`, `ai.prompt`, `ai.apply`, `source.ingest`, `import.apkg`, `export.apkg`, `import.csv`, `export.csv`, `panel` and more).

- Not part of the implicit "all permissions": the module declares `learn` itself.
- MemuAI computes the FSRS schedule on this computer; course data is shared by the MemuLearn module, the Review today panel and the Mini App Trainer tab.
- Model requests (summaries, cards, exams) are made by the module itself through `Memu.request`, which needs the `chat` permission.

## Databases (`database`)

A module explicitly declares `database` and calls `Memu.database.call(op, args)`.
The host opens SQLite databases selected by the user; normal opening is read-only.
Changes are previewed on a copy, require confirmation in the host interface, and
are then applied atomically with a backup. A module cannot impersonate user
confirmation. AI tools create SQL proposals; they do not apply changes silently.

## What happens without a permission

The call rejects with a clear error: "The module is not allowed…". The error is visible inside the module, so the author sees the reason at once.

## Least privilege

Ask for what you need. A calculator module needs `storage`; a module that lists models needs `models`; a translator needs `chat`, `storage`, `models`, `files`, `download`.

## Why the sandbox

A module runs in a separate document with `allow-scripts` and no access to the MemuAI page:

- it cannot see chats, memory, the profile or settings;
- it cannot read another DOM or eavesdrop on events;
- it does not reach the network directly;
- it cannot close or modify MemuAI.

The only channel is messages with source verification. Only data the host itself built travels back.

> Permissions limit the blast radius of an accident, not trust in the author: a foreign module should not be able to read your conversations or delete your files.
