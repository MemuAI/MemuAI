/* MemuMusic · art.js — генеративные обложки, палитры, значки сервисов и
 * мгновенный детект источника по ссылке (тот же разбор, что у бэкенда, но
 * локально: логотип появляется ещё до ответа моста). */

function hash(text) {
  let h = 2166136261;
  const s = String(text || "");
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function seeded(seed) {
  let a = seed >>> 0 || 1;
  return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function hsl(h, s, l) {
  h = ((h % 360) + 360) % 360; s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return "#" + [f(0), f(8), f(4)].map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
}
function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [n >> 16 & 255, n >> 8 & 255, n & 255];
}
function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min, s = l > .5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s * 100, l * 100];
}

/** Тот же оттенок, но не светлее maxL (%): живой фон не «выцветает» от светлых обложек. */
function tone(hex, maxL, minS) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const [h, s, l] = rgbToHsl(rgb);
  return hsl(h, Math.max(minS || 0, s), Math.min(maxL, l));
}
/** Три цвета для фона и акцентов: из accent трека или из его «семени». */
function paletteFor(item) {
  const key = item && (item.id || item.key || item.name || item.title) || "memu";
  const rgb = hexToRgb(item && item.accent);
  if (rgb) {
    const [h, s] = rgbToHsl(rgb);
    const sat = Math.max(55, Math.min(90, s));
    return [hsl(h, sat, 58), hsl(h + 38, sat, 52), hsl(h - 42, sat * .9, 46)];
  }
  return coverScheme(seeded(hash(key))).colors.slice(0, 3);   // те же цвета, что у генеративной обложки
}

const coverCache = new Map();
/** Палитра для генеративной обложки: схема (аналоги/комплемент/триада/дуотон на светлом) — по «семени» трека. */
function coverScheme(rnd) {
  const h = Math.floor(rnd() * 360), kind = Math.floor(rnd() * 5);
  const sets = [
    [[h, 74, 62], [h + 32, 78, 56], [h - 34, 66, 46], [h + 12, 38, 9]],          // аналоги на тёмном
    [[h, 72, 60], [h + 180, 62, 56], [h + 30, 82, 72], [h + 200, 40, 10]],        // комплемент
    [[h, 68, 60], [h + 120, 52, 54], [h + 240, 64, 64], [h + 60, 30, 8]],         // триада, приглушённая
    [[h, 62, 50], [h + 18, 80, 64], [h - 20, 50, 38], [h + 40, 50, 92]],          // светлый фон
    [[h, 70, 60], [h + 300, 62, 56], [h + 40, 86, 72], [h + 260, 50, 12]],        // сумерки
  ][kind];
  return { colors: sets.map(([a, b, c]) => hsl(a, b, c)), light: kind === 3 };
}
/** Генеративная обложка SVG (data:) — детерминированно по треку: меш-градиенты, баухаус, солнце, волны,
 * орбиты, кольца и «типографика» по первой букве названия. label — подпись (плейлисты), title — для буквы. */
function coverArt(key, label, title) {
  const cacheKey = key + "|" + (label || "") + "|" + (title || "");
  if (coverCache.has(cacheKey)) return coverCache.get(cacheKey);
  const rnd = seeded(hash(key));
  const { colors: [c1, c2, c3, bg], light } = coverScheme(rnd);
  const style = Math.floor(rnd() * 8);
  const id = "g" + hash(key).toString(36);
  const ink = light ? "#16131c" : "#fff";
  const n = (v) => v.toFixed(1);
  let body = "", defs = "", grain = style === 0 || style === 6 || style === 7;
  if (style === 0) {          // меш: размытые пятна света
    for (let i = 0; i < 5; i++) {
      const cx = rnd() * 200, cy = rnd() * 200, r = 50 + rnd() * 80;
      body += `<circle cx="${n(cx)}" cy="${n(cy)}" r="${n(r)}" fill="${[c1, c2, c3, c1, "#fff"][i]}" opacity="${i === 4 ? .22 : .9}"/>`;
    }
    body = `<g filter="url(#b${id})">${body}</g>`;
  } else if (style === 1) {   // баухаус: дуги из угла
    const corner = Math.floor(rnd() * 4), ox = corner % 2 ? 200 : 0, oy = corner > 1 ? 200 : 0;
    for (let i = 6; i > 0; i--) body += `<circle cx="${ox}" cy="${oy}" r="${i * 34}" fill="${[c1, c2, c3][i % 3]}"/>`;
    body += `<circle cx="${n(40 + rnd() * 120)}" cy="${n(40 + rnd() * 120)}" r="${n(12 + rnd() * 16)}" fill="${bg}" opacity=".85"/>`;
  } else if (style === 2) {   // солнце над горизонтом, разрезанное полосами
    const cy = 92 + rnd() * 30;
    defs += `<linearGradient id="s${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c3}"/><stop offset="1" stop-color="${c1}"/></linearGradient>`;
    body += `<circle cx="100" cy="${n(cy)}" r="64" fill="url(#s${id})"/>`;
    for (let i = 0; i < 6; i++) body += `<rect x="20" y="${n(cy + 8 + i * 9)}" width="160" height="${n(1.5 + i * .9)}" fill="${bg}"/>`;
    body += `<rect y="${n(cy + 62)}" width="200" height="200" fill="${c2}" opacity=".9"/><rect y="${n(cy + 62)}" width="200" height="2" fill="#fff" opacity=".5"/>`;
  } else if (style === 3) {   // волны-ленты
    for (let i = 0; i < 6; i++) {
      const y = 40 + i * 30, a = 14 + rnd() * 26, ph = rnd() * 60;
      body += `<path d="M-20 ${n(y)} C ${n(40 + ph)} ${n(y - a)}, ${n(110 + ph)} ${n(y + a)}, 220 ${n(y - a / 3)} L220 220 L-20 220Z" fill="${[c1, c2, c3][i % 3]}" opacity="${n(.55 + i * .07)}"/>`;
    }
  } else if (style === 4) {   // сетка фигур 3×3
    const shapes = (x, y, k, c) => [
      `<circle cx="${x + 30}" cy="${y + 30}" r="26" fill="${c}"/>`,
      `<path d="M${x + 4} ${y + 56} A26 26 0 0 1 ${x + 56} ${y + 56}Z" fill="${c}"/>`,
      `<path d="M${x + 4} ${y + 4} L${x + 56} ${y + 4} A52 52 0 0 1 ${x + 4} ${y + 56}Z" fill="${c}"/>`,
      `<rect x="${x + 8}" y="${y + 8}" width="44" height="44" rx="${k % 2 ? 22 : 4}" fill="${c}"/>`,
      `<path d="M${x + 30} ${y + 4} L${x + 56} ${y + 56} L${x + 4} ${y + 56}Z" fill="${c}"/>`,
    ][k];
    for (let gx = 0; gx < 3; gx++) for (let gy = 0; gy < 3; gy++) body += shapes(10 + gx * 62, 10 + gy * 62, Math.floor(rnd() * 5), [c1, c2, c3][Math.floor(rnd() * 3)]);
  } else if (style === 5) {   // кольца со смещением, тонкие линии
    const cx = 60 + rnd() * 80, cy = 60 + rnd() * 80;
    defs += `<linearGradient id="r${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient>`;
    body += `<circle cx="${n(cx)}" cy="${n(cy)}" r="58" fill="url(#r${id})"/>`;
    for (let i = 1; i < 14; i++) body += `<circle cx="${n(cx + i * 2.2)}" cy="${n(cy - i * 1.4)}" r="${58 + i * 9}" fill="none" stroke="${i % 3 ? c3 : ink}" stroke-width="${i % 4 ? .8 : 1.6}" opacity="${n(.85 - i * .05)}"/>`;
  } else if (style === 6) {   // типографика: крупная буква названия поверх меша
    const letter = (String(title || label || key).trim().match(/[\p{L}\p{N}]/u) || ["♪"])[0].toUpperCase();
    body += `<g filter="url(#b${id})"><circle cx="40" cy="40" r="90" fill="${c1}"/><circle cx="180" cy="170" r="90" fill="${c2}"/></g>`;
    body += `<text x="${n(96 + rnd() * 20)}" y="188" text-anchor="middle" font-family="Segoe UI Variable Display,Segoe UI,system-ui,sans-serif" font-size="230" font-weight="900" fill="${ink}" opacity=".92" letter-spacing="-10">${escapeXml(letter)}</text>`;
  } else {                    // орбиты: светящееся ядро и эллипсы
    body += `<g filter="url(#b${id})"><circle cx="${n(80 + rnd() * 40)}" cy="${n(80 + rnd() * 40)}" r="60" fill="${c1}"/><circle cx="${n(60 + rnd() * 80)}" cy="${n(60 + rnd() * 80)}" r="42" fill="${c2}"/></g>`;
    const rot = Math.floor(rnd() * 180);
    for (let i = 0; i < 4; i++) body += `<ellipse cx="100" cy="100" rx="${70 + i * 10}" ry="${26 + i * 7}" fill="none" stroke="${ink}" stroke-width=".9" opacity="${n(.7 - i * .14)}" transform="rotate(${rot + i * 22} 100 100)"/>`;
    for (let i = 0; i < 3; i++) { const a = rnd() * Math.PI * 2; body += `<circle cx="${n(100 + Math.cos(a) * 78)}" cy="${n(100 + Math.sin(a) * 30)}" r="${n(2 + rnd() * 3)}" fill="${ink}" transform="rotate(${rot} 100 100)"/>`; }
  }
  if (grain) defs += `<filter id="n${id}"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" stitchTiles="stitch"/><feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .07 0"/></filter>`;
  const text = label ? `<text x="16" y="182" font-family="Segoe UI Variable Display,Segoe UI,system-ui,sans-serif" font-size="16" font-weight="800" letter-spacing="-.2" fill="${ink}" opacity=".95">${escapeXml(String(label).slice(0, 22))}</text>` : "";
  const shade = light ? "" : `<rect width="200" height="200" fill="url(#v${id})"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200"><defs>${defs}<filter id="b${id}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="22"/></filter>`
    + `<radialGradient id="v${id}" cx=".5" cy=".4" r=".75"><stop offset=".6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".35"/></radialGradient></defs>`
    + `<rect width="200" height="200" fill="${bg}"/>${body}${grain ? `<rect width="200" height="200" filter="url(#n${id})"/>` : ""}${shade}${text}</svg>`;
  const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  coverCache.set(cacheKey, url);
  return url;
}
function escapeXml(text) { return String(text).replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" })[c]); }

/** <img> обложки: реальная, а при ошибке загрузки — генеративная (см. обработчик error в main.js). */
function artImg(item, kind) {
  const key = (item && (item.album && kind === "album" ? item.artist + "|" + item.album : item.id || item.key || item.name)) || "x";
  const fallback = coverArt(key, "", item && (kind === "album" ? item.album : item.title || item.name));
  const src = (item && item.cover) || fallback;
  return `<img alt="" draggable="false" loading="lazy" decoding="async" src="${esc(src)}" data-fb="${esc(fallback)}">`;
}

/* Значки сервисов — упрощённые знаки, без фирменных логотипов. */
const SOURCES = {
  youtube: { name: "YouTube", color: "#ff2d2d", icon: '<rect x="2.5" y="5.5" width="19" height="13" rx="4" fill="currentColor" stroke="none"/><path d="M10 9v6l5-3z" fill="#ff2d2d" stroke="none"/>' },
  ytmusic: { name: "YouTube Music", color: "#ff0f3c", icon: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.8"/><path d="M10.6 10v4l3.2-2z" fill="currentColor" stroke="none"/>' },
  spotify: { name: "Spotify", color: "#1db954", icon: '<path d="M6 9.2c4-1.3 8.6-1 12 1M7 12.6c3.2-1 6.7-.7 9.5.9M8 15.8c2.5-.7 5-.5 7 .7"/>' },
  apple: { name: "Apple Music", color: "#fa2d55", icon: '<path d="M9.5 17.5V7l9-2v10"/><circle cx="7.5" cy="17.5" r="2" fill="currentColor"/><circle cx="16.5" cy="15" r="2" fill="currentColor"/>' },
  soundcloud: { name: "SoundCloud", color: "#ff6a00", icon: '<path d="M8 17h9.5a3.5 3.5 0 0 0 .2-7A5 5 0 0 0 8 9.4z" fill="currentColor" stroke="none"/><path d="M3 15v2M5 13v4M6.5 11.5V17"/>' },
  deezer: { name: "Deezer", color: "#a238ff", icon: '<path d="M4 17h3M8.5 17h3M13 17h3M17.5 17h3M13 14h3M17.5 14h3M17.5 11h3M17.5 8h3M8.5 14h3"/>' },
  yandex: { name: "Яндекс Музыка", name_en: "Yandex Music", color: "#ffcc00", ink: "#111", icon: '<circle cx="12" cy="12" r="8.5"/><path d="M13.5 7.5v9M13.5 12 9.5 7.5M13.5 12l-3.5 4.5"/>' },
  vk: { name: "VK Музыка", name_en: "VK Music", color: "#0077ff", icon: '<path d="M4 8c.3 5 2.8 8 7 8h.6v-3c1.6.2 2.8 1.4 3.3 3H18c-.6-2.3-2.2-3.6-3.2-4 1-.6 2.4-2 2.8-4h-2.6c-.5 1.6-1.9 3-3.4 3.2V8H9v5.6C7.8 13.2 6.6 11.6 6.5 8z" fill="currentColor" stroke="none"/>' },
  bandcamp: { name: "Bandcamp", color: "#1da0c3", icon: '<path d="M9 7h12l-6 10H3z" fill="currentColor" stroke="none"/>' },
  tidal: { name: "TIDAL", color: "#111318", icon: '<path d="M4 9l2.7-2.7L9.3 9 6.7 11.6zM9.3 9 12 6.3 14.7 9 12 11.6zM14.7 9l2.6-2.7L20 9l-2.7 2.6zM9.3 14.3 12 11.6l2.7 2.7L12 17z" fill="currentColor" stroke="none"/>' },
  twitch: { name: "Twitch", color: "#9146ff", icon: '<path d="M5 4h14v10l-4 4h-3l-3 2v-2H5z"/><path d="M11 8v4M15 8v4"/>' },
  vimeo: { name: "Vimeo", color: "#1ab7ea", icon: '<path d="M4 9c2-1.5 3-2 3.6.2.6 2 1 6 2.2 6s3.8-4 4.4-6.3C14.7 6.5 12.6 6.6 11.5 7.5 12.4 4.5 19 3.8 18 8.5S12 19 9.5 19 6.8 11.5 6 10.7 4.6 10 4 9z" fill="currentColor" stroke="none"/>' },
  mixcloud: { name: "Mixcloud", color: "#5000ff", icon: '<path d="M6 16a3 3 0 0 1 .4-6 4.5 4.5 0 0 1 8.6 1.5A2.4 2.4 0 1 1 15.5 16z"/>' },
  local: { name: "Компьютер", name_en: "This computer", color: "#6b6b7b", icon: '<rect x="3.5" y="5" width="17" height="11" rx="2"/><path d="M8.5 19.5h7M12 16v3.5"/>' },
  web: { name: "Ссылка", name_en: "Link", color: "#64748b", icon: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>' },
};
function sourceInfo(id) { return SOURCES[id] || SOURCES.web; }
function sourceName(id) { const s = sourceInfo(id); return (Memu.lang === "en" && s.name_en) || s.name; }
function sourceIcon(id, cls) {
  const s = sourceInfo(id);
  return `<span class="${cls || "mm-srcdot"}" style="background:${s.color};color:${s.ink || "#fff"}" title="${esc(sourceName(id))}"><svg viewBox="0 0 24 24">${s.icon}</svg></span>`;
}

const DETECT_RULES = [
  [/music\.youtube\.com/i, "ytmusic"], [/(youtube\.com|youtu\.be)/i, "youtube"], [/spotify\.(com|link)|spoti\.fi/i, "spotify"],
  [/music\.apple\.com|itunes\.apple\.com/i, "apple"], [/soundcloud\.com|snd\.sc/i, "soundcloud"], [/deezer\.(com|page\.link)/i, "deezer"],
  [/music\.yandex\.|yandex\.[a-z]+\/music/i, "yandex"], [/(vk\.com|vk\.ru|vkvideo\.ru).*(audio|music|playlist)|boom\.ru/i, "vk"],
  [/bandcamp\.com/i, "bandcamp"], [/tidal\.com/i, "tidal"], [/twitch\.tv/i, "twitch"], [/vimeo\.com/i, "vimeo"], [/mixcloud\.com/i, "mixcloud"],
];
/** Мгновенный разбор ссылки: {source, kind} или null, если это не ссылка. */
function detectLocal(text) {
  const url = String(text || "").trim();
  if (!/^(https?:\/\/)?([\w-]+\.)+[a-z]{2,}(\/|$|\?)/i.test(url)) return null;
  let source = "web";
  for (const [re, id] of DETECT_RULES) if (re.test(url)) { source = id; break; }
  let kind = "track";
  if (/[?&]list=|\/playlist|\/sets\/|\/playlists?\/|\/users\/.+\/playlists|album\/.+\?i=/i.test(url)) kind = "playlist";
  if (/\/album\//i.test(url) && !/[?&]i=\d/.test(url)) kind = "album";
  if (/\/artist\/|\/channel\/|\/@[\w.-]+\/?$|\/browse\/UC/i.test(url)) kind = "artist";
  if (/[?&]v=|youtu\.be\/|\/track\/|\/watch|\/song\//i.test(url) && !/[?&]list=/i.test(url)) kind = "track";
  return { source, kind, url };
}
function firstUrl(text) {
  const m = /https?:\/\/[^\s<>"']+/i.exec(String(text || ""));
  return m ? m[0] : "";
}
