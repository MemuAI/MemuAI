/* MemuLearn · course.js — курс: шапка (прогресс, повторить, экзамен, экспорт), дерево модулей и уроков,
 * урок (конспект + его карточки, «Сгенерировать» одним запросом к модели), вкладка «Карточки» (поиск, правка,
 * своя карточка Basic/Cloze). «Подготовить все уроки» идёт по урокам по одному и останавливается кнопкой. */
const COURSE = { tab: "learn", lesson: "", q: "", stop: false };

VIEWS.course = async (params) => {
  const id = params.id || (S.course && S.course.id) || S.last;
  if (!id) return go("home");
  const view = await API.call("course.get", { id });
  S.course = view.course;
  S.tree = view.tree;
  S.exams = view.exams || [];
  S.last = id;
  remember();
  $$(".ml-course-link").forEach((b) => b.classList.toggle("on", b.dataset.course === id));
  if (params.id && params.id !== COURSE.course) { COURSE.lesson = ""; COURSE.tab = "learn"; }
  COURSE.course = id;
  if (!COURSE.lesson) COURSE.lesson = firstLesson();
  renderCourse();
};

function lessons() { return S.tree.flatMap((m) => (m.kind === "lesson" ? [m] : m.children || [])); }
function firstLesson() {
  const all = lessons();
  const next = all.find((l) => !l.done) || all[0];   // первый непройденный по порядку, а не первый «без карточек»
  return next ? next.id : "";
}

function renderCourse() {
  const c = S.course;
  const all = lessons();
  const ready = all.filter((l) => l.cards > 0).length;
  const mastery = c.cards ? (c.cards - c.new) / c.cards : 0;
  main.innerHTML = `<div class="ml-page ml-course" style="${hueStyle(c.hue)}">
    <header class="ml-course-head">
      <div class="ml-course-ring">${ring(mastery, 64, 6)}<b>${num(Math.round(mastery * 100))}%</b></div>
      <div class="ml-course-title">
        <span class="ml-eyebrow">${esc(t("src." + (c.source_kind === "file" ? "book" : c.source_kind || "topic")))}${c.source_name ? " · " + esc(c.source_name) : ""}</span>
        <h2>${esc(c.title)}</h2>
        ${c.description ? `<p class="ml-muted">${esc(c.description)}</p>` : ""}
        <div class="ml-chips">
          <span class="ml-chip">${esc(plural(all.length, "count.lessons"))}</span>
          <span class="ml-chip">${esc(plural(c.cards, "count.cards"))}</span>
          ${c.due ? `<span class="ml-chip due">${esc(t("course.due", { n: num(c.due) }))}</span>` : ""}
          ${all.length ? `<span class="ml-chip">${esc(t("course.ready", { a: num(ready), b: num(all.length) }))}</span>` : ""}
        </div>
      </div>
      <div class="ml-head-actions">
        <button type="button" class="ml-btn primary" id="co-review"${c.due || c.new ? "" : " disabled"}>${icon("play")}<span>${esc(t("course.review"))}</span></button>
        <button type="button" class="ml-btn" id="co-exam"${all.length ? "" : " disabled"}>${icon("exam")}<span>${esc(t("course.exam"))}</span></button>
        <div class="ml-menu-wrap"><button type="button" class="ml-icon-btn" id="co-more" aria-haspopup="menu" aria-label="${esc(t("more"))}" title="${esc(t("more"))}">${icon("more")}</button></div>
      </div>
    </header>
    <div class="ml-tabs" role="tablist">
      <button type="button" role="tab" data-tab="learn" class="${COURSE.tab === "learn" ? "on" : ""}">${esc(t("tab.learn"))}</button>
      <button type="button" role="tab" data-tab="cards" class="${COURSE.tab === "cards" ? "on" : ""}">${esc(t("tab.cards"))} <i>${num(c.cards)}</i></button>
      <button type="button" role="tab" data-tab="exams" class="${COURSE.tab === "exams" ? "on" : ""}">${esc(t("tab.exams"))}${S.exams.length ? ` <i>${num(S.exams.length)}</i>` : ""}</button>
    </div>
    <div id="co-body"></div>
  </div>`;
  $("#co-review").onclick = () => startReview({ courseId: c.id });
  $("#co-exam").onclick = () => startExamSetup();
  $("#co-more").onclick = (e) => courseMenu(e.currentTarget);
  $$(".ml-tabs [data-tab]").forEach((b) => b.onclick = () => { COURSE.tab = b.dataset.tab; renderCourse(); });
  if (COURSE.tab === "cards") renderCardsTab();
  else if (COURSE.tab === "exams") renderExamsTab();
  else renderLearnTab();
}

function courseMenu(anchor) {
  const items = [
    { id: "gen-all", icon: "spark", label: t("course.gen_all") },
    { id: "outline", icon: "spark", label: t(S.tree.length ? "course.rebuild" : "course.build") },
    { sep: true },
    { id: "import", icon: "upload", label: t("course.import") },
    { id: "apkg", icon: "download", label: t("course.export_apkg") },
    { id: "csv", icon: "download", label: t("course.export_csv") },
    { sep: true },
    { id: "rename", icon: "edit", label: t("course.rename") },
    { id: "delete", icon: "trash", label: t("course.delete"), danger: true },
  ];
  popMenu(anchor, items, async (id) => {
    const c = S.course;
    if (id === "gen-all") return generateAll();
    if (id === "outline") return buildOutline(!!S.tree.length);
    if (id === "import") { S.importTo = c.id; return $("#file-import").click(); }
    if (id === "apkg" || id === "csv") return exportCourse(id);
    if (id === "rename") {
      const name = await askText(t("course.rename"), c.title);
      if (name && name.trim()) { await API.call("course.update", { id: c.id, title: name.trim() }); await refreshSide(); go("course", { id: c.id }); }
      return;
    }
    if (id === "delete") {
      if (!(await askConfirm(t("course.delete_q", { name: c.title }), t("course.delete")))) return;
      await API.call("course.delete", { id: c.id });
      S.course = null; S.last = ""; remember();
      await refreshSide();
      toast(t("course.deleted"), "ok");
      go("home");
    }
  });
}

/* ---------------------------------------------------------------- learn tab: tree + lesson */
function renderLearnTab() {
  const body = $("#co-body");
  if (!S.tree.length) {
    body.innerHTML = `<div class="ml-empty"><span class="ml-empty-ico">${icon("spark")}</span><b>${esc(t("course.empty"))}</b>
      <small>${esc(t("course.empty_hint"))}</small><div class="ml-row"><button type="button" class="ml-btn primary" id="co-build">${icon("spark")}<span>${esc(t("course.build"))}</span></button>
      <button type="button" class="ml-btn" id="co-add-card">${icon("plus")}<span>${esc(t("card.add"))}</span></button></div></div>`;
    $("#co-build").onclick = () => buildOutline(false);
    $("#co-add-card").onclick = () => editCard(null);
    return;
  }
  body.innerHTML = `<div class="ml-learn">
    <nav class="ml-tree" aria-label="${esc(t("tab.learn"))}">${S.tree.map((m, mi) => m.kind === "module" ? `<div class="ml-mod">
        <div class="ml-mod-head"><span class="ml-mod-n">${num(mi + 1)}</span><b>${esc(m.title)}</b></div>
        ${(m.children || []).map((l, li) => lessonLink(l, `${mi + 1}.${li + 1}`)).join("")}</div>` : lessonLink(m, num(mi + 1))).join("")}
      <button type="button" class="ml-tree-add" id="co-add-lesson">${icon("plus")}<span>${esc(t("lesson.add"))}</span></button>
    </nav>
    <section class="ml-lesson" id="co-lesson"></section>
  </div>`;
  $$(".ml-lesson-link").forEach((b) => b.onclick = () => { COURSE.lesson = b.dataset.lesson; $$(".ml-lesson-link").forEach((x) => x.classList.toggle("on", x === b)); renderLesson(); });
  $("#co-add-lesson").onclick = addLesson;
  renderLesson();
}

function lessonLink(l, n) {
  const st = l.done ? "done" : l.cards ? "ready" : "todo";
  return `<button type="button" class="ml-lesson-link${l.id === COURSE.lesson ? " on" : ""}" data-lesson="${esc(l.id)}" data-st="${st}">
    <span class="ml-ln">${st === "done" ? icon("check") : esc(n)}</span><span class="ml-lt">${esc(l.title)}</span>${l.due ? `<em>${num(l.due)}</em>` : l.cards ? `<small>${num(l.cards)}</small>` : ""}</button>`;
}

async function renderLesson() {
  const host = $("#co-lesson");
  if (!host) return;
  const lesson = lessons().find((l) => l.id === COURSE.lesson);
  if (!lesson) { host.innerHTML = ""; return; }
  const parent = S.tree.find((m) => (m.children || []).some((x) => x.id === lesson.id));
  host.innerHTML = `<div class="ml-skel tall"></div>`;
  const [node, cards] = await Promise.all([API.call("node.get", { id: lesson.id }), API.call("card.list", { course_id: S.course.id, lesson_id: lesson.id })]);
  if (COURSE.lesson !== lesson.id) return;
  const hasSummary = !!(node.summary || "").trim();
  host.innerHTML = `<div class="ml-lesson-head">
      <div><span class="ml-eyebrow">${esc(parent ? parent.title : t("lesson.title"))}</span><h3>${esc(node.title)}</h3>${node.brief ? `<p class="ml-muted">${esc(node.brief)}</p>` : ""}</div>
      <div class="ml-head-actions">
        ${hasSummary ? `<button type="button" class="ml-btn" id="ls-gen">${icon("spark")}<span>${esc(t("lesson.regen"))}</span></button>` : ""}
        <button type="button" class="ml-icon-btn" id="ls-done" aria-pressed="${node.done ? "true" : "false"}" title="${esc(t(node.done ? "lesson.undone" : "lesson.done"))}" aria-label="${esc(t("lesson.done"))}">${icon("check")}</button>
        <button type="button" class="ml-icon-btn" id="ls-del" title="${esc(t("lesson.delete"))}" aria-label="${esc(t("lesson.delete"))}">${icon("trash")}</button>
      </div></div>
    <div class="ml-gen-status" id="ls-status" hidden></div>
    ${hasSummary ? `<article class="ml-prose" id="ls-summary"></article>` : `<div class="ml-empty soft"><span class="ml-empty-ico">${icon("book")}</span><b>${esc(t("lesson.empty"))}</b><small>${esc(t("lesson.empty_hint"))}</small>
      <div class="ml-row"><button type="button" class="ml-btn primary" id="ls-gen">${icon("spark")}<span>${esc(t("lesson.gen"))}</span></button></div></div>`}
    <div class="ml-section-head"><h4>${esc(t("lesson.cards"))} <span class="ml-muted">${num(cards.total)}</span></h4>
      <div class="ml-row">${cards.total ? `<button type="button" class="ml-btn small" id="ls-review">${icon("play")}<span>${esc(t("lesson.review"))}</span></button>` : ""}
      <button type="button" class="ml-btn small" id="ls-add">${icon("plus")}<span>${esc(t("card.add"))}</span></button></div></div>
    <div class="ml-card-list" id="ls-cards">${cards.cards.map(cardRow).join("") || (hasSummary ? `<div class="ml-cta" id="ls-cta">
      <span class="ml-empty-ico">${icon("cards")}</span><div><b>${esc(t("lesson.make_cards_title"))}</b><small>${esc(t("lesson.make_cards_hint"))}</small></div>
      <button type="button" class="ml-btn primary" id="ls-make">${icon("spark")}<span>${esc(t("lesson.make_cards"))}</span></button></div>`
      : `<p class="ml-muted small">${esc(t("lesson.no_cards"))}</p>`)}</div>`;
  if (hasSummary) MD.into($("#ls-summary"), node.summary);
  MD.typeset($("#ls-cards"));
  $("#ls-gen").onclick = () => generateLesson(lesson.id);
  const make = $("#ls-make");
  if (make) make.onclick = () => generateLesson(lesson.id, false, true);
  $("#ls-done").onclick = async () => { await API.call("node.update", { id: lesson.id, done: !node.done }); go("course", { id: S.course.id }); };
  $("#ls-del").onclick = async () => {
    if (!(await askConfirm(t("lesson.delete_q", { name: node.title }), t("lesson.delete")))) return;
    await API.call("node.delete", { id: lesson.id });
    COURSE.lesson = "";
    go("course", { id: S.course.id });
  };
  $("#ls-add").onclick = () => editCard(null, lesson.id);
  const rv = $("#ls-review");
  if (rv) rv.onclick = () => startReview({ courseId: S.course.id, lessonId: lesson.id, cram: true });
  wireCardRows($("#ls-cards"));
}

function cardRow(c) {
  const front = c.kind === "cloze" ? MD.render(c.front, { cloze: { ord: c.ord, reveal: true } }) : MD.render(c.front);
  const st = c.suspended ? "susp" : c.state === 0 ? "new" : c.state === 2 ? "rev" : "lrn";
  return `<div class="ml-card-row" data-card="${esc(c.id)}" data-st="${st}">
    <span class="ml-card-kind" title="${esc(t("kind." + c.kind))}">${c.kind === "cloze" ? "[…]" : "Q"}</span>
    <div class="ml-card-q">${front}</div>
    <div class="ml-card-a">${c.kind === "cloze" ? (c.extra || c.back ? MD.render(c.extra || c.back) : "") : MD.render(c.back)}</div>
    <span class="ml-card-due">${esc(cardDue(c))}</span>
    <button type="button" class="ml-icon-btn small" data-edit title="${esc(t("card.edit"))}" aria-label="${esc(t("card.edit"))}">${icon("edit")}</button>
  </div>`;
}
function cardDue(c) {
  if (c.suspended) return t("card.suspended");
  if (c.state === 0) return t("card.new");
  const s = c.due - Date.now() / 1000;
  if (s <= 0) return t("card.due_now");
  return t("card.in", { v: fmtSpan(s) });
}
function fmtSpan(s) {
  if (s < 3600) return t("span.m", { n: num(Math.max(1, Math.round(s / 60))) });
  if (s < 86400) return t("span.h", { n: num(Math.round(s / 3600)) });
  if (s < 86400 * 60) return t("span.d", { n: num(Math.round(s / 86400)) });
  if (s < 86400 * 365) return t("span.mo", { n: num(Math.round(s / 86400 / 30.4 * 10) / 10) });
  return t("span.y", { n: num(Math.round(s / 86400 / 365 * 10) / 10) });
}
function wireCardRows(host) {
  host.onclick = (e) => {
    const row = e.target.closest("[data-card]");
    if (row && e.target.closest("[data-edit]")) API.call("card.get", { id: row.dataset.card }).then((card) => editCard(card), fail);
  };
}

async function addLesson() {
  const title = await askText(t("lesson.add"), "");
  if (!title || !title.trim()) return;
  const mod = S.tree.find((m) => (m.children || []).some((l) => l.id === COURSE.lesson)) || S.tree[S.tree.length - 1];
  const node = await API.call("node.add", { course_id: S.course.id, title: title.trim(), parent_id: mod && mod.kind === "module" ? mod.id : "" });
  COURSE.lesson = node.id;
  go("course", { id: S.course.id });
}

async function buildOutline(replace) {
  if (S.busy) return;
  if (replace && !(await askConfirm(t("course.rebuild_q"), t("course.rebuild")))) return;
  setBusy("outline");
  toast(t("create.stage.outline"));
  try {
    await aiTask("outline", { course_id: S.course.id, replace: !!replace, rename: false });
    COURSE.lesson = "";
    toast(t("create.done"), "ok");
    go("course", { id: S.course.id });
  } catch (e) { fail(e); } finally { setBusy(null); }
}

async function generateLesson(lessonId, quiet, cardsOnly) {
  const status = $("#ls-status"), btn = $(cardsOnly ? "#ls-make" : "#ls-gen");
  if (!quiet && S.busy) return;
  if (!quiet) setBusy("lesson");
  if (btn) { btn.disabled = true; btn.classList.add("loading"); }
  const stage = (k) => { if (status && COURSE.lesson === lessonId) { status.hidden = false; status.innerHTML = `<span class="ml-spin"></span>${esc(t("lesson.stage." + k))}`; } };
  stage("material");
  try {
    const r = await aiTask("lesson", { lesson_id: lessonId, cards: S.prefs.cards_per_lesson || 8, cards_only: !!cardsOnly }, stage);
    if (!quiet) toast(t(cardsOnly ? "lesson.made_cards" : "lesson.generated", { n: num(r.added) }), "ok");
    return r;
  } catch (e) { if (!quiet) fail(e); throw e; } finally {
    if (!quiet) { setBusy(null); go("course", { id: S.course.id }); }
  }
}

async function generateAll() {
  if (S.busy) return;
  const todo = lessons().filter((l) => !l.cards);
  if (!todo.length) return toast(t("course.all_ready"), "ok");
  setBusy("all");
  COURSE.stop = false;
  const bar = document.createElement("div");
  bar.className = "ml-batch";
  document.body.appendChild(bar);
  const draw = (i, title) => {
    bar.innerHTML = `<span class="ml-spin"></span><div><b>${esc(t("course.gen_progress", { a: num(i + 1), b: num(todo.length) }))}</b><small>${esc(title)}</small>
      <span class="ml-batch-bar"><i style="width:${Math.round(i / todo.length * 100)}%"></i></span></div><button type="button" class="ml-btn small" id="batch-stop">${icon("stop")}<span>${esc(t("stop"))}</span></button>`;
    $("#batch-stop").onclick = () => { COURSE.stop = true; bar.querySelector("small").textContent = t("course.stopping"); };
  };
  let done = 0;
  try {
    for (let i = 0; i < todo.length && !COURSE.stop; i++) {
      draw(i, todo[i].title);
      COURSE.lesson = todo[i].id;
      try { await generateLesson(todo[i].id, true); done++; } catch (e) { fail(e); break; }
    }
  } finally {
    bar.remove();
    setBusy(null);
    toast(t("course.gen_done", { n: num(done) }), "ok");
    go("course", { id: S.course.id });
  }
}

/* ---------------------------------------------------------------- cards tab */
async function renderCardsTab() {
  const body = $("#co-body");
  body.innerHTML = `<div class="ml-cards-tab">
    <div class="ml-toolbar"><label class="ml-search">${icon("search")}<input id="ct-q" type="search" placeholder="${esc(t("cards.search"))}" value="${esc(COURSE.q)}"></label>
      <span class="ml-grow"></span><button type="button" class="ml-btn small" id="ct-add">${icon("plus")}<span>${esc(t("card.add"))}</span></button></div>
    <div class="ml-card-list" id="ct-list"><div class="ml-skel"></div><div class="ml-skel"></div></div></div>`;
  const load = async () => {
    const r = await API.call("card.list", { course_id: S.course.id, q: COURSE.q, limit: 400 });
    const list = $("#ct-list");
    if (!list) return;
    list.innerHTML = r.cards.map(cardRow).join("") || `<div class="ml-empty soft"><b>${esc(t(COURSE.q ? "cards.none_found" : "cards.none"))}</b></div>`;
    if (r.total > r.cards.length) list.insertAdjacentHTML("beforeend", `<p class="ml-muted small center">${esc(t("cards.more", { n: num(r.total - r.cards.length) }))}</p>`);
    MD.typeset(list);
    wireCardRows(list);
  };
  let timer = 0;
  $("#ct-q").oninput = (e) => { COURSE.q = e.target.value; clearTimeout(timer); timer = setTimeout(load, 220); };
  $("#ct-add").onclick = () => editCard(null);
  load().catch(fail);
}

/* ---------------------------------------------------------------- card editor (dialog) */
function editCard(card, lessonId) {
  const isNew = !card;
  const c = card || { kind: "basic", front: "", back: "", extra: "", tags: "", lesson_id: lessonId || COURSE.lesson || "" };
  const dlg = openDialog(`<h3>${esc(t(isNew ? "card.add" : "card.edit"))}</h3>
    <div class="ml-seg" role="radiogroup">${["basic", "cloze"].map((k) => `<button type="button" role="radio" data-kind="${k}" aria-checked="${c.kind === k}"${isNew ? "" : " disabled"}>${esc(t("kind." + k))}</button>`).join("")}</div>
    <label class="ml-field"><span id="ce-front-label">${esc(t(c.kind === "cloze" ? "card.text" : "card.front"))}</span><textarea class="ml-input mono" id="ce-front" rows="4">${esc(c.front)}</textarea></label>
    <label class="ml-field" id="ce-back-wrap"><span>${esc(t(c.kind === "cloze" ? "card.extra" : "card.back"))}</span><textarea class="ml-input mono" id="ce-back" rows="3">${esc(c.kind === "cloze" ? (c.extra || c.back) : c.back)}</textarea></label>
    <p class="ml-muted small">${esc(t("card.syntax"))}</p>
    <div class="ml-preview" id="ce-preview"></div>
    <div class="ml-dialog-foot">${isNew ? "" : `<button type="button" class="ml-btn danger-text" data-act="delete">${icon("trash")}<span>${esc(t("card.delete"))}</span></button>
      <button type="button" class="ml-btn" data-act="suspend">${esc(t(c.suspended ? "card.unsuspend" : "card.suspend"))}</button>`}
      <span class="ml-grow"></span><button type="button" class="ml-btn" data-act="cancel">${esc(t("cancel"))}</button>
      <button type="button" class="ml-btn primary" data-act="save">${esc(t("save"))}</button></div>`);
  let kind = c.kind;
  const front = $("#ce-front", dlg), back = $("#ce-back", dlg), preview = $("#ce-preview", dlg);
  let ptimer = 0;
  const draw = () => {
    clearTimeout(ptimer);
    ptimer = setTimeout(() => {
      MD.into(preview, kind === "cloze" ? front.value : front.value + (back.value ? "\n\n---\n\n" + back.value : ""), kind === "cloze" ? { cloze: { ord: 1, reveal: true } } : null);
    }, 120);
  };
  front.oninput = draw; back.oninput = draw; draw();
  $$("[data-kind]", dlg).forEach((b) => b.onclick = () => {
    kind = b.dataset.kind;
    $$("[data-kind]", dlg).forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    $("#ce-front-label", dlg).textContent = t(kind === "cloze" ? "card.text" : "card.front");
    $("#ce-back-wrap span", dlg).textContent = t(kind === "cloze" ? "card.extra" : "card.back");
    if (kind === "cloze" && !front.value) front.value = t("card.cloze_example");
    draw();
  });
  front.focus();
  dlg.onclick = async (e) => {
    const act = (e.target.closest("[data-act]") || {}).dataset;
    if (!act || !act.act) return;
    try {
      if (act.act === "cancel") return closeDialog(dlg);
      if (act.act === "delete") { await API.call("card.delete", { id: c.id }); }
      else if (act.act === "suspend") { await API.call("card.update", { id: c.id, suspended: !c.suspended }); }
      else if (isNew) {
        await API.call("card.add", { course_id: S.course.id, lesson_id: c.lesson_id, kind, front: front.value, back: kind === "cloze" ? "" : back.value, extra: kind === "cloze" ? back.value : "" });
        toast(t("card.added"), "ok");
      } else {
        await API.call("card.update", kind === "cloze" ? { id: c.id, front: front.value, extra: back.value } : { id: c.id, front: front.value, back: back.value });
      }
      closeDialog(dlg);
      go("course", { id: S.course.id });
    } catch (err) { fail(err); }
  };
}

/* ---------------------------------------------------------------- dialogs and menus (own, inside the window) */
function openDialog(html) {
  const wrap = document.createElement("div");
  wrap.className = "ml-dialog-wrap";
  wrap.innerHTML = `<div class="ml-dialog" role="dialog" aria-modal="true">${html}</div>`;
  appEl.appendChild(wrap);
  wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) closeDialog(wrap.firstElementChild); });
  wrap.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); closeDialog(wrap.firstElementChild); } });
  return wrap.firstElementChild;
}
function closeDialog(dlg) { const w = dlg && dlg.parentElement; if (w) w.remove(); }
function askText(title, value) {
  return new Promise((resolve) => {
    const dlg = openDialog(`<h3>${esc(title)}</h3><input class="ml-input" id="ask-v" value="${esc(value || "")}">
      <div class="ml-dialog-foot"><span class="ml-grow"></span><button type="button" class="ml-btn" data-a="0">${esc(t("cancel"))}</button><button type="button" class="ml-btn primary" data-a="1">${esc(t("ok"))}</button></div>`);
    const input = $("#ask-v", dlg);
    input.focus(); input.select();
    const done = (ok) => { const v = input.value; closeDialog(dlg); resolve(ok ? v : null); };
    input.onkeydown = (e) => { if (e.key === "Enter") done(true); };
    dlg.onclick = (e) => { const b = e.target.closest("[data-a]"); if (b) done(b.dataset.a === "1"); };
  });
}
function askConfirm(text, action) {
  return new Promise((resolve) => {
    const dlg = openDialog(`<p class="ml-confirm">${esc(text)}</p><div class="ml-dialog-foot"><span class="ml-grow"></span>
      <button type="button" class="ml-btn" data-a="0">${esc(t("cancel"))}</button><button type="button" class="ml-btn danger" data-a="1">${esc(action)}</button></div>`);
    $("[data-a='1']", dlg).focus();
    dlg.onclick = (e) => { const b = e.target.closest("[data-a]"); if (b) { closeDialog(dlg); resolve(b.dataset.a === "1"); } };
  });
}
function popMenu(anchor, items, onPick) {
  $$(".ml-menu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "ml-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = items.map((it) => it.sep ? '<hr>' : `<button type="button" role="menuitem" data-id="${it.id}" class="${it.danger ? "danger" : ""}">${icon(it.icon)}<span>${esc(it.label)}</span></button>`).join("");
  anchor.parentElement.appendChild(menu);
  const close = (e) => { if (!e || !menu.contains(e.target)) { menu.remove(); document.removeEventListener("mousedown", close, true); } };
  setTimeout(() => document.addEventListener("mousedown", close, true), 0);
  menu.onclick = (e) => { const b = e.target.closest("[data-id]"); if (b) { close(); onPick(b.dataset.id); } };
  menu.onkeydown = (e) => { if (e.key === "Escape") { close(); anchor.focus(); } };
  const first = menu.querySelector("button");
  if (first) first.focus();
}
