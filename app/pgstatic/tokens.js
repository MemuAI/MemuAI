/* MemuAI — сколько токенов стоит текст: честная оценка с учётом языка.
 *
 * JS-двойник app/token_estimate.py: те же признаки (латинские и кириллические
 * буквы и слова, цифры, знаки, переводы строк, пробелы, отступы, CJK, хангыль,
 * прочее) и та же таблица коэффициентов по семействам моделей, откалиброванная
 * по настоящим токенизаторам (o200k, Gemma, Qwen, DeepSeek, Llama, Mistral,
 * GLM, MiniMax; Claude — приближённо). Таблицу правят в обоих файлах сразу —
 * tests/round30_misc_test.py сверяет их и проверяет одинаковые числа.
 *
 *   MemuTokens.estimate(text, model?)  — типичное число (медиана семейств) или
 *                                        число семейства модели;
 *   MemuTokens.breakdown(text)         — {tokens, low, high, families[], mix};
 *   MemuTokens.labelOf(model)          — «Claude 3 – 4.6» (или "" — тогда считается медиана);
 *   MemuTokens.approx(n) / short(n)    — 84 300 / «84 тыс.»: у оценки нет ложной точности.
 *
 * Без состояния приложения: работает в окне MemuAI, в Node (тесты) и где угодно.
 */
"use strict";
const MemuTokens = (() => {
  const FEATURES = ["latw", "lat", "cyrw", "cyr", "dig", "punct", "nl", "ws", "wsrun", "cjk", "hang", "other"];
  /* TABLE-BEGIN */
  const FAMILIES = [
    { id: "gpt", label: "GPT-4o / GPT-5", coef: [0.484, 0.079, 1.053, 0.075, 1.033, 0.529, 0.438, 0.320, 0.046, 0.794, 0.673, 0.544] },
    { id: "claude", label: "Claude 3 – 4.6", coef: [0.724, 0.111, 0.907, 0.338, 1.180, 0.679, 0.721, 0.046, 0.010, 0.916, 1.195, 0.523] },
    { id: "claude-new", label: "Claude Opus 4.7+", coef: [1.075, 0.165, 0.911, 0.339, 1.380, 0.794, 1.069, 0.068, 0.015, 0.920, 1.200, 0.526] },
    { id: "gemini", label: "Gemini / Gemma", coef: [0.672, 0.090, 1.220, 0.067, 1.512, 0.625, 0.956, 0.063, 0.097, 0.519, 0.665, 0.747] },
    { id: "qwen", label: "Qwen 3", coef: [0.485, 0.083, 0.977, 0.175, 1.562, 0.497, 0.432, 0.310, 0.049, 0.684, 0.801, 0.418] },
    { id: "deepseek", label: "DeepSeek V3 / R1", coef: [0.498, 0.080, 1.370, 0.068, 0.953, 0.589, 0.338, 0.322, 0.078, 0.649, 0.771, 0.227] },
    { id: "llama", label: "Llama 3 / 4", coef: [0.564, 0.069, 0.957, 0.148, 1.066, 0.515, 0.411, 0.288, 0.043, 0.714, 0.715, 0.920] },
    { id: "mistral", label: "Mistral", coef: [0.372, 0.123, 1.167, 0.106, 1.513, 0.565, 0.342, 0.214, 0.066, 0.776, 0.649, 1.204] },
    { id: "glm", label: "GLM", coef: [0.529, 0.068, 1.106, 0.075, 1.145, 0.504, 0.448, 0.345, 0.046, 0.717, 0.974, 0.334] },
    { id: "minimax", label: "MiniMax", coef: [0.450, 0.081, 1.209, 0.112, 0.907, 0.566, 0.378, 0.325, 0.063, 0.414, 0.567, 0.921] },
  ];
  /* TABLE-END */
  const BY_ID = Object.fromEntries(FAMILIES.map((row) => [row.id, row]));

  const LATIN_WORD = /[A-Za-z\u00C0-\u024F]+/g;
  const CYRILLIC_WORD = /[\u0400-\u04FF]+/g;
  const DIGIT = /[0-9]/g;
  const PUNCT = /[!-/:-@[-`{-~]/g;
  const WS_RUN = /[ \t]{2,}/g;
  const CJK = /[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]/g;
  const HANGUL = /[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]/g;
  const SURROGATE = /[\uD800-\uDBFF]/g;      // эмодзи и прочее вне BMP: одна буква, а в JS — две единицы

  const count = (text, re) => { const m = text.match(re); return m ? m.length : 0; };
  const sumLength = (text, re) => { let n = 0, words = 0; for (const m of text.match(re) || []) { n += m.length; words += 1; } return [words, n]; };

  function features(value) {
    const text = String(value ?? "");
    if (!text) return FEATURES.map(() => 0);
    const [latw, lat] = sumLength(text, LATIN_WORD);
    const [cyrw, cyr] = sumLength(text, CYRILLIC_WORD);
    const nl = count(text, /\n/g);
    const spaces = count(text, /[ \t]/g);
    let run = 0;
    for (const m of text.match(WS_RUN) || []) run += m.length - 1;
    const dig = count(text, DIGIT), punct = count(text, PUNCT);
    // eslint-disable-next-line no-control-regex
    const ascii = /^[\x00-\x7f]*$/.test(text);
    const cjk = ascii ? 0 : count(text, CJK), hang = ascii ? 0 : count(text, HANGUL);
    const length = text.length - (ascii ? 0 : count(text, SURROGATE));
    const known = lat + cyr + dig + punct + nl + spaces + cjk + hang;
    return [latw, lat, cyrw, cyr, dig, punct, nl, spaces - run, run, cjk, hang, Math.max(0, length - known)];
  }

  const apply = (coef, counts) => coef.reduce((sum, c, i) => sum + c * counts[i], 0);
  const perFamily = (counts) => FAMILIES.map((row) => apply(row.coef, counts));
  function median(values) {
    const sorted = [...values].sort((a, b) => a - b);
    const mid = sorted.length >> 1;
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function familyOf(model) {
    const name = String(model || "").toLowerCase();
    if (!name) return "";
    if (/claude|anthropic|opus|sonnet|haiku/.test(name)) {
      return /(opus|sonnet|haiku)[-_ ]?(4[-_.][7-9]|[5-9])|claude[-_ ]?[5-9]/.test(name) ? "claude-new" : "claude";
    }
    const rules = [[/gemini|gemma/, "gemini"], [/qwen|qwq/, "qwen"], [/deepseek/, "deepseek"], [/llama/, "llama"],
      [/mistral|mixtral|codestral|devstral|magistral|ministral/, "mistral"], [/glm|zhipu|z-ai/, "glm"], [/minimax/, "minimax"],
      [/gpt|openai|codex|(^|[-/_])o\d|chatgpt/, "gpt"]];
    for (const [re, id] of rules) if (re.test(name)) return id;
    return "";
  }

  // Разговор пересчитывается на каждый запрос, а почти весь он — те же строки: длинные считаем один раз.
  const memo = new Map();
  const MEMO_FROM = 400, MEMO_LIMIT = 1500;

  function estimate(value, model) {
    if (!value) return 0;
    const text = String(value);
    const family = familyOf(model);
    const key = family ? family + "\0" + text : text;
    const long = text.length >= MEMO_FROM;
    if (long && memo.has(key)) return memo.get(key);
    const counts = features(text);
    const raw = family ? apply(BY_ID[family].coef, counts) : median(perFamily(counts));
    const result = Math.max(1, Math.ceil(raw - 1e-9));
    if (long) {
      if (memo.size >= MEMO_LIMIT) { let drop = Math.floor(MEMO_LIMIT / 3); for (const k of memo.keys()) { memo.delete(k); if (--drop <= 0) break; } }
      memo.set(key, result);
    }
    return result;
  }

  function scriptMix(counts) {
    const total = counts[1] + counts[3] + counts[4] + counts[5] + counts[9] + counts[10] + counts[11] || 1;
    const r = (n) => Math.round(n / total * 1000) / 1000;
    return { cyrillic: r(counts[3]), latin: r(counts[1]), code: r(counts[4] + counts[5]), cjk: r(counts[9] + counts[10]), other: r(counts[11]) };
  }

  function breakdown(value) {
    const text = String(value ?? "");
    const counts = features(text);
    const raw = perFamily(counts);
    const families = FAMILIES.map((row, i) => ({ id: row.id, label: row.label, tokens: Math.ceil(raw[i] - 1e-9) }));
    const values = families.map((row) => row.tokens);
    return { tokens: text ? estimate(text) : 0, low: text ? Math.min(...values) : 0, high: text ? Math.max(...values) : 0,
      families, mix: scriptMix(counts), estimated: true };
  }

  /** Оценке не нужна ложная точность: три значащие цифры (84 330 → 84 300). */
  function approx(n) {
    const value = Math.round(Number(n) || 0);
    if (value < 1000) return value;
    const step = 10 ** (Math.floor(Math.log10(value)) - 2);
    return Math.round(value / step) * step;
  }
  /** Коротко: «84 тыс.» / «84k», «1,2 млн» / «1.2M». */
  function short(n, lang) {
    const en = lang === "en", value = Number(n) || 0;
    const fmt = (v) => (+v.toFixed(v < 10 ? 1 : 0)).toLocaleString(en ? "en-US" : "ru-RU");
    if (value >= 1e6) return fmt(value / 1e6) + (en ? "M" : " млн");
    if (value >= 1e3) return fmt(value / 1e3) + (en ? "k" : " тыс.");
    return String(Math.round(value));
  }
  /** «79–126 тыс.» / «79k–126k»: единица один раз, если она у обоих концов одна. */
  function shortRange(low, high, lang) {
    const a = short(low, lang), b = short(high, lang);
    const unit = / (тыс\.|млн)$/.exec(b);
    return unit && a.endsWith(unit[0]) ? `${a.slice(0, -unit[0].length)}–${b}` : `${a}–${b}`;
  }
  const labelOf = (model) => (BY_ID[familyOf(model)] || {}).label || "";

  return { FEATURES, FAMILIES, features, estimate, breakdown, familyOf, labelOf, approx, short, shortRange };
})();
if (typeof window !== "undefined") window.MemuTokens = MemuTokens;
if (typeof module !== "undefined" && module.exports) module.exports = MemuTokens;
