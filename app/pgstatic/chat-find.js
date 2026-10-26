

(function () {
  "use strict";
  if (window.MemuChatFind) return;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    bookmark: '<path d="M6 4h12v17l-6-4-6 4z"/>',
  };
  const svg = (name, filled) => `<svg viewBox="0 0 24 24" aria-hidden="true"${filled ? ' class="filled"' : ""}>${SVG[name]}</svg>`;
  const chatBox = () => document.getElementById("chat");
  const canHighlight = () => typeof CSS !== "undefined" && CSS.highlights && typeof Highlight === "function";
  const visibleText = (m) => {
    const raw = String(m?.content || "");
    return (typeof visibleAnswer === "function" && m?.role === "assistant" ? visibleAnswer(raw) : raw)
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
      .replace(/(\*\*|__|\*|_|~~|`)(?=\S)([^\n]*?\S)\1/g, "$2")
      .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\s+/g, " ").trim();
  };
  const fold = (s) => s.toLowerCase().replace(/ё/g, "е");

  const key = (m) => (m ? String(m.id || `${m.role || "?"}:${m.createdAt || 0}`) : "");
  const chatOf = (id) => (typeof S !== "undefined" ? (S.chats || []).find((c) => c.id === (id || S.currentId)) : null);

  let memo = null;
  const openChatObj = () => {
    if (!memo) { memo = { chat: chatOf() }; queueMicrotask(() => { memo = null; }); }
    return memo.chat;
  };
  const has = (m) => {
    const chat = openChatObj();
    return !!(chat && chat.bookmarks && m && chat.bookmarks[key(m)]);
  };
  const cls = (m) => (has(m) ? " msg-bm" : "");
  function toolButton(i) {
    const m = typeof S !== "undefined" ? S.messages[i] : null;
    if (!m || m.internal) return "";
    const on = has(m);
    const label = on ? L("Убрать из закладок", "Remove bookmark") : L("В закладки", "Bookmark");
    return `<button class="msg-tool cbm-tool${on ? " on" : ""}" data-bookmark="${i}" aria-pressed="${on}" aria-label="${label}" title="${label}">${svg("bookmark", on)}</button>`;
  }
  function toggle(i) {
    const m = S.messages[i], chat = chatOf();
    if (!m) return;
    if (!chat) { toast(L("Сначала отправьте сообщение — пустой чат не сохраняется", "Send a message first — an empty chat is not saved"), "warn"); return; }
    const k = key(m), map = { ...(chat.bookmarks || {}) };
    const added = !map[k];
    if (added) map[k] = { at: Date.now(), role: m.role, text: visibleText(m).slice(0, 160) };
    else delete map[k];
    if (Object.keys(map).length) chat.bookmarks = map; else delete chat.bookmarks;
    if (typeof saveLocalChats === "function") saveLocalChats();
    renderMessages();
    toast(added ? L("Добавлено в закладки — список: Ctrl+F → ★", "Bookmarked — list: Ctrl+F → ★") : L("Закладка убрана", "Bookmark removed"), "ok");
    if (state.bar) drawCount();
  }
  function list(chat = chatOf()) {
    if (!chat || !chat.bookmarks) return [];
    const msgs = chat.id === S.currentId ? S.messages : (chat.messages || []);
    return Object.entries(chat.bookmarks).map(([k, b]) => {
      const index = msgs.findIndex((m) => key(m) === k);
      return { key: k, index, at: +b.at || 0, role: b.role || msgs[index]?.role || "assistant",
        text: (index >= 0 ? visibleText(msgs[index]).slice(0, 160) : "") || b.text || "" };
    }).sort((a, b) => (a.index < 0) - (b.index < 0) || a.index - b.index);
  }
  async function jump(chatId, k) {
    if (S.currentId !== chatId) await openChat(chatId);
    if (S.currentId !== chatId) return;
    let index = S.messages.findIndex((m) => key(m) === k);
    if (index < 0 && typeof MemuBranches !== "undefined" && !/:/.test(k)) {
      MemuBranches.revealMessage(k, { scroll: false });
      index = S.messages.findIndex((m) => key(m) === k);
    }
    if (index < 0) { toast(L("Сообщение из закладки не найдено — возможно, оно удалено", "The bookmarked message was not found — it may have been deleted"), "warn"); return; }
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const node = document.querySelector(`#messages > [data-msg="${index}"]`);
    if (!node) return;
    node.style.contentVisibility = "visible";

    const center = () => node.isConnected && node.scrollIntoView({ block: "center" });
    center(); requestAnimationFrame(() => { center(); requestAnimationFrame(center); });
    node.classList.remove("cbm-flash"); void node.offsetWidth; node.classList.add("cbm-flash");
    setTimeout(() => node.classList.remove("cbm-flash"), 1400);
  }

  document.addEventListener("click", (event) => {
    const b = event.target instanceof Element ? event.target.closest("#messages [data-bookmark]") : null;
    if (!b) return;
    event.preventDefault(); event.stopPropagation();
    toggle(+b.dataset.bookmark);
  });

  const state = { bar: null, ranges: [], pos: -1, q: "", timer: 0, ro: null, pop: false };

  const OPTS_KEY = "memu_find_opts";
  const opts = (() => { try { const o = JSON.parse(localStorage.getItem(OPTS_KEY) || "{}"); return { cs: !!o.cs, word: !!o.word }; } catch (e) { return { cs: false, word: false }; } })();
  const WORD_CH = /[\p{L}\p{N}_]/u;
  const isWordAt = (text, at, len) => !WORD_CH.test(text.charAt(at - 1) || " ") && !WORD_CH.test(text.charAt(at + len) || " ");

  function collect(q) {
    const out = [];
    const needle = opts.cs ? q : fold(q);
    if (!needle) return out;
    const skip = "button, .msg-tools, time, svg, style, script, .msg-progress, .cbm-pop, [hidden]";
    for (const msg of document.querySelectorAll("#messages > .msg")) {
      const walker = document.createTreeWalker(msg, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => (n.parentElement?.closest(skip) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
      });
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const hay = opts.cs ? n.data : fold(n.data);
        let at = hay.indexOf(needle);
        while (at !== -1) {
          if (!opts.word || isWordAt(hay, at, needle.length)) {
            const r = new Range(); r.setStart(n, at); r.setEnd(n, at + needle.length);
            out.push(r);
            if (out.length >= 2000) return out;
            at = hay.indexOf(needle, at + needle.length);
          } else at = hay.indexOf(needle, at + 1);
        }
      }
    }
    return out;
  }
  function paint() {
    if (!canHighlight()) return;
    CSS.highlights.delete("memu-find"); CSS.highlights.delete("memu-find-current");
    if (!state.ranges.length) return;
    CSS.highlights.set("memu-find", new Highlight(...state.ranges));
    const cur = state.ranges[state.pos];
    if (cur) CSS.highlights.set("memu-find-current", new Highlight(cur));
  }
  function drawCount() {
    const n = state.bar?.querySelector(".cfind-n");
    if (!n) return;
    n.textContent = state.q ? (state.ranges.length ? `${state.pos + 1}/${state.ranges.length}${state.ranges.length >= 2000 ? "+" : ""}` : "0/0") : "";
    state.bar.classList.toggle("none", !!state.q && !state.ranges.length);
    const count = list().length;
    const bm = state.bar.querySelector("#cfind-bm");
    bm.querySelector("b").textContent = count ? String(count) : "";
    bm.classList.toggle("has", count > 0);
  }
  function run(keepPos = false) {
    state.q = state.bar?.querySelector("input").value || "";
    const old = state.ranges[state.pos];
    state.ranges = collect(state.q.trim() ? state.q : "");
    if (keepPos && old) {
      const at = state.ranges.findIndex((r) => r.startContainer === old.startContainer && r.startOffset === old.startOffset);
      state.pos = at >= 0 ? at : 0;
    } else {

      const top = chatBox()?.getBoundingClientRect().top || 0;
      const at = state.ranges.findIndex((r) => r.getBoundingClientRect().bottom >= top + 8);
      state.pos = state.ranges.length ? (at >= 0 ? at : 0) : -1;
    }
    paint(); drawCount();
    if (state.pos >= 0) reveal(false);
  }
  function stale() { return state.ranges.some((r) => !r.startContainer.isConnected); }
  function step(dir) {
    if (stale() || !state.ranges.length) run(true);
    if (!state.ranges.length) return;
    state.pos = (state.pos + dir + state.ranges.length) % state.ranges.length;
    paint(); drawCount(); reveal(true);
  }
  function reveal(force) {
    const r = state.ranges[state.pos];
    const box = chatBox();
    if (!r || !box) return;
    const msg = r.startContainer.parentElement?.closest("#messages > .msg");
    if (msg) {
      msg.style.contentVisibility = "visible";
      msg.querySelectorAll(".user-text.collapsed").forEach((el) => el.classList.remove("collapsed"));
    }
    const view = box.getBoundingClientRect();
    const barBottom = state.bar ? state.bar.getBoundingClientRect().bottom : view.top;
    const rect = r.getBoundingClientRect();
    if (!force && rect.top >= barBottom + 8 && rect.bottom <= view.bottom - 24) return;

    const go = () => { if (r.startContainer.isConnected) box.scrollTop += r.getBoundingClientRect().top - view.top - view.height / 2.4; };
    go(); requestAnimationFrame(() => { go(); requestAnimationFrame(go); });
  }
  function place() {
    const bar = state.bar, rect = chatBox()?.getBoundingClientRect();
    if (!bar || !rect || !rect.width) return;

    bar.style.top = `${Math.max(8, rect.top + 10)}px`;
    bar.style.right = `${Math.max(8, window.innerWidth - rect.right + 16)}px`;
    bar.style.maxWidth = `${Math.max(280, rect.width - 32)}px`;
  }
  function open(query) {
    if (typeof S === "undefined") return;
    if (!state.bar) build();
    const input = state.bar.querySelector("input");
    const sel = String(window.getSelection?.() || "").trim();
    if (typeof query === "string") input.value = query;
    else if (sel && sel.length <= 80 && !sel.includes("\n")) input.value = sel;
    place();
    input.focus(); input.select();
    run();
  }
  function build() {
    const bar = document.createElement("div");
    bar.className = "cfind";
    bar.setAttribute("role", "search");
    bar.setAttribute("aria-label", L("Найти в этом чате", "Find in this chat"));
    bar.innerHTML = `<span class="cfind-ico">${svg("search")}</span>
      <input type="text" spellcheck="false" autocomplete="off" placeholder="${esc(L("Найти в этом чате", "Find in this chat"))}" aria-label="${esc(L("Найти в этом чате", "Find in this chat"))}">
      <span class="cfind-n" aria-live="polite"></span>
      <button type="button" class="cfind-opt" data-cf="cs" aria-pressed="${opts.cs}" aria-label="${esc(L("Учитывать регистр", "Match case"))}" title="${esc(L("Учитывать регистр (Alt+C)", "Match case (Alt+C)"))}"><span class="cfind-aa">Aa</span></button>
      <button type="button" class="cfind-opt" data-cf="word" aria-pressed="${opts.word}" aria-label="${esc(L("Слово целиком", "Whole word"))}" title="${esc(L("Слово целиком (Alt+W)", "Whole word (Alt+W)"))}"><span class="cfind-ab">ab</span></button>
      <button type="button" data-cf="prev" aria-label="${esc(L("Предыдущее", "Previous"))}" title="${esc(L("Предыдущее (Shift+Enter)", "Previous (Shift+Enter)"))}">${svg("up")}</button>
      <button type="button" data-cf="next" aria-label="${esc(L("Следующее", "Next"))}" title="${esc(L("Следующее (Enter)", "Next (Enter)"))}">${svg("down")}</button>
      <button type="button" id="cfind-bm" data-cf="bm" aria-haspopup="true" aria-expanded="false" aria-label="${esc(L("Закладки этого чата", "This chat's bookmarks"))}" title="${esc(L("Закладки этого чата", "This chat's bookmarks"))}">${svg("bookmark")}<b></b></button>
      <button type="button" data-cf="close" aria-label="${esc(L("Закрыть (Esc)", "Close (Esc)"))}" title="${esc(L("Закрыть (Esc)", "Close (Esc)"))}">${svg("close")}</button>
      <div class="cbm-pop" hidden role="menu"></div>`;
    const input = bar.querySelector("input");
    input.addEventListener("input", () => { clearTimeout(state.timer); state.timer = setTimeout(() => run(), 140); });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); clearTimeout(state.timer); if (state.q !== input.value) run(); else step(event.shiftKey ? -1 : 1); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (state.pop) togglePop(false); else close(); }
      else if (event.key === "ArrowDown" && state.pop) { event.preventDefault(); bar.querySelector(".cbm-pop button")?.focus(); }
      else if (event.altKey && !event.ctrlKey && (event.code === "KeyC" || event.code === "KeyW")) { event.preventDefault(); setOpt(event.code === "KeyC" ? "cs" : "word"); }
    });
    bar.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && event.target !== input) { event.preventDefault(); event.stopPropagation(); togglePop(false); input.focus(); }
    });
    bar.addEventListener("click", (event) => {
      const b = event.target.closest("[data-cf], [data-cbm]");
      if (!b) return;
      if (b.dataset.cbm !== undefined) { togglePop(false); jump(S.currentId, b.dataset.cbm); return; }
      const what = b.dataset.cf;
      if (what === "prev") step(-1);
      else if (what === "next") step(1);
      else if (what === "bm") togglePop(!state.pop);
      else if (what === "cs" || what === "word") { setOpt(what); input.focus(); }
      else close();
    });
    (document.getElementById("chat-view-wrap") || document.body).appendChild(bar);
    state.bar = bar;
    if (typeof ResizeObserver === "function" && chatBox()) { state.ro = new ResizeObserver(() => requestAnimationFrame(place)); state.ro.observe(chatBox()); }
  }
  function setOpt(name) {
    opts[name] = !opts[name];
    try { localStorage.setItem(OPTS_KEY, JSON.stringify(opts)); } catch (e) {}
    state.bar?.querySelector(`[data-cf="${name}"]`)?.setAttribute("aria-pressed", String(opts[name]));
    clearTimeout(state.timer);
    run();
  }
  function togglePop(on) {
    const pop = state.bar?.querySelector(".cbm-pop");
    if (!pop) return;
    state.pop = on;
    state.bar.querySelector("#cfind-bm").setAttribute("aria-expanded", String(on));
    if (!on) { pop.hidden = true; return; }
    const rows = list();
    pop.innerHTML = rows.length
      ? `<div class="cbm-h">${esc(L("Закладки этого чата", "This chat's bookmarks"))}<small>${rows.length}</small></div>` + rows.map((r) => `
        <button type="button" role="menuitem" data-cbm="${esc(r.key)}"${r.index < 0 ? " disabled" : ""}>
          <span class="cbm-role ${r.role === "user" ? "u" : "a"}">${esc(r.role === "user" ? L("Вы", "You") : "MemuAI")}</span>
          <span class="cbm-text">${esc(r.text || L("(без текста)", "(no text)"))}</span></button>`).join("")
      : `<div class="cbm-empty"><b>${esc(L("Закладок пока нет", "No bookmarks yet"))}</b>
          <span>${esc(L("Наведите на сообщение и нажмите ☆ — оно появится здесь и в поиске Ctrl+K.", "Hover a message and press ☆ — it will show up here and in Ctrl+K search."))}</span></div>`;
    pop.hidden = false;
    pop.addEventListener("keydown", popKeys);
  }
  function popKeys(event) {
    const items = [...event.currentTarget.querySelectorAll("button:not(:disabled)")];
    const at = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = items[(at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
      if (at === 0 && event.key === "ArrowUp") state.bar.querySelector("input").focus(); else next?.focus();
    }
  }
  function close() {
    if (!state.bar) return;
    clearTimeout(state.timer);
    if (canHighlight()) { CSS.highlights.delete("memu-find"); CSS.highlights.delete("memu-find-current"); }
    state.ro?.disconnect();
    state.bar.remove();
    Object.assign(state, { bar: null, ranges: [], pos: -1, q: "", ro: null, pop: false });
    document.getElementById("input")?.focus({ preventScroll: true });
  }

  document.addEventListener("memu:chat-open", () => {
    if (!state.bar) return;
    togglePop(false);

    requestAnimationFrame(() => run());
  });
  window.addEventListener("resize", () => { if (state.bar) requestAnimationFrame(place); }, { passive: true });

  const chatVisible = () => {
    const box = chatBox();
    return !!(box && box.offsetParent !== null && !document.querySelector("#panel-back.visible") && !document.documentElement.classList.contains("msx-open"));
  };
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.isComposing) return;
    const match = window.MemuHotkeys ? MemuHotkeys.match("find_in_chat", event, "Ctrl+F")
      : (event.ctrlKey && !event.altKey && !event.shiftKey && event.code === "KeyF");
    if (match) {
      if (!chatVisible() || event.target?.closest?.(".cm-editor, .code-editor, .docs-root")) return;
      event.preventDefault(); open();
      return;
    }
    if (state.bar && event.key === "F3") { event.preventDefault(); step(event.shiftKey ? -1 : 1); }
  });

  function registerSearch() {
    if (!window.MemuSearch?.registerProvider) return;
    MemuSearch.registerProvider({
      id: "bookmarks", type: "bookmark", label: ["Закладки", "Bookmarks"], icon: SVG.bookmark,
      search(q, ctx) {
        const query = String(q || "").trim();
        const words = L("закладки закладка избранное bookmarks", "bookmarks bookmark saved закладки");
        const rows = [];
        for (const chat of S.chats || []) {
          if (!chat.bookmarks) continue;
          for (const r of list(chat)) {
            const title = r.text || L("(без текста)", "(no text)");
            const score = ctx.score(query, title + " " + (chat.title || ""), words);
            if (!score) continue;
            rows.push({ id: `bookmark:${chat.id}:${r.key}`, title: title.slice(0, 120),
              subtitle: `${chat.title || L("Без названия", "Untitled")} · ${r.role === "user" ? L("вы", "you") : "MemuAI"}`,
              snippet: title.length > 120 ? title : "", score: Math.min(1, score + 0.05), ts: r.at,
              _chat: chat.id, _key: r.key });
          }
        }
        return rows;
      },
      open(item) { MemuSearch.close(); setTimeout(() => jump(item._chat, item._key), 40); },
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(registerSearch, 0), { once: true });
  else setTimeout(registerSearch, 0);

  window.MemuBookmarks = Object.freeze({ key, has, cls, toolButton, toggle, list, jump });
  window.MemuChatFind = Object.freeze({ open, close, isOpen: () => !!state.bar, options: () => ({ ...opts }), _state: state });
})();
