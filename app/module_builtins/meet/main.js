"use strict";
/* MemuMeet: recording, transcript and summary of meetings.
 *
 * No server route of its own: meetings live in the generic module store
 * (Memu.store, <DATA_DIR>/memuai/modules/store/meet/), the microphone is
 * the host bridge (Memu.record), transcription is Memu.transcribe — the
 * Live/STT preferences, never a second setup — and summaries are plain
 * Memu.request calls the user starts with a button. The Python mirror of
 * the segmentation / export rules is app/module_builtins/meet/backend.py.
 *
 * Recording sources: microphone and system audio through the host
 * bridge (direct display capture in a standalone browser), both at
 * once (one meeting, one timeline, two tracks; the transcript marks
 * microphone speech separately from system speech), or an imported file.
 * The host WASAPI bridge supplies devices, meters and recorded audio.
 */

var t = function (key, vars) { return Memu.t(key, vars); };
var en = function () {
  try { return String(Memu.lang || (Memu.env && Memu.env.lang) || "ru").indexOf("en") === 0; }
  catch (e) { return false; }
};
var $ = function (sel) { return document.querySelector(sel); };
var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>\"']/g, function (ch) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}
function fmtTime(seconds) {
  seconds = Math.max(0, Math.round(+seconds || 0));
  var m = Math.floor(seconds / 60), s = seconds % 60;
  return m + ":" + String(s).padStart(2, "0");
}
function fmtClock(ms) {
  try { return new Date(+ms || 0).toLocaleString(en() ? "en" : "ru", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch (e) { return ""; }
}
function toast(message) {
  var box = $("#toasts");
  var el = document.createElement("div");
  el.className = "mm-toast";
  el.textContent = message;
  box.appendChild(el);
  setTimeout(function () { el.remove(); }, 4200);
}


function errText(error) { return String((error && error.message) || error || t("common.error")); }
function clip(text, limit) {
  text = String(text || "");
  return text.length > limit ? text.slice(0, limit) + "\n[…]" : text;
}
function svg(path) { return '<svg viewBox="0 0 24 24" aria-hidden="true">' + path + "</svg>"; }
var ICON = {
  mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>'),
  system: svg('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
  both: svg('<rect x="2.5" y="4" width="13" height="9" rx="2"/><path d="M6 17h6M9 13v4"/><rect x="17" y="8" width="4.5" height="8" rx="2.2"/>'),
  file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M10 16.5a1.5 1.5 0 1 1-1.5-1.5H10V11l4-1"/>'),
  clock: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
  text: svg('<path d="M5 6h14M5 10h14M5 14h9M5 18h6"/>'),
  spark: svg('<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>'),
  check: svg('<path d="M5 12.5l4.2 4.2L19 7"/>'),
  star: svg('<path d="m12 3.6 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
  flag: svg('<path d="M6 21V4h10l-1.6 3.5L16 11H6"/>'),
  user: svg('<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/>'),
  x: svg('<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>'),
  note: svg('<path d="M5 4h10l4 4v12H5z"/><path d="M15 4v4h4M8.5 12h7M8.5 16h5"/>'),
  play: svg('<path d="M8 5.5v13l10-6.5z"/>'),
  chat: svg('<path d="M5 5h14v10H9l-4 4z"/>')
};
function sourceIcon(source) {
  return source === "system" ? ICON.system : source === "both" ? ICON.both : source === "import" ? ICON.file : ICON.mic;
}
function copyText(text) {
  var value = String(text || "");
  if (!value.trim()) return Promise.resolve();
  if (!Memu.copy) { toast(t("common.error")); return Promise.resolve(); }
  return Memu.copy(value).then(function () { toast(t("detail.copied")); })
    .catch(function (error) { toast(errText(error)); });
}

/* Safe Markdown for summaries and answers: the text is escaped FIRST,
 * then a small subset is re-introduced (headings, lists, task boxes,
 * bold/italic, inline code, quotes, rules). No links, no raw HTML. */
function inlineMd(html) {
  return html
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(«])\*([^*\s][^*]*)\*(?=[\s).,;:!?»]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[\s(«])_([^_\s][^_]*)_(?=[\s).,;:!?»]|$)/g, "$1<em>$2</em>");
}
function renderMarkdown(md) {
  var lines = String(md || "").replace(/\r/g, "").split("\n");
  var html = [], list = null, para = [];
  function flushPara() { if (para.length) { html.push("<p>" + para.join("<br>") + "</p>"); para = []; } }
  function closeList() { if (list) { html.push("</" + list + ">"); list = null; } }
  lines.forEach(function (raw) {
    var line = raw.replace(/\s+$/, "");
    var m;
    if (!line.trim()) { flushPara(); closeList(); return; }
    if ((m = line.match(/^\s*(#{1,6})\s+(.*?)\s*#*\s*$/))) {
      flushPara(); closeList();
      var level = Math.min(4, m[1].length + 1);
      html.push("<h" + level + ">" + inlineMd(esc(m[2])) + "</h" + level + ">");
      return;
    }
    if ((m = line.match(/^\s*\*\*([^*]+)\*\*:?\s*$/))) { flushPara(); closeList(); html.push("<h4>" + esc(m[1]) + "</h4>"); return; }
    if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) { flushPara(); closeList(); html.push("<hr>"); return; }
    var ordered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    var bullet = ordered ? null : line.match(/^\s*[-*•]\s+(.*)$/);
    if (ordered || bullet) {
      var kind = ordered ? "ol" : "ul";
      var body = ordered ? ordered[2] : bullet[1];
      flushPara();
      if (list !== kind) { closeList(); html.push("<" + kind + ">"); list = kind; }
      var box = body.match(/^\[( |x|X)\]\s*(.*)$/);
      if (box) html.push('<li class="mm-li-task' + (box[1] !== " " ? " done" : "") + '"><span class="mm-box" aria-hidden="true"></span>' + inlineMd(esc(box[2])) + "</li>");
      else html.push("<li>" + inlineMd(esc(body)) + "</li>");
      return;
    }
    if ((m = line.match(/^\s*>\s?(.*)$/))) { flushPara(); closeList(); html.push("<blockquote>" + inlineMd(esc(m[1])) + "</blockquote>"); return; }
    closeList();
    para.push(inlineMd(esc(line.trim())));
  });
  flushPara(); closeList();
  return html.join("");
}

/* ---- tasks: the "Tasks" section of a summary as a checklist ----
 * Mirror of backend.extract_tasks (same headings, owner and due rules). */
var TASK_HEADINGS = ["задачи", "tasks", "action items", "следующие шаги", "next steps", "поручения", "todo", "to do"];
var MAX_TASKS = 200;
function plainText(value) { return String(value || "").replace(/[*_`]+/g, "").replace(/\s+/g, " ").trim(); }
function taskKey(value) { return plainText(value).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim(); }
function extractTasks(summary) {
  var out = [], seen = {}, inside = false;
  String(summary || "").replace(/\r/g, "").split("\n").forEach(function (raw) {
    if (out.length >= MAX_TASKS) return;
    var heading = raw.match(/^\s*#{1,6}\s*(.+?)\s*#*\s*$/) || raw.match(/^\s*\*\*(.+?)\*\*\s*:?\s*$/);
    if (heading) {
      var name = plainText(heading[1]).replace(/^[\s:«»"]+|[\s:«»"]+$/g, "").toLowerCase();
      inside = TASK_HEADINGS.some(function (h) { return name === h || name.indexOf(h + " ") === 0; });
      return;
    }
    var line = raw.trim();
    if (!inside || !line || line === "-" || line === "*" || line === "•") return;
    if (line.charAt(0) === "|" || line.charAt(0) === ">") return;
    var text = line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "");
    var done = false, owner = "", due = "", m;
    var box = text.match(/^\[( |x|X|✓)\]\s*/);
    if (box) { done = box[1] !== " "; text = text.slice(box[0].length); }
    if ((m = text.match(/^\*\*([^*]{1,60})\*\*\s*[:—–-]\s*/))) { owner = plainText(m[1]); text = text.slice(m[0].length); }
    if ((m = text.match(/\s*[(,;—–-]?\s*(?:срок|due|deadline|до|by)\s*:?\s*(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|\d{4}-\d{2}-\d{2})\)?\s*$/i))) {
      due = m[1]; text = text.slice(0, m.index);
    }
    if (!owner && (m = text.match(/\((?:ответственн[а-яё]*|отв\.|owner|assignee)\s*:?\s*([^)]{1,60})\)/i))) {
      owner = plainText(m[1]); text = text.slice(0, m.index) + text.slice(m.index + m[0].length);
    }
    if (!owner && (m = text.match(/[;,.]?\s*(?:ответственн[а-яё]*|owner|assignee)\s*:\s*([^;,.]{1,60})\s*$/i))) {
      owner = plainText(m[1]); text = text.slice(0, m.index);
    }
    if (!owner && (m = text.match(/\s[—–]\s*([^—–]{2,40})$/)) && m[1].trim().split(/\s+/).length <= 4) {
      owner = plainText(m[1]); text = text.slice(0, m.index);
    }
    text = plainText(text).replace(/^[\s—–\-:;,.]+|[\s—–\-:;,.]+$/g, "");
    if (text.length < 2) return;
    var key = taskKey(text);
    if (seen[key]) return;
    seen[key] = true;
    out.push({ text: text.slice(0, 300), owner: owner.slice(0, 60), due: due.slice(0, 20), done: done });
  });
  return out;
}
function newTaskId() { return "t" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
function mergeTasks(existing, extracted) {
  var rows = (Array.isArray(existing) ? existing : []).slice();
  var keys = {};
  rows.forEach(function (row) { keys[taskKey(row && row.text)] = true; });
  var added = 0;
  (extracted || []).forEach(function (row) {
    var key = taskKey(row.text);
    if (!key || keys[key] || rows.length >= MAX_TASKS) return;
    keys[key] = true;
    rows.push({ id: newTaskId(), text: row.text, owner: row.owner || "", due: row.due || "", done: !!row.done,
      from: "summary", created_at: Date.now() });
    added++;
  });
  return { rows: rows, added: added };
}
function taskChecklist(tasks) {
  return (tasks || []).map(function (task) {
    var extra = [task.owner, task.due].filter(Boolean).join(" — ");
    return "- [" + (task.done ? "x" : " ") + "] " + task.text + (extra ? " — " + extra : "");
  }).join("\n");
}

/* The sandbox has no allow-modals, so native dialogs would silently
 * return false. Confirmations are an in-page dialog instead. */
function confirmDialog(message) {
  return new Promise(function (resolve) {
    var modal = $("#confirm-modal");
    $("#confirm-text").textContent = message;
    modal.hidden = false;
    var done = function (value) {
      modal.hidden = true;
      $("#confirm-yes").removeEventListener("click", yes);
      $("#confirm-no").removeEventListener("click", no);
      modal.removeEventListener("click", backdrop);
      document.removeEventListener("keydown", keys);
      resolve(value);
    };
    var yes = function () { done(true); };
    var no = function () { done(false); };
    var backdrop = function (event) { if (event.target === modal) done(false); };
    var keys = function (event) { if (event.key === "Escape") done(false); };
    $("#confirm-yes").addEventListener("click", yes);
    $("#confirm-no").addEventListener("click", no);
    modal.addEventListener("click", backdrop);
    document.addEventListener("keydown", keys);
    $("#confirm-no").focus();
  });
}

/* ---- settings: autosaved, debounce + indicator (see scheduleSaveSettings) ---- */
var SUMMARY_STYLES = ["standard", "brief", "detailed", "protocol", "actions", "decisions", "followup"];
var SETTING_DEFAULTS = {
  model: "", summary_lang: "auto", speaker_heuristic: true,
  keep_audio: true, captions_large: false, confirm_before_summary: true,
  mic_device: "", source: "microphone",
  summary_style: "standard", auto_transcribe: false, auto_tasks: true,
  system_devices: [], system_gains: {}
};
/* Mirror of backend.clean_settings: unknown keys are dropped and every
 * value is checked, so a hand-edited persist file cannot break the UI. */
function normalizeSettings(raw) {
  var src = raw && typeof raw === "object" ? raw : {};
  var out = Object.assign({}, SETTING_DEFAULTS);
  if (typeof src.model === "string") out.model = src.model.slice(0, 300);
  if (["auto", "ru", "en"].indexOf(src.summary_lang) >= 0) out.summary_lang = src.summary_lang;
  if (typeof src.mic_device === "string") out.mic_device = src.mic_device.slice(0, 200);
  if (["microphone", "system", "both", "import"].indexOf(src.source) >= 0) out.source = src.source;
  if (Array.isArray(src.system_devices)) {
    out.system_devices = src.system_devices.filter(function (id) { return typeof id === "string" && id.trim(); })
      .map(function (id) { return id.slice(0, 200); }).slice(0, 16);
  }
  if (src.system_gains && typeof src.system_gains === "object") {
    var gains = {};
    Object.keys(src.system_gains).slice(0, 16).forEach(function (key) {
      var value = Number(src.system_gains[key]);
      if (isFinite(value)) gains[key.slice(0, 200)] = Math.max(0, Math.min(1, value));
    });
    out.system_gains = gains;
  }
  if (SUMMARY_STYLES.indexOf(src.summary_style) >= 0) out.summary_style = src.summary_style;
  ["speaker_heuristic", "keep_audio", "captions_large", "confirm_before_summary", "auto_transcribe", "auto_tasks"].forEach(function (key) {
    if (typeof src[key] === "boolean") out[key] = src[key];
  });
  return out;
}
var settings = normalizeSettings((Memu.saved && Memu.saved.settings) || {});
var saveTimer = null;
var saveChain = Promise.resolve();
function setSaveIndicator(state) {
  var el = $("#set-status");
  if (!el) return;
  if (state === "saving") el.textContent = t("settings.saving");
  else if (state === "saved") el.textContent = t("settings.saved");
  else if (state === "error") el.textContent = t("settings.save_error");
  else el.textContent = "";
  el.className = "mm-status inline mm-save-" + (state || "idle");
}
function scheduleSaveSettings() {
  /* Any change restarts the timer, so a burst of quick edits saves once,
   * with the LAST state; saves are chained so they never overtake each
   * other and a slow save cannot lose a newer value. */
  setSaveIndicator("saving");
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(function () {
    saveTimer = null;
    var snapshot = Object.assign({}, settings);
    saveChain = saveChain.then(function () { return Memu.save({ settings: snapshot }); })
      .then(function () { setSaveIndicator("saved"); })
      .catch(function () { setSaveIndicator("error"); });
  }, 350);
}
function flushSaveSettings() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  var snapshot = Object.assign({}, settings);
  setSaveIndicator("saving");
  saveChain = saveChain.then(function () { return Memu.save({ settings: snapshot }); })
    .then(function () { setSaveIndicator("saved"); })
    .catch(function () { setSaveIndicator("error"); });
  return saveChain;
}
function saveSettings() { return flushSaveSettings(); }

/* ---- the module store: meetings/<id> + meetings/<id>/audio (+ /audio-mic) ---- */
function meetingKey(id) { return "meetings/" + id; }
function audioKey(id) { return "meetings/" + id + "/audio"; }
function audioMicKey(id) { return "meetings/" + id + "/audio-mic"; }
function storeGet(key, fallback) {
  return Memu.store.get(key, fallback == null ? null : fallback);
}
function storeSet(key, value) { return Memu.store.set(key, value); }
function storeRemove(key) { return Memu.store.remove(key); }
function isMeetingKey(key) { return /^meetings\/[^/]+$/.test(String(key || "")); }

var meetings = [];
var current = null;       /* the meeting open in the detail view */
var currentAudio = "";    /* its data URL, loaded on demand */
var currentMicAudio = ""; /* the microphone track of a "both" meeting */
var liveCaptions = [];    /* chunks transcribed while system audio records */
var models = [];
var recording = null;     /* {source, started, timer, ...} — see startRecording */
var pending = null;       /* {tracks:[{source,audio,mime,ms}], title, ms} awaiting save */
var recStep = 1;          /* wizard step: 1 sources, 2 sound check, 3 record, 4 processing */
var checkSource = "";
var checkSequence = 0;
var micCheckTimer = 0;
var micPreviewUrl = "";
var hostSystemCheck = false;
/* Native capture (WASAPI loopback helper): the probe result from the
 * host window — {available, reason, backend, devices:[{id,name,
 * is_default}]} — or null before the probe answers. A null probe means
 * "not known yet", never "unavailable". */
var nativeStatus = null;
var nativeLevels = {};   /* last per-device levels from the helper */
var nativeLost = {};     /* device ids lost during the current session */
var liveMeterStop = null;
var recSystemReason = "";   /* why a recording has no system track, if any */
var recRequestedSource = "";

/* ---- capture availability: probed once, before the UI is painted ---- */
var captureGate = null;   /* cached displayCaptureAllowed() result */
function captureAvailable() {
  if (!captureGate) captureGate = displayCaptureAllowed();
  return !!captureGate.allowed;
}
var MAX_AUDIO_BYTES = 25 * 1024 * 1024;
var AUDIO_MIMES = {
  "audio/webm": 1, "video/webm": 1, "audio/ogg": 1, "audio/opus": 1, "application/ogg": 1,
  "audio/wav": 1, "audio/x-wav": 1, "audio/wave": 1, "audio/vnd.wave": 1,
  "audio/mpeg": 1, "audio/mp3": 1, "audio/mp4": 1, "audio/x-m4a": 1, "audio/m4a": 1,
  "video/mp4": 1, "audio/flac": 1, "audio/x-flac": 1, "audio/aac": 1, "audio/x-aac": 1, "audio/amr": 1
};
function cleanMime(mime) { return String(mime || "").split(";", 1)[0].trim().toLowerCase(); }
function dataUrlBytes(dataUrl) {
  var body = String(dataUrl || "").split(",", 2)[1] || "";
  return Math.floor(body.replace(/\s/g, "").length * 3 / 4);
}
function checkAudio(dataUrl, mime) {
  var clean = cleanMime(mime);
  if (!AUDIO_MIMES[clean]) throw new Error(t("record.error_format"));
  if (dataUrlBytes(dataUrl) > MAX_AUDIO_BYTES) throw new Error(t("record.too_big"));
  return clean;
}
function blobToDataUrl(blob) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () { resolve(String(reader.result || "")); };
    reader.onerror = function () { reject(new Error(t("record.error"))); };
    reader.readAsDataURL(blob);
  });
}
function dataUrlToBlob(dataUrl) {
  var parts = String(dataUrl || "").split(",", 2);
  var mime = cleanMime((parts[0].match(/data:([^;]+)/) || [])[1]);
  var binary = atob(parts[1] || "");
  var bytes = new Uint8Array(binary.length);
  for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function displayCaptureAllowed() {
  return { allowed: !!(nativeStatus && nativeStatus.available), reason: nativeStatus && nativeStatus.reason || "helper_unavailable" };
}
function systemUnavailableMessage(reason) {
  if (reason === "capture-timeout") return t("record.capture_timeout");
  /* "policy" never reaches a status line any more (see
   * applyCaptureAvailability / markCaptureBlocked): the neutral
   * label is the only wording for it, here and in the UI. */
  if (reason === "policy") return t("record.state_unavailable");
  if (reason === "denied") return t("record.system_denied");
  if (reason === "unsupported") return t("record.system_unavailable_context");
  if (reason === "no-audio") return t("record.system_no_audio");
  if (reason === "native-unavailable") return t("record.native_unavailable_fallback", { reason: nativeReasonText(nativeStatus && nativeStatus.reason) });
  if (reason === "no-devices") return t("record.native_no_devices");
  if (reason === "busy") return t("record.native_reason_busy");
  if (reason === "device-lost") return t("record.native_reason_device_lost");
  if (reason === "helper_unavailable") return t("record.native_reason_helper_unavailable");
  return t("record.system_unavailable");
}
function nativeReasonText(reason) {
  /* The helper reports short codes; the UI shows a human reason.
   * Every code from meet_native_audio.py has a text here — a raw
   * code must never reach the user. */
  if (reason === "no_devices") return t("record.native_reason_no_devices");
  if (reason === "no_backend") return t("record.native_reason_no_backend");
  if (reason === "portaudio_missing") return t("record.native_reason_portaudio_missing");
  if (reason === "helper_unavailable") return t("record.native_reason_helper_unavailable");
  if (reason === "unsupported_platform") return t("record.native_reason_unsupported");
  if (reason === "capture_failed") return t("record.native_reason_capture_failed");
  if (reason === "device_not_found") return t("record.native_reason_device_not_found");
  if (reason === "session_busy") return t("record.native_reason_busy");
  if (reason === "timeout") return t("record.native_reason_timeout");
  if (reason === "invalid_selection") return t("record.native_reason_invalid_selection");
  if (reason === "device_lost") return t("record.native_reason_device_lost");
  return t("record.native_reason_helper_unavailable");
}
function nativeDevices() {
  return (nativeStatus && Array.isArray(nativeStatus.devices)) ? nativeStatus.devices : [];
}
function nativeDeviceName(id) {
  var found = nativeDevices().filter(function (d) { return d.id === id; })[0];
  if (found && found.name) return found.name;
  return String(id || "");
}
function effectiveCaptureMode() { return "native"; }
function devicesFor(source) {
  var available = nativeDevices();
  var inputs = available.filter(function (d) { return d.kind === "input"; });
  var outputs = available.filter(function (d) { return d.kind === "output"; });
  var selected = outputs.filter(function (d) { return settings.system_devices.includes(d.id); });
  if (!selected.length && outputs.length) selected = [outputs.find(function (d) { return d.is_default; }) || outputs[0]];
  var input = inputs.find(function (d) { return d.id === settings.mic_device; })
    || inputs.find(function (d) { return d.is_default; }) || inputs[0];
  if (source === "system") return selected;
  if (source === "microphone") return input ? [input] : [];
  return input && selected.length ? selected.concat(input) : [];
}
function systemCaptureOpts(source) {
  var devices = devicesFor(source || selectedSource()).map(function (d) { return d.id; });
  var gains = {};
  devices.forEach(function (id) {
    if (typeof settings.system_gains[id] === "number") gains[id] = settings.system_gains[id];
  });
  return { mode: "native", strictDevices: true, devices: devices, gains: gains };
}
function emptyEl(el) {
  /* innerHTML = "" is enough in a browser, but it leaves children in
   * place in a stubbed document: remove them explicitly, so a
   * re-render never duplicates rows. */
  if (!el) return;
  while (el.children && el.children.length) el.removeChild(el.children[0]);
}
function renderNativeDevices() {
  /* Step 1: the mode selector and the device list. The block only
   * shows for a system source with capture available; before the
   * probe answers it stays hidden — nothing is claimed. Rows are
   * rebuilt from the probe result, so a device that disappeared is
   * gone instead of lingering as a stale checkbox. Names come from
   * the OS/helper and are set as text, never as HTML. */
  var block = $("#native-block");
  if (!block) return;
  var source = selectedSource();
  var show = source === "system" || source === "both";
  block.hidden = !show;
  if (!show) return;
  var status = $("#native-status");
  if (status) {
    if (!nativeStatus) status.textContent = "";
    else if (nativeStatus.available) status.textContent = t("record.native_check_hint");
    else status.textContent = t("record.native_unavailable_fallback", { reason: nativeReasonText(nativeStatus.reason) });
  }
  var list = $("#native-devices");
  if (!list) return;
  emptyEl(list);
  if (!nativeStatus) return;
  var devices = nativeDevices().filter(function (d) { return d.kind === "output"; });
  if (!devices.length) {
    var empty = document.createElement("p");
    empty.className = "mm-hint";
    empty.textContent = t("record.native_no_devices");
    list.appendChild(empty);
    return;
  }
  var other = document.createElement("details");
  other.className = "mm-native-other";
  other.open = !!list.dataset.expanded;
  other.addEventListener("toggle", function () { list.dataset.expanded = other.open ? "1" : ""; });
  var summary = document.createElement("summary");
  summary.textContent = t("record.other_outputs");
  other.appendChild(summary);
  devices.forEach(function (device) {
    var row = document.createElement("label");
    row.className = "mm-native-device" + (nativeLost[device.id] ? " lost" : "");
    row.dataset.device = device.id;
    var check = document.createElement("input");
    check.type = "checkbox";
    check.className = "mm-native-check";
    check.checked = devicesFor("system").some(function (d) { return d.id === device.id; });
    check.addEventListener("change", function () {
      var chosen = devicesFor("system").map(function (d) { return d.id; });
      var at = chosen.indexOf(device.id);
      if (check.checked && at < 0) chosen.push(device.id);
      if (!check.checked && at >= 0) chosen.splice(at, 1);
      settings.system_devices = chosen;
      scheduleSaveSettings();
      renderNativeDevices();
    });
    row.appendChild(check);
    var name = document.createElement("span");
    name.className = "mm-native-name";
    name.textContent = nativeDeviceName(device.id);
    name.title = name.textContent;
    row.appendChild(name);
    var kind = document.createElement("span");
    kind.className = "mm-native-kind";
    kind.textContent = device.kind === "input" ? t("record.native_kind_input") : t("record.native_kind_output");
    if (device.is_default) { kind.textContent = t("record.native_default"); row.appendChild(kind); }
    var gain = document.createElement("input");
    gain.type = "range";
    gain.min = "0";
    gain.max = "100";
    gain.value = String(Math.round((typeof settings.system_gains[device.id] === "number" ? settings.system_gains[device.id] : 1) * 100));
    gain.className = "mm-native-gain";
    gain.hidden = !check.checked;
    gain.setAttribute("aria-label", t("record.native_gain") + ": " + nativeDeviceName(device.id));
    gain.addEventListener("input", function () {
      var value = Math.max(0, Math.min(1, Number(gain.value) / 100));
      settings.system_gains[device.id] = value;
      scheduleSaveSettings();
      if ((hostSystemCheck || (recording && recording.hostSystem)) && Memu.record && Memu.record.system) {
        Memu.record.system("gain", { device: device.id, gain: value }).catch(function () {});
      }
    });
    row.appendChild(gain);
    var level = document.createElement("span");
    level.className = "mm-native-level";
    var bar = document.createElement("i");
    level.appendChild(bar);
    if (check.checked) row.appendChild(level);
    (check.checked ? list : other).appendChild(row);
  });
  if (other.children.length > 1) list.appendChild(other);
  updateNativeLevels(nativeLevels);
}
function updateNativeLevels(levels) {
  /* Per-device level bars in step 1 and in the step-2 check block.
   * A missing device simply keeps its last width; unknown ids are
   * ignored, so a stale event cannot invent a row. */
  nativeLevels = levels || {};
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  var rows = document.querySelectorAll(".mm-native-device");
  Array.prototype.forEach.call(rows, function (row) {
    var id = row.dataset && row.dataset.device;
    if (!id || typeof nativeLevels[id] !== "number") return;
    var bar = row.querySelector ? row.querySelector(".mm-native-level i") : null;
    if (bar) bar.style.width = Math.round(Math.max(0, Math.min(1, nativeLevels[id])) * 100) + "%";
  });
  var checkRows = document.querySelectorAll("#native-levels .mm-native-level-row");
  Array.prototype.forEach.call(checkRows, function (row) {
    var id = row.dataset && row.dataset.device;
    if (!id || typeof nativeLevels[id] !== "number") return;
    var bar = row.querySelector ? row.querySelector("i") : null;
    if (bar) bar.style.width = Math.round(Math.max(0, Math.min(1, nativeLevels[id])) * 100) + "%";
  });
  var diagRows = document.querySelectorAll("#diag-levels .mm-native-level-row");
  Array.prototype.forEach.call(diagRows, function (row) {
    var id = row.dataset && row.dataset.device;
    if (!id || typeof nativeLevels[id] !== "number") return;
    var bar = row.querySelector ? row.querySelector("i") : null;
    if (bar) bar.style.width = Math.round(Math.max(0, Math.min(1, nativeLevels[id])) * 100) + "%";
  });
}
function markNativeDeviceLost(deviceId) {
  nativeLost[deviceId] = true;
  if (typeof document === "undefined" || !document.querySelectorAll) return;
  var rows = document.querySelectorAll(".mm-native-device");
  Array.prototype.forEach.call(rows, function (row) {
    if (row.dataset && row.dataset.device === deviceId) row.classList.add("lost");
  });
}
function clearNativeLevels() {
  nativeLevels = {};
  nativeLost = {};
  updateNativeLevels({});
}

/* ---- audio diagnostics (Settings): helper status, devices, last error ---- */
var lastNativeError = null; /* {at, code, detail} from the host probe */
var diagTest = null; /* {peaks: {id: max}, timer} while a sound test runs */
function renderDiagnostics() {
  /* Settings → "Диагностика звука": what the helper is, what it found,
   * and the last error in plain words. Nothing here starts a capture;
   * the probe result (nativeStatus) is the source of truth. */
  var block = $("#diag-block");
  if (!block) return;
  var status = $("#diag-status");
  var list = $("#diag-devices");
  var err = $("#diag-error");
  if (!nativeStatus) {
    if (status) status.textContent = t("record.diag_unknown");
    if (list) emptyEl(list);
    if (err) err.textContent = "";
    return;
  }
  if (status) {
    var sd = nativeStatus.sounddevice || {};
    var bits = [];
    bits.push(nativeStatus.available ? t("record.diag_ok") : t("record.diag_unavailable", { reason: nativeReasonText(nativeStatus.reason) }));
    bits.push(t("record.diag_backend", { backend: nativeStatus.backend || "none" }));
    if (sd.installed === true) bits.push(t("record.diag_sd_ok", { version: sd.version || "" }));
    else if (sd.installed === false) bits.push(t("record.diag_sd_missing"));
    if (sd.portaudio === false) bits.push(t("record.diag_portaudio_missing"));
    status.textContent = bits.join(" · ");
  }
  if (list) {
    emptyEl(list);
    var devices = nativeDevices();
    if (!devices.length) {
      var empty = document.createElement("p");
      empty.className = "mm-hint";
      empty.textContent = t("record.native_no_devices");
      list.appendChild(empty);
    }
    devices.forEach(function (device) {
      var row = document.createElement("p");
      row.className = "mm-diag-device";
      row.textContent = nativeDeviceName(device.id) + " — " +
        (device.kind === "input" ? t("record.native_kind_input") : t("record.native_kind_output")) +
        ", " + (device.channels || 2) + " " + t("record.diag_channels") +
        ", " + Math.round((device.default_samplerate || 48000) / 100) / 10 + " " + t("record.diag_khz");
      list.appendChild(row);
    });
  }
  if (err) {
    var last = lastNativeError || (nativeStatus && nativeStatus.last_error);
    if (last && last.code) {
      var when = "";
      try { when = last.at ? new Date(last.at).toLocaleTimeString() : ""; } catch (e) {}
      err.textContent = t("record.diag_last_error", {
        error: nativeReasonText(last.code) + (last.detail ? " — " + last.detail : "") + (when ? " (" + when + ")" : "")
      });
    } else {
      err.textContent = t("record.diag_no_error");
    }
  }
  renderDiagLevels();
}
function renderDiagLevels() {
  /* Per-device level rows for the sound test. Rows are rebuilt on
   * every render; updateNativeLevels fills their bars from live events. */
  var levels = $("#diag-levels");
  if (!levels) return;
  emptyEl(levels);
  if (!diagTest) return;
  nativeDevices().forEach(function (device) {
    var row = document.createElement("div");
    row.className = "mm-native-level-row";
    row.dataset.device = device.id;
    var name = document.createElement("span");
    name.className = "mm-native-level-name";
    name.textContent = nativeDeviceName(device.id);
    row.appendChild(name);
    var meter = document.createElement("span");
    meter.className = "mm-native-level";
    meter.appendChild(document.createElement("i"));
    row.appendChild(meter);
    levels.appendChild(row);
  });
  updateNativeLevels(nativeLevels);
}
function runNativeDiagnostics() {
  /* A 5-second native capture test: start a "check" session in native
   * mode, watch the per-device levels, then stop and say which devices
   * actually produced sound. Silence is a result, not a failure — the
   * session must survive a quiet machine (see fixlog-meet-audio2 F1). */
  if (diagTest || recording || hostSystemCheck) return;
  var status = $("#diag-status");
  if (!nativeStatus || !nativeStatus.available) {
    if (status) status.textContent = t("record.diag_native_unavailable");
    return;
  }
  if (!Memu.record || !Memu.record.system) return;
  var opts = systemCaptureOpts();
  opts.mode = "native";
  diagTest = { peaks: {} };
  renderDiagLevels();
  if (status) status.textContent = t("record.diag_running");
  Memu.record.system("check", opts).then(function (result) {
    if (!diagTest) return;
    if (!result || result.reason) {
      diagTest = null;
      renderDiagLevels();
      renderDiagnostics(); // first: it rewrites #diag-status from the probe
      if (status) status.textContent = systemUnavailableMessage(result && result.reason);
      return;
    }
    diagTest.timer = setTimeout(finishNativeDiagnostics, 5000);
  }).catch(function (error) {
    diagTest = null;
    renderDiagLevels();
    renderDiagnostics(); // first: it rewrites #diag-status from the probe
    if (status) status.textContent = String((error && error.message) || error);
  });
}
function finishNativeDiagnostics() {
  if (!diagTest) return;
  var test = diagTest;
  diagTest = null;
  if (Memu.record && Memu.record.system) Memu.record.system("cancel").catch(function () {});
  clearNativeLevels();
  renderDiagLevels();
  var heard = [], silent = [];
  nativeDevices().forEach(function (device) {
    var peak = test.peaks[device.id] || 0;
    (peak > 0.02 ? heard : silent).push(nativeDeviceName(device.id));
  });
  renderDiagnostics(); // first: it rewrites #diag-status from the probe
  var status = $("#diag-status");
  if (status) {
    var parts = [];
    if (heard.length) parts.push(t("record.diag_heard", { devices: heard.join(", ") }));
    if (silent.length) parts.push(t("record.diag_silent", { devices: silent.join(", ") }));
    status.textContent = parts.join(" ") || t("record.native_no_devices");
  }
}
function diagTestObserve(levels) {
  if (!diagTest) return;
  Object.keys(levels || {}).forEach(function (id) {
    var value = Number(levels[id]) || 0;
    if (value > (diagTest.peaks[id] || 0)) diagTest.peaks[id] = value;
  });
}
var diagCheckBtn = $("#diag-check");
if (diagCheckBtn) diagCheckBtn.addEventListener("click", runNativeDiagnostics);
var diagRefreshBtn = $("#diag-refresh");
if (diagRefreshBtn) diagRefreshBtn.addEventListener("click", function () {
  if (!Memu.record || !Memu.record.system) return;
  Memu.record.system("probe", { force: true }).then(function (result) {
    if (!result) return;
    if (result.native) {
      nativeStatus = result.native;
      if (result.native.last_error) lastNativeError = result.native.last_error;
    }
    renderNativeDevices();
    refreshSystemCheckUI();
    renderDiagnostics();
  }).catch(function () {});
});

/* ---- pause segmentation (mirror of backend.py: segment_energy) ---- */
function segmentEnergy(energies, frameSeconds) {
  var frame = frameSeconds || 0.1, threshold = 0.02, minSilence = 0.5, minSegment = 0.5;
  var need = Math.max(1, Math.round(minSilence / frame));
  var segments = [], start = -1, silence = 0;
  energies.forEach(function (energy, index) {
    if (energy < threshold) {
      silence += 1;
      if (start >= 0 && silence >= need) {
        var end = index - silence + 1;
        if (end > start) segments.push({ start: +(start * frame).toFixed(2), end: +(end * frame).toFixed(2) });
        start = -1;
      }
    } else {
      silence = 0;
      if (start < 0) start = index;
    }
  });
  if (start >= 0) segments.push({ start: +(start * frame).toFixed(2), end: +(energies.length * frame).toFixed(2) });
  var merged = [];
  segments.forEach(function (seg) {
    var prev = merged[merged.length - 1];
    if (prev && (seg.end - seg.start < minSegment || prev.end - prev.start < minSegment)) prev.end = seg.end;
    else merged.push({ start: seg.start, end: seg.end });
  });
  return merged;
}
function assignSpeakers(segments, enabled) {
  var out = [], speaker = 0, prevEnd = null;
  segments.forEach(function (seg) {
    var row = { start: seg.start, end: seg.end };
    if (enabled && prevEnd !== null && seg.start - prevEnd >= 2) speaker = 1 - speaker;
    row.speaker = "speaker-" + (speaker + 1);
    out.push(row);
    prevEnd = seg.end;
  });
  return out;
}
function distributeText(segments, text) {
  var words = String(text || "").split(/\s+/).filter(Boolean);
  if (!segments.length) {
    return text.trim() ? [{ start: 0, end: 0, speaker: "speaker-1", text: text.trim(), approximate: true }] : [];
  }
  if (!words.length) {
    return segments.map(function (s) { return Object.assign({}, s, { text: "", approximate: true }); });
  }
  var total = segments.reduce(function (sum, s) { return sum + Math.max(0, s.end - s.start); }, 0) || 1;
  var cursor = 0;
  return segments.map(function (seg, index) {
    var take = index === segments.length - 1
      ? words.length - cursor
      : Math.round(Math.max(0, seg.end - seg.start) / total * words.length);
    var row = Object.assign({}, seg, { text: words.slice(cursor, cursor + take).join(" "), approximate: true });
    cursor += take;
    return row;
  });
}
function decodeEnergies(dataUrl) {
  /* RMS per 0.1 s from the decoded audio. decodeAudioData is offline work
   * in the module page; if the container cannot be decoded there are simply
   * no timings, and the transcript stays one block. */
  return new Promise(function (resolve) {
    try {
      var blob = dataUrlToBlob(dataUrl);
      blob.arrayBuffer().then(function (buffer) {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return resolve([]);
        new Ctx().decodeAudioData(buffer).then(function (decoded) {
          var frame = Math.max(1, Math.floor(decoded.sampleRate * 0.1));
          var energies = [];
          for (var ch = 0; ch < decoded.numberOfChannels; ch++) {
            var data = decoded.getChannelData(ch);
            for (var i = 0; i < data.length; i += frame) {
              var sum = 0, n = 0;
              for (var j = i; j < Math.min(data.length, i + frame); j++) { sum += data[j] * data[j]; n++; }
              var rms = Math.sqrt(sum / Math.max(1, n));
              var slot = Math.floor(i / frame);
              energies[slot] = Math.max(energies[slot] || 0, rms);
            }
          }
          resolve(energies);
        }).catch(function () { resolve([]); });
      }).catch(function () { resolve([]); });
    } catch (e) { resolve([]); }
  });
}
function sourceLabel(source) {
  if (source === "microphone") return t("record.you");
  if (source === "system") return t("record.system_short");
  return t("meetings.source." + source, { fallback: source });
}
function speakerLabel(id, source) {
  /* In a two-track meeting the track IS the speaker: the microphone is
   * the user, the system track is everyone else on the call. Single-track
   * meetings keep the provisional speaker numbers and renaming. */
  var multi = (recording && recording.source === "both") || (current && current.source === "both");
  if (multi && source === "microphone") return t("record.you");
  if (multi && source === "system") return t("record.system_short");
  var names = (current && current.speaker_names) || {};
  if (names[id]) return names[id];
  var order = ["speaker-1", "speaker-2"];
  return t("common.speaker") + " " + (order.indexOf(id) + 1 || 1);
}
function transcriptText(segments) {
  var sources = {};
  (segments || []).forEach(function (seg) { if (seg.source) sources[seg.source] = true; });
  var showSource = Object.keys(sources).length > 1;
  return (segments || []).map(function (seg) {
    var who = showSource && seg.source ? sourceLabel(seg.source) : speakerLabel(seg.speaker, seg.source);
    return "[" + fmtTime(seg.start) + "] " + who + ": " + (seg.text || "");
  }).join("\n");
}

/* ---- views ---- */
function show(view) {
  $$(".mm-view").forEach(function (section) { section.hidden = section.id !== "view-" + view; });
  $$(".mm-nav button").forEach(function (btn) {
    btn.classList.toggle("active", btn.dataset.view === view);
    btn.setAttribute("aria-selected", btn.dataset.view === view ? "true" : "false");
  });
  if (view === "captions") renderCaptions();
  if (view === "settings") renderStorage();
  var main = document.querySelector(".mm-main");
  if (main && main.scrollTo) main.scrollTo(0, 0);
}
$$(".mm-nav button").forEach(function (btn) {
  btn.addEventListener("click", function () { show(btn.dataset.view); });
});

function meetingSourceLabel(source) {
  return t("meetings.source." + source, { fallback: source });
}
function loadMeetings() {
  $("#list-status").textContent = t("common.loading");
  return Memu.store.list("meetings/").then(function (rows) {
    var keys = (rows || []).map(function (row) { return row.key; }).filter(isMeetingKey);
    if (!keys.length) return [];
    return Memu.store.getMany(keys).then(function (values) {
      return keys.map(function (key) { return values[key]; }).filter(function (row) { return row && row.id; });
    });
  }).then(function (rows) {
    meetings = rows.sort(function (a, b) { return (b.created_at || 0) - (a.created_at || 0); });
    renderList();
    $("#list-status").textContent = "";
  }).catch(function (error) {
    meetings = [];
    renderList();
    $("#list-status").textContent = String((error && error.message) || error);
  });
}
/* ---- the list: stats, filter chips, sort, day groups, stars ---- */
var listFilter = "all";
var listSort = "new";
var GROUP_KEYS = { today: "meetings.group_today", yesterday: "meetings.group_yesterday", week: "meetings.group_week",
  month: "meetings.group_month", earlier: "meetings.group_earlier" };
function meetingTasks(row) { return row && Array.isArray(row.tasks) ? row.tasks : []; }
function openTaskCount(row) { return meetingTasks(row).filter(function (task) { return task && !task.done; }).length; }
function hasTranscriptRow(row) {
  return !!(row && (row.has_transcript || String(row.transcript || "").trim() || (row.segments || []).length));
}
function hasSummaryRow(row) { return !!(row && String(row.summary || "").trim()); }
function matchesFilter(row, filter) {
  if (filter === "starred") return !!row.starred;
  if (filter === "todo") return !hasTranscriptRow(row);
  if (filter === "nosummary") return hasTranscriptRow(row) && !hasSummaryRow(row);
  if (filter === "tasks") return openTaskCount(row) > 0;
  return true;
}
function summarySnippet(row) {
  var lines = String(row.summary || "").split(/\r?\n/);
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (!line || /^#{1,6}\s/.test(line) || /^\*\*[^*]+\*\*:?$/.test(line)) continue;
    var text = plainText(line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, "").replace(/^\[( |x|X)\]\s*/, ""));
    if (text.length > 3) return text.length > 160 ? text.slice(0, 157) + "…" : text;
  }
  var transcript = plainText(row.transcript || "");
  return transcript.length > 160 ? transcript.slice(0, 157) + "…" : transcript;
}
function highlightText(text, query) {
  var raw = String(text == null ? "" : text);
  var q = String(query || "").toLowerCase();
  if (!q) return esc(raw);
  var lower = raw.toLowerCase(), out = "", i = 0, j;
  while ((j = lower.indexOf(q, i)) >= 0) {
    out += esc(raw.slice(i, j)) + "<mark>" + esc(raw.slice(j, j + q.length)) + "</mark>";
    i = j + q.length;
  }
  return out + esc(raw.slice(i));
}
function dayBucket(ms) {
  var now = new Date();
  var start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  var value = +ms || 0;
  if (value >= start) return "today";
  if (value >= start - 86400000) return "yesterday";
  if (value >= start - 6 * 86400000) return "week";
  if (value >= start - 29 * 86400000) return "month";
  return "earlier";
}
function fmtTotal(ms) {
  var minutes = Math.round((+ms || 0) / 60000);
  if (minutes < 60) return t("meetings.stat_min", { n: minutes });
  return t("meetings.stat_hours", { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, "0") });
}
function renderStats() {
  var box = $("#stats");
  if (!box) return;
  if (!meetings.length) { box.hidden = true; box.innerHTML = ""; return; }
  var total = 0, transcribed = 0, summarized = 0, open = 0, all = 0;
  meetings.forEach(function (row) {
    total += +row.duration_ms || 0;
    if (hasTranscriptRow(row)) transcribed++;
    if (hasSummaryRow(row)) summarized++;
    meetingTasks(row).forEach(function (task) { all++; if (!task.done) open++; });
  });
  var tiles = [
    { n: String(meetings.length), label: t("meetings.stat_meetings"), icon: ICON.mic },
    { n: fmtTotal(total), label: t("meetings.stat_duration"), icon: ICON.clock },
    { n: transcribed + "/" + meetings.length, label: t("meetings.stat_transcribed"), icon: ICON.text },
    { n: String(summarized), label: t("meetings.stat_summaries"), icon: ICON.spark },
    { n: String(open), label: t("meetings.stat_open_tasks"), icon: ICON.check, hot: open > 0, filter: "tasks" }
  ];
  box.hidden = false;
  box.innerHTML = tiles.map(function (tile) {
    var tag = tile.filter ? "button" : "div";
    return "<" + tag + (tile.filter ? ' type="button" data-stat-filter="' + tile.filter + '"' : "") +
      ' class="mm-stat' + (tile.hot ? " hot" : "") + '"><span class="mm-stat-ico">' + tile.icon + "</span>" +
      "<span><b>" + esc(tile.n) + "</b><small>" + esc(tile.label) + "</small></span></" + tag + ">";
  }).join("");
}
function sortedRows(rows) {
  var copy = rows.slice();
  if (listSort === "old") copy.sort(function (a, b) { return (a.created_at || 0) - (b.created_at || 0); });
  else if (listSort === "long") copy.sort(function (a, b) { return (b.duration_ms || 0) - (a.duration_ms || 0); });
  else if (listSort === "title") {
    copy.sort(function (a, b) {
      return String(a.title || "").localeCompare(String(b.title || ""), en() ? "en" : "ru", { sensitivity: "base" });
    });
  } else copy.sort(function (a, b) { return (b.created_at || 0) - (a.created_at || 0); });
  return copy;
}
function itemHtml(row, query) {
  var tags = [];
  tags.push(hasTranscriptRow(row) ? '<span class="mm-tag done">' + esc(t("meetings.has_transcript")) + "</span>"
    : '<span class="mm-tag todo">' + esc(t("meetings.tag_todo")) + "</span>");
  if (hasSummaryRow(row)) tags.push('<span class="mm-tag done">' + esc(t("meetings.has_summary")) + "</span>");
  var tasks = meetingTasks(row);
  if (tasks.length) {
    var open = openTaskCount(row);
    tags.push('<span class="mm-tag ' + (open ? "warn" : "done") + '">' + ICON.check + esc((tasks.length - open) + "/" + tasks.length) + "</span>");
  }
  if (Array.isArray(row.markers) && row.markers.length) tags.push('<span class="mm-tag">' + ICON.flag + esc(String(row.markers.length)) + "</span>");
  var snippet = summarySnippet(row);
  var title = row.title || t("common.untitled");
  return '<article class="mm-item' + (row.starred ? " starred" : "") + '" data-id="' + esc(row.id) + '">' +
    '<button type="button" class="mm-item-open" data-open="' + esc(row.id) + '">' +
    '<span class="mm-item-ico src-' + esc(row.source || "microphone") + '">' + sourceIcon(row.source) + "</span>" +
    '<span class="mm-item-main"><b>' + highlightText(title, query) + "</b>" +
    '<span class="mm-item-meta">' + esc(fmtClock(row.created_at)) + " · " + esc(meetingSourceLabel(row.source)) +
    (row.duration_ms ? " · " + esc(fmtTime(row.duration_ms / 1000)) : "") + "</span>" +
    (snippet ? '<span class="mm-item-snippet">' + highlightText(snippet, query) + "</span>" : "") + "</span>" +
    '<span class="mm-item-tags">' + tags.join("") + "</span></button>" +
    '<button type="button" class="mm-item-star' + (row.starred ? " on" : "") + '" data-star="' + esc(row.id) + '" aria-pressed="' +
    (row.starred ? "true" : "false") + '" title="' + esc(t("detail.star")) + '" aria-label="' + esc(t("detail.star")) + '">' + ICON.star + "</button>" +
    "</article>";
}
function renderList() {
  var query = ($("#search").value || "").trim().toLowerCase();
  var base = meetings.filter(function (row) {
    return !query || (row.title || "").toLowerCase().indexOf(query) >= 0 ||
      (row.transcript || "").toLowerCase().indexOf(query) >= 0 ||
      (row.summary || "").toLowerCase().indexOf(query) >= 0 ||
      (row.notes || "").toLowerCase().indexOf(query) >= 0;
  });
  var counts = { all: base.length, starred: 0, todo: 0, nosummary: 0, tasks: 0 };
  base.forEach(function (row) {
    ["starred", "todo", "nosummary", "tasks"].forEach(function (name) { if (matchesFilter(row, name)) counts[name]++; });
  });
  $$("#filters [data-count]").forEach(function (el) {
    var n = counts[el.dataset.count] || 0;
    el.textContent = n ? String(n) : "";
  });
  $$("#filters [data-filter]").forEach(function (btn) {
    var on = btn.dataset.filter === listFilter;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  var rows = sortedRows(base.filter(function (row) { return matchesFilter(row, listFilter); }));
  $("#list-empty").hidden = meetings.length > 0;
  $("#list-nomatch").hidden = !(meetings.length > 0 && rows.length === 0);
  $("#filters").hidden = meetings.length === 0;
  var html = "";
  if (listSort === "new" || listSort === "old") {
    var last = "";
    rows.forEach(function (row) {
      var bucket = dayBucket(row.created_at);
      if (bucket !== last) { html += '<h3 class="mm-group">' + esc(t(GROUP_KEYS[bucket])) + "</h3>"; last = bucket; }
      html += itemHtml(row, query);
    });
  } else {
    html = rows.map(function (row) { return itemHtml(row, query); }).join("");
  }
  $("#list").innerHTML = html;
  renderStats();
}
function findMeeting(id) { return meetings.filter(function (m) { return m.id === id; })[0] || null; }
function toggleStar(id) {
  var row = findMeeting(id) || (current && current.id === id ? current : null);
  if (!row) return;
  row.starred = !row.starred;
  storeSet(meetingKey(row.id), row).then(function () {
    renderList();
    if (current && current.id === row.id) renderDetailHead();
  }).catch(function (error) { row.starred = !row.starred; toast(errText(error)); });
}
function closestIn(event, selector) {
  var node = event && event.target;
  return node && node.closest ? node.closest(selector) : null;
}
$("#list").addEventListener("click", function (event) {
  var star = closestIn(event, "[data-star]");
  if (star) { toggleStar(star.dataset.star); return; }
  var open = closestIn(event, "[data-open]");
  if (open) openMeeting(open.dataset.open);
});
$("#filters").addEventListener("click", function (event) {
  var chip = closestIn(event, "[data-filter]");
  if (!chip) return;
  listFilter = chip.dataset.filter || "all";
  renderList();
});
$("#stats").addEventListener("click", function (event) {
  var tile = closestIn(event, "[data-stat-filter]");
  if (!tile) return;
  listFilter = listFilter === tile.dataset.statFilter ? "all" : tile.dataset.statFilter;
  renderList();
});
$("#sort").addEventListener("change", function () { listSort = $("#sort").value || "new"; renderList(); });
$("#search").addEventListener("input", renderList);
$("#record-open").addEventListener("click", function () { show("record"); setStep(1); });
function goRecord() { show("record"); setStep(1); }
var emptyNew = $("#empty-new");
if (emptyNew) emptyNew.addEventListener("click", goRecord);
var captionsGo = $("#captions-go-record");
if (captionsGo) captionsGo.addEventListener("click", goRecord);
$("#import-open").addEventListener("click", function () {
  show("record");
  setStep(1);
  document.querySelector('input[name="source"][value="import"]').checked = true;
  syncSourceUI();
});

/* ---- detail ---- */
function openMeeting(id) {
  var row = meetings.filter(function (m) { return m.id === id; })[0];
  if (!row) return;
  flushNotes();
  current = row;
  currentAudio = "";
  currentMicAudio = "";
  segQuery = "";
  var segSearch = $("#seg-search");
  if (segSearch) segSearch.value = "";
  finishRename(false);
  detailTab = (row.summary || "").trim() ? "summary" : hasTranscriptRow(row) ? "transcript" : "summary";
  show("detail");
  renderDetail();
  storeGet(audioKey(id), null).then(function (value) {
    if (value && value.data) {
      currentAudio = String(value.data);
      var player = $("#player");
      player.src = currentAudio;
      player.hidden = false;
      ensureAudioDuration(player);
      $("#audio-missing").hidden = true;
    } else {
      $("#player").removeAttribute("src");
      $("#player").hidden = true;
      $("#audio-missing").hidden = !row.has_audio;
    }
  }).catch(function () { $("#audio-missing").hidden = false; });
  if (row.tracks && row.tracks.some(function (tr) { return tr.source === "microphone"; })) {
    storeGet(audioMicKey(id), null).then(function (value) {
      if (value && value.data) {
        currentMicAudio = String(value.data);
        $("#player-mic").src = currentMicAudio;
        ensureAudioDuration($("#player-mic"));
        $("#player-mic-wrap").hidden = false;
      }
    }).catch(function () {});
  } else {
    $("#player-mic-wrap").hidden = true;
  }
}
$("#back").addEventListener("click", function () { flushNotes(); finishRename(true); show("meetings"); loadMeetings(); });
var detailTab = "summary";
var segQuery = "";
var STYLE_KEYS = { standard: "summary.style_standard", brief: "summary.style_brief", detailed: "summary.style_detailed",
  protocol: "summary.style_protocol", actions: "summary.style_actions", decisions: "summary.style_decisions",
  followup: "summary.style_followup" };
var STYLE_HINT_KEYS = { brief: "summary.hint_brief", detailed: "summary.hint_detailed", protocol: "summary.hint_protocol",
  actions: "summary.hint_actions", decisions: "summary.hint_decisions", followup: "summary.hint_followup" };
function renderDetailHead() {
  if (!current) return;
  $("#detail-title").textContent = current.title || t("common.untitled");
  $("#detail-meta").textContent = fmtClock(current.created_at) + " · " + meetingSourceLabel(current.source) +
    (current.duration_ms ? " · " + fmtTime(current.duration_ms / 1000) : "");
  var star = $("#detail-star");
  star.classList.toggle("on", !!current.starred);
  star.setAttribute("aria-pressed", current.starred ? "true" : "false");
  var chips = [];
  chips.push(hasTranscriptRow(current) ? '<span class="mm-tag done">' + ICON.text + esc(t("meetings.has_transcript")) + "</span>"
    : '<span class="mm-tag todo">' + esc(t("meetings.tag_todo")) + "</span>");
  if (hasSummaryRow(current)) chips.push('<span class="mm-tag done">' + ICON.spark + esc(t("meetings.has_summary")) + "</span>");
  var tasks = meetingTasks(current);
  if (tasks.length) {
    var open = openTaskCount(current);
    chips.push('<span class="mm-tag ' + (open ? "warn" : "done") + '">' + ICON.check +
      esc(t("detail.tasks_progress", { done: tasks.length - open, total: tasks.length })) + "</span>");
  }
  if (Array.isArray(current.markers) && current.markers.length) {
    chips.push('<span class="mm-tag">' + ICON.flag + esc(t("detail.markers_n", { n: current.markers.length })) + "</span>");
  }
  if (current.tracks && current.tracks.length > 1) chips.push('<span class="mm-tag">' + ICON.both + esc(t("detail.two_tracks")) + "</span>");
  $("#detail-chips").innerHTML = chips.join("");
}
function setCount(sel, value) { var el = $(sel); if (el) el.textContent = value ? String(value) : ""; }
function renderTabCounts() {
  if (!current) return;
  setCount("#dtab-transcript-count", (current.segments || []).length || "");
  var tasks = meetingTasks(current);
  setCount("#dtab-tasks-count", tasks.length ? openTaskCount(current) || "" : "");
  setCount("#dtab-notes-count", String(current.notes || "").trim() ? "•" : "");
  setCount("#dtab-ask-count", Array.isArray(current.qa) && current.qa.length ? current.qa.length : "");
}
function showDetailTab(name) {
  detailTab = name || "summary";
  $$("#detail-tabs [data-dtab]").forEach(function (btn) {
    var on = btn.dataset.dtab === detailTab;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  $$(".mm-dpane").forEach(function (pane) { pane.hidden = pane.dataset.dpane !== detailTab; });
  if (detailTab === "export") refreshExport();
}
function summaryMeta() {
  if (!current || !(current.summary || "").trim()) return "";
  var parts = [];
  if (current.summary_style && STYLE_KEYS[current.summary_style]) parts.push(t(STYLE_KEYS[current.summary_style]));
  if (current.summary_model) {
    var row = models.filter(function (m) { return m.id === current.summary_model; })[0];
    parts.push(row ? (row.name || row.model || row.id) : current.summary_model);
  }
  if (current.summarized_at) parts.push(fmtClock(current.summarized_at));
  return parts.join(" · ");
}
function renderDetail() {
  if (!current) return;
  renderDetailHead();
  var hasSummary = !!(current.summary || "").trim();
  $("#summary-card").hidden = !hasSummary;
  $("#summary-empty").hidden = hasSummary;
  $("#summary").innerHTML = hasSummary ? renderMarkdown(current.summary) : "";
  $("#summary-meta").textContent = summaryMeta();
  var styleSelect = $("#summary-style");
  if (styleSelect) styleSelect.value = SUMMARY_STYLES.indexOf(current.summary_style) >= 0 ? current.summary_style : settings.summary_style;
  renderSegments();
  $("#no-transcript").hidden = (current.segments || []).length > 0 || (current.transcript || "").trim() !== "";
  renderTasks();
  renderNotes();
  renderQA();
  renderTabCounts();
  showDetailTab(detailTab);
  refreshExport();
}
function segDuration(seg) {
  var span = Math.max(0, (+seg.end || 0) - (+seg.start || 0));
  return span || String(seg.text || "").length / 15;
}
function renderSpeakerStats() {
  var box = $("#speaker-stats");
  if (!box) return;
  var segs = (current && current.segments) || [];
  var totals = {}, order = [], sum = 0;
  segs.forEach(function (seg) {
    var who = speakerLabel(seg.speaker, seg.source);
    var dur = segDuration(seg);
    if (!(who in totals)) { totals[who] = 0; order.push(who); }
    totals[who] += dur;
    sum += dur;
  });
  if (order.length < 2 || !sum) { box.hidden = true; box.innerHTML = ""; return; }
  var appear = order.slice(); /* colours follow first appearance, like the avatars */
  order.sort(function (a, b) { return totals[b] - totals[a]; });
  box.hidden = false;
  box.innerHTML = '<span class="mm-speak-title">' + esc(t("detail.talk_time")) + "</span>" +
    '<div class="mm-speak-bar" aria-hidden="true">' + order.map(function (who, i) {
      return '<i class="c' + (appear.indexOf(who) % 6) + '" style="width:' + (totals[who] * 100 / sum).toFixed(1) + '%"></i>';
    }).join("") + "</div>" +
    '<div class="mm-speak-legend">' + order.map(function (who, i) {
      return '<span><i class="c' + (appear.indexOf(who) % 6) + '"></i>' + esc(who) + " <b>" + Math.round(totals[who] * 100 / sum) + "%</b></span>";
    }).join("") + "</div>";
}
function renderDetailMarkers() {
  var box = $("#detail-markers");
  if (!box) return;
  var rows = (current && Array.isArray(current.markers)) ? current.markers : [];
  box.hidden = !rows.length;
  box.innerHTML = rows.length ? '<span class="mm-speak-title">' + esc(t("detail.markers")) + "</span>" + rows.map(function (m) {
    return '<button type="button" class="mm-marker" data-seek="' + esc(String(+m.t || 0)) + '">' + ICON.flag +
      "<b>" + esc(fmtTime(m.t)) + "</b>" + esc(m.label || "") + "</button>";
  }).join("") : "";
}
function renderSegments() {
  var segments = current.segments || [];
  var hasSources = segments.some(function (s) { return !!s.source; });
  var speakers = [];
  segments.forEach(function (seg) { if (!seg.source && speakers.indexOf(seg.speaker) < 0) speakers.push(seg.speaker); });
  /* colours are fixed by first appearance over the WHOLE transcript, so searching never re-colours people */
  var tones = [];
  segments.forEach(function (seg) {
    var who = speakerLabel(seg.speaker, seg.source);
    if (tones.indexOf(who) < 0) tones.push(who);
  });
  function colorOf(who) {
    var i = tones.indexOf(who);
    if (i < 0) { tones.push(who); i = tones.length - 1; }
    return i % 6;
  }
  var namesHtml = (!hasSources && speakers.length > 1 && !segQuery) ? '<div class="mm-speaker-names">' + speakers.map(function (id) {
    return '<label class="mm-speaker-name"><span class="mm-seg-ava c' + colorOf(speakerLabel(id)) + '" aria-hidden="true">' + esc(String(speakerLabel(id) || "?").slice(0, 1)) + "</span>" +
      '<input class="mm-input" data-speaker="' + esc(id) + '" value="' + esc(speakerLabel(id)) + '" aria-label="' + esc(t("detail.rename_speaker")) + '"></label>';
  }).join("") + "</div>" : "";
  var approxNote = segments.some(function (seg) { return !!seg.approximate; })
    ? '<p class="mm-approx-note">' + esc(t("detail.approximate_note")) + "</p>" : "";
  var query = segQuery.toLowerCase();
  var shown = 0;
  var body = segments.map(function (seg) {
    if (query && String(seg.text || "").toLowerCase().indexOf(query) < 0) return "";
    shown++;
    var who = speakerLabel(seg.speaker, seg.source);
    var tone = colorOf(who);
    var badge = (seg.source && current.source === "both" && who !== sourceLabel(seg.source))
      ? '<span class="mm-src-badge">' + esc(sourceLabel(seg.source)) + "</span>" : "";
    return '<div class="mm-seg"><button type="button" class="mm-seg-time" data-seek="' + esc(String(+seg.start || 0)) + '"' +
      (seg.source ? ' data-seek-source="' + esc(seg.source) + '"' : "") + ' title="' + esc(t("detail.play_from")) + '">' + esc(fmtTime(seg.start)) + "</button>" +
      '<span class="mm-seg-ava c' + tone + '" aria-hidden="true">' + esc(String(who || "?").slice(0, 1)) + "</span>" +
      '<span class="mm-seg-who">' + esc(who) + badge + "</span>" +
      "<p>" + highlightText(seg.text || "", segQuery) + "</p></div>";
  }).join("");
  $("#segments").innerHTML = namesHtml + (body ? approxNote : "") + body;
  if (!segments.length && (current.transcript || "").trim()) {
    var plain = String(current.transcript || "");
    var hit = !query || plain.toLowerCase().indexOf(query) >= 0;
    shown = hit ? 1 : 0;
    $("#segments").innerHTML = hit ? '<div class="mm-seg plain"><p>' + highlightText(plain, segQuery) + "</p></div>" : "";
  }
  setCount("#seg-count", query ? t("detail.seg_found", { n: shown }) : "");
  $$("#segments input[data-speaker]").forEach(function (input) {
    input.addEventListener("change", function () {
      current.speaker_names = Object.assign({}, current.speaker_names);
      current.speaker_names[input.dataset.speaker] = input.value.trim().slice(0, 60) || speakerLabel(input.dataset.speaker);
      storeSet(meetingKey(current.id), current).then(function () { renderSegments(); refreshExport(); }).catch(function () {});
    });
  });
  renderSpeakerStats();
  renderDetailMarkers();
}
$("#seg-search").addEventListener("input", function () {
  segQuery = ($("#seg-search").value || "").trim().slice(0, 120);
  if (current) renderSegments();
});
function seekTo(seconds, source) {
  var player = (source === "microphone" && currentMicAudio) ? $("#player-mic") : $("#player");
  if (!player || !player.getAttribute || !player.getAttribute("src")) { toast(t("detail.no_audio_seek")); return; }
  try {
    player.currentTime = Math.max(0, +seconds || 0);
    var playing = player.play();
    if (playing && playing.catch) playing.catch(function () {});
  } catch (e) {}
}
$("#view-detail").addEventListener("click", function (event) {
  var seek = closestIn(event, "[data-seek]");
  if (seek) seekTo(seek.dataset.seek, seek.dataset.seekSource || "");
});
$("#detail-tabs").addEventListener("click", function (event) {
  var tab = closestIn(event, "[data-dtab]");
  if (tab) showDetailTab(tab.dataset.dtab);
});
$("#detail-star").addEventListener("click", function () { if (current) toggleStar(current.id); });
$("#summary-copy").addEventListener("click", function () { if (current) copyText(current.summary || ""); });

/* ---- rename (inline, Enter saves, Escape cancels) ---- */
function startRename() {
  if (!current) return;
  var input = $("#detail-title-input");
  input.value = current.title || "";
  input.hidden = false;
  $("#detail-title").hidden = true;
  input.focus();
  if (input.select) input.select();
}
function finishRename(save) {
  var input = $("#detail-title-input");
  if (!input || input.hidden) return;
  input.hidden = true;
  $("#detail-title").hidden = false;
  if (!save || !current) return;
  var value = String(input.value || "").trim().slice(0, 160);
  if (!value || value === current.title) return;
  current.title = value;
  saveCurrent().then(function () { renderDetailHead(); refreshExport(); renderList(); })
    .catch(function (error) { toast(errText(error)); });
}
$("#detail-rename").addEventListener("click", startRename);
$("#detail-title").addEventListener("dblclick", startRename);
$("#detail-title-input").addEventListener("keydown", function (event) {
  if (event.key === "Enter") { event.preventDefault(); finishRename(true); }
  else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finishRename(false); }
});
$("#detail-title-input").addEventListener("blur", function () { finishRename(true); });

/* ---- tasks pane ---- */
function renderTasks() {
  if (!current) return;
  var tasks = meetingTasks(current);
  var done = tasks.filter(function (task) { return task.done; }).length;
  $("#tasks-progress").textContent = tasks.length ? t("detail.tasks_progress", { done: done, total: tasks.length }) : "";
  var bar = $("#tasks-bar");
  if (bar) bar.style.width = (tasks.length ? Math.round(done * 100 / tasks.length) : 0) + "%";
  $("#tasks-empty").hidden = tasks.length > 0;
  $("#tasks-extract").disabled = !(current.summary || "").trim();
  $("#tasks-copy").disabled = !tasks.length;
  $("#tasks").innerHTML = tasks.map(function (task, i) {
    var meta = (task.owner ? '<span class="mm-task-owner">' + ICON.user + esc(task.owner) + "</span>" : "") +
      (task.due ? '<span class="mm-task-due">' + ICON.clock + esc(task.due) + "</span>" : "") +
      (task.from === "summary" ? '<span class="mm-task-from">' + esc(t("detail.task_from_summary")) + "</span>" : "");
    return '<li class="mm-task' + (task.done ? " done" : "") + '">' +
      '<label class="mm-task-check"><input type="checkbox" data-task-done="' + i + '"' + (task.done ? " checked" : "") +
      ' aria-label="' + esc(t("detail.task_done")) + '"><span aria-hidden="true">' + ICON.check + "</span></label>" +
      '<span class="mm-task-body"><span class="mm-task-text">' + esc(task.text) + "</span>" +
      (meta ? '<span class="mm-task-meta">' + meta + "</span>" : "") + "</span>" +
      '<button type="button" class="mm-icon-btn" data-task-del="' + i + '" title="' + esc(t("common.delete")) + '" aria-label="' +
      esc(t("common.delete")) + '">' + ICON.x + "</button></li>";
  }).join("");
}
function saveTasks(target) {
  return storeSet(meetingKey(target.id), target).then(function () {
    if (current === target) { renderTasks(); renderDetailHead(); renderTabCounts(); refreshExport(); }
    renderList();
  }).catch(function (error) { toast(errText(error)); });
}
$("#tasks").addEventListener("change", function (event) {
  var box = closestIn(event, "[data-task-done]");
  if (!box || !current) return;
  var task = meetingTasks(current)[+box.dataset.taskDone];
  if (!task) return;
  task.done = !!box.checked;
  task.done_at = task.done ? Date.now() : 0;
  saveTasks(current);
});
$("#tasks").addEventListener("click", function (event) {
  var del = closestIn(event, "[data-task-del]");
  if (!del || !current) return;
  current.tasks = meetingTasks(current).filter(function (_task, i) { return i !== +del.dataset.taskDel; });
  saveTasks(current);
});
$("#task-form").addEventListener("submit", function (event) {
  event.preventDefault();
  if (!current) return;
  var text = String($("#task-input").value || "").trim().slice(0, 300);
  if (!text) { $("#task-input").focus(); return; }
  if (meetingTasks(current).length >= MAX_TASKS) { toast(t("detail.tasks_limit")); return; }
  current.tasks = meetingTasks(current).concat([{ id: newTaskId(), text: text,
    owner: String($("#task-owner").value || "").trim().slice(0, 60), due: "", done: false, from: "manual", created_at: Date.now() }]);
  $("#task-input").value = "";
  $("#task-owner").value = "";
  saveTasks(current).then(function () { $("#task-input").focus(); });
});
$("#tasks-extract").addEventListener("click", function () {
  if (!current || !(current.summary || "").trim()) return;
  var merged = mergeTasks(current.tasks, extractTasks(current.summary));
  if (!merged.added) { toast(t("detail.tasks_none_new")); return; }
  current.tasks = merged.rows;
  saveTasks(current).then(function () { toast(t("detail.tasks_added", { n: merged.added })); });
});
$("#tasks-copy").addEventListener("click", function () { if (current) copyText(taskChecklist(meetingTasks(current))); });

/* ---- notes pane: autosaved, flushed when the meeting closes ---- */
var notesTimer = null;
var notesTarget = null;
function renderNotes() {
  var box = $("#notes");
  if (!box || !current) return;
  if (box.value !== String(current.notes || "")) box.value = String(current.notes || "");
  $("#notes-status").textContent = "";
}
function flushNotes() {
  if (!notesTimer) return;
  clearTimeout(notesTimer);
  notesTimer = null;
  var target = notesTarget;
  notesTarget = null;
  if (target) storeSet(meetingKey(target.id), target).catch(function () {});
}
$("#notes").addEventListener("input", function () {
  if (!current) return;
  current.notes = String($("#notes").value || "").slice(0, 20000);
  notesTarget = current;
  $("#notes-status").textContent = t("settings.saving");
  if (notesTimer) clearTimeout(notesTimer);
  notesTimer = setTimeout(function () {
    notesTimer = null;
    var target = notesTarget;
    notesTarget = null;
    if (!target) return;
    storeSet(meetingKey(target.id), target).then(function () {
      if (current === target) { $("#notes-status").textContent = t("detail.saved"); renderTabCounts(); refreshExport(); }
    }).catch(function (error) { if (current === target) $("#notes-status").textContent = errText(error); });
  }, 600);
});

/* ---- ask the AI about this meeting (explicit button, the selected model) ---- */
var asking = false;
function summaryLanguage() {
  var lang = settings.summary_lang === "auto" ? (en() ? "en" : "ru") : settings.summary_lang;
  return lang === "en" ? "\n\nWrite the answer in English." : "\n\nПиши ответ на русском языке.";
}
function askContext(target) {
  var segs = target.segments || [];
  var transcript = (segs.length ? transcriptText(segs) : target.transcript || "").trim();
  var parts = [];
  if ((target.summary || "").trim()) parts.push("<summary>\n" + clip(target.summary.trim(), 12000) + "\n</summary>");
  if (transcript) parts.push("<transcript>\n" + clip(transcript, 60000) + "\n</transcript>");
  if (String(target.notes || "").trim()) parts.push("<notes>\n" + clip(String(target.notes).trim(), 4000) + "\n</notes>");
  return parts.join("\n\n");
}
function renderQA() {
  if (!current) return;
  var rows = Array.isArray(current.qa) ? current.qa : [];
  $("#ask-clear").hidden = !rows.length;
  $("#ask-send").disabled = asking;
  $("#ask-list").innerHTML = rows.map(function (row, i) {
    return '<div class="mm-qa-item' + (row.pending ? " pending" : "") + '">' +
      '<div class="mm-qa-q">' + ICON.user + "<b>" + esc(row.q) + "</b></div>" +
      '<div class="mm-qa-a">' + ICON.spark + (row.pending
        ? '<span class="mm-dots" role="status" aria-label="' + esc(t("detail.ask_thinking")) + '"><i></i><i></i><i></i></span>'
        : '<div class="mm-prose">' + renderMarkdown(row.a) + "</div>") + "</div>" +
      (row.pending ? "" : '<div class="mm-qa-foot"><span class="mm-meta">' + esc(fmtClock(row.at)) + "</span><span class=\"mm-grow\"></span>" +
        '<button type="button" class="mm-btn ghost small" data-qa-copy="' + i + '">' + esc(t("detail.copy")) + "</button>" +
        '<button type="button" class="mm-btn ghost small" data-qa-note="' + i + '">' + esc(t("detail.ask_to_notes")) + "</button></div>") +
      "</div>";
  }).join("");
}
function askQuestion(question) {
  question = String(question || "").trim().slice(0, 600);
  if (!current || !question || asking) return;
  var target = current;
  var context = askContext(target);
  if (!context) { toast(t("detail.ask_no_context")); return; }
  var model = effectiveModel();
  if (!model) { toast(t("detail.no_model")); return; }
  asking = true;
  var entry = { q: question, a: "", at: Date.now(), model: model, pending: true };
  target.qa = [entry].concat(Array.isArray(target.qa) ? target.qa : []).slice(0, 20);
  $("#ask-input").value = "";
  renderQA();
  Memu.request({
    model: model,
    instructions: t("detail.ask_system") + summaryLanguage(),
    text: (target.title || t("common.untitled")) + "\n\n" + context + "\n\n<question>\n" + question + "\n</question>"
  }).then(function (answer) {
    entry.a = String((answer && answer.text) || "").trim() || t("detail.ask_empty");
    delete entry.pending;
    return storeSet(meetingKey(target.id), target);
  }).catch(function (error) {
    target.qa = (target.qa || []).filter(function (row) { return row !== entry; });
    toast(errText(error));
  }).then(function () {
    asking = false;
    if (current === target) { renderQA(); renderTabCounts(); }
  });
}
$("#ask-form").addEventListener("submit", function (event) {
  event.preventDefault();
  askQuestion($("#ask-input").value);
});
$("#ask-chips").addEventListener("click", function (event) {
  var chip = closestIn(event, "[data-ask]");
  if (chip) askQuestion(t(chip.dataset.ask));
});
$("#ask-list").addEventListener("click", function (event) {
  if (!current) return;
  var rows = Array.isArray(current.qa) ? current.qa : [];
  var copyBtn = closestIn(event, "[data-qa-copy]");
  if (copyBtn && rows[+copyBtn.dataset.qaCopy]) { copyText(rows[+copyBtn.dataset.qaCopy].a); return; }
  var noteBtn = closestIn(event, "[data-qa-note]");
  if (noteBtn && rows[+noteBtn.dataset.qaNote]) {
    var row = rows[+noteBtn.dataset.qaNote];
    current.notes = (String(current.notes || "").trim() ? String(current.notes).trim() + "\n\n" : "") +
      "Q: " + row.q + "\n" + row.a;
    current.notes = current.notes.slice(0, 20000);
    saveCurrent().then(function () { renderNotes(); renderTabCounts(); refreshExport(); toast(t("detail.ask_noted")); })
      .catch(function (error) { toast(errText(error)); });
  }
});
$("#ask-clear").addEventListener("click", function () {
  if (!current || asking) return;
  var target = current;
  confirmDialog(t("detail.ask_clear_confirm")).then(function (ok) {
    if (!ok) return;
    target.qa = [];
    storeSet(meetingKey(target.id), target).then(function () { if (current === target) { renderQA(); renderTabCounts(); } })
      .catch(function (error) { toast(errText(error)); });
  });
});
function saveCurrent() {
  return storeSet(meetingKey(current.id), current);
}

/* Transcription: Memu.transcribe uses the Live/STT preferences
 * (voice_stt_provider / voice_stt_local_model / voice_stt_api_model).
 * The module sends only the audio; it never sees keys or provider setup.
 * For a two-track meeting each track is transcribed separately and the
 * segments are merged on one timeline, tagged by source. */
function transcribeTrack(audio, source) {
  return Memu.transcribe({ audio: audio }).then(function (result) {
    var text = String((result && result.text) || "").trim();
    if (!text) return [];
    return decodeEnergies(audio).then(function (energies) {
      var timed = energies.length ? segmentEnergy(energies, 0.1) : [];
      var labelled = assignSpeakers(timed, settings.speaker_heuristic !== false);
      var rows = distributeText(labelled, text);
      if (source) rows.forEach(function (row) { row.source = source; });
      return rows;
    });
  });
}
var transcribing = false;
function transcribeCurrent() {
  if (!current || transcribing) return Promise.resolve();
  var target = current;
  var status = $("#detail-status");
  var audio = current === target ? currentAudio : "";
  var micAudio = current === target ? currentMicAudio : "";
  function setBusy(on) {
    transcribing = on;
    $("#transcribe").disabled = on;
    $("#app").classList.toggle("busy", on);
  }
  function loadAudio() {
    return storeGet(audioKey(target.id), null).then(function (value) {
      if (!value || !value.data) throw new Error(t("detail.audio_missing"));
      audio = String(value.data);
      if (target.source !== "both") return null;
      return storeGet(audioMicKey(target.id), null).then(function (mic) {
        micAudio = mic && mic.data ? String(mic.data) : "";
        return null;
      });
    });
  }
  setBusy(true);
  status.textContent = t("detail.transcribing");
  var ready = (audio && (target.source !== "both" || micAudio)) ? Promise.resolve() : loadAudio();
  return ready.then(function () {
    var jobs = [transcribeTrack(audio, target.source === "both" ? "system" : null)];
    if (target.source === "both" && micAudio) jobs.push(transcribeTrack(micAudio, "microphone"));
    return Promise.all(jobs);
  }).then(function (parts) {
    var segments = [];
    parts.forEach(function (rows) { segments = segments.concat(rows); });
    if (!segments.length) throw new Error(t("detail.transcript_error"));
    segments.sort(function (a, b) { return (a.start - b.start) || ((a.source || "") < (b.source || "") ? -1 : 1); });
    var texts = [];
    parts.forEach(function (rows) { rows.forEach(function (r) { if (r.text) texts.push(r.text); }); });
    target.transcript = texts.join("\n");
    target.segments = segments;
    target.transcribed_at = Date.now();
    target.approximate_segments = true;
    if (!target.duration_ms) {
      var end = 0;
      segments.forEach(function (seg) { end = Math.max(end, +seg.end || +seg.start || 0); });
      if (end) target.duration_ms = Math.round(end * 1000);
    }
    if (!settings.keep_audio) {
      var drop = [storeRemove(audioKey(target.id))];
      if (target.source === "both") drop.push(storeRemove(audioMicKey(target.id)));
      return Promise.all(drop).then(function () {
        target.has_audio = false;
        if (current === target) { currentAudio = ""; currentMicAudio = ""; }
      });
    }
    if (current === target) { currentAudio = audio; currentMicAudio = micAudio; }
    return null;
  }).then(function () { return storeSet(meetingKey(target.id), target); }).then(function () {
    setBusy(false);
    if (current === target) {
      status.textContent = t("detail.transcribed");
      detailTab = "transcript";
      renderDetail();
    }
    loadMeetings();
  }).catch(function (error) {
    setBusy(false);
    if (current === target) status.textContent = errText(error);
    else toast(errText(error));
  });
}
$("#transcribe").addEventListener("click", function () { transcribeCurrent(); });

function effectiveModel() {
  if (settings.model) return settings.model;
  var fallback = models.filter(function (row) { return row.default; })[0];
  return fallback ? fallback.id : "";
}
var summarizing = false;
function currentStyle() {
  var select = $("#summary-style");
  var value = select ? select.value : "";
  return SUMMARY_STYLES.indexOf(value) >= 0 ? value : settings.summary_style;
}
$("#summarize").addEventListener("click", function () {
  if (!current || summarizing) return;
  var text = (current.segments && current.segments.length ? transcriptText(current.segments) : current.transcript || "").trim();
  if (!text) { $("#detail-status").textContent = t("detail.no_transcript"); return; }
  var model = effectiveModel();
  if (!model) { $("#detail-status").textContent = t("detail.no_model"); return; }
  var style = currentStyle();
  if (settings.confirm_before_summary !== false) {
    confirmDialog(t("detail.summary_confirm")).then(function (ok) {
      if (ok) doSummarize(model, text, style);
    });
    return;
  }
  doSummarize(model, text, style);
});

function doSummarize(model, text, style) {
  var status = $("#detail-status");
  var target = current;
  if (!target) return;
  style = SUMMARY_STYLES.indexOf(style) >= 0 ? style : "standard";
  var hint = STYLE_HINT_KEYS[style] ? " " + t(STYLE_HINT_KEYS[style]) : "";
  var added = 0;
  summarizing = true;
  $("#summarize").disabled = true;
  status.textContent = t("detail.summarizing");
  Memu.request({
    model: model,
    instructions: (t("summary.prompt_system") ||
      (Memu.manifest && Memu.manifest.bridge && Memu.manifest.bridge.instructions) || "") + hint + summaryLanguage(),
    text: (target.title || t("common.untitled")) + "\n\n<transcript>\n" + clip(text, 120000) + "\n</transcript>"
  }).then(function (answer) {
    var summary = String((answer && answer.text) || "").trim();
    if (!summary) throw new Error(t("detail.summary_error"));
    target.summary = summary;
    target.summary_model = model;
    target.summary_style = style;
    target.summarized_at = Date.now();
    if (settings.auto_tasks !== false) {
      var merged = mergeTasks(target.tasks, extractTasks(summary));
      target.tasks = merged.rows;
      added = merged.added;
    }
    return storeSet(meetingKey(target.id), target);
  }).then(function () {
    if (current === target) {
      status.textContent = t("detail.saved") + (added ? " · " + t("detail.tasks_added", { n: added }) : "");
      detailTab = "summary";
      renderDetail();
    }
    loadMeetings();
  }).catch(function (error) {
    if (current === target) status.textContent = errText(error);
    else toast(errText(error));
  }).then(function () {
    summarizing = false;
    $("#summarize").disabled = false;
  });
}

/* ---- export (mirror of backend.py: build_export) ---- */
function buildExport(kind) {
  var m = current || {};
  var title = m.title || t("common.untitled");
  var created = fmtClock(m.created_at);
  var duration = m.duration_ms ? fmtTime(m.duration_ms / 1000) : "—";
  var segments = m.segments || [];
  var transcript = segments.length ? transcriptText(segments) : (m.transcript || "");
  var summary = (m.summary || "").trim();
  var note = en()
    ? "_Speaker labels are a pause/energy heuristic, not exact diarization; segment text boundaries are approximate._"
    : "_Метки говорящих — эвристика по паузам и энергии, а не точная диаризация; границы текста сегментов приблизительные._";
  var tasks = taskChecklist(meetingTasks(m));
  var notes = String(m.notes || "").trim();
  var markers = (Array.isArray(m.markers) ? m.markers : []).map(function (mk) {
    return "[" + fmtTime(mk.t) + "] " + (mk.label || "");
  });
  if (kind === "txt") {
    var out = [title, "", t("detail.export_date") + ": " + created,
      t("detail.export_source") + ": " + meetingSourceLabel(m.source),
      t("detail.export_duration") + ": " + duration, "",
      en() ? "SUMMARY" : "КОНСПЕКТ", summary || t("detail.summary_empty"), ""];
    if (tasks) out.push(en() ? "CHECKLIST" : "ЧЕК-ЛИСТ ЗАДАЧ", tasks, "");
    if (notes) out.push(en() ? "NOTES" : "ЗАМЕТКИ", notes, "");
    if (markers.length) out.push(en() ? "MARKERS" : "ОТМЕТКИ", markers.join("\n"), "");
    out.push(en() ? "TRANSCRIPT" : "ТРАНСКРИПТ", transcript, "", note, "");
    return out.join("\n");
  }
  return "# " + title + "\n\n" +
    "- " + t("detail.export_date") + ": " + created + "\n" +
    "- " + t("detail.export_source") + ": " + meetingSourceLabel(m.source) + "\n" +
    "- " + t("detail.export_duration") + ": " + duration + "\n\n" +
    (summary ? summary + "\n\n" : "## " + t("detail.summary") + "\n\n_" + t("detail.summary_empty") + "_\n\n") +
    (tasks ? "## " + t("detail.tasks") + "\n\n" + tasks + "\n\n" : "") +
    (notes ? "## " + t("detail.notes") + "\n\n" + notes + "\n\n" : "") +
    (markers.length ? "## " + t("detail.markers") + "\n\n" + markers.map(function (x) { return "- " + x; }).join("\n") + "\n\n" : "") +
    note + "\n\n## " + t("detail.transcript") + "\n\n" + transcript + "\n";
}
function refreshExport() {
  $("#export-text").value = current ? buildExport("markdown") : "";
}
function downloadExport(kind) {
  if (!current) return;
  var text = buildExport(kind);
  var ext = kind === "txt" ? "txt" : "md";
  var stamp = new Date(current.created_at || Date.now());
  var slug = String(current.title || "meeting").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "meeting";
  var name = stamp.getFullYear() + String(stamp.getMonth() + 1).padStart(2, "0") + String(stamp.getDate()).padStart(2, "0") +
    "-" + String(stamp.getHours()).padStart(2, "0") + String(stamp.getMinutes()).padStart(2, "0") + "-" + slug.slice(0, 60) + "." + ext;
  var base64 = "";
  try { base64 = btoa(unescape(encodeURIComponent(text))); } catch (e) { base64 = ""; }
  Memu.download({ url: "data:text/" + (kind === "txt" ? "plain" : "markdown") + ";base64," + base64, name: name })
    .then(function () { toast(t("detail.export_saved")); })
    .catch(function (error) { toast(String((error && error.message) || error)); });
}
$("#export-md").addEventListener("click", function () { downloadExport("markdown"); });
$("#export-txt").addEventListener("click", function () { downloadExport("txt"); });
$("#copy-export").addEventListener("click", function () {
  var value = $("#export-text").value;
  function done() { toast(t("detail.copied")); }
  function fallback() {
    $("#export-text").select();
    try { document.execCommand("copy"); } catch (e) {}
    done();
  }
  if (Memu.copy) Memu.copy(value).then(done).catch(fallback);
  else fallback();
});
$("#refresh-export").addEventListener("click", function () { refreshExport(); toast(t("detail.export_refreshed")); });
$("#delete-meeting").addEventListener("click", function () {
  if (!current) return;
  var id = current.id;
  confirmDialog(t("meetings.delete_confirm", { title: current.title || t("common.untitled") })).then(function (ok) {
    if (!ok) return;
    doDelete(id);
  });
});

function doDelete(id) {
  storeRemove(audioKey(id)).then(function () { return storeRemove(audioMicKey(id)); })
    .then(function () { return storeRemove(meetingKey(id)); })
    .then(function () { current = null; show("meetings"); loadMeetings(); })
    .catch(function (error) { toast(String((error && error.message) || error)); });
}

/* ---- wizard steps: sources → sound check → record → processing ---- */
function setStep(step) {
  recStep = step;
  $$(".mm-step").forEach(function (block) { block.hidden = String(block.dataset.step) !== String(step); });
  $$(".mm-steps li").forEach(function (li) {
    var n = Number(li.dataset.step);
    li.classList.toggle("current", n === step);
    li.classList.toggle("done", n < step);
    if (n === step) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });
  if (step !== 2) stopCheckStream();
  if (step === 2) refreshSystemCheckUI();
}
$$(".mm-steps li").forEach(function (li) {
  li.addEventListener("click", function () {
    var n = Number(li.dataset.step);
    if (n <= 2 || (n === 3 && recStep >= 2)) setStep(n);
    if (n === 4 && pending) setStep(4);
  });
});
function selectedSource() {
  var checked = document.querySelector('input[name="source"]:checked');
  return checked ? checked.value : "microphone";
}
function syncSourceUI(save) {
  var source = selectedSource();
  settings.source = source;
  if (save !== false) scheduleSaveSettings();
  $("#mic-row").hidden = source !== "microphone" && source !== "both";
  $("#check-mic-block").hidden = $("#mic-row").hidden;
  $("#check-system-block").hidden = source !== "system" && source !== "both";
  /* The system note is an informational hint for a selectable
   * system source only. When capture is unavailable the options
   * are pre-marked instead (applyCaptureAvailability) and this
   * note stays hidden: no error status is ever written here. */
  var note = $("#system-note");
  if (captureAvailable() && (source === "system" || source === "both")) {
    note.textContent = t("record.system_hint");
    note.hidden = false;
  } else {
    note.textContent = "";
    note.hidden = true;
  }
  captureStates();
  renderNativeDevices();
  refreshSystemCheckUI();
  updateRecordingTracks();
}
$$('input[name="source"]').forEach(function (radio) {
  radio.addEventListener("change", syncSourceUI);
});
$("#to-check").addEventListener("click", function () { setStep(2); });
$("#back-sources").addEventListener("click", function () { setStep(1); });
$("#to-record").addEventListener("click", function () { setStep(3); });
$("#skip-check").addEventListener("click", function () { setStep(3); });

/* ---- capture availability (honest states, no guessing) ---- */
function captureStates() {
  /* Microphone: only the host bridge can open it (Memu.record). System
   * audio: the Permissions-Policy pre-check decides "blocked" without
   * ever calling getDisplayMedia; otherwise it stays unknown until the
   * user actually tries it — the OS and the sharing picker decide. */
  var micOk = devicesFor("microphone").length > 0;
  var recOk = typeof MediaRecorder !== "undefined";
  setState($("#state-mic"), micOk && recOk, micOk && recOk ? t("record.state_ok") : t("record.state_no"));
  var gate = displayCaptureAllowed();
  if (!gate.allowed) setState($("#state-system"), "na", t("record.state_unavailable"));
  else setState($("#state-system"), null, t("record.state_unknown"));
  var bothOk = devicesFor("both").length > 0 && recOk;
  setState($("#state-both"), bothOk ? null : (gate.allowed ? false : "na"),
    bothOk ? t("record.state_unknown") : gate.allowed ? t("record.state_no") : t("record.state_unavailable"));
  setState($("#state-import"), true, t("record.state_ok"));
}
function setState(el, ok, text) {
  if (!el) return;
  el.textContent = text;
  el.className = "mm-state" + (ok === true ? " ok" : ok === false ? " bad" : ok === "na" ? " na" : "");
}

function refreshMicDevices(force) {
  var select = $("#mic-device");
  var status = $("#mic-permission");
  if (!select) return Promise.resolve([]);
  function paint() {
    var inputs = nativeDevices().filter(function (d) { return d.kind === "input"; });
    select.replaceChildren();
    select.add(new Option(t("record.mic_device_default"), ""));
    inputs.forEach(function (d) { select.add(new Option(d.name, d.id)); });
    select.disabled = !inputs.length;
    select.value = inputs.some(function (d) { return d.id === settings.mic_device; }) ? settings.mic_device : "";
    select.title = select.selectedOptions[0] ? select.selectedOptions[0].text : "";
    if (status) status.textContent = inputs.length ? "" :
      nativeStatus ? nativeReasonText(nativeStatus.reason || "no_devices") : t("record.mic_loading");
    return inputs;
  }
  if (!force || !Memu.record || !Memu.record.system) return Promise.resolve(paint());
  return refreshNativeAudio(true).then(paint);
}
$("#mic-device").addEventListener("change", function (event) {
  settings.mic_device = event.target.value || "";
  event.target.title = event.target.selectedOptions[0].text;
  scheduleSaveSettings();
});
$("#mic-refresh").addEventListener("click", function () { refreshMicDevices(true); });

/* A MediaRecorder WebM clip has no duration in its header, so Chromium
 * reports NaN/Infinity and the player shows 0:00 until the duration is
 * resolved. Seeking far ahead once makes the browser read the real
 * duration from the end of the file; seeking back to 0 leaves the clip
 * ready to play from the start. Clips with a finite duration already
 * (WAV/MP3/...) are left alone, and a player that never loads metadata
 * is untouched. */
function ensureAudioDuration(player) {
  if (!player || typeof player.addEventListener !== "function") return;
  var fixed = false;
  function maybeFix() {
    if (fixed) return;
    var dur = player.duration;
    if (typeof dur === "number" && isFinite(dur) && dur > 0) return;
    fixed = true;
    function back() {
      player.removeEventListener("durationchange", back);
      try { player.currentTime = 0; } catch (e) {}
    }
    player.addEventListener("durationchange", back);
    try { player.currentTime = 1e7; } catch (e) {}
    setTimeout(back, 1500);
  }
  player.addEventListener("loadedmetadata", maybeFix);
  if (player.readyState >= 1) maybeFix();
}

/* ---- separate native sound checks for the microphone and system audio ---- */
function stopCheckStream(saveMicPreview) {
  var sequence = ++checkSequence;
  clearTimeout(micCheckTimer);
  var save = saveMicPreview === true && checkSource === "microphone" && hostSystemCheck;
  checkSource = "";
  $("#level-mic").style.width = "0%";
  $("#level-mic").parentNode.setAttribute("aria-valuenow", "0");
  clearNativeLevels();
  if (hostSystemCheck) {
    hostSystemCheck = false;
    if (!save) Memu.record.system("cancel").catch(function () {});
    var levelBar = $("#level-system");
    levelBar.style.width = "0%";
    /* The meter's parent is the role=meter wrapper in the real page;
     * guard it so a missing parent cannot break the stop flow. */
    if (levelBar.parentNode) levelBar.parentNode.setAttribute("aria-valuenow", "0");
  }
  var btn = $("#check-system-stop");
  if (btn) btn.hidden = true;
  var start = $("#check-system");
  if (start) start.hidden = false;
  $("#check-mic-stop").hidden = true;
  $("#check-mic").hidden = false;
  if (!save) return;
  $("#check-mic").disabled = true;
  $("#to-record").disabled = true;
  return Memu.record.system("stop").then(function (result) {
    if (sequence !== checkSequence) return;
    var player = $("#mic-test-player");
    if (!result || !result.audio || !result.audio.size) {
      setCheckStatus(t("record.mic_preview_empty"), true);
      return;
    }
    clearMicPreview();
    micPreviewUrl = URL.createObjectURL(result.audio);
    player.src = micPreviewUrl;
    player.hidden = false;
    ensureAudioDuration(player);
    setCheckStatus(t("record.mic_preview_ready"), true);
  }).catch(function (error) {
    if (sequence === checkSequence) setCheckStatus(String(error.message || error), true);
  }).finally(function () {
    $("#check-mic").disabled = false;
    $("#to-record").disabled = false;
  });
}
function clearMicPreview() {
  if (!micPreviewUrl) return;
  URL.revokeObjectURL(micPreviewUrl);
  micPreviewUrl = "";
  var player = $("#mic-test-player");
  player.removeAttribute("src");
  player.load();
  player.hidden = true;
}
function setCheckStatus(text, micOnly) {
  /* One check, one status, in exactly one place: a microphone check
   * writes only under the microphone block, a system check only under
   * the system block. Writing the same text into the shared footer as
   * well made the fallback paragraph appear twice on step 2 — three
   * times together with the static hint above it — while the level
   * meter stayed empty and the test player showed 0:00. Every caller
   * passes true (microphone) or false (system); there is no shared
   * status element any more (see index.html). textContent assignment
   * replaces, never appends, so re-renders cannot accumulate copies. */
  var target = micOnly === true ? $("#mic-check-status")
    : micOnly === false ? $("#system-check-status") : null;
  if (target) target.textContent = text;
}
/* The system check is only offered when capture is really available:
 * the button is disabled and the reason is shown otherwise, so the
 * user is never invited to open a picker that cannot appear. */
function refreshSystemCheckUI() {
  var gate = { allowed: devicesFor("system").length > 0 };
  var btn = $("#check-system");
  var hint = $("#check-system-hint");
  if (!gate.allowed) {
    if (btn) btn.disabled = true;
    if (hint) hint.textContent = nativeStatus ? nativeReasonText(nativeStatus.reason || "no_devices") : t("record.mic_loading");
    setState($("#state-system"), "na", t("record.state_unavailable"));
  } else {
    if (btn) btn.disabled = false;
    if (hint) hint.textContent = effectiveCaptureMode() === "native"
      ? t("record.native_check_hint") : t("record.check_system_hint");
  }
  var levels = $("#native-levels");
  if (levels) {
    var showLevels = gate.allowed && devicesFor("system").length > 0;
    levels.hidden = !showLevels;
    if (showLevels) {
      emptyEl(levels);
      devicesFor("system").forEach(function (device) {
        var row = document.createElement("div");
        row.className = "mm-native-level-row";
        row.dataset.device = device.id;
        var name = document.createElement("span");
        name.className = "mm-native-level-name";
        name.textContent = nativeDeviceName(device.id);
        row.appendChild(name);
        var meter = document.createElement("span");
        meter.className = "mm-native-level";
        meter.appendChild(document.createElement("i"));
        row.appendChild(meter);
        levels.appendChild(row);
      });
      updateNativeLevels(nativeLevels);
    }
  }
  updateRecordingTracks();
}
function checkNativeAudio(source) {
  if (recording || (hostSystemCheck && checkSource === source)) return;
  if (hostSystemCheck) {
    var wasMic = checkSource === "microphone";
    stopCheckStream();
    setCheckStatus("", wasMic);
  }
  var micOnly = source === "microphone";
  var start = $(micOnly ? "#check-mic" : "#check-system");
  var stop = $(micOnly ? "#check-mic-stop" : "#check-system-stop");
  var opts = systemCaptureOpts(source);
  if (micOnly) {
    clearMicPreview();
    opts.preview = true;
    if (!opts.devices.length && settings.mic_device) opts.devices.push(settings.mic_device);
  }
  if ((!micOnly && !opts.devices.length) || !Memu.record || !Memu.record.system) {
    setCheckStatus(nativeReasonText(nativeStatus && nativeStatus.reason || "no_devices"), micOnly);
    return;
  }
  var sequence = ++checkSequence;
  hostSystemCheck = true;
  checkSource = source;
  start.disabled = true;
  setCheckStatus(t("record.native_checking"), micOnly);
  return Memu.record.system("check", opts).then(function (result) {
    if (sequence !== checkSequence) return;
    if (!result || result.reason) {
      var detail = result && result.detail;
      throw new Error(systemUnavailableMessage(result && result.reason) + (detail ? " " + detail : ""));
    }
    setCheckStatus(t(micOnly ? "record.mic_check_active" : "record.check_ok"), micOnly);
    start.hidden = true;
    stop.hidden = false;
    if (micOnly) micCheckTimer = setTimeout(function () { stopCheckStream(true); }, 8000);
  }).catch(function (error) {
    if (sequence !== checkSequence) return;
    stopCheckStream();
    setCheckStatus(String(error.message || error), micOnly);
  }).finally(function () { start.disabled = false; });
}
$("#check-system").addEventListener("click", function () { checkNativeAudio("system"); });
$("#check-mic").addEventListener("click", function () { checkNativeAudio("microphone"); });
$("#check-system-stop").addEventListener("click", function () { stopCheckStream(); setCheckStatus("", false); });
$("#check-mic-stop").addEventListener("click", function () { stopCheckStream(true); });
/* ---- recording ---- */
function updateRecordingTracks() {
  /* What is being recorded right now — or, when idle, what the
   * selected source will record. Never claims a track that is not
   * there: a missing system track names its reason, and a
   * microphone-only recording says so while it is happening. */
  var micState = $("#rec-track-mic-state");
  var sysState = $("#rec-track-system-state");
  var meter = $("#level-live-wrap");
  var label = $("#level-live-label");
  if (!micState || !sysState) return;
  var source = recording ? (recRequestedSource || recording.source) : selectedSource();
  var activeMic = !!recording && (recording.source === "microphone" || recording.micActive || recording.micOnlyFallback);
  var activeSys = !!recording && recording.source !== "microphone" && recording.hostSystem;
  if (recording) {
    micState.textContent = activeMic ? t("record.rec_track_mic_active") : t("record.rec_track_mic_off");
    if (activeSys && recording.captureMode === "native") {
      var names = (recording.captureDevices || []).map(nativeDeviceName).filter(Boolean);
      sysState.textContent = names.length ? t("record.native_active", { devices: names.join(", ") })
        : t("record.rec_track_system_active");
    } else if (activeSys) sysState.textContent = t("record.rec_track_system_active");
    else if (!captureAvailable()) sysState.textContent = t("record.state_unavailable");
    else if (recSystemReason || source === "system" || source === "both") {
      sysState.textContent = t("record.rec_track_system_unavailable") + " " + systemUnavailableMessage(recSystemReason);
    } else sysState.textContent = t("record.rec_track_system_off");
  } else {
    micState.textContent = source === "system" ? t("record.rec_track_mic_off") : t("record.rec_track_mic_planned");
    if (source === "system" || source === "both") {
      if (!captureAvailable()) sysState.textContent = t("record.state_unavailable");
      else if (effectiveCaptureMode() === "native") {
        var planned = devicesFor("system").map(function (d) { return d.name; });
        sysState.textContent = planned.length ? t("record.native_active", { devices: planned.join(", ") })
          : t("record.native_planned_default");
      } else sysState.textContent = t("record.rec_track_system_planned");
    } else sysState.textContent = t("record.rec_track_system_off");
  }
  if (meter) meter.hidden = !(recording && activeSys);
  if (label) label.textContent = (recording && activeSys) ? t("record.level_system") : "";
}
function recElapsed() { return recording ? Date.now() - recording.started : 0; }
function tickTimer() {
  var text = fmtTime(recElapsed() / 1000);
  $("#rec-time").textContent = text;
  var pill = $("#bar-rec-time");
  if (pill) pill.textContent = text;
}
/* ---- markers: "something important just happened" during a recording ---- */
var lastMarkers = [];
function renderRecMarkers(rows) {
  var box = $("#rec-markers");
  if (!box) return;
  rows = rows || [];
  box.hidden = !rows.length;
  box.innerHTML = rows.map(function (m) {
    return '<span class="mm-marker">' + ICON.flag + "<b>" + esc(fmtTime(m.t)) + "</b>" + esc(m.label || "") + "</span>";
  }).join("");
}
$("#rec-mark").addEventListener("click", function () {
  if (!recording) return;
  recording.markers = recording.markers || [];
  if (recording.markers.length >= 100) { toast(t("record.marks_limit")); return; }
  var seconds = Math.round(recElapsed() / 100) / 10;
  recording.markers.push({ t: seconds, label: t("record.marker_n", { n: recording.markers.length + 1 }) });
  renderRecMarkers(recording.markers);
  toast(t("record.marked", { time: fmtTime(seconds) }));
});
$("#bar-rec").addEventListener("click", function () { show("record"); setStep(3); });
function setRecordingUI(active) {
  document.getElementById("app").classList.toggle("recording", !!active);
  $("#rec-dot").classList.toggle("on", !!active);
  var toggleLabel = $("#rec-toggle-label");
  var toggleText = t(active ? "record.stop" : "record.start");
  if (toggleLabel) toggleLabel.textContent = toggleText;
  else $("#rec-toggle").textContent = toggleText;
  $("#rec-cancel").hidden = !active;
  $("#rec-mark").hidden = !active;
  $("#bar-rec").hidden = !active;
  if (active) renderRecMarkers(recording && recording.markers);
  var live = $("#rec-live");
  if (live) {
    if (!active) live.textContent = "";
    else if (recording) {
      /* A policy-blocked fallback records exactly like a plain
       * microphone recording: no fallback wording at all. A
       * fallback after a declined picker keeps its wording. */
      live.textContent = (recording.micOnlyFallback && captureAvailable()) ? t("record.recording_mic_only")
        : recording.source === "both" ? t("record.recording_both")
        : recording.source === "system" ? t("record.recording_system")
        : t("record.recording_mic");
    }
  }
  updateRecordingTracks();
}
function startRecording() {
  var source = selectedSource();
  if (source === "import") { $("#import-file").click(); return Promise.resolve(); }
  var title = $("#rec-title").value.trim();
  var opts = systemCaptureOpts(source);
  if (!opts.devices.length || !Memu.record || !Memu.record.system) {
    $("#rec-status").textContent = nativeReasonText(nativeStatus && nativeStatus.reason || "no_devices");
    return Promise.resolve();
  }
  recSystemReason = "";
  recRequestedSource = source;
  $("#rec-status").textContent = t("record.native_checking");
  return Memu.record.system("start", opts).then(function (result) {
    if (!result || result.reason) {
      $("#rec-status").textContent = systemUnavailableMessage(result && result.reason) + (result && result.detail ? " " + result.detail : "");
      return;
    }
    liveCaptions = [];
    $("#rec-status").textContent = "";
    recording = { source: source, hostSystem: true, title: title, started: result.started,
      timer: setInterval(tickTimer, 500), captureMode: "native", captureDevices: result.devices || opts.devices,
      micActive: source !== "system", nativeMic: source !== "system" };
    setRecordingUI(true);
    tickTimer();
  });
}
function transcribeChunk(blob) {
  /* Live captions, chunk by chunk: the only streaming-like path the
   * project offers, because Memu.transcribe itself is whole-file. A chunk
   * that fails to transcribe is skipped — no invented captions. */
  var session = recording;
  if (!session) return;
  var start = Math.max(0, (Date.now() - session.started) / 1000 - 8);
  blobToDataUrl(blob).then(function (dataUrl) {
    if (recording !== session) return null;
    return Memu.transcribe({ audio: dataUrl });
  }).then(function (result) {
    var text = String((result && result.text) || "").trim();
    if (!text || recording !== session) return;
    liveCaptions.push({ start: start, speaker: "speaker-1", source: "system", text: text });
    liveCaptions.sort(function (a, b) { return a.start - b.start; });
    renderCaptions();
  }).catch(function () { /* a failed chunk stays silent */ });
}
function stopSystemRecorder(rec) {
  return Memu.record.system("stop").then(function (result) {
    if (!result || !result.audio || !result.audio.size) return null;
    return blobToDataUrl(result.audio).then(function (dataUrl) {
      return { source: rec.source, audio: dataUrl, mime: cleanMime(result.audio.type), ms: result.ms };
    });
  });
}
function stopRecording() {
  if (!recording) return Promise.resolve();
  var rec = recording;
  lastMarkers = (rec.markers || []).slice();
  recording = null;
  clearInterval(rec.timer);
  if (liveMeterStop) { liveMeterStop(); liveMeterStop = null; }
  setRecordingUI(false);
  var sysJob = stopSystemRecorder(rec).catch(function (error) {
    $("#rec-status").textContent = String(error.message || error);
    return null;
  });
  return sysJob.then(function (track) {
    var tracks = track ? [track] : [];
    if (!tracks.length) { $("#rec-status").textContent = t("record.error_empty"); return; }
    var source = tracks.length > 1 ? "both" : tracks[0].source;
    showPendingTracks(tracks, rec.title, source, recRequestedSource || rec.source, recSystemReason);
  });
}
function showPendingTracks(tracks, title, source, requestedSource, systemReason) {
  try {
    tracks.forEach(function (track) { checkAudio(track.audio, track.mime); });
  } catch (error) {
    $("#rec-status").textContent = String(error.message || error);
    return;
  }
  var ms = Math.max.apply(null, tracks.map(function (tr) { return tr.ms || 0; }));
  pending = { tracks: tracks, title: title, ms: ms, source: source, markers: lastMarkers };
  lastMarkers = [];
  renderRecMarkers([]);
  var markNote = $("#pending-markers");
  if (markNote) {
    markNote.hidden = !pending.markers.length;
    markNote.textContent = pending.markers.length ? t("record.pending_markers", { n: pending.markers.length }) : "";
  }
  var auto = $("#pending-auto");
  if (auto) auto.checked = !!settings.auto_transcribe;
  $("#pending-card").hidden = false;
  var note = $("#pending-tracks-note");
  if (note) {
    if (requestedSource === "both" && tracks.length === 1 && tracks[0].source === "microphone") {
      if (captureAvailable()) {
        note.textContent = t("record.pending_mic_only") + " " + systemUnavailableMessage(systemReason || "");
      } else {
        note.textContent = t("record.pending_mic_only_short");
      }
      note.hidden = false;
    } else {
      note.textContent = "";
      note.hidden = true;
    }
  }
  $("#pending-meta").textContent = fmtTime(ms / 1000) + " · " + meetingSourceLabel(source);
  $("#pending-tracks").innerHTML = tracks.map(function (track, i) {
    return '<div class="mm-track"><span class="mm-src-badge">' + esc(sourceLabel(track.source)) + "</span>" +
      '<audio controls preload="metadata" data-pending-track="' + i + '" src="' + esc(track.audio) + '"></audio></div>';
  }).join("");
  $$("#pending-tracks audio").forEach(ensureAudioDuration);
  $("#rec-status").textContent = "";
  setStep(4);
}
function showPending(audio, mime, ms, source, title) {
  showPendingTracks([{ source: source, audio: audio, mime: mime, ms: ms }], title, source);
}
$("#rec-toggle").addEventListener("click", function (event) {
  var button = event.currentTarget;
  if (button.disabled) return;
  button.disabled = true;
  Promise.resolve(recording ? stopRecording() : startRecording()).catch(function (error) {
    $("#rec-status").textContent = String(error.message || error);
  }).finally(function () { button.disabled = false; });
});
$("#rec-cancel").addEventListener("click", function () {
  if (!recording) return;
  var rec = recording;
  recording = null;
  clearInterval(rec.timer);
  if (liveMeterStop) { liveMeterStop(); liveMeterStop = null; }
  setRecordingUI(false);
  if (rec.hostSystem) Memu.record.system("cancel").catch(function () {});
    liveCaptions = [];
  lastMarkers = [];
  renderRecMarkers([]);
  $("#rec-time").textContent = "0:00";
});
$("#pending-discard").addEventListener("click", function () {
  pending = null;
  $("#pending-card").hidden = true;
  $("#pending-tracks").innerHTML = "";
  $("#rec-time").textContent = "0:00";
  setStep(3);
});
$("#pending-save").addEventListener("click", function () {
  if (!pending) return;
  var tracks = pending.tracks;
  var source = pending.source;
  var primary = tracks.filter(function (tr) { return tr.source === "system"; })[0] || tracks[0];
  var micTrack = tracks.filter(function (tr) { return tr.source === "microphone"; })[0];
  var record = {
    id: "meet-" + Math.random().toString(36).slice(2, 12) + Date.now().toString(36),
    title: pending.title || (en() ? "Meeting" : "Встреча") + " " + fmtClock(Date.now()),
    source: source, created_at: Date.now(), duration_ms: Math.round(pending.ms),
    mime: primary.mime, status: "recorded", has_audio: true,
    audio_bytes: dataUrlBytes(primary.audio),
    tracks: tracks.map(function (tr) { return { source: tr.source, mime: tr.mime, ms: Math.round(tr.ms || 0), bytes: dataUrlBytes(tr.audio) }; }),
    transcript: "", segments: [], speakers: [], summary: "", summary_model: "",
    transcribed_at: 0, approximate_segments: true, speaker_names: {},
    markers: (pending.markers || []).slice(0, 100), tasks: [], notes: "", qa: [], starred: false
  };
  var autoBox = $("#pending-auto");
  var autoTranscribe = !!(autoBox && autoBox.checked);
  $("#rec-status").textContent = t("record.saving");
  storeSet(meetingKey(record.id), record).then(function () {
    return storeSet(audioKey(record.id), { mime: primary.mime, data: primary.audio });
  }).then(function () {
    if (micTrack && source === "both") {
      return storeSet(audioMicKey(record.id), { mime: micTrack.mime, data: micTrack.audio });
    }
    return null;
  }).then(function () {
    pending = null;
    $("#pending-card").hidden = true;
    $("#pending-tracks").innerHTML = "";
    $("#rec-status").textContent = t("record.saved");
    $("#rec-title").value = "";
    $("#rec-time").textContent = "0:00";
    loadMeetings().then(function () {
      openMeeting(record.id);
      if (autoTranscribe && current && current.id === record.id) transcribeCurrent();
    });
  }).catch(function (error) {
    $("#rec-status").textContent = String((error && error.message) || error);
  });
});
$("#import-file").addEventListener("change", function (event) {
  var file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;
  if (file.size > MAX_AUDIO_BYTES) { $("#rec-status").textContent = t("record.too_big"); return; }
  var reader = new FileReader();
  reader.onload = function () {
    var dataUrl = String(reader.result || "");
    var mime = cleanMime(file.type) || "audio/mpeg";
    try { checkAudio(dataUrl, mime); } catch (error) {
      $("#rec-status").textContent = String(error.message || error);
      return;
    }
    var title = file.name.replace(/\.[^.]+$/, "");
    showPending(dataUrl, mime, 0, "import", title);
    /* Duration is unknown until the audio is decoded; the detail view
     * shows it after the first transcription pass fills duration_ms. */
    decodeEnergies(dataUrl).then(function (energies) {
      if (pending) {
        pending.ms = energies.length * 100;
        pending.tracks.forEach(function (tr) { tr.ms = pending.ms; });
        $("#pending-meta").textContent = fmtTime(pending.ms / 1000) + " · " + meetingSourceLabel("import");
      }
    });
  };
  reader.onerror = function () { $("#rec-status").textContent = t("record.error"); };
  reader.readAsDataURL(file);
});

/* ---- captions ---- */
function captionRows() {
  if (recording && (recording.source === "system" || recording.source === "both") && liveCaptions.length) {
    return liveCaptions.map(function (cap, i) {
      return { start: cap.start, speaker: cap.speaker, source: cap.source, text: cap.text, current: i === liveCaptions.length - 1 };
    });
  }
  if (current && current.segments && current.segments.length) {
    return current.segments.map(function (seg) {
      return { start: seg.start, speaker: seg.speaker, source: seg.source, text: seg.text, current: false };
    });
  }
  return [];
}
function renderCaptions() {
  var box = $("#captions");
  var large = settings.captions_large || $("#captions-large").checked;
  box.classList.toggle("large", !!large);
  var rows = captionRows();
  var live = !!(recording && liveCaptions.length);
  $("#captions-empty").hidden = rows.length > 0;
  var meta = $("#captions-meta");
  if (meta) {
    meta.textContent = !rows.length ? "" : live ? t("captions.meta_live", { n: rows.length })
      : t("captions.meta_meeting", { title: (current && current.title) || t("common.untitled"), n: rows.length });
  }
  var copyBtn = $("#captions-copy");
  if (copyBtn) copyBtn.disabled = !rows.length;
  box.innerHTML = rows.map(function (row) {
    return '<div class="mm-cap' + (row.current ? " current" : "") + '"><span class="mm-cap-who">' +
      esc(speakerLabel(row.speaker, row.source)) + " · " + esc(fmtTime(row.start)) + "</span>" + esc(row.text) + "</div>";
  }).join("");
  var last = box.lastElementChild;
  if (last) last.scrollIntoView({ block: "end" });
}
$("#captions-copy").addEventListener("click", function () {
  copyText(captionRows().map(function (row) {
    return "[" + fmtTime(row.start) + "] " + speakerLabel(row.speaker, row.source) + ": " + row.text;
  }).join("\n"));
});
$("#captions-large").addEventListener("change", function (event) {
  settings.captions_large = event.target.checked;
  scheduleSaveSettings();
  renderCaptions();
});

/* ---- settings: the shared model selector + autosave on every control ---- */
function loadModels() {
  var select = $("#set-model");
  select.innerHTML = '<option value="">' + esc(t("settings.model_loading")) + "</option>";
  Memu.models().then(function (rows) {
    models = (rows || []).filter(function (row) {
      return row && (!row.kind || row.kind === "chat") && !row.image && !row.video && !row.audio;
    });
    var currentValue = settings.model;
    var known = models.some(function (row) { return row.id === currentValue; });
    if (currentValue && !known) {
      select.innerHTML = '<option value="">' + esc(t("settings.model_fallback")) + "</option>";
    } else {
      select.innerHTML = '<option value="">' + esc(t("settings.model_default")) + "</option>";
    }
    /* Provider names come from the catalogue rows themselves: the
     * selector resolves provider_label / account / id prefix and never
     * prints a raw "custom" provider. Grouping uses <optgroup> labels,
     * which the selector shows as the provider sections. */
    var groups = {};
    models.forEach(function (row) {
      var name = window.MemuModelSelector ? window.MemuModelSelector.providerName(row) : (row.provider_label || row.provider || "");
      if (!groups[name]) groups[name] = [];
      groups[name].push(row);
    });
    Object.keys(groups).sort().forEach(function (name) {
      var group = document.createElement("optgroup");
      group.label = name;
      groups[name].forEach(function (row) {
        var option = document.createElement("option");
        option.value = row.id;
        option.textContent = (row.name || row.id) + (row.default ? " · " + t("settings.model_is_default") : "");
        group.appendChild(option);
      });
      select.appendChild(group);
    });
    select.value = known ? currentValue : "";
    mountModelSelector();
  }).catch(function () {
    select.innerHTML = '<option value="">' + esc(t("settings.model_none")) + "</option>";
  });
}
var modelSelectorHandle = null;
function mountModelSelector() {
  var select = $("#set-model");
  if (!window.MemuModelSelector || !window.MemuModelSelector.mountSelect) return;
  if (modelSelectorHandle) { modelSelectorHandle.sync(); return; }
  try {
    modelSelectorHandle = window.MemuModelSelector.mountSelect(select, {
      preset: "dropdown", kind: "chat", allowEmpty: true,
      emptyLabel: t("settings.model_default"),
      onChange: function (value) {
        settings.model = value || "";
        scheduleSaveSettings();
      }
    });
  } catch (e) { modelSelectorHandle = null; }
}
function renderSettings() {
  $("#set-model").value = settings.model || "";
  $("#set-lang").value = settings.summary_lang || "auto";
  $("#set-speakers").checked = settings.speaker_heuristic !== false;
  $("#set-keep-audio").checked = settings.keep_audio !== false;
  $("#set-large").checked = !!settings.captions_large;
  $("#set-confirm").checked = settings.confirm_before_summary !== false;
  $("#set-style").value = settings.summary_style || "standard";
  $("#set-auto-transcribe").checked = !!settings.auto_transcribe;
  $("#set-auto-tasks").checked = settings.auto_tasks !== false;
  $("#captions-large").checked = !!settings.captions_large;
  if (!current) { var styleSelect = $("#summary-style"); if (styleSelect) styleSelect.value = settings.summary_style || "standard"; }
  var mic = $("#mic-device");
  if (mic && settings.mic_device) mic.value = settings.mic_device;
  var sourceRadio = document.querySelector('input[name="source"][value="' + (settings.source || "microphone") + '"]');
  if (sourceRadio) sourceRadio.checked = true;
  if (modelSelectorHandle) modelSelectorHandle.sync();
  /* STT is the Live setup: the module cannot read preferences from the
   * sandbox, so it names the keys and points at Settings → Voice instead
   * of showing a model it did not actually read. */
  $("#stt-info").innerHTML = "<b>" + esc(t("settings.stt_source_live")) + "</b>" +
    '<span class="mm-stt-path">' + esc(t("settings.stt_path")) + "</span>" +
    '<details class="mm-stt-tech"><summary>' + esc(t("settings.stt_tech")) + "</summary>" +
    esc(t("settings.stt_keys")) + ' <code>voice_stt_provider</code>, <code>voice_stt_local_model</code>, ' +
    "<code>voice_stt_api_model</code>, <code>voice_language</code></details>";
  syncSourceUI(false);
    renderDiagnostics();
}
function readSettingsFromControls() {
  settings.model = $("#set-model").value || "";
  settings.summary_lang = $("#set-lang").value || "auto";
  settings.speaker_heuristic = $("#set-speakers").checked;
  settings.keep_audio = $("#set-keep-audio").checked;
  settings.captions_large = $("#set-large").checked;
  settings.confirm_before_summary = $("#set-confirm").checked;
  settings.summary_style = SUMMARY_STYLES.indexOf($("#set-style").value) >= 0 ? $("#set-style").value : "standard";
  settings.auto_transcribe = $("#set-auto-transcribe").checked;
  settings.auto_tasks = $("#set-auto-tasks").checked;
}
$("#set-model").addEventListener("change", function () { settings.model = $("#set-model").value || ""; scheduleSaveSettings(); });
$("#set-lang").addEventListener("change", function () { settings.summary_lang = $("#set-lang").value || "auto"; scheduleSaveSettings(); });
$("#set-speakers").addEventListener("change", function () { settings.speaker_heuristic = $("#set-speakers").checked; scheduleSaveSettings(); });
$("#set-keep-audio").addEventListener("change", function () { settings.keep_audio = $("#set-keep-audio").checked; scheduleSaveSettings(); });
$("#set-large").addEventListener("change", function () { settings.captions_large = $("#set-large").checked; scheduleSaveSettings(); renderCaptions(); });
$("#set-confirm").addEventListener("change", function () { settings.confirm_before_summary = $("#set-confirm").checked; scheduleSaveSettings(); });
$("#set-style").addEventListener("change", function () {
  settings.summary_style = SUMMARY_STYLES.indexOf($("#set-style").value) >= 0 ? $("#set-style").value : "standard";
  scheduleSaveSettings();
});
$("#set-auto-transcribe").addEventListener("change", function () { settings.auto_transcribe = $("#set-auto-transcribe").checked; scheduleSaveSettings(); });
$("#set-auto-tasks").addEventListener("change", function () { settings.auto_tasks = $("#set-auto-tasks").checked; scheduleSaveSettings(); });
function fmtBytes(n) {
  n = +n || 0;
  if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}
function renderStorage() {
  var info = $("#storage-info");
  if (!info || !Memu.store || typeof Memu.store.usage !== "function") return;
  Memu.store.usage().then(function (usage) {
    var bytes = +(usage && usage.bytes) || 0;
    var limit = +(usage && usage.limit) || 0;
    info.textContent = t("settings.storage_info", { used: fmtBytes(bytes), limit: limit ? fmtBytes(limit) : "—", n: meetings.length });
    var bar = $("#storage-bar");
    if (bar) bar.style.width = (limit ? Math.min(100, Math.max(1, bytes * 100 / limit)) : 0).toFixed(1) + "%";
  }).catch(function () { info.textContent = ""; });
}
$("#set-save").addEventListener("click", function () {
  readSettingsFromControls();
  flushSaveSettings();
});

/* ---- commands from the chat / Quick Access (run.open) ---- */
if (Memu.on) Memu.on("navigate", function (data) {
  var d = data || {};
  var command = d.command || d.route || d.view || "";
  if (command === "record") {
    show("record");
    setStep(1);
  } else if (command === "import") {
    show("record");
    setStep(1);
    document.querySelector('input[name="source"][value="import"]').checked = true;
    syncSourceUI();
  } else if (command === "captions") {
    if (!current && meetings.length) current = meetings[0];
    show("captions");
  } else if (command === "settings") {
    show("settings");
  } else if (command === "meetings") {
    show("meetings");
  } else {
    show("meetings");
  }
});
if (Memu.onLanguage) Memu.onLanguage(function () {
  renderList();
  if (current) renderDetail();
  renderSettings();
  renderCaptions();
});

captureAvailable(); /* probe the capture policy BEFORE any UI is painted */
captureStates();
renderSettings();
loadModels();
refreshMicDevices(false);
loadMeetings();
show("meetings");
setStep(1);
if (typeof Memu.ready === "function") Memu.ready();

function refreshNativeAudio(force) {
  if (!Memu.record || !Memu.record.system) return Promise.resolve();
  return Memu.record.system("probe", { force: !!force }).then(function (result) {
    if (!result) return;
    /* The probe runs even when the web capture works: on Windows the
     * native helper is the better default, and the device list needs
     * the probe whether or not it is chosen yet. */
    if (result.native) {
      nativeStatus = result.native;
      if (result.native.last_error) lastNativeError = result.native.last_error;
    }
    renderDiagnostics();
    captureGate = null;
    captureStates();
    refreshMicDevices(false);
    renderSettings();
    renderNativeDevices();
    refreshSystemCheckUI();
  }).catch(function () {});
}
refreshNativeAudio(false);
if (Memu.on) Memu.on("system-audio", function (data) {
  if (data.ended) {
    if (data.detail) {
      lastNativeError = { at: Date.now(), code: data.reason || "device_lost", detail: data.detail };
      renderDiagnostics();
    }
    if (diagTest) { finishNativeDiagnostics(); return; }
    if (recording && recording.hostSystem) stopRecording();
    else if (hostSystemCheck) { var mic = checkSource === "microphone"; stopCheckStream(); setCheckStatus(t("record.native_reason_device_lost"), mic); }
    return;
  }
  if (data.reconnecting) {
    setCheckStatus(t("record.native_reconnecting"), hostSystemCheck && checkSource === "microphone");
    return;
  }
  if (data.deviceLost) {
    nativeLost[data.deviceLost] = true;
    markNativeDeviceLost(data.deviceLost);
    setCheckStatus(t("record.native_device_lost", { name: nativeDeviceName(data.deviceLost) }), hostSystemCheck && checkSource === "microphone");
    return;
  }
  if (data.levels) { updateNativeLevels(data.levels); diagTestObserve(data.levels); }
  if (data.clip && recording && recording.hostSystem) transcribeChunk(data.clip);
  if (typeof data.peak === "number") {
    var bar = hostSystemCheck ? $(checkSource === "microphone" ? "#level-mic" : "#level-system") : recording && recording.hostSystem ? $("#level-live") : null;
    if (!bar) return;
    bar.style.width = Math.round(Math.min(1, data.peak * 1.6) * 100) + "%";
    bar.parentNode.setAttribute("aria-valuenow", String(Math.round(data.peak * 100)));
  }
});

window.addEventListener("pagehide", function () {
  flushNotes();
  stopCheckStream();
  clearMicPreview();
  if (!recording) return;
  var rec = recording;
  recording = null;
  clearInterval(rec.timer);
  if (rec.hostSystem) Memu.record.system("cancel").catch(function () {});
  });
