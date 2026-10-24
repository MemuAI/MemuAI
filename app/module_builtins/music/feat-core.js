/* MemuMusic · feat-core.js — общая логика функций раунда 38:
 * серверные настройки (Cfg ↔ settings.get/set, общие с Mini App), «библиотека изменилась» (счётчики умных плейлистов),
 * множественный выбор в списках (Ctrl/Shift/рамка), горячие клавиши (настраиваемые), перемотка колесом,
 * перетаскивание файлов и папок в окно (импорт), A-B повтор, «радио» по окончании очереди, тост с «Отменить». */

/* ------------------------------------------------------------ иконки функций (дописываются в общий спрайт) */
(function addFeatIcons() {
  const sprite = document.querySelector("svg symbol") && document.querySelector("svg symbol").parentNode;
  if (!sprite) return;
  const icons = {
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    target: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',   // r41-ui: «по строке»
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="m4 18 5-5 4 4 3-3 4 4"/>',
    radio: '<circle cx="12" cy="12" r="2"/><path d="M8.2 8.2a5.4 5.4 0 0 0 0 7.6M15.8 8.2a5.4 5.4 0 0 1 0 7.6M5.3 5.3a9.5 9.5 0 0 0 0 13.4M18.7 5.3a9.5 9.5 0 0 1 0 13.4"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M3 3l18 18M10.6 6a9.7 9.7 0 0 1 1.4-.1c6 0 9.5 6.1 9.5 6.1a17 17 0 0 1-2.6 3.3M6.6 6.6C3.9 8.3 2.5 12 2.5 12s3.5 6.5 9.5 6.5a9 9 0 0 0 4.4-1.1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3.5 4v4h4M12 7.5V12l3 2"/>',
    tag: '<path d="M3.5 12.5V4.5a1 1 0 0 1 1-1h8l8 8-9 9z"/><circle cx="8" cy="8" r="1.5"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    ab: '<path d="M3 18 6.5 6h1L11 18M4.2 14h5.6M14 6h4a3 3 0 0 1 0 6h-4zm0 6h4.5a3 3 0 0 1 0 6H14z"/>',
    keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14h10"/>',
    hide: '<path d="M4 12h16"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    filter: '<path d="M4 5h16l-6.5 7.5V19l-3 1.5v-8z"/>',
    rule: '<path d="M4 6h9M4 12h6M4 18h9"/><circle cx="17" cy="6" r="2"/><circle cx="14" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    copies: '<rect x="3.5" y="7.5" width="11" height="12" rx="2.5"/><rect x="9.5" y="4.5" width="11" height="12" rx="2.5"/>',
    music: '<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
  };
  sprite.insertAdjacentHTML("beforeend", Object.entries(icons).map(([id, body]) => `<symbol id="i-${id}" viewBox="0 0 24 24">${body}</symbol>`).join(""));
})();

/* ------------------------------------------------------------ серверные настройки */
const Cfg = (() => {
  let data = {};
  const listeners = [];
  const apply = () => Player.configure({ normalize: data.normalize !== false, gapless: data.gapless !== false,
    resume: data.resume_long !== false, resumeMin: Number(data.resume_min) || 20 });
  async function load() {
    const got = await API.soft("settings.get", {}, null);
    if (got && typeof got === "object") data = got;
    if (typeof data.crossfade === "number" && data.crossfade !== Player.st.crossfade) Player.setCrossfade(data.crossfade);
    apply();
    listeners.forEach((fn) => fn(data));
    return data;
  }
  /** Изменить и сохранить на сервере (Mini App увидит то же). Возвращает true при успехе. */
  async function set(patch) {
    Object.assign(data, patch);
    apply();
    const saved = await API.soft("settings.set", patch, null);
    if (saved) Object.assign(data, saved);
    listeners.forEach((fn) => fn(data));
    return !!saved;
  }
  return { load, set, get: (key, fallback) => (data[key] === undefined ? fallback : data[key]), all: () => data, on: (fn) => listeners.push(fn) };
})();

/* ------------------------------------------------------------ библиотека изменилась */
let changeTimer = 0;
/** Любая правка библиотеки (скачали, импортировали, лайк, удалили, изменили теги) → перечитать треки и плейлисты:
 * счётчики, обложки и длительность умных плейлистов («Недавно добавленные» и т.п.) берутся из playlists.list. */
function libraryChanged(opts) {
  clearTimeout(changeTimer);
  return new Promise((resolve) => {
    changeTimer = setTimeout(async () => {
      await (opts && opts.playlistsOnly ? loadPlaylists() : refreshAll());
      if (!(opts && opts.quiet) && ["library", "playlists", "playlist", "liked", "album", "artist", "genre", "folder"].includes(S.view)) rerender();
      resolve();
    }, opts && opts.now ? 0 : 250);
  });
}

/* ------------------------------------------------------------ тост с действием */
function toastAction(text, label, run, ms) {
  const node = document.createElement("div");
  node.className = "mm-toast mm-toast-act";
  node.innerHTML = `<span>${esc(text)}</span><button type="button">${icon("undo")}${esc(label)}</button>`;
  let done = false;
  const close = () => { if (!done) { done = true; node.remove(); } };
  node.querySelector("button").onclick = () => { if (done) return; close(); run(); };
  $("#toasts").appendChild(node);
  setTimeout(close, ms || 6500);
  return close;
}

/* ------------------------------------------------------------ множественный выбор */
const Sel = (() => {
  const ids = new Set();
  let anchor = -1, list = [], opts = {};
  const bar = document.createElement("div");
  bar.className = "mm-selbar"; bar.hidden = true; bar.setAttribute("role", "toolbar");
  document.getElementById("app").appendChild(bar);
  function paint() {
    $$(".mm-row[data-id]").forEach((r) => { if (ids.has(r.dataset.id)) r.setAttribute("aria-selected", "true"); else r.removeAttribute("aria-selected"); });
    const n = ids.size;
    bar.hidden = n < 2;
    if (n < 2) return;
    bar.innerHTML = `<b>${esc(t("sel.count", { n }))}</b>
      <button type="button" class="mm-btn tint sm" data-s="play">${icon("play")}${esc(t("play"))}</button>
      <button type="button" class="mm-icon" data-s="queue" title="${esc(t("menu.add_queue"))}" aria-label="${esc(t("menu.add_queue"))}">${icon("queue")}</button>
      <button type="button" class="mm-icon" data-s="playlist" title="${esc(t("menu.add_playlist"))}" aria-label="${esc(t("menu.add_playlist"))}">${icon("playlist")}</button>
      <button type="button" class="mm-icon" data-s="like" title="${esc(t("like"))}" aria-label="${esc(t("like"))}">${icon("heart")}</button>
      <button type="button" class="mm-icon" data-s="edit" title="${esc(t("menu.edit"))}" aria-label="${esc(t("menu.edit"))}">${icon("edit")}</button>
      <button type="button" class="mm-icon" data-s="more" title="${esc(t("more"))}" aria-label="${esc(t("more"))}">${icon("more")}</button>
      <button type="button" class="mm-icon" data-s="clear" title="${esc(t("sel.clear"))}" aria-label="${esc(t("sel.clear"))}">${icon("close")}</button>`;
  }
  const tracks = () => list.filter((x) => ids.has(x.id)).concat([...ids].filter((id) => !list.some((x) => x.id === id)).map((id) => S.byId.get(id)).filter(Boolean));
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("[data-s]"); if (!b) return;
    const sel = tracks();
    const act = b.dataset.s;
    if (act === "play") Player.playList(sel, null, t("sel.title"));
    else if (act === "queue") { Player.enqueue(sel); toast(t("sel.queued", { n: sel.length })); }
    else if (act === "playlist") playlistPicker(sel, b);
    else if (act === "like") likeMany(sel, !sel.every((x) => x.liked));
    else if (act === "edit") editTracks(sel);
    else if (act === "more") bulkMenu(sel, b, opts);
    else clear();
  });
  function clear() { ids.clear(); anchor = -1; paint(); }
  /** Клик по строке списка: обычный — выбрать одну (или играть, если в настройках «одним кликом»), Ctrl — добавить/убрать, Shift — диапазон. */
  function click(e, row, tracksOfList, listOpts) {
    if (list !== tracksOfList) { list = tracksOfList; ids.forEach((id) => { if (!list.some((x) => x.id === id)) ids.delete(id); }); }
    opts = listOpts || {};
    const i = Number(row.dataset.i), x = list[i];
    if (!x) return;
    if (e.shiftKey && anchor >= 0) {
      if (!(e.ctrlKey || e.metaKey)) ids.clear();
      for (let k = Math.min(anchor, i); k <= Math.max(anchor, i); k++) if (list[k]) ids.add(list[k].id);
    } else if (e.ctrlKey || e.metaKey) { ids.has(x.id) ? ids.delete(x.id) : ids.add(x.id); anchor = i; }
    else {
      ids.clear(); ids.add(x.id); anchor = i;
      if (Cfg.get("play_click") === "single") playFrom(list, x, opts.from);
    }
    paint();
  }
  /** Рамка: тянем по пустому месту строки (не по кнопкам) — выбираем пересечённые строки. */
  function lasso(box, tracksOfList, listOpts) {
    box.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button, input, .grip") || e.target.closest(".mm-row[draggable='true']")) return;
      const start = { x: e.clientX, y: e.clientY };
      let rect = null, moved = false;
      const base = e.ctrlKey || e.metaKey ? new Set(ids) : new Set();
      const move = (ev) => {
        if (!moved && Math.abs(ev.clientY - start.y) < 8) return;
        if (!moved) { moved = true; list = tracksOfList; opts = listOpts || {}; rect = document.createElement("div"); rect.className = "mm-lasso"; document.body.appendChild(rect); }
        const l = Math.min(start.x, ev.clientX), tp = Math.min(start.y, ev.clientY), h = Math.abs(ev.clientY - start.y);
        Object.assign(rect.style, { left: l + "px", top: tp + "px", width: Math.abs(ev.clientX - start.x) + "px", height: h + "px" });
        ids.clear(); base.forEach((id) => ids.add(id));
        $$(".mm-row", box).forEach((r) => { const b = r.getBoundingClientRect(); if (b.bottom > tp && b.top < tp + h) { const x = list[Number(r.dataset.i)]; if (x) ids.add(x.id); } });
        paint();
      };
      const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); if (rect) rect.remove(); };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }
  function selectAll() { if (!list.length) return false; list.forEach((x) => ids.add(x.id)); paint(); return true; }
  return { click, lasso, paint, clear, has: (id) => ids.has(id), size: () => ids.size, tracks, selectAll, listOpts: () => opts };
})();

async function likeMany(list, liked) {
  await Promise.all(list.map((x) => API.soft("library.like", { id: x.id, liked }, null)));
  list.forEach((x) => { x.liked = liked; const lib = S.byId.get(x.id); if (lib) lib.liked = liked; });
  toast(t(liked ? "sel.liked" : "sel.unliked", { n: list.length }));
  libraryChanged();
}

/* ------------------------------------------------------------ горячие клавиши */
const HOTKEYS = [
  ["toggle", "Space"], ["next", "Shift+ArrowRight"], ["prev", "Shift+ArrowLeft"], ["seek_fwd", "ArrowRight"], ["seek_back", "ArrowLeft"],
  ["vol_up", "Shift+ArrowUp"], ["vol_down", "Shift+ArrowDown"], ["mute", "M"], ["like", "L"], ["np", "F"], ["queue", "Q"],
  ["shuffle", "Shift+S"], ["repeat", "R"], ["search", "Ctrl+K"], ["ab", "B"], ["sleep_end", "Shift+Z"], ["radio", "Shift+R"],
  ["edit", "E"], ["lyrics", "Shift+L"], ["home", "Alt+1"], ["library", "Alt+2"], ["playlists", "Alt+3"], ["history", "Alt+4"],
  // r41-ui: the lyrics of this track earlier / later (saved with the track)
  ["ly_earlier", "BracketLeft"], ["ly_later", "BracketRight"], ["ly_earlier5", "Shift+BracketLeft"], ["ly_later5", "Shift+BracketRight"],
];
/** Сочетание из события: «Ctrl+Shift+K», «Space», «ArrowLeft», «Alt+1». */
function comboOf(e) {
  let key = e.code || e.key;
  if (/^Key[A-Z]$/.test(key)) key = key.slice(3);
  else if (/^Digit\d$/.test(key)) key = key.slice(5);
  else if (/^(Control|Shift|Alt|Meta)(Left|Right)?$/.test(key)) return "";
  return [e.ctrlKey || e.metaKey ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", key].filter(Boolean).join("+");
}
function hotkeyMap() {
  const custom = Cfg.get("hotkeys", {}) || {};
  const map = new Map();
  HOTKEYS.forEach(([action, def]) => { const combo = custom[action] !== undefined ? custom[action] : def; if (combo) map.set(combo, action); });
  return map;
}
function runHotkey(action) {
  const step = Number(Cfg.get("seek_step", 10)) || 10;
  const cur = Player.current();
  const acts = {
    toggle: () => Player.toggle(), next: () => Player.next(), prev: () => Player.prev(),
    seek_fwd: () => Player.seek(Player.times().cur + step), seek_back: () => Player.seek(Player.times().cur - step),
    vol_up: () => Player.setVolume(Player.st.volume + .05), vol_down: () => Player.setVolume(Player.st.volume - .05),
    mute: () => Player.toggleMute(), like: () => cur && toggleLike(cur), np: () => (NP.isOpen() ? NP.close() : NP.open()),
    queue: () => toggleQueueDrawer(), shuffle: () => Player.setShuffle(), repeat: () => Player.cycleRepeat(),
    search: () => go("search"), ab: () => { Player.setAB(); }, sleep_end: () => { Player.setSleep(0, !Player.st.sleepEnd); toast(Player.st.sleepEnd ? t("sleep.set") : t("sleep.cancelled")); },
    radio: () => cur && startRadio(cur), edit: () => cur && editTracks([cur]), lyrics: () => NP.open("lyrics"),
    home: () => go("home"), library: () => go("library"), playlists: () => go("playlists"), history: () => go("history"),
    ly_earlier: () => NP.nudge(-.1, true), ly_later: () => NP.nudge(.1, true), ly_earlier5: () => NP.nudge(-.5, true), ly_later5: () => NP.nudge(.5, true),
  };
  if (acts[action]) acts[action]();
}
/** Обработчик keydown окна (main.js зовёт его после Escape). true — клавиша обработана. */
function handleHotkey(e) {
  if (e.target.closest && e.target.closest(".mm-flow") && /Arrow(Left|Right)$/.test(e.code) && !e.shiftKey) return false;
  const combo = comboOf(e);
  if (!combo) return false;
  if (isTyping(e.target) && combo !== "Ctrl+K") return false;
  if (combo === "Ctrl+A" && !isTyping(e.target) && $(".mm-tracks")) { e.preventDefault(); return Sel.selectAll(); }
  if (combo === "Delete" && Sel.size() > 0) { e.preventDefault(); deleteTracks(Sel.tracks()); return true; }
  const action = hotkeyMap().get(combo);
  if (!action) return false;
  e.preventDefault();
  runHotkey(action);
  return true;
}

/* ------------------------------------------------------------ перемотка колесом над полосой времени */
$$(".mm-seek").forEach((el) => el.addEventListener("wheel", (e) => {
  e.preventDefault();
  const step = Number(Cfg.get("seek_step", 10)) || 10;
  Player.seek(Player.times().cur - Math.sign(e.deltaY || e.deltaX) * step);
}, { passive: false }));

/* ------------------------------------------------------------ A-B повтор: метка в «Сейчас играет» */
(function abChip() {
  const tools = $(".mm-np-tools");
  if (!tools) return;
  const chip = document.createElement("button");
  chip.type = "button"; chip.className = "mm-chip glass"; chip.id = "np-ab";
  chip.innerHTML = `${icon("ab")}<span>${esc(t("ab.title"))}</span>`;
  chip.title = t("ab.hint");
  chip.onclick = () => Player.setAB();
  tools.appendChild(chip);
  Player.on("ab", (st) => {
    const ab = st.ab;
    chip.setAttribute("aria-pressed", String(!!ab));
    chip.querySelector("span").textContent = !ab ? t("ab.title") : ab.b == null ? t("ab.a", { a: fmtTime(ab.a) }) : t("ab.on", { a: fmtTime(ab.a), b: fmtTime(ab.b) });
  });
})();

/* ------------------------------------------------------------ радио / микс */
async function startRadio(track, opts) {
  const res = await API.soft("library.radio", { id: track.id, limit: 40 }, null);
  if (!res || !res.tracks || !res.tracks.length) { toast(t("radio.empty")); return; }
  const list = res.tracks.map((x) => S.byId.get(x.id) || normTrack(x));
  if (opts && opts.append) Player.enqueue(list.slice(1).filter((x) => !Player.st.queue.some((q) => q.id === x.id)));
  else Player.playList(list, track.id, t("radio.from", { title: track.title }));
  toast(t("radio.started", { title: track.title }));
}
// r39-integ: what plays after the queue — the radio of the last track («auto radio»), otherwise the library in its order
// (S.tracks: the library as the «Library» view sorts it); before the first track — the previous track of the library.
Player.setContinue(async (last, dir) => {
  if (dir > 0 && Cfg.get("auto_radio")) {
    const res = await API.soft("library.radio", { id: last.id, limit: 40 }, null);
    const list = ((res && res.tracks) || []).map((x) => S.byId.get(x.id) || normTrack(x)).filter((x) => x.id !== last.id);
    if (list.length) { toast(t("radio.started", { title: last.title })); return list; }
  }
  const lib = S.tracks || [];
  const at = lib.findIndex((x) => x.id === last.id);
  if (dir > 0) return (at >= 0 ? lib.slice(at + 1) : lib).slice(0, 300);
  return at > 0 ? [lib[at - 1]] : [];
});

/* ------------------------------------------------------------ перетаскивание файлов и папок (импорт) */
const AUDIO_RE = /\.(mp3|flac|m4a|mp4|aac|ogg|oga|opus|wav|webm|wma|aiff?|alac)$/i;
function fileDrag(e) { return !!(e.dataTransfer && [...e.dataTransfer.types].includes("Files")); }
let fileDepth = 0;
document.addEventListener("dragenter", (e) => { if (!fileDrag(e)) return; fileDepth++; $("#drop").hidden = false; $("#drop").classList.add("files"); });
document.addEventListener("dragleave", (e) => { if (!fileDrag(e)) return; if (--fileDepth <= 0) { fileDepth = 0; $("#drop").hidden = true; $("#drop").classList.remove("files"); } });
document.addEventListener("dragover", (e) => { if (fileDrag(e)) e.preventDefault(); });
document.addEventListener("drop", (e) => {
  if (!fileDrag(e)) return;
  e.preventDefault(); fileDepth = 0; $("#drop").hidden = true; $("#drop").classList.remove("files");
  const entries = [...e.dataTransfer.items || []].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
  if (entries.length) collectEntries(entries).then(importFiles);
  else importFiles([...e.dataTransfer.files]);
});
async function collectEntries(entries) {
  const out = [];
  const walk = async (entry) => {
    if (out.length >= 500) return;
    if (entry.isFile) { const f = await new Promise((res) => entry.file(res, () => res(null))); if (f) out.push(f); return; }
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((res) => reader.readEntries(res, () => res([])));
      if (!batch.length) break;
      for (const child of batch) await walk(child);
    }
  };
  for (const entry of entries) await walk(entry);
  return out;
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}
async function importFiles(files) {
  const audio = files.filter((f) => AUDIO_RE.test(f.name) && f.size < 600 * 1024 * 1024);
  if (!audio.length) { toast(t("drop.no_audio")); return; }
  let ok = 0, fail = 0;
  const note = document.createElement("div");
  note.className = "mm-toast"; $("#toasts").appendChild(note);
  for (const f of audio) {
    note.textContent = t("drop.importing", { done: ok + fail, total: audio.length, name: f.name });
    try { await API.call("library.upload", { name: f.name, data: await fileToBase64(f) }); ok++; } catch (err) { fail++; console.warn("[music] upload", f.name, err); }
  }
  note.remove();
  toast(fail ? t("drop.imported_some", { ok, fail }) : t("drop.imported", { n: ok }));
  libraryChanged({ now: true });
}

/* ------------------------------------------------------------ старт */
Cfg.load().then(() => { if (S.view === "settings") rerender(); });
/** «Любимые» скрыты пользователем (серверная настройка smart_hidden) — их плитку и пункт в боковой панели не показываем. */
function likedHidden() { return (Cfg.get("smart_hidden", []) || []).includes("smart:liked"); }
Cfg.on(() => renderSidePlaylists());
/* Прослушивание засчитывается во время трека — при смене трека обновим «Самые слушаемые». */
Player.on("track", () => libraryChanged({ playlistsOnly: true, quiet: true }));
