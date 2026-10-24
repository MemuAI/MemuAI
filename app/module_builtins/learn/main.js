/* MemuLearn — встроенный модуль MemuAI: ИИ-академия, интервальное повторение FSRS-5 и экзаменатор.
 * Код разбит на части (маркеры memu-part), MemuAI склеивает их в один main.js:
 *   md.js — Markdown, KaTeX (лениво, из MemuAI офлайн), подсветка кода, cloze;
 *   home.js — «Сегодня», боковая панель; create.js — новый курс (тема / книга / MemuNote / Anki, CSV);
 *   course.js — дерево модулей и уроков, конспект, карточки; review.js — сессия повторения;
 *   exam.js — экзаменатор; extra.js — статистика, настройки, импорт/экспорт.
 * Данные — app/learn (SQLite в данных MemuAI) через Memu.learn.call; к модели — Memu.request.
 * Все строки — в i18n.json (ru/en). */
const t = (key, vars) => Memu.t(key, vars);
const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const saved = Memu.saved || {};
const num = (value, opts) => Memu.i18n.number(value, opts);
const plural = (n, key) => t(key, { count: n });

const S = {
  view: "home", course: null, tree: [], lesson: null, overview: null, models: [],
  model: saved.model || "", prefs: Object.assign({ calm: false, cards_per_lesson: 8 }, saved.prefs || {}),
  last: saved.last || "", busy: null,
};
const main = $("#main");
const appEl = $("#app");

const API = {
  call(op, args) {
    if (!Memu.learn || !Memu.learn.call) return Promise.reject(new Error(t("err.bridge")));
    return Memu.learn.call(op, args || {});
  },
};

/* ------------------------------------------------------------------ the model */
async function loadModels() {
  try {
    const rows = await Memu.models();
    const list = (Array.isArray(rows) ? rows : []).filter((m) => !m.image && !m.video && !m.audio);
    S.models = list;
    if (!S.model || !list.some((m) => m.id === S.model)) S.model = (list.find((m) => m.default) || list[0] || {}).id || "";
  } catch (e) { S.models = []; }
  return S.models;
}

/** Ask the model for one of app/learn/ai.py tasks and apply the JSON answer on the server. */
async function aiTask(task, args, onStage) {
  if (!S.models.length) await loadModels();
  const p = await API.call("ai.prompt", Object.assign({ task }, args));
  onStage && onStage("think");
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    const answer = await Memu.request({ model: S.model || undefined, instructions: p.instructions, text: p.text,
      temperature: p.temperature, title: "MemuLearn" });
    const text = String((answer && (answer.text || answer.answer)) || "");
    onStage && onStage("save");
    try {
      return await API.call("ai.apply", Object.assign({ task, answer: text }, args));
    } catch (e) {
      lastError = e;
      if (!/format|формат/i.test(String(e.message || ""))) throw e;
      onStage && onStage("retry");
    }
  }
  throw lastError;
}

/* ------------------------------------------------------------------ helpers */
function toast(text, kind) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "ml-toast" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, kind === "error" ? 6000 : 3200);
}
function fail(error) { toast((error && error.message) || String(error), "error"); }

function hueStyle(h) { return `--h:${Number(h) || 210}`; }
function ring(value, size, stroke) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, v = Math.max(0, Math.min(1, value || 0));
  return `<svg class="ml-ring" viewBox="0 0 ${size} ${size}" style="width:${size}px;height:${size}px" aria-hidden="true">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}" class="ml-ring-bg"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${stroke}" class="ml-ring-fg" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - v)).toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})"/></svg>`;
}
function icon(name) {
  const paths = {
    play: '<path d="M8 5.5v13l10.5-6.5z"/>', plus: '<path d="M12 5v14M5 12h14"/>', back: '<path d="M15 5l-7 7 7 7"/>',
    spark: '<path d="M12 3.5l1.8 5.2 5.2 1.8-5.2 1.8L12 17.5l-1.8-5.2L5 10.5l5.2-1.8z"/>', exam: '<path d="M9 4h6l1 2h3v14H5V6h3z"/><path d="M9 12l2 2 4-4"/>',
    cards: '<rect x="4" y="7" width="13" height="13" rx="2.5"/><path d="M8 4h9.5A2.5 2.5 0 0 1 20 6.5V16"/>', trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>', check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', upload: '<path d="M12 16V5M7 10l5-5 5 5M5 20h14"/>', undo: '<path d="M9 7 4 12l5 5"/><path d="M4 12h10a6 6 0 0 1 0 12"/>',
    book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>',
    note: '<path d="M5 3h10l4 4v14H5z"/><path d="M15 3v4h4M8 12h8M8 16h6"/>', topic: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5v5l3 2"/>',
    anki: '<rect x="3.5" y="5" width="12" height="15" rx="2"/><path d="M8.5 5V3.5h10A2 2 0 0 1 20.5 5.5V17"/><path d="M9.5 9.5l.9 1.9 2 .3-1.5 1.4.4 2-1.8-1-1.8 1 .4-2-1.5-1.4 2-.3z"/>',
    flame: '<path d="M12 21c4 0 7-2.7 7-6.6 0-4-3.3-6.2-4.5-10.4-2 1.6-3 3.6-3 5.6-1-.8-1.7-2-2-3.3C7.7 8 5 10.8 5 14.4 5 18.3 8 21 12 21z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>', stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>', x: '<path d="m6 6 12 12M18 6 6 18"/>',
    chev: '<path d="m9 6 6 6-6 6"/>', code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  };
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
}
function fmtMinutes(min) { return min >= 60 ? t("dur.h_m", { h: Math.floor(min / 60), m: min % 60 }) : t("dur.m", { m: Math.max(1, min) }); }
function ago(at) {
  if (!at) return "";
  return Memu.i18n.date(at * 1000, { day: "numeric", month: "short" });
}
function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error(t("err.read")));
    reader.readAsDataURL(file);
  });
}
function readText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error(t("err.read")));
    reader.readAsText(file);
  });
}
function remember() {
  try { Memu.save({ model: S.model, prefs: S.prefs, last: S.last }); } catch (e) { /* storage is optional */ }
}
function setBusy(key) { S.busy = key; appEl.toggleAttribute("data-busy", !!key); }

/* ------------------------------------------------------------------ router */
const VIEWS = {};
async function go(view, params) {
  S.view = view;
  appEl.dataset.view = view;
  $$(".ml-nav-item").forEach((b) => b.classList.toggle("on", b.dataset.go === view));
  $$(".ml-course-link").forEach((b) => b.classList.toggle("on", view === "course" && S.course && b.dataset.course === S.course.id));
  const fn = VIEWS[view] || VIEWS.home;
  try { await fn(params || {}); } catch (e) { fail(e); }
  main.scrollTop = 0;
  if (Memu.toolbar && Memu.toolbar.supported && Memu.toolbar.supported()) {
    Memu.toolbar.title(view === "course" && S.course ? S.course.title : t("view." + view));
  }
}
appEl.addEventListener("click", (e) => {
  const b = e.target.closest("[data-go]");
  if (b) { e.preventDefault(); go(b.dataset.go, { id: b.dataset.course || "" }); }
});

/* @memu-part:md.js */
/* @memu-part:home.js */
/* @memu-part:create.js */
/* @memu-part:course.js */
/* @memu-part:review.js */
/* @memu-part:exam.js */
/* @memu-part:extra.js */

/* ------------------------------------------------------------------ start */
function applyPrefs() { document.documentElement.toggleAttribute("data-calm", !!S.prefs.calm || matchMedia("(prefers-reduced-motion: reduce)").matches); }
applyPrefs();
Memu.onLanguage(() => go(S.view, { id: S.course ? S.course.id : "" }));
Memu.on("navigate", (data) => {
  const d = data || {};
  if (d.route === "review" || d.command === "review") return startReview({ courseId: d.course || "" });
  if (d.course) return go("course", { id: d.course });
  go("home");
});
window.addEventListener("pagehide", remember);
document.addEventListener("keydown", (e) => { if (S.view === "review") reviewKey(e); }, true);
refreshSide().then(() => go("home"));
loadModels();
if (typeof Memu.ready === "function") Memu.ready();
