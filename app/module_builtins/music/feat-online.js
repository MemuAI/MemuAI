/* MemuMusic · feat-online.js — онлайн-поиск (раунд 41, r41b-online): откуда трек и что это.
 * Результаты: значок и имя площадки, канал (✓), длительность, просмотры, метки «Офиц. аудио / Клип / Live / Кавер /
 * Ремикс / Превью», «В библиотеке». Переключатель площадки (YouTube Music / YouTube / SoundCloud) и «Только оригиналы».
 * Клик по строке — лист «Информация о треке»: подробности грузятся лениво (online.details, кэш на сервере и здесь),
 * похожие треки и текст — по требованию. Действия: Слушать (скачает и включит), Скачать, В очередь, В плейлист,
 * Найти текст, Копировать ссылку, Открыть в браузере. */

const ONL_SOURCES = ["ytmusic", "youtube", "soundcloud"];
const ONL = { details: new Map(), related: new Map(), lyrics: new Map(), queued: new Map(), waiting: new Map(), timer: 0, sheet: null };
const ONL_TAG_TONE = { official_audio: "ok", official_video: "ok", live: "warm", cover: "warm", remix: "cool", slowed: "cool", sped_up: "cool", karaoke: "warm", preview: "warn" };
const ONL_NOT_ORIGINAL = ["cover", "remix", "slowed", "sped_up", "karaoke", "instrumental"];

function onlCount(n) {
  n = Number(n) || 0;
  if (!n) return "";
  try { return new Intl.NumberFormat(Memu.lang === "en" ? "en-US" : "ru-RU", { notation: "compact", maximumFractionDigits: 1 }).format(n); } catch (e) { return String(n); }
}
function onlDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 4 ? iso + "-01-01" : iso);
  if (isNaN(d)) return iso;
  return iso.length === 4 ? iso : d.toLocaleDateString(Memu.lang === "en" ? "en-US" : "ru-RU", { day: "numeric", month: "long", year: "numeric" });
}
function onlTags(r, max) {
  const tags = (r.tags || []).slice(0, max || 3).map((k) => `<em class="mm-otag" data-tone="${ONL_TAG_TONE[k] || ""}">${esc(t("onl.tag." + k))}</em>`);
  if (r.explicit) tags.push(`<em class="mm-otag mm-otag-e" title="${esc(t("onl.explicit"))}">E</em>`);
  return tags.join("");
}
function onlThumb(r, cls) {
  const fb = coverArt(r.url || r.title, r.title);
  return `<span class="mm-art ${cls || ""}"${r.source === "ytmusic" || r.source === "soundcloud" ? ' data-square=""' : ""}><img alt="" loading="lazy" decoding="async" src="${esc(r.thumb || fb)}" data-fb="${esc(fb)}"></span>`;
}
function onlLibTrack(r) { return r.in_library ? S.byId.get(r.in_library) || null : null; }

/** Строка результата: обложка · название + метки · площадка, канал, длительность, просмотры · действия. */
function onlineRow(r, i) {
  const lib = onlLibTrack(r), queued = ONL.queued.has(r.url);
  const meta = [r.artist && r.artist !== r.channel ? esc(r.artist) : "", r.album ? esc(r.album) : ""].filter(Boolean).join(" · ");
  const chan = r.channel ? `<span class="mm-ochan">${esc(r.channel)}${r.verified ? `<i class="mm-overified" title="${esc(t("onl.verified"))}">${icon("check")}</i>` : ""}</span>` : "";
  const stats = [r.duration ? fmtTime(r.duration) : "", r.views ? t(r.source === "ytmusic" ? "onl.plays" : "onl.views", { n: onlCount(r.views) }) : "", r.published ? r.published.slice(0, 4) : ""].filter(Boolean);
  return `<div class="mm-orow" data-k="${i}" role="button" tabindex="0" aria-label="${esc(t("onl.info_for", { title: r.title }))}">${onlThumb(r)}
    <div class="mm-otxt"><div class="mm-otitle"><b>${esc(r.title)}</b>${onlTags(r)}</div>
      ${meta ? `<span class="mm-ometa">${meta}</span>` : ""}
      <span class="mm-osrc">${sourceIcon(r.source, "mm-srcdot sm")}<span>${esc(sourceName(r.source))}</span>${chan}${stats.map((s) => `<span>${esc(s)}</span>`).join("")}</span></div>
    ${lib ? `<span class="mm-pill mm-oinlib">${icon("check")}${esc(t("onl.in_library"))}</span>` : ""}
    <div class="mm-oacts">${lib ? `<button type="button" class="mm-btn ghost sm" data-play>${icon("play")}${esc(t("play"))}</button>`
      : `<button type="button" class="mm-btn ghost sm" data-dl${queued ? " disabled" : ""}>${icon(queued ? "check" : "download")}${esc(t(queued ? "download.queued" : "search.download"))}</button>`}
      <button type="button" class="mm-icon sm" data-more aria-label="${esc(t("more"))}">${icon("more")}</button></div></div>`;
}

/** Онлайн-поиск в box: переключатель площадки, «Только оригиналы», результаты. opts.quality — качество загрузки. */
async function onlineSearch(box, query, opts) {
  opts = opts || {};
  const seq = (box._onlSeq = (box._onlSeq || 0) + 1);   // быстрый ввод: ответ старого запроса не перерисует новый
  const src = ONL_SOURCES.includes(S.prefs.onlineSource) ? S.prefs.onlineSource : "ytmusic";
  const originals = !!S.prefs.onlineOriginals;
  box.innerHTML = `<div class="mm-otools"><div class="mm-oseg" role="tablist" aria-label="${esc(t("onl.source"))}">${ONL_SOURCES.map((s) => `<button type="button" role="tab" data-src="${s}" aria-selected="${s === src}">${sourceIcon(s, "mm-srcdot sm")}<span>${esc(sourceName(s))}</span></button>`).join("")}</div>
      <button type="button" class="mm-chip" data-orig aria-pressed="${originals}" title="${esc(t("onl.originals_hint"))}">${icon("sparkle")}${esc(t("onl.originals"))}</button></div>
    <div class="mm-online" aria-busy="true">${Array.from({ length: 4 }, () => '<div class="mm-skel mm-oskel"></div>').join("")}</div>`;
  const list = box.querySelector(".mm-online");
  box.onclick = null; box.onkeydown = null;
  box.querySelector(".mm-oseg").onclick = (e) => {
    const b = e.target.closest("[data-src]"); if (!b || b.dataset.src === src) return;
    S.prefs.onlineSource = b.dataset.src; savePrefs(); onlineSearch(box, query, opts);
  };
  box.querySelector("[data-orig]").onclick = () => { S.prefs.onlineOriginals = !originals; savePrefs(); onlineSearch(box, query, opts); };
  const res = await API.soft("online.search", { q: query, limit: 12, source: src }, null);
  if (!box.isConnected || box._onlSeq !== seq) return;
  list.removeAttribute("aria-busy");
  const all = (res && res.results) || [];
  const found = originals ? all.filter((r) => !(r.tags || []).some((k) => ONL_NOT_ORIGINAL.includes(k))) : all;
  if (!found.length) {
    list.innerHTML = `<p class="mm-sub mm-oempty">${esc(!res || (res.error && !all.length) ? t("search.online_error") : all.length ? t("onl.only_versions", { n: all.length }) : t("empty.nothing"))}</p>`;
    return;
  }
  list.innerHTML = found.map(onlineRow).join("");
  $$("img[data-fb]", list).forEach((img) => { img.onerror = () => { img.onerror = null; img.src = img.dataset.fb; }; });
  const pick = (e) => { const row = e.target.closest("[data-k]"); return row ? [row, found[Number(row.dataset.k)]] : [null, null]; };
  list.onclick = (e) => {
    const [row, r] = pick(e); if (!r) return;
    if (e.target.closest("[data-dl]")) { onlineDownload(r, opts, () => refreshRow(row, r)); return; }
    if (e.target.closest("[data-play]")) { const x = onlLibTrack(r); if (x) playFrom([x], x, t("search.online")); return; }
    if (e.target.closest("[data-more]")) { onlineMenu(r, e.target.closest("[data-more]"), opts); return; }
    onlineSheet(r, opts);
  };
  list.onkeydown = (e) => { if ((e.key === "Enter" || e.key === " ") && e.target.matches(".mm-orow")) { e.preventDefault(); const [, r] = pick(e); r && onlineSheet(r, opts); } };
  list.oncontextmenu = (e) => { const [, r] = pick(e); if (!r) return; e.preventDefault(); onlineMenu(r, { x: e.clientX, y: e.clientY }, opts); };
  function refreshRow(row, r) { if (!row.isConnected) return; const k = row.dataset.k; row.outerHTML = onlineRow(r, Number(k)); }
}

/* ------------------------------------------------------------ действия (общие для строки и листа) */
async function onlineDownload(r, opts, then, playlistId) {
  if (ONL.queued.has(r.url)) return ONL.queued.get(r.url);
  try {
    const job = await API.call(playlistId ? "playlist.save_online" : "download.start", { url: r.url, quality: (opts && opts.quality) || "", playlist_id: playlistId || "" });
    ONL.queued.set(r.url, job && job.job_id);
    toast(t("search.downloading", { title: r.title }));
    pollJobs(); then && then();
    return job && job.job_id;
  } catch (err) { toast(String(err.message || err)); return null; }
}
/** Сделать что-то с треком, когда загрузка закончится (опрос — pollJobs; здесь только чтение S.jobs, пока есть ожидания). */
function afterDownload(jobId, fn) {
  if (!jobId) return;
  ONL.waiting.set(jobId, fn);
  const tick = () => {
    ONL.timer = 0;
    for (const [id, run] of ONL.waiting) {
      const j = S.jobs.find((x) => x.job_id === id);
      if (!j) continue;
      if (j.status === "done") { const x = (j.track_ids || []).map((tid) => S.byId.get(tid)).find(Boolean); if (x) { ONL.waiting.delete(id); run(x); } }
      else if (j.status === "error" || j.status === "cancelled") ONL.waiting.delete(id);
    }
    if (ONL.waiting.size) ONL.timer = setTimeout(tick, 1500);
  };
  if (!ONL.timer) ONL.timer = setTimeout(tick, 1500);
}
async function onlineListen(r, opts) {
  const x = onlLibTrack(r);
  if (x) { playFrom([x], x, t("search.online")); return; }
  const id = await onlineDownload(r, opts);
  if (id) { toast(t("onl.listen_soon", { title: r.title })); afterDownload(id, (tr) => Player.playList([tr], tr.id, t("search.online"))); }
}
async function onlineQueue(r, opts) {
  const x = onlLibTrack(r);
  if (x) { Player.enqueue(x); toast(t("toast.queued", { title: x.title })); return; }
  const id = await onlineDownload(r, opts);
  if (id) { toast(t("onl.queue_soon", { title: r.title })); afterDownload(id, (tr) => { Player.enqueue(tr); toast(t("toast.queued", { title: tr.title })); }); }
}
function onlineOpen(r) { Memu.open({ url: r.url }).catch(() => toast(r.url)); }
function onlineMenu(r, anchor, opts) {
  openMenu([
    { icon: "play", label: t("play"), run: () => onlineListen(r, opts) },
    { icon: "queue", label: t("menu.add_queue"), run: () => onlineQueue(r, opts) },
    { icon: "playlist", label: t("search.to_playlist"), run: () => onlineToPlaylist(r, anchor) },
    "-",
    { icon: "lyrics", label: t("onl.find_lyrics"), run: () => onlineSheet(r, opts, "lyrics") },
    { icon: "radio", label: t("onl.similar"), run: () => onlineSheet(r, opts, "similar") },
    { icon: "copy", label: t("onl.copy_link"), run: () => copyText(r.url) },
    { icon: "link", label: t("onl.open", { source: sourceName(r.source) }), run: () => onlineOpen(r) },
  ], anchor);
}

/* ------------------------------------------------------------ лист «Информация о треке» */
function onlineSheet(r, opts, focus) {
  closeOverlays();
  let host = ONL.sheet;
  if (!host) {
    host = ONL.sheet = document.createElement("div");
    host.className = "mm-osheet"; host.hidden = true;
    host.innerHTML = `<div class="mm-osheet-scrim" data-close></div><aside class="mm-osheet-panel" role="dialog" aria-modal="true" aria-labelledby="osh-title"><div class="mm-osheet-bg"><img alt=""></div><div class="mm-osheet-body"></div></aside>`;
    $(".mm").appendChild(host);
    host.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeSheet(); });
    const sc = host.querySelector(".mm-osheet-body");
    sc.addEventListener("scroll", () => host.classList.toggle("scrolled", sc.scrollTop > 8), { passive: true });
    document.addEventListener("keydown", (e) => {
      if (host.hidden || e.key !== "Escape" || !$("#menu").hidden) return;
      e.preventDefault(); e.stopImmediatePropagation(); closeSheet();
    }, true);
  }
  const back = document.activeElement;
  function closeSheet() { host.classList.remove("open"); host.hidden = true; ONL.current = null; if (back && back.isConnected) back.focus(); }
  ONL.current = r.url;
  host.hidden = false;
  requestAnimationFrame(() => host.classList.add("open"));
  const bg = host.querySelector(".mm-osheet-bg img");
  bg.src = r.thumb || coverArt(r.url || r.title, r.title);
  const body = host.querySelector(".mm-osheet-body");
  body.scrollTop = 0;
  const draw = (d) => {
    const keep = body.scrollTop;
    const x = Object.assign({}, r, d ? Object.fromEntries(Object.entries(d).filter(([, v]) => v !== "" && v != null && !(Array.isArray(v) && !v.length) && v !== 0)) : {});
    x.tags = (d && d.tags && d.tags.length ? d.tags : r.tags) || [];
    x.in_library = r.in_library || (d && d.in_library) || "";
    if (x.in_library && !r.in_library) r.in_library = x.in_library;
    const lib = onlLibTrack(x), cover = (d && d.cover) || r.thumb || coverArt(r.url || r.title, r.title);
    const best = (x.formats || [])[0];
    const rows = [
      ["album", x.album], ["year", x.year], ["genre", x.genre], ["duration", x.duration ? fmtTime(x.duration) : ""],
      ["published", onlDate(x.published)], [x.source === "ytmusic" ? "plays" : "views_n", onlCount(x.views)], ["likes", onlCount(x.likes)],
      ["quality", best ? [best.codec.toUpperCase(), best.kbps ? best.kbps + " kbps" : ""].filter(Boolean).join(" · ") : ""],
    ].filter((p) => p[1]);
    const loading = d === undefined;
    body.innerHTML = `<header class="mm-osheet-top"><span class="mm-eyebrow">${esc(t("onl.info"))}</span><button type="button" class="mm-icon" data-close aria-label="${esc(t("close"))}">${icon("close")}</button></header>
      <div class="mm-ohero"><span class="mm-art mm-ocover"><img alt="" src="${esc(cover)}" data-fb="${esc(coverArt(r.url || r.title, r.title))}"></span>
        <div class="mm-ohero-txt"><div class="mm-otags">${onlTags(x, 4)}</div><h2 id="osh-title">${esc(x.title)}</h2>
          ${x.artist ? `<p class="mm-oartist">${esc(x.artist)}</p>` : ""}
          <p class="mm-sub">${esc([x.album, x.year].filter(Boolean).join(" · ") || (loading ? "" : sourceName(x.source)))}</p>
          ${lib ? `<span class="mm-pill mm-oinlib">${icon("check")}${esc(t("onl.in_library"))}</span>` : ""}</div></div>
      <div class="mm-oprimary">
        <button type="button" class="mm-btn tint" data-a="listen">${icon("play")}${esc(t("play"))}</button>
        ${lib ? "" : `<button type="button" class="mm-btn ghost" data-a="dl"${ONL.queued.has(r.url) ? " disabled" : ""}>${icon(ONL.queued.has(r.url) ? "check" : "download")}${esc(t(ONL.queued.has(r.url) ? "download.queued" : "onl.download_lib"))}</button>`}
      </div>
      <div class="mm-osecondary">
        <button type="button" class="mm-chip" data-a="queue">${icon("queue")}${esc(t("onl.to_queue"))}</button>
        <button type="button" class="mm-chip" data-a="pl">${icon("playlist")}${esc(t("search.to_playlist"))}</button>
        <button type="button" class="mm-chip" data-a="copy">${icon("copy")}${esc(t("onl.copy_link"))}</button>
      </div>
      ${!lib && !x.in_library ? `<p class="mm-ohint">${esc(t("onl.listen_hint"))}</p>` : ""}
      <a class="mm-osource" href="#" data-a="open" style="--src:${sourceInfo(x.source).color}">${sourceIcon(x.source, "mm-srcdot lg")}
        <span><b>${esc(sourceName(x.source))}</b><span>${x.channel ? esc(x.channel) : esc(t("onl.source"))}${x.verified ? `<i class="mm-overified">${icon("check")}</i>` : ""}${x.followers ? ` · ${esc(t("onl.followers", { n: onlCount(x.followers) }))}` : ""}</span></span>
        <span class="mm-osource-go">${esc(t("onl.open_short"))}${icon("link")}</span></a>
      <dl class="mm-ogrid">${rows.map(([k, v]) => `<div><dt>${esc(t("onl.f." + k))}</dt><dd>${esc(v)}</dd></div>`).join("")}
        ${loading ? '<div class="mm-skel"></div><div class="mm-skel"></div><div class="mm-skel"></div><div class="mm-skel"></div>' : ""}</dl>
      ${x.formats && x.formats.length ? `<section class="mm-osec"><h3>${esc(t("onl.formats"))}</h3><div class="mm-oformats">${x.formats.map((f) => `<span>${esc([f.codec.toUpperCase() || f.ext.toUpperCase(), f.kbps ? f.kbps + " kbps" : "", f.khz ? f.khz + " kHz" : "", f.size ? (f.size / 1048576).toFixed(1) + (Memu.lang === "en" ? " MB" : " МБ") : ""].filter(Boolean).join(" · "))}</span>`).join("")}</div></section>` : ""}
      ${x.description ? `<section class="mm-osec"><h3>${esc(t("onl.description"))}</h3><p class="mm-odesc" data-clamp>${esc(x.description)}</p><button type="button" class="mm-btn ghost sm" data-a="more-desc">${esc(t("onl.show_all"))}</button></section>` : ""}
      ${d && d.errors && d.errors.info ? `<p class="mm-ohint">${icon("sparkle")}${esc(t("onl.err." + d.errors.info))}</p>` : ""}
      ${d && d.meta_source ? `<p class="mm-ocredit">${esc(t("onl.meta_from", { source: d.meta_source === "itunes" ? "iTunes" : "Deezer" }))}</p>` : ""}
      <section class="mm-osec" id="osh-lyrics"><div class="mm-osec-head"><h3>${esc(t("lyrics.title"))}</h3><button type="button" class="mm-btn ghost sm" data-a="lyrics">${icon("lyrics")}${esc(t("onl.find_lyrics"))}</button></div><div class="mm-olyrics"></div></section>
      ${(d ? d.related : ONL_SOURCES.includes(r.source)) ? `<section class="mm-osec" id="osh-similar"><div class="mm-osec-head"><h3>${esc(t("onl.similar"))}</h3><span class="mm-sub">${esc(t("onl.similar_from", { source: sourceName(r.source === "soundcloud" ? "soundcloud" : "ytmusic") }))}</span></div><div class="mm-osimilar"><div class="mm-skel mm-oskel"></div><div class="mm-skel mm-oskel"></div></div></section>` : ""}`;
    $$("img[data-fb]", body).forEach((img) => { img.onerror = () => { img.onerror = null; img.src = img.dataset.fb; }; });
    body.scrollTop = keep;
    if (ONL.lyrics.has(r.url)) showLyrics(ONL.lyrics.get(r.url));
    if (ONL.related.has(r.url)) showSimilar(ONL.related.get(r.url));
    else if (!loading && body.querySelector("#osh-similar")) loadSimilar();
  };
  function showLyrics(ly) {
    const box = body.querySelector(".mm-olyrics"); if (!box) return;
    const btn = body.querySelector('[data-a="lyrics"]'); if (btn) btn.hidden = true;
    if (!ly) { box.innerHTML = `<div class="mm-skel" style="height:72px"></div>`; return; }
    box.innerHTML = ly.found ? `<p class="mm-otext" data-clamp>${esc(ly.text)}</p><div class="mm-osec-foot"><button type="button" class="mm-btn ghost sm" data-a="more-ly">${esc(t("onl.show_all"))}</button><span class="mm-sub">${esc(t("onl.lyrics_from", { source: ly.source }))}${ly.synced ? " · " + esc(t("onl.synced")) : ""}</span></div>`
      : `<p class="mm-sub">${esc(t(ly.instrumental ? "onl.instrumental" : "lyrics.not_found"))}</p>`;
  }
  async function loadLyrics() {
    if (ONL.lyrics.has(r.url)) { showLyrics(ONL.lyrics.get(r.url)); return; }   // null — уже ищется
    ONL.lyrics.set(r.url, null); showLyrics(null);
    const ly = await API.soft("online.lyrics", { title: r.title, artist: r.artist || r.channel || "", duration: r.duration || 0 }, { found: false });
    ONL.lyrics.set(r.url, ly);
    if (ONL.current === r.url) showLyrics(ly);
  }
  function showSimilar(list) {
    const box = body.querySelector(".mm-osimilar"); if (!box) return;
    if (!list.length) { box.innerHTML = `<p class="mm-sub">${esc(t("empty.nothing"))}</p>`; return; }
    box.innerHTML = list.slice(0, 10).map((s, i) => `<button type="button" class="mm-osim" data-s="${i}">${onlThumb(s)}<span><b>${esc(s.title)}</b><span>${esc([s.artist, s.duration ? fmtTime(s.duration) : ""].filter(Boolean).join(" · "))}</span></span>${s.in_library ? `<i class="mm-overified" title="${esc(t("onl.in_library"))}">${icon("check")}</i>` : ""}</button>`).join("");
    $$("img[data-fb]", box).forEach((img) => { img.onerror = () => { img.onerror = null; img.src = img.dataset.fb; }; });
    box.onclick = (e) => { const b = e.target.closest("[data-s]"); if (b) onlineSheet(list[Number(b.dataset.s)], opts); };
  }
  async function loadSimilar() {
    const list = await API.soft("online.related", { url: r.url, limit: 12 }, []);
    ONL.related.set(r.url, list || []);
    if (ONL.current === r.url) showSimilar(list || []);
  }
  body.onclick = async (e) => {
    const a = e.target.closest("[data-a]"); if (!a) return;
    e.preventDefault();
    const act = a.dataset.a;
    if (act === "listen") onlineListen(r, opts);
    if (act === "dl") { a.disabled = true; a.innerHTML = icon("check") + esc(t("download.queued")); onlineDownload(r, opts); }
    if (act === "queue") onlineQueue(r, opts);
    if (act === "pl") onlineToPlaylist(r, a);
    if (act === "copy") copyText(r.url);
    if (act === "open") onlineOpen(r);
    if (act === "lyrics") loadLyrics();
    if (act === "more-desc" || act === "more-ly") { const p = a.closest(".mm-osec").querySelector("[data-clamp]"); const open = p.toggleAttribute("data-open"); a.textContent = t(open ? "onl.show_less" : "onl.show_all"); }
  };
  if (ONL.details.has(r.url)) draw(ONL.details.get(r.url));
  else {
    draw(undefined);
    const hint = { title: r.title, artist: r.artist, album: r.album, year: r.year, source: r.source, raw_title: r.raw_title, channel: r.channel, channel_url: r.channel_url,
      verified: r.verified, views: r.views, likes: r.likes, published: r.published, duration: r.duration, explicit: r.explicit, preview: r.preview };
    API.soft("online.details", { url: r.url, hint }, null).then((d) => {
      ONL.details.set(r.url, d || { errors: { info: "failed" }, related: ONL_SOURCES.includes(r.source) });
      if (ONL.current === r.url && !host.hidden) draw(ONL.details.get(r.url));
    });
  }
  if (focus === "lyrics") loadLyrics();
  if (focus) requestAnimationFrame(() => { const s = body.querySelector(focus === "lyrics" ? "#osh-lyrics" : "#osh-similar"); s && s.scrollIntoView({ block: "start" }); });
  requestAnimationFrame(() => { const c = body.querySelector("[data-close]"); c && c.focus({ preventScroll: true }); });
}
