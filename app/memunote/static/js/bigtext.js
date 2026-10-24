// MemuNote — huge text without big strings (Round 31).
// Opening: the file is streamed from /api/raw, decoded chunk by chunk (TextDecoder stream mode) and split
// into lines while it downloads; the CodeMirror Text is built straight from the lines (no 50 MB string,
// no second split inside EditorState.create). Saving: the document goes out as a Blob of ~4M-char slices
// (no doc.toString() of the whole document). Measured on a 50 M-char log: open 3.6 s → see changelog.
import { CM } from "./editor.js";

const SLICE = 1 << 22;
export const LONG_LINE = 100000;      // chars: a longer line turns word wrap off for the tab (wrapping it freezes the page)

/** {doc: CM.Text, maxLine, chars} of the text at *url* in *encoding* (a TextDecoder label). */
export async function readText(url, encoding = "utf-8", onProgress = null, total = 0) {
  const res = await fetch(url);
  if (!res.ok) throw new Error("HTTP " + res.status);
  let decoder;
  try { decoder = new TextDecoder(encoding || "utf-8", { fatal: false }); } catch { decoder = new TextDecoder("utf-8"); }
  const lines = [];
  let carry = "", maxLine = 0, chars = 0, loaded = 0, lastReport = 0;
  const push = (text, final) => {
    let s = carry + text;
    let keep = "";
    if (!final && s.endsWith("\r")) { keep = "\r"; s = s.slice(0, -1); }   // "\r" + "\n" may straddle two chunks
    const parts = s.split(/\r\n|\r|\n/);
    carry = final ? "" : parts.pop() + keep;
    for (const p of parts) { lines.push(p); if (p.length > maxLine) maxLine = p.length; }
  };
  if (res.body && res.body.getReader) {
    const reader = res.body.getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      loaded += value.byteLength;
      const text = decoder.decode(value, { stream: true });
      chars += text.length;
      push(text, false);
      if (onProgress && total && loaded - lastReport > 4 * 1048576) { lastReport = loaded; onProgress(loaded / total); }
    }
    const tail = decoder.decode();
    chars += tail.length;
    push(tail, true);
  } else {
    const text = decoder.decode(await res.arrayBuffer());
    chars = text.length;
    push(text, true);
  }
  if (!lines.length) lines.push("");
  return { doc: CM.Text.of(lines), maxLine, chars };
}

/** Longest line of a CodeMirror Text (cheap: walks the lines, no copy). */
export function longestLine(doc, limit = Infinity) {
  let max = 0;
  for (const it = doc.iterLines(); !it.next().done;) { if (it.value.length > max) { max = it.value.length; if (max >= limit) break; } }
  return max;
}

/** The document as a Blob (UTF-8) built from slices: no single giant string. */
export function docBlob(doc) {
  if (doc.length <= SLICE) return new Blob([doc.toString()], { type: "text/plain; charset=utf-8" });
  const parts = [];
  for (let pos = 0; pos < doc.length;) {
    let end = Math.min(doc.length, pos + SLICE);
    let s = doc.sliceString(pos, end);
    const last = s.charCodeAt(s.length - 1);
    if (last >= 0xd800 && last <= 0xdbff && end < doc.length) { s += doc.sliceString(end, end + 1); end++; }   // never split a surrogate pair
    parts.push(s); pos = end;
  }
  return new Blob(parts, { type: "text/plain; charset=utf-8" });
}
