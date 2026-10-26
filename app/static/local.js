

"use strict";

window.MemuLocal = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = {
    status: null, timer: null, query: "", sort: "downloads", results: null, searching: false,
    open: "", files: {}, servers: null, scanning: false, busy: new Set(),
  };

  function bytes(value) {
    const n = Number(value) || 0;
    if (!n) return "—";
    const units = LANG === "en" ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${units[i]}`;
  }
  function count(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }
  function eta(seconds) {
    if (!seconds) return "";
    if (seconds < 60) return `${seconds} ${L("с", "s")}`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} ${L("мин", "min")}`;
    return `${(seconds / 3600).toFixed(1)} ${L("ч", "h")}`;
  }
  const FIT = {
    gpu: ["ok", () => L("Влезет в видеопамять", "Fits in VRAM")],
    partial: ["info", () => L("Видеокарта + ОЗУ", "GPU + RAM")],
    ram: ["info", () => L("Пойдёт в ОЗУ", "Runs from RAM")],
    tight: ["warn", () => L("Впритык", "Tight fit")],
    too_big: ["invalid", () => L("Слишком большая", "Too big")],
    unknown: ["unknown", () => L("Не оценить", "Unknown")],
  };
  function fitBadge(fit) {
    const [cls, label] = FIT[(fit && fit.level) || "unknown"] || FIT.unknown;
    const need = fit && fit.need ? ` · ~${bytes(fit.need)}` : "";
    return `<span class="badge ${cls}" title="${esc(L("Оценка: веса + контекст 8K", "Estimate: weights + 8K context"))}">${esc(label())}${esc(need)}</span>`;
  }
  const STATE = {
    ready: ["ok", () => L("Загружена", "Loaded")],
    starting: ["info", () => L("Запускается…", "Starting…")],
    stopped: ["disabled", () => L("Не загружена", "Not loaded")],
    error: ["invalid", () => L("Ошибка запуска", "Failed to start")],
  };
  function stateBadge(model) {
    if (!model.exists) return `<span class="badge invalid">${esc(L("Файл не найден", "File missing"))}</span>`;
    const [cls, label] = STATE[model.state] || STATE.stopped;
    return `<span class="badge ${cls}">${esc(label())}</span>`;
  }
  function errorOf(error) { return error && error.message ? error.message : String(error || ""); }

  function active() { return $("#tab-local")?.classList.contains("active") && !document.hidden; }

  async function load() {
    const root = $("#local-root");
    if (!root) return;
    if (!root.dataset.ready) shell(root);
    await refresh();
  }

  async function refresh() {
    clearTimeout(S.timer);
    try {
      S.status = await api("/api/admin/local/status");
      renderDynamic();
    } catch (error) {
      $("#lm-engine").innerHTML = `<div class="warn-strip">${esc(errorOf(error))}</div>`;
    }
    schedule();
  }

  function schedule() {
    clearTimeout(S.timer);
    if (!active()) return;
    const st = S.status || {};
    const busy = (st.downloads || []).some((d) => d.state === "downloading" || d.state === "queued")
      || (st.runtime && st.runtime.install && st.runtime.install.state === "running")
      || (st.models || []).some((m) => m.state === "starting");
    S.timer = setTimeout(() => { if (active()) refresh(); }, busy ? 1200 : 6000);
  }
  document.addEventListener("visibilitychange", () => { if (active()) refresh(); });

  function shell(root) {
    root.dataset.ready = "1";
    root.innerHTML = `
      <div class="lm-grid">
        <div class="card lm-wizard-card">
          <span class="lw-mark" aria-hidden="true">${window.MemuIcons ? MemuIcons.svg("sparkles") : ""}</span>
          <div class="grow"><h3>${esc(L("Не знаете, какую модель выбрать?", "Not sure which model to pick?"))}</h3>
            <p>${esc(L("Мастер посмотрит на видеокарту, память и процессор, спросит о задачах и подберёт лучшие модели с квантованием, скоростью и установкой в один клик.",
              "The wizard looks at your GPU, memory and CPU, asks about your tasks and picks the best models with quantisation, speed and one-click setup."))}</p></div>
          <button class="btn primary" id="lm-wizard">✦ ${esc(L("Подобрать модель", "Find a model"))}</button>
        </div>
        <div class="card lm-engine" id="lm-engine"></div>
        <div id="lm-downloads"></div>
        <div class="card">
          <div class="row lm-head">
            <h3 class="grow">${esc(L("Мои модели", "My models"))}</h3>
            <button class="btn small" id="lm-add">${esc(L("＋ Файл с диска", "＋ File from disk"))}</button>
            <button class="btn small" id="lm-settings">${esc(L("Настройки", "Settings"))}</button>
          </div>
          <p class="sub">${esc(L("Каждая модель появляется в каталоге Proxy как local-<имя> и работает через тот же API, что и облачные.",
            "Each model appears in the Proxy catalogue as local-<name> and works through the same API as cloud models."))}</p>
          <div id="lm-models"></div>
        </div>
        <div class="card">
          <h3>${esc(L("Поиск на Hugging Face", "Search Hugging Face"))}</h3>
          <p class="sub">${esc(L("Только репозитории с файлами GGUF. Рядом с каждым файлом — оценка, поместится ли он на этот компьютер.",
            "Only repositories with GGUF files. Each file shows whether it fits on this computer."))}</p>
          <form class="row lm-search" id="lm-search">
            <input class="grow" id="lm-q" type="search" autocomplete="off" spellcheck="false"
              placeholder="${esc(L("Например: qwen3.8 27b, gemma 4 12b, gpt-oss 20b…", "For example: qwen3.8 27b, gemma 4 12b, gpt-oss 20b…"))}">
            <select id="lm-sort" aria-label="${esc(L("Сортировка", "Sort"))}">
              <option value="downloads">${esc(L("Популярные", "Most downloaded"))}</option>
              <option value="trendingScore">${esc(L("В тренде", "Trending"))}</option>
              <option value="likes">${esc(L("Больше лайков", "Most liked"))}</option>
              <option value="lastModified">${esc(L("Свежие", "Recently updated"))}</option>
            </select>
            <button class="btn primary" type="submit">${esc(L("Найти", "Search"))}</button>
          </form>
          <div id="lm-results"></div>
        </div>
        <div class="card">
          <div class="row lm-head">
            <h3 class="grow">${esc(L("Другие локальные серверы", "Other local servers"))}</h3>
            <button class="btn small" id="lm-scan">${esc(L("Найти на этом компьютере", "Scan this computer"))}</button>
          </div>
          <p class="sub">${esc(L("Ollama, LM Studio, llamafile, Jan, KoboldCpp, text-generation-webui и vLLM уже умеют отдавать OpenAI-совместимый API — их можно подключить как пользовательский провайдер.",
            "Ollama, LM Studio, llamafile, Jan, KoboldCpp, text-generation-webui and vLLM already serve an OpenAI-compatible API — connect them as a custom provider."))}</p>
          <div id="lm-servers"></div>
        </div>
      </div>`;
    $("#lm-sort").value = S.sort;
    $("#lm-q").value = S.query;
    $("#lm-search").onsubmit = (event) => { event.preventDefault(); search(); };
    $("#lm-sort").onchange = () => { S.sort = $("#lm-sort").value; if (S.results) search(); };
    $("#lm-add").onclick = addFile;
    $("#lm-settings").onclick = openSettings;
    $("#lm-scan").onclick = scan;
    $("#lm-wizard").onclick = () => window.MemuLocalWizard && MemuLocalWizard.open();
    root.addEventListener("click", onClick);
    renderResults();
    renderServers();
  }

  function renderDynamic() {
    renderEngine();
    renderDownloads();
    renderModels();

    const key = (S.status.downloads || []).filter((d) => d.state === "downloading" || d.state === "queued").map((d) => d.id).join(",");
    if (key !== S.dlKey) { S.dlKey = key; if (S.results && S.open) renderResults(); }
  }

  function renderEngine() {
    const st = S.status;
    const rt = st.runtime || {};
    const hw = st.hardware || {};
    const install = rt.install || {};
    const gpus = (hw.gpus || []).map((g) => `${esc(g.name)}${g.integrated ? ` <span class="muted">(${esc(L("встроенная", "integrated"))})</span>`
      : g.vram ? ` · ${bytes(g.vram)}` : ""}`).join("<br>") || esc(L("не найдена", "none found"));
    let body;
    if (install.state === "running") {
      const pct = install.total ? Math.round((install.done / install.total) * 100) : 0;
      body = `<div class="lm-progress"><div class="row"><b class="grow">${esc(L("Устанавливаю llama.cpp", "Installing llama.cpp"))} (${esc(install.backend || "")})</b>
        <span class="muted">${install.total ? `${bytes(install.done)} / ${bytes(install.total)}` : esc(L("готовлю…", "preparing…"))}</span></div>
        <div class="bar"><i style="width:${pct}%"></i></div></div>`;
    } else if (rt.installed) {
      body = `<div class="row"><span class="badge ok">${esc(L("Движок готов", "Engine ready"))}</span>
        <span class="chip">${esc(rt.custom ? L("свой llama-server", "own llama-server") : `llama.cpp ${rt.tag || ""} · ${rt.backend || ""}`)}</span>
        <div class="grow"></div>
        ${rt.custom ? "" : `<button class="btn small" data-lm="reinstall">${esc(L("Обновить", "Update"))}</button>
        <button class="btn small danger" data-lm="rt-remove">${esc(L("Удалить движок", "Remove engine"))}</button>`}</div>
        <div class="lm-path mono selectable">${esc(rt.path)}</div>`;
    } else {
      const choose = `<select id="lm-backend" aria-label="${esc(L("Сборка", "Build"))}">
        <option value="auto">${esc(L("Автовыбор по видеокарте", "Pick by graphics card"))}</option>
        <option value="cuda">CUDA (NVIDIA)</option><option value="vulkan">Vulkan (AMD, Intel, NVIDIA)</option>
        <option value="cpu">${esc(L("Только процессор", "CPU only"))}</option></select>`;
      body = `<p class="lm-lead">${esc(L("Модели запускает llama.cpp — открытый движок. Он скачивается один раз (≈ 30–400 МБ в зависимости от сборки) с официальной страницы релизов на GitHub.",
          "Models run on llama.cpp, an open engine. It is downloaded once (≈ 30–400 MB depending on the build) from the official GitHub releases page."))}</p>
        ${install.state === "error" ? `<div class="warn-strip">${esc(L(install.error, install.error_en || install.error))}</div>` : ""}
        <div class="row">${choose}<button class="btn primary" data-lm="install">${esc(L("Установить движок", "Install the engine"))}</button>
        <button class="btn" data-lm="own-server">${esc(L("У меня уже есть llama-server", "I already have llama-server"))}</button></div>`;
    }
    $("#lm-engine").innerHTML = `
      <div class="lm-engine-main"><h3>${esc(L("Движок", "Engine"))}</h3>${body}</div>
      <dl class="lm-hw">
        <dt>${esc(L("Память", "Memory"))}</dt><dd>${bytes(hw.ram)}</dd>
        <dt>${esc(L("Видеокарта", "GPU"))}</dt><dd>${gpus}</dd>
        <dt>${esc(L("Процессор", "CPU"))}</dt><dd>${hw.cpu_cores ? `${esc(hw.cpu_cores)} ${esc(L("ядер", "cores"))} · ` : ""}${esc(hw.cpu_threads || "—")} ${esc(L("потоков", "threads"))}${hw.avx2 === false ? ` · <span class="muted">${esc(L("без AVX2", "no AVX2"))}</span>` : ""}</dd>
      </dl>`;
  }

  function renderDownloads() {
    const rows = (S.status.downloads || []).filter((d) => d.state !== "cancelled");
    const box = $("#lm-downloads");
    if (!rows.length) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="card"><h3>${esc(L("Загрузки", "Downloads"))}</h3>${rows.map((d) => {
      const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
      const running = d.state === "downloading" || d.state === "queued";
      const info = d.state === "done" ? L("Готово — модель добавлена", "Done — the model was added")
        : d.state === "error" ? L(d.error, d.error_en || d.error)
        : `${bytes(d.done)} / ${bytes(d.total)} · ${bytes(d.speed)}/${L("с", "s")}${d.eta ? ` · ${L("осталось", "left")} ${eta(d.eta)}` : ""}`;
      return `<div class="lm-dl ${esc(d.state)}">
        <div class="row"><b class="grow mono">${esc(d.name)}</b><span class="muted">${esc(d.repo)}</span>
          ${running ? `<button class="btn small" data-lm="dl-cancel" data-id="${esc(d.id)}">${esc(L("Отменить", "Cancel"))}</button>`
            : `${d.state === "error" ? `<button class="btn small" data-lm="dl-retry" data-id="${esc(d.id)}" title="${esc(L("Скачанная часть сохранена — загрузка продолжится с места обрыва", "The downloaded part is kept — the download resumes where it stopped"))}">${esc(L("Повторить", "Retry"))}</button>` : ""}<button class="icon-btn" data-lm="dl-forget" data-id="${esc(d.id)}" aria-label="${esc(L("Убрать из списка", "Dismiss"))}">✕</button>`}</div>
        <div class="bar"><i style="width:${d.state === "done" ? 100 : pct}%"></i></div>
        <div class="lm-dl-info ${d.state === "error" ? "err" : ""}">${esc(info)}</div></div>`;
    }).join("")}</div>`;
  }

  function renderModels() {
    const models = S.status.models || [];
    const box = $("#lm-models");
    if (!models.length) {
      box.innerHTML = `<div class="empty">${esc(L("Пока нет ни одной модели. Нажмите «Подобрать модель» выше, найдите её на Hugging Face ниже или добавьте готовый файл .gguf.",
        "No models yet. Press “Find a model” above, search Hugging Face below or add a .gguf file you already have."))}</div>`;
      return;
    }
    const ready = S.status.runtime && S.status.runtime.installed;
    box.innerHTML = models.map((m) => {
      const meta = m.meta || {};
      const s = m.settings || {};
      const busy = S.busy.has(m.id);
      const loaded = m.state === "ready" || m.state === "starting";
      const facts = [meta.architecture, meta.quant, bytes(m.size), `${L("контекст", "context")} ${count(s.context)}`,
        meta.context_length ? `${L("макс.", "max")} ${count(meta.context_length)}` : "", s.mmproj ? L("видит изображения", "vision") : "",
        s.draft ? L("ускорение draft", "draft speed-up") : ""]
        .filter(Boolean).map((x) => `<span>${esc(x)}</span>`).join("");
      const draftWarning = m.draft_head ? `<div class="warn-strip lm-draft-warn">${esc(L(
        "Это не модель, а draft-файл (MTP-голова) для ускорения полной модели. Отдельно он не запускается — llama.cpp аварийно завершается. Скачайте основной файл из того же репозитория (без «draft» в имени), а этот укажите в его настройках как «Draft-модель для ускорения».",
        "This is not a model but a draft file (an MTP head) that speeds up the full model. It cannot run on its own — llama.cpp crashes. Download the main file from the same repository (without “draft” in the name) and set this one as its “Draft model for speed-up”."))}
        ${m.repo ? `<button class="btn small" data-lm="find-main" data-repo="${esc(m.repo)}">${esc(L("Открыть файлы репозитория", "Open the repository files"))}</button>` : ""}</div>` : "";
      return `<div class="lm-model ${s.enabled ? "" : "off"}" data-slug="${esc(m.id)}">
        <div class="lm-model-main">
          <div class="row"><b class="lm-model-name">${esc(s.name || m.file)}</b>${stateBadge(m)}${fitBadge(m.fit)}
            ${s.enabled ? "" : `<span class="badge disabled">${esc(L("Выключена", "Disabled"))}</span>`}</div>
          <div class="lm-facts">${facts}</div>
          <div class="lm-id row"><code class="mono selectable">${esc(m.public_id)}</code>
            <button class="icon-btn" data-lm="copy-id" aria-label="${esc(L("Скопировать ID модели", "Copy the model ID"))}" title="${esc(L("Скопировать ID модели", "Copy the model ID"))}">⧉</button></div>
          ${draftWarning}
          ${m.error && !m.draft_head ? `<div class="lm-dl-info err selectable">${esc(L(m.error, m.error_en || m.error))}</div>` : ""}
        </div>
        <div class="lm-actions">
          ${loaded ? `<button class="btn small" data-lm="unload" ${busy ? "disabled" : ""}>${esc(L("Выгрузить", "Unload"))}</button>`
            : `<button class="btn small primary" data-lm="load" ${busy || !ready || !m.exists ? "disabled" : ""}
                title="${esc(ready ? L("Загрузить в память заранее — первый запрос не будет ждать", "Load into memory now so the first request does not wait") : L("Сначала установите движок", "Install the engine first"))}">${busy ? "…" : esc(L("Загрузить", "Load"))}</button>`}
          <button class="btn small" data-lm="test" ${!ready || !m.exists ? "disabled" : ""}>${esc(L("Проверить", "Try it"))}</button>
          <button class="btn small" data-lm="edit">${esc(L("Настроить", "Configure"))}</button>
          <button class="icon-btn" data-lm="more" aria-label="${esc(L("Ещё", "More"))}" title="${esc(L("Ещё", "More"))}">⋯</button>
        </div></div>`;
    }).join("");
  }

  const findModel = (slug) => (S.status.models || []).find((m) => m.id === slug);

  async function onClick(event) {
    const button = event.target.closest("[data-lm]");
    if (!button) return;
    const action = button.dataset.lm;
    const slug = button.closest("[data-slug]")?.dataset.slug || "";
    const model = slug ? findModel(slug) : null;
    try {
      switch (action) {
        case "install": {
          await api("/api/admin/local/runtime/install", { method: "POST", body: { backend: $("#lm-backend")?.value || "auto" } });
          return refresh();
        }
        case "reinstall": return openReinstall();
        case "rt-remove":
          return confirmDialog(L("Удалить движок?", "Remove the engine?"),
            L("Файлы llama.cpp будут удалены. Модели останутся — движок можно поставить заново.", "The llama.cpp files are removed. Your models stay — the engine can be installed again."),
            async () => { await api("/api/admin/local/runtime/remove", { method: "POST" }); refresh(); });
        case "own-server": return openSettings("server_path");
        case "dl-cancel":
          await api("/api/admin/local/download/cancel", { method: "POST", body: { id: button.dataset.id } });
          return refresh();
        case "dl-retry": {

          const job = (S.status.downloads || []).find((d) => d.id === button.dataset.id);
          if (!job) return refresh();
          try {
            await api("/api/admin/local/download", { method: "POST", body: { repo: job.repo, files: job.files,
              projector: job.projector || "", draft: job.draft || "", preset: job.preset || null } });
            await api("/api/admin/local/download/cancel", { method: "POST", body: { id: job.id, forget: true } });
          } catch (error) { toast(errorOf(error), "err"); }
          return refresh();
        }
        case "dl-forget":
          await api("/api/admin/local/download/cancel", { method: "POST", body: { id: button.dataset.id, forget: true } });
          return refresh();
        case "copy-id": return copyText(model.public_id);
        case "load": {
          S.busy.add(slug); renderModels();
          const pending = api(`/api/admin/local/models/${encodeURIComponent(slug)}/load`, { method: "POST" });
          setTimeout(refresh, 400);
          try { await pending; toast(L("Модель загружена", "Model loaded")); }
          finally { S.busy.delete(slug); refresh(); }
          return;
        }
        case "unload":
          await api(`/api/admin/local/models/${encodeURIComponent(slug)}/unload`, { method: "POST" });
          return refresh();
        case "test": return openTest(model);
        case "edit": return openModel(model);
        case "more": return moreMenu(button, model);
        case "hf-open": return toggleRepo(button.dataset.repo);
        case "find-main": {

          const repo = button.dataset.repo;
          $("#lm-q").value = repo;
          await search();
          if (Array.isArray(S.results) && !S.results.some((r) => r.repo === repo)) {
            S.results.unshift({ repo, author: repo.split("/")[0], downloads: 0, likes: 0 });
          }
          if (S.open !== repo) await toggleRepo(repo);
          $("#lm-results")?.scrollIntoView({ block: "start", behavior: "smooth" });
          return;
        }
        case "hf-get": return download(button.dataset.repo, button.dataset.file);
        case "srv-add": return connectServer(button.dataset.id);
        default: return;
      }
    } catch (error) {
      toast(errorOf(error), "err");
      refresh();
    }
  }

  function moreMenu(anchor, model) {
    document.querySelector(".lm-menu")?.remove();
    const menu = document.createElement("div");
    menu.className = "ctx-menu lm-menu";
    menu.setAttribute("role", "menu");
    const items = [
      ["log", L("Журнал сервера", "Server log")],
      ["toggle", model.settings.enabled ? L("Выключить", "Disable") : L("Включить", "Enable")],
      ["path", L("Скопировать путь к файлу", "Copy the file path")],
      model.repo ? ["hf", L("Открыть на Hugging Face", "Open on Hugging Face")] : null,
      ["detect", L("Проверить в детекторе", "Check in the detector")],
      ["sep"],
      ["delete", L("Удалить…", "Delete…"), "danger"],
    ].filter(Boolean);
    menu.innerHTML = items.map(([id, label, cls]) => id === "sep" ? `<div class="ctx-sep"></div>`
      : `<button type="button" role="menuitem" class="${cls || ""}" data-act="${id}">${esc(label)}</button>`).join("");
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 8, rect.bottom + 4)}px`;
    menu.style.left = `${Math.max(8, rect.right - menu.offsetWidth)}px`;
    menu.querySelector("button")?.focus();
    const close = () => { menu.remove(); document.removeEventListener("mousedown", outside, true); document.removeEventListener("keydown", key, true); };
    const outside = (e) => { if (!menu.contains(e.target)) close(); };
    const key = (e) => { if (e.key === "Escape") { e.preventDefault(); close(); anchor.focus(); } };
    document.addEventListener("mousedown", outside, true);
    document.addEventListener("keydown", key, true);
    menu.onclick = async (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (!act) return;
      close();
      try {
        if (act === "log") return openLog(model);
        if (act === "toggle") {
          await api(`/api/admin/local/models/${encodeURIComponent(model.id)}`, { method: "POST", body: { enabled: !model.settings.enabled } });
          return refresh();
        }
        if (act === "path") return copyText(model.path);
        if (act === "hf") return window.open(`https://huggingface.co/${model.repo}`, "_blank", "noopener");
        if (act === "detect") {
          $('.nav-item[data-tab="detector"]')?.click();
          return window.MemuDetector && MemuDetector.pick(model.public_id);
        }
        if (act === "delete") return confirmDelete(model);
      } catch (error) { toast(errorOf(error), "err"); }
    };
  }

  function confirmDelete(model) {
    const own = model.copied || model.path.includes("local-models");
    openModal(`<h3>${esc(L("Удалить модель?", "Delete the model?"))}</h3>
      <p class="muted">${esc(model.settings.name || model.file)}</p>
      ${own ? `<label class="lm-check"><input type="checkbox" id="lm-del-file" checked> ${esc(L("Удалить и файл с диска", "Also delete the file from disk"))} (${bytes(model.size)})</label>`
        : `<p class="field-hint">${esc(L("Файл лежит в вашей папке и останется на месте — из Proxy пропадёт только запись.", "The file is in your own folder and stays there — only the entry is removed from the Proxy."))}</p>`}
      <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button><button class="btn danger" id="m-yes">${t("acc.btn.del")}</button></div>`);
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      const deleteFile = Boolean($("#lm-del-file")?.checked);
      closeModal();
      try {
        await api(`/api/admin/local/models/${encodeURIComponent(model.id)}/delete`, { method: "POST", body: { delete_file: deleteFile } });
        toast(L("Модель удалена", "Model deleted"));
      } catch (error) { toast(errorOf(error), "err"); }
      refresh();
    };
  }

  const FIELDS = [
    ["context", () => L("Длина контекста (токенов)", "Context length (tokens)"), "number", () => L("Больше — дольше помнит разговор, но нужно больше памяти.", "Longer remembers more of the chat but needs more memory.")],
    ["gpu_layers", () => L("Слоёв на видеокарте", "Layers on the GPU"), "number", () => L("-1 — все слои (быстрее всего), 0 — только процессор.", "-1 — all layers (fastest), 0 — CPU only.")],
    ["threads", () => L("Потоков процессора", "CPU threads"), "number", () => L("0 — решает llama.cpp.", "0 — llama.cpp decides.")],
    ["batch", () => L("Размер пакета", "Batch size"), "number", () => L("0 — по умолчанию.", "0 — default.")],
    ["idle_minutes", () => L("Выгружать после простоя, мин", "Unload after idle, min"), "number", () => L("0 — держать в памяти, пока работает Proxy.", "0 — keep loaded while the Proxy runs.")],
    ["max_tokens", () => L("Лимит ответа (токенов)", "Answer limit (tokens)"), "number", () => L("Пусто — без ограничения, если клиент не задал своё.", "Empty — no limit unless the client sets one.")],
    ["temperature", () => L("Температура", "Temperature"), "decimal", null],
    ["top_p", () => "Top P", "decimal", null],
    ["top_k", () => "Top K", "number", null],
    ["min_p", () => "Min P", "decimal", null],
    ["repeat_penalty", () => L("Штраф за повторы", "Repeat penalty"), "decimal", null],
  ];

  function openModel(model) {
    const s = model.settings;
    const meta = model.meta || {};
    const field = ([key, label, kind, hint]) => `<div class="field"><label>${esc(label())}</label>
      <input data-key="${key}" type="text" inputmode="${kind === "decimal" ? "decimal" : "numeric"}" value="${s[key] === null || s[key] === undefined ? "" : esc(s[key])}"
        placeholder="${esc(L("по умолчанию", "default"))}">${hint ? `<div class="field-hint">${esc(hint())}</div>` : ""}</div>`;
    openModal(`<h3>${esc(s.name || model.file)}</h3>
      <p class="muted mono lm-path selectable">${esc(model.path)}</p>
      <div class="grid-2">
        <div class="field"><label>${esc(L("Название в списках", "Display name"))}</label><input data-key="name" value="${esc(s.name)}"></div>
        <div class="field"><label>${esc(L("Доступна в API", "Available in the API"))}</label>
          <label class="switch"><input type="checkbox" data-key="enabled" ${s.enabled ? "checked" : ""}><span class="slider"></span></label></div>
        ${FIELDS.slice(0, 6).map(field).join("")}
      </div>
      <details class="lm-adv"><summary>${esc(L("Параметры генерации по умолчанию", "Default sampling parameters"))}</summary>
        <p class="field-hint">${esc(L("Применяются, только если клиент не прислал своё значение. Пусто — значение из модели.", "Applied only when the client sends none. Empty — the model's own value."))}</p>
        <div class="grid-3">${FIELDS.slice(6).map(field).join("")}</div></details>
      <div class="field"><label>${esc(L("Системный промпт по умолчанию", "Default system prompt"))}</label>
        <textarea data-key="system_prompt" rows="3" placeholder="${esc(L("Добавляется, если в запросе нет своего системного сообщения", "Added when the request has no system message of its own"))}">${esc(s.system_prompt)}</textarea></div>
      <details class="lm-adv"><summary>${esc(L("Для опытных", "Advanced"))}</summary>
        <div class="field"><label>${esc(L("Проектор изображений (mmproj .gguf)", "Image projector (mmproj .gguf)"))}</label>
          <input data-key="mmproj" class="mono" value="${esc(s.mmproj)}" placeholder="C:\\models\\mmproj-f16.gguf">
          <div class="field-hint">${esc(L("Нужен, чтобы модель видела картинки. Без него запросы с изображениями честно отклоняются.", "Needed for the model to see images. Without it, requests with images are refused with a clear error."))}</div></div>
        <div class="field"><label>${esc(L("Draft-модель для ускорения (.gguf)", "Draft model for speed-up (.gguf)"))}</label>
          <input data-key="draft" class="mono" value="${esc(s.draft || "")}" placeholder="C:\\models\\model-draft-Q8_0.gguf">
          <div class="field-hint">${esc(L("Спекулятивное декодирование: маленькая draft/MTP-модель предлагает токены, основная их проверяет. Качество ответа не меняется, скорость обычно растёт.", "Speculative decoding: a small draft/MTP model proposes tokens and the main model checks them. Answer quality stays the same; speed usually improves."))}</div></div>
        <div class="field"><label>${esc(L("Дополнительные аргументы llama-server", "Extra llama-server arguments"))}</label>
          <input data-key="extra_args" class="mono" value="${esc(s.extra_args)}" placeholder="--flash-attn on --cache-type-k q8_0"></div>
        <dl class="lm-meta">${Object.entries({ [L("Архитектура", "Architecture")]: meta.architecture, [L("Квантование", "Quantisation")]: meta.quant,
          [L("Слоёв", "Layers")]: meta.block_count, [L("Макс. контекст", "Max context")]: meta.context_length,
          [L("Шаблон чата", "Chat template")]: meta.chat_template ? L("есть", "present") : L("нет — будет общий", "none — a generic one is used"),
          [L("Источник", "Source")]: model.repo || L("файл с диска", "file from disk") })
          .filter(([, v]) => v !== undefined && v !== "" && v !== null).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      </details>
      <p class="field-hint">${esc(L("Изменения контекста, слоёв и аргументов применятся при следующей загрузке модели.", "Context, layer and argument changes apply the next time the model loads."))}</p>
      <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button><button class="btn primary" id="m-yes">${esc(L("Сохранить", "Save"))}</button></div>`, true);
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      const values = {};
      $$("#modal-box [data-key]").forEach((el) => { values[el.dataset.key] = el.type === "checkbox" ? el.checked : el.value.trim(); });
      try {
        await api(`/api/admin/local/models/${encodeURIComponent(model.id)}`, { method: "POST", body: values });
        closeModal();
        toast(L("Сохранено", "Saved"));
        refresh();
      } catch (error) { toast(errorOf(error), "err"); }
    };
  }

  function openTest(model) {
    openModal(`<h3>${esc(L("Проверка модели", "Try the model"))} · ${esc(model.settings.name || model.file)}</h3>
      <div class="field"><label>${esc(L("Сообщение", "Message"))}</label>
        <textarea id="lm-test-q" rows="2">${esc(L("Привет! Представься одним предложением.", "Hi! Introduce yourself in one sentence."))}</textarea></div>
      <div class="lm-test-out selectable" id="lm-test-out" aria-live="polite"></div>
      <div class="row-end"><button class="btn" id="m-no">${esc(L("Закрыть", "Close"))}</button><button class="btn primary" id="m-yes">${esc(L("Отправить", "Send"))}</button></div>`, true);
    const generation = modalGeneration;
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      const out = $("#lm-test-out");
      $("#m-yes").disabled = true;
      out.className = "lm-test-out selectable";
      out.textContent = model.state === "ready" ? L("Жду ответ…", "Waiting for the answer…") : L("Загружаю модель в память — это может занять до минуты…", "Loading the model into memory — this can take up to a minute…");
      setTimeout(refresh, 500);
      try {
        const r = await api(`/api/admin/local/models/${encodeURIComponent(model.id)}/test`, { method: "POST", body: { prompt: $("#lm-test-q").value } });
        if (generation !== modalGeneration) return;
        out.innerHTML = `${r.reasoning ? `<details class="lm-think"><summary>${esc(L("Размышления", "Reasoning"))}</summary>${esc(r.reasoning)}</details>` : ""}<div>${esc(r.text || L("(пустой ответ)", "(empty answer)"))}</div>
          <div class="lm-dl-info">${esc(L("первый токен", "first token"))} ${esc(r.ttft_ms)} ${esc(L("мс", "ms"))} · ${esc(L("всего", "total"))} ${esc(r.ms)} ${esc(L("мс", "ms"))}</div>`;
      } catch (error) {
        if (generation !== modalGeneration) return;
        out.className = "lm-test-out selectable err";
        out.textContent = errorOf(error);
      } finally {
        if (generation === modalGeneration) $("#m-yes").disabled = false;
        refresh();
      }
    };
  }

  async function openLog(model) {
    openModal(`<h3>${esc(L("Журнал llama-server", "llama-server log"))} · ${esc(model.settings.name || model.file)}</h3>
      <pre class="lm-log selectable" id="lm-log">…</pre>
      <div class="row-end"><button class="btn" id="lm-log-copy">${esc(L("Скопировать", "Copy"))}</button><button class="btn" id="m-no">${esc(L("Закрыть", "Close"))}</button></div>`, true);
    $("#m-no").onclick = closeModal;
    const generation = modalGeneration;
    let timer = 0;
    const pull = async () => {
      if (generation !== modalGeneration) return;
      try {
        const r = await api(`/api/admin/local/models/${encodeURIComponent(model.id)}/log`);
        const el = $("#lm-log");
        if (!el) return;
        const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
        el.textContent = (r.lines || []).join("\n") || L("Журнал пуст: модель ещё не запускалась.", "The log is empty: the model has not been started yet.");
        if (stick) el.scrollTop = el.scrollHeight;
      } catch (error) {  }
      timer = setTimeout(pull, 2000);
    };
    onModalClose(() => clearTimeout(timer));
    $("#lm-log-copy").onclick = () => copyText($("#lm-log").textContent);
    pull();
  }

  function openReinstall() {
    openModal(`<h3>${esc(L("Обновить движок", "Update the engine"))}</h3>
      <p class="muted">${esc(L("Скачается последняя сборка llama.cpp. Загруженные модели будут выгружены.", "The latest llama.cpp build is downloaded. Loaded models are unloaded."))}</p>
      <div class="field"><label>${esc(L("Сборка", "Build"))}</label><select id="lm-backend-2">
        <option value="auto">${esc(L("Автовыбор по видеокарте", "Pick by graphics card"))}</option>
        <option value="cuda">CUDA (NVIDIA)</option><option value="vulkan">Vulkan (AMD, Intel, NVIDIA)</option>
        <option value="cpu">${esc(L("Только процессор", "CPU only"))}</option></select></div>
      <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button><button class="btn primary" id="m-yes">${esc(L("Скачать", "Download"))}</button></div>`);
    $("#lm-backend-2").value = (S.status.settings && S.status.settings.backend) || "auto";
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      const backend = $("#lm-backend-2").value;
      closeModal();
      try { await api("/api/admin/local/runtime/install", { method: "POST", body: { backend } }); } catch (error) { toast(errorOf(error), "err"); }
      refresh();
    };
  }

  function openSettings(focus) {
    const s = (S.status && S.status.settings) || {};
    openModal(`<h3>${esc(L("Настройки локальных моделей", "Local model settings"))}</h3>
      <div class="grid-2">
        <div class="field"><label>${esc(L("Моделей в памяти одновременно", "Models in memory at once"))}</label>
          <input id="ls-max" type="number" min="1" max="8" value="${esc(s.max_loaded || 1)}">
          <div class="field-hint">${esc(L("При запросе к другой модели самая давняя выгружается.", "Requesting another model unloads the least recently used one."))}</div></div>
        <div class="field"><label>${esc(L("Потоков процессора по умолчанию", "Default CPU threads"))}</label>
          <input id="ls-threads" type="number" min="0" max="256" value="${esc(s.threads || 0)}">
          <div class="field-hint">${esc(L("0 — решает llama.cpp.", "0 — llama.cpp decides."))}</div></div>
      </div>
      <div class="field"><label>${esc(L("Свой llama-server (необязательно)", "Your own llama-server (optional)"))}</label>
        <input id="ls-path" class="mono" value="${esc(s.server_path || "")}" placeholder="C:\\llama.cpp\\llama-server.exe">
        <div class="field-hint">${esc(L("Если указан, встроенный движок не используется. Удобно для собственных сборок (ROCm, SYCL, Metal).", "When set, the built-in engine is not used. Handy for your own builds (ROCm, SYCL, Metal)."))}</div></div>
      <div class="field"><label>${esc(L("Токен Hugging Face (необязательно)", "Hugging Face token (optional)"))}</label>
        <input id="ls-token" type="password" autocomplete="off" placeholder="${esc(s.hf_token_set ? L("сохранён — оставьте пустым, чтобы не менять", "saved — leave empty to keep it") : "hf_…")}">
        <div class="field-hint">${esc(L("Нужен только для закрытых репозиториев (Llama, Gemma): сначала примите лицензию на сайте. Хранится локально и никуда, кроме huggingface.co, не отправляется.",
          "Only for gated repositories (Llama, Gemma): accept the licence on the site first. Stored locally and sent only to huggingface.co."))}</div>
        ${s.hf_token_set ? `<label class="lm-check"><input type="checkbox" id="ls-token-clear"> ${esc(L("Удалить сохранённый токен", "Remove the saved token"))}</label>` : ""}</div>
      <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button><button class="btn primary" id="m-yes">${esc(L("Сохранить", "Save"))}</button></div>`);
    if (focus === "server_path") queueMicrotask(() => $("#ls-path")?.focus());
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      const body = { max_loaded: $("#ls-max").value, threads: $("#ls-threads").value, server_path: $("#ls-path").value.trim() };
      const token = $("#ls-token").value.trim();
      if (token) body.hf_token = token;
      else if ($("#ls-token-clear")?.checked) body.hf_token = "";
      try {
        await api("/api/admin/local/settings", { method: "POST", body });
        closeModal();
        toast(L("Сохранено", "Saved"));
        refresh();
      } catch (error) { toast(errorOf(error), "err"); }
    };
  }

  async function addFile() {
    let path = "";
    try {
      const picked = await api("/api/admin/local/pick", { method: "POST" });
      if (!picked.ok) return;
      path = picked.path;
    } catch (error) {  }
    openModal(`<h3>${esc(L("Добавить файл модели", "Add a model file"))}</h3>
      <div class="field"><label>${esc(L("Путь к файлу .gguf", "Path to the .gguf file"))}</label>
        <input id="li-path" class="mono" value="${esc(path)}" placeholder="D:\\models\\Qwen3-8B-Q4_K_M.gguf">
        <div class="field-hint">${esc(L("Для модели из нескольких частей укажите первую (…-00001-of-0000N.gguf).", "For a split model, pick the first part (…-00001-of-0000N.gguf)."))}</div></div>
      <div class="field"><label>${esc(L("Название (необязательно)", "Name (optional)"))}</label><input id="li-name" placeholder="${esc(L("возьмётся из файла", "taken from the file"))}"></div>
      <label class="lm-check"><input type="checkbox" id="li-copy"> ${esc(L("Скопировать в папку MemuAI", "Copy into the MemuAI folder"))}</label>
      <p class="field-hint">${esc(L("Без копирования Proxy работает с файлом на месте и никогда его не удаляет.", "Without copying, the Proxy uses the file in place and never deletes it."))}</p>
      <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button><button class="btn primary" id="m-yes">${esc(L("Добавить", "Add"))}</button></div>`);
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = async () => {
      $("#m-yes").disabled = true;
      $("#m-yes").innerHTML = `<span class="spin">◌</span> ${esc(L("Читаю файл…", "Reading the file…"))}`;
      try {
        const r = await api("/api/admin/local/import", { method: "POST", body: { path: $("#li-path").value.trim().replace(/^"|"$/g, ""), name: $("#li-name").value.trim(), copy: $("#li-copy").checked } });
        closeModal();
        toast(L(`Добавлена: ${r.model.public_id}`, `Added: ${r.model.public_id}`));
        refresh();
      } catch (error) {
        toast(errorOf(error), "err");
        if ($("#m-yes")) { $("#m-yes").disabled = false; $("#m-yes").textContent = L("Добавить", "Add"); }
      }
    };
  }

  let searchSeq = 0;
  async function search() {
    S.query = $("#lm-q").value.trim();
    S.sort = $("#lm-sort").value;
    S.searching = true;
    S.open = "";
    renderResults();

    const seq = ++searchSeq;
    try {
      const r = await api(`/api/admin/local/hf/search?q=${encodeURIComponent(S.query)}&sort=${encodeURIComponent(S.sort)}`);
      if (seq !== searchSeq) return;
      S.results = r.results || [];
    } catch (error) {
      if (seq !== searchSeq) return;
      S.results = { error: errorOf(error) };
    }
    S.searching = false;
    renderResults();
  }

  function renderResults() {
    const box = $("#lm-results");
    if (!box) return;
    if (S.searching) { box.innerHTML = `<div class="empty"><span class="spin">◌</span> ${esc(L("Ищу…", "Searching…"))}</div>`; return; }
    if (!S.results) {
      box.innerHTML = `<div class="lm-suggest">${esc(L("Популярное:", "Popular:"))} ${["Qwen3.8", "Gemma 4", "Qwen3.6", "Ministral 3", "gpt-oss", "GLM-4.7"]
        .map((q) => `<button type="button" class="chip-btn" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
      box.querySelectorAll("[data-q]").forEach((b) => { b.onclick = () => { $("#lm-q").value = b.dataset.q; search(); }; });
      return;
    }
    if (S.results.error) { box.innerHTML = `<div class="warn-strip">${esc(S.results.error)}</div>`; return; }
    if (!S.results.length) { box.innerHTML = `<div class="empty">${esc(L("Ничего не нашлось. Попробуйте короче: «qwen 7b».", "Nothing found. Try something shorter: “qwen 7b”."))}</div>`; return; }
    const installed = new Set((S.status && S.status.models || []).map((m) => m.repo).filter(Boolean));
    box.innerHTML = `<div class="lm-results">${S.results.map((r) => {
      const open = S.open === r.repo;
      return `<div class="lm-repo ${open ? "open" : ""}">
        <button type="button" class="lm-repo-head" data-lm="hf-open" data-repo="${esc(r.repo)}" aria-expanded="${open}">
          <span class="lm-repo-name"><b>${esc(r.repo.split("/")[1])}</b><span class="muted">${esc(r.author)}</span></span>
          <span class="lm-repo-tags">${r.vision ? `<span class="chip tag">${esc(L("зрение", "vision"))}</span>` : ""}
            ${r.gated ? `<span class="chip warn" title="${esc(L("Нужен токен и принятая лицензия", "Needs a token and an accepted licence"))}">${esc(L("закрытая", "gated"))}</span>` : ""}
            ${installed.has(r.repo) ? `<span class="chip">${esc(L("уже есть", "installed"))}</span>` : ""}
            <span class="muted" title="${esc(L("Скачиваний", "Downloads"))}">↓ ${count(r.downloads)}</span>
            <span class="muted" title="${esc(L("Лайков", "Likes"))}">♥ ${count(r.likes)}</span></span>
          <span class="lm-caret">${open ? "▾" : "▸"}</span>
        </button>
        ${open ? `<div class="lm-files">${renderFiles(r.repo)}</div>` : ""}
      </div>`;
    }).join("")}</div>`;
  }

  function renderFiles(repo) {
    const data = S.files[repo];
    if (!data) return `<div class="empty"><span class="spin">◌</span></div>`;
    if (data.error) return `<div class="warn-strip">${esc(data.error)}</div>`;
    if (!data.files.length) return `<div class="empty">${esc(L("В репозитории нет файлов GGUF.", "The repository has no GGUF files."))}</div>`;
    const rec = pickRecommended(data.files);
    const dl = new Set((S.status.downloads || []).filter((d) => d.state === "downloading" || d.state === "queued").map((d) => d.files[0]));
    const projector = data.projectors.length ? `<div class="row lm-proj"><label for="lm-proj">${esc(L("Проектор изображений:", "Image projector:"))}</label>
      <select id="lm-proj"><option value="">${esc(L("не скачивать", "do not download"))}</option>${data.projectors.map((p, i) =>
        `<option value="${esc(p.path)}" ${i === 0 ? "selected" : ""}>${esc(p.path.split("/").pop())} · ${bytes(p.size)}</option>`).join("")}</select></div>` : "";
    const drafts = data.drafts || [];
    const draft = drafts.length ? `<div class="row lm-proj"><label for="lm-draft">${esc(L("Ускорение (draft, необязательно):", "Speed-up (draft, optional):"))}</label>
      <select id="lm-draft"><option value="">${esc(L("не скачивать", "do not download"))}</option>${drafts.map((p) =>
        `<option value="${esc(p.path)}">${esc(p.path.split("/").pop())} · ${bytes(p.size)}</option>`).join("")}</select>
      <span class="field-hint">${esc(L("Draft-файлы — не модели: они только ускоряют основную модель и отдельно не запускаются.",
        "Draft files are not models: they only speed up the main model and cannot run on their own."))}</span></div>` : "";
    return `${projector}${draft}<table class="tbl lm-files-tbl"><thead><tr><th>${esc(L("Файл", "File"))}</th><th>${esc(L("Квант", "Quant"))}</th><th>${esc(L("Размер", "Size"))}</th><th>${esc(L("На этом ПК", "On this PC"))}</th><th></th></tr></thead><tbody>
      ${data.files.map((f) => `<tr class="${f === rec ? "rec" : ""}"><td class="mono">${esc(f.name)}${f.files.length > 1 ? ` <span class="chip dim">${f.files.length} ${esc(L("части", "parts"))}</span>` : ""}
        ${f === rec ? ` <span class="chip tag">${esc(L("рекомендую", "recommended"))}</span>` : ""}</td>
        <td>${esc(f.quant || "—")}</td><td>${bytes(f.size)}</td><td>${fitBadge(f.fit)}</td>
        <td class="actions">${dl.has(f.files[0]) ? `<span class="muted">${esc(L("качается…", "downloading…"))}</span>`
          : `<button class="btn small ${f === rec ? "primary" : ""}" data-lm="hf-get" data-repo="${esc(repo)}" data-file="${esc(f.name)}">${esc(L("Скачать", "Download"))}</button>`}</td></tr>`).join("")}
      </tbody></table>`;
  }

  function pickRecommended(files) {
    const good = files.filter((f) => f.fit && (f.fit.level === "gpu" || f.fit.level === "partial" || f.fit.level === "ram"));
    if (!good.length) return null;
    const pref = good.filter((f) => /^Q(4_K_M|5_K_M|6_K|8_0)$/i.test(f.quant || ""));
    const pool = pref.length ? pref : good;
    const onGpu = pool.filter((f) => f.fit.level === "gpu");
    return (onGpu.length ? onGpu : pool).reduce((a, b) => (b.size > a.size ? b : a));
  }

  async function toggleRepo(repo) {
    S.open = S.open === repo ? "" : repo;
    renderResults();
    if (!S.open || (S.files[repo] && !S.files[repo].error)) return;
    try { S.files[repo] = await api(`/api/admin/local/hf/files?repo=${encodeURIComponent(repo)}`); }
    catch (error) { S.files[repo] = { error: errorOf(error) }; }
    if (S.open === repo) renderResults();
  }

  async function download(repo, name) {
    const data = S.files[repo];
    const group = data && data.files.find((f) => f.name === name);
    if (!group) return;
    const fit = group.fit || {};
    const go = async () => {
      try {
        await api("/api/admin/local/download", { method: "POST", body: { repo, files: group.files,
          projector: $("#lm-proj")?.value || "", draft: $("#lm-draft")?.value || "" } });
        toast(L("Загрузка началась", "Download started"));
      } catch (error) { toast(errorOf(error), "err"); }
      refresh();
    };
    if (fit.level === "too_big" || fit.level === "tight") {
      return confirmDialog(L("Модель может не поместиться", "The model may not fit"),
        L(`Нужно около ${bytes(fit.need)}, а в компьютере ${bytes(fit.ram)} ОЗУ. Она может работать очень медленно или не запуститься. Всё равно скачать?`,
          `It needs about ${bytes(fit.need)} and this computer has ${bytes(fit.ram)} of RAM. It may be very slow or fail to start. Download anyway?`),
        go, L("Скачать", "Download"));
    }
    return go();
  }

  async function scan() {
    S.scanning = true;
    renderServers();
    try { S.servers = (await api("/api/admin/local/scan", { method: "POST" })).servers || []; }
    catch (error) { S.servers = { error: errorOf(error) }; }
    S.scanning = false;
    renderServers();
  }

  function renderServers() {
    const box = $("#lm-servers");
    if (!box) return;
    if (S.scanning) { box.innerHTML = `<div class="empty"><span class="spin">◌</span> ${esc(L("Проверяю порты 11434, 1234, 8080, 1337, 5001, 5000, 8000…", "Checking ports 11434, 1234, 8080, 1337, 5001, 5000, 8000…"))}</div>`; return; }
    if (!S.servers) { box.innerHTML = ""; return; }
    if (S.servers.error) { box.innerHTML = `<div class="warn-strip">${esc(S.servers.error)}</div>`; return; }
    if (!S.servers.length) { box.innerHTML = `<div class="empty">${esc(L("Запущенных серверов не найдено. Запустите Ollama или сервер LM Studio и проверьте снова.", "No running servers found. Start Ollama or the LM Studio server and scan again."))}</div>`; return; }
    const taken = new Set((typeof accountsCache !== "undefined" ? accountsCache : []).filter((a) => a.provider === "custom")
      .map((a) => String((a.fields && a.fields.base_url) || "").replace(/\/$/, "")));
    box.innerHTML = S.servers.map((s) => `<div class="lm-server row">
      <b>${esc(s.title)}</b><code class="mono">${esc(s.base_url)}</code>
      <span class="muted grow">${esc(s.models.length)} ${esc(L("моделей", "models"))}${s.models.length ? `: ${esc(s.models.slice(0, 4).join(", "))}${s.models.length > 4 ? "…" : ""}` : ""}</span>
      ${taken.has(s.base_url) ? `<span class="badge ok">${esc(L("Подключён", "Connected"))}</span>`
        : `<button class="btn small primary" data-lm="srv-add" data-id="${esc(s.id)}">${esc(L("Подключить", "Connect"))}</button>`}</div>`).join("");
  }

  async function connectServer(id) {
    const server = (S.servers || []).find((s) => s.id === id);
    if (!server) return;
    const before = typeof modelIdsSnapshot === "function" ? await modelIdsSnapshot() : null;
    const added = await api("/api/admin/accounts", { method: "POST", body: { provider: "custom", label: server.title,
      fields: { name: server.id, base_url: server.base_url, api_key: "" } } });
    if (typeof offerNewModels === "function") offerNewModels(before, added && added.account && added.account.id);
    toast(L(`${server.title} подключён: модели появятся как ${server.id}-<модель>`, `${server.title} connected: its models appear as ${server.id}-<model>`));
    if (typeof loadAccounts === "function") await loadAccounts();
    renderServers();
  }

  return {
    load,

    relabel() {
      const root = $("#local-root");
      if (!root || !root.dataset.ready) return;
      root.removeEventListener("click", onClick);
      delete root.dataset.ready;
      shell(root);
      if (S.status) renderDynamic();
      if (window.MemuLocalWizard) MemuLocalWizard.relabel();
    },
  };
})();
