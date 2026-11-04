

export const clamp = (v, a = 0, b = 255) => (v < a ? a : v > b ? b : v);
const lumOf = (r, g, b) => 0.299 * r + 0.587 * g + 0.114 * b;

export function cloneImage(img) { return { width: img.width, height: img.height, data: new Uint8ClampedArray(img.data) }; }
export function makeImage(w, h, rgba = [0, 0, 0, 0]) {
  const data = new Uint8ClampedArray(w * h * 4);
  if (rgba.some((v) => v)) for (let i = 0; i < data.length; i += 4) { data[i] = rgba[0]; data[i + 1] = rgba[1]; data[i + 2] = rgba[2]; data[i + 3] = rgba[3]; }
  return { width: w, height: h, data };
}

export function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  let h = 0, s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0); else if (max === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h *= 60;
  }
  return [h, s * 100, l * 100];
}
function hue2rgb(p, q, t) { if (t < 0) t += 1; if (t > 1) t -= 1; if (t < 1 / 6) return p + (q - p) * 6 * t; if (t < 1 / 2) return q; if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6; return p; }
export function hslToRgb(h, s, l) {
  h = (((h % 360) + 360) % 360) / 360; s = clamp(s, 0, 100) / 100; l = clamp(l, 0, 100) / 100;
  if (s === 0) { const v = Math.round(l * 255); return [v, v, v]; }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
  return [Math.round(hue2rgb(p, q, h + 1 / 3) * 255), Math.round(hue2rgb(p, q, h) * 255), Math.round(hue2rgb(p, q, h - 1 / 3) * 255)];
}
export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) { if (max === r) h = ((g - b) / d) % 6; else if (max === g) h = (b - r) / d + 2; else h = (r - g) / d + 4; h *= 60; if (h < 0) h += 360; }
  return [h, max ? (d / max) * 100 : 0, max * 100];
}
export function hsvToRgb(h, s, v) {
  h = ((h % 360) + 360) % 360; s /= 100; v /= 100;
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
}
export function parseHex(hex) {
  let s = String(hex || "").trim().replace(/^#/, "");
  if (/^[0-9a-f]{3,4}$/i.test(s)) s = s.split("").map((c) => c + c).join("");
  if (!/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(s)) return null;
  const n = (i) => parseInt(s.slice(i, i + 2), 16);
  return [n(0), n(2), n(4), s.length === 8 ? n(6) : 255];
}
export const toHex = (r, g, b) => "#" + [r, g, b].map((v) => clamp(Math.round(v)).toString(16).padStart(2, "0")).join("");

function applyMask(src, out, mask) {
  if (!mask) return out;
  const s = src.data, o = out.data;
  for (let p = 0, i = 0; p < mask.length; p++, i += 4) {
    const m = mask[p];
    if (m === 255) continue;
    if (m === 0) { o[i] = s[i]; o[i + 1] = s[i + 1]; o[i + 2] = s[i + 2]; o[i + 3] = s[i + 3]; continue; }
    const k = m / 255, j = 1 - k;
    o[i] = s[i] * j + o[i] * k; o[i + 1] = s[i + 1] * j + o[i + 1] * k; o[i + 2] = s[i + 2] * j + o[i + 2] * k; o[i + 3] = s[i + 3] * j + o[i + 3] * k;
  }
  return out;
}
function lutOp(img, lr, lg = lr, lb = lr, mask) {
  const out = cloneImage(img), d = out.data;
  for (let i = 0; i < d.length; i += 4) { d[i] = lr[d[i]]; d[i + 1] = lg[d[i + 1]]; d[i + 2] = lb[d[i + 2]]; }
  return applyMask(img, out, mask);
}
function makeLut(fn) { const l = new Uint8ClampedArray(256); for (let v = 0; v < 256; v++) l[v] = Math.round(fn(v)); return l; }
function perPixel(img, fn, mask) {
  const out = cloneImage(img), d = out.data, px = [0, 0, 0];
  for (let i = 0; i < d.length; i += 4) {
    if (d[i + 3] === 0) continue;
    px[0] = d[i]; px[1] = d[i + 1]; px[2] = d[i + 2];
    fn(px); d[i] = px[0]; d[i + 1] = px[1]; d[i + 2] = px[2];
  }
  return applyMask(img, out, mask);
}

export const ADJUST = {
  brightnessContrast(img, { brightness = 0, contrast = 0 }, mask) {
    const b = brightness * 1.5, c = contrast >= 0 ? 1 + contrast / 50 * (contrast / 100 + 1) : 1 + contrast / 100;
    return lutOp(img, makeLut((v) => (v - 127.5) * c + 127.5 + b), undefined, undefined, mask);
  },
  levels(img, { inBlack = 0, inWhite = 255, gamma = 1, outBlack = 0, outWhite = 255, channel = "rgb" }, mask) {
    const span = Math.max(1, inWhite - inBlack);
    const lut = makeLut((v) => { const n = clamp((v - inBlack) / span, 0, 1); return outBlack + Math.pow(n, 1 / Math.max(0.05, gamma)) * (outWhite - outBlack); });
    const id = makeLut((v) => v);
    return lutOp(img, channel === "rgb" || channel === "r" ? lut : id, channel === "rgb" || channel === "g" ? lut : id, channel === "rgb" || channel === "b" ? lut : id, mask);
  },
  curves(img, { points = [[0, 0], [255, 255]], channel = "rgb" }, mask) {
    const lut = curveLut(points), id = makeLut((v) => v);
    return lutOp(img, channel === "rgb" || channel === "r" ? lut : id, channel === "rgb" || channel === "g" ? lut : id, channel === "rgb" || channel === "b" ? lut : id, mask);
  },
  exposure(img, { exposure = 0, offset = 0, gamma = 1 }, mask) {
    const k = Math.pow(2, exposure);
    return lutOp(img, makeLut((v) => Math.pow(clamp((v / 255) * k + offset, 0, 1), 1 / Math.max(0.05, gamma)) * 255), undefined, undefined, mask);
  },
  hueSaturation(img, { hue = 0, saturation = 0, lightness = 0, colorize = false }, mask) {
    return perPixel(img, (px) => {
      let [h, s, l] = rgbToHsl(px[0], px[1], px[2]);
      if (colorize) { h = hue < 0 ? hue + 360 : hue; s = clamp(25 + saturation / 2, 0, 100); }
      else { h += hue; s = saturation >= 0 ? s + (100 - s) * (saturation / 100) * (s > 0 ? 1 : 0) : s * (1 + saturation / 100); }
      l = lightness >= 0 ? l + (100 - l) * (lightness / 100) : l * (1 + lightness / 100);
      const rgb = hslToRgb(h, s, l); px[0] = rgb[0]; px[1] = rgb[1]; px[2] = rgb[2];
    }, mask);
  },
  vibrance(img, { vibrance = 0, saturation = 0 }, mask) {
    const vb = vibrance / 100, st = saturation / 100;
    return perPixel(img, (px) => {
      const [r, g, b] = px, max = Math.max(r, g, b), avg = (r + g + b) / 3;
      const amt = (((Math.abs(max - avg) * 2) / 255) * -vb) * (vb > 0 ? 1 : 0) + vb;
      const k = 1 + st + amt * (vb > 0 ? 1 : 1);
      const l = lumOf(r, g, b);
      px[0] = clamp(l + (r - l) * k); px[1] = clamp(l + (g - l) * k); px[2] = clamp(l + (b - l) * k);
    }, mask);
  },
  colorBalance(img, { cyanRed = 0, magentaGreen = 0, yellowBlue = 0, preserve = true }, mask) {
    return perPixel(img, (px) => {
      const l0 = lumOf(px[0], px[1], px[2]);
      const mid = 1 - Math.abs(l0 / 127.5 - 1) * 0.6;
      let r = px[0] + cyanRed * mid, g = px[1] + magentaGreen * mid, b = px[2] + yellowBlue * mid;
      if (preserve) { const d = l0 - lumOf(r, g, b); r += d; g += d; b += d; }
      px[0] = clamp(r); px[1] = clamp(g); px[2] = clamp(b);
    }, mask);
  },
  temperature(img, { temperature = 0, tint = 0 }, mask) {
    const t = temperature / 100, n = tint / 100;
    return perPixel(img, (px) => {
      px[0] = clamp(px[0] + 40 * t); px[2] = clamp(px[2] - 40 * t); px[1] = clamp(px[1] + 6 * t - 30 * n);
      px[0] = clamp(px[0] + 15 * n); px[2] = clamp(px[2] + 15 * n);
    }, mask);
  },
  blackWhite(img, { red = 40, yellow = 60, green = 40, cyan = 60, blue = 20, magenta = 80, tint = "" } = {}, mask) {
    const w = { red, yellow, green, cyan, blue, magenta };
    const t = tint ? parseHex(tint) : null;
    return perPixel(img, (px) => {
      const [r, g, b] = px, mn = Math.min(r, g, b), sum = r + g + b;

      let gray = mn;
      const rr = r - mn, gg = g - mn, bb = b - mn;
      if (rr >= gg && gg >= bb) gray += (rr - gg) * w.red / 100 + gg * w.yellow / 100;
      else if (rr >= bb && bb >= gg) gray += (rr - bb) * w.red / 100 + bb * w.magenta / 100;
      else if (gg >= rr && rr >= bb) gray += (gg - rr) * w.green / 100 + rr * w.yellow / 100;
      else if (gg >= bb && bb >= rr) gray += (gg - bb) * w.green / 100 + bb * w.cyan / 100;
      else if (bb >= rr && rr >= gg) gray += (bb - rr) * w.blue / 100 + rr * w.magenta / 100;
      else gray += (bb - gg) * w.blue / 100 + gg * w.cyan / 100;
      if (sum === 0) gray = 0;
      gray = clamp(gray);
      if (t) { px[0] = clamp(gray * t[0] / 255 * 1.0 + gray * 0.25); px[1] = clamp(gray * t[1] / 255 + gray * 0.25); px[2] = clamp(gray * t[2] / 255 + gray * 0.25); }
      else { px[0] = px[1] = px[2] = gray; }
    }, mask);
  },
  grayscale(img, _p, mask) { return perPixel(img, (px) => { const g = clamp(Math.round(lumOf(px[0], px[1], px[2]))); px[0] = px[1] = px[2] = g; }, mask); },
  sepia(img, { amount = 100 } = {}, mask) {
    const k = amount / 100;
    return perPixel(img, (px) => {
      const [r, g, b] = px;
      const sr = 0.393 * r + 0.769 * g + 0.189 * b, sg = 0.349 * r + 0.686 * g + 0.168 * b, sb = 0.272 * r + 0.534 * g + 0.131 * b;
      px[0] = clamp(r + (sr - r) * k); px[1] = clamp(g + (sg - g) * k); px[2] = clamp(b + (sb - b) * k);
    }, mask);
  },
  invert(img, _p, mask) { return lutOp(img, makeLut((v) => 255 - v), undefined, undefined, mask); },
  threshold(img, { level = 128 }, mask) { return perPixel(img, (px) => { const v = lumOf(px[0], px[1], px[2]) >= level ? 255 : 0; px[0] = px[1] = px[2] = v; }, mask); },
  posterize(img, { levels = 4 }, mask) {
    const n = Math.max(2, Math.min(255, Math.round(levels)));
    return lutOp(img, makeLut((v) => Math.round(Math.round((v / 255) * (n - 1)) * (255 / (n - 1)))), undefined, undefined, mask);
  },
  autoLevels(img, { clip = 0.5 } = {}, mask) {
    const h = histogram(img), total = Math.max(1, h.count), cut = (total * clip) / 100, luts = [];
    for (const ch of [h.r, h.g, h.b]) {
      let lo = 0, hi = 255, acc = 0;
      while (lo < 255 && (acc += ch[lo]) <= cut) lo++;
      acc = 0; while (hi > 0 && (acc += ch[hi]) <= cut) hi--;
      if (hi <= lo) { luts.push(makeLut((v) => v)); continue; }
      luts.push(makeLut((v) => ((v - lo) / (hi - lo)) * 255));
    }
    return lutOp(img, luts[0], luts[1], luts[2], mask);
  },
  replaceColor(img, { from = "#ff0000", to = "#00ff00", fuzz = 40 }, mask) {
    const a = parseHex(from) || [255, 0, 0], b = parseHex(to) || [0, 255, 0];
    const [bh, bs] = rgbToHsl(b[0], b[1], b[2]);
    return perPixel(img, (px) => {
      const d = Math.hypot(px[0] - a[0], px[1] - a[1], px[2] - a[2]);
      if (d > fuzz * 2.2) return;
      const k = 1 - d / (fuzz * 2.2);
      const [, , l] = rgbToHsl(px[0], px[1], px[2]);
      const c = hslToRgb(bh, bs, l);
      px[0] = clamp(px[0] + (c[0] - px[0]) * k); px[1] = clamp(px[1] + (c[1] - px[1]) * k); px[2] = clamp(px[2] + (c[2] - px[2]) * k);
    }, mask);
  },
  shadowsHighlights(img, { shadows = 35, highlights = 0, radius = 30 } = {}, mask) {
    const base = lumBlur(img, Math.max(1, radius)), out = cloneImage(img), d = out.data;
    const s = clamp(shadows, -100, 100) / 100, hl = clamp(highlights, -100, 100) / 100;
    for (let p = 0, i = 0; p < base.length; p++, i += 4) {
      if (!d[i + 3]) continue;
      const L = lumOf(d[i], d[i + 1], d[i + 2]) / 255, B = base[p], ws = (1 - B) * (1 - B), wh = B * B;
      const nl = clamp(L + s * ws * (1 - L) * 0.9 - hl * wh * L * 0.55, 0, 1);
      toneTo(d, i, L, nl);
    }
    return applyMask(img, out, mask);
  },
  clarity(img, { amount = 30, radius = 20 } = {}, mask) {
    const base = lumBlur(img, Math.max(1, radius)), out = cloneImage(img), d = out.data, a = clamp(amount, -100, 100) / 100;
    for (let p = 0, i = 0; p < base.length; p++, i += 4) {
      if (!d[i + 3]) continue;
      const L = lumOf(d[i], d[i + 1], d[i + 2]) / 255, mid = 1 - (2 * L - 1) * (2 * L - 1);
      const dl = (L - base[p]) * a * 1.2 * (0.35 + 0.65 * mid) * 255;
      d[i] = clamp(d[i] + dl); d[i + 1] = clamp(d[i + 1] + dl); d[i + 2] = clamp(d[i + 2] + dl);
    }
    return applyMask(img, out, mask);
  },
  dehaze(img, { amount = 30 } = {}, mask) {
    const { width: w, height: h, data: s } = img, n = w * h, k = clamp(amount, -100, 100) / 100, out = cloneImage(img), d = out.data;
    if (!k || !n) return applyMask(img, out, mask);
    const dc = new Uint8ClampedArray(n * 4);
    for (let p = 0, i = 0; p < n; p++, i += 4) { const m = Math.min(s[i], s[i + 1], s[i + 2]); dc[i] = dc[i + 1] = dc[i + 2] = m; dc[i + 3] = 255; }
    const db = gaussianBlurData(dc, w, h, Math.max(2, Math.round(Math.max(w, h) / 80)));
    const hist = new Uint32Array(256); for (let i = 0; i < db.length; i += 4) hist[db[i]]++;
    let cut = 255, acc = 0; const top = Math.max(1, Math.round(n * 0.002)); while (cut > 0 && (acc += hist[cut]) < top) cut--;
    const A = [0, 0, 0]; let cnt = 0;
    for (let i = 0; i < db.length; i += 4) if (db[i] >= cut && s[i + 3]) { A[0] += s[i]; A[1] += s[i + 1]; A[2] += s[i + 2]; cnt++; }
    if (cnt) { A[0] /= cnt; A[1] /= cnt; A[2] /= cnt; } else A[0] = A[1] = A[2] = 255;
    const Am = Math.max(1, (A[0] + A[1] + A[2]) / 3);
    for (let i = 0; i < d.length; i += 4) {
      if (!d[i + 3]) continue;
      if (k > 0) { const tr = Math.max(0.12, 1 - k * 0.92 * Math.min(1, db[i] / Am)); for (let c = 0; c < 3; c++) d[i + c] = clamp((s[i + c] - A[c]) / tr + A[c]); }
      else { const tr = 1 + k * 0.6; for (let c = 0; c < 3; c++) d[i + c] = clamp(s[i + c] * tr + A[c] * (1 - tr)); }
    }
    return applyMask(img, out, mask);
  },
  channelMixer(img, { rr = 100, rg = 0, rb = 0, gr = 0, gg = 100, gb = 0, br = 0, bg = 0, bb = 100, mono = false } = {}, mask) {
    return perPixel(img, (px) => {
      const [r, g, b] = px;
      if (mono) { px[0] = px[1] = px[2] = clamp((r * rr + g * rg + b * rb) / 100); return; }
      px[0] = clamp((r * rr + g * rg + b * rb) / 100); px[1] = clamp((r * gr + g * gg + b * gb) / 100); px[2] = clamp((r * br + g * bg + b * bb) / 100);
    }, mask);
  },
  gradientMap(img, { from = "#1e1b4b", to = "#fbbf77", amount = 100, reverse = false } = {}, mask) {
    let a = parseHex(from) || [0, 0, 0], b = parseHex(to) || [255, 255, 255]; if (reverse) [a, b] = [b, a];
    const k = clamp(amount, 0, 100) / 100;
    return perPixel(img, (px) => {
      const t = lumOf(px[0], px[1], px[2]) / 255;
      for (let c = 0; c < 3; c++) px[c] = clamp(px[c] + (a[c] + (b[c] - a[c]) * t - px[c]) * k);
    }, mask);
  },
  photoFilter(img, { color = "#ec8a00", density = 25, preserve = true } = {}, mask) {
    const col = parseHex(color) || [236, 138, 0], k = clamp(density, 0, 100) / 100;
    return perPixel(img, (px) => {
      const L0 = lumOf(px[0], px[1], px[2]);
      let r = px[0] + (px[0] * col[0] / 255 * 1.35 - px[0]) * k + (col[0] - px[0]) * k * 0.25, g = px[1] + (px[1] * col[1] / 255 * 1.35 - px[1]) * k + (col[1] - px[1]) * k * 0.25, b = px[2] + (px[2] * col[2] / 255 * 1.35 - px[2]) * k + (col[2] - px[2]) * k * 0.25;
      if (preserve) { const dl = L0 - lumOf(r, g, b); r += dl; g += dl; b += dl; }
      px[0] = clamp(r); px[1] = clamp(g); px[2] = clamp(b);
    }, mask);
  },
  splitToning(img, { shadowHue = 210, shadowSat = 35, highlightHue = 40, highlightSat = 30, balance = 0 } = {}, mask) {
    const sc = hslToRgb(shadowHue, 100, 50), hc = hslToRgb(highlightHue, 100, 50), ss = clamp(shadowSat, 0, 100) / 100, hs = clamp(highlightSat, 0, 100) / 100;
    const pivot = clamp(0.5 + clamp(balance, -100, 100) / 100 * 0.35, 0.1, 0.9);
    return perPixel(img, (px) => {
      const L0 = lumOf(px[0], px[1], px[2]), L = L0 / 255;
      let ws = L < pivot ? 1 - L / pivot : 0, wh = L > pivot ? (L - pivot) / (1 - pivot) : 0; ws = ws * ws * (3 - 2 * ws); wh = wh * wh * (3 - 2 * wh);
      const v = [0, 0, 0]; for (let c = 0; c < 3; c++) v[c] = px[c] + (sc[c] - 128) * ws * ss * 0.55 + (hc[c] - 128) * wh * hs * 0.55;
      const dl = L0 - lumOf(v[0], v[1], v[2]);
      px[0] = clamp(v[0] + dl); px[1] = clamp(v[1] + dl); px[2] = clamp(v[2] + dl);
    }, mask);
  },
  look(img, { name = "vivid", amount = 100 } = {}, mask) {
    const steps = LOOKS[name]; if (!steps) return applyMask(img, cloneImage(img), mask);
    let cur = img;
    for (const [op, p] of steps) cur = (ADJUST[op] || FILTER[op])(cur, { ...p }, null);
    const k = clamp(amount, 0, 100) / 100;
    if (k < 1) { const s = img.data, o = cur === img ? (cur = cloneImage(img)).data : cur.data; for (let i = 0; i < o.length; i += 4) { o[i] = s[i] + (o[i] - s[i]) * k; o[i + 1] = s[i + 1] + (o[i + 1] - s[i + 1]) * k; o[i + 2] = s[i + 2] + (o[i + 2] - s[i + 2]) * k; o[i + 3] = s[i + 3] + (o[i + 3] - s[i + 3]) * k; } }
    if (cur === img) cur = cloneImage(img);
    return applyMask(img, cur, mask);
  },
};

function lumBlur(img, radius) {
  const { width: w, height: h, data: s } = img, n = w * h, buf = new Uint8ClampedArray(n * 4);
  for (let p = 0, i = 0; p < n; p++, i += 4) { const v = lumOf(s[i], s[i + 1], s[i + 2]); buf[i] = buf[i + 1] = buf[i + 2] = v; buf[i + 3] = 255; }
  const b = gaussianBlurData(buf, w, h, radius), out = new Float32Array(n);
  for (let p = 0, i = 0; p < n; p++, i += 4) out[p] = b[i] / 255;
  return out;
}
function toneTo(d, i, L, nl) {
  if (nl > L) { const t = (nl - L) / Math.max(1e-6, 1 - L); d[i] += (255 - d[i]) * t; d[i + 1] += (255 - d[i + 1]) * t; d[i + 2] += (255 - d[i + 2]) * t; }
  else if (nl < L) { const k = L > 1e-6 ? nl / L : 0; d[i] *= k; d[i + 1] *= k; d[i + 2] *= k; }
}

export const LOOKS = {
  vivid: [["vibrance", { vibrance: 45, saturation: 5 }], ["brightnessContrast", { brightness: 4, contrast: 14 }]],
  warm: [["temperature", { temperature: 28, tint: 4 }], ["vibrance", { vibrance: 12 }]],
  cool: [["temperature", { temperature: -26, tint: -2 }], ["brightnessContrast", { contrast: 6 }]],
  golden: [["temperature", { temperature: 42, tint: 8 }], ["shadowsHighlights", { shadows: 20, highlights: 10, radius: 30 }], ["vibrance", { vibrance: 18 }]],
  matte: [["curves", { points: [[0, 34], [70, 72], [190, 196], [255, 236]] }], ["vibrance", { saturation: -12 }]],
  fade: [["curves", { points: [[0, 44], [128, 132], [255, 222]] }], ["vibrance", { saturation: -28 }]],
  vintage: [["sepia", { amount: 38 }], ["curves", { points: [[0, 28], [128, 126], [255, 228]] }], ["vignette", { amount: 38, size: 55 }]],
  film: [["curves", { points: [[0, 12], [64, 56], [192, 204], [255, 244]] }], ["temperature", { temperature: 8 }], ["noise", { amount: 6, mono: true }]],
  cinematic: [["splitToning", { shadowHue: 195, shadowSat: 45, highlightHue: 32, highlightSat: 40, balance: 0 }], ["brightnessContrast", { contrast: 12 }], ["vibrance", { saturation: -8 }]],
  dramatic: [["clarity", { amount: 45, radius: 20 }], ["brightnessContrast", { contrast: 18 }], ["vibrance", { saturation: -15 }], ["vignette", { amount: 30, size: 60 }]],
  noir: [["blackWhite", {}], ["brightnessContrast", { contrast: 32 }], ["vignette", { amount: 35, size: 58 }]],
  mono: [["grayscale", {}], ["curves", { points: [[0, 6], [64, 54], [192, 206], [255, 250]] }]],
  lomo: [["vibrance", { vibrance: 35, saturation: 8 }], ["brightnessContrast", { contrast: 24 }], ["vignette", { amount: 62, size: 48 }]],
  pastel: [["hueSaturation", { saturation: -32, lightness: 10 }], ["curves", { points: [[0, 30], [255, 250]] }]],
  duotone: [["gradientMap", { from: "#1e1b4b", to: "#fbbf77", amount: 100 }]],
};
export const LOOK_NAMES = Object.keys(LOOKS);

export function curveLut(points) {
  const pts = points.map(([x, y]) => [clamp(x), clamp(y)]).sort((a, b) => a[0] - b[0]).filter((p, i, a) => i === 0 || p[0] !== a[i - 1][0]);
  if (pts.length === 1) return makeLut(() => pts[0][1]);
  const n = pts.length, dx = [], dy = [], m = [], c1 = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; dy[i] = pts[i + 1][1] - pts[i][1]; m[i] = dy[i] / dx[i]; }
  c1[0] = m[0]; c1[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (m[i - 1] * m[i] <= 0) c1[i] = 0;
    else { const common = dx[i - 1] + dx[i]; c1[i] = (3 * common) / ((common + dx[i]) / m[i - 1] + (common + dx[i - 1]) / m[i]); }
  }
  return makeLut((x) => {
    if (x <= pts[0][0]) return pts[0][1];
    if (x >= pts[n - 1][0]) return pts[n - 1][1];
    let i = 0; while (i < n - 2 && x > pts[i + 1][0]) i++;
    const h = dx[i], t = (x - pts[i][0]) / h, t2 = t * t, t3 = t2 * t;
    return (2 * t3 - 3 * t2 + 1) * pts[i][1] + (t3 - 2 * t2 + t) * h * c1[i] + (-2 * t3 + 3 * t2) * pts[i + 1][1] + (t3 - t2) * h * c1[i + 1];
  });
}

export function histogram(img, mask) {
  const r = new Uint32Array(256), g = new Uint32Array(256), b = new Uint32Array(256), l = new Uint32Array(256), d = img.data;
  let count = 0;
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    if (d[i + 3] === 0 || (mask && mask[p] < 128)) continue;
    r[d[i]]++; g[d[i + 1]]++; b[d[i + 2]]++; l[Math.round(lumOf(d[i], d[i + 1], d[i + 2]))]++; count++;
  }
  return { r, g, b, l, count };
}

function premultiply(d) { for (let i = 0; i < d.length; i += 4) { const a = d[i + 3] / 255; d[i] *= a; d[i + 1] *= a; d[i + 2] *= a; } }
function unpremultiply(d) { for (let i = 0; i < d.length; i += 4) { const a = d[i + 3]; if (a && a < 255) { const k = 255 / a; d[i] = Math.min(255, d[i] * k); d[i + 1] = Math.min(255, d[i + 1] * k); d[i + 2] = Math.min(255, d[i + 2] * k); } } }
function boxBlurH(src, dst, w, h, r) {
  const iarr = 1 / (r + r + 1);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let c = 0; c < 4; c++) {
      let val = 0;
      for (let k = -r; k <= r; k++) val += src[(row + Math.min(w - 1, Math.max(0, k))) * 4 + c];
      for (let x = 0; x < w; x++) {
        dst[(row + x) * 4 + c] = val * iarr;
        const add = Math.min(w - 1, x + r + 1), sub = Math.max(0, x - r);
        val += src[(row + add) * 4 + c] - src[(row + sub) * 4 + c];
      }
    }
  }
}
function boxBlurV(src, dst, w, h, r) {
  const iarr = 1 / (r + r + 1);
  for (let x = 0; x < w; x++) {
    for (let c = 0; c < 4; c++) {
      let val = 0;
      for (let k = -r; k <= r; k++) val += src[(Math.min(h - 1, Math.max(0, k)) * w + x) * 4 + c];
      for (let y = 0; y < h; y++) {
        dst[(y * w + x) * 4 + c] = val * iarr;
        const add = Math.min(h - 1, y + r + 1), sub = Math.max(0, y - r);
        val += src[(add * w + x) * 4 + c] - src[(sub * w + x) * 4 + c];
      }
    }
  }
}

export function gaussianBlurData(d, w, h, radius) {
  if (radius < 0.5) return d;
  const sigma = radius / 2 + 0.01, n = 3;
  const wIdeal = Math.sqrt((12 * sigma * sigma) / n + 1);
  let wl = Math.floor(wIdeal); if (wl % 2 === 0) wl--;
  const wu = wl + 2, mIdeal = (12 * sigma * sigma - n * wl * wl - 4 * n * wl - 3 * n) / (-4 * wl - 4), m = Math.round(mIdeal);
  const a = new Float32Array(d.length); a.set(d);
  const b = new Float32Array(d.length);
  premultiply(a);
  for (let i = 0; i < n; i++) { const r = ((i < m ? wl : wu) - 1) / 2; boxBlurH(a, b, w, h, r); boxBlurV(b, a, w, h, r); }
  unpremultiply(a);
  const out = new Uint8ClampedArray(d.length);
  for (let i = 0; i < a.length; i++) out[i] = a[i];
  return out;
}
function convolve(img, kernel, { divisor, offset = 0, alpha = false } = {}) {
  const { width: w, height: h, data: s } = img, side = Math.round(Math.sqrt(kernel.length)), half = side >> 1;
  const out = cloneImage(img), o = out.data, div = divisor || kernel.reduce((a, b) => a + b, 0) || 1;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let r = 0, g = 0, b = 0;
    for (let ky = 0; ky < side; ky++) {
      const sy = Math.min(h - 1, Math.max(0, y + ky - half));
      for (let kx = 0; kx < side; kx++) {
        const sx = Math.min(w - 1, Math.max(0, x + kx - half)), wt = kernel[ky * side + kx];
        if (!wt) continue;
        const i = (sy * w + sx) * 4; r += s[i] * wt; g += s[i + 1] * wt; b += s[i + 2] * wt;
      }
    }
    const i = (y * w + x) * 4; o[i] = r / div + offset; o[i + 1] = g / div + offset; o[i + 2] = b / div + offset;
    if (alpha) o[i + 3] = 255;
  }
  return out;
}

export const FILTER = {
  gaussianBlur(img, { radius = 4 }, mask) { const out = { width: img.width, height: img.height, data: gaussianBlurData(img.data, img.width, img.height, radius) }; return applyMask(img, out, mask); },
  boxBlur(img, { radius = 3 }, mask) {
    const r = Math.max(1, Math.round(radius)), a = new Float32Array(img.data), b = new Float32Array(img.data.length);
    premultiply(a); boxBlurH(a, b, img.width, img.height, r); boxBlurV(b, a, img.width, img.height, r); unpremultiply(a);
    return applyMask(img, { width: img.width, height: img.height, data: new Uint8ClampedArray(a) }, mask);
  },
  motionBlur(img, { angle = 0, distance = 12 }, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data;
    const n = Math.max(1, Math.round(distance)), dx = Math.cos((angle * Math.PI) / 180), dy = Math.sin((angle * Math.PI) / 180);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let r = 0, g = 0, b = 0, a = 0, c = 0;
      for (let k = -n; k <= n; k++) {
        const sx = Math.round(x + dx * k), sy = Math.round(y + dy * k);
        if (sx < 0 || sy < 0 || sx >= w || sy >= h) continue;
        const i = (sy * w + sx) * 4, al = s[i + 3]; r += s[i] * al; g += s[i + 1] * al; b += s[i + 2] * al; a += al; c++;
      }
      const i = (y * w + x) * 4;
      if (a) { o[i] = r / a; o[i + 1] = g / a; o[i + 2] = b / a; o[i + 3] = a / c; }
    }
    return applyMask(img, out, mask);
  },
  sharpen(img, { amount = 50 } = {}, mask) {
    const k = amount / 100;
    return applyMask(img, convolve(img, [0, -k, 0, -k, 1 + 4 * k, -k, 0, -k, 0], { divisor: 1 }), mask);
  },
  unsharpMask(img, { amount = 80, radius = 2, threshold = 0 }, mask) {
    const blurred = gaussianBlurData(img.data, img.width, img.height, radius), out = cloneImage(img), o = out.data, s = img.data, k = amount / 100;
    for (let i = 0; i < s.length; i += 4) for (let c = 0; c < 3; c++) {
      const diff = s[i + c] - blurred[i + c];
      if (Math.abs(diff) >= threshold) o[i + c] = s[i + c] + diff * k;
    }
    return applyMask(img, out, mask);
  },
  emboss(img, { strength = 1 } = {}, mask) {
    const s = strength;
    return applyMask(img, convolve(img, [-2 * s, -s, 0, -s, 1, s, 0, s, 2 * s], { divisor: 1 }), mask);
  },
  edges(img, _p, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data;
    const L = (x, y) => { x = Math.min(w - 1, Math.max(0, x)); y = Math.min(h - 1, Math.max(0, y)); const i = (y * w + x) * 4; return lumOf(s[i], s[i + 1], s[i + 2]); };
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const gx = -L(x - 1, y - 1) - 2 * L(x - 1, y) - L(x - 1, y + 1) + L(x + 1, y - 1) + 2 * L(x + 1, y) + L(x + 1, y + 1);
      const gy = -L(x - 1, y - 1) - 2 * L(x, y - 1) - L(x + 1, y - 1) + L(x - 1, y + 1) + 2 * L(x, y + 1) + L(x + 1, y + 1);
      const v = clamp(Math.hypot(gx, gy)); const i = (y * w + x) * 4; o[i] = o[i + 1] = o[i + 2] = v;
    }
    return applyMask(img, out, mask);
  },
  noise(img, { amount = 20, mono = true, seed = 1 }, mask) {
    let st = seed >>> 0 || 1; const rnd = () => { st ^= st << 13; st ^= st >>> 17; st ^= st << 5; return ((st >>> 0) / 4294967296) - 0.5; };
    const out = cloneImage(img), o = out.data, k = amount * 2.55;
    for (let i = 0; i < o.length; i += 4) {
      if (mono) { const n = rnd() * k; o[i] += n; o[i + 1] += n; o[i + 2] += n; }
      else { o[i] += rnd() * k; o[i + 1] += rnd() * k; o[i + 2] += rnd() * k; }
    }
    return applyMask(img, out, mask);
  },
  denoise(img, { radius = 1 } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, r = Math.max(1, Math.min(3, Math.round(radius)));
    const buf = [[], [], []];
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      buf[0].length = buf[1].length = buf[2].length = 0;
      for (let ky = -r; ky <= r; ky++) for (let kx = -r; kx <= r; kx++) {
        const sx = Math.min(w - 1, Math.max(0, x + kx)), sy = Math.min(h - 1, Math.max(0, y + ky)), i = (sy * w + sx) * 4;
        buf[0].push(s[i]); buf[1].push(s[i + 1]); buf[2].push(s[i + 2]);
      }
      const i = (y * w + x) * 4, mid = buf[0].length >> 1;
      for (let c = 0; c < 3; c++) { buf[c].sort((a, b) => a - b); o[i + c] = buf[c][mid]; }
    }
    return applyMask(img, out, mask);
  },
  pixelate(img, { size = 10 }, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, n = Math.max(2, Math.round(size));
    for (let by = 0; by < h; by += n) for (let bx = 0; bx < w; bx += n) {
      let r = 0, g = 0, b = 0, a = 0, c = 0;
      for (let y = by; y < Math.min(h, by + n); y++) for (let x = bx; x < Math.min(w, bx + n); x++) { const i = (y * w + x) * 4; r += s[i]; g += s[i + 1]; b += s[i + 2]; a += s[i + 3]; c++; }
      r /= c; g /= c; b /= c; a /= c;
      for (let y = by; y < Math.min(h, by + n); y++) for (let x = bx; x < Math.min(w, bx + n); x++) { const i = (y * w + x) * 4; o[i] = r; o[i + 1] = g; o[i + 2] = b; o[i + 3] = a; }
    }
    return applyMask(img, out, mask);
  },
  vignette(img, { amount = 50, size = 60, color = "#000000" } = {}, mask) {
    const { width: w, height: h } = img, out = cloneImage(img), o = out.data, cx = w / 2, cy = h / 2, k = amount / 100;
    const inner = clamp(size, 5, 100) / 100, col = parseHex(color) || [0, 0, 0];
    const maxD = Math.hypot(cx, cy);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const d = Math.hypot((x - cx) / cx * (maxD / Math.SQRT2) / maxD * Math.SQRT2, (y - cy) / cy * (maxD / Math.SQRT2) / maxD * Math.SQRT2) / Math.SQRT2;
      const t = clamp((d - inner * 0.75) / (1.05 - inner * 0.75), 0, 1), f = t * t * (3 - 2 * t) * k;
      if (!f) continue;
      const i = (y * w + x) * 4; o[i] += (col[0] - o[i]) * f; o[i + 1] += (col[1] - o[i + 1]) * f; o[i + 2] += (col[2] - o[i + 2]) * f;
    }
    return applyMask(img, out, mask);
  },
  oilPaint(img, { radius = 3, levels = 20 } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, r = Math.max(1, Math.min(6, Math.round(radius))), L = Math.max(4, Math.min(64, levels));
    const cnt = new Uint32Array(L), sr = new Float64Array(L), sg = new Float64Array(L), sb = new Float64Array(L);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      cnt.fill(0); sr.fill(0); sg.fill(0); sb.fill(0);
      for (let ky = -r; ky <= r; ky++) for (let kx = -r; kx <= r; kx++) {
        const sx = Math.min(w - 1, Math.max(0, x + kx)), sy = Math.min(h - 1, Math.max(0, y + ky)), i = (sy * w + sx) * 4;
        const lv = Math.min(L - 1, Math.floor((lumOf(s[i], s[i + 1], s[i + 2]) / 256) * L));
        cnt[lv]++; sr[lv] += s[i]; sg[lv] += s[i + 1]; sb[lv] += s[i + 2];
      }
      let best = 0; for (let k = 1; k < L; k++) if (cnt[k] > cnt[best]) best = k;
      const i = (y * w + x) * 4; o[i] = sr[best] / cnt[best]; o[i + 1] = sg[best] / cnt[best]; o[i + 2] = sb[best] / cnt[best];
    }
    return applyMask(img, out, mask);
  },
  zoomBlur(img, { amount = 25, cx = 50, cy = 50 } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, k = clamp(amount, 0, 100) / 100 * 0.35, N = 18;
    if (!k) return applyMask(img, out, mask);
    const ox = w * clamp(cx, 0, 100) / 100, oy = h * clamp(cy, 0, 100) / 100;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let r = 0, g = 0, b = 0, a = 0;
      for (let j = 0; j < N; j++) {
        const f = 1 - k * j / (N - 1), sx = Math.round(ox + (x - ox) * f), sy = Math.round(oy + (y - oy) * f), i = ((sy < 0 ? 0 : sy >= h ? h - 1 : sy) * w + (sx < 0 ? 0 : sx >= w ? w - 1 : sx)) * 4, al = s[i + 3];
        r += s[i] * al; g += s[i + 1] * al; b += s[i + 2] * al; a += al;
      }
      const i = (y * w + x) * 4;
      if (a) { o[i] = r / a; o[i + 1] = g / a; o[i + 2] = b / a; } o[i + 3] = a / N;
    }
    return applyMask(img, out, mask);
  },
  tiltShift(img, { position = 55, band = 22, radius = 10 } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, bl = gaussianBlurData(s, w, h, Math.max(0.5, radius));
    const c = clamp(position, 0, 100) / 100, half = clamp(band, 1, 100) / 200, soft = Math.max(0.02, half * 1.2);
    for (let y = 0; y < h; y++) {
      let t = clamp((Math.abs((y + 0.5) / h - c) - half) / soft, 0, 1); t = t * t * (3 - 2 * t);
      if (!t) continue;
      for (let x = 0, i = y * w * 4; x < w; x++, i += 4) { o[i] = s[i] + (bl[i] - s[i]) * t; o[i + 1] = s[i + 1] + (bl[i + 1] - s[i + 1]) * t; o[i + 2] = s[i + 2] + (bl[i + 2] - s[i + 2]) * t; o[i + 3] = s[i + 3] + (bl[i + 3] - s[i + 3]) * t; }
    }
    return applyMask(img, out, mask);
  },
  chromatic(img, { distance = 4 } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, cx = (w - 1) / 2, cy = (h - 1) / 2, k = clamp(distance, 0, 200) / Math.max(1, Math.hypot(cx, cy));
    if (!k) return applyMask(img, out, mask);
    const at = (x, y) => ((y < 0 ? 0 : y >= h ? h - 1 : y) * w + (x < 0 ? 0 : x >= w ? w - 1 : x)) * 4;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const dx = x - cx, dy = y - cy, i = (y * w + x) * 4;
      o[i] = s[at(Math.round(cx + dx * (1 - k)), Math.round(cy + dy * (1 - k)))];
      o[i + 2] = s[at(Math.round(cx + dx * (1 + k)), Math.round(cy + dy * (1 + k))) + 2];
    }
    return applyMask(img, out, mask);
  },
  halftone(img, { size = 8, angle = 45, colored = false } = {}, mask) {
    const { width: w, height: h, data: s } = img, out = cloneImage(img), o = out.data, n = Math.max(3, size), a = (angle * Math.PI) / 180, ca = Math.cos(a), sa = Math.sin(a);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const u = x * ca + y * sa, v = -x * sa + y * ca, cu = (Math.floor(u / n) + 0.5) * n, cv = (Math.floor(v / n) + 0.5) * n;
      const X = Math.round(cu * ca - cv * sa), Y = Math.round(cu * sa + cv * ca), j = ((Y < 0 ? 0 : Y >= h ? h - 1 : Y) * w + (X < 0 ? 0 : X >= w ? w - 1 : X)) * 4;
      const L = lumOf(s[j], s[j + 1], s[j + 2]) / 255, r = n * Math.sqrt((1 - L * (colored ? 0.8 : 1)) / Math.PI), cover = clamp(r - Math.hypot(u - cu, v - cv) + 0.5, 0, 1);
      const i = (y * w + x) * 4, ink = colored ? [s[j], s[j + 1], s[j + 2]] : [0, 0, 0];
      o[i] = 255 + (ink[0] - 255) * cover; o[i + 1] = 255 + (ink[1] - 255) * cover; o[i + 2] = 255 + (ink[2] - 255) * cover;
    }
    return applyMask(img, out, mask);
  },
  sketch(img, { radius = 10, strength = 100 } = {}, mask) {
    const { width: w, height: h, data: s } = img, n = w * h, inv = new Uint8ClampedArray(n * 4), gray = new Uint8Array(n);
    for (let p = 0, i = 0; p < n; p++, i += 4) { const g = Math.round(lumOf(s[i], s[i + 1], s[i + 2])); gray[p] = g; inv[i] = inv[i + 1] = inv[i + 2] = 255 - g; inv[i + 3] = 255; }
    const bl = gaussianBlurData(inv, w, h, Math.max(0.5, radius)), out = cloneImage(img), o = out.data, k = clamp(strength, 0, 300) / 100;
    for (let p = 0, i = 0; p < n; p++, i += 4) {
      const b = bl[i], dodge = b >= 255 ? 255 : Math.min(255, (gray[p] * 255) / (255 - b)), v = clamp(255 - (255 - dodge) * k);
      o[i] = o[i + 1] = o[i + 2] = v;
    }
    return applyMask(img, out, mask);
  },
};

export function runOp(name, img, params = {}, mask = null) {
  const fn = ADJUST[name] || FILTER[name];
  if (!fn) throw new Error("unknown operation " + name);
  return fn(img, params || {}, mask);
}
export const OPS = [...Object.keys(ADJUST), ...Object.keys(FILTER)];

const B = {
  normal: (b, s) => s, multiply: (b, s) => b * s, screen: (b, s) => b + s - b * s,
  overlay: (b, s) => B["hard-light"](s, b), darken: (b, s) => Math.min(b, s), lighten: (b, s) => Math.max(b, s),
  "color-dodge": (b, s) => (b === 0 ? 0 : s >= 1 ? 1 : Math.min(1, b / (1 - s))),
  "color-burn": (b, s) => (b >= 1 ? 1 : s <= 0 ? 0 : 1 - Math.min(1, (1 - b) / s)),
  "hard-light": (b, s) => (s <= 0.5 ? b * 2 * s : B.screen(b, 2 * s - 1)),
  "soft-light": (b, s) => { if (s <= 0.5) return b - (1 - 2 * s) * b * (1 - b); const d = b <= 0.25 ? ((16 * b - 12) * b + 4) * b : Math.sqrt(b); return b + (2 * s - 1) * (d - b); },
  difference: (b, s) => Math.abs(b - s), exclusion: (b, s) => b + s - 2 * b * s,
};
export const BLEND_MODES = ["normal", "multiply", "screen", "overlay", "darken", "lighten", "color-dodge", "color-burn", "hard-light", "soft-light", "difference", "exclusion", "hue", "saturation", "color", "luminosity"];
const LUM = (c) => 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2];
function clipColor(c) { const l = LUM(c), n = Math.min(...c), x = Math.max(...c); if (n < 0) c = c.map((v) => l + ((v - l) * l) / (l - n)); if (x > 1) c = c.map((v) => l + ((v - l) * (1 - l)) / (x - l)); return c; }
const setLum = (c, l) => { const d = l - LUM(c); return clipColor(c.map((v) => v + d)); };
const SAT = (c) => Math.max(...c) - Math.min(...c);
function setSat(c, s) {
  const idx = [0, 1, 2].sort((a, b) => c[a] - c[b]), out = [0, 0, 0], [mn, md, mx] = idx;
  if (c[mx] > c[mn]) { out[md] = ((c[md] - c[mn]) * s) / (c[mx] - c[mn]); out[mx] = s; } else { out[md] = out[mx] = 0; }
  out[mn] = 0; return out;
}

export function blendPixel(mode, back, src, opacity = 1) {
  const as = (src[3] / 255) * opacity, ab = back[3] / 255, ao = as + ab * (1 - as);
  if (ao <= 0) return [0, 0, 0, 0];
  const cb = [back[0] / 255, back[1] / 255, back[2] / 255], cs = [src[0] / 255, src[1] / 255, src[2] / 255];
  let mixed;
  if (mode === "hue") mixed = setLum(setSat(cs, SAT(cb)), LUM(cb));
  else if (mode === "saturation") mixed = setLum(setSat(cb, SAT(cs)), LUM(cb));
  else if (mode === "color") mixed = setLum(cs, LUM(cb));
  else if (mode === "luminosity") mixed = setLum(cb, LUM(cs));
  else { const f = B[mode] || B.normal; mixed = [f(cb[0], cs[0]), f(cb[1], cs[1]), f(cb[2], cs[2])]; }
  const out = [0, 0, 0];
  for (let c = 0; c < 3; c++) {
    const csm = (1 - ab) * cs[c] + ab * mixed[c];
    out[c] = Math.round(((as * csm + ab * cb[c] * (1 - as)) / ao) * 255);
  }
  return [out[0], out[1], out[2], Math.round(ao * 255)];
}

export function magicWand(img, sx, sy, { tolerance = 32, contiguous = true, antialias = false } = {}) {
  const { width: w, height: h, data: d } = img, mask = new Uint8Array(w * h);
  sx = Math.floor(sx); sy = Math.floor(sy);
  if (sx < 0 || sy < 0 || sx >= w || sy >= h) return mask;
  const i0 = (sy * w + sx) * 4, r0 = d[i0], g0 = d[i0 + 1], b0 = d[i0 + 2], a0 = d[i0 + 3], tol = tolerance;
  const near = (p) => { const i = p * 4; return Math.abs(d[i] - r0) <= tol && Math.abs(d[i + 1] - g0) <= tol && Math.abs(d[i + 2] - b0) <= tol && Math.abs(d[i + 3] - a0) <= tol; };
  if (!contiguous) { for (let p = 0; p < w * h; p++) if (near(p)) mask[p] = 255; }
  else {
    const stack = new Int32Array(w * h); let top = 0; stack[top++] = sy * w + sx; mask[sy * w + sx] = 255;
    while (top) {
      const p = stack[--top], x = p % w, y = (p / w) | 0;
      if (x > 0 && !mask[p - 1] && near(p - 1)) { mask[p - 1] = 255; stack[top++] = p - 1; }
      if (x < w - 1 && !mask[p + 1] && near(p + 1)) { mask[p + 1] = 255; stack[top++] = p + 1; }
      if (y > 0 && !mask[p - w] && near(p - w)) { mask[p - w] = 255; stack[top++] = p - w; }
      if (y < h - 1 && !mask[p + w] && near(p + w)) { mask[p + w] = 255; stack[top++] = p + w; }
    }
  }
  return antialias ? featherMask(mask, w, h, 1) : mask;
}
export function colorRange(img, rgb, fuzz = 40) {
  const { width: w, height: h, data: d } = img, mask = new Uint8Array(w * h), lim = fuzz * 2.2;
  for (let p = 0, i = 0; p < w * h; p++, i += 4) {
    const dist = Math.hypot(d[i] - rgb[0], d[i + 1] - rgb[1], d[i + 2] - rgb[2]);
    mask[p] = dist >= lim ? 0 : Math.round(255 * (1 - dist / lim) ** 0.5);
  }
  return mask;
}
export function combineMasks(a, b, op) {
  const out = new Uint8Array(a.length);
  for (let i = 0; i < a.length; i++) {
    if (op === "add") out[i] = Math.max(a[i], b[i]);
    else if (op === "subtract") out[i] = Math.min(a[i], 255 - b[i]);
    else if (op === "intersect") out[i] = Math.min(a[i], b[i]);
    else out[i] = b[i];
  }
  return out;
}
export function invertMask(m) { const o = new Uint8Array(m.length); for (let i = 0; i < m.length; i++) o[i] = 255 - m[i]; return o; }
export function featherMask(mask, w, h, radius) {
  if (radius < 0.5) return mask;
  const rgba = new Uint8ClampedArray(w * h * 4);
  for (let p = 0; p < mask.length; p++) { rgba[p * 4 + 3] = 255; rgba[p * 4] = mask[p]; }
  const out = gaussianBlurData(rgba, w, h, radius), m = new Uint8Array(w * h);
  for (let p = 0; p < m.length; p++) m[p] = out[p * 4];
  return m;
}

export function growMask(mask, w, h, r) {
  if (!r) return mask;
  const grow = r > 0, n = Math.abs(Math.round(r)), tmp = new Uint8Array(mask.length), out = new Uint8Array(mask.length);
  const pick = grow ? Math.max : Math.min;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = grow ? 0 : 255;
    for (let k = -n; k <= n; k++) { const xx = x + k; const s = xx < 0 || xx >= w ? (grow ? 0 : 0) : mask[y * w + xx]; v = pick(v, s); }
    tmp[y * w + x] = v;
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let v = grow ? 0 : 255;
    for (let k = -n; k <= n; k++) { const yy = y + k; const s = yy < 0 || yy >= h ? 0 : tmp[yy * w + x]; v = pick(v, s); }
    out[y * w + x] = v;
  }
  return out;
}
export function maskBounds(mask, w, h, min = 1) {
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) { const row = y * w; for (let x = 0; x < w; x++) if (mask[row + x] >= min) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; } }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
}
export function maskArea(mask) { let n = 0; for (let i = 0; i < mask.length; i++) n += mask[i]; return n / 255; }

function kmeans(samples, k, iters = 8) {

  const n = samples.length / 3; if (!n) return [];
  const centers = [];
  for (let c = 0; c < k; c++) { const i = Math.floor(((c + 0.5) / k) * n) * 3; centers.push([samples[i], samples[i + 1], samples[i + 2]]); }
  const assign = new Int32Array(n);
  for (let it = 0; it < iters; it++) {
    const sums = centers.map(() => [0, 0, 0, 0]);
    for (let p = 0; p < n; p++) {
      let best = 0, bd = Infinity;
      for (let c = 0; c < k; c++) { const dr = samples[p * 3] - centers[c][0], dg = samples[p * 3 + 1] - centers[c][1], db = samples[p * 3 + 2] - centers[c][2]; const dd = dr * dr + dg * dg + db * db; if (dd < bd) { bd = dd; best = c; } }
      assign[p] = best; const s = sums[best]; s[0] += samples[p * 3]; s[1] += samples[p * 3 + 1]; s[2] += samples[p * 3 + 2]; s[3]++;
    }
    for (let c = 0; c < k; c++) if (sums[c][3]) centers[c] = [sums[c][0] / sums[c][3], sums[c][1] / sums[c][3], sums[c][2] / sums[c][3]];
  }
  return centers;
}
function nearestDist(centers, r, g, b) { let bd = Infinity; for (const c of centers) { const d = (r - c[0]) ** 2 + (g - c[1]) ** 2 + (b - c[2]) ** 2; if (d < bd) bd = d; } return Math.sqrt(bd); }
function sampleRegion(img, pick, maxN = 6000) {
  const { width: w, height: h, data: d } = img, idx = [];
  for (let p = 0; p < w * h; p++) if (pick(p % w, (p / w) | 0, p)) idx.push(p);
  const step = Math.max(1, Math.floor(idx.length / maxN)), out = new Float32Array(Math.ceil(idx.length / step) * 3);
  let j = 0; for (let i = 0; i < idx.length; i += step) { const q = idx[i] * 4; out[j++] = d[q]; out[j++] = d[q + 1]; out[j++] = d[q + 2]; }
  return out.subarray(0, j);
}
function largestComponent(bin, w, h, prefer) {
  const label = new Int32Array(w * h).fill(-1), sizes = []; let best = -1, bestScore = -1;
  const stack = new Int32Array(w * h);
  for (let p0 = 0; p0 < w * h; p0++) {
    if (!bin[p0] || label[p0] >= 0) continue;
    const id = sizes.length; let top = 0, size = 0, hit = 0; stack[top++] = p0; label[p0] = id;
    while (top) {
      const p = stack[--top]; size++; if (prefer && prefer[p]) hit++;
      const x = p % w, y = (p / w) | 0;
      for (const q of [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, y > 0 ? p - w : -1, y < h - 1 ? p + w : -1]) if (q >= 0 && bin[q] && label[q] < 0) { label[q] = id; stack[top++] = q; }
    }
    sizes.push(size);
    const score = size + hit * 4;
    if (score > bestScore) { bestScore = score; best = id; }
  }
  const out = new Uint8Array(w * h); if (best < 0) return out;
  for (let p = 0; p < w * h; p++) if (label[p] === best) out[p] = 1;
  return out;
}
function fillHoles(bin, w, h) {

  const outside = new Uint8Array(w * h), stack = new Int32Array(w * h); let top = 0;
  const push = (p) => { if (!bin[p] && !outside[p]) { outside[p] = 1; stack[top++] = p; } };
  for (let x = 0; x < w; x++) { push(x); push((h - 1) * w + x); }
  for (let y = 0; y < h; y++) { push(y * w); push(y * w + w - 1); }
  while (top) { const p = stack[--top], x = p % w, y = (p / w) | 0; if (x > 0) push(p - 1); if (x < w - 1) push(p + 1); if (y > 0) push(p - w); if (y < h - 1) push(p + w); }
  const out = new Uint8Array(w * h); for (let p = 0; p < w * h; p++) out[p] = outside[p] ? 0 : 1; return out;
}

export function segment(img, box, { iterations = 4, k = 6, feather = 1.2, seedMask = null } = {}) {
  const { width: w, height: h, data: d } = img;
  const bx = Math.max(0, Math.floor(box.x)), by = Math.max(0, Math.floor(box.y)), bw = Math.min(w - bx, Math.ceil(box.w)), bh = Math.min(h - by, Math.ceil(box.h));
  const inBox = (x, y) => x >= bx && y >= by && x < bx + bw && y < by + bh;
  const touchesAll = bx <= 1 && by <= 1 && bx + bw >= w - 1 && by + bh >= h - 1;
  const band = Math.max(2, Math.round(Math.min(bw, bh) * 0.06));
  let bgS = sampleRegion(img, (x, y) => (touchesAll ? (x < band || y < band || x >= w - band || y >= h - band) : !inBox(x, y) || x < bx + 2 || y < by + 2 || x >= bx + bw - 2 || y >= by + bh - 2));
  const cx = bx + bw / 2, cy = by + bh / 2;
  let fgS = sampleRegion(img, (x, y, p) => (seedMask ? seedMask[p] > 128 : Math.abs(x - cx) < bw * 0.22 && Math.abs(y - cy) < bh * 0.28));
  let fg = new Uint8Array(w * h);
  for (let it = 0; it < iterations; it++) {
    const bgC = kmeans(bgS, k + 4);

    let fgC = kmeans(fgS, k); const own = fgC.filter((c) => nearestDist(bgC, c[0], c[1], c[2]) > 28); if (own.length) fgC = own;
    const prob = new Float32Array(w * h * 4);
    for (let y = by; y < by + bh; y++) for (let x = bx; x < bx + bw; x++) {
      const p = y * w + x, i = p * 4;
      const db = nearestDist(bgC, d[i], d[i + 1], d[i + 2]), df = nearestDist(fgC, d[i], d[i + 1], d[i + 2]);
      let v = (db + 1) / (db + df + 2);
      if (d[i + 3] < 16) v = 0;
      if (seedMask) v = v * 0.6 + (seedMask[p] / 255) * 0.4;
      prob[i] = v * 255; prob[i + 3] = 255;
    }
    const sm = gaussianBlurData(prob, w, h, Math.max(1, Math.min(bw, bh) / 120));
    for (let p = 0; p < w * h; p++) { const x = p % w, y = (p / w) | 0; fg[p] = inBox(x, y) && sm[p * 4] > 132 ? 1 : 0; }
    fg = largestComponent(fg, w, h, null);
    fg = fillHoles(fg, w, h);

    fgS = sampleRegion(img, (x, y, p) => fg[p] === 1);
    const bgNew = sampleRegion(img, (x, y, p) => inBox(x, y) && fg[p] === 0);
    if (bgNew.length > 30) { const merged = new Float32Array(bgS.length + bgNew.length); merged.set(bgS); merged.set(bgNew, bgS.length); bgS = merged; }
    if (fgS.length < 30) break;
  }
  const mask = new Uint8Array(w * h); for (let p = 0; p < w * h; p++) mask[p] = fg[p] ? 255 : 0;
  return feather ? featherMask(mask, w, h, feather) : mask;
}

export function inpaint(img, mask, { noise = 0 } = {}) {
  const { width: w, height: h } = img, out = cloneImage(img), o = out.data;
  const known = new Uint8Array(w * h); let todo = 0;
  for (let p = 0; p < w * h; p++) { if (mask[p] < 128) known[p] = 1; else todo++; }
  let guard = 0;
  while (todo > 0 && guard++ < Math.max(w, h)) {
    const fill = [];
    for (let p = 0; p < w * h; p++) {
      if (known[p]) continue;
      const x = p % w, y = (p / w) | 0; let r = 0, g = 0, b = 0, a = 0, c = 0;
      for (let ky = -2; ky <= 2; ky++) for (let kx = -2; kx <= 2; kx++) {
        const xx = x + kx, yy = y + ky; if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        const q = yy * w + xx; if (!known[q]) continue;
        const wt = 1 / (kx * kx + ky * ky); const i = q * 4; r += o[i] * wt; g += o[i + 1] * wt; b += o[i + 2] * wt; a += o[i + 3] * wt; c += wt;
      }
      if (c > 0.6) fill.push([p, r / c, g / c, b / c, a / c]);
    }
    if (!fill.length) break;
    for (const [p, r, g, b, a] of fill) { const i = p * 4; o[i] = r; o[i + 1] = g; o[i + 2] = b; o[i + 3] = a; known[p] = 1; todo--; }
  }
  if (noise) FILTER.noise(out, { amount: noise, mono: true }, mask);
  return out;
}

export function resizeNearest(img, nw, nh) {
  const out = makeImage(nw, nh), o = out.data, s = img.data;
  for (let y = 0; y < nh; y++) { const sy = Math.min(img.height - 1, Math.floor((y + 0.5) * img.height / nh)); for (let x = 0; x < nw; x++) { const sx = Math.min(img.width - 1, Math.floor((x + 0.5) * img.width / nw)); const i = (y * nw + x) * 4, j = (sy * img.width + sx) * 4; o[i] = s[j]; o[i + 1] = s[j + 1]; o[i + 2] = s[j + 2]; o[i + 3] = s[j + 3]; } }
  return out;
}
export function resizeMask(mask, w, h, nw, nh) {
  const out = new Uint8Array(nw * nh);
  for (let y = 0; y < nh; y++) { const sy = Math.min(h - 1, Math.floor((y + 0.5) * h / nh)); for (let x = 0; x < nw; x++) out[y * nw + x] = mask[sy * w + Math.min(w - 1, Math.floor((x + 0.5) * w / nw))]; }
  return out;
}

export function palette(img, n = 6) {
  const s = sampleRegion(img, (x, y, p) => img.data[p * 4 + 3] > 64, 8000), centers = kmeans(s, n, 10);
  return centers.map((c) => toHex(c[0], c[1], c[2]));
}
