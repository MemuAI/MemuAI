

(function (root) {
  "use strict";

  const CUR = [
    ["USD", /^(?:usd|\$|us\$|долл?ар[а-яёa-z]*|бакс[а-яёa-z]*|dollars?|bucks?)$/i],
    ["EUR", /^(?:eur|€|евро|euros?)$/i],
    ["RUB", /^(?:rub|rur|₽|р\.?|руб\.?|рубл[а-яёa-z]*|roubles?|rubles?)$/i],
    ["GBP", /^(?:gbp|£|фунт[а-яёa-z]*|pounds?)$/i],
    ["CNY", /^(?:cny|rmb|¥|юан[а-яёa-z]*|yuan|renminbi)$/i],
    ["JPY", /^(?:jpy|иен[а-яёa-z]*|йен[а-яёa-z]*|yen)$/i],
    ["KZT", /^(?:kzt|₸|тенге|тг|tenge)$/i],
    ["UAH", /^(?:uah|₴|гривн[а-яёa-z]*|hryvnias?)$/i],
    ["BYN", /^(?:byn|бел\.?\s*руб[а-яёa-z]*|белорусск[а-яёa-z]*)$/i],
    ["TRY", /^(?:try|₺|лир(?:а|ы|ов)?|liras?)$/i],
    ["CHF", /^(?:chf|франк[а-яёa-z]*|francs?)$/i],
    ["INR", /^(?:inr|₹|рупи[а-яёa-z]*|rupees?)$/i],
    ["AED", /^(?:aed|дирхам[а-яёa-z]*|dirhams?)$/i],
    ["AMD", /^(?:amd|драм(?:ов|а|ы)?|drams?)$/i],
    ["GEL", /^(?:gel|₾|лари|lari)$/i],
    ["KGS", /^(?:kgs|сом(?:ов|а|ы)?|soms?)$/i],
    ["UZS", /^(?:uzs|сум(?:ов|а|ы)?|sums?)$/i],
    ["PLN", /^(?:pln|zł|злот[а-яёa-z]*|zloty)$/i],
    ["CZK", /^(?:czk|kč|чешск[а-яёa-z]*\s*крон[а-яёa-z]*|korun[а-яёa-z]*)$/i],
    ["KRW", /^(?:krw|₩|вон(?:ов|а|ы)?|won)$/i],
    ["THB", /^(?:thb|฿|бат(?:ов|а|ы)?|baht)$/i],
    ["VND", /^(?:vnd|₫|донг(?:ов|а|и)?|dong)$/i],
    ["ILS", /^(?:ils|₪|шекел[а-яёa-z]*|shekels?)$/i],
    ["CAD", /^(?:cad|c\$|канадск[а-яёa-z]*\s*долл[а-яёa-z]*)$/i],
    ["AUD", /^(?:aud|a\$|австралийск[а-яёa-z]*\s*долл[а-яёa-z]*)$/i],
  ];
  const ISO = /^[A-Za-z]{3}$/;
  const KNOWN_ISO = new Set(CUR.map((r) => r[0]).concat(["SEK", "NOK", "DKK", "HUF", "RON", "BGN", "SGD", "HKD", "NZD", "MXN", "BRL", "ZAR", "IDR", "MYR", "PHP", "SAR", "QAR", "EGP", "AZN", "MDL", "TJS", "TMT", "MNT", "ISK", "RSD"]));
  function currencyCode(token) {
    const s = String(token || "").trim().replace(/[.,;:!?]+$/, "");
    if (!s) return "";
    for (const [code, re] of CUR) if (re.test(s)) return code;
    if (ISO.test(s) && KNOWN_ISO.has(s.toUpperCase())) return s.toUpperCase();
    return "";
  }
  function parseAmount(text) {
    let s = String(text || "").replace(/\s+/g, "").replace(/[’']/g, "");
    let mult = 1;
    const suffix = s.match(/(k|к|тыс\.?|thousand|m|млн\.?|mln|million|bn|млрд\.?|billion)$/i);
    if (suffix) {
      const x = suffix[1].toLowerCase();
      mult = /^(k|к|тыс|thousand)/.test(x) ? 1e3 : (/^(m|млн|mln|million)/.test(x) ? 1e6 : 1e9);
      s = s.slice(0, -suffix[1].length);
    }
    if (!/^\d[\d.,]*$/.test(s)) return NaN;
    if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, "");
    else if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
    else s = s.replace(",", ".");
    const v = parseFloat(s);
    return isFinite(v) ? v * mult : NaN;
  }

  function parseCurrency(text) {
    const s = String(text || "").trim().toLowerCase().replace(/\s+/g, " ")
      .replace(/^(?:сколько(?: будет| стоит| это)?|how much is|how much are|what is|what's)\s+/, "");
    if (!s || s.length > 80) return null;
    let m = s.match(/^(?:курс|exchange rate(?: of)?|rate(?: of)?)\s+(.+?)(?:\s+(?:в|к|to|in)\s+(.+))?\??$/);
    if (m) {
      const from = currencyCode(m[1]);
      const to = m[2] ? currencyCode(m[2]) : null;
      if (from && (to || !m[2])) return { amount: 1, from, to };
      return null;
    }
    const sym = "(\\$|€|£|¥|₽|₸|₴|₺|₹|₾|₪|₩|฿|₫)";

    const num = "(\\d[\\d\\s.,'’]*(?:\\s?(?:k|к|тыс\\.?|thousand|m|млн\\.?|mln|million|bn|млрд\\.?|billion)(?![a-zа-яё]))?)";
    const tail = "(?:\\s*(?:в|во|к|на|to|in|into|->|→|=|>)\\s*(.+?))?\\??$";
    m = s.match(new RegExp("^" + sym + "\\s?" + num + tail));
    let amountText, fromText, toText;
    if (m) { fromText = m[1]; amountText = m[2]; toText = m[3]; }
    else {
      m = s.match(new RegExp("^" + num + "\\s?([^\\d\\s][^\\s]*(?:\\s(?:руб[а-яё]*|долл[а-яё]*|крон[а-яё]*))?)" + tail));
      if (!m) return null;
      amountText = m[1]; fromText = m[2]; toText = m[3];
    }
    const amount = parseAmount(amountText);
    const from = currencyCode(fromText);

    if (!toText && /^(?:try|sums?|soms?|won|dong|drams?|бат|вон|сом|сум|донг)$/i.test(String(fromText || "").trim())) return null;
    if (!from || !isFinite(amount) || amount <= 0) return null;
    let to = null;
    if (toText) { to = currencyCode(toText); if (!to) return null; }
    if (to === from) return null;
    return { amount, from, to };
  }
  const api0 = { parseCurrency, currencyCode, parseAmount };
  if (typeof module !== "undefined" && module.exports) { module.exports = api0; return; }

  const Q = root.MemuQuick;
  if (!Q || !Q.registerPanel) return;
  const html = document.documentElement;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const IC = {
    money: svg('<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9v.01M18 15v.01"/>'),
    drop: svg('<path d="m14 4 6 6M17 7 8 16l-4 1 1-4 9-9"/><path d="M12 6l6 6"/>'),
    ocr: svg('<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M8 10h8M8 14h5"/>'),
    bug: svg('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'),
    note: svg('<path d="M5 4h14a1 1 0 0 1 1 1v10l-5 5H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M15 20v-4a1 1 0 0 1 1-1h4M8 9h8M8 13h5"/>'),
    dl: svg('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
    copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>'),
    back: svg('<path d="M15 6l-6 6 6 6"/>'),
    spark: svg('<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/>'),
    folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    open: svg('<path d="M14 4h6v6M20 4l-9 9M10 6H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>'),
  };
  const S = { settings: {}, native: !!Q.native, view: "", rates: {}, conv: null, color: null, live: false, ocr: null, downloads: null, index: 0, noteText: "", busy: false, startColor: null };
  let panel = null, liveTimer = 0;
  async function api(path, body) { return Q.api(path, body === undefined ? {} : { body: { lang: Q.lang(), ...body } }); }
  const on = (key) => S.settings[key] !== false;

  async function loadSettings() {
    try { const d = await api("/api/quick/apps/settings?lang=" + Q.lang()); S.settings = d.settings || {}; S.native = !!d.native; } catch (e) {  }
  }

  const defaultTarget = () => S.settings.currency_to || (Q.lang() === "en" ? "USD" : "RUB");
  const fmt = (v, code) => {
    const digits = v >= 100 ? 2 : (v >= 1 ? 3 : 5);
    try { return new Intl.NumberFormat(Q.lang() === "en" ? "en-US" : "ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: v >= 100 ? 2 : 0 }).format(v) + " " + code; }
    catch (e) { return v.toFixed(2) + " " + code; }
  };
  async function rates(base) {
    const row = S.rates[base];
    if (row && Date.now() - row.at < 30 * 60 * 1000) return row.data;
    const data = await api("/api/quick/extras/rates?base=" + base + "&lang=" + Q.lang());
    S.rates[base] = { at: Date.now(), data };
    return data;
  }
  let convSeq = 0;
  async function updateCurrency(text) {
    const parsed = on("currency") ? parseCurrency(text) : null;
    const seq = ++convSeq;
    if (!parsed) { if (S.conv) { S.conv = null; render(); } return; }
    let to = parsed.to || defaultTarget();
    if (to === parsed.from) to = parsed.from === "USD" ? (Q.lang() === "en" ? "EUR" : "RUB") : "USD";
    try {
      const data = await rates(parsed.from);
      if (seq !== convSeq) return;
      const r = data.rates || {};
      if (!r[to]) { S.conv = { error: t(`Нет курса ${to}`, `No ${to} rate`) }; render(); return; }
      const value = parsed.amount * r[to];
      S.conv = { text: `${fmt(parsed.amount, parsed.from)} = ${fmt(value, to)}`, value: fmt(value, to), rate: `1 ${parsed.from} = ${fmt(r[to], to)}`,
        date: data.t ? new Date(data.t * 1000).toLocaleDateString(Q.lang() === "en" ? "en-US" : "ru-RU") : "", source: data.source || "", stale: !!data.stale };
    } catch (e) {
      if (seq !== convSeq) return;
      S.conv = { error: e.message };
    }
    render();
  }

  function hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
    let h = 0, s = 0;
    if (max !== min) {
      const d = max - min;
      s = l > .5 ? d / (2 - max - min) : d / (max + min);
      h = max === r ? (g - b) / d + (g < b ? 6 : 0) : (max === g ? (b - r) / d + 2 : (r - g) / d + 4);
      h *= 60;
    }
    return [Math.round(h), Math.round(s * 100), Math.round(l * 100)];
  }
  function cmyk(r, g, b) {
    const k = 1 - Math.max(r, g, b) / 255;
    if (k >= 1) return [0, 0, 0, 100];
    return [r, g, b].map((v) => Math.round((1 - v / 255 - k) / (1 - k) * 100)).concat(Math.round(k * 100));
  }
  function colorRows(c) {
    const [h, s, l] = hsl(c.r, c.g, c.b);
    const [cc, m, y, k] = cmyk(c.r, c.g, c.b);
    return [["HEX", c.hex], ["RGB", `rgb(${c.r}, ${c.g}, ${c.b})`], ["HSL", `hsl(${h}, ${s}%, ${l}%)`], ["CMYK", `${cc}% ${m}% ${y}% ${k}%`], ["CSS", `--color: ${c.hex.toLowerCase()};`]];
  }
  function startLive() {
    stopLive();
    if (!S.native) { S.colorError = t("Пипетка работает в приложении MemuAI для Windows", "The eyedropper works in the MemuAI Windows app"); render(); return; }
    S.live = true; render();
    const tick = async () => {
      if (!S.live) return;
      try { const d = await api("/api/quick/extras/color/now"); if (d.color && S.live) { S.color = d.color; renderView(); } } catch (e) {  }
      if (S.live) liveTimer = setTimeout(tick, 120);
    };
    tick();
  }
  function stopLive() { S.live = false; clearTimeout(liveTimer); }
  async function copy(text, label) {
    try { await navigator.clipboard.writeText(text); Q.toast(t(`Скопировано: ${label || text}`, `Copied: ${label || text}`), "ok", 1600); }
    catch (e) { Q.toast(t("Не удалось скопировать", "Could not copy"), "err"); }
  }

  async function ocr() {
    if (!on("ocr")) return;
    openView("ocr");
    S.ocr = { busy: true }; renderView();
    try {
      const shot = await api("/api/quick/features/region", {});
      if (shot.cancelled || !shot.data) { closeView(); return; }
      S.ocr = { busy: true, image: shot.data }; renderView();
      const r = await api("/api/quick/extras/ocr", { data: shot.data });
      S.ocr = { text: r.text || "", lang: r.lang, image: shot.data };
    } catch (e) { S.ocr = { error: e.message }; }
    renderView();
  }
  async function explainError(useSelection) {
    const sel = Q.selection ? Q.selection() : { text: "" };
    const prompt = t("Найди сообщение об ошибке (на снимке экрана или в тексте) и объясни: 1) что означает ошибка простыми словами, 2) самая вероятная причина, 3) как исправить — конкретные шаги или команда. Если ошибок несколько — начни с первой. Если ошибки не видно — так и скажи.",
      "Find the error message (on the screenshot or in the text) and explain: 1) what it means in plain words, 2) the most likely cause, 3) how to fix it — concrete steps or a command. Several errors — start with the first. No error visible — say so.");
    if (!(useSelection && sel.text)) {
      try {
        const shot = await api("/api/quick/screenshot", { monitor: "cursor" });
        Q.addImage({ data: shot.data, width: shot.width, height: shot.height, label: t("экран", "screen"), name: "screen.jpg" });
      } catch (e) { Q.toast(S.native ? e.message : t("Снимок экрана работает в приложении MemuAI для Windows", "Screenshots work in the MemuAI Windows app"), "err", 3200); return; }
    }
    Q.send(t("Объясни ошибку", "Explain the error"), { prompt, direct: true });
  }
  async function saveNote(text, open) {
    const body = String(text || "").trim();
    if (!body) { openView("note"); return false; }
    try {
      const r = await api("/api/quick/extras/note", { text: body, open: !!open });
      Q.toast(t(`Сохранено в «${r.name}»`, `Saved to “${r.name}”`), "ok", 2200);
      return true;
    } catch (e) { Q.toast(e.message, "err"); return false; }
  }
  async function loadDownloads() {
    S.downloads = null; renderView();
    try { const d = await api("/api/quick/extras/downloads"); S.downloads = d.items || []; S.dlFolder = d.folder || ""; S.dlFound = d.found !== false; }
    catch (e) { S.downloads = { error: e.message }; }
    renderView();
  }
  async function useDownload(name) {
    try {
      const r = await api("/api/quick/extras/downloads/use", { name });
      closeView(false);

      if (r.image && Q.addImage && Q.addImage({ data: r.image, name, label: name })) {
        Q.setInput("", { focus: true });
        Q.toast(t(`«${name}» прикреплена — спросите о ней`, `“${name}” attached — ask about it`), "", 2600);
        return;
      }
      if (r.image_error) Q.toast(r.image_error, "warn", 3200);
      if (window.QuickExplorer && r.context) window.QuickExplorer.setContext(r.context);
      Q.setInput("", { focus: true });
      if (!r.image_error) Q.toast(t(`«${name}» — спросите о нём или выберите действие`, `“${name}” — ask about it or pick an action`), "", 2600);
    } catch (e) { Q.toast(e.message, "err"); }
  }

  function openView(view) {
    S.view = view; S.index = 0;
    html.dataset.qxxView = view;
    if (view === "color") { S.color = S.color || S.startColor; S.colorError = ""; }
    if (view === "downloads") loadDownloads();
    render();
    setTimeout(() => { const el = panel && panel.querySelector("[data-autofocus]"); if (el) el.focus(); }, 30);
  }
  function closeView(focus = true) {
    stopLive();
    S.view = ""; S.ocr = null;
    delete html.dataset.qxxView;
    render();
    if (focus) Q.setInput(Q.getInput(), { focus: true });
  }
  function head(title) {
    return `<div class="qa-view-head qxx-head"><button type="button" class="qa-icon qa-back" data-x="back" title="${esc(t("Назад (Esc)", "Back (Esc)"))}" aria-label="${esc(t("Назад", "Back"))}">${IC.back}</button><span class="qxx-title">${esc(title)}</span></div>`;
  }
  function render() {
    if (!panel) return;
    if (S.view) { panel.hidden = false; panel.dataset.mode = "view"; renderView(); return; }
    panel.dataset.mode = "inline";
    const parts = [];
    if (S.conv) {
      parts.push(S.conv.error ? `<div class="qxx-conv err">${IC.money}<span>${esc(S.conv.error)}</span></div>`
        : `<button type="button" class="qxx-conv" data-x="copy-conv" title="${esc(t("Скопировать результат", "Copy the result"))}">${IC.money}<b>${esc(S.conv.text)}</b>
          <small>${esc(S.conv.rate + (S.conv.date ? " · " + S.conv.date : "") + (S.conv.stale ? t(" · старый курс", " · old rate") : ""))}</small><span class="cp">${IC.copy}</span></button>`);
    }
    const sel = Q.selection ? Q.selection() : { text: "" };
    const st = Q.state();
    if (on("error") && sel.text && !st.turns.length && looksLikeError(sel.text)) {
      parts.push(`<button type="button" class="qxx-pill" data-x="error-sel">${IC.bug}<span>${esc(t("Объяснить ошибку", "Explain the error"))}</span></button>`);
    }
    panel.hidden = !parts.length;
    panel.innerHTML = parts.join("");
    const c = panel.querySelector('[data-x="copy-conv"]');
    if (c) c.onclick = () => copy(S.conv.value);
    const e = panel.querySelector('[data-x="error-sel"]');
    if (e) e.onclick = () => explainError(true);
    Q.resize();
  }
  function looksLikeError(text) {
    return /(Traceback \(most recent call last\)|\b\w+(Error|Exception)\b[:(]|\bat [\w.$<>]+\(.*:\d+\)|\berror\s*(TS|CS|C|E)?\d{2,5}\b|npm ERR!|FATAL|Segmentation fault|panicked at|ошибка[:\s]|не удалось|failed with exit code|0x[0-9a-f]{8})/i.test(String(text).slice(0, 4000));
  }
  function renderView() {
    if (!panel || !S.view) return;
    let body = "";
    if (S.view === "color") {
      const c = S.color;
      body = head(t("Пипетка", "Eyedropper")) + `<div class="qxx-color">
        <div class="qxx-swatch" style="background:${c ? esc(c.hex) : "transparent"}">${c ? "" : esc(t("нет цвета", "no colour"))}</div>
        <div class="qxx-crows">${c ? colorRows(c).map(([k, v]) => `<button type="button" class="qxx-crow" data-copy="${esc(v)}"><span>${k}</span><b>${esc(v)}</b>${IC.copy}</button>`).join("") : ""}</div></div>
        <div class="qxx-foot">${S.colorError ? `<span class="err">${esc(S.colorError)}</span>` : `<span>${esc(S.live ? t("Наведите указатель на любой пиксель экрана и нажмите Пробел.", "Point at any pixel on the screen and press Space.") : (c && c.x !== undefined ? t(`Цвет под указателем (${c.x}, ${c.y}).`, `The colour under the pointer (${c.x}, ${c.y}).`) : t("Цвет под указателем в момент Alt+Space.", "The colour under the pointer at Alt+Space.")))}</span>`}
        <button type="button" class="qa-btn ${S.live ? "" : "accent"}" data-x="live" data-autofocus>${esc(S.live ? t("Взять цвет (Пробел)", "Take it (Space)") : t("Выбрать на экране", "Pick on screen"))}</button></div>`;
    } else if (S.view === "ocr") {
      const o = S.ocr || {};
      body = head(t("Текст с экрана (без ИИ)", "Text from the screen (no AI)")) + (o.busy ? `<div class="qxx-empty"><span class="qxx-spin"></span><span>${esc(o.image ? t("Распознаю текст средствами Windows…", "Recognising the text with Windows…") : t("Выделите область экрана…", "Select a screen region…"))}</span></div>`
        : (o.error ? `<div class="qxx-empty err">${esc(o.error)}</div>`

          : !o.text ? `<div class="qxx-empty">${esc(t("Текст не найден — выделите область крупнее или распознайте моделью (если она понимает картинки).", "No text found — select a larger area or let a model read it (if it understands images)."))}</div>
            <div class="qxx-foot"><span></span><span class="qxx-btns"><button type="button" class="qa-btn" data-x="ocr-retry">${esc(t("Выделить ещё раз", "Select again"))}</button>
            ${o.image ? `<button type="button" class="qa-btn accent" data-x="ocr-model" data-autofocus>${IC.spark}${esc(t("Распознать моделью", "Read with a model"))}</button>` : ""}</span></div>`
          : `<textarea class="qxx-text" id="qxx-ocr" spellcheck="false" data-autofocus>${esc(o.text)}</textarea>
            <div class="qxx-foot"><span>${esc(t(`Строк: ${(o.text.match(/\n/g) || []).length + 1}${o.lang ? " · " + o.lang : ""}`, `Lines: ${(o.text.match(/\n/g) || []).length + 1}${o.lang ? " · " + o.lang : ""}`))}</span>
            <span class="qxx-btns"><button type="button" class="qa-btn" data-x="ocr-ask">${IC.spark}${esc(t("Спросить ИИ", "Ask AI"))}</button>
            ${on("notes") ? `<button type="button" class="qa-btn" data-x="ocr-note">${IC.note}${esc(t("В заметку", "To note"))}</button>` : ""}
            <button type="button" class="qa-btn accent" data-x="ocr-copy">${IC.copy}${esc(t("Копировать", "Copy"))}</button></span></div>`));
    } else if (S.view === "note") {
      body = head(t("Заметка в MemuNote", "Note to MemuNote")) + `<textarea class="qxx-text" id="qxx-note" placeholder="${esc(t("Текст заметки… (Ctrl+Enter — сохранить)", "Note text… (Ctrl+Enter — save)"))}" data-autofocus>${esc(S.noteText)}</textarea>
        <div class="qxx-foot"><span>${esc(t("Добавится в «Быстрые заметки.md» с датой и временем.", "Appended to “Quick notes.md” with the date and time."))}</span>
        <button type="button" class="qa-btn" data-x="note-open">${IC.open}${esc(t("Открыть заметки", "Open notes"))}</button>
        <button type="button" class="qa-btn accent" data-x="note-save">${esc(t("Сохранить", "Save"))} <kbd>Ctrl ↵</kbd></button></div>`;
    } else if (S.view === "downloads") {
      const d = S.downloads;
      body = head(t("Последние загрузки", "Latest downloads")) + (!d ? `<div class="qxx-empty"><span class="qxx-spin"></span></div>`
        : (d.error ? `<div class="qxx-empty err">${esc(d.error)}</div>`
          : (!d.length ? `<div class="qxx-empty">${esc(S.dlFound === false ? t("Папка «Загрузки» не найдена", "The Downloads folder was not found") : t("Папка «Загрузки» пуста", "The Downloads folder is empty"))}</div>`
            : `<div class="qxx-list" role="listbox">${d.map((f, i) => `<div class="qa-row qxx-row ${i === S.index ? "active" : ""}" role="option" data-i="${i}" aria-selected="${i === S.index}">
              <span class="ic">${IC.dl}</span><span class="qxx-main"><span class="tx">${esc(f.name)}</span><small>${esc(ago(f.mtime) + " · " + human(f.size))}</small></span></div>`).join("")}</div>
              <div class="qxx-foot"><span>${esc(t("Enter — спросить о файле (ИИ прочитает, переименует, перенесёт), Shift+Enter — показать в Проводнике.", "Enter — ask about the file (AI reads, renames, moves it), Shift+Enter — show in Explorer."))}</span></div>`)));
    }
    panel.innerHTML = body;
    bindView();
    Q.resize();
  }
  function human(size) {
    const u = Q.lang() === "en" ? ["B", "KB", "MB", "GB"] : ["Б", "КБ", "МБ", "ГБ"];
    let v = +size || 0, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    return (i ? v.toFixed(1) : String(Math.round(v))).replace(".", Q.lang() === "en" ? "." : ",") + " " + u[i];
  }
  function ago(sec) {
    const d = Math.max(0, Date.now() / 1000 - sec);
    if (d < 60) return t("только что", "just now");
    if (d < 3600) return t(`${Math.round(d / 60)} мин назад`, `${Math.round(d / 60)} min ago`);
    if (d < 86400) return t(`${Math.round(d / 3600)} ч назад`, `${Math.round(d / 3600)} h ago`);
    return new Date(sec * 1000).toLocaleDateString(Q.lang() === "en" ? "en-US" : "ru-RU");
  }
  function bindView() {
    const $ = (sel) => panel.querySelector(sel);
    const back = $('[data-x="back"]');
    if (back) back.onclick = () => closeView();
    panel.querySelectorAll("[data-copy]").forEach((b) => { b.onclick = () => copy(b.dataset.copy); });
    const live = $('[data-x="live"]');
    if (live) live.onclick = () => (S.live ? (stopLive(), renderView()) : startLive());
    const ocrText = () => ($("#qxx-ocr") ? $("#qxx-ocr").value : "");
    const oc = $('[data-x="ocr-copy"]'); if (oc) oc.onclick = () => copy(ocrText(), t("текст", "text"));
    const oa = $('[data-x="ocr-ask"]'); if (oa) oa.onclick = () => { const text = ocrText(); closeView(false); Q.addContext(text, { name: t("текст с экрана", "screen text") }); };
    const on2 = $('[data-x="ocr-note"]'); if (on2) on2.onclick = () => saveNote(ocrText());
    const orr = $('[data-x="ocr-retry"]'); if (orr) orr.onclick = () => ocr();
    const om = $('[data-x="ocr-model"]');
    if (om) om.onclick = () => {
      const image = S.ocr && S.ocr.image;
      closeView(false);
      Q.addImage({ data: image, label: t("область экрана", "screen region"), name: "region.png" });
      Q.send(t("Распознай текст", "Extract the text"), { prompt: t("Перепиши весь текст с картинки как есть, сохраняя строки. Без пояснений.", "Transcribe all the text in the image exactly as it is, keeping the lines. No comments."), direct: true });
    };
    const note = $("#qxx-note");
    if (note) {
      note.oninput = () => { S.noteText = note.value; };
      note.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveNote(note.value).then((ok) => { if (ok) { S.noteText = ""; closeView(); } }); } };
    }
    const ns = $('[data-x="note-save"]'); if (ns) ns.onclick = () => saveNote(note.value).then((ok) => { if (ok) { S.noteText = ""; closeView(); } });
    const no = $('[data-x="note-open"]'); if (no) no.onclick = async () => { try { const r = await api("/api/quick/extras/note/open", {}); if (!r.ok) Q.toast(t("Не удалось открыть", "Could not open"), "err"); } catch (e) { Q.toast(e.message, "err"); } };
    panel.querySelectorAll(".qxx-row").forEach((r) => { r.onclick = () => useDownload(S.downloads[+r.dataset.i].name); });
  }

  Q.registerPanel("extras", (el) => { panel = el; el.classList.add("qxx"); el.hidden = true; return {}; }, { slot: "top", order: -6 });
  const input = document.getElementById("qa-input");
  if (input) input.addEventListener("input", () => updateCurrency(input.value));
  Q.on("open", () => { loadSettings().then(() => { if (window.QuickExplorer) { const c = window.QuickExplorer.context(); if (c && c.color) S.startColor = c.color; } }); updateCurrency(Q.getInput()); });
  Q.on("app-context", (e) => { if (e && e.context && e.context.color) S.startColor = e.context.color; });
  Q.on("selection", () => { if (!S.view) render(); });
  Q.on("answer-start", () => { S.conv = null; if (S.view) closeView(false); else render(); });
  Q.on("chat-new", () => render());
  Q.on("escape", (e) => { if (S.view) { e.preventDefault(); closeView(); } else if (S.conv) {  } });
  document.addEventListener("keydown", (e) => {
    if (S.view === "color" && S.live && (e.key === " " || e.key === "Enter")) { e.preventDefault(); e.stopPropagation(); stopLive(); renderView(); if (S.color) copy(S.color.hex); }
    else if (S.view === "downloads" && Array.isArray(S.downloads) && S.downloads.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); S.index = (S.index + (e.key === "ArrowDown" ? 1 : -1) + S.downloads.length) % S.downloads.length; renderView(); }
      else if (e.key === "Enter") {
        e.preventDefault(); e.stopPropagation();
        const f = S.downloads[S.index];
        if (e.shiftKey) {
          api("/api/quick/extras/downloads/use", { name: f.name }).then((r) => api("/api/quick/apps/reveal", { id: r.context.id, path: f.name })).catch((err) => Q.toast(err.message, "err"));
        } else useDownload(f.name);
      }
    }
  }, true);

  const SEC = () => t("Инструменты", "Tools");
  if (Q.addAction) {
    Q.addAction({ id: "extras-color", section: SEC(), icon: IC.drop, label: t("Пипетка: цвет с экрана", "Eyedropper: colour from the screen"), hidden: () => !on("color"), run: () => openView("color") });
    Q.addAction({ id: "extras-ocr", section: SEC(), icon: IC.ocr, label: t("Текст с экрана без ИИ (Windows OCR)", "Text from the screen without AI (Windows OCR)"), hidden: () => !on("ocr"), run: () => ocr() });
    Q.addAction({ id: "extras-error", section: SEC(), icon: IC.bug, label: t("Объяснить ошибку на экране", "Explain the error on screen"), hidden: () => !on("error"), run: () => explainError(false) });
    Q.addAction({ id: "extras-note", section: SEC(), icon: IC.note, label: t("Заметка в MemuNote", "Note to MemuNote"), sub: t("текст из поля или новая заметка", "the input text or a new note"), hidden: () => !on("notes"),
      run: () => { const text = Q.getInput().trim(); if (text) { saveNote(text).then((ok) => { if (ok) Q.setInput(""); }); } else openView("note"); } });
    Q.addAction({ id: "extras-downloads", section: SEC(), icon: IC.dl, label: t("Последние загрузки", "Latest downloads"), hidden: () => !on("downloads"), run: () => openView("downloads") });
  }
  if (Q.addAnswerAction) {
    Q.addAnswerAction({ id: "extras-to-note", icon: IC.note, label: t("В заметку", "To note"), title: t("Сохранить ответ в быстрые заметки", "Save the answer to quick notes"),
      hidden: () => !on("notes"), run: (turn) => saveNote(((turn && turn.q) ? `**${turn.q}**\n` : "") + String((turn && turn.a) || "")) });
  }
  root.QuickExtras = { ...api0, openView, explainError, ocr };
})(typeof window !== "undefined" ? window : globalThis);
