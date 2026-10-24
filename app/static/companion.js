/* MemuAI Proxy — встроенный ИИ-помощник: выдвижная панель, которая разбирает ошибки
   с учётом базы знаний о провайдерах и текущего состояния этого Proxy. */
"use strict";

window.MemuCompanion = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const STORE = "oh_companion_chat";
  const S = { history: load(), context: null, busy: false, controller: null, info: null, models: [] };

  function load() {
    try {
      const rows = JSON.parse(localStorage.getItem(STORE) || "[]");
      return Array.isArray(rows) ? rows.filter((r) => r && (r.role === "user" || r.role === "assistant")).slice(-40) : [];
    } catch (error) { return []; }
  }
  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(S.history.slice(-40))); } catch (error) { /* хранилище недоступно */ }
  }

  const panel = () => $("#companion");
  const isOpen = () => panel() && !panel().hidden;

  /* ---------------- разметка ответа ---------------- */

  function inline(text) {
    return esc(text)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,:;!?]|$)/g, "$1<i>$2</i>")
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  /** A small, safe Markdown subset: code blocks, headings, lists, quotes, paragraphs. */
  function markdown(source) {
    const out = [];
    const lines = String(source || "").replace(/\r/g, "").split("\n");
    let list = null;
    let para = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };
    const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+-]*)/);
      if (fence) {
        flushPara(); flushList();
        const body = [];
        for (i += 1; i < lines.length && !/^\s*```/.test(lines[i]); i += 1) body.push(lines[i]);
        out.push(`<div class="comp-code"><button type="button" class="comp-copy" data-copy-code aria-label="${esc(L("Скопировать", "Copy"))}" title="${esc(L("Скопировать", "Copy"))}">⧉</button><pre><code>${esc(body.join("\n"))}</code></pre></div>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const number = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const quote = line.match(/^>\s?(.*)$/);
      if (heading) { flushPara(); flushList(); out.push(`<h4>${inline(heading[2])}</h4>`); }
      else if (bullet || number) {
        flushPara();
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
        list.items.push((bullet || number)[1]);
      } else if (quote) { flushPara(); flushList(); out.push(`<blockquote>${inline(quote[1])}</blockquote>`); }
      else if (!line.trim()) { flushPara(); flushList(); }
      else if (list && /^\s{2,}\S/.test(line)) list.items[list.items.length - 1] += " " + line.trim();
      else { flushList(); para.push(line); }
    }
    flushPara(); flushList();
    return out.join("");
  }

  /* ---------------- отрисовка ---------------- */

  const SUGGEST = [
    () => L("Почему аккаунт в статусе «Недействителен»?", "Why is an account marked “Invalid”?"),
    () => L("Как подключить Proxy к Cursor или Cline?", "How do I connect the Proxy to Cursor or Cline?"),
    () => L("Как запустить модель прямо на компьютере?", "How do I run a model on this computer?"),
    () => L("Что делать при ошибке 429?", "What should I do about error 429?"),
  ];

  function render() {
    const log = $("#companion-log");
    if (!log) return;
    if (!S.history.length && !S.busy) {
      log.innerHTML = `<div class="comp-empty">
        <div class="comp-hello">✦</div>
        <p>${esc(L("Я знаю, как устроены провайдеры Proxy, и вижу состояние ваших аккаунтов и последние ошибки. Ключи и куки мне не передаются.",
          "I know how the Proxy providers work and can see your account states and recent errors. Keys and cookies are never sent to me."))}</p>
        <div class="comp-suggest">${SUGGEST.map((s) => `<button type="button" class="chip-btn" data-suggest>${esc(s())}</button>`).join("")}</div></div>`;
      return;
    }
    log.innerHTML = S.history.map((m, i) => bubble(m, i)).join("");
    log.scrollTop = log.scrollHeight;
  }

  function bubble(m, index) {
    if (m.role === "user") {
      return `<div class="comp-msg user">${m.context ? `<div class="comp-ctx-chip">⚠ ${esc(contextTitle(m.context))}</div>` : ""}<div class="comp-text">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
    }
    return `<div class="comp-msg bot ${m.error ? "err" : ""}" data-index="${index}">
      ${m.reasoning ? `<details class="comp-think"><summary>${esc(L("Размышления", "Reasoning"))}</summary><div>${esc(m.reasoning)}</div></details>` : ""}
      <div class="comp-text selectable">${m.error ? `<b>${esc(L("Помощник не ответил", "The companion did not answer"))}</b><p>${esc(m.content)}</p>${m.hint ? `<p class="muted">${esc(m.hint)}</p>` : ""}` : markdown(m.content) || '<span class="comp-typing"><i></i><i></i><i></i></span>'}</div>
      ${m.model && !m.pending ? `<div class="comp-foot"><span>${esc(m.model)}</span><button type="button" class="comp-copy-msg" data-copy-msg>${esc(L("Скопировать", "Copy"))}</button></div>` : ""}
    </div>`;
  }

  function contextTitle(ctx) {
    return [ctx.provider, ctx.model, ctx.status ? `HTTP ${ctx.status}` : "", ctx.account].filter(Boolean).join(" · ")
      || String(ctx.message || "").slice(0, 80) || L("ошибка", "error");
  }

  function renderContext() {
    const box = $("#companion-context");
    if (!box) return;
    box.hidden = !S.context;
    if (S.context) {
      box.innerHTML = `<span>⚠ ${esc(L("Разбираем", "About"))}: ${esc(contextTitle(S.context))}</span>
        <button type="button" class="icon-btn" id="companion-ctx-drop" aria-label="${esc(L("Убрать контекст ошибки", "Remove the error context"))}">✕</button>`;
      $("#companion-ctx-drop").onclick = () => { S.context = null; renderContext(); };
    }
  }

  /* ---------------- модели ---------------- */

  async function loadInfo() {
    try {
      const [info, models] = await Promise.all([api("/api/admin/companion"), api("/api/admin/models")]);
      S.info = info;
      S.models = (models.models || []).filter((m) => m.enabled !== false
        && !(m.caps?.music && m.caps?.text !== true) && !(m.capabilities?.music && m.capabilities?.text !== true)).map((m) => m.id).sort();
    } catch (error) { S.models = []; }
    const select = $("#companion-model");
    const auto = S.info && S.info.model;
    const saved = (S.info && S.info.saved) || "";
    // "Auto" follows the Proxy's own choice; an explicit pick is remembered in the settings.
    select.innerHTML = `<option value="">${esc(auto ? `${L("Авто", "Auto")} · ${auto}` : L("Нет включённых моделей", "No enabled models"))}</option>`
      + S.models.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
    select.value = S.models.includes(saved) ? saved : "";
    $("#companion-model-label").textContent = select.value || auto || "";
  }

  /* ---------------- диалог ---------------- */

  function open(focus = true) {
    const el = panel();
    if (!el) return;
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add("open"));
    $("#companion-fab").hidden = true;
    document.body.classList.add("companion-open");
    render();
    renderContext();
    if (!S.info) loadInfo();
    if (focus) setTimeout(() => $("#companion-input")?.focus(), 60);
  }

  function close() {
    const el = panel();
    if (!el) return;
    el.classList.remove("open");
    el.hidden = true;
    $("#companion-fab").hidden = !document.body.classList.contains("app-ready");
    const wasInside = el.contains(document.activeElement);
    document.body.classList.remove("companion-open");
    if (wasInside) $("#companion-open")?.focus();
  }

  async function send(text) {
    text = String(text ?? $("#companion-input").value).trim();
    if (S.busy) return;
    if (!text && !S.context) return;
    const context = S.context;
    const question = text || L("Что означает эта ошибка и как её исправить?", "What does this error mean and how do I fix it?");
    const history = S.history.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.content }));
    S.history.push({ role: "user", content: question, context: context || undefined });
    const reply = { role: "assistant", content: "", reasoning: "", model: "", pending: true };
    S.history.push(reply);
    S.context = null;
    $("#companion-input").value = "";
    autosize();
    renderContext();
    setBusy(true);
    render();
    const controller = new AbortController();
    S.controller = controller;
    try {
      const resp = await fetch("/api/admin/companion/chat", {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: "Bearer " + TOKEN } : {}) },
        body: JSON.stringify({ message: question, history, error: context, lang: LANG, model: $("#companion-model").value }),
      });
      if (resp.status === 401) { showLogin(""); throw new Error(L("Требуется вход", "Sign-in required")); }
      if (!resp.ok || !resp.body) {
        let data = null;
        try { data = await resp.json(); } catch (error) { /* не JSON */ }
        throw new Error((data && (LANG === "en" ? data.error_en || data.error : data.error)) || `HTTP ${resp.status}`);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let frame = 0;
      const paint = () => { frame = 0; updateLast(); };
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        // SSE допускает CRLF: без нормализации кадры «\r\n\r\n» не делились бы
        // по «\n\n», и ответ появлялся бы только целиком (или не появлялся).
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch (error) { continue; }
          if (event.type === "meta") { reply.model = event.model; $("#companion-model-label").textContent = event.model; }
          else if (event.type === "text") reply.content += event.text;
          else if (event.type === "reasoning") reply.reasoning += event.text;
          else if (event.type === "error") { reply.error = true; reply.content = event.error; reply.hint = event.hint || ""; }
          if (!frame) frame = requestAnimationFrame(paint);
        }
      }
      if (!reply.content && !reply.error) { reply.error = true; reply.content = L("Модель вернула пустой ответ. Попробуйте другую модель помощника.", "The model returned an empty answer. Try another companion model."); }
    } catch (error) {
      if (error && error.name === "AbortError") {
        if (!reply.content) { reply.error = true; reply.content = L("Остановлено.", "Stopped."); }
      } else {
        reply.error = true;
        reply.content = error && error.message ? error.message : String(error);
      }
    } finally {
      reply.pending = false;
      S.controller = null;
      setBusy(false);
      save();
      render();
    }
  }

  /** Repaint only the streaming bubble, keeping the scroll pinned if the user is at the bottom. */
  function updateLast() {
    const log = $("#companion-log");
    const last = log && log.lastElementChild;
    const index = S.history.length - 1;
    if (!last || !last.classList.contains("bot")) { render(); return; }
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
    last.outerHTML = bubble(S.history[index], index);
    if (stick) log.scrollTop = log.scrollHeight;
  }

  function setBusy(on) {
    S.busy = on;
    const btn = $("#companion-send");
    btn.textContent = on ? L("Стоп", "Stop") : t("comp.send");
    btn.classList.toggle("danger", on);
    btn.type = on ? "button" : "submit";
  }

  function autosize() {
    const input = $("#companion-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(180, input.scrollHeight)}px`;
  }

  /** Prepare an editable question; the error context is sent only on submit. */
  function ask(text, context) {
    S.context = context ? clean(context) : null;
    open(false);
    renderContext();
    const input = $("#companion-input");
    const question = String(text || L("Что означает эта ошибка и как её исправить?", "What does this error mean and how do I fix it?")).trim();
    const draft = input.value.trim();
    input.value = draft && draft !== question ? draft + "\n\n" + question : question;
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  function clean(ctx) {
    const out = {};
    Object.entries(ctx).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") out[k] = typeof v === "string" ? v.slice(0, 3000) : v; });
    return out;
  }

  /* ---------------- события ---------------- */

  function bind() {
    $("#companion-open").onclick = () => (isOpen() ? close() : open());
    $("#companion-fab").onclick = () => open();
    $("#companion-close").onclick = close;
    $("#companion-new").onclick = () => {
      if (S.controller) S.controller.abort();
      S.history = [];
      S.context = null;
      save();
      render();
      renderContext();
      $("#companion-input").focus();
    };
    $("#companion-new").title = t("comp.new");
    $("#companion-form").onsubmit = (event) => { event.preventDefault(); send(); };
    $("#companion-send").addEventListener("click", (event) => {
      if (S.busy && S.controller) { event.preventDefault(); S.controller.abort(); }
    });
    const input = $("#companion-input");
    input.addEventListener("input", autosize);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
    });
    $("#companion-model").onchange = async (event) => {
      const model = event.target.value;
      $("#companion-model-label").textContent = model || (S.info && S.info.model) || "";
      try {
        await api("/api/admin/companion/model", { method: "POST", body: { model } });
        if (S.info) S.info.saved = model;
      } catch (error) { toast(error.message, "err"); }
    };
    $("#companion-log").addEventListener("click", (event) => {
      const suggestion = event.target.closest("[data-suggest]");
      if (suggestion) return send(suggestion.textContent);
      const code = event.target.closest("[data-copy-code]");
      if (code) return copyText(code.parentElement.querySelector("code").textContent);
      const msg = event.target.closest("[data-copy-msg]");
      if (msg) {
        const row = S.history[Number(msg.closest("[data-index]").dataset.index)];
        return row && copyText(row.content);
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isOpen() && !$("#modal-back").classList.contains("visible") && panel().contains(document.activeElement)) {
        event.preventDefault();
        close();
      }
    });
    // Buttons next to failed log rows and broken accounts.
    document.addEventListener("click", (event) => {
      const logBtn = event.target.closest("[data-ask-log]");
      if (logBtn && typeof logsCache !== "undefined") {
        event.stopPropagation();
        const l = logsCache[Number(logBtn.dataset.askLog)];
        if (!l) return;
        const stages = (l.stages || []).slice(-6).map((st) => `${st.step}${st.payload ? `: ${JSON.stringify(st.payload).slice(0, 400)}` : ""}`).join("\n");
        return ask("", { source: "log", status: l.status, model: l.model, provider: l.provider, account: l.account,
          error_code: l.error_code, message: l.error, request_id: l.request_id, stages });
      }
      const accBtn = event.target.closest("[data-ask-acc]");
      if (accBtn && typeof accountsCache !== "undefined") {
        event.stopPropagation();
        const a = accountsCache.find((x) => x.id === accBtn.dataset.askAcc);
        if (!a) return;
        return ask(L("Почему этот аккаунт не работает и как его починить?", "Why is this account not working and how do I fix it?"),
          { source: "account", provider: a.provider, account: a.label || a.id, status_name: a.status, message: a.status_message });
      }
    }, true);
  }

  /** Called after sign-in: the floating button appears only inside the panel. */
  function ready() { document.body.classList.add("app-ready"); if (!isOpen()) $("#companion-fab").hidden = false; S.info = null; }

  bind();
  // app.js may have shown the panel before this script loaded.
  if ($("#app").classList.contains("visible") && $("#login-overlay").hidden) ready();

  return {
    ask, open, close, ready, markdown,
    relabel() { $("#companion-new").title = t("comp.new"); if (!S.busy) setBusy(false); render(); renderContext(); },
    signedOut() { if (S.controller) S.controller.abort(); document.body.classList.remove("app-ready"); close(); $("#companion-fab").hidden = true; },
  };
})();
