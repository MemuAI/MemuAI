

"use strict";
const MemuVisuals = (() => {
  const MAX_SOURCE = 60000;
  const frames = new Map();
  let counter = 0;

  const PROMPT = `<visualizations>
You may insert native rich blocks anywhere they make an answer clearer: process/branch diagrams, sortable rankings,
comparisons, illustrated entity cards, charts, timelines, tables, tabs, accordions, metrics and calculators.
Use ordinary prose around them. Never invent facts, numeric values, image URLs or sources. Avoid decoration without purpose.
Native blocks are declarative JSON in a hidden fenced block named memuai-visual, for example:
\`\`\`memuai-visual
{"type":"text","title":"Options","blocks":[{"type":"ranking","max":10,"items":[{"name":"A","value":9,"detail":"Evidence"}]}]}
\`\`\`
Call {"tool":"visual_schema","args":{}} in a memuai-tool array for all shapes and supported controls.
Call {"tool":"visual_validate","args":{"visual":{...}}} to validate a complex block before emitting it.
For inline clickable terms define a hidden memuai-entity JSON fence with id, name, summary, optional image, facts,
sources[{title,url}] and inline_only:true. Refer outside code with [[entity:id|Visible term]].
The right information panel shows only the data you supply, so cite reliable sources and do not pretend it was verified.
For interactions beyond native controls, use a memuai-visual JSON object:
{"type":"custom","summary":"Concise explanation copied as readable text","html":"<div>...</div>"}.
Custom HTML runs in an isolated iframe: inline vanilla JS/SVG/canvas only, no external scripts/fonts/images/network,
no storage or application access. Use --bg,--panel,--text,--muted,--accent,--border,--font CSS variables.
Keep custom fragments under 12 KB where possible, responsive and keyboard accessible. Include a useful summary.
Copy/export of native blocks retains their meaningful text, labels and values. All visual features are user configurable.
</visualizations>`;

  function themeVars() {
    const style = getComputedStyle(document.documentElement);
    const read = (name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;

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
    const rich = window.MemuRichVisuals;
    if (rich) {
      const native = rich.render(code);
      if (native) return native;
      const value = rich.parse(code);
      if (value && (value.type === "custom" || typeof value.html === "string")) {
        if (!rich.customEnabled()) return `<div class="rv-plain">${escapeText(value.summary || "")}</div>`;
        code = String(value.html || "");
      } else if (/^\s*[\[{]/.test(code)) {
        return `<div class="rv-plain">${escapeText(en ? "The visual block could not be read." : "Не удалось прочитать визуальный блок.")}</div>`;
      } else if (!rich.customEnabled()) return `<div class="rv-plain">${escapeText(htmlText(code))}</div>`;
    }
    if (code.length > MAX_SOURCE) {
      return `<div class="memuai-visual failed" role="note">${en ? "The visualization is too large to show." : "Визуализация слишком большая, чтобы её показать."}</div>`;
    }
    const encoded = encodeURIComponent(code).replace(/'/g, "%27");
    return `<div class="memuai-visual" data-visual="${encoded}"><div class="visual-loading"><i></i>${en ? "Preparing the visualization…" : "Готовлю визуализацию…"}</div></div>`;
  }

  function pending(en) {
    return `<div class="memuai-visual pending"><div class="visual-loading"><i></i>${en ? "Creating a visualization…" : "Создаю визуализацию…"}</div></div>`;
  }

  function maskPending(text) {
    return String(text || "").replace(/```[ \t]*memuai[-_ ]?(?:visual|entity)\b((?:(?!```)[\s\S])*)$/i, "```memuai-visual-pending\n```");
  }

  function escapeText(value) { return String(value || "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  function htmlText(code) {
    const template=document.createElement("template");template.innerHTML=String(code || "");
    template.content.querySelectorAll("script,style,iframe,object,embed,link,meta").forEach(n=>n.remove());
    template.content.querySelectorAll("p,div,section,article,header,footer,h1,h2,h3,h4,li,tr,br").forEach(n=>n.append("\n"));
    return template.content.textContent.replace(/[ \t]+/g," ").replace(/\n[ \t]+/g,"\n").replace(/\n{3,}/g,"\n\n").trim().slice(0,12000);
  }
  function stripForText(text, en) {
    const source=String(text || "").replace(/```[ \t]*memuai[-_ ]?(visual|entity)\b[ \t]*\r?\n([\s\S]*?)(?:```|$)/gi, (_,kind,body) => {
      const value=window.MemuRichVisuals?.parse(body);
      if(value?.inline_only && kind.toLowerCase()==="entity")return "";
      if(value)return value.type==="custom" || typeof value.html==="string" ? String(value.summary || htmlText(value.html)) : MemuRichVisuals.plain(kind.toLowerCase()==="entity"?{...value,type:"entity"}:value);
      return /^\s*</.test(body)?htmlText(body):"";
    });
    return window.MemuRichVisuals ? MemuRichVisuals.stripInline(source) : source;
  }

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
    window.MemuRichVisuals?.hydrate(root);
    (root || document).querySelectorAll(".memuai-visual[data-visual]:not([data-ready])").forEach((host) => {
      if (isLive && isLive(host)) return;
      const code = decode(host.dataset.visual);
      if (!code) return;
      const kept = stashed.get(host.dataset.visual);
      if (kept && kept.length) {
        const frame = kept.shift();
        try { host.replaceChildren(); host.moveBefore(frame, null); host.dataset.ready = "1"; return; }
        catch (e) {  }
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

      frame.style.colorScheme = theme.scheme;

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
