/* MemuAI Proxy — вкладка «Чат»: простой разговор с любой моделью Proxy.
 *
 * Это инструмент проверки, а не чат-приложение: здесь нет системного промпта,
 * памяти, инструментов и скрытых блоков. Модель получает ровно то, что
 * написано в поле ввода (и, по желанию, свой системный промпт из
 * «Параметров»). Запрос идёт через обычный конвейер Proxy и виден в логах,
 * поэтому вкладка удобна, чтобы быстро проверить аккаунт или модель.
 */
"use strict";

window.MemuProxyChat = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const STORE = "oh_proxy_chat";
  const PREFS = "oh_proxy_chat_prefs";
  const MAX_IMAGE = 8 * 1024 * 1024;
  const S = {
    models: [], history: load(STORE, []), prefs: load(PREFS, {}),
    images: [], busy: false, controller: null, ready: false,
  };

  function load(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      if (Array.isArray(fallback)) return Array.isArray(value) ? value.filter((m) => m && (m.role === "user" || m.role === "assistant")).slice(-80) : fallback;
      return value && typeof value === "object" ? value : fallback;
    } catch (error) { return fallback; }
  }
  function save() {
    try {
      // Pictures are not kept between sessions: a few of them would fill localStorage.
      const rows = S.history.slice(-80).map((m) => (m.images ? { ...m, images: undefined, hadImages: m.images.length } : m));
      localStorage.setItem(STORE, JSON.stringify(rows));
      localStorage.setItem(PREFS, JSON.stringify(S.prefs));
    } catch (error) { /* хранилище недоступно */ }
  }

  const md = (text) => (window.MemuCompanion && MemuCompanion.markdown ? MemuCompanion.markdown(text) : esc(text).replace(/\n/g, "<br>"));
  const errorOf = (error) => (error && error.message ? error.message : String(error || ""));
  const tokens = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n));

  /* ---------------- каркас ---------------- */

  function shell(root) {
    root.dataset.ready = "1";
    root.innerHTML = `
      <div class="pc-layout">
        <div class="card pc-bar">
          <div class="field pc-model-field"><label for="pc-model">${esc(L("Модель", "Model"))}</label>
            <input id="pc-model" list="pc-models" class="mono" autocomplete="off" spellcheck="false" placeholder="${esc(L("Начните вводить название…", "Start typing a name…"))}">
            <datalist id="pc-models"></datalist></div>
          <div class="pc-bar-actions">
            <button type="button" class="btn small" id="pc-params-toggle" aria-expanded="false">${esc(L("Параметры", "Parameters"))}</button>
            <button type="button" class="btn small" id="pc-clear">${esc(L("Новый разговор", "New conversation"))}</button>
          </div>
          <div class="pc-params" id="pc-params" hidden>
            <div class="field"><label for="pc-system">${esc(L("Системный промпт (необязательно)", "System prompt (optional)"))}</label>
              <textarea id="pc-system" rows="2" placeholder="${esc(L("Пусто — модель получит только ваши сообщения", "Empty — the model receives only your messages"))}"></textarea></div>
            <div class="grid-3">
              <div class="field"><label for="pc-temp">${esc(L("Температура", "Temperature"))}</label>
                <input id="pc-temp" type="text" inputmode="decimal" placeholder="${esc(L("по умолчанию", "default"))}"></div>
              <div class="field"><label for="pc-max">${esc(L("Макс. токенов ответа", "Max answer tokens"))}</label>
                <input id="pc-max" type="text" inputmode="numeric" placeholder="${esc(L("по умолчанию", "default"))}"></div>
              <div class="field"><label>${esc(L("Возможности", "Features"))}</label>
                <label class="pc-check"><input type="checkbox" id="pc-search"> ${esc(L("Веб-поиск", "Web search"))}</label>
                <label class="pc-check"><input type="checkbox" id="pc-think"> ${esc(L("Размышления", "Reasoning"))}</label></div>
            </div>
            <p class="field-hint">${esc(L("Это чат для проверки: без скрытых системных промптов, памяти и инструментов. Запросы видны в «Логах».",
              "A test chat: no hidden system prompts, memory or tools. Requests show up in Logs."))}</p>
          </div>
        </div>
        <div class="card pc-chat">
          <div class="pc-log" id="pc-log" role="log" aria-live="polite"></div>
          <form class="pc-composer" id="pc-form">
            <div class="pc-attach" id="pc-attach" hidden></div>
            <div class="pc-input-row">
              <button type="button" class="icon-btn" id="pc-image" title="${esc(L("Прикрепить изображение", "Attach an image"))}" aria-label="${esc(L("Прикрепить изображение", "Attach an image"))}">▣</button>
              <input type="file" id="pc-file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden>
              <textarea id="pc-input" rows="1" placeholder="${esc(L("Сообщение модели… (Enter — отправить, Shift+Enter — новая строка)", "Message the model… (Enter to send, Shift+Enter for a new line)"))}"></textarea>
              <button type="submit" class="btn primary" id="pc-send">${esc(L("Отправить", "Send"))}</button>
            </div>
          </form>
        </div>
      </div>`;
    const input = $("#pc-input");
    $("#pc-form").onsubmit = (event) => { event.preventDefault(); S.busy ? stop() : send(); };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!S.busy) send(); }
    });
    input.addEventListener("input", autosize);
    input.addEventListener("paste", (event) => {
      const files = Array.from(event.clipboardData?.files || []).filter((f) => f.type.startsWith("image/"));
      if (files.length) { event.preventDefault(); addImages(files); }
    });
    $("#pc-image").onclick = () => $("#pc-file").click();
    $("#pc-file").onchange = (event) => { addImages(Array.from(event.target.files || [])); event.target.value = ""; };
    $("#pc-clear").onclick = () => {
      if (S.busy) stop();
      S.history = [];
      save();
      render();
      input.focus();
    };
    $("#pc-params-toggle").onclick = () => {
      const box = $("#pc-params");
      box.hidden = !box.hidden;
      $("#pc-params-toggle").setAttribute("aria-expanded", String(!box.hidden));
    };
    const remember = () => {
      S.prefs = { model: $("#pc-model").value.trim(), system: $("#pc-system").value, temperature: $("#pc-temp").value.trim(),
        max_tokens: $("#pc-max").value.trim(), web_search: $("#pc-search").checked, thinking: $("#pc-think").checked };
      save();
    };
    ["pc-model", "pc-system", "pc-temp", "pc-max"].forEach((id) => $("#" + id).addEventListener("change", remember));
    ["pc-search", "pc-think"].forEach((id) => $("#" + id).addEventListener("change", remember));
    $("#pc-system").value = S.prefs.system || "";
    $("#pc-temp").value = S.prefs.temperature || "";
    $("#pc-max").value = S.prefs.max_tokens || "";
    $("#pc-search").checked = !!S.prefs.web_search;
    $("#pc-think").checked = !!S.prefs.thinking;
    if (S.prefs.system || S.prefs.temperature || S.prefs.max_tokens || S.prefs.web_search || S.prefs.thinking) {
      $("#pc-params").hidden = false;
      $("#pc-params-toggle").setAttribute("aria-expanded", "true");
    }
    root.addEventListener("click", onClick);
    const log = $("#pc-log");
    log.addEventListener("dragover", (event) => { if (event.dataTransfer?.types?.includes("Files")) event.preventDefault(); });
    log.addEventListener("drop", (event) => {
      const files = Array.from(event.dataTransfer?.files || []).filter((f) => f.type.startsWith("image/"));
      if (files.length) { event.preventDefault(); addImages(files); }
    });
  }

  async function load_() {
    const root = $("#chat-root");
    if (!root) return;
    if (!root.dataset.ready) {
      shell(root);
      // relabel() во время ответа пересоздаёт кнопку с текстом «Отправить»,
      // хотя запрос ещё идёт: возвращаем ей состояние «Стоп».
      setBusy(S.busy);
    }
    try {
      const data = await api("/api/admin/models");
      S.models = (data.models || []).filter((m) => m.enabled !== false
        && !(m.caps?.music && m.caps?.text !== true) && !(m.capabilities?.music && m.capabilities?.text !== true))
        .map((m) => ({ id: m.id, provider: m.provider || "" }))
        .sort((a, b) => a.id.localeCompare(b.id));
    } catch (error) {
      S.models = [];
    }
    $("#pc-models").innerHTML = S.models.map((m) => `<option value="${esc(m.id)}">${esc(m.provider)}</option>`).join("");
    const field = $("#pc-model");
    if (!field.value) field.value = S.prefs.model && S.models.some((m) => m.id === S.prefs.model) ? S.prefs.model : (S.models[0] || {}).id || "";
    render();
    if (!S.busy) setTimeout(() => $("#pc-input")?.focus(), 30);
  }

  /* ---------------- отрисовка ---------------- */

  function render() {
    const log = $("#pc-log");
    if (!log) return;
    if (!S.history.length) {
      log.innerHTML = `<div class="pc-empty"><div class="pc-empty-mark">✦</div>
        <b>${esc(L("Проверьте любую модель Proxy", "Try any Proxy model"))}</b>
        <p>${esc(L("Выберите модель сверху и напишите сообщение. Ответ придёт тем же путём, что и у сторонних программ, — без скрытых системных промптов.",
          "Pick a model above and write a message. The answer takes the same path as third-party apps — without hidden system prompts."))}</p>
        <div class="comp-suggest">${[L("Привет! Кто ты и какая у тебя дата знаний?", "Hi! Who are you and what is your knowledge cutoff?"),
          L("Реши: 17 × 23 − 45", "Solve: 17 × 23 − 45"), L("Напиши хайку про прокси-сервер", "Write a haiku about a proxy server")]
          .map((s) => `<button type="button" class="chip-btn" data-pc-suggest>${esc(s)}</button>`).join("")}</div></div>`;
      return;
    }
    log.innerHTML = S.history.map(bubble).join("");
    log.scrollTop = log.scrollHeight;
  }

  function bubble(m, index) {
    if (m.role === "user") {
      const pictures = (m.images || []).map((src) => `<img src="${esc(src)}" alt="">`).join("");
      return `<div class="comp-msg user pc-msg" data-index="${index}">${pictures ? `<div class="pc-imgs">${pictures}</div>` : ""}
        ${m.hadImages ? `<div class="pc-note">${esc(L(`Изображений: ${m.hadImages} (не сохраняются)`, `Images: ${m.hadImages} (not kept)`))}</div>` : ""}
        <div class="comp-text">${esc(m.content).replace(/\n/g, "<br>")}</div></div>`;
    }
    const facts = [];
    if (m.model) facts.push(esc(m.model));
    if (m.ms) facts.push(`${(m.ms / 1000).toFixed(1)} ${esc(L("с", "s"))}`);
    if (m.ttft) facts.push(`${esc(L("первый токен", "first token"))} ${(m.ttft / 1000).toFixed(1)} ${esc(L("с", "s"))}`);
    if (m.usage && (m.usage.prompt_tokens || m.usage.completion_tokens)) {
      facts.push(`${esc(L("токены", "tokens"))} ${tokens(m.usage.prompt_tokens || 0)} → ${tokens(m.usage.completion_tokens || 0)}`);
      if (m.usage.completion_tokens_details?.reasoning_tokens != null) {
        facts.push(`${esc(L("размышления", "reasoning"))}: ${tokens(m.usage.completion_tokens_details.reasoning_tokens)}`);
      }
    }
    if (m.search) facts.push(esc(L("с веб-поиском", "with web search")));
    const typing = '<span class="comp-typing"><i></i><i></i><i></i></span>';
    return `<div class="comp-msg bot pc-msg ${m.error ? "err" : ""}" data-index="${index}">
      ${m.reasoning ? `<details class="comp-think" ${m.pending && !m.content ? "open" : ""}><summary>${esc(L("Размышления", "Reasoning"))}</summary><div>${esc(m.reasoning)}</div></details>` : ""}
      <div class="comp-text selectable">${m.error ? `<b>${esc(L("Модель не ответила", "The model did not answer"))}</b><p class="selectable">${esc(m.content)}</p>${m.hint ? `<p class="muted">${esc(m.hint)}</p>` : ""}` : md(m.content) || typing}</div>
      ${!m.pending ? `<div class="comp-foot pc-foot"><span>${facts.join(" · ")}</span>
        <span class="pc-foot-actions">
          ${!m.error ? `<button type="button" class="comp-copy-msg" data-pc="copy">${esc(L("Скопировать", "Copy"))}</button>` : ""}
          ${index === S.history.length - 1 ? `<button type="button" class="comp-copy-msg" data-pc="retry">${esc(L("Повторить", "Retry"))}</button>` : ""}
          ${m.error && window.MemuCompanion ? `<button type="button" class="comp-copy-msg" data-pc="ask">✦ ${esc(t("comp.ask"))}</button>` : ""}
        </span></div>` : ""}
    </div>`;
  }

  function updateLast() {
    const log = $("#pc-log");
    const last = log && log.lastElementChild;
    const index = S.history.length - 1;
    if (!last || !last.classList.contains("bot")) { render(); return; }
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 60;
    last.outerHTML = bubble(S.history[index], index);
    if (stick) log.scrollTop = log.scrollHeight;
  }

  function renderAttach() {
    const box = $("#pc-attach");
    box.hidden = !S.images.length;
    box.innerHTML = S.images.map((src, i) => `<span class="pc-thumb"><img src="${esc(src)}" alt="">
      <button type="button" class="icon-btn" data-pc="unattach" data-i="${i}" aria-label="${esc(L("Убрать", "Remove"))}">✕</button></span>`).join("");
  }

  function autosize() {
    const input = $("#pc-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(220, input.scrollHeight)}px`;
  }

  function setBusy(on) {
    S.busy = on;
    const button = $("#pc-send");
    if (!button) return;
    button.textContent = on ? L("Стоп", "Stop") : L("Отправить", "Send");
    button.classList.toggle("danger", on);
    button.classList.toggle("primary", !on);
  }

  /* ---------------- действия ---------------- */

  function addImages(files) {
    files.forEach((file) => {
      if (file.size > MAX_IMAGE) { toast(L(`${file.name}: больше 8 МБ`, `${file.name}: larger than 8 MB`), "warn"); return; }
      const reader = new FileReader();
      reader.onload = () => { S.images.push(String(reader.result)); renderAttach(); };
      reader.readAsDataURL(file);
    });
  }

  function onClick(event) {
    const suggestion = event.target.closest("[data-pc-suggest]");
    if (suggestion) { $("#pc-input").value = suggestion.textContent; return send(); }
    const code = event.target.closest("[data-copy-code]");
    if (code && code.closest("#pc-log")) return copyText(code.parentElement.querySelector("code").textContent);
    const button = event.target.closest("[data-pc]");
    if (!button) return;
    const action = button.dataset.pc;
    const row = S.history[Number(button.closest("[data-index]")?.dataset.index)];
    if (action === "copy" && row) return copyText(row.content);
    if (action === "unattach") { S.images.splice(Number(button.dataset.i), 1); return renderAttach(); }
    if (action === "retry") return retry();
    if (action === "ask" && row && window.MemuCompanion) {
      return MemuCompanion.ask("", { source: "chat", model: row.model, message: row.content });
    }
  }

  function retry() {
    if (S.busy) return;
    const last = S.history[S.history.length - 1];
    if (last && last.role === "assistant") S.history.pop();
    const user = S.history[S.history.length - 1];
    if (!user || user.role !== "user") return render();
    S.history.pop();
    send(user.content, user.images || []);
  }

  function stop() {
    if (S.controller) S.controller.abort();
  }

  function apiMessages() {
    const out = [];
    const system = $("#pc-system").value.trim();
    if (system) out.push({ role: "system", content: system });
    for (const m of S.history) {
      if (m.pending || (m.role === "assistant" && m.error)) continue;
      if (m.role === "user" && m.images && m.images.length) {
        out.push({ role: "user", content: [{ type: "text", text: m.content || "" },
          ...m.images.map((url) => ({ type: "image_url", image_url: { url } }))] });
      } else {
        out.push({ role: m.role, content: m.content || "" });
      }
    }
    return out;
  }

  async function send(textArg, imagesArg) {
    if (S.busy) return;
    const input = $("#pc-input");
    const text = String(textArg ?? input.value).trim();
    const images = imagesArg || S.images.slice();
    const model = $("#pc-model").value.trim();
    if (!text && !images.length) return;
    if (!model) { toast(L("Выберите модель", "Pick a model"), "warn"); $("#pc-model").focus(); return; }
    S.history.push({ role: "user", content: text, images: images.length ? images : undefined });
    const reply = { role: "assistant", content: "", reasoning: "", model, pending: true };
    S.history.push(reply);
    input.value = "";
    S.images = [];
    renderAttach();
    autosize();
    setBusy(true);
    render();
    const body = { model, messages: apiMessages(), web_search: $("#pc-search").checked, thinking: $("#pc-think").checked,
      tools_available: true, prompt_caching: false };
    const temperature = parseFloat($("#pc-temp").value.replace(",", "."));
    if (Number.isFinite(temperature)) body.temperature = Math.max(0, Math.min(2, temperature));
    const maxTokens = parseInt($("#pc-max").value, 10);
    if (Number.isFinite(maxTokens) && maxTokens > 0) body.max_tokens = maxTokens;
    const controller = new AbortController();
    S.controller = controller;
    const started = performance.now();
    let frame = 0;
    const paint = () => { frame = 0; updateLast(); };
    try {
      const resp = await fetch("/api/admin/playground/chat", {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: "Bearer " + TOKEN } : {}) },
        body: JSON.stringify(body),
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
      let finished = false;
      while (!finished) {
        const { value, done } = await reader.read();
        if (done) break;
        // SSE допускает CRLF: без нормализации кадры «\r\n\r\n» не делились бы
        // по «\n\n», и ответ появлялся бы только целиком (или не появлялся).
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") { finished = true; break; }
          let event;
          try { event = JSON.parse(payload); } catch (error) { continue; }
          if (event.error) {
            reply.error = true;
            reply.content = LANG === "en" ? event.error.message_en || event.error.message : event.error.message;
            reply.hint = LANG === "en" ? event.error.hint_en || event.error.hint || "" : event.error.hint || "";
            continue;
          }
          if (event.object === "memuai.event") { if (event.web_search) reply.search = true; continue; }
          if (event.usage) reply.usage = event.usage;
          if (event.model && !reply.served) reply.served = event.model;
          const delta = ((event.choices || [])[0] || {}).delta || {};
          if (delta.reasoning_content) reply.reasoning += delta.reasoning_content;
          if (delta.content) {
            if (!reply.ttft) reply.ttft = Math.round(performance.now() - started);
            reply.content += delta.content;
          }
          if (!frame) frame = requestAnimationFrame(paint);
        }
      }
      if (!reply.content && !reply.error) {
        reply.error = true;
        reply.content = L("Модель вернула пустой ответ.", "The model returned an empty answer.");
      }
    } catch (error) {
      if (error && error.name === "AbortError") {
        if (!reply.content) { reply.error = true; reply.content = L("Остановлено.", "Stopped."); }
      } else {
        reply.error = true;
        reply.content = errorOf(error);
      }
    } finally {
      if (frame) cancelAnimationFrame(frame);
      reply.pending = false;
      reply.ms = Math.round(performance.now() - started);
      S.controller = null;
      setBusy(false);
      save();
      render();
      $("#pc-input")?.focus();
    }
  }

  return { load: load_, relabel() { const root = $("#chat-root"); if (root) { root.dataset.ready = ""; root.innerHTML = ""; load_(); } } };
})();
