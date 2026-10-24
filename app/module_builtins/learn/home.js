/* MemuLearn · home.js — боковая панель курсов и экран «Сегодня»: сколько повторить (карточки · минуты),
 * серия дней, неделя, курсы с прогрессом. Одно чтение overview при входе, без опросов. */
async function refreshSide() {
  try { S.overview = await API.call("overview"); } catch (e) { S.overview = { courses: [], today: { total: 0 }, minutes: 0, streak: 0 }; fail(e); }
  const list = $("#side-courses");
  const courses = S.overview.courses || [];
  list.innerHTML = courses.length ? courses.map((c) => `<button type="button" class="ml-course-link${S.view === "course" && S.course && S.course.id === c.id ? " on" : ""}" data-go="course" data-course="${esc(c.id)}" style="${hueStyle(c.hue)}">
      <i class="ml-dot"></i><span>${esc(c.title)}</span>${c.due ? `<em>${num(c.due)}</em>` : ""}</button>`).join("")
    : `<p class="ml-side-empty">${esc(t("side.empty"))}</p>`;
  const due = (S.overview.today || {}).total || 0;
  const badge = $("#nav-due");
  badge.hidden = !due;
  badge.textContent = num(due);
  return S.overview;
}

function weekBars(values) {
  const max = Math.max(1, ...values);
  const days = values.map((v, i) => {
    const d = new Date(Date.now() - (values.length - 1 - i) * 86400000);
    return `<span class="ml-bar${i === values.length - 1 ? " today" : ""}" title="${esc(Memu.i18n.date(d, { weekday: "short", day: "numeric" }))}: ${num(v)}">
      <i style="height:${Math.max(6, Math.round(v / max * 100))}%"></i><small>${esc(Memu.i18n.date(d, { weekday: "narrow" }))}</small></span>`;
  });
  return `<div class="ml-week">${days.join("")}</div>`;
}

VIEWS.home = async () => {
  const ov = await refreshSide();
  const stats = await API.call("stats", { days: 7 }).catch(() => ({ by_day: [0, 0, 0, 0, 0, 0, 0], retention: null, streak: 0 }));
  const today = ov.today || { total: 0, new: 0, review: 0, learning: 0 };
  const courses = ov.courses || [];
  const hour = new Date().getHours();
  const greet = t(hour < 5 ? "greet.night" : hour < 12 ? "greet.morning" : hour < 18 ? "greet.day" : "greet.evening");
  const hero = today.total
    ? `<div class="ml-hero-main">
        <span class="ml-eyebrow">${esc(t("today.title"))}</span>
        <h1>${esc(plural(today.total, "count.cards"))}<span class="ml-sep">·</span><span class="ml-soft">${esc(fmtMinutes(ov.minutes || 1))}</span></h1>
        <p class="ml-split">${today.review ? `<span><i class="ml-k rev"></i>${esc(t("q.review"))} <b>${num(today.review)}</b></span>` : ""}${today.learning ? `<span><i class="ml-k lrn"></i>${esc(t("q.learning"))} <b>${num(today.learning)}</b></span>` : ""}${today.new ? `<span><i class="ml-k new"></i>${esc(t("q.new"))} <b>${num(today.new)}</b></span>` : ""}</p>
        <button type="button" class="ml-btn primary big" id="start-review">${icon("play")}<span>${esc(t("today.start"))}</span><kbd>Enter</kbd></button>
      </div>`
    : `<div class="ml-hero-main">
        <span class="ml-eyebrow">${esc(t("today.title"))}</span>
        <h1>${esc(courses.length ? t("today.done") : t("today.welcome"))}</h1>
        <p class="ml-muted">${esc(courses.length ? t("today.done_hint") : t("today.welcome_hint"))}</p>
        ${courses.length ? "" : `<button type="button" class="ml-btn primary big" data-go="create">${icon("spark")}<span>${esc(t("course.new"))}</span></button>`}
      </div>`;
  const streak = stats.streak || ov.streak || 0;
  main.innerHTML = `<div class="ml-page ml-home">
    <header class="ml-page-head"><div><span class="ml-eyebrow">${esc(greet)}</span><h2>${esc(t("view.home"))}</h2></div>
      <div class="ml-head-actions"><button type="button" class="ml-btn" data-go="create">${icon("plus")}<span>${esc(t("course.new"))}</span></button></div></header>
    <section class="ml-hero${today.total ? "" : " calm"}">
      ${hero}
      <div class="ml-hero-side">
        <div class="ml-stat"><span class="ml-stat-ico flame">${icon("flame")}</span><div><b>${num(streak)}</b><small>${esc(t("today.streak"))}</small></div></div>
        <div class="ml-stat"><span class="ml-stat-ico">${icon("check")}</span><div><b>${num(ov.reviewed_today || 0)}</b><small>${esc(t("today.reviewed"))}</small></div></div>
        <div class="ml-stat"><span class="ml-stat-ico">${icon("spark")}</span><div><b>${stats.retention == null ? "—" : num(Math.round(stats.retention * 100)) + "%"}</b><small>${esc(t("today.retention"))}</small></div></div>
        ${weekBars(stats.by_day || [])}
      </div>
    </section>
    <div class="ml-section-head"><h3>${esc(t("home.courses"))}</h3>${courses.length ? `<span class="ml-muted">${esc(plural(courses.length, "count.courses"))}</span>` : ""}</div>
    ${courses.length ? `<div class="ml-grid">${courses.map(courseCard).join("")}<button type="button" class="ml-course-card add" data-go="create">${icon("plus")}<b>${esc(t("course.new"))}</b><small>${esc(t("create.sub"))}</small></button></div>`
      : `<div class="ml-empty-sources">${["topic", "book", "note", "anki"].map((k) => `<button type="button" class="ml-source-tile" data-go="create" data-src="${k}">${icon(k === "book" ? "book" : k)}<b>${esc(t("src." + k))}</b><small>${esc(t("src." + k + ".hint"))}</small></button>`).join("")}</div>`}
  </div>`;
  const start = $("#start-review");
  if (start) { start.onclick = () => startReview({}); start.focus({ preventScroll: true }); }
  $$(".ml-source-tile").forEach((b) => b.addEventListener("click", () => { S.createSource = b.dataset.src; }, true));
};

function courseCard(c) {
  const progress = c.lessons ? c.lessons_done / c.lessons : 0;
  const mastery = c.cards ? (c.cards - c.new) / c.cards : 0;
  return `<button type="button" class="ml-course-card" data-go="course" data-course="${esc(c.id)}" style="${hueStyle(c.hue)}">
    <span class="ml-cc-top"><span class="ml-cc-ico">${icon(c.source_kind === "anki" ? "anki" : c.source_kind === "file" ? "book" : c.source_kind === "note" ? "note" : "spark")}</span>
      ${c.due ? `<span class="ml-pill due">${esc(t("course.due", { n: num(c.due) }))}</span>` : `<span class="ml-pill">${esc(t("course.ok"))}</span>`}</span>
    <b class="ml-cc-title">${esc(c.title)}</b>
    <small class="ml-cc-desc">${esc(c.description || t("src." + (c.source_kind === "file" ? "book" : c.source_kind || "topic")))}</small>
    <span class="ml-cc-meta">
      <span class="ml-cc-ring">${ring(mastery, 34, 4)}<i>${num(Math.round(mastery * 100))}%</i></span>
      <span><b>${num(c.cards)}</b> ${esc(t("course.cards_short"))}${c.lessons ? ` · <b>${num(c.lessons_done)}/${num(c.lessons)}</b> ${esc(t("course.lessons_short"))}` : ""}</span>
    </span>
    <span class="ml-cc-progress"><i style="width:${Math.round(progress * 100)}%"></i></span>
  </button>`;
}
