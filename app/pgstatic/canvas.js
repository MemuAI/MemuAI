

(function () {
  "use strict";
  const lang = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en") ? "en" : "ru";
  const L = (ru, en) => lang() === "en" ? en : ru;
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const TOOL_RE = /^canvas_(?:create|edit|read|write_file)$/;
  const ICONS = {
    document: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
    code: '<path d="m9 8-4 4 4 4"/><path d="m15 8 4 4-4 4"/>',
    html: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M7 6.5h.01M10 6.5h.01"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16M15 4v16"/>',
    email: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
    download: '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
    more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    check: '<path d="m5 12.5 4.2 4.2L19 7"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    reload: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    expand: '<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/>',
    canvas: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M14 4v16"/><path d="M17 8h1M17 11h1"/>',
    note: '<path d="M5 4h10l4 4v12H5z"/><path d="M9 12h6M9 16h4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    module: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><path d="M16.5 13v7M13 16.5h7"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    terminal: '<path d="m5 8 4 4-4 4"/><path d="M12 17h7"/>',
    diagram: '<rect x="3" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="16" width="7" height="5" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><path d="M6.5 8v5.5h11V16M10 5.5h4"/>',
    slides: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v3M8 21h8"/><path d="M7 9h6M7 12h9"/>',
    file: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    warn: '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/>',
    sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
  };
  const ico = (name, cls = "") => `<svg class="cv-i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  const TYPE_LABEL = {
    document: ["Документ", "Document"], code: ["Код", "Code"], html: ["Страница", "Web page"],
    table: ["Таблица", "Table"], email: ["Письмо", "Email"], diagram: ["Схема", "Diagram"], slides: ["Презентация", "Slides"],
  };
  const typeLabel = (t) => { const row = TYPE_LABEL[t] || TYPE_LABEL.document; return L(row[0], row[1]); };
  const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; " +
    "font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none'; worker-src 'none'; " +
    "form-action 'none'; base-uri 'none'; object-src 'none'";

  const MARK = "\u27e6file: ";
  function unpackFiles(text) {
    const src = String(text || "");
    if (!src.startsWith(MARK)) return null;
    const marks = [...src.matchAll(/^\u27e6file: (.+?)\u27e7$/gm)];
    if (!marks.length || marks[0].index !== 0) return null;
    return marks.map((m, n) => {
      let start = m.index + m[0].length;
      if (src[start] === "\n") start += 1;
      const end = n + 1 < marks.length ? marks[n + 1].index - 1 : src.length;
      return { path: m[1], content: src.slice(start, Math.max(start, end)) };
    });
  }
  const packFiles = (files) => files.map((f) => `${MARK}${f.path}\u27e7\n${f.content}`).join("\n");
  const extOfPath = (path) => { const base = String(path || "").split("/").pop(); return base.includes(".") ? base.split(".").pop().toLowerCase() : ""; };
  const LANG_OF_EXT = { js: "javascript", mjs: "javascript", ts: "typescript", py: "python", css: "css", html: "html", htm: "html",
    json: "json", md: "markdown", sh: "bash", sql: "sql", yml: "yaml", yaml: "yaml", xml: "xml", svg: "xml", mmd: "mermaid" };
  const isWebFiles = (files) => !!files && files.some((f) => /\.(html?|m?js)$/i.test(f.path));
  const KINDS_URL = "/canvas-kinds.js?v=1.0-r40";
  let kindsPromise = null;

  function kinds() {
    if (window.MemuCanvasKinds) return Promise.resolve(window.MemuCanvasKinds);
    if (kindsPromise) return kindsPromise;
    kindsPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = KINDS_URL;
      script.onload = () => (window.MemuCanvasKinds ? resolve(window.MemuCanvasKinds) : reject(new Error("canvas-kinds")));
      script.onerror = () => { kindsPromise = null; reject(new Error(L("Не удалось загрузить модуль холста", "Could not load the canvas module"))); };
      document.head.appendChild(script);
    });
    return kindsPromise;
  }

  const st = {
    chatId: "", list: [], listChat: "", full: new Map(),
    openId: "", v: 0, tab: "view", draft: null, diff: null, decisions: {}, logs: [], nonce: "",
    width: Math.min(1100, Math.max(360, +(localStorage.getItem("memu_canvas_w") || 0) || 580)),
    menu: "", busy: false, wide: false,
    file: {}, errors: [], errIdx: 0, errHidden: false, lineMap: [], fixFor: "", fixAt: 0,
  };
  const currentChatId = () => { try { return S.currentId || ""; } catch (e) { return ""; } };
  const messagesOf = (chatId) => {
    try {
      if (!chatId || chatId === S.currentId) return S.messages || [];
      return ((S.chats || []).find((c) => c.id === chatId) || {}).messages || [];
    } catch (e) { return []; }
  };
  async function req(path, opts = {}) {
    const resp = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, signal: opts.signal });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false && data.error && !opts.raw) {
      const error = new Error(data.message || data.error || `HTTP ${resp.status}`);
      error.payload = data; error.status = resp.status;
      throw error;
    }
    return data;
  }
  const say = (text, kind = "ok", action = null) => { try { if (typeof toast === "function") toast(text, kind, action); } catch (e) {  } };
  const enc = encodeURIComponent;

  async function loadList(chatId, force = false) {
    if (!chatId) return [];
    if (!force && st.listChat === chatId) return st.list;
    const data = await req(`/api/pg/canvas/${enc(chatId)}`);
    if (currentChatId() === chatId || st.chatId === chatId || !st.listChat) { st.list = data.artifacts || []; st.listChat = chatId; }
    if ((data.synced || []).length) onLinkSynced(chatId, data.synced);
    return data.artifacts || [];
  }
  async function loadFull(chatId, id, force = false) {
    const key = chatId + "|" + id;
    if (!force && st.full.has(key)) return st.full.get(key);
    const data = await req(`/api/pg/canvas/${enc(chatId)}/${enc(id)}`);
    st.full.set(key, data.artifact);
    return data.artifact;
  }

  function onLinkSynced(chatId, rows) {
    for (const r of rows) st.full.delete(chatId + "|" + r.id);
    const mine = rows.find((r) => r.id === st.openId && chatId === st.chatId);
    say(L(`Правки из MemuNote → v${rows[0].version}`, `Edits from MemuNote → v${rows[0].version}`));
    if (mine && isOpen() && !(st.draft && st.draft.dirty)) setTimeout(() => open(mine.id, { v: mine.version, tab: "diff", quiet: true }), 0);
  }
  function invalidate(chatId, id) {
    if (id) st.full.delete(chatId + "|" + id);
    else for (const key of [...st.full.keys()]) if (key.startsWith(chatId + "|")) st.full.delete(key);
    if (st.listChat === chatId) st.listChat = "";
  }

  function pathHeads(messages, list) {
    const ref = {};
    for (const m of messages || []) for (const r of (m && m.canvas) || []) {
      if (r && r.id && r.v) ref[r.id] = Math.max(ref[r.id] || 0, +r.v || 0);
    }
    const heads = {};
    for (const art of list || []) {
      const want = ref[art.id];
      if (!want) { heads[art.id] = art.head; continue; }
      const rows = new Map((art.versions || []).map((row) => [row.v, row]));
      if (!rows.has(want)) { heads[art.id] = art.head; continue; }
      let cur = art.head, seen = 0, ok = false;
      while (cur && seen++ < 200) {
        if (cur === want) { ok = true; break; }
        const row = rows.get(cur);
        if (!row || row.author !== "user") break;
        cur = row.parent;
      }
      heads[art.id] = ok ? art.head : want;
    }
    return heads;
  }
  const versionOf = (art, v) => (art.versions || []).find((row) => row.v === v) || (art.versions || []).slice(-1)[0] || { content: "", meta: {} };
  const msgIdOf = (m) => { try { return window.MemuBranches ? MemuBranches.msgId(m) : (m.id || ""); } catch (e) { return ""; } };

  async function runTool(assistant, call, source = {}) {
    const name = String(call.tool || "");
    const chatId = source.chatId || currentChatId();
    if (!chatId) return { tool: name, error: "Canvas is unavailable: the chat is not saved yet. Answer in the chat instead." };
    let heads = {};
    try { heads = pathHeads(source.messages || messagesOf(chatId), await loadList(chatId, true)); } catch (e) {  }
    let result;
    try {
      result = await req("/api/pg/canvas/tool", { method: "POST", raw: true, body: {
        chat_id: chatId, tool: name, args: call.args || {}, model: assistant?.model || "", msg_id: msgIdOf(assistant), heads } });
    } catch (error) {
      return { tool: name, error: error.message || String(error) };
    }
    if (result && result.ok && name === "canvas_create" && Array.isArray(call.args?.files)) {
      call.args = { ...call.args, files: call.args.files.map((f) => ({ path: f?.path || f?.name || "", content: `[${String(f?.content || "").length} chars — in the canvas]` })) };
    }
    if (result && result.ok && name === "canvas_write_file" && typeof call.args?.content === "string" && call.args.content.length > 200) {
      call.args = { ...call.args, content: `[${call.args.content.length} chars — saved in the canvas as ${result.id} v${result.version}]` };
    }
    if (result && result.ok && name === "canvas_create" && typeof call.args?.content === "string" && call.args.content.length > 200) {

      call.args = { ...call.args, content: `[${call.args.content.length} chars — saved in the canvas as ${result.id} v${result.version}]` };
    }
    if (result && result.ok && (name === "canvas_create" || name === "canvas_edit" || name === "canvas_write_file") && assistant) {
      invalidate(chatId, result.id);
      const list = await loadList(chatId, true).catch(() => []);
      const art = list.find((a) => a.id === result.id) || {};
      const refs = (assistant.canvas || []).filter((r) => r.id !== result.id);
      const prev = (assistant.canvas || []).find((r) => r.id === result.id);
      refs.push({ id: result.id, v: result.version, op: prev?.op === "create" ? "create" : (name === "canvas_create" ? "create" : "edit"),
        title: art.title || result.title || "", type: art.type || result.type || "document",
        added: (prev?.added || 0) + (result.lines_added || 0), removed: (prev?.removed || 0) + (result.lines_removed || 0) });
      assistant.canvas = refs;
      if (chatId === currentChatId()) {

        const fixing = st.fixFor === result.id && name !== "canvas_create" && Date.now() - st.fixAt < 180000;
        if (result.file) st.file[result.id] = result.file;
        await open(result.id, { v: result.version, tab: name === "canvas_create" || fixing ? "view" : "diff", chatId, quiet: true });
      }
    }
    return result;
  }
  async function promptBlock(chatState = {}) {
    const chatId = chatState.chatId || "";
    try {
      const list = chatId ? await loadList(chatId, true) : [];
      const heads = pathHeads(chatState.messages || messagesOf(chatId), list);
      const data = await req("/api/pg/canvas/context", { method: "POST", body: {
        chat_id: chatId, heads, focus: st.chatId === chatId && isOpen() ? st.openId : "" } });
      return data.block ? "\n\n" + data.block : "";
    } catch (e) { return ""; }
  }
  function historyNote(m) {
    const refs = (m && m.canvas) || [];
    if (!refs.length) return "";
    return refs.map((r) => `\n[Canvas: ${r.op === "create" ? "created" : "edited"} «${r.title || r.id}» (id ${r.id}, version ${r.v}) — the text is in the canvas, not in this message]`).join("");
  }

  function streamingCanvas(content) {
    const text = String(content || "");
    const at = text.lastIndexOf("```memuai-tool");
    if (at < 0) return null;
    const block = text.slice(at);
    if (/```\s*$/.test(block.slice(14))) return null;
    const tool = ((block.match(/canvas_(create|edit|write_file)/) || [])[1] || "").replace("write_file", "edit");
    if (!tool) return null;
    const title = ((block.match(/"title"\s*:\s*"((?:[^"\\]|\\.)*)"/) || [])[1] || "").replace(/\\(.)/g, "$1");
    const type = (block.match(/"type"\s*:\s*"(\w+)"/) || [])[1] || "document";
    return { tool, title, type: TYPE_LABEL[type] ? type : "document", size: block.length };
  }
  function cardsHtml(m) {
    const refs = (m && m.canvas) || [];
    const live = m && m.streaming ? streamingCanvas(m.content) : null;
    if (!refs.length && !live) return "";
    const liveHtml = live ? `<div class="cv-card cv-card-live" aria-live="polite">
        <span class="cv-card-ico">${ico(live.type)}</span>
        <span class="cv-card-main"><span class="cv-card-title">${esc(live.title || (live.tool === "edit" ? L("Правки", "Edits") : typeLabel(live.type)))}</span>
        <span class="cv-card-sub">${esc(live.tool === "edit" ? L("Готовлю правки для холста…", "Preparing canvas edits…") : L("Пишу в холст…", "Writing to the canvas…"))} ${esc(fmtSize(live.size))}</span></span>
        <span class="cv-spin" aria-hidden="true"></span></div>` : "";
    return `<div class="cv-cards">${liveHtml}${refs.map((r) => {
      const what = r.op === "create" ? L("создан", "created") : L("изменён", "edited");
      const diff = r.op !== "create" && (r.added || r.removed) ? ` <span class="cv-card-diff"><b class="cv-add">+${r.added || 0}</b> <b class="cv-del">−${r.removed || 0}</b></span>` : "";
      const active = st.openId === r.id && isOpen();
      return `<button type="button" class="cv-card${active ? " is-open" : ""}" data-cv-open="${esc(r.id)}" data-cv-v="${esc(r.v)}"
        title="${esc(L("Открыть в холсте", "Open in Canvas"))}">
        <span class="cv-card-ico cv-t-${esc(r.type || "document")}">${ico(r.type || "document")}</span>
        <span class="cv-card-main"><span class="cv-card-title">${esc(r.title || L("Без названия", "Untitled"))}</span>
        <span class="cv-card-sub">${esc(typeLabel(r.type))} · v${esc(r.v)} ${esc(what)}${diff}</span></span>
        <span class="cv-card-go">${esc(L("Открыть", "Open"))}</span></button>`;
    }).join("")}</div>`;
  }

  const fmtSize = (n) => n >= 1000 ? L(`${(n / 1000).toFixed(1).replace(".", ",")} тыс. символов`, `${(n / 1000).toFixed(1)}k characters`) : L(`${n} символов`, `${n} characters`);

  function consoleBridge(nonce, files) {
    var send = function (level, args, extra) {
      try {
        var text = Array.prototype.map.call(args, function (a) {
          try { return typeof a === "string" ? a : (a instanceof Error ? (a.stack || String(a)) : JSON.stringify(a)); } catch (e) { return String(a); }
        }).join(" ");
        var msg = { __memuCanvas: nonce, level: level, text: String(text).slice(0, 2000) };
        if (extra) for (var k in extra) msg[k] = extra[k];
        parent.postMessage(msg, "*");
      } catch (e) {  }
    };

    var report = function (kind, message, line, col, stack) {
      send("error", [message + (line ? " (line " + line + ")" : "")], { kind: kind, message: String(message || "Error").slice(0, 600),
        line: +line || 0, col: +col || 0, stack: String(stack || "").slice(0, 1500) });
    };

    if (files && typeof fetch === "function") {
      var realFetch = window.fetch;
      window.fetch = function (input, init) {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var key = String(url).replace(/^about:srcdoc/, "").replace(/^\.?\//, "").split(/[?#]/)[0];
        try { key = decodeURIComponent(key); } catch (e) {  }
        if (Object.prototype.hasOwnProperty.call(files, key)) {
          var type = /\.json$/i.test(key) ? "application/json" : /\.svg$/i.test(key) ? "image/svg+xml" : /\.css$/i.test(key) ? "text/css"
            : /\.m?js$/i.test(key) ? "text/javascript" : /\.csv$/i.test(key) ? "text/csv" : "text/plain";
          return Promise.resolve(new Response(files[key], { status: 200, headers: { "Content-Type": type + "; charset=utf-8" } }));
        }
        return realFetch.apply(this, arguments);
      };
    }
    ["log", "info", "warn", "error", "debug"].forEach(function (k) {
      var original = console[k];
      console[k] = function () {
        if (k === "error") {
          var err = Array.prototype.find.call(arguments, function (a) { return a instanceof Error; });
          var text = Array.prototype.map.call(arguments, function (a) { try { return typeof a === "string" ? a : a instanceof Error ? String(a) : JSON.stringify(a); } catch (e) { return String(a); } }).join(" ");
          report("console", text, 0, 0, err ? err.stack : (new Error()).stack);
        } else send(k, arguments);
        try { if (original) original.apply(console, arguments); } catch (e) {  }
      };
    });

    var memStore = function () {
      var d = {};
      return { getItem: function (k) { k = String(k); return Object.prototype.hasOwnProperty.call(d, k) ? d[k] : null; },
        setItem: function (k, v) { d[String(k)] = String(v); }, removeItem: function (k) { delete d[String(k)]; },
        clear: function () { d = {}; }, key: function (i) { return Object.keys(d)[i] || null; },
        get length() { return Object.keys(d).length; } };
    };
    ["localStorage", "sessionStorage"].forEach(function (name) {
      try { void window[name]; } catch (e) { try { Object.defineProperty(window, name, { value: memStore(), configurable: true }); } catch (e2) {  } }
    });
    try { void document.cookie; } catch (e) {
      try { var jar = ""; Object.defineProperty(document, "cookie", { get: function () { return jar; }, set: function (v) { jar = String(v).split(";")[0]; }, configurable: true }); } catch (e2) {  }
    }
    window.alert = function (m) { send("info", ["alert: " + m]); };
    window.confirm = function (m) { send("info", ["confirm: " + m + " → false"]); return false; };
    window.prompt = function (m) { send("info", ["prompt: " + m + " → null"]); return null; };
    addEventListener("error", function (e) {
      var t = e.target;
      if (t && t !== window && t.tagName) { report("resource", "Failed to load <" + t.tagName.toLowerCase() + "> " + (t.src || t.href || ""), 0, 0, ""); return; }
      report("error", e.message || "Error", e.lineno, e.colno, e.error && e.error.stack);
    }, true);
    addEventListener("unhandledrejection", function (e) {
      var r = e.reason;
      report("rejection", "Unhandled rejection: " + ((r && r.message) || r), 0, 0, r && r.stack);
    });
    addEventListener("securitypolicyviolation", function (e) { send("warn", ["Blocked by the sandbox (no network): " + e.violatedDirective + " " + (e.blockedURI || "")]); });
  }
  function sandboxDoc(html, nonce, files = null) {
    const vfs = files && Object.keys(files).length ? JSON.stringify(files).replace(/<\//g, "<\\/").replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029") : "null";
    const head = `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}">` +
      `<script>(${consoleBridge.toString()})(${JSON.stringify(nonce)}, ${vfs});<\/script>`;
    const src = String(html || "");

    const doctype = src.match(/^\uFEFF?\s*<!doctype[^>]*>/i);
    if (doctype) return doctype[0] + head + src.slice(doctype[0].length);
    if (/<(html|head|body)[\s>]/i.test(src)) return head + src;
    return `<!doctype html><html><head>${head}</head><body>${src}</body></html>`;
  }
  function jsDoc(code) {
    const safe = String(code || "").replace(/<\/script/gi, "<\\/script");
    return `<!doctype html><html><head><style>body{font:14px system-ui,sans-serif;margin:16px;color:#222;background:#fff}
      @media (prefers-color-scheme: dark){body{color:#ddd;background:#16181c}}</style></head><body><div id="app"></div>
      <script>${safe}<\/script></body></html>`;
  }
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object" || !st.nonce || data.__memuCanvas !== st.nonce) return;
    const frame = $("#cv-frame");
    if (!frame || event.source !== frame.contentWindow) return;
    const row = { level: String(data.level || "log"), text: String(data.text || "").slice(0, 2000), at: Date.now() };
    const where = data.kind ? errorPlace(data) : null;
    row.text = row.text.replace(/ \(line (\d+)\)$/, (m, n) => {
      const at = where && where.line ? where : docPlace(+n);
      return ` (${at.path ? at.path + ":" : L("строка ", "line ")}${at.line})`;
    });
    const prev = st.logs[st.logs.length - 1];
    if (prev && prev.level === row.level && prev.text === row.text && row.at - prev.at < 300) return;
    st.logs.push(row);
    if (st.logs.length > 300) st.logs.splice(0, st.logs.length - 300);
    if (where) addError({ kind: String(data.kind), message: String(data.message || row.text), ...where });
    renderConsole();
  });

  function docPlace(docLine) {
    const n = Math.max(1, docLine - (st.lineShift || 0));
    let hit = null;
    for (const r of st.lineMap || []) if (r.from <= n && n <= r.to) hit = r;
    return hit ? { path: hit.path, line: hit.line + (n - hit.from) } : { path: "", line: n };
  }
  function errorPlace(data) {
    if (data.line) return docPlace(+data.line);

    const m = String(data.stack || "").match(/about:srcdoc:(\d+):\d+/g) || [];
    for (const frame of m) {
      const n = +frame.split(":")[2];
      if (n > (st.lineShift || 0)) return docPlace(n);
    }
    return { path: "", line: 0 };
  }
  function addError(err) {
    const key = `${err.message}|${err.path}|${err.line}`;
    const same = st.errors.find((e) => e.key === key);
    if (same) { same.count += 1; renderErrBar(); return; }
    if (st.errors.length >= 50) return;
    st.errors.push({ ...err, key, count: 1 });

    st.errors.sort((x, y) => (x.kind === "console") - (y.kind === "console"));
    st.errHidden = false;
    renderErrBar();
  }

  function parseCsvLine(text, delim) {
    const rows = []; let row = [], cell = "", quoted = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (quoted) {
        if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
        else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"' && cell === "") quoted = true;
      else if (ch === delim) { row.push(cell); cell = ""; }
      else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
      else if (ch !== "\r") cell += ch;
    }
    if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
    return rows;
  }
  function parseTable(text) {
    const src = String(text || "").replace(/^\n+|\n+$/g, "");
    if (!src.trim()) return [];
    const lines = src.split("\n");
    const nonEmpty = lines.filter((l) => l.trim());
    const pipes = nonEmpty.filter((l) => l.trim().startsWith("|") || (l.match(/\|/g) || []).length >= 2);
    if (pipes.length && pipes.length >= nonEmpty.length - 1) {
      return nonEmpty.filter((l) => !/^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$/.test(l.trim()))
        .map((l) => { let s = l.trim(); if (s.startsWith("|")) s = s.slice(1); if (s.endsWith("|")) s = s.slice(0, -1); return s.split(/(?<!\\)\|/).map((c) => c.trim()); });
    }
    const sample = lines.slice(0, 5).join("\n");
    const count = (ch) => (sample.split(ch).length - 1);
    const delim = count("\t") > count(",") && count("\t") >= count(";") ? "\t" : (count(";") > count(",") ? ";" : ",");
    return parseCsvLine(src, delim);
  }
  const mdHtml = (text) => { try { return typeof md === "function" ? md(text) : `<pre>${esc(text)}</pre>`; } catch (e) { return `<pre>${esc(text)}</pre>`; } };
  const codeHtml = (text, langName) => {
    try { if (typeof highlightSyntax === "function") return highlightSyntax(String(text || ""), langName || ""); } catch (e) {  }
    return esc(text);
  };
  const isRunnable = (art, row) => {
    const files = row ? unpackFiles(row.content) : (art.files ? [] : null);
    if (files) return row ? isWebFiles(files) : (art.files || []).some((f) => /\.(html?|m?js)$/i.test(f.path));
    return art.type === "html" || (art.type === "code" && /^(js|javascript|mjs)$/i.test(art.language || ""));
  };

  function curFile(a, files) {
    if (!files || !files.length) return null;
    const want = st.file[a.id];
    return files.find((f) => f.path === want) || files.find((f) => f.path === "index.html") || files[0];
  }
  const fileIcon = (path) => {
    const ext = extOfPath(path);
    const cls = /^(html?)$/.test(ext) ? "html" : ext === "css" ? "css" : /^(m?js|ts)$/.test(ext) ? "js" : /^(json|csv)$/.test(ext) ? "data" : "other";
    return `<span class="cv-fdot cv-f-${cls}" aria-hidden="true">${esc((ext || "·").slice(0, 4))}</span>`;
  };

  function ensurePane() {
    let pane = $("#canvas-pane");
    if (pane) return pane;
    const host = $("#main-content");
    if (!host) return null;
    const resizer = document.createElement("div");
    resizer.className = "cv-resizer"; resizer.id = "canvas-resizer"; resizer.hidden = true;
    resizer.setAttribute("role", "separator"); resizer.setAttribute("tabindex", "0"); resizer.setAttribute("aria-orientation", "vertical");
    pane = document.createElement("aside");
    pane.className = "cv-pane"; pane.id = "canvas-pane"; pane.hidden = true;
    const backdrop = document.createElement("div");
    backdrop.className = "cv-backdrop"; backdrop.id = "canvas-backdrop"; backdrop.hidden = true;
    const anchor = $("#live-preview-resizer") || null;
    host.insertBefore(resizer, anchor); host.insertBefore(pane, anchor); host.appendChild(backdrop);
    backdrop.addEventListener("click", close);
    bindResizer(resizer);
    pane.addEventListener("click", onPaneClick);
    pane.addEventListener("change", onPaneChange);
    pane.addEventListener("input", onPaneInput);
    pane.addEventListener("keydown", onPaneKey);
    pane.addEventListener("dblclick", (event) => {
      const tab = event.target.closest('[data-cv-act="file-pick"]');
      const a = art();
      if (tab && a) fileOp(a, "rename", tab.dataset.path);
    });
    return pane;
  }
  function bindResizer(resizer) {
    let startX = 0, startW = 0;
    const move = (e) => {
      const max = Math.max(420, Math.round(window.innerWidth * 0.72));
      st.width = Math.min(max, Math.max(360, startW + (startX - e.clientX)));
      applyWidth();
    };
    const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up);
      resizer.classList.remove("resizing"); document.body.classList.remove("cv-resizing"); localStorage.setItem("memu_canvas_w", String(st.width)); };
    resizer.addEventListener("pointerdown", (e) => {
      startX = e.clientX; startW = $("#canvas-pane")?.getBoundingClientRect().width || st.width;
      resizer.classList.add("resizing"); document.body.classList.add("cv-resizing");
      document.addEventListener("pointermove", move); document.addEventListener("pointerup", up); e.preventDefault();
    });
    resizer.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      st.width = Math.min(Math.round(window.innerWidth * 0.72), Math.max(360, st.width + (e.key === "ArrowLeft" ? 24 : -24)));
      applyWidth(); localStorage.setItem("memu_canvas_w", String(st.width)); e.preventDefault();
    });
    resizer.title = L("Потяните, чтобы изменить ширину холста", "Drag to resize the canvas");
    resizer.setAttribute("aria-label", L("Ширина холста", "Canvas width"));
  }
  function applyWidth() {
    const pane = $("#canvas-pane");
    if (pane) pane.style.setProperty("--cv-w", (st.wide ? Math.round(window.innerWidth * 0.72) : st.width) + "px");
  }
  const isOpen = () => { const pane = $("#canvas-pane"); return !!pane && !pane.hidden; };
  const isNarrow = () => window.innerWidth < 900;

  async function open(id, opts = {}) {
    const chatId = opts.chatId || currentChatId();
    if (!chatId || !id) return false;
    const pane = ensurePane();
    if (!pane) return false;
    try {
      const art = await loadFull(chatId, id, opts.force !== false);
      await loadList(chatId, true).catch(() => []);
      const heads = pathHeads(messagesOf(chatId), st.list);
      const sameArt = st.openId === id && st.chatId === chatId;
      st.chatId = chatId; st.openId = id;
      st.v = +opts.v || heads[id] || art.head;
      if (opts.tab) st.tab = opts.tab;
      else if (!sameArt) st.tab = "view";
      if (st.tab === "diff" && !versionOf(art, st.v).parent) st.tab = "view";
      if (!sameArt || opts.v) { st.draft = null; st.decisions = {}; st.logs = []; }
      st.diff = null; st.menu = "";
    } catch (error) {
      if (!opts.quiet) say(L("Не удалось открыть холст: ", "Could not open the canvas: ") + error.message, "warn");
      return false;
    }
    pane.hidden = false;
    $("#canvas-resizer").hidden = false;
    $("#canvas-backdrop").hidden = !isNarrow();
    document.body.classList.add("cv-open");
    applyWidth();
    render();
    syncCards();
    syncLauncher();
    return true;
  }
  function close() {
    const pane = $("#canvas-pane");
    if (!pane || pane.hidden) return;
    if (st.draft && st.draft.dirty && !window.confirm(L("Закрыть холст без сохранения правок?", "Close the canvas without saving your edits?"))) return;
    pane.hidden = true;
    $("#canvas-resizer").hidden = true;
    $("#canvas-backdrop").hidden = true;
    document.body.classList.remove("cv-open");
    st.draft = null; st.menu = "";
    pane.innerHTML = "";
    syncCards();
    syncLauncher();
  }
  function syncCards() {
    document.querySelectorAll(".cv-card[data-cv-open]").forEach((el) => {
      el.classList.toggle("is-open", isOpen() && el.dataset.cvOpen === st.openId);
    });
  }
  const art = () => st.full.get(st.chatId + "|" + st.openId);
  const linkOf = (a) => (st.list.find((x) => x.id === a.id) || {}).link || null;

  function fmtTime(ms) {
    try { return new Date(ms).toLocaleTimeString(lang() === "en" ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
  }

  function versionLabel(row, headV) {
    const op = { create: L("создан", "created"), edit: L("правка", "edit"), rewrite: L("переписан", "rewrite"),
      review: L("проверка", "review"), user: L("ваша правка", "your edit"), restore: L("возврат", "restore") }[row.op] || row.op || "";
    const flag = row.review === "pending" ? L(" · на проверке", " · to review") : "";
    return `${row.v === headV ? "✓ " : ""}v${row.v} · ${op} · ${fmtTime(row.at)}${flag}`;
  }
  function render() {
    const pane = $("#canvas-pane");
    const a = art();
    if (!pane || !a) return;
    const row = versionOf(a, st.v);
    const versions = [...(a.versions || [])].reverse();
    const others = st.list.length > 1 ? st.list : [];
    const pendingHere = row.review === "pending" && row.parent;
    const headV = pathHeads(messagesOf(st.chatId), st.list)[a.id] || a.head;
    const files = unpackFiles(row.content);
    const tabs = [["view", L("Просмотр", "Preview")], ["code", a.type === "document" || a.type === "email" || a.type === "slides" ? L("Текст", "Text") : L("Код", "Code")], ["diff", "Diff"]];
    pane.dataset.type = a.type;
    pane.innerHTML = `
      <div class="cv-head">
        <span class="cv-head-ico cv-t-${esc(a.type)}">${ico(a.type)}</span>
        <div class="cv-head-main">
          ${others.length ? `<select class="cv-art-pick" data-cv-act="pick-art" aria-label="${esc(L("Объект холста", "Canvas item"))}">
            ${others.map((x) => `<option value="${esc(x.id)}"${x.id === a.id ? " selected" : ""}>${esc(x.title)}</option>`).join("")}</select>`
            : `<button type="button" class="cv-title" data-cv-act="rename" title="${esc(L("Переименовать", "Rename"))}">${esc(a.title)}</button>`}
          <span class="cv-sub">${esc(typeLabel(a.type))}${a.language ? " · " + esc(a.language) : ""} · ${files
            ? esc(L(`${files.length} ${files.length === 1 ? "файл" : files.length < 5 ? "файла" : "файлов"}`, `${files.length} file${files.length === 1 ? "" : "s"}`))
            : `${esc(String((row.content || "").split("\n").length))} ${esc(L("стр.", "lines"))}`}${linkOf(a) ? ` · <span class="cv-linked" title="${esc(L("Связан с MemuNote: сохранения там приходят сюда новой версией, правки отсюда — в MemuNote", "Linked with MemuNote: saves there come here as a new version, edits here go to MemuNote"))}">${ico("note")}MemuNote</span>` : ""}</span>
        </div>
        <select class="cv-ver" data-cv-act="pick-ver" aria-label="${esc(L("Версия", "Version"))}" title="${esc(L("Версии", "Versions"))}">
          ${versions.map((x) => `<option value="${x.v}"${x.v === row.v ? " selected" : ""}>${esc(versionLabel(x, headV))}</option>`).join("")}
        </select>
        <button type="button" class="cv-btn" data-cv-act="copy" title="${esc(L("Копировать", "Copy"))}" aria-label="${esc(L("Копировать", "Copy"))}">${ico("copy")}</button>
        <button type="button" class="cv-btn" data-cv-act="menu-dl" title="${esc(L("Скачать", "Download"))}" aria-label="${esc(L("Скачать", "Download"))}" aria-haspopup="menu">${ico("download")}</button>
        <button type="button" class="cv-btn" data-cv-act="menu-more" title="${esc(L("Ещё", "More"))}" aria-label="${esc(L("Ещё", "More"))}" aria-haspopup="menu">${ico("more")}</button>
        <button type="button" class="cv-btn cv-hide-narrow" data-cv-act="wide" title="${esc(st.wide ? L("Обычная ширина", "Normal width") : L("Шире", "Wider"))}" aria-pressed="${st.wide}">${ico("expand")}</button>
        <button type="button" class="cv-btn" data-cv-act="close" title="${esc(L("Закрыть холст", "Close the canvas"))}" aria-label="${esc(L("Закрыть холст", "Close the canvas"))}">${ico("close")}</button>
        ${st.menu ? menuHtml(a) : ""}
      </div>
      <div class="cv-tabs" role="tablist">
        ${tabs.map(([key, label]) => `<button type="button" role="tab" class="cv-tab${st.tab === key ? " is-on" : ""}" data-cv-tab="${key}" aria-selected="${st.tab === key}"
          ${key === "diff" && !row.parent ? `disabled title="${esc(L("У первой версии нет изменений", "The first version has no changes"))}"` : ""}>${esc(label)}${key === "diff" && pendingHere ? '<span class="cv-dot" aria-hidden="true"></span>' : ""}</button>`).join("")}
        <span class="cv-grow"></span>
        ${row.v !== headV ? `<span class="cv-oldver">${esc(L("Это не текущая версия", "Not the current version"))}
          <button type="button" class="cv-link" data-cv-act="make-head">${esc(L("Вернуть эту версию", "Restore this version"))}</button></span>` : ""}
      </div>
      ${files && st.tab !== "diff" && !(st.tab === "view" && isWebFiles(files)) ? filesBarHtml(a, files) : ""}
      <div class="cv-body" data-tab="${esc(st.tab)}">${bodyHtml(a, row)}</div>`;
    afterRender(a, row);
  }
  function filesBarHtml(a, files) {
    const cur = curFile(a, files);
    return `<div class="cv-files" role="tablist" aria-label="${esc(L("Файлы", "Files"))}">
      ${files.map((f) => `<span class="cv-file${f === cur ? " is-on" : ""}"><button type="button" role="tab" class="cv-file-btn" data-cv-act="file-pick" data-path="${esc(f.path)}"
        aria-selected="${f === cur}" title="${esc(f.path + " · " + L("двойной щелчок — переименовать", "double-click to rename"))}">${fileIcon(f.path)}<span class="cv-file-name">${esc(f.path)}</span></button>${f === cur && files.length > 1
        ? `<button type="button" class="cv-file-x" data-cv-act="file-del" data-path="${esc(f.path)}" title="${esc(L("Удалить файл", "Delete the file"))}" aria-label="${esc(L("Удалить файл", "Delete the file"))}">${ico("close")}</button>` : ""}</span>`).join("")}
      <button type="button" class="cv-file-add" data-cv-act="file-add" title="${esc(L("Новый файл", "New file"))}" aria-label="${esc(L("Новый файл", "New file"))}">${ico("plus")}</button></div>`;
  }
  function menuHtml(a) {
    const row = versionOf(a, st.v);
    const files = unpackFiles(row.content);
    if (st.menu === "dl" && files) {
      const cur = curFile(a, files);
      const items = [["dl-zip", L("Проект (.zip)", "Project (.zip)")]];
      if (isWebFiles(files)) items.push(["dl-html", L("Одна страница (.html)", "One page (.html)")]);
      if (cur) items.push(["dl-cur", L(`Файл ${cur.path}`, `File ${cur.path}`)]);
      return `<div class="cv-menu cv-menu-dl" role="menu">${items.map(([k, label]) => `<button type="button" role="menuitem" data-cv-act="${k}">${ico("download")}<span>${esc(label)}</span></button>`).join("")}</div>`;
    }
    if (st.menu === "dl" && (a.type === "diagram" || a.type === "slides")) {
      const items = a.type === "diagram"
        ? [["dl-svg", "SVG"], ["dl-png", "PNG"], ["dl-file", L("Код Mermaid (.mmd)", "Mermaid source (.mmd)")]]
        : [["dl-pptx", "PowerPoint (.pptx)"], ["dl-file", "Markdown (.md)"]];
      return `<div class="cv-menu cv-menu-dl" role="menu">${items.map(([k, label]) => `<button type="button" role="menuitem" data-cv-act="${k}">${ico("download")}<span>${esc(label)}</span></button>`).join("")}</div>`;
    }
    if (st.menu === "dl") {
      const items = a.type === "table"
        ? [["dl-csv", "CSV"], ["dl-xlsx", "Excel (.xlsx)"], ["dl-txt", L("Как текст", "As text")]]
        : a.type === "email" ? [["dl-eml", L("Письмо (.eml)", "Email (.eml)")], ["dl-txt", L("Как текст", "As text")]]
          : [["dl-file", L("Файл", "File") + " (." + extOf(a) + ")"]];
      return `<div class="cv-menu cv-menu-dl" role="menu">${items.map(([k, label]) => `<button type="button" role="menuitem" data-cv-act="${k}">${ico("download")}<span>${esc(label)}</span></button>`).join("")}</div>`;
    }
    const items = [["memunote", "note", L("Открыть в MemuNote", "Open in MemuNote")],
      ["work", "folder", L("Сохранить в проект Work", "Save to the Work project")]];
    if (isRunnable(a, row)) items.push(["module", "module", L("Сделать модулем", "Make a module")]);
    items.push(["delete", "trash", L("Удалить из холста", "Remove from Canvas")]);
    return `<div class="cv-menu cv-menu-more" role="menu">${items.map(([k, i, label]) => `<button type="button" role="menuitem" data-cv-act="${k}"${k === "delete" ? ' class="cv-danger"' : ""}>${ico(i)}<span>${esc(label)}</span></button>`).join("")}</div>`;
  }
  const extOf = (a) => a.type === "html" ? "html" : a.type === "table" ? "csv" : a.type === "email" ? "eml" : a.type === "document" || a.type === "slides" ? "md"
    : a.type === "diagram" ? "mmd"
    : ({ python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts", css: "css", json: "json", bash: "sh", sh: "sh",
      sql: "sql", java: "java", cpp: "cpp", "c++": "cpp", c: "c", csharp: "cs", cs: "cs", go: "go", rust: "rs", ruby: "rb", php: "php",
      yaml: "yaml", html: "html", powershell: "ps1", kotlin: "kt", swift: "swift", lua: "lua" }[String(a.language || "").toLowerCase()] || "txt");

  function bodyHtml(a, row) {
    if (st.tab === "code") return editorHtml(a, row);
    if (st.tab === "diff") return `<div class="cv-diff" id="cv-diff"><div class="cv-loading">${esc(L("Считаю изменения…", "Computing changes…"))}</div></div>`;
    const content = row.content || "";
    const files = unpackFiles(content);
    if (files && !isWebFiles(files)) {
      const cur = curFile(a, files);
      const lang = LANG_OF_EXT[extOfPath(cur.path)] || "";
      return lang === "markdown" ? `<article class="cv-doc body">${mdHtml(cur.content)}</article>` : `<pre class="cv-code"><code>${codeHtml(cur.content, lang)}</code></pre>`;
    }
    if (a.type === "diagram" || a.type === "slides") {
      return `<div class="cv-kind cv-kind-${esc(a.type)}" id="cv-kind"><div class="cv-loading">${esc(L("Рисую…", "Rendering…"))}</div></div>
        <div class="cv-errbar" id="cv-errbar" hidden></div>`;
    }
    if (a.type === "document") return `<article class="cv-doc body">${mdHtml(content)}</article>`;
    if (a.type === "code" && !isRunnable(a, row)) return `<pre class="cv-code"><code>${codeHtml(content, a.language)}</code></pre>`;
    if (isRunnable(a, row)) {
      return `<div class="cv-run">
        <div class="cv-run-bar"><span class="cv-sandbox-note" title="${esc(L("Скрипты работают в изолированной песочнице: без сети, без доступа к MemuAI и вашим файлам", "Scripts run in an isolated sandbox: no network, no access to MemuAI or your files"))}">${ico("check")}${esc(L("Песочница · без сети", "Sandbox · no network"))}</span>
          ${files ? `<span class="cv-run-files">${esc(files.map((f) => f.path).join(" · "))}</span>` : ""}
          <span class="cv-grow"></span>
          <button type="button" class="cv-btn" data-cv-act="rerun" title="${esc(L("Перезапустить", "Run again"))}" aria-label="${esc(L("Перезапустить", "Run again"))}">${ico("reload")}</button></div>
        <div class="cv-errbar" id="cv-errbar" hidden></div>
        <iframe id="cv-frame" class="cv-frame" sandbox="allow-scripts" referrerpolicy="no-referrer" title="${esc(L("Предпросмотр", "Preview"))}"></iframe>
        <div class="cv-console" id="cv-console"></div></div>`;
    }
    if (a.type === "table") {
      const rows = parseTable(content);
      if (!rows.length) return `<div class="cv-empty">${esc(L("Таблица пуста", "The table is empty"))}</div>`;
      const width = Math.max(...rows.map((r) => r.length));
      const num = (v) => /^-?[\d\s\u00a0]+([.,]\d+)?%?$/.test(String(v).trim()) && String(v).trim() !== "";
      return `<div class="cv-grid-wrap"><table class="cv-grid"><thead><tr><th class="cv-rn">#</th>${Array.from({ length: width }, (_, c) => `<th>${esc(rows[0][c] ?? "")}</th>`).join("")}</tr></thead>
        <tbody>${rows.slice(1).map((r, i) => `<tr><td class="cv-rn">${i + 1}</td>${Array.from({ length: width }, (_, c) => `<td${num(r[c] ?? "") ? ' class="cv-num"' : ""}>${esc(r[c] ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <div class="cv-foot"><span>${esc(L(`${rows.length - 1} строк × ${width} столбцов`, `${rows.length - 1} rows × ${width} columns`))}</span><span class="cv-grow"></span>
        <button type="button" class="cv-chip" data-cv-act="dl-csv">${ico("download")}CSV</button><button type="button" class="cv-chip" data-cv-act="dl-xlsx">${ico("download")}XLSX</button></div>`;
    }
    if (a.type === "email") {
      const meta = row.meta || {};
      const field = (key, label) => `<div class="cv-mail-row"><span class="cv-mail-k">${esc(label)}</span><span class="cv-mail-v">${esc(meta[key] || "—")}</span>
        ${meta[key] ? `<button type="button" class="cv-btn cv-btn-s" data-cv-act="copy-${key}" title="${esc(L("Копировать", "Copy"))}" aria-label="${esc(L("Копировать", "Copy"))}">${ico("copy")}</button>` : ""}</div>`;
      return `<div class="cv-mail">${field("to", L("Кому", "To"))}${meta.cc ? field("cc", L("Копия", "Cc")) : ""}${field("subject", L("Тема", "Subject"))}
        <div class="cv-mail-body">${esc(content)}</div>
        <div class="cv-foot"><span class="cv-grow"></span><button type="button" class="cv-chip" data-cv-act="copy-body">${ico("copy")}${esc(L("Копировать текст", "Copy the text"))}</button>
        <button type="button" class="cv-chip cv-chip-main" data-cv-act="copy-mail">${ico("email")}${esc(L("Копировать письмо", "Copy the email"))}</button></div></div>`;
    }
    return `<pre class="cv-code"><code>${esc(content)}</code></pre>`;
  }
  function editorHtml(a, row) {
    if (!st.draft || st.draft.v !== row.v) st.draft = { v: row.v, text: row.content || "", meta: { ...(row.meta || {}) }, dirty: false, files: unpackFiles(row.content) };
    const d = st.draft;
    const cur = d.files ? curFile(a, d.files) : null;
    const live = !d.files && (a.type === "diagram" || a.type === "slides");
    const meta = a.type === "email" ? `<div class="cv-ed-meta">
        <label><span>${esc(L("Кому", "To"))}</span><input data-cv-meta="to" value="${esc(d.meta.to || "")}" spellcheck="false"></label>
        <label><span>${esc(L("Тема", "Subject"))}</span><input data-cv-meta="subject" value="${esc(d.meta.subject || "")}"></label></div>` : "";
    return `<div class="cv-editor">${meta}
      <textarea class="cv-ed${a.type === "document" || a.type === "email" || (cur && extOfPath(cur.path) === "md") ? " cv-ed-prose" : ""}" id="cv-ed" spellcheck="${a.type === "document" || a.type === "email"}"
        aria-label="${esc(cur ? L(`Файл ${cur.path}`, `File ${cur.path}`) : L("Текст объекта", "Item text"))}">${esc(cur ? cur.content : d.text)}</textarea>
      ${live ? `<div class="cv-kind cv-kind-${esc(a.type)} cv-kind-live" id="cv-kind"></div><div class="cv-errbar" id="cv-errbar" hidden></div>` : ""}
      <div class="cv-foot"><span class="cv-hint">${esc(d.dirty ? L("Есть несохранённые правки · Ctrl+S — сохранить", "Unsaved edits · Ctrl+S to save") : L("Правьте прямо здесь — сохранится новой версией", "Edit right here — saved as a new version"))}</span>
        <button type="button" class="cv-chip" data-cv-act="ed-cancel"${d.dirty ? "" : " disabled"}>${esc(L("Отменить", "Discard"))}</button>
        <button type="button" class="cv-chip cv-chip-main" data-cv-act="ed-save"${d.dirty ? "" : " disabled"}>${ico("check")}${esc(L("Сохранить версию", "Save version"))}</button></div></div>`;
  }
  function afterRender(a, row) {
    if (st.tab === "view" && isRunnable(a, row)) runPreview(a, row);
    if ((a.type === "diagram" || a.type === "slides") && !unpackFiles(row.content) && (st.tab === "view" || st.tab === "code")) {
      drawKind(a, st.tab === "code" && st.draft ? st.draft.text : row.content || "");
    }
    if (st.tab === "diff") loadDiff(a, row);

  }
  async function runPreview(a, row) {
    const frame = $("#cv-frame");
    if (!frame) return;
    const nonce = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random())).replace(/-/g, "");
    st.nonce = nonce;
    st.logs = []; st.errors = []; st.errIdx = 0; st.errHidden = false;
    renderConsole(); renderErrBar();
    let page = a.type === "html" ? row.content : jsDoc(row.content), files = null;
    st.lineMap = [];
    if (unpackFiles(row.content)) {

      try {
        const data = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/preview?v=${row.v}`);
        if (st.nonce !== nonce) return;
        page = data.html || ""; files = data.files || null; st.lineMap = data.map || [];
      } catch (error) { say(error.message, "warn"); return; }
    }
    const doc = sandboxDoc(page, nonce, files);

    const nl = (text) => (String(text || "").match(/\n/g) || []).length;
    st.lineShift = nl(doc) - nl(page);
    const target = $("#cv-frame");
    if (target) target.srcdoc = doc;
  }

  function renderErrBar() {
    const bar = $("#cv-errbar");
    if (!bar) return;
    const list = st.errors;
    if (!list.length || st.errHidden) { bar.hidden = true; bar.innerHTML = ""; return; }
    st.errIdx = Math.min(st.errIdx, list.length - 1);
    const e = list[st.errIdx];
    const where = e.line ? `${e.path ? e.path + ":" : L("строка ", "line ")}${e.line}` : "";
    const kind = { console: "console.error", rejection: L("Promise не обработан", "Unhandled promise"), resource: L("Ресурс", "Resource"), diagram: L("Ошибка схемы", "Diagram error"), slides: L("Ошибка разметки", "Markup error") }[e.kind] || L("Ошибка", "Error");
    bar.hidden = false;
    bar.innerHTML = `<span class="cv-err-ico">${ico("warn")}</span>
      <div class="cv-err-main"><div class="cv-err-top"><b>${esc(kind)}</b>${where ? `<button type="button" class="cv-err-where" data-cv-act="err-goto" title="${esc(L("Показать в коде", "Show in the code"))}">${esc(where)}</button>` : ""}
        ${e.count > 1 ? `<span class="cv-err-n">×${e.count}</span>` : ""}
        ${list.length > 1 ? `<span class="cv-err-pager"><button type="button" class="cv-btn cv-btn-s" data-cv-act="err-prev" aria-label="${esc(L("Предыдущая", "Previous"))}"${st.errIdx ? "" : " disabled"}>‹</button>${st.errIdx + 1}/${list.length}<button type="button" class="cv-btn cv-btn-s" data-cv-act="err-next" aria-label="${esc(L("Следующая", "Next"))}"${st.errIdx < list.length - 1 ? "" : " disabled"}>›</button></span>` : ""}</div>
        <div class="cv-err-msg" title="${esc(e.message)}">${esc(e.message)}</div></div>
      <button type="button" class="cv-chip cv-chip-main cv-err-fix" data-cv-act="err-fix">${ico("sparkle")}${esc(L("Исправить с ИИ", "Fix with AI"))}</button>
      <button type="button" class="cv-btn cv-btn-s" data-cv-act="err-hide" title="${esc(L("Скрыть", "Hide"))}" aria-label="${esc(L("Скрыть", "Hide"))}">${ico("close")}</button>`;
  }
  function codeLineOf(a, row, err) {
    if (!err.line) return "";
    const files = unpackFiles(row.content);
    const src = files ? (files.find((f) => f.path === err.path) || {}).content : row.content;
    return String(src || "").split("\n")[err.line - 1] || "";
  }
  async function fixWithAI() {
    const a = art();
    const e = st.errors[st.errIdx];
    if (!a || !e) return;
    if (typeof sendPrepared !== "function") { say(L("Отправка в чат недоступна", "Sending to the chat is unavailable"), "warn"); return; }
    const row = versionOf(a, st.v);
    const code = codeLineOf(a, row, e).trim().slice(0, 300).replace(/`/g, "'");
    const where = e.path ? L(`файл ${e.path}, строка ${e.line}`, `file ${e.path}, line ${e.line}`) : e.line ? L(`строка ${e.line}`, `line ${e.line}`) : "";
    const text = L(
      `Исправь ошибку в превью холста «${a.title}» (${a.id}, v${row.v}${where ? ", " + where : ""}):\n\`\`\`\n${e.message}\n\`\`\`` +
        (code ? `\nСтрока кода: \`${code}\`` : "") + "\nИсправь причину точечно через canvas_edit, не пересоздавай объект.",
      `Fix the error in the canvas preview «${a.title}» (${a.id}, v${row.v}${where ? ", " + where : ""}):\n\`\`\`\n${e.message}\n\`\`\`` +
        (code ? `\nCode line: \`${code}\`` : "") + "\nFix the cause with a targeted canvas_edit; do not recreate the item.");
    const input = $("#input");
    const draft = input ? input.value : "";
    if (draft.trim() && !window.confirm(L("В поле ввода есть текст — заменить его запросом на исправление?", "The message box has text — replace it with the fix request?"))) return;
    st.fixFor = a.id; st.fixAt = Date.now();
    await sendPrepared(text, L("Исправить с ИИ", "Fix with AI"));
    if (input && input.value === text) { input.value = draft; input.dispatchEvent(new Event("input")); st.fixFor = ""; }
  }
  function gotoError() {
    const a = art();
    const e = st.errors[st.errIdx];
    if (!a || !e || !e.line) return;
    if (e.path) st.file[a.id] = e.path;
    st.tab = "code"; st.menu = "";
    render();
    const ed = $("#cv-ed");
    if (!ed) return;
    const lines = ed.value.split("\n");
    const start = lines.slice(0, e.line - 1).reduce((n, l) => n + l.length + 1, 0);
    ed.focus();
    ed.setSelectionRange(start, start + (lines[e.line - 1] || "").length);
    const lh = parseFloat(getComputedStyle(ed).lineHeight) || 18;
    ed.scrollTop = Math.max(0, (e.line - 4) * lh);
  }

  let kindJob = 0;
  async function drawKind(a, text) {
    const host = $("#cv-kind");
    if (!host) return;
    const job = ++kindJob;
    try {
      const K = await kinds();
      if (job !== kindJob || !$("#cv-kind")) return;
      st.errors = []; st.errIdx = 0;
      await K[a.type].view($("#cv-kind"), {
        art: a, text, lang: lang(), L, esc, ico, editable: st.tab === "view",
        onError: (message, line) => { if (job === kindJob) addError({ kind: a.type, message: String(message || ""), path: "", line: +line || 0 }); },
        save: (next, note) => saveText(a, next, note),
        say,
      });
      if (job === kindJob) renderErrBar();
    } catch (error) {
      if (job === kindJob && $("#cv-kind")) $("#cv-kind").innerHTML = `<div class="cv-empty">${esc(error.message || String(error))}</div>`;
    }
  }

  async function saveText(a, content, note) {
    const row = versionOf(a, st.v);
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/version`, { method: "POST", body: { content, base: row.v, meta: row.meta || {} } });
      invalidate(st.chatId, a.id);
      await open(a.id, { v: res.version, tab: st.tab, force: true });
      say(note ? L(`${note} · v${res.version}`, `${note} · v${res.version}`) : L(`Сохранено как v${res.version}`, `Saved as v${res.version}`), "ok",
        { label: L("Отменить", "Undo"), run: () => restoreText(a.id, row) });
      return true;
    } catch (error) { say(error.message, "warn"); return false; }
  }
  async function restoreText(id, row) {
    const a = st.full.get(st.chatId + "|" + id) || art();
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(id)}/version`, { method: "POST", body: { content: row.content || "", meta: row.meta || {}, base: a?.head, op: "restore" } });
      invalidate(st.chatId, id);
      await open(id, { v: res.version, tab: st.tab, force: true });
    } catch (error) { say(error.message, "warn"); }
  }
  function renderConsole() {
    const box = $("#cv-console");
    if (!box) return;
    const errors = st.logs.filter((l) => l.level === "error").length;
    const open = box.classList.contains("is-open") || (errors > 0 && !box.dataset.touched);
    box.classList.toggle("is-open", open);
    box.innerHTML = `<button type="button" class="cv-console-head" data-cv-act="console">${ico("terminal")}<span>${esc(L("Консоль", "Console"))}</span>
      <span class="cv-console-count">${st.logs.length ? st.logs.length : ""}${errors ? ` <b class="cv-del">${errors} ${esc(L("ошиб.", "err."))}</b>` : ""}</span></button>
      <div class="cv-console-list">${st.logs.length ? st.logs.map((l) => `<div class="cv-log cv-log-${esc(l.level)}">${esc(l.text)}</div>`).join("")
        : `<div class="cv-log cv-log-empty">${esc(L("Пока пусто — здесь появятся console.log и ошибки страницы", "Empty — console.log output and page errors appear here"))}</div>`}</div>`;
    const list = box.querySelector(".cv-console-list");
    if (list) list.scrollTop = list.scrollHeight;
  }

  async function loadDiff(a, row) {
    const host = $("#cv-diff");
    if (!host) return;
    try {
      const data = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/diff?v=${row.v}`);
      if (st.tab !== "diff" || st.openId !== a.id) return;
      st.diff = data;
      renderDiff();
    } catch (error) {
      host.innerHTML = `<div class="cv-empty">${esc(error.message)}</div>`;
    }
  }
  function renderDiff() {
    const host = $("#cv-diff");
    const data = st.diff;
    if (!host || !data) return;
    const pending = data.review === "pending";
    const hunks = data.hunks || [];
    const decided = Object.keys(st.decisions).length;
    const lines = (text, cls, sign) => String(text || "").replace(/\n$/, "").split("\n").map((l) => `<div class="cv-dl ${cls}"><span class="cv-sign">${sign}</span><span>${esc(l) || "&nbsp;"}</span></div>`).join("");
    const ctx = (text) => lines(String(text || "").split("\n").map((l) => l.length > 160 ? l.slice(0, 157) + "…" : l).join("\n"), "cv-dl-ctx", " ");
    const words = (h) => {
      const del = h.words.filter((w) => w[0] !== "+").map((w) => w[0] === "-" ? `<del>${esc(w[1])}</del>` : esc(w[1])).join("");
      const ins = h.words.filter((w) => w[0] !== "-").map((w) => w[0] === "+" ? `<ins>${esc(w[1])}</ins>` : esc(w[1])).join("");
      return `<div class="cv-dl cv-dl-del cv-dl-words"><span class="cv-sign">−</span><span>${del.replace(/\n$/, "")}</span></div>` +
        `<div class="cv-dl cv-dl-add cv-dl-words"><span class="cv-sign">+</span><span>${ins.replace(/\n$/, "")}</span></div>`;
    };
    const metaChanged = ["subject", "to", "cc"].filter((k) => (data.meta_old || {})[k] !== (data.meta_new || {})[k]);
    const who = data.author === "user" ? L("вы", "you") : L("ИИ", "AI");
    host.innerHTML = `
      <div class="cv-diff-head">
        <span>${esc(L(`v${data.v} против v${data.base} · правка: ${who}`, `v${data.v} vs v${data.base} · edited by ${who}`))} ·
          ${esc(L(`${hunks.length} изм.`, `${hunks.length} change${hunks.length === 1 ? "" : "s"}`))}</span>
        <span class="cv-grow"></span>
        ${pending ? `<button type="button" class="cv-chip" data-cv-act="reject-all">${ico("undo")}${esc(L("Отклонить всё", "Reject all"))}</button>
          <button type="button" class="cv-chip cv-chip-main" data-cv-act="accept-all">${ico("check")}${esc(L("Принять всё", "Accept all"))}</button>`
        : `<span class="cv-review cv-review-${esc(data.review || "none")}">${esc({ accepted: L("Принято", "Accepted"), partial: L("Принято частично", "Partly accepted"), rejected: L("Отклонено", "Rejected") }[data.review] || "")}</span>`}
      </div>
      ${metaChanged.length ? `<div class="cv-hunk"><div class="cv-hunk-body">${metaChanged.map((k) => `<div class="cv-dl cv-dl-ctx"><span class="cv-sign"> </span><span><b>${esc(k === "subject" ? L("Тема", "Subject") : k === "to" ? L("Кому", "To") : L("Копия", "Cc"))}:</b></span></div>` +
        lines((data.meta_old || {})[k] || "", "cv-dl-del", "−") + lines((data.meta_new || {})[k] || "", "cv-dl-add", "+")).join("")}</div></div>` : ""}
      ${hunks.length ? hunks.map((h) => {
        const dec = st.decisions[h.i];
        return `<div class="cv-hunk${dec ? " is-" + dec : ""}" data-hunk="${h.i}">
          <div class="cv-hunk-top"><span>${esc(hunkLabel(h))}</span><span class="cv-grow"></span>
            ${pending ? `<button type="button" class="cv-chip cv-chip-s${dec === "rejected" ? " is-on" : ""}" data-cv-act="hunk-reject" data-i="${h.i}" aria-pressed="${dec === "rejected"}">${ico("undo")}${esc(L("Отклонить", "Reject"))}</button>
              <button type="button" class="cv-chip cv-chip-s cv-chip-ok${dec === "accepted" ? " is-on" : ""}" data-cv-act="hunk-accept" data-i="${h.i}" aria-pressed="${dec === "accepted"}">${ico("check")}${esc(L("Принять", "Accept"))}</button>` : ""}</div>
          <div class="cv-hunk-body">${h.before.trim() ? ctx(h.before) : ""}${h.words ? words(h) : lines(h.old, "cv-dl-del", "−") + lines(h.new, "cv-dl-add", "+")}${h.after.trim() ? ctx(h.after) : ""}</div></div>`;
      }).join("") : `<div class="cv-empty">${esc(L("Текст не изменился", "The text did not change"))}</div>`}
      ${pending && decided && decided < hunks.length ? `<div class="cv-foot cv-diff-foot"><span>${esc(L(`Решено ${decided} из ${hunks.length} — остальные будут приняты`, `${decided} of ${hunks.length} decided — the rest will be accepted`))}</span><span class="cv-grow"></span>
        <button type="button" class="cv-chip cv-chip-main" data-cv-act="apply-decisions">${esc(L("Применить", "Apply"))}</button></div>` : ""}`;
  }
  function hunkLabel(h) {
    if (h.file) {
      if (!h.file_line) {
        const what = { add: L("новый файл", "new file"), delete: L("файл удалён", "file deleted"), rename: L("переименование", "renamed") }[h.file_op];
        return `${h.file} · ${what || L("заголовок файла", "file header")}`;
      }
      const n = Math.max(0, h.new_end - h.new_start) || Math.max(0, h.old_end - h.old_start);
      const span = n > 1 ? L(`строки ${h.file_line}–${h.file_line + n - 1}`, `lines ${h.file_line}–${h.file_line + n - 1}`) : L(`строка ${h.file_line}`, `line ${h.file_line}`);
      return `${h.file} · ${span}`;
    }
    const from = h.new_start + 1, to = h.new_end;
    if (h.new_end <= h.new_start) return L(`Удалено после строки ${h.new_start}`, `Removed after line ${h.new_start}`);
    return to > from ? L(`Строки ${from}–${to}`, `Lines ${from}–${to}`) : L(`Строка ${from}`, `Line ${from}`);
  }
  async function resolve(body) {
    const a = art();
    if (!a || st.busy) return;
    st.busy = true;
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/resolve`, { method: "POST", body: { v: st.v, ...body } });
      st.decisions = {};
      invalidate(st.chatId, a.id);
      await open(a.id, { v: res.version, tab: res.version !== st.v ? "view" : "diff", force: true });
      say(res.rejected ? L(`Принято: ${res.accepted}, отклонено: ${res.rejected}`, `Accepted: ${res.accepted}, rejected: ${res.rejected}`) : L("Изменения приняты", "Changes accepted"));
    } catch (error) { say(error.message, "warn"); }
    finally { st.busy = false; }
  }

  function textForCopy(a, row) {
    if (a.type === "email") {
      const meta = row.meta || {};
      return [meta.to ? `${L("Кому", "To")}: ${meta.to}` : "", meta.subject ? `${L("Тема", "Subject")}: ${meta.subject}` : "", "", row.content || ""].filter((x, i) => x || i >= 2).join("\n").trim();
    }
    return row.content || "";
  }
  async function copy(text) {
    try { if (typeof copyText === "function") { await copyText(text, false); } else await navigator.clipboard.writeText(text); say(L("Скопировано", "Copied")); }
    catch (e) { say(L("Не удалось скопировать", "Could not copy"), "warn"); }
  }
  async function download(fmt) {
    const a = art();
    if (!a) return;
    try {
      const resp = await fetch(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/export?v=${st.v}&fmt=${enc(fmt || "")}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const disp = resp.headers.get("Content-Disposition") || "";
      const star = disp.match(/filename\*=UTF-8''([^;]+)/i);
      const name = star ? decodeURIComponent(star[1]) : (disp.match(/filename="([^"]+)"/i) || [])[1] || "canvas.txt";
      const dataUrl = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(blob); });
      if (typeof saveToDownloads === "function") {
        const saved = await saveToDownloads({ name, url: dataUrl });
        if (saved && saved.ok !== false) { if (typeof savedToast === "function") savedToast(saved); else say(L("Сохранено: ", "Saved: ") + name); return; }
      }
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob); link.download = name; link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    } catch (error) { say(L("Не удалось скачать: ", "Download failed: ") + error.message, "warn"); }
  }
  async function toMemuNote() {
    const a = art();
    try {
      await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/memunote`, { method: "POST", body: { v: st.v } });
      say(L("Открываю в MemuNote — объект связан: сохранения там станут новой версией здесь", "Opening in MemuNote — linked: saves there become a new version here"));
      loadList(st.chatId, true).then(() => { if (isOpen()) render(); }).catch(() => {});
    } catch (error) {
      if (error.payload?.code === "not_installed") {
        say(L("MemuNote не установлен — его можно поставить в Настройки → MemuNote", "MemuNote is not installed — install it in Settings → MemuNote"), "warn",
          typeof openSettings === "function" ? { label: L("Открыть", "Open"), run: () => { try { openSettings("memunote"); } catch (e) {  } } } : null);
      } else say(error.message, "warn");
    }
  }
  async function toWork() {
    const a = art();
    const row = versionOf(a, st.v);
    let ws = {};
    try { ws = S.workspaces || {}; } catch (e) { ws = {}; }
    const project = (ws.items || []).find((x) => x.id === ws.active_id);
    if (!project) { say(L("Сначала выберите проект в режиме Work", "Pick a project in Work mode first"), "warn"); return; }
    const suggested = (String(a.title || "canvas").replace(/[\\/:*?"<>|]+/g, " ").trim().replace(/\s+/g, "-").slice(0, 60) || "canvas") + "." + (a.type === "email" ? "txt" : extOf(a));
    const path = window.prompt(L(`Путь файла в проекте «${project.name}»:`, `File path in the project “${project.name}”:`), suggested);
    if (!path || !path.trim()) return;
    const text = a.type === "email" ? textForCopy(a, row) : row.content || "";
    const write = (allowShrink) => req("/api/work/write", { method: "POST", body: { project_id: project.id, path: path.trim(), text, allow_shrink: !!allowShrink } });
    try {
      const res = await write(false);
      say(L(`Сохранено в Work: ${res.path || path}`, `Saved to Work: ${res.path || path}`));
    } catch (error) {
      if (error.status === 409 && window.confirm(L("Файл уже есть и он больше. Заменить его целиком?", "The file exists and is larger. Replace it entirely?"))) {
        try { const res = await write(true); say(L(`Сохранено в Work: ${res.path || path}`, `Saved to Work: ${res.path || path}`)); } catch (e) { say(e.message, "warn"); }
      } else if (error.status !== 409) say(error.message, "warn");
    }
  }
  async function toModule() {
    const a = art();
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/module`, { method: "POST", body: { v: st.v } });
      say(L(`Модуль «${a.title}» создан (выключен) — откройте его в мастерской`, `Module “${a.title}” created (off) — open it in the workshop`), "ok",
        window.MemuModules ? { label: L("Открыть", "Open"), run: async () => { try { await MemuModules.refresh?.(); MemuModules.editCode?.(res.id); } catch (e) {  } } } : null);
      try { await window.MemuModules?.refresh?.(); } catch (e) {  }
    } catch (error) { say(error.message, "warn"); }
  }
  async function removeArt() {
    const a = art();
    if (!window.confirm(L(`Удалить «${a.title}» из холста со всеми версиями?`, `Remove “${a.title}” with all versions from Canvas?`))) return;
    try {
      await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/delete`, { method: "POST", body: {} });
      invalidate(st.chatId);
      st.draft = null;
      close();
      syncLauncher();
    } catch (error) { say(error.message, "warn"); }
  }
  async function saveDraft() {
    const a = art();
    const d = st.draft;
    if (!a || !d || !d.dirty || st.busy) return;
    st.busy = true;
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/version`, { method: "POST", body: { content: d.text, base: d.v, meta: d.meta } });
      st.draft = null;
      invalidate(st.chatId, a.id);
      await open(a.id, { v: res.version, tab: "view", force: true });
      say(L(`Сохранено как v${res.version}`, `Saved as v${res.version}`));
    } catch (error) { say(error.message, "warn"); }
    finally { st.busy = false; }
  }
  async function rename() {
    const a = art();
    const title = window.prompt(L("Название:", "Title:"), a.title);
    if (!title || !title.trim() || title.trim() === a.title) return;
    try { await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/rename`, { method: "POST", body: { title: title.trim() } }); invalidate(st.chatId, a.id); await open(a.id, { v: st.v, tab: st.tab, force: true }); }
    catch (error) { say(error.message, "warn"); }
  }
  async function onPaneClick(event) {
    const tab = event.target.closest("[data-cv-tab]");
    if (tab && !tab.disabled) {
      if (st.draft?.dirty && tab.dataset.cvTab !== "code" && !window.confirm(L("Уйти без сохранения правок?", "Leave without saving your edits?"))) return;
      if (tab.dataset.cvTab !== "code") st.draft = null;
      st.tab = tab.dataset.cvTab; st.menu = ""; render(); return;
    }
    const btn = event.target.closest("[data-cv-act]");
    if (!btn || btn.tagName === "SELECT") { if (st.menu && !event.target.closest(".cv-menu")) setMenu(""); return; }
    const act = btn.dataset.cvAct;
    const a = art();
    if (!a) return;
    const row = versionOf(a, st.v);
    if (act !== "menu-dl" && act !== "menu-more" && st.menu) setMenu("");
    switch (act) {
      case "close": close(); return;
      case "copy": copy(textForCopy(a, row)); return;
      case "copy-body": copy(row.content || ""); return;
      case "copy-mail": copy(textForCopy(a, row)); return;
      case "copy-to": copy((row.meta || {}).to || ""); return;
      case "copy-cc": copy((row.meta || {}).cc || ""); return;
      case "copy-subject": copy((row.meta || {}).subject || ""); return;
      case "menu-dl": setMenu(st.menu === "dl" ? "" : "dl"); return;
      case "menu-more": setMenu(st.menu === "more" ? "" : "more"); return;
      case "dl-csv": download("csv"); return;
      case "dl-xlsx": download("xlsx"); return;
      case "dl-txt": download("txt"); return;
      case "dl-eml": download("eml"); return;
      case "dl-file": download(""); return;
      case "dl-zip": download("zip"); return;
      case "dl-html": download("html"); return;
      case "dl-cur": { const f = curFile(a, unpackFiles(row.content)); if (f) download("file:" + f.path); return; }
      case "dl-svg": case "dl-png": case "dl-pptx": exportKind(a, row, act.slice(3)); return;
      case "file-pick":
        if (st.draft?.dirty && st.tab !== "code" && !window.confirm(L("Уйти без сохранения правок?", "Leave without saving your edits?"))) return;
        st.file[a.id] = btn.dataset.path;
        render();
        return;
      case "file-add": fileOp(a, "add"); return;
      case "file-del": fileOp(a, "delete", btn.dataset.path); return;
      case "err-fix": fixWithAI(); return;
      case "err-goto": gotoError(); return;
      case "err-hide": st.errHidden = true; renderErrBar(); return;
      case "err-prev": st.errIdx = Math.max(0, st.errIdx - 1); renderErrBar(); return;
      case "err-next": st.errIdx = Math.min(st.errors.length - 1, st.errIdx + 1); renderErrBar(); return;
      case "memunote": toMemuNote(); return;
      case "work": toWork(); return;
      case "module": toModule(); return;
      case "delete": removeArt(); return;
      case "wide": st.wide = !st.wide; applyWidth(); btn.setAttribute("aria-pressed", String(st.wide));
        btn.title = st.wide ? L("Обычная ширина", "Normal width") : L("Шире", "Wider"); return;
      case "rename": rename(); return;
      case "make-head":

        try {
          const base = pathHeads(messagesOf(st.chatId), st.list)[a.id] || a.head;
          const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/version`, { method: "POST",
            body: { content: row.content || "", meta: row.meta || {}, base, op: "restore" } });
          invalidate(st.chatId, a.id);
          await open(a.id, { v: res.version, tab: "view", force: true });
          say(L(`Версия v${row.v} восстановлена как v${res.version}`, `v${row.v} restored as v${res.version}`));
        }
        catch (error) { say(error.message, "warn"); }
        return;
      case "rerun": runPreview(a, row); return;
      case "console": { const box = $("#cv-console"); if (box) { box.dataset.touched = "1"; box.classList.toggle("is-open"); } return; }
      case "accept-all": resolve({ decision: "accept" }); return;
      case "reject-all": resolve({ decision: "reject" }); return;
      case "hunk-accept": case "hunk-reject": {
        const i = +btn.dataset.i;
        const want = act === "hunk-accept" ? "accepted" : "rejected";
        if (st.decisions[i] === want) delete st.decisions[i]; else st.decisions[i] = want;
        const total = (st.diff?.hunks || []).length;
        if (Object.keys(st.decisions).length >= total && total) { applyDecisions(); return; }
        renderDiff(); return;
      }
      case "apply-decisions": applyDecisions(); return;
      case "ed-save": saveDraft(); return;
      case "ed-cancel": st.draft = null; render(); return;
      default: return;
    }
    render();
  }

  async function fileOp(a, op, path = "") {
    if (st.draft?.dirty) { say(L("Сначала сохраните или отмените правки", "Save or discard your edits first"), "warn"); return; }
    const row = versionOf(a, st.v);
    let body;
    if (op === "add") {
      const name = window.prompt(L("Имя нового файла (например, about.html, style.css, data.json):", "New file name (e.g. about.html, style.css, data.json):"), "");
      if (!name || !name.trim()) return;
      body = { path: name.trim(), content: "" };
    } else if (op === "delete") {
      if (!window.confirm(L(`Удалить файл ${path}? Он останется в прежних версиях.`, `Delete ${path}? It stays in the earlier versions.`))) return;
      body = { path, delete: true };
    } else {
      const name = window.prompt(L("Новое имя файла:", "New file name:"), path);
      if (!name || !name.trim() || name.trim() === path) return;
      body = { path, rename_to: name.trim() };
    }
    try {
      const res = await req(`/api/pg/canvas/${enc(st.chatId)}/${enc(a.id)}/file`, { method: "POST", body: { ...body, base: row.v } });
      if (op === "add") st.file[a.id] = body.path.replace(/\\/g, "/").replace(/^\.?\//, "");
      if (op === "rename") st.file[a.id] = body.rename_to;
      invalidate(st.chatId, a.id);
      await open(a.id, { v: res.version, tab: op === "add" ? "code" : st.tab, force: true });
    } catch (error) { say(error.message, "warn"); }
  }
  async function exportKind(a, row, fmt) {
    try {
      const K = await kinds();
      const out = await K[a.type].export(row.content || "", fmt, { lang: lang(), title: a.title });
      const name = (String(a.title || "canvas").replace(/[\\/:*?"<>|]+/g, " ").trim() || "canvas") + "." + fmt;
      const dataUrl = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(out); });
      if (typeof saveToDownloads === "function") {
        const saved = await saveToDownloads({ name, url: dataUrl });
        if (saved && saved.ok !== false) { if (typeof savedToast === "function") savedToast(saved); else say(L("Сохранено: ", "Saved: ") + name); return; }
      }
      const link = document.createElement("a");
      link.href = URL.createObjectURL(out); link.download = name; link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    } catch (error) { say(L("Не удалось скачать: ", "Download failed: ") + (error.message || error), "warn"); }
  }

  function setMenu(kind) {
    st.menu = kind;
    const head = $("#canvas-pane .cv-head");
    if (!head) return;
    head.querySelector(".cv-menu")?.remove();
    const a = art();
    if (kind && a) head.insertAdjacentHTML("beforeend", menuHtml(a));
    head.querySelectorAll('[data-cv-act="menu-dl"], [data-cv-act="menu-more"]').forEach((b) =>
      b.setAttribute("aria-expanded", String(b.dataset.cvAct === "menu-" + kind)));
  }
  function applyDecisions() {
    const total = (st.diff?.hunks || []).length;
    const reject = Object.entries(st.decisions).filter(([, d]) => d === "rejected").map(([i]) => +i);
    if (!reject.length) { resolve({ decision: "accept" }); return; }
    if (reject.length >= total) { resolve({ decision: "reject" }); return; }
    resolve({ reject });
  }
  async function onPaneChange(event) {
    const el = event.target;
    if (el.dataset.cvAct === "pick-ver") {
      if (st.draft?.dirty && !window.confirm(L("Перейти к другой версии без сохранения правок?", "Switch versions without saving your edits?"))) { el.value = String(st.v); return; }
      st.v = +el.value; st.draft = null; st.decisions = {};
      const row = versionOf(art(), st.v);
      if (st.tab === "diff" && !row.parent) st.tab = "view";
      render();
    } else if (el.dataset.cvAct === "pick-art") {
      open(el.value, { tab: "view" });
    }
  }
  let liveTimer = 0;
  function onPaneInput(event) {
    const el = event.target;
    if (!st.draft) return;
    if (el.id === "cv-ed" && st.draft.files) {
      const cur = curFile(art(), st.draft.files);
      if (cur) cur.content = el.value;
      st.draft.text = packFiles(st.draft.files);
    } else if (el.id === "cv-ed") {
      st.draft.text = el.value;
      const a = art();
      if (a && (a.type === "diagram" || a.type === "slides")) {
        clearTimeout(liveTimer);
        liveTimer = setTimeout(() => { if (st.tab === "code" && st.draft) drawKind(a, st.draft.text); }, 450);
      }
    }
    else if (el.dataset.cvMeta) st.draft.meta[el.dataset.cvMeta] = el.value;
    else return;
    const row = versionOf(art(), st.draft.v);
    const dirty = st.draft.text !== (row.content || "") || ["to", "subject"].some((k) => (st.draft.meta[k] || "") !== ((row.meta || {})[k] || ""));
    if (dirty !== st.draft.dirty) {
      st.draft.dirty = dirty;
      const foot = $("#canvas-pane .cv-editor .cv-foot");
      if (foot) {
        foot.querySelector(".cv-hint").textContent = dirty ? L("Есть несохранённые правки · Ctrl+S — сохранить", "Unsaved edits · Ctrl+S to save") : L("Правьте прямо здесь — сохранится новой версией", "Edit right here — saved as a new version");
        foot.querySelectorAll("button").forEach((b) => { b.disabled = !dirty; });
      }
    }
  }
  function onPaneKey(event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && st.tab === "code") { event.preventDefault(); saveDraft(); return; }
    if (event.key === "Escape") {
      if (st.menu) { setMenu(""); event.stopPropagation(); return; }
      if (!event.target.closest("textarea, input")) { close(); event.stopPropagation(); }
    }
    if (event.key === "Tab" && event.target.id === "cv-ed" && !event.shiftKey && art()?.type === "code") {
      event.preventDefault();
      const el = event.target; const s = el.selectionStart, e = el.selectionEnd;
      el.setRangeText("  ", s, e, "end"); el.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  function syncLauncher() {
    const wrap = $("#chat-view-wrap");
    if (!wrap) return;
    let btn = $("#canvas-launcher");
    const chatId = currentChatId();
    const count = st.listChat === chatId ? st.list.length : 0;
    if (!count || isOpen()) { if (btn) btn.hidden = true; return; }
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button"; btn.id = "canvas-launcher"; btn.className = "cv-launcher";
      btn.addEventListener("click", () => {
        const list = st.list.slice().sort((x, y) => (y.updated || 0) - (x.updated || 0));
        if (list[0]) open(list[0].id);
      });
      wrap.appendChild(btn);
    }
    btn.hidden = false;
    btn.innerHTML = `${ico("canvas")}<span>${esc(L("Холст", "Canvas"))}</span>${count > 1 ? `<b>${count}</b>` : ""}`;
    btn.title = L("Открыть холст этого чата", "Open this chat's canvas");
  }
  let lastChat = null;
  async function onChatMaybeChanged() {
    const chatId = currentChatId();
    if (chatId === lastChat) return;
    lastChat = chatId;
    if (isOpen() && st.chatId !== chatId) close();
    if (!chatId) { st.list = []; st.listChat = ""; syncLauncher(); return; }
    try { await loadList(chatId, true); } catch (e) { st.list = []; st.listChat = chatId; }
    if (currentChatId() === chatId) syncLauncher();
  }

  document.addEventListener("memu:chat-open", onChatMaybeChanged);
  if (typeof S !== "undefined" && S.currentId) onChatMaybeChanged();
  window.addEventListener("memu:branch-switch", (event) => {
    if (!isOpen() || event.detail?.chatId !== st.chatId) return;
    const heads = pathHeads(messagesOf(st.chatId), st.list);
    if (heads[st.openId] && heads[st.openId] !== st.v) { st.v = heads[st.openId]; st.draft = null; st.decisions = {}; render(); }
  });
  document.addEventListener("memu:lang", () => { if (isOpen()) render(); syncLauncher(); });

  window.addEventListener("focus", () => {
    const a = isOpen() && art();
    if (a && linkOf(a) && !(st.draft && st.draft.dirty)) loadList(st.chatId, true).catch(() => {});
  });
  window.addEventListener("resize", () => { const bd = $("#canvas-backdrop"); if (bd && isOpen()) bd.hidden = !isNarrow(); applyWidth(); });
  document.addEventListener("pointerdown", (event) => {
    if (st.menu && !event.target.closest("#canvas-pane")) setMenu("");
  }, true);
  document.addEventListener("click", (event) => {
    const card = event.target.closest("[data-cv-open]");
    if (!card) return;
    event.preventDefault();
    if (isOpen() && st.openId === card.dataset.cvOpen && +card.dataset.cvV === st.v) { close(); return; }
    open(card.dataset.cvOpen, { v: +card.dataset.cvV || 0 });
  });

  window.MemuCanvas = {
    isTool: (name) => TOOL_RE.test(String(name || "")),
    runTool, promptBlock, historyNote, cardsHtml, open, close, isOpen,
    refresh: async () => { const chatId = currentChatId(); if (chatId) { invalidate(chatId); await loadList(chatId, true).catch(() => []); } syncLauncher(); if (isOpen()) await open(st.openId, { v: st.v, tab: st.tab }); },

    _pathHeads: pathHeads, _parseTable: parseTable, _sandboxDoc: sandboxDoc, CSP,
  };
})();
