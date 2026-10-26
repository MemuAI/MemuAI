

import { t } from "./i18n.js";

const root = document.documentElement;
const NO_DRAG = "button, a[href], input, textarea, select, [role=tab], [role=menu], [role=menuitem], [draggable='true'], .tab, .memu-wc, .memu-tb-resize, [data-no-drag]";
const ICONS = {
  minimize: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M0 5.5h10"/></svg>',
  maximize: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y=".5" width="9" height="9" rx="1.5"/></svg>',
  restore: '<svg viewBox="0 0 10 10" aria-hidden="true"><rect x=".5" y="2.5" width="7" height="7" rx="1.25"/>' +
    '<path d="M2.5 2.5V2A1.5 1.5 0 0 1 4 .5h4A1.5 1.5 0 0 1 9.5 2v4A1.5 1.5 0 0 1 8 7.5h-.5"/></svg>',
  close: '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M.5.5l9 9M9.5.5l-9 9"/></svg>',
};

let state = null, enabled = false, controls = null, edges = null, bar = null, lastPress = null, stateTimer = 0, booted = false, tries = 0;
const bridge = () => (window.pywebview && window.pywebview.api) || null;
const has = (name) => { const a = bridge(); return !!(a && typeof a[name] === "function"); };
async function call(name, ...args) {
  if (!has(name)) return null;
  try { return await bridge()[name](...args); } catch (e) { return null; }
}

function build() {
  if (controls) return;
  controls = document.createElement("div");
  controls.className = "memu-wc";
  controls.setAttribute("role", "group");
  controls.innerHTML = ["minimize", "maximize", "close"].map((kind) =>
    `<button type="button" class="memu-wc-btn memu-wc-${kind}" data-wc="${kind}" tabindex="-1">` +
    (kind === "maximize" ? `<span class="wc-ico max">${ICONS.maximize}</span><span class="wc-ico restore">${ICONS.restore}</span>`
      : `<span class="wc-ico">${ICONS[kind]}</span>`) + "</button>").join("");
  controls.addEventListener("mousedown", (e) => { e.stopPropagation(); if (e.button === 0) e.preventDefault(); });
  controls.addEventListener("dblclick", (e) => e.stopPropagation());
  controls.addEventListener("click", (e) => {
    const b = e.target.closest("[data-wc]");
    if (!b) return;
    call("titlebar_command", b.dataset.wc === "maximize" ? "toggle" : b.dataset.wc);
  });
  controls.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); call("titlebar_system_menu", "mouse"); });
  edges = document.createElement("div");
  edges.className = "memu-tb-resize";
  edges.setAttribute("aria-hidden", "true");
  edges.innerHTML = '<i data-edge="topleft"></i><i data-edge="top"></i><i data-edge="topright"></i>';
  edges.addEventListener("mousedown", (e) => {
    const edge = e.target.closest("[data-edge]");
    if (!edge || e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    call("titlebar_drag", edge.dataset.edge);
  });
  document.body.append(edges);
}

export function mountControls(tabbar) {
  bar = tabbar;
  if (enabled && controls && bar && controls.parentElement !== bar) bar.append(controls);
}

function labels() {
  if (!controls) return;
  const max = state && state.maximized;
  const text = { minimize: t("wc.minimize"), maximize: max ? t("wc.restore") : t("wc.maximize"), close: t("wc.close") };
  controls.setAttribute("aria-label", t("wc.group"));
  controls.querySelectorAll("[data-wc]").forEach((b) => { b.title = text[b.dataset.wc]; b.setAttribute("aria-label", text[b.dataset.wc]); });
}

export function isDragTarget(target) {
  if (!enabled || !bar || !(target instanceof Element) || !bar.contains(target)) return false;
  return !target.closest(NO_DRAG);
}

function onPointerDown(e) {
  if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || !isDragTarget(e.target)) return;
  e.preventDefault();
  const now = performance.now();
  const limit = (state && state.doubleClickMs) || 500;
  if (lastPress && now - lastPress.t <= limit && Math.abs(e.screenX - lastPress.x) <= 4 && Math.abs(e.screenY - lastPress.y) <= 4) {
    lastPress = null;
    call("titlebar_command", "toggle");
    return;
  }
  lastPress = { t: now, x: e.screenX, y: e.screenY };
  call("titlebar_drag", "caption");
}
function onContextMenu(e) {
  if (!isDragTarget(e.target)) return;
  e.preventDefault(); e.stopPropagation();
  call("titlebar_system_menu", "mouse");
}
function onKeyDown(e) {
  if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && (e.code === "Space" || e.key === " ")) {
    e.preventDefault();
    call("titlebar_system_menu", "keyboard");
  }
}
function applyFlags() {
  const s = state || {};
  root.dataset.titlebarMax = s.maximized ? "true" : "false";
  root.dataset.titlebarActive = s.active === false ? "false" : "true";
  if (s.os) root.dataset.titlebarOs = s.os;
  labels();
}
function onResize() { clearTimeout(stateTimer); stateTimer = setTimeout(sync, 80); }
function onFocus() {
  setTimeout(() => {
    if (!state) return;
    const active = document.hasFocus();
    if (active !== (state.active !== false)) { state = { ...state, active }; applyFlags(); }
    if (active) sync();
  }, 0);
}

function enable() {
  if (enabled) return;
  build();
  enabled = true;
  root.dataset.titlebar = "custom";
  if (bar) bar.append(controls);
  document.addEventListener("mousedown", onPointerDown, true);
  document.addEventListener("contextmenu", onContextMenu, true);
  document.addEventListener("keydown", onKeyDown, true);
  window.addEventListener("resize", onResize);
  window.addEventListener("focus", onFocus);
  window.addEventListener("blur", onFocus);
}
function disable() {
  if (!enabled) return;
  enabled = false;
  ["titlebar", "titlebarMax", "titlebarActive", "titlebarOs"].forEach((k) => delete root.dataset[k]);
  document.removeEventListener("mousedown", onPointerDown, true);
  document.removeEventListener("contextmenu", onContextMenu, true);
  document.removeEventListener("keydown", onKeyDown, true);
  window.removeEventListener("resize", onResize);
  window.removeEventListener("focus", onFocus);
  window.removeEventListener("blur", onFocus);
  if (controls) controls.remove();
  if (edges) edges.remove();
  controls = edges = null;
}
function apply(next) {
  if (!next || typeof next !== "object") return;
  state = next;
  if (next.installed) enable(); else disable();
  if (enabled) applyFlags();
}
export async function sync() {
  const next = await call("titlebar_state");
  if (next) apply(next);
  if (next && next.pending && tries < 60) { tries += 1; setTimeout(sync, 250); }
  return next;
}

export async function setMode(custom) {
  const next = await call("titlebar_set_mode", custom ? "custom" : "native");
  if (next) apply(next);
  return next;
}
export const available = () => has("titlebar_state") && !!(state ? state.supported : true);
export const active = () => enabled;

function boot() {
  if (booted || !has("titlebar_state")) return;
  booted = true;
  sync();
}
export function initTitlebar(tabbar) {
  mountControls(tabbar);
  window.addEventListener("pywebviewready", boot);
  boot();
}
