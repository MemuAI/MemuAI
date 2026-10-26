

"use strict";

window.MemuLocalWizard = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const GB = 1024 ** 3;

  const OWN = {
    code: '<path d="m8 7-5 5 5 5"/><path d="m16 7 5 5-5 5"/><path d="m13.5 4.5-3 15"/>',
    wrench: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 6.2-6.2a4 4 0 0 1-5-5L13 5z"/><path d="M3 21l6-6"/>',
    file: '<path d="M14 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V9z"/><path d="M14 3.5V9h5.5"/><path d="M8.5 13h7M8.5 16.5h5"/>',
    gpu: '<rect x="2.5" y="6.5" width="19" height="11" rx="2"/><circle cx="9" cy="12" r="2.6"/><circle cx="16" cy="12" r="2.6"/><path d="M5 17.5v2M8 17.5v2M11 17.5v2"/>',
    memory: '<rect x="3" y="7" width="18" height="9" rx="1.5"/><path d="M7 10.5v2M11 10.5v2M15 10.5v2M19 10.5v2"/><path d="M6 16v2.5M10 16v2.5M14 16v2.5M18 16v2.5"/>',
    disk: '<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><circle cx="12" cy="11" r="3.5"/><path d="M12 11h.01M7 17h2"/>',
    lock: '<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/><path d="M12 14.5v2"/>',
  };
  const ico = (name) => (OWN[name]
    ? `<svg class="i" data-i="${name}" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${OWN[name]}</svg>`
    : (window.MemuIcons ? MemuIcons.svg(name) : ""));
  const STEPS = [
    ["pc", () => L("Компьютер", "Computer")],
    ["uses", () => L("Задачи", "Tasks")],
    ["prio", () => L("Приоритеты", "Priorities")],
    ["result", () => L("Рекомендации", "Recommendations")],
  ];
  const USES = [
    ["chat", "chat", () => L("Общение и вопросы", "Chat and questions"), () => L("Ответы, советы, тексты, перевод", "Answers, advice, writing, translation")],
    ["coding", "code", () => L("Программирование", "Coding"), () => L("Код, объяснения, исправление ошибок", "Code, explanations, bug fixing")],
    ["agent", "wrench", () => L("Агенты и инструменты", "Agents and tools"), () => L("MemuCode, вызов функций, автоматизация", "MemuCode, function calls, automation")],
    ["vision", "image", () => L("Картинки и скриншоты", "Images and screenshots"), () => L("Описать фото, прочитать скриншот или документ", "Describe a photo, read a screenshot or a document")],
    ["reasoning", "sparkles", () => L("Сложные рассуждения", "Hard reasoning"), () => L("Математика, логика, многошаговые задачи", "Maths, logic, multi-step problems")],
    ["docs", "file", () => L("Длинные документы", "Long documents"), () => L("Большие файлы, книги, длинная переписка", "Big files, books, long threads")],
  ];
  const S = {
    step: 0, hw: null, runtime: null, edited: null, editing: false, loadingHw: false,
    goals: { uses: ["chat"], language: LANG === "en" ? "en" : "ru", priority: 40, context: 0, license: "any", keep_free_gb: 0, mtp: false },
    result: null, loading: false, error: "", picked: {}, installs: {}, poll: 0, expanded: new Set(), gen: 0, showMore: false,
  };

  function gb(value, digits = 1) {
    const n = (Number(value) || 0) / GB;
    if (!n) return "—";
    const text = n >= 100 ? n.toFixed(0) : n.toFixed(digits);
    return `${LANG === "en" ? text : text.replace(".", ",")} ${L("ГБ", "GB")}`;
  }
  function num(value, digits = 0) {
    const text = (Number(value) || 0).toFixed(digits);
    return LANG === "en" ? text : text.replace(".", ",");
  }
  function tps(value) { return num(value, value >= 10 ? 0 : 1); }
  function ctx(tokens) {
    const n = Number(tokens) || 0;
    if (n >= 1048576) return `${Math.round(n / 1048576)}M`;
    return n >= 1024 ? `${Math.round(n / 1024)}K` : String(n);
  }
  function errorOf(error) { return error && error.message ? error.message : String(error || ""); }
  const PLACE = {
    gpu: ["ok", () => L("Целиком на видеокарте", "Fully on the GPU")],
    partial: ["info", () => L("Видеокарта + ОЗУ", "GPU + RAM")],
    cpu: ["warn", () => L("Процессор и ОЗУ", "CPU and RAM")],
    none: ["invalid", () => L("Не помещается", "Does not fit")],
  };
  function placeBadge(placement, share) {
    const [cls, label] = PLACE[placement] || PLACE.none;
    const pct = placement === "partial" && share ? ` · ${Math.round(share * 100)}%` : "";
    return `<span class="badge ${cls}">${esc(label())}${esc(pct)}</span>`;
  }

  function open() {
    S.gen += 1;
    S.step = S.result ? 3 : 0;
    openModal(`<div class="lw" id="lw">
        <div class="lw-head">
          <div class="lw-title"><span class="lw-mark" aria-hidden="true">${ico("sparkles")}</span>
            <div><h3>${esc(L("Мастер подбора локальной модели", "Local model wizard"))}</h3>
            <p class="lw-sub">${esc(L("Подберу лучшие модели под ваш компьютер и задачи — с квантованием, скоростью и памятью.",
              "I will pick the best models for your computer and tasks — with quantisation, speed and memory."))}</p></div></div>
          <button class="icon-btn lw-close" id="lw-close" aria-label="${esc(L("Закрыть", "Close"))}" title="${esc(L("Закрыть", "Close"))}">✕</button>
        </div>
        <ol class="lw-steps" id="lw-steps" role="tablist"></ol>
        <div class="lw-body" id="lw-body" tabindex="-1"></div>
        <div class="lw-foot" id="lw-foot"></div>
      </div>`, true);
    $("#modal-box").classList.add("lw-modal");
    $("#lw-close").onclick = closeModal;
    $("#lw").addEventListener("click", onClick);
    $("#lw").addEventListener("change", onChange);
    $("#lw").addEventListener("input", onInput);
    const gen = S.gen;
    onModalClose(() => { if (gen === S.gen) { clearTimeout(S.poll); S.poll = 0; } });
    render();
    if (!S.hw) loadHardware(false);
  }

  function alive() { return Boolean($("#lw")); }

  function render() {
    if (!alive()) return;
    $("#lw-steps").innerHTML = STEPS.map(([id, label], i) => `<li class="${i === S.step ? "on" : ""} ${i < S.step ? "done" : ""}">
      <button type="button" role="tab" aria-selected="${i === S.step}" data-lw="goto" data-step="${i}" ${i > maxStep() ? "disabled" : ""}>
        <span class="lw-num">${i < S.step ? ico("check") : i + 1}</span><span>${esc(label())}</span></button></li>`).join("");
    const body = $("#lw-body");
    body.innerHTML = [renderPc, renderUses, renderPrio, renderResult][S.step]();
    renderFoot();
  }

  function maxStep() { return S.hw ? (S.result ? 3 : 2) : 0; }

  function renderFoot() {
    const foot = $("#lw-foot");
    const back = S.step > 0 ? `<button class="btn" data-lw="back">${esc(L("Назад", "Back"))}</button>` : "";
    let next = "";
    if (S.step < 2) next = `<button class="btn primary" data-lw="next" ${S.hw ? "" : "disabled"}>${esc(L("Далее", "Next"))} →</button>`;
    else if (S.step === 2) next = `<button class="btn primary" data-lw="recommend" ${S.loading ? "disabled" : ""}>${ico("sparkles")} ${esc(L("Подобрать модели", "Find models"))}</button>`;
    else next = `<button class="btn" data-lw="restart">${esc(L("Изменить условия", "Change the conditions"))}</button>`;
    const note = S.step === 3 && S.result ? `<span class="lw-foot-note">${esc(L("Каталог", "Catalogue"))}: ${esc(S.result.catalog.count)} ${esc(L("моделей", "models"))} · ${esc(S.result.catalog.version || "")}
      <button type="button" class="link-btn" data-lw="refresh-catalog">${esc(L("обновить данные", "refresh data"))}</button></span>` : "";
    foot.innerHTML = `${note}<span class="lw-spacer"></span>${back}${next}`;
  }

  async function loadHardware(refresh) {
    S.loadingHw = true;
    render();
    try {
      const r = await api("/api/admin/local/wizard/hardware" + (refresh ? "?refresh=1" : ""));
      S.hw = r.hardware; S.runtime = r.runtime;
    } catch (error) {
      S.error = errorOf(error);
    }
    S.loadingHw = false;
    if (alive()) render();
  }

  function profile() { return S.edited || S.hw; }

  function renderPc() {
    if (!S.hw) {
      return S.loadingHw ? `<div class="lw-empty"><span class="spin">◌</span> ${esc(L("Определяю видеокарту, процессор и память…", "Detecting the GPU, CPU and memory…"))}</div>`
        : `<div class="warn-strip">${esc(S.error || L("Не удалось определить железо", "Could not detect the hardware"))}</div>`;
    }
    const hw = profile();
    const gpu = hw.main_gpu;
    const cpu = hw.cpu || {};
    const ram = hw.ram || {};
    const others = (hw.gpus || []).filter((g) => g !== gpu && (!gpu || g.name !== gpu.name));
    const backend = { cuda: "CUDA", vulkan: "Vulkan", cpu: L("Процессор", "CPU") }[hw.backend] || hw.backend;
    const flags = [cpu.avx2 ? "AVX2" : cpu.avx2 === false ? L("без AVX2", "no AVX2") : "", cpu.avx512 ? "AVX-512" : ""].filter(Boolean);
    const tile = (icon, title, main, sub, chips = []) => `<div class="lw-hw-tile"><div class="lw-hw-ico" aria-hidden="true">${ico(icon)}</div>
      <div class="lw-hw-text"><span class="lw-hw-cap">${esc(title)}</span><b class="selectable">${esc(main)}</b>
      ${sub ? `<span class="lw-hw-sub">${esc(sub)}</span>` : ""}${chips.length ? `<span class="lw-chips">${chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</span>` : ""}</div></div>`;
    const gpuMain = gpu ? gpu.name : L("Нет отдельной видеокарты", "No dedicated GPU");
    const also = gpu && others.length ? ` · ${L("ещё", "also")}: ${others.map((g) => g.name + (g.integrated ? ` (${L("встроенная", "integrated")})` : "")).join(", ")}` : "";
    const gpuSub = gpu ? `${gb(gpu.vram)} ${L("видеопамяти", "of VRAM")}${gpu.bandwidth ? ` · ≈ ${gpu.bandwidth} ${L("ГБ/с", "GB/s")}` : ""}${also}`
      : (others.length ? `${others.map((g) => g.name).join(", ")} — ${L("встроенная, модели пойдут на процессоре", "integrated, models run on the CPU")}` : L("Модели будут работать на процессоре", "Models run on the CPU"));
    const ramSub = [ram.type, ram.speed ? `${ram.speed} MT/s` : "", ram.bandwidth ? `≈ ${ram.bandwidth} ${L("ГБ/с", "GB/s")}` : ""].filter(Boolean).join(" · ");
    const engine = S.runtime ? (S.runtime.installed ? `<span class="badge ok">${esc(L("Движок llama.cpp установлен", "llama.cpp engine installed"))}${S.runtime.tag ? ` · ${esc(S.runtime.tag)}` : ""}</span>`
      : `<span class="badge warn">${esc(L("Движок ещё не установлен — мастер поставит его сам", "The engine is not installed yet — the wizard installs it"))}</span>`) : "";
    return `<div class="lw-section">
        <div class="row lw-row-head"><h4 class="grow">${esc(L("Что я нашёл", "What I found"))}${hw.mocked ? ` <span class="chip warn">${esc(L("тестовые данные", "test data"))}</span>` : ""}${S.edited ? ` <span class="chip tag">${esc(L("изменено вручную", "edited by hand"))}</span>` : ""}</h4>
          <button class="btn small" data-lw="hw-refresh" ${S.loadingHw ? "disabled" : ""}>⟳ ${esc(L("Определить заново", "Detect again"))}</button>
          <button class="btn small" data-lw="hw-edit" aria-expanded="${S.editing}">✎ ${esc(L("Другой компьютер…", "Another computer…"))}</button></div>
        <div class="lw-hw-grid">
          ${tile("gpu", L("Видеокарта", "Graphics card"), gpuMain, gpuSub, gpu ? [backend] : [backend])}
          ${tile("cpu", L("Процессор", "Processor"), cpu.name || L("Неизвестный процессор", "Unknown processor"),
            `${cpu.cores || "?"} ${L("ядер", "cores")} · ${cpu.threads || "?"} ${L("потоков", "threads")}`, flags)}
          ${tile("memory", L("Оперативная память", "Memory (RAM)"), gb(ram.total, 0), ramSub)}
          ${tile("disk", L("Свободно на диске", "Free disk space"), gb((hw.disk || {}).free, 0), (hw.os || {}).label || "")}
        </div>
        ${S.editing ? renderEdit(hw) : ""}
        <div class="lw-engine">${engine}</div>
      </div>
      <p class="lw-hint">${esc(L("От видеопамяти зависит, какие модели поместятся целиком и будут быстрыми; от ОЗУ — какие вообще запустятся. Все модели работают на этом компьютере: без интернета и без отправки данных.",
        "Video memory decides which models fit entirely and run fast; RAM decides which run at all. Every model runs on this computer: offline, no data leaves it."))}</p>`;
  }

  function renderEdit(hw) {
    const gpu = hw.main_gpu || {};
    const vram = Math.round((gpu.vram || 0) / GB);
    const ram = Math.round(((hw.ram || {}).total || 0) / GB);
    const opt = (values, current, label) => values.map((v) => `<option value="${v}" ${v === current ? "selected" : ""}>${esc(label(v))}</option>`).join("");
    return `<div class="lw-edit">
      <p class="field-hint">${esc(L("Подбор для другого компьютера или если что-то определилось неверно. Скорость оценивается по типу видеокарты.",
        "Plan for another computer or fix a wrong detection. Speed is estimated from the card model."))}</p>
      <div class="grid-3">
        <div class="field"><label>${esc(L("Видеокарта", "Graphics card"))}</label>
          <input id="lw-e-gpu" value="${esc(gpu.name || "")}" placeholder="${esc(L("например, RTX 4070", "e.g. RTX 4070"))}"></div>
        <div class="field"><label>${esc(L("Видеопамять", "Video memory"))}</label>
          <select id="lw-e-vram">${opt([0, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 48, 96], [0, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 48, 96].includes(vram) ? vram : -1,
            (v) => (v ? `${v} ${L("ГБ", "GB")}` : L("нет видеокарты", "no GPU")))}${[0, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32, 48, 96].includes(vram) ? "" : `<option value="${vram}" selected>${vram} ${esc(L("ГБ", "GB"))}</option>`}</select></div>
        <div class="field"><label>${esc(L("Оперативная память", "RAM"))}</label>
          <select id="lw-e-ram">${opt([8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256], [8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256].includes(ram) ? ram : -1, (v) => `${v} ${L("ГБ", "GB")}`)}${[8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256].includes(ram) ? "" : `<option value="${ram}" selected>${ram} ${esc(L("ГБ", "GB"))}</option>`}</select></div>
      </div>
      <div class="row-end"><button class="btn small" data-lw="hw-reset">${esc(L("Вернуть найденное", "Back to detected"))}</button>
        <button class="btn small primary" data-lw="hw-apply">${esc(L("Применить", "Apply"))}</button></div></div>`;
  }

  function applyEdit() {
    const base = JSON.parse(JSON.stringify(S.hw));
    const name = $("#lw-e-gpu").value.trim();
    const vram = Number($("#lw-e-vram").value) || 0;
    const ram = Number($("#lw-e-ram").value) || 0;
    base.gpus = vram ? [{ name: name || "GPU", vram: vram * GB }] : [];
    base.ram = { ...(base.ram || {}), total: ram * GB, bandwidth: 0 };
    delete base.main_gpu;
    S.edited = base;
    S.editing = false;
    S.result = null;

    S.edited.main_gpu = vram ? { name: name || "GPU", vram: vram * GB, bandwidth: 0, vendor: "" } : null;
    S.edited.backend = vram ? (/(nvidia|geforce|rtx|gtx|quadro)/i.test(name) ? "cuda" : "vulkan") : "cpu";
    render();
  }

  function renderUses() {
    const g = S.goals;
    const tiles = USES.map(([id, icon, title, sub]) => {
      const on = g.uses.includes(id);
      return `<button type="button" class="lw-tile ${on ? "on" : ""}" data-lw="use" data-use="${id}" aria-pressed="${on}">
        <span class="lw-tile-ico" aria-hidden="true">${ico(icon)}</span>
        <span class="lw-tile-text"><b>${esc(title())}</b><span>${esc(sub())}</span></span>
        <span class="lw-tile-check" aria-hidden="true">${ico("check")}</span></button>`;
    }).join("");
    return `<div class="lw-section"><h4>${esc(L("Для чего нужна модель?", "What is the model for?"))}</h4>
        <p class="lw-hint">${esc(L("Можно выбрать несколько — модель будет хороша во всём выбранном.", "Pick several if you like — the model should be good at all of them."))}</p>
        <div class="lw-tiles">${tiles}</div></div>
      <div class="lw-section"><h4>${esc(L("На каком языке будете общаться?", "Which language will you use?"))}</h4>
        ${seg("language", [["ru", L("В основном по-русски", "Mostly Russian")], ["en", L("В основном по-английски", "Mostly English")], ["both", L("На обоих", "Both")]], g.language)}
        <p class="lw-hint">${esc(L("Для русского важен не только размер: одни модели пишут по-русски естественно, другие — с ошибками.",
          "For Russian, size is not everything: some models write it naturally, others with mistakes."))}</p></div>`;
  }

  function seg(key, options, current) {
    return `<div class="lw-seg" role="radiogroup" data-key="${key}">${options.map(([value, label]) =>
      `<button type="button" role="radio" aria-checked="${String(value) === String(current)}" class="${String(value) === String(current) ? "on" : ""}" data-lw="seg" data-key="${key}" data-value="${esc(value)}">${esc(label)}</button>`).join("")}</div>`;
  }

  function autoContext() {
    const u = S.goals.uses;
    return u.includes("docs") ? 65536 : (u.includes("coding") || u.includes("agent")) ? 32768 : 16384;
  }

  function renderPrio() {
    const g = S.goals;
    const p = g.priority;
    const label = p <= 20 ? L("Максимум качества — пусть медленнее", "Maximum quality — slower is fine")
      : p <= 45 ? L("Баланс с уклоном в качество", "Balanced, leaning to quality")
      : p <= 65 ? L("Баланс с уклоном в скорость", "Balanced, leaning to speed") : L("Максимум скорости — ответы мгновенно", "Maximum speed — instant answers");
    return `<div class="lw-section"><h4>${esc(L("Качество или скорость?", "Quality or speed?"))}</h4>
        <div class="lw-slider"><span>${esc(L("Качество", "Quality"))}</span>
          <input type="range" id="lw-prio" min="0" max="100" step="5" value="${p}" aria-label="${esc(L("Качество или скорость", "Quality or speed"))}" aria-valuetext="${esc(label)}">
          <span>${esc(L("Скорость", "Speed"))}</span></div>
        <p class="lw-hint" id="lw-prio-label">${esc(label)}</p></div>
      <div class="lw-section lw-two">
        <div><h4>${esc(L("Длина контекста", "Context length"))}</h4>
          ${seg("context", [[0, L(`Авто (${ctx(autoContext())})`, `Auto (${ctx(autoContext())})`)], [8192, "8K"], [16384, "16K"], [32768, "32K"], [65536, "64K"], [131072, "128K"]], g.context)}
          <p class="lw-hint">${esc(L("Сколько текста модель держит в памяти разговора. 16K ≈ 25 страниц. Больше — нужно больше памяти.",
            "How much text the model keeps in mind. 16K ≈ 25 pages. Longer needs more memory."))}</p></div>
        <div><h4>${esc(L("Оставить память другим программам", "Leave memory for other programs"))}</h4>
          ${seg("keep_free_gb", [[0, L("Не нужно", "Not needed")], [4, `4 ${L("ГБ", "GB")}`], [8, `8 ${L("ГБ", "GB")}`], [16, `16 ${L("ГБ", "GB")}`]], g.keep_free_gb)}
          <p class="lw-hint">${esc(L("Если параллельно открыты браузер, IDE или игры.", "If a browser, an IDE or games run alongside."))}</p></div>
      </div>
      <div class="lw-section"><h4>${esc(L("Лицензия", "Licence"))}</h4>
        ${seg("license", [["any", L("Любая", "Any")], ["commercial", L("Можно в работе (коммерция)", "Commercial use allowed")], ["open", L("Только Apache 2.0 / MIT", "Apache 2.0 / MIT only")]], g.license)}</div>
      <label class="lm-check lw-check"><input type="checkbox" id="lw-mtp" ${g.mtp ? "checked" : ""}>
        <span>${esc(L("Скачать MTP-ускоритель, если он есть у модели (экспериментально: спекулятивное декодирование, +≈1 ГБ)",
          "Download the MTP speed-up when the model has one (experimental: speculative decoding, +≈1 GB)"))}</span></label>
      <div class="lw-privacy">${ico("lock")}<span>${esc(L("Конфиденциальность и офлайн: модель работает только на этом компьютере. Интернет нужен один раз — чтобы скачать файлы.",
        "Privacy and offline: the model runs only on this computer. The internet is needed once — to download the files."))}</span></div>`;
  }

  async function recommend() {
    S.loading = true; S.error = "";
    S.step = 3;
    render();
    const gen = S.gen;
    try {
      const body = { goals: { ...S.goals, limit: 8 } };
      if (S.edited) body.hardware = S.edited;
      S.result = await api("/api/admin/local/wizard/recommend", { method: "POST", body });
      S.picked = {}; S.expanded = new Set(); S.showMore = false;
    } catch (error) {
      S.error = errorOf(error);
    }
    S.loading = false;
    if (gen === S.gen && alive()) render();
  }

  function renderResult() {
    if (S.loading) return `<div class="lw-empty"><span class="spin">◌</span> ${esc(L("Сравниваю модели, кванты и скорость на вашем железе…", "Comparing models, quants and speed on your hardware…"))}</div>`;
    if (S.error) return `<div class="warn-strip">${esc(S.error)}</div>`;
    const r = S.result;
    if (!r) return "";
    const rows = S.showMore ? r.results.concat(r.more || []) : r.results;
    const top = rows.length ? rows.map(card).join("") : `<div class="lw-empty">${esc(L("Под эти условия ничего не подошло. Уберите «Картинки» или «Лицензию», уменьшите контекст или выберите «Скорость».",
      "Nothing matches these conditions. Drop “Images” or the licence condition, lower the context or move towards speed."))}</div>`;
    const more = (r.more || []).length && !S.showMore ? `<button class="btn small lw-more" data-lw="show-more">${esc(L(`Показать ещё ${r.more.length}`, `Show ${r.more.length} more`))}</button>` : "";
    const excluded = (r.excluded || []).length ? `<details class="lw-excluded"><summary>${esc(L(`Не подошли: ${r.excluded.length}`, `Ruled out: ${r.excluded.length}`))}</summary>
      <ul>${r.excluded.map((e) => `<li><b>${esc(e.name)}</b> — ${esc(L(e.reason, e.reason_en))}</li>`).join("")}</ul></details>` : "";
    return `<div class="lw-summary">${ico("sparkles")}<span>${esc(L(r.summary, r.summary_en))}</span></div>
      <div class="lw-cards">${top}</div>${more}${excluded}
      <p class="lw-hint lw-fine">${esc(L("Скорость — оценка по пропускной способности памяти (±25 %); реальная зависит от драйвера, длины разговора и нагрузки. Оценки качества — сравнительные, по опубликованным тестам моделей.",
        "Speed is an estimate from memory bandwidth (±25%); real speed depends on the driver, conversation length and load. Quality scores are comparative, from the models' published benchmarks."))}</p>`;
  }

  function current(row) {
    const quant = S.picked[row.id] || row.quant;
    const alt = (row.alternatives || []).find((a) => a.quant === quant) || {};
    return { quant, alt, isBest: quant === row.quant };
  }

  function capChips(row) {
    const c = row.caps || {};
    const chips = [];
    if (row.moe) chips.push(["tag", "MoE"]);
    if (c.vision) chips.push(["", L("зрение", "vision")]);
    if (c.reasoning) chips.push(["", L("размышления", "reasoning")]);
    if (c.tools) chips.push(["", L("инструменты", "tools")]);
    if (c.audio) chips.push(["", L("звук", "audio")]);
    if ((row.languages || []).includes("ru")) chips.push(["ok", "RU"]);
    return chips.map(([cls, text]) => `<span class="chip ${cls}">${esc(text)}</span>`).join("");
  }

  function card(row) {
    const { quant, alt, isBest } = current(row);
    const speed = isBest ? row.tps : alt.tps;
    const placement = isBest ? row.placement : alt.placement;
    const size = isBest ? row.size : alt.size;
    const context = isBest ? row.context : alt.context;
    const bar = Math.max(4, Math.min(100, Math.round((Math.log(Math.max(2, speed)) - Math.log(2)) / (Math.log(80) - Math.log(2)) * 100)));
    const why = L(row.why, row.why_en) || [];
    const warnings = L(row.warnings, row.warnings_en) || [];
    const open = S.expanded.has(row.id);
    const shown = open ? why : why.slice(0, 3);

    const options = (row.alternatives || []).map((a) => `<option value="${esc(a.quant)}" ${a.quant === quant ? "selected" : ""} ${a.placement === "none" ? "disabled" : ""}>${esc(a.file_quant || a.quant)} · ${esc(gb(a.size))}${a.placement === "none" ? ` — ${esc(L("не влезет", "won't fit"))}` : a.quant === row.quant ? " ★" : ""}</option>`).join("");
    const inst = S.installs[row.id];
    const download = size + ((row.mmproj && row.mmproj.size) || 0);
    return `<article class="lw-card ${row.rank === 1 ? "best" : ""}" data-id="${esc(row.id)}">
      <div class="lw-card-main">
        <div class="lw-card-head">
          <span class="lw-rank" aria-label="${esc(L("Место", "Rank"))} ${row.rank}">${row.rank}</span>
          <div class="lw-card-title"><b>${esc(row.name)}</b>
            <span class="lw-card-meta">${esc(row.vendor)} · ${esc(num(row.params_b, row.params_b < 10 ? 1 : 0))}B${row.moe ? ` (${esc(L("активно", "active"))} ${esc(num(row.active_b, 1))}B)` : ""} · ${esc(row.released || "")}</span></div>
          ${row.rank === 1 ? `<span class="chip tag lw-best">${esc(L("Лучший выбор", "Best pick"))}</span>` : ""}
          ${row.installed ? `<span class="badge ok">${esc(L("Установлена", "Installed"))}</span>` : ""}
        </div>
        <div class="lw-chips">${capChips(row)}</div>
        <ul class="lw-why">${shown.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
        ${why.length > 3 ? `<button type="button" class="link-btn" data-lw="expand">${esc(open ? L("Свернуть", "Less") : L(`Почему ещё (${why.length - 3})`, `More reasons (${why.length - 3})`))}</button>` : ""}
        ${warnings.length ? `<ul class="lw-warn">${warnings.map((w) => `<li>${ico("alert")}<span>${esc(w)}</span></li>`).join("")}</ul>` : ""}
      </div>
      <aside class="lw-card-side">
        <div class="lw-speed"><span class="lw-speed-num">≈ ${esc(tps(speed))}</span><span class="lw-speed-unit">${esc(L("токенов/с", "tokens/s"))}</span>
          <div class="lw-bar" role="img" aria-label="${esc(L("Скорость", "Speed"))}"><i style="width:${bar}%"></i></div></div>
        ${placeBadge(placement, isBest ? row.gpu_share : 0)}
        <dl class="lw-facts">
          <dt>${esc(L("Квант", "Quant"))}</dt><dd><select class="lw-quant" data-lw-quant="${esc(row.id)}" aria-label="${esc(L("Квантование", "Quantisation"))}">${options}</select></dd>
          <dt>${esc(L("Скачать", "Download"))}</dt><dd>${esc(gb(download))}</dd>
          <dt>${esc(L("Контекст", "Context"))}</dt><dd>${esc(ctx(context))} <span class="muted">/ ${esc(ctx(row.ctx_max))}</span></dd>
          <dt>${esc(L("Лицензия", "Licence"))}</dt><dd>${esc(row.license_label || row.license || "—")}</dd>
        </dl>
        <div class="lw-actions">${actions(row, inst)}</div>
        <a class="lw-hf" href="${esc(row.hf_url)}" target="_blank" rel="noopener">${esc(row.repo)} ↗</a>
      </aside>
    </article>`;
  }

  function actions(row, inst) {
    if (inst && inst.state === "starting") return `<button class="btn primary" disabled><span class="spin">◌</span> ${esc(L("Готовлю…", "Preparing…"))}</button>`;
    if (inst && inst.job) {
      const d = inst.job;
      const rt = inst.runtime || {};
      const engineRunning = rt.install && rt.install.state === "running";
      if (d.state === "downloading" || d.state === "queued" || engineRunning) {
        const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
        const label = d.state === "queued" ? L("В очереди…", "Queued…") : `${gb(d.done)} / ${gb(d.total)}${d.eta ? ` · ${eta(d.eta)}` : ""}`;
        return `<div class="lw-progress"><div class="row"><b class="grow">${esc(L("Скачиваю", "Downloading"))}</b><span class="muted">${esc(label)}</span></div>
          <div class="bar"><i style="width:${pct}%"></i></div>
          ${engineRunning ? `<span class="lw-hint">${esc(L("Параллельно ставлю движок llama.cpp…", "Installing the llama.cpp engine alongside…"))}</span>` : ""}
          <button class="btn small" data-lw="cancel">${esc(L("Отменить", "Cancel"))}</button></div>`;
      }
      if (d.state === "error") {
        return `<div class="lw-err">${esc(L(d.error, d.error_en || d.error))}</div><button class="btn small primary" data-lw="install">${esc(L("Повторить", "Retry"))}</button>`;
      }
      if (d.state === "cancelled") return `<button class="btn primary" data-lw="install">${ico("download")} ${esc(L("Установить", "Install"))}</button>`;
      if (d.state === "done") {
        const ready = rt.installed;
        const test = inst.test;
        return `<span class="badge ok">${esc(L("Установлена и настроена", "Installed and configured"))}</span>
          ${ready ? `<button class="btn primary" data-lw="try" ${test === "running" ? "disabled" : ""}>${test === "running" ? `<span class="spin">◌</span> ${esc(L("Запускаю…", "Starting…"))}` : `▶ ${esc(L("Запустить и проверить", "Start and try"))}`}</button>`
            : `<span class="lw-hint">${esc(rt.install && rt.install.state === "error" ? L(rt.install.error, rt.install.error_en || rt.install.error) : L("Нужен движок llama.cpp: вкладка «Локальные модели» → «Установить движок».", "The llama.cpp engine is needed: Local models tab → Install the engine."))}</span>`}
          ${test && test.text ? `<div class="lw-test selectable">${esc(test.text)}<span class="lw-hint">${esc(test.meta)}</span></div>` : ""}
          ${test && test.error ? `<div class="lw-err selectable">${esc(test.error)}</div>` : ""}
          <button class="btn small" data-lw="open-chat">${esc(L("Открыть в чате", "Open in chat"))}</button>`;
      }
    }
    if (row.installed) {
      return `<button class="btn primary" data-lw="try-installed">▶ ${esc(L("Запустить и проверить", "Start and try"))}</button>
        ${inst && inst.test && inst.test.text ? `<div class="lw-test selectable">${esc(inst.test.text)}<span class="lw-hint">${esc(inst.test.meta)}</span></div>` : ""}
        ${inst && inst.test && inst.test.error ? `<div class="lw-err selectable">${esc(inst.test.error)}</div>` : ""}`;
    }
    return `<button class="btn primary" data-lw="install">${ico("download")} ${esc(L("Установить и настроить", "Install and set up"))}</button>`;
  }

  function eta(seconds) {
    if (seconds < 60) return `${seconds} ${L("с", "s")}`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} ${L("мин", "min")}`;
    return `${num(seconds / 3600, 1)} ${L("ч", "h")}`;
  }

  function rerenderCard(id) {
    if (!alive() || S.step !== 3 || !S.result) return;
    const row = S.result.results.concat(S.result.more || []).find((r) => r.id === id);
    const el = document.querySelector(`.lw-card[data-id="${CSS.escape(id)}"]`);
    if (!row || !el) return;
    const focusId = document.activeElement && document.activeElement.closest(".lw-card") === el ? document.activeElement.dataset.lw : "";
    el.outerHTML = card(row);
    if (focusId) document.querySelector(`.lw-card[data-id="${CSS.escape(id)}"] [data-lw="${focusId}"]`)?.focus();
  }

  async function install(row) {
    const { quant, alt, isBest } = current(row);
    const preset = { ...(row.preset || {}) };
    preset.context = isBest ? row.context : (alt.context || row.context);
    S.installs[row.id] = { state: "starting" };
    rerenderCard(row.id);
    try {
      const r = await api("/api/admin/local/wizard/install", { method: "POST",
        body: { id: row.id, quant, context: preset.context, vision: S.goals.uses.includes("vision") || Boolean(row.caps && row.caps.vision && row.mmproj), mtp: S.goals.mtp, preset } });
      S.installs[row.id] = { job: r.download, runtime: r.runtime };
      if (r.engine && r.engine.error) toast(L(r.engine.error, r.engine.error_en || r.engine.error), "err");
      toast(L("Загрузка началась — можно закрыть мастер, она продолжится", "The download started — you can close the wizard, it continues"));
    } catch (error) {
      S.installs[row.id] = null;
      toast(errorOf(error), "err");
    }
    rerenderCard(row.id);
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(S.poll);
    const busy = Object.values(S.installs).some((i) => i && i.job && (i.job.state === "downloading" || i.job.state === "queued"
      || (i.runtime && i.runtime.install && i.runtime.install.state === "running")));
    if (!busy || !alive()) return;
    S.poll = setTimeout(poll, 1200);
  }

  async function poll() {
    if (!alive()) return;
    try {
      const st = await api("/api/admin/local/status");
      for (const [id, inst] of Object.entries(S.installs)) {
        if (!inst || !inst.job) continue;
        const job = (st.downloads || []).find((d) => d.id === inst.job.id);
        if (job) inst.job = job;
        inst.runtime = st.runtime;
        if (job && job.state === "done") {
          const row = S.result && S.result.results.concat(S.result.more || []).find((r) => r.id === id);
          if (row) { row.installed = true; row.installed_id = job.model; }
        }
        rerenderCard(id);
      }
      if (window.MemuLocal && $("#tab-local")?.classList.contains("active")) MemuLocal.load();
    } catch (error) {  }
    schedulePoll();
  }

  async function cancel(row) {
    const inst = S.installs[row.id];
    if (!inst || !inst.job) return;
    try { await api("/api/admin/local/download/cancel", { method: "POST", body: { id: inst.job.id } }); } catch (error) { toast(errorOf(error), "err"); }
    inst.job = { ...inst.job, state: "cancelled" };
    rerenderCard(row.id);
  }

  async function tryModel(row) {
    const inst = S.installs[row.id] || (S.installs[row.id] = {});
    const slug = (inst.job && inst.job.model) || row.installed_id;
    if (!slug) return;
    inst.test = "running";
    rerenderCard(row.id);
    try {
      const r = await api(`/api/admin/local/models/${encodeURIComponent(slug)}/test`, { method: "POST",
        body: { prompt: L("Привет! Представься одним предложением.", "Hi! Introduce yourself in one sentence.") } });
      const seconds = Math.max(0.001, ((r.ms || 0) - (r.ttft_ms || 0)) / 1000);
      const words = String(r.text || "").trim().split(/\s+/).filter(Boolean).length;
      inst.test = { text: r.text || L("(пустой ответ)", "(empty answer)"),
        meta: `${L("первый токен", "first token")} ${r.ttft_ms} ${L("мс", "ms")} · ${L("всего", "total")} ${num((r.ms || 0) / 1000, 1)} ${L("с", "s")}${words > 3 ? ` · ≈ ${num(words * 1.4 / seconds, 0)} ${L("ток/с", "tok/s")}` : ""}` };
    } catch (error) {
      inst.test = { error: errorOf(error) };
    }
    rerenderCard(row.id);
    if (window.MemuLocal && $("#tab-local")?.classList.contains("active")) MemuLocal.load();
  }

  function openChat(row) {
    const inst = S.installs[row.id] || {};
    const slug = (inst.job && inst.job.model) || row.installed_id;
    if (!slug) return;
    const id = `local-${slug}`;
    try {
      const prefs = JSON.parse(localStorage.getItem("oh_proxy_chat_prefs") || "{}") || {};
      prefs.model = id;
      localStorage.setItem("oh_proxy_chat_prefs", JSON.stringify(prefs));
    } catch (error) {  }
    closeModal();
    $('.nav-item[data-tab="chat"]')?.click();
    setTimeout(() => { const field = $("#pc-model"); if (field) { field.value = id; field.dispatchEvent(new Event("change")); } }, 300);
  }

  async function refreshCatalog() {
    try {
      await api("/api/admin/local/wizard/catalog/refresh", { method: "POST" });
      toast(L("Обновляю данные каталога с Hugging Face…", "Refreshing the catalogue data from Hugging Face…"));
      const wait = async () => {
        const st = await api("/api/admin/local/wizard/catalog/status");
        if (st.refresh.state === "running") return setTimeout(wait, 1500);
        if (st.refresh.state === "error") return toast(L(st.refresh.error, st.refresh.error_en), "err");
        toast(L("Данные каталога обновлены", "The catalogue data is up to date"));
        if (alive() && S.step === 3) recommend();
      };
      setTimeout(wait, 1500);
    } catch (error) { toast(errorOf(error), "err"); }
  }

  function onClick(event) {
    const button = event.target.closest("[data-lw]");
    if (!button || button.disabled) return;
    const action = button.dataset.lw;
    const id = button.closest(".lw-card")?.dataset.id || "";
    const row = id && S.result ? S.result.results.concat(S.result.more || []).find((r) => r.id === id) : null;
    switch (action) {
      case "goto": S.step = Number(button.dataset.step) || 0; return render();
      case "next": S.step = Math.min(2, S.step + 1); render(); return $("#lw-body")?.focus();
      case "back": S.step = Math.max(0, S.step - 1); return render();
      case "restart": S.step = 1; return render();
      case "recommend": return recommend();
      case "hw-refresh": S.edited = null; S.result = null; return loadHardware(true);
      case "hw-edit": S.editing = !S.editing; return render();
      case "hw-apply": return applyEdit();
      case "hw-reset": S.edited = null; S.editing = false; S.result = null; return render();
      case "use": {
        const use = button.dataset.use;
        const list = S.goals.uses;
        S.goals.uses = list.includes(use) ? list.filter((u) => u !== use) : list.concat(use);
        if (!S.goals.uses.length) S.goals.uses = ["chat"];
        S.result = null;
        return render();
      }
      case "seg": {
        const key = button.dataset.key;
        const raw = button.dataset.value;
        S.goals[key] = ["context", "keep_free_gb"].includes(key) ? Number(raw) : raw;
        S.result = null;
        return render();
      }
      case "install": return row && install(row);
      case "cancel": return row && cancel(row);
      case "try": case "try-installed": return row && tryModel(row);
      case "open-chat": return row && openChat(row);
      case "expand":
        if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id);
        return rerenderCard(id);
      case "show-more": S.showMore = true; return render();
      case "refresh-catalog": return refreshCatalog();
      default: return undefined;
    }
  }

  function onChange(event) {
    const el = event.target;
    if (el.id === "lw-mtp") { S.goals.mtp = el.checked; S.result = null; renderFoot(); return; }
    if (el.dataset.lwQuant) {
      S.picked[el.dataset.lwQuant] = el.value;
      rerenderCard(el.dataset.lwQuant);
      document.querySelector(`[data-lw-quant="${CSS.escape(el.dataset.lwQuant)}"]`)?.focus();
    }
  }

  function onInput(event) {
    if (event.target.id !== "lw-prio") return;
    S.goals.priority = Number(event.target.value) || 0;
    S.result = null;
    const p = S.goals.priority;
    const label = p <= 20 ? L("Максимум качества — пусть медленнее", "Maximum quality — slower is fine")
      : p <= 45 ? L("Баланс с уклоном в качество", "Balanced, leaning to quality")
      : p <= 65 ? L("Баланс с уклоном в скорость", "Balanced, leaning to speed") : L("Максимум скорости — ответы мгновенно", "Maximum speed — instant answers");
    $("#lw-prio-label").textContent = label;
    event.target.setAttribute("aria-valuetext", label);
  }

  return {
    open,

    relabel() { if (alive()) render(); },
  };
})();
