(function () {
  "use strict";

  var STYLE_ID = "server-mode-style";
  var SVG = {
    server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3 5 6v5c0 4.5 3 8 7 9.5C16 19 19 15.5 19 11V6l-7-3z"/></svg>',
    plug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7zM12 16v5"/></svg>'
  };

  function isEn() {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) {}
    return (document.documentElement.lang || "ru").startsWith("en");
  }
  function L(ru, en) { return isEn() ? en : ru; }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function ico(name) { return '<span class="ui-icon" aria-hidden="true">' + (SVG[name] || "") + "</span>"; }

  var ctx = null;
  var st = null;       // {settings, has_token, token_mask, last_check, bridge}
  var bridge = null;   // {entries, pending}
  var busy = "";

  function kit() { return (ctx && ctx.kit) || window.MemuSettingsKit; }
  function say(text, type) {
    try {
      if (ctx && ctx.toast) ctx.toast(text, type || "ok");
      else if (typeof toast === "function") toast(text, type || "ok");
    } catch (e) {}
  }
  async function call(path, body) {
    var options = { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" } };
    if (body !== undefined) options.body = JSON.stringify(body);
    var response = await fetch(path, options);
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      var error = new Error(data.error || ("HTTP " + response.status));
      error.payload = data;
      throw error;
    }
    return data;
  }
  function settings() { return (st && st.settings) || {}; }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = ".srv-hero{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;border:1px solid var(--sx-border,#e3e3e3);border-radius:12px;margin-bottom:14px}"
      + ".srv-hero .srv-hero-icon{flex:0 0 auto;width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:var(--sx-accent-soft,rgba(0,0,0,.06))}"
      + ".srv-hero .srv-hero-icon svg{width:20px;height:20px}"
      + ".srv-hero b{display:block;font-size:14px}"
      + ".srv-hero p{margin:4px 0 0;font-size:12.5px;opacity:.75}"
      + ".srv-chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}"
      + ".srv-journal{font-size:12.5px;padding:12px 18px;border-top:1px solid var(--sx-line-soft,#eee)}"
      + "#server-mode-root .sx-subhead{margin:0;padding:16px 18px 6px;border-top:1px solid var(--sx-line-soft,#eee);font-size:13px;font-weight:600}"
      + "#server-mode-root > .sx-note{margin-top:22px}"
      + ".srv-journal-row{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--sx-border,#eee)}"
      + ".srv-journal-row:last-child{border-bottom:0}"
      + ".srv-mono{font-family:ui-monospace,monospace;word-break:break-all}"
      + ".srv-pending{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 10px;border:1px solid var(--sx-border,#e3e3e3);border-radius:10px;margin:6px 0}"
      + ".srv-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#c33;vertical-align:middle;margin-left:6px}"
      + ".srv-dot.on{background:#2a7}";
    document.head.appendChild(el);
  }

  /* Status dot next to the Settings nav button: the in-interface indicator. */
  function updateNavDot() {
    try {
      var btn = document.getElementById("nav-settings");
      if (!btn) return;
      var dot = btn.querySelector("[data-server-mode-dot]");
      var s = settings();
      var show = s.mode && s.mode !== "local";
      if (!show) { if (dot) dot.remove(); return; }
      if (!dot) {
        dot = document.createElement("span");
        dot.setAttribute("data-server-mode-dot", "1");
        dot.className = "srv-dot";
        dot.title = L("Серверный режим включён", "Server mode is on");
        btn.appendChild(dot);
      }
      var connected = st && st.last_check && st.last_check.ok;
      dot.classList.toggle("on", !!connected);
      dot.title = connected
        ? L("Серверный режим: подключено", "Server mode: connected")
        : L("Серверный режим: нет подключения", "Server mode: not connected");
    } catch (e) {}
  }

  function modeLabel(mode) {
    return mode === "server" ? L("Сервер", "Server") : mode === "hybrid" ? L("Гибрид", "Hybrid") : L("Локально", "Local");
  }

  function heroHtml(K) {
    var s = settings();
    var last = (st && st.last_check) || {};
    var title, text, iconName = "server";
    if (s.mode === "local") {
      title = L("Серверный режим выключен", "Server mode is off");
      text = L("MemuAI работает только на этом компьютере. Включите режим «Сервер» или «Гибрид», чтобы файлы, проекты и чаты жили на вашем сервере.", "MemuAI works only on this computer. Switch to Server or Hybrid so files, projects and chats live on your server.");
    } else if (!s.server_url || !st.has_token) {
      iconName = "warn";
      title = L("Серверный режим: не настроен", "Server mode: not set up");
      text = L("Укажите адрес сервера и вставьте токен ниже, затем проверьте соединение.", "Enter the server address and paste the token below, then check the connection.");
    } else if (last.checked && last.ok) {
      iconName = "check";
      title = L("Подключено к серверу", "Connected to the server");
      text = (last.latency_ms != null ? L("Ответ за ", "Answered in ") + last.latency_ms + L(" мс", " ms") : "") + (s.server_url ? " · " + s.server_url : "");
    } else if (last.checked) {
      iconName = "warn";
      title = L("Нет подключения к серверу", "Cannot reach the server");
      text = esc(last.error || "") + (s.server_url ? " · " + s.server_url : "");
    } else {
      title = L("Серверный режим включён", "Server mode is on");
      text = L("Нажмите «Проверить соединение», чтобы убедиться, что сервер отвечает.", "Press “Check connection” to make sure the server answers.");
    }
    var chips = [
      K.chip(modeLabel(s.mode), s.mode === "local" ? "muted" : "ok"),
      s.sync_files ? K.chip(L("Файлы", "Files"), "muted") : "",
      s.sync_projects ? K.chip(L("Проекты", "Projects"), "muted") : "",
      s.sync_chats ? K.chip(L("Чаты", "Chats"), "muted") : "",
      s.auto_sync ? K.chip(L("Автосинхронизация", "Auto-sync"), "muted") : "",
      s.bridge_enabled ? K.chip(L("Мост включён", "Bridge on"), "warn") : "",
      st && st.bridge && st.bridge.revoked ? K.chip(L("Мост отозван", "Bridge revoked"), "danger") : ""
    ].filter(Boolean).join("");
    return '<div class="srv-hero"><span class="srv-hero-icon">' + ico(iconName) + '</span><div><b>' + esc(title) + '</b><p>' + esc(text) + '</p><div class="srv-chips">' + chips + '</div></div></div>';
  }

  function render() {
    var K = kit();
    var node = document.getElementById("server-mode-root");
    if (!node || !K) return;
    var s = settings();
    var serverOn = s.mode !== "local";
    var journalRows = ((bridge && bridge.entries) || []).slice(-12).reverse().map(function (row) {
      return '<div class="srv-journal-row"><span>' + esc(row.event || row.status || "") + '</span><span class="srv-mono">' + esc(row.kind || row.action_class || "") + '</span><span class="srv-mono">' + esc(row.target || "") + '</span><span>' + esc(row.status || row.error || "") + '</span></div>';
    }).join("");
    var pendingHtml = ((bridge && bridge.pending) || []).map(function (row) {
      return '<div class="srv-pending"><span><b>' + esc(row.kind || row.action_class) + '</b>' + ' <span class="srv-mono">' + esc(row.target || (row.argv || []).join(" ")) + '</span></span>'
        + '<span style="flex:1"></span>'
        + '<button type="button" class="pbtn ghost sx-btn-sm" data-act="confirm" data-id="' + esc(row.id) + '" data-approve="1">' + esc(L("Разрешить", "Allow")) + '</button>'
        + '<button type="button" class="pbtn ghost sx-btn-sm" data-act="confirm" data-id="' + esc(row.id) + '" data-approve="0">' + esc(L("Запретить", "Deny")) + '</button></div>';
    }).join("");
    node.innerHTML = heroHtml(K)
      + K.section({ id: "server-mode-connection", title: L("Подключение", "Connection"), keywords: "server сервер url адрес token токен mode режим",
        body:
          K.row({ label: L("Режим", "Mode"), desc: L("«Локально» — как раньше, без сервера. «Сервер» — данные на сервере. «Гибрид» — данные и локально, и на сервере.", "“Local” works as before, with no server. “Server” keeps data on the server. “Hybrid” keeps it in both places."), keywords: "mode local server hybrid режим локально гибрид",
            control: K.segmented("server-mode", [
              { value: "local", label: L("Локально", "Local") },
              { value: "server", label: L("Сервер", "Server") },
              { value: "hybrid", label: L("Гибрид", "Hybrid") }
            ], s.mode, { id: "server-mode-seg", label: L("Режим", "Mode") }) })
          + K.row({ label: L("Адрес сервера", "Server address"), stack: true, keywords: "url адрес address host порт port",
            desc: L("Например: https://memu.example.com — только через reverse proxy с HTTPS. Локальный сервер: http://127.0.0.1:8327.", "For example: https://memu.example.com — only behind a reverse proxy with HTTPS. A local server: http://127.0.0.1:8327."),
            control: K.input("server-url", s.server_url || "", { mono: true, width: 340, placeholder: "https://memu.example.com" }) })
          + K.row({ label: L("Токен сервера", "Server token"), stack: true, keywords: "token токен bearer auth",
            desc: st.has_token
              ? L("Сохранён в защищённом хранилище. Показывается только маска: ", "Stored in protected storage. Only a mask is shown: ") + '<span class="srv-mono">' + esc(st.token_mask || "") + "</span>. " + L("Введите новый, чтобы заменить; пустое поле ничего не меняет.", "Enter a new one to replace it; an empty field changes nothing.")
              : L("Токен создаётся при первом запуске сервера и хранится только на сервере. Вставьте его сюда один раз — MemuAI сохранит его в защищённом хранилище и никогда не покажет целиком.", "The token is created on the server's first start and stored only there. Paste it here once — MemuAI keeps it in protected storage and never shows it in full."),
            control: K.secret("server-token", "", { placeholder: st.has_token ? "••••••••" : L("Вставьте токен", "Paste the token") }) })
          + K.actions('<button type="button" class="pbtn sx-btn-sm" data-act="check">' + ico("plug") + " " + esc(L("Проверить соединение", "Check connection")) + '</button>'
            + (st.has_token ? '<button type="button" class="pbtn ghost sx-btn-sm" data-act="clear-token">' + esc(L("Удалить токен", "Remove token")) + "</button>" : ""))
      })
      + K.section({ id: "server-mode-sync", title: L("Синхронизация", "Sync"), keywords: "sync синхронизация push pull конфликты offline офлайн",
        desc: L("Что синхронизировать с сервером. При изменении с обеих сторон побеждает более новая версия, а при одинаковом времени MemuAI спросит вас. Без соединения операции копятся и применяются позже.", "What to sync with the server. When both sides changed, the newer version wins; with equal times MemuAI asks you. Offline, operations queue up and apply later."),
        body:
          K.row({ label: L("Проекты", "Projects"), control: K.toggle("server-sync-projects", !!s.sync_projects) })
          + K.row({ label: L("Файлы", "Files"), control: K.toggle("server-sync-files", !!s.sync_files) })
          + K.row({ label: L("Чаты", "Chats"), control: K.toggle("server-sync-chats", !!s.sync_chats) })
          + K.row({ label: L("Синхронизировать автоматически", "Sync automatically"), desc: L("Ручной режим: только кнопка ниже.", "Manual mode: only the button below."), control: K.toggle("server-auto-sync", !!s.auto_sync) })
          + K.actions('<button type="button" class="pbtn sx-btn-sm" data-act="sync-now" ' + (serverOn && s.sync_files ? "" : "disabled") + ">" + esc(L("Синхронизировать сейчас", "Sync now")) + "</button>")
      })
      + K.section({ id: "server-mode-bridge", title: L("Мост к этому компьютеру", "Bridge to this computer"), keywords: "bridge мост доступ allowlist подтверждение revoke отозвать",
        desc: L("Серверный ИИ видит только файлы сервера. К этому компьютеру он обращается только через мост: только в разрешённых папках, только разрешёнными командами и только с вашего подтверждения. Каждое обращение записывается в журнал.", "The server-side AI only sees the server's files. It reaches this computer only through the bridge: only inside allowed folders, only with allowlisted commands, and only with your confirmation. Every call is journaled."),
        body:
          K.row({ label: L("Мост включён", "Bridge enabled"), desc: L("Выключено по умолчанию. Без моста серверный ИИ к этому компьютеру не обращается вообще.", "Off by default. Without the bridge the server-side AI never reaches this computer at all."), control: K.toggle("server-bridge-enabled", !!s.bridge_enabled) })
          + K.row({ label: L("Чтение файлов", "Read files"), control: K.toggle("server-bridge-read", !!s.bridge_read) })
          + K.row({ label: L("Запись файлов", "Write files"), control: K.toggle("server-bridge-write", !!s.bridge_write) })
          + K.row({ label: L("Запуск команд", "Run commands"), desc: L("Только команды из явного списка, без оболочки.", "Only commands from an explicit list, without a shell."), control: K.toggle("server-bridge-execute", !!s.bridge_execute) })
          + K.row({ label: L("Браузер", "Browser"), desc: L("Открытие ссылок — только публичные адреса; выполняет браузер MemuAI.", "Opening links — public addresses only; performed by the MemuAI browser."), control: K.toggle("server-bridge-browser", !!s.bridge_browser) })
          + K.row({ label: L("Подтверждение", "Confirmation"), control: K.select("server-bridge-confirm", [["always", L("Каждый раз", "Every time")], ["once", L("Один раз на действие", "Once per action")]], s.bridge_confirm || "always", { width: 220 }) })
          + K.row({ label: L("Разрешённые папки", "Allowed folders"), stack: true, keywords: "roots папки folders корни",
            desc: L("По одной папке в строке. За пределами этих папок мост ничего не читает, не пишет и не запускает.", "One folder per line. Outside these folders the bridge reads, writes and runs nothing."),
            control: K.textarea("server-bridge-roots", (s.bridge_roots || []).join("\n"), { rows: 3, placeholder: "C:\\Users\\you\\Projects" }) })
          + (pendingHtml ? '<h4 class="sx-subhead">' + esc(L("Ожидают подтверждения", "Waiting for confirmation")) + "</h4>" + pendingHtml : "")
          + '<h4 class="sx-subhead">' + esc(L("Журнал моста", "Bridge journal")) + "</h4>"
          + '<div class="srv-journal">' + (journalRows || esc(L("Обращений пока не было.", "No calls yet."))) + "</div>"
          + K.actions('<button type="button" class="pbtn ghost sx-btn-sm" data-act="bridge-poll">' + esc(L("Проверить очередь моста", "Check the bridge queue")) + "</button>"
            + '<button type="button" class="pbtn ghost danger sx-btn-sm" data-act="revoke">' + ico("shield") + " " + esc(L("Отозвать доступ", "Revoke access")) + "</button>")
      })
      + K.note(L("Серверный режим полностью опционален и выключен по умолчанию. Один владелец: многопользовательских областей нет, доступ — только по токену. Подробнее — в docs/SERVER.md.", "Server mode is fully optional and off by default. Single owner: no multi-user scopes, token-only access. Details are in docs/SERVER.md."), "info");
    bind(node);
    updateNavDot();
    try { if (typeof window.enhanceSelects === "function") window.enhanceSelects(node); } catch (e) {}
    try { kit().refreshDeps && kit().refreshDeps(); } catch (e) {}
  }

  function paint() { render(); }

  async function loadAll() {
    try { st = await call("/api/pg/server-mode/settings"); } catch (e) { st = st || { settings: {}, has_token: false, token_mask: "", last_check: {} }; }
    try { bridge = await call("/api/pg/bridge/journal"); } catch (e) { bridge = bridge || { entries: [], pending: [] }; }
    paint();
  }

  async function save(patch, opts) {
    opts = opts || {};
    try {
      var body = { settings: patch };
      if (opts.token !== undefined) body.token = opts.token;
      if (opts.clearToken) body.clear_token = true;
      var data = await call("/api/pg/server-mode/settings", body);
      st = data;
      try { kit().saved && kit().saved(true); } catch (e) {}
      if (opts.repaint) await loadAll(); else paint();
      return true;
    } catch (e) {
      say(e.message, "error");
      await loadAll();
      return false;
    }
  }

  async function guarded(name, fn) {
    if (busy) return;
    busy = name;
    try { await fn(); } catch (e) { say(e.message || String(e), "error"); } finally { busy = ""; }
  }

  function bind(node) {
    if (!node || node.dataset.bound) return;
    node.dataset.bound = "1";
    node.addEventListener("change", function (event) {
      var t = event.target;
      var id = t.id || "";
      if (t.name === "server-mode") { save({ mode: t.value }, { repaint: true }); return; }
      switch (id) {
        case "server-url": save({ server_url: t.value.trim() }); break;
        case "server-sync-projects": save({ sync_projects: t.checked }); break;
        case "server-sync-files": save({ sync_files: t.checked }, { repaint: true }); break;
        case "server-sync-chats": save({ sync_chats: t.checked }); break;
        case "server-auto-sync": save({ auto_sync: t.checked }); break;
        case "server-bridge-enabled": save({ bridge_enabled: t.checked }, { repaint: true }); break;
        case "server-bridge-read": save({ bridge_read: t.checked }); break;
        case "server-bridge-write": save({ bridge_write: t.checked }); break;
        case "server-bridge-execute": save({ bridge_execute: t.checked }); break;
        case "server-bridge-browser": save({ bridge_browser: t.checked }); break;
        case "server-bridge-confirm": save({ bridge_confirm: t.value }); break;
        case "server-bridge-roots": save({ bridge_roots: t.value.split(/\n+/).map(function (x) { return x.trim(); }).filter(Boolean) }); break;
        default: break;
      }
    });
    node.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-act]");
      if (!btn || !node.contains(btn)) return;
      var act = btn.getAttribute("data-act");
      if (act === "check") {
        guarded("check", async function () {
          var tokenInput = document.getElementById("server-token");
          var token = tokenInput && tokenInput.value ? tokenInput.value.trim() : undefined;
          if (token) await save({ server_url: (document.getElementById("server-url") || {}).value || settings().server_url }, { token: token });
          var data = await call("/api/pg/server-mode/check", {});
          // The check response carries ok/latency but no "checked" flag
          // (the backend only adds it to the stored snapshot); mark
          // this fresh result as checked so the hero shows it.
          data.checked = true;
          st.last_check = data;
          if (data.ok) say(L("Подключено к серверу", "Connected to the server"));
          else say(L("Нет подключения: ", "Cannot connect: ") + (data.error || ""), "error");
          paint();
        });
      } else if (act === "clear-token") {
        guarded("clear-token", async function () {
          await save({}, { clearToken: true });
          say(L("Токен удалён", "Token removed"));
        });
      } else if (act === "sync-now") {
        guarded("sync", async function () {
          var data = await call("/api/pg/server-mode/sync", {});
          var parts = [];
          if (data.pushed) parts.push(L("отправлено: ", "pushed: ") + data.pushed);
          if (data.pulled) parts.push(L("получено: ", "pulled: ") + data.pulled);
          if (data.queued) parts.push(L("в очереди: ", "queued: ") + data.queued);
          if (data.conflicts && data.conflicts.length) parts.push(L("конфликты: ", "conflicts: ") + data.conflicts.join(", "));
          say(parts.length ? parts.join(" · ") : L("Изменений нет", "Nothing changed"));
          await loadAll();
        });
      } else if (act === "bridge-poll") {
        guarded("poll", async function () {
          var data = await call("/api/pg/server-mode/bridge/poll", {});
          say(L("Очередь моста проверена: ", "Bridge queue checked: ") + (data.outcomes || []).length);
          await loadAll();
        });
      } else if (act === "revoke") {
        guarded("revoke", async function () {
          await call("/api/pg/bridge/revoke", {});
          say(L("Доступ моста отозван", "Bridge access revoked"));
          await loadAll();
        });
      } else if (act === "confirm") {
        guarded("confirm", async function () {
          await call("/api/pg/bridge/confirm", { request_id: btn.getAttribute("data-id"), approve: btn.getAttribute("data-approve") === "1" });
          await loadAll();
        });
      }
    });
    // Token field: save on blur/Enter, never on every keystroke.
    var tokenInput = node.querySelector("#server-token");
    if (tokenInput) {
      tokenInput.addEventListener("change", function () {
        var value = tokenInput.value.trim();
        if (!value) return;
        save({}, { token: value }).then(function () { tokenInput.value = ""; });
      });
    }
  }

  window.MemuServerMode = {
    icon: SVG.server,
    html: function (o) {
      ctx = o || {};
      injectStyle();
      return '<div id="server-mode-root">' + esc(L("Загружаю настройки сервера…", "Loading server settings…")) + "</div>";
    },
    mount: function (o) {
      ctx = o || ctx || {};
      injectStyle();
      loadAll();
    },
    _renderForTests: function (snapshot) { st = snapshot; bridge = { entries: [], pending: [] }; render(); }
  };
})();
