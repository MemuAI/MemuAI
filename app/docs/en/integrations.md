---
title: Integrations
summary: Telegram Use, the Chrome extension, Google, Notion and GitHub — setup, Telegram commands, proxies, permissions, browser pages and scheduled actions.
---

# Integrations

**Settings → Integrations**. Connect a service once — after that the assistant finds an email, checks your schedule, reads a Drive document or a Notion page, opens a repository — in the chat, in Work and in Quick Ask (Alt+Space).

## Telegram Use — your personal account

Telegram Use connects **your personal account**, separately from the MemuAI Telegram bot. Once connected, ask in a normal chat: "Find John Smith and summarize our last 50 messages." The same tools are available in MemuCode CLI; keep the MemuAI window running. If several contacts share a name, identify the correct person before proceeding.

1. Open **Telegram Use** and enable it. Obtain **API ID and API hash** at [my.telegram.org/apps](https://my.telegram.org/apps) and enter both. Settings save automatically; wait for **All changes saved**. Both values are also required for QR login; API ID alone is insufficient.
2. Press **Show QR**. On your phone, open Telegram → **Settings → Devices → Link Desktop Device** and scan the code. Alternatively, expand **Sign in with a phone number instead**, enter your number with its country code and the Telegram login code. Enter your 2FA cloud password if requested. MemuAI saves your completed settings before starting sign-in.
3. Wait for the account to show as connected. API hash and the session use the protected Windows store; login codes and the 2FA password are not saved. Leaving API hash empty when editing settings keeps the saved value.

The assistant can find chats, search and read messages, reply, forward, edit **your own** messages, delete, react and send files. Writes have a separate setting and follow the normal chat approval policy. The CLI's file-editing `acceptEdits` mode does not automatically allow sending Telegram messages.

Before sending or editing, the confirmation shows Telegram formatting: line breaks, emphasis, quotes, code and links. Long messages show every part; file captions show the actual shortened text. Check the recipient and content, then confirm. Preparing the preview does not send anything.

History supplies explicit author information: your own messages, the other person, group participant or channel author. This helps summaries distinguish the participants correctly.

Tool reference: `telegram_use_dialogs` finds a chat, `telegram_use_history` reads history, `telegram_use_search` searches messages, `telegram_use_media` describes/transcribes media, and `telegram_use_send` sends a message.

### If my.telegram.org refuses to create an application

“Too many attempts. Please try again later.” is a Telegram website refusal before MemuAI connects. Stop repeated submissions and check for an existing application for this number: Telegram permits one API ID per number, which can be reused. Otherwise retry later or contact [Telegram support](https://telegram.org/support); the website does not specify a reliable waiting period. QR login still requires both API ID and hash. MemuAI's Telegram proxy affects the Telegram Use connection, not the website in your browser.

If you use a VPN for the website, try disabling it when signing in or creating an application: a shared VPN address can trigger restrictions. This cannot remove a Telegram server rate limit. **Generating a QR code** needs a connection to Telegram itself: if a direct connection is blocked in Russia, enable a working VPN or configure a Telegram proxy in MemuAI. The website and Telegram sign-in may require different network settings.

### Commands in Telegram messages

Enable **Process Telegram commands automatically** separately. Commands use MemuAI's general assistant, main instructions and personality. Telegram Use has its own additional instruction. With web access enabled, the assistant can search for current information and read web pages; automatic commands do not receive desktop modification tools.

| Command | Action |
| --- | --- |
| `!//question <question>` | Answer using conversation context and available web search |
| `!//summary <count> [chat]` | Summarize the requested number of recent messages; count is required, from 1 to 1000 |
| `!//voice` | Transcribe the voice message **you replied to** with the command |

`!//summary 50` reads the current conversation. `!//summary 50 AI Telegram News` reads another chat or channel you can access, then delivers the result in the conversation where you sent the command, or in MemuAI according to its delivery setting. A chat title may contain spaces without quotes. Decorative characters and small spelling differences are considered: for example, a plain title can match `AI T@l@gram News`. If several chats are close matches, specify the numeric ID; a random chat is never selected. Oversized context is explicitly marked as an excerpt. Other allowed users can only summarize the current conversation.

Each command has an instruction, text model, context and delivery setting: replace your own command, reply in Telegram or save a new MemuAI chat. Basic formatting, links, quotes and code work without Premium; Premium-only custom emoji are not sent. Invalid arguments in your own command produce a usage hint while keeping the original message.

The editor supports custom commands with **ordered arguments**: text, chat title, integer, number, yes/no or a choice. Argument names use Latin letters, digits and `_`. Set required fields, defaults, bounds and hints, then reference them with `{argument_name}` in the instruction. `{question}` contains the original text after the command. The last text or chat argument may consume the remaining line, including spaces. A Summarize conversation action requires an integer `count` argument and can include a `chat` argument of type Chat title.

Allowed users and chats use numeric IDs; cooldowns and hourly/daily limits are configurable. An empty other-user list permits only you. In a group another person needs **both** their user ID and the group chat ID to be allowed. Other people's messages are replied to, never edited.

### Speech recognition and transcript cleanup

`!//voice` **needs no chat model** when cleanup is off. In Telegram Use, open **Speech recognition and transcript cleanup**. Choose the model from **Voice & Live**, **Quick Access** settings, a separate local model or the Audio API configured in Voice settings. Select a local model and speech language, including automatic detection. Download local models in Voice & Live; the Audio API address and credentials are reused from there.

**Clean up transcripts after recognition** is a separate switch. Choose a text model and cleanup instruction; an empty instruction corrects punctuation, speech slips and paragraphs while preserving meaning. If cleanup fails, the original transcript is kept. All changes, including commands and speech settings, save automatically; incomplete fields remain in the form with an explanation. **Save now** lets you retry after a save error.

### Telegram Use proxies and limits

Under **Configure a proxy**, paste a URL such as `tg://proxy?server=HOST&port=PORT&secret=SECRET` or `socks5://USER:PASS@HOST:PORT` and wait for autosave. `https://t.me/proxy`, `tg://socks`, `socks4://` and `http://` are also supported. Standard MTProxy secrets and `dd`-prefixed secrets work; **`ee` / Fake-TLS does not**. An empty field keeps the current proxy; use the separate removal checkbox to delete it.

Only cloud chats accessible to your account are available; **Telegram secret chats are unavailable**. A vision model describes images; video is described from up to six sampled frames rather than every moment. After signing in, check that the intended chat can be read: access depends on your account, network and proxy.

## Chrome · MemuAI — browser extension

1. In **Settings → Integrations → Chrome · MemuAI**, press **Download extension** and extract the ZIP to a permanent folder.
2. Open `chrome://extensions` in Chrome, enable **Developer mode**, press **Load unpacked** and select the extracted folder. No store installation is required.
3. In extension options, select **Connect through MemuAI**. A `memuai://` link opens the installed app; approve the connection in MemuAI. The address and one-time code transfer automatically. If the link is unavailable, expand **Connect with a code or change the address**: obtain a code in MemuAI's Chrome integration settings, enter the displayed local address and code, then select **Connect with a code**. Keep MemuAI running.
4. Press **Allow site access** in extension options if you need page context. The extension button opens the side panel: choose a model and chat or start a new one. Page context is added when you choose it; ordinary questions work without a page.

Controls for page reading, actions on tabs (**Ask / Allow / Deny**), **DFY in my Chrome**, schedules and history are available **inside extension options** and synchronize with MemuAI. The browser's site-access permission is separate and is granted by Chrome itself. Within those permissions, the assistant reads pages and their elements, opens/closes tabs, types and captures screenshots. A normal screenshot requires the tab to be active. Chrome's internal pages, the extension store and some protected pages are restricted by the browser.

[Quick Ask](doc:quick-ask) prefers the paired extension in Chrome; without it or in another browser, the existing reading method remains available. [DFY](doc:dfy) requires a separate opt-in and creates its own background tabs with your site sessions without switching your tab. Chrome displays its standard debugger notice. Disconnecting the extension stops its tasks. To revoke pairing fully, press **Revoke extension access** in MemuAI.

Browser tools: `chrome_tabs` lists tabs, `chrome_read` reads page text and elements, `chrome_screenshot` captures a tab, `chrome_open` / `chrome_navigate` / `chrome_close` manage tabs, `chrome_close_tabs` closes a selected group of tabs, and `chrome_action` acts on the page. For example, “Close all GitHub tabs” can run as one batch: Ask mode shows one approval with tab titles and URLs. Only tabs included in the approved list are affected.

**Download extension** saves the ZIP to **Downloads / MemuAI**, shows its path and offers **Show in folder**; **Save as…** is also available. Click the selected model for search. The default model and pins are shared with the active MemuAI profile and synchronize in both directions. Change an existing conversation's model in the panel.

**New chats stay in a separate browser history by default**, without appearing in MemuAI's main list. They are saved locally, rather than being unsaved or incognito conversations. History has **In browser / MemuAI / All** filters. **Show new chats in MemuAI** changes where subsequent conversations are saved; existing chats stay in their current location. Project chats can be read with a link to continue in the app.

**Page tools** can summarize the current page, explain it in plain language, translate it into Russian or extract facts and actions. Page reading and tool calls appear as individual steps with their progress and result. Stop generation or copy answers/code from the panel. Attach images, text-based PDFs, DOCX, text or source code with the paperclip, drag/drop or paste: up to 5 MB each, 8 files/4 images per message. Long documents are explicitly marked as excerpts.

**Panel on the right:** extension options link to browser appearance settings; select **Side panel → Right**. Chrome owns this preference and offers no extension API to change it. After updating, extract the ZIP to the same permanent folder and press **Reload** on chrome://extensions to preserve the extension ID and connection.

## Telegram Use and Chrome in schedules

Save a [scheduled task](doc:schedules) using `telegram_use_*` or `chrome_*`, for example to read messages regularly or work with tabs. For writes, enable Telegram Use's scheduled-write permission or Chrome's **Scheduled actions without prompts** in advance; browser DFY also requires **DFY in my Chrome**. Saved tasks do not wait for approval dialogs. Control denial, disabled integrations and access restrictions still apply. Keep MemuAI running and, for Chrome, the paired browser open. The Telegram proxy belongs to Telegram Use; Chrome uses its own browser/system network settings.

## How the assistant uses them

The model only knows it may connect to a service when a task needs it. "What did Anna write this week?" becomes calls to `gmail_search` and `gmail_read`; "open my memuai repository and read the README" becomes `github_list_repos` and `github_read_file`.

**Writes** — sending an email, a draft, labels and the trash, a calendar event, a Drive file, a Notion page, a commit, a pull request or a new GitHub repository — are first shown to you as a card ("Send this email?", "Delete the file forever?") with the recipient, subject, path, time and text, and run only after you press the button. Destructive actions get a red button, and Enter means "Cancel". **Always, even with Full access**, MemuAI confirms what cannot be undone: sending, replying to and forwarding an email, deleting emails and Drive files forever (skipping the trash), deleting a repository or a calendar. In Quick Ask the assistant only reads; for a write it suggests the main chat.

Keys and tokens stay on this computer; Google and Notion tokens are kept in the protected Windows store and never shown in the interface.

## Video guides

Every connection guide has a **▶ Video guide** button — a half-minute silent clip with Russian and English captions (the **CC** button turns them off; the steps under the video are clickable). The steps on the Google, GitHub and Notion sites need a sign-in, so the clips show them as cards with the sites' exact button names, while the MemuAI steps are the real interface:

- **Google** — the «own OAuth client» wizard (project, APIs, Google Auth Platform, test users, a Desktop app client); the same clip is in **Sync** next to «How to get a Client ID».
- **GitHub** — in the «How to get the token and which rights it needs» help.
- **Notion** — under the «Integration token» steps.

## Google: Gmail, Calendar, Drive, Docs, Sheets, Tasks, Contacts

One Google account for everything: the same sign-in serves [backups](doc:sync) and "Files from Google Drive" in the chat.

1. Open **Google** in the list of services.
2. Tick what MemuAI may use: **Gmail**, **Google Calendar**, **Google Drive**, **Google Docs**, **Google Sheets**, **Google Tasks**, **Google Contacts**.
3. Press **Sign in with Google**. Your browser opens Google's page — sign in, **tick every box** and press **Allow**. Come back to MemuAI: the sign-in is picked up by itself, and MemuAI immediately checks that the APIs are enabled in your Google Cloud project.

Google asks permission **only for the ticked services**. A service switched on later is one tick: Google asks about it alone, everything granted before stays. An unticked service simply takes its tools away from the assistant.

| Service | What is granted | What the assistant can do |
| --- | --- | --- |
| Gmail | full access to mail and filters | search (`from:`, `subject:`, `newer_than:7d`, `is:unread`, `has:attachment`), read emails and threads, reply (and reply all), forward with attachments, attach files from this computer and save attachments, drafts, labels, archive, read/unread, star, trash and restore, delete forever, bulk actions by a query, filters, auto-reply |
| Calendar | calendars and events | the schedule for any period, events with guests, a Google Meet link, repeats and reminders, moving and editing, replying to invitations, free/busy, your own calendars |
| Drive | all Drive files | search by name and text, read (Docs and Sheets as text), upload from this computer, folders, rename, move, copy, trash and restore, delete forever, sharing by email and by link, download and export to PDF, DOCX, XLSX and other formats |
| Docs | Google Docs | read, create documents, append and replace text |
| Sheets | Google Sheets | read and write cells (formulas work), append rows, new spreadsheets and sheets, clear |
| Tasks | Google Tasks | lists, new tasks with a due date, edit, mark done, delete |
| Contacts | Google Contacts | search, create and edit contacts, delete |

**Sign out** revokes the access at Google and forgets the token — backups and the Drive file picker are disconnected with it.

### "Basic access" and "Expand access"

If you signed in to Google in an earlier MemuAI version, Gmail and Drive show **basic access**: reading and sending work, while deleting, labels, renaming and so on need a wider permission. Press **Expand access** — Google asks once more, everything granted before stays. Until then the assistant says exactly what to press and sends nothing to Google in vain.

### "Check APIs" and the "no access" error

For Google to answer, every service must be **enabled in your Google Cloud project** — a separate step that signing in does not replace. When an API is off, the assistant and the service row say which one and give a direct **Enable** link — press **Enable** on Google's page and **Check APIs** a minute later. **Enable them all at once** opens the enabling of every disabled API together.

### If you need your own OAuth client

Google only lets apps in through a registered "OAuth client". If your MemuAI build includes one, sign-in is one button. If not, the Google section shows a step-by-step wizard with direct links (about 5 minutes, once):

1. **New project** in the [Google Cloud Console](https://console.cloud.google.com/projectcreate) — any name.
2. **Enable all seven APIs** — the "Enable all APIs" button or one by one: [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com), [Google Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com), [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com), [Google Docs API](https://console.cloud.google.com/apis/library/docs.googleapis.com), [Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com), [Google Tasks API](https://console.cloud.google.com/apis/library/tasks.googleapis.com), [People API](https://console.cloud.google.com/apis/library/people.googleapis.com) (contacts). Check that your project is selected at the top.
3. **Consent screen** ([Google Auth Platform → Branding](https://console.cloud.google.com/auth/branding)): **Get started** → app name and your email → Audience: **External** → contact email → **Create**. No need to add scopes under Data Access — MemuAI requests them itself.
4. **Test users** ([Audience](https://console.cloud.google.com/auth/audience)): **Test users → Add users** → your Gmail. In Testing mode Google asks you to sign in again every 7 days; to stop that, press **Publish app**. For a personal client Google shows "Google hasn't verified this app" — that is fine: **Advanced → Go to … (unsafe)**.
5. **Create client** ([Clients → Create client](https://console.cloud.google.com/auth/clients/create)): Application type **Desktop app** → Create. Copy the **Client ID** and **Client secret** or press **Download JSON** — Google shows the secret only once.
6. Paste them into the wizard (or **Load JSON**), **Save**, then **Sign in with Google**.

The wizard puts your project number (it starts the Client ID) into the links, so once the client is saved the links open exactly your project.

**The "Use this client for an AI-powered agent" checkbox** on the create-client form is optional — leave it either way. According to [Google's help](https://support.google.com/cloud/answer/15549257) it is only a self-declaration that an AI agent uses the client: it grants no extra access and matters for Google's MCP servers. MemuAI calls Google's regular APIs, so everything works the same with or without it.

The sign-in follows the standard for desktop apps: MemuAI waits for Google's answer on `127.0.0.1` (a random port) with PKCE protection; the password is typed only on Google's page.

| Tools | What they do |
| --- | --- |
| `gmail_search`, `gmail_read`, `gmail_read_thread` | search, emails and threads |
| `gmail_send`, `gmail_reply`, `gmail_forward` | send, reply, forward — always confirmed |
| `gmail_create_draft`, `gmail_update_draft`, `gmail_list_drafts`, `gmail_delete_draft` | drafts |
| `gmail_archive`, `gmail_mark_read`, `gmail_star`, `gmail_modify` | archive, read status, star, labels |
| `gmail_trash`, `gmail_untrash`, `gmail_delete`, `gmail_bulk` | trash, restore, delete forever (always confirmed), bulk actions |
| `gmail_list_labels`, `gmail_create_label`, `gmail_update_label`, `gmail_delete_label` | labels |
| `gmail_list_filters`, `gmail_create_filter`, `gmail_delete_filter`, `gmail_vacation`, `gmail_save_attachment` | filters, auto-reply, saving attachments |
| `calendar_list_calendars`, `calendar_list_events`, `calendar_get_event`, `calendar_freebusy` | calendars, events, free/busy |
| `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, `calendar_quick_add` | events (guests, Meet, repeats, reminders) |
| `calendar_respond`, `calendar_move_event`, `calendar_create_calendar`, `calendar_delete_calendar` | reply to an invitation, move to another calendar, your own calendars |
| `drive_list_files`, `drive_read_file`, `drive_get_file`, `drive_storage` | search, read, details, Drive storage |
| `drive_create_file`, `drive_upload_file`, `drive_update_file`, `drive_create_folder` | new files and folders, upload (up to 25 MB), overwrite |
| `drive_rename`, `drive_move`, `drive_copy`, `drive_trash`, `drive_untrash`, `drive_delete_file` | rename, move, copy, trash, delete forever |
| `drive_share`, `drive_list_permissions`, `drive_unshare`, `drive_download` | sharing, download and export |
| `gdocs_read`, `gdocs_create`, `gdocs_append`, `gdocs_replace` | Google Docs |
| `gsheets_read`, `gsheets_write`, `gsheets_append`, `gsheets_clear`, `gsheets_create`, `gsheets_add_sheet`, `gsheets_delete_sheet` | Google Sheets |
| `gtasks_lists`, `gtasks_list`, `gtasks_create`, `gtasks_update`, `gtasks_complete`, `gtasks_delete`, `gtasks_create_list` | Google Tasks |
| `gcontacts_search`, `gcontacts_list`, `gcontacts_get`, `gcontacts_create`, `gcontacts_update`, `gcontacts_delete` | Google Contacts |

## Notion

Two ways:

- **Integration token** — the simplest. In the Notion Developer portal (**[app.notion.com/developers](https://app.notion.com/developers)**, or in Notion **Settings → Connections → Go to developer portal**) open **Internal connections → Create a new connection**, give it a name and pick the workspace. On the **Configuration** tab copy the **API token** (it starts with `ntn_`, `secret_` for old integrations) and paste it into the field. Then share pages with it: the **Content access → Edit access** tab, or on a page itself **"•••" → Connections → + Add connection →** your connection. Sharing a parent page shares everything inside it.
- **Sign in with Notion** — the button appears when the build includes a Notion OAuth client or you set your own (a public integration, section "Your own Notion OAuth client"; its Redirect URI is `http://127.0.0.1:47652/notion/callback`). Your browser opens and you pick the pages for MemuAI.

| Tool | What it does |
| --- | --- |
| `notion_search` | search pages and databases |
| `notion_read_page` | page text (headings, lists, to-dos, code) and properties |
| `notion_query_database` | database rows |
| `notion_create_page` | a new page from Markdown — confirmed |
| `notion_append` | add to a page — confirmed |
| `notion_get_database` | database properties and their options — to fill a row correctly |
| `notion_update_page` | title, properties (as plain values: `"Status": "Done"`, `"Due": "2026-10-16"`), icon |
| `notion_delete_page`, `notion_restore_page` | to Notion's trash and back |
| `notion_replace_content`, `notion_delete_block` | replace a page's content, delete a block |
| `notion_comment` | a comment on a page |

If the assistant says a page "was not found", it is not shared: add the connection under the page's **Connections** (or under **Content access** in the Developer portal).

## GitHub

Repositories, files, branches, pull requests, issues, releases, gists and Actions — through a personal access token. Either kind works.

**Classic — the simplest:** github.com → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**. Tick the scopes `repo`, `workflow`, `gist`, `delete_repo` (for organisation repositories also `read:org`) and copy the token (`ghp_…`).

**Fine-grained — more precise:** same place → **Fine-grained tokens** → **Generate new token**. Repository access — **All repositories** (otherwise the assistant cannot create new repositories) or selected ones. Repository permissions — **Read and write**: Administration (create, delete, rename, visibility, collaborators), Contents, Issues, Pull requests, Actions, Workflows; Metadata — Read. Account permissions: **Gists** — Read and write. Copy the token (`github_pat_…`).

Paste the token into **Personal Access Token** and press **Save** — MemuAI checks it and shows the token kind and the missing scopes. When GitHub refuses an action, the assistant names the right it needs (GitHub reports it in the answer).

**Default repository** (optional) — e.g. `me/notes`: then you can leave it out of requests. Deleting a repository and adding or removing collaborators always need an explicitly named repository.

| Tools | What they do |
| --- | --- |
| `github_status`, `github_list_repos`, `github_repo_info`, `github_search_repos` | token, repositories, search |
| `github_create_repo`, `github_update_repo`, `github_fork_repo`, `github_delete_repo` | create (also from a template and in an organisation), rename, visibility and settings, fork, delete (always confirmed) |
| `github_list_files`, `github_read_file`, `github_search_code` | files and code search |
| `github_write_file`, `github_commit_files`, `github_move_file`, `github_delete_file` | write a file, one commit of many files, move/rename a file or folder, delete a file or folder |
| `github_list_branches`, `github_create_branch`, `github_delete_branch`, `github_list_commits`, `github_get_commit`, `github_compare`, `github_list_tags` | branches, commits, comparison, tags |
| `github_list_pulls`, `github_get_pull`, `github_create_pull`, `github_update_pull`, `github_merge_pull`, `github_review_pull`, `github_request_reviewers` | pull requests: create, change, merge, review |
| `github_list_issues`, `github_get_issue`, `github_create_issue`, `github_update_issue`, `github_comment`, `github_list_comments`, `github_search_issues` | issues and comments (close/reopen with `github_update_issue`) |
| `github_list_labels`, `github_create_label`, `github_add_labels`, `github_remove_label`, `github_add_assignees` | labels and assignees |
| `github_list_releases`, `github_create_release`, `github_delete_release` | releases (release notes generated automatically) |
| `github_list_gists`, `github_read_gist`, `github_create_gist`, `github_update_gist`, `github_delete_gist` | gists |
| `github_list_workflows`, `github_list_runs`, `github_get_run`, `github_job_logs`, `github_rerun`, `github_cancel_run`, `github_dispatch_workflow` | Actions: runs, logs, re-run, cancel, manual run |
| `github_list_collaborators`, `github_add_collaborator`, `github_remove_collaborator` | repository collaborators |

## For builders: built-in one-click sign-in

The owner of a build can bake in their OAuth client: set `MEMUAI_GOOGLE_CLIENT_ID`, `MEMUAI_GOOGLE_CLIENT_SECRET` (a Desktop app client), `MEMUAI_NOTION_CLIENT_ID`, `MEMUAI_NOTION_CLIENT_SECRET` (a public integration) before `build.py` — or put them into `oauth_clients.local.json` next to `build.py`. Users then need no wizard. A user's own client always wins over the built-in one.

## Disconnecting

**Sign out** for Google and **Disconnect** for Notion revoke access and delete the tokens from the computer. For GitHub **Disconnect** deletes the token; revoke the token itself on GitHub if you no longer need it.

## Integrations and memes

A [meme](doc:agents) can be denied integrations — then Google, Notion and GitHub tools are unavailable in its chats.

See also: [Sync](doc:sync), [Notifications and remote access](doc:notifications).
