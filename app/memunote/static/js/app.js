

import { $, $$, api, rawUrl, esc, icon, t, I18N, toast, showMenu, closeMenus, modal, prompt, confirm, palette, debounce,
  uid, basename, dirname, extname, fmtBytes, countWords, kindIcon, copyText, kbd, plural } from "./core.js";
import { CM, createState, buildConfig, configComp, langComp, readOnlyComp, languageFor, loadLanguage, languageByName, detectIndentation,
  mdWrap, mdLinePrefix, mdHeading, mdInsert, mdLink, mdTable, mdFormatTable, TOOLS, insertDateTime, jsonFormat, jsonError,
  setGhost } from "./editor.js";
import { readText, longestLine, docBlob, LONG_LINE } from "./bigtext.js";
import { BookView, NotebookView, JsonTree, xmlFormat, xmlError, notebookToMarkdown, notebookToScript } from "./formatviews.js";
import { SheetGrid, SheetData } from "./sheetgrid.js";
import { MarkdownPreview, CsvTable, PdfView, ImageView, HtmlPreview, DocEditor, docFind, renderMarkdown } from "./views.js";
import { AI, refreshStatus, onAIStatus, ChatPanel, inlineCommand, requestGhost, COMMANDS, aiExcerpt } from "./ai.js";
import { SlidesView, slidesOwnsKey, newDeckDialog } from "./slides/view.js";

import { liveMarkdown, LIVE_MAX } from "./livemd.js";
import { spellExtension, spellWordAt, suggestions as spellSuggestions, addToDictionary, ignoreInDoc, warmUp as spellWarmUp } from "./spell.js";
import { codeComplete, langKey } from "./codecomplete.js";
import { RunPanel, isRunnable } from "./runpanel.js";
import "./toolbar-fit.js";

const MN = (window.MN = { settings: {}, tabs: [], panes: [], active: "left", recent: [], closed: [], boot: null });

const TEXT_KINDS = new Set(["text", "markdown", "json", "csv", "log", "html", "notebook"]);

const viewMode = (tab) => (tab.kind === "csv" || tab.kind === "notebook") && tab.csvMode === "table";
const XML_RE = /\.(xml|xsd|xsl|xslt|svg|plist|csproj|vbproj|props|targets|config|resx|xaml|rss|atom|gpx|kml)$/i;
const DOC_KINDS = new Set(["docx", "odt", "rtf"]);
let untitledCounter = 0;

async function boot() {
  let data;
  try { data = await api("boot"); }
  catch (e) { document.body.innerHTML = `<div class="fatal">${icon("warn")}<p>MemuNote: ${esc(e.message)}</p></div>`; return; }
  MN.boot = data; MN.settings = data.settings; MN.recent = data.recent || []; MN.sep = data.sep || "\\";
  I18N.lang = data.lang; document.documentElement.lang = data.lang;
  applyTheme(data.theme);
  buildShell();
  MN.run = new RunPanel(runHooks());
  applySettings();
  if (MN.settings.spell_check) spellWarmUp();
  MN.panes = [makePane("left", $("#pane-left"))];
  const session = data.session || {};
  let restored = 0;
  if (MN.settings.restore_session && Array.isArray(session.tabs)) {
    for (const s of session.tabs) { if (restoreTab(s)) restored++; }
  }
  for (const f of data.files || []) await openPath(f, { activate: true });
  if (!MN.tabs.length) newTab();
  else {
    const want = MN.tabs.find((x) => x.id === session.active) || MN.tabs[MN.tabs.length - 1];
    if (!(data.files || []).length) activate(want.id);
  }
  if (session.layout?.split && MN.tabs.length) toggleSplit(true);
  if (session.layout?.ai) toggleAI(true);
  if (MN.settings.show_sidebar) toggleSidebar(true);
  pollEvents();
  setInterval(checkExternal, 2500);
  refreshStatus();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 30000);
  window.addEventListener("blur", () => { if (MN.settings.autosave === "focus") saveAll(true); saveSession.flush(); });
  window.addEventListener("focus", () => reloadSettings());
  window.addEventListener("beforeunload", () => { saveSession.flush(); });
  document.addEventListener("keydown", globalKeys, true);
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => e.preventDefault());
  document.body.classList.add("ready");
}

function applyTheme(theme) {
  MN.theme = theme;
  const root = document.documentElement;
  root.dataset.theme = theme.mode;
  for (const [k, v] of Object.entries(theme.tokens || {})) root.style.setProperty(k, v);
  if (!theme.tokens?.["--bg"]) root.classList.add("fallback-tokens"); else root.classList.remove("fallback-tokens");
  api("window", { cmd: "theme", mode: theme.mode }).catch(() => {});
}

function applySettings() {
  const s = MN.settings, root = document.documentElement;
  root.style.setProperty("--ed-font", `"${s.font_family}", "JetBrains Mono", "Cascadia Code", Consolas, monospace`);
  root.style.setProperty("--ed-size", (s.font_size * s.zoom / 100).toFixed(1) + "px");
  root.style.setProperty("--ed-lh", s.line_height);
  root.style.setProperty("--doc-zoom", s.zoom / 100);
  document.body.classList.toggle("no-toolbar", !s.show_toolbar);
  document.body.classList.toggle("no-statusbar", !s.show_statusbar);
  document.body.classList.toggle("compact-tabs", !!s.compact_tabs);
  for (const tab of MN.tabs) {
    if (tab.indent?.manual) continue;
    if (!tab.indent?.detected) tab.indent = { tab_size: s.tab_size, indent_with_tabs: s.indent_with_tabs };
    else if (tab.indent.indent_with_tabs) tab.indent.tab_size = s.tab_size;
  }
  for (const p of MN.panes) reconfigurePane(p);
  for (const tab of MN.tabs) tab.doc?.setZoom(s.zoom);
  updateStatus();
}

async function reloadSettings() {
  let r;
  try { r = await api("settings"); } catch { return; }
  if (JSON.stringify(r.settings) === JSON.stringify(MN.settings)) return;
  const before = MN.settings;
  MN.settings = r.settings;
  const th = await api("theme").catch(() => null);
  if (th && (before.theme !== r.settings.theme || before.language !== r.settings.language)) applyTheme(th.theme);
  applySettings();
  if (before.ai_enabled !== r.settings.ai_enabled || before.proxy_url !== r.settings.proxy_url || before.ai_model !== r.settings.ai_model) refreshStatus(true);
  if (th && th.lang !== I18N.lang) { I18N.lang = th.lang; document.documentElement.lang = th.lang; rebuildChrome(); }
  else renderToolbar();
}

async function saveSettings(patch) {
  const r = await api("settings", patch);
  const langChanged = r.lang !== I18N.lang;
  MN.settings = r.settings;
  if (patch.theme !== undefined) applyTheme(r.theme);
  applySettings();
  if (langChanged) { I18N.lang = r.lang; document.documentElement.lang = r.lang; rebuildChrome(); }
  return r.settings;
}

function buildShell() {
  document.body.innerHTML = `
  <header class="topbar">
    <button class="icon-btn brand" id="btn-menu" title="${esc(t("menu"))}" aria-label="${esc(t("menu"))}"><img class="logo" src="memunote-small.svg" alt=""></button>
    <div class="tabstrip" id="tabs" role="tablist"></div>
    <button class="icon-btn" id="btn-new" title="${esc(t("new_tab"))} (${kbd("Ctrl+N")})">${icon("plus")}</button>
    <button class="icon-btn" id="btn-tablist" title="${esc(t("all_tabs"))}">${icon("chevron")}</button>
    <span class="spacer"></span>
    <button class="icon-btn" id="btn-palette" title="${esc(t("command_palette"))} (${kbd("Ctrl+Shift+P")})">${icon("palette")}</button>
    <button class="icon-btn ai-toggle" id="btn-ai" title="${esc(t("ai_panel"))} (${kbd("Ctrl+Shift+L")})">${icon("sparkle")}<span class="ai-dot"></span></button>
    <button class="icon-btn" id="btn-settings" title="${esc(t("settings"))} (${kbd("Ctrl+,")})">${icon("settings")}</button>
  </header>
  <div class="toolbar" id="toolbar" role="toolbar"></div>
  <div class="banner-host" id="banners"></div>
  <main class="workspace">
    <aside class="sidebar" id="sidebar" hidden></aside>
    <div class="panes" id="panes"><section class="pane" id="pane-left" data-pane="left"></section><div class="pane-gutter" hidden></div><section class="pane" id="pane-right" data-pane="right" hidden></section></div>
    <div class="ai-gutter" hidden></div>
    <aside class="ai-panel" id="ai-panel" hidden></aside>
  </main>
  <footer class="statusbar" id="status"></footer>
  <div id="print-root" class="doc-prose"></div>`;
  $("#btn-menu").onclick = (e) => appMenu(e.currentTarget);
  $("#btn-new").onclick = () => newTab();
  $("#btn-tablist").onclick = (e) => tabList(e.currentTarget);
  $("#btn-palette").onclick = () => commandPalette();
  $("#btn-ai").onclick = () => toggleAI();
  $("#btn-settings").onclick = () => openSettings();
  $("#tabs").addEventListener("wheel", (e) => { if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { e.currentTarget.scrollLeft += e.deltaY; e.preventDefault(); } }, { passive: false });
  $("#tabs").addEventListener("dblclick", (e) => { if (e.target.id === "tabs") newTab(); });
  setupResizer($(".ai-gutter"), $("#ai-panel"), "ai_w", true);
  setupResizer($(".pane-gutter"), $("#pane-left"), "split_w", false, true);
  onAIStatus(() => { $("#btn-ai").classList.toggle("online", AI.online); updateStatus(); renderToolbar(); document.body.classList.toggle("ai-offline", !AI.online); });
}
function rebuildChrome() {
  const keep = { ai: !$("#ai-panel").hidden, side: !$("#sidebar").hidden };

  $("#btn-menu").title = t("menu"); $("#btn-new").title = t("new_tab"); $("#btn-palette").title = t("command_palette");
  $("#btn-ai").title = t("ai_panel"); $("#btn-settings").title = t("settings");

  MN.chatCarry = MN.chat || MN.chatCarry; MN.chat = null; if (keep.ai) { $("#ai-panel").innerHTML = ""; toggleAI(true); }
  if (keep.side) renderSidebar();
  renderTabs(); renderToolbar(); updateStatus();
  MN.run?.render();
  for (const p of MN.panes) reconfigurePane(p);
}
function setupResizer(gutter, target, key, fromRight, isSplit) {
  const saved = +localStorage.getItem("mn_" + key);
  if (saved) target.style.flexBasis = saved + "px";
  gutter.addEventListener("pointerdown", (e) => {
    e.preventDefault(); gutter.setPointerCapture(e.pointerId); document.body.classList.add("resizing");
    const start = e.clientX, w0 = target.getBoundingClientRect().width;
    const move = (ev) => { const d = ev.clientX - start; const w = Math.max(isSplit ? 220 : 280, Math.min(innerWidth * 0.75, fromRight ? w0 - d : w0 + d)); target.style.flexBasis = w + "px"; target.style.flexGrow = "0"; };
    const up = () => { gutter.removeEventListener("pointermove", move); document.body.classList.remove("resizing"); localStorage.setItem("mn_" + key, Math.round(target.getBoundingClientRect().width)); };
    gutter.addEventListener("pointermove", move); gutter.addEventListener("pointerup", up, { once: true });
  });
  gutter.addEventListener("dblclick", () => { target.style.flexBasis = ""; target.style.flexGrow = ""; localStorage.removeItem("mn_" + key); });
}

function makeTab(o) {
  return { id: o.id || uid(), path: o.path || "", name: o.name || basename(o.path) || untitledName(), kind: o.kind || "text",
    encoding: o.encoding || MN.settings.default_encoding || "utf-8", eol: o.eol || MN.settings.default_eol, bom: !!o.bom,
    mtime: o.mtime || 0, size: o.size || 0, dirty: false, loaded: false, large: false, readOnly: false, langName: o.langName || "",
    mdMode: o.mdMode || "", csvMode: o.csvMode || "", pinned: !!o.pinned, recovery: !!o.recovery, cursor: o.cursor || 0,
    scroll: o.scroll || 0, untitled: !o.path, mdOk: !!o.mdOk, follow: false, savedDoc: null, comp: null, states: {},
    spell: typeof o.spell === "boolean" ? o.spell : undefined,
    indent: o.indent?.manual && typeof o.indent.indent_with_tabs === "boolean" && Number.isInteger(o.indent.tab_size) && o.indent.tab_size >= 1 && o.indent.tab_size <= 8
      ? { tab_size: o.indent.tab_size, indent_with_tabs: o.indent.indent_with_tabs, manual: true } : null };
}
function untitledName() { untitledCounter++; return t("untitled", { n: untitledCounter }); }
function tabById(id) { return MN.tabs.find((x) => x.id === id); }
function pane(id) { return MN.panes.find((p) => p.id === id); }
function activePane() { return pane(MN.active) || MN.panes[0]; }
export function activeTab() { const p = activePane(); return p && tabById(p.tabId); }

function restoreTab(s) {
  if (!s || typeof s !== "object") return false;
  if (!s.path && !s.recovery) return false;
  const tab = makeTab({ ...s, id: s.id });
  if (!s.path) { tab.name = untitledName(); }
  MN.tabs.push(tab);
  renderTabs();
  return true;
}

function newTab(opts = {}) {
  const tab = makeTab({ kind: opts.kind || "text" });
  tab.loaded = true;
  tab.initialText = opts.text || "";
  if (opts.name) tab.name = opts.name;
  MN.tabs.push(tab);
  activate(tab.id);
  if (opts.text) markDirty(tab, true);
  return tab;
}

function renderTabs() {
  const host = $("#tabs"); if (!host) return;
  const active = activePane()?.tabId, other = MN.panes.find((p) => p.id !== MN.active)?.tabId;
  host.innerHTML = MN.tabs.map((tab) => `<div class="tab ${tab.id === active ? "active" : ""} ${tab.id === other ? "other" : ""} ${tab.dirty ? "dirty" : ""} ${tab.pinned ? "pinned" : ""}" role="tab" draggable="true" data-id="${tab.id}" title="${esc(tab.path || tab.name)}" aria-selected="${tab.id === active}">
      ${icon(kindIcon(tab.kind, tab.langName && tab.kind === "text" && tab.langName !== "Plain text"))}<span class="tab-name">${esc(tab.name)}</span>
      <button class="tab-x" data-x="${tab.id}" aria-label="${esc(t("close_tab"))}">${tab.pinned ? icon("pin") : icon("close")}</button><span class="tab-dot"></span></div>`).join("");
  host.querySelectorAll(".tab").forEach((el) => {
    el.addEventListener("pointerdown", (e) => { if (e.button === 0 && !e.target.closest(".tab-x")) activate(el.dataset.id); });
    el.addEventListener("auxclick", (e) => { if (e.button === 1) { e.preventDefault(); closeTab(el.dataset.id); } });
    el.addEventListener("contextmenu", (e) => { e.preventDefault(); tabMenu(el.dataset.id, { x: e.clientX, y: e.clientY }); });
    el.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/mn-tab", el.dataset.id); el.classList.add("dragging"); });
    el.addEventListener("dragend", () => el.classList.remove("dragging"));
    el.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/mn-tab")) { e.preventDefault(); el.classList.add("drop"); } });
    el.addEventListener("dragleave", () => el.classList.remove("drop"));
    el.addEventListener("drop", (e) => {
      el.classList.remove("drop"); const id = e.dataTransfer.getData("text/mn-tab"); if (!id || id === el.dataset.id) return;
      e.preventDefault(); const from = MN.tabs.findIndex((x) => x.id === id), to = MN.tabs.findIndex((x) => x.id === el.dataset.id);
      const [moved] = MN.tabs.splice(from, 1); MN.tabs.splice(to, 0, moved); renderTabs(); saveSession();
    });
  });
  host.querySelectorAll(".tab-x").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); closeTab(b.dataset.x); }));
  host.querySelector(".tab.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function tabMenu(id, at) {
  const tab = tabById(id);
  showMenu([
    { label: t("close_tab"), icon: "close", kbd: kbd("Ctrl+W"), run: () => closeTab(id) },
    { label: t("close_others"), run: () => closeMany(MN.tabs.filter((x) => x.id !== id && !x.pinned)) },
    { label: t("close_right"), run: () => closeMany(MN.tabs.slice(MN.tabs.indexOf(tab) + 1).filter((x) => !x.pinned)) },
    { label: t("close_saved"), run: () => closeMany(MN.tabs.filter((x) => !x.dirty && !x.pinned)) },
    "-",
    { label: tab.pinned ? t("unpin") : t("pin"), icon: "pin", run: () => { tab.pinned = !tab.pinned; renderTabs(); saveSession(); } },
    { label: t("open_in_split"), icon: "split", run: () => { toggleSplit(true); activate(id, "right"); } },
    "-",
    { label: t("copy_path"), icon: "copy", disabled: !tab.path, run: () => copyText(tab.path).then(() => toast(t("copied"), { kind: "ok" })) },
    { label: t("reveal"), icon: "folderOpen", disabled: !tab.path, run: () => api("reveal", { path: tab.path }) },
    { label: t("rename"), icon: "fileText", disabled: !tab.path, run: () => renameTab(tab) },
  ], at);
}
function tabList(anchor) {
  showMenu(MN.tabs.map((x) => ({ label: (x.dirty ? "● " : "") + x.name, hint: x.path ? dirname(x.path) : "", icon: kindIcon(x.kind), checked: x.id === activePane()?.tabId ? true : undefined, run: () => activate(x.id) })), anchor, { alignRight: true, cls: "tablist" });
}

async function activate(id, paneId) {
  const tab = tabById(id); if (!tab) return;
  const p = paneId ? pane(paneId) : activePane();
  if (!p) return;
  MN.active = p.id;
  if (p.tabId && p.tabId !== id) stashPane(p);
  p.tabId = id;
  if (!tab.loaded || tab.loading) {
    try { await ensureLoaded(tab); }
    catch (e) {
      toast(e.status === 404 ? t("file_not_found", { name: tab.name }) : t("open_failed") + ": " + e.message, { kind: "error" });
      if (p.tabId === id) p.tabId = null;
      await closeTab(id, { force: true });
      return;
    }
    if (p.tabId !== id) return;
  }
  showInPane(p, tab);
  renderTabs(); renderToolbar(); updateStatus(); updateTitle(); renderSidebar(); saveSession();
  $$(".pane").forEach((el) => el.classList.toggle("focused", el.dataset.pane === MN.active && MN.panes.length > 1));
}

async function closeTab(id, opts = {}) {
  const tab = tabById(id); if (!tab) return true;
  if (tab.dirty && !opts.force) {
    activate(id);
    const r = await modal({ title: t("unsaved_title"), body: `<p>${esc(t("unsaved_text", { name: tab.name }))}</p>`,
      buttons: [{ label: t("dont_save"), value: "discard", danger: true, left: true }, { label: t("cancel"), value: null }, { label: t("save"), value: "save", primary: true }] });
    if (!r) return false;
    if (r === "save" && !(await saveTab(tab))) return false;
  }
  if (tab.path) MN.closed.push(tab.path);
  api("recovery?id=" + tab.id, undefined, { method: "DELETE", allowError: true }).catch(() => {});
  for (const p of MN.panes) {
    p.states?.delete(tab.id);
    if (p.tabId === id) p.tabId = null;
  }
  tab.comp?.destroy?.(); tab.doc?.destroy(); tab.preview?.destroy?.(); tab.box?.remove();
  const idx = MN.tabs.indexOf(tab);
  MN.tabs.splice(idx, 1);
  for (const p of MN.panes) {
    if (!p.tabId) {
      const next = MN.tabs[Math.min(idx, MN.tabs.length - 1)];
      if (next) { activate(next.id, p.id); }
      else if (p.id === "right") toggleSplit(false);
      else { newTab(); }
    }
  }
  renderTabs(); saveSession();
  return true;
}
async function closeMany(tabs) { for (const tab of tabs.slice()) if (!(await closeTab(tab.id))) break; }
async function reopenClosed() { const p = MN.closed.pop(); if (p) openPath(p); }

function makePane(id, host) {
  const p = { id, host, tabId: null, view: null, states: new Map(), mdPreview: null };
  host.innerHTML = `<div class="pane-body"><div class="editor-host"></div><div class="side-host" hidden></div><div class="comp-host"></div></div>`;
  p.editorHost = $(".editor-host", host); p.sideHost = $(".side-host", host); p.compHost = $(".comp-host", host);
  host.addEventListener("pointerdown", () => { if (MN.active !== id) { MN.active = id; renderTabs(); renderToolbar(); updateStatus(); updateTitle(); $$(".pane").forEach((el) => el.classList.toggle("focused", el.dataset.pane === id && MN.panes.length > 1)); } }, true);
  return p;
}
function isTextTab(tab) { return TEXT_KINDS.has(tab.kind); }

const isCodeTab = (tab) => tab.kind === "json" || tab.kind === "html" || (tab.kind === "text" && !!tab.langName && tab.langName !== "Plain text" && tab.langName !== "Markdown");
function mdModeOf(tab) {
  const mode = tab.mdMode || MN.settings.markdown_preview || "live";
  return mode === "live" && ((docOf(tab)?.length || tab.size || 0) > LIVE_MAX || tab.large) ? "off" : mode;
}
function spellOn(tab) { return tab.spell ?? !!MN.settings.spell_check; }
function spellMode(tab) { return tab.kind === "markdown" || (tab.kind === "text" && tab.langName === "Markdown") ? "markdown" : isCodeTab(tab) || tab.kind === "csv" || tab.kind === "log" || tab.kind === "notebook" || tab.kind === "binary" ? (isCodeTab(tab) ? "code" : "") : "text"; }
function r33Extensions(tab) {
  if (tab.large) return [];
  const ext = [];
  if (tab.kind === "markdown" && mdModeOf(tab) === "live") {
    ext.push(liveMarkdown({ getBase: () => ({ dir: tab.path ? dirname(tab.path) : "", sep: MN.sep }), openLink: (href) => openMdLink(tab, href) }));
  }
  const sm = spellMode(tab);
  if (sm && spellOn(tab)) ext.push(spellExtension(sm));
  if (isCodeTab(tab) && MN.settings.code_autocomplete) ext.push(codeComplete(() => langKey(tab.langName, tab.path || tab.name), true));
  return ext;
}
function openMdLink(tab, href) {
  if (!href) return;
  if (/^(https?:|mailto:)/i.test(href)) { api("external", { url: href }); return; }
  if (href.startsWith("#")) return;
  if (!tab.path) return;
  let p = href; try { p = decodeURIComponent(href); } catch {  }
  p = p.split("#")[0];
  if (!/^([a-zA-Z]:[\\/]|\/|\\\\)/.test(p)) p = dirname(tab.path) + MN.sep + p.replace(/^\.[\\/]/, "").replace(/[\\/]/g, MN.sep);
  openPath(p);
}

function spellMenu(view, e) {
  const tab = viewTab(view); if (!tab || !spellOn(tab)) return false;
  const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
  if (pos == null) return false;
  const w = spellWordAt(view, pos);
  if (!w) return false;
  const at = { x: e.clientX, y: e.clientY };
  const r = view.state.selection.main;
  if (r.empty || r.from > w.to || r.to < w.from) view.dispatch({ selection: { anchor: w.from, head: w.to } });
  editorMenu(at, [{ header: t("spell_loading") }]);
  spellSuggestions(w.word).then((list) => {
    if (view.state.sliceDoc(w.from, w.to) !== w.word) return;
    const open = [...document.querySelectorAll(".menu")].some((m) => m.textContent.includes(t("spell_loading")));
    if (!open) return;
    const items = list.length ? list.map((s) => ({ label: s, cls: "spell-sugg", run: () => { view.dispatch({ changes: { from: w.from, to: w.to, insert: s }, selection: { anchor: w.from + s.length }, userEvent: "input.spell" }); view.focus(); } }))
      : [{ label: t("spell_no_suggestions"), disabled: true }];
    items.push({ label: t("spell_add"), icon: "plus", run: () => addToDictionary(w.word).then(() => toast(t("spell_added", { word: w.word }), { kind: "ok", ms: 1600 })) },
      { label: t("spell_ignore"), icon: "eye", run: () => ignoreInDoc(view, w.word) }, "-");
    editorMenu(at, items);
  });
  return true;
}
function viewTab(view) { return tabById(view?.__tab) || activeTab(); }
function toggleSpell(tab) {
  if (!tab) return;
  tab.spell = !spellOn(tab); tab.cfgVersion = -1;
  for (const p of MN.panes) if (p.tabId === tab.id) reconfigureView(p, tab);
  updateStatus(); saveSession();
}

function editorOpts(tab) {
  return {
    settings: MN.settings, indent: tab.indent, large: tab.large, size: tab.size, kind: tab.kind, readOnly: tab.readOnly, noWrap: !!tab.longLines,
    ghost: MN.settings.ai_autocomplete ? (view) => requestGhost(view, aiCtx(), setGhost) : null,
    keys: [{ key: "Mod-Enter", run: () => { aiInline(); return true; } }],
    onUpdate: (u) => onEditorUpdate(tab, u),
    extraConfig: () => r33Extensions(tab),
    domEvents: {
      contextmenu: (e, view) => { e.preventDefault(); spellMenu(view, e) || editorMenu({ x: e.clientX, y: e.clientY }); return true; },
      scroll: () => syncPreview(tab),
    },
  };
}
function stateFor(p, tab) {
  let st = p.states.get(tab.id);
  if (!st) {
    const other = MN.panes.find((q) => q !== p && q.states.get(tab.id));
    const doc = other ? currentState(other, tab).doc : (tab.initialText ?? "");
    if (!tab.indent?.manual) tab.indent = detectIndentation(doc, MN.settings);
    st = createState(doc, { ...editorOpts(tab), selection: other ? undefined : { anchor: Math.min(tab.cursor || 0, doc.length) } });
    delete tab.initialText;
    p.states.set(tab.id, st);
    if (!tab.savedDoc) tab.savedDoc = st.doc;
    applyLanguage(tab, p);
  }
  return st;
}

function currentState(p, tab) { return p.view && p.tabId === tab.id && p.view.__tab === tab.id && !p.editorHost.hidden ? p.view.state : p.states.get(tab.id); }
function stashPane(p) {
  const tab = tabById(p.tabId);
  if (tab && p.view && isTextTab(tab) && p.view.__tab === tab.id && !p.editorHost.hidden) {
    p.states.set(tab.id, p.view.state);
    tab.scroll = p.view.scrollDOM.scrollTop; tab.cursor = p.view.state.selection.main.head;
  }
  if (tab?.box) tab.box.hidden = true;
}
function showInPane(p, tab) {

  p.compHost.querySelectorAll(":scope > .comp-box").forEach((b) => { if (b !== tab.box) b.hidden = true; });
  p.sideHost.hidden = true; p.sideHost.innerHTML = ""; p.mdPreview = null;
  p.host.classList.remove("md-split", "md-only", "csv-table", "html-split");
  p.jsonTree?.destroy(); p.jsonTree = null;
  if (isTextTab(tab) && !viewMode(tab)) {
    p.compHost.hidden = true; p.editorHost.hidden = false;
    const st = stateFor(p, tab);
    if (!p.view) {
      p.view = new CM.EditorView({ state: st, parent: p.editorHost, dispatchTransactions: (trs, view) => dispatchSynced(p, trs, view) });
    } else { p.view.setState(st); }
    p.view.__tab = tab.id;
    if (tab.cfgVersion !== cfgVersion) reconfigureView(p, tab);
    requestAnimationFrame(() => { p.view.scrollDOM.scrollTop = tab.scroll || 0; });
    if (tab.kind === "markdown") setupMdPreview(p, tab);
    if (tab.kind === "html" && tab.htmlPreview) setupHtmlPreview(p, tab);
    if (tab.kind === "json" && tab.jsonTree) setupJsonTree(p, tab);
    if (MN.active === p.id) setTimeout(() => p.view.focus(), 0);
  } else {
    p.editorHost.hidden = true; p.compHost.hidden = false;
    if (!tab.box || tab.box.parentElement !== p.compHost) buildComponent(p, tab);
    tab.box.hidden = false;
    if (tab.kind === "slides" && MN.active === p.id) setTimeout(() => tab.comp?.focusStage?.(), 0);
    if (tab.kind === "csv") p.host.classList.add("csv-table");
  }
}
function dispatchSynced(p, trs, view) {
  view.update(trs);
  const tab = tabById(view.__tab); if (!tab) return;
  const changes = trs.filter((tr) => !tr.changes.empty && !tr.annotation(CM.Transaction.remote));
  if (!changes.length) return;
  for (const q of MN.panes) {
    if (q === p) continue;
    for (const tr of changes) {
      if (q.view && q.view.__tab === tab.id) q.view.dispatch({ changes: tr.changes, annotations: CM.Transaction.remote.of(true) });
      else if (q.states.get(tab.id)) q.states.set(tab.id, q.states.get(tab.id).update({ changes: tr.changes, annotations: CM.Transaction.remote.of(true) }).state);
    }
  }
}
let cfgVersion = 0;
function reconfigureView(p, tab) {
  if (!p.view) return;
  p.view.dispatch({ effects: [configComp.reconfigure(buildConfig(editorOpts(tab))), readOnlyComp.reconfigure(tab.readOnly ? [CM.EditorState.readOnly.of(true)] : [])] });
  tab.cfgVersion = cfgVersion;
}
function reconfigurePane(p) {
  cfgVersion++;
  const tab = p && tabById(p.tabId);
  if (tab && p.view && p.view.__tab === tab.id) reconfigureView(p, tab);
}
async function applyLanguage(tab, onlyPane) {
  if (tab.large && !tab.langName) return;
  const info = tab.langName ? languageByName(tab.langName) : languageFor(tab.name, tab.kind);
  tab.langName = info?.name || "Plain text";
  const support = await loadLanguage(info);
  tab.cfgVersion = -1;
  for (const p of MN.panes) {
    if (onlyPane && p !== onlyPane) continue;
    const shown = p.view && p.view.__tab === tab.id && !p.editorHost.hidden;
    if (shown) p.view.dispatch({ effects: [langComp.reconfigure(support), configComp.reconfigure(buildConfig(editorOpts(tab)))] });
    const st = p.states.get(tab.id);
    if (st && !shown) p.states.set(tab.id, st.update({ effects: langComp.reconfigure(support) }).state);
  }
  updateStatus();
}

function onEditorUpdate(tab, u) {
  if (u.docChanged) {
    scheduleDirty(tab);
    if (tab.kind === "markdown") for (const p of MN.panes) if (p.tabId === tab.id) p.mdPreview?.render();
    if (tab.kind === "html") for (const p of MN.panes) if (p.tabId === tab.id) p.htmlPreview?.render();
    if (tab.kind === "json") for (const p of MN.panes) if (p.tabId === tab.id) p.jsonTree?.renderSoon?.();
    scheduleRecovery(tab);
    if (MN.settings.autosave === "delay" && tab.path) scheduleAutosave(tab);
    outlineChanged();
  }
  if (u.docChanged || u.selectionSet) updateStatusSoon();
}
const dirtyTimers = new Map();
function scheduleDirty(tab) {
  if (dirtyTimers.has(tab.id)) return;
  dirtyTimers.set(tab.id, setTimeout(() => { dirtyTimers.delete(tab.id); const d = !docOf(tab)?.eq(tab.savedDoc); markDirty(tab, d); }, tab.large ? 800 : 120));
}
function markDirty(tab, dirty) {
  if (tab.dirty === dirty) return;
  tab.dirty = dirty; renderTabs(); updateTitle(); saveSession();
}
function docOf(tab) {
  for (const p of MN.panes) { const st = currentState(p, tab); if (st) return st.doc; }
  return null;
}
export function textOf(tab) {
  if (DOC_KINDS.has(tab.kind)) return tab.doc ? tab.doc.text() : "";
  if (tab.kind === "sheet" || tab.kind === "book" || tab.kind === "slides") return tab.comp?.text ? tab.comp.text() : "";
  const d = docOf(tab); return d ? d.toString() : String(tab.initialText || "");
}
function viewOf(tab) { const p = MN.panes.find((q) => q.view && q.view.__tab === tab.id && q.tabId === tab.id && !q.editorHost.hidden); return p?.view || null; }

function setupMdPreview(p, tab) {
  const mode = mdModeOf(tab);
  if (mode === "off" || mode === "live") return;
  p.sideHost.hidden = false;
  p.mdPreview = new MarkdownPreview(p.sideHost, () => ({ path: tab.path, sep: MN.sep, view: currentState(p, tab) ? { state: currentState(p, tab) } : null, state: currentState(p, tab) }));
  p.mdPreview.onReveal = (line) => { const v = p.view; const l = v.state.doc.line(Math.min(v.state.doc.lines, line + 1)); v.dispatch({ selection: { anchor: l.from }, scrollIntoView: true }); v.focus(); };
  p.host.classList.add(mode === "preview" ? "md-only" : "md-split");
  p.mdPreview._render();
}
function setupHtmlPreview(p, tab) {
  p.sideHost.hidden = false; p.host.classList.add("html-split");
  p.htmlPreview = new HtmlPreview(p.sideHost, () => currentState(p, tab).doc.toString());
}
function setupJsonTree(p, tab) {
  p.sideHost.hidden = false; p.host.classList.add("html-split");
  p.jsonTree = new JsonTree(p.sideHost, () => currentState(p, tab).doc.toString());
  p.jsonTree.renderSoon = debounce(() => p.jsonTree?.render(), 400);
}
function syncPreview(tab) {
  for (const p of MN.panes) {
    if (p.tabId !== tab.id || !p.mdPreview || !p.view) continue;
    const top = p.view.lineBlockAtHeight(p.view.scrollDOM.scrollTop);
    p.mdPreview.syncTo(p.view.state.doc.lineAt(top.from).number - 1);
  }
}
function setMdMode(mode) {
  const tab = activeTab(); if (!tab || tab.kind !== "markdown") return;
  tab.mdMode = mode; tab.cfgVersion = -1; const p = activePane(); stashPane(p); showInPane(p, tab); renderToolbar(); saveSession();
}

function buildComponent(p, tab) {
  tab.box?.remove();
  const box = document.createElement("div"); box.className = "comp-box"; tab.box = box; p.compHost.append(box);
  if (DOC_KINDS.has(tab.kind)) {
    tab.doc = new DocEditor(box, tab, { spellcheck: MN.settings.spell_check, zoom: MN.settings.zoom,
      onChange: () => { markDirty(tab, true); scheduleRecovery(tab); updateStatusSoon(); outlineChanged(); },
      onSelection: () => updateStatusSoon(),
      onImage: (insert) => pickImage(insert) });
  } else if (tab.kind === "pdf") {
    if (tab.comp instanceof PdfView) { box.append(tab.comp.el); return; }
    tab.comp = new PdfView(box, tab);
    tab.comp.onExtract = async () => { const text = await tab.comp.text(); newTab({ text, name: tab.name.replace(/\.pdf$/i, "") + ".txt" }); };
    tab.comp.onExport = (fmt) => exportPdfText(tab, fmt);
    tab.comp.onAsk = () => { toggleAI(true); MN.chat?.focus(t("ai_pdf_question")); };
    tab.comp.onChange = () => markDirty(tab, tab.comp.dirty);
    tab.comp.onSave = () => saveTab(tab);
  } else if (tab.kind === "slides") {
    if (tab.comp instanceof SlidesView) { box.append(tab.comp.el); return; }
    tab.comp = new SlidesView(box, tab, slidesHooks(tab));
  } else if (tab.kind === "image") {

    if (tab.comp?.isPhoto) { box.append(tab.comp.el); return; }
    tab.comp = { destroy() {}, loading: true };
    import("./photo/editor.js").then(({ PhotoEditor }) => {
      if (tab.box !== box) return;
      tab.comp = new PhotoEditor(box, tab, photoHooks(tab)); tab.comp.isPhoto = true;
    }).catch((e) => { console.error(e); if (tab.box === box) tab.comp = new ImageView(box, tab); });
  } else if (tab.kind === "csv") {

    const st = stateFor(p, tab);
    tab.comp = new CsvTable(box, st.doc.toString(), { delim: /\.(tsv|tab)$/i.test(tab.path || tab.name) ? "\t" : undefined,
      onEdit: (text) => {
        for (const q of MN.panes) { const cur = q.states.get(tab.id); if (cur) q.states.set(tab.id, cur.update({ changes: { from: 0, to: cur.doc.length, insert: text } }).state); }
        markDirty(tab, !docOf(tab)?.eq(tab.savedDoc)); scheduleRecovery(tab);
      } });
  } else if (tab.kind === "notebook") {
    const st = stateFor(p, tab);
    tab.comp = new NotebookView(box, () => (currentState(p, tab) || st).doc.toString());
  } else if (tab.kind === "sheet") {
    if (tab.comp instanceof SheetGrid) { box.append(tab.comp.el); return; }
    tab.comp = new SheetGrid(box, tab, { onChange: (dirty) => markDirty(tab, dirty) });
  } else if (tab.kind === "book") {
    tab.comp = new BookView(box, tab);
  } else {
    box.innerHTML = `<div class="empty-state">${icon("warn", "big")}<h3>${esc(t(tab.kind === "legacy" ? "legacy_title" : "binary_title"))}</h3>
      <p>${esc(t(tab.kind === "legacy" ? "legacy_text" : "binary_text", { ext: extname(tab.path) }))}</p>
      <div class="row"><button class="btn" data-a="text">${esc(t("open_as_text"))}</button>${tab.path ? `<button class="btn ghost" data-a="reveal">${esc(t("reveal"))}</button>` : ""}</div></div>`;
    box.querySelector("[data-a=text]").onclick = () => reopenAsText(tab);
    box.querySelector("[data-a=reveal]")?.addEventListener("click", () => api("reveal", { path: tab.path }));
  }
}

function photoHooks(tab) {
  return {
    onDirty: (d) => markDirty(tab, d),
    fileDialog: (mode, o) => fileDialog(mode, o),
    newImage: () => newImageTab(), open: () => openDialog(), save: () => saveTab(tab),
    newTextTab: (text, name) => newTab({ text, name }),
    newImageFromCanvas: (canvas, name) => { const t2 = newImageTab(name); t2.photoCanvas = canvas; },
    afterSaveAs: (path, r) => { Object.assign(tab, { path, name: basename(path), untitled: false, mtime: r.mtime || 0, size: r.size || 0 }); addRecent(path); renderTabs(); updateTitle(); saveSession(); },
  };
}
function newImageTab(name) { return newTab({ kind: "image", name: name || t("untitled", { n: ++untitledCounter }) + ".png" }); }
async function pickImage(insert) {
  const paths = await fileDialog("open", { filters: [t("filter_images") + " (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp;*.svg)"], multiple: false });
  if (!paths?.[0]) return;
  const res = await fetch(`/api/asset?path=${encodeURIComponent(paths[0])}&t=${encodeURIComponent(sessionStorage.getItem("mn_token") || "")}`);
  if (!res.ok) { toast(t("image_failed"), { kind: "error" }); return; }
  const blob = await res.blob();
  if (blob.size > 15 * 1048576) { toast(t("image_too_big"), { kind: "error" }); return; }
  const reader = new FileReader(); reader.onload = () => insert(reader.result); reader.readAsDataURL(blob);
}

async function openPath(path, opts = {}) {
  const existing = MN.tabs.find((x) => x.path && x.path.toLowerCase() === String(path).toLowerCase());
  if (existing) { if (opts.activate !== false) activate(existing.id); return existing; }
  const tab = makeTab({ path });
  MN.tabs.push(tab);
  renderTabs();
  try { await ensureLoaded(tab); }
  catch (e) {
    MN.tabs.splice(MN.tabs.indexOf(tab), 1); renderTabs();
    toast(e.status === 404 ? t("file_not_found", { name: basename(path) }) : t("open_failed") + ": " + e.message, { kind: "error" });
    MN.recent = MN.recent.filter((r) => r !== path);
    return null;
  }
  addRecent(path);

  const blank = MN.tabs.length !== 2 ? [] : MN.tabs.filter((x) => x !== tab && !x.path && !x.dirty && !x.pinned && !x.recovery && x.loaded && !textOf(x));
  if (opts.activate !== false) await activate(tab.id);
  for (const x of blank) if (MN.tabs.length > 1) await closeTab(x.id, { force: true });
  return tab;
}

function ensureLoaded(tab) {
  if (!tab.loading) tab.loading = loadTab(tab).catch((e) => { tab.loaded = false; throw e; }).finally(() => { tab.loading = null; });
  return tab.loading;
}
async function loadTab(tab, opts = {}) {
  tab.loaded = true;
  if (!tab.path) {
    try { const r = await api("recovery?id=" + tab.id); tab.initialText = r.text; tab.kind = r.meta?.kind && TEXT_KINDS.has(r.meta.kind) ? r.meta.kind : tab.kind; tab.savedDoc = CM.Text.of([""]); setTimeout(() => markDirty(tab, true), 50); }
    catch { tab.initialText = ""; }
    return;
  }
  const info = await api("open", { path: tab.path, encoding: opts.encoding || "" }).catch(async (e) => {
    if (tab.recovery && e.status === 404) return null; throw e;
  });
  if (!info) {
    const r = await api("recovery?id=" + tab.id).catch(() => null);
    tab.initialText = r?.text || ""; tab.savedDoc = CM.Text.of([""]); setTimeout(() => markDirty(tab, true), 50);
    toast(t("file_missing_recovered", { name: tab.name }), { kind: "error" });
    return;
  }
  Object.assign(tab, { kind: opts.forceText ? (info.kind === "binary" || info.kind === "legacy" ? "text" : info.kind) : info.kind, mtime: info.mtime, size: info.size, readOnly: !!info.readonly && false, name: info.name });
  tab.fileReadOnly = !!info.readonly;
  if (DOC_KINDS.has(tab.kind)) {
    tab.html = info.html; tab.meta = info.meta;
    if (tab.recovery) { const r = await api("recovery?id=" + tab.id).catch(() => null); if (r?.text && r.meta?.kind === "html") { tab.html = r.text; setTimeout(() => markDirty(tab, true), 50); } }
    return;
  }
  if (tab.kind === "sheet") { tab.sheets = info.sheets || []; tab.sheetEditable = !!info.editable && /\.(xlsx|xlsm)$/i.test(tab.path || ""); tab.looks = info.looks || []; tab.plainText = null; return; }
  if (tab.kind === "book") { tab.book = { html: info.html, title: info.title, author: info.author, chapters: info.chapters }; return; }
  if (["pdf", "image", "legacy", "slides"].includes(tab.kind) || (tab.kind === "binary" && !opts.forceText)) return;
  tab.encoding = info.encoding; tab.webEncoding = info.web_encoding || ""; tab.bom = info.bom; tab.eol = info.eol === "mixed" ? info.eol_major || MN.settings.default_eol : info.eol; tab.mixedEol = info.eol === "mixed";
  tab.large = info.size > MN.settings.large_file_mb * 1048576;
  if (info.damaged) toast(t("utf8_damaged", { n: info.damaged }), { ms: 8000 });
  let doc;
  if (info.text === undefined) {
    if (info.size > 1024 * 1048576) throw new Error(t("too_big"));
    const overlay = tab.large ? showLoading(t("opening_large", { size: fmtBytes(info.size) })) : null;
    try {

      const r = await readText(rawUrl(tab.path), info.web_encoding || "utf-8",
        overlay ? (f) => { const s = overlay.querySelector("span"); if (s) s.textContent = t("opening_large", { size: fmtBytes(info.size) }) + " " + Math.round(f * 100) + "%"; } : null, info.size);
      doc = r.doc; tab.longLines = r.maxLine > LONG_LINE;
    } finally { overlay?.remove(); }
  } else {
    doc = CM.Text.of(info.text.split(/\r\n|\r|\n/));
    tab.longLines = info.text.length > LONG_LINE && longestLine(doc, LONG_LINE + 1) > LONG_LINE;
  }
  tab.savedDoc = doc;
  tab.initialText = doc;
  if (tab.recovery) {
    const r = await api("recovery?id=" + tab.id).catch(() => null);
    if (r && typeof r.text === "string" && r.text !== doc.toString()) { tab.initialText = r.text; setTimeout(() => markDirty(tab, true), 50); }
  }
  if (!tab.indent?.manual) tab.indent = detectIndentation(tab.initialText, MN.settings);
  if (tab.kind === "csv" && !tab.csvMode) tab.csvMode = info.size < 32 * 1048576 ? "table" : "text";
  if (tab.kind === "notebook" && !tab.csvMode) tab.csvMode = info.size < 64 * 1048576 ? "table" : "text";
  if (tab.kind === "log") tab.follow = false;
}
function showLoading(text) {
  const el = document.createElement("div"); el.className = "loading-overlay"; el.innerHTML = `<div class="spinner"></div><span>${esc(text)}</span>`;
  document.body.append(el); return el;
}
async function reopenAsText(tab, encoding) {
  const p = MN.panes.find((q) => q.tabId === tab.id) || activePane();
  for (const q of MN.panes) q.states.delete(tab.id);
  tab.box?.remove(); tab.box = null; tab.comp = null; tab.doc = null; tab.savedDoc = null;
  await loadTab(tab, { forceText: true, encoding });
  if (tab.kind === "binary" || tab.kind === "legacy") tab.kind = "text";
  tab.cfgVersion = -1;
  showInPane(p, tab); applyLanguage(tab); renderTabs(); renderToolbar(); updateStatus();
}
async function reloadTab(tab) {
  const p = MN.panes.find((q) => q.tabId === tab.id);
  const sel = p?.view && p.view.__tab === tab.id ? p.view.state.selection.main.head : 0;
  const scroll = p?.view?.scrollDOM.scrollTop || 0;
  if (DOC_KINDS.has(tab.kind)) {
    const info = await api("open", { path: tab.path }); tab.html = info.html; tab.mtime = info.mtime;
    tab.doc?.destroy(); tab.doc = null; tab.box?.remove(); tab.box = null;
    if (p) showInPane(p, tab); markDirty(tab, false); return;
  }
  if (!isTextTab(tab)) { tab.comp?.destroy?.(); tab.box?.remove(); tab.box = null; const info = await api("open", { path: tab.path }); tab.mtime = info.mtime; tab.size = info.size; if (p) showInPane(p, tab); return; }
  await loadTab(tab, { encoding: tab.encoding });
  const text = tab.initialText; delete tab.initialText;
  for (const q of MN.panes) {
    const view = q.view && q.view.__tab === tab.id ? q.view : null;
    if (view) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text }, selection: { anchor: Math.min(sel, text.length) }, effects: configComp.reconfigure(buildConfig(editorOpts(tab))), annotations: CM.Transaction.remote.of(true) });
    else if (q.states.get(tab.id)) q.states.delete(tab.id);
  }
  tab.savedDoc = docOf(tab); markDirty(tab, false);
  if (tab.follow) { const v = viewOf(tab); v?.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true }); }
  else if (p?.view) requestAnimationFrame(() => (p.view.scrollDOM.scrollTop = scroll));
}

async function fileDialog(mode, opts = {}) {
  if (!MN.boot.desktop) {
    const v = await prompt(mode === "save" ? t("save_as") : t("open_file"), t("path_absolute"), opts.filename ? (opts.directory ? opts.directory + MN.sep : "") + opts.filename : "");
    return v ? [v] : [];
  }
  const r = await api("dialog", { mode, directory: opts.directory || "", filename: opts.filename || "", filters: opts.filters || [], multiple: opts.multiple !== false });
  return r.paths || [];
}
const OPEN_FILTERS = () => [
  t("filter_all_supported") + " (*.txt;*.md;*.markdown;*.docx;*.odt;*.rtf;*.pdf;*.pptx;*.odp;*.csv;*.tsv;*.json;*.log;*.xlsx;*.ods;*.epub;*.fb2;*.ipynb;*.xml;*.yaml;*.yml;*.ini;*.html;*.htm;*.css;*.js;*.ts;*.py;*.java;*.c;*.cpp;*.h;*.cs;*.go;*.rs;*.sql;*.sh;*.bat;*.ps1;*.png;*.jpg;*.jpeg;*.gif;*.webp;*.svg;*.bmp;*.ico;*.avif;*.tif;*.tiff;*.psd;*.memuimg;*.heic)",
  t("ph_filter_images") + " (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp;*.ico;*.avif;*.svg;*.tif;*.tiff;*.psd;*.tga;*.heic;*.memuimg)",
  t("filter_text") + " (*.txt;*.log;*.ini;*.cfg;*.conf)", "Markdown (*.md;*.markdown)", t("filter_docs") + " (*.docx;*.odt;*.rtf)", "PDF (*.pdf)", t("filter_slides") + " (*.pptx;*.pptm;*.ppsx;*.potx;*.odp)", t("filter_data") + " (*.csv;*.tsv;*.json;*.xml;*.yaml;*.yml)", t("filter_all") + " (*.*)"];
async function openDialog() {
  const tab = activeTab();
  const paths = await fileDialog("open", { directory: tab?.path ? dirname(tab.path) : "", filters: OPEN_FILTERS() });
  for (const p of paths) await openPath(p);
}
function addRecent(path) { MN.recent = [path, ...MN.recent.filter((r) => r.toLowerCase() !== path.toLowerCase())].slice(0, 30); api("recent", { path }).catch(() => {}); }

async function saveTab(tab, opts = {}) {
  if (!tab || opts.force) return saveTabNow(tab, opts);
  while (tab.saving) await tab.saving.catch(() => {});
  if (opts.silent && !tab.dirty && tab.path) return true;
  const job = saveTabNow(tab, opts); tab.saving = job;
  try { return await job; } finally { if (tab.saving === job) tab.saving = null; }
}
async function saveTabNow(tab, opts = {}) {
  if (!tab) return false;
  if (!tab.path || opts.as) return saveAs(tab);
  let snapshot = null, richSnapshot = null;
  try {
    let r;
    if (DOC_KINDS.has(tab.kind)) {
      if (MN.settings.rich_save_warning && !tab.richOk && (tab.kind === "docx" || tab.kind === "odt") && !opts.silent) {
        const c = await modal({ title: t("rich_save_title"), body: `<p>${esc(t("rich_save_text"))}</p><label class="check"><input type="checkbox" id="rs-dont"> ${esc(t("dont_ask_again"))}</label>`,
          buttons: [{ label: t("cancel"), value: null }, { label: t("save_copy"), value: (b) => ({ v: "copy", d: b.querySelector("#rs-dont").checked }) }, { label: t("save_over"), primary: true, value: (b) => ({ v: "over", d: b.querySelector("#rs-dont").checked }) }] });
        if (!c) return false;
        if (c.d) await saveSettings({ rich_save_warning: false });
        if (c.v === "copy") return saveAs(tab);
        tab.richOk = true;
      }
      richSnapshot = tab.doc ? tab.doc.html() : tab.html;
      r = await api("save_rich", { path: tab.path, html: richSnapshot, format: tab.kind, mtime: opts.force ? 0 : tab.mtime, force: !!opts.force, title: tab.name });
    } else if (isTextTab(tab)) {
      snapshot = docOf(tab);
      if (!snapshot && tab.initialText == null) return true;

      if (tab.mixedEol && !tab.dirty && !opts.force && snapshot && tab.savedDoc && snapshot.eq(tab.savedDoc)) return true;
      const text = snapshot ? docBlob(snapshot) : textOf(tab);
      const q = new URLSearchParams({ path: tab.path, encoding: tab.encoding, eol: tab.eol, bom: tab.bom ? "1" : "0", mtime: opts.force ? "0" : String(tab.mtime || 0), force: opts.force ? "1" : "0",
        trim: MN.settings.trim_trailing_whitespace ? "1" : "0", final_newline: MN.settings.final_newline ? "1" : "0", backup: MN.settings.backup_on_save ? "1" : "0" });
      r = await api("save_text?" + q, text, { raw: true });
      if (r.lost) toast(t("encoding_lost", { n: r.lost, enc: tab.encoding }), { kind: "error", ms: 8000 });
    } else if (tab.kind === "sheet" && tab.comp?.save && tab.sheetEditable) {
      if (!tab.dirty && !opts.force) return true;
      r = await tab.comp.save(tab.path, { mtime: opts.force ? 0 : tab.mtime, force: !!opts.force });
    } else if (tab.kind === "slides" && tab.comp?.save) {
      if (!/\.(pptx|pptm|ppsx|potx)$/i.test(tab.path)) return saveSlidesAs(tab);
      r = await tab.comp.save(tab.path, { mtime: opts.force ? 0 : tab.mtime, force: !!opts.force });
    } else if (tab.kind === "pdf" && tab.comp?.save) {
      if (!tab.comp.dirty) return true;
      r = await tab.comp.save(tab.path, { mtime: opts.force ? 0 : tab.mtime, force: !!opts.force });
    } else if (tab.kind === "image" && tab.comp?.isPhoto) {
      if (!tab.comp.dirty && !opts.force) return true;
      r = await tab.comp.save(tab.path, { mtime: opts.force ? 0 : tab.mtime, force: !!opts.force });
    } else return true;
    if (r.conflict) {
      const c = await modal({ title: t("conflict_title"), body: `<p>${esc(t("conflict_text", { name: tab.name }))}</p>`,
        buttons: [{ label: t("cancel"), value: null }, { label: t("reload_from_disk"), value: "reload" }, { label: t("overwrite"), danger: true, value: "over" }] });
      if (c === "over") return saveTab(tab, { ...opts, force: true });
      if (c === "reload") { await reloadTab(tab); return false; }
      return false;
    }
    tab.mtime = r.mtime; tab.size = r.size;
    tab.savedDoc = snapshot || docOf(tab); tab.mixedEol = false;
    const stillDirty = snapshot ? !!docOf(tab) && !docOf(tab).eq(snapshot)
      : richSnapshot !== null ? (tab.doc ? tab.doc.html() : tab.html) !== richSnapshot
      : tab.kind === "sheet" ? !!tab.comp?.isDirty : !!tab.comp?.dirty;
    markDirty(tab, stillDirty);
    if (!tab.dirty) api("recovery?id=" + tab.id, undefined, { method: "DELETE", allowError: true }).catch(() => {});
    if (!opts.silent) toast(t("saved", { name: tab.name }), { kind: "ok", ms: 1600 });
    if (MN.settings.trim_trailing_whitespace || MN.settings.final_newline) {  }
    updateStatus();
    return true;
  } catch (e) {
    toast(t("save_failed") + ": " + (e.data?.error === "permission_denied" ? t("permission_denied") : e.message), { kind: "error", ms: 9000,
      action: { label: t("save_as"), run: () => saveAs(tab) } });
    return false;
  }
}
async function saveSheetAs(tab) {
  const paths = await fileDialog("save", { directory: dirname(tab.path), filename: basename(tab.path), filters: ["Excel (*.xlsx)"], multiple: false });
  let path = paths?.[0]; if (!path) return false;
  if (!/\.(xlsx|xlsm)$/i.test(path)) path += ".xlsx";
  try {
    const r = await tab.comp.save(tab.path, { target: path, force: true });
    tab.path = r.path || path; tab.name = basename(tab.path); tab.mtime = r.mtime; tab.size = r.size;
    markDirty(tab, !!tab.comp?.isDirty); renderTabs(); updateStatus(); saveSession();
    toast(t("saved", { name: tab.name }), { kind: "ok", ms: 1600 });
    return true;
  } catch (e) { toast(t("save_failed") + ": " + e.message, { kind: "error", ms: 9000 }); return false; }
}
async function savePdfAs(tab) {
  const paths = await fileDialog("save", { directory: tab.path ? dirname(tab.path) : "", filename: basename(tab.path || "document.pdf"), filters: ["PDF (*.pdf)"], multiple: false });
  let path = paths?.[0]; if (!path) return false;
  if (extname(path) !== ".pdf") path += ".pdf";
  const old = tab.path;
  tab.path = path;
  try {
    const r = await tab.comp.save(path, { force: true });
    if (!r.ok) throw new Error(r.error || "save failed");
    Object.assign(tab, { name: basename(path), mtime: r.mtime, size: r.size });
    markDirty(tab, !!tab.comp?.dirty); addRecent(path); renderTabs(); updateTitle(); saveSession();
    toast(t("saved", { name: tab.name }), { kind: "ok", ms: 1600 });
    return true;
  } catch (e) { tab.path = old; toast(t("save_failed") + ": " + e.message, { kind: "error", ms: 9000 }); return false; }
}

async function saveSlidesAs(tab) {
  if (/\.odp$/i.test(tab.path || "")) toast(t("sl_odp_save"), { ms: 5000 });
  const stem = (tab.path ? basename(tab.path) : tab.name).replace(/\.\w+$/, "");
  const paths = await fileDialog("save", { directory: tab.path ? dirname(tab.path) : "", filename: stem + ".pptx", filters: ["PowerPoint (*.pptx)", "PowerPoint Show (*.ppsx)", "PowerPoint Template (*.potx)"], multiple: false });
  let path = paths?.[0]; if (!path) return false;
  if (!/\.(pptx|ppsx|potx|pptm)$/i.test(path)) path = path.replace(/\.(odp|ppt)$/i, "") + ".pptx";
  const old = { path: tab.path, name: tab.name };
  try {
    const r = await tab.comp.save(path, { force: true });
    if (!r.ok) throw new Error(r.error || "save failed");
    Object.assign(tab, { path, name: basename(path), mtime: r.mtime, size: r.size, untitled: false });
    markDirty(tab, !!tab.comp?.dirty); addRecent(path); renderTabs(); updateTitle(); saveSession(); updateStatus();
    toast(t("saved", { name: tab.name }), { kind: "ok", ms: 1600 });
    return true;
  } catch (e) { Object.assign(tab, old); toast(t("save_failed") + ": " + e.message, { kind: "error", ms: 9000 }); return false; }
}
function slidesHooks(tab) {
  return {
    onChange: () => markDirty(tab, !!tab.comp?.dirty),
    saveDialog: async ({ filename, filters }) => (await fileDialog("save", { directory: tab.path ? dirname(tab.path) : "", filename, filters, multiple: false }))?.[0] || null,
    openDocs: () => MN.tabs.filter((x) => x !== tab && x.kind !== "slides" && !["image", "legacy", "binary"].includes(x.kind)).map((x) => ({ id: x.id, name: x.name })),
    docText: async (id) => { const x = tabById(id); if (!x) return ""; if (!x.loaded) await ensureLoaded(x).catch(() => {}); return textOf(x) || (x.html ? x.html.replace(/<[^>]+>/g, " ") : ""); },
  };
}
async function newPresentation(ask = true) {
  const opts = ask ? await newDeckDialog() : {};
  if (!opts) return null;
  const tab = makeTab({ kind: "slides" });
  tab.loaded = true; tab.newDeck = opts; tab.name = t("untitled", { n: ++untitledCounter }) + ".pptx";
  if (opts.ai) tab.pendingAI = opts.ai;
  MN.tabs.push(tab);
  await activate(tab.id);
  markDirty(tab, true);
  return tab;
}
async function exportPdfText(tab, fmt) {
  try {
    const text = fmt === "markdown" ? (await api("pdf_text", { path: tab.path, format: "markdown" })).text : await tab.comp.text();
    newTab({ text, kind: fmt === "markdown" ? "markdown" : "text", name: tab.name.replace(/\.pdf$/i, "") + (fmt === "markdown" ? ".md" : ".txt") });
  } catch (e) { toast(t("export_failed") + ": " + e.message, { kind: "error" }); }
}
async function saveAs(tab) {
  if (tab.kind === "slides" && tab.comp?.save) return saveSlidesAs(tab);
  if (tab.kind === "pdf" && tab.comp?.save) return savePdfAs(tab);
  if (tab.kind === "sheet" && tab.sheetEditable && tab.comp?.save) return saveSheetAs(tab);
  if (tab.kind === "image") return tab.comp?.isPhoto ? tab.comp.saveAs(false) : false;
  const isDoc = DOC_KINDS.has(tab.kind);
  const suggested = tab.path ? basename(tab.path) : (tab.kind === "markdown" ? tab.name.replace(/\.\w+$/, "") + ".md" : tab.name.replace(/\.\w+$/, "") + (isDoc ? ".docx" : ".txt"));
  const filters = isDoc ? ["Word (*.docx)", "OpenDocument (*.odt)", "Rich Text (*.rtf)"] : tab.kind === "markdown" ? ["Markdown (*.md)", t("filter_text") + " (*.txt)", t("filter_all") + " (*.*)"] : [t("filter_text") + " (*.txt)", "Markdown (*.md)", t("filter_all") + " (*.*)"];
  const paths = await fileDialog("save", { directory: tab.path ? dirname(tab.path) : "", filename: suggested, filters, multiple: false });
  let path = paths?.[0]; if (!path) return false;
  let ext = extname(path);
  if (!ext) { path += isDoc ? ".docx" : tab.kind === "markdown" ? ".md" : ".txt"; ext = extname(path); }
  if (isDoc && ![".docx", ".odt", ".rtf"].includes(ext)) { await exportTo(ext.slice(1), path); return true; }
  if (!isDoc && [".docx", ".odt", ".rtf", ".pdf", ".html", ".htm"].includes(ext)) { await exportTo(ext.replace(".htm", ".html").slice(1).replace("htmll", "html"), path); return true; }
  const old = { path: tab.path, name: tab.name, kind: tab.kind };
  tab.path = path; tab.name = basename(path); tab.untitled = false; tab.mtime = 0;
  if (isDoc) tab.kind = ext.slice(1);
  else if (ext === ".md" || ext === ".markdown") tab.kind = "markdown";
  else if (tab.kind === "markdown" && ext !== ".md") tab.kind = "text";
  tab.richOk = true;
  const ok = await saveTab(tab, { force: true });
  if (!ok) Object.assign(tab, old);
  else { addRecent(path); tab.langName = ""; applyLanguage(tab); const p = activePane(); stashPane(p); showInPane(p, tab); }
  renderTabs(); renderToolbar(); updateTitle(); saveSession();
  return ok;
}
async function saveAll(silent) { for (const tab of MN.tabs) if (tab.dirty && tab.path) await saveTab(tab, { silent }); }
const autosaveTimers = new Map();
function scheduleAutosave(tab) { clearTimeout(autosaveTimers.get(tab.id)); autosaveTimers.set(tab.id, setTimeout(() => { if (tab.dirty) saveTab(tab, { silent: true }); }, MN.settings.autosave_delay)); }
async function renameTab(tab) {
  const name = await prompt(t("rename"), t("new_name"), tab.name); if (!name || name === tab.name) return;
  const target = dirname(tab.path) + MN.sep + name;
  try { const r = await api("rename", { from: tab.path, to: target }); tab.path = target; tab.name = name; tab.mtime = r.mtime; addRecent(target); renderTabs(); updateTitle(); saveSession(); }
  catch (e) { toast(e.status === 409 ? t("file_exists") : e.message, { kind: "error" }); }
}

const recoveryTimers = new Map();
function scheduleRecovery(tab) {
  if (!MN.settings.recovery) return;
  clearTimeout(recoveryTimers.get(tab.id));
  recoveryTimers.set(tab.id, setTimeout(() => writeRecovery(tab), tab.large ? 60000 : 1500));
}
async function writeRecovery(tab) {
  if (!tab.dirty) return;
  const isDoc = DOC_KINDS.has(tab.kind);
  const d = isDoc ? null : docOf(tab);
  const body = isDoc ? (tab.doc ? tab.doc.html() : tab.html || "") : d ? docBlob(d) : textOf(tab);
  const q = new URLSearchParams({ id: tab.id, title: tab.name, path: tab.path || "", kind: isDoc ? "html" : tab.kind });
  try { await api("recovery?" + q, body, { raw: true }); tab.recovery = true; saveSession(); } catch {  }
}
const saveSession = debounce(() => {
  const tabs = MN.tabs.map((tab) => {
    const v = viewOf(tab);
    return { id: tab.id, path: tab.path, name: tab.name, kind: tab.kind, pinned: tab.pinned, recovery: tab.dirty && MN.settings.recovery,
      cursor: v ? v.state.selection.main.head : tab.cursor, scroll: v ? Math.round(v.scrollDOM.scrollTop) : tab.scroll,
      langName: tab.langOverride ? tab.langName : "", mdMode: tab.mdMode, csvMode: tab.csvMode, mdOk: tab.mdOk, spell: tab.spell,
      indent: tab.indent?.manual ? tab.indent : undefined };
  }).filter((x) => x.path || (x.recovery && x.kind !== "slides"));
  api("session", { tabs, active: activePane()?.tabId || "", layout: { split: MN.panes.length > 1, ai: !$("#ai-panel")?.hidden } }).catch(() => {});
}, 800);

let externalBusy = false;
async function checkExternal() {
  if (externalBusy) return;
  externalBusy = true;
  try { await checkExternalNow(); } finally { externalBusy = false; }
}

async function appendTail(tab, st) {
  if (!(tab.follow || tab.kind === "log") || tab.dirty || !tab.size || st.size <= tab.size || !isTextTab(tab)) return false;
  if (!/^(utf-8|windows-125\d|iso-8859-\d+|koi8-r|ibm866|x-mac-cyrillic)$/.test(tab.webEncoding || "")) return false;
  const res = await fetch(rawUrl(tab.path) + "&offset=" + tab.size).catch(() => null);
  if (!res?.ok) return false;
  const buf = new Uint8Array(await res.arrayBuffer());
  const end = buf.lastIndexOf(10) + 1;
  if (end > 0) {
    const text = new TextDecoder(tab.webEncoding).decode(buf.subarray(0, end));
    const doc = docOf(tab); if (!doc) return false;
    for (const q of MN.panes) {
      const view = q.view && q.view.__tab === tab.id ? q.view : null;
      if (view) {
        const atEnd = tab.follow || view.state.selection.main.head === view.state.doc.length;
        view.dispatch({ changes: { from: view.state.doc.length, insert: text }, annotations: [CM.Transaction.remote.of(true), CM.Transaction.addToHistory.of(false)],
          ...(atEnd && tab.follow ? { selection: { anchor: view.state.doc.length + text.length }, scrollIntoView: true } : {}) });
      } else if (q.states.get(tab.id)) q.states.set(tab.id, q.states.get(tab.id).update({ changes: { from: doc.length, insert: text }, annotations: CM.Transaction.addToHistory.of(false) }).state);
    }
    tab.size += end; tab.savedDoc = docOf(tab);
    markDirty(tab, false);
  }
  tab.mtime = st.mtime;
  return true;
}
async function checkExternalNow() {
  if (document.hidden || !MN.settings.detect_external_changes) return;
  const tabs = MN.tabs.filter((x) => x.path && x.loaded && x.mtime);
  if (!tabs.length) return;
  let r; try { r = await api("stat", { paths: tabs.map((x) => x.path) }); } catch { return; }
  for (const tab of tabs) {
    const st = r.files[tab.path]; if (!st) continue;
    if (!st.exists) { if (!tab.missing) { tab.missing = true; banner(tab, "missing"); } continue; }
    tab.missing = false;
    if (st.mtime && Math.abs(st.mtime - tab.mtime) > 2) {
      if (await appendTail(tab, st)) continue;
      if (!tab.dirty || tab.follow) { tab.mtime = st.mtime; await reloadTab(tab); if (!tab.follow && tab.kind !== "log") toast(t("reloaded", { name: tab.name }), { ms: 1800 }); }
      else if (tab.bannerFor !== st.mtime) { tab.bannerFor = st.mtime; banner(tab, "changed", st.mtime); }
    }
  }
}
function banner(tab, kind, mtime) {
  const host = $("#banners");
  host.querySelector(`[data-tab="${tab.id}"]`)?.remove();
  const el = document.createElement("div"); el.className = "banner"; el.dataset.tab = tab.id;
  el.innerHTML = `${icon("warn")}<span>${esc(t(kind === "missing" ? "banner_missing" : "banner_changed", { name: tab.name }))}</span><span class="spacer"></span>
    ${kind === "changed" ? `<button class="btn sm" data-b="reload">${esc(t("reload_from_disk"))}</button><button class="btn sm ghost" data-b="keep">${esc(t("keep_mine"))}</button>` : `<button class="btn sm" data-b="save">${esc(t("save_again"))}</button><button class="btn sm ghost" data-b="keep">${esc(t("dismiss"))}</button>`}`;
  el.onclick = async (e) => {
    const b = e.target.closest("[data-b]")?.dataset.b; if (!b) return;
    el.remove();
    if (b === "reload") await reloadTab(tab);
    if (b === "keep" && mtime) tab.mtime = mtime;
    if (b === "save") saveTab(tab, { force: true });
  };
  host.append(el);
}
async function pollEvents() {
  let since = MN.boot?.events_seq || 0;
  for (;;) {
    try {
      const r = await api("events?since=" + since);
      since = r.seq;
      for (const ev of r.events) {
        if (ev.type === "open") for (const p of ev.paths || []) await openPath(p);
        if (ev.type === "settings") await reloadSettings();
        if (ev.type === "shutdown" || ev.type === "closing") { saveSession.flush(); for (const tab of MN.tabs) if (tab.dirty) await writeRecovery(tab); if (ev.type === "shutdown") api("window", { cmd: "close" }).catch(() => {}); }
      }
    } catch { await new Promise((r) => setTimeout(r, 2000)); }
  }
}

function renderToolbar() {
  const bar = $("#toolbar"); if (!bar) return;
  const tab = activeTab();
  if (!tab) { bar.innerHTML = ""; return; }
  bar.classList.toggle("photo-hidden", tab.kind === "image");
  const B = (a, ic, label, extra = "") => `<button class="tb-btn" data-a="${a}" title="${esc(label)}" aria-label="${esc(label)}" ${extra}>${icon(ic)}</button>`;
  const L = (a, ic, label, extra = "") => `<button class="tb-btn wide" data-a="${a}" ${extra}>${icon(ic)}<span>${esc(label)}</span></button>`;
  const sep = '<span class="tb-sep"></span>';
  let html = "";
  if (isTextTab(tab) && !viewMode(tab)) {
    html += B("open", "folderOpen", t("open_file") + " (" + kbd("Ctrl+O") + ")") + B("save", "save", t("save") + " (" + kbd("Ctrl+S") + ")") + sep;
    html += B("undo", "undo", t("undo")) + B("redo", "redo", t("redo")) + sep;
    html += B("find", "search", t("find") + " (" + kbd("Ctrl+F") + ")") + B("replace", "replace", t("replace") + " (" + kbd("Ctrl+H") + ")");
    html += B("wrap", "wrap", t("word_wrap") + " (Alt+Z)", MN.settings.word_wrap ? 'class="tb-btn on"' : "") + sep;
    const md = tab.kind === "markdown" || (tab.kind === "text" && !XML_RE.test(tab.path || tab.name));
    if (md) {
      html += `<div class="tb-group md-group ${tab.kind === "text" ? "muted-group" : ""}" title="${tab.kind === "text" ? esc(t("md_group_hint_txt")) : ""}">`
        + B("md:bold", "bold", t("fmt_bold") + " (" + kbd("Ctrl+B") + ")") + B("md:italic", "italic", t("fmt_italic") + " (" + kbd("Ctrl+I") + ")") + B("md:strike", "strike", t("fmt_strike"))
        + B("md:code", "inlineCode", t("fmt_code")) + B("md:heading", "heading", t("fmt_heading")) + B("md:ul", "listUl", t("fmt_ul")) + B("md:ol", "listOl", t("fmt_ol"))
        + B("md:task", "checklist", t("fmt_tasks")) + B("md:quote", "quote", t("fmt_quote")) + B("md:link", "link", t("fmt_link") + " (" + kbd("Ctrl+K") + ")") + B("md:table", "table", t("fmt_table")) + B("md:hr", "hr", t("fmt_hr"))
        + "</div>" + sep;
    }
    if (tab.kind === "markdown") {
      const mode = mdModeOf(tab);
      html += `<div class="seg" role="group">${[["live", "liveMd", "md_mode_live"], ["off", "code", "md_mode_source"], ["split", "split", "md_mode_split"], ["preview", "eye", "md_mode_preview"]].map(([m, ic, k]) => `<button class="seg-btn ${mode === m ? "on" : ""}" data-a="mdmode:${m}" title="${esc(t(k))}">${icon(ic)}</button>`).join("")}</div>` + sep;
    }
    if (tab.kind === "json") html += L("json:format", "json", t("json_format")) + B("json:minify", "lines", t("json_minify")) + B("json:validate", "check", t("json_validate"))
      + `<button class="tb-btn wide ${tab.jsonTree ? "on" : ""}" data-a="json:tree">${icon("outline")}<span>${esc(t("json_tree"))}</span></button>` + sep;
    if (tab.kind === "text" && XML_RE.test(tab.path || tab.name)) html += L("xml:format", "code", t("xml_format")) + B("xml:validate", "check", t("xml_validate")) + sep;
    if (tab.kind === "csv") html += L("csv:table", "table", t("csv_table_view")) + sep;
    if (tab.kind === "notebook") html += L("csv:table", "eye", t("nb_rendered")) + sep;
    if (tab.kind === "log") html += `<button class="tb-btn wide ${tab.follow ? "on" : ""}" data-a="log:follow">${icon("follow")}<span>${esc(t("log_follow"))}</span></button>` + sep;
    if (tab.kind === "html") html += `<button class="tb-btn wide ${tab.htmlPreview ? "on" : ""}" data-a="html:preview">${icon("eye")}<span>${esc(t("html_preview"))}</span></button>` + sep;
  } else if (tab.kind === "csv") {
    html += B("open", "folderOpen", t("open_file")) + B("save", "save", t("save") + " (" + kbd("Ctrl+S") + ")") + sep + L("csv:text", "code", t("csv_text_view")) + sep;
  } else if (tab.kind === "notebook") {
    html += B("open", "folderOpen", t("open_file")) + B("save", "save", t("save") + " (" + kbd("Ctrl+S") + ")") + sep + L("csv:text", "code", t("nb_source")) + sep
      + L("nb:md", "markdown", t("nb_to_md")) + L("nb:py", "code", t("nb_to_script")) + sep;
  } else if (tab.kind === "sheet") {
    html += B("open", "folderOpen", t("open_file")) + sep + L("sheet:csv", "table", t("sheet_to_csv")) + sep;
  } else if (tab.kind === "slides") {
    html += B("open", "folderOpen", t("open_file")) + B("save", "save", t("save") + " (" + kbd("Ctrl+S") + ")") + sep;
  } else if (DOC_KINDS.has(tab.kind)) {
    html += B("open", "folderOpen", t("open_file")) + B("save", "save", t("save") + " (" + kbd("Ctrl+S") + ")") + sep
      + `<span class="tb-note">${icon("doc")}${esc(t("doc_mode", { fmt: tab.kind.toUpperCase() }))}</span>` + sep
      + L("doc:md", "markdown", t("doc_to_md")) + B("find", "search", t("find"));
  } else {
    html += B("open", "folderOpen", t("open_file")) + sep;
  }
  html += `<span class="spacer"></span>`;
  if (isRunnable(tab) && !viewMode(tab)) {
    const running = MN.run?.run && !MN.run.run.done && MN.run.run.path === tab.path;
    html += running ? `<button class="tb-btn wide run-btn stop" data-a="run:stop" title="${esc(t("run_stop"))} (${esc(kbd("Shift+F5"))})">${icon("stop")}<span>${esc(t("run_stop"))}</span></button>`
      : `<button class="tb-btn wide run-btn" data-a="run" title="${esc(t("run_button"))} (F5)">${icon("play")}<span>${esc(t("run_button"))}</span></button>`;
    html += `<button class="tb-btn run-more" data-a="run:menu" title="${esc(t("run_menu"))}" aria-label="${esc(t("run_menu"))}">${icon("chevron", "small")}</button>`;
  }
  html += `<button class="tb-btn wide ai-btn" data-a="ai:menu" ${AI.online ? "" : `title="${esc(t("ai_offline"))}"`}>${icon("sparkle")}<span>${esc(t("ai_button"))}</span>${icon("chevron", "small")}</button>`;
  html += B("export", "export", t("export")) + B("split", "split", t("split_view") + " (" + kbd("Ctrl+\\") + ")", MN.panes.length > 1 ? 'class="tb-btn on"' : "");
  bar.innerHTML = html;
  bar.onmousedown = (e) => { if (e.target.closest(".tb-btn,.seg-btn")) e.preventDefault(); };
  bar.onclick = (e) => { const b = e.target.closest("[data-a]"); if (b) toolbarAction(b.dataset.a, b); };
}
function toolbarAction(a, el) {
  const tab = activeTab(); const v = tab && viewOf(tab);
  const [group, arg] = a.split(":");
  if (group === "md") { mdAction(arg, el); return; }
  switch (a) {
    case "open": openDialog(); break;
    case "save": saveTab(tab); break;
    case "undo": v && CM.undo(v); v?.focus(); break;
    case "redo": v && CM.redo(v); v?.focus(); break;
    case "find": openFind(); break;
    case "replace": openFind(true); break;
    case "wrap": saveSettings({ word_wrap: !MN.settings.word_wrap }).then(renderToolbar); break;
    case "export": exportMenu(el); break;
    case "split": toggleSplit(); break;
    case "ai:menu": if (tab?.kind === "slides" && tab.comp?.cmd) { tab.comp.cmd("rtab:ai"); break; } aiMenu(el); break;
    case "json:format": jsonAction("format"); break;
    case "json:minify": jsonAction("minify"); break;
    case "json:validate": jsonAction("validate"); break;
    case "csv:table": case "csv:text": tab.csvMode = arg; tab.comp?.destroy(); tab.comp = null; tab.box?.remove(); tab.box = null; { const p = activePane(); stashPane(p); showInPane(p, tab); } renderToolbar(); saveSession(); break;
    case "json:tree": { tab.jsonTree = !tab.jsonTree; const p = activePane(); stashPane(p); showInPane(p, tab); renderToolbar(); break; }
    case "xml:format": case "xml:validate": xmlAction(arg); break;
    case "nb:md": case "nb:py": try { const src = textOf(tab); newTab({ kind: arg === "md" ? "markdown" : "text", text: arg === "md" ? notebookToMarkdown(src) : notebookToScript(src), name: tab.name.replace(/\.ipynb$/i, "") + (arg === "md" ? ".md" : ".py") }); } catch (e) { toast(e.message, { kind: "error" }); } break;
    case "sheet:csv": newTab({ text: tab.comp?.csv?.() || "", name: tab.name.replace(/\.\w+$/, "") + (tab.comp?.sheets?.length > 1 ? "-" + (tab.comp.index + 1) : "") + ".csv" }); break;
    case "log:follow": tab.follow = !tab.follow; if (tab.follow) { v?.dispatch({ selection: { anchor: v.state.doc.length }, scrollIntoView: true }); } renderToolbar(); break;
    case "html:preview": { tab.htmlPreview = !tab.htmlPreview; const p = activePane(); p.htmlPreview?.destroy(); p.htmlPreview = null; stashPane(p); showInPane(p, tab); renderToolbar(); break; }
    case "doc:md": docToMarkdown(tab); break;
    case "run": MN.run.start(tab); break;
    case "run:stop": MN.run.stop(); break;
    case "run:menu": runMenu(el); break;
    default:
      if (group === "mdmode") setMdMode(arg);
  }
}

async function ensureMarkdown(tab) {
  if (tab.kind === "markdown" || tab.mdOk) return true;
  if (!MN.settings.ask_markdown_convert) { tab.mdOk = true; return true; }

  const ext = extname(tab.path || tab.name) || ".txt";
  const r = await modal({ title: t("md_convert_title"), cls: "md-convert", body: `<p>${esc(t("md_convert_text", { name: tab.name }))}</p><p class="muted small">${esc(tab.path ? t("md_convert_note", { ext }) : t("md_convert_note_new"))}</p>`,
    buttons: [{ label: t("md_keep_txt", { ext }), value: "plain", left: true }, ...(AI.online ? [{ label: t("md_convert_ai"), value: "ai" }] : []), { label: t("md_convert"), primary: true, value: "convert" }] });
  if (!r) return false;
  if (r === "plain") { tab.mdOk = true; saveSession(); return true; }
  await convertToMarkdown(tab);
  if (r === "ai") { aiInline({ id: "format_md" }, true); return false; }
  return true;
}
async function convertToMarkdown(tab) {
  if (!tab.path) { tab.kind = "markdown"; if (!/\.md$/i.test(tab.name)) tab.name = tab.name.replace(/\.\w+$/, "") + ".md"; }
  else {
    let target = tab.path.replace(/\.[^.\\/]+$/, "") + ".md";
    const st = await api("stat", { paths: [target] });
    if (st.files[target]?.exists) {
      const ok = await confirm(t("md_convert_title"), t("md_target_exists", { name: basename(target) }), t("overwrite"), { danger: true });
      if (!ok) return;
    }
    const old = tab.path;
    tab.path = target; tab.name = basename(target); tab.kind = "markdown"; tab.mtime = 0;
    if (!(await saveTab(tab, { force: true, silent: true }))) { tab.path = old; tab.name = basename(old); tab.kind = "text"; return; }
    addRecent(target);
    toast(t("md_converted", { name: tab.name, old: basename(old) }), { kind: "ok", ms: 5000 });
  }
  tab.langName = ""; const p = activePane(); stashPane(p); showInPane(p, tab); applyLanguage(tab);
  renderTabs(); renderToolbar(); updateTitle(); saveSession();
}
async function mdAction(cmd, el) {
  const tab = activeTab(); if (!tab || !isTextTab(tab)) return;
  if (!(await ensureMarkdown(tab))) return;
  const v = viewOf(tab); if (!v) return;
  switch (cmd) {
    case "bold": mdWrap(v, "**", "**", t("md_bold_text")); break;
    case "italic": mdWrap(v, "*", "*", t("md_italic_text")); break;
    case "strike": mdWrap(v, "~~", "~~", t("md_strike_text")); break;
    case "code": mdWrap(v, "`", "`", "code"); break;
    case "heading": showMenu([1, 2, 3, 4, 5, 6].map((n) => ({ label: t("fmt_h", { n }), icon: n <= 3 ? "h" + n : "heading", kbd: n <= 3 ? kbd(`Ctrl+Alt+${n}`) : "", run: () => mdHeading(v, n) })), el); break;
    case "ul": mdLinePrefix(v, "- "); break;
    case "ol": mdLinePrefix(v, "1. ", { numbered: true, re: /^\s*\d+[.)]\s+/ }); break;
    case "task": mdLinePrefix(v, "- [ ] ", { re: /^\s*[-*+]\s+\[[ xX]\]\s+/ }); break;
    case "quote": mdLinePrefix(v, "> "); break;
    case "link": mdLink(v); break;
    case "hr": mdInsert(v, "\n\n---\n\n"); break;
    case "table": {
      const spec = await prompt(t("fmt_table"), t("table_size"), "3x3"); const m = /(\d+)\s*[x×х*]\s*(\d+)/i.exec(spec || "");
      if (m) mdInsert(v, mdTable(Math.min(200, +m[1]), Math.min(30, +m[2]))); break;
    }
    case "formatTable": if (!mdFormatTable(v)) toast(t("md_no_table")); break;
  }
}
async function docToMarkdown(tab) {
  const html = tab.doc ? tab.doc.html() : tab.html;
  const r = await api("convert", { html, to: "markdown" });
  newTab({ kind: "markdown", text: r.text, name: tab.name.replace(/\.\w+$/, "") + ".md" });
}
function xmlAction(kind) {
  const tab = activeTab(); const v = viewOf(tab); if (!v) return;
  const text = v.state.doc.toString();
  if (kind === "validate") { const err = xmlError(text); toast(err ? t("xml_invalid") + ": " + err : t("xml_valid"), { kind: err ? "error" : "ok", ms: err ? 8000 : 2400 }); return; }
  try { v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: xmlFormat(text, v.state.facet(CM.indentUnit)) }, userEvent: "input.format" }); }
  catch (e) { toast(t("xml_invalid") + ": " + e.message, { kind: "error", ms: 8000 }); }
}
function jsonAction(kind) {
  const tab = activeTab(); const v = viewOf(tab); if (!v) return;
  const text = v.state.doc.toString();
  if (kind === "validate") {
    const err = jsonError(text);
    if (!err) { toast(t("json_valid"), { kind: "ok" }); return; }
    toast(t("json_invalid") + ": " + err.message, { kind: "error", ms: 8000 });
    if (err.pos >= 0) v.dispatch({ selection: { anchor: Math.min(err.pos, text.length) }, scrollIntoView: true });
    return;
  }
  try {
    const out = kind === "format" ? jsonFormat(text, v.state.facet(CM.indentUnit)) : JSON.stringify(JSON.parse(text));
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: out } });
  } catch (e) { toast(t("json_invalid") + ": " + e.message, { kind: "error" }); }
}

function openFind(replace) {
  const tab = activeTab();
  if (tab && DOC_KINDS.has(tab.kind) && tab.doc) { docFind(tab.doc); return; }
  const v = tab && viewOf(tab); if (!v) return;
  const wasOpen = !!v.dom.querySelector(".mn-find");
  CM.openSearchPanel(v);
  setTimeout(() => {
    const panel = v.dom.querySelector(".mn-find"); if (!panel) return;
    const r = v.state.selection.main;
    let sel = !wasOpen && r.to - r.from < 1000 ? v.state.sliceDoc(r.from, r.to) : "";
    const s = panel.querySelector("[name=search]");
    if (sel && CM.getSearchQuery(v.state).regexp) sel = sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (sel && !sel.includes("\n")) { s.value = sel; s.dispatchEvent(new Event("input", { bubbles: true })); }
    if (replace && !panel.classList.contains("with-replace")) panel.querySelector("[data-act=toggleReplace]")?.click();
    const field = panel.querySelector(replace && s.value ? "[name=replace]" : "[name=search]");
    field?.focus(); field?.select();
  }, 0);
}
async function gotoLine() {
  const v = viewOf(activeTab() || {}); if (!v) return;
  const cur = v.state.doc.lineAt(v.state.selection.main.head);
  const val = await prompt(t("goto_line"), t("goto_line_label", { n: v.state.doc.lines }), String(cur.number));
  if (!val) return;
  const m = /^\s*(\d+)(?:\s*[:,]\s*(\d+))?\s*$/.exec(val); if (!m) return;
  const line = v.state.doc.line(Math.max(1, Math.min(v.state.doc.lines, +m[1])));
  const pos = Math.min(line.to, line.from + Math.max(0, (+m[2] || 1) - 1));
  v.dispatch({ selection: { anchor: pos }, effects: CM.EditorView.scrollIntoView(pos, { y: "center" }) }); v.focus();
}

function aiCtx() {
  return {
    settings: () => MN.settings,
    saveSetting: (p) => saveSettings(p),
    getContext: () => {
      const tab = activeTab(); if (!tab) return { name: "", kind: "text", text: "" };
      const v = viewOf(tab);
      const text = textOf(tab);
      const sel = selectionText();
      const kind = DOC_KINDS.has(tab.kind) ? "document" : tab.kind === "text" && tab.langName && tab.langName !== "Plain text" ? "code" : tab.kind;

      const r = v ? v.state.selection.main : null;
      const c = aiExcerpt({ text, kind, selFrom: r ? r.from : text.length, selTo: r ? r.to : text.length, limit: MN.settings.ai_context_chars });
      return { name: tab.name, kind, text: c.text, selection: sel, pos: c.pos };
    },

    editor: () => {
      const tab = activeTab(); if (!tab) return null;
      const kind = DOC_KINDS.has(tab.kind) ? "document" : tab.kind === "text" && tab.langName && tab.langName !== "Plain text" ? "code" : tab.kind;
      return { tab, name: tab.name, kind, view: viewOf(tab), doc: DOC_KINDS.has(tab.kind) ? tab.doc || null : null, readOnly: !!tab.readOnly, text: () => textOf(tab) };
    },
    viewFor: (id) => { const tab = tabById(id); return tab ? viewOf(tab) : null; },
    docFor: (id) => tabById(id)?.doc || null,
    // Round 40 (r40-note): other open tabs for the AI context (chips / @tab) and their full text
    tabs: () => MN.tabs.map((x) => ({ id: x.id, name: x.name, kind: x.kind, path: x.path || "" })),
    tabDoc: async (id) => {
      const x = tabById(id); if (!x) return null;
      if (!x.loaded) await ensureLoaded(x).catch(() => {});
      if (x.kind === "sheet") return { name: x.name, kind: "sheet", text: (x.comp?.plan ? x.comp : new SheetData(x)).text(), editKind: "sheet", readOnly: !x.sheetEditable };
      let text = x.kind === "pdf" ? (x.comp?.text ? await x.comp.text() : "") : await textOf(x);
      if (!text && x.html) text = x.html.replace(/<[^>]+>/g, " ");
      return { name: x.name, kind: DOC_KINDS.has(x.kind) ? "document" : x.kind, text: String(text || ""), readOnly: !!x.readOnly,
        editKind: DOC_KINDS.has(x.kind) ? "doc" : isTextTab(x) ? "cm" : "none" };
    },
    sheetFor: (id) => { const x = tabById(id); return x?.comp instanceof SheetGrid ? x.comp : null; },
    sheetData: (id) => { const x = tabById(id); return x ? (x.comp instanceof SheetGrid ? x.comp : new SheetData(x)) : null; },
    activate: (id) => { if (tabById(id) && activeTab()?.id !== id) activate(id); },
    insert: (text) => applyAI(text, "insert"),
    replace: (text) => applyAI(text, selectionText() ? "replace" : "insert"),
    newTab: (text, o = {}) => newTab({ text, name: o.title || undefined, kind: o.kind || (/(^|\n)(#{1,6} |[-*] |\|.*\|)/.test(text) ? "markdown" : "text") }),
    command: (id) => runAICommand(id),
    close: () => toggleAI(false),
  };
}
function selectionText() {
  const tab = activeTab(); if (!tab) return "";
  if (DOC_KINDS.has(tab.kind)) return tab.doc?.selectionText() || "";
  const v = viewOf(tab); if (!v) return "";
  return v.state.selection.ranges.map((r) => v.state.sliceDoc(r.from, r.to)).join("\n");
}
function applyAI(text, mode) {
  const tab = activeTab(); if (!tab) return;
  if (DOC_KINDS.has(tab.kind)) {
    const html = /(^|\n)(#{1,6} |[-*] |\d+\. |\|.*\||> )|\*\*|__/.test(text) ? renderMarkdown(text).replace(/ data-line="\d+"/g, "") : text.split(/\n{2,}/).map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
    if (mode === "below") tab.doc.page.insertAdjacentHTML("beforeend", html), tab.doc.opts.onChange();
    else tab.doc.replaceSelection(html);
    return;
  }
  const v = viewOf(tab); if (!v) { newTab({ text }); return; }
  if (tab.readOnly) { toast(t("read_only"), { kind: "error" }); return; }
  const r = v.state.selection.main;
  if (mode === "replace" && !r.empty) v.dispatch({ changes: { from: r.from, to: r.to, insert: text }, selection: { anchor: r.from, head: r.from + text.length }, scrollIntoView: true, userEvent: "input.ai" });
  else if (mode === "below") { const line = v.state.doc.lineAt(r.to); const ins = "\n\n" + text; v.dispatch({ changes: { from: line.to, insert: ins }, selection: { anchor: line.to + 2, head: line.to + ins.length }, scrollIntoView: true, userEvent: "input.ai" }); }
  else {

    const line = v.state.doc.lineAt(r.head);
    const pre = text.includes("\n") && r.head > line.from ? "\n" : "", post = text.includes("\n") && r.head < line.to ? "\n" : "";
    const ins = pre + text + post;
    v.dispatch({ changes: { from: r.head, insert: ins }, selection: { anchor: r.head + pre.length + text.length }, scrollIntoView: true, userEvent: "input.ai" });
  }
  v.focus();
}
function aiInline(preset, force) {
  const tab = activeTab(); if (!tab) return;
  if (!MN.settings.ai_enabled) { toast(t("ai_disabled"), { kind: "error" }); return; }
  const v = viewOf(tab);
  if (preset?.id === "format_md" && tab.kind === "text" && !force) { ensureMarkdown(tab); return; }
  if (DOC_KINDS.has(tab.kind)) tab.doc?.saveRange();
  inlineCommand({
    anchorRect: () => {
      if (v) { const c = v.coordsAtPos(v.state.selection.main.from) || v.dom.getBoundingClientRect(); const e = v.coordsAtPos(v.state.selection.main.to) || c; return { left: c.left, top: c.top, bottom: Math.max(c.bottom, e.bottom) }; }
      const sel = window.getSelection(); const r = sel?.rangeCount ? sel.getRangeAt(0).getBoundingClientRect() : null;
      return r && r.width + r.height > 0 ? r : { left: 120, top: 140, bottom: 160 };
    },
    selection: selectionText,
    getContext: aiCtx().getContext,
    settings: () => MN.settings,
    before: () => v ? v.state.sliceDoc(0, v.state.selection.main.head) : textOf(tab),
    isCode: () => tab.kind === "json" || (tab.kind === "text" && tab.langName && tab.langName !== "Plain text") || tab.kind === "html",
    apply: (text, mode) => applyAI(text, mode),
    toChat: (id, input, param) => { toggleAI(true); MN.chat.ask(id, input, { param }); },
    focus: () => (v ? v.focus() : tab.doc?.focus()),
  }, preset);
}
function runAICommand(id, param) {
  const cmd = COMMANDS.find((c) => c.id === id); if (!cmd) return;
  if (!cmd.transform) { toggleAI(true); MN.chat.ask(id, selectionText(), { param }); return; }
  aiInline({ id, param });
}
function aiMenu(anchor) {
  if (!AI.online) {
    showMenu([{ header: t("ai_offline") }, { label: t("ai_offline_text"), disabled: true }, "-", { label: t("retry"), icon: "retry", run: () => refreshStatus(true).then(() => AI.online && toast(t("ai_online"), { kind: "ok" })) }], anchor);
    return;
  }
  const sel = !!selectionText();
  const items = [
    { label: t("ai_command") + "…", icon: "sparkle", kbd: kbd("Ctrl+J"), run: () => aiInline() },
    { label: t("ai_panel"), icon: "sparkle", kbd: kbd("Ctrl+Shift+L"), run: () => toggleAI(true) },
    "-", { header: sel ? t("ai_for_selection") : t("ai_for_document") },
  ];
  const tab = activeTab();
  const isCode = tab && (tab.kind === "json" || tab.kind === "html" || (tab.kind === "text" && tab.langName && tab.langName !== "Plain text"));
  for (const c of COMMANDS) {
    if (c.code && !isCode) continue;
    if (!sel && ["improve", "grammar", "rewrite", "shorten", "expand", "simplify", "tone", "translate", "list"].includes(c.id) && !c.code) {
      if (!["translate"].includes(c.id)) continue;
    }
    if (c.params) {
      items.push({ label: t(c.label), icon: c.icon, sub: c.params.map((p) => ({ label: t((c.id === "translate" ? "lang_" : "tone_") + p), run: () => runAICommand(c.id, p) })) });
    } else items.push({ label: t(c.label), icon: c.icon, run: () => runAICommand(c.id) });
  }
  items.push("-", { label: t("ai_web_search_selection"), icon: "globe", disabled: !sel, run: () => webSearch(selectionText()) });
  showMenu(items, anchor, { alignRight: true, cls: "ai-menu" });
}
async function webSearch(query) {
  query = (query || "").trim().slice(0, 300);
  if (!query) query = await prompt(t("web_search"), t("web_search_query"), "");
  if (!query) return;
  toggleAI(true);
  const bubble = MN.chat.addBubble("assistant", `<div class="msg-meta">${icon("globe")}<span>${esc(t("web_search"))}: ${esc(query)}</span></div><div class="msg-body"><span class="typing"><i></i><i></i><i></i></span></div>`);
  try {
    const r = await api("ai/search", { query, limit: 8 });
    bubble.querySelector(".msg-body").innerHTML = r.results.length ? `<ol class="results">${r.results.map((x) => `<li><a href="#" data-url="${esc(x.url)}">${esc(x.title || x.url)}</a><div class="muted small">${esc(x.url)}</div><p>${esc(x.snippet)}</p><button class="chip" data-ins="${esc(x.url)}" data-title="${esc(x.title)}">${icon("insert")}${esc(t("insert_link"))}</button></li>`).join("")}</ol>` : `<p class="muted">${esc(t("nothing_found"))}</p>`;
    bubble.onclick = (e) => {
      const a = e.target.closest("[data-url]"); if (a) { e.preventDefault(); api("external", { url: a.dataset.url }); }
      const ins = e.target.closest("[data-ins]"); if (ins) { const tab = activeTab(); applyAI(tab?.kind === "markdown" ? `[${ins.dataset.title || ins.dataset.ins}](${ins.dataset.ins})` : ins.dataset.ins, "insert"); }
    };
  } catch (e) { bubble.querySelector(".msg-body").innerHTML = `<div class="err">${icon("warn")}${esc(e.message)}</div>`; }
}
function toggleAI(force) {
  const ptab = activeTab();
  if (ptab?.kind === "image" && ptab.comp?.isPhoto && force === undefined && $("#ai-panel").hidden) { ptab.comp.showTab("ai"); return; }
  const panel = $("#ai-panel"); const open = force ?? panel.hidden;
  if (open && !MN.settings.ai_enabled) { toast(t("ai_disabled"), { kind: "error" }); return; }
  panel.hidden = !open; $(".ai-gutter").hidden = !open;
  $("#btn-ai").classList.toggle("on", open);
  if (open && !MN.chat) { MN.chat = new ChatPanel(panel, aiCtx()); if (MN.chatCarry) { MN.chat.adopt(MN.chatCarry); MN.chatCarry = null; } }
  if (open) MN.chat.focus(); else viewOf(activeTab() || {})?.focus();
  saveSession();
}

function runHooks() {
  return {
    activeTab, tabAlive: (tab) => MN.tabs.includes(tab),
    saveTab: (tab, o) => saveTab(tab, o), saveAs: (tab) => saveAs(tab), prompt: (a, b, c) => prompt(a, b, c),
    fileDialog: (mode, o) => fileDialog(mode, o),
    focusEditor: () => viewOf(activeTab() || {})?.focus(),
    onState: () => renderToolbar(),
    openAt: async (path, line, col) => {
      const tab = await openPath(path); if (!tab) return;
      const v = viewOf(tab); if (!v) return;
      const l = v.state.doc.line(Math.max(1, Math.min(v.state.doc.lines, line)));
      const pos = Math.min(l.to, l.from + Math.max(0, col - 1));
      v.dispatch({ selection: { anchor: pos }, effects: CM.EditorView.scrollIntoView(pos, { y: "center" }) }); v.focus();
    },
  };
}
async function runMenu(anchor) {
  const tab = activeTab(); if (!tab) return;
  let info = null;
  if (tab.path) info = await api("run/info?path=" + encodeURIComponent(tab.path)).catch(() => null);
  const ext = extname(tab.path || tab.name);
  showMenu([
    { label: t("run_button"), icon: "play", kbd: "F5", run: () => MN.run.start(tab) },
    { label: t("run_with_args"), icon: "terminal", run: () => MN.run.start(tab, { askArgs: true }) },
    { label: t("run_stop"), icon: "stop", kbd: kbd("Shift+F5"), disabled: !(MN.run.run && !MN.run.run.done), run: () => MN.run.stop() },
    { label: t("run_toggle_panel"), icon: "terminal", kbd: kbd("Ctrl+`"), checked: MN.run.visible, run: () => MN.run.toggle() },
    "-",
    { header: info ? (info.found ? t("run_interp_now", { name: info.interpreter }) : t("run_interp_none")) : t("run_menu") },
    { label: t("run_interp_change"), icon: "settings", run: () => MN.run.pickInterpreter(ext) },
    { label: t("run_interp_default"), disabled: !info?.custom, run: () => MN.run.resetInterpreter(ext) },
    { label: t("run_forget_trust"), disabled: !info?.trusted, run: () => api("run/config", { untrust: tab.path }).then(() => toast(t("done"), { ms: 1200 })) },
  ], anchor, { alignRight: true });
}

function toggleSplit(force) {
  const want = force ?? MN.panes.length === 1;
  const right = $("#pane-right"), gutter = $(".pane-gutter");
  if (want && MN.panes.length === 1) {
    right.hidden = false; gutter.hidden = false;
    const p = makePane("right", right); MN.panes.push(p);
    const tab = activeTab(); MN.active = "right";
    if (tab) activate(tab.id, "right");
  } else if (!want && MN.panes.length > 1) {
    const p = pane("right"); stashPane(p);
    p.view?.destroy(); p.states.clear();
    for (const tab of MN.tabs) if (tab.box && tab.box.parentElement === p.compHost) { tab.comp?.destroy?.(); tab.doc?.destroy(); tab.box.remove(); tab.box = null; tab.comp = null; tab.doc = null; }
    right.innerHTML = ""; right.hidden = true; gutter.hidden = true;
    MN.panes = MN.panes.filter((x) => x.id !== "right"); MN.active = "left";
    const left = pane("left"); const tab = tabById(left.tabId); if (tab) showInPane(left, tab);
    $$(".pane").forEach((el) => el.classList.remove("focused"));
  }
  renderTabs(); renderToolbar(); saveSession();
}

let sideMode = localStorage.getItem("mn_side") || "files", sideDir = "";
function toggleSidebar(force) {
  const el = $("#sidebar"); const open = force ?? el.hidden; el.hidden = !open;
  if (open) renderSidebar();
  if (force === undefined) saveSettings({ show_sidebar: open });
}
const outlineChanged = debounce(() => { if (!$("#sidebar").hidden && sideMode === "outline") renderSidebar(); }, 400);
async function renderSidebar() {
  const el = $("#sidebar"); if (!el || el.hidden) return;
  const tab = activeTab();
  el.innerHTML = `<div class="side-tabs"><button class="${sideMode === "files" ? "on" : ""}" data-s="files">${icon("folder")}${esc(t("side_files"))}</button><button class="${sideMode === "outline" ? "on" : ""}" data-s="outline">${icon("outline")}${esc(t("side_outline"))}</button><span class="spacer"></span><button class="icon-btn sm" data-s="close">${icon("close")}</button></div><div class="side-body"></div>`;
  el.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => { if (b.dataset.s === "close") { toggleSidebar(false); return; } sideMode = b.dataset.s; localStorage.setItem("mn_side", sideMode); renderSidebar(); }));
  const body = $(".side-body", el);
  if (sideMode === "outline") {
    const heads = [];
    if (tab && DOC_KINDS.has(tab.kind) && tab.doc) tab.doc.page.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((h, i) => heads.push({ level: +h.tagName[1], text: h.textContent, el: h, i }));
    else if (tab && isTextTab(tab)) {
      const big = (docOf(tab)?.length || 0) >= 8 * 1048576;
      const text = big ? "" : textOf(tab); const lines = text.split("\n"); let inFence = false;
      if (!big) lines.forEach((l, n) => {
        if (/^\s*(```|~~~)/.test(l)) inFence = !inFence;
        const m = !inFence && /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(l); if (m && (tab.kind === "markdown" || tab.kind === "text")) heads.push({ level: m[1].length, text: m[2], line: n + 1 });
        const f = tab.kind === "text" && tab.langName !== "Plain text" && /^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|sub|procedure|interface|struct|enum)\s+([\w$]+)/.exec(l);
        if (f) heads.push({ level: /^\s+/.test(l) ? 2 : 1, text: f[1], line: n + 1 });
      });
    }
    body.innerHTML = heads.length ? `<div class="outline">${heads.map((h, i) => `<button class="ol-item l${h.level}" data-i="${i}">${esc(h.text)}</button>`).join("")}</div>` : `<p class="muted small pad">${esc(t("outline_empty"))}</p>`;
    body.querySelectorAll(".ol-item").forEach((b) => (b.onclick = () => {
      const h = heads[+b.dataset.i];
      if (h.el) h.el.scrollIntoView({ behavior: "smooth", block: "start" });
      else { const v = viewOf(tab); const line = v.state.doc.line(h.line); v.dispatch({ selection: { anchor: line.from }, effects: CM.EditorView.scrollIntoView(line.from, { y: "start", yMargin: 20 }) }); v.focus(); }
    }));
    return;
  }
  const dir = sideDir || (tab?.path ? dirname(tab.path) : MN.boot.home);
  body.innerHTML = `<p class="muted small pad">${esc(t("loading"))}</p>`;
  try {
    const r = await api("fs/list", { dir });
    sideDir = r.dir;
    body.innerHTML = `<div class="fs-head">${r.parent ? `<button class="icon-btn sm" data-up title="${esc(t("folder_up"))}">${icon("up")}</button>` : ""}<span class="fs-path" title="${esc(r.dir)}">${esc(basename(r.dir) || r.dir)}</span><button class="icon-btn sm" data-pick title="${esc(t("open_folder"))}">${icon("folderOpen")}</button></div>
      <div class="fs-list">${r.entries.map((e, i) => `<button class="fs-item ${e.dir ? "dir" : ""} ${tab?.path === e.path ? "on" : ""}" data-i="${i}" title="${esc(e.path)}">${icon(e.dir ? "folder" : kindIcon(e.kind))}<span>${esc(e.name)}</span>${e.dir ? "" : `<small>${fmtBytes(e.size)}</small>`}</button>`).join("")}</div>`;
    body.querySelector("[data-up]")?.addEventListener("click", () => { sideDir = r.parent; renderSidebar(); });
    body.querySelector("[data-pick]")?.addEventListener("click", async () => { const p = await fileDialog("folder", { directory: r.dir, multiple: false }); if (p?.[0]) { sideDir = p[0]; renderSidebar(); } });
    body.querySelectorAll(".fs-item").forEach((b) => (b.onclick = () => { const e = r.entries[+b.dataset.i]; if (e.dir) { sideDir = e.path; renderSidebar(); } else openPath(e.path); }));
  } catch (e) { body.innerHTML = `<p class="muted small pad">${esc(e.message)}</p>`; sideDir = ""; }
}

const updateStatusSoon = debounce(() => updateStatus(), 60);
function updateStatus() {
  const bar = $("#status"); if (!bar) return;
  const tab = activeTab();
  const v = tab && viewOf(tab);
  const parts = [];
  const aiLabel = AI.online ? (AI.models.find((m) => m.id === AI.model)?.label || AI.model || t("ai_no_model_short")) : t("ai_offline_short");
  parts.push(`<button class="st ai ${AI.online ? "on" : "off"}" data-st="ai" title="${esc(AI.online ? t("ai_online") : t("ai_offline"))}"><span class="dot"></span>${esc(aiLabel)}</button>`);
  if (tab?.large) parts.push(`<span class="st badge" title="${esc(t("large_hint"))}">${icon("lines")}${esc(t("large_mode"))}</span>`);
  if (tab?.longLines && MN.settings.word_wrap) parts.push(`<span class="st badge" title="${esc(t("long_lines_hint"))}">${icon("wrap")}${esc(t("long_lines"))}</span>`);
  if (tab?.fileReadOnly) parts.push(`<span class="st badge">${icon("lock")}${esc(t("read_only"))}</span>`);
  parts.push('<span class="spacer"></span>');
  if (v) {
    const st = v.state, r = st.selection.main, line = st.doc.lineAt(r.head);
    parts.push(`<button class="st" data-st="goto">${esc(t("st_pos", { l: line.number, c: r.head - line.from + 1 }))}</button>`);
    if (!r.empty) { const sel = st.sliceDoc(r.from, r.to); parts.push(`<span class="st">${esc(t("st_sel", { n: (r.to - r.from).toLocaleString(), l: st.doc.lineAt(r.to).number - line.number + 1 }))}</span>`); if (sel.length < 500000) parts.push(`<span class="st">${esc(t("st_words", { n: countWords(sel).toLocaleString() }))}</span>`); }
    else if (st.doc.length < 1048576) parts.push(`<span class="st" title="${esc(t("st_chars", { n: st.doc.length.toLocaleString() }))}">${esc(t("st_words", { n: countWords(st.doc.toString()).toLocaleString() }))}</span>`);
    else parts.push(`<span class="st">${esc(t("st_lines", { n: st.doc.lines.toLocaleString() }))}</span>`);

    if (spellMode(tab)) parts.push(`<button class="st st-toggle ${spellOn(tab) ? "on" : "off"}" data-st="spell" title="${esc(t(spellOn(tab) ? "st_spell_on" : "st_spell_off"))}">${icon("spell")}<span>${esc(t("st_spell"))}</span></button>`);
    if (isCodeTab(tab) && AI.online && MN.settings.ai_enabled) parts.push(`<button class="st st-toggle ${MN.settings.ai_autocomplete ? "on" : "off"}" data-st="aisuggest" title="${esc(t(MN.settings.ai_autocomplete ? "st_ai_suggest_on" : "st_ai_suggest_off"))}">${icon("sparkle")}<span>${esc(t("st_ai_suggest"))}</span></button>`);
    parts.push(`<button class="st" data-st="lang">${esc(tab.langName === "Plain text" || !tab.langName ? t("plain_text") : tab.langName)}</button>`);
    const indent = tab.indent || MN.settings;
    parts.push(`<button class="st" data-st="indent" title="${esc(t(indent.detected ? "indent_detected" : "indent_document"))}">${esc(indent.indent_with_tabs ? t("st_tabs", { n: indent.tab_size }) : t("st_spaces", { n: indent.tab_size }))}</button>`);
    parts.push(`<button class="st" data-st="enc">${esc(encLabel(tab))}</button>`);
    parts.push(`<button class="st" data-st="eol" title="${tab.mixedEol ? esc(t("eol_mixed_hint")) : ""}">${esc(tab.mixedEol ? t("eol_mixed") : tab.eol.toUpperCase())}</button>`);
  } else if (tab && DOC_KINDS.has(tab.kind) && tab.doc) {
    const sel = tab.doc.selectionText();
    const text = tab.doc.text();
    parts.push(`<span class="st">${esc(sel ? t("st_words_sel", { n: countWords(sel), all: countWords(text) }) : t("st_words", { n: countWords(text).toLocaleString() }))}</span>`);
    parts.push(`<span class="st">${esc(t("st_chars", { n: text.length.toLocaleString() }))}</span>`);
    parts.push(`<span class="st">${esc(tab.kind.toUpperCase())}</span>`);
  } else if (tab) parts.push(`<span class="st">${esc({ sheet: t("kind_sheet"), book: t("kind_book"), notebook: t("kind_notebook"), slides: t("kind_slides") }[tab.kind] || tab.kind.toUpperCase())}${tab.size ? " · " + fmtBytes(tab.size) : ""}</span>`);
  parts.push(`<button class="st" data-st="zoom" title="${esc(t("zoom"))}">${MN.settings.zoom}%</button>`);
  bar.innerHTML = parts.join("");
  bar.onclick = (e) => { const b = e.target.closest("[data-st]"); if (b) statusAction(b.dataset.st, b); };
}
function encLabel(tab) { const e = (tab.encoding || "utf-8").toLowerCase(); const map = { "utf-8": "UTF-8", "utf-8-sig": "UTF-8 BOM", "utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE", cp1251: "Windows-1251", cp1252: "Windows-1252", "koi8-r": "KOI8-R", cp866: "CP866" }; return map[e] || e.toUpperCase(); }
const ENCODINGS = [["utf-8", "UTF-8"], ["utf-8-sig", "UTF-8 BOM"], ["utf-16-le", "UTF-16 LE"], ["utf-16-be", "UTF-16 BE"], ["cp1251", "Windows-1251"], ["koi8-r", "KOI8-R"], ["cp866", "CP866 (DOS)"], ["cp1252", "Windows-1252"], ["iso-8859-1", "ISO-8859-1"], ["iso-8859-5", "ISO-8859-5"], ["mac-cyrillic", "Mac Cyrillic"], ["cp1250", "Windows-1250"], ["cp1253", "Windows-1253"], ["cp1254", "Windows-1254"], ["gb18030", "GB18030"], ["big5", "Big5"], ["shift_jis", "Shift-JIS"], ["euc-kr", "EUC-KR"]];
function statusAction(a, el) {
  const tab = activeTab(); const v = tab && viewOf(tab);
  if (a === "ai") { if (!AI.online) refreshStatus(true).then(() => toast(AI.online ? t("ai_online") : t("ai_offline"), { kind: AI.online ? "ok" : "error" })); else toggleAI(true); }
  if (a === "goto") gotoLine();
  if (a === "spell") toggleSpell(tab);
  if (a === "aisuggest") saveSettings({ ai_autocomplete: !MN.settings.ai_autocomplete }).then(() => toast(t(MN.settings.ai_autocomplete ? "st_ai_suggest_on" : "st_ai_suggest_off"), { ms: 1800 }));
  if (a === "zoom") showMenu([{ label: t("zoom_in"), kbd: kbd("Ctrl+="), run: () => zoom(10) }, { label: t("zoom_out"), kbd: kbd("Ctrl+-"), run: () => zoom(-10) }, { label: t("zoom_reset"), kbd: kbd("Ctrl+0"), run: () => zoom(0) }], el);
  if (a === "eol") showMenu(["crlf", "lf", "cr"].map((e) => ({ label: { crlf: "CRLF (Windows)", lf: "LF (Unix, macOS)", cr: "CR (Classic Mac)" }[e], checked: tab.eol === e && !tab.mixedEol, run: () => { tab.eol = e; tab.mixedEol = false; markDirty(tab, true); updateStatus(); } })), el);
  if (a === "indent" && v) {
    const indent = tab.indent || MN.settings;
    const change = (patch) => setDocumentIndent(tab, { ...indent, ...patch, detected: false, manual: true });
    showMenu([{ header: t("indent_document") },
      { label: t("indent_spaces"), checked: !indent.indent_with_tabs, run: () => change({ indent_with_tabs: false }) },
      { label: t("indent_tabs"), checked: indent.indent_with_tabs, run: () => change({ indent_with_tabs: true }) }, "-",
      ...[2, 4, 8].map((n) => ({ label: t("tab_size_n", { n }), checked: indent.tab_size === n, run: () => change({ tab_size: n }) })),
      { label: t("indent_detect"), run: () => setDocumentIndent(tab, detectIndentation(v.state.doc, MN.settings)) }, "-",
      { label: t("convert_tabs_spaces"), run: () => { TOOLS.tabsToSpaces(v, indent.tab_size); change({ indent_with_tabs: false }); } },
      { label: t("convert_spaces_tabs"), run: () => { TOOLS.spacesToTabs(v, indent.tab_size); change({ indent_with_tabs: true }); } }], el);
  }
  if (a === "enc") showMenu([{ header: t("enc_reopen") }, { label: t("enc_reopen_with") + "…", sub: ENCODINGS.map(([k, l]) => ({ label: l, checked: tab.encoding === k, run: async () => {
      if (tab.dirty && !(await confirm(t("enc_reopen"), t("enc_reopen_dirty"), t("enc_reopen")))) return; await reopenAsText(tab, k); toast(t("enc_reopened", { enc: l })); } })) },
    { header: t("enc_save") }, { label: t("enc_save_with") + "…", sub: ENCODINGS.map(([k, l]) => ({ label: l, checked: tab.encoding === k, run: () => { tab.encoding = k; tab.bom = k === "utf-8-sig" || (k.startsWith("utf-16") && tab.bom); markDirty(tab, true); updateStatus(); toast(t("enc_will_save", { enc: l })); } })) }], el);
  if (a === "lang") {
    const items = [{ label: t("plain_text"), run: () => setLang("Plain text") }, { label: "Markdown", run: () => setLang("Markdown") }, ...CM.languages.map((l) => ({ label: l.name, keywords: (l.extensions || []).join(" "), run: () => setLang(l.name) }))];
    palette(items, { placeholder: t("lang_pick") });
  }
}
function setDocumentIndent(tab, indent) {
  tab.indent = indent;
  for (const p of MN.panes) {
    const effect = configComp.reconfigure(buildConfig(editorOpts(tab)));
    if (p.view?.__tab === tab.id) p.view.dispatch({ effects: effect });
    else if (p.states.has(tab.id)) p.states.set(tab.id, p.states.get(tab.id).update({ effects: effect }).state);
  }
  updateStatus(); saveSession();
}
function setLang(name) { const tab = activeTab(); if (!tab) return; tab.langName = name; tab.langOverride = true; applyLanguage(tab); saveSession(); }
function zoom(d) { saveSettings({ zoom: d === 0 ? 100 : Math.max(50, Math.min(300, MN.settings.zoom + d)) }); }
function updateTitle() { const tab = activeTab(); const title = tab ? `${tab.dirty ? "● " : ""}${tab.name} — MemuNote` : "MemuNote"; document.title = title; api("window", { cmd: "title", title }).catch(() => {}); }

function appMenu(anchor) {
  const tab = activeTab(); const v = tab && viewOf(tab);
  const recent = MN.recent.slice(0, 15).map((p) => ({ label: basename(p), hint: dirname(p), icon: kindIcon(kindByExt(p)), run: () => openPath(p) }));
  showMenu([
    { label: t("new_tab"), icon: "plus", kbd: kbd("Ctrl+N"), run: () => newTab() },
    { label: t("new_markdown"), icon: "markdown", run: () => newTab({ kind: "markdown", name: t("untitled", { n: ++untitledCounter }) + ".md" }) },
    { label: t("new_presentation"), icon: "slides", run: () => newPresentation() },
    { label: t("ph_new_image"), icon: "image", run: () => newImageTab() },
    { label: t("open_file") + "…", icon: "folderOpen", kbd: kbd("Ctrl+O"), run: openDialog },
    { label: t("recent"), icon: "history", sub: recent.length ? [...recent, "-", { label: t("recent_clear"), run: () => { MN.recent = []; api("recent", { clear: true }); } }] : [{ label: t("recent_empty"), disabled: true }] },
    { label: t("reopen_closed"), icon: "retry", kbd: kbd("Ctrl+Shift+T"), disabled: !MN.closed.length, run: reopenClosed },
    "-",
    { label: t("save"), icon: "save", kbd: kbd("Ctrl+S"), run: () => saveTab(tab) },
    { label: t("save_as") + "…", kbd: kbd("Ctrl+Shift+S"), run: () => saveAs(tab) },
    { label: t("save_all"), kbd: kbd("Ctrl+Alt+S"), run: () => saveAll() },
    { label: t("export"), icon: "export", sub: exportItems() },
    { label: t("print"), icon: "print", kbd: kbd("Ctrl+P"), run: () => printTab() },
    ...(isRunnable(tab) ? [{ label: t("run_button"), icon: "play", kbd: "F5", run: () => MN.run.start(tab) }] : []),
    "-",
    { label: t("menu_edit"), icon: "fileText", sub: editItems(v) },
    { label: t("menu_view"), icon: "eye", sub: viewItems() },
    { label: t("menu_tools"), icon: "wand", sub: toolItems(v) },
    { label: t("set_ai"), icon: "sparkle", run: () => aiMenu(anchor) },
    "-",
    { label: t("reveal"), icon: "folderOpen", disabled: !tab?.path, run: () => api("reveal", { path: tab.path }) },
    { label: t("settings") + "…", icon: "settings", kbd: kbd("Ctrl+,"), run: openSettings },
    { label: t("shortcuts"), icon: "keyboard", kbd: "F1", run: showShortcuts },
    { label: t("about"), icon: "info", run: about },
  ], anchor);
}
function kindByExt(p) { const e = extname(p); if ([".md", ".markdown"].includes(e)) return "markdown"; if ([".docx", ".odt", ".rtf"].includes(e)) return "docx"; if (e === ".pdf") return "pdf"; if ([".csv", ".tsv"].includes(e)) return "csv"; if (e === ".json") return "json"; if (e === ".log") return "log";
  if ([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".avif", ".svg", ".tif", ".tiff", ".psd", ".memuimg", ".heic"].includes(e)) return "image";
  if ([".xlsx", ".xlsm", ".ods"].includes(e)) return "sheet"; if ([".epub", ".fb2"].includes(e)) return "book"; if (e === ".ipynb") return "notebook"; if ([".pptx", ".pptm", ".ppsx", ".potx", ".odp"].includes(e)) return "slides"; return "text"; }
function editItems(v) {
  const dis = !v;
  return [
    { label: t("undo"), kbd: kbd("Ctrl+Z"), disabled: dis, run: () => CM.undo(v) },
    { label: t("redo"), kbd: kbd("Ctrl+Y"), disabled: dis, run: () => CM.redo(v) },
    "-",
    { label: t("find"), kbd: kbd("Ctrl+F"), run: () => openFind() },
    { label: t("replace"), kbd: kbd("Ctrl+H"), disabled: dis, run: () => openFind(true) },
    { label: t("goto_line"), kbd: kbd("Ctrl+G"), disabled: dis, run: gotoLine },
    { label: t("select_all"), kbd: kbd("Ctrl+A"), disabled: dis, run: () => CM.selectAll(v) },
    "-",
    { label: t("toggle_comment"), kbd: kbd("Ctrl+/"), disabled: dis, run: () => CM.toggleComment(v) },
    { label: t("duplicate_line"), kbd: kbd("Shift+Alt+↓"), disabled: dis, run: () => CM.copyLineDown(v) },
    { label: t("move_line_up"), kbd: kbd("Alt+↑"), disabled: dis, run: () => CM.moveLineUp(v) },
    { label: t("move_line_down"), kbd: kbd("Alt+↓"), disabled: dis, run: () => CM.moveLineDown(v) },
    { label: t("delete_line"), kbd: kbd("Ctrl+Shift+K"), disabled: dis, run: () => CM.deleteLine(v) },
    { label: t("insert_datetime"), kbd: "F5", disabled: dis, run: () => insertDateTime(v) },
  ];
}
function toolItems(v) {
  const dis = !v; const T = (k, fn) => ({ label: t(k), disabled: dis, run: () => { try { fn(v); } catch (e) { toast(e.message, { kind: "error" }); } } });
  return [
    { header: t("tools_lines") }, T("tool_sort_asc", TOOLS.sortAsc), T("tool_sort_desc", TOOLS.sortDesc), T("tool_sort_len", TOOLS.sortLength), T("tool_reverse", TOOLS.reverse), T("tool_shuffle", TOOLS.shuffle),
    T("tool_unique", TOOLS.unique), T("tool_remove_empty", TOOLS.removeEmpty), T("tool_trim", TOOLS.trimTrailing), T("tool_trim_lead", TOOLS.trimLeading), T("tool_join", TOOLS.join), T("tool_number", TOOLS.number),
    { header: t("tools_case") }, T("tool_upper", TOOLS.upper), T("tool_lower", TOOLS.lower), T("tool_title", TOOLS.title), T("tool_sentence", TOOLS.sentence), T("tool_invert", TOOLS.invert),
    { header: t("tools_encode") }, T("tool_b64e", TOOLS.base64enc), T("tool_b64d", TOOLS.base64dec), T("tool_urle", TOOLS.urlenc), T("tool_urld", TOOLS.urldec),
    { header: "Markdown" }, { label: t("md_format_table"), disabled: dis, run: () => mdAction("formatTable") },
  ];
}
function viewItems() {
  const s = MN.settings;
  const S = (k, key, extra = {}) => ({ label: t(k), checked: !!s[key], run: () => saveSettings({ [key]: !s[key] }).then(renderToolbar), ...extra });
  return [
    S("word_wrap", "word_wrap", { kbd: "Alt+Z" }), S("line_numbers", "line_numbers"), S("minimap", "minimap"), S("show_whitespace", "show_whitespace"), S("highlight_active_line", "highlight_active_line"),
    "-",
    { label: t("split_view"), checked: MN.panes.length > 1, kbd: kbd("Ctrl+\\"), run: () => toggleSplit() },
    { label: t("sidebar"), checked: !$("#sidebar").hidden, kbd: kbd("Ctrl+B"), run: () => toggleSidebar() },
    { label: t("ai_panel"), checked: !$("#ai-panel").hidden, kbd: kbd("Ctrl+Shift+L"), run: () => toggleAI() },
    S("toolbar", "show_toolbar"), S("statusbar", "show_statusbar"),
    "-",
    { label: t("theme"), icon: MN.theme.mode === "dark" ? "moon" : "sun", sub: [["auto", "theme_auto"], ["dark", "theme_dark"], ["light", "theme_light"]].map(([v, k]) => ({ label: t(k), checked: s.theme === v, run: () => saveSettings({ theme: v }) })) },
    { label: t("zoom_in"), kbd: kbd("Ctrl+="), run: () => zoom(10) }, { label: t("zoom_out"), kbd: kbd("Ctrl+-"), run: () => zoom(-10) }, { label: t("zoom_reset"), kbd: kbd("Ctrl+0"), run: () => zoom(0) },
    { label: t("fullscreen"), kbd: "F11", run: () => api("window", { cmd: "fullscreen" }) },
  ];
}
function editorMenu(at, prefix = []) {
  const tab = activeTab(); const v = viewOf(tab); if (!v) return;
  const sel = !v.state.selection.main.empty;
  showMenu([...prefix,
    ...(AI.online && MN.settings.ai_enabled ? [{ label: t("ai_command") + "…", icon: "sparkle", kbd: kbd("Ctrl+J"), run: () => aiInline() },
      ...(sel ? [{ label: t("ai_improve"), icon: "wand", run: () => runAICommand("improve") }, { label: t("ai_grammar"), icon: "check", run: () => runAICommand("grammar") }, { label: t("ai_explain"), icon: "info", run: () => runAICommand("explain") },
        { label: t("ai_translate"), icon: "translate", sub: COMMANDS.find((c) => c.id === "translate").params.map((p) => ({ label: t("lang_" + p), run: () => runAICommand("translate", p) })) }] : [{ label: t("ai_continue"), icon: "play", run: () => runAICommand("continue") }]), "-"] : []),
    { label: t("cut"), kbd: kbd("Ctrl+X"), disabled: !sel, run: () => { copyText(selectionText()); v.dispatch(v.state.replaceSelection("")); } },
    { label: t("copy"), kbd: kbd("Ctrl+C"), disabled: !sel, run: () => copyText(selectionText()) },
    { label: t("paste"), kbd: kbd("Ctrl+V"), run: async () => { try { const s = await navigator.clipboard.readText(); v.dispatch(v.state.replaceSelection(s)); } catch { toast(t("paste_blocked")); } } },
    "-",
    { label: t("menu_tools"), icon: "wand", sub: toolItems(v) },
    { label: t("ai_web_search_selection"), icon: "globe", disabled: !sel || !AI.online, run: () => webSearch(selectionText()) },
    { label: t("select_all"), kbd: kbd("Ctrl+A"), run: () => CM.selectAll(v) },
  ], at);
}

function exportItems() {
  return [["pdf", "PDF", "pdf"], ["html", "HTML", "globe"], ["docx", "Word (DOCX)", "doc"], ["odt", "OpenDocument (ODT)", "doc"], ["rtf", "Rich Text (RTF)", "doc"], ["md", "Markdown", "markdown"], ["txt", t("filter_text"), "fileText"]]
    .map(([f, label, ic]) => ({ label, icon: ic, run: () => exportTo(f) }));
}
function exportMenu(anchor) {
  if (activeTab()?.kind === "slides" && activeTab().comp?.cmd) { const c = activeTab().comp; showMenu([{ header: t("export_as") }, { label: t("sl_export_pdf"), icon: "pdf", run: () => c.cmd("exp:pdf") }, { label: t("sl_export_png"), icon: "image", run: () => c.cmd("exp:png") }, { label: t("sl_export_png_all"), icon: "image", run: () => c.cmd("exp:pngall") }], anchor, { alignRight: true }); return; }
  showMenu([{ header: t("export_as") }, ...exportItems(), "-", { label: t("print"), icon: "print", kbd: kbd("Ctrl+P"), run: printTab }], anchor, { alignRight: true }); }
function htmlForExport(tab) {
  if (DOC_KINDS.has(tab.kind)) return tab.doc ? tab.doc.html() : tab.html;
  const text = textOf(tab);
  if (tab.kind === "markdown") return renderMarkdown(text, tab.path, MN.sep);
  return `<pre class="plain">${esc(text)}</pre>`;
}
async function exportTo(fmt, path) {
  const tab = activeTab(); if (!tab) return;
  const base = (tab.path ? basename(tab.path) : tab.name).replace(/\.[^.]+$/, "");
  if (!path) {
    const filters = { pdf: ["PDF (*.pdf)"], html: ["HTML (*.html)"], docx: ["Word (*.docx)"], odt: ["OpenDocument (*.odt)"], rtf: ["Rich Text (*.rtf)"], md: ["Markdown (*.md)"], txt: [t("filter_text") + " (*.txt)"] }[fmt];
    const p = await fileDialog("save", { directory: tab.path ? dirname(tab.path) : "", filename: base + "." + fmt, filters, multiple: false });
    path = p?.[0]; if (!path) return;
    if (!extname(path)) path += "." + fmt;
  }
  try {
    if (fmt === "pdf") { await printTab(path); return; }
    const isDoc = DOC_KINDS.has(tab.kind);
    const body = { path, format: fmt, title: base };
    if (fmt === "md") body[isDoc ? "html" : "text"] = isDoc ? htmlForExport(tab) : textOf(tab);
    else if (fmt === "txt") body[isDoc ? "html" : "text"] = isDoc ? htmlForExport(tab) : textOf(tab);
    else body.html = htmlForExport(tab);
    await api("export", body);
    toast(t("exported", { name: basename(path) }), { kind: "ok", action: { label: t("open"), run: () => openPath(path) } });
  } catch (e) { toast(t("export_failed") + ": " + e.message, { kind: "error" }); }
}
async function printTab(pdfPath) {
  const tab = activeTab(); if (!tab) return;
  if (tab.kind === "pdf") { toast(t("print_pdf_hint")); return; }
  const root = $("#print-root");
  root.innerHTML = htmlForExport(tab);
  document.documentElement.classList.add("printing");
  try {
    if (pdfPath && MN.boot.desktop) {
      const r = await api("export", { format: "pdf", path: pdfPath }, { allowError: true });
      if (r.ok) toast(t("exported", { name: basename(pdfPath) }), { kind: "ok" });
      else { toast(t("pdf_fallback")); window.print(); }
    } else window.print();
  } finally { document.documentElement.classList.remove("printing"); root.innerHTML = ""; }
}

function commandItems() {
  const tab = activeTab(); const v = tab && viewOf(tab);
  const items = [
    ["new_tab", "plus", "Ctrl+N", () => newTab()], ["new_presentation", "slides", "", () => newPresentation()], ["open_file", "folderOpen", "Ctrl+O", openDialog], ["save", "save", "Ctrl+S", () => saveTab(tab)], ["save_as", "save", "Ctrl+Shift+S", () => saveAs(tab)],
    ["save_all", "save", "Ctrl+Alt+S", () => saveAll()], ["close_tab", "close", "Ctrl+W", () => tab && closeTab(tab.id)], ["reopen_closed", "retry", "Ctrl+Shift+T", reopenClosed],
    ["find", "search", "Ctrl+F", () => openFind()], ["replace", "replace", "Ctrl+H", () => openFind(true)], ["goto_line", "lines", "Ctrl+G", gotoLine],
    ["word_wrap", "wrap", "Alt+Z", () => saveSettings({ word_wrap: !MN.settings.word_wrap }).then(renderToolbar)], ["split_view", "split", "Ctrl+\\", () => toggleSplit()],
    ["sidebar", "sidebar", "Ctrl+B", () => toggleSidebar()], ["ai_panel", "sparkle", "Ctrl+Shift+L", () => toggleAI()], ["ai_command", "sparkle", "Ctrl+J", () => aiInline()],
    ["settings", "settings", "Ctrl+,", openSettings], ["print", "print", "Ctrl+P", () => printTab()], ["shortcuts", "keyboard", "F1", showShortcuts],
    ["zoom_in", "zoomIn", "Ctrl+=", () => zoom(10)], ["zoom_out", "zoomOut", "Ctrl+-", () => zoom(-10)], ["zoom_reset", "search", "Ctrl+0", () => zoom(0)],
    ["theme_toggle", "moon", "", () => saveSettings({ theme: MN.theme.mode === "dark" ? "light" : "dark" })], ["md_convert_cmd", "markdown", "", () => tab && convertToMarkdown(tab)],
    ["web_search", "globe", "", () => webSearch(selectionText())], ["md_format_table", "table", "", () => mdAction("formatTable")], ["insert_datetime", "clock", "F5", () => v && insertDateTime(v)],
    ["reveal", "folderOpen", "", () => tab?.path && api("reveal", { path: tab.path })], ["copy_path", "copy", "", () => tab?.path && copyText(tab.path)],

    ["run_button", "play", "F5", () => MN.run.start(tab)], ["run_with_args", "terminal", "", () => MN.run.start(tab, { askArgs: true })], ["run_stop", "stop", "Shift+F5", () => MN.run.stop()],
    ["run_toggle_panel", "terminal", "Ctrl+`", () => MN.run.toggle()], ["st_spell", "spell", "", () => toggleSpell(tab)],
    ["md_mode_live", "liveMd", "", () => setMdMode("live")],
  ].map(([k, ic, key, run]) => ({ label: t(k), icon: ic, kbd: key ? kbd(key) : "", run }));
  for (const c of COMMANDS) items.push({ label: t("set_ai") + ": " + t(c.label), icon: c.icon, run: () => runAICommand(c.id, c.params?.[0] && c.id === "translate" ? (I18N.lang === "ru" ? "en" : "ru") : undefined) });
  for (const [k, fn] of Object.entries({ tool_sort_asc: TOOLS.sortAsc, tool_sort_desc: TOOLS.sortDesc, tool_unique: TOOLS.unique, tool_trim: TOOLS.trimTrailing, tool_upper: TOOLS.upper, tool_lower: TOOLS.lower, tool_title: TOOLS.title, tool_remove_empty: TOOLS.removeEmpty, tool_join: TOOLS.join }))
    items.push({ label: t("tools") + ": " + t(k), icon: "wand", run: () => v && fn(v) });
  for (const f of ["pdf", "html", "docx", "odt", "rtf", "md", "txt"]) items.push({ label: t("export") + ": " + f.toUpperCase(), icon: "export", run: () => exportTo(f) });
  return items;
}
function commandPalette() { palette(commandItems(), { placeholder: t("palette_placeholder"), icon: "palette" }); }
function quickOpen() {
  const items = [...MN.tabs.map((x) => ({ label: x.name, hint: t("open_tab"), icon: kindIcon(x.kind), run: () => activate(x.id) })),
    ...MN.recent.filter((p) => !MN.tabs.some((x) => x.path === p)).map((p) => ({ label: basename(p), hint: dirname(p), icon: kindIcon(kindByExt(p)), keywords: p, run: () => openPath(p) }))];
  palette(items, { placeholder: t("quick_open_placeholder"), icon: "search", dynamic: (q) => (/^([a-zA-Z]:[\\/]|\/|\\\\)/.test(q) ? [{ label: t("open_path", { p: q }), icon: "folderOpen", run: () => openPath(q) }] : null) });
}
function showShortcuts() {
  const rows = [["Ctrl+N", "new_tab"], ["Ctrl+O", "open_file"], ["Ctrl+S", "save"], ["Ctrl+Shift+S", "save_as"], ["Ctrl+W", "close_tab"], ["Ctrl+Shift+T", "reopen_closed"], ["Ctrl+Tab", "next_tab"],
    ["Ctrl+F", "find"], ["Ctrl+H", "replace"], ["F3 / Shift+F3", "find_next_prev"], ["Ctrl+G", "goto_line"], ["Ctrl+D", "select_next"], ["Alt+Click", "multi_cursor"], ["Ctrl+/", "toggle_comment"],
    ["Alt+↑ / Alt+↓", "move_line"], ["Shift+Alt+↓", "duplicate_line"], ["Ctrl+Shift+K", "delete_line"], ["Alt+Z", "word_wrap"], ["Ctrl+\\", "split_view"], ["Ctrl+B", "fmt_bold_or_sidebar"],
    ["Ctrl+I", "fmt_italic"], ["Ctrl+K", "fmt_link"], ["Ctrl+Alt+1…3", "fmt_heading"], ["Ctrl+Shift+V", "md_toggle_preview"], ["Ctrl+J / Ctrl+Enter", "ai_command"], ["Ctrl+Shift+L", "ai_panel"],
    ["Tab", "ai_accept_ghost"], ["F5", "sc_run"], ["Shift+F5", "sc_stop"], ["Ctrl+`", "sc_run_panel"], ["Ctrl+Space", "sc_complete"], ["Ctrl+P", "quick_open"], ["Ctrl+Shift+P", "command_palette"], ["Ctrl+= / Ctrl+-", "zoom"], ["F11", "fullscreen"], ["Ctrl+,", "settings"]];
  modal({ title: t("shortcuts"), wide: true, body: `<div class="shortcuts">${rows.map(([k, l]) => `<div><kbd>${esc(kbd(k))}</kbd><span>${esc(t(l))}</span></div>`).join("")}</div>`, buttons: [{ label: t("close"), primary: true, value: true }] });
}
function about() {
  modal({ title: "MemuNote", body: `<div class="about"><img class="logo big" src="memunote.svg" alt=""><div><h3>MemuNote ${esc(MN.boot.version || "")}</h3><p>${esc(t("about_text"))}</p><p class="muted small">${esc(t("about_libs"))}</p></div></div>`, buttons: [{ label: t("close"), primary: true, value: true }] });
}

function globalKeys(e) {
  const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  const inEditor = !!e.target.closest?.(".cm-editor");
  const inDoc = !!e.target.closest?.(".doc-page");
  const inField = !inEditor && !inDoc && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  const tab = activeTab();
  const stop = () => { e.preventDefault(); e.stopPropagation(); };
  if (document.querySelector(".modal-back, .palette-back")) return;
  if (e.target.closest?.(".sl-app, .sl-show") && slidesOwnsKey(e)) return;
  if (tab?.kind === "image" && tab.comp?.isPhoto && tab.comp.handleKey(e)) return;
  if (mod && !e.altKey) {
    if (k === "n" && !e.shiftKey) { stop(); newTab(); return; }
    if (k === "o") { stop(); openDialog(); return; }
    if (k === "s") { stop(); if (e.shiftKey) saveAs(tab); else saveTab(tab); return; }
    if (k === "w") { stop(); if (tab) closeTab(tab.id); return; }
    if (k === "t" && e.shiftKey) { stop(); reopenClosed(); return; }
    if (k === "p") { stop(); if (e.shiftKey) commandPalette(); else if (!inField) quickOpen(); return; }
    if (k === "tab") { stop(); const i = MN.tabs.indexOf(tab); const n = MN.tabs[(i + (e.shiftKey ? -1 : 1) + MN.tabs.length) % MN.tabs.length]; if (n) activate(n.id); return; }
    if (/^[1-9]$/.test(k) && !inField) { stop(); const n = k === "9" ? MN.tabs[MN.tabs.length - 1] : MN.tabs[+k - 1]; if (n) activate(n.id); return; }
    if (k === "g" && !e.shiftKey) { stop(); gotoLine(); return; }
    if (k === "h" && !e.shiftKey) { stop(); openFind(true); return; }
    if (k === "f" && inDoc) { stop(); openFind(); return; }
    if (k === "j") { stop(); aiInline(); return; }
    if (k === "l" && e.shiftKey) { stop(); toggleAI(); return; }
    if (k === "\\") { stop(); toggleSplit(); return; }
    if (k === "`" && !e.shiftKey) { stop(); MN.run?.toggle(); return; }
    if (k === "," ) { stop(); openSettings(); return; }
    if (k === "=" || k === "+") { stop(); zoom(10); return; }
    if (k === "-") { stop(); zoom(-10); return; }
    if (k === "0" && !inField) { stop(); zoom(0); return; }
    if (k === "v" && e.shiftKey && tab?.kind === "markdown") { stop(); setMdMode((tab.mdMode || MN.settings.markdown_preview) === "off" ? "split" : (tab.mdMode || MN.settings.markdown_preview) === "split" ? "preview" : "off"); return; }
    if (inEditor && tab && (tab.kind === "markdown" || tab.kind === "text")) {
      if (k === "b" && !e.shiftKey) { stop(); mdAction("bold"); return; }
      if (k === "i" && !e.shiftKey) { stop(); mdAction("italic"); return; }
      if (k === "k" && !e.shiftKey) { stop(); mdAction("link"); return; }
    }
    if (k === "b" && !inEditor && !inDoc) { stop(); toggleSidebar(); return; }
  }
  if (mod && e.altKey) {
    if (k === "s") { stop(); saveAll(); return; }
    if (/^[1-6]$/.test(k) && inEditor) { stop(); (async () => { if (await ensureMarkdown(tab)) mdHeading(viewOf(tab), +k); })(); return; }
  }
  if (e.altKey && !mod && k === "z") { stop(); saveSettings({ word_wrap: !MN.settings.word_wrap }).then(renderToolbar); return; }
  if (e.key === "F1") { stop(); showShortcuts(); return; }
  if (e.key === "F5" && e.shiftKey && !mod && MN.run?.run && !MN.run.run.done) { stop(); MN.run.stop(); return; }
  if (e.key === "F5" && !e.shiftKey && !mod && isRunnable(tab) && !e.target.closest?.(".sl-app, .sl-show")) { stop(); MN.run.start(tab); return; }
  if (e.key === "F5" && inEditor) { stop(); insertDateTime(viewOf(tab)); return; }
  if (e.key === "F11") { stop(); api("window", { cmd: "fullscreen" }); return; }
  if (e.key === "Escape") closeMenus();
}

function openSettings() {
  const s = MN.settings;
  const tog = (key, label, hint = "") => `<label class="srow"><span><b>${esc(t(label))}</b>${hint ? `<small>${esc(t(hint))}</small>` : ""}</span><input type="checkbox" class="switch" data-k="${key}" ${s[key] ? "checked" : ""}></label>`;
  const sel = (key, label, options, hint = "") => `<label class="srow"><span><b>${esc(t(label))}</b>${hint ? `<small>${esc(t(hint))}</small>` : ""}</span><select class="input sm" data-k="${key}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(s[key]) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  const num = (key, label, min, max, step = 1, hint = "") => `<label class="srow"><span><b>${esc(t(label))}</b>${hint ? `<small>${esc(t(hint))}</small>` : ""}</span><input type="number" class="input sm num" data-k="${key}" value="${s[key]}" min="${min}" max="${max}" step="${step}"></label>`;
  const sections = {
    general: [t("set_general"), "settings", sel("language", "set_language", [["auto", t("follow_memuai")], ["ru", "Русский"], ["en", "English"]])
      + sel("theme", "theme", [["auto", t("theme_auto")], ["dark", t("theme_dark")], ["light", t("theme_light")]])
      + tog("restore_session", "set_restore", "set_restore_hint") + tog("recovery", "set_recovery", "set_recovery_hint")
      + sel("autosave", "set_autosave", [["off", t("autosave_off")], ["focus", t("autosave_focus")], ["delay", t("autosave_delay")]]) + num("autosave_delay", "set_autosave_delay", 500, 60000, 500)
      + tog("show_toolbar", "toolbar") + tog("show_statusbar", "statusbar") + tog("compact_tabs", "set_compact_tabs")],
    editor: [t("set_editor"), "code", sel("font_family", "set_font", MN.boot.fonts.map((f) => [f, f])) + num("font_size", "set_font_size", 8, 40) + num("line_height", "set_line_height", 1, 2.6, 0.05)
      + tog("word_wrap", "word_wrap") + tog("line_numbers", "line_numbers") + tog("minimap", "minimap") + tog("highlight_active_line", "highlight_active_line") + tog("show_whitespace", "show_whitespace")
      + tog("indent_with_tabs", "indent_tabs") + num("tab_size", "set_tab_size", 1, 8) + tog("auto_close_brackets", "set_brackets") + tog("bracket_matching", "set_bracket_match")
      + tog("fold_gutter", "set_fold") + tog("scroll_past_end", "set_scroll_past") + tog("highlight_selection_matches", "set_sel_matches") + tog("spell_check", "set_spell_check", "set_spell_check_hint") + tog("code_autocomplete", "set_code_autocomplete", "set_code_autocomplete_hint")],
    files: [t("set_files"), "file", sel("default_encoding", "set_default_enc", ENCODINGS.map(([k, l]) => [k, l])) + sel("default_eol", "set_default_eol", [["crlf", "CRLF (Windows)"], ["lf", "LF (Unix)"], ["cr", "CR"]])
      + tog("trim_trailing_whitespace", "set_trim") + tog("final_newline", "set_final_nl") + tog("backup_on_save", "set_backup", "set_backup_hint")
      + num("large_file_mb", "set_large", 1, 512, 1, "set_large_hint") + tog("detect_external_changes", "set_external")
      + sel("markdown_preview", "set_md_preview", [["live", t("md_mode_live")], ["split", t("md_mode_split")], ["off", t("md_mode_source")], ["preview", t("md_mode_preview")]])
      + tog("ask_markdown_convert", "set_ask_md") + tog("rich_save_warning", "set_rich_warn")
      + `<div class="srow"><span><b>${esc(t("set_open_with"))}</b><small>${esc(t("set_open_with_hint"))}</small></span><span class="ow-state muted small">…</span></div>`],
    ai: [t("set_ai"), "sparkle", tog("ai_enabled", "set_ai_enabled", "set_ai_enabled_hint")
      + `<label class="srow"><span><b>${esc(t("ai_model"))}</b><small>${esc(AI.online ? t("ai_online") : t("ai_offline"))}</small></span><select class="input sm" data-k="ai_model"><option value="">${esc(t("ai_model_default"))}</option>${AI.models.map((m) => `<option value="${esc(m.id)}" ${s.ai_model === m.id ? "selected" : ""}>${esc(m.label && m.label !== m.id ? `${m.label} · ${m.id}` : m.id)}</option>`).join("")}</select></label>`
      + tog("ai_web_search", "set_ai_web") + tog("ai_memory", "set_ai_memory", "set_ai_memory_hint") + tog("ai_memory_write", "set_ai_memory_write", "set_ai_memory_write_hint")
      + tog("ai_autocomplete", "set_ai_autocomplete", "set_ai_autocomplete_hint") + num("ai_autocomplete_delay", "set_ai_autocomplete_delay", 300, 5000, 100)
      + num("ai_context_chars", "set_ai_context", 2000, 400000, 1000, "set_ai_context_hint")
      + `<label class="srow"><span><b>${esc(t("set_proxy_url"))}</b><small>${esc(t("set_proxy_url_hint"))}</small></span><input class="input sm" data-k="proxy_url" value="${esc(s.proxy_url)}" placeholder="http://127.0.0.1:8317" spellcheck="false"></label>`],
  };
  const nav = Object.entries(sections).map(([id, [title, ic]], i) => `<button class="snav ${i === 0 ? "on" : ""}" data-sec="${id}">${icon(ic)}${esc(title)}</button>`).join("");
  const panes = Object.entries(sections).map(([id, [title, , body]], i) => `<section class="spane" data-sec="${id}" ${i ? "hidden" : ""}><h3>${esc(title)}</h3>${body}</section>`).join("");
  modal({ title: t("settings"), wide: true, cls: "settings", body: `<div class="settings-grid"><nav>${nav}</nav><div class="settings-body">${panes}</div></div>`, buttons: [{ label: t("done"), primary: true, value: true }],
    onOpen: async (box) => {
      box.querySelectorAll(".snav").forEach((b) => (b.onclick = () => { box.querySelectorAll(".snav").forEach((x) => x.classList.toggle("on", x === b)); box.querySelectorAll(".spane").forEach((p) => (p.hidden = p.dataset.sec !== b.dataset.sec)); }));
      box.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("change", async () => {
        const key = el.dataset.k; let value = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
        const saved = await saveSettings({ [key]: value });
        if (el.type === "number") el.value = saved[key];
        if (key === "ai_model") { AI.model = value || AI.status?.default_model || AI.model; refreshStatus(); }
        if (key === "proxy_url" || key === "ai_enabled") refreshStatus(true);
        renderToolbar();
      }));
      try {
        const ow = await api("openwith");
        const host = box.querySelector(".ow-state");
        if (!ow.supported) host.textContent = t("set_open_with_unsupported");
        else {
          host.innerHTML = `<input type="checkbox" class="switch" ${ow.enabled ? "checked" : ""}>`;
          const hint = host.parentElement?.querySelector("small"); if (hint && ow.extensions?.length) hint.insertAdjacentHTML("beforeend", `<br><span class="ow-types">${esc(t("set_open_with_types", { list: ow.extensions.join(" ") }))}</span>`);
          host.querySelector("input").onchange = async (e) => {
            try { await api("openwith", { enable: e.target.checked }); toast(t(e.target.checked ? "open_with_on" : "open_with_off"), { kind: "ok" }); }
            catch (err) { e.target.checked = !e.target.checked; toast(err.message, { kind: "error" }); }
          }; }
      } catch {  }
    } });
}

boot();
