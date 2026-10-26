

(function () {
  "use strict";
  if (window.MemuGuideVideo) return;

  const GUIDES = {
    "telegram-bot": { ru: "Бот Telegram: токен у @BotFather", en: "Telegram bot: a token from @BotFather", seconds: 40 },
    "google-client": { ru: "Свой OAuth-клиент Google и включение API", en: "Your own Google OAuth client and enabling the APIs", seconds: 43 },
    "github-token": { ru: "Токен GitHub для интеграции", en: "A GitHub token for the integration", seconds: 38 },
    "notion-connection": { ru: "Подключение Notion по токену", en: "Connecting Notion with a token", seconds: 40 },
    "proxy-api-key": { ru: "Аккаунт по API-ключу в MemuAI Proxy", en: "An API-key account in MemuAI Proxy", seconds: 38 },
  };
  const BASE = "/guides/";
  const PLAY = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="M8 5.5v13a1 1 0 0 0 1.52.85l10.4-6.5a1 1 0 0 0 0-1.7L9.52 4.65A1 1 0 0 0 8 5.5z" fill="currentColor"/></svg>';
  const CLOSE = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>';
  const REPLAY = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>';

  function detectLang() {
    if (typeof window.LANG === "string" && /^(en|ru)$/.test(window.LANG)) return window.LANG;
    return (document.documentElement.lang || "ru").toLowerCase().startsWith("en") ? "en" : "ru";
  }
  const isEn = (lang) => (lang || detectLang()) === "en";
  const T = (lang, ru, en) => (isEn(lang) ? en : ru);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (sec) => { const s = Math.max(0, Math.floor(sec || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

  function has(id) { return Object.prototype.hasOwnProperty.call(GUIDES, id); }

  function button(id, opts = {}) {
    if (!has(id)) return "";
    const lang = opts.lang || detectLang();
    const label = opts.label || T(lang, "Видео-инструкция", "Video guide");
    const title = T(lang, GUIDES[id].ru, GUIDES[id].en);
    return `<button type="button" class="mgv-btn${opts.compact ? " mgv-btn--compact" : ""}" data-guide-video="${esc(id)}" data-guide-lang="${esc(lang)}" title="${esc(title)}" aria-haspopup="dialog">${PLAY}<span>${esc(label)}</span><small>${esc(clock(GUIDES[id].seconds))}</small></button>`;
  }

  function parseVtt(text) {
    const cues = [];
    const toSec = (v) => { const p = v.trim().split(":").map(Number); return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]; };
    String(text || "").replace(/\r/g, "").split(/\n\n+/).forEach((block) => {
      const lines = block.split("\n").filter(Boolean);
      const at = lines.findIndex((l) => l.includes("-->"));
      if (at < 0) return;
      const [a, b] = lines[at].split("-->");
      const body = lines.slice(at + 1).join(" ").replace(/<[^>]+>/g, "").trim();
      if (body) cues.push({ start: toSec(a), end: toSec(b.trim().split(/\s+/)[0]), text: body });
    });
    return cues;
  }

  let current = null;

  function close() {
    if (!current) return;
    const { root, video, blobs, opener, onKey } = current;
    try { video.pause(); } catch (_) {  }
    document.removeEventListener("keydown", onKey, true);
    blobs.forEach((u) => URL.revokeObjectURL(u));
    root.remove();
    current = null;
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
  }

  async function open(id, opts = {}) {
    if (!has(id)) return false;
    close();
    const lang = opts.lang || detectLang();
    const guide = GUIDES[id];
    const root = document.createElement("div");
    root.className = "mgv-backdrop";
    root.innerHTML = `<div class="mgv-dialog" role="dialog" aria-modal="true" aria-labelledby="mgv-title" tabindex="-1">
      <header class="mgv-head"><span class="mgv-badge">${PLAY}</span><div class="mgv-titles"><b id="mgv-title">${esc(T(lang, guide.ru, guide.en))}</b>
        <small>${esc(T(lang, "Видео без звука · подписи можно выключить кнопкой CC", "Silent video · turn captions off with the CC button"))}</small></div>
        <button type="button" class="mgv-x" data-mgv="close" aria-label="${esc(T(lang, "Закрыть", "Close"))}" title="${esc(T(lang, "Закрыть (Esc)", "Close (Esc)"))}">${CLOSE}</button></header>
      <div class="mgv-stage"><video class="mgv-video" controls playsinline preload="auto" src="${BASE}${esc(id)}.webm"></video>
        <div class="mgv-error" hidden>${esc(T(lang, "Видео не загрузилось. Текстовая инструкция рядом с кнопкой описывает те же шаги.", "The video did not load. The text instructions next to the button describe the same steps."))}</div></div>
      <div class="mgv-foot"><ol class="mgv-steps" aria-label="${esc(T(lang, "Шаги", "Steps"))}"></ol>
        <div class="mgv-actions"><button type="button" class="mgv-act" data-mgv="replay">${REPLAY}<span>${esc(T(lang, "Сначала", "Replay"))}</span></button>
          <button type="button" class="mgv-act" data-mgv="speed" aria-label="${esc(T(lang, "Скорость", "Speed"))}">1×</button></div></div>
    </div>`;
    document.body.appendChild(root);
    const dialog = root.querySelector(".mgv-dialog");
    const video = root.querySelector("video");
    const list = root.querySelector(".mgv-steps");
    const state = { root, video, blobs: [], opener: opts.opener || document.activeElement, onKey: null };
    current = state;

    state.onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.key === "Tab") {
        const items = [...dialog.querySelectorAll("button, video, [tabindex='0']")].filter((el) => !el.hidden && el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", state.onKey, true);
    root.addEventListener("mousedown", (event) => { if (event.target === root) close(); });
    root.addEventListener("click", (event) => {
      const act = event.target.closest("[data-mgv]");
      if (!act) return;
      const what = act.dataset.mgv;
      if (what === "close") close();
      else if (what === "replay") { video.currentTime = 0; video.play().catch(() => {}); }
      else if (what === "speed") {
        const next = { 1: 1.5, 1.5: 0.75, 0.75: 1 }[video.playbackRate] || 1;
        video.playbackRate = next; act.textContent = `${next}×`;
      }
    });
    video.addEventListener("error", () => { root.querySelector(".mgv-error").hidden = false; });
    dialog.focus({ preventScroll: true });

    for (const code of ["ru", "en"]) {
      try {
        const response = await fetch(`${BASE}${id}.${code}.vtt`, { cache: "no-cache" });
        if (!response.ok) continue;
        const text = await response.text();
        if (current !== state) return true;
        const url = URL.createObjectURL(new Blob([text], { type: "text/vtt" }));
        state.blobs.push(url);
        const track = document.createElement("track");
        Object.assign(track, { kind: "subtitles", srclang: code, label: code === "ru" ? "Русский" : "English", src: url });
        if (code === (isEn(lang) ? "en" : "ru")) {
          track.default = true;
          const cues = parseVtt(text);
          list.innerHTML = cues.map((c, i) => `<li><button type="button" class="mgv-step" data-at="${c.start}" data-i="${i}"><span class="mgv-step-t">${clock(c.start)}</span><span>${esc(c.text)}</span></button></li>`).join("");
          list.addEventListener("click", (event) => {
            const step = event.target.closest(".mgv-step");
            if (step) { video.currentTime = Number(step.dataset.at) + 0.05; video.play().catch(() => {}); }
          });
          video.addEventListener("timeupdate", () => {
            const t = video.currentTime;
            let active = -1;
            cues.forEach((c, i) => { if (t >= c.start) active = i; });
            list.querySelectorAll(".mgv-step").forEach((el) => {
              const on = Number(el.dataset.i) === active;
              if (on && !el.classList.contains("is-on")) el.scrollIntoView({ block: "nearest" });
              el.classList.toggle("is-on", on);
            });
          });
        }
        video.appendChild(track);
      } catch (_) {  }
    }

    const pick = () => { for (const track of video.textTracks) track.mode = track.language === (isEn(lang) ? "en" : "ru") ? "showing" : "disabled"; };
    pick();
    setTimeout(() => { if (current === state) pick(); }, 0);
    setTimeout(() => { if (current === state) pick(); }, 250);
    video.play().catch(() => {});
    return true;
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest && event.target.closest("[data-guide-video]");
    if (!trigger || trigger.closest(".mgv-backdrop")) return;
    event.preventDefault();
    event.stopPropagation();
    open(trigger.dataset.guideVideo, { lang: trigger.dataset.guideLang || undefined, opener: trigger });
  }, true);

  window.MemuGuideVideo = { button, open, close, has, list: () => Object.keys(GUIDES), guides: GUIDES };
})();
