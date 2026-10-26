

const setGroup = (title, hint, body) => `<section class="mm-ap-group"><header><h3>${esc(title)}</h3>${hint ? `<p>${esc(hint)}</p>` : ""}</header>${body}</section>`;
const setLine = (title, hint, control) => `<div class="mm-ap-line"><div><b>${esc(title)}</b>${hint ? `<p>${esc(hint)}</p>` : ""}</div>${control}</div>`;
const setSwitch = (key) => `<label class="mm-switch"><input type="checkbox" data-cfg-bool="${key}"${Cfg.get(key, true) ? " checked" : ""} aria-label="${esc(key)}"><i></i></label>`;
const setSeg = (key, values, labelOf) => `<div class="mm-seg2" role="radiogroup" data-cfg-seg="${key}">${values.map((v) => `<button type="button" role="radio" data-v="${v}" aria-checked="${String(Cfg.get(key)) === String(v)}">${esc(labelOf(v))}</button>`).join("")}</div>`;
const setRange = (key, min, max, step, label) => `<span class="mm-set-range"><input type="range" class="mm-range" data-cfg-range="${key}" min="${min}" max="${max}" step="${step}" value="${Number(Cfg.get(key, min))}" style="--v:${(Number(Cfg.get(key, min)) - min) / (max - min) * 100}%"><em data-cfg-label="${key}">${esc(label(Number(Cfg.get(key, min))))}</em></span>`;

function bindCfgControls(host, labels) {
  host.addEventListener("change", (e) => {
    const el = e.target;
    if (el.dataset.cfgBool) Cfg.set({ [el.dataset.cfgBool]: el.checked }).then((ok) => ok || toast(t("error.save")));
  });
  host.addEventListener("click", (e) => {
    const b = e.target.closest("[data-cfg-seg] [data-v]"); if (!b) return;
    const key = b.closest("[data-cfg-seg]").dataset.cfgSeg;
    $$("[data-v]", b.parentNode).forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    Cfg.set({ [key]: /^-?\d+$/.test(b.dataset.v) ? Number(b.dataset.v) : b.dataset.v });
  });
  host.addEventListener("input", (e) => {
    const el = e.target; if (!el.dataset.cfgRange) return;
    const key = el.dataset.cfgRange, v = Number(el.value);
    el.style.setProperty("--v", (v - el.min) / (el.max - el.min) * 100 + "%");
    const lab = host.querySelector(`[data-cfg-label="${key}"]`); if (lab && labels[key]) lab.textContent = labels[key](v);
    clearTimeout(el._t); el._t = setTimeout(() => { Cfg.set({ [key]: v }); if (key === "crossfade") Player.setCrossfade(v); }, 250);
  });
}

function renderPlaybackSettings(host) {
  const secs = (v) => (v ? v + " " + t("sec") : t("off"));
  host.innerHTML = `<div class="mm-ap">
    ${setGroup(t("set.flow"), "", [
      setLine(t("set.gapless"), t("set.gapless_hint"), setSwitch("gapless")),
      setLine(t("settings.crossfade"), t("settings.crossfade_hint"), setRange("crossfade", 0, 12, 1, secs)),
      setLine(t("set.auto_radio"), t("set.auto_radio_hint"), setSwitch("auto_radio")),
      setLine(t("set.play_click"), t("set.play_click_hint"), setSeg("play_click", ["double", "single"], (v) => t("set.play_click." + v))),
    ].join(""))}
    ${setGroup(t("set.volume"), t("set.volume_hint"), [
      setLine(t("set.normalize"), "", setSwitch("normalize")),
      setLine(t("set.target"), t("set.target_hint"), setSeg("normalize_target", [-18, -16, -14, -11], (v) => v + " LUFS")),
      `<div class="mm-ap-line"><div><b>${esc(t("set.analyze"))}</b><p id="loud-status">${esc(t("settings.checking"))}</p></div><button type="button" class="mm-btn ghost sm" id="loud-go">${icon("wave")}${esc(t("set.analyze_go"))}</button></div>`,
      setLine(t("set.auto_analyze"), t("set.auto_analyze_hint"), setSwitch("auto_analyze")),
    ].join(""))}
    ${setGroup(t("set.long"), "", [
      setLine(t("set.resume"), t("set.resume_hint"), setSwitch("resume_long")),
      setLine(t("set.resume_min"), "", setSeg("resume_min", [10, 20, 40, 60], (v) => t("dur.minutes", { m: v }))),
      setLine(t("set.seek_step"), t("set.seek_step_hint"), setSeg("seek_step", [5, 10, 15, 30], (v) => v + " " + t("sec"))),
    ].join(""))}
  </div>`;
  bindCfgControls(host, { crossfade: secs });
  const status = async () => {
    const s = await API.soft("tools.status", {}, null);
    const line = host.querySelector("#loud-status"); if (!line) return;
    const l = s && s.loudness;
    line.textContent = !l ? t("error.tools") : !l.ffmpeg ? t("set.analyze_no_ffmpeg") : l.queued || l.running ? t("set.analyze_running", { n: l.queued + (l.running ? 1 : 0) }) : t("set.analyze_done", { n: l.done });
    if (l && (l.queued || l.running) && host.isConnected) setTimeout(status, 3000);
  };
  host.querySelector("#loud-go").onclick = async () => { const r = await API.soft("library.analyze", {}, null); toast(r ? t("set.analyze_queued", { n: r.queued }) : t("error.tools")); status(); };
  status();
}

function renderLibrarySettings(host) {
  host.innerHTML = `<div class="mm-ap">
    ${setGroup(t("set.smart"), t("set.smart_hint"), `<div class="mm-set-smart" id="set-smart"></div>
      <div class="mm-ap-foot"><button type="button" class="mm-btn ghost sm" id="set-smart-manage">${icon("rule")}${esc(t("smart.manage"))}</button><button type="button" class="mm-btn ghost sm" id="set-smart-new">${icon("plus")}${esc(t("smart.new"))}</button></div>`)}
    ${setGroup(t("set.sections"), t("set.sections_hint"), [
      setLine(t("lib.genres"), "", setSwitch("show_genres")),
      setLine(t("lib.folders"), "", setSwitch("show_folders")),
      setLine(t("lib.history"), "", setSwitch("show_history")),
    ].join(""))}
    ${setGroup(t("set.editing"), "", [
      setLine(t("ed.write_tags"), t("set.write_tags_hint"), setSwitch("write_tags")),
      setLine(t("set.confirm_delete"), t("set.confirm_delete_hint"), setSwitch("confirm_delete")),
      setLine(t("set.artist_photos"), t("set.artist_photos_hint"), setSwitch("artist_photos")),
    ].join(""))}
    ${setGroup(t("set.import"), t("import.hint"), `<div class="mm-ed-row"><input class="mm-input" id="imp-dir" spellcheck="false" placeholder="D:\\Music"><button type="button" class="mm-btn ghost sm" id="imp-go">${icon("folder")}${esc(t("import.go"))}</button></div>
      <div class="mm-ed-row"><input class="mm-input" id="m3u-path" spellcheck="false" placeholder="D:\\Music\\list.m3u8"><button type="button" class="mm-btn ghost sm" id="m3u-go">${icon("playlist")}${esc(t("set.m3u_import"))}</button></div>
      <p class="mm-sub">${esc(t("set.drop_hint"))}</p>`)}
    ${setGroup(t("set.tidy"), "", setLine(t("dupes.title"), t("dupes.hint"), `<button type="button" class="mm-btn ghost sm" id="set-dupes">${icon("copies")}${esc(t("dupes.find"))}</button>`))}
  </div>`;
  bindCfgControls(host, {});
  const smartBox = host.querySelector("#set-smart");
  const drawSmart = async () => {
    const all = ((await API.soft("playlists.list", { all: true }, [])) || []).filter((p) => p.smart || p.rules);
    if (!host.isConnected) return;
    smartBox.innerHTML = all.map((p) => `<label class="mm-ap-toggle"><span>${esc((Memu.lang === "en" ? p.name_en : p.name_ru) || p.name)}<em>${esc(plural("count.tracks", p.count || 0))}</em></span><span class="mm-switch"><input type="checkbox" data-smart="${esc(p.id)}"${p.hidden ? "" : " checked"}><i></i></span></label>`).join("");
  };
  smartBox.addEventListener("change", (e) => { const id = e.target.dataset.smart; if (id) setPlaylistHidden(id, !e.target.checked); });
  drawSmart();
  host.querySelector("#set-smart-manage").onclick = () => smartManager();
  host.querySelector("#set-smart-new").onclick = () => smartBuilder(null);
  host.querySelector("#imp-go").onclick = async () => {
    const path = host.querySelector("#imp-dir").value.trim(); if (!path) return;
    try { await API.call("import.folder", { path }); toast(t("import.started")); pollJobs(); } catch (error) { toast(String(error.message || error)); }
  };
  host.querySelector("#m3u-go").onclick = async () => {
    const path = host.querySelector("#m3u-path").value.trim(); if (!path) return;
    try { const r = await API.call("playlist.import", { path }); toast(t("set.m3u_done", { n: r.matched, miss: r.missing.length })); await libraryChanged({ now: true }); go("playlist", { id: r.playlist.id }); }
    catch (error) { toast(String(error.message || error)); }
  };
  host.querySelector("#set-dupes").onclick = () => go("dupes");
}

const LYRIC_SOURCES = [["lrclib", "LRCLIB", 1], ["musixmatch", "Musixmatch", 1], ["netease", "NetEase", 1], ["qq", "QQ Music", 1], ["genius", "Genius", 0], ["lyricsovh", "lyrics.ovh", 0]];
function lyricSources(box) {
  const saved = Cfg.get("lyrics_sources", LYRIC_SOURCES.map((x) => x[0]));
  const on = (Array.isArray(saved) ? saved : []).filter((id) => LYRIC_SOURCES.some((x) => x[0] === id));
  const draw = () => {
    const rows = [...on, ...LYRIC_SOURCES.map((x) => x[0]).filter((id) => !on.includes(id))];
    box.innerHTML = rows.map((id) => {
      const [, name, synced] = LYRIC_SOURCES.find((x) => x[0] === id), i = on.indexOf(id);
      return `<div class="mm-ap-line mm-ly-source" data-src="${id}"><div><b>${esc(name)}</b><p>${esc(t(synced ? "lyrics.synced" : "lyrics.plain"))}</p></div>
        ${i >= 0 ? `<button type="button" class="mm-icon sm" data-mv="-1" aria-label="↑" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" class="mm-icon sm" data-mv="1" aria-label="↓" ${i === on.length - 1 ? "disabled" : ""}>↓</button>` : ""}
        <label class="mm-switch"><input type="checkbox" data-src-on${i >= 0 ? " checked" : ""} aria-label="${esc(name)}"><i></i></label></div>`;
    }).join("");
  };
  const save = () => { draw(); Cfg.set({ lyrics_sources: on.slice() }).then((ok) => ok || toast(t("error.save"))); };
  box.addEventListener("change", (e) => {
    if (!e.target.matches("[data-src-on]")) return;
    e.stopPropagation();
    const id = e.target.closest("[data-src]").dataset.src, i = on.indexOf(id);
    if (e.target.checked && i < 0) on.push(id); else if (!e.target.checked && i >= 0) on.splice(i, 1);
    save();
  });
  box.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mv]"); if (!b) return;
    const id = b.closest("[data-src]").dataset.src, i = on.indexOf(id), j = i + Number(b.dataset.mv);
    if (i < 0 || j < 0 || j >= on.length) return;
    [on[i], on[j]] = [on[j], on[i]]; save();
  });
  draw();
}

const lyOffsetLabel = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + (Math.abs(v) / 1000).toFixed(2).replace(".", t("lyrics.sec") === "с" ? "," : ".") + " " + t("lyrics.sec");

function renderDownloadSettings(host) {
  host.innerHTML = `<div class="mm-ap">
    ${setGroup(t("set.where"), "", `${setLine(t("settings.folder"), t("settings.folder_hint"), "")}<div class="mm-ed-row"><input class="mm-input" id="set-dir" spellcheck="false" value="${esc(Cfg.get("download_dir", ""))}" placeholder="${esc(Cfg.get("default_download_dir", ""))}"><button type="button" class="mm-btn ghost sm" id="set-dir-save">${esc(t("save"))}</button></div>`)}
    ${setGroup(t("set.quality_group"), "", [
      setLine(t("settings.quality"), t("settings.quality_hint"), setSeg("quality", ["best", "normal", "small"], (v) => t("quality." + v))),
      setLine(t("set.prefer_m4a"), t("set.prefer_m4a_hint"), setSwitch("prefer_m4a")),
      setLine(t("set.parallel"), t("set.parallel_hint"), setSeg("parallel_downloads", [1, 2, 3, 4], String)),
    ].join(""))}
    ${setGroup(t("set.after"), "", [
      setLine(t("set.embed_cover"), "", setSwitch("embed_cover")),
      setLine(t("set.embed_metadata"), "", setSwitch("embed_metadata")),
      setLine(t("set.youtube_fallback"), t("set.youtube_fallback_hint"), setSwitch("youtube_fallback")),
    ].join(""))}
    ${setGroup(t("lyrics.title"), "", [
      setLine(t("set.auto_lyrics"), t("set.auto_lyrics_hint"), setSwitch("auto_lyrics")),
      setLine(t("set.lyrics_plain"), t("set.lyrics_plain_hint"), setSwitch("lyrics_plain_fallback")),
      setLine(t("set.lyrics_sync"), t("set.lyrics_sync_hint"), setSeg("lyrics_sync", ["smart", "line", "off"], (v) => t("set.lyrics_sync." + v))),
      setLine(t("set.lyrics_offset"), t("set.lyrics_offset_hint"), setRange("lyrics_offset_ms", -1000, 1000, 10, lyOffsetLabel)),
      setLine(t("set.lyrics_sources"), t("set.lyrics_sources_hint"), ""),
      `<div class="mm-ly-sources" id="set-ly-src"></div>`,
    ].join(""))}
    ${setGroup(t("settings.loader"), "", [
      `<div class="mm-ap-line"><div><b>yt-dlp · ffmpeg</b><p id="tools-line">${esc(t("settings.checking"))}</p></div><span class="mm-status" id="tools-dot"><i></i><span></span></span><button type="button" class="mm-btn ghost sm" id="tools-up">${icon("refresh")}${esc(t("settings.update"))}</button></div>`,
      setLine(t("settings.cookies"), t("settings.cookies_hint"), `<select class="mm-select" id="set-cookies" aria-label="${esc(t("settings.cookies"))}"><option value="">${esc(t("settings.cookies_none"))}</option>${["chrome", "edge", "firefox", "opera", "brave", "vivaldi", "chromium"].map((b) => `<option value="${b}"${Cfg.get("cookies_from_browser") === b ? " selected" : ""}>${b[0].toUpperCase() + b.slice(1)}</option>`).join("")}</select>`),
    ].join(""))}
  </div>`;
  bindCfgControls(host, { lyrics_offset_ms: lyOffsetLabel });
  lyricSources(host.querySelector("#set-ly-src"));
  host.querySelector("#set-dir-save").onclick = async () => toast((await Cfg.set({ download_dir: host.querySelector("#set-dir").value.trim() })) ? t("toast.saved") : t("error.save"));
  host.querySelector("#set-cookies").onchange = (e) => Cfg.set({ cookies_from_browser: e.target.value });
  const tools = async () => {
    const s = await API.soft("tools.status", {}, null);
    if (!host.isConnected) return;
    const ok = s && s.ytdlp && s.ytdlp.ok;
    host.querySelector("#tools-dot").className = "mm-status " + (ok ? "ok" : "bad");
    host.querySelector("#tools-dot span").textContent = ok ? t("settings.ready") : t("settings.missing");
    host.querySelector("#tools-line").textContent = s ? t("settings.loader_line", { ytdlp: ok ? s.ytdlp.version || "?" : t("settings.missing"), ffmpeg: s.ffmpeg && s.ffmpeg.ok ? (s.ffmpeg.version || t("settings.ready")) : t("settings.ffmpeg_optional") }) : t("error.tools");
  };
  host.querySelector("#tools-up").onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true; b.innerHTML = icon("refresh") + esc(t("settings.updating"));
    const r = await API.soft("tools.update", {}, null);
    b.disabled = false; b.innerHTML = icon("refresh") + esc(t("settings.update"));
    toast(r ? t("settings.updated", { version: r.version || "" }) : t("error.tools")); tools();
  };
  tools();
}

function renderKeySettings(host) {
  const custom = Object.assign({}, Cfg.get("hotkeys", {}) || {});
  const comboFor = (action, def) => (custom[action] !== undefined ? custom[action] : def);
  const pretty = (combo) => combo ? combo.split("+").map((k) => `<kbd>${esc(t("key." + k) === "key." + k ? k.replace(/^Arrow/, "") : t("key." + k))}</kbd>`).join("") : `<span class="mm-faint">${esc(t("keys.none"))}</span>`;
  const draw = () => {
    host.innerHTML = `<div class="mm-ap">${setGroup(t("keys.title"), t("keys.hint"), `<div class="mm-keys">${HOTKEYS.map(([action, def]) => {
      const combo = comboFor(action, def);
      const clash = combo && HOTKEYS.some(([a, d]) => a !== action && comboFor(a, d) === combo);
      return `<div class="mm-key${clash ? " clash" : ""}" data-action="${action}"><span>${esc(t("keys.a." + action))}</span><button type="button" class="mm-key-combo" data-rec title="${esc(t("keys.change"))}">${pretty(combo)}</button>
        <button type="button" class="mm-icon sm" data-clear title="${esc(t("keys.clear"))}" aria-label="${esc(t("keys.clear"))}">${icon("close")}</button></div>`;
    }).join("")}</div><div class="mm-ap-foot"><button type="button" class="mm-btn ghost sm" id="keys-reset">${icon("refresh")}${esc(t("keys.reset"))}</button></div>`)}</div>`;
  };
  draw();
  const save = () => Cfg.set({ hotkeys: custom }).then(draw);
  host.addEventListener("click", (e) => {
    if (e.target.closest("#keys-reset")) { Object.keys(custom).forEach((k) => delete custom[k]); save(); return; }
    const rowEl = e.target.closest("[data-action]"); if (!rowEl) return;
    const action = rowEl.dataset.action;
    if (e.target.closest("[data-clear]")) { custom[action] = ""; save(); return; }
    const btn = e.target.closest("[data-rec]"); if (!btn) return;
    btn.classList.add("rec"); btn.innerHTML = `<span>${esc(t("keys.press"))}</span>`;
    const onKey = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      if (ev.key === "Escape") { finish(); draw(); return; }
      const combo = comboOf(ev); if (!combo) return;
      custom[action] = combo; finish(); save();
    };
    const finish = () => document.removeEventListener("keydown", onKey, true);
    document.addEventListener("keydown", onKey, true);
  });
}

SETTINGS_TABS.push(
  { id: "playback", order: 20, icon: "play", title: () => t("set.tab.playback"), render: renderPlaybackSettings },
  { id: "library", order: 30, icon: "library", title: () => t("set.tab.library"), render: renderLibrarySettings },
  { id: "downloads", order: 40, icon: "download", title: () => t("set.tab.downloads"), render: renderDownloadSettings },
  { id: "keys", order: 50, icon: "keyboard", title: () => t("set.tab.keys"), render: renderKeySettings },
);

VIEWS.settings = async (el, p) => {
  const tabs = SETTINGS_TABS.slice().sort((a, b) => a.order - b.order);
  const cur = tabs.find((x) => x.id === (p && p.tab)) || tabs.find((x) => x.id === S.prefs.settingsTab) || tabs[0];
  el.innerHTML = `<h1 class="mm-h1">${esc(t("nav.settings"))}</h1>${API.mocked ? `<p class="mm-sub">${esc(t("settings.demo"))}</p>` : ""}
    <div class="mm-set-tabs" role="tablist">${tabs.map((x) => `<button type="button" role="tab" data-stab="${x.id}" aria-selected="${x === cur}">${icon(x.icon || "settings")}<span>${esc(x.title())}</span></button>`).join("")}</div>
    <div class="mm-set-body" id="set-body"></div>`;
  const show = (tab) => {
    S.prefs.settingsTab = tab.id; savePrefs();
    $$("[data-stab]", el).forEach((b) => b.setAttribute("aria-selected", String(b.dataset.stab === tab.id)));
    const body = el.querySelector("#set-body"), fresh = body.cloneNode(false); body.replaceWith(fresh);
    tab.render(fresh);
  };
  el.querySelector(".mm-set-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-stab]"); if (b) show(tabs.find((x) => x.id === b.dataset.stab)); });
  if (!Object.keys(Cfg.all()).length) await Cfg.load();
  if (el.isConnected) show(cur);
};
