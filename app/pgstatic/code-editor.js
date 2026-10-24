/* A small code editor for quick fixes inside the MemuCode panel.
 *
 * A plain <textarea> does the editing, so the browser keeps its native undo
 * stack (Ctrl+Z / Ctrl+Y), IME input, spell-free typing and accessibility.
 * Every programmatic change goes through execCommand("insertText"), which
 * records it in that same stack. A highlighted copy of the text sits behind
 * the transparent textarea; the gutter and the current-line band follow its
 * scroll. It is deliberately not a full IDE: no autocomplete, no LSP.
 */
"use strict";
const MemuCodeEditor = (() => {
  const LINE_COMMENTS = {
    js: "//", mjs: "//", cjs: "//", jsx: "//", ts: "//", tsx: "//", java: "//", c: "//", h: "//",
    cpp: "//", hpp: "//", cs: "//", go: "//", rs: "//", swift: "//", kt: "//", php: "//", dart: "//",
    scss: "//", less: "//", json5: "//",
    py: "#", rb: "#", sh: "#", bash: "#", ps1: "#", yml: "#", yaml: "#", toml: "#", r: "#",
    dockerfile: "#", env: "#", ini: ";", sql: "--", lua: "--", bat: "REM", cmd: "REM",
  };
  const BLOCK_COMMENTS = { css: ["/*", "*/"], html: ["<!--", "-->"], htm: ["<!--", "-->"], xml: ["<!--", "-->"],
    svg: ["<!--", "-->"], vue: ["<!--", "-->"], md: ["<!--", "-->"] };
  const LANGUAGE_NAMES = { js: "JavaScript", mjs: "JavaScript", cjs: "JavaScript", jsx: "JSX", ts: "TypeScript",
    tsx: "TSX", py: "Python", json: "JSON", md: "Markdown", css: "CSS", scss: "SCSS", html: "HTML", htm: "HTML",
    xml: "XML", svg: "SVG", yml: "YAML", yaml: "YAML", toml: "TOML", sh: "Shell", ps1: "PowerShell",
    bat: "Batch", sql: "SQL", go: "Go", rs: "Rust", java: "Java", cs: "C#", c: "C", cpp: "C++", h: "C header",
    rb: "Ruby", php: "PHP", txt: "Plain text", ini: "INI", vue: "Vue", kt: "Kotlin", swift: "Swift" };
  const HIGHLIGHT_LIMIT = 8000; // lines; beyond this the text stays plain to keep typing smooth

  const escape = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  function create(host, options) {
    const opts = options || {};
    const T = opts.t || ((ru) => ru);
    const path = String(opts.path || "");
    const ext = (path.split("/").pop().split(".").pop() || "").toLowerCase();
    const indentUnit = detectIndent(String(opts.text || ""));
    host.innerHTML = `
      <div class="ce" data-own-menu>
        <div class="ce-find" hidden>
          <input class="ce-find-input" type="text" spellcheck="false" placeholder="${escape(T("Найти", "Find"))}" aria-label="${escape(T("Найти", "Find"))}">
          <span class="ce-find-count" aria-live="polite"></span>
          <button type="button" class="ce-find-btn" data-find="prev" title="${escape(T("Предыдущее совпадение — Shift+Enter", "Previous match — Shift+Enter"))}">↑</button>
          <button type="button" class="ce-find-btn" data-find="next" title="${escape(T("Следующее совпадение — Enter", "Next match — Enter"))}">↓</button>
          <input class="ce-replace-input" type="text" spellcheck="false" placeholder="${escape(T("Заменить на", "Replace with"))}" aria-label="${escape(T("Заменить на", "Replace with"))}">
          <button type="button" class="ce-find-btn wide" data-find="replace" title="${escape(T("Заменить это совпадение", "Replace this match"))}">${escape(T("Заменить", "Replace"))}</button>
          <button type="button" class="ce-find-btn wide" data-find="all" title="${escape(T("Заменить все совпадения", "Replace all matches"))}">${escape(T("Все", "All"))}</button>
          <button type="button" class="ce-find-btn" data-find="close" title="${escape(T("Закрыть поиск — Esc", "Close search — Esc"))}">×</button>
        </div>
        <div class="ce-body">
          <div class="ce-gutter" aria-hidden="true"><pre class="ce-numbers"></pre></div>
          <div class="ce-stack">
            <div class="ce-current" aria-hidden="true"></div>
            <pre class="ce-highlight" aria-hidden="true"><code></code></pre>
            <textarea class="ce-input" spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off"
              wrap="off" data-own-menu aria-label="${escape(T("Текст файла ", "File text ") + path)}"></textarea>
          </div>
        </div>
        <div class="ce-status">
          <span class="ce-pos"></span><span class="ce-sel"></span><span class="ce-grow"></span>
          <span title="${escape(T("Язык подсветки", "Highlighting language"))}">${escape(LANGUAGE_NAMES[ext] || ext.toUpperCase() || "Text")}</span>
          <span class="ce-indent" title="${escape(T("Отступ: Tab вставляет его, Shift+Tab убирает", "Indent: Tab inserts it, Shift+Tab removes it"))}">${indentUnit === "\t" ? "Tab" : T(`Пробелы: ${indentUnit.length}`, `Spaces: ${indentUnit.length}`)}</span>
          <span title="${escape(T("Переводы строк сохранятся как в исходном файле", "Line endings are kept as in the original file"))}">${escape(String(opts.eol || "lf").toUpperCase())}</span>
          <span title="${escape(T("Кодировка файла", "File encoding"))}">${escape(String(opts.encoding || "utf-8").toUpperCase())}</span>
        </div>
      </div>`;

    const root = host.querySelector(".ce");
    const area = root.querySelector(".ce-input");
    const code = root.querySelector(".ce-highlight code");
    const pre = root.querySelector(".ce-highlight");
    const numbers = root.querySelector(".ce-numbers");
    const current = root.querySelector(".ce-current");
    const pos = root.querySelector(".ce-pos");
    const sel = root.querySelector(".ce-sel");
    const find = root.querySelector(".ce-find");
    const findInput = root.querySelector(".ce-find-input");
    const replaceInput = root.querySelector(".ce-replace-input");
    const findCount = root.querySelector(".ce-find-count");
    let saved = String(opts.text || "");
    let lineHeight = 20;
    let paintFrame = 0;
    let lastLines = -1;
    area.value = saved;

    /* ---------------------------------- paint --------------------------------- */

    function paint() {
      cancelAnimationFrame(paintFrame);
      paintFrame = requestAnimationFrame(() => {
        const text = area.value;
        const lines = text.split("\n");
        let html;
        if (lines.length <= HIGHLIGHT_LIMIT && window.MemuSyntax) {
          try { html = MemuSyntax.highlight(text, path).join("\n"); } catch (_) { html = escape(text); }
        } else html = escape(text);
        // A trailing newline needs a visible last line, or the layers drift apart.
        code.innerHTML = html + "\n ";
        if (lines.length !== lastLines) {
          lastLines = lines.length;
          numbers.textContent = Array.from({ length: lines.length }, (_, index) => index + 1).join("\n");
        }
        sync();
        caret();
        if (typeof opts.onChange === "function") opts.onChange(isDirty());
      });
    }

    function sync() {
      const x = area.scrollLeft, y = area.scrollTop;
      pre.style.transform = `translate(${-x}px, ${-y}px)`;
      numbers.style.transform = `translateY(${-y}px)`;
      caret();
    }

    function lineOf(offset) {
      return area.value.slice(0, offset).split("\n").length;
    }

    function caret() {
      const start = area.selectionStart, end = area.selectionEnd;
      const line = lineOf(start);
      const column = start - area.value.lastIndexOf("\n", start - 1);
      pos.textContent = T(`Стр ${line}, стлб ${column}`, `Ln ${line}, Col ${column}`);
      const count = end - start;
      sel.textContent = count ? T(` · выделено ${count}`, ` · ${count} selected`) : "";
      current.style.transform = `translateY(${(line - 1) * lineHeight - area.scrollTop + padTop()}px)`;
      current.style.height = `${lineHeight}px`;
      current.hidden = count > 0;
    }

    function padTop() {
      return parseFloat(getComputedStyle(area).paddingTop) || 0;
    }

    function measure() {
      const value = parseFloat(getComputedStyle(area).lineHeight);
      if (value) lineHeight = value;
    }

    /* ------------------------------ edit helpers ------------------------------ */

    function replaceRange(start, end, text, selectStart, selectEnd) {
      area.focus();
      area.setSelectionRange(start, end);
      let done = false;
      try { done = text ? document.execCommand("insertText", false, text) : document.execCommand("delete"); }
      catch (_) { done = false; }
      if (!done) {
        area.setRangeText(text, start, end, "end");
        area.dispatchEvent(new Event("input", { bubbles: true }));
      }
      const caretAt = start + text.length;
      area.setSelectionRange(selectStart ?? caretAt, selectEnd ?? selectStart ?? caretAt);
      paint();
    }

    /** Start/end offsets covering the whole lines of the selection. */
    function selectedLines() {
      const value = area.value;
      let start = area.selectionStart, end = area.selectionEnd;
      const lineStart = value.lastIndexOf("\n", start - 1) + 1;
      if (end > start && value[end - 1] === "\n") end -= 1;
      let lineEnd = value.indexOf("\n", end);
      if (lineEnd < 0) lineEnd = value.length;
      return { start: lineStart, end: lineEnd, text: value.slice(lineStart, lineEnd) };
    }

    function indent(outdent) {
      const { start, end, text } = selectedLines();
      const single = area.selectionStart === area.selectionEnd && !outdent;
      if (single) return replaceRange(area.selectionStart, area.selectionEnd, indentUnit);
      const lines = text.split("\n");
      const changed = lines.map((line) => {
        if (!outdent) return indentUnit + line;
        if (line.startsWith(indentUnit)) return line.slice(indentUnit.length);
        return line.replace(/^( {1,4}|\t)/, "");
      }).join("\n");
      replaceRange(start, end, changed, start, start + changed.length);
    }

    function toggleComment() {
      const { start, end, text } = selectedLines();
      const block = BLOCK_COMMENTS[ext];
      const token = LINE_COMMENTS[ext];
      let changed;
      if (token) {
        const lines = text.split("\n");
        const commented = lines.filter((line) => line.trim()).every((line) => line.trimStart().startsWith(token));
        const minIndent = Math.min(...lines.filter((line) => line.trim()).map((line) => line.match(/^\s*/)[0].length), Infinity);
        changed = lines.map((line) => {
          if (!line.trim()) return line;
          if (commented) return line.replace(new RegExp(`^(\\s*)${token.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")} ?`), "$1");
          const at = Number.isFinite(minIndent) ? minIndent : 0;
          return line.slice(0, at) + token + " " + line.slice(at);
        }).join("\n");
      } else if (block) {
        const trimmed = text.trim();
        const [open, close] = block;
        if (trimmed.startsWith(open) && trimmed.endsWith(close)) {
          changed = text.replace(open + " ", "").replace(open, "").replace(new RegExp(`\\s?${close.replace(/[*/]/g, "\\$&")}(\\s*)$`), "$1");
        } else {
          const lead = text.match(/^\s*/)[0];
          changed = `${lead}${open} ${text.slice(lead.length)} ${close}`;
        }
      } else return;
      replaceRange(start, end, changed, start, start + changed.length);
    }

    function duplicateLines() {
      const { start, end, text } = selectedLines();
      replaceRange(end, end, "\n" + text, end + 1, end + 1 + text.length);
    }

    function moveLines(direction) {
      const value = area.value;
      const { start, end, text } = selectedLines();
      if (direction < 0) {
        if (start === 0) return;
        const prevStart = value.lastIndexOf("\n", start - 2) + 1;
        const prev = value.slice(prevStart, start - 1);
        replaceRange(prevStart, end, text + "\n" + prev, prevStart, prevStart + text.length);
      } else {
        if (end >= value.length) return;
        let nextEnd = value.indexOf("\n", end + 1);
        if (nextEnd < 0) nextEnd = value.length;
        const next = value.slice(end + 1, nextEnd);
        const moved = start + next.length + 1;
        replaceRange(start, nextEnd, next + "\n" + text, moved, moved + text.length);
      }
    }

    function newline() {
      const value = area.value;
      const at = area.selectionStart;
      const lineStart = value.lastIndexOf("\n", at - 1) + 1;
      const line = value.slice(lineStart, at);
      let lead = line.match(/^\s*/)[0];
      if (/[{[(]\s*$/.test(line) || (ext === "py" && /:\s*$/.test(line))) lead += indentUnit;
      replaceRange(at, area.selectionEnd, "\n" + lead);
    }

    function goToLine(number) {
      const lines = area.value.split("\n");
      const target = Math.max(1, Math.min(lines.length, Number(number) || 1));
      const offset = lines.slice(0, target - 1).reduce((sum, line) => sum + line.length + 1, 0);
      area.focus();
      area.setSelectionRange(offset, offset + lines[target - 1].length);
      area.scrollTop = Math.max(0, (target - 5) * lineHeight);
      sync();
    }

    /* ---------------------------------- find ---------------------------------- */

    function matches() {
      const needle = findInput.value;
      if (!needle) return [];
      const hay = area.value.toLowerCase(), lower = needle.toLowerCase();
      const found = [];
      let at = hay.indexOf(lower);
      while (at >= 0 && found.length < 10000) { found.push(at); at = hay.indexOf(lower, at + Math.max(1, lower.length)); }
      return found;
    }

    function findStep(direction) {
      const list = matches();
      if (!list.length) { findCount.textContent = findInput.value ? T("нет", "none") : ""; return false; }
      const from = direction < 0 ? area.selectionStart - 1 : area.selectionEnd;
      let index = direction < 0 ? list.findLastIndex((at) => at <= from) : list.findIndex((at) => at >= from);
      if (index < 0) index = direction < 0 ? list.length - 1 : 0;
      const at = list[index];
      area.setSelectionRange(at, at + findInput.value.length);
      const line = lineOf(at);
      if ((line - 1) * lineHeight < area.scrollTop || line * lineHeight > area.scrollTop + area.clientHeight) {
        area.scrollTop = Math.max(0, (line - 4) * lineHeight);
      }
      findCount.textContent = `${index + 1} / ${list.length}`;
      sync();
      return true;
    }

    function openFind(withReplace) {
      find.hidden = false;
      root.classList.toggle("ce-replace-open", !!withReplace);
      const picked = area.value.slice(area.selectionStart, area.selectionEnd);
      if (picked && !picked.includes("\n")) findInput.value = picked;
      findInput.focus();
      findInput.select();
      const list = matches();
      findCount.textContent = findInput.value ? (list.length ? `${list.length}` : T("нет", "none")) : "";
    }

    function closeFind() {
      find.hidden = true;
      area.focus();
    }

    function replaceOne() {
      const needle = findInput.value;
      if (!needle) return;
      const selected = area.value.slice(area.selectionStart, area.selectionEnd);
      if (selected.toLowerCase() === needle.toLowerCase()) {
        replaceRange(area.selectionStart, area.selectionEnd, replaceInput.value);
      }
      findStep(1);
    }

    function replaceAll() {
      const needle = findInput.value;
      if (!needle) return;
      const list = matches();
      if (!list.length) return;
      let result = "", last = 0;
      list.forEach((at) => { result += area.value.slice(last, at) + replaceInput.value; last = at + needle.length; });
      result += area.value.slice(last);
      replaceRange(0, area.value.length, result, 0, 0);
      findCount.textContent = T(`заменено: ${list.length}`, `replaced: ${list.length}`);
    }

    /* ---------------------------------- menu ---------------------------------- */

    async function paste() {
      try {
        const text = await navigator.clipboard.readText();
        if (text) replaceRange(area.selectionStart, area.selectionEnd, text.replace(/\r\n?/g, "\n"));
      } catch (_) {
        if (typeof toast === "function") toast(T("Буфер обмена недоступен — используйте Ctrl+V", "The clipboard is unavailable — use Ctrl+V"), "warn");
      }
    }

    function menu(event) {
      event.preventDefault();
      event.stopPropagation();
      if (typeof showContextMenu !== "function") return;
      const picked = area.value.slice(area.selectionStart, area.selectionEnd);
      const commentable = !!(LINE_COMMENTS[ext] || BLOCK_COMMENTS[ext]);
      showContextMenu([
        { label: T("Отменить", "Undo"), hint: "Ctrl+Z", icon: "undo", run: () => { area.focus(); document.execCommand("undo"); paint(); } },
        { label: T("Повторить", "Redo"), hint: "Ctrl+Y", icon: "redo", run: () => { area.focus(); document.execCommand("redo"); paint(); } },
        { sep: true },
        picked ? { label: T("Вырезать", "Cut"), icon: "scissors", run: async () => {
          try { await navigator.clipboard.writeText(picked); } catch (_) {}
          replaceRange(area.selectionStart, area.selectionEnd, "");
        } } : null,
        picked ? { label: T("Копировать", "Copy"), icon: "copy", run: () => { if (typeof copyText === "function") copyText(picked); } } : null,
        { label: T("Вставить", "Paste"), icon: "clipboard", run: paste },
        { label: T("Выделить всё", "Select all"), icon: "text", run: () => { area.focus(); area.select(); caret(); } },
        { sep: true },
        commentable ? { label: T("Закомментировать строки", "Toggle comment"), hint: "Ctrl+/", icon: "text", run: toggleComment } : null,
        { label: T("Дублировать строку", "Duplicate line"), hint: "Ctrl+D", icon: "copy", run: duplicateLines },
        { label: T("Сдвинуть вправо", "Indent"), hint: "Tab", icon: "forward", run: () => indent(false) },
        { label: T("Сдвинуть влево", "Outdent"), hint: "Shift+Tab", icon: "back", run: () => indent(true) },
        { sep: true },
        { label: T("Найти", "Find"), hint: "Ctrl+F", icon: "search", run: () => openFind(false) },
        { label: T("Найти и заменить", "Find and replace"), hint: "Ctrl+H", icon: "search", run: () => openFind(true) },
        { label: T("Перейти к строке", "Go to line"), hint: "Ctrl+G", icon: "target", run: askLine },
        { sep: true },
        { label: T("Сохранить", "Save"), hint: "Ctrl+S", icon: "check", run: () => opts.onSave && opts.onSave() },
        isDirty() ? { label: T("Вернуть как было при открытии", "Revert to the opened version"), icon: "undo", danger: true, run: revert } : null,
      ].filter(Boolean), event.clientX, event.clientY);
    }

    function askLine() {
      openFind(false);
      findInput.value = "";
      findInput.placeholder = T("Номер строки и Enter", "Line number and Enter");
      findInput.dataset.mode = "line";
      findCount.textContent = T(`из ${area.value.split("\n").length}`, `of ${area.value.split("\n").length}`);
    }

    function revert() {
      replaceRange(0, area.value.length, saved, 0, 0);
    }

    /* --------------------------------- events --------------------------------- */

    area.addEventListener("input", paint);
    area.addEventListener("scroll", sync, { passive: true });
    ["keyup", "click", "select", "focus"].forEach((name) => area.addEventListener(name, caret));
    area.addEventListener("contextmenu", menu);
    area.addEventListener("keydown", (event) => {
      const ctrl = event.ctrlKey || event.metaKey;
      const code = event.code;
      if (ctrl && !event.shiftKey && code === "KeyS") { event.preventDefault(); if (opts.onSave) opts.onSave(); return; }
      if (ctrl && code === "KeyF") { event.preventDefault(); openFind(false); return; }
      if (ctrl && code === "KeyH") { event.preventDefault(); openFind(true); return; }
      if (ctrl && code === "KeyG") { event.preventDefault(); askLine(); return; }
      if (ctrl && (code === "Slash" || code === "NumpadDivide")) { event.preventDefault(); toggleComment(); return; }
      if (ctrl && !event.shiftKey && code === "KeyD") { event.preventDefault(); duplicateLines(); return; }
      if (event.altKey && (code === "ArrowUp" || code === "ArrowDown")) { event.preventDefault(); moveLines(code === "ArrowUp" ? -1 : 1); return; }
      if (code === "Tab" && !ctrl && !event.altKey) { event.preventDefault(); indent(event.shiftKey); return; }
      if (event.key === "Enter" && !ctrl && !event.shiftKey && !event.altKey && !event.isComposing) { event.preventDefault(); newline(); return; }
      if (event.key === "F3") { event.preventDefault(); if (find.hidden) openFind(false); else findStep(event.shiftKey ? -1 : 1); return; }
      if (event.key === "Escape" && !find.hidden) { event.preventDefault(); event.stopPropagation(); closeFind(); }
    });
    findInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        if (findInput.dataset.mode === "line") {
          goToLine(findInput.value);
          delete findInput.dataset.mode;
          findInput.placeholder = T("Найти", "Find");
          find.hidden = true;
          return;
        }
        findStep(event.shiftKey ? -1 : 1);
      } else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeFind(); }
    });
    findInput.addEventListener("input", () => {
      if (findInput.dataset.mode === "line") return;
      const list = matches();
      findCount.textContent = findInput.value ? (list.length ? `${list.length}` : T("нет", "none")) : "";
    });
    replaceInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); if (event.ctrlKey) replaceAll(); else replaceOne(); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeFind(); }
    });
    find.addEventListener("click", (event) => {
      const button = event.target.closest("[data-find]");
      if (!button) return;
      const action = button.dataset.find;
      if (action === "next") findStep(1);
      else if (action === "prev") findStep(-1);
      else if (action === "replace") replaceOne();
      else if (action === "all") replaceAll();
      else closeFind();
    });

    measure();
    paint();
    requestAnimationFrame(() => { measure(); area.focus(); area.setSelectionRange(0, 0); caret(); });

    function isDirty() { return area.value !== saved; }

    return {
      get value() { return area.value; },
      isDirty,
      markSaved(text) { saved = text === undefined ? area.value : text; paint(); },
      replaceAll(text) { replaceRange(0, area.value.length, text, 0, 0); },
      goToLine,
      focus() { area.focus(); },
      element: root,
    };
  }

  function detectIndent(text) {
    const lines = text.split("\n").slice(0, 400);
    const tabs = lines.filter((line) => line.startsWith("\t")).length;
    const spaces = lines.map((line) => (line.match(/^ +/) || [""])[0].length).filter((width) => width > 0);
    if (tabs > spaces.length) return "\t";
    if (!spaces.length) return "  ";
    const four = spaces.filter((width) => width % 4 === 0).length;
    const two = spaces.filter((width) => width % 2 === 0).length;
    return two > four * 1.5 ? "  " : four >= spaces.length * 0.8 ? "    " : "  ";
  }

  return Object.freeze({ create, detectIndent });
})();
window.MemuCodeEditor = MemuCodeEditor;
