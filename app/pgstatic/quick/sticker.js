/* Round 27: a sticker window (see sticker.html). Reads /api/quick/features/stickers/<id>, renders the answer
   with QuickMarkdown; double click edits (Ctrl+Enter saves, Esc cancels); the header moves the window and the
   corner grip resizes it — natively (POST …/stickers {action: drag|resize}), the page only starts it. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const params = new URLSearchParams(location.search);
  const NATIVE = root.dataset.native === "1";
  const ID = String(params.get("id") || "");
  const S = { sticker: null, lang: root.lang === "en" ? "en" : "ru", editing: false };
  const tr = (ru, en) => (S.lang === "en" ? en : ru);
  const esc = (window.QuickMarkdown && window.QuickMarkdown.esc) || ((v) => String(v));
  const ICON = {
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    color: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><circle cx="8.5" cy="10" r="1.2" fill="currentColor"/><circle cx="12" cy="7.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="10" r="1.2" fill="currentColor"/></svg>',
  };
  const COLORS = [["", "var(--panel)"], ["yellow", "#e8b931"], ["green", "#4fae6a"], ["blue", "#4a8fe0"], ["pink", "#e0628f"], ["violet", "#9a6be0"]];

  async function api(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lang: S.lang, ...body }) };
    const response = await fetch(path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    return data;
  }
  function toast(text) {
    const el = $("st-toast");
    el.textContent = text; el.classList.add("on");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("on"), 1500);
  }
  async function appearance() {
    try {
      const data = await api("/api/quick/config");
      const prefs = data.prefs || {};
      const theme = prefs.theme === "light" ? "light" : "dark";
      root.dataset.theme = theme;
      root.dataset.accent = /^[a-z]{2,24}$/.test(prefs.accent_color || "") ? prefs.accent_color : "terracotta";
      const surface = prefs["surface_" + theme] || "";
      if (surface) root.dataset.surface = surface; else delete root.dataset.surface;
      root.dataset.motion = prefs.ui_motion === "off" ? "off" : (prefs.ui_motion === "reduced" ? "reduced" : "full");
      S.lang = prefs.language === "en" ? "en" : "ru";
      root.lang = S.lang;
    } catch (e) { /* the first-paint cache stays */ }
    labels();
  }
  function labels() {
    const set = (id, icon, label) => { const b = $(id); b.innerHTML = icon; b.title = label; b.setAttribute("aria-label", label); };
    set("st-color", ICON.color, tr("Цвет", "Colour"));
    set("st-copy", ICON.copy, tr("Копировать (Ctrl+C)", "Copy (Ctrl+C)"));
    set("st-close", ICON.x, tr("Закрыть стикер (Ctrl+W) — он останется в списке «Стикеры»", "Close the sticker (Ctrl+W) — it stays in the «Stickers» list"));
    $("st-body").title = tr("Двойной щелчок — изменить", "Double-click to edit");
  }
  function render() {
    const sticker = S.sticker;
    if (!sticker) {
      $("st-title").textContent = tr("Стикер удалён", "The sticker was deleted");
      $("st-body").innerHTML = `<p class="st-empty">${esc(tr("Этого стикера больше нет.", "This sticker no longer exists."))}</p>`;
      return;
    }
    root.dataset.color = sticker.color || "";
    $("st-title").textContent = sticker.title || tr("Стикер", "Sticker");
    document.title = "MemuAI · " + (sticker.title || "sticker");
    $("st-body").innerHTML = window.QuickMarkdown ? QuickMarkdown.render(sticker.text, { labels: { copy: tr("Копировать", "Copy") } }) : esc(sticker.text);
    $("st-colors").innerHTML = COLORS.map(([id, css]) => `<button type="button" data-color="${id}" style="background:${css}" aria-pressed="${(sticker.color || "") === id}"
      title="${esc(id ? id : tr("Без цвета", "No colour"))}" aria-label="${esc(id ? id : tr("Без цвета", "No colour"))}"></button>`).join("");
  }
  async function load() {
    try { S.sticker = (await api("/api/quick/features/stickers/" + encodeURIComponent(ID))).sticker; }
    catch (e) { S.sticker = null; }
    render();
  }
  async function copy() {
    const text = S.sticker ? S.sticker.text : "";
    try { await navigator.clipboard.writeText(text); toast(tr("Скопировано", "Copied")); }
    catch (e) {
      try { await api("/api/quick/window", { action: "copy", text }); toast(tr("Скопировано", "Copied")); }
      catch (_) { toast(tr("Не удалось скопировать", "Copy failed")); }
    }
  }
  async function close() {
    try { await api("/api/quick/features/stickers", { action: "close", id: ID }); } catch (e) { /* the window may already be gone */ }
    if (!NATIVE) window.close();
  }
  function edit(on) {
    if (!S.sticker) return;
    S.editing = on;
    $("st-edit").hidden = !on; $("st-body").hidden = on;
    if (on) { $("st-edit").value = S.sticker.text; $("st-edit").focus(); }
  }
  async function save() {
    const text = $("st-edit").value;
    if (!text.trim()) { toast(tr("Пустой стикер не сохраняется — закройте его", "An empty sticker is not saved — close it")); return; }
    try { S.sticker = (await api("/api/quick/features/stickers", { action: "update", id: ID, text })).sticker; toast(tr("Сохранено", "Saved")); }
    catch (e) { toast(e.message); }
    edit(false); render();
  }
  function native(action) { if (NATIVE) api("/api/quick/features/stickers", { action, id: ID }).catch(() => {}); }

  $("st-head").addEventListener("pointerdown", (e) => { if (e.button === 0 && !e.target.closest("button")) { e.preventDefault(); native("drag"); } });
  $("st-grip").addEventListener("pointerdown", (e) => { if (e.button === 0) { e.preventDefault(); native("resize"); } });
  $("st-copy").onclick = copy;
  $("st-close").onclick = close;
  $("st-color").onclick = () => { const open = $("st-colors").hidden; $("st-colors").hidden = !open; $("st-color").setAttribute("aria-expanded", String(open)); };
  $("st-colors").onclick = async (e) => {
    const btn = e.target.closest("[data-color]");
    if (!btn || !S.sticker) return;
    $("st-colors").hidden = true; $("st-color").setAttribute("aria-expanded", "false");
    try { S.sticker = (await api("/api/quick/features/stickers", { action: "update", id: ID, color: btn.dataset.color })).sticker; } catch (err) { toast(err.message); }
    render();
  };
  $("st-body").addEventListener("dblclick", () => edit(true));
  $("st-edit").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); edit(false); }
  });
  document.addEventListener("keydown", (e) => {
    if (S.editing) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.code === "KeyC" && !String(getSelection())) { e.preventDefault(); copy(); }
    else if (mod && e.code === "KeyW") { e.preventDefault(); close(); }
    else if (e.key === "Escape" && !$("st-colors").hidden) { $("st-colors").hidden = true; $("st-color").setAttribute("aria-expanded", "false"); }
  });
  document.addEventListener("pointerdown", (e) => {
    if (!$("st-colors").hidden && !e.target.closest("#st-colors, #st-color")) { $("st-colors").hidden = true; $("st-color").setAttribute("aria-expanded", "false"); }
  });
  window.addEventListener("focus", appearance);

  window.Sticker = { reload: () => { load(); return true; }, state: () => S };
  labels();
  appearance().then(load);
})();
