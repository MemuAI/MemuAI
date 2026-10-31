/* MemuModelSelector — the one model picker for MemuAI modules.
 *
 * Inlined into every module document by app/modules_host.py (like
 * memu-keys.js), so module code can rely on window.MemuModelSelector
 * without bundling anything. It works only with the model catalogue the
 * host already serves through Memu.models(): no hardcoded model list,
 * no keys, no network of its own.
 *
 * Provider names are REAL names from the catalogue. A row whose
 * provider is "custom" (a user-named Custom API connection) is NEVER
 * labelled "custom" / "Custom API": the connection's own name is taken
 * from provider_label / account / the connection name / the API base
 * URL host / the id prefix (see providerName()). If a connection
 * truly has no name, the fallback is "External API".
 *
 * Kinds and badges come ONLY from the catalogue rows the host serves
 * (kind / output_modalities / caps flags: vision, reasoning, tools,
 * code, free). No kind is invented from a model name.
 *
 * Pinned models are SHARED with MemuAI itself: the single source is
 * MemuAI preferences (pinned_models). Inside a module the bridge
 * exposes it through Memu.pinnedModels() / Memu.setModelPinned()
 * (the host writes preferences.json, the same store the main window
 * uses). The "Pinned" section, the star button and getFavorites() /
 * setFavorite() all read and write that shared list, so pinning in
 * MemuAI shows up in every module and vice versa. Without the bridge
 * (preview, standalone tests) a shared localStorage mirror is kept
 * as a fallback, and the public API is unchanged.
 *
 * Quick start (a <select> the module already saves from):
 *
 *   MemuModelSelector.mountSelect(document.querySelector("#model"), {
 *     preset: "dropdown", kind: "chat",
 *     onChange: function (value) { save(); }
 *   });
 *   // The <select> keeps working exactly as before: its value is set
 *   // and a change event is dispatched, so existing save handlers run.
 *   // Options rebuilt later (innerHTML) are picked up automatically.
 *
 * Standalone, rendered into any element:
 *
 *   var picker = MemuModelSelector.create(hostEl, {
 *     preset: "grid", kind: "image", value: saved.model,
 *     load: function () { return Memu.models(); },
 *     onChange: function (value, row) { saved.model = value; Memu.save({ model: value }); }
 *   });
 *   picker.setValue("some-model-id"); picker.refresh(); picker.destroy();
 *
 * Options for create()/mountSelect():
 *   preset: "dropdown" | "inline-list" | "grid" | "cards" | "compact" |
 *     "full" | "circle" | "rail" | "segmented" | "chips" | "table" |
 *     "minimal" (default "dropdown"). Every preset renders the same
 *     rows; the layout, density and grouping differ, see model-selector.css.
 *   kind: "" (all) or "chat" | "image" | "video" | "music" | "audio" |
 *     "stt" | "tts" | "embedding" | "rerank" | "decisions".
 *     (mountSelect() ignores kind: the <select> options are the list.)
 *   models: array of catalogue rows (create() only; may be set later
 *     with picker.setRows()).
 *   load: function returning a Promise of rows (create() only). While
 *     it runs the picker shows a loading state; on failure an error
 *     state with a retry button. No exception escapes into the console.
 *   value: the selected model id ("" = none).
 *   allowEmpty / emptyLabel: offer a "no model / default" row.
 *   search: show the search box (default true when > 6 rows).
 *   favorites / recent: show the pinned (star) and recent sections
 *     (default true). Pinned models are the shared MemuAI list (see
 *     above); recent models are stored per module (localStorage with
 *     an in-memory fallback when storage is unavailable).
 *   storageKey: override the favourites/recent storage key.
 *   label / placeholder / searchPlaceholder / emptyText: strings.
 *   disabled: boolean.
 *   onChange(value, row), onError(error): callbacks.
 *   filter: extra predicate function (row) -> boolean.
 *
 * Styling: the base look follows the module theme tokens
 * (--accent, --panel, --panel-2, --text, --muted, --border, --radius).
 * Override the --ms-* custom properties on the host element to restyle
 * one picker without touching the shared CSS, e.g.
 *   host.style.setProperty("--ms-radius", "16px");
 * Available: --ms-accent, --ms-bg, --ms-panel, --ms-text, --ms-muted,
 * --ms-border, --ms-radius, --ms-font-size, --ms-gap, --ms-row-padding.
 *
 * Helpers (also used by modules that render their own lists):
 *   providerName(row) / providerKey(row) / modelName(row) /
 *   normalize(rows) / groupByProvider(rows) / filterModels(rows, opts) /
 *   kindOf(row) / searchHaystack(row).
 */
(function () {
  "use strict";
  if (window.MemuModelSelector) return;
  var VERSION = "1.0";

  /* ---------- language ---------- */
  function lang() {
    try {
      var m = (window.Memu && window.Memu.lang) || (window.Memu && window.Memu.env && window.Memu.env.lang);
      if (m) return String(m).indexOf("en") === 0 ? "en" : "ru";
    } catch (e) {}
    try {
      if (document.documentElement && document.documentElement.lang === "en") return "en";
      if (typeof navigator !== "undefined" && String(navigator.language || "").indexOf("en") === 0) return "en";
    } catch (e) {}
    return "ru";
  }
  function t(ru, en) { return lang() === "en" ? en : ru; }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }
  function compactNum(n) {
    n = Number(n) || 0;
    if (!n) return "";
    if (n >= 1000000) return (Math.round(n / 100000) / 10) + "M";
    if (n >= 1000) return Math.round(n / 1000) + "K";
    return String(n);
  }

  /* ---------- provider names: real names only, never "custom" ---------- */
  var KNOWN_PROVIDERS = {
    genspark: "Genspark", kiro: "Kiro", antigravity: "Antigravity",
    geminiweb: "Gemini Web", gemini: "Google", grok: "Grok",
    local: "Local", composite: "MemuAI Proxy", openai: "OpenAI",
    anthropic: "Anthropic", google: "Google", xai: "xAI",
    mistral: "Mistral", deepseek: "DeepSeek", meta: "Meta",
    ollama: "Ollama", openrouter: "OpenRouter", together: "Together",
    perplexity: "Perplexity", cohere: "Cohere", ai21: "AI21",
    azure: "Azure", bedrock: "Bedrock", vertex: "Vertex AI",
    huggingface: "Hugging Face", replicate: "Replicate", fal: "fal.ai"
  };
  function isPlaceholderLabel(value) {
    var s = String(value == null ? "" : value).trim().toLowerCase();
    return !s || s === "custom" || s === "custom api" || s === "custom-api" ||
      s === "custom_api" || s === "customapi" || s === "—" || s === "-" || s === "unknown";
  }
  /* A provider name from an API base URL: "https://api.acme.example/v1"
   * -> "Acme". Generic subdomains and bare hosts give nothing, so the
   * caller falls through to the next source instead of inventing one. */
  function domainName(value) {
    var s = String(value == null ? "" : value).trim();
    if (!s) return "";
    var m = s.match(/^(?:[a-z][a-z0-9+.-]*:\/\/)?([^/?#:]+)/i);
    if (!m) return "";
    var host = m[1].toLowerCase();
    if (!host || host === "localhost" || /^\d+\.\d+\.\d+\.\d+$/.test(host)) return "";
    var parts = host.split(".").filter(Boolean);
    while (parts.length > 1 && (parts[0] === "www" || parts[0] === "api" || parts[0] === "app")) parts.shift();
    if (!parts.length || isPlaceholderLabel(parts[0])) return "";
    return prettifyToken(parts[0]);
  }
  function prettifyToken(token) {
    var words = String(token || "").split(/[-_/.]+/).filter(Boolean);
    if (!words.length) return "";
    return words.map(function (w) {
      if (/^[a-z]{1,3}\d/.test(w)) return w.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1);
    }).join(" ");
  }
  function idPrefix(row) {
    if (row && typeof row.prefix === "string" && row.prefix.trim()) return row.prefix.trim();
    var id = String((row && row.id) || "");
    var model = String((row && row.model) || "");
    if (!id) return "";
    if (model && (id === model || id.endsWith("-" + model) || id.endsWith("/" + model))) {
      var cut = id.slice(0, id.length - model.length).replace(/[-/]+$/, "");
      if (cut) return cut;
    }
    /* "genspark-claude-opus-5": the first segment is the provider/connection. */
    var first = id.split(/[/:]/)[0].split("-")[0];
    return first || "";
  }
  /* The id prefix as a provider name, or "" when it is itself a
   * placeholder ("custom-..."): such an id names no provider, and
   * returning "Custom" for it would be exactly the label this
   * component must never show. */
  function providerPrefix(row) {
    var prefix = idPrefix(row);
    if (!prefix || isPlaceholderLabel(prefix)) return "";
    return prefix;
  }
  function providerName(row) {
    row = row || {};
    var candidates = [];
    if (!isPlaceholderLabel(row.provider_label)) candidates.push(String(row.provider_label).trim());
    if (!isPlaceholderLabel(row.account)) candidates.push(String(row.account).trim());
    var connection = row.connection_name || row.connection || row.account_name;
    if (!isPlaceholderLabel(connection)) candidates.push(String(connection).trim());
    var domain = domainName(row.base_url || row.baseUrl || row.api_url || row.url || row.host || row.endpoint);
    if (domain) candidates.push(domain);
    var prefix = providerPrefix(row);
    if (prefix) {
      var known = KNOWN_PROVIDERS[prefix.toLowerCase()];
      candidates.push(known || prettifyToken(prefix));
    }
    var providerToken = String(row.provider || "").trim().split(/[/:]/)[0].trim();
    if (!isPlaceholderLabel(providerToken)) {
      candidates.push(KNOWN_PROVIDERS[providerToken.toLowerCase()] || prettifyToken(providerToken));
    }
    for (var i = 0; i < candidates.length; i++) {
      if (candidates[i] && !isPlaceholderLabel(candidates[i])) return candidates[i];
    }
    return t("Внешний API", "External API");
  }
  function providerKey(row) {
    var name = providerName(row);
    if (name === "Внешний API" || name === "External API") {
      var raw = String((row && (row.account || row.connection_name)) || "").trim().toLowerCase();
      if (raw) return "external:" + raw;
      var prefix = providerPrefix(row);
      if (prefix) return "external:" + prefix.toLowerCase();
      return "external";
    }
    return name.toLowerCase();
  }
  /* A stable colour accent per provider: a deterministic hue from the
   * provider key, so every provider gets its own colour without any
   * brand palette being invented here. */
  function providerHue(key) {
    var s = String(key || "");
    var hue = 0;
    for (var i = 0; i < s.length; i++) hue = (hue * 31 + s.charCodeAt(i)) % 360;
    return hue;
  }
  function modelName(row) {
    row = row || {};
    var name = String(row.name || "").trim();
    if (name && name !== String(row.id || "")) return name;
    var id = String(row.id || "");
    var prefix = idPrefix(row);
    var tail = id;
    if (prefix && id.toLowerCase().indexOf(prefix.toLowerCase() + "-") === 0) tail = id.slice(prefix.length + 1);
    else if (prefix && id.toLowerCase().indexOf(prefix.toLowerCase() + "/") === 0) tail = id.slice(prefix.length + 1);
    return prettifyToken(tail) || id;
  }

  /* ---------- kinds: catalogue data only ---------- */
  /* The kind comes from the catalogue row itself (kind, output
   * modalities, caps flags). A model name is never parsed for a kind:
   * the canonical kinds are chat / image / video / music / audio /
   * stt / tts / embedding / rerank / decisions, and the capabilities
   * vision / reasoning / tools / code ride on caps (see badgesHtml). */
  var KIND_ALIASES = {
    text: "chat", decision: "decisions", decisions: "decisions",
    choice: "decisions",
    embeddings: "embedding", embedding: "embedding", embed: "embedding",
    reranker: "rerank", reranking: "rerank",
    transcription: "stt", "speech-to-text": "stt", speech: "stt",
    "text-to-speech": "tts", "speech-synthesis": "tts",
    audio: "audio", music: "music",
    "image-generation": "image", "text-to-image": "image",
    "video-generation": "video", "text-to-video": "video",
    "music-generation": "music", "audio-generation": "audio",
    images: "image", videos: "video"
  };
  var VALID_KINDS = ["chat", "image", "video", "music", "audio", "stt", "tts", "embedding", "rerank", "decisions"];
  function normalizeKind(value) {
    var declared = String(value || "").trim().toLowerCase().replace(/[_\s]+/g, "-");
    if (KIND_ALIASES[declared]) return KIND_ALIASES[declared];
    if (VALID_KINDS.indexOf(declared) >= 0) return declared;
    return "";
  }
  function outputModalities(row) {
    var caps = (row && row.caps) || {};
    var fields = caps.fields || {};
    var fieldOut = fields.output && fields.output.value;
    var raw = (row && row.architecture && row.architecture.output_modalities) ||
      (row && row.output_modalities) || caps.output_modalities || caps.out || caps.outputs || fieldOut;
    if (typeof raw === "string") return raw.toLowerCase().split(/[,+\s]+/).filter(Boolean);
    if (Array.isArray(raw)) return raw.map(function (v) { return String(v).toLowerCase(); }).filter(Boolean);
    return [];
  }
  function kindOf(row) {
    if (!row) return "chat";
    var caps = row.caps || {};
    var sources = [row.kind, caps.kind, caps.fields && caps.fields.kind];
    for (var i = 0; i < sources.length; i++) {
      var src = sources[i];
      if (src && typeof src === "object") src = src.value;
      var declared = normalizeKind(src);
      if (declared) return declared;
    }
    var outputs = outputModalities(row);
    if (outputs.indexOf("decisions") >= 0 || outputs.indexOf("choice") >= 0) return "decisions";
    if (outputs.indexOf("embeddings") >= 0 || outputs.indexOf("embedding") >= 0) return "embedding";
    if (outputs.indexOf("scores") >= 0) return "rerank";
    if (outputs.length && outputs.indexOf("text") < 0) {
      if (outputs.indexOf("image") >= 0) return "image";
      if (outputs.indexOf("video") >= 0) return "video";
      if (outputs.indexOf("music") >= 0) return "music";
      if (outputs.indexOf("audio") >= 0 || outputs.indexOf("speech") >= 0 || outputs.indexOf("voice") >= 0) return "audio";
    }
    if (outputs.indexOf("text") >= 0 && outputs.indexOf("video") < 0 && outputs.indexOf("image") < 0) return "chat";
    if (row.image === true || caps.image === true) return "image";
    if (row.video === true || caps.video === true) return "video";
    if (row.music === true || caps.music === true) return "music";
    if (row.audio === true || caps.audio === true) return "audio";
    if (row.stt === true || caps.stt === true || caps.transcription === true) return "stt";
    if (row.tts === true || caps.tts === true) return "tts";
    if (row.embedding === true || caps.embedding === true) return "embedding";
    if (row.rerank === true || caps.rerank === true) return "rerank";
    if (row.decisions === true || caps.decisions === true) return "decisions";
    return "chat";
  }
  var KIND_LABELS = {
    chat: ["Текст", "Text"], image: ["Картинки", "Images"], video: ["Видео", "Video"],
    music: ["Музыка", "Music"], audio: ["Аудио", "Audio"], stt: ["Транскрипция", "Transcription"],
    tts: ["Озвучка", "Speech"], embedding: ["Эмбеддинги", "Embeddings"],
    rerank: ["Реранкинг", "Reranking"], decisions: ["Решения", "Decisions"]
  };
  function kindLabel(kind) {
    var pair = KIND_LABELS[kind] || [kind, kind];
    return lang() === "en" ? pair[1] : pair[0];
  }

  function searchHaystack(row) {
    var n = normalizeOne(row);
    return [n.id, n.name, n.displayName, n.providerName, n.providerKey,
      n.rawModel, n.account, n.kind].join(" ").toLowerCase();
  }
  function normalizeOne(row) {
    var copy = {};
    if (row && typeof row === "object") {
      Object.keys(row).forEach(function (k) { copy[k] = row[k]; });
    }
    copy.id = String(copy.id || "");
    /* The bridge appends " · [IMAGE]" and friends to the name of a
     * non-chat row; the kind badge already says that, so the suffix
     * would only duplicate it in the row title. */
    copy.name = String(copy.name || copy.id).replace(/\s·\s\[[A-Z0-9_-]+\]$/, "");
    if (!copy.name) copy.name = String(copy.id || "");
    copy.pinned = copy.pinned === true;
    copy.displayName = modelName(copy);
    copy.providerName = providerName(copy);
    copy.providerKey = providerKey(copy);
    copy.rawModel = String(copy.model || "");
    copy.account = String(copy.account || "");
    copy.kind = kindOf(copy);
    copy.context = Number(copy.context || (copy.caps && copy.caps.context) || 0) || 0;
    return copy;
  }
  function normalize(rows) {
    return (Array.isArray(rows) ? rows : []).filter(function (r) { return r && r.id; }).map(normalizeOne);
  }
  function groupByProvider(rows) {
    var groups = [], byKey = {};
    normalize(rows).forEach(function (row) {
      var key = row.providerKey;
      if (!byKey[key]) { byKey[key] = { key: key, name: row.providerName, rows: [] }; groups.push(byKey[key]); }
      byKey[key].rows.push(row);
    });
    groups.sort(function (a, b) { return a.name.localeCompare(b.name); });
    groups.forEach(function (g) { g.rows.sort(function (a, b) { return a.displayName.localeCompare(b.displayName); }); });
    return groups;
  }
  function filterModels(rows, opts) {
    opts = opts || {};
    var list = normalize(rows);
    if (opts.kind) list = list.filter(function (r) { return r.kind === opts.kind; });
    if (typeof opts.filter === "function") list = list.filter(opts.filter);
    if (opts.query) {
      var toks = String(opts.query).toLowerCase().split(/[\s,/_\-:]+/).filter(Boolean);
      list = list.filter(function (r) {
        var hay = searchHaystack(r);
        return toks.every(function (tk) { return hay.indexOf(tk) >= 0; });
      });
    }
    return list;
  }

  /* ---------- pinned / recent: shared pins, per-module recent ---------- */
  /* Pinned models live in MemuAI preferences (pinned_models) — one
   * list for the main window and every module. Inside a module the
   * bridge (Memu.pinnedModels / Memu.setModelPinned, see
   * app/modules_host.py) reads and writes that store; the host
   * serialises the read-modify-write, so two toggles cannot silently
   * drop each other. The localStorage mirror below is only a cache:
   * it lets getFavorites() answer synchronously before the first
   * bridge round-trip, and it keeps pinning working in previews and
   * standalone documents, where there is no bridge at all. An explicit
   * storageKey keeps the old per-key behaviour (callers that asked
   * for their own list keep it). Recent models stay per module. */
  var SHARED_PIN_KEY = "memu-model-selector:shared-pinned";
  var pinCache = null;
  var pinWriteQueue = Promise.resolve();
  function hasBridge() {
    try {
      return !!(window.Memu && typeof window.Memu.pinnedModels === "function" &&
        typeof window.Memu.setModelPinned === "function");
    } catch (e) { return false; }
  }
  function sharedMode(opts) { return !(opts && opts.storageKey); }
  function cleanPinList(ids) {
    var seen = {};
    return (Array.isArray(ids) ? ids : []).map(function (v) { return String(v || ""); })
      .filter(function (v) { if (!v || seen[v]) return false; seen[v] = true; return true; }).slice(0, 100);
  }
  function readMirror() {
    try {
      var raw = window.localStorage.getItem(SHARED_PIN_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      if (parsed && Array.isArray(parsed.pinned)) return cleanPinList(parsed.pinned);
    } catch (e) {}
    return [];
  }
  function writeMirror(ids) {
    try { window.localStorage.setItem(SHARED_PIN_KEY, JSON.stringify({ pinned: ids })); } catch (e) {}
  }
  function pinCacheIds() {
    if (pinCache) return pinCache.ids.slice();
    pinCache = { ids: readMirror(), bridge: false };
    return pinCache.ids.slice();
  }
  function setPinCache(ids, fromBridge) {
    ids = cleanPinList(ids);
    pinCache = { ids: ids, bridge: !!fromBridge };
    writeMirror(ids);
    return ids.slice();
  }
  /* Re-read the shared list from the bridge and refresh the cache.
   * Resolves with the current ids; never throws. */
  function syncPins(opts) {
    if (!sharedMode(opts) || !hasBridge()) return Promise.resolve(getFavorites(opts));
    return pinWriteQueue.then(function () {
      return Promise.resolve().then(function () { return window.Memu.pinnedModels(); })
        .then(function (res) {
          var ids = res && Array.isArray(res.pinned) ? res.pinned : (Array.isArray(res) ? res : null);
          if (ids) return setPinCache(ids, true);
          return pinCacheIds();
        })
        .catch(function () { return pinCacheIds(); });
    });
  }
  /* Catalogue rows may carry pinned=true (the model catalogue marks
   * the shared list on each row). Adopt those ids into the cache so
   * a picker fed with catalogue rows shows the section at once, even
   * before the bridge round-trip; ids are only ever added here, never
   * dropped — an unpin goes through the bridge. */
  function mergeRowPins(rows, opts) {
    if (!sharedMode(opts)) return;
    var pinned = normalize(rows).filter(function (r) { return r.pinned; }).map(function (r) { return r.id; });
    if (!pinned.length) return;
    var cached = pinCacheIds();
    setPinCache(pinned.concat(cached.filter(function (id) { return pinned.indexOf(id) < 0; })), false);
  }

  var memoryStore = {};
  function storageKeyFor(opts) {
    if (opts && opts.storageKey) return String(opts.storageKey);
    var mod = "module";
    try { mod = (window.Memu && window.Memu.env && window.Memu.env.module) || (window.Memu && window.Memu.manifest && window.Memu.manifest.id) || "module"; } catch (e) {}
    return "memu-model-selector:" + String(mod);
  }
  function loadStored(key) {
    try {
      var raw = window.localStorage.getItem(key);
      var parsed = raw ? JSON.parse(raw) : null;
      if (parsed && typeof parsed === "object") return parsed;
    } catch (e) {}
    return memoryStore[key] || { favorites: [], recent: [] };
  }
  function saveStored(key, data) {
    memoryStore[key] = data;
    try { window.localStorage.setItem(key, JSON.stringify(data)); } catch (e) {}
  }
  function getFavorites(opts) {
    if (sharedMode(opts)) return pinCacheIds();
    return (loadStored(storageKeyFor(opts)).favorites || []).slice();
  }
  function setFavorite(id, on, opts) {
    if (sharedMode(opts)) {
      var ids = pinCacheIds().filter(function (x) { return x !== id; });
      if (on) ids.unshift(id);
      ids = setPinCache(ids, false);
      if (hasBridge()) {
        pinWriteQueue = pinWriteQueue.then(function () {
          return Promise.resolve().then(function () { return window.Memu.setModelPinned(id, on); })
            .then(function (res) {
              var server = res && Array.isArray(res.pinned) ? res.pinned : null;
              if (server) setPinCache(server, true);
            })
            .catch(function () {});
        });
      }
      return ids.slice();
    }
    var key = storageKeyFor(opts);
    var data = loadStored(key);
    var favs = (data.favorites || []).filter(function (x) { return x !== id; });
    if (on) favs.unshift(id);
    data.favorites = favs.slice(0, 50);
    saveStored(key, data);
    return data.favorites.slice();
  }
  function getRecent(opts) { return (loadStored(storageKeyFor(opts)).recent || []).slice(); }
  function noteRecent(id, opts) {
    if (!id) return;
    var key = storageKeyFor(opts);
    var data = loadStored(key);
    data.recent = [id].concat((data.recent || []).filter(function (x) { return x !== id; })).slice(0, 6);
    saveStored(key, data);
  }

  /* ---------- row rendering ---------- */
  function badgesHtml(row) {
    var out = [];
    var caps = row.caps || {};
    out.push('<span class="ms-badge ms-kind" data-kind="' + esc(row.kind) + '">' + esc(kindLabel(row.kind)) + "</span>");
    if (row.kind === "chat") {
      var vision = caps.vision === true || row.vision_state === "yes" || row.vision === true;
      if (vision) out.push('<span class="ms-badge" title="' + esc(t("Видит изображения", "Sees images")) + '">◉</span>');
      if (caps.reasoning === true || caps.thinking === true || row.reasoning || row.thinking) {
        out.push('<span class="ms-badge" title="' + esc(t("Рассуждает", "Reasons")) + '">✦</span>');
      }
      if (caps.tools === true || row.tools === true) {
        out.push('<span class="ms-badge" title="' + esc(t("Вызывает инструменты", "Calls tools")) + '">⚙</span>');
      }
      if (caps.code === true || row.code === true) {
        out.push('<span class="ms-badge" title="' + esc(t("Пишет код", "Writes code")) + '">&lt;/&gt;</span>');
      }
    }
    if (caps.free === true || (row.badges || []).indexOf("free") >= 0) out.push('<span class="ms-badge ms-free">' + esc(t("бесплатно", "free")) + "</span>");
    if (row.default) out.push('<span class="ms-badge ms-default">' + esc(t("основная", "default")) + "</span>");
    return out.length ? '<span class="ms-badges">' + out.join("") + "</span>" : "";
  }
  function rowButtonHtml(row, opts, state) {
    var selected = state.value === row.id;
    var fav = state.favorites.indexOf(row.id) >= 0;
    var hue = providerHue(row.providerKey);
    var ctx = row.context ? '<span class="ms-ctx">' + esc(compactNum(row.context)) + "</span>" : "";
    var sub = opts.preset === "table"
      ? '<span class="ms-cell ms-provider">' + esc(row.providerName) + "</span>"
      : '<span class="ms-id">' + esc(row.id) + "</span>";
    return '<button type="button" role="option" aria-selected="' + selected + '" data-ms-model="' + esc(row.id) + '" ' +
      'data-kind="' + esc(row.kind) + '" data-provider-key="' + esc(row.providerKey) + '" style="--ms-prov-h:' + hue + '" ' +
      'class="ms-row' + (selected ? " sel" : "") + '" title="' + esc(row.id) + '">' +
      (opts.showFavorites ? '<span class="ms-fav' + (fav ? " on" : "") + '" data-ms-fav="' + esc(row.id) + '" role="button" tabindex="0" aria-label="' + esc(fav ? t("Открепить", "Unpin") : t("Закрепить", "Pin")) + '" title="' + esc(fav ? t("Открепить", "Unpin") : t("Закрепить", "Pin")) + '">' + (fav ? "★" : "☆") + "</span>" : "") +
      '<span class="ms-mono" aria-hidden="true">' + esc((row.displayName || "?").trim().charAt(0).toUpperCase()) + "</span>" +
      '<span class="ms-text"><span class="ms-name">' + esc(row.displayName) + ctx + "</span>" + sub + "</span>" +
      badgesHtml(row) +
      (selected ? '<span class="ms-check" aria-hidden="true">✓</span>' : "") +
      "</button>";
  }
  function emptyRowHtml(opts) {
    return '<button type="button" role="option" aria-selected="false" data-ms-model="" class="ms-row ms-empty-row"><span class="ms-text"><span class="ms-name">' + esc(opts.emptyLabel || t("Модель по умолчанию MemuAI", "MemuAI default model")) + "</span></span></button>";
  }
  function sectionHtml(title, rows, opts, state, collapsible, cls, isProvider) {
    if (!rows.length) return "";
    var hueAttr = isProvider && rows.length ? ' data-prov="1" style="--ms-prov-h:' + providerHue(rows[0].providerKey) + '"' : "";
    var head = title
      ? (collapsible
        ? '<button type="button" class="ms-sec-h" data-ms-sec aria-expanded="true"><span class="ms-chev" aria-hidden="true">▾</span><span>' + esc(title) + '</span><small>' + rows.length + "</small></button>"
        : '<div class="ms-sec-h static"><span>' + esc(title) + "</span><small>" + rows.length + "</small></div>")
      : "";
    return '<section class="ms-sec' + (cls ? " " + cls : "") + '"' + hueAttr + ">" + head + '<div class="ms-sec-body">' + rows.map(function (r) { return rowButtonHtml(r, opts, state); }).join("") + "</div></section>";
  }
  function listInnerHtml(rows, opts, state) {
    var searching = !!(state.query || "").trim();
    var parts = [];
    if (opts.allowEmpty && !searching) parts.push(emptyRowHtml(opts));
    var favRows = [], recentRows = [], rest = rows.slice();
    if (!searching && opts.showFavorites) {
      favRows = rows.filter(function (r) { return state.favorites.indexOf(r.id) >= 0; });
      favRows.sort(function (a, b) { return state.favorites.indexOf(a.id) - state.favorites.indexOf(b.id); });
    }
    if (!searching && opts.showRecent) {
      recentRows = state.recent.map(function (id) { return rows.filter(function (r) { return r.id === id; })[0]; })
        .filter(function (r) { return r && favRows.indexOf(r) < 0; });
    }
    var used = {};
    favRows.concat(recentRows).forEach(function (r) { used[r.id] = true; });
    rest = rows.filter(function (r) { return !used[r.id]; });
    if (favRows.length) parts.push(sectionHtml(t("Закреплённые", "Pinned"), favRows, opts, state, false, "ms-sec-pinned", false));
    if (recentRows.length) parts.push(sectionHtml(t("Недавние", "Recent"), recentRows, opts, state, false, "ms-sec-recent", false));
    if (opts.preset === "segmented") {
      parts.push(segmentedHtml(rest, opts, state));
    } else if (opts.groupByProvider === false) {
      parts.push('<div class="ms-flat">' + rest.map(function (r) { return rowButtonHtml(r, opts, state); }).join("") + "</div>");
    } else {
      groupByProvider(rest).forEach(function (g) {
        parts.push(sectionHtml(g.name, g.rows, opts, state, rest.length > 8, "ms-sec-prov", true));
      });
    }
    if (!parts.length || (!rows.length && !opts.allowEmpty)) {
      return '<div class="ms-empty">' + esc(state.loading ? t("Загрузка моделей…", "Loading models…")
        : state.error ? t("Не удалось загрузить модели", "Could not load models")
        : (opts.emptyText || t("Подходящих моделей нет", "No suitable models"))) + "</div>";
    }
    return parts.join("");
  }
  function segmentedHtml(rows, opts, state) {
    var groups = groupByProvider(rows);
    if (!groups.length) return "";
    var active = state.segment || groups[0].key;
    if (!groups.some(function (g) { return g.key === active; })) active = groups[0].key;
    state.segment = active;
    var tabs = groups.map(function (g) {
      return '<button type="button" role="tab" aria-selected="' + (g.key === active) + '" data-ms-segment="' + esc(g.key) + '" class="ms-tab' + (g.key === active ? " on" : "") + '">' + esc(g.name) + " <small>" + g.rows.length + "</small></button>";
    }).join("");
    var current = groups.filter(function (g) { return g.key === active; })[0];
    return '<div class="ms-segmented"><div class="ms-tabs" role="tablist">' + tabs + "</div>" +
      '<div class="ms-sec-body">' + current.rows.map(function (r) { return rowButtonHtml(r, opts, state); }).join("") + "</div></div>";
  }
  function selectedRow(state) {
    if (!state.value) return null;
    return state.rows.filter(function (r) { return r.id === state.value; })[0] || null;
  }
  function triggerLabel(state, opts) {
    if (state.loading) return t("Загрузка моделей…", "Loading models…");
    if (state.error) return t("Модели недоступны", "Models unavailable");
    var row = selectedRow(state);
    if (row) return row.displayName;
    if (!state.value && opts.allowEmpty) return opts.emptyLabel || t("Модель по умолчанию MemuAI", "MemuAI default model");
    if (state.value) return state.value;
    return opts.placeholder || t("Выберите модель", "Choose a model");
  }

  /* ---------- shared renderer: fills one host element ---------- */
  var PRESETS = ["dropdown", "inline-list", "grid", "cards", "compact", "full",
    "circle", "rail", "segmented", "chips", "table", "minimal"];
  function validPreset(p) { return PRESETS.indexOf(p) >= 0 ? p : "dropdown"; }

  function renderPicker(host, opts, state) {
    var preset = validPreset(opts.preset);
    host.classList.add("ms");
    host.setAttribute("data-preset", preset);
    host.setAttribute("data-ms-state", state.loading ? "loading" : state.error ? "error" : "ready");
    var label = opts.label ? '<span class="ms-label">' + esc(opts.label) + "</span>" : "";
    var search = "";
    if (opts.search && (preset === "dropdown" || preset === "full" || preset === "grid" || preset === "cards" || preset === "table" || preset === "rail" || state.rows.length > 6)) {
      search = '<div class="ms-search"><span aria-hidden="true">⌕</span><input type="search" class="ms-search-input" autocomplete="off" spellcheck="false" placeholder="' + esc(opts.searchPlaceholder || t("Поиск: название, ID или провайдер", "Search: name, ID or provider")) + '" aria-label="' + esc(t("Поиск модели", "Search models")) + '" value="' + esc(state.query || "") + '"></div>';
    }
    if (preset === "dropdown" || preset === "minimal") {
      var selRow = selectedRow(state);
      host.innerHTML = label +
        '<button type="button" class="ms-trigger" aria-haspopup="listbox" aria-expanded="false" ' + (opts.disabled ? "disabled" : "") + ">" +
        '<span class="ms-trigger-text">' + esc(triggerLabel(state, opts)) + "</span>" +
        (selRow ? '<span class="ms-badge ms-kind ms-trigger-kind" data-kind="' + esc(selRow.kind) + '">' + esc(kindLabel(selRow.kind)) + "</span>" : "") +
        (selRow ? '<span class="ms-trigger-provider">' + esc(selRow.providerName) + "</span>" : "") +
        '<span class="ms-caret" aria-hidden="true">▾</span></button>' +
        '<div class="ms-pop" role="listbox" hidden>' + search +
        '<div class="ms-list">' + listInnerHtml(state.rows, opts, state) + "</div>" +
        (state.error ? '<div class="ms-error"><span>' + esc(state.errorText || t("Список моделей не загрузился. Проверьте MemuAI Proxy и попробуйте снова.", "The model list did not load. Check MemuAI Proxy and try again.")) + '</span><button type="button" class="ms-retry">' + esc(t("Повторить", "Retry")) + "</button></div>" : "") +
        "</div>";
      return;
    }
    if (preset === "circle") {
      host.innerHTML = label + search +
        '<div class="ms-circle-wrap"><div class="ms-circle-center"><b>' + esc(triggerLabel(state, opts)) + "</b><span>" + esc(selectedRow(state) ? selectedRow(state).providerName : t("модель не выбрана", "no model selected")) + "</span></div>" +
        '<div class="ms-list ms-circle" role="listbox">' + listInnerHtml(state.rows, opts, state) + "</div></div>" +
        (state.error ? '<div class="ms-error"><span>' + esc(state.errorText || "") + '</span><button type="button" class="ms-retry">' + esc(t("Повторить", "Retry")) + "</button></div>" : "");
      positionCircle(host, state);
      return;
    }
    host.innerHTML = label + search +
      '<div class="ms-list" role="listbox">' + listInnerHtml(state.rows, opts, state) + "</div>" +
      (state.error ? '<div class="ms-error"><span>' + esc(state.errorText || "") + '</span><button type="button" class="ms-retry">' + esc(t("Повторить", "Retry")) + "</button></div>" : "") +
      (state.loading ? '<div class="ms-loading" aria-live="polite"><span class="ms-spinner"></span>' + esc(t("Загрузка моделей…", "Loading models…")) + "</div>" : "");
    if (preset === "table") {
      var list = host.querySelector(".ms-list");
      if (list && state.rows.length) {
        list.insertAdjacentHTML("afterbegin", '<div class="ms-table-head" aria-hidden="true"><span>' + esc(t("Модель", "Model")) + "</span><span>" + esc(t("Провайдер", "Provider")) + "</span><span>" + esc(t("Контекст", "Context")) + "</span></div>");
      }
    }
  }
  function positionCircle(host, state) {
    /* Radial layout: rows orbit the centre. With many models the orbit
     * would crowd, so beyond 14 rows the CSS falls back to a grid. */
    var wrap = host.querySelector(".ms-circle");
    if (!wrap) return;
    var rows = wrap.querySelectorAll(".ms-row");
    if (rows.length > 14) { wrap.classList.add("ms-circle-many"); return; }
    var w = wrap.clientWidth || 420;
    var radius = Math.max(90, Math.min(170, w * 0.36));
    wrap.style.setProperty("--ms-orbit", radius + "px");
    rows.forEach(function (row, i) {
      var angle = (360 / rows.length) * i - 90;
      row.style.setProperty("--ms-angle", angle + "deg");
    });
  }

  function bindPicker(host, opts, state, callbacks) {
    function currentRows() {
      return filterModels(state.allRows, { kind: opts.kind, filter: opts.filter, query: state.query });
    }
    function redraw(keepFocus) {
      var active = document.activeElement;
      var searchValue = "";
      var input = host.querySelector(".ms-search-input");
      if (input) searchValue = input.value;
      state.rows = currentRows();
      state.favorites = getFavorites(opts);
      state.recent = getRecent(opts);
      renderPicker(host, opts, state);
      bindPickerEvents(host, opts, state, callbacks, redraw);
      if (keepFocus) {
        var next = host.querySelector(".ms-search-input");
        if (next && searchValue) { next.value = searchValue; next.focus(); try { next.setSelectionRange(next.value.length, next.value.length); } catch (e) {} }
        else if (active && active.dataset && active.dataset.msModel) {
          var again = host.querySelector('[data-ms-model="' + active.dataset.msModel + '"]');
          if (again) again.focus();
        }
      }
    }
    function pick(id) {
      var row = state.allRows.filter(function (r) { return r.id === id; })[0] || null;
      if (id) noteRecent(id, opts);
      state.value = id;
      redraw(false);
      if (typeof opts.onChange === "function") { try { opts.onChange(id, row ? normalizeOne(row) : null); } catch (e) {} }
      if (typeof callbacks.onPick === "function") callbacks.onPick(id, row);
    }
    function toggleFav(id, el) {
      var on = state.favorites.indexOf(id) < 0;
      setFavorite(id, on, opts);
      redraw(true);
      if (el) { var again = host.querySelector('[data-ms-fav="' + id + '"]'); if (again) again.focus(); }
    }
    function bindPickerEvents() {
      var searchInput = host.querySelector(".ms-search-input");
      if (searchInput) searchInput.addEventListener("input", function () { state.query = searchInput.value; redraw(true); });
      var trigger = host.querySelector(".ms-trigger");
      var pop = host.querySelector(".ms-pop");
      if (trigger && pop) {
        trigger.addEventListener("click", function () {
          var open = pop.hidden;
          pop.hidden = !open;
          trigger.setAttribute("aria-expanded", String(open));
          if (open) {
            var input = host.querySelector(".ms-search-input");
            if (input) input.focus();
          }
        });
      }
      host.querySelectorAll("[data-ms-sec]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var sec = btn.closest(".ms-sec");
          if (!sec) return;
          var collapsed = sec.classList.toggle("collapsed");
          btn.setAttribute("aria-expanded", String(!collapsed));
        });
      });
      host.querySelectorAll("[data-ms-segment]").forEach(function (btn) {
        btn.addEventListener("click", function () { state.segment = btn.dataset.msSegment; redraw(false); });
      });
      host.querySelectorAll("[data-ms-fav]").forEach(function (fav) {
        fav.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); toggleFav(fav.dataset.msFav, fav); });
        fav.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleFav(fav.dataset.msFav, fav); } });
      });
      host.querySelectorAll("[data-ms-model]").forEach(function (rowEl) {
        rowEl.addEventListener("click", function () {
          if (opts.disabled) return;
          var id = rowEl.dataset.msModel;
          pick(id);
          if (pop) { pop.hidden = true; if (trigger) trigger.setAttribute("aria-expanded", "false"); }
        });
      });
      var retry = host.querySelector(".ms-retry");
      if (retry) retry.addEventListener("click", function () { if (typeof callbacks.onRetry === "function") callbacks.onRetry(); });
    }
    /* Keyboard + outside-click are bound ONCE: bindPickerEvents runs on
     * every redraw, and re-binding here would stack listeners. */
    host.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var rows = Array.prototype.slice.call(host.querySelectorAll(".ms-row"));
      if (!rows.length) return;
      var i = rows.indexOf(document.activeElement);
      e.preventDefault();
      var next = e.key === "ArrowDown" ? Math.min(rows.length - 1, i + 1) : Math.max(0, i - 1);
      rows[next < 0 ? 0 : next].focus();
    });
    var outside = function (e) {
      var pop = host.querySelector(".ms-pop");
      var trigger = host.querySelector(".ms-trigger");
      if (pop && trigger && !pop.hidden && !host.contains(e.target)) {
        pop.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      }
    };
    document.addEventListener("pointerdown", outside, true);
    host._msOutside = outside;
    redraw(false);
    return { redraw: redraw, pick: pick };
  }

  function create(host, opts) {
    opts = opts || {};
    /* Documented defaults: favourites, recent and search are on unless
     * the caller opts out — without this the star and the sections
     * never render, because listInnerHtml only checks the flags. */
    if (opts.showFavorites === undefined) opts.showFavorites = true;
    if (opts.showRecent === undefined) opts.showRecent = true;
    if (opts.search === undefined) opts.search = true;
    if (!host) throw new Error("MemuModelSelector.create: host element is required");
    var state = {
      allRows: normalize(opts.models || []),
      rows: [],
      value: String(opts.value == null ? "" : opts.value),
      query: "",
      loading: false,
      error: false,
      errorText: "",
      favorites: getFavorites(opts),
      recent: getRecent(opts),
      segment: ""
    };
    var destroyed = false;
    var api;
    function load() {
      if (typeof opts.load !== "function") return Promise.resolve(state.allRows);
      state.loading = true; state.error = false; state.errorText = "";
      api.redraw(false);
      return Promise.resolve()
        .then(function () { return opts.load(); })
        .then(function (rows) {
          if (destroyed) return;
          mergeRowPins(rows, opts);
          state.allRows = normalize(rows);
          state.loading = false;
          /* A saved value that no longer exists falls back to "" only
           * when the caller asked for it (keepSaved=false default keeps
           * the value so the UI can show it as unavailable). */
          api.redraw(false);
        })
        .catch(function (error) {
          if (destroyed) return;
          state.loading = false; state.error = true;
          state.errorText = String((error && error.message) || error || "");
          api.redraw(false);
          if (typeof opts.onError === "function") { try { opts.onError(error); } catch (e) {} }
        });
    }
    api = {
      setRows: function (rows) { mergeRowPins(rows, opts); state.allRows = normalize(rows); state.loading = false; state.error = false; api.redraw(false); },
      setValue: function (value, silent) {
        state.value = String(value == null ? "" : value);
        api.redraw(false);
        if (!silent && typeof opts.onChange === "function") {
          var row = state.allRows.filter(function (r) { return r.id === state.value; })[0] || null;
          try { opts.onChange(state.value, row ? normalizeOne(row) : null); } catch (e) {}
        }
      },
      getValue: function () { return state.value; },
      getRows: function () { return state.allRows.slice(); },
      refresh: load,
      destroy: function () {
        destroyed = true;
        if (host._msOutside) { try { document.removeEventListener("pointerdown", host._msOutside, true); } catch (e) {} }
        host.innerHTML = "";
        host.removeAttribute("data-preset");
      }
    };
    var bound = bindPicker(host, opts, state, { onRetry: load });
    api.redraw = bound.redraw;
    /* Shared pins: refresh from the bridge once mounted (the mirror
     * cache already painted), then stay in sync while the picker
     * lives — a pin changed in MemuAI or another module repaints this
     * picker without a reload. All listeners are removed on destroy. */
    var pinUnsub = null;
    var onStoragePin = null;
    var lastPinSync = 0;
    function refreshPinsIfChanged(ids) {
      if (destroyed) return;
      var before = (state.favorites || []).join("\u0000");
      state.favorites = getFavorites(opts);
      if (state.favorites.join("\u0000") !== before) api.redraw(false);
    }
    if (sharedMode(opts)) {
      syncPins(opts).then(function (ids) { if (!destroyed) refreshPinsIfChanged(ids); });
      try {
        if (window.Memu && typeof window.Memu.on === "function") {
          pinUnsub = window.Memu.on("model-pins", function (data) {
            if (data && Array.isArray(data.pinned)) { setPinCache(data.pinned, true); refreshPinsIfChanged(data.pinned); }
          });
        }
      } catch (e) {}
      try {
        onStoragePin = function (event) {
          if (event && event.key === SHARED_PIN_KEY && event.newValue) {
            try {
              var parsed = JSON.parse(event.newValue);
              if (parsed && Array.isArray(parsed.pinned)) { setPinCache(parsed.pinned, false); refreshPinsIfChanged(parsed.pinned); }
            } catch (e) {}
          }
        };
        window.addEventListener("storage", onStoragePin);
      } catch (e) {}
      var onFocusPin = function () {
        var now = Date.now();
        if (now - lastPinSync < 1000) return;
        lastPinSync = now;
        syncPins(opts).then(function (ids) { if (!destroyed) refreshPinsIfChanged(ids); });
      };
      host._msFocusPin = onFocusPin;
      try { window.addEventListener("focus", onFocusPin); } catch (e) {}
    }
    var origDestroy = api.destroy;
    api.destroy = function () {
      destroyed = true;
      try { if (typeof pinUnsub === "function") pinUnsub(); } catch (e) {}
      try { if (onStoragePin) window.removeEventListener("storage", onStoragePin); } catch (e) {}
      try { if (host._msFocusPin) window.removeEventListener("focus", host._msFocusPin); } catch (e) {}
      origDestroy();
    };
    api.syncPins = function () { return syncPins(opts).then(function (ids) { if (!destroyed) refreshPinsIfChanged(ids); return ids; }); };
    if (typeof opts.load === "function") load();
    return api;
  }

  /* ---------- enhance an existing <select> ---------- */
  function mountSelect(select, opts) {
    opts = opts || {};
    if (!select || select.tagName !== "SELECT") throw new Error("MemuModelSelector.mountSelect: a <select> is required");
    if (select.dataset.msMounted === "1") return select._msHandle || null;
    select.dataset.msMounted = "1";
    var host = document.createElement("div");
    host.className = "ms-mount";
    select.parentNode.insertBefore(host, select);
    select.classList.add("ms-native-hidden");
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;

    function rowsFromSelect() {
      var rows = [];
      var groups = {};
      select.querySelectorAll("optgroup").forEach(function (g) {
        g.querySelectorAll("option").forEach(function (o) { groups[o.value] = g.label; });
      });
      Array.prototype.slice.call(select.options).forEach(function (o) {
        if (!o.value && !opts.allowEmpty) {
          /* Keep a placeholder option out of the model list. */
          if (o.text && /loading|загруз|none|нет|выберите|choose/i.test(o.text)) return;
        }
        rows.push({
          id: o.value,
          kind: (o.dataset && o.dataset.kind) || "",
          /* Option text is usually "Name · id · provider": the first
           * segment is the display name; the rest is catalogue detail
           * the selector re-derives itself. */
          name: String(o.textContent || o.text || "").split("·")[0].trim(),
          provider_label: (o.dataset && (o.dataset.providerLabel || o.dataset.provider)) || groups[o.value] || "",
          provider: "",
          default: /основная|default/i.test(o.textContent || "")
        });
      });
      return rows;
    }
    var suppress = false;
    /* The <select>'s own options are already the curated list (the
     * module filtered by kind when it built them), so no kind filter
     * is applied here: rows from <option> elements carry no kind flags
     * and filtering would empty the list. */
    var picker = create(host, Object.assign({}, opts, {
      kind: "",
      models: rowsFromSelect(),
      value: select.value,
      onChange: function (value) {
        if (suppress) return;
        if (select.value !== value) {
          select.value = value;
          select.dispatchEvent(new Event("input", { bubbles: true }));
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        if (typeof opts.onChange === "function") { try { opts.onChange(value); } catch (e) {} }
      }
    }));
    function syncFromSelect() {
      suppress = true;
      picker.setRows(rowsFromSelect());
      picker.setValue(select.value, true);
      suppress = false;
    }
    select.addEventListener("change", function () {
      if (suppress) return;
      picker.setValue(select.value, true);
    });
    var observer = null;
    try {
      observer = new MutationObserver(function () { syncFromSelect(); });
      observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["value", "disabled"] });
    } catch (e) {}
    var handle = {
      sync: syncFromSelect,
      setValue: function (v) { select.value = String(v == null ? "" : v); syncFromSelect(); },
      getValue: function () { return select.value; },
      destroy: function () {
        if (observer) observer.disconnect();
        picker.destroy();
        host.remove();
        select.classList.remove("ms-native-hidden");
        select.removeAttribute("aria-hidden");
        select.tabIndex = 0;
        delete select.dataset.msMounted;
      }
    };
    select._msHandle = handle;
    return handle;
  }

  /* ---------- standalone list (multi-select, custom pickers) ---------- */
  function renderList(container, rows, opts) {
    opts = opts || {};
    if (!container) throw new Error("MemuModelSelector.renderList: container is required");
    var state = {
      allRows: normalize(rows),
      rows: [],
      value: "",
      query: opts.query || "",
      loading: false, error: false, errorText: "",
      favorites: getFavorites(opts), recent: getRecent(opts), segment: ""
    };
    var selected = new Set(opts.selected || []);
    var listOpts = Object.assign({}, opts, {
      preset: validPreset(opts.preset || "full"),
      search: opts.search !== false,
      showFavorites: opts.showFavorites !== false,
      showRecent: opts.showRecent !== false,
      onChange: null
    });
    var alive = true;
    function draw(keepSearchFocus) {
      state.favorites = getFavorites(opts);
      state.recent = getRecent(opts);
      state.rows = filterModels(state.allRows, { kind: opts.kind, filter: opts.filter, query: state.query });
      container.classList.add("ms");
      container.setAttribute("data-preset", listOpts.preset);
      var searchHtml = "";
      if (listOpts.search && state.allRows.length > 6) {
        searchHtml = '<div class="ms-search"><span aria-hidden="true">⌕</span><input type="search" class="ms-search-input" autocomplete="off" spellcheck="false" placeholder="' + esc(opts.searchPlaceholder || t("Поиск: название, ID или провайдер", "Search: name, ID or provider")) + '" aria-label="' + esc(t("Поиск модели", "Search models")) + '" value="' + esc(state.query || "") + '"></div>';
      }
      container.innerHTML = searchHtml + '<div class="ms-list" role="listbox" aria-multiselectable="' + (!!opts.multi) + '">' +
        listInnerHtml(state.rows, listOpts, state) + "</div>";
      var searchInput = container.querySelector(".ms-search-input");
      if (searchInput) {
        searchInput.addEventListener("input", function () { state.query = searchInput.value; draw(true); });
        if (keepSearchFocus) {
          searchInput.focus();
          try { searchInput.setSelectionRange(searchInput.value.length, searchInput.value.length); } catch (e) {}
        }
      }
      container.querySelectorAll("[data-ms-model]").forEach(function (el) {
        var id = el.dataset.msModel;
        if (selected.has(id)) el.classList.add("sel");
        el.setAttribute("aria-selected", String(selected.has(id)));
        el.addEventListener("click", function (e) {
          if (e.target.closest && e.target.closest("[data-ms-fav]")) return;
          if (opts.multi) {
            if (selected.has(id)) selected.delete(id); else selected.add(id);
          } else {
            selected.clear(); selected.add(id);
          }
          if (id) noteRecent(id, opts);
          draw();
          if (typeof opts.onToggle === "function") { try { opts.onToggle(id, Array.from(selected)); } catch (err) {} }
          if (typeof opts.onChange === "function") { try { opts.onChange(id, Array.from(selected)); } catch (err2) {} }
        });
      });
      container.querySelectorAll("[data-ms-fav]").forEach(function (fav) {
        fav.addEventListener("click", function (e) {
          e.preventDefault(); e.stopPropagation();
          setFavorite(fav.dataset.msFav, state.favorites.indexOf(fav.dataset.msFav) < 0, opts);
          state.favorites = getFavorites(opts);
          draw();
        });
      });
    }
    draw();
    if (sharedMode(opts)) {
      mergeRowPins(rows, opts);
      syncPins(opts).then(function () { if (alive) draw(); });
    }
    return {
      setRows: function (rows) { mergeRowPins(rows, opts); state.allRows = normalize(rows); draw(); },
      setSelected: function (ids) { selected = new Set(ids || []); draw(); },
      getSelected: function () { return Array.from(selected); },
      setQuery: function (q) { state.query = String(q || ""); draw(); },
      syncPins: function () { return syncPins(opts).then(function (ids) { if (alive) draw(); return ids; }); },
      destroy: function () { alive = false; container.innerHTML = ""; }
    };
  }

  window.MemuModelSelector = Object.freeze({
    VERSION: VERSION,
    PRESETS: PRESETS.slice(),
    create: create,
    mountSelect: mountSelect,
    renderList: renderList,
    providerName: providerName,
    providerKey: providerKey,
    modelName: modelName,
    kindOf: kindOf,
    kindLabel: kindLabel,
    normalize: normalize,
    groupByProvider: groupByProvider,
    filterModels: filterModels,
    searchHaystack: searchHaystack,
    getFavorites: getFavorites,
    setFavorite: setFavorite,
    getPinned: getFavorites,
    setPinned: setFavorite,
    syncPins: syncPins,
    providerHue: providerHue,
    getRecent: getRecent,
    noteRecent: noteRecent
  });
})();
