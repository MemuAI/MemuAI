"use strict";
/* MemuUtils window: the catalogue comes from the host (Memu.utils.call("catalog")), every form is built from the
 * utility spec and its presentation hints (row.ui: label, examples, one-line or box input, run verb), runs go through
 * Memu.utils.call("run"), AI utilities through Memu.stream with the chosen model.
 * What is already on screen is patched in place: a star, a switch or a job poll never rebuilds (and re-animates) a list. */

const t = (key, vars) => Memu.t(key, vars);
const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g,
  (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const num = (value, options) => Memu.i18n.number(value, options);
const call = (op, args) => Memu.utils.call(op, args || {});

const INLINE_LIMIT = 4 * 1024 * 1024;      // smaller files travel inside the run request
const CHUNK = 1536 * 1024;                  // bigger ones are uploaded in parts first
const TEXT_SHOW = 200000;                   // characters of a text result drawn on screen
const TABLE_SHOW = 2000;                    // table rows drawn on screen
const LIVE_LIMIT = 100000;                  // live utilities re-run while typing up to this size
const SKILL_HEAD_TOKENS = 290;              // the fixed part of the AI tool list (rules, file refs)
const HOVER_DELAY = 420;                    // the card description pops up after this pause
const ANSWER_MAX = 160;                     // a one-line text result up to this length is shown as a big answer
const STAGGER = 24;                         // only the first cards of a fresh list animate in

const ICON = {
  all: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  fav: '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  recent: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  text: '<path d="M5 7V5h14v2M9.5 19h5M12 5v14"/>',
  format: '<path d="M4 6h16M8 10h12M4 14h16M8 18h12"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  dev: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M12.5 15h4.5"/>',
  check: '<path d="M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
  convert: '<path d="M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7"/>',
  gen: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6.3 6.3l2.5 2.5M15.2 15.2l2.5 2.5M6.3 17.7l2.5-2.5M15.2 8.8l2.5-2.5"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.6"/><path d="m4 18 5-5 4 4 3-3 4 4"/>',
  audio: '<path d="M3 12h1.5M7 8.5v7M11 5v14M15 8v8M19 10.5v3M21 12h0"/>',
  video: '<rect x="3" y="5.5" width="13" height="13" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
  pdf: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M8.5 13h7M8.5 17h5"/>',
  docs: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 0 7.5 23H19v-5M9 7.5h6"/>',
  data: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 14.5h18M9 4v16"/>',
  net: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/>',
  system: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5"/>',
  ai: '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="M19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  save: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M4 17v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  next: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  reuse: '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  files: '<path d="M15 3H9a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7z"/><path d="M15 3v4h4M4 8v11a2 2 0 0 0 2 2h8"/>',
  play: '<path d="M7 5v14l11-7z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.8v.4"/>',
  bolt: '<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4M12 17v.4"/>',
  wand: '<path d="M4.5 19.5 14 10"/><path d="m15.5 3.5.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8zM19.5 11l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5z"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  windows: '<path d="M3.5 5.5 10.5 4.5v7h-7zM13 4.2l7.5-1.1V11.5H13zM3.5 13h7v6.5l-7-1zM13 13h7.5v8l-7.5-1.1z"/>',
};
const HUE = { text: 212, format: 192, code: 262, dev: 232, check: 150, convert: 32, gen: 318, image: 282, audio: 14,
  video: 348, pdf: 2, docs: 204, data: 140, net: 176, system: 222, ai: 268, all: 222, fav: 42, recent: 200 };
const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ICON.all}</svg>`;
const LOCAL_NET = new Set(["net.subnet"]);

const saved = Memu.saved || {};
const prefs = Object.assign({ opts: {}, cat: "all", layout: "grid", topOpen: true },
  saved.prefs && typeof saved.prefs === "object" ? saved.prefs : {});
if (!prefs.opts || typeof prefs.opts !== "object") prefs.opts = {};

const S = {
  loaded: false, utils: [], byId: new Map(), cats: [], settings: {}, skill: {}, jobs: [], platform: "", top: [], recommended: [],
  view: "list", cat: prefs.cat || "all", q: "", tool: null, files: [], carry: null,
  runs: new Map(), stream: null, streamFor: "",
  results: new Map(), models: null, modelState: "idle", texts: { plain: "", folder: "", second: "" }, lineTexts: {},
  cards: new Map(), aiQuery: "", aiOnly: false, aiSaveTimer: 0, jobTimer: 0, liveTimer: 0, prefsTimer: 0, runTimer: 0,
  searchTimer: 0, viewerFile: null, confirmResolve: null, lastFocus: null, fileKey: 0,
};

const el = {
  app: $("#app"), q: $("#q"), cats: $("#cats"), grid: $("#grid"), empty: $("#empty"), emptyTitle: $("#empty-title"),
  emptyText: $("#empty-text"), listTitle: $("#list-title"), listSub: $("#list-sub"), layoutSeg: $("#layout-seg"),
  carry: $("#carry"), carryText: $("#carry-text"), aiBadge: $("#ai-badge"), jobsBadge: $("#jobs-badge"),
  top: $("#top"), topGrid: $("#top-grid"), topFold: $("#top-fold"), topFoldText: $("#top-fold-text"),
  views: { list: $("#view-list"), tool: $("#view-tool"), ai: $("#view-ai"), jobs: $("#view-jobs") },
  toolIco: $("#tool-ico"), toolCat: $("#tool-cat"), toolTitle: $("#tool-title"), toolDesc: $("#tool-desc"), traits: $("#tool-traits"),
  toolAi: $("#tool-ai"), toolAiWrap: $("#tool-ai-wrap"), fav: $("#fav"),
  inText: $("#in-text"), textLabel: $("#text-label"), text: $("#text"), textCount: $("#text-count"),
  text2Wrap: $("#text2-wrap"), text2: $("#text2"), label2: $("#label2"), text2Count: $("#text2-count"),
  folderRow: $("#folder-row"), folder: $("#folder"), pickFolder: $("#pick-folder"), examples: $("#examples"), examples2: $("#examples2"),
  paste: $("#paste"), loadText: $("#load-text"), clearText: $("#clear-text"), textFile: $("#text-file"),
  inFiles: $("#in-files"), drop: $("#drop"), dropTitle: $("#drop-title"), dropHint: $("#drop-hint"), flist: $("#flist"),
  fileInput: $("#file-input"), optsWrap: $("#opts-wrap"), opts: $("#opts"), optsReset: $("#opts-reset"),
  modelWrap: $("#model-wrap"), model: $("#model"), modelHint: $("#model-hint"),
  confirm: $("#confirm"), confirmText: $("#confirm-text"), confirmYes: $("#confirm-yes"), confirmNo: $("#confirm-no"),
  run: $("#run"), runLabel: $("#run-label"), stop: $("#stop"), status: $("#status"), progress: $("#progress"), out: $("#out"),
  aiEnabled: $("#ai-enabled"), aiCount: $("#ai-count"), aiTokens: $("#ai-tokens"), aiMeter: $("#ai-meter"),
  aiGroups: $("#ai-groups"), aiQ: $("#ai-q"), aiOnly: $("#ai-only"), aiPreview: $("#ai-preview"), aiPreviewBox: $("#ai-preview-box"),
  aiDefaults: $("#ai-defaults"), aiAll: $("#ai-all"), aiNone: $("#ai-none"),
  jobsList: $("#jobs-list"), viewer: $("#viewer"), viewerTitle: $("#viewer-title"), viewerBody: $("#viewer-body"),
  viewerSave: $("#viewer-save"), toast: $("#toast"), hover: $("#hover"),
};

/* ── small helpers ─────────────────────────────────────────────────────────────────────────────────────────────── */
function fmtTokens(n) {
  const value = Number(n) || 0;
  return value >= 1000 ? t("tokens.k", { n: num(value / 1000, { maximumFractionDigits: 1 }) }) : t("tokens.n", { n: num(value) });
}
function fmtBytes(n) {
  const units = [t("unit.b"), t("unit.kb"), t("unit.mb"), t("unit.gb")];
  let value = Number(n) || 0, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${num(value, { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}
function fmtMs(ms) {
  const value = Number(ms) || 0;
  return value < 1000 ? t("time.ms", { n: num(Math.round(value)) }) : t("time.s", { n: num(value / 1000, { maximumFractionDigits: 1 }) });
}
function restartAnim(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
}
let toastTimer = 0;
function showToast(kind, ms) {
  el.toast.dataset.kind = kind || "";
  el.toast.hidden = false;
  restartAnim(el.toast, "show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.classList.remove("show"); setTimeout(() => { el.toast.hidden = true; }, 220); }, ms);
}
function toast(text, kind) {
  el.toast.textContent = text;
  showToast(kind, kind === "error" ? 5200 : 2600);
}
function setStatus(text, kind) {
  el.status.textContent = text || "";
  el.status.dataset.kind = kind || "";
  el.status.title = text || "";
}
function errorText(error) {
  return String((error && error.message) || error || t("error.generic"));
}
async function copyText(text, what) {
  try {
    await Memu.copy(String(text));
    toast(what ? t("copied.what", { what }) : t("copied"), "ok");
  } catch (error) { toast(errorText(error), "error"); }
}
function savePrefs() {
  clearTimeout(S.prefsTimer);
  S.prefsTimer = setTimeout(() => { S.prefsTimer = 0; Memu.save({ prefs }).catch(() => {}); }, 500);
}
function readAs(blob, how) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error(t("file.read_error")));
    if (how === "text") reader.readAsText(blob); else reader.readAsDataURL(blob);
  });
}
const readAsDataURL = (blob) => readAs(blob, "url");
const readAsText = (blob) => readAs(blob, "text");
function extOf(name) {
  const m = /\.([a-z0-9]{1,8})$/i.exec(String(name || ""));
  return m ? m[1].toLowerCase() : "";
}
function accepts(u, name, mime) {
  if (!u || !["file", "files", "any"].includes(u.input)) return false;
  const accept = String(u.accept || "").trim();
  if (!accept) return true;
  const ext = "." + extOf(name);
  const type = String(mime || "").toLowerCase();
  return accept.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean).some((rule) =>
    rule.startsWith(".") ? rule === ext : rule.endsWith("/*") ? type.startsWith(rule.slice(0, -1)) || guessKind(name) === rule.slice(0, -2) : rule === type);
}
function guessKind(name) {
  const ext = extOf(name);
  if (/^(png|jpe?g|webp|gif|bmp|tiff?|ico|avif|heic|heif|svg|psd)$/.test(ext)) return "image";
  if (/^(mp3|wav|flac|ogg|oga|opus|m4a|aac|wma|aiff?)$/.test(ext)) return "audio";
  if (/^(mp4|mkv|mov|webm|avi|m4v|wmv|flv|3gp|mpe?g|ts)$/.test(ext)) return "video";
  return "file";
}
function catTitle(id) {
  const row = S.cats.find((c) => c.id === id);
  return row ? row.title : id;
}
function favIds() { return (S.settings.favorites || []).filter((id) => S.byId.has(id)); }
function recentIds() { return (S.settings.recent || []).filter((id) => S.byId.has(id)); }
function isFav(id) { return (S.settings.favorites || []).includes(id); }
function aiOn(u) { return S.settings.ai_enabled !== false && !!u.ai_on; }
function hueOf(cat) { return HUE[cat] != null ? HUE[cat] : 222; }
function uiOf(u) { return (u && u.ui) || {}; }
function isLine(u) { return !!u && u.input === "text" && u.pick !== "folder" && uiOf(u).size === "line"; }
function focusSoft(node) {
  if (!node) return;
  try { node.focus({ preventScroll: true }); } catch (e) { node.focus(); }
}

/* ── catalogue ─────────────────────────────────────────────────────────────────────────────────────────────────── */
async function load() {
  try {
    const c = await call("catalog");
    S.utils = Array.isArray(c.utils) ? c.utils : [];
    S.byId = new Map(S.utils.map((u) => [u.id, u]));
    S.cats = Array.isArray(c.categories) ? c.categories : [];
    S.settings = c.settings || {};
    S.skill = c.skill || {};
    S.jobs = Array.isArray(c.jobs) ? c.jobs : [];
    S.platform = c.platform || "";
    S.top = (Array.isArray(c.top) ? c.top : []).filter((id) => S.byId.has(id));
    S.recommended = (Array.isArray(c.recommended) ? c.recommended : []).filter((id) => S.byId.has(id));
    S.cards.clear();
    el.topGrid.dataset.sig = "";
    S.loaded = true;
    if (!["all", "fav", "recent"].includes(S.cat) && !S.cats.some((row) => row.id === S.cat)) S.cat = "all";
    el.app.dataset.ready = "1";
    renderSide();
    renderJobsBadge();
    if (S.view === "tool" && S.tool && S.byId.has(S.tool.id)) openTool(S.tool.id, { keep: true });
    else renderList({ animate: true });
    if (S.view === "ai") renderAi();
    if (S.view === "jobs") renderJobs();
    if (S.jobs.some((job) => job.running)) pollJobs();
  } catch (error) {
    el.grid.replaceChildren();
    showEmpty(t("load.error"), errorText(error));
  }
}

/* ── sidebar ───────────────────────────────────────────────────────────────────────────────────────────────────── */
function sideRows() {
  return [
    { id: "all", title: t("cat.all"), count: S.utils.length },
    { id: "fav", title: t("cat.fav"), count: favIds().length },
    { id: "recent", title: t("cat.recent"), count: recentIds().length },
    null,
    ...S.cats.map((c) => ({ id: c.id, title: c.title, count: c.count })),
  ];
}
function renderSide() {
  el.cats.innerHTML = sideRows().map((row) => row
    ? `<button type="button" class="ut-cat" data-cat="${esc(row.id)}" aria-current="false" style="--h:${hueOf(row.id)}">
        <span class="ut-cat-ico">${svg(row.id)}</span><span class="ut-cat-name">${esc(row.title)}</span><span class="ut-cat-n" data-n="${esc(row.id)}">${num(row.count)}</span></button>`
    : '<div class="ut-cat-sep" role="separator"></div>').join("");
  patchSide();
}
/* The highlighted section follows every change — a click while the list is already open included. */
function patchSide() {
  const current = S.view === "list" && !S.q.trim() ? S.cat : "";
  $$(".ut-cat", el.cats).forEach((btn) => {
    const value = btn.dataset.cat === current ? "page" : "false";
    if (btn.getAttribute("aria-current") !== value) btn.setAttribute("aria-current", value);
  });
  const fav = $('[data-n="fav"]', el.cats), recent = $('[data-n="recent"]', el.cats);
  if (fav) fav.textContent = num(favIds().length);
  if (recent) recent.textContent = num(recentIds().length);
  el.aiBadge.textContent = S.settings.ai_enabled === false ? t("ai.badge.off")
    : S.skill.tools ? t("ai.badge", { n: num(S.skill.tools), tokens: fmtTokens(S.skill.tokens || 0) }) : t("ai.badge.none");
  $$("[data-view-btn]").forEach((btn) => btn.setAttribute("aria-current", S.view === btn.dataset.viewBtn ? "page" : "false"));
}

/* ── search ────────────────────────────────────────────────────────────────────────────────────────────────────── */
function stem(word) {
  return word.length >= 6 ? word.slice(0, word.length - 2) : word;
}
function scoreOf(u, words) {
  const title = u.title.toLowerCase(), desc = String(u.desc || "").toLowerCase(), keys = String(u.keys || ""), id = u.id.toLowerCase();
  let score = 0;
  for (const word of words) {
    const w = stem(word);
    if (title.startsWith(w)) score += 9;
    else if (title.includes(w)) score += 6;
    else if (id.includes(w)) score += 4;
    else if (keys.includes(w)) score += 3;
    else if (desc.includes(w)) score += 2;
    else return 0;
  }
  return score + (isFav(u.id) ? 1 : 0);
}
function visibleUtils() {
  const query = S.q.trim().toLowerCase();
  let rows = S.utils;
  if (S.carry) rows = rows.filter((u) => S.carry.files.every((f) => accepts(u, f.name, f.mime)));
  if (query) {
    const words = query.split(/\s+/).filter(Boolean);
    return rows.map((u) => [u, scoreOf(u, words)]).filter((pair) => pair[1] > 0).sort((a, b) => b[1] - a[1]).map((pair) => pair[0]);
  }
  if (S.cat === "fav") return favIds().map((id) => S.byId.get(id)).filter((u) => rows.includes(u));
  if (S.cat === "recent") return recentIds().map((id) => S.byId.get(id)).filter((u) => rows.includes(u));
  if (S.cat !== "all") return rows.filter((u) => u.cat === S.cat);
  return rows;
}

/* ── cards (built once per language, then moved and patched) ───────────────────────────────────────────────────── */
function badgesHtml(u) {
  const out = [];
  if (aiOn(u)) out.push(`<i class="ut-b ai" title="${esc(t("badge.ai.hint"))}">${esc(t("badge.ai"))}</i>`);
  if (u.prompt) out.push(`<i class="ut-b model" title="${esc(t("badge.model.hint"))}">${svg("ai")}</i>`);
  if (u.platform === "win") out.push(`<i class="ut-b win" title="${esc(t("badge.win.hint"))}">Windows</i>`);
  if (u.danger || u.danger_if) out.push(`<i class="ut-b warn" title="${esc(t("badge.danger.hint"))}">${svg("warn")}</i>`);
  return out.join("");
}
function starAttrs(fav) {
  const label = esc(t(fav ? "fav.remove" : "fav.add"));
  return `aria-pressed="${fav}" aria-label="${label}" title="${label}"`;
}
function cardHtml(u) {
  return `<div class="ut-card${u.available ? "" : " off"}" role="listitem" data-id="${esc(u.id)}" style="--h:${hueOf(u.cat)}">
    <button type="button" class="ut-card-open" data-open="${esc(u.id)}"${u.available ? "" : ` title="${esc(t("badge.win.only"))}"`}>
      <span class="ut-card-ico">${svg(u.cat)}</span>
      <span class="ut-card-text"><b>${esc(u.title)}</b><small>${esc(u.desc)}</small></span>
    </button>
    <span class="ut-card-badges">${badgesHtml(u)}</span>
    <button type="button" class="ut-star" data-fav="${esc(u.id)}" ${starAttrs(isFav(u.id))}>${svg("fav")}</button>
  </div>`;
}
function cardNode(u) {
  let node = S.cards.get(u.id);
  if (!node) {
    const box = document.createElement("div");
    box.innerHTML = cardHtml(u);
    node = box.firstElementChild;
    S.cards.set(u.id, node);
  }
  return node;
}
function placeCard(u, index, animate) {
  const node = cardNode(u);
  const stagger = animate && index < STAGGER;
  node.classList.toggle("ut-a", stagger);
  if (stagger) node.style.setProperty("--i", index); else node.style.removeProperty("--i");
  return node;
}
function patchStar(btn, fav, pop) {
  btn.setAttribute("aria-pressed", String(fav));
  btn.title = t(fav ? "fav.remove" : "fav.add");
  btn.setAttribute("aria-label", btn.title);
  if (pop && fav) restartAnim(btn, "ut-popping");
}
function patchCards() {
  S.cards.forEach((node, id) => {
    const u = S.byId.get(id);
    if (!u) return;
    const badges = $(".ut-card-badges", node);
    const html = badgesHtml(u);
    if (badges.innerHTML !== html) badges.innerHTML = html;
    patchStar($(".ut-star", node), isFav(id), false);
  });
}
function groupHead(cat, count) {
  const head = document.createElement("div");
  head.className = "ut-group-head";
  head.setAttribute("role", "presentation");
  head.style.setProperty("--h", hueOf(cat.id));
  head.innerHTML = `<span class="ut-cat-ico">${svg(cat.id)}</span><b>${esc(cat.title)}</b><span>${num(count)}</span>
    <button type="button" class="ut-mini" data-cat="${esc(cat.id)}">${esc(t("list.open_cat"))}${svg("next")}</button>`;
  return head;
}
function showEmpty(title, text) {
  el.empty.hidden = false;
  el.emptyTitle.textContent = title;
  el.emptyText.textContent = text || "";
}
function listTitles(rows, query) {
  el.listTitle.textContent = query ? t("list.search", { q: query }) : S.cat === "all" ? t("list.all") : S.cat === "fav" ? t("cat.fav")
    : S.cat === "recent" ? t("cat.recent") : catTitle(S.cat);
  el.listSub.textContent = query ? t("list.found", { n: num(rows.length) })
    : S.cat === "all" ? t("list.all.sub", { n: num(S.utils.length), cats: num(S.cats.length) }) : t("list.count", { n: num(rows.length) });
}
/* animate: only a new section or the first paint fades the cards in; typing in the search box just swaps them. */
function renderList(options) {
  if (!S.loaded) return;
  const o = options || {};
  const rows = visibleUtils();
  const query = S.q.trim();
  hideHover();
  listTitles(rows, query);
  el.grid.dataset.layout = prefs.layout === "rows" ? "rows" : "grid";
  $$("[data-layout]", el.layoutSeg).forEach((btn) => btn.setAttribute("aria-checked", String(btn.dataset.layout === el.grid.dataset.layout)));
  el.carry.hidden = !S.carry;
  if (S.carry) el.carryText.textContent = t("carry.text", { name: S.carry.files.map((f) => f.name).join(", ") });
  renderTop(!query && S.cat === "all" && !S.carry, !!o.animate);
  el.empty.hidden = true;
  if (!rows.length) {
    el.grid.replaceChildren();
    if (query) showEmpty(t("empty.search"), t("empty.search.hint"));
    else if (S.cat === "fav") showEmpty(t("empty.fav"), t("empty.fav.hint"));
    else if (S.cat === "recent") showEmpty(t("empty.recent"), t("empty.recent.hint"));
    else showEmpty(t("empty.search"), "");
    return;
  }
  const animate = !!o.animate;
  const nodes = [];
  if (S.cat === "all" && !query && !S.carry) {
    let index = 0;
    S.cats.forEach((cat) => {
      const items = rows.filter((u) => u.cat === cat.id);
      if (!items.length) return;
      nodes.push(groupHead(cat, items.length));
      items.forEach((u) => nodes.push(placeCard(u, index++, animate)));
    });
  } else rows.forEach((u, i) => nodes.push(placeCard(u, i, animate)));
  el.grid.replaceChildren(...nodes);
}
function tileHtml(u, i) {
  return `<button type="button" class="ut-tile${u.available ? "" : " off"}" data-open="${esc(u.id)}" style="--h:${hueOf(u.cat)};--i:${i}">
    <span class="ut-tile-ico">${svg(u.cat)}</span><span class="ut-tile-text"><b>${esc(u.title)}</b><small>${esc(catTitle(u.cat))}</small></span></button>`;
}
function renderTop(show, animate) {
  const ids = S.top.filter((id) => S.byId.has(id));
  el.top.hidden = !show || !ids.length;
  if (el.top.hidden) return;
  const open = prefs.topOpen !== false;
  el.top.classList.toggle("closed", !open);
  el.topFold.setAttribute("aria-expanded", String(open));
  el.topFoldText.textContent = t(open ? "top.hide" : "top.show");
  const sig = ids.join(",");
  if (el.topGrid.dataset.sig !== sig) {
    el.topGrid.innerHTML = ids.map((id, i) => tileHtml(S.byId.get(id), i)).join("");
    el.topGrid.dataset.sig = sig;
  }
  el.top.classList.toggle("ut-anim", !!animate);
}

/* ── hover card: the whole description and what the utility needs ──────────────────────────────────────────────── */
function acceptNames(u) {
  const accept = String(u.accept || "").split(",").map((s) => s.trim()).filter(Boolean);
  const names = accept.map((rule) => rule === "image/*" ? t("kind.images") : rule === "audio/*" ? t("kind.audio")
    : rule === "video/*" ? t("kind.video") : rule.replace(/^\./, "").toUpperCase());
  return names.length ? names.slice(0, 6).join(", ") + (names.length > 6 ? "…" : "") : t("kind.any");
}
function traitsOf(u) {
  const out = [];
  if (u.input === "none") out.push({ icon: "sliders", text: t("trait.none") });
  else if (u.input === "file") out.push({ icon: "file", text: t("trait.file", { what: acceptNames(u) }) });
  else if (u.input === "files") out.push({ icon: "files", text: t("trait.files", { what: acceptNames(u) }) });
  else if (u.input === "any") out.push({ icon: "text", text: t("trait.any") });
  else if (u.input === "text2") out.push({ icon: "text", text: t("trait.text2") });
  else if (u.pick === "folder") out.push({ icon: "folder", text: t("trait.folder") });
  else out.push({ icon: "text", text: t("trait.text") });
  if (u.live) out.push({ icon: "bolt", text: t(u.input === "none" ? "trait.live.none" : "trait.live"), kind: "live" });
  if (u.prompt) out.push({ icon: "ai", text: t("trait.model"), kind: "ai" });
  else if (u.cat === "net" && !LOCAL_NET.has(u.id)) out.push({ icon: "net", text: t("trait.net") });
  else out.push({ icon: "lock", text: t("trait.local") });
  if (aiOn(u)) out.push({ icon: "ai", text: t("trait.ai_on"), kind: "ai" });
  if (u.danger || u.danger_if) out.push({ icon: "warn", text: t(u.danger ? "trait.danger" : "trait.danger_if"), kind: "warn" });
  if (u.platform === "win") out.push({ icon: "windows", text: t("trait.win"), kind: u.available ? "" : "warn" });
  return out;
}
function traitHtml(f) {
  return `<span class="ut-trait${f.kind ? " " + f.kind : ""}">${svg(f.icon)}<span>${esc(f.text)}</span></span>`;
}
const HOVER = { timer: 0, anchor: null };
function scheduleHover(anchor, delay) {
  if (HOVER.anchor === anchor) return;
  clearTimeout(HOVER.timer);
  HOVER.anchor = anchor;
  HOVER.timer = setTimeout(() => showHover(anchor), el.hover.hidden ? (delay == null ? HOVER_DELAY : delay) : 80);
}
function showHover(anchor) {
  if (!anchor.isConnected || S.view !== "list") return;
  const u = S.byId.get(anchor.dataset.open);
  if (!u) return;
  el.hover.style.setProperty("--h", hueOf(u.cat));
  el.hover.innerHTML = `<div class="ut-hover-head"><span class="ut-cat-ico">${svg(u.cat)}</span><span class="ut-hover-titles"><b>${esc(u.title)}</b>
      <small>${esc(catTitle(u.cat))}</small></span></div>
    <p>${esc(u.desc)}</p>
    <div class="ut-hover-traits">${traitsOf(u).map(traitHtml).join("")}</div>`;
  el.hover.hidden = false;
  const box = anchor.closest(".ut-card") || anchor;
  const r = box.getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  const width = Math.min(360, vw - 16);
  el.hover.style.width = `${width}px`;
  const h = el.hover.offsetHeight;
  const left = Math.max(8, Math.min(r.left + Math.min(24, r.width / 6), vw - width - 8));
  let top = r.bottom + 6;
  if (top + h > vh - 8) top = r.top - h - 6;
  if (top < 8) top = Math.max(8, Math.min(vh - h - 8, r.bottom + 6));
  el.hover.style.left = `${Math.round(left)}px`;
  el.hover.style.top = `${Math.round(top)}px`;
  el.hover.dataset.side = top < r.top ? "up" : "down";
  restartAnim(el.hover, "show");
}
function hideHover() {
  clearTimeout(HOVER.timer);
  HOVER.anchor = null;
  if (!el.hover.hidden) { el.hover.hidden = true; el.hover.classList.remove("show"); }
}

/* ── views ─────────────────────────────────────────────────────────────────────────────────────────────────────── */
function setView(view) {
  if (S.view === view) { patchSide(); return; }
  S.view = view;
  hideHover();
  el.app.dataset.view = view;
  Object.entries(el.views).forEach(([name, node]) => {
    const show = name === view;
    node.hidden = !show;
    if (show) restartAnim(node, "ut-enter");
  });
  if (view !== "tool") closeConfirm(false);
  patchSide();
  if (view === "ai") renderAi();
  if (view === "jobs") { renderJobs(); pollJobs(); }
}
function goList(options) {
  setView("list");
  renderList({ animate: !!(options && options.animate) });
  patchSide();
}

/* ── tool view ─────────────────────────────────────────────────────────────────────────────────────────────────── */
function toolLayout(u) {
  if (u.input === "none" || u.pick === "folder" || isLine(u)) return "stack";
  return "split";
}
function stashText() {
  const u = S.tool;
  if (!u) return;
  if (u.pick === "folder") S.texts.folder = el.folder.value;
  else if (isLine(u)) S.lineTexts[u.id] = el.text.value;
  else S.texts.plain = el.text.value;
  S.texts.second = el.text2.value;
}
function storedText(u) {
  return u.pick === "folder" ? S.texts.folder : isLine(u) ? (S.lineTexts[u.id] || "") : S.texts.plain;
}
function hasText() {
  return !!(el.text.value.trim() || (S.tool && S.tool.input === "text2" && el.text2.value.trim()));
}
function openTool(id, options) {
  const o = options || {};
  const u = S.byId.get(id);
  if (!u) return;
  hideHover();
  if (!o.keep) stashText();
  const changed = !S.tool || S.tool.id !== id;
  S.tool = u;
  if (changed && !o.keep) {
    S.files = S.carry && S.carry.files.every((f) => accepts(u, f.name, f.mime)) ? S.carry.files.slice() : S.files.filter((f) => accepts(u, f.name, f.mime));
    if (u.input === "file") S.files = S.files.slice(0, 1);
    S.files.forEach((f) => { f.fresh = true; });
    S.carry = null;
  }
  setView("tool");
  const tool = el.views.tool;
  tool.style.setProperty("--h", hueOf(u.cat));
  tool.dataset.layout = toolLayout(u);
  tool.dataset.input = u.input;
  el.toolIco.innerHTML = svg(u.cat);
  el.toolCat.textContent = catTitle(u.cat);
  el.toolTitle.textContent = u.title;
  el.toolDesc.textContent = u.desc;
  syncToolHead(u);
  renderInputs(u);
  renderOpts(u, !o.keep);
  renderModelRow(u);
  closeConfirm(false);
  syncRunButton(u);
  el.stop.hidden = !(S.stream && S.streamFor === u.id);
  el.progress.hidden = true;
  if (!busyHere(u)) {
    setStatus(!u.available ? t("badge.win.only") : u.live ? t(u.input === "none" ? "status.live.none" : "status.live") : "", u.available ? "" : "error");
  }
  const last = S.results.get(u.id);
  if (last) {
    renderResult(last, { animate: false });
    if (busyHere(u) && !last.ai) showRunning(u);
  } else if (busyHere(u)) renderRunning(u);
  else renderOutEmpty(u);
  tickRuns();
  const ui = uiOf(u);
  if (!o.keep) {
    tool.scrollTop = 0;
    const first = u.pick === "folder" ? el.folder : ["text", "text2", "any"].includes(u.input) ? el.text : u.input === "none" ? null : el.drop;
    if (first) setTimeout(() => focusSoft(first), 30);
  }
  if (!o.keep && u.available && !last && !S.runs.has(u.id)) {
    if (ui.auto) run();
    else if (u.live && u.input === "none") run({ live: true });
  }
  if (u.live && u.input !== "none" && hasText()) scheduleLive();
}
function syncToolHead(u) {
  patchStar(el.fav, isFav(u.id), false);
  el.toolAi.checked = aiOn(u);
  el.toolAi.disabled = !u.ai || S.settings.ai_enabled === false;
  el.toolAiWrap.title = !u.ai ? t("tool.ai.window_only") : S.settings.ai_enabled === false ? t("tool.ai.all_off")
    : t("tool.ai.hint", { tokens: fmtTokens(u.tokens || 0) });
  el.toolAiWrap.classList.toggle("off", !u.ai);
  el.toolAiWrap.querySelector(".ut-switch-text").textContent = t(u.ai ? "tool.ai" : "tool.ai.window");
  el.traits.innerHTML = traitsOf(u).map(traitHtml).join("");
}
function verbOf(u) {
  const ui = uiOf(u);
  if (ui.auto && S.results.has(u.id)) return t("run.again");
  return ui.verb || (u.prompt ? t("run.ai") : t("run"));
}
/* a utility that is working right now (live re-runs while typing do not count) */
function busyHere(u) {
  const r = u && S.runs.get(u.id);
  return !!(r && !r.live);
}
function syncRunButton(u) {
  const busy = busyHere(u);
  el.run.classList.toggle("running", busy);
  if (busy) el.run.setAttribute("aria-busy", "true"); else el.run.removeAttribute("aria-busy");
  el.runLabel.textContent = busy ? t("run.running") : verbOf(u);
  el.run.disabled = !u.available || busy;
}
function renderOutEmpty(u) {
  const kind = ["file", "files"].includes(u.input) ? "files" : u.input === "none" ? "none" : u.pick === "folder" ? "folder" : "text";
  const ui = uiOf(u);
  el.out.innerHTML = `<div class="ut-out-empty"><span class="ut-out-ico">${svg(u.cat)}</span>
    <b>${esc(t("out.empty"))}</b><span>${esc(t(`out.empty.${kind}`, { verb: verbOf(u) }))}</span>
    ${kind === "text" && (ui.examples || u.example) ? `<span class="ut-out-tip">${svg("wand")}${esc(t("out.empty.examples"))}</span>` : ""}
    <span class="ut-keys"><kbd>Ctrl</kbd> + <kbd>Enter</kbd></span></div>`;
}
function renderRunning(u) {
  el.out.innerHTML = `<div class="ut-running" role="status"><span class="ut-running-spin" aria-hidden="true"></span>
    <b>${esc(t("out.running", { title: u.title }))}</b><span class="ut-running-time">${esc(fmtMs(performance.now() - ((S.runs.get(u.id) || {}).started || performance.now())))}</span>
    <small>${esc(t("out.running.hint"))}</small></div>`;
}
function showRunning(u) {
  const current = $(".ut-result", el.out);
  if (current) {
    current.classList.add("is-stale");
    const time = $(".ut-out-time", current);
    if (time) time.textContent = t("status.updating");
  } else renderRunning(u);
}
function renderInputs(u) {
  const ui = uiOf(u);
  const kind = u.input;
  const textual = ["text", "text2", "any"].includes(kind);
  const folder = u.pick === "folder";
  const line = isLine(u);
  el.inText.hidden = !textual;
  el.folderRow.hidden = !folder;
  el.text.hidden = folder;
  el.text2Wrap.hidden = kind !== "text2";
  el.inText.classList.toggle("pair", kind === "text2");
  el.inText.classList.toggle("line", line || folder);
  el.text.classList.toggle("ut-mono", !!ui.code);
  el.text2.classList.toggle("ut-mono", !!ui.code);
  el.text.rows = line ? 1 : kind === "text2" ? 6 : 9;
  el.text.setAttribute("enterkeyhint", line ? "go" : "enter");
  el.textLabel.textContent = ui.label || (folder ? t("in.folder") : kind === "text2" ? t("in.text1") : kind === "any" ? t("in.any") : t("in.text"));
  el.textLabel.htmlFor = folder ? "folder" : "text";
  el.label2.textContent = ui.label2 || u.label2 || t("in.text2");
  el.paste.hidden = folder;
  el.loadText.hidden = folder || line;
  el.clearText.hidden = folder;
  if (folder) {
    el.folder.value = S.texts.folder;
    el.folder.placeholder = u.placeholder || t("in.folder.ph");
  } else {
    el.text.value = storedText(u);
    el.text.placeholder = u.placeholder || (kind === "any" ? t("in.any.ph") : line ? t("in.line.ph") : t("in.text.ph"));
  }
  el.text2.value = S.texts.second;
  el.text2.placeholder = t("in.text2.ph");
  renderExamples(u);
  el.inFiles.hidden = !["file", "files", "any"].includes(kind);
  el.inFiles.classList.toggle("alt", kind === "any");
  el.fileInput.accept = u.accept || "";
  el.fileInput.multiple = kind !== "file";
  el.dropTitle.textContent = kind === "any" ? t("in.drop.any") : kind === "file" ? t("in.drop.one") : t("in.drop");
  el.dropHint.textContent = t("in.drop.hint", { what: acceptNames(u), size: fmtBytes(300 * 1024 * 1024) });
  countText();
  renderFiles();
}

/* ── examples: one click fills the form ────────────────────────────────────────────────────────────────────────── */
function examplesOf(u) {
  if (!u || u.pick === "folder" || !["text", "text2", "any"].includes(u.input)) return [];
  const ui = uiOf(u);
  const list = Array.isArray(ui.examples) && ui.examples.length ? ui.examples : u.example ? [u.example] : [];
  return list.filter((x) => typeof x === "string" && x.trim()).slice(0, 4);
}
function exampleLabel(x) {
  const flat = String(x).replace(/\s*\n\s*/g, " · ").replace(/\s+/g, " ").trim();
  return flat.length > 44 ? flat.slice(0, 42).trimEnd() + "…" : flat;
}
function renderExamples(u) {
  const list = examplesOf(u);
  el.examples.hidden = !list.length;
  el.examples2.hidden = true;
  el.examples.innerHTML = list.length ? `<span class="ut-ex-label">${svg("wand")}<span>${esc(t("in.examples"))}</span></span>` + list.map((x, i) => {
    const label = exampleLabel(x);
    return `<button type="button" class="ut-chip" data-example="${i}"${label !== x ? ` title="${esc(x)}"` : ""}>${esc(label)}</button>`;
  }).join("") : "";
}
function applyExample(i) {
  const u = S.tool;
  const value = examplesOf(u)[i];
  if (value == null) return;
  const ui = uiOf(u);
  el.text.value = value;
  const second = Array.isArray(ui.examples2) ? (ui.examples2[i] != null ? ui.examples2[i] : ui.examples2[0]) : null;
  if (u.input === "text2" && second != null) el.text2.value = second;
  if (ui.exopts && typeof ui.exopts === "object") setOptValues(u, ui.exopts);
  stashText();
  countText();
  $$("[data-example]", el.examples).forEach((chip) => chip.setAttribute("aria-pressed", String(Number(chip.dataset.example) === i)));
  if (u.live) scheduleLive(true);
  else { restartAnim(el.run, "ut-nudge"); focusSoft(el.run); }
}
function countText() {
  const a = el.text.value, b = el.text2.value;
  el.textCount.textContent = a && !isLine(S.tool) ? t("count.chars", { n: num(a.length) }) : "";
  el.text2Count.textContent = b ? t("count.chars", { n: num(b.length) }) : "";
}

/* ── files ─────────────────────────────────────────────────────────────────────────────────────────────────────── */
function fileMeta(f) {
  return `${esc(fmtBytes(f.size))}${f.ref ? " · " + esc(t("file.from_run")) : ""}${f.progress != null && f.progress < 1 ? " · " + num(Math.round(f.progress * 100)) + "%" : ""}`;
}
function renderFiles() {
  const u = S.tool;
  el.flist.innerHTML = S.files.map((f, i) => `<li class="ut-fitem${f.fresh ? " ut-new" : ""}">
      ${f.thumb ? `<img src="${esc(f.thumb)}" alt="">` : `<span class="ut-fext">${esc(extOf(f.name).slice(0, 4).toUpperCase() || "•")}</span>`}
      <span class="ut-fmeta"><b title="${esc(f.name)}">${esc(f.name)}</b><small>${fileMeta(f)}</small></span>
      ${S.files.length > 1 ? `<span class="ut-forder"><button type="button" class="ut-mini icon" data-fmove="${i}" data-dir="-1" aria-label="${esc(t("file.up"))}" title="${esc(t("file.up"))}"${i ? "" : " disabled"}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg></button><button type="button" class="ut-mini icon" data-fmove="${i}" data-dir="1" aria-label="${esc(t("file.down"))}" title="${esc(t("file.down"))}"${i < S.files.length - 1 ? "" : " disabled"}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button></span>` : ""}
      <button type="button" class="ut-mini icon" data-fremove="${i}" aria-label="${esc(t("file.remove"))}" title="${esc(t("file.remove"))}">${svg("x")}</button>
    </li>`).join("");
  S.files.forEach((f) => { f.fresh = false; });
  el.drop.classList.toggle("has", S.files.length > 0);
  if (u && u.input === "file" && S.files.length) el.dropTitle.textContent = t("in.drop.replace");
  else if (u) el.dropTitle.textContent = u.input === "any" ? t("in.drop.any") : u.input === "file" ? t("in.drop.one") : t("in.drop");
}
function patchFileProgress() {
  $$(".ut-fitem", el.flist).forEach((item, i) => {
    const f = S.files[i];
    const small = $(".ut-fmeta small", item);
    if (f && small) small.innerHTML = fileMeta(f);
  });
}
async function addFiles(list) {
  const u = S.tool;
  if (!u) return;
  const incoming = Array.from(list || []);
  if (!incoming.length) return;
  const bad = incoming.filter((f) => !accepts(u, f.name, f.type));
  const good = incoming.filter((f) => accepts(u, f.name, f.type));
  if (bad.length) toast(t("file.skipped", { names: bad.map((f) => f.name).slice(0, 3).join(", ") }), "error");
  const rows = [];
  for (const f of good) {
    if (f.size > 300 * 1024 * 1024) { toast(t("file.too_big", { name: f.name }), "error"); continue; }
    const row = { name: f.name, size: f.size, mime: f.type, file: f, thumb: "", fresh: true, key: ++S.fileKey };
    if (/^image\/(png|jpe?g|webp|gif|bmp|avif|svg\+xml)$/.test(f.type) && f.size < 12 * 1024 * 1024) {
      try { row.thumb = URL.createObjectURL(f); } catch (e) { row.thumb = ""; }
    }
    rows.push(row);
  }
  if (u.input === "file") S.files = rows.length ? rows.slice(-1) : S.files;
  else S.files = S.files.concat(rows).slice(0, 40);
  renderFiles();
  if (rows.length) setStatus(t("file.added", { n: num(S.files.length) }), "");
}

/* ── options form ──────────────────────────────────────────────────────────────────────────────────────────────── */
function optValues(u) {
  const stored = prefs.opts[u.id] || {};
  const out = {};
  u.opts.forEach((o) => { out[o.key] = stored[o.key] !== undefined && o.kind !== "password" ? stored[o.key] : o.default; });
  return out;
}
/* seg — a short switch of 2–4 words; tiles — 2–4 longer choices with a caption; pills — up to 8; a list beyond that. */
function choiceStyle(o) {
  const labels = (o.choices || []).map((c) => String(c.label));
  if (labels.length < 2) return "select";
  if (labels.length <= 4 && labels.every((l) => l.length <= 12) && labels.join("").length <= 34) return "seg";
  if (labels.length <= 4) return "tiles";
  if (labels.length <= 8 && labels.every((l) => l.length <= 22)) return "pills";
  return "select";
}
function splitLabel(label) {
  const s = String(label);
  let m = /^(.+?)\s*\(([^()]+)\)$/.exec(s);
  if (m) return [m[1], m[2]];
  m = /^(.+?)\s+[—–]\s+(.+)$/.exec(s);
  if (m) return [m[1], m[2]];
  return [s, ""];
}
function optControl(o, value, id) {
  const v = value == null ? "" : value;
  const key = esc(o.key);
  switch (o.kind) {
    case "bool":
      return `<label class="ut-check"><input type="checkbox" id="${id}" data-opt="${key}"${v ? " checked" : ""}><span class="ut-check-box" aria-hidden="true"></span><span class="ut-check-text">${esc(o.label)}</span></label>`;
    case "choice": {
      const style = choiceStyle(o);
      const choices = o.choices || [];
      if (style === "select") {
        return `<select class="ut-field ut-select" id="${id}" data-opt="${key}">${choices.map((c) =>
          `<option value="${esc(c.value)}"${String(c.value) === String(v) ? " selected" : ""}>${esc(c.label)}</option>`).join("")}</select>`;
      }
      const picked = choices.some((c) => String(c.value) === String(v)) ? String(v) : String((choices[0] || {}).value);
      return `<div class="ut-choice ut-${style}" role="radiogroup" id="${id}" data-opt="${key}" data-seg="1" aria-labelledby="${id}-l">${choices.map((c) => {
        const on = String(c.value) === picked;
        const attrs = `type="button" role="radio" data-value="${esc(c.value)}" aria-checked="${on}" tabindex="${on ? 0 : -1}"`;
        if (style !== "tiles") return `<button ${attrs} title="${esc(c.label)}">${esc(c.label)}</button>`;
        const [main, sub] = splitLabel(c.label);
        return `<button ${attrs}><b>${esc(main)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</button>`;
      }).join("")}</div>`;
    }
    case "int":
    case "number":
      return `<input type="number" class="ut-field ut-num" id="${id}" data-opt="${key}" value="${esc(v)}"${o.min != null ? ` min="${o.min}"` : ""}${o.max != null ? ` max="${o.max}"` : ""} step="${o.step != null ? o.step : o.kind === "int" ? 1 : "any"}" inputmode="${o.kind === "int" ? "numeric" : "decimal"}">`;
    case "color":
      return `<span class="ut-color"><input type="color" id="${id}" data-opt="${key}" value="${esc(/^#[0-9a-f]{6}$/i.test(v) ? v : "#000000")}" aria-label="${esc(o.label)}"><input type="text" class="ut-field ut-mono" data-color-for="${id}" value="${esc(v)}" spellcheck="false" aria-label="${esc(o.label)}"></span>`;
    case "password":
      return `<span class="ut-pass"><input type="password" class="ut-field" id="${id}" data-opt="${key}" value="" autocomplete="off" spellcheck="false"><button type="button" class="ut-mini icon" data-reveal="${id}" aria-label="${esc(t("opt.show"))}" title="${esc(t("opt.show"))}">${svg("eye")}</button></span>`;
    case "textarea":
      return `<textarea class="ut-field ut-area" id="${id}" data-opt="${key}" rows="3" spellcheck="false">${esc(v)}</textarea>`;
    default:
      return `<input type="text" class="ut-field" id="${id}" data-opt="${key}" value="${esc(v)}" spellcheck="false" autocomplete="off">`;
  }
}
function renderOpts(u, animate) {
  const values = optValues(u);
  el.optsWrap.hidden = !u.opts.length;
  const bools = [], rest = [];
  u.opts.forEach((o, i) => (o.kind === "bool" ? bools : rest).push([o, i]));
  const html = rest.map(([o, i]) => {
    const id = `opt-${i}`;
    const hint = o.hint ? `<small class="ut-hint">${esc(o.hint)}</small>` : "";
    const style = o.kind === "choice" ? choiceStyle(o) : "";
    const wide = o.kind === "textarea" || style === "tiles" || style === "pills" || (style === "seg" && (o.choices || []).length > 3);
    const label = style && style !== "select" ? `<span class="ut-opt-label" id="${id}-l">${esc(o.label)}</span>` : `<label class="ut-opt-label" for="${id}">${esc(o.label)}</label>`;
    return `<div class="ut-opt${wide ? " wide" : ""}" data-key="${esc(o.key)}" data-kind="${esc(o.kind)}"${style ? ` data-style="${style}"` : ""}>${label}${optControl(o, values[o.key], id)}${hint}</div>`;
  }).join("") + (bools.length ? `<div class="ut-opt wide ut-bools">${bools.map(([o, i]) => `<div class="ut-bool" data-key="${esc(o.key)}">${optControl(o, values[o.key], `opt-${i}`)}${o.hint ? `<small class="ut-hint">${esc(o.hint)}</small>` : ""}</div>`).join("")}</div>` : "");
  el.opts.classList.toggle("ut-anim", !!animate);
  el.opts.innerHTML = html;
  applyShow(u);
  syncReset(u);
}
function readOpts(u) {
  const out = {};
  u.opts.forEach((o, i) => {
    const node = $(`#opt-${i}`, el.opts);
    if (!node) { out[o.key] = o.default; return; }
    if (o.kind === "bool") out[o.key] = node.checked;
    else if (node.dataset.seg) {
      const on = $('[aria-checked="true"]', node);
      out[o.key] = on ? coerce(o, on.dataset.value) : o.default;
    } else if (o.kind === "int" || o.kind === "number") {
      const raw = node.value.trim();
      out[o.key] = raw === "" ? o.default : o.kind === "int" ? Math.round(Number(raw)) : Number(raw);
      if (Number.isNaN(out[o.key])) out[o.key] = o.default;
    } else if (o.kind === "color") {
      const text = $(`[data-color-for="${node.id}"]`, el.opts);
      out[o.key] = text && text.value.trim() ? text.value.trim() : node.value;
    } else out[o.key] = coerce(o, node.value);
  });
  return out;
}
function setOptValues(u, values) {
  u.opts.forEach((o, i) => {
    if (!(o.key in values)) return;
    const node = $(`#opt-${i}`, el.opts);
    if (!node) return;
    const v = values[o.key];
    if (o.kind === "bool") node.checked = !!v;
    else if (node.dataset.seg) $$("[data-value]", node).forEach((b) => { const on = String(b.dataset.value) === String(v); b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
    else if (o.kind === "color") { node.value = /^#[0-9a-f]{6}$/i.test(v) ? v : node.value; const text = $(`[data-color-for="${node.id}"]`, el.opts); if (text) text.value = v; }
    else node.value = v;
  });
  onOptsChanged({ quiet: true });
}
function coerce(o, raw) {
  const match = (o.choices || []).find((c) => String(c.value) === String(raw));
  return match ? match.value : raw;
}
function applyShow(u) {
  const values = readOpts(u);
  u.opts.forEach((o) => {
    if (!o.show) return;
    const [key, list] = String(o.show).split("=");
    const visible = String(list || "").split("|").includes(String(values[key]));
    const box = $(`[data-key="${CSS.escape(o.key)}"]`, el.opts);
    if (box) box.hidden = !visible;
  });
  const bools = $(".ut-bools", el.opts);
  if (bools) bools.hidden = !$$(".ut-bool:not([hidden])", bools).length;
}
function customOpts(u) {
  const stored = prefs.opts[u.id];
  if (!stored) return false;
  return u.opts.some((o) => o.kind !== "password" && stored[o.key] !== undefined && String(stored[o.key]) !== String(o.default));
}
function syncReset(u) {
  el.optsReset.hidden = !customOpts(u);
}
function onOptsChanged(options) {
  const u = S.tool;
  if (!u) return;
  applyShow(u);
  const values = readOpts(u);
  u.opts.forEach((o) => { if (o.kind === "password") delete values[o.key]; });
  prefs.opts[u.id] = values;
  savePrefs();
  syncReset(u);
  if (u.live && !(options && options.quiet)) scheduleLive();
}

/* ── running ───────────────────────────────────────────────────────────────────────────────────────────────────── */
function isDanger(u, opts) {
  if (u.danger) return true;
  if (!u.danger_if) return false;
  const [key, list] = String(u.danger_if).split("=");
  const value = opts[key];
  if (!list) return !!value && !["0", "false", "no"].includes(String(value).toLowerCase());
  return String(list).split("|").includes(String(value));
}
function askConfirm(text) {
  el.confirmText.textContent = text;
  el.confirm.hidden = false;
  restartAnim(el.confirm, "ut-pop");
  el.confirmYes.focus();
  return new Promise((resolve) => { S.confirmResolve = resolve; });
}
function closeConfirm(answer) {
  el.confirm.hidden = true;
  const resolve = S.confirmResolve;
  S.confirmResolve = null;
  if (resolve) resolve(answer);
}
/* A live re-run while typing stays quiet; a real run turns the button into a progress indicator (full colour, a
   spinner and the elapsed time) and the result pane into a "running" state instead of the old placeholder. */
/* Every utility runs on its own: while a long conversion works, another utility can be opened and run. */
function setRunning(active, u, live) {
  if (!u) return;
  if (active) S.runs.set(u.id, { live: !!live, started: performance.now(), queued: false, again: false });
  else S.runs.delete(u.id);
  const card = S.cards.get(u.id);
  if (card) card.classList.toggle("running", active && !live);
  if (S.tool && S.tool.id === u.id) {
    syncRunButton(u);
    el.stop.hidden = !(active && S.stream && S.streamFor === u.id);
  }
  tickRuns();
}
function tickRuns() {
  clearInterval(S.runTimer);
  const u = S.tool;
  const r = u && S.runs.get(u.id);
  if (!r || r.live) return;
  const tick = () => {
    if (!S.tool || S.runs.get(S.tool.id) !== r) { clearInterval(S.runTimer); return; }
    const time = fmtMs(performance.now() - r.started);
    if (!S.uploading) setStatus(t("status.running", { time }), "busy");
    const clock = $(".ut-running-time", el.out);
    if (clock) clock.textContent = time;
  };
  tick();
  S.runTimer = setInterval(tick, 250);
}
async function uploadFile(f, onProgress) {
  const start = await call("upload.start", { name: f.name, size: f.size });
  let offset = 0;
  while (offset < f.size) {
    const part = f.file.slice(offset, Math.min(f.size, offset + CHUNK));
    const data = await readAsDataURL(part);
    await call("upload.chunk", { upload: start.upload, offset, data });
    offset += part.size;
    onProgress(offset / f.size);
  }
  const done = await call("upload.done", { upload: start.upload });
  return done.ref;
}
async function filesPayload(here) {
  const out = [];
  const list = S.files;
  const bigTotal = list.filter((f) => !f.ref && f.size > INLINE_LIMIT).reduce((sum, f) => sum + f.size, 0);
  let sent = 0;
  try {
    for (const f of list) {
      if (f.ref) { out.push({ ref: f.ref, name: f.name }); continue; }
      if (f.size <= INLINE_LIMIT) { out.push({ name: f.name, data: await readAsDataURL(f.file) }); continue; }
      S.uploading = true;
      if (here()) el.progress.hidden = false;
      const base = sent;
      f.ref = await uploadFile(f, (share) => {
        f.progress = share;
        if (!here()) return;
        const total = bigTotal ? (base + share * f.size) / bigTotal : 1;
        el.progress.hidden = false;
        $("i", el.progress).style.width = `${Math.round(total * 100)}%`;
        setStatus(t("status.upload", { name: f.name, p: num(Math.round(share * 100)) }), "busy");
        patchFileProgress();
      });
      f.uploaded = true;
      sent += f.size;
      out.push({ ref: f.ref, name: f.name });
    }
  } finally {
    S.uploading = false;
    if (here()) { el.progress.hidden = true; patchFileProgress(); }
  }
  return out;
}
function textValue(u) {
  return u.pick === "folder" ? el.folder.value.trim() : el.text.value;
}
function finishedElsewhere(u, ok) {
  if (S.tool && S.tool.id === u.id && S.view === "tool") return false;
  el.toast.innerHTML = `<span>${esc(t(ok ? "status.done.other" : "status.failed.other", { title: u.title }))}</span>`
    + `<button type="button" class="ut-mini" data-goto="${esc(u.id)}"><span>${esc(t("toast.open"))}</span>${svg("next")}</button>`;
  showToast(ok ? "ok" : "error", 7000);
  return true;
}
async function run(options) {
  const o = options || {};
  const u = S.tool;
  if (!u) return;
  const current = S.runs.get(u.id);
  if (current) {
    if (o.live) current.again = true; else if (current.live) current.queued = true;
    return;
  }
  if (u.prompt && S.stream) { if (!o.live) setStatus(t("status.streaming"), "busy"); return; }
  if (!u.available) return setStatus(t("badge.win.only"), "error");
  const opts = readOpts(u);
  const text = textValue(u);
  if (["file", "files"].includes(u.input) && !S.files.length) {
    if (!o.live) { setStatus(t("need.files"), "error"); restartAnim(el.drop, "ut-shake"); }
    return;
  }
  if (u.input === "any" && !text.trim() && !S.files.length) { if (!o.live) setStatus(t("need.any"), "error"); return; }
  if (u.pick === "folder" && !text) {
    if (!o.live) { setStatus(t("need.folder"), "error"); restartAnim(el.folderRow, "ut-shake"); focusSoft(el.folder); }
    return;
  }
  if (u.prompt) return runAi(u, opts, text);
  if (!o.live && isDanger(u, opts)) {
    const yes = await askConfirm(t("confirm.text", { title: u.title }));
    if (!yes) return setStatus(t("status.cancelled"), "");
  }
  setRunning(true, u, !!o.live);
  if (!o.live) showRunning(u);
  const here = () => S.tool && S.tool.id === u.id;
  const text2 = u.input === "text2" ? el.text2.value : "";
  try {
    const files = ["file", "files", "any"].includes(u.input) && S.files.length ? await filesPayload(here) : [];
    const res = await call("run", { util: u.id, text, text2, files, opts, live: !!o.live });
    if (!res || res.ok === false) {
      if (/expired|устарел|gone|не найден/i.test(String(res && res.error)) && S.files.some((f) => f.uploaded)) {
        S.files.forEach((f) => { if (f.uploaded) { f.ref = ""; f.uploaded = false; } });
      }
      S.results.delete(u.id);
      if (here()) { renderError(res && res.error, u); setStatus(t("status.failed"), "error"); }
      if (!o.live) finishedElsewhere(u, false);
    } else {
      S.results.set(u.id, res);
      if (here()) {
        renderResult(res, { animate: !o.live });
        setStatus(t("status.done", { time: fmtMs(res.ms) }), "ok");
      }
      if (!o.live) { remember(u.id); finishedElsewhere(u, true); }
      if (res.result && res.result.job) pollJobs();
    }
  } catch (error) {
    S.results.delete(u.id);
    if (here()) { renderError(errorText(error), u); setStatus(t("status.failed"), "error"); }
    if (!o.live) finishedElsewhere(u, false);
  } finally {
    const record = S.runs.get(u.id) || {};
    if (here()) el.progress.hidden = true;
    setRunning(false, u);
    if (record.queued && here()) setTimeout(() => run(), 0);
    else if (record.again && here() && u.live) scheduleLive();
  }
}
function remember(id) {
  S.settings.recent = [id].concat((S.settings.recent || []).filter((x) => x !== id)).slice(0, 12);
  patchSide();
}
function scheduleLive(now) {
  const u = S.tool;
  clearTimeout(S.liveTimer);
  if (!u || !u.live || !u.available) return;
  if (u.input !== "none") {
    if (el.text.value.length > LIVE_LIMIT || el.text2.value.length > LIVE_LIMIT) return;
    if (!hasText()) return;
  }
  S.liveTimer = setTimeout(() => run({ live: true }), now ? 0 : 320);
}

/* ── AI utilities ──────────────────────────────────────────────────────────────────────────────────────────────── */
function providerNameOf(item) {
  try { if (window.MemuModelSelector) return window.MemuModelSelector.providerName(item); } catch (e) { /* plain list */ }
  return item.provider_label || item.account || "";
}
function modelOptions(items) {
  const groups = {};
  items.forEach((item) => { const g = providerNameOf(item); (groups[g] = groups[g] || []).push(item); });
  return Object.keys(groups).sort().map((g) => {
    const opts = groups[g].map((item) => `<option value="${esc(item.id)}">${esc(item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id)}</option>`).join("");
    return g ? `<optgroup label="${esc(g)}">${opts}</optgroup>` : opts;
  }).join("");
}
async function ensureModels() {
  if (S.models || S.modelState === "loading") return;
  S.modelState = "loading";
  el.model.innerHTML = `<option value="">${esc(t("model.loading"))}</option>`;
  try {
    const rows = await Memu.models();
    S.models = (Array.isArray(rows) ? rows : []).filter((item) => (item.kind || "chat") === "chat" && !item.image && !item.video && !item.audio);
    S.modelState = S.models.length ? "ready" : "none";
  } catch (error) {
    S.models = [];
    S.modelState = "error";
  }
  fillModels();
}
function fillModels() {
  if (!S.models) return;
  if (!S.models.length) {
    el.model.innerHTML = `<option value="">${esc(t(S.modelState === "error" ? "model.error" : "model.none"))}</option>`;
    return;
  }
  el.model.innerHTML = modelOptions(S.models);
  const wanted = [S.settings.model, (S.models.find((m) => m.default) || {}).id, S.models[0].id].find((id) => id && S.models.some((m) => m.id === id));
  if (wanted) el.model.value = wanted;
  try { if (window.MemuModelSelector && window.MemuModelSelector.mountSelect && !el.model.dataset.mounted) { window.MemuModelSelector.mountSelect(el.model, { preset: "dropdown", kind: "chat" }); el.model.dataset.mounted = "1"; } } catch (e) { /* plain select */ }
}
function renderModelRow(u) {
  el.modelWrap.hidden = !u.prompt;
  if (!u.prompt) return;
  el.modelHint.textContent = u.input === "files" ? t("model.hint.vision") : t("model.hint");
  ensureModels();
}
function choiceLabel(u, key, value) {
  const o = u.opts.find((x) => x.key === key);
  const c = o && (o.choices || []).find((x) => String(x.value) === String(value));
  return c ? `${c.label} (${c.value})` : value;
}
async function runAi(u, opts, text) {
  if (!S.models) await ensureModels();
  const model = el.model.value;
  if (!model) { setStatus(t("model.need"), "error"); el.model.focus(); return; }
  const instructions = String(u.prompt).replace(/\{([a-z0-9_]+)\}/gi, (m, key) => (key in opts ? String(choiceLabel(u, key, opts[key])) : m));
  const images = [];
  if (u.input === "files") {
    for (const f of S.files.slice(0, 8)) {
      if (f.ref) continue;
      if (f.size > 15 * 1024 * 1024) { toast(t("file.too_big", { name: f.name }), "error"); continue; }
      images.push(await readAsDataURL(f.file));
    }
    if (!images.length) { setStatus(t("need.files"), "error"); return; }
  }
  let body = text;
  if (u.input === "text2" && el.text2.value.trim()) body = `${text}\n\n${uiOf(u).label2 || u.label2 || t("in.text2")}:\n${el.text2.value}`;
  if (!String(body || "").trim() && !images.length) { setStatus(t("need.text"), "error"); restartAnim(el.inText, "ut-shake"); el.text.focus(); return; }
  S.settings.model = model;
  call("settings.set", { model }).catch(() => {});
  let streamed = "";
  const started = performance.now();
  const res = { ok: true, util: u.id, title: u.title, run: "", result: { text: "" }, outputs: [], ms: 0, ai: true };
  S.results.set(u.id, res);
  const here = () => S.tool && S.tool.id === u.id;
  renderResult(res, { animate: true, streaming: true });
  try {
    S.stream = Memu.stream({ model, instructions, text: String(body || "").trim() || t("ai.image_request"), images, keep_in_chat: false }, {
      onDelta(chunk) {
        streamed += chunk;
        res.result.text = streamed;
        if (!here()) return;
        const pre = $(".ut-pre", el.out);
        if (pre) { pre.textContent = streamed.slice(0, TEXT_SHOW); pre.scrollTop = pre.scrollHeight; }
      },
    });
    S.streamFor = u.id;
    setRunning(true, u, false);
    const answer = await S.stream;
    res.result.text = String((answer && answer.text) || streamed || "").trim();
    res.ms = performance.now() - started;
    if (here()) {
      renderResult(res, { animate: false });
      setStatus(res.result.text ? t("status.done", { time: fmtMs(res.ms) }) : t("status.empty"), res.result.text ? "ok" : "error");
    } else finishedElsewhere(u, !!res.result.text);
    remember(u.id);
  } catch (error) {
    const stopped = /останов|stopped|abort/i.test(errorText(error));
    res.result.text = streamed;
    res.ms = performance.now() - started;
    if (!streamed && !stopped) S.results.delete(u.id);
    if (here()) {
      if (streamed) renderResult(res, { animate: false });
      else if (!stopped) renderError(errorText(error), u);
      else { S.results.delete(u.id); renderOutEmpty(u); }
      setStatus(stopped ? t("status.stopped") : t("status.failed"), stopped ? "" : "error");
    }
  } finally {
    S.stream = null;
    S.streamFor = "";
    setRunning(false, u);
  }
}

/* ── results ───────────────────────────────────────────────────────────────────────────────────────────────────── */
function renderError(message, u) {
  el.out.innerHTML = `<div class="ut-out-error ut-enter"><span class="ut-out-ico">${svg("warn")}</span><b>${esc(t("out.error"))}</b>
    <p>${esc(message || t("error.generic"))}</p></div>`;
  if (u) S.results.delete(u.id);
}
function textExt(r) {
  const lang = String(r.lang || "").toLowerCase();
  const map = { json: "json", html: "html", css: "css", js: "js", javascript: "js", sql: "sql", xml: "xml", yaml: "yaml", yml: "yaml", md: "md",
    markdown: "md", csv: "csv", tsv: "tsv", toml: "toml", python: "py", py: "py", sh: "sh", bash: "sh", svg: "svg", ini: "ini" };
  return map[lang] || "txt";
}
/* Monospace only where it helps reading: hashes, ids, addresses, byte dumps — not for words, paths and plain numbers. */
function looksCode(value) {
  const s = String(value == null ? "" : value).trim();
  if (!s || s.length > 400) return false;
  if (/^(\d{1,3}\.){3}\d{1,3}(\/\d+)?(:\d+)?$/.test(s) || /^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(s)) return true;
  if (/\s/.test(s)) return /^([0-9a-f]{2} ){3,}[0-9a-f]{2}$/i.test(s);
  if (/[\\/]/.test(s) && !/^[A-Za-z0-9+/=]{24,}$/.test(s)) return false;
  if (/^[0-9a-f-]{16,}$/i.test(s) && /\d/.test(s)) return true;
  return s.length >= 20 && /^[A-Za-z0-9+/=_.:-]+$/.test(s) && /\d/.test(s) && /[A-Za-z]/.test(s);
}
function infoHtml(info) {
  return `<div class="ut-kvs">${info.map(([k, v]) => {
    const value = String(v == null ? "" : v);
    const wide = value.length > 42 || value.includes("\n");
    return `<div class="ut-kv${wide ? " wide" : ""}"><span class="ut-k">${esc(k)}</span><span class="ut-v${looksCode(value) ? " mono" : ""}">${esc(value)}</span>
      <button type="button" class="ut-mini icon ut-kv-copy" data-copy="${esc(value)}" aria-label="${esc(t("out.copy"))}" title="${esc(t("out.copy"))}">${svg("copy")}</button></div>`;
  }).join("")}</div>`;
}
function tableHtml(table) {
  const cols = table.columns || [];
  const rows = (table.rows || []).slice(0, TABLE_SHOW);
  const numeric = cols.map((_, c) => rows.length > 0 && rows.slice(0, 50).every((row) => row[c] === "" || row[c] == null || /^[-+]?[\d\s.,]+(%|\s?[KMGT]?i?B|\s?[кМГТ]?Б)?$/.test(String(row[c]))));
  return `<div class="ut-table-wrap"><table class="ut-table"><thead><tr>${cols.map((c, i) => `<th${numeric[i] ? ' class="n"' : ""}>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${cols.map((_, i) => {
      const cell = row[i] == null ? "" : String(row[i]);
      const meter = /^[█▓▒░]{3,}\s*(\d{1,3})%$/.exec(cell.trim());
      if (meter) {
        const p = Math.min(100, Number(meter[1]));
        return `<td><span class="ut-meter-cell${p >= 90 ? " warn" : ""}" style="--p:${p}%"><i></i><span>${p}%</span></span></td>`;
      }
      return `<td class="${numeric[i] ? "n" : ""}${!numeric[i] && looksCode(cell) ? " mono" : ""}">${esc(cell)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table></div>`
    + ((table.rows || []).length > TABLE_SHOW ? `<p class="ut-more">${esc(t("out.table.more", { n: num(table.rows.length - TABLE_SHOW) }))}</p>` : "");
}
function tableText(table, sep) {
  const cell = (v) => {
    const s = String(v == null ? "" : v);
    return sep === "," && /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s.replace(/\t|\n/g, " ");
  };
  return [table.columns || []].concat(table.rows || []).map((row) => row.map(cell).join(sep)).join("\n");
}
function fileCard(o, i) {
  const pv = o.preview ? `<img src="${esc(o.preview)}" alt="" loading="lazy">` : `<span class="ut-fext big">${esc(extOf(o.name).slice(0, 4).toUpperCase() || "•")}</span>`;
  const dims = o.width && o.height ? ` · ${num(o.width)}×${num(o.height)}` : "";
  const viewable = ["image", "audio", "video", "text"].includes(o.kind) && o.size <= 40 * 1024 * 1024;
  return `<div class="ut-file" style="--i:${Math.min(i, 12)}" data-file="${i}">
    <button type="button" class="ut-file-pv" data-fact="view" ${viewable ? "" : "disabled"} aria-label="${esc(t("out.view"))}">${pv}${["audio", "video"].includes(o.kind) ? `<span class="ut-play">${svg("play")}</span>` : ""}</button>
    <div class="ut-file-meta"><b title="${esc(o.name)}">${esc(o.name)}</b><small>${esc(o.size_h || fmtBytes(o.size))}${esc(dims)}</small></div>
    <div class="ut-file-acts">
      <button type="button" class="ut-mini" data-fact="save" title="${esc(t("out.save.hint"))}">${svg("save")}<span>${esc(t("out.save"))}</span></button>
      <button type="button" class="ut-mini icon" data-fact="next" title="${esc(t("out.next"))}" aria-label="${esc(t("out.next"))}">${svg("next")}</button>
    </div></div>`;
}
function isAnswer(r, outs) {
  const text = String(r.text || "").trim();
  return !!text && text.length <= ANSWER_MAX && !text.includes("\n") && !outs.length && !(r.table && (r.table.columns || []).length);
}
function renderResult(res, options) {
  const o = options || {};
  const r = res.result || {};
  const outs = res.outputs || [];
  const text = String(r.text || "");
  const answer = !o.streaming && isAnswer(r, outs);
  const parts = [];
  const textActs = r.text && !answer ? `<button type="button" class="ut-mini" data-oact="copy">${svg("copy")}<span>${esc(t("out.copy"))}</span></button>
      <button type="button" class="ut-mini" data-oact="download">${svg("save")}<span>${esc(t("out.download"))}</span></button>` : "";
  const reuse = r.text && S.tool && ["text", "text2", "any"].includes(S.tool.input) && S.tool.pick !== "folder"
    ? `<button type="button" class="ut-mini icon" data-oact="reuse" title="${esc(t("out.reuse"))}" aria-label="${esc(t("out.reuse"))}">${svg("reuse")}</button>` : "";
  parts.push(`<div class="ut-out-head"><span class="ut-ok">${svg(res.ai ? "ai" : "check")}</span><b>${esc(t("out.title"))}</b>
    <span class="ut-out-time">${res.ms ? esc(fmtMs(res.ms)) : o.streaming ? esc(t("status.streaming")) : ""}</span><span class="ut-grow"></span>${textActs}${reuse}</div>`);
  if (r.note) parts.push(`<div class="ut-callout">${svg("info")}<span>${esc(r.note)}</span></div>`);
  if (answer) {
    parts.push(`<div class="ut-answer"><span class="ut-answer-text${looksCode(text) ? " mono" : ""}">${esc(text.trim())}</span>
      <button type="button" class="ut-mini" data-oact="copy">${svg("copy")}<span>${esc(t("out.copy"))}</span></button></div>`);
  }
  if (r.swatches && r.swatches.length) {
    parts.push(`<div class="ut-swatches">${r.swatches.map((c, i) => `<button type="button" class="ut-sw" data-copy="${esc(c)}" style="--sw:${esc(c)};--i:${Math.min(i, 16)}" title="${esc(t("out.copy"))}"><i></i><span>${esc(c)}</span></button>`).join("")}</div>`);
  }
  if (r.info && r.info.length) parts.push(infoHtml(r.info));
  if (!answer && (r.text || o.streaming)) {
    parts.push(`<pre class="ut-pre${r.mono === false ? " prose" : ""}" tabindex="0">${esc(text.slice(0, TEXT_SHOW))}</pre>`);
    if (text.length > TEXT_SHOW) parts.push(`<p class="ut-more">${esc(t("out.text.more", { n: num(text.length) }))}</p>`);
  }
  if (r.table && (r.table.columns || []).length) {
    parts.push(`<div class="ut-sub-head"><b>${esc(t("out.table", { n: num((r.table.rows || []).length) }))}</b><span class="ut-grow"></span>
      <button type="button" class="ut-mini" data-oact="tsv">${svg("copy")}<span>${esc(t("out.copy_table"))}</span></button>
      <button type="button" class="ut-mini" data-oact="csv">${svg("save")}<span>CSV</span></button></div>` + tableHtml(r.table));
  }
  if (r.job) parts.push(`<div class="ut-job-inline" data-job="${esc(r.job.id)}"></div>`);
  if (outs.length) {
    const total = outs.reduce((sum, f) => sum + (f.size || 0), 0);
    parts.push(`<div class="ut-sub-head"><b>${esc(t("out.files", { n: num(outs.length), size: fmtBytes(total) }))}</b><span class="ut-grow"></span>
      ${outs.length > 1 ? `<button type="button" class="ut-mini" data-oact="save-all">${svg("save")}<span>${esc(t("out.save_all"))}</span></button>` : ""}
      <button type="button" class="ut-mini" data-oact="save-to">${svg("folder")}<span>${esc(t("out.save_to"))}</span></button></div>
      <div class="ut-files">${outs.map(fileCard).join("")}</div>`);
  }
  if (parts.length === 1) parts.push(`<p class="ut-more">${esc(t("out.nothing"))}</p>`);
  el.out.innerHTML = `<div class="ut-result${o.animate ? " ut-enter" : ""}">${parts.join("")}</div>`;
  if (r.job) syncInlineJobs(liveJob(r.job));
  if (o.animate) el.out.scrollTop = 0;
}
async function saveOutput(res, name, folder) {
  try {
    const out = await call("out.save", { run: res.run, name: name || "", folder: folder || "" });
    toastSaved(out);
    return out;
  } catch (error) { toast(errorText(error), "error"); return null; }
}
function toastSaved(out) {
  el.toast.innerHTML = `<span>${esc(t("out.saved", { path: out.path }))}</span><button type="button" class="ut-mini" data-reveal-path="${esc(out.saved && out.saved.length === 1 ? out.saved[0] : out.folder)}">${svg("folder")}<span>${esc(t("out.reveal"))}</span></button>`;
  showToast("ok", 6000);
}
async function viewFile(res, o) {
  S.viewerFile = { res, o };
  el.viewerTitle.textContent = o.name;
  el.viewerBody.innerHTML = `<div class="ut-viewer-wait"><span class="mu-spinner"></span></div>`;
  openViewer();
  try {
    const data = await call("out.get", { run: res.run, name: o.name });
    if (!S.viewerFile || S.viewerFile.o !== o) return;
    if (o.kind === "image") el.viewerBody.innerHTML = `<img src="${esc(data.data)}" alt="${esc(o.name)}">`;
    else if (o.kind === "audio") el.viewerBody.innerHTML = `<audio controls autoplay src="${esc(data.data)}"></audio>`;
    else if (o.kind === "video") el.viewerBody.innerHTML = `<video controls autoplay playsinline src="${esc(data.data)}"></video>`;
    else {
      const b64 = String(data.data).split(",")[1] || "";
      const bytes = Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));
      const text = new TextDecoder("utf-8").decode(bytes);
      el.viewerBody.innerHTML = `<pre class="ut-pre">${esc(text.slice(0, TEXT_SHOW))}</pre>`;
    }
  } catch (error) {
    el.viewerBody.innerHTML = `<div class="ut-out-error"><p>${esc(errorText(error))}</p></div>`;
  }
}
function openViewer() {
  S.lastFocus = document.activeElement;
  el.viewer.hidden = false;
  restartAnim(el.viewer, "open");
  $("#viewer-close").focus();
}
function closeViewer() {
  if (el.viewer.hidden) return;
  $$("audio,video", el.viewerBody).forEach((m) => { try { m.pause(); } catch (e) { /* gone */ } });
  el.viewer.hidden = true;
  el.viewerBody.innerHTML = "";
  S.viewerFile = null;
  if (S.lastFocus && S.lastFocus.focus) S.lastFocus.focus();
}
function carryFile(res, o) {
  S.carry = { files: [{ name: o.name, size: o.size, mime: o.mime, ref: o.ref, thumb: o.preview || "" }] };
  S.q = "";
  el.q.value = "";
  S.cat = "all";
  stashText();
  goList({ animate: true });
  toast(t("carry.toast"), "ok");
}

/* ── AI access: switches only (no half-checked boxes), counts per section, patched in place ───────────────────── */
function aiList() {
  return S.utils.filter((u) => u.ai_on).map((u) => u.id);
}
function usableAi() {
  return S.utils.filter((u) => u.ai && u.available);
}
function sameSet(a, b) {
  if (a.length !== b.length) return false;
  const set = new Set(a);
  return b.every((id) => set.has(id));
}
function renderAiStats() {
  const tools = S.skill.tools || 0, tokens = S.skill.tokens || 0;
  const usable = usableAi();
  const allTokens = usable.reduce((sum, u) => sum + (u.tokens || 0), 0) + SKILL_HEAD_TOKENS;
  el.aiEnabled.checked = S.settings.ai_enabled !== false;
  el.aiCount.textContent = t("ai.count", { n: num(tools), total: num(usable.length) });
  el.aiTokens.textContent = tools ? t("ai.tokens", { tokens: fmtTokens(tokens) }) : t("ai.tokens.none");
  el.aiMeter.style.width = `${tools ? Math.max(2, Math.min(100, Math.round((tokens / Math.max(allTokens, 1)) * 100))) : 0}%`;
  el.aiMeter.dataset.level = tokens > 6000 ? "high" : tokens > 3500 ? "mid" : "low";
  el.app.classList.toggle("ai-off", S.settings.ai_enabled === false);
  const on = aiList();
  const recommended = S.recommended.filter((id) => { const u = S.byId.get(id); return u && u.ai && u.available; });
  el.aiDefaults.title = t("ai.defaults.hint", { n: num(recommended.length) });
  el.aiDefaults.setAttribute("aria-pressed", String(on.length > 0 && sameSet(on, recommended)));
  el.aiAll.setAttribute("aria-pressed", String(on.length > 0 && on.length === usable.length));
  el.aiNone.setAttribute("aria-pressed", String(on.length === 0));
}
function aiGroupState(catId) {
  const usable = S.utils.filter((u) => u.cat === catId && u.ai && u.available);
  const on = usable.filter((u) => u.ai_on);
  return { usable, on, tokens: on.reduce((sum, u) => sum + (u.tokens || 0), 0) };
}
function aiGroupCount(state) {
  return t("ai.group", { on: num(state.on.length), total: num(state.usable.length) }) + (state.tokens ? " · " + fmtTokens(state.tokens) : "");
}
function renderAi() {
  if (!S.loaded) return;
  renderAiStats();
  const query = S.aiQuery.trim().toLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  el.aiGroups.innerHTML = S.cats.map((cat) => {
    let items = S.utils.filter((u) => u.cat === cat.id);
    if (words.length) items = items.filter((u) => scoreOf(u, words) > 0);
    if (S.aiOnly) items = items.filter((u) => u.ai_on);
    if (!items.length) return "";
    const state = aiGroupState(cat.id);
    const open = words.length || S.aiOnly || (prefs.aiOpen || []).includes(cat.id);
    const all = state.usable.length > 0 && state.on.length === state.usable.length;
    const share = state.usable.length ? state.on.length / state.usable.length : 0;
    return `<section class="ut-ai-group${open ? " open" : ""}${state.on.length ? " has-on" : ""}" data-group="${esc(cat.id)}" style="--h:${hueOf(cat.id)};--share:${share}">
      <header>
        <button type="button" class="ut-ai-fold" data-fold="${esc(cat.id)}" aria-expanded="${open ? "true" : "false"}">
          <span class="ut-cat-ico">${svg(cat.id)}</span>
          <span class="ut-ai-gtitle"><b>${esc(cat.title)}</b><span class="ut-ai-bar" aria-hidden="true"><i></i></span></span>
          <span class="ut-ai-n">${esc(aiGroupCount(state))}</span>
          <svg class="ut-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        <label class="ut-switch" title="${esc(t("ai.cat.switch", { cat: cat.title }))}">
          <input type="checkbox" data-ai-cat="${esc(cat.id)}"${all ? " checked" : ""}${state.usable.length ? "" : " disabled"} aria-label="${esc(t("ai.cat.switch", { cat: cat.title }))}">
          <span class="ut-switch-ui" aria-hidden="true"></span>
        </label>
      </header>
      <div class="ut-ai-rows">${items.map((u) => {
        const usableTool = u.ai && u.available;
        const note = !u.ai ? t("ai.row.window") : !u.available ? t("badge.win.only") : (u.danger || u.danger_if) ? t("ai.row.confirm") : "";
        return `<label class="ut-ai-row${usableTool ? "" : " off"}">
          <span class="ut-switch sm"><input type="checkbox" data-ai-util="${esc(u.id)}"${u.ai_on ? " checked" : ""}${usableTool ? "" : " disabled"}><span class="ut-switch-ui" aria-hidden="true"></span></span>
          <span class="ut-ai-name"><b>${esc(u.title)}</b><code>${esc(u.id)}</code></span>
          ${note ? `<span class="ut-ai-note">${esc(note)}</span>` : ""}
          <span class="ut-ai-tok">${usableTool ? esc(fmtTokens(u.tokens || 0)) : ""}</span>
        </label>`;
      }).join("")}</div></section>`;
  }).join("") || `<div class="mu-empty">${esc(t("empty.search"))}</div>`;
  if (el.aiPreviewBox.open) loadPreview();
}
function patchAi() {
  renderAiStats();
  $$(".ut-ai-group", el.aiGroups).forEach((group) => {
    const state = aiGroupState(group.dataset.group);
    const box = $("[data-ai-cat]", group);
    if (box) box.checked = state.usable.length > 0 && state.on.length === state.usable.length;
    const n = $(".ut-ai-n", group);
    if (n) n.textContent = aiGroupCount(state);
    group.style.setProperty("--share", state.usable.length ? state.on.length / state.usable.length : 0);
    group.classList.toggle("has-on", state.on.length > 0);
  });
  $$("[data-ai-util]", el.aiGroups).forEach((box) => {
    const u = S.byId.get(box.dataset.aiUtil);
    if (u && box.checked !== !!u.ai_on) box.checked = !!u.ai_on;
  });
}
function setAiTools(ids, immediate) {
  const set = new Set(ids);
  S.utils.forEach((u) => { u.ai_on = set.has(u.id) && u.ai && u.available; });
  let tokens = SKILL_HEAD_TOKENS;
  S.utils.forEach((u) => { if (u.ai_on) tokens += u.tokens || 0; });
  const tools = S.utils.filter((u) => u.ai_on).length;
  S.skill = Object.assign({}, S.skill, { tools, tokens: tools ? tokens : 20 });
  patchAi();
  patchSide();
  patchCards();
  if (S.tool) syncToolHead(S.byId.get(S.tool.id) || S.tool);
  clearTimeout(S.aiSaveTimer);
  S.aiSaveTimer = setTimeout(() => { S.aiSaveTimer = 0; saveAi({ ai_tools: aiList() }); }, immediate ? 0 : 450);
}
async function saveAi(patch) {
  try {
    const res = await call("settings.set", patch);
    S.settings = res.settings || res;
    if (res.skill) S.skill = res.skill;
    renderAiStats();
    patchSide();
    patchCards();
    if (S.tool) syncToolHead(S.byId.get(S.tool.id) || S.tool);
    if (el.aiPreviewBox.open) loadPreview();
  } catch (error) { toast(errorText(error), "error"); }
}
async function loadPreview() {
  try {
    const res = await call("skill.preview");
    el.aiPreview.textContent = res.text || "";
    if (res.tokens != null) { S.skill = Object.assign({}, S.skill, { tools: res.tools, tokens: res.tokens }); renderAiStats(); patchSide(); }
  } catch (error) { el.aiPreview.textContent = errorText(error); }
}

/* ── background jobs: rows are kept and patched, only a new job slides in ──────────────────────────────────────── */
function liveJob(job) {
  return S.jobs.find((j) => j.id === job.id) || job;
}
function jobState(job) {
  return job.running ? "running" : job.status;
}
function jobLine(job) {
  const state = jobState(job);
  const label = { running: t("job.running"), done: t("job.done"), stopped: t("job.stopped"), error: t("job.error") }[state] || state;
  return `${label} · ${fmtMs((job.elapsed || 0) * 1000)}${job.count ? " · " + t("job.count", { n: num(job.count) }) : ""}${job.message ? " · " + job.message : ""}`;
}
function jobNode(job, fresh) {
  const node = document.createElement("div");
  node.className = "ut-job" + (fresh ? " ut-new" : "");
  node.dataset.jobId = job.id;
  node.innerHTML = `<span class="ut-job-dot" aria-hidden="true"></span>
    <span class="ut-job-meta"><b></b><small></small></span>
    <button type="button" class="mu-btn small danger" data-job-stop="${esc(job.id)}">${svg("stop")}<span>${esc(t("job.stop"))}</span></button>`;
  patchJob(node, job);
  return node;
}
function patchJob(node, job) {
  const state = jobState(job);
  if (node.dataset.state !== state) node.dataset.state = state;
  const b = $(".ut-job-meta b", node), small = $(".ut-job-meta small", node), stop = $("[data-job-stop]", node);
  if (b.textContent !== job.title) b.textContent = job.title || "";
  const line = jobLine(job);
  if (small.textContent !== line) small.textContent = line;
  stop.hidden = !job.running;
}
function syncJobList(box, jobs, fresh) {
  const known = new Map($$(".ut-job[data-job-id]", box).map((n) => [n.dataset.jobId, n]));
  $$(":scope > :not(.ut-job)", box).forEach((n) => n.remove());
  jobs.forEach((job, i) => {
    let node = known.get(job.id);
    if (node) { patchJob(node, job); known.delete(job.id); } else node = jobNode(job, fresh);
    if (box.children[i] !== node) box.insertBefore(node, box.children[i] || null);
  });
  known.forEach((node) => node.remove());
}
function renderJobs() {
  $("#jobs-stop-all").hidden = !S.jobs.some((j) => j.running);
  if (!S.jobs.length) {
    if (!$(".mu-empty", el.jobsList)) el.jobsList.innerHTML = `<div class="mu-empty">${esc(t("jobs.empty"))}</div>`;
    return;
  }
  syncJobList(el.jobsList, S.jobs, el.jobsList.dataset.ready === "1");
  el.jobsList.dataset.ready = "1";
}
function renderJobsBadge() {
  const running = S.jobs.filter((j) => j.running).length;
  el.jobsBadge.textContent = running ? t("jobs.badge", { n: num(running) }) : t("jobs.badge.none");
  $("#go-jobs").classList.toggle("live", running > 0);
}
function syncInlineJobs(fallback) {
  $$(".ut-job-inline", el.out).forEach((box) => {
    const job = S.jobs.find((j) => j.id === box.dataset.job) || (fallback && fallback.id === box.dataset.job ? fallback : null);
    if (job) syncJobList(box, [job], false);
  });
}
async function pollJobs() {
  clearTimeout(S.jobTimer);
  try {
    const res = await call("jobs.list");
    S.jobs = res.jobs || [];
  } catch (error) { /* keep the last list */ }
  renderJobsBadge();
  syncInlineJobs();
  if (S.view === "jobs") renderJobs();
  if (S.jobs.some((j) => j.running) || S.view === "jobs") S.jobTimer = setTimeout(pollJobs, S.view === "jobs" ? 1200 : 1600);
}
async function stopJob(id) {
  try {
    const res = await call("jobs.stop", id ? { id } : { all: true });
    S.jobs = res.jobs || S.jobs;
  } catch (error) { toast(errorText(error), "error"); }
  renderJobsBadge();
  syncInlineJobs();
  if (S.view === "jobs") renderJobs();
  setTimeout(pollJobs, 400);
}

/* ── events: list ──────────────────────────────────────────────────────────────────────────────────────────────── */
function pickCategory(cat) {
  const fresh = S.cat !== cat || !!S.q.trim() || S.view !== "list" || !!S.carry;
  S.cat = cat;
  S.q = "";
  el.q.value = "";
  prefs.cat = S.cat;
  savePrefs();
  stashText();
  goList({ animate: fresh });
  el.views.list.scrollTop = 0;
}
el.cats.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-cat]");
  if (btn) pickCategory(btn.dataset.cat);
});
el.q.addEventListener("input", () => {
  S.q = el.q.value;
  if (S.view !== "list") { stashText(); setView("list"); }
  clearTimeout(S.searchTimer);
  S.searchTimer = setTimeout(() => { renderList(); patchSide(); }, 60);
});
el.q.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    clearTimeout(S.searchTimer);
    renderList();
    const first = $("[data-open]", el.grid);
    if (first) { event.preventDefault(); openTool(first.dataset.open); }
  } else if (event.key === "Escape" && el.q.value) {
    event.preventDefault();
    el.q.value = "";
    S.q = "";
    renderList();
    patchSide();
  } else if (event.key === "ArrowDown") {
    const first = $("[data-open]", el.top.hidden ? el.grid : el.views.list);
    if (first) { event.preventDefault(); first.focus(); }
  }
});
async function toggleFav(id) {
  const list = (S.settings.favorites || []).slice();
  const at = list.indexOf(id);
  const adding = at < 0;
  if (adding) list.unshift(id); else list.splice(at, 1);
  S.settings.favorites = list;
  const node = S.cards.get(id);
  if (node) patchStar($(".ut-star", node), adding, true);
  if (S.tool && S.tool.id === id) patchStar(el.fav, adding, true);
  patchSide();
  if (S.view === "list" && S.cat === "fav" && !S.q.trim()) {
    if (!adding && node && node.isConnected) {
      node.classList.add("ut-leave");
      const done = () => {
        node.classList.remove("ut-leave");
        if (S.cat === "fav" && !isFav(id)) { node.remove(); if (!favIds().length) renderList(); else listTitles(visibleUtils(), ""); }
      };
      node.addEventListener("animationend", done, { once: true });
      setTimeout(() => { if (node.classList.contains("ut-leave")) done(); }, 400);
    } else renderList();
  }
  try { const res = await call("settings.set", { favorites: list }); S.settings = res.settings || res; } catch (error) { toast(errorText(error), "error"); }
}
el.grid.addEventListener("click", (event) => {
  const star = event.target.closest("[data-fav]");
  if (star) { toggleFav(star.dataset.fav); return; }
  const cat = event.target.closest("[data-cat]");
  if (cat) { pickCategory(cat.dataset.cat); return; }
  const open = event.target.closest("[data-open]");
  if (open) openTool(open.dataset.open);
});
el.topGrid.addEventListener("click", (event) => {
  const open = event.target.closest("[data-open]");
  if (open) openTool(open.dataset.open);
});
el.topFold.addEventListener("click", () => {
  prefs.topOpen = prefs.topOpen === false;
  savePrefs();
  renderTop(true, false);
});
function gridKeys(event, box, itemSel) {
  if (!["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
  const items = $$(itemSel, box);
  const index = items.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const first = items[0].getBoundingClientRect();
  const cols = Math.max(1, items.filter((n) => Math.abs(n.getBoundingClientRect().top - first.top) < 4).length);
  const step = event.key === "ArrowDown" ? cols : event.key === "ArrowUp" ? -cols : event.key === "ArrowRight" ? 1 : -1;
  const next = items[Math.max(0, Math.min(items.length - 1, index + step))];
  if (next) next.focus();
}
el.grid.addEventListener("keydown", (event) => gridKeys(event, el.grid, "[data-open]"));
el.topGrid.addEventListener("keydown", (event) => gridKeys(event, el.topGrid, "[data-open]"));
el.layoutSeg.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-layout]");
  if (!btn || prefs.layout === btn.dataset.layout) return;
  prefs.layout = btn.dataset.layout;
  savePrefs();
  renderList({ animate: true });
});
$("#carry-clear").addEventListener("click", () => { S.carry = null; renderList({ animate: true }); });

/* the hover card: mouse pause or keyboard focus on a card, gone on leave, scroll, click or Esc */
el.views.list.addEventListener("pointerover", (event) => {
  if (event.pointerType && event.pointerType !== "mouse" && event.pointerType !== "pen") return;
  const anchor = event.target.closest("[data-open]");
  if (anchor && !event.target.closest(".ut-star")) scheduleHover(anchor);
  else if (!event.target.closest(".ut-card, .ut-tile")) hideHover();
});
el.views.list.addEventListener("pointerout", (event) => {
  const from = event.target.closest(".ut-card, .ut-tile");
  if (from && !(event.relatedTarget && from.contains(event.relatedTarget))) hideHover();
});
el.views.list.addEventListener("focusin", (event) => {
  const anchor = event.target.closest("[data-open]");
  let keyboard = false;
  try { keyboard = event.target.matches(":focus-visible"); } catch (e) { keyboard = false; }
  if (anchor && keyboard) scheduleHover(anchor, 650); else hideHover();
});
el.views.list.addEventListener("focusout", (event) => { if (!event.relatedTarget || !event.relatedTarget.closest || !event.relatedTarget.closest("[data-open]")) hideHover(); });
el.views.list.addEventListener("scroll", hideHover, { passive: true });
document.addEventListener("pointerdown", hideHover, true);
window.addEventListener("blur", hideHover);

/* ── events: tool ──────────────────────────────────────────────────────────────────────────────────────────────── */
el.fav.addEventListener("click", () => { if (S.tool) toggleFav(S.tool.id); });
el.toolAi.addEventListener("change", () => {
  const u = S.tool;
  if (!u) return;
  const ids = new Set(aiList());
  if (el.toolAi.checked) ids.add(u.id); else ids.delete(u.id);
  setAiTools(Array.from(ids), true);
  toast(el.toolAi.checked ? t("tool.ai.on", { title: u.title }) : t("tool.ai.off", { title: u.title }), "ok");
});
$("#back").addEventListener("click", () => { stashText(); goList(); });
$$("[data-back]").forEach((btn) => btn.addEventListener("click", () => { stashText(); goList(); }));
$$("[data-view-btn]").forEach((btn) => btn.addEventListener("click", () => { stashText(); setView(btn.dataset.viewBtn); }));

el.text.addEventListener("input", () => { countText(); stashText(); scheduleLive(); });
el.text2.addEventListener("input", () => { countText(); S.texts.second = el.text2.value; scheduleLive(); });
el.text.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.isComposing && isLine(S.tool)) {
    event.preventDefault();
    clearTimeout(S.liveTimer);
    run();
  }
});
el.folder.addEventListener("input", () => { S.texts.folder = el.folder.value; });
el.folder.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); run(); } });
el.examples.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-example]");
  if (chip) applyExample(Number(chip.dataset.example));
});
el.paste.addEventListener("click", async () => {
  try {
    const text = await Memu.paste();
    if (!text) return toast(t("paste.empty"), "");
    const target = document.activeElement === el.text2 ? el.text2 : el.text;
    target.value = isLine(S.tool) && target === el.text ? String(text).replace(/\s*\n\s*/g, " ").trim() : text;
    stashText();
    countText();
    scheduleLive();
    focusSoft(target);
  } catch (error) { toast(errorText(error), "error"); }
});
el.clearText.addEventListener("click", () => {
  el.text.value = ""; el.text2.value = "";
  stashText();
  countText();
  $$("[data-example]", el.examples).forEach((chip) => chip.removeAttribute("aria-pressed"));
  el.text.focus();
});
el.loadText.addEventListener("click", () => el.textFile.click());
el.textFile.addEventListener("change", async () => {
  const f = el.textFile.files && el.textFile.files[0];
  el.textFile.value = "";
  if (!f) return;
  if (f.size > 3 * 1024 * 1024) return toast(t("file.text_too_big"), "error");
  try {
    el.text.value = await readAsText(f);
    stashText();
    countText();
    scheduleLive();
    toast(t("file.loaded", { name: f.name }), "ok");
  } catch (error) { toast(errorText(error), "error"); }
});
el.pickFolder.addEventListener("click", async () => {
  try {
    const res = await call("pick.folder");
    if (res && res.path) { el.folder.value = res.path; S.texts.folder = res.path; el.folder.focus(); }
  } catch (error) { toast(errorText(error), "error"); el.folder.focus(); }
});

el.drop.addEventListener("click", () => el.fileInput.click());
el.drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); el.fileInput.click(); } });
el.fileInput.addEventListener("change", () => { addFiles(el.fileInput.files); el.fileInput.value = ""; });
["dragenter", "dragover"].forEach((type) => el.views.tool.addEventListener(type, (event) => {
  if (!S.tool || !["file", "files", "any"].includes(S.tool.input)) return;
  if (!Array.from((event.dataTransfer && event.dataTransfer.types) || []).includes("Files")) return;
  event.preventDefault();
  el.drop.classList.add("over");
}));
["dragleave", "drop"].forEach((type) => el.views.tool.addEventListener(type, (event) => {
  if (type === "dragleave" && el.views.tool.contains(event.relatedTarget)) return;
  el.drop.classList.remove("over");
}));
el.views.tool.addEventListener("drop", (event) => {
  if (!S.tool || !["file", "files", "any"].includes(S.tool.input)) return;
  event.preventDefault();
  addFiles(event.dataTransfer && event.dataTransfer.files);
});
document.addEventListener("paste", (event) => {
  if (S.view !== "tool" || !S.tool || !["file", "files", "any"].includes(S.tool.input)) return;
  const files = Array.from((event.clipboardData && event.clipboardData.files) || []);
  if (!files.length) return;
  event.preventDefault();
  addFiles(files);
});
el.flist.addEventListener("click", (event) => {
  const remove = event.target.closest("[data-fremove]");
  if (remove) {
    const [gone] = S.files.splice(Number(remove.dataset.fremove), 1);
    if (gone && gone.thumb && gone.thumb.startsWith("blob:")) URL.revokeObjectURL(gone.thumb);
    renderFiles();
    return;
  }
  const move = event.target.closest("[data-fmove]");
  if (move) {
    const i = Number(move.dataset.fmove), j = i + Number(move.dataset.dir);
    if (j < 0 || j >= S.files.length) return;
    [S.files[i], S.files[j]] = [S.files[j], S.files[i]];
    renderFiles();
    const again = $(`[data-fmove="${j}"][data-dir="${move.dataset.dir}"]`, el.flist) || $(`[data-fmove="${j}"]`, el.flist);
    focusSoft(again);
  }
});

function pickChoice(btn) {
  const group = btn.closest("[data-seg]");
  if (!group || btn.getAttribute("aria-checked") === "true") return;
  $$("[data-value]", group).forEach((b) => { const on = b === btn; b.setAttribute("aria-checked", String(on)); b.tabIndex = on ? 0 : -1; });
  onOptsChanged();
}
el.opts.addEventListener("input", (event) => {
  const color = event.target.closest("[data-color-for]");
  if (color) { const picker = $(`#${color.dataset.colorFor}`, el.opts); if (picker && /^#[0-9a-f]{6}$/i.test(color.value.trim())) picker.value = color.value.trim(); }
  if (event.target.type === "color") { const text = $(`[data-color-for="${event.target.id}"]`, el.opts); if (text) text.value = event.target.value; }
  onOptsChanged();
});
el.opts.addEventListener("change", (event) => { if (event.target.matches("select, input[type=checkbox]")) onOptsChanged(); });
el.opts.addEventListener("click", (event) => {
  const choice = event.target.closest("[data-seg] [data-value]");
  if (choice) { pickChoice(choice); return; }
  const reveal = event.target.closest("[data-reveal]");
  if (reveal) {
    const input = $(`#${reveal.dataset.reveal}`, el.opts);
    if (input) input.type = input.type === "password" ? "text" : "password";
  }
});
el.opts.addEventListener("keydown", (event) => {
  const choice = event.target.closest("[data-seg] [data-value]");
  if (!choice || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const items = $$("[data-value]", choice.parentElement);
  const at = items.indexOf(choice);
  const next = event.key === "Home" ? items[0] : event.key === "End" ? items[items.length - 1]
    : items[(at + (event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length];
  next.focus();
  pickChoice(next);
});
el.optsReset.addEventListener("click", () => {
  if (!S.tool) return;
  delete prefs.opts[S.tool.id];
  savePrefs();
  renderOpts(S.tool, false);
  if (S.tool.live) scheduleLive();
});
el.model.addEventListener("change", () => { S.settings.model = el.model.value; call("settings.set", { model: el.model.value }).catch(() => {}); });
el.run.addEventListener("click", () => { clearTimeout(S.liveTimer); run(); });
el.stop.addEventListener("click", () => { if (S.stream && S.stream.cancel) S.stream.cancel(); });
el.confirmYes.addEventListener("click", () => closeConfirm(true));
el.confirmNo.addEventListener("click", () => closeConfirm(false));

el.out.addEventListener("click", async (event) => {
  const u = S.tool;
  const res = u && S.results.get(u.id);
  const copyBtn = event.target.closest("[data-copy]");
  if (copyBtn) { copyText(copyBtn.dataset.copy); return; }
  const stop = event.target.closest("[data-job-stop]");
  if (stop) { stopJob(stop.dataset.jobStop); return; }
  if (!res) return;
  const r = res.result || {};
  const act = event.target.closest("[data-oact]");
  if (act) {
    const kind = act.dataset.oact;
    if (kind === "copy") copyText(String(r.text || "").trim() && isAnswer(r, res.outputs || []) ? String(r.text).trim() : r.text || "");
    else if (kind === "download") {
      try {
        const out = await Memu.download({ name: `${u.id.replace(/\./g, "-")}.${textExt(r)}`, text: String(r.text || "") });
        toast(t("out.downloaded", { name: (out && out.name) || "" }), "ok");
      } catch (error) { toast(errorText(error), "error"); }
    } else if (kind === "reuse") {
      el.text.value = isLine(u) ? String(r.text || "").replace(/\s*\n\s*/g, " ").trim() : String(r.text || "");
      stashText();
      countText();
      toast(t("out.reused"), "ok");
      el.text.focus();
    } else if (kind === "tsv") copyText(tableText(r.table, "\t"));
    else if (kind === "csv") {
      try { await Memu.download({ name: `${u.id.replace(/\./g, "-")}.csv`, text: "\ufeff" + tableText(r.table, ",") }); toast(t("out.downloaded", { name: "CSV" }), "ok"); }
      catch (error) { toast(errorText(error), "error"); }
    } else if (kind === "save-all") saveOutput(res, "");
    else if (kind === "save-to") {
      try {
        const picked = await call("pick.folder");
        if (picked && picked.path) saveOutput(res, (res.outputs || []).length === 1 ? res.outputs[0].name : "", picked.path);
      } catch (error) { toast(errorText(error), "error"); }
    }
    return;
  }
  const fact = event.target.closest("[data-fact]");
  if (fact) {
    const card = fact.closest("[data-file]");
    const o = (res.outputs || [])[Number(card && card.dataset.file)];
    if (!o) return;
    if (fact.dataset.fact === "save") saveOutput(res, o.name);
    else if (fact.dataset.fact === "view") viewFile(res, o);
    else if (fact.dataset.fact === "next") carryFile(res, o);
  }
});
el.toast.addEventListener("click", async (event) => {
  const go = event.target.closest("[data-goto]");
  if (go) {
    clearTimeout(toastTimer);
    el.toast.classList.remove("show");
    el.toast.hidden = true;
    openTool(go.dataset.goto);
    return;
  }
  const btn = event.target.closest("[data-reveal-path]");
  if (!btn) return;
  try { await call("out.reveal", { path: btn.dataset.revealPath }); } catch (error) { toast(errorText(error), "error"); }
});
el.viewer.addEventListener("click", (event) => { if (event.target === el.viewer) closeViewer(); });
$("#viewer-close").addEventListener("click", closeViewer);
el.viewerSave.addEventListener("click", () => { if (S.viewerFile) saveOutput(S.viewerFile.res, S.viewerFile.o.name); });

/* ── events: AI access and jobs ────────────────────────────────────────────────────────────────────────────────── */
el.aiEnabled.addEventListener("change", () => {
  S.settings.ai_enabled = el.aiEnabled.checked;
  renderAiStats();
  patchSide();
  patchCards();
  saveAi({ ai_enabled: el.aiEnabled.checked });
});
el.aiDefaults.addEventListener("click", () => setAiTools(S.recommended, true));
el.aiAll.addEventListener("click", () => setAiTools(usableAi().map((u) => u.id), true));
el.aiNone.addEventListener("click", () => setAiTools([], true));
el.aiQ.addEventListener("input", () => { S.aiQuery = el.aiQ.value; renderAi(); });
el.aiOnly.addEventListener("change", () => { S.aiOnly = el.aiOnly.checked; renderAi(); });
el.aiGroups.addEventListener("change", (event) => {
  const cat = event.target.closest("[data-ai-cat]");
  const one = event.target.closest("[data-ai-util]");
  const ids = new Set(aiList());
  if (cat) {
    S.utils.filter((u) => u.cat === cat.dataset.aiCat && u.ai && u.available).forEach((u) => { if (cat.checked) ids.add(u.id); else ids.delete(u.id); });
  } else if (one) {
    if (one.checked) ids.add(one.dataset.aiUtil); else ids.delete(one.dataset.aiUtil);
  } else return;
  setAiTools(Array.from(ids));
});
el.aiGroups.addEventListener("click", (event) => {
  const fold = event.target.closest("[data-fold]");
  if (!fold) return;
  const id = fold.dataset.fold;
  const group = fold.closest(".ut-ai-group");
  const open = !group.classList.contains("open");
  group.classList.toggle("open", open);
  fold.setAttribute("aria-expanded", String(open));
  const list = new Set(prefs.aiOpen || []);
  if (open) list.add(id); else list.delete(id);
  prefs.aiOpen = Array.from(list);
  savePrefs();
});
el.aiPreviewBox.addEventListener("toggle", () => { if (el.aiPreviewBox.open) loadPreview(); });
el.jobsList.addEventListener("click", (event) => {
  const stop = event.target.closest("[data-job-stop]");
  if (stop) stopJob(stop.dataset.jobStop);
});
$("#jobs-stop-all").addEventListener("click", () => stopJob(""));
document.addEventListener("animationend", (event) => {
  const node = event.target;
  if (node.classList && node.classList.contains("ut-new")) node.classList.remove("ut-new");
  if (node.classList && node.classList.contains("ut-popping")) node.classList.remove("ut-popping");
});

document.addEventListener("keydown", (event) => {
  const mod = event.ctrlKey || event.metaKey;
  if (mod && (event.key === "k" || event.key === "K")) { event.preventDefault(); el.q.focus(); el.q.select(); return; }
  if (mod && event.key === "Enter" && S.view === "tool") { event.preventDefault(); clearTimeout(S.liveTimer); run(); return; }
  if (event.key === "Escape") {
    if (!el.hover.hidden) { hideHover(); return; }
    if (!el.viewer.hidden) { event.preventDefault(); closeViewer(); return; }
    if (!el.confirm.hidden) { event.preventDefault(); closeConfirm(false); return; }
    if (S.stream && S.stream.cancel && S.tool && S.streamFor === S.tool.id && S.view === "tool") { event.preventDefault(); S.stream.cancel(); return; }
    const typing = event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) && event.target !== el.q;
    if (typing) { event.target.blur(); return; }
    if (S.view !== "list") { event.preventDefault(); stashText(); goList(); }
    return;
  }
  if (event.key === "/" && S.view === "list" && document.activeElement !== el.q && !/^(INPUT|TEXTAREA)$/.test(document.activeElement && document.activeElement.tagName)) {
    event.preventDefault();
    el.q.focus();
  }
});

/* ── launch from the chat card / the AI command ────────────────────────────────────────────────────────────────── */
async function openFromHost(data) {
  const d = data || {};
  const args = (d.command === "open" && d.args) || d;
  if (!S.loaded) await load();
  const utilId = String(args.util || "");
  if (args.run) {
    try {
      const res = await call("run.get", { run: String(args.run) });
      const id = res.util || utilId;
      if (id && S.byId.has(id)) {
        const full = { ok: true, util: id, title: res.title, run: res.run, result: res.result || {}, outputs: res.outputs || [], ms: res.ms || 0 };
        S.results.set(id, full);
        openTool(id);
        setStatus(t("status.from_chat"), "ok");
        return;
      }
    } catch (error) { toast(errorText(error), "error"); }
  }
  if (utilId && S.byId.has(utilId)) {
    const u = S.byId.get(utilId);
    if (args.text) {
      if (isLine(u)) S.lineTexts[u.id] = String(args.text);
      else if (u.pick === "folder") S.texts.folder = String(args.text);
      else S.texts.plain = String(args.text);
      if (S.tool && S.tool.id === u.id) { S.tool = null; }
    }
    openTool(utilId);
  } else if (args.query || utilId) {
    el.q.value = String(args.query || utilId);
    S.q = el.q.value;
    stashText();
    goList({ animate: true });
  }
}
Memu.on("navigate", (data) => { openFromHost(data); });
Memu.onLanguage(() => { load(); });

renderSide();
el.grid.dataset.layout = prefs.layout === "rows" ? "rows" : "grid";
load();
if (typeof Memu.ready === "function") Memu.ready();

/* A window closed within the debounce interval still keeps the last choice: write the pending preferences and the
   AI access list right away, and stop polling. */
window.addEventListener("pagehide", () => {
  if (S.prefsTimer) { clearTimeout(S.prefsTimer); S.prefsTimer = 0; try { Memu.save({ prefs }); } catch (e) { /* closing */ } }
  if (S.aiSaveTimer) { clearTimeout(S.aiSaveTimer); S.aiSaveTimer = 0; try { call("settings.set", { ai_tools: aiList() }); } catch (e) { /* closing */ } }
  clearTimeout(S.jobTimer); clearTimeout(S.liveTimer); clearInterval(S.runTimer); clearTimeout(S.searchTimer);
});
