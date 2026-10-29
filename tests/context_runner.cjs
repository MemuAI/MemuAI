

const fs = require("fs");
const vm = require("vm");
const path = require("path");
const zlib = require("zlib");

const root = path.join(__dirname, "..", "app", "pgstatic");
const contextSource = fs.readFileSync(path.join(root, "tokens.js"), "utf8").replace('"use strict";', "")
  + "\n" + fs.readFileSync(path.join(root, "context.js"), "utf8").replace('"use strict";', "");
const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
const video = fs.readFileSync(path.join(root, "video.js"), "utf8");
const from = app.indexOf("function imageTokenEstimate(");
const to = app.indexOf("function modelSeesImages(", from);
if (from < 0 || to < 0) {
  process.stdout.write(JSON.stringify({ error: "context functions not found in app.js" }) + "\n");
  process.exit(0);
}

function png(width, height) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(body) : 0);
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  const raw = Buffer.alloc((width * 3 + 1) * Math.min(height, 4));
  const file = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
  return "data:image/png;base64," + file.toString("base64");
}
function jpeg(width, height, exifBytes = 0) {
  const parts = [Buffer.from([0xff, 0xd8])];
  if (exifBytes) {
    const size = Buffer.alloc(2); size.writeUInt16BE(exifBytes + 2);
    parts.push(Buffer.from([0xff, 0xe1]), size, Buffer.alloc(exifBytes, 0x20));
  }
  const sof = Buffer.alloc(19);
  sof[0] = 0xff; sof[1] = 0xc0; sof.writeUInt16BE(17, 2); sof[4] = 8; sof.writeUInt16BE(height, 5); sof.writeUInt16BE(width, 7); sof[9] = 3;
  parts.push(sof, Buffer.alloc(600, 0x11), Buffer.from([0xff, 0xd9]));
  return "data:image/jpeg;base64," + Buffer.concat(parts).toString("base64");
}

const harness = `
  let PG_LANG = "en";
  const prefs = { context_count_images: true };
  const pref = (key, fallback) => (key in prefs ? prefs[key] : fallback);
  let model = "antigravity-claude-opus-4-6-thinking";
  const currentModel = () => model;
  const S = { currentId: "c1", mode: "chat", messages: [], contextSummary: "", contextSummaryThrough: 0, models: [] };
  const modelSeesImages = (id) => !/text-only/.test(id);
  const buildSystemPrompt = () => "S".repeat(3800);            // ≈ 1000 tokens
  const integrationsPromptBlock = () => "";
  const memePromptBlock = () => "";
  const activeMeme = () => null;
  ${contextSource}
  ${video.replace('"use strict";', "")}
  ${app.slice(from, to)}

  const out = {};
  const C = MemuContext;
  // ---- text
  // the same sentence in English and in Russian: Russian costs more tokens per character and in total
  const english = "The quick brown fox jumps over the lazy dog near the river. ".repeat(10);
  const russian = "Быстрая коричневая лиса прыгает через ленивую собаку у реки. ".repeat(10);
  out.latin = [C.estimateText(english), english.length];
  out.cyrillic = [C.estimateText(russian), russian.length];
  out.claudeRussian = C.estimateText(russian, "claude-sonnet-4-6");
  out.empty = C.estimateText("");
  // ---- image sizes come from the file header, not from its weight
  out.pngSize = C.imageSize(IMAGES.png);
  out.jpegSize = C.imageSize(IMAGES.jpeg);
  out.exifSize = C.imageSize(IMAGES.exif);
  out.notImage = C.imageSize("data:text/plain;base64,aGVsbG8=");
  out.remote = C.imageSize("https://example.com/a.png");
  // ---- the same 1920×1080 picture costs differently in each family
  out.byFamily = { claude: C.imageTokensFor(1920, 1080, "claude"), openai: C.imageTokensFor(1920, 1080, "openai"),
    gemini: C.imageTokensFor(1920, 1080, "gemini"), small: C.imageTokensFor(300, 200, "gemini"), unknown: C.imageTokensFor(0, 0, "claude") };
  out.families = ["antigravity-claude-opus-4-6-thinking", "kiro-claude-sonnet-4-6", "antigravity-gemini-3.8-flash-high",
    "genspark-gpt-5", "openai/o3", "local-qwen"].map((id) => C.family(id));
  out.sameBytes = [C.imageTokens(IMAGES.png, "claude-x"), C.imageTokens(IMAGES.small, "claude-x")];

  // ---- a request: system + text + two images
  const request = C.payload([
    { role: "system", content: "S".repeat(380) },
    { role: "user", content: [{ type: "text", text: "a".repeat(38) }, { type: "image_url", image_url: { url: IMAGES.png } },
      { type: "image_url", image_url: { url: IMAGES.jpeg } }] },
    { role: "assistant", content: "b".repeat(76) }], "claude-x");
  out.request = request;

  // ---- a chat: reasoning is not resent, a video is its sampled frames, a described image is text
  const frames = Array.from({ length: 24 }, (_, i) => ({ t: i * 2, data: IMAGES.frame }));
  S.messages = [
    { role: "user", content: "a".repeat(380), attachments: [{ name: "shot.png", data: IMAGES.png }] },
    { role: "assistant", content: "b".repeat(380), reasoning: "r".repeat(40000) },
    { role: "user", content: "watch this", attachments: [{ name: "clip.mp4", isVideo: true, frames, duration: 48, width: 1280, height: 720, transcript: "t".repeat(380) }] },
    { role: "assistant", content: "c".repeat(38) },
  ];
  const whole = contextTokenTotals();
  out.chat = { system: whole.system, images: whole.images, imageCount: whole.imageCount, measured: whole.measured,
    total: whole.total, sums: whole.total === whole.system + whole.summary + whole.messages + whole.images };
  out.reasoningIgnored = messageTokenParts(S.messages[1]).text;
  out.video = messageTokenParts(S.messages[2]);
  out.videoAnalysed = messageTokenParts({ ...S.messages[2], videoAnalysis: "v".repeat(380) });
  out.described = messageTokenParts({ role: "user", content: "x", visionDescription: "d".repeat(380), attachments: [{ data: IMAGES.png }] });
  model = "text-only-model";
  out.blindModel = messageTokenParts(S.messages[0]).images;
  model = "antigravity-claude-opus-4-6-thinking";

  // ---- images switched off in the meter
  prefs.context_count_images = false;
  out.withoutImages = contextTokenTotals().total === whole.total - whole.images;
  prefs.context_count_images = true;

  // ---- after compression only the summary and the recent messages are sent
  S.contextSummary = "s".repeat(380); S.contextSummaryThrough = 2;
  const compressed = contextTokenTotals();
  out.compressed = { smaller: compressed.total < whole.total, summary: compressed.summary, count: compressed.compressed,
    firstImageGone: compressed.imageCount === whole.imageCount - 1 };
  S.contextSummary = ""; S.contextSummaryThrough = 0;

  // ---- the provider's own count wins, and what was added afterwards is put on top
  S.messages[3].ctx = { sent: 5000, real: 9000, index: 3, model, through: 0, own: 0 };
  const measured = contextTokenTotals();
  out.measured = { flag: measured.measured, total: measured.total };
  S.messages.push({ role: "user", content: "a".repeat(380) });
  out.measuredPlusNew = contextTokenTotals().total - measured.total;
  S.messages[3].ctx = { sent: 5000, index: 3, model, through: 0, own: 0 };
  const sentOnly = contextTokenTotals();
  out.sentOnly = { flag: sentOnly.measured, total: sentOnly.total };
  S.messages[3].ctx = { sent: 5000, real: 9000, index: 3, model: "another-model", through: 0, own: 0 };
  out.otherModel = contextTokenTotals().measured;
  S.messages[3].ctx = { sent: 5000, real: 9000, index: 1, model, through: 0, own: 0 };
  out.movedMessage = contextTokenTotals().measured;

  // ---- reports of tools for earlier questions are not resent
  S.messages = [
    { role: "user", content: "first" }, { role: "assistant", content: "ok" },
    { role: "user", internal: true, content: "<local-tool_report>" + "z".repeat(38000) + "</local-tool_report>" },
    { role: "user", content: "second" }];
  out.staleReport = contextTokenTotals().messages;
  S.messages.pop();
  out.freshReport = contextTokenTotals().messages;
  __done(out);
`;
const IMAGES = { png: png(1920, 1080), small: png(64, 64), jpeg: jpeg(800, 600), exif: jpeg(4000, 3000, 60000), frame: jpeg(768, 432) };
const ctx = { __done: (value) => process.stdout.write(JSON.stringify(value) + "\n"), IMAGES,
  atob: (text) => Buffer.from(text, "base64").toString("latin1"), console };
vm.createContext(ctx);
try {
  vm.runInContext(harness, ctx);
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String((error && error.stack) || error) }) + "\n");
}
