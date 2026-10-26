

const TE = new TextEncoder();
const TD = new TextDecoder();

let CRC_TABLE = null;
export function crc32(bytes) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; CRC_TABLE[n] = c >>> 0; }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

async function streamBytes(bytes, transform) {
  const stream = new Blob([bytes]).stream().pipeThrough(transform);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
export const inflateRaw = (bytes) => streamBytes(bytes, new DecompressionStream("deflate-raw"));
export const deflateRaw = (bytes) => streamBytes(bytes, new CompressionStream("deflate-raw"));

export class ZipArchive {
  constructor() { this.files = new Map(); this.order = []; }

  static async read(buffer) {
    const u8 = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
    let eocd = -1;
    for (let i = u8.length - 22; i >= Math.max(0, u8.length - 22 - 65536); i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error("not a ZIP file");
    const count = dv.getUint16(eocd + 10, true);
    let p = dv.getUint32(eocd + 16, true);
    const zip = new ZipArchive();
    for (let n = 0; n < count; n++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("damaged ZIP directory");
      const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true);
      const crc = dv.getUint32(p + 16, true), csize = dv.getUint32(p + 20, true), size = dv.getUint32(p + 24, true);
      const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
      const local = dv.getUint32(p + 42, true);
      const nameBytes = u8.subarray(p + 46, p + 46 + nlen);
      const name = (flags & 0x800) ? TD.decode(nameBytes) : Array.from(nameBytes, (b) => String.fromCharCode(b)).join("");
      p += 46 + nlen + xlen + clen;
      if (flags & 1) throw new Error("encrypted ZIP entries are not supported");
      if (dv.getUint32(local, true) !== 0x04034b50) throw new Error("damaged ZIP entry " + name);
      const lstart = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
      if (name.endsWith("/")) continue;
      zip.files.set(name, { name, method, crc, size, raw: u8.subarray(lstart, lstart + csize), data: null });
      zip.order.push(name);
    }
    return zip;
  }

  has(name) { return this.files.has(name); }
  names() { return this.order.filter((n) => this.files.has(n)); }

  async bytes(name) {
    const f = this.files.get(name);
    if (!f) return null;
    if (f.data) return f.data;
    if (f.method === 0) f.data = f.raw;
    else if (f.method === 8) f.data = await inflateRaw(f.raw);
    else throw new Error(`unsupported ZIP method ${f.method} (${name})`);
    return f.data;
  }
  async text(name) { const b = await this.bytes(name); return b ? TD.decode(b) : null; }

  set(name, data) {
    const bytes = typeof data === "string" ? TE.encode(data) : data;
    if (!this.files.has(name)) this.order.push(name);
    this.files.set(name, { name, method: -1, data: bytes, raw: null, crc: 0, size: bytes.length });
  }

  delete(name) { this.files.delete(name); const i = this.order.indexOf(name); if (i >= 0) this.order.splice(i, 1); }

  async write() {
    const STORE = /\.(png|jpe?g|gif|webp|mp4|m4a|mp3|wdp|zip)$/i;
    const parts = [], central = [];
    let offset = 0;
    const names = this.names();

    names.sort((a, b) => rank(a) - rank(b));
    for (const name of names) {
      const f = this.files.get(name);
      let method, crc, size, raw;
      if (f.method >= 0 && f.raw) { method = f.method; crc = f.crc; size = f.size; raw = f.raw; }
      else {
        const data = f.data;
        crc = crc32(data); size = data.length;
        if (name === "mimetype" || STORE.test(name) || size < 64) { method = 0; raw = data; }
        else { method = 8; raw = await deflateRaw(data); if (raw.length >= size) { method = 0; raw = data; } }
      }
      const nameBytes = TE.encode(name);
      const head = new Uint8Array(30 + nameBytes.length), hv = new DataView(head.buffer);
      hv.setUint32(0, 0x04034b50, true); hv.setUint16(4, 20, true); hv.setUint16(6, 0x800, true); hv.setUint16(8, method, true);
      hv.setUint16(10, 0, true); hv.setUint16(12, 0x21, true);
      hv.setUint32(14, crc, true); hv.setUint32(18, raw.length, true); hv.setUint32(22, size, true);
      hv.setUint16(26, nameBytes.length, true); hv.setUint16(28, 0, true); head.set(nameBytes, 30);
      const cen = new Uint8Array(46 + nameBytes.length), cv = new DataView(cen.buffer);
      cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x800, true);
      cv.setUint16(10, method, true); cv.setUint16(12, 0, true); cv.setUint16(14, 0x21, true);
      cv.setUint32(16, crc, true); cv.setUint32(20, raw.length, true); cv.setUint32(24, size, true);
      cv.setUint16(28, nameBytes.length, true); cv.setUint32(42, offset, true); cen.set(nameBytes, 46);
      parts.push(head, raw); central.push(cen);
      offset += head.length + raw.length;
    }
    const cenSize = central.reduce((s, c) => s + c.length, 0);
    const end = new Uint8Array(22), ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, names.length, true); ev.setUint16(10, names.length, true);
    ev.setUint32(12, cenSize, true); ev.setUint32(16, offset, true);
    const out = new Uint8Array(offset + cenSize + 22);
    let p = 0;
    for (const part of [...parts, ...central, end]) { out.set(part, p); p += part.length; }
    return out;
  }
}
function rank(name) { return name === "mimetype" ? 0 : name === "[Content_Types].xml" ? 1 : name.startsWith("_rels/") ? 2 : 3; }
