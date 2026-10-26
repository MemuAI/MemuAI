

import { t } from "./i18n.js";
import { S, api, ndjson, $, h, esc, icon, fileIcon, fmtSize, fmtDate, joinPath, baseName, parentPath, samePath,
  extOf, stemOf, typeOf, IMAGE_EXT, thumbUrl, showError, debounce } from "./core.js";
import { kindMatch, renderKindBar } from "./feat40.js";
import { noteHead, shellHint } from "./feat41.js";

let collator = null;

const coll = () => (collator ||= new Intl.Collator("en", { numeric: true, sensitivity: "base" }));
export function resetCollator() { collator = null; }

const HEAD_H = 30;
const GROUP_H = 34;
export const SPECIAL = { HOME: "::home", TRASH: "::trash", SEARCH: "::search" };
export const isSpecial = (p) => typeof p === "string" && p.startsWith("::");

export const DEFAULT_COLS = {
  dir: [["name", 340], ["m", 150], ["type", 140], ["s", 96], ["c", 150]],
  search: [["name", 280], ["folder", 300], ["m", 150], ["s", 96]],
  trash: [["name", 260], ["original", 300], ["deleted", 150], ["s", 96]],
};

export function folderKey(path) {
  let p = String(path || "");
  if (p.length > 3 && /[\\/]$/.test(p)) p = p.replace(/[\\/]+$/, "");
  return S.platform === "nt" ? p.replace(/\//g, "\\").toLowerCase() : (p.length > 1 ? p.replace(/\/+$/, "") : p);
}

const KNOWN_DEFAULTS = {
  downloads: { sort: { key: "m", dir: -1 }, group: "date" },
  recent: { sort: { key: "m", dir: -1 }, group: "date" },
};

export function foldersOrder(settings) {
  const v = settings && settings.folders_order;
  if (v === "top" || v === "mixed" || v === "auto") return v;
  return settings && settings.folders_first === false ? "mixed" : "auto";
}

export function tileScale(settings) {
  return { s: 0.875, m: 1, l: 1.15 }[(settings && settings.tile_size) || "m"] || 1;
}
export function knownDefault(path) {
  const known = (S.boot && S.boot.known_folders) || {};
  const key = folderKey(path);
  for (const [id, where] of Object.entries(known)) if (where && KNOWN_DEFAULTS[id] && folderKey(where) === key) return KNOWN_DEFAULTS[id];
  return null;
}

function loadCols() {
  try { return JSON.parse(localStorage.getItem("mf.cols") || "{}"); } catch (e) { return {}; }
}

export class Pane {
  constructor(app, state = {}) {
    this.app = app;
    this.id = Math.random().toString(36).slice(2, 9);
    this.path = state.path || "";
    this.history = state.history && state.history.length ? state.history.slice(-50) : (this.path ? [this.path] : []);
    this.hidx = typeof state.hidx === "number" ? Math.min(state.hidx, this.history.length - 1) : this.history.length - 1;
    this.view = state.view || app.settings.default_view || "details";
    this.sort = state.sort || { key: "name", dir: 1 };
    this.group = state.group || "none";
    this.iconSize = state.iconSize || app.settings.icon_size || 96;
    this.items = [];
    this.shown = [];
    this.rows = [];
    this.tops = [];
    this.sel = new Set();
    this.focus = -1;
    this.anchor = -1;
    this.filter = "";
    this.kind = "";
    this.loading = false;
    this.error = null;
    this.head = null;
    this.search = null;
    this.scrollMemo = {};
    this.stamp = null;
    this.hiddenCols = new Set(state.hiddenCols || ["c"]);
    this.el = null;
  }

  serialize() {
    return { path: this.path === SPECIAL.SEARCH ? (this.search && this.search.root) || S.home : this.path,
      history: this.history.filter((p) => p !== SPECIAL.SEARCH).slice(-20), view: this.view, sort: this.sort,
      group: this.group, iconSize: this.iconSize, hiddenCols: [...this.hiddenCols] };
  }
  get mode() { return this.path === SPECIAL.SEARCH ? "search" : this.path === SPECIAL.TRASH ? "trash" : this.path === SPECIAL.HOME ? "home" : "dir"; }
  get title() {
    if (this.path === SPECIAL.HOME) return t("side.home");
    if (this.path === SPECIAL.TRASH) return t("side.trash");
    if (this.path === SPECIAL.SEARCH) return t("search.title", { q: (this.search && this.search.query) || "" });
    return baseName(this.path) || this.path;
  }
  get folder() { return isSpecial(this.path) ? "" : this.path; }

  mount(host) {
    this.el = h("div", { class: "pane", "data-pane": this.id });
    this.headEl = h("div", { class: "pane-head", hidden: true });
    this.bannerEl = h("div", { class: "banner", hidden: true });
    this.fv = h("div", { class: "fv", tabindex: "0", "data-view": this.view });
    this.sizer = h("div", { class: "fv-sizer" });
    this.homeEl = h("div", { class: "home", hidden: true });
    this.fv.append(this.sizer);
    this.emptyEl = h("div", { class: "empty", hidden: true });
    this.loadEl = h("div", { class: "loading-bar", hidden: true, html: "<i></i>" });
    this.el.append(this.headEl, this.bannerEl, this.loadEl, this.fv, this.homeEl, this.emptyEl);
    host.append(this.el);
    this.bind();
    this.ro = new ResizeObserver(() => this.layout());
    this.ro.observe(this.fv);
    return this.el;
  }
  unmount() { if (this.ro) this.ro.disconnect(); if (this.abort) this.abort.abort(); if (this.el) this.el.remove(); this.el = null; }

  async navigate(path, opts = {}) {
    if (!path) return;
    if (!opts.fromHistory && !opts.reload) {
      if (this.path && !isSpecial(this.path)) this.scrollMemo[this.path] = this.fv ? this.fv.scrollTop : 0;
      if (this.history[this.hidx] !== path) {
        this.history = this.history.slice(0, this.hidx + 1);
        this.history.push(path);
        if (this.history.length > 60) this.history.shift();
        this.hidx = this.history.length - 1;
      }
    }
    const previous = this.path;
    this.path = path;
    if (path !== SPECIAL.SEARCH) this.search = null;
    if (!opts.reload) { this.filter = ""; this.kind = ""; this.sel.clear(); this.focus = -1; this.anchor = -1; }
    if (opts.select) this.pendingSelect = opts.select;
    else if (!opts.reload && previous && !isSpecial(previous) && samePath(parentPath(previous), path)) this.pendingSelect = [previous];
    this.applyFolderView();
    this.app.paneChanged(this);
    await this.load(opts.reload);
  }
  canBack() { return this.hidx > 0; }
  canForward() { return this.hidx < this.history.length - 1; }
  back() { if (this.canBack()) { this.hidx--; this.navigate(this.history[this.hidx], { fromHistory: true }); } }
  forward() { if (this.canForward()) { this.hidx++; this.navigate(this.history[this.hidx], { fromHistory: true }); } }
  up() {
    if (this.path === SPECIAL.SEARCH && this.search) return this.navigate(this.search.root || S.home);
    if (isSpecial(this.path)) return this.navigate(SPECIAL.HOME);
    const parent = parentPath(this.path);
    this.navigate(parent || SPECIAL.HOME);
  }
  reload(soft = true) { return this.navigate(this.path, { reload: soft }); }

  applyFolderView() {
    const map = this.app.settings.view_by_folder || {};
    const memo = this.mode === "dir" ? (map[folderKey(this.path)] || map[this.path]) : null;
    this.autoView = false;
    if (this.mode !== "dir") return;
    if (memo) {
      if (memo.view) this.view = memo.view;
      if (memo.sort) this.sort = memo.sort;
      if (memo.group) this.group = memo.group;
    } else {

      const known = knownDefault(this.path);
      this.view = this.app.settings.default_view || "details";

      const ds = this.app.settings.default_sort || "name";
      this.sort = known ? { ...known.sort } : { key: ds, dir: ds === "m" || ds === "s" ? -1 : 1 };
      this.group = known ? known.group : (this.app.settings.default_group || "none");
      this.autoView = true;
    }
  }

  autoPickView() {
    if (!this.autoView || this.view !== (this.app.settings.default_view || "details")) return;
    let files = 0, images = 0;
    for (const r of this.items) { if (r.d) continue; files++; if (IMAGE_EXT.has(extOf(r.n))) images++; if (files > 400) break; }
    if (files >= 4 && images / files >= 0.6) { this.view = "grid"; if (this.iconSize < 96) this.iconSize = 128; this.layout(); }
  }
  rememberView() {
    if (this.mode !== "dir") return;
    this.app.saveFolderView(this.path, { view: this.view, sort: this.sort, group: this.group });
  }

  async load(soft) {
    let p = this.loadOnce(soft);
    this.loadP = p;
    for (;;) { await p; if (this.loadP === p) return; p = this.loadP; }
  }
  async loadOnce(soft) {
    if (this.abort) this.abort.abort();
    const ctrl = new AbortController();
    this.abort = ctrl;
    this.error = null;
    const mode = this.mode;
    this.fv.dataset.view = this.view;
    if (mode === "home") {
      this.items = []; this.shown = []; this.loading = false;
      this.fv.hidden = true; this.homeEl.hidden = false; this.emptyEl.hidden = true;
      this.app.renderHome(this, this.homeEl);
      this.app.paneUpdated(this);
      return;
    }
    this.fv.hidden = false; this.homeEl.hidden = true;
    if (mode === "search") { this.app.paneUpdated(this); return; }
    const keepSel = soft ? new Set(this.sel) : null;
    const scroll = soft ? this.fv.scrollTop : (this.scrollMemo[this.path] || 0);
    this.loading = true;
    this.loadEl.hidden = false;
    let fresh = [];
    let rendered = false;
    const renderSoon = debounce(() => { if (!soft && this.abort === ctrl) { this.items = fresh.slice(); this.refresh(false); rendered = true; } }, 40);
    try {
      if (mode === "trash") {
        const data = await api("trash");
        fresh = data.items.map((r) => ({ n: r.name, d: r.is_dir ? 1 : 0, s: r.size, m: r.deleted, path: r.path, token: r.token,
          original: r.original, deleted: r.deleted, deletedText: r.deleted_text || "" }));
      } else {
        const dir = this.path;
        await ndjson("ls?path=" + encodeURIComponent(dir), (line) => {
          if (line.ok) { this.head = line; this.stamp = line.stamp; noteHead(line); }
          if (line.rows) {
            for (const r of line.rows) { r.path = joinPath(dir, r.n); fresh.push(r); }
            if (!soft && (fresh.length <= 30000 || !rendered)) renderSoon();
          }
        }, ctrl.signal);
      }
      if (this.abort !== ctrl) return;
      this.items = fresh;
      this.loading = false;
      this.loadEl.hidden = true;
      if (!soft) this.autoPickView();
      if (keepSel) { const present = new Set(fresh.map((r) => r.path)); this.sel = new Set([...keepSel].filter((p) => present.has(p))); }
      this.refresh(true);
      if (this.pendingSelect) {
        const want = new Set(this.pendingSelect.map((p) => (S.platform === "nt" ? p.toLowerCase() : p)));
        this.pendingSelect = null;
        const idx = this.shown.findIndex((r) => want.has(S.platform === "nt" ? r.path.toLowerCase() : r.path));
        if (idx >= 0) {
          this.sel = new Set(this.shown.filter((r) => want.has(S.platform === "nt" ? r.path.toLowerCase() : r.path)).map((r) => r.path));
          this.focus = this.anchor = idx;
          this.revealIndex(idx);
          this.renderVisible();
        } else this.fv.scrollTop = scroll;
      } else this.fv.scrollTop = scroll;
      if (this.shellHint) { this.shellHint = false; shellHint(this.app, this); }
      this.app.selectionChanged(this);
    } catch (err) {
      if (err.name === "AbortError") return;
      this.loading = false;
      this.loadEl.hidden = true;
      this.error = err;
      this.items = [];
      this.refresh(true);
      if (!soft) showError(err);
    }
    this.app.paneUpdated(this);
  }

  async runSearch(query, scope, root) {
    if (this.searchAbort) this.searchAbort.abort();
    const ctrl = new AbortController();
    this.searchAbort = ctrl;
    if (this.path !== SPECIAL.SEARCH) {
      root = root || this.folder || S.home;
      await this.navigate(SPECIAL.SEARCH);
    }
    this.search = { query, scope, root: root || (this.search && this.search.root) || S.home, running: true, count: 0, source: "" };
    this.items = []; this.sel.clear(); this.focus = -1;
    this.loading = true; this.loadEl.hidden = false;
    this.refresh(true);
    this.app.paneChanged(this);
    const params = new URLSearchParams({ q: query, scope, root: this.search.root, hidden: this.app.settings.show_hidden ? "1" : "0", limit: "20000" });
    const flush = debounce(() => { if (this.searchAbort === ctrl) { this.refresh(false); this.app.paneUpdated(this); } }, 60);
    try {
      await ndjson("search?" + params, (line) => {
        if (line.source) this.search.source = line.source;
        if (line.rows) {
          for (const r of line.rows) this.items.push(r);
          this.search.count = this.items.length;
          flush();
        }
        if (line.done) { this.search.done = line; }
      }, ctrl.signal);
    } catch (err) {
      if (err.name !== "AbortError") showError(err);
    }
    if (this.searchAbort !== ctrl) return;
    this.search.running = false;
    this.loading = false; this.loadEl.hidden = true;
    this.refresh(true);
    this.app.paneUpdated(this);
  }
  stopSearch() { if (this.searchAbort) { this.searchAbort.abort(); api("search/stop", {}).catch(() => {}); } if (this.search) this.search.running = false; this.loading = false; this.loadEl.hidden = true; this.app.paneUpdated(this); }

  refresh(full = true) {
    const showHidden = this.app.settings.show_hidden;
    const f = this.filter.toLowerCase();
    let list = this.items;
    if (!showHidden && this.mode !== "trash") list = list.filter((r) => !r.h);
    else if (!this.app.settings.show_system && this.mode !== "trash") list = list.filter((r) => !(r.sys && r.h));
    if (f) list = list.filter((r) => r.n.toLowerCase().includes(f));
    if (this.kind) list = list.filter((r) => kindMatch(this.kind, r));
    renderKindBar(this);
    const { key, dir } = this.sort;
    const foldersRule = foldersOrder(this.app.settings);
    const c = coll();
    const cmpName = (a, b) => c.compare(a.n, b.n);
    let cmp;
    if (key === "s") cmp = (a, b) => (a.s - b.s) * dir || cmpName(a, b);
    else if (key === "m" || key === "deleted") cmp = (a, b) => ((a.m || 0) - (b.m || 0)) * dir || cmpName(a, b);
    else if (key === "c") cmp = (a, b) => ((a.c || 0) - (b.c || 0)) * dir || cmpName(a, b);
    else if (key === "type") cmp = (a, b) => c.compare(extOf(a.n), extOf(b.n)) * dir || cmpName(a, b);
    else if (key === "folder") cmp = (a, b) => c.compare(parentPath(a.path), parentPath(b.path)) * dir || cmpName(a, b);
    else if (key === "original") cmp = (a, b) => c.compare(a.original || "", b.original || "") * dir || cmpName(a, b);
    else cmp = (a, b) => cmpName(a, b) * dir;

    const folderSign = foldersRule === "top" ? 1 : foldersRule === "mixed" ? 0 : (dir < 0 ? -1 : 1);
    const sorted = list.slice().sort(folderSign ? (a, b) => ((b.d ? 1 : 0) - (a.d ? 1 : 0)) * folderSign || cmp(a, b) : cmp);
    if (this.group !== "none") {
      const groups = new Map();
      const keyOf = this.groupKey();
      for (const r of sorted) {
        const g = keyOf(r);
        if (!groups.has(g[0])) groups.set(g[0], { order: g[1], label: g[2], rows: [] });
        groups.get(g[0]).rows.push(r);
      }
      const ordered = [...groups.values()].sort((a, b) => (a.order - b.order) || c.compare(a.label, b.label));
      this.groups = ordered.map((g) => ({ label: g.label, count: g.rows.length }));
      this.shown = ordered.flatMap((g) => g.rows);
      this.groupSizes = ordered.map((g) => g.rows.length);
    } else { this.groups = null; this.groupSizes = null; this.shown = sorted; }
    this.index = new Map();
    this.shown.forEach((r, i) => this.index.set(r.path, i));
    if (this.focus >= this.shown.length) this.focus = this.shown.length - 1;
    this.layout();
    if (full) this.app.selectionChanged(this);
  }
  groupKey() {
    const g = this.group;
    if (g === "type") return (r) => { const ty = typeOf(r.n, r.d); return [ty, ty === "folder" ? 0 : 1, t("type." + ty)]; };
    if (g === "size") return (r) => {
      if (r.d) return ["d", 0, t("group.folders")];
      const s = r.s;
      const b = s === 0 ? [1, "empty"] : s < 16384 ? [2, "tiny"] : s < 1048576 ? [3, "small"] : s < 134217728 ? [4, "medium"] : s < 1073741824 ? [5, "large"] : [6, "huge"];
      return [b[1], b[0], t("group.size." + b[1])];
    };
    if (g === "name") return (r) => { const ch = (r.n[0] || "#").toUpperCase(); const letter = /[\p{L}]/u.test(ch) ? ch : "#"; return [letter, letter === "#" ? 0 : 1, letter]; };
    if (g === "date") {
      const now = new Date();
      const day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();

      const week0 = day0 - ((now.getDay() + 6) % 7) * 86400000;
      const lastWeek0 = week0 - 7 * 86400000;
      const month0 = new Date(now.getFullYear(), now.getMonth(), 1).getTime();
      const lastMonth0 = new Date(now.getFullYear(), now.getMonth() - 1, 1).getTime();
      const year0 = new Date(now.getFullYear(), 0, 1).getTime();
      const flip = this.sort.key === "m" && this.sort.dir > 0 ? -1 : 1;
      return (r) => {
        const m = r.m || 0;
        const b = m >= day0 ? [1, "today"] : m >= day0 - 86400000 ? [2, "yesterday"] : m >= week0 ? [3, "week"] : m >= lastWeek0 ? [4, "last_week"]
          : m >= month0 ? [5, "month"] : m >= lastMonth0 ? [6, "last_month"] : m >= year0 ? [7, "year"] : [8, "older"];
        return [b[1], b[0] * flip, t("group.date." + b[1])];
      };
    }
    if (g === "tag") return (r) => { const tg = this.app.tagOf(r.path); const c = tg && tg.colors && tg.colors[0]; return [c || "~", c ? 1 : 2, c ? t("tag." + c) : t("group.untagged")]; };
    return () => ["", 0, ""];
  }

  cols() {
    const mode = this.mode === "search" ? "search" : this.mode === "trash" ? "trash" : "dir";
    const saved = loadCols()[mode] || {};
    let cols = DEFAULT_COLS[mode].filter(([k]) => k === "name" || !this.hiddenCols.has(k) || mode !== "dir").map(([k, w]) => [k, saved[k] || w]);

    const avail = this.fv ? this.fv.clientWidth - 14 : 0;
    if (avail > 0) {
      const drop = { dir: ["c", "type", "m"], search: ["m", "s"], trash: ["deleted", "s"] }[mode];
      const minName = 220;
      const pathCol = cols.find(([c]) => c === "folder" || c === "original");
      if (pathCol && !saved[pathCol[0]]) {
        const others = cols.reduce((a, [c, w]) => a + (c === "name" ? 0 : w), 0);
        if (avail - others < minName) pathCol[1] = Math.max(170, pathCol[1] - (minName - (avail - others)));
      }
      for (const k of drop) {
        const others = cols.reduce((a, [c, w]) => a + (c === "name" ? 0 : w), 0);
        if (avail - others >= minName || cols.length <= 2) break;
        cols = cols.filter(([c]) => c !== k);
      }
      const others = cols.reduce((a, [k, w]) => a + (k === "name" ? 0 : w), 0);
      const name = cols.find(([k]) => k === "name");
      if (name) name[1] = Math.max(saved.name ? Math.min(saved.name, 200) : 200, Math.max(saved.name || 0, avail - others));
    }
    return cols;
  }
  metrics() {
    const v = this.view;
    const rowH = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--row-h")) || 30;
    if (v === "details") return { kind: "rows", rowH, head: HEAD_H };
    if (v === "list") return { kind: "tiles", tileW: 250, tileH: rowH, pad: 6, gap: 0, list: true };

    if (v === "tiles") {
      const width = this.fv ? this.fv.clientWidth - 16 : 0;
      const base = Math.round(272 * tileScale(this.app.settings));
      const cols = Math.max(1, Math.floor((width + 4) / (base + 4)));
      return { kind: "tiles", tileW: width > 0 ? Math.max(200, Math.floor((width + 4) / cols) - 4) : base, tileH: Math.round(64 * tileScale(this.app.settings)), pad: 8, gap: 4, card: true };
    }
    const size = this.iconSize;
    if (v === "gallery") { const s = Math.round(size * 1.9); return { kind: "tiles", tileW: s, tileH: s + 26, pad: 10, gap: 8, gallery: true }; }

    return { kind: "tiles", tileW: Math.max(76, size + 30), tileH: size + (size >= 128 ? 76 : 58), thH: size, pad: 10, gap: 4 };
  }
  layout() {
    if (!this.fv || this.fv.hidden) return;
    const m = this.metrics();
    this.m = m;
    const width = this.fv.clientWidth;
    this.el.classList.toggle("narrow", width < 560);
    this.rows = [];
    this.tops = [];
    let y = 0;
    const pushRow = (row, height) => { this.rows.push(row); this.tops.push(y); y += height; };
    const sizes = this.groupSizes || [this.shown.length];
    let start = 0;
    if (m.kind === "rows") {
      sizes.forEach((count, gi) => {
        if (this.groups) pushRow({ g: gi }, GROUP_H);
        for (let i = start; i < start + count; i++) pushRow({ i }, m.rowH);
        start += count;
      });
      this.colsCache = this.cols();
      this.totalW = this.colsCache.reduce((a, [, w]) => a + w, 0) + 12;

      const over = this.totalW - width;
      if (over > 0 && over <= 16 && this.colsCache.length) { this.colsCache = this.colsCache.map(([k, w], ci) => [k, ci === 0 ? w - over : w]); this.totalW = width; }
    } else {
      const cols = Math.max(1, Math.floor((width - m.pad * 2 + m.gap) / (m.tileW + m.gap)));
      this.perRow = cols;
      sizes.forEach((count, gi) => {
        if (this.groups) pushRow({ g: gi }, GROUP_H);
        for (let i = start; i < start + count; i += cols) pushRow({ a: i, b: Math.min(i + cols, start + count) }, m.tileH + m.gap);
        start += count;
      });
    }
    this.totalH = y + (m.kind === "rows" ? 8 : m.pad * 2);
    this.sizer.style.height = this.totalH + "px";
    this.sizer.style.width = m.kind === "rows" ? Math.max(this.totalW, width) + "px" : "100%";
    this.fv.dataset.view = this.view;
    this.fv.classList.toggle("gallery", this.view === "gallery");
    this.renderHead();
    this.renderVisible(true);
    this.renderEmpty();
  }
  renderHead() {
    let head = this.fv.querySelector(".fv-head");
    if (this.m.kind !== "rows") { if (head) head.remove(); return; }
    if (!head) { head = h("div", { class: "fv-head" }); this.fv.insertBefore(head, this.sizer); }
    const { key, dir } = this.sort;
    head.innerHTML = (this.colsCache || this.cols()).map(([k, w]) => {
      const sorted = key === k || (k === "deleted" && key === "m");
      const r = k === "s" ? " r" : "";
      return `<div class="col${sorted ? " sorted" : ""}${r}" data-col="${k}" style="width:${w}px">${r ? (sorted ? icon(dir > 0 ? "up" : "down") : "") : ""}<span>${esc(t("col." + k))}</span>${!r && sorted ? icon(dir > 0 ? "up" : "down") : ""}<span class="grip" data-grip="${k}"></span></div>`;
    }).join("");
    head.style.width = Math.max(this.totalW, this.fv.clientWidth) + "px";
  }
  renderEmpty() {
    const empty = !this.shown.length && !this.loading;
    this.emptyEl.hidden = !empty;
    if (!empty) return;
    let html;
    if (this.error) html = icon("warn") + `<b>${esc(t("empty.error"))}</b><span>${esc(this.error.code === "permission_denied" ? t("err.permission_denied") : this.error.code === "not_found" ? t("err.not_found") : (this.error.detail || this.error.message))}</span>`;
    else if (this.mode === "search") html = icon("search") + `<b>${esc(this.search && this.search.running ? t("search.searching") : t("search.none"))}</b><span>${esc(t("search.hint"))}</span>`;
    else if (this.mode === "trash") html = icon("trash") + `<b>${esc(t("trash.empty"))}</b>`;
    else if (this.filter || this.kind) html = icon("filter") + `<b>${esc(t("empty.filter"))}</b>`;
    else html = icon("folder") + `<b>${esc(t("empty.folder"))}</b>`;
    this.emptyEl.innerHTML = html;
  }
  visibleRange() {
    const top = this.fv.scrollTop - (this.m.kind === "rows" ? HEAD_H : 0);
    const bottom = top + this.fv.clientHeight;
    const tops = this.tops;
    let lo = 0, hi = tops.length - 1, first = 0;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (tops[mid] <= top) { first = mid; lo = mid + 1; } else hi = mid - 1; }
    let last = first;
    while (last < tops.length && tops[last] < bottom) last++;
    return [Math.max(0, first - 6), Math.min(tops.length, last + 6)];
  }
  renderVisible(force) {
    if (!this.m || this.fv.hidden) return;
    const [a, b] = this.visibleRange();
    if (!force && this.lastRange && this.lastRange[0] === a && this.lastRange[1] === b && !this.dirty) return;
    this.lastRange = [a, b];
    this.dirty = false;
    const parts = [];
    const cut = this.app.cutSet();
    for (let r = a; r < b; r++) {
      const row = this.rows[r];
      const top = this.tops[r];
      if (row.g !== undefined) {
        const g = this.groups[row.g];
        parts.push(["g" + row.g, `<div class="group-head" style="top:${top}px;height:${GROUP_H}px" data-group="${row.g}">${esc(g.label)} <span class="cnt">${g.count}</span></div>`]);
      } else if (row.i !== undefined) parts.push(["i" + row.i, this.rowHtml(row.i, top, cut)]);
      else for (let i = row.a; i < row.b; i++) parts.push(["i" + i, this.tileHtml(i, top, i - row.a, cut)]);
    }

    const old = this.nodes || new Map();
    const next = new Map();
    const fresh = [];
    for (const [key, html] of parts) {
      const m = /^<div class="([^"]*)"/.exec(html);
      const cls = m ? m[1] : "";
      const body = m ? "<div" + html.slice(m[0].length) : html;
      const prev = old.get(key);
      if (prev && prev.body === body && prev.el.parentNode === this.sizer) {
        if (prev.cls !== cls) { prev.el.className = cls; prev.cls = cls; }
        next.set(key, prev); old.delete(key);
        continue;
      }
      const tpl = document.createElement("template");
      tpl.innerHTML = html;
      const el = tpl.content.firstElementChild;
      next.set(key, { el, body, cls });
      fresh.push(el);
    }
    for (const rest of old.values()) rest.el.remove();
    if (fresh.length) this.sizer.append(...fresh);
    this.nodes = next;
    if (this.renaming && this.renaming.input.parentNode !== this.sizer) this.sizer.append(this.renaming.input);
  }

  cardHtml(r, i, top, left, cls) {
    const m = this.m;
    const ext = extOf(r.n);
    const thumb = !r.d && IMAGE_EXT.has(ext) && this.mode !== "trash"
      ? `<img src="${thumbUrl(r.path, 96, r.m)}" loading="lazy" decoding="async" alt="" data-fb="${esc(fileIcon(r.n, false))}">` : fileIcon(r.n, r.d);
    const size = r.d ? (r.fs !== undefined ? fmtSize(r.fs) : "") : fmtSize(r.s);
    const third = this.mode === "search" ? parentPath(r.path) : size;
    return `<div class="${cls} card" style="top:${top}px;left:${left}px;width:${m.tileW}px;height:${m.tileH}px" data-i="${i}" draggable="true"><div class="th" style="width:${m.tileH - 16}px;height:${m.tileH - 16}px">${thumb}</div>` +
      `<div class="tc"><div class="tl" title="${esc(r.n)}">${esc(this.displayName(r))}${this.tagsHtml(r.path)}</div><div class="sub">${esc(typeLabel(r))}</div>${third ? `<div class="sub">${esc(third)}</div>` : ""}</div></div>`;
  }
  tagsHtml(path) {
    const tg = this.app.tagOf(path);
    if (!tg) return "";
    return (tg.favorite ? `<span class="fav">${icon("star", "sm")}</span>` : "") +
      (tg.colors && tg.colors.length ? `<span class="tags">${tg.colors.map((c) => `<i class="dot ${c}"></i>`).join("")}</span>` : "");
  }
  displayName(r) {
    if (!r.d && this.app.settings.show_extensions === false) { const s = stemOf(r.n); return s || r.n; }
    return r.n;
  }
  rowHtml(i, top, cut) {
    const r = this.shown[i];
    const cls = "row" + (this.sel.has(r.path) ? " sel" : "") + (i === this.focus ? " focus" : "") + (cut && cut.has(r.path) ? " cut" : "") + (r.h ? " hidden-file" : "");
    const cells = (this.colsCache || this.cols()).map(([k, w]) => {
      if (k === "name") return `<div class="cell name" style="width:${w}px">${fileIcon(r.n, r.d, r.l ? `<span class="lnk">${icon("link")}</span>` : "")}<span class="nm-text">${esc(this.displayName(r))}</span>${this.tagsHtml(r.path)}</div>`;
      let v = "";
      if (k === "m") v = fmtDate(r.m);
      else if (k === "c") v = fmtDate(r.c);
      else if (k === "deleted") v = r.deleted ? fmtDate(r.deleted) : (r.deletedText || "");
      else if (k === "s") v = r.d ? (r.fs !== undefined ? fmtSize(r.fs) : "") : fmtSize(r.s);
      else if (k === "type") v = typeLabel(r);
      else if (k === "folder") v = parentPath(r.path);
      else if (k === "original") v = parentPath(r.original || "");
      return `<div class="cell${k === "s" ? " r" : ""}${k === "folder" || k === "original" ? " path" : ""}" style="width:${w}px" title="${k === "folder" || k === "original" ? esc(v) : ""}">${esc(v)}</div>`;
    }).join("");
    return `<div class="${cls}" style="top:${top}px;width:${Math.max(this.totalW, this.fv.clientWidth) - 4}px;left:2px" data-i="${i}" draggable="true">${cells}</div>`;
  }
  tileHtml(i, top, col, cut) {
    const r = this.shown[i];
    const m = this.m;
    const left = m.pad + col * (m.tileW + m.gap);
    const cls = "tile" + (this.sel.has(r.path) ? " sel" : "") + (i === this.focus ? " focus" : "") + (cut && cut.has(r.path) ? " cut" : "") + (r.h ? " hidden-file" : "");
    if (m.list) {
      return `<div class="row${cls.slice(4)}" style="top:${top}px;left:${left}px;width:${m.tileW - 4}px" data-i="${i}" draggable="true"><div class="cell name" style="width:100%">${fileIcon(r.n, r.d)}<span class="nm-text">${esc(this.displayName(r))}</span>${this.tagsHtml(r.path)}</div></div>`;
    }
    if (m.card) return this.cardHtml(r, i, top, left, cls);
    const ext = extOf(r.n);
    let thumb;
    const px = m.gallery ? Math.min(512, Math.round(m.tileW * 1.5)) : (this.iconSize >= 128 ? 256 : 160);
    if (!r.d && IMAGE_EXT.has(ext) && this.iconSize >= 48 && this.mode !== "trash") {
      thumb = `<img src="${thumbUrl(r.path, px, r.m)}" loading="lazy" decoding="async" alt="" data-fb="${esc(fileIcon(r.n, false))}">`;
    } else thumb = fileIcon(r.n, r.d);
    const sub = m.gallery ? "" : (this.iconSize >= 128 && !r.d ? `<div class="sub">${esc(fmtSize(r.s))}</div>` : "");
    return `<div class="${cls}" style="top:${top}px;left:${left}px;width:${m.tileW}px;height:${m.tileH}px" data-i="${i}" draggable="true"><div class="th"${m.thH ? ` style="flex:none;height:${m.thH}px"` : ""}>${thumb}</div><div class="tl" title="${esc(r.n)}">${esc(this.displayName(r))}${this.tagsHtml(r.path)}</div>${sub}</div>`;
  }

  selected() { return this.shown.filter((r) => this.sel.has(r.path)); }
  selectedPaths() { return this.selected().map((r) => r.path); }
  setSelection(indices, focus) {
    this.sel = new Set(indices.map((i) => this.shown[i] && this.shown[i].path).filter(Boolean));
    if (focus !== undefined) this.focus = focus;
    this.dirty = true; this.renderVisible(true);
    this.app.selectionChanged(this);
  }
  selectAll() { this.sel = new Set(this.shown.map((r) => r.path)); this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); }
  selectNone() { this.sel.clear(); this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); }
  invertSelection() { this.sel = new Set(this.shown.filter((r) => !this.sel.has(r.path)).map((r) => r.path)); this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); }
  selectWhere(fn) { this.sel = new Set(this.shown.filter(fn).map((r) => r.path)); this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); }
  rowOfItem(i) {

    for (let r = 0; r < this.rows.length; r++) {
      const row = this.rows[r];
      if (row.i === i || (row.a !== undefined && i >= row.a && i < row.b)) return r;
    }
    return -1;
  }

  keyboardMenu() {
    const i = this.focus >= 0 && this.shown[this.focus] && this.sel.has(this.shown[this.focus].path) ? this.focus : this.shown.findIndex((r) => this.sel.has(r.path));
    if (i >= 0) {
      this.scrollTo(i);
      const el = this.fv.querySelector(`[data-i="${i}"]`);
      const r = (el || this.fv).getBoundingClientRect();
      this.app.contextMenu(this, Math.round(r.left + Math.min(48, r.width / 2)), Math.round(r.top + r.height / 2), true);
    } else {
      const r = this.fv.getBoundingClientRect();
      this.app.contextMenu(this, Math.round(r.left + 24), Math.round(r.top + 40), false);
    }
  }

  selectAndReveal(paths) {
    const want = new Set(paths.map((p) => (S.platform === "nt" ? p.toLowerCase() : p)));
    const key = (r) => (S.platform === "nt" ? r.path.toLowerCase() : r.path);
    if (!this.shown.some((r) => want.has(key(r))) && this.items.some((r) => want.has(key(r))) && (this.filter || this.kind)) {
      this.filter = ""; this.kind = ""; this.refresh(false);
    }
    const idx = this.shown.findIndex((r) => want.has(key(r)));
    if (idx < 0) return false;
    this.sel = new Set(this.shown.filter((r) => want.has(key(r))).map((r) => r.path));
    this.focus = this.anchor = idx;
    this.revealIndex(idx);
    this.renderVisible(true);
    this.app.selectionChanged(this);
    return true;
  }
  revealIndex(i) {
    const r = this.rowOfItem(i);
    if (r < 0) return;
    const headH = this.m.kind === "rows" ? HEAD_H : 0;
    const top = this.tops[r];
    const viewH = this.fv.clientHeight - headH;
    if (top < this.fv.scrollTop || top + (this.m.tileH || this.m.rowH || 30) > this.fv.scrollTop + viewH) this.fv.scrollTop = Math.max(0, top - Math.round(viewH / 3));
    if (document.activeElement !== this.fv && !document.querySelector(".modal-back")) this.fv.focus({ preventScroll: true });
  }
  scrollTo(i) {
    const r = this.rowOfItem(i);
    if (r < 0) return;
    const headH = this.m.kind === "rows" ? HEAD_H : 0;
    const top = this.tops[r];
    const height = (this.tops[r + 1] || this.totalH) - top;
    const viewTop = this.fv.scrollTop;
    const viewH = this.fv.clientHeight - headH;
    if (top < viewTop) this.fv.scrollTop = top;
    else if (top + height > viewTop + viewH) this.fv.scrollTop = top + height - viewH;
  }

  bind() {
    const fv = this.fv;
    fv.addEventListener("scroll", () => {
      if (this.raf) return;
      this.raf = requestAnimationFrame(() => { this.raf = 0; this.renderVisible(); });
    }, { passive: true });
    fv.addEventListener("focus", () => this.app.activatePane(this));
    this.el.addEventListener("mousedown", () => this.app.activatePane(this), true);
    fv.addEventListener("mousedown", (e) => this.onMouseDown(e));
    const itemAt = (e) => e.target.closest("[data-i]") || (document.elementFromPoint(e.clientX, e.clientY) || fv).closest("[data-i]");
    fv.addEventListener("dblclick", (e) => {
      const el = itemAt(e);
      if (el) this.app.openItem(this, this.shown[+el.dataset.i]);
      else if (!e.target.closest(".fv-head") && this.app.settings.dblclick_up) this.up();
    });
    fv.addEventListener("click", (e) => {
      const col = e.target.closest(".col");
      if (col && !e.target.classList.contains("grip")) this.sortBy(col.dataset.col);
      if (this.app.settings.single_click && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
        const el = itemAt(e);
        if (el) this.app.openItem(this, this.shown[+el.dataset.i]);
      }
    });
    fv.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (this.kbMenuAt && performance.now() - this.kbMenuAt < 800) return;
      if (e.target.closest(".fv-head")) { this.app.columnsMenu(this, e.clientX, e.clientY); return; }
      const el = e.target.closest("[data-i]");
      if (el) {
        const i = +el.dataset.i;
        const r = this.shown[i];
        if (!this.sel.has(r.path)) { this.setSelection([i], i); this.anchor = i; }
      } else if (!e.ctrlKey) this.selectNone();
      this.app.contextMenu(this, e.clientX, e.clientY, !!el);
    });

    fv.addEventListener("mousedown", (e) => {
      const grip = e.target.closest("[data-grip]");
      if (!grip) return;
      e.preventDefault(); e.stopPropagation();
      const key = grip.dataset.grip;
      const mode = this.mode === "search" ? "search" : this.mode === "trash" ? "trash" : "dir";
      const startX = e.clientX;
      const startW = (this.cols().find(([k]) => k === key) || [0, 120])[1];
      document.body.classList.add("resizing");
      const move = (ev) => {
        const all = loadCols();
        all[mode] = all[mode] || {};
        all[mode][key] = Math.max(60, Math.min(900, startW + ev.clientX - startX));
        localStorage.setItem("mf.cols", JSON.stringify(all));
        this.layout();
      };
      const upFn = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", upFn); document.body.classList.remove("resizing"); };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", upFn);
    }, true);

    fv.addEventListener("dragstart", (e) => {
      const el = e.target.closest("[data-i]");
      if (!el) return;
      const i = +el.dataset.i;
      if (!this.sel.has(this.shown[i].path)) { this.setSelection([i], i); this.anchor = i; }
      this.app.dragStart(this, e, this.selectedPaths());
    });
    fv.addEventListener("dragover", (e) => {
      const target = this.dropTarget(e);
      if (!target) return;
      this.app.dragOver(e, target.path, target.el);
    });
    fv.addEventListener("dragleave", (e) => { if (!fv.contains(e.relatedTarget)) this.app.dragLeave(); });
    fv.addEventListener("drop", (e) => {
      const target = this.dropTarget(e);
      if (!target) return;
      this.app.drop(e, target.path);
    });
    this.homeEl.addEventListener("contextmenu", (e) => e.preventDefault());

    fv.addEventListener("error", (e) => {
      const img = e.target;
      if (img && img.tagName === "IMG" && img.dataset.fb) { const span = document.createElement("span"); span.innerHTML = img.dataset.fb; img.replaceWith(span.firstChild || span); }
    }, true);
  }
  dropTarget(e) {
    if (this.mode === "trash" || this.mode === "home") return null;
    const el = e.target.closest("[data-i]");
    if (el) {
      const r = this.shown[+el.dataset.i];
      if (r && r.d) return { path: r.path, el };
    }
    if (this.mode === "search") return null;
    return { path: this.path, el: this.fv };
  }
  sortBy(key) {
    if (!key) return;
    if (key === "deleted") key = "m";
    if (this.sort.key === key) this.sort = { key, dir: -this.sort.dir };
    else this.sort = { key, dir: key === "m" || key === "s" || key === "c" ? -1 : 1 };
    this.rememberView();
    this.refresh(false);
    this.app.paneUpdated(this);
  }
  setView(view) {
    this.view = view;
    this.rememberView();
    this.layout();
    if (this.focus >= 0) this.scrollTo(this.focus);
    this.app.paneUpdated(this);
  }
  setGroup(group) { this.group = group; this.rememberView(); this.refresh(false); this.app.paneUpdated(this); }

  onMouseDown(e) {
    if (e.button !== 0 && e.button !== 2) return;
    if (e.target.closest(".fv-head") || e.target.closest(".rename-input")) return;
    const el = e.target.closest("[data-i]");
    if (this.renaming && !e.target.closest(".rename-input")) this.commitRename();
    if (e.button === 2) return;
    if (el) {
      const i = +el.dataset.i;
      const r = this.shown[i];
      if (e.shiftKey && this.anchor >= 0) {
        const [a, b] = this.anchor < i ? [this.anchor, i] : [i, this.anchor];
        const range = [];
        for (let k = a; k <= b; k++) range.push(k);
        if (e.ctrlKey || e.metaKey) { range.forEach((k) => this.sel.add(this.shown[k].path)); this.focus = i; this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); }
        else this.setSelection(range, i);
      } else if (e.ctrlKey || e.metaKey) {
        if (this.sel.has(r.path)) this.sel.delete(r.path); else this.sel.add(r.path);
        this.focus = this.anchor = i;
        this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this);
      } else if (!this.sel.has(r.path)) {
        this.setSelection([i], i); this.anchor = i;
      } else {
        this.focus = i; this.anchor = i;

        const up = (ev) => { document.removeEventListener("mouseup", up); if (Math.abs(ev.clientX - e.clientX) < 4 && Math.abs(ev.clientY - e.clientY) < 4 && this.sel.size > 1) { this.setSelection([i], i); } };
        document.addEventListener("mouseup", up);
        this.dirty = true; this.renderVisible(true);
      }
      return;
    }
    if (e.target === this.fv && e.offsetX > this.fv.clientWidth) return;
    this.startMarquee(e);
  }
  startMarquee(e) {
    const fvRect = this.fv.getBoundingClientRect();
    const headH = this.m.kind === "rows" ? HEAD_H : 0;
    const startX = e.clientX - fvRect.left + this.fv.scrollLeft;
    const startY = e.clientY - fvRect.top + this.fv.scrollTop - headH;
    const base = e.ctrlKey || e.metaKey ? new Set(this.sel) : new Set();
    if (!e.ctrlKey && !e.metaKey && this.sel.size) this.selectNone();
    let box = null;
    let lastY = e.clientY, lastX = e.clientX;
    const update = () => {
      const x = lastX - fvRect.left + this.fv.scrollLeft;
      const y = lastY - fvRect.top + this.fv.scrollTop - headH;
      const x1 = Math.min(startX, x), x2 = Math.max(startX, x), y1 = Math.min(startY, y), y2 = Math.max(startY, y);
      if (!box) {
        if (Math.abs(x - startX) < 4 && Math.abs(y - startY) < 4) return;
        box = h("div", { class: "marquee" });
        this.fv.append(box);
      }
      Object.assign(box.style, { left: x1 + "px", top: (y1 + headH) + "px", width: (x2 - x1) + "px", height: (y2 - y1) + "px" });
      const sel = new Set(base);
      const m = this.m;
      for (let r = 0; r < this.rows.length; r++) {
        const top = this.tops[r];
        const row = this.rows[r];
        const height = row.g !== undefined ? GROUP_H : (m.kind === "rows" ? m.rowH : m.tileH);
        if (top + height < y1) continue;
        if (top > y2) break;
        if (row.i !== undefined) { if (x1 < this.totalW) sel.add(this.shown[row.i].path); }
        else if (row.a !== undefined) {
          for (let i = row.a; i < row.b; i++) {
            const left = m.pad + (i - row.a) * (m.tileW + m.gap);
            if (left + m.tileW >= x1 && left <= x2) sel.add(this.shown[i].path);
          }
        }
      }
      this.sel = sel;
      this.dirty = true; this.renderVisible(true);
    };
    let scrollTimer = null;
    const move = (ev) => {
      lastX = ev.clientX; lastY = ev.clientY;
      update();
      clearInterval(scrollTimer);
      if (ev.clientY > fvRect.bottom - 20 || ev.clientY < fvRect.top + headH + 10) {
        scrollTimer = setInterval(() => { this.fv.scrollTop += lastY > fvRect.bottom - 20 ? 24 : -24; update(); }, 30);
      }
    };
    const upFn = () => {
      clearInterval(scrollTimer);
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", upFn);
      if (box) box.remove();
      this.app.selectionChanged(this);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", upFn);
  }

  onKey(e) {
    const n = this.shown.length;
    const ctrl = e.ctrlKey || e.metaKey;
    const move = (to) => {
      to = Math.max(0, Math.min(n - 1, to));
      if (n === 0) return;
      if (e.shiftKey) {
        if (this.anchor < 0) this.anchor = Math.max(0, this.focus);
        const [a, b] = this.anchor < to ? [this.anchor, to] : [to, this.anchor];
        const range = [];
        for (let k = a; k <= b; k++) range.push(k);
        this.setSelection(range, to);
      } else if (ctrl) { this.focus = to; this.dirty = true; this.renderVisible(true); }
      else { this.setSelection([to], to); this.anchor = to; }
      this.scrollTo(to);
    };
    const per = this.m && this.m.kind === "tiles" ? this.perRow || 1 : 1;
    const page = Math.max(1, Math.floor(this.fv.clientHeight / ((this.m && (this.m.rowH || this.m.tileH)) || 30)) * per);
    const cur = this.focus;
    switch (e.key) {
      case "ArrowDown": move(cur < 0 ? 0 : cur + per); return true;
      case "ArrowUp": move(cur < 0 ? 0 : cur - per); return true;
      case "ArrowRight": if (per > 1) { move(cur + 1); return true; } return false;
      case "ArrowLeft": if (per > 1) { move(cur - 1); return true; } return false;
      case "PageDown": move(cur + page); return true;
      case "PageUp": move(cur - page); return true;
      case "Home": move(0); return true;
      case "End": move(n - 1); return true;
      case " ":
        if (ctrl && cur >= 0) { const p = this.shown[cur].path; if (this.sel.has(p)) this.sel.delete(p); else this.sel.add(p); this.dirty = true; this.renderVisible(true); this.app.selectionChanged(this); return true; }
        if (!this.typeBuffer && cur >= 0) { if (!this.sel.has(this.shown[cur].path)) this.setSelection([cur], cur); this.app.togglePreview(true); return true; }
        break;
      case "ContextMenu": case "F10":
        if (e.key === "F10" && !e.shiftKey) break;
        this.kbMenuAt = performance.now();
        this.keyboardMenu();
        return true;
      default: break;
    }
    if (e.key.length === 1 && !ctrl && !e.altKey && /[\p{L}\p{N}._\- ]/u.test(e.key)) {

      clearTimeout(this.typeTimer);
      this.typeBuffer = (this.typeBuffer || "") + e.key.toLowerCase();
      this.typeTimer = setTimeout(() => { this.typeBuffer = ""; }, 900);
      const buf = this.typeBuffer;
      const same = buf.length > 1 && buf.split("").every((c) => c === buf[0]);
      const startAt = buf.length === 1 || same ? cur + 1 : Math.max(0, cur);
      const probe = same ? buf[0] : buf;
      for (let k = 0; k < n; k++) {
        const i = (startAt + k) % n;
        if (this.shown[i].n.toLowerCase().startsWith(probe)) { this.setSelection([i], i); this.anchor = i; this.scrollTo(i); break; }
      }
      return true;
    }
    return false;
  }

  startRename(i) {
    if (i === undefined) i = this.focus >= 0 && this.sel.has(this.shown[this.focus].path) ? this.focus : this.shown.findIndex((r) => this.sel.has(r.path));
    if (i < 0 || this.mode === "trash" || this.mode === "home") return false;
    this.scrollTo(i);
    this.renderVisible(true);
    const el = this.sizer.querySelector(`[data-i="${i}"]`);
    if (!el) return false;
    const r = this.shown[i];
    const tile = this.m.kind === "tiles" && !this.m.list;
    const input = h(tile ? "textarea" : "input", { class: "rename-input", spellcheck: "false" });
    input.value = r.n;
    const nameEl = el.querySelector(tile ? ".tl" : ".nm-text");
    const base = this.sizer.getBoundingClientRect();
    const rect = (nameEl || el).getBoundingClientRect();
    if (tile) Object.assign(input.style, { left: (rect.left - base.left - 4) + "px", top: (rect.top - base.top - 3) + "px", width: (el.offsetWidth - 4) + "px", rows: 3 });
    else Object.assign(input.style, { left: (rect.left - base.left - 6) + "px", top: (el.offsetTop + (el.offsetHeight - 26) / 2) + "px", width: Math.max(220, Math.min(480, rect.width + 80)) + "px" });
    this.renaming = { input, item: r };
    this.sizer.append(input);
    input.focus();
    const stem = r.d ? r.n.length : stemOf(r.n).length;
    input.setSelectionRange(0, stem || r.n.length);
    input.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Enter") { ev.preventDefault(); this.commitRename(); this.fv.focus(); }
      else if (ev.key === "Escape") { ev.preventDefault(); this.cancelRename(); this.fv.focus(); }
      else if (ev.key === "Tab") { ev.preventDefault(); const next = i + (ev.shiftKey ? -1 : 1); this.commitRename().then(() => { if (next >= 0 && next < this.shown.length) { this.setSelection([next], next); this.startRename(next); } }); }
    });
    input.addEventListener("blur", () => setTimeout(() => { if (this.renaming && this.renaming.input === input) this.commitRename(); }, 80));
    return true;
  }
  cancelRename() { if (this.renaming) { this.renaming.input.remove(); this.renaming = null; this.reloadDeferred(); } }

  reloadDeferred() { if (this.reloadAfterRename) { this.reloadAfterRename = false; this.reload(true); } }
  async commitRename() {
    const state = this.renaming;
    if (!state) return;
    this.renaming = null;
    state.input.remove();
    const name = state.input.value.trim();
    if (!name || name === state.item.n) { this.reloadDeferred(); return; }
    this.reloadAfterRename = false;
    try {
      const res = await api("rename", { path: state.item.path, name });
      this.pendingSelect = [res.path];
      await this.reload(true);
    } catch (err) { showError(err); }
  }
}

export function typeLabel(r) {
  if (r.d) return t("type.folder");
  const ext = extOf(r.n);
  const ty = typeOf(r.n, false);
  if (ty === "file") return ext ? t("type.file_ext", { ext: ext.toUpperCase() }) : t("type.file");
  return t("type." + ty) + (ext ? " · " + ext.toUpperCase() : "");
}
