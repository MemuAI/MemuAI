// MemuFiles · Round 39 features (r39-files-feat): Quick Look (Space), «Копировать как…», «Открыть в VS Code»,
// folder compare & sync, batch image conversion, document templates (DOCX/XLSX/PPTX…), recent files and
// saved searches on the Home page, squarified treemap for «Место на диске», safe drive eject.
// Everything here is created on demand: nothing runs until the user opens one of these tools.
import { t, addStrings } from "./i18n.js";
import { S, api, $, h, esc, icon, fileIcon, folderSvg, fmtSize, fmtNum, fmtDate, itemsWord, joinPath, baseName,
  parentPath, extOf, typeOf, rawUrl, BROWSER_IMG, IMAGE_EXT, toast, showError, errText, modal, isWin } from "./core.js";
import { highlight, renderMarkdown } from "./preview.js";
import { runJob } from "./dialogs.js";
import { SPECIAL } from "./pane.js";

addStrings({
  "x.ql.of": "{i} of {n}", "x.ql.open": "Open", "x.ql.close": "Close (Esc)", "x.ql.hint": "Space / Esc close · ←/→ previous/next · Enter open",
  "x.ql.folder": "Folder · {n}", "x.ql.no_preview": "No preview for this file type",
  "x.copy_as": "Copy path as", "x.copy.path": "Full path", "x.copy.quoted": "Path in quotes", "x.copy.name": "Name only",
  "x.copy.stem": "Name without extension", "x.copy.folder": "Folder path", "x.copy.unix": "With forward slashes (/)",
  "x.copy.uri": "file:// link", "x.copy.ps": "For PowerShell", "x.copy.bash": "For Bash / WSL", "x.copy.md": "Markdown link",
  "x.copy.done": "Copied: {what}",
  "x.editor": "Open in code editor", "x.editor_none": "No code editor found (VS Code, Cursor, Sublime, Notepad++)",
  "x.editor_opened": "Opening in {name}",
  "x.cmp.title": "Compare folders", "x.cmp.left": "Left folder", "x.cmp.right": "Right folder", "x.cmp.run": "Compare",
  "x.cmp.content": "Compare contents (SHA-256, slower)", "x.cmp.recursive": "Include subfolders",
  "x.cmp.f.diff": "Differences", "x.cmp.f.all": "All", "x.cmp.f.left": "Only left", "x.cmp.f.right": "Only right",
  "x.cmp.f.newer": "Newer", "x.cmp.f.same": "Same",
  "x.cmp.st.same": "same", "x.cmp.st.left": "only left", "x.cmp.st.right": "only right", "x.cmp.st.newer_left": "newer on the left",
  "x.cmp.st.newer_right": "newer on the right", "x.cmp.st.differ": "differs",
  "x.cmp.to_right": "Copy → right", "x.cmp.to_left": "← Copy left", "x.cmp.sync": "Sync both ways",
  "x.cmp.mirror": "Mirror left → right", "x.cmp.summary": "{diff} differences of {total}", "x.cmp.identical": "The folders are identical",
  "x.cmp.queued": "Copy queued: {n}", "x.cmp.nothing": "Nothing to copy", "x.cmp.need_two": "Choose two different folders",
  "x.cmp.scanning": "Comparing…", "x.cmp.selected": "{n} selected",
  "x.img.title": "Convert images", "x.img.format": "Format", "x.img.keep": "Keep format", "x.img.size": "Max side",
  "x.img.orig": "Original size", "x.img.quality": "Quality", "x.img.sub": "Save to subfolder", "x.img.sub_hint": "Empty = next to the originals",
  "x.img.sub_default": "Converted", "x.img.run": "Convert", "x.img.working": "Converting {i} of {n}…", "x.img.done": "Converted: {n}",
  "x.img.failed": "Failed: {n}", "x.img.menu": "Convert images…",
  "x.new.docx": "Word document", "x.new.xlsx": "Excel workbook", "x.new.pptx": "PowerPoint presentation", "x.new.py": "Python script",
  "x.new.ps1": "PowerShell script", "x.new.bat": "Batch file", "x.new.csv": "CSV table", "x.new.html": "HTML page", "x.new.more": "More templates",
  "x.new.doc_name": "New document", "x.new.xls_name": "New workbook", "x.new.ppt_name": "New presentation", "x.new.script": "script",
  "x.recent": "Recent files", "x.recent_clear": "Clear", "x.saved": "Saved searches", "x.save_search": "Save search",
  "x.saved_name": "Name of the search", "side.saved_run": "Search again", "side.saved_tab": "Search in a new tab", "x.saved_done": "Search saved", "x.saved_remove": "Remove",
  "x.saved_where_all": "everywhere", "x.compare_tool_sub": "Find differences and sync", "x.img_tool_sub": "Resize and change format",
  "x.eject": "Eject", "x.ejected": "{drive} can be removed safely", "err.eject_failed": "The drive is busy — close the files on it and try again",
  "br.tok.name": "Original name", "br.tok.n": "Counter", "br.tok.n3": "Counter 001", "br.tok.ext": "Extension", "br.tok.date": "Modified date",
  "br.tok.time": "Modified time", "br.tok.parent": "Folder name", "br.tok.created": "Created date", "br.tok.exif": "Photo date taken (EXIF)",
  "br.tok.widthxheight": "Image size, px",
  "err.no_editor": "No code editor found", "err.no_pillow": "Image support (Pillow) is not available", "err.bad_format": "Unknown format",
});
addStrings(null, {
  "x.ql.of": "{i} из {n}", "x.ql.open": "Открыть", "x.ql.close": "Закрыть (Esc)", "x.ql.hint": "Пробел / Esc — закрыть · ←/→ — соседние · Enter — открыть",
  "x.ql.folder": "Папка · {n}", "x.ql.no_preview": "Для этого типа файлов нет просмотра",
  "x.copy_as": "Копировать путь как", "x.copy.path": "Полный путь", "x.copy.quoted": "Путь в кавычках", "x.copy.name": "Только имя",
  "x.copy.stem": "Имя без расширения", "x.copy.folder": "Путь к папке", "x.copy.unix": "С прямыми слэшами (/)",
  "x.copy.uri": "Ссылка file://", "x.copy.ps": "Для PowerShell", "x.copy.bash": "Для Bash / WSL", "x.copy.md": "Ссылка Markdown",
  "x.copy.done": "Скопировано: {what}",
  "x.editor": "Открыть в редакторе кода", "x.editor_none": "Редактор кода не найден (VS Code, Cursor, Sublime, Notepad++)",
  "x.editor_opened": "Открываю в {name}",
  "x.cmp.title": "Сравнение папок", "x.cmp.left": "Левая папка", "x.cmp.right": "Правая папка", "x.cmp.run": "Сравнить",
  "x.cmp.content": "Сравнивать содержимое (SHA-256, медленнее)", "x.cmp.recursive": "Со вложенными папками",
  "x.cmp.f.diff": "Различия", "x.cmp.f.all": "Все", "x.cmp.f.left": "Только слева", "x.cmp.f.right": "Только справа",
  "x.cmp.f.newer": "Новее", "x.cmp.f.same": "Одинаковые",
  "x.cmp.st.same": "одинаково", "x.cmp.st.left": "только слева", "x.cmp.st.right": "только справа", "x.cmp.st.newer_left": "слева новее",
  "x.cmp.st.newer_right": "справа новее", "x.cmp.st.differ": "отличается",
  "x.cmp.to_right": "Копировать →", "x.cmp.to_left": "← Копировать", "x.cmp.sync": "Синхронизировать",
  "x.cmp.mirror": "Зеркало слева → вправо", "x.cmp.summary": "Различий: {diff} из {total}", "x.cmp.identical": "Папки одинаковые",
  "x.cmp.queued": "Копирование в очереди: {n}", "x.cmp.nothing": "Нечего копировать", "x.cmp.need_two": "Выберите две разные папки",
  "x.cmp.scanning": "Сравниваю…", "x.cmp.selected": "Выбрано: {n}",
  "x.img.title": "Преобразование изображений", "x.img.format": "Формат", "x.img.keep": "Не менять", "x.img.size": "Большая сторона",
  "x.img.orig": "Исходный размер", "x.img.quality": "Качество", "x.img.sub": "Сохранить в подпапку", "x.img.sub_hint": "Пусто — рядом с исходными",
  "x.img.sub_default": "Преобразованные", "x.img.run": "Преобразовать", "x.img.working": "Обрабатываю {i} из {n}…", "x.img.done": "Готово: {n}",
  "x.img.failed": "С ошибкой: {n}", "x.img.menu": "Преобразовать изображения…",
  "x.new.docx": "Документ Word", "x.new.xlsx": "Таблица Excel", "x.new.pptx": "Презентация PowerPoint", "x.new.py": "Скрипт Python",
  "x.new.ps1": "Скрипт PowerShell", "x.new.bat": "Пакетный файл (BAT)", "x.new.csv": "Таблица CSV", "x.new.html": "Страница HTML", "x.new.more": "Другие шаблоны",
  "x.new.doc_name": "Новый документ", "x.new.xls_name": "Новая таблица", "x.new.ppt_name": "Новая презентация", "x.new.script": "скрипт",
  "x.recent": "Недавние файлы", "x.recent_clear": "Очистить", "x.saved": "Сохранённые поиски", "x.save_search": "Сохранить поиск",
  "x.saved_name": "Название поиска", "side.saved_run": "Искать снова", "side.saved_tab": "Искать в новой вкладке", "x.saved_done": "Поиск сохранён", "x.saved_remove": "Удалить",
  "x.saved_where_all": "везде", "x.compare_tool_sub": "Найти различия и синхронизировать", "x.img_tool_sub": "Размер и формат пачкой",
  "x.eject": "Извлечь", "x.ejected": "Устройство {drive} можно извлечь", "err.eject_failed": "Диск занят — закройте файлы на нём и повторите",
  "br.tok.name": "Исходное имя", "br.tok.n": "Счётчик", "br.tok.n3": "Счётчик 001", "br.tok.ext": "Расширение", "br.tok.date": "Дата изменения",
  "br.tok.time": "Время изменения", "br.tok.parent": "Имя папки", "br.tok.created": "Дата создания", "br.tok.exif": "Дата съёмки (EXIF)",
  "br.tok.widthxheight": "Размер изображения, px",
  "err.no_editor": "Редактор кода не найден", "err.no_pillow": "Нет поддержки изображений (Pillow)", "err.bad_format": "Неизвестный формат",
});

const VIDEO = new Set(["mp4", "webm", "mov", "m4v", "ogv", "mkv"]);
const AUDIO = new Set(["mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "opus", "weba"]);
const ARCH = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "jar", "apk", "epub"]);
const TEXTISH = new Set(["txt", "md", "markdown", "log", "csv", "tsv", "json", "jsonc", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf", "env",
  "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "c", "h", "cpp", "hpp", "cc", "cs", "go", "rs", "rb", "php", "html", "htm", "css", "scss",
  "less", "sh", "bash", "zsh", "ps1", "bat", "cmd", "sql", "kt", "swift", "lua", "r", "pl", "vue", "svelte", "dart", "gradle", "properties",
  "gitignore", "dockerfile", "makefile", "srt", "vtt", "rst", "tex", "nfo", "reg", "editorconfig", "lock"]);

// ================================================================== Quick Look
let ql = null;

export function quickLook(app, pane) {
  if (ql) { closeQuickLook(); return; }
  if (!pane || pane.focus < 0 || !pane.shown[pane.focus]) return;
  // .modal-back: the window shortcuts (Delete, F2…) stay off while Quick Look is open
  const back = h("div", { class: "modal-back ql-back", role: "dialog", "aria-modal": "true" });
  const box = h("div", { class: "ql" });
  const head = h("div", { class: "ql-head" });
  const stage = h("div", { class: "ql-stage" });
  const foot = h("div", { class: "ql-foot", text: t("x.ql.hint") });
  box.append(head, stage, foot);
  back.append(box);
  document.body.append(back);
  const onKey = (e) => {
    if (!ql) return;
    const k = e.key;
    if (k === "Escape" || k === " ") { e.preventDefault(); e.stopPropagation(); closeQuickLook(); return; }
    if (k === "ArrowRight" || k === "ArrowDown" || k === "ArrowLeft" || k === "ArrowUp") {
      if (e.target.tagName === "VIDEO" || e.target.tagName === "AUDIO") return;
      e.preventDefault(); e.stopPropagation();
      step(k === "ArrowRight" || k === "ArrowDown" ? 1 : -1);
      return;
    }
    if (k === "Enter") { e.preventDefault(); e.stopPropagation(); const item = pane.shown[ql.index]; closeQuickLook(); app.openItem(pane, item); return; }
    if (k === "Home" || k === "End") { e.preventDefault(); e.stopPropagation(); go(k === "Home" ? 0 : pane.shown.length - 1); }
  };
  back.addEventListener("mousedown", (e) => { if (e.target === back) closeQuickLook(); });
  document.addEventListener("keydown", onKey, true);
  ql = { back, head, stage, pane, app, index: pane.focus, onKey, token: 0 };
  const go = (i) => {
    if (!ql || i < 0 || i >= pane.shown.length) return;
    ql.index = i;
    pane.setSelection([i], i); pane.anchor = i; pane.scrollTo(i);
    render();
  };
  const step = (d) => go(ql.index + d);
  ql.go = go;
  render();
}

export function closeQuickLook() {
  if (!ql) return;
  document.removeEventListener("keydown", ql.onKey, true);
  ql.stage.querySelectorAll("video,audio").forEach((m) => { try { m.pause(); } catch (e) { /* gone */ } });
  ql.back.remove();
  const pane = ql.pane;
  ql = null;
  if (pane && pane.fv) pane.fv.focus();
}

async function render() {
  const { pane, app, head, stage } = ql;
  const item = pane.shown[ql.index];
  const my = ++ql.token;
  const alive = () => ql && ql.token === my;
  head.innerHTML = "";
  const title = h("div", { class: "ql-title" });
  title.innerHTML = (item.d ? `<span class="fi">${folderSvg()}</span>` : fileIcon(item.n, false)) +
    `<div><b title="${esc(item.n)}">${esc(item.n)}</b><span>${esc([item.d ? "" : fmtSize(item.s), item.m ? fmtDate(item.m) : "", t("x.ql.of", { i: fmtNum(ql.index + 1), n: fmtNum(pane.shown.length) })].filter(Boolean).join(" · "))}</span></div>`;
  const nav = h("div", { class: "ql-nav" });
  const prev = h("button", { class: "icon-btn", title: "←", html: icon("back"), disabled: ql.index <= 0 });
  const next = h("button", { class: "icon-btn", title: "→", html: icon("fwd"), disabled: ql.index >= pane.shown.length - 1 });
  prev.addEventListener("click", () => ql.go(ql.index - 1));
  next.addEventListener("click", () => ql.go(ql.index + 1));
  const open = h("button", { class: "btn sm primary", html: icon("open") + esc(t("x.ql.open")) });
  open.addEventListener("click", () => { closeQuickLook(); app.openItem(pane, item); });
  const close = h("button", { class: "icon-btn", title: t("x.ql.close"), html: icon("x") });
  close.addEventListener("click", closeQuickLook);
  nav.append(prev, next, open, close);
  head.append(title, nav);
  stage.querySelectorAll("video,audio").forEach((m) => { try { m.pause(); } catch (e) { /* gone */ } });
  stage.innerHTML = "";
  stage.className = "ql-stage";
  const ext = extOf(item.n);
  const big = (html, note) => { stage.innerHTML = `<div class="ql-big">${html}${note ? `<span>${esc(note)}</span>` : ""}</div>`; };
  if (pane.mode === "trash") { big(fileIcon(item.n, item.d), item.original || ""); return; }
  if (item.d) {
    big(`<span class="fi">${folderSvg()}</span>`, t("type.folder"));
    try {
      const res = await runJob("size", { paths: [item.path], wait: 1.5 }, () => {});
      if (alive() && res) big(`<span class="fi">${folderSvg()}</span>`, t("x.ql.folder", { n: `${fmtSize(res.size)} · ${t("props.contents", { files: fmtNum(res.files), folders: fmtNum(res.folders) })}` }));
    } catch (e) { /* keep the icon */ }
    return;
  }
  if (BROWSER_IMG.has(ext) || IMAGE_EXT.has(ext)) {
    stage.classList.add("media");
    const src = BROWSER_IMG.has(ext) ? rawUrl(item.path) : `/api/thumb?path=${encodeURIComponent(item.path)}&s=1600&t=${encodeURIComponent(S.token)}`;
    const img = h("img", { src, alt: "", draggable: "false" });
    img.addEventListener("load", () => { if (alive()) { const s = $(".ql-title > div > span", head); if (s) s.textContent += ` · ${img.naturalWidth} × ${img.naturalHeight}`; } });
    img.addEventListener("error", () => { if (alive()) big(fileIcon(item.n, false), t("pv.cant")); });
    img.addEventListener("click", () => stage.classList.toggle("zoom"));
    stage.append(img);
    return;
  }
  if (VIDEO.has(ext) || AUDIO.has(ext)) {
    stage.classList.add("media");
    const media = h(VIDEO.has(ext) ? "video" : "audio", { src: rawUrl(item.path), controls: true, autoplay: true, preload: "metadata" });
    media.addEventListener("error", () => { if (alive()) big(fileIcon(item.n, false), t("pv.codec")); });
    if (AUDIO.has(ext)) stage.append(h("div", { class: "ql-big", html: fileIcon(item.n, false) }));
    stage.append(media);
    return;
  }
  if (ext === "pdf") {
    stage.classList.add("media");
    stage.append(h("iframe", { class: "ql-frame", src: rawUrl(item.path) + "#view=FitH", title: item.n }));
    return;
  }
  if (ARCH.has(ext)) {
    stage.innerHTML = `<div class="job-progress"><span class="spinner"></span></div>`;
    try {
      const res = await api("archive?path=" + encodeURIComponent(item.path));
      if (!alive()) return;
      if (res.error === "no_7zip") { big(icon("zip"), t("pv.no7zip")); return; }
      stage.innerHTML = `<div class="ql-arch"><div class="ar muted">${esc(t("pv.archive_summary", { n: fmtNum(res.total), size: fmtSize(res.unpacked) }))}</div>` +
        res.items.slice(0, 2000).map((r) => `<div class="ar">${r.is_dir ? icon("folder", "sm") : icon("file", "sm")}<span class="nm">${esc(r.name)}</span><span class="sz">${r.is_dir ? "" : esc(fmtSize(r.size))}</span></div>`).join("") + "</div>";
    } catch (err) { if (alive()) big(icon("warn"), errText(err)); }
    return;
  }
  if (TEXTISH.has(ext) || (!ext && item.s < 256 * 1024) || item.n.startsWith(".") || ext === "svg") {
    try {
      const res = await api("text?max=" + (400 * 1024) + "&path=" + encodeURIComponent(item.path));
      if (!alive()) return;
      if (res.binary) { big(fileIcon(item.n, false), t("x.ql.no_preview")); return; }
      if (ext === "md" || ext === "markdown") stage.append(h("div", { class: "pv-md ql-md", html: renderMarkdown(res.text) }));
      else {
        const code = h("div", { class: "pv-code ql-code", html: highlight(res.text, ext) });
        if (ext === "txt" || ext === "log") code.classList.add("wrap");
        stage.append(code);
      }
    } catch (err) { if (alive()) big(icon("warn"), errText(err)); }
    return;
  }
  big(fileIcon(item.n, false), `${typeOf(item.n, false) === "file" ? (ext || "").toUpperCase() : t("type." + typeOf(item.n, false))} · ${t("x.ql.no_preview")}`);
}

// ================================================================== copy path as…
function setClip(text, what) {
  const done = () => toast(t("x.copy.done", { what }), "info");
  navigator.clipboard.writeText(text).then(done).catch(() => {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); done();
  });
}

export function pathFormats(path) {
  const name = baseName(path) || path;
  const dot = name.lastIndexOf(".");
  const unix = path.replace(/\\/g, "/");
  const uriPath = unix.split("/").map((seg, i) => (i === 0 && /^[A-Za-z]:$/.test(seg) ? seg : encodeURIComponent(seg))).join("/");
  const wsl = /^[A-Za-z]:\//.test(unix) ? `/mnt/${unix[0].toLowerCase()}${unix.slice(2)}` : unix;
  return {
    path, quoted: `"${path}"`, name, stem: dot > 0 ? name.slice(0, dot) : name, folder: parentPath(path) || path, unix,
    uri: "file://" + (uriPath.startsWith("/") ? "" : "/") + uriPath,
    ps: `'${path.replace(/'/g, "''")}'`, bash: `'${wsl.replace(/'/g, "'\\''")}'`, md: `[${name}](<${uriPath.startsWith("/") ? "file://" : "file:///"}${uriPath}>)`,
  };
}

export function copyAsItems(paths) {
  const kinds = ["path", "quoted", "name", "stem", "folder", "unix", "uri", isWin() ? "ps" : null, "bash", "md"].filter(Boolean);
  return kinds.map((k) => ({ label: t("x.copy." + k), run: () => {
    const lines = paths.map((p) => pathFormats(p)[k]);
    setClip(lines.join("\n"), t("x.copy." + k).toLowerCase());
  } }));
}

// ================================================================== code editors
let editorsCache = null;
export async function editorItems(path) {
  if (!editorsCache) {
    try { editorsCache = (await api("x/editors")).editors; } catch (e) { editorsCache = []; }
    setTimeout(() => { editorsCache = null; }, 60000);
  }
  if (!editorsCache.length) return [{ label: t("x.editor_none"), disabled: true }];
  return editorsCache.map((e) => ({ label: e.name, icon: "code", run: () => openInEditor(path, e) }));
}
async function openInEditor(path, editor) {
  try { const res = await api("x/editor", { path, id: editor.id }); toast(t("x.editor_opened", { name: res.editor }), "info"); }
  catch (err) { showError(err); }
}

// ================================================================== new document templates
export function templateItems(app) {
  const make = (key, name) => ({ label: t("x.new." + key), icon: key === "py" || key === "ps1" || key === "bat" ? "code" : "file", run: () => newDoc(app, name) });
  return [
    make("docx", t("x.new.doc_name") + ".docx"), make("xlsx", t("x.new.xls_name") + ".xlsx"), make("pptx", t("x.new.ppt_name") + ".pptx"),
    { sep: true },
    make("csv", t("x.new.xls_name") + ".csv"), make("html", "index.html"), make("py", t("x.new.script") + ".py"),
    make("ps1", t("x.new.script") + ".ps1"),
    isWin() ? make("bat", t("x.new.script") + ".bat") : null,
  ].filter(Boolean);
}
export async function newDoc(app, name) {
  const pane = app.pane();
  if (pane.mode !== "dir") return;
  try {
    const res = await api("x/newdoc", { parent: pane.path, name });
    pane.pendingSelect = [res.path];
    await pane.reload(true);
    const idx = pane.index.get(res.path);
    if (idx !== undefined) pane.startRename(idx);
  } catch (err) { showError(err); }
}

// ================================================================== folder compare & sync
export function compareDialog(app, leftPath, rightPath) {
  const body = h("div", { class: "tool-wrap cmp" });
  const holder = {};
  const left = h("input", { class: "input mono", value: leftPath || "", spellcheck: "false" });
  const right = h("input", { class: "input mono", value: rightPath || "", spellcheck: "false" });
  const swap = h("button", { class: "icon-btn", title: "⇄", html: icon("split") });
  swap.addEventListener("click", () => { const v = left.value; left.value = right.value; right.value = v; });
  const recursive = h("input", { type: "checkbox", checked: true });
  const content = h("input", { type: "checkbox" });
  const runBtn = h("button", { class: "btn primary sm", html: icon("refresh") + esc(t("x.cmp.run")) });
  const paths = h("div", { class: "cmp-paths" },
    h("label", { class: "field" }, h("span", { text: t("x.cmp.left") }), left), swap,
    h("label", { class: "field" }, h("span", { text: t("x.cmp.right") }), right));
  const opts = h("div", { class: "tool-bar" }, h("label", { class: "check" }, recursive, t("x.cmp.recursive")),
    h("label", { class: "check" }, content, t("x.cmp.content")), h("span", { style: { flex: "1" } }), runBtn);
  const filters = h("div", { class: "seg cmp-filters", hidden: true });
  const list = h("div", { class: "cmp-list" });
  const summary = h("span", { class: "muted small" });
  body.append(paths, opts, filters, list);
  let result = null;
  let filter = "diff";
  const sel = new Set();
  const STATE_ICON = { same: "=", left: "◀", right: "▶", newer_left: "◀", newer_right: "▶", differ: "≠" };
  const match = (r) => filter === "all" || (filter === "diff" && r.state !== "same") || (filter === "left" && r.state === "left") ||
    (filter === "right" && r.state === "right") || (filter === "newer" && r.state.startsWith("newer")) || (filter === "same" && r.state === "same");
  const side = (x) => (x ? `<span class="sz">${x.d ? "" : esc(fmtSize(x.s))}</span><span class="dt">${esc(fmtDate(x.m))}</span>` : `<span class="sz"></span><span class="dt"></span>`);
  const draw = () => {
    if (!result) return;
    const rows = result.rows.filter(match);
    list.innerHTML = "";
    if (!rows.length) { list.append(h("div", { class: "job-progress", text: result.rows.some((r) => r.state !== "same") ? "—" : t("x.cmp.identical") })); }
    const frag = document.createDocumentFragment();
    for (const r of rows.slice(0, 3000)) {
      const row = h("div", { class: `cmp-row st-${r.state}${sel.has(r.rel) ? " on" : ""}`, title: r.rel });
      row.innerHTML = `<span class="nm">${r.d ? icon("folder", "sm") : icon("file", "sm")}<span>${esc(r.rel)}</span></span>${side(r.l)}` +
        `<span class="st" title="${esc(t("x.cmp.st." + r.state))}">${STATE_ICON[r.state]}</span>${side(r.r)}`;
      row.addEventListener("click", (e) => {
        if (!(e.ctrlKey || e.metaKey)) sel.clear();
        if (sel.has(r.rel)) sel.delete(r.rel); else sel.add(r.rel);
        draw();
      });
      frag.append(row);
    }
    list.append(frag);
    const diff = result.rows.filter((r) => r.state !== "same").length;
    summary.textContent = (sel.size ? t("x.cmp.selected", { n: fmtNum(sel.size) }) + " · " : "") + t("x.cmp.summary", { diff: fmtNum(diff), total: fmtNum(result.rows.length) });
    for (const b of filters.children) b.classList.toggle("on", b.dataset.f === filter);
  };
  for (const f of ["diff", "all", "left", "right", "newer", "same"]) {
    const b = h("button", { text: t("x.cmp.f." + f), "data-f": f });
    b.addEventListener("click", () => { filter = f; draw(); });
    filters.append(b);
  }
  const run = async () => {
    if (!left.value.trim() || !right.value.trim() || left.value.trim() === right.value.trim()) { toast(t("x.cmp.need_two"), "err"); return; }
    list.innerHTML = `<div class="job-progress"><span class="spinner"></span><span>${esc(t("x.cmp.scanning"))}</span></div>`;
    sel.clear();
    try {
      result = await runJob("compare", { left: left.value.trim(), right: right.value.trim(), recursive: recursive.checked, content: content.checked },
        (p) => { const s = list.querySelector(".job-progress span:last-child"); if (s) s.textContent = `${t("x.cmp.scanning")} ${fmtNum(p.files || 0)}`; }, holder);
    } catch (err) { list.innerHTML = ""; list.append(h("div", { class: "job-progress", text: errText(err) })); return; }
    if (!result) return;
    filters.hidden = false;
    draw();
  };
  runBtn.addEventListener("click", run);
  // copy rows to one side: each source goes into the matching parent folder of the other side (it always exists —
  // a folder present on one side only is listed as one row, its contents are not)
  const copy = async (rows, toRight, replace) => {
    const groups = new Map();
    for (const r of rows) {
      const src = joinPath(toRight ? result.left : result.right, r.rel.split("/").join(S.sep));
      const relParent = r.rel.includes("/") ? r.rel.slice(0, r.rel.lastIndexOf("/")).split("/").join(S.sep) : "";
      const destRoot = toRight ? result.right : result.left;
      const dest = relParent ? joinPath(destRoot, relParent) : destRoot;
      if (!groups.has(dest)) groups.set(dest, []);
      groups.get(dest).push(src);
    }
    let n = 0;
    for (const [dest, sources] of groups) {
      try {
        const res = await api("ops/submit", { kind: "copy", sources, dest, args: { on_conflict: replace ? "replace" : undefined, label: t("x.cmp.title") } });
        app.trackOp(res.op); n += sources.length;
      } catch (err) { showError(err); }
    }
    return n;
  };
  const report = (n) => toast(n ? t("x.cmp.queued", { n: itemsWord(n) }) : t("x.cmp.nothing"), n ? "ok" : "info");
  const pick = (states) => result.rows.filter((r) => (sel.size ? sel.has(r.rel) : true) && states.includes(r.state));
  const actions = h("div", { class: "cmp-actions" });
  const btn = (label, cls, fn) => { const b = h("button", { class: "btn sm " + cls, text: label }); b.addEventListener("click", () => { if (result) fn(); }); actions.append(b); return b; };
  btn(t("x.cmp.to_left"), "", async () => report(await copy(pick(["right", "newer_right", "differ"]), false, true)));
  btn(t("x.cmp.to_right"), "", async () => report(await copy(pick(["left", "newer_left", "differ"]), true, true)));
  btn(t("x.cmp.sync"), "primary", async () => report(await copy(pick(["left", "newer_left"]), true, true) + await copy(pick(["right", "newer_right"]), false, true)));
  const dlg = modal({ title: t("x.cmp.title"), icon: "split", body, xwide: true, left: h("div", { class: "cmp-foot" }, summary, actions),
    buttons: [{ label: t("common.close"), cls: "ghost" }],
    onClose: () => { if (holder.id) api("jobs/cancel", { id: holder.id }).catch(() => {}); } });
  dlg.el.classList.add("cmp-modal");
  if (leftPath && rightPath) run(); else setTimeout(() => (leftPath ? right : left).focus(), 40);
  return dlg;
}

// ================================================================== batch image conversion
export function isImage(row) { return row && !row.d && (BROWSER_IMG.has(extOf(row.n)) || IMAGE_EXT.has(extOf(row.n))) && extOf(row.n) !== "svg" && extOf(row.n) !== "ico"; }

export function imageDialog(app, paths) {
  const body = h("div", {});
  const fmt = h("select", { class: "input" });
  for (const [v, l] of [["keep", t("x.img.keep")], ["jpg", "JPG"], ["png", "PNG"], ["webp", "WebP"], ["bmp", "BMP"], ["gif", "GIF"], ["tiff", "TIFF"]]) fmt.append(h("option", { value: v, text: l }));
  fmt.value = "jpg";
  const size = h("select", { class: "input" });
  for (const v of [0, 3840, 2560, 1920, 1600, 1280, 1024, 800, 512]) size.append(h("option", { value: String(v), text: v ? `${v} px` : t("x.img.orig") }));
  size.value = "1920";
  const quality = h("input", { type: "range", min: "40", max: "100", value: "88", class: "range" });
  const qv = h("b", { class: "mono small", text: "88" });
  quality.addEventListener("input", () => { qv.textContent = quality.value; });
  const sub = h("input", { class: "input", value: t("x.img.sub_default"), spellcheck: "false" });
  const status = h("div", { class: "muted small", style: { minHeight: "18px" } });
  body.append(
    h("div", { class: "grid3" }, h("label", { class: "field" }, h("span", { text: t("x.img.format") }), fmt),
      h("label", { class: "field" }, h("span", { text: t("x.img.size") }), size),
      h("label", { class: "field" }, h("span", { class: "x-q" }, h("span", { text: t("x.img.quality") }), qv), quality)),
    h("label", { class: "field" }, h("span", { text: t("x.img.sub") }), sub, h("span", { class: "hint", text: t("x.img.sub_hint") })),
    status);
  const holder = {};
  modal({ title: t("x.img.title") + ` · ${itemsWord(paths.length)}`, icon: "image", body, wide: false,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("x.img.run"), cls: "primary", primary: true, run: async (btn) => {
      btn.disabled = true;
      try {
        const res = await runJob("imgconvert", { paths, format: fmt.value, max_side: +size.value, quality: +quality.value, subfolder: sub.value.trim() },
          (p) => { status.textContent = t("x.img.working", { i: fmtNum((p.files || 0) + 1), n: fmtNum(paths.length) }); }, holder);
        if (!res) return true;
        toast(t("x.img.done", { n: itemsWord(res.done.length) }) + (res.failed.length ? " · " + t("x.img.failed", { n: res.failed.length }) : ""), res.failed.length ? "err" : "ok");
        app.pane().reload(true);
        return true;
      } catch (err) { showError(err); btn.disabled = false; return false; }
    } }],
    onClose: () => { if (holder.id) api("jobs/cancel", { id: holder.id }).catch(() => {}); } });
}

// ================================================================== Home: recent files, saved searches, tools
export async function renderHomeExtras(app, pane, el) {
  const tools = el.querySelector(".home-grid:last-of-type");
  if (tools) {
    const card = (ico, title, sub, run) => {
      const b = h("button", { class: "home-card" });
      b.innerHTML = `<span class="hc-ico">${icon(ico)}</span><span class="hc-txt"><b>${esc(title)}</b><span>${esc(sub)}</span></span>`;
      b.addEventListener("click", run);
      return b;
    };
    tools.append(card("split", t("x.cmp.title"), t("x.compare_tool_sub"), () => { const other = app.otherPane && app.otherPane(); compareDialog(app, other ? other.folder : "", ""); }));
  }
  let recent = [];
  let saved = [];
  try { [recent, saved] = await Promise.all([api("x/recent").then((r) => r.items), api("x/saved").then((r) => r.items)]); } catch (e) { return; }
  if (!document.body.contains(el) || pane.path !== SPECIAL.HOME) return;
  if (saved.length) {
    const sec = h("section", { class: "home-x" });
    sec.append(h("h3", { text: t("x.saved") }));
    const chips = h("div", { class: "x-chips" });
    for (const s of saved) {
      const c = h("button", { class: "chip x-saved", title: `${s.query} · ${s.scope === "everywhere" ? t("x.saved_where_all") : s.root}`, html: icon("search", "sm") + `<span>${esc(s.name)}</span>` });
      c.addEventListener("click", () => runSaved(app, s));
      c.addEventListener("contextmenu", async (e) => { e.preventDefault(); c.remove(); await removeSaved(app, s); });
      chips.append(c);
    }
    sec.append(chips);
    el.insertBefore(sec, el.querySelector("h3"));
  }
  if (recent.length) {
    const sec = h("section", { class: "home-x" });
    const hd = h("div", { class: "x-head" }, h("h3", { text: t("x.recent") }));
    const clear = h("button", { class: "btn sm ghost", text: t("x.recent_clear") });
    clear.addEventListener("click", async () => { await api("x/recent", { clear: true }).catch(showError); sec.remove(); });
    hd.append(clear);
    const list = h("div", { class: "x-recent" });
    for (const r of recent.slice(0, 12)) {
      const row = h("button", { class: "x-rrow", title: r.path });
      row.innerHTML = fileIcon(baseName(r.path), false) + `<span class="nm">${esc(baseName(r.path))}</span><span class="fd">${esc(parentPath(r.path))}</span><span class="dt">${esc(fmtDate(r.at))}</span>`;
      row.addEventListener("click", () => api("open", { path: r.path }).catch(showError));
      row.addEventListener("contextmenu", (e) => { e.preventDefault(); app.revealPath(r.path); });
      list.append(row);
    }
    sec.append(hd, list);
    el.insertBefore(sec, el.querySelector("h3:not(.home-x h3)") || null);
  }
}

export function runSaved(app, s) {
  app.searchInput.value = s.query;
  app.searchScopeValue = s.scope === "everywhere" ? "everywhere" : "folder";
  app.updateSearchScope();
  app.pane().runSearch(s.query, app.searchScopeValue, s.root || S.home);
}

export async function saveSearch(app, pane) {
  const s = pane.search;
  if (!s || !s.query) return;
  const { promptDialog } = await import("./core.js");
  const name = await promptDialog({ title: t("x.save_search"), label: t("x.saved_name"), value: s.query, icon: "search" });
  if (!name) return;
  try { await api("x/saved", { name, query: s.query, root: s.root || "", scope: s.scope || "folder" }); toast(t("x.saved_done")); app.loadSidebar(); }
  catch (err) { showError(err); }
}
/** r40-files-2: remove a saved search (Home chip, sidebar «smart folder»). */
export async function removeSaved(app, s) {
  try { await api("x/saved", { remove: s.id }); app.loadSidebar(); } catch (err) { showError(err); }
}

// ================================================================== squarified treemap for «Место на диске»
export function treemap(children, total, colors, onPick) {
  const box = h("div", { class: "treemap sq" });
  const items = children.filter((c) => c.size > 0 && c.size / (total || 1) > 0.004).slice(0, 60);
  requestAnimationFrame(() => {
    const W = box.clientWidth || 800, H = box.clientHeight || 200;
    const sum = items.reduce((a, c) => a + c.size, 0) || 1;
    const scaled = items.map((c) => ({ c, a: (c.size / sum) * W * H }));
    let x = 0, y = 0, w = W, hgt = H, row = [];
    const worst = (r, side) => { const s = r.reduce((a, o) => a + o.a, 0); const mx = Math.max(...r.map((o) => o.a)); const mn = Math.min(...r.map((o) => o.a)); return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn)); };
    const layoutRow = (r) => {
      const s = r.reduce((a, o) => a + o.a, 0);
      if (w >= hgt) { const cw = s / hgt; let cy = y; for (const o of r) { const ch = o.a / cw; place(o, x, cy, cw, ch); cy += ch; } x += cw; w -= cw; }
      else { const rh = s / w; let cx = x; for (const o of r) { const cw = o.a / rh; place(o, cx, y, cw, rh); cx += cw; } y += rh; hgt -= rh; }
    };
    let i = 0;
    const place = (o, px, py, pw, ph) => {
      const d = h("div", { style: { left: px + "px", top: py + "px", width: Math.max(0, pw - 2) + "px", height: Math.max(0, ph - 2) + "px", background: colors[i++ % colors.length] },
        title: `${o.c.name} — ${fmtSize(o.c.size)}` });
      if (pw > 46 && ph > 22) d.innerHTML = `<b>${esc(o.c.name)}</b>${ph > 38 ? `<span>${esc(fmtSize(o.c.size))}</span>` : ""}`;
      d.addEventListener("click", () => onPick(o.c));
      box.append(d);
    };
    for (const o of scaled) {
      const side = Math.min(w, hgt);
      if (!row.length || worst([...row, o], side) <= worst(row, side)) row.push(o);
      else { layoutRow(row); row = [o]; }
    }
    if (row.length) layoutRow(row);
  });
  return box;
}

// ================================================================== drives
export function ejectItem(app, path) {
  if (!isWin()) return null;
  const d = (app.sidebar.drives || []).find((x) => x.path && x.path.toLowerCase() === String(path).toLowerCase());
  if (!d || d.kind !== "removable") return null;
  return { label: t("x.eject"), icon: "up", run: async () => {
    try { await api("x/eject", { path }); toast(t("x.ejected", { drive: path.replace(/\\$/, "") })); app.loadSidebar(); } catch (err) { showError(err); }
  } };
}

