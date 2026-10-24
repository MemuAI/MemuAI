// MemuNote Slides — rich text editing inside a text box / table cell. The browser edits a
// contenteditable copy of the rendered paragraphs; every input is parsed back into the model
// (paras → runs with their explicit properties); formatting commands work on the model and re-render.

const ZW = /\u200b/g;
const clone = (v) => JSON.parse(JSON.stringify(v));
const RUN_KEYS = ["b", "i", "u", "s", "sz", "color", "font", "hl", "base", "cap", "spc", "href", "_x", "fld", "_fid"];

/** Parse the edited DOM back into paragraphs. `old` = paragraphs the DOM was rendered from. */
export function domToParas(root, old) {
  const paras = [];
  const divs = Array.from(root.children).filter((c) => c.classList.contains("sl-p") || c.tagName === "DIV" || c.tagName === "P");
  if (!divs.length) return [{ ...(old?.[0] ? strip(old[0]) : {}), runs: [{ ...(old?.[0]?.runs?.[0] ? runProps(old[0].runs[0]) : {}), t: root.textContent.replace(ZW, "") }] }];
  for (const div of divs) {
    const pi = div.dataset.p !== undefined ? +div.dataset.p : -1;
    const src = old?.[pi] || old?.[old.length - 1] || { runs: [] };
    const para = { ...strip(src), runs: [] };
    let last = src.runs?.find((r) => !r.br) || {};
    const walk = (node) => {
      for (const n of node.childNodes) {
        if (n.nodeType === 3) {
          const text = n.nodeValue.replace(ZW, "");
          if (!text) continue;
          const span = n.parentElement.closest("[data-r]");
          const r = span && span.closest(".sl-p, div") === div ? src.runs?.[+span.dataset.r] : null;
          const props = r ? runProps(r) : runProps(last);
          if (r) last = r;
          pushText(para.runs, props, text);
        } else if (n.nodeType === 1) {
          if (n.classList.contains("sl-bu")) continue;
          if (n.tagName === "BR") { if (n.nextSibling || n.previousSibling && n.parentElement === div && div.lastChild !== n) para.runs.push({ br: true, t: "\n" }); continue; }
          if (n.tagName === "DIV" || n.tagName === "P") continue;   // nested blocks are handled as their own paragraphs
          walk(n);
        }
      }
    };
    walk(div);
    // drop a trailing <br> placeholder Chromium leaves in an emptied paragraph
    while (para.runs.length && para.runs[para.runs.length - 1].br && !para.runs.some((r) => !r.br && r.t)) para.runs.pop();
    if (!para.runs.length && src.runs?.[0]) { const p = runProps(src.runs.find((r) => !r.br) || {}); if (p.sz) para.endSz = p.sz; }
    paras.push(para);
    // Chromium may nest a new <div> inside the paragraph div when pressing Enter at the end
    for (const inner of Array.from(div.children).filter((c) => c.tagName === "DIV" || c.tagName === "P")) {
      const sub = domToParas({ children: [inner], textContent: inner.textContent }, [src]);
      paras.push(...sub);
    }
  }
  return paras;
}
function strip(p) { const o = {}; for (const k in p) if (k !== "runs") o[k] = clone(p[k]); return o; }
function runProps(r) { const o = {}; for (const k of RUN_KEYS) if (r && r[k] !== undefined) o[k] = clone(r[k]); return o; }
function pushText(runs, props, text) {
  const prev = runs[runs.length - 1];
  if (prev && !prev.br && JSON.stringify(runProps(prev)) === JSON.stringify(props)) prev.t += text;
  else runs.push({ ...props, t: text });
}

// ------------------------------------------------------------------ selection as (paragraph, offset)
function paraDivs(root) { return Array.from(root.querySelectorAll(":scope > .sl-p, :scope > div")); }
function offsetIn(div, node, off) {
  let n = 0;
  const walker = document.createTreeWalker(div, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  let cur = walker.currentNode;
  if (node === div) {   // offset counts child nodes
    for (let i = 0; i < off && i < div.childNodes.length; i++) n += textLen(div.childNodes[i]);
    return n;
  }
  while ((cur = walker.nextNode())) {
    if (cur.nodeType === 1 && cur.classList?.contains("sl-bu")) { walker.nextSibling(); continue; }
    if (cur === node) return n + (cur.nodeType === 3 ? cur.nodeValue.slice(0, off).replace(ZW, "").length : Array.from(cur.childNodes).slice(0, off).reduce((a, c) => a + textLen(c), 0));
    if (cur.nodeType === 3 && !cur.parentElement.closest(".sl-bu")) n += cur.nodeValue.replace(ZW, "").length;
    if (cur.nodeType === 1 && cur.tagName === "BR") n += 1;
  }
  return n;
}
function textLen(node) {
  if (node.nodeType === 3) return node.nodeValue.replace(ZW, "").length;
  if (node.nodeType !== 1 || node.classList.contains("sl-bu")) return 0;
  if (node.tagName === "BR") return 1;
  return Array.from(node.childNodes).reduce((a, c) => a + textLen(c), 0);
}
export function getSel(root) {
  const sel = getSelection();
  if (!sel.rangeCount || !root.contains(sel.anchorNode)) return null;
  const divs = paraDivs(root);
  const pos = (node, off) => {
    const div = (node.nodeType === 1 && divs.includes(node)) ? node : node.parentElement?.closest(".sl-p, div");
    let p = divs.indexOf(div);
    if (p < 0) { if (node === root) { p = Math.min(off, divs.length) - (off >= divs.length ? 1 : 0); return { p: Math.max(0, p), o: off >= divs.length ? textLen(divs[divs.length - 1] || root) : 0 }; } p = 0; }
    return { p, o: offsetIn(divs[p], node, off) };
  };
  let a = pos(sel.anchorNode, sel.anchorOffset), b = pos(sel.focusNode, sel.focusOffset);
  if (b.p < a.p || (b.p === a.p && b.o < a.o)) [a, b] = [b, a];
  return { start: a, end: b, collapsed: a.p === b.p && a.o === b.o };
}
export function setSel(root, s) {
  if (!s) return;
  const divs = paraDivs(root);
  const point = ({ p, o }) => {
    const div = divs[Math.min(p, divs.length - 1)]; if (!div) return [root, 0];
    let left = o;
    const walker = document.createTreeWalker(div, NodeFilter.SHOW_TEXT);
    let n, last = null;
    while ((n = walker.nextNode())) {
      if (n.parentElement.closest(".sl-bu")) continue;
      const len = n.nodeValue.replace(ZW, "").length;
      last = n;
      if (left <= len) return [n, Math.min(n.nodeValue.length, left + (n.nodeValue.startsWith("\u200b") ? 1 : 0))];
      left -= len;
    }
    return last ? [last, last.nodeValue.length] : [div, div.childNodes.length];
  };
  const [sn, so] = point(s.start), [en, eo] = point(s.end);
  const r = document.createRange();
  try { r.setStart(sn, so); r.setEnd(en, eo); } catch { return; }
  const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
}

// ------------------------------------------------------------------ model operations
/** Split runs so that [start,end) of each paragraph is covered by whole runs; returns the covered runs. */
export function splitRange(paras, s) {
  const out = [];
  for (let p = s.start.p; p <= s.end.p; p++) {
    const para = paras[p]; if (!para) continue;
    const from = p === s.start.p ? s.start.o : 0, to = p === s.end.p ? s.end.o : Infinity;
    const runs = [];
    let pos = 0;
    for (const r of para.runs) {
      const len = r.br ? 1 : r.t.length;
      const a = pos, b = pos + len; pos = b;
      if (r.br || b <= from || a >= to) { runs.push(r); if (!r.br && a >= from && b <= to && len) out.push(r); continue; }
      const cut1 = Math.max(0, from - a), cut2 = Math.min(len, to - a);
      if (cut1 > 0) runs.push({ ...clone(r), t: r.t.slice(0, cut1) });
      const mid = { ...clone(r), t: r.t.slice(cut1, cut2) }; runs.push(mid); out.push(mid);
      if (cut2 < len) runs.push({ ...clone(r), t: r.t.slice(cut2) });
    }
    para.runs = runs.filter((r) => r.br || r.t.length || runs.length === 1);
  }
  return out;
}
export function wordRange(paras, pos) {
  const text = (paras[pos.p]?.runs || []).map((r) => (r.br ? "\n" : r.t)).join("");
  let a = pos.o, b = pos.o;
  while (a > 0 && /[\p{L}\p{N}_'-]/u.test(text[a - 1])) a--;
  while (b < text.length && /[\p{L}\p{N}_'-]/u.test(text[b])) b++;
  return { start: { p: pos.p, o: a }, end: { p: pos.p, o: b }, collapsed: a === b };
}
export function allRange(paras) {
  const last = paras.length - 1;
  return { start: { p: 0, o: 0 }, end: { p: last, o: (paras[last]?.runs || []).reduce((n, r) => n + (r.br ? 1 : r.t.length), 0) } };
}
export function setRunProps(runs, props) {
  for (const r of runs) for (const [k, v] of Object.entries(props)) { if (v === undefined || v === null) delete r[k]; else r[k] = clone(v); }
}
export function mergeRuns(paras) {
  for (const p of paras) {
    const out = [];
    for (const r of p.runs) {
      const prev = out[out.length - 1];
      if (prev && !prev.br && !r.br && !r.fld && !prev.fld && JSON.stringify(runProps(prev)) === JSON.stringify(runProps(r))) prev.t += r.t;
      else out.push(r);
    }
    p.runs = out;
  }
  return paras;
}
/** Insert plain text (may contain newlines) at a position; returns the caret after it. */
export function insertText(paras, s, text) {
  if (!s.collapsed) deleteRange(paras, s);
  const at = s.start;
  const para = paras[at.p];
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  const head = [], tail = [];
  let pos = 0, props = null;
  for (const r of para.runs) {
    const len = r.br ? 1 : r.t.length;
    if (pos + len <= at.o) head.push(r);
    else if (pos >= at.o) tail.push(r);
    else { head.push({ ...clone(r), t: r.t.slice(0, at.o - pos) }); tail.push({ ...clone(r), t: r.t.slice(at.o - pos) }); }
    if (!r.br && pos <= at.o) props = runProps(r);
    pos += len;
  }
  props ||= runProps(para.runs.find((r) => !r.br) || {});
  const first = { ...strip(para), runs: [...head, { ...props, t: lines[0] }] };
  if (lines.length === 1) { first.runs.push(...tail); paras[at.p] = first; mergeRuns([first]); return { p: at.p, o: at.o + lines[0].length }; }
  const mids = lines.slice(1, -1).map((l) => ({ ...strip(para), runs: [{ ...props, t: l }] }));
  const last = { ...strip(para), runs: [{ ...props, t: lines[lines.length - 1] }, ...tail] };
  paras.splice(at.p, 1, first, ...mids, last);
  mergeRuns([first, last]);
  return { p: at.p + lines.length - 1, o: lines[lines.length - 1].length };
}
export function deleteRange(paras, s) {
  const covered = splitRange(paras, s);
  const set = new Set(covered);
  for (let p = s.start.p; p <= s.end.p; p++) if (paras[p]) paras[p].runs = paras[p].runs.filter((r) => !set.has(r));
  if (s.end.p > s.start.p) {
    const merged = paras[s.start.p];
    merged.runs.push(...paras[s.end.p].runs);
    paras.splice(s.start.p + 1, s.end.p - s.start.p);
  }
}
/** Properties at the caret / start of a range (explicit props only). */
export function propsAt(paras, pos) {
  const para = paras[pos.p]; if (!para) return {};
  let n = 0, found = null;
  for (const r of para.runs) { if (r.br) { n++; continue; } if (n + r.t.length >= pos.o && pos.o > n || (!found && n >= pos.o)) { found = r; if (n + r.t.length >= pos.o) break; } n += r.t.length; found = r; }
  return found ? runProps(found) : runProps(para.runs.find((r) => !r.br) || {});
}
export function parasInRange(paras, s) { return paras.slice(s.start.p, s.end.p + 1); }
