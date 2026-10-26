

"use strict";
const MemuContext = (() => {

  let preferred = "";
  function useModel(name) { preferred = String(name || ""); }

  let warned = false;
  function tokenizer() {
    if (typeof MemuTokens !== "undefined") return MemuTokens;
    if (typeof require !== "undefined") { try { return require("./tokens.js"); } catch (error) {  } }
    if (!warned) { warned = true; try { console.warn("MemuAI: tokens.js is not loaded — rough token estimate"); } catch (error) {  } }
    return null;
  }
  function estimateText(text, forModel) {
    if (!text) return 0;
    const tokens = tokenizer();
    if (tokens) return tokens.estimate(text, forModel || preferred);
    const value = String(text), cyrillic = (value.match(/[\u0400-\u04FF]/g) || []).length;
    return Math.max(1, Math.ceil(cyrillic / 2.6 + (value.length - cyrillic) / 3.8));
  }

  const FILE_SAMPLE = 65536;
  const FILE_LIMIT = 100000;
  const BINARY_PER_BYTE = 0.25;

  function fileTokens(dataUrl, model) {
    const raw = String(dataUrl || "");
    const comma = raw.indexOf(",");
    if (!raw.startsWith("data:") || comma < 0) return 0;
    const head = raw.slice(5, comma).toLowerCase(), base64 = /;base64/.test(head), body = raw.slice(comma + 1);
    const total = base64 ? Math.floor(body.length * 3 / 4) : body.length;
    if (!total) return 0;
    let sample = "";
    if (base64) {
      const bytes = bytesOf(body, FILE_SAMPLE);
      const binary = !bytes || bytes.subarray(0, 4096).some((b) => b === 0) || /^(application\/(pdf|zip|octet|vnd\.|msword|x-(7z|rar|tar|gzip))|audio|video|font)/.test(head);
      if (!binary) {

        sample = new TextDecoder("utf-8").decode(bytes);
        if ((sample.match(/\uFFFD/g) || []).length > 4 + sample.length / 100) sample = "";
      }
    } else {
      try { sample = decodeURIComponent(body.slice(0, FILE_SAMPLE)); } catch (error) { sample = body.slice(0, FILE_SAMPLE); }
    }
    if (!sample) return Math.min(FILE_LIMIT, Math.ceil(total * BINARY_PER_BYTE));
    const sampled = base64 ? Math.min(total, FILE_SAMPLE) : Math.min(body.length, FILE_SAMPLE);
    return Math.min(FILE_LIMIT, Math.ceil(estimateText(sample, model) * Math.max(1, total / Math.max(1, sampled))));
  }

  const sizes = new Map();

  function bytesOf(base64, limit) {
    const chunk = base64.slice(0, Math.ceil(limit / 3) * 4);
    let binary = "";
    try { binary = atob(chunk.slice(0, chunk.length - (chunk.length % 4))); } catch (error) { return null; }
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function readSize(bytes) {
    if (!bytes || bytes.length < 24) return null;
    const be16 = (at) => (bytes[at] << 8) | bytes[at + 1];
    const be32 = (at) => ((bytes[at] << 24) | (bytes[at + 1] << 16) | (bytes[at + 2] << 8) | bytes[at + 3]) >>> 0;
    const text = (at, length) => String.fromCharCode(...bytes.subarray(at, at + length));
    if (bytes[0] === 0x89 && text(1, 3) === "PNG") return { width: be32(16), height: be32(20) };
    if (text(0, 3) === "GIF") return { width: bytes[6] | (bytes[7] << 8), height: bytes[8] | (bytes[9] << 8) };
    if (text(0, 4) === "RIFF" && text(8, 4) === "WEBP") {
      const kind = text(12, 4);
      if (kind === "VP8X") return { width: 1 + (bytes[24] | (bytes[25] << 8) | (bytes[26] << 16)), height: 1 + (bytes[27] | (bytes[28] << 8) | (bytes[29] << 16)) };
      if (kind === "VP8 ") return { width: (bytes[26] | (bytes[27] << 8)) & 0x3fff, height: (bytes[28] | (bytes[29] << 8)) & 0x3fff };
      if (kind === "VP8L") return { width: 1 + (((bytes[22] & 0x3f) << 8) | bytes[21]), height: 1 + (((bytes[24] & 0x0f) << 10) | (bytes[23] << 2) | ((bytes[22] & 0xc0) >> 6)) };
      return null;
    }
    if (bytes[0] === 0xff && bytes[1] === 0xd8) {

      let at = 2;
      while (at + 9 < bytes.length) {
        if (bytes[at] !== 0xff) { at += 1; continue; }
        const marker = bytes[at + 1];
        if (marker === 0xff) { at += 1; continue; }
        if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
          return { height: be16(at + 5), width: be16(at + 7) };
        }
        if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) { at += 2; continue; }
        at += 2 + be16(at + 2);
      }
    }
    return null;
  }

  function imageSize(dataUrl) {
    const raw = String(dataUrl || "");
    if (!raw.startsWith("data:")) return null;
    const comma = raw.indexOf(",");
    if (comma < 0) return null;
    const key = `${raw.length}:${raw.slice(comma + 1, comma + 49)}:${raw.slice(-24)}`;
    if (sizes.has(key)) return sizes.get(key);
    const base64 = raw.slice(comma + 1);
    let size = readSize(bytesOf(base64, 2048));
    if (!size && base64.startsWith("/9j/")) size = readSize(bytesOf(base64, 262144));
    if (size && !(size.width > 0 && size.height > 0 && size.width < 65536 && size.height < 65536)) size = null;
    if (sizes.size > 600) sizes.clear();
    sizes.set(key, size);
    return size;
  }

  function family(model) {
    const name = String(model || "").toLowerCase();
    if (/claude|anthropic|opus|sonnet|haiku|fable/.test(name)) return "claude";
    if (/gemini|gemma|google/.test(name)) return "gemini";
    if (/gpt|openai|codex|(^|[-/_])o\d/.test(name)) return "openai";
    return "other";
  }

  function imageTokensFor(width, height, kind) {
    if (!(width > 0 && height > 0)) return { claude: 1100, gemini: 1032, openai: 765, other: 765 }[kind] || 765;
    if (kind === "claude") {

      const scale = Math.min(1, 1568 / Math.max(width, height));
      return Math.max(50, Math.min(1600, Math.ceil((width * scale) * (height * scale) / 750)));
    }
    if (kind === "gemini") {

      if (width <= 384 && height <= 384) return 258;
      const unit = Math.max(256, Math.min(768, Math.floor(Math.min(width, height) / 1.5)));
      return 258 * Math.ceil(width / unit) * Math.ceil(height / unit);
    }

    let w = width, h = height;
    const fit = Math.min(1, 2048 / Math.max(w, h));
    w *= fit; h *= fit;
    const short = Math.min(1, 768 / Math.min(w, h));
    w *= short; h *= short;
    return 85 + 170 * Math.ceil(w / 512) * Math.ceil(h / 512);
  }

  function imageTokens(dataUrl, model) {
    const size = imageSize(dataUrl);
    return imageTokensFor(size ? size.width : 0, size ? size.height : 0, family(model));
  }

  const PER_MESSAGE = 4;

  function contentTokens(content, model) {
    if (typeof content === "string") return { text: estimateText(content, model), images: 0, imageCount: 0 };
    let text = 0, images = 0, imageCount = 0;
    for (const part of Array.isArray(content) ? content : []) {
      if (!part || typeof part !== "object") continue;
      if (part.type === "image_url" || part.type === "input_image" || part.type === "image") {
        const url = typeof part.image_url === "string" ? part.image_url : (part.image_url?.url || part.url || "");
        if (part.filename && !String(url).startsWith("data:image")) text += fileTokens(url, model);
        else { images += imageTokens(url, model); imageCount += 1; }
      } else {
        text += estimateText(part.text || part.content || "", model);
      }
    }
    return { text, images, imageCount };
  }

  function payload(apiMessages, model) {
    const out = { system: 0, text: 0, images: 0, imageCount: 0, total: 0 };
    for (const message of apiMessages || []) {
      const parts = contentTokens(message.content, model);
      if (message.role === "system") out.system += parts.text + PER_MESSAGE;
      else out.text += parts.text + PER_MESSAGE;
      out.images += parts.images;
      out.imageCount += parts.imageCount;
    }
    out.total = out.system + out.text + out.images;
    return out;
  }

  return { estimateText, fileTokens, useModel, imageSize, imageTokens, imageTokensFor, family, contentTokens, payload, PER_MESSAGE };
})();
if (typeof window !== "undefined") window.MemuContext = MemuContext;
