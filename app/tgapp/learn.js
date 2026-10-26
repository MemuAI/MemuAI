

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, $$, esc, tr, icon, ICONS, haptic, toast, api, md } = kit;
    const P = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
    Object.assign(ICONS, {
      learn: P('<path d="M12 4 2.5 8.5 12 13l9.5-4.5z"/><path d="M6 10.6V15c0 1.7 2.7 3 6 3s6-1.3 6-3v-4.4"/><path d="M21.5 8.5V14"/>'),
      lnUndo: P('<path d="M9 7 4 12l5 5"/><path d="M4 12h10a6 6 0 0 1 0 12"/>'),
      lnFlame: P('<path d="M12 21c4 0 7-2.7 7-6.6 0-4-3.3-6.2-4.5-10.4-2 1.6-3 3.6-3 5.6-1-.8-1.7-2-2-3.3C7.7 8 5 10.8 5 14.4 5 18.3 8 21 12 21z"/>'),
    });
    const L = { on: false, digest: null, queue: [], card: null, shown: false, started: 0, done: 0, right: 0, course: "", root: null };
    const plural = (n, one, few, many) => { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; };
    const cardsWord = (n) => S.lang === "en" ? `${n} ${n === 1 ? "card" : "cards"}` : `${n} ${plural(n, "карточка", "карточки", "карточек")}`;
    const NAMES = [["again", "Снова", "Again"], ["hard", "Трудно", "Hard"], ["good", "Хорошо", "Good"], ["easy", "Легко", "Easy"]];

    function cardHtml(text, card, reveal) {
      let src = String(text || "");
      if (card.kind === "cloze") {
        src = src.replace(/\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g, (_, n, answer, hint) =>
          +n !== +card.ord ? answer : reveal ? `**⟦${answer}⟧**` : `**[${hint || "…"}]**`);
      }
      if (/<(br|div|p|b|i|img|span|ul|li)\b/i.test(src)) src = src.replace(/<br\s*\/?>/gi, "\n").replace(/<[^>]+>/g, "");
      src = src.replace(/\$\$([\s\S]+?)\$\$/g, "\n```tex\n$1\n```\n").replace(/(?<![\\$\w])\$(?!\s)([^$\n]+?)\$/g, "`$1`");
      return md(src);
    }

    function setTop() { kit.setTop(tr("Тренажёр", "Trainer"), ""); }
    async function renderTab(view) {
      L.root = view;
      view.classList.add("ln-root");
      setTop();
      view.innerHTML = `<div class="ln-skel"></div>`;
      try {
        const st = await api("learn.state", {});
        L.digest = st.digest || null;
        drawHome();
      } catch (e) { view.innerHTML = `<div class="ln-empty"><b>${esc(e.message)}</b></div>`; }
    }

    function drawHome() {
      const d = L.digest || { due: 0, courses: [] };
      const view = L.root;
      if (!d.courses || !d.courses.length) {
        view.innerHTML = `<div class="ln-empty">${icon("learn")}<b>${esc(tr("Пока нет курсов", "No courses yet"))}</b>
          <span>${esc(tr("Создайте курс в MemuLearn на компьютере — карточки появятся здесь.", "Create a course in MemuLearn on the computer — its cards show up here."))}</span></div>`;
        return;
      }
      const c = d.counts || {};
      view.innerHTML = `<section class="ln-hero">
          <span class="ln-eyebrow">${esc(tr("Повторить сегодня", "Review today"))}</span>
          <div class="ln-big"><b>${d.due}</b><span>${esc(d.due ? cardsWord(d.due).replace(/^\d+\s/, "") : tr("ничего не ждёт", "nothing due"))}${d.due ? `<small>≈${Math.max(1, d.minutes)} ${esc(tr("мин", "min"))}</small>` : ""}</span></div>
          ${d.due ? `<div class="ln-split">${[["n", c.new], ["l", c.learning], ["r", c.review]].map(([k, v]) => v ? `<i class="${k}" style="flex:${v}"></i>` : "").join("")}</div>` : ""}
          <div class="ln-meta">${d.streak ? `<span>${icon("lnFlame")}${d.streak} ${esc(S.lang === "en" ? "day streak" : plural(d.streak, "день подряд", "дня подряд", "дней подряд"))}</span>` : ""}
            ${d.retention != null ? `<span>${Math.round(d.retention * 100)}% ${esc(tr("удержание", "retention"))}</span>` : ""}</div>
          <button class="btn primary ln-start" id="ln-start"${d.due ? "" : " disabled"}>${esc(d.due ? tr("Начать", "Start") : tr("На сегодня всё", "All done for today"))}</button>
        </section>
        <h3 class="ln-h">${esc(tr("Курсы", "Courses"))}</h3>
        <div class="ln-courses">${d.courses.map((x) => `<button class="ln-course" data-course="${esc(x.id)}" style="--h:${+x.hue || 210}">
          <i class="ln-dot"></i><span><b>${esc(x.title)}</b><small>${esc(cardsWord(x.cards))}${x.lessons ? " · " + x.lessons_done + "/" + x.lessons + tr(" уроков", " lessons") : ""}</small></span>
          ${x.due ? `<em>${x.due}</em>` : `<em class="ok">${x.new ? esc(x.new + tr(" нов.", " new")) : "✓"}</em>`}</button>`).join("")}</div>`;
      const start = $("#ln-start", view);
      if (start) start.onclick = () => startSession("");
      $$(".ln-course", view).forEach((b) => b.onclick = () => startSession(b.dataset.course));
    }

    async function startSession(course) {
      haptic("light");
      L.course = course; L.done = 0; L.right = 0;
      try {
        const q = await api("learn.queue", { course_id: course });
        L.queue = q.cards || [];
      } catch (e) { return toast(e.message, "err"); }
      if (!L.queue.length) return toast(tr("Здесь нечего повторять", "Nothing to review here"));
      next();
    }
    function next() {
      const now = Date.now() / 1000;
      let i = L.queue.findIndex((c) => !(c.state === 1 || c.state === 3) || c.due <= now + 1);
      if (i < 0 && L.queue.length) i = 0;
      if (i < 0) return finish();
      L.card = L.queue.splice(i, 1)[0];
      L.shown = false;
      L.started = Date.now();
      drawCard();
    }
    function drawCard() {
      const c = L.card, view = L.root, total = L.done + L.queue.length + 1;
      const back = c.kind === "cloze" ? (c.extra || c.back || "") : [c.back, c.extra].filter(Boolean).join("\n\n");
      view.innerHTML = `<div class="ln-session">
        <div class="ln-bar"><button class="icon-btn" id="ln-close" aria-label="${esc(tr("Закончить", "Finish"))}">${icon("close")}</button>
          <div class="ln-progress"><i style="width:${Math.round(L.done / total * 100)}%"></i></div>
          <button class="icon-btn" id="ln-undo" aria-label="${esc(tr("Отменить", "Undo"))}"${L.done ? "" : " disabled"}>${icon("lnUndo")}</button></div>
        <article class="ln-card${L.shown ? " shown" : ""}" id="ln-card">
          <span class="ln-tag">${esc(c.state === 0 ? tr("новая", "new") : c.state === 2 ? tr("повторение", "review") : tr("изучаю", "learning"))}</span>
          <div class="ln-q msg-md">${cardHtml(c.front, c, L.shown)}</div>
          ${L.shown && back ? `<hr><div class="ln-a msg-md">${cardHtml(back, c, true)}</div>` : ""}
          ${L.shown ? "" : `<span class="ln-tap">${esc(tr("Нажмите, чтобы увидеть ответ", "Tap to see the answer"))}</span>`}
        </article>
        <div class="ln-grades"${L.shown ? "" : " hidden"}>${(c.buttons || []).map((b, i) => `<button class="ln-grade ${NAMES[i][0]}" data-r="${b.rating}"><small>${esc(b.label)}</small><b>${esc(tr(NAMES[i][1], NAMES[i][2]))}</b></button>`).join("")}</div>
      </div>`;
      $("#ln-card", view).onclick = () => { if (!L.shown) { L.shown = true; haptic("light"); drawCard(); } };
      $$(".ln-grade", view).forEach((b) => b.onclick = () => answer(+b.dataset.r));
      $("#ln-close", view).onclick = finish;
      $("#ln-undo", view).onclick = undo;
    }
    async function answer(rating) {
      if (L.busy) return;
      L.busy = true;
      haptic(rating === 1 ? "warning" : "success");
      const card = L.card;
      try {
        const r = await api("learn.answer", { id: card.id, rating, duration_ms: Math.min(120000, Date.now() - L.started) });
        L.done++;
        if (rating > 1) L.right++;
        if ((r.state === 1 || r.state === 3) && r.due - Date.now() / 1000 < 4 * 3600) {
          const q = await api("learn.queue", { course_id: L.course });
          const fresh = (q.cards || []).find((x) => x.id === card.id);
          if (fresh) L.queue.push(fresh);
        }
        next();
      } catch (e) { toast(e.message, "err"); } finally { L.busy = false; }
    }
    async function undo() {
      try {
        const r = await api("learn.undo", {});
        const q = await api("learn.queue", { course_id: L.course });
        const back = (q.cards || []).find((x) => x.id === r.id);
        if (L.card) L.queue.unshift(L.card);
        if (back) { L.queue = L.queue.filter((x) => x.id !== back.id); L.card = back; }
        L.done = Math.max(0, L.done - 1);
        L.shown = false;
        L.started = Date.now();
        haptic("light");
        drawCard();
      } catch (e) { toast(e.message, "err"); }
    }
    async function finish() {
      L.card = null;
      const view = L.root;
      if (L.done) {
        view.innerHTML = `<div class="ln-done">${icon("learn")}<b>${esc(tr("Сессия завершена", "Session complete"))}</b>
          <span>${esc(cardsWord(L.done))} · ${Math.round(L.right / L.done * 100)}% ${esc(tr("верно", "recalled"))}</span>
          <button class="btn primary" id="ln-home">${esc(tr("Готово", "Done"))}</button></div>`;
        $("#ln-home", view).onclick = () => renderTab(view);
        haptic("success");
      } else renderTab(view);
    }

    kit.addTab({ id: "learn", icon: "learn", ru: "Тренажёр", en: "Trainer", before: "modules", when: () => L.on }, renderTab);
    kit.hooks.afterBoot.push(() => {
      api("learn.state", {}).then((st) => {
        L.on = !!(st && st.enabled);
        L.digest = (st && st.digest) || null;
        if (L.on) kit.renderTabbar();
      }).catch(() => {  });
    });
  });
})();
