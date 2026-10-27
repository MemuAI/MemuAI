

const NP = (() => {
  const el = $("#np");
  let bg = null, viz = null, lyrics = null, lyricIdx = -2, lyricToken = 0, manual = 0, manualTimer = 0, tab = "lyrics", closing = 0;
  let wordIdx = -1, tapSync = false, editing = false, roman = false, romanToken = 0;
  const offsetTimers = new Map();

  let timing = null, lineNodes = [], gapNodes = new Map(), wordSpans = [], gapKey = "", gapEnd = false, syncShown = "";
  const syncMode = () => { const v = typeof Cfg !== "undefined" ? Cfg.get("lyrics_sync", "smart") : "smart"; return v === "line" || v === "off" ? v : "smart"; };
  function lazyInit() {
    if (bg) return;
    bg = Fx.Ambient($("#np-bg"), { w: 80, h: 50, strength: 1.15 });
    viz = Fx.Visualizer($("#viz"));
    Fx.tilt($("#np-stage"), $("#np-art"));
    new ResizeObserver(() => { if (isOpen()) { viz.resize(); positionLyrics(); } }).observe(el);
    if (typeof Cfg !== "undefined") Cfg.on(() => { if (lyrics && lyrics.synced && syncShown !== syncMode()) renderLyrics(); });
  }
  function isOpen() { return !el.hidden && !el.classList.contains("out"); }

  function flip() {
    const from = $("#bar-img"), target = $("#np-art .mm-np-cover");
    if (!from || !from.getAttribute("src") || Theme.ui.motion !== "full" || Theme.ui.npStyle === "full" || root.hasAttribute("data-calm") || Fx.reduce.matches || !el.animate) return false;
    const a = from.getBoundingClientRect(), b = target.getBoundingClientRect();
    if (a.width < 8 || b.width < 8) return false;
    const ghost = document.createElement("img");
    ghost.className = "mm-flip"; ghost.alt = ""; ghost.src = from.currentSrc || from.src;
    Object.assign(ghost.style, { left: b.left + "px", top: b.top + "px", width: b.width + "px", height: b.height + "px" });
    document.body.appendChild(ghost);
    target.style.opacity = "0";
    const s = a.width / b.width;
    const anim = ghost.animate([
      { transform: `translate(${(a.left - b.left).toFixed(1)}px, ${(a.top - b.top).toFixed(1)}px) scale(${s.toFixed(4)})`, borderRadius: (10 / s).toFixed(1) + "px" },
      { transform: "none", borderRadius: getComputedStyle(target).borderTopLeftRadius },
    ], { duration: 560, easing: "cubic-bezier(.2,.8,.2,1)" });
    anim.onfinish = anim.oncancel = () => { target.style.opacity = ""; ghost.remove(); };
    return true;
  }
  function open(which) {
    lazyInit();
    clearTimeout(closing);
    el.hidden = false; el.classList.remove("out", "flip"); el.classList.add("in");
    if (flip()) el.classList.add("flip");
    setTab(which || tab);
    if (ambient) ambient.stop();
    restyle();
    setColors(paletteFor(Player.current() || { id: "memu" }).map((c, i) => getComputedStyle(root).getPropertyValue(["--tint", "--tint-2", "--tint-3"][i]).trim() || c));
    onTrack(Player.current(), true);
    applyCalm();
    Theme.appbar();
    setTimeout(() => { if (isOpen() && viz) viz.resize(); }, 650);
    $("#np-close").focus();
  }
  function close() {
    if (el.hidden) return;
    el.classList.remove("in"); el.classList.add("out");
    if (viz) viz.stop(); if (bg) bg.stop(); Fx.remove(lyricFrame);
    if (ambient && Theme.ui.bg === "gradient") ambient.start();
    Theme.appbar();
    closing = setTimeout(() => { el.hidden = true; el.classList.remove("out"); }, root.hasAttribute("data-calm") ? 0 : 340);
  }
  function setColors(colors) {
    if (!bg) return;
    const rgb = hexToRgb(colors[2]) || [20, 20, 30], light = el.hasAttribute("data-light");

    const base = "#" + rgb.map((v) => Math.round(light ? 246 + (v - 128) * .03 : v * .18).toString(16).padStart(2, "0")).join("");
    bg.setColors(colors.map((c) => light ? hsl(rgbToHsl(hexToRgb(c) || [124, 92, 255])[0], 70, 80) : tone(c, 52, 45)), base);
    viz.setColors([colors[0], colors[1]]);
  }

  function restyle() {
    const ui = Theme.ui;
    el.dataset.style = ui.npStyle; el.dataset.bg = ui.bg;
    const light = ui.npTheme === "auto" && Theme.scheme() === "light";
    if (light !== el.hasAttribute("data-light")) { el.toggleAttribute("data-light", light); Theme.appbar(); if (bg) setColors(paletteFor(Player.current() || { id: "memu" }).map((c, i) => getComputedStyle(root).getPropertyValue(["--tint", "--tint-2", "--tint-3"][i]).trim() || c)); }
    $$("#np-style [data-style]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.style === ui.npStyle)));
    if (!bg || !isOpen()) return;
    ui.bg === "gradient" && ui.npStyle !== "full" ? bg.start() : bg.stop();
    applyCalm();
    setTimeout(() => { if (isOpen() && viz) { viz.resize(); positionLyrics(); } }, 380);
  }
  function applyCalm() {
    if (!viz || !isOpen()) return;
    const mode = Theme.ui.viz, style = Theme.ui.npStyle;
    const on = mode !== "off" && style !== "minimal" && style !== "full" && !root.hasAttribute("data-calm") && !Fx.reduce.matches;
    $("#np-viz-toggle").setAttribute("aria-pressed", String(mode !== "off"));
    viz.setMode(mode);
    on ? viz.start() : viz.stop();
    bg.kick();
  }

  const lyricFrame = () => syncLyrics(Player.times().cur);
  function lyricLoop() {
    if (isOpen() && tab === "lyrics" && lyrics && lyrics.synced && syncMode() !== "off" && Player.st.playing && root.hasAttribute("data-calm")) Fx.add(lyricFrame);
    else Fx.remove(lyricFrame);
  }
  function onState() { applyCalm(); lyricLoop(); }
  function onTrack(tr, force) {
    if (!tr || !lyrics || lyrics.id !== tr.id) { editing = false; ++lyricToken; }
    if (!tr) { $("#np-title").textContent = t("player.idle"); $("#np-artist").textContent = t("player.idle_hint"); return; }
    if (el.hidden && !force) { lyrics = null; lyricIdx = -2; return; }
    const fb = coverArt(tr.id, "", tr.title);
    [$("#np-img"), $("#np-label"), $("#np-blur"), $("#np-backdrop")].forEach((img) => { img.dataset.fb = fb; img.src = tr.cover || fb; });
    const title = $("#np-title");
    title.textContent = tr.title; $("#np-artist").textContent = tr.artist;
    title.title = tr.title;

    title.dataset.len = tr.title.length > 44 ? "xl" : tr.title.length > 26 ? "l" : "";
    $("#np-from").textContent = Player.st.from || tr.album || "MemuMusic";
    const like = $("#np-like"); like.dataset.like = tr.id; like.setAttribute("aria-pressed", String(!!tr.liked));
    if (force && lyrics && lyrics.id === tr.id) return;
    loadLyrics(tr);
    if (tab === "queue") renderQueue();
  }

  async function loadLyrics(tr, force) {
    const token = ++lyricToken;
    const box = $("#lyrics-inner");
    lyrics = { id: tr.id, synced: null, plain: null, state: "loading", offset: 0, alts: [] }; lyricIdx = -2; tapSync = false; timing = null;
    editing = false; renderBar();
    box.style.transform = ""; box.innerHTML = [72, 56, 64, 44].map((w) => `<div class="mm-skel mm-lyric-skel" style="width:${w}%"></div>`).join("");
    const timeout = new Promise((ok) => setTimeout(() => ok("timeout"), 16000));
    const res = await Promise.race([API.soft("lyrics.get", force ? { id: tr.id, force: true } : { id: tr.id }, null), timeout]);
    if (token !== lyricToken) return;
    if (res === "timeout") { lyrics.state = "timeout"; renderLyrics(); return; }
    setLyrics(tr.id, res);
    if (force && !lyrics.synced && !lyrics.plain) toast(t("lyrics.not_found"));
  }
  function setLyrics(id, res) {
    ++romanToken;
    lyrics = { id, synced: res && Array.isArray(res.synced) && res.synced.length ? res.synced : null, plain: res && res.plain,
      state: res && res.instrumental ? "instrumental" : res && res.auto_off ? "off" : "none", source: (res && res.source) || "",
      name: res && res.source === "manual" ? t("lyrics.mine") : (res && res.source_name) || "", offset: +(res && res.offset) || 0, alts: (res && res.alts) || [], choice: (res && res.choice) || 0,
      durDiff: +(res && res.duration_diff) || 0 };
    lyrics.original = { synced: lyrics.synced, plain: lyrics.plain };
    if (!editing) renderLyrics();
    if (roman && hasRomanizable(lyrics)) applyRoman();
  }

  async function applyRoman() {
    const current = lyrics, token = ++romanToken;
    if (!current) return;
    if (!roman) { current.synced = current.original.synced; current.plain = current.original.plain; renderLyrics(true); refreshRomanBar(); return; }
    try {
      const res = await API.call("lyrics.romanize", { id: current.id, language: "auto" });
      if (token !== romanToken || lyrics !== current || !roman) return;
      current.synced = res.synced; current.plain = res.plain; renderLyrics(true); refreshRomanBar();
    } catch (error) {
      if (token === romanToken && lyrics === current) {
        roman = false; current.synced = current.original.synced; current.plain = current.original.plain;
        renderLyrics(true); refreshRomanBar(); toast(t("lyrics.roman_error") + ": " + String(error.message || error));
      }
    }
  }

  function refreshRomanBar() {
    const bar = $("#ly-bar"), button = $("[data-ly=roman]", bar), label = $(".mm-ly-src em", bar);
    if (button) button.setAttribute("aria-pressed", String(roman));
    if (!roman) $("[data-lylang]", bar)?.remove();
    if (label && lyrics) label.textContent = lyrics.synced ? (lyrics.synced.some((r) => r[2]) ? t("lyrics.words") : t("lyrics.synced")) : t("lyrics.plain");
  }

  function hasRomanizable(value) {
    const original = value && (value.original || value);
    return !!original && /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7a3]/.test((original.plain || "") + "\n" + (original.synced || []).map(row => row[1] || "").join("\n"));
  }

  function renderBar() {
    const bar = $("#ly-bar");
    if (!lyrics || !Player.current() || tab !== "lyrics" || editing) { bar.hidden = true; return; }
    bar.hidden = false;
    const kind = lyrics.synced ? (lyrics.synced.some((r) => r[2]) ? t("lyrics.words") : t("lyrics.synced")) : t("lyrics.plain");
    const off = lyrics.offset;
    const offText = (off > 0 ? "+" : "") + off.toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
    bar.innerHTML = `<button type="button" class="mm-ly-src" data-ly="alts" aria-haspopup="menu" title="${esc(t("lyrics.variants"))}"><span>${esc(lyrics.name || lyrics.source || "—")}</span><em>${esc(kind)}</em><svg><use href="#i-down"/></svg></button>
      ${lyrics.synced ? `<span class="mm-ly-off" role="group" aria-label="${esc(t("lyrics.offset"))}"><button type="button" data-ly="off-" title="${esc(t("lyrics.earlier"))}" aria-keyshortcuts="[">−</button><button type="button" class="mm-ly-reset" data-ly="off-reset" title="${esc(t("lyrics.offset_reset"))}" aria-label="${esc(t("lyrics.offset_reset"))}">${offText} ${esc(t("lyrics.sec"))}</button><button type="button" data-ly="off+" title="${esc(t("lyrics.later"))}" aria-keyshortcuts="]">+</button></span>` : ""}
      ${lyrics.synced && syncMode() !== "off" ? `<button type="button" class="mm-ly-sync" data-ly="sync" aria-pressed="${syncMode() === "line"}" title="${esc(t(syncMode() === "line" ? "lyrics.switch_chars" : "lyrics.switch_lines"))}">${icon("lyrics")}<span>${esc(t(syncMode() === "line" ? "lyrics.mode_lines" : "lyrics.mode_chars"))}</span></button>` : ""}
      ${hasRomanizable(lyrics) ? `<button type="button" class="mm-ly-sync" data-ly="roman" aria-pressed="${roman}" title="${esc(t("lyrics.roman_hint"))}">Aa <span>${esc(t("lyrics.roman"))}</span></button>` : ""}
      ${lyrics.synced ? `<button type="button" class="mm-ly-tap${tapSync ? " on" : ""}" data-ly="tap" aria-pressed="${tapSync}" title="${esc(t("lyrics.tap_hint"))}">${icon("target")}<span>${esc(tapSync ? t("lyrics.tap_now") : t("lyrics.tap"))}</span></button>` : ""}
      <span class="mm-grow"></span>
      ${lyrics.synced && lyrics.durDiff > 2 ? `<span class="mm-ly-warn" title="${esc(t("lyrics.dur_hint", { n: Math.round(lyrics.durDiff) }))}">${esc(t("lyrics.dur", { n: Math.round(lyrics.durDiff) }))}</span>` : ""}
      ${lyrics.alts.length > 1 ? `<button type="button" class="mm-ly-wrong" data-ly="next">${esc(t("lyrics.wrong"))}</button>` : ""}
      <button type="button" class="mm-ly-find" data-ly="find" title="${esc(t("lyrics.find"))}" aria-label="${esc(t("lyrics.find"))}">${icon("search")}</button>
      <button type="button" class="mm-ly-edit" data-ly="edit" title="${esc(t("lyrics.edit_hint"))}">${icon("edit")}<span>${esc(t("lyrics.edit"))}</span></button>`;
  }
  function altsMenu() {
    const bar = $("#ly-bar"), old = $(".mm-ly-alts", bar);
    if (old) { old.remove(); return; }
    if (!lyrics) return;
    const menu = document.createElement("div");
    menu.className = "mm-ly-alts"; menu.setAttribute("role", "menu");
    menu.innerHTML = (lyrics.alts.length ? lyrics.alts.map((a) => `<button type="button" role="menuitemradio" aria-checked="${a.index === lyrics.choice && lyrics.source !== "manual"}" data-ly="pick" data-i="${a.index}">
      <b>${esc(a.name)}</b><span>${esc(a.synced ? (a.words ? t("lyrics.words") : t("lyrics.synced")) : t("lyrics.plain"))}${a.duration ? " · " + fmtTime(a.duration) : ""} · ${esc(t("lyrics.lines", { n: a.lines }))}</span>
      <small>${esc([a.artist, a.title].filter(Boolean).join(" — "))}</small></button>`).join("") : `<span class="mm-ly-alts-empty">${esc(t("lyrics.no_variants"))}</span>`) + `<button type="button" data-ly="find">${icon("search")} ${esc(t("lyrics.find"))}</button>${typeof MusicAI !== "undefined" ? `<button type="button" data-ly="ai">${icon("sparkle")} ${esc(t("lyrics.ai_find"))}</button>` : ""}`;
    bar.appendChild(menu);
    const outside = (e) => { if (!bar.contains(e.target)) { menu.remove(); document.removeEventListener("pointerdown", outside, true); } };
    setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
  }
  async function choose(args) {
    const tr = Player.current(); if (!tr || !lyrics) return;
    const token = ++lyricToken;
    try {
      const res = await API.call("lyrics.choose", { id: tr.id, ...args });
      if (token === lyricToken && Player.current()?.id === tr.id && lyrics?.id === tr.id) setLyrics(tr.id, res);
    } catch (error) { toast(String(error.message || error)); }
  }

  function nudge(delta, say) {
    const tr = Player.current(); if (!tr || !lyrics || !lyrics.synced || lyrics.id !== tr.id) return false;
    setOffset(Math.round((lyrics.offset + delta) * 10) / 10);
    if (say) toast(t("lyrics.offset_now", { n: (lyrics.offset > 0 ? "+" : "") + lyrics.offset.toFixed(1) }));
    return true;
  }
  function setOffset(value) {
    const tr = Player.current(); if (!tr || !lyrics) return;
    if (!Number.isFinite(value)) return;
    const id = tr.id, offset = Math.max(-30, Math.min(30, Math.round(value * 100) / 100));
    lyrics.offset = offset;
    renderBar(); lyricIdx = -2; syncLyrics(Player.times().cur, true);
    clearTimeout(offsetTimers.get(id));
    offsetTimers.set(id, setTimeout(async () => {
      offsetTimers.delete(id);
      const result = await API.soft("lyrics.offset", { id, offset }, null);
      if (!result) toast(t("error.save"));
    }, 400));
  }

  const globalOffset = () => (typeof Cfg !== "undefined" ? Number(Cfg.get("lyrics_offset_ms", 0)) || 0 : 0) / 1000;
  function lyricClock(cur) { return (Player.st.playing ? Player.heard() : cur) - lyrics.offset - globalOffset(); }
  function lineHtml(row, plain) {
    if (!row[2] || plain) return row[1] ? esc(row[1]) : "♪";
    return row[2].map(([, w], k) => `<i class="w" data-w="${k}">${esc(w)}</i>`).join("");
  }

  const gapHtml = (after) => `<div class="mm-ly-gap" data-g="${after}" aria-hidden="true"><i></i><i></i><i></i></div>`;
  function syncedHtml(mode) {
    const list = lyrics.synced;
    if (mode === "off") return list.filter((row) => String(row[1] || "").trim()).map((row) => `<button type="button" class="mm-lyric" data-l="${list.indexOf(row)}" data-t="${row[0]}"><span>${esc(row[1])}</span></button>`).join("");
    const tr = Player.current();
    timing = LyricTiming.build(list, tr && tr.id === lyrics.id ? tr.duration : 0);
    const gapAfter = new Set(timing.gaps.map((g) => g.after));
    return (gapAfter.has(-1) ? gapHtml(-1) : "") + timing.lines.map((ln, i) => {
      const row = list[ln.row], words = mode === "smart" && row[2];
      return `<button type="button" class="mm-lyric${words ? " words" : ""}" data-l="${ln.row}" data-t="${row[0]}"><span>${lineHtml(row, !words)}</span></button>${gapAfter.has(i) ? gapHtml(i) : ""}`;
    }).join("");
  }
  function renderLyrics(preserveBar = false) {
    const box = $("#lyrics-inner");
    if (!lyrics || editing) return;
    if (!preserveBar) renderBar();
    const mode = syncShown = syncMode();

    $("#lyrics").classList.toggle("static", (!lyrics.synced && lyrics.state !== "loading") || (!!lyrics.synced && mode === "off"));
    $("#lyrics").dataset.sync = mode;
    lyricLoop();
    if (lyrics.synced) {
      timing = null;
      box.innerHTML = syncedHtml(mode);
      lineNodes = $$(".mm-lyric", box); gapNodes = new Map($$(".mm-ly-gap", box).map((g) => [Number(g.dataset.g), g]));
      wordSpans = []; gapKey = ""; gapEnd = false;
      if (mode === "off") box.style.transform = "";
      lyricIdx = -2; syncLyrics(Player.times().cur, true);
    } else if (lyrics.plain) {
      box.style.transform = "";
      box.innerHTML = `<div class="mm-lyrics-plain">${esc(lyrics.plain)}</div>`;
    } else if (lyrics.state !== "loading") {
      box.style.transform = "";
      const kind = lyrics.state === "instrumental" ? "instrumental" : lyrics.state === "timeout" ? "timeout" : lyrics.state === "off" ? "off" : "none";
      box.innerHTML = `<div class="mm-lyrics-empty">
        <span class="mm-lyrics-badge" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#i-${kind === "instrumental" ? "wave" : "lyrics"}"/></svg></span>
        <b>${esc(t("lyrics." + kind))}</b><span>${esc(t("lyrics." + kind + "_hint"))}</span>
        <div class="mm-lyrics-acts"><button type="button" class="mm-chip glass" data-ly="find"><svg><use href="#i-search"/></svg>${esc(t("lyrics.find"))}</button>
        <button type="button" class="mm-chip glass" data-ly="paste"><svg><use href="#i-plus"/></svg>${esc(t("lyrics.paste"))}</button>${typeof MusicAI !== "undefined" ? `<button type="button" class="mm-chip glass" data-ly="ai">${esc(t("lyrics.ai_find"))}</button>` : ""}</div></div>`;
    }
  }

  function pasteForm(initial) {
    const box = $("#lyrics-inner");
    editing = true; ++lyricToken;
    $("#lyrics").classList.add("static");
    $("#ly-bar").hidden = true; tapSync = false; timing = null;
    box.style.transform = "";

    box.innerHTML = `<div class="mm-lyrics-paste"><textarea rows="9" placeholder="${esc(t("lyrics.paste_ph"))}" aria-label="${esc(t("lyrics.paste"))}"></textarea>
      <div class="mm-lyrics-acts">${lyrics && lyrics.synced ? `<button type="button" class="mm-chip glass" data-ly="end" title="${esc(t("lyrics.end_hint"))}">${icon("more")}${esc(t("lyrics.end"))}</button><span class="mm-grow"></span>` : ""}<button type="button" class="mm-chip glass" data-ly="cancel">${esc(t("lyrics.cancel"))}</button>
      <button type="button" class="mm-chip solid" data-ly="save">${icon("check")}${esc(t("lyrics.save"))}</button></div></div>`;
    const area = $("textarea", box);
    if (initial) { area.value = initial; area.setSelectionRange(0, 0); area.scrollTop = 0; }
    area.focus({ preventScroll: true });
    area.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); savePasted(area.value); } });
  }
  function editText() {
    if (!lyrics) return "";
    const original = lyrics.original || lyrics;
    if (original.synced) return lrcText(original.synced);
    return original.plain || "";
  }

  function parseLrc(text) {
    const out = [];
    for (const raw of String(text || "").split(/\r?\n/)) {
      const tags = [...raw.matchAll(/\[(\d{1,2}):(\d{2}(?:[.:]\d{1,3})?)\]/g)];
      if (!tags.length) continue;
      const line = raw.replace(/\[[^\]]*\]|<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>/g, "").replace(/\s+/g, " ").trim();
      tags.forEach((m) => out.push([Number(m[1]) * 60 + Number(m[2].replace(":", ".")), line]));
    }
    return out.sort((x, y) => x[0] - y[0]);
  }
  async function savePasted(text) {
    const tr = Player.current();
    const form = $(".mm-lyrics-paste");
    if (!tr || !form || form.dataset.saving) return;
    form.dataset.saving = "true";
    $$("button", form).forEach(button => { button.disabled = true; });
    try {
      const res = await API.call("lyrics.set", { id: tr.id, text: String(text) });
      if (!res) throw new Error(t("error.save"));
      if (Player.current()?.id === tr.id && form.isConnected) { editing = false; setLyrics(tr.id, res); }
      toast(t("lyrics.saved"));
    } catch (error) { toast(t("error.save") + ": " + String(error.message || error)); }
    finally { delete form.dataset.saving; $$("button", form).forEach(button => { button.disabled = false; }); }
  }

  function syncLyrics(cur, force) {
    if (!lyrics || !lyrics.synced || !timing || el.hidden || tab !== "lyrics") return;
    const now = lyricClock(cur), pos = LyricTiming.at(timing, now), idx = pos.line;
    const row = idx >= 0 ? timing.lines[idx].row : -1, key = pos.gap ? String(pos.gap.after) : "";
    if (row !== lyricIdx || key !== gapKey || force) {
      const changed = row !== lyricIdx;
      lyricIdx = row; gapKey = key; wordIdx = -1; gapEnd = false;
      lineNodes.forEach((n, i) => {
        n.classList.toggle("on", i === idx && !pos.gap); n.classList.toggle("past", i < idx || (i === idx && !!pos.gap));
        n.classList.toggle("just", i === idx && !!pos.gap);
        n.classList.toggle("next", i === idx + 1);
        n.classList.toggle("far", Math.abs(i - idx) > 3);
        if ((i !== idx || pos.gap) && n.classList.contains("words")) $$(".w.sung, .w.on", n).forEach((w) => w.classList.remove("sung", "on"));
      });
      gapNodes.forEach((g, after) => { g.classList.toggle("on", !!pos.gap && pos.gap.after === after); g.classList.remove("end"); });
      wordSpans = idx >= 0 && !pos.gap && lineNodes[idx].classList.contains("words") ? $$(".w", lineNodes[idx]) : [];

      if (changed || force || pos.gap) $("#lyrics-inner").style.transitionDuration = (Math.max(.3, .85 / Math.max(1, Player.st.speed || 1))).toFixed(2) + "s";
      positionLyrics();
    }
    if (pos.gap) {
      const g = gapNodes.get(pos.gap.after);
      if (g) {
        g.style.setProperty("--gp", pos.gapProgress.toFixed(3));
        const ending = pos.gapLeft < .55;
        if (ending !== gapEnd) { gapEnd = ending; g.classList.toggle("end", ending); }
      }
      return;
    }
    const node = lineNodes[idx];
    if (!node || syncShown === "line") return;
    const line = timing.lines[idx];
    if (!wordSpans.length) { node.style.setProperty("--lp", pos.progress.toFixed(3)); return; }
    const words = line.words;
    let w = -1;
    for (let k = 0; k < words.length && words[k].start <= now; k++) w = k;
    const live = w >= 0 && now < words[w].end ? w : -1;
    const sungTo = live >= 0 ? live : w + 1;
    if (w * 2 + (live >= 0 ? 1 : 0) !== wordIdx) {
      wordSpans.forEach((sp, k) => { sp.classList.toggle("sung", k < sungTo); sp.classList.toggle("on", k === live); });
      wordIdx = w * 2 + (live >= 0 ? 1 : 0);
    }
    if (live >= 0) wordSpans[live].style.setProperty("--wp", Math.max(0, Math.min(1, (now - words[live].start) / Math.max(.08, words[live].end - words[live].start))).toFixed(3));
  }
  function positionLyrics() {
    const box = $("#lyrics-inner"), holder = $("#lyrics");
    if (!timing || holder.classList.contains("static")) return;
    const idx = timing.lines.findIndex((ln) => ln.row === lyricIdx);
    const node = (gapKey !== "" && gapNodes.get(Number(gapKey))) || lineNodes[Math.max(0, idx)];
    if (!node) return;

    const y = node.offsetTop - holder.clientHeight * .34 + node.offsetHeight / 2;
    box.style.transform = `translateY(${(-y + manual).toFixed(1)}px)`;
  }
  $("#lyrics").addEventListener("click", (e) => {
    const ly = e.target.closest("[data-ly]");
    if (ly) {
      const tr = Player.current();
      if (ly.dataset.ly === "find" && tr) { toast(t("lyrics.searching")); loadLyrics(tr, true); }
      else if (ly.dataset.ly === "paste") pasteForm();
      else if (ly.dataset.ly === "cancel") { editing = false; if (lyrics?.state === "loading" && tr) loadLyrics(tr); else renderLyrics(); }
      else if (ly.dataset.ly === "save") savePasted($(".mm-lyrics-paste textarea").value);
      else if (ly.dataset.ly === "end") insertLineEnd($(".mm-lyrics-paste textarea"), Math.max(0, lyricClock(Player.times().cur)));
      else if (ly.dataset.ly === "ai" && tr && typeof MusicAI !== "undefined") MusicAI.lyrics(tr, res => { if (Player.current()?.id === tr.id) setLyrics(tr.id, res); });
      return;
    }
    const b = e.target.closest(".mm-lyric"); if (!b) return;

    if (tapSync && lyrics && lyrics.synced && Player.st.playing) {
      tapSync = false;
      setOffset(Player.heard() - globalOffset() - Number(b.dataset.t));
      toast(t("lyrics.offset_now", { n: (lyrics.offset > 0 ? "+" : "") + lyrics.offset.toFixed(2) }));
      return;
    }
    Player.seek(Number(b.dataset.t) + (lyrics ? lyrics.offset + globalOffset() : 0)); manual = 0; if (!Player.st.playing) Player.play();
  });
  $("#ly-bar").addEventListener("click", (e) => {
    const ly = e.target.closest("[data-ly]"); if (!ly) return;
    const kind = ly.dataset.ly;
    if (kind === "alts") altsMenu();
    else if (kind === "pick") choose({ index: Number(ly.dataset.i) });
    else if (kind === "next") { choose({ next: true }); toast(t("lyrics.next_variant")); }
    else if (kind === "off-" || kind === "off+") nudge((kind === "off-" ? -1 : 1) * (e.shiftKey ? .5 : .1));
    else if (kind === "off-reset") setOffset(0);
    else if (kind === "sync") Cfg.set({ lyrics_sync: syncMode() === "line" ? "smart" : "line" }).then(ok => { if (ok) renderLyrics(); else toast(t("error.save")); });
    else if (kind === "roman") { roman = !roman; renderBar(); applyRoman(); }
    else if (kind === "ai" && typeof MusicAI !== "undefined") { const tr = Player.current(); if (tr) MusicAI.lyrics(tr, res => { if (Player.current()?.id === tr.id) setLyrics(tr.id, res); }); }
    else if (kind === "find") { const tr = Player.current(); if (tr) loadLyrics(tr, true); }
    else if (kind === "tap") { tapSync = !tapSync; renderBar(); if (tapSync) toast(t("lyrics.tap_hint")); }
    else if (kind === "edit") pasteForm(editText());
  });
  $("#lyrics").addEventListener("wheel", (e) => {
    if (!lyrics || !lyrics.synced || syncShown === "off" || e.target.closest(".mm-lyrics-paste")) return;
    e.preventDefault(); manual -= e.deltaY; positionLyrics();
    clearTimeout(manualTimer); manualTimer = setTimeout(() => { manual = 0; positionLyrics(); }, 2500);
  }, { passive: false });

  function setTab(name) {
    tab = name;
    $$(".mm-np-tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $(".mm-np-tabs").dataset.on = name;
    $("#lyrics").hidden = name !== "lyrics"; $("#np-queue").hidden = name !== "queue";
    renderBar();
    if (name === "queue") renderQueue(); else { lyricIdx = -2; syncLyrics(Player.times().cur, true); }
    lyricLoop();
  }
  $(".mm-np-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); });
  $("#np-close").addEventListener("click", close);
  $("#np-like").addEventListener("click", () => { const tr = Player.current(); if (tr) toggleLike(tr); });
  $("#np-style").addEventListener("click", (e) => {
    const b = e.target.closest("[data-style]"); if (!b) return;
    Theme.set("npStyle", b.dataset.style);
  });
  let lastViz = Theme.ui.viz !== "off" ? Theme.ui.viz : "ring";
  $("#np-viz-toggle").addEventListener("click", () => { if (Theme.ui.viz !== "off") lastViz = Theme.ui.viz; Theme.set("viz", Theme.ui.viz === "off" ? lastViz : "off"); });
  el.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act) ({ play: Player.toggle, prev: Player.prev, next: Player.next, shuffle: () => Player.setShuffle(), repeat: Player.cycleRepeat })[act.dataset.act]();
    const pop = e.target.closest("[data-pop]");
    if (pop) openPop(pop.dataset.pop, pop);
  });
  function onSleep() {
    const st = Player.st;
    $("#sleep-label").textContent = st.sleepEnd ? t("sleep.end_short") : st.sleepAt ? t("sleep.left", { m: Math.max(1, Math.round((st.sleepAt - Date.now()) / 60000)) }) : t("sleep.off");
    $("[data-pop=sleep]").setAttribute("aria-pressed", String(!!(st.sleepAt || st.sleepEnd)));
  }
  function relabel() { onSleep(); if (lyrics && !lyrics.synced) renderLyrics(); else renderBar(); if (tab === "queue") renderQueue(); }

  function reloadLyrics(id) { const tr = Player.current(); if (tr && tr.id === id && !el.hidden) loadLyrics(tr); }
  return { open, close, isOpen, setColors, applyCalm, restyle, onState, onTrack, syncLyrics, nudge, onSleep, relabel, reloadLyrics, tab: () => tab };
})();

async function lyricsClipboardRead() {
  try { if (navigator.clipboard?.readText) return await navigator.clipboard.readText(); } catch (_) {}
  if (typeof Memu !== "undefined" && typeof Memu.paste === "function") return await Memu.paste();
  throw new Error("clipboard unavailable");
}
async function lyricsClipboardWrite(value) {
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return; } } catch (_) {}
  if (typeof Memu !== "undefined" && typeof Memu.copy === "function") { await Memu.copy(value); return; }
  throw new Error("clipboard unavailable");
}

// WebView2 disables its native menu in production. Keep editing commands available there too.
document.addEventListener("contextmenu", (event) => {
  const area = event.target.closest?.(".mm-lyrics-paste textarea, .mm-ed-lyrics");
  if (!area) return;
  event.preventDefault();
  document.querySelector(".mm-ly-edit-menu")?.remove();
  const start = area.selectionStart, end = area.selectionEnd;
  const menu = document.createElement("div");
  menu.className = "mm-ly-edit-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = [["undo", "lyrics.undo"], ["redo", "lyrics.redo"], ["cut", "lyrics.cut"], ["copy", "lyrics.copy"], ["paste", "lyrics.clip_paste"], ["select", "lyrics.select_all"]].map(([action, label]) => `<button type="button" role="menuitem" data-action="${action}">${esc(t(label))}</button>`).join("");
  document.body.appendChild(menu);
  menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, event.clientX)) + "px";
  menu.style.top = Math.max(8, Math.min(innerHeight - menu.offsetHeight - 8, event.clientY)) + "px";
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside, true); document.removeEventListener("keydown", key, true); };
  const outside = (e) => { if (!menu.contains(e.target)) close(); };
  const key = (e) => { if (e.key === "Escape") { close(); area.focus(); } };
  menu.addEventListener("click", async (e) => {
    const action = e.target.closest("[data-action]")?.dataset.action;
    if (!action) return;
    close(); area.focus(); area.setSelectionRange(start, end);
    if (action === "select") { area.select(); return; }
    if (action === "undo" || action === "redo") { document.execCommand(action); return; }
    if (action === "paste") {
      try {
        const value = await lyricsClipboardRead();
        area.focus(); area.setSelectionRange(start, end);
        if (!document.execCommand("insertText", false, value)) { area.setRangeText(value, start, end, "end"); area.dispatchEvent(new Event("input", { bubbles: true })); }
      } catch { toast(t("lyrics.paste_shortcut")); }
      return;
    }
    if (start === end) return;
    try {
      await lyricsClipboardWrite(area.value.slice(start, end));
      if (action === "cut") { area.focus(); area.setSelectionRange(start, end); document.execCommand("insertText", false, ""); }
    } catch { area.focus(); area.setSelectionRange(start, end); if (!document.execCommand(action)) toast(t("lyrics.clip_error")); }
  });
  document.addEventListener("pointerdown", outside, true);
  document.addEventListener("keydown", key, true);
});

function queueHtml() {
  const cur = Player.current(), up = Player.upcoming();
  if (!cur) return emptyState("music", t("queue.empty"), t("queue.empty_hint"));
  const row = (x, pos, now) => `<div class="mm-qrow${now ? " now" : ""}" data-pos="${pos}" data-id="${esc(x.id)}"${now ? "" : ' draggable="true"'}><span class="grip">${now ? `<i class="mm-eqmini"><i></i><i></i><i></i></i>` : icon("grip")}</span><span class="mm-art">${artImg(x)}</span><div style="min-width:0"><b>${esc(x.title)}</b><span>${esc(x.artist)}</span></div>${now ? "" : `<button type="button" class="mm-icon sm x" data-rm aria-label="${esc(t("queue.remove"))}">${icon("close")}</button>`}</div>`;
  return `<div class="mm-q-label">${esc(t("queue.now"))}</div>${row(cur, Player.st.index, true)}<div class="mm-q-label">${esc(up.length ? t("queue.next_from", { from: Player.st.from || "MemuMusic" }) : t("queue.nothing_next"))}</div>`
    + up.slice(0, 200).map((u) => row(u.track, u.pos, false)).join("");
}
function renderQueue() {
  const drawer = $("#queue-drawer");
  if (!drawer.hidden) $("#queue-list").innerHTML = queueHtml();
  if (!$("#np").hidden && NP.tab() === "queue") $("#np-queue").innerHTML = queueHtml();
}
function toggleQueueDrawer() {
  const d = $("#queue-drawer");
  d.hidden = !d.hidden;
  $("#open-queue").setAttribute("aria-pressed", String(!d.hidden));
  if (!d.hidden) renderQueue();
}
[$("#queue-list"), $("#np-queue")].forEach((box) => {
  bindReorder(box, ".mm-qrow[draggable]", (from, to) => Player.moveInQueue(from, to));
  box.addEventListener("click", (e) => {
    const row = e.target.closest(".mm-qrow"); if (!row) return;
    const pos = Number(row.dataset.pos);
    if (e.target.closest("[data-rm]")) { Player.removeFromQueue(pos); return; }
  });
  box.addEventListener("dblclick", (e) => {
    const row = e.target.closest(".mm-qrow"); if (!row || e.target.closest("button")) return;
    const pos = Number(row.dataset.pos);
    Player.moveInQueue(pos, Player.st.index + 1); Player.next();
  });
});
$("#queue-clear").addEventListener("click", () => Player.clearQueue());
$("[data-close=queue-drawer]").addEventListener("click", toggleQueueDrawer);

let popCleanup = null;
function closePop() { if (popCleanup) { popCleanup(); popCleanup = null; } $("#pop").hidden = true; }
function openPop(kind, anchor) {
  const pop = $("#pop");
  if (!pop.hidden && pop.dataset.kind === kind) { closePop(); return; }
  closeOverlays();
  pop.dataset.kind = kind;
  const st = Player.st;
  if (kind === "speed") {
    pop.innerHTML = `<h4>${icon("speed")}${esc(t("speed.title"))}</h4><div class="mm-pop-list">${[.5, .75, .9, 1, 1.1, 1.25, 1.5, 2].map((v) => `<button type="button" role="menuitemradio" data-v="${v}" aria-checked="${st.speed === v}">${v}×${st.speed === v ? icon("check") : ""}</button>`).join("")}</div>`;
    pop.onclick = (e) => { const b = e.target.closest("[data-v]"); if (b) { Player.setSpeed(Number(b.dataset.v)); closePop(); } };
  } else if (kind === "sleep") {
    const opts = [[0, t("sleep.off")], [5, t("sleep.min", { m: 5 })], [15, t("sleep.min", { m: 15 })], [30, t("sleep.min", { m: 30 })], [45, t("sleep.min", { m: 45 })], [60, t("sleep.hour")], [-1, t("sleep.end")]];
    pop.innerHTML = `<h4>${icon("moon")}${esc(t("sleep.title"))}</h4><div class="mm-pop-list">${opts.map(([v, label]) => `<button type="button" role="menuitemradio" data-v="${v}">${esc(label)}</button>`).join("")}</div>`;
    pop.onclick = (e) => { const b = e.target.closest("[data-v]"); if (!b) return; const v = Number(b.dataset.v); Player.setSleep(v > 0 ? v : 0, v === -1); toast(v ? t("sleep.set") : t("sleep.cancelled")); closePop(); };
  } else if (kind === "eq") {
    const label = (f) => f >= 1000 ? f / 1000 + "k" : String(f);
    pop.innerHTML = `<h4>${icon("eq")}${esc(t("eq.title"))}<span class="mm-grow"></span><span class="mm-pill" id="eq-name">${esc(t("eq.preset." + st.eqPreset))}</span></h4>
      <div class="mm-eq">${EQ_BANDS.map((f, i) => `<label><input type="range" min="-12" max="12" step="1" value="${st.eq[i] || 0}" data-band="${i}" aria-label="${label(f)} Hz"><span>${label(f)}</span></label>`).join("")}</div>
      <div class="mm-eq-presets">${Object.keys(EQ_PRESETS).map((k) => `<button type="button" class="mm-chip" data-preset="${k}" aria-pressed="${st.eqPreset === k}">${esc(t("eq.preset." + k))}</button>`).join("")}</div>`;
    pop.oninput = (e) => {
      const r = e.target.closest("[data-band]"); if (!r) return;
      const values = $$("[data-band]", pop).map((x) => Number(x.value));
      Player.setEq(values, "custom");
      $("#eq-name").textContent = t("eq.preset.custom");
      $$("[data-preset]", pop).forEach((b) => b.setAttribute("aria-pressed", "false"));
    };
    pop.onclick = (e) => {
      const b = e.target.closest("[data-preset]"); if (!b) return;
      Player.setEq(EQ_PRESETS[b.dataset.preset], b.dataset.preset);
      $$("[data-band]", pop).forEach((x, i) => { x.value = String(EQ_PRESETS[b.dataset.preset][i]); });
      $$("[data-preset]", pop).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      $("#eq-name").textContent = t("eq.preset." + b.dataset.preset);
    };
  }
  pop.hidden = false;
  const r = anchor.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(innerWidth - pw - 8, r.left + r.width / 2 - pw / 2)) + "px";
  pop.style.top = Math.max(8, r.top - ph - 10) + "px";
  const outside = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) closePop(); };
  setTimeout(() => document.addEventListener("pointerdown", outside, true));
  popCleanup = () => document.removeEventListener("pointerdown", outside, true);
}
