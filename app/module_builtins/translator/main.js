/* Переводчик MemuAI. Только мост Memu: ни сети, ни своих запросов.
 * Все тексты интерфейса — в i18n.json; при смене языка MemuAI разметка
 * переводится мостом, а строки, собранные здесь, перерисовывает relabel().
 */
const t = (key, vars) => Memu.t(key, vars);
const LANGS = [
  ["auto", ""],
  ["ru", "Русский"], ["en", "English"], ["de", "Deutsch"], ["fr", "Français"],
  ["es", "Español"], ["it", "Italiano"], ["pt", "Português"], ["nl", "Nederlands"],
  ["uk", "Українська"], ["pl", "Polski"], ["cs", "Čeština"], ["tr", "Türkçe"],
  ["ar", "العربية"], ["he", "עברית"], ["hi", "हिन्दी"], ["id", "Indonesia"],
  ["vi", "Tiếng Việt"], ["th", "ไทย"], ["zh", "中文"], ["ja", "日本語"], ["ko", "한국어"],
];
const MODES = ["exact", "natural", "formal", "summary"];
const TONES = ["auto", "neutral", "formal", "casual"];
const OUTPUTS = ["only", "both", "notes"];

const saved = Memu.saved || {};
const defaults = (Memu.manifest && Memu.manifest.bridge) || {};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

/* The shipped instruction exists in every locale. An instruction that equals
 * one of them is "the default" and follows the language; anything else is the
 * person's own text and is never touched. */
function shippedInstructions() {
  return Memu.i18n.locales().map((lang) => Memu.i18n.get("instructions.default", lang)).filter(Boolean);
}
function isDefaultInstruction(text) {
  const value = String(text || "").trim();
  return !value || shippedInstructions().some((item) => item.trim() === value);
}
function defaultInstructions() {
  return isDefaultInstruction(defaults.instructions) ? t("instructions.default") : defaults.instructions;
}

const el = {
  app: $("#app"),
  from: $("#from"),
  to: $("#to"),
  swap: $("#swap"),
  model: $("#model"),
  modelLabel: $("#model-label"),
  mode: $("#mode"),
  tone: $("#tone"),
  output: $("#output"),
  glossary: $("#glossary"),
  instructions: $("#instructions"),
  keep: $("#keep"),
  source: $("#source"),
  result: $("#result"),
  status: $("#status"),
  files: $("#files"),
  panel: $("#panel"),
  settings: $("#settings"),
  fromLabel: $("#from-label"),
  toLabel: $("#to-label"),
  sourceCount: $("#source-count"),
  resultCount: $("#result-count"),
  history: $("#history"),
  historyList: $("#history-list"),
  historyCount: $("#history-count"),
  historyToggle: $("#history-toggle"),
  stop: $("#stop"),
  go: $("#go"),
  live: $("#live"),
  dictate: $("#dictate"),
  dictateLabel: $("#dictate-label"),
  speak: $("#speak"),
  speakLabel: $("#speak-label"),
};

let images = [];      // [{ name, url }]
let documents = [];   // [{ name, text }]
let history = Array.isArray(saved.history) ? saved.history.slice(0, 30) : [];
let currentJob = null;
let recorder = null;
let recordTimer = null;
let recordStarted = 0;
let playing = null;
let modelState = "loading"; // loading | ready | none | error

/* --------------------------------- помощники -------------------------------- */

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function setStatus(text, kind) {
  el.status.textContent = text || "";
  el.status.className = "tr-status" + (kind ? " " + kind : "");
  el.status.title = text || "";
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error(t("file.read_error")));
    reader.readAsDataURL(file);
  });
}

function languageName(code) {
  if (code === "auto") return t("lang.auto");
  const row = LANGS.find(([id]) => id === code);
  return row ? row[1] : code;
}

const number = (value) => Memu.i18n.number(value);

/* ----------------------------------- выборы --------------------------------- */

function fillLanguages(select, value, withAuto) {
  const rows = withAuto ? LANGS : LANGS.filter(([id]) => id !== "auto");
  select.innerHTML = rows.map(([id]) => `<option value="${id}">${esc(languageName(id))}</option>`).join("");
  select.value = rows.some(([id]) => id === value) ? value : (withAuto ? "auto" : "en");
}

fillLanguages(el.from, saved.from || "auto", true);
fillLanguages(el.to, saved.to && saved.to !== "auto" ? saved.to : "en", false);
el.source.value = saved.source || "";
el.result.value = saved.result || "";
el.mode.value = MODES.includes(saved.mode) ? saved.mode : "exact";
el.tone.value = TONES.includes(saved.tone) ? saved.tone : "auto";
el.output.value = OUTPUTS.includes(saved.output) ? saved.output : "only";
el.glossary.value = saved.glossary || "";
el.instructions.value = saved.instructions && !isDefaultInstruction(saved.instructions)
  ? saved.instructions : defaultInstructions();
el.keep.checked = saved.keep_in_chat != null ? !!saved.keep_in_chat : !!defaults.keep_in_chat;

function syncLabels() {
  el.fromLabel.textContent = el.from.value === "auto" ? t("source.title") : languageName(el.from.value);
  el.toLabel.textContent = languageName(el.to.value);
  el.swap.disabled = el.from.value === "auto";
  el.swap.title = el.from.value === "auto" ? t("swap.need_source") : t("swap.title");
}

function syncMode() {
  $$("[data-mode]").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.mode === el.mode.value)));
}

function counts() {
  el.sourceCount.textContent = number(el.source.value.length);
  el.resultCount.textContent = number(el.result.value.length);
}

syncLabels();
syncMode();
counts();

/* --------------------------------- сохранение ------------------------------- */

let saveTimer = null;
function snapshot() {
  return {
    source: el.source.value,
    result: el.result.value,
    from: el.from.value,
    to: el.to.value,
    model: el.model.value || saved.model || "",
    mode: el.mode.value,
    tone: el.tone.value,
    output: el.output.value,
    glossary: el.glossary.value,
    // A default instruction is stored empty so it keeps following the language.
    instructions: isDefaultInstruction(el.instructions.value) ? "" : el.instructions.value,
    keep_in_chat: el.keep.checked,
    history: history.slice(0, 30),
  };
}
function remember(immediate) {
  clearTimeout(saveTimer);
  const run = () => Memu.save(snapshot()).catch((error) => setStatus(error.message, "error"));
  if (immediate) run();
  else saveTimer = setTimeout(run, 500);
}
["input", "change"].forEach((name) => document.body.addEventListener(name, () => remember(false)));
// Страница в iframe сносится без beforeunload, поэтому отложенную запись
// сбрасываем ещё и на pagehide — иначе последние правки теряются.
window.addEventListener("pagehide", () => remember(true));
window.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") remember(true);
});

/* ----------------------------------- модель --------------------------------- */

let modelNames = {};   // Round 34: id → readable name (the footer showed the raw id «nineaws-gpt-6.1-sol»)
function syncModelLabel() {
  el.modelLabel.textContent = modelNames[el.model.value] || el.model.value || "";
  el.modelLabel.title = el.model.value ? t("model.hint") : "";
}

function drawEmptyModelOption() {
  if (modelState === "ready") return;
  const key = modelState === "none" ? "model.none" : modelState === "error" ? "model.unavailable" : "model.loading";
  el.model.innerHTML = `<option value="">${esc(t(key))}</option>`;
}

Memu.models().then((rows) => {
  const list = Array.isArray(rows) ? rows : [];
  const chat = list.filter((item) => !item.image && !item.video && !item.audio);
  const usable = chat.length ? chat : list;
  modelState = usable.length ? "ready" : "none";
  if (usable.length) el.model.innerHTML = usable.map((item) => `<option value="${esc(item.id)}">${esc(item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id)}</option>`).join("");
  else drawEmptyModelOption();
  modelNames = Object.fromEntries(usable.map((item) => [item.id, item.name || item.id]));
  // Round 34: MemuAI's default model comes before "the first one in the list".
  const wanted = [saved.model, defaults.model, (usable.find((item) => item.default) || {}).id].find((id) => id && usable.some((item) => item.id === id));
  if (wanted) el.model.value = wanted;
  if (!usable.length) setStatus(t("model.connect"), "error");
  syncModelLabel();
}).catch((error) => {
  modelState = "error";
  drawEmptyModelOption();
  setStatus(error.message, "error");
});
el.model.addEventListener("change", syncModelLabel);

/* ---------------------------------- инструкции ------------------------------ */

function buildInstructions(from, to) {
  const parts = [el.instructions.value.trim() || defaultInstructions() || t("rule.base")];
  parts.push(t("rule.direction", { from, to }));
  parts.push(t("rule." + (MODES.includes(el.mode.value) ? el.mode.value : "exact")));
  if (el.tone.value !== "auto") parts.push(t("rule.tone." + el.tone.value));
  parts.push(t("rule.keep"));
  parts.push(t("rule.data"));
  const glossary = el.glossary.value.trim();
  if (glossary) parts.push(t("rule.glossary", { glossary }));
  if (images.length) parts.push(t("rule.images"));
  parts.push(t("rule.output." + (OUTPUTS.includes(el.output.value) ? el.output.value : "only")));
  return parts.join("\n");
}

/* ------------------------------------ файлы --------------------------------- */

function renderFiles() {
  const chips = [
    ...images.map((item, index) => ({ kind: "image", index, label: t("file.image", { name: item.name }) })),
    ...documents.map((item, index) => ({ kind: "doc", index, label: t("file.doc", { name: item.name, count: number(item.text.length) }) })),
  ];
  el.files.innerHTML = chips.map((chip) => `<span class="tr-file"><span title="${esc(chip.label)}">${esc(chip.label)}</span>
    <button type="button" data-remove-file="${chip.kind}:${chip.index}" aria-label="${esc(t("file.remove", { name: chip.label }))}">
      <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></span>`).join("");
}
el.files.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-file]");
  if (!button) return;
  const [kind, index] = button.dataset.removeFile.split(":");
  if (kind === "image") images.splice(Number(index), 1);
  else documents.splice(Number(index), 1);
  renderFiles();
});

$("#attach-image").onclick = () => $("#file-image").click();
$("#attach-doc").onclick = () => $("#file-doc").click();

$("#file-image").onchange = async (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;
  try {
    images = [{ name: file.name, url: await readFile(file) }];
    renderFiles();
    setStatus(t("status.image"), "ok");
  } catch (error) { setStatus(error.message, "error"); }
};

$("#file-doc").onchange = async (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";
  if (!file) return;
  setStatus(t("status.reading"), "busy");
  try {
    const parsed = await Memu.extract({ name: file.name, data: await readFile(file) });
    const text = String(parsed.text || "");
    if (!text.trim()) throw new Error(t("status.no_text"));
    // Пустая колонка получает текст целиком; иначе документ идёт вложением,
    // чтобы не отправлять один и тот же текст дважды.
    if (!el.source.value.trim()) {
      el.source.value = text;
      documents = [];
    } else {
      documents = [{ name: parsed.name || file.name, text }];
    }
    renderFiles();
    counts();
    setStatus(parsed.truncated ? t("status.doc_partial") : t("status.doc_read"), parsed.truncated ? "error" : "ok");
    remember(false);
  } catch (error) {
    setStatus(error.message, "error");
  }
};

/* ----------------------------------- диктовка ------------------------------- */

function drawRecordLabel() {
  if (!recorder) { el.dictateLabel.textContent = t("dictate"); return; }
  const total = Math.round((Date.now() - recordStarted) / 1000);
  el.dictateLabel.textContent = t("dictate.stop", { time: `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}` });
}

function recordingUi(active, started) {
  el.dictate.setAttribute("aria-pressed", String(!!active));
  clearInterval(recordTimer);
  recordTimer = null;
  recordStarted = started || Date.now();
  drawRecordLabel();
  if (active) recordTimer = setInterval(drawRecordLabel, 500);
}

// Запись ведёт окно MemuAI (Memu.record): странице модуля в песочнице браузер
// микрофон не даёт. Первое нажатие начинает запись, второе — распознаёт.
el.dictate.onclick = async () => {
  if (recorder) {
    recorder = null;
    recordingUi(false);
    setStatus(t("status.recognizing"), "busy");
    el.dictate.disabled = true;
    try {
      const clip = await Memu.record.stop();
      const answer = await Memu.transcribe({ audio: clip.audio });
      const text = String((answer && answer.text) || "").trim();
      if (!text) throw new Error(t("status.not_recognized"));
      el.source.value = el.source.value.trim() ? `${el.source.value.trim()} ${text}` : text;
      counts();
      setStatus(t("status.recognized"), "ok");
      remember(true);
    } catch (error) {
      setStatus(error.message || String(error), "error");
    } finally {
      el.dictate.disabled = false;
    }
    return;
  }
  el.dictate.disabled = true;
  try {
    const started = await Memu.record.start();
    recorder = true;
    recordingUi(true, (started && started.started) || Date.now());
    setStatus(t("status.speak_now"), "busy");
  } catch (error) {
    recorder = null;
    recordingUi(false);
    setStatus(error.message || String(error), "error");
  } finally {
    el.dictate.disabled = false;
  }
};

/* ---------------------------------- история --------------------------------- */

function drawHistory() {
  el.historyCount.textContent = String(history.length);
  if (!history.length) {
    el.historyList.innerHTML = `<div class="tr-history-empty">${esc(t("history.empty"))}</div>`;
    return;
  }
  el.historyList.innerHTML = history.map((row, index) => `
    <button type="button" class="tr-history-item" data-history="${index}">
      <b>${esc(languageName(row.from || "auto"))} → ${esc(languageName(row.to || ""))}${row.at ? " · " + esc(Memu.i18n.date(row.at, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })) : ""}</b>
      <span>${esc((row.source || "").replace(/\s+/g, " ").slice(0, 160))}</span>
      <small>${esc((row.result || "").replace(/\s+/g, " ").slice(0, 120))}</small>
    </button>`).join("");
}
el.historyList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-history]");
  if (!button) return;
  const row = history[Number(button.dataset.history)];
  if (!row) return;
  el.source.value = row.source || "";
  el.result.value = row.result || "";
  el.from.value = LANGS.some(([id]) => id === row.from) ? row.from : "auto";
  if (row.to && row.to !== "auto") el.to.value = row.to;
  syncLabels();
  counts();
  closeDrawers();
  setStatus(t("status.restored"), "ok");
  remember(true);
});

function pushHistory(sourceText, resultText) {
  if (!sourceText.trim() || !resultText.trim()) return;
  history.unshift({
    from: el.from.value, to: el.to.value,
    source: sourceText.slice(0, 4000), result: resultText.slice(0, 4000),
    at: Date.now(),
  });
  history = history.slice(0, 30);
  drawHistory();
}
drawHistory();

$("#history-clear").onclick = () => {
  history = [];
  drawHistory();
  remember(true);
  setStatus(t("status.history_cleared"));
};

/* --------------------------------- выдвижные -------------------------------- */

function openDrawer(node) {
  const open = node.hidden;
  closeDrawers();
  if (!open) return;
  node.hidden = false;
  el.app.dataset.drawer = "open";
  if (node === el.panel) el.settings.setAttribute("aria-pressed", "true");
  else el.historyToggle.setAttribute("aria-expanded", "true");
  const focusable = node.querySelector("select, textarea, input, button:not([data-close-drawer])");
  if (focusable) focusable.focus();
}
function closeDrawers() {
  el.panel.hidden = true;
  el.history.hidden = true;
  el.app.dataset.drawer = "";
  el.settings.setAttribute("aria-pressed", "false");
  el.historyToggle.setAttribute("aria-expanded", "false");
}
el.settings.onclick = () => openDrawer(el.panel);
el.historyToggle.onclick = () => openDrawer(el.history);
$$("[data-close-drawer]").forEach((node) => node.addEventListener("click", closeDrawers));

/* ---------------------------------- действия -------------------------------- */

$$("[data-mode]").forEach((button) => {
  button.onclick = () => {
    el.mode.value = button.dataset.mode;
    syncMode();
    remember(true);
  };
});
el.mode.addEventListener("change", syncMode);
el.from.addEventListener("change", syncLabels);
el.to.addEventListener("change", syncLabels);
el.source.addEventListener("input", counts);
el.result.addEventListener("input", counts);

function swapLanguages() {
  if (el.from.value === "auto") {
    setStatus(t("swap.need_source"), "error");
    return;
  }
  const from = el.from.value;
  el.from.value = el.to.value;
  el.to.value = from;
  const text = el.source.value;
  el.source.value = el.result.value;
  el.result.value = text;
  syncLabels();
  counts();
  remember(true);
}
el.swap.onclick = swapLanguages;

$("#clear").onclick = () => {
  el.source.value = "";
  el.result.value = "";
  images = [];
  documents = [];
  renderFiles();
  counts();
  remember(true);
  setStatus("");
  el.source.focus();
};

$("#copy").onclick = async () => {
  if (!el.result.value.trim()) return setStatus(t("status.nothing_copy"));
  try { await Memu.copy(el.result.value); setStatus(t("status.copied"), "ok"); }
  catch (error) { setStatus(error.message, "error"); }
};

function drawSpeakLabel() {
  el.speakLabel.textContent = playing ? t("speak.stop") : t("speak");
}

el.speak.onclick = async () => {
  if (playing) {
    playing.pause();
    playing = null;
    drawSpeakLabel();
    return setStatus(t("status.speech_stopped"));
  }
  const text = el.result.value.trim();
  if (!text) return setStatus(t("status.nothing_speak"));
  setStatus(t("status.preparing_speech"), "busy");
  el.speak.disabled = true;
  try {
    const speech = await Memu.speak({ text: text.slice(0, 4000) });
    if (!speech || !speech.audio) throw new Error(t("status.no_audio"));
    const audio = new Audio(speech.audio);
    playing = audio;
    drawSpeakLabel();
    audio.onended = () => {
      playing = null;
      drawSpeakLabel();
      setStatus(text.length > 4000 ? t("status.spoken_partial") : t("status.spoken"), "ok");
    };
    await audio.play();
    setStatus(t("status.playing"), "busy");
  } catch (error) {
    playing = null;
    drawSpeakLabel();
    setStatus(error.message || String(error), "error");
  } finally {
    el.speak.disabled = false;
  }
};

$("#use-as-source").onclick = () => {
  if (!el.result.value.trim()) return setStatus(t("status.empty_result"));
  const target = el.to.value;
  el.source.value = el.result.value;
  el.result.value = "";
  el.to.value = el.from.value !== "auto" ? el.from.value : (target === "ru" ? "en" : "ru");
  el.from.value = target;
  syncLabels();
  counts();
  setStatus(t("status.moved"), "ok");
  remember(true);
};

$("#save").onclick = async () => {
  if (!el.result.value.trim()) return setStatus(t("status.nothing_save"));
  try {
    const result = await Memu.download({
      name: t("file.name", { from: el.from.value, to: el.to.value }),
      text: `${el.source.value}\n\n---\n\n${el.result.value}`,
    });
    setStatus(t("status.saved", { name: (result && result.name) || t("status.saved_file") }), "ok");
  } catch (error) { setStatus(error.message, "error"); }
};

$("#reset-instructions").onclick = () => {
  el.instructions.value = defaultInstructions();
  setStatus(t("status.instructions_reset"), "ok");
  remember(true);
};

el.stop.onclick = () => {
  if (currentJob && currentJob.cancel) currentJob.cancel();
};

/* ----------------------------------- перевод -------------------------------- */

function setTranslating(active) {
  el.go.disabled = active;
  el.go.textContent = active ? t("translating") : t("translate");
  el.stop.hidden = !active;
  el.live.hidden = !active;
}

async function translate() {
  if (currentJob) return;
  const sourceText = el.source.value;
  if (!sourceText.trim() && !images.length && !documents.length) {
    el.source.focus();
    return setStatus(t("status.need_text"), "error");
  }
  if (!el.model.value) { openDrawer(el.panel); return setStatus(t("status.need_model"), "error"); }
  const from = el.from.value === "auto" ? t("lang.detect") : languageName(el.from.value);
  const to = languageName(el.to.value);

  setStatus(t("translating"), "busy");
  el.result.value = "";
  counts();
  setTranslating(true);
  const started = performance.now();
  let streamed = "";
  try {
    currentJob = Memu.stream({
      model: el.model.value,
      instructions: buildInstructions(from, to),
      text: t("rule.request", { to, text: sourceText }),
      images: images.map((item) => item.url),
      documents,
      keep_in_chat: el.keep.checked,
    }, {
      onDelta(chunk) {
        streamed += chunk;
        el.result.value = streamed;
        el.resultCount.textContent = number(streamed.length);
        el.result.scrollTop = el.result.scrollHeight;
      },
    });
    const result = await currentJob;
    const text = String((result && result.text) || streamed || "").trim();
    el.result.value = text;
    counts();
    const seconds = Memu.i18n.number((performance.now() - started) / 1000, { maximumFractionDigits: 1, minimumFractionDigits: 1 });
    if (!text) {
      setStatus(t("status.empty_translation"), "error");
    } else {
      pushHistory(sourceText, text);
      setStatus(t(el.keep.checked ? "status.done_kept" : "status.done", { seconds }), "ok");
      remember(true);
    }
  } catch (error) {
    const stopped = /останов|stopped/i.test(error.message || "");
    el.result.value = streamed;
    counts();
    setStatus(stopped ? (streamed ? t("status.stopped_partial") : t("status.stopped")) : (error.message || String(error)),
      stopped ? "" : "error");
  } finally {
    currentJob = null;
    setTranslating(false);
  }
}
el.go.onclick = translate;

/* ------------------------------------ язык ---------------------------------- */

// Разметку переводит мост; здесь — строки, которые модуль собирает сам.
function relabel() {
  const from = el.from.value, to = el.to.value;
  fillLanguages(el.from, from, true);
  fillLanguages(el.to, to, false);
  if (isDefaultInstruction(el.instructions.value)) el.instructions.value = defaultInstructions();
  syncLabels();
  counts();
  syncModelLabel();
  drawEmptyModelOption();
  renderFiles();
  drawHistory();
  drawRecordLabel();
  drawSpeakLabel();
  setTranslating(!!currentJob);
}
Memu.onLanguage(relabel);

/* ----------------------------------- клавиши -------------------------------- */

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    translate();
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.code === "KeyS") {
    event.preventDefault();
    swapLanguages();
    return;
  }
  if (event.key === "Escape") {
    if (el.app.dataset.drawer === "open") { event.preventDefault(); closeDrawers(); }
    else if (currentJob) { event.preventDefault(); el.stop.click(); }
  }
});

// Round 38: «переведи …» from the MemuAI chat / Quick Access (contributes.commands → run.open) — text and target language.
Memu.on("navigate", (data) => {
  const args = (data && data.command === "translate" && data.args) || {};
  if (!args.text) return;
  const to = String(args.to || "").toLowerCase();
  if (LANGS.some(([code]) => code === to && code !== "auto")) el.to.value = to;
  el.source.value = String(args.text);
  syncLabels(); counts();
  if (el.model.value) translate(); else el.source.focus();
});

setTranslating(false);
// Round 30: one header — in the MemuAI window the name, status and buttons of this bar live in the window's title row.
if (Memu.toolbar && Memu.toolbar.adopt) Memu.toolbar.adopt({ buttons: [{ id: "settings", el: "#settings", icon: "sliders" }] });
if (typeof Memu.ready === "function") Memu.ready();
