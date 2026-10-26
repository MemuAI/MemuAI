

function barChart(values, opts) {
  const max = Math.max(1, ...values);
  return `<div class="ml-chart${opts && opts.future ? " future" : ""}">${values.map((v, i) => `<span title="${esc(opts.label(i))}: ${num(v)}"><i style="height:${Math.max(v ? 4 : 2, Math.round(v / max * 100))}%"></i></span>`).join("")}</div>`;
}

VIEWS.stats = async () => {
  const s = await API.call("stats", { days: 30 });
  const total = s.states.new + s.states.learning + s.states.review;
  const seg = (k, v) => total ? `<i class="ml-seg-${k}" style="flex:${v}"></i>` : "";
  const day = (offset) => Memu.i18n.date(Date.now() + offset * 86400000, { day: "numeric", month: "short" });
  main.innerHTML = `<div class="ml-page ml-stats">
    <header class="ml-page-head"><div><span class="ml-eyebrow">MemuLearn</span><h2>${esc(t("view.stats"))}</h2></div></header>
    <div class="ml-kpis">
      <div class="ml-kpi"><small>${esc(t("stats.reviews30"))}</small><b>${num(s.reviews)}</b></div>
      <div class="ml-kpi"><small>${esc(t("stats.retention"))}</small><b>${s.retention == null ? "—" : num(Math.round(s.retention * 100)) + "%"}</b></div>
      <div class="ml-kpi"><small>${esc(t("stats.streak"))}</small><b>${esc(plural(s.streak, "count.days"))}</b></div>
      <div class="ml-kpi"><small>${esc(t("stats.cards"))}</small><b>${num(total)}</b></div>
    </div>
    <section class="ml-card"><div class="ml-section-head"><h4>${esc(t("stats.history"))}</h4><span class="ml-muted small">${esc(t("stats.days30"))}</span></div>
      ${barChart(s.by_day, { label: (i) => day(i - s.by_day.length + 1) })}</section>
    <section class="ml-card"><div class="ml-section-head"><h4>${esc(t("stats.forecast"))}</h4><span class="ml-muted small">${esc(t("stats.days14"))}</span></div>
      ${barChart(s.forecast, { future: true, label: (i) => day(i) })}</section>
    <section class="ml-card"><div class="ml-section-head"><h4>${esc(t("stats.states"))}</h4></div>
      <div class="ml-stack">${seg("new", s.states.new)}${seg("lrn", s.states.learning)}${seg("rev", s.states.review)}</div>
      <div class="ml-legend"><span><i class="ml-k new"></i>${esc(t("q.new"))} <b>${num(s.states.new)}</b></span><span><i class="ml-k lrn"></i>${esc(t("q.learning"))} <b>${num(s.states.learning)}</b></span><span><i class="ml-k rev"></i>${esc(t("q.review"))} <b>${num(s.states.review)}</b></span></div></section>
  </div>`;
};

VIEWS.settings = async () => {
  const st = await API.call("settings.get");
  if (!S.models.length) await loadModels();
  main.innerHTML = `<div class="ml-page ml-settings">
    <header class="ml-page-head"><div><span class="ml-eyebrow">MemuLearn</span><h2>${esc(t("view.settings"))}</h2></div></header>
    <section class="ml-card ml-set">
      <div class="ml-set-row"><div><b>${esc(t("set.retention"))}</b><small>${esc(t("set.retention_hint"))}</small></div>
        <div class="ml-range"><input type="range" id="st-ret" min="75" max="97" step="1" value="${Math.round(st.desired_retention * 100)}"><b id="st-ret-v">${num(Math.round(st.desired_retention * 100))}%</b></div></div>
      <div class="ml-set-row"><div><b>${esc(t("set.new"))}</b><small>${esc(t("set.new_hint"))}</small></div><input class="ml-input num" type="number" id="st-new" min="0" max="9999" value="${st.new_per_day}"></div>
      <div class="ml-set-row"><div><b>${esc(t("set.max"))}</b><small>${esc(t("set.max_hint"))}</small></div><input class="ml-input num" type="number" id="st-max" min="0" max="9999" value="${st.max_reviews}"></div>
      <div class="ml-set-row"><div><b>${esc(t("set.fuzz"))}</b><small>${esc(t("set.fuzz_hint"))}</small></div><label class="ml-switch"><input type="checkbox" id="st-fuzz"${st.fuzz ? " checked" : ""}><i></i></label></div>
    </section>
    <section class="ml-card ml-set">
      <div class="ml-set-row"><div><b>${esc(t("set.model"))}</b><small>${esc(t("set.model_hint"))}</small></div><select class="ml-input" id="st-model">${modelOptions()}</select></div>
      <div class="ml-set-row"><div><b>${esc(t("set.cards"))}</b><small>${esc(t("set.cards_hint"))}</small></div><input class="ml-input num" type="number" id="st-cards" min="3" max="20" value="${S.prefs.cards_per_lesson || 8}"></div>
      <div class="ml-set-row"><div><b>${esc(t("set.calm"))}</b><small>${esc(t("set.calm_hint"))}</small></div><label class="ml-switch"><input type="checkbox" id="st-calm"${S.prefs.calm ? " checked" : ""}><i></i></label></div>
    </section>
    <p class="ml-muted small">${esc(t("set.fsrs_note"))}</p>
  </div>`;
  const save = (patch) => API.call("settings.set", patch).then(() => toast(t("saved"), "ok"), fail);
  const ret = $("#st-ret");
  ret.oninput = () => { $("#st-ret-v").textContent = num(+ret.value) + "%"; };
  ret.onchange = () => save({ desired_retention: +ret.value / 100 });
  $("#st-new").onchange = (e) => save({ new_per_day: +e.target.value });
  $("#st-max").onchange = (e) => save({ max_reviews: +e.target.value });
  $("#st-fuzz").onchange = (e) => save({ fuzz: e.target.checked });
  $("#st-model").onchange = (e) => { S.model = e.target.value; remember(); toast(t("saved"), "ok"); };
  $("#st-cards").onchange = (e) => { S.prefs.cards_per_lesson = Math.max(3, Math.min(20, +e.target.value || 8)); remember(); toast(t("saved"), "ok"); };
  $("#st-calm").onchange = (e) => { S.prefs.calm = e.target.checked; applyPrefs(); remember(); };
};

async function exportCourse(kind) {
  try {
    if (kind === "csv") {
      const r = await API.call("export.csv", { course_id: S.course.id });
      const saved = await Memu.download({ name: r.name, text: "\ufeff" + r.text });
      toast(t("export.done", { path: (saved && saved.path) || r.name }), "ok");
    } else {
      const r = await API.call("export.apkg", { course_id: S.course.id });
      const saved = await Memu.download({ name: r.name, url: "data:application/octet-stream;base64," + r.data });
      toast(t("export.done", { path: (saved && saved.path) || r.name }), "ok");
    }
  } catch (e) { fail(e); }
}
