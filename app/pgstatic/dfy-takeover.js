

(function () {
  "use strict";
  if (window.MemuDFYTakeover) return;
  const en = () => (window.PG_LANG || (window.MemuQuick && window.MemuQuick.lang && window.MemuQuick.lang()) || document.documentElement.lang || "ru").toLowerCase().startsWith("en");
  const T = (ru, e) => (en() ? e : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = {
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    fwd: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>',
    reload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
  };
  const WHY = {
    captcha: ["ИИ просит пройти проверку «я не робот» и вернуть управление.", "The AI asks you to pass the “I'm not a robot” check and hand control back."],
    login: ["ИИ просит войти на сайт и вернуть управление.", "The AI asks you to sign in and hand control back."],
    "2fa": ["ИИ просит подтвердить вход (код, приложение) и вернуть управление.", "The AI asks you to confirm the sign-in (code, app) and hand control back."],
    verification: ["ИИ просит пройти проверку на сайте и вернуть управление.", "The AI asks you to pass the site's check and hand control back."],
  };

  const S = { id: "", ws: null, layer: null, pill: null, opts: {}, meta: { width: 0, height: 0 }, state: null, url: "",
    frameUrl: "", pressed: 0, lastMove: 0, moveQueued: null, logOpen: false, timer: 0, prevSize: null, question: null,
    minimized: false, watchStop: null, pillTimer: 0, since: 0 };
  const $ = (sel) => S.layer && S.layer.querySelector(sel);

  function css() {
    if (document.getElementById("dfyt-css")) return;
    const link = document.createElement("link");
    link.id = "dfyt-css"; link.rel = "stylesheet"; link.href = "/dfy-takeover.css?v=30";
    document.head.appendChild(link);
  }
  async function call(path, method) {
    const resp = await fetch(path, { method: method || "POST", headers: { "Content-Type": "application/json" }, body: method === "GET" ? undefined : "{}", cache: "no-store" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw Object.assign(new Error(data.error || "HTTP " + resp.status), { status: resp.status });
    return data;
  }
  const toast = (text, kind) => { try { (window.MemuQuick ? window.MemuQuick.toast : (window.toast || alert))(text, kind === "err" ? "error" : kind); } catch (e) {} };

  function build() {
    if (S.layer) return;
    css();
    const layer = document.createElement("div");
    layer.className = "dfyt-layer";
    layer.hidden = true;
    layer.setAttribute("role", "dialog");
    layer.setAttribute("aria-modal", "true");
    layer.innerHTML = `<div class="dfyt-win">
      <div class="dfyt-bar">
        <button type="button" class="dfyt-ico" data-act="back" title="${esc(T("Назад (Alt+←)", "Back (Alt+←)"))}" aria-label="${esc(T("Назад", "Back"))}">${ICON.back}</button>
        <button type="button" class="dfyt-ico dfyt-hide-narrow" data-act="forward" title="${esc(T("Вперёд (Alt+→)", "Forward (Alt+→)"))}" aria-label="${esc(T("Вперёд", "Forward"))}">${ICON.fwd}</button>
        <button type="button" class="dfyt-ico" data-act="reload" title="${esc(T("Обновить (F5)", "Reload (F5)"))}" aria-label="${esc(T("Обновить", "Reload"))}">${ICON.reload}</button>
        <input class="dfyt-addr" type="text" spellcheck="false" autocomplete="off" aria-label="${esc(T("Адрес страницы", "Page address"))}" title="${esc(T("Адрес (Ctrl+L)", "Address (Ctrl+L)"))}">
        <span class="dfyt-state" title="${esc(T("ИИ на паузе, пока вы управляете браузером", "The AI is paused while you control the browser"))}"><i></i>${esc(T("Управляете вы", "You're in control"))}</span>
        <button type="button" class="dfyt-btn dfyt-hide-narrow" data-act="log" aria-pressed="false" title="${esc(T("Что получит ИИ", "What the AI will get"))}">${esc(T("Журнал", "Log"))} <span class="n">0</span></button>
        <button type="button" class="dfyt-btn" data-act="minimize" title="${esc(T("Скрыть окно — управление останется у вас", "Hide the window — you keep control"))}">${esc(T("Свернуть", "Minimize"))}</button>
        <button type="button" class="dfyt-btn primary" data-act="giveback">${esc(T("Вернуть управление ИИ", "Return control to AI"))}</button>
      </div>
      <div class="dfyt-note" hidden></div>
      <div class="dfyt-body">
        <div class="dfyt-stage" tabindex="-1">
          <img class="dfyt-screen loading" alt="${esc(T("Браузер DFY", "DFY browser"))}" draggable="false">
          <textarea class="dfyt-sink" aria-label="${esc(T("Ввод в браузер DFY", "Typing into the DFY browser"))}" autocapitalize="off" autocomplete="off" spellcheck="false"></textarea>
          <div class="dfyt-wait"><div class="dfyt-spin"></div><span>${esc(T("Подключаюсь к браузеру…", "Connecting to the browser…"))}</span></div>
        </div>
        <aside class="dfyt-log" hidden>
          <h4>${esc(T("Что получит ИИ", "What the AI will get"))}</h4>
          <p>${esc(T("Ваши действия и несколько скриншотов. Пароли и коды скрыты (•••).", "Your actions and a few screenshots. Passwords and codes are hidden (•••)."))}</p>
          <ol></ol>
        </aside>
      </div></div>`;
    document.body.appendChild(layer);
    const pill = document.createElement("div");
    pill.className = "dfyt-pill"; pill.hidden = true;
    pill.innerHTML = `<span class="dfyt-state"><i></i>${esc(T("Управляете вы — ИИ на паузе", "You're in control — the AI is paused"))}</span>
      <button type="button" class="dfyt-btn" data-act="restore">${esc(T("Развернуть", "Open"))}</button>
      <button type="button" class="dfyt-btn primary" data-act="giveback">${esc(T("Вернуть управление ИИ", "Return control to AI"))}</button>`;
    document.body.appendChild(pill);
    S.layer = layer; S.pill = pill;
    layer.addEventListener("click", onButton);
    pill.addEventListener("click", onButton);
    bindStage();
  }

  function onButton(ev) {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "giveback") return giveBack();
    if (act === "minimize") return minimize();
    if (act === "restore") return open(S.id, S.opts);
    if (act === "back") return send({ t: "history", delta: -1 });
    if (act === "forward") return send({ t: "history", delta: 1 });
    if (act === "reload") return send({ t: "reload" });
    if (act === "ping") { send({ t: "ping" }); focusStage(); return; }
    if (act === "log") { S.logOpen = !S.logOpen; paintLog(); b.setAttribute("aria-pressed", String(S.logOpen)); fitSoon(); }
  }

  const send = (msg) => { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(msg)); };
  const mods = (e) => (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
  const BTN = ["left", "middle", "right"];
  function point(e) {
    const img = $(".dfyt-screen");
    const r = img.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / (r.width || 1))), y: Math.max(0, Math.min(1, (e.clientY - r.top) / (r.height || 1))) };
  }
  function focusStage() { const sink = $(".dfyt-sink"); if (sink) try { sink.focus({ preventScroll: true }); } catch (e) {} }

  function bindStage() {
    const img = $(".dfyt-screen"), sink = $(".dfyt-sink"), stage = $(".dfyt-stage"), addr = $(".dfyt-addr");
    img.addEventListener("mousedown", (e) => {
      e.preventDefault();
      focusStage();
      S.pressed |= 1 << e.button;
      send({ t: "down", ...point(e), button: BTN[e.button] || "left", buttons: e.buttons, clicks: e.detail || 1, mods: mods(e) });
    });
    window.addEventListener("mouseup", (e) => {
      if (!S.pressed || !layerOpen()) return;
      S.pressed &= ~(1 << e.button);
      send({ t: "up", ...point(e), button: BTN[e.button] || "left", buttons: e.buttons, clicks: e.detail || 1, mods: mods(e) });
    });
    const move = (e) => {
      if (!layerOpen()) return;
      if (!S.pressed && e.target !== img) return;
      S.moveQueued = { ...point(e), buttons: e.buttons, mods: mods(e) };
      const now = performance.now();
      if (now - S.lastMove < (S.pressed ? 16 : 50)) return;
      S.lastMove = now;
      send({ t: "move", ...S.moveQueued }); S.moveQueued = null;
    };
    window.addEventListener("mousemove", move);
    img.addEventListener("contextmenu", (e) => e.preventDefault());
    img.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
      send({ t: "wheel", ...point(e), dx: e.deltaX * k, dy: e.deltaY * k, mods: mods(e) });
    }, { passive: false });
    img.addEventListener("dragstart", (e) => e.preventDefault());

    sink.addEventListener("compositionend", (e) => { if (e.data) send({ t: "text", text: e.data }); sink.value = ""; });
    sink.addEventListener("input", (e) => { if (!e.isComposing && sink.value) { send({ t: "text", text: sink.value }); sink.value = ""; } });
    sink.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData)?.getData("text") || "";
      if (text) send({ t: "text", text, paste: true });
    });
    sink.addEventListener("focus", () => stage.classList.add("typing"));
    sink.addEventListener("blur", () => stage.classList.remove("typing"));
    addr.addEventListener("focus", () => addr.select());
    addr.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); const url = addr.value.trim(); if (url) send({ t: "nav", url }); focusStage(); }
      if (e.key === "Escape") { e.preventDefault(); addr.value = S.url; focusStage(); }
    });

    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", onKey, true);
    window.addEventListener("resize", fitSoon);
  }
  const layerOpen = () => !!(S.layer && !S.layer.hidden);

  function onKey(e) {
    if (!layerOpen()) return;
    const addr = $(".dfyt-addr");
    const target = e.target;
    if (target === addr) return;
    if (target && S.layer.contains(target) && target.closest(".dfyt-bar,.dfyt-note,.dfyt-log") && (e.key === "Enter" || e.key === " ")) return;
    e.stopPropagation();
    if (e.isComposing || e.key === "Process" || e.key === "Dead") return;
    const down = e.type === "keydown";
    const ctrl = e.ctrlKey || e.metaKey;
    if (down && ctrl && !e.altKey && (e.key === "v" || e.key === "V" || e.code === "KeyV")) { focusStage(); return; }
    e.preventDefault();
    if (down && ctrl && !e.altKey && (e.code === "KeyL")) { addr.focus(); return; }
    if (down && ctrl && !e.altKey && (e.code === "KeyC" || e.code === "KeyX") && !e.shiftKey) { send({ t: "copy" }); if (e.code === "KeyC") return; }
    if (down && (e.key === "F5" || (ctrl && e.code === "KeyR"))) { send({ t: "reload" }); return; }
    if (down && e.altKey && !ctrl && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { send({ t: "history", delta: e.key === "ArrowLeft" ? -1 : 1 }); return; }
    if (!down && (e.key === "F5" || (ctrl && (e.code === "KeyR" || e.code === "KeyL" || e.code === "KeyC")))) return;
    send({ t: "key", type: down ? "down" : "up", key: e.key, code: e.code, keyCode: e.keyCode || 0, mods: mods(e) });
  }

  let fitTimer = 0;
  function fitSoon() { clearTimeout(fitTimer); fitTimer = setTimeout(fit, 220); }
  function fit() {
    if (!layerOpen()) return;
    const stage = $(".dfyt-stage");
    const r = stage.getBoundingClientRect();
    if (r.width > 100 && r.height > 100) send({ t: "fit", w: Math.floor(r.width), h: Math.floor(r.height) });
  }
  function paintLog() {
    const log = $(".dfyt-log");
    if (!log) return;
    log.hidden = !S.logOpen;
    const lines = (S.state && S.state.journal) || [];
    $('[data-act="log"] .n').textContent = String((S.state && S.state.events) || 0);
    if (!S.logOpen) return;
    const ol = log.querySelector("ol");
    ol.innerHTML = lines.length ? lines.map((l) => `<li>${esc(l)}</li>`).join("") : `<li class="empty">${esc(T("Пока ничего — кликайте и печатайте в окне браузера.", "Nothing yet — click and type in the browser."))}</li>`;
    ol.scrollTop = ol.scrollHeight;
  }
  function paintNote() {
    const note = $(".dfyt-note");
    if (!note) return;
    const st = S.state || {};
    const left = st.deadline ? Math.round(st.deadline - Date.now() / 1000) : 1e9;
    if (left <= 60) {
      const m = Math.max(0, left);
      note.className = "dfyt-note warn"; note.hidden = false;
      note.innerHTML = `<span>${esc(T(`Нет действий — через ${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")} управление вернётся ИИ.`, `No activity — control returns to the AI in ${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}.`))}</span>
        <button type="button" class="dfyt-btn" data-act="ping">${esc(T("Я ещё здесь", "I'm still here"))}</button>`;
      return;
    }
    const why = WHY[st.reason] || (st.by === "agent" ? ["ИИ просит вас сделать шаг в браузере и вернуть управление.", "The AI asks you to do a step in the browser and hand control back."] : null);
    const ask = S.question && S.question.prompt ? S.question.prompt : "";
    note.className = "dfyt-note";
    if (why || ask) { note.hidden = false; note.innerHTML = `<span><b>${esc(T("ИИ:", "AI:"))}</b> ${esc(ask || (en() ? why[1] : why[0]))}</span>`; }
    else { note.hidden = true; note.innerHTML = ""; }
  }
  function onState(st) {
    S.state = st;
    if (st.active === false) { finished(st.end_reason); return; }
    if (st.url && document.activeElement !== $(".dfyt-addr")) { S.url = st.url; $(".dfyt-addr").value = st.url; }
    paintLog(); paintNote();
  }
  function finished(reason) {
    const id = S.id;
    close();
    if (!id) return;
    S.id = "";
    if (reason === "timeout") toast(T("Вы долго ничего не делали — управление вернулось ИИ", "You were idle — control went back to the AI"));
    else if (reason === "cancel") toast(T("Задача DFY остановлена", "The DFY task was stopped"));
    else if (reason !== "user" && reason !== "stale") toast(T("Управление вернулось ИИ", "Control went back to the AI"));
    document.dispatchEvent(new CustomEvent("dfy-takeover", { detail: { id, active: false } }));
  }

  const FINAL = ["done", "failed", "cancelled", "gone"];
  function sync(task) {
    if (!task || !S.id || String(task.id) !== S.id) return;
    if (FINAL.includes(task.status)) { finished(task.status === "cancelled" ? "cancel" : "user"); return; }

    if (task.control !== "user" && S.minimized && Date.now() - S.since > 1500) finished("ai");
  }
  function watchTask() {
    unwatchTask();
    const id = S.id;
    if (!id || !window.MemuDFY || !window.MemuDFY.watch) return;
    S.watchStop = window.MemuDFY.watch(id, (task) => sync(task && task.gone ? { id, status: "gone" } : task));
  }
  function unwatchTask() { if (S.watchStop) { try { S.watchStop(); } catch (e) {} S.watchStop = null; } }
  function hasCard(id) {
    try { return !!document.querySelector(`[data-dfy-card="${CSS.escape(id)}"]`); } catch (e) { return false; }
  }
  function placePill() {
    if (!S.pill) return;
    S.pill.hidden = !(S.minimized && S.id && !S.opts.compact && hasCard(S.id));
  }

  function connect() {
    disconnect();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/api/dfy/tasks/${encodeURIComponent(S.id)}/takeover/ws`);
    ws.binaryType = "blob";
    S.ws = ws;
    ws.onopen = () => { fitSoon(); };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") {
        const url = URL.createObjectURL(ev.data), img = $(".dfyt-screen");
        img.onload = () => { if (S.frameUrl && S.frameUrl !== url) URL.revokeObjectURL(S.frameUrl); S.frameUrl = url; img.classList.remove("loading"); $(".dfyt-wait").hidden = true; };
        img.src = url;
        return;
      }
      let msg = {};
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "meta") S.meta = msg;
      else if (msg.type === "state") onState(msg);
      else if (msg.type === "clipboard") copyText(msg.text || "");
      else if (msg.type === "error") toast(msg.error, "err");
    };
    ws.onclose = () => {
      if (S.ws !== ws) return;
      S.ws = null;
      if (layerOpen()) setTimeout(() => { if (layerOpen() && !S.ws) connect(); }, 1200);
    };
  }
  function disconnect() { const ws = S.ws; S.ws = null; if (ws) try { ws.close(); } catch (e) {} }
  function copyText(text) {
    if (!text) return;
    const fallback = () => { const t = document.createElement("textarea"); t.value = text; t.style.cssText = "position:fixed;opacity:0"; document.body.appendChild(t); t.select(); try { document.execCommand("copy"); } catch (e) {} t.remove(); focusStage(); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).catch(fallback); else fallback();
  }

  function growQuick(on) {
    if (!S.opts.compact || !window.MemuQuick || !window.MemuQuick.native) return;
    const post = (body) => fetch("/api/quick/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    if (on) { if (!S.prevSize) S.prevSize = { width: window.innerWidth }; post({ action: "resize", height: 2000, width: 2000, animate: false }); }
    else if (S.prevSize) { post({ action: "resize", height: 0, width: S.prevSize.width, animate: false }); S.prevSize = null; setTimeout(() => window.MemuQuick.resize(), 60); }
  }

  async function open(id, opts) {
    build();
    if (S.id && S.id !== id && layerOpen()) close();
    S.id = String(id); S.opts = opts || {}; S.question = S.opts.question || null;
    try { await call(`/api/dfy/tasks/${encodeURIComponent(S.id)}/takeover`); }
    catch (e) {
      toast(e.message, "err");

      if (e.status === 404 || e.status === 409) { if (S.minimized) finished("stale"); }
      return false;
    }
    unwatchTask(); S.minimized = false; clearInterval(S.pillTimer); S.since = Date.now();
    S.layer.classList.toggle("compact", !!S.opts.compact);
    S.layer.hidden = false; S.pill.hidden = true;
    $(".dfyt-wait").hidden = false; $(".dfyt-screen").classList.add("loading");
    S.logOpen = S.logOpen || window.innerWidth >= 1500;
    $('[data-act="log"]').setAttribute("aria-pressed", String(S.logOpen));
    paintLog(); paintNote();
    growQuick(true);
    connect();
    clearInterval(S.timer); S.timer = setInterval(paintNote, 1000);
    setTimeout(focusStage, 30);
    document.dispatchEvent(new CustomEvent("dfy-takeover", { detail: { id: S.id, active: true } }));
    return true;
  }
  function minimize() {
    if (!S.layer) return;
    disconnect();
    S.layer.hidden = true;
    S.minimized = true;
    placePill();
    clearInterval(S.timer);
    clearInterval(S.pillTimer); S.pillTimer = setInterval(placePill, 700);
    watchTask();
    growQuick(false);
  }
  function close() {
    disconnect();
    unwatchTask();
    clearInterval(S.timer); clearInterval(S.pillTimer);
    S.minimized = false;
    if (S.layer) S.layer.hidden = true;
    if (S.pill) S.pill.hidden = true;
    growQuick(false);
  }
  async function giveBack(id) {
    const task = id || S.id;
    if (!task) return;
    const buttons = document.querySelectorAll('.dfyt-layer [data-act="giveback"], .dfyt-pill [data-act="giveback"]');
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const reply = await call(`/api/dfy/tasks/${encodeURIComponent(task)}/takeover/end`);
      if (task === S.id) { close(); S.id = ""; }
      const over = reply && ["done", "failed", "cancelled"].includes(reply.status);
      toast(over ? T("Задача DFY уже завершена", "The DFY task has already finished")
        : T("Управление у ИИ — он продолжит с этой страницы", "The AI is back in control and continues from this page"));
      document.dispatchEvent(new CustomEvent("dfy-takeover", { detail: { id: task, active: false } }));
    } catch (e) {
      toast(e.message, "err");
      if (e.status === 404 && task === S.id) { close(); S.id = ""; }
    }
    finally { buttons.forEach((b) => { b.disabled = false; }); }
  }

  window.MemuDFYTakeover = { open, giveBack, minimize, close, sync, isOpen: layerOpen, get taskId() { return S.id; },
    get minimized() { return S.minimized; } };
})();
