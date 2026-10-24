/* MemuAI Quick Ask · instant answers without a model.
   As the user types, "2^10 + 15%", "√2·3", "15% от 2400", "5 km в милях",
   "72 F to C", "3 GB in MB" get a result in the input row immediately (no
   network, no model, no eval): a tiny recursive-descent parser and a unit
   table. Anything it does not fully understand returns null, so a normal
   question is never mistaken for maths. Exposes window.QuickCalc
   { evaluate(text, lang) → {value, text, kind} | null }; also works in Node
   (module.exports) for the unit tests. */
(function (root) {
  "use strict";

  const FUNCS = {
    sqrt: Math.sqrt, "√": Math.sqrt, abs: Math.abs, round: Math.round, floor: Math.floor, ceil: Math.ceil,
    ln: Math.log, log: Math.log10, lg: Math.log10, log2: Math.log2, exp: Math.exp,
    sin: Math.sin, cos: Math.cos, tan: Math.tan, tg: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan,
  };
  const CONSTS = { pi: Math.PI, "π": Math.PI, e: Math.E, tau: 2 * Math.PI };

  function tokenize(text) {
    const tokens = [];
    let i = 0;
    const s = text;
    while (i < s.length) {
      const ch = s[i];
      if (/\s/.test(ch)) { i += 1; continue; }
      // Round 34: «1 000 000» is ONE number (digit groups), not 1·000·000 = 0
      const grouped = s.slice(i).match(/^\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?(?![\d.,])/);
      if (grouped) {
        tokens.push({ t: "num", v: parseFloat(grouped[0].replace(/[ \u00a0\u202f]/g, "").replace(",", ".")) });
        i += grouped[0].length; continue;
      }
      const num = s.slice(i).match(/^(\d+(?:[.,]\d+)?|[.,]\d+)(?:e[+-]?\d+)?/i);
      if (num && !(ch === "," && !/\d/.test(s[i + 1] || ""))) {
        tokens.push({ t: "num", v: parseFloat(num[0].replace(",", ".")) });
        i += num[0].length; continue;
      }
      const word = s.slice(i).match(/^(sqrt|abs|round|floor|ceil|ln|log2|log|lg|exp|asin|acos|atan|sin|cos|tan|tg|pi|tau|e)(?![a-zа-я])/i);
      if (word) {
        const w = word[0].toLowerCase();
        tokens.push(CONSTS[w] !== undefined && !FUNCS[w] ? { t: "num", v: CONSTS[w], c: true } : { t: "fn", v: w });
        i += word[0].length; continue;
      }
      if (ch === "π") { tokens.push({ t: "num", v: Math.PI, c: true }); i += 1; continue; }
      if (ch === "√") { tokens.push({ t: "fn", v: "√" }); i += 1; continue; }
      if (s.startsWith("**", i)) { tokens.push({ t: "op", v: "^" }); i += 2; continue; }
      if ("+-*/^%()×÷·−°!".includes(ch)) {
        const map = { "×": "*", "·": "*", "÷": "/", "−": "-" };
        tokens.push({ t: "op", v: map[ch] || ch }); i += 1; continue;
      }
      return null;
    }
    return tokens;
  }

  function parse(tokens) {
    let pos = 0;
    let operators = 0;
    const peek = () => tokens[pos];
    const take = (v) => { const tok = tokens[pos]; if (tok && tok.t === "op" && tok.v === v) { pos += 1; return true; } return false; };
    function expr() {
      let left = term();
      for (;;) {
        if (take("+")) {
          operators += 1;
          const right = term();
          left = right.percent ? { v: left.v * (1 + right.v / 100) } : { v: left.v + right.v };
        } else if (take("-")) {
          operators += 1;
          const right = term();
          left = right.percent ? { v: left.v * (1 - right.v / 100) } : { v: left.v - right.v };
        } else return left;
      }
    }
    function term() {
      let left = power();
      for (;;) {
        if (take("*")) { operators += 1; const r = power(); left = { v: left.v * (r.percent ? r.v / 100 : r.v) }; }
        else if (take("/")) { operators += 1; const r = power(); left = { v: left.v / (r.percent ? r.v / 100 : r.v) }; }
        else if (peek() && ((peek().t === "num" && (peek().c || tokens[pos - 1].c)) || peek().t === "fn"
                            || (peek().t === "op" && peek().v === "("))) {
          // implicit: 2π, 3(4+1), 2 sqrt 9 — but never two plain numbers («2 3», «192.168.1.1», «5 % 3»,
          // a phone or a date): that is not maths, the old reading multiplied them
          operators += 1; const r = power(); left = { v: left.v * r.v };
        } else return left;
      }
    }
    function power() {
      const base = unary();
      if (take("^")) { operators += 1; const exp = power(); return { v: Math.pow(base.v, exp.v) }; }
      return base;
    }
    function unary() {
      if (take("-")) { operators += 1; const r = unary(); return { v: -r.v, percent: r.percent }; }
      if (take("+")) return unary();
      return postfix();
    }
    function postfix() {
      let value = primary();
      for (;;) {
        if (take("%")) { operators += 1; value = { v: value.v, percent: true }; }
        else if (take("°")) { value = { v: value.v * Math.PI / 180 }; }
        else if (take("!")) {
          operators += 1;
          if (value.v < 0 || value.v > 170 || !Number.isInteger(value.v)) throw new Error("factorial");
          let f = 1; for (let k = 2; k <= value.v; k += 1) f *= k; value = { v: f };
        } else return value;
      }
    }
    function primary() {
      const tok = peek();
      if (!tok) throw new Error("end");
      if (tok.t === "num") { pos += 1; return { v: tok.v }; }
      if (tok.t === "fn") {
        pos += 1; operators += 1;
        const fn = FUNCS[tok.v];
        if (take("(")) { const inner = expr(); if (!take(")")) throw new Error(")"); return { v: fn(inner.v) }; }
        const arg = power();
        return { v: fn(arg.v) };
      }
      if (take("(")) { const inner = expr(); if (!take(")")) throw new Error(")"); return inner; }
      throw new Error("unexpected");
    }
    const result = expr();
    if (pos !== tokens.length) throw new Error("trailing");
    return { value: result.percent ? result.v / 100 : result.v, operators };
  }

  /* ----------------------------------------------------------- units */
  const U = [];
  const unit = (kind, factor, names, offset = 0) => U.push({ kind, factor, offset, names });
  unit("len", 1, ["m", "м", "meter", "meters", "metre", "метр", "метра", "метров", "метрах", "метры"]);
  unit("len", 1000, ["km", "км", "kilometer", "kilometers", "километр", "километра", "километров", "километрах", "километры"]);
  unit("len", .01, ["cm", "см", "сантиметр", "сантиметров", "сантиметрах", "сантиметры", "сантиметра"]);
  unit("len", .001, ["mm", "мм", "миллиметр", "миллиметров", "миллиметрах", "миллиметры", "миллиметра"]);
  unit("len", 1609.344, ["mi", "mile", "miles", "миля", "мили", "миль", "милях", "милю"]);
  unit("len", .3048, ["ft", "foot", "feet", "фут", "фута", "футов", "футах", "футы"]);
  unit("len", .0254, ["in", "inch", "inches", "\"", "дюйм", "дюйма", "дюймов", "дюймах", "дюймы"]);
  unit("len", .9144, ["yd", "yard", "yards", "ярд", "ярда", "ярдов"]);
  unit("len", 1852, ["nmi", "морская миля", "морских миль"]);
  unit("mass", 1, ["kg", "кг", "kilogram", "kilograms", "килограмм", "килограмма", "килограммов", "килограммах", "килограммы"]);
  unit("mass", .001, ["g", "г", "gram", "grams", "грамм", "грамма", "граммов", "граммах", "граммы"]);
  unit("mass", 1e-6, ["mg", "мг", "миллиграмм", "миллиграммов"]);
  unit("mass", 1000, ["t", "т", "ton", "tons", "tonne", "тонна", "тонны", "тонн", "тоннах"]);
  unit("mass", .45359237, ["lb", "lbs", "pound", "pounds", "фунт", "фунта", "фунтов", "фунтах"]);
  unit("mass", .028349523125, ["oz", "ounce", "ounces", "унция", "унции", "унций", "унциях"]);
  unit("temp", 1, ["c", "°c", "celsius", "цельсий", "цельсия", "цельсию", "градусов цельсия", "℃"]);
  unit("temp", 5 / 9, ["f", "°f", "fahrenheit", "фаренгейт", "фаренгейта", "фаренгейту", "градусов фаренгейта", "℉"], -32);
  unit("temp", 1, ["k", "kelvin", "кельвин", "кельвина", "кельвинов"], -273.15);
  unit("data", 1, ["b", "byte", "bytes", "байт", "байта", "байтов", "байтах", "байты"]);
  unit("data", 1024, ["kb", "кб", "kilobyte", "kilobytes", "килобайт", "килобайта", "килобайтах", "килобайты"]);
  unit("data", 1024 ** 2, ["mb", "мб", "megabyte", "megabytes", "мегабайт", "мегабайта", "мегабайтах", "мегабайты"]);
  unit("data", 1024 ** 3, ["gb", "гб", "gigabyte", "gigabytes", "гигабайт", "гигабайта", "гигабайтах", "гигабайты"]);
  unit("data", 1024 ** 4, ["tb", "тб", "terabyte", "terabytes", "терабайт", "терабайта", "терабайтах", "терабайты"]);
  unit("time", 1, ["s", "sec", "secs", "second", "seconds", "с", "сек", "секунд", "секунды", "секунду", "секундах"]);
  unit("time", 60, ["min", "mins", "minute", "minutes", "мин", "минут", "минуты", "минуту", "минутах"]);
  unit("time", 3600, ["h", "hr", "hrs", "hour", "hours", "ч", "час", "часа", "часов", "часах", "часы"]);
  unit("time", 86400, ["d", "day", "days", "дн", "день", "дня", "дней", "днях", "сутки", "суток", "дни"]);
  unit("time", 604800, ["wk", "week", "weeks", "неделя", "недели", "недель", "неделях", "неделю"]);
  unit("speed", 1 / 3.6, ["km/h", "kmh", "kph", "км/ч"]);
  unit("speed", 1, ["m/s", "м/с"]);
  unit("speed", .44704, ["mph", "миль/ч"]);
  unit("speed", .514444, ["kn", "knot", "knots", "узел", "узла", "узлов"]);
  unit("vol", 1, ["l", "л", "liter", "liters", "litre", "литр", "литра", "литров", "литрах", "литры"]);
  unit("vol", .001, ["ml", "мл", "миллилитр", "миллилитров"]);
  unit("vol", 3.785411784, ["gal", "gallon", "gallons", "галлон", "галлона", "галлонов", "галлонах"]);
  unit("area", 1, ["m2", "м2", "m²", "м²", "кв. м", "кв м"]);
  unit("area", 1e4, ["ha", "га", "гектар", "гектара", "гектаров"]);
  unit("area", 4046.8564224, ["acre", "acres", "акр", "акра", "акров"]);
  const LABELS = { c: "°C", f: "°F", k: "K", b: "B", kb: "KB", mb: "MB", gb: "GB", tb: "TB", l: "L", m2: "m²",
    "km/h": "km/h", "m/s": "m/s" };
  const BY_NAME = new Map();
  for (const row of U) for (const name of row.names) BY_NAME.set(name.toLowerCase(), row);

  const fmt = (value, lang) => {
    if (!Number.isFinite(value)) return null;
    const abs = Math.abs(value);
    if (abs !== 0 && (abs >= 1e15 || abs < 1e-6)) return value.toExponential(6).replace(/\.?0+e/, "e");
    const rounded = Math.round(value * 1e10) / 1e10;
    const digits = abs >= 1 ? 6 : 10;
    return rounded.toLocaleString(lang === "en" ? "en-US" : "ru-RU", { maximumFractionDigits: digits, useGrouping: true });
  };

  function convert(text, lang) {
    const m = String(text).trim().toLowerCase()
      .match(/^(-?[\d\s]*[\d][.,]?\d*)\s*([^\d\s][^\s]*(?:\s(?:цельсия|фаренгейта|миля|миль|м|с))?)\s+(?:to|in|into|в|во|->|→|=|на)\s+(.+?)\s*\??$/);
    if (!m) return null;
    const amount = parseFloat(m[1].replace(/\s/g, "").replace(",", "."));
    const from = BY_NAME.get(m[2].replace(/^градус(?:ов|а)?\s*/, ""));
    const to = BY_NAME.get(m[3].replace(/^градус(?:ов|а|ы)?\s*/, ""));
    if (!Number.isFinite(amount) || !from || !to || from.kind !== to.kind || from === to) return null;
    const base = (amount + from.offset) * from.factor;
    const value = base / to.factor - to.offset;
    const shown = fmt(value, lang);
    if (shown === null) return null;
    return { value, text: `${shown} ${LABELS[to.names[0]] || to.names[0]}`, kind: "unit" };
  }

  function evaluate(text, lang = "ru") {
    const source = String(text || "").trim();
    if (!source || source.length > 160 || /\n/.test(source)) return null;
    const converted = convert(source, lang);
    if (converted) return converted;
    let expr = source.replace(/[=?]\s*$/, "").trim()
      .replace(/^(?:сколько будет|сколько|посчитай|вычисли|calculate|calc|what(?:'s| is))\s+/i, "");
    // "15% от 2400" / "15% of 2400" → 2400 * 15%
    const pct = expr.match(/^(-?\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(-?\d+(?:[.,]\d+)?)$/i);
    if (pct) expr = `${pct[2]}*${pct[1]}%`;
    if (!/\d/.test(expr) && !/(pi|π|tau)/i.test(expr)) return null;
    // Round 34: a date («2024-10-11», «11.10.2026») or a phone («+7 999 123-45-67», «123-45-67») is not a sum
    if (/^\d{1,4}([-./])\d{1,2}\1\d{1,4}$/.test(expr) || /^\+\d[\d\s()-]{6,}$/.test(expr)
        || /^\(?\d[\d\s()]*-\d{2}-\d{2}$/.test(expr)) return null;
    if (/[a-zа-яё]{3,}/i.test(expr.replace(/\b(sqrt|abs|round|floor|ceil|log2|log|exp|asin|acos|atan|sin|cos|tan|pi|tau)\b/gi, ""))) return null;
    const tokens = tokenize(expr);
    if (!tokens || !tokens.length) return null;
    try {
      const { value, operators } = parse(tokens);
      if (!operators || !Number.isFinite(value)) return null;
      const shown = fmt(value, lang);
      return shown === null ? null : { value, text: shown, kind: "math" };
    } catch (e) {
      return null;
    }
  }

  const api = { evaluate };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.QuickCalc = api;
})(typeof window !== "undefined" ? window : null);
