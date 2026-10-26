
import { t } from "./i18n.js";
import { S, api, $, $$, h, esc, icon, fileIcon, folderSvg, fmtSize, fmtNum, fmtDate, itemsWord, baseName, parentPath,
  toast, showError, modal, isWin } from "./core.js";
import { runJob } from "./dialogs.js";
import { SPECIAL } from "./pane.js";
import * as extras from "./extras.js";

export function renderHome(app, pane, el) {
  el.innerHTML = "";
  const hero = h("div", { class: "home-hero", html: `<img class="logo" src="memufiles.svg" alt=""><div><b>${esc(t("home.title"))}</b><span>${esc(t("home.subtitle"))}</span></div>` });
  el.append(hero);
  const card = (ico, title, sub, path, opts = {}) => {
    const b = h("button", { class: "home-card", title: path || title });
    b.innerHTML = `<span class="hc-ico${opts.folder ? " folder" : ""}">${opts.folder ? `<span class="ic">${folderSvg()}</span>` : icon(ico)}</span><span class="hc-txt"><b>${esc(title)}</b><span>${esc(sub || "")}</span>${opts.bar !== undefined ? `<span class="bar"><i style="width:${(opts.bar * 100).toFixed(1)}%;${opts.bar > 0.9 ? "background:var(--danger)" : ""}"></i></span>` : ""}</span>`;
    b.addEventListener("click", () => (opts.run ? opts.run() : pane.navigate(path)));
    b.addEventListener("mousedown", (e) => { if (e.button === 1 && path) { e.preventDefault(); app.newTab(path); } });
    b.addEventListener("contextmenu", (e) => { e.preventDefault(); if (path) app.folderMenu(path, e.clientX, e.clientY); });
    if (path && !opts.noDrop) {
      b.addEventListener("dragover", (e) => app.dragOver(e, path, b));
      b.addEventListener("dragleave", () => b.classList.remove("drop"));
      b.addEventListener("drop", (e) => { b.classList.remove("drop"); app.drop(e, path); });
    }
    return b;
  };
  const icons = { home: "home", desktop: "desktop", documents: "documents", downloads: "downloads", pictures: "pictures", music: "music", videos: "videos" };
  const places = (app.sidebar.places || []).filter((p) => p.id !== "onedrive" && p.exists !== false);
  if (places.length) {
    el.append(h("h3", { text: t("home.libraries") }));
    const grid = h("div", { class: "home-grid" });
    for (const p of places) grid.append(card(icons[p.id] || "folder", t("place." + p.id), p.path, p.path));
    el.append(grid);
  }
  const pins = app.sidebar.pinned || [];
  if (pins.length) {
    el.append(h("h3", { text: t("home.pinned") }));
    const grid = h("div", { class: "home-grid" });
    for (const p of pins) grid.append(card("folder", baseName(p.path) || p.path, p.path, p.path, { folder: true }));
    el.append(grid);
  }
  el.append(h("h3", { text: t("home.drives") }));
  const dg = h("div", { class: "home-grid" });
  for (const d of app.sidebar.drives || []) {
    const used = d.total ? (d.total - d.free) / d.total : 0;
    const label = isWin() ? (d.label ? `${d.label} (${d.path.replace(/\\$/, "")})` : `${t("drive.local")} (${d.path.replace(/\\$/, "")})`) : (d.path === "/" ? t("drive.root") : d.label);
    dg.append(card(d.kind === "removable" ? "usb" : d.kind === "network" ? "network" : "drive", label, d.total ? t("drive.free", { free: fmtSize(d.free), total: fmtSize(d.total) }) : t("drive.not_ready"), d.path, { bar: d.total ? used : undefined }));
  }
  el.append(dg);
  el.append(h("h3", { text: t("home.tools") }));
  const tg = h("div", { class: "home-grid" });
  tg.append(card("trash", t("side.trash"), t("home.trash_sub"), "", { run: () => pane.navigate(SPECIAL.TRASH), noDrop: true }));
  tg.append(card("chart", t("tools.usage"), t("home.usage_sub"), "", { run: () => usageDialog(app, S.home), noDrop: true }));
  tg.append(card("dupes", t("tools.dupes"), t("home.dupes_sub"), "", { run: () => dupesDialog(app, S.home), noDrop: true }));
  tg.append(card("sparkle", t("cmd.ai"), t("home.ai_sub"), "", { run: () => app.setRight("ai"), noDrop: true }));
  el.append(tg);
  extras.renderHomeExtras(app, pane, el);
}

const COLORS = ["#ef8a62", "#a76cf0", "#4d95f0", "#4cbf74", "#e8c43d", "#d6537a", "#5ab3a7", "#c9944a", "#8c8c96"];
export function usageDialog(app, path) {
  const body = h("div", { class: "tool-wrap" });
  const holder = {};
  let current = path;
  const dlg = modal({ title: t("tools.usage"), icon: "chart", body, xwide: true, buttons: [{ label: t("common.close"), cls: "primary", primary: true }],
    onClose: () => { if (holder.id) api("jobs/cancel", { id: holder.id }).catch(() => {}); } });
  const load = async (target) => {
    current = target;
    body.innerHTML = "";
    const bar = h("div", { class: "tool-bar" });
    const upBtn = h("button", { class: "btn sm", html: icon("up") + esc(t("nav.up")), disabled: !parentPath(target) });
    upBtn.addEventListener("click", () => load(parentPath(target)));
    const openBtn = h("button", { class: "btn sm ghost", html: icon("open") + esc(t("tools.open_here")) });
    openBtn.addEventListener("click", () => { dlg.close(); app.pane().navigate(target); });
    bar.append(upBtn, h("b", { text: target, style: { flex: "1", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } }), openBtn);
    const prog = h("div", { class: "job-progress", html: `<span class="spinner"></span><span>${esc(t("tools.scanning"))}</span>` });
    body.append(bar, prog);
    const res = await runJob("usage", { path: target }, (p) => { const s = prog.querySelector("span:last-child"); if (s) s.textContent = t("tools.scanning") + " " + fmtSize(p.bytes || 0) + (p.current ? " · " + p.current : ""); }, holder);
    if (current !== target || !document.body.contains(body)) return;
    prog.remove();
    if (!res) return;
    const total = res.size || 1;

    const map = extras.treemap(res.children, total, COLORS, (c) => (c.is_dir ? load(c.path) : (dlg.close(), app.revealPath(c.path))));
    const summary = h("div", { class: "muted small", text: t("tools.usage_summary", { size: fmtSize(res.size), n: fmtNum(res.children.length) }) + (res.disk && res.disk.total ? " · " + t("drive.free", { free: fmtSize(res.disk.free), total: fmtSize(res.disk.total) }) : "") });
    const list = h("div", { class: "usage-list" });
    for (const c of res.children) {
      const pct = c.size / total;
      const row = h("div", { class: "u-row", title: c.path });
      row.innerHTML = `${c.is_dir ? `<span class="fi">${folderSvg()}</span>` : fileIcon(c.name, false)}<span class="nm">${esc(c.name)}</span><span class="sz">${esc(fmtSize(c.size))}</span><span class="ubar"><i style="width:${(pct * 100).toFixed(1)}%"></i></span><span class="pc">${(pct * 100).toFixed(1)}%</span>`;
      row.addEventListener("dblclick", () => (c.is_dir ? load(c.path) : (dlg.close(), app.revealPath(c.path))));
      row.addEventListener("click", () => { if (c.is_dir) load(c.path); });
      row.addEventListener("contextmenu", (e) => { e.preventDefault(); dlg.close(); app.revealPath(c.path); });
      list.append(row);
    }
    body.append(map, summary, list);
  };
  load(path);
}

export function dupesDialog(app, path) {
  const body = h("div", { class: "tool-wrap" });
  const holder = {};
  const footInfo = h("span", { class: "muted small" });
  let checked = new Set();
  let trashBtn;
  const dlg = modal({ title: t("tools.dupes"), icon: "dupes", body, xwide: true, left: footInfo,
    buttons: [{ label: t("common.close"), cls: "ghost" }, { label: t("dupes.trash"), cls: "danger", run: async () => {
      if (!checked.size) return false;
      try { const res = await api("ops/submit", { kind: "trash", sources: [...checked] }); app.trackOp(res.op); toast(t("dupes.trashed", { n: itemsWord(checked.size) })); }
      catch (err) { showError(err); return false; }
      return true;
    } }],
    onClose: () => { if (holder.id) api("jobs/cancel", { id: holder.id }).catch(() => {}); } });
  trashBtn = $$(".btn.danger", dlg.foot).pop();
  trashBtn.disabled = true;
  const bar = h("div", { class: "tool-bar", html: `<b style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(path)}</b>` });
  const keepNewest = h("button", { class: "btn sm", text: t("dupes.keep_oldest") });
  const clearSel = h("button", { class: "btn sm ghost", text: t("sel.none") });
  bar.append(keepNewest, clearSel);
  const prog = h("div", { class: "job-progress", html: `<span class="spinner"></span><span>${esc(t("tools.scanning"))}</span>` });
  const list = h("div", { style: { flex: "1", overflow: "auto" } });
  body.append(bar, prog, list);
  const update = () => {
    let size = 0;
    for (const g of groups) for (const f of g.files) if (checked.has(f.path)) size += g.size;
    footInfo.textContent = checked.size ? t("dupes.selected", { n: itemsWord(checked.size), size: fmtSize(size) }) : "";
    trashBtn.disabled = !checked.size;
    trashBtn.textContent = checked.size ? t("dupes.trash_n", { n: fmtNum(checked.size) }) : t("dupes.trash");
  };
  let groups = [];
  const render = () => {
    list.innerHTML = "";
    for (const g of groups.slice(0, 400)) {
      const box = h("div", { class: "dup-group" });
      box.append(h("div", { class: "dup-head", html: `${fileIcon(baseName(g.files[0].path), false)}<b>${esc(baseName(g.files[0].path))}</b><span class="muted">${esc(fmtSize(g.size))} × ${g.files.length} · ${esc(t("dupes.wasted", { size: fmtSize(g.wasted) }))}</span>` }));
      g.files.forEach((f, i) => {
        const cb = h("input", { type: "checkbox" });
        cb.checked = checked.has(f.path);
        cb.addEventListener("change", () => {
          if (cb.checked) {
            if (g.files.every((x) => x.path === f.path || checked.has(x.path))) { cb.checked = false; toast(t("dupes.keep_one"), "info"); return; }
            checked.add(f.path);
          } else checked.delete(f.path);
          update();
        });
        const row = h("label", { class: "dup-file" }, cb, h("span", { class: "p", title: f.path, text: "\u200e" + f.path + "\u200e" }), h("span", { class: "d", text: fmtDate(f.mtime) + (i === 0 ? " · " + t("dupes.oldest") : "") }));
        box.append(row);
      });
      list.append(box);
    }
    update();
  };
  keepNewest.addEventListener("click", () => { checked = new Set(); for (const g of groups) g.files.slice(1).forEach((f) => checked.add(f.path)); render(); });
  clearSel.addEventListener("click", () => { checked = new Set(); render(); });
  runJob("dupes", { path }, (p) => {
    const s = prog.querySelector("span:last-child");
    if (s) s.textContent = p.phase === "hash" ? t("dupes.hashing", { done: fmtNum(p.done || 0), total: fmtNum(p.total || 0) }) : t("dupes.scanning", { n: fmtNum(p.files || 0) });
  }, holder).then((res) => {
    prog.remove();
    if (!res) return;
    groups = res.groups;
    if (!groups.length) { list.innerHTML = `<div class="pv-empty">${icon("check")}<span>${esc(t("dupes.none", { n: fmtNum(res.scanned) }))}</span></div>`; return; }
    bar.querySelector("b").textContent = t("dupes.summary", { groups: fmtNum(groups.length), size: fmtSize(res.wasted), n: fmtNum(res.scanned) });
    render();
  });
}

export async function folderSizes(app, pane, paths) {
  const folders = paths ? pane.shown.filter((r) => r.d && paths.includes(r.path)) : pane.shown.filter((r) => r.d);
  if (!folders.length) { toast(t("tools.no_folders"), "info"); return; }
  toast(t("tools.sizing", { n: itemsWord(folders.length) }), "info");
  const res = await runJob("size", { paths: folders.map((r) => r.path) });
  if (!res) return;
  for (const r of folders) { const info = res.items[r.path]; if (info) { r.fs = info.size; r.s = info.size; } }
  if (pane.hiddenCols.has("s")) pane.hiddenCols.delete("s");
  pane.refresh(false);
}
