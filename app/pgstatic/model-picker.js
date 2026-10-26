

(function () {
  "use strict";
  if (window.MemuModelPicker) return;
  const VERSION = "1.0-r33";

  const lang = () => {
    try { if (typeof PG_LANG !== "undefined") return PG_LANG; } catch (e) {}
    try { return localStorage.getItem("memu_lang") === "en" ? "en" : (document.documentElement.lang === "en" ? "en" : "ru"); } catch (e) { return "ru"; }
  };
  const en = () => lang() === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVG = {
    code: '<svg viewBox="0 0 24 24"><path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16"/></svg>',
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.9 10.9 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.8 8.4 2 12 2 12s3.6 7 10 7a10.6 10.6 0 0 0 5.4-1.5"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>',
    eyeQ: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7c2.2 0 4 .8 5.5 1.8M22 12s-1 2-3 3.9"/><path d="M2 12s3.6 7 10 7"/><circle cx="12" cy="12" r="3"/><path d="M17.5 13.6a2.2 2.2 0 1 1 3 2.1c-.5.2-.8.6-.8 1.1v.3"/><path d="M19.7 20h.01"/></svg>',
    brain: '<svg viewBox="0 0 24 24"><path d="M9 4a3 3 0 0 0-3 3v.2A3 3 0 0 0 4 10a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3V4z"/><path d="M15 4a3 3 0 0 1 3 3v.2A3 3 0 0 1 20 10a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3V4z"/></svg>',
    tools: '<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/></svg>',
    image: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/></svg>',
    video: '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10.5 5-3v9l-5-3z"/></svg>',
    music: '<svg viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
    pin: '<svg viewBox="0 0 24 24"><path d="m14 4 6 6-3 1-4 4-1 5-2-6-6-2 5-1 4-4z"/><path d="m4 20 6-6"/></svg>',
    more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    chev: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    gift: '<svg viewBox="0 0 24 24"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v9H5v-9M7.5 8a2.5 2.5 0 1 1 0-5C10 3 12 8 12 8s2-5 4.5-5a2.5 2.5 0 1 1 0 5"/></svg>',
  };
  const svg = (name, cls = "") => `<span class="mmp-ic ${cls}" aria-hidden="true">${SVG[name] || ""}</span>`;
  const tokens = (q) => String(q || "").toLowerCase().split(/[\s,/_\-:]+/).filter(Boolean);
  const matches = (hay, toks) => !toks.length || toks.every((tk) => hay.includes(tk));

  let extRows = null, extPrefs = null;
  function rows() {
    try { if (typeof S !== "undefined" && Array.isArray(S.models) && S.models.length) return S.models; } catch (e) {}
    return extRows || [];
  }
  function prefsView() {
    if (typeof pref === "function") {
      try {
        return { aliases: pref("model_aliases", {}) || {}, pinned: pref("pinned_models", []) || [],
          hidden: pref("hidden_models", []) || [], def: pref("default_model", "") || "" };
      } catch (e) {}
    }
    return extPrefs || { aliases: {}, pinned: [], hidden: [], def: "" };
  }
  function setRows(list, prefs) {
    extRows = Array.isArray(list) ? list : [];
    const aliases = {};
    extRows.forEach((r) => { if (r && r.alias) aliases[r.id] = r.alias; });
    extPrefs = { aliases: (prefs && prefs.aliases) || aliases, pinned: (prefs && prefs.pinned) || extRows.filter((r) => r.pinned).map((r) => r.id),
      hidden: (prefs && prefs.hidden) || extRows.filter((r) => r.hidden).map((r) => r.id), def: (prefs && prefs.default) || (extRows.find((r) => r.default) || {}).id || "" };
    refreshAll();
  }
  async function load(params = {}) {
    const q = new URLSearchParams({ all: "1" });
    if (params.kind) q.set("kind", params.kind);
    if (params.need) q.set("need", params.need);
    const resp = await fetch("/api/pg/model-catalog?" + q.toString());
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    setRows(data.models || [], { default: data.default, pinned: data.pinned });
    return extRows;
  }
  const byId = new Map();
  let byIdSource = null, byIdLen = -1;
  function find(id) {
    const list = rows();
    if (list !== byIdSource || list.length !== byIdLen) {
      byId.clear(); list.forEach((r) => r && byId.set(r.id, r)); byIdSource = list; byIdLen = list.length;
    }
    return byId.get(String(id || "")) || null;
  }
  function autoName(id, row) {
    if (row && row.name) return row.name;
    try { if (window.MemuModelNames) return window.MemuModelNames.pretty(id, { prefix: row && row.prefix }); } catch (e) {}
    return String(id || "");
  }
  function name(id) {
    const alias = (prefsView().aliases || {})[id];
    if (typeof alias === "string" && alias.trim()) return alias.trim();
    return autoName(id, find(id));
  }
  function label(id) { const n = name(id); return n && n !== id ? `${n} · ${id}` : String(id || ""); }
  function kindOf(row) {
    if (!row) return "chat";
    const ids=[row.raw_model,row.id].filter(Boolean);
    if(ids.some(id=>/(?:^|[-_\/])jev(?:[-_.]?(?:v?\d|latest)|$)|(?:^|[-\/])(?:jaredpalmer\/kev-4b|respan\/span-01(?:-lite)?|upstage\/solar-decide|inception\/mercury-decide|togethercomputer\/tev1-4b-experimental)(?::free)?$/i.test(id)))return "decisions";
    const c = row.caps || {};
    const fields = c.fields || {};
    const rawOutputs=row.architecture?.output_modalities || row.output_modalities || c.out || c.output_modalities || fields.output?.value || [];
    const outputs=(Array.isArray(rawOutputs)?rawOutputs:String(rawOutputs).split(/[,+\s]+/)).map(v=>String(v).toLowerCase()).filter(Boolean);
    if(outputs.includes('decisions'))return 'decisions';
    if(outputs.includes('embeddings') || outputs.includes('embedding'))return 'embedding';
    if(outputs.includes('scores'))return 'rerank';
    const aliases={text:'chat',decision:'decisions',embeddings:'embedding',reranker:'rerank',reranking:'rerank',transcription:'stt','speech-to-text':'stt','text-to-speech':'tts'};
    const declared=String(row.kind || c.kind || fields.kind?.value || '').toLowerCase(), kind=aliases[declared] || declared;
    if(['image','video','music','audio','stt','tts','embedding','rerank','decisions'].includes(kind))return kind;
    if(outputs.length && !outputs.includes('text'))for(const value of ['image','video','audio','music'])if(outputs.includes(value))return value;
    if(outputs.includes('text') && !outputs.includes('video') && !outputs.includes('image')) {
      if(ids.some(id=>/(?<![a-z0-9])(?:whisper|transcribe|stt|asr|parakeet)(?![a-z])/i.test(id)))return 'stt';
      return 'chat';
    }
    const isSummaryCaps = ('effective' in c || 'reference' in c || 'video_input' in c || 'audio_input' in c || 'fields' in c);
    if (!isSummaryCaps && kind !== "chat") {
      const chatLike = !!(c.vision || c.tools || c.reasoning || c.thinking || c.structured || c.json || c.web || c.code || c.caching);
      if (!chatLike) {
        if (c.video) return "video";
        if (c.image) return "image";
        if (c.music && c.text !== true) return "music";
      }
    }
    return "chat";
  }
  const KIND_WARN_LABELS = {
    image: ["генерации изображений", "image generation"],
    video: ["генерации видео", "video generation"],
    music: ["генерации музыки", "music generation"],
    audio: ["работы со звуком", "audio processing"],
    tts: ["озвучки текста (TTS)", "speech synthesis (TTS)"],
    stt: ["распознавания речи (STT)", "speech recognition (STT)"],
    embedding: ["создания эмбеддингов", "embeddings"],
    rerank: ["реранкинга", "reranking"],
    decisions: ["принятия решений (да/нет)", "yes/no decisions"],
  };
  function warnIfSpecialized(idOrRow, expectedKind = "chat") {
    const row = typeof idOrRow === "object" && idOrRow ? idOrRow : find(idOrRow);
    if (!row) return false;
    const k = kindOf(row);
    if (!expectedKind || k === expectedKind) return false;
    const meta = KIND_WARN_LABELS[k] || [k, k];
    const msg = t(
      `Внимание: «${name(row.id)}» — модель другого типа (${meta[0]}). В текстовом чате она может работать некорректно.`,
      `Warning: “${name(row.id)}” is a model of another type (${meta[1]}). It may not work in a regular text chat.`
    );
    try { if (typeof toast === "function") toast(msg, "warn"); } catch (e) {}
    return true;
  }
  const SRC_TEXT = {
    probe: ["проверено настоящим запросом", "checked with a real request"],
    user: ["указано вами", "set by you"],
    local: ["по настройкам локальной модели", "from the local model settings"],
    adapter: ["этот провайдер не передаёт изображения", "this provider cannot pass images"],
    provider: ["по данным провайдера", "according to the provider"],
    catalog: ["по каталогу моделей", "from the model catalogue"],
    family: ["по семейству модели — не проверено", "by model family — not checked"],
    openrouter: ["по каталогу OpenRouter", "from the OpenRouter catalogue"],
    "models.dev": ["по каталогу Models.dev", "from the Models.dev catalogue"],
    heuristic: ["по названию — не проверено", "by name — not checked"],
    kind: ["модель генерации", "a generation model"],
    none: ["нет сведений", "no information"],
  };
  function vision(idOrRow) {
    const row = typeof idOrRow === "object" && idOrRow ? idOrRow : find(idOrRow);
    const caps = (row && row.caps) || {};
    let state = caps.vision_state;
    if (!state) state = caps.vision === false ? "no" : caps.vision === true && !caps.vision_guess ? "yes" : "unknown";
    const src = caps.vision_src || (state === "unknown" ? "none" : "provider");
    const why = (SRC_TEXT[src] || SRC_TEXT.none)[en() ? 1 : 0];
    const text = state === "yes" ? t("Видит изображения", "Sees images") : state === "no" ? t("Не видит изображения", "Cannot see images")
      : t("Зрение неизвестно", "Vision unknown");
    return { state, src, text, hint: `${text} · ${why}`, firm: ["probe", "user", "provider", "local", "adapter", "kind"].includes(src) };
  }

  const HUES = { claude: 22, opus: 22, sonnet: 22, haiku: 22, gpt: 158, chatgpt: 158, o1: 158, o3: 158, o4: 158, gemini: 214, gemma: 214,
    qwen: 262, qwq: 262, deepseek: 228, glm: 196, kimi: 330, grok: 0, llama: 205, mistral: 32, mixtral: 32, minimax: 345,
    flux: 285, sora: 158, veo: 214, wanx: 262, wan: 262, apodex: 120, yandexgpt: 48, gigachat: 140, auto: 40 };
  function monogram(row) {
    let base = String((row && row.raw_model) || "");
    if (!base) { try { base = window.MemuModelNames ? window.MemuModelNames.parse(row && row.id, { prefix: row && row.prefix }).base : String(row && row.id || ""); } catch (e) { base = String(row && row.id || ""); } }
    base = base.toLowerCase().split("/").pop().replace(/^[^a-z]+/, "");
    const word = (autoName(row && row.id, row) || base || "?").trim();
    const fam = (base.match(/^[a-z]+/) || [""])[0];
    let hue = HUES[fam];
    if (hue == null) { hue = 0; for (const ch of fam || word) hue = (hue * 31 + ch.charCodeAt(0)) % 360; }
    const letter = (word.match(/[A-Za-zА-Яа-я0-9]/) || ["?"])[0].toUpperCase();
    const neutral = fam === "grok" || fam === "auto";
    return `<span class="mmp-mono${neutral ? " neutral" : ""}" style="--mmp-h:${hue}" aria-hidden="true">${esc(letter)}</span>`;
  }
  function badgesHtml(row, opts = {}) {
    if (!row) return "";
    const caps = row.caps || {};
    const kind = kindOf(row);
    const out = [];
    const taskKinds={image:["image","Картинки","Images"],video:["video","Видео","Video"],music:["music","Музыка","Music"],audio:["music","Аудио","Audio"],tts:["music","Озвучка","Speech synthesis"],stt:["music","Транскрипция","Transcription"],embedding:["code","Эмбеддинги","Embeddings"],rerank:["more","Реранкинг","Reranking"],decisions:["brain","Решения","Decisions"]};
    if (kind !== "chat") {const meta=taskKinds[kind] || ["code",kind,kind];const caption=t(meta[1],meta[2]);out.push(`<span class="mmp-b mmp-kind" title="${esc(caption)}" aria-label="${esc(caption)}">${svg(meta[0])}<em>${esc(caption)}</em></span>`);}
    else {
      const v = vision(row);
      const cls = v.state === "yes" ? "v-yes" : v.state === "no" ? "v-no" : "v-unk";
      out.push(`<span class="mmp-b mmp-v ${cls}${v.firm ? " firm" : ""}" title="${esc(v.hint)}" aria-label="${esc(v.text)}">${svg(v.state === "yes" ? "eye" : v.state === "no" ? "eyeOff" : "eyeQ")}</span>`);
      if (caps.reasoning === true || caps.thinking === true) out.push(`<span class="mmp-b" title="${esc(t("Умеет рассуждать", "Can reason"))}">${svg("brain")}</span>`);
      if (caps.tools === true) out.push(`<span class="mmp-b" title="${esc(t("Вызывает инструменты", "Calls tools"))}">${svg("tools")}</span>`);
    }
    const free = caps.free === true || (row.badges || []).includes("free");
    if (free && !opts.noFree) out.push(`<span class="mmp-free" title="${esc(t("Бесплатная модель", "Free model"))}">${esc(t("free", "free"))}</span>`);
    return `<span class="mmp-badges">${out.join("")}</span>`;
  }
  function rowHtml(row, opts = {}) {
    const id = String(row.id || "");
    const shown = name(id);
    const pinned = (opts.pinnedSet || new Set(prefsView().pinned)).has(id);
    const selected = opts.value === id;
    const bad = opts.need === "vision" && kindOf(row) === "chat" && vision(row).state === "no";
    const date = row.date ? `<span class="mmp-date">${esc(row.date)}</span>` : "";
    const isDefault = opts.showDefault && prefsView().def === id;
    const sub = [esc(id)];
    if (opts.showAccount && row.account) sub.push(`<span class="mmp-acc">${esc(row.account)}</span>`);
    return `<div class="mmp-row dd-item${selected ? " sel" : ""}${bad ? " mmp-dim" : ""}" role="option" aria-selected="${selected}" data-model="${esc(id)}"${bad ? ` data-mmp-novision="1"` : ""} title="${esc(id)}">
      ${opts.pin ? `<button type="button" class="model-pin ${pinned ? "active" : ""}" data-model-pin="${esc(id)}" title="${esc(pinned ? t("Открепить", "Unpin") : t("Закрепить", "Pin"))}" aria-label="${esc(pinned ? t("Открепить", "Unpin") : t("Закрепить", "Pin"))}">${svg("pin")}</button>` : ""}
      ${monogram(row)}
      <span class="mmp-text model-dd-name"><span class="mmp-name model-id-full">${esc(shown)}${date}${isDefault ? `<small class="mmp-default">${esc(t("Основная", "Default"))}</small>` : ""}</span><span class="mmp-id model-original">${sub.join(" · ")}</span>${bad ? `<span class="mmp-why">${esc(t("Не видит изображения", "Cannot see images"))}</span>` : ""}</span>
      ${badgesHtml(row, { noKind: opts.noKind })}
      ${selected && !opts.options ? `<span class="mmp-check">${svg("check")}</span>` : ""}
      ${opts.options ? `<button type="button" class="model-options" data-model-options="${esc(id)}" title="${esc(t("Настроить модель", "Model options"))}" aria-label="${esc(t("Настроить модель", "Model options"))}">${svg("more")}</button>` : ""}
    </div>`;
  }

  function recent() { try { return JSON.parse(localStorage.getItem("memu_model_recent") || "[]").filter((x) => typeof x === "string"); } catch (e) { return []; } }
  function noteRecent(id) {
    if (!id) return;
    try { localStorage.setItem("memu_model_recent", JSON.stringify([id, ...recent().filter((x) => x !== id)].slice(0, 8))); } catch (e) {}
  }
  function collapsed() { try { return JSON.parse(localStorage.getItem("memu_mmp_collapsed") || "{}") || {}; } catch (e) { return {}; } }
  function setCollapsed(key, on) {
    const all = collapsed(); if (on) all[key] = 1; else delete all[key];
    try { localStorage.setItem("memu_mmp_collapsed", JSON.stringify(all)); } catch (e) {}
  }
  function haystack(row) {
    const id = String(row.id || "");
    return (id + " " + name(id) + " " + autoName(id, row) + " " + (row.account || "") + " " + (row.provider || "") + " "
      + (row.provider_label || "") + " " + (row.raw_model || "") + " " + kindOf(row)).toLowerCase();
  }
  const FILTERS = {
    vision: { label: () => t("Со зрением", "Vision"), icon: "eye", test: (r) => kindOf(r) === "chat" && vision(r).state === "yes" },
    reasoning: { label: () => t("Рассуждают", "Reason"), icon: "brain", test: (r) => (r.caps || {}).reasoning === true || (r.caps || {}).thinking === true },
    free: { label: () => t("Бесплатные", "Free"), icon: "gift", test: (r) => (r.caps || {}).free === true || (r.badges || []).includes("free") },
    media: { label: () => t("Специальные", "Specialized"), icon: "image", test: (r) => kindOf(r) !== "chat" },
  };
  function filterChips(active, available) {
    return `<div class="mmp-chips" role="toolbar">${available.map((key) => `<button type="button" class="mmp-chip${active.has(key) ? " on" : ""}" data-mmp-filter="${key}" aria-pressed="${active.has(key)}">${svg(FILTERS[key].icon)}<span>${esc(FILTERS[key].label())}</span></button>`).join("")}</div>`;
  }

  function listHtml(list, opts = {}) {
    const toks = tokens(opts.query || "");
    const active = opts.filters || new Set();
    const shown = list.filter((r) => r && matches(haystack(r), toks) && [...active].every((k) => !FILTERS[k] || FILTERS[k].test(r)));
    if (!shown.length) return `<div class="mmp-empty dd-empty">${esc(opts.emptyText || t("Ничего не найдено", "Nothing found"))}</div>`;
    const pinnedSet = new Set(prefsView().pinned);
    const kinds = new Set(shown.map(kindOf));
    const rowOpts = { ...opts, pinnedSet, noKind: kinds.size === 1 && !kinds.has("chat") };
    const used = new Set();
    const sections = [];
    const searching = toks.length > 0;
    const head = (key, title, icon, count, collapsible) => {
      const isCollapsed = collapsible && !searching && collapsed()[key];
      return { key, title, icon, count, collapsible, isCollapsed };
    };
    if (opts.sections !== false && !searching) {
      const pins = shown.filter((r) => pinnedSet.has(r.id));
      pins.sort((a, b) => prefsView().pinned.indexOf(a.id) - prefsView().pinned.indexOf(b.id));
      if (pins.length && shown.length > 6) { sections.push({ ...head("pinned", t("Закреплённые", "Pinned"), "pin", pins.length, false), rows: pins }); pins.forEach((r) => used.add(r.id)); }
      const rec = recent().map((id) => shown.find((r) => r.id === id)).filter((r) => r && !used.has(r.id)).slice(0, 4);
      if (rec.length && shown.length > 10) { sections.push({ ...head("recent", t("Недавние", "Recent"), "clock", rec.length, false), rows: rec }); rec.forEach((r) => used.add(r.id)); }
    }
    const groups = new Map();
    const order = {chat:0,image:1,video:2,music:3,audio:4,tts:5,stt:6,embedding:7,rerank:8,decisions:9};
    for (const r of shown) {
      if (used.has(r.id)) continue;
      const acc = String(r.account || r.provider_label || r.provider || "—");
      if (!groups.has(acc)) groups.set(acc, []);
      groups.get(acc).push(r);
    }
    const many = groups.size > 1 || sections.length > 0;
    for (const [acc, items] of groups) {
      items.sort((a, b) => (order[kindOf(a)] ?? 10) - (order[kindOf(b)] ?? 10));
      sections.push({ ...head("acc:" + acc, acc, "", items.length, many && opts.collapsible !== false), rows: items });
    }
    const showHeads = opts.sections !== false && (many || sections.length > 1);
    return sections.map((s) => `<div class="mmp-sec${s.isCollapsed ? " collapsed" : ""}" data-mmp-sec="${esc(s.key)}">
      ${showHeads ? (s.collapsible ? `<button type="button" class="mmp-sec-h" data-mmp-toggle="${esc(s.key)}" aria-expanded="${!s.isCollapsed}">${svg("chev", "mmp-chev")}<span>${esc(s.title)}</span><small>${s.count}</small></button>`
        : `<div class="mmp-sec-h static">${s.icon ? svg(s.icon) : ""}<span>${esc(s.title)}</span><small>${s.count}</small></div>`) : ""}
      <div class="mmp-sec-body">${s.rows.map((r) => rowHtml(r, rowOpts)).join("")}</div></div>`).join("");
  }

  function bindSections(box, onToggle) {
    box.querySelectorAll("[data-mmp-toggle]").forEach((btn) => btn.onclick = (e) => {
      e.preventDefault(); e.stopPropagation();
      const sec = btn.closest(".mmp-sec");
      const on = !sec.classList.contains("collapsed");
      sec.classList.toggle("collapsed", on);
      btn.setAttribute("aria-expanded", String(!on));
      setCollapsed(btn.dataset.mmpToggle, on);
      if (onToggle) onToggle();
    });
  }

  let openState = null;
  function close(result = null, reason = "api") {
    const st = openState;
    if (!st) return;
    openState = null;
    try { window.MemuLayers?.close(st.pop, "api"); } catch (e) {}
    st.pop.remove();
    st.anchorEl?.setAttribute?.("aria-expanded", "false");
    if (st.anchorEl && reason !== "outside" && st.anchorEl.isConnected) { try { st.anchorEl.focus({ preventScroll: true }); } catch (e) {} }
    st.resolve(result);
  }
  function rowsFor(opts) {
    const hidden = new Set(opts.includeHidden ? [] : prefsView().hidden);
    let list = rows().filter((r) => r && r.id && (opts.ids ? opts.ids.includes(r.id) : !hidden.has(r.id)));
    if (opts.ids) {
      list = opts.ids.map((id) => find(id) || { id, caps: {}, account: opts.groupOf?.(id) || "" });
    }
    const kind = opts.kind || "";
    if (kind === "text") list = list.filter((r) => kindOf(r) === "chat");
    else if (kind) list = list.filter((r) => kindOf(r) === kind);
    if (typeof opts.filter === "function") list = list.filter(opts.filter);
    return list;
  }
  function open(anchor, opts = {}) {
    if (openState) close(null, "replaced");
    return new Promise((resolve) => {
      const pop = document.createElement("div");
      pop.className = "mmp-pop";
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", opts.title || t("Выбор модели", "Choose a model"));
      const list = rowsFor(opts);
      const chipKeys = Object.keys(FILTERS).filter((k) => {
        if (k === "media" && (opts.kind && opts.kind !== "")) return false;
        if (k === "vision" && (opts.kind === "image" || opts.kind === "video")) return false;
        const n = list.filter((r) => FILTERS[k].test(r)).length;
        return n > 0 && n < list.length;
      });
      const active = new Set(opts.filters || []);
      const special = (opts.special || []).filter((s) => s && s.value !== undefined);
      pop.innerHTML = `${opts.title ? `<div class="mmp-title">${esc(opts.title)}</div>` : ""}
        <div class="mmp-search">${svg("search")}<input type="text" autocomplete="off" spellcheck="false" placeholder="${esc(t("Поиск: название, id или аккаунт", "Search: name, id or account"))}" aria-label="${esc(t("Поиск модели", "Search models"))}"></div>
        ${list.length > 8 && chipKeys.length ? filterChips(active, chipKeys) : ""}
        <div class="mmp-list dd-list" role="listbox"></div>
        ${opts.footer ? `<div class="mmp-foot">${opts.footer}</div>` : ""}`;
      document.body.appendChild(pop);
      const input = pop.querySelector("input");
      const box = pop.querySelector(".mmp-list");
      const draw = () => {
        const q = input.value;
        const specials = special.filter((s) => matches(String(s.label + " " + s.value).toLowerCase(), tokens(q)));
        box.innerHTML = (specials.length ? `<div class="mmp-sec mmp-special">${specials.map((s) => `<div class="mmp-row dd-item mmp-special-row${opts.value === s.value ? " sel" : ""}" role="option" data-model="${esc(s.value)}" data-mmp-special="1">
            <span class="mmp-text"><span class="mmp-name">${esc(s.label)}</span>${s.hint ? `<span class="mmp-id">${esc(s.hint)}</span>` : ""}</span>${opts.value === s.value ? `<span class="mmp-check">${svg("check")}</span>` : ""}</div>`).join("")}</div>` : "")
          + listHtml(list, { query: q, filters: active, value: opts.value, need: opts.need, sections: opts.sections, showAccount: !!q,
            emptyText: list.length ? "" : (opts.emptyText || t("Подходящих моделей нет", "No suitable models")) });
        bindSections(box);
        box.querySelectorAll("[data-model]").forEach((el) => el.onclick = (e) => {
          e.stopPropagation();
          if (el.dataset.mmpNovision === "1" && opts.need === "vision" && opts.blockNoVision) return;
          const id = el.dataset.model;
          if (!el.dataset.mmpSpecial) noteRecent(id);
          close(id);
        });
        const sel = box.querySelector(".mmp-row.sel");
        if (sel) { sel.classList.add("active", "kb"); try { sel.scrollIntoView({ block: "nearest" }); } catch (e) {} }
      };
      pop.querySelectorAll("[data-mmp-filter]").forEach((chip) => chip.onclick = (e) => {
        e.stopPropagation();
        const key = chip.dataset.mmpFilter;
        if (active.has(key)) active.delete(key); else active.add(key);
        chip.classList.toggle("on", active.has(key)); chip.setAttribute("aria-pressed", String(active.has(key)));
        draw(); input.focus();
      });
      input.addEventListener("input", draw);
      input.addEventListener("keydown", (e) => {
        const items = Array.from(box.querySelectorAll(".mmp-row[data-model]")).filter((n) => n.offsetParent !== null);
        let i = items.findIndex((n) => n.classList.contains("active"));
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          i = i < 0 ? (e.key === "ArrowDown" ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
          items.forEach((n, j) => { n.classList.toggle("active", j === i); n.classList.toggle("kb", j === i); });
          try { items[i]?.scrollIntoView({ block: "nearest" }); } catch (err) {}
        } else if (e.key === "Enter") {
          e.preventDefault();
          (items[i] || items[0])?.click();
        } else if (e.key === "Escape") {
          e.preventDefault(); e.stopPropagation(); close(null, "escape");
        }
      });
      pop.addEventListener("pointerdown", (e) => e.stopPropagation());
      pop.addEventListener("click", (e) => e.stopPropagation());
      const anchorEl = anchor && anchor.getBoundingClientRect ? anchor : null;
      openState = { pop, resolve, anchorEl };
      draw();
      const place = () => {
        const width = Math.min(Math.max(opts.width || 460, anchorEl ? anchorEl.getBoundingClientRect().width : 0), 560, innerWidth - 16);
        pop.style.width = width + "px";
        if (anchorEl && window.MemuLayers) {
          window.MemuLayers.place(pop, anchorEl, { maxHeight: Math.min(opts.maxHeight || 470, innerHeight - 24), minWidth: width });
          pop.style.width = width + "px";
          pop.style.overflowY = "hidden";
        } else {
          const r = anchorEl ? anchorEl.getBoundingClientRect() : { left: anchor?.x || 40, bottom: anchor?.y || 40, top: anchor?.y || 40 };
          const h = Math.min(opts.maxHeight || 470, innerHeight - 24);
          pop.style.position = "fixed"; pop.style.maxHeight = h + "px";
          pop.style.left = Math.max(8, Math.min(r.left, innerWidth - width - 8)) + "px";
          const below = innerHeight - r.bottom - 12;
          pop.style.top = (below >= Math.min(h, 300) ? r.bottom + 6 : Math.max(8, r.top - 6 - Math.min(h, pop.scrollHeight))) + "px";
        }
      };
      place();
      if (window.MemuLayers && anchorEl) {
        const register = () => window.MemuLayers.open({ el: pop, anchor: anchorEl, kind: "model-picker", close: (reason) => {
          if (!(openState && openState.pop === pop)) return;

          if (reason === "rerender" && anchorEl.isConnected) { setTimeout(() => { if (openState && openState.pop === pop) register(); }, 0); return; }
          close(null, reason);
        } });
        register();
      } else {
        const outside = (e) => { if (!pop.contains(e.target) && openState?.pop === pop) { document.removeEventListener("pointerdown", outside, true); close(null, "outside"); } };
        setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
      }
      anchorEl?.setAttribute?.("aria-expanded", "true");
      setTimeout(() => input.focus({ preventScroll: true }), 10);
    });
  }

  const SPECIAL_VALUES = new Set(["", "off", "auto", "none", "default", "inherit", "current", "chat", "same"]);

  function isModelSelect(select) {
    if (!select || select.multiple || select.tagName !== "SELECT") return false;
    if (select.dataset.modelPicker === "off" || select.hasAttribute("data-no-model-picker")) return false;
    if (select.dataset.modelPicker === "1") return true;
    const known = rows();
    if (!known.length) return false;
    const opts = Array.from(select.options);
    let model = 0, other = 0;
    for (const o of opts) {
      if (find(o.value)) model++;
      else if (!SPECIAL_VALUES.has(o.value)) other++;
    }
    return model >= 1 && model >= other * 2;
  }
  function optionGroups(select) {
    const map = new Map();
    select.querySelectorAll("optgroup").forEach((g) => g.querySelectorAll("option").forEach((o) => map.set(o.value, g.label)));
    return map;
  }
  function triggerHtml(select) {
    const opt = select.options[select.selectedIndex];
    const value = opt ? opt.value : "";
    const row = find(value);
    if (!row) {
      const text = opt ? opt.text : t("Не выбрано", "Not selected");
      return `<span class="mmp-text"><span class="mmp-name">${esc(text)}</span></span>${svg("chev", "mmp-caret")}`;
    }
    return `${monogram(row)}<span class="mmp-text"><span class="mmp-name">${esc(name(value))}</span><span class="mmp-id">${esc(value)}</span></span>${badgesHtml(row, { noFree: true })}${svg("chev", "mmp-caret")}`;
  }
  function enhanceSelect(select, extra = {}) {
    if (!select || select.dataset.mmp === "1" || !select.parentElement) return false;
    select.dataset.mmp = "1"; select.dataset.pretty = "1"; select.dataset.mpick = "1";
    const wrap = document.createElement("div");
    wrap.className = "mmp-select";
    const width = select.style.maxWidth || select.style.width || "";
    if (width && !/^1?\d{2}px$/.test(width)) wrap.style.maxWidth = width;
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "mmp-trigger";
    btn.setAttribute("aria-haspopup", "dialog"); btn.setAttribute("aria-expanded", "false");
    const labelText = select.getAttribute("aria-label") || (select.labels && select.labels[0] ? select.labels[0].textContent.trim().slice(0, 80) : "");
    if (labelText) btn.setAttribute("aria-label", labelText);
    select.parentElement.insertBefore(wrap, select);
    wrap.append(btn, select);
    select.classList.add("mmp-native");
    const sync = () => {
      btn.innerHTML = triggerHtml(select);
      btn.disabled = !!select.disabled;
      const opt = select.options[select.selectedIndex];
      btn.title = opt ? (find(opt.value) ? label(opt.value) : opt.text) : "";
    };
    sync();
    select.addEventListener("change", sync);
    try {
      const proto = HTMLSelectElement.prototype;
      ["value", "selectedIndex"].forEach((key) => {
        const native = Object.getOwnPropertyDescriptor(proto, key);
        Object.defineProperty(select, key, { configurable: true, get() { return native.get.call(this); }, set(v) { native.set.call(this, v); sync(); } });
      });
      new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "selected"] });
    } catch (e) {}
    btn.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      if (openState && openState.anchorEl === btn) { close(null, "api"); return; }
      const groups = optionGroups(select);
      const ids = [], special = [];
      Array.from(select.options).forEach((o) => {
        if (o.disabled && !o.value) return;
        if (find(o.value) || (o.value && groups.has(o.value) && /[-/:]/.test(o.value))) ids.push(o.value);
        else special.push({ value: o.value, label: o.text });
      });
      const picked = await open(btn, { value: select.value, ids, special, groupOf: (id) => groups.get(id) || "",
        need: extra.need || select.dataset.modelNeed || "", title: extra.title || "", sections: ids.length > 6 ? undefined : false });
      if (picked === null || picked === undefined) return;
      if (picked === select.value) return;
      if (!extra.kind && !select.dataset.modelKind) warnIfSpecialized(picked, "chat");
      select.value = picked;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    };
    btn.onkeydown = (e) => {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") { e.preventDefault(); btn.click(); }
    };
    wrap._mmpSync = sync;
    return true;
  }
  function claim(select) {
    try { return isModelSelect(select) ? enhanceSelect(select) : false; } catch (e) { return false; }
  }
  function enhanceAll(root = document) {
    if (!root || !root.querySelectorAll) return 0;
    let n = 0;
    root.querySelectorAll("select:not([data-mmp])").forEach((s) => { if (claim(s)) n++; });
    return n;
  }
  function refreshAll() {
    document.querySelectorAll(".mmp-select").forEach((w) => { try { w._mmpSync && w._mmpSync(); } catch (e) {} });
  }

  const Main = {
    filters: (() => { try { return new Set(JSON.parse(localStorage.getItem("memu_mmp_main_filters") || "[]")); } catch (e) { return new Set(); } })(),
    saveFilters() { try { localStorage.setItem("memu_mmp_main_filters", JSON.stringify([...this.filters])); } catch (e) {} },

    render(pool, value, query) {
      const pop = document.getElementById("model-dd-pop");
      const list = document.getElementById("model-list");
      if (!pop || !list) return;
      pop.classList.add("mmp-main");
      let chips = document.getElementById("model-chips");
      const keys = Object.keys(FILTERS).filter((k) => { const n = pool.filter((r) => FILTERS[k].test(r)).length; return n > 0 && n < pool.length; });
      [...this.filters].forEach((k) => { if (!keys.includes(k)) this.filters.delete(k); });
      if (pool.length > 8 && keys.length) {
        if (!chips) {
          chips = document.createElement("div"); chips.id = "model-chips";
          list.parentElement.insertBefore(chips, list);
        }
        chips.innerHTML = filterChips(this.filters, keys);
        chips.querySelectorAll("[data-mmp-filter]").forEach((chip) => chip.onclick = (e) => {
          e.stopPropagation();
          const key = chip.dataset.mmpFilter;
          if (this.filters.has(key)) this.filters.delete(key); else this.filters.add(key);
          this.saveFilters();
          if (typeof renderModelDD === "function") renderModelDD();
          document.getElementById("model-search")?.focus();
        });
      } else if (chips) chips.innerHTML = "";
      const search = document.getElementById("model-search");
      if (search && !search.dataset.mmp) {
        search.dataset.mmp = "1";
        search.placeholder = t("Поиск: название, id или аккаунт", "Search: name, id or account");
      }
      list.innerHTML = listHtml(pool, { query, filters: this.filters, value, pin: true, options: true, showDefault: true,
        showAccount: !!String(query || "").trim(),
        emptyText: pool.length ? (this.filters.size ? t("Нет моделей с такими фильтрами", "No models match these filters") : "") : t("Нет моделей — добавьте аккаунт в MemuAI Proxy", "No models — add an account in MemuAI Proxy") });
      bindSections(list);
    },

    indicator(row) {
      const btn = document.getElementById("model-dd-btn");
      if (!btn) return;
      let ind = btn.querySelector(".mmp-vind");
      if (!row || kindOf(row) !== "chat") { if (ind) ind.remove(); try { window.MemuVisionGate && window.MemuVisionGate.note(); } catch (e) {} return; }
      const v = vision(row);
      if (!ind) {
        ind = document.createElement("span");
        const cur = btn.querySelector("#model-current");
        if (cur && cur.nextSibling) btn.insertBefore(ind, cur.nextSibling); else btn.appendChild(ind);
      }
      ind.className = "mmp-vind " + (v.state === "yes" ? "v-yes" : v.state === "no" ? "v-no" : "v-unk");
      ind.innerHTML = svg(v.state === "yes" ? "eye" : v.state === "no" ? "eyeOff" : "eyeQ");
      ind.title = v.hint;
      ind.setAttribute("aria-label", v.text);
      const cur = btn.querySelector("#model-current");
      if (cur) cur.title = `${label(row.id)}\n${v.hint}`;
      try { window.MemuVisionGate && window.MemuVisionGate.note(); } catch (e) {}
    },
  };
  window.MemuModelPickerMain = Main;

  window.MemuModelPicker = Object.freeze({
    VERSION, rows, load, setRows, find, name, label, vision, kindOf, warnIfSpecialized, badgesHtml, rowHtml, listHtml, bindSections,
    filterChips, FILTERS, open, close: () => close(null, "api"), isOpen: () => !!openState, claim, enhanceSelect,
    enhanceAll, refreshAll, noteRecent, recent, monogram, svg,
  });
})();
