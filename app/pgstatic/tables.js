/* MemuAI 3.1 · Round 29 · Rich tables (window.MemuTables).
 *
 * 1. Streaming: a Markdown table that is still being written is never shown half-done (raw pipes, broken
 *    rows). maskPending() swaps the unfinished table at the end of a streaming answer for a hidden
 *    ```memuai-table-pending block, rendered by pending() as a quiet placeholder with a row counter. The
 *    table appears whole once the block closes (a non-table line follows or the answer ends).
 * 2. Rendered tables (.table-wrap > table of app.js and of the Quick Access renderer) get a hover toolbar:
 *    sort by column (numbers, dates and text understood), filter, column resize, copy (Markdown, TSV for
 *    Excel, CSV, HTML), export (CSV, XLSX, Markdown, JSON, HTML, PNG, SVG), fullscreen, picture studio and
 *    «Edit and send back to the AI».
 * 3. Table editor (composer «+» menu): a grid, paste from Excel/Google Sheets/CSV, import .csv/.tsv/.md/
 *    .json/.xlsx (xlsx parsed by the backend: /api/tables/xlsx), attach as a Markdown table or insert.
 * 4. Picture studio: many presets, background (color / gradient / image), colors, fonts, padding, borders,
 *    zebra, header style, title and caption, scale 1x–4x, live preview. Rendered offline to SVG and PNG.
 *
 * Everything here is offline and self-contained; app.js calls only maskPending / block / hydrate /
 * openEditor (small hooks). The Quick Access window uses the same file.
 */
"use strict";
(function () {
  const VERSION = "1.0-r29";
  const isEn = () => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en";
  const t = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many;
  };
  const rowsWord = (n) => isEn() ? (n === 1 ? "row" : "rows") : plural(n, "строка", "строки", "строк");

  /* ---------------------------------------------------------------- icons (own, stroke = currentColor) */
  const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const IC = {
    table: P('<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 9.5v10"/>'),
    sort: P('<path d="M8 4v16m0 0-3.5-3.5M8 20l3.5-3.5M16 20V4m0 0-3.5 3.5M16 4l3.5 3.5"/>'),
    up: P('<path d="m6 15 6-6 6 6"/>'),
    down: P('<path d="m6 9 6 6 6-6"/>'),
    filter: P('<path d="M4 5h16l-6 7.5V19l-4 1.5v-8z"/>'),
    copy: P('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
    download: P('<path d="M12 3v12m-5-5 5 5 5-5M5 21h14"/>'),
    image: P('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/>'),
    full: P('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
    edit: P('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
    close: P('<path d="m6 6 12 12M18 6 6 18"/>'),
    plus: P('<path d="M12 5v14M5 12h14"/>'),
    minus: P('<path d="M5 12h14"/>'),
    send: P('<path d="M12 19V5m-6 6 6-6 6 6"/>'),
    attach: P('<path d="m20 12-8 8a6 6 0 0 1-8.5-8.5l9-9a4 4 0 0 1 5.7 5.7l-9 9a2 2 0 0 1-2.8-2.8l8-8"/>'),
    upload: P('<path d="M12 21V9m-5 5 5-5 5 5M5 3h14"/>'),
    paste: P('<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4.5V3h6v1.5"/>'),
    check: P('<path d="m5 12 5 5L20 7"/>'),
    more: P('<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>'),
    alignL: P('<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>'),
    alignC: P('<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>'),
    alignR: P('<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>'),
    transpose: P('<path d="M4 4h7v7H4zM13 13h7v7h-7z"/><path d="M15 4h3a2 2 0 0 1 2 2v3m0 0-2-2m2 2 2-2M9 20H6a2 2 0 0 1-2-2v-3m0 0 2 2m-2-2-2 2"/>'),
    trash: P('<path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14"/>'),
  };
  const ic = (name) => `<span class="mt-ic">${IC[name] || ""}</span>`;
  // The composer menu uses app.js's icon(): give it a table icon too (app.js loads after this file).
  function registerIcons() {
    try { if (typeof ICONS === "object" && ICONS && !ICONS.table) ICONS.table = IC.table; } catch (e) { /* not in the main window */ }
  }

  /* ---------------------------------------------------------------- data model
     data = { headers: string[], rows: string[][], align: ("left"|"center"|"right"|"")[], title?, caption? } */
  function normalize(data) {
    const headers = (data && Array.isArray(data.headers) ? data.headers : []).map((v) => String(v ?? ""));
    const rows = (data && Array.isArray(data.rows) ? data.rows : []).filter(Array.isArray).map((r) => r.map((v) => String(v ?? "")));
    const width = Math.max(headers.length, ...rows.map((r) => r.length), 1);
    while (headers.length < width) headers.push("");
    rows.forEach((r) => { while (r.length < width) r.push(""); r.length = width; });
    const align = Array.from({ length: width }, (_, i) => ["left", "center", "right"].includes(data?.align?.[i]) ? data.align[i] : "");
    return { headers, rows, align, title: String(data?.title || ""), caption: String(data?.caption || "") };
  }

  function splitRow(line) {
    let body = line.trim();
    if (body.startsWith("|")) body = body.slice(1);
    if (body.endsWith("|") && !body.endsWith("\\|")) body = body.slice(0, -1);
    const cells = [];
    let cell = "", code = false;
    for (let k = 0; k < body.length; k += 1) {
      const ch = body[k];
      if (ch === "\\" && body[k + 1] === "|") { cell += "|"; k += 1; continue; }
      if (ch === "`") code = !code;
      if (ch === "|" && !code) { cells.push(cell.trim()); cell = ""; continue; }
      cell += ch;
    }
    cells.push(cell.trim());
    return cells;
  }
  const SEP_RE = /^\|?(?:\s*:?-{1,}:?\s*\|)+\s*:?-*:?\s*\|?$/;
  function parseMarkdown(md) {
    const lines = String(md || "").replace(/\r\n?/g, "\n").split("\n").map((l) => l.trim());
    for (let i = 0; i + 1 < lines.length; i += 1) {
      if (!lines[i].includes("|") || !SEP_RE.test(lines[i + 1]) || !lines[i + 1].includes("-")) continue;
      const headers = splitRow(lines[i]);
      const align = splitRow(lines[i + 1]).map((c) => /^:-+:$/.test(c) ? "center" : /-:$/.test(c) ? "right" : /^:-/.test(c) ? "left" : "");
      const rows = [];
      for (let j = i + 2; j < lines.length && lines[j].includes("|"); j += 1) rows.push(splitRow(lines[j]));
      let title = "";
      const before = lines.slice(0, i).filter(Boolean).pop() || "";
      const head = before.match(/^#{1,6}\s+(.+)$/) || before.match(/^\*\*(.+)\*\*$/);
      if (head) title = head[1].trim();
      return normalize({ headers, rows, align, title });
    }
    return null;
  }

  // CSV / TSV / semicolon text (Excel, Google Sheets, LibreOffice clipboard): quotes, doubled quotes, newlines in cells.
  function detectDelimiter(text) {
    const sample = String(text || "").split(/\r?\n/).slice(0, 12).join("\n");
    const counts = { "\t": 0, ";": 0, ",": 0, "|": 0 };
    let quoted = false;
    for (const ch of sample) {
      if (ch === '"') quoted = !quoted;
      else if (!quoted && ch in counts) counts[ch] += 1;
    }
    if (counts["\t"]) return "\t";
    return counts[";"] > counts[","] ? ";" : counts[","] ? "," : counts["|"] ? "|" : "\t";
  }
  function parseDelimited(text, delimiter) {
    const src = String(text || "").replace(/^\ufeff/, "").replace(/\r\n?/g, "\n").replace(/\n+$/, "");
    if (!src.trim()) return null;
    if (/^\s*\|.*\|\s*\n\s*\|?\s*:?-/.test(src)) return parseMarkdown(src);
    const d = delimiter || detectDelimiter(src);
    const out = [];
    let row = [], cell = "", quoted = false;
    for (let i = 0; i < src.length; i += 1) {
      const ch = src[i];
      if (quoted) {
        if (ch === '"' && src[i + 1] === '"') { cell += '"'; i += 1; }
        else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"' && cell === "") quoted = true;
      else if (ch === d) { row.push(cell); cell = ""; }
      else if (ch === "\n") { row.push(cell); out.push(row); row = []; cell = ""; }
      else cell += ch;
    }
    row.push(cell); out.push(row);
    const clean = out.map((r) => r.map((c) => c.trim()));
    if (!clean.length) return null;
    return normalize({ headers: clean[0], rows: clean.slice(1) });
  }

  // A rendered <table> back to data (the visible text of each cell; current order and filter ignored).
  function fromTable(table, { visibleOnly = false } = {}) {
    if (!table) return normalize({});
    if (table.headers) return normalize(table);
    const el = table.matches?.("table") ? table : table.querySelector?.("table");
    if (!el) return normalize({});
    const text = (cell) => (cell.dataset.mtRaw ?? cell.innerText ?? cell.textContent ?? "").replace(/\s*\n\s*/g, " ").trim();
    const headRow = el.tHead?.rows?.[0] || el.rows?.[0];
    const headers = headRow ? [...headRow.cells].map(text) : [];
    const align = headRow ? [...headRow.cells].map((c) => c.dataset.mtAlign || (c.style.textAlign || "")) : [];
    const bodyRows = [...(el.tBodies?.[0]?.rows || [])].filter((r) => !visibleOnly || !r.hidden);
    const rows = (el.tBodies?.length ? bodyRows : [...el.rows].slice(1)).map((r) => [...r.cells].map(text));
    const host = el.closest(".mt-host");
    return normalize({ headers, rows, align, title: host?.dataset.mtTitle || "" });
  }

  /* ---------------------------------------------------------------- formats */
  const mdCell = (v) => String(v).replace(/\|/g, "\\|").replace(/\n/g, " ");
  function toMarkdown(data, { pretty = true } = {}) {
    const d = normalize(data);
    const width = d.headers.map((h, i) => Math.max(3, mdCell(h).length, ...d.rows.map((r) => mdCell(r[i]).length)));
    const pad = (v, i) => pretty ? mdCell(v).padEnd(Math.min(width[i], 40)) : mdCell(v);
    const sep = d.headers.map((_, i) => {
      const n = pretty ? Math.min(width[i], 40) : 3, a = d.align[i];
      return a === "center" ? ":" + "-".repeat(Math.max(1, n - 2)) + ":" : a === "right" ? "-".repeat(Math.max(2, n - 1)) + ":" : a === "left" ? ":" + "-".repeat(Math.max(2, n - 1)) : "-".repeat(n);
    });
    const line = (cells) => "| " + cells.join(" | ") + " |";
    const out = [line(d.headers.map(pad)), line(sep), ...d.rows.map((r) => line(r.map(pad)))];
    return out.join("\n");
  }
  const tsvCell = (v) => /[\t\n"]/.test(v) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  function toTSV(data) {
    const d = normalize(data);
    return [d.headers, ...d.rows].map((r) => r.map(tsvCell).join("\t")).join("\r\n");
  }
  function toCSV(data, { delimiter = ",", bom = false } = {}) {
    const d = normalize(data);
    const cell = (v) => (new RegExp(`[${delimiter === "\t" ? "\\t" : delimiter}\\n\\r"]`).test(v) || /^\s|\s$/.test(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
    return (bom ? "\ufeff" : "") + [d.headers, ...d.rows].map((r) => r.map(cell).join(delimiter)).join("\r\n");
  }
  function toHTML(data, { standalone = false } = {}) {
    const d = normalize(data);
    const al = (i) => d.align[i] ? ` style="text-align:${d.align[i]}"` : "";
    const table = `<table>${d.title ? `<caption>${esc(d.title)}</caption>` : ""}<thead><tr>${d.headers.map((h, i) => `<th${al(i)}>${esc(h)}</th>`).join("")}</tr></thead>` +
      `<tbody>${d.rows.map((r) => `<tr>${r.map((c, i) => `<td${al(i)}>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    if (!standalone) return table;
    return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(d.title || "MemuAI table")}</title><style>
body{font:14px/1.5 "Segoe UI",system-ui,sans-serif;margin:32px;color:#1f2328;background:#fff}
table{border-collapse:collapse;min-width:50%}caption{font-weight:600;font-size:16px;text-align:left;margin-bottom:8px}
th,td{border:1px solid #d0d7de;padding:7px 12px;vertical-align:top}th{background:#f3f4f6;text-align:left}
tbody tr:nth-child(even) td{background:#fafbfc}</style></head><body>${table}${d.caption ? `<p>${esc(d.caption)}</p>` : ""}</body></html>`;
  }
  function toJSON(data) {
    const d = normalize(data);
    const keys = d.headers.map((h, i) => h || `column_${i + 1}`);
    return JSON.stringify(d.rows.map((r) => Object.fromEntries(keys.map((k, i) => [k, typedValue(r[i])]))), null, 2);
  }

  /* numbers in cells: «1 234,5», «$1,234.50», «12%», «−3», «1.2k» */
  function parseNumber(value) {
    let s = String(value ?? "").trim().replace(/[\u00a0\u202f\u2009 ]/g, "").replace(/[−–]/g, "-");
    if (!s) return null;
    s = s.replace(/^[~≈±]|[*]+$/g, "");
    let mult = 1;
    const unit = s.match(/^(.*?)(%|k|K|тыс\.?|млн|M|млрд|B)$/);
    if (unit) { s = unit[1]; mult = { "%": 1, k: 1e3, K: 1e3, "тыс": 1e3, "тыс.": 1e3, "млн": 1e6, M: 1e6, "млрд": 1e9, B: 1e9 }[unit[2]] || 1; }
    s = s.replace(/^[$€£¥₽₸₴]|[$€£¥₽₸₴]$|(?:руб\.?|р\.|USD|EUR|RUB)$/gi, "");
    if (!/^-?[\d.,]+$/.test(s) || !/\d/.test(s)) return null;
    if (/^\d{1,2}[./]\d{1,2}[./]\d{2,4}$/.test(s)) return null;           // a date, not a number
    const lastComma = s.lastIndexOf(","), lastDot = s.lastIndexOf(".");
    if (lastComma > -1 && lastDot > -1) s = lastComma > lastDot ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    else if (lastComma > -1) s = /^-?\d{1,3}(,\d{3})+$/.test(s) && !/^-?0,/.test(s) ? s.replace(/,/g, "") : (s.match(/,/g).length > 1 ? "x" : s.replace(",", "."));
    else if ((s.match(/\./g) || []).length > 1) s = /^-?\d{1,3}(\.\d{3})+$/.test(s) ? s.replace(/\./g, "") : "x";
    const n = Number(s);
    return Number.isFinite(n) ? n * mult : null;
  }
  function parseDate(value) {
    const s = String(value || "").trim();
    let m = s.match(/^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$/);
    if (m) { const y = +m[3] < 100 ? 2000 + +m[3] : +m[3]; return new Date(y, +m[2] - 1, +m[1]).getTime(); }
    m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0)).getTime();
    return null;
  }
  function typedValue(v) {
    const s = String(v ?? "");
    if (/^-?\d+(\.\d+)?$/.test(s.trim()) && s.trim().length < 16) return Number(s);
    if (/^(true|false)$/i.test(s.trim())) return s.trim().toLowerCase() === "true";
    return s;
  }
  function columnKind(rows, col) {
    let nums = 0, dates = 0, filled = 0;
    for (const r of rows) {
      const v = String(r[col] ?? "").trim();
      if (!v || v === "—" || v === "-") continue;
      filled += 1;
      if (parseNumber(v) !== null) nums += 1;
      else if (parseDate(v) !== null) dates += 1;
    }
    if (!filled) return "text";
    return nums / filled >= 0.8 ? "number" : dates / filled >= 0.8 ? "date" : "text";
  }
  const collator = (() => { try { return new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }); } catch (e) { return null; } })();
  function compareValues(a, b, kind) {
    if (kind === "number") {
      const x = parseNumber(a), y = parseNumber(b);
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return x - y;
    }
    if (kind === "date") {
      const x = parseDate(a), y = parseDate(b);
      if (x === null || y === null) return x === null ? (y === null ? 0 : 1) : -1;
      return x - y;
    }
    const sa = String(a ?? ""), sb = String(b ?? "");
    if (!sa && sb) return 1;
    if (sa && !sb) return -1;
    return collator ? collator.compare(sa, sb) : sa.localeCompare(sb);
  }

  /* ---------------------------------------------------------------- XLSX writer (stored zip, no library) */
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) { let c = n; for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; }
    return table;
  })();
  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  function zipStore(files) {
    const enc = new TextEncoder();
    const chunks = [], central = [];
    let offset = 0;
    const now = new Date();
    const time = (now.getHours() << 11) | (now.getMinutes() << 5) | (now.getSeconds() >> 1);
    const date = ((now.getFullYear() - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate();
    for (const file of files) {
      const name = enc.encode(file.name);
      const data = typeof file.data === "string" ? enc.encode(file.data) : file.data;
      const crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(8, 0, true); local.setUint16(10, time, true); local.setUint16(12, date, true);
      local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
      local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
      chunks.push(new Uint8Array(local.buffer), name, data);
      const cen = new DataView(new ArrayBuffer(46));
      cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, 0x0800, true);
      cen.setUint16(10, 0, true); cen.setUint16(12, time, true); cen.setUint16(14, date, true);
      cen.setUint32(16, crc, true); cen.setUint32(20, data.length, true); cen.setUint32(24, data.length, true);
      cen.setUint16(28, name.length, true); cen.setUint32(42, offset, true);
      central.push(new Uint8Array(cen.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const size = central.reduce((n, c) => n + c.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, size, true); end.setUint32(16, offset, true);
    return new Blob([...chunks, ...central, new Uint8Array(end.buffer)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  }
  const xmlEsc = (v) => esc(v).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "");
  function colName(i) { let s = ""; i += 1; while (i) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; }
  function toXLSX(data) {
    const d = normalize(data);
    const kinds = d.headers.map((_, i) => columnKind(d.rows, i));
    const widths = d.headers.map((h, i) => Math.min(60, Math.max(8, String(h).length + 2, ...d.rows.map((r) => String(r[i]).length + 1))));
    const cell = (v, r, c, header) => {
      const ref = colName(c) + (r + 1);
      const n = !header && kinds[c] === "number" ? parseNumber(v) : null;
      if (n !== null && !/%$/.test(String(v).trim())) return `<c r="${ref}"><v>${n}</v></c>`;
      if (!String(v).length) return "";
      return `<c r="${ref}" t="inlineStr"${header ? ' s="1"' : ""}><is><t xml:space="preserve">${xmlEsc(v)}</t></is></c>`;
    };
    const rowsXml = [d.headers, ...d.rows].map((r, ri) => `<row r="${ri + 1}">${r.map((v, ci) => cell(v, ri, ci, ri === 0)).join("")}</row>`).join("");
    const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join("")}</cols><sheetData>${rowsXml}</sheetData><autoFilter ref="A1:${colName(d.headers.length - 1)}${d.rows.length + 1}"/></worksheet>`;
    const sheetName = xmlEsc((d.title || "Table").replace(/[\\/?*[\]:]/g, " ").slice(0, 31) || "Table");
    return zipStore([
      { name: "[Content_Types].xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>` },
      { name: "_rels/.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>` },
      { name: "xl/workbook.xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${sheetName}" sheetId="1" r:id="rId1"/></sheets><definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'${sheetName.replace(/'/g, "''")}'!$A$1:$${colName(d.headers.length - 1)}$${d.rows.length + 1}</definedName></definedNames></workbook>` },
      { name: "xl/_rels/workbook.xml.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>` },
      { name: "xl/styles.xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF1F3F5"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>` },
      { name: "xl/worksheets/sheet1.xml", data: sheet },
    ]);
  }

  /* ---------------------------------------------------------------- saving and clipboard */
  const blobToDataUrl = (blob) => new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(blob); });
  function notify(text, kind = "ok", action) {
    if (typeof toast === "function") { try { toast(text, kind, action || undefined); return; } catch (e) { /* below */ } }
    const box = document.createElement("div");
    box.className = "mt-toast " + kind;
    box.textContent = text;
    document.body.appendChild(box);
    setTimeout(() => box.classList.add("show"), 10);
    setTimeout(() => { box.classList.remove("show"); setTimeout(() => box.remove(), 300); }, 2600);
  }
  function safeName(name) {
    return String(name || "table").replace(/[\\/:*?"<>|\r\n\t]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) || "table";
  }
  async function saveFile(name, content, mime = "text/plain;charset=utf-8") {
    const blob = content instanceof Blob ? content : new Blob([content], { type: mime });
    const fileName = safeName(name);
    try {
      const url = await blobToDataUrl(blob);
      const resp = await fetch("/api/pg/download-file", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url, name: fileName }) });
      const saved = await resp.json().catch(() => ({}));
      if (resp.ok && saved && saved.ok) {
        if (typeof savedToast === "function") savedToast(saved);
        else notify(t("Сохранено в «Загрузки/MemuAI»: ", "Saved to Downloads/MemuAI: ") + (saved.name || fileName));
        return saved;
      }
    } catch (e) { /* browser mode below */ }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = fileName;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    return { ok: true, name: fileName };
  }
  async function copyText(text, html) {
    try {
      if (html && window.ClipboardItem && navigator.clipboard?.write) {
        await navigator.clipboard.write([new ClipboardItem({ "text/plain": new Blob([text], { type: "text/plain" }), "text/html": new Blob([html], { type: "text/html" }) })]);
      } else await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      const area = document.createElement("textarea");
      area.value = text; area.style.cssText = "position:fixed;opacity:0;left:-9999px";
      document.body.appendChild(area); area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      area.remove();
      return ok;
    }
  }
  async function copyImageBlob(blob) {
    if (!window.ClipboardItem || !navigator.clipboard?.write) throw new Error(t("Буфер обмена не принимает картинки", "The clipboard does not accept images"));
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  }

  /* ---------------------------------------------------------------- picture styles and presets */
  const FONTS = [
    ["sans", "Sans", '"Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif'],
    ["inter", "Modern", '"Segoe UI Variable Text", "Inter", "Helvetica Neue", Arial, sans-serif'],
    ["serif", "Serif", 'Georgia, "Times New Roman", Times, serif'],
    ["classic", "Classic", '"Palatino Linotype", "Book Antiqua", Palatino, Georgia, serif'],
    ["mono", "Mono", '"Cascadia Mono", Consolas, "Courier New", monospace'],
    ["condensed", "Condensed", 'Bahnschrift, "Arial Narrow", "Roboto Condensed", sans-serif'],
    ["rounded", "Rounded", '"Nunito", "Segoe UI", "Arial Rounded MT Bold", sans-serif'],
    ["hand", "Handwritten", '"Segoe Print", "Comic Sans MS", "Bradley Hand", cursive'],
  ];
  const fontCss = (id) => (FONTS.find((f) => f[0] === id) || FONTS[0])[2];
  const BASE_STYLE = {
    preset: "minimal", font: "sans", fontSize: 14, cellPadX: 14, cellPadY: 9, padding: 32, radius: 10,
    bgType: "solid", bg1: "#ffffff", bg2: "#eef2ff", gradAngle: 135, bgImage: "", bgDim: 0,
    card: true, cardBg: "#ffffff", cardOpacity: 1, shadow: false,
    text: "#1f2328", muted: "#6b7280", headBg: "#f3f4f6", headText: "#111827",
    border: "#e5e7eb", borderMode: "rows", borderWidth: 1, zebra: false, zebraColor: "#f9fafb",
    headerStyle: "filled", title: "", caption: "", titleColor: "#111827", accent: "#4f46e5",
    scale: 2, maxColWidth: 320, numbersRight: true, lineHeight: 1.35,
  };
  const PRESETS = [
    { id: "minimal", ru: "Минимал", en: "Minimal", s: {} },
    { id: "minimal-dark", ru: "Минимал тёмный", en: "Minimal dark", s: { bg1: "#16181d", cardBg: "#1d2027", text: "#e6e8eb", muted: "#9aa1ad", headBg: "#252932", headText: "#f3f4f6", border: "#2f343e", zebraColor: "#21252c", titleColor: "#f3f4f6" } },
    { id: "clean", ru: "Чистый", en: "Clean", s: { card: false, padding: 24, borderMode: "header", headBg: "transparent", headerStyle: "caps", headText: "#6b7280", fontSize: 14 } },
    { id: "lines", ru: "Линии", en: "Lines", s: { card: false, borderMode: "all", headBg: "#fafafa", radius: 0 } },
    { id: "zebra", ru: "Зебра", en: "Zebra", s: { zebra: true, zebraColor: "#f4f6f8", borderMode: "none", headBg: "#e9edf2" } },
    { id: "github", ru: "GitHub", en: "GitHub", s: { borderMode: "all", border: "#d0d7de", headBg: "#f6f8fa", zebra: true, zebraColor: "#f6f8fa", radius: 6 } },
    { id: "notion", ru: "Notion", en: "Notion", s: { card: false, borderMode: "all", border: "#e9e9e7", headBg: "#ffffff", headText: "#787774", headerStyle: "plain", text: "#37352f", font: "inter", radius: 0 } },
    { id: "excel", ru: "Таблица Excel", en: "Spreadsheet", s: { borderMode: "all", border: "#c6cbd1", headBg: "#217346", headText: "#ffffff", radius: 2, font: "sans", fontSize: 13, cellPadY: 6, cellPadX: 10 } },
    { id: "corporate", ru: "Деловой", en: "Corporate", s: { headBg: "#1e3a8a", headText: "#ffffff", zebra: true, zebraColor: "#eef2ff", border: "#dbe2f1", radius: 8, titleColor: "#1e3a8a" } },
    { id: "newspaper", ru: "Газета", en: "Newspaper", s: { font: "classic", bg1: "#f7f3ea", cardBg: "#f7f3ea", card: false, text: "#1b1b1b", headBg: "transparent", headText: "#1b1b1b", headerStyle: "caps", borderMode: "newspaper", border: "#1b1b1b", titleColor: "#1b1b1b", fontSize: 15 } },
    { id: "paper", ru: "Бумага", en: "Paper", s: { font: "serif", bg1: "#fbf8f1", cardBg: "#fffdf8", border: "#e8dfcc", headBg: "#f3ecdc", headText: "#4a3b22", text: "#3b3428", shadow: true, titleColor: "#4a3b22" } },
    { id: "terminal", ru: "Терминал", en: "Terminal", s: { font: "mono", bg1: "#0b0f0c", cardBg: "#0b0f0c", text: "#7ee787", muted: "#3fb950", headBg: "#0f1a12", headText: "#a5f3a1", border: "#1f3b26", borderMode: "dashed", titleColor: "#7ee787", radius: 4, fontSize: 13, headerStyle: "caps" } },
    { id: "pastel", ru: "Пастель", en: "Pastel", s: { bgType: "gradient", bg1: "#fde2f3", bg2: "#e0f2fe", gradAngle: 135, cardBg: "#ffffff", cardOpacity: 0.9, headBg: "#fbe7ff", headText: "#6b21a8", zebra: true, zebraColor: "#f8fafc", border: "#f1e4f7", radius: 16, shadow: true, font: "rounded", titleColor: "#6b21a8" } },
    { id: "sunset", ru: "Закат", en: "Sunset", s: { bgType: "gradient", bg1: "#ff7e5f", bg2: "#feb47b", gradAngle: 135, cardBg: "#ffffff", headBg: "#fff1e9", headText: "#9a3412", border: "#fde3d3", radius: 16, shadow: true, titleColor: "#ffffff" } },
    { id: "ocean", ru: "Океан", en: "Ocean", s: { bgType: "gradient", bg1: "#0ea5e9", bg2: "#6366f1", gradAngle: 135, cardBg: "#ffffff", headBg: "#eef6ff", headText: "#1e40af", border: "#dbeafe", radius: 16, shadow: true, titleColor: "#ffffff" } },
    { id: "glass", ru: "Стекло", en: "Glass", s: { bgType: "gradient", bg1: "#8b5cf6", bg2: "#06b6d4", gradAngle: 120, cardBg: "#ffffff", cardOpacity: 0.18, text: "#ffffff", muted: "#e0e7ff", headBg: "rgba(255,255,255,0.16)", headText: "#ffffff", border: "rgba(255,255,255,0.28)", radius: 18, shadow: true, titleColor: "#ffffff", glass: true } },
    { id: "midnight", ru: "Полночь", en: "Midnight", s: { bgType: "gradient", bg1: "#0f172a", bg2: "#312e81", gradAngle: 160, cardBg: "#111827", cardOpacity: 0.85, text: "#e5e7eb", headBg: "#1f2937", headText: "#c7d2fe", border: "#334155", zebra: true, zebraColor: "#162033", radius: 14, shadow: true, titleColor: "#e0e7ff" } },
    { id: "neon", ru: "Неон", en: "Neon", s: { bg1: "#0a0a12", cardBg: "#0f0f1a", text: "#f0abfc", headBg: "#1a1030", headText: "#22d3ee", border: "#3b2a5c", borderMode: "all", radius: 10, font: "mono", titleColor: "#22d3ee", glow: true } },
    { id: "blueprint", ru: "Чертёж", en: "Blueprint", s: { bg1: "#1e4b8f", cardBg: "#1e4b8f", card: false, text: "#e6f0ff", headBg: "transparent", headText: "#ffffff", border: "#8fb3e8", borderMode: "all", font: "mono", headerStyle: "caps", titleColor: "#ffffff", grid: true } },
    { id: "mint", ru: "Мята", en: "Mint", s: { bg1: "#ecfdf5", cardBg: "#ffffff", headBg: "#d1fae5", headText: "#065f46", border: "#c6f0dc", zebra: true, zebraColor: "#f3fcf8", radius: 14, titleColor: "#065f46" } },
    { id: "print", ru: "Для печати", en: "Print", s: { bg1: "#ffffff", card: false, borderMode: "all", border: "#000000", headBg: "#ffffff", headText: "#000000", text: "#000000", radius: 0, font: "serif", headerStyle: "bold" } },
  ];
  function presetStyle(id, extra) {
    const preset = PRESETS.find((p) => p.id === id) || PRESETS[0];
    return { ...BASE_STYLE, ...preset.s, preset: preset.id, ...(extra || {}) };
  }
  function autoPreset() {
    const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia?.("(prefers-color-scheme: dark)").matches);
    return dark ? "minimal-dark" : "minimal";
  }
  const STYLE_KEY = "memu_table_image_style";
  function loadStyle() {
    try { const s = JSON.parse(localStorage.getItem(STYLE_KEY) || "null"); if (s && typeof s === "object") return { ...BASE_STYLE, ...s, bgImage: "" }; } catch (e) { /* default */ }
    return presetStyle(autoPreset());
  }
  function saveStyle(style) {
    try { const { bgImage, title, caption, ...rest } = style; localStorage.setItem(STYLE_KEY, JSON.stringify(rest)); } catch (e) { /* quota */ }
  }

  /* ---------------------------------------------------------------- SVG renderer (offline, measured with canvas) */
  let measureCtx = null;
  function measurer(font) {
    if (!measureCtx) measureCtx = document.createElement("canvas").getContext("2d");
    measureCtx.font = font;
    return (text) => measureCtx.measureText(String(text)).width;
  }
  function wrapText(text, maxWidth, measure) {
    const words = String(text || "").split(/(\s+)/).filter((w) => w.length);
    const lines = [];
    let line = "";
    for (const word of words) {
      const next = line + word;
      if (measure(next.trimEnd()) <= maxWidth || !line.trim()) {
        if (measure(word) > maxWidth && !line.trim()) {
          // one very long word: break by characters
          let chunk = "";
          for (const ch of word) { if (measure(chunk + ch) > maxWidth && chunk) { lines.push(chunk); chunk = ch; } else chunk += ch; }
          line = chunk;
        } else line = next;
      } else { lines.push(line.trimEnd()); line = word.trimStart(); }
    }
    if (line.trim() || !lines.length) lines.push(line.trimEnd());
    return lines;
  }
  function svgAttr(v) { return esc(v); }
  function renderSVG(input, styleIn) {
    const d = normalize(input && input.headers ? input : fromTable(input));
    const s = { ...BASE_STYLE, ...(styleIn || {}) };
    const fam = fontCss(s.font);
    const fs = Math.max(8, Math.min(40, +s.fontSize || 14));
    const lh = Math.round(fs * (s.lineHeight || 1.35));
    const caps = s.headerStyle === "caps";
    const headFont = `${s.headerStyle === "plain" ? 500 : 650} ${caps ? Math.round(fs * 0.82) : fs}px ${fam}`;
    const bodyFont = `400 ${fs}px ${fam}`;
    const mHead = measurer(headFont), mBody = measurer(bodyFont);
    const px = +s.cellPadX, py = +s.cellPadY, maxW = Math.max(60, +s.maxColWidth || 320);
    const kinds = d.headers.map((_, i) => columnKind(d.rows, i));
    const headerText = d.headers.map((h) => caps ? h.toUpperCase() : h);
    const colW = d.headers.map((_, i) => {
      const widths = [mHead(headerText[i]) + (caps ? headerText[i].length * fs * 0.06 : 0), ...d.rows.map((r) => mBody(r[i]))];
      return Math.ceil(Math.min(maxW, Math.max(24, ...widths)));
    });
    const align = d.headers.map((_, i) => d.align[i] || (s.numbersRight && kinds[i] === "number" ? "right" : "left"));
    const headLines = headerText.map((h, i) => wrapText(h, colW[i], mHead));
    const bodyLines = d.rows.map((r) => r.map((c, i) => wrapText(c, colW[i], mBody)));
    const headH = Math.max(1, ...headLines.map((l) => l.length)) * lh + py * 2;
    const rowH = bodyLines.map((r) => Math.max(1, ...r.map((l) => l.length)) * lh + py * 2);
    const tableW = colW.reduce((a, w) => a + w + px * 2, 0);
    const tableH = headH + rowH.reduce((a, h) => a + h, 0);
    const pad = Math.max(0, +s.padding || 0);
    const title = String(s.title ?? d.title ?? "").trim();
    const caption = String(s.caption ?? d.caption ?? "").trim();
    const titleFs = Math.round(fs * 1.35);
    const titleH = title ? titleFs * 1.3 + 12 : 0;
    const capH = caption ? fs * 1.3 + 10 : 0;
    const W = Math.ceil(tableW + pad * 2), H = Math.ceil(tableH + pad * 2 + titleH + capH);
    const tx = pad, ty = pad + titleH;
    const r = Math.max(0, +s.radius || 0);
    const defs = [];
    let bg = "";
    if (s.bgType === "solid") bg = `<rect width="${W}" height="${H}" fill="${svgAttr(s.bg1)}"/>`;
    else if (s.bgType === "gradient") {
      const a = ((+s.gradAngle || 0) - 90) * Math.PI / 180;
      const x1 = 50 - Math.cos(a) * 50, y1 = 50 - Math.sin(a) * 50, x2 = 50 + Math.cos(a) * 50, y2 = 50 + Math.sin(a) * 50;
      defs.push(`<linearGradient id="mtbg" x1="${x1.toFixed(1)}%" y1="${y1.toFixed(1)}%" x2="${x2.toFixed(1)}%" y2="${y2.toFixed(1)}%"><stop offset="0" stop-color="${svgAttr(s.bg1)}"/><stop offset="1" stop-color="${svgAttr(s.bg2)}"/></linearGradient>`);
      bg = `<rect width="${W}" height="${H}" fill="url(#mtbg)"/>`;
    } else if (s.bgType === "image" && s.bgImage) {
      bg = `<rect width="${W}" height="${H}" fill="${svgAttr(s.bg1)}"/><image href="${svgAttr(s.bgImage)}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="xMidYMid slice"/>` +
        (+s.bgDim ? `<rect width="${W}" height="${H}" fill="#000" opacity="${Math.min(0.85, +s.bgDim)}"/>` : "");
    }
    if (s.grid) {
      defs.push(`<pattern id="mtgrid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#ffffff" stroke-opacity=".12" stroke-width="1"/></pattern>`);
      bg += `<rect width="${W}" height="${H}" fill="url(#mtgrid)"/>`;
    }
    defs.push(`<clipPath id="mtclip"><rect x="${tx}" y="${ty}" width="${tableW}" height="${tableH}" rx="${r}"/></clipPath>`);
    if (s.shadow) defs.push(`<filter id="mtsh" x="-20%" y="-20%" width="140%" height="160%"><feDropShadow dx="0" dy="${Math.round(fs * 0.5)}" stdDeviation="${Math.round(fs * 0.9)}" flood-color="#0f172a" flood-opacity=".18"/></filter>`);
    if (s.glow) defs.push(`<filter id="mtglow"><feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`);
    const parts = [];
    if (s.card) parts.push(`<rect x="${tx}" y="${ty}" width="${tableW}" height="${tableH}" rx="${r}" fill="${svgAttr(s.cardBg)}" fill-opacity="${+s.cardOpacity}"${s.shadow ? ' filter="url(#mtsh)"' : ""}/>`);
    const g = [];
    if (s.headBg && s.headBg !== "transparent") g.push(`<rect x="${tx}" y="${ty}" width="${tableW}" height="${headH}" fill="${svgAttr(s.headBg)}"/>`);
    let y = ty + headH;
    rowH.forEach((h, i) => {
      if (s.zebra && i % 2 === 1) g.push(`<rect x="${tx}" y="${y}" width="${tableW}" height="${h}" fill="${svgAttr(s.zebraColor)}"/>`);
      y += h;
    });
    const bw = Math.max(0, +s.borderWidth || 1);
    const line = (x1, y1, x2, y2, extra = "", width = bw) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${svgAttr(s.border)}" stroke-width="${width}"${extra}/>`;
    const mode = s.borderMode;
    const dash = mode === "dashed" ? ' stroke-dasharray="4 4"' : "";
    // horizontal lines
    y = ty + headH;
    if (mode !== "none") g.push(line(tx, y, tx + tableW, y, dash, mode === "newspaper" ? bw * 1.5 : bw));
    if (["rows", "all", "dashed", "newspaper"].includes(mode)) {
      rowH.slice(0, -1).forEach((h) => { y += h; g.push(line(tx, y, tx + tableW, y, dash + (mode === "newspaper" ? ' stroke-opacity=".35"' : ""))); });
    }
    if (mode === "all" || mode === "dashed") {
      let x = tx;
      colW.slice(0, -1).forEach((w) => { x += w + px * 2; g.push(line(x, ty, x, ty + tableH, dash)); });
    }
    parts.push(`<g clip-path="url(#mtclip)">${g.join("")}</g>`);
    if (mode === "newspaper") {
      parts.push(line(tx, ty, tx + tableW, ty, "", bw * 3), line(tx, ty + tableH, tx + tableW, ty + tableH, "", bw * 2));
    } else if (mode === "all" || mode === "outer" || mode === "dashed" || (s.card && mode !== "none" && mode !== "header")) {
      parts.push(`<rect x="${tx + bw / 2}" y="${ty + bw / 2}" width="${tableW - bw}" height="${tableH - bw}" rx="${Math.max(0, r - bw / 2)}" fill="none" stroke="${svgAttr(s.border)}" stroke-width="${bw}"${dash}/>`);
    }
    // text
    const textEl = (lines, x, yTop, w, anchorAlign, fill, font, weight, extra = "") => {
      const anchor = anchorAlign === "right" ? "end" : anchorAlign === "center" ? "middle" : "start";
      const ax = anchorAlign === "right" ? x + w : anchorAlign === "center" ? x + w / 2 : x;
      return `<text x="${ax}" y="${yTop}" fill="${svgAttr(fill)}" font-family="${svgAttr(fam)}" font-size="${font}" font-weight="${weight}" text-anchor="${anchor}"${extra}>` +
        lines.map((l, k) => `<tspan x="${ax}" dy="${k ? lh : 0}">${esc(l)}</tspan>`).join("") + "</text>";
    };
    const base = (lh - fs) / 2 + fs * 0.8;
    const glow = s.glow ? ' filter="url(#mtglow)"' : "";
    let x = tx;
    headLines.forEach((lines, i) => {
      const headWeight = s.headerStyle === "plain" ? 500 : 650;
      parts.push(textEl(lines, x + px, ty + py + base, colW[i], align[i], s.headText, caps ? Math.round(fs * 0.82) : fs, headWeight,
        (caps ? ` letter-spacing="${(fs * 0.06).toFixed(2)}"` : "") + glow));
      if (s.headerStyle === "underline") parts.push(`<line x1="${x + px}" y1="${ty + headH - py / 2}" x2="${x + px + Math.min(colW[i], mHead(headerText[i]))}" y2="${ty + headH - py / 2}" stroke="${svgAttr(s.accent)}" stroke-width="2"/>`);
      x += colW[i] + px * 2;
    });
    y = ty + headH;
    bodyLines.forEach((row, ri) => {
      x = tx;
      row.forEach((lines, i) => {
        parts.push(textEl(lines, x + px, y + py + base, colW[i], align[i], s.text, fs, 400, kinds[i] === "number" ? ' font-variant-numeric="tabular-nums"' : ""));
        x += colW[i] + px * 2;
      });
      y += rowH[ri];
    });
    if (title) parts.push(`<text x="${tx}" y="${pad + titleFs}" fill="${svgAttr(s.titleColor)}" font-family="${svgAttr(fam)}" font-size="${titleFs}" font-weight="700">${esc(title)}</text>`);
    if (caption) parts.push(`<text x="${tx}" y="${ty + tableH + 10 + fs}" fill="${svgAttr(s.bgType === "gradient" || s.bgType === "image" ? s.titleColor : s.muted)}" font-family="${svgAttr(fam)}" font-size="${Math.round(fs * 0.86)}" opacity=".85">${esc(caption)}</text>`);
    return { svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs>${defs.join("")}</defs>${bg}${parts.join("")}</svg>`, width: W, height: H };
  }

  async function svgToPngBlob(svg, width, height, scale = 2) {
    const img = new Image();
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
    try {
      await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = () => reject(new Error(t("Не удалось нарисовать картинку", "Could not draw the image"))); img.src = url; });
      const canvas = document.createElement("canvas");
      const k = Math.max(1, Math.min(4, +scale || 2));
      canvas.width = Math.round(width * k); canvas.height = Math.round(height * k);
      if (canvas.width * canvas.height > 120e6) throw new Error(t("Картинка слишком большая — уменьшите масштаб", "The image is too large — lower the scale"));
      const ctx = canvas.getContext("2d");
      ctx.scale(k, k); ctx.drawImage(img, 0, 0, width, height);
      return await new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG"))), "image/png"));
    } finally { URL.revokeObjectURL(url); }
  }
  function toSVG(input, style) { return renderSVG(input, style || presetStyle(autoPreset())).svg; }
  async function toPNG(input, opts = {}) {
    const style = opts.style || presetStyle(opts.preset || (opts.theme === "dark" ? "minimal-dark" : opts.theme === "light" ? "minimal" : autoPreset()));
    const out = renderSVG(input, style);
    const blob = await svgToPngBlob(out.svg, out.width, out.height, opts.scale || style.scale || 2);
    return { src: await blobToDataUrl(blob), w: out.width, h: out.height, blob };
  }

  /* ---------------------------------------------------------------- streaming placeholder */
  function fenceOpen(text) { return ((String(text).match(/^[ \t]*```/gm) || []).length % 2) === 1; }
  function maskPending(text) {
    const value = String(text || "");
    if (!value.includes("|")) return value;
    const endsWithNewline = /\n$/.test(value);
    const lines = value.split("\n");
    if (endsWithNewline) lines.pop();
    let start = lines.length;
    while (start > 0 && /^[ \t]*\|/.test(lines[start - 1])) start -= 1;
    if (start === lines.length) return value;
    const before = lines.slice(0, start).join("\n");
    if (fenceOpen(before)) return value;          // the pipes are inside a code block
    const block = lines.slice(start);
    const sep = block.findIndex((l) => SEP_RE.test(l.trim()) && l.includes("-"));
    const rows = sep >= 0 ? block.slice(sep + 1).filter((l, k, all) => /\|[ \t]*$/.test(l) || k < all.length - 1).length : 0;
    const cols = block.length ? Math.max(1, splitRow(block[0]).length) : 0;
    return (before ? before + "\n" : "") + "\n```memuai-table-pending\n" + rows + " " + cols + "\n```\n";
  }
  function pending(rows, en, cols) {
    const n = Math.max(0, +rows || 0), c = Math.max(0, +cols || 0);
    const label = en ? "Building the table…" : "Таблица формируется…";
    const count = n ? `${n} ${en ? (n === 1 ? "row" : "rows") : plural(n, "строка", "строки", "строк")}` : (en ? "header" : "заголовок");
    const bars = Array.from({ length: Math.min(6, Math.max(2, n + 1)) }, (_, i) => `<i style="--w:${70 + ((i * 37) % 30)}%"></i>`).join("");
    return `<div class="mt-pending" role="status" aria-live="polite"><div class="mt-pending-head">${ic("table")}<span>${label}</span><b>${count}${c ? ` · ${c} ${en ? (c === 1 ? "column" : "columns") : plural(c, "столбец", "столбца", "столбцов")}` : ""}</b></div><div class="mt-pending-rows">${bars}</div></div>`;
  }
  // app.js / quick-md.js code-fence hook: returns HTML for our own fences or "" for the rest.
  function block(lang, code, en) {
    if (lang === "memuai-table-pending") {
      const [rows, cols] = String(code || "").trim().split(/\s+/).map(Number);
      return pending(rows, en, cols);
    }
    return "";
  }

  /* ---------------------------------------------------------------- small popover menu */
  let openMenu = null;
  function closeMenu() {
    if (!openMenu) return;
    openMenu.remove(); openMenu = null;
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("keydown", onMenuKey, true);
  }
  function onOutside(e) { if (openMenu && !openMenu.contains(e.target)) closeMenu(); }
  function onMenuKey(e) {
    if (!openMenu) return;
    const items = [...openMenu.querySelectorAll("button:not([disabled])")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
  }
  function showMenu(anchor, items) {
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "mt-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = items.map((it, i) => it.section ? `<div class="mt-menu-sec">${esc(it.section)}</div>`
      : `<button type="button" role="menuitem" data-i="${i}">${it.icon ? ic(it.icon) : ""}<span>${esc(it.label)}</span>${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</button>`).join("");
    document.body.appendChild(menu);
    const box = anchor.getBoundingClientRect();
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    let left = Math.min(window.innerWidth - mw - 8, Math.max(8, box.right - mw));
    let top = box.bottom + 6;
    if (top + mh > window.innerHeight - 8) top = Math.max(8, box.top - mh - 6);
    menu.style.left = left + "px"; menu.style.top = top + "px";
    menu.querySelectorAll("button[data-i]").forEach((b) => b.onclick = async () => {
      const it = items[+b.dataset.i]; closeMenu();
      try { await it.run(); } catch (err) { notify(err.message || String(err), "warn"); }
    });
    openMenu = menu;
    setTimeout(() => {
      document.addEventListener("pointerdown", onOutside, true);
      document.addEventListener("keydown", onMenuKey, true);
      menu.querySelector("button")?.focus();
    }, 0);
  }

  /* ---------------------------------------------------------------- enhanced rendered tables */
  const tableState = new Map();     // signature -> { sort, dir, filter, widths }
  function signature(table) {
    // Round 34 (r34-quick-core): textContent only — the layout-aware text getter forces a layout per table, and
    // hydrating an answer with 100+ tables took 14 s of layout thrashing (Quick Ask and the main chat froze).
    const text = (table.tHead?.textContent || "") + "|" + (table.tBodies?.[0]?.rows.length || 0) + "|" + (table.tBodies?.[0]?.rows?.[0]?.textContent || "");
    let h = 0;
    for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) | 0;
    const msg = table.closest("[data-msg], [data-turn]");
    return (msg ? (msg.dataset.msg ?? "q" + msg.dataset.turn) : "") + ":" + h;
  }
  function stateOf(table) {
    const key = signature(table);
    if (!tableState.has(key)) {
      tableState.set(key, { sort: -1, dir: 1, filter: "", widths: {} });
      if (tableState.size > 300) tableState.delete(tableState.keys().next().value);
    }
    return tableState.get(key);
  }
  function copyActions(getData) {
    return [
      { icon: "copy", label: t("Markdown", "Markdown"), hint: t("для чата и заметок", "for chats and notes"), run: async () => done(await copyText(toMarkdown(getData()))) },
      { icon: "copy", label: t("Для Excel (TSV)", "For Excel (TSV)"), hint: t("вставьте в ячейку A1", "paste into cell A1"), run: async () => done(await copyText(toTSV(getData()), toHTML(getData()))) },
      { icon: "copy", label: "CSV", run: async () => done(await copyText(toCSV(getData()))) },
      { icon: "copy", label: "HTML", hint: t("для Word и писем", "for Word and email"), run: async () => done(await copyText(toHTML(getData()), toHTML(getData()))) },
      { icon: "image", label: t("Как картинку", "As an image"), run: async () => { const out = await toPNG(getData(), { style: loadStyle() }); await copyImageBlob(out.blob); done(true, t("Картинка скопирована", "Image copied")); } },
    ];
  }
  function done(ok, text) { notify(ok ? (text || t("Скопировано", "Copied")) : t("Не удалось скопировать", "Could not copy"), ok ? "ok" : "warn"); }
  function exportActions(getData) {
    const base = () => safeName(getData().title || t("таблица", "table"));
    return [
      { icon: "download", label: "CSV", hint: t("Excel, Google Таблицы", "Excel, Google Sheets"), run: () => saveFile(base() + ".csv", toCSV(getData(), { bom: true }), "text/csv;charset=utf-8") },
      { icon: "download", label: "Excel (XLSX)", run: () => saveFile(base() + ".xlsx", toXLSX(getData())) },
      { icon: "download", label: "Markdown", run: () => saveFile(base() + ".md", toMarkdown(getData()) + "\n", "text/markdown;charset=utf-8") },
      { icon: "download", label: "JSON", run: () => saveFile(base() + ".json", toJSON(getData()), "application/json") },
      { icon: "download", label: "HTML", run: () => saveFile(base() + ".html", toHTML(getData(), { standalone: true }), "text/html;charset=utf-8") },
      { icon: "image", label: "PNG", hint: t("в последнем стиле", "in the last style"), run: async () => { const out = await toPNG(getData(), { style: loadStyle() }); await saveFile(base() + ".png", out.blob); } },
      { icon: "image", label: "SVG", run: () => saveFile(base() + ".svg", renderSVG(getData(), loadStyle()).svg, "image/svg+xml") },
    ];
  }
  function applyState(host, table, st) {
    const body = table.tBodies?.[0];
    if (!body) return;
    if (!host._mtOrder) host._mtOrder = [...body.rows];
    const original = host._mtOrder;
    const ths = [...(table.tHead?.rows?.[0]?.cells || [])];
    let rows = original.slice();
    if (st.sort >= 0) {
      const kind = columnKind(rows.map((r) => [...r.cells].map((c) => c.innerText)), st.sort);
      rows.sort((a, b) => compareValues(a.cells[st.sort]?.innerText, b.cells[st.sort]?.innerText, kind) * st.dir);
    }
    rows.forEach((r) => body.appendChild(r));
    const q = st.filter.trim().toLowerCase();
    let shown = 0;
    const terms = q ? q.split(/\s+/) : [];
    rows.forEach((r) => {
      if (!terms.length) { r.hidden = false; shown += 1; return; }   // Round 34: no innerText (a forced layout per row) without a filter
      const text = r.innerText.toLowerCase();
      const ok = terms.every((term) => {
        const m = term.match(/^(.+?)([:=<>])(.+)$/);
        if (m) {
          const col = ths.findIndex((th) => th.innerText.trim().toLowerCase().startsWith(m[1]));
          if (col >= 0) {
            const cell = (r.cells[col]?.innerText || "").toLowerCase();
            if (m[2] === ":" || m[2] === "=") return m[2] === "=" ? cell.trim() === m[3] : cell.includes(m[3]);
            const a = parseNumber(cell), b = parseNumber(m[3]);
            return a !== null && b !== null && (m[2] === ">" ? a > b : a < b);
          }
        }
        return text.includes(term);
      });
      r.hidden = !ok;
      if (ok) shown += 1;
    });
    ths.forEach((th, i) => {
      th.classList.toggle("mt-sorted", st.sort === i);
      th.setAttribute("aria-sort", st.sort === i ? (st.dir > 0 ? "ascending" : "descending") : "none");
      const arrow = th.querySelector(".mt-sort");
      if (arrow) arrow.innerHTML = st.sort === i ? (st.dir > 0 ? IC.up : IC.down) : IC.sort;
      if (st.widths[i]) { th.style.width = st.widths[i] + "px"; th.style.minWidth = st.widths[i] + "px"; th.style.maxWidth = st.widths[i] + "px"; th.style.whiteSpace = "normal"; }
    });
    const foot = host.querySelector(".mt-foot");
    if (foot) {
      foot.hidden = !q;
      foot.textContent = q ? t(`Показано ${shown} из ${original.length}`, `Showing ${shown} of ${original.length}`) : "";
    }
  }
  function enhance(wrap, opts) {
    const table = wrap.querySelector("table");
    if (!table || wrap.dataset.mtReady) return;
    wrap.dataset.mtReady = "1";
    const host = document.createElement("div");
    host.className = "mt-host" + (opts.compact ? " compact" : "");
    wrap.parentNode.insertBefore(host, wrap);
    host.appendChild(wrap);
    const st = stateOf(table);
    const rowsCount = table.tBodies?.[0]?.rows.length || 0;
    const getData = () => fromTable(table);
    const bar = document.createElement("div");
    bar.className = "mt-bar";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", t("Действия с таблицей", "Table actions"));
    const btn = (id, icon, label) => `<button type="button" class="mt-btn" data-mt="${id}" title="${esc(label)}" aria-label="${esc(label)}">${ic(icon)}</button>`;
    bar.innerHTML = (rowsCount > 3 ? btn("filter", "filter", t("Фильтр", "Filter")) : "") +
      btn("copy", "copy", t("Копировать", "Copy")) + btn("export", "download", t("Экспорт", "Export")) +
      (opts.compact ? "" : btn("image", "image", t("Картинкой…", "As an image…"))) +
      (opts.compact ? "" : btn("full", "full", t("На весь экран", "Fullscreen"))) +
      (opts.onSendBack ? btn("edit", "edit", t("Изменить и отправить ИИ", "Edit and send back to the AI")) : "");
    host.insertBefore(bar, wrap);
    const filter = document.createElement("div");
    filter.className = "mt-filter";
    filter.hidden = !st.filter;
    filter.innerHTML = `${ic("filter")}<input type="search" spellcheck="false" placeholder="${esc(t("Фильтр строк… (например: цена>100)", "Filter rows… (e.g. price>100)"))}" value="${esc(st.filter)}"><button type="button" class="mt-btn" data-mt="filter-close" aria-label="${esc(t("Закрыть фильтр", "Close filter"))}">${ic("close")}</button>`;
    host.insertBefore(filter, wrap);
    const foot = document.createElement("div");
    foot.className = "mt-foot"; foot.hidden = true;
    host.appendChild(foot);
    const input = filter.querySelector("input");
    input.oninput = () => { st.filter = input.value; applyState(host, table, st); };
    input.onkeydown = (e) => { if (e.key === "Escape") { st.filter = ""; input.value = ""; filter.hidden = true; applyState(host, table, st); } };
    // header: sort + resize
    const ths = [...(table.tHead?.rows?.[0]?.cells || [])];
    ths.forEach((th, i) => {
      if (rowsCount > 1) {
        th.classList.add("mt-sortable");
        th.tabIndex = 0;
        th.insertAdjacentHTML("beforeend", `<span class="mt-sort" aria-hidden="true">${IC.sort}</span>`);
        const sort = (e) => {
          if (e.target.closest(".mt-resize")) return;
          if (st.sort !== i) { st.sort = i; st.dir = 1; } else if (st.dir > 0) st.dir = -1; else { st.sort = -1; st.dir = 1; }
          applyState(host, table, st);
        };
        th.addEventListener("click", sort);
        th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(e); } });
      }
      if (i < ths.length - 1 || ths.length > 1) {
        const handle = document.createElement("span");
        handle.className = "mt-resize";
        handle.title = t("Потяните, чтобы изменить ширину; двойной щелчок — сброс", "Drag to resize; double-click to reset");
        handle.addEventListener("pointerdown", (e) => {
          e.preventDefault(); e.stopPropagation();
          const startX = e.clientX, startW = th.getBoundingClientRect().width;
          handle.setPointerCapture(e.pointerId);
          host.classList.add("mt-resizing");
          const move = (ev) => {
            const w = Math.max(48, Math.round(startW + ev.clientX - startX));
            st.widths[i] = w; th.style.width = w + "px"; th.style.minWidth = w + "px"; th.style.maxWidth = w + "px"; th.style.whiteSpace = "normal";
          };
          const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); host.classList.remove("mt-resizing"); };
          handle.addEventListener("pointermove", move);
          handle.addEventListener("pointerup", up);
        });
        handle.addEventListener("dblclick", (e) => { e.stopPropagation(); delete st.widths[i]; th.style.width = th.style.minWidth = th.style.maxWidth = th.style.whiteSpace = ""; });
        handle.addEventListener("click", (e) => e.stopPropagation());
        th.appendChild(handle);
      }
    });
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-mt]");
      if (!b) return;
      const what = b.dataset.mt;
      if (what === "filter") { filter.hidden = !filter.hidden; if (!filter.hidden) input.focus(); else { st.filter = ""; input.value = ""; applyState(host, table, st); } }
      else if (what === "copy") showMenu(b, [{ section: t("Копировать как", "Copy as") }, ...copyActions(getData)]);
      else if (what === "export") showMenu(b, [{ section: t("Сохранить как", "Save as") }, ...exportActions(getData)]);
      else if (what === "image") openStudio(getData());
      else if (what === "full") openFullscreen(table, opts);
      else if (what === "edit") openEditor({ data: getData(), mode: "sendback", onSendBack: opts.onSendBack });
    });
    filter.querySelector("[data-mt='filter-close']").onclick = () => { st.filter = ""; input.value = ""; filter.hidden = true; applyState(host, table, st); };
    if (st.sort >= 0 || st.filter || Object.keys(st.widths).length) applyState(host, table, st);
  }
  function defaultSendBack(md) {
    const input = document.getElementById("input");
    if (!input) return false;
    const intro = t("Я изменил таблицу — учти правки:", "I edited the table — take my changes into account:");
    input.value = (input.value.trim() ? input.value.trim() + "\n\n" : intro + "\n\n") + md + "\n";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange?.(input.value.length, input.value.length);
    return true;
  }
  function hydrate(root, isLive, opts = {}) {
    const scope = root || document;
    // Chat export (export.js) asks for «rich tables»: there the table must stay plain HTML.
    if (scope.querySelector?.(".mx-content") || scope.closest?.(".mx-content")) {
      scope.querySelectorAll(".mt-bar, .mt-filter, .mt-foot, .mt-sort, .mt-resize").forEach((n) => n.remove());
      return;
    }
    registerIcons();
    const options = { compact: !!opts.compact, onSendBack: opts.onSendBack === undefined ? (document.getElementById("input") ? defaultSendBack : null) : opts.onSendBack };
    scope.querySelectorAll(".table-wrap:not([data-mt-ready])").forEach((wrap) => {
      if (wrap.closest(".mt-editor, .mt-studio, .mt-pending")) return;
      try { enhance(wrap, options); } catch (e) { console.warn("MemuTables", e); }
    });
  }

  /* ---------------------------------------------------------------- modal shell */
  function modal(cls, title, bodyHtml, { wide = false, onClose } = {}) {
    const back = document.createElement("div");
    back.className = "mt-modal-back";
    back.innerHTML = `<div class="mt-modal ${cls}${wide ? " wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="mt-modal-head"><b>${esc(title)}</b><button type="button" class="mt-btn" data-mt-close aria-label="${esc(t("Закрыть", "Close"))}">${ic("close")}</button></div>
      <div class="mt-modal-body">${bodyHtml}</div></div>`;
    document.body.appendChild(back);
    const prevFocus = document.activeElement;
    const close = () => { back.remove(); document.removeEventListener("keydown", key, true); onClose?.(); try { prevFocus?.focus?.(); } catch (e) { /* gone */ } };
    const key = (e) => { if (e.key === "Escape" && !openMenu && document.body.lastElementChild === back) { e.preventDefault(); e.stopPropagation(); close(); } };
    document.addEventListener("keydown", key, true);
    back.addEventListener("pointerdown", (e) => { if (e.target === back) back._downOutside = true; });
    back.addEventListener("click", (e) => { if (e.target === back && back._downOutside) close(); back._downOutside = false; });
    back.querySelector("[data-mt-close]").onclick = close;
    requestAnimationFrame(() => back.classList.add("open"));
    return { el: back.querySelector(".mt-modal"), back, close };
  }

  function openFullscreen(table, opts) {
    const data = fromTable(table, {});
    const m = modal("mt-full", data.title || t("Таблица", "Table"), `<div class="mt-full-scroll"><div class="table-wrap">${table.outerHTML}</div></div>`, { wide: true });
    const wrap = m.el.querySelector(".table-wrap");
    wrap.querySelectorAll(".mt-sort, .mt-resize").forEach((n) => n.remove());
    wrap.querySelectorAll("th").forEach((th) => { th.classList.remove("mt-sortable", "mt-sorted"); th.removeAttribute("tabindex"); th.style.width = th.style.minWidth = th.style.maxWidth = ""; });
    wrap.querySelectorAll("tr[hidden]").forEach((r) => (r.hidden = false));
    enhance(wrap, { ...opts, compact: false, fullscreen: true });
    m.el.querySelector(".mt-full .mt-bar [data-mt='full']")?.remove();
  }

  /* ---------------------------------------------------------------- table editor */
  function openEditor({ data, mode = "compose", onSendBack, onDone } = {}) {
    registerIcons();
    let d = normalize(data && (data.headers?.length || data.rows?.length) ? JSON.parse(JSON.stringify(data)) : { headers: ["", "", ""], rows: [["", "", ""], ["", "", ""], ["", "", ""]] });
    if (!data) d.headers = d.headers.map((_, i) => t("Столбец ", "Column ") + (i + 1));
    let headerOn = true;
    let focus = { r: 0, c: 0 };
    const history = [];
    const pushHistory = () => { history.push(JSON.stringify(d)); if (history.length > 80) history.shift(); };
    const sendback = mode === "sendback";
    const title = sendback ? t("Изменить таблицу и отправить ИИ", "Edit the table and send it back") : t("Таблица для сообщения", "Table for the message");
    const m = modal("mt-editor", title, `
      <div class="mt-ed-tools">
        <button type="button" class="mt-tb" data-ed="row+">${ic("plus")}<span>${t("Строка", "Row")}</span></button>
        <button type="button" class="mt-tb" data-ed="col+">${ic("plus")}<span>${t("Столбец", "Column")}</span></button>
        <button type="button" class="mt-tb" data-ed="row-" title="${esc(t("Удалить строку с курсором", "Delete the current row"))}">${ic("minus")}<span>${t("Строка", "Row")}</span></button>
        <button type="button" class="mt-tb" data-ed="col-" title="${esc(t("Удалить столбец с курсором", "Delete the current column"))}">${ic("minus")}<span>${t("Столбец", "Column")}</span></button>
        <span class="mt-ed-sep"></span>
        <button type="button" class="mt-tb icon" data-ed="al-left" title="${esc(t("По левому краю", "Align left"))}">${ic("alignL")}</button>
        <button type="button" class="mt-tb icon" data-ed="al-center" title="${esc(t("По центру", "Align center"))}">${ic("alignC")}</button>
        <button type="button" class="mt-tb icon" data-ed="al-right" title="${esc(t("По правому краю", "Align right"))}">${ic("alignR")}</button>
        <button type="button" class="mt-tb icon" data-ed="transpose" title="${esc(t("Поменять строки и столбцы", "Swap rows and columns"))}">${ic("transpose")}</button>
        <span class="mt-ed-sep"></span>
        <button type="button" class="mt-tb" data-ed="paste">${ic("paste")}<span>${t("Вставить из Excel/CSV", "Paste from Excel/CSV")}</span></button>
        <button type="button" class="mt-tb" data-ed="import">${ic("upload")}<span>${t("Импорт файла", "Import a file")}</span></button>
        <button type="button" class="mt-tb" data-ed="new">${ic("table")}<span>${t("Новая сетка", "New grid")}</span></button>
        <button type="button" class="mt-tb icon" data-ed="clear" title="${esc(t("Очистить", "Clear"))}">${ic("trash")}</button>
        <input type="file" hidden accept=".csv,.tsv,.txt,.md,.markdown,.json,.xlsx,text/csv,text/plain,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
      </div>
      <div class="mt-ed-meta"><input type="text" class="mt-ed-title" maxlength="140" placeholder="${esc(t("Заголовок таблицы (необязательно)", "Table title (optional)"))}" value="${esc(d.title)}">
        <label class="mt-check"><input type="checkbox" data-ed="header" checked> ${t("Первая строка — заголовки", "First row is the header")}</label></div>
      <div class="mt-ed-grid-wrap"><div class="mt-ed-grid" role="grid"></div></div>
      <div class="mt-ed-hint">${t("Tab / Enter — следующая ячейка · Ctrl+V — вставка диапазона из Excel · Ctrl+Z — отменить", "Tab / Enter — next cell · Ctrl+V — paste a range from Excel · Ctrl+Z — undo")}</div>
      <div class="mt-ed-drop" hidden>${t("Отпустите файл, чтобы импортировать", "Drop the file to import it")}</div>
      <div class="mt-ed-actions">
        <span class="mt-ed-size"></span>
        <button type="button" class="mt-tb ghost" data-ed="cancel">${t("Отмена", "Cancel")}</button>
        ${sendback ? `<button type="button" class="mt-tb primary" data-ed="sendback">${ic("send")}<span>${t("В поле ввода", "To the message box")}</span></button>`
        : `<button type="button" class="mt-tb" data-ed="insert" title="${esc(t("Вставить Markdown-таблицу в текст сообщения", "Insert the Markdown table into the message text"))}">${ic("edit")}<span>${t("Вставить текстом", "Insert as text")}</span></button>
           <button type="button" class="mt-tb primary" data-ed="attach">${ic("attach")}<span>${t("Прикрепить", "Attach")}</span></button>`}
      </div>`, { wide: true });
    const el = m.el;
    const grid = el.querySelector(".mt-ed-grid");
    const fileInput = el.querySelector("input[type=file]");
    const all = () => headerOn ? [d.headers, ...d.rows] : [d.headers, ...d.rows];
    function draw() {
      const width = d.headers.length;
      grid.style.gridTemplateColumns = `34px repeat(${width}, minmax(110px, 1fr))`;
      const cells = [`<div class="mt-ed-corner"></div>`];
      for (let c = 0; c < width; c += 1) cells.push(`<div class="mt-ed-colh">${colName(c)}${d.align[c] ? ` <small>${d.align[c] === "center" ? "↔" : d.align[c] === "right" ? "→" : "←"}</small>` : ""}</div>`);
      all().forEach((row, r) => {
        cells.push(`<div class="mt-ed-rowh">${headerOn ? (r === 0 ? "H" : r) : r + 1}</div>`);
        row.forEach((v, c) => cells.push(`<input class="mt-ed-cell${r === 0 && headerOn ? " head" : ""}" data-r="${r}" data-c="${c}" value="${esc(v)}" spellcheck="false" aria-label="${colName(c)}${r}" style="text-align:${d.align[c] || "left"}">`));
      });
      grid.innerHTML = cells.join("");
      el.querySelector(".mt-ed-size").textContent = `${d.rows.length} × ${d.headers.length}`;
      const target = grid.querySelector(`[data-r="${focus.r}"][data-c="${focus.c}"]`);
      if (target) target.focus({ preventScroll: false });
    }
    const setCell = (r, c, v) => { if (r === 0) d.headers[c] = v; else { while (d.rows.length < r) d.rows.push(d.headers.map(() => "")); d.rows[r - 1][c] = v; } };
    function pasteBlock(text, r0, c0) {
      const parsed = parseDelimited(text);
      if (!parsed) return false;
      const block = [parsed.headers, ...parsed.rows];
      if (block.length === 1 && block[0].length === 1) return false;     // a single value: normal paste
      pushHistory();
      const needCols = c0 + Math.max(...block.map((b) => b.length));
      while (d.headers.length < needCols) { d.headers.push(""); d.rows.forEach((row) => row.push("")); d.align.push(""); }
      block.forEach((row, i) => row.forEach((v, j) => setCell(r0 + i, c0 + j, v)));
      d = normalize(d);
      draw();
      return true;
    }
    function load(next) {
      if (!next) { notify(t("Не нашёл таблицу в данных", "No table found in the data"), "warn"); return; }
      pushHistory();
      d = normalize({ ...next, title: next.title || el.querySelector(".mt-ed-title").value });
      el.querySelector(".mt-ed-title").value = d.title;
      focus = { r: 0, c: 0 };
      draw();
      notify(t(`Загружено: ${d.rows.length} ${rowsWord(d.rows.length)}`, `Loaded: ${d.rows.length} ${rowsWord(d.rows.length)}`));
    }
    async function importFile(file) {
      if (!file) return;
      if (file.size > 15 * 1024 * 1024) { notify(t("Файл больше 15 МБ", "The file is larger than 15 MB"), "warn"); return; }
      const name = file.name.toLowerCase();
      try {
        if (name.endsWith(".xlsx")) {
          const url = await blobToDataUrl(file);
          const resp = await fetch("/api/tables/xlsx", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data: url, name: file.name }) });
          const out = await resp.json().catch(() => ({}));
          if (!resp.ok) throw new Error((isEn() ? out.error : out.error_ru || out.error) || "HTTP " + resp.status);
          const sheets = out.sheets || [];
          if (!sheets.length) throw new Error(t("В книге нет данных", "The workbook has no data"));
          let sheet = sheets[0];
          if (sheets.length > 1) {
            sheet = await new Promise((resolve) => {
              const anchor = el.querySelector("[data-ed='import']");
              showMenu(anchor, [{ section: t("Какой лист?", "Which sheet?") }, ...sheets.map((s) => ({ icon: "table", label: s.name, hint: `${s.rows.length} × ${(s.rows[0] || []).length}`, run: () => resolve(s) }))]);
            });
          }
          const rows = sheet.rows || [];
          load(normalize({ headers: rows[0] || [], rows: rows.slice(1), title: (out.truncated ? "" : "") }));
          if (out.truncated) notify(t("Показаны первые 2000 строк и 60 столбцов", "Showing the first 2000 rows and 60 columns"), "warn");
          return;
        }
        const text = await file.text();
        if (name.endsWith(".json")) {
          const value = JSON.parse(text);
          const list = Array.isArray(value) ? value : Array.isArray(value?.rows) ? value.rows : null;
          if (!list || !list.length) throw new Error(t("Ожидался массив объектов", "An array of objects was expected"));
          if (Array.isArray(list[0])) { load(normalize({ headers: list[0], rows: list.slice(1) })); return; }
          const keys = [...new Set(list.flatMap((o) => Object.keys(o || {})))];
          load(normalize({ headers: keys, rows: list.map((o) => keys.map((k) => (o?.[k] == null ? "" : typeof o[k] === "object" ? JSON.stringify(o[k]) : String(o[k])))) }));
          return;
        }
        load(name.endsWith(".md") || name.endsWith(".markdown") ? parseMarkdown(text) : parseDelimited(text, name.endsWith(".tsv") ? "\t" : undefined));
      } catch (err) { notify(t("Не удалось импортировать: ", "Could not import: ") + (err.message || err), "warn"); }
    }
    grid.addEventListener("input", (e) => {
      const cell = e.target.closest(".mt-ed-cell");
      if (!cell) return;
      if (!cell._edited) { pushHistory(); cell._edited = true; }
      setCell(+cell.dataset.r, +cell.dataset.c, cell.value);
    });
    grid.addEventListener("focusin", (e) => { const cell = e.target.closest(".mt-ed-cell"); if (cell) focus = { r: +cell.dataset.r, c: +cell.dataset.c }; });
    grid.addEventListener("paste", (e) => {
      const cell = e.target.closest(".mt-ed-cell");
      const text = e.clipboardData?.getData("text/plain") || "";
      if (!cell || !/[\t\n]/.test(text.replace(/\n$/, ""))) return;
      if (pasteBlock(text, +cell.dataset.r, +cell.dataset.c)) e.preventDefault();
    });
    grid.addEventListener("keydown", (e) => {
      const cell = e.target.closest(".mt-ed-cell");
      if (!cell) return;
      const r = +cell.dataset.r, c = +cell.dataset.c, rows = d.rows.length + 1, cols = d.headers.length;
      const go = (nr, nc) => { focus = { r: nr, c: nc }; const n = grid.querySelector(`[data-r="${nr}"][data-c="${nc}"]`); if (n) { n.focus(); n.select(); } };
      if (e.key === "Tab") {
        e.preventDefault();
        if (e.shiftKey) go(c > 0 ? r : Math.max(0, r - 1), c > 0 ? c - 1 : cols - 1);
        else if (c < cols - 1) go(r, c + 1);
        else if (r < rows - 1) go(r + 1, 0);
        else { pushHistory(); d.rows.push(d.headers.map(() => "")); focus = { r: r + 1, c: 0 }; draw(); }
      } else if (e.key === "Enter" && !e.ctrlKey) {
        e.preventDefault();
        if (r < rows - 1) go(r + 1, c); else { pushHistory(); d.rows.push(d.headers.map(() => "")); focus = { r: r + 1, c }; draw(); }
      } else if (e.key === "ArrowDown" && r < rows - 1) { e.preventDefault(); go(r + 1, c); }
      else if (e.key === "ArrowUp" && r > 0) { e.preventDefault(); go(r - 1, c); }
      else if (e.key === "ArrowRight" && cell.selectionStart === cell.value.length && c < cols - 1) { e.preventDefault(); go(r, c + 1); }
      else if (e.key === "ArrowLeft" && cell.selectionStart === 0 && cell.selectionEnd === 0 && c > 0) { e.preventDefault(); go(r, c - 1); }
      else if (e.key.toLowerCase() === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey && history.length) { e.preventDefault(); d = JSON.parse(history.pop()); draw(); }
      else if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); el.querySelector("[data-ed='attach'], [data-ed='sendback']")?.click(); }
    });
    el.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); el.querySelector(".mt-ed-drop").hidden = false; } });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.querySelector(".mt-ed-drop").hidden = true; });
    el.addEventListener("drop", (e) => { e.preventDefault(); el.querySelector(".mt-ed-drop").hidden = true; importFile(e.dataTransfer?.files?.[0]); });
    fileInput.onchange = () => { importFile(fileInput.files[0]); fileInput.value = ""; };
    el.querySelector("[data-ed='header']").onchange = (e) => { headerOn = e.target.checked; draw(); };
    const finalData = () => {
      const out = normalize({ ...d, title: el.querySelector(".mt-ed-title").value.trim() });
      // empty trailing rows are dropped; no header row -> generic column names
      while (out.rows.length && out.rows[out.rows.length - 1].every((v) => !v.trim())) out.rows.pop();
      if (!headerOn) { out.rows.unshift(out.headers.slice()); out.headers = out.headers.map((_, i) => t("Столбец ", "Column ") + (i + 1)); }
      return out;
    };
    const markdownOut = () => { const out = finalData(); return (out.title ? `**${out.title.replace(/\*/g, "")}**\n\n` : "") + toMarkdown(out); };
    el.querySelector(".mt-ed-tools").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-ed]");
      if (!b) return;
      const what = b.dataset.ed;
      const { r, c } = focus;
      if (what === "row+") { pushHistory(); d.rows.splice(Math.max(0, r), 0, d.headers.map(() => "")); focus = { r: r + 1, c }; }
      else if (what === "col+") { pushHistory(); d.headers.splice(c + 1, 0, t("Столбец ", "Column ") + (d.headers.length + 1)); d.rows.forEach((row) => row.splice(c + 1, 0, "")); d.align.splice(c + 1, 0, ""); focus = { r, c: c + 1 }; }
      else if (what === "row-") { if (r > 0 && d.rows.length > 1) { pushHistory(); d.rows.splice(r - 1, 1); focus = { r: Math.min(r, d.rows.length), c }; } }
      else if (what === "col-") { if (d.headers.length > 1) { pushHistory(); d.headers.splice(c, 1); d.rows.forEach((row) => row.splice(c, 1)); d.align.splice(c, 1); focus = { r, c: Math.max(0, c - 1) }; } }
      else if (what.startsWith("al-")) { pushHistory(); const a = what.slice(3); d.align[c] = d.align[c] === a ? "" : a; }
      else if (what === "transpose") { pushHistory(); const m2 = [d.headers, ...d.rows]; const tr = m2[0].map((_, i) => m2.map((row) => row[i])); d = normalize({ headers: tr[0], rows: tr.slice(1), title: d.title }); focus = { r: 0, c: 0 }; }
      else if (what === "clear") { pushHistory(); d.rows = d.rows.map((row) => row.map(() => "")); d.headers = d.headers.map(() => ""); }
      else if (what === "import") { fileInput.click(); return; }
      else if (what === "paste") {
        try {
          const text = await navigator.clipboard.readText();
          if (!text.trim()) { notify(t("Буфер обмена пуст", "The clipboard is empty"), "warn"); return; }
          const parsed = parseDelimited(text);
          if (parsed) load(parsed);
        } catch (err) { notify(t("Нет доступа к буферу — нажмите Ctrl+V в ячейке", "No clipboard access — press Ctrl+V in a cell"), "warn"); }
        return;
      } else if (what === "new") {
        showMenu(b, [{ section: t("Размер новой таблицы", "New table size") }, ...[[3, 3], [4, 3], [5, 4], [8, 5], [10, 6]].map(([rows, cols]) => ({ icon: "table", label: `${rows} × ${cols}`, hint: t(`${rows} ${rowsWord(rows)}, ${cols} столбца`, `${rows} rows, ${cols} columns`), run: () => { pushHistory(); d = normalize({ headers: Array.from({ length: cols }, (_, i) => t("Столбец ", "Column ") + (i + 1)), rows: Array.from({ length: rows }, () => Array(cols).fill("")) }); focus = { r: 1, c: 0 }; draw(); } }))]);
        return;
      }
      draw();
    });
    el.querySelector(".mt-ed-actions").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-ed]");
      if (!b) return;
      const what = b.dataset.ed;
      if (what === "cancel") { m.close(); return; }
      const out = finalData();
      if (!out.rows.length && out.headers.every((h) => !h.trim())) { notify(t("Таблица пуста", "The table is empty"), "warn"); return; }
      const md = markdownOut();
      if (what === "sendback") {
        const handler = onSendBack || defaultSendBack;
        if (handler(md, out) !== false) { m.close(); notify(t("Таблица в поле ввода — допишите просьбу и отправьте", "The table is in the message box — add your request and send")); }
        return;
      }
      if (onDone) { onDone(md, out, what); m.close(); return; }
      if (what === "insert") { if (defaultInsert(md)) m.close(); return; }
      if (what === "attach") {
        const name = safeName(out.title || t("таблица", "table")) + ".md";
        const file = new File([md + "\n"], name, { type: "text/markdown" });
        if (typeof addAttachmentFiles === "function") {
          const added = await addAttachmentFiles([file]);
          if (added) { m.close(); notify(t("Таблица прикреплена к сообщению", "The table is attached to the message")); }
        } else if (defaultInsert(md)) m.close();
      }
    });
    draw();
    setTimeout(() => grid.querySelector(".mt-ed-cell")?.select(), 40);
    return m;
  }
  function defaultInsert(md) {
    const input = document.getElementById("input") || document.getElementById("qa-input");
    if (!input) return false;
    const before = input.value.slice(0, input.selectionStart ?? input.value.length), after = input.value.slice(input.selectionEnd ?? input.value.length);
    const glueA = before && !/\n\n$/.test(before) ? (before.endsWith("\n") ? "\n" : "\n\n") : "";
    input.value = before + glueA + md + "\n" + after;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    return true;
  }

  /* ---------------------------------------------------------------- picture studio */
  function openStudio(input) {
    const data = normalize(input && input.headers ? input : fromTable(input));
    let style = { ...loadStyle(), title: data.title || "", caption: "" };
    const en = isEn();
    const fontsOpts = FONTS.map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join("");
    const color = (key, label) => `<label class="mt-st-color"><span>${label}</span><input type="color" data-st="${key}"></label>`;
    const m = modal("mt-studio", t("Таблица как картинка", "Table as an image"), `
      <div class="mt-st-layout">
        <div class="mt-st-preview"><div class="mt-st-canvas"></div><div class="mt-st-dim"></div></div>
        <div class="mt-st-side">
          <div class="mt-st-sec"><b>${t("Стиль", "Style")}</b><div class="mt-st-presets">${PRESETS.map((p) => `<button type="button" class="mt-st-preset" data-preset="${p.id}" title="${esc(en ? p.en : p.ru)}"><span class="sw" style="${presetSwatch(p)}"></span><span>${esc(en ? p.en : p.ru)}</span></button>`).join("")}</div></div>
          <div class="mt-st-sec"><b>${t("Текст", "Text")}</b>
            <input type="text" data-st="title" placeholder="${esc(t("Заголовок", "Title"))}" maxlength="140">
            <input type="text" data-st="caption" placeholder="${esc(t("Подпись (источник, дата…)", "Caption (source, date…)"))}" maxlength="200">
            <div class="mt-st-row"><label>${t("Шрифт", "Font")}<select data-st="font">${fontsOpts}</select></label><label>${t("Размер", "Size")}<input type="range" min="10" max="24" step="1" data-st="fontSize"></label></div>
            <div class="mt-st-row"><label>${t("Шапка", "Header")}<select data-st="headerStyle"><option value="filled">${t("Заливка", "Filled")}</option><option value="bold">${t("Жирная", "Bold")}</option><option value="caps">${t("Капслок", "Small caps")}</option><option value="underline">${t("Подчёркнутая", "Underlined")}</option><option value="plain">${t("Простая", "Plain")}</option></select></label>
              <label class="mt-check"><input type="checkbox" data-st="numbersRight"> ${t("Числа вправо", "Numbers right")}</label></div>
          </div>
          <div class="mt-st-sec"><b>${t("Фон", "Background")}</b>
            <div class="mt-seg" data-seg="bgType"><button type="button" data-v="none">${t("Нет", "None")}</button><button type="button" data-v="solid">${t("Цвет", "Color")}</button><button type="button" data-v="gradient">${t("Градиент", "Gradient")}</button><button type="button" data-v="image">${t("Картинка", "Image")}</button></div>
            <div class="mt-st-row">${color("bg1", t("Цвет 1", "Color 1"))}<span data-when="gradient">${color("bg2", t("Цвет 2", "Color 2"))}</span><label data-when="gradient">${t("Угол", "Angle")}<input type="range" min="0" max="360" step="5" data-st="gradAngle"></label></div>
            <div class="mt-st-row" data-when="image"><button type="button" class="mt-tb" data-st-act="bgimage">${ic("upload")}<span>${t("Выбрать картинку", "Choose an image")}</span></button><label>${t("Затемнение", "Dim")}<input type="range" min="0" max="0.8" step="0.05" data-st="bgDim"></label><input type="file" accept="image/*" hidden data-st-file></div>
            <div class="mt-st-row"><label class="mt-check"><input type="checkbox" data-st="card"> ${t("Карточка", "Card")}</label><label>${t("Непрозрачность", "Opacity")}<input type="range" min="0.1" max="1" step="0.05" data-st="cardOpacity"></label><label class="mt-check"><input type="checkbox" data-st="shadow"> ${t("Тень", "Shadow")}</label></div>
          </div>
          <div class="mt-st-sec"><b>${t("Цвета", "Colors")}</b>
            <div class="mt-st-colors">${color("text", t("Текст", "Text"))}${color("headText", t("Текст шапки", "Header text"))}${color("headBg", t("Фон шапки", "Header fill"))}${color("cardBg", t("Карточка", "Card"))}${color("border", t("Линии", "Lines"))}${color("zebraColor", t("Зебра", "Zebra"))}${color("titleColor", t("Заголовок", "Title"))}${color("accent", t("Акцент", "Accent"))}</div>
          </div>
          <div class="mt-st-sec"><b>${t("Линии и отступы", "Lines and spacing")}</b>
            <div class="mt-st-row"><label>${t("Линии", "Lines")}<select data-st="borderMode"><option value="rows">${t("Между строками", "Between rows")}</option><option value="all">${t("Сетка", "Grid")}</option><option value="header">${t("Под шапкой", "Under the header")}</option><option value="outer">${t("Рамка", "Outline")}</option><option value="dashed">${t("Пунктир", "Dashed")}</option><option value="newspaper">${t("Газетные", "Newspaper")}</option><option value="none">${t("Без линий", "None")}</option></select></label>
              <label class="mt-check"><input type="checkbox" data-st="zebra"> ${t("Зебра", "Zebra")}</label></div>
            <div class="mt-st-row"><label>${t("Скругление", "Radius")}<input type="range" min="0" max="28" step="1" data-st="radius"></label><label>${t("Толщина", "Width")}<input type="range" min="0.5" max="4" step="0.5" data-st="borderWidth"></label></div>
            <div class="mt-st-row"><label>${t("Поля картинки", "Image padding")}<input type="range" min="0" max="120" step="4" data-st="padding"></label><label>${t("Ячейки", "Cells")}<input type="range" min="4" max="28" step="1" data-st="cellPadX"></label></div>
            <div class="mt-st-row"><label>${t("Высота строк", "Row height")}<input type="range" min="3" max="20" step="1" data-st="cellPadY"></label><label>${t("Макс. ширина столбца", "Max column width")}<input type="range" min="120" max="640" step="20" data-st="maxColWidth"></label></div>
          </div>
          <div class="mt-st-sec"><b>${t("Размер", "Size")}</b>
            <div class="mt-seg" data-seg="scale"><button type="button" data-v="1">1×</button><button type="button" data-v="2">2×</button><button type="button" data-v="3">3×</button><button type="button" data-v="4">4×</button></div>
          </div>
        </div>
      </div>
      <div class="mt-ed-actions">
        <button type="button" class="mt-tb ghost" data-st-act="reset">${t("Сбросить", "Reset")}</button>
        <span class="mt-ed-size" data-st-size></span>
        <button type="button" class="mt-tb" data-st-act="copy">${ic("copy")}<span>${t("Копировать", "Copy")}</span></button>
        <button type="button" class="mt-tb" data-st-act="svg">${ic("download")}<span>SVG</span></button>
        <button type="button" class="mt-tb primary" data-st-act="png">${ic("download")}<span>PNG</span></button>
      </div>`, { wide: true });
    const el = m.el;
    const canvas = el.querySelector(".mt-st-canvas");
    function hydrateControls() {
      el.querySelectorAll("[data-st]").forEach((input) => {
        const key = input.dataset.st, v = style[key];
        if (input.type === "checkbox") input.checked = !!v;
        else if (input.type === "color") input.value = toHex(v);
        else if (input.type !== "file") input.value = v ?? "";
      });
      el.querySelectorAll("[data-seg]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", String(style[seg.dataset.seg]) === b.dataset.v)));
      el.querySelectorAll("[data-preset]").forEach((b) => b.classList.toggle("on", b.dataset.preset === style.preset));
      el.querySelectorAll("[data-when]").forEach((n) => (n.hidden = n.dataset.when !== style.bgType));
    }
    let raf = 0;
    function preview() {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const out = renderSVG(data, style);
        canvas.innerHTML = out.svg;
        const svg = canvas.querySelector("svg");
        svg.removeAttribute("width"); svg.removeAttribute("height");
        svg.style.maxWidth = Math.min(out.width, 2000) + "px";
        const k = +style.scale || 2;
        el.querySelector("[data-st-size]").textContent = `${Math.round(out.width * k)} × ${Math.round(out.height * k)} px`;
        canvas.classList.toggle("checker", style.bgType === "none");
      });
    }
    el.addEventListener("input", (e) => {
      const input = e.target.closest("[data-st]");
      if (!input || input.type === "file") return;
      const key = input.dataset.st;
      style[key] = input.type === "checkbox" ? input.checked : input.type === "range" ? +input.value : input.value;
      if (input.type !== "text") style.preset = "custom";
      if (key !== "title" && key !== "caption") el.querySelectorAll("[data-preset]").forEach((b) => b.classList.toggle("on", false));
      preview();
    });
    el.addEventListener("change", (e) => { if (e.target.closest("select[data-st]")) el.dispatchEvent(new Event("input")); });
    el.querySelectorAll("[data-seg]").forEach((seg) => seg.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      const key = seg.dataset.seg;
      style[key] = key === "scale" ? +b.dataset.v : b.dataset.v;
      if (key === "bgType" && b.dataset.v === "image" && !style.bgImage) el.querySelector("[data-st-file]").click();
      hydrateControls(); preview();
    }));
    el.querySelector(".mt-st-presets").addEventListener("click", (e) => {
      const b = e.target.closest("[data-preset]");
      if (!b) return;
      style = presetStyle(b.dataset.preset, { title: style.title, caption: style.caption, scale: style.scale, bgImage: style.bgImage });
      hydrateControls(); preview();
    });
    const file = el.querySelector("[data-st-file]");
    file.onchange = async () => {
      const f = file.files[0]; file.value = "";
      if (!f) return;
      if (f.size > 12 * 1024 * 1024) { notify(t("Картинка больше 12 МБ", "The image is larger than 12 MB"), "warn"); return; }
      style.bgImage = await blobToDataUrl(f); style.bgType = "image";
      hydrateControls(); preview();
    };
    const fileBase = () => safeName(style.title || data.title || t("таблица", "table"));
    el.querySelector(".mt-ed-actions").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-st-act]");
      if (!b) return;
      const act = b.dataset.stAct;
      try {
        if (act === "reset") { style = presetStyle(autoPreset(), { title: data.title || "" }); hydrateControls(); preview(); return; }
        saveStyle(style);
        const out = renderSVG(data, style);
        if (act === "svg") await saveFile(fileBase() + ".svg", out.svg, "image/svg+xml");
        else {
          b.disabled = true;
          const blob = await svgToPngBlob(out.svg, out.width, out.height, style.scale);
          if (act === "copy") { await copyImageBlob(blob); done(true, t("Картинка скопирована", "Image copied")); }
          else await saveFile(fileBase() + ".png", blob);
        }
      } catch (err) { notify(err.message || String(err), "warn"); }
      finally { b.disabled = false; }
    });
    el.querySelector("[data-st-act='bgimage']").onclick = () => file.click();
    hydrateControls(); preview();
    return m;
  }
  function presetSwatch(p) {
    const s = { ...BASE_STYLE, ...p.s };
    const bg = s.bgType === "gradient" ? `linear-gradient(${s.gradAngle}deg, ${s.bg1}, ${s.bg2})` : s.bg1;
    return `background:${bg};--sw-head:${s.headBg === "transparent" ? s.border : s.headBg};--sw-card:${s.card ? s.cardBg : "transparent"};--sw-line:${s.border}`;
  }
  function toHex(color) {
    const c = String(color || "");
    if (/^#[0-9a-f]{6}$/i.test(c)) return c;
    if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + c.slice(1).split("").map((x) => x + x).join("");
    const m = c.match(/rgba?\((\d+)\D+(\d+)\D+(\d+)/);
    if (m) return "#" + [m[1], m[2], m[3]].map((x) => (+x).toString(16).padStart(2, "0")).join("");
    return "#ffffff";
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", registerIcons); else registerIcons();

  window.MemuTables = Object.freeze({
    VERSION, PRESETS: PRESETS.map((p) => ({ id: p.id, ru: p.ru, en: p.en })), FONTS: FONTS.map(([id, label]) => ({ id, label })),
    maskPending, pending, block, hydrate, enhance: (wrap, opts) => enhance(wrap, opts || {}),
    parseMarkdown, parseDelimited, fromTable, normalize, parseNumber, columnKind, compareValues,
    toMarkdown, toTSV, toCSV, toHTML, toJSON, toXLSX, renderSVG, toSVG, toPNG, presetStyle,
    openEditor, openStudio, openFullscreen, saveFile, copyText, registerIcons,
    // shared UI kit for diagrams.js (same look for menus, dialogs and saving)
    ui: Object.freeze({ modal, showMenu, closeMenu, notify, saveFile, copyText, copyImageBlob, blobToDataUrl, svgToPngBlob, ic, IC, esc, t, safeName, toHex, isEn }),
  });
})();
