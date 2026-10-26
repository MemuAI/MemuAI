

const ED_FIELDS = [["title", "col.title"], ["artist", "ed.artist"], ["album", "col.album"], ["album_artist", "ed.album_artist"],
  ["year", "ed.year"], ["track_no", "ed.track_no"], ["genre", "ed.genre"]];
let genreCache = null;

function lrcStamp(sec) { const m = Math.floor(sec / 60), s = sec - m * 60; return `[${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}]`; }

function lrcRow(row) { return lrcStamp(row[0]) + (row[2] ? row[2].map(([sec, w]) => lrcStamp(sec).replace("[", "<").replace("]", ">") + w).join("") + (row[3] ? lrcStamp(row[3]).replace("[", "<").replace("]", ">") : "") : row[1]); }

function lrcText(rows) {
  return rows.map((row, i) => {
    const next = rows[i + 1], pause = row[3] && !row[2] && String(row[1] || "").trim() && (!next || (next[0] > row[3] + .05 && String(next[1] || "").trim()));
    return lrcRow(row) + (pause ? "\n" + lrcStamp(row[3]) : "");
  }).join("\n");
}

function insertLineEnd(area, sec) {
  const v = area.value, pos = area.selectionStart;
  const start = v.lastIndexOf("\n", pos - 1) + 1, endAt = v.indexOf("\n", pos) < 0 ? v.length : v.indexOf("\n", pos);
  const line = v.slice(start, endAt), stamp = lrcStamp(sec);
  let caret;
  if (/^\s*\[\d+:\d+(?:\.\d+)?\]\s*$/.test(line)) { area.value = v.slice(0, start) + stamp + v.slice(endAt); caret = start + stamp.length; }
  else { area.value = v.slice(0, endAt) + "\n" + stamp + v.slice(endAt); caret = endAt + 1 + stamp.length; }
  const next = area.value.indexOf("\n", caret);
  area.focus(); area.selectionStart = area.selectionEnd = next < 0 ? area.value.length : next + 1;
  area.dispatchEvent(new Event("input", { bubbles: true }));
}
function readImageFile(file) {
  return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.onerror = () => reject(r.error); r.readAsDataURL(file); });
}

async function clipboardImage() {
  if (!navigator.clipboard || !navigator.clipboard.read) return null;
  try {
    for (const item of await navigator.clipboard.read()) {
      const type = item.types.find((x) => x.startsWith("image/"));
      if (type) return readImageFile(await item.getType(type));
    }
  } catch (e) {  }
  return null;
}

function mergeTrack(updated) {
  if (!updated || !updated.id) return;
  normTrack(updated);
  [S.byId.get(updated.id), ...Player.st.queue.filter((q) => q.id === updated.id)].forEach((x) => { if (x) Object.assign(x, updated); });
  const cur = Player.current();
  if (cur && cur.id === updated.id) { updateBar(); applyPalette(cur); }
}

function editTracks(list) {
  list = [].concat(list).filter(Boolean);
  if (!list.length) return;
  const bulk = list.length > 1;
  const one = list[0];
  const common = (key) => { const v = String(list[0][key] ?? ""); return list.every((x) => String(x[key] ?? "") === v) ? v : null; };
  const tabs = bulk ? ["tags", "cover"] : ["tags", "cover", "lyrics", "file"];
  const field = ([key, label]) => {
    const v = common(key);
    const val = v === null ? "" : key === "track_no" && v === "0" ? "" : v;
    return `<label class="mm-ed-field ${["title", "artist", "album", "genre"].includes(key) ? "wide" : key === "album_artist" ? "half" : "quarter"}"><span>${esc(t(label))}</span>
      <input class="mm-input" data-f="${key}" value="${esc(val)}" ${v === null ? `placeholder="${esc(t("ed.mixed"))}"` : ""} ${key === "year" || key === "track_no" ? 'inputmode="numeric" maxlength="10"' : 'maxlength="300"'} ${key === "genre" ? 'list="mm-genres"' : ""} spellcheck="false"></label>`;
  };
  modal(`<div class="mm-ed">
    <header class="mm-ed-head"><span class="mm-ed-art" id="ed-art">${bulk ? `<span class="mm-collage">${list.slice(0, 4).map((x) => artImg(x)).join("")}</span>` : artImg(one, "album")}</span>
      <div><span class="mm-kind">${esc(bulk ? t("ed.bulk_kind", { n: list.length }) : t("ed.kind"))}</span><h3>${esc(bulk ? t("ed.bulk_title", { n: list.length }) : one.title)}</h3><p class="mm-sub">${esc(bulk ? t("ed.bulk_hint") : one.artist || "")}</p></div></header>
    <div class="mm-tabs mm-ed-tabs" role="tablist">${tabs.map((k, i) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${i === 0}">${esc(t("ed.tab." + k))}</button>`).join("")}</div>
    <section class="mm-ed-pane" data-pane="tags"><div class="mm-ed-grid">${ED_FIELDS.filter(([k]) => !bulk || (k !== "title" && k !== "track_no")).map(field).join("")}</div>
      <datalist id="mm-genres"></datalist>
      <label class="mm-ed-check"><span class="mm-switch"><input type="checkbox" id="ed-write"${Cfg.get("write_tags", true) ? " checked" : ""}><i></i></span><span>${esc(t("ed.write_tags"))}</span></label></section>
    <section class="mm-ed-pane" data-pane="cover" hidden>
      <div class="mm-ed-cover-acts">
        <label class="mm-btn ghost sm">${icon("upload")}${esc(t("ed.cover_file"))}<input type="file" accept="image/*" id="ed-file" hidden></label>
        <button type="button" class="mm-btn ghost sm" id="ed-paste">${icon("copy")}${esc(t("ed.cover_paste"))}</button>
        ${!bulk ? `<button type="button" class="mm-btn ghost sm" id="ed-frame">${icon("image")}${esc(t("ed.cover_frame"))}</button>` : ""}
        <button type="button" class="mm-btn ghost sm" id="ed-clear">${icon("trash")}${esc(t("ed.cover_clear"))}</button>
      </div>
      <div class="mm-ed-row"><input class="mm-input" id="ed-url" type="url" placeholder="${esc(t("ed.cover_url"))}" spellcheck="false"><button type="button" class="mm-btn ghost sm" id="ed-url-go">${esc(t("ed.apply"))}</button></div>
      <div class="mm-ed-row"><input class="mm-input" id="ed-q" type="search" value="${esc([common("artist"), bulk ? common("album") : one.title].filter(Boolean).join(" "))}" placeholder="${esc(t("ed.cover_search"))}"><button type="button" class="mm-btn tint sm" id="ed-q-go">${icon("search")}${esc(t("ed.find"))}</button></div>
      <div class="mm-ed-found" id="ed-found"><p class="mm-sub">${esc(t("ed.cover_hint"))}</p></div>
    </section>
    ${bulk ? "" : `<section class="mm-ed-pane" data-pane="lyrics" hidden>
      <div class="mm-ed-row"><span class="mm-sub" id="ed-ly-state">${esc(t("ed.ly_loading"))}</span><span class="mm-grow"></span>
        <button type="button" class="mm-btn ghost sm" id="ed-stamp" title="${esc(t("ed.ly_stamp_hint"))}">${icon("lyrics")}${esc(t("ed.ly_stamp"))}</button>
        <button type="button" class="mm-btn ghost sm" id="ed-ly-end" title="${esc(t("lyrics.end_hint"))}">${icon("more")}${esc(t("lyrics.end"))}</button>
        <button type="button" class="mm-btn ghost sm" id="ed-ly-find">${icon("search")}${esc(t("ed.ly_find"))}</button></div>
      <textarea class="mm-input mm-ed-lyrics" id="ed-lyrics" spellcheck="false" placeholder="${esc(t("ed.ly_ph"))}"></textarea></section>
    <section class="mm-ed-pane" data-pane="file" hidden><dl class="mm-ed-info" id="ed-info"></dl></section>`}
    <div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" id="ed-save">${esc(t("save"))}</button></div>
  </div>`, (card, close) => {
    const pane = (k) => card.querySelector(`[data-pane="${k}"]`);
    let lyricsLoaded = null, coverChanged = false;
    card.querySelector(".mm-ed-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("[data-tab]"); if (!b) return;
      $$("[data-tab]", card).forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      $$("[data-pane]", card).forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
      if (b.dataset.tab === "lyrics" && lyricsLoaded === null) loadLyrics();
      if (b.dataset.tab === "file") fileInfo();
    });
    (genreCache ? Promise.resolve(genreCache) : API.soft("library.genres", {}, [])).then((g) => {
      genreCache = g || [];
      card.querySelector("#mm-genres").innerHTML = genreCache.map((x) => `<option value="${esc(x.name)}">`).join("");
    });

    const showArt = (track) => { if (!bulk) card.querySelector("#ed-art").innerHTML = artImg(track, "album"); };
    const setCover = async (args) => {
      const btns = $$(".mm-ed-cover-acts button, .mm-ed-row button", pane("cover"));
      btns.forEach((b) => { b.disabled = true; });
      try {
        let first = await API.call("track.cover", Object.assign({ id: one.id, write_tags: card.querySelector("#ed-write").checked }, args));
        if (bulk) {
          const res = args.clear ? await Promise.all(list.slice(1).map((x) => API.call("track.cover", { id: x.id, clear: true })))
            : (await API.call("library.update_many", { ids: list.map((x) => x.id), changes: { cover_from: one.id, write_tags: card.querySelector("#ed-write").checked } })).tracks;
          res.forEach(mergeTrack);
        }
        mergeTrack(first); showArt(first); coverChanged = true;
        toast(t(args.clear ? "ed.cover_removed" : "ed.cover_set"));
      } catch (err) { toast(String(err.message || err)); }
      btns.forEach((b) => { b.disabled = false; });
    };
    card.querySelector("#ed-file").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) setCover({ data: await readImageFile(f) }); });
    card.querySelector("#ed-paste").onclick = async () => { const data = await clipboardImage(); if (data) setCover({ data }); else toast(t("ed.paste_hint")); };
    card.addEventListener("paste", async (e) => {
      const item = [...(e.clipboardData && e.clipboardData.items) || []].find((x) => x.type.startsWith("image/"));
      if (!item) return;
      e.preventDefault(); e.stopPropagation();
      setCover({ data: await readImageFile(item.getAsFile()) });
    });
    card.querySelector("#ed-clear").onclick = () => setCover({ clear: true });
    const frame = card.querySelector("#ed-frame");
    if (frame) frame.onclick = () => { const cur = Player.current(); setCover({ video: cur && cur.id === one.id ? Math.floor(Player.times().cur) : 30 }); };
    card.querySelector("#ed-url-go").onclick = () => { const url = card.querySelector("#ed-url").value.trim(); if (url) setCover({ url }); };
    const search = async () => {
      const q = card.querySelector("#ed-q").value.trim(); if (!q) return;
      const box = card.querySelector("#ed-found");
      box.innerHTML = `<div class="mm-ed-found-grid">${Array.from({ length: 6 }, () => '<span class="mm-skel"></span>').join("")}</div>`;
      const found = await API.soft("cover.search", { q, limit: 12 }, []);
      if (!card.isConnected) return;
      box.innerHTML = found && found.length ? `<div class="mm-ed-found-grid">${found.map((f, i) => `<button type="button" data-k="${i}" title="${esc([f.artist, f.album || f.title].filter(Boolean).join(" — "))}"><img alt="" src="${esc(f.thumb)}" loading="lazy"><span>${esc(f.album || f.title)}</span><em>${esc(f.source === "itunes" ? "Apple" : "Deezer")}</em></button>`).join("")}</div>`
        : `<p class="mm-sub">${esc(t("ed.cover_none"))}</p>`;
      box.onclick = (e) => { const b = e.target.closest("[data-k]"); if (b) setCover({ url: found[Number(b.dataset.k)].url }); };
    };
    card.querySelector("#ed-q-go").onclick = search;
    card.querySelector("#ed-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); search(); } });

    async function loadLyrics(force) {
      const state = card.querySelector("#ed-ly-state"), area = card.querySelector("#ed-lyrics");
      state.textContent = t("ed.ly_loading");
      const ly = await API.soft("lyrics.get", { id: one.id, force: !!force }, null);
      if (!card.isConnected) return;
      lyricsLoaded = ly && ly.synced ? lrcText(ly.synced) : (ly && ly.plain) || "";
      if (!area.dataset.dirty || force) { area.value = lyricsLoaded; delete area.dataset.dirty; }
      state.textContent = (!lyricsLoaded ? t("ed.ly_none") : ly.synced ? t("ed.ly_synced") : t("ed.ly_plain")) + (lyricsLoaded && ly.source_name ? " · " + (ly.source === "manual" ? t("lyrics.mine") : ly.source_name) : "");
    }
    const stamp = card.querySelector("#ed-stamp");
    if (stamp) {
      card.querySelector("#ed-lyrics").addEventListener("input", (e) => { e.target.dataset.dirty = "1"; if (lyricsLoaded === null) lyricsLoaded = ""; });
      stamp.onclick = () => {
        const area = card.querySelector("#ed-lyrics"), cur = Player.current();
        const sec = cur && cur.id === one.id ? Player.times().cur : 0;
        const pos = area.selectionStart, start = area.value.lastIndexOf("\n", pos - 1) + 1;
        const line = area.value.slice(start).split("\n")[0].replace(/^\[\d+:\d+(?:\.\d+)?\]/, "");
        const end = start + area.value.slice(start).split("\n")[0].length;
        area.value = area.value.slice(0, start) + lrcStamp(sec) + line + area.value.slice(end);
        const next = area.value.indexOf("\n", start);
        area.focus(); area.selectionStart = area.selectionEnd = next < 0 ? area.value.length : next + 1;
      };
      card.querySelector("#ed-ly-end").onclick = () => { const cur = Player.current(); insertLineEnd(card.querySelector("#ed-lyrics"), cur && cur.id === one.id ? Player.times().cur : 0); };
      card.querySelector("#ed-ly-find").onclick = () => loadLyrics(true);
    }

    function fileInfo() {
      const info = card.querySelector("#ed-info"); if (!info || info.dataset.done) return;
      info.dataset.done = "1";
      const kb = (one.size || 0) / 1024;
      const rows = [["ed.info.format", (one.file_ext || "").toUpperCase()], ["ed.info.bitrate", one.bitrate ? one.bitrate + " kbps" : "—"],
        ["ed.info.size", kb > 1024 ? (kb / 1024).toFixed(1) + " MB" : Math.round(kb) + " KB"], ["ed.info.duration", fmtTime(one.duration)],
        ["ed.info.source", sourceName(one.source || "local")], ["ed.info.plays", String(one.plays || 0)],
        ["ed.info.loudness", typeof one.lufs === "number" ? `${one.lufs.toFixed(1)} LUFS · ${one.gain > 0 ? "+" : ""}${(one.gain || 0).toFixed(1)} dB` : t("ed.info.not_measured")],
        ["ed.info.added", one.added_at ? new Date(one.added_at).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU") : "—"]];
      info.innerHTML = rows.map(([k, v]) => `<dt>${esc(t(k))}</dt><dd>${esc(v)}</dd>`).join("")
        + `<dt>${esc(t("ed.info.file"))}</dt><dd><button type="button" class="mm-link" id="ed-reveal">${icon("folder")}${esc(t("menu.reveal"))}</button></dd>`
        + (one.source_url ? `<dt>${esc(t("ed.info.link"))}</dt><dd class="mm-ed-link">${esc(one.source_url)}</dd>` : "");
      info.querySelector("#ed-reveal").onclick = () => revealTrack(one);
      if (typeof one.lufs !== "number") API.soft("library.analyze", { ids: [one.id] }, null);
    }

    card.querySelector("#ed-save").onclick = async () => {
      const save = card.querySelector("#ed-save"); save.disabled = true;
      const write = card.querySelector("#ed-write").checked;
      const changes = {};
      $$("[data-f]", card).forEach((input) => {
        const key = input.dataset.f, v = input.value.trim(), was = common(key);
        if (bulk ? v !== "" && v !== was : v !== String(was === "0" && key === "track_no" ? "" : was ?? "")) changes[key] = key === "track_no" ? Number(v) || 0 : v;
      });
      try {
        if (Object.keys(changes).length) {
          if (bulk) (await API.call("library.update_many", { ids: list.map((x) => x.id), changes: Object.assign({ write_tags: write }, changes) })).tracks.forEach(mergeTrack);
          else mergeTrack(await API.call("library.update", Object.assign({ id: one.id, write_tags: write }, changes)));
        }
        const area = card.querySelector("#ed-lyrics");

        const lyricsChanged = !!(area && lyricsLoaded !== null && area.value.trim() !== lyricsLoaded.trim());
        if (lyricsChanged) {
          await API.call("lyrics.set", { id: one.id, text: area.value });
          if (NP.reloadLyrics) NP.reloadLyrics(one.id);
        }
        close();
        if (Object.keys(changes).length || coverChanged) { toast(t("ed.saved")); genreCache = null; libraryChanged(); }
        else if (lyricsChanged) toast(t("lyrics.saved"));
      } catch (err) { save.disabled = false; toast(String(err.message || err)); }
    };
  });
}

function editPlaylist(pl, onDone) {
  const rules = !!pl.rules;
  modal(`<div class="mm-ed">
    <header class="mm-ed-head"><span class="mm-ed-art" id="pe-art">${collage(pl)}</span>
      <div><span class="mm-kind">${esc(t(rules ? "smart.kind" : "kind.playlist"))}</span><h3>${esc(t("pe.title"))}</h3><p class="mm-sub">${esc(plural("count.tracks", pl.count || (pl.tracks || []).length))}</p></div></header>
    <label class="mm-ed-field wide"><span>${esc(t("pe.name"))}</span><input class="mm-input" id="pe-name" maxlength="120" value="${esc(pl.name)}"></label>
    <label class="mm-ed-field wide"><span>${esc(t("pe.desc"))}</span><textarea class="mm-input mm-ed-desc" id="pe-desc" maxlength="500" placeholder="${esc(t("pe.desc_ph"))}">${esc(pl.description || "")}</textarea></label>
    <div class="mm-ed-cover-acts">
      <label class="mm-btn ghost sm">${icon("upload")}${esc(t("ed.cover_file"))}<input type="file" accept="image/*" id="pe-file" hidden></label>
      <button type="button" class="mm-btn ghost sm" id="pe-paste">${icon("copy")}${esc(t("ed.cover_paste"))}</button>
      ${pl.own_cover ? `<button type="button" class="mm-btn ghost sm" id="pe-clear">${icon("trash")}${esc(t("pe.cover_auto"))}</button>` : ""}
    </div>
    <div class="mm-ed-row"><input class="mm-input" id="pe-url" type="url" placeholder="${esc(t("ed.cover_url"))}" spellcheck="false"><button type="button" class="mm-btn ghost sm" id="pe-url-go">${esc(t("ed.apply"))}</button></div>
    <div class="mm-ed-cover-acts">
      ${rules ? `<button type="button" class="mm-btn ghost sm" id="pe-rules">${icon("rule")}${esc(t("smart.edit_rules"))}</button>` : ""}
      <button type="button" class="mm-btn ghost sm" id="pe-m3u">${icon("download")}${esc(t("pe.export"))}</button>
      <button type="button" class="mm-btn ghost sm" id="pe-m3u-save">${icon("folder")}${esc(t("pe.export_folder"))}</button>
    </div>
    <div class="acts"><button type="button" class="mm-btn ghost" data-cancel>${esc(t("cancel"))}</button><button type="button" class="mm-btn tint" id="pe-save">${esc(t("save"))}</button></div>
  </div>`, (card, close) => {
    const cover = async (args) => {
      try {
        const out = await API.call("playlist.cover", Object.assign({ id: pl.id }, args));
        Object.assign(pl, { cover: out.cover, own_cover: out.own_cover });
        card.querySelector("#pe-art").innerHTML = collage(pl);
        toast(t(args.clear ? "ed.cover_removed" : "ed.cover_set"));
        libraryChanged({ playlistsOnly: true, quiet: true });
      } catch (err) { toast(String(err.message || err)); }
    };
    card.querySelector("#pe-file").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) cover({ data: await readImageFile(f) }); });
    card.querySelector("#pe-paste").onclick = async () => { const data = await clipboardImage(); if (data) cover({ data }); else toast(t("ed.paste_hint")); };
    card.addEventListener("paste", async (e) => {
      const item = [...(e.clipboardData && e.clipboardData.items) || []].find((x) => x.type.startsWith("image/"));
      if (item) { e.preventDefault(); cover({ data: await readImageFile(item.getAsFile()) }); }
    });
    card.querySelector("#pe-url-go").onclick = () => { const url = card.querySelector("#pe-url").value.trim(); if (url) cover({ url }); };
    const clear = card.querySelector("#pe-clear"); if (clear) clear.onclick = () => cover({ clear: true });
    const rulesBtn = card.querySelector("#pe-rules"); if (rulesBtn) rulesBtn.onclick = () => smartBuilder(pl);
    card.querySelector("#pe-m3u").onclick = async () => {
      const out = await API.soft("playlist.export", { id: pl.id }, null); if (!out) { toast(t("error.title")); return; }
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([out.text], { type: "audio/x-mpegurl" })); a.download = out.filename;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    };
    card.querySelector("#pe-m3u-save").onclick = async () => {
      const dir = Cfg.get("download_dir") || Cfg.get("default_download_dir") || "";
      try { const out = await API.call("playlist.export", { id: pl.id, path: dir }); toast(t("pe.exported", { path: out.path })); } catch (err) { toast(String(err.message || err)); }
    };
    card.querySelector("#pe-save").onclick = async () => {
      const name = card.querySelector("#pe-name").value.trim(), description = card.querySelector("#pe-desc").value.trim();
      if (!name) { card.querySelector("#pe-name").focus(); return; }
      try { await API.call("playlist.update", { id: pl.id, name, description }); close(); toast(t("toast.saved")); await libraryChanged({ now: true, playlistsOnly: true }); onDone && onDone(); }
      catch (err) { toast(String(err.message || err)); }
    };
  });
}
