/* Round 44: automatic model discovery uses the same catalogue engine as Refresh. */
"use strict";
window.MemuModelRefresh = (() => {
  const section = document.getElementById("sg-sec-routing");
  if (!section) return {};
  const en = () => typeof LANG !== "undefined" && LANG === "en";
  const text = (ru, english) => en() ? english : ru;
  const group = document.createElement("div");
  group.className = "sg-group mr-group";
  group.id = "model-refresh-settings";
  group.innerHTML = `<h4 class="sg-gtitle" id="mr-title"></h4><div class="sg-card">
    <div class="sg-row" data-k="автообновление моделей при запуске catalogue catalog models automatic refresh startup">
      <div class="sg-text"><label class="sg-label" id="mr-start-label" for="mr-start"></label><div class="sg-desc" id="mr-start-desc"></div></div>
      <div class="sg-ctl"><label class="switch"><input type="checkbox" id="mr-start" data-setting="model_refresh_on_start" role="switch"><span class="slider"></span></label></div>
    </div>
    <div class="sg-row" data-k="периодическое обновление каталог интервал минут interval automatic refresh models">
      <div class="sg-text"><label class="sg-label" id="mr-interval-label" for="mr-interval"></label><div class="sg-desc" id="mr-interval-desc"></div></div>
      <div class="sg-ctl"><span class="sg-num"><input type="number" id="mr-interval" min="0" max="10080" step="1" required data-setting="model_refresh_interval"><span class="sg-unit" id="mr-unit"></span></span></div>
    </div>
    <div class="mr-footer"><div class="mr-status" id="mr-status" role="status" aria-live="polite"></div><button type="button" class="btn small" id="mr-run"></button></div>
    <div class="mr-catalogue sg-desc" id="mr-antigravity-status" hidden></div>
  </div>`;
  section.insertBefore(group, section.querySelector(".sg-more"));
  let running = false, requesting = false, loading = false, last = null, catalogues = [];
  const start = group.querySelector("#mr-start"), interval = group.querySelector("#mr-interval");
  const run = group.querySelector("#mr-run"), status = group.querySelector("#mr-status");
  function label() {
    const values = {
      "mr-title": text("Актуальность каталога", "Catalogue updates"),
      "mr-start-label": text("Обновлять модели при запуске Proxy", "Refresh models when Proxy starts"),
      "mr-start-desc": text("Все подключённые провайдеры проверяются в фоне. Окно открывается сразу.", "All connected providers are checked in the background. The window opens immediately."),
      "mr-interval-label": text("Повторять каждые", "Refresh every"),
      "mr-interval-desc": text("0 — только вручную или при запуске. Antigravity проверяется не реже чем раз в 15 минут при включённом автообновлении. При ошибке каталог сохраняется; добавленные вручную модели остаются.", "0 means manual or startup refresh only. With periodic refresh enabled, Antigravity is checked at least every 15 minutes. Errors preserve the catalogue; manually added models remain."),
      "mr-unit": text("мин", "min"),
    };
    Object.entries(values).forEach(([id, value]) => { group.querySelector("#" + id).textContent = value; });
    const ag = catalogues.find(row => row.provider === "antigravity");
    const detail = group.querySelector("#mr-antigravity-status");
    detail.hidden = !ag;
    if (ag) {
      const stamp = ag.checked ? new Date(ag.checked * 1000).toLocaleString(en() ? "en-GB" : "ru-RU") : text("ожидается", "pending");
      detail.replaceChildren(document.createTextNode(text(
        `Antigravity · серверный селектор IDE API · проверка: ${stamp} · аккаунты: ${ag.verified_accounts}/${ag.accounts}. `,
        `Antigravity · IDE API server selector · checked: ${stamp} · accounts: ${ag.verified_accounts}/${ag.accounts}. `)));
      const link = document.createElement("a");
      link.href = "https://www.antigravity.google/docs/models/"; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = text("Доступность моделей и клиентов", "Model and client availability");
      detail.append(link, document.createTextNode(text(
        " — Gemini 4 Argon может быть доступна в App / CLI, но отсутствовать в селекторе IDE. MemuAI не подменяет данные сервера.",
        " — Gemini 4 Argon may be available in App / CLI but absent from the IDE selector. MemuAI uses the server's actual catalogue.")));
      if (ag.last_error) detail.append(document.createTextNode(" " + text("Ошибка последней проверки: ", "Last refresh error: ") + ag.last_error));
    }
    const busy = running || requesting;
    run.textContent = busy ? text("Обновление…", "Refreshing…") : text("Обновить сейчас", "Refresh now");
    run.disabled = busy;
    if (busy) status.textContent = text("Получаем списки моделей от провайдеров…", "Fetching provider model lists…");
    else if (last?.finished) {
      const date = new Date(last.finished * 1000).toLocaleString(en() ? "en-GB" : "ru-RU", {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
      status.textContent = text("Обновлено ", "Updated ") + date + ` · +${last.added || 0} / −${last.pruned || 0}` +
        (last.unavailable ? text(` · без ответа: ${last.unavailable}`, ` · unavailable: ${last.unavailable}`) : "");
    } else status.textContent = text("Ещё не обновляли в этом запуске", "No refresh during this session yet");
    status.classList.toggle("mr-warning", !!last?.errors?.length);
    status.title = (last?.errors || []).join("\n");
  }
  function sync() {
    const settings = typeof settingsCache === "undefined" ? {} : settingsCache;
    if (document.activeElement !== start) start.checked = settings.model_refresh_on_start !== false;
    if (document.activeElement !== interval) interval.value = settings.model_refresh_interval ?? 360;
    label();
  }
  async function refreshStatus() {
    const tab = document.getElementById("tab-settings");
    if (loading || document.hidden || typeof api !== "function" || section.hidden || (tab && !tab.classList.contains("active"))) return;
    loading = true;
    try { const data = await api("/api/admin/models/auto-refresh"); running = !!data.running; last = data.last; catalogues = Array.isArray(data.catalogues) ? data.catalogues : []; label(); }
    catch (_) { /* authentication/network feedback belongs to the shared API helper */ }
    finally { loading = false; }
  }
  group.addEventListener("change", async (event) => {
    const input = event.target.closest("[data-setting]");
    if (!input) return;
    if (!input.checkValidity()) { input.reportValidity(); return; }
    const value = input.type === "checkbox" ? input.checked : Number(input.value);
    await autoSaveSetting({ [input.dataset.setting]: value });
    sync();
  });
  run.addEventListener("click", async () => {
    if (running || requesting) return;
    requesting = true; label();
    try {
      const result = await api("/api/admin/models/refresh", { method: "POST", body: {} });
      if (result.busy) { await refreshStatus(); return; }
      last = result;
      if (typeof loadModels === "function") await loadModels();
      if (result.errors?.length) toast(result.errors.join("\n"), "warn", 6000);
    } catch (error) { toast(error.message, "err"); }
    finally { requesting = false; running = false; label(); await refreshStatus(); }
  });
  if (window.MemuSettings) {
    for (const name of ["sync", "show", "relabel"]) {
      const previous = window.MemuSettings[name];
      window.MemuSettings[name] = function (...args) { const result = previous.apply(this, args); sync(); refreshStatus(); return result; };
    }
  }
  const timer = setInterval(refreshStatus, 15000);
  window.addEventListener("pagehide", () => clearInterval(timer), { once: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });
  sync();
  return { sync, refreshStatus };
})();
