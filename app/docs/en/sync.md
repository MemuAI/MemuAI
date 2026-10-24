---
title: Sync
summary: Automatic backups of all your MemuAI data to Google Drive or a folder, with versions, passphrase encryption and restore on this or another PC.
---

# Sync

**Settings → Sync** (in the «You and your data» group) keeps a copy of all your MemuAI data — chats, memory, settings, modules, panels, characters and the rest — in Google Drive or any folder (for example a OneDrive or Dropbox folder, or a USB stick). Backups run on a schedule, when the program closes or on demand, and any saved version can be restored.

![Sync settings](docs-img/en/sync.webp "The Sync page: where to keep backups — Google Drive or a folder — and the Google connection")

The program itself, logs, caches, browser profiles and downloaded models (voices, the wake word, local models) are not included — they are easy to download again.

## Where to store

Pick a **Storage**:

- **Google Drive** — backups live in your Google account. By default they go to the hidden app folder (the `drive.appdata` scope): it is not shown in the Drive interface and other programs can't touch it. To see the backups, choose the visible «MemuAI Backups» folder in **Where in Drive** — MemuAI then gets access only to files it created itself (`drive.file`). MemuAI never sees the rest of your Drive.
- **Folder** — any folder on this PC or the network. A synced cloud folder — for example the «Google Drive» folder of Google Drive for desktop, OneDrive, Dropbox or Yandex Disk — turns it into a cloud backup without any OAuth setup. A folder inside the MemuAI data folder can't be chosen.

**Check access** writes and reads a small test file and tells you right away what is wrong.

## Google Cloud client ID

Google lets programs sign in to Drive only through their own «OAuth client». If your MemuAI build already includes one, just press **Connect**. Otherwise create your own — it is free and takes about five minutes:

1. Open [console.cloud.google.com](https://console.cloud.google.com) and create a project (any name).
2. **APIs & Services → Library**: **Enable** the **Google Drive API**. If you will use the same client in [Integrations](doc:integrations) — Gmail, Calendar, Docs, Sheets, Tasks, Contacts — enable their APIs right away too: [all seven with one button](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com,calendar-json.googleapis.com,drive.googleapis.com,docs.googleapis.com,sheets.googleapis.com,tasks.googleapis.com,people.googleapis.com). Otherwise Google answers "no access" even after signing in.
3. **Consent screen** — menu ☰ → **Google Auth Platform → Branding** ([link](https://console.cloud.google.com/auth/branding)). If it says «Google Auth Platform not configured yet», press **Get started**: App name and support email → **Next** → Audience **External** → **Next** → contact email → **Next** → agree to the User Data Policy → **Continue** → **Create**.
4. **Test users** — **Audience → Test users → Add users** ([link](https://console.cloud.google.com/auth/audience)) → your Gmail → **Save**. While the app is in Testing mode Google asks you to sign in again every 7 days; **Publish app** stops that.
5. **OAuth client** — **Clients → Create client** ([link](https://console.cloud.google.com/auth/clients/create)), Application type **Desktop app**, any name → **Create**. Copy the **Client ID** and **Client secret** right away (or **Download JSON**) — Google shows the secret only when the client is created.
6. Paste them into **Your own OAuth client** and press **Save the key**.

Press **▶ Video** next to «How to get a Client ID» to watch the same steps as a short video guide.

It is the same account and the same key as in **Settings → Integrations → Google**: one Google sign-in for everything — Gmail, Calendar, Drive, backups and picking Drive files. Sign-in happens in your normal browser: MemuAI waits for Google's answer on `127.0.0.1` and asks only for `drive.appdata` and `drive.file`. The token is stored encrypted by Windows and never reaches the logs. **Disconnect** revokes access (for Gmail, Calendar and Drive in Integrations too); backups already saved stay in Drive. If you signed in to Google from Integrations without Drive, press **Connect** here once — Google asks only for the backup permission and keeps the rest.

## Schedule

- **Back up automatically** and **How often** — from 15 minutes to a week; daily backups can run **At what time** you choose.
- **Back up when MemuAI closes** — one last backup on exit. If MemuAI Proxy is running it finishes the job in the background and the window closes at once; otherwise MemuAI waits no longer than the limit set in **Advanced**.
- **Only when something changed** — no new version when nothing changed.

While the MemuAI window is closed, MemuAI Proxy keeps making backups from the tray. Only one backup runs at a time — the other process waits.

## What to back up

Categories are switched on one by one: settings, chats (with generated images and videos), memory, prompts and skills, schedules, the Work list, modules, panels, Quick Ask, characters, companion and voices, Telegram, Proxy, MemuNote (settings, recent files, open tabs and unsaved drafts), other. Temporary files — MemuNote's browser cache and profile, the Explorer «Undo» copies of Quick Ask, downloaded tools — are not copied. **Work history** (MemuCode checkpoints) is off by default — it can be large.

**Keys & tokens** (provider accounts, API keys, integration tokens) can be backed up only with encryption. Without it the secret fields are cut out of the copy, and a restore keeps your current values.

## Versions

Each backup uploads only the files that changed, so frequent backups take almost no space. **Versions to keep** and **Delete backups older than** clean old versions up; a starred backup (**Never delete**) stays forever. Any version can be downloaded with **Download as .zip**.

## Encryption

**Encrypt backups with a passphrase** encrypts the contents: the key is derived from the passphrase with scrypt, and every file is encrypted and protected against tampering (HMAC-SHA256). Without the passphrase not even the file list is visible — only the date and size. The passphrase never goes into the backup itself or the logs; on this PC it is stored encrypted by Windows so automatic backups work without asking. **If you forget it, the backup can't be restored**.

## Restore

In **Restore and the backup list** pick a version and categories. Before writing anything MemuAI saves the current versions of the affected files to a **safety copy** — **Undo the restore** brings everything back (the last 5 are kept). **Remove what the backup didn't have** deletes files created later (for example new modules). Restart MemuAI after restoring.

On a new PC: install MemuAI, choose the same storage (sign in to the same Google account or pick the same folder) — backups from other PCs show up in the list with their names. An encrypted backup needs its passphrase.

## Add files from Google Drive

When Google Drive is connected, the ＋ menu of the message field (**Files, tools and modes**) gets **Add from Google Drive**. It opens a picker:

![The Add from Google Drive window](docs-img/en/drive-picker.webp "Picking files on Google Drive: sections, search, type filters, several files at once and the format for Google files")

- on the left — **My Drive** (folders, breadcrumbs, ++Backspace++ goes one level up), **Recent**, **Shared with me** and **Starred**;
- on top — search across all of Drive (names and text) and a type filter: docs, sheets, slides, PDF, images;
- click a file to select it, several with ++Shift++ or ++Ctrl+A++; a double click attaches a file at once, ++Enter++ attaches the selection.

The chosen files are downloaded to your computer and attached to the message exactly like files from the disk, with the same limits — up to 10 attachments and at most 5 MB per file. Google files are saved in a format the model can read; the format can be changed at the bottom of the window when such files are selected:

| Google file | Default | Other options |
| --- | --- | --- |
| Document | Word (.docx) | PDF, Markdown, text |
| Spreadsheet | Markdown — every sheet as a table | CSV (first sheet), PDF, Excel (.xlsx) as a file |
| Presentation | PDF | text, PowerPoint |
| Drawing | PNG | PDF |

Excel workbooks (.xlsx) from Drive also become Markdown tables — the model cannot read text out of an `.xlsx` file itself. Google Forms, Sites and Maps cannot be downloaded; files whose owner disabled downloading are marked and cannot be selected.

**Permission.** Backups need access only to MemuAI's own folder (`drive.appdata` and `drive.file`). Showing your files needs one more permission — **see your Google Drive files** (`drive.readonly`, read-only). MemuAI asks for it separately and only the first time you open this item: the window explains why, and after **Allow access** Google shows its consent page in your browser — the permissions already given for backups do not change. Google marks an app with your own OAuth client in testing mode as "unverified" — that is normal for a personal client. Only the files you pick are downloaded, and only to this computer; MemuAI never changes or deletes anything on Drive. You can revoke access in your Google account settings or with **Disconnect** in this section.

If **Google Drive** is switched on in **Settings → Integrations → Google**, the picker works at once — it is the same account. If Google is not connected, the menu item offers to open Integrations.

## If something goes wrong

- **«Access denied» when signing in to Google** — add your account to **Test users** (step 4 above).
- **The folder is unavailable** — the backup is postponed and retried later; with **Notify about errors** you get a notification.
- **Wrong passphrase** — the restore changes nothing; try again.

A portable export of chats is described in [Data transfer](doc:transfer); if something does not start after a restore, [safe mode](doc:safe-mode) helps.
