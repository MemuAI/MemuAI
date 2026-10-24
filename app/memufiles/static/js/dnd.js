// MemuFiles · drag & drop (Round 40, r40-files-core).
//
// Sources and how each one is handled:
//   • our own rows (another pane, a tab, the tree, the breadcrumbs) — paths in "application/x-memufiles";
//   • files and folders from Explorer — WebView2 hides their paths from the page; pywebview's document-level drop
//     listener (desktop.py) reports them in a backend "drop" event, so the page waits for it briefly and copies /
//     moves by path (fast, no bytes through HTTP, Shift = move);
//   • VIRTUAL files with no path at all — a zip folder open in Explorer, Outlook attachments, images dragged out of a
//     browser — are read with the File API (folders via webkitGetAsEntry) and streamed into the target folder as an
//     "upload" operation (same progress card, conflicts and cancel as a copy);
//   • links dropped from a browser — downloaded by the backend ("download" operation); data: URLs are uploaded.
// The "Copy to …" hint is removed on drop, dragleave out of the window, dragend, Esc, window blur, the first mouse
// move after a drag (no mouse events fire during one) and by a watchdog when dragover events stop arriving —
// Chromium does not always deliver drop / dragleave for virtual files, which is what left the hint stuck.
import { S, api, h, esc, icon, baseName, isWin, toast, showError, rawUrl } from "./core.js";
import { t, addStrings } from "./i18n.js";

addStrings({
  "dnd2.copy_n": "Copy {n} to", "dnd2.move_n": "Move {n} to", "dnd2.copy": "Copy to", "dnd2.move": "Move to",
  "dnd2.download": "Download to", "dnd2.nothing": "Nothing to copy: the dragged item has no files or links",
  "dnd2.read_failed": "The source app did not hand over the file", "dnd2.dropped": "Dropped files",
  "dnd2.reading": "Reading the dropped files…",
}, {
  "dnd2.copy_n": "Копировать {n} в", "dnd2.move_n": "Переместить {n} в", "dnd2.copy": "Копировать в", "dnd2.move": "Переместить в",
  "dnd2.download": "Скачать в", "dnd2.nothing": "Копировать нечего: в перетаскиваемом нет файлов или ссылок",
  "dnd2.read_failed": "Программа-источник не отдала файл", "dnd2.dropped": "Перетащенные файлы",
  "dnd2.reading": "Читаю перетащенные файлы…",
});

const WATCHDOG_MS = 900;          // no dragover for this long = the drag is over (whatever the browser reported)
const BACKEND_WAIT_MS = 1500;     // how long a desktop drop waits for pywebview's paths before reading via File API
const INTERNAL = "application/x-memufiles";

// ------------------------------------------------------------------ the hint
let hintEl = null, lastOver = 0, watch = 0, cleanupBound = false;
export function showHint(app, e, effect, target) {
  if (!hintEl) {
    hintEl = h("div", { class: "drop-hint", role: "status" });
    document.body.append(hintEl);
    bindCleanup(app);
    // no mouse events fire during a drag — the first one means it is over (a drop the browser never delivered)
    window.addEventListener("pointermove", hideOnPointer, { passive: true });
    window.addEventListener("pointerdown", hideOnPointer, true);
  }
  const dt = e.dataTransfer;
  const internal = dt.types.includes(INTERNAL);
  const n = internal ? (app.dragPaths || []).length : Array.from(dt.items || []).filter((i) => i.kind === "file").length;
  const links = !internal && !n && (dt.types.includes("text/uri-list"));
  const verb = links ? t("dnd2.download") : n > 1 ? t(effect === "move" ? "dnd2.move_n" : "dnd2.copy_n", { n }) : t(effect === "move" ? "dnd2.move" : "dnd2.copy");
  const key = verb + "|" + target + "|" + effect;
  if (hintEl._key !== key) {
    hintEl._key = key;
    hintEl.className = "drop-hint " + effect;
    hintEl.innerHTML = `<span class="dh-ico">${icon(effect === "move" ? "cut" : links ? "link" : "plus", "sm")}</span><span>${esc(verb)}</span><b>${esc(baseName(target) || target)}</b>`;
  }
  // keep the pill inside the window
  const x = Math.min(e.clientX + 16, window.innerWidth - (hintEl.offsetWidth || 160) - 6);
  const y = Math.min(e.clientY + 20, window.innerHeight - 34);
  hintEl.style.transform = `translate(${Math.max(4, x)}px, ${Math.max(4, y)}px)`;
  lastOver = performance.now();
  if (!watch) watch = setTimeout(tick, WATCHDOG_MS);
}
function tick() {
  watch = 0;
  if (!hintEl) return;
  if (performance.now() - lastOver >= WATCHDOG_MS) { hideHint(); return; }
  watch = setTimeout(tick, WATCHDOG_MS - (performance.now() - lastOver) + 20);
}
export function hideHint(app) {
  if (hintEl) { hintEl.remove(); hintEl = null; }
  if (hideOnPointer) { window.removeEventListener("pointermove", hideOnPointer); window.removeEventListener("pointerdown", hideOnPointer, true); }
  clearTimeout(watch); watch = 0;
  const a = app || boundApp;
  if (a && a.dropEl) { a.dropEl.classList.remove("drop"); a.dropEl = null; }
}
let boundApp = null, hideOnPointer = null;
function bindCleanup(app) {
  boundApp = app;
  if (cleanupBound) return;
  cleanupBound = true;
  const hide = () => { if (hintEl) hideHint(); };
  hideOnPointer = hide;
  window.addEventListener("drop", hide, true);                 // capture: runs even if a handler stops propagation
  window.addEventListener("dragend", hide, true);
  window.addEventListener("dragleave", (e) => {
    // leaving the window itself (relatedTarget is null and the pointer is at / outside the edge)
    if (!e.relatedTarget && (e.clientX <= 0 || e.clientY <= 0 || e.clientX >= window.innerWidth || e.clientY >= window.innerHeight)) hide();
  }, true);
  window.addEventListener("blur", hide);
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); }, true);
  document.addEventListener("visibilitychange", () => { if (document.hidden) hide(); });
}

// ------------------------------------------------------------------ drag over / drop
export function canAccept(dt) {
  const types = dt.types;
  return types.includes(INTERNAL) || types.includes("Files") || types.includes("text/uri-list");
}

/** Synchronous part of a drop of things from outside MemuFiles: everything readable must be taken now. */
export function captureExternal(e) {
  const dt = e.dataTransfer;
  const files = Array.from(dt.files || []);
  const items = Array.from(dt.items || []).filter((i) => i.kind === "file");
  const entries = items.map((i) => { try { return i.webkitGetAsEntry ? i.webkitGetAsEntry() : null; } catch (err) { return null; } });
  let uris = [];
  if (!files.length) {
    const list = dt.getData("text/uri-list") || "";
    uris = list.split(/\r?\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith("#"));
    if (!uris.length) {
      const html = dt.getData("text/html") || "";
      const m = html.match(/<img[^>]+src=["']([^"']+)["']/i) || html.match(/<a[^>]+href=["']([^"']+)["']/i);
      if (m) uris = [m[1].replace(/&amp;/g, "&")];
    }
  }
  return { files, entries, uris, shift: e.shiftKey, ctrl: e.ctrlKey };
}

export async function externalDrop(app, cap, target) {
  const effect = cap.shift && !cap.ctrl ? "move" : "copy";
  if (cap.files.length) {
    let rows = null;
    if (S.boot && S.boot.desktop && isWin()) rows = await app.waitBackendDrop(BACKEND_WAIT_MS);
    const withPath = [], virtual = [];
    const pool = (rows || []).filter((r) => r.path);
    cap.files.forEach((file, i) => {
      const k = pool.findIndex((r) => r.name === file.name);
      if (k >= 0) { withPath.push(pool[k].path); pool.splice(k, 1); }
      else virtual.push({ file, entry: cap.entries[i] || null });
    });
    if (withPath.length) app.transfer(withPath, target, effect);
    if (virtual.length) await uploadVirtual(app, virtual, target);
    return;
  }
  if (cap.uris.length) {
    for (const url of cap.uris.slice(0, 20)) {
      if (/^data:/i.test(url)) {
        try {
          const blob = await (await fetch(url)).blob();
          const ext = (blob.type.split("/")[1] || "bin").replace("jpeg", "jpg").replace(/\+.*/, "");
          await upload(app, [{ file: blob, rel: `image-${stamp()}.${ext}`, size: blob.size, mtime: Date.now() }], target, t("dnd2.dropped"));
        } catch (err) { showError(err); }
      } else if (/^https?:/i.test(url)) {
        try { const res = await api("ops/submit", { kind: "download", url, dest: target }); app.trackOp(res.op); } catch (err) { showError(err); }
      }
    }
    return;
  }
  toast(t("dnd2.nothing"), "info");
}
function stamp() {
  const d = new Date(); const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

// ------------------------------------------------------------------ File API → upload
async function readTree(entry, prefix, out) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    out.push({ file, rel: prefix + entry.name, size: file.size, mtime: file.lastModified || 0 });
    return;
  }
  if (!entry.isDirectory) return;
  const rel = prefix + entry.name;
  out.push({ rel, dir: true });
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));   // ≤100 per call
    if (!batch.length) break;
    for (const child of batch) await readTree(child, rel + "/", out);
  }
}
async function uploadVirtual(app, virtual, target) {
  const list = [];
  for (const { file, entry } of virtual) {
    try {
      if (entry && entry.isDirectory) await readTree(entry, "", list);
      else list.push({ file, rel: file.name, size: file.size, mtime: file.lastModified || 0 });
    } catch (err) {
      list.push({ file, rel: file.name, size: file.size, mtime: file.lastModified || 0 });
    }
  }
  if (list.length) await upload(app, list, target, t("dnd2.dropped"));
}

/** Stream File/Blob objects into *dest* as one "upload" operation (progress, pause, skip, conflicts, cancel). */
export async function upload(app, list, dest, label) {
  let res;
  try { res = await api("upload/start", { dest, label: label || "", items: list.map((x) => ({ rel: x.rel, size: x.size || 0, mtime: x.mtime || 0, dir: !!x.dir })) }); }
  catch (err) { showError(err); return null; }
  const id = res.op.id;
  app.trackOp(res.op);
  for (let i = 0; i < list.length; i++) {
    const item = list[i];
    if (item.dir) continue;
    let prep;
    try { prep = await api("upload/prepare", { op: id, i }); } catch (err) { break; }
    if (prep.state === "cancelled") break;
    if (prep.state !== "ready") continue;
    try {
      const resp = await fetch(`/api/upload/put?op=${encodeURIComponent(id)}&i=${i}`, { method: "POST", headers: { "X-MF-Token": S.token, "Content-Type": "application/octet-stream" }, body: item.file });
      const data = await resp.json().catch(() => ({}));
      if (data.state === "cancelled") break;
      if (!resp.ok && data.error === "no_operation") break;
    } catch (err) {
      // the body could not be read (a virtual file its source app no longer serves) or the backend dropped the
      // connection after "skip file" / cancel — the backend knows which; a still-pending item becomes an error
      try { await api("upload/fail", { op: id, i, error: t("dnd2.read_failed") }); } catch (e) { /* ignore */ }
    }
  }
  return id;
}

// ------------------------------------------------------------------ dragging out of MemuFiles
/** Explorer / the desktop / a messenger accept one file through Chromium's DownloadURL: drag a single file out. */
export function exportDownloadUrl(e, paths, rows) {
  if (paths.length !== 1) return;
  const row = rows && rows.find((r) => r.path === paths[0]);
  if (!row || row.d) return;
  try { e.dataTransfer.setData("DownloadURL", `application/octet-stream:${baseName(paths[0]).replace(/:/g, "_")}:${location.origin}${rawUrl(paths[0])}`); } catch (err) { /* ignore */ }
}
