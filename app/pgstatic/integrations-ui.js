

(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {}
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const kit = () => window.MemuSettingsKit;

  const BRAND = {
    telegram: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#229ED9"/><path fill="#fff" d="M5 11.5 18.5 6.2 15.7 18.5 11.2 15.2 8.8 17.4 9.1 13.4 15.7 8.8 7.8 12.8Z"/></svg>',
    google: '<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M21.6 12.23c0-.68-.06-1.34-.17-1.97H12v3.73h5.4a4.6 4.6 0 0 1-2 3.03v2.5h3.23c1.9-1.74 2.97-4.3 2.97-7.29z"/><path fill="#34A853" d="M12 22c2.7 0 4.96-.9 6.62-2.43l-3.23-2.5c-.9.6-2.04.96-3.39.96-2.6 0-4.81-1.76-5.6-4.12H3.07v2.58A10 10 0 0 0 12 22z"/><path fill="#FBBC05" d="M6.4 13.91a6 6 0 0 1 0-3.82V7.51H3.07a10 10 0 0 0 0 8.98z"/><path fill="#EA4335" d="M12 5.97c1.47 0 2.79.5 3.83 1.5l2.86-2.87A9.6 9.6 0 0 0 12 2a10 10 0 0 0-8.93 5.51L6.4 10.1C7.19 7.73 9.4 5.97 12 5.97z"/></svg>',
    gmail: '<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M3.5 19h3V11.2L2 7.9V17.5c0 .83.67 1.5 1.5 1.5z"/><path fill="#34A853" d="M17.5 19h3c.83 0 1.5-.67 1.5-1.5V7.9l-4.5 3.3z"/><path fill="#FBBC05" d="M17.5 5.6v5.6L22 7.9V6.1c0-1.66-1.9-2.6-3.2-1.6z"/><path fill="#EA4335" d="M6.5 11.2V5.6L12 9.7l5.5-4.1v5.6L12 15.3z"/><path fill="#C5221F" d="M2 6.1v1.8l4.5 3.3V5.6L5.2 4.5C3.9 3.5 2 4.44 2 6.1z"/></svg>',
    calendar: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3" fill="#fff"/><path fill="#4285F4" d="M3 6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v2H3z"/><path fill="#1967D2" d="M17 21h1a3 3 0 0 0 3-3v-1h-4z"/><path fill="#EA4335" d="M17 21v-4h4z" opacity=".9"/><rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="#4285F4" stroke-width="1.2"/><text x="12" y="17.6" text-anchor="middle" font-family="Segoe UI, Arial, sans-serif" font-weight="700" font-size="8.2" fill="#1967D2">31</text></svg>',
    drive: '<svg viewBox="0 0 24 24"><path fill="#0066DA" d="M3.4 18.6 4.5 20.5c.2.4.6.6 1 .7l3.6-6.2H1.9c0 .4.1.8.3 1.2z"/><path fill="#00AC47" d="M12 8.6 8.4 2.4c-.4.2-.7.5-.9.9L1.9 13.3c-.2.4-.3.8-.3 1.2h7.2z"/><path fill="#EA4335" d="M18.5 21.2c.4-.2.8-.5 1-.9l.4-.7 2-3.4c.2-.4.3-.8.3-1.2h-7.2l1.5 3z"/><path fill="#00832D" d="M12 8.6 15.6 2.4c-.4-.2-.8-.3-1.2-.3H9.6c-.4 0-.8.1-1.2.3z"/><path fill="#2684FC" d="M14.9 14.9H9.1l-3.6 6.3c.4.2.8.3 1.2.3h10.6c.4 0 .8-.1 1.2-.3z"/><path fill="#FFBA00" d="m18.4 9.1-3.3-5.8c-.2-.4-.6-.7-1-.9L12 8.6l3.6 6.3h7.2c0-.4-.1-.8-.3-1.2z"/></svg>',
    docs: '<svg viewBox="0 0 24 24"><path fill="#4285F4" d="M14.5 2H6.2C5.5 2 5 2.6 5 3.3v17.4c0 .7.5 1.3 1.2 1.3h11.6c.7 0 1.2-.6 1.2-1.3V6.5z"/><path fill="#A1C2FA" d="M14.5 2v3.3c0 .7.5 1.2 1.2 1.2H19z"/><path fill="#fff" d="M8 11h8v1.3H8zm0 2.6h8v1.3H8zm0 2.6h5.4v1.3H8z"/></svg>',
    sheets: '<svg viewBox="0 0 24 24"><path fill="#0F9D58" d="M14.5 2H6.2C5.5 2 5 2.6 5 3.3v17.4c0 .7.5 1.3 1.2 1.3h11.6c.7 0 1.2-.6 1.2-1.3V6.5z"/><path fill="#87CEAC" d="M14.5 2v3.3c0 .7.5 1.2 1.2 1.2H19z"/><path fill="#fff" d="M8 10.5v7h8v-7zm1.2 1.2h2.2v1.7H9.2zm3.4 0h2.2v1.7h-2.2zm-3.4 2.9h2.2v1.7H9.2zm3.4 0h2.2v1.7h-2.2z"/></svg>',
    tasks: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5" fill="#1A73E8"/><path fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" d="m7.8 12.3 2.9 2.9 5.6-5.8"/><circle cx="17.6" cy="17.6" r="3" fill="#FBBC04"/></svg>',
    contacts: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5" fill="#1A73E8"/><circle cx="12" cy="9.4" r="3.1" fill="#fff"/><path fill="#fff" d="M6.6 17.2c1-2.2 3-3.3 5.4-3.3s4.4 1.1 5.4 3.3a7.6 7.6 0 0 1-10.8 0z"/></svg>',
    notion: '<svg viewBox="0 0 24 24"><rect x="3" y="2.5" width="18" height="19" rx="3.2" fill="#fff" stroke="#111" stroke-width="1.5"/><path fill="#111" d="M8 7.2h2.2l4.3 6.6V7.2h1.8v9.6h-2.1L9.8 10.2v6.6H8z"/></svg>',
    github: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.6 9.6 0 0 1 12 6.84c.85 0 1.71.11 2.51.34 1.91-1.3 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2z"/></svg>',
  };
  const ICON = {
    link: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/></svg>',
    pulse: '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 5 12 2.5-6h4"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.3v.2"/></svg>',
    key: '<svg viewBox="0 0 24 24"><circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16 7l2.5 2.5M14 9l2 2"/></svg>',
    spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/></svg>',
  };
  const brand = (name, cls = "") => `<span class="gi-brand ${cls}" aria-hidden="true">${BRAND[name] || ""}</span>`;
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${ICON[name] || ""}</span>`;

  const SERVICES = [
    { id: "gmail", title: "Gmail", ru: "Поиск, чтение, ответы и пересылка, метки, архив, корзина и удаление, фильтры — отправка и удаление только после вашего «Да»", en: "Search, read, reply and forward, labels, archive, trash and delete, filters — sending and deleting only after your «Yes»" },
    { id: "calendar", title: "Google Calendar", ru: "Расписание, события с гостями и Meet, повторы, ответы на приглашения", en: "Your schedule, events with guests and Meet, repeats, replies to invitations" },
    { id: "drive", title: "Google Drive", ru: "Поиск и чтение, загрузка, папки, переименование, перенос, корзина, доступ, экспорт", en: "Search and read, upload, folders, rename, move, trash, sharing, export" },
    { id: "docs", title: "Google Docs", ru: "Чтение документов, новые документы, дописывание и замена текста", en: "Read documents, create them, append and replace text" },
    { id: "sheets", title: "Google Sheets", ru: "Чтение и запись ячеек, новые таблицы и листы", en: "Read and write cells, new spreadsheets and sheets" },
    { id: "tasks", title: "Google Tasks", ru: "Списки задач: новые, сроки, отметка «выполнено»", en: "Task lists: new tasks, due dates, «done»" },
    { id: "contacts", title: "Google Contacts", ru: "Поиск, новые и изменённые контакты", en: "Search, create and edit contacts" },
  ];

  const WRITE_TOOLS = new Set(["calendar_create_calendar", "calendar_create_event", "calendar_delete_calendar", "calendar_delete_event",
    "calendar_move_event", "calendar_quick_add", "calendar_respond", "calendar_update_event", "drive_copy", "drive_create_file",
    "drive_create_folder", "drive_delete_file", "drive_download", "drive_move", "drive_rename", "drive_share", "drive_trash",
    "drive_unshare", "drive_untrash", "drive_update_file", "drive_upload_file", "gcontacts_create", "gcontacts_delete",
    "gcontacts_update", "gdocs_append", "gdocs_create", "gdocs_replace", "github_add_assignees", "github_add_collaborator",
    "github_add_labels", "github_cancel_run", "github_comment", "github_commit_files", "github_create_branch", "github_create_gist",
    "github_create_issue", "github_create_label", "github_create_pull", "github_create_release", "github_create_repo",
    "github_delete_branch", "github_delete_file", "github_delete_gist", "github_delete_release", "github_delete_repo",
    "github_dispatch_workflow", "github_fork_repo", "github_merge_pull", "github_move_file", "github_remove_collaborator",
    "github_remove_label", "github_request_reviewers", "github_rerun", "github_review_pull", "github_update_gist",
    "github_update_issue", "github_update_pull", "github_update_repo", "github_write_file", "gmail_archive", "gmail_bulk",
    "gmail_create_draft", "gmail_create_filter", "gmail_create_label", "gmail_delete", "gmail_delete_draft", "gmail_delete_filter",
    "gmail_delete_label", "gmail_forward", "gmail_mark_read", "gmail_modify", "gmail_reply", "gmail_save_attachment", "gmail_send",
    "gmail_star", "gmail_trash", "gmail_untrash", "gmail_update_draft", "gmail_update_label", "gmail_vacation", "gsheets_add_sheet",
    "gsheets_append", "gsheets_clear", "gsheets_create", "gsheets_delete_sheet", "gsheets_write", "gtasks_complete", "gtasks_create",
    "gtasks_create_list", "gtasks_delete", "gtasks_update", "notion_append", "notion_comment", "notion_create_page",
    "notion_delete_block", "notion_delete_page", "notion_replace_content", "notion_restore_page", "notion_update_page"]);
  const ALWAYS_TOOLS = new Set(["gmail_send", "gmail_reply", "gmail_forward", "gmail_delete", "github_delete_repo",
    "drive_delete_file", "calendar_delete_calendar"]);
  const DANGER_TOOLS = new Set(["calendar_delete_calendar", "calendar_delete_event", "drive_delete_file", "drive_trash", "drive_unshare",
    "drive_update_file", "gcontacts_delete", "gdocs_replace", "github_delete_branch", "github_delete_file", "github_delete_gist",
    "github_delete_release", "github_delete_repo", "github_merge_pull", "github_remove_collaborator", "gmail_bulk", "gmail_delete",
    "gmail_delete_filter", "gmail_delete_label", "gmail_trash", "gsheets_clear", "gsheets_delete_sheet", "gtasks_delete",
    "notion_delete_block", "notion_delete_page", "notion_replace_content"]);

  let google = null;
  let notion = null;
  let pick = new Set(["gmail", "calendar", "drive"]);
  let check = null;
  let pollTimer = 0;
  let busy = "";

  async function call(path, body) {
    const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error((isEn() ? data.error_en : data.error_ru) || data.error || `HTTP ${response.status}`);
    return data;
  }
  const say = (text, tone = "ok") => { try { if (typeof toast === "function") toast(text, tone); } catch (e) {} };

  function stateChip(kind, text) {
    return `<span class="int-state ${kind}">${esc(text)}</span>`;
  }
  function googleValue() {
    if (!google) return stateChip("off", "—");
    if (!google.connected) return stateChip("off", L("Не подключено", "Not connected"));
    const ready = google.services.filter((s) => s.ready).length;
    if (!ready) return stateChip("warn", L("Нет сервисов", "No services"));
    return stateChip("on", (google.account.email || L("Подключено", "Connected")) + " · " + ready);
  }
  function notionValue() {
    if (!notion) return stateChip("off", "—");
    if (!notion.connected) return stateChip("off", L("Не подключено", "Not connected"));
    return stateChip(notion.last_error ? "err" : "on", notion.account || L("Подключено", "Connected"));
  }

  function listHtml() {
    const K = kit();
    return K.link({ sub: "int-google", icon: brand("google"), label: "Google", id: "gi-row-google",
      desc: L("Gmail, Календарь, Диск, Документы, Таблицы, Задачи и Контакты — один вход для всего, и для резервных копий тоже", "Gmail, Calendar, Drive, Docs, Sheets, Tasks and Contacts — one sign-in for everything, backups included"),
      value: `<span id="gi-google-value">${googleValue()}</span>`, keywords: "google gmail почта mail calendar календарь drive диск docs документы sheets таблицы tasks задачи contacts контакты oauth api войти sign in account аккаунт" })
      + K.link({ sub: "int-notion", icon: brand("notion"), label: "Notion", id: "gi-row-notion",
        desc: L("Поиск, чтение страниц и баз, новые страницы и заметки", "Search, read pages and databases, create pages and notes"),
        value: `<span id="gi-notion-value">${notionValue()}</span>`, keywords: "notion ноушн notes заметки pages страницы database база token токен" });
  }

  function subsHtml() {
    const K = kit();
    return K.sub({ id: "int-google", title: "Google", desc: L("Один аккаунт Google для Gmail, Календаря, Диска, Документов, Таблиц, Задач, Контактов и резервных копий. Вход — в вашем браузере, пароль MemuAI не видит.", "One Google account for Gmail, Calendar, Drive, Docs, Sheets, Tasks, Contacts and backups. You sign in in your browser — MemuAI never sees the password."),
      body: `<div class="gi-root" id="gi-google-root">${googleBody()}</div>` })
      + K.sub({ id: "int-notion", title: "Notion", desc: L("Ассистент ищет, читает и дополняет страницы, к которым вы дали доступ.", "The assistant searches, reads and extends the pages you share with it."),
        body: `<div class="gi-root" id="gi-notion-root">${notionBody()}</div>` });
  }

  function googleBody() {
    const K = kit();
    if (!google) return `<div class="gi-loading">${L("Проверяю подключение…", "Checking the connection…")}</div>`;
    const flow = google.flow || {};
    const waiting = flow.state === "waiting";
    const client = google.client || {};
    const parts = [];
    if (google.connected) {
      const a = google.account || {};
      parts.push(`<div class="gi-account">
        <span class="gi-avatar">${esc(((a.name || a.email || "G").trim()[0] || "G").toUpperCase())}</span>
        <div class="gi-account-text"><b class="gi-title">${esc(a.email || L("Аккаунт Google", "Google account"))}</b>
          <small>${esc(a.name ? a.name + " · " : "")}${esc(L("вход выполнен", "signed in"))}</small></div>
        <button type="button" class="pbtn ghost sx-btn-sm" data-gi="google-disconnect">${esc(L("Выйти", "Sign out"))}</button>
      </div>`);
    } else {
      parts.push(`<div class="gi-hero">
        ${brand("google", "big")}
        <div class="gi-hero-text"><b class="gi-title">${esc(L("Войдите в Google", "Sign in to Google"))}</b>
          <p>${esc(L("Отметьте, к чему у MemuAI будет доступ. Google спросит разрешение только на эти сервисы — остальные можно добавить позже.", "Tick what MemuAI may use. Google asks permission for these services only — you can add the rest later."))}</p></div>
      </div>`);
    }

    if (!client.has_client) parts.push(clientWizard(client));
    if (waiting) parts.push(waitBlock(flow, "google"));
    else if (flow.state === "error" && flow.purpose === "integrations") parts.push(K.note(esc(isEn() ? flow.error_en : flow.error_ru), "danger"));

    const rows = SERVICES.map((svc) => serviceRow(svc, waiting, client)).join("");
    parts.push(`<div class="sx-card gi-card">${rows}</div>`);
    if (google.connected) parts.push(checkBlock());
    if (!google.connected) {
      const none = !pick.size;
      parts.push(`<div class="gi-cta">
        <button type="button" class="pbtn gi-google-btn" data-gi="google-connect" ${waiting || none || !client.has_client ? "disabled" : ""}>${brand("google", "btn")}<span>${esc(L("Войти через Google", "Sign in with Google"))}</span></button>
        <small>${esc(!client.has_client ? L("Сначала добавьте OAuth-клиент — мастер выше (5 минут, один раз).", "Add an OAuth client first — the wizard above (5 minutes, once).")
          : none ? L("Отметьте хотя бы один сервис.", "Tick at least one service.") : L("Откроется ваш браузер. Пароль вводится на странице Google.", "Your browser opens. The password is typed on Google's page."))}</small>
      </div>`);
    }
    parts.push(`<div class="gi-facts">
      <span>${ico("shield")}${esc(L("Письма уходят, а файлы и события меняются только после вашего подтверждения; отправка и удаление навсегда — всегда", "Mail is sent and files and events change only after you confirm; sending and deleting forever — always"))}</span>
      <span>${ico("check")}${esc(L("Токен хранится в защищённом хранилище Windows на этом компьютере", "The token is kept in the protected Windows store on this computer"))}</span>
      ${google.connected && !google.backups ? "" : `<span>${ico("check")}${esc(L("Тот же аккаунт — у резервных копий и «Файлов с Google Диска»", "The same account serves backups and «Files from Google Drive»"))}</span>`}
    </div>`);
    if (client.has_client) parts.push(clientWizard(client));
    return parts.join("");
  }

  function serviceRow(svc, waiting, client) {
    const K = kit();
    const row = (google.services || []).find((s) => s.id === svc.id) || {};
    const res = (check?.services || []).find((c) => c.id === svc.id);
    const on = google.connected ? !!row.enabled : pick.has(svc.id);
    const off = waiting || !client.has_client;
    let tag = "";
    let action = "";
    if (google.connected && row.enabled && !row.granted) {
      tag = `<span class="gi-pill warn">${esc(L("нужно разрешение", "needs permission"))}</span>`;
      action = `<button type="button" class="pbtn ghost sx-btn-sm" data-gi="google-grant" data-svc="${svc.id}" ${off ? "disabled" : ""}>${esc(L("Разрешить", "Allow"))}</button>`;
    } else if (google.connected && row.ready && res && res.state === "disabled") {
      tag = `<span class="gi-pill err">${esc(L("API выключен", "API is off"))}</span>`;
      action = `<a class="pbtn sx-btn-sm" href="${esc(res.enable_url || row.api_url || "#")}" target="_blank" rel="noopener" data-gi-enable="${svc.id}">${ico("link")} ${esc(L("Включить", "Enable"))} ${esc(row.api_title || "API")}</a>`;
    } else if (google.connected && row.ready && !row.full) {
      tag = `<span class="gi-pill warn" title="${esc(L("Google выдал только базовые права (старый вход). Удаление, метки, переименование и т. п. попросят расширить доступ.", "Google granted only the basic rights (an older sign-in). Deleting, labels, renaming etc. will ask to expand access."))}">${esc(L("базовый доступ", "basic access"))}</span>`;
      action = `<button type="button" class="pbtn ghost sx-btn-sm" data-gi="google-grant" data-svc="${svc.id}" ${off ? "disabled" : ""}>${esc(L("Расширить доступ", "Expand access"))}</button>`;
    } else if (google.connected && row.ready && res && res.state && res.state !== "ok" && res.state !== "off") {
      tag = `<span class="gi-pill err" title="${esc(res.error || "")}">${esc(L("ошибка", "error"))}</span>`;
    } else if (google.connected && row.ready) {
      tag = `<span class="gi-pill on">${esc(L("работает", "on"))}</span>`;
    }
    const toggle = google.connected && row.enabled && !row.granted ? ""
      : K.toggle("gi-svc-" + svc.id, on, `data-gi-svc="${svc.id}" ${waiting || (google.connected && !client.has_client) ? "disabled" : ""} aria-label="${esc(svc.title)}"`);
    const error = google.connected && row.ready && res && res.state !== "ok" && res.state !== "off" && res.error
      ? `<div class="gi-svc-error">${esc(res.state === "disabled" ? L(`${row.api_title || "API"} не включён в вашем проекте Google Cloud — нажмите «Включить», затем Enable на странице Google и через минуту «Проверить API».`, `${row.api_title || "The API"} is not enabled in your Google Cloud project — press «Enable», then Enable on Google's page and «Check APIs» a minute later.`) : res.error)}</div>` : "";
    return `<div class="sx-row gi-svc" data-gi-row="${svc.id}">
        ${brand(svc.id)}
        <div class="sx-row-text"><div class="sx-label tl"><span class="gi-svc-title">${esc(svc.title)}</span>${tag}</div><div class="sx-desc td">${esc(isEn() ? svc.en : svc.ru)}</div>${error}</div>
        <div class="sx-control gi-svc-control">${action}${toggle}</div>
      </div>`;
  }

  function checkBlock() {
    const ready = (google.services || []).filter((s) => s.ready);
    if (!ready.length) return "";
    let summary = L("Проверьте, что API этих сервисов включены в вашем проекте Google Cloud — без этого Google ответит «нет доступа».", "Check that the APIs of these services are enabled in your Google Cloud project — otherwise Google answers «no access».");
    let tone = "";
    let extra = "";
    if (busy === "google-check") {
      summary = L("Проверяю API…", "Checking the APIs…");
    } else if (check) {
      const bad = check.services.filter((c) => c.state === "disabled");
      const broken = check.services.filter((c) => c.state && !["ok", "off", "disabled"].includes(c.state));
      if (bad.length) {
        tone = "err";
        summary = L(`Выключено в проекте: ${bad.map((c) => c.api_title).join(", ")}.`, `Disabled in the project: ${bad.map((c) => c.api_title).join(", ")}.`);
        if (check.enable_all_url) extra = `<a class="pbtn sx-btn-sm" href="${esc(check.enable_all_url)}" target="_blank" rel="noopener">${ico("link")} ${esc(bad.length > 1 ? L("Включить все одной кнопкой", "Enable them all at once") : L("Включить", "Enable"))}</a>`;
      } else if (broken.length) {
        tone = "warn";
        summary = L(`Есть ошибки: ${broken.map((c) => c.api_title).join(", ")} — подробности в строке сервиса.`, `Errors: ${broken.map((c) => c.api_title).join(", ")} — details in the service row.`);
      } else {
        tone = "ok";
        summary = L("Все включённые сервисы отвечают.", "Every enabled service answers.");
      }
    }
    return `<div class="gi-check ${tone}" id="gi-check">
      <span class="gi-check-ico" aria-hidden="true">${ICON[tone === "ok" ? "check" : tone ? "warn" : "pulse"]}</span>
      <p>${esc(summary)}</p>
      <div class="gi-check-actions">${extra}<button type="button" class="pbtn ghost sx-btn-sm" data-gi="google-check" ${busy === "google-check" ? "disabled" : ""}>${ico("pulse")} ${esc(L("Проверить API", "Check APIs"))}</button></div>
    </div>`;
  }

  function waitBlock(flow, kind) {
    return `<div class="gi-wait"><span class="gi-spin" aria-hidden="true"></span><div>
      <b>${esc(kind === "google" ? L("Войдите в Google в открывшемся браузере", "Sign in to Google in the browser that opened") : L("Разрешите доступ в открывшемся браузере", "Allow access in the browser that opened"))}</b>
      <p>${esc(L("После «Разрешить» вернитесь сюда — MemuAI подхватит вход сам.", "After «Allow», come back here — MemuAI picks it up by itself."))}</p>
      <div class="sx-actions"><a class="pbtn ghost sx-btn-sm" href="${esc(flow.auth_url || "#")}" target="_blank" rel="noopener">${ico("link")} ${esc(L("Открыть страницу ещё раз", "Open the page again"))}</a>
        <button type="button" class="pbtn ghost sx-btn-sm" data-gi="${kind}-cancel">${esc(L("Отмена", "Cancel"))}</button></div></div></div>`;
  }

  const APIS = [
    ["gmail.googleapis.com", "Gmail API"], ["calendar-json.googleapis.com", "Google Calendar API"],
    ["drive.googleapis.com", "Google Drive API"], ["docs.googleapis.com", "Google Docs API"],
    ["sheets.googleapis.com", "Google Sheets API"], ["tasks.googleapis.com", "Google Tasks API"],
    ["people.googleapis.com", "People API"],
  ];
  const GC = "https://console.cloud.google.com";
  function consoleUrl(path, project) { return GC + path + (project ? (path.includes("?") ? "&" : "?") + "project=" + encodeURIComponent(project) : ""); }
  function step(n, title, text, href, label, extra = "") {
    return `<li class="gi-step"><span class="gi-step-n">${n}</span><div><b>${esc(title)}</b><p>${text}</p>${extra}${href ? `<a class="pbtn ghost sx-btn-sm" href="${esc(href)}" target="_blank" rel="noopener">${ico("link")} ${esc(label)}</a>` : ""}</div></li>`;
  }
  function apiChips(project) {
    return `<div class="gi-api-chips">${APIS.map(([id, title]) => `<a class="gi-api-chip" href="${esc(consoleUrl("/apis/library/" + id, project))}" target="_blank" rel="noopener" title="${esc(id)}">${esc(title)}</a>`).join("")}</div>`;
  }
  function clientWizard(client) {
    const K = kit();
    const project = client.project || "";
    const enableAll = consoleUrl("/flows/enableapi?apiid=" + APIS.map((a) => a[0]).join(","), project);
    const b = (text) => `<b>${esc(text)}</b>`;
    const steps = `<ol class="gi-steps">
      ${step(1, L("Создайте проект Google Cloud", "Create a Google Cloud project"), esc(L("Любое имя, например «MemuAI». Нужен только ваш аккаунт Google, карта не нужна.", "Any name, e.g. «MemuAI». Only your Google account is needed, no card.")), GC + "/projectcreate", L("Новый проект", "New project"))}
      ${step(2, L("Включите API — все семь", "Enable the APIs — all seven"), esc(L("Без этого Google отвечает «нет доступа» даже после входа. Кнопка ниже включает все сразу (проверьте, что сверху выбран ваш проект → Next → Enable). Или по одному:", "Without this Google answers «no access» even after sign-in. The button below enables them all (check that your project is selected at the top → Next → Enable). Or one by one:")), enableAll, L("Включить все API", "Enable all APIs"), apiChips(project))}
      ${step(3, L("Экран согласия (Google Auth Platform)", "Consent screen (Google Auth Platform)"), L(`${b("Get started")} → App name (например, MemuAI) и ваша почта → Audience: ${b("External")} → контактная почта → согласие с правилами → ${b("Create")}. Области доступа (Data Access) добавлять не нужно — MemuAI запросит их сам при входе.`, `${b("Get started")} → App name (e.g. MemuAI) and your email → Audience: ${b("External")} → contact email → agree → ${b("Create")}. No need to add scopes under Data Access — MemuAI requests them itself at sign-in.`), consoleUrl("/auth/branding", project), L("Открыть экран согласия", "Open the consent screen"))}
      ${step(4, L("Добавьте себя в тестовые пользователи", "Add yourself as a test user"), L(`${b("Audience")} → ${b("Test users")} → ${b("Add users")} → ваш Gmail → Save. В режиме Testing Google просит входить заново раз в 7 дней; чтобы не просил — ${b("Publish app")} (In production). Для личного клиента это нормально: Google покажет «Приложение не проверено» → ${b("Дополнительные настройки")} → ${b("Перейти на страницу…")}.`, `${b("Audience")} → ${b("Test users")} → ${b("Add users")} → your Gmail → Save. In Testing mode Google asks you to sign in again every 7 days; to stop that press ${b("Publish app")} (In production). That is fine for a personal client: Google shows «Google hasn't verified this app» → ${b("Advanced")} → ${b("Go to … (unsafe)")}.`), consoleUrl("/auth/audience", project), L("Открыть Audience", "Open Audience"))}
      ${step(5, L("Создайте OAuth-клиент «Desktop app»", "Create a «Desktop app» OAuth client"), L(`${b("Create client")} → Application type: ${b("Desktop app")} → любое имя → ${b("Create")}. Скопируйте Client ID и Client secret или нажмите ${b("Download JSON")} — секрет Google показывает только один раз.`, `${b("Create client")} → Application type: ${b("Desktop app")} → any name → ${b("Create")}. Copy the Client ID and Client secret or press ${b("Download JSON")} — Google shows the secret only once.`), consoleUrl("/auth/clients/create", project), L("Создать клиент", "Create client"),
        `<div class="gi-agent-note">${ico("spark")}<div><b>${esc(L("Галочка «Use this client for an AI-powered agent»", "The «Use this client for an AI-powered agent» checkbox"))}</b><p>${esc(L("Необязательна — можно оставить как есть. Это только пометка для Google, что клиентом пользуется ИИ-агент: прав она не добавляет и на работу MemuAI не влияет (MemuAI обращается к обычным API Google, а не к MCP-серверам Google, которым такая пометка нужна). Отметили — всё работает так же.", "Optional — leave it either way. It only tells Google that an AI agent uses the client: it grants no extra access and does not change how MemuAI works (MemuAI calls Google's regular APIs, not Google's MCP servers, which are the ones that need the flag). Ticked — everything works the same."))}</p></div></div>`)}
      ${step(6, L("Вставьте их сюда", "Paste them here"), esc(L("Client ID и secret — в поля ниже (или «Загрузить JSON»), «Сохранить», затем «Войти через Google» и отметьте на странице Google все галочки.", "The Client ID and secret go into the fields below (or «Load JSON»), «Save», then «Sign in with Google» and tick every box on Google's page.")), "", "")}
    </ol>`;
    const fields = `<div class="gi-client-fields">
      ${K.field({ label: "Client ID", forId: "gi-client-id", control: K.input("gi-client-id", client.own_client_id || "", { mono: true, placeholder: "123456789-abc.apps.googleusercontent.com" }) })}
      ${K.field({ label: "Client secret", forId: "gi-client-secret", help: client.own_has_secret ? L("Сохранён в защищённом хранилище. Введите новый, чтобы заменить.", "Stored in the protected store. Type a new one to replace it.") : "",
        control: K.secret("gi-client-secret", "", { placeholder: client.own_has_secret ? "••••••••" : "GOCSPX-…" }) })}
      ${K.actions(`<label class="pbtn ghost sx-btn-sm gi-json-btn">${ico("key")} ${esc(L("Загрузить JSON", "Load JSON"))}<input type="file" accept=".json,application/json" id="gi-client-json" hidden></label>${client.own_client_id ? `<button type="button" class="pbtn ghost sx-btn-sm danger" data-gi="google-client-clear">${esc(L("Убрать свой клиент", "Remove my client"))}</button>` : ""}<button type="button" class="pbtn sx-btn-sm" data-gi="google-client-save">${esc(L("Сохранить", "Save"))}</button>`)}
    </div>`;
    const body = `<p class="gi-muted">${esc(client.bundled
      ? L("В эту сборку MemuAI уже встроен OAuth-клиент — свой нужен, только если вы хотите использовать собственный проект Google Cloud.", "This MemuAI build already includes an OAuth client — you need your own only to use your own Google Cloud project.")
      : L("Google пускает приложения только через зарегистрированный OAuth-клиент. Создайте свой — это бесплатно и делается один раз (около 5 минут).", "Google only lets apps in through a registered OAuth client. Create your own — it is free and done once (about 5 minutes)."))}${project ? " " + esc(L(`Ссылки ниже открывают ваш проект ${project}.`, `The links below open your project ${project}.`)) : ""}</p>${window.MemuGuideVideo ? `<div class="mgv-row">${MemuGuideVideo.button("google-client", { lang: isEn() ? "en" : "ru" })}</div>` : ""}${steps}${fields}`;
    const needed = !client.has_client;
    return K.section({ id: "gi-client-section", card: false, body: K.more({ id: "gi-client-more", open: needed,
      title: needed ? L("Нужен OAuth-клиент Google — пошаговый мастер", "A Google OAuth client is needed — step-by-step wizard") : L("Свой OAuth-клиент и настройка Google Cloud", "Your own OAuth client and Google Cloud setup"),
      hint: needed ? L("5 минут, один раз", "5 minutes, once") : (client.source === "user" ? L("используется ваш", "yours is in use") : L("необязательно", "optional")), body: `<div class="gi-more-pad">${body}</div>` }) });
  }

  function notionBody() {
    const K = kit();
    if (!notion) return `<div class="gi-loading">${L("Проверяю подключение…", "Checking the connection…")}</div>`;
    const flow = notion.flow || {};
    const oauth = notion.oauth || {};
    const parts = [];
    if (notion.connected) {
      parts.push(`<div class="gi-account">
        ${notion.workspace_icon ? `<img class="gi-avatar img" src="${esc(notion.workspace_icon)}" alt="">` : brand("notion", "avatar")}
        <div class="gi-account-text"><b class="gi-title">${esc(notion.account || "Notion")}</b>
          <small>${esc(notion.mode === "oauth" ? L("вход через Notion", "signed in with Notion") : L("токен интеграции", "integration token"))}</small></div>
        <button type="button" class="pbtn ghost sx-btn-sm" data-gi="notion-test">${esc(L("Проверить", "Test"))}</button>
        <button type="button" class="pbtn ghost sx-btn-sm" data-gi="notion-disconnect">${esc(L("Отключить", "Disconnect"))}</button>
      </div>`);
      if (notion.last_error) parts.push(K.note(esc(notion.last_error), "danger"));
      parts.push(K.note(esc(L("Ассистент видит только страницы, к которым вы дали доступ: в Notion откройте страницу → «•••» → Connections → + Add connection (или вкладка Content access подключения в портале разработчика). Доступ к родительской странице открывает и все вложенные.", "The assistant sees only the pages you share: in Notion open a page → «•••» → Connections → + Add connection (or the connection's Content access tab in the Developer portal). Sharing a parent page shares everything inside it.")), "info"));
    } else {
      parts.push(`<div class="gi-hero">${brand("notion", "big")}
        <div class="gi-hero-text"><b class="gi-title">${esc(L("Подключите Notion", "Connect Notion"))}</b>
          <p>${esc(L("Поиск, чтение страниц и баз, новые страницы и дополнения — создание и запись только после вашего подтверждения.", "Search, read pages and databases, create and extend pages — writes only after you confirm."))}</p></div></div>`);
      if (flow.state === "waiting") parts.push(waitBlock(flow, "notion"));
      else if (flow.state === "error") parts.push(K.note(esc(isEn() ? flow.error_en : flow.error_ru), "danger"));
      if (oauth.available) {
        parts.push(`<div class="gi-cta"><button type="button" class="pbtn gi-notion-btn" data-gi="notion-oauth" ${flow.state === "waiting" ? "disabled" : ""}>${brand("notion", "btn")}<span>${esc(L("Войти через Notion", "Sign in with Notion"))}</span></button>
          <small>${esc(L("Откроется браузер: выберите страницы, к которым MemuAI получит доступ.", "Your browser opens: pick the pages MemuAI may access."))}</small></div>`);
      }
      parts.push(K.section({ title: oauth.available ? L("Или токен интеграции", "Or an integration token") : L("Токен интеграции", "Integration token"), body:
        `<ol class="gi-steps compact">
          ${step(1, L("Создайте внутреннее подключение", "Create an internal connection"), esc(L("Портал разработчика Notion → Internal connections → Create a new connection → имя (например, MemuAI) и рабочее пространство → Create.", "Notion Developer portal → Internal connections → Create a new connection → a name (e.g. MemuAI) and the workspace → Create.")), "https://app.notion.com/developers", L("Открыть портал разработчика", "Open the Developer portal"))}
          ${step(2, L("Скопируйте токен", "Copy the token"), esc(L("Вкладка Configuration → API token → Copy (начинается с ntn_, у старых интеграций — secret_). Там же можно оставить галочки Read / Update / Insert content.", "The Configuration tab → API token → Copy (it starts with ntn_, secret_ for old integrations). Keep Read / Update / Insert content ticked there.")), "", "")}
          ${step(3, L("Дайте доступ к страницам", "Share pages with it"), esc(L("Вкладка Content access → Edit access → отметьте страницы. Или на самой странице: «•••» → Connections → + Add connection → ваше подключение.", "The Content access tab → Edit access → tick the pages. Or on a page itself: «•••» → Connections → + Add connection → your connection.")), "", "")}
        </ol>${window.MemuGuideVideo ? `<div class="mgv-row">${MemuGuideVideo.button("notion-connection", { lang: isEn() ? "en" : "ru" })}</div>` : ""}
        <div class="gi-token-row">${K.secret("gi-notion-token", "", { placeholder: "ntn_…" })}<button type="button" class="pbtn sx-btn-sm" data-gi="notion-token">${esc(L("Подключить", "Connect"))}</button></div>` }));
    }
    const client = `<p class="gi-muted">${esc(L("Для входа одной кнопкой нужна публичная интеграция Notion (Public). В её настройках укажите Redirect URI ниже, затем вставьте OAuth client ID и secret.", "One-button sign-in needs a public Notion integration. Set the Redirect URI below in its settings, then paste the OAuth client ID and secret."))}</p>
      <div class="gi-copy"><code id="gi-notion-redirect">${esc(oauth.redirect_uri || "")}</code><button type="button" class="pbtn ghost sx-btn-sm" data-gi="notion-copy-redirect">${ico("copy")} ${esc(L("Копировать", "Copy"))}</button></div>
      ${K.field({ label: "OAuth client ID", forId: "gi-notion-cid", control: K.input("gi-notion-cid", oauth.own_client_id || "", { mono: true, placeholder: "00000000-0000-0000-0000-000000000000" }) })}
      ${K.field({ label: "OAuth client secret", forId: "gi-notion-secret", control: K.secret("gi-notion-secret", "", { placeholder: oauth.own_has_secret ? "••••••••" : "secret_…" }) })}
      ${K.actions(`<button type="button" class="pbtn sx-btn-sm" data-gi="notion-client-save">${esc(L("Сохранить", "Save"))}</button>`)}`;
    parts.push(K.section({ card: false, body: K.more({ id: "gi-notion-more", title: L("Свой OAuth-клиент Notion", "Your own Notion OAuth client"),
      hint: oauth.source === "user" ? L("используется ваш", "yours is in use") : oauth.available ? L("встроен в сборку", "built in") : L("для входа одной кнопкой", "for one-button sign-in"), body: `<div class="gi-more-pad">${client}</div>` }) }));
    return parts.join("");
  }

  function paint() {
    const g = document.getElementById("gi-google-root");
    if (g) { const open = !!document.getElementById("gi-client-more")?.open; g.innerHTML = googleBody(); if (open) { const d = document.getElementById("gi-client-more"); if (d) d.open = true; } }
    const n = document.getElementById("gi-notion-root");
    if (n) { const open = !!document.getElementById("gi-notion-more")?.open; n.innerHTML = notionBody(); if (open) { const d = document.getElementById("gi-notion-more"); if (d) d.open = true; } }
    const gv = document.getElementById("gi-google-value"); if (gv) gv.innerHTML = googleValue();
    const nv = document.getElementById("gi-notion-value"); if (nv) nv.innerHTML = notionValue();
  }

  async function load() {
    const [g, n] = await Promise.all([call("/api/pg/integrations/google").catch(() => null), call("/api/pg/integrations/notion").catch(() => null)]);
    const wasWaiting = (google?.flow?.state === "waiting") || (notion?.flow?.state === "waiting");
    if (g) google = g;
    if (n) notion = n;
    paint();
    const waiting = (google?.flow?.state === "waiting") || (notion?.flow?.state === "waiting");
    if (waiting) schedule();
    if (wasWaiting && !waiting) {
      if (google?.flow?.state === "done" || notion?.flow?.state === "done") say(L("Подключено", "Connected"));
      if (typeof refreshIntegrations === "function") refreshIntegrations();

      if (google?.flow?.state === "done" && google.connected) act("google-check");
    }
  }
  function schedule() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(() => { if (document.getElementById("gi-google-root")) load(); }, 1500);
  }

  async function act(name, target) {
    if (busy) return;
    busy = name;
    try {
      if (name === "google-check") {
        paint();
        const r = await call("/api/pg/integrations/google", { action: "check" });
        check = r.check || null;
        if (r.status) google = r.status;
        const bad = (check?.services || []).filter((c) => c.state === "disabled").length;
        if (target && target.dataset) say(bad ? L(`Нужно включить API: ${bad}`, `APIs to enable: ${bad}`) : L("Проверка завершена", "Check finished"), bad ? "warn" : "ok");
      } else if (name === "google-connect") {
        await call("/api/pg/integrations/google", { action: "connect", services: [...pick] });
      } else if (name === "google-grant") {
        check = null;
        await call("/api/pg/integrations/google", { action: "enable", service: target.dataset.svc });
      } else if (name === "google-cancel") {
        await call("/api/pg/integrations/google", { action: "cancel" });
      } else if (name === "google-disconnect") {
        const ok = typeof askConfirm === "function" ? await askConfirm(L("Выйти из Google?", "Sign out of Google?"),
          L("MemuAI отзовёт доступ у Google и забудет токен. Gmail, Календарь, Диск, резервные копии и «Файлы с Google Диска» перестанут работать, пока вы не войдёте снова.", "MemuAI revokes its access at Google and forgets the token. Gmail, Calendar, Drive, backups and «Files from Google Drive» stop working until you sign in again."),
          L("Выйти", "Sign out")) : true;
        if (!ok) return;
        await call("/api/pg/integrations/google", { action: "disconnect" });
        say(L("Вы вышли из Google", "Signed out of Google"));
      } else if (name === "google-client-save" || name === "google-client-clear") {
        const clear = name === "google-client-clear";
        let cid = clear ? "" : document.getElementById("gi-client-id")?.value.trim() || "";
        let secret = clear ? "" : document.getElementById("gi-client-secret")?.value.trim() || "";
        if (cid.startsWith("{")) {
          try { const d = JSON.parse(cid); const c = d.installed || d.web || d; cid = c.client_id || ""; secret = secret || c.client_secret || ""; } catch (e) {  }
        }
        await call("/api/pg/integrations/google", { action: "client", client_id: cid, client_secret: secret });
        say(clear ? L("Свой клиент убран", "Your client was removed") : L("OAuth-клиент сохранён", "OAuth client saved"));
      } else if (name === "notion-token") {
        await call("/api/pg/integrations/notion", { action: "token", token: document.getElementById("gi-notion-token")?.value || "" });
        say(L("Notion подключён", "Notion connected"));
      } else if (name === "notion-oauth") {
        await call("/api/pg/integrations/notion", { action: "oauth" });
      } else if (name === "notion-cancel") {
        await call("/api/pg/integrations/notion", { action: "cancel" });
      } else if (name === "notion-test") {
        const r = await call("/api/pg/integrations/notion", { action: "test" });
        say(L("Notion отвечает", "Notion answers") + (r.account ? ": " + r.account : ""));
      } else if (name === "notion-disconnect") {
        const ok = typeof askConfirm === "function" ? await askConfirm(L("Отключить Notion?", "Disconnect Notion?"),
          L("Токен будет удалён с этого компьютера.", "The token is deleted from this computer."), L("Отключить", "Disconnect")) : true;
        if (!ok) return;
        await call("/api/pg/integrations/notion", { action: "disconnect" });
      } else if (name === "notion-client-save") {
        await call("/api/pg/integrations/notion", { action: "client", client_id: document.getElementById("gi-notion-cid")?.value.trim() || "",
          client_secret: document.getElementById("gi-notion-secret")?.value.trim() || "" });
        say(L("Сохранено", "Saved"));
      } else if (name === "notion-copy-redirect") {
        try { await navigator.clipboard.writeText(notion?.oauth?.redirect_uri || ""); say(L("Скопировано", "Copied")); } catch (e) {}
        return;
      }
    } catch (error) {
      say(error.message || String(error), "warn");
    } finally {
      busy = "";
    }
    await load();
    if (typeof refreshIntegrations === "function") refreshIntegrations();
  }

  function onToggle(input) {
    const svc = input.dataset.giSvc;
    if (!google || !google.connected) {
      if (input.checked) pick.add(svc); else pick.delete(svc);
      paint();
      return;
    }
    if (busy) { input.checked = !input.checked; return; }
    busy = "toggle";
    call("/api/pg/integrations/google", { action: input.checked ? "enable" : "disable", service: svc })
      .catch((error) => say(error.message || String(error), "warn"))
      .finally(() => { busy = ""; load(); if (typeof refreshIntegrations === "function") refreshIntegrations(); });
  }

  let bound = false;
  function mount() {
    if (!bound) {
      bound = true;
      document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-gi]");
        if (!button || !button.closest(".gi-root")) return;
        event.preventDefault();
        act(button.dataset.gi, button);
      });
      document.addEventListener("change", (event) => {
        const input = event.target.closest("[data-gi-svc]");
        if (input && input.closest(".gi-root")) onToggle(input);
      });
      document.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.id === "gi-notion-token") { event.preventDefault(); act("notion-token", event.target); }
      });

      document.addEventListener("change", async (event) => {
        if (event.target.id !== "gi-client-json" || !event.target.files?.length) return;
        try {
          const data = JSON.parse(await event.target.files[0].text());
          const c = data.installed || data.web || data;
          if (!c.client_id) throw new Error("no client_id");
          const id = document.getElementById("gi-client-id"); if (id) id.value = c.client_id;
          const sec = document.getElementById("gi-client-secret"); if (sec) sec.value = c.client_secret || "";
          if (data.web) say(L("Это клиент типа «Web application» — нужен «Desktop app». Создайте клиент заново.", "This is a «Web application» client — a «Desktop app» one is needed. Create the client again."), "warn");
          else act("google-client-save");
        } catch (e) {
          say(L("Это не файл OAuth-клиента Google (client_secret_….json)", "This is not a Google OAuth client file (client_secret_….json)"), "warn");
        } finally { event.target.value = ""; }
      });
    }
    load();
  }

  async function open(sub) {
    if (typeof showSettings === "function") await showSettings();
    if (typeof openSettingsPage === "function") openSettingsPage("apps", { sub: sub || "" });
  }

  const VERBS = {
    telegram_use_send: ["Отправить сообщение в Telegram?", "Send this Telegram message?", "Отправить", "Send", "telegram"],
    telegram_use_edit: ["Изменить сообщение в Telegram?", "Edit this Telegram message?", "Изменить", "Edit", "telegram"],
    telegram_use_send_file: ["Отправить файл в Telegram?", "Send this file to Telegram?", "Отправить", "Send", "telegram"],
    telegram_use_forward: ["Переслать сообщения в Telegram?", "Forward Telegram messages?", "Переслать", "Forward", "telegram"],
    telegram_use_delete: ["Удалить сообщения в Telegram?", "Delete Telegram messages?", "Удалить", "Delete", "telegram"],
    telegram_use_react: ["Добавить реакцию в Telegram?", "React in Telegram?", "Добавить", "React", "telegram"],
    telegram_use_mark_read: ["Отметить сообщения прочитанными?", "Mark Telegram messages as read?", "Отметить", "Mark read", "telegram"],

    gmail_send: ["Отправить письмо?", "Send this email?", "Отправить", "Send", "gmail"],
    gmail_reply: ["Отправить ответ?", "Send the reply?", "Отправить", "Send", "gmail"],
    gmail_forward: ["Переслать письмо?", "Forward the email?", "Переслать", "Forward", "gmail"],
    gmail_create_draft: ["Сохранить черновик в Gmail?", "Save a Gmail draft?", "Сохранить", "Save", "gmail"],
    gmail_update_draft: ["Изменить черновик?", "Change the draft?", "Изменить", "Change", "gmail"],
    gmail_delete_draft: ["Удалить черновик?", "Delete the draft?", "Удалить", "Delete", "gmail"],
    gmail_archive: ["Убрать письма в архив?", "Archive the emails?", "В архив", "Archive", "gmail"],
    gmail_mark_read: ["Изменить отметку «прочитано»?", "Change «read» status?", "Изменить", "Change", "gmail"],
    gmail_star: ["Изменить пометку звёздочкой?", "Change the star?", "Изменить", "Change", "gmail"],
    gmail_modify: ["Изменить метки писем?", "Change the labels of the emails?", "Изменить", "Change", "gmail"],
    gmail_trash: ["Переместить письма в корзину?", "Move the emails to the trash?", "В корзину", "Move to trash", "gmail"],
    gmail_untrash: ["Вернуть письма из корзины?", "Restore the emails from the trash?", "Вернуть", "Restore", "gmail"],
    gmail_delete: ["Удалить письма навсегда?", "Delete the emails forever?", "Удалить навсегда", "Delete forever", "gmail"],
    gmail_bulk: ["Обработать письма по запросу?", "Apply to every matching email?", "Выполнить", "Apply", "gmail"],
    gmail_create_label: ["Создать метку в Gmail?", "Create a Gmail label?", "Создать", "Create", "gmail"],
    gmail_update_label: ["Переименовать метку?", "Rename the label?", "Переименовать", "Rename", "gmail"],
    gmail_delete_label: ["Удалить метку?", "Delete the label?", "Удалить", "Delete", "gmail"],
    gmail_save_attachment: ["Сохранить вложение на компьютер?", "Save the attachment to this computer?", "Сохранить", "Save", "gmail"],
    gmail_create_filter: ["Создать фильтр Gmail?", "Create a Gmail filter?", "Создать", "Create", "gmail"],
    gmail_delete_filter: ["Удалить фильтр Gmail?", "Delete the Gmail filter?", "Удалить", "Delete", "gmail"],
    gmail_vacation: ["Изменить автоответ?", "Change the auto-reply?", "Сохранить", "Save", "gmail"],

    calendar_create_event: ["Создать событие в календаре?", "Create a calendar event?", "Создать", "Create", "calendar"],
    calendar_update_event: ["Изменить событие?", "Change the event?", "Изменить", "Change", "calendar"],
    calendar_delete_event: ["Удалить событие?", "Delete the event?", "Удалить", "Delete", "calendar"],
    calendar_quick_add: ["Добавить событие?", "Add the event?", "Добавить", "Add", "calendar"],
    calendar_respond: ["Ответить на приглашение?", "Reply to the invitation?", "Ответить", "Reply", "calendar"],
    calendar_move_event: ["Перенести событие в другой календарь?", "Move the event to another calendar?", "Перенести", "Move", "calendar"],
    calendar_create_calendar: ["Создать календарь?", "Create a calendar?", "Создать", "Create", "calendar"],
    calendar_delete_calendar: ["Удалить календарь со всеми событиями?", "Delete the calendar with all its events?", "Удалить", "Delete", "calendar"],

    drive_create_file: ["Создать файл на Google Диске?", "Create a file in Google Drive?", "Создать", "Create", "drive"],
    drive_upload_file: ["Загрузить файл на Google Диск?", "Upload the file to Google Drive?", "Загрузить", "Upload", "drive"],
    drive_update_file: ["Перезаписать файл на Google Диске?", "Overwrite the file in Google Drive?", "Перезаписать", "Overwrite", "drive"],
    drive_create_folder: ["Создать папку на Google Диске?", "Create a folder in Google Drive?", "Создать", "Create", "drive"],
    drive_rename: ["Переименовать файл?", "Rename the file?", "Переименовать", "Rename", "drive"],
    drive_move: ["Переместить файл?", "Move the file?", "Переместить", "Move", "drive"],
    drive_copy: ["Сделать копию файла?", "Copy the file?", "Копировать", "Copy", "drive"],
    drive_trash: ["Переместить файл в корзину Диска?", "Move the file to the Drive trash?", "В корзину", "Move to trash", "drive"],
    drive_untrash: ["Вернуть файл из корзины?", "Restore the file from the trash?", "Вернуть", "Restore", "drive"],
    drive_delete_file: ["Удалить файл навсегда?", "Delete the file forever?", "Удалить навсегда", "Delete forever", "drive"],
    drive_share: ["Открыть доступ к файлу?", "Share the file?", "Открыть доступ", "Share", "drive"],
    drive_unshare: ["Закрыть доступ к файлу?", "Remove access to the file?", "Закрыть доступ", "Remove access", "drive"],
    drive_download: ["Скачать файл на компьютер?", "Download the file to this computer?", "Скачать", "Download", "drive"],

    gdocs_create: ["Создать документ Google?", "Create a Google Doc?", "Создать", "Create", "docs"],
    gdocs_append: ["Дописать текст в документ?", "Append text to the document?", "Дописать", "Append", "docs"],
    gdocs_replace: ["Заменить текст в документе?", "Replace text in the document?", "Заменить", "Replace", "docs"],
    gsheets_create: ["Создать таблицу Google?", "Create a Google Sheet?", "Создать", "Create", "sheets"],
    gsheets_write: ["Записать ячейки таблицы?", "Write the cells?", "Записать", "Write", "sheets"],
    gsheets_append: ["Добавить строки в таблицу?", "Append rows to the sheet?", "Добавить", "Append", "sheets"],
    gsheets_clear: ["Очистить диапазон таблицы?", "Clear the range?", "Очистить", "Clear", "sheets"],
    gsheets_add_sheet: ["Добавить лист?", "Add a sheet?", "Добавить", "Add", "sheets"],
    gsheets_delete_sheet: ["Удалить лист со всеми данными?", "Delete the sheet with all its data?", "Удалить", "Delete", "sheets"],
    gtasks_create: ["Создать задачу?", "Create a task?", "Создать", "Create", "tasks"],
    gtasks_update: ["Изменить задачу?", "Change the task?", "Изменить", "Change", "tasks"],
    gtasks_complete: ["Отметить задачу выполненной?", "Mark the task done?", "Выполнено", "Done", "tasks"],
    gtasks_delete: ["Удалить задачу?", "Delete the task?", "Удалить", "Delete", "tasks"],
    gtasks_create_list: ["Создать список задач?", "Create a task list?", "Создать", "Create", "tasks"],
    gcontacts_create: ["Создать контакт?", "Create a contact?", "Создать", "Create", "contacts"],
    gcontacts_update: ["Изменить контакт?", "Change the contact?", "Изменить", "Change", "contacts"],
    gcontacts_delete: ["Удалить контакт?", "Delete the contact?", "Удалить", "Delete", "contacts"],

    notion_create_page: ["Создать страницу в Notion?", "Create a Notion page?", "Создать", "Create", "notion"],
    notion_append: ["Дополнить страницу Notion?", "Add to the Notion page?", "Дополнить", "Add", "notion"],
    notion_update_page: ["Изменить страницу Notion?", "Change the Notion page?", "Изменить", "Change", "notion"],
    notion_delete_page: ["Удалить страницу Notion в корзину?", "Move the Notion page to the trash?", "Удалить", "Delete", "notion"],
    notion_restore_page: ["Вернуть страницу Notion из корзины?", "Restore the Notion page?", "Вернуть", "Restore", "notion"],
    notion_replace_content: ["Заменить всё содержимое страницы?", "Replace the whole page content?", "Заменить", "Replace", "notion"],
    notion_delete_block: ["Удалить блок Notion?", "Delete the Notion block?", "Удалить", "Delete", "notion"],
    notion_comment: ["Оставить комментарий в Notion?", "Comment in Notion?", "Отправить", "Comment", "notion"],

    github_write_file: ["Записать файл в GitHub?", "Write the file to GitHub?", "Записать", "Write", "github"],
    github_commit_files: ["Сделать коммит в GitHub?", "Commit to GitHub?", "Закоммитить", "Commit", "github"],
    github_delete_file: ["Удалить файл в GitHub?", "Delete the file in GitHub?", "Удалить", "Delete", "github"],
    github_move_file: ["Переместить или переименовать файл в GitHub?", "Move or rename the file in GitHub?", "Переместить", "Move", "github"],
    github_create_repo: ["Создать репозиторий GitHub?", "Create a GitHub repository?", "Создать", "Create", "github"],
    github_update_repo: ["Изменить настройки репозитория?", "Change the repository settings?", "Изменить", "Change", "github"],
    github_delete_repo: ["Удалить репозиторий навсегда?", "Delete the repository forever?", "Удалить навсегда", "Delete forever", "github"],
    github_fork_repo: ["Сделать форк репозитория?", "Fork the repository?", "Форк", "Fork", "github"],
    github_create_branch: ["Создать ветку?", "Create a branch?", "Создать", "Create", "github"],
    github_delete_branch: ["Удалить ветку?", "Delete the branch?", "Удалить", "Delete", "github"],
    github_create_issue: ["Создать issue в GitHub?", "Create a GitHub issue?", "Создать", "Create", "github"],
    github_update_issue: ["Изменить issue?", "Change the issue?", "Изменить", "Change", "github"],
    github_comment: ["Оставить комментарий в GitHub?", "Comment on GitHub?", "Отправить", "Comment", "github"],
    github_create_label: ["Создать метку в репозитории?", "Create a repository label?", "Создать", "Create", "github"],
    github_add_labels: ["Добавить метки?", "Add labels?", "Добавить", "Add", "github"],
    github_remove_label: ["Снять метку?", "Remove the label?", "Снять", "Remove", "github"],
    github_add_assignees: ["Назначить исполнителей?", "Assign people?", "Назначить", "Assign", "github"],
    github_create_pull: ["Создать pull request?", "Create a pull request?", "Создать", "Create", "github"],
    github_update_pull: ["Изменить pull request?", "Change the pull request?", "Изменить", "Change", "github"],
    github_merge_pull: ["Слить pull request?", "Merge the pull request?", "Слить", "Merge", "github"],
    github_review_pull: ["Отправить ревью?", "Submit the review?", "Отправить", "Submit", "github"],
    github_request_reviewers: ["Запросить ревью?", "Request reviewers?", "Запросить", "Request", "github"],
    github_create_release: ["Опубликовать релиз?", "Publish a release?", "Опубликовать", "Publish", "github"],
    github_delete_release: ["Удалить релиз?", "Delete the release?", "Удалить", "Delete", "github"],
    github_create_gist: ["Создать gist?", "Create a gist?", "Создать", "Create", "github"],
    github_update_gist: ["Изменить gist?", "Change the gist?", "Изменить", "Change", "github"],
    github_delete_gist: ["Удалить gist?", "Delete the gist?", "Удалить", "Delete", "github"],
    github_rerun: ["Перезапустить workflow?", "Re-run the workflow?", "Перезапустить", "Re-run", "github"],
    github_cancel_run: ["Остановить запуск workflow?", "Cancel the workflow run?", "Остановить", "Cancel", "github"],
    github_dispatch_workflow: ["Запустить workflow?", "Run the workflow?", "Запустить", "Run", "github"],
    github_add_collaborator: ["Пригласить участника в репозиторий?", "Invite a collaborator?", "Пригласить", "Invite", "github"],
    github_remove_collaborator: ["Убрать участника из репозитория?", "Remove the collaborator?", "Убрать", "Remove", "github"],
  };
  for (const tool of ["telegram_use_send", "telegram_use_edit", "telegram_use_send_file", "telegram_use_forward", "telegram_use_delete", "telegram_use_react", "telegram_use_mark_read"]) WRITE_TOOLS.add(tool);
  for (const tool of ["telegram_use_send", "telegram_use_edit", "telegram_use_send_file", "telegram_use_forward", "telegram_use_delete", "telegram_use_react", "telegram_use_mark_read"]) ALWAYS_TOOLS.add(tool);
  DANGER_TOOLS.add("telegram_use_delete");
  const NOTE = {
    gmail_delete: ["Письма удаляются мимо корзины — вернуть их будет нельзя. MemuAI спрашивает об этом всегда.", "The emails skip the trash — they cannot be restored. MemuAI always asks about this."],
    gmail_send: ["Отправленное письмо нельзя вернуть. MemuAI спрашивает об этом всегда.", "A sent email cannot be taken back. MemuAI always asks about it."],
    gmail_reply: ["Отправленное письмо нельзя вернуть. MemuAI спрашивает об этом всегда.", "A sent email cannot be taken back. MemuAI always asks about it."],
    gmail_forward: ["Пересланное письмо нельзя вернуть. MemuAI спрашивает об этом всегда.", "A forwarded email cannot be taken back. MemuAI always asks about it."],
    gmail_trash: ["Письма лежат в корзине Gmail 30 дней — их можно вернуть.", "The emails stay in the Gmail trash for 30 days — they can be restored."],
    gmail_bulk: ["Действие применится ко всем письмам, найденным по запросу (до указанного лимита).", "The action applies to every email the query finds (up to the limit)."],
    drive_delete_file: ["Файл удаляется мимо корзины — вернуть его будет нельзя. MemuAI спрашивает об этом всегда.", "The file skips the trash — it cannot be restored. MemuAI always asks about this."],
    drive_trash: ["Файл лежит в корзине Диска 30 дней — его можно вернуть.", "The file stays in the Drive trash for 30 days — it can be restored."],
    github_delete_repo: ["Репозиторий со всем кодом, issues и релизами будет удалён безвозвратно. MemuAI спрашивает об этом всегда.", "The repository with all its code, issues and releases is deleted for good. MemuAI always asks about this."],
    calendar_delete_calendar: ["Все события этого календаря исчезнут. MemuAI спрашивает об этом всегда.", "Every event of this calendar disappears. MemuAI always asks about this."],
    notion_delete_page: ["Страница уходит в корзину Notion — её можно вернуть.", "The page goes to Notion's trash — it can be restored."],
  };
  const groupBrand = { gmail: "gmail", calendar: "calendar", drive: "drive", docs: "docs", sheets: "sheets", tasks: "tasks", contacts: "contacts", notion: "notion", github: "github" };
  function fieldsOf(tool, args) {
    const a = args || {};
    const list = (v) => (Array.isArray(v) ? v.join(", ") : (v && typeof v === "object" ? Object.keys(v).join(", ") : v));
    const count = (v) => (Array.isArray(v) ? (v.length > 3 ? L(`${v.length} писем`, `${v.length} emails`) : v.join(", ")) : v);
    const F = (ru, en, value) => (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length) ? null : [L(ru, en), String(list(value))]);
    const yes = (v) => (v === true || String(v).toLowerCase() === "true" ? L("да", "yes") : v === false || String(v).toLowerCase() === "false" ? L("нет", "no") : v);
    const rows = {
      telegram: [F("Чат", "Chat", a.chat), F("Из чата", "From chat", a.from_chat),
        F("Сообщения", "Messages", a.message_ids || a.message_id), F("Ответ на сообщение", "Reply to message", a.reply_to),
        F("Файл", "File", a.path), F("Реакция", "Reaction", a.emoji), F("Без уведомления", "Silent", a.silent !== undefined ? yes(a.silent === true) : ""),
        F("Удалить у всех", "Delete for everyone", tool === "telegram_use_delete" ? yes(a.revoke !== false) : "")],
      gmail: [F("Кому", "To", a.to || (a.reply_to_message_id || tool === "gmail_reply" ? L("ответ отправителю", "reply to the sender") : "")),
        F("Копия", "Cc", a.cc), F("Скрытая копия", "Bcc", a.bcc), F("Тема", "Subject", a.subject),
        F("Ответить всем", "Reply all", a.reply_all ? yes(a.reply_all) : ""), F("Черновик", "Draft", a.draft_id || (a.draft ? yes(a.draft) : "")),
        F("Письма", "Emails", count(a.message_ids || a.message_id)), F("Цепочка", "Thread", a.thread_id),
        F("Запрос", "Query", a.query), F("Действие", "Action", a.action), F("Добавить метки", "Add labels", a.add_labels),
        F("Снять метки", "Remove labels", a.remove_labels), F("Метка", "Label", a.label || a.name), F("Новое имя", "New name", a.new_name),
        F("Прочитано", "Read", a.read !== undefined ? yes(a.read) : ""), F("Звёздочка", "Star", a.starred !== undefined ? yes(a.starred) : ""),
        F("Вложения", "Attachments", a.attachments), F("От", "From", a.from), F("Папка", "Folder", a.folder), F("Фильтр", "Filter", a.filter_id),
        F("Автоответ", "Auto-reply", a.enable !== undefined ? yes(a.enable) : "")],
      calendar: [F("Событие", "Event", a.summary || a.text), F("Начало", "Start", a.start), F("Конец", "End", a.end), F("Место", "Location", a.location),
        F("Гости", "Guests", a.attendees), F("Google Meet", "Google Meet", a.meet ? yes(a.meet) : ""), F("Повтор", "Repeat", a.recurrence),
        F("Ответ", "Response", a.response), F("Календарь", "Calendar", a.calendar_id), F("Куда", "Destination", a.destination), F("ID", "ID", a.event_id)],
      drive: [F("Файл", "File", a.name && tool !== "drive_rename" ? a.name : (a.path || a.file_id)), F("Новое имя", "New name", tool === "drive_rename" ? a.name : ""),
        F("Папка", "Folder", a.folder_id || a.folder), F("Кому", "With", a.email), F("Права", "Role", a.role), F("Формат", "Format", a.format)],
      docs: [F("Документ", "Document", a.title || a.document_id), F("Найти", "Find", a.find), F("Заменить на", "Replace with", a.replace)],
      sheets: [F("Таблица", "Spreadsheet", a.title || a.spreadsheet_id), F("Диапазон", "Range", a.range), F("Лист", "Sheet", a.sheet),
        F("Строк", "Rows", Array.isArray(a.values) ? a.values.length : "")],
      tasks: [F("Задача", "Task", a.title || a.task_id), F("Срок", "Due", a.due), F("Статус", "Status", a.status), F("Список", "List", a.list_id)],
      contacts: [F("Имя", "Name", a.name || a.resource_name), F("Почта", "Email", a.email), F("Телефон", "Phone", a.phone), F("Организация", "Organization", a.organization)],
      notion: [F("Заголовок", "Title", a.title), F("Страница", "Page", a.page_id || a.parent_id), F("Блок", "Block", a.block_id), F("Свойства", "Properties", a.properties)],
      github: [F("Репозиторий", "Repository", a.repo || a.name), F("Новое имя", "New name", a.new_name), F("Путь", "Path", a.path),
        F("Куда", "To", a.new_path), F("Ветка", "Branch", a.branch || a.head), F("В ветку", "Into", a.base), F("Номер", "Number", a.number ? "#" + a.number : ""),
        F("Заголовок", "Title", a.title), F("Коммит", "Commit", a.message), F("Файлы", "Files", Array.isArray(a.files) ? a.files.map((f) => f && f.path || f.name).filter(Boolean) : a.files),
        F("Удалить", "Delete", a.delete), F("Приватный", "Private", a.private !== undefined ? yes(a.private) : ""), F("Состояние", "State", a.state),
        F("Метки", "Labels", a.labels || a.label), F("Исполнители", "Assignees", a.assignees || a.reviewers), F("Тег", "Tag", a.tag),
        F("Способ", "Method", a.method || a.event), F("Участник", "User", a.username), F("Права", "Permission", a.permission),
        F("Workflow", "Workflow", a.workflow), F("Запуск", "Run", a.run_id), F("Gist", "Gist", a.gist_id)],
    }[(VERBS[tool] || [])[4]] || [];
    let body = String(a.body ?? a.content ?? a.description ?? a.text ?? a.html ?? "");
    if ((VERBS[tool] || [])[4] === "telegram") body = String(a.text ?? a.caption ?? "");
    if (tool === "telegram_use_send_file" && !body.trim()) body = "";
    if (tool === "calendar_quick_add") body = "";
    return { rows: rows.filter(Boolean), body };
  }
  function describe(tool, args) {
    const { rows, body } = fieldsOf(tool, args);
    return rows.map(([k, v]) => `${k}: ${v}`).join("\n") + (body ? "\n\n" + body.slice(0, 700) : "");
  }
  function telegramPreview(parts, target) {
    const allowed = new Set(["B", "STRONG", "I", "EM", "U", "S", "DEL", "CODE", "PRE", "A", "BLOCKQUOTE", "TG-SPOILER"]);
    function copy(node, parent) {
      if (node.nodeType === 3) { parent.append(document.createTextNode(node.textContent)); return; }
      if (node.nodeType !== 1 || !allowed.has(node.tagName)) return;
      const element = document.createElement(node.tagName === "TG-SPOILER" ? "span" : node.tagName.toLowerCase());
      if (node.tagName === "A") {
        const href = node.getAttribute("href") || "";
        if (/^(https?:|mailto:|tg:)/i.test(href)) { element.href = href; element.target = "_blank"; element.rel = "noopener noreferrer"; }
      }
      if (node.tagName === "TG-SPOILER") { element.className = "gi-tg-spoiler"; element.title = L("Спойлер", "Spoiler"); }
      for (const child of node.childNodes) copy(child, element);
      parent.append(element);
    }
    target.replaceChildren();
    for (const [index, html] of parts.entries()) {
      if (index) { const separator = document.createElement("div"); separator.className = "gi-tg-part"; separator.textContent = L(`Сообщение ${index + 1}`, `Message ${index + 1}`); target.append(separator); }
      const template = document.createElement("template"); template.innerHTML = html;
      for (const child of template.content.childNodes) copy(child, target);
    }
  }
  function confirm(tool, args) {
    const verb = VERBS[tool];
    if (!verb || typeof panel !== "function") {
      return typeof askConfirm === "function" ? askConfirm(L("Разрешить действие?", "Allow this action?"), `${tool}: ${JSON.stringify(args).slice(0, 600)}`, L("Разрешить", "Allow")) : Promise.resolve(false);
    }
    const { rows, body } = fieldsOf(tool, args);
    const telegram = verb[4] === "telegram";
    const formatted = telegram && !!body;
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => { if (settled) return; settled = true; try { closePanel(value ? "confirm" : "cancel"); } catch (e) {} resolve(value); };
      const danger = DANGER_TOOLS.has(tool);
      const note = NOTE[tool] ? L(NOTE[tool][0], NOTE[tool][1]) : L("Ассистент просит выполнить это действие от вашего имени.", "The assistant asks to do this on your behalf.");
      panel(`<div class="gi-confirm ${danger ? "danger" : ""}">
        <div class="gi-confirm-head">${brand(groupBrand[verb[4]] || verb[4], "big")}<h2>${esc(L(verb[0], verb[1]))}</h2></div>
        ${rows.length ? `<dl class="gi-confirm-rows">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v.length > 400 ? v.slice(0, 400) + "…" : v)}</dd>`).join("")}</dl>` : ""}
        ${body ? `<div class="gi-confirm-body${telegram ? " gi-telegram-preview" : ""}" id="gi-confirm-body">${esc(telegram ? body : body.slice(0, 4000))}${!telegram && body.length > 4000 ? "…" : ""}</div>` : ""}
        ${formatted ? `<p class="gi-muted" id="gi-preview-status" role="status">${esc(L("Подготавливаю предпросмотр Telegram…", "Preparing Telegram preview…"))}</p>` : ""}
        <p class="gi-muted ${ALWAYS_TOOLS.has(tool) ? "gi-confirm-warn" : ""}">${esc(note)}</p>
        <div class="row-end"><button class="pbtn ghost" id="gi-confirm-no">${esc(L("Отмена", "Cancel"))}</button>
          <button class="pbtn ${danger ? "danger" : ""}" id="gi-confirm-yes"${formatted ? " disabled" : ""}>${esc(L(verb[2], verb[3]))}</button></div></div>`, "confirm-panel gi-confirm-panel", { onClose: () => finish(false) });
      document.getElementById("gi-confirm-no").onclick = () => finish(false);
      if (formatted) {
        const content = document.getElementById("gi-confirm-body"), status = document.getElementById("gi-preview-status"), yesButton = document.getElementById("gi-confirm-yes");
        fetch("/api/pg/integrations/telegram-use/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: body, kind: tool === "telegram_use_send_file" ? "caption" : "message" }) })
          .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); return data; })
          .then((data) => { if (settled || !content.isConnected) return; if (!Array.isArray(data.parts) || !data.parts.length || data.parts.some(p => typeof p !== "string")) throw new Error(L("Нет предпросмотра", "Preview unavailable"));
            telegramPreview(data.parts, content); status.textContent = data.truncated ? L("Подпись будет сокращена до показанного текста по лимиту Telegram", "The caption will be shortened to the shown text for Telegram's limit") : data.parts.length > 1 ? L(`Будет отправлено сообщений: ${data.parts.length}`, `Messages to send: ${data.parts.length}`) : L("Форматирование соответствует Telegram", "Formatting matches Telegram"); yesButton.disabled = false; })
          .catch((error) => { if (!settled && status.isConnected) status.textContent = L("Не удалось подготовить предпросмотр: ", "Could not prepare preview: ") + error.message; });
      }
      document.getElementById("gi-confirm-yes").onclick = () => finish(true);

      queueMicrotask(() => document.getElementById(danger ? "gi-confirm-no" : "gi-confirm-yes")?.focus({ preventScroll: true }));
    });
  }

  function githubGuide() {
    const b = (t) => `<b>${esc(t)}</b>`;
    const code = (t) => `<code>${esc(t)}</code>`;
    return `<div class="sx-more-text gi-gh-guide">
      ${window.MemuGuideVideo ? `<div class="mgv-row">${MemuGuideVideo.button("github-token", { lang: isEn() ? "en" : "ru" })}</div>` : ""}
      <p>${esc(L("Подойдёт любой из двух видов токена. Чтобы ассистент мог всё — создавать и удалять репозитории, файлы, ветки, pull request, релизы, gists, запускать Actions, — дайте права ниже.", "Either kind of token works. For the assistant to do everything — create and delete repositories, files, branches, pull requests, releases, gists, run Actions — grant the rights below."))}</p>
      <h4>${esc(L("Classic — проще всего", "Classic — the simplest"))}</h4>
      <ol><li>${L(`github.com → Settings → Developer settings → Personal access tokens → ${b("Tokens (classic)")} → ${b("Generate new token (classic)")}.`, `github.com → Settings → Developer settings → Personal access tokens → ${b("Tokens (classic)")} → ${b("Generate new token (classic)")}.`)}</li>
        <li>${L(`Срок — на ваше усмотрение. Отметьте scopes: ${code("repo")}, ${code("workflow")}, ${code("gist")}, ${code("delete_repo")}; для репозиториев организаций — ещё ${code("read:org")}.`, `Pick an expiration. Tick the scopes: ${code("repo")}, ${code("workflow")}, ${code("gist")}, ${code("delete_repo")}; for organisation repositories also ${code("read:org")}.`)}</li>
        <li>${esc(L("Generate token → скопируйте (ghp_…) и вставьте выше.", "Generate token → copy it (ghp_…) and paste it above."))}</li></ol>
      <h4>${esc(L("Fine-grained — точнее", "Fine-grained — more precise"))}</h4>
      <ol><li>${L(`Там же → ${b("Fine-grained tokens")} → ${b("Generate new token")}. Repository access: ${b("All repositories")} (иначе ассистент не сможет создавать новые репозитории) или выбранные.`, `Same place → ${b("Fine-grained tokens")} → ${b("Generate new token")}. Repository access: ${b("All repositories")} (otherwise the assistant cannot create new repositories) or selected ones.`)}</li>
        <li>${L(`Repository permissions — ${b("Read and write")}: Administration (создание, удаление, переименование, видимость, участники), Contents, Issues, Pull requests, Actions, Workflows; Metadata — Read (ставится сам).`, `Repository permissions — ${b("Read and write")}: Administration (create, delete, rename, visibility, collaborators), Contents, Issues, Pull requests, Actions, Workflows; Metadata — Read (set automatically).`)}</li>
        <li>${L(`Account permissions: ${b("Gists")} — Read and write. Generate token → скопируйте (github_pat_…) и вставьте выше.`, `Account permissions: ${b("Gists")} — Read and write. Generate token → copy it (github_pat_…) and paste it above.`)}</li></ol>
      <p class="gi-muted">${esc(L("«Проверить» покажет вид токена и каких scopes не хватает. Если GitHub отказывает, ассистент сообщит, какое именно право нужно. Удаление репозитория MemuAI подтверждает у вас всегда.", "«Test» shows the token kind and the missing scopes. When GitHub refuses, the assistant says which right is needed. Deleting a repository is always confirmed with you."))}</p>
    </div>`;
  }

  window.MemuIntegrations = { listHtml, subsHtml, mount, open, confirm, describe, brand: (name) => brand(name), githubGuide,
    WRITE: WRITE_TOOLS, ALWAYS: ALWAYS_TOOLS, DANGER: DANGER_TOOLS };
})();
