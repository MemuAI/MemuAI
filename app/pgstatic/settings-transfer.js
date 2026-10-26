

(function () {
  "use strict";
  if (window.MemuSettingsTransfer) return;
  const KIND = "memuai-settings";

  const PRIVATE = /(api_key|api_keys|_token$|token_|password|secret|_chat_id$|allowed_users|device_id|_dir$|_path$|^profile_|onboarded|^telegram_enabled$|^voice_realtime_api_keys$)/i;
  const L = (ru, en) => ((typeof PG_LANG !== "undefined" ? PG_LANG : "ru") === "en" ? en : ru);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  let pending = null;

  function exportable(prefs) {
    const out = {};
    for (const [k, v] of Object.entries(prefs || {})) if (!PRIVATE.test(k) && v !== undefined) out[k] = v;
    return out;
  }
  function parse(raw) {
    let data;
    try { data = JSON.parse(raw); } catch (_) { return null; }
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    const prefs = data.kind === KIND ? data.preferences : null;
    return prefs && typeof prefs === "object" && !Array.isArray(prefs) ? prefs : null;
  }

  function diff(prefs, current = (typeof S !== "undefined" ? S.preferences : {}) || {}) {
    const changes = {};
    for (const [k, v] of Object.entries(prefs || {})) {
      if (PRIVATE.test(k) || !Object.prototype.hasOwnProperty.call(current, k)) continue;
      if (!same(current[k], v)) changes[k] = v;
    }
    return changes;
  }

  function html(L2, icon) {
    return `<div class="transfer-card" id="tr-settings-card">
      <div class="transfer-head"><span class="transfer-ico">${icon("settings")}</span>
        <div><div class="settings-title tl">${L2("Настройки", "Settings")}</div>
          <p class="sub">${L2("Все настройки окна одним файлом: внешний вид, модели, горячие клавиши, поведение. Удобно для переноса на другой компьютер или как копия перед экспериментами.",
            "Every window setting in one file: appearance, models, shortcuts, behaviour. Handy to move to another computer or as a backup before experimenting.")}</p></div></div>
      <div class="transfer-actions">
        <button class="pbtn ghost" id="tr-export-settings">${icon("download")} ${L2("Экспортировать настройки", "Export settings")}</button>
        <button class="pbtn ghost" id="tr-import-settings">${icon("attach")} ${L2("Импортировать настройки", "Import settings")}</button>
      </div>
      <div class="transfer-note">${L2("Ключи API, токен Telegram-бота, пароли, папки на диске и ваш профиль в файл не попадают.",
        "API keys, the Telegram bot token, passwords, folders on disk and your profile are never written to the file.")}</div>
      <div class="sxt-confirm" id="tr-settings-confirm" hidden></div>
    </div>`;
  }

  async function doExport() {
    const prefs = exportable(S.preferences);
    const stamp = new Date().toISOString().slice(0, 10);
    const payload = { kind: KIND, version: 1, app: "MemuAI", app_version: S.appVersion?.version || "", exported_at: new Date().toISOString(), preferences: prefs };
    await downloadTextFile(`memuai-settings-${stamp}.json`, JSON.stringify(payload, null, 2));
  }
  function pick() {
    const input = document.createElement("input");
    input.type = "file"; input.accept = ".json,application/json"; input.style.display = "none";
    document.body.appendChild(input);
    input.onchange = async () => {
      const file = input.files && input.files[0];
      let raw = "";
      try { raw = file ? await file.text() : ""; } catch (_) { raw = ""; }
      input.remove();
      const prefs = parse(raw);
      if (!prefs) { toast(L("Это не файл настроек MemuAI", "This is not a MemuAI settings file"), "warn"); return; }
      showConfirm(diff(prefs));
    };
    input.click();
  }
  function showConfirm(changes) {
    const box = document.getElementById("tr-settings-confirm");
    if (!box) return;
    const keys = Object.keys(changes);
    pending = keys.length ? changes : null;
    box.hidden = false;
    box.innerHTML = keys.length
      ? `<b>${esc(L(`Файл изменит настроек: ${keys.length}`, `The file changes ${keys.length} setting${keys.length === 1 ? "" : "s"}`))}</b>
         <span class="sxt-keys">${keys.slice(0, 12).map((k) => `<code>${esc(k)}</code>`).join(" ")}${keys.length > 12 ? ` <span>+${keys.length - 12}</span>` : ""}</span>
         <div class="sxt-btns"><button class="pbtn ghost sx-btn-sm" data-sxt="cancel">${esc(L("Отмена", "Cancel"))}</button>
           <button class="pbtn sx-btn-sm" data-sxt="apply">${esc(L("Применить и перезагрузить", "Apply and reload"))}</button></div>`
      : `<b>${esc(L("Всё уже так, как в файле — менять нечего", "Everything already matches the file — nothing to change"))}</b>
         <div class="sxt-btns"><button class="pbtn ghost sx-btn-sm" data-sxt="cancel">${esc(L("Закрыть", "Close"))}</button></div>`;
  }
  async function apply() {
    if (!pending) return;
    const patch = pending; pending = null;
    try {
      await savePreferences(patch);
      toast(L("Настройки импортированы — перезагружаю интерфейс", "Settings imported — reloading the interface"), "ok");
      setTimeout(() => location.reload(), 700);
    } catch (e) { toast(e.message || String(e), "err"); }
  }

  document.addEventListener("click", (event) => {
    const t = event.target instanceof Element ? event.target : null;
    if (!t) return;
    if (t.closest("#tr-export-settings")) { event.preventDefault(); doExport(); }
    else if (t.closest("#tr-import-settings")) { event.preventDefault(); pick(); }
    else if (t.closest('[data-sxt="apply"]')) { event.preventDefault(); apply(); }
    else if (t.closest('[data-sxt="cancel"]')) { event.preventDefault(); pending = null; const box = document.getElementById("tr-settings-confirm"); if (box) { box.hidden = true; box.innerHTML = ""; } }
  });

  window.MemuSettingsTransfer = Object.freeze({ html, exportable, parse, diff, _showConfirm: showConfirm });
})();
