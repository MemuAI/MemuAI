// MemuFiles · application shell: tabs, split panes, sidebar, address bar, commands, clipboard, drag & drop,
// keyboard shortcuts (Explorer-compatible), events from the backend, session restore (Round 35).
import { t, setLang, getLang } from "./i18n.js";
import { S, api, $, $$, h, esc, icon, fileIcon, folderSvg, fmtSize, fmtNum, itemsWord, plural, joinPath, baseName,
  parentPath, splitPath, samePath, isInside, driveOf, extOf, stemOf, toast, showError, errText, menu, closeMenus,
  confirmDialog, promptDialog, debounce, isWin } from "./core.js";
import { Pane, SPECIAL, isSpecial, resetCollator, folderKey, foldersOrder } from "./pane.js";
import * as dialogs from "./dialogs.js";
import * as preview from "./preview.js";
import * as ai from "./ai.js";
import * as tools from "./tools.js";
import * as titlebar from "./titlebar.js";
import * as extras from "./extras.js";       // Round 39 (r39-files-feat): Quick Look, compare, templates…
import { Ops } from "./ops.js";               // Round 40 (r40-files-core): operations flyout, progress, conflicts
import * as dnd from "./dnd.js";              // Round 40 (r40-files-core): drag & drop incl. virtual files
import * as feat40 from "./feat40.js";        // Round 40 (r40-files-max): type filter, pinned tabs, Properties tabs
import * as feat41 from "./feat41.js";        // r41-files: settings window, look, archives as folders, «show in folder»

const ARCHIVE_EXT = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"]);

class App {
  constructor() {
    this.tabs = [];
    this.active = 0;
    this.closed = [];
    this.clipboard = null;           // {paths: [], cut: bool, set: Set}
    this.tags = new Map();
    this.sidebar = { drives: [], places: [], pinned: [] };
    this.ops = new Map();
    this.opsUi = new Ops(this);      // r40-files-core
    this.right = null;               // null | "preview" | "ai"
    this.settings = {};
    this.eventsSeq = 0;
  }

  // ================================================================ boot
  async boot() {
    const hash = new URLSearchParams(location.hash.slice(1));
    S.token = hash.get("t") || sessionStorage.getItem("mf.token") || "";
    sessionStorage.setItem("mf.token", S.token);
    history.replaceState(null, "", location.pathname);
    let boot;
    try { boot = await api("boot"); } catch (err) {
      document.body.innerHTML = `<div class="boot-splash"><b>MemuFiles</b><span class="muted">${esc(err.message)}</span></div>`;
      return;
    }
    S.boot = boot; S.sep = boot.sep; S.platform = boot.platform; S.home = boot.home;
    this.settings = boot.settings;
    this.eventsSeq = boot.events_seq;
    setLang(boot.lang);
    this.applyTheme(boot.theme);
    this.setTags(boot.tags);
    document.documentElement.dataset.density = this.settings.density || "normal";
    this.build();
    const session = boot.session && Array.isArray(boot.session.tabs) ? boot.session : null;
    if (session && session.tabs.length) {
      for (const tab of session.tabs) this.addTab(tab, false, true);
      this.closed = session.closed || [];
      this.switchTab(Math.min(session.active || 0, this.tabs.length - 1));
    }
    if (boot.paths && boot.paths.length) for (const p of boot.paths) this.openPathInNewTab(p, (boot.path_info || {})[p], this.openOpts(boot, p));   // r39-files-perf: no /api/stat round trip
    if (!this.tabs.length) this.addTab({ panes: [{ path: feat41.startPath(this) }] }, true);   // r41-files: «A new window opens»
    // r40-files-max: in a narrow window (≤760 px) the preview is an overlay over the list — it is not opened by itself on
    // start (it covered the list, the search banner and Home); the user opens it with Alt+P when wanted
    const roomy = innerWidth > 760;
    if (session && session.layout) {
      if (session.layout.right && (roomy || session.layout.right !== "preview")) this.setRight(session.layout.right, false);
    } else if (this.settings.preview_open && roomy) this.setRight("preview", false);
    document.body.classList.remove("booting");
    $(".boot-splash") && $(".boot-splash").remove();
    this.loadSidebar();
    setInterval(() => { if (!document.hidden) this.loadSidebar(); }, 30000);   // r39-files-perf: nothing while hidden/minimized
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { this.checkStamps(); this.loadSidebar(); } });
    this.pollEvents();
    window.addEventListener("focus", () => this.reloadSettings());   // Round 38: a change made in MemuAI while the event was missed
    setInterval(() => this.checkStamps(), 2500);
    this.refreshOps();
  }

  applyTheme(theme) {
    const root = document.documentElement;
    root.dataset.theme = theme.mode;
    const tokens = theme.tokens || {};
    root.classList.toggle("fallback-tokens", !Object.keys(tokens).length);
    for (const name of [...root.style]) if (name.startsWith("--")) root.style.removeProperty(name);
    for (const [k, v] of Object.entries(tokens)) root.style.setProperty(k, v);
    document.getElementById("mf-theme")?.remove();     // r39-files-perf: the first-frame palette the server put in index.html
    if (theme.font_scale && theme.font_scale !== 100) root.style.fontSize = theme.font_scale + "%";
    this.theme = theme;
    feat41.applyLook(this);   // r41-files: accent, font, text size, row shading, formats… (after the theme's tokens)
    api("window", { cmd: "theme", mode: theme.mode }).catch(() => {});
  }

  setTags(rows) {
    this.tags = new Map();
    for (const row of rows || []) this.tags.set(this.tagKey(row.path), row);
  }
  tagKey(path) { return isWin() ? path.toLowerCase() : path; }
  tagOf(path) { return this.tags.size ? this.tags.get(this.tagKey(path)) : null; }

  // ================================================================ DOM
  build() {
    const app = h("div", { id: "app" });
    this.tabbar = h("header", { class: "tabbar" });
    this.navbar = h("div", { class: "navbar" });
    this.cmdbar = h("div", { class: "cmdbar" });
    this.bodyEl = h("main", { class: "body" });
    this.sidebarEl = h("aside", { class: "sidebar", style: { width: (this.settings.sidebar_width || 236) + "px" } });
    this.sideResizer = h("div", { class: "resizer side" });
    this.panesEl = h("section", { class: "panes" });
    this.rightResizer = h("div", { class: "resizer", hidden: true });
    this.rightEl = h("aside", { class: "rightpanel", hidden: true, style: { width: (this.settings.preview_width || 340) + "px" } });
    this.statusEl = h("footer", { class: "statusbar" });
    this.bodyEl.append(this.sidebarEl, this.sideResizer, this.panesEl, this.rightResizer, this.rightEl);
    app.append(this.tabbar, this.navbar, this.cmdbar, this.bodyEl, this.statusEl);
    document.body.append(app);
    this.buildTabbar();
    titlebar.initTitlebar(this.tabbar);
    this.buildNavbar();
    this.buildCmdbar();
    this.buildStatus();
    this.bindResizers();
    this.bindKeys();
    this.bindGlobalDrop();
    // labels hide only when the buttons really do not fit (a fixed 900 px cut clipped «Preview»/«AI» at 900–1000 px)
    new ResizeObserver(() => {
      const bar = this.cmdbar;
      bar.classList.remove("narrow");
      bar.classList.toggle("narrow", bar.scrollWidth > bar.clientWidth + 1);
    }).observe(this.cmdbar);
    window.addEventListener("beforeunload", () => this.saveSession(true));
  }

  // ---------------------------------------------------------------- tabs
  buildTabbar() {
    this.tabbar.innerHTML = "";
    const logo = h("img", { class: "logo", src: "memufiles-small.svg", alt: "" });
    this.tabsEl = h("div", { class: "tabs", role: "tablist" });
    const add = h("button", { class: "icon-btn sm tab-add", title: t("tab.new") + " (Ctrl+T)", html: icon("plus") });
    add.addEventListener("click", () => this.newTab());
    const right = h("div", { class: "tb-right" });
    const settings = h("button", { class: "icon-btn sm", title: t("cmd.settings"), html: icon("gear") });
    settings.addEventListener("click", () => feat41.settingsDialog(this));
    right.append(settings);
    this.tabbar.append(logo, this.tabsEl, add, right);
    titlebar.mountControls(this.tabbar);
    // a double click on the empty strip: a new tab — unless the strip is the window caption (then it maximizes)
    this.tabbar.addEventListener("dblclick", (e) => { if (!titlebar.active() && (e.target === this.tabbar || e.target === this.tabsEl)) this.newTab(); });
  }
  renderTabs() {
    this.tabsEl.innerHTML = "";
    this.tabs.forEach((tab, index) => {
      const pane = tab.panes[tab.activePane] || tab.panes[0];
      const el = h("div", { class: "tab" + (index === this.active ? " active" : "") + (tab.pinned ? " pinned" : ""), role: "tab", draggable: "true", tabindex: "0", title: pane.path && !isSpecial(pane.path) ? pane.path : pane.title, "data-tab": index });
      const ico = pane.path === SPECIAL.HOME ? icon("home") : pane.path === SPECIAL.TRASH ? icon("trash") : pane.path === SPECIAL.SEARCH ? icon("search") : `<span class="ic tab-ico">${folderSvg()}</span>`;
      el.innerHTML = ico + `<span class="tab-name">${esc(pane.title)}</span>` + (tab.panes.length > 1 ? `<span class="split-badge">${icon("split", "sm")}</span>` : "");
      const close = h("button", { class: "icon-btn sm tab-close", title: t("tab.close") + " (Ctrl+W)", html: icon("x") });
      close.addEventListener("click", (e) => { e.stopPropagation(); this.closeTab(index); });
      if (!tab.pinned) el.append(close);   // r40-files-max: a pinned tab is a compact icon, closed from its menu
      el.addEventListener("mousedown", (e) => { if (e.button === 1) { e.preventDefault(); this.closeTab(index); } });
      el.addEventListener("click", () => this.switchTab(index));
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); this.tabMenu(index, e.clientX, e.clientY); });
      el.addEventListener("dragstart", (e) => { this.dragTab = index; el.classList.add("dragging"); e.dataTransfer.setData("application/x-memufiles-tab", String(index)); e.dataTransfer.effectAllowed = "move"; });
      el.addEventListener("dragend", () => { el.classList.remove("dragging"); this.dragTab = null; $$(".tab").forEach((x) => x.classList.remove("drop-before", "drop-after", "drop-into")); });
      el.addEventListener("dragover", (e) => {
        const types = e.dataTransfer.types;
        if (types.includes("application/x-memufiles-tab")) {
          e.preventDefault();
          const r = el.getBoundingClientRect();
          const after = e.clientX > r.left + r.width / 2;
          el.classList.toggle("drop-before", !after); el.classList.toggle("drop-after", after);
        } else if (types.includes("application/x-memufiles") || types.includes("Files")) {
          e.preventDefault();
          el.classList.add("drop-into");
          clearTimeout(this.tabHoverTimer);
          this.tabHoverTimer = setTimeout(() => this.switchTab(index), 700);
          const p = this.tabs[index].panes[this.tabs[index].activePane];
          if (p.folder) this.dragOver(e, p.folder, null);
        }
      });
      el.addEventListener("dragleave", () => { el.classList.remove("drop-before", "drop-after", "drop-into"); clearTimeout(this.tabHoverTimer); });
      el.addEventListener("drop", (e) => {
        el.classList.remove("drop-before", "drop-after", "drop-into");
        clearTimeout(this.tabHoverTimer);
        if (e.dataTransfer.types.includes("application/x-memufiles-tab")) {
          e.preventDefault();
          const from = +e.dataTransfer.getData("application/x-memufiles-tab");
          const r = el.getBoundingClientRect();
          let to = index + (e.clientX > r.left + r.width / 2 ? 1 : 0);
          this.moveTab(from, to);
        } else {
          const p = this.tabs[index].panes[this.tabs[index].activePane];
          if (p.folder) this.drop(e, p.folder);
        }
      });
      this.tabsEl.append(el);
    });
    const activeEl = this.tabsEl.children[this.active];   // many tabs in a narrow window: the strip scrolls
    if (activeEl && this.tabsEl.scrollWidth > this.tabsEl.clientWidth) activeEl.scrollIntoView({ block: "nearest", inline: "nearest" });
    const pane = this.pane();
    if (pane) api("window", { cmd: "title", title: `${pane.title} — MemuFiles` }).catch(() => {});
    document.title = pane ? `${pane.title} — MemuFiles` : "MemuFiles";
  }
  addTab(state = {}, activate = true, restoring = false) {
    const panesState = state.panes && state.panes.length ? state.panes : [{ path: state.path || SPECIAL.HOME }];
    const tab = { id: Math.random().toString(36).slice(2), panes: [], activePane: Math.min(state.activePane || 0, panesState.length - 1), pinned: !!state.pinned };
    for (const ps of panesState.slice(0, 2)) tab.panes.push(new Pane(this, ps));
    this.tabs.push(tab);
    if (activate) this.switchTab(this.tabs.length - 1);
    else this.renderTabs();
    return tab;
  }
  newTab(path) { const p = path || feat41.newTabPath(this); this.addTab({ panes: [{ path: p }] }); this.saveSession(); }   // r41-files: «New tab» setting
  /** opts (r41-files): {reveal} — open the PARENT and select the item even when it is a folder (explorer /select,);
   *  {shell} — a folder from the shell's folder handler («Show in folder» in a browser): select the fresh file there. */
  openPathInNewTab(path, known, opts = {}) {
    // a file opens its folder with the file selected; a folder already open in a tab (a restored session, the
    // same folder sent again from Quick Access / the command line) is activated instead of piling up duplicates
    const existing = (folder) => this.tabs.findIndex((tab) => tab.panes.length === 1 && tab.panes[0].mode !== "search" && samePath(tab.panes[0].path, folder));
    const place = (info) => {
      const folder = ((info.exists && !info.is_dir) || (opts.reveal && parentPath(path))) && !opts.browse ? parentPath(path) : path;
      const index = existing(folder);
      if (index >= 0) {
        const pane = this.tabs[index].panes[0];
        if (index !== this.active) {             // switchTab reloads the folder: the selection is applied after that load
          if (folder !== path) pane.pendingSelect = [path];
          else if (opts.shell) pane.shellHint = true;
          this.switchTab(index);
        } else if (folder !== path) { if (!pane.selectAndReveal([path])) pane.navigate(folder, { select: [path], reload: true }); }
        else if (opts.shell) { pane.shellHint = true; pane.navigate(folder, { reload: true }); }
      } else if (folder !== path) {
        const tab = this.addTab({ panes: [{ path: folder }] });
        tab.panes[0].pendingSelect = [path];
      } else {
        const tab = this.addTab({ panes: [{ path }] });
        if (opts.shell) tab.panes[0].shellHint = true;
      }
      this.saveSession();
    };
    // r39-files-perf: the boot payload / the "open" event already carry {exists, is_dir} — the tab opens synchronously
    // (no /api/stat round trip, and no extra «Home» tab created while the answer was on its way)
    if (known) return place(known);
    api("stat", { paths: [path] }).then((res) => place(res.items[path] || {})).catch(() => this.addTab({ panes: [{ path }] }));
  }
  /** r41-files: how a start-up / handed-over path opens — {reveal} for «/select,», {shell} from the folder handler. */
  openOpts(src, path) { return { reveal: (src.reveal || []).includes(path), shell: (src.shell || []).includes(path), browse: (src.browse || []).includes(path) }; }
  switchTab(index) {
    if (index < 0 || index >= this.tabs.length) return;
    const previous = this.tabs[this.active];
    if (previous && previous !== this.tabs[index]) { previous.panes.forEach((p) => p.unmount()); this.prevTab = previous; }
    this.active = index;
    const tab = this.tabs[index];
    this.panesEl.innerHTML = "";
    tab.panes.forEach((pane, i) => {
      pane.mount(this.panesEl);
      pane.headEl.hidden = tab.panes.length < 2;
      pane.el.classList.toggle("active", i === tab.activePane);
      pane.headEl.addEventListener("click", () => this.activatePane(pane));
      this.renderPaneHead(pane);
      if (!pane.loadedOnce) { pane.loadedOnce = true; pane.navigate(pane.path, { fromHistory: true }); }
      else { pane.applyFolderView(); pane.load(true); }
    });
    this.renderTabs();
    this.updateChrome();
    this.saveSession();
    const pane = this.pane();
    if (pane && pane.fv) setTimeout(() => pane.fv.focus({ preventScroll: true }), 0);
  }
  closeTab(index = this.active, force = false) {
    if (this.tabs[index] && this.tabs[index].pinned && !force) { toast(t("tab.pinned_hint")); return; }   // r40-files-max
    if (this.tabs.length === 1) {
      const tab = this.tabs[0];
      this.closed.push(this.serializeTab(tab));
      tab.panes.forEach((p) => p.unmount());
      this.tabs = [];
      this.addTab({ panes: [{ path: SPECIAL.HOME }] });
      return;
    }
    const [tab] = this.tabs.splice(index, 1);
    this.closed.push(this.serializeTab(tab));
    this.closed = this.closed.slice(-20);
    tab.panes.forEach((p) => p.unmount());
    const closingActive = index === this.active;
    if (this.active >= this.tabs.length) this.active = this.tabs.length - 1;
    else if (index < this.active) this.active--;
    if (closingActive) this.active = feat41.tabAfterClose(this, index);   // r41-files: «After closing a tab»
    this.active = Math.max(0, Math.min(this.active, this.tabs.length - 1));
    this.panesEl.innerHTML = "";
    const keep = this.active;
    this.active = -1;
    this.switchTab(keep);
  }
  reopenTab() { const state = this.closed.pop(); if (state) { this.addTab(state); this.saveSession(); } }
  moveTab(from, to) {
    if (from === to || from + 1 === to) return;
    const current = this.tabs[this.active];
    const [tab] = this.tabs.splice(from, 1);
    this.tabs.splice(to > from ? to - 1 : to, 0, tab);
    this.active = this.tabs.indexOf(current);
    this.renderTabs();
    this.saveSession();
  }
  duplicateTab(index) { const state = this.serializeTab(this.tabs[index]); this.addTab(state); this.saveSession(); }
  tabMenu(index, x, y) {
    menu([
      { label: t("tab.new"), icon: "plus", kb: "Ctrl+T", run: () => this.newTab() },
      { label: t("tab.duplicate"), icon: "copy", run: () => this.duplicateTab(index) },
      { label: t("tab.reopen"), icon: "restore", kb: "Ctrl+Shift+T", disabled: !this.closed.length, run: () => this.reopenTab() },
      { label: t(this.tabs[index].pinned ? "tab.unpin" : "tab.pin"), icon: "pin", run: () => feat40.pinTab(this, index, !this.tabs[index].pinned) },   // r40-files-max
      { sep: true },
      { label: t("tab.close"), icon: "x", kb: "Ctrl+W", run: () => this.closeTab(index, true) },
      // r40-files-max: «close others / to the right» keep pinned tabs (Chrome, Arc, Files)
      { label: t("tab.close_others"), disabled: this.tabs.length < 2, run: () => { const keep = this.tabs[index]; const stay = (tb) => tb === keep || tb.pinned; this.tabs.filter((tb) => !stay(tb)).forEach((tb) => { this.closed.push(this.serializeTab(tb)); tb.panes.forEach((p) => p.unmount()); }); const current = this.tabs[this.active]; this.tabs = this.tabs.filter(stay); this.active = -1; this.switchTab(Math.max(0, this.tabs.indexOf(stay(current) ? current : keep))); } },
      { label: t("tab.close_right"), disabled: index >= this.tabs.length - 1, run: () => { const removed = this.tabs.slice(index + 1).filter((tb) => !tb.pinned); removed.forEach((tb) => { this.closed.push(this.serializeTab(tb)); tb.panes.forEach((p) => p.unmount()); }); const current = this.tabs[this.active]; this.tabs = this.tabs.filter((tb) => !removed.includes(tb)); const keep = this.tabs.includes(current) ? this.tabs.indexOf(current) : Math.min(index, this.tabs.length - 1); this.active = -1; this.switchTab(keep); } },
    ], x, y);
  }
  serializeTab(tab) { return { panes: tab.panes.map((p) => p.serialize()), activePane: tab.activePane, ...(tab.pinned ? { pinned: true } : {}) }; }
  saveSession(now) {
    clearTimeout(this.sessionTimer);
    const run = () => {
      const body = { tabs: this.tabs.map((tab) => this.serializeTab(tab)), active: this.active, closed: this.closed.slice(-10),
        layout: { right: this.right } };
      if (now && navigator.sendBeacon) { /* the session is saved on every change anyway */ }
      api("session", body).catch(() => {});
    };
    if (now) run(); else this.sessionTimer = setTimeout(run, 600);
  }

  tab() { return this.tabs[this.active]; }
  pane() { const tab = this.tab(); return tab ? tab.panes[tab.activePane] || tab.panes[0] : null; }
  otherPane() { const tab = this.tab(); return tab && tab.panes.length > 1 ? tab.panes[1 - tab.activePane] : null; }
  activatePane(pane) {
    const tab = this.tab();
    if (!tab) return;
    const index = tab.panes.indexOf(pane);
    if (index < 0 || index === tab.activePane) return;
    tab.activePane = index;
    tab.panes.forEach((p, i) => p.el && p.el.classList.toggle("active", i === index));
    this.renderTabs();
    this.updateChrome();
    this.selectionChanged(pane);
  }
  toggleSplit() {
    const tab = this.tab();
    if (tab.panes.length > 1) {
      const removed = tab.panes.splice(1 - tab.activePane, 1)[0];
      removed.unmount();
      tab.activePane = 0;
    } else {
      const current = this.pane();
      const pane = new Pane(this, { path: current.folder || SPECIAL.HOME, view: current.view, sort: current.sort });
      tab.panes.push(pane);
      tab.activePane = 1;
    }
    const keep = this.active;
    this.panesEl.innerHTML = "";
    tab.panes.forEach((p) => p.unmount());
    this.active = -1;
    this.switchTab(keep);
  }

  // called by panes
  paneChanged(pane) {
    if (pane === this.pane()) this.updateChrome();
    this.renderTabs();
    this.renderPaneHead(pane);
    this.highlightSidebar();
    this.saveSession();
  }
  renderPaneHead(pane) {
    if (pane.headEl) pane.headEl.innerHTML = `${icon(pane.path === SPECIAL.HOME ? "home" : pane.path === SPECIAL.TRASH ? "trash" : pane.path === SPECIAL.SEARCH ? "search" : "folder", "sm")}<span class="ph-path">${esc(isSpecial(pane.path) ? pane.title : pane.path)}</span>`;
  }
  paneUpdated(pane) {
    if (pane === this.pane()) { this.updateChrome(); this.updateStatus(); feat41.reportState(this); }
    if (pane.mode === "search") this.renderTabs();
  }
  selectionChanged(pane) {
    if (pane !== this.pane()) return;
    feat41.reportState(this);       // r41-files: Quick Access over this window knows the folder and the selection
    if (feat41.inArchive(pane)) this.renderBanner(pane);
    this.updateCommands();
    this.updateStatus();
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => { if (this.right === "preview") preview.show(this, this.previewTarget()); if (this.right === "ai") ai.selectionChanged(this); }, 90);
  }
  previewTarget() {
    const pane = this.pane();
    if (!pane) return null;
    const selected = pane.selected();
    if (selected.length === 1) return { item: selected[0], pane };
    if (selected.length > 1) return { multi: selected, pane };
    return { folder: pane.folder, pane };
  }
  saveFolderView(path, value) {
    const map = Object.assign({}, this.settings.view_by_folder || {});
    if (folderKey(path) !== path) delete map[path];      // r39-files-perf: one normalised key per folder
    map[folderKey(path)] = value;
    this.settings.view_by_folder = map;
    clearTimeout(this.viewSaveTimer);
    this.viewSaveTimer = setTimeout(() => api("settings", { view_by_folder: map }).catch(() => {}), 800);
  }
  cutSet() { return this.clipboard && this.clipboard.cut ? this.clipboard.set : null; }

  // ---------------------------------------------------------------- navbar (back/forward/up, address, search)
  buildNavbar() {
    const nav = h("div", { class: "nav-group" });
    this.btnBack = h("button", { class: "icon-btn", title: t("nav.back") + " (Alt+←)", html: icon("back") });
    this.btnFwd = h("button", { class: "icon-btn", title: t("nav.forward") + " (Alt+→)", html: icon("fwd") });
    this.btnUp = h("button", { class: "icon-btn", title: t("nav.up") + " (Alt+↑)", html: icon("up") });
    this.btnRefresh = h("button", { class: "icon-btn", title: t("nav.refresh") + " (F5)", html: icon("refresh") });
    this.btnBack.addEventListener("click", () => this.pane().back());
    this.btnFwd.addEventListener("click", () => this.pane().forward());
    this.btnUp.addEventListener("click", () => this.pane().up());
    this.btnRefresh.addEventListener("click", () => { this.pane().reload(true); this.loadSidebar(); });
    for (const [btn, dir] of [[this.btnBack, -1], [this.btnFwd, 1]]) {
      btn.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const pane = this.pane();
        const items = [];
        const range = dir < 0 ? pane.history.slice(0, pane.hidx).reverse() : pane.history.slice(pane.hidx + 1);
        range.slice(0, 15).forEach((path, k) => items.push({ label: isSpecial(path) ? t(path === SPECIAL.HOME ? "side.home" : path === SPECIAL.TRASH ? "side.trash" : "search.results") : path, icon: "folder", run: () => { pane.hidx += dir * (k + 1); pane.navigate(pane.history[pane.hidx], { fromHistory: true }); } }));
        if (items.length) menu(items, e.clientX, e.clientY);
      });
    }
    nav.append(this.btnBack, this.btnFwd, this.btnUp, this.btnRefresh);
    this.addressEl = h("div", { class: "address" });
    this.crumbsEl = h("div", { class: "crumbs" });
    this.addrInput = h("input", { spellcheck: "false", hidden: true, "aria-label": t("nav.address") });
    this.addressEl.append(this.crumbsEl, this.addrInput);
    this.crumbsEl.addEventListener("click", (e) => { if (e.target === this.crumbsEl) this.editAddress(); });
    this.addrInput.addEventListener("keydown", (e) => this.addressKey(e));
    this.addrInput.addEventListener("input", debounce(() => this.suggest(), 90));
    this.addrInput.addEventListener("blur", () => setTimeout(() => this.endAddressEdit(), 150));
    this.searchEl = h("div", { class: "searchbox" });
    this.searchInput = h("input", { placeholder: t("search.placeholder"), spellcheck: "false", "aria-label": t("search.placeholder") });
    this.searchScope = h("button", { class: "search-scope", title: t("search.scope_hint") });
    this.searchScopeValue = "folder";
    this.searchScope.addEventListener("click", () => { this.searchScopeValue = this.searchScopeValue === "folder" ? "everywhere" : "folder"; this.updateSearchScope(); if (this.searchInput.value.trim()) this.doSearch(); });
    this.searchEl.innerHTML = icon("search");
    this.searchEl.append(this.searchInput, this.searchScope);
    this.searchInput.addEventListener("input", debounce(() => this.doSearch(), 220));
    this.searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.doSearch(); }
      else if (e.key === "Escape") { e.preventDefault(); this.clearSearch(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); this.pane().fv.focus(); }
    });
    this.updateSearchScope();
    this.navbar.append(nav, this.addressEl, this.searchEl);
  }
  updateSearchScope() {
    this.searchScope.textContent = this.searchScopeValue === "folder" ? t("search.here") : t("search.everywhere");
    this.searchScope.classList.toggle("on", this.searchScopeValue === "everywhere");
  }
  renderCrumbs() {
    const pane = this.pane();
    this.crumbsEl.innerHTML = "";
    if (!pane) return;
    if (isSpecial(pane.path)) {
      const label = pane.path === SPECIAL.SEARCH && pane.search ? (pane.search.scope === "everywhere" ? t("search.in_everywhere", { q: pane.search.query }) : t("search.in", { q: pane.search.query, where: baseName(pane.search.root) })) : pane.title;
      const b = h("button", { class: "crumb wide", title: label, html: icon(pane.path === SPECIAL.HOME ? "home" : pane.path === SPECIAL.TRASH ? "trash" : "search", "sm") + `<span class="cr-text">${esc(label)}</span>` });
      this.crumbsEl.append(b);
      return;
    }
    const home = h("button", { class: "crumb", title: t("side.home"), html: icon("home", "sm") });
    home.addEventListener("click", () => pane.navigate(SPECIAL.HOME));
    this.crumbsEl.append(home);
    const parts = splitPath(pane.path);
    // Linux/macOS: «⌂ › / › tmp» showed two roots — the first separator already lists "/", so the "/" crumb is
    // shown only for the root folder itself
    if (!isWin() && parts.length > 1 && parts[0].path === "/") parts.shift();
    parts.forEach((part, i) => {
      const sep = h("button", { class: "crumb-sep", html: icon("right", "sm"), title: t("nav.subfolders") });
      sep.addEventListener("click", (e) => { e.stopPropagation(); this.crumbMenu(i === 0 ? null : parts[i - 1].path, part.path, sep); });
      const crumb = h("button", { class: "crumb", text: part.name });
      crumb.addEventListener("click", (e) => { e.stopPropagation(); pane.navigate(part.path); });
      crumb.addEventListener("dragover", (e) => this.dragOver(e, part.path, crumb));
      crumb.addEventListener("dragleave", () => crumb.classList.remove("drop"));
      crumb.addEventListener("drop", (e) => { crumb.classList.remove("drop"); this.drop(e, part.path); });
      crumb.addEventListener("contextmenu", (e) => { e.preventDefault(); this.folderMenu(part.path, e.clientX, e.clientY); });
      this.crumbsEl.append(sep, crumb);
    });
    if (parts.length) {      // like Explorer: the arrow after the last folder lists its subfolders
      const last = parts[parts.length - 1].path;
      const tail = h("button", { class: "crumb-sep", html: icon("right", "sm"), title: t("nav.subfolders") });
      tail.addEventListener("click", (e) => { e.stopPropagation(); this.crumbMenu(last, null, tail); });
      this.crumbsEl.append(tail);
    }
    // keep the end of a long path visible — r40-files-max: leading folders fold into «…» instead of being cut
    requestAnimationFrame(() => feat40.fitCrumbs(this));
    if (!this.crumbFit) { this.crumbFit = true; let raf = 0; addEventListener("resize", () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; feat40.fitCrumbs(this); }); }, { passive: true }); }
  }
  /** The arrow before a folder lists its parent's subfolders (siblings, the current one marked); before the first
   *  folder — the drives (Windows) or "/"; ``current === null`` — the subfolders of ``parent`` itself. */
  async crumbMenu(parent, current, anchor) {
    const target = parent === null ? (isWin() ? null : "/") : parent;
    let items = [];
    if (target === null) items = this.sidebar.drives.map((d) => ({ label: d.label ? `${d.label} (${d.path.replace(/\\$/, "")})` : d.path, icon: "drive", run: () => this.pane().navigate(d.path) }));
    else {
      try {
        const res = await api("complete?q=" + encodeURIComponent(target.endsWith(S.sep) ? target : target + S.sep) + (this.settings.show_hidden ? "&hidden=1" : ""));
        items = res.items.slice().sort((x, y) => baseName(x).localeCompare(baseName(y), undefined, { numeric: true, sensitivity: "base" })).slice(0, 40).map((p) => ({ label: baseName(p), icon: "folder", checked: current ? samePath(p.replace(/[\\/]+$/, ""), current) : undefined, run: () => this.pane().navigate(p) }));
      } catch (e) { items = []; }
    }
    if (!items.length) items = [{ label: t("nav.no_subfolders"), disabled: true }];
    const r = anchor.getBoundingClientRect();
    menu(items, r.left, r.bottom + 4);
  }
  editAddress() {
    const pane = this.pane();
    this.crumbsEl.hidden = true;
    this.addrInput.hidden = false;
    this.addrInput.value = pane.folder || (pane.search ? pane.search.root : "");
    this.addrInput.focus();
    this.addrInput.select();
  }
  endAddressEdit() {
    if (this.addrInput.hidden) return;
    this.addrInput.hidden = true;
    this.crumbsEl.hidden = false;
    const sg = $(".suggest", this.addressEl);
    if (sg) sg.remove();
  }
  async addressKey(e) {
    const sg = $(".suggest", this.addressEl);
    if (e.key === "Escape") { e.preventDefault(); this.endAddressEdit(); this.pane().fv.focus(); return; }
    if (sg && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const items = $$(".sg", sg);
      let index = items.findIndex((x) => x.classList.contains("on"));
      index = (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((x, i) => x.classList.toggle("on", i === index));
      this.addrInput.value = items[index].dataset.path;
      return;
    }
    if (e.key === "Tab" && sg) {
      e.preventDefault();
      const first = $(".sg.on", sg) || $(".sg", sg);
      if (first) { this.addrInput.value = first.dataset.path + S.sep; this.suggest(); }
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const value = this.addrInput.value.trim();
      if (!value) return;
      this.endAddressEdit();
      await this.goToTyped(value);
    }
  }
  async goToTyped(value) {
    const pane = this.pane();
    const aliases = { "shell:recyclebinfolder": SPECIAL.TRASH, "trash": SPECIAL.TRASH, "корзина": SPECIAL.TRASH, "home": SPECIAL.HOME, "этот компьютер": SPECIAL.HOME, "this pc": SPECIAL.HOME };
    if (aliases[value.toLowerCase()]) { pane.navigate(aliases[value.toLowerCase()]); return; }
    if (/^https?:\/\//i.test(value)) { toast(t("err.url"), "err"); return; }
    let path = value.replace(/^"|"$/g, "");
    if (path === "~" || path.startsWith("~/") || path.startsWith("~\\")) path = S.home + path.slice(1);
    if (isWin() && /^[A-Za-z]:$/.test(path)) path += "\\";
    if (!isWin() && !path.startsWith("/")) path = joinPath(pane.folder || S.home, path);
    try {
      const res = await api("stat", { paths: [path] });
      const info = res.items[path];
      if (!info || !info.exists) { toast(t("err.not_found_path", { path }), "err"); return; }
      if (info.is_dir) pane.navigate(path);
      else { await pane.navigate(parentPath(path), { select: [path] }); }
    } catch (err) { showError(err); }
  }
  async suggest() {
    const value = this.addrInput.value;
    let sg = $(".suggest", this.addressEl);
    if (!value || value.length < 1) { if (sg) sg.remove(); return; }
    let items = [];
    try { items = (await api("complete?q=" + encodeURIComponent(value) + (this.settings.show_hidden ? "&hidden=1" : ""))).items; } catch (e) { items = []; }
    if (this.addrInput.hidden) return;
    sg = $(".suggest", this.addressEl);
    if (!items.length) { if (sg) sg.remove(); return; }
    if (!sg) { sg = h("div", { class: "suggest" }); this.addressEl.append(sg); }
    sg.innerHTML = items.map((p) => `<div class="sg" data-path="${esc(p)}">${icon("folder")}${esc(p)}</div>`).join("");
    $$(".sg", sg).forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); this.endAddressEdit(); this.pane().navigate(el.dataset.path); }));
  }
  doSearch() {
    const query = this.searchInput.value.trim();
    const pane = this.pane();
    if (!query) { this.clearSearch(true); return; }
    const root = pane.mode === "search" && pane.search ? pane.search.root : (pane.folder || S.home);
    pane.runSearch(query, this.searchScopeValue, root);
  }
  clearSearch(keepFocus) {
    const pane = this.pane();
    this.searchInput.value = "";
    if (pane.mode === "search") { pane.stopSearch(); pane.navigate((pane.search && pane.search.root) || S.home); }
    if (!keepFocus) pane.fv.focus();
  }

  // ---------------------------------------------------------------- command bar
  buildCmdbar() {
    const c = (id, ico, label, run, opts = {}) => {
      const b = h("button", { class: "cmd" + (opts.primary ? " primary" : ""), "data-cmd": id, title: label + (opts.kb ? ` (${opts.kb})` : "") });
      b.innerHTML = icon(ico) + (opts.iconOnly ? "" : `<span class="lbl">${esc(label)}</span>`) + (opts.caret ? icon("down", "caret") : "");
      b.addEventListener("click", (e) => run(e, b));
      return b;
    };
    const bar = this.cmdbar;
    bar.append(
      c("new", "plus", t("cmd.new"), (e, b) => this.newMenu(b), { caret: true, primary: true }),
      h("span", { class: "sep" }),
      c("cut", "cut", t("cmd.cut"), () => this.cmdCut(), { iconOnly: true, kb: "Ctrl+X" }),
      c("copy", "copy", t("cmd.copy"), () => this.cmdCopy(), { iconOnly: true, kb: "Ctrl+C" }),
      c("paste", "paste", t("cmd.paste"), () => this.cmdPaste(), { iconOnly: true, kb: "Ctrl+V" }),
      c("rename", "rename", t("cmd.rename"), () => this.cmdRename(), { iconOnly: true, kb: "F2" }),
      c("delete", "trash", t("cmd.delete"), (e) => this.cmdDelete(e.shiftKey), { iconOnly: true, kb: "Del" }),
      h("span", { class: "sep" }),
      c("sort", "sort", t("cmd.sort"), (e, b) => this.sortMenu(b), { caret: true }),
      c("view", "view", t("cmd.view"), (e, b) => this.viewMenu(b), { caret: true }),
      c("select", "checkAll", t("cmd.select"), (e, b) => this.selectMenu(b), { caret: true }),
      c("kinds", "filter", t("kind.bar"), () => feat40.toggleKindBar(this), { iconOnly: true, kb: "Ctrl+Shift+F" }),   // r40-files-max
      h("span", { class: "spacer" }),
      c("split", "split", t("cmd.split"), () => this.toggleSplit(), { kb: "Ctrl+\\" }),
      c("preview", "preview", t("cmd.preview"), () => this.togglePreview(), { kb: "Alt+P" }),
      c("ai", "sparkle", t("cmd.ai"), () => this.toggleAI(), { kb: "Ctrl+Shift+A" }),
      c("more", "more", t("cmd.more"), (e, b) => this.moreMenu(b), { iconOnly: true }),
    );
  }
  updateCommands() {
    const pane = this.pane();
    if (!pane) return;
    const sel = pane.selected();
    const writable = pane.mode === "dir";
    const set = (id, disabled, on) => { const b = $(`[data-cmd="${id}"]`, this.cmdbar); if (b) { b.disabled = !!disabled; b.classList.toggle("on", !!on); } };
    set("new", !writable);
    set("cut", !sel.length || pane.mode === "trash" || pane.mode === "home");
    set("copy", !sel.length || pane.mode === "home");
    set("paste", !writable || !this.clipboard);
    set("rename", !sel.length || pane.mode === "trash" || pane.mode === "home");
    set("delete", !sel.length || pane.mode === "home");
    set("sort", pane.mode === "home");
    set("view", pane.mode === "home");
    set("select", pane.mode === "home");
    set("kinds", pane.mode === "home" || pane.mode === "trash", feat40.kindBarOn());   // r40-files-max
    set("split", false, this.tab().panes.length > 1);
    set("preview", false, this.right === "preview");
    set("ai", false, this.right === "ai");
  }
  updateChrome() {
    const pane = this.pane();
    if (!pane) return;
    this.btnBack.disabled = !pane.canBack();
    this.btnFwd.disabled = !pane.canForward();
    this.btnUp.disabled = pane.path === SPECIAL.HOME;
    this.renderCrumbs();
    if (pane.mode === "search" && pane.search) {
      if (document.activeElement !== this.searchInput) this.searchInput.value = pane.search.query;
      this.searchScopeValue = pane.search.scope; this.updateSearchScope();
    } else if (document.activeElement !== this.searchInput) this.searchInput.value = "";
    this.searchInput.placeholder = pane.folder ? t("search.placeholder_in", { name: baseName(pane.folder) || pane.folder }) : t("search.placeholder");
    this.updateCommands();
    this.updateStatus();
    this.renderBanner(pane);
  }
  renderBanner(pane) {   // r40-files-max: text on one line (ellipsis), labels hide in a narrow pane
    const b = pane.bannerEl;
    if (!b) return;
    if (pane.mode === "trash") {
      b.hidden = false; b.className = "banner";
      b.innerHTML = icon("trash") + `<span class="bn-text">${esc(t("trash.banner"))}</span><span class="spacer"></span>`;
      const restore = h("button", { class: "btn sm", html: icon("restore") + `<span class="lbl">${esc(t("trash.restore_sel"))}</span>`, title: t("trash.restore_sel") });
      restore.addEventListener("click", () => this.trashRestore());
      const empty = h("button", { class: "btn sm danger", html: icon("trash") + `<span class="lbl">${esc(t("trash.empty_btn"))}</span>`, title: t("trash.empty_btn") });
      empty.addEventListener("click", () => this.trashEmpty());
      b.append(restore, empty);
    } else if (pane.mode === "search" && pane.search) {
      b.hidden = false; b.className = "banner";
      const s = pane.search;
      const src = s.source === "index" ? t("search.src_index") : t("search.src_walk");
      b.innerHTML = icon("search") + `<span class="bn-text">${esc(s.running ? t("search.running", { n: fmtNum(s.count) }) : t("search.done", { n: fmtNum(s.count), ms: s.done && s.done.ms !== undefined ? s.done.ms : "?" }))} · ${esc(src)}</span><span class="spacer"></span>`;
      if (s.running) { const stop = h("button", { class: "btn sm", html: icon("stop") + `<span class="lbl">${esc(t("search.stop"))}</span>`, title: t("search.stop") }); stop.addEventListener("click", () => pane.stopSearch()); b.append(stop); }
      const save = h("button", { class: "btn sm ghost", html: icon("star") + `<span class="lbl">${esc(t("x.save_search"))}</span>`, title: t("x.save_search") });   // r39
      save.addEventListener("click", () => extras.saveSearch(this, pane));
      b.append(save);
      const close = h("button", { class: "btn sm ghost", html: icon("x") + `<span class="lbl">${esc(t("search.close"))}</span>`, title: t("search.close") });
      close.addEventListener("click", () => this.clearSearch());
      b.append(close);
    } else if (feat41.inArchive(pane)) {
      feat41.archiveBanner(this, pane, b);                  // r41-files
    } else if (pane.head && pane.head.protected && pane.mode === "dir") {
      b.hidden = false; b.className = "banner warn";
      b.innerHTML = icon("lock") + `<span class="bn-text">${esc(t("banner.protected"))}</span>`;
    } else b.hidden = true;
  }

  // ---------------------------------------------------------------- status bar
  buildStatus() {
    this.stCount = h("span", {});
    this.stSel = h("span", { class: "st-sel" });
    this.stFree = h("span", { class: "st-free" });
    this.opsBtn = h("button", { class: "ops-ind idle", title: t("ops.title") });
    this.opsBtn.addEventListener("click", () => this.opsUi.toggle());     // r40-files-core
    const views = h("div", { class: "views" });
    for (const [v, ico, kb] of [["details", "details", "Ctrl+Shift+6"], ["list", "list", "Ctrl+Shift+5"], ["tiles", "tiles", "Ctrl+Shift+7"], ["grid", "grid", "Ctrl+Shift+2"], ["gallery", "gallery", "Ctrl+Shift+1"]]) {
      const b = h("button", { class: "icon-btn", "data-view": v, title: t("view." + v) + ` (${kb})`, html: icon(ico) });
      b.addEventListener("click", () => this.pane().setView(v));
      views.append(b);
    }
    this.zoom = h("input", { type: "range", class: "zoom", min: "32", max: "256", step: "8", title: t("view.size") });
    this.zoom.addEventListener("input", () => { const p = this.pane(); p.iconSize = +this.zoom.value; if (p.view === "details" || p.view === "list") p.view = "grid"; p.layout(); this.updateStatus(); });
    this.zoom.addEventListener("change", () => { api("settings", { icon_size: +this.zoom.value }).catch(() => {}); this.saveSession(); });
    this.statusEl.append(this.stCount, this.stSel, this.stFree, this.opsBtn, views, this.zoom);
  }
  updateStatus() {
    const pane = this.pane();
    if (!pane || !this.stCount) return;
    if (pane.mode === "home") { this.stCount.textContent = t("side.home"); this.stSel.textContent = ""; }
    else {
      const total = pane.shown.length;
      this.stCount.textContent = pane.loading && pane.mode === "dir" ? t("status.loading", { n: fmtNum(pane.items.length) }) : itemsWord(total);
      const sel = pane.selected();
      if (sel.length) {
        const size = sel.reduce((a, r) => a + (r.d ? 0 : r.s || 0), 0);
        this.stSel.textContent = t("status.selected", { n: itemsWord(sel.length) }) + (size ? " · " + fmtSize(size) : "");
      } else this.stSel.textContent = "";
    }
    const drive = pane.folder ? this.sidebar.drives.filter((d) => isInside(pane.folder, d.path)).sort((a, b) => b.path.length - a.path.length)[0] : null;
    this.stFree.textContent = drive ? t("status.free", { free: fmtSize(drive.free), total: fmtSize(drive.total) }) : "";
    $$(".views .icon-btn", this.statusEl).forEach((b) => b.classList.toggle("on", b.dataset.view === pane.view));
    this.zoom.value = pane.iconSize;
    this.zoom.hidden = pane.view === "details" || pane.view === "list";
  }

  // ---------------------------------------------------------------- sidebar
  async loadSidebar() {
    try {
      const data = await api("sidebar");
      this.sidebar = data;
      this.setTags(data.tags);
      this.renderSidebar();
      this.updateStatus();
      const pane = this.pane();
      if (pane && pane.mode === "home") this.renderHome(pane, pane.homeEl);
    } catch (err) { /* offline backend: keep the last state */ }
  }
  renderSidebar() {
    const el = this.sidebarEl;
    el.innerHTML = "";
    const sec = (title, extra) => { const s = h("div", { class: "side-sec" }); const head = h("div", { class: "side-head", text: title }); if (extra) head.append(extra); s.append(head); el.append(s); return s; };
    const item = (parent, { ico, folderIcon, label, path, meta, special, drop = true, onclick, menuFn, extra }) => {
      const b = h("button", { class: "side-item", "data-path": path || special || "", title: path || label });
      b.innerHTML = (folderIcon ? `<span class="ic folder">${folderSvg()}</span>` : icon(ico)) + `<span class="nm">${esc(label)}</span>` + (meta ? `<span class="meta">${esc(meta)}</span>` : "");
      if (extra) b.append(extra);
      b.addEventListener("click", onclick || (() => this.pane().navigate(path || special)));
      b.addEventListener("mousedown", (e) => { if (e.button === 1 && (path || special)) { e.preventDefault(); this.newTab(path || special); } });
      if (menuFn) b.addEventListener("contextmenu", (e) => { e.preventDefault(); menuFn(e); });
      if (drop && path) {
        b.addEventListener("dragover", (e) => this.dragOver(e, path, b));
        b.addEventListener("dragleave", () => b.classList.remove("drop"));
        b.addEventListener("drop", (e) => { b.classList.remove("drop"); this.drop(e, path); });
      }
      parent.append(b);
      return b;
    };
    const quick = sec(t("side.quick"));
    item(quick, { ico: "home", label: t("side.home"), special: SPECIAL.HOME, drop: false });
    const placeIcons = { home: "home", desktop: "desktop", documents: "documents", downloads: "downloads", pictures: "pictures", music: "music", videos: "videos" };
    for (const place of this.sidebar.places || []) {
      if (place.id === "onedrive" || (place.exists === false)) continue;
      item(quick, { ico: placeIcons[place.id] || "folder", label: t("place." + place.id), path: place.path, menuFn: (e) => this.folderMenu(place.path, e.clientX, e.clientY) });
    }
    for (const pin of this.sidebar.pinned || []) {
      const x = h("button", { class: "icon-btn sm pin-x", title: t("side.unpin"), html: icon("x") });
      x.addEventListener("click", (e) => { e.stopPropagation(); this.pin(pin.path, false); });
      item(quick, { folderIcon: true, label: baseName(pin.path) || pin.path, path: pin.path, extra: x, menuFn: (e) => this.folderMenu(pin.path, e.clientX, e.clientY) });
    }
    const favs = [...this.tags.values()].filter((r) => r.favorite);
    if (favs.length) {
      const fs = sec(t("side.favorites"));
      for (const f of favs.slice(0, 30)) item(fs, { ico: "star", label: baseName(f.path), path: f.path, drop: false, onclick: () => this.revealPath(f.path) });
    }
    // r40-files-2: saved searches — «smart folders» one click away (right click: run in a new tab / remove)
    const saved = this.sidebar.saved || [];
    if (saved.length) {
      const ss = sec(t("x.saved"));
      for (const s of saved) item(ss, { ico: "search", label: s.name || s.query, drop: false, onclick: () => extras.runSaved(this, s),
        menuFn: (e) => menu([
          { label: t("side.saved_run"), icon: "search", run: () => extras.runSaved(this, s) },
          { label: t("side.saved_tab"), icon: "plus", run: () => { this.newTab(); extras.runSaved(this, s); } },
          { sep: true },
          { label: t("x.saved_remove"), icon: "trash", danger: true, run: () => extras.removeSaved(this, s) },
        ], e.clientX, e.clientY) });
    }
    const pc = sec(t("side.pc"));
    for (const d of this.sidebar.drives || []) {
      const used = d.total ? (d.total - d.free) / d.total : 0;
      const b = item(pc, { ico: d.kind === "removable" ? "usb" : d.kind === "network" ? "network" : d.kind === "cdrom" ? "drive" : "drive", label: driveLabel(d), path: d.path, menuFn: (e) => this.folderMenu(d.path, e.clientX, e.clientY) });
      b.classList.add("drive");
      b.innerHTML = `<span class="drive-row">${icon(d.kind === "removable" ? "usb" : d.kind === "network" ? "network" : "drive")}<span class="nm">${esc(driveLabel(d))}</span></span>` +
        (d.total ? `<span class="bar"><i class="${used > 0.9 ? "full" : ""}" style="width:${(used * 100).toFixed(1)}%"></i></span><span class="free">${esc(t("drive.free", { free: fmtSize(d.free), total: fmtSize(d.total) }))}</span>` : "");
    }
    const other = sec(t("side.other"));
    item(other, { ico: "trash", label: t("side.trash"), special: SPECIAL.TRASH, drop: false });
    const trashBtn = $(`[data-path="${SPECIAL.TRASH}"]`, other);
    trashBtn.addEventListener("dragover", (e) => { if (this.dragPaths || e.dataTransfer.types.includes("application/x-memufiles")) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; trashBtn.classList.add("drop"); } });
    trashBtn.addEventListener("dragleave", () => trashBtn.classList.remove("drop"));
    trashBtn.addEventListener("drop", (e) => { e.preventDefault(); trashBtn.classList.remove("drop"); const paths = this.readDragPaths(e); if (paths.length) this.deletePaths(paths, false); });
    const od = (this.sidebar.places || []).find((p) => p.id === "onedrive");
    if (od && od.exists) item(other, { ico: "cloud", label: "OneDrive", path: od.path });
    else item(other, { ico: "cloud", label: "OneDrive", drop: false, meta: t("side.not_found"), onclick: () => toast(t("side.onedrive_missing"), "info") });
    item(other, { ico: "network", label: t("side.network"), drop: false, onclick: () => this.networkPrompt() });
    if (this.tags.size) {
      const colors = new Set();
      for (const r of this.tags.values()) (r.colors || []).forEach((c) => colors.add(c));
      if (colors.size) {
        const ts = sec(t("side.tags"));
        for (const c of S.boot.tag_colors.filter((x) => colors.has(x))) {
          const b = item(ts, { ico: "tag", label: t("tag." + c), drop: false, onclick: () => this.showTag(c) });
          b.querySelector(".ic").outerHTML = `<span class="ic"><i class="dot ${c}"></i></span>`;
        }
      }
    }
    this.highlightSidebar();
  }
  highlightSidebar() {
    const pane = this.pane();
    $$(".side-item", this.sidebarEl).forEach((b) => b.classList.toggle("active", !!pane && !!b.dataset.path && samePath(b.dataset.path, pane.path)));
  }
  async networkPrompt() {
    const value = await promptDialog({ title: t("side.network"), label: t("net.prompt"), value: isWin() ? "\\\\" : "/mnt/", icon: "network" });
    if (value) this.goToTyped(value);
  }
  showTag(color) {
    const rows = [...this.tags.values()].filter((r) => (r.colors || []).includes(color));
    const pane = this.pane();
    pane.navigate(SPECIAL.SEARCH).then(async () => {
      pane.search = { query: t("tag." + color), scope: "tag", root: pane.history[pane.hidx - 1] || S.home, running: false, count: rows.length, source: "tags" };
      const res = await api("stat", { paths: rows.map((r) => r.path) });
      pane.items = rows.filter((r) => res.items[r.path] && res.items[r.path].exists).map((r) => {
        const info = res.items[r.path];
        return { n: baseName(r.path), d: info.is_dir ? 1 : 0, s: info.size, m: info.mtime, path: r.path };
      });
      pane.loading = false;
      pane.refresh(true);
      this.paneChanged(pane);
      this.paneUpdated(pane);
    });
  }
  revealPath(path) { const pane = this.pane(); pane.navigate(parentPath(path), { select: [path] }); }
  async pin(path, on = true) {
    const pinned = (this.settings.pinned || []).filter((p) => !samePath(p, path));
    if (on) pinned.push(path);
    const res = await api("settings", { pinned });
    this.settings = res.settings;
    this.loadSidebar();
  }

  // home page ("This PC")
  renderHome(pane, el) { tools.renderHome(this, pane, el); }

  // ---------------------------------------------------------------- right panel
  setRight(kind, save = true) {
    this.right = kind;
    this.rightEl.hidden = !kind;
    this.rightResizer.hidden = !kind;
    this.rightEl.innerHTML = "";
    if (kind) {
      const tabs = h("div", { class: "rp-tabs" });
      const tb = (id, ico, label) => { const b = h("button", { class: "rp-tab" + (kind === id ? " on" : ""), html: icon(ico) + esc(label) }); b.addEventListener("click", () => this.setRight(id)); return b; };
      const close = h("button", { class: "icon-btn sm", style: { marginLeft: "auto" }, title: t("common.close"), html: icon("x") });
      close.addEventListener("click", () => this.setRight(null));
      tabs.append(tb("preview", "preview", t("cmd.preview")), tb("ai", "sparkle", t("cmd.ai")), close);
      const body = h("div", { class: "rp-body" });
      this.rightEl.append(tabs, body);
      if (kind === "preview") preview.show(this, this.previewTarget(), body);
      else ai.mount(this, body);
    }
    this.updateCommands();
    const pane = this.pane();
    if (pane) setTimeout(() => pane.layout(), 0);
    if (save) { this.saveSession(); api("settings", { preview_open: kind === "preview" }).catch(() => {}); }
  }
  togglePreview(force) { this.setRight(force === true ? "preview" : this.right === "preview" ? null : "preview"); }
  toggleAI() { this.setRight(this.right === "ai" ? null : "ai"); }

  bindResizers() {
    const drag = (resizer, target, key, sign) => {
      resizer.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const startX = e.clientX, startW = target.getBoundingClientRect().width;
        resizer.classList.add("on");
        document.body.classList.add("resizing");
        const move = (ev) => { target.style.width = Math.max(160, Math.min(760, startW + sign * (ev.clientX - startX))) + "px"; const p = this.pane(); if (p) p.layout(); };
        const up = () => {
          document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
          resizer.classList.remove("on"); document.body.classList.remove("resizing");
          api("settings", { [key]: Math.round(target.getBoundingClientRect().width) }).catch(() => {});
        };
        document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
      });
    };
    drag(this.sideResizer, this.sidebarEl, "sidebar_width", 1);
    drag(this.rightResizer, this.rightEl, "preview_width", -1);
  }

  // ================================================================ commands
  async openItem(pane, item) {
    if (!item) return;
    if (pane.mode === "trash") { this.trashRestore([item]); return; }
    if (await feat41.openItem(this, pane, item)) return;   // r41-files: an archive opens like a folder; a file inside one opens
    if (item.d) { pane.navigate(item.path); return; }
    try { const res = await api("open", { path: item.path }); if (res && res.navigate) pane.navigate(res.navigate); }   // r39: folder .lnk
    catch (err) { showError(err); }
  }
  openSelected(newTab) {
    const pane = this.pane();
    const sel = pane.selected();
    if (!sel.length) return;
    if (sel.length === 1 && !newTab) { this.openItem(pane, sel[0]); return; }
    for (const item of sel.slice(0, 20)) {
      if (item.d) this.newTab(item.path);
      else api("open", { path: item.path }).catch(showError);
    }
  }
  newMenu(anchor) {
    const pane = this.pane();
    if (pane.mode !== "dir") return;
    const r = anchor.getBoundingClientRect();
    menu([
      { label: t("new.folder"), icon: "folderPlus", kb: "Ctrl+Shift+N", run: () => this.newFolder() },
      { sep: true },
      { label: t("new.text"), icon: "file", run: () => this.newFile(t("new.text_name") + ".txt") },
      { label: t("new.markdown"), icon: "documents", run: () => this.newFile(t("new.md_name") + ".md") },
      { label: t("new.html"), icon: "file", run: () => this.newFile("index.html") },
      { label: t("new.json"), icon: "file", run: () => this.newFile("data.json") },
      { label: t("x.new.more"), icon: "filePlus", sub: () => extras.templateItems(this) },
      { label: t("new.custom"), icon: "filePlus", run: async () => { const name = await promptDialog({ title: t("new.custom"), label: t("new.custom_label"), value: "file.txt", icon: "filePlus", select: [0, 4] }); if (name) this.newFile(name.trim(), false); } },
    ], r.left, r.bottom + 4);
  }
  async newFolder() {
    const pane = this.pane();
    if (pane.mode !== "dir" || feat41.guardReadOnly(pane)) return;
    try {
      const res = await api("mkdir", { parent: pane.path, name: t("new.folder_name"), unique: true });
      pane.pendingSelect = [res.path];
      await pane.reload(true);
      const idx = pane.index.get(res.path);
      if (idx !== undefined) pane.startRename(idx);
    } catch (err) { showError(err); }
  }
  async newFile(name, unique = true) {
    const pane = this.pane();
    if (pane.mode !== "dir" || !name) return;
    try {
      const res = await api("newfile", { parent: pane.path, name, unique });
      pane.pendingSelect = [res.path];
      await pane.reload(true);
      const idx = pane.index.get(res.path);
      if (idx !== undefined) pane.startRename(idx);
    } catch (err) { showError(err); }
  }
  cmdCopy() { this.setClipboard(this.pane().selectedPaths(), false); }
  cmdCut() { const pane = this.pane(); if (pane.mode === "trash") return; this.setClipboard(pane.selectedPaths(), true); }
  setClipboard(paths, cut) {
    if (!paths.length) return;
    this.clipboard = { paths, cut, set: new Set(paths), at: Date.now() };
    this.osClipboardStamp = null;
    api("osclip", { paths, cut }).catch(() => {});
    toast(t(cut ? "clip.cut" : "clip.copied", { n: itemsWord(paths.length) }), "info");
    this.tabs.forEach((tab) => tab.panes.forEach((p) => { if (p.el) { p.dirty = true; p.renderVisible(true); } }));
    this.updateCommands();
  }
  async cmdPaste(target) {
    const pane = this.pane();
    const dest = target || pane.folder;
    if (!dest) return;
    let clip = this.clipboard;
    if (isWin()) {
      // files copied in Explorer since our own copy win
      try {
        const os = await api("osclip");
        const mine = clip && os.paths.length === clip.paths.length && os.paths.every((p, i) => samePath(p, clip.paths[i]));
        if (os.paths.length && !mine) clip = { paths: os.paths, cut: os.cut, set: new Set(os.paths) };
        // r40-files-core: files copied out of a zip folder in Explorer / Outlook attachments — no paths, only contents
        if (!os.paths.length && os.virtual) {
          const res = await api("ops/submit", { kind: "vpaste", dest, count: os.virtual });
          this.trackOp(res.op);
          return;
        }
      } catch (e) { /* keep the internal clipboard */ }
    }
    if (!clip || !clip.paths.length) return;
    await this.transfer(clip.paths, dest, clip.cut ? "move" : "copy");
    if (clip.cut && clip === this.clipboard) { this.clipboard = null; this.updateCommands(); }
    if (clip.cut && isWin()) api("osclip", { paths: [], cut: false }).catch(() => {});   // r40-files-core: like Explorer
  }
  async transfer(paths, dest, kind) {
    paths = paths.filter((p) => !(kind === "move" && samePath(parentPath(p), dest)));
    if (!paths.length) return;
    for (const p of paths) {
      if (isInside(dest, p)) { toast(t("err.into_self"), "err"); return; }
    }
    if (feat41.transfer(this, paths, dest, kind)) return;   // r41-files: out of an archive = extract, into one = add
    try {
      const res = await api("ops/submit", { kind, sources: paths, dest, args: { copy_suffix: t("copy.suffix") } });
      this.trackOp(res.op);
    } catch (err) { showError(err); }
  }
  cmdRename() {
    const pane = this.pane();
    if (feat41.guardReadOnly(pane)) return;
    const sel = pane.selected();
    if (!sel.length) return;
    if (sel.length === 1) pane.startRename();
    else dialogs.batchRename(this, sel.map((r) => r.path));
  }
  async cmdDelete(permanent) {
    const pane = this.pane();
    const sel = pane.selectedPaths();
    if (!sel.length) return;
    if (pane.mode === "trash") { this.trashPurge(pane.selected()); return; }
    if (feat41.guardReadOnly(pane)) return;
    await this.deletePaths(sel, permanent);
  }
  async deletePaths(paths, permanent) {
    try {
      const prot = await api("protected", { paths });
      const bad = Object.keys(prot.protected);
      if (bad.length) { toast(t("err.protected_list", { name: baseName(bad[0]) }), "err"); return; }
    } catch (e) { /* the backend checks again */ }
    const label = paths.length === 1 ? `«${baseName(paths[0])}»` : itemsWord(paths.length);
    if (permanent && this.settings.confirm_delete !== false) {   // r41-files: «Ask before deleting permanently»
      const ok = await confirmDialog({ title: t("del.perm_title"), text: t("del.perm_text", { what: label }), ok: t("del.perm_ok"), danger: true, icon: "trash" });
      if (!ok) return;
    } else if (this.settings.confirm_trash) {
      const ok = await confirmDialog({ title: t("del.trash_title"), text: t("del.trash_text", { what: label }), ok: t("del.trash_ok"), icon: "trash" });
      if (!ok) return;
    }
    try {
      const res = await api("ops/submit", { kind: permanent ? "delete" : "trash", sources: paths });
      this.trackOp(res.op);
      const pane = this.pane();
      // move the focus to the next item, like Explorer
      const next = pane.shown.find((r, i) => i > pane.focus && !paths.includes(r.path)) || pane.shown.slice().reverse().find((r) => !paths.includes(r.path));
      if (next) pane.pendingSelect = [next.path];
    } catch (err) { showError(err); }
  }
  async undo() {
    try { const res = await api("ops/undo", {}); this.trackOp(res.op); toast(t("ops.undoing"), "info"); }
    catch (err) { if (err.code === "nothing_to_undo") toast(t("err.nothing_to_undo"), "info"); else showError(err); }
  }
  async redo() {   // r40-files-2
    try { const res = await api("ops/redo", {}); this.trackOp(res.op); toast(t("ops.redoing"), "info"); }
    catch (err) { if (err.code === "nothing_to_redo") toast(t("err.nothing_to_redo"), "info"); else showError(err); }
  }
  copyPaths(paths, quoted) {
    const text = paths.map((p) => (quoted ? `"${p}"` : p)).join("\n");
    navigator.clipboard.writeText(text).then(() => toast(t("clip.path_copied"), "info")).catch(() => {
      const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast(t("clip.path_copied"), "info");
    });
  }
  compress(paths) { return feat41.compressDialog(this, paths); }          // r41-files: format, level, password
  extract(path, here) { return feat41.quickExtract(this, path, here); }   // r41-files: one root folder → no extra level
  async terminalHere(path) { try { await api("terminal", { path: path || this.pane().folder || S.home }); } catch (err) { showError(err); } }
  async setTag(paths, body) {
    try { const res = await api("tags", { paths, ...body }); this.setTags(res.tags); this.renderSidebar(); this.tabs.forEach((tab) => tab.panes.forEach((p) => { if (p.el) { p.dirty = true; p.renderVisible(true); } })); }
    catch (err) { showError(err); }
  }
  async trashRestore(items) {
    const pane = this.pane();
    const rows = items || pane.selected();
    if (!rows.length) { toast(t("trash.select_first"), "info"); return; }
    try { const res = await api("trash/restore", { tokens: rows.map((r) => r.token) }); toast(t("trash.restored", { n: itemsWord(res.restored.length) })); pane.reload(true); }
    catch (err) { showError(err); }
  }
  async trashPurge(rows) {
    if (!rows.length) return;
    const ok = await confirmDialog({ title: t("del.perm_title"), text: t("del.perm_text", { what: itemsWord(rows.length) }), ok: t("del.perm_ok"), danger: true });
    if (!ok) return;
    try { await api("trash/purge", { tokens: rows.map((r) => r.token) }); this.pane().reload(true); } catch (err) { showError(err); }
  }
  async trashEmpty() {
    const ok = await confirmDialog({ title: t("trash.empty_title"), text: t("trash.empty_text"), ok: t("trash.empty_btn"), danger: true, icon: "trash" });
    if (!ok) return;
    try { await api("trash/empty", {}); this.pane().reload(true); toast(t("trash.emptied")); } catch (err) { showError(err); }
  }

  // ---------------------------------------------------------------- menus
  sortMenu(anchor) {
    const pane = this.pane();
    const r = anchor.getBoundingClientRect();
    const keys = pane.mode === "search" ? ["name", "folder", "m", "s"] : pane.mode === "trash" ? ["name", "original", "m", "s"] : ["name", "m", "type", "s", "c"];
    menu([
      ...keys.map((k) => ({ label: t("col." + (k === "m" && pane.mode === "trash" ? "deleted" : k)), checked: pane.sort.key === k, run: () => pane.sortBy(k) })),
      { sep: true },
      { label: t("sort.asc"), checked: pane.sort.dir > 0, run: () => { pane.sort.dir = 1; pane.rememberView(); pane.refresh(false); pane.renderHead(); } },
      { label: t("sort.desc"), checked: pane.sort.dir < 0, run: () => { pane.sort.dir = -1; pane.rememberView(); pane.refresh(false); pane.renderHead(); } },
      { sep: true },
      { label: t("sort.folders"), icon: "folder", sub: () => ["auto", "top", "mixed"].map((v) => ({ label: t("sort.folders." + v), checked: foldersOrder(this.settings) === v, run: () => this.setSetting("folders_order", v) })) },   // r41-files
      { sep: true },
      { label: t("group.by"), icon: "group", sub: () => ["none", "type", "date", "size", "name", "tag"].map((g) => ({ label: t("group." + g), checked: pane.group === g, run: () => pane.setGroup(g) })) },
    ], r.left, r.bottom + 4);
  }
  viewMenu(anchor) {
    const pane = this.pane();
    const r = anchor.getBoundingClientRect();
    const sizes = [["xl", 224], ["l", 160], ["m", 96], ["s", 48]];
    menu([
      { label: t("view.details"), icon: "details", checked: pane.view === "details", kb: "Ctrl+Shift+6", run: () => pane.setView("details") },
      { label: t("view.list"), icon: "list", checked: pane.view === "list", kb: "Ctrl+Shift+5", run: () => pane.setView("list") },
      { label: t("view.tiles"), icon: "tiles", checked: pane.view === "tiles", kb: "Ctrl+Shift+7", run: () => pane.setView("tiles") },   // r41-files
      ...sizes.map(([k, s]) => ({ label: t("view.icons_" + k), checked: pane.view === "grid" && pane.iconSize === s, run: () => { pane.iconSize = s; pane.setView("grid"); } })),
      { label: t("view.gallery"), icon: "gallery", checked: pane.view === "gallery", kb: "Ctrl+Shift+1", run: () => pane.setView("gallery") },
      { sep: true },
      { label: t("view.hidden"), checked: !!this.settings.show_hidden, kb: "Ctrl+H", run: () => this.setSetting("show_hidden", !this.settings.show_hidden) },
      { label: t("view.extensions"), checked: this.settings.show_extensions !== false, run: () => this.setSetting("show_extensions", this.settings.show_extensions === false) },
      { label: t("view.preview"), checked: this.right === "preview", kb: "Alt+P", run: () => this.togglePreview() },
      { label: t("view.split"), checked: this.tab().panes.length > 1, kb: "Ctrl+\\", run: () => this.toggleSplit() },
      { sep: true },
      { label: t("view.density"), sub: () => ["compact", "normal", "comfortable"].map((d) => ({ label: t("density." + d), checked: (this.settings.density || "normal") === d, run: () => { this.setSetting("density", d); document.documentElement.dataset.density = d; } })) },
      { label: t("view.columns"), disabled: pane.view !== "details", sub: () => this.columnItems(pane) },
    ], r.left, r.bottom + 4);
  }
  columnItems(pane) {
    return [["m", "col.m"], ["type", "col.type"], ["s", "col.s"], ["c", "col.c"]].map(([k, label]) => ({ label: t(label), checked: !pane.hiddenCols.has(k), run: () => { if (pane.hiddenCols.has(k)) pane.hiddenCols.delete(k); else pane.hiddenCols.add(k); pane.layout(); this.saveSession(); } }));
  }
  columnsMenu(pane, x, y) {
    if (pane.mode !== "dir") return;
    menu([...this.columnItems(pane), { sep: true }, { label: t("col.reset"), run: () => { localStorage.removeItem("mf.cols"); pane.layout(); } }], x, y);
  }
  selectMenu(anchor) {
    const pane = this.pane();
    const r = anchor.getBoundingClientRect();
    menu([
      { label: t("sel.all"), icon: "checkAll", kb: "Ctrl+A", run: () => pane.selectAll() },
      { label: t("sel.none"), icon: "x", run: () => pane.selectNone() },
      { label: t("sel.invert"), icon: "invert", run: () => pane.invertSelection() },
      { sep: true },
      { label: t("sel.files"), run: () => pane.selectWhere((x) => !x.d) },
      { label: t("sel.folders"), run: () => pane.selectWhere((x) => x.d) },
      { label: t("sel.same_type"), disabled: !pane.selected().length, run: () => { const exts = new Set(pane.selected().map((x) => (x.d ? "/" : extOf(x.n)))); pane.selectWhere((x) => exts.has(x.d ? "/" : extOf(x.n))); } },
      { label: t("sel.pattern"), icon: "filter", run: async () => {
        const value = await promptDialog({ title: t("sel.pattern"), label: t("sel.pattern_label"), value: "*.txt", icon: "filter" });
        if (!value) return;
        const re = new RegExp("^" + value.trim().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$", "i");
        pane.selectWhere((x) => re.test(x.n));
      } },
      { label: t("sel.filter"), icon: "search", run: async () => {
        const value = await promptDialog({ title: t("sel.filter"), label: t("sel.filter_label"), value: pane.filter, icon: "filter" });
        if (value === null) return;
        pane.filter = value; pane.refresh(true);
      } },
    ], r.left, r.bottom + 4);
  }
  moreMenu(anchor) {
    const pane = this.pane();
    const r = anchor.getBoundingClientRect();
    const sel = pane.selectedPaths();
    menu([
      { label: t("cmd.undo"), icon: "undo", kb: "Ctrl+Z", run: () => this.undo() },
      { label: t("cmd.redo"), icon: "redo", kb: "Ctrl+Y", run: () => this.redo() },   // r40-files-2
      { label: t("ops.title"), icon: "clock", run: () => this.opsUi.toggle(true) },
      { sep: true },
      { label: t("cmd.compress"), icon: "zip", disabled: !sel.length || pane.mode !== "dir", run: () => this.compress(sel) },
      { label: t("cmd.copy_path"), icon: "link", kb: "Ctrl+Shift+C", disabled: !sel.length && !pane.folder, run: () => this.copyPaths(sel.length ? sel : [pane.folder]) },
      { label: t("cmd.terminal"), icon: "terminal", disabled: !pane.folder, run: () => this.terminalHere() },
      { label: t("cmd.reveal_native"), icon: "windows", disabled: !pane.folder, run: () => api("reveal", { path: sel[0] || pane.folder }).catch(showError) },
      { label: t("cmd.pin"), icon: "pin", disabled: !pane.folder, run: () => this.pin(pane.folder) },
      { sep: true },
      { label: t("tools.usage"), icon: "chart", disabled: !pane.folder, run: () => tools.usageDialog(this, pane.folder) },
      { label: t("tools.dupes"), icon: "dupes", disabled: !pane.folder, run: () => tools.dupesDialog(this, pane.folder) },
      { label: t("tools.foldersize"), icon: "hash", disabled: !pane.folder || pane.mode !== "dir", run: () => tools.folderSizes(this, pane) },
      { label: t("x.cmp.title"), icon: "split", run: () => { const other = this.otherPane(); extras.compareDialog(this, pane.folder, other ? other.folder : ""); } },
      { label: t("x.img.menu"), icon: "image", disabled: !pane.selected().some(extras.isImage), run: () => extras.imageDialog(this, pane.selected().filter(extras.isImage).map((r) => r.path)) },
      { label: t("x.editor"), icon: "code", disabled: !pane.folder, sub: () => extras.editorItems(sel[0] || pane.folder) },
      { sep: true },
      { label: t("cmd.properties"), icon: "info", kb: "Alt+Enter", run: () => dialogs.properties(this, sel.length ? sel : [pane.folder].filter(Boolean)) },
      { label: t("cmd.settings"), icon: "gear", run: () => feat41.settingsDialog(this) },
    ], r.right - 240, r.bottom + 4);
  }
  contextMenu(pane, x, y, onItem) {
    const sel = pane.selected();
    const paths = sel.map((r) => r.path);
    if (pane.mode === "trash") {
      menu(onItem ? [
        { label: t("trash.restore"), icon: "restore", run: () => this.trashRestore() },
        { label: t("del.perm_ok"), icon: "trash", danger: true, run: () => this.trashPurge(sel) },
      ] : [{ label: t("nav.refresh"), icon: "refresh", run: () => pane.reload(true) }, { label: t("trash.empty_btn"), icon: "trash", danger: true, run: () => this.trashEmpty() }], x, y);
      return;
    }
    if (!onItem) { this.backgroundMenu(pane, x, y); return; }
    const one = sel.length === 1 ? sel[0] : null;
    const ext = one ? extOf(one.n) : "";
    const isArchive = one && !one.d && ARCHIVE_EXT.has(ext);
    if (one && !one.d && isWin()) this.openWithList(one.path);    // prefetch: the submenu opens filled
    const tagRow = one ? this.tagOf(one.path) : null;
    const colorsNode = h("div", { class: "colors" });
    for (const c of S.boot.tag_colors) {
      const on = !!(tagRow && (tagRow.colors || []).includes(c));
      const b = h("button", { class: on ? "on" : "", title: t("tag." + c), html: `<i class="dot ${c}"></i>` });
      b.addEventListener("click", () => { closeMenus(); this.setTag(paths, { toggle: c }); });
      colorsNode.append(b);
    }
    const items = [
      { label: one && one.d ? t("ctx.open") : t("ctx.open"), icon: "open", kb: "Enter", run: () => this.openSelected() },
      one && one.d ? { label: t("ctx.open_tab"), icon: "plus", kb: "Ctrl+Enter", run: () => this.newTab(one.path) } : null,
      one && one.d && this.tab().panes.length > 1 ? { label: t("ctx.open_other"), icon: "split", run: () => this.otherPane().navigate(one.path) } : null,
      one && !one.d && isWin() ? { label: t("ctx.open_with_menu"), icon: "windows", sub: () => this.openWithItems(one.path) } : null,
      pane.mode === "search" ? { label: t("ctx.open_location"), icon: "folder", run: () => this.revealPath(sel[0].path) } : null,
      isArchive ? { label: t("ctx.browse_archive"), icon: "folder", run: () => pane.navigate(one.path) } : null,   // r41-files
      isArchive ? { label: t("ctx.extract_to"), icon: "extract", run: () => feat41.extractDialog(this, one.path, {}) } : null,
      isArchive ? { label: t("ctx.extract"), icon: "extract", run: () => this.extract(one.path, false) } : null,
      isArchive ? { label: t("ctx.extract_here"), icon: "extract", run: () => this.extract(one.path, true) } : null,
      { sep: true },
      { node: h("div", { class: "mrow" }, ...[
        ["cut", "cut", () => this.cmdCut()], ["copy", "copy", () => this.cmdCopy()], ["rename", "rename", () => this.cmdRename()], ["delete", "trash", () => this.cmdDelete(false)],
      ].map(([key, ico, run]) => { const b = h("button", { class: "icon-btn", title: t("cmd." + key), html: icon(ico) }); b.addEventListener("click", () => { closeMenus(); run(); }); return b; })) },
      one && one.d ? { label: t("cmd.paste_into"), icon: "paste", disabled: !this.clipboard, run: () => this.cmdPaste(one.path) } : null,
      { label: t("cmd.compress"), icon: "zip", disabled: pane.mode !== "dir", run: () => this.compress(paths) },
      { label: t("cmd.copy_path"), icon: "link", kb: "Ctrl+Shift+C", run: () => this.copyPaths(paths, sel.length > 1) },
      { label: t("x.copy_as"), icon: "link", sub: () => extras.copyAsItems(paths) },
      { label: t("cmd.batch_rename"), icon: "rename", disabled: sel.length < 2, run: () => dialogs.batchRename(this, paths) },
      sel.some(extras.isImage) ? { label: t("x.img.menu"), icon: "image", run: () => extras.imageDialog(this, sel.filter(extras.isImage).map((r) => r.path)) } : null,
      sel.length === 2 && sel.every((r) => r.d) ? { label: t("x.cmp.title"), icon: "split", run: () => extras.compareDialog(this, paths[0], paths[1]) } : null,
      { sep: true },
      { head: t("ctx.tags") },
      { node: colorsNode },
      { label: tagRow && tagRow.favorite ? t("ctx.unfavorite") : t("ctx.favorite"), icon: "star", run: () => this.setTag(paths, { favorite: !(tagRow && tagRow.favorite) }) },
      one && one.d ? { label: t("cmd.pin"), icon: "pin", run: () => this.pin(one.path) } : null,
      { sep: true },
      one && one.d ? { label: t("cmd.terminal"), icon: "terminal", run: () => this.terminalHere(one.path) } : null,
      one ? { label: t("x.editor"), icon: "code", sub: () => extras.editorItems(one.path) } : null,
      { label: t("ctx.ask_ai"), icon: "sparkle", run: () => { this.setRight("ai"); ai.prefill(this, paths); } },
      one && one.d ? { label: t("tools.foldersize"), icon: "hash", run: () => tools.folderSizes(this, pane, paths) } : null,
      { label: t("cmd.reveal_native"), icon: "windows", run: () => api("reveal", { path: paths[0] }).catch(showError) },
      isWin() ? { label: t("ctx.win_props"), icon: "windows", disabled: !one, run: () => api("shellverb", { path: one.path, verb: "properties" }).catch(showError) } : null,
      { label: t("del.perm_ok"), icon: "trash", danger: true, kb: "Shift+Del", run: () => this.cmdDelete(true) },
      { sep: true },
      { label: t("cmd.properties"), icon: "info", kb: "Alt+Enter", run: () => dialogs.properties(this, paths) },
    ];
    menu(items, x, y);
  }
  /** «Open with» submenu: apps Windows recommends for the extension (cached per extension, prefetched when the
   *  context menu opens) + the system chooser. Resolves to menu items. */
  openWithList(path) {
    const ext = extOf(baseName(path)) || "";
    if (!this._openWith) this._openWith = new Map();
    const hit = this._openWith.get(ext);
    if (hit && Date.now() - hit.at < 60000) return hit.promise;
    // a failure (shell busy / timed out) is not cached: the next open asks again
    const promise = api("openwith?path=" + encodeURIComponent(path)).then((r) => r.handlers || [])
      .catch(() => { this._openWith.delete(ext); return []; });
    this._openWith.set(ext, { at: Date.now(), promise });
    return promise;
  }
  openWithItems(path) {
    return this.openWithList(path).then((apps) => [
      ...apps.map((a) => ({
        label: a.default ? `${a.name} (${t("ctx.default_app")})` : a.name,
        img: a.icon ? `/api/appicon?id=${encodeURIComponent(a.id)}&t=${encodeURIComponent(S.token)}` : null,
        icon: a.icon ? null : "open",
        run: () => api("openwith", { path, handler: a.id }).catch(showError),
      })),
      apps.length ? null : { label: t("ctx.no_apps"), disabled: true },
      { sep: true },
      { label: t("ctx.choose_app"), icon: "windows", run: () => this.openWithChooser(path) },
    ]);
  }
  openWithChooser(path) { return api("openwith", { path }).catch(showError); }
  backgroundMenu(pane, x, y) {
    if (pane.mode === "home") return;
    const items = [
      { label: t("cmd.view"), icon: "view", sub: () => [["details", "details"], ["list", "list"], ["tiles", "tiles"], ["grid", "grid"], ["gallery", "gallery"]].map(([v, ico]) => ({ label: t("view." + v), icon: ico, checked: pane.view === v, run: () => pane.setView(v) })) },
      { label: t("cmd.sort"), icon: "sort", sub: () => ["name", "m", "type", "s"].map((k) => ({ label: t("col." + k), checked: pane.sort.key === k, run: () => pane.sortBy(k) })) },
      { label: t("group.by"), icon: "group", sub: () => ["none", "type", "date", "size", "name", "tag"].map((g) => ({ label: t("group." + g), checked: pane.group === g, run: () => pane.setGroup(g) })) },
      { label: t("nav.refresh"), icon: "refresh", kb: "F5", run: () => pane.reload(true) },
      { sep: true },
    ];
    if (pane.mode === "dir") {
      items.push(
        { label: t("cmd.paste"), icon: "paste", kb: "Ctrl+V", disabled: !this.clipboard && !isWin(), run: () => this.cmdPaste() },
        { label: t("cmd.undo"), icon: "undo", kb: "Ctrl+Z", run: () => this.undo() },
        { sep: true },
        { label: t("new.folder"), icon: "folderPlus", kb: "Ctrl+Shift+N", run: () => this.newFolder() },
        { label: t("cmd.new"), icon: "plus", sub: () => [
          { label: t("new.text"), icon: "file", run: () => this.newFile(t("new.text_name") + ".txt") },
          { label: t("new.markdown"), icon: "documents", run: () => this.newFile(t("new.md_name") + ".md") },
          ...extras.templateItems(this),
          { sep: true },
          { label: t("new.custom"), icon: "filePlus", run: async () => { const name = await promptDialog({ title: t("new.custom"), label: t("new.custom_label"), value: "file.txt", icon: "filePlus", select: [0, 4] }); if (name) this.newFile(name.trim(), false); } },
        ] },
        { sep: true },
        { label: t("cmd.terminal"), icon: "terminal", run: () => this.terminalHere() },
        { label: t("x.editor"), icon: "code", sub: () => extras.editorItems(pane.path) },
        { label: t("cmd.pin"), icon: "pin", run: () => this.pin(pane.path) },
        { label: t("cmd.copy_path"), icon: "link", run: () => this.copyPaths([pane.path]) },
        { label: t("ctx.ask_ai"), icon: "sparkle", run: () => this.setRight("ai") },
        { sep: true },
        { label: t("cmd.properties"), icon: "info", run: () => dialogs.properties(this, [pane.path]) },
      );
    }
    menu(items, x, y);
  }
  folderMenu(path, x, y) {
    menu([
      { label: t("ctx.open"), icon: "open", run: () => this.pane().navigate(path) },
      { label: t("ctx.open_tab"), icon: "plus", run: () => this.newTab(path) },
      this.tab().panes.length > 1 ? { label: t("ctx.open_other"), icon: "split", run: () => this.otherPane().navigate(path) } : null,
      { sep: true },
      { label: t("cmd.paste_into"), icon: "paste", disabled: !this.clipboard, run: () => this.cmdPaste(path) },
      { label: t("cmd.pin"), icon: "pin", run: () => this.pin(path) },
      { label: t("cmd.copy_path"), icon: "link", run: () => this.copyPaths([path]) },
      { label: t("cmd.terminal"), icon: "terminal", run: () => this.terminalHere(path) },
      { label: t("tools.usage"), icon: "chart", run: () => tools.usageDialog(this, path) },
      extras.ejectItem(this, path),
      { sep: true },
      { label: t("cmd.properties"), icon: "info", run: () => dialogs.properties(this, [path]) },
    ], x, y);
  }
  async setSetting(key, value) {
    this.settings[key] = value;
    try { const res = await api("settings", { [key]: value }); this.settings = res.settings; } catch (err) { showError(err); }
    this.tabs.forEach((tab) => tab.panes.forEach((p) => { if (p.el) p.refresh(true); }));
    this.updateChrome();
  }

  // ================================================================ keyboard
  bindKeys() {
    document.addEventListener("keydown", (e) => {
      if ($(".modal-back")) return;
      if ($(".menu") && ["Escape", "Delete", "Enter", " ", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "F2"].includes(e.key)) return;   // r40-files-max: an open menu owns these keys
      const tag = e.target.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || e.target.isContentEditable;
      const ctrl = e.ctrlKey || e.metaKey;
      const pane = this.pane();
      if (!pane) return;
      const key = e.key;
      // r39-files-perf: letter shortcuts by physical key — with the Russian layout e.key is «с», not «c», and
      // Ctrl+C/V/X/Z/A/T/W/L/N… silently did nothing
      const kl = /^Key[A-Z]$/.test(e.code || "") ? e.code.slice(3).toLowerCase() : key.toLowerCase();
      const run = (fn) => { e.preventDefault(); e.stopPropagation(); fn(); };
      // window-wide shortcuts (also while typing)
      if (ctrl && !e.shiftKey && kl === "t") return run(() => this.newTab());   // r41-files: Settings → «New tab (Ctrl+T)»
      if (ctrl && !e.shiftKey && !e.altKey && kl === "n") return run(() => this.newTab(pane.folder || SPECIAL.HOME));   // r39: Ctrl+N (Explorer: new window) — a new tab here
      if (ctrl && e.shiftKey && kl === "t") return run(() => this.reopenTab());
      if (ctrl && kl === "w") return run(() => this.closeTab());
      if (ctrl && key === "Tab") return run(() => this.switchTab((this.active + (e.shiftKey ? -1 : 1) + this.tabs.length) % this.tabs.length));
      if (ctrl && /^[1-9]$/.test(key) && !e.shiftKey) return run(() => this.switchTab(key === "9" ? this.tabs.length - 1 : +key - 1));
      if ((ctrl && kl === "l") || (e.altKey && kl === "d") || key === "F4") return run(() => this.editAddress());
      if ((ctrl && !e.shiftKey && (kl === "f" || kl === "e")) || key === "F3") return run(() => { this.searchInput.focus(); this.searchInput.select(); });
      if (key === "F11") return run(() => api("window", { cmd: "fullscreen" }));
      if (ctrl && e.shiftKey && kl === "a") return run(() => this.toggleAI());
      if (ctrl && e.shiftKey && kl === "f") return run(() => feat40.toggleKindBar(this));   // r40-files-max: type filter
      if (ctrl && key === "\\") return run(() => this.toggleSplit());
      if (e.altKey && kl === "p") return run(() => this.togglePreview());
      if (typing) return;
      if (e.altKey && key === "ArrowLeft") return run(() => pane.back());
      if (e.altKey && key === "ArrowRight") return run(() => pane.forward());
      if (e.altKey && key === "ArrowUp") return run(() => pane.up());
      if (key === "BrowserBack") return run(() => pane.back());
      if (key === "BrowserForward") return run(() => pane.forward());
      if (key === "Backspace") return run(() => pane.back());
      if (key === "F5" || (ctrl && kl === "r")) return run(() => { pane.reload(true); this.loadSidebar(); });
      if (key === "F6") return run(() => { const other = this.otherPane(); if (other) { this.activatePane(other); other.fv.focus(); } else this.searchInput.focus(); });
      if (key === "Escape") return run(() => { if (pane.mode === "search") this.clearSearch(); else if (this.clipboard && this.clipboard.cut) { this.clipboard = null; pane.dirty = true; pane.renderVisible(true); } else pane.selectNone(); });
      if (pane.mode === "home") return;
      if (ctrl && kl === "a") return run(() => pane.selectAll());
      if (ctrl && kl === "c" && e.shiftKey) return run(() => this.copyPaths(pane.selectedPaths().length ? pane.selectedPaths() : [pane.folder]));
      if (ctrl && kl === "c") return run(() => this.cmdCopy());
      if (ctrl && kl === "x") return run(() => this.cmdCut());
      if (ctrl && kl === "v") return run(() => this.cmdPaste());
      if (ctrl && kl === "z" && !e.shiftKey) return run(() => this.undo());
      if (ctrl && (kl === "y" || (e.shiftKey && kl === "z"))) return run(() => this.redo());   // r40-files-2: Explorer's redo
      if (ctrl && kl === "h") return run(() => this.setSetting("show_hidden", !this.settings.show_hidden));
      if (ctrl && e.shiftKey && kl === "n") return run(() => this.newFolder());
      if (ctrl && e.shiftKey && /^[1-8]$/.test(e.code.replace("Digit", ""))) {
        const n = +e.code.replace("Digit", "");
        const map = { 1: ["grid", 224], 2: ["grid", 160], 3: ["grid", 96], 4: ["grid", 48], 5: ["list"], 6: ["details"], 7: ["tiles"], 8: ["gallery"] };   // r41-files: 7 = Tiles, like Explorer
        const [v, s] = map[n] || ["details"];
        return run(() => { if (s) pane.iconSize = s; pane.setView(n === 1 && false ? "gallery" : v); });
      }
      if (key === "F2") return run(() => this.cmdRename());
      if (key === "Delete") return run(() => this.cmdDelete(e.shiftKey));
      if (ctrl && !e.shiftKey && kl === "d") return run(() => this.cmdDelete(false));   // r39: Ctrl+D = to the recycle bin (Explorer)
      if (e.altKey && key === "Enter") return run(() => dialogs.properties(this, pane.selectedPaths().length ? pane.selectedPaths() : [pane.folder].filter(Boolean)));
      if (key === "Enter") return run(() => this.openSelected(ctrl));
      if (ctrl && (key === "=" || key === "+" || key === "-")) return run(() => { const p = pane; p.iconSize = Math.max(32, Math.min(256, p.iconSize + (key === "-" ? -16 : 16))); if (p.view === "details" || p.view === "list") p.view = "grid"; p.layout(); this.updateStatus(); });
      if (key === " " && !ctrl && !e.shiftKey && !pane.typeBuffer && pane.focus >= 0) return run(() => extras.quickLook(this, pane));   // r39: Quick Look
      if (pane.onKey(e)) { e.preventDefault(); }
    });
    document.addEventListener("wheel", (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      const pane = this.pane();
      if (!pane || !pane.fv.contains(e.target)) return;
      e.preventDefault();
      pane.iconSize = Math.max(32, Math.min(256, pane.iconSize + (e.deltaY < 0 ? 16 : -16)));
      if (pane.view === "details" || pane.view === "list") pane.view = "grid";
      pane.layout(); this.updateStatus();
    }, { passive: false });
    document.addEventListener("mouseup", (e) => {
      if (e.button === 3) { e.preventDefault(); this.pane().back(); }
      if (e.button === 4) { e.preventDefault(); this.pane().forward(); }
    });
  }

  // ================================================================ drag & drop (r40-files-core: logic in dnd.js)
  dragStart(pane, e, paths) {
    if (!paths.length) { e.preventDefault(); return; }
    const nativeDrag = window.pywebview?.api?.file_drag;
    if (isWin() && nativeDrag && paths.every((p) => !p.includes("::"))) {
      e.preventDefault(); // cancel Chromium's text drag before starting OLE
      this.dragPaths = paths;
      this.dragFrom = pane;
      nativeDrag(paths).then((r) => { if (!r.ok) toast(t("err." + r.error), "err"); })
        .catch(showError).finally(() => { this.dragPaths = null; this.dragFrom = null; this.dragLeave(); });
      return;
    }
    this.dragPaths = paths;
    this.dragFrom = pane;
    e.dataTransfer.setData("application/x-memufiles", JSON.stringify(paths));
    dnd.exportDownloadUrl(e, paths, pane.shown);        // one file can be dragged out to Explorer / the desktop
    e.dataTransfer.effectAllowed = "copyMove";
    const ghost = h("div", { class: "drag-ghost" });
    ghost.innerHTML = fileIcon(baseName(paths[0]), (pane.shown.find((r) => r.path === paths[0]) || {}).d) + `<span>${esc(baseName(paths[0]))}</span>` + (paths.length > 1 ? `<b>${paths.length}</b>` : "");
    document.body.append(ghost);
    e.dataTransfer.setDragImage(ghost, 16, 16);
    setTimeout(() => ghost.remove(), 0);
    const end = () => { this.dragPaths = null; this.dragLeave(); document.removeEventListener("dragend", end); };
    document.addEventListener("dragend", end);
  }
  readDragPaths(e) {
    try { const raw = e.dataTransfer.getData("application/x-memufiles"); if (raw) return JSON.parse(raw); } catch (err) { /* ignore */ }
    return this.dragPaths || [];
  }
  dropEffect(e, target) {
    const paths = this.dragPaths || [];
    if (e.ctrlKey) return "copy";
    if (e.shiftKey) return "move";
    if (!paths.length) return "copy";
    return driveOf(paths[0]) === driveOf(target) ? "move" : "copy";
  }
  dragOver(e, target, el) {
    const dt = e.dataTransfer;
    if (!dnd.canAccept(dt)) return;
    const internal = dt.types.includes("application/x-memufiles");
    if (internal && this.dragPaths && this.dragPaths.some((p) => isInside(target, p) || samePath(parentPath(p), target) && !e.ctrlKey)) {
      dt.dropEffect = "none";
      this.dragLeave();
      return;
    }
    e.preventDefault();
    // from outside: copy; Shift = move (only files Explorer gives us by path can really move)
    const effect = internal ? this.dropEffect(e, target) : (e.shiftKey && !e.ctrlKey && dt.types.includes("Files") ? "move" : "copy");
    try { dt.dropEffect = effect; } catch (err) { /* ignore */ }
    if (this.dropEl !== el) { if (this.dropEl) this.dropEl.classList.remove("drop"); this.dropEl = el; if (el) el.classList.add("drop"); }
    dnd.showHint(this, e, effect, target);
  }
  dragLeave() { dnd.hideHint(this); }
  drop(e, target) {
    e.preventDefault();
    // r40-files-core: NO stopPropagation — pywebview's document-level listener must see the drop to report
    // the paths of files dragged from Explorer (desktop.py); before, this swallowed it and nothing was copied
    this.dragLeave();
    const paths = this.readDragPaths(e);
    if (paths.length) { this.transfer(paths, target, this.dropEffect(e, target)); this.dragPaths = null; return; }
    const cap = dnd.captureExternal(e);              // synchronous: File objects / entries are gone after the event
    dnd.externalDrop(this, cap, target).catch(showError);
  }
  /* The backend "drop" event (pywebview, desktop only): [{name, path}] — path is "" for virtual files. */
  waitBackendDrop(ms) {
    if (this.backendDrop && Date.now() - this.backendDrop.at < 3000) { const rows = this.backendDrop.files; this.backendDrop = null; return Promise.resolve(rows); }
    this.backendDrop = null;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.dropWaiter = null; resolve(null); }, ms);
      this.dropWaiter = (rows) => { clearTimeout(timer); this.dropWaiter = null; resolve(rows); };
    });
  }
  bindGlobalDrop() {
    // anything dropped outside a drop target must not make WebView2 navigate to the file
    document.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("Files") && !e.defaultPrevented) e.preventDefault(); });
    document.addEventListener("drop", (e) => { if (!e.defaultPrevented) { e.preventDefault(); this.dragLeave(); } });
  }

  // ================================================================ backend events
  async pollEvents() {
    for (;;) {
      try {
        const data = await api("events?since=" + this.eventsSeq);
        if (data.seq < this.eventsSeq) this.eventsSeq = 0;
        else this.eventsSeq = data.seq;
        for (const ev of data.events) this.onEvent(ev);
      } catch (err) {
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  }
  onEvent(ev) {
    if (ev.type === "op") this.onOp(ev.op);
    else if (ev.type === "changed") {
      const paths = ev.paths || [];
      this.tabs.forEach((tab) => tab.panes.forEach((p) => {
        if (!p.el) return;
        if (p.mode === "trash" && paths.includes("::trash")) p.reload(true);
        else if (p.folder && paths.some((x) => samePath(x, p.folder))) { if (p.renaming) p.reloadAfterRename = true; else p.reload(true); }   // r40-files-2
      }));
      this.loadSidebar();
    } else if (ev.type === "open") { for (const p of ev.paths || []) this.openPathInNewTab(p, (ev.info || {})[p], this.openOpts(ev, p)); if (!(ev.paths || []).length) this.newTab(); }
    else if (ev.type === "drop") {     // r40-files-core: paths of a drop (dnd.externalDrop waits for them)
      const rows = ev.files || (ev.paths || []).map((p) => ({ name: baseName(p), path: p }));
      if (this.dropWaiter) this.dropWaiter(rows); else this.backendDrop = { files: rows, at: Date.now() };
    } else if (ev.type === "shutdown") toast(t("app.closing"), "info");
    else if (ev.type === "settings") this.reloadSettings();
  }
  /* Round 38: settings changed outside this window (MemuAI → Settings → MemuFiles): a "settings" event, or a
     re-read when the window gets focus. Applied in place; only a language change reloads the page. */
  async reloadSettings() {
    let res;
    try { res = await api("settings"); } catch (err) { return; }
    if (JSON.stringify(res.settings) !== JSON.stringify(this.settings)) this.applySettings(res);
  }
  /* res = reply of GET/POST /api/settings: { settings, lang, theme } (also used by the settings dialog). */
  applySettings(res) {
    if (res.lang !== getLang()) { setLang(res.lang); location.reload(); return true; }
    if (titlebar.available() && res.settings.custom_titlebar !== titlebar.active()) titlebar.setMode(res.settings.custom_titlebar);
    this.settings = res.settings;
    this.applyTheme(res.theme);
    document.documentElement.dataset.density = res.settings.density;
    this.sidebarEl.style.width = res.settings.sidebar_width + "px";
    this.rightEl.style.width = res.settings.preview_width + "px";
    resetCollator();
    this.tabs.forEach((tab) => tab.panes.forEach((p) => { if (p.el) p.refresh(true); }));
    this.updateChrome();
    this.setRight(this.right, false);
    return false;
  }
  trackOp(op) { if (op) { this.onOp(op); } }
  onOp(op) {
    const before = this.ops.get(op.id);
    if (before && (before.rev || 0) > (op.rev || 0)) return;   // an older snapshot (a submit reply after its events)
    this.ops.set(op.id, op);
    this.opsUi.onOp(op, before);        // r40-files-core: flyout card, status bar, conflicts, completion notices
    ai.opUpdated(this, op);
  }
  async refreshOps() { try { const res = await api("ops"); for (const op of res.ops) this.ops.set(op.id, op); this.opsUi.schedule(); } catch (e) { /* ignore */ } }
  async checkStamps() {
    if (document.hidden) return;
    const panes = [];
    const tab = this.tab();
    if (!tab) return;
    for (const p of tab.panes) if (p.el && p.mode === "dir" && !p.loading && p.stamp) panes.push(p);
    if (!panes.length) return;
    try {
      const res = await api("dirstamp", { paths: panes.map((p) => p.path) });
      for (const p of panes) { const s = res.stamps[p.path]; if (s && s !== p.stamp && !p.renaming) p.reload(true); }
    } catch (e) { /* ignore */ }
  }
}

function driveLabel(d) {
  if (isWin()) { const letter = d.path.replace(/\\$/, ""); return d.label ? `${d.label} (${letter})` : `${t("drive.local")} (${letter})`; }
  return d.path === "/" ? t("drive.root") : d.label;
}

const app = new App();
window.MemuFiles = app;
app.boot();
