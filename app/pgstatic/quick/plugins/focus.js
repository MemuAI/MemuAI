/* Quick Ask plugin: «Время и фокус» (Round 27, r27-reflect).

   Uses only the MemuQuick contract (events open/submit, registerPanel):
   * opening Quick Ask during the user's quiet hours shows a calm strip «Сейчас ваши тихие часы» with
     «Продолжить» (hides it until the window ends) and «Позже» (closes the window) — a reminder, never a lock;
   * every request pings /api/focus/activity: the day's time with MemuAI is shared with the main window
     and the Telegram bot (app/reflect.py FocusTracker) and answers break_due once the user's break threshold is reached.
   Settings live in MemuAI → Settings → «Время и фокус». */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  let panel = null, mode = "", ackUntil = 0;

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = {
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    cup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V9z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17M8 3v3M12 3v3"/></svg>',
  };

  function ensurePanel() {
    if (panel) return panel;
    panel = Q.registerPanel("focus", (el) => { el.classList.add("qf-host"); return {}; }, { slot: "top", order: -10, hidden: true });
    return panel;
  }

  function show(kind, title, text, buttons) {
    const p = ensurePanel();
    mode = kind;
    p.el.innerHTML = `<div class="qf-card" data-kind="${kind}" role="status"><span class="qf-icon" aria-hidden="true">${kind === "break" ? ICON.cup : ICON.moon}</span>
      <div class="qf-body"><b>${esc(title)}</b><span>${esc(text)}</span></div>
      <div class="qf-actions">${buttons.map((b, i) => `<button type="button" class="qf-btn${b.primary ? " primary" : ""}" data-i="${i}">${esc(b.label)}</button>`).join("")}</div></div>`;
    p.el.querySelectorAll("[data-i]").forEach((btn) => btn.onclick = () => { hide(); buttons[+btn.dataset.i].run(); });
    p.show();
  }
  function hide() { if (panel) panel.hide(); mode = ""; }

  async function state() {
    try { return await Q.api("/api/focus/state"); } catch (e) { return null; }
  }
  async function activity() {
    try { return await Q.api("/api/focus/activity", { method: "POST", body: {} }); } catch (e) { return null; }
  }
  function breakAction(action) { Q.api("/api/focus/break", { method: "POST", body: { action } }).catch(() => {}); }

  function quiet(q) {
    if (!q || Date.now() < ackUntil) { if (mode === "quiet") hide(); return; }
    show("quiet", t("Сейчас ваши тихие часы", "These are your quiet hours"),
      t(`Вы оставили время до ${q.until} для другого. Можно продолжить — это только напоминание.`, `You set aside the time until ${q.until} for something else. You can continue — it's only a reminder.`), [
        { label: t("Продолжить", "Continue"), primary: true, run: () => { ackUntil = q.ends_at; Q.setInput(Q.getInput()); } },
        { label: t("Позже", "Later"), run: () => Q.hide() },
      ]);
  }
  function spent(m, lang) {
    const h = Math.floor(m / 60), r = m % 60;
    if (lang === "en") return h ? `${h} h${r ? " " + r + " min" : ""}` : `${r} min`;
    return h ? `${h} ч${r ? " " + r + " мин" : ""}` : `${r} мин`;
  }
  function breakDue(minutes) {
    show("break", t("Пора сделать перерыв?", "Time for a break?"),
      t(`Сегодня вы уже ${spent(minutes, "ru")} с MemuAI.`, `You've spent ${spent(minutes, "en")} with MemuAI today.`), [
        { label: t("Сделаю перерыв", "Take a break"), primary: true, run: () => { breakAction("break"); Q.hide(); } },
        { label: t("Через 10 мин", "In 10 min"), run: () => breakAction("snooze") },
        { label: t("Скрыть", "Dismiss"), run: () => breakAction("dismiss") },
      ]);
  }

  Q.on("open", async () => {
    const s = await state();
    if (!s) return;
    quiet(s.quiet);
    if (s.break_enabled) {
      const a = await activity();
      if (a && a.break_due) breakDue(a.minutes_today);
    }
  });
  Q.on("submit", async () => {
    if (mode === "quiet") hide();          // sending is the answer to the reminder
    const a = await activity();
    if (a && a.break_due) breakDue(a.minutes_today);
  });
  window.MemuQuickFocus = { quiet, breakDue, hide };
})();
