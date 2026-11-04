

import { t, addStrings, getLang } from "./i18n.js";
import { S, api, $$, h, esc, icon, fmtSize, fmtNum, joinPath, baseName, parentPath, stemOf, isWin,
  toast, showError, modal, debounce, setFormats, samePath, itemsWord, menu, confirmDialog, promptDialog } from "./core.js";
import * as titlebar from "./titlebar.js";
import { defaultShellBlock } from "./defaultshell.js";
import { LOOK_KEYS, lookBlock } from "./look.js";

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
  "st.css_title": "Custom CSS",
  "st.css_hint": "Your own rules for the MemuFiles window only: colours, spacing, fonts. Applied live as you type, saved with “Apply”.",
  "st.css_placeholder": "Example: .tab { font-weight: 600; }",
  "st.css_apply": "Apply",
  "st.css_reset": "Reset",
  "st.css_saved": "Saved",
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
  "arc.readonly_op": "This archive is read-only here (rar, iso, cab and single .gz/.xz files; 7z needs 7-Zip) — extract the files first",
  "arc.added": "Added to the archive",
  "arc.no_add": "This archive can't be changed (rar, iso and cab are read-only; 7z needs 7-Zip)",
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
  "st.css_title": "Свой CSS",
  "st.css_hint": "Свои правила только для окна MemuFiles: цвета, отступы, шрифты. Применяется сразу при наборе, сохраняется кнопкой «Применить».",
  "st.css_placeholder": "Например: .tab { font-weight: 600; }",
  "st.css_apply": "Применить",
  "st.css_reset": "Сбросить",
  "st.css_saved": "Сохранено",
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
  "arc.readonly_op": "Этот архив здесь только для чтения (rar, iso, cab и одиночные .gz/.xz; для 7z нужен 7-Zip) — сначала извлеките файлы",
  "arc.added": "Добавлено в архив",
  "arc.no_add": "Этот архив нельзя изменить (rar, iso и cab только для чтения; для 7z нужен 7-Zip)",
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

addStrings({
  "arc.new_folder": "New folder",
  "arc.folder_name": "Folder name inside the archive",
  "arc.tools": "Archive tools",
  "arc.stats": "{files} files · {size}",
  "arc.ratio": "compressed by {pct}%",
  "arc.cross": "Files can't go straight from one archive into another — extract them first",
  "arc.copy_inside": "Inside an archive files can be moved (Ctrl+X, Ctrl+V), not copied",
  "arc.replace_title": "Replace files in the archive?",
  "arc.replace_text": "{what} is already in «{name}». Replace the old copies or skip them?",
  "arc.replace": "Replace",
  "arc.skip": "Skip",
  "arc.del_title": "Delete from the archive?",
  "arc.del_text": "{what} will be removed from «{name}». The previous version of the archive goes to the Recycle Bin, so Ctrl+Z brings everything back.",
  "arc.del_ok": "Delete from archive",
  "arc.rename": "Rename",
  "arc.rename_one": "Inside an archive, rename one item at a time",
  "arc.test": "Test the archive",
  "arc.comment": "Comment…",
  "arc.comment_title": "Archive comment",
  "arc.comment_hint": "Shown by archivers when the archive is opened. Leave empty to remove it.",
  "arc.comment_ro": "This archive is read-only — the comment can only be read",
  "arc.comment_saved": "Comment saved",
  "arc.comment_zip_only": "Only ZIP archives have a comment",
  "arc.save": "Save",
  "arc.convert": "Convert to another format…",
  "arc.convert_title": "Convert",
  "arc.convert_hint": "A new archive is created next to this one; the original stays as it is.",
  "arc.convert_go": "Convert",
  "arc.readonly_why": "Read-only: rar, iso, cab, single .gz/.xz (7z needs 7-Zip)",
  "opx.k.arcedit": "Updating {what}", "opx.d.arcedit": "Archive {what} updated",
  "opx.k.arctest": "Testing {what}", "opx.d.arctest": "No errors found in {what}",
  "opx.k.arcconvert": "Converting {what}", "opx.d.arcconvert": "Converted {what}",
  "opx.k.arcadd": "Adding {what} to the archive", "opx.d.arcadd": "Added {what} to the archive",
  "op.arcedit": "Archive update", "op.arctest": "Archive test", "op.arcconvert": "Conversion", "op.arcadd": "Adding to the archive",
  "err.damaged": "damaged — the data doesn't match its checksum",
  "err.archive_damaged": "The archive is damaged",
  "err.archive_readonly": "This archive can't be changed",
  "err.archive_edit_failed": "7-Zip couldn't change the archive",
  "err.archive_empty": "The archive is empty",
  "err.password_required": "The archive needs a password",
  "err.bad_password": "Wrong password",
  "err.unsupported_method": "unsupported compression method (install 7-Zip)",
  "err.not_an_archive": "This is not an archive",
  "err.bad_action": "Unknown archive action",
  "err.bad_destination": "A new archive can't be created there",
  "err.no_7zip": "This needs 7-Zip",
  "err.too_long": "The text is too long",
  "err.too_many": "Too many items at once",
}, {
  "arc.new_folder": "Новая папка",
  "arc.folder_name": "Имя папки внутри архива",
  "arc.tools": "Действия с архивом",
  "arc.stats": "файлов: {files} · {size}",
  "arc.ratio": "сжато на {pct}%",
  "arc.cross": "Из архива в другой архив напрямую нельзя — сначала извлеките файлы",
  "arc.copy_inside": "Внутри архива файлы можно перемещать (Ctrl+X, Ctrl+V), но не копировать",
  "arc.replace_title": "Заменить файлы в архиве?",
  "arc.replace_text": "{what} уже есть в «{name}». Заменить старые копии или пропустить их?",
  "arc.replace": "Заменить",
  "arc.skip": "Пропустить",
  "arc.del_title": "Удалить из архива?",
  "arc.del_text": "{what} будет удалено из «{name}». Прежняя версия архива уйдёт в корзину — Ctrl+Z всё вернёт.",
  "arc.del_ok": "Удалить из архива",
  "arc.rename": "Переименовать",
  "arc.rename_one": "Внутри архива переименовывайте по одному элементу",
  "arc.test": "Проверить архив",
  "arc.comment": "Комментарий…",
  "arc.comment_title": "Комментарий к архиву",
  "arc.comment_hint": "Архиваторы показывают его при открытии архива. Оставьте пустым, чтобы убрать.",
  "arc.comment_ro": "Архив только для чтения — комментарий можно лишь прочитать",
  "arc.comment_saved": "Комментарий сохранён",
  "arc.comment_zip_only": "Комментарий бывает только у ZIP-архивов",
  "arc.save": "Сохранить",
  "arc.convert": "Преобразовать в другой формат…",
  "arc.convert_title": "Преобразование",
  "arc.convert_hint": "Рядом появится новый архив, исходный останется как есть.",
  "arc.convert_go": "Преобразовать",
  "arc.readonly_why": "Только чтение: rar, iso, cab, одиночные .gz/.xz (для 7z нужен 7-Zip)",
  "opx.k.arcedit": "Изменение архива {what}", "opx.d.arcedit": "Архив {what} изменён",
  "opx.k.arctest": "Проверка {what}", "opx.d.arctest": "Ошибок не найдено: {what}",
  "opx.k.arcconvert": "Преобразование {what}", "opx.d.arcconvert": "Преобразовано: {what}",
  "opx.k.arcadd": "Добавление в архив: {what}", "opx.d.arcadd": "Добавлено в архив: {what}",
  "op.arcedit": "Изменение архива", "op.arctest": "Проверка архива", "op.arcconvert": "Преобразование", "op.arcadd": "Добавление в архив",
  "err.damaged": "повреждён — данные не сходятся с контрольной суммой",
  "err.archive_damaged": "Архив повреждён",
  "err.archive_readonly": "Этот архив нельзя изменить",
  "err.archive_edit_failed": "7-Zip не смог изменить архив",
  "err.archive_empty": "Архив пуст",
  "err.password_required": "Для архива нужен пароль",
  "err.bad_password": "Неверный пароль",
  "err.unsupported_method": "неподдерживаемый метод сжатия (установите 7-Zip)",
  "err.not_an_archive": "Это не архив",
  "err.bad_action": "Неизвестное действие с архивом",
  "err.bad_destination": "Там нельзя создать новый архив",
  "err.no_7zip": "Для этого нужен 7-Zip",
  "err.too_long": "Слишком длинный текст",
  "err.too_many": "Слишком много элементов за раз",
});

const L = (ru, en) => (getLang() === "en" ? en : ru);

const FONTS = {
  "segoe-variable": '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif',
  segoe: '"Segoe UI", system-ui, sans-serif',
  system: "system-ui, sans-serif",
  rounded: '"Segoe UI Variable Display", "Nunito", "Quicksand", ui-rounded, "Segoe UI", sans-serif',
};

export function applyLook(app) {
  const s = app.settings || {};
  const root = document.documentElement;
  // The accent, the palette and the design come resolved from the server (appearance.py, applyTheme).
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
  // Changes made within the debounce window travel together (a custom accent sets two keys at once).
  const pending = {};
  let lookHost = null;
  const flush = debounce(async () => {
    const changes = Object.assign({}, pending);
    for (const k of Object.keys(pending)) delete pending[k];
    if (!Object.keys(changes).length) return;
    try { app.applySettings(await api("settings", changes)); refreshDeps(); if (lookHost && lookHost.isConnected) lookHost.repaint(); }
    catch (err) { showError(err); }
  }, 120);
  const changeMany = (patch) => {
    Object.assign(app.settings, patch); Object.assign(pending, patch);
    refreshDeps();
    flush();
  };
  const change = (key, val) => changeMany({ [key]: val });
  const control = (item) => {
    const v = value(item.key);
    if (item.type === "bool") {
      const el = h("input", { type: "checkbox", class: "st41-sw", role: "switch", "aria-label": T(item) });
      el.checked = !!v;
      el.addEventListener("change", () => change(item.key, el.checked));
      return el;
    }
    if (item.type === "enum") {
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
    const pattern = item.pattern ? new RegExp(`^(?:${item.pattern})$`) : null;
    el.addEventListener("input", () => el.classList.remove("bad"));
    el.addEventListener("change", () => {
      const next = el.value.trim();
      if (pattern && next && !pattern.test(next)) { el.classList.add("bad"); el.setAttribute("aria-invalid", "true"); return; }
      el.removeAttribute("aria-invalid");
      change(item.key, next);
    });
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
    if (gid === "appearance") {
      const area = h("textarea", { class: "input css-area", rows: 8, spellcheck: "false", placeholder: t("st.css_placeholder") });
      area.value = app.settings.custom_css || "";
      const note = h("span", { class: "muted small" });
      area.addEventListener("input", () => app.applyCustomCss(area.value));
      const applyBtn = h("button", { class: "btn sm", text: t("st.css_apply") });
      applyBtn.addEventListener("click", async () => { try { const res = await api("settings", { custom_css: area.value }); app.applySettings(res); note.textContent = t("st.css_saved"); } catch (err) { showError(err); } });
      const resetBtn = h("button", { class: "btn sm", text: t("st.css_reset") });
      resetBtn.addEventListener("click", async () => { area.value = ""; try { const res = await api("settings", { custom_css: "" }); app.applySettings(res); note.textContent = t("st.css_saved"); } catch (err) { showError(err); } });
      return h("div", { class: "st41-extra" },
        h("div", { class: "st41-row" }, h("div", { class: "lbl" }, h("b", { text: t("st.css_title") }), h("span", { text: t("st.css_hint") }))),
        area,
        h("div", { class: "css-actions" }, applyBtn, resetBtn, note));
    }
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
    lookHost = null;
    if (gid === "appearance") pane.append(lookHost = lookBlock(app, changeMany));
    for (const item of items.filter((x) => x.group === gid && !(gid === "appearance" && LOOK_KEYS.includes(x.key)))) pane.append(row(item));
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
  b.innerHTML = icon("zip") + `<span class="bn-text">${esc(t("arc.banner", { name: baseName(a.path), kind: tags.join(" · ") }))}<span class="arc-stats"></span></span><span class="spacer"></span>`;
  fillStats(b, a.path);
  if (a.writable) {
    const add = h("button", { class: "btn sm", html: icon("folderPlus") + `<span class="lbl">${esc(t("arc.new_folder"))}</span>`, title: t("arc.new_folder") + " (Ctrl+Shift+N)" });
    add.addEventListener("click", () => archiveNewFolder(app, pane));
    b.append(add);
  }
  const all = h("button", { class: "btn sm", html: icon("extract") + `<span class="lbl">${esc(t("arc.extract_all"))}</span>`, title: t("arc.extract_all") });
  all.addEventListener("click", () => extractDialog(app, a.path, { strip: a.inner }));
  b.append(all);
  const sel = pane.selectedPaths();
  if (sel.length) {
    const some = h("button", { class: "btn sm primary", html: icon("extract") + `<span class="lbl">${esc(t("arc.extract_sel"))}</span>`, title: t("arc.extract_sel") });
    some.addEventListener("click", () => extractDialog(app, a.path, { members: pane.selectedPaths().map((p) => innerOf(a.path, p)), strip: a.inner }));
    b.append(some);
  }
  const more = h("button", { class: "icon-btn arc-more", html: icon("more"), title: t("arc.tools"), "aria-label": t("arc.tools") });
  more.addEventListener("click", () => { const r = more.getBoundingClientRect(); menu(archiveTools(app, pane), Math.max(8, r.right - 250), r.bottom + 4); });
  b.append(more);
}

const STATS = new Map();
function fillStats(b, path) {
  const put = (info) => {
    const slot = b.querySelector(".arc-stats");
    if (!slot || !info) return;
    const ratio = info.unpacked > 0 && info.packed > 0 ? Math.round(100 - (100 * info.packed) / info.unpacked) : null;
    slot.textContent = " · " + t("arc.stats", { files: fmtNum(info.files), size: fmtSize(info.unpacked) }) + (ratio !== null && ratio > 0 ? " · " + t("arc.ratio", { pct: ratio }) : "");
  };
  const hit = STATS.get(path);
  if (hit && Date.now() - hit.at < 4000) { put(hit.info); return; }
  api("arc/info?path=" + encodeURIComponent(path)).then((info) => { STATS.set(path, { at: Date.now(), info }); put(info); }).catch(() => {});
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
    if (from && from !== into) { toast(t("arc.cross"), "err"); return true; }
    if (from) { moveInside(app, into, paths, dest, kind); return true; }
    addFiles(app, into, paths, dest);
    return true;
  }
  return false;
}

function addFiles(app, into, paths, dest) {
  const pane = app.pane();
  const here = pane && pane.items && samePath(pane.path, dest) ? new Set(pane.items.map((r) => r.n)) : null;
  const clash = here ? paths.filter((p) => here.has(baseName(p))) : [];
  const go = (policy) => api("ops/submit", { kind: "arcadd", sources: paths, dest: into, args: { inner: innerOf(into, dest), label: baseName(into), ...(policy ? { on_conflict: policy } : {}) } })
    .then((res) => { app.trackOp(res.op); whenDone(app, res.op.id, (op) => { if (op.status === "done") refreshArchive(app, into); }); })
    .catch((err) => toast(err && err.code === "archive_readonly" ? t("arc.no_add") : (err.message || String(err)), "err"));
  if (!clash.length) { go(""); return; }
  const what = clash.length === 1 ? `«${baseName(clash[0])}»` : itemsWord(clash.length);
  modal({ title: t("arc.replace_title"), icon: "zip", body: h("p", { class: "arc-replace", text: t("arc.replace_text", { what, name: baseName(into) }) }),
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("arc.skip"), cls: "ghost", run: () => { go("skip"); } },
      { label: t("arc.replace"), cls: "primary", primary: true, run: () => { go("replace"); } }] });
}

function moveInside(app, archive, paths, dest, kind) {
  if (kind !== "move") { toast(t("arc.copy_inside"), "err"); return; }
  const to = innerOf(archive, dest);
  const actions = [];
  for (const p of paths) {
    if (archiveOf(p) !== archive) continue;
    const src = innerOf(archive, p);
    const target = (to ? to + "/" : "") + src.split("/").pop();
    if (target === src) continue;
    if (target.startsWith(src + "/")) { toast(t("err.into_self"), "err"); return; }
    actions.push({ do: "rename", from: src, to: target });
  }
  if (actions.length) submitEdit(app, archive, actions);
}

export function guardReadOnly(pane) {
  if (!inArchive(pane) || pane.head.archive.writable) return false;
  toast(t("arc.readonly_op"), "err");
  return true;
}

/* Archive editor: delete / rename / move / new folder (arcedit), test, comment, convert. */
const PASSWORDS = new Map();

function whenDone(app, id, cb) {
  let ticks = 0;
  const wait = setInterval(() => {
    const op = app.ops.get(id);
    if (++ticks > 7200) { clearInterval(wait); return; }
    if (!op || !["done", "error", "cancelled"].includes(op.status)) return;
    clearInterval(wait);
    cb(op);
  }, 250);
}

function refreshArchive(app, archive) {
  STATS.delete(archive);
  for (const tab of app.tabs) for (const p of tab.panes) {
    if (p.el && p.head && p.head.archive && samePath(p.head.archive.path, archive)) p.reload(true);
  }
}

async function submitEdit(app, archive, actions, after) {
  const args = { actions, label: baseName(archive) };
  const pw = PASSWORDS.get(fold(archive));
  if (pw) args.password = pw;
  let res;
  try { res = await api("ops/submit", { kind: "arcedit", sources: [archive], dest: archive, args }); }
  catch (err) { showError(err); return; }
  app.trackOp(res.op);
  whenDone(app, res.op.id, async (op) => {
    if (op.status === "error" && op.message === "password_required") {
      const again = await askPassword();
      if (again) { PASSWORDS.set(fold(archive), again); submitEdit(app, archive, actions, after); }
      return;
    }
    if (op.status !== "done") return;
    if (after) after(op);
    refreshArchive(app, archive);
  });
}

const canEdit = (pane) => { if (pane.head.archive.writable) return true; toast(t("arc.readonly_op"), "err"); return false; };

export async function archiveDelete(app, pane) {
  const a = pane.head.archive;
  const sel = pane.selectedPaths();
  if (!sel.length || !canEdit(pane)) return;
  const what = sel.length === 1 ? `«${baseName(sel[0])}»` : itemsWord(sel.length);
  const ok = await confirmDialog({ title: t("arc.del_title"), text: t("arc.del_text", { what, name: baseName(a.path) }), ok: t("arc.del_ok"), danger: true, icon: "trash" });
  if (ok) submitEdit(app, a.path, sel.map((p) => ({ do: "delete", path: innerOf(a.path, p) })));
}

export async function archiveNewFolder(app, pane) {
  const a = pane.head.archive;
  if (!canEdit(pane)) return;
  const taken = new Set((pane.items || []).map((r) => r.n));
  const base = t("new.folder_name");
  let name = base;
  for (let i = 2; taken.has(name); i++) name = `${base} (${i})`;
  const typed = ((await promptDialog({ title: t("arc.new_folder"), label: t("arc.folder_name"), value: name, icon: "folderPlus" })) || "").trim();
  if (!typed) return;
  if (/[\\/]/.test(typed)) { toast(t("err.invalid_chars"), "err"); return; }
  const inner = innerOf(a.path, pane.path);
  submitEdit(app, a.path, [{ do: "mkdir", path: (inner ? inner + "/" : "") + typed }], () => { pane.pendingSelect = [joinPath(pane.path, typed)]; });
}

export function archiveRenameCmd(app, pane) {
  if (!canEdit(pane)) return;
  const sel = pane.selected();
  if (!sel.length) return;
  if (sel.length > 1) { toast(t("arc.rename_one"), "info"); return; }
  pane.startRename();
}

export function archiveRename(app, pane, item, name) {
  const a = pane.head.archive;
  if (!canEdit(pane)) return;
  if (/[\\/]/.test(name)) { toast(t("err.invalid_chars"), "err"); return; }
  const from = innerOf(a.path, item.path);
  const parent = from.split("/").slice(0, -1).join("/");
  submitEdit(app, a.path, [{ do: "rename", from, to: (parent ? parent + "/" : "") + name }], () => { pane.pendingSelect = [joinPath(parentPath(item.path), name)]; });
}

export async function archiveTest(app, archive, password) {
  const args = { label: baseName(archive) };
  const pw = password || PASSWORDS.get(fold(archive));
  if (pw) args.password = pw;
  try {
    const res = await api("ops/submit", { kind: "arctest", sources: [archive], dest: "", args });
    app.trackOp(res.op);
    whenDone(app, res.op.id, async (op) => {
      if (op.status !== "error" || !["password_required", "bad_password"].includes(op.message)) return;
      const again = await askPassword();
      if (again) { PASSWORDS.set(fold(archive), again); archiveTest(app, archive, again); }
    });
  } catch (err) { showError(err); }
}

export async function archiveComment(app, archive) {
  let info;
  try { info = await api("arc/comment?path=" + encodeURIComponent(archive)); } catch (err) { showError(err); return; }
  if (!info.supported) { toast(t("arc.comment_zip_only"), "info"); return; }
  const area = h("textarea", { class: "input arc-comment", rows: 8, spellcheck: "false", maxlength: 60000 });
  area.value = info.comment || "";
  area.readOnly = !info.writable;
  const body = h("div", { class: "zip41" }, h("div", { class: "field" }, h("span", { text: t(info.writable ? "arc.comment_hint" : "arc.comment_ro") }), area));
  modal({ title: t("arc.comment_title") + " · " + baseName(archive), icon: "text", body, wide: true,
    buttons: info.writable ? [{ label: t("common.cancel"), cls: "ghost" }, { label: t("arc.save"), cls: "primary", primary: true, run: async () => {
      try { await api("arc/comment", { path: archive, comment: area.value }); toast(t("arc.comment_saved"), "ok"); refreshArchive(app, archive); }
      catch (err) { showError(err); return false; }
      return true;
    } }] : [{ label: t("common.close"), cls: "primary", primary: true }] });
  setTimeout(() => area.focus(), 30);
}

export async function convertDialog(app, archive) {
  let has7z = true;
  try { has7z = !!(await api("arc/info?path=" + encodeURIComponent(archive))).seven_zip; } catch (e) { has7z = false; }
  const stem = stemOfArchive(archive);
  const low = archive.toLowerCase();
  const fmts = [["zip", "ZIP"], ["7z", "7z"], ["tar.gz", "TAR.GZ"], ["tar.xz", "TAR.XZ"], ["tar.bz2", "TAR.BZ2"], ["tar", "TAR"]];
  let fmt = low.endsWith(".zip") ? (has7z ? "7z" : "tar.gz") : "zip";
  const name = h("input", { class: "input", value: stem + "." + fmt, spellcheck: "false" });
  const format = h("select", { class: "input" });
  for (const [v, label] of fmts) {
    const o = h("option", { value: v, text: label + (v === "7z" && !has7z ? " — " + t("zip.need7z") : "") });
    if (v === "7z" && !has7z) o.disabled = true;
    if (v === fmt) o.selected = true;
    format.append(o);
  }
  const level = h("select", { class: "input" });
  for (const v of ["store", "fast", "normal", "max"]) { const o = h("option", { value: v, text: t("zip.level." + v) }); if (v === (app.settings.archive_level || "normal")) o.selected = true; level.append(o); }
  const pw = h("input", { class: "input", type: "password", autocomplete: "new-password", placeholder: has7z ? "" : t("zip.need7z") });
  const pwField = h("div", { class: "field" }, h("span", { text: t("zip.password") }), pw);
  const sync = () => {
    const f = format.value;
    name.value = name.value.replace(/\.(zip|7z|tar\.gz|tar\.xz|tar\.bz2|tar)$/i, "") + "." + f;
    pw.disabled = !has7z || !(f === "zip" || f === "7z");
    pwField.style.opacity = pw.disabled ? ".5" : "";
  };
  format.addEventListener("change", sync);
  sync();
  const body = h("div", { class: "zip41" },
    h("div", { class: "field" }, h("span", { text: t("zip.name") }), name),
    h("div", { class: "grid2" }, h("div", { class: "field" }, h("span", { text: t("zip.format") }), format), h("div", { class: "field" }, h("span", { text: t("zip.level") }), level)),
    pwField,
    h("div", { class: "hint", text: t("arc.convert_hint") }));
  modal({ title: t("arc.convert_title") + " · " + baseName(archive), icon: "zip", body, wide: true,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("arc.convert_go"), cls: "primary", primary: true, run: async () => {
      const file = name.value.trim();
      if (!file || /[\\/]/.test(file)) return false;
      const args = { format: format.value, level: level.value, label: baseName(archive) };
      if (pw.value && !pw.disabled) args.password = pw.value;
      const known = PASSWORDS.get(fold(archive));
      if (known) args.source_password = known;
      try { const res = await api("ops/submit", { kind: "arcconvert", sources: [archive], dest: joinPath(parentPath(archive), file), args }); app.trackOp(res.op); }
      catch (err) { showError(err); return false; }
      return true;
    } }] });
  setTimeout(() => { name.focus(); name.setSelectionRange(0, stem.length); }, 30);
}

export function archiveTools(app, pane) {
  const a = pane.head.archive;
  const sel = pane.selectedPaths();
  return [
    { label: t("arc.extract_all"), icon: "extract", run: () => extractDialog(app, a.path, { strip: a.inner }) },
    sel.length ? { label: t("arc.extract_sel"), icon: "extract", run: () => extractDialog(app, a.path, { members: sel.map((p) => innerOf(a.path, p)), strip: a.inner }) } : null,
    { sep: true },
    { label: t("arc.new_folder"), icon: "folderPlus", kb: "Ctrl+Shift+N", disabled: !a.writable, run: () => archiveNewFolder(app, pane) },
    { label: t("arc.rename"), icon: "rename", kb: "F2", disabled: !a.writable || sel.length !== 1, run: () => pane.startRename() },
    { label: t("arc.del_ok"), icon: "trash", kb: "Del", danger: true, disabled: !a.writable || !sel.length, run: () => archiveDelete(app, pane) },
    { sep: true },
    { label: t("arc.test"), icon: "check", run: () => archiveTest(app, a.path) },
    a.kind === "zip" ? { label: t("arc.comment"), icon: "text", run: () => archiveComment(app, a.path) } : null,
    { label: t("arc.convert"), icon: "zip", run: () => convertDialog(app, a.path) },
    a.writable ? null : { label: t("arc.readonly_why"), icon: "lock", disabled: true },
  ];
}

export function archiveItemMenu(app, pane) {
  const a = pane.head.archive;
  const sel = pane.selected();
  const one = sel.length === 1 ? sel[0] : null;
  return [
    { label: t("ctx.open"), icon: "open", kb: "Enter", run: () => app.openSelected() },
    { label: t("arc.extract_sel"), icon: "extract", run: () => extractDialog(app, a.path, { members: sel.map((r) => innerOf(a.path, r.path)), strip: a.inner }) },
    { sep: true },
    { label: t("cmd.cut"), icon: "cut", kb: "Ctrl+X", disabled: !a.writable, run: () => app.cmdCut() },
    { label: t("cmd.copy"), icon: "copy", kb: "Ctrl+C", run: () => app.cmdCopy() },
    one && one.d ? { label: t("cmd.paste_into"), icon: "paste", disabled: !app.clipboard || !a.writable, run: () => app.cmdPaste(one.path) } : null,
    { label: t("arc.rename"), icon: "rename", kb: "F2", disabled: !a.writable || !one, run: () => pane.startRename() },
    { label: t("arc.del_ok"), icon: "trash", kb: "Del", danger: true, disabled: !a.writable, run: () => archiveDelete(app, pane) },
    { sep: true },
    { label: t("cmd.copy_path"), icon: "link", kb: "Ctrl+Shift+C", run: () => app.copyPaths(sel.map((r) => r.path), sel.length > 1) },
    { label: t("arc.tools"), icon: "zip", sub: () => archiveTools(app, pane) },
  ];
}

export function archiveBackItems(app, pane) {
  const a = pane.head.archive;
  return [
    { label: t("cmd.paste"), icon: "paste", kb: "Ctrl+V", disabled: !a.writable || (!app.clipboard && !isWin()), run: () => app.cmdPaste() },
    { label: t("cmd.undo"), icon: "undo", kb: "Ctrl+Z", run: () => app.undo() },
    { sep: true },
    ...archiveTools(app, pane),
  ];
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
