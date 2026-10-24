---
title: Where data is stored
summary: The MemuAI data folder, what lives in which file, backups, privacy and moving data.
---

# Where data is stored

Everything MemuAI knows about you lives on your computer in one folder:

```
%APPDATA%\MemuAI
```

An easy way to open it: ++Win+R++ → paste `%APPDATA%\MemuAI` → ++Enter++.

## What is where

### MemuAI Proxy

| File or folder | Contents |
| --- | --- |
| `config.json` | Proxy settings, client API keys |
| `accounts.json` | provider accounts — cookies and tokens |
| `models.json` | the model catalogue |
| `logs\` | the request log, when keeping it between runs is on |
| `metrics.json` | request statistics |
| `backups\` | automatic backups of important files |
| `local-models\` | [local models](doc:local-models) and the llama.cpp engine |
| `detector-history.json` | [model detector](doc:model-detector) history |

### MemuAI

Inside the `memuai\` folder:

| File or folder | Contents |
| --- | --- |
| `preferences.json` | settings shared by all profiles |
| `profiles.json` | profiles; the PIN is stored as a hash |
| `chats.json` | the default profile's chats |
| `memory.json` | memory |
| `schedules.json`, `schedule_results.json` | scheduled tasks and their results |
| `workspaces.json` | Work and MemuCode projects |
| `prompts.json` | the prompt library |
| `profiles\<id>\` | chats, memory, tasks and projects of the other profiles |
| `themes\` | [custom themes](doc:themes) |
| `modules\` | your modules and edits of built-in ones |
| `integrations.json` | [integration](doc:integrations) keys |
| `voice-models\` | downloaded recognition models and voices |
| `browser-profile\` | the separate Edge profile of the Browser skill |
| `checkpoints\`, `code_artifacts\` | MemuCode undo points and artifacts |
| `startup-error.log` | the cause of a failed start, if there was one |

Downloaded files and chat exports are saved separately — to `Downloads\MemuAI`.

## Reliable writes

Files are written atomically: to a temporary file first, then swapped in. A power cut in the middle of a write never leaves a half-empty file. Important files keep rotating backups, and MemuAI restores the last good version by itself if one gets damaged.

MemuAI and the Proxy may write the same files at once — a chat from Telegram and from the window, for example. Changes are merged and no history is lost.

## Privacy

- Messages go only to the service whose model is selected. [Local models](doc:local-models) send nothing.
- Cookies, tokens and keys are never shown in the interface, written to logs or passed to the AI companion.
- Profile statistics are computed on the computer and never sent anywhere.
- MemuAI has no server of its own: the program window is a local page served by MemuAI itself.

> [!NOTE]
> A [temporary chat](doc:chats) is not saved by MemuAI, but its messages still reach the model's service under that service's rules.

## Removing and resetting

- Uninstalling the program leaves the data folder **untouched**.
- To start from scratch, close MemuAI and the Proxy (tray → Shut down) and rename the `MemuAI` folder — to `MemuAI.old`, for example. A new one is created on the next start. The old one can be brought back if needed.
- To delete only chats — **Settings → Data → Danger zone → Delete the history of all chats**.

## Moving to another computer

Copy the whole `%APPDATA%\MemuAI` folder while the programs are closed. More: [Moving data](doc:transfer).

See also: [Profiles](doc:profiles), [Troubleshooting](doc:troubleshooting).
