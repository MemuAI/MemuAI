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
 * Recording sources: microphone (host bridge), system audio
 * (getDisplayMedia, guarded — see displayCaptureAllowed()), both at
 * once (one meeting, one timeline, two tracks; the transcript marks
 * microphone speech separately from system speech), or an imported file.
 * getDisplayMedia is NEVER called when display-capture is disallowed:
 * the call itself is what the browser reports as a console error, so
 * the pre-check is what keeps the console clean. The Permissions-Policy
 * API is absent in the Chromium/WebView2 context the module window
 * runs in, so an opaque origin (window.origin === "null": the sandboxed
 * iframe without allow-same-origin, i.e. the real MemuAI module
 * window) is treated as disallowed too — verified: a call from an
 * opaque origin is rejected with NotAllowedError and logged as a
 * policy violation. The microphone list asks getUserMedia for
 * permission BEFORE enumerateDevices (see refreshMicDevices), so the
 * browser can return real device names; the sandboxed page itself
 * cannot open the microphone (getUserMedia rejects with SecurityError
 * there), so the level check falls back to a bridge test clip and the
 * device list says so honestly instead of inventing names.
 *
 * Capture unavailability is NEVER shown as an alarming status. The
 * policy is probed BEFORE the source choice is painted
 * (captureAvailable / applyCaptureAvailability): system/both options
 * are disabled with one short neutral label ("unavailable in this
 * window"), the microphone is selected by default, and microphone
 * recording and import run without a single mention of capture.
 * When the probe cannot see the policy (no Permissions-Policy API
 * and a non-opaque origin), a getDisplayMedia rejection is
 * classified quietly (classifyCaptureError) and remembered for the
 * rest of the page load (markCaptureBlocked) — still with no status
 * text. Only a picker the user actually declined is reported, once,
 * in its own block.
 *
 * Settings autosave: every settings control schedules a debounced
 * Memu.save; the indicator next to the form shows saving/saved/error.
 * The model picker is the shared MemuModelSelector (dropdown preset):
 * provider names come from the catalogue, never "custom".
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
var SETTING_DEFAULTS = {
  model: "", summary_lang: "auto", speaker_heuristic: true,
  keep_audio: true, captions_large: false, confirm_before_summary: true,
  mic_device: "", source: "microphone"
};
var settings = Object.assign({}, SETTING_DEFAULTS, (Memu.saved && Memu.saved.settings) || {});
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
var checkStream = null;   /* system-audio stream held only during the sound check */
var checkMeterStop = null;
var liveMeterStop = null;
var micPermission = "unknown"; /* unknown|granted|denied|sandbox|unavailable */
var micCheckStream = null;  /* stream held only during the microphone level check */
var micCheckStop = null;
var micCheckPeak = 0;
var micCheckToken = 0;
var recSystemReason = "";   /* why a recording has no system track, if any */
var recRequestedSource = "";

/* ---- capture availability: probed once, before the UI is painted ---- */
var captureGate = null;   /* cached displayCaptureAllowed() result */
var sourceForcedToMic = false; /* session-only: the radio shows the
 * microphone because capture is unavailable, while the saved
 * source choice stays untouched in settings.source (see
 * applyCaptureAvailability / syncSourceUI). */
function captureAvailable() {
  if (!captureGate) captureGate = displayCaptureAllowed();
  return !!captureGate.allowed;
}
function applyCaptureAvailability() {
  /* Pre-marking, decided BEFORE the source choice is shown: when
   * capture is unavailable the system/both options are disabled
   * with a short neutral label, and the microphone is selected.
   * Nothing here writes an error status, and nothing here touches
   * the saved choice: only the radio button moves, so a saved
   * system/both choice is restored again in a window where
   * capture works, and no autosave can persist the forced
   * microphone (syncSourceUI skips the assignment while the
   * forcing flag is set). */
  var blocked = !captureAvailable();
  $$('input[name="source"]').forEach(function (radio) {
    if (radio.value === "system" || radio.value === "both") radio.disabled = blocked;
  });
  sourceForcedToMic = false;
  if (blocked && (settings.source === "system" || settings.source === "both")) {
    var mic = document.querySelector('input[name="source"][value="microphone"]');
    if (mic) mic.checked = true;
    sourceForcedToMic = true;
  }
}
function markCaptureBlocked() {
  /* A getDisplayMedia rejection the probe could not foresee (no
   * Permissions-Policy API in this window): remember it for the
   * rest of the page load and re-mark the UI neutrally, silently. */
  captureSessionBlocked = true;
  captureGate = { allowed: false, reason: "policy" };
  applyCaptureAvailability();
  captureStates();
  refreshSystemCheckUI();
  updateRecordingTracks();
}
function classifyCaptureError(error, startedAt) {
  /* A policy rejection arrives without a picker: Chromium rejects
   * immediately, before any picker can be shown or closed, while a
   * user decline takes visibly longer. The message is checked too,
   * when the browser names the policy. Anything else is a plain
   * failure, reported by the caller in its own block. */
  var name = String((error && error.name) || "");
  if (!/NotAllowedError|SecurityError/.test(name)) return "";
  var message = String((error && error.message) || "");
  if (!captureAvailable()) return "policy";
  if (/policy|display-capture/i.test(message)) return "policy";
  if (startedAt && Date.now() - startedAt < 600) return "policy";
  return "denied";
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

/* ---- display-capture policy: check BEFORE calling getDisplayMedia ---- */
function opaqueOrigin() {
  /* The MemuAI module window is an iframe with sandbox="allow-scripts"
   * and no allow-same-origin: its origin is opaque ("null"). */
  try { return typeof window !== "undefined" && window.origin === "null"; }
  catch (e) { return false; }
}
var captureSessionBlocked = false; /* a rejection the probe could not foresee */
function displayCaptureAllowed() {
  /* A remembered policy rejection overrides every probe below:
   * direct callers (captureStates, refreshSystemCheckUI) must see
   * the same answer as the cached gate. */
  if (captureSessionBlocked) return { allowed: false, reason: "policy" };
  /* Check BEFORE calling getDisplayMedia. When display-capture is
   * disallowed, calling it is not just rejected — Chromium logs the
   * policy violation as a console error. Checking first keeps that
   * red exception out of the console entirely.
   *
   * Three signals, because no single one survives every context:
   * the Permissions-Policy API (absent in the Chromium/WebView2
   * context the module window runs in), the older Feature-Policy
   * API, and an opaque origin — a call from an opaque origin is
   * rejected as a policy violation, so it counts as disallowed
   * even when neither policy API exists. */
  try {
    var policy = (typeof document !== "undefined" && document.permissionsPolicy) ||
      (typeof navigator !== "undefined" && navigator.permissionsPolicy);
    if (policy && typeof policy.allowsFeature === "function") {
      if (!policy.allowsFeature("display-capture")) return { allowed: false, reason: "policy" };
    }
    var fp = (typeof document !== "undefined") ? document.featurePolicy : null;
    if (fp && typeof fp.allowsFeature === "function") {
      if (!fp.allowsFeature("display-capture")) return { allowed: false, reason: "policy" };
    }
  } catch (e) { /* no policy API: fall through to the other checks */ }
  var devices = (typeof navigator !== "undefined") ? navigator.mediaDevices : null;
  if (typeof MediaRecorder === "undefined" || !devices || typeof devices.getDisplayMedia !== "function") {
    return { allowed: false, reason: "unsupported" };
  }
  if (opaqueOrigin()) return { allowed: false, reason: "policy" };
  return { allowed: true, reason: "" };
}
function systemUnavailableMessage(reason) {
  /* "policy" never reaches a status line any more (see
   * applyCaptureAvailability / markCaptureBlocked): the neutral
   * label is the only wording for it, here and in the UI. */
  if (reason === "policy") return t("record.state_unavailable");
  if (reason === "denied") return t("record.system_denied");
  if (reason === "unsupported") return t("record.system_unavailable_context");
  if (reason === "no-audio") return t("record.system_no_audio");
  return t("record.system_unavailable");
}

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
function renderList() {
  var query = ($("#search").value || "").trim().toLowerCase();
  var rows = meetings.filter(function (row) {
    return !query || (row.title || "").toLowerCase().indexOf(query) >= 0 ||
      (row.transcript || "").toLowerCase().indexOf(query) >= 0;
  });
  $("#list-empty").hidden = rows.length > 0;
  $("#list").innerHTML = rows.map(function (row) {
    var tags = [];
    if (row.has_transcript || (row.transcript || "").trim()) tags.push('<span class="mm-tag done">' + esc(t("meetings.has_transcript")) + "</span>");
    if ((row.summary || "").trim()) tags.push('<span class="mm-tag done">' + esc(t("meetings.has_summary")) + "</span>");
    return '<button type="button" class="mm-item" data-id="' + esc(row.id) + '">' +
      '<span class="mm-item-ico"><svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/></svg></span>' +
      '<span class="mm-item-main"><b>' + esc(row.title || t("common.untitled")) + "</b>" +
      "<span>" + esc(fmtClock(row.created_at)) + " · " + esc(meetingSourceLabel(row.source)) +
      (row.duration_ms ? " · " + esc(fmtTime(row.duration_ms / 1000)) : "") + "</span></span>" +
      (tags.length ? '<span class="mm-item-tags">' + tags.join("") + "</span>" : "") +
      '<span class="mm-item-chev" aria-hidden="true">\u203a</span></button>';
  }).join("");
  $$("#list .mm-item").forEach(function (item) {
    item.addEventListener("click", function () { openMeeting(item.dataset.id); });
  });
}
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
  current = row;
  currentAudio = "";
  currentMicAudio = "";
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
$("#back").addEventListener("click", function () { show("meetings"); loadMeetings(); });
function renderDetail() {
  if (!current) return;
  $("#detail-title").textContent = current.title || t("common.untitled");
  $("#detail-meta").textContent = fmtClock(current.created_at) + " · " + meetingSourceLabel(current.source) +
    (current.duration_ms ? " · " + fmtTime(current.duration_ms / 1000) : "");
  var summaryCard = $("#summary-card");
  summaryCard.hidden = !(current.summary || "").trim();
  $("#summary").textContent = current.summary || "";
  renderSegments();
  $("#no-transcript").hidden = (current.segments || []).length > 0 || (current.transcript || "").trim() !== "";
  refreshExport();
}
function renderSegments() {
  var segments = current.segments || [];
  var hasSources = segments.some(function (s) { return !!s.source; });
  var speakers = [];
  segments.forEach(function (seg) { if (!seg.source && speakers.indexOf(seg.speaker) < 0) speakers.push(seg.speaker); });
  var namesHtml = (!hasSources && speakers.length > 1) ? speakers.map(function (id) {
    return '<label class="mm-seg"><span class="mm-seg-who">' + esc(t("common.speaker")) + '</span>' +
      '<input data-speaker="' + esc(id) + '" value="' + esc(speakerLabel(id)) + '" data-i18n-aria-label="detail.rename_speaker"></label>';
  }).join("") : "";
  var body = segments.map(function (seg) {
    var who = speakerLabel(seg.speaker, seg.source);
    var badge = (seg.source && current.source === "both" && who !== sourceLabel(seg.source))
      ? '<span class="mm-src-badge">' + esc(sourceLabel(seg.source)) + "</span>" : "";
    return '<div class="mm-seg"><time>' + esc(fmtTime(seg.start)) + "</time>" +
      '<span class="mm-seg-ava" aria-hidden="true">' + esc(String(who || "?").slice(0, 1)) + "</span>" +
      '<span class="mm-seg-who">' + esc(who) + badge + "</span>" +
      "<p>" + esc(seg.text || "") + "</p>" +
      (seg.approximate ? '<span class="mm-approx">' + esc(t("detail.approximate")) + "</span>" : "") + "</div>";
  }).join("");
  $("#segments").innerHTML = namesHtml + body ||
    (segments.length ? body : "");
  if (!segments.length && (current.transcript || "").trim()) {
    $("#segments").innerHTML = '<div class="mm-seg"><p>' + esc(current.transcript) + "</p></div>";
  }
  $$("#segments input[data-speaker]").forEach(function (input) {
    input.addEventListener("change", function () {
      current.speaker_names = Object.assign({}, current.speaker_names);
      current.speaker_names[input.dataset.speaker] = input.value.trim().slice(0, 60) || speakerLabel(input.dataset.speaker);
      storeSet(meetingKey(current.id), current).then(renderSegments).catch(function () {});
    });
  });
}
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
$("#transcribe").addEventListener("click", function () {
  if (!current) return;
  var status = $("#detail-status");
  function run(audio, micAudio) {
    status.textContent = t("detail.transcribing");
    var jobs = [transcribeTrack(audio, current.source === "both" ? "system" : null)];
    if (micAudio) jobs.push(transcribeTrack(micAudio, "microphone"));
    return Promise.all(jobs).then(function (parts) {
      var segments = [];
      parts.forEach(function (rows) { segments = segments.concat(rows); });
      segments.sort(function (a, b) { return (a.start - b.start) || ((a.source || "") < (b.source || "") ? -1 : 1); });
      var texts = [];
      parts.forEach(function (rows) { rows.forEach(function (r) { if (r.text) texts.push(r.text); }); });
      current.transcript = texts.join("\n");
      current.segments = segments;
      current.transcribed_at = Date.now();
      current.approximate_segments = true;
      if (!settings.keep_audio) {
        var drop = [storeRemove(audioKey(current.id))];
        if (micAudio) drop.push(storeRemove(audioMicKey(current.id)));
        return Promise.all(drop).then(function () {
          current.has_audio = false;
          currentAudio = "";
          currentMicAudio = "";
        });
      }
      return null;
    }).then(saveCurrent).then(function () {
      status.textContent = t("detail.saved");
      renderDetail();
      loadMeetings();
    }).catch(function (error) {
      status.textContent = String((error && error.message) || error);
    });
  }
  function loadAudio() {
    return storeGet(audioKey(current.id), null).then(function (value) {
      if (!value || !value.data) throw new Error(t("detail.audio_missing"));
      currentAudio = String(value.data);
      if (current.source !== "both") return null;
      return storeGet(audioMicKey(current.id), null).then(function (mic) {
        currentMicAudio = mic && mic.data ? String(mic.data) : "";
        return currentMicAudio || null;
      });
    });
  }
  if (currentAudio) {
    var mic = current.source === "both" ? (currentMicAudio || null) : null;
    if (mic) return run(currentAudio, mic);
    if (current.source === "both") return loadAudio().then(function (m) { return run(currentAudio, m); });
    return run(currentAudio, null);
  }
  loadAudio().then(function (micAudio) { return run(currentAudio, micAudio); })
    .catch(function (error) { status.textContent = String((error && error.message) || error); });
});

function effectiveModel() {
  if (settings.model) return settings.model;
  var fallback = models.filter(function (row) { return row.default; })[0];
  return fallback ? fallback.id : "";
}
$("#summarize").addEventListener("click", function () {
  if (!current) return;
  var text = (current.segments && current.segments.length ? transcriptText(current.segments) : current.transcript || "").trim();
  if (!text) { $("#detail-status").textContent = t("detail.no_transcript"); return; }
  var model = effectiveModel();
  if (!model) { $("#detail-status").textContent = t("detail.no_model"); return; }
  if (settings.confirm_before_summary !== false) {
    confirmDialog(t("detail.summary_confirm")).then(function (ok) {
      if (ok) doSummarize(model, text);
    });
    return;
  }
  doSummarize(model, text);
});

function doSummarize(model, text) {
  var status = $("#detail-status");
  status.textContent = t("detail.summarizing");
  Memu.request({
    model: model,
    instructions: t("summary.prompt_system") ||
      (Memu.manifest && Memu.manifest.bridge && Memu.manifest.bridge.instructions) || "",
    text: (current.title || t("common.untitled")) + "\n\n<transcript>\n" + text + "\n</transcript>"
  }).then(function (answer) {
    var summary = String((answer && answer.text) || "").trim();
    if (!summary) throw new Error(t("detail.summary_error"));
    current.summary = summary;
    current.summary_model = model;
    return saveCurrent();
  }).then(function () {
    status.textContent = t("detail.saved");
    renderDetail();
    loadMeetings();
  }).catch(function (error) {
    status.textContent = String((error && error.message) || error);
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
  if (kind === "txt") {
    return [title, "", t("detail.export_date") + ": " + created,
      t("detail.export_source") + ": " + meetingSourceLabel(m.source),
      t("detail.export_duration") + ": " + duration, "",
      en() ? "SUMMARY" : "КОНСПЕКТ", summary || t("detail.summary_empty"), "",
      en() ? "TRANSCRIPT" : "ТРАНСКРИПТ", transcript, "", note, ""
    ].join("\n");
  }
  return "# " + title + "\n\n" +
    "- " + t("detail.export_date") + ": " + created + "\n" +
    "- " + t("detail.export_source") + ": " + meetingSourceLabel(m.source) + "\n" +
    "- " + t("detail.export_duration") + ": " + duration + "\n\n" +
    (summary ? summary + "\n\n" : "## " + t("detail.summary") + "\n\n_" + t("detail.summary_empty") + "_\n\n") +
    note + "\n\n## " + t("detail.transcript") + "\n\n" + transcript + "\n";
}
function refreshExport() {
  $("#export-text").value = current ? buildExport("markdown") : "";
}
$("#refresh-export").addEventListener("click", refreshExport);
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
  if (step !== 2) { stopCheckStream(); stopMicCheck(); }
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
  /* A forced microphone (capture unavailable) is session-only:
   * never let the radio state overwrite the saved choice — the
   * next autosave would otherwise persist it and the user's
   * system/both choice would be lost. */
  if (!sourceForcedToMic) settings.source = source;
  if (save !== false) scheduleSaveSettings();
  $("#mic-row").hidden = source !== "microphone" && source !== "both";
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
  refreshSystemCheckUI();
  updateRecordingTracks();
}
$$('input[name="source"]').forEach(function (radio) {
  radio.addEventListener("change", syncSourceUI);
});
$("#to-check").addEventListener("click", function () { setStep(2); refreshMicDevices(true); });
$("#back-sources").addEventListener("click", function () { setStep(1); });
$("#to-record").addEventListener("click", function () { setStep(3); });
$("#skip-check").addEventListener("click", function () { setStep(3); });

/* ---- capture availability (honest states, no guessing) ---- */
function captureStates() {
  /* Microphone: only the host bridge can open it (Memu.record). System
   * audio: the Permissions-Policy pre-check decides "blocked" without
   * ever calling getDisplayMedia; otherwise it stays unknown until the
   * user actually tries it — the OS and the sharing picker decide. */
  var micOk = !!(Memu.record && Memu.record.start);
  var recOk = typeof MediaRecorder !== "undefined";
  setState($("#state-mic"), micOk && recOk, micOk && recOk ? t("record.state_ok") : t("record.state_no"));
  var gate = displayCaptureAllowed();
  if (!gate.allowed) setState($("#state-system"), "na", t("record.state_unavailable"));
  else setState($("#state-system"), null, t("record.state_unknown"));
  var bothOk = (micOk && recOk) && gate.allowed;
  setState($("#state-both"), bothOk ? null : (gate.allowed ? false : "na"),
    bothOk ? t("record.state_unknown") : gate.allowed ? t("record.state_no") : t("record.state_unavailable"));
  setState($("#state-import"), true, t("record.state_ok"));
}
function setState(el, ok, text) {
  if (!el) return;
  el.textContent = text;
  el.className = "mm-state" + (ok === true ? " ok" : ok === false ? " bad" : ok === "na" ? " na" : "");
}

/* ---- microphones: permission first, then enumerateDevices ---- */
function shortDeviceId(id) {
  /* Mirror of backend.py: short_device_id. */
  var text = String(id || "").trim();
  if (text.length <= 10) return text;
  return text.slice(0, 4) + "\u2026" + text.slice(-4);
}
function micDeviceLabel(device) {
  /* Mirror of backend.py: format_mic_label. A real label is used
   * as-is; without one the device is shown honestly as unlabelled
   * with the short id — never as a numbered "Microphone 1/2/3",
   * which would pretend the browser named it. */
  var label = String((device && device.label) || "").trim();
  if (label) return label;
  var suffix = shortDeviceId(device && device.deviceId);
  return t("record.mic_unlabeled") + (suffix ? " \u00b7 " + suffix : "");
}
function classifyMicError(error) {
  var name = String((error && error.name) || "");
  if (name === "SecurityError") return opaqueOrigin() ? "sandbox" : "denied";
  if (name === "NotAllowedError" || name === "AbortError") return "denied";
  return "unavailable";
}
function setMicPermissionUI() {
  var el = $("#mic-permission");
  if (!el) return;
  var key = {
    granted: "record.mic_permission_granted",
    denied: "record.mic_permission_denied",
    sandbox: "record.mic_permission_sandbox",
    unavailable: "record.mic_permission_unavailable",
    unknown: "record.mic_permission_unknown"
  }[micPermission] || "record.mic_permission_unknown";
  el.textContent = t(key);
  el.className = "mm-hint mm-mic-" + micPermission;
}
function requestMicPermission() {
  /* Ask the browser for microphone access BEFORE enumerateDevices,
   * so it can return real device names. The returned stream is the
   * caller's to stop. Never throws: a refusal is a state, reported
   * by setMicPermissionUI, not an exception. In the sandboxed module
   * window this rejects with SecurityError (opaque origin) — the
   * host bridge opens the microphone there instead. */
  var devices = (typeof navigator !== "undefined") ? navigator.mediaDevices : null;
  if (!devices || typeof devices.getUserMedia !== "function") {
    micPermission = "unavailable";
    setMicPermissionUI();
    return Promise.resolve(null);
  }
  return devices.getUserMedia({ audio: true }).then(function (stream) {
    micPermission = "granted";
    setMicPermissionUI();
    return stream;
  }).catch(function (error) {
    micPermission = classifyMicError(error);
    setMicPermissionUI();
    return null;
  });
}
function refreshMicDevices(requestPermission) {
  var select = $("#mic-device");
  if (!select) return Promise.resolve([]);
  var status = $("#mic-permission");
  if (status) status.textContent = t("record.mic_loading");
  function enumerate() {
    var devices = (typeof navigator !== "undefined") ? navigator.mediaDevices : null;
    if (!devices || typeof devices.enumerateDevices !== "function") {
      select.innerHTML = '<option value="">' + esc(t("record.mic_none")) + "</option>";
      select.disabled = true;
      micPermission = "unavailable";
      setMicPermissionUI();
      return Promise.resolve([]);
    }
    return devices.enumerateDevices().then(function (list) {
      var mics = (list || []).filter(function (d) { return d && d.kind === "audioinput"; });
      select.disabled = false;
      var hasLabels = mics.some(function (d) { return String(d.label || "").trim(); });
      if (hasLabels && micPermission === "unknown") micPermission = "granted";
      var options = '<option value="">' + esc(t("record.mic_device_default")) + "</option>" +
        mics.map(function (d) {
          return '<option value="' + esc(d.deviceId || "") + '">' + esc(micDeviceLabel(d)) + "</option>";
        }).join("");
      if (!mics.length) options = '<option value="">' + esc(t("record.mic_none")) + "</option>";
      select.innerHTML = options;
      if (settings.mic_device && mics.some(function (d) { return d.deviceId === settings.mic_device; })) {
        select.value = settings.mic_device;
      } else {
        select.value = "";
      }
      setMicPermissionUI();
      return mics;
    }).catch(function () {
      select.innerHTML = '<option value="">' + esc(t("record.mic_none")) + "</option>";
      setMicPermissionUI();
      return [];
    });
  }
  if (requestPermission) {
    return requestMicPermission().then(function (stream) {
      if (stream) stopTracks(stream);
      return enumerate();
    });
  }
  return enumerate();
}
$("#mic-device").addEventListener("change", function (event) {
  settings.mic_device = event.target.value || "";
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

/* ---- sound check: separate checks for the microphone and system audio.
 * The microphone check is a live level meter (AnalyserNode) and never
 * touches getDisplayMedia; the system check is the only place that
 * calls getDisplayMedia, and only after the pre-check in
 * displayCaptureAllowed() says capture is actually available. ---- */
function startLevelMeter(stream, barEl, onPeak) {
  try {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx || !stream || !barEl) return function () {};
    var ctx = new Ctx();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    var data = new Uint8Array(analyser.frequencyBinCount);
    var stopped = false;
    (function loop() {
      if (stopped) return;
      analyser.getByteTimeDomainData(data);
      var peak = 0;
      for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
      if (onPeak) onPeak(peak);
      barEl.style.width = Math.round(Math.min(1, peak * 1.6) * 100) + "%";
      barEl.parentNode.setAttribute("aria-valuenow", String(Math.round(peak * 100)));
      requestAnimationFrame(loop);
    })();
    return function () {
      stopped = true;
      try { ctx.close(); } catch (e) {}
      barEl.style.width = "0%";
    };
  } catch (e) { return function () {}; }
}
function stopCheckStream() {
  if (checkMeterStop) { checkMeterStop(); checkMeterStop = null; }
  if (checkStream) { stopTracks(checkStream); checkStream = null; }
  var btn = $("#check-system-stop");
  if (btn) btn.hidden = true;
  var start = $("#check-system");
  if (start) start.hidden = false;
}
function stopMicCheck() {
  micCheckToken++;
  if (micCheckStop) { micCheckStop(); micCheckStop = null; }
  if (micCheckStream) { stopTracks(micCheckStream); micCheckStream = null; }
  var btn = $("#check-mic-stop");
  if (btn) btn.hidden = true;
  var start = $("#check-mic");
  if (start) start.hidden = false;
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
  var gate = displayCaptureAllowed();
  var btn = $("#check-system");
  var hint = $("#check-system-hint");
  if (!gate.allowed) {
    if (btn) btn.disabled = true;
    if (hint) hint.textContent = t("record.check_system_unavailable");
    setState($("#state-system"), "na", t("record.state_unavailable"));
  } else {
    if (btn) btn.disabled = false;
    if (hint) hint.textContent = t("record.check_system_hint");
  }
  updateRecordingTracks();
}
function checkSystemAudio() {
  var gate = displayCaptureAllowed();
  if (!gate.allowed) {
    /* Silent: the option and the check are already pre-marked. */
    refreshSystemCheckUI();
    return;
  }
  /* The picker really is about to open: only now may the text say so. */
  setCheckStatus(t("record.checking_picker"), false);
  var startedAt = Date.now();
  navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }).then(function (stream) {
    var audioTracks = stream.getAudioTracks ? stream.getAudioTracks() : [];
    if (!audioTracks.length) {
      stopTracks(stream);
      setCheckStatus(t("record.system_no_audio"), false);
      setState($("#state-system"), false, t("record.state_no"));
      return;
    }
    checkStream = stream;
    setState($("#state-system"), true, t("record.state_ok"));
    setCheckStatus(t("record.check_ok"), false);
    checkMeterStop = startLevelMeter(stream, $("#level-system"));
    $("#check-system").hidden = true;
    $("#check-system-stop").hidden = false;
  }).catch(function (error) {
    /* Handled: nothing leaks to the console. A rejection the probe
     * could not foresee is classified quietly: a policy block
     * re-marks the UI neutrally with no status text at all, while
     * a picker the user declined says so, once, in its own block. */
    var reason = classifyCaptureError(error, startedAt);
    if (reason === "policy") {
      markCaptureBlocked();
      setCheckStatus("", false);
      return;
    }
    setCheckStatus(systemUnavailableMessage(reason), false);
    setState($("#state-system"), false, t("record.state_no"));
  });
}
$("#check-system").addEventListener("click", checkSystemAudio);
$("#check-system-stop").addEventListener("click", function () {
  stopCheckStream();
  setCheckStatus("", false);
});
function finishMicLevelCheck() {
  var peak = micCheckPeak;
  stopMicCheck();
  setCheckStatus(peak > 0.02 ? t("record.mic_level_done") : t("record.mic_level_silent"), true);
}
function micCheckViaBridge() {
  /* Fallback when this window cannot open the microphone itself
   * (sandboxed module window, no getUserMedia): a short test clip
   * through the host bridge. It is only played back and discarded,
   * and it never involves screen capture. */
  if (!Memu.record || !Memu.record.start) { setCheckStatus(t("record.no_recorder"), true); return; }
  if (recording) { setCheckStatus(t("record.check_busy"), true); return; }
  setCheckStatus(t("record.mic_level_unavailable"), true);
  var args = settings.mic_device ? { deviceId: settings.mic_device } : undefined;
  Memu.record.start(args).then(function () {
    return new Promise(function (resolve) { setTimeout(resolve, 2000); });
  }).then(function () { return Memu.record.stop(); })
    .then(function (result) {
      var player = $("#mic-test-player");
      if (result && result.audio) {
        player.src = result.audio;
        player.hidden = false;
        ensureAudioDuration(player);
        setCheckStatus(t("record.mic_test_done"), true);
      } else {
        setCheckStatus(t("record.mic_test_none"), true);
      }
    }).catch(function (error) {
      setCheckStatus(String((error && error.message) || error), true);
    });
}
function startMicLevelCheck() {
  /* Microphone check: a live level meter from the microphone stream
   * (AnalyserNode). No getDisplayMedia, no screen capture, no picker. */
  if (recording) { setCheckStatus(t("record.check_busy"), true); return; }
  var devices = (typeof navigator !== "undefined") ? navigator.mediaDevices : null;
  var Ctx = (typeof window !== "undefined") ? (window.AudioContext || window.webkitAudioContext) : null;
  if (!devices || typeof devices.getUserMedia !== "function" || !Ctx) {
    micCheckViaBridge();
    return;
  }
  setCheckStatus(t("record.mic_level_requesting"), true);
  var constraints = settings.mic_device
    ? { audio: { deviceId: { ideal: settings.mic_device } } }
    : { audio: true };
  devices.getUserMedia(constraints).then(function (stream) {
    micCheckStream = stream;
    micCheckPeak = 0;
    var token = ++micCheckToken;
    micCheckStop = startLevelMeter(stream, $("#level-mic"), function (peak) {
      micCheckPeak = Math.max(micCheckPeak, peak);
    });
    $("#check-mic").hidden = true;
    $("#check-mic-stop").hidden = false;
    setCheckStatus(t("record.mic_level_listening"), true);
    setTimeout(function () {
      if (token === micCheckToken && micCheckStream) finishMicLevelCheck();
    }, 8000);
  }).catch(function (error) {
    micPermission = classifyMicError(error);
    setMicPermissionUI();
    micCheckViaBridge();
  });
}
$("#check-mic").addEventListener("click", startMicLevelCheck);
$("#check-mic-stop").addEventListener("click", finishMicLevelCheck);

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
  var activeSys = !!recording && !!recording.recorder;
  if (recording) {
    micState.textContent = activeMic ? t("record.rec_track_mic_active") : t("record.rec_track_mic_off");
    if (activeSys) sysState.textContent = t("record.rec_track_system_active");
    else if (!captureAvailable()) sysState.textContent = t("record.state_unavailable");
    else if (recSystemReason || source === "system" || source === "both") {
      sysState.textContent = t("record.rec_track_system_unavailable") + " " + systemUnavailableMessage(recSystemReason);
    } else sysState.textContent = t("record.rec_track_system_off");
  } else {
    micState.textContent = source === "system" ? t("record.rec_track_mic_off") : t("record.rec_track_mic_planned");
    if (source === "system" || source === "both") {
      sysState.textContent = captureAvailable() ? t("record.rec_track_system_planned")
        : t("record.state_unavailable");
    } else sysState.textContent = t("record.rec_track_system_off");
  }
  if (meter) meter.hidden = !(recording && activeSys);
  if (label) label.textContent = (recording && activeSys) ? t("record.level_system") : "";
}
function recElapsed() { return recording ? Date.now() - recording.started : 0; }
function tickTimer() {
  $("#rec-time").textContent = fmtTime(recElapsed() / 1000);
}
function setRecordingUI(active) {
  document.getElementById("app").classList.toggle("recording", !!active);
  $("#rec-dot").classList.toggle("on", !!active);
  var toggleLabel = $("#rec-toggle-label");
  var toggleText = t(active ? "record.stop" : "record.start");
  if (toggleLabel) toggleLabel.textContent = toggleText;
  else $("#rec-toggle").textContent = toggleText;
  $("#rec-cancel").hidden = !active;
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
  recSystemReason = "";
  recRequestedSource = selectedSource();
  var source = selectedSource();
  var title = $("#rec-title").value.trim();
  if (source === "microphone") return startMicRecording(title);
  if (source === "system") return startSystemRecording(title, false);
  if (source === "both") return startBothRecording(title);
  $("#import-file").click();
}
function stopTracks(stream) {
  try { (stream.getTracks ? stream.getTracks() : []).forEach(function (track) { track.stop(); }); } catch (e) {}
}
function startMicRecording(title) {
  if (!Memu.record || !Memu.record.start) {
    $("#rec-status").textContent = t("record.no_recorder");
    return Promise.resolve();
  }
  $("#rec-status").textContent = "";
  var args = settings.mic_device ? { deviceId: settings.mic_device } : undefined;
  return Memu.record.start(args).then(function () {
    liveCaptions = [];
    recRequestedSource = "microphone";
    recording = { source: "microphone", title: title, started: Date.now(), timer: setInterval(tickTimer, 500) };
    setRecordingUI(true);
    tickTimer();
  }).catch(function (error) {
    $("#rec-status").textContent = String((error && error.message) || error);
  });
}
function startSystemStream(title) {
  /* Guarded: returns null (with an explanation) instead of calling
   * getDisplayMedia when the policy forbids it. */
  var gate = displayCaptureAllowed();
  if (!gate.allowed) {
    /* No call and no status: the picker cannot appear, and the
     * pre-marked UI already says the option is unavailable. */
    recSystemReason = gate.reason;
    return Promise.resolve(null);
  }
  var startedAt = Date.now();
  return navigator.mediaDevices.getDisplayMedia({ video: true, audio: true }).then(function (stream) {
    var audioTracks = stream.getAudioTracks ? stream.getAudioTracks() : [];
    if (!audioTracks.length) {
      /* The picker did open and the user shared a screen without
       * sound: only here is the picker mentioned. */
      recSystemReason = "no-audio";
      stopTracks(stream);
      setState($("#state-system"), false, t("record.state_no"));
      $("#rec-status").textContent = t("record.system_no_audio");
      updateRecordingTracks();
      return null;
    }
    recSystemReason = "";
    setState($("#state-system"), true, t("record.state_ok"));
    $("#system-note").hidden = true;
    return stream;
  }).catch(function (error) {
    var reason = classifyCaptureError(error, startedAt);
    recSystemReason = reason;
    if (reason === "policy") {
      /* The probe could not see the policy: re-mark silently. */
      markCaptureBlocked();
      return null;
    }
    setState($("#state-system"), false, t("record.state_no"));
    $("#rec-status").textContent = systemUnavailableMessage(reason);
    updateRecordingTracks();
    return null;
  });
}
function startSystemRecording(title, withMic) {
  return startSystemStream(title).then(function (stream) {
    if (!stream) return;
    var chunks = [];
    var recorder = new MediaRecorder(stream);
    liveCaptions = [];
    recRequestedSource = withMic ? "both" : "system";
    recording = {
      source: withMic ? "both" : "system", title: title, started: Date.now(), stream: stream,
      recorder: recorder, chunks: chunks, timer: setInterval(tickTimer, 500),
      chunkStarted: Date.now()
    };
    recorder.ondataavailable = function (event) {
      if (event.data && event.data.size) {
        chunks.push(event.data);
        transcribeChunk(event.data);
      }
    };
    recorder.start(8000);
    liveMeterStop = startLevelMeter(stream, $("#level-live"));
    setRecordingUI(true);
    tickTimer();
  });
}
function startBothRecording(title) {
  /* One meeting, two tracks started together: the microphone through
   * the host bridge, the system sound through the guarded capture.
   * If the system capture is blocked, the microphone still records —
   * the meeting is saved with the tracks that exist, and the UI says
   * which ones they are. */
  if (!Memu.record || !Memu.record.start) return startSystemRecording(title, false);
  $("#rec-status").textContent = "";
  var args = settings.mic_device ? { deviceId: settings.mic_device } : undefined;
  return Memu.record.start(args).then(function () {
    return startSystemRecording(title, true).then(function () {
      if (!recording) {
        /* System capture failed: keep the microphone recording alone,
         * and say so while it records — no silent downgrade, no
         * system labels later. */
        if (!recSystemReason) recSystemReason = displayCaptureAllowed().reason || "";
        liveCaptions = [];
        recRequestedSource = "both";
        recording = { source: "microphone", title: title, started: Date.now(), timer: setInterval(tickTimer, 500), micOnlyFallback: true };
        /* Policy-blocked: the microphone recording stands on its
         * own, with no status text. A declined picker is explained. */
        $("#rec-status").textContent = captureAvailable()
          ? t("record.rec_mic_only") + " " + systemUnavailableMessage(recSystemReason) : "";
        setRecordingUI(true);
        tickTimer();
      } else {
        recording.micActive = true;
        setRecordingUI(true);
      }
    });
  }).catch(function (error) {
    $("#rec-status").textContent = String((error && error.message) || error);
  });
}
function transcribeChunk(blob) {
  /* Live captions, chunk by chunk: the only streaming-like path the
   * project offers, because Memu.transcribe itself is whole-file. A chunk
   * that fails to transcribe is skipped — no invented captions. */
  blobToDataUrl(blob).then(function (dataUrl) {
    return Memu.transcribe({ audio: dataUrl });
  }).then(function (result) {
    var text = String((result && result.text) || "").trim();
    if (!text) return;
    liveCaptions.push({ start: (Date.now() - (recording ? recording.started : Date.now())) / 1000, speaker: "speaker-1", source: "system", text: text });
    renderCaptions();
  }).catch(function () { /* a failed chunk stays silent */ });
}
function stopSystemRecorder(rec) {
  return new Promise(function (resolve) {
    try {
      rec.recorder.onstop = resolve;
      if (rec.recorder.state !== "inactive") rec.recorder.stop();
      else resolve();
    } catch (e) { resolve(); }
    setTimeout(resolve, 2000);
  }).then(function () {
    stopTracks(rec.stream);
    var blob = new Blob(rec.chunks, { type: (rec.recorder && rec.recorder.mimeType) || "audio/webm" });
    if (!blob.size) return null;
    return blobToDataUrl(blob).then(function (dataUrl) {
      return { source: "system", audio: dataUrl, mime: cleanMime(blob.type), ms: Date.now() - rec.started };
    });
  });
}
function stopRecording() {
  if (!recording) return Promise.resolve();
  var rec = recording;
  recording = null;
  clearInterval(rec.timer);
  if (liveMeterStop) { liveMeterStop(); liveMeterStop = null; }
  setRecordingUI(false);
  var micJob = null;
  if (rec.source === "microphone" || rec.micActive || rec.micOnlyFallback) {
    micJob = Memu.record.stop().then(function (result) {
      if (!result || !result.audio) return null;
      return { source: "microphone", audio: result.audio, mime: cleanMime(result.mime), ms: result.ms || recElapsed() };
    }).catch(function (error) {
      $("#rec-status").textContent = String((error && error.message) || error);
      return null;
    });
  }
  var sysJob = rec.recorder ? stopSystemRecorder(rec) : Promise.resolve(null);
  return Promise.all([micJob || Promise.resolve(null), sysJob]).then(function (parts) {
    var tracks = parts.filter(Boolean);
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
  pending = { tracks: tracks, title: title, ms: ms, source: source };
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
$("#rec-toggle").addEventListener("click", function () {
  if (recording) stopRecording();
  else startRecording();
});
$("#rec-cancel").addEventListener("click", function () {
  if (!recording) return;
  var rec = recording;
  recording = null;
  clearInterval(rec.timer);
  if (liveMeterStop) { liveMeterStop(); liveMeterStop = null; }
  setRecordingUI(false);
  if ((rec.source === "microphone" || rec.micActive || rec.micOnlyFallback) && Memu.record && Memu.record.cancel) Memu.record.cancel().catch(function () {});
  if (rec.stream) stopTracks(rec.stream);
  try { if (rec.recorder && rec.recorder.state !== "inactive") rec.recorder.stop(); } catch (e) {}
  liveCaptions = [];
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
    transcribed_at: 0, approximate_segments: true, speaker_names: {}
  };
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
    loadMeetings().then(function () { openMeeting(record.id); });
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
function renderCaptions() {
  var box = $("#captions");
  var large = settings.captions_large || $("#captions-large").checked;
  box.classList.toggle("large", !!large);
  var rows = [];
  if (recording && (recording.source === "system" || recording.source === "both") && liveCaptions.length) {
    rows = liveCaptions.map(function (cap, i) {
      return { start: cap.start, speaker: cap.speaker, source: cap.source, text: cap.text, current: i === liveCaptions.length - 1 };
    });
  } else if (current && current.segments && current.segments.length) {
    rows = current.segments.map(function (seg) {
      return { start: seg.start, speaker: seg.speaker, source: seg.source, text: seg.text, current: false };
    });
  }
  $("#captions-empty").hidden = rows.length > 0;
  box.innerHTML = rows.map(function (row) {
    return '<div class="mm-cap' + (row.current ? " current" : "") + '"><span class="mm-cap-who">' +
      esc(speakerLabel(row.speaker, row.source)) + " · " + esc(fmtTime(row.start)) + "</span>" + esc(row.text) + "</div>";
  }).join("");
  var last = box.lastElementChild;
  if (last) last.scrollIntoView({ block: "end" });
}
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
  $("#captions-large").checked = !!settings.captions_large;
  var mic = $("#mic-device");
  if (mic && settings.mic_device) mic.value = settings.mic_device;
  var sourceRadio = document.querySelector('input[name="source"][value="' + (settings.source || "microphone") + '"]');
  if (sourceRadio) sourceRadio.checked = true;
  if (modelSelectorHandle) modelSelectorHandle.sync();
  /* STT is the Live setup: the module cannot read preferences from the
   * sandbox, so it names the keys and points at Settings → Voice instead
   * of showing a model it did not actually read. */
  $("#stt-info").innerHTML = "<b>" + esc(t("settings.stt_source_live")) + "</b><br>" +
    esc(t("settings.stt_keys")) + ' <code>voice_stt_provider</code>, <code>voice_stt_local_model</code>, ' +
    "<code>voice_stt_api_model</code>, <code>voice_language</code>";
  syncSourceUI(false);
  applyCaptureAvailability();
}
function readSettingsFromControls() {
  settings.model = $("#set-model").value || "";
  settings.summary_lang = $("#set-lang").value || "auto";
  settings.speaker_heuristic = $("#set-speakers").checked;
  settings.keep_audio = $("#set-keep-audio").checked;
  settings.captions_large = $("#set-large").checked;
  settings.confirm_before_summary = $("#set-confirm").checked;
}
$("#set-model").addEventListener("change", function () { settings.model = $("#set-model").value || ""; scheduleSaveSettings(); });
$("#set-lang").addEventListener("change", function () { settings.summary_lang = $("#set-lang").value || "auto"; scheduleSaveSettings(); });
$("#set-speakers").addEventListener("change", function () { settings.speaker_heuristic = $("#set-speakers").checked; scheduleSaveSettings(); });
$("#set-keep-audio").addEventListener("change", function () { settings.keep_audio = $("#set-keep-audio").checked; scheduleSaveSettings(); });
$("#set-large").addEventListener("change", function () { settings.captions_large = $("#set-large").checked; scheduleSaveSettings(); renderCaptions(); });
$("#set-confirm").addEventListener("change", function () { settings.confirm_before_summary = $("#set-confirm").checked; scheduleSaveSettings(); });
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
applyCaptureAvailability();
captureStates();
renderSettings();
loadModels();
refreshMicDevices(false);
loadMeetings();
show("meetings");
setStep(1);
if (typeof Memu.ready === "function") Memu.ready();

window.addEventListener("pagehide", function () {
  stopCheckStream();
  if (!recording) return;
  var rec = recording;
  recording = null;
  clearInterval(rec.timer);
  if ((rec.source === "microphone" || rec.micActive || rec.micOnlyFallback) && Memu.record && Memu.record.cancel) {
    try { Memu.record.cancel(); } catch (e) {}
  }
  if (rec.stream) stopTracks(rec.stream);
  try { if (rec.recorder && rec.recorder.state !== "inactive") rec.recorder.stop(); } catch (e) {}
});
