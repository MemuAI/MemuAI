/* MemuAI — Round 33 (r33-thinking): the reasoning levels each model really supports.
 *
 * The Proxy computes a profile per model (app/reasoning_levels.py) and ships it in
 * S.models[i].caps.reasoning_profile: {kind, levels, can_off, default, options, source, family, note}.
 * kind: "levels" (Auto + its own depths, + Off when it can be switched off), "toggle" (Auto / On / Off),
 * "fixed" (reasons, nothing to choose) or "none" (no reasoning). Every picker in the window uses this file,
 * so a model never offers a level its API rejects (Gemini 3.7 Flash: Low / Medium / High only).
 *
 * Without a profile (an old Proxy) the former name rule and the generic list are used.
 */
"use strict";
const MemuReasoning = (() => {
  const DEPTHS = ["minimal", "low", "medium", "high", "xhigh", "max"];
  const ALL = ["auto", "off", "on", ...DEPTHS];
  const ALIASES = { none: "off", disabled: "off", min: "minimal", enabled: "on", ultra: "max" };
  const LABELS = {
    auto: ["Авто", "Auto"], off: ["Выкл.", "Off"], on: ["Вкл.", "On"],
    minimal: ["Минимальный", "Minimal"], low: ["Низкий", "Low"], medium: ["Средний", "Medium"],
    high: ["Высокий", "High"], xhigh: ["Очень высокий", "Extra high"], max: ["Максимальный", "Max"],
  };
  const LEGACY_RE = /(?:gpt[-_/ ]?5|o[134](?:[-_/ ]|$)|claude[-_/ ]?(?:3|4|5)|gemini[-_/ ]?3|deepseek.*r1|qwen.*thinking|reasoning)/i;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");

  function normalize(value) {
    const text = String(value == null ? "" : value).trim().toLowerCase();
    const mapped = ALIASES[text] || text;
    return ALL.includes(mapped) ? mapped : "auto";
  }
  function label(level) {
    const pair = LABELS[normalize(level)] || LABELS.auto;
    return en() ? pair[1] : pair[0];
  }
  function model(id) {
    return (typeof S !== "undefined" && Array.isArray(S.models)) ? S.models.find((m) => m.id === id) : null;
  }
  /** The profile of a model; null when the Proxy did not send one (old Proxy). */
  function profile(id) {
    const prof = model(id)?.caps?.reasoning_profile;
    return prof && typeof prof === "object" && Array.isArray(prof.options) ? prof : null;
  }
  /** Picker options for a model, "auto" first; [] — the model has nothing to choose. */
  function options(id) {
    const prof = profile(id);
    if (prof) return prof.options.slice();
    const caps = model(id)?.caps || {};
    if (caps.thinking || caps.effort || LEGACY_RE.test(String(id || ""))) return ["auto", "minimal", "low", "medium", "high", "max"];
    return [];
  }
  function supports(id) { return options(id).length > 0; }

  /** A stored / requested level → the nearest level this model accepts (mirror of reasoning_levels.clamp). */
  function clamp(level, id) {
    level = normalize(level);
    const prof = profile(id);
    if (!prof) return supports(id) ? level : "auto";
    if (level === "auto" || !["levels", "toggle"].includes(prof.kind)) return "auto";
    const levels = DEPTHS.filter((x) => (prof.levels || []).includes(x));
    if (level === "off") return prof.can_off ? "off" : (levels[0] || "auto");
    if (prof.kind === "toggle") return prof.default === "on" ? "auto" : "on";
    if (level === "on") {
      if (levels.includes(prof.default)) return prof.default;
      return levels.includes("medium") ? "medium" : (levels[Math.floor(levels.length / 2)] || "auto");
    }
    if (levels.includes(level)) return level;
    if (!levels.length) return "auto";
    if (level === "minimal" && prof.can_off) return "off";
    if (level === "max") return levels[levels.length - 1];
    const index = DEPTHS.indexOf(level);
    return levels.slice().sort((a, b) => {
      const da = Math.abs(DEPTHS.indexOf(a) - index), db = Math.abs(DEPTHS.indexOf(b) - index);
      return da - db || DEPTHS.indexOf(b) - DEPTHS.indexOf(a);
    })[0];
  }

  /** «Auto» names what the model does by itself when the provider documents it: «Auto · Medium». */
  function autoLabel(id) {
    const prof = profile(id);
    const base = label("auto");
    if (!prof || !prof.default) return base;
    const short = { max: ["макс.", "max"], minimal: ["мин.", "minimal"], xhigh: ["очень высокий", "extra high"] }[prof.default];
    return `${base} · ${short ? (en() ? short[1] : short[0]) : label(prof.default).toLowerCase()}`;
  }
  function optionLabel(level, id) { return normalize(level) === "auto" ? autoLabel(id) : label(level); }

  /** <option> list for a <select> of this model. */
  function optionsHtml(id, esc = (x) => x) {
    return options(id).map((value) => `<option value="${value}">${esc(optionLabel(value, id))}</option>`).join("");
  }
  /** Short explanation for tooltips and the settings rows. */
  function hint(id) {
    const prof = profile(id);
    if (!prof) return "";
    const list = (prof.levels || []).map(label).join(", ");
    const parts = [];
    if (prof.kind === "toggle") parts.push(en() ? "Reasoning can only be switched on or off" : "Размышления можно только включить или выключить");
    else if (list) parts.push((en() ? "Levels: " : "Уровни: ") + list);
    if (prof.kind === "levels" && !prof.can_off) parts.push(en() ? "cannot be switched off" : "выключить нельзя");
    if (prof.default) parts.push((en() ? "by default — " : "по умолчанию — ") + label(prof.default).toLowerCase());
    if (prof.source === "guess") parts.push(en() ? "assumed for an unknown model (set it in the Proxy model card)" : "предположение для незнакомой модели (уточните в карточке модели в Proxy)");
    if (prof.source === "override") parts.push(en() ? "set by hand in the Proxy" : "задано вручную в Proxy");
    return parts.join(" · ");
  }
  /** One short line under a model name: «Low · Medium · High · cannot be switched off». */
  function shortHint(id) {
    const prof = profile(id);
    if (!prof) return "";
    if (prof.kind === "toggle") return en() ? "On / off only" : "Только вкл. / выкл.";
    const parts = (prof.levels || []).map(label);
    if (prof.can_off) parts.unshift(label("off"));
    else parts.push(en() ? "cannot be switched off" : "не выключается");
    if (prof.source === "guess") parts.push(en() ? "assumed" : "предположение");
    return parts.join(" · ");
  }
  /** A generic list for settings that apply to every model (each model then gets its nearest level). */
  function genericHtml(esc = (x) => x) {
    return ["auto", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
      .map((v) => `<option value="${v}">${esc(label(v))}</option>`).join("");
  }
  return { DEPTHS, ALL, normalize, label, profile, options, supports, clamp, optionLabel, autoLabel, optionsHtml, hint, shortHint, genericHtml };
})();
window.MemuReasoning = MemuReasoning;
