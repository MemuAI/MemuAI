/* MemuAI Quick Ask · copying (Round 30, r30-quick-core).

   What the user gets:
   • «Копировать» под каждым ответом (у последнего — в ряду действий, у прежних — в строке «кто ответил»),
     ▾ рядом — формат: «С оформлением» (HTML + простой текст — Word, почта, мессенджеры), «Markdown», «Простой текст».
   • Выделенный кусок ответа: всплывающая полоска «Копировать · Спросить об этом»; Ctrl+C копирует его без служебных
     надписей (кнопок блоков кода, курсора стрима), формулы — их TeX.
   • Ctrl+C, когда ничего не выделено (и поле пустое/без выделения), — копирует последний ответ; Ctrl+Shift+C — тоже.
   • Каждое копирование подтверждается тостом «Скопировано».

   window.QuickCopy = { toPlain(md), toHtml(md), fromNode(node), write({plain, html}), copyMarkdown(md, format, opts),
                        openFormats(anchor, md), openMenu(anchor, {head, items, radio}), FORMATS }
   quick.js calls it for the answer buttons; everything else (selection bar, copy event, Ctrl+C) lives here. */
(function () {
  "use strict";
  const Q = () => window.MemuQuick;
  const t = (ru, en) => (Q() ? Q().t(ru, en) : (document.documentElement.lang === "en" ? en : ru));
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const FORMAT_KEY = "memu_quick_copy_format";
  const FORMATS = ["rich", "markdown", "plain"];

  /* ---------------------------------------------------------------- markdown → text / html */
  function rendered(md) {
    const box = document.createElement("div");
    box.innerHTML = window.QuickMarkdown ? QuickMarkdown.render(String(md || ""), { streaming: false }) : esc(md);
    return box;
  }
  const SKIP = ".code-header, .code-copy-btn, .qa-caret, button, .mt-tools, .md-tools, [data-nocopy], .katex-html, script, style";
  function texOf(el) {
    const ann = el.querySelector('annotation[encoding="application/x-tex"]');
    return ann ? ann.textContent.trim() : el.textContent.trim();
  }

  /* DOM → readable plain text: paragraphs separated by a blank line, list bullets and numbers kept, code verbatim,
     table rows tab-separated, formulas as TeX. Works on a detached tree (no layout, unlike innerText). */
  function fromNode(rootNode) {
    const out = [];
    const block = (s) => { const v = s.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim(); if (v) out.push(v); };
    const inline = (node) => {
      let s = "";
      for (const child of node.childNodes) {
        if (child.nodeType === 3) { s += child.nodeValue; continue; }
        if (child.nodeType !== 1 || child.matches(SKIP)) continue;
        if (child.matches(".katex-display, .katex")) { const tex = texOf(child); s += child.matches(".katex-display") ? `\n${tex}\n` : tex; continue; }
        if (child.tagName === "BR") { s += "\n"; continue; }
        if (child.tagName === "A") { const label = inline(child); const href = child.getAttribute("href") || ""; s += href && label.trim() !== href ? `${label} (${href})` : label; continue; }
        if (child.matches("ul, ol, table, pre, .code-block-wrap, .table-wrap, p, div, blockquote, h1, h2, h3, h4, h5, h6, hr")) continue;  // blocks are walked by walk()
        s += inline(child);
      }
      return s;
    };
    const list = (el, depth) => {
      let n = Number(el.getAttribute("start") || 1);
      const lines = [];
      for (const li of el.children) {
        if (li.tagName !== "LI") continue;
        const mark = el.tagName === "OL" ? `${n++}.` : "•";
        lines.push("  ".repeat(depth) + mark + " " + inline(li).replace(/\s+/g, " ").trim());
        for (const sub of li.querySelectorAll(":scope > ul, :scope > ol")) lines.push(list(sub, depth + 1));
      }
      return lines.filter(Boolean).join("\n");
    };
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (child.nodeType === 3) { if (child.nodeValue.trim()) block(child.nodeValue); continue; }
        if (child.nodeType !== 1 || child.matches(SKIP)) continue;
        if (child.matches(".code-block-wrap, pre")) { const code = (child.matches("pre") ? child : child.querySelector("pre")); if (code) out.push(code.textContent.replace(/\n$/, "")); continue; }
        if (child.matches("table, .table-wrap")) {
          const rows = Array.from(child.querySelectorAll("tr")).map((tr) => Array.from(tr.children).map((c) => inline(c).replace(/\s+/g, " ").trim()).join("\t"));
          if (rows.length) out.push(rows.join("\n"));
          continue;
        }
        if (child.matches("ul, ol")) { block(list(child, 0)); continue; }
        if (child.tagName === "HR") { out.push("———"); continue; }
        if (child.matches(".katex-display")) { block(texOf(child)); continue; }
        if (child.matches("p, h1, h2, h3, h4, h5, h6, blockquote, li")) {
          if (child.querySelector("ul, ol, pre, table, p")) { walk(child); continue; }
          block(inline(child)); continue;
        }
        if (child.querySelector("p, ul, ol, pre, table, h3, h4, blockquote, .katex-display, div")) walk(child);
        else block(inline(child));
      }
    };
    walk(rootNode);
    return out.join("\n\n").trim();
  }
  const toPlain = (md) => fromNode(rendered(md));

  /* HTML for rich paste (Word / Outlook / Google Docs / Telegram Desktop): the rendered answer without our buttons,
     with formulas as their TeX in <code> (KaTeX markup needs its CSS and pastes as garbage elsewhere). */
  function cleanHtml(box) {
    box.querySelectorAll(".code-header, .code-copy-btn, .qa-caret, button, [data-nocopy]").forEach((el) => el.remove());
    box.querySelectorAll(".katex-display, .katex").forEach((el) => {
      if (!el.isConnected && !box.contains(el)) return;
      const code = document.createElement(el.matches(".katex-display") ? "pre" : "code");
      code.textContent = texOf(el);
      el.replaceWith(code);
    });
    box.querySelectorAll("[class]").forEach((el) => { if (!/^language-/.test(el.className)) el.removeAttribute("class"); });
    box.querySelectorAll("[style]").forEach((el) => el.removeAttribute("style"));
    return box.innerHTML;
  }
  const toHtml = (md) => cleanHtml(rendered(md));

  /* ---------------------------------------------------------------- clipboard */
  async function write({ plain, html }) {
    plain = String(plain || "");
    if (!plain.trim()) return false;
    try {
      if (html && window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        await navigator.clipboard.write([new ClipboardItem({
          "text/plain": new Blob([plain], { type: "text/plain" }),
          "text/html": new Blob([`<meta charset="utf-8">${html}`], { type: "text/html" }),
        })]);
        return true;
      }
    } catch (e) { /* plain text below */ }
    try { await navigator.clipboard.writeText(plain); return true; } catch (e) { /* native / execCommand below */ }
    if (Q() && Q().native) {
      try { const r = await Q().api("/api/quick/window", { method: "POST", body: { action: "copy", text: plain } }); if (r && r.ok) return true; } catch (e) { /* next */ }
    }
    const area = document.createElement("textarea");
    area.value = plain; area.setAttribute("readonly", ""); area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(area); area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    return ok;
  }
  function savedFormat() { try { const f = localStorage.getItem(FORMAT_KEY); return FORMATS.includes(f) ? f : "rich"; } catch (e) { return "rich"; } }
  function label(format) {
    return { rich: t("С оформлением", "Formatted"), markdown: "Markdown", plain: t("Простой текст", "Plain text") }[format];
  }
  function toastText(format) {
    return format === "markdown" ? t("Скопировано как Markdown", "Copied as Markdown")
      : format === "plain" ? t("Скопировано простым текстом", "Copied as plain text") : t("Скопировано", "Copied");
  }
  function toast(ok, text) {
    if (Q()) Q().toast(ok ? text : t("Не удалось скопировать", "Could not copy"), ok ? "ok" : "err", 1800);
  }
  /* format: "rich" | "markdown" | "plain" | undefined (= the last one the user picked). */
  async function copyMarkdown(md, format, { notify = true } = {}) {
    const text = String(md || "").replace(/<think>[\s\S]*?(<\/think>|$)/g, "").trim();
    if (!text) return false;
    const f = FORMATS.includes(format) ? format : savedFormat();
    let ok;
    if (f === "markdown") ok = await write({ plain: text });
    else if (f === "plain") ok = await write({ plain: toPlain(text) });
    else ok = await write({ plain: toPlain(text), html: toHtml(text) });
    if (notify) toast(ok, toastText(f));
    return ok;
  }

  /* ---------------------------------------------------------------- the format menu (▾ next to «Копировать») */
  let menu = null;
  function closeFormats() {
    if (!menu) return;
    const anchor = menu._anchor;
    menu.remove(); menu = null;
    if (anchor) anchor.setAttribute("aria-expanded", "false");
    if (anchor && anchor.classList && anchor.classList.contains("qc-ctx-anchor")) anchor.remove();   // Round 31: the context menu's point
  }
  /* A small menu next to a button (also «⋯ Ещё» under the answer): items = [{id?, label, sub?, keys?, icon?, checked?, run}].
     ↑↓ move, Enter/Space pick, Esc closes and returns the focus to the button. */
  function openMenu(anchor, { head = "", items = [], radio = false } = {}) {
    if (menu && menu._anchor === anchor) { closeFormats(); return; }
    closeFormats();
    menu = document.createElement("div");
    menu.className = "qc-menu" + (radio ? " qc-menu-radio" : "");
    menu.setAttribute("role", "menu");
    menu._anchor = anchor;
    menu.innerHTML = (head ? `<div class="qc-menu-head">${esc(head)}</div>` : "") + items.map((it, i) => `<button type="button" role="${radio ? "menuitemradio" : "menuitem"}" ${radio ? `aria-checked="${!!it.checked}"` : ""} ${it.id ? `id="${esc(it.id)}"` : ""} data-i="${i}">
        ${radio ? '<span class="qc-menu-dot" aria-hidden="true"></span>' : `<span class="qc-menu-ic" aria-hidden="true">${it.icon || ""}</span>`}<span class="qc-menu-label">${esc(it.label)}</span>
        ${it.keys ? `<span class="qc-menu-keys">${it.keys.map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span>` : ""}
        ${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</button>`).join("");
    const card = document.getElementById("qa-card") || document.body;
    card.appendChild(menu);
    // Round 34 (r34-quick-design-2): the layout size, not getBoundingClientRect — the qc-pop scale-in made the menu look
    // ~4 % narrower, so «⋯ Ещё» opened 2–3 px from the card edge instead of 8.
    const a = anchor.getBoundingClientRect(), c = card.getBoundingClientRect(), m = { width: menu.offsetWidth, height: menu.offsetHeight };
    const left = Math.max(8, Math.min(a.right - c.left - m.width, c.width - m.width - 8));
    const above = a.top - c.top - m.height - 6;
    menu.style.left = left + "px";
    menu.style.top = (above >= 8 ? above : Math.min(a.bottom - c.top + 6, c.height - m.height - 8)) + "px";
    anchor.setAttribute("aria-expanded", "true");
    const buttons = Array.from(menu.querySelectorAll("[data-i]"));
    (buttons.find((b) => b.getAttribute("aria-checked") === "true") || buttons[0]).focus();
    menu.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      const it = items[+b.dataset.i];
      closeFormats();
      try { it.run(); } catch (err) { console.warn("[QuickCopy] menu", err); }
    });
    menu.addEventListener("keydown", (e) => {
      const i = buttons.indexOf(document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); buttons[(i + (e.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length].focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeFormats(); anchor.focus(); }
      else if (e.key === "Tab") closeFormats();
    });
  }
  function openFormats(anchor, md, onDone) {
    const current = savedFormat();
    const subs = { rich: t("для Word, почты, мессенджеров", "for Word, email, messengers"), markdown: t("с разметкой ** # `", "with ** # ` markup"), plain: t("без разметки", "no markup") };
    openMenu(anchor, { head: t("Копировать как", "Copy as"), radio: true, items: FORMATS.map((f) => ({ label: label(f), sub: subs[f], checked: f === current,
      run: async () => {
        try { localStorage.setItem(FORMAT_KEY, f); } catch (err) { /* private mode */ }
        const ok = await copyMarkdown(md, f);
        if (onDone) onDone(ok);
      } })) });
  }
  document.addEventListener("pointerdown", (e) => { if (menu && !menu.contains(e.target) && e.target !== menu._anchor && !menu._anchor.contains(e.target)) closeFormats(); }, true);

  /* ---------------------------------------------------------------- selection inside an answer */
  const answerBox = () => document.getElementById("qa-answer");
  /* Round 31 (r31-quick): not only the answer — a selection anywhere in the window (the question, command cards, Computer
     Use steps, Explorer change sets, DFY, errors…) gets the bar and the clean Ctrl+C. Fields keep their own copy. */
  const selectableRoot = () => document.getElementById("qa-card");
  function inField(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return !!(el && el.closest && el.closest("input, textarea, [contenteditable=''], [contenteditable='true']"));
  }
  function answerSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    const box = selectableRoot();
    if (!box || !box.contains(range.commonAncestorContainer) || inField(range.commonAncestorContainer)) return null;
    const frag = document.createElement("div");
    frag.appendChild(range.cloneContents());
    const plain = fromNode(frag) || sel.toString().trim();
    if (!plain) return null;
    return { range, plain, html: cleanHtml(frag) };
  }
  let bar = null;
  function hideBar() { if (bar) { bar.remove(); bar = null; } }
  function showBar() {
    const got = answerSelection();
    if (!got) { hideBar(); return; }
    const card = document.getElementById("qa-card");
    if (!card) return;
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "qc-selbar";
      bar.setAttribute("role", "toolbar");
      const icons = (Q() && Q().icons) || {};
      bar.innerHTML = `<button type="button" data-sel="copy" title="Ctrl+C">${icons.copy || ""}<span>${esc(t("Копировать", "Copy"))}</span></button>
        <button type="button" data-sel="ask">${icons.ask || ""}<span>${esc(t("Спросить об этом", "Ask about this"))}</span></button>`;
      bar.addEventListener("pointerdown", (e) => e.preventDefault());   // keep the selection
      bar.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-sel]");
        const cur = answerSelection();
        if (!b || !cur) { hideBar(); return; }
        if (b.dataset.sel === "copy") toast(await write({ plain: cur.plain, html: cur.html }), t("Скопировано", "Copied"));
        else if (Q()) {
          Q().addContext(cur.plain, { name: t("Фрагмент ответа", "Part of the answer") });
          window.getSelection().removeAllRanges();
          Q().setInput(Q().getInput(), { focus: true });
        }
        hideBar();
      });
      card.appendChild(bar);
    }
    // over the selection, else under it — always inside the visible part of the answer (never over the footer)
    const r = got.range.getBoundingClientRect(), c = card.getBoundingClientRect(), b = { width: bar.offsetWidth, height: bar.offsetHeight };  // Round 34: unscaled (qc-pop)
    const ans = answerBox();
    const inAnswer = ans && !ans.hidden && ans.contains(got.range.commonAncestorContainer);
    const view = inAnswer ? ans.getBoundingClientRect() : c;
    const lo = view.top - c.top + 4, hi = view.bottom - c.top - b.height - 4;
    const left = Math.max(8, Math.min(r.left - c.left + r.width / 2 - b.width / 2, c.width - b.width - 8));
    let top = r.top - c.top - b.height - 8;
    if (top < lo) top = r.bottom - c.top + 8;
    bar.style.left = Math.round(left) + "px";
    bar.style.top = Math.round(Math.max(lo, Math.min(top, hi))) + "px";
  }
  document.addEventListener("mouseup", (e) => { if (bar && bar.contains(e.target)) return; setTimeout(showBar, 0); });
  document.addEventListener("keyup", (e) => { if (e.shiftKey || e.key === "Shift") showBar(); });
  document.addEventListener("selectionchange", () => { if (bar && !answerSelection()) hideBar(); });
  document.addEventListener("scroll", hideBar, true);

  /* Ctrl+C on a selected part of an answer: clean text (no «Копировать» of code headers, no caret) + HTML. */
  document.addEventListener("copy", (e) => {
    const got = answerSelection();
    if (!got || !e.clipboardData) return;
    e.preventDefault();
    e.clipboardData.setData("text/plain", got.plain);
    e.clipboardData.setData("text/html", `<meta charset="utf-8">${got.html}`);
    toast(true, t("Скопировано", "Copied"));
  });

  /* Ctrl+C with nothing selected anywhere — the last answer (the field keeps its own Ctrl+C when text is selected). */
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing) return;
    if (e.key === "Escape" && (menu || bar)) { e.preventDefault(); e.stopPropagation(); closeFormats(); hideBar(); return; }
    const mod = e.ctrlKey || e.metaKey;
    if (!mod || e.shiftKey || e.altKey || e.code !== "KeyC") return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && String(sel).trim()) return;          // the browser copies the selection (copy event above)
    const target = e.target;
    if (target && /^(INPUT|TEXTAREA)$/.test(target.tagName) && target.selectionStart !== target.selectionEnd) return;
    if (target && target.isContentEditable) return;
    if (document.documentElement.dataset.qxView) return;               // the clipboard view has its own Ctrl+C
    const st = Q() && Q().state();
    const last = st && [...st.turns].reverse().find((x) => x.done && String(x.a || "").trim());
    if (!last || st.busy) return;
    e.preventDefault();
    copyMarkdown(last.a);
  }, true);

  /* ---------------------------------------------------------------- Round 31 (r31-quick): copy anything */
  /* «Far from everything can be copied»: the question, errors, command cards, Explorer change sets, DFY, Computer Use
     steps and its report had no copy button, and the native window has no context menu (WebView2's own one is off).
     • a quiet «copy» button on every such block (hover / focus; data-nocopy — never part of what is copied);
     • a context menu (right click / the menu key / Shift+F10): Копировать · как простой текст · блок · ответ · код ·
       Выделить всё · Спросить об этом; in fields — Вырезать / Копировать / Вставить / Выделить всё. */
  const BLOCKS = [".qa-q", ".qa-error", ".qa-instant", ".mqc-card", ".qe-card", ".qdfy-card", ".qcu-task", ".qcu-report", ".qcu-steps",
    ".qcu-approve-text", ".qxx-text", ".dfy-cmd-out", ".memu-stuck"].join(", ");
  const COPY_ICON = () => ((Q() && Q().icons && Q().icons.copy) || '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/></svg>');
  function blockText(el) {
    if (!el) return "";
    const clone = el.cloneNode(true);
    clone.querySelectorAll("button, input, textarea, select, svg, img, [data-nocopy], .qc-blk-copy, .qa-dots, kbd").forEach((x) => x.remove());
    const text = fromNode(clone).trim();
    return text || String(el.innerText || el.textContent || "").trim();
  }
  function decorate(root) {
    (root || document).querySelectorAll(BLOCKS).forEach((el) => {
      if (el.querySelector(":scope > .qc-blk-copy") || el.closest(".qc-menu, .qc-selbar")) return;
      if (el.parentElement && el.parentElement.closest(BLOCKS)) return;      // one button per outermost block
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "qc-blk-copy";
      btn.dataset.nocopy = "1";
      btn.title = t("Копировать", "Copy");
      btn.setAttribute("aria-label", t("Копировать текст блока", "Copy the text of this block"));
      btn.innerHTML = COPY_ICON();
      el.classList.add("qc-blk");
      el.appendChild(btn);
    });
  }
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest && e.target.closest(".qc-blk-copy");
    if (!btn) return;
    e.preventDefault(); e.stopPropagation();
    const ok = await write({ plain: blockText(btn.parentElement) });
    toast(ok, t("Скопировано", "Copied"));
    if (ok) { btn.classList.add("done"); setTimeout(() => btn.classList.remove("done"), 1200); }
  }, true);
  let decorateQueued = false;
  const observer = new MutationObserver(() => {
    if (decorateQueued) return;
    decorateQueued = true;
    requestAnimationFrame(() => { decorateQueued = false; decorate(document.getElementById("qa-card")); });
  });
  function watch() {
    const card = document.getElementById("qa-card");
    if (!card) return;
    observer.observe(card, { childList: true, subtree: true });
    decorate(card);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch); else watch();

  /* Round 31 (wave 2): selecting a sortable header's text by dragging ends with a click — the table re-sorted under the
     user's selection. A click that finishes a selection inside the header is not a «sort». */
  document.addEventListener("click", (e) => {
    const th = e.target.closest && e.target.closest("#qa-card th.mt-sortable");
    if (!th) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && String(sel).trim() && (th.contains(sel.anchorNode) || th.contains(sel.focusNode))) e.stopPropagation();
  }, true);

  /* the context menu */
  function selectNode(node) {
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(node);
    sel.removeAllRanges(); sel.addRange(range);
  }
  function turnOf(el) {
    const node = el && el.closest && el.closest(".qa-turn[data-turn]");
    const st = Q() && Q().state();
    if (!node || !st) return null;
    const turn = st.turns[+node.dataset.turn];
    return turn && turn.done && String(turn.a || "").trim() ? { node, turn } : null;
  }
  function fieldMenu(field) {
    const hasSel = field.selectionStart !== field.selectionEnd;
    const ro = field.readOnly || field.disabled;
    const items = [];
    if (hasSel && !ro) items.push({ label: t("Вырезать", "Cut"), keys: ["Ctrl", "X"], run: async () => {
      const v = field.value.slice(field.selectionStart, field.selectionEnd);
      if (await write({ plain: v })) { field.setRangeText("", field.selectionStart, field.selectionEnd, "end"); field.dispatchEvent(new Event("input", { bubbles: true })); }
      field.focus(); } });
    if (hasSel) items.push({ label: t("Копировать", "Copy"), keys: ["Ctrl", "C"], run: async () => { toast(await write({ plain: field.value.slice(field.selectionStart, field.selectionEnd) }), t("Скопировано", "Copied")); field.focus(); } });
    if (!ro) items.push({ label: t("Вставить", "Paste"), keys: ["Ctrl", "V"], run: async () => {
      field.focus();
      let text = "";
      try { text = await navigator.clipboard.readText(); } catch (err) { text = ""; }
      if (text) { field.setRangeText(text, field.selectionStart, field.selectionEnd, "end"); field.dispatchEvent(new Event("input", { bubbles: true })); }
      else if (!document.execCommand("paste") && Q()) Q().toast(t("Нажмите Ctrl+V, чтобы вставить", "Press Ctrl+V to paste"), "", 2200);
    } });
    items.push({ label: t("Выделить всё", "Select all"), keys: ["Ctrl", "A"], run: () => { field.focus(); field.select(); } });
    return items;
  }
  function contentMenu(target) {
    const items = [];
    const got = answerSelection();
    const icons = (Q() && Q().icons) || {};
    if (got) {
      items.push({ label: t("Копировать", "Copy"), keys: ["Ctrl", "C"], icon: icons.copy, run: async () => toast(await write({ plain: got.plain, html: got.html }), t("Скопировано", "Copied")) });
      items.push({ label: t("Копировать простым текстом", "Copy as plain text"), run: async () => toast(await write({ plain: got.plain }), t("Скопировано простым текстом", "Copied as plain text")) });
    }
    const code = target.closest(".code-block-wrap, pre");
    if (code) {
      const pre = code.matches("pre") ? code : code.querySelector("pre");
      if (pre) items.push({ label: t("Копировать код", "Copy the code"), icon: icons.copy, run: async () => toast(await write({ plain: pre.textContent.replace(/\n$/, "") }), t("Код скопирован", "Code copied")) });
    }
    const table = target.closest("table");
    if (table) items.push({ label: t("Копировать таблицу", "Copy the table"), run: async () => {
      const box = document.createElement("div"); box.appendChild(table.cloneNode(true));
      toast(await write({ plain: fromNode(box), html: cleanHtml(box.cloneNode(true)) }), t("Таблица скопирована", "Table copied")); } });
    const turn = turnOf(target);
    if (turn) {
      items.push({ label: t("Копировать ответ", "Copy the answer"), icon: icons.copy, run: () => copyMarkdown(turn.turn.a) });
      items.push({ label: t("Копировать ответ как Markdown", "Copy the answer as Markdown"), run: () => copyMarkdown(turn.turn.a, "markdown") });
    }
    const blk = target.closest(BLOCKS);
    if (blk && !(turn && blk.closest(".qa-md"))) items.push({ label: t("Копировать блок", "Copy this block"), icon: icons.copy, run: async () => toast(await write({ plain: blockText(blk) }), t("Скопировано", "Copied")) });
    const scope = (turn && turn.node.querySelector(".qa-md")) || blk || target.closest(".qa-answer, .qa-slot, .qa-turn");
    if (scope) items.push({ label: t("Выделить всё", "Select all"), keys: ["Ctrl", "A"], run: () => selectNode(scope) });
    if (got && Q()) items.push({ label: t("Спросить об этом", "Ask about this"), icon: icons.ask, run: () => {
      Q().addContext(got.plain, { name: t("Фрагмент", "Fragment") });
      window.getSelection().removeAllRanges();
      Q().setInput(Q().getInput(), { focus: true });
    } });
    return items;
  }
  function openAt(x, y, items) {
    if (!items.length) return false;
    const card = document.getElementById("qa-card") || document.body;
    const c = card.getBoundingClientRect();
    const anchor = document.createElement("span");
    anchor.className = "qc-ctx-anchor";
    anchor.style.cssText = `position:absolute;left:${Math.round(x - c.left)}px;top:${Math.round(y - c.top)}px;width:1px;height:1px;pointer-events:none`;
    card.appendChild(anchor);
    closeFormats();
    openMenu(anchor, { items });
    if (menu) {
      menu.classList.add("qc-ctx");
      const m = { width: menu.offsetWidth, height: menu.offsetHeight };   // Round 34: unscaled by the qc-pop animation
      menu.style.left = Math.max(6, Math.min(x - c.left, c.width - m.width - 6)) + "px";
      menu.style.top = Math.max(6, Math.min(y - c.top, c.height - m.height - 6)) + "px";
    } else anchor.remove();
    return true;
  }
  document.addEventListener("contextmenu", (e) => {
    if (e.defaultPrevented || e.ctrlKey || e.shiftKey) return;              // a plugin's own menu (panels, tiles); Ctrl/Shift — the native one if any
    const card = document.getElementById("qa-card");
    if (!card || !card.contains(e.target) || e.target.closest(".qc-menu, [data-own-menu]")) return;
    const field = e.target.closest("input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]), textarea");
    const items = field ? fieldMenu(field) : contentMenu(e.target);
    if (openAt(e.clientX, e.clientY, items)) e.preventDefault();
  });
  document.addEventListener("keydown", (e) => {
    if (!(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) || e.defaultPrevented) return;
    const sel = window.getSelection();
    const node = sel && sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0).commonAncestorContainer : document.activeElement;
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    const card = document.getElementById("qa-card");
    if (!el || !card || !card.contains(el)) return;
    const field = el.closest("input, textarea");
    const r = (sel && sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0) : el).getBoundingClientRect();
    if (openAt(r.left + 8, r.bottom + 2, field ? fieldMenu(field) : contentMenu(el))) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  window.QuickCopy = { toPlain, toHtml, fromNode, write, copyMarkdown, openFormats, openMenu, closeFormats, savedFormat, FORMATS, label, blockText, decorate };
})();
