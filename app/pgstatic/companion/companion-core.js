/* MemuAI companion — pure logic shared by the host pages, the stage and node tests.
   No DOM access here: emotion tags, keyword emotions, lip-sync mapping, prefs. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MemuCompanionCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "relaxed", "thinking"];
  const ALIASES = {
    joy: "happy", glad: "happy", excited: "happy", smile: "happy", laugh: "happy", fun: "happy", love: "happy",
    "радость": "happy", "рад": "happy", "весело": "happy",
    sorrow: "sad", upset: "sad", cry: "sad", sorry: "sad", "грусть": "sad", "грустно": "sad",
    mad: "angry", annoyed: "angry", "злость": "angry", "злой": "angry",
    surprise: "surprised", shock: "surprised", shocked: "surprised", wow: "surprised", amazed: "surprised",
    "удивление": "surprised",
    calm: "relaxed", relax: "relaxed", content: "relaxed", "спокойно": "relaxed", "спокойствие": "relaxed",
    think: "thinking", thoughtful: "thinking", curious: "thinking", hmm: "thinking", "думаю": "thinking",
    none: "neutral", normal: "neutral", default: "neutral",
  };

  function normalizeEmotion(name) {
    const key = String(name || "").trim().toLowerCase();
    if (EMOTIONS.includes(key)) return key;
    return ALIASES[key] || "";
  }

  // [emotion:happy] / [emotion: happy 0.6] / [emo:sad] / <emotion:happy> — hidden tags the chat model may emit.
  const TAG_RE = /([ \t]*)[\[<]\s*(?:emotion|emo|mood)\s*[:=]\s*([A-Za-zА-Яа-яЁё_-]{1,24})(?:\s+(0(?:\.\d+)?|1(?:\.0+)?))?\s*[\]>]([ \t]*)/gi;
  // An unfinished tag at the very end of a stream: "[emo", "[emotion:ha".
  const PARTIAL_RE = /[ \t]*[\[<]\s*(?:em(?:o(?:t(?:i(?:o(?:n)?)?)?)?)?|mo(?:o(?:d)?)?)(?:\s*[:=]\s*[A-Za-zА-Яа-яЁё_-]{0,24}(?:\s+[\d.]{0,4})?)?\s*$/i;

  /** Parse and remove emotion tags. Returns {text, emotions:[{emotion, intensity, index}], last}. */
  function parseEmotionTags(text, options) {
    const source = String(text == null ? "" : text);
    const streaming = !!(options && options.streaming);
    const emotions = [];
    let removed = 0;
    // Only the tag and the spaces around it go: "Hi [emo:x] there" → "Hi there";
    // indentation and spacing elsewhere (code, lists) are never touched.
    let out = source.replace(TAG_RE, function (match, lead, name, value, trail, offset) {
      const emotion = normalizeEmotion(name);
      const keep = lead && trail ? " " : "";
      if (emotion) {
        const intensity = value == null ? 1 : Math.max(0, Math.min(1, parseFloat(value)));
        emotions.push({ emotion: emotion, intensity: intensity, index: offset - removed });
      }
      removed += match.length - keep.length;
      return keep;
    });
    if (streaming) out = out.replace(PARTIAL_RE, "");
    return { text: out, emotions: emotions, last: emotions.length ? emotions[emotions.length - 1] : null };
  }

  function stripTags(text, streaming) { return parseEmotionTags(text, { streaming: !!streaming }).text; }

  // Local fallback when the model gave no tag: weighted keywords and emoji (RU + EN).
  const KEYWORDS = {
    happy: [/\b(great|awesome|glad|happy|yay|wonderful|congrat\w*|excellent|love it|nice|haha|lol)\b/i,
      /(?<![а-яё])(отлично|здорово|рада?|ура|прекрасно|поздравля[а-яё]*|классно|класс|супер|чудесно|замечательно|хаха|ахах)(?![а-яё])/i, /[😀😃😄😁😊🙂😍🥳🎉❤️]/u],
    sad: [/\b(sorry|sad|unfortunately|regret|miss you|tough|condolence\w*)\b/i,
      /(?<![а-яё])(к сожалению|жаль|грустно|печально|сочувству[а-яё]*|соболезну[а-яё]*|извини[а-яё]*)(?![а-яё])/i, /[😢😭😞☹️🙁💔]/u],
    angry: [/\b(angry|furious|annoyed|unacceptable|outrageous)\b/i, /(?<![а-яё])(злюсь|возмутительно|бесит|недопустимо|раздража[а-яё]*)(?![а-яё])/i, /[😠😡🤬]/u],
    surprised: [/\b(wow|whoa|really\?|no way|incredible|unbelievable|surprising)\b/i,
      /(?<![а-яё])(ого|вау|неужели|невероятно|удивительно|ничего себе)(?![а-яё])/i, /[😮😲😯🤯]/u],
    relaxed: [/\b(calm|relax|no worries|take your time|peaceful|cozy)\b/i, /(?<![а-яё])(спокойн[а-яё]*|не торопись|не волнуйся|уютно|расслаб[а-яё]*)(?![а-яё])/i, /[😌🌿☕]/u],
    thinking: [/\b(let me think|hmm+|i wonder|let's think)\b/i, /(?<![а-яё])(хм+|дай подумать|интересный вопрос|подумаем|давай подумаем)(?![а-яё])/i, /[🤔]/u],
  };

  /** Keyword emotion of a reply; {emotion, score} with score 0 when nothing matched. */
  function detectEmotion(text) {
    const value = String(text || "").slice(-1200);
    let best = { emotion: "neutral", score: 0 };
    for (const emotion of Object.keys(KEYWORDS)) {
      let score = 0;
      for (const re of KEYWORDS[emotion]) {
        const flags = re.flags.includes("g") ? re.flags : re.flags + "g";
        const found = value.match(new RegExp(re.source, flags));
        if (found) score += found.length;
      }
      if (/!/.test(value) && emotion === "happy" && score) score += 0.5;
      if (score > best.score) best = { emotion: emotion, score: score };
    }
    return best;
  }

  /** The emotion to show for an answer: last tag wins, then keywords, else neutral. */
  function emotionForAnswer(text, options) {
    const parsed = parseEmotionTags(text);
    if (parsed.last) return { emotion: parsed.last.emotion, intensity: parsed.last.intensity, source: "tag" };
    if (options && options.keywords === false) return { emotion: "neutral", intensity: 0, source: "none" };
    const found = detectEmotion(parsed.text);
    if (!found.score) return { emotion: "neutral", intensity: 0, source: "none" };
    return { emotion: found.emotion, intensity: Math.min(1, 0.45 + 0.2 * found.score), source: "keywords" };
  }

  // ---------------------------------------------------------- lip sync
  const VISEMES = ["aa", "ih", "ou", "ee", "oh"];

  /** RMS of a time-domain byte buffer (0…255, 128 = silence) → 0…1. */
  function rmsFromBytes(bytes) {
    if (!bytes || !bytes.length) return 0;
    let sum = 0;
    for (let i = 0; i < bytes.length; i++) { const x = (bytes[i] - 128) / 128; sum += x * x; }
    return Math.sqrt(sum / bytes.length);
  }

  /** Loudness → mouth openness with a noise gate and a soft knee. */
  function levelToOpenness(rms, gate) {
    const g = gate == null ? 0.012 : gate;
    if (!(rms > g)) return 0;
    const x = Math.min(1, (rms - g) * 5.5);
    return Math.min(1, Math.pow(x, 0.7));
  }

  /** Energy in formant-ish bands of a frequency byte buffer (getByteFrequencyData). */
  function bandsFromSpectrum(freq, sampleRate, fftSize) {
    const empty = { low: 0, mid: 0, high: 0 };
    if (!freq || !freq.length || !sampleRate || !fftSize) return empty;
    const hz = sampleRate / fftSize;
    const band = function (from, to) {
      const a = Math.max(1, Math.floor(from / hz)), b = Math.min(freq.length - 1, Math.ceil(to / hz));
      if (b < a) return 0;
      let sum = 0;
      for (let i = a; i <= b; i++) sum += freq[i];
      return sum / ((b - a + 1) * 255);
    };
    return { low: band(250, 900), mid: band(900, 2200), high: band(2200, 4000) };
  }

  /**
   * Openness (0…1) + spectral bands → VRM viseme weights {aa, ih, ou, ee, oh}.
   * Rough vowel cues: F1 (low band) opens the jaw (a/o), F2 (mid/high) spreads
   * the lips (i/e), a dark spectrum with little F2 rounds them (u/o).
   * Without bands (bands=null) the mouth just opens on "aa" with a little "oh".
   */
  function visemeWeights(openness, bands) {
    const out = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    const open = Math.max(0, Math.min(1, +openness || 0));
    if (!open) return out;
    if (!bands || !(bands.low + bands.mid + bands.high > 0.002)) {
      out.aa = open * 0.85; out.oh = open * 0.2;
      return out;
    }
    const total = bands.low + bands.mid + bands.high;
    const low = bands.low / total, mid = bands.mid / total, high = bands.high / total;
    const raw = {
      aa: Math.max(0, low * 1.2 + mid * 0.6 - 0.45),
      oh: Math.max(0, low * 1.4 - mid * 0.6 - 0.25),
      ou: Math.max(0, low * 1.1 - mid * 0.9 - high * 0.6 - 0.2),
      ee: Math.max(0, mid * 1.3 + high * 0.9 - low * 0.8 - 0.25),
      ih: Math.max(0, high * 1.8 + mid * 0.2 - low * 0.6 - 0.2),
    };
    let sum = 0;
    for (const k of VISEMES) sum += raw[k];
    if (sum < 0.05) { raw.aa = 1; sum = 1; }
    for (const k of VISEMES) out[k] = Math.min(1, open * raw[k] / sum * 1.1);
    return out;
  }

  /** Smooth weights: fast attack, slower release (per frame, dt in seconds). */
  function smoothVisemes(prev, next, dt) {
    const out = {};
    const attack = 1 - Math.exp(-dt * 28), release = 1 - Math.exp(-dt * 12);
    for (const k of VISEMES) {
      const a = (prev && prev[k]) || 0, b = (next && next[k]) || 0;
      out[k] = a + (b - a) * (b > a ? attack : release);
      if (out[k] < 0.004) out[k] = 0;
    }
    return out;
  }

  // Text-driven fallback (SAPI audio that cannot be analysed, desktop window
  // without the audio): letters → visemes at a speaking pace.
  const LETTER = {
    a: "aa", "а": "aa", "я": "aa", o: "oh", "о": "oh", "ё": "oh", u: "ou", "у": "ou", "ю": "ou", w: "ou",
    i: "ih", "и": "ih", "ы": "ih", y: "ih", e: "ee", "е": "ee", "э": "ee",
  };
  function textVisemeTrack(text, msPerChar) {
    const step = msPerChar || 62;
    const track = [];
    let t = 0;
    for (const ch of String(text || "").toLowerCase()) {
      if (/[\s]/.test(ch)) { track.push({ t: t, v: "", w: 0 }); t += step * 0.8; continue; }
      if (/[.,!?;:…—–-]/.test(ch)) { track.push({ t: t, v: "", w: 0 }); t += step * 3; continue; }
      const v = LETTER[ch];
      if (v) track.push({ t: t, v: v, w: 0.75 });
      else if (/[a-zа-я0-9]/.test(ch)) track.push({ t: t, v: "ih", w: 0.18 });
      t += step;
    }
    track.push({ t: t, v: "", w: 0 });
    return { track: track, duration: t };
  }
  function sampleTrack(track, ms) {
    const out = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    if (!track || !track.track.length) return out;
    let lo = 0, hi = track.track.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (track.track[mid].t <= ms) lo = mid; else hi = mid - 1; }
    const item = track.track[lo];
    if (item && item.v) out[item.v] = item.w;
    return out;
  }

  // ------------------------------------------------------------ prefs
  const PREF_DEFAULTS = {
    companion_live_enabled: false, companion_live_placement: "panel",
    companion_quick_enabled: false, companion_quick_mode: "inline",
    companion_model_id: "sample", companion_size: 100, companion_framing: "bust",
    companion_lipsync: true, companion_emotions: true, companion_emotion_tags: true, companion_bubble: true,
    companion_look_at: true, companion_gestures: true, companion_fps: 30, companion_low_power: false,
    companion_personality_link: true, companion_live2d_consent: false,
  };
  function normalizePrefs(raw) {
    const src = raw && typeof raw === "object" ? raw : {};
    const out = {};
    for (const key of Object.keys(PREF_DEFAULTS)) {
      const def = PREF_DEFAULTS[key];
      let v = src[key];
      if (typeof def === "boolean") v = v == null ? def : (typeof v === "string" ? /^(1|true|yes|on)$/i.test(v) : !!v);
      out[key] = v == null ? def : v;
    }
    out.companion_live_placement = out.companion_live_placement === "window" ? "window" : "panel";
    out.companion_quick_mode = ["inline", "desktop", "both"].includes(out.companion_quick_mode) ? out.companion_quick_mode : "inline";
    out.companion_framing = out.companion_framing === "full" ? "full" : "bust";
    const size = Math.round(parseFloat(out.companion_size));
    out.companion_size = Number.isFinite(size) ? Math.max(60, Math.min(200, size)) : 100;
    out.companion_fps = +out.companion_fps >= 45 ? 60 : 30;
    out.companion_model_id = /^[a-z0-9][a-z0-9-]{0,47}$/.test(String(out.companion_model_id)) ? out.companion_model_id : "sample";
    return out;
  }

  // Personality → how lively the body is (gesture rate, sway, base smile).
  const PERSONALITY_ENERGY = {
    warm: { energy: 0.6, smile: 0.15 }, therapist: { energy: 0.35, smile: 0.1 }, storyteller: { energy: 0.8, smile: 0.12 },
    challenger: { energy: 0.7, smile: 0.0 }, charismatic: { energy: 0.95, smile: 0.2 }, witty: { energy: 0.8, smile: 0.2 },
    coach: { energy: 0.85, smile: 0.15 }, teacher: { energy: 0.55, smile: 0.1 }, calm: { energy: 0.25, smile: 0.08 },
    creative: { energy: 0.8, smile: 0.15 }, romantic: { energy: 0.5, smile: 0.22 }, concise: { energy: 0.35, smile: 0.0 },
  };
  function personalityProfile(id) { return PERSONALITY_ENERGY[id] || { energy: 0.6, smile: 0.1 }; }

  const EVENT_GESTURES = { open: "wave", "answer-start": "nod", thinking: "think", submit: "think", click: "happy-bounce", error: "shake" };
  function gestureForEvent(name) { return EVENT_GESTURES[name] || ""; }

  /* ---------------------------------------------------------- gaze (Round 25)
     The look direction is measured from the character's FACE, not from the centre of the frame: with the old
     frame-centre origin a cursor between the face and the middle of the window (i.e. BELOW her eyes) made her
     look up — the "inverted gaze" report. Screen coordinates: x right, y DOWN (CSS pixels, any point of the
     screen, may lie outside the stage). `reach` = the distance at which she looks fully aside; farther away the
     direction keeps following while the magnitude saturates smoothly towards 1. */
  function lookVector(cursor, face, reach) {
    if (!cursor || !face || !isFinite(cursor.x) || !isFinite(cursor.y) || !isFinite(face.x) || !isFinite(face.y)) return { x: 0, y: 0 };
    const r = Math.max(40, +reach || 300);
    const dx = cursor.x - face.x, dy = cursor.y - face.y;
    const norm = Math.sqrt(dx * dx + dy * dy + r * r);
    return { x: dx / norm, y: dy / norm };
  }
  /** Live2D focus: +x = the viewer's right, +y = UP (ParamAngleY / ParamEyeBallY), so the screen y flips once here. */
  function live2dFocus(look) {
    const clamp = (v) => Math.max(-1, Math.min(1, +v || 0));
    return { x: clamp(look && look.x), y: clamp(-(look && look.y)) };
  }
  /** Physical screen pixels (GetCursorPos) → CSS pixels of a viewport whose top-left corner is `origin` (CSS screen px). */
  function screenToClient(point, origin, dpr) {
    const scale = Math.max(0.25, +dpr || 1);
    return { x: point.x / scale - ((origin && origin.x) || 0), y: point.y / scale - ((origin && origin.y) || 0) };
  }

  /* --------------------------------------------------- VRM bone axes (Round 26)
     three-vrm's normalized bones keep the axes of the model FILE: VRM 0.x faces −Z (VRMUtils.rotateVRM0 only turns
     the whole scene by π), VRM 1.0 faces +Z. So the same local angle about x (pitch) or z (roll) moves a 0.x bone
     the opposite way to a 1.0 bone; y (yaw) is the same. Our poses are written in the 0.x axes (the built-in
     sample): +x lifts the chin / swings an arm back-up. Before Round 26 the head pitch alone was written as if
     +x lowered the chin, so on the sample a cursor ABOVE her made her look DOWN (the user's report). */
  function vrmAxisSign(metaVersion) { return String(metaVersion) === "0" ? 1 : -1; }
  /** Local Euler angles of a pose written in VRM 0.x axes → the angles for this model. */
  function vrmEuler(metaVersion, x, y, z) {
    const s = vrmAxisSign(metaVersion);
    return [(+x || 0) * s, +y || 0, (+z || 0) * s];
  }
  /** Head pitch (0.x axes, before the neck/head split) for a look vector (y down): a cursor below the face lowers the chin. */
  function vrmHeadPitch(lookY) { return -(+lookY || 0) * 0.22; }

  /* ------------------------------------------------------- framing (Round 25)
     Fit a character into a w×h stage so nothing is cut at the sides: `box` = the character's bounds in model units
     ({x0, y0, x1, y1}, y down), `pad` = extra room around it in the same units (raised/waving arms), `headroom` =
     share of the stage height kept free at the top (desktop bubble), `bust` = show only the upper part:
     from the top down to `bustCut` (model units, y). Returns the scale and the top-left offset of model point (0,0). */
  function fitBox(box, w, h, options) {
    const o = options || {};
    const pad = o.pad || { x: 0, top: 0, bottom: 0 };
    const top = box.y0 - (pad.top || 0);
    const bottom = o.bust && isFinite(o.bustCut) ? Math.min(box.y1 + (pad.bottom || 0), o.bustCut) : box.y1 + (pad.bottom || 0);
    const left = box.x0 - (pad.x || 0), right = box.x1 + (pad.x || 0);
    const room = Math.max(1, h * (1 - (o.headroom || 0)));
    const scale = Math.max(1e-6, Math.min(w / Math.max(1e-6, right - left), room / Math.max(1e-6, bottom - top)));
    const offsetX = w / 2 - ((left + right) / 2) * scale;
    // Bust: the head sits at the top of the free room; full: centred vertically in it.
    const offsetY = h - room + (o.bust ? 0 : (room - (bottom - top) * scale) / 2) - top * scale;
    return { scale: scale, x: offsetX, y: offsetY };
  }

  /** Round 26: how far a camera on the +z axis (looking at x = 0, y = centerY) must stand so that every point
      {x, y, z, m} (world, m = margin around it) stays inside the frame. A point at depth z is (|x| + m) / (dist − z)
      off-centre: a waving hand nearer the camera than the body looks wider — the old z = 0 maths cut it off.
      tanH/tanV = tan of half the horizontal/vertical field of view. */
  function reachDistance(points, tanH, tanV, centerY) {
    let dist = 0;
    for (const p of points || []) {
      dist = Math.max(dist, (Math.abs(p.x) + p.m) / tanH + p.z, (p.y + p.m - centerY) / tanV + p.z);
    }
    return dist;
  }

  /** Round 26: bounds of the painted pixels of a WebGL RGBA readback (rows bottom-up) → {x0, y0, x1, y1} in CSS px
      (sx, sy = CSS px per buffer px), or null when nothing is drawn. Alpha ≤ 8 counts as empty (antialiasing haze). */
  function alphaBounds(pixels, W, H, sx, sy) {
    let x0 = W, x1 = -1, top = -1, bottom = -1;
    for (let row = 0; row < H; row++) {
      const base = row * W * 4;
      let first = -1, last = -1;
      for (let col = 0; col < W; col++) if (pixels[base + col * 4 + 3] > 8) { first = col; break; }
      if (first < 0) continue;
      for (let col = W - 1; col >= first; col--) if (pixels[base + col * 4 + 3] > 8) { last = col; break; }
      if (first < x0) x0 = first;
      if (last > x1) x1 = last;
      if (bottom < 0) bottom = row;
      top = row;
    }
    if (x1 < 0) return null;
    // Rows are bottom-up: buffer row r covers CSS y from (H − r − 1)·sy to (H − r)·sy.
    return { x0: x0 * sx, x1: (x1 + 1) * sx, y0: (H - top - 1) * sy, y1: (H - bottom) * sy };
  }

  /** The hidden instruction for chat models (classic Live): one tag per answer, stripped before display/TTS. */
  function promptHint(en) {
    return "<companion_emotion>An animated avatar shows your mood. Begin each reply with one hidden tag " +
      "[emotion:NAME] where NAME is one of: neutral, happy, sad, angry, surprised, relaxed, thinking. " +
      "Choose it honestly from the content. The tag is removed before display and speech; never mention it." +
      "</companion_emotion>";
  }

  return {
    EMOTIONS, VISEMES, PREF_DEFAULTS, normalizeEmotion, parseEmotionTags, stripTags, detectEmotion, emotionForAnswer,
    rmsFromBytes, levelToOpenness, bandsFromSpectrum, visemeWeights, smoothVisemes, textVisemeTrack, sampleTrack,
    normalizePrefs, personalityProfile, gestureForEvent, promptHint, lookVector, live2dFocus, screenToClient, fitBox,
    vrmAxisSign, vrmEuler, vrmHeadPitch, alphaBounds, reachDistance,
  };
});
