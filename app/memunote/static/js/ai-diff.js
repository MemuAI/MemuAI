

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const MARK_CURSOR = "⟦cursor⟧", MARK_SEL = "⟦selection⟧", MARK_SEL_END = "⟦/selection⟧";
export const MARKERS_RE = /⟦(?:cursor|\/?selection)⟧/g;

export function outlineOf(text, kind) {
  const out = [];
  const max = Math.min(text.length, 4 * 1048576);
  let pos = 0, line = 1, fence = false;
  while (pos < max && out.length < 200) {
    let end = text.indexOf("\n", pos); if (end < 0) end = text.length;
    const s = text.slice(pos, Math.min(end, pos + 400));
    if (/^\s*(```|~~~)/.test(s)) fence = !fence;
    else if (!fence) {
      const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(s);
      if (m && (kind === "markdown" || kind === "text" || kind === "document")) out.push({ level: m[1].length, title: m[2], line, from: pos });
    }
    pos = end + 1; line++;
  }
  return out;
}

export function sectionAt(text, kind, pos) {
  const heads = outlineOf(text, kind);
  let cur = -1;
  for (let i = 0; i < heads.length; i++) if (heads[i].from <= pos) cur = i;
  if (cur < 0) return null;
  const h = heads[cur];
  let end = text.length;
  for (let j = cur + 1; j < heads.length; j++) if (heads[j].level <= h.level) { end = heads[j].from; break; }
  const bodyFrom = Math.min(end, (text.indexOf("\n", h.from) + 1) || text.length);
  return { title: h.title, level: h.level, from: h.from, bodyFrom, to: end };
}

function windowAround(text, a, b, size) {
  let from = Math.max(0, a - Math.floor(size / 2)), to = Math.min(text.length, Math.max(b, from + size));
  from = Math.max(0, Math.min(from, to - size));
  const p = text.lastIndexOf("\n\n", from); if (p >= 0 && from - p < 2000) from = p + 2; else if (from > 0) { const l = text.lastIndexOf("\n", from); if (l >= 0 && from - l < 400) from = l + 1; }
  const q = text.indexOf("\n\n", to); if (q >= 0 && q - to < 2000) to = q; else { const l = text.indexOf("\n", to); if (l >= 0 && l - to < 400) to = l; }
  return [from, Math.min(text.length, to)];
}

const lineOf = (text, pos) => { let n = 1, i = -1; while ((i = text.indexOf("\n", i + 1)) >= 0 && i < pos) n++; return n; };

export function buildContext(info) {
  const text = info.text || "", kind = info.kind || "text";
  const limit = Math.max(4000, Math.min(info.limit || 48000, 120000));
  const selFrom = Math.min(info.selFrom ?? info.cursor ?? 0, text.length), selTo = Math.min(info.selTo ?? selFrom, text.length);
  const hasSel = selTo > selFrom;
  let scope = info.scope || "auto";
  if (scope === "auto") scope = hasSel ? "selection" : "document";
  if (scope === "selection" && !hasSel) scope = "document";
  const sec = sectionAt(text, kind, selFrom);
  if (scope === "section" && !sec) scope = "document";
  let range = [0, text.length];
  if (scope === "selection") range = [selFrom, selTo];
  else if (scope === "section") range = [sec.from, sec.to];

  let excerptFrom = 0, excerptTo = text.length, truncated = false;
  const total = text.length;
  if (total > limit) {
    truncated = true;
    const a = scope === "section" ? sec.from : selFrom, b = scope === "section" ? Math.min(sec.to, sec.from + limit) : selTo;
    [excerptFrom, excerptTo] = windowAround(text, a, Math.max(a, Math.min(b, a + limit - 2000)), Math.max(limit - 4000, 4000));
  }
  const pieces = [];
  if (truncated && excerptFrom > 0) {
    const head = text.slice(0, Math.min(3000, excerptFrom));
    pieces.push(head, head.length < excerptFrom ? "\n[… part of the document omitted …]\n" : "");
  }
  const prefixLen = pieces.join("").length;
  let body = text.slice(excerptFrom, excerptTo);

  const rel = (p) => p - excerptFrom;
  if (hasSel && selFrom >= excerptFrom && selTo <= excerptTo && selTo - selFrom < 200000) {
    body = body.slice(0, rel(selTo)) + MARK_SEL_END + body.slice(rel(selTo));
    body = body.slice(0, rel(selFrom)) + MARK_SEL + body.slice(rel(selFrom));
  } else if (!hasSel && selFrom >= excerptFrom && selFrom <= excerptTo) {
    body = body.slice(0, rel(selFrom)) + MARK_CURSOR + body.slice(rel(selFrom));
  }
  pieces.push(body);
  if (truncated && excerptTo < total) pieces.push("\n[… the rest of the document is omitted …]");
  const lines = total < 8e6 ? lineOf(text, total) : 0;
  return {
    text: pieces.join(""), excerpt_pos: prefixLen + Math.max(0, selFrom - excerptFrom), total_chars: total, lines,
    truncated, outline: outlineOf(text, kind).slice(0, 120).map(({ level, title, line }) => ({ level, title, line })),
    scope, section_title: sec?.title || "", selection: hasSel ? text.slice(selFrom, Math.min(selTo, selFrom + 30000)) : "",
    selection_lines: hasSel ? `${lineOf(text, selFrom)}-${lineOf(text, selTo)}` : "", range, section: sec,
  };
}

export function tokenize(s) { return s.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) || []; }

export function diffTokens(a, b, maxD = 3000) {
  let s = 0; while (s < a.length && s < b.length && a[s] === b[s]) s++;
  let ea = a.length, eb = b.length; while (ea > s && eb > s && a[ea - 1] === b[eb - 1]) { ea--; eb--; }
  const A = a.slice(s, ea), B = b.slice(s, eb), N = A.length, M = B.length;
  const out = [];
  if (s) out.push({ op: "=", text: a.slice(0, s).join("") });
  if (!N || !M) {
    if (N) out.push({ op: "-", text: A.join("") });
    if (M) out.push({ op: "+", text: B.join("") });
  } else {
    const max = N + M, off = max + 1, trace = [];
    let v = new Int32Array(2 * max + 3); v[off + 1] = 0; let found = false;
    for (let d = 0; d <= Math.min(max, maxD) && !found; d++) {
      trace.push(v.slice());
      for (let k = -d; k <= d; k += 2) {
        let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
        let y = x - k;
        while (x < N && y < M && A[x] === B[y]) { x++; y++; }
        v[off + k] = x;
        if (x >= N && y >= M) { found = true; break; }
      }
    }
    if (!found) return null;
    trace.push(v.slice());
    const ops = [];
    let x = N, y = M;
    for (let d = trace.length - 2; d >= 0 && (x > 0 || y > 0); d--) {
      const vv = trace[d], k = x - y;
      const prevK = k === -d || (k !== d && vv[off + k - 1] < vv[off + k + 1]) ? k + 1 : k - 1;
      const px = vv[off + prevK], py = px - prevK;
      while (x > px && y > py) { ops.push(["=", A[x - 1]]); x--; y--; }
      if (d > 0) { if (x === px) ops.push(["+", B[y - 1]]); else ops.push(["-", A[x - 1]]); }
      x = px; y = py;
    }
    while (x > 0 && y > 0) { ops.push(["=", A[x - 1]]); x--; y--; }
    ops.reverse();
    for (const [op, tk] of ops) { const last = out[out.length - 1]; if (last && last.op === op) last.text += tk; else out.push({ op, text: tk }); }
  }
  if (ea < a.length) { const tail = a.slice(ea).join(""); const last = out[out.length - 1]; if (last && last.op === "=") last.text += tail; else out.push({ op: "=", text: tail }); }
  return out;
}

export function buildHunks(oldText, newText, base = 0) {
  if (oldText === newText) return [];
  const ta = tokenize(oldText), tb = tokenize(newText);
  const parts = ta.length + tb.length < 60000 ? diffTokens(ta, tb) : null;
  if (!parts) return [{ from: base, to: base + oldText.length, insert: newText, del: oldText }];
  let hunks = [], pos = 0, cur = null;
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i];
    if (p.op === "=") {
      const glue = cur && i < parts.length - 1 && p.text.length <= 3 && !p.text.includes("\n");
      if (glue) { cur.to = pos + p.text.length; cur.insert += p.text; cur.del += p.text; }
      else if (cur) { hunks.push(cur); cur = null; }
      pos += p.text.length;
    } else {
      if (!cur) cur = { from: pos, to: pos, insert: "", del: "" };
      if (p.op === "-") { cur.to = pos + p.text.length; cur.del += p.text; pos += p.text.length; }
      else cur.insert += p.text;
    }
  }
  if (cur) hunks.push(cur);

  const changedChars = hunks.reduce((n, h) => n + (h.to - h.from), 0);
  if (oldText.length > 40 && changedChars > oldText.trim().length * 0.5) return paragraphHunks(oldText, newText, base);

  hunks = mergeBy(hunks, oldText, /\n\s*\n/g, (h, para) => h.length > 2 && h.reduce((n, x) => n + Math.max(x.del.length, x.insert.length), 0) > para.trim().length * 0.55);
  if (hunks.length > 60) hunks = mergeBy(hunks, oldText, /\n/g, () => true);
  return hunks.map((h) => ({ from: base + h.from, to: base + h.to, insert: h.insert, del: oldText.slice(h.from, h.to) }));
}

function paragraphHunks(oldText, newText, base) {
  const split = (t) => t.split(/(\n\s*\n)/);
  const parts = diffTokens(split(oldText), split(newText), 2000);
  if (!parts) return [{ from: base, to: base + oldText.length, insert: newText, del: oldText }];
  const out = []; let pos = 0, cur = null;
  for (const p of parts) {
    if (p.op === "=") { if (cur && !/^\n\s*\n$/.test(p.text)) { out.push(cur); cur = null; } else if (cur) { cur.to = pos + p.text.length; cur.insert += p.text; } pos += p.text.length; continue; }
    if (!cur) cur = { from: pos, to: pos, insert: "" };
    if (p.op === "-") { pos += p.text.length; cur.to = pos; } else cur.insert += p.text;
  }
  if (cur) out.push(cur);
  return out.map((h) => {

    const del = oldText.slice(h.from, h.to);
    const m = /\n\s*\n$/.exec(del), n = /\n\s*\n$/.exec(h.insert);
    if (m && n && m[0] === n[0]) { h.to -= m[0].length; h.insert = h.insert.slice(0, -n[0].length); }
    return { from: base + h.from, to: base + h.to, insert: h.insert, del: oldText.slice(h.from, h.to) };
  });
}
function mergeBy(hunks, oldText, sepRe, should) {
  if (hunks.length < 2) return hunks;
  const bounds = [0]; let m; sepRe.lastIndex = 0;
  while ((m = sepRe.exec(oldText))) bounds.push(m.index + m[0].length);
  bounds.push(oldText.length + 1);
  const blockOf = (pos) => { let lo = 0, hi = bounds.length - 1; while (lo < hi - 1) { const mid = (lo + hi) >> 1; if (bounds[mid] <= pos) lo = mid; else hi = mid; } return lo; };
  const out = []; let i = 0;
  while (i < hunks.length) {
    const b = blockOf(hunks[i].from); let j = i;
    while (j + 1 < hunks.length && blockOf(hunks[j + 1].from) === b && blockOf(hunks[j].to) === b) j++;
    const group = hunks.slice(i, j + 1);
    const para = oldText.slice(bounds[b], Math.min(oldText.length, bounds[b + 1]));
    if (group.length > 1 && should(group, para)) {
      const from = group[0].from, to = group[group.length - 1].to;
      let insert = "", p = from;
      for (const h of group) { insert += oldText.slice(p, h.from) + h.insert; p = h.to; }
      out.push({ from, to, insert, del: oldText.slice(from, to) });
    } else out.push(...group);
    i = j + 1;
  }
  return out;
}

export function diffHtml(oldText, newText, maxLen = 4000) {
  const a = oldText.length > maxLen ? oldText.slice(0, maxLen) + "…" : oldText, b = newText.length > maxLen ? newText.slice(0, maxLen) + "…" : newText;
  const parts = diffTokens(tokenize(a), tokenize(b), 1500);
  if (!parts) return (a ? `<del>${esc(a)}</del>` : "") + (b ? `<ins>${esc(b)}</ins>` : "");
  return parts.map((p) => (p.op === "=" ? esc(p.text) : p.op === "-" ? `<del>${esc(p.text)}</del>` : `<ins>${esc(p.text)}</ins>`)).join("");
}

export const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function locate(text, needle, near = 0) {
  needle = String(needle || "").replace(MARKERS_RE, "");
  if (!needle) return null;
  let best = null, i = -1, n = 0;
  while ((i = text.indexOf(needle, i + 1)) >= 0 && n++ < 5000) { if (!best || Math.abs(i - near) < Math.abs(best.from - near)) best = { from: i, to: i + needle.length }; }
  if (best) return best;
  const trimmed = needle.trim();
  if (!trimmed || trimmed.length > 20000) return null;
  const re = new RegExp(trimmed.split(/\s+/).map(escRe).join("\\s+").replace(/[“”«»"]/g, "[“”«»\"]").replace(/[‘’']/g, "[‘’']").replace(/[—–-]/g, "[—–-]"), "g");
  let m; n = 0;
  while ((m = re.exec(text)) && n++ < 5000) { if (!best || Math.abs(m.index - near) < Math.abs(best.from - near)) best = { from: m.index, to: m.index + m[0].length }; if (!m[0].length) re.lastIndex++; }
  return best;
}

export function resolveEdit(edit, text, info) {
  const near = info.cursor ?? 0;
  const ins = String(edit.text ?? "");
  const fail = (error) => ({ changes: [], error });
  switch (edit.op) {
    case "replace": case "delete": {
      const r = locate(text, edit.find, near); if (!r) return fail("not_found");
      return { changes: [{ from: r.from, to: r.to, insert: edit.op === "delete" ? "" : ins }] };
    }
    case "replace_all": {
      const changes = [];
      if (edit.regex) {
        let re; try { re = new RegExp(edit.find, "gu"); } catch { return fail("bad_regex"); }
        let m, n = 0; while ((m = re.exec(text)) && n++ < 20000) { if (!m[0].length) { re.lastIndex++; continue; } changes.push({ from: m.index, to: m.index + m[0].length, insert: m[0].replace(new RegExp(edit.find, "u"), ins) }); }
      } else { let i = -1, n = 0; const f = edit.find; while ((i = text.indexOf(f, i + 1)) >= 0 && n++ < 20000) { changes.push({ from: i, to: i + f.length, insert: ins }); i += f.length - 1; } }
      return changes.length ? { changes } : fail("not_found");
    }
    case "insert": {
      let pos = near;
      if (edit.at === "start") pos = 0;
      else if (edit.at === "end") pos = text.length;
      else if (edit.at === "after" || edit.at === "before") { const r = locate(text, edit.anchor, near); if (!r) return fail("not_found"); pos = edit.at === "after" ? r.to : r.from; }
      return { changes: [{ from: pos, to: pos, insert: joinInsert(text, pos, ins, edit.at) }] };
    }
    case "replace_selection": {
      if (!(info.selTo > info.selFrom)) return fail("no_selection");
      return { changes: [{ from: info.selFrom, to: info.selTo, insert: ins }] };
    }
    case "rewrite_section": {
      const h = String(edit.heading || "").trim().replace(/^#+\s*/, "");
      const heads = outlineOf(text, info.kind || "markdown");
      const hit = heads.find((x) => x.title.trim() === h) || heads.find((x) => x.title.trim().toLowerCase() === h.toLowerCase())
        || heads.find((x) => x.title.toLowerCase().includes(h.toLowerCase()));
      if (!hit) { const r = locate(text, edit.heading, near); if (!r) return fail("not_found"); const sec = sectionAt(text, info.kind || "markdown", r.from); if (!sec) return fail("not_found"); const gap = /^(?:[ \t]*\n)*/.exec(text.slice(sec.bodyFrom, sec.to))[0].length; return { changes: [{ from: sec.bodyFrom + gap, to: sec.to, insert: sectionBody(ins.replace(/^\s*\n/, ""), text, sec.to) }] }; }
      const sec = sectionAt(text, info.kind || "markdown", hit.from);
      let body = ins; const first = body.split("\n", 1)[0];
      if (/^#{1,6}\s/.test(first) && first.replace(/^#+\s*/, "").trim() === hit.title.trim()) body = body.slice(first.length);
      body = body.replace(/^\s*\n/, "");

      const gap = /^(?:[ \t]*\n)*/.exec(text.slice(sec.bodyFrom, sec.to))[0].length;
      return { changes: [{ from: sec.bodyFrom + gap, to: sec.to, insert: sectionBody(body, text, sec.to) }] };
    }
    default: return fail("unsupported");
  }
}
function sectionBody(body, text, to) { body = body.replace(/\s+$/, ""); return to < text.length ? body + "\n\n" : body + (text.endsWith("\n") ? "\n" : ""); }

function joinInsert(text, pos, ins, at) {
  if (at === "cursor" || !ins) return ins;
  const before = text.slice(Math.max(0, pos - 2), pos), after = text.slice(pos, pos + 2);
  const block = /\n/.test(ins) || at === "end" || at === "start";
  if (!block) return ins;
  let s = ins.replace(/^\n+|\n+$/g, "");
  if (pos > 0 && !before.endsWith("\n\n")) s = (before.endsWith("\n") ? "\n" : "\n\n") + s;
  if (pos < text.length && !after.startsWith("\n\n")) s += after.startsWith("\n") ? "\n" : "\n\n";
  else if (pos >= text.length && at === "end") s += "\n";
  return s;
}

