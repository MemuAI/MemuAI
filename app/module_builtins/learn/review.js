

const RV = { queue: [], i: 0, card: null, shown: false, started: 0, done: 0, ratings: [0, 0, 0, 0, 0], courseId: "", lessonId: "", cram: false, at: 0 };

async function startReview(opts) {
  RV.courseId = opts.courseId || "";
  RV.lessonId = opts.lessonId || "";
  RV.cram = !!opts.cram;
  RV.done = 0;
  RV.ratings = [0, 0, 0, 0, 0];
  RV.at = Date.now();
  S.view = "review";
  appEl.dataset.view = "review";
  main.innerHTML = `<div class="ml-review"><div class="ml-skel tall"></div></div>`;
  await loadQueue();
  nextCard();
}

async function loadQueue() {
  if (RV.cram && RV.lessonId) {

    const q = await API.call("review.queue", { course_id: RV.courseId, limit: 200 });
    RV.queue = q.cards.filter((c) => c.lesson_id === RV.lessonId);
    if (!RV.queue.length) {
      const all = await API.call("card.list", { course_id: RV.courseId, lesson_id: RV.lessonId });
      const ids = new Set(all.cards.map((c) => c.id));
      RV.queue = q.cards.filter((c) => ids.has(c.id));
    }
  } else {
    RV.queue = (await API.call("review.queue", { course_id: RV.courseId, limit: 200 })).cards;
  }
  RV.counts = countQueue();
}
function countQueue() {
  const now = Date.now() / 1000;
  const c = { new: 0, learning: 0, review: 0 };
  RV.queue.forEach((x) => { if (x.state === 0) c.new++; else if (x.state === 2) c.review++; else if (x.due <= now + 1200) c.learning++; });
  return c;
}

function nextCard() {
  const now = Date.now() / 1000;

  let idx = RV.queue.findIndex((c) => !(c.state === 1 || c.state === 3) || c.due <= now + 1);
  if (idx < 0 && RV.queue.length) idx = 0;
  if (idx < 0) return finishReview();
  RV.card = RV.queue.splice(idx, 1)[0];
  RV.shown = false;
  RV.started = Date.now();
  drawCard();
}

function drawCard() {
  const c = RV.card;
  const total = RV.done + RV.queue.length + 1;
  const counts = countQueue();
  const cloze = c.kind === "cloze";
  main.innerHTML = `<div class="ml-review">
    <header class="ml-rv-head">
      <button type="button" class="ml-icon-btn" id="rv-close" title="${esc(t("review.close"))}" aria-label="${esc(t("review.close"))}">${icon("x")}</button>
      <div class="ml-rv-progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${RV.done}"><i style="width:${Math.round(RV.done / total * 100)}%"></i></div>
      <div class="ml-rv-counts" title="${esc(t("review.counts"))}"><span class="new">${num(counts.new + (c.state === 0 ? 1 : 0))}</span><span class="lrn">${num(counts.learning + (c.state === 1 || c.state === 3 ? 1 : 0))}</span><span class="rev">${num(counts.review + (c.state === 2 ? 1 : 0))}</span></div>
      <button type="button" class="ml-icon-btn" id="rv-undo" title="${esc(t("review.undo"))} (Ctrl+Z)" aria-label="${esc(t("review.undo"))}"${RV.done ? "" : " disabled"}>${icon("undo")}</button>
    </header>
    <div class="ml-rv-stage">
      <article class="ml-flash${RV.shown ? " shown" : ""}" id="rv-card">
        <span class="ml-flash-tag">${esc(t(c.state === 0 ? "card.new" : c.state === 2 ? "q.review" : "q.learning"))}${c.tags ? ` · ${esc(c.tags.split(/\s+/)[0].replace(/^weak:/, ""))}` : ""}</span>
        <div class="ml-flash-q ml-prose" id="rv-q"></div>
        <div class="ml-flash-a" id="rv-a"${RV.shown ? "" : " hidden"}><hr><div class="ml-prose" id="rv-a-body"></div></div>
      </article>
    </div>
    <footer class="ml-rv-foot" id="rv-foot"></footer>
  </div>`;
  MD.into($("#rv-q"), c.front, cloze ? { cloze: { ord: c.ord, reveal: RV.shown } } : null);
  if (RV.shown) MD.into($("#rv-a-body"), cloze ? (c.extra || c.back || "") : [c.back, c.extra].filter(Boolean).join("\n\n"));
  if (cloze && RV.shown && !(c.extra || c.back)) $("#rv-a").hidden = true;
  drawFoot();
  $("#rv-close").onclick = () => finishReview(true);
  $("#rv-undo").onclick = undoReview;
}

function drawFoot() {
  const foot = $("#rv-foot");
  if (!RV.shown) {
    foot.innerHTML = `<button type="button" class="ml-btn primary big ml-show" id="rv-show"><span>${esc(t("review.show"))}</span><kbd>${esc(t("key.space"))}</kbd></button>`;
    $("#rv-show").onclick = showAnswer;
    $("#rv-show").focus({ preventScroll: true });
    return;
  }
  const names = ["again", "hard", "good", "easy"];
  foot.innerHTML = `<div class="ml-grades">${(RV.card.buttons || []).map((b, i) => `<button type="button" class="ml-grade ${names[i]}" data-rating="${b.rating}">
      <small>${esc(b.label)}</small><b>${esc(t("grade." + names[i]))}</b><kbd>${b.rating}</kbd></button>`).join("")}</div>`;
  $$(".ml-grade", foot).forEach((b) => b.onclick = () => answer(+b.dataset.rating));
  const good = $(".ml-grade.good", foot);
  if (good) good.focus({ preventScroll: true });
}

function showAnswer() {
  if (RV.shown || !RV.card) return;
  RV.shown = true;
  drawCard();
}

async function answer(rating) {
  if (!RV.card || !RV.shown || RV.sending) return;
  RV.sending = true;
  const card = RV.card;
  try {
    const r = await API.call("review.answer", { id: card.id, rating, duration_ms: Math.min(120000, Date.now() - RV.started), device: "MemuLearn" });
    RV.done++;
    RV.ratings[rating]++;
    RV.last = card;

    if ((r.state === 1 || r.state === 3) && r.due - Date.now() / 1000 < 3600 * 4) {
      const again = await API.call("review.queue", { course_id: RV.courseId, limit: 200 });
      const fresh = again.cards.find((x) => x.id === card.id);
      if (fresh) RV.queue.push(fresh);
    }
    nextCard();
  } catch (e) { fail(e); } finally { RV.sending = false; }
}

async function undoReview() {
  if (!RV.done) return;
  try {
    const card = await API.call("review.undo");
    RV.queue = RV.queue.filter((c) => c.id !== card.id);
    if (RV.card) RV.queue.unshift(RV.card);
    const q = await API.call("review.queue", { course_id: RV.courseId, limit: 200 });
    RV.card = q.cards.find((c) => c.id === card.id) || Object.assign(card, { buttons: [] });
    RV.done = Math.max(0, RV.done - 1);
    RV.shown = false;
    RV.started = Date.now();
    drawCard();
    toast(t("review.undone"));
  } catch (e) { fail(e); }
}

function reviewKey(e) {
  if (e.target.closest && e.target.closest("input, textarea, .ml-dialog")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); return undoReview(); }
  if (e.key === "Escape") { e.preventDefault(); return finishReview(true); }
  if (!RV.shown && (e.key === " " || e.key === "Enter")) { e.preventDefault(); return showAnswer(); }
  if (RV.shown && /^[1-4]$/.test(e.key)) { e.preventDefault(); return answer(+e.key); }
  if (RV.shown && e.key === " ") { e.preventDefault(); return answer(3); }
}

async function finishReview(early) {
  RV.card = null;
  const minutes = Math.max(1, Math.round((Date.now() - RV.at) / 60000));
  const right = RV.ratings[2] + RV.ratings[3] + RV.ratings[4];
  const ov = await refreshSide();
  const left = (ov.today || {}).total || 0;
  if (!RV.done) return go(RV.courseId ? "course" : "home", { id: RV.courseId });
  main.innerHTML = `<div class="ml-review"><div class="ml-rv-done">
    <span class="ml-done-ico">${icon(early ? "check" : "spark")}</span>
    <h2>${esc(t(early ? "review.paused" : "review.done"))}</h2>
    <p class="ml-muted">${esc(t("review.summary", { n: plural(RV.done, "count.cards"), m: fmtMinutes(minutes) }))}</p>
    <div class="ml-done-stats">
      <div><b>${num(Math.round(right / RV.done * 100))}%</b><small>${esc(t("review.right"))}</small></div>
      ${["again", "hard", "good", "easy"].map((k, i) => `<div class="g-${k}"><b>${num(RV.ratings[i + 1])}</b><small>${esc(t("grade." + k))}</small></div>`).join("")}
    </div>
    <div class="ml-row center">
      ${left ? `<button type="button" class="ml-btn primary" id="rv-more">${icon("play")}<span>${esc(t("review.more", { n: num(left) }))}</span></button>` : ""}
      <button type="button" class="ml-btn" id="rv-back">${esc(t(RV.courseId ? "review.to_course" : "review.to_home"))}</button>
    </div></div></div>`;
  const more = $("#rv-more");
  if (more) { more.onclick = () => startReview({}); more.focus(); }
  $("#rv-back").onclick = () => go(RV.courseId ? "course" : "home", { id: RV.courseId });
  S.view = "review-done";
  appEl.dataset.view = "review";
}
