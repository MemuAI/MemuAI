
import { t } from "./i18n.js";
import { S, api, $, h, esc, icon, fileIcon, fmtSize, fmtNum, fmtDate, itemsWord, baseName, extOf, rawUrl, typeOf,
  BROWSER_IMG, menu } from "./core.js";
import { typeLabel } from "./pane.js";
import { runJob } from "./dialogs.js";

const TEXT_EXT = new Set(["txt", "md", "markdown", "log", "csv", "tsv", "json", "jsonc", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf", "env",
  "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "c", "h", "cpp", "hpp", "cc", "cs", "go", "rs", "rb", "php", "html", "htm", "css", "scss",
  "less", "sh", "bash", "zsh", "ps1", "bat", "cmd", "sql", "kt", "swift", "lua", "r", "pl", "vue", "svelte", "dart", "gradle", "properties",
  "gitignore", "dockerfile", "makefile", "srt", "vtt", "rst", "tex", "nfo", "reg", "editorconfig", "lock", "svg"]);
const VIDEO = new Set(["mp4", "webm", "mov", "m4v", "ogv", "mkv"]);
const AUDIO = new Set(["mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "opus", "weba"]);
const ARCH = new Set(["zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "jar", "apk", "docx", "xlsx", "pptx", "epub"]);
let host = null;
let token = 0;

export function show(app, target, el) {
  if (el) host = el;
  if (!host || !document.body.contains(host) || app.right !== "preview") return;
  const my = ++token;
  host.innerHTML = "";
  const wrap = h("div", { class: "pv" });
  host.append(wrap);
  if (!target) return empty(wrap);
  if (target.multi) return multi(app, wrap, target.multi, my);
  if (target.folder !== undefined && !target.item) {
    if (!target.folder) return empty(wrap);
    return folder(app, wrap, { n: baseName(target.folder) || target.folder, path: target.folder, d: 1 }, my, true);
  }
  const item = target.item;
  if (target.pane && target.pane.mode === "trash") return trashItem(wrap, item);
  if (item.d) return folder(app, wrap, item, my, false);
  return file(app, wrap, item, my);
}

function empty(wrap) { wrap.innerHTML = `<div class="pv-empty">${icon("preview")}<span>${esc(t("pv.empty"))}</span></div>`; }

function header(wrap, item, sub) {
  const head = h("div", { class: "pv-head" });
  head.innerHTML = fileIcon(item.n, item.d) + `<div class="pv-title"><b>${esc(item.n)}</b><span>${esc(sub)}</span></div>`;
  wrap.append(head);
}
function actions(app, wrap, item) {
  const row = h("div", { class: "pv-actions" });
  const open = h("button", { class: "btn sm primary", html: icon("open") + esc(t("ctx.open")) });
  open.addEventListener("click", () => app.openItem(app.pane(), item));
  row.append(open);
  if (!item.d && S.platform === "nt") {
    const w = h("button", { class: "btn sm", html: icon("windows") + esc(t("ctx.open_with")) });

    w.addEventListener("click", () => app.openWithItems(item.path).then((items) => {
      const r = w.getBoundingClientRect();
      menu(items, r.left, r.bottom + 4);
    }));
    row.append(w);
  }
  const copy = h("button", { class: "btn sm ghost", html: icon("link") + esc(t("cmd.copy_path")) });
  copy.addEventListener("click", () => app.copyPaths([item.path]));
  row.append(copy);
  wrap.append(row);
}

function pathSpan(path) {
  return h("span", { class: "pv-path", title: path, html: String(path).split(/(?<=[\\/])/).map(esc).join("<wbr>") });
}
function info(wrap, rows) {
  const kv = h("div", { class: "kv pv-info" });
  for (const [k, v] of rows) if (v !== "" && v !== undefined && v !== null) kv.append(h("span", { text: k }), k === t("props.location") ? pathSpan(v) : h("span", { text: v }));
  wrap.append(kv);
}

async function file(app, wrap, item, my) {
  const ext = extOf(item.n);
  header(wrap, item, `${typeLabel(item)} · ${fmtSize(item.s)}`);
  actions(app, wrap, item);
  const meta = [[t("props.modified"), fmtDate(item.m)], [t("props.created"), fmtDate(item.c)], [t("props.location"), item.path.slice(0, item.path.length - item.n.length - 1) || item.path]];
  if (BROWSER_IMG.has(ext)) {
    const box = h("div", { class: "pv-media" });
    const img = h("img", { src: rawUrl(item.path), alt: "" });
    img.addEventListener("load", () => { if (my === token) dims.textContent = `${img.naturalWidth} × ${img.naturalHeight} px`; });
    img.addEventListener("error", () => { box.innerHTML = `<div class="pv-empty">${icon("warn")}<span>${esc(t("pv.cant"))}</span></div>`; });
    box.append(img);
    wrap.append(box);
    const dims = h("div", { class: "pv-note", style: { marginTop: "8px" } });
    wrap.append(dims);
    info(wrap, meta);
    return;
  }
  if (typeOf(item.n, false) === "image") {
    const box = h("div", { class: "pv-media" });
    const img = h("img", { src: `/api/thumb?path=${encodeURIComponent(item.path)}&s=900&t=${encodeURIComponent(S.token)}`, alt: "" });
    img.addEventListener("error", () => { box.innerHTML = `<div class="pv-big">${fileIcon(item.n, false)}</div>`; });
    box.append(img); wrap.append(box); info(wrap, meta); return;
  }
  if (VIDEO.has(ext) || AUDIO.has(ext)) {
    const box = h("div", { class: "pv-media" });
    const media = h(VIDEO.has(ext) ? "video" : "audio", { src: rawUrl(item.path), controls: true, preload: "metadata" });
    media.addEventListener("error", () => { box.innerHTML = `<div class="pv-empty">${icon("warn")}<span>${esc(t("pv.codec"))}</span></div>`; });
    box.append(media); wrap.append(box); info(wrap, meta); return;
  }
  if (ext === "pdf") {
    const frame = h("iframe", { class: "pv-frame", src: rawUrl(item.path) + "#toolbar=0&view=FitH", title: item.n });
    wrap.append(frame); info(wrap, meta); return;
  }
  if (ARCH.has(ext)) {
    const list = h("div", { class: "pv-arch", html: `<div class="job-progress"><span class="spinner"></span></div>` });
    wrap.append(list);
    try {
      const res = await api("archive?path=" + encodeURIComponent(item.path));
      if (my !== token) return;
      if (res.error === "no_7zip") { list.innerHTML = `<div class="pv-empty">${icon("zip")}<span>${esc(t("pv.no7zip"))}</span></div>`; }
      else {
        list.innerHTML = `<div class="ar muted"><span class="nm">${esc(t("pv.archive_summary", { n: fmtNum(res.total), size: fmtSize(res.unpacked) }))}</span></div>` +
          res.items.slice(0, 1000).map((r) => `<div class="ar">${r.is_dir ? icon("folder", "sm") : icon("file", "sm")}<span class="nm" title="${esc(r.name)}">${esc(r.name)}</span><span class="sz">${r.is_dir ? "" : esc(fmtSize(r.size))}</span></div>`).join("");
      }
      const ex = h("button", { class: "btn sm", html: icon("extract") + esc(t("ctx.extract")) });
      ex.addEventListener("click", () => app.extract(item.path, false));
      $(".pv-actions", wrap).append(ex);
    } catch (err) { list.innerHTML = `<div class="pv-empty">${icon("warn")}<span>${esc(err.message)}</span></div>`; }
    info(wrap, meta);
    return;
  }
  if (TEXT_EXT.has(ext) || (item.s < 256 * 1024 && !ext) || item.n.startsWith(".")) {
    const box = h("div", { class: "pv-code", html: "" });
    wrap.append(box);
    try {
      const res = await api("text?max=" + (200 * 1024) + "&path=" + encodeURIComponent(item.path));
      if (my !== token) return;
      if (res.binary) { box.remove(); wrap.append(h("div", { class: "pv-big", html: fileIcon(item.n, false) })); info(wrap, meta); return; }
      if (ext === "md" || ext === "markdown") {
        box.remove();
        const md = h("div", { class: "pv-md", html: renderMarkdown(res.text) });
        wrap.append(md);
      } else {
        box.innerHTML = highlight(res.text, ext);
        if (ext === "txt" || ext === "log" || ext === "md") box.classList.add("wrap");
      }
      const note = [t("pv.lines", { n: fmtNum(res.lines) }), res.encoding.toUpperCase()];
      if (res.truncated) note.push(t("pv.truncated"));
      wrap.append(h("div", { class: "pv-note", text: note.join(" · ") }));
    } catch (err) { box.innerHTML = esc(err.message); }
    info(wrap, meta);
    return;
  }
  wrap.append(h("div", { class: "pv-big", html: fileIcon(item.n, false) }));
  info(wrap, meta);
}

async function folder(app, wrap, item, my, current) {
  header(wrap, item, current ? t("pv.this_folder") : t("type.folder"));
  if (!current) actions(app, wrap, item);
  const kv = h("div", { class: "kv pv-info" });
  const size = h("span", { text: t("props.calculating") });
  const contents = h("span", { text: "…" });
  kv.append(h("span", { text: t("props.size") }), size, h("span", { text: t("props.contains") }), contents);
  if (item.m) kv.append(h("span", { text: t("props.modified") }), h("span", { text: fmtDate(item.m) }));
  kv.append(h("span", { text: t("props.location") }), pathSpan(item.path.slice(0, item.path.length - item.n.length - 1) || item.path));
  wrap.append(kv);
  const holder = {};
  const res = await runJob("size", { paths: [item.path] }, (p) => { if (my === token) size.textContent = t("props.calculating") + " " + fmtSize(p.bytes_now || 0); }, holder);
  if (my !== token) { if (holder.id) api("jobs/cancel", { id: holder.id }).catch(() => {}); return; }
  if (res) { size.textContent = fmtSize(res.size, true); contents.textContent = t("props.contents", { files: fmtNum(res.files), folders: fmtNum(res.folders) }); }
}

function multi(app, wrap, items, my) {
  const files = items.filter((r) => !r.d);
  const size = files.reduce((a, r) => a + (r.s || 0), 0);
  const head = h("div", { class: "pv-head" });
  head.innerHTML = `<span class="fi">${icon("copy")}</span><div class="pv-title"><b>${esc(t("status.selected", { n: itemsWord(items.length) }))}</b><span>${esc(fmtSize(size))}</span></div>`;
  wrap.append(head);
  const images = files.filter((r) => BROWSER_IMG.has(extOf(r.n))).slice(0, 9);
  if (images.length) {
    const grid = h("div", { style: { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "6px", margin: "0 14px 12px" } });
    for (const r of images) grid.append(h("img", { src: `/api/thumb?path=${encodeURIComponent(r.path)}&s=160&t=${encodeURIComponent(S.token)}`, style: { width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: "8px" }, alt: "" }));
    wrap.append(grid);
  }
  const types = new Map();
  for (const r of items) { const ty = typeOf(r.n, r.d); types.set(ty, (types.get(ty) || 0) + 1); }
  info(wrap, [[t("pv.files"), fmtNum(files.length)], [t("pv.folders"), fmtNum(items.length - files.length)],
    [t("pv.types"), [...types.entries()].map(([k, v]) => `${t("type." + k)} ${v}`).join(", ")]]);
}

function trashItem(wrap, item) {
  header(wrap, item, t("side.trash"));
  info(wrap, [[t("trash.original"), item.original], [t("trash.deleted"), item.deleted ? fmtDate(item.deleted) : item.deletedText], [t("props.size"), item.d ? "" : fmtSize(item.s)]]);
}

const KW = {
  js: "break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new of return super switch this throw try typeof var void while with yield async await static get set true false null undefined",
  py: "and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None self",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected namespace using template typename virtual override new delete true false nullptr bool string var func package import type interface fn let mut impl pub use mod match",
  sql: "select from where and or not insert into values update set delete create table index drop alter join left right inner outer on group by order having limit as distinct union null is in like between",
  sh: "if then else elif fi for while do done case esac function in return export local echo exit",
};
const FAMILY = { js: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js", jsx: "js", json: "js", jsonc: "js", vue: "js", py: "py", c: "c", h: "c", cpp: "c", hpp: "c", cc: "c", cs: "c", java: "c", go: "c", rs: "c", kt: "c", swift: "c", dart: "c", php: "c", sql: "sql", sh: "sh", bash: "sh", zsh: "sh", ps1: "sh", bat: "sh", cmd: "sh" };
export function highlight(text, ext) {
  const fam = FAMILY[ext];
  const lines = text.split("\n");
  if (lines.length > 6000) lines.length = 6000;
  if (!fam && !["html", "htm", "xml", "svg", "css", "scss", "yaml", "yml", "toml", "ini"].includes(ext)) return lines.map((l) => `<span class="ln">${esc(l) || " "}</span>`).join("");
  const words = new Set((KW[fam] || "").split(" "));
  const comment = fam === "py" || fam === "sh" || ["yaml", "yml", "toml"].includes(ext) ? "#" : ext === "ini" ? ";" : fam === "sql" ? "--" : "//";
  const re = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\/\*.*?\*\/|<!--.*?-->)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$-]*)|(\S)/g;
  return lines.map((line) => {
    let out = "";
    let rest = line;
    let tail = "";
    const ci = findComment(line, comment);
    if (ci >= 0) { rest = line.slice(0, ci); tail = `<span class="tk-com">${esc(line.slice(ci))}</span>`; }
    let last = 0;
    rest.replace(re, (m, str, block, num, fn, word, other, offset) => {
      out += esc(rest.slice(last, offset));
      last = offset + m.length;
      if (str) out += `<span class="tk-str">${esc(m)}</span>`;
      else if (block) out += `<span class="tk-com">${esc(m)}</span>`;
      else if (num) out += `<span class="tk-num">${m}</span>`;
      else if (fn) out += words.has(fn) ? `<span class="tk-kw">${esc(fn)}</span>` : `<span class="tk-fn">${esc(fn)}</span>`;
      else if (word) out += words.has(fam === "sql" ? word.toLowerCase() : word) ? `<span class="tk-kw">${esc(word)}</span>` : /^[A-Z][a-z]/.test(word) ? `<span class="tk-type">${esc(word)}</span>` : esc(word);
      else out += esc(m);
      return m;
    });
    out += esc(rest.slice(last));
    return `<span class="ln">${out + tail || " "}</span>`;
  }).join("");
}
function findComment(line, marker) {
  let quote = null;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) { if (ch === "\\") i++; else if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'" || ch === "`") { quote = ch; continue; }
    if (line.startsWith(marker, i) && (marker !== "#" || i === 0 || /\s/.test(line[i - 1]))) return i;
  }
  return -1;
}

export function renderMarkdown(src) {
  const lines = src.replace(/\r/g, "").split("\n").slice(0, 4000);
  let html = "", inCode = false, inList = null, para = [];
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/__([^_]+)__/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const flush = () => { if (para.length) { html += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const closeList = () => { if (inList) { html += `</${inList}>`; inList = null; } };
  for (const line of lines) {
    if (line.startsWith("```")) { flush(); closeList(); html += inCode ? "</code></pre>" : "<pre><code>"; inCode = !inCode; continue; }
    if (inCode) { html += esc(line) + "\n"; continue; }
    const hm = /^(#{1,6})\s+(.*)$/.exec(line);
    if (hm) { flush(); closeList(); const n = Math.min(3, hm[1].length); html += `<h${n}>${inline(hm[2])}</h${n}>`; continue; }
    const li = /^\s*([-*+]|\d+\.)\s+(.*)$/.exec(line);
    if (li) { flush(); const type = /\d/.test(li[1]) ? "ol" : "ul"; if (inList !== type) { closeList(); html += `<${type}>`; inList = type; } html += `<li>${inline(li[2])}</li>`; continue; }
    if (/^>\s?/.test(line)) { flush(); closeList(); html += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`; continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flush(); closeList(); html += "<hr>"; continue; }
    if (!line.trim()) { flush(); closeList(); continue; }
    para.push(line.trim());
  }
  flush(); closeList();
  if (inCode) html += "</code></pre>";
  return html;
}
