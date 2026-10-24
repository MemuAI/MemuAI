/* MemuLearn · create.js — новый курс из любого источника: тема, книга или документация (PDF/DOCX/EPUB/TXT/MD —
 * разбор app/bigdocs.py), документ MemuNote (недавние файлы), колода Anki .apkg или таблица .csv.
 * ИИ строит дерево модулей и уроков (задача outline), потом курс открывается. */
const CREATE = { src: "topic", doc: null, notes: null };

VIEWS.create = async () => {
  CREATE.src = S.createSource || CREATE.src || "topic";
  S.createSource = "";
  CREATE.doc = null;
  renderCreate();
};

function renderCreate() {
  const src = CREATE.src;
  const tabs = ["topic", "book", "note", "anki"].map((k) => `<button type="button" role="tab" class="ml-src${src === k ? " on" : ""}" data-src="${k}" aria-selected="${src === k}">
      ${icon(k === "book" ? "book" : k)}<span><b>${esc(t("src." + k))}</b><small>${esc(t("src." + k + ".hint"))}</small></span></button>`).join("");
  let body = "";
  if (src === "topic") {
    body = `<label class="ml-field"><span>${esc(t("create.topic"))}</span><input class="ml-input big" id="c-title" maxlength="200" placeholder="${esc(t("create.topic.ph"))}" autocomplete="off"></label>`;
  } else if (src === "book") {
    body = `<div class="ml-drop" id="c-drop" tabindex="0">${icon("upload")}<b>${esc(CREATE.doc ? CREATE.doc.name : t("create.file.pick"))}</b>
        <small>${esc(CREATE.doc ? t("create.file.parsed", { pages: num(CREATE.doc.pages || 1), sections: num((CREATE.doc.sections || []).length) }) : t("create.file.hint"))}</small></div>
      <label class="ml-field"><span>${esc(t("create.title"))}</span><input class="ml-input" id="c-title" maxlength="200" value="${esc(CREATE.doc ? (CREATE.doc.title || CREATE.doc.name.replace(/\.[^.]+$/, "")) : "")}" placeholder="${esc(t("create.title.ph"))}"></label>`;
  } else if (src === "note") {
    const files = CREATE.notes || [];
    body = `<div class="ml-notes" id="c-notes">${CREATE.notes == null ? `<div class="ml-skel"></div><div class="ml-skel"></div>`
      : files.length ? files.map((f) => `<button type="button" class="ml-note-file${CREATE.doc && CREATE.doc.path === f.path ? " on" : ""}" data-path="${esc(f.path)}">${icon("note")}<span><b>${esc(f.name)}</b><small>${esc(f.path)}</small></span></button>`).join("")
      : `<p class="ml-muted">${esc(t("create.note.none"))}</p>`}</div>
      <label class="ml-field"><span>${esc(t("create.title"))}</span><input class="ml-input" id="c-title" maxlength="200" value="${esc(CREATE.doc ? (CREATE.doc.title || CREATE.doc.name.replace(/\.[^.]+$/, "")) : "")}" placeholder="${esc(t("create.title.ph"))}"></label>`;
  } else {
    body = `<div class="ml-drop" id="c-drop-import" tabindex="0">${icon("anki")}<b>${esc(t("create.anki.pick"))}</b><small>${esc(t("create.anki.hint"))}</small></div>`;
  }
  const ai = src !== "anki" ? `<div class="ml-row3">
      <label class="ml-field"><span>${esc(t("create.level"))}</span><select class="ml-input" id="c-level">
        ${["zero", "basic", "mid", "pro"].map((k) => `<option value="${k}">${esc(t("level." + k))}</option>`).join("")}</select></label>
      <label class="ml-field"><span>${esc(t("create.depth"))}</span><select class="ml-input" id="c-depth">
        ${["short", "normal", "deep"].map((k) => `<option value="${k}"${k === "normal" ? " selected" : ""}>${esc(t("depth." + k))}</option>`).join("")}</select></label>
      <label class="ml-field"><span>${esc(t("create.model"))}</span><select class="ml-input" id="c-model">${modelOptions()}</select></label>
    </div>
    <label class="ml-field"><span>${esc(t("create.goal"))}</span><textarea class="ml-input" id="c-goal" rows="2" maxlength="600" placeholder="${esc(t("create.goal.ph"))}"></textarea></label>` : "";
  main.innerHTML = `<div class="ml-page ml-create">
    <header class="ml-page-head"><div><span class="ml-eyebrow">${esc(t("create.eyebrow"))}</span><h2>${esc(t("course.new"))}</h2></div></header>
    <div class="ml-src-tabs" role="tablist">${tabs}</div>
    <section class="ml-card ml-create-card">
      ${body}${ai}
      <div class="ml-create-foot">
        <p class="ml-muted small" id="c-status">${esc(t(src === "anki" ? "create.anki.note" : "create.ai_note"))}</p>
        ${src !== "anki" ? `<button type="button" class="ml-btn primary" id="c-go">${icon("spark")}<span>${esc(t("create.go"))}</span></button>` : ""}
      </div>
    </section>
  </div>`;
  $$(".ml-src").forEach((b) => b.onclick = () => { CREATE.src = b.dataset.src; CREATE.doc = null; renderCreate(); if (CREATE.src === "note") loadNotes(); });
  const title = $("#c-title");
  if (title) title.focus({ preventScroll: true });
  const drop = $("#c-drop");
  if (drop) wireDrop(drop, () => $("#file-doc").click(), ingestFile);
  const dropImport = $("#c-drop-import");
  if (dropImport) wireDrop(dropImport, () => $("#file-import").click(), (file) => importFile(file, ""));
  $$(".ml-note-file").forEach((b) => b.onclick = () => pickNote(b.dataset.path));
  const goBtn = $("#c-go");
  if (goBtn) goBtn.onclick = createCourse;
  if (title) title.addEventListener("keydown", (e) => { if (e.key === "Enter" && goBtn) createCourse(); });
  const model = $("#c-model");
  if (model) model.onchange = () => { S.model = model.value; remember(); };
}

function modelOptions() {
  if (!S.models.length) return `<option value="">${esc(t("model.default"))}</option>`;
  return S.models.map((m) => `<option value="${esc(m.id)}"${m.id === S.model ? " selected" : ""}>${esc(m.name && m.name !== m.id ? m.name : m.id)}</option>`).join("");
}

function wireDrop(el, pick, onFile) {
  el.onclick = pick;
  el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  el.ondragover = (e) => { e.preventDefault(); el.classList.add("over"); };
  el.ondragleave = () => el.classList.remove("over");
  el.ondrop = (e) => { e.preventDefault(); el.classList.remove("over"); const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) onFile(f); };
}
$("#file-doc").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) ingestFile(f); };
$("#file-import").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importFile(f, S.importTo || ""); S.importTo = ""; };

async function ingestFile(file) {
  const status = $("#c-status");
  if (status) status.textContent = t("create.file.reading", { name: file.name });
  try {
    const doc = await API.call("source.ingest", { name: file.name, data: await readFile(file) });
    CREATE.doc = Object.assign({ kind: "file" }, doc);
    renderCreate();
  } catch (e) { fail(e); if (status) status.textContent = ""; }
}

async function loadNotes() {
  try { CREATE.notes = (await API.call("source.notes")).files || []; } catch (e) { CREATE.notes = []; }
  if (CREATE.src === "note") renderCreate();
}
async function pickNote(path) {
  const status = $("#c-status");
  if (status) status.textContent = t("create.file.reading", { name: path.split(/[\\/]/).pop() });
  try {
    CREATE.doc = Object.assign({ kind: "note", path }, await API.call("source.note", { path }));
    renderCreate();
  } catch (e) { fail(e); }
}

async function importFile(file, courseId) {
  const isApkg = /\.apkg$/i.test(file.name);
  toast(t("import.busy", { name: file.name }));
  try {
    const r = isApkg ? await API.call("import.apkg", { name: file.name, data: await readFile(file), course_id: courseId })
      : await (async () => {
        let cid = courseId;
        if (!cid) cid = (await API.call("course.create", { title: file.name.replace(/\.[^.]+$/, ""), description: t("import.csv_desc") })).course.id;
        const res = await API.call("import.csv", { course_id: cid, text: await readText(file) });
        return { course_id: cid, notes: res.added, skipped: res.skipped };
      })();
    toast(t("import.done", { n: num(r.notes) }) + (r.skipped ? " · " + t("import.skipped", { n: num(r.skipped) }) : ""), "ok");
    await refreshSide();
    go("course", { id: r.course_id });
  } catch (e) { fail(e); }
}

async function createCourse() {
  if (S.busy) return;
  const title = ($("#c-title") || {}).value || "";
  const src = CREATE.src;
  if (src === "topic" && !title.trim()) { $("#c-title").focus(); return toast(t("create.need_topic"), "error"); }
  if ((src === "book" || src === "note") && !CREATE.doc) return toast(t("create.need_file"), "error");
  const level = $("#c-level").value, depth = $("#c-depth").value, goal = $("#c-goal").value.trim();
  const btn = $("#c-go"), status = $("#c-status");
  setBusy("create");
  btn.disabled = true;
  btn.classList.add("loading");
  const stage = (k) => { status.innerHTML = `<span class="ml-spin"></span>${esc(t("create.stage." + k))}`; };
  let courseId = "";
  try {
    stage("course");
    const source = CREATE.doc ? { kind: src === "note" ? "note" : "file", doc_id: CREATE.doc.doc_id } : { kind: "topic" };
    const view = await API.call("course.create", { title: title.trim(), source });
    courseId = view.course.id;
    stage("outline");
    await aiTask("outline", { course_id: courseId, depth, level: t("level." + level), goal, rename: src !== "topic" ? false : true },
      (k) => stage(k === "think" ? "outline" : k === "save" ? "tree" : "retry"));
    S.last = courseId;
    remember();
    await refreshSide();
    toast(t("create.done"), "ok");
    go("course", { id: courseId, fresh: true });
  } catch (e) {
    fail(e);
    status.textContent = t("create.failed");
    if (courseId) { await refreshSide(); }
  } finally {
    setBusy(null);
    if (btn.isConnected) { btn.disabled = false; btn.classList.remove("loading"); }
  }
}
