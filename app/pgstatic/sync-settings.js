

(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {}
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    cloud: '<svg viewBox="0 0 24 24"><path d="M7 18h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.6 9.2 4.5 4.5 0 0 0 7 18z"/></svg>',
    cloudOk: '<svg viewBox="0 0 24 24"><path d="M7 18h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.6 9.2 4.5 4.5 0 0 0 7 18z"/><path d="m9.5 13.5 2 2 3.5-3.5"/></svg>',
    cloudOff: '<svg viewBox="0 0 24 24"><path d="M7 18h10.5a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.6 9.2 4.5 4.5 0 0 0 7 18z"/><path d="m4 4 16 16"/></svg>',
    upload: '<svg viewBox="0 0 24 24"><path d="M12 16V5"/><path d="m7.5 9.5 4.5-4.5 4.5 4.5"/><path d="M5 19h14"/></svg>',
    restore: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l3 2"/></svg>',
    folder: '<svg viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></svg>',
    drive: '<svg viewBox="0 0 24 24"><path d="M8.5 4h7l5.5 9.5-3.5 6h-11L3 13.5z"/><path d="M8.5 4 12 10l-3.5 6M15.5 4 9 15.5M21 13.5H8"/></svg>',
    lock: '<svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/></svg>',
    pin: '<svg viewBox="0 0 24 24"><path d="m12 3 2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6-4.5-4.2 6.1-.7z"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M10 11v6M14 11v6M6 7l1 12.5h10L18 7M9 7V4.5h6V7"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19h14"/></svg>',
    file: '<svg viewBox="0 0 24 24"><path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/></svg>',
    undo: '<svg viewBox="0 0 24 24"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2"/></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
  };
  const ico = (name) => `<span class="ui-icon" aria-hidden="true">${SVG[name] || ""}</span>`;

  let ctx = null;
  let st = null;
  let stats = null;
  let snaps = null;
  let snapsError = "";
  let safety = [];
  let folders = null;
  let pollTimer = 0;
  let restoreSel = null;
  let busyAction = "";

  const kit = () => (ctx && ctx.kit) || window.MemuSettingsKit;
  const root = () => document.getElementById("sync-root");

  async function call(path, body, method) {
    const options = { method: method || (body === undefined ? "GET" : "POST"), headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error((isEn() ? data.error_en : data.error_ru) || data.error || `HTTP ${response.status}`);
      error.code = data.code || "";
      error.payload = data;
      throw error;
    }
    return data;
  }
  const say = (text, type = "ok") => {
    try { if (ctx && ctx.toast) ctx.toast(text, type); else if (typeof toast === "function") toast(text, type); } catch (e) {}
  };

  function bytes(n) {
    const v = Number(n) || 0;
    const units = isEn() ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let i = 0, x = v;
    while (x >= 1024 && i < units.length - 1) { x /= 1024; i++; }
    const digits = i === 0 || x >= 100 ? 0 : x >= 10 ? 1 : 2;
    return `${x.toLocaleString(isEn() ? "en-US" : "ru-RU", { maximumFractionDigits: digits })} ${units[i]}`;
  }
  function ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - Number(ts));
    if (s < 60) return L("только что", "just now");
    const m = Math.round(s / 60);
    if (m < 60) return L(`${m} мин назад`, `${m} min ago`);
    const h = Math.round(m / 60);
    if (h < 24) return L(`${h} ч назад`, `${h} h ago`);
    const d = Math.round(h / 24);
    return L(`${d} дн. назад`, d === 1 ? "1 day ago" : `${d} days ago`);
  }
  function until(ts) {
    if (!ts) return "";
    const s = Number(ts) - Date.now() / 1000;
    if (s <= 60) return L("скоро", "soon");
    const m = Math.round(s / 60);
    if (m < 60) return L(`через ${m} мин`, `in ${m} min`);
    const h = Math.round(m / 60);
    if (h < 24) return L(`через ${h} ч`, `in ${h} h`);
    const d = Math.round(h / 24);
    return d === 1 ? L("завтра", "tomorrow") : L(`через ${d} ${ruPlural(d, "день", "дня", "дней")}`, `in ${d} days`);
  }

  const ruPlural = (n, one, few, many) => { const t = n % 100, u = n % 10; return t > 10 && t < 20 ? many : u === 1 ? one : u >= 2 && u <= 4 ? few : many; };
  function nFiles(n) {
    n = Number(n) || 0;
    return isEn() ? (n === 1 ? "1 file" : `${n} files`) : `${n} ${ruPlural(n, "файл", "файла", "файлов")}`;
  }

  const shortTarget = (t) => { const parts = String(t || "").split(/[\\/]+/).filter(Boolean); return parts.length > 2 ? "…" + (String(t).includes("\\") ? "\\" : "/") + parts.slice(-2).join(String(t).includes("\\") ? "\\" : "/") : String(t || ""); };
  function when(ts, seconds) {
    if (!ts) return "";
    try {
      return new Date(Number(ts) * 1000).toLocaleString(isEn() ? "en-GB" : "ru-RU", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) });
    } catch (e) { return ""; }
  }
  function interval(minutes) {
    const m = Number(minutes) || 0;
    if (m < 60) return L(`${m} мин`, `${m} min`);
    if (m < 1440) return L(`${m / 60} ч`, `${m / 60} h`);
    if (m === 10080) return L("неделю", "week");
    const d = m / 1440;
    return L(d === 1 ? "день" : `${d} дн.`, d === 1 ? "day" : `${d} days`);
  }
  const REASON = { manual: ["вручную", "manual"], auto: ["по расписанию", "scheduled"], exit: ["при выходе", "on exit"], start: ["при запуске", "on start"] };
  const reason = (r) => { const row = REASON[r] || [r || "", r || ""]; return L(row[0], row[1]); };
  const PHASE = {
    start: ["Подготовка…", "Preparing…"], scan: ["Ищу изменения…", "Looking for changes…"], index: ["Сверяю с хранилищем…", "Comparing with the storage…"],
    pack: ["Упаковываю изменённые файлы…", "Packing changed files…"], upload: ["Загружаю…", "Uploading…"], commit: ["Сохраняю описание копии…", "Saving the backup description…"],
    cleanup: ["Удаляю старые версии…", "Removing old versions…"], download: ["Скачиваю копию…", "Downloading the backup…"],
    safety: ["Делаю страховочную копию…", "Making a safety copy…"], write: ["Восстанавливаю файлы…", "Restoring files…"], other: ["Идёт операция…", "Working…"],
  };
  const phase = (p) => { const row = PHASE[p] || PHASE.other; return L(row[0], row[1]); };
  const errText = (e) => (e ? (isEn() ? e.en : e.ru) || e.ru || "" : "");
  const settings = () => (st && st.settings) || {};
  const catName = (id) => { const c = ((st && st.categories) || []).find((row) => row.id === id); return c ? (isEn() ? c.en : c.ru) : id; };

  function hero() {
    const s = settings();
    const running = st && st.running;
    const err = st && st.last_error;
    const ready = s.target === "folder" ? !!s.folder_path : !!(st && st.connected);
    let tone = "idle", title, text = "", iconName = "cloud";

    const lostAccess = !running && !ready && s.target !== "folder" && err && ["invalid_grant", "scope"].includes(err.code);
    if (running) {
      tone = "busy"; iconName = "upload";
      title = running.kind === "restore" ? L("Восстановление…", "Restoring…") : phase(running.phase);
    } else if (lostAccess) {
      tone = "err"; iconName = "cloudOff";
      title = L("Google Drive нужно подключить заново", "Google Drive needs to be connected again");
      text = errText(err);
    } else if (!ready) {
      tone = "idle"; iconName = "cloudOff";
      title = L("Копии пока некуда сохранять", "Nowhere to store backups yet");
      text = L("Подключите Google Drive или выберите папку ниже — и ваши чаты, настройки и всё остальное будут в безопасности.", "Connect Google Drive or choose a folder below — and your chats, settings and everything else will be safe.");
    } else if (err && (!st.last_success || err.ts > st.last_success)) {
      tone = "err"; iconName = "warn";
      title = L("Последняя копия не сохранилась", "The last backup failed");
      text = errText(err);
    } else if (st.last_success) {
      tone = "ok"; iconName = "cloudOk";
      const r = st.last_result || {};
      title = L(`Последняя копия — ${ago(st.last_success)}`, `Last backup ${ago(st.last_success)}`);
      text = [r.files ? `${nFiles(r.files)}, ${bytes(r.bytes)}` : "",
        r.unchanged ? L("без изменений", "nothing changed") : (r.new_bytes ? L(`новых данных ${bytes(r.new_bytes)}`, `${bytes(r.new_bytes)} new`) : ""),
        shortTarget(r.target)].filter(Boolean).join(" · ");
    } else {
      title = L("Копий ещё нет", "No backups yet");
      text = L("Нажмите «Сохранить сейчас», чтобы сделать первую.", "Press «Back up now» to make the first one.");
    }
    const next = st && st.next_due && !running ? `<span class="sync-hero-next">${ico("restore")}${esc(L("Следующая", "Next"))} ${esc(until(st.next_due))}</span>` : "";
    const paused = st && st.paused ? `<span class="sync-hero-next warn">${esc(L("Автоматические копии на паузе (безопасный режим)", "Automatic backups are paused (safe mode)"))}</span>` : "";
    const pct = running && running.total ? Math.min(100, Math.round((running.done || 0) * 100 / running.total)) : null;
    const progress = running ? `<div class="sync-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100"${pct !== null ? ` aria-valuenow="${pct}"` : ""}>
        <i style="${pct !== null ? `width:${pct}%` : ""}" class="${pct === null ? "indet" : ""}"></i></div>
        <small class="sync-progress-text">${pct !== null && running.phase === "upload" ? esc(`${bytes(running.done)} / ${bytes(running.total)}`) : pct !== null ? esc(pct + "%") : ""}</small>` : "";
    const actions = running
      ? `<button type="button" class="pbtn ghost sx-btn-sm" data-act="cancel">${ico("stop")} ${esc(L("Остановить", "Stop"))}</button>`
      : lostAccess && (s.client_id || (st && st.bundled_client))
      ? `<button type="button" class="pbtn sx-btn-sm" data-act="connect">${ico("drive")} ${esc(L("Подключить", "Connect"))}</button>`
      : `<button type="button" class="pbtn sx-btn-sm" data-act="backup" ${ready ? "" : "disabled"}>${ico("upload")} ${esc(L("Сохранить сейчас", "Back up now"))}</button>
         <button type="button" class="pbtn ghost sx-btn-sm" data-sx-open="sync-restore" ${ready ? "" : "disabled"}>${ico("restore")} ${esc(L("Восстановить…", "Restore…"))}</button>`;
    return `<div class="sync-hero" data-tone="${tone}">
      <span class="sync-hero-icon">${ico(iconName)}</span>
      <div class="sync-hero-body"><b class="sync-hero-title">${esc(title)}</b>${text ? `<p class="sync-hero-text">${esc(text)}</p>` : ""}
        ${progress}<div class="sync-hero-meta">${next}${paused}</div></div>
      <div class="sync-hero-actions">${actions}</div>
    </div>`;
  }

  function driveBlock(K) {
    const s = settings();
    const flow = (st && st.auth_flow) || {};
    if (st && st.connected) {
      const a = st.account || {};
      const quota = a.quota_limit ? `<div class="sync-quota" title="${esc(`${bytes(a.quota_usage)} / ${bytes(a.quota_limit)}`)}"><i style="width:${Math.min(100, Math.round((a.quota_usage || 0) * 100 / a.quota_limit))}%"></i></div>
        <small class="sync-muted">${esc(L(`Занято ${bytes(a.quota_usage)} из ${bytes(a.quota_limit)}`, `${bytes(a.quota_usage)} of ${bytes(a.quota_limit)} used`))}</small>` : "";
      return K.row({ label: `${ico("drive")} ${esc(a.email || L("Google Drive подключён", "Google Drive connected"))}`, desc: (a.name ? esc(a.name) + "<br>" : "") + quota, keywords: "google account аккаунт drive disconnect отключить",
          control: `<button type="button" class="pbtn ghost sx-btn-sm" data-act="disconnect">${esc(L("Отключить", "Disconnect"))}</button>` })
        + K.row({ label: L("Где на Диске", "Where in Drive"), keywords: "appdata hidden скрытая папка folder visible видимая",
          desc: s.drive_space === "folder" ? L("Папка «MemuAI Backups» видна в Google Диске; MemuAI видит только свои файлы.", "The «MemuAI Backups» folder is visible in Google Drive; MemuAI sees only its own files.")
            : L("Скрытая папка приложения: копии не мешают в Диске и не видны другим программам. Место всё равно считается в квоте.", "A hidden app folder: backups don't clutter your Drive and other apps can't see them. They still count towards your quota."),
          control: K.select("sync-drive-space", [["appdata", L("Скрытая папка (рекомендуется)", "Hidden app folder (recommended)")], ["folder", L("Видимая папка «MemuAI Backups»", "Visible «MemuAI Backups» folder")]], s.drive_space, { width: 270 }) });
    }
    if (flow.state === "waiting") {
      return `<div class="sync-wait">${ico("link")}<div><b>${esc(L("Войдите в Google в открывшемся браузере", "Sign in to Google in the browser that opened"))}</b>
        <p class="sync-muted">${esc(L("После разрешения доступа вернитесь сюда — MemuAI подхватит вход сам.", "After you allow access, come back here — MemuAI picks it up by itself."))}</p>
        ${K.actions(`<a class="pbtn ghost sx-btn-sm" href="${esc(flow.auth_url || "#")}" target="_blank" rel="noopener">${esc(L("Открыть страницу входа ещё раз", "Open the sign-in page again"))}</a>
          <button type="button" class="pbtn ghost sx-btn-sm" data-act="auth-cancel">${esc(L("Отмена", "Cancel"))}</button>`)}</div></div>`;
    }
    const failed = flow.state === "error" ? K.note(esc(isEn() ? flow.error_en : flow.error_ru), "danger") : "";
    const bundled = st && st.bundled_client;
    const hasClient = !!s.client_id || bundled;
    const clientFields = `${K.row({ label: "OAuth Client ID", stack: true, keywords: "client id oauth google cloud console",
        desc: L("Google требует собственный «ключ приложения» (тип «Приложение для ПК»). Это 5 минут один раз — пошагово в справке.", "Google requires an app key of type «Desktop app». It takes 5 minutes once — step by step in the help."),
        control: K.input("sync-client-id", s.client_id || "", { mono: true, placeholder: "123456789-abc.apps.googleusercontent.com" }) })}
      ${K.row({ label: "Client secret", stack: true, keywords: "client secret",
        desc: s.has_client_secret ? L("Сохранён в защищённом хранилище Windows. Введите новый, чтобы заменить.", "Stored in the protected Windows store. Type a new one to replace it.") : L("Для клиента типа «Приложение для ПК» Google тоже выдаёт секрет — вставьте его сюда.", "Google also issues a secret for a «Desktop app» client — paste it here."),
        control: K.secret("sync-client-secret", "", { placeholder: s.has_client_secret ? "••••••••" : "GOCSPX-…" }) })}
      ${K.actions(`<button type="button" class="pbtn ghost sx-btn-sm" data-act="client-save">${ico("check")} ${esc(L("Сохранить ключ", "Save the key"))}</button>
        <button type="button" class="pbtn ghost sx-btn-sm" data-act="docs">${esc(L("Как получить Client ID", "How to get a Client ID"))}</button>${window.MemuGuideVideo ? MemuGuideVideo.button("google-client", { lang: isEn() ? "en" : "ru", label: L("Видео", "Video") }) : ""}`, "sync-row-actions")}`;
    return `${failed}<div class="sync-connect">
        <span class="sync-connect-icon">${ico("drive")}</span>
        <div><b>${esc(L("Подключите Google Drive", "Connect Google Drive"))}</b>
          <p class="sync-muted">${esc(L("MemuAI получит доступ только к своей папке — остальные файлы Диска ему не видны.", "MemuAI gets access to its own folder only — it cannot see the rest of your Drive."))}</p></div>
        <button type="button" class="pbtn sx-btn-sm" data-act="connect" ${hasClient ? "" : "disabled"}>${esc(L("Подключить", "Connect"))}</button>
      </div>
      ${bundled && !s.client_id ? K.more({ id: "sync-own-client", title: L("Свой OAuth-клиент", "Your own OAuth client"), hint: L("необязательно", "optional"), body: clientFields }) : clientFields}`;
  }

  function folderBlock(K) {
    const s = settings();
    const list = folders || [];
    const chips = list.length ? `<div class="sync-folders">${list.map((row) => `<button type="button" class="sync-folder${s.folder_path === row.path ? " is-on" : ""}" data-act="pick-detected" data-path="${esc(row.path)}">
        ${ico(row.kind === "gdrive" ? "drive" : "folder")}<span><b>${esc(isEn() ? row.en : row.ru)}</b><small>${esc(row.path)}</small></span></button>`).join("")}</div>` : "";
    return `${chips}${K.row({ label: L("Папка для копий", "Backup folder"), keywords: "folder папка path путь google drive for desktop onedrive dropbox",
        desc: s.folder_path ? `<span class="sync-mono">${esc(s.folder_path)}</span> ${esc(L("→ подпапка «MemuAI Backups»", "→ subfolder «MemuAI Backups»"))}` : L("Лучше всего — папка «Google Диск» программы Google Drive для компьютера: файлы сами уйдут в облако без настройки OAuth.", "Best of all — the «Google Drive» folder of Google Drive for desktop: files reach the cloud by themselves, no OAuth setup."),
        control: `<button type="button" class="pbtn ghost sx-btn-sm" data-act="pick-folder">${ico("folder")} ${esc(L("Выбрать…", "Choose…"))}</button>` })}
      ${s.folder_path ? K.row({ label: L("Проверить доступ", "Check access"), desc: L("Записать и прочитать пробный файл", "Write and read back a probe file"), keywords: "test проверить",
        control: `<button type="button" class="pbtn ghost sx-btn-sm" data-act="test">${esc(L("Проверить", "Check"))}</button>` }) : ""}`;
  }

  function categoryRows(K) {
    const s = settings();
    const cats = ((st && st.categories) || []).filter((c) => c.id !== "secrets");
    const size = (id) => stats && stats.categories && stats.categories[id] ? stats.categories[id] : null;
    return cats.map((c) => {
      const info = size(c.id);
      const meta = info ? `<span class="sync-size">${esc(info.files ? `${bytes(info.bytes)} · ${info.files}` : L("пусто", "empty"))}</span>` : "";
      return K.row({ label: esc(isEn() ? c.en : c.ru) + meta, desc: esc(isEn() ? c.desc_en : c.desc_ru), key: `sync.cat.${c.id}`, def: !!c.default,
        keywords: `sync category ${c.id} ${c.ru} ${c.en}`, control: K.toggle(`sync-cat-${c.id}`, !!(s.categories || {})[c.id], `data-cat="${esc(c.id)}"`) });
    }).join("");
  }

  function render() {
    const K = kit();
    if (!K) return `<div id="sync-root"></div>`;
    const s = settings();
    const presets = (st && st.presets) || [15, 30, 60, 180, 360, 720, 1440, 4320, 10080];
    const every = (m) => (m === 60 ? L("Каждый час", "Every hour") : m === 1440 ? L("Каждый день", "Every day") : m === 10080 ? L("Каждую неделю", "Every week") : L(`Каждые ${interval(m)}`, `Every ${interval(m)}`));
    const intervalOptions = presets.map((m) => [String(m), every(m)]);
    if (!presets.includes(Number(s.interval_minutes)) && s.interval_minutes) intervalOptions.push([String(s.interval_minutes), every(Number(s.interval_minutes))]);
    const secretsInfo = stats && stats.categories && stats.categories.secrets;
    const skipped = stats && stats.skipped && stats.skipped.length ? K.note(esc(L(`Слишком большие файлы не попадут в копию (${stats.skipped.length}): `, `Files too large to back up (${stats.skipped.length}): `))
      + stats.skipped.slice(0, 4).map((r) => `<span class="sync-mono">${esc(r.rel)}</span> (${esc(bytes(r.size))})`).join(", "), "warn") : "";
    const history = ((st && st.history) || []).slice(0, 12).map((h) => `<div class="sync-log-row" data-ok="${h.ok ? 1 : 0}">
        <span class="sync-log-dot"></span><span class="sync-log-time">${esc(when(h.ts))}</span>
        <span>${esc(h.kind === "restore" ? L("Восстановление", "Restore") : L("Копия", "Backup"))} · ${esc(reason(h.reason))}</span>
        <span class="sync-muted">${esc(h.ok ? (h.unchanged ? L("без изменений", "no changes") : h.kind === "restore" ? nFiles(h.files) : bytes(h.new_bytes || 0)) : errText(h.error))}</span></div>`).join("");
    return `<div id="sync-root" class="sync-root">
      ${hero()}

      ${K.section({ id: "sync-where", title: L("Где хранить", "Where to store"), keywords: "google drive folder папка target хранилище",
        body: K.row({ label: L("Хранилище", "Storage"), keywords: "target drive folder",
            control: K.segmented("sync-target", [{ value: "drive", label: "Google Drive", icon: ico("drive") }, { value: "folder", label: L("Папка", "Folder"), icon: ico("folder") }], s.target || "drive", { id: "sync-target", label: L("Хранилище", "Storage") }) })
          + (s.target === "folder" ? folderBlock(K) : driveBlock(K)) })}

      ${K.section({ id: "sync-schedule", title: L("Расписание", "Schedule"), keywords: "schedule расписание auto автоматически interval интервал",
        body: K.row({ label: L("Сохранять копии автоматически", "Back up automatically"), key: "sync.enabled", def: false,
            desc: L("Работает, пока открыт MemuAI или MemuAI Proxy в трее", "Works while MemuAI or MemuAI Proxy (tray) is running"),
            keywords: "enabled включить auto backup", control: K.toggle("sync-enabled", !!s.enabled) })
          + K.row({ label: L("Как часто", "How often"), when: "sync-enabled", keywords: "interval период частота every каждые",
            control: K.select("sync-interval", intervalOptions, String(s.interval_minutes || 1440), { width: 200 }) })
          + (Number(s.interval_minutes) >= 1440 ? K.row({ label: L("В какое время", "At what time"), when: "sync-enabled", keywords: "time время at",
            desc: L("Пусто — в любое время. Если компьютер выключен, копия будет сделана при следующем запуске.", "Empty — any time. If the PC is off, the backup runs at the next start."),
            control: K.input("sync-at-time", s.at_time || "", { type: "time", width: 120 }) }) : "")
          + K.row({ label: L("Сохранять при выходе из MemuAI", "Back up when MemuAI closes"), key: "sync.on_exit", def: true, keywords: "exit выход on exit закрытие",
            desc: L("Если Proxy работает в трее, копию сделает он — окно закроется сразу", "If Proxy runs in the tray it makes the backup — the window closes at once"),
            control: K.toggle("sync-on-exit", s.on_exit !== false) })
          + K.row({ label: L("Только если что-то изменилось", "Only when something changed"), key: "sync.only_changed", def: true, keywords: "changed изменения incremental",
            desc: L("Без изменений новая версия не создаётся; загружаются всегда только изменённые файлы", "No new version without changes; only changed files are ever uploaded"),
            control: K.toggle("sync-only-changed", s.only_changed !== false) }) })}

      ${K.section({ id: "sync-what", title: L("Что сохранять", "What to back up"), keywords: "categories разделы include что сохранять",
        desc: L("Программа, логи, кэши, профили браузера и скачанные модели не копируются никогда — их можно скачать снова.", "The program, logs, caches, browser profiles and downloaded models are never copied — they can be downloaded again."),
        body: categoryRows(K)
          + K.row({ label: `${ico("lock")} ${esc(L("Ключи и токены", "Keys & tokens"))}${secretsInfo && secretsInfo.files ? `<span class="sync-size">${esc(bytes(secretsInfo.bytes))} · ${secretsInfo.files}</span>` : ""}`,
            desc: s.encrypt ? L("Аккаунты провайдеров, ключи API, токены интеграций и MCP. Сохраняются только в зашифрованном виде.", "Provider accounts, API keys, integration and MCP tokens. Stored encrypted only.")
              : L("Доступно только с шифрованием (ниже). Без него токены вырезаются даже из файлов настроек, а при восстановлении остаются ваши текущие.", "Only with encryption (below). Without it tokens are cut even out of settings files, and a restore keeps your current ones."),
            keywords: "secrets keys tokens ключи токены accounts аккаунты", control: K.toggle("sync-secrets", !!s.include_secrets, s.encrypt ? "" : "disabled") })
          + skipped })}

      ${K.section({ id: "sync-versions", title: L("Версии", "Versions"), keywords: "versions версии keep хранить retention",
        body: K.row({ label: L("Сколько версий хранить", "Versions to keep"), key: "sync.keep_versions", def: 14, keywords: "keep versions количество",
            desc: L("Отмеченные звёздочкой копии не удаляются никогда. Благодаря дедупликации версии почти не занимают лишнего места.", "Starred backups are never deleted. Thanks to deduplication extra versions take little space."),
            control: K.input("sync-keep", String(s.keep_versions || 14), { type: "number", width: 90, attrs: 'min="1" max="500"' }) })
          + K.row({ label: L("Удалять копии старше", "Delete backups older than"), keywords: "days дни age возраст",
            control: K.select("sync-keep-days", [["0", L("Не удалять по возрасту", "Never by age")], ["7", L("недели", "a week")], ["30", L("месяца", "a month")], ["90", L("3 месяцев", "3 months")], ["365", L("года", "a year")]], String(s.keep_days || 0), { width: 260 }) }) })}

      ${K.section({ id: "sync-crypto", title: L("Шифрование", "Encryption"), keywords: "encryption шифрование passphrase пароль",
        body: K.row({ label: L("Шифровать копии паролем", "Encrypt backups with a passphrase"), keywords: "encrypt шифровать",
            desc: L("Без пароля содержимое копий никто не прочитает — ни Google, ни тот, кто получит доступ к папке.", "Without the passphrase nobody can read the backups — not Google, not anyone with access to the folder."),
            control: K.toggle("sync-encrypt", !!s.encrypt) })
          + K.row({ label: s.has_passphrase ? L("Сменить пароль", "Change the passphrase") : L("Пароль", "Passphrase"), stack: true, keywords: "passphrase пароль",
            desc: L("Не короче 8 символов. Хранится в защищённом хранилище Windows, чтобы копии делались сами. Новый пароль действует для новых копий; для старых нужен прежний.", "At least 8 characters. Kept in the protected Windows store so backups run by themselves. A new passphrase applies to new backups; older ones need the old one."),
            control: `<div class="sync-pass">${K.secret("sync-pass-1", "", { placeholder: L("Новый пароль", "New passphrase") })}${K.secret("sync-pass-2", "", { placeholder: L("Ещё раз", "Repeat") })}
              <button type="button" class="pbtn ghost sx-btn-sm" data-act="pass-save">${esc(L("Сохранить", "Save"))}</button></div>` })
          + K.note(L("Если забыть пароль, зашифрованные копии восстановить невозможно — запишите его в надёжном месте.", "If you forget the passphrase, encrypted backups cannot be restored — keep it somewhere safe."), "warn") })}

      ${K.section({ id: "sync-advanced", card: false, body: K.more({ id: "sync-more", title: L("Дополнительно", "Advanced"), body: `<div class="sx-card">
        ${K.row({ label: L("Ограничение скорости загрузки", "Upload speed limit"), keywords: "bandwidth скорость limit канал",
          desc: L("Чтобы копия не мешала видеозвонкам и играм", "So backups don't disturb video calls and games"),
          control: K.select("sync-bw", [["0", L("Без ограничения", "Unlimited")], ["256", "256 KB/s"], ["512", "512 KB/s"], ["1024", "1 MB/s"], ["2048", "2 MB/s"], ["5120", "5 MB/s"]], String(s.bandwidth_kbps || 0), { width: 180 }) })}
        ${K.row({ label: L("Максимальный размер файла", "Largest file to include"), keywords: "max size размер файла limit",
          control: K.select("sync-max-mb", [["25", "25 MB"], ["50", "50 MB"], ["100", "100 MB"], ["250", "250 MB"], ["500", "500 MB"], ["1024", "1 GB"]], String(s.max_file_mb || 100), { width: 140 }) })}
        ${K.row({ label: L("Имя этого компьютера", "This computer's name"), keywords: "device name компьютер устройство",
          desc: L("Видно в списке копий, если вы сохраняете с нескольких ПК в один аккаунт", "Shown in the backup list when several PCs use one account"),
          control: K.input("sync-device", s.device_name || "", { width: 200, placeholder: (st && st.device && st.device.name) || "" }) })}
        ${K.row({ label: L("Ждать копию при выходе не дольше", "Wait for the exit backup at most"), keywords: "exit timeout",
          desc: L("Когда Proxy не запущен, окно ждёт окончания копии", "When Proxy isn't running the window waits for the backup"),
          control: K.select("sync-exit-timeout", [["15", L("15 секунд", "15 seconds")], ["30", L("30 секунд", "30 seconds")], ["60", L("1 минуту", "1 minute")], ["180", L("3 минуты", "3 minutes")]], String(s.exit_timeout_s || 60), { width: 150 }) })}
        ${K.row({ label: L("Уведомлять об ошибках", "Notify about errors"), key: "sync.notify_errors", def: true, keywords: "notify уведомления errors ошибки",
          control: K.toggle("sync-notify", s.notify_errors !== false) })}
        ${K.row({ label: L("Не копировать", "Exclude"), stack: true, keywords: "exclude исключить patterns шаблоны",
          desc: L("Шаблоны путей внутри папки данных, по одному в строке, например memuai/modules/big-module/*", "Path patterns inside the data folder, one per line, e.g. memuai/modules/big-module/*"),
          control: K.textarea("sync-exclude", (s.exclude_patterns || []).join("\n"), { rows: 3, placeholder: "memuai/companion/models/*" }) })}
        ${history ? `<div class="sync-log"><div class="sync-log-head">${esc(L("Журнал", "Log"))}</div>${history}</div>` : ""}
      </div>` }) })}

      ${K.link({ sub: "sync-restore", icon: ico("restore"), label: L("Восстановление и список копий", "Restore and the backup list"), keywords: "restore восстановить history список копий versions",
        desc: L("Выберите версию и разделы; перед восстановлением MemuAI сохранит страховочную копию текущих данных", "Pick a version and categories; MemuAI saves a safety copy of the current data first") })}
      ${K.sub({ id: "sync-restore", title: L("Восстановление", "Restore"), desc: L("Копии, сохранённые в выбранном хранилище", "Backups in the selected storage"),
        body: `<div id="sync-restore-body">${restoreBody(K)}</div>` })}
    </div>`;
  }

  const sameMinute = (row) => ((snaps && snaps.snapshots) || []).some((o) => o !== row && Math.floor((o.created_ts || 0) / 60) === Math.floor((row.created_ts || 0) / 60));
  function snapRow(row) {
    const st_ = row.stats || {};
    const chips = [
      row.own ? "" : `<span class="sync-tag">${esc(row.device || "?")}</span>`,
      `<span class="sync-tag">${esc(reason(row.reason))}</span>`,
      row.encrypted ? `<span class="sync-tag" title="${esc(L("Зашифрована", "Encrypted"))}">${ico("lock")}</span>` : "",
      row.secrets ? `<span class="sync-tag">${esc(L("с ключами", "with keys"))}</span>` : "",
      row.complete ? "" : `<span class="sync-tag err">${esc(L("неполная", "incomplete"))}</span>`,
      row.label ? `<span class="sync-tag">${esc(row.label)}</span>` : "",
    ].join("");
    return `<div class="sync-snap${row.pinned ? " is-pinned" : ""}" data-id="${esc(row.id)}">
      <button type="button" class="sync-star" data-act="pin" data-id="${esc(row.id)}" aria-pressed="${row.pinned ? "true" : "false"}" title="${esc(row.pinned ? L("Не удалять никогда — снять", "Never delete — unpin") : L("Не удалять никогда", "Never delete"))}">${ico("pin")}</button>
      <div class="sync-snap-main"><b>${esc(when(row.created_ts, sameMinute(row)))}</b><small>${esc(ago(row.created_ts))} · ${esc(`${nFiles(st_.files)}, ${bytes(st_.bytes)}`)}</small><div class="sync-tags">${chips}</div></div>
      <div class="sync-snap-actions">
        <button type="button" class="pbtn sx-btn-sm" data-act="choose" data-id="${esc(row.id)}" ${row.complete ? "" : "disabled"}>${esc(L("Восстановить…", "Restore…"))}</button>
        <button type="button" class="sync-icon-btn" data-act="export" data-id="${esc(row.id)}" title="${esc(L("Скачать как .zip", "Download as .zip"))}" aria-label="${esc(L("Скачать как .zip", "Download as .zip"))}">${ico("download")}</button>
        ${row.own ? `<button type="button" class="sync-icon-btn danger" data-act="delete" data-id="${esc(row.id)}" title="${esc(L("Удалить", "Delete"))}" aria-label="${esc(L("Удалить", "Delete"))}">${ico("trash")}</button>` : ""}
      </div></div>`;
  }

  function restorePanel(K) {
    const sel = restoreSel;
    const h = sel.header || {};
    const cats = Object.entries(sel.categories || {}).sort((a, b) => (st.categories || []).findIndex((c) => c.id === a[0]) - (st.categories || []).findIndex((c) => c.id === b[0]));
    const rows = cats.map(([id, info]) => `<label class="sync-pick">
        <input type="checkbox" data-pick="${esc(id)}" ${sel.chosen.has(id) ? "checked" : ""}>
        <span><b>${esc(catName(id))}</b><small>${esc(`${nFiles(info.files)}, ${bytes(info.bytes)}`)}${info.changed ? esc(L(` · отличается: ${info.changed}`, ` · differs: ${info.changed}`)) : (sel.kind === "snapshot" ? esc(L(" · совпадает", " · same as now")) : "")}</small></span></label>`).join("");
    return `<div class="sync-restore-card">
      <div class="sync-restore-head"><b>${esc(sel.kind === "file" ? (sel.file || L("Файл копии", "Backup file")) : when(h.created_ts))}</b>
        <small>${esc([h.device, h.app_version ? "MemuAI " + h.app_version : "", reason(h.reason)].filter(Boolean).join(" · "))}</small></div>
      ${sel.needPass ? K.row({ label: L("Пароль копии", "Backup passphrase"), stack: true, desc: L("Эта копия зашифрована другим паролем", "This backup was encrypted with a different passphrase"),
        control: `<div class="sync-pass">${K.secret("sync-restore-pass", "", {})}<button type="button" class="pbtn ghost sx-btn-sm" data-act="unlock">${esc(L("Открыть", "Unlock"))}</button></div>` }) : `
      <p class="sync-muted">${esc(L("Что вернуть:", "What to bring back:"))}</p>
      <div class="sync-picks">${rows || esc(L("В копии нет файлов", "The backup has no files"))}</div>
      ${K.row({ label: L("Удалить то, чего не было в копии", "Remove what the backup didn't have"), keywords: "mirror зеркало",
        desc: L("В выбранных разделах будут удалены файлы, появившиеся после копии (например, новые модули). Они тоже попадут в страховочную копию.", "In the chosen categories, files created after the backup (e.g. new modules) are removed. They also go into the safety copy."),
        control: K.toggle("sync-mirror", !!sel.mirror) })}
      ${K.note(L("Перед восстановлением текущие версии файлов будут сохранены в страховочную копию — её можно вернуть одним нажатием. После восстановления перезапустите MemuAI.", "Before restoring, the current versions of the files are saved to a safety copy — you can bring them back with one click. Restart MemuAI after restoring."), "info")}
      ${K.actions(`<button type="button" class="pbtn ghost sx-btn-sm" data-act="restore-back">${esc(L("Назад к списку", "Back to the list"))}</button>
        <button type="button" class="pbtn sx-btn-sm" data-act="restore-go" ${sel.chosen.size ? "" : "disabled"}>${ico("restore")} ${esc(L("Восстановить", "Restore"))}</button>`)}`}
    </div>`;
  }

  function restoreBody(K) {
    if (!K) return "";
    if (restoreSel && restoreSel.done) {
      const r = restoreSel.done;
      return `<div class="sync-restore-card">${K.note(r.ok ? esc(L(`Восстановлено файлов: ${r.written}${r.removed ? `, удалено: ${r.removed}` : ""}.`, `Files restored: ${r.written}${r.removed ? `, removed: ${r.removed}` : ""}.`))
          : esc(L("Восстановлено с ошибками: ", "Restored with errors: ")) + (r.errors || []).map(esc).join("; "), r.ok ? "ok" : "warn")}
        <p class="sync-muted">${esc(L("Перезапустите MemuAI, чтобы все разделы подхватили восстановленные данные.", "Restart MemuAI so every part picks up the restored data."))}</p>
        ${K.actions(`<button type="button" class="pbtn sx-btn-sm" data-act="restart">${esc(L("Перезапустить MemuAI", "Restart MemuAI"))}</button>
          <button type="button" class="pbtn ghost sx-btn-sm" data-act="undo" data-name="${esc(r.safety || "")}">${ico("undo")} ${esc(L("Отменить восстановление", "Undo the restore"))}</button>
          <button type="button" class="pbtn ghost sx-btn-sm" data-act="restore-back">${esc(L("К списку копий", "Back to the list"))}</button>`)}</div>`;
    }
    if (restoreSel) return restorePanel(K);
    const list = snapsError ? K.note(esc(snapsError), "danger")
      : snaps === null ? `<div class="sync-loading">${esc(L("Загружаю список копий…", "Loading the backup list…"))}</div>`
      : snaps.snapshots && snaps.snapshots.length ? `<div class="sync-snaps">${snaps.snapshots.map(snapRow).join("")}</div>
          <p class="sync-muted sync-total">${esc(L(`Всего в хранилище: ${bytes(snaps.total_bytes)} · ${snaps.target}`, `Total in storage: ${bytes(snaps.total_bytes)} · ${snaps.target}`))}</p>`
      : `<div class="sync-empty">${ico("cloud")}<p>${esc(L("Копий пока нет. Нажмите «Сохранить сейчас» на главной странице раздела.", "No backups yet. Press «Back up now» on the main page of this section."))}</p></div>`;
    const safetyRows = safety.length ? `<div class="sync-safety">${safety.map((row) => `<div class="sync-safety-row">${ico("undo")}
        <span><b>${esc(when(Date.parse(row.created || "") / 1000) || row.name)}</b><small>${esc(`${nFiles(row.files)}, ${bytes(row.size)}`)}</small></span>
        <button type="button" class="pbtn ghost sx-btn-sm" data-act="undo" data-name="${esc(row.name)}">${esc(L("Вернуть", "Bring back"))}</button></div>`).join("")}</div>` : "";
    return `${K.actions(`<button type="button" class="pbtn ghost sx-btn-sm" data-act="snaps-refresh">${ico("restore")} ${esc(L("Обновить", "Refresh"))}</button>
        <button type="button" class="pbtn ghost sx-btn-sm" data-act="import-file">${ico("file")} ${esc(L("Восстановить из файла…", "Restore from a file…"))}</button>`, "sync-row-actions")}
      ${list}
      ${safetyRows ? `<h4 class="sync-subhead">${esc(L("Страховочные копии", "Safety copies"))}</h4><p class="sync-muted">${esc(L("Данные, какими они были перед каждым восстановлением (последние 5).", "Your data as it was before each restore (last 5)."))}</p>${safetyRows}` : ""}`;
  }

  async function loadStatus() {
    try { st = await call("/api/pg/sync/status"); } catch (e) { st = st || null; }
    return st;
  }
  async function loadStats() {
    try { stats = await call("/api/pg/sync/stats"); } catch (e) { stats = null; }
  }
  async function loadFolders() {
    try { folders = (await call("/api/pg/sync/folders")).folders || []; } catch (e) { folders = []; }
  }
  async function loadSnaps() {
    snapsError = "";
    snaps = null;
    paintRestore();
    try { snaps = await call("/api/pg/sync/snapshots"); } catch (e) { snaps = { snapshots: [] }; snapsError = e.message; }
    try { safety = (await call("/api/pg/sync/safety")).items || []; } catch (e) { safety = []; }
    paintRestore();
  }

  function paintRestore() {
    const body = document.getElementById("sync-restore-body");
    if (body) body.innerHTML = restoreBody(kit());
  }

  function paint() {
    const node = root();
    if (!node) return;
    const openSub = node.querySelector(".sx-sub:not([hidden])")?.dataset.sxSub || "";
    const scroll = document.querySelector(".sx-shell .settings-body")?.scrollTop || 0;
    const tmp = document.createElement("div");
    tmp.innerHTML = render();
    const fresh = tmp.firstElementChild;
    node.replaceWith(fresh);
    if (openSub) {

      const sub = fresh.querySelector(`.sx-sub[data-sx-sub="${openSub}"]`);
      if (sub) {
        sub.hidden = false;
        let item = sub;
        while (item && item !== fresh) {
          const parent = item.parentElement;
          if (!parent) break;
          [...parent.children].forEach((child) => { if (child !== item) child.classList.add("sx-sub-hidden"); });
          item = parent;
        }
      }
    }
    try { if (typeof window.enhanceSelects === "function") window.enhanceSelects(fresh); } catch (e) {}
    bind(fresh);
    try { kit().refreshDeps && kit().refreshDeps(); kit().refreshModified && kit().refreshModified(); } catch (e) {}
    const body = document.querySelector(".sx-shell .settings-body");
    if (body) body.scrollTop = scroll;
  }

  function paintHero() {
    const node = root();
    const hero_ = node && node.querySelector(".sync-hero");
    if (!hero_) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = hero();
    hero_.replaceWith(tmp.firstElementChild);
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    const visible = !!root() && root().offsetParent !== null;
    if (!visible) return;
    const waiting = st && st.auth_flow && st.auth_flow.state === "waiting";
    const running = st && st.running;
    if (!waiting && !running) return;
    pollTimer = setTimeout(async () => {
      const wasRunning = !!(st && st.running), wasConnected = !!(st && st.connected), wasWaiting = waiting;
      await loadStatus();
      if (wasConnected !== !!(st && st.connected) || (wasWaiting && !(st.auth_flow && st.auth_flow.state === "waiting"))) {
        paint();
        if (st.connected) say(L("Google Drive подключён", "Google Drive connected"));
      } else paintHero();
      if (wasRunning && !(st && st.running)) {
        if (st.last_error && st.last_error.ts >= (st.last_attempt || 0)) say(errText(st.last_error), "error");
        else if (st.last_result) say(st.last_result.unchanged ? L("Изменений нет — копия актуальна", "Nothing changed — the backup is current") : L("Копия сохранена", "Backup saved"));
        loadStats().then(() => paint());
        if (snaps !== null) loadSnaps();
      }
      schedulePoll();
    }, 1000);
  }

  async function save(updates, { repaint = false, quiet = false } = {}) {
    try {
      const data = await call("/api/pg/sync/settings", updates);
      if (st) st.settings = data.settings;
      try { kit().saved && kit().saved(true); } catch (e) {}
      if (repaint) { await loadStatus(); paint(); } else paintHero();
      return true;
    } catch (error) {
      if (!quiet) say(error.message, "error");
      await loadStatus();
      paint();
      return false;
    }
  }

  function bind(node) {
    if (!node || node.dataset.bound) return;
    node.dataset.bound = "1";
    node.addEventListener("change", onChange);
    node.addEventListener("click", onClick);
  }

  function onChange(event) {
    const t = event.target;
    const id = t.id || "";
    if (t.name === "sync-target") { save({ target: t.value }, { repaint: true }); if (t.value === "folder" && folders === null) loadFolders().then(paint); return; }
    if (t.dataset.cat) { save({ categories: { [t.dataset.cat]: t.checked } }); setTimeout(() => loadStats().then(() => {}), 0); return; }
    if (t.dataset.pick) { if (t.checked) restoreSel.chosen.add(t.dataset.pick); else restoreSel.chosen.delete(t.dataset.pick); paintRestore(); return; }
    switch (id) {
      case "sync-enabled": save({ enabled: t.checked }); break;
      case "sync-interval": save({ interval_minutes: Number(t.value) }, { repaint: true }); break;
      case "sync-at-time": save({ at_time: t.value || "" }); break;
      case "sync-on-exit": save({ on_exit: t.checked }); break;
      case "sync-only-changed": save({ only_changed: t.checked }); break;
      case "sync-secrets": save({ include_secrets: t.checked }); break;
      case "sync-keep": save({ keep_versions: Number(t.value) || 14 }); break;
      case "sync-keep-days": save({ keep_days: Number(t.value) || 0 }); break;
      case "sync-bw": save({ bandwidth_kbps: Number(t.value) || 0 }); break;
      case "sync-max-mb": save({ max_file_mb: Number(t.value) || 100 }).then(() => loadStats().then(paint)); break;
      case "sync-device": save({ device_name: t.value.trim() }); break;
      case "sync-exit-timeout": save({ exit_timeout_s: Number(t.value) || 60 }); break;
      case "sync-notify": save({ notify_errors: t.checked }); break;
      case "sync-exclude": save({ exclude_patterns: t.value.split(/\n+/).map((x) => x.trim()).filter(Boolean) }); break;
      case "sync-drive-space": save({ drive_space: t.value }); break;
      case "sync-mirror": if (restoreSel) restoreSel.mirror = t.checked; break;
      case "sync-encrypt":
        if (t.checked && !settings().has_passphrase) {
          t.checked = false;
          say(L("Сначала задайте пароль шифрования", "Set an encryption passphrase first"), "error");
          document.getElementById("sync-pass-1")?.focus();
          break;
        }
        save(t.checked ? { encrypt: true } : { encrypt: false, include_secrets: false }, { repaint: true });
        break;
      default: break;
    }
  }

  async function guarded(name, fn) {
    if (busyAction) return;
    busyAction = name;
    try { await fn(); } catch (error) { say(error.message || String(error), "error"); } finally { busyAction = ""; }
  }

  function confirmBox(text, okText, danger) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const node = document.createElement("div");
      node.className = "sync-confirm";
      node.innerHTML = `<div class="sync-confirm-card" role="alertdialog" aria-modal="true" aria-describedby="sync-confirm-text">
        <p id="sync-confirm-text">${esc(text)}</p>
        <div class="sync-confirm-actions"><button type="button" class="pbtn ghost" data-no>${esc(L("Отмена", "Cancel"))}</button>
          <button type="button" class="pbtn${danger ? " danger" : ""}" data-yes>${esc(okText)}</button></div></div>`;
      const finish = (value) => {
        node.remove();
        document.removeEventListener("keydown", onKey, true);
        try { opener && opener.focus && opener.focus({ preventScroll: true }); } catch (e) {}
        resolve(value);
      };
      const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(false); }
        if (event.key === "Tab") {
          const items = [...node.querySelectorAll("button")];
          const i = items.indexOf(document.activeElement);
          event.preventDefault();
          items[(i + (event.shiftKey ? items.length - 1 : 1)) % items.length].focus();
        }
      };
      node.addEventListener("click", (event) => {
        if (event.target.closest("[data-yes]")) finish(true);
        else if (event.target.closest("[data-no]") || event.target === node) finish(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(node);
      requestAnimationFrame(() => node.querySelector("[data-yes]").focus());
    });
  }

  async function chooseSnapshot(id, passphrase) {
    const row = (snaps && snaps.snapshots || []).find((r) => r.id === id) || {};
    try {
      const data = await call("/api/pg/sync/preview", { id, passphrase: passphrase || "" });
      const chosen = new Set(Object.keys(data.categories || {}).filter((c) => c !== "secrets" || row.secrets));
      restoreSel = { kind: "snapshot", id, header: { ...(data.header || {}), created_ts: data.header?.created_ts || row.created_ts }, categories: data.categories || {}, chosen, mirror: false, passphrase: passphrase || "", needPass: false };
    } catch (error) {
      if (error.code === "need_passphrase") {
        restoreSel = { kind: "snapshot", id, header: { created_ts: row.created_ts, device: row.device, reason: row.reason }, categories: {}, chosen: new Set(), needPass: true };
        if (passphrase) say(error.message, "error");
      } else throw error;
    }
    paintRestore();
  }

  async function onClick(event) {
    const btn = event.target.closest("[data-act]");
    if (!btn || btn.disabled) return;
    const act = btn.dataset.act;
    switch (act) {
      case "backup":
        await guarded(act, async () => {
          await call("/api/pg/sync/backup", {});
          await loadStatus(); paintHero(); schedulePoll();
        });
        break;
      case "cancel":
        await call("/api/pg/sync/cancel", {}).catch(() => {});
        say(L("Останавливаю…", "Stopping…"));
        break;
      case "connect":
        await guarded(act, async () => {
          const data = await call("/api/pg/sync/auth/start", {});
          await loadStatus(); paint(); schedulePoll();
          if (data.auth_url && !st.auth_flow?.state) window.open(data.auth_url, "_blank", "noopener");
        });
        break;
      case "auth-cancel":
        await call("/api/pg/sync/auth/cancel", {}).catch(() => {});
        await loadStatus(); paint();
        break;
      case "disconnect":
        if (!(await confirmBox(L("Отключить аккаунт Google? Копии в Диске останутся, но MemuAI перестанет их делать и видеть. Это один аккаунт на всё — Gmail, Календарь и Диск в «Интеграциях» тоже отключатся.", "Disconnect the Google account? Backups stay in Drive, but MemuAI stops making and seeing them. It is one account for everything — Gmail, Calendar and Drive in Integrations are disconnected too."), L("Отключить", "Disconnect"), true))) return;
        await guarded(act, async () => { await call("/api/pg/sync/auth/disconnect", {}); await loadStatus(); paint(); });
        break;
      case "client-save": {
        const cid = document.getElementById("sync-client-id")?.value.trim() || "";
        const secret = document.getElementById("sync-client-secret")?.value.trim() || "";
        const updates = { client_id: cid };
        if (secret) updates.client_secret = secret;
        if (await save(updates, { repaint: true })) say(L("Ключ приложения сохранён", "App key saved"));
        break;
      }
      case "docs":
        try { if (window.MemuDocs && typeof MemuDocs.open === "function") MemuDocs.open("sync", isEn() ? "google-cloud-client-id" : "ключ-google-client-id"); } catch (e) {}
        break;
      case "pick-folder":
        await guarded(act, async () => {
          const data = await call("/api/pg/sync/pick-folder", {});
          if (data.cancelled) return;
          await loadStatus(); paint(); loadStats().then(paint);
        });
        break;
      case "pick-detected":
        await guarded(act, async () => { await call("/api/pg/sync/pick-folder", { detected: btn.dataset.path }); await loadStatus(); paint(); });
        break;
      case "test":
        await guarded(act, async () => { const data = await call("/api/pg/sync/test", {}); say(L(`Хранилище работает (${data.ms} мс)`, `Storage works (${data.ms} ms)`)); });
        break;
      case "pass-save": {
        const a = document.getElementById("sync-pass-1")?.value || "";
        const b = document.getElementById("sync-pass-2")?.value || "";
        if (a.length < 8) { say(L("Пароль — не короче 8 символов", "The passphrase must be at least 8 characters"), "error"); return; }
        if (a !== b) { say(L("Пароли не совпадают", "The passphrases don't match"), "error"); return; }
        if (await save({ passphrase: a, encrypt: true }, { repaint: true })) say(L("Пароль сохранён, шифрование включено", "Passphrase saved, encryption is on"));
        break;
      }
      case "snaps-refresh": loadSnaps(); break;
      case "pin": {
        const pinned = btn.getAttribute("aria-pressed") !== "true";
        await guarded(act, async () => {
          await call("/api/pg/sync/pin", { id: btn.dataset.id, pinned });
          const row = snaps && snaps.snapshots.find((r) => r.id === btn.dataset.id);
          if (row) row.pinned = pinned;
          paintRestore();
        });
        break;
      }
      case "delete":
        if (!(await confirmBox(L("Удалить эту копию из хранилища? Это нельзя отменить.", "Delete this backup from the storage? This cannot be undone."), L("Удалить", "Delete"), true))) return;
        await guarded(act, async () => { await call("/api/pg/sync/delete", { id: btn.dataset.id }); await loadSnaps(); });
        break;
      case "export":
        await guarded(act, async () => {
          say(L("Собираю архив…", "Building the archive…"));
          const data = await call("/api/pg/sync/export", { id: btn.dataset.id });
          if (!data.cancelled) say(L(`Сохранено: ${data.path}`, `Saved: ${data.path}`));
        });
        break;
      case "choose": await guarded(act, () => chooseSnapshot(btn.dataset.id)); break;
      case "unlock": {
        const pass = document.getElementById("sync-restore-pass")?.value || "";
        await guarded(act, () => chooseSnapshot(restoreSel.id, pass));
        break;
      }
      case "import-file":
        await guarded(act, async () => {
          const data = await call("/api/pg/sync/import-file", {});
          if (data.cancelled) return;
          restoreSel = { kind: "file", token: data.token, file: data.file, header: data.header || {}, categories: data.categories || {},
            chosen: new Set(Object.keys(data.categories || {})), mirror: false };
          paintRestore();
        });
        break;
      case "restore-back": restoreSel = null; paintRestore(); break;
      case "restore-go": {
        const names = [...restoreSel.chosen].map(catName).join(", ");
        if (!(await confirmBox(L(`Восстановить: ${names}? Текущие версии файлов сначала будут сохранены в страховочную копию.`, `Restore: ${names}? The current versions of the files are saved to a safety copy first.`), L("Восстановить", "Restore"), false))) return;
        await guarded(act, async () => {
          btn.disabled = true;
          setTimeout(() => { loadStatus().then(() => { paintHero(); schedulePoll(); }); }, 300);
          const body = { categories: [...restoreSel.chosen], mirror: !!restoreSel.mirror };
          const result = restoreSel.kind === "file"
            ? await call("/api/pg/sync/restore-file", { ...body, token: restoreSel.token })
            : await call("/api/pg/sync/restore", { ...body, id: restoreSel.id, passphrase: restoreSel.passphrase || "" });
          restoreSel = { ...restoreSel, done: result };
          paintRestore();
          await loadStatus(); paintHero();
        });
        break;
      }
      case "undo":
        if (!(await confirmBox(L("Вернуть данные, какими они были перед восстановлением?", "Bring the data back to how it was before the restore?"), L("Вернуть", "Bring back"), false))) return;
        await guarded(act, async () => {
          await call("/api/pg/sync/undo", { name: btn.dataset.name });
          say(L("Данные возвращены. Перезапустите MemuAI.", "Data brought back. Restart MemuAI."));
          restoreSel = null;
          await loadSnaps();
        });
        break;
      case "restart":
        if (window.MemuSafeMode && MemuSafeMode.restart) MemuSafeMode.restart("normal");
        else call("/api/pg/app/restart", { mode: "normal" }).catch((e) => say(e.message, "error"));
        break;
      default: break;
    }
  }

  function html(context) {
    ctx = context || ctx || {};
    return render();
  }

  async function mount(context) {
    ctx = context || ctx || {};
    const shell = document.querySelector("#panel .sx-shell");
    if (shell && !shell.dataset.syncSub) {
      shell.dataset.syncSub = "1";
      shell.addEventListener("sx:sub", (event) => {
        if (event.detail && event.detail.sub === "sync-restore") { restoreSel = null; loadSnaps(); }
      });
    }
    bind(root());
    await loadStatus();
    paint();
    schedulePoll();
    const tasks = [loadStats()];
    if (settings().target === "folder") tasks.push(loadFolders());
    await Promise.all(tasks);
    paint();
  }

  window.MemuSync = {
    icon: `<span class="ui-icon">${SVG.cloud}</span>`,
    html, mount,
    status: () => st,
  };
})();
