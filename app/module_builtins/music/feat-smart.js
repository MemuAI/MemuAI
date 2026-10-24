/* MemuMusic · feat-smart.js — умные плейлисты: показать/скрыть и порядок встроенных («Любимые»,
 * «Недавно добавленные», «Самые слушаемые») и своих по правилам; конструктор правил с живым предпросмотром. */

const RULE_FIELDS = {
  artist: "text", album: "text", album_artist: "text", title: "text", genre: "text", source: "text", file_ext: "text",
  year: "number", duration: "number", plays: "number", bitrate: "number", liked: "bool", added_days: "days", played_days: "days",
};
const RULE_OPS = { text: ["contains", "not_contains", "is", "is_not", "starts"], number: ["gt", "lt", "eq", "between"], bool: ["is"], days: ["within", "older"] };
const RULE_SORTS = ["added", "title", "artist", "album", "plays", "recent", "duration", "year", "random"];

/** Скрытые умные плейлисты — серверная настройка smart_hidden (её же видит Mini App). */
async function setPlaylistHidden(id, hidden) {
  const cur = new Set(Cfg.get("smart_hidden", []) || []);
  hidden ? cur.add(id) : cur.delete(id);
  await Cfg.set({ smart_hidden: [...cur] });
  await libraryChanged({ now: true, playlistsOnly: true });
  if (["playlists", "home"].includes(S.view)) rerender();
}

/* ------------------------------------------------------------ управление умными плейлистами */
async function smartManager() {
  const all = (await API.soft("playlists.list", { all: true }, [])) || [];
  const smart = all.filter((p) => p.smart || p.rules);
  const name = (p) => (Memu.lang === "en" ? p.name_en : p.name_ru) || p.name;
  modal(`<div class="mm-ed">
    <header class="mm-ed-head plain"><div><span class="mm-kind">${esc(t("smart.kind"))}</span><h3>${esc(t("smart.manage"))}</h3><p class="mm-sub">${esc(t("smart.manage_hint"))}</p></div></header>
    <ol class="mm-smart-list" id="sm-list">${smart.map((p, i) => `<li draggable="true" data-id="${esc(p.id)}" data-pos="${i}"><span class="grip">${icon("grip")}</span><span class="mm-pick-art">${collage(p)}</span>
      <span class="mm-smart-name"><b>${esc(name(p))}</b><em>${esc(p.rules ? t("smart.by_rules") : t("smart.builtin"))} · ${esc(plural("count.tracks", p.count || 0))}</em></span>
      ${p.rules ? `<button type="button" class="mm-icon sm" data-rules title="${esc(t("smart.edit_rules"))}" aria-label="${esc(t("smart.edit_rules"))}">${icon("rule")}</button>` : ""}
      <label class="mm-switch" title="${esc(t("smart.show"))}"><input type="checkbox" data-show${p.hidden ? "" : " checked"} aria-label="${esc(t("smart.show"))}"><i></i></label></li>`).join("")}</ol>
    <div class="mm-ed-grid two">
      <label class="mm-ed-field"><span>${esc(t("smart.recent_days"))}</span><select class="mm-select" id="sm-days">${[7, 14, 30, 90, 365, 0].map((d) => `<option value="${d}"${Number(Cfg.get("recent_days", 30)) === d ? " selected" : ""}>${esc(d ? t("smart.days", { n: d }) : t("smart.all_time"))}</option>`).join("")}</select></label>
      <label class="mm-ed-field"><span>${esc(t("smart.limit"))}</span><select class="mm-select" id="sm-limit">${[25, 50, 100, 200, 500].map((d) => `<option value="${d}"${Number(Cfg.get("smart_limit", 50)) === d ? " selected" : ""}>${d}</option>`).join("")}</select></label>
    </div>
    <div class="acts"><button type="button" class="mm-btn ghost" id="sm-new">${icon("plus")}${esc(t("smart.new"))}</button><span class="mm-grow"></span><button type="button" class="mm-btn tint" data-cancel>${esc(t("done"))}</button></div>
  </div>`, (card) => {
    const box = card.querySelector("#sm-list");
    box.addEventListener("change", (e) => { const li = e.target.closest("li"); if (li && e.target.matches("[data-show]")) setPlaylistHidden(li.dataset.id, !e.target.checked); });
    box.addEventListener("click", (e) => { const li = e.target.closest("li"); if (li && e.target.closest("[data-rules]")) smartBuilder(all.find((p) => p.id === li.dataset.id)); });
    bindReorder(box, "li", async (from, to) => {
      const ids = $$("li", box).map((li) => li.dataset.id);
      const [m] = ids.splice(from, 1); ids.splice(to > from ? to - 1 : to, 0, m);
      const items = $$("li", box);
      ids.forEach((id, i) => { const li = items.find((x) => x.dataset.id === id); li.dataset.pos = i; box.appendChild(li); });
      await Cfg.set({ smart_order: ids });
      libraryChanged({ now: true, playlistsOnly: true });
    });
    card.querySelector("#sm-days").onchange = async (e) => { await Cfg.set({ recent_days: Number(e.target.value) }); libraryChanged({ now: true, playlistsOnly: true }); };
    card.querySelector("#sm-limit").onchange = async (e) => { await Cfg.set({ smart_limit: Number(e.target.value) }); libraryChanged({ now: true, playlistsOnly: true }); };
    card.querySelector("#sm-new").onclick = () => smartBuilder(null);
  });
}

/* ------------------------------------------------------------ конструктор правил */
function smartBuilder(pl) {
  const rules = (pl && pl.rules) || { match: "all", conditions: [{ field: "added_days", op: "within", value: 30 }], sort: "added", limit: 0 };
  const conds = rules.conditions.map((c) => Object.assign({}, c));
  const opLabel = (op) => t("rule.op." + op);
  const valueInput = (c, i) => {
    const kind = RULE_FIELDS[c.field];
    if (kind === "bool") return `<select class="mm-select" data-v="${i}"><option value="1"${c.value ? " selected" : ""}>${esc(t("rule.yes"))}</option><option value="0"${c.value ? "" : " selected"}>${esc(t("rule.no"))}</option></select>`;
    if (c.op === "between") { const v = Array.isArray(c.value) ? c.value : [0, 0]; return `<span class="mm-rule-between"><input class="mm-input" type="number" data-v="${i}" data-k="0" value="${esc(v[0])}"><span>—</span><input class="mm-input" type="number" data-v="${i}" data-k="1" value="${esc(v[1])}"></span>`; }
    const unit = kind === "days" ? t("rule.unit.days") : c.field === "duration" ? t("rule.unit.sec") : c.field === "bitrate" ? "kbps" : "";
    const listId = c.field === "genre" ? "mm-rule-genres" : c.field === "artist" ? "mm-rule-artists" : c.field === "source" ? "mm-rule-sources" : "";
    return `<span class="mm-rule-val"><input class="mm-input" data-v="${i}" ${kind === "text" ? "" : 'type="number"'} value="${esc(c.value ?? "")}" ${listId ? `list="${listId}"` : ""}>${unit ? `<em>${esc(unit)}</em>` : ""}</span>`;
  };
  const row = (c, i) => `<div class="mm-rule" data-i="${i}">
    <select class="mm-select" data-field="${i}">${Object.keys(RULE_FIELDS).map((f) => `<option value="${f}"${c.field === f ? " selected" : ""}>${esc(t("rule.f." + f))}</option>`).join("")}</select>
    <select class="mm-select" data-op="${i}">${RULE_OPS[RULE_FIELDS[c.field]].map((op) => `<option value="${op}"${c.op === op ? " selected" : ""}>${esc(opLabel(op))}</option>`).join("")}</select>
    ${valueInput(c, i)}
    <button type="button" class="mm-icon sm" data-del="${i}" aria-label="${esc(t("rule.remove"))}" title="${esc(t("rule.remove"))}">${icon("close")}</button></div>`;
  const artists = [...new Set(S.tracks.map((x) => x.artist).filter(Boolean))].slice(0, 400);
  const sources = [...new Set(S.tracks.map((x) => x.source).filter(Boolean))];
  modal(`<div class="mm-ed">
    <header class="mm-ed-head plain"><div><span class="mm-kind">${esc(t("smart.kind"))}</span><h3>${esc(pl ? pl.name : t("smart.create_title"))}</h3><p class="mm-sub">${esc(t("smart.builder_hint"))}</p></div></header>
    <label class="mm-ed-field wide"><span>${esc(t("pe.name"))}</span><input class="mm-input" id="sb-name" maxlength="120" value="${esc(pl ? pl.name : "")}" placeholder="${esc(t("smart.name_ph"))}"></label>
    <div class="mm-ed-row"><span>${esc(t("rule.match"))}</span><div class="mm-seg2" role="radiogroup" id="sb-match">${["all", "any"].map((m) => `<button type="button" role="radio" data-m="${m}" aria-checked="${rules.match === m}">${esc(t("rule.match." + m))}</button>`).join("")}</div></div>
    <div class="mm-rules" id="sb-rules"></div>
    <button type="button" class="mm-btn ghost sm" id="sb-add">${icon("plus")}${esc(t("rule.add"))}</button>
    <div class="mm-ed-grid two">
      <label class="mm-ed-field"><span>${esc(t("rule.sort"))}</span><select class="mm-select" id="sb-sort">${RULE_SORTS.map((k) => `<option value="${k}"${rules.sort === k ? " selected" : ""}>${esc(t("rule.sort." + k))}</option>`).join("")}</select></label>
      <label class="mm-ed-field"><span>${esc(t("rule.limit"))}</span><input class="mm-input" id="sb-limit" type="number" min="0" max="5000" value="${esc(rules.limit || "")}" placeholder="${esc(t("rule.no_limit"))}"></label>
    </div>
    <div class="mm-rule-preview" id="sb-preview"></div>
    <datalist id="mm-rule-artists">${artists.map((a) => `<option value="${esc(a)}">`).join("")}</datalist>
    <datalist id="mm-rule-sources">${sources.map((a) => `<option value="${esc(a)}">`).join("")}</datalist>
    <datalist id="mm-rule-genres">${(genreCache || []).map((g) => `<option value="${esc(g.name)}">`).join("")}</datalist>
    <div class="acts">${pl ? `<button type="button" class="mm-btn ghost" id="sb-del">${icon("trash")}${esc(t("playlist.delete"))}</button><span class="mm-grow"></span>` : ""}<button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" id="sb-save">${esc(t(pl ? "save" : "playlist.create"))}</button></div>
  </div>`, (card, close) => {
    const box = card.querySelector("#sb-rules");
    let match = rules.match;
    const draw = () => { box.innerHTML = conds.length ? conds.map(row).join("") : `<p class="mm-sub">${esc(t("rule.empty"))}</p>`; preview(); };
    const collect = () => ({ match, conditions: conds, sort: card.querySelector("#sb-sort").value, limit: Number(card.querySelector("#sb-limit").value) || 0 });
    const preview = debounce(async () => {
      const out = card.querySelector("#sb-preview");
      const res = await API.soft("playlist.preview", { rules: collect() }, null);
      if (!card.isConnected) return;
      out.innerHTML = !res ? `<p class="mm-sub">${esc(t("rule.invalid"))}</p>` : `<b>${esc(plural("count.tracks", res.count))}</b>${res.tracks.length ? `<div class="mm-rule-chips">${res.tracks.slice(0, 8).map((x) => `<span>${esc(x.title)}<em>${esc(x.artist || "")}</em></span>`).join("")}${res.count > 8 ? `<span class="more">+${res.count - 8}</span>` : ""}</div>` : ""}`;
    }, 250);
    draw();
    if (!genreCache) API.soft("library.genres", {}, []).then((g) => { genreCache = g || []; const dl = card.querySelector("#mm-rule-genres"); if (dl) dl.innerHTML = genreCache.map((x) => `<option value="${esc(x.name)}">`).join(""); });
    box.addEventListener("change", (e) => {
      const el = e.target;
      if (el.dataset.field !== undefined) { const c = conds[Number(el.dataset.field)]; c.field = el.value; const kind = RULE_FIELDS[c.field]; c.op = RULE_OPS[kind][0]; c.value = kind === "bool" ? true : kind === "text" ? "" : kind === "days" ? 30 : 0; draw(); return; }
      if (el.dataset.op !== undefined) { const c = conds[Number(el.dataset.op)]; c.op = el.value; if (c.op === "between") c.value = [0, 0]; else if (Array.isArray(c.value)) c.value = c.value[0]; draw(); return; }
    });
    box.addEventListener("input", (e) => {
      const el = e.target; if (el.dataset.v === undefined) return;
      const c = conds[Number(el.dataset.v)], kind = RULE_FIELDS[c.field];
      if (el.dataset.k !== undefined) { c.value = Array.isArray(c.value) ? c.value.slice() : [0, 0]; c.value[Number(el.dataset.k)] = Number(el.value) || 0; }
      else c.value = kind === "bool" ? el.value === "1" : kind === "text" ? el.value : Number(el.value) || 0;
      preview();
    });
    box.addEventListener("click", (e) => { const b = e.target.closest("[data-del]"); if (b) { conds.splice(Number(b.dataset.del), 1); draw(); } });
    card.querySelector("#sb-add").onclick = () => { conds.push({ field: "artist", op: "contains", value: "" }); draw(); const last = box.querySelector(".mm-rule:last-child input"); last && last.focus(); };
    card.querySelector("#sb-match").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (!b) return; match = b.dataset.m; $$("#sb-match [data-m]", card).forEach((x) => x.setAttribute("aria-checked", String(x === b))); preview(); });
    card.querySelector("#sb-sort").onchange = preview;
    card.querySelector("#sb-limit").oninput = preview;
    const del = card.querySelector("#sb-del");
    if (del) del.onclick = () => confirmDialog(t("playlist.delete"), t("playlist.delete_text", { name: pl.name }), async () => { await API.call("playlist.delete", { id: pl.id }); await libraryChanged({ now: true, playlistsOnly: true }); if (S.view === "playlist") go("playlists"); });
    card.querySelector("#sb-save").onclick = async () => {
      const name = card.querySelector("#sb-name").value.trim() || t("smart.default_name");
      try {
        const out = pl ? await API.call("playlist.update", { id: pl.id, name, rules: collect() }) : await API.call("playlist.create", { name, rules: collect() });
        close();
        toast(t(pl ? "toast.saved" : "toast.created", { name }));
        await libraryChanged({ now: true, playlistsOnly: true });
        if (out && out.id) go("playlist", { id: out.id }, { force: true });
      } catch (err) { toast(String(err.message || err)); }
    };
  });
}

/* ------------------------------------------------------------ экран «Плейлисты» и шапка плейлиста */
function featPlaylistActions() {
  return `<span class="mm-pl-actions"><button type="button" class="mm-btn ghost" id="pl-smart-new">${icon("rule")}${esc(t("smart.new"))}</button>
    <button type="button" class="mm-icon lg" id="pl-smart-manage" title="${esc(t("smart.manage"))}" aria-label="${esc(t("smart.manage"))}">${icon("eye")}</button>
    <label class="mm-icon lg" title="${esc(t("set.m3u_import"))}" aria-label="${esc(t("set.m3u_import"))}">${icon("upload")}<input type="file" accept=".m3u,.m3u8,audio/x-mpegurl,audio/mpegurl" id="pl-m3u" hidden></label></span>`;
}
function bindFeatPlaylists(el) {
  el.querySelector("#pl-smart-new").onclick = () => smartBuilder(null);
  el.querySelector("#pl-smart-manage").onclick = () => smartManager();
  el.querySelector("#pl-m3u").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await API.call("playlist.import", { text: await f.text(), name: f.name.replace(/\.m3u8?$/i, "") });
      toast(t("set.m3u_done", { n: r.matched, miss: r.missing.length }));
      await libraryChanged({ now: true, playlistsOnly: true });
      go("playlist", { id: r.playlist.id });
    } catch (err) { toast(String(err.message || err)); }
  });
  el.addEventListener("contextmenu", (e) => {
    const tl = e.target.closest("[data-p]"); if (!tl) return;
    e.preventDefault();
    playlistMenu(S.playlists[Number(tl.dataset.p)], { x: e.clientX, y: e.clientY });
  });
}
function playlistMenu(p, anchor) {
  if (!p) return;
  const builtin = !!p.smart, ruled = !!p.rules;
  openMenu([
    { icon: "play", label: t("play"), run: async () => { const full = await API.soft("playlist.get", { id: p.id }, null); if (full && full.tracks.length) Player.playList(full.tracks.map((x) => S.byId.get(x.id) || normTrack(x)), null, p.name); } },
    !builtin ? { icon: "edit", label: t("pe.title"), run: async () => editPlaylist(await API.call("playlist.get", { id: p.id }), rerender) } : null,
    ruled ? { icon: "rule", label: t("smart.edit_rules"), run: async () => smartBuilder(await API.call("playlist.get", { id: p.id })) } : null,
    builtin || ruled ? { icon: "eye-off", label: t("smart.hide"), run: () => setPlaylistHidden(p.id, true) } : null,
    { icon: "download", label: t("pe.export"), run: async () => { const out = await API.soft("playlist.export", { id: p.id }, null); if (out) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([out.text], { type: "audio/x-mpegurl" })); a.download = out.filename; document.body.appendChild(a); a.click(); a.remove(); } } },
    !builtin ? "-" : null,
    !builtin ? { icon: "trash", label: t("playlist.delete"), danger: true, run: () => confirmDialog(t("playlist.delete"), t("playlist.delete_text", { name: p.name }), async () => { await API.call("playlist.delete", { id: p.id }); await libraryChanged({ now: true, playlistsOnly: true }); rerender(); }) } : null,
  ].filter(Boolean), anchor);
}
function playlistHeadExtra(pl, builtin) {
  if (builtin) return `<button type="button" class="mm-icon lg" data-feat="hide" title="${esc(t("smart.hide"))}" aria-label="${esc(t("smart.hide"))}">${icon("eye-off")}</button>`;
  return `<button type="button" class="mm-icon lg" data-feat="edit" title="${esc(t("pe.title"))}" aria-label="${esc(t("pe.title"))}">${icon("edit")}</button>`
    + (pl.rules ? `<button type="button" class="mm-icon lg" data-feat="rules" title="${esc(t("smart.edit_rules"))}" aria-label="${esc(t("smart.edit_rules"))}">${icon("rule")}</button>` : "")
    + `<button type="button" class="mm-icon lg" data-feat="more" title="${esc(t("more"))}" aria-label="${esc(t("more"))}">${icon("more")}</button>`;
}
function bindPlaylistHead(el, pl, builtin) {
  el.querySelector(".mm-head").addEventListener("click", (e) => {
    const b = e.target.closest("[data-feat]"); if (!b) return;
    const act = b.dataset.feat;
    if (act === "hide") setPlaylistHidden(pl.id, true).then(() => { toast(t("smart.hidden_toast", { name: pl.name })); go("playlists"); });
    else if (act === "edit") editPlaylist(pl, rerender);
    else if (act === "rules") smartBuilder(pl);
    else if (act === "more") playlistMenu(Object.assign({}, pl, { smart: builtin }), b);
  });
}
