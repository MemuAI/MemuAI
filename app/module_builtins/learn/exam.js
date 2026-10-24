/* MemuLearn · exam.js — экзаменатор: билеты по урокам курса (теория / код / смешанно), развёрнутый ответ или код,
 * ИИ проверяет по критериям, разбирает ошибки, показывает образцовый ответ и сразу создаёт карточки по слабым темам. */
const EX = { exam: null, i: 0 };

function startExamSetup() {
  const all = lessons();
  const dlg = openDialog(`<h3>${esc(t("exam.new"))}</h3>
    <p class="ml-muted small">${esc(t("exam.new_hint"))}</p>
    <div class="ml-field"><span>${esc(t("exam.mode"))}</span><div class="ml-seg" role="radiogroup" id="ex-mode">
      ${["mixed", "theory", "code"].map((k, i) => `<button type="button" role="radio" data-mode="${k}" aria-checked="${i === 0}">${esc(t("exam.mode." + k))}</button>`).join("")}</div></div>
    <label class="ml-field"><span>${esc(t("exam.count"))}</span><div class="ml-range"><input type="range" id="ex-count" min="1" max="10" value="4"><b id="ex-count-v">4</b></div></label>
    <label class="ml-field"><span>${esc(t("exam.scope"))}</span><select class="ml-input" id="ex-scope"><option value="">${esc(t("exam.scope.all"))}</option>
      ${all.filter((l) => l.cards || l.done).map((l) => `<option value="${esc(l.id)}">${esc(l.title)}</option>`).join("")}</select></label>
    <div class="ml-dialog-foot"><span class="ml-muted small" id="ex-status"></span><span class="ml-grow"></span><button type="button" class="ml-btn" data-a="0">${esc(t("cancel"))}</button>
      <button type="button" class="ml-btn primary" data-a="1">${icon("spark")}<span>${esc(t("exam.make"))}</span></button></div>`);
  let mode = "mixed";
  $$("[data-mode]", dlg).forEach((b) => b.onclick = () => { mode = b.dataset.mode; $$("[data-mode]", dlg).forEach((x) => x.setAttribute("aria-checked", String(x === b))); });
  const count = $("#ex-count", dlg);
  count.oninput = () => { $("#ex-count-v", dlg).textContent = count.value; };
  dlg.onclick = async (e) => {
    const b = e.target.closest("[data-a]");
    if (!b) return;
    if (b.dataset.a === "0") return closeDialog(dlg);
    if (S.busy) return;
    b.disabled = true;
    b.classList.add("loading");
    $("#ex-status", dlg).innerHTML = `<span class="ml-spin"></span>${esc(t("exam.making"))}`;
    setBusy("exam");
    try {
      const scope = $("#ex-scope", dlg).value;
      const r = await aiTask("exam", { course_id: S.course.id, count: +count.value, mode, lesson_ids: scope ? [scope] : [] });
      closeDialog(dlg);
      openExam(r.exam);
    } catch (err) { fail(err); b.disabled = false; b.classList.remove("loading"); $("#ex-status", dlg).textContent = ""; }
    finally { setBusy(null); }
  };
}

function openExam(exam, index) {
  EX.exam = exam;
  const firstOpen = exam.questions.findIndex((q) => !exam.results[q.id]);
  EX.i = index != null ? index : firstOpen < 0 ? 0 : firstOpen;
  S.view = "exam";
  appEl.dataset.view = "exam";
  drawExam();
}

function drawExam() {
  const ex = EX.exam, q = ex.questions[EX.i], res = ex.results[q.id], ans = ex.answers[q.id] || "";
  const doneN = Object.keys(ex.results).length;
  main.innerHTML = `<div class="ml-page ml-exam">
    <header class="ml-exam-head">
      <button type="button" class="ml-icon-btn" id="ex-back" title="${esc(t("exam.back"))}" aria-label="${esc(t("exam.back"))}">${icon("back")}</button>
      <div><span class="ml-eyebrow">${esc(t("exam.ticket_of", { a: num(EX.i + 1), b: num(ex.questions.length) }))}</span><h2>${esc(ex.title)}</h2></div>
      <span class="ml-grow"></span>
      ${ex.score != null && doneN ? `<span class="ml-chip">${esc(t("exam.avg", { n: num(ex.score) }))}</span>` : ""}
    </header>
    <nav class="ml-tickets">${ex.questions.map((x, i) => {
      const r = ex.results[x.id];
      const cls = r ? (r.score >= 80 ? "good" : r.score >= 50 ? "mid" : "bad") : "";
      return `<button type="button" class="ml-ticket ${cls}${i === EX.i ? " on" : ""}" data-i="${i}" title="${esc(MD.plain(x.prompt).slice(0, 120))}">${x.kind === "code" ? icon("code") : ""}<span>${num(i + 1)}</span></button>`;
    }).join("")}</nav>
    <section class="ml-card ml-question"><span class="ml-pill">${esc(t("exam.kind." + q.kind))}</span><div class="ml-prose" id="ex-q"></div></section>
    <section class="ml-answer">
      <label class="ml-field"><span>${esc(t(q.kind === "code" ? "exam.your_code" : "exam.your_answer"))}</span>
        <textarea class="ml-input${q.kind === "code" ? " mono code" : ""}" id="ex-a" rows="${q.kind === "code" ? 12 : 7}" spellcheck="${q.kind === "code" ? "false" : "true"}" placeholder="${esc(t(q.kind === "code" ? "exam.code_ph" : "exam.answer_ph"))}">${esc(ans)}</textarea></label>
      <div class="ml-row"><span class="ml-muted small" id="ex-status">${esc(t("exam.hint"))}</span><span class="ml-grow"></span>
        ${EX.i < ex.questions.length - 1 ? `<button type="button" class="ml-btn" id="ex-skip">${esc(t("exam.next"))}</button>` : ""}
        <button type="button" class="ml-btn primary" id="ex-check">${icon("check")}<span>${esc(t(res ? "exam.recheck" : "exam.check"))}</span><kbd>Ctrl+Enter</kbd></button></div>
    </section>
    <section id="ex-result"></section>
  </div>`;
  MD.into($("#ex-q"), q.prompt);
  const area = $("#ex-a");
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); checkAnswer(); }
    if (e.key === "Tab" && q.kind === "code" && !e.shiftKey) { e.preventDefault(); area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end"); }
  });
  $("#ex-check").onclick = checkAnswer;
  const skip = $("#ex-skip");
  if (skip) skip.onclick = () => { EX.i++; drawExam(); };
  $("#ex-back").onclick = () => { COURSE.tab = "exams"; go("course", { id: ex.course_id }); };
  $$(".ml-ticket").forEach((b) => b.onclick = () => { EX.i = +b.dataset.i; drawExam(); });
  if (res) drawResult(res); else area.focus({ preventScroll: true });
}

async function checkAnswer() {
  const ex = EX.exam, q = ex.questions[EX.i];
  const answerText = $("#ex-a").value.trim();
  if (!answerText) return toast(t("exam.empty"), "error");
  if (S.busy) return;
  setBusy("grade");
  const btn = $("#ex-check");
  btn.disabled = true;
  btn.classList.add("loading");
  $("#ex-status").innerHTML = `<span class="ml-spin"></span>${esc(t("exam.checking"))}`;
  try {
    const r = await aiTask("grade", { exam_id: ex.id, question_id: q.id, student_answer: answerText, make_cards: true });
    EX.exam = r.exam;
    drawExam();
    if (r.result.cards) toast(t("exam.cards_made", { n: num(r.result.cards) }), "ok");
  } catch (e) { fail(e); btn.disabled = false; btn.classList.remove("loading"); $("#ex-status").textContent = ""; }
  finally { setBusy(null); }
}

function drawResult(r) {
  const host = $("#ex-result");
  const tone = r.score >= 80 ? "good" : r.score >= 50 ? "mid" : "bad";
  host.innerHTML = `<div class="ml-card ml-verdict ${tone}">
    <div class="ml-verdict-head"><div class="ml-score">${ring(r.score / 100, 72, 7)}<b>${num(Math.round(r.score))}</b></div>
      <div><span class="ml-eyebrow">${esc(t("exam.verdict"))}</span><h3>${esc(r.verdict || t("exam.tone." + tone))}</h3>
      ${r.weak_topics && r.weak_topics.length ? `<div class="ml-chips">${r.weak_topics.map((w) => `<span class="ml-chip weak">${esc(w)}</span>`).join("")}</div>` : ""}</div></div>
    ${r.mistakes && r.mistakes.length ? `<h4>${esc(t("exam.mistakes"))}</h4><ul class="ml-mistakes">${r.mistakes.map((m) => `<li><div class="ml-m-what">${MD.render(m.what)}</div>${m.fix ? `<div class="ml-m-fix">${MD.render(m.fix)}</div>` : ""}</li>`).join("")}</ul>` : ""}
    ${r.feedback ? `<h4>${esc(t("exam.feedback"))}</h4><div class="ml-prose" id="ex-fb"></div>` : ""}
    ${r.model_answer ? `<details class="ml-model"><summary>${esc(t("exam.model_answer"))}</summary><div class="ml-prose" id="ex-model"></div></details>` : ""}
    ${r.cards ? `<p class="ml-made">${icon("cards")}<span>${esc(t("exam.cards_made", { n: num(r.cards) }))}</span></p>` : ""}
    <div class="ml-row">${EX.i < EX.exam.questions.length - 1 ? `<span class="ml-grow"></span><button type="button" class="ml-btn primary" id="ex-next">${esc(t("exam.next"))}${icon("chev")}</button>`
      : `<span class="ml-grow"></span><button type="button" class="ml-btn primary" id="ex-finish">${esc(t("exam.finish"))}</button>`}</div>
  </div>`;
  if (r.feedback) MD.into($("#ex-fb"), r.feedback);
  if (r.model_answer) MD.into($("#ex-model"), r.model_answer);
  MD.typeset(host);
  const next = $("#ex-next");
  if (next) next.onclick = () => { EX.i++; drawExam(); };
  const fin = $("#ex-finish");
  if (fin) fin.onclick = () => { COURSE.tab = "exams"; go("course", { id: EX.exam.course_id }); };
}

function renderExamsTab() {
  const body = $("#co-body");
  body.innerHTML = S.exams.length ? `<div class="ml-exams">${S.exams.map((x) => `<button type="button" class="ml-exam-row" data-exam="${esc(x.id)}">
      <span class="ml-exam-score ${x.score == null ? "" : x.score >= 80 ? "good" : x.score >= 50 ? "mid" : "bad"}">${x.score == null ? "—" : num(Math.round(x.score))}</span>
      <span><b>${esc(x.title)}</b><small>${esc(ago(x.created))}${x.finished ? "" : " · " + esc(t("exam.unfinished"))}</small></span>${icon("chev")}</button>`).join("")}
      <button type="button" class="ml-btn" id="ex-new">${icon("plus")}<span>${esc(t("exam.new"))}</span></button></div>`
    : `<div class="ml-empty"><span class="ml-empty-ico">${icon("exam")}</span><b>${esc(t("exam.none"))}</b><small>${esc(t("exam.none_hint"))}</small>
      <button type="button" class="ml-btn primary" id="ex-new">${icon("spark")}<span>${esc(t("exam.new"))}</span></button></div>`;
  $("#ex-new").onclick = startExamSetup;
  $$(".ml-exam-row").forEach((b) => b.onclick = async () => { try { openExam(await API.call("exam.get", { id: b.dataset.exam })); } catch (e) { fail(e); } });
}
