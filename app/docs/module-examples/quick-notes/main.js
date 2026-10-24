// Notes: a list on the left, an editor on the right; everything is kept with Memu.save.
const $ = (selector) => document.querySelector(selector);
let notes = Array.isArray(Memu.saved.notes) ? Memu.saved.notes : [];
let selected = Memu.saved.selected || (notes[0] ? notes[0].id : "");
let saveTimer = 0;
let armedRemove = false;

const escapeHtml = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[c]));
const current = () => notes.find((note) => note.id === selected) || null;
const status = (text) => { $("#status").textContent = text; };

// Saving is debounced: one write per pause in typing, not per key.
function persist() {
  clearTimeout(saveTimer);
  status(Memu.t("saving"));
  saveTimer = setTimeout(async () => {
    try {
      await Memu.save({ notes, selected });
      status(Memu.t("saved", { time: Memu.i18n.date(Date.now(), { hour: "2-digit", minute: "2-digit" }) }));
    } catch (error) {
      status(error.message);
    }
  }, 400);
}

function drawList() {
  const query = $("#search").value.trim().toLowerCase();
  const rows = notes.filter((note) => !query || (note.title + " " + note.text).toLowerCase().includes(query));
  $("#list").innerHTML = rows.length
    ? rows.map((note) => `<button type="button" class="note-item" role="option" data-id="${escapeHtml(note.id)}" aria-selected="${note.id === selected}">
        <b>${escapeHtml(note.title || Memu.t("untitled"))}</b>
        <span>${escapeHtml(Memu.i18n.date(note.updated, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</span>
      </button>`).join("")
    : `<div class="mu-empty">${escapeHtml(Memu.t(query ? "nothing" : "empty"))}</div>`;
}

function drawEditor() {
  const note = current();
  for (const id of ["#title", "#text", "#copy", "#export", "#remove"]) $(id).disabled = !note;
  $("#title").value = note ? note.title : "";
  $("#text").value = note ? note.text : "";
  armedRemove = false;
  $("#remove").textContent = Memu.t("remove");
}

function draw() {
  drawList();
  drawEditor();
  $("#add").title = Memu.t("count", { count: notes.length });
}

$("#list").addEventListener("click", (event) => {
  const item = event.target.closest("[data-id]");
  if (!item) return;
  selected = item.dataset.id;
  draw();
  persist();
});

$("#add").addEventListener("click", () => {
  const note = { id: crypto.randomUUID(), title: "", text: "", updated: Date.now() };
  notes.unshift(note);
  selected = note.id;
  $("#search").value = "";
  draw();
  persist();
  $("#title").focus();
});

for (const field of ["title", "text"]) {
  $("#" + field).addEventListener("input", () => {
    const note = current();
    if (!note) return;
    note[field] = $("#" + field).value;
    note.updated = Date.now();
    drawList();
    persist();
  });
}

$("#search").addEventListener("input", drawList);
// Esc clears the search; preventDefault keeps MemuAI from collapsing the window on this press.
$("#search").addEventListener("keydown", (event) => {
  if (event.key === "Escape" && event.target.value) {
    event.preventDefault();
    event.target.value = "";
    drawList();
  }
});

$("#copy").addEventListener("click", async () => {
  const note = current();
  if (!note) return;
  try { await Memu.copy(note.text); status(Memu.t("copied")); } catch (error) { status(error.message); }
});

$("#export").addEventListener("click", async () => {
  const note = current();
  if (!note) return;
  const name = (note.title || Memu.t("untitled")).replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60) + ".md";
  try {
    const file = await Memu.download({ name, text: `# ${note.title}\n\n${note.text}\n` });
    status(Memu.t("exported", { name: file.name }));
  } catch (error) {
    status(error.message);
  }
});

// confirm() is blocked in the module sandbox: the button asks twice instead.
$("#remove").addEventListener("click", () => {
  if (!current()) return;
  if (!armedRemove) {
    armedRemove = true;
    $("#remove").textContent = Memu.t("remove_sure");
    setTimeout(() => { armedRemove = false; $("#remove").textContent = Memu.t("remove"); }, 3000);
    return;
  }
  notes = notes.filter((note) => note.id !== selected);
  selected = notes[0] ? notes[0].id : "";
  draw();
  persist();
});

Memu.onLanguage(draw);
draw();
Memu.ready();
