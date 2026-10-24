/* MemuAI — Telegram updates for panels (Round 24).
 * Adds a bell switch and "what to send" to every eligible panel in
 * Settings → Panels (the list panels.js draws). Data: /api/pg/panels-telegram
 * (telegram_panels.register_window_routes). Self-contained: panels.js is not
 * changed; the list is re-decorated whenever panels.js re-renders it. */
(function () {
  "use strict";
  const ru = () => !/^en/i.test(document.documentElement.lang || "ru");
  const t = (a, b) => (ru() ? a : b);
  let state = null, loading = null, scheduled = false;
  // r30: SVG bells in the same line style as the rest of the page (the emoji drew as a box without an emoji font
  // and looked foreign next to the line icons)
  const svg = (d) => `<svg class="mp-tg-ico" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const BELL = svg('<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>');
  const BELL_OFF = svg('<path d="M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5"/><path d="M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><path d="m2 2 20 20"/>');
  const MODES = [["changes", "Только важное", "Important only"], ["every", "Любые изменения", "Every change"], ["digest", "Сводка раз в день", "Daily digest"]];

  async function load(force) {
    if (state && !force) return state;
    if (!loading) loading = fetch("/api/pg/panels-telegram").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((d) => { loading = null; if (d && Array.isArray(d.eligible)) state = { ...d, subs: d.subs || {} }; return state; });
    return loading;
  }
  async function save(pid, patch) {
    const r = await fetch("/api/pg/panels-telegram/" + encodeURIComponent(pid), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    state.subs[pid] = data.sub;
    return data.sub;
  }
  function style() {
    if (document.getElementById("mp-tg-style")) return;
    const el = document.createElement("style");
    el.id = "mp-tg-style";
    el.textContent = `.mp-tg{display:inline-flex;align-items:center;gap:4px;margin-left:4px}
.mp-tg button{display:grid;place-items:center;width:28px;height:28px;border:0;background:transparent;color:var(--muted,inherit);cursor:pointer;padding:0;border-radius:8px}
.mp-tg button:hover{color:var(--text,inherit);background:color-mix(in srgb,var(--text,#888) 7%,transparent)}
.mp-tg button[aria-pressed="true"]{color:var(--accent,#e89a7e);background:color-mix(in srgb,var(--accent,#e89a7e) 14%,transparent)}
.mp-tg-note .mp-tg-ico{width:13px;height:13px;vertical-align:-2px;margin-right:2px}
.mp-tg select{font:inherit;font-size:12px;padding:2px 4px;border-radius:8px;border:1px solid var(--border,rgba(127,127,127,.3));background:transparent;color:inherit;max-width:150px}
.mp-tg-note{font-size:12px;color:var(--muted,inherit);margin:6px 2px 0}`;
    document.head.appendChild(el);
  }
  function decorate() {
    const force = scheduled === "force";
    scheduled = false;
    const lists = document.querySelectorAll(".mp-set-list");
    if (!lists.length) return;
    // A panel added after the first load is unknown to `state` — re-read it
    // when the settings list is (re)built, so its bell appears without a reload.
    const unknown = !state || [...document.querySelectorAll(".mp-set-item[data-panel-id]")].some((i) => !(state.known || []).includes(i.dataset.panelId));
    load(force || unknown).then((s) => {
      if (s && !s.known) s.known = [...document.querySelectorAll(".mp-set-item[data-panel-id]")].map((i) => i.dataset.panelId);
      if (!s) return;
      style();
      lists.forEach((list) => {
        if (!list.querySelector(".mp-tg-note") && s.eligible.length) {
          const note = document.createElement("p");
          note.className = "mp-tg-note";
          note.innerHTML = BELL + (s.ready ? t(" — присылать обновления панели в Telegram (без повторов и спама; тихие часы бота учитываются).", " — send this panel's updates to Telegram (no duplicates or spam; the bot's quiet hours apply).")
            : t(" — обновления панелей в Telegram: сначала включите и привяжите бота (Настройки → Telegram).", " — panel updates in Telegram: switch on and pair the bot first (Settings → Telegram)."));
          list.after(note);
        }
        list.querySelectorAll(".mp-set-item[data-panel-id]").forEach((item) => {
          const pid = item.dataset.panelId;
          if (!s.eligible.includes(pid)) return;
          let box = item.querySelector(".mp-tg");
          if (box && box.dataset.rev === JSON.stringify(s.subs[pid] || {})) return;
          if (!box) { box = document.createElement("span"); box.className = "mp-tg"; const edit = item.querySelector("[data-set-edit]"); (edit?.parentNode || item).insertBefore(box, edit || null); }
          const sub = s.subs[pid] || { on: false, mode: "changes" };
          box.dataset.rev = JSON.stringify(s.subs[pid] || {});
          box.innerHTML = `${sub.on ? `<select data-tg-mode aria-label="${t("Что присылать", "What to send")}">${MODES.map(([v, a, b]) => `<option value="${v}"${sub.mode === v ? " selected" : ""}>${t(a, b)}</option>`).join("")}</select>` : ""}
            <button type="button" data-tg-bell aria-pressed="${!!sub.on}" aria-label="Telegram" title="${sub.on ? t("Не присылать в Telegram", "Stop Telegram updates") : t("Присылать обновления в Telegram", "Send updates to Telegram")}">${sub.on ? BELL : BELL_OFF}</button>`;
          box.querySelector("[data-tg-bell]").onclick = async (e) => { e.stopPropagation(); try { await save(pid, { on: !sub.on }); } catch (err) { console.warn(err); } schedule(); };
          const sel = box.querySelector("[data-tg-mode]");
          if (sel) { sel.onclick = (e) => e.stopPropagation(); sel.onchange = async () => { try { await save(pid, { mode: sel.value }); } catch (err) { console.warn(err); } schedule(); }; }
        });
      });
    });
  }
  function schedule(force) { if (force) scheduled = scheduled ? "force" : scheduled; if (!scheduled) { scheduled = force ? "force" : true; requestAnimationFrame(decorate); } }
  const observer = new MutationObserver((records) => {
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n.nodeType === 1 && !n.closest?.(".mp-tg") && (n.matches?.(".mp-set-list, .mp-set-item, .mp-set-card") || n.querySelector?.(".mp-set-list"))) { schedule(!!(n.matches?.(".mp-set-list") || n.querySelector?.(".mp-set-list"))); return; }
      }
    }
  });
  function start() { observer.observe(document.body, { childList: true, subtree: true }); load(true).then(() => schedule()); }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
  window.MemuPanelsTelegram = { refresh: () => load(true).then(() => schedule()) };
})();
