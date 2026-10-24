---
title: Troubleshooting
summary: Common MemuAI and Proxy problems — symptoms, causes and what to do.
---

# Troubleshooting

First, two universal steps:

1. **Look at the log.** Proxy → **Logs**: every request shows the cause of its error. **Ask AI** next to an error opens the [AI companion](doc:proxy-companion), which explains it in plain words.
2. **Restart services.** Proxy → **Home → Restart services** rebuilds the connections without closing the program. It cures most "hangs" after a laptop wakes up or the network changes.

## MemuAI does not answer

| Symptom | Cause | What to do |
| --- | --- | --- |
| "MemuAI Proxy is not running" at the bottom of the sidebar | the Proxy is off or failed to start | press "Start" next to the status or open MemuAI Proxy from the Start menu |
| The model list is empty | no accounts or every model is disabled | add an account — see [Accounts](doc:accounts) |
| The answer takes long to start, then fails | the provider is silent | check the log; the Proxy moves to another account after the first-byte wait |
| The answer is cut in the middle | the stream idle timeout ran out or the service dropped the connection | retry; if it happens often, raise the "Stream idle timeout" in the Proxy settings |
| "Model not found" | the model is disabled in the catalogue or removed by the provider | refresh the catalogue: Proxy → Model catalog (left panel) → Refresh from servers |

## An account stopped working

| Code in the log | Means | What to do |
| --- | --- | --- |
| 401, 403 | the cookie or token no longer works | sign in to the service again and refresh the cookie in the account card |
| 403 mentioning captcha | the service wants a browser check | open the site in a browser, pass the check, refresh the cookie |
| 429 | the quota is used up | wait or add a second account — the Proxy switches by itself |
| 502 with an unclear error from one provider | the service changed its protocol | check for a MemuAI update; unofficial protocols sometimes break and the fix ships in a new version |

Details about each service: [Accounts and providers](doc:accounts).

## Images

- **The model "does not see" the picture.** The model lacks vision or the service silently drops attachments. Choose **Private vision bridge** in **Settings → Images & video → Sending images** — see [Models](doc:models).
- **Generation returned an error about a check.** The service wants a captcha — pass it on the site and refresh the cookie.
- **A file does not download.** Downloads go to `Downloads\MemuAI`; "Show in folder" opens it.

## Voice

- **The microphone button is inactive** — the chosen recognition model is not downloaded yet or the API address is missing. Open **Settings → Voice & Live**.
- **Windows withholds the microphone** — Windows Settings → Privacy → Microphone → allow desktop apps.
- **Speech is silent** — check that Windows has a voice for the language, or choose another voice source.

## Work and MemuCode

- **The model does not see a file** — it is outside the project folder or in a service folder (`node_modules`, `.git`, builds). Mention it as `@path`.
- **Changes are not applied** — Ask mode waits for your confirmation: confirm the change under the answer or switch to Full access.
- **A terminal command was refused** — Ask mode allows read commands only; dangerous commands are always forbidden.
- **Files need restoring** — MemuCode → right panel → **Undo**.

## Local models

See "Errors" on the [Local models](doc:local-models#errors) page.

## The program does not start

- **A window with a path to `startup-error.log`** — open that file in `%APPDATA%\MemuAI\memuai\`: it holds the cause.
- **A white window** — reinstall "Microsoft Edge WebView2 Runtime".
- **Port 8317 is busy** — change the Proxy port in **Settings → Advanced → Server and API**.
- **Settings "vanished" after a crash** — MemuAI restores damaged files from backups automatically; if not, the copies are in `%APPDATA%\MemuAI\backups`.

## Interface

- **Something looks odd** — ++Ctrl+R++ reloads the interface without losing data.
- **Unreadable colours after a custom theme** — pick a built-in variant in **Settings → Appearance**.
- **The MemuAI taskbar icon turned into a white page** — the causes are fixed in 3.5, and MemuAI repairs shortcuts that point at a lost icon file by itself at start. If Windows still shows an old cached icon after an update, unpin MemuAI from the taskbar and pin it again (or restart Explorer in Task Manager).
- **A module opens empty** — open its workbench: code errors are shown there. See [Modules: troubleshooting](doc:modules-troubleshooting).

## If nothing helps

Collect details for a report: the error text from the Proxy log (the record's copy button), the **Server log** tab and, for a failed start, `startup-error.log`. Cookies and keys never reach these logs, so they are safe to share.
