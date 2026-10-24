/* MemuAI product cards (Round 21, inspired by ChatGPT Shopping).
 *
 * The model writes one hidden ```memuai-products JSON block in a shopping
 * answer. md() turns the block into placeholder() markup; the block is sent
 * to POST /api/pg/products/normalize (app/product_cards.py), which removes
 * unknown stores, non-http links and unsourced reviews and replaces prices
 * with the ones really seen on store pages. The verified result is cached by
 * the block text, so every later re-render (streaming, chat switch, reload)
 * is synchronous and never flickers.
 *
 * Everything else lives here as well: the row of cards under the answer, the
 * right side sheet (gallery, offers, specs, summary, pros/cons, reviews), the
 * text fallback for copy/export/speech, and the system prompt glue
 * (systemBlock). The prompt text itself is the internal prompt
 * "product-cards" (app/pgdata.py → PRODUCT_CARDS_PROMPT).
 *
 * All values are escaped; links are http(s) only and open in the system
 * browser through target="_blank" (pywebview hands those to the OS).
 */
"use strict";
const MemuProducts = (() => {
  const cache = new Map();      // key → {ok, data} | {ok:false}
  const rawByKey = new Map();   // key → block text
  const inflight = new Map();   // key → Promise
  const shown = new Set();      // keys whose entrance already played
  const MAX_RAW = 200000;
  let sheet = null, scrim = null, sheetState = null, lastTrigger = null;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en";
  const T = (ru, eng) => (en() ? eng : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
  const safeUrl = (value) => {
    const text = String(value || "").trim();
    if (!/^https?:\/\//i.test(text) || /[\s"'<>`]/.test(text)) return "";
    try { const url = new URL(text); return /^https?:$/.test(url.protocol) && !url.username && !url.password ? url.href : ""; }
    catch (e) { return ""; }
  };
  const hostOf = (value) => { try { return new URL(value).hostname.replace(/^www\./, ""); } catch (e) { return ""; } };

  const SVG = {
    bag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8h12l-1 12H7L6 8Z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>',
    left: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg>',
    right: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5L20 7"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/></svg>',
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 4 4 2.5-2.5L20 18"/></svg>',
    expand: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/></svg>',
    link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
    text: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 10h14M5 14h10M5 18h7"/></svg>',
    tag: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.5 1.5 0 0 1 0 2.1l-6.6 6.6a1.5 1.5 0 0 1-2.1 0Z"/><circle cx="8" cy="8" r="1.4"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
  };
  const ico = (name, cls = "") => `<span class="prd-ico${cls ? " " + cls : ""}">${SVG[name] || ""}</span>`;

  /* ------------------------------------------------------------ keys */
  function keyOf(raw) {
    let hash = 5381;
    const text = String(raw || "");
    for (let i = 0; i < text.length; i += 1) hash = ((hash * 33) ^ text.charCodeAt(i)) >>> 0;
    return "mp" + hash.toString(36) + text.length.toString(36);
  }

  /* ------------------------------------------------------------ formatting */
  const SIGNS = { RUB: "₽", USD: "$", EUR: "€", GBP: "£", KZT: "₸", UAH: "₴", BYN: "Br" };
  function money(price, { approxMark = true } = {}) {
    if (!price || !(Number(price.amount) > 0)) return "";
    const amount = Number(price.amount);
    const code = String(price.currency || "");
    const whole = Math.abs(amount - Math.round(amount)) < 0.005;
    let number;
    try {
      number = amount.toLocaleString(en() ? "en-US" : "ru-RU", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
    } catch (e) { number = String(amount); }
    const sign = SIGNS[code] || code;
    const body = ["USD", "GBP", "EUR"].includes(code) && en() ? `${sign}${number}` : `${number}\u00a0${sign}`;
    return (approxMark && price.approx ? "≈\u00a0" : "") + body;
  }
  function count(value) {
    const n = Number(value);
    if (!(n >= 0)) return "";
    try { return n.toLocaleString(en() ? "en-US" : "ru-RU"); } catch (e) { return String(n); }
  }
  function ratingText(value) {
    const n = Number(value);
    return n > 0 ? (en() ? n.toFixed(1) : n.toFixed(1).replace(".", ",")) : "";
  }
  function stars(value, size = "") {
    const n = Math.max(0, Math.min(5, Number(value) || 0));
    const pct = Math.round((n / 5) * 100);
    return `<span class="prd-stars${size ? " " + size : ""}" style="--prd-fill:${pct}%" role="img" aria-label="${esc(T(`Оценка ${ratingText(n)} из 5`, `Rated ${ratingText(n)} out of 5`))}"><span>★★★★★</span><span class="prd-stars-on" aria-hidden="true">★★★★★</span></span>`;
  }
  function dateText(ts) {
    if (!ts) return "";
    try { return new Date(ts * 1000).toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" }); }
    catch (e) { return ""; }
  }
  function availability(code) {
    return {
      in_stock: T("В наличии", "In stock"), out_of_stock: T("Нет в наличии", "Out of stock"),
      preorder: T("Предзаказ", "Pre-order"), unknown: "",
    }[code] || "";
  }
  function storeMark(offer, cls = "") {
    const name = String(offer.store || hostOf(offer.url) || "?");
    const letter = (name.match(/[A-Za-zА-Яа-яЁё0-9]/) || ["•"])[0].toUpperCase();
    const color = /^#[0-9a-f]{3,8}$/i.test(String(offer.color || "")) ? offer.color : "";
    let fg = "";
    if (color) {
      const hex = color.length === 4 ? color.slice(1).split("").map((c) => c + c).join("") : color.slice(1, 7);
      const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
      fg = (0.299 * r + 0.587 * g + 0.114 * b) > 170 ? "#1d1d1f" : "#fff";
    }
    return `<span class="prd-store${cls ? " " + cls : ""}"${color ? ` style="--prd-store:${color};--prd-store-fg:${fg}"` : ""} title="${esc(name)}" aria-hidden="true">${esc(letter)}</span>`;
  }

  /* ------------------------------------------------------------ local fallback */
  // Used only when the server is unreachable: show what is safe, mark every
  // price approximate, keep no review without a link and no "verified" flag.
  function localNormalize(raw) {
    let data;
    try {
      const text = String(raw || "").trim();
      data = JSON.parse(text);
    } catch (e) { return { ok: false }; }
    const rows = Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : [];
    const clean = (v, n) => String(v ?? "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, n);
    const items = rows.slice(0, 6).map((row, i) => {
      if (!row || typeof row !== "object") return null;
      const name = clean(row.name || row.title, 140);
      if (!name) return null;
      const priceRaw = row.price && typeof row.price === "object" ? row.price : { amount: row.price };
      const amount = Number(String(priceRaw.amount ?? "").replace(/[^\d.]/g, ""));
      return {
        id: "p" + (i + 1), name, brand: clean(row.brand, 60), model: clean(row.model, 80),
        best: !!row.best, badge: clean(row.badge, 28),
        key_specs: (Array.isArray(row.key_specs) ? row.key_specs : []).map((x) => clean(x, 80)).filter(Boolean).slice(0, 6),
        summary: clean(row.summary, 700),
        pros: (Array.isArray(row.pros) ? row.pros : []).map((x) => clean(x, 140)).filter(Boolean).slice(0, 5),
        cons: (Array.isArray(row.cons) ? row.cons : []).map((x) => clean(x, 140)).filter(Boolean).slice(0, 5),
        price: amount > 0 ? { amount, currency: clean(priceRaw.currency || data.currency, 3).toUpperCase(), approx: true } : null,
        images: (Array.isArray(row.images) ? row.images : []).map(safeUrl).filter(Boolean).slice(0, 8),
        offers: [], reviews: [],
      };
    }).filter(Boolean);
    let best = false;
    items.forEach((item) => { if (item.best && !best) best = true; else item.best = false; });
    return items.length ? { ok: true, local: true, data: { v: 1, items, currency: data.currency || "" } } : { ok: false };
  }

  /* ------------------------------------------------------------ server */
  function ensure(key) {
    if (cache.has(key) || inflight.has(key)) return inflight.get(key) || Promise.resolve(cache.get(key));
    const raw = rawByKey.get(key);
    const job = fetch("/api/pg/products/normalize", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw, lang: en() ? "en" : "ru" }),
    }).then((r) => (r.ok || r.status === 400 ? r.json() : Promise.reject(new Error("HTTP " + r.status))))
      .then((result) => (result && typeof result === "object" ? result : { ok: false }))
      .catch(() => localNormalize(raw))
      .then((result) => {
        cache.set(key, result);
        inflight.delete(key);
        if (cache.size > 300) cache.delete(cache.keys().next().value);
        swap(key);
        return result;
      });
    inflight.set(key, job);
    return job;
  }
  function swap(key) {
    document.querySelectorAll(`.memu-products[data-pkey="${key}"]:not([data-ready])`).forEach((host) => {
      const tpl = document.createElement("template");
      tpl.innerHTML = renderBlock(key).trim();
      const next = tpl.content.firstElementChild;
      if (next) host.replaceWith(next); else host.remove();
    });
    updateArrows();
  }

  /* ------------------------------------------------------------ md() hooks */
  function placeholder(raw, isEn) {
    const text = String(raw || "").trim();
    if (!text) return "";
    if (text.length > MAX_RAW) return `<div class="memu-products failed" role="note">${esc(T("Блок товаров слишком большой, чтобы его показать.", "The product block is too large to show."))}</div>`;
    const key = keyOf(text);
    rawByKey.set(key, text);
    if (cache.has(key)) return renderBlock(key);
    queueMicrotask(() => ensure(key));
    return skeleton(key);
  }
  function skeleton(key = "", n = 3) {
    const cards = Array.from({ length: n }, () => `<span class="prd-card prd-skel" aria-hidden="true"><span class="prd-img"></span><span class="prd-body"><i></i><i></i><i class="short"></i></span></span>`).join("");
    return `<div class="memu-products loading"${key ? ` data-pkey="${key}"` : ""} aria-busy="true"><div class="prd-head"><span class="prd-title">${ico("bag")}${esc(T("Подбираю товары…", "Finding products…"))}</span></div><div class="prd-row">${cards}</div></div>`;
  }
  function pending() { return skeleton("", 3); }
  // Streaming: an unfinished block becomes a skeleton instead of raw JSON.
  function maskPending(text) {
    return String(text || "").replace(/```[ \t]*memuai[-_ ]?products\b((?:(?!```)[\s\S])*)$/i, "```memuai-products-pending\n```");
  }

  /* ------------------------------------------------------------ the row */
  function itemPrice(item) {
    const price = item.price;
    if (!price) return `<span class="prd-price none">${esc(T("Цена не найдена", "No price found"))}</span>`;
    const from = price.from ? `<small>${esc(T("от", "from"))}</small> ` : "";
    return `<span class="prd-price${price.approx ? " approx" : ""}" title="${esc(price.approx ? T("Ориентировочная цена: не подтверждена страницей магазина", "Approximate price: not confirmed by a store page") : T("Цена со страницы магазина", "Price from a store page"))}">${from}${esc(money(price))}</span>`;
  }
  // Бюджет и наличие проверяет сервер (product_cards.normalize) по ценам со
  // страниц магазинов: товар без проверенной цены в бюджете не выдаётся за «в бюджете».
  function flagOf(item) {
    if (item.availability === "unavailable") {
      return { kind: "unavailable", text: item.discontinued ? T("Снят с продажи", "Discontinued") : T("Нет в продаже", "Not available"),
        hint: T("Ни в одном проверенном магазине товара нет в наличии", "No checked store has it in stock") };
    }
    if (item.budget_status === "over") return { kind: "over", text: T("Выше бюджета", "Over budget"),
      hint: T("Проверенные цены в магазинах выше вашего бюджета", "Verified store prices are above your budget") };
    // Without any price the card already says «Цена не найдена»; a second badge only repeats it.
    if (item.budget_status === "unverified" && item.price) return { kind: "unverified", text: T("Цена не подтверждена", "Price not confirmed"),
      hint: T("Нет цены со страницы магазина — укладывается ли товар в бюджет, неизвестно", "No store-page price — whether it fits the budget is unknown") };
    if (item.budget_status === "within") return { kind: "within", text: T("В бюджете", "Within budget"),
      hint: T("Есть проверенная цена в наличии не выше бюджета", "A verified in-stock price is within the budget") };
    return null;
  }
  function imageTag(src, alt, cls = "") {
    const url = safeUrl(src);
    if (!url) return `<span class="prd-noimg">${ico("image")}</span>`;
    return `<img class="${cls}" src="${esc(url)}" alt="${esc(alt)}" loading="lazy" decoding="async" referrerpolicy="no-referrer" draggable="false" data-prd-img>`;
  }
  function renderBlock(key) {
    const result = cache.get(key);
    if (!result || !result.ok || !result.data?.items?.length) {
      return `<div class="memu-products empty" data-pkey="${key}" data-ready="1" role="note">${ico("info")}<span>${esc(T("Карточки товаров не показаны: в блоке не нашлось проверяемых данных.", "Product cards are hidden: the block had no verifiable data."))}</span></div>`;
    }
    const items = result.data.items;
    const first = !shown.has(key);
    shown.add(key);
    const cards = items.map((item, index) => {
      const offers = item.offers || [];
      const marks = offers.slice(0, 3).map((o) => storeMark(o)).join("");
      const more = offers.length > 3 ? `<span class="prd-store more">+${offers.length - 3}</span>` : "";
      const label = [item.name, item.badge, item.price ? money(item.price) : "", item.rating ? T(`рейтинг ${ratingText(item.rating)}`, `rated ${ratingText(item.rating)}`) : ""].filter(Boolean).join(", ");
      const flag = flagOf(item);
      return `<div class="prd-cell" role="listitem" style="--prd-i:${index}"><button type="button" class="prd-card${item.best ? " best" : ""}${flag ? " flag-" + flag.kind : ""}" data-pkey="${key}" data-pi="${index}" aria-label="${esc([label, flag ? flag.text : ""].filter(Boolean).join(", "))}">
        <span class="prd-img">${imageTag((item.images || [])[0], item.name)}${item.badge || item.best ? `<span class="prd-badge${item.best ? " best" : ""}">${esc(item.badge || T("Лучший выбор", "Best pick"))}</span>` : ""}</span>
        <span class="prd-body">
          <span class="prd-name">${esc(item.name)}</span>
          ${itemPrice(item)}
          ${flag ? `<span class="prd-flag ${flag.kind}" title="${esc(flag.hint)}">${esc(flag.text)}</span>` : ""}
          ${item.rating ? `<span class="prd-rating">${stars(item.rating, "sm")}<b>${esc(ratingText(item.rating))}</b>${item.review_count ? `<span>(${esc(count(item.review_count))})</span>` : ""}</span>` : `<span class="prd-rating empty"></span>`}
          <span class="prd-stores">${marks}${more}${offers.length ? "" : `<span class="prd-nostore">${esc(T("Без ссылок на магазины", "No store links"))}</span>`}</span>
        </span>
      </button><button type="button" class="prd-copy" data-prd-copy-menu data-pkey="${key}" data-pi="${index}" aria-haspopup="menu" aria-label="${esc(T(`Копировать: ${item.name}`, `Copy: ${item.name}`))}" title="${esc(T("Копировать", "Copy"))}">${ico("copy")}</button></div>`;
    }).join("");
    const title = T("Товары", "Products");
    return `<div class="memu-products${first ? " prd-enter" : ""}${result.local ? " local" : ""}" data-pkey="${key}" data-ready="1">
      <div class="prd-head"><span class="prd-title">${ico("bag")}${esc(title)}<span class="prd-count">${items.length}</span></span>
        <span class="prd-nav"><button type="button" class="prd-arrow" data-prd-scroll="-1" aria-label="${esc(T("Прокрутить влево", "Scroll left"))}" hidden>${ico("left")}</button><button type="button" class="prd-arrow" data-prd-scroll="1" aria-label="${esc(T("Прокрутить вправо", "Scroll right"))}" hidden>${ico("right")}</button></span></div>
      <div class="prd-row" role="list" aria-label="${esc(T("Карточки товаров", "Product cards"))}">${cards}</div>
      ${result.local ? `<div class="prd-note">${esc(T("Не удалось проверить данные: показано только описание.", "Could not verify the data: only the description is shown."))}</div>` : ""}
    </div>`;
  }
  function updateArrows() {
    document.querySelectorAll(".memu-products[data-ready] .prd-row").forEach((row) => {
      const block = row.closest(".memu-products");
      const [prev, next] = block.querySelectorAll(".prd-arrow");
      if (!prev || !next) return;
      const overflow = row.scrollWidth - row.clientWidth > 4;
      prev.hidden = next.hidden = !overflow;
      prev.disabled = row.scrollLeft <= 2;
      next.disabled = row.scrollLeft >= row.scrollWidth - row.clientWidth - 2;
    });
  }

  /* ------------------------------------------------------------ the side sheet */
  function ensureSheet() {
    if (sheet) return sheet;
    scrim = document.createElement("div");
    scrim.className = "prd-scrim";
    scrim.addEventListener("click", closeSheet);
    sheet = document.createElement("aside");
    sheet.className = "prd-sheet";
    sheet.id = "prd-sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "false");
    sheet.setAttribute("aria-labelledby", "prd-sheet-title");
    sheet.tabIndex = -1;
    sheet.hidden = true;
    document.body.append(scrim, sheet);
    sheet.addEventListener("keydown", (event) => {
      if (event.key === "Tab") trapFocus(event);
      if (event.target.closest(".prd-gallery") && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
        event.preventDefault();
        galleryStep(event.key === "ArrowLeft" ? -1 : 1);
      }
    });
    return sheet;
  }
  function trapFocus(event) {
    const items = [...sheet.querySelectorAll('button:not([disabled]), a[href], [tabindex="0"]')].filter((el) => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  function sheetHtml(item, index, total) {
    const images = item.images || [];
    const gallery = `<div class="prd-gallery" tabindex="0" aria-roledescription="${esc(T("галерея", "gallery"))}" aria-label="${esc(T("Фотографии товара", "Product photos"))}">
      <div class="prd-gallery-track" style="--prd-g:0">${images.length ? images.map((src, i) => `<button type="button" class="prd-slide" data-prd-zoom="${i}" aria-label="${esc(T(`Фото ${i + 1} из ${images.length}, открыть крупно`, `Photo ${i + 1} of ${images.length}, open large`))}">${imageTag(src, item.name)}</button>`).join("") : `<div class="prd-slide empty">${ico("image")}<span>${esc(T("Фото не найдено", "No photo found"))}</span></div>`}</div>
      ${images.length > 1 ? `<button type="button" class="prd-g-btn prev" data-prd-g="-1" aria-label="${esc(T("Предыдущее фото", "Previous photo"))}">${ico("left")}</button><button type="button" class="prd-g-btn next" data-prd-g="1" aria-label="${esc(T("Следующее фото", "Next photo"))}">${ico("right")}</button>
      <div class="prd-thumbs">${images.map((src, i) => `<button type="button" class="prd-thumb${i ? "" : " on"}" data-prd-thumb="${i}" aria-label="${esc(T(`Фото ${i + 1}`, `Photo ${i + 1}`))}">${imageTag(src, "")}</button>`).join("")}</div>` : ""}
      ${images.length ? `<span class="prd-zoom-hint">${ico("expand")}</span>` : ""}
    </div>`;
    const price = item.price;
    const priceNote = !price ? T("Цену не удалось найти", "No price could be found")
      : price.approx ? T("Ориентировочно — цена не подтверждена страницей магазина", "Approximate — not confirmed by a store page")
        : [price.store ? T(`Лучшая цена — ${price.store}`, `Best price — ${price.store}`) : "", price.checked_at ? T(`проверено ${dateText(price.checked_at)}`, `checked ${dateText(price.checked_at)}`) : ""].filter(Boolean).join(" · ");
    const offers = (item.offers || []).map((offer) => {
      const url = safeUrl(offer.url);
      const avail = availability(offer.availability);
      const p = offer.price ? money(offer.price) : "";
      return `<li class="prd-offer">
        ${storeMark(offer, "lg")}
        <span class="prd-offer-main"><b>${esc(offer.store)}${offer.verified ? `<span class="prd-ok" title="${esc(T("Цена и наличие взяты со страницы магазина", "Price and stock taken from the store page"))}${offer.checked_at ? " · " + esc(dateText(offer.checked_at)) : ""}">${ico("check")}</span>` : ""}</b><small>${[avail ? `<span class="prd-avail ${esc(offer.availability)}">${esc(avail)}</span>` : "", !offer.verified && offer.price ? `<span>${esc(T("цена не подтверждена", "unconfirmed price"))}</span>` : ""].filter(Boolean).join(" · ") || esc(hostOf(url))}</small></span>
        <span class="prd-offer-price${offer.price?.approx ? " approx" : ""}">${esc(p || "—")}</span>
        ${url ? `<button type="button" class="prd-icon-btn prd-copy-link" data-prd-copy="link" data-url="${esc(url)}" aria-label="${esc(T(`Копировать ссылку: ${offer.store}`, `Copy link: ${offer.store}`))}" title="${esc(T("Копировать ссылку", "Copy link"))}">${ico("link")}</button>
        <a class="prd-open" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${esc(url)}">${esc(T("Открыть", "Open"))}${ico("external")}</a>` : "<span></span><span></span>"}
      </li>`;
    }).join("");
    // Магазины региона без оффера: честно «не удалось проверить» / «не найден»,
    // с поиском по самому магазину, чтобы проверить вручную.
    const checks = (item.store_checks || []).map((check) => {
      const found = check.status === "unconfirmed" ? safeUrl(check.url) : "";
      const search = found || safeUrl(check.search_url);
      const text = check.status === "not_found" ? T("Не найден", "Not found")
        : check.status === "unconfirmed" ? T("Найден, цена не подтверждена", "Found, price not confirmed")
          : T("Не удалось проверить", "Could not check");
      const hint = check.status === "not_found" ? T("Поиск по магазину не нашёл этот товар", "The store search did not find this product")
        : check.status === "unconfirmed" ? T("Страница товара нашлась в поиске, но магазин не отдал цену", "The product page was found, but the store did not return a price")
          : T("Магазин закрыл страницу защитой от ботов или не ответил — наличие неизвестно", "The store blocked the request or did not answer — availability is unknown");
      return `<li class="prd-offer unchecked">
        ${storeMark(check, "lg")}
        <span class="prd-offer-main" title="${esc(hint)}"><b>${esc(check.store)}</b><small>${esc(text)}</small></span>
        <span class="prd-offer-price">—</span><span></span>
        ${search ? `<a class="prd-open ghost" href="${esc(search)}" target="_blank" rel="noopener noreferrer" title="${esc(search)}">${found ? esc(T("Открыть", "Open")) + ico("external") : esc(T("Искать", "Search")) + ico("search")}</a>` : "<span></span>"}
      </li>`;
    }).join("");
    const flag = flagOf(item);
    const specs = (item.key_specs || []).map((spec) => `<li>${esc(spec)}</li>`).join("");
    const pc = (list, cls, sign) => (list || []).length ? `<div class="prd-pc ${cls}"><h4>${cls === "pros" ? esc(T("Плюсы", "Pros")) : esc(T("Минусы", "Cons"))}</h4><ul>${list.map((x) => `<li>${ico(sign)}<span>${esc(x)}</span></li>`).join("")}</ul></div>` : "";
    const reviews = (item.reviews || []).map((review) => {
      const url = safeUrl(review.url);
      return `<figure class="prd-review">
        ${review.rating ? `<div class="prd-review-stars">${stars(review.rating, "sm")}</div>` : ""}
        <blockquote>${esc(review.text)}</blockquote>
        <figcaption>${review.author ? `<b>${esc(review.author)}</b> · ` : ""}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(review.source || hostOf(url))}${ico("external")}</a>` : esc(review.source || "")}${review.verified ? ` <span class="prd-ok" title="${esc(T("Цитата найдена на странице источника", "Quote found on the source page"))}">${ico("check")}${esc(T("проверено", "verified"))}</span>` : ""}</figcaption>
      </figure>`;
    }).join("");
    const ratingSource = safeUrl(item.rating_source);
    const meta = [item.brand, item.model].filter(Boolean).join(" · ");
    return `<div class="prd-sheet-head">
        <span class="prd-sheet-kicker">${ico("bag")}${esc(T("Товар", "Product"))} <b>${index + 1}</b> / ${total}</span>
        <span class="prd-grow"></span>
        <button type="button" class="prd-icon-btn" data-prd-copy-menu aria-haspopup="menu" aria-label="${esc(T("Копировать карточку", "Copy the card"))}" title="${esc(T("Копировать", "Copy"))}">${ico("copy")}</button>
        <button type="button" class="prd-icon-btn" data-prd-item="-1" aria-label="${esc(T("Предыдущий товар", "Previous product"))}" ${index ? "" : "disabled"}>${ico("left")}</button>
        <button type="button" class="prd-icon-btn" data-prd-item="1" aria-label="${esc(T("Следующий товар", "Next product"))}" ${index < total - 1 ? "" : "disabled"}>${ico("right")}</button>
        <button type="button" class="prd-icon-btn" data-prd-close aria-label="${esc(T("Закрыть", "Close"))}">${ico("close")}</button>
      </div>
      <div class="prd-sheet-scroll">
        ${gallery}
        <div class="prd-sheet-titles">
          ${item.badge || item.best ? `<span class="prd-badge inline${item.best ? " best" : ""}">${esc(item.badge || T("Лучший выбор", "Best pick"))}</span>` : ""}
          <h3 id="prd-sheet-title">${esc(item.name)}<button type="button" class="prd-icon-btn prd-copy-name" data-prd-copy="name" aria-label="${esc(T("Копировать название и модель", "Copy name and model"))}" title="${esc(T("Копировать название", "Copy name"))}">${ico("copy")}</button></h3>
          ${meta ? `<div class="prd-meta">${esc(meta)}</div>` : ""}
        </div>
        <div class="prd-price-block">
          <div class="prd-price-big${price?.approx ? " approx" : ""}">${price ? `${price.from ? `<small>${esc(T("от", "from"))}</small> ` : ""}${esc(money(price))}` : "—"}</div>
          <div class="prd-price-note">${esc(priceNote)}</div>
          ${flag ? `<div class="prd-flag ${flag.kind} wide">${esc(flag.text)}<span>${esc(flag.hint)}</span></div>` : ""}
          ${item.rating ? `<div class="prd-rating big">${stars(item.rating)}<b>${esc(ratingText(item.rating))}</b>${item.review_count ? `<span>${esc(T(`${count(item.review_count)} оценок`, `${count(item.review_count)} ratings`))}</span>` : ""}${ratingSource ? `<a href="${esc(ratingSource)}" target="_blank" rel="noopener noreferrer">${esc(item.rating_store || hostOf(ratingSource))}${ico("external")}</a>` : ""}</div>` : ""}
        </div>
        <section class="prd-section"><h4>${esc(T("Где купить", "Where to buy"))}</h4>
          ${offers || checks ? `<ul class="prd-offers">${offers}${checks}</ul>` : `<p class="prd-muted">${esc(T("Ссылок на магазины нет: проверенных предложений не нашлось. Попросите MemuAI поискать с включённым веб-поиском.", "No store links: no verified offers were found. Ask MemuAI to search with web search turned on."))}</p>`}
        </section>
        ${specs ? `<section class="prd-section"><h4>${esc(T("Главное", "Key specs"))}</h4><ul class="prd-specs">${specs}</ul></section>` : ""}
        ${item.summary ? `<section class="prd-section prd-summary"><h4>${esc(T("Кратко от MemuAI", "MemuAI summary"))}</h4><p>${esc(item.summary)}</p></section>` : ""}
        ${(item.pros || []).length || (item.cons || []).length ? `<section class="prd-section prd-pcs">${pc(item.pros, "pros", "plus")}${pc(item.cons, "cons", "minus")}</section>` : ""}
        ${reviews ? `<section class="prd-section"><h4>${esc(T("Отзывы", "Reviews"))}</h4><div class="prd-reviews">${reviews}</div></section>` : ""}
        <p class="prd-foot">${ico("info")}<span>${esc(T("Цены и наличие меняются. Данные собраны с открытых страниц магазинов и сайтов с отзывами; перед покупкой проверьте их у продавца.", "Prices and availability change. The data comes from public store and review pages; check with the seller before buying."))}</span></p>
      </div>`;
  }
  function openSheet(key, index, trigger) {
    const result = cache.get(key);
    const items = result?.data?.items || [];
    if (!items[index]) return;
    ensureSheet();
    const opening = sheet.hidden;
    sheetState = { key, index, gallery: 0 };
    if (trigger) lastTrigger = trigger;
    sheet.innerHTML = sheetHtml(items[index], index, items.length);
    document.querySelectorAll(".prd-card.active").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(`.prd-card[data-pkey="${key}"][data-pi="${index}"]`).forEach((el) => el.classList.add("active"));
    if (opening) {
      sheet.hidden = false;
      scrim.hidden = false;
      // Next frame: the transition runs from the off-screen state.
      requestAnimationFrame(() => { sheet.classList.add("open"); scrim.classList.add("open"); document.documentElement.classList.add("prd-sheet-open"); });
      sheet.focus({ preventScroll: true });
    } else {
      sheet.querySelector(".prd-sheet-scroll")?.scrollTo(0, 0);
    }
  }
  function closeSheet() {
    if (!sheet || sheet.hidden) return;
    sheet.classList.remove("open");
    scrim.classList.remove("open");
    document.documentElement.classList.remove("prd-sheet-open");
    document.querySelectorAll(".prd-card.active").forEach((el) => el.classList.remove("active"));
    const done = () => { if (!sheet.classList.contains("open")) { sheet.hidden = true; scrim.hidden = true; } };
    const reduced = document.body.classList.contains("reduce-motion") || document.documentElement.dataset.motion === "off"
      || matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) done(); else setTimeout(done, 340);
    const back = lastTrigger && lastTrigger.isConnected ? lastTrigger
      : (sheetState ? document.querySelector(`.prd-card[data-pkey="${sheetState.key}"][data-pi="${sheetState.index}"]`) : null);
    sheetState = null;
    if (back) back.focus({ preventScroll: true });
  }
  function galleryGo(index) {
    if (!sheet || !sheetState) return;
    const slides = sheet.querySelectorAll(".prd-gallery .prd-slide");
    if (!slides.length) return;
    const n = slides.length;
    sheetState.gallery = (index + n) % n;
    sheet.querySelector(".prd-gallery-track")?.style.setProperty("--prd-g", String(sheetState.gallery));
    sheet.querySelectorAll(".prd-thumb").forEach((el, i) => el.classList.toggle("on", i === sheetState.gallery));
    sheet.querySelector(".prd-thumb.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  function galleryStep(dir) { if (sheetState) galleryGo(sheetState.gallery + dir); }
  function zoom(index) {
    const item = cache.get(sheetState?.key)?.data?.items?.[sheetState.index];
    const src = safeUrl(item?.images?.[index]);
    if (!src) return;
    if (typeof openImagePreview === "function") openImagePreview(src, item.name);
    else window.open(src, "_blank", "noopener");
  }

  /* ------------------------------------------------------------ events */
  document.addEventListener("click", (event) => {
    const card = event.target.closest(".prd-card[data-pkey]");
    if (card) { openSheet(card.dataset.pkey, Number(card.dataset.pi) || 0, card); return; }
    const arrow = event.target.closest("[data-prd-scroll]");
    if (arrow) {
      const row = arrow.closest(".memu-products")?.querySelector(".prd-row");
      if (row) row.scrollBy({ left: Number(arrow.dataset.prdScroll) * Math.max(220, row.clientWidth * 0.8), behavior: "smooth" });
      return;
    }
    if (!sheet || !sheet.contains(event.target)) return;
    if (event.target.closest("[data-prd-close]")) return closeSheet();
    const step = event.target.closest("[data-prd-item]");
    if (step && sheetState) return openSheet(sheetState.key, sheetState.index + Number(step.dataset.prdItem), null);
    const g = event.target.closest("[data-prd-g]");
    if (g) return galleryStep(Number(g.dataset.prdG));
    const thumb = event.target.closest("[data-prd-thumb]");
    if (thumb) return galleryGo(Number(thumb.dataset.prdThumb));
    const slide = event.target.closest("[data-prd-zoom]");
    if (slide && !sheet.dataset.swiped) return zoom(Number(slide.dataset.prdZoom));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sheet && !sheet.hidden) {
      if (document.getElementById("image-viewer")?.classList.contains("visible")) return;
      event.stopPropagation();
      closeSheet();
      return;
    }
    const card = event.target.closest?.(".prd-card[data-pkey]");
    if (!card) return;
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      const cards = [...(card.closest(".prd-row") || card.parentElement).querySelectorAll(".prd-card[data-pkey]")];
      const at = cards.indexOf(card);
      const next = event.key === "Home" ? 0 : event.key === "End" ? cards.length - 1
        : Math.max(0, Math.min(cards.length - 1, at + (event.key === "ArrowLeft" ? -1 : 1)));
      event.preventDefault();
      cards[next].focus();
      cards[next].scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    }
  }, true);
  // Swipe in the gallery (pointer events: mouse, pen and touch).
  let swipe = null;
  document.addEventListener("pointerdown", (event) => {
    const track = event.target.closest?.(".prd-sheet .prd-gallery");
    if (!track || event.button > 0) return;
    swipe = { x: event.clientX, y: event.clientY, id: event.pointerId };
    delete sheet.dataset.swiped;
  });
  document.addEventListener("pointerup", (event) => {
    if (!swipe || event.pointerId !== swipe.id) return;
    const dx = event.clientX - swipe.x, dy = event.clientY - swipe.y;
    swipe = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.3) {
      sheet.dataset.swiped = "1";
      setTimeout(() => { if (sheet) delete sheet.dataset.swiped; }, 50);
      galleryStep(dx < 0 ? 1 : -1);
    }
  });
  // Images that refuse to load: one retry through the app's media bridge
  // (hotlink protection), then a neutral placeholder.
  document.addEventListener("error", (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || !img.hasAttribute("data-prd-img")) return;
    if (!img.dataset.prdRetry && /^https?:/i.test(img.src)) {
      img.dataset.prdRetry = "1";
      img.src = "/api/pg/media?limit_mb=8&url=" + encodeURIComponent(img.src);
      return;
    }
    const box = document.createElement("span");
    box.className = "prd-noimg";
    box.innerHTML = SVG.image;
    img.replaceWith(box);
  }, true);
  document.addEventListener("scroll", (event) => {
    if (event.target instanceof Element && event.target.classList.contains("prd-row")) updateArrows();
  }, true);
  window.addEventListener("resize", () => updateArrows());

  /* ------------------------------------------------------------ text fallback */
  function itemsFor(raw) {
    const text = String(raw || "").trim();
    const key = keyOf(text);
    const result = cache.get(key) || localNormalize(text);
    return result?.ok ? result.data.items : [];
  }
  // Copy/export get Markdown; speech gets one short sentence per product.
  function stripForText(text, isEn, mode = "markdown") {
    const english = isEn === undefined ? en() : !!isEn;
    const label = (ru, eng) => (english ? eng : ru);
    return String(text || "").replace(/```[ \t]*memuai[-_ ]?products\b[ \t]*\r?\n?([\s\S]*?)(?:```|$)/gi, (all, body) => {
      const items = itemsFor(body);
      if (!items.length) return "";
      if (mode === "speech") return items.map((item) => [item.name, item.price ? money({ ...item.price, approx: false }) : ""].filter(Boolean).join(", ")).join(". ") + ".";
      return "\n" + items.map((item) => {
        let head = `- **${item.name}**`;
        if (item.price) head += ` — ${money(item.price)}`;
        if (item.rating) head += ` · ★ ${ratingText(item.rating)}${item.review_count ? ` (${count(item.review_count)})` : ""}`;
        if (item.best) head += ` — ${item.badge || label("лучший выбор", "best pick")}`;
        const flag = flagOf(item);
        if (flag && flag.kind !== "within") head += ` — ${flag.text.toLowerCase()}`;
        const lines = [head];
        if (item.summary) lines.push(`  ${item.summary}`);
        (item.offers || []).slice(0, 4).forEach((o) => lines.push(`  - ${o.store}: ${o.price ? money(o.price) : "—"} — ${o.url}`));
        (item.store_checks || []).forEach((c) => lines.push(`  - ${c.store}: ${c.status === "not_found" ? label("не найден", "not found")
          : c.status === "unconfirmed" ? label("найден, цена не подтверждена", "found, price not confirmed") : label("не удалось проверить", "could not check")}`));
        return lines.join("\n");
      }).join("\n") + "\n";
    });
  }

  /* ------------------------------------------------------------ copy */
  // «Название (модель)»: модель добавляется, только если её ещё нет в названии.
  function nameText(item) {
    const name = String(item?.name || "").trim();
    const model = String(item?.model || "").trim();
    return model && !name.toLowerCase().includes(model.toLowerCase()) ? `${name} (${model})` : name;
  }
  // Ссылка на лучшее предложение: проверенная цена → самая дешёвая из них, иначе первое.
  function offerLink(item) {
    const offers = (item?.offers || []).filter((o) => safeUrl(o.url));
    const source = safeUrl(item?.price?.source);
    if (source && offers.some((o) => o.url === source)) return source;
    const verified = offers.filter((o) => o.verified && o.price && o.availability !== "out_of_stock");
    const pick = verified.sort((a, b) => a.price.amount - b.price.amount)[0] || offers[0];
    return pick ? safeUrl(pick.url) : "";
  }
  // Вся карточка простым текстом (мессенджер, заметка): только проверенные поля.
  function cardText(item) {
    if (!item) return "";
    const lines = [nameText(item)];
    const flag = flagOf(item);
    const tags = [item.best ? (item.badge || T("Лучший выбор", "Best pick")) : item.badge, flag ? flag.text : ""].filter(Boolean);
    if (tags.length) lines.push(tags.join(" · "));
    if (item.price) {
      const where = item.price.approx ? T("ориентировочно, не подтверждена магазином", "approximate, not confirmed by a store")
        : [item.price.store, item.price.checked_at ? T(`проверено ${dateText(item.price.checked_at)}`, `checked ${dateText(item.price.checked_at)}`) : ""].filter(Boolean).join(", ");
      lines.push(`${T("Цена", "Price")}: ${item.price.from ? T("от ", "from ") : ""}${money(item.price)}${where ? ` (${where})` : ""}`);
    }
    if (item.rating) {
      lines.push(`${T("Рейтинг", "Rating")}: ${ratingText(item.rating)} ${T("из 5", "of 5")}${item.review_count ? T(` (${count(item.review_count)} оценок`, ` (${count(item.review_count)} ratings`) + (item.rating_store ? `, ${item.rating_store})` : ")") : item.rating_store ? ` (${item.rating_store})` : ""}`);
    }
    if ((item.key_specs || []).length) lines.push(`${T("Главное", "Key specs")}: ${item.key_specs.join("; ")}`);
    if (item.summary) lines.push(`${T("Кратко", "Summary")}: ${item.summary}`);
    if ((item.pros || []).length) lines.push(`${T("Плюсы", "Pros")}: ${item.pros.join("; ")}`);
    if ((item.cons || []).length) lines.push(`${T("Минусы", "Cons")}: ${item.cons.join("; ")}`);
    const offers = (item.offers || []).filter((o) => safeUrl(o.url));
    const checks = item.store_checks || [];
    if (offers.length || checks.length) {
      lines.push(`${T("Где купить", "Where to buy")}:`);
      offers.forEach((o) => {
        const avail = availability(o.availability);
        lines.push(`- ${o.store}: ${o.price ? money(o.price) : "—"}${avail ? ` · ${avail.toLowerCase()}` : ""}${o.price && !o.verified ? T(" · цена не подтверждена", " · unconfirmed price") : ""} — ${safeUrl(o.url)}`);
      });
      checks.forEach((c) => lines.push(`- ${c.store}: ${c.status === "not_found" ? T("не найден", "not found")
        : c.status === "unconfirmed" ? T("найден, цена не подтверждена", "found, price not confirmed") : T("не удалось проверить", "could not check")}${c.status === "unconfirmed" && safeUrl(c.url) ? ` — ${safeUrl(c.url)}` : ""}`));
    }
    lines.push(T("Цены и наличие меняются — проверьте у продавца.", "Prices and availability change — check with the seller."));
    return lines.join("\n");
  }
  function itemOf(key, index) { return cache.get(key)?.data?.items?.[index] || null; }
  async function copyValue(text, done) {
    const value = String(text || "");
    if (!value) { notify(T("Нечего копировать", "Nothing to copy"), "warn"); return false; }
    let ok = false;
    try {
      if (typeof copyText === "function") ok = await copyText(value, false);
      else { await navigator.clipboard.writeText(value); ok = true; }
    } catch (e) { ok = false; }
    notify(ok ? done : T("Не удалось скопировать", "Could not copy"), ok ? "ok" : "err");
    return ok;
  }
  function notify(text, kind) { if (typeof toast === "function") toast(text, kind); }
  function copyAction(kind, item, url) {
    if (kind === "name") return copyValue(nameText(item), T("Название скопировано", "Name copied"));
    if (kind === "link") return copyValue(url || offerLink(item), T("Ссылка скопирована", "Link copied"));
    return copyValue(cardText(item), T("Карточка скопирована", "Card copied"));
  }
  // Небольшое меню у кнопки копирования (карточка в ряду и шапка панели).
  let menu = null, menuFor = null;
  function closeMenu(focusBack = false) {
    if (!menu) return;
    const back = menuFor;
    menu.remove(); menu = null; menuFor = null;
    if (back) back.setAttribute("aria-expanded", "false");
    if (focusBack && back?.isConnected) back.focus({ preventScroll: true });
  }
  function openMenu(button, key, index) {
    if (menu && menuFor === button) { closeMenu(); return; }
    closeMenu();
    const item = itemOf(key, index);
    if (!item) return;
    const link = offerLink(item);
    menu = document.createElement("div");
    menu.className = "prd-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = [
      ["name", "tag", T("Название и модель", "Name and model")],
      ["link", "link", T("Ссылку на предложение", "Link to the offer"), !link],
      ["card", "text", T("Всю карточку текстом", "The whole card as text")],
    ].map(([kind, icon, label, off]) => `<button type="button" role="menuitem" data-prd-menu="${kind}"${off ? " disabled" : ""}>${ico(icon)}<span>${esc(label)}</span></button>`).join("");
    menu.dataset.pkey = key; menu.dataset.pi = String(index);
    document.body.append(menu);
    menuFor = button;
    button.setAttribute("aria-expanded", "true");
    const r = button.getBoundingClientRect(), m = menu.getBoundingClientRect();
    const left = Math.max(8, Math.min(innerWidth - m.width - 8, r.right - m.width));
    const below = r.bottom + 6 + m.height < innerHeight - 8;
    menu.style.left = left + "px";
    menu.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - 6 - m.height)) + "px";
    menu.querySelector("button:not([disabled])")?.focus({ preventScroll: true });
  }
  document.addEventListener("keydown", (event) => {
    if (!menu) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(true); return; }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const items = [...menu.querySelectorAll("button:not([disabled])")];
      const at = items.indexOf(document.activeElement);
      event.preventDefault();
      items[(at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }
    if (event.key === "Tab") closeMenu();
  }, true);
  document.addEventListener("pointerdown", (event) => {
    if (menu && !menu.contains(event.target) && !event.target.closest?.("[data-prd-copy-menu]")) closeMenu();
  }, true);
  document.addEventListener("scroll", (event) => { if (menu && !menu.contains(event.target)) closeMenu(); }, true);
  window.addEventListener("resize", () => closeMenu());
  document.addEventListener("click", (event) => {
    const choice = event.target.closest?.("[data-prd-menu]");
    if (choice && menu?.contains(choice)) {
      const item = itemOf(menu.dataset.pkey, Number(menu.dataset.pi));
      const kind = choice.dataset.prdMenu;
      closeMenu(true);
      if (item) copyAction(kind, item);
      return;
    }
    const opener = event.target.closest?.("[data-prd-copy-menu]");
    if (opener) {
      event.stopPropagation();
      const key = opener.dataset.pkey || sheetState?.key;
      const index = opener.dataset.pi != null ? Number(opener.dataset.pi) : sheetState?.index;
      if (key != null && index != null) openMenu(opener, key, index);
      return;
    }
    const direct = event.target.closest?.("[data-prd-copy]");
    if (direct && sheet?.contains(direct) && sheetState) {
      event.stopPropagation();
      copyAction(direct.dataset.prdCopy, itemOf(sheetState.key, sheetState.index), direct.dataset.url || "");
    }
  }, true);

  /* ------------------------------------------------------------ research progress */
  // Пока модель исследует (web_search → product_lookup), в ответе стоит понятный
  // прогресс со скелетом карточек, а черновой текст раундов не показывается —
  // раньше он появлялся и исчезал на каждом раунде инструментов.
  const INTENT_RU = /(подбер|посоветуй|порекомендуй|помоги (?:мне )?(?:выбрать|подобрать)|что (?:лучше )?(?:купить|взять|выбрать)|как(?:ой|ую|ое|ие) .{0,40}(?:купить|взять|выбрать)|(?:купить|выбрать|взять) .{0,50}(?:до|за|не дороже|в пределах)\s*\d|лучш\w* .{0,50}(?:до|за|не дороже)\s*\d|до\s*\d[\d\s.,]*\s*(?:₽|руб|р\b|к\b|тыс)|альтернатив\w* .{0,40}(?:дешевле|подешевле)|(?:сравни|сравнить) .{0,80}(?:какой|какую|что) .{0,20}(?:купить|взять|выбрать))/i;
  const INTENT_EN = /(recommend (?:me )?(?:a|an|the|some)?\s*\w+|which .{0,40}(?:to buy|should i (?:buy|get|pick))|what .{0,40}(?:to buy|should i (?:buy|get))|best .{0,50}(?:under|below|for less than|up to)\s*\$?\d|under \$\s*\d|cheaper alternative|help me (?:choose|pick|find) (?:a|an|the)\b|(?:shopping|buying) (?:for|advice))/i;
  function intent(text) {
    const value = String(text || "").slice(0, 2000);
    if (value.length < 6) return false;
    if (/(не (?:надо|нужно) (?:карточ|ссыл|покуп))|без карточек|plain text|no cards/i.test(value)) return false;
    return INTENT_RU.test(value) || INTENT_EN.test(value);
  }
  function inFlow(m) {
    if (m.productIntent) return true;
    const used = (m.toolHistory || []).some((row) => row.tool === "product_lookup");
    return used || (m.toolCalls?.calls || []).some((call) => call.tool === "product_lookup");
  }
  function stageOf(m) {
    const busy = ["pending", "working"].includes(m.toolCalls?.status);
    const now = busy ? (m.toolCalls.calls || []) : [];
    const done = new Set((m.toolHistory || []).map((row) => row.tool).filter(Boolean));
    const lookups = now.filter((call) => call.tool === "product_lookup");
    const locale = (lookups.find((c) => c.args?.locale)?.args?.locale) || (en() ? "en" : "ru");
    const stores = Array.isArray(lookups[0]?.args?.stores) && lookups[0].args.stores.length ? lookups[0].args.stores.length
      : String(locale).startsWith("en") ? 6 : 7;
    let step = "search";
    if (lookups.length) step = "prices";
    else if (now.length) step = done.has("product_lookup") ? "prices" : "search";
    else if (done.has("product_lookup")) step = "cards";
    else if (done.has("web_search") || done.has("web_fetch")) step = "compare";
    const names = lookups.map((c) => String(c.args?.query || "").trim()).filter(Boolean).slice(0, 8);
    return { step, stores, count: lookups.length, names };
  }
  // Вызывается из app.js для ответа в потоке: строка-замена видимого текста или null.
  function holdStream(m, visible) {
    if (!m || m.role !== "assistant" || m.error) return null;
    if (!m.streaming || !inFlow(m)) return null;    // streaming остаётся true между раундами инструментов
    if (/```[ \t]*memuai[-_ ]?products\b/i.test(String(m.content || ""))) return null;   // финальный ответ с карточками
    if (String(visible || "").length > 1500) return null;                                // длинный ответ — не черновик
    return "```memuai-products-progress\n" + JSON.stringify(stageOf(m)) + "\n```";
  }
  function plural(n, one, few, many) {
    const k = Math.abs(Number(n) || 0) % 100, d = k % 10;
    const word = k > 10 && k < 20 ? many : d === 1 ? one : d >= 2 && d <= 4 ? few : many;
    return `${n} ${word}`;
  }
  function progress(raw) {
    let state = {};
    try { state = JSON.parse(String(raw || "{}")); } catch (e) { state = {}; }
    const order = ["search", "compare", "prices", "cards"];
    const at = Math.max(0, order.indexOf(state.step));
    const steps = [
      { id: "search", label: T("Ищу актуальные модели и обзоры", "Looking for current models and reviews"), upto: 2 },
      { id: "prices", label: state.count > 1 ? T(`Проверяю цены и наличие: ${plural(state.count, "модель", "модели", "моделей")}, ${plural(state.stores || 7, "магазин", "магазина", "магазинов")}`, `Checking prices and stock: ${state.count} models, ${state.stores || 6} stores`)
        : T(`Проверяю цены в ${plural(state.stores || 7, "магазине", "магазинах", "магазинах")}`, `Checking prices in ${state.stores || 6} stores`), upto: 3 },
      { id: "cards", label: T("Сравниваю и собираю карточки", "Comparing and building the cards"), upto: 4 },
    ];
    const headline = { search: T("Ищу товары…", "Finding products…"), compare: T("Выбираю кандидатов…", "Choosing candidates…"),
      prices: T("Проверяю цены в магазинах…", "Checking store prices…"), cards: T("Собираю карточки…", "Building the cards…") }[order[at]];
    const list = steps.map((step, i) => {
      const startAt = i === 0 ? 0 : steps[i - 1].upto;
      const state2 = at >= step.upto ? "done" : at >= startAt ? "now" : "todo";
      return `<li class="${state2}"><i aria-hidden="true">${state2 === "done" ? SVG.check : ""}</i><span>${esc(step.label)}</span></li>`;
    }).join("");
    const names = (state.names || []).length ? `<div class="prd-progress-names">${state.names.map((n) => `<span>${esc(String(n).slice(0, 60))}</span>`).join("")}</div>` : "";
    const cards = Array.from({ length: 3 }, () => `<span class="prd-card prd-skel" aria-hidden="true"><span class="prd-img"></span><span class="prd-body"><i></i><i></i><i class="short"></i></span></span>`).join("");
    return `<div class="memu-products loading prd-progress" data-step="${esc(order[at])}" aria-busy="true">
      <div class="prd-head"><span class="prd-title" role="status" aria-live="polite">${ico("bag")}${esc(headline)}</span></div>
      <ol class="prd-steps">${list}</ol>${names}
      <div class="prd-row">${cards}</div></div>`;
  }

  /* ------------------------------------------------------------ system prompt */
  function promptText() {
    try { if (typeof internalPromptText === "function") return internalPromptText("product-cards"); } catch (e) {}
    return "";
  }
  // The self-contained prompt section (Settings → Prompts → "Product cards")
  // plus one runtime line the model needs: region, currency, tool status.
  function systemBlock({ lang = "ru", lookup = false, builtinSearch = false } = {}) {
    const text = promptText();
    if (!text) return "";
    const ru = lang !== "en";
    const tool = lookup ? "available" : builtinSearch ? "unavailable (use your built-in web search for real data)" : "unavailable (web search is off)";
    return `<product_cards>\n${text}\n<product_cards_runtime locale="${ru ? "ru" : "en"}" region="${ru ? "Russia" : "USA"}" currency="${ru ? "RUB" : "USD"}" product_lookup="${tool}"/>\n</product_cards>`;
  }

  return Object.freeze({ placeholder, pending, maskPending, stripForText, systemBlock, openSheet, closeSheet,
    intent, holdStream, progress, cardText, nameText, offerLink,
    _normalizeLocal: localNormalize, _cache: cache, _keyOf: keyOf, _money: money, _stageOf: stageOf });
})();
