

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MemuModelNames = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const VERSION = "1.0-r33";
  const BRANDS = {
    gpt: "GPT", chatgpt: "ChatGPT", glm: "GLM", qwen: "Qwen", qwq: "QwQ", qvq: "QVQ", deepseek: "DeepSeek",
    claude: "Claude", gemini: "Gemini", gemma: "Gemma", grok: "Grok", kimi: "Kimi", moonshot: "Moonshot",
    minimax: "MiniMax", mistral: "Mistral", mixtral: "Mixtral", codestral: "Codestral", devstral: "Devstral",
    magistral: "Magistral", ministral: "Ministral", pixtral: "Pixtral", llama: "Llama", codellama: "CodeLlama",
    phi: "Phi", yi: "Yi", command: "Command", aya: "Aya", nova: "Nova", titan: "Titan", sonar: "Sonar",
    dall: "DALL", imagen: "Imagen", flux: "FLUX", sdxl: "SDXL", seedream: "Seedream", seedance: "Seedance",
    doubao: "Doubao", ernie: "ERNIE", hunyuan: "Hunyuan", wanx: "Wanx", wan: "Wan", kling: "Kling",
    veo: "Veo", sora: "Sora", hailuo: "Hailuo", runway: "Runway", ideogram: "Ideogram", recraft: "Recraft",
    midjourney: "Midjourney", nemotron: "Nemotron", granite: "Granite", hermes: "Hermes", olmo: "OLMo",
    falcon: "Falcon", internlm: "InternLM", internvl: "InternVL", baichuan: "Baichuan", yandexgpt: "YandexGPT",
    gigachat: "GigaChat", jamba: "Jamba", lfm: "LFM", cogview: "CogView", cogvideo: "CogVideo",
    cogvideox: "CogVideoX", whisper: "Whisper", suno: "Suno", udio: "Udio", lyria: "Lyria", kandinsky: "Kandinsky",
    opus: "Opus", sonnet: "Sonnet", haiku: "Haiku", fable: "Fable", mythos: "Mythos", codex: "Codex",
    llava: "LLaVA", moondream: "Moondream", smollm: "SmolLM", starcoder: "StarCoder", dbrx: "DBRX",
    palm: "PaLM", bard: "Bard", copilot: "Copilot", perplexity: "Perplexity", step: "Step", spark: "Spark",
    openai: "OpenAI", anthropic: "Anthropic", google: "Google", xai: "xAI", meta: "Meta", ai: "AI",
    api: "API", oss: "OSS", vl: "VL", tts: "TTS", stt: "STT", asr: "ASR", ocr: "OCR", hd: "HD",
    xl: "XL", xxl: "XXL", sd: "SD", it: "IT", moe: "MoE", gguf: "GGUF", awq: "AWQ", gptq: "GPTQ",
    fp8: "FP8", fp16: "FP16", bf16: "BF16", int4: "INT4", int8: "INT8", mlx: "MLX", rl: "RL", ft: "FT",
    t2i: "T2I", t2v: "T2V", i2v: "I2V", i2i: "I2I", r1: "R1", rp: "RP", "nano-banana": "Nano Banana",
    "dall-e": "DALL·E", "gpt-oss": "GPT-OSS", "4o": "4o", "ui-tars": "UI-TARS", apodex: "Apodex", ollama: "Ollama",
    auto: "Auto", genspark: "Genspark", kiro: "Kiro", antigravity: "Antigravity",
  };
  const FAMILIES = [
    "claude", "gpt", "chatgpt", "o1", "o3", "o4", "o5", "gemini", "gemma", "grok", "llama", "codellama", "mistral",
    "mixtral", "codestral", "devstral", "magistral", "ministral", "pixtral", "qwen", "qwq", "qvq", "deepseek", "kimi",
    "moonshot", "glm", "minimax", "abab", "yi", "phi", "command", "aya", "nova", "titan", "sonar", "dall", "imagen",
    "flux", "sdxl", "stable", "seedream", "seedance", "doubao", "ernie", "hunyuan", "wanx", "wan", "kling", "veo", "sora",
    "hailuo", "runway", "ideogram", "recraft", "midjourney", "nemotron", "granite", "hermes", "olmo", "falcon", "internlm",
    "internvl", "baichuan", "yandexgpt", "gigachat", "jamba", "lfm", "cogview", "cogvideo", "cogvideox", "whisper", "tts",
    "suno", "udio", "lyria", "kandinsky", "nano", "fable", "mythos", "opus", "sonnet", "haiku", "codex", "llava",
    "moondream", "smollm", "starcoder", "dbrx", "palm", "text", "embedding", "ui", "step", "spark", "apodex", "auto",
    "chat", "playground", "hidream", "kolors", "qwen2", "qwen3", "llama3", "llama4", "gemma2", "gemma3",
  ];
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const FAMILY_RE = new RegExp("^(?:" + [...new Set(FAMILIES)].sort((a, b) => b.length - a.length).map(escRe).join("|") + ")(?:[-\\d.:_/]|$)");
  const HYPHEN_BRANDS = new Set(["gpt", "glm", "phi", "chatgpt", "gpt-oss"]);
  const GLUE_BRANDS = new Set(["qwen", "qwq", "qvq"]);
  const SUFFIX_BADGES = { free: "free", batch: "batch", beta: "beta", extended: "extended", nitro: "nitro",
    floor: "floor", online: "online", exacto: "exacto" };
  const SUFFIX_WORDS = new Set(["thinking", "reasoning", "high", "low", "medium", "minimal", "max", "vision", "fast"]);
  const DATE_FULL = /(?:^|[-_.])((?:19|20)\d{2})-?(0[1-9]|1[0-2])-?([0-2]\d|3[01])$/;
  const DATE_SHORT = /(?:^|[-_.])(0[1-9]|1[0-2])([0-2]\d|3[01])$/;
  const DATE_MY = /(?:^|[-_.])(0[1-9]|1[0-2])-((?:19|20)\d{2})$/;
  const DATE_YYMMDD = /(?:^|[-_.])(2\d)(0[1-9]|1[0-2])([0-2]\d|3[01])$/;
  const DATE_YM = /(?:^|[-_.])((?:19|20)\d{2})-(0[1-9]|1[0-2])$/;
  const VERSION_DASH = /(?<=\d)[-_](?=\d(?:[^0-9bmk]|$))/g;
  const SIZE = /^(?:(\d+)x)?(\d+(?:\.\d+)?)([bmkt])$/;
  const ACTIVE = /^([ae])(\d+(?:\.\d+)?)b$/;
  const QUANT = /(?<![a-z0-9])i?q\d(?:_k)?(?:_[a-z0-9]{1,2})?(?![a-z0-9])/g;
  const QUANT_FULL = /^i?q\d(?:_k)?(?:_[a-z0-9]{1,2})?$/;
  const LETTER_VER = /^([a-z])(\d+(?:\.\d+)*)([a-z]?)$/;
  const BRAND_VER = /^([a-z]+)(\.?)(\d+(?:\.\d+)*)([a-z]*)$/;
  const isAlpha = (c) => !!c && c.toLowerCase() !== c.toUpperCase();

  function stripAccount(text, prefix) {
    if (prefix) {
      const lowText = text.toLowerCase();
      if ((lowText.startsWith(prefix + "-") || lowText.startsWith(prefix + "/")) && text.length > prefix.length + 1) {
        text = text.slice(prefix.length + 1);
      }
    }
    text = text.replace(/^~+/, "");
    if (text.includes("://")) {
      text = text.split("://").slice(1).join("://");
      const parts = text.split("/").filter(Boolean);
      while (parts.length > 1 && /^(?:latest|rc|deprecated|\d{8})$/i.test(parts[parts.length - 1])) parts.pop();
      text = parts.length ? parts[parts.length - 1] : text;
    }
    if (text.includes("/")) {
      const parts = text.split("/").filter(Boolean);
      while (parts.length > 1 && /^(?:latest|rc|deprecated)$/i.test(parts[parts.length - 1])) parts.pop();
      text = parts.length ? parts[parts.length - 1] : text;
    }
    if (!prefix) {
      const low = text.toLowerCase();
      const dash = low.indexOf("-");
      if (dash > 0) {
        const first = low.slice(0, dash), rest = low.slice(dash + 1);
        if (rest && !FAMILY_RE.test(first) && FAMILY_RE.test(rest)) text = text.slice(first.length + 1);
      }
    }
    return text;
  }

  function token(word, index) {
    const low = word.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(BRANDS, low)) return BRANDS[low];
    let m = low.match(SIZE);
    if (m) return (m[1] ? m[1] + "x" : "") + m[2] + m[3].toUpperCase();
    m = low.match(ACTIVE);
    if (m) return m[1].toUpperCase() + m[2] + "B";
    if (/^\d+(?:\.\d+)*[a-z]?$/.test(low)) return low;
    if (/^o\d+$/.test(low) && index === 0) return low;
    m = low.match(LETTER_VER);
    if (m && "vkmrqs".includes(m[1])) return m[1].toUpperCase() + m[2] + m[3];
    m = low.match(BRAND_VER);
    if (m && Object.prototype.hasOwnProperty.call(BRANDS, m[1])) {
      const name = BRANDS[m[1]], dot = m[2], ver = m[3], tail = m[4];
      if (dot) return name + "." + ver + tail;
      if (GLUE_BRANDS.has(m[1])) return name + ver + tail;
      if (HYPHEN_BRANDS.has(m[1])) return name + "-" + ver + tail;
      return name + " " + ver + tail;
    }
    if (/^[a-z]\d$/.test(low)) return low.toUpperCase();
    return isAlpha(word[0]) ? word[0].toUpperCase() + word.slice(1) : word;
  }

  const cache = new Map();
  function parseRaw(raw, prefix) {
    let text = stripAccount(raw, prefix);
    const badges = [], wordsAfter = [];
    if (text.includes("@")) text = text.slice(0, text.indexOf("@"));
    for (;;) {
      const m = text.match(/:([a-z0-9-]+)$/i);
      if (!m) break;
      const tag = m[1].toLowerCase();
      text = text.slice(0, m.index);
      if (Object.prototype.hasOwnProperty.call(SUFFIX_BADGES, tag)) badges.unshift(SUFFIX_BADGES[tag]);
      else if (SUFFIX_WORDS.has(tag)) wordsAfter.unshift(tag);
      else if (/^\d+(?:\.\d+)?[bmk]?$/.test(tag)) wordsAfter.unshift(tag);
      else if (tag === "latest") continue;
      else wordsAfter.unshift(tag);
    }
    for (const ext of [".gguf", ".bin", ".safetensors"]) {
      if (text.toLowerCase().endsWith(ext)) text = text.slice(0, -ext.length);
    }
    let date = "";
    for (let round = 0; round < 2; round++) {
      let m = text.match(DATE_FULL);
      if (m && m.index > 0) { date = `${m[1]}-${m[2]}-${m[3]}`; text = text.slice(0, m.index); continue; }
      m = text.match(DATE_YM);
      if (m && m.index > 0) { date = `${m[1]}-${m[2]}`; text = text.slice(0, m.index); continue; }
      m = text.match(DATE_MY);
      if (m && m.index > 0) { date = `${m[2]}-${m[1]}`; text = text.slice(0, m.index); continue; }
      m = text.match(DATE_YYMMDD);
      if (m && m.index > 0) { date = `20${m[1]}-${m[2]}-${m[3]}`; text = text.slice(0, m.index); continue; }
      m = text.match(DATE_SHORT);
      if (m && m.index > 0 && !/(?:^|[-_.])k\d$/.test(text.slice(0, m.index).toLowerCase())) {
        date = `${m[1]}-${m[2]}`; text = text.slice(0, m.index); continue;
      }
      break;
    }
    text = text.replace(VERSION_DASH, ".");
    let low = text.toLowerCase();
    for (const multi of ["dall-e", "gpt-oss", "nano-banana", "ui-tars"]) {
      low = low.replace(new RegExp("(?<![a-z0-9])" + escRe(multi) + "(?![a-z])", "g"), multi.replace(/-/g, "\u2060"));
    }
    low = low.replace(QUANT, (q) => q.replace(/_/g, "\u2061"));
    let tokens = low.split(/[-_\s]+/).filter(Boolean);
    tokens = tokens.map((t) => (t.includes("\u2061") || QUANT_FULL.test(t)) ? t.replace(/\u2061/g, "_").toUpperCase() : t);
    tokens = tokens.map((t) => t.replace(/\u2060/g, "-"));
    tokens = tokens.concat(wordsAfter);
    const out = [];
    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i], nxt = i + 1 < tokens.length ? tokens[i + 1] : "";
      let shown = token(tok, i);
      if (HYPHEN_BRANDS.has(tok) && nxt && /^\d+(?:\.\d+)*[a-z]{0,2}$/.test(nxt) && !SIZE.test(nxt)) {
        shown = shown + "-" + (nxt.endsWith("v") ? nxt.slice(0, -1) + "V" : nxt); i++;
      } else if (GLUE_BRANDS.has(tok) && nxt && /^\d+(?:\.\d+)*$/.test(nxt)) {
        shown = shown + nxt; i++;
      }
      out.push(shown);
    }
    const name = out.join(" ").trim() || raw;
    return { name, base: text, badges, date };
  }

  function parse(id, opts = {}) {
    const raw = String(id == null ? "" : id).trim();
    const prefix = String(opts.prefix || "").trim().toLowerCase();
    const key = prefix + "\u0000" + raw;
    let found = cache.get(key);
    if (!found) {
      found = parseRaw(raw, prefix);
      if (cache.size > 20000) cache.clear();
      cache.set(key, found);
    }
    return { name: found.name, base: found.base, badges: found.badges.slice(), date: found.date };
  }
  function pretty(id, opts = {}) {
    const alias = opts.aliases && opts.aliases[String(id || "")];
    if (typeof alias === "string" && alias.trim()) return alias.trim();
    return parse(id, opts).name;
  }
  return Object.freeze({ VERSION, parse, pretty });
});
