

(function () {
  "use strict";
  const Core = window.MemuCompanionCore;
  if (!Core || !window.MemuCompanion) return;
  let inst = null, placement = "", greet = false, live = false, analyserAttached = null, speakingAudio = null;
  const prefOf = (key, fallback) => (typeof pref === "function" ? pref(key, fallback) : fallback);
  const allPrefs = () => (typeof S !== "undefined" && S.preferences) || {};
  const lang = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en" ? "en" : "ru");
  const panel = () => document.getElementById("live-call");

  function enabled() { return !(window.MEMU_SAFE_MODE && window.MEMU_SAFE_MODE.active) && !!prefOf("companion_live_enabled", false); }

  function destroy() {
    if (inst) inst.destroy();
    inst = null; placement = ""; analyserAttached = null;
    panel()?.classList.remove("has-companion");
  }

  function ensure() {
    if (!enabled() || !live) { destroy(); return null; }
    const wanted = prefOf("companion_live_placement", "panel") === "window" ? "window" : "panel";
    if (inst && placement === wanted) return inst;
    destroy();
    placement = wanted;
    const host = panel();
    if (!host) return null;
    const floating = wanted === "window";
    const container = floating ? document.body : host.querySelector(".live-call-body");
    inst = MemuCompanion.create({
      container: container, surface: "live", lang: lang(), bubble: floating, movable: floating, prefs: allPrefs,
      onEvent: (type) => {
        if (type === "loaded") {
          host.classList.toggle("has-companion", !floating);
          if (greet) { greet = false; inst && inst.gesture("wave"); }
        } else if (type === "error") {

          host.classList.remove("has-companion");
          if (!floating && inst) inst.el.hidden = true;
        } else if (type === "click") {
          if (typeof MemuRealtime !== "undefined" && MemuRealtime.active && MemuRealtime.state === "speaking") MemuRealtime.interrupt();
        }
      },
      onMove: (pos) => {
        fetch("/api/companion/state", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ live_window: pos }) }).catch(() => {});
      },
      onResize: (size) => { if (typeof savePreferences === "function") savePreferences({ companion_size: size }).then(() => inst && inst.setPrefs()); },
    });
    if (floating) {
      inst.el.classList.add("floating");
      fetch("/api/companion/state").then((r) => r.json()).then((state) => { if (inst && state.live_window) inst.placeRelative(state.live_window); }).catch(() => {});
    } else {
      inst.el.classList.add("in-live");
      const orb = host.querySelector(".live-orb");
      if (orb) container.insertBefore(inst.el, orb);
    }
    return inst;
  }

  function attachRealtime() {
    if (!inst || typeof MemuRealtime === "undefined" || !MemuRealtime.active) return;
    const node = MemuRealtime.analyser;
    if (node && node !== analyserAttached && prefOf("companion_lipsync", true)) {
      analyserAttached = node;
      inst.attachAnalyser(node);
    }
  }

  const STATE = { working: "thinking", thinking: "thinking", connecting: "thinking", listening: "listening",
    speaking: "speaking", ready: "idle", idle: "idle", error: "idle" };

  function emit(type, data) {
    data = data || {};
    switch (type) {
      case "open":
        live = true; greet = true;
        if (ensure()) { inst.setVisible(true); if (inst.loaded) { greet = false; inst.gesture("wave"); } }
        break;
      case "close":
        live = false; destroy();
        break;
      case "minimize": if (inst) inst.setVisible(false); break;
      case "restore": if (inst) inst.setVisible(true); break;
      case "state": {
        if (!inst) return;
        const state = STATE[data.state] || "idle";
        const previous = inst.el.dataset.state;
        inst.state(state);
        if (state === "speaking" && previous !== "speaking") inst.gesture("nod");
        if (data.state === "error") inst.gesture("shake");
        attachRealtime();
        break;
      }
      case "caption":
        if (inst && data.role === "assistant") inst.text(data.text, false);
        break;
      case "turn":
        if (inst && data.role === "assistant") inst.text(data.text, true);
        break;
      case "audio": {

        if (!inst || !data.audio) return;
        const audio = data.audio;
        speakingAudio = audio;
        inst.attachAudio(audio);
        inst.text(data.text || "", true);
        inst.speak(true, data.text || "");

        const done = () => {
          if (speakingAudio !== audio) return;
          speakingAudio = null;
          if (inst) { inst.speak(false); inst.detachAudio(); inst.state("idle"); }
        };
        audio.addEventListener("ended", done, { once: true });
        audio.addEventListener("error", done, { once: true });
        audio.addEventListener("pause", () => { if (audio.ended || audio.currentTime > 0) done(); }, { once: true });
        break;
      }
    }
  }

  function promptHint() {
    return enabled() && prefOf("companion_emotions", true) && prefOf("companion_emotion_tags", true)
      ? "\n" + Core.promptHint(lang() === "en") : "";
  }

  function refresh() {
    if (!live) return;
    if (!enabled()) { destroy(); return; }
    const wanted = prefOf("companion_live_placement", "panel") === "window" ? "window" : "panel";
    if (!inst || placement !== wanted) { ensure(); return; }
    inst.setPrefs();
  }

  window.MemuCompanionLive = { emit: emit, promptHint: promptHint, refresh: refresh, strip: (text) => Core.stripTags(text),
    get instance() { return inst; } };
})();
