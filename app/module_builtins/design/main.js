

const t = (key, vars) => Memu.t(key, vars);
const saved = Memu.saved || {};
const defaults = (Memu.manifest && Memu.manifest.bridge) || {};
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const DEVICES = { desktop: 1280, tablet: 834, phone: 390 };
const MAX_IMAGES = 8;
const MAX_VERSIONS = 12;

const HISTORY_BUDGET = 1600000;

const IMAGE_SHRINK_BYTES = 1500000;
const IMAGE_MAX_SIDE = 1600;
const LIVE_EVERY_MS = 700;
const PALETTES = [
  ["", null],
  ["monochrome", ["#111111", "#555555", "#bbbbbb", "#ffffff"]],
  ["earth", ["#5b4636", "#a67c52", "#d9c3a0", "#f5ecdf"]],
  ["ocean", ["#0b3c5d", "#1d7ea8", "#5cc8c0", "#f2e6c9"]],
  ["forest", ["#1f3d2b", "#3f7d4e", "#a8c69f", "#f4f1e6"]],
  ["berry", ["#4a1942", "#a4306a", "#e3799b", "#fbe3ea"]],
  ["neon", ["#0b0b14", "#1c1c33", "#39ff88", "#ff3df2"]],
  ["pastel", ["#cdb4db", "#ffc8dd", "#bde0fe", "#fdfcdc"]],
  ["corporate", ["#0f2a4a", "#2563eb", "#94a3b8", "#f1f5f9"]],
];
const EXAMPLES = ["cafe", "dashboard", "portfolio"];

function shippedInstructions() {
  return Memu.i18n.locales().map((lang) => Memu.i18n.get("instructions.default", lang)).filter(Boolean);
}
function isDefaultInstruction(text) {
  const value = String(text || "").trim();
  return !value || shippedInstructions().some((item) => item.trim() === value);
}
function baseInstructions() {
  return isDefaultInstruction(defaults.instructions) ? t("instructions.default") : defaults.instructions;
}

const el = {
  app: $("#app"),
  model: $("#model"),
  brief: $("#brief"),
  kind: $("#kind"),
  refine: $("#refine"),
  refineBox: $("#refine-box"),
  system: $("#system"),
  palette: $("#palette"),
  swatches: $("#palette-swatches"),
  mode: $("#mode"),
  font: $("#font"),
  instructions: $("#instructions"),
  code: $("#code"),
  codePatch: $("#code-patch"),
  codeSize: $("#code-size"),
  status: $("#design-status"),
  preview: $("#preview"),
  previewB: $("#preview-b"),
  stage: $("#frame-wrap"),
  box: $("#device-box"),
  device: $("#device"),
  chromeTitle: $("#chrome-title"),
  empty: $("#ds-empty"),
  imageList: $("#image-list"),
  imageCount: $("#image-count"),
  imageClear: $("#image-clear"),
  deviceSize: $("#device-size"),
  handoffNote: $("#handoff-note"),
  handoffResult: $("#handoff-result"),
  previewTitle: $("#preview-title"),
  make: $("#make"),
  makeLabel: $("#make-label"),
  stop: $("#stop"),
  outline: $("#preview-devtools"),
  focus: $("#preview-focus"),
  versionBack: $("#version-back"),
  side: $("#side"),
  drop: $("#drop"),
  undo: $("#undo"),
  redo: $("#redo"),
  historyToggle: $("#history-toggle"),
  historyPop: $("#history-pop"),
  historyList: $("#history-list"),
  historyLabel: $("#history-label"),
  historyNote: $("#history-note"),
  live: $("#live-badge"),
  pill: $("#live-pill"),
  pillStage: $("#live-stage"),
  pillNote: $("#live-note"),
};

let images = [];
let code = String(saved.code || "");
let lastPrompt = String(saved.last_prompt || "");

let history = [];
let cursor = -1;

let heavyRev = 0;
let savedRev = 0;
const touchHeavy = () => { heavyRev += 1; };
let device = DEVICES[saved.device] ? saved.device : "desktop";
let job = null;
let modelState = "loading";
let emptyView = null;
let handoffState = null;
let cancelled = false;
let liveShown = false;

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function setStatus(text, kind) {

  if (text) el.status.removeAttribute("data-i18n");
  else el.status.setAttribute("data-i18n", "status.idle");
  el.status.textContent = text || t("status.idle");
  el.status.className = "ds-status" + (kind ? " " + kind : "");
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error(t("read_error")));
    reader.readAsDataURL(file);
  });
}

function shrinkImage(url) {
  return new Promise((resolve) => {
    if (typeof Image !== "function" || typeof document.createElement !== "function") return resolve(url);
    const image = new Image();
    image.onload = () => {
      try {
        const side = Math.max(image.naturalWidth, image.naturalHeight);
        if (!side || side <= IMAGE_MAX_SIDE) return resolve(url);
        const scale = IMAGE_MAX_SIDE / side;
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        let out = canvas.toDataURL("image/webp", 0.9);
        if (!/^data:image\/webp/.test(out)) out = canvas.toDataURL("image/jpeg", 0.9);
        resolve(out.length < url.length ? out : url);
      } catch (error) { resolve(url); }
    };
    image.onerror = () => resolve(url);
    image.src = url;
  });
}

async function prepareImage(file) {
  const url = await readFile(file);
  if (file.size > IMAGE_SHRINK_BYTES && !/gif|svg/.test(file.type)) return shrinkImage(url);
  return url;
}

function optionText(select) {
  const option = select.selectedOptions && select.selectedOptions[0];
  return option ? option.textContent : select.value;
}

function formatSize(length) {
  if (!length) return "";
  return length > 9999
    ? t("size.thousands", { count: Memu.i18n.number(length / 1000, { maximumFractionDigits: 1 }) })
    : t("size.chars", { count: length });
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function historyEntry(value) {
  if (typeof value === "string") return value ? { code: value, at: 0, kind: "legacy", label: "" } : null;
  if (!value || typeof value !== "object" || typeof value.code !== "string" || !value.code) return null;
  return {
    code: value.code,
    at: Number.isFinite(Number(value.at)) ? Number(value.at) : 0,
    kind: ["new", "refine", "patch", "manual", "legacy"].includes(value.kind) ? value.kind : "legacy",
    label: typeof value.label === "string" ? value.label.slice(0, 160) : "",
    edited: !!value.edited,
  };
}

function loadHistory(rawVersions, current, rawCursor) {
  let rows = (Array.isArray(rawVersions) ? rawVersions : []).map(historyEntry).filter(Boolean);
  const legacy = Array.isArray(rawVersions) && rawVersions.some((item) => typeof item === "string");
  let index = Number.isInteger(rawCursor) ? rawCursor : rows.length - 1;
  if (legacy || !rows.length || index < 0 || index >= rows.length || rows[index].code !== current) {
    if (current && (!rows.length || rows[rows.length - 1].code !== current)) rows.push({ code: current, at: 0, kind: "legacy", label: "" });
    index = current ? rows.length - 1 : rows.length - 1;
  }
  const trimmed = trimHistory(rows, index);
  return trimmed;
}

function trimHistory(rows, index) {
  let list = rows.slice();
  let at = index;
  const drop = () => {
    const victim = at === 0 ? 1 : 0;
    if (victim >= list.length) return false;
    list.splice(victim, 1);
    if (victim < at) at -= 1;
    return true;
  };
  while (list.length > MAX_VERSIONS && drop()) {  }
  while (list.length > 1 && JSON.stringify(list).length > HISTORY_BUDGET && drop()) {  }
  return { rows: list, cursor: list.length ? Math.max(0, Math.min(at, list.length - 1)) : -1 };
}

function commitVersion(html, kind, label) {
  if (cursor >= 0 && history[cursor] && history[cursor].code === html) return;
  const next = trimHistory([...history, { code: html, at: Date.now(), kind, label: String(label || "").slice(0, 160) }], history.length);
  history = next.rows;
  cursor = next.cursor;
  touchHeavy();
}

function goToVersion(index, statusKey) {
  if (job || index < 0 || index >= history.length || index === cursor) return;
  cursor = index;
  touchHeavy();
  code = history[cursor].code;
  el.code.value = code;
  updateCodeSize();
  renderPreview(code);
  drawHistory();
  setStatus(t(statusKey || "history.restored", { n: cursor + 1, total: history.length }), "ok");
  remember(true);
}

function versionTitle(row) {
  return t("history.kind." + (row.kind || "legacy"));
}

function versionTime(row) {
  if (!row.at) return "";
  try { return Memu.i18n.date(row.at, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); }
  catch (error) { return ""; }
}

function drawHistory() {
  const count = history.length;
  el.undo.disabled = !!job || cursor <= 0;
  el.redo.disabled = !!job || cursor < 0 || cursor >= count - 1;
  el.historyToggle.disabled = !count;
  el.historyLabel.textContent = count ? t("history.position", { n: cursor + 1, total: count }) : t("history.title");
  el.versionBack.hidden = cursor <= 0;
  el.historyNote.textContent = count ? t("history.note", { max: MAX_VERSIONS }) : "";
  el.historyList.innerHTML = history.map((row, index) => `
    <li><button type="button" role="menuitemradio" class="ds-history-item" data-version="${index}" aria-checked="${index === cursor}">
      <span class="ds-history-num">${index + 1}</span>
      <span class="ds-history-text"><b>${esc(versionTitle(row))}${row.edited ? ` · ${esc(t("history.edited"))}` : ""}</b>
        ${row.label ? `<small>${esc(row.label)}</small>` : ""}</span>
      <span class="ds-history-meta">${esc(versionTime(row))}<small>${esc(formatSize(row.code.length))}</small></span>
    </button></li>`).reverse().join("");
}

function toggleHistory(force) {
  const open = force === undefined ? el.historyPop.hidden : !!force;
  if (open && !history.length) return;
  el.historyPop.hidden = !open;
  el.historyToggle.setAttribute("aria-expanded", String(open));
  if (open) {
    drawHistory();
    const current = el.historyList.querySelector('[aria-checked="true"]');
    if (current && current.focus) current.focus();
  }
}

{
  const loaded = loadHistory(saved.versions, code, saved.version_cursor);
  history = loaded.rows;
  cursor = loaded.cursor;
  if (cursor >= 0) code = history[cursor].code;
}

el.brief.value = saved.brief || "";
el.refine.value = saved.refine || "";
el.instructions.value = saved.instructions && !isDefaultInstruction(saved.instructions)
  ? saved.instructions : baseInstructions();
el.handoffNote.value = saved.handoff_note || "";
for (const [key, node] of [["kind", el.kind], ["system", el.system], ["palette", el.palette],
  ["mode", el.mode], ["font", el.font]]) {
  if (saved[key] != null && Array.from(node.options).some((option) => option.value === saved[key])) {
    node.value = saved[key];
  }
}
if (saved.sections && typeof saved.sections === "object") {
  $$("[data-section]").forEach((box) => {
    if (typeof saved.sections[box.dataset.section] === "boolean") box.checked = saved.sections[box.dataset.section];
  });
}
["a11y", "motion", "responsive", "content"].forEach((id) => {
  const box = $("#" + id);
  if (box && saved[id] !== undefined) box.checked = !!saved[id];
});

let saveTimer = null;
function snapshot() {
  const heavy = heavyRev !== savedRev;
  const row = {
    brief: el.brief.value,
    refine: el.refine.value,

    instructions: isDefaultInstruction(el.instructions.value) ? "" : el.instructions.value,
    handoff_note: el.handoffNote.value,
    ...(heavy ? { code, versions: history, version_cursor: cursor } : {}),
    last_prompt: lastPrompt,
    model: el.model.value || saved.model || "",
    kind: el.kind.value,
    system: el.system.value,
    palette: el.palette.value,
    mode: el.mode.value,
    font: el.font.value,
    device,
    a11y: $("#a11y").checked,
    motion: $("#motion").checked,
    responsive: $("#responsive").checked,
    content: $("#content").checked,
    sections: Object.fromEntries($$("[data-section]").map((box) => [box.dataset.section, box.checked])),
    custom_sections: customSections.slice(0, 20),
  };
  return { row, rev: heavyRev };
}
function remember(immediate) {
  clearTimeout(saveTimer);
  const run = () => {
    const { row, rev } = snapshot();
    return Memu.save(row).then(() => { if (rev > savedRev) savedRev = rev; })
      .catch((error) => setStatus(error.message, "error"));
  };
  if (immediate) run();
  else saveTimer = setTimeout(run, 600);
}
document.body.addEventListener("input", () => remember(false));
document.body.addEventListener("change", () => remember(false));

window.addEventListener("pagehide", () => remember(true));
window.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") remember(true);
});

function drawEmptyModelOption() {
  if (modelState === "ready") return;
  const key = modelState === "none" ? "model.none" : modelState === "error" ? "model.unavailable" : "model.loading";
  el.model.innerHTML = `<option value="">${esc(t(key))}</option>`;
}

Memu.models().then((rows) => {
  const list = Array.isArray(rows) ? rows : [];

  const caps = (item) => item.caps || {};
  const chat = list.filter((item) => !item.image && !item.video && !item.audio && !item.music
    && !caps(item).image && !caps(item).video && !caps(item).audio && !caps(item).music && caps(item).text !== false);
  const usable = chat.length ? chat : list;
  modelState = usable.length ? "ready" : "none";
  if (usable.length) {
    el.model.innerHTML = usable.map((item) => `<option value="${esc(item.id)}">${esc(item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id)}</option>`).join("");
  } else drawEmptyModelOption();

  const wanted = [saved.model, defaults.model, (usable.find((item) => item.default) || {}).id].find((id) => id && usable.some((item) => item.id === id));
  if (wanted) el.model.value = wanted;
  if (!usable.length) setStatus(t("model.connect"), "error");
}).catch((error) => {
  modelState = "error";
  drawEmptyModelOption();
  setStatus(error.message, "error");
});

function renderSwatches() {
  el.swatches.innerHTML = PALETTES.map(([value, colors]) => `
    <button type="button" class="ds-swatch" role="radio" data-palette="${esc(value)}"
            aria-checked="${el.palette.value === value}" title="${esc(t(`palette.${value || "auto"}.full`))}">
      <span class="ds-swatch-bar ${colors ? "" : "auto"}">${(colors || []).map((color) => `<i style="background:${color}"></i>`).join("")}</span>
      <span>${esc(t(`palette.${value || "auto"}`))}</span>
    </button>`).join("");
}
el.swatches.addEventListener("click", (event) => {
  const button = event.target.closest("[data-palette]");
  if (!button) return;
  el.palette.value = button.dataset.palette;
  renderSwatches();
  remember(false);
});
renderSwatches();

function renderImages() {
  el.imageList.innerHTML = images.map((url, index) => `
    <div class="ds-thumb"><img alt="${esc(t("images.alt", { n: index + 1 }))}" src="${esc(url)}">
      <button type="button" data-remove-image="${index}" aria-label="${esc(t("images.remove", { n: index + 1 }))}" title="${esc(t("remove"))}">
        <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg>
      </button></div>`).join("");
  el.imageCount.textContent = images.length
    ? t("images.count", { count: images.length, max: MAX_IMAGES })
    : t("images.empty_hint", { max: MAX_IMAGES });
  el.imageClear.hidden = !images.length;
}
el.imageList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-image]");
  if (!button) return;
  images.splice(Number(button.dataset.removeImage), 1);
  renderImages();
});
$("#add-image").onclick = () => $("#images").click();
el.imageClear.onclick = () => {
  images = [];
  renderImages();
  setStatus(t("images.removed"));
};
async function addImageFiles(list) {
  const files = Array.from(list || []).filter((file) => file && /^image\//.test(file.type));
  if (!files.length) return false;
  const room = MAX_IMAGES - images.length;
  if (!room) { setStatus(t("images.limit", { max: MAX_IMAGES }), "error"); return true; }
  try {
    for (const file of files.slice(0, room)) images.push(await prepareImage(file));
    renderImages();
    if (files.length > room) setStatus(t("images.partial", { room, max: MAX_IMAGES }), "error");
    else setStatus(t("images.added", { count: Math.min(files.length, room) }), "ok");
  } catch (error) { setStatus(error.message, "error"); }
  return true;
}
$("#images").onchange = async (event) => {
  const files = Array.from(event.target.files || []);
  event.target.value = "";
  await addImageFiles(files);
};

document.addEventListener("paste", (event) => {
  const data = event.clipboardData;
  const files = data ? Array.from(data.files || []).filter((file) => /^image\//.test(file.type)) : [];
  if (!files.length || (data.getData && data.getData("text/plain"))) return;
  event.preventDefault();
  addImageFiles(files);
});

const DROP_IDLE_MS = 1200;
const hasFiles = (event) => !!(event.dataTransfer && Array.from(event.dataTransfer.types || []).includes("Files"));
let dragDepth = 0;
let dropIdle = 0;
function showDrop(on) {
  clearTimeout(dropIdle);
  if (!on) dragDepth = 0;
  el.drop.hidden = !on;
}
function armDrop() { clearTimeout(dropIdle); dropIdle = setTimeout(() => showDrop(false), DROP_IDLE_MS); }
function hideDrop() { if (el.drop.hidden) return false; showDrop(false); return true; }
el.side.addEventListener("dragenter", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  dragDepth += 1;
  showDrop(true);
  armDrop();
});
el.side.addEventListener("dragover", (event) => {
  if (!hasFiles(event)) return;
  event.preventDefault();
  try { event.dataTransfer.dropEffect = "copy"; } catch (error) {  }
  if (el.drop.hidden) showDrop(true);
  armDrop();
});
el.side.addEventListener("dragleave", (event) => {
  if (el.drop.hidden) return;
  const to = event.relatedTarget;
  if (to && el.side.contains(to)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!to || !dragDepth) showDrop(false);
});
el.side.addEventListener("drop", (event) => {
  showDrop(false);
  if (!hasFiles(event)) return;
  event.preventDefault();
  event.stopPropagation();
  addImageFiles(event.dataTransfer.files);
});

document.addEventListener("dragover", (event) => {
  if (!hasFiles(event) || event.defaultPrevented) return;
  event.preventDefault();
  try { event.dataTransfer.dropEffect = "none"; } catch (error) {  }
});
document.addEventListener("drop", (event) => { if (hasFiles(event)) event.preventDefault(); hideDrop(); });
document.addEventListener("dragend", hideDrop, true);
document.addEventListener("dragleave", (event) => { if (!event.relatedTarget) hideDrop(); });
window.addEventListener("blur", hideDrop);
document.addEventListener("visibilitychange", () => { if (document.hidden) hideDrop(); });
["mousemove", "pointerdown", "wheel"].forEach((name) => document.addEventListener(name, hideDrop, { passive: true }));
renderImages();

let customSections = Array.isArray(saved.custom_sections)
  ? saved.custom_sections.filter((row) => row && typeof row.text === "string" && row.text.trim()).slice(0, 20)
  : [];

function renderCustomSections() {
  const host = $("#custom-sections");
  if (!host) return;
  host.innerHTML = customSections.map((row, index) => `
    <label class="ds-chip custom" title="${esc(row.text)}">
      <input type="checkbox" data-custom-section="${index}" ${row.on !== false ? "checked" : ""}>
      <span>${esc(row.text.length > 40 ? row.text.slice(0, 39) + "…" : row.text)}
        <button type="button" class="ds-chip-remove" data-remove-section="${index}" aria-label="${esc(t("section.remove", { name: row.text }))}" title="${esc(t("remove"))}">
          <svg viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></svg></button></span>
    </label>`).join("");
}

const sectionHost = $("#custom-sections");
if (sectionHost) {
  sectionHost.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-section]");
    if (!remove) return;
    event.preventDefault();
    customSections.splice(Number(remove.dataset.removeSection), 1);
    renderCustomSections();
    remember(false);
  });
  sectionHost.addEventListener("change", (event) => {
    const box = event.target.closest("[data-custom-section]");
    if (!box) return;
    const row = customSections[Number(box.dataset.customSection)];
    if (row) row.on = box.checked;
  });
}

function addCustomSection() {
  const input = $("#section-custom");
  const text = input.value.trim().replace(/\s+/g, " ");
  if (!text) return input.focus();
  if (customSections.some((row) => row.text.toLowerCase() === text.toLowerCase())) {
    setStatus(t("section.exists"), "error");
    return;
  }
  if (customSections.length >= 20) return setStatus(t("section.limit"), "error");
  customSections.push({ text, on: true });
  input.value = "";
  renderCustomSections();
  remember(false);
  input.focus();
}
if ($("#section-add")) $("#section-add").onclick = addCustomSection;
if ($("#section-custom")) {
  $("#section-custom").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) { event.preventDefault(); addCustomSection(); }
  });
}

function selectedSections() {
  const builtin = $$("[data-section]").filter((box) => box.checked)
    .map((box) => t("prompt.section." + box.dataset.section));
  const custom = customSections.filter((row) => row.on !== false).map((row) => row.text);
  return [...builtin, ...custom];
}

function buildPrompt(task) {
  const parts = [t("prompt.build", { kind: optionText(el.kind).toLowerCase() }), task];
  const sections = selectedSections();
  if (sections.length) parts.push(t("prompt.sections", { list: sections.join("; ") }));
  if (el.system.value !== "auto") parts.push(t("prompt.system", { value: optionText(el.system) }));
  if (el.palette.value) parts.push(t("prompt.palette", { value: optionText(el.palette) }));
  if (el.mode.value !== "auto") parts.push(t("prompt.mode", { value: optionText(el.mode) }));
  if (el.font.value !== "auto") parts.push(t("prompt.font", { value: optionText(el.font) }));
  if ($("#a11y").checked) parts.push(t("prompt.a11y"));
  if ($("#responsive").checked) parts.push(t("prompt.responsive"));
  if ($("#motion").checked) parts.push(t("prompt.motion"));
  if ($("#content").checked) parts.push(t("prompt.content"));
  if (images.length) parts.push(t("prompt.images", { count: images.length }));
  return parts.join("\n");
}

function extractHtml(text) {
  const source = String(text || "");
  const fenced = source.match(/```(?:html)?\s*([\s\S]*?)```/i);
  let body = fenced ? fenced[1] : source;
  let start = body.search(/<!doctype\s+html/i);
  if (start < 0) start = body.search(/<html[\s>]/i);
  if (start >= 0) {
    const end = body.search(/<\/html\s*>/i);
    body = end >= 0 ? body.slice(start, end + 7) : body.slice(start);
  }
  return body.trim();
}

function documentTitle(html) {
  const match = String(html || "").match(/<title[^>]*>([^<]{1,120})<\/title>/i);
  return match ? match[1].trim() : "";
}

const OUTLINE_CSS = "<style data-memu-outline>*{outline:1px dashed rgba(234,88,12,.55)!important;outline-offset:-1px}</style>";
const SPARK = '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/>';
const ALERT = '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>';

const LINK_GUARD = "<script>document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[href]');" +
  "if(a&&!/^#/.test(a.getAttribute('href')||'')){e.preventDefault();}},true);" +
  "document.addEventListener('submit',function(e){e.preventDefault();},true);<\/script>";

function decorate(html) {
  let page = String(html || "");
  page = /<\/body\s*>/i.test(page) ? page.replace(/<\/body\s*>/i, LINK_GUARD + "</body>") : page + LINK_GUARD;
  if (el.outline.getAttribute("aria-pressed") !== "true") return page;
  if (/<\/head\s*>/i.test(page)) return page.replace(/<\/head\s*>/i, OUTLINE_CSS + "</head>");
  return OUTLINE_CSS + page;
}

function examplesHtml() {
  return `<div class="ds-examples" id="examples">${EXAMPLES.map((id) =>
    `<button type="button" class="ds-example" data-example="${id}">${esc(t("example." + id))}</button>`).join("")}</div>`;
}

function drawEmpty() {
  const card = $("#ds-empty-card");
  const view = emptyView || { kind: "idle" };
  card.className = "ds-empty-card" + (view.kind === "error" ? " error" : "");
  if (view.kind === "working") {
    card.innerHTML = `<span class="ds-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${SPARK}</svg></span>
      <b>${esc(t(view.title))}</b><p>${esc(view.images ? t("working.images", { count: view.images }) : t("working.plain"))}</p>
      <div class="ds-progress"><div class="ds-progress-bar"><i></i></div>${stagesHtml(view.stage || "thinking")}<small id="progress-note">${esc(t("working.waiting"))}</small></div>`;
  } else if (view.kind === "error") {
    card.innerHTML = `<span class="ds-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${ALERT}</svg></span>
      <b>${esc(t("status.failed"))}</b><p>${esc(view.message)}</p>`;
  } else {
    card.innerHTML = `<span class="ds-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24">${SPARK}</svg></span>
      <b>${esc(t("empty.title"))}</b><p>${esc(t("empty.text"))}</p>${examplesHtml()}`;
  }
  el.empty.hidden = false;
  el.stage.dataset.empty = "1";
}

let frontFrame = 0;
let showToken = 0;
function frames() { return [el.preview, el.previewB].filter(Boolean); }
function showDocument(page) {
  const list = frames();
  const token = ++showToken;
  if (list.length < 2 || !page) {
    list.forEach((frame) => { frame.onload = null; frame.srcdoc = list.length < 2 ? page : ""; });
    if (list.length === 2) { list[0].classList.add("front"); list[1].classList.remove("front"); frontFrame = 0; }
    return;
  }
  const back = list[1 - frontFrame];
  back.onload = () => {
    back.onload = null;
    if (token !== showToken) return;
    back.classList.add("front");
    back.removeAttribute("aria-hidden");
    back.removeAttribute("tabindex");
    const front = list[frontFrame];
    front.classList.remove("front");
    front.setAttribute("aria-hidden", "true");
    front.setAttribute("tabindex", "-1");
    frontFrame = 1 - frontFrame;
  };
  back.srcdoc = page;
}
function shownDocument() {
  const list = frames();
  return (list[frontFrame] || list[0] || {}).srcdoc || "";
}

function renderPreview(html) {
  if (!html) {
    emptyView = null;
    drawEmpty();
    showDocument("");
  } else {
    el.empty.hidden = true;
    el.stage.dataset.empty = "";
    showDocument(decorate(html));
  }
  liveShown = false;
  el.live.hidden = true;
  drawPreviewTitle(html);
  el.refineBox.hidden = !html;
  drawHistory();
  fitDevice();
}

function drawPreviewTitle(html) {
  const title = documentTitle(html);
  el.chromeTitle.textContent = title || "design.html";
  el.previewTitle.textContent = html ? (title || t("preview.design")) : t("preview.title");
}

function updateCodeSize() {
  el.codeSize.textContent = formatSize(code.length);
}

function fitDevice() {
  const width = DEVICES[device] || 1280;
  if (!el.stage.clientWidth) {

    el.deviceSize.textContent = `${width} px`;
    return;
  }
  const style = getComputedStyle(el.stage);
  const padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  const availW = Math.max(120, el.stage.clientWidth - padX);
  const availH = Math.max(240, el.stage.clientHeight - padY);
  const scale = Math.min(1, availW / width);
  el.box.style.width = Math.floor(width * scale) + "px";
  el.box.style.height = availH + "px";
  el.device.style.width = width + "px";
  el.device.style.height = Math.floor(availH / scale) + "px";
  el.device.style.transform = scale < 1 ? `scale(${scale})` : "";
  el.deviceSize.textContent = scale < 0.995 ? `${width} px · ${Math.round(scale * 100)}%` : `${width} px`;
  el.deviceSize.title = scale < 0.995 ? t("device.scaled") : t("device.width");
}

function selectDevice(name) {
  device = DEVICES[name] ? name : "desktop";
  el.stage.dataset.device = device;
  $$("[data-device]").forEach((button) => button.setAttribute("aria-checked", String(button.dataset.device === device)));
  fitDevice();
}
$$("[data-device]").forEach((button) => {
  button.onclick = () => { selectDevice(button.dataset.device); remember(false); };
});
if (window.ResizeObserver) new ResizeObserver(() => fitDevice()).observe(el.stage);
else window.addEventListener("resize", fitDevice);

function selectStep(name) {
  $$("[data-step]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.step === name)));
  $$("[data-step-panel]").forEach((panel) => { panel.hidden = panel.dataset.stepPanel !== name; });
}
$$("[data-step]").forEach((button) => { button.onclick = () => selectStep(button.dataset.step); });

function selectView(name) {
  el.app.dataset.view = name === "preview" ? "preview" : "setup";
  $$("[data-view-btn]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.viewBtn === el.app.dataset.view)));
  requestAnimationFrame(fitDevice);
}
$$("[data-view-btn]").forEach((button) => { button.onclick = () => selectView(button.dataset.viewBtn); });

function toggleFocus(force) {
  const on = force === undefined ? el.app.dataset.focus !== "1" : !!force;
  el.app.dataset.focus = on ? "1" : "";
  el.focus.setAttribute("aria-pressed", String(on));
  requestAnimationFrame(fitDevice);
}
el.focus.onclick = () => toggleFocus();

function setBusy(busy) {
  el.make.hidden = busy;
  el.stop.hidden = !busy;
  $("#refine-go").disabled = busy;
  $("#patch-go").disabled = busy;
  el.pill.hidden = !busy;
  el.app.dataset.busy = busy ? "1" : "";
  drawHistory();
}

const STAGES = ["thinking", "head", "styles", "markup", "scripts", "finishing"];
function stageOf(text) {
  const source = String(text || "");
  if (!source.trim()) return "thinking";
  if (/<\/html\s*>/i.test(source)) return "finishing";
  const body = source.search(/<body[\s>]/i);
  if (body >= 0) return /<script[\s>]/i.test(source.slice(body)) ? "scripts" : "markup";
  if (/<style[\s>]/i.test(source)) return "styles";
  return "head";
}

function livePage(text) {
  let html = extractHtml(text);
  const body = html.search(/<body[\s>]/i);
  if (body < 0 || html.length - body < 80) return "";

  const openScript = html.toLowerCase().lastIndexOf("<script");
  if (openScript > html.toLowerCase().lastIndexOf("</script")) html = html.slice(0, openScript);
  const lastOpen = html.lastIndexOf("<");
  if (lastOpen > html.lastIndexOf(">")) html = html.slice(0, lastOpen);
  return html;
}

function stagesHtml(current) {
  const at = STAGES.indexOf(current);
  return `<ol class="ds-stages">${STAGES.slice(1).map((name, index) => {
    const state = index + 1 < at ? "done" : index + 1 === at ? "now" : "";
    return `<li class="${state}"><i aria-hidden="true"></i>${esc(t("stage." + name))}</li>`;
  }).join("")}</ol>`;
}

async function generate(kind) {
  if (job) return;
  const task = kind === "new" ? el.brief.value.trim()
    : kind === "refine" ? el.refine.value.trim() : el.codePatch.value.trim();
  if (!task) {
    if (kind === "new") { selectStep("brief"); el.brief.focus(); }
    return setStatus(t(kind === "new" ? "status.need_brief" : "status.need_change"), "error");
  }
  if (kind !== "new" && !code) return setStatus(t("status.need_design"), "error");
  if (!el.model.value) return setStatus(t("status.need_model"), "error");

  const prompt = buildPrompt(task);
  if (kind === "new") lastPrompt = prompt;
  const text = kind === "new" ? prompt : `${prompt}\n\n${t("prompt.edit")}`;
  const base = code;
  cancelled = false;
  toggleHistory(false);

  if (!base) {
    emptyView = { kind: "working", title: "working.new", images: images.length, stage: "thinking" };
    drawEmpty();
  }
  if (el.app.dataset.view !== "preview" && window.matchMedia && window.matchMedia("(max-width: 820px)").matches) selectView("preview");
  setStatus(t(kind === "new" ? "status.building" : "status.refining"), "busy");
  setBusy(true);

  const started = Date.now();
  let received = 0;
  let streamed = "";
  let stage = "thinking";
  let lastLive = 0;
  let liveTimer = null;
  const note = () => {
    const seconds = Math.round((Date.now() - started) / 1000);
    const line = received
      ? t("working.received", { size: formatSize(received), seconds })
      : t("working.waiting_time", { seconds });
    el.pillStage.textContent = t("stage." + stage);
    el.pillNote.textContent = line;
    const node = $("#progress-note");
    if (node) node.textContent = line;
  };
  const showLive = () => {
    liveTimer = null;
    if (!job) return;
    const page = livePage(streamed);
    if (!page) return;
    lastLive = Date.now();
    liveShown = true;
    el.empty.hidden = true;
    el.stage.dataset.empty = "";
    el.live.hidden = false;
    showDocument(decorate(page));
    drawPreviewTitle(page);
  };
  const ticker = setInterval(note, 1000);
  note();
  try {
    job = Memu.stream({
      model: el.model.value,
      instructions: el.instructions.value.trim() || baseInstructions(),
      text,
      images,
      documents: kind === "new" ? [] : [{ name: "current-design.html", text: base }],
      thinking: !!defaults.thinking,
      keep_in_chat: false,
    }, {
      onDelta(chunk) {
        streamed += chunk;
        received = streamed.length;
        const next = stageOf(streamed);
        if (next !== stage) {
          stage = next;
          if (emptyView && emptyView.kind === "working") {
            emptyView.stage = stage;
            const list = $(".ds-stages");
            if (list) list.outerHTML = stagesHtml(stage);
          }
        }
        note();

        if (!liveTimer) liveTimer = setTimeout(showLive, Math.max(0, LIVE_EVERY_MS - (Date.now() - lastLive)));
      },
    });
    drawHistory();
    const result = await job;
    clearTimeout(liveTimer);
    const html = extractHtml((result && result.text) || streamed);
    if (!html || html.length < 60 || !/<[a-z]/i.test(html)) throw new Error(t("status.too_short"));
    const label = task.replace(/\s+/g, " ");
    commitVersion(html, kind, label);
    code = html;
    el.code.value = code;
    updateCodeSize();
    renderPreview(code);
    if (kind === "refine") el.refine.value = "";
    if (kind === "patch") el.codePatch.value = "";
    const seconds = Math.round((Date.now() - started) / 1000);

    if (/<html[\s>]|<!doctype/i.test(html) && !/<\/html\s*>/i.test(html)) setStatus(t("status.truncated", { seconds }), "warn");
    else setStatus(t(kind === "new" ? "status.done_new" : "status.done_refine", { seconds }), "ok");
    remember(true);
  } catch (error) {
    clearTimeout(liveTimer);
    const message = (error && error.message) || String(error);
    const stopped = cancelled || /останов|stopped|cancel/i.test(message);
    if (base) renderPreview(base);
    else if (stopped) renderPreview("");
    else {

      showDocument("");
      liveShown = false;
      el.live.hidden = true;
      drawPreviewTitle("");
      emptyView = { kind: "error", message };
      drawEmpty();
    }
    setStatus(stopped ? t("status.stopped") : message, stopped ? "" : "error");
  } finally {
    clearInterval(ticker);
    clearTimeout(liveTimer);
    job = null;
    cancelled = false;
    setBusy(false);
  }
}

function stopGeneration() {
  if (!job) return;
  cancelled = true;
  setStatus(t("status.stopping"), "busy");
  if (job.cancel) job.cancel();
}

el.make.onclick = () => generate("new");
$("#refine-go").onclick = () => generate("refine");
$("#patch-go").onclick = () => generate("patch");
el.stop.onclick = stopGeneration;
$("#live-stop").onclick = stopGeneration;
el.stage.addEventListener("click", (event) => {
  const example = event.target.closest("[data-example]");
  if (!example) return;
  el.brief.value = t(`example.${example.dataset.example}.text`);
  selectStep("brief");
  if (window.matchMedia("(max-width: 820px)").matches) selectView("setup");
  el.brief.focus();
  remember(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && hideDrop()) { event.preventDefault(); return; }
  const typing = event.target.closest && event.target.closest("input, textarea, select");
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    if (event.target === el.refine) generate("refine");
    else if (event.target === el.codePatch) generate("patch");
    else generate("new");
    return;
  }

  if (event.key === "Escape") {
    if (!el.historyPop.hidden) { event.preventDefault(); toggleHistory(false); el.historyToggle.focus(); return; }
    if (job) { event.preventDefault(); stopGeneration(); return; }
    if (el.app.dataset.focus === "1") { event.preventDefault(); toggleFocus(false); return; }
    return;
  }
  if ((event.ctrlKey || event.metaKey) && !event.altKey && event.code === "KeyS") {
    event.preventDefault();
    $("#code-save").click();
    return;
  }
  if (!typing && (event.ctrlKey || event.metaKey) && !event.altKey) {
    const redo = event.code === "KeyY" || (event.code === "KeyZ" && event.shiftKey);
    if (event.code === "KeyZ" || redo) {
      event.preventDefault();
      goToVersion(redo ? cursor + 1 : cursor - 1, redo ? "history.redone" : "history.undone");
      return;
    }
  }
  if (!typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (event.code === "KeyF") { event.preventDefault(); toggleFocus(); }
    else if (event.code === "Digit1") selectDevice("desktop");
    else if (event.code === "Digit2") selectDevice("tablet");
    else if (event.code === "Digit3") selectDevice("phone");
  }
});

const livePreview = debounce(() => renderPreview(code), 450);
el.code.addEventListener("input", () => {
  code = el.code.value;
  touchHeavy();

  if (cursor >= 0 && history[cursor]) {
    history[cursor].code = code;
    history[cursor].edited = true;
    if (!code) { history.splice(cursor, 1); cursor = Math.min(cursor, history.length - 1); }
  } else if (code) commitVersion(code, "manual", "");
  updateCodeSize();
  livePreview();
});
el.code.addEventListener("keydown", (event) => {
  if (event.key === "Tab" && !event.ctrlKey && !event.altKey) {
    event.preventDefault();
    el.code.setRangeText("  ", el.code.selectionStart, el.code.selectionEnd, "end");
    el.code.dispatchEvent(new Event("input", { bubbles: true }));
  }
});

$("#preview-refresh").onclick = () => renderPreview(code);
el.outline.onclick = () => {
  el.outline.setAttribute("aria-pressed", String(el.outline.getAttribute("aria-pressed") !== "true"));
  renderPreview(code);
};

$("#code-copy").onclick = async () => {
  if (!code) return setStatus(t("code.none"), "error");
  try { await Memu.copy(code); setStatus(t("code.copied"), "ok"); }
  catch (error) { setStatus(error.message, "error"); }
};

$("#code-save").onclick = async () => {
  if (!code) return setStatus(t("code.none"), "error");
  try {
    const result = await Memu.download({ name: "design.html", text: code });
    setStatus(t("code.saved", { name: (result && result.name) || "design.html" }), "ok");
  } catch (error) { setStatus(error.message, "error"); }
};

el.versionBack.onclick = () => goToVersion(cursor - 1, "version.restored");
el.undo.onclick = () => goToVersion(cursor - 1, "history.undone");
el.redo.onclick = () => goToVersion(cursor + 1, "history.redone");
el.historyToggle.onclick = (event) => { event.stopPropagation(); toggleHistory(); };
el.historyList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-version]");
  if (!item) return;
  toggleHistory(false);
  goToVersion(Number(item.dataset.version));
});
document.addEventListener("click", (event) => {
  if (!el.historyPop.hidden && !(event.target.closest && event.target.closest("#history"))) toggleHistory(false);
});

$("#reset-instructions").onclick = () => {
  el.instructions.value = baseInstructions();
  setStatus(t("instructions.restored"), "ok");
  remember(true);
};

function handoffPrompt() {
  return [t("handoff.continue"), lastPrompt, el.handoffNote.value.trim()]
    .filter(Boolean).join("\n\n");
}

$("#handoff-copy").onclick = async () => {
  try { await Memu.copy(handoffPrompt()); setStatus(t("prompt.copied"), "ok"); }
  catch (error) { setStatus(error.message, "error"); }
};

function drawHandoffResult() {
  if (!handoffState) { el.handoffResult.hidden = true; return; }
  el.handoffResult.innerHTML = t("handoff.ready_html", { images: handoffState.images, files: handoffState.paths.length })
    + "<br>" + handoffState.paths.map((item) => `<code>${esc(item)}</code>`).join("<br>");
  el.handoffResult.hidden = false;
}

$("#handoff").onclick = async () => {
  if (!code) return setStatus(t("handoff.need_design"), "error");
  const button = $("#handoff");
  button.disabled = true;
  setStatus(t("handoff.building"), "busy");
  handoffState = null;
  drawHandoffResult();
  try {
    const bundle = await Memu.handoff({
      prompt: [lastPrompt, el.handoffNote.value.trim()].filter(Boolean).join("\n\n"),
      images,
      code,
      files: [
        { name: "index.html", content: code },
        { name: "design-notes.md", content: designNotes() },
      ],
    });
    const paths = bundle.paths || [];
    if (!paths.length) throw new Error(t("handoff.not_saved"));
    handoffState = { images: images.length, paths };
    drawHandoffResult();
    setStatus(t("handoff.ready"), "ok");
    remember(true);
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
};

function designNotes() {
  return [
    t("notes.title"), "",
    t("notes.task", { value: el.brief.value.trim() || "—" }), "",
    t("notes.kind", { value: optionText(el.kind) }),
    t("notes.system", { value: optionText(el.system) }),
    t("notes.palette", { value: optionText(el.palette) }),
    t("notes.mode", { value: optionText(el.mode) }),
    t("notes.font", { value: optionText(el.font) }), "",
    t("notes.check"), "",
    t("notes.check.1"), t("notes.check.2"), t("notes.check.3"), t("notes.check.4"), "",
    t("notes.instructions"), "", el.instructions.value || baseInstructions(), "",
  ].join("\n");
}

function relabel() {
  if (isDefaultInstruction(el.instructions.value)) el.instructions.value = baseInstructions();
  drawEmptyModelOption();
  renderSwatches();
  renderImages();
  renderCustomSections();
  updateCodeSize();
  drawPreviewTitle(code);
  if (!el.empty.hidden) drawEmpty();
  drawHandoffResult();
  drawHistory();
  fitDevice();
}
Memu.onLanguage(relabel);

el.code.value = code;
updateCodeSize();
renderCustomSections();
selectDevice(device);
renderPreview(code);
if (code) setStatus(t("status.restored"));

if (Memu.toolbar && Memu.toolbar.adopt) Memu.toolbar.adopt({ hide: ".ds-brand", status: "#design-status" });
if (typeof Memu.ready === "function") Memu.ready();
