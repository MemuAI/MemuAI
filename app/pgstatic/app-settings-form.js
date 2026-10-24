/* MemuAI · every preference of MemuNote / MemuFiles in one place (Round 38).

   window.MemuAppSettings.create({ app, idp, name, extra }) → a form controller used by
   memunote-settings.js and memufiles-settings.js. The schema, values and defaults come from
   /api/pg/appsettings/<app> (app/app_settings_api.py), which reads/writes the app's own settings.json
   and validates by the app's settings_schema.py — so there is one source of truth for MemuAI and the app.

   · Rows are settings-kit rows (data-sx-key + default): the global settings search finds them, the
     per-row / per-section reset works, and the «Сохранено» pill shows saves.
   · A change is saved at once; the running app gets a "settings" event and applies it without a restart.
   · Changes made inside the app are picked up when the page is shown or the MemuAI window gets focus
     (one cheap GET comparing the file's mtime) — no timers, no polling.
   · extra: { <groupId>: { html: () => string, mount: (node) => void, desc?: () => string } } adds a block to a group
     (MemuFiles → «Интеграция с Windows»: window.MemuFilesDefaultShell from memufiles-default.js). */
(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) { /* ignore */ }
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const T = (o, base = "") => (o ? (isEn() ? (o[base + "en"] || o[base + "ru"]) : (o[base + "ru"] || o[base + "en"])) || "" : "");
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const GROUP_ICONS = {
    appearance: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor" stroke="none" opacity=".35"/></svg>',
    editor: '<svg viewBox="0 0 24 24"><path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/></svg>',
    view: '<svg viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="4.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.5"/></svg>',
    files: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    ai: '<svg viewBox="0 0 24 24"><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/></svg>',
    hotkeys: '<svg viewBox="0 0 24 24"><rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17.5 10h.01M8 14h8"/></svg>',
    windows: '<svg viewBox="0 0 24 24"><path d="M4 5.5 11 4.5v7H4zM13 4.2l7-1v8.3h-7zM4 12.5h7v7l-7-1zM13 12.5h7v8.3l-7-1z"/></svg>',
    advanced: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/></svg>',
  };
  const ERRORS = {
    type: ["Неверный тип значения", "Wrong value type"], range: ["Значение вне допустимого диапазона", "Value out of range"],
    choice: ["Такого варианта нет", "Not one of the options"], length: ["Слишком длинное значение", "Value is too long"],
    font: ["Недопустимое имя шрифта", "Invalid font name"], url: ["Нужен локальный адрес, например http://127.0.0.1:8317", "A local address is needed, e.g. http://127.0.0.1:8317"],
    unknown_key: ["Неизвестная настройка", "Unknown setting"],
  };

  function create(opts) {
    const app = opts.app, idp = opts.idp, extra = opts.extra || {};
    let data = null;          // { schema, values, defaults, mtime }
    let failed = "";
    let loading = null;
    let filter = "";
    let focusBound = false;
    const kit = () => window.MemuSettingsKit;
    const host = () => document.getElementById(idp + "form");
    const cid = (key) => idp + key;

    async function call(path, body) {
      const response = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body) });
      const json = await response.json().catch(() => ({}));
      if (!response.ok || json.ok === false) { const e = new Error(json.error || "HTTP " + response.status); e.data = json; throw e; }
      return json;
    }

    /* ------------------------------------------------------------ render */
    function whenAttr(expr) {
      if (!expr || !data) return "";
      const [key, value] = String(expr).split("=");
      const item = data.schema.items.find((x) => x.key === key);
      if (!item) return "";
      if (value === undefined) return cid(key);
      return item.type === "enum" && item.choices.length <= 3 ? `@${cid(key)}=${value}` : `${cid(key)}=${value}`;
    }
    function modelOptions() {
      try { if (typeof S !== "undefined" && Array.isArray(S.models)) return S.models.map((m) => m.id).filter(Boolean).slice(0, 400); } catch (e) { /* ignore */ }
      return [];
    }
    function control(item, value) {
      const K = kit(), id = cid(item.key), attrs = `data-appset="${esc(item.key)}"`;
      if (item.type === "bool") return K.toggle(id, !!value, attrs);
      if (item.type === "enum") {
        const options = item.choices.map((c) => ({ value: c.value, label: T(c) }));
        return options.length <= 3 ? K.segmented(id, options, value, { label: T(item), attrs }) : K.select(id, options, value, { width: 220, attrs });
      }
      if (item.type === "int" || item.type === "float") {
        const steps = (item.max - item.min) / item.step;
        if (steps <= 240) return K.slider(id, { min: item.min, max: item.max, step: item.step, value, unit: item.unit || "", attrs });
        return `<span class="appset-num">${K.input(id, value, { type: "number", width: 130, attrs: `${attrs} min="${item.min}" max="${item.max}" step="${item.step}"` })}${item.unit ? `<small>${esc(item.unit.trim())}</small>` : ""}</span>`;
      }
      const list = item.type === "font" ? item.suggest || [] : item.models ? modelOptions() : [];
      const listId = list.length ? id + "-list" : "";
      const placeholder = item.placeholder || (item.models ? L("По умолчанию", "Default") : "");
      return K.input(id, value, { width: 240, placeholder, mono: item.type === "url", attrs: `${attrs} spellcheck="false" autocomplete="off" maxlength="${item.maxlen || 200}"${listId ? ` list="${listId}"` : ""}` })
        + (listId ? `<datalist id="${listId}">${list.map((v) => `<option value="${esc(v)}"></option>`).join("")}</datalist>` : "");
    }
    function rowHtml(item) {
      const K = kit();
      return K.row({ id: idp + "row-" + item.key, key: idp + item.key, def: data.defaults[item.key], label: esc(T(item)), desc: esc(T(item, "desc_")),
        when: whenAttr(item.when), keywords: `${app} ${item.key.replace(/_/g, " ")}`, control: control(item, data.values[item.key]) });
    }
    function shortcutsHtml(list) {
      return `<div class="appset-keys">${list.map((s) => `<div class="appset-key"><span>${esc(T(s))}</span><kbd>${esc(s.keys)}</kbd></div>`).join("")}</div>`;
    }
    function groupsHtml() {
      const K = kit();
      return data.schema.groups.map((g) => {
        const items = data.schema.items.filter((x) => x.group === g.id);
        const ext = extra[g.id];
        const extHtml = ext ? safe(() => ext.html(), "") : "";
        let body = items.map(rowHtml).join("");
        if (g.id === "hotkeys" && data.schema.shortcuts.length) body += shortcutsHtml(data.schema.shortcuts);
        if (!body && !extHtml) return "";
        const desc = g.id === "hotkeys" ? esc(L(`Сочетания ${opts.name} (F1 в окне — полный список).`, `${opts.name} shortcuts (F1 in the window lists them all).`))
          : ext && ext.desc ? esc(safe(() => ext.desc(), "")) : "";
        return K.section({ id: `${idp}g-${g.id}`, title: esc(T(g)), icon: GROUP_ICONS[g.id] || "", reset: items.length > 0, desc,
          cls: "appset-group", keywords: `${app} ${g.id}`, card: true, body: body + (extHtml ? `<div class="appset-ext" data-appset-ext="${g.id}">${extHtml}</div>` : "") });
      }).join("");
    }
    function safe(fn, fallback) { try { return fn(); } catch (e) { console.error(app, "settings block", e); return fallback; } }
    function inner() {
      const K = kit();
      if (!K) return "";
      if (failed && !data) return K.note(esc(L("Не удалось прочитать настройки: ", "Couldn't read the settings: ") + failed), "warn");
      if (!data) return `<div class="appset-skeleton" aria-busy="true">${"<i></i>".repeat(4)}</div>`;
      return `<div class="appset-bar"><label class="appset-filter"><span class="ui-icon" aria-hidden="true">${GROUP_ICONS.search}</span>
          <input type="search" id="${idp}filter" value="${esc(filter)}" placeholder="${esc(L(`Найти в настройках ${opts.name}`, `Find in ${opts.name} settings`))}" autocomplete="off" spellcheck="false"></label>
          <span class="appset-sync" title="${esc(L(`Хранится в настройках ${opts.name} и применяется в открытом окне сразу`, `Stored in ${opts.name}'s own settings and applied to an open window at once`))}"><i aria-hidden="true"></i>${esc(L("Синхронизируется с " + opts.name, "Synced with " + opts.name))}</span></div>
        <div class="appset-groups" id="${idp}groups">${groupsHtml()}</div>
        <div class="appset-empty" id="${idp}nothing" hidden>${esc(L("Ничего не найдено", "Nothing found"))}</div>`;
    }
    function html() {
      if (!data && !loading) refresh();
      return `<div class="appset" id="${idp}form">${inner()}</div>`;
    }
    function paint() {
      const el = host();
      if (!el) return;
      el.innerHTML = inner();
      decorate(el);
    }
    /* After the markup is in the page: `when` dependencies, «changed» marks, search index, filter, extra blocks. */
    function decorate(el) {
      if (!data) return;
      const K = kit();
      if (K) { K.refreshDeps(el); K.refreshModified(el); K.invalidate(); }
      applyFilter();
      for (const [group, ext] of Object.entries(extra)) {
        const node = el.querySelector(`[data-appset-ext="${group}"]`);
        if (node && ext.mount) safe(() => ext.mount(node), null);
      }
    }

    /* ------------------------------------------------------------ data */
    async function refresh(force) {
      if (loading) return loading;
      loading = (async () => {
        try {
          if (!data) { data = await call(`/api/pg/appsettings/${app}`); failed = ""; paint(); return; }
          const snap = await call(`/api/pg/appsettings/${app}?schema=0`);
          if (force || snap.mtime !== data.mtime) {
            data = { ...data, values: snap.values, defaults: snap.defaults, mtime: snap.mtime };
            const el = host();
            if (el && !el.contains(document.activeElement)) paint(); else syncControls();
          }
        } catch (e) { failed = e.message || String(e); if (!data) paint(); }
        finally { loading = null; }
      })();
      return loading;
    }
    /* A field is focused: update the other controls in place instead of repainting under the cursor. */
    function syncControls() {
      const el = host();
      if (!el || !data) return;
      for (const item of data.schema.items) {
        const value = data.values[item.key];
        const node = el.querySelector(`#${CSS.escape(cid(item.key))}`);
        if (node && node === document.activeElement) continue;
        if (item.type === "bool" && node) node.checked = !!value;
        else if (item.type === "enum" && !node) el.querySelectorAll(`input[name="${CSS.escape(cid(item.key))}"]`).forEach((r) => { r.checked = r.value === String(value); });
        else if (node) { node.value = String(value ?? ""); if (node.type === "range") node.dispatchEvent(new Event("input", { bubbles: true })); }
      }
      const K = kit();
      if (K) { K.refreshDeps(el); K.refreshModified(el); }
    }
    function readValue(item, node) {
      if (item.type === "bool") return node.checked;
      if (item.type === "int") return Math.round(Number(node.value));
      if (item.type === "float") return Number(node.value);
      return node.value;
    }
    async function save(key, node) {
      const item = data && data.schema.items.find((x) => x.key === key);
      if (!item) return;
      const value = readValue(item, node);
      if (Object.is(value, data.values[key])) return;
      try {
        const r = await call(`/api/pg/appsettings/${app}`, { changes: { [key]: value } });
        data = { ...data, values: r.values, mtime: r.mtime };
        kit()?.saved(true);
      } catch (e) {
        const code = e.data && e.data.errors ? e.data.errors[key] : "";
        const msg = code && ERRORS[code] ? L(ERRORS[code][0], ERRORS[code][1]) : (e.message || String(e));
        toast(`${T(item)}: ${msg}`, "error");
        syncControls();
        kit()?.saved(false);
      }
    }
    function toast(text, kind) {
      if (opts.toast) opts.toast(text, kind);
      else if (typeof window.toast === "function") window.toast(text, kind);
    }

    /* ------------------------------------------------------------ filter */
    function applyFilter() {
      const el = host();
      if (!el) return;
      const q = filter.trim().toLowerCase();
      let any = false;
      el.querySelectorAll(".appset-group").forEach((sec) => {
        const title = (sec.querySelector(".sx-section-title")?.textContent || "").toLowerCase();
        const groupHit = q && title.includes(q);
        let shown = 0;
        sec.querySelectorAll(".sx-row, .appset-key").forEach((row) => {
          const hit = !q || groupHit || row.textContent.toLowerCase().includes(q) || (row.dataset.sxK || "").includes(q);
          row.classList.toggle("appset-hide", !hit);
          if (hit) shown++;
        });
        const ext = sec.querySelector(".appset-ext");
        const extHit = ext && (!q || groupHit || ext.textContent.toLowerCase().includes(q));
        sec.classList.toggle("appset-hide", !(shown || extHit));
        if (shown || extHit) any = true;
      });
      const none = el.querySelector(`#${CSS.escape(idp)}nothing`);
      if (none) none.hidden = any;
    }

    /* ------------------------------------------------------------ mount */
    function mount() {
      const el = host();
      if (!el) return;
      if (!el.dataset.bound) {
        el.dataset.bound = "1";
        el.addEventListener("change", (e) => {
          // data-appset sits on the control itself, or on the .sx-seg group of a segmented choice
          const holder = e.target.closest("[data-appset]");
          if (holder) save(holder.dataset.appset, e.target);
        });
        el.addEventListener("input", (e) => { if (e.target.id === idp + "filter") { filter = e.target.value; applyFilter(); } });
      }
      if (!focusBound) {
        focusBound = true;
        const check = () => { const h = host(); if (h && h.isConnected && !document.hidden && h.offsetParent !== null) refresh(); };
        window.addEventListener("focus", check);
        document.addEventListener("visibilitychange", check);
      }
      decorate(el);
      refresh();
    }

    return { html, mount, refresh, values: () => (data ? data.values : null) };
  }

  window.MemuAppSettings = { create };
})();
