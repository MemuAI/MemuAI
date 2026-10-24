// MemuNote (Round 34) — polyfills for the vendored pdf.js 6.x on older Chromium / WebView2.
// pdf.js 6 calls ES2025+/proposal APIs directly (Uint8Array base64/hex, Map#getOrInsertComputed, Math.sumPrecise,
// Response#bytes, iterator helpers…); on Chromium 128 or an un-updated WebView2 a PDF failed to open with
// "a.toHex is not a function". This module is imported before pdf.min.mjs in the page (pdfedit.js) and first thing
// in the worker (pdf.worker.compat.mjs). Every shim is installed only when the native one is missing, as a
// non-enumerable property, so a current browser keeps its own implementation. Keep it dependency-free and side-effect
// only; never edit the minified vendor files themselves.
const G = globalThis;
function define(target, name, value) {
  if (target && typeof target[name] !== "function") {
    Object.defineProperty(target, name, { value, writable: true, configurable: true, enumerable: false });
  }
}

// --- Promise.withResolvers (Chrome 119) / Promise.try (Chrome 128)
define(Promise, "withResolvers", function withResolvers() {
  let resolve, reject;
  const promise = new this((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
});
define(Promise, "try", function (fn, ...args) {
  return new this((resolve) => resolve(fn(...args)));
});

// --- Uint8Array base64 / hex (Chrome 140)
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
function checkBytes(bytes) {
  if (!(bytes instanceof Uint8Array)) throw new TypeError("Uint8Array expected");
}
define(Uint8Array.prototype, "toHex", function toHex() {
  checkBytes(this);
  let out = "";
  for (let i = 0; i < this.length; i++) out += (this[i] < 16 ? "0" : "") + this[i].toString(16);
  return out;
});
define(Uint8Array.prototype, "toBase64", function toBase64(options) {
  checkBytes(this);
  const abc = options?.alphabet === "base64url" ? B64URL : B64;
  const pad = !options?.omitPadding;
  let out = "";
  const n = this.length;
  for (let i = 0; i < n; i += 3) {
    const a = this[i], b = i + 1 < n ? this[i + 1] : 0, c = i + 2 < n ? this[i + 2] : 0;
    const t = (a << 16) | (b << 8) | c;
    out += abc[(t >> 18) & 63] + abc[(t >> 12) & 63];
    out += i + 1 < n ? abc[(t >> 6) & 63] : pad ? "=" : "";
    out += i + 2 < n ? abc[t & 63] : pad ? "=" : "";
  }
  return out;
});
function decodeBase64(str, options) {
  if (typeof str !== "string") throw new TypeError("string expected");
  const abc = options?.alphabet === "base64url" ? B64URL : B64;
  const clean = str.replace(/[\t\n\f\r ]+/g, "").replace(/=+$/, "");
  if (clean.length % 4 === 1) throw new SyntaxError("Invalid base64 string");
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4));
  let bits = 0, acc = 0, j = 0;
  for (let i = 0; i < clean.length; i++) {
    const v = abc.indexOf(clean[i]);
    if (v < 0) throw new SyntaxError("Invalid base64 string");
    acc = (acc << 6) | v; bits += 6;
    if (bits >= 8) { bits -= 8; out[j++] = (acc >> bits) & 255; }
  }
  return out;
}
function decodeHex(str) {
  if (typeof str !== "string") throw new TypeError("string expected");
  if (str.length % 2 || /[^0-9a-f]/i.test(str)) throw new SyntaxError("Invalid hex string");
  const out = new Uint8Array(str.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(str.substr(i * 2, 2), 16);
  return out;
}
define(Uint8Array, "fromBase64", (str, options) => decodeBase64(str, options));
define(Uint8Array, "fromHex", (str) => decodeHex(str));
// --- Map / WeakMap upsert (TC39 proposal, not shipped in Chromium 128)
for (const C of [Map, WeakMap]) {
  define(C.prototype, "getOrInsert", function getOrInsert(key, value) {
    if (!this.has(key)) this.set(key, value);
    return this.get(key);
  });
  define(C.prototype, "getOrInsertComputed", function getOrInsertComputed(key, callback) {
    if (typeof callback !== "function") throw new TypeError("callback is not a function");
    if (!this.has(key)) this.set(key, callback(key));
    return this.get(key);
  });
}

// --- Math.sumPrecise (Neumaier compensated sum; exact for the integer sizes pdf.js adds up)
define(Math, "sumPrecise", function sumPrecise(items) {
  let sum = 0, comp = 0, count = 0;
  for (const x of items) {
    if (typeof x !== "number") throw new TypeError("Math.sumPrecise: number expected");
    count++;
    const t = sum + x;
    comp += Math.abs(sum) >= Math.abs(x) ? sum - t + x : x - t + sum;
    sum = t;
  }
  return count ? sum + comp : -0;
});

// --- URL.parse / URL.canParse (Chrome 126 / 120)
define(URL, "parse", function (url, base) {
  try { return base === undefined ? new URL(url) : new URL(url, base); } catch { return null; }
});
define(URL, "canParse", (url, base) => URL.parse(url, base) !== null);

// --- ArrayBuffer#transfer / #transferToFixedLength (Chrome 114)
for (const name of ["transfer", "transferToFixedLength"]) {
  define(ArrayBuffer.prototype, name, function (length) {
    const size = length === undefined ? this.byteLength : Math.max(0, Math.floor(Number(length)) || 0);
    const copy = new Uint8Array(size);
    copy.set(new Uint8Array(this, 0, Math.min(size, this.byteLength)));
    return copy.buffer;
  });
}

// --- Response#bytes / Blob#bytes (Chrome 132)
if (typeof Response !== "undefined") define(Response.prototype, "bytes", async function bytes() { return new Uint8Array(await this.arrayBuffer()); });
if (typeof Blob !== "undefined") define(Blob.prototype, "bytes", async function bytes() { return new Uint8Array(await this.arrayBuffer()); });

// --- AbortSignal.any (Chrome 116)
if (typeof AbortSignal !== "undefined") {
  define(AbortSignal, "any", function any(signals) {
    const ctrl = new AbortController();
    for (const s of signals) {
      if (s.aborted) { ctrl.abort(s.reason); break; }
      s.addEventListener("abort", () => ctrl.abort(s.reason), { once: true, signal: ctrl.signal });
    }
    return ctrl.signal;
  });
}

// --- async iteration of ReadableStream (Chrome 124): `for await (const chunk of stream)`
if (typeof ReadableStream !== "undefined" && !ReadableStream.prototype[Symbol.asyncIterator]) {
  const values = async function* values(options) {
    const reader = this.getReader();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) return;
        yield value;
      }
    } finally {
      if (!options?.preventCancel) reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  };
  define(ReadableStream.prototype, "values", values);
  Object.defineProperty(ReadableStream.prototype, Symbol.asyncIterator, { value: values, writable: true, configurable: true });
}

// --- Set#intersection (Chrome 122)
define(Set.prototype, "intersection", function intersection(other) { return new Set([...this].filter((v) => other.has(v))); });

// --- Iterator global + iterator helpers (Chrome 122). pdf.js touches `Iterator.prototype` at module load and calls
// map/filter/some/find/toArray on Map/Set iterators.
const IteratorProto = Object.getPrototypeOf(Object.getPrototypeOf([][Symbol.iterator]()));
if (typeof G.Iterator === "undefined") {
  function Iterator() { if (new.target === undefined || new.target === Iterator) throw new TypeError("Iterator is abstract"); }
  Iterator.prototype = IteratorProto;
  Object.defineProperty(G, "Iterator", { value: Iterator, writable: true, configurable: true, enumerable: false });
}
function* helperGen(src, fn) { yield* fn(src); }
const lazy = (fn) => function (...args) { const src = this; return helperGen(src, (it) => fn(it, ...args)); };
define(IteratorProto, "map", lazy(function* (it, f) { let i = 0; for (const v of { [Symbol.iterator]: () => it }) yield f(v, i++); }));
define(IteratorProto, "filter", lazy(function* (it, f) { let i = 0; for (const v of { [Symbol.iterator]: () => it }) if (f(v, i++)) yield v; }));
define(IteratorProto, "take", lazy(function* (it, n) {
  let left = Number(n); if (!(left >= 0)) throw new RangeError("take: invalid limit");
  if (!left) return; for (const v of { [Symbol.iterator]: () => it }) { yield v; if (--left <= 0) return; }
}));
define(IteratorProto, "drop", lazy(function* (it, n) {
  let left = Number(n); if (!(left >= 0)) throw new RangeError("drop: invalid limit");
  for (const v of { [Symbol.iterator]: () => it }) { if (left > 0) { left--; continue; } yield v; }
}));
define(IteratorProto, "flatMap", lazy(function* (it, f) { let i = 0; for (const v of { [Symbol.iterator]: () => it }) yield* f(v, i++); }));
const eager = (fn) => function (...args) { return fn({ [Symbol.iterator]: () => this }, ...args); };
define(IteratorProto, "toArray", eager((it) => [...it]));
define(IteratorProto, "forEach", eager((it, f) => { let i = 0; for (const v of it) f(v, i++); }));
define(IteratorProto, "some", eager((it, f) => { let i = 0; for (const v of it) if (f(v, i++)) return true; return false; }));
define(IteratorProto, "every", eager((it, f) => { let i = 0; for (const v of it) if (!f(v, i++)) return false; return true; }));
define(IteratorProto, "find", eager((it, f) => { let i = 0; for (const v of it) if (f(v, i++)) return v; return undefined; }));
define(IteratorProto, "reduce", function (f, ...init) {
  let i = 0, acc, started = init.length > 0;
  if (started) acc = init[0];
  for (const v of { [Symbol.iterator]: () => this }) {
    if (!started) { acc = v; started = true; i++; continue; }
    acc = f(acc, v, i++);
  }
  if (!started) throw new TypeError("Reduce of empty iterator with no initial value");
  return acc;
});
