// MemuAI first run. A short welcome screen (name, avatar, look, workspace
// type and companion) is followed by a setup conversation: the model is
// picked and confirmed, the model itself asks a few questions, then the main
// settings are shown either on one screen or section by section, and the
// conversation ends with tips. Scripted messages never reach the model; the
// steps that need the model are written by it from short reports.
(function () {
  "use strict";
  if (window.MemuOnboarding) return;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : "ru") === "en";
  const L = (ru, english) => (en() ? english : ru);
  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => [...root.querySelectorAll(sel)];
  const h = (value) => esc(value == null ? "" : String(value));
  const ui = (name) => (typeof icon === "function" ? icon(name) : "");
  const P = (key, fallback) => (typeof pref === "function" ? pref(key, fallback) : fallback);
  const now = () => Date.now();
  const say = (text, type = "ok") => { try { toast(text, type); } catch (e) { /* toasts are optional */ } };
  const spaces = () => window.MemuSpaces || null;
  const agentSpace = () => !!(spaces() && spaces().isAgent());
  const companion = () => (agentSpace() ? spaces().companion() : null);
  // B-ONB-M4: localStorage throws when storage is blocked — guard the
  // force-flag read so a blocked storage can never break the wizard.
  const onbForced = () => { try { return localStorage.getItem("memu_onb_force") === "1"; } catch (_) { return false; } };
  const blocked = () => !!document.documentElement.dataset.moduleApp
    || (!!navigator.webdriver && !onbForced());
  const reduced = () => document.documentElement.dataset.motion === "off"
    || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  // A fresh profile is called «По умолчанию»: that is a placeholder, not the user's name.
  const PLACEHOLDER_NAMES = ["По умолчанию", "Профиль", "Default", "Profile"];
  const ownName = (value) => { const v = String(value || "").trim(); return PLACEHOLDER_NAMES.includes(v) ? "" : v; };
  function userName() {
    try { const p = currentProfile(); const v = ownName(p && p.name); if (v) return v; } catch (e) { /* profiles not ready */ }
    return ownName(P("profile_name", ""));
  }
  // Scripted lines speak as MemuAI (masculine in Russian) or as the companion.
  function gender() { const c = companion(); return c ? (c.gender || "f") : "m"; }
  const gv = (f, m, n) => { const g = gender(); return g === "f" ? f : g === "m" ? m : n; };
  // The companion talks like a friend; MemuAI in the standard space stays formal.
  const T = (formal, informal) => (agentSpace() ? informal : formal);
  function keyLabel(id, fallback) {
    try {
      const hk = window.MemuHotkeys;
      const value = hk && hk.value ? hk.value(id) : "";
      if (value) return hk.label(value);
      if (hk && hk.value) return "";
    } catch (e) { /* hotkeys not loaded */ }
    return fallback;
  }
  const kbd = (text) => String(text || "").split("+").map((part) => `<kbd>${h(part)}</kbd>`).join("<span class=\"onb-plus\">+</span>");

  // ── First-run questions ─────────────────────────────────────────────────
  // Tags steer which settings are shown later. Users may replace the
  // questions in Settings → General; developers may set
  // window.MEMUAI_ONBOARDING_SURVEY to an array in the same shape.
  const DEFAULT_SURVEY = Object.freeze([
    { id: "uses", type: "multi", other: true,
      q: { ru: "Для чего вам MemuAI?", en: "What will you use MemuAI for?" },
      hint: { ru: "Можно выбрать несколько", en: "Pick as many as you like" },
      options: [
        { id: "code", ru: "Программирование", en: "Coding", tags: ["code"] },
        { id: "automation", ru: "Автоматизация задач", en: "Automating tasks", tags: ["automation"] },
        { id: "chat", ru: "Общение и советы", en: "Talking and advice", tags: ["chat"] },
        { id: "writing", ru: "Тексты и переводы", en: "Writing and translation", tags: ["writing"] },
        { id: "research", ru: "Поиск и исследования", en: "Search and research", tags: ["research"] },
        { id: "study", ru: "Учёба", en: "Studying", tags: ["study"] },
        { id: "files", ru: "Документы и файлы", en: "Documents and files", tags: ["files"] },
      ] },
    { id: "media", type: "single",
      q: { ru: "Понадобятся модели для изображений или видео?", en: "Will you need image or video models?" },
      hint: { ru: "Создание картинок и роликов, распознавание того, что на них", en: "Creating pictures and clips, understanding what is in them" },
      options: [
        { id: "images", ru: "Да, для изображений", en: "Yes, for images", tags: ["images"] },
        { id: "both", ru: "Для изображений и видео", en: "Images and video", tags: ["images", "video"] },
        { id: "no", ru: "Нет", en: "No", tags: [] },
      ] },
    { id: "server", type: "single",
      q: { ru: "Есть свой сервер для MemuAI?", en: "Do you have your own server for MemuAI?" },
      hint: { ru: "Чтобы файлы, проекты и чаты жили на нём", en: "To keep files, projects and chats on it" },
      options: [
        { id: "yes", ru: "Да, есть", en: "Yes", tags: ["server"] },
        { id: "no", ru: "Нет", en: "No", tags: [] },
        { id: "unsure", ru: "Не знаю, что это", en: "Not sure what that is", tags: ["server_info"] },
      ] },
  ]);
  const pick = (value) => (value && typeof value === "object" ? String((en() ? value.en || value.ru : value.ru || value.en) || "") : String(value || ""));
  function defaultTagsFor(label) {
    const text = String(label || "").trim().toLowerCase();
    if (!text) return [];
    for (const question of DEFAULT_SURVEY) {
      for (const option of question.options) if (option.ru.toLowerCase() === text || option.en.toLowerCase() === text) return option.tags.slice();
    }
    return [];
  }
  function normQuestion(raw, index) {
    if (!raw || typeof raw !== "object") return null;
    const text = pick(raw.q).trim();
    if (!text) return null;
    const type = ["single", "multi", "text"].includes(raw.type) ? raw.type : "single";
    const options = type === "text" ? [] : (Array.isArray(raw.options) ? raw.options : []).map((option, i) => {
      const label = (option && typeof option === "object" ? pick(option.label || option) : String(option || "")).trim();
      if (!label) return null;
      const tags = option && typeof option === "object" && Array.isArray(option.tags) ? option.tags.map(String) : defaultTagsFor(label);
      return { id: String((option && option.id) || "o" + i).slice(0, 40), label: label.slice(0, 80), tags };
    }).filter(Boolean).slice(0, 10);
    if (type !== "text" && !options.length) return null;
    const id = String(raw.id || "").replace(/[^\w-]/g, "").slice(0, 40) || "q" + (index + 1);
    return { id, type, other: type !== "text" && !!raw.other, q: text.slice(0, 200), hint: pick(raw.hint).slice(0, 200), options };
  }
  function surveySource() {
    const dev = Array.isArray(window.MEMUAI_ONBOARDING_SURVEY) ? window.MEMUAI_ONBOARDING_SURVEY : null;
    const custom = P("onboarding_survey_custom", []);
    if (dev && dev.length) return dev;
    if (Array.isArray(custom) && custom.length) return custom;
    return DEFAULT_SURVEY;
  }
  function survey() {
    const seen = new Set();
    return surveySource().map(normQuestion).filter((row) => row && !seen.has(row.id) && seen.add(row.id)).slice(0, 8);
  }

  // ── Welcome screen ──────────────────────────────────────────────────────
  let wiz = null;
  const STEP_LABEL = { profile: ["Профиль", "Profile"], space: ["Рабочая область", "Workspace"], companion: ["Компаньон", "Companion"] };
  function wizSteps() {
    if (!spaces()) return ["profile"];
    return wiz && wiz.draft.space === "agent" ? ["profile", "space", "companion"] : ["profile", "space"];
  }
  function gradients() { return typeof GRADIENT_PRESETS !== "undefined" && Array.isArray(GRADIENT_PRESETS) ? GRADIENT_PRESETS : []; }
  // appearance.js keeps MemuAppearance as a plain global constant, not on window.
  const appearance = () => (typeof MemuAppearance !== "undefined" ? MemuAppearance : null);
  function catalog() { return (appearance() && appearance().catalog) || null; }

  function start(opts = {}) {
    if ((blocked() && !opts.force) || !document.body) return false;
    if (q("#memu-onb")) return true;
    let prof = {};
    try { prof = currentProfile() || {}; } catch (e) { prof = {}; }
    const grads = gradients();
    wiz = {
      step: 0,
      draft: {
        name: ownName(prof.name) || ownName(P("profile_name", "")),
        avatar: String(prof.avatar || P("user_avatar", "") || ""),
        gradient: String(prof.gradient || P("user_gradient", "") || (grads[1] && grads[1].val) || ""),
        space: opts.space || "",
        companion: spaces() && spaces().normCompanion ? spaces().normCompanion({}) : {},
        nameTouched: false,
      },
      form: null,
      busy: false,
    };
    const root = document.createElement("div");
    root.id = "memu-onb";
    root.className = "onb-back";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "onb-title");
    document.body.appendChild(root);
    document.body.classList.add("onb-open");
    q("#app")?.setAttribute("inert", "");
    root.addEventListener("click", onWizardClick);
    root.addEventListener("input", onWizardInput);
    root.addEventListener("change", onWizardChange);
    root.addEventListener("keydown", onWizardKey);
    document.addEventListener("focusin", keepFocus, true);
    renderWizard(true);
    if (appearance() && !catalog() && appearance().load) {
      appearance().load().then(() => { if (wiz && wizSteps()[wiz.step] === "profile") refreshLook(); }).catch(() => {});
    }
    requestAnimationFrame(() => root.classList.add("in"));
    return true;
  }

  function keepFocus(event) {
    const root = q("#memu-onb");
    if (!root || root.contains(event.target)) return;
    if (event.target.closest && event.target.closest("#panel-back, .mmp-pop, .toast-container, .sx-pop, .dd-pop")) return;
    const target = q("[data-onb-w='next']", root) || root;
    try { target.focus({ preventScroll: true }); } catch (e) { /* element gone */ }
  }

  function closeWizard() {
    const root = q("#memu-onb");
    document.removeEventListener("focusin", keepFocus, true);
    document.body.classList.remove("onb-open");
    if (!q("#panel-back.visible")) q("#app")?.removeAttribute("inert");
    wiz = null;
    if (!root) return;
    if (reduced()) { root.remove(); return; }
    root.classList.remove("in");
    root.classList.add("out");
    setTimeout(() => root.remove(), 260);
  }

  function avatarInner(d) {
    if (d.avatar) return `<img src="${h(d.avatar)}" alt="">`;
    return `<span>${h((String(d.name || "").trim()[0] || "M").toUpperCase())}</span>`;
  }
  function accentsHtml() {
    const list = (catalog() && catalog().accents) || [];
    if (!list.length) return `<span class="onb-muted">${h(L("Загружаю цвета…", "Loading colours…"))}</span>`;
    const current = String(P("accent_color", "terracotta") || "terracotta");
    return list.map((row) => `<button type="button" class="onb-accent${row.id === current ? " on" : ""}" data-onb-accent="${h(row.id)}" role="radio" aria-checked="${row.id === current}"
        title="${h(L(row.ru || row.id, row.en || row.id))}" aria-label="${h(L(row.ru || row.id, row.en || row.id))}" style="--sw-l:${h(row.light)};--sw-d:${h(row.dark)}"><span></span></button>`).join("");
  }
  function designsHtml() {
    const list = (catalog() && Array.isArray(catalog().designs) && catalog().designs) || [];
    if (!list.length) return "";
    const current = String(P("ui_design", "classic") || "classic");
    return list.map((row) => `<button type="button" class="onb-design${row.id === current ? " on" : ""}" data-onb-design="${h(row.id)}" role="radio" aria-checked="${row.id === current}" title="${h(L(row.note_ru || "", row.note_en || ""))}">
        <span class="design-mini design-mini-${h(row.id)}" aria-hidden="true"><i class="dm-side"></i><i class="dm-top"></i><i class="dm-msg a"></i><i class="dm-msg b"></i><i class="dm-msg c"></i><i class="dm-input"></i></span>
        <span class="onb-design-name">${h(L(row.ru || row.id, row.en || row.id))}</span></button>`).join("");
  }
  function designName() {
    const id = String(P("ui_design", "classic") || "classic");
    const row = ((catalog() && catalog().designs) || []).find((item) => item.id === id);
    return row ? L(row.ru || row.id, row.en || row.id) : L("Классический", "Classic");
  }
  function previewHtml(d) {
    const name = String(d.name || "").trim();
    return `<div class="onb-pv">
        <div class="onb-pv-top"><i></i><i></i><i></i><b>MemuAI</b></div>
        <div class="onb-pv-chat">
          <div class="onb-pv-msg ai"><span class="onb-pv-ava ai">M</span><span class="onb-pv-bubble" data-onb-pv-hello>${h(name ? L(`Здравствуйте, ${name}! С чего начнём?`, `Hi ${name}! Where shall we start?`) : L("Здравствуйте! С чего начнём?", "Hi! Where shall we start?"))}</span></div>
          <div class="onb-pv-msg me"><span class="onb-pv-bubble">${h(L("Помоги разобрать почту", "Help me sort out my inbox"))}</span><span class="onb-pv-ava me" data-onb-pv-me style="background:${h(d.gradient)}">${avatarInner(d)}</span></div>
          <div class="onb-pv-msg ai"><span class="onb-pv-ava ai">M</span><span class="onb-pv-bubble onb-pv-lines"><i></i><i></i><i></i></span></div>
        </div>
        <div class="onb-pv-input"><span>${h(L("Сообщение…", "Message…"))}</span><b>${ui("send")}</b></div>
      </div>
      <p class="onb-pv-note">${h(L("Так будет выглядеть MemuAI. Тему, цвета и дизайн можно менять когда угодно.", "This is how MemuAI will look. Theme, colours and design can be changed any time."))}</p>`;
  }

  function profileStepHtml() {
    const d = wiz.draft;
    const theme = (typeof S !== "undefined" && S.theme) || document.documentElement.dataset.theme || "light";
    const grads = gradients();
    return `<div class="onb-grid">
      <section class="onb-main">
        <h1 class="onb-title" id="onb-title">${h(L("Добро пожаловать в MemuAI", "Welcome to MemuAI"))}</h1>
        <p class="onb-lead">${h(L("Пара шагов — и можно начинать. Всё это потом меняется в настройках.", "A couple of steps and you are in. Everything here can be changed later in Settings."))}</p>
        <label class="onb-field"><span class="onb-label">${h(L("Как к вам обращаться?", "What should MemuAI call you?"))}</span>
          <input class="pinput onb-name" id="onb-name" maxlength="80" autocomplete="nickname" spellcheck="false" value="${h(d.name)}" placeholder="${h(L("Имя или никнейм", "Name or nickname"))}" aria-describedby="onb-name-err" aria-required="true">
          <small class="onb-err" id="onb-name-err" role="alert" hidden>${h(L("Введите имя — так MemuAI будет к вам обращаться", "Enter a name — MemuAI will use it to address you"))}</small></label>
        <div class="onb-field"><span class="onb-label">${h(L("Аватар", "Avatar"))}<small>${h(L("необязательно", "optional"))}</small></span>
          <div class="onb-ava-row">
            <span class="onb-ava" id="onb-ava" style="background:${h(d.gradient)}">${avatarInner(d)}</span>
            <div class="onb-ava-tools">
              <div class="onb-grads" role="radiogroup" aria-label="${h(L("Цвет аватара", "Avatar colour"))}">${grads.map((g) => `<button type="button" class="onb-grad${!d.avatar && d.gradient === g.val ? " on" : ""}" data-onb-grad="${h(g.id)}" style="background:${h(g.val)}" role="radio" aria-checked="${!d.avatar && d.gradient === g.val}" aria-label="${h(g.name)}" title="${h(g.name)}"></button>`).join("")}</div>
              <div class="onb-ava-btns">
                <button type="button" class="pbtn ghost onb-small" data-onb-w="photo">${ui("camera")}<span class="onb-photo-label">${h(d.avatar ? L("Другое фото", "Another photo") : L("Загрузить фото", "Upload a photo"))}</span></button>
                <button type="button" class="pbtn ghost onb-small" data-onb-w="photo-clear"${d.avatar ? "" : " hidden"}>${ui("close")}<span>${h(L("Убрать", "Remove"))}</span></button>
                <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" id="onb-photo" hidden>
              </div>
            </div>
          </div></div>
        <div class="onb-field"><span class="onb-label">${h(L("Тема", "Theme"))}</span>
          <div class="onb-themes" role="radiogroup" aria-label="${h(L("Тема", "Theme"))}">${["light", "dark"].map((mode) => `<button type="button" class="onb-theme${theme === mode ? " on" : ""}" data-onb-theme="${mode}" role="radio" aria-checked="${theme === mode}">
              <span class="theme-mini theme-mini-${mode}" aria-hidden="true"><i class="theme-mini-side"></i><i class="theme-mini-line"></i><i class="theme-mini-bubble"></i><i class="theme-mini-input"></i></span>
              <span class="onb-theme-name">${ui(mode === "dark" ? "moon" : "sun")}<span>${h(mode === "dark" ? L("Тёмная", "Dark") : L("Светлая", "Light"))}</span></span></button>`).join("")}</div></div>
        <div class="onb-field"><span class="onb-label">${h(L("Акцентный цвет", "Accent colour"))}</span>
          <div class="onb-accents" id="onb-accents" role="radiogroup" aria-label="${h(L("Акцентный цвет", "Accent colour"))}">${accentsHtml()}</div></div>
        <details class="onb-more" id="onb-design-more"${wiz.designOpen ? " open" : ""}><summary>${ui("palette")}<span>${h(L("Дизайн интерфейса", "Interface design"))}</span><small id="onb-design-name">${h(designName())}</small></summary>
          <div class="onb-designs" id="onb-designs" role="radiogroup" aria-label="${h(L("Дизайн интерфейса", "Interface design"))}">${designsHtml()}</div></details>
      </section>
      <aside class="onb-side" aria-hidden="true">${previewHtml(d)}</aside>
    </div>`;
  }

  function spaceStepHtml() {
    return `<div class="onb-narrow">
      <h1 class="onb-title" id="onb-title">${h(L("Как вы хотите работать?", "How would you like to work?"))}</h1>
      <p class="onb-lead">${h(L("Выберите рабочую область. У каждой области свои чаты и память. Сменить её или создать ещё одну можно в любой момент — кнопкой рядом с логотипом или в «Настройки → Рабочие области».",
        "Choose a workspace. Each one has its own chats and memory. You can switch or create another one any time — with the button next to the logo or in Settings → Workspaces."))}</p>
      <div class="sp-type-cards onb-types" role="radiogroup" aria-label="${h(L("Тип рабочей области", "Workspace type"))}">${spaces().typeCardsHtml(wiz.draft.space)}</div>
      <p class="onb-note">${ui("info")}<span>${h(L("Не уверены? Начните со стандартной: агентную можно добавить позже, ничего не потеряв.", "Not sure? Start with Standard: you can add an agentic one later without losing anything."))}</span></p>
    </div>`;
  }

  function companionStepHtml() {
    return `<div class="onb-narrow onb-comp">
      <h1 class="onb-title" id="onb-title">${h(L("Знакомьтесь — ваш компаньон", "Meet your companion"))}</h1>
      <p class="onb-lead">${h(L("Придумайте, каким он будет: имя, внешность, характер и манеру общения. Всё это можно поменять и перед первым сообщением, и потом.",
        "Shape who they will be: name, looks, character and manner. All of it can be changed before the first message and later."))}</p>
      ${spaces().companionFormHtml(wiz.draft.companion, { compact: true })}
    </div>`;
  }

  function renderWizard(first = false) {
    const root = q("#memu-onb");
    if (!root || !wiz) return;
    const steps = wizSteps();
    wiz.step = Math.max(0, Math.min(wiz.step, steps.length - 1));
    const id = steps[wiz.step];
    const last = wiz.step === steps.length - 1;
    const body = id === "profile" ? profileStepHtml() : id === "space" ? spaceStepHtml() : companionStepHtml();
    const nextDisabled = id === "space" && !wiz.draft.space;
    root.innerHTML = `<div class="onb-shell" data-step="${id}">
      <header class="onb-head">
        <div class="onb-brand"><span class="onb-mark" aria-hidden="true">M</span><span>MemuAI</span></div>
        <ol class="onb-steps" aria-label="${h(L("Шаги", "Steps"))}">${steps.map((step, i) => `<li class="${i < wiz.step ? "done" : i === wiz.step ? "on" : ""}"${i === wiz.step ? ' aria-current="step"' : ""}>
            <span class="onb-step-n">${i < wiz.step ? ui("check") : i + 1}</span><span class="onb-step-t">${h(L(STEP_LABEL[step][0], STEP_LABEL[step][1]))}</span></li>`).join("")}</ol>
        <div class="onb-lang" role="group" aria-label="${h(L("Язык", "Language"))}">
          <button type="button" data-onb-lang="ru" aria-pressed="${!en()}" class="${en() ? "" : "on"}">RU</button><button type="button" data-onb-lang="en" aria-pressed="${en()}" class="${en() ? "on" : ""}">EN</button></div>
      </header>
      <div class="onb-body${first ? "" : " onb-swap"}" id="onb-body">${body}</div>
      <footer class="onb-foot">
        <span class="onb-foot-note">${h(L(`Шаг ${wiz.step + 1} из ${steps.length}`, `Step ${wiz.step + 1} of ${steps.length}`))}</span>
        <span class="onb-grow"></span>
        ${wiz.step > 0 ? `<button type="button" class="pbtn ghost" data-onb-w="back">${ui("back")}<span>${h(L("Назад", "Back"))}</span></button>` : ""}
        <button type="button" class="pbtn ghost" data-onb-w="skip" title="${h(L("Перейти в приложение без настройки", "Go to the app without setup"))}"><span>${h(L("Пропустить знакомство", "Skip the intro"))}</span></button>
        <button type="button" class="pbtn onb-next" data-onb-w="next"${nextDisabled ? " disabled" : ""}${nextDisabled ? ` title="${h(L("Выберите вариант", "Choose an option"))}"` : ""}>
          <span>${h(last ? L("Начать", "Get started") : L("Далее", "Next"))}</span>${ui("forward")}</button>
      </footer>
    </div>`;
    if (id === "companion") {
      wiz.form = spaces().bindCompanionForm(q("#onb-body", root), wiz.draft.companion, (value) => { wiz.draft.companion = value; });
    }
    requestAnimationFrame(() => {
      if (id === "profile") { const input = q("#onb-name", root); if (input && (first || !input.value)) input.focus({ preventScroll: true }); }
      else q(id === "space" ? ".sp-type-card.on, .sp-type-card" : ".cf input[data-cf='name']", root)?.focus({ preventScroll: true });
    });
  }

  function refreshLook() {
    const root = q("#memu-onb");
    if (!root || !wiz) return;
    const theme = (typeof S !== "undefined" && S.theme) || document.documentElement.dataset.theme;
    qa("[data-onb-theme]", root).forEach((b) => { const on = b.dataset.onbTheme === theme; b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on)); });
    const accents = q("#onb-accents", root);
    if (accents) accents.innerHTML = accentsHtml();
    const designs = q("#onb-designs", root);
    if (designs) designs.innerHTML = designsHtml();
    const name = q("#onb-design-name", root);
    if (name) name.textContent = designName();
  }
  function refreshAvatar() {
    const root = q("#memu-onb");
    if (!root || !wiz) return;
    const d = wiz.draft;
    const ava = q("#onb-ava", root);
    if (ava) { ava.style.background = d.gradient; ava.innerHTML = avatarInner(d); }
    const me = q("[data-onb-pv-me]", root);
    if (me) { me.style.background = d.gradient; me.innerHTML = avatarInner(d); }
    qa("[data-onb-grad]", root).forEach((b) => {
      const row = gradients().find((g) => g.id === b.dataset.onbGrad);
      const on = !d.avatar && !!row && row.val === d.gradient;
      b.classList.toggle("on", on); b.setAttribute("aria-checked", String(on));
    });
    const photo = q("[data-onb-w='photo'] .onb-photo-label", root);
    if (photo) photo.textContent = d.avatar ? L("Другое фото", "Another photo") : L("Загрузить фото", "Upload a photo");
    const clear = q("[data-onb-w='photo-clear']", root);
    if (clear) clear.hidden = !d.avatar;
    const hello = q("[data-onb-pv-hello]", root);
    const name = String(d.name || "").trim();
    if (hello) hello.textContent = name ? L(`Здравствуйте, ${name}! С чего начнём?`, `Hi ${name}! Where shall we start?`) : L("Здравствуйте! С чего начнём?", "Hi! Where shall we start?");
  }

  function setLanguage(value) {
    if (value === (en() ? "en" : "ru")) return;
    const c = wiz && wiz.draft.companion;
    if (c && !wiz.draft.companionTouched) c.name = value === "en" ? (c.name === "Мира" ? "Mira" : c.name) : (c.name === "Mira" ? "Мира" : c.name);
    PG_LANG = value;
    try { localStorage.setItem("memu_lang", value); } catch (e) { /* private mode */ }
    if (typeof savePreferences === "function") savePreferences({ language: value });
    try { if (typeof pgApplyLang === "function") pgApplyLang(); if (typeof renderBottomProfile === "function") renderBottomProfile(); } catch (e) { /* static UI not ready */ }
    renderWizard();
  }

  // A small cropper: drag the picture, zoom with the slider; the circle is
  // what becomes the avatar.
  const CROP = 232;
  function openCrop(file) {
    if (!file || !/^image\//.test(file.type || "")) { say(L("Выберите изображение", "Choose an image"), "warn"); return; }
    if (file.size > 25 * 1024 * 1024) { say(L("Файл слишком большой", "The file is too large"), "warn"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        if (!wiz) return;
        wiz.crop = { img, src: String(reader.result || ""), zoom: 1, x: 0, y: 0 };
        renderCrop();
      };
      img.onerror = () => say(L("Не удалось открыть изображение", "Could not open the image"), "err");
      img.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  }
  function cropLayout() {
    const c = wiz && wiz.crop;
    if (!c) return null;
    const scale = Math.max(CROP / c.img.naturalWidth, CROP / c.img.naturalHeight) * c.zoom;
    const width = c.img.naturalWidth * scale, height = c.img.naturalHeight * scale;
    const maxX = Math.max(0, (width - CROP) / 2), maxY = Math.max(0, (height - CROP) / 2);
    c.x = Math.max(-maxX, Math.min(maxX, c.x));
    c.y = Math.max(-maxY, Math.min(maxY, c.y));
    return { scale, width, height, left: (CROP - width) / 2 + c.x, top: (CROP - height) / 2 + c.y };
  }
  function paintCrop() {
    const img = q("#onb-crop-img");
    const box = cropLayout();
    if (!img || !box) return;
    Object.assign(img.style, { width: box.width + "px", height: box.height + "px", left: box.left + "px", top: box.top + "px" });
  }
  function renderCrop() {
    const shell = q("#memu-onb .onb-shell");
    if (!shell || !wiz || !wiz.crop) return;
    q(".onb-crop", shell)?.remove();
    const box = document.createElement("div");
    box.className = "onb-crop";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", L("Область аватара", "Avatar area"));
    box.innerHTML = `<div class="onb-crop-card">
        <b class="onb-crop-title">${h(L("Выберите область аватара", "Choose your avatar area"))}</b>
        <small class="onb-muted">${h(L("Перетащите изображение и настройте масштаб", "Drag the picture and adjust the zoom"))}</small>
        <div class="onb-crop-stage" id="onb-crop-stage" style="--crop:${CROP}px"><img id="onb-crop-img" src="${h(wiz.crop.src)}" alt="" draggable="false"><span class="onb-crop-mask"></span></div>
        <label class="onb-crop-zoom"><span>${h(L("Масштаб", "Zoom"))}</span><input type="range" id="onb-crop-zoom" min="1" max="3" step="0.01" value="${wiz.crop.zoom}"></label>
        <div class="onb-crop-btns"><button type="button" class="pbtn ghost" data-onb-w="crop-cancel">${h(L("Отмена", "Cancel"))}</button>
          <button type="button" class="pbtn" data-onb-w="crop-save">${ui("check")}<span>${h(L("Готово", "Done"))}</span></button></div>
      </div>`;
    shell.appendChild(box);
    paintCrop();
    const stage = q("#onb-crop-stage", box);
    let drag = null;
    stage.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY, ox: wiz.crop.x, oy: wiz.crop.y };
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("drag");
    });
    stage.addEventListener("pointermove", (event) => {
      if (!drag || !wiz || !wiz.crop) return;
      wiz.crop.x = drag.ox + event.clientX - drag.x;
      wiz.crop.y = drag.oy + event.clientY - drag.y;
      paintCrop();
    });
    const stop = () => { drag = null; stage.classList.remove("drag"); };
    stage.addEventListener("pointerup", stop);
    stage.addEventListener("pointercancel", stop);
    stage.addEventListener("wheel", (event) => {
      event.preventDefault();
      if (!wiz || !wiz.crop) return;
      wiz.crop.zoom = Math.max(1, Math.min(3, wiz.crop.zoom - event.deltaY * 0.0015));
      const zoom = q("#onb-crop-zoom", box);
      if (zoom) zoom.value = String(wiz.crop.zoom);
      paintCrop();
    }, { passive: false });
    q("#onb-crop-zoom", box).addEventListener("input", (event) => { if (wiz && wiz.crop) { wiz.crop.zoom = +event.target.value || 1; paintCrop(); } });
    requestAnimationFrame(() => q("[data-onb-w='crop-save']", box)?.focus({ preventScroll: true }));
  }
  function closeCrop(saveIt) {
    const c = wiz && wiz.crop;
    if (saveIt && c) {
      try {
        const box = cropLayout();
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 256;
        const ctx = canvas.getContext("2d");
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(c.img, -box.left / box.scale, -box.top / box.scale, CROP / box.scale, CROP / box.scale, 0, 0, 256, 256);
        wiz.draft.avatar = canvas.toDataURL("image/jpeg", 0.9);
        refreshAvatar();
      } catch (error) { say(error.message || String(error), "err"); }
    }
    if (wiz) wiz.crop = null;
    q("#memu-onb .onb-crop")?.remove();
    q("#memu-onb [data-onb-w='photo']")?.focus({ preventScroll: true });
  }

  function validName() {
    const root = q("#memu-onb");
    const input = q("#onb-name", root);
    const value = String((input ? input.value : wiz.draft.name) || "").trim();
    wiz.draft.name = value;
    const error = q("#onb-name-err", root);
    if (value) { if (error) error.hidden = true; input?.removeAttribute("aria-invalid"); return true; }
    if (error) error.hidden = false;
    if (input) {
      input.setAttribute("aria-invalid", "true");
      input.focus();
      input.classList.remove("onb-shake");
      void input.offsetWidth;
      input.classList.add("onb-shake");
    }
    return false;
  }

  function wizardNext() {
    if (!wiz || wiz.busy) return;
    const steps = wizSteps();
    const id = steps[wiz.step];
    if (id === "profile" && !validName()) return;
    if (id === "space" && !wiz.draft.space) return;
    if (wiz.step < steps.length - 1) { wiz.step += 1; renderWizard(); return; }
    finishWizard();
  }
  function wizardBack() {
    if (!wiz || wiz.busy || wiz.step === 0) return;
    wiz.step -= 1;
    renderWizard();
  }

  // B-ONB-H2: the first-run wizard could not be skipped or closed — it
  // reappeared on every load. Skip keeps the default profile in place,
  // marks onboarding done (so it never loops back), and goes straight to
  // the app without starting the setup chat.
  async function wizardSkip() {
    if (!wiz || wiz.busy) return;
    wiz.busy = true;
    try {
      const d = wiz.draft;
      let currentName = "";
      try { currentName = String((currentProfile() || {}).name || "").trim(); } catch (e) { /* profiles not ready */ }
      const name = String(d.name || "").trim() || userName() || currentName || "По умолчанию";
      if (typeof savePreferences === "function") {
        savePreferences({ profile_name: name, profile_onboarded: true, onboarding_stage: "done", onboarding_v5_done: true });
      }
      if (typeof updateCurrentProfile === "function") { try { await updateCurrentProfile({ name }); } catch (e) { /* profile not ready */ } }
      if (typeof flushPreferences === "function") { try { await flushPreferences(); } catch (e) { /* saved on next sync */ } }
    } finally {
      closeWizard();
    }
  }

  function onWizardClick(event) {
    if (!wiz) return;
    const t = event.target;
    const lang = t.closest("[data-onb-lang]");
    if (lang) { setLanguage(lang.dataset.onbLang); return; }
    const action = t.closest("[data-onb-w]");
    if (action) {
      const kind = action.dataset.onbW;
      if (kind === "next") wizardNext();
      else if (kind === "back") wizardBack();
      else if (kind === "skip") wizardSkip();
      else if (kind === "photo") q("#onb-photo")?.click();
      else if (kind === "photo-clear") { wiz.draft.avatar = ""; refreshAvatar(); }
      else if (kind === "crop-save") closeCrop(true);
      else if (kind === "crop-cancel") closeCrop(false);
      return;
    }
    const grad = t.closest("[data-onb-grad]");
    if (grad) {
      const row = gradients().find((g) => g.id === grad.dataset.onbGrad);
      if (row) { wiz.draft.gradient = row.val; wiz.draft.avatar = ""; refreshAvatar(); }
      return;
    }
    const theme = t.closest("[data-onb-theme]");
    if (theme) {
      if (typeof setTheme === "function" && S.theme !== theme.dataset.onbTheme) setTheme(theme.dataset.onbTheme);
      setTimeout(refreshLook, 30);
      return;
    }
    const accent = t.closest("[data-onb-accent]");
    if (accent) {
      if (typeof savePreferences === "function") savePreferences({ accent_color: accent.dataset.onbAccent });
      if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
      refreshLook();
      return;
    }
    const design = t.closest("[data-onb-design]");
    if (design) {
      if (typeof savePreferences === "function") savePreferences({ ui_design: design.dataset.onbDesign });
      if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
      refreshLook();
      return;
    }
    const type = t.closest("[data-sp-type]");
    if (type) {
      wiz.draft.space = type.dataset.spType === "agent" ? "agent" : "standard";
      renderWizard();
    }
  }
  function onWizardInput(event) {
    if (!wiz) return;
    if (event.target.id === "onb-name") {
      wiz.draft.name = event.target.value;
      if (event.target.value.trim()) { const error = q("#onb-name-err"); if (error) error.hidden = true; event.target.removeAttribute("aria-invalid"); }
      refreshAvatar();
    } else if (event.target.closest("[data-cf]")) wiz.draft.companionTouched = true;
  }
  function onWizardChange(event) {
    if (!wiz) return;
    if (event.target.id === "onb-photo") {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (file) openCrop(file);
    }
    if (event.target.closest("#onb-design-more")) wiz.designOpen = q("#onb-design-more")?.open;
  }
  function onWizardKey(event) {
    if (!wiz) return;
    if (wiz.crop && (event.key === "Escape" || (event.key === "Enter" && !event.target.closest("button")))) { event.preventDefault(); closeCrop(event.key === "Enter"); return; }
    // B-ONB-H2: Escape skips the wizard (same as the "Skip the intro" button).
    if (event.key === "Escape") { event.preventDefault(); wizardSkip(); return; }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.target.matches("#onb-name, .cf input[data-cf='name']")) {
      event.preventDefault();
      wizardNext();
    }
  }

  async function finishWizard() {
    if (!wiz || wiz.busy) return;
    const d = wiz.draft;
    const button = q("#memu-onb [data-onb-w='next']");
    wiz.busy = true;
    if (button) { button.disabled = true; button.classList.add("busy"); }
    try {
      const name = String(d.name || "").trim() || userName();
      if (typeof savePreferences === "function") {
        savePreferences({ profile_name: name, profile_onboarded: true, user_avatar: d.avatar || "", user_gradient: d.gradient || "", onboarding_stage: "setup", onboarding_v5_done: false });
      }
      if (typeof updateCurrentProfile === "function") await updateCurrentProfile({ name, avatar: d.avatar || "", gradient: d.gradient || "" });
      try { if (typeof renderChips === "function") renderChips(); } catch (e) { /* composer not ready */ }
      if (d.space === "agent" && spaces()) {
        const body = spaces().companionPayload(d.companion || {});
        if (spaces().isAgent()) {
          await spaces().patchActive({ companion: body });
          closeWizard();
          await beginSetup();
          return;
        }
        const res = await spaces().createSpace({ type: "agent", name: "", companion: body, activate: false });
        const id = res && res.space && res.space.id;
        if (!id) throw new Error(L("Не удалось создать рабочую область", "Could not create the workspace"));
        if (typeof flushPreferences === "function") { try { await flushPreferences(); } catch (e) { /* saved on activation */ } }
        // B-ONB-H1: switchTo() swallows the error (toast) and returns false
        // on failure; without this check the wizard stayed open with its
        // button disabled forever (success reloads the page instead).
        if (!(await spaces().switchTo(id, { force: true, prefs: { onboarding_stage: "setup" } }))) {
          if (wiz) wiz.busy = false;
          if (button) { button.disabled = false; button.classList.remove("busy"); }
        }
        return;
      }
      if (d.space === "standard" && spaces() && spaces().isAgent()) {
        if (!(await spaces().switchTo("standard", { force: true, prefs: { onboarding_stage: "setup" } }))) {
          if (wiz) wiz.busy = false;
          if (button) { button.disabled = false; button.classList.remove("busy"); }
        }
        return;
      }
      closeWizard();
      await beginSetup();
    } catch (error) {
      if (wiz) wiz.busy = false;
      if (button) { button.disabled = false; button.classList.remove("busy"); }
      say(error.message || String(error), "err");
    }
  }

  // ── Setup conversation ─────────────────────────────────────────────────
  const REPORT_TAG = "memuai-setup_report";
  let watchTimer = 0;
  let watchSig = "";
  let starting = false;
  let proxyUrl = "";

  const stage = () => String(P("onboarding_stage", "") || "");
  function setPrefs(patch) { if (typeof savePreferences === "function") savePreferences(patch); }
  function setupChatId() { return agentSpace() ? spaces().globalId() : String(P("onboarding_chat", "") || ""); }
  function inSetupChat() { const id = setupChatId(); return !!id && typeof S !== "undefined" && S.currentId === id; }
  function chatExists(id) { return !!id && (S.chats || []).some((c) => c.id === id); }
  function busyHere() { return typeof generationJobs !== "undefined" && generationJobs.has(S.currentId); }
  function persist() { try { if (typeof persistChat === "function") persistChat(); } catch (e) { /* saved with the next change */ } }
  function memoryOn() { return typeof S !== "undefined" && !!S.memory && S.memory.enabled !== false; }
  function pageExists(id) { try { return typeof settingsPages === "function" && settingsPages().some((p) => p.id === id); } catch (e) { return false; } }
  function pageInfo(id) {
    try { const row = typeof settingsPages === "function" ? settingsPages().find((p) => p.id === id) : null; if (row) return row; } catch (e) { /* settings not ready */ }
    return { id, title: id, desc: "", icon: ui("settings") };
  }
  function openPage(id) { try { if (typeof openSettingsPage === "function") openSettingsPage(id); } catch (e) { say(e.message || String(e), "err"); } }
  function textModels() {
    try {
      const list = typeof visibleModels === "function" ? visibleModels() : (S.models || []);
      return list.filter((row) => row && row.id && (!window.MemuModelPicker || MemuModelPicker.kindOf(row) === "chat"));
    } catch (e) { return []; }
  }
  function modelName(id) {
    if (!id) return L("не выбрана", "not chosen");
    try { return typeof modelDisplayName === "function" ? modelDisplayName(id) : id; } catch (e) { return id; }
  }
  function hubState() { return (q("#hub-status") && q("#hub-status").dataset.state) || "offline"; }
  const pickerOpen = () => !!(window.MemuModelPicker && MemuModelPicker.isOpen && MemuModelPicker.isOpen());

  function scripted(kind, content, extra = {}) {
    return { role: "assistant", content, localOnly: true, createdAt: now(), setupCard: { kind, state: "pending", ...extra } };
  }
  function lastCardIndex() {
    const list = (typeof S !== "undefined" && S.messages) || [];
    for (let i = list.length - 1; i >= 0; i -= 1) if (list[i] && list[i].setupCard) return i;
    return -1;
  }
  function rerender() { if (!pickerOpen() && typeof renderMessages === "function") renderMessages(); }

  async function openSetupChat(fresh) {
    if (agentSpace()) {
      await spaces().openHome();
      return S.currentId === spaces().globalId();
    }
    const id = String(P("onboarding_chat", "") || "");
    if (!fresh && chatExists(id)) {
      if (S.currentId !== id) await openChat(id);
      return S.currentId === id;
    }
    if (S.mode !== "chat") S.mode = "chat";
    if (typeof startDraft !== "function" || startDraft(false, true) === false) return false;
    if (typeof updateWorkUI === "function") { try { updateWorkUI(); } catch (e) { /* static UI */ } }
    return "new";
  }

  async function beginSetup(opts = {}) {
    if (starting || typeof S === "undefined") return false;
    starting = true;
    try {
      if (stage() !== "setup") setPrefs({ onboarding_stage: "setup" });
      const opened = await openSetupChat(!!opts.fresh);
      if (!opened) return false;
      const last = lastCardIndex();
      const pending = last >= 0 && S.messages[last].setupCard.state === "pending" && !opts.fresh;
      if (!pending && (opened === "new" || opts.fresh || last < 0 || S.messages[last].setupCard.kind === "done")) {
        S.messages.push(scripted("model", modelText()));
        persist();
        if (!agentSpace()) {
          const chat = typeof currentChat === "function" ? currentChat() : null;
          if (chat) { chat.title = L("Знакомство с MemuAI", "Getting started"); chat.titleManual = true; chat.updated = now(); }
          setPrefs({ onboarding_chat: S.currentId });
          if (typeof saveLocalChats === "function") saveLocalChats();
          if (typeof renderChats === "function") renderChats();
        }
      }
      rerender();
      watch();
      requestAnimationFrame(() => { const box = q("#messages")?.parentElement; if (box) box.scrollTop = box.scrollHeight; });
      return true;
    } finally {
      starting = false;
    }
  }

  function modelText() {
    const name = userName();
    const c = companion();
    const keys = keyLabel("models", "Ctrl+M");
    if (c) {
      return L(`${name ? name + ", привет" : "Привет"}! Я ${c.name}. Чтобы я ${gv("могла", "мог", "мог(ла)")} отвечать, выбери модель ИИ — на ней я буду думать и разговаривать. Поменять её можно когда угодно.`,
        `${name ? "Hi " + name : "Hi"}! I'm ${c.name}. To be able to answer, I need an AI model — pick the one I'll think and talk with. You can change it any time.`);
    }
    return L(`Здравствуйте${name ? ", " + name : ""}! Я MemuAI. Чтобы начать, выберите модель ИИ — на ней я буду отвечать. Сменить её можно в любой момент: кнопкой модели над полем ввода${keys ? " или " + keys : ""}.`,
      `Hello${name ? ", " + name : ""}! I'm MemuAI. To begin, choose an AI model — it is what I will answer with. You can switch it any time with the model button above the composer${keys ? " or " + keys : ""}.`);
  }

  // Watches the model list while the first card waits for a model, so a key
  // added in MemuAI Proxy shows up without reloading.
  function watch() {
    clearInterval(watchTimer);
    watchTimer = setInterval(() => {
      const i = lastCardIndex();
      const card = i >= 0 ? S.messages[i].setupCard : null;
      if (!card || card.kind !== "model" || card.state !== "pending") { clearInterval(watchTimer); watchTimer = 0; return; }
      // B-ONB-M2: leaving the setup chat used to leave this 1.5s interval
      // running for the rest of the session. watch() is re-armed by
      // beginSetup() when the user returns to the setup chat.
      if (!inSetupChat()) { clearInterval(watchTimer); watchTimer = 0; return; }
      if (document.hidden) return;
      const sig = `${textModels().length}:${hubState()}:${currentModel()}`;
      if (sig !== watchSig) { watchSig = sig; rerender(); }
    }, 1500);
  }

  async function proxyAddress() {
    if (proxyUrl) return proxyUrl;
    try { const st = await api("/api/pg/hub"); proxyUrl = String((st && st.proxy) || "").replace(/\/+$/, ""); } catch (e) { proxyUrl = ""; }
    return proxyUrl;
  }
  async function openProxy() {
    let res = null;
    try { if (window.MemuLinks && MemuLinks.open) res = await MemuLinks.open("memuai://proxy/accounts", "app"); } catch (e) { res = null; }
    if (!res || res.ok === false) {
      const url = await proxyAddress();
      if (url) window.open(url, "_blank", "noopener");
      else say(L("MemuAI Proxy недоступен", "MemuAI Proxy is unavailable"), "warn");
    } else if (res.message) say(res.message, "info");
    setTimeout(() => { if (typeof refreshState === "function") refreshState().then(rerender).catch(() => {}); }, 1500);
  }
  async function launchProxy(button) {
    if (button) button.disabled = true;
    say(L("Запускаю MemuAI Proxy…", "Launching MemuAI Proxy…"), "info");
    try {
      const res = await api("/api/pg/launch_proxy", { method: "POST" });
      if (res && res.ok === false) throw new Error(res.error || L("ошибка", "error"));
      for (let n = 0; n < 8; n += 1) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        if (typeof refreshState === "function") await refreshState().catch(() => {});
        if (hubState() !== "offline") break;
      }
    } catch (error) {
      say(L("Не удалось запустить: ", "Could not launch: ") + (error.message || String(error)), "err");
    }
    rerender();
  }

  // ── Reports for the steps written by the model ──────────────────────────
  // Facts come in both languages so the model names features exactly as the
  // interface does in the user's language.
  const ABOUT = [
    ["разговоры с любой моделью, подключённой в MemuAI Proxy; модель меняется кнопкой модели над полем ввода или по Ctrl+M",
      "conversations with any model connected in MemuAI Proxy; the model is switched with the model button above the composer or Ctrl+M"],
    ["веб-поиск с источниками и глубокое исследование командой /research", "web search with sources and deep research with the /research command"],
    ["режим Work: работа с файлами папки проекта; прежде чем что-то изменить, MemuAI спрашивает разрешения",
      "Work mode: works with the files of a project folder and asks before changing them"],
    ["MemuCode: отдельное окно для кода — агент, редактор с изменениями и терминал", "MemuCode: a separate window for code with an agent, an editor and a terminal"],
    ["Do For You: ИИ сам выполняет задачи в своём браузере и спрашивает перед важными шагами; запуск — /dfy",
      "Do For You: the AI performs tasks in its own browser and asks before important steps; started with /dfy"],
    ["голос: диктовка, озвучивание ответов и голосовой разговор MemuAI Live", "voice: dictation, reading replies aloud and the MemuAI Live voice conversation"],
    ["моделям можно отправлять картинки и видео; модели изображений рисуют прямо в чате",
      "pictures and video can be sent to models; image models create pictures right in the chat"],
    ["память о пользователе, «Планирование» (задачи по расписанию), интеграции (Gmail, Календарь, Диск, Notion, GitHub) и «Быстрый доступ» по Alt+Space",
      "memory about the user, Planning (scheduled tasks), integrations (Gmail, Calendar, Drive, Notion, GitHub) and Quick Ask on Alt+Space"],
  ];
  const AGENT_ABOUT = [
    ["эта агентная область — один бесконечный разговор с компаньоном, как в мессенджере",
      "this agentic workspace is one endless conversation with the companion, like a messenger"],
    ["компаньон сам включает Work или MemuCode, когда задаче нужны файлы или код; большие задачи становятся отдельными проектами",
      "the companion switches Work or MemuCode on by itself when a task needs files or code; big tasks become separate projects"],
    ["компаньон запоминает важное и бережёт токены: сводки старых сообщений и поиск по всей переписке",
      "the companion remembers what matters and keeps the conversation economical with summaries and search"],
  ];
  const TAG_FACTS = {
    code: [["MemuCode — отдельное окно для кода: агент с выбранными вами разрешениями, редактор с изменениями и терминал",
      "MemuCode is a separate window for code: an agent with permissions you choose, an editor with changes and a terminal"],
    ["режим Work работает с файлами папки проекта и спрашивает перед изменениями; «Полный доступ» применяет изменения сам",
      "Work mode works with the files of a project folder and asks before changing them; Full access applies changes by itself"]],
    automation: [["Do For You выполняет задачи в своём браузере и спрашивает перед важными шагами; /dfy запускает его из поля ввода",
      "Do For You performs tasks in its own browser and asks before important steps; /dfy starts it from the composer"],
    ["«Планирование» запускает запросы по расписанию", "Planning runs requests on a schedule"],
    ["«Быстрый доступ» открывает маленькое окно по Alt+Space поверх любой программы", "Quick Ask opens a small window on Alt+Space above any program"]],
    chat: [["«Характер ответов» меняет тон и манеру ассистента во всех чатах", "Reply style changes the tone and manner of the assistant in every chat"],
    ["голос: диктовка, озвучивание ответов и голосовой разговор MemuAI Live", "voice: dictation, reading replies aloud and the MemuAI Live voice conversation"],
    ["память хранит факты о пользователе; их можно посмотреть и поправить в «Профиль и память»",
      "memory keeps facts about the user; they can be seen and edited in Profile & memory"]],
    writing: [["«Ваши инструкции» добавляются в каждый чат", "Your instructions are added to every chat"],
    ["«Быстрый доступ» по Alt+Space помогает с текстом поверх любой программы", "Quick Ask on Alt+Space helps with text above any program"]],
    research: [["веб-поиск даёт ответы с источниками и может быть включён по умолчанию", "web search gives answers with sources and can be on by default"],
    ["/research запускает глубокое исследование и пишет отчёт", "/research runs deep research and writes a report"]],
    study: [["веб-поиск с источниками и /research для глубокого исследования", "web search with sources, and /research for deep research"],
    ["визуализации: схемы и интерактивные блоки прямо в ответах", "visualizations: diagrams and interactive blocks inside answers"],
    ["голос: диктовка и озвучивание ответов", "voice: dictation and reading replies aloud"]],
    files: [["файлы и картинки прикрепляются к любому сообщению кнопкой +", "files and pictures are attached to any message with the + button"],
    ["режим Work работает с файлами папки проекта", "Work mode works with the files of a project folder"]],
    images: [["модели изображений рисуют прямо в чате; они подключаются в MemuAI Proxy, как и другие модели",
      "image models create pictures right in the chat; they are connected in MemuAI Proxy like other models"],
    ["если модель не видит картинки, их описывает модель распознавания (раздел «Изображения и видео»)",
      "if a model cannot see pictures, a recognition model describes them (Images & video settings)"]],
    video: [["видео понимается по равномерно выбранным кадрам с помощью модели для видео; речь из него можно расшифровать (раздел «Изображения и видео»)",
      "video is understood from evenly sampled frames by a video model; speech in it can be transcribed (Images & video settings)"]],
    server: [["раздел «Сервер» подключает MemuAI к вашему серверу: режимы «Локально», «Сервер» и «Гибрид», адрес и токен сервера, автоматическая синхронизация и мост к этому компьютеру",
      "Server settings connect MemuAI to your server: Local, Server or Hybrid mode, server address and token, automatic sync and a bridge to this computer"]],
    server_info: [["сервер не обязателен: MemuAI полностью работает на этом компьютере, а сервер лишь позволяет хранить на нём файлы, проекты и чаты",
      "a server is optional: MemuAI works fully on this computer; a server only lets files, projects and chats live on it"]],
  };
  const factLines = (rows) => rows.map((row) => "- " + L(row[0], row[1])).join("\n");
  const aboutFacts = () => factLines(companion() ? AGENT_ABOUT.concat(ABOUT) : ABOUT);
  function answerTags() {
    const answers = P("onboarding_answers", {}) || {};
    return Array.isArray(answers._tags) ? answers._tags.map(String) : [];
  }
  function answerLines() {
    const answers = P("onboarding_answers", {}) || {};
    const questions = survey();
    return Object.keys(answers).filter((key) => !key.startsWith("_")).map((key) => {
      const question = questions.find((row) => row.id === key);
      const value = Array.isArray(answers[key]) ? answers[key].join(", ") : String(answers[key] || "");
      return value ? { q: question ? question.q : key, a: value } : null;
    }).filter(Boolean);
  }
  function header(step) {
    const c = companion();
    const who = c
      ? `You are ${c.name}, the user's companion in an agentic MemuAI workspace. Stay fully in character — your usual manner, like texting someone close; short messages; you may split the reply into 2–3 short paragraphs.`
      : "You are MemuAI, the assistant of the MemuAI app.";
    return [
      "MemuAI first-run setup. This text comes from the app, not from the user: the user sees only your reply and the buttons the app shows under it.",
      who,
      `Reply in ${en() ? "English" : "Russian"}. The user's name: ${JSON.stringify(userName() || "unknown")}.`,
      c ? "" : (en() ? "Be polite and friendly." : "Address the user formally, with «вы» (never «ты»); polite and friendly."),
      "Use feature and settings names exactly as written in the facts below, in the same language.",
      "Plain text only: no headings, tables, code blocks, links, special blocks or markers, and no tools. Never invent MemuAI features, settings or shortcuts — use only the facts given here.",
      step,
    ].filter(Boolean).join("\n");
  }
  function introReport() {
    const c = companion();
    const facts = aboutFacts();
    const intro = c
      ? "Introduce yourself warmly in your own manner, as in a first message to a new friend: who you are and that this is your shared conversation, where you can also work with files and code when needed."
      : "Say in one sentence who you are and name two or three things you can help with, chosen from the facts.";
    return header(`Step: your very first message. The user has just chosen the model you run on (${modelName(currentModel())}).
Greet the user by name. ${intro} Then say that a few quick questions below will help set MemuAI up for them. The questions are shown as buttons under your message: do not list or repeat them and do not ask anything else. At most four short sentences in total.
Facts about MemuAI:
${facts}`);
  }
  function reactReport(lines, skipped) {
    if (skipped === "none") {
      return header(`Step: your very first message. The user has just chosen the model you run on (${modelName(currentModel())}).
Greet the user by name and say in one sentence who you are${companion() ? ", in your own manner" : ""}. Then offer to go through the main settings in one of two ways — briefly (one screen with the most useful settings) or in detail (section by section, with explanations). Both choices are buttons under your message: do not number them and do not ask anything else. At most four short sentences.
Facts about MemuAI:
${aboutFacts()}`);
    }
    const tags = new Set();
    lines.forEach((row) => (row.tags || []).forEach((tag) => tags.add(tag)));
    const facts = [...tags].flatMap((tag) => TAG_FACTS[tag] || []);
    const answers = skipped || !lines.length ? "The user skipped the questions." : "The user's answers:\n" + lines.map((row) => `- ${row.q} — ${row.a}`).join("\n");
    return header(`Step: the user's answers to the questions.
${answers}
Reply in two or three short sentences: ${skipped ? "say that's fine and nothing is lost" : "react to the answers briefly and specifically — what in MemuAI will help with that, using only the matching facts below"}; then offer to go through the main settings in one of two ways — briefly (one screen with the settings most useful for them) or in detail (section by section, with explanations). Both choices are buttons under your message: do not number them and do not ask anything else.
${facts.length ? "Matching facts:\n" + factLines(facts) : "Facts about MemuAI:\n" + factLines(ABOUT.slice(0, 4))}`);
  }
  function sectionReport(list, at) {
    const id = list[at];
    const info = pageInfo(id);
    const section = SECTIONS.find((row) => row.id === id) || { facts: [] };
    const labels = section.facts.map((row) => L(row[0], row[1])).join("; ");
    const lines = answerLines();
    const next = list[at + 1] ? pageInfo(list[at + 1]).title : "";
    return header(`Step: settings walkthrough, section ${at + 1} of ${list.length}: "${info.title}" — ${info.desc || ""}
What this section contains (names exactly as in the interface): ${labels || "see the section"}.
${section.note ? "Also: " + L(section.note[0], section.note[1]) + "\n" : ""}The user's answers: ${lines.length ? lines.map((row) => `${row.q} — ${row.a}`).join("; ") : "not given"}.
Explain this section in three to five short sentences: what it is for and what is worth setting here for this user; name settings only from the list above, exactly as written${en() ? "" : ", in «» quotes (for example «" + (section.facts[0] ? section.facts[0][0] : info.title) + "»)"}. If the section matters little for them, say so in one sentence and that it can stay as it is. Do not start with the section name as a title and do not ask questions: buttons to open the section${next ? ` or go on to "${next}"` : " or finish"} are under your message.`);
  }

  function runStep(kind, report, extra = {}) {
    if (busyHere()) { say(L("Дождитесь окончания ответа", "Wait for the current answer to finish"), "warn"); return null; }
    const model = currentModel();
    if (!model) { say(L("Сначала выберите модель", "Choose a model first"), "warn"); return null; }
    const internal = { role: "user", internal: true, content: `<${REPORT_TAG}>\n${report}\n</${REPORT_TAG}>`, createdAt: now() };
    const assistant = { role: "assistant", content: "", streaming: true, model, createdAt: now(), setupCard: { kind, state: "pending", ...extra } };
    S.messages.push(internal, assistant);
    rerender();
    persist();
    generateFor(assistant, model, internal.content);
    return assistant;
  }
  function generateFor(assistant, model, raw) {
    const chatId = S.currentId;
    let job;
    try { job = generate(assistant, model, raw, []); } catch (error) { job = Promise.reject(error); }
    Promise.resolve(job).catch((error) => {
      assistant.streaming = false;
      if (!assistant.error) assistant.error = (error && error.message) || String(error);
    }).finally(() => {
      if (S.currentId === chatId) { rerender(); persist(); }
    });
  }
  function retryStep(i) {
    const m = S.messages[i];
    const prev = S.messages[i - 1];
    if (!m || !m.setupCard || !prev || !prev.internal) return;
    if (busyHere()) return;
    const model = currentModel();
    if (!model) { say(L("Сначала выберите модель", "Choose a model first"), "warn"); return; }
    const fresh = { role: "assistant", content: "", streaming: true, model, createdAt: now(), setupCard: m.setupCard };
    S.messages.splice(i, 1, fresh);
    rerender();
    persist();
    generateFor(fresh, model, prev.content);
  }

  // ── Settings shown during the walkthrough ───────────────────────────────
  // Names are the real labels of each settings page; the model may mention
  // nothing else. A section is shown when it fits the answers.
  const SECTIONS = [
    { id: "models", always: true, facts: [["Модель по умолчанию", "Default model"], ["Глубина размышления", "Reasoning effort"], ["Видимые модели", "Visible models"],
      ["Размышление по моделям", "Reasoning by model"], ["Закреплять основные модели", "Pin primary models"], ["Кэширование промптов", "Prompt caching"]],
      note: ["новые модели и API-ключи добавляются в MemuAI Proxy («Аккаунты» → «Добавить аккаунт»); список моделей здесь обновится сам", "new models and API keys are added in MemuAI Proxy (Accounts → Add account); the model list here updates by itself"] },
    { id: "spaces", agent: true, tags: ["chat"], facts: [["Компаньон", "Companion"], ["Модель общего разговора", "Model of the main conversation"], ["Экономия контекста", "Context economy"],
      ["Модель для сводок", "Summary model"], ["Модель эмбеддингов", "Embedding model"], ["Вспоминать автоматически", "Recall automatically"], ["Подстраиваться под ваш стиль", "Mirror your style"]],
      note: ["в обычной области на этой странице также создаётся агентная область с ИИ-компаньоном", "in the standard workspace this page also creates an agentic workspace with an AI companion"] },
    { id: "chat", tags: ["chat", "writing", "study"], fallback: true, facts: [["Характер ответов", "Reply style"], ["Ваши инструкции", "Your instructions"], ["Визуализации", "Visualizations"],
      ["Кастомные интерактивные блоки", "Custom interactive blocks"], ["Карточки товаров", "Product cards"], ["Сжимать историю автоматически", "Compress history automatically"]] },
    { id: "search", tags: ["research", "study", "writing"], fallback: true, facts: [["Включать по умолчанию", "On by default"], ["Источник", "Source"], ["Сервис", "Service"],
      ["API-ключ", "API key"], ["Максимум результатов", "Maximum results"]], note: ["/research в поле ввода запускает глубокое исследование с отчётом", "/research in the composer runs deep research with a report"] },
    { id: "media", tags: ["images", "video"], facts: [["Отправка изображений", "Sending images"], ["Модель распознавания", "Recognition model"], ["Обработка видео", "Handling video"],
      ["Модель для видео", "Video model"], ["Кадров из видео", "Frames per video"], ["Расшифровывать речь", "Transcribe speech"]],
      note: ["модели для создания картинок подключаются в MemuAI Proxy, как и любые другие", "image generation models are connected in MemuAI Proxy like any other model"] },
    { id: "voice", tags: ["chat", "study"], facts: [["Микрофон для диктовки", "Dictation microphone"], ["Кнопка MemuAI Live", "MemuAI Live button"], ["Распознавание речи", "Speech recognition"],
      ["Шумоподавление", "Noise suppression"], ["Очистка диктовки", "Dictation cleanup"], ["Озвучивание ответов", "Reading replies aloud"]] },
    { id: "work", tags: ["code", "automation", "files"], fallback: true, facts: [["Доступ к файлам", "File access"], ["Сначала план", "Plan first"], ["Подтверждать команды терминала", "Confirm terminal commands"],
      ["Модель для Browser", "Browser model"], ["Модель для Computer Use", "Computer Use model"], ["Модель агентов", "Agent model"], ["Агентов одновременно", "Agents at once"]] },
    { id: "memucode", tags: ["code"], facts: [["Разрешения агента", "Agent permissions"], ["Агент", "Agent"], ["Редактор и изменения", "Editor and changes"],
      ["Терминал и команды", "Terminal and commands"], ["Инструменты и навыки", "Tools and skills"]] },
    { id: "dfy", tags: ["automation"], facts: [["Включить DFY", "Enable DFY"], ["Модель DFY", "DFY model"], ["Лимит шагов", "Step limit"], ["Подтверждения", "Confirmations"],
      ["Показывать окно браузера", "Show the browser window"], ["Браузер для планирования", "Browser for scheduled tasks"]], note: ["/dfy в поле ввода запускает задачу Do For You", "/dfy in the composer starts a Do For You task"] },
    { id: "quick", tags: ["automation", "writing"], facts: [["Быстрый доступ", "Quick Ask"], ["Сочетание клавиш", "Hotkey"], ["Модель", "Model"], ["Длина ответа", "Answer length"]],
      note: ["маленькое окно по Alt+Space поверх любой программы", "a small window on Alt+Space above any program"] },
    { id: "server", tags: ["server", "server_info"], facts: [["Режим", "Mode"], ["Локально", "Local"], ["Сервер", "Server"], ["Гибрид", "Hybrid"], ["Адрес сервера", "Server address"],
      ["Токен сервера", "Server token"], ["Синхронизировать автоматически", "Sync automatically"], ["Мост включён", "Bridge enabled"]] },
    { id: "profile", always: true, facts: [["Имя и аватар", "Name and avatar"], ["PIN-код профиля", "Profile PIN"], ["Память включена", "Memory enabled"], ["Факты памяти", "Memory facts"]] },
    { id: "appearance", always: true, facts: [["Тема", "Theme"], ["Акцентный цвет", "Accent colour"], ["Дизайн интерфейса", "Interface design"], ["Шрифт", "Font"],
      ["Размер интерфейса", "Interface size"], ["Анимации", "Animations"]] },
  ];
  const MAX_SECTIONS = 9;
  function sectionsFor(tags) {
    const agent = agentSpace();
    const none = !tags.length;
    const fits = (row) => {
      if (!pageExists(row.id)) return false;
      if (row.agent && agent) return true;
      if (row.always) return true;
      return none ? !!row.fallback : (row.tags || []).some((tag) => tags.includes(tag));
    };
    const list = SECTIONS.filter(fits);
    if (list.length <= MAX_SECTIONS) return list.map((row) => row.id);
    const keep = new Set(list.filter((row) => row.always || (row.agent && agent)).map((row) => row.id));
    let room = MAX_SECTIONS - keep.size;
    return list.filter((row) => keep.has(row.id) || (room-- > 0)).map((row) => row.id);
  }

  const ECONOMY_NAMES = { max: ["Максимальная экономия", "Maximum savings"], balanced: ["Баланс", "Balanced"], rich: ["Больше контекста", "More context"] };
  const TILES = {
    model: { icon: "bot", ru: "Модель по умолчанию", en: "Default model", always: true, control: "model",
      value: () => modelName(P("default_model", "") || currentModel()),
      why: () => { const keys = keyLabel("models", "Ctrl+M"); return L(`Сменить на ходу — кнопка модели над полем ввода${keys ? " или " + keys : ""}`, `Switch any time with the model button above the composer${keys ? " or " + keys : ""}`); } },
    companion: { icon: "user", ru: "Компаньон", en: "Companion", agentOnly: true, always: true, control: "action", action: "companion-edit", actionRu: "Изменить", actionEn: "Edit",
      value: () => { const c = companion(); const style = c && spaces().STYLES[c.style]; return c ? [c.name, style ? L(style.ru, style.en) : ""].filter(Boolean).join(" · ") : ""; },
      why: () => L("Имя, внешность, характер и манера общения — меняются в любой момент", "Name, looks, character and manner — change them any time") },
    memory: { icon: "brain", ru: "Память", en: "Memory", always: true, control: "toggle", get: () => memoryOn(),
      set: (on) => { S.memory.enabled = on; if (typeof saveMemory === "function") saveMemory(); },
      why: () => (agentSpace()
        ? L(`${companion().name} ${gv("запоминает", "запоминает", "запоминает")} важное о тебе; всё видно и правится в «Профиль и память»`, `${companion().name} remembers what matters about you; see and edit it in Profile & memory`)
        : L("MemuAI запоминает важное о вас; факты видны и правятся в «Профиль и память»", "MemuAI remembers what matters about you; see and edit the facts in Profile & memory")) },
    web: { icon: "globe", ru: "Веб-поиск по умолчанию", en: "Web search by default", tags: ["research", "study", "writing"], fallback: true, control: "toggle",
      get: () => !!P("web_search", false), set: (on) => setPrefs({ web_search: on }),
      why: (tags) => (tags.includes("research")
        ? L("Ответы со ссылками на свежие источники; для больших вопросов — /research", "Answers with links to fresh sources; for big questions use /research")
        : L("Ответы с опорой на свежие источники. Можно включать и для одного сообщения", "Answers backed by fresh sources. Can also be turned on for a single message")) },
    thinking: { icon: "sparkle", ru: "Размышление", en: "Thinking", tags: ["code", "research", "study"], control: "toggle",
      get: () => !!P("thinking", false), set: (on) => setPrefs({ thinking: on }),
      why: () => L("Модель обдумывает ответ: точнее в сложных задачах, но медленнее. Работает у моделей, которые это умеют", "The model thinks before answering: better on hard tasks, but slower. Works with models that support it") },
    personality: { icon: "chat", ru: "Характер ответов", en: "Reply style", standardOnly: true, tags: ["chat", "writing", "study"], fallback: true, control: "select",
      select: () => `<select class="pinput compact onb-select" data-onb-select="personality" aria-label="${h(L("Характер ответов", "Reply style"))}">${window.MemuPersonalities ? MemuPersonalities.optionsHtml(P("ai_personality", "friendly")) : ""}</select>`,
      why: () => L("Тон и манера ассистента во всех чатах", "Tone and manner of the assistant in every chat") },
    work: { icon: "folder", ru: "Доступ к файлам в Work", en: "File access in Work", tags: ["code", "automation", "files"], fallback: true, control: "segment",
      options: [["ask", "С подтверждением", "Ask first"], ["full", "Полный доступ", "Full access"]],
      get: () => (typeof workAccessMode === "function" ? workAccessMode("work") : "ask"),
      set: (value) => (typeof setWorkAccessMode === "function" ? setWorkAccessMode(value) : setPrefs({ work_access_mode: value })),
      why: () => L("Work работает с файлами папки проекта. В режиме «Сначала спросить» каждое изменение — только после вашего «да»", "Work edits the files of a project folder. With Ask first you approve every change") },
    memucode: { icon: "code", ru: "MemuCode", en: "MemuCode", tags: ["code"], page: "memucode",
      why: () => L("Отдельное окно для кода: агент, редактор с правками и терминал", "A separate window for code: an agent, an editor with changes and a terminal") },
    dfy: { icon: "zap", ru: "Do For You", en: "Do For You", tags: ["automation"], page: "dfy",
      why: () => L("ИИ выполняет задачи в своём браузере и спрашивает перед важными шагами. Быстрый старт — /dfy", "The AI does tasks in its own browser and asks before important steps. Quick start: /dfy") },
    media: { icon: "image", ru: "Изображения и видео", en: "Images & video", tags: ["images", "video"], page: "media",
      value: () => { const n = (S.imageModels || []).length; return n ? L(`Моделей для картинок: ${n}`, `Image models: ${n}`) : L("Моделей для картинок пока нет", "No image models yet"); },
      why: (tags) => (tags.includes("video")
        ? L("Что делать, если модель не видит картинки; видео разбирается по кадрам. Модели для картинок подключаются в MemuAI Proxy", "What to do when a model cannot see pictures; video is read frame by frame. Image models are connected in MemuAI Proxy")
        : L("Что делать, если модель не видит картинки. Модели для картинок подключаются в MemuAI Proxy", "What to do when a model cannot see pictures. Image models are connected in MemuAI Proxy")) },
    voice: { icon: "mic", ru: "Голос и Live", en: "Voice & Live", tags: ["chat", "study"], fallback: true, page: "voice",
      why: () => L("Диктовка, озвучивание ответов и голосовой разговор MemuAI Live", "Dictation, reading replies aloud and the MemuAI Live voice conversation") },
    quick: { icon: "keyboard", ru: "Быстрый доступ", en: "Quick Ask", tags: ["automation", "writing"], page: "quick",
      why: () => L("Маленькое окно по Alt+Space поверх любых программ", "A small Alt+Space window above any program") },
    server: { icon: "database", ru: "Сервер", en: "Server", tags: ["server", "server_info"], page: "server",
      why: (tags) => (tags.includes("server")
        ? L("Адрес, токен и синхронизация: файлы, проекты и чаты на вашем сервере", "Address, token and sync: files, projects and chats on your server")
        : L("Необязательно: MemuAI полностью работает на этом компьютере. Появится сервер — подключите здесь", "Optional: MemuAI works fully on this computer. Got a server later? Connect it here")) },
    economy: { icon: "hourglass", ru: "Экономия контекста", en: "Context economy", agentOnly: true, always: true, page: "spaces",
      value: () => { const row = ECONOMY_NAMES[(spaces().settings() || {}).economy] || ECONOMY_NAMES.balanced; return L(row[0], row[1]); },
      why: () => L("Сколько недавнего разговора помнить дословно — остальное хранится в сводках и находится поиском", "How much recent talk is kept verbatim — the rest lives in summaries and is found by search") },
    spaces: { icon: "layout", ru: "Рабочие области", en: "Workspaces", standardOnly: true, tags: ["chat"], page: "spaces",
      why: () => L("Агентная область: ИИ-компаньон и один бесконечный разговор, со своими чатами и памятью", "An agentic workspace: an AI companion and one endless conversation, with its own chats and memory") },
    appearance: { icon: "palette", ru: "Внешний вид", en: "Appearance", always: true, page: "appearance",
      why: () => L("Тема, акцентный цвет, дизайн интерфейса, шрифт и анимации", "Theme, accent colour, interface design, font and animations") },
  };
  const TILE_ORDER = ["model", "companion", "memory", "economy", "web", "thinking", "personality", "work", "memucode", "dfy", "media", "voice", "quick", "server", "spaces", "appearance"];
  const MAX_TILES = 9;
  function tilesFor(tags) {
    const agent = agentSpace();
    const none = !tags.length;
    const list = TILE_ORDER.filter((id) => {
      const t = TILES[id];
      if (!t || (t.agentOnly && !agent) || (t.standardOnly && agent)) return false;
      if (t.page && !pageExists(t.page)) return false;
      if (t.always) return true;
      return none ? !!t.fallback : (t.tags || []).some((tag) => tags.includes(tag));
    });
    if (list.length <= MAX_TILES) return list;
    const keep = new Set(list.filter((id) => TILES[id].always));
    let room = MAX_TILES - keep.size;
    return list.filter((id) => keep.has(id) || (room-- > 0));
  }

  // ── Cards under the setup messages ──────────────────────────────────────
  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    return b === 1 ? one : many;
  }
  const failed = (m) => !!m.error || !String(m.content || "").trim();
  function doneLine(text, iconName = "check") {
    return `<div class="onb-card onb-card-line"><span class="onb-line">${ui(iconName)}<span>${h(text)}</span></span></div>`;
  }
  function callout(iconName, title, text, actions = "", tone = "") {
    return `<div class="onb-callout${tone ? " " + tone : ""}"><span class="onb-callout-ico">${ui(iconName)}</span><div class="onb-callout-body"><b>${h(title)}</b><p>${h(text)}</p>${actions ? `<div class="onb-actions">${actions}</div>` : ""}</div></div>`;
  }
  function errorBlock(m) {
    return callout("info", L("Модель не ответила", "The model did not answer"), m.error || L("Ответ пустой или остановлен.", "The answer is empty or was stopped."),
      `<button type="button" class="pbtn onb-small" data-onb="retry">${ui("retry")}<span>${h(L("Повторить", "Try again"))}</span></button>
       <button type="button" class="pbtn ghost onb-small" data-onb="model-change">${ui("bot")}<span>${h(L("Другая модель", "Another model"))}</span></button>`, "warn");
  }
  const skipLink = () => `<button type="button" class="onb-link" data-onb="skip-all">${h(L("Пропустить знакомство", "Skip the introduction"))}</button>`;

  function modelCard(m, i, card, latest) {
    if (card.state !== "pending") return card.model ? doneLine(L("Модель: ", "Model: ") + modelName(card.model)) : "";
    if (!latest) return "";
    const models = textModels();
    const current = chosenModel(card);
    const offline = hubState() === "offline";
    let main;
    if (!models.length && offline) {
      main = callout("power", L("MemuAI Proxy не запущен", "MemuAI Proxy is not running"),
        L("Модели подключаются через MemuAI Proxy — без него MemuAI их не видит. Запустите его: он работает в фоне.", "Models come through MemuAI Proxy — without it MemuAI cannot see them. Launch it: it runs in the background."),
        `<button type="button" class="pbtn onb-small" data-onb="proxy-launch">${ui("power")}<span>${h(L("Запустить MemuAI Proxy", "Launch MemuAI Proxy"))}</span></button>
         <button type="button" class="pbtn ghost onb-small" data-onb="models-refresh">${ui("refresh")}<span>${h(L("Проверить снова", "Check again"))}</span></button>`, "warn");
    } else if (!models.length) {
      main = callout("lock", L("Пока нет ни одного API-ключа", "No API keys yet"),
        L("Модели берутся из MemuAI Proxy. Откройте его → «Аккаунты» → «Добавить аккаунт»: подойдёт API-ключ любого OpenAI-совместимого сервиса («Собственный API»), а многие провайдеры подключаются входом через браузер. Список здесь обновится сам.",
          "Models come from MemuAI Proxy. Open it → Accounts → Add account: an API key of any OpenAI-compatible service works (Custom API), and many providers connect by signing in through the browser. The list here updates by itself."),
        `<button type="button" class="pbtn onb-small" data-onb="proxy-open">${ui("external")}<span>${h(L("Открыть MemuAI Proxy", "Open MemuAI Proxy"))}</span></button>
         <button type="button" class="pbtn ghost onb-small" data-onb="models-refresh">${ui("refresh")}<span>${h(L("Обновить список", "Refresh the list"))}</span></button>`);
    } else {
      const row = (window.MemuModelPicker && MemuModelPicker.find && MemuModelPicker.find(current)) || { id: current };
      const mono = window.MemuModelPicker && MemuModelPicker.monogram ? MemuModelPicker.monogram(row) : "";
      const badges = window.MemuModelPicker && MemuModelPicker.badgesHtml ? MemuModelPicker.badgesHtml(row) : "";
      main = `<button type="button" class="onb-model" data-onb="model-pick" aria-haspopup="dialog" title="${h(L("Выбрать модель", "Choose a model"))}">${mono}
          <span class="onb-model-text"><b>${h(modelName(current))}</b><small>${h([current, row.account].filter(Boolean).join(" · "))}</small></span>${badges}<span class="onb-model-caret">${ui("updown")}</span></button>
        <div class="onb-actions"><button type="button" class="pbtn" data-onb="model-confirm"${current ? "" : " disabled"}>${ui("check")}<span>${h(L("Подтвердить", "Confirm"))}</span></button>
          <span class="onb-muted">${h(L(`Доступно ${models.length} ${plural(models.length, "модель", "модели", "моделей")}`, `${models.length} model${models.length === 1 ? "" : "s"} available`))}</span></div>
        <p class="onb-hint">${ui("info")}<span>${h(L("Нужна другая модель или свой ключ? Их добавляют в ", "Need another model or your own key? Add them in "))}<button type="button" class="onb-link" data-onb="proxy-open">MemuAI Proxy</button></span></p>`;
    }
    const c = companion();
    const comp = c ? `<div class="onb-comp-row">${spaces().avatarHtml(c, 38, "onb-comp-ava")}<span class="onb-comp-text"><b>${h(c.name)}</b>
        <small>${h(L("Имя, внешность и характер можно поменять ещё до первого сообщения", "Name, looks and character can be changed before the first message"))}</small></span>
        <button type="button" class="pbtn ghost onb-small" data-onb="companion-edit">${ui("edit")}<span>${h(L("Изменить", "Edit"))}</span></button></div>` : "";
    return `<div class="onb-card onb-card-model"><div class="onb-card-head">${ui("bot")}<b>${h(L("Модель ИИ", "AI model"))}</b></div>${main}${comp}
      <div class="onb-card-foot">${skipLink()}</div></div>`;
  }

  function surveyCard(m, i, card, latest) {
    if (card.state !== "pending") return doneLine(card.state === "skipped" ? L("Вопросы пропущены", "Questions skipped") : L("Ответы учтены", "Answers noted"));
    if (!latest) return "";
    const questions = Array.isArray(card.questions) ? card.questions : [];
    const draft = card.draft || (card.draft = {});
    const err = failed(m) ? errorBlock(m) : "";
    const qs = questions.map((question, k) => {
      const d = draft[question.id] || {};
      let control;
      if (question.type === "text") {
        control = `<textarea class="pinput onb-q-text" data-onb-text="${h(question.id)}" rows="2" maxlength="600" placeholder="${h(L("Ваш ответ", "Your answer"))}">${h(d.text || "")}</textarea>`;
      } else {
        const multi = question.type === "multi";
        const chips = question.options.map((option) => {
          const on = (d.sel || []).includes(option.id);
          return `<button type="button" class="onb-chip${on ? " on" : ""}" data-onb="chip" data-q="${h(question.id)}" data-o="${h(option.id)}" role="${multi ? "checkbox" : "radio"}" aria-checked="${on}">${multi ? `<span class="onb-chip-box" aria-hidden="true">${on ? ui("check") : ""}</span>` : ""}<span>${h(option.label)}</span></button>`;
        }).join("");
        const other = question.other ? `<button type="button" class="onb-chip onb-chip-other${d.otherOn ? " on" : ""}" data-onb="chip-other" data-q="${h(question.id)}" aria-pressed="${!!d.otherOn}">${ui("plus")}<span>${h(L("Другое", "Other"))}</span></button>` : "";
        control = `<div class="onb-chips" role="${multi ? "group" : "radiogroup"}" aria-label="${h(question.q)}">${chips}${other}</div>
          ${question.other && d.otherOn ? `<input class="pinput onb-other" data-onb-text="${h(question.id)}" data-other="1" maxlength="200" value="${h(d.other || "")}" placeholder="${h(L("Что ещё?", "What else?"))}">` : ""}`;
      }
      return `<div class="onb-q" data-q="${h(question.id)}"><div class="onb-q-head"><span class="onb-q-n">${k + 1}</span><span><b>${h(question.q)}</b>${question.hint ? `<small>${h(question.hint)}</small>` : ""}</span></div>${control}</div>`;
    }).join("");
    const remember = memoryOn() && window.MemuMemory && MemuMemory.remember
      ? `<label class="onb-check"><input type="checkbox" data-onb-remember${card.remember !== false ? " checked" : ""}><span>${h(L("Запомнить ответы", "Remember the answers"))}</span></label>` : "";
    return `<div class="onb-card onb-card-survey">${err}<div class="onb-qs">${qs}</div>
      <div class="onb-actions"><button type="button" class="pbtn" data-onb="survey-submit">${ui("send")}<span>${h(L("Отправить ответы", "Send answers"))}</span></button>
        <button type="button" class="pbtn ghost" data-onb="survey-skip">${h(L("Пропустить", "Skip"))}</button><span class="onb-grow"></span>${remember}</div>
      <div class="onb-card-foot"><button type="button" class="onb-link" data-onb="survey-edit">${ui("edit")}<span>${h(L("Изменить вопросы", "Edit the questions"))}</span></button>${skipLink()}</div></div>`;
  }

  function walkCard(m, i, card, latest) {
    if (card.state !== "pending") {
      const text = card.choice === "brief" ? L("Выбрано: кратко", "Chosen: briefly") : card.choice === "detailed" ? L("Выбрано: подробно", "Chosen: in detail") : L("Настройки пропущены", "Settings skipped");
      return doneLine(text);
    }
    if (!latest) return "";
    const n = sectionsFor(answerTags()).length;
    return `<div class="onb-card onb-card-walk">${failed(m) ? errorBlock(m) : ""}
      <div class="onb-walk" role="group" aria-label="${h(L("Как пройти настройки", "How to go through the settings"))}">
        <button type="button" class="onb-walk-opt" data-onb="walk" data-choice="brief"><span class="onb-walk-ico">${ui("layout")}</span><b>${h(L("Кратко", "Briefly"))}</b><small>${h(L("Самое нужное вам — на одном экране", "What you need most, on one screen"))}</small></button>
        <button type="button" class="onb-walk-opt" data-onb="walk" data-choice="detailed"><span class="onb-walk-ico">${ui("list")}</span><b>${h(L("Подробно", "In detail"))}</b><small>${h(L(`По разделам, с объяснениями · ${n} ${plural(n, "раздел", "раздела", "разделов")}`, `Section by section, explained · ${n} sections`))}</small></button>
      </div>
      <div class="onb-card-foot"><button type="button" class="onb-link" data-onb="walk" data-choice="skip">${h(L("Пропустить настройки", "Skip the settings"))}</button></div></div>`;
  }

  function tileControl(id, t) {
    const title = L(t.ru, t.en);
    if (t.control === "toggle") {
      const on = !!t.get();
      return `<label class="onb-switch"><span class="switch"><input type="checkbox" role="switch" data-onb-toggle="${id}"${on ? " checked" : ""} aria-label="${h(title)}"><span class="sl"></span></span><span class="onb-switch-text">${h(on ? L("Включено", "On") : L("Выключено", "Off"))}</span></label>`;
    }
    if (t.control === "segment") {
      const cur = t.get();
      return `<div class="onb-seg" role="radiogroup" aria-label="${h(title)}">${t.options.map(([value, ru, english]) => `<button type="button" role="radio" aria-checked="${cur === value}" class="${cur === value ? "on" : ""}" data-onb="tile-seg" data-tile="${id}" data-v="${h(value)}">${h(L(ru, english))}</button>`).join("")}</div>`;
    }
    if (t.control === "select") return t.select();
    if (t.control === "model") return `<button type="button" class="pbtn ghost onb-small" data-onb="tile-model">${ui("updown")}<span>${h(L("Сменить", "Change"))}</span></button>`;
    if (t.control === "action") return `<button type="button" class="pbtn ghost onb-small" data-onb="${h(t.action)}">${ui("edit")}<span>${h(L(t.actionRu, t.actionEn))}</span></button>`;
    if (t.page) return `<button type="button" class="onb-tile-open" data-onb="open-page" data-page="${h(t.page)}"><span>${h(L("Открыть", "Open"))}</span>${ui("forward")}</button>`;
    return "";
  }
  function tileHtml(id, tags) {
    const t = TILES[id];
    if (!t) return "";
    let value = "", why = "";
    try { value = t.value ? t.value() : ""; why = t.why ? t.why(tags) : ""; } catch (e) { /* optional part not loaded */ }
    const openable = t.page && t.control;
    return `<div class="onb-tile" data-tile="${id}">
      <div class="onb-tile-head"><span class="onb-tile-ico">${ui(t.icon)}</span><b>${h(L(t.ru, t.en))}</b>${openable ? `<button type="button" class="onb-tile-more" data-onb="open-page" data-page="${h(t.page)}" title="${h(L("Открыть в настройках", "Open in Settings"))}" aria-label="${h(L("Открыть в настройках", "Open in Settings"))}">${ui("external")}</button>` : ""}</div>
      ${value ? `<span class="onb-tile-val">${h(value)}</span>` : ""}
      <p class="onb-tile-why">${h(why)}</p>
      <div class="onb-tile-ctl">${tileControl(id, t)}</div></div>`;
  }
  function briefCard(m, i, card, latest) {
    const tags = Array.isArray(card.tags) ? card.tags : answerTags();
    const ids = Array.isArray(card.tiles) && card.tiles.length ? card.tiles : tilesFor(tags);
    const live = card.state === "pending" && latest;
    return `<div class="onb-card onb-card-brief"><div class="onb-tiles">${ids.map((id) => tileHtml(id, tags)).join("")}</div>
      ${live ? `<div class="onb-actions"><button type="button" class="pbtn" data-onb="brief-done">${ui("check")}<span>${h(L("Готово", "Done"))}</span></button>
        <button type="button" class="pbtn ghost" data-onb="brief-detailed">${ui("list")}<span>${h(L("Подробно по разделам", "Section by section"))}</span></button></div>` : ""}</div>`;
  }
  function sectionCard(m, i, card, latest) {
    const list = Array.isArray(card.list) ? card.list : [];
    if (!list.length) return "";
    const at = Math.max(0, Math.min(+card.at || 0, list.length - 1));
    const info = pageInfo(list[at]);
    const next = list[at + 1] ? pageInfo(list[at + 1]) : null;
    const live = card.state === "pending" && latest;
    return `<div class="onb-card onb-card-section">${live && failed(m) ? errorBlock(m) : ""}
      <div class="onb-sec-head"><span class="onb-sec-ico">${info.icon || ui("settings")}</span><span class="onb-sec-text"><b>${h(info.title)}</b><small>${h(L(`Раздел ${at + 1} из ${list.length}`, `Section ${at + 1} of ${list.length}`))}</small></span>
        <button type="button" class="pbtn ghost onb-small" data-onb="open-page" data-page="${h(list[at])}">${ui("external")}<span>${h(L("Открыть раздел", "Open section"))}</span></button></div>
      <div class="onb-progress" role="progressbar" aria-valuemin="1" aria-valuemax="${list.length}" aria-valuenow="${at + 1}"><i style="width:${Math.round(((at + 1) / list.length) * 100)}%"></i></div>
      ${live ? `<div class="onb-actions">${next
        ? `<button type="button" class="pbtn" data-onb="section-next">${ui("forward")}<span>${h(L("Дальше: ", "Next: ") + next.title)}</span></button><button type="button" class="pbtn ghost" data-onb="section-finish">${h(L("Закончить здесь", "Stop here"))}</button>`
        : `<button type="button" class="pbtn" data-onb="section-finish">${ui("check")}<span>${h(L("Завершить", "Finish"))}</span></button>`}</div>` : ""}</div>`;
  }
  function tipsList() {
    const out = [];
    const add = (key, title, text) => out.push({ key, title, text });
    const glyph = (name) => `<span class="onb-tip-ico">${ui(name)}</span>`;
    const search = keyLabel("search", "Ctrl+K");
    if (search) add(kbd(search), L("Поиск везде", "Search everywhere"), L("Чаты, память, файлы и настройки", "Chats, memory, files and settings"));
    add(kbd("/"), L("Команды", "Commands"), L("В поле ввода: /research — исследование, /plan — план, /dfy — сделать за вас", "In the composer: /research for research, /plan for a plan, /dfy to have it done"));
    add(kbd("@"), L("Упоминания", "Mentions"), L("Добавить в сообщение чат, заметку, файл или модель", "Add a chat, a note, a file or a model to a message"));
    add(glyph("plus"), L("Кнопка «+»", "The + button"), L("Файлы, картинки и инструменты", "Files, pictures and tools"));
    const models = keyLabel("models", "Ctrl+M");
    if (models) add(kbd(models), L("Модели", "Models"), L("Сменить модель; новые ключи — в MemuAI Proxy", "Switch the model; new keys go into MemuAI Proxy"));
    const help = keyLabel("help", "Ctrl+/");
    if (help) add(kbd(help), L("Горячие клавиши", "Shortcuts"), L("Шпаргалка по всем сочетаниям", "A cheat sheet of every shortcut"));
    if (agentSpace()) {
      const c = companion();
      add(glyph("message"), L("Как в мессенджере", "Like a messenger"), L(`Пиши о чём угодно — важное ${c.name} запомнит, а для файлов и кода ${gv("сама", "сам", "сам(а)")} включит Work или MemuCode`, `Write about anything — ${c.name} remembers what matters and turns on Work or MemuCode for files and code`));
      add(glyph("folder"), L("Проекты", "Projects"), L("Большие задачи живут в отдельных чатах-проектах на боковой панели", "Big tasks live in separate project chats in the sidebar"));
    } else {
      add(glyph("work"), L("Режимы", "Modes"), L("Чат, Work и MemuCode — вкладки вверху боковой панели", "Chat, Work and MemuCode are the tabs at the top of the sidebar"));
    }
    add(glyph("layout"), L("Рабочие области", "Workspaces"), L("Переключиться или создать новую — кнопка рядом с логотипом", "Switch or create one with the button next to the logo"));
    return out;
  }
  function doneCard(m, i, card, latest) {
    const home = pageExists("home") ? "home" : "general";
    return `<div class="onb-card onb-card-tips"><div class="onb-tips">${tipsList().map((t) => `<div class="onb-tip"><span class="onb-tip-key">${t.key}</span><span class="onb-tip-text"><b>${h(t.title)}</b><small>${h(t.text)}</small></span></div>`).join("")}</div>
      ${latest ? `<div class="onb-actions">${agentSpace()
        ? `<button type="button" class="pbtn" data-onb="done-write">${ui("message")}<span>${h(L("Начать разговор", "Start talking"))}</span></button>`
        : `<button type="button" class="pbtn" data-onb="done-new">${ui("plus")}<span>${h(L("Новый чат", "New chat"))}</span></button>`}
        <button type="button" class="pbtn ghost" data-onb="open-page" data-page="${home}">${ui("settings")}<span>${h(L("Все настройки", "All settings"))}</span></button></div>` : ""}
      <p class="onb-hint">${ui("info")}<span>${h(L("Пройти знакомство заново — меню «?» → «Знакомство с MemuAI».", "Run this introduction again from the ? menu → Getting started."))}</span></p></div>`;
  }

  function cardHtml(m, i) {
    const card = m && m.setupCard;
    if (!card || m.streaming) return "";
    try {
      const latest = lastCardIndex() === i;
      const kinds = { model: modelCard, survey: surveyCard, walk: walkCard, brief: briefCard, section: sectionCard, done: doneCard };
      const body = kinds[card.kind] ? kinds[card.kind](m, i, card, latest) : "";
      return body ? `<div class="onb-wrap${latest ? " latest" : ""}" data-onb-msg="${i}">${body}</div>` : "";
    } catch (error) {
      console.warn("MemuAI setup card:", error);
      return "";
    }
  }

  // ── Actions on the cards ────────────────────────────────────────────────
  function chosenModel(card) {
    const models = textModels();
    const has = (id) => !!id && models.some((row) => row.id === id);
    if (card && has(card.pick)) return card.pick;
    if (has(currentModel())) return currentModel();
    if (has(P("default_model", ""))) return P("default_model", "");
    return (models[0] && models[0].id) || "";
  }
  function reveal(index) {
    requestAnimationFrame(() => {
      const box = q("#messages") && q("#messages").parentElement;
      if (!box) return;
      const node = index >= 0 ? q(`#messages [data-msg="${index}"]`) : null;
      const behavior = reduced() ? "auto" : "smooth";
      if (node && node.offsetHeight > box.clientHeight * 0.8) {
        const top = node.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 12;
        box.scrollTo({ top, behavior });
      } else box.scrollTo({ top: box.scrollHeight, behavior });
    });
  }
  function refocus(selector) {
    requestAnimationFrame(() => {
      let el = q(selector);
      // a select dressed by enhanceSelects is hidden: focus its button instead
      if (el && el.classList && el.classList.contains("pretty-native")) el = (el.parentNode && el.parentNode.querySelector(".pretty-select-button")) || el;
      if (el && typeof el.focus === "function") el.focus({ preventScroll: true });
    });
  }
  const cssId = (value) => (window.CSS && CSS.escape ? CSS.escape(String(value)) : String(value).replace(/["\\]/g, "\\$&"));
  function idle() {
    if (!busyHere()) return true;
    say(L("Дождитесь окончания ответа", "Wait for the current answer to finish"), "warn");
    return false;
  }
  function userBubble(text) {
    return { role: "user", content: text, localOnly: true, setupAnswer: true, createdAt: now() };
  }
  function pushScripted(kind, content, extra) {
    S.messages.push(scripted(kind, content, extra));
    persist();
    rerender();
    reveal(S.messages.length - 1);
  }

  function startSurvey() {
    const questions = survey();
    if (!questions.length) return runStep("walk", reactReport([], "none"));
    return runStep("survey", introReport(), { questions, draft: {}, remember: true });
  }

  function surveyResult(card) {
    const questions = Array.isArray(card.questions) ? card.questions : [];
    const draft = card.draft || {};
    const answers = {};
    const lines = [];
    const tags = new Set();
    const focus = [];
    questions.forEach((question) => {
      const d = draft[question.id] || {};
      if (question.type === "text") {
        const text = String(d.text || "").trim().slice(0, 600);
        if (text) { answers[question.id] = text; lines.push({ q: question.q, a: text, tags: [] }); }
        return;
      }
      const chosen = question.options.filter((option) => (d.sel || []).includes(option.id));
      const own = [];
      chosen.forEach((option) => (option.tags || []).forEach((tag) => { tags.add(tag); own.push(tag); }));
      if (question.type === "multi") chosen.forEach((option) => (option.tags || []).forEach((tag) => focus.includes(tag) || focus.push(tag)));
      const labels = chosen.map((option) => option.label);
      const other = d.otherOn ? String(d.other || "").trim().slice(0, 200) : "";
      if (other) labels.push(other);
      if (!labels.length) return;
      answers[question.id] = question.type === "multi" ? labels.slice(0, 12) : labels.join(", ");
      lines.push({ q: question.q, a: labels.join(", "), tags: own });
    });
    answers._tags = [...tags].slice(0, 24);
    if (focus.length) answers._focus = focus.slice(0, 12);
    return { answers, lines };
  }
  function rememberAnswers(lines) {
    if (!window.MemuMemory || !MemuMemory.remember || !memoryOn()) return;
    const useful = lines.filter((row) => row.a).slice(0, 6);
    if (!useful.length) return;
    const text = L("Ответы при знакомстве с MemuAI: ", "Answers from the MemuAI introduction: ")
      + useful.map((row) => `${row.q.replace(/[?:]\s*$/, "")} — ${row.a}`).join("; ");
    Promise.resolve(MemuMemory.remember(text.slice(0, 480), { manual: true })).catch(() => {});
  }

  // The brief overview is drawn by the app; the line above it speaks in the
  // assistant's voice and mentions what the user said they need.
  const FOCUS_WORDS = {
    code: ["кода", "code"], automation: ["автоматизации", "automation"], chat: ["общения", "conversations"], writing: ["текстов", "writing"],
    research: ["поиска", "research"], study: ["учёбы", "studying"], files: ["документов", "documents"], images: ["картинок", "images"], video: ["видео", "video"],
  };
  function joinWords(list) {
    if (list.length < 2) return list.join("");
    return list.slice(0, -1).join(", ") + L(" и ", " and ") + list[list.length - 1];
  }
  function briefText() {
    const answers = P("onboarding_answers", {}) || {};
    const focus = (Array.isArray(answers._focus) ? answers._focus : answerTags()).filter((tag) => FOCUS_WORDS[tag]).slice(0, 3);
    const words = joinWords(focus.map((tag) => L(FOCUS_WORDS[tag][0], FOCUS_WORDS[tag][1])));
    if (agentSpace()) {
      return words
        ? L(`Смотри, вот что тебе пригодится для ${words}. Переключай прямо тут, а «Открыть» ведёт в нужный раздел настроек.`, `Look, here's what you'll want for ${words}. Flip the switches right here — Open takes you to the right settings section.`)
        : L("Смотри, вот главное — всё на одном экране. Переключай прямо тут, а «Открыть» ведёт в нужный раздел настроек.", "Look, here's the main stuff on one screen. Flip the switches right here — Open takes you to the right settings section.");
    }
    return words
      ? L(`Вот самое полезное для ${words} — на одном экране. Переключатели работают прямо здесь, а «Открыть» ведёт в нужный раздел настроек.`, `Here is what helps most with ${words}, on one screen. The switches work right here, and Open takes you to the right settings section.`)
      : L("Вот самое полезное — на одном экране. Переключатели работают прямо здесь, а «Открыть» ведёт в нужный раздел настроек.", "Here is what is most useful, on one screen. The switches work right here, and Open takes you to the right settings section.");
  }
  function showBrief() {
    const tags = answerTags();
    pushScripted("brief", briefText(), { tags, tiles: tilesFor(tags) });
  }
  function startSections() {
    const list = sectionsFor(answerTags());
    if (!list.length) { finish(); return true; }
    return !!runStep("section", sectionReport(list, 0), { list, at: 0 });
  }

  function doneText(skipped) {
    const name = userName();
    if (agentSpace()) {
      return skipped
        ? L(`Окей${name ? ", " + name : ""}, без долгих настроек. Пара подсказок — и можно болтать:`, `Okay${name ? ", " + name : ""}, no long setup. A couple of tips and we can talk:`)
        : L(`Всё${name ? ", " + name : ""}, мы готовы! Пиши о чём угодно — я рядом. Пара подсказок напоследок:`, `That's it${name ? ", " + name : ""}, we're ready! Write about anything — I'm here. A couple of tips before we start:`);
    }
    return skipped
      ? L(`Хорошо${name ? ", " + name : ""}, начнём без знакомства. Несколько подсказок на будущее:`, `All right${name ? ", " + name : ""}, let's start without the introduction. A few tips for later:`)
      : L(`Готово${name ? ", " + name : ""}! MemuAI настроен. Несколько подсказок, которые экономят время:`, `Done${name ? ", " + name : ""}! MemuAI is set up. A few tips that save time:`);
  }
  const SETUP_REPORT = new RegExp("^<" + REPORT_TAG + ">");
  // After the setup the walkthrough stays on screen but no longer travels to
  // the model: in a standard chat the conversation starts clean, and the
  // companion keeps only its first words and the reaction to the answers.
  function detachSetup(list) {
    const agent = agentSpace();
    list.forEach((m, k) => {
      if (!m || m.localOnly) return;
      const card = m.setupCard;
      if (m.internal && SETUP_REPORT.test(String(m.content || ""))) {
        const next = list[k + 1];
        const kind = next && next.setupCard ? next.setupCard.kind : "";
        if (!agent || kind === "section") m.localOnly = true;
      } else if (card && m.role === "assistant" && (!agent || card.kind === "section")) m.localOnly = true;
    });
  }
  function finish(opts = {}) {
    clearInterval(watchTimer);
    watchTimer = 0;
    const list = S.messages || [];
    list.forEach((m) => { if (m && m.setupCard && m.setupCard.state === "pending") m.setupCard.state = opts.skipped ? "skipped" : "done"; });
    detachSetup(list);
    setPrefs({ onboarding_stage: "done", onboarding_v5_done: true });
    pushScripted("done", doneText(!!opts.skipped), { state: "done" });
  }

  async function pickModel(anchor, value) {
    if (!window.MemuModelPicker || !MemuModelPicker.open) return "";
    const picked = await MemuModelPicker.open(anchor, { title: L("Модель ИИ", "AI model"), kind: "text", value: value || "" });
    return picked === null || picked === undefined ? "" : String(picked);
  }
  function useModel(id) {
    if (!id) return false;
    if (typeof chatModelAllowed === "function" && !chatModelAllowed(id)) return false;
    setCurrentModel(id, { warn: false });
    if (currentModel() !== id) return false;
    setPrefs({ default_model: id });
    try { if (typeof renderModelDD === "function") renderModelDD(); } catch (e) { /* composer not ready */ }
    return true;
  }
  function draftOf(card, qid) {
    const draft = card.draft || (card.draft = {});
    return draft[qid] || (draft[qid] = {});
  }
  function questionOf(card, qid) {
    return (Array.isArray(card.questions) ? card.questions : []).find((row) => row.id === qid) || null;
  }

  const ACTIONS = {
    "model-pick": async (ctx, el) => {
      const id = await pickModel(el, chosenModel(ctx.card));
      if (!id) return;
      ctx.card.pick = id;
      rerender();
      refocus(".onb-wrap.latest [data-onb='model-confirm']");
    },
    "model-confirm": (ctx, el) => {
      if (!idle()) return;
      const id = chosenModel(ctx.card);
      if (!useModel(id)) return;
      el.disabled = true;
      ctx.card.state = "done";
      ctx.card.model = id;
      if (!startSurvey()) { ctx.card.state = "pending"; el.disabled = false; rerender(); return; }
      reveal(S.messages.length - 1);
    },
    "proxy-open": () => openProxy(),
    "proxy-launch": (ctx, el) => launchProxy(el),
    "models-refresh": async (ctx, el) => {
      el.disabled = true;
      try { if (typeof refreshState === "function") await refreshState(); } catch (e) { /* the card shows the state */ }
      el.disabled = false;
      rerender();
      if (!textModels().length) say(L("Моделей пока нет", "No models yet"), "info");
    },
    "companion-edit": () => { if (spaces() && spaces().editCompanion) spaces().editCompanion(); },
    "skip-all": () => { if (idle()) finish({ skipped: true }); },
    chip: (ctx, el) => {
      const question = questionOf(ctx.card, el.dataset.q);
      if (!question) return;
      const d = draftOf(ctx.card, question.id);
      const sel = new Set(d.sel || []);
      const id = el.dataset.o;
      if (question.type === "multi") {
        if (sel.has(id)) sel.delete(id); else sel.add(id);
      } else {
        const was = sel.has(id);
        sel.clear();
        if (!was) sel.add(id);
        d.otherOn = false;
      }
      d.sel = [...sel];
      rerender();
      refocus(`.onb-wrap.latest [data-onb="chip"][data-q="${cssId(question.id)}"][data-o="${cssId(id)}"]`);
    },
    "chip-other": (ctx, el) => {
      const question = questionOf(ctx.card, el.dataset.q);
      if (!question) return;
      const d = draftOf(ctx.card, question.id);
      d.otherOn = !d.otherOn;
      if (d.otherOn && question.type !== "multi") d.sel = [];
      rerender();
      refocus(d.otherOn ? `.onb-wrap.latest [data-onb-text="${cssId(question.id)}"][data-other]` : `.onb-wrap.latest [data-onb="chip-other"][data-q="${cssId(question.id)}"]`);
    },
    "survey-submit": (ctx) => {
      if (!idle()) return;
      const { answers, lines } = surveyResult(ctx.card);
      if (!lines.length) { say(L("Выберите хотя бы один ответ или нажмите «Пропустить»", "Pick at least one answer or press Skip"), "warn"); return; }
      if (!currentModel()) { say(L("Сначала выберите модель", "Choose a model first"), "warn"); return; }
      ctx.card.state = "done";
      setPrefs({ onboarding_answers: answers });
      S.messages.push(userBubble(lines.map((row) => `${row.q} — ${row.a}`).join("\n")));
      if (ctx.card.remember !== false) rememberAnswers(lines);
      runStep("walk", reactReport(lines, false));
      reveal(S.messages.length - 1);
    },
    "survey-skip": (ctx) => {
      if (!idle()) return;
      if (!currentModel()) { say(L("Сначала выберите модель", "Choose a model first"), "warn"); return; }
      ctx.card.state = "skipped";
      setPrefs({ onboarding_answers: {} });
      S.messages.push(userBubble(T(L("Пропущу вопросы", "I'll skip the questions"), L("Давай без вопросов", "Let's skip the questions"))));
      runStep("walk", reactReport([], true));
      reveal(S.messages.length - 1);
    },
    "survey-edit": () => openSurveyEditor(),
    walk: (ctx, el) => {
      const choice = el.dataset.choice;
      if (!["brief", "detailed", "skip"].includes(choice) || !idle()) return;
      ctx.card.state = "done";
      ctx.card.choice = choice;
      const said = {
        brief: T(L("Кратко, пожалуйста", "Briefly, please"), L("Давай кратко", "Let's keep it short")),
        detailed: T(L("Подробно, по разделам", "In detail, section by section"), L("Давай подробно, по разделам", "Let's go through it in detail")),
        skip: T(L("Пропустим настройки", "Let's skip the settings"), L("Давай без настроек", "Let's skip the settings for now")),
      };
      S.messages.push(userBubble(said[choice]));
      if (choice === "brief") showBrief();
      else if (choice === "detailed") { if (!startSections()) { ctx.card.state = "pending"; S.messages.pop(); rerender(); } else reveal(S.messages.length - 1); }
      else finish();
    },
    "tile-seg": async (ctx, el) => {
      const t = TILES[el.dataset.tile];
      if (!t || !t.set) return;
      await t.set(el.dataset.v);
      rerender();
      refocus(`.onb-wrap [data-tile="${cssId(el.dataset.tile)}"] [aria-checked="true"]`);
    },
    "tile-model": async (ctx, el) => {
      const id = await pickModel(el, P("default_model", "") || currentModel());
      if (!id || !useModel(id)) return;
      rerender();
      say(L("Модель по умолчанию: ", "Default model: ") + modelName(id), "ok");
    },
    "open-page": (ctx, el) => openPage(el.dataset.page || "general"),
    "brief-done": (ctx) => { ctx.card.state = "done"; finish(); },
    "brief-detailed": (ctx) => {
      if (!idle()) return;
      ctx.card.state = "done";
      S.messages.push(userBubble(T(L("Давайте подробно, по разделам", "Let's go section by section"), L("Давай подробно, по разделам", "Let's go section by section"))));
      if (!startSections()) { ctx.card.state = "pending"; S.messages.pop(); rerender(); return; }
      reveal(S.messages.length - 1);
    },
    "section-next": (ctx) => {
      if (!idle()) return;
      const list = Array.isArray(ctx.card.list) ? ctx.card.list : [];
      const at = (+ctx.card.at || 0) + 1;
      ctx.card.state = "done";
      if (at >= list.length) { finish(); return; }
      if (!runStep("section", sectionReport(list, at), { list, at })) { ctx.card.state = "pending"; rerender(); return; }
      reveal(S.messages.length - 1);
    },
    "section-finish": (ctx) => { if (!idle()) return; ctx.card.state = "done"; finish(); },
    retry: (ctx) => retryStep(ctx.i),
    "model-change": async (ctx, el) => {
      if (!idle()) return;
      const id = await pickModel(el, currentModel());
      if (!id || !useModel(id)) return;
      retryStep(ctx.i);
    },
    "done-new": () => {
      if (S.mode !== "chat") S.mode = "chat";
      if (typeof startDraft === "function") startDraft(false, true);
      if (typeof updateWorkUI === "function") { try { updateWorkUI(); } catch (e) { /* static UI */ } }
    },
    "done-write": () => { const input = q("#input"); if (input) input.focus(); },
  };

  function contextOf(el) {
    const wrap = el && el.closest(".onb-wrap");
    const box = q("#messages");
    if (!wrap || !box || !box.contains(wrap)) return null;
    const i = +wrap.dataset.onbMsg;
    const m = Number.isInteger(i) && typeof S !== "undefined" ? S.messages[i] : null;
    return m && m.setupCard ? { i, m, card: m.setupCard } : null;
  }
  function onCardClick(event) {
    const el = event.target.closest("[data-onb]");
    if (!el || el.disabled) return;
    const ctx = contextOf(el);
    const fn = ctx && ACTIONS[el.dataset.onb];
    if (!fn) return;
    event.preventDefault();
    event.stopPropagation();
    Promise.resolve().then(() => fn(ctx, el, event)).catch((error) => say(error.message || String(error), "err"));
  }
  function onCardInput(event) {
    const el = event.target;
    if (!el.matches || !el.matches("[data-onb-text]")) return;
    const ctx = contextOf(el);
    if (!ctx) return;
    const d = draftOf(ctx.card, el.dataset.onbText);
    if (el.dataset.other) d.other = el.value; else d.text = el.value;
  }
  function onCardChange(event) {
    const el = event.target;
    if (!el.matches) return;
    const ctx = contextOf(el);
    if (!ctx) return;
    if (el.matches("[data-onb-remember]")) { ctx.card.remember = !!el.checked; return; }
    if (el.matches("[data-onb-toggle]")) {
      const t = TILES[el.dataset.onbToggle];
      if (!t || !t.set) return;
      Promise.resolve(t.set(!!el.checked)).catch((error) => say(error.message || String(error), "err")).finally(() => {
        rerender();
        refocus(`.onb-wrap [data-onb-toggle="${cssId(el.dataset.onbToggle)}"]`);
      });
      return;
    }
    if (el.matches("[data-onb-select='personality']")) {
      setPrefs({ ai_personality: el.value });
      say(L("Характер ответов изменён", "Reply style changed"), "ok");
    }
  }
  function onCardKey(event) {
    const el = event.target;
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || !el.matches || !el.matches("input[data-onb-text]")) return;
    const ctx = contextOf(el);
    if (!ctx || ctx.card.kind !== "survey") return;
    event.preventDefault();
    const submit = el.closest(".onb-card") && el.closest(".onb-card").querySelector("[data-onb='survey-submit']");
    if (submit) submit.click();
  }

  // ── Editing the first-run questions ─────────────────────────────────────
  const MAX_QUESTIONS = 8;
  const MAX_OPTIONS = 10;
  const TYPE_LABEL = { single: ["Один вариант", "One option"], multi: ["Несколько вариантов", "Several options"], text: ["Свой ответ текстом", "Free text answer"] };
  function plainSurvey(list) {
    return list.map((row) => ({ id: row.id, q: row.q, hint: row.hint || "", type: row.type, other: !!row.other, options: (row.options || []).map((option) => option.label) }));
  }
  function editorRowHtml(row, k, total) {
    const type = row.type;
    return `<div class="onb-ed-q" data-k="${k}">
      <div class="onb-ed-top"><span class="onb-q-n">${k + 1}</span>
        <input class="pinput onb-ed-text" data-ed="q" maxlength="200" value="${h(row.q)}" placeholder="${h(L("Текст вопроса", "Question text"))}" aria-label="${h(L("Вопрос", "Question") + " " + (k + 1))}">
        <button type="button" class="iconbtn onb-ed-btn" data-ed-act="up" title="${h(L("Выше", "Move up"))}" aria-label="${h(L("Выше", "Move up"))}"${k === 0 ? " disabled" : ""}>${ui("arrowUp")}</button>
        <button type="button" class="iconbtn onb-ed-btn" data-ed-act="down" title="${h(L("Ниже", "Move down"))}" aria-label="${h(L("Ниже", "Move down"))}"${k === total - 1 ? " disabled" : ""}>${ui("arrowDown")}</button>
        <button type="button" class="iconbtn onb-ed-btn" data-ed-act="del" title="${h(L("Удалить вопрос", "Delete the question"))}" aria-label="${h(L("Удалить вопрос", "Delete the question"))}">${ui("trash")}</button></div>
      <input class="pinput onb-ed-hint" data-ed="hint" maxlength="200" value="${h(row.hint || "")}" placeholder="${h(L("Пояснение под вопросом — необязательно", "A note under the question — optional"))}">
      <div class="onb-ed-meta"><select class="pinput compact" data-ed="type" aria-label="${h(L("Тип ответа", "Answer type"))}">${Object.keys(TYPE_LABEL).map((id) => `<option value="${id}"${id === type ? " selected" : ""}>${h(L(TYPE_LABEL[id][0], TYPE_LABEL[id][1]))}</option>`).join("")}</select>
        ${type === "text" ? "" : `<label class="onb-check"><input type="checkbox" data-ed="other"${row.other ? " checked" : ""}><span>${h(L("Вариант «Другое»", "An Other option"))}</span></label>`}</div>
      ${type === "text" ? "" : `<textarea class="pinput onb-ed-opts" data-ed="options" rows="${Math.min(6, Math.max(3, row.options.length))}" placeholder="${h(L("Варианты ответа — по одному в строке", "Answer options — one per line"))}">${h(row.options.join("\n"))}</textarea>
      <small class="onb-ed-small">${h(L(`До ${MAX_OPTIONS} вариантов, по одному в строке`, `Up to ${MAX_OPTIONS} options, one per line`))}</small>`}</div>`;
  }
  function openSurveyEditor() {
    if (typeof panel !== "function") return;
    let rows = plainSurvey(survey());
    const dev = Array.isArray(window.MEMUAI_ONBOARDING_SURVEY) && window.MEMUAI_ONBOARDING_SURVEY.length > 0;
    panel(`<h2>${h(L("Вопросы первого запуска", "First-run questions"))}</h2>
      <p class="sub">${h(L("Их задаёт ИИ после выбора модели, чтобы подобрать настройки. Можно менять формулировки и варианты, добавлять и удалять вопросы.", "The AI asks them after the model is chosen, to suggest settings. Change the wording and options, add or remove questions."))}</p>
      ${dev ? `<p class="onb-ed-note">${ui("info")}<span>${h(L("Сейчас действует список из сборки MemuAI. Ваши вопросы сохранятся и заработают, когда его уберут.", "A list from the MemuAI build is in effect now. Your questions are saved and take over once it is removed."))}</span></p>` : ""}
      <div class="onb-ed-list" id="onb-ed-list"></div>
      <button type="button" class="pbtn ghost onb-ed-add" id="onb-ed-add">${ui("plus")}<span>${h(L("Добавить вопрос", "Add a question"))}</span></button>
      <div class="row-end onb-ed-end"><button type="button" class="pbtn ghost" id="onb-ed-reset">${ui("undo")}<span>${h(L("Стандартные вопросы", "Default questions"))}</span></button><span class="grow"></span>
        <button type="button" class="pbtn ghost" id="onb-ed-cancel">${h(L("Отмена", "Cancel"))}</button><button type="button" class="pbtn" id="onb-ed-save">${h(L("Сохранить", "Save"))}</button></div>`, "onb-editor-panel");
    const dialog = q("#panel");
    const list = q("#onb-ed-list", dialog);
    const draw = () => {
      list.innerHTML = rows.map((row, k) => editorRowHtml(row, k, rows.length)).join("") || `<p class="onb-muted">${h(L("Вопросов нет. Добавьте хотя бы один или верните стандартные.", "No questions. Add at least one or bring back the defaults."))}</p>`;
      q("#onb-ed-add", dialog).disabled = rows.length >= MAX_QUESTIONS;
      if (typeof enhanceSelects === "function") enhanceSelects(list);
    };
    const rowOf = (el) => rows[+el.closest(".onb-ed-q").dataset.k];
    list.addEventListener("input", (event) => {
      const el = event.target;
      const row = el.closest(".onb-ed-q") && rowOf(el);
      if (!row) return;
      if (el.dataset.ed === "q") row.q = el.value;
      else if (el.dataset.ed === "hint") row.hint = el.value;
      else if (el.dataset.ed === "options") row.options = el.value.split("\n").slice(0, MAX_OPTIONS + 5);
    });
    list.addEventListener("change", (event) => {
      const el = event.target;
      const row = el.closest(".onb-ed-q") && rowOf(el);
      if (!row) return;
      if (el.dataset.ed === "other") row.other = el.checked;
      if (el.dataset.ed === "type") {
        row.type = el.value;
        if (row.type !== "text" && !row.options.filter((x) => String(x).trim()).length) row.options = [L("Да", "Yes"), L("Нет", "No")];
        const k = +el.closest(".onb-ed-q").dataset.k;
        draw();
        refocus(`#onb-ed-list [data-k="${k}"] [data-ed="type"]`);
      }
    });
    list.addEventListener("click", (event) => {
      const button = event.target.closest("[data-ed-act]");
      if (!button) return;
      const k = +button.closest(".onb-ed-q").dataset.k;
      const act = button.dataset.edAct;
      if (act === "del") rows.splice(k, 1);
      else if (act === "up" && k > 0) rows.splice(k - 1, 0, rows.splice(k, 1)[0]);
      else if (act === "down" && k < rows.length - 1) rows.splice(k + 1, 0, rows.splice(k, 1)[0]);
      draw();
      const to = act === "up" ? k - 1 : act === "down" ? k + 1 : Math.min(k, rows.length - 1);
      if (to >= 0) refocus(`#onb-ed-list [data-k="${to}"] ${act === "del" ? "[data-ed='q']" : `[data-ed-act='${act}']:not([disabled]), #onb-ed-list [data-k="${to}"] [data-ed='q']`}`);
    });
    q("#onb-ed-add", dialog).onclick = () => {
      if (rows.length >= MAX_QUESTIONS) return;
      rows.push({ id: "q" + Date.now().toString(36), q: "", hint: "", type: "single", other: false, options: [L("Да", "Yes"), L("Нет", "No")] });
      draw();
      refocus(`#onb-ed-list [data-k="${rows.length - 1}"] [data-ed='q']`);
    };
    q("#onb-ed-reset", dialog).onclick = () => { rows = plainSurvey(DEFAULT_SURVEY.map(normQuestion).filter(Boolean)); draw(); };
    q("#onb-ed-cancel", dialog).onclick = () => closePanel();
    q("#onb-ed-save", dialog).onclick = () => {
      const clean = [];
      for (const row of rows) {
        const text = String(row.q || "").trim();
        const options = row.type === "text" ? [] : [...new Set((row.options || []).map((x) => String(x).trim()).filter(Boolean))];
        if (!text && !options.length) continue;
        if (!text) { say(L("У вопроса нет текста", "A question has no text"), "warn"); return; }
        if (row.type !== "text" && options.length < 2 && !row.other) { say(L(`«${text}»: нужно хотя бы два варианта`, `“${text}”: at least two options are needed`), "warn"); return; }
        if (options.length > MAX_OPTIONS) { say(L(`«${text}»: не больше ${MAX_OPTIONS} вариантов`, `“${text}”: at most ${MAX_OPTIONS} options`), "warn"); return; }
        clean.push({ id: String(row.id || "").replace(/[^\w-]/g, "").slice(0, 40) || "q" + (clean.length + 1), q: text.slice(0, 200), hint: String(row.hint || "").trim().slice(0, 200), type: row.type, other: row.type !== "text" && !!row.other, options: options.map((x) => x.slice(0, 80)) });
      }
      if (!clean.length) { say(L("Оставьте хотя бы один вопрос — ответы всегда можно пропустить", "Keep at least one question — the answers can always be skipped"), "warn"); return; }
      const same = JSON.stringify(clean) === JSON.stringify(plainSurvey(DEFAULT_SURVEY.map(normQuestion).filter(Boolean)));
      setPrefs({ onboarding_survey_custom: same ? [] : clean });
      closePanel("confirm");
      refreshPendingSurvey();
      say(L("Вопросы сохранены", "Questions saved"), "ok");
    };
    draw();
  }
  function refreshPendingSurvey() {
    if (typeof S === "undefined" || !S.messages) return;
    const i = lastCardIndex();
    const card = i >= 0 ? S.messages[i].setupCard : null;
    if (!card || card.kind !== "survey" || card.state !== "pending") return;
    card.questions = survey();
    card.draft = {};
    persist();
    rerender();
  }

  // ── Entry points ────────────────────────────────────────────────────────
  async function resume() {
    if (blocked() || stage() !== "setup") return false;
    await new Promise((resolve) => setTimeout(resolve, 200));
    return beginSetup();
  }
  async function restart() {
    if (document.documentElement.dataset.moduleApp) return false;
    for (let n = 0; n < 8 && q("#panel-back") && q("#panel-back").classList.contains("visible"); n += 1) { try { closePanel("dismiss"); } catch (e) { break; } }
    if (typeof S !== "undefined" && inSetupChat()) {
      (S.messages || []).forEach((m) => { if (m && m.setupCard && m.setupCard.state === "pending") m.setupCard.state = "skipped"; });
    }
    setPrefs({ onboarding_stage: "setup", onboarding_v5_done: false });
    return beginSetup({ fresh: true });
  }
  function active() { return stage() === "setup"; }

  document.addEventListener("click", onCardClick);
  document.addEventListener("input", onCardInput);
  document.addEventListener("change", onCardChange);
  document.addEventListener("keydown", onCardKey);

  window.MemuOnboarding = Object.freeze({
    start, resume, restart, active, cardHtml, openSurveyEditor, survey, DEFAULT_SURVEY,
    // for tests and the developer console
    _sections: sectionsFor, _tiles: tilesFor,
  });
})();
