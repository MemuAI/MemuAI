// MemuFiles · Round 40 (r40-files-max): one-click type filter, pinned tabs, the «Details» / «Hashes» tabs of Properties,
// strings of the AI «sort by meaning» plan. Everything here is lazy: nothing runs until the user opens it.
import { t, addStrings } from "./i18n.js";
import { api, h, esc, icon, fmtNum, fmtSize, typeOf } from "./core.js";

addStrings({
  "plan.op_organize": "Sort",
  "plan.org_here": "Stays in this folder",
  "plan.why.no_model": "no AI model",
  "plan.why.not_classified": "not recognised",
  "op.organize": "Sorting by meaning",
  "ai.t_classify": "Reading documents",
  "ai.files_n": "{n} files",
  "kind.all": "All",
  "kind.folder": "Folders",
  "kind.image": "Pictures",
  "kind.video": "Videos",
  "kind.audio": "Music",
  "kind.docs": "Documents",
  "kind.archive": "Archives",
  "kind.code": "Code",
  "kind.other": "Other",
  "kind.bar": "Filter by type",
  "kind.hint": "Ctrl+Shift+F — type filter",
  "tab.pin": "Pin tab",
  "tab.unpin": "Unpin tab",
  "tab.pinned_hint": "The tab is pinned — unpin it to close",
  "props.tab_general": "General",
  "props.tab_details": "Details",
  "props.tab_hashes": "Hashes",
  "props.loading_meta": "Reading the file…",
  "props.no_meta": "No extra details for this file.",
  "props.verify": "Paste a hash to compare",
  "props.match": "Matches {alg}",
  "props.nomatch": "Does not match any hash",
  "props.copy": "Copy",
  "props.compute": "Compute",
  "meta.dimensions": "Dimensions", "meta.taken": "Date taken", "meta.camera": "Camera", "meta.lens": "Lens",
  "meta.exposure": "Exposure", "meta.iso": "ISO", "meta.focal": "Focal length", "meta.gps": "Location",
  "meta.software": "Program", "meta.pages": "Pages", "meta.title": "Title", "meta.author": "Author",
  "meta.subject": "Subject", "meta.created": "Created (document)", "meta.modified": "Saved (document)",
  "meta.words": "Words", "meta.duration": "Duration", "meta.bitrate": "Bit rate", "meta.artist": "Artist",
  "meta.album": "Album", "meta.year": "Year", "meta.genre": "Genre", "meta.track": "Track", "meta.files": "Files inside",
  "meta.unpacked": "Unpacked size", "meta.lines": "Lines", "meta.encoding": "Encoding", "meta.slides": "Slides",
  "meta.sheets": "Sheets", "meta.frames": "Frames", "meta.mode": "Colour mode", "meta.dpi": "Resolution",
}, {
  "plan.op_organize": "Разложить",
  "plan.org_here": "Остаётся в этой папке",
  "plan.why.no_model": "нет модели ИИ",
  "plan.why.not_classified": "не распознаны",
  "op.organize": "Раскладка по смыслу",
  "ai.t_classify": "Читаю документы",
  "ai.files_n": "файлов: {n}",
  "kind.all": "Все",
  "kind.folder": "Папки",
  "kind.image": "Изображения",
  "kind.video": "Видео",
  "kind.audio": "Музыка",
  "kind.docs": "Документы",
  "kind.archive": "Архивы",
  "kind.code": "Код",
  "kind.other": "Другое",
  "kind.bar": "Фильтр по типу",
  "kind.hint": "Ctrl+Shift+F — фильтр по типу",
  "tab.pin": "Закрепить вкладку",
  "tab.unpin": "Открепить вкладку",
  "tab.pinned_hint": "Вкладка закреплена — открепите её, чтобы закрыть",
  "props.tab_general": "Общие",
  "props.tab_details": "Подробно",
  "props.tab_hashes": "Хеши",
  "props.loading_meta": "Читаю файл…",
  "props.no_meta": "Дополнительных сведений нет.",
  "props.verify": "Вставьте хеш для сравнения",
  "props.match": "Совпадает с {alg}",
  "props.nomatch": "Не совпадает ни с одним хешем",
  "props.copy": "Копировать",
  "props.compute": "Вычислить",
  "meta.dimensions": "Размеры", "meta.taken": "Дата съёмки", "meta.camera": "Камера", "meta.lens": "Объектив",
  "meta.exposure": "Выдержка", "meta.iso": "ISO", "meta.focal": "Фокусное расстояние", "meta.gps": "Место съёмки",
  "meta.software": "Программа", "meta.pages": "Страниц", "meta.title": "Название", "meta.author": "Автор",
  "meta.subject": "Тема", "meta.created": "Создан (документ)", "meta.modified": "Сохранён (документ)",
  "meta.words": "Слов", "meta.duration": "Длительность", "meta.bitrate": "Битрейт", "meta.artist": "Исполнитель",
  "meta.album": "Альбом", "meta.year": "Год", "meta.genre": "Жанр", "meta.track": "Трек", "meta.files": "Файлов внутри",
  "meta.unpacked": "Размер без сжатия", "meta.lines": "Строк", "meta.encoding": "Кодировка", "meta.slides": "Слайдов",
  "meta.sheets": "Листов", "meta.frames": "Кадров", "meta.mode": "Цветовой режим", "meta.dpi": "Разрешение",
});

// ------------------------------------------------------------------ one-click type filter
const DOCS = new Set(["pdf", "doc", "sheet", "slides", "text", "book"]);
export const KINDS = ["folder", "image", "video", "audio", "docs", "archive", "code", "other"];
export function kindOf(r) {
  if (r.d) return "folder";
  const ty = typeOf(r.n, false);
  if (DOCS.has(ty)) return "docs";
  return KINDS.includes(ty) ? ty : "other";
}
export const kindMatch = (kind, r) => !kind || kindOf(r) === kind;

let barOn = localStorage.getItem("mf.kindbar") === "1";
export const kindBarOn = () => barOn;
export function toggleKindBar(app) {
  barOn = !barOn;
  localStorage.setItem("mf.kindbar", barOn ? "1" : "0");
  for (const tab of app.tabs) for (const pane of tab.panes) if (!barOn && pane.kind) { pane.kind = ""; pane.refresh(); }
  for (const pane of app.tabs[app.active].panes) renderKindBar(pane);
  if (app.updateCommands) app.updateCommands();
}

/** The chip row above a folder listing: «Все 13 · Изображения 3 · Документы 9 …» — only kinds present here. */
export function renderKindBar(pane) {
  if (!pane.el) return;
  let bar = pane.kindEl;
  const show = barOn && (pane.mode === "dir" || pane.mode === "search") && pane.items.length > 0;
  if (!show) { if (bar) bar.hidden = true; return; }
  if (!bar) {
    bar = pane.kindEl = h("div", { class: "kindbar", role: "toolbar", "aria-label": t("kind.bar") });
    pane.el.insertBefore(bar, pane.loadEl);
    bar.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-kind]");
      if (!chip) return;
      const kind = chip.dataset.kind;
      pane.kind = pane.kind === kind ? "" : kind;
      pane.refresh();
      pane.app.paneUpdated(pane);
    });
  }
  bar.hidden = false;
  const showHidden = pane.app.settings.show_hidden;
  const counts = {};
  let total = 0;
  for (const r of pane.items) { if (!showHidden && r.h && pane.mode !== "trash") continue; total++; const k = kindOf(r); counts[k] = (counts[k] || 0) + 1; }
  if (pane.kind && !counts[pane.kind]) pane.kind = "";
  const chip = (kind, n) => `<button class="kchip${(pane.kind || "") === kind ? " on" : ""}" data-kind="${kind}" aria-pressed="${(pane.kind || "") === kind}">` +
    `<span>${esc(t("kind." + (kind || "all")))}</span><b>${esc(fmtNum(n))}</b></button>`;
  const html = chip("", total) + KINDS.filter((k) => counts[k]).map((k) => chip(k, counts[k])).join("");
  if (bar.innerHTML !== html) bar.innerHTML = html;
}

// ------------------------------------------------------------------ pinned tabs
export function pinTab(app, index, on) {
  const tab = app.tabs[index];
  if (!tab) return;
  const current = app.tabs[app.active];
  tab.pinned = on;
  app.tabs.splice(index, 1);
  const lastPinned = app.tabs.reduce((acc, tb, i) => (tb.pinned ? i : acc), -1);
  app.tabs.splice(on ? lastPinned + 1 : lastPinned + 1, 0, tab);
  app.active = app.tabs.indexOf(current);
  app.renderTabs();
  app.saveSession();
}

// ------------------------------------------------------------------ Properties: «Details» and «Hashes» tabs
const META_ORDER = ["title", "author", "subject", "pages", "slides", "sheets", "words", "lines", "encoding", "dimensions", "dpi", "mode",
  "frames", "taken", "camera", "lens", "exposure", "iso", "focal", "gps", "software", "duration", "bitrate", "artist", "album",
  "year", "genre", "track", "files", "unpacked", "created", "modified"];
function metaValue(key, v) {
  if (key === "duration") { const s = Math.round(+v || 0); const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60; return (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0"); }
  if (key === "unpacked") return fmtSize(+v || 0);
  if (key === "bitrate") return Math.round((+v || 0) / 1000) + " kbps";
  if (typeof v === "number") return fmtNum(v);
  return String(v);
}

/** Turns the single-page Properties body into tabs: General (as before) · Details (metadata) · Hashes (+ verify). */
export function propertiesTabs(info, generalBody) {
  if (info.is_dir) return generalBody;
  const wrap = h("div", { class: "ptabs-wrap" });
  const bar = h("div", { class: "ptabs", role: "tablist" });
  const pages = {};
  const tabs = [["general", t("props.tab_general")], ["details", t("props.tab_details")], ["hashes", t("props.tab_hashes")]];
  const show = (id) => {
    for (const [k] of tabs) { pages[k].hidden = k !== id; bar.querySelector(`[data-t=${k}]`).setAttribute("aria-selected", String(k === id)); }
    if (id === "details" && !pages.details.dataset.loaded) loadDetails(info, pages.details);
  };
  for (const [id, label] of tabs) {
    const b = h("button", { class: "ptab", role: "tab", "data-t": id, text: label });
    b.addEventListener("click", () => show(id));
    bar.append(b);
  }
  pages.general = generalBody;
  pages.details = h("div", { class: "ptab-page details" });
  pages.hashes = hashesPage(info);
  wrap.append(bar, pages.general, pages.details, pages.hashes);
  // the page height does not jump between tabs
  pages.details.style.minHeight = pages.hashes.style.minHeight = "220px";
  show("general");
  return wrap;
}

async function loadDetails(info, page) {
  page.dataset.loaded = "1";
  page.innerHTML = `<div class="muted small"><span class="spinner"></span> ${esc(t("props.loading_meta"))}</div>`;
  let meta = {};
  try { meta = (await api("x40/meta", { path: info.path })).meta || {}; } catch (e) { meta = {}; }
  const keys = META_ORDER.filter((k) => meta[k] !== undefined && meta[k] !== "" && meta[k] !== null);
  if (!keys.length) { page.innerHTML = `<div class="muted">${esc(t("props.no_meta"))}</div>`; return; }
  const kv = h("div", { class: "kv" });
  for (const k of keys) kv.append(h("span", { text: t("meta." + k) }), h("span", { text: metaValue(k, meta[k]), title: String(meta[k]) }));
  page.innerHTML = "";
  page.append(kv);
}

function hashesPage(info) {
  const page = h("div", { class: "ptab-page hashes" });
  const list = h("div", { class: "hash-list" });
  const verify = h("input", { class: "input", placeholder: t("props.verify"), spellcheck: "false" });
  const verdict = h("div", { class: "hash-verdict small" });
  const run = h("button", { class: "btn sm", html: icon("hash") + esc(t("props.compute")) });
  let values = null;
  const check = () => {
    const want = verify.value.trim().toLowerCase().replace(/\s+/g, "");
    verdict.className = "hash-verdict small";
    if (!want || !values) { verdict.textContent = ""; return; }
    const hit = Object.entries(values).find(([, v]) => String(v).toLowerCase() === want);
    verdict.classList.add(hit ? "ok" : "bad");
    verdict.innerHTML = icon(hit ? "check" : "x", "sm") + esc(hit ? t("props.match", { alg: hit[0].toUpperCase() }) : t("props.nomatch"));
  };
  const compute = async () => {
    run.disabled = true;
    list.innerHTML = `<span class="spinner"></span>`;
    const { runJob } = await import("./dialogs.js");
    values = await runJob("hash", { path: info.path });
    list.innerHTML = "";
    for (const [alg, v] of Object.entries(values || {})) {
      const copy = h("button", { class: "icon-btn sm", title: t("props.copy"), html: icon("copy") });
      copy.addEventListener("click", () => navigator.clipboard && navigator.clipboard.writeText(String(v)));
      list.append(h("div", { class: "hash-row" }, h("b", { text: alg.toUpperCase() }), h("code", { text: String(v) }), copy));
    }
    run.hidden = true;
    check();
  };
  run.addEventListener("click", compute);
  verify.addEventListener("input", () => { if (!values && verify.value.trim().length >= 32) compute(); else check(); });
  page.append(h("div", { class: "hash-top" }, run), list, verify, verdict);
  return page;
}

// ------------------------------------------------------------------ breadcrumbs that fit (Explorer's «…» overflow)
/** A long path in a narrow address bar: the leading folders fold into a «…» button (a menu of them) instead of the bar
 *  being scrolled to the end, which cut the first visible crumb in half. The last folder always stays. */
export function fitCrumbs(app) {
  const el = app.crumbsEl;
  if (!el || !el.isConnected) return;
  el.querySelectorAll(".crumb.more").forEach((b) => b.remove());
  const kids = [...el.children];
  kids.forEach((k) => { k.hidden = false; });
  if (el.scrollWidth <= el.clientWidth + 1) return;
  const crumbs = kids.filter((k) => k.classList.contains("crumb") && !k.querySelector(".ic"));   // folders (not ⌂)
  if (crumbs.length < 2) return;
  const more = h("button", { class: "crumb more", text: "…", title: t("nav.subfolders") });
  const hiddenOnes = [];
  more.addEventListener("click", async (e) => {
    e.stopPropagation();
    const { menu } = await import("./core.js");
    const r = more.getBoundingClientRect();
    menu(hiddenOnes.slice().reverse().map((c) => ({ label: c.textContent, icon: "folder", run: () => c.click() })), r.left, r.bottom + 4);
  });
  el.insertBefore(more, kids[1] || null);
  for (const crumb of crumbs.slice(0, -1)) {
    if (el.scrollWidth <= el.clientWidth + 1) break;
    const sep = crumb.previousElementSibling && crumb.previousElementSibling.classList.contains("crumb-sep") ? crumb.previousElementSibling : null;
    crumb.hidden = true;
    if (sep) sep.hidden = true;
    hiddenOnes.push(crumb);
  }
  el.scrollLeft = 0;
}
