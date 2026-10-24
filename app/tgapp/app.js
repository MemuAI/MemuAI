/* MemuAI — Telegram Mini App.
 *
 * Tabs: Chats · Agents (мемесы) · MemuCode · Modules · Settings.
 * Talks only to /tgapp/api/* of the computer (see app/tgapp_api.py) with a
 * session token obtained for Telegram initData. Long answers are server-side
 * jobs read in short streamed POST segments (Cloudflare quick tunnels buffer
 * GET streams) with polling as the fallback.
 */
(function () {
  "use strict";

  /* ================================================================ basics */
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) => window.MemuMD.esc(value);
  const md = (text) => window.MemuMD.render(text, { copyLabel: tr("Копировать", "Copy") });
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

  const S = {
    tg: null, official: false, lang: "ru", token: "", tokenExp: 0, initData: "", user: null,
    boot: null, settings: {}, models: [], memes: [], folders: [], profiles: [], profileId: "",
    tab: "chats", stack: [], chats: [], chatsRev: "", chatFilter: "all", chatQuery: "", chatLimit: 60,
    ui: { look: "memuai", scheme: "telegram" }, appearance: null, themes: null, features: {},
    codeSessions: [], modules: [], timers: {}, memory: { enabled: true, facts: [] },
  };

  function tr(ru, en) { return S.lang === "en" ? en : ru; }

  /* ================================================================ icons */
  const P = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  const ICONS = {
    chat: P('<path d="M21 12a8.5 8.5 0 0 1-12.4 7.5L3 21l1.6-5.2A8.5 8.5 0 1 1 21 12z"/>'),
    chatFill: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 3a9 9 0 0 0-7.9 13.3L3 21l4.8-1.2A9 9 0 1 0 12 3z"/></svg>',
    agents: P('<path d="M12 3l1.9 4.6L19 8.3l-3.8 3.4 1.1 5L12 14.2 7.7 16.7l1.1-5L5 8.3l5.1-.7z"/><path d="M5 21h14"/>'),
    code: P('<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 4l-4 16"/>'),
    terminal: P('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9l3 3-3 3M12 15h5"/>'),
    modules: P('<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>'),
    settings: P('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    plus: P('<path d="M12 5v14M5 12h14"/>'),
    compose: P('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>'),
    search: P('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
    send: P('<path d="M12 19V5M5 12l7-7 7 7"/>', 'stroke-width="2.4"'),
    stop: '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2.5" fill="currentColor"/></svg>',
    attach: P('<path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>'),
    globe: P('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18"/>'),
    brain: P('<path d="M9.5 3A3.5 3.5 0 0 0 6 6.5v.2A3.5 3.5 0 0 0 4 13a3.5 3.5 0 0 0 3 5.4A3 3 0 0 0 12 20V4.5A2.5 2.5 0 0 0 9.5 3zM14.5 3A3.5 3.5 0 0 1 18 6.5v.2a3.5 3.5 0 0 1 2 6.3 3.5 3.5 0 0 1-3 5.4A3 3 0 0 1 12 20"/>'),
    copy: P('<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>'),
    retry: P('<path d="M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5"/>'),
    more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.9" fill="currentColor"/><circle cx="12" cy="12" r="1.9" fill="currentColor"/><circle cx="19" cy="12" r="1.9" fill="currentColor"/></svg>',
    back: P('<path d="M15 18l-6-6 6-6"/>', 'stroke-width="2.4"'),
    chev: P('<path d="M9 6l6 6-6 6"/>', 'stroke-width="2.4"'),
    close: P('<path d="M18 6L6 18M6 6l12 12"/>', 'stroke-width="2.4"'),
    check: P('<path d="M20 6L9 17l-5-5"/>', 'stroke-width="2.6"'),
    pin: P('<path d="M12 17v5M9 3h6l-1 6 4 3v2H6v-2l4-3z"/>'),
    folder: P('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    edit: P('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4z"/>'),
    trash: P('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'),
    unread: P('<circle cx="12" cy="12" r="4" fill="currentColor"/>'),
    temp: P('<path d="M12 7v5l3 2"/><circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/>'),
    sparkle: P('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
    bot: P('<rect x="4" y="8" width="16" height="12" rx="4"/><path d="M12 4v4M9 14h.01M15 14h.01"/>'),
    user: P('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
    palette: P('<path d="M12 3a9 9 0 1 0 0 18c1 0 1.5-.7 1.5-1.5 0-.5-.2-.8-.5-1.2-.3-.3-.5-.7-.5-1.2 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4-4-7.6-9-7.6z"/><circle cx="7.5" cy="11" r="1.2" fill="currentColor"/><circle cx="10.5" cy="7" r="1.2" fill="currentColor"/><circle cx="15" cy="7.5" r="1.2" fill="currentColor"/>'),
    model: P('<path d="M12 2l8.5 5v10L12 22l-8.5-5V7z"/><path d="M12 22V12M20.5 7L12 12 3.5 7"/>'),
    memory: P('<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>'),
    bell: P('<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0"/>'),
    telegram: P('<path d="M21.5 4.5L2.5 11.6l6.2 2.1 2.2 6.6 3.5-4.2 5 3.6z"/><path d="M8.7 13.7l9.5-6.3-6.6 7.7"/>'),
    info: P('<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>'),
    warn: P('<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4M12 17.5h.01"/>'),
    shield: P('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>'),
    lock: P('<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>'),
    image: P('<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.8"/><path d="M21 15l-5-5L5 21"/>'),
    file: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
    play: P('<path d="M7 4v16l13-8z"/>'),
    esc: P('<rect x="3" y="6" width="18" height="12" rx="3"/><path d="M7.5 12h9"/>'),
    mic: P('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
    search2: P('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
    zap: P('<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
    list: P('<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'),
    clock: P('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    text: P('<path d="M4 7V5h16v2M9 19h6M12 5v14"/>'),
    work: P('<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'),
    monitor: P('<rect x="3" y="4" width="18" height="12" rx="2.5"/><path d="M8 20h8M12 16v4"/>'),
    target: P('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1" fill="currentColor"/>'),
    headphones: P('<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1v-6h3zM3 19a2 2 0 0 0 2 2h1v-6H3z"/>'),
    download: P('<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>'),
    circle: P('<circle cx="12" cy="12" r="8"/>'),
    circleCheck: P('<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>'),
    dots: P('<circle cx="12" cy="12" r="8" stroke-dasharray="2 3"/>'),
  };
  const icon = (name) => ICONS[name] || ICONS.circle;
  const MODULE_ICON = { text: "text", chat: "chat", sparkle: "sparkle", zap: "zap", image: "image", work: "work", bot: "bot",
    settings: "settings", palette: "palette", search: "search", globe: "globe", list: "list", clock: "clock", bell: "bell",
    user: "user", monitor: "monitor", terminal: "terminal", target: "target", mic: "mic", headphones: "headphones",
    download: "download", folder: "folder", info: "info", play: "play" };

  /* ================================================================ Telegram helpers */
  const tg = () => S.tg;
  function haptic(kind = "light") {
    try {
      const h = S.tg && S.tg.HapticFeedback;
      if (!h || !S.tg.isVersionAtLeast || !S.tg.isVersionAtLeast("6.1")) return;
      if (kind === "select") h.selectionChanged();
      else if (["success", "error", "warning"].includes(kind)) h.notificationOccurred(kind);
      else h.impactOccurred(kind);
    } catch (e) { /* not in Telegram */ }
  }
  function openLink(url) {
    try { if (S.tg && S.tg.openLink) { S.tg.openLink(url); return; } } catch (e) { /* fall through */ }
    window.open(url, "_blank", "noopener");
  }
  function confirmDialog(message, { title = "", ok = tr("Да", "Yes"), danger = false } = {}) {
    return new Promise((resolve) => {
      const app = S.tg;
      if (app && app.initData && app.isVersionAtLeast && app.isVersionAtLeast("6.2")) {
        try {
          app.showPopup({ title: title.slice(0, 64), message: String(message).slice(0, 256),
            buttons: [{ id: "ok", type: danger ? "destructive" : "default", text: ok.slice(0, 64) }, { type: "cancel" }] },
          (id) => resolve(id === "ok"));
          return;
        } catch (e) { /* fall back to the in-app sheet */ }
      }
      const sheet = openSheet({
        title: title || tr("Подтверждение", "Confirm"),
        body: `<p class="section-note" style="margin:0 20px 8px;font-size:15px;color:var(--text)">${esc(message)}</p>`,
        actions: [{ label: tr("Отмена", "Cancel"), kind: "secondary", run: () => resolve(false) },
                  { label: ok, kind: danger ? "danger" : "", run: () => resolve(true) }],
        onClose: () => resolve(false),
      });
      return sheet;
    });
  }

  /* ================================================================ toast */
  function toast(text, kind = "") {
    const root = $("#toast-root");
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.innerHTML = (kind === "err" ? icon("warn") : kind === "ok" ? icon("check") : icon("info")) + `<span>${esc(text)}</span>`;
    root.appendChild(el);
    if (kind === "err") haptic("error"); else if (kind === "ok") haptic("success");
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 260); }, kind === "err" ? 4200 : 2400);
  }

  /* ================================================================ API */
  class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code; } }

  async function authenticate() {
    if (S.web) {
      // Round 29: a browser session has no Telegram data to sign in again with — back to the sign-in screen.
      if (window.MemuTgWeb) window.MemuTgWeb.expired();
      throw new ApiError(tr("Сессия в браузере закончилась — войдите снова", "The browser session has ended — sign in again"), 401, "unauthorized");
    }
    const response = await fetch("api/auth", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: S.initData }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.error || ("HTTP " + response.status), response.status, data.code || "");
    S.token = data.token; S.tokenExp = data.expires; S.user = data.user; S.lang = data.lang || S.lang;
    return data;
  }

  async function api(name, body = {}, { raw = false, signal, retry = true } = {}) {
    if (!S.token || Date.now() / 1000 > S.tokenExp - 60) await authenticate();
    const payload = Object.assign({}, body);
    if (S.profileId && payload.profile_id === undefined) payload.profile_id = S.profileId;
    let response;
    try {
      response = await fetch("api/" + name, { method: "POST", signal,
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + S.token }, body: JSON.stringify(payload) });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError(tr("Нет связи с компьютером", "No connection to the computer"), 0, "offline");
    }
    if (response.status === 401 && retry && !S.web) { S.token = ""; return api(name, body, { raw, signal, retry: false }); }
    if (response.status === 401 && S.web && window.MemuTgWeb) window.MemuTgWeb.expired();
    if (raw && response.ok) return response;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.error || ("HTTP " + response.status), response.status, data.code || "");
    return data;
  }

  /* Read a server-side job: streamed NDJSON segments, polling as the fallback. */
  // A chat answer may finish before its title: «done» says title_pending and the job ends with «settled».
  function jobFinished(event) { return (event.type === "done" && !event.title_pending) || event.type === "settled"; }
  async function followJob(job, from, onEvent, control = {}) {
    let offset = from || 0;
    let streamFailures = 0;
    while (!control.cancelled) {
      let finished = false;
      if (streamFailures < 2) {
        try {
          const response = await api("jobs.stream", { job, from: offset }, { raw: true, signal: control.abort && control.abort.signal });
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let gotData = false;
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let cut;
            while ((cut = buffer.indexOf("\n")) >= 0) {
              const line = buffer.slice(0, cut); buffer = buffer.slice(cut + 1);
              if (!line.trim()) continue;
              let event; try { event = JSON.parse(line); } catch (e) { continue; }
              if (event.type === "hello" || event.type === "ping") { gotData = true; continue; }
              if (typeof event.seq === "number") offset = event.seq + 1;
              gotData = true;
              onEvent(event);
              if (jobFinished(event)) finished = true;
            }
            if (control.cancelled) { try { reader.cancel(); } catch (e) { /* closed */ } return; }
          }
          if (!gotData) streamFailures++;
        } catch (error) {
          if (control.cancelled || (error && error.name === "AbortError")) return;
          if (error.code === "no_job" || error.status === 404) { onEvent({ type: "lost" }); return; }
          streamFailures++;
          await sleep(600);
        }
      } else {
        try {
          const data = await api("jobs.poll", { job, from: offset, wait: 8 });
          for (const event of data.events || []) { offset = event.seq + 1; onEvent(event); if (jobFinished(event)) finished = true; }
          if (data.done && !(data.events || []).length) finished = true;
        } catch (error) {
          if (error.status === 404) { onEvent({ type: "lost" }); return; }
          await sleep(1500);
        }
      }
      if (finished) return;
    }
  }

  /* ================================================================ appearance */
  function hexOk(value) { return /^#[0-9a-f]{3,8}$/i.test(String(value || "").trim()); }
  function luminance(hex) {
    const m = /^#?([0-9a-f]{6})/i.exec(String(hex || "").trim());
    if (!m) return 0.1;
    const n = parseInt(m[1], 16);
    return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
  }
  function currentScheme() {
    if (S.ui.scheme === "app") return (S.appearance && S.appearance.theme) || "dark";
    const app = S.tg;
    if (app && app.colorScheme) return app.colorScheme;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function applyAppearance() {
    const root = document.documentElement;
    const look = S.ui.look === "telegram" ? "telegram" : "memuai";
    const scheme = currentScheme();
    const appearance = S.appearance || { tokens: {}, accent: "terracotta", design: "classic", font: "serif", font_scale: 100 };
    const tokens = (appearance.tokens || {})[scheme] || {};
    const theme = (S.tg && S.tg.themeParams) || {};
    const v = {};
    const accentCatalog = ((S.themes && S.themes.accents) || []).find((row) => row.id === appearance.accent);
    const accent = (accentCatalog && accentCatalog[scheme]) || tokens["--accent"] || "#efa58b";
    if (look === "telegram" && hexOk(theme.bg_color)) {
      v.bg = theme.bg_color; v.bg2 = theme.secondary_bg_color || theme.bg_color; v.card = theme.section_bg_color || theme.bg_color;
      v.text = theme.text_color || "#000"; v.hint = theme.hint_color || theme.subtitle_text_color || "#999";
      v.accent = theme.button_color || theme.link_color || accent; v.onAccent = theme.button_text_color || "#fff";
      v.accent2 = theme.accent_text_color || theme.link_color || v.accent; v.danger = theme.destructive_text_color || "#e5484d";
      v.sep = theme.section_separator_color || "";
    } else {
      v.bg = tokens["--bg"] || (scheme === "light" ? "#faf9f7" : "#1a1a1c");
      v.bg2 = tokens["--bg-deep"] || (scheme === "light" ? "#f1efea" : "#111113");
      v.card = tokens["--panel"] || (scheme === "light" ? "#ffffff" : "#222225");
      v.card2 = tokens["--panel-2"] || "";
      v.text = tokens["--text"] || (scheme === "light" ? "#1f1e1c" : "#ededf0");
      v.hint = tokens["--muted"] || "#9a9aa5";
      v.accent = accent; v.onAccent = tokens["--on-accent"] || (luminance(accent) > 0.55 ? "#141416" : "#ffffff");
      v.accent2 = (accentCatalog && accentCatalog[scheme + "2"]) || tokens["--accent-2"] || accent;
      v.danger = tokens["--danger"] || "#e5484d"; v.ok = tokens["--ok"] || "";
      v.sep = tokens["--border"] ? `color-mix(in srgb, ${tokens["--border"]} 70%, transparent)` : "";
      v.codeBg = tokens["--code-bg"] || ""; v.codeText = tokens["--code-text"] || "";
      if (scheme === "light" && v.bg2 === v.bg) v.bg2 = "#f2f1ee";
    }
    if (luminance(v.onAccent) > 0.5 === luminance(v.accent) > 0.5) v.onAccent = luminance(v.accent) > 0.55 ? "#141416" : "#ffffff";
    const set = (name, value) => { if (value) root.style.setProperty(name, value); else root.style.removeProperty(name); };
    set("--bg", v.bg); set("--bg2", v.bg2); set("--card", v.card); set("--text", v.text); set("--hint", v.hint);
    set("--accent", v.accent); set("--accent2", v.accent2); set("--on-accent", v.onAccent); set("--danger", v.danger);
    set("--card2", v.card2 || `color-mix(in srgb, ${v.card} 88%, ${scheme === "light" ? "#000" : "#fff"})`);
    set("--sep", v.sep || (scheme === "light" ? "rgba(0,0,0,.09)" : "rgba(255,255,255,.08)"));
    set("--on-danger", luminance(v.danger) > 0.5 ? "#1c0f0f" : "#ffffff");
    set("--ok", v.ok || (scheme === "light" ? "#1f9d55" : "#7fd49a"));
    set("--warn", scheme === "light" ? "#b7791f" : "#f0c56b");
    set("--code-bg", v.codeBg || (scheme === "light" ? "#f4f3f0" : "#121315"));
    set("--code-text", v.codeText || (scheme === "light" ? "#26241f" : "#ebe9e4"));
    root.dataset.scheme = scheme;
    root.dataset.look = look;
    root.dataset.design = appearance.design || "classic";
    root.dataset.font = appearance.font || "serif";
    root.dataset.compact = appearance.compact ? "1" : "0";
    root.dataset.motion = appearance.reduce_motion ? "reduce" : "";
    root.style.setProperty("--scale", String(clamp((+appearance.font_scale || 100) / 100, 0.85, 1.25)));
    root.lang = S.lang;
    try {
      const app = S.tg;
      if (app && app.setHeaderColor && app.isVersionAtLeast && app.isVersionAtLeast("6.1")) {
        if (app.isVersionAtLeast("6.9")) { app.setHeaderColor(v.bg2); app.setBackgroundColor(v.bg2); }
        else { app.setHeaderColor("secondary_bg_color"); }
        if (app.setBottomBarColor && app.isVersionAtLeast("7.10")) app.setBottomBarColor(v.bg);
      }
    } catch (e) { /* older client */ }
  }
  function applySafeArea() {
    const app = S.tg;
    if (!app) return;
    const root = document.documentElement;
    const safe = app.safeAreaInset || {}, content = app.contentSafeAreaInset || {};
    root.style.setProperty("--tg-safe-top", (safe.top || 0) + "px");
    root.style.setProperty("--tg-safe-bottom", (safe.bottom || 0) + "px");
    root.style.setProperty("--tg-content-top", (app.isFullscreen ? (content.top || 0) : 0) + "px");
  }

  /* UI-only preferences: Telegram CloudStorage when present, else localStorage. */
  const UI_KEY = "memu_ui_v1";
  function loadUiPrefs() {
    return new Promise((resolve) => {
      const local = () => { try { return JSON.parse(localStorage.getItem(UI_KEY) || "{}") || {}; } catch (e) { return {}; } };
      const cloud = S.tg && S.tg.CloudStorage;
      if (cloud && S.tg.isVersionAtLeast && S.tg.isVersionAtLeast("6.9") && S.official) {
        let settled = false;
        const finish = (value) => { if (!settled) { settled = true; resolve(value); } };
        try { cloud.getItem(UI_KEY, (error, value) => { try { finish(error ? local() : (JSON.parse(value || "{}") || {})); } catch (e) { finish(local()); } }); }
        catch (e) { finish(local()); }
        setTimeout(() => finish(local()), 1200);
      } else resolve(local());
    });
  }
  function saveUiPrefs() {
    const text = JSON.stringify(S.ui);
    try { localStorage.setItem(UI_KEY, text); } catch (e) { /* private */ }
    try { if (S.official && S.tg && S.tg.CloudStorage && S.tg.isVersionAtLeast("6.9")) S.tg.CloudStorage.setItem(UI_KEY, text); } catch (e) { /* ignore */ }
  }

  /* ================================================================ time & text */
  function ago(ms) {
    const value = +ms || 0;
    if (!value) return "";
    const date = new Date(value > 1e12 ? value : value * 1000);
    const now = new Date();
    const diff = (now - date) / 1000;
    if (diff < 60) return tr("сейчас", "now");
    if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString(S.lang === "en" ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" });
    const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString()) return tr("вчера", "yesterday");
    if (diff < 6 * 86400) return date.toLocaleDateString(S.lang === "en" ? "en-GB" : "ru-RU", { weekday: "short" });
    return date.toLocaleDateString(S.lang === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "short" });
  }
  function duration(seconds) {
    const total = Math.max(0, Math.round(+seconds || 0)), m = Math.floor(total / 60), sec = total % 60;
    if (m >= 60) return `${Math.floor(m / 60)} ${tr("ч", "h")} ${m % 60} ${tr("мин", "min")}`;
    return m ? `${m} ${tr("мин", "min")} ${sec} ${tr("с", "s")}` : `${sec} ${tr("с", "s")}`;
  }
  function modelName(id) {
    const row = S.models.find((m) => m.id === id);
    let name = (row && row.name) || id || "";
    if (name === id) {
      // URI-style ids ("ydx-gpt://folder/deepseek-v4.1-flash/latest") show the model, like the desktop's shortModelId().
      const uri = String(id).match(/^[a-z][a-z-]*:\/\/[^/]+\/([^/]+)(?:\/[^/]*)?$/i);
      if (uri) return uri[1];
      // Account-prefixed ids ("genspark-claude-…") read better without the prefix — but "grok-4" must stay "grok-4".
      const bare = String(id).replace(/^(genspark|kiro|antigravity|geminiweb|grok|custom|local)[-/]/i, "");
      if (/^[a-z]/i.test(bare) && bare.length > 2) name = bare;
    }
    return name;
  }
  function initialsGradient(seed) {
    const pairs = [["#7c5cd6", "#ab8eed"], ["#cc785c", "#e89a7e"], ["#2f8f83", "#6fd1c2"], ["#3f6fd8", "#82aaff"], ["#c2457a", "#f08bb5"], ["#b7791f", "#f0c56b"], ["#5b6b84", "#a2b1c9"]];
    let hash = 7; const s = String(seed || "x");
    for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) % 99991;
    return pairs[hash % pairs.length];
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); }
    catch (e) {
      const area = document.createElement("textarea"); area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
      document.body.appendChild(area); area.select(); try { document.execCommand("copy"); } catch (err) { /* ignore */ } area.remove();
    }
    haptic("success");
  }

  /* ================================================================ meme avatars (same art as the desktop) */
  const MEME_KINDS = ["orbit", "bloom", "waves", "prism", "spark", "grid", "moon", "leaf", "bolt", "drops", "rings", "cross"];
  const MEME_PALETTES = [["#f97316", "#facc15"], ["#22d3ee", "#3b82f6"], ["#a855f7", "#ec4899"], ["#10b981", "#84cc16"], ["#ef4444", "#fb923c"], ["#6366f1", "#22d3ee"],
    ["#f43f5e", "#a855f7"], ["#0ea5e9", "#14b8a6"], ["#eab308", "#84cc16"], ["#8b5cf6", "#3b82f6"], ["#fb7185", "#fdba74"], ["#2dd4bf", "#a3e635"]];
  const MEME_CAPS = [["planning", "Планирование и расписания", "Planning and schedules"], ["telegram", "Сообщения в Telegram", "Telegram messages"],
    ["tools", "Локальные инструменты", "Local tools"], ["integrations", "Интеграции", "Integrations"], ["files", "Файлы и изображения", "Files and images"],
    ["memory", "Долгая память", "Long-term memory"], ["skills", "Скиллы ИИ", "AI skills"]];
  const EMOJI = ["✨", "🧠", "🦊", "🐱", "🤖", "📚", "🧪", "🛠", "🌿", "🔥", "🌙", "⭐", "🎯", "📝", "💡", "🎵", "🧩", "🚀"];
  function memeAvatarConfig(meme) {
    const raw = meme && meme.avatar;
    if (raw && typeof raw === "object" && ["none", "emoji", "art"].includes(raw.type)) return raw;
    if (raw && typeof raw === "object" && MEME_KINDS.includes(raw.kind)) return Object.assign({ type: "art" }, raw);
    const key = String((meme && (meme.id || meme.name)) || "meme");
    let hash = 7;
    for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) % 99991;
    return { type: "art", kind: MEME_KINDS[hash % MEME_KINDS.length], palette: hash % MEME_PALETTES.length, seed: 1 + (hash % 9999) };
  }
  function memeAvatar(meme, cls = "") {
    const cfg = memeAvatarConfig(meme);
    if (cfg.type === "none") {
      const [a, b] = initialsGradient(meme && meme.name);
      return `<div class="avatar ${cls}" style="--av1:${a};--av2:${b}">${esc(String((meme && meme.name) || "M").trim().charAt(0).toUpperCase() || "M")}</div>`;
    }
    if (cfg.type === "emoji" && cfg.emoji) return `<div class="avatar ${cls}" style="--av1:var(--card2);--av2:var(--card2)"><span class="emoji">${esc(cfg.emoji)}</span></div>`;
    const pair = MEME_PALETTES[Math.abs(cfg.palette || 0) % MEME_PALETTES.length];
    const seed = Math.abs(cfg.seed || 1) || 1, angle = seed % 180, gid = "ma" + Math.abs(cfg.palette || 0) + cfg.kind + seed;
    const shapes = {
      orbit: `<circle cx="16" cy="16" r="5.4" fill="#fff" opacity=".95"/><ellipse cx="16" cy="16" rx="12.5" ry="5" fill="none" stroke="#fff" stroke-width="2" opacity=".7" transform="rotate(${angle} 16 16)"/>`,
      bloom: `<g fill="#fff" opacity=".9"><circle cx="16" cy="8.5" r="4.2"/><circle cx="23.5" cy="14" r="4.2"/><circle cx="20.6" cy="23" r="4.2"/><circle cx="11.4" cy="23" r="4.2"/><circle cx="8.5" cy="14" r="4.2"/></g><circle cx="16" cy="16" r="3.2" fill="#fff" opacity=".55"/>`,
      waves: `<g fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".9"><path d="M4 12c3-3 5-3 8 0s5 3 8 0 5-3 8 0"/><path d="M4 20c3-3 5-3 8 0s5 3 8 0 5-3 8 0" opacity=".65"/></g>`,
      prism: `<path d="M16 5l11 19H5z" fill="#fff" opacity=".92"/><path d="M16 12l6 12H10z" fill="#fff" opacity=".45"/>`,
      spark: `<path d="M16 3l3.3 9.2L28 16l-8.7 3.8L16 29l-3.3-9.2L4 16l8.7-3.8z" fill="#fff" opacity=".92" transform="rotate(${angle % 45} 16 16)"/>`,
      grid: `<g fill="#fff" opacity=".9"><circle cx="10" cy="10" r="2.6"/><circle cx="16" cy="10" r="2.6" opacity=".55"/><circle cx="22" cy="10" r="2.6"/><circle cx="10" cy="16" r="2.6" opacity=".55"/><circle cx="16" cy="16" r="2.6"/><circle cx="22" cy="16" r="2.6" opacity=".55"/><circle cx="10" cy="22" r="2.6"/><circle cx="16" cy="22" r="2.6" opacity=".55"/><circle cx="22" cy="22" r="2.6"/></g>`,
      moon: `<path d="M20.5 5a11 11 0 100 22 13 13 0 010-22z" fill="#fff" opacity=".92"/><circle cx="24" cy="9" r="1.8" fill="#fff" opacity=".7"/>`,
      leaf: `<path d="M25 6c-11 0-19 6-19 14 0 3 2 6 2 6s3-13 17-16c-6 4-9 8-10 14 8 1 14-6 14-13 0-2 0-4-4-5z" fill="#fff" opacity=".92"/>`,
      bolt: `<path d="M18 3l-9 15h6l-3 11 11-16h-6z" fill="#fff" opacity=".95"/>`,
      drops: `<g fill="#fff"><circle cx="12" cy="12" r="6" opacity=".92"/><circle cx="22" cy="18" r="4.4" opacity=".7"/><circle cx="14" cy="23" r="3" opacity=".55"/></g>`,
      rings: `<g fill="none" stroke="#fff"><circle cx="16" cy="16" r="11" stroke-width="2" opacity=".45"/><circle cx="16" cy="16" r="7" stroke-width="2.4" opacity=".75"/><circle cx="16" cy="16" r="2.6" stroke-width="2.6"/></g>`,
      cross: `<g fill="#fff" opacity=".92"><rect x="13.4" y="5" width="5.2" height="22" rx="2.6"/><rect x="5" y="13.4" width="22" height="5.2" rx="2.6" opacity=".7"/></g>`,
    };
    return `<div class="avatar ${cls}" style="--av1:${pair[0]};--av2:${pair[1]}"><svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${pair[0]}"/><stop offset="1" stop-color="${pair[1]}"/></linearGradient></defs><rect width="32" height="32" fill="url(#${gid})"/>${shapes[cfg.kind] || shapes.orbit}</svg></div>`;
  }
  function memeGlow(meme) { const cfg = memeAvatarConfig(meme); return cfg.type === "art" ? MEME_PALETTES[Math.abs(cfg.palette || 0) % MEME_PALETTES.length][0] : "var(--accent)"; }
  const memeById = (id) => S.memes.find((m) => m.id === id) || null;

  /* ================================================================ sheets */
  function openSheet({ title = "", body = "", actions = [], full = false, onClose = null, onMount = null, headerAction = "" } = {}) {
    const root = $("#sheet-root");
    const backdrop = document.createElement("div");
    backdrop.className = "sheet-backdrop";
    const sheet = document.createElement("div");
    sheet.className = "sheet" + (full ? " full" : "");
    sheet.setAttribute("role", "dialog");
    sheet.innerHTML = `<div class="sheet-grab"></div><div class="sheet-head"><h3>${esc(title)}</h3>${headerAction}<button class="icon-btn muted" data-close aria-label="${esc(tr("Закрыть", "Close"))}">${icon("close")}</button></div>
      <div class="sheet-body">${body}</div>${actions.length ? `<div class="sheet-actions">${actions.map((a, i) => `<button class="btn ${a.kind || ""}" data-act="${i}">${esc(a.label)}</button>`).join("")}</div>` : ""}`;
    root.append(backdrop, sheet);
    let closed = false;
    const close = (viaAction) => {
      if (closed) return; closed = true;
      backdrop.classList.remove("open"); sheet.classList.remove("open");
      popBack(handle);
      setTimeout(() => { backdrop.remove(); sheet.remove(); }, 320);
      if (!viaAction && onClose) onClose();
    };
    const handle = { el: sheet, close, back: () => close(false) };
    backdrop.onclick = () => close(false);
    $("[data-close]", sheet).onclick = () => close(false);
    $$("[data-act]", sheet).forEach((button) => button.onclick = async () => {
      const action = actions[+button.dataset.act];
      haptic("light");
      if (action.run) {
        const keep = await action.run(handle);
        if (keep === true) return;
      }
      close(true);
    });
    // Swipe the grab handle down to close.
    let startY = null;
    $(".sheet-grab", sheet).addEventListener("touchstart", (e) => { startY = e.touches[0].clientY; }, { passive: true });
    sheet.addEventListener("touchmove", (e) => { if (startY != null) { const dy = e.touches[0].clientY - startY; if (dy > 0) sheet.style.transform = `translateY(${dy}px)`; } }, { passive: true });
    sheet.addEventListener("touchend", (e) => {
      if (startY == null) return;
      const dy = (e.changedTouches[0] || {}).clientY - startY; startY = null; sheet.style.transform = "";
      if (dy > 90) close(false);
    });
    pushBack(handle);
    requestAnimationFrame(() => { backdrop.classList.add("open"); sheet.classList.add("open"); });
    if (onMount) onMount(sheet, handle);
    return handle;
  }
  function actionSheet(title, items) {
    const body = `<div class="group action-list">${items.map((item, i) => item.sep ? "" :
      `<button class="cell ${item.danger ? "danger" : ""}" data-item="${i}"><div class="cell-icon">${icon(item.icon || "chev")}</div><div class="cell-body"><div class="cell-title">${esc(item.label)}</div>${item.sub ? `<div class="cell-sub">${esc(item.sub)}</div>` : ""}</div>${item.checked ? `<span class="check">${icon("check")}</span>` : ""}</button>`).join("")}</div>`;
    const sheet = openSheet({ title, body, onMount: (el, handle) => {
      $$("[data-item]", el).forEach((button) => button.onclick = () => { handle.close(true); haptic("select"); setTimeout(() => items[+button.dataset.item].run(), 60); });
    } });
    return sheet;
  }
  function promptSheet(title, { value = "", placeholder = "", multiline = false, ok = tr("Сохранить", "Save"), max = 200 } = {}) {
    return new Promise((resolve) => {
      const field = multiline ? `<textarea id="ps-input" maxlength="${max}" placeholder="${esc(placeholder)}">${esc(value)}</textarea>`
        : `<input id="ps-input" maxlength="${max}" value="${esc(value)}" placeholder="${esc(placeholder)}" enterkeyhint="done">`;
      openSheet({ title, body: `<div class="field" style="margin-top:4px">${field}</div>`,
        actions: [{ label: ok, run: (h) => { const v = $("#ps-input").value.trim(); if (!v) { haptic("error"); return true; } resolve(v); } }],
        onClose: () => resolve(null),
        onMount: (el) => { const input = $("#ps-input", el); setTimeout(() => { input.focus(); if (input.select) input.select(); }, 280);
          if (!multiline) input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("[data-act]", el).click(); } }; } });
    });
  }

  /* ================================================================ back stack (Telegram BackButton) */
  const backStack = [];
  function pushBack(handle) { backStack.push(handle); syncBackButton(); }
  function popBack(handle) { const i = backStack.lastIndexOf(handle); if (i >= 0) backStack.splice(i, 1); syncBackButton(); }
  function syncBackButton() {
    const app = S.tg;
    const visible = backStack.length > 0;
    $("#top-back").hidden = !(visible && !(app && app.initData));
    try { if (app && app.BackButton && app.isVersionAtLeast("6.1")) { if (visible) app.BackButton.show(); else app.BackButton.hide(); } } catch (e) { /* old */ }
  }
  function goBack() { const top = backStack[backStack.length - 1]; if (top) top.back(); }

  /* ================================================================ views */
  const TABS = [
    { id: "chats", icon: "chat", ru: "Чаты", en: "Chats" },
    { id: "memes", icon: "agents", ru: "Мемесы", en: "Agents" },
    { id: "code", icon: "terminal", ru: "MemuCode", en: "MemuCode" },
    { id: "panels", icon: "target", ru: "Панели", en: "Panels" },
    { id: "modules", icon: "modules", ru: "Модули", en: "Modules" },
    { id: "settings", icon: "settings", ru: "Настройки", en: "Settings" },
  ];
  function renderTabbar() {
    const tabs = TABS.filter((tab) => (tab.id !== "code" || S.features.code) && (!tab.when || tab.when()));
    const bar = $("#tabbar");
    bar.style.setProperty("--tabs", tabs.length);
    bar.innerHTML = tabs.map((tab) => `<button class="tab ${S.tab === tab.id ? "active" : ""}" role="tab" data-tab="${tab.id}" aria-selected="${S.tab === tab.id}">
      ${icon(tab.icon)}<span>${esc(tr(tab.ru, tab.en))}</span>${tab.id === "code" && S.codeSessions.some((s) => s.live && (s.asking)) ? '<i class="dot"></i>' : tab.id === "code" && S.codeSessions.some((s) => s.live) ? '<i class="dot live"></i>' : ""}</button>`).join("");
    $$(".tab", bar).forEach((button) => button.onclick = () => switchTab(button.dataset.tab));
  }
  /* Round 29: on tablets and computers (device.js → data-layout="split") the tab's list keeps its own header
     (#list-head) on the left and the open chat / session / page fills the right pane under the top bar. */
  function isSplit() { return document.documentElement.dataset.layout === "split"; }
  /* r30: header buttons are bound with `.onclick` right after setTop()/openStack(); every redraw of the headers
     (a title update, a layout change, closing a stack) used to recreate them without their handlers — «+ New
     session», «⋯» of a chat and the session's «Files» went dead. Each header (the root of the tab, every stack
     entry) keeps the handlers of its buttons and gets them back after a redraw. */
  function keepHandlers(actionsEl) {
    const owner = actionsEl && actionsEl._owner;
    if (!owner) return;
    owner.handlers = owner.handlers || {};
    $$("[id]", actionsEl).forEach((button) => { if (button.onclick) owner.handlers[button.id] = button.onclick; });
  }
  function drawTop(titleEl, actionsEl, title, actions, compact, sub, owner = null) {
    titleEl.className = "top-title" + (compact ? " compact" : "");
    titleEl.innerHTML = esc(title) + (sub ? `<small>${esc(sub)}</small>` : "");
    keepHandlers(actionsEl);
    actionsEl.innerHTML = actions;
    actionsEl._owner = owner;
    if (owner && owner.handlers) $$("[id]", actionsEl).forEach((button) => { if (owner.handlers[button.id]) button.onclick = owner.handlers[button.id]; });
  }
  function setTop(title, actions = "", { compact = false, sub = "" } = {}) {
    if (!S.stack.length) S.rootTop = { title, actions, compact, sub };
    if (isSplit()) { refreshTops(); return; }
    const top = S.stack[S.stack.length - 1];
    drawTop($("#top-title"), $("#top-actions"), title, actions, compact, sub, top || S.rootTop);
  }
  function listHead() {
    let head = $("#list-head");
    if (!head) {
      head = document.createElement("header"); head.id = "list-head"; head.className = "list-head";
      head.innerHTML = `<div class="topbar-inner"><div class="top-title" id="list-title"></div><div class="top-actions" id="list-actions"></div></div>`;
      $("#app").insertBefore(head, $("#views"));
    }
    return head;
  }
  /* Draw the headers for the current layout (also called by device.js when the layout changes). */
  function refreshTops() {
    const root = S.rootTop || { title: "", actions: "", compact: false, sub: "" };
    const top = S.stack[S.stack.length - 1];
    const split = isSplit();
    const head = listHead();
    head.hidden = !split;
    $("#app").dataset.tab = S.tab;
    $("#app").classList.toggle("has-detail", !!top);
    keepHandlers($("#top-actions")); keepHandlers($("#list-actions"));
    if (split) {
      // The ids of the root actions must exist only once: clear the top bar first.
      $("#top-actions").innerHTML = ""; $("#top-actions")._owner = null;
      drawTop($("#list-title"), $("#list-actions"), root.title, root.actions, root.compact, root.sub, S.rootTop);
      if (top) drawTop($("#top-title"), $("#top-actions"), top.title, top.actions, top.compact, top.sub, top);
      else drawTop($("#top-title"), $("#top-actions"), "", "", true, "");
      drawDetailEmpty(!top);
    } else {
      if ($("#list-actions")) { $("#list-actions").innerHTML = ""; $("#list-actions")._owner = null; }
      const shown = top || root;
      drawTop($("#top-title"), $("#top-actions"), shown.title, shown.actions, shown.compact, shown.sub, top || S.rootTop);
      drawDetailEmpty(false);
    }
  }
  const DETAIL_HINTS = {
    chats: ["chat", "Выберите чат слева или начните новый", "Pick a chat on the left or start a new one"],
    memes: ["agents", "Откройте мемеса или начните с ним чат", "Open an agent or start a chat with it"],
    code: ["terminal", "Откройте сессию MemuCode или создайте новую", "Open a MemuCode session or create a new one"],
    modules: ["modules", "Выберите модуль слева", "Pick a module on the left"],
    settings: ["settings", "Выберите раздел настроек", "Pick a settings section"],
  };
  function drawDetailEmpty(show) {
    let box = $("#detail-empty");
    if (!box) { box = document.createElement("div"); box.id = "detail-empty"; box.className = "detail-empty"; $("#views").appendChild(box); }
    const hint = DETAIL_HINTS[S.tab];
    box.hidden = !show || !hint;
    if (show && hint) box.innerHTML = `<div class="detail-art">${icon(hint[0])}</div><p>${esc(tr(hint[1], hint[2]))}</p>${window.MemuDevice && window.MemuDevice.device === "desktop" ? `<p class="kbd-hint">${esc(tr("Сочетания клавиш", "Keyboard shortcuts"))}: <kbd>?</kbd></p>` : ""}`;
  }
  /* A chat, a session or a settings page opened from a list replaces the open one in the right pane. */
  function primaryStack() { if (isSplit()) while (S.stack.length) closeStack(S.stack[S.stack.length - 1], { instant: true }); }
  function rootView() {
    const views = $("#views");
    let view = $(".view.root", views);
    if (!view) { view = document.createElement("div"); view.className = "view root"; views.appendChild(view); bindScrollShadow(view); }
    return view;
  }
  function bindScrollShadow(el) { el.addEventListener("scroll", () => $("#topbar").classList.toggle("scrolled", el.scrollTop > 4), { passive: true }); }
  function switchTab(tab, { force = false, keepScroll = false } = {}) {
    if (S.tab === tab && !force && !S.stack.length) { const v = rootView(); v.scrollTo({ top: 0, behavior: "smooth" }); return; }
    while (S.stack.length) closeStack(S.stack[S.stack.length - 1], { instant: true });
    if (!keepScroll) haptic("select");
    S.tab = tab;
    $("#app").dataset.tab = tab;
    Object.values(S.timers).forEach((timer) => clearInterval(timer)); S.timers = {};
    renderTabbar();
    const view = rootView();
    const scrolled = view.scrollTop;
    view.scrollTop = 0;
    if (!keepScroll) { view.classList.remove("fade"); void view.offsetWidth; view.classList.add("fade"); }
    $("#topbar").classList.remove("scrolled");
    ({ chats: renderChatsTab, memes: renderMemesTab, code: renderCodeTab, modules: renderModulesTab, settings: renderSettingsTab, panels: renderPanelsTab }[tab] || kit.tabViews[tab] || renderChatsTab)(view);
    if (keepScroll) view.scrollTop = scrolled;
    refreshTops();
    try { sessionStorage.setItem("memu_tab", tab); } catch (e) { /* ignore */ }
  }
  function openStack({ title = "", sub = "", actions = "", build, onClose, compact = true }) {
    const view = document.createElement("div");
    view.className = "view stack enter";
    $("#views").appendChild(view);
    const entry = { view, title, sub, actions, onClose, compact, back: () => closeStack(entry) };
    S.stack.push(entry);
    pushBack(entry);
    $("#tabbar").classList.add("hidden");
    setTop(title, actions, { compact, sub });
    $("#topbar").classList.remove("scrolled");
    build(view, entry);
    return entry;
  }
  function closeStack(entry, { instant = false } = {}) {
    const i = S.stack.indexOf(entry);
    if (i < 0) return;
    S.stack.splice(i, 1);
    popBack(entry);
    if (entry.onClose) try { entry.onClose(); } catch (e) { console.error(e); }
    if (instant) entry.view.remove();
    else { entry.view.classList.add("leave"); setTimeout(() => entry.view.remove(), 260); }
    const top = S.stack[S.stack.length - 1];
    if (top) setTop(top.title, top.actions, { compact: top.compact, sub: top.sub });
    else {
      $("#tabbar").classList.remove("hidden");
      // Split view: the list stayed visible — refresh it in place instead of jumping to its top.
      if (!instant) switchTab(S.tab, { force: true, keepScroll: isSplit() });
      else refreshTops();
    }
  }
  function updateStackTop(entry, { title, sub, actions }) {
    if (title !== undefined) entry.title = title;
    if (sub !== undefined) entry.sub = sub;
    if (actions !== undefined) entry.actions = actions;
    if (S.stack[S.stack.length - 1] === entry) setTop(entry.title, entry.actions, { compact: entry.compact, sub: entry.sub });
  }

  function longPress(el, handler) {
    let timer = null, moved = false, fired = false;
    el.addEventListener("touchstart", (e) => { moved = false; fired = false; const t0 = e.touches[0]; const x = t0.clientX, y = t0.clientY;
      timer = setTimeout(() => { if (!moved) { fired = true; haptic("medium"); handler(); } }, 480);
      el._lp = { x, y }; }, { passive: true });
    el.addEventListener("touchmove", (e) => { const t0 = e.touches[0]; if (el._lp && Math.hypot(t0.clientX - el._lp.x, t0.clientY - el._lp.y) > 10) { moved = true; clearTimeout(timer); } }, { passive: true });
    el.addEventListener("touchend", (e) => { clearTimeout(timer); if (fired) e.preventDefault(); }, { passive: false });
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); clearTimeout(timer); if (!fired) handler(); });
    el.addEventListener("click", (e) => { if (fired) { e.stopImmediatePropagation(); e.preventDefault(); fired = false; } }, true);
  }

  /* ================================================================ CHATS TAB */
  async function loadChats(force = false) {
    try {
      const data = await api("chats.list", { rev: force ? "" : S.chatsRev });
      if (data.unchanged) return false;
      S.chats = data.chats || []; S.chatsRev = data.rev; S.folders = data.folders || S.folders;
      return true;
    } catch (error) { if (force) toast(error.message, "err"); return false; }
  }
  function renderChatsTab(view) {
    setTop(tr("Чаты", "Chats"), `<button class="icon-btn" id="new-temp" aria-label="${esc(tr("Временный чат", "Temporary chat"))}">${icon("temp")}</button>`);
    view.innerHTML = `<div class="search">${icon("search")}<input id="chat-search" type="search" placeholder="${esc(tr("Поиск", "Search"))}" value="${esc(S.chatQuery)}" autocomplete="off"></div>
      <div class="chips" id="chat-chips"></div><div id="chat-list"></div><div id="chat-hits"></div>
      <button class="fab" id="chat-new" aria-label="${esc(tr("Новый чат", "New chat"))}">${icon("compose")}</button>`;
    $("#new-temp").onclick = () => openChat(null, { temp: true });
    $("#chat-new").onclick = () => { haptic("medium"); openChat(null); };
    $("#chat-search").oninput = (e) => { S.chatQuery = e.target.value; S.chatLimit = 60; drawChatList(); searchMessagesSoon(); };
    drawChatChips(); drawChatList();
    loadChats(true).then(() => { drawChatChips(); drawChatList(); });
    S.timers.chats = setInterval(async () => { if (document.hidden || S.stack.length) return; if (await loadChats()) { drawChatChips(); drawChatList(); } }, 5000);
  }
  function drawChatChips() {
    const box = $("#chat-chips"); if (!box) return;
    const count = (filter) => S.chats.filter(filter).length;
    const chips = [["all", tr("Все", "All"), S.chats.length], ["pinned", tr("Закреплённые", "Pinned"), count((c) => c.pinned)]];
    if (S.memes.length) chips.push(["memes", tr("Мемесы", "Agents"), count((c) => c.meme_id)]);
    (S.folders || []).forEach((f) => chips.push(["f:" + f.id, f.name, count((c) => c.folder_id === f.id)]));
    box.innerHTML = chips.map(([id, label, n]) => `<button class="chip ${S.chatFilter === id ? "active" : ""}" data-filter="${esc(id)}">${id.startsWith("f:") ? icon("folder") : ""}${esc(label)}${n ? ` <small>${n}</small>` : ""}</button>`).join("") +
      `<button class="chip" id="folder-add" aria-label="${esc(tr("Новая папка", "New folder"))}">${icon("plus")}</button>`;
    $$("[data-filter]", box).forEach((button) => {
      button.onclick = () => { S.chatFilter = button.dataset.filter; haptic("select"); drawChatChips(); drawChatList(); };
      if (button.dataset.filter.startsWith("f:")) longPress(button, () => folderMenu(button.dataset.filter.slice(2)));
    });
    $("#folder-add").onclick = createFolder;
  }
  function visibleChats() {
    const q = S.chatQuery.trim().toLowerCase();
    let rows = S.chats.slice();
    if (S.chatFilter === "pinned") rows = rows.filter((c) => c.pinned);
    else if (S.chatFilter === "memes") rows = rows.filter((c) => c.meme_id);
    else if (S.chatFilter.startsWith("f:")) rows = rows.filter((c) => c.folder_id === S.chatFilter.slice(2));
    if (q) rows = rows.filter((c) => (c.title || "").toLowerCase().includes(q) || (c.snippet || "").toLowerCase().includes(q));
    rows.sort((a, b) => (b.pinned - a.pinned) || (b.updated - a.updated));
    return rows;
  }
  function chatAvatar(chat) {
    const meme = chat.meme_id && memeById(chat.meme_id);
    if (meme) return memeAvatar(meme) ;
    const [a, b] = initialsGradient(chat.id);
    const letter = (String(chat.title || "").trim().match(/[\p{L}\p{N}]/u) || ["•"])[0].toUpperCase();
    return `<div class="avatar" style="--av1:${a};--av2:${b}">${esc(letter)}${chat.running ? '<i class="run"></i>' : ""}</div>`;
  }
  function drawChatList() {
    const box = $("#chat-list"); if (!box) return;
    // While searching the «new chat» button would sit on top of the results — hide it (as Telegram does).
    const rows = visibleChats();
    // Round 34: …and over an empty list, whose own «New chat» button is the one action (no second button).
    const fab = $("#chat-new"); if (fab) fab.classList.toggle("fab-off", !!S.chatQuery.trim() || !rows.length);
    if (!rows.length) {
      box.innerHTML = `<div class="empty"><div class="empty-art">${icon("chat")}</div><h3>${esc(S.chatQuery ? tr("Ничего не найдено", "Nothing found") : tr("Пока пусто", "Nothing here yet"))}</h3>
        <p>${esc(S.chatQuery ? tr("Попробуйте другое слово.", "Try another word.") : tr("Начните разговор — он появится и в MemuAI на компьютере.", "Start a conversation — it appears in MemuAI on your computer too."))}</p>
        ${S.chatQuery ? "" : `<button class="btn" id="empty-new">${icon("compose")}${esc(tr("Новый чат", "New chat"))}</button>`}</div>`;
      const b = $("#empty-new"); if (b) b.onclick = () => openChat(null);
      return;
    }
    const shown = rows.slice(0, S.chatLimit);
    box.innerHTML = `<div class="group">${shown.map((chat) => {
      const flags = [chat.pinned ? icon("pin") : "", chat.telegram ? icon("telegram") : ""].join("");
      const prefix = chat.last_role === "user" ? `<b>${esc(tr("Вы", "You"))}:</b> ` : "";
      return `<button class="chat-cell ${chat.id === S.openChatId ? "current" : ""}" data-chat="${esc(chat.id)}">${chatAvatar(chat)}<div class="chat-main"><div class="chat-top"><div class="chat-title">${esc(chat.title || tr("Без названия", "Untitled"))}</div><span class="chat-flags">${flags}</span><span class="chat-time">${esc(ago(chat.updated))}</span></div>
        <div class="chat-top"><div class="chat-snippet">${chat.running ? `<span style="color:var(--accent)">${esc(tr("печатает…", "typing…"))}</span>` : prefix + esc(chat.snippet || "")}</div>${chat.unread ? '<i class="unread-dot"></i>' : ""}</div></div></button>`;
    }).join("")}</div>${rows.length > shown.length ? `<button class="btn secondary small list-more" id="chats-more">${esc(tr("Показать ещё", "Show more"))} · ${rows.length - shown.length}</button>` : ""}`;
    $$("[data-chat]", box).forEach((cell) => {
      cell.onclick = () => { openChat(cell.dataset.chat); if (isSplit()) drawChatList(); };
      longPress(cell, () => chatMenu(cell.dataset.chat));
    });
    const more = $("#chats-more"); if (more) more.onclick = () => { S.chatLimit += 60; drawChatList(); };
  }
  /* Round 29: the search box also finds text inside messages (chats.search on the computer). */
  let searchTimer = null, searchSeq = 0;
  function searchMessagesSoon() {
    clearTimeout(searchTimer);
    const box = $("#chat-hits"); if (box && S.chatQuery.trim().length < 2) box.innerHTML = "";
    if (S.chatQuery.trim().length < 2) return;
    searchTimer = setTimeout(searchMessages, 320);
  }
  async function searchMessages() {
    const query = S.chatQuery.trim(), seq = ++searchSeq;
    let data;
    try { data = await api("chats.search", { query }); } catch (error) { return; }
    const box = $("#chat-hits");
    if (!box || seq !== searchSeq || S.chatQuery.trim() !== query) return;
    // A chat whose title matched only by a word form / typo ("фрукты" → «Три фрукта») is not in the substring-filtered
    // list above, so it is shown here too; titles the list already shows are not repeated.
    const listed = new Set(visibleChats().map((c) => c.id));
    const hits = (data.hits || []).filter((hit) => (hit.matches || []).length || (hit.title_match && !listed.has(hit.id)));
    const global = (data.global || []).filter((g) => (g.items || []).length);
    const listEmpty = $("#chat-list .empty");
    if (listEmpty) listEmpty.hidden = !!(hits.length || global.length);
    if (!hits.length && !global.length) {
      box.innerHTML = data.suggestion ? `<div class="search-suggest">${esc(tr("Возможно, вы искали", "Did you mean"))} <button type="button" data-suggest>${esc(data.suggestion)}</button></div>` : "";
      const fix = $("[data-suggest]", box);
      if (fix) fix.onclick = () => { const input = $("#chat-search"); if (input) { input.value = data.suggestion; S.chatQuery = data.suggestion; drawChatList(); searchMessagesSoon(); } };
      return;
    }
    const needle = esc(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    /* Round 29: results of the global search carry exact [start, length] highlights (word forms, typos). */
    const mark = (text, spans) => {
      const raw = String(text || "");
      if (Array.isArray(spans) && spans.length) {
        let out = "", at = 0;
        [...spans].sort((x, y) => x[0] - y[0]).forEach(([start, len]) => {
          if (start < at || start >= raw.length) return;
          out += esc(raw.slice(at, start)) + `<mark>${esc(raw.slice(start, start + len))}</mark>`; at = start + len;
        });
        return out + esc(raw.slice(at));
      }
      return esc(raw).replace(new RegExp(needle, "ig"), (x) => `<mark>${x}</mark>`);
    };
    const TYPE_ICON = { memory: "brain", prompt: "sparkle", schedule: "clock", character: "user", panel: "list", quick: "zap", work: "folder", code: "terminal", doc: "info" };
    const label = (g) => (g.label && g.label.length === 2) ? (S.lang === "en" ? g.label[1] : g.label[0]) : g.type;
    box.innerHTML = (hits.length ? `<div class="section-title">${esc(hits.some((h) => (h.matches || []).length) ? tr("В сообщениях", "In messages") : tr("Чаты", "Chats"))} · ${hits.length}</div><div class="group">${hits.map((hit) =>
      `<button class="cell flat search-hit" data-hit="${esc(hit.id)}"><div class="cell-body"><div class="cell-title">${hit.title_highlights && hit.title_highlights.length ? mark(hit.title, hit.title_highlights) : esc(hit.title || tr("Без названия", "Untitled"))}</div>${(hit.matches || []).slice(0, 2).map((m) => `<div class="cell-sub">${m.role === "user" ? `<b>${esc(tr("Вы", "You"))}:</b> ` : ""}${mark(m.snippet, m.highlights)}</div>`).join("")}</div><span class="chat-time">${esc(ago(hit.updated))}</span></button>`).join("")}</div>` : "")
      + global.map((g) => `<div class="section-title">${esc(label(g))} · ${g.count}</div><div class="group">${g.items.map((item) =>
        `<button class="cell flat search-hit" data-gid="${esc(item.id)}"><div class="cell-icon">${icon(TYPE_ICON[g.type] || "search")}</div><div class="cell-body"><div class="cell-title">${mark(item.title || tr("Без названия", "Untitled"), item.title_highlights)}</div>${item.snippet ? `<div class="cell-sub">${mark(item.snippet, item.highlights)}</div>` : (item.subtitle ? `<div class="cell-sub">${esc(item.subtitle)}</div>` : "")}</div>${item.ts ? `<span class="chat-time">${esc(ago(item.ts))}</span>` : ""}</button>`).join("")}</div>`).join("");
    $$("[data-hit]", box).forEach((b) => b.onclick = () => openChat(b.dataset.hit));
    $$("[data-gid]", box).forEach((b) => b.onclick = () => previewResult(b.dataset.gid, query, mark));
  }
  async function previewResult(id, query, mark) {
    let data;
    try { data = await api("search.preview", { id, query }); } catch (error) { toast(error.message, "err"); return; }
    const blocks = (data.blocks || []).map((b) => `<div class="search-block${b.hit ? " hit" : ""}">${b.label || b.role ? `<div class="cell-sub"><b>${esc(b.label || (b.role === "user" ? tr("Вы", "You") : b.role === "assistant" ? "MemuAI" : b.role))}</b></div>` : ""}<div class="search-text">${mark(b.text, b.highlights)}</div></div>`).join("");
    openSheet({ title: data.title || tr("Результат", "Result"), body: `${data.subtitle && data.subtitle !== data.title ? `<div class="cell-sub" style="margin-bottom:8px">${esc(data.subtitle)}</div>` : ""}<div class="search-preview">${blocks || esc(tr("Нет текста", "No text"))}</div>`, full: true });
  }
  function chatMenu(id, after) {
    const chat = S.chats.find((c) => c.id === id); if (!chat) return;
    actionSheet(chat.title || tr("Чат", "Chat"), [
      { label: chat.pinned ? tr("Открепить", "Unpin") : tr("Закрепить", "Pin"), icon: "pin", run: () => patchChat(id, { pinned: !chat.pinned }, after) },
      { label: tr("Переименовать", "Rename"), icon: "edit", run: async () => { const title = await promptSheet(tr("Название чата", "Chat title"), { value: chat.title, max: 120 }); if (title) patchChat(id, { title }, after); } },
      { label: tr("В папку…", "Move to folder…"), icon: "folder", run: () => moveToFolder(id, after) },
      { label: chat.unread ? tr("Прочитано", "Mark as read") : tr("Непрочитанное", "Mark as unread"), icon: "unread", run: () => patchChat(id, { unread: !chat.unread }, after) },
      { label: tr("Удалить", "Delete"), icon: "trash", danger: true, run: async () => {
        if (S.settings.confirm_deletes !== false && !(await confirmDialog(tr("Чат исчезнет и на компьютере.", "The chat will disappear on the computer too."), { title: tr("Удалить чат?", "Delete chat?"), ok: tr("Удалить", "Delete"), danger: true }))) return;
        try { await api("chats.delete", { id }); S.chats = S.chats.filter((c) => c.id !== id); S.chatsRev = ""; drawChatList(); drawChatChips(); toast(tr("Чат удалён", "Chat deleted"), "ok"); if (after) after("deleted"); }
        catch (error) { toast(error.message, "err"); }
      } },
    ]);
  }
  async function patchChat(id, patch, after) {
    try {
      const data = await api("chats.update", Object.assign({ id }, patch));
      const i = S.chats.findIndex((c) => c.id === id);
      if (i >= 0) S.chats[i] = Object.assign(S.chats[i], data.chat);
      S.chatsRev = ""; drawChatList(); drawChatChips(); haptic("success");
      if (after) after("patched", data.chat);
    } catch (error) { toast(error.message, "err"); }
  }
  function moveToFolder(id, after) {
    const chat = S.chats.find((c) => c.id === id);
    const items = [{ label: tr("Без папки", "No folder"), icon: chat && !chat.folder_id ? "check" : "close", run: () => patchChat(id, { folder_id: "" }, after) }]
      .concat((S.folders || []).map((f) => ({ label: f.name, icon: chat && chat.folder_id === f.id ? "check" : "folder", run: () => patchChat(id, { folder_id: f.id }, after) })))
      .concat([{ label: tr("Новая папка…", "New folder…"), icon: "plus", run: async () => { const f = await createFolder(); if (f) patchChat(id, { folder_id: f.id }, after); } }]);
    actionSheet(tr("Переместить в папку", "Move to folder"), items);
  }
  async function createFolder() {
    const name = await promptSheet(tr("Новая папка", "New folder"), { placeholder: tr("Название папки", "Folder name"), max: 50, ok: tr("Создать", "Create") });
    if (!name) return null;
    const folder = { id: "f-" + Math.random().toString(36).slice(2, 12), name };
    try { const data = await api("folders.save", { folders: (S.folders || []).concat([folder]) }); S.folders = data.folders; drawChatChips(); toast(tr("Папка создана", "Folder created"), "ok"); return folder; }
    catch (error) { toast(error.message, "err"); return null; }
  }
  function folderMenu(id) {
    const folder = (S.folders || []).find((f) => f.id === id); if (!folder) return;
    actionSheet(folder.name, [
      { label: tr("Переименовать", "Rename"), icon: "edit", run: async () => {
        const name = await promptSheet(tr("Название папки", "Folder name"), { value: folder.name, max: 50 }); if (!name) return;
        try { const data = await api("folders.save", { folders: S.folders.map((f) => f.id === id ? Object.assign({}, f, { name }) : f) }); S.folders = data.folders; drawChatChips(); } catch (e) { toast(e.message, "err"); } } },
      { label: tr("Удалить папку", "Delete folder"), icon: "trash", danger: true, run: async () => {
        if (!(await confirmDialog(tr("Чаты из папки останутся в списке.", "Chats in the folder stay in the list."), { title: tr("Удалить папку?", "Delete folder?"), ok: tr("Удалить", "Delete"), danger: true }))) return;
        try { const data = await api("folders.save", { folders: S.folders.filter((f) => f.id !== id) }); S.folders = data.folders; if (S.chatFilter === "f:" + id) S.chatFilter = "all"; await loadChats(true); drawChatChips(); drawChatList(); } catch (e) { toast(e.message, "err"); } } },
    ]);
  }

  /* ================================================================ CONVERSATION */
  function openChat(chatId, { temp = false, memeId = "" } = {}) {
    primaryStack();
    const C = { id: chatId, temp, memeId, messages: [], model: "", web: null, think: null, effort: "", attachments: [], job: null, control: null,
      streaming: null, total: 0, title: "", tempHistory: [] };
    S.openChatId = chatId || "";
    const listRow = chatId && S.chats.find((c) => c.id === chatId);
    if (listRow) { C.title = listRow.title; C.memeId = listRow.meme_id || ""; C.model = listRow.model || ""; }
    const meme = memeById(C.memeId);
    if (!C.model) C.model = (meme && meme.model && S.models.some((m) => m.id === meme.model)) ? meme.model : (S.settings.default_model || (S.models[0] && S.models[0].id) || "");
    if (!S.models.some((m) => m.id === C.model)) C.model = (S.models[0] && S.models[0].id) || "";
    C.web = meme ? !!meme.web_search : !!S.settings.web_search;
    C.think = meme ? !!meme.thinking : !!S.settings.thinking;
    const entry = openStack({
      title: C.title || (temp ? tr("Временный чат", "Temporary chat") : tr("Новый чат", "New chat")),
      sub: meme ? meme.name : "",
      actions: chatId ? `<button class="icon-btn" id="chat-more" aria-label="${esc(tr("Действия", "Actions"))}">${icon("more")}</button>` : "",
      build: (view, stackEntry) => {
        view.innerHTML = `<div class="convo" id="convo"><div class="convo-inner" id="convo-inner"></div></div>
          <div class="composer" id="composer"><div class="composer-inner">
            ${temp ? `<div class="temp-banner">${icon("temp")}${esc(tr("Временный чат: не сохраняется и не использует память", "Temporary chat: not saved, no memory"))}</div>` : ""}
            <div class="att-tray" id="att-tray" hidden></div>
            <div class="composer-tools" id="composer-tools"></div>
            <div class="composer-row">
              <div class="composer-box"><button class="round-btn" id="att-btn" aria-label="${esc(tr("Прикрепить", "Attach"))}">${icon("attach")}</button>
                <textarea id="msg-input" rows="1" placeholder="${esc(meme ? tr("Сообщение для ", "Message ") + meme.name : tr("Сообщение", "Message"))}" enterkeyhint="enter"></textarea></div>
              <button class="round-btn send-btn" id="send-btn" disabled aria-label="${esc(tr("Отправить", "Send"))}">${icon("send")}</button>
            </div></div></div>
          <input type="file" id="file-input" multiple hidden accept="image/*,audio/*,.ogg,.oga,.opus,.m4a,.mp3,.wav,.flac,.pdf,.docx,.txt,.md,.json,.csv,.py,.js,.ts,.html,.css,.xml,.yaml,.yml,.log,.sql,.java,.c,.cpp,.cs,.go,.rs,.rb,.php">`;
        bindComposer(C, view, stackEntry);
        // Round 29: feature modules add their composer buttons (voice.js — the microphone).
        kit.hooks.composerExtras.forEach((fn) => { try { fn(C, view, stackEntry); } catch (e) { console.error(e); } });
      },
      onClose: () => { if (S.openChatId === C.id) S.openChatId = ""; if (C.control) C.control.cancelled = true; if (C.control && C.control.abort) try { C.control.abort.abort(); } catch (e) { /* closed */ } S.chatsRev = ""; },
    });
    C.entry = entry;
    const more = $("#chat-more"); if (more) more.onclick = () => chatMenu(C.id, (what, chat) => { if (what === "deleted") closeStack(entry); else if (chat) { C.title = chat.title; updateStackTop(entry, { title: chat.title }); } });
    drawToolsRow(C);
    if (chatId) loadConversation(C); else drawConversation(C);
    return C;
  }
  async function loadConversation(C, limit = 120) {
    try {
      const data = await api("chats.get", { id: C.id, limit });
      C.messages = data.messages || []; C.total = data.total || C.messages.length;
      C.title = data.chat.title || C.title; C.memeId = data.chat.meme_id || C.memeId;
      if (data.chat.model && S.models.some((m) => m.id === data.chat.model)) C.model = data.chat.model;
      updateStackTop(C.entry, { title: C.title || tr("Чат", "Chat"), sub: memeById(C.memeId) ? memeById(C.memeId).name : modelName(C.model) });
      drawToolsRow(C);
      drawConversation(C, true);
      if (data.job && !C.job) attachJob(C, data.job, 0, true);
    } catch (error) { toast(error.message, "err"); }
  }
  function assistantAvatar(C) {
    const meme = memeById(C.memeId);
    if (meme) return memeAvatar(meme, "").replace('class="avatar ', 'class="ava ').replace(/--av1:[^;]+;--av2:[^"]+/, "");
    return `<div class="ava">${'<svg viewBox="0 0 32 32"><path d="M9 22V11l7 6.5 7-6.5v11" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'}</div>`;
  }
  /* Round 35 (branches): «‹ 2/3 ›» — the chat keeps other versions of this message (regenerate / edit). */
  function versionsHtml(C, m) {
    const v = m.versions;
    if (!v || !(v.n > 1) || C.temp) return "";
    const off = C.job ? " disabled" : "";
    const chev = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
    return `<span class="ver-switch" role="group" aria-label="${esc(tr("Версии", "Versions"))}">`
      + `<button data-ver-key="${esc(v.key)}" data-ver-to="${v.cur - 1}" aria-label="${esc(tr("Предыдущая версия", "Previous version"))}"${v.cur <= 0 ? " disabled" : off}>${chev("M15 6l-6 6 6 6")}</button>`
      + `<b>${v.cur + 1}/${v.n}</b>`
      + `<button data-ver-key="${esc(v.key)}" data-ver-to="${v.cur + 1}" aria-label="${esc(tr("Следующая версия", "Next version"))}"${v.cur >= v.n - 1 ? " disabled" : off}>${chev("M9 6l6 6-6 6")}</button></span>`;
  }
  function messageHtml(C, m, index, isLast) {
    if (m.role === "user") {
      const atts = (m.attachments || []).map((a) => a.isImg && a.data ? `<img src="${esc(a.data)}" alt="${esc(a.name)}">` : `<span class="file-pill">${icon(a.isImg ? "image" : "file")}${esc(a.name)}</span>`).join("");
      const long = (m.content || "").length > 900;
      // Round 29: your messages can be copied and edited (the answer after it is written again).
      const editable = !C.temp && C.id && typeof m.i === "number" && !C.job && m.content;
      const userTools = m.content || m.versions ? `<div class="msg-tools user-tools">${versionsHtml(C, m)}<button data-copy-msg="${index}" aria-label="${esc(tr("Копировать", "Copy"))}">${icon("copy")}</button>${editable ? `<button data-edit-msg="${index}" aria-label="${esc(tr("Изменить", "Edit"))}">${icon("edit")}${esc(tr("Изменить", "Edit"))}</button>` : ""}</div>` : "";
      return `<div class="msg user">${atts ? `<div class="msg-atts">${atts}</div>` : ""}${m.content ? `<div class="bubble ${long ? "collapsed" : ""}" ${long ? 'data-expand="1"' : ""}>${esc(m.content)}</div>` : ""}${userTools}</div>`;
    }
    const sources = (m.sources || []).length ? `<div class="sources">${m.sources.slice(0, 8).map((s) => { const url = s.url || s.link || ""; let host = ""; try { host = new URL(url).hostname.replace(/^www\./, ""); } catch (e) { host = ""; }
      return url ? `<a href="${esc(url)}" data-link="1">${esc((s.title || host).slice(0, 60))}<small>${esc(host)}</small></a>` : ""; }).join("")}</div>` : "";
    const thinking = m.reasoning ? `<details class="thinking"><summary>${icon("brain")}${esc(tr("Ход мыслей", "Thinking"))}</summary><div>${esc(m.reasoning)}</div></details>` : "";
    const tools = `<div class="msg-tools">${versionsHtml(C, m)}<button data-copy-msg="${index}">${icon("copy")}${esc(tr("Копировать", "Copy"))}</button>${isLast && !C.job ? `<button data-regen="1">${icon("retry")}${esc(tr("Повторить", "Regenerate"))}</button>` : ""}<span class="msg-meta">${esc(modelName(m.model))}${m.stopped ? " · " + esc(tr("остановлено", "stopped")) : ""}</span></div>`;
    const asked = (m.askForms || []).length && window.MemuAskForm ? `<div class="au-stack">${m.askForms.map((r) => MemuAskForm.html(r, { compact: true })).join("")}</div>` : "";   // Round 35
    return `<div class="msg assistant">${assistantAvatar(C)}<div class="answer-wrap" style="flex:1;min-width:0">${sources}${thinking}${asked}${m.content ? `<div class="answer">${md(m.content)}</div>` : ""}${mediaHtml(m)}${tools}</div></div>`;
  }
  /* Round 29 (r29c-media): generated pictures and videos. Files of the computer (generated_media) load through media.file. */
  const genFiles = new Map();
  function mediaHtml(m) {
    const images = (m.images || []).filter(Boolean), videos = (m.videos || []).filter(Boolean);
    if (!images.length && !videos.length && !m.mediaError) return "";
    const src = (url) => /^(data:|https:)/.test(url) ? `src="${esc(url)}"` : `data-gen-src="${esc(url)}"`;
    const prompt = m.media && m.media.prompt ? `<details class="gen-prompt"><summary>${icon("image")}${esc(m.media.autoprompted ? tr("Улучшенный промпт", "Enhanced prompt") : tr("Промпт", "Prompt"))}</summary><div>${esc(m.media.prompt)}</div></details>` : "";
    return `<div class="gen-media">${images.map((url) => `<img class="gen-img" ${src(url)} alt="${esc(tr("Созданное изображение", "Generated image"))}" loading="lazy">`).join("")}`
      + `${videos.map((url) => `<video class="gen-video" controls playsinline preload="metadata" ${src(url)}></video>`).join("")}${prompt}`
      + `${m.mediaError ? `<div class="msg-error">${icon("warn")}<div>${esc(m.mediaError)}</div></div>` : ""}</div>`;
  }
  function bindGenMedia(box) {
    // Round 33 (r33d-media): an old picture behind a temporary link (DashScope & co., ~24 h) — a clean note, no broken icon.
    $$("img.gen-img[src^='https:']", box).forEach((img) => img.addEventListener("error", () => img.replaceWith(Object.assign(document.createElement("div"),
      { className: "gen-missing", textContent: tr("Изображение истекло — попросите создать его заново", "The image expired — ask to create it again") })), { once: true }));
    $$("[data-gen-src]", box).forEach(async (el) => {
      const url = el.dataset.genSrc; el.removeAttribute("data-gen-src");
      try {
        if (!genFiles.has(url)) genFiles.set(url, api("media.file", { url }).then((r) => r.data));
        el.src = await genFiles.get(url);
      } catch (error) { genFiles.delete(url); el.replaceWith(Object.assign(document.createElement("div"), { className: "gen-missing", textContent: tr("Файл недоступен", "The file is unavailable") })); }
    });
  }
  function heroHtml(C) {
    const meme = memeById(C.memeId);
    const name = S.user && S.user.first_name ? S.user.first_name : "";
    const suggestions = meme ? [] : [tr("Объясни простыми словами, как работает HTTPS", "Explain simply how HTTPS works"),
      tr("Составь план на неделю для изучения Python", "Plan a week of learning Python"), tr("Придумай 5 идей для выходных", "Give me 5 ideas for the weekend")];
    return `<div class="convo-hero">${meme ? memeAvatar(meme) : `<div class="avatar" style="--av1:var(--accent);--av2:var(--accent2)"><svg viewBox="0 0 32 32"><path d="M9 22V11l7 6.5 7-6.5v11" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>`}
      <h2>${esc(meme ? meme.name : (C.temp ? tr("Временный чат", "Temporary chat") : (name ? tr("Привет, ", "Hi, ") + name : tr("Чем помочь?", "How can I help?"))))}</h2>
      <p>${esc(meme ? (meme.description || tr("Ваш мемес готов к работе.", "Your agent is ready.")) : (C.temp ? tr("Ничего не сохранится в истории и памяти.", "Nothing is kept in history or memory.") : tr("Ответ придёт с вашего компьютера, а чат появится и в MemuAI.", "The answer comes from your computer; the chat appears in MemuAI too.")))}</p>
      ${suggestions.length ? `<div class="suggests">${suggestions.map((s) => `<button data-suggest="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : ""}</div>`;
  }
  function drawConversation(C, scrollEnd = false) {
    const box = $("#convo-inner", C.entry.view); if (!box) return;
    const last = C.messages.length - 1;
    const lastAssistant = (() => { for (let i = last; i >= 0; i--) if (C.messages[i].role === "assistant") return i; return -1; })();
    let html = "";
    if (C.total > C.messages.length) html += `<button class="btn secondary small load-older" id="load-older">${esc(tr("Ранние сообщения", "Earlier messages"))} · ${C.total - C.messages.length}</button>`;
    if (!C.messages.length && !C.streaming) html += heroHtml(C);
    html += C.messages.map((m, i) => messageHtml(C, m, i, i === lastAssistant && i === last)).join("");
    // An answer that failed (rate limit, offline account…) can be asked again without retyping the message.
    const canRetry = C.error && !C.job && !C.temp && C.id && C.messages.length && C.messages[last].role === "user";
    if (C.error) html += `<div class="msg-error">${icon("warn")}<div>${esc(C.error)}</div>${canRetry ? `<button class="btn secondary small" data-regen="1">${icon("retry")}${esc(tr("Повторить", "Retry"))}</button>` : ""}</div>`;
    if (C.streaming) html += `<div class="msg assistant" id="streaming">${assistantAvatar(C)}<div style="flex:1;min-width:0"><div id="stream-sources"></div><div class="answer" id="stream-answer"><div class="typing"><i></i><i></i><i></i></div></div></div></div>`;
    box.innerHTML = html;
    bindConversation(C, box);
    if (C.streaming) renderStreaming(C);
    if (scrollEnd) scrollConvo(C, true);
  }
  function bindConversation(C, box) {
    const older = $("#load-older", box); if (older) older.onclick = () => loadConversation(C, Math.min(400, C.messages.length + 120));
    $$("[data-suggest]", box).forEach((b) => b.onclick = () => { const input = $("#msg-input", C.entry.view); input.value = b.dataset.suggest; input.dispatchEvent(new Event("input")); input.focus(); });
    $$("[data-copy-msg]", box).forEach((b) => b.onclick = () => { copyText(C.messages[+b.dataset.copyMsg].content || ""); toast(tr("Скопировано", "Copied"), "ok"); });
    $$("[data-edit-msg]", box).forEach((b) => b.onclick = () => editUserMessage(C, +b.dataset.editMsg));
    $$("[data-regen]", box).forEach((b) => b.onclick = () => sendMessage(C, { regenerate: true }));
    $$("[data-ver-key]", box).forEach((b) => b.onclick = async () => {
      if (C.job) return;
      haptic("light");
      try { await api("chats.version", { id: C.id, key: b.dataset.verKey, to: +b.dataset.verTo }); await loadConversation(C, Math.max(120, C.messages.length)); }
      catch (error) { toast(error.message, "err"); }
    });
    $$("[data-expand]", box).forEach((b) => b.onclick = () => b.classList.toggle("collapsed"));
    bindRichContent(box);
    bindGenMedia(box);
  }
  function bindRichContent(box) {
    $$("[data-copy]", box).forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const code = b.closest(".md-code").querySelector("code").innerText;
      copyText(code); b.textContent = tr("Скопировано", "Copied"); b.classList.add("done");
      setTimeout(() => { b.textContent = tr("Копировать", "Copy"); b.classList.remove("done"); }, 1600);
    });
    $$("a[data-link]", box).forEach((a) => a.onclick = (e) => { e.preventDefault(); openLink(a.getAttribute("href")); });
  }
  function scrollConvo(C, force = false) {
    const convo = $("#convo", C.entry.view); if (!convo) return;
    const near = convo.scrollHeight - convo.scrollTop - convo.clientHeight < 160;
    if (force || near) convo.scrollTop = convo.scrollHeight;
  }
  let renderPending = false;
  function renderStreaming(C) {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      const box = $("#stream-answer", C.entry.view); if (!box || !C.streaming) return;
      const text = C.streaming.text.replace(/\[(?:TITLE|НАЗВАНИЕ|MEM|МЕМ):[^\]\n]*\]?$/i, "").replace(/\[(?:TITLE|НАЗВАНИЕ|MEM|МЕМ):[^\]\n]*\]/gi, "")
        .replace(/```[ \t]*memuai[-_ ]?memory\b[\s\S]*?(?:\n[ \t]*```[ \t]*(?=\n|$)|$)/gi, "")   // Round 29: memory v2 block
        .replace(/```[ \t]*memuai-(?:image|video)\b[\s\S]*?(?:```|$)/gi, "").trim();   // Round 29 (r29c-media)
      const media = C.streaming.media;
      const mediaLine = media ? `<div class="gen-progress">${icon("image")}<span>${esc(media.status === "prompting" ? tr("Продумываю промпт…", "Thinking the prompt through…")
        : media.media_kind === "video" ? tr("Создаю видео — обычно 1–5 минут", "Creating the video — usually 1–5 minutes") : tr("Создаю изображение…", "Creating the image…"))}</span><small>${esc(modelName(media.model || ""))}</small></div>` : "";
      const forms = (C.streaming.askForms || []).length && window.MemuAskForm   // Round 35 (askuser)
        ? `<div class="au-stack">${C.streaming.askForms.map((r) => MemuAskForm.html(r, { compact: true })).join("")}</div>` : "";
      if (forms && !text) { box.innerHTML = forms + ((C.streaming.askForms || []).some((r) => r.status === "pending") ? "" : '<div class="typing"><i></i><i></i><i></i></div>'); scrollConvo(C); return; }
      if (!text && !C.streaming.reasoning && !media) { box.innerHTML = '<div class="typing"><i></i><i></i><i></i></div>'; return; }
      box.innerHTML = forms + (C.streaming.reasoning && !text && !media ? `<div class="thinking"><summary style="display:inline-flex">${icon("brain")}${esc(tr("Думает…", "Thinking…"))}</summary></div>` : "") + md(text) + (text && !media ? '<span class="cursor"></span>' : "") + mediaLine;
      const last = box.lastElementChild && box.lastElementChild.previousElementSibling;
      if (last && last.tagName === "P" && box.lastElementChild.classList.contains("cursor")) { const cursor = box.lastElementChild; last.appendChild(cursor); }
      const sources = $("#stream-sources", C.entry.view);
      if (sources && C.streaming.sources && !sources.childElementCount) sources.innerHTML = messageHtml(C, { role: "assistant", sources: C.streaming.sources }, -1, false).match(/<div class="sources">[\s\S]*?<\/div>(?=<details|<div class="answer")/)?.[0] || "";
      bindRichContent(box);
      scrollConvo(C);
    });
  }
  function drawToolsRow(C) {
    const row = $("#composer-tools", C.entry.view); if (!row) return;
    const meme = memeById(C.memeId);
    row.innerHTML = `<button class="toggle-chip model" id="tool-model">${icon("model")}<span>${esc(modelName(C.model) || tr("Модель", "Model"))}</span></button>
      <button class="toggle-chip ${C.web ? "on" : ""}" id="tool-web">${icon("globe")}${esc(tr("Поиск", "Search"))}</button>
      ${thinkChip(C)}
      ${!C.id && !C.messages.length && !meme && !C.temp ? `<button class="toggle-chip" id="tool-meme">${icon("agents")}${esc(tr("Мемес", "Agent"))}</button>` : ""}`;
    $("#tool-model", row).onclick = () => pickModel(C.model, (id) => { if (id !== C.model) C.effort = ""; C.model = id; drawToolsRow(C); if (C.id) updateStackTop(C.entry, { sub: meme ? meme.name : modelName(id) }); });
    $("#tool-web", row).onclick = () => { C.web = !C.web; haptic("select"); drawToolsRow(C); };
    const thinkBtn = $("#tool-think", row);
    if (thinkBtn) thinkBtn.onclick = () => {
      const levels = modelLevels(C.model);
      if (!levels) { C.think = !C.think; haptic("select"); drawToolsRow(C); return; }
      const current = chatEffort(C);
      actionSheet(tr("Глубина размышления", "Reasoning effort") + " · " + modelName(C.model), levels.map((level) => ({
        label: effortLabel(level), checked: level === current, icon: level === "off" ? "close" : "brain",
        sub: level === "auto" ? tr("Как решит модель (или ваш уровень для неё в MemuAI)", "The model decides (or your MemuAI level for it)") : "",
        run: () => { C.effort = level; drawToolsRow(C); } })));
    };
    const memeBtn = $("#tool-meme", row);
    if (memeBtn) memeBtn.onclick = () => actionSheet(tr("Начать с мемесом", "Start with an agent"), S.memes.map((m) => ({ label: m.name, sub: m.description, icon: "agents",
      run: () => { closeStack(C.entry, { instant: true }); openChat(null, { memeId: m.id }); } })).concat(S.memes.length ? [] : [{ label: tr("Создать мемеса", "Create an agent"), icon: "plus", run: () => { closeStack(C.entry, { instant: true }); switchTab("memes"); } }]));
  }
  /* Round 33 (r33c-models-lists): reasoning levels of THIS model (app/reasoning_levels.py via chat_models rows) —
     the chip opens only the levels the model accepts; a model without a control shows no chip. */
  const EFFORT_LABELS = { auto: ["Авто", "Auto"], off: ["Выкл", "Off"], on: ["Вкл", "On"], minimal: ["Минимум", "Minimal"],
    low: ["Низкая", "Low"], medium: ["Средняя", "Medium"], high: ["Высокая", "High"], xhigh: ["Очень высокая", "Extra high"], max: ["Максимум", "Max"] };
  function effortLabel(level) { const pair = EFFORT_LABELS[level] || [level, level]; return tr(pair[0], pair[1]); }
  function modelLevels(id) {
    const row = S.models.find((m) => m.id === id);
    if (!row || !Array.isArray(row.reasoning_levels)) return null;           // an older server: the plain toggle
    return row.reasoning_levels.length ? row.reasoning_levels : [];
  }
  function chatEffort(C) {
    const levels = modelLevels(C.model) || [];
    const row = S.models.find((m) => m.id === C.model) || {};
    const value = C.effort || row.effort || "auto";
    return levels.includes(value) ? value : "auto";
  }
  function thinkChip(C) {
    const levels = modelLevels(C.model);
    if (levels === null) return `<button class="toggle-chip ${C.think ? "on" : ""}" id="tool-think">${icon("brain")}${esc(tr("Размышление", "Thinking"))}</button>`;
    if (!levels.length) return "";
    const level = chatEffort(C);
    return `<button class="toggle-chip effort ${level !== "auto" && level !== "off" ? "on" : ""}" id="tool-think" title="${esc(tr("Глубина размышления", "Reasoning effort"))}">${icon("brain")}<span>${esc(level === "auto" ? tr("Размышление", "Thinking") : effortLabel(level))}</span></button>`;
  }
  function modelBadges(m) {
    const out = [];
    if (m.vision_state === "yes" || (m.vision_state === undefined && m.vision === true)) out.push(`<span class="mbadge eye" title="${esc(tr("Видит изображения", "Sees images"))}">${esc(tr("зрение", "vision"))}</span>`);
    else if (m.vision_state === "unknown") out.push(`<span class="mbadge guess" title="${esc(tr("Неизвестно, видит ли изображения", "Not known whether it sees images"))}">${esc(tr("зрение?", "vision?"))}</span>`);
    if (Array.isArray(m.reasoning_levels) && m.reasoning_levels.length) out.push(`<span class="mbadge think" title="${esc(tr("Можно выбрать глубину размышления", "Reasoning effort can be chosen"))}">${icon("brain")}</span>`);
    if ((m.badges || []).includes("free")) out.push(`<span class="mbadge free">${esc(tr("бесплатно", "free"))}</span>`);
    return out.join("");
  }
  function pickModel(current, done, { allowEmpty = false, emptyLabel = "" } = {}) {
    let query = "";
    const draw = (el) => {
      const rows = S.models.filter((m) => !query || query.split(/\s+/).every((word) => (m.name + " " + (m.auto_name || "") + " " + m.id + " " + (m.provider || "")).toLowerCase().includes(word)));
      $("#model-list", el).innerHTML = (allowEmpty ? `<button class="cell model-item flat" data-model=""><div class="cell-body"><div class="cell-title">${esc(emptyLabel || tr("Как в MemuAI", "Same as MemuAI"))}</div></div>${!current ? `<span class="check">${icon("check")}</span>` : ""}</button>` : "") +
        rows.map((m) => `<button class="cell model-item flat" data-model="${esc(m.id)}"><div class="cell-body"><div class="cell-title"><span class="mname">${esc(modelName(m.id))}</span>${modelBadges(m)}</div><div class="tag"><span class="mid">${esc(m.id)}</span>${m.default ? " · " + esc(tr("основная", "default")) : ""}${m.pinned ? " · " + esc(tr("закреплена", "pinned")) : ""}</div></div>${m.id === current ? `<span class="check">${icon("check")}</span>` : ""}</button>`).join("") ||
        `<div class="empty" style="padding:30px">${esc(tr("Нет моделей: добавьте аккаунт в MemuAI Proxy", "No models: add an account in MemuAI Proxy"))}</div>`;
      $$("[data-model]", el).forEach((b) => b.onclick = () => { haptic("select"); done(b.dataset.model); handle.close(true); });
    };
    const handle = openSheet({ title: tr("Модель", "Model"), full: S.models.length > 8,
      body: `<div class="search">${icon("search")}<input id="model-q" type="search" placeholder="${esc(tr("Найти модель", "Find a model"))}"></div><div class="group flat" id="model-list"></div>`,
      onMount: (el) => { draw(el); $("#model-q", el).oninput = (e) => { query = e.target.value.toLowerCase(); draw(el); }; } });
  }
  /* Enter sends on a computer (a touch-screen laptop too — "ontouchstart" alone made Enter a new line there);
     on a phone Enter is a new line and the button sends. */
  function enterSends() {
    const device = window.MemuDevice;
    return device ? device.device === "desktop" || document.documentElement.dataset.pointer === "fine" : !("ontouchstart" in window);
  }
  function bindComposer(C, view, entry) {
    const input = $("#msg-input", view), send = $("#send-btn", view), fileInput = $("#file-input", view);
    const autosize = () => { input.style.height = "auto"; input.style.height = Math.min(168, input.scrollHeight) + "px"; };
    const sync = () => { send.disabled = !C.job && !input.value.trim() && !C.attachments.length; };
    input.addEventListener("input", () => { autosize(); sync(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (e.ctrlKey || e.metaKey || enterSends())) {
        e.preventDefault();
        // While an answer is being written the button is «Stop»: Enter must not stop it by accident.
        if (C.job) { if (input.value.trim()) toast(tr("Дождитесь ответа или остановите его", "Wait for the answer or stop it")); return; }
        if (!send.disabled) send.click();
      }
    });
    input.addEventListener("focus", () => setTimeout(() => scrollConvo(C, true), 300));
    send.onclick = () => { if (C.job) stopJob(C); else sendMessage(C); };
    $("#att-btn", view).onclick = () => { const meme = memeById(C.memeId); if (meme && meme.caps && meme.caps.files === false) { toast(tr("Этому мемесу запрещены файлы", "Files are disabled for this agent"), "err"); return; } fileInput.click(); };
    fileInput.onchange = async () => { for (const file of Array.from(fileInput.files || [])) await addAttachment(C, file); fileInput.value = ""; drawAttachments(C); sync(); };
    C.syncComposer = () => {
      send.classList.toggle("stop", !!C.job); send.innerHTML = C.job ? icon("stop") : icon("send");
      send.setAttribute("aria-label", C.job ? tr("Остановить", "Stop") : tr("Отправить", "Send"));
      sync();
    };
  }
  async function addAttachment(C, file) {
    // Round 29: an audio file is transcribed on the computer (voice.js) instead of being attached.
    if (window.MemuTgVoice && window.MemuTgVoice.isAudioFile(file)) { await window.MemuTgVoice.transcribeFile(C, file); return; }
    if (C.attachments.length >= 8) { toast(tr("Не больше 8 вложений", "Up to 8 attachments"), "err"); return; }
    try {
      if (/^image\//.test(file.type) && !/gif$/.test(file.type)) C.attachments.push({ name: file.name || "photo.jpg", isImg: true, data: await shrinkImage(file) });
      else {
        if (file.size > 5 * 1024 * 1024) { toast(file.name + ": " + tr("больше 5 МБ", "larger than 5 MB"), "err"); return; }
        C.attachments.push({ name: file.name || "file", isImg: /^image\//.test(file.type), data: await readDataUrl(file) });
      }
      haptic("light");
    } catch (error) { toast(tr("Не удалось прочитать файл", "Could not read the file"), "err"); }
  }
  function readDataUrl(file) { return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(file); }); }
  async function shrinkImage(file) {
    const url = await readDataUrl(file);
    const img = await new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = url; });
    const max = 1800, scale = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
    if (scale === 1 && file.size < 1.5 * 1024 * 1024) return url;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(img.naturalWidth * scale); canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.86);
  }
  function drawAttachments(C) {
    const tray = $("#att-tray", C.entry.view); if (!tray) return;
    tray.hidden = !C.attachments.length;
    tray.innerHTML = C.attachments.map((a, i) => `<div class="att">${a.isImg ? `<img src="${esc(a.data)}" alt="">` : `<span class="file-pill">${icon("file")}${esc(a.name)}</span>`}<button class="x" data-rm="${i}" aria-label="${esc(tr("Убрать", "Remove"))}">${icon("close")}</button></div>`).join("");
    $$("[data-rm]", tray).forEach((b) => b.onclick = () => { C.attachments.splice(+b.dataset.rm, 1); drawAttachments(C); C.syncComposer && C.syncComposer(); });
  }
  async function sendMessage(C, { regenerate = false, quietAck = false } = {}) {
    const input = $("#msg-input", C.entry.view);
    const text = regenerate ? "" : input.value.trim();
    if (!regenerate && !text && !C.attachments.length) return;
    if (!C.model) { toast(tr("Нет доступных моделей", "No models available"), "err"); return; }
    haptic("medium");
    const attachments = regenerate ? [] : C.attachments.slice();
    C.error = "";
    const replaced = [];
    if (regenerate) {
      while (C.messages.length && C.messages[C.messages.length - 1].role === "assistant") replaced.unshift(C.messages.pop());
    } else {
      C.messages.push({ role: "user", content: text, attachments: attachments.map((a) => ({ name: a.name, isImg: a.isImg, data: a.isImg ? a.data : "" })) });
      input.value = ""; input.style.height = "auto"; C.attachments = []; drawAttachments(C);
    }
    C.streaming = { text: "", reasoning: "", sources: null };
    drawConversation(C, true);
    try {
      const data = await api("chats.send", { chat_id: C.temp ? "" : (C.id || ""), text, attachments, model: C.model, web_search: C.web, thinking: C.think, effort: C.effort || "",
        temp: C.temp, meme_id: C.memeId || "", regenerate, history: C.temp ? C.tempHistory : undefined, quiet_ack: quietAck });
      if (!C.temp && !C.id) { C.id = data.chat_id; S.chatsRev = ""; }
      if (C.temp && text) C.tempHistory.push({ role: "user", content: text });
      attachJob(C, data.job, 0, false);
      if (data.break) breakReminder(data.break.text);
    } catch (error) {
      const quiet = error.code === "quiet_hours";
      C.streaming = null; C.error = quiet ? "" : error.message;
      if (!regenerate) {
        const last = C.messages.pop(); input.value = last ? last.content : "";
        if (quiet && attachments.length) { C.attachments = attachments; drawAttachments(C); }
      } else if (quiet) C.messages.push(...replaced);     // «Позже»: the old answer stays on screen
      drawConversation(C, true); C.syncComposer && C.syncComposer();
      // «Время и фокус»: quiet hours are a reminder, not a lock — «Продолжить» sends it after all.
      if (quiet && await confirmDialog(error.message, { title: tr("Тихие часы", "Quiet hours"), ok: tr("Продолжить", "Continue") }))
        sendMessage(C, { regenerate, quietAck: true });
    }
  }
  /* Round 29: edit one of your messages; everything after it is answered again (chats.edit). */
  async function editUserMessage(C, index) {
    const m = C.messages[index];
    if (!m || C.job) return;
    const text = await promptSheet(tr("Изменить сообщение", "Edit message"), { value: m.content || "", multiline: true, max: 100000, ok: tr("Отправить", "Send") });
    if (!text || text === m.content) return;
    // Round 35 (branches): nothing is lost any more — the old message and its answers stay as version «1/2».
    haptic("medium");
    try {
      const data = await api("chats.edit", { id: C.id, index: m.i, text, model: C.model, web_search: C.web, thinking: C.think });
      C.messages = C.messages.slice(0, index).concat([Object.assign({}, m, { content: text })]);
      C.error = ""; C.streaming = { text: "", reasoning: "", sources: null };
      drawConversation(C, true);
      attachJob(C, data.job, 0, false);
    } catch (error) { toast(error.message, "err"); }
  }
  function breakReminder(text) {
    const act = (action) => api("focus.break", { action }).catch(() => {});
    openSheet({ title: tr("Пора сделать перерыв?", "Time for a break?"),
      body: `<p class="section-note" style="margin:0 20px 8px;font-size:15px;color:var(--text)">${esc(text)}</p>`,
      actions: [{ label: tr("Скрыть", "Dismiss"), kind: "secondary", run: () => act("dismiss") },
                { label: tr("Через 10 мин", "In 10 min"), kind: "secondary", run: () => act("snooze") },
                { label: tr("Сделаю перерыв", "Take a break"), run: () => act("break") }] });   // closing = the server's 10-minute snooze
  }
  function attachJob(C, job, from, resumed) {
    C.job = job; C.control = { cancelled: false, abort: new AbortController() };
    if (!C.streaming) C.streaming = { text: "", reasoning: "", sources: null };
    if (resumed) drawConversation(C, true);
    C.syncComposer && C.syncComposer();
    drawToolsRow(C);
    if (S.tg && S.tg.enableClosingConfirmation) try { S.tg.enableClosingConfirmation(); } catch (e) { /* old */ }
    followJob(job, from, (event) => onChatEvent(C, event), C.control).finally(() => {
      if (S.tg && S.tg.disableClosingConfirmation) try { S.tg.disableClosingConfirmation(); } catch (e) { /* old */ }
    });
  }
  /* r30: a new chat (or a new answer) shows up in the list next to it at once — in two panes the list stayed
     «Пока пусто» until the tab was reopened. */
  function syncChatList() {
    loadChats(true).then((changed) => { if (changed && S.tab === "chats" && $("#chat-list")) { drawChatChips(); drawChatList(); } });
  }
  function askUserForm(C, event) {
    if (!window.MemuAskForm || !event.form) return;
    MemuAskForm.setLang(() => S.lang);
    const rec = { id: String(event.id), form: event.form, status: "pending", answers: null };
    (C.streaming.askForms = C.streaming.askForms || []).push(rec);
    MemuAskForm.mount(rec, { compact: true, rerender: () => renderStreaming(C), onSubmit: async (answers, skipped) => {
      const data = await api("jobs.answer", { job: C.job, id: rec.id, answers: answers || {}, skip: !!skipped });
      if (data && data.errors && data.errors.length) return { errors: data.errors };
      haptic("success");
      return data;
    } });
    haptic("select");
    renderStreaming(C);
  }
  function onChatEvent(C, event) {
    if (!C.streaming && !["done", "lost", "title", "settled"].includes(event.type)) C.streaming = { text: "", reasoning: "", sources: null };
    switch (event.type) {
      case "start": if (event.chat_id && !C.temp) { C.id = event.chat_id; S.openChatId = C.id; } break;
      case "delta": C.streaming.text += event.text || ""; renderStreaming(C); break;
      case "reasoning": C.streaming.reasoning += event.text || ""; renderStreaming(C); break;
      case "sources": C.streaming.sources = event.sources || null; renderStreaming(C); break;
      case "media": C.streaming.media = event; renderStreaming(C); break;   // Round 29 (r29c-media)
      // Round 35 (askuser): the model asks with a small form; the answer continues the same answer.
      case "ask_user": askUserForm(C, event); break;
      case "ask_user_done": {
        const rec = (C.streaming.askForms || []).find((r) => r.id === event.id);
        if (rec) { rec.status = event.status || "answered"; rec.answers = event.answers || null; if (window.MemuAskForm) MemuAskForm.update(rec); }
        renderStreaming(C); break;
      }
      case "title": C.title = event.title; updateStackTop(C.entry, { title: event.title }); if (!C.job) syncChatList(); break;
      case "memory": toast(tr("Запомнил: ", "Remembered: ") + String((event.facts || [])[0] || "").slice(0, 50), "ok"); break;
      case "message": C.messages.push(event.message); if (C.temp) C.tempHistory.push({ role: "assistant", content: event.message.content }); break;
      case "error": C.error = event.message; break;
      case "lost":
      case "done":
        C.streaming = null; C.job = null;
        drawConversation(C); scrollConvo(C, true); C.syncComposer && C.syncComposer(); drawToolsRow(C);
        haptic(C.error ? "error" : "success");
        if (!C.temp && C.id && !$("#chat-more")) {
          updateStackTop(C.entry, { actions: `<button class="icon-btn" id="chat-more" aria-label="${esc(tr("Действия", "Actions"))}">${icon("more")}</button>` });
          const more = $("#chat-more"); if (more) more.onclick = () => chatMenu(C.id, (what, chat) => { if (what === "deleted") closeStack(C.entry); else if (chat) updateStackTop(C.entry, { title: chat.title }); });
        }
        if (!C.temp && C.id) syncChatList();
        if (event.type === "lost" && C.id) loadConversation(C);
        // Round 29: the stored indexes make your new messages editable at once.
        else if (!C.temp && C.id && C.messages.some((m) => typeof m.i !== "number")) loadConversation(C);
        break;
      default: break;
    }
  }
  async function stopJob(C) {
    if (!C.job) return;
    haptic("rigid");
    try { await api("jobs.stop", { job: C.job }); } catch (error) { toast(error.message, "err"); }
  }

  /* ================================================================ MEMES TAB */
  function renderMemesTab(view) {
    setTop(tr("Мемесы", "Agents"), `<button class="icon-btn" id="meme-add" aria-label="${esc(tr("Новый мемес", "New agent"))}">${icon("plus")}</button>`);
    $("#meme-add").onclick = createMemeChoice;
    const counts = {};
    S.chats.forEach((c) => { if (c.meme_id) counts[c.meme_id] = (counts[c.meme_id] || 0) + 1; });
    view.innerHTML = `<p class="section-note" style="margin:2px 20px 12px">${esc(tr("Мемес — ваш агент: в его чатах ИИ всегда следует заданным инструкциям.", "An agent keeps its instructions in every chat you start with it."))}</p>
      <div class="meme-grid">${S.memes.map((m) => `<button class="meme-card" data-meme="${esc(m.id)}" style="--glow:${memeGlow(m)}">${memeAvatar(m)}<h4>${esc(m.name || tr("Без имени", "Untitled"))}</h4><p>${esc(m.description || m.instructions || "")}</p>
        <div class="meme-meta">${icon("chat")}${counts[m.id] || 0}${m.web_search ? icon("globe") : ""}${m.thinking ? icon("brain") : ""}${m.model ? `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">· ${esc(modelName(m.model))}</span>` : ""}</div></button>`).join("")}
        <button class="meme-card meme-new" id="meme-new">${icon("plus")}<span style="font-weight:650">${esc(tr("Новый мемес", "New agent"))}</span></button></div>`;
    $$("[data-meme]", view).forEach((card) => { card.onclick = () => memeDetail(card.dataset.meme); longPress(card, () => memeMenu(card.dataset.meme)); });
    $("#meme-new").onclick = createMemeChoice;
    if (!S.chats.length) loadChats(true);
  }
  function memeMenu(id) {
    const meme = memeById(id); if (!meme) return;
    actionSheet(meme.name, [
      { label: tr("Новый чат", "New chat"), icon: "compose", run: () => openChat(null, { memeId: id }) },
      { label: tr("Изменить", "Edit"), icon: "edit", run: () => editMeme(meme) },
      { label: tr("Дублировать", "Duplicate"), icon: "copy", run: () => saveMeme(Object.assign({}, meme, { id: "", name: meme.name + tr(" (копия)", " (copy)") })) },
      { label: tr("Удалить", "Delete"), icon: "trash", danger: true, run: () => deleteMeme(meme) },
    ]);
  }
  function memeDetail(id) {
    const meme = memeById(id); if (!meme) return;
    const chats = S.chats.filter((c) => c.meme_id === id);
    const caps = Object.assign({}, ...MEME_CAPS.map(([k]) => ({ [k]: true })), meme.caps || {});
    openSheet({ title: "", full: true,
      body: `<div class="meme-hero">${memeAvatar(meme)}<div style="min-width:0"><div style="font-size:21px;font-weight:700;letter-spacing:-.02em">${esc(meme.name)}</div><div class="cell-sub">${esc(meme.description || "")}</div></div></div>
        <div style="display:flex;gap:10px;margin:0 16px 6px"><button class="btn block" id="md-chat">${icon("compose")}${esc(tr("Новый чат", "New chat"))}</button><button class="btn secondary" id="md-edit" aria-label="${esc(tr("Изменить", "Edit"))}">${icon("edit")}</button><button class="btn secondary" id="md-more" aria-label="…">${icon("more")}</button></div>
        <div class="section-title">${esc(tr("Настройки", "Settings"))}</div>
        <div class="group flat"><div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Модель", "Model"))}</div></div><div class="cell-value">${esc(meme.model ? modelName(meme.model) : tr("Текущая", "Current"))}</div></div>
          <div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Веб-поиск · Размышление", "Web search · Thinking"))}</div></div><div class="cell-value">${meme.web_search ? "✓" : "—"} · ${meme.thinking ? "✓" : "—"}</div></div>
          <div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Строго в рамках задачи", "Stay in scope"))}</div></div><div class="cell-value">${meme.strict ? "✓" : "—"}</div></div></div>
        <div class="section-title">${esc(tr("Возможности", "Capabilities"))}</div>
        <div class="cap-grid">${MEME_CAPS.map(([k, ru, en]) => `<div class="cap ${caps[k] ? "on" : ""}">${icon(caps[k] ? "circleCheck" : "dots")}${esc(tr(ru, en))}</div>`).join("")}</div>
        ${meme.instructions ? `<div class="section-title">${esc(tr("Инструкции", "Instructions"))}</div><div class="group"><div class="cell" style="display:block;white-space:pre-wrap;font-size:14px;line-height:1.45">${esc(meme.instructions)}</div></div>` : ""}
        <div class="section-title">${esc(tr("Чаты", "Chats"))} · ${chats.length}</div>
        <div class="group">${chats.map((c) => `<button class="cell flat" data-chat-open="${esc(c.id)}"><div class="cell-body"><div class="cell-title">${esc(c.title || tr("Чат", "Chat"))}</div><div class="cell-sub">${esc(c.snippet || "")}</div></div><span class="chev">${icon("chev")}</span></button>`).join("") || `<div class="cell flat"><div class="cell-sub">${esc(tr("Чатов пока нет", "No chats yet"))}</div></div>`}</div>`,
      onMount: (el, handle) => {
        $("#md-chat", el).onclick = () => { handle.close(true); openChat(null, { memeId: id }); };
        $("#md-edit", el).onclick = () => { handle.close(true); editMeme(meme); };
        $("#md-more", el).onclick = () => memeMenu(id);
        $$("[data-chat-open]", el).forEach((b) => b.onclick = () => { handle.close(true); openChat(b.dataset.chatOpen); });
      } });
  }
  function createMemeChoice() {
    actionSheet(tr("Новый мемес", "New agent"), [
      { label: tr("Настроить вручную", "Set up manually"), sub: tr("Имя, инструкции, модель и возможности", "Name, instructions, model and capabilities"), icon: "edit", run: () => editMeme(null) },
      { label: tr("Создать с помощью ИИ", "Create with AI"), sub: tr("Опишите задачу — ИИ соберёт мемеса", "Describe the task — the AI drafts it"), icon: "sparkle", run: generateMeme },
    ]);
  }
  async function generateMeme() {
    const request = await promptSheet(tr("Мемес с помощью ИИ", "Agent with AI"), { multiline: true, max: 4000, ok: tr("Создать", "Generate"),
      placeholder: tr("Например: агент, который редактирует мои тексты и отвечает таблицей с правками", "E.g. an agent that edits my texts and answers with a table of fixes") });
    if (!request) return;
    toast(tr("ИИ собирает мемеса…", "The AI is drafting the agent…"));
    try { const data = await api("memes.generate", { request, model: S.settings.default_model || "" }); haptic("success"); editMeme(data.meme, { draft: true }); }
    catch (error) { toast(error.message, "err"); }
  }
  function editMeme(meme, { draft = false } = {}) {
    const m = JSON.parse(JSON.stringify(meme || { id: "", name: "", description: "", instructions: "", model: "", web_search: false, thinking: false, strict: true,
      avatar: { type: "art", kind: MEME_KINDS[Math.floor(Math.random() * 12)], palette: Math.floor(Math.random() * 12), seed: 1 + Math.floor(Math.random() * 9999) } }));
    m.caps = Object.assign({}, ...MEME_CAPS.map(([k]) => ({ [k]: true })), m.caps || {});
    const isNew = !meme || draft || !meme.id;
    openSheet({ title: isNew ? tr("Новый мемес", "New agent") : tr("Изменить мемеса", "Edit agent"), full: true,
      body: `<div class="meme-hero"><div id="me-av">${memeAvatar(m)}</div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn small secondary" id="me-shuffle">${icon("palette")}${esc(tr("Рисунок", "Artwork"))}</button><button class="btn small secondary" id="me-emoji">😀 ${esc(tr("Эмодзи", "Emoji"))}</button></div></div>
        <div class="field"><label>${esc(tr("Имя", "Name"))}</label><input id="me-name" maxlength="60" value="${esc(m.name)}" placeholder="${esc(tr("Например, Редактор", "E.g. Editor"))}"></div>
        <div class="field"><label>${esc(tr("Описание", "Description"))}</label><input id="me-desc" maxlength="160" value="${esc(m.description)}"></div>
        <div class="field"><label>${esc(tr("Инструкции", "Instructions"))}</label><textarea id="me-inst" rows="7" maxlength="20000">${esc(m.instructions)}</textarea></div>
        <div class="group flat"><button class="cell flat" id="me-model"><div class="cell-body"><div class="cell-title">${esc(tr("Модель", "Model"))}</div></div><div class="cell-value" id="me-model-v">${esc(m.model ? modelName(m.model) : tr("Текущая", "Current"))}</div><span class="chev">${icon("chev")}</span></button>
          ${[["web_search", tr("Веб-поиск", "Web search")], ["thinking", tr("Размышление", "Thinking")], ["strict", tr("Строго в рамках задачи", "Stay in scope")]].map(([k, label]) =>
            `<label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(label)}</div></div><span class="switch"><input type="checkbox" data-flag="${k}" ${m[k] ? "checked" : ""}><span></span></span></label>`).join("")}</div>
        <div class="section-title">${esc(tr("Возможности", "Capabilities"))}</div>
        <div class="group flat">${MEME_CAPS.map(([k, ru, en]) => `<label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr(ru, en))}</div></div><span class="switch"><input type="checkbox" data-cap="${k}" ${m.caps[k] ? "checked" : ""}><span></span></span></label>`).join("")}</div>`,
      actions: [{ label: tr("Сохранить", "Save"), run: async () => {
        m.name = $("#me-name").value.trim(); m.description = $("#me-desc").value.trim(); m.instructions = $("#me-inst").value;
        $$("[data-flag]").forEach((i) => m[i.dataset.flag] = i.checked); $$("[data-cap]").forEach((i) => m.caps[i.dataset.cap] = i.checked);
        if (!m.name) { toast(tr("Дайте мемесу имя", "Give the agent a name"), "err"); return true; }
        return !(await saveMeme(m));
      } }],
      onMount: (el) => {
        const redraw = () => { $("#me-av", el).innerHTML = memeAvatar(m); };
        $("#me-shuffle", el).onclick = () => { m.avatar = { type: "art", kind: MEME_KINDS[Math.floor(Math.random() * 12)], palette: Math.floor(Math.random() * 12), seed: 1 + Math.floor(Math.random() * 9999) }; haptic("select"); redraw(); };
        $("#me-emoji", el).onclick = () => openSheet({ title: tr("Эмодзи", "Emoji"), body: `<div class="emoji-grid">${EMOJI.map((e) => `<button data-e="${e}">${e}</button>`).join("")}</div>`,
          onMount: (sheet, h) => $$("[data-e]", sheet).forEach((b) => b.onclick = () => { m.avatar = { type: "emoji", emoji: b.dataset.e }; redraw(); h.close(true); }) });
        $("#me-model", el).onclick = () => pickModel(m.model, (id) => { m.model = id; $("#me-model-v", el).textContent = id ? modelName(id) : tr("Текущая", "Current"); }, { allowEmpty: true, emptyLabel: tr("Текущая модель чата", "The chat's current model") });
      } });
  }
  async function saveMeme(m) {
    try {
      const data = await api("memes.save", { meme: m });
      const i = S.memes.findIndex((x) => x.id === data.meme.id);
      if (i >= 0) S.memes[i] = data.meme; else S.memes.push(data.meme);
      toast(tr("Мемес сохранён", "Agent saved"), "ok");
      if (S.tab === "memes" && !S.stack.length) renderMemesTab(rootView());
      return true;
    } catch (error) { toast(error.message, "err"); return false; }
  }
  async function deleteMeme(meme) {
    if (!(await confirmDialog(tr("Чаты мемеса останутся.", "The agent's chats stay."), { title: tr("Удалить мемеса?", "Delete agent?"), ok: tr("Удалить", "Delete"), danger: true }))) return;
    try { await api("memes.delete", { id: meme.id }); S.memes = S.memes.filter((x) => x.id !== meme.id); S.chats.forEach((c) => { if (c.meme_id === meme.id) c.meme_id = ""; }); toast(tr("Мемес удалён", "Agent deleted"), "ok"); if (S.tab === "memes") renderMemesTab(rootView()); }
    catch (error) { toast(error.message, "err"); }
  }

  /* ================================================================ MEMUCODE TAB
     Round 29: the whole MemuCode part lives in code.js (new sessions, slash commands with auto-complete,
     phone-hosted sessions, files, approvals). These delegates keep the old entry points. */
  async function loadCodeSessions() {
    if (!window.MemuTgCode) return false;
    try { return await window.MemuTgCode.loadSessions(); } catch (error) { return false; }
  }
  function renderCodeTab(view) {
    if (window.MemuTgCode) { window.MemuTgCode.renderTab(view); return; }
    setTop("MemuCode");
    view.innerHTML = `<div class="empty"><p>${esc(tr("Не удалось загрузить MemuCode", "MemuCode failed to load"))}</p></div>`;
  }
  function openCodeSession(sessionId, options) { if (window.MemuTgCode) return window.MemuTgCode.openSession(sessionId, options); return null; }

  /* ================================================================ MODULES TAB */
  /* Round 24: «Панели» — the same server data as the desktop cards (tgapp/panels.js). */
  function renderPanelsTab(view) {
    if (!window.MemuTgPanels) { view.innerHTML = `<div class="empty"><p>${esc(tr("Не удалось загрузить панели", "Panels failed to load"))}</p></div>`; return; }
    window.MemuTgPanels.render(view, { api, esc, tr, toast, haptic, setTop, icon, state: S, timers: S.timers });
  }
  function renderModulesTab(view) {
    setTop(tr("Модули", "Modules"));
    view.innerHTML = `<div id="mod-list"><div class="empty"><div class="typing"><i></i><i></i><i></i></div></div></div>`;
    api("modules.list").then((data) => { S.modules = data.modules || []; drawModules(view); }).catch((e) => { $("#mod-list", view).innerHTML = `<div class="empty"><p>${esc(e.message)}</p></div>`; });
  }
  function moduleTitle(m) { return S.lang === "en" ? (m.title_en || m.title) : m.title; }
  function drawModules(view) {
    const box = $("#mod-list", view); if (!box) return;
    if (!S.modules.length) { box.innerHTML = `<div class="empty"><div class="empty-art">${icon("modules")}</div><h3>${esc(tr("Модулей нет", "No modules"))}</h3></div>`; return; }
    box.innerHTML = `<p class="section-note" style="margin:2px 20px 12px">${esc(tr("Модули — небольшие приложения MemuAI. Здесь они открываются прямо в Telegram; действия, которым нужен компьютер (файлы, медиа, фон), работают только там.", "Modules are small MemuAI apps. They open right here; actions that need the computer (files, media, background) work only there."))}</p>
      <div class="group">${S.modules.map((m) => `<div class="cell module-cell ${m.enabled ? "" : "off"}" data-mod="${esc(m.id)}"><div class="avatar" style="--av1:${esc(m.accent || "var(--accent)")};--av2:var(--accent2)">${icon(MODULE_ICON[m.icon] || "modules")}</div>
        <div class="cell-body"><div class="cell-title">${esc(moduleTitle(m))}</div><div class="cell-sub">${esc(S.lang === "en" ? (m.summary_en || m.summary) : m.summary)}</div></div>
        <span class="switch" data-stop><input type="checkbox" data-toggle="${esc(m.id)}" ${m.enabled ? "checked" : ""} aria-label="${esc(tr("Включён", "Enabled"))}"><span></span></span></div>`).join("")}</div>`;
    $$("[data-mod]", box).forEach((c) => c.onclick = (e) => { if (e.target.closest("[data-stop]")) return; openModule(c.dataset.mod); });
    $$("[data-toggle]", box).forEach((input) => input.onchange = async () => {
      haptic("select");
      try { const data = await api("modules.toggle", { id: input.dataset.toggle, enabled: input.checked }); const m = S.modules.find((x) => x.id === input.dataset.toggle); if (m) m.enabled = data.enabled; const cell = input.closest(".module-cell"); if (cell) cell.classList.toggle("off", !data.enabled); }
      catch (error) { input.checked = !input.checked; toast(error.message, "err"); }
    });
  }
  async function openModule(id) {
    const m = S.modules.find((x) => x.id === id); if (!m) return;
    primaryStack();
    if (!m.enabled) { toast(tr("Сначала включите модуль", "Switch the module on first"), "err"); return; }
    let opened;
    try { opened = await api("modules.open", { id }); } catch (error) { toast(error.message, "err"); return; }
    const streams = new Map();
    let frame = null;
    const handler = async (event) => {
      if (!frame || event.source !== frame.contentWindow) return;
      const data = event.data || {};
      if (data.type !== "memu-module") return;
      const reply = (result, error) => { try { frame.contentWindow.postMessage({ type: "memu-module-result", id: data.id, result: result === undefined ? null : result, error: error ? String(error) : "" }, "*"); } catch (e) { /* closed */ } };
      const payload = data.payload || {};
      try {
        switch (data.action) {
          case "env": return reply({ lang: S.lang, theme: currentScheme(), accent_name: (S.appearance || {}).accent || "terracotta", surface: "", density: "normal", font_scale: (S.appearance || {}).font_scale || 100, host: "telegram" });
          case "log": return reply({ ok: true });
          case "resize": return reply({ ok: true, mobile: true });
          case "close": setTimeout(() => closeStack(entry), 0); return reply({ ok: true });
          case "clipboard": await copyText(String(payload.text || "")); if (payload.notify !== false) toast(tr("Скопировано", "Copied"), "ok"); return reply({ ok: true });
          case "notify": toast(String(payload.title || payload.text || "").slice(0, 120)); return reply({ ok: true });
          case "cancel": { const s = streams.get(String(payload.id || "")); if (s) { s.cancelled = true; api("jobs.stop", { job: s.job }).catch(() => {}); } return reply({ ok: true, cancelled: !!s }); }
          case "stream": {
            const started = await api("modules.stream", { id, payload });
            const control = { cancelled: false, job: started.job };
            streams.set(data.id, control);
            const send = (message) => { try { frame.contentWindow.postMessage(Object.assign({ type: "memu-module-event", id: data.id }, message), "*"); } catch (e) { /* closed */ } };
            send({ event: "status", status: "queued" });
            let result = null, error = "";
            await followJob(started.job, 0, (ev) => {
              if (ev.type === "delta") send({ event: "delta", delta: ev.text });
              else if (ev.type === "result") result = ev.result;
              else if (ev.type === "error") error = ev.message;
            }, control);
            streams.delete(data.id);
            if (error || !result) return reply(null, error || tr("Поток прервался", "The stream ended"));
            send({ event: "done", result });
            return reply(result);
          }
          default: {
            const out = await api("modules.bridge", { id, action: data.action, payload });
            return reply(out.result);
          }
        }
      } catch (error) { reply(null, error.message || String(error)); }
    };
    const entry = openStack({ title: moduleTitle(m), sub: tr("Модуль", "Module"),
      build: (view) => {
        view.innerHTML = `${(m.desktop_only || []).length ? `<div class="module-note">${esc(tr("Часть возможностей этого модуля работает только на компьютере", "Some features of this module work only on the computer"))}</div>` : ""}<iframe class="module-frame" sandbox="allow-scripts allow-forms allow-modals" referrerpolicy="no-referrer" title="${esc(moduleTitle(m))}"></iframe>`;
        frame = $("iframe", view);
        window.addEventListener("message", handler);
        frame.src = opened.url;
      },
      onClose: () => { window.removeEventListener("message", handler); streams.forEach((s) => { s.cancelled = true; api("jobs.stop", { job: s.job }).catch(() => {}); }); },
    });
  }

  /* ================================================================ SETTINGS TAB */
  const SET = {
    toggle(key, title, sub = "") {
      return `<label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(title)}</div>${sub ? `<div class="cell-sub">${esc(sub)}</div>` : ""}</div><span class="switch"><input type="checkbox" data-set-bool="${key}" ${S.settings[key] ? "checked" : ""}><span></span></span></label>`;
    },
    choice(key, title, options, sub = "") {
      const current = options.find((o) => o[0] === S.settings[key]);
      return `<button class="cell flat" data-set-enum="${key}"><div class="cell-body"><div class="cell-title">${esc(title)}</div>${sub ? `<div class="cell-sub">${esc(sub)}</div>` : ""}</div><div class="cell-value">${esc(current ? current[1] : String(S.settings[key] ?? ""))}</div><span class="chev">${icon("chev")}</span></button>`;
    },
    range(key, title, min, max, step = 1, unit = "") {
      return `<div class="cell flat" style="display:block"><div class="cell-title">${esc(title)}</div><div class="slider-row" style="padding:6px 0 0"><input type="range" min="${min}" max="${max}" step="${step}" value="${+S.settings[key] || min}" data-set-int="${key}"><output>${+S.settings[key] || min}${unit}</output></div></div>`;
    },
    model(key, title, emptyLabel = "") {
      return `<button class="cell flat" data-set-model="${key}" data-empty="${esc(emptyLabel)}"><div class="cell-body"><div class="cell-title">${esc(title)}</div></div><div class="cell-value">${esc(S.settings[key] ? modelName(S.settings[key]) : (emptyLabel || "—"))}</div><span class="chev">${icon("chev")}</span></button>`;
    },
    nav(id, iconName, color, title, value = "") {
      return `<button class="cell" data-nav="${id}"><div class="cell-icon" style="background:${color}">${icon(iconName)}</div><div class="cell-body"><div class="cell-title">${esc(title)}</div></div>${value ? `<div class="cell-value">${esc(value)}</div>` : ""}<span class="chev">${icon("chev")}</span></button>`;
    },
  };
  const ENUMS = () => ({
    language: [["ru", "Русский"], ["en", "English"]],
    theme: [["dark", tr("Тёмная", "Dark")], ["light", tr("Светлая", "Light")]],
    app_font: [["serif", tr("С засечками", "Serif")], ["sans", tr("Без засечек", "Sans")], ["mono", tr("Моноширинный", "Mono")], ["system", tr("Системный", "System")]],
    // Round 33: the same scale as MemuAI (default for every model; each one gets its nearest supported level).
    default_effort: [["auto", tr("Авто", "Auto")], ["off", tr("Выкл", "Off")], ["minimal", tr("Минимум", "Minimal")], ["low", tr("Низкое", "Low")], ["medium", tr("Среднее", "Medium")], ["high", tr("Высокое", "High")], ["xhigh", tr("Очень высокое", "Extra high")], ["max", tr("Максимум", "Max")]],
    chat_titles: [["ai", tr("Придумывает ИИ", "AI writes them")], ["off", tr("Начало сообщения", "First words")]],
    ai_personality: [["friendly", tr("Дружелюбная", "Friendly")], ["pragmatic", tr("Прагматичная", "Pragmatic")]],
    web_search_mode: [["auto", tr("Авто", "Auto")], ["builtin", tr("Встроенный у модели", "Model's own")], ["custom", tr("Свой источник", "Custom source")]],
    image_send_mode: [["ask", tr("Спрашивать", "Ask")], ["direct", tr("Отправлять модели", "Send to the model")], ["fallback", tr("Описание другой моделью", "Describe with another model")]],
    notify_sound_style: [["gentle", tr("Мягкий", "Gentle")], ["crystal", tr("Кристалл", "Crystal")], ["pop", "Pop"], ["warm", tr("Тёплый аккорд", "Warm chord")]],
    telegram_notify_mode: [["off", tr("Выключены", "Off")], ["silent", tr("Без звука", "Silent")], ["sound", tr("Со звуком", "With sound")]],
    telegram_language: [["ru", "Русский"], ["en", "English"]],
    ui_design: ((S.themes && S.themes.designs) || []).map((d) => [d.id, d.name]),
    surface_dark: ((S.themes && S.themes.variants && S.themes.variants.dark) || []).map((v) => [v.id, v.name]),
    surface_light: ((S.themes && S.themes.variants && S.themes.variants.light) || []).map((v) => [v.id, v.name]),
  });
  async function saveSetting(values, { quiet = false } = {}) {
    const before = Object.assign({}, S.settings);
    Object.assign(S.settings, values);
    try {
      const data = await api("settings.save", { values });
      S.settings = Object.assign(S.settings, data.settings || {});
      if (data.appearance) { S.appearance = data.appearance; applyAppearance(); }
      if (values.language && values.language !== S.lang) { S.lang = values.language; await reloadLocalized(); renderTabbar(); switchTab("settings", { force: true }); }
      if (!quiet) haptic("success");
      return true;
    } catch (error) { S.settings = before; toast(error.message, "err"); return false; }
  }
  function renderSettingsTab(view) {
    setTop(tr("Настройки", "Settings"));
    const profile = S.profiles.find((p) => p.id === S.profileId) || S.profiles[0] || { name: "" };
    // Round 34: the card read «По умолчанию / Профиль: По умолчанию» — the person's own name comes before the
    // profile's, and the profile line is not repeated when it is the same word.
    const own = S.settings.profile_name && S.settings.profile_name !== profile.name ? S.settings.profile_name : "";
    const name = own || (S.user && S.user.first_name) || profile.name || "MemuAI";
    const profileLine = profile.name && profile.name !== name ? `${tr("Профиль", "Profile")}: ${profile.name}` : tr("Профиль MemuAI", "MemuAI profile");
    const [a, b] = initialsGradient(name);
    view.innerHTML = `<div class="profile-card"><div class="avatar" style="--av1:${a};--av2:${b}">${esc(name.charAt(0).toUpperCase())}</div><div style="min-width:0;flex:1"><div class="pc-name">${esc(name)}</div>
        <div class="pc-sub">${esc(profileLine)}${S.user && S.user.username ? " · @" + esc(S.user.username) : ""}</div></div>
        ${S.profiles.length > 1 ? `<button class="btn small secondary" id="profile-switch">${esc(tr("Сменить", "Switch"))}</button>` : ""}</div>
      <div class="section-title">${esc(tr("Оформление", "Appearance"))}</div>
      <div class="group">${SET.nav("look", "palette", "linear-gradient(140deg,#a855f7,#ec4899)", tr("Тема и цвета", "Theme and colours"), (ENUMS().ui_design.find((d) => d[0] === S.settings.ui_design) || ["", ""])[1])}
        ${SET.nav("lang", "globe", "#3b82f6", tr("Язык", "Language"), S.settings.language === "en" ? "English" : "Русский")}</div>
      <div class="section-title">${esc(tr("ИИ", "AI"))}</div>
      <div class="group">${SET.nav("models", "model", "#10b981", tr("Модели и ответы", "Models and answers"), S.settings.default_model ? modelName(S.settings.default_model) : "")}
        ${SET.nav("persona", "sparkle", "#f97316", tr("Личность и инструкции", "Personality and instructions"))}
        ${SET.nav("memory", "memory", "#8b5cf6", tr("Память", "Memory"), String((S.memory.facts || []).length))}
        ${SET.nav("search", "search", "#0ea5e9", tr("Поиск и исследования", "Search and research"))}
        ${SET.nav("context", "list", "#64748b", tr("Контекст", "Context"))}</div>
      <div class="section-title">${esc(tr("Уведомления", "Notifications"))}</div>
      <div class="group">${SET.nav("telegram", "telegram", "#229ed9", "Telegram", (ENUMS().telegram_notify_mode.find((x) => x[0] === S.settings.telegram_notify_mode) || ["", ""])[1])}
        ${SET.nav("desktop", "bell", "#ef4444", tr("На компьютере", "On the computer"))}</div>
      ${kit.hooks.settingsRows.map((fn) => { try { return fn(SET) || ""; } catch (e) { return ""; } }).join("")}
      <div class="section-title">${esc(tr("Ещё", "More"))}</div>
      <div class="group">${SET.nav("general", "settings", "#6b7280", tr("Общие", "General"))}
        ${SET.nav("about", "info", "#14b8a6", tr("О Mini App", "About the Mini App"))}</div>
      <div class="about">MemuAI ${esc(S.boot.version || "")} · Telegram Mini App<br>${esc(tr("Настройки сохраняются в MemuAI на компьютере.", "Settings are stored in MemuAI on the computer."))}</div>`;
    $$("[data-nav]", view).forEach((b) => b.onclick = () => openSettingsPage(b.dataset.nav));
    const sw = $("#profile-switch", view); if (sw) sw.onclick = switchProfile;
    refreshSettings().then((changed) => { if (changed && S.tab === "settings" && !S.stack.length) renderSettingsTab(rootView()); });
  }
  /* Round 26: values changed on the computer or in the bot while the Mini App stayed open
     (Telegram keeps it alive in the background) were shown stale until a restart. */
  let settingsFetchedAt = 0;
  async function refreshSettings() {
    if (Date.now() - settingsFetchedAt < 3000) return false;
    settingsFetchedAt = Date.now();
    let data;
    try { data = await api("settings.get"); } catch (error) { return false; }
    const before = JSON.stringify([S.settings, S.appearance, S.models]);
    S.settings = data.settings || S.settings; S.models = data.models || S.models; S.themes = data.themes || S.themes;
    if (data.appearance) S.appearance = data.appearance;
    if (JSON.stringify([S.settings, S.appearance, S.models]) === before) return false;
    applyAppearance();
    if ((S.settings.language === "en" || S.settings.language === "ru") && S.settings.language !== S.lang) { S.lang = S.settings.language; await reloadLocalized(); renderTabbar(); }
    return true;
  }
  /* Names the server localizes (the unnamed default profile, theme catalog) follow a language switch. */
  async function reloadLocalized() {
    try { const boot = await api("bootstrap"); S.profiles = boot.profiles || S.profiles; S.themes = boot.themes || S.themes; } catch (error) { /* keep the old names */ }
  }
  function settingsPage(title, html, after) {
    return openStack({ title, build: (view) => { view.style.overflowY = "auto"; view.innerHTML = `<div style="padding:8px 0 40px">${html}</div>`; const redraw = () => { const fresh = settingsPageHtml[view.dataset.page]; if (fresh) { view.firstElementChild.innerHTML = fresh(); bindSettingControls(view, redraw); } }; bindSettingControls(view, redraw); if (after) after(view); } });
  }
  const settingsPageHtml = {};
  function bindSettingControls(view, redraw) {
    $$("[data-set-bool]", view).forEach((input) => input.onchange = () => { haptic("select"); saveSetting({ [input.dataset.setBool]: input.checked }, { quiet: true }).then((ok) => { if (!ok) input.checked = !input.checked; }); });
    $$("[data-set-int]", view).forEach((input) => {
      const out = input.parentElement.querySelector("output");
      const unit = (out.textContent.match(/[^\d]+$/) || [""])[0];
      input.oninput = () => { out.textContent = input.value + unit; };
      input.onchange = () => { haptic("select"); saveSetting({ [input.dataset.setInt]: +input.value }, { quiet: true }); };
    });
    $$("[data-set-enum]", view).forEach((button) => button.onclick = () => {
      const key = button.dataset.setEnum;
      const options = ENUMS()[key] || [];
      actionSheet(button.querySelector(".cell-title").textContent, options.map(([value, label]) => ({ label, icon: value === S.settings[key] ? "check" : "circle",
        run: async () => { if (await saveSetting({ [key]: value })) { button.querySelector(".cell-value").textContent = label; if (redraw) redraw(); } } })));
    });
    $$("[data-set-model]", view).forEach((button) => button.onclick = () => {
      const key = button.dataset.setModel;
      pickModel(S.settings[key], async (id) => { if (await saveSetting({ [key]: id })) button.querySelector(".cell-value").textContent = id ? modelName(id) : (button.dataset.empty || "—"); },
        { allowEmpty: !!button.dataset.empty, emptyLabel: button.dataset.empty });
    });
    $$("[data-set-text]", view).forEach((button) => button.onclick = async () => {
      const key = button.dataset.setText;
      const value = await promptSheet(button.querySelector(".cell-title").textContent, { value: S.settings[key] || "", multiline: button.dataset.multi === "1", max: +button.dataset.max || 1000, ok: tr("Сохранить", "Save") });
      if (value === null) return;
      if (await saveSetting({ [key]: value })) { const v = button.querySelector(".cell-value"); if (v) v.textContent = value.slice(0, 40); }
    });
  }
  function textCell(key, title, { multi = false, max = 1000, placeholder = "" } = {}) {
    const value = String(S.settings[key] || "");
    return `<button class="cell flat" data-set-text="${key}" data-multi="${multi ? 1 : 0}" data-max="${max}"><div class="cell-body"><div class="cell-title">${esc(title)}</div>${multi ? `<div class="cell-sub">${esc(value || placeholder)}</div>` : ""}</div>${multi ? "" : `<div class="cell-value">${esc(value.slice(0, 40) || placeholder)}</div>`}<span class="chev">${icon("chev")}</span></button>`;
  }
  function openSettingsPage(id) {
    const E = ENUMS();
    if (!["lang"].includes(id)) primaryStack();
    const pages = {
      look: () => { openLookPage(); },
      lang: () => actionSheet(tr("Язык", "Language"), E.language.map(([v, l]) => ({ label: l, icon: v === S.settings.language ? "check" : "globe", run: () => saveSetting({ language: v }) }))),
      models: () => settingsPage(tr("Модели и ответы", "Models and answers"), `
        <div class="group flat" style="margin-top:8px">${SET.model("default_model", tr("Основная модель", "Default model"))}${SET.choice("default_effort", tr("Глубина размышления", "Reasoning effort"), E.default_effort)}</div>
        <div class="section-title">${esc(tr("По умолчанию в новых чатах", "Default for new chats"))}</div>
        <div class="group flat">${SET.toggle("web_search", tr("Веб-поиск", "Web search"))}${SET.toggle("thinking", tr("Размышление", "Thinking"))}${SET.toggle("prompt_caching", tr("Кэш промпта", "Prompt caching"), tr("Дешевле и быстрее у поддерживающих провайдеров", "Cheaper and faster where supported"))}</div>
        <div class="section-title">${esc(tr("Ответы", "Answers"))}</div>
        <div class="group flat">${SET.choice("chat_titles", tr("Названия чатов", "Chat titles"), E.chat_titles)}${SET.toggle("chat_titles_update", tr("Переименовывать при смене темы", "Rename when the topic changes"))}${SET.toggle("visualizations_enabled", tr("Интерактивные визуализации", "Interactive visualizations"), tr("На компьютере", "On the computer"))}</div>
        <div class="section-title">${esc(tr("Изображения", "Images"))}</div>
        <div class="group flat">${SET.choice("image_send_mode", tr("Картинки для моделей без зрения", "Images for non-vision models"), E.image_send_mode)}${SET.model("vision_fallback_model", tr("Модель для описания", "Describing model"), tr("Не выбрана", "None"))}</div>`),
      persona: () => settingsPage(tr("Личность и инструкции", "Personality and instructions"), `
        <div class="group flat" style="margin-top:8px">${SET.choice("ai_personality", tr("Личность ИИ", "AI personality"), E.ai_personality)}${textCell("profile_name", tr("Как к вам обращаться", "What to call you"), { max: 80 })}</div>
        <div class="section-title">${esc(tr("О вас", "About you"))}</div><div class="group flat">${textCell("profile_bio", tr("Описание", "Bio"), { multi: true, max: 1000, placeholder: tr("Расскажите о себе — ИИ учтёт это", "Tell the AI about yourself") })}</div>
        <div class="section-title">${esc(tr("Свои инструкции", "Custom instructions"))}</div><div class="group flat">${textCell("custom_system_prompt", tr("Инструкции для всех чатов", "Instructions for every chat"), { multi: true, max: 12000, placeholder: tr("Например: отвечай кратко, с примерами", "E.g. answer briefly, with examples") })}</div>`),
      memory: () => openMemoryPage(),
      search: () => settingsPage(tr("Поиск и исследования", "Search and research"), `
        <div class="group flat" style="margin-top:8px">${SET.choice("web_search_mode", tr("Источник поиска", "Search source"), E.web_search_mode)}${SET.range("custom_search_max_results", tr("Результатов поиска", "Search results"), 1, 10)}</div>
        <div class="section-title">Deep Research</div>
        <div class="group flat">${SET.model("research_model", tr("Модель исследования", "Research model"), tr("Модель чата", "Chat model"))}${SET.range("research_max_queries", tr("Запросов не больше", "Max queries"), 4, 150)}${SET.range("research_max_rounds", tr("Раундов не больше", "Max rounds"), 1, 8)}${SET.range("research_max_sources", tr("Источников не больше", "Max sources"), 4, 100)}</div>
        <p class="section-note">${esc(tr("API-ключи поисковых сервисов меняются только на компьютере.", "Search API keys are changed on the computer only."))}</p>`),
      context: () => settingsPage(tr("Контекст", "Context"), `
        <div class="group flat" style="margin-top:8px">${SET.toggle("context_optimization", tr("Сжимать длинный контекст", "Compress long context"))}${SET.range("context_compression_threshold", tr("Порог сжатия", "Compression threshold"), 50, 98, 1, "%")}${SET.range("context_recent_messages", tr("Последних сообщений дословно", "Recent messages verbatim"), 6, 80)}</div>
        <div class="section-title">${esc(tr("Агенты Work и MemuCode", "Work and MemuCode agents"))}</div><div class="group flat">${SET.range("agent_count", tr("Агентов одновременно", "Agents at once"), 1, 4)}</div>`),
      telegram: () => settingsPage("Telegram", `
        <div class="group flat" style="margin-top:8px">${SET.choice("telegram_notify_mode", tr("Уведомления о завершении", "Completion notifications"), E.telegram_notify_mode)}${SET.model("telegram_model", tr("Модель бота", "Bot model"), tr("Основная модель MemuAI", "MemuAI default"))}${SET.choice("telegram_language", tr("Язык бота", "Bot language"), E.telegram_language)}${SET.range("telegram_history_messages", tr("Сообщений в контексте бота", "Messages in the bot's context"), 10, 100, 10)}</div>
        <p class="section-note">${esc(tr("Токен бота, привязка и белый список меняются только на компьютере: Настройки → Telegram.", "The bot token, pairing and the allowed users are changed on the computer only: Settings → Telegram."))}</p>`),
      desktop: () => settingsPage(tr("На компьютере", "On the computer"), `
        <div class="group flat" style="margin-top:8px">${SET.toggle("notify_windows", tr("Уведомления Windows", "Windows notifications"))}${SET.toggle("notify_sound", tr("Звук по завершении", "Sound when done"))}${SET.choice("notify_sound_style", tr("Сигнал", "Sound"), E.notify_sound_style)}${SET.range("notify_sound_volume", tr("Громкость", "Volume"), 0, 100, 5, "%")}</div>`),
      general: () => settingsPage(tr("Общие", "General"), `
        <div class="group flat" style="margin-top:8px">${SET.toggle("confirm_deletes", tr("Подтверждать удаление", "Confirm deletions"))}${SET.toggle("updates_enabled", tr("Проверять обновления", "Check for updates"), tr("На компьютере", "On the computer"))}</div>`),
      about: () => settingsPage(tr("О Mini App", "About the Mini App"), `
        <div class="group flat" style="margin-top:8px"><div class="cell flat"><div class="cell-body"><div class="cell-title">MemuAI</div></div><div class="cell-value">${esc(S.boot.version || "")}</div></div>
        <div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Связь с Telegram", "Telegram bridge"))}</div></div><div class="cell-value">${esc(S.official ? "telegram-web-app.js" : tr("встроенный мост", "built-in bridge"))}</div></div>
        <div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Платформа", "Platform"))}</div></div><div class="cell-value">${esc((S.tg && S.tg.platform) || "web")} · Bot API ${esc((S.tg && S.tg.version) || "—")}</div></div></div>
        <div class="section-title">MemuAI Live</div><div class="group flat"><div class="cell flat"><div class="cell-body"><div class="cell-sub" style="-webkit-line-clamp:unset">${esc(tr("Голосовой режим Live работает только на компьютере: ему нужен микрофон через WebView2 и постоянное аудиосоединение, а быстрый туннель Cloudflare не держит такие соединения надёжно, и Telegram не на всех платформах даёт Mini App доступ к микрофону.", "Live voice works on the computer only: it needs the microphone through WebView2 and a continuous audio connection, which the Cloudflare quick tunnel does not carry reliably, and Telegram does not give Mini Apps microphone access on every platform."))}</div></div></div></div>
        <div class="section-title">${esc(tr("Безопасность", "Security"))}</div><div class="group flat"><div class="cell flat"><div class="cell-body"><div class="cell-sub" style="-webkit-line-clamp:unset">${esc(tr("Вход — только с вашего аккаунта Telegram, привязанного в MemuAI. Подпись Telegram проверяется на компьютере, ключи и пароли сюда не передаются. Отозвать все сессии: на компьютере → Telegram → Mini App → «Сбросить сессии».", "Only your Telegram account paired in MemuAI can sign in. The Telegram signature is checked on the computer; keys and passwords never come here. Revoke every session on the computer → Telegram → Mini App → “Reset sessions”."))}</div></div></div></div>`),
    };
    (pages[id] || kit.pages[id] || (() => {}))();
  }
  function openLookPage() {
    const E = ENUMS();
    const build = () => {
      const accents = (S.themes && S.themes.accents) || [];
      const scheme = currentScheme();
      const variants = ((S.themes && S.themes.variants) || {})[scheme] || [];
      const variantKey = "surface_" + scheme;
      return `<div class="section-title" style="margin-top:8px">${esc(tr("В Telegram", "In Telegram"))}</div>
        <div class="segmented" id="look-seg"><button data-look="memuai" class="${S.ui.look !== "telegram" ? "active" : ""}">${esc(tr("Палитра MemuAI", "MemuAI palette"))}</button><button data-look="telegram" class="${S.ui.look === "telegram" ? "active" : ""}">${esc(tr("Цвета Telegram", "Telegram colours"))}</button></div>
        <div style="height:10px"></div>
        <div class="segmented" id="scheme-seg"><button data-scheme="telegram" class="${S.ui.scheme !== "app" ? "active" : ""}">${esc(tr("Как в Telegram", "Like Telegram"))}</button><button data-scheme="app" class="${S.ui.scheme === "app" ? "active" : ""}">${esc(tr("Как в MemuAI", "Like MemuAI"))}</button></div>
        <p class="section-note">${esc(tr("Светлая или тёмная тема: по Telegram или по настройке MemuAI.", "Light or dark: follow Telegram or the MemuAI setting."))}</p>
        <div class="section-title">${esc(tr("Акцент", "Accent"))}</div>
        <div class="group"><div class="swatches">${accents.map((a) => `<button class="swatch ${S.settings.accent_color === a.id ? "active" : ""}" data-accent="${esc(a.id)}" style="background:${esc(a[scheme])};color:${esc(a[scheme])}" aria-label="${esc(a.name)}"></button>`).join("")}</div></div>
        <div class="section-title">${esc(tr("Палитра", "Palette"))} · ${esc(scheme === "light" ? tr("светлая", "light") : tr("тёмная", "dark"))}</div>
        <div class="group"><div class="variant-row">${variants.map((v) => `<button class="variant ${(S.settings[variantKey] || "") === v.id ? "active" : ""}" data-variant="${esc(v.id)}"><div class="pv" style="background:${esc(v.bg || "var(--bg)")}"><i style="top:14px;background:${esc(v.panel || "var(--card)")}"></i><i style="top:28px;right:40px;background:var(--accent)"></i></div>${esc(v.name)}</button>`).join("")}</div></div>
        <div class="section-title">MemuAI</div>
        <div class="group flat">${SET.choice("theme", tr("Тема на компьютере", "Theme on the computer"), E.theme)}${SET.choice("ui_design", tr("Дизайн", "Design"), E.ui_design)}${SET.choice("app_font", tr("Шрифт ответов", "Answer font"), E.app_font)}
          ${SET.range("font_scale", tr("Размер текста", "Text size"), 85, 125, 5, "%")}${SET.toggle("compact_messages", tr("Компактные сообщения", "Compact messages"))}${SET.toggle("reduce_motion", tr("Меньше анимации", "Reduce motion"))}</div>
        ${kit.hooks.lookRows.map((row) => { try { return row.html(SET) || ""; } catch (e) { return ""; } }).join("")}`;
    };
    const entry = openStack({ title: tr("Тема и цвета", "Theme and colours"), build: (view) => {
      const draw = () => {
        view.innerHTML = `<div style="padding:4px 0 40px">${build()}</div>`;
        $$("#look-seg button", view).forEach((b) => b.onclick = () => { S.ui.look = b.dataset.look; saveUiPrefs(); applyAppearance(); haptic("select"); draw(); });
        $$("#scheme-seg button", view).forEach((b) => b.onclick = () => { S.ui.scheme = b.dataset.scheme; saveUiPrefs(); applyAppearance(); haptic("select"); draw(); });
        $$("[data-accent]", view).forEach((b) => b.onclick = async () => { haptic("select"); await saveSetting({ accent_color: b.dataset.accent }, { quiet: true }); draw(); });
        $$("[data-variant]", view).forEach((b) => b.onclick = async () => { haptic("select"); await saveSetting({ ["surface_" + currentScheme()]: b.dataset.variant }, { quiet: true }); draw(); });
        bindSettingControls(view, draw);
        $$("[data-set-int]", view).forEach((input) => { input.onchange = async () => { await saveSetting({ [input.dataset.setInt]: +input.value }, { quiet: true }); }; });
        kit.hooks.lookRows.forEach((row) => { try { if (row.bind) row.bind(view, draw); } catch (e) { console.error(e); } });
      };
      view.style.overflowY = "auto";
      draw();
    } });
    return entry;
  }
  function openMemoryPage() {
    /* Round 29: memory v2 — facts with category, source, confidence, pin; pending facts approved or rejected here. */
    const SERVICE = { chat: ["Чат", "Chat"], work: ["Work", "Work"], code: ["MemuCode", "MemuCode"], quick: ["Быстрый доступ", "Quick Access"],
      telegram: ["Telegram", "Telegram"], dfy: ["Do For You", "Do For You"], note: ["MemuNote", "MemuNote"], manual: ["Вручную", "Added by you"], import: ["Импорт", "Import"] };
    let query = "";
    openStack({ title: tr("Память", "Memory"), build: (view) => {
      view.style.overflowY = "auto";
      let data = {};
      const act = async (payload) => {
        try { data = await api("memory.item", payload); haptic("select"); draw(); } catch (e) { toast(e.message, "err"); }
      };
      const label = (pair) => Array.isArray(pair) ? (S.lang === "en" ? pair[1] : pair[0]) : "";
      const sourceLine = (item) => {
        const src = item.source || {};
        const bits = [label(SERVICE[src.service] || [src.service || "", src.service || ""])];
        if (src.chat_title) bits.push("«" + src.chat_title + "»");
        if (typeof item.confidence === "number" && item.status === "active" && item.confidence < 1) bits.push(Math.round(item.confidence * 100) + "%");
        if (item.updated_at || item.created_at) bits.push(ago(item.updated_at || item.created_at));
        return bits.filter(Boolean).join(" · ");
      };
      const row = (item, editable) => `<div class="fact mem-item${item.pinned ? " pinned" : ""}" data-mem="${esc(item.id)}">
          <span><span class="mem-text">${item.pinned ? `<i class="mem-pin">${icon("pin")}</i>` : ""}${esc(item.text)}</span><small class="mem-src">${esc(sourceLine(item))}</small></span>
          ${editable ? `<button data-mem-menu="${esc(item.id)}" aria-label="${esc(tr("Действия", "Actions"))}">${icon("more")}</button>` : ""}</div>`;
      const draw = () => {
        const memory = data.memory || { enabled: true, facts: [] }; S.memory = memory;
        const editable = !!data.editable;
        const items = Array.isArray(data.items) ? data.items : null;
        const needle = query.trim().toLowerCase();
        const match = (text) => !needle || String(text || "").toLowerCase().includes(needle);
        let body;
        if (items) {
          const pending = items.filter((i) => i.status === "pending" && match(i.text));
          const active = items.filter((i) => i.status === "active" && match(i.text));
          const groups = {};
          active.forEach((i) => { (groups[i.category || "other"] = groups[i.category || "other"] || []).push(i); });
          const order = Object.keys(data.categories || {});
          const keys = Object.keys(groups).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
          keys.forEach((k) => groups[k].sort((a, b) => (b.pinned - a.pinned) || ((b.updated_at || 0) - (a.updated_at || 0))));
          body = (items.length > 6 ? `<div class="search">${icon("search")}<input id="mem-q" type="search" placeholder="${esc(tr("Найти в памяти", "Search memory"))}" value="${esc(query)}" autocomplete="off"></div>` : "")
            + (pending.length ? `<div class="section-title">${esc(tr("Ждут подтверждения", "Waiting for approval"))} · ${pending.length}</div><div class="group">${pending.map((item) =>
              `<div class="fact mem-item pending"><span><span class="mem-text">${esc(item.text)}</span><small class="mem-src">${esc(sourceLine(item))}${item.op === "forget" ? " · " + esc(tr("забыть", "forget")) : item.op === "update" ? " · " + esc(tr("обновить", "update")) : ""}</small></span>
                ${editable ? `<button data-mem-ok="${esc(item.id)}" class="mem-ok" aria-label="${esc(tr("Запомнить", "Remember"))}">${icon("check")}</button><button data-mem-no="${esc(item.id)}" aria-label="${esc(tr("Отклонить", "Reject"))}">${icon("close")}</button>` : ""}</div>`).join("")}</div>` : "")
            + (keys.length ? keys.map((k) => `<div class="section-title">${esc(label((data.categories || {})[k]) || k)} · ${groups[k].length}</div><div class="group">${groups[k].map((i) => row(i, editable)).join("")}</div>`).join("")
              : `<div class="group"><div class="fact"><span style="color:var(--hint)">${esc(needle ? tr("Ничего не найдено", "Nothing found") : tr("Пока ничего не запомнено", "Nothing remembered yet"))}</span></div></div>`);
        } else {
          body = `<div class="section-title">${esc(tr("Факты", "Facts"))} · ${(memory.facts || []).length}</div>
          <div class="group">${(memory.facts || []).map((f, i) => `<div class="fact"><span>${esc(f)}</span>${editable ? `<button data-forget="${i}" aria-label="${esc(tr("Забыть", "Forget"))}">${icon("trash")}</button>` : ""}</div>`).join("") || `<div class="fact"><span style="color:var(--hint)">${esc(tr("Пока ничего не запомнено", "Nothing remembered yet"))}</span></div>`}</div>`;
        }
        view.innerHTML = `<div style="padding:8px 0 40px"><div class="group flat"><label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Долгая память", "Long-term memory"))}</div><div class="cell-sub">${esc(tr("ИИ запоминает устойчивые факты о вас", "The AI keeps stable facts about you"))}</div></div><span class="switch"><input type="checkbox" id="mem-on" ${memory.enabled ? "checked" : ""} ${editable ? "" : "disabled"}><span></span></span></label></div>
          ${body}
          ${editable ? `<p class="section-note">${esc(tr("Правила, области действия и импорт — в MemuAI на компьютере: Настройки → Профиль и память.", "Rules, scopes and import live in MemuAI on the computer: Settings → Profile & memory."))}</p>` : `<p class="section-note">${esc(tr("Это не активный профиль на компьютере: его память меняется там.", "This is not the computer's active profile: its memory is edited there."))}</p>`}</div>`;
        const q = $("#mem-q", view);
        if (q) q.oninput = () => { query = q.value; const pos = q.selectionStart; draw(); const again = $("#mem-q", view); if (again) { again.focus(); try { again.setSelectionRange(pos, pos); } catch (e) { /* ignore */ } } };
        const on = $("#mem-on", view); if (on) on.onchange = async () => { try { data = Object.assign(data, await api("memory.save", { enabled: on.checked })); haptic("select"); draw(); } catch (e) { toast(e.message, "err"); } };
        $$("[data-forget]", view).forEach((b) => b.onclick = async () => {
          const fact = memory.facts[+b.dataset.forget];
          if (!(await confirmDialog(fact, { title: tr("Забыть факт?", "Forget this fact?"), ok: tr("Забыть", "Forget"), danger: true }))) return;
          try { data = Object.assign(data, await api("memory.save", { remove: [fact] })); draw(); } catch (e) { toast(e.message, "err"); }
        });
        $$("[data-mem-ok]", view).forEach((b) => b.onclick = () => act({ id: b.dataset.memOk, action: "approve" }));
        $$("[data-mem-no]", view).forEach((b) => b.onclick = () => act({ id: b.dataset.memNo, action: "reject" }));
        $$("[data-mem-menu]", view).forEach((b) => b.onclick = () => {
          const item = (data.items || []).find((i) => i.id === b.dataset.memMenu); if (!item) return;
          actionSheet(item.text.length > 60 ? item.text.slice(0, 60) + "…" : item.text, [
            { label: item.pinned ? tr("Открепить", "Unpin") : tr("Закрепить — всегда в контексте", "Pin — always in context"), icon: "pin", run: () => act({ id: item.id, action: item.pinned ? "unpin" : "pin" }) },
            { label: tr("Изменить", "Edit"), icon: "edit", run: async () => { const text = await promptSheet(tr("Факт", "Fact"), { value: item.text, multiline: true, max: 2000 }); if (text && text.trim() !== item.text) act({ id: item.id, action: "edit", text: text.trim() }); } },
            { label: tr("Забыть", "Forget"), icon: "trash", danger: true, run: async () => {
              if (!(await confirmDialog(item.text, { title: tr("Забыть факт?", "Forget this fact?"), ok: tr("Забыть", "Forget"), danger: true }))) return;
              act({ id: item.id, action: "delete" });
            } },
          ]);
        });
      };
      view.innerHTML = `<div class="empty"><div class="typing"><i></i><i></i><i></i></div></div>`;
      api("memory.get").then((reply) => { data = reply; draw(); }).catch((e) => toast(e.message, "err"));
    } });
  }
  function switchProfile() {
    actionSheet(tr("Профиль", "Profile"), S.profiles.map((p) => ({ label: p.name || p.id, sub: p.active ? tr("Активный на компьютере", "Active on the computer") : p.has_pin ? tr("Защищён PIN-кодом", "PIN protected") : "",
      icon: p.id === S.profileId ? "check" : p.has_pin ? "lock" : "user",
      run: async () => {
        let pin = "";
        if (p.has_pin && !p.active) { pin = await promptSheet(tr("PIN-код профиля", "Profile PIN"), { max: 64, ok: tr("Войти", "Unlock") }); if (!pin) return; }
        try {
          const data = await api("profiles.switch", { profile_id: p.id, pin });
          S.token = data.token; S.tokenExp = data.expires; S.profileId = data.profile_id; S.chatsRev = ""; S.chats = [];
          try { sessionStorage.setItem("memu_profile", S.profileId); } catch (e) { /* ignore */ }
          toast(tr("Профиль: ", "Profile: ") + (p.name || p.id), "ok");
          const boot = await api("bootstrap"); applyBoot(boot); switchTab("settings", { force: true });
        } catch (error) { toast(error.message, "err"); }
      } })));
  }

  /* ================================================================ boot */
  function applyBoot(boot) {
    S.boot = boot; S.lang = boot.lang || S.lang; S.appearance = boot.appearance; S.themes = boot.themes; S.settings = boot.settings || {};
    S.models = boot.models || []; S.memes = boot.memes || []; S.folders = boot.folders || []; S.profiles = boot.profiles || [];
    S.profileId = boot.profile_id || S.profileId; S.features = boot.features || {}; S.memory = boot.memory || S.memory;
    applyAppearance();
  }
  function bootMessage(html) { $("#boot-sub").innerHTML = html; }
  async function start() {
    const bridge = await window.MemuBridge.ready;
    S.tg = bridge.app; S.official = bridge.official;
    S.initData = (S.tg && S.tg.initData) || "";
    const langCode = ((S.tg && S.tg.initDataUnsafe && S.tg.initDataUnsafe.user) || {}).language_code || navigator.language || "ru";
    S.lang = /^ru|^uk|^be|^kk/i.test(langCode) ? "ru" : "en";
    try { S.tg.ready(); S.tg.expand(); } catch (e) { /* preview */ }
    try { if (S.tg.disableVerticalSwipes && S.tg.isVersionAtLeast("7.7")) S.tg.disableVerticalSwipes(); } catch (e) { /* old */ }
    S.ui = Object.assign(S.ui, await loadUiPrefs());
    applyAppearance(); applySafeArea();
    try {
      S.tg.onEvent("themeChanged", applyAppearance);
      S.tg.onEvent("safeAreaChanged", applySafeArea); S.tg.onEvent("contentSafeAreaChanged", applySafeArea); S.tg.onEvent("fullscreenChanged", applySafeArea);
      S.tg.BackButton.onClick(goBack);
    } catch (e) { /* preview */ }
    $("#top-back").innerHTML = icon("back");
    $("#top-back").onclick = goBack;
    if (!S.initData && (bridge.params.tgWebAppPlatform || bridge.params.tgWebAppVersion || window.TelegramWebviewProxy)) {
      // Launched from a reply-keyboard button: Telegram sends no signed initData there.
      bootMessage(esc(tr("Telegram не передал подпись входа: так бывает, если открыть приложение кнопкой клавиатуры. Откройте MemuAI кнопкой меню слева от поля ввода или кнопкой «Открыть MemuAI» в сообщении бота.",
        "Telegram sent no sign-in signature: this happens when the app is opened from a keyboard button. Open MemuAI with the menu button left of the input field or the “Open MemuAI” button in a bot message.")));
      return;
    }
    if (!S.initData) {
      // Round 29: a normal browser — sign in with a QR code / code confirmed in the Mini App, or a one-time link (web.js).
      const session = window.MemuTgWeb ? await window.MemuTgWeb.signIn(kit) : null;
      if (!session) {
        if (!window.MemuTgWeb) bootMessage(esc(tr("Откройте MemuAI через кнопку в боте Telegram. Здесь, вне Telegram, приложение не может подтвердить, что это вы.",
          "Open MemuAI from the button in the Telegram bot. Outside Telegram the app cannot confirm it is you.")));
        return;
      }
      S.web = true; S.token = session.token; S.tokenExp = session.expires;
      document.documentElement.dataset.web = "1";
    }
    bootMessage(esc(tr("Подключение к компьютеру…", "Connecting to your computer…")));
    try {
      if (!S.web) await authenticate();
      try { const saved = sessionStorage.getItem("memu_profile"); if (saved) S.profileId = saved; } catch (e) { /* ignore */ }
      let boot;
      try { boot = await api("bootstrap"); }
      catch (error) { if (error.code === "pin_required") { S.profileId = ""; boot = await api("bootstrap"); } else throw error; }
      applyBoot(boot);
    } catch (error) {
      const hints = {
        not_paired: tr("Этот аккаунт Telegram не привязан. В MemuAI на компьютере: Настройки → Telegram → «Подключить Telegram» или добавьте его user id в «Дополнительно: Chat ID и доступ».", "This Telegram account is not paired. In MemuAI on the computer: Settings → Telegram → “Connect Telegram”, or add its user id under “Advanced: Chat ID and access”."),
        expired: tr("Данные запуска устарели. Закройте и снова откройте приложение.", "The launch data is too old. Close and reopen the app."),
        offline: tr("Компьютер не отвечает. Проверьте, что он включён и MemuAI Proxy запущен.", "The computer does not answer. Make sure it is on and MemuAI Proxy is running."),
        disabled: tr("Mini App выключен в настройках MemuAI.", "The Mini App is switched off in MemuAI settings."),
      };
      // r37: no computer (network error / tunnel 5xx) — a plugin may open without it (MemuMusic: tracks saved on the phone)
      const unreachable = error.code === "offline" || !error.status || error.status >= 500;
      const away = unreachable ? kit.hooks.offlineBoot.map((fn) => { try { return fn(); } catch (e) { return null; } }).find(Boolean) : null;
      bootMessage(`${esc(hints[unreachable ? "offline" : error.code] || error.message)}<br><button class="btn small secondary" id="boot-retry">${esc(tr("Повторить", "Retry"))}</button>${away ? ` <button class="btn small" id="boot-away">${esc(away.label)}</button>` : ""}`);
      $("#boot-retry").onclick = () => location.reload();
      if (away) $("#boot-away").onclick = away.run;
      haptic("error");
      return;
    }
    $("#app").hidden = false;
    $("#boot").classList.add("out");
    setTimeout(() => $("#boot").remove(), 400);
    let tab = "chats";
    try { tab = sessionStorage.getItem("memu_tab") || "chats"; } catch (e) { /* ignore */ }
    const startParam = (S.tg.initDataUnsafe && S.tg.initDataUnsafe.start_param) || "";
    const knownTab = (id) => TABS.some((t) => t.id === id);   // r37: plugins add tabs (kit.addTab — «Музыка»)
    if (!knownTab(tab)) tab = "chats";
    if (knownTab(startParam)) tab = startParam;
    try { const wanted = new URLSearchParams(location.search).get("tab") || ""; if (knownTab(wanted)) tab = wanted; } catch (e) { /* ignore */ }
    if (tab === "code" && !S.features.code) tab = "chats";
    S.tab = tab;
    renderTabbar();
    switchTab(tab, { force: true });
    if (/^chat_[\w-]+$/.test(startParam)) openChat(startParam.slice(5));
    loadCodeSessions();
    kit.hooks.afterBoot.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) return;
      if (S.tab === "chats") loadChats().then((changed) => { if (changed) { drawChatChips(); drawChatList(); } });
      if (S.tab === "settings" && !S.stack.length) refreshSettings().then((changed) => { if (changed) renderSettingsTab(rootView()); });
    });
  }
  /* Round 29: the kit for the feature modules (code.js, voice.js, extras.js, web.js, device.js). They register
     themselves with window.MemuTgPlugins.push(fn) — before or after this file — and get the kit. */
  const kit = {
    S, $, $$, esc, md, tr, icon, ICONS, sleep, clamp, haptic, toast, api, ApiError, followJob, openSheet, actionSheet,
    promptSheet, confirmDialog, openStack, closeStack, updateStackTop, setTop, rootView, switchTab, renderTabbar,
    bindRichContent, copyText, ago, duration, modelName, pickModel, longPress, openChat, primaryStack, isSplit,
    refreshTops, goBack, openLink, enterSends, bootMessage, loadChats, readDataUrl, scrollConvo: (C, force) => scrollConvo(C, force),
    sendMessage: (C, options) => sendMessage(C, options), attachJob: (C, job, from, resumed) => attachJob(C, job, from, resumed),
    drawConversation: (C, end) => drawConversation(C, end), openSettingsPage, saveUiPrefs, applyAppearance,
    hooks: { composerExtras: [], settingsRows: [], afterBoot: [], lookRows: [], offlineBoot: [] }, pages: {},
    // r37: a plugin tab — {id, icon, ru, en, before?, when?} + its render(view); BackButton for own overlays
    tabViews: {}, pushBack, popBack,
    addTab(tab, render) { const at = TABS.findIndex((t) => t.id === tab.before); TABS.splice(at < 0 ? TABS.length : at, 0, tab); kit.tabViews[tab.id] = render; },
  };
  function runPlugin(fn) { try { fn(kit); } catch (error) { console.error("MemuAI plugin", error); } }
  const queued = Array.isArray(window.MemuTgPlugins) ? window.MemuTgPlugins : [];
  window.MemuTgPlugins = { push: runPlugin };
  queued.forEach(runPlugin);
  window.MemuTgApp = { state: S, kit, switchTab, openChat, openCodeSession, openSettingsPage, applyAppearance };
  start().catch((error) => { console.error(error); bootMessage(esc(String(error && error.message || error))); });
})();
