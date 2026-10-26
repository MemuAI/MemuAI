

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || !Q.registerPanel) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const svg = (d, fill) => `<svg viewBox="0 0 24 24" ${fill ? 'fill="currentColor"' : 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'} aria-hidden="true">${d}</svg>`;
  const IC = {
    play: svg('<path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/>', true),
    pause: svg('<rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/>', true),
    next: svg('<path d="M6 5.5v13l9-6.5zM17 5v14"/>'),
    prev: svg('<path d="M18 5.5v13L9 12zM7 5v14"/>'),
    open: svg('<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
    resume: svg('<path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/>', true),
    note: svg('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
    close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    heart: svg('<path d="M12 20s-7-4.4-9.2-8.6C1.2 8.3 3 5 6.3 5c2 0 3.2 1.1 3.9 2.2h3.6C14.5 6.1 15.7 5 17.7 5 21 5 22.8 8.3 21.2 11.4 19 15.6 12 20 12 20z"/>'),
    heartOn: svg('<path d="M12 20s-7-4.4-9.2-8.6C1.2 8.3 3 5 6.3 5c2 0 3.2 1.1 4.2 2.4h3C14.5 6.1 15.7 5 17.7 5 21 5 22.8 8.3 21.2 11.4 19 15.6 12 20 12 20z"/>', true),
    vol: svg('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>'),
    mute: svg('<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/>'),
    np: svg('<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M10 9.2v5.6l4.8-2.8z"/>'),
  };
  const S = { modules: [], phrases: [], ready: false, card: null, player: null, es: null, shown: false, raf: 0, ringTimer: 0, ring: null, ctx: null, seed: 1,
    expect: null, touched: false, tick: 0, fade: 0, meta: new Map(), volTimer: 0 };
  const CACHE = "memu-qmod-catalogue";
  const reduced = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "reduced";
  const widget = (slot) => {
    for (const m of S.modules) for (const w of m.widgets || []) if (w.slot === slot && (w.values || {}).enabled !== false) return { module: m.module, ...w };
    return null;
  };

  function compile(phrase) {
    const names = [];
    const parts = String(phrase).trim().toLowerCase().split(/\{([a-z_][a-z0-9_]*)\}/);
    let src = "";
    parts.forEach((part, i) => {
      if (i % 2) { names.push(part); src += "(.+?)"; } else src += part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
    });
    try { return { re: new RegExp("^\\s*" + src + "\\s*[.!?]*\\s*$", "i"), names }; } catch (e) { return null; }
  }
  function catalogue(modules) {
    S.modules = modules || [];
    S.phrases = [];
    for (const m of S.modules) for (const c of m.commands || []) for (const p of [...(c.phrases || []), ...(c.phrases_en || [])]) {
      const rx = compile(p);
      if (rx) S.phrases.push({ module: m.module, command: c.id, danger: !!c.danger, ...rx });
    }
  }
  try {
    const saved = JSON.parse(localStorage.getItem(CACHE) || "null");
    if (saved && Array.isArray(saved.modules)) { catalogue(saved.modules); S.ready = true; }
  } catch (e) {  }
  async function load() {
    try {
      const data = await Q.api("/api/pg/modint?lang=" + (Q.lang() === "en" ? "en" : "ru"));
      catalogue(data.modules || []);
      try {
        localStorage.setItem(CACHE, JSON.stringify({ modules: S.modules.map((m) => ({ module: m.module, title: m.title, widgets: m.widgets || [],
          commands: (m.commands || []).map((c) => ({ id: c.id, phrases: c.phrases, phrases_en: c.phrases_en, danger: c.danger })) })) }));
      } catch (e) {  }
    } catch (e) { if (!S.ready) catalogue([]); }
    S.ready = true;
    syncWidgets();
  }
  function matchLocal(text) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean || clean.length > 200 || clean.includes("\n")) return null;
    for (const p of S.phrases) {
      const m = p.re.exec(clean);
      if (m) return { module: p.module, command: p.command, danger: p.danger, args: Object.fromEntries(p.names.map((n, i) => [n, m[i + 1].trim().replace(/^[«"']|[»"']$/g, "")])) };
    }
    return null;
  }

  let cardPanel = null;
  function ensureCardPanel() {
    if (cardPanel) return cardPanel;
    cardPanel = Q.registerPanel("module-card", (el) => {
      el.classList.add("qmod-host");
      el.addEventListener("click", onCardClick);
      return { hide() { S.card = null; syncWidgets(); } };
    }, { slot: "bottom", hidden: true, order: 5 });
    return cardPanel;
  }
  function cardHtml(ui) {
    if (ui.skeleton) {
      return `<article class="qmod-card is-skel" aria-busy="true"><div class="qmod-main"><span class="qmod-cover qmod-sk"></span>
        <div class="qmod-meta"><i class="qmod-sk qmod-sk-l"></i><i class="qmod-sk qmod-sk-s"></i></div></div></article>`;
    }
    if (ui.player) return `<article class="qmod-card qmod-card-player"><div class="qmod-player-slot"></div><button type="button" class="qmod-x" data-close aria-label="${esc(t("Скрыть", "Hide"))}">${IC.close}</button></article>`;
    const card = ui.card || {};
    const title = card.title || (ui.ok ? t("Готово", "Done") : t("Не получилось", "Did not work"));
    const sub = card.subtitle || (card.title ? "" : ui.text) || "";
    const cover = card.cover ? `<img class="qmod-cover" src="${esc(card.cover)}" alt="" decoding="async">`
      : `<span class="qmod-cover qmod-gen" aria-hidden="true">${card.kind === "track" ? esc(String(title).charAt(0).toUpperCase()) : IC.note}</span>`;
    const actions = (card.actions || []).map((a, i) => `<button type="button" class="qmod-act${a.toggle ? " main" : ""}" data-act="${i}" title="${esc(a.label)}" aria-label="${esc(a.label)}">${IC[a.icon] || esc(a.label)}</button>`).join("");
    const items = (card.items || []).slice(0, 6).map((it, i) => `<button type="button" class="qmod-item" data-item="${i}"${it.command || it.open ? "" : " disabled"}>${it.cover ? `<img src="${esc(it.cover)}" alt="">`
      : it.rank ? `<i class="qmod-rank" data-place="${+it.rank}">${+it.rank}</i>` : it.mono ? `<i class="qmod-mono">${esc(it.mono)}</i>` : ""}<span><b>${esc(it.title)}</b>${it.subtitle ? `<small>${esc(it.subtitle)}</small>` : ""}</span>${it.value != null ? `<em>${esc(it.value)}</em>` : ""}</button>`).join("");
    const confirm = ui.needs_confirm ? `<div class="qmod-confirm"><span>${esc(t("Выполнить это действие?", "Run this action?"))}</span><button type="button" class="qmod-btn" data-confirm>${esc(t("Выполнить", "Run"))}</button></div>` : "";
    return `<article class="qmod-card" data-kind="${esc(card.kind || "info")}" data-ok="${ui.ok ? 1 : 0}" style="${/^#[0-9a-f]{3,8}$/i.test(card.accent || "") ? `--qmod-c:${card.accent}` : ""}">
      <div class="qmod-main">${cover}<div class="qmod-meta"><b class="qmod-title">${esc(title)}</b>${sub ? `<span class="qmod-sub">${esc(sub)}</span>` : ""}${card.note ? `<span class="qmod-note">${esc(card.note)}</span>` : ""}</div>
      ${actions ? `<div class="qmod-acts">${actions}</div>` : ""}<button type="button" class="qmod-x" data-close aria-label="${esc(t("Скрыть", "Hide"))}">${IC.close}</button></div>
      ${items ? `<div class="qmod-items">${items}</div>` : ""}${confirm}</article>`;
  }
  function showCard(ui) {

    if (ui && ui.ok && ui.card && ui.card.kind === "track") { adopt(ui.card, ui.card.playing !== false); ui = { ...ui, player: true }; }
    S.card = ui;
    const panel = ensureCardPanel();
    panel.el.innerHTML = cardHtml(ui);
    panel.show();
    syncWidgets();
  }
  async function run(module, command, args, extra = {}) {
    try {
      const out = await Q.api("/api/pg/modint/command", { body: { module, command, args: args || {}, surface: "quick", lang: Q.lang(), ...extra } });
      return { type: "module-card", module, command, ok: !!out.ok, text: out.text || out.error || "", card: out.card || null,
        needs_confirm: !!out.needs_confirm, args: out.args || args || {}, client: out.client || {} };
    } catch (e) {
      return { type: "module-card", module, command, ok: false, text: e.message || String(e), card: null };
    }
  }
  async function onCardClick(e) {
    const ui = S.card;
    if (!ui) return;
    if (e.target.closest("[data-close]")) { cardPanel.hide(); return; }
    if (ui.player) return onPlayerClick(e);
    const card = ui.card || {};
    const act = e.target.closest("[data-act]");
    const item = e.target.closest("[data-item]");
    if (e.target.closest("[data-confirm]")) return showCard(await run(ui.module, ui.command, ui.args, { confirmed: true }));
    const spec = act ? (card.actions || [])[+act.dataset.act] : item ? (card.items || [])[+item.dataset.item] : null;
    if (!spec) return;
    if (spec.open) return openModule(ui.module, spec.detail || {});
    if (spec.command) {
      const next = await run(ui.module, spec.command, spec.args || {});
      if (next.card || !next.ok) showCard(next);
    }
  }
  function openModule(module, detail) {

    Q.api("/api/pg/modint/open", { body: { module, detail } }).catch(() => {});
    Q.hide();
  }

  function listen() {
    if (S.es || !S.shown || document.hidden) return;
    if (!widget("quick.ring") && !widget("quick.card") && !(S.card && S.card.player)) return;
    try {
      S.es = window.MemuLive ? MemuLive.stream("/api/pg/music/player/events") : new EventSource("/api/pg/music/player/events");
      S.es.onmessage = (ev) => {
        let data; try { data = JSON.parse(ev.data); } catch (e) { return; }
        if (data.event === "state") applyState(data.state || {});
      };
      S.es.onerror = () => { stopListen(); setTimeout(listen, 5000); };
    } catch (e) { S.es = null; }
  }
  function stopListen() { if (S.es) { S.es.close(); S.es = null; } }

  function applyState(st) {
    const prev = S.player || {};
    const ex = S.expect;
    if (ex && performance.now() < ex.until) {
      const confirmed = (ex.playing === undefined || !!st.playing === ex.playing) && (!ex.track_id || st.track_id === ex.track_id);
      if (confirmed) S.expect = null;
      else { st = { ...st, playing: prev.playing, track_id: prev.track_id, position_now: pos(), duration: prev.duration }; }
    } else S.expect = null;
    const changed = prev.track_id !== st.track_id;
    st.track = !changed && prev.track ? prev.track : S.meta.get(st.track_id) || null;
    st.at = performance.now();
    S.player = st;
    if (st.track_id && !st.track) trackInfo(st.track_id);
    upNext();
    syncWidgets();
  }
  async function meta(id) {
    if (!id) return null;
    if (S.meta.has(id)) return S.meta.get(id);
    try {
      const out = await Q.api("/api/pg/music/call", { body: { op: "library.get", args: { id }, lang: Q.lang() } });
      if (out.result && out.result.id) { S.meta.set(id, out.result); return out.result; }
    } catch (e) {  }
    return null;
  }
  async function trackInfo(id) {
    const tr = await meta(id);
    if (tr && S.player && S.player.track_id === id) { S.player.track = tr; syncWidgets(); }
  }
  function upNext() {
    const st = S.player;
    if (!st || !Array.isArray(st.queue)) return;
    const i = st.queue.indexOf(st.track_id);
    const id = i >= 0 ? st.queue[i + 1] : "";
    st.next_id = id || "";
    if (id && !S.meta.has(id)) meta(id).then(() => paintPlayer());
  }
  const playing = () => !!(S.player && S.player.playing && !S.player.stale && S.player.track_id);
  const duration = () => { const st = S.player || {}; return +st.duration || +((st.track || {}).duration) || 0; };
  function pos() {
    const st = S.player;
    if (!st) return 0;
    const base = +st.position_now || +st.position || 0;
    const p = playing() ? base + (performance.now() - (st.at || performance.now())) / 1000 * (+st.speed || 1) : base;
    return Math.max(0, duration() ? Math.min(duration(), p) : p);
  }
  function adopt(card, isPlaying) {
    const prev = S.player || {};
    const track = { id: card.id, title: card.title, artist: card.artist || card.subtitle || "", cover: card.cover, accent: card.accent,
      duration: card.duration, liked: !!card.liked };
    if (card.id) S.meta.set(card.id, { ...(S.meta.get(card.id) || {}), ...track });
    S.player = { ...prev, track_id: card.id, playing: isPlaying, stale: false, track,
      position_now: card.position != null ? +card.position : prev.track_id === card.id ? pos() : 0,
      duration: +card.duration || (prev.track_id === card.id ? prev.duration : 0),
      volume: card.volume != null ? +card.volume : prev.volume, at: performance.now() };
    S.expect = { playing: isPlaying, track_id: card.id, until: performance.now() + 2500 };
    upNext();
  }

  const fmt = (sec) => { sec = Math.max(0, Math.floor(sec || 0)); return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0"); };
  function playerShell() {
    return `<div class="qmod-np" data-np-root>
      <button type="button" class="qmod-np-art" data-np="open-np"><span class="qmod-gen">${IC.note}</span><img alt="" decoding="async" hidden></button>
      <div class="qmod-np-meta"><b class="qmod-np-title"></b><small class="qmod-np-sub"></small></div>
      <button type="button" class="qmod-act qmod-like" data-np="like"></button>
      <div class="qmod-np-ctl">
        <button type="button" class="qmod-act" data-np="prev">${IC.prev}</button>
        <button type="button" class="qmod-act main" data-np="toggle"></button>
        <button type="button" class="qmod-act" data-np="next">${IC.next}</button>
      </div>
      <div class="qmod-np-line">
        <span class="qmod-np-time qmod-np-cur">0:00</span>
        <div class="qmod-seek" data-np-seek role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="100"><i class="qmod-seek-fill"></i></div>
        <span class="qmod-np-time qmod-np-dur">0:00</span>
        <span class="qmod-vol"><button type="button" class="qmod-act sm" data-np="mute"></button><input type="range" min="0" max="100" step="1" data-np-vol></span>
        <button type="button" class="qmod-act sm" data-np="open-np">${IC.np}</button>
      </div></div>`;
  }
  function playerHost() {
    if (S.card && S.card.player && cardPanel && !cardPanel.el.hidden) return cardPanel.el.querySelector(".qmod-player-slot");
    return npPanel && !npPanel.el.hidden ? npPanel.el : null;
  }
  function paintPlayer() {
    const host = playerHost();
    if (!host || !S.player) return;
    let root = host.querySelector("[data-np-root]");
    if (!root) {
      host.innerHTML = playerShell();
      root = host.querySelector("[data-np-root]");
      const seek = root.querySelector("[data-np-seek]");
      seek.addEventListener("pointerdown", (e) => { const r = seek.getBoundingClientRect(); seekTo(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration()); });
      seek.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); seekTo(pos() + (e.key === "ArrowLeft" ? -5 : 5)); } });
      root.querySelector("[data-np-vol]").addEventListener("input", (e) => setVolume(+e.target.value / 100));
    }
    const st = S.player, tr = st.track || S.meta.get(st.track_id) || {};
    const on = playing();
    root.style.setProperty("--qmod-c", /^#[0-9a-f]{6}$/i.test(tr.accent || "") ? tr.accent : "var(--accent, #d97757)");
    root.classList.toggle("is-playing", on);
    root.classList.toggle("is-wait", !!S.expect);
    const img = root.querySelector(".qmod-np-art img");
    if (tr.cover) { if (img.getAttribute("src") !== tr.cover) img.src = tr.cover; img.hidden = false; } else { img.hidden = true; img.removeAttribute("src"); }
    root.querySelector(".qmod-np-title").textContent = tr.title || "—";
    const nxt = st.next_id && S.meta.get(st.next_id);
    root.querySelector(".qmod-np-sub").innerHTML = esc(tr.artist || "") + (nxt ? `<span class="qmod-np-next"> · ${esc(t("Далее", "Up next"))}: ${esc(nxt.title || "")}</span>` : "");
    const like = root.querySelector('[data-np="like"]');
    like.innerHTML = tr.liked ? IC.heartOn : IC.heart; like.classList.toggle("on", !!tr.liked);
    const toggle = root.querySelector('[data-np="toggle"]');
    toggle.innerHTML = on ? IC.pause : IC.play;
    const vol = typeof st.volume === "number" ? st.volume : 1;
    const range = root.querySelector("[data-np-vol]");
    if (document.activeElement !== range) range.value = String(Math.round(vol * 100));
    range.style.setProperty("--v", Math.round(vol * 100) + "%");
    root.querySelector('[data-np="mute"]').innerHTML = vol > 0.001 ? IC.vol : IC.mute;
    const label = (sel, text) => root.querySelectorAll(sel).forEach((b) => { b.title = text; b.setAttribute("aria-label", text); });
    label('[data-np="prev"]', t("Назад", "Previous")); label('[data-np="next"]', t("Дальше", "Next"));
    label('[data-np="toggle"]', on ? t("Пауза", "Pause") : t("Играть", "Play"));
    label('[data-np="like"]', tr.liked ? t("Убрать из любимых", "Remove from liked") : t("В любимые", "Like"));
    label('[data-np="mute"]', t("Громкость", "Volume")); label("[data-np-vol]", t("Громкость", "Volume"));
    label('[data-np="open-np"]', t("Открыть «Сейчас играет»", "Open “Now playing”")); label("[data-np-seek]", t("Перемотка", "Seek"));
    progress(root);
  }

  function progress(root) {
    root = root || (playerHost() || document).querySelector("[data-np-root]");
    if (!root) return;
    const dur = duration(), p = pos(), k = dur ? Math.max(0, Math.min(1, p / dur)) : 0;
    const fill = root.querySelector(".qmod-seek-fill");
    fill.style.animation = "none";
    fill.style.setProperty("--k", k.toFixed(4));
    if (playing() && dur && dur - p > 0.2) { void fill.offsetWidth; fill.style.animation = `qmod-progress ${((dur - p) / (+S.player.speed || 1)).toFixed(2)}s linear forwards`; }
    root.querySelector("[data-np-seek]").setAttribute("aria-valuenow", String(Math.round(k * 100)));
    clock(root);
  }
  function clock(root) {
    clearTimeout(S.tick); S.tick = 0;
    root = root || (playerHost() || document).querySelector("[data-np-root]");
    if (!root || !root.isConnected) return;
    root.querySelector(".qmod-np-cur").textContent = fmt(pos());
    root.querySelector(".qmod-np-dur").textContent = fmt(duration());
    if (playing() && S.shown && !document.hidden) S.tick = setTimeout(() => clock(), 1000 - (pos() * 1000) % 1000 + 20);
  }

  const musicCall = (op, args) => Q.api("/api/pg/music/call", { body: { op, args, lang: Q.lang() } });
  function freeze() { if (S.player) { S.player.position_now = pos(); S.player.at = performance.now(); } }
  async function control(act) {
    const st = S.player;
    if (!st || !st.track_id) return;
    S.touched = true;
    if (act === "toggle") {
      const willPlay = !playing();
      freeze(); st.playing = willPlay; st.stale = false;
      S.expect = { playing: willPlay, until: performance.now() + 2500 };
      syncWidgets();

      if (!willPlay) return musicCall("player.command", { cmd: "pause", target: "pc" }).catch(() => {});
      if (st.online && st.device !== "phone") return musicCall("player.command", { cmd: "play", target: "pc" }).catch(() => {});
      const ui = await run("music", "resume", {});
      if (!ui.ok) showCard(ui);
      return;
    }
    if (act === "next" || act === "prev") {
      freeze();
      if (act === "prev" && pos() > 3) { st.position_now = 0; st.at = performance.now(); syncWidgets(); }
      else {
        const i = (st.queue || []).indexOf(st.track_id), id = (st.queue || [])[i + (act === "next" ? 1 : -1)];
        const tr = id && S.meta.get(id);
        if (tr) { st.track_id = id; st.track = tr; st.position_now = 0; st.at = performance.now(); st.duration = +tr.duration || 0; st.playing = true; upNext(); }
        S.expect = { playing: true, track_id: id || undefined, until: performance.now() + 2500 };
        syncWidgets();
      }
      const ui = await run("music", act, {});
      if (ui.ok && ui.card && ui.card.kind === "track") { adopt(ui.card, true); syncWidgets(); } else if (!ui.ok) showCard(ui);
      return;
    }
    if (act === "like") {
      const tr = st.track || {};
      const liked = !tr.liked;
      st.track = { ...tr, liked }; S.meta.set(st.track_id, { ...(S.meta.get(st.track_id) || {}), ...st.track });
      paintPlayer();
      musicCall("library.like", { id: st.track_id, liked }).catch(() => { st.track = { ...st.track, liked: !liked }; paintPlayer(); });
      return;
    }
    if (act === "mute") { setVolume((typeof st.volume === "number" ? st.volume : 1) > 0.001 ? 0 : (S.lastVol || 0.8)); return; }
    if (act === "open-np") openModule("music", { route: "np" });
  }
  function seekTo(sec) {
    const st = S.player;
    if (!st || !duration()) return;
    sec = Math.max(0, Math.min(duration() - 0.5, sec));
    st.position_now = sec; st.at = performance.now();
    S.touched = true;
    progress();
    musicCall("player.command", { cmd: "seek", position: Math.round(sec * 10) / 10, target: "pc" }).catch(() => {});
  }
  function setVolume(v) {
    const st = S.player;
    if (!st) return;
    if ((typeof st.volume === "number" ? st.volume : 1) > 0.001) S.lastVol = st.volume;
    st.volume = Math.max(0, Math.min(1, v));
    S.touched = true;
    paintPlayer();
    clearTimeout(S.volTimer);
    S.volTimer = setTimeout(() => musicCall("player.command", { cmd: "volume", value: Math.round(st.volume * 100) / 100, target: "pc" }).catch(() => {}), 120);
  }
  function onPlayerClick(e) {
    const b = e.target.closest("[data-np]");
    if (b) control(b.dataset.np);
  }

  let npPanel = null;
  function syncWidgets() {
    const card = widget("quick.card");
    const trackCard = S.card && S.card.player && cardPanel && !cardPanel.el.hidden;
    const has = !!(S.player && S.player.track_id && (S.player.track || S.meta.get(S.player.track_id)));

    if (card && has && !trackCard && (playing() || S.touched)) {
      if (!npPanel) npPanel = Q.registerPanel("module-nowplaying", (el) => { el.classList.add("qmod-host"); el.addEventListener("click", onPlayerClick); return {}; }, { slot: "top", hidden: true, order: 90 });
      npPanel.show();
    } else if (npPanel) npPanel.hide();
    if (has) paintPlayer(); else clearTimeout(S.tick);
    const ring = widget("quick.ring");
    if (ring && playing()) startRing(ring); else stopRing(true);
  }

  function hash(s) { let h = 2166136261; for (const ch of String(s || "")) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
  function startRing(spec) {
    const host = document.getElementById("qa-card");
    if (!host) return;
    if (S.fade) { clearTimeout(S.fade); S.fade = 0; if (S.ring) S.ring.classList.remove("out"); }
    if (!S.ring) {
      S.ring = document.createElement("canvas");
      S.ring.className = "qmod-ring";
      S.ring.setAttribute("aria-hidden", "true");
      const aura = host.querySelector(".qa-aura");
      if (aura) aura.after(S.ring); else host.prepend(S.ring);
      S.ctx = S.ring.getContext("2d");
    }
    const tr = (S.player && S.player.track) || {};
    const accent = (spec.values || {}).color === "cover" && /^#[0-9a-f]{6}$/i.test(tr.accent || "") ? tr.accent : getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#d97757";
    S.ring.dataset.style = (spec.values || {}).style || "bars";
    S.ring.style.setProperty("--qmod-c", accent);
    S.color = accent;
    S.seed = hash(S.player && S.player.track_id) || 1;
    S.bpm = +tr.bpm > 40 ? +tr.bpm : 96 + (S.seed % 40);
    host.classList.add("qmod-ringed");
    if (reduced() || S.ring.dataset.style === "glow") { haltRing(); S.ring.dataset.still = reduced() ? "1" : ""; drawRing(performance.now(), true); return; }
    if (!S.raf && !S.ringTimer) S.raf = requestAnimationFrame(frame);
  }

  function stopRing(fade) {
    const ring = S.ring;
    if (!ring) { haltRing(); return; }
    const gone = () => {
      haltRing(); S.fade = 0;
      if (S.ring === ring) { ring.remove(); S.ring = null; S.ctx = null; document.getElementById("qa-card")?.classList.remove("qmod-ringed"); }
    };
    if (!fade || reduced()) { clearTimeout(S.fade); gone(); return; }
    if (S.fade) return;
    ring.classList.add("out");
    S.fade = setTimeout(gone, 420);
  }

  function frame(now) {
    S.raf = 0;
    if (!S.ring || document.hidden || !S.shown || (!playing() && !S.fade)) return;
    drawRing(now, false);
    S.ringTimer = setTimeout(() => { S.ringTimer = 0; S.raf = requestAnimationFrame(frame); }, 50);
  }
  function haltRing() { cancelAnimationFrame(S.raf); clearTimeout(S.ringTimer); S.raf = 0; S.ringTimer = 0; }
  function drawRing(now, still) {
    const c = S.ring, ctx = S.ctx;
    if (!c || !ctx) return;
    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const w = c.clientWidth, h = c.clientHeight;
    if (!w || !h) return;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const style = c.dataset.style;
    const beatPhase = ((now / 60000) * S.bpm) % 1;
    const beat = still ? 0.5 : Math.pow(1 - beatPhase, 3);
    ctx.strokeStyle = S.color; ctx.fillStyle = S.color;
    if (style === "glow") {
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 2; ctx.shadowColor = S.color; ctx.shadowBlur = 14;
      roundRect(ctx, 2, 2, w - 4, h - 4, 18); ctx.stroke();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
      return;
    }

    const per = 2 * (w + h), step = 6, n = Math.floor(per / step), seed = S.seed;
    const tt = now / 1000;
    ctx.globalAlpha = 0.85;
    ctx.lineCap = "round";
    ctx.lineWidth = 2.5;
    if (style === "wave") ctx.beginPath();

    const LEVELS = 6, paths = style === "wave" ? null : Array.from({ length: LEVELS }, () => new Path2D());
    for (let i = 0; i < n; i += 1) {
      const u = i / n;

      const wave = Math.max(0, Math.sin(u * Math.PI * 2 * 2 - tt * 0.9 + (seed % 7))) ** 3 + Math.max(0, Math.sin(u * Math.PI * 2 * 3 + tt * 0.6)) ** 4 * 0.6;
      const band = (Math.sin(u * 61 + (seed % 97)) + Math.sin(u * 23.3 + tt * 2.4)) / 2;
      const lvl = Math.max(0, Math.min(1, wave * (0.55 + band * 0.25) + beat * 0.3 * wave));
      if (lvl < 0.04 && style !== "wave") continue;
      const len = 1.5 + lvl * 11;
      let d = u * per, x, y, nx, ny;
      if (d < w) { x = d; y = 1; nx = 0; ny = 1; } else if ((d -= w) < h) { x = w - 1; y = d; nx = -1; ny = 0; }
      else if ((d -= h) < w) { x = w - d; y = h - 1; nx = 0; ny = -1; } else { d -= w; x = 1; y = h - d; nx = 1; ny = 0; }
      if (style === "wave") { const px = x + nx * len, py = y + ny * len; i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); continue; }
      const path = paths[Math.min(LEVELS - 1, Math.floor(lvl * LEVELS))];
      path.moveTo(x + nx * 2, y + ny * 2); path.lineTo(x + nx * (2 + len), y + ny * (2 + len));
    }
    if (style === "wave") { ctx.closePath(); ctx.lineWidth = 2; ctx.globalAlpha = 0.8; ctx.stroke(); }
    else paths.forEach((path, k) => { ctx.globalAlpha = 0.25 + ((k + 0.5) / LEVELS) * 0.7; ctx.stroke(path); });
    ctx.globalAlpha = 1;
  }
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  Q.on("tool-ui", (e) => { if (e && e.ui && e.ui.type === "module-card") showCard(e.ui); });
  Q.on("chat-new", () => { if (cardPanel) cardPanel.hide(); });
  Q.on("open", () => { S.shown = true; if (S.ready) listen(); load().then(listen); });
  Q.on("close", () => { S.shown = false; S.touched = false; stopListen(); stopRing(false); clearTimeout(S.tick); });
  Q.on("submit", (e) => {

    const text = String((e && e.text) || "");
    if (e.images || e.selection) return;
    if (!S.ready) {

      e.preventDefault(); e.text = "";
      load().then(() => { const f = matchLocal(text); if (f && !f.danger) runPhrase(f); else Q.send(text); });
      return;
    }
    const found = matchLocal(text);
    if (!found || found.danger) return;
    e.preventDefault();

    e.text = "";
    runPhrase(found);
  });
  function runPhrase(found) {
    showCard({ type: "module-card", module: found.module, command: found.command, skeleton: true, ok: true });
    S.touched = true;
    run(found.module, found.command, found.args).then((ui) => { showCard(ui); listen(); });
  }
  document.addEventListener("visibilitychange", () => { if (document.hidden) { stopListen(); haltRing(); clearTimeout(S.tick); } else if (S.shown) { listen(); syncWidgets(); } });
  window.MemuQuickModules = { match: matchLocal, state: () => ({ modules: S.modules.length, phrases: S.phrases.length, ring: !!S.ring, raf: !!(S.raf || S.ringTimer), es: !!S.es, card: !!S.card,
    fading: !!S.fade, playing: playing(), position: pos(), expect: !!S.expect, ready: S.ready, trackId: (S.player && S.player.track_id) || "" }),
    showCard, setPlayer: (st) => { S.expect = null; S.player = { ...st, at: performance.now() }; if (st && st.track) S.meta.set(st.track_id, st.track); upNext(); syncWidgets(); }, control, load };
  if (document.visibilityState === "visible") { S.shown = true; load().then(listen); }
})();
