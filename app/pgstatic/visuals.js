/* MemuAI inline visualizations (like ChatGPT/Gemini visuals).
 *
 * The model may place a hidden ```memuai-visual block anywhere in an answer.
 * It contains a small self-contained HTML/SVG/Canvas + JS fragment. MemuAI
 * renders it in place inside a sandboxed iframe: no same-origin access (no
 * app data, storage or cookies), no network (CSP), no popups or navigation.
 * The user sees the result, never the code. Theme colors are passed in as
 * CSS variables and the frame follows the content height.
 */
"use strict";
const MemuVisuals = (() => {
  const MAX_SOURCE = 60000;
  const frames = new Map();
  let counter = 0;

  const PROMPT = `<visualizations>
You can embed a small interactive visualization directly in your answer, like ChatGPT or Gemini visuals.
Use it RARELY — only when seeing or manipulating something is clearly better than text: plotting numeric
data or a function, comparing values, a timeline, a process or physics simulation, a small calculator or
converter with sliders. Never for plain text, simple lists, a single number, code the user asked to see,
or anything you could show as a normal table. At most one or two per answer.
Put it exactly where it belongs in the answer (it can be in the middle), always together with a normal text
explanation, and never mention the code, the block or "the visualization below was generated".
Write one hidden fenced block:
\`\`\`memuai-visual
<div id="app"></div>
<style>/* scoped CSS */</style>
<script>/* vanilla JS */</script>
\`\`\`
Rules: an HTML fragment only (no <html>, <head> or <body>); vanilla JS, inline SVG or <canvas>; no external
scripts, fonts, images or network requests (they are blocked) and no localStorage; keep it under ~12 KB.
Use the provided CSS variables for a native look in both themes: var(--bg), var(--panel), var(--text),
var(--muted), var(--accent), var(--border), var(--font). Transparent page background, width 100%,
responsive, height up to about 480px. Label axes and values, keep text legible, make controls keyboard
accessible, and make the numbers it shows consistent with your text.
</visualizations>`;

  function themeVars() {
    const style = getComputedStyle(document.documentElement);
    const read = (name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;
    // Themes have many names: decide light/dark from the page background.
    const probe = document.createElement("div");
    probe.style.color = read("--bg", "#ffffff");
    document.body.appendChild(probe);
    const rgb = (getComputedStyle(probe).color.match(/\d+(\.\d+)?/g) || [255, 255, 255]).map(Number);
    probe.remove();
    const dark = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) < 128;
    return {
      scheme: dark ? "dark" : "light",
      bg: read("--bg", dark ? "#191b1e" : "#faf9f7"), panel: read("--panel", dark ? "#22252a" : "#ffffff"),
      text: read("--text", dark ? "#f0f1f3" : "#24282e"), muted: read("--muted", dark ? "#aeb4be" : "#606773"),
      accent: read("--accent", dark ? "#efa58b" : "#a84b30"), border: read("--border", dark ? "#3c424b" : "#d9dadd"),
      font: read("--sans", "system-ui, sans-serif"),
    };
  }

  function srcdoc(code, id, t = themeVars()) {
    const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:";
    return `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>:root{color-scheme:${t.scheme};--bg:${t.bg};--panel:${t.panel};--text:${t.text};--muted:${t.muted};--accent:${t.accent};--border:${t.border};--font:${t.font}}
*{box-sizing:border-box}html,body{margin:0;padding:0;height:auto;min-height:0;background:transparent;color:var(--text);font:14px/1.5 var(--font)}
button,input,select{font:inherit;color:inherit}svg text{fill:currentColor}</style>
<script>(function(){var id=${JSON.stringify(id)};
window.addEventListener("error",function(e){parent.postMessage({memuaiVisual:id,error:String(e.message||"Script error")},"*")});
function post(){if(!document.body)return;var r=document.body.getBoundingClientRect();parent.postMessage({memuaiVisual:id,height:Math.ceil(r.bottom+Math.max(0,window.scrollY))},"*")}
window.addEventListener("load",function(){post();if(window.ResizeObserver)new ResizeObserver(post).observe(document.body);setTimeout(post,300)});})();</script>
</head><body>${code}</body></html>`;
  }

  function decode(value) {
    try { return decodeURIComponent(value || ""); } catch (e) { return ""; }
  }

  function placeholder(code, en) {
    if (!code.trim()) return "";
    if (code.length > MAX_SOURCE) {
      return `<div class="memuai-visual failed" role="note">${en ? "The visualization is too large to show." : "Визуализация слишком большая, чтобы её показать."}</div>`;
    }
    const encoded = encodeURIComponent(code).replace(/'/g, "%27");
    return `<div class="memuai-visual" data-visual="${encoded}"><div class="visual-loading"><i></i>${en ? "Preparing the visualization…" : "Готовлю визуализацию…"}</div></div>`;
  }

  function pending(en) {
    return `<div class="memuai-visual pending"><div class="visual-loading"><i></i>${en ? "Creating a visualization…" : "Создаю визуализацию…"}</div></div>`;
  }

  // Streaming: an unfinished block becomes a skeleton instead of raw code.
  function maskPending(text) {
    return String(text || "").replace(/```[ \t]*memuai[-_ ]?visual\b((?:(?!```)[\s\S])*)$/i, "```memuai-visual-pending\n```");
  }

  // Copy/export/speech: a visualization is not text.
  function stripForText(text, en) {
    return String(text || "").replace(/```[ \t]*memuai[-_ ]?visual\b[\s\S]*?(?:```|$)/gi, en ? "[visualization]" : "[визуализация]");
  }

  // Перенос кадра без перезагрузки: Element.moveBefore (Chromium 133+).
  // Без поддержки кадры просто создаются заново, как раньше.
  const holder = document.createElement("div");
  holder.hidden = true;
  const stashed = new Map();
  function stash(root) {
    if (typeof Element.prototype.moveBefore !== "function") return;
    if (!holder.isConnected) document.body.appendChild(holder);
    (root || document).querySelectorAll(".memuai-visual[data-ready] > iframe.memuai-visual-frame").forEach((frame) => {
      const key = frame.parentElement.dataset.visual;
      if (!key) return;
      try { holder.moveBefore(frame, null); } catch (e) { return; }
      if (!stashed.has(key)) stashed.set(key, []);
      stashed.get(key).push(frame);
    });
  }

  function hydrate(root, isLive) {
    (root || document).querySelectorAll(".memuai-visual[data-visual]:not([data-ready])").forEach((host) => {
      if (isLive && isLive(host)) return; // keep the skeleton while the answer streams
      const code = decode(host.dataset.visual);
      if (!code) return;
      const kept = stashed.get(host.dataset.visual);
      if (kept && kept.length) {
        const frame = kept.shift();
        try { host.replaceChildren(); host.moveBefore(frame, null); host.dataset.ready = "1"; return; }
        catch (e) { /* ниже — новый кадр */ }
      }
      host.dataset.ready = "1";
      const id = "v" + (++counter);
      const frame = document.createElement("iframe");
      frame.className = "memuai-visual-frame";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.setAttribute("loading", "lazy");
      frame.title = document.documentElement.lang === "en" ? "Visualization" : "Визуализация";
      frame.style.height = "160px";
      const theme = themeVars();
      // Same color scheme as the page, or Chromium paints an opaque backdrop.
      frame.style.colorScheme = theme.scheme;
      // CSP не запрещает кадру перейти на другой адрес (location = …), а в
      // адресе можно унести данные. Вторая загрузка кадра — это переход:
      // такой кадр сразу заменяется сообщением.
      let loads = 0;
      frame.addEventListener("load", () => {
        loads += 1;
        if (loads < 2) return;
        frames.delete(id);
        const note = document.createElement("div");
        note.className = "memuai-visual failed";
        note.setAttribute("role", "note");
        note.textContent = document.documentElement.lang === "en"
          ? "The visualization tried to leave its frame and was stopped."
          : "Визуализация попыталась уйти со своей страницы и была остановлена.";
        frame.replaceWith(note);
      });
      frame.srcdoc = srcdoc(code, id, theme);
      frames.set(id, { frame, host });
      host.replaceChildren(frame);
    });
    // Кадры, которым не нашлось места (ответ удалён), выбрасываются.
    stashed.forEach((list) => list.forEach((frame) => frame.remove()));
    stashed.clear();
    for (const [id, item] of frames) {
      if (!item.frame.isConnected) frames.delete(id);
      else item.host = item.frame.parentElement;
    }
  }

  function refresh(root) {
    (root || document).querySelectorAll(".memuai-visual[data-ready]").forEach((host) => host.removeAttribute("data-ready"));
    hydrate(root);
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data.memuaiVisual !== "string") return;
    const item = frames.get(data.memuaiVisual);
    if (!item || event.source !== item.frame.contentWindow) return;
    if (typeof data.height === "number" && isFinite(data.height)) {
      item.frame.style.height = Math.max(60, Math.min(1400, Math.round(data.height))) + "px";
    }
    if (data.error && !item.host.querySelector(".visual-error")) {
      const note = document.createElement("div");
      note.className = "visual-error";
      note.textContent = (document.documentElement.lang === "en" ? "The visualization reported an error: " : "В визуализации произошла ошибка: ") + String(data.error).slice(0, 200);
      item.host.appendChild(note);
    }
  });

  return Object.freeze({ PROMPT, placeholder, pending, maskPending, stripForText, hydrate, refresh, stash });
})();
