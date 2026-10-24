/* MemuAI · Settings → «MemuFiles» → «Интеграция с Windows» (Round 38, r38-files).

   «MemuFiles по умолчанию»: open folders / Win+E in MemuFiles — only on an explicit, confirmed user action,
   always reversible («Вернуть Проводник Windows» restores the exact registry backup).
   Backend: app/memufiles/default_shell.py, routes GET|POST /api/pg/memufiles/default-shell.

   Extension point used by memufiles-settings.js (r38-suite): MemuFilesDefaultShell.html() inside its section,
   then MemuFilesDefaultShell.mount(el) once the markup is in the DOM. Status is fetched lazily on mount. */
(function () {
  "use strict";

  const isEn = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG === "en"; } catch (e) { /* ignore */ }
    return (document.documentElement.lang || "ru").startsWith("en");
  };
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const API = "/api/pg/memufiles/default-shell";
  let st = null;
  let busy = false;
  const root = () => document.getElementById("mfds-root");

  async function call(body) {
    const response = await fetch(API, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = new Error(errorText(data) || ("HTTP " + response.status)); error.code = data.error; throw error;
    }
    return data;
  }
  function errorText(data) {
    const map = {
      unavailable: L("Доступно только в Windows.", "Windows only."),
      no_exe: L("MemuFiles не установлен — сначала установите его выше.", "MemuFiles is not installed — install it above first."),
      registry: L("Windows не дала изменить реестр. Ничего не изменено.", "Windows refused the registry change. Nothing was changed."),
    };
    return map[data && data.error] || (data && (data.message || data.error)) || "";
  }
  function toast(text, kind) { if (typeof window.toast === "function") window.toast(text, kind || "ok"); }

  function stateChip(state) {
    const K = window.MemuSettingsKit;
    const [text, tone] = state === "on" ? [L("Включено", "On"), "ok"] : state === "partial" ? [L("Частично", "Partly"), "warn"]
      : state === "unavailable" ? [L("Только Windows", "Windows only"), "muted"] : [L("Выключено", "Off"), "muted"];
    return K ? K.chip(text, tone) : `<span class="mf-chip">${esc(text)}</span>`;
  }

  function rows() {
    const K = window.MemuSettingsKit;
    if (!st) return `<div class="sx-desc td" style="padding:12px 16px">${esc(L("Проверяю…", "Checking…"))}</div>`;
    const off = !st.available;
    const f = st.features || {};
    const sw = (id, feature) => {
      const s = (f[feature] || {}).state || "off";
      return K ? K.toggle(id, s !== "off", `data-mfds="${feature}" ${off || busy ? "disabled" : ""}`)
        : `<input type="checkbox" id="${id}" data-mfds="${feature}" ${s !== "off" ? "checked" : ""} ${off || busy ? "disabled" : ""}>`;
    };
    const title = (text, feature) => esc(text) + (off ? "" : ` <span style="margin-left:8px;vertical-align:1px">${stateChip((f[feature] || {}).state || "off")}</span>`);
    const row = (o) => K ? K.row(o) : `<div class="p-toggle"><div><div class="tl">${o.label}</div><div class="td">${o.desc || ""}</div></div>${o.control || ""}</div>`;
    let out = row({ label: title(L("Открывать папки в MemuFiles", "Open folders in MemuFiles"), "folders"),
      desc: esc(L("Двойной щелчок по папке или диску, «Этот компьютер» и папки, которые открывают другие программы, — в MemuFiles. Корзина, Панель управления и другие системные окна по-прежнему открывает Проводник.",
        "Double-clicking a folder or drive, This PC and folders other programs open go to MemuFiles. The Recycle Bin, Control Panel and other system windows still open in Explorer.")),
      control: sw("mfds-folders", "folders"), keywords: "проводник по умолчанию default file manager explorer" });
    out += row({ label: title(L("Win+E открывает MemuFiles", "Win+E opens MemuFiles"), "win_e"),
      desc: esc(L("Сочетание Win+E и кнопка Проводника на панели задач открывают MemuFiles.", "The Win+E shortcut and the taskbar's Explorer button open MemuFiles.")),
      control: sw("mfds-win-e", "win_e"), keywords: "win+e хоткей hotkey" });
    out += row({ label: esc(L("Запасной перехват Win+E", "Win+E fallback")),
      desc: esc(L("Если Windows игнорирует настройку выше: пока окно MemuFiles открыто, оно само ловит Win+E. Ничего не меняет в системе.",
        "If Windows ignores the setting above: while the MemuFiles window is open it catches Win+E itself. Changes nothing in the system.")),
      control: K ? K.toggle("mfds-hook", !!st.hook, `data-mfds="hook" ${off || busy ? "disabled" : ""}`) : "" });
    const exeNote = st.available && st.state !== "off" && !st.exe_exists
      ? `<div class="sx-desc td" style="color:var(--warn,#d9a640)">${esc(L("MemuFiles не найден — папки сейчас открываются в Проводнике. Нажмите «Вернуть Проводник Windows», чтобы убрать настройку.",
        "MemuFiles was not found — folders open in Explorer for now. Press «Restore Windows Explorer» to remove the setting."))}</div>` : "";
    out += row({ label: esc(L("Вернуть Проводник Windows", "Restore Windows Explorer")),
      desc: esc(L("Возвращает реестр ровно в состояние до включения (резервная копия делается автоматически). Аварийно: «MemuFiles.exe --restore-explorer» или файл restore-explorer.reg в папке данных MemuFiles; удаление MemuFiles тоже всё возвращает.",
        "Puts the registry back exactly as it was before (a backup is made automatically). Emergency: «MemuFiles.exe --restore-explorer» or restore-explorer.reg in the MemuFiles data folder; removing MemuFiles restores it too.")),
      extra: exeNote,
      control: `<button type="button" class="pbtn ghost" data-mfds-restore ${off || busy || !st.can_restore ? "disabled" : ""}>${esc(L("Вернуть Проводник", "Restore Explorer"))}</button>` });
    if (off) out += `<div class="sx-desc td" style="padding:8px 16px 12px">${esc(L("Эти настройки работают только в Windows.", "These settings work on Windows only."))}</div>`;
    return out;
  }

  function html() { return `<div id="mfds-root" class="mfds">${rows()}</div>`; }
  function paint() { const el = root(); if (el) el.innerHTML = rows(); }

  function confirmBox(text, okText) {
    return new Promise((resolve) => {
      const node = document.createElement("div");
      node.className = "sync-confirm";
      node.innerHTML = `<div class="sync-confirm-card" role="alertdialog" aria-modal="true"><p>${esc(text)}</p>
        <div class="sync-confirm-actions"><button type="button" class="pbtn ghost" data-no>${esc(L("Отмена", "Cancel"))}</button>
        <button type="button" class="pbtn" data-yes>${esc(okText)}</button></div></div>`;
      const finish = (v) => { node.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
      const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); finish(false); } };
      node.addEventListener("click", (e) => {
        if (e.target.closest("[data-yes]")) finish(true);
        else if (e.target.closest("[data-no]") || e.target === node) finish(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(node);
      requestAnimationFrame(() => node.querySelector("[data-no]").focus());
    });
  }

  const CONFIRM = {
    folders: () => L("Папки будут открываться в MemuFiles. MemuFiles сохранит резервную копию реестра — отменить можно в любой момент кнопкой «Вернуть Проводник Windows». Включить?",
      "Folders will open in MemuFiles. MemuFiles backs the registry up first — undo any time with «Restore Windows Explorer». Turn on?"),
    win_e: () => L("Win+E будет открывать MemuFiles. Резервная копия реестра сохраняется, отменить можно в любой момент. Включить?",
      "Win+E will open MemuFiles. The registry is backed up first; you can undo it any time. Turn on?"),
  };

  async function change(input) {
    const feature = input.dataset.mfds;
    const on = input.checked;
    if (busy) return;
    if (on && feature !== "hook" && !(await confirmBox(CONFIRM[feature](), L("Включить", "Turn on")))) { input.checked = false; return; }
    busy = true; paint();
    try {
      st = feature === "hook" ? await call({ action: "hook", on })
        : await call(on ? { action: "enable", feature, confirmed: true } : { action: "disable", feature });
      toast(on ? L("Готово. Отменить можно здесь же.", "Done. You can undo it right here.") : L("Проводник Windows возвращён", "Windows Explorer restored"));
    } catch (e) { toast(e.message || String(e), "error"); }
    busy = false;
    await load();
  }

  async function restore() {
    if (busy) return;
    busy = true; paint();
    try {
      const r = await call({ action: "restore" });
      toast(r.problems && r.problems.length ? L("Восстановлено не всё: ", "Partly restored: ") + r.problems.join("; ") : L("Проводник Windows возвращён", "Windows Explorer restored"),
        r.problems && r.problems.length ? "error" : "ok");
    } catch (e) { toast(e.message || String(e), "error"); }
    busy = false;
    await load();
  }

  async function load() {
    try { st = await call(); } catch (e) { st = { available: false, state: "unavailable", features: {} }; }
    paint();
  }

  function mount() {
    const el = root();
    if (!el) return;
    if (!el.dataset.bound) {
      el.dataset.bound = "1";
      el.addEventListener("change", (e) => { const i = e.target.closest("input[data-mfds]"); if (i) change(i); });
      el.addEventListener("click", (e) => { if (e.target.closest("[data-mfds-restore]:not([disabled])")) restore(); });
    }
    load();
  }

  window.MemuFilesDefaultShell = { html, mount, status: () => st };
})();
