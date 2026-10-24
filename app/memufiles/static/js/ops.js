// MemuFiles · operations: the progress flyout (one card per operation), the status-bar indicator, the name-conflict
// dialog and completion notices (Round 40, r40-files-core).
//
// Cheap by design: the backend sends at most ~4 snapshots per second per operation (collapsed in the event queue);
// here every snapshot only marks its card dirty and one requestAnimationFrame patches the text / widths of the dirty
// cards (built once, never re-rendered as a whole list) plus the status-bar indicator. The speed graph is a canvas
// drawn only for expanded cards while the flyout is open. Nothing runs while no operation is active.
import { api, h, esc, icon, baseName, parentPath, samePath, fmtSize, fmtNum, fmtDate, itemsWord, toast, showError, modal, thumbUrl, IMAGE_EXT, extOf, fileIcon } from "./core.js";
import { t, addStrings } from "./i18n.js";

addStrings({
  "opx.title": "Operations",
  "opx.sub_active": "{n} running",
  "opx.sub_queued": "{n} queued",
  "opx.sub_none": "Nothing is running",
  "opx.clear": "Clear",
  "opx.clear_hint": "Remove finished operations from the list",
  "opx.close": "Close",
  "opx.empty": "Copying, moving, deleting and archiving show up here with their progress.",
  "opx.k.copy": "Copying {what}", "opx.k.move": "Moving {what}", "opx.k.trash": "Moving {what} to the Recycle Bin",
  "opx.k.delete": "Deleting {what}", "opx.k.rename": "Renaming {what}", "opx.k.extract": "Extracting {what}",
  "opx.k.compress": "Compressing {what}", "opx.k.undo": "Undoing", "opx.k.mkdir": "Creating {what}",
  "opx.k.upload": "Copying {what}", "opx.k.download": "Downloading {what}", "opx.k.vpaste": "Pasting files from the clipboard",
  "opx.k.organize": "Sorting {what} by meaning",
  "cmd.redo": "Redo", "ops.redoing": "Redoing…", "err.nothing_to_redo": "Nothing to redo",
  "op.upload": "Copying", "op.download": "Downloading", "op.vpaste": "Pasting", "op.organize": "Sorting by meaning",
  "opx.d.copy": "Copied {what}", "opx.d.move": "Moved {what}", "opx.d.trash": "Moved {what} to the Recycle Bin",
  "opx.d.delete": "Deleted {what}", "opx.d.rename": "Renamed {what}", "opx.d.extract": "Extracted {what}",
  "opx.d.compress": "Compressed {what}", "opx.d.undo": "Undone", "opx.d.mkdir": "Created {what}",
  "opx.d.upload": "Copied {what}", "opx.d.download": "Downloaded {what}", "opx.d.vpaste": "Pasted {what}",
  "opx.d.organize": "Sorted {what} into folders",
  "opx.from_to": "from {src} to {dst}", "opx.to": "to {dst}", "opx.from": "from {src}",
  "opx.dropped": "dropped files",
  "opx.left": "About {t} left", "opx.left_calc": "Calculating time left…",
  "opx.counting": "Counting: {n} · {size}",
  "opx.of_bytes": "{done} of {total}", "opx.of_items": "{done} of {total} items",
  "opx.speed": "Speed: {v}/s", "opx.items_speed": "{v} items/s",
  "opx.file": "File", "opx.skip_file": "Skip file", "opx.skipped": "skipped: {n}",
  "opx.pause": "Pause", "opx.resume": "Resume", "opx.cancel": "Cancel", "opx.remove": "Remove from list",
  "opx.more": "More details", "opx.less": "Fewer details",
  "opx.queued": "Waiting in queue", "opx.queued_n": "In queue · #{n}",
  "opx.paused": "Paused", "opx.cancelled": "Cancelled", "opx.cancelling": "Cancelling…",
  "opx.need_answer": "The destination already has «{name}»", "opx.resolve": "Choose…",
  "opx.errors": "{n} could not be processed", "opx.retry": "Try again", "opx.open": "Open folder",
  "opx.undo": "Undo", "opx.undo_cancel": "Remove what was copied",
  "opx.summary": "{size} in {time}", "opx.avg": "avg {v}/s",
  "opx.ind.one": "{what} · {pct}%", "opx.ind.many": "{n} operations · {pct}%", "opx.ind.paused": "Paused · {pct}%",
  "opx.ind.scan": "Preparing…", "opx.ind.done": "Done", "opx.ind.err": "Finished with errors",
  "opx.notify_done": "{what} — done", "opx.notify_err": "{what} — finished with errors",
  "opx.t.s": "{n} s", "opx.t.m": "{n} min", "opx.t.ms": "{m} min {s} s", "opx.t.h": "{h} h {m} min",
  "cfx.title": "Replace or skip?", "cfx.text": "«{dir}» already has a file named «{name}».",
  "cfx.text_dir": "«{dir}» already has an item named «{name}».",
  "cfx.replace": "Replace the file in the destination", "cfx.skip": "Skip this file",
  "cfx.both": "Keep both", "cfx.both_hint": "The new one becomes «{name}»",
  "cfx.rename": "Rename…", "cfx.rename_go": "Save as",
  "cfx.compare": "Compare files", "cfx.compare_hide": "Hide comparison",
  "cfx.incoming": "Incoming", "cfx.existing": "In the destination",
  "cfx.newer": "Newer", "cfx.older": "Older", "cfx.bigger": "Larger", "cfx.smaller": "Smaller",
  "cfx.same_size": "Same size", "cfx.same_time": "Same date", "cfx.likely_same": "They look identical (same size and date)",
  "cfx.diff": "Difference: {v}", "cfx.all": "Do this for all conflicts ({n})", "cfx.cancel_op": "Cancel operation",
  "cfx.keys": "R — replace · S — skip · B — keep both",
}, {
  "opx.title": "Операции",
  "opx.sub_active": "Выполняется: {n}",
  "opx.sub_queued": "в очереди: {n}",
  "opx.sub_none": "Сейчас ничего не выполняется",
  "opx.clear": "Очистить",
  "opx.clear_hint": "Убрать завершённые операции из списка",
  "opx.close": "Закрыть",
  "opx.empty": "Здесь появятся копирование, перенос, удаление и архивация — с прогрессом и скоростью.",
  "opx.k.copy": "Копирование: {what}", "opx.k.move": "Перемещение: {what}", "opx.k.trash": "В корзину: {what}",
  "opx.k.delete": "Удаление: {what}", "opx.k.rename": "Переименование: {what}", "opx.k.extract": "Распаковка: {what}",
  "opx.k.compress": "Сжатие: {what}", "opx.k.undo": "Отмена действия", "opx.k.mkdir": "Создание: {what}",
  "opx.k.upload": "Копирование: {what}", "opx.k.download": "Загрузка: {what}", "opx.k.vpaste": "Вставка файлов из буфера обмена",
  "opx.k.organize": "Раскладка по смыслу: {what}",
  "cmd.redo": "Повторить", "ops.redoing": "Повтор действия…", "err.nothing_to_redo": "Нечего повторять",
  "op.upload": "Копирование", "op.download": "Загрузка", "op.vpaste": "Вставка", "op.organize": "Раскладка по смыслу",
  "opx.d.copy": "Скопировано: {what}", "opx.d.move": "Перемещено: {what}", "opx.d.trash": "В корзине: {what}",
  "opx.d.delete": "Удалено: {what}", "opx.d.rename": "Переименовано: {what}", "opx.d.extract": "Распаковано: {what}",
  "opx.d.compress": "Сжато: {what}", "opx.d.undo": "Действие отменено", "opx.d.mkdir": "Создано: {what}",
  "opx.d.upload": "Скопировано: {what}", "opx.d.download": "Загружено: {what}", "opx.d.vpaste": "Вставлено: {what}",
  "opx.d.organize": "Разложено по папкам: {what}",
  "opx.from_to": "из {src} в {dst}", "opx.to": "в {dst}", "opx.from": "из {src}",
  "opx.dropped": "перетащенные файлы",
  "opx.left": "Осталось ≈ {t}", "opx.left_calc": "Оценка оставшегося времени…",
  "opx.counting": "Подсчёт: {n} · {size}",
  "opx.of_bytes": "{done} из {total}", "opx.of_items": "{done} из {total} элем.",
  "opx.speed": "Скорость: {v}/с", "opx.items_speed": "{v} элем./с",
  "opx.file": "Файл", "opx.skip_file": "Пропустить файл", "opx.skipped": "пропущено: {n}",
  "opx.pause": "Пауза", "opx.resume": "Продолжить", "opx.cancel": "Отменить", "opx.remove": "Убрать из списка",
  "opx.more": "Подробнее", "opx.less": "Свернуть",
  "opx.queued": "Ждёт в очереди", "opx.queued_n": "В очереди · №{n}",
  "opx.paused": "Пауза", "opx.cancelled": "Отменено", "opx.cancelling": "Отмена…",
  "opx.need_answer": "В папке назначения уже есть «{name}»", "opx.resolve": "Выбрать…",
  "opx.errors": "Не удалось обработать: {n}", "opx.retry": "Повторить", "opx.open": "Открыть папку",
  "opx.undo": "Отменить", "opx.undo_cancel": "Убрать скопированное",
  "opx.summary": "{size} за {time}", "opx.avg": "в среднем {v}/с",
  "opx.ind.one": "{what} · {pct}%", "opx.ind.many": "Операций: {n} · {pct}%", "opx.ind.paused": "Пауза · {pct}%",
  "opx.ind.scan": "Подготовка…", "opx.ind.done": "Готово", "opx.ind.err": "Завершено с ошибками",
  "opx.notify_done": "{what} — готово", "opx.notify_err": "{what} — с ошибками",
  "opx.t.s": "{n} с", "opx.t.m": "{n} мин", "opx.t.ms": "{m} мин {s} с", "opx.t.h": "{h} ч {m} мин",
  "cfx.title": "Заменить или пропустить?", "cfx.text": "В папке «{dir}» уже есть файл «{name}».",
  "cfx.text_dir": "В папке «{dir}» уже есть элемент «{name}».",
  "cfx.replace": "Заменить файл в папке назначения", "cfx.skip": "Пропустить этот файл",
  "cfx.both": "Оставить оба", "cfx.both_hint": "Новый сохранится как «{name}»",
  "cfx.rename": "Переименовать…", "cfx.rename_go": "Сохранить как",
  "cfx.compare": "Сравнить файлы", "cfx.compare_hide": "Скрыть сравнение",
  "cfx.incoming": "Копируемый", "cfx.existing": "В папке назначения",
  "cfx.newer": "Новее", "cfx.older": "Старее", "cfx.bigger": "Больше", "cfx.smaller": "Меньше",
  "cfx.same_size": "Тот же размер", "cfx.same_time": "Та же дата", "cfx.likely_same": "Похоже, это один и тот же файл (размер и дата совпадают)",
  "cfx.diff": "Разница: {v}", "cfx.all": "Сделать так для всех конфликтов ({n})", "cfx.cancel_op": "Отменить операцию",
  "cfx.keys": "R — заменить · S — пропустить · B — оставить оба",
});

const FINISHED = new Set(["done", "error", "cancelled"]);
const SKIPPABLE = new Set(["copy", "move", "extract", "upload", "download", "vpaste"]);
const SVG = {
  skip: '<path d="M6 5l8 7-8 7z"/><path d="M18 5v14"/>',
  retry: '<path d="M4 12a8 8 0 0 1 13.7-5.7L20 8.6"/><path d="M20 4v4.6h-4.6"/><path d="M20 12a8 8 0 0 1-13.7 5.7L4 15.4"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  dl: '<path d="M12 4v11M7 10l5 5 5-5"/><path d="M5 20h14"/>',
};
const svg = (name, cls = "") => `<span class="ic ${cls}" aria-hidden="true"><svg viewBox="0 0 24 24">${SVG[name]}</svg></span>`;
const KIND_ICON = { copy: "copy", move: "cut", trash: "trash", delete: "trash", rename: "rename", extract: "extract", compress: "zip", undo: "undo", mkdir: "folderPlus", upload: "copy", download: null, vpaste: "paste", organize: "sparkle" };   // r40-files-2: organize had the download arrow

export function fmtDuration(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return t("opx.t.s", { n: Math.max(1, sec) });
  if (sec < 600) { const m = Math.floor(sec / 60), s = sec % 60; return s ? t("opx.t.ms", { m, s }) : t("opx.t.m", { n: m }); }
  if (sec < 3600) return t("opx.t.m", { n: Math.round(sec / 60) });
  return t("opx.t.h", { h: Math.floor(sec / 3600), m: Math.round((sec % 3600) / 60) });
}
export function opPct(op) {
  if (op.status === "done") return 1;
  if (op.unit === "bytes" && op.total_bytes) return Math.min(1, op.done_bytes / op.total_bytes);
  if (op.total_items) return Math.min(1, op.done_items / op.total_items);
  return 0;
}
function isIndet(op) {
  return op.status === "scanning" || (op.status === "running" && op.unit === "items" && (op.total_items || 0) <= 1);
}
function whatOf(op) {
  const n = op.kind === "upload" || op.kind === "download" ? (op.count || 1) : (op.count || op.total_items || 0);
  if (op.kind === "download") return baseName(op.current || (op.results || [])[0] || "") || "…";
  if (op.kind === "vpaste") return itemsWord(op.done_items || op.total_items || 0);
  if (op.count === 1 && (op.sources[0] || op.results[0])) return "«" + baseName(op.sources[0] || op.results[0]) + "»";
  if (op.kind === "undo") return "";
  return itemsWord(n);
}
function placeName(path) { return baseName(path) || path; }

export class Ops {
  constructor(app) {
    this.app = app;
    this.cards = new Map();          // op id → { el, refs, key }
    this.dirty = new Set();
    this.raf = 0;
    this.fly = null;
    this.expanded = new Set();
    this.collapsed = new Set();
    this.autoShown = new Set();
    this.flash = null;                // { text, kind, until } — "Done" in the status bar for a few seconds
    this.flashTimer = 0;
    this.openConflicts = new Map();
  }

  // ------------------------------------------------------------------ events
  onOp(op, before) {
    if (op.status === "conflict" && op.conflict && (!before || before.status !== "conflict" || JSON.stringify(before.conflict) !== JSON.stringify(op.conflict))) this.conflict(op);
    if (op.status !== "conflict" && this.openConflicts.has(op.id)) { this.openConflicts.get(op.id).close("gone"); this.openConflicts.delete(op.id); }
    if (before && !FINISHED.has(before.status) && FINISHED.has(op.status)) this.finished(op);
    // a long operation opens the flyout once by itself (like Explorer's progress window); closed = stays closed
    if (!this.fly && !FINISHED.has(op.status) && op.status !== "queued" && !this.autoShown.has(op.id) && (op.elapsed || 0) >= 1.5
        && (op.unit === "bytes" ? op.total_bytes >= 64 * 1048576 : op.total_items >= 400) && !document.hidden) {
      this.autoShown.add(op.id);
      this.toggle(true);
    }
    this.dirty.add(op.id);
    this.schedule();
  }
  schedule() { if (!this.raf) this.raf = requestAnimationFrame(() => { this.raf = 0; this.flush(); }); }
  flush() {
    this.renderIndicator();
    if (!this.fly) { this.dirty.clear(); return; }
    const order = this.ordered().map((o) => o.id);
    const key = order.join(",");
    if (key !== this.orderKey) { this.orderKey = key; this.renderList(); this.dirty.clear(); return; }
    for (const id of this.dirty) { const op = this.app.ops.get(id); if (op) this.patch(op); }
    this.dirty.clear();
    this.renderHead();
  }
  ordered() {
    const all = [...this.app.ops.values()];
    const rank = (o) => (o.status === "conflict" ? 0 : ["running", "scanning", "paused"].includes(o.status) ? 1 : o.status === "queued" ? 2 : 3);
    return all.sort((a, b) => rank(a) - rank(b) || (rank(a) === 3 ? (b.finished || b.created) - (a.finished || a.created) : a.created - b.created)).slice(0, 40);
  }

  finished(op) {
    const app = this.app;
    const pane = app.pane();
    if (op.status === "done" && op.results && op.results.length && pane && pane.folder && ["copy", "move", "compress", "extract", "upload", "download", "vpaste"].includes(op.kind)) {
      const inHere = op.results.filter((p) => parentPath(p) && samePath(parentPath(p), pane.folder));
      if (inHere.length) pane.pendingSelect = inHere;
    }
    // r40-files-2: moved / deleted items leave the selection at once — the preview and the status bar must not
    // keep pointing at a file that is gone until the folder re-reads (an image preview 404-ed in between)
    if (["move", "trash", "delete", "organize", "rename"].includes(op.kind) && op.sources && op.sources.length) {
      const gone = new Set(op.sources);
      for (const tab of app.tabs) for (const p of tab.panes) {
        if (![...p.sel].some((x) => gone.has(x))) continue;
        p.sel = new Set([...p.sel].filter((x) => !gone.has(x)));
        p.dirty = true;
        if (p.el) { p.renderVisible(true); app.selectionChanged(p); }
      }
    }
    const title = t("opx.d." + op.kind, { what: whatOf(op) });
    if (op.status === "error" || (op.status === "done" && op.error_count)) {
      const first = op.errors && op.errors[0];
      const why = first ? (t("err." + first.error) !== "err." + first.error ? t("err." + first.error) : first.error) : "";
      toast((op.status === "error" && !op.done_items ? t("ops.failed", { what: t("op." + op.kind) }) : t("opx.errors", { n: op.error_count })) + (why ? ": " + why : ""), "err",
        op.retryable ? { label: t("opx.retry"), run: () => this.retry(op) } : { label: t("opx.more"), run: () => this.toggle(true) });
      this.setFlash(t("opx.ind.err"), "err");
    } else if (op.status === "done") {
      if (op.undoable && ["trash", "move", "copy", "rename", "upload", "download", "extract", "compress", "vpaste", "organize"].includes(op.kind)) toast(title, "ok", { label: t("cmd.undo"), run: () => this.undo(op) });
      else if (op.kind !== "undo" && op.kind !== "mkdir" && op.kind !== "rename") toast(title, "ok");
      this.setFlash(t("opx.ind.done"), "ok");
    }
    // the window is in the background: a system notification (only if the user allowed them; never asks)
    if ((document.hidden || !document.hasFocus()) && (op.elapsed || 0) >= 3 && op.status !== "cancelled" && window.Notification && Notification.permission === "granted") {
      try { new Notification("MemuFiles", { body: t(op.status === "done" && !op.error_count ? "opx.notify_done" : "opx.notify_err", { what: title }), silent: false }); } catch (e) { /* ignore */ }
    }
  }
  setFlash(text, kind) {
    this.flash = { text, kind };
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => { this.flash = null; this.renderIndicator(); }, 4000);
  }

  // ------------------------------------------------------------------ actions
  control(op, action) { return api("ops/control", { action, id: op.id }).catch(showError); }
  undo(op) { return api("ops/undo", { id: op.id }).then((r) => this.app.trackOp(r.op)).catch(showError); }
  retry(op) { return api("ops/control", { action: "retry", id: op.id }).then((r) => r.op && this.app.trackOp(r.op)).catch(showError); }
  remove(op) {
    this.app.ops.delete(op.id);
    this.dirty.add(op.id);
    this.schedule();
  }
  async clearFinished() {
    try { await api("ops/control", { action: "clear" }); } catch (e) { /* the list is local too */ }
    for (const [id, op] of this.app.ops) if (FINISHED.has(op.status)) this.app.ops.delete(id);
    this.schedule();
  }

  // ------------------------------------------------------------------ status-bar indicator
  renderIndicator() {
    const btn = this.app.opsBtn;
    if (!btn) return;
    const active = [...this.app.ops.values()].filter((o) => !FINISHED.has(o.status));
    let cls = "ops-ind", html;
    if (!active.length) {
      if (this.flash) { cls += " flash " + this.flash.kind; html = icon(this.flash.kind === "err" ? "warn" : "check", "sm") + `<span>${esc(this.flash.text)}</span>`; }
      else { cls += " idle"; html = icon("clock", "sm") + `<span>${esc(t("ops.idle"))}</span>`; }
    } else {
      const fr = active.map(opPct);
      const pct = fr.reduce((a, b) => a + b, 0) / fr.length;
      const conflict = active.some((o) => o.status === "conflict");
      const paused = active.every((o) => o.status === "paused");
      const scanning = active.every((o) => isIndet(o) || o.status === "queued");
      const c = 2 * Math.PI * 5.5;
      cls += conflict ? " warn" : paused ? " paused" : " busy";
      const p = Math.round(pct * 100);
      let text;
      if (conflict) text = t("ops.need_answer");
      else if (paused) text = t("opx.ind.paused", { pct: p });
      else if (scanning) text = active.length === 1 && active[0].status === "running" ? t("op." + active[0].kind) + "…" : t("opx.ind.scan");
      else if (active.length === 1) {
        const o = active[0];
        text = t("opx.ind.one", { what: t("op." + o.kind), pct: p }) + (o.eta ? " · " + fmtDuration(o.eta) : "");
      } else text = t("opx.ind.many", { n: active.length, pct: p });
      html = `<svg class="ring${scanning ? " spin" : ""}" viewBox="0 0 14 14"><circle class="bg" cx="7" cy="7" r="5.5"/><circle class="fg" cx="7" cy="7" r="5.5" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${(scanning ? c * 0.72 : c * (1 - pct)).toFixed(2)}"/></svg><span class="oi-long">${esc(text)}</span><span class="oi-short">${esc(conflict ? "!" : p + "%")}</span><i class="ops-ind-bar" style="transform:scaleX(${pct.toFixed(3)})"></i>`;
    }
    if (btn.className !== cls) btn.className = cls;
    if (btn._html !== html) { btn._html = html; btn.innerHTML = html; }
    const long = btn.querySelector(".oi-long");    // r40-files-2: a narrow status bar shows only «34 %»
    btn.setAttribute("aria-label", long ? long.textContent : btn.textContent);
    btn.title = long ? long.textContent : t("ops.title");
  }

  // ------------------------------------------------------------------ flyout
  toggle(force) {
    const open = force === undefined ? !this.fly : force;
    if (!open) {
      if (this.fly) { this.fly.remove(); this.fly = null; document.removeEventListener("mousedown", this.away, true); document.removeEventListener("keydown", this.onKey, true); }
      for (const op of this.app.ops.values()) if (!FINISHED.has(op.status)) this.autoShown.add(op.id);
      return;
    }
    if (this.fly) return;
    this.fly = h("section", { class: "ops-fly", role: "dialog", "aria-label": t("opx.title") });
    this.headEl = h("div", { class: "of-head" });
    this.listEl = h("div", { class: "of-list" });
    this.fly.append(this.headEl, this.listEl);
    document.body.append(this.fly);
    this.orderKey = null;
    this.flush();
    this.away = (e) => { if (this.fly && !this.fly.contains(e.target) && !e.target.closest(".ops-ind") && !e.target.closest(".modal-back") && !e.target.closest(".toasts")) this.toggle(false); };
    this.onKey = (e) => { if (e.key === "Escape" && this.fly && !document.querySelector(".modal-back")) { e.preventDefault(); e.stopPropagation(); this.toggle(false); } };
    setTimeout(() => { document.addEventListener("mousedown", this.away, true); document.addEventListener("keydown", this.onKey, true); }, 0);
  }
  renderHead() {
    if (!this.fly) return;
    const ops = [...this.app.ops.values()];
    const active = ops.filter((o) => ["running", "scanning", "paused", "conflict"].includes(o.status)).length;
    const queued = ops.filter((o) => o.status === "queued").length;
    const sub = active || queued ? [active ? t("opx.sub_active", { n: active }) : "", queued ? t("opx.sub_queued", { n: queued }) : ""].filter(Boolean).join(" · ") : t("opx.sub_none");
    const hasDone = ops.some((o) => FINISHED.has(o.status));
    const key = sub + "|" + hasDone;
    if (this.headEl._key === key) return;
    this.headEl._key = key;
    this.headEl.innerHTML = `<div class="of-titles"><b>${esc(t("opx.title"))}</b><span>${esc(sub)}</span></div>`;
    if (hasDone) {
      const clear = h("button", { class: "btn sm ghost", text: t("opx.clear"), title: t("opx.clear_hint") });
      clear.addEventListener("click", () => this.clearFinished());
      this.headEl.append(clear);
    }
    const close = h("button", { class: "icon-btn sm", title: t("opx.close"), "aria-label": t("opx.close"), html: icon("x") });
    close.addEventListener("click", () => this.toggle(false));
    this.headEl.append(close);
  }
  renderList() {
    this.renderHead();
    const ops = this.ordered();
    const keep = new Set(ops.map((o) => o.id));
    for (const id of [...this.cards.keys()]) if (!keep.has(id)) this.cards.delete(id);
    const frag = document.createDocumentFragment();
    if (!ops.length) frag.append(h("div", { class: "of-empty", html: icon("clock") + `<span>${esc(t("opx.empty"))}</span>` }));
    let queuePos = 0;
    let firstActive = true;
    for (const op of ops) {
      if (op.status === "queued") op._pos = ++queuePos;
      if (!FINISHED.has(op.status) && op.status !== "queued" && firstActive) {
        firstActive = false;
        if (!this.collapsed.has(op.id)) this.expanded.add(op.id);    // the first running card shows its graph
      }
      let card = this.cards.get(op.id);
      if (!card) { card = this.build(op); this.cards.set(op.id, card); }
      this.patch(op, card);
      frag.append(card.el);
    }
    this.listEl.replaceChildren(frag);
  }

  build(op) {
    const el = h("div", { class: "opc", "data-op": op.id });
    el.innerHTML = `<div class="opc-head"><span class="opc-ico"></span><div class="opc-titles"><div class="opc-title"></div><div class="opc-route"></div></div><div class="opc-btns"></div></div>
      <div class="opc-body"><div class="opc-line"><b class="opc-pct"></b><span class="opc-left"></span><span class="opc-speed"></span></div>
      <div class="opc-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div>
      <div class="opc-graph" hidden><canvas></canvas></div>
      <div class="opc-meta"></div><div class="opc-file" hidden><span class="opc-file-name"></span><span class="opc-file-bar"><i></i></span></div>
      <div class="opc-conf" hidden></div><div class="opc-errs" hidden></div></div><div class="opc-sum" hidden></div>`;
    const refs = {};
    for (const k of ["ico", "title", "route", "btns", "body", "pct", "left", "speed", "bar", "graph", "meta", "file", "conf", "errs", "sum"]) refs[k] = el.querySelector(".opc-" + k);
    refs.barI = refs.bar.firstElementChild;
    refs.canvas = refs.graph.querySelector("canvas");
    refs.fileName = refs.file.querySelector(".opc-file-name");
    refs.fileBar = refs.file.querySelector(".opc-file-bar i");
    // event delegation: one listener per card
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const cur = this.app.ops.get(op.id) || op;
      const act = b.dataset.act;
      if (act === "pause") this.control(cur, cur.status === "paused" ? "resume" : "pause");
      else if (act === "cancel") this.control(cur, "cancel");
      else if (act === "skip") this.control(cur, "skip");
      else if (act === "undo") this.undo(cur);
      else if (act === "retry") this.retry(cur);
      else if (act === "remove") this.remove(cur);
      else if (act === "open") this.app.revealPath(b.dataset.path || cur.dest);
      else if (act === "goto") this.app.pane().navigate(b.dataset.path);
      else if (act === "resolve") this.conflict(cur, true);
      else if (act === "quick") api("ops/control", { action: "resolve", id: cur.id, choice: b.dataset.choice, all: false }).catch(showError);
      else if (act === "toggle") { if (this.expanded.has(op.id)) { this.expanded.delete(op.id); this.collapsed.add(op.id); } else { this.expanded.add(op.id); this.collapsed.delete(op.id); } this.patch(cur); }
      else if (act === "errs") { b.closest(".opc-errs").classList.toggle("open"); }
    });
    return { el, refs, key: {} };
  }
  set(card, name, value, fn) {
    if (card.key[name] === value) return false;
    card.key[name] = value;
    fn(value);
    return true;
  }
  patch(op, card = this.cards.get(op.id)) {
    if (!card) return;
    const r = card.refs;
    const fin = FINISHED.has(op.status);
    const pct = opPct(op);
    const expanded = this.expanded.has(op.id) && !fin;
    this.set(card, "cls", `opc ${op.status} k-${op.kind}${expanded ? " expanded" : ""}${fin ? " fin" : ""}`, (v) => { card.el.className = v; });
    this.set(card, "ico", op.status + op.kind, () => {
      const name = op.status === "error" ? "warn" : op.status === "done" ? "check" : op.status === "cancelled" ? "x" : op.status === "conflict" ? "warn" : KIND_ICON[op.kind];
      r.ico.innerHTML = name ? icon(name) : svg("dl");
    });
    const what = whatOf(op);
    this.set(card, "title", (fin && op.status === "done" ? "d" : "k") + op.kind + what, () => {
      const text = t((fin && op.status === "done" ? "opx.d." : "opx.k.") + op.kind, { what }).replace(/:\s*$/, "");
      r.title.textContent = text; r.title.title = text;
    });
    this.set(card, "route", op.src_dir + "|" + op.dest + "|" + op.kind, () => {
      const src = op.kind === "upload" ? "" : op.kind === "download" ? (op.sources[0] || "") : op.src_dir;
      const dst = ["copy", "move", "extract", "upload", "download", "compress", "vpaste"].includes(op.kind) ? (op.kind === "compress" ? parentPath(op.dest) : op.dest) : "";
      const link = (p, label) => p && !/^https?:/i.test(p) ? `<a href="#" data-act="goto" data-path="${esc(p)}" title="${esc(p)}">${esc(label || placeName(p))}</a>` : `<span title="${esc(p)}">${esc(label || p)}</span>`;
      let html = "";
      const srcHtml = op.kind === "download" ? link(src, new URL(src, location.href).hostname) : link(src);
      if (src && dst) html = esc(t("opx.from_to", { src: "\u0001", dst: "\u0002" })).replace("\u0001", srcHtml).replace("\u0002", link(dst));
      else if (dst) html = esc(t("opx.to", { dst: "\u0002" })).replace("\u0002", link(dst));
      else if (src) html = esc(t("opx.from", { src: "\u0001" })).replace("\u0001", link(src));
      r.route.innerHTML = html;
      r.route.hidden = !html;
    });
    // buttons
    const btnKey = `${op.status}|${op.undoable}|${op.retryable}|${expanded}|${op.kind}`;
    this.set(card, "btns", btnKey, () => {
      const b = (act, ico, label, cls = "icon-btn sm") => `<button class="${cls}" data-act="${act}" title="${esc(label)}" aria-label="${esc(label)}">${ico}${cls.includes("icon-btn") ? "" : `<span>${esc(label)}</span>`}</button>`;
      let html = "";
      if (!fin) {
        if (op.status !== "conflict" && op.status !== "queued" && op.kind !== "undo") html += b("pause", icon(op.status === "paused" ? "play" : "pause"), t(op.status === "paused" ? "opx.resume" : "opx.pause"));
        html += b("cancel", icon("x"), t("opx.cancel"));
      } else {
        if (op.retryable) html += b("retry", svg("retry"), t("opx.retry"), "btn sm");
        if (op.undoable) html += b("undo", icon("undo"), op.status === "cancelled" && op.kind === "copy" ? t("opx.undo_cancel") : t("opx.undo"), "btn sm ghost");
        html += b("remove", icon("x"), t("opx.remove"));
      }
      r.btns.innerHTML = html;
    });
    // finished: one summary line instead of the body
    r.body.hidden = fin;
    r.sum.hidden = !fin;
    if (fin) {
      const size = op.unit === "bytes" ? (op.status === "done" ? op.total_bytes : op.done_bytes) : 0;
      const parts = [];
      if (op.status === "cancelled") parts.push(t("opx.cancelled"));
      if (size) parts.push(t("opx.summary", { size: fmtSize(size), time: fmtDuration(op.elapsed || 0) }));
      else if (op.elapsed >= 1) parts.push(fmtDuration(op.elapsed));
      if (size && op.elapsed >= 2) parts.push(t("opx.avg", { v: fmtSize(size / op.elapsed) }));
      if (op.skipped) parts.push(t("opx.skipped", { n: fmtNum(op.skipped) }));
      const open = op.status !== "cancelled" && op.results && op.results[0] && ["copy", "move", "extract", "compress", "upload", "download", "vpaste"].includes(op.kind)
        ? `<a href="#" class="opc-open" data-act="open" data-path="${esc(op.results[0])}">${esc(t("opx.open"))}</a>` : "";
      this.set(card, "sum", parts.join(" · ") + open + op.error_count, () => { r.sum.innerHTML = `<span>${esc(parts.join(" · "))}</span>${open}`; });
      this.patchErrors(op, card, r.sum);
      return;
    }
    // progress
    const p100 = Math.round(pct * 100);
    // one indivisible item (a folder to the Recycle Bin, a 7-Zip archive): no fake 0 %, a moving bar instead
    const indet = isIndet(op);
    this.set(card, "pct", op.status === "queued" || indet ? "" : p100 + "%", (v) => { r.pct.textContent = v; r.pct.hidden = !v; });
    r.bar.setAttribute("aria-valuenow", String(p100));
    this.set(card, "bar", indet ? -1 : pct.toFixed(3), (v) => { r.bar.classList.toggle("indet", v === -1); r.barI.style.transform = v === -1 ? "" : `scaleX(${v})`; });
    let left;
    if (op.status === "queued") left = op._pos ? t("opx.queued_n", { n: op._pos }) : t("opx.queued");
    else if (op.status === "scanning") left = t("opx.counting", { n: itemsWord(op.total_items || 0), size: fmtSize(op.total_bytes || 0) });
    else if (op.status === "paused") left = t("opx.paused");
    else if (op.status === "conflict") left = "";
    else left = op.eta ? t("opx.left", { t: fmtDuration(op.eta) }) : indet ? "" : t("opx.left_calc");
    this.set(card, "left", left, (v) => { r.left.textContent = v; });
    const speed = op.status === "running" ? (op.unit === "bytes" ? (op.speed ? t("opx.speed", { v: fmtSize(op.speed) }) : "")
      : op.elapsed >= 1 && op.done_items ? t("opx.items_speed", { v: fmtNum(Math.round(op.done_items / op.elapsed)) }) : "") : "";
    this.set(card, "speed", speed, (v) => { r.speed.textContent = v; });
    const meta = [];
    if (op.status !== "queued" && op.status !== "scanning") {
      if (op.unit === "bytes") meta.push(t("opx.of_bytes", { done: fmtSize(op.done_bytes), total: fmtSize(op.total_bytes) }));
      if (op.total_items > 1 || op.unit !== "bytes") meta.push(t("opx.of_items", { done: fmtNum(op.done_items), total: fmtNum(op.total_items) }));
      if (op.skipped) meta.push(t("opx.skipped", { n: fmtNum(op.skipped) }));
    }
    this.set(card, "meta", meta.join(" · "), (v) => { r.meta.textContent = v; r.meta.hidden = !v; });
    // current file (+ its own bar for big files, + "skip file")
    const showFile = op.current && ["running", "paused"].includes(op.status) && op.kind !== "undo";
    r.file.hidden = !showFile;
    if (showFile) {
      this.set(card, "file", op.current + "|" + SKIPPABLE.has(op.kind), () => {
        r.fileName.innerHTML = `<span class="lbl">${esc(t("opx.file"))}</span>${fileIcon(baseName(op.current), false)}<span class="nm" title="${esc(op.current)}">${esc(baseName(op.current) || op.current)}</span>`;
        let skip = r.file.querySelector("[data-act=skip]");
        if (SKIPPABLE.has(op.kind) && !skip) { skip = h("button", { class: "btn xs ghost", "data-act": "skip", html: svg("skip") + `<span>${esc(t("opx.skip_file"))}</span>` }); r.file.append(skip); }
        else if (!SKIPPABLE.has(op.kind) && skip) skip.remove();
      });
      const big = op.cur_size >= 32 * 1048576;
      this.set(card, "fileBar", big ? (op.cur_done / op.cur_size).toFixed(3) : "-", (v) => { r.fileBar.parentElement.hidden = v === "-"; if (v !== "-") r.fileBar.style.transform = `scaleX(${v})`; });
    }
    // conflict inline
    r.conf.hidden = op.status !== "conflict" || !op.conflict;
    if (op.status === "conflict" && op.conflict) {
      this.set(card, "conf", JSON.stringify(op.conflict.dst), () => {
        r.conf.innerHTML = `<div class="opc-conf-text">${icon("warn")}<span>${esc(t("opx.need_answer", { name: op.conflict.dst.name || "" }))}</span></div>
          <div class="opc-conf-btns"><button class="btn xs" data-act="quick" data-choice="replace">${esc(t("cf.replace"))}</button><button class="btn xs" data-act="quick" data-choice="skip">${esc(t("cf.skip"))}</button><button class="btn xs" data-act="quick" data-choice="keep_both">${esc(t("cf.keep_both"))}</button><button class="btn xs primary" data-act="resolve">${esc(t("opx.resolve"))}</button></div>`;
      });
    }
    this.patchErrors(op, card, r.errs);
    // speed graph — only for expanded byte operations
    const graph = expanded && op.unit === "bytes" && op.status !== "queued" && op.status !== "scanning";
    r.graph.hidden = !graph;
    if (graph) this.drawGraph(op, card);
    this.set(card, "more", expanded + "|" + (op.unit === "bytes"), () => {
      let more = card.el.querySelector(".opc-more");
      if (op.unit !== "bytes") { if (more) more.remove(); return; }
      if (!more) { more = h("button", { class: "opc-more", "data-act": "toggle" }); card.el.append(more); }
      more.innerHTML = `<span>${esc(t(expanded ? "opx.less" : "opx.more"))}</span>${svg("chev")}`;
    });
  }
  patchErrors(op, card, host) {
    const errs = op.errors || [];
    const target = host === card.refs.errs ? card.refs.errs : (card.refs.errsFin ||= (() => { const d = h("div", { class: "opc-errs" }); card.el.append(d); return d; })());
    target.hidden = !op.error_count;
    if (!op.error_count) return;
    this.set(card, "errs" + (host === card.refs.errs ? "" : "F"), op.error_count + "|" + op.retryable + "|" + FINISHED.has(op.status), () => {
      const rows = errs.slice(-20).map((e) => {
        const why = t("err." + e.error) !== "err." + e.error ? t("err." + e.error) : e.error;
        return `<li><span class="nm" title="${esc(e.path)}">${esc(baseName(e.path) || e.path)}</span><span class="why">${esc(why)}</span></li>`;
      }).join("");
      const open = target.classList.contains("open");
      target.innerHTML = `<button class="opc-errs-head" data-act="errs">${icon("warn")}<span>${esc(t("opx.errors", { n: fmtNum(op.error_count) }))}</span>${svg("chev")}</button><ul>${rows}</ul>`;
      target.classList.toggle("open", open);
    });
  }
  drawGraph(op, card) {
    const hist = op.speed_hist || [];
    const canvas = card.refs.canvas;
    const w = canvas.clientWidth || 360, hgt = canvas.clientHeight || 54;
    const dpr = window.devicePixelRatio || 1;
    const key = hist.length + ":" + String(hist[hist.length - 1]) + ":" + w + ":" + document.documentElement.dataset.theme + op.status;
    if (card.key.graph === key) return;
    card.key.graph = key;
    if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(hgt * dpr); }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, hgt);
    const cs = getComputedStyle(card.el);
    const accent = op.status === "paused" ? cs.getPropertyValue("--warn").trim() : cs.getPropertyValue("--accent").trim() || "#4c9aff";
    const grid = cs.getPropertyValue("--border").trim() || "#444";
    ctx.strokeStyle = grid; ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
    for (let i = 1; i < 3; i++) { const y = Math.round((hgt * i) / 3) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
    ctx.globalAlpha = 1;
    // x = progress of the whole operation (‰), y = speed — the area grows left to right like Explorer's graph
    const pts = hist.filter((p) => Array.isArray(p));
    const max = Math.max(1, ...pts.map((p) => p[1]), op.speed || 0) * 1.15;
    const X = (p) => (p / 1000) * w, Y = (v) => hgt - (v / max) * (hgt - 6);
    if (pts.length) {
      const first = pts[0];
      ctx.beginPath();
      ctx.moveTo(0, hgt);
      ctx.lineTo(0, Y(first[1]));
      for (const p of pts) ctx.lineTo(X(p[0]), Y(p[1]));
      const lastX = X(pts[pts.length - 1][0]);
      ctx.lineTo(lastX, hgt);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, 0, 0, hgt);
      grad.addColorStop(0, colorAlpha(accent, 0.45)); grad.addColorStop(1, colorAlpha(accent, 0.08));
      ctx.fillStyle = grad; ctx.fill();
      ctx.beginPath();
      ctx.moveTo(0, Y(first[1]));
      for (const p of pts) ctx.lineTo(X(p[0]), Y(p[1]));
      ctx.strokeStyle = accent; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.stroke();
      // the current speed as a dashed line across the graph
      if (op.speed) {
        const y = Math.round(Math.max(6, Y(op.speed))) + 0.5;
        ctx.setLineDash([3, 3]); ctx.strokeStyle = colorAlpha(accent, 0.75); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); ctx.setLineDash([]);
      }
    }
  }

  // ------------------------------------------------------------------ conflict dialog
  conflict(op, fromUser) {
    if (this.openConflicts.has(op.id)) return;
    const c = op.conflict;
    if (!c) return;
    const src = c.src || {}, dst = c.dst || {};
    const left = Math.max(0, c.left ?? ((op.total_items || 0) - (op.done_items || 0)));
    // FAT/exFAT and zip keep 2-second times: a smaller difference is "the same date"
    const newerSrc = (src.mtime || 0) - (dst.mtime || 0) > 2000, newerDst = (dst.mtime || 0) - (src.mtime || 0) > 2000;
    const body = h("div", { class: "cfx" });
    const dirName = placeName(parentPath(dst.path || "")) || dst.path || "";
    body.innerHTML = `<p class="cfx-text">${esc(t(dst.is_dir ? "cfx.text_dir" : "cfx.text", { dir: dirName, name: dst.name || "" }))}</p>`;
    const preview = (info, real) => {
      const ext = extOf(info.name || "");
      if (real && info.path && IMAGE_EXT.has(ext) && !info.is_dir) return `<img class="cfx-thumb" alt="" src="${thumbUrl(info.path, 96, info.mtime)}" onerror="this.replaceWith(document.createElement('span'))">`;
      return `<span class="cfx-icon">${fileIcon(info.name || "", !!info.is_dir)}</span>`;
    };
    const badge = (on, text, cls = "") => (on ? `<span class="cfx-badge ${cls}">${esc(text)}</span>` : "");
    const card = (choice, title, info, real, newer, older, bigger, smaller) => {
      const b = h("button", { class: "cfx-card", "data-choice": choice });
      b.innerHTML = `<div class="cfx-card-title">${esc(title)}</div><div class="cfx-card-main">${preview(info, real)}<div class="cfx-card-info">
        <div class="nm" title="${esc(info.path || info.name || "")}">${esc(info.name || "")}</div>
        <div class="cfx-row"><span>${esc(info.size != null ? fmtSize(info.size) : "—")}</span>${badge(bigger, t("cfx.bigger"))}${badge(smaller, t("cfx.smaller"), "dim")}</div>
        <div class="cfx-row"><span>${esc(info.mtime ? fmtDate(info.mtime) : "—")}</span>${badge(newer, t("cfx.newer"), "ok")}${badge(older, t("cfx.older"), "dim")}</div>
        </div></div>`;
      return b;
    };
    const sizeKnown = src.size != null && dst.size != null;
    const cards = h("div", { class: "cfx-cards" });
    cards.append(
      card("replace", t("cfx.replace"), { ...src, path: op.kind === "upload" || op.kind === "download" ? "" : src.path }, op.kind !== "upload" && op.kind !== "download", newerSrc, newerDst, sizeKnown && src.size > dst.size, sizeKnown && src.size < dst.size),
      card("skip", t("cfx.skip"), dst, true, newerDst, newerSrc, sizeKnown && dst.size > src.size, sizeKnown && dst.size < src.size),
    );
    body.append(cards);
    // compare: exact numbers side by side
    const cmp = h("div", { class: "cfx-compare", hidden: true });
    const same = sizeKnown && src.size === dst.size && Math.abs((src.mtime || 0) - (dst.mtime || 0)) < 2000;
    cmp.innerHTML = `<table><tr><th></th><th>${esc(t("cfx.incoming"))}</th><th>${esc(t("cfx.existing"))}</th></tr>
      <tr><td>${esc(t("props.size"))}</td><td>${esc(src.size != null ? fmtSize(src.size, true) : "—")}</td><td>${esc(dst.size != null ? fmtSize(dst.size, true) : "—")}</td></tr>
      <tr><td>${esc(t("props.modified"))}</td><td>${esc(src.mtime ? fmtDate(src.mtime, true) : "—")}</td><td>${esc(dst.mtime ? fmtDate(dst.mtime, true) : "—")}</td></tr>
      <tr><td>${esc(t("props.location"))}</td><td class="path">${esc(parentPath(src.path || "") || src.path || "—")}</td><td class="path">${esc(parentPath(dst.path || "") || "—")}</td></tr></table>
      <div class="cfx-verdict">${esc(same ? t("cfx.likely_same") : sizeKnown && src.size !== dst.size ? t("cfx.diff", { v: fmtSize(Math.abs(src.size - dst.size)) }) : "")}</div>`;
    const actions = h("div", { class: "cfx-actions" });
    const both = h("button", { class: "btn", "data-choice": "keep_both", title: t("cfx.both_hint", { name: c.suggested || "" }), html: icon("plus") + `<span>${esc(t("cfx.both"))}</span><small>${esc(t("cfx.both_hint", { name: c.suggested || "" }))}</small>` });
    const renameBtn = h("button", { class: "btn ghost", html: icon("rename") + `<span>${esc(t("cfx.rename"))}</span>` });
    const cmpBtn = h("button", { class: "btn ghost", html: icon("split") + `<span>${esc(t("cfx.compare"))}</span>` });
    actions.append(both, renameBtn, cmpBtn);
    const renameRow = h("div", { class: "cfx-rename", hidden: true });
    const nameInput = h("input", { class: "input", value: c.suggested || dst.name || "", spellcheck: "false" });
    const renameGo = h("button", { class: "btn primary", text: t("cfx.rename_go") });
    renameRow.append(nameInput, renameGo);
    body.append(actions, renameRow, cmp, h("div", { class: "cfx-keys muted small", text: t("cfx.keys") }));
    const all = h("input", { type: "checkbox" });
    const leftEl = left > 1 ? h("label", { class: "check" }, all, t("cfx.all", { n: fmtNum(left) })) : null;
    let dlg = null;
    const answer = async (choice, name) => {
      this.openConflicts.delete(op.id);
      if (dlg) dlg.close("answered");
      try { await api("ops/control", { action: "resolve", id: op.id, choice, all: all.checked, name: name || "" }); } catch (err) { showError(err); }
    };
    body.addEventListener("click", (e) => { const b = e.target.closest("[data-choice]"); if (b) answer(b.dataset.choice); });
    renameBtn.addEventListener("click", () => { renameRow.hidden = !renameRow.hidden; if (!renameRow.hidden) { nameInput.focus(); const dot = nameInput.value.lastIndexOf("."); nameInput.setSelectionRange(0, dot > 0 ? dot : nameInput.value.length); } });
    renameGo.addEventListener("click", () => { const v = nameInput.value.trim(); if (v) answer("rename", v); });
    nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); renameGo.click(); } });
    cmpBtn.addEventListener("click", () => { cmp.hidden = !cmp.hidden; cmpBtn.querySelector("span:last-child").textContent = t(cmp.hidden ? "cfx.compare" : "cfx.compare_hide"); });
    // R / S / B anywhere in the dialog (also after ticking «for all») — not while typing a new name
    const keys = (e) => {
      if (e.target.closest("input:not([type=checkbox])") || e.ctrlKey || e.altKey || e.metaKey) return false;
      const choice = { KeyR: "replace", KeyS: "skip", KeyB: "keep_both" }[e.code];
      if (choice) answer(choice);
      return !!choice;
    };
    dlg = modal({ title: t("cfx.title"), icon: "warn", body, wide: true, left: leftEl, keys,
      buttons: [{ label: t("cfx.cancel_op"), cls: "ghost danger", run: () => { answer("cancel"); return false; } }],
      onClose: (v) => {
        // closed with Esc / ×: the operation keeps waiting; the card in the flyout (and the status bar) still asks
        this.openConflicts.delete(op.id);
        if (v === null && !fromUser) this.toggle(true);
      } });
    dlg.el.classList.add("cfx-modal");
    setTimeout(() => cards.firstElementChild && cards.firstElementChild.focus(), 30);
    this.openConflicts.set(op.id, dlg);
  }
}

function colorAlpha(color, a) {
  color = color.trim();
  if (color.startsWith("#")) {
    let hex = color.slice(1);
    if (hex.length === 3) hex = hex.split("").map((x) => x + x).join("");
    const n = parseInt(hex.slice(0, 6), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  const m = color.match(/rgba?\(([^)]+)\)/);
  if (m) { const p = m[1].split(",").map((x) => x.trim()); return `rgba(${p[0]},${p[1]},${p[2]},${a})`; }
  return `color-mix(in srgb, ${color} ${Math.round(a * 100)}%, transparent)`;
}

