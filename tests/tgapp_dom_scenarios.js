// In-page scenarios for the jsdom harness. Runs against the REAL tgapp code
// (app.js / music.js / panels.js) with fixture data from the prelude.
// Assertions target the fixed behaviour; on unfixed code they fail with the
// bug's observable symptoms (sheet pile-up, empty clock, no reorder).
(function () {
  "use strict";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const sheetCount = () => $$("#sheet-root .sheet").length;
  function zOf(el) {
    if (!el) return NaN;
    try { const v = parseFloat(getComputedStyle(el).zIndex); if (Number.isFinite(v)) return v; } catch (e) { /* fall through */ }
    return NaN;
  }
  function cssHas(pattern) { return pattern.test(window.__cssText || ""); }

  async function bootMusic() {
    const app = document.getElementById("app"); app.hidden = false;
    const boot = document.getElementById("boot"); if (boot) boot.remove();
    const kit = window.MemuTgApp.kit; kit.S.tab = "music";
    kit.hooks.afterBoot.forEach((fn) => { try { fn(); } catch (e) { window.__errors.push("afterBoot: " + e.message); } });
    const view = kit.rootView();
    kit.tabViews.music(view);
    await sleep(400);
    return kit;
  }

  async function scenarioMenu() {
    const out = [];
    const M = window.MemuMusicTg;
    await M.playQueue([window.__T1, window.__T2], 0);
    if (M.state.audio) M.state.audio._dur = 130;
    await sleep(300);
    M.openFull(); await sleep(150);
    const more = $(".mm-more");
    if (!more) return [{ name: "menu: more button exists", pass: false, details: "no .mm-more" }];
    more.click(); await sleep(120); more.click(); await sleep(120); more.click(); await sleep(150);
    const n = sheetCount();
    out.push({ name: "menu: triple tap on ... opens exactly one menu", pass: n === 1, details: "sheets=" + n });
    const sh = $(".sheet"), full = $(".mm-full");
    const zOk = sh && full && zOf(sh) > zOf(full);
    const cssOk = cssHas(/\.mm-full-open\s+\.sheet[^{]*\{[^}]*z-index:\s*7\d/) || cssHas(/\.sheet[^{]*\{[^}]*z-index:\s*7\d/);
    out.push({ name: "menu: sheet stacks above the full player", pass: !!(zOk || cssOk), details: "sheetZ=" + zOf(sh) + " fullZ=" + zOf(full) + " cssRule=" + cssOk });
    const closeBtn = $(".mm-close");
    if (closeBtn) closeBtn.click();
    await sleep(600);
    out.push({ name: "menu: closing the player leaves no hanging menu", pass: sheetCount() === 0 && !$(".mm-full"), details: "sheets=" + sheetCount() + " full=" + !!$(".mm-full") });
    return out;
  }

  async function scenarioMenuItem() {
    const out = [];
    const M = window.MemuMusicTg;
    await M.playQueue([window.__T1, window.__T2], 0);
    if (M.state.audio) M.state.audio._dur = 130;
    await sleep(250);
    M.openFull(); await sleep(150);
    $(".mm-more").click(); await sleep(150);
    const before = window.__apiLog.filter((x) => x === "music.like").length;
    const likeCell = $$("#sheet-root .cell").find((b) => /любимые/i.test(b.textContent));
    if (!likeCell) { out.push({ name: "menu item: like cell exists", pass: false, details: "cells=" + $$("#sheet-root .cell").length }); return out; }
    likeCell.click(); await sleep(450); // sheet DOM removal is delayed 320 ms for the close animation
    const likes = window.__apiLog.filter((x) => x === "music.like").length - before;
    out.push({ name: "menu item: like fires exactly once, no sheet left, player stays", pass: likes === 1 && sheetCount() === 0 && !!$(".mm-full"), details: "likes=" + likes + " sheets=" + sheetCount() + " full=" + !!$(".mm-full") });
    $(".mm-more").click(); await sleep(150);
    const artistCell = $$("#sheet-root .cell").find((b) => /исполнителю/i.test(b.textContent));
    if (!artistCell) { out.push({ name: "menu item: artist cell exists", pass: false, details: "" }); }
    else {
      artistCell.click(); await sleep(400);
      out.push({ name: "menu item: go-to-artist opens stack and leaves no sheet", pass: sheetCount() === 0 && !!$(".view.stack"), details: "sheets=" + sheetCount() + " stack=" + !!$(".view.stack") });
      window.MemuTgApp.kit.goBack(); await sleep(200);
    }
    if ($(".mm-full")) { window.MemuMusicTg.closeFull(); await sleep(500); }
    out.push({ name: "menu item: player closed cleanly afterwards", pass: !$(".mm-full") && sheetCount() === 0, details: "full=" + !!$(".mm-full") + " sheets=" + sheetCount() });
    return out;
  }

  async function scenarioLyrics() {
    const out = [];
    const M = window.MemuMusicTg;
    await M.playQueue([window.__T1, window.__T2], 0);
    if (M.state.audio) M.state.audio._dur = 130;
    await sleep(250);
    M.openFull(); await sleep(150);
    $(".mm-tab-lyrics").click(); await sleep(350);
    const lines = $$(".mm-ly");
    out.push({ name: "lyrics: synced lines render", pass: lines.length === 3, details: "lines=" + lines.length });
    const bar = $(".mm-ly-bar");
    const hasRoman = bar && !!$('[data-ly="roman"]', bar);
    const hasOff = bar && !!$('[data-ly="off+"]', bar);
    out.push({ name: "lyrics: toolbar with romanize/offset/edit exists", pass: !!bar && !!hasRoman && !!hasOff, details: "bar=" + !!bar + " roman=" + !!hasRoman + " off=" + !!hasOff });
    if (bar) {
      $('[data-ly="roman"]', bar).click(); await sleep(120);
      const ls = $$(".mm-ly");
      out.push({ name: "lyrics: romanize converts kana/hangul", pass: /konnichiha/i.test(ls[0].textContent) || /sarang/i.test(ls[1].textContent), details: "line1=" + ls[0].textContent + " line2=" + ls[1].textContent });
      $('[data-ly="roman"]', bar).click(); await sleep(120);
      out.push({ name: "lyrics: romanize toggles back to original", pass: /こんにちは/.test($(".mm-ly").textContent), details: $(".mm-ly").textContent });
      const a = M.state.audio; a.currentTime = 2.05; a.dispatchEvent(new Event("timeupdate")); await sleep(80);
      const activeBefore = $(".mm-ly.on") && $(".mm-ly.on").textContent;
      const plus = $('[data-ly="off+"]', bar);
      plus.click(); plus.click(); await sleep(80);
      a.currentTime = 2.05; a.dispatchEvent(new Event("timeupdate")); await sleep(80);
      const activeAfter = $(".mm-ly.on") && $(".mm-ly.on").textContent;
      const resetLabel = $('[data-ly="off-reset"]', bar) && $('[data-ly="off-reset"]', bar).textContent;
      out.push({ name: "lyrics: offset shift moves the active line", pass: activeBefore !== activeAfter && /こんにちは/.test(activeAfter || ""), details: "before=" + activeBefore + " after=" + activeAfter + " offLabel=" + resetLabel });
      const reset = $('[data-ly="off-reset"]', bar); if (reset) reset.click(); await sleep(80);
      a.currentTime = 4.3; a.dispatchEvent(new Event("timeupdate")); await sleep(80);
      const words = $$(".mm-ly-w");
      const activeWord = $(".mm-ly-w.on");
      out.push({ name: "lyrics: word-level karaoke highlights the current word", pass: words.length >= 2 && !!activeWord && activeWord.textContent === "Hello", details: "words=" + words.length + " active=" + (activeWord && activeWord.textContent) });
      const edit = $('[data-ly="edit"]', bar);
      if (!edit) out.push({ name: "lyrics: edit button exists", pass: false, details: "" });
      else {
        edit.click(); await sleep(450);
        const input = $("#ps-input");
        if (!input) out.push({ name: "lyrics: manual lyrics editor opens", pass: false, details: "no #ps-input" });
        else {
          input.value = "[00:01.00] Своя строка";
          const act = $("[data-act]"); if (act) act.click(); await sleep(350);
          out.push({ name: "lyrics: pasted LRC replaces the text", pass: $$(".mm-ly").some((p) => /Своя строка/.test(p.textContent)), details: $$(".mm-ly").map((p) => p.textContent).join(" | ") });
        }
      }
      // volume + speed controls in the full player (phone mode)
      const vol = $(".mm-vol input", $(".mm-full"));
      out.push({ name: "player: phone volume control exists", pass: !!vol, details: "vol=" + !!vol });
      if (vol) { vol.value = "50"; vol.dispatchEvent(new Event("input", { bubbles: true })); await sleep(60); out.push({ name: "player: volume changes audio volume", pass: Math.abs(M.state.audio.volume - 0.5) < 0.01, details: "volume=" + M.state.audio.volume }); }
      const speed = $(".mm-speed", $(".mm-full"));
      out.push({ name: "player: speed button exists", pass: !!speed, details: "" });
      // queue management
      $(".mm-tab-queue").click(); await sleep(150);
      const qx = $(".mm-q-x", $(".mm-full"));
      out.push({ name: "queue: remove-from-queue control exists", pass: !!qx, details: "queueRows=" + $$(".mm-q", $(".mm-full")).length });
    }
    if ($(".mm-full")) { M.closeFull(); await sleep(500); }
    // pure helpers (exposed for tests)
    const R = window.MemuMusicTg;
    if (R && R.romanizeText) {
      out.push({ name: "romanize: hangul fixture", pass: R.romanizeText("사랑해요") === "saranghaeyo", details: R.romanizeText("사랑해요") });
      out.push({ name: "romanize: kana fixture", pass: R.romanizeText("がっこう") === "gakkou" && R.romanizeText("ありがとう") === "arigatou", details: R.romanizeText("がっこう") + " / " + R.romanizeText("ありがとう") });
      out.push({ name: "romanize: kanji is left as-is", pass: R.romanizeText("東京") === "東京", details: R.romanizeText("東京") });
      const parsed = R.parseLrc("[00:01.00] one\n[00:02.50] two\nplain");
      out.push({ name: "parseLrc: timestamps parsed, plain lines kept", pass: Array.isArray(parsed) && parsed.length === 3 && parsed[0][0] === 1 && parsed[1][0] === 2.5, details: JSON.stringify(parsed) });
    } else {
      out.push({ name: "romanize: helpers exposed", pass: false, details: "MemuMusicTg.romanizeText missing" });
    }
    return out;
  }

  async function scenarioPanels() {
    const out = [];
    const kit = window.MemuTgApp.kit;
    const helpers = { api: async (name, body) => { const r = await window.fetch("api/" + name, { method: "POST", body: JSON.stringify(body || {}) }); if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }, esc: kit.esc, tr: kit.tr, toast: kit.toast, haptic: kit.haptic, setTop: () => {}, icon: kit.icon, state: { tab: "panels", stack: [] }, timers: {} };
    const view = document.createElement("div"); view.className = "view root"; $("#views").appendChild(view);
    window.MemuTgPanels.render(view, helpers);
    await sleep(350);
    const clock = $(".pn-clock-time", view);
    const dateEl = $(".pn-clock-date", view);
    out.push({ name: "panels: clock shows time and date", pass: !!clock && /^\d{2}:\d{2}:\d{2}$/.test(clock.textContent.trim()) && !!dateEl && dateEl.textContent.trim().length > 3, details: "time=" + (clock && clock.textContent) + " date=" + (dateEl && dateEl.textContent) });
    const t0 = clock && clock.textContent;
    await sleep(1200);
    out.push({ name: "panels: clock ticks every second", pass: !!clock && clock.textContent !== t0, details: "t0=" + t0 + " t1=" + (clock && clock.textContent) });
    const wx = $$(".pn-card", view).find((c) => /Погода/.test(c.textContent));
    out.push({ name: "panels: other panels still render their data", pass: !!wx && /\+18/.test(wx.textContent), details: wx ? wx.textContent.slice(0, 60) : "no weather card" });
    const timers = $$(".pn-card", view).find((c) => /Таймеры/.test(c.textContent));
    out.push({ name: "panels: timers panel has a specific empty state", pass: !!timers && !/Нет данных/.test(timers.textContent), details: timers ? timers.textContent.slice(0, 90) : "no timers card" });
    const ids0 = $$(".pn-card", view).map((c) => c.dataset.panel);
    const firstCard = $(".pn-card", view);
    const down = firstCard && $('[data-move="1"]', firstCard);
    if (!down) out.push({ name: "panels: move-down button exists", pass: false, details: "ids=" + ids0.join(",") });
    else {
      down.click(); await sleep(120);
      const ids1 = $$(".pn-card", view).map((c) => c.dataset.panel);
      const stored = JSON.parse(localStorage.getItem("memu_tg_panels_order") || "null");
      out.push({ name: "panels: down button swaps order and persists it", pass: ids1[1] === ids0[0] && Array.isArray(stored) && stored[1] === ids0[0], details: "before=" + ids0.join(",") + " after=" + ids1.join(",") + " stored=" + JSON.stringify(stored) });
      clearInterval(helpers.timers.panels);
      window.MemuTgPanels.render(view, helpers); await sleep(300);
      const ids2 = $$(".pn-card", view).map((c) => c.dataset.panel);
      out.push({ name: "panels: saved order survives re-render", pass: ids2.join(",") === ids1.join(","), details: ids2.join(",") });
      const cards = $$(".pn-card", view);
      const handle = cards[0] && $(".pn-drag", cards[0]);
      if (handle) {
        const dt = new DataTransfer();
        handle.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: dt }));
        cards[2].dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
        cards[2].dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
        handle.dispatchEvent(new DragEvent("dragend", { bubbles: true, cancelable: true, dataTransfer: dt }));
        await sleep(150);
        const ids3 = $$(".pn-card", view).map((c) => c.dataset.panel);
        out.push({ name: "panels: drag moves the card", pass: ids3[0] !== ids1[0], details: "before=" + ids1.join(",") + " after=" + ids3.join(",") });
      } else out.push({ name: "panels: drag handle exists", pass: false, details: "" });
    }
    window.__fx.panelsFail = true;
    clearInterval(helpers.timers.panels);
    window.MemuTgPanels.render(view, helpers); await sleep(300);
    const retry = $("[data-pn-retry]", view);
    out.push({ name: "panels: load error shows a retry control, no crash", pass: !!retry, details: view.textContent.slice(0, 80) });
    if (retry) { window.__fx.panelsFail = false; retry.click(); await sleep(350); out.push({ name: "panels: retry reloads the panels", pass: $$(".pn-card", view).length >= 4, details: "cards=" + $$(".pn-card", view).length }); }
    view.remove();
    Object.values(helpers.timers).forEach(clearInterval);
    return out;
  }

  async function scenarioGuards() {
    const out = [];
    const errorsBefore = window.__errors.length;
    const MM = window.MemuMusicTg; if (MM) { MM.state.home = null; MM.state.playlists = null; }
    window.__fx.musicHomeFail = true;
    const kit = window.MemuTgApp.kit; kit.S.tab = "music";
    const view = kit.rootView(); view.innerHTML = "";
    kit.tabViews.music(view); await sleep(400);
    out.push({ name: "guards: music.home failure renders an offline empty state, no exception", pass: /не отвечает|Нет связи|компьютер/i.test(view.textContent) && window.__errors.length === errorsBefore, details: view.textContent.slice(0, 90) + " errors=" + (window.__errors.length - errorsBefore) });
    window.__fx.musicHomeFail = false;
    document.documentElement.dataset.scheme = "light";
    out.push({ name: "guards: light scheme applies without errors", pass: document.documentElement.dataset.scheme === "light" && window.__errors.length === errorsBefore, details: "" });
    out.push({ name: "guards: no unhandled exceptions across all scenarios", pass: window.__errors.length === 0, details: window.__errors.join(" ;; ") });
    return out;
  }

  window.__runAll = async () => {
    const results = [];
    try { await bootMusic(); } catch (e) { return [{ name: "boot", pass: false, details: String((e && e.stack) || e) }]; }
    for (const fn of [scenarioMenu, scenarioMenuItem, scenarioLyrics, scenarioPanels, scenarioGuards]) {
      try { results.push(...await fn()); } catch (e) { results.push({ name: fn.name, pass: false, details: "THREW " + String((e && e.stack) || e) }); }
    }
    results.push({ name: "harness: window errors", pass: window.__errors.length === 0, details: window.__errors.join(" ;; ") || "none" });
    return results;
  };
})();
