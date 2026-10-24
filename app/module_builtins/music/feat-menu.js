/* MemuMusic · feat-menu.js — контекстное меню трека (ПКМ и «⋯»), меню выбранных треков,
 * выбор плейлиста с поиском и «Новый плейлист», удаление с подтверждением и «Отменить». */

function trackMenu(x, anchor, opts) {
  opts = opts || {};
  if (Sel.size() > 1 && Sel.has(x.id)) { bulkMenu(Sel.tracks(), anchor, opts); return; }
  const items = [
    { icon: "play", label: t("menu.play_next"), run: () => { Player.enqueue(x, true); toast(t("toast.next", { title: x.title })); } },
    { icon: "queue", label: t("menu.add_queue"), run: () => { Player.enqueue(x); toast(t("toast.queued", { title: x.title })); } },
    { icon: "playlist", label: t("menu.add_playlist"), run: () => playlistPicker([x], anchor) },
    { icon: "radio", label: t("menu.radio"), run: () => startRadio(x) },
    { icon: "heart", label: t(x.liked ? "menu.unlike" : "menu.like"), run: () => toggleLike(x) },
    "-",
    x.artist ? { icon: "user", label: t("menu.go_artist"), run: () => go("artist", { name: x.artist }) } : null,
    x.album ? { icon: "album", label: t("menu.go_album"), run: () => go("album", { key: x.artist + "|" + x.album }) } : null,
    { icon: "edit", label: t("menu.edit"), run: () => editTracks([x]) },
    { icon: "folder", label: t("menu.reveal"), run: () => revealTrack(x) },
    x.source_url ? { icon: "link", label: t("menu.open_source"), run: () => Memu.open({ url: x.source_url }).catch(() => {}) } : null,
    x.source_url ? { icon: "copy", label: t("menu.copy_link"), run: () => copyText(x.source_url) } : null,
    x.source_url ? { icon: "download", label: t("menu.redownload"), run: () => redownload([x]) } : null,
    "-",
    opts.moveTo ? { icon: "top", label: t("move.top"), run: () => opts.moveTo([x.id], "top") } : null,   // r39-music-lib
    opts.moveTo ? { icon: "bottom", label: t("move.bottom"), run: () => opts.moveTo([x.id], "bottom") } : null,
    opts.playlistId ? { icon: "close", label: t("menu.remove_playlist"), run: () => removeFromPlaylist(opts.playlistId, [x]) } : null,
    { icon: "trash", label: t("menu.delete"), danger: true, run: () => deleteTracks([x]) },
  ].filter(Boolean);
  openMenu(items.filter((it, i) => it !== "-" || (i > 0 && items[i - 1] !== "-")), anchor);
}

function bulkMenu(list, anchor, opts) {
  opts = opts || {};
  const n = list.length;
  const items = [
    { icon: "play", label: t("sel.play_next", { n }), run: () => { Player.enqueue(list, true); toast(t("sel.queued", { n })); } },
    { icon: "queue", label: t("menu.add_queue"), run: () => { Player.enqueue(list); toast(t("sel.queued", { n })); } },
    { icon: "playlist", label: t("menu.add_playlist"), run: () => playlistPicker(list, anchor) },
    { icon: "plus", label: t("sel.new_playlist"), run: () => createPlaylistDialog(list.map((x) => x.id)) },
    { icon: "heart", label: t("sel.like_all"), run: () => likeMany(list, true) },
    { icon: "heart", label: t("sel.unlike_all"), run: () => likeMany(list, false) },
    "-",
    { icon: "edit", label: t("sel.edit", { n }), run: () => editTracks(list) },
    list.some((x) => x.source_url) ? { icon: "download", label: t("menu.redownload"), run: () => redownload(list.filter((x) => x.source_url)) } : null,
    "-",
    opts.moveTo ? { icon: "top", label: t("move.top"), run: () => opts.moveTo(list.map((x) => x.id), "top") } : null,   // r39-music-lib
    opts.moveTo ? { icon: "bottom", label: t("move.bottom"), run: () => opts.moveTo(list.map((x) => x.id), "bottom") } : null,
    opts.playlistId ? { icon: "close", label: t("menu.remove_playlist"), run: () => removeFromPlaylist(opts.playlistId, list) } : null,
    { icon: "trash", label: t("sel.delete", { n }), danger: true, run: () => deleteTracks(list) },
  ].filter(Boolean);
  openMenu(items, anchor);
}

/** Выбор плейлиста: поиск по названию сверху, «Новый плейлист» с этими треками. */
function playlistPicker(list, anchor) {
  list = [].concat(list);
  closeOverlays();
  const menu = $("#menu");
  const own = () => S.playlists.filter((p) => !p.smart && !p.rules);
  menu.innerHTML = `<div class="mm-pick-search">${icon("search")}<input class="mm-input" type="search" placeholder="${esc(t("pick.search"))}" aria-label="${esc(t("pick.search"))}"></div>
    <button type="button" role="menuitem" data-new>${icon("plus")}<span>${esc(t("playlist.new"))}</span></button><hr><div class="mm-pick-list"></div>`;
  const box = menu.querySelector(".mm-pick-list"), input = menu.querySelector("input");
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const found = own().filter((p) => !q || p.name.toLowerCase().includes(q));
    box.innerHTML = found.length ? found.map((p) => `<button type="button" role="menuitem" data-pid="${esc(p.id)}"><span class="mm-pick-art">${collage(p)}</span><span>${esc(p.name)}</span><em>${esc(String(p.count || 0))}</em></button>`).join("")
      : `<p class="mm-pick-empty">${esc(t(q ? "pick.none" : "pick.no_playlists"))}</p>`;
  };
  draw();
  input.addEventListener("input", draw);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { const first = box.querySelector("[data-pid]"); if (first) first.click(); else if (input.value.trim()) menu.querySelector("[data-new]").click(); } });
  menu.hidden = false;
  placeMenu(menu, anchor);
  const onClick = async (e) => {
    if (e.target.closest("[data-new]")) {
      const name = input.value.trim();
      closeOverlays();
      if (name) { await API.call("playlist.create", { name, track_ids: list.map((x) => x.id) }); toast(t("toast.created", { name })); libraryChanged({ playlistsOnly: true }); }
      else createPlaylistDialog(list.map((x) => x.id));
      return;
    }
    const b = e.target.closest("[data-pid]"); if (!b) return;
    const p = S.playlists.find((x) => x.id === b.dataset.pid);
    closeOverlays();
    try {
      await API.call("playlist.update", { id: p.id, add: list.map((x) => x.id) });
      toast(list.length > 1 ? t("sel.added", { n: list.length, name: p.name }) : t("toast.added", { name: p.name }));
      libraryChanged({ playlistsOnly: true, quiet: S.view !== "playlist" });
    } catch (err) { toast(String(err.message || err)); }
  };
  const outside = (e) => { if (!menu.contains(e.target)) closeOverlays(); };
  menu.addEventListener("click", onClick);
  setTimeout(() => document.addEventListener("pointerdown", outside, true));
  input.focus();
  menuCleanup = () => { menu.removeEventListener("click", onClick); document.removeEventListener("pointerdown", outside, true); menu.hidden = true; };
}
function placeMenu(menu, anchor) {
  const r = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y };
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(innerWidth - mw - 8, r.right - mw)) + "px";
  menu.style.top = (r.bottom + mh + 8 > innerHeight ? Math.max(8, r.top - mh - 6) : r.bottom + 6) + "px";
}

/* ------------------------------------------------------------ действия */
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast(t("toast.copied")); }
  catch (e) {
    const area = document.createElement("textarea"); area.value = text; document.body.appendChild(area); area.select();
    const ok = document.execCommand && document.execCommand("copy"); area.remove();
    toast(ok ? t("toast.copied") : text);
  }
}
async function revealTrack(x) {
  try {
    const r = await API.call("library.reveal", { id: x.id });
    toast(r && r.opened ? t(r.via === "memufiles" ? "reveal.memufiles" : "reveal.opened") : t("reveal.path", { path: (r && r.path) || "" }));
  } catch (err) { toast(String(err.message || err)); }
}
async function redownload(list) {
  let started = 0;
  for (const x of list) { if (await API.soft("library.redownload", { id: x.id, quality: "best" }, null)) started++; }
  toast(started ? t("redownload.started", { n: started }) : t("error.title"));
  if (started) pollJobs();
}

/** Убрать из плейлиста — сразу, с «Отменить» (возвращает на прежние места). */
async function removeFromPlaylist(pid, list) {
  const before = await API.soft("playlist.get", { id: pid }, null);
  const order = before ? before.tracks.map((x) => x.id) : null;
  await API.call("playlist.update", { id: pid, remove: list.map((x) => x.id) });
  Sel.clear();
  await libraryChanged({ now: true, playlistsOnly: true });
  if (S.view === "playlist") rerender();
  toastAction(list.length > 1 ? t("sel.removed", { n: list.length }) : t("toast.removed", { title: list[0].title }), t("undo"), async () => {
    if (order) await API.soft("playlist.update", { id: pid, order }, null);
    await libraryChanged({ now: true, playlistsOnly: true });
    if (S.view === "playlist") rerender();
  });
}

/** Удалить из библиотеки: подтверждение (если включено), затем трек исчезает сразу, а файл удаляется через 6 с —
 * до этого «Отменить» возвращает всё как было. */
function deleteTracks(list) {
  list = list.filter(Boolean);
  if (!list.length) return;
  const run = () => {
    const gone = new Set(list.map((x) => x.id));
    const keep = S.tracks;
    setTracks(S.tracks.filter((x) => !gone.has(x.id)));
    Sel.clear();
    rerender();
    let undone = false;
    const timer = setTimeout(async () => {
      if (undone) return;
      for (const x of list) await API.soft("library.delete", { id: x.id }, null);
      libraryChanged({ now: true });
    }, 6000);
    toastAction(list.length > 1 ? t("sel.deleted", { n: list.length }) : t("toast.deleted"), t("undo"), () => {
      undone = true; clearTimeout(timer); setTracks(keep); rerender();
    }, 6000);
  };
  if (Cfg.get("confirm_delete", true) === false) { run(); return; }
  confirmDialog(t("delete.title"), list.length > 1 ? t("sel.delete_text", { n: list.length }) : t("delete.text", { title: list[0].title }), run);
}
