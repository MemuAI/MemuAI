

import { t, addStrings, getLang } from "./i18n.js";
import { S, api, $$, h, esc, icon, fmtSize, fmtNum, joinPath, baseName, parentPath, stemOf, isWin,
  toast, showError, modal, debounce, setFormats } from "./core.js";
import * as titlebar from "./titlebar.js";
import { defaultShellBlock } from "./defaultshell.js";

addStrings({
  "view.tiles": "Tiles",
  "ctx.browse_archive": "Open like a folder",
  "ctx.extract_to": "Extract…",
  "sort.folders": "Folders and files",
  "sort.folders.auto": "Like Explorer",
  "sort.folders.top": "Folders always on top",
  "sort.folders.mixed": "Mixed together",
  "st.search": "Find a setting",
  "st.none": "Nothing found",
  "st.reset": "Reset to default",
  "st.thumbs": "Thumbnail cache",
  "st.index": "Search index",
  "st.shortcuts": "Keyboard shortcuts",
  "arc.banner": "Archive «{name}» · {kind}",
  "arc.readonly": "read-only",
  "arc.locked": "password-protected",
  "arc.extract_all": "Extract all…",
  "arc.extract_sel": "Extract selected…",
  "arc.title": "Extract",
  "arc.to": "Extract to",
  "arc.open_after": "Open the folder when done",
  "arc.password": "Password",
  "arc.password_hint": "Only if the archive is protected",
  "arc.go": "Extract",
  "arc.readonly_op": "Files inside an archive can't be changed here — extract them first",
  "arc.added": "Added to the archive",
  "arc.no_add": "This archive can't be changed (install 7-Zip for 7z/tar)",
  "arc.single": "The archive holds one folder «{name}» — it goes straight here, without an extra level",
  "arc.delete_after": "Delete the archive after extracting?",
  "arc.delete": "Delete archive",
  "zip.format": "Format",
  "zip.level": "Compression",
  "zip.level.store": "Store",
  "zip.level.fast": "Fast",
  "zip.level.normal": "Normal",
  "zip.level.max": "Maximum",
  "zip.password": "Password (optional)",
  "zip.need7z": "Needs 7-Zip",
  "zip.create": "Create",
  "zip.title2": "Create an archive",
}, {
  "view.tiles": "Плитка",
  "ctx.browse_archive": "Открыть как папку",
  "ctx.extract_to": "Извлечь…",
  "sort.folders": "Папки и файлы",
  "sort.folders.auto": "Как в Проводнике",
  "sort.folders.top": "Папки всегда сверху",
  "sort.folders.mixed": "Вперемешку",
  "st.search": "Найти настройку",
  "st.none": "Ничего не нашлось",
  "st.reset": "Сбросить",
  "st.thumbs": "Кэш миниатюр",
  "st.index": "Индекс поиска",
  "st.shortcuts": "Горячие клавиши",
  "arc.banner": "Архив «{name}» · {kind}",
  "arc.readonly": "только чтение",
  "arc.locked": "защищён паролем",
  "arc.extract_all": "Извлечь всё…",
  "arc.extract_sel": "Извлечь выделенное…",
  "arc.title": "Извлечение",
  "arc.to": "Куда извлечь",
  "arc.open_after": "Открыть папку после извлечения",
  "arc.password": "Пароль",
  "arc.password_hint": "Только если архив защищён",
  "arc.go": "Извлечь",
  "arc.readonly_op": "Файлы внутри архива здесь не меняются — сначала извлеките их",
  "arc.added": "Добавлено в архив",
  "arc.no_add": "Этот архив нельзя изменить (для 7z/tar нужен 7-Zip)",
  "arc.single": "В архиве одна папка «{name}» — она попадёт сюда без лишней вложенности",
  "arc.delete_after": "Удалить архив после извлечения?",
  "arc.delete": "Удалить архив",
  "zip.format": "Формат",
  "zip.level": "Сжатие",
  "zip.level.store": "Без сжатия",
  "zip.level.fast": "Быстрое",
  "zip.level.normal": "Обычное",
  "zip.level.max": "Максимальное",
  "zip.password": "Пароль (необязательно)",
  "zip.need7z": "Нужен 7-Zip",
  "zip.create": "Создать",
  "zip.title2": "Создать архив",
});

const L = (ru, en) => (getLang() === "en" ? en : ru);

const ACCENTS = { blue: "#3e7bfa", violet: "#8b5cf6", pink: "#e0457b", red: "#e5484d", orange: "#f08c2e", green: "#2fa36b", teal: "#14a3a3", graphite: "#7c8590" };
const FONTS = {
  "segoe-variable": '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif',
  segoe: '"Segoe UI", system-ui, sans-serif',
  system: "system-ui, sans-serif",
  rounded: '"Segoe UI Variable Display", "Nunito", "Quicksand", ui-rounded, "Segoe UI", sans-serif',
};

export function applyLook(app) {
  const s = app.settings || {};
  const root = document.documentElement;
  const accent = ACCENTS[s.accent];
  if (accent) {
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-soft", `color-mix(in srgb, ${accent} 16%, transparent)`);
  }
  if (FONTS[s.font]) root.style.setProperty("--sans", FONTS[s.font]);
  else root.style.removeProperty("--sans");
  const scale = s.ui_scale && s.ui_scale !== "auto" ? Number(s.ui_scale) : 0;
  root.style.zoom = scale && scale !== 100 ? String(scale / 100) : "";
  root.dataset.rowlines = s.row_lines ? "1" : "";
  root.dataset.motion = s.motion === "off" ? "off" : s.motion === "on" ? "on" : "";
  root.dataset.sel = s.selection_style === "outline" ? "outline" : "";
  root.dataset.groups = s.group_style === "plain" ? "plain" : "";
  setFormats(s);
}

let schemaP = null;
function schema() { return (schemaP ||= api("settings/schema").catch((err) => { schemaP = null; throw err; })); }
const GROUP_ICON = { appearance: "palette", view: "view", files: "file", tabs: "tabs", archives: "zip", ai: "sparkle", hotkeys: "keyboard", windows: "windows", advanced: "gear" };

export async function settingsDialog(app, focusGroup) {
  let sc;
  try { sc = await schema(); } catch (err) { showError(err); return; }
  const lang = getLang();
  const T = (o, pre = "") => (o && (o[pre + lang] || o[pre + "ru"])) || "";
  const items = sc.items.filter((x) => !(x.windows && !isWin()) && !(x.key === "custom_titlebar" && !(isWin() && titlebar.available())));
  const groups = sc.groups.filter((g) => items.some((x) => x.group === g.id) || g.id === "hotkeys" || (g.id === "windows" && isWin()) || g.id === "advanced");
  let current = focusGroup && groups.some((g) => g.id === focusGroup) ? focusGroup : groups[0].id;
  const nav = h("div", { class: "st41-nav", role: "tablist" });
  const pane = h("div", { class: "st41-pane" });
  const search = h("input", { class: "input st41-search", type: "search", placeholder: t("st.search"), spellcheck: "false" });
  const body = h("div", { class: "st41-body" }, h("div", { class: "st41-side" }, search, nav), pane);
  const value = (k) => app.settings[k];
  const enabled = (item) => {
    if (!item.when) return true;
    const [k, v] = String(item.when).split("=");
    return v === undefined ? !!value(k) : String(value(k)) === v;
  };
  const save = debounce(async (changes) => {
    try { app.applySettings(await api("settings", changes)); refreshDeps(); }
    catch (err) { showError(err); }
  }, 120);
  const pending = {};
  const change = (key, val) => {
    app.settings[key] = val; pending[key] = val;
    refreshDeps();
    save(Object.assign({}, pending));
    for (const k of Object.keys(pending)) delete pending[k];
  };
  const control = (item) => {
    const v = value(item.key);
    if (item.type === "bool") {
      const el = h("input", { type: "checkbox", class: "st41-sw", role: "switch", "aria-label": T(item) });
      el.checked = !!v;
      el.addEventListener("change", () => change(item.key, el.checked));
      return el;
    }
    if (item.type === "enum") {
      if (item.key === "accent") {
        const box = h("div", { class: "st41-swatches", role: "radiogroup", "aria-label": T(item) });
        for (const c of item.choices) {
          const b = h("button", { type: "button", class: (c.value === "auto" ? "auto" : "") + (v === c.value ? " on" : ""), title: T(c), "aria-label": T(c) });
          if (ACCENTS[c.value]) b.style.setProperty("--c", ACCENTS[c.value]);
          b.addEventListener("click", () => { $$("button", box).forEach((x) => x.classList.toggle("on", x === b)); change(item.key, c.value); });
          box.append(b);
        }
        return box;
      }
      const el = h("select", { class: "input", "aria-label": T(item) });
      for (const c of item.choices) { const o = h("option", { value: c.value, text: T(c) }); if (String(v) === String(c.value)) o.selected = true; el.append(o); }
      el.addEventListener("change", () => change(item.key, el.value));
      return el;
    }
    if (item.type === "int") {
      const out = h("span", { class: "val", text: v + (item.unit || "") });
      const el = h("input", { type: "range", min: item.min, max: item.max, step: item.step, value: v, "aria-label": T(item) });
      el.addEventListener("input", () => { out.textContent = el.value + (item.unit || ""); });
      el.addEventListener("change", () => change(item.key, Number(el.value)));
      return h("span", { style: { display: "inline-flex", alignItems: "center", gap: "8px" } }, el, out);
    }
    const el = h("input", { class: "input", value: v || "", placeholder: item.placeholder || "", spellcheck: "false", maxlength: String(item.maxlen || 200), "aria-label": T(item) });
    el.addEventListener("change", () => change(item.key, el.value.trim()));
    return el;
  };
  const row = (item) => {
    const lbl = h("div", { class: "lbl" }, h("b", { text: T(item) }));
    const desc = T(item, "desc_");
    if (desc) lbl.append(h("span", { text: desc }));
    const r = h("div", { class: "st41-row", "data-key": item.key }, lbl, h("div", { class: "ctl" }, control(item)));
    return r;
  };
  const refreshDeps = () => {
    for (const r of $$(".st41-row[data-key]", pane)) {
      const item = items.find((x) => x.key === r.dataset.key);
      if (item) r.classList.toggle("off", !enabled(item));
    }
  };
  const extra = (gid) => {
    if (gid === "hotkeys") return h("div", { class: "st41-extra" }, ...sc.shortcuts.map((s) => h("div", { class: "st41-row" }, h("div", { class: "lbl" }, h("b", { text: T(s) })), h("div", { class: "ctl" }, h("kbd", { text: s.keys })))));
    if (gid === "windows" && isWin()) return h("div", { class: "st41-extra" }, defaultShellBlock());
    if (gid === "advanced") {
      const cacheInfo = h("span", { class: "muted small", text: "…" });
      const clear = h("button", { class: "btn sm", html: icon("trash") + esc(t("set.clear_thumbs")) });
      const loadCache = async () => { try { const c = await api("thumbs/cache"); cacheInfo.textContent = t("set.thumbs_info", { n: fmtNum(c.count), size: fmtSize(c.size) }); } catch (e) { cacheInfo.textContent = ""; } };
      clear.addEventListener("click", async () => { await api("thumbs/cache", {}); loadCache(); toast(t("set.cleared")); });
      loadCache();
      const indexInfo = h("span", { class: "muted small", text: "…" });
      api("search/status").then((r) => {
        const ix = r.index || {};
        const backend = ix.everything || ix.backend === "everything" ? "Everything" : t("set.ix_own");
        const state = ix.backend === "everything" ? t("set.ix_state.ready") : t("set.ix_state." + (ix.state || "idle"));
        indexInfo.textContent = ix.available && ix.backend !== "off" ? t("set.index_on", { backend, state: state.startsWith("set.") ? ix.state : state, n: fmtNum(ix.entries || 0) }) : t("set.index_off");
      }).catch(() => { indexInfo.textContent = t("set.index_off"); });
      return h("div", { class: "st41-extra" },
        h("div", { class: "st41-row" }, h("div", { class: "lbl" }, h("b", { text: t("st.thumbs") }), cacheInfo), h("div", { class: "ctl" }, clear)),
        h("div", { class: "st41-row" }, h("div", { class: "lbl" }, h("b", { text: t("st.index") }), indexInfo)),
        h("div", { class: "st41-foot", text: `MemuFiles ${S.boot.version || ""} · ${t("app.tagline")}` }));
    }
    return null;
  };
  const show = (gid) => {
    current = gid;
    $$("button", nav).forEach((b) => { b.classList.toggle("on", b.dataset.g === gid); b.setAttribute("aria-selected", b.dataset.g === gid ? "true" : "false"); });
    const g = groups.find((x) => x.id === gid);
    pane.textContent = "";
    pane.append(h("div", { class: "st41-h", text: T(g) }));
    for (const item of items.filter((x) => x.group === gid)) pane.append(row(item));
    const ex = extra(gid);
    if (ex) pane.append(ex);
    pane.scrollTop = 0;
    refreshDeps();
  };
  const find = () => {
    const q = search.value.trim().toLowerCase();
    if (!q) { show(current); return; }
    pane.textContent = "";
    const hits = items.filter((x) => `${T(x)} ${T(x, "desc_")} ${x.key.replace(/_/g, " ")} ${(x.choices || []).map((c) => T(c)).join(" ")}`.toLowerCase().includes(q));
    if (!hits.length) { pane.append(h("div", { class: "muted", style: { padding: "16px 0" }, text: t("st.none") })); return; }
    let last = "";
    for (const item of hits) {
      if (item.group !== last) { last = item.group; pane.append(h("div", { class: "st41-sec", text: T(groups.find((g) => g.id === item.group) || {}) })); }
      pane.append(row(item));
    }
    refreshDeps();
  };
  for (const g of groups) {
    const b = h("button", { type: "button", role: "tab", "data-g": g.id, html: icon(GROUP_ICON[g.id] || "gear") + `<span>${esc(T(g))}</span>` });
    b.addEventListener("click", () => { search.value = ""; show(g.id); });
    nav.append(b);
  }
  search.addEventListener("input", debounce(find, 80));
  show(current);
  const dlg = modal({ title: t("cmd.settings"), icon: "gear", body, xwide: true,
    buttons: [{ label: t("common.close"), cls: "primary", primary: true }] });
  dlg.el.classList.add("st41");
  setTimeout(() => search.focus({ preventScroll: true }), 30);
}

const ROOTS = new Map();
const fold = (p) => (isWin() ? p.toLowerCase() : p);
export function noteHead(head) { if (head && head.archive) ROOTS.set(fold(head.archive.path), head.archive.path); }
export function archiveOf(path) {
  const f = fold(path || "");
  for (const [k, root] of ROOTS) if (f === k || f.startsWith(k + S.sep) || f.startsWith(k + "/")) return root;
  return null;
}
const innerOf = (root, path) => path.slice(root.length).replace(/^[\\/]+/, "").replace(/\\/g, "/");
export function inArchive(pane) { return !!(pane && pane.head && pane.head.archive && pane.mode === "dir"); }
export function isArchiveName(name) { return /\.(zip|7z|rar|tar|gz|tgz|bz2|tbz2?|xz|txz|zst|cab|iso|jar|apk|arj|lzh|wim|cbz|cbr|cb7|epub|whl|nupkg|xpi|lz|lzma)$/i.test(name || ""); }
const KIND = { zip: "ZIP", tar: "TAR", single: "GZ", "7z": "7z" };

export function archiveBanner(app, pane, b) {
  const a = pane.head.archive;
  b.hidden = false; b.className = "banner arc";
  const tags = [KIND[a.kind] || a.kind.toUpperCase()];
  if (a.encrypted) tags.push(t("arc.locked"));
  if (!a.writable) tags.push(t("arc.readonly"));
  b.innerHTML = icon("zip") + `<span class="bn-text">${esc(t("arc.banner", { name: baseName(a.path), kind: tags.join(" · ") }))}</span><span class="spacer"></span>`;
  const all = h("button", { class: "btn sm", html: icon("extract") + `<span class="lbl">${esc(t("arc.extract_all"))}</span>`, title: t("arc.extract_all") });
  all.addEventListener("click", () => extractDialog(app, a.path, { strip: a.inner }));
  b.append(all);
  const sel = pane.selectedPaths();
  if (sel.length) {
    const some = h("button", { class: "btn sm primary", html: icon("extract") + `<span class="lbl">${esc(t("arc.extract_sel"))}</span>`, title: t("arc.extract_sel") });
    some.addEventListener("click", () => extractDialog(app, a.path, { members: pane.selectedPaths().map((p) => innerOf(a.path, p)), strip: a.inner }));
    b.append(some);
  }
}

export async function openItem(app, pane, item) {
  if (item.d || pane.mode !== "dir") return false;
  if (inArchive(pane)) {
    try { await api("open", { path: item.path }); }
    catch (err) {
      if (err && err.code === "password_required") { const pw = await askPassword(); if (pw) api("open", { path: item.path, password: pw }).catch(showError); }
      else showError(err);
    }
    return true;
  }
  if (!isArchiveName(item.n)) return false;
  const how = app.settings.archive_click || "browse";
  if (how === "browse") { pane.navigate(item.path); return true; }
  if (how === "extract") { extractDialog(app, item.path, {}); return true; }
  return false;
}

function askPassword() {
  return new Promise((resolve) => {
    const input = h("input", { class: "input", type: "password", autocomplete: "off" });
    modal({ title: t("arc.password"), icon: "lock", body: h("div", { class: "field" }, h("span", { text: t("arc.password_hint") }), input),
      onClose: (v) => resolve(v === true ? input.value : ""),
      buttons: [{ label: t("common.cancel"), cls: "ghost", value: false }, { label: "OK", cls: "primary", primary: true, value: true }] });
    setTimeout(() => input.focus(), 30);
  });
}

export function transfer(app, paths, dest, kind) {
  const from = paths.length ? archiveOf(paths[0]) : null;
  const into = archiveOf(dest);
  if (from && !into) {
    const members = paths.filter((p) => archiveOf(p) === from).map((p) => innerOf(from, p));
    const strip = members.length ? members[0].split("/").slice(0, -1).join("/") : "";
    submitExtract(app, from, dest, { members, strip }, false);
    return true;
  }
  if (into) {
    if (from) { toast(t("arc.readonly_op"), "err"); return true; }
    api("ops/submit", { kind: "arcadd", sources: paths, dest: into, args: { inner: innerOf(into, dest), label: baseName(into) } })
      .then((res) => app.trackOp(res.op)).catch((err) => toast(err && err.code === "archive_readonly" ? t("arc.no_add") : (err.message || String(err)), "err"));
    return true;
  }
  return false;
}

export function guardReadOnly(pane) {
  if (!inArchive(pane)) return false;
  toast(t("arc.readonly_op"), "err");
  return true;
}

async function submitExtract(app, archive, dest, args, openAfter) {
  try {
    const res = await api("ops/submit", { kind: "extract", sources: [archive], dest, args: { ...args, label: baseName(archive) } });
    app.trackOp(res.op);
    if (openAfter || app.settings.archive_delete_after) {
      const id = res.op.id;
      const wait = setInterval(() => {
        const op = app.ops.get(id);
        if (!op || !["done", "error", "cancelled"].includes(op.status)) return;
        clearInterval(wait);
        if (op.status !== "done") return;
        if (openAfter) app.openPathInNewTab(dest, { exists: true, is_dir: true });
        if (app.settings.archive_delete_after) {
          modal({ title: t("arc.delete_after"), icon: "trash", body: h("p", { text: baseName(archive) }),
            buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("arc.delete"), cls: "danger", primary: true, run: () => { app.deletePaths([archive], false); } }] });
        }
      }, 400);
    }
  } catch (err) {
    if (err && err.code === "password_required") { const pw = await askPassword(); if (pw) submitExtract(app, archive, dest, { ...args, password: pw }, openAfter); }
    else showError(err);
  }
}

const stemOfArchive = (path) => { let s = stemOf(baseName(path)); if (/\.tar$/i.test(s)) s = s.slice(0, -4); return s || baseName(path); };

export async function extractDialog(app, archive, args = {}) {
  const outside = archiveOf(app.pane().folder || "") ? parentPath(archive) : (app.pane().folder && !archiveOf(app.pane().folder) ? app.pane().folder : parentPath(archive));
  let info = {};
  try { info = await api("arc/info?path=" + encodeURIComponent(archive)); } catch (e) {  }
  const whole = !(args.members && args.members.length) && !args.strip;
  const single = whole && info.single_root;
  const def = app.settings.extract_to === "here" || single ? outside : joinPath(outside, stemOfArchive(archive));
  const input = h("input", { class: "input", value: def, spellcheck: "false" });
  const pw = h("input", { class: "input", type: "password", autocomplete: "off", placeholder: t("arc.password_hint") });
  const open = h("input", { type: "checkbox" });
  const body = h("div", { class: "zip41" },
    h("div", { class: "field" }, h("span", { text: t("arc.to") }), input),
    single ? h("div", { class: "hint", text: t("arc.single", { name: info.single_root }) }) : null,
    info.encrypted ? h("div", { class: "field" }, h("span", { text: t("arc.password") }), pw) : null,
    h("label", { class: "check", style: { marginTop: "8px" } }, open, t("arc.open_after")));
  modal({ title: t("arc.title") + " · " + baseName(archive), icon: "extract", body, wide: true,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("arc.go"), cls: "primary", primary: true, run: () => {
      const dest = input.value.trim();
      if (!dest) return false;
      submitExtract(app, archive, dest, { ...args, ...(pw.value ? { password: pw.value } : {}) }, open.checked);
      return true;
    } }] });
  setTimeout(() => { input.focus(); input.select(); }, 30);
}

export async function quickExtract(app, archive, here) {
  const folder = parentPath(archive);
  let dest = here ? folder : joinPath(folder, stemOfArchive(archive));
  if (!here) { try { const info = await api("arc/info?path=" + encodeURIComponent(archive)); if (info.single_root) dest = folder; } catch (e) {  } }
  submitExtract(app, archive, dest, {}, false);
}

export async function compressDialog(app, paths) {
  const pane = app.pane();
  if (!paths.length || !pane.folder || archiveOf(pane.folder)) return;
  let has7z = true;
  try { has7z = !!(await api("arc/info?path=" + encodeURIComponent(paths[0]))).seven_zip; } catch (e) { has7z = false; }
  const base = paths.length === 1 ? stemOf(baseName(paths[0])) || baseName(paths[0]) : (baseName(pane.folder) || "archive");
  const fmts = [["zip", "ZIP"], ["7z", "7z"], ["tar.gz", "TAR.GZ"], ["tar.xz", "TAR.XZ"], ["tar.bz2", "TAR.BZ2"], ["tar", "TAR"]];
  let fmt = app.settings.archive_format || "zip";
  if (fmt === "7z" && !has7z) fmt = "zip";
  const name = h("input", { class: "input", value: base + "." + fmt, spellcheck: "false" });
  const format = h("select", { class: "input" });
  for (const [v, label] of fmts) { const o = h("option", { value: v, text: label + (v === "7z" && !has7z ? " — " + t("zip.need7z") : "") }); if (v === "7z" && !has7z) o.disabled = true; if (v === fmt) o.selected = true; format.append(o); }
  const level = h("select", { class: "input" });
  for (const v of ["store", "fast", "normal", "max"]) { const o = h("option", { value: v, text: t("zip.level." + v) }); if (v === (app.settings.archive_level || "normal")) o.selected = true; level.append(o); }
  const pw = h("input", { class: "input", type: "password", autocomplete: "new-password", placeholder: has7z ? "" : t("zip.need7z") });
  const pwField = h("div", { class: "field" }, h("span", { text: t("zip.password") }), pw);
  const sync = () => {
    const f = format.value;
    const stem = name.value.replace(/\.(zip|7z|tar\.gz|tar\.xz|tar\.bz2|tar)$/i, "");
    name.value = stem + "." + f;
    pw.disabled = !has7z || !(f === "zip" || f === "7z");
    pwField.style.opacity = pw.disabled ? ".5" : "";
  };
  format.addEventListener("change", sync);
  sync();
  const body = h("div", { class: "zip41" },
    h("div", { class: "field" }, h("span", { text: t("zip.name") }), name),
    h("div", { class: "grid2" }, h("div", { class: "field" }, h("span", { text: t("zip.format") }), format), h("div", { class: "field" }, h("span", { text: t("zip.level") }), level)),
    pwField);
  modal({ title: t("zip.title2"), icon: "zip", body, wide: true,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("zip.create"), cls: "primary", primary: true, run: async () => {
      const file = name.value.trim();
      if (!file) return false;
      const args = { format: format.value, level: level.value };
      if (pw.value && !pw.disabled) args.password = pw.value;
      try { const res = await api("ops/submit", { kind: "compress", sources: paths, dest: joinPath(pane.folder, file), args }); app.trackOp(res.op); }
      catch (err) { showError(err); return false; }
      return true;
    } }] });
  setTimeout(() => { name.focus(); name.setSelectionRange(0, base.length); }, 30);
}

export function startPath(app) {
  const s = app.settings, known = (S.boot && S.boot.known_folders) || {};
  const pick = { downloads: known.downloads, documents: known.documents, desktop: known.desktop, user: S.home, custom: (s.start_path || "").trim() }[s.start_page];
  return pick || "::home";
}

export function newTabPath(app) {
  const how = app.settings.new_tab_page || "home";
  if (how === "same") { const pane = app.pane && app.pane(); return (pane && pane.mode === "dir" && pane.folder) || "::home"; }
  return how === "start" ? startPath(app) : "::home";
}

export function tabAfterClose(app, index) {
  const n = app.tabs.length;
  const how = app.settings.tab_after_close || "right";
  if (how === "last" && app.prevTab) { const i = app.tabs.indexOf(app.prevTab); if (i >= 0) return i; }
  if (how === "left") return Math.max(0, Math.min(index - 1, n - 1));
  return Math.max(0, Math.min(index, n - 1));
}

export function shellHint(app, pane) {
  if (app.settings.shell_select_recent === false || !pane || pane.mode !== "dir" || pane.sel.size) return;
  const now = Date.now();
  let best = null;
  for (const r of pane.items) {
    if (r.d || r.h) continue;
    const at = Math.max(r.m || 0, r.c || 0);
    if (now - at > 600000 || now - at < -60000) continue;
    if (!best || at > Math.max(best.m || 0, best.c || 0)) best = r;
  }
  if (best) pane.selectAndReveal([best.path]);
}

let lastState = "";
const sendState = debounce((app) => {
  const pane = app.pane && app.pane();
  if (!pane) return;
  const folder = pane.mode === "dir" ? (pane.head && pane.head.archive ? pane.head.archive.path : pane.folder || "") : "";
  const state = { folder, selected: pane.mode === "dir" ? pane.selectedPaths().slice(0, 200) : [], mode: pane.mode, archive: !!(pane.head && pane.head.archive) };
  const text = JSON.stringify(state);
  if (text === lastState) return;
  lastState = text;
  api("uistate", state).catch(() => { lastState = ""; });
}, 250);
export function reportState(app) { sendState(app); }
