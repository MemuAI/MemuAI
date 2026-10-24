/* MemuMusic · feat-lib.js (r39-music-lib) — сортировка любого списка (заголовки колонок, меню «Сортировка» для сеток,
 * запоминание для каждого вида в серверной настройке sort_views — общей с Mini App), ручной порядок
 * (плавное перетаскивание, Alt+↑/↓, «В начало/в конец»; порядок плейлистов в боковой панели и на экране «Плейлисты»),
 * редактор исполнителя (имя с объединением, фото: файл/буфер/ссылка/поиск Deezer, авто-фото, описание) и альбома. */

(() => {
  const sprite = document.querySelector("svg symbol") && document.querySelector("svg symbol").parentNode;
  sprite && sprite.insertAdjacentHTML("beforeend", `<symbol id="i-sort" viewBox="0 0 24 24"><path d="M7 4v16M3.5 16.5 7 20l3.5-3.5M14 6h7M14 11h5M14 16h3"/></symbol><symbol id="i-blank" viewBox="0 0 24 24"></symbol><symbol id="i-top" viewBox="0 0 24 24"><path d="M5 4h14M12 20V9M7 13l5-5 5 5"/></symbol><symbol id="i-bottom" viewBox="0 0 24 24"><path d="M5 20h14M12 4v11M7 11l5 5 5-5"/></symbol>`);
})();

/* ------------------------------------------------------------ сортировка */
const Sorter = (() => {
  const DIR = { added: "desc", liked: "desc", plays: "desc", year: "desc", updated: "desc", count: "desc", tracks: "desc" };
  const str = (a, b) => String(a || "").localeCompare(String(b || ""), undefined, { numeric: true, sensitivity: "base" });
  const num = (a, b) => (Number(a) || 0) - (Number(b) || 0);
  const CMP = {
    title: (a, b) => str(a.title || a.name, b.title || b.name), name: (a, b) => str(a.name || a.title, b.name || b.title),
    artist: (a, b) => str(a.artist, b.artist) || str(a.album, b.album) || num(a.track_no, b.track_no),
    album: (a, b) => str(a.album, b.album) || num(a.track_no, b.track_no), duration: (a, b) => num(a.duration, b.duration),
    added: (a, b) => str(a.added_at || a.added, b.added_at || b.added), plays: (a, b) => num(a.plays, b.plays),
    year: (a, b) => num(parseInt(a.year, 10), parseInt(b.year, 10)), liked: (a, b) => num(a.liked_at, b.liked_at),
    updated: (a, b) => num(a.updated_at, b.updated_at), count: (a, b) => num(a.count, b.count),
    tracks: (a, b) => num((a.tracks || []).length, (b.tracks || []).length),
  };
  const parse = (text) => { const [field, dir] = String(text || "").split(":"); return { field, dir: dir === "asc" ? "asc" : "desc" }; };
  function get(view, def) {
    const saved = (Cfg.get("sort_views", {}) || {})[view];
    const spec = parse(saved || def || "added:desc");
    return spec.field ? spec : parse(def);
  }
  function set(view, spec) {
    const all = Object.assign({}, Cfg.get("sort_views", {}) || {}, { [view]: spec.field + ":" + spec.dir });
    const done = Cfg.set({ sort_views: all });
    $$(`[data-grid-sort="${view}"] span`).forEach((n) => { n.textContent = sortLabel(spec); });   // кнопка «Сортировка» в тулбаре — та же
    return done;
  }
  /** Тот же столбец — обратное направление; новый — его естественное (даты и счётчики — по убыванию). */
  function toggle(view, field, def) {
    const cur = get(view, def);
    const spec = cur.field === field ? { field, dir: cur.dir === "asc" ? "desc" : "asc" } : { field, dir: DIR[field] || "asc" };
    set(view, spec);
    return spec;
  }
  function apply(list, spec) {
    const out = list.slice();
    if (spec.field === "pos") return spec.dir === "desc" ? out.reverse() : out;
    const cmp = CMP[spec.field]; if (!cmp) return out;
    const sign = spec.dir === "asc" ? 1 : -1;
    return out.map((x, i) => [x, i]).sort((a, b) => sign * cmp(a[0], b[0]) || a[1] - b[1]).map((p) => p[0]);
  }
  return { get, set, toggle, apply, natural: DIR };
})();
const TRACK_SORTS = ["added", "title", "artist", "album", "duration", "plays", "year"];

/** Перед отрисовкой списка: порядок из настройки вида; ручное перемещение — только при «своём порядке». */
function sortedForList(host, tracks, opts) {
  if (opts.reorderable === undefined) opts.reorderable = !!opts.reorder;
  opts._host = host; opts._base = tracks;
  const spec = opts._spec = Sorter.get(opts.sortView, opts.sortDefault);
  opts.reorder = opts.reorderable && spec.field === "pos";
  return Sorter.apply(tracks, spec);
}
function sortFieldsFor(opts) {
  return (opts.sortDefault && opts.sortDefault.startsWith("pos") ? ["pos"] : []).concat(opts.sortView === "liked" ? ["liked"] : [], TRACK_SORTS);
}
function theadHtml(opts) {
  if (!opts.sortView) return `<div class="mm-thead"><span class="r">#</span><span>${esc(t("col.title"))}</span><span class="al">${esc(t("col.album"))}</span><span class="src" title="${esc(t("col.source"))}"></span><span class="r">${esc(t("col.time"))}</span><span></span></div>`;
  const spec = opts._spec;
  const head = (field, label, cls) => `<button type="button" class="mm-th${cls ? " " + cls : ""}" data-sort="${field}"${spec.field === field ? ` aria-sort="${spec.dir === "asc" ? "ascending" : "descending"}"` : ""}>${esc(label)}<i class="mm-th-dir" aria-hidden="true"></i></button>`;
  const first = opts.sortDefault && opts.sortDefault.startsWith("pos") ? "pos" : "added";
  return `<div class="mm-thead mm-thead-sort"><span class="r">${head(first, "#", "r")}</span><span class="mm-th-pair">${head("title", t("col.title"))}<span class="mm-th-sep" aria-hidden="true">·</span>${head("artist", t("sort.col_artist"))}</span><span class="al">${head("album", t("col.album"))}</span><span class="src" title="${esc(t("col.source"))}"></span><span class="r">${head("duration", t("col.time"), "r")}</span>
    <span class="r"><button type="button" class="mm-icon sm mm-th-menu" data-sort-menu aria-label="${esc(t("lib.sort"))}" title="${esc(sortLabel(spec))}">${icon("sort")}</button></span></div>`;
}
function sortLabel(spec) { return t("sort.f." + spec.field) + (spec.field === "pos" && spec.dir === "asc" ? "" : " " + (spec.dir === "asc" ? "↑" : "↓")); }
/** Меню «Сортировка»: поля с отметкой и направление. */
function sortMenu(anchor, view, fields, def, onChange, extra) {
  const cur = Sorter.get(view, def);
  const pick = (spec) => { Sorter.set(view, spec); onChange(spec); };
  openMenu(fields.map((f) => ({ icon: cur.field === f ? "check" : "blank", label: t("sort.f." + f), run: () => pick(cur.field === f ? cur : { field: f, dir: Sorter.natural[f] || "asc" }) }))
    .concat(["-", { icon: cur.dir === "asc" ? "check" : "blank", label: t("sort.asc"), run: () => pick({ field: cur.field, dir: "asc" }) },
      { icon: cur.dir === "desc" ? "check" : "blank", label: t("sort.desc"), run: () => pick({ field: cur.field, dir: "desc" }) }], extra || []), anchor);
}
function bindThead(host, opts) {
  const head = host.querySelector(".mm-thead-sort"); if (!head) return;
  head.addEventListener("click", (e) => {
    const again = () => trackList(opts._host, opts._base, opts);
    const th = e.target.closest("[data-sort]");
    if (th) { Sorter.toggle(opts.sortView, th.dataset.sort, opts.sortDefault); again(); return; }
    const m = e.target.closest("[data-sort-menu]");
    // плейлист, отсортированный по полю: «Сохранить как свой порядок» — порядок записывается в плейлист
    const keep = opts.reorderable && opts._spec.field !== "pos" ? ["-", { icon: "check", label: t("sort.keep"), run: () => {
      const ids = Sorter.apply(opts._base, opts._spec).map((x) => x.id);
      const map = new Map(opts._base.map((x) => [x.id, x]));
      opts._base = ids.map((id) => map.get(id));
      Sorter.set(opts.sortView, { field: "pos", dir: "asc" }); again();
      opts.onReorder && opts.onReorder(ids); toast(t("sort.kept"));
    } }] : [];
    if (m) sortMenu(m, opts.sortView, sortFieldsFor(opts), opts.sortDefault, again, keep);
  });
}
/** Кнопка «Сортировка» для сеток (альбомы, исполнители, плейлисты). */
function sortButton(view, def) {
  return `<button type="button" class="mm-btn ghost sm mm-sort-btn" data-grid-sort="${esc(view)}" data-def="${esc(def)}">${icon("sort")}<span>${esc(sortLabel(Sorter.get(view, def)))}</span></button>`;
}
function bindSortButton(el, fields, onChange) {
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-grid-sort]"); if (!b) return;
    sortMenu(b, b.dataset.gridSort, fields, b.dataset.def, onChange);
  });
}

/* ------------------------------------------------------------ ручной порядок */
/** FLIP: строки, сменившие место, плавно доезжают до нового (ключ — data-id). */
function flipRows(box, selector, change) {
  const before = new Map($$(selector, box).map((n) => [n.dataset.id, n.getBoundingClientRect().top]));
  change();
  if (document.documentElement.dataset.mmMotion === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  $$(selector, box).forEach((n) => {
    const was = before.get(n.dataset.id); if (was === undefined) return;
    const dy = was - n.getBoundingClientRect().top;
    if (Math.abs(dy) > 1) n.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 220, easing: "cubic-bezier(.2,.8,.2,1)" });
  });
}
/** Новый порядок ids после «переместить»: where — "top" | "bottom" | -1 | +1. */
function movedOrder(ids, moving, where) {
  const set = new Set(moving);
  if (where === "top" || where === "bottom") { const rest = ids.filter((id) => !set.has(id)); return where === "top" ? moving.concat(rest) : rest.concat(moving); }
  const out = ids.slice();
  const idx = out.map((id, i) => (set.has(id) ? i : -1)).filter((i) => i >= 0);
  if (!idx.length || (where < 0 && idx[0] === 0) || (where > 0 && idx[idx.length - 1] === out.length - 1)) return out;
  (where < 0 ? idx : idx.reverse()).forEach((i) => { const j = i + where; [out[i], out[j]] = [out[j], out[i]]; });
  return out;
}
/** Alt+↑/↓ двигает выбранные строки списка с ручным порядком (одна клавиатурная привязка на окно). */
let reorderList = null;
document.addEventListener("keydown", (e) => {
  if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown") || isTyping(e.target)) return;
  if (!reorderList || !reorderList.alive() || !Sel.size()) return;
  e.preventDefault(); e.stopPropagation();
  reorderList.move(Sel.tracks().map((x) => x.id), e.key === "ArrowUp" ? -1 : 1);
}, true);

/** Перетаскивание плиток/строк плейлистов (боковая панель, экран «Плейлисты») — свой порядок обычных плейлистов. */
function bindPlaylistDrag(box, selector) {
  let dragId = "";
  const plain = () => S.playlists.filter((p) => !p.smart && !p.rules).map((p) => p.id);
  box.addEventListener("pointerdown", (e) => { const n = e.target.closest(selector); if (n && plain().includes(n.dataset.id)) n.draggable = true; });
  box.addEventListener("dragstart", (e) => {
    const n = e.target.closest(selector); if (!n || !plain().includes(n.dataset.id)) return;
    dragId = n.dataset.id; n.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("application/x-memu-playlist", dragId);
  });
  const clear = () => $$(".dragging, .drop-before", box).forEach((n) => n.classList.remove("dragging", "drop-before"));
  box.addEventListener("dragend", () => { clear(); dragId = ""; });
  box.addEventListener("dragover", (e) => {
    if (!dragId) return; const n = e.target.closest(selector);
    if (!n || !plain().includes(n.dataset.id)) return;
    e.preventDefault(); $$(".drop-before", box).forEach((x) => x.classList.remove("drop-before")); n.classList.add("drop-before");
  });
  box.addEventListener("drop", async (e) => {
    if (!dragId) return; const n = e.target.closest(selector); const moving = dragId; dragId = ""; clear();
    if (!n || n.dataset.id === moving || !plain().includes(n.dataset.id)) return;
    e.preventDefault();
    const ids = plain().filter((id) => id !== moving); ids.splice(ids.indexOf(n.dataset.id), 0, moving);
    const rank = new Map(ids.map((id, i) => [id, i]));
    S.playlists.sort((a, b) => (rank.has(a.id) && rank.has(b.id) ? rank.get(a.id) - rank.get(b.id) : 0));
    flipRows($("#side-playlists"), ".mm-side-pl[data-id]", () => renderSidePlaylists());
    if (S.view === "playlists") rerender();
    await API.soft("playlists.reorder", { ids }, null);
    Cfg.load();
  });
}
bindPlaylistDrag($("#side-playlists"), ".mm-side-pl[data-id]");

/* ------------------------------------------------------------ исполнители: фото, описание, имя */
const ArtistMeta = (() => {
  const map = new Map();   // имя (нижний регистр) -> { image, bio }
  let loaded = null;
  function load(force) {
    if (loaded && !force) return loaded;
    loaded = API.soft("artists.list", {}, []).then((list) => {
      map.clear(); (Array.isArray(list) ? list : []).forEach((a) => { if (a.image) map.set(String(a.name).toLowerCase(), { image: a.image }); });
    });
    return loaded;
  }
  return { load, get: (name) => map.get(String(name || "").toLowerCase()) || null, set: (name, v) => { if (v && v.image) map.set(String(name).toLowerCase(), v); else map.delete(String(name).toLowerCase()); } };
})();
/** Обложка исполнителя: его фото, иначе обложка трека. */
function artistArt(name, track) {
  const m = ArtistMeta.get(name);
  return m && m.image ? `<img alt="" src="${esc(m.image)}" data-fb="${esc(coverArt(name))}">` : artImg(track || { id: name });
}
ArtistMeta.load().then(() => { if (["library", "home", "artist"].includes(S.view)) rerender(); });

/** Страница исполнителя: фото, описание, кнопка «Изменить», авто-фото из Deezer (если включено и фото нет). */
async function decorateArtist(el, name) {
  const head = el.querySelector(".mm-head"); if (!head) return;
  const acts = head.querySelector(".mm-head-actions");
  acts.insertAdjacentHTML("beforeend", `<button type="button" class="mm-icon lg" data-edit-artist title="${esc(t("artist.edit"))}" aria-label="${esc(t("artist.edit"))}">${icon("edit")}</button>`);
  acts.querySelector("[data-edit-artist]").onclick = () => editArtist(name);
  const info = await API.soft("artist.get", { name }, null);
  if (!el.isConnected || !info) return;
  const paint = (meta) => {
    if (meta.image) head.querySelector(".mm-art").innerHTML = `<img alt="" src="${esc(meta.image)}">`;
    ArtistMeta.set(name, meta.image ? { image: meta.image } : null);
  };
  paint(info);
  if (info.bio) head.querySelector(".mm-sub").insertAdjacentHTML("afterend", `<p class="mm-artist-bio">${esc(info.bio)}</p>`);
  if (!info.image && !info.image_auto && Cfg.get("artist_photos", true) !== false && !API.mocked) {
    const got = await API.soft("artist.image_auto", { name }, null);
    if (got && got.image && el.isConnected) paint(got);
  }
}
function decorateAlbum(el, list) {
  const acts = el.querySelector(".mm-head .mm-head-actions"); if (!acts) return;
  acts.insertAdjacentHTML("beforeend", `<button type="button" class="mm-icon lg" data-edit-album title="${esc(t("album.edit"))}" aria-label="${esc(t("album.edit"))}">${icon("edit")}</button>`);
  acts.querySelector("[data-edit-album]").onclick = () => editAlbum(list);
}

/** Общий блок «картинка»: файл, вставка, ссылка, поиск онлайн. apply({data}|{url}|{clear}) → Promise. */
function imagePicker(card, prefix, query, search, apply) {
  card.querySelector(`#${prefix}-file`).addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) apply({ data: await readImageFile(f) }); });
  card.querySelector(`#${prefix}-paste`).onclick = async () => { const data = await clipboardImage(); if (data) apply({ data }); else toast(t("ed.paste_hint")); };
  card.addEventListener("paste", async (e) => {
    const item = [...(e.clipboardData && e.clipboardData.items) || []].find((x) => x.type.startsWith("image/"));
    if (item) { e.preventDefault(); apply({ data: await readImageFile(item.getAsFile()) }); }
  });
  card.querySelector(`#${prefix}-url-go`).onclick = () => { const url = card.querySelector(`#${prefix}-url`).value.trim(); if (url) apply({ url }); };
  const grid = card.querySelector(`#${prefix}-found`);
  const find = async () => {
    const q = card.querySelector(`#${prefix}-q`).value.trim() || query; if (!q) return;
    grid.innerHTML = '<div class="mm-skel" style="height:96px;grid-column:1/-1"></div>';
    const found = await API.soft(search, { q, name: q, limit: 12 }, []);
    grid.innerHTML = (found || []).length ? found.map((x, i) => `<button type="button" data-c="${i}" title="${esc(x.name || x.title || "")}"><img alt="" src="${esc(x.thumb)}"></button>`).join("") : `<p class="mm-sub" style="grid-column:1/-1;margin:0">${esc(t("empty.nothing"))}</p>`;
    grid.onclick = (e) => { const b = e.target.closest("[data-c]"); if (b) apply({ url: found[Number(b.dataset.c)].url }); };
  };
  card.querySelector(`#${prefix}-q-go`).onclick = find;
  card.querySelector(`#${prefix}-q`).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); find(); } });
}
function imagePickerHtml(prefix, query, removable) {
  return `<div class="mm-ed-cover-acts">
      <label class="mm-btn ghost sm">${icon("upload")}${esc(t("ed.cover_file"))}<input type="file" accept="image/*" id="${prefix}-file" hidden></label>
      <button type="button" class="mm-btn ghost sm" id="${prefix}-paste">${icon("copy")}${esc(t("ed.cover_paste"))}</button>
      ${removable ? `<button type="button" class="mm-btn ghost sm" id="${prefix}-clear">${icon("trash")}${esc(t("artist.photo_remove"))}</button>` : ""}
    </div>
    <div class="mm-ed-row"><input class="mm-input" id="${prefix}-url" type="url" placeholder="${esc(t("ed.cover_url"))}" spellcheck="false"><button type="button" class="mm-btn ghost sm" id="${prefix}-url-go">${esc(t("ed.apply"))}</button></div>
    <div class="mm-ed-row"><input class="mm-input" id="${prefix}-q" type="search" value="${esc(query)}" placeholder="${esc(t("ed.cover_search"))}"><button type="button" class="mm-btn tint sm" id="${prefix}-q-go">${icon("search")}${esc(t("ed.find"))}</button></div>
    <div class="mm-ed-found-grid" id="${prefix}-found"></div>`;
}

function editArtist(name) {
  const list = S.tracks.filter((x) => (x.artist || "").toLowerCase() === String(name).toLowerCase());
  const meta = ArtistMeta.get(name) || {};
  modal(`<div class="mm-ed">
    <header class="mm-ed-head"><span class="mm-ed-art round" id="ae-art">${artistArt(name, list[0])}</span>
      <div><span class="mm-kind">${esc(t("kind.artist"))}</span><h3>${esc(t("artist.edit"))}</h3><p class="mm-sub">${esc(plural("count.tracks", list.length))}</p></div></header>
    <label class="mm-ed-field wide"><span>${esc(t("artist.name"))}</span><input class="mm-input" id="ae-name" maxlength="200" value="${esc(name)}"></label>
    <label class="mm-ed-field wide"><span>${esc(t("artist.bio"))}</span><textarea class="mm-input mm-ed-desc" id="ae-bio" maxlength="2000" placeholder="${esc(t("artist.bio_ph"))}"></textarea></label>
    <div class="mm-ed-sec">${esc(t("artist.photo"))}</div>
    ${imagePickerHtml("ae", name, !!meta.image)}
    <div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" id="ae-save">${esc(t("save"))}</button></div>
  </div>`, async (card, close) => {
    const bio = card.querySelector("#ae-bio");
    API.soft("artist.get", { name }, null).then((info) => { if (info && !bio.value) bio.value = info.bio || ""; });
    const setPhoto = async (args) => {
      try {
        const out = await API.call("artist.image", Object.assign({ name }, args));
        ArtistMeta.set(name, out.image ? { image: out.image } : null);
        card.querySelector("#ae-art").innerHTML = artistArt(name, list[0]);
        toast(t(args.clear ? "artist.photo_removed" : "artist.photo_set"));
        if (S.view === "artist" || S.view === "library") rerender();
      } catch (err) { toast(String(err.message || err)); }
    };
    imagePicker(card, "ae", name, "artist.image_search", setPhoto);
    const clear = card.querySelector("#ae-clear"); if (clear) clear.onclick = () => setPhoto({ clear: true });
    card.querySelector("#ae-save").onclick = async () => {
      const next = card.querySelector("#ae-name").value.trim();
      if (!next) { card.querySelector("#ae-name").focus(); return; }
      const save = async () => {
        try {
          await API.call("artist.bio", { name, bio: bio.value });
          let target = name;
          if (next !== name) {
            const out = await API.call("artist.rename", { name, new_name: next });
            target = out.name;
            toast(t(out.merged ? "artist.merged" : "artist.renamed", { name: target }));
          } else toast(t("toast.saved"));
          close();
          await ArtistMeta.load(true);
          await libraryChanged({ now: true, quiet: true });
          go("artist", { name: target }, { force: true });
        } catch (err) { toast(String(err.message || err)); }
      };
      if (next === name) { save(); return; }
      const exists = S.tracks.some((x) => (x.artist || "").toLowerCase() === next.toLowerCase() && (x.artist || "").toLowerCase() !== name.toLowerCase());
      modal(`<h3>${esc(t(exists ? "artist.merge_title" : "artist.rename_title"))}</h3><p class="mm-sub" style="margin:0">${esc(t(exists ? "artist.merge_text" : "artist.rename_text", { from: name, to: next, n: list.length }))}</p>
        <div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" data-yes>${esc(t(exists ? "artist.merge_yes" : "artist.rename_yes"))}</button></div>`,
        (c2, close2) => { c2.querySelector("[data-yes]").onclick = () => { close2(); save(); }; });
    };
  });
}

function editAlbum(list) {
  const a = list[0] || {};
  const ids = list.map((x) => x.id);
  modal(`<div class="mm-ed">
    <header class="mm-ed-head"><span class="mm-ed-art" id="al-art">${artImg(a, "album")}</span>
      <div><span class="mm-kind">${esc(t("kind.album"))}</span><h3>${esc(t("album.edit"))}</h3><p class="mm-sub">${esc(plural("count.tracks", list.length))}</p></div></header>
    <label class="mm-ed-field wide"><span>${esc(t("album.title"))}</span><input class="mm-input" id="al-title" maxlength="200" value="${esc(a.album || a.title || "")}"></label>
    <div class="mm-ed-grid two"><label class="mm-ed-field"><span>${esc(t("album.artist"))}</span><input class="mm-input" id="al-artist" maxlength="200" value="${esc(a.album_artist || a.artist || "")}"></label>
      <label class="mm-ed-field"><span>${esc(t("album.year"))}</span><input class="mm-input" id="al-year" maxlength="10" value="${esc(a.year || "")}"></label></div>
    <div class="mm-ed-sec">${esc(t("album.cover"))}</div>
    ${imagePickerHtml("al", [a.artist, a.album].filter(Boolean).join(" "), false)}
    <div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" id="al-save">${esc(t("save"))}</button></div>
  </div>`, (card, close) => {
    const setCover = async (cover) => {
      try {
        const out = await API.call("album.update", { ids, cover });
        (out.tracks || []).forEach((x) => mergeTrack(x));
        card.querySelector("#al-art").innerHTML = artImg(S.byId.get(ids[0]) || a, "album");
        toast(t("ed.cover_set"));
      } catch (err) { toast(String(err.message || err)); }
    };
    imagePicker(card, "al", [a.artist, a.album].filter(Boolean).join(" "), "cover.search", setCover);
    card.querySelector("#al-save").onclick = async () => {
      const title = card.querySelector("#al-title").value.trim();
      if (!title) { card.querySelector("#al-title").focus(); return; }
      try {
        const out = await API.call("album.update", { ids, title, album_artist: card.querySelector("#al-artist").value.trim(), year: card.querySelector("#al-year").value.trim() });
        (out.tracks || []).forEach((x) => mergeTrack(x));
        close(); toast(t("toast.saved"));
        await libraryChanged({ now: true, quiet: true });
        const first = S.byId.get(ids[0]);
        go("album", { key: first ? first.artist + "|" + (first.album || first.title) : "" }, { force: true });
      } catch (err) { toast(String(err.message || err)); }
    };
  });
}
