

(function () {
  "use strict";
  if (window.MemuStuck) return;

  const SENSITIVITIES = ["off", "low", "normal", "high"];
  const DECISIONS = ["continue", "hint", "replan", "stop"];
  const PROFILES = {
    low: { same_action: 6, hint_at: 3, revisit_repeat: 9, cycles: 4, same_error: 5, no_new_state: 14, waiting: 24, stalled: 7, no_evidence: 7, budget: 24, window: 30 },
    normal: { same_action: 4, hint_at: 2, revisit_repeat: 6, cycles: 3, same_error: 3, no_new_state: 10, waiting: 16, stalled: 5, no_evidence: 5, budget: 18, window: 24 },
    high: { same_action: 3, hint_at: 2, revisit_repeat: 4, cycles: 2, same_error: 2, no_new_state: 7, waiting: 10, stalled: 3, no_evidence: 3, budget: 12, window: 18 },
  };
  const PASSIVE = new Set(["wait", "sleep", "poll", "screenshot", "snapshot", "observe", "status", "read_state"]);
  const IGNORED = new Set(["reason", "report", "thought", "thoughts", "summary", "explanation", "id", "call_id", "tool_call_id",
    "description", "rationale", "comment", "note"]);
  const COORDS = new Set(["x", "y", "x1", "y1", "x2", "y2"]);
  const NAV_KEYS = /^(arrow\w*|up|down|left|right|tab|pagedown|pageup|pgdn|pgup|space|j|k)$/i;
  const REASONS = {
    repeat: ["Повторяется одно и то же действие без результата", "The same action keeps repeating with no effect"],
    oscillation: ["Агент ходит по кругу между одними и теми же шагами", "The agent is going in circles between the same steps"],
    same_error: ["Одна и та же ошибка повторяется", "The same error keeps coming back"],
    no_new_state: ["Несколько шагов подряд ничего нового не происходит", "Several steps in a row bring nothing new"],
    waiting: ["Ожидание затянулось: состояние давно не меняется", "The wait drags on: nothing has changed for a long time"],
    plan_stalled: ["Шаг плана не продвигается", "The plan step is not moving forward"],
    no_evidence: ["Модель сообщает о прогрессе, но ничего не меняется", "The model reports progress, but nothing actually changes"],
    budget: ["Много шагов без новых результатов", "Many steps without any new result"],
    model_signal: ["Модель сама сообщила, что застряла", "The model itself said it is stuck"],
  };
  const MODEL_HINTS = {
    repeat: "You are repeating the same action and nothing changes. Do something different or finish.",
    oscillation: "You are alternating between the same few actions and states. Stop the cycle: choose a different approach or finish.",
    same_error: "The same error keeps happening. Do not retry the same way: change the approach.",
    no_new_state: "Your last steps did not reach anything new. Change the approach.",
    waiting: "Waiting has not changed anything for a long time. Do something else or finish.",
    plan_stalled: "The current plan step has not advanced. Verify what blocks it and take a different action.",
    no_evidence: "You keep reporting progress without any observable change. Produce verifiable results.",
    budget: "Many steps without new results. Re-evaluate the approach.",
    model_signal: "",
  };

  const isEn = (lang) => String(lang || window.PG_LANG || (typeof PG_LANG !== "undefined" ? PG_LANG : "") || document.documentElement.lang || "ru").toLowerCase().startsWith("en");
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const normText = (v, limit = 300) => String(v == null ? "" : v).replace(/\s+/g, " ").trim().toLowerCase().slice(0, limit);
  function normValue(key, v) {
    if (COORDS.has(key)) { const n = Number(v); return Number.isFinite(n) ? Math.round(n / 12) : normText(v); }
    if (v === null || v === undefined || typeof v === "boolean") return v ?? null;
    if (typeof v === "number") return Math.round(v * 100) / 100;
    if (Array.isArray(v)) return v.slice(0, 50).map((x) => normValue("", x));
    if (typeof v === "object") {
      const out = {};
      Object.keys(v).sort().forEach((k) => { if (!IGNORED.has(k)) out[k] = normValue(k, v[k]); });
      return out;
    }
    return normText(v);
  }
  function stable(v) {
    if (Array.isArray(v)) return "[" + v.map(stable).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + stable(v[k])).join(",") + "}";
    return JSON.stringify(v === undefined ? null : v);
  }

  function hash(str) {
    let a = 0x811c9dc5, b = 0x01000193 ^ 0x5bd1e995;
    for (let i = 0; i < str.length; i++) {
      const c = str.charCodeAt(i);
      a = Math.imul(a ^ c, 0x01000193) >>> 0;
      b = Math.imul(b ^ (c + i), 0x01000193) >>> 0;
    }
    return a.toString(16).padStart(8, "0") + b.toString(16).padStart(8, "0");
  }
  const signature = (action) => hash(action && typeof action === "object" ? stable(normValue("", action)) : normText(action, 2000));
  const actionKind = (action) => action && typeof action === "object"
    ? normText(action.action || action.tool || action.name || action.kind || "", 60)
    : (action ? normText(action, 60).split(" ")[0] : "");
  function stateKey(state) {
    if (state === null || state === undefined || state === "") return null;
    if (Array.isArray(state) && state.length && state.every((v) => Number.isInteger(v))) return state.slice();
    if (Array.isArray(state) && state.length && state.every(Array.isArray)) {
      const flat = []; state.forEach((item) => item.forEach((v) => { if (typeof v === "number") flat.push(Math.round(v)); }));
      return flat.length ? flat : null;
    }
    if (typeof state === "string" && /^[0-9a-f]{8,64}$/.test(state)) return state;
    return hash(stable(state));
  }
  function sameState(a, b) {
    if (a === null || b === null || a === undefined || b === undefined) return false;
    if (Array.isArray(a) && Array.isArray(b)) {
      if (a.length !== b.length || !a.length) return false;
      let sum = 0; for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
      return sum / a.length < 1.0;
    }
    return a === b;
  }
  const normalizeError = (e) => e ? normText(e, 400).replace(/0x[0-9a-f]+|\b[0-9a-f]{8,}\b/g, "#").replace(/\d+/g, "#").slice(0, 160) : "";
  const progressive = (s) => ["scroll", "scroll_down", "scroll_up", "wheel", "next", "next_page"].includes(s.kind)
    || (["key", "press", "keys"].includes(s.kind) && NAV_KEYS.test(s.key || ""));
  function defaultLabel(action) {
    if (action && typeof action === "object") {
      const args = {}; Object.keys(action).forEach((k) => { if (!IGNORED.has(k) && !["action", "tool", "name"].includes(k)) args[k] = action[k]; });
      const kind = actionKind(action) || "?";
      return Object.keys(args).length ? `${kind} ${JSON.stringify(args)}` : kind;
    }
    return String(action || "");
  }
  const uid = () => Math.random().toString(16).slice(2, 12);

  class Detector {
    constructor(sensitivity = "normal", area = "") {
      this.sensitivity = SENSITIVITIES.includes(sensitivity) ? sensitivity : "normal";
      this.area = area; this.steps = []; this.baseline = 0; this.scale = 1; this.pauses = 0; this.decisions = []; this.seen = [];
    }
    get enabled() { return this.sensitivity !== "off"; }
    limit(name) {
      const base = (PROFILES[this.sensitivity] || PROFILES.normal)[name];
      return name === "hint_at" || name === "window" ? base : Math.max(base, Math.round(base * this.scale));
    }
    observe(action, o = {}) {
      const kind = actionKind(action);
      const key = stateKey(o.state);
      const novel = key !== null && !this.seen.some((old) => sameState(key, old));
      if (novel) { this.seen.push(key); if (this.seen.length > 400) this.seen = this.seen.slice(-400); }
      const step = {
        sig: signature(action), kind, label: String(o.label || defaultLabel(action)).slice(0, 160), state: key,
        passive: o.passive === undefined ? PASSIVE.has(kind) : !!o.passive, error: normalizeError(o.error),
        progress: o.progress === undefined || o.progress === null ? null : (stateKey(o.progress) ?? o.progress),
        progressed: o.progressed === undefined ? null : o.progressed, claimed: !!o.claimed,
        files: Math.max(0, +o.filesChanged || 0), novel,
        key: action && typeof action === "object" ? normText(action.key || action.keys || action.combo || "", 40) : "",
      };
      this.steps.push(step);
      if (this.steps.length > 600) { const cut = this.steps.length - 600; this.steps = this.steps.slice(cut); this.baseline = Math.max(0, this.baseline - cut); }
      if (!this.enabled) return { level: "ok", area: this.area };
      if (o.modelStuck) { const t = String(o.modelReason || "").slice(0, 400); return this.verdict("pause", "model_signal", 1, { detail: t, detailEn: t }); }
      return this.judge();
    }
    noteResult(o = {}) {
      const last = this.steps[this.steps.length - 1]; if (!last) return;
      if (o.error) last.error = normalizeError(o.error);
      if (o.filesChanged) last.files += +o.filesChanged;
      if (o.progressed !== undefined) last.progressed = o.progressed;
    }
    acknowledge(decision) {
      decision = DECISIONS.includes(decision) ? decision : "continue";
      this.decisions.push(decision); this.baseline = this.steps.length;
      if (decision === "continue") this.scale = Math.min(3, this.scale + 0.5);
    }
    window() { return this.steps.slice(this.baseline).slice(-this.limit("window") * 2); }
    judge() {
      const steps = this.window();
      if (!steps.length) return { level: "ok", area: this.area };
      const cur = steps[steps.length - 1];
      let hint = null;
      const W = this.limit("window");

      if (!cur.passive) {
        let run = 1;
        for (let i = steps.length - 2; i >= 0; i--) {
          const prev = steps[i], nxt = steps[i + 1];
          if (prev.sig !== cur.sig || prev.passive) break;
          const unchanged = prev.state !== null && nxt.state !== null ? sameState(prev.state, nxt.state)
            : (prev.state === null && nxt.state === null && !nxt.files && !prev.files && prev.progressed !== true
              && prev.progress === null && nxt.progress === null && !prev.claimed && !nxt.claimed);
          if (!unchanged) break;
          run++;
        }
        const factor = progressive(cur) ? 2 : 1;
        if (run >= this.limit("same_action") * factor) return this.verdict("pause", "repeat", run, { tail: run });
        if (run >= Math.max(2, this.limit("hint_at")) * factor) hint = this.verdict("hint", "repeat", run, { tail: run });
        const same = steps.slice(-W).filter((s) => s.sig === cur.sig && !s.novel && !s.passive && !s.files);
        if (cur.state !== null && !cur.novel && same.length >= this.limit("revisit_repeat") * factor)
          return this.verdict("pause", "repeat", same.length, { tail: Math.min(steps.length, same.length * 2) });
      }

      for (const period of [2, 3]) {
        const cycles = this.limit("cycles"), need = period * cycles;
        if (steps.length < need) continue;
        const tail = steps.slice(-need);
        if (tail.some((s) => s.passive) || new Set(tail.slice(0, period).map((s) => s.sig)).size < 2) continue;
        let ok = true;
        for (let i = period; i < need; i++) {
          const a = tail[i], b = tail[i - period];
          if (a.sig !== b.sig || (a.state !== null && b.state !== null && !sameState(a.state, b.state)) || a.files || a.progressed === true) { ok = false; break; }
        }
        if (ok) return this.verdict("pause", "oscillation", cycles, { tail: need, period });
      }

      const errors = steps.slice(-W).filter((s) => s.error).map((s) => s.error);
      const lastErr = [...steps].reverse().find((s) => s.error)?.error || "";
      if (lastErr && steps.slice(-2).some((s) => s.error === lastErr)) {
        const count = errors.filter((e) => e === lastErr).length;
        if (count >= this.limit("same_error")) return this.verdict("pause", "same_error", count, { tail: Math.min(steps.length, W), detail: lastErr, detailEn: lastErr });
      }

      if (cur.passive && cur.state !== null) {
        let waited = 0;
        for (let i = steps.length - 1; i >= 0; i--) { const s = steps[i]; if (!s.passive || !sameState(s.state, cur.state)) break; waited++; }
        if (waited >= this.limit("waiting")) return this.verdict("pause", "waiting", waited, { tail: waited });
      }

      const active = steps.filter((s) => !s.passive && !progressive(s));
      let streak = 0;
      for (let i = active.length - 1; i >= 0; i--) { const s = active[i]; if (s.state === null || s.novel || s.files || s.progressed === true) break; streak++; }
      if (streak >= this.limit("no_new_state")) return this.verdict("pause", "no_new_state", streak, { tail: streak });

      if (cur.progress !== null) {
        let stalled = 0;
        for (let i = steps.length - 1; i >= 0; i--) { const s = steps[i]; if (s.progress === null || s.progress !== cur.progress || s.files || s.progressed === true) break; stalled++; }
        if (stalled >= this.limit("stalled")) return this.verdict("pause", "plan_stalled", stalled, { tail: stalled });
      }

      let claims = 0;
      for (let i = steps.length - 1; i >= 0; i--) { const s = steps[i]; if (!s.claimed || s.files || s.novel || s.progressed === true) break; claims++; }
      if (claims >= this.limit("no_evidence")) return this.verdict("pause", "no_evidence", claims, { tail: claims });

      if (steps.some((s) => s.state !== null || s.progress !== null || s.progressed !== null)) {
        let since = 0;
        for (let i = steps.length - 1; i >= 0; i--) { const s = steps[i]; if (s.novel || s.files || s.progressed === true) break; since++; }
        if (since >= this.limit("budget")) return this.verdict("pause", "budget", since, { tail: Math.min(since, 10) });
      }
      return hint || { level: "ok", area: this.area };
    }
    verdict(level, reason, count, o = {}) {
      const tail = this.steps.slice(this.baseline).slice(-Math.max(1, Math.min(o.tail || 6, 12)));
      const [detail, detailEn] = o.detail || o.detailEn ? [o.detail || "", o.detailEn || ""] : details(reason, count, o.period);
      if (level === "pause") this.pauses++;
      return { level, reason, count, area: this.area, id: uid(), modelHint: MODEL_HINTS[reason] || "", steps: summarize(tail), step: this.steps.length,
        paused: level === "pause", detail, detailEn };
    }
  }
  const ruTimes = (n) => { n = Math.abs(n | 0); return [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "раза" : "раз"; };
  function details(reason, count, period) {
    const map = {
      repeat: [`Одно и то же действие — ${count} ${ruTimes(count)} подряд, а состояние не меняется.`, `The same action ${count} times in a row while nothing changes.`],
      oscillation: [`Цикл из ${period || 2} шагов повторился ${count} ${ruTimes(count)}.`, `A cycle of ${period || 2} steps repeated ${count} times.`],
      same_error: [`Ошибка повторилась ${count} ${ruTimes(count)}.`, `The error happened ${count} times.`],
      waiting: [`${count} ожиданий подряд, состояние не меняется.`, `${count} waits in a row and nothing changes.`],
      plan_stalled: [`${count} итераций на одном шаге плана без изменений в файлах.`, `${count} iterations on the same plan step without file changes.`],
      no_evidence: [`${count} ${ruTimes(count)} подряд модель говорит «продолжаю», но файлы и состояние не меняются.`, `${count} times in a row the model says “continuing”, but files and state stay the same.`],
      no_new_state: [`${count} шагов подряд без нового состояния.`, `${count} steps in a row without a new state.`],
      budget: [`${count} шагов без новых результатов.`, `${count} steps without new results.`],
    };
    return map[reason] || ["", ""];
  }
  function summarize(steps) {
    const out = []; let last = null;
    steps.forEach((s) => {
      if (out.length && s.sig === last) { out[out.length - 1].count++; if (s.error) out[out.length - 1].error = s.error; return; }
      const row = { label: s.label, count: 1 }; if (s.error) row.error = s.error;
      out.push(row); last = s.sig;
    });
    return out.slice(-8);
  }
  function toCard(v) {
    const pair = REASONS[v.reason] || ["Агент, похоже, застрял", "The agent seems to be stuck"];
    return { id: v.id, kind: "stuck", reason: v.reason, area: v.area, title: pair[0], title_en: pair[1], detail: v.detail, detail_en: v.detailEn,
      steps: v.steps, count: v.count, step: v.step, options: DECISIONS.slice(), created: Date.now() };
  }
  const WORDS = {
    continue: ["continue", "продолжить", "продолжай", "продолжи", "да", "yes", "go on"],
    hint: ["hint", "подсказка", "с подсказкой", "continue with a hint", "продолжить с подсказкой"],
    replan: ["replan", "change approach", "change the approach", "сменить подход", "другой подход", "перепланировать", "re-plan"],
    stop: ["stop", "стоп", "остановить", "остановись", "нет", "no", "cancel", "отмена"],
  };
  function parseDecision(payload) {
    let text = "";
    if (payload && typeof payload === "object") {
      const d = String(payload.decision || "").toLowerCase(); let hint = String(payload.hint || "").trim().slice(0, 2000);
      if (DECISIONS.includes(d)) { if (d === "hint" && !hint) hint = String(payload.answer || "").trim().slice(0, 2000); return { decision: d !== "hint" || hint ? d : "continue", hint }; }
      if (payload.approve === false) return { decision: "stop", hint: "" };
      text = String(payload.answer || "").trim();
    } else text = String(payload || "").trim();
    const low = normText(text, 200);
    for (const d of ["hint", "replan", "stop", "continue"]) if (WORDS[d].includes(low)) return { decision: d === "hint" ? "continue" : d, hint: "" };
    return low ? { decision: "hint", hint: text.slice(0, 2000) } : { decision: "continue", hint: "" };
  }
  function modelNote(decision, hint, card) {
    const rows = (card?.steps || []).slice(-4).map((r) => r.label + (r.count > 1 ? ` ×${r.count}` : "")).join("; ");
    const what = rows ? ` (repeating: ${rows.slice(0, 400)})` : "";
    if (decision === "hint") return `PAUSED as stuck${what}. The user looked at it and gave a hint — follow it: ${String(hint).slice(0, 1500)}`;
    if (decision === "replan") return `PAUSED as stuck${what}. The user asked to CHANGE THE APPROACH: the current strategy did not work. Make a new plan that does not repeat the previous actions, then continue.${hint ? " User note: " + String(hint).slice(0, 1000) : ""}`;
    if (decision === "stop") return `PAUSED as stuck${what}. The user stopped the task.`;
    return `PAUSED as stuck${what}. The user chose to continue: take a fresh look and try again, but do not repeat an action that changed nothing.`;
  }

  const SVG = {
    pause: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M7 5v14l11-7z"/></svg>',
    hint: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/></svg>',
    route: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h7"/></svg>',
    stop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    loop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>',
  };
  const ic = (name) => `<span class="ui-icon memu-stuck-ic" aria-hidden="true">${SVG[name]}</span>`;
  const AREA = {
    work: ["Work", "Work"], plan: ["План Work", "Work plan"], goal: ["Цель Work", "Work goal"], tools: ["Инструменты Work", "Work tools"],
    plan_code: ["План MemuCode", "MemuCode plan"], goal_code: ["Цель MemuCode", "MemuCode goal"], tools_code: ["Инструменты MemuCode", "MemuCode tools"],
    browser: ["Browser", "Browser"], computer: ["Computer Use", "Computer Use"], dfy: ["Do For You", "Do For You"], cli: ["MemuCode", "MemuCode"],
  };

  function cardHtml(card, o = {}) {
    if (!card) return "";
    const en = isEn(o.lang), T = (ru, e) => (en ? e : ru);
    const title = en ? card.title_en || card.title : card.title || card.title_en;
    const detail = en ? card.detail_en || card.detail : card.detail || card.detail_en;
    const area = AREA[o.area || card.area] || null;
    const steps = Array.isArray(card.steps) ? card.steps.slice(-6) : [];
    const disabled = o.busy ? " disabled" : "";
    return `<section class="memu-stuck${o.compact ? " compact" : ""}" role="alertdialog" aria-live="assertive" aria-label="${esc(title)}"
        data-stuck-card="${esc(card.id || "")}" data-stuck-source="${esc(o.source || "")}" data-stuck-task="${esc(o.task || "")}">
      <header class="memu-stuck-head">${ic("pause")}<div class="memu-stuck-titles">
        <b class="memu-stuck-title">${esc(T("Пауза — ", "Paused — "))}${esc(title)}</b>
        <span class="memu-stuck-sub">${area ? `<span class="memu-stuck-area">${esc(en ? area[1] : area[0])}</span>` : ""}${esc(T("Работа приостановлена и ждёт вашего решения. Ничего не выполняется.", "The work is on hold until you decide. Nothing is running."))}</span></div></header>
      ${detail ? `<p class="memu-stuck-detail">${esc(detail)}</p>` : ""}
      ${steps.length ? `<div class="memu-stuck-steps-h">${ic("loop")}${esc(T("Последние шаги", "Last steps"))}</div><ol class="memu-stuck-steps">${steps.map((s) => `<li><span class="memu-stuck-step">${esc(String(s.label || "").slice(0, 160))}</span>${(+s.count || 1) > 1 ? `<span class="memu-stuck-x" title="${esc(T("повторов", "repeats"))}">×${+s.count}</span>` : ""}${s.error ? `<small class="memu-stuck-err">${esc(String(s.error).slice(0, 160))}</small>` : ""}</li>`).join("")}</ol>` : ""}
      <div class="memu-stuck-actions">
        <button type="button" class="memu-stuck-btn primary" data-stuck-act="continue"${disabled}>${ic("play")}${esc(T("Продолжить", "Continue"))}</button>
        <button type="button" class="memu-stuck-btn" data-stuck-act="hint-open"${disabled} aria-expanded="false">${ic("hint")}${esc(T("С подсказкой…", "With a hint…"))}</button>
        <button type="button" class="memu-stuck-btn" data-stuck-act="replan"${disabled}>${ic("route")}${esc(T("Сменить подход", "Change approach"))}</button>
        <button type="button" class="memu-stuck-btn danger" data-stuck-act="stop"${disabled}>${ic("stop")}${esc(T("Остановить", "Stop"))}</button>
      </div>
      <div class="memu-stuck-hint" hidden>
        <textarea rows="2" data-stuck-hint maxlength="2000" placeholder="${esc(T("Что подсказать ИИ? Например: «кнопка поиска справа вверху» или «сначала войди в аккаунт»", "What should the AI know? E.g. “the search button is top right” or “sign in first”"))}"></textarea>
        <div class="memu-stuck-hint-row"><small>${esc(T("Текст попадёт к модели как ваша заметка. Ctrl+Enter — отправить.", "The text reaches the model as your note. Ctrl+Enter sends it."))}</small>
          <button type="button" class="memu-stuck-btn primary" data-stuck-act="hint">${esc(T("Продолжить с подсказкой", "Continue with this hint"))}</button></div>
      </div>
      <small class="memu-stuck-error" aria-live="polite"></small>
    </section>`;
  }
  function dfyQuestionHtml(task, q, o = {}) {
    const card = q.card || { id: q.id, title: q.prompt, title_en: q.prompt_en || q.prompt, steps: [] };
    return `<div class="dfy-q qdfy-q memu-stuck-dfy" data-kind="stuck">${cardHtml(card, { ...o, source: "dfy", task: task.id, area: "dfy", compact: o.compact })}</div>`;
  }

  async function post(path, body) {
    const resp = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), cache: "no-store" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    return data;
  }
  const workResolvers = new Map();

  async function decide(target, decision, hint = "") {
    const source = target.source || "";
    if (!DECISIONS.includes(decision)) throw new Error("bad decision");
    let result;
    if (source === "work") {
      const fn = workResolvers.get(target.card) || resolveWork;
      result = await fn(target.card, decision, hint);
    } else if (source === "dfy") {
      result = await post(`/api/dfy/tasks/${encodeURIComponent(target.task)}/answer`, { decision, hint });
    } else {
      result = await post("/api/stuck/decide", { source, task_id: target.task || "", card_id: target.card || "", decision, hint });
    }
    document.dispatchEvent(new CustomEvent("memu-stuck-decided", { detail: { ...target, decision } }));
    return result;
  }
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest?.("[data-stuck-act]");
    if (!btn || btn.disabled) return;
    const box = btn.closest(".memu-stuck");
    if (!box) return;
    ev.preventDefault(); ev.stopPropagation();
    const act = btn.dataset.stuckAct;
    const hintBox = box.querySelector(".memu-stuck-hint");
    if (act === "hint-open") {
      const open = hintBox.hidden;
      hintBox.hidden = !open; btn.setAttribute("aria-expanded", String(open));
      if (open) setTimeout(() => box.querySelector("[data-stuck-hint]")?.focus(), 0);
      return;
    }
    let hint = "";
    if (act === "hint") {
      hint = String(box.querySelector("[data-stuck-hint]")?.value || "").trim();
      if (!hint) { box.querySelector("[data-stuck-hint]")?.focus(); return; }
    }
    if (act === "stop" && box.dataset.stuckSource !== "dfy") {

      btn.classList.add("confirming");
      if (!btn.dataset.armed) {
        btn.dataset.armed = "1";
        const label = btn.lastChild; const prev = label.textContent;
        label.textContent = isEn() ? "Stop — sure?" : "Точно остановить?";
        setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; label.textContent = prev; btn.classList.remove("confirming"); } }, 3500);
        return;
      }
    }
    box.querySelectorAll("button").forEach((b) => (b.disabled = true));
    box.classList.add("is-busy");
    const err = box.querySelector(".memu-stuck-error");
    try {
      await decide({ source: box.dataset.stuckSource, task: box.dataset.stuckTask, card: box.dataset.stuckCard }, act, hint);
      box.classList.add("is-done");
    } catch (e) {
      if (err) err.textContent = e.message || String(e);
      box.querySelectorAll("button").forEach((b) => (b.disabled = false));
      box.classList.remove("is-busy");
    }
  }, true);
  document.addEventListener("keydown", (ev) => {
    const ta = ev.target.closest?.("[data-stuck-hint]");
    if (!ta) return;
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); ta.closest(".memu-stuck")?.querySelector('[data-stuck-act="hint"]')?.click(); }
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); const box = ta.closest(".memu-stuck-hint"); if (box) box.hidden = true; }
  }, true);

  const pref = (key, def) => {
    try { if (typeof S !== "undefined" && S.preferences && S.preferences[key] !== undefined) return S.preferences[key]; } catch (e) {}
    return def;
  };
  const sensitivity = () => { const v = String(pref("stuck_sensitivity", "normal")); return SENSITIVITIES.includes(v) ? v : "normal"; };
  function settingsHtml(ctx) {
    const en = !!ctx.en, L = (ru, e) => (en ? e : ru), K = ctx.kit;
    const value = SENSITIVITIES.includes(ctx.pref("stuck_sensitivity", "normal")) ? ctx.pref("stuck_sensitivity", "normal") : "normal";
    const options = [["off", L("Выключено", "Off")], ["low", L("Низкая — реже, только явные циклы", "Low — fewer pauses, only clear loops")],
      ["normal", L("Обычная (рекомендуется)", "Normal (recommended)")], ["high", L("Высокая — раньше, при первых признаках", "High — sooner, at the first signs")]];
    const control = K.select ? K.select("st-stuck-sensitivity", options, value, { width: 300 })
      : `<select class="pinput compact" id="st-stuck-sensitivity" style="width:300px;margin:0">${options.map(([v, l]) => `<option value="${v}" ${v === value ? "selected" : ""}>${ctx.esc(l)}</option>`).join("")}</select>`;
    return K.section({ id: "settings-stuck", title: L("Детектор зацикливания", "Stuck detector"), badge: "Work · DFY · Browser · Computer Use · MemuCode",
      desc: L("Если ИИ ходит по кругу — повторяет одно и то же действие, получает ту же ошибку, не продвигает шаг плана или говорит «продолжаю» без результата, — работа ставится на паузу. Вы решаете: продолжить, подсказать, сменить подход или остановить.",
        "When the AI goes in circles — repeats the same action, hits the same error, does not move the plan step or says “continuing” without results — the work is paused. You decide: continue, give a hint, change the approach or stop."),
      body: K.row({ label: L("Чувствительность", "Sensitivity"), key: "stuck_sensitivity", def: "normal", keywords: "stuck loop зацикливание цикл пауза pause повтор repeat детектор detector",
        desc: L("«Выключено» — без пауз: при явном цикле работа просто останавливается, как раньше.", "“Off” — no pauses: a clear loop just ends the run, as before."), control })
        + `<div class="sx-row sx-row--note">${K.note ? K.note(L("Ожидания (загрузка, сборка), прокрутка новых страниц и правки разных файлов зацикливанием не считаются. На паузе ничего не выполняется; карточка видна в окне MemuAI и в Быстром доступе.",
          "Waiting (loading, builds), scrolling through new content and edits of different files are not treated as loops. While paused nothing runs; the card shows in the MemuAI window and in Quick Access.")) : ""}</div>` });
  }
  function bindSettings(ctx) {
    const el = document.getElementById("st-stuck-sensitivity");
    if (!el) return;
    el.addEventListener("change", async (e) => {
      const v = SENSITIVITIES.includes(e.target.value) ? e.target.value : "normal";
      try { await ctx.savePreferences({ stuck_sensitivity: v }); } catch (err) { ctx.toast?.(err.message || String(err), "err"); }
    });
  }

  let dockKey = "";
  function dock() {
    let el = document.getElementById("memu-stuck-dock");
    if (el) return el;
    const wrap = document.querySelector(".composer-wrap");
    if (!wrap) return null;
    el = document.createElement("div");
    el.id = "memu-stuck-dock"; el.className = "memu-stuck-dock"; el.hidden = true;
    wrap.insertBefore(el, wrap.firstChild);
    return el;
  }
  function showDock(entry) {
    const el = dock();
    if (!el) return;
    const key = entry ? `${entry.source}:${entry.card.id}:${isEn() ? "en" : "ru"}` : "";
    if (key === dockKey) return;

    dockKey = key;
    if (!entry) { el.hidden = true; el.innerHTML = ""; return; }
    el.innerHTML = cardHtml(entry.card, { source: entry.source, task: entry.task || "", area: entry.area || entry.card.area });
    el.hidden = false;
    pulse(entry);
  }
  const announced = new Set();
  function pulse(entry) {
    const id = `${entry.source}:${entry.card.id}`;
    if (announced.has(id)) return;
    announced.add(id); if (announced.size > 50) announced.delete(announced.values().next().value);
    document.dispatchEvent(new CustomEvent("memu-stuck-pause", { detail: entry }));
    const en = isEn(), title = en ? entry.card.title_en : entry.card.title;
    try { if (typeof toast === "function") toast((en ? "Paused: " : "Пауза: ") + title, "warn"); } catch (e) {}
    try {
      if (document.hidden && window.Notification && Notification.permission === "granted")
        new Notification("MemuAI", { body: (en ? "The AI is stuck and waits for your decision: " : "ИИ застрял и ждёт вашего решения: ") + title, silent: false });
    } catch (e) {}
  }

  const automationPauses = {};
  function automation(kind, state) {
    const card = state && state.stuck;
    if (card && card.id) automationPauses[kind] = { source: kind, card, area: kind };
    else delete automationPauses[kind];
    refresh();
  }

  const detectors = new Map();
  const seenTools = new Map();
  const planBest = new Map();

  const workKey = (chatId, phase) => (chatId || "") + (phase === "tools" ? "#tools" : "");
  function detectorFor(chatId) {
    const sens = sensitivity();
    let d = detectors.get(chatId);
    if (!d || d.sensitivity !== sens) { d = new Detector(sens, "work"); detectors.set(chatId, d); }
    return d;
  }
  const FILE_TOOL = /(write|edit|patch|apply|create|delete|remove|move|rename|replace|insert|mkdir)/i;
  const PASSIVE_TOOL = new Set(["agent_status", "agent_result"]);
  function workObservation(assistant, phase, ctx) {
    const plan = ctx.plan || null, goal = ctx.goal || null;
    const files = (assistant.workActions?.files || []).map((f) => `${f.path || f.file || ""}#${String(f.content || f.text || "").length}`).sort();
    const deletes = (assistant.workDeletes?.paths || []).slice().sort();
    const round = phase === "tools" ? (assistant.toolCalls?.calls || []) : [];
    const recent = phase === "tools" ? [] : (assistant.toolHistory || []).filter((h) => h.tool).map((h) => ({ tool: h.tool, args: h.args }));
    const calls = (phase === "tools" ? round.map((c) => ({ tool: c.tool, args: c.args })) : recent).slice(0, 30);
    const firstPending = plan?.steps?.find?.((s) => s.status !== "complete");
    const action = { action: phase, step: firstPending ? firstPending.id : null, files, deletes, command: assistant.workCommand?.command || "",
      tools: calls.map((c) => ({ tool: c.tool, args: c.args })) };
    let filesChanged = files.length + deletes.length;
    (assistant.toolHistory || []).forEach((h) => { if (h.ok && h.tool && FILE_TOOL.test(h.tool) && h.round === assistant._toolRounds) filesChanged++; });
    if (phase === "tools") filesChanged = 0;
    const seen = seenTools.get(ctx.chatId) || new Set(); seenTools.set(ctx.chatId, seen);
    let fresh = false;
    calls.forEach((c) => { const sig = signature(c); if (!seen.has(sig)) { seen.add(sig); fresh = true; } });
    const lastErr = [...(assistant.toolHistory || [])].reverse().find((h) => h.error)?.error || assistant.workCommand?.error || "";
    const passive = phase === "tools" && calls.length > 0 && calls.every((c) => PASSIVE_TOOL.has(c.tool) || /^(status|read|list)$/.test(String(c.args?.action || "")) && /^(code_task|code_terminal)$/.test(c.tool));

    let advanced = false, progress = null;
    if (plan) {
      const done = (plan.steps || []).filter((st) => st && st.status === "complete").length || +plan.completed || 0;
      const hwKey = `${ctx.chatId || ""}\u0000${plan.objective || ""}`;
      const first = !planBest.has(hwKey);
      const best = first ? done : planBest.get(hwKey);
      advanced = first ? (assistant.planUpdates || []).some((u) => u && u.status === "complete") : done > best;
      if (first || advanced) planBest.set(hwKey, Math.max(done, best));
      progress = `${Math.max(done, best)}/${(plan.steps || []).length}`;
    }
    const progressed = advanced || fresh || undefined;

    const callLabel = (c) => { const a = c.args && typeof c.args === "object" ? c.args : {};
      const what = a.command || a.path || a.file_path || a.pattern || a.query || a.url || a.action || "";
      return c.tool + (what ? " " + String(what).replace(/\s+/g, " ").slice(0, 70) : ""); };
    const label = phase === "tools" ? calls.slice(0, 4).map(callLabel).join("; ") || "tools"
      : [firstPending ? (isEn() ? `step ${firstPending.id}` : `шаг ${firstPending.id}`) : "", files.length ? (isEn() ? `${files.length} file(s)` : `файлов: ${files.length}`) : "",
        action.command ? "$ " + action.command.slice(0, 60) : "", calls.length ? calls.map((c) => c.tool).slice(0, 4).join(", ") : "",
        goal && assistant.goalProgress?.nextStep ? "→ " + assistant.goalProgress.nextStep.slice(0, 80) : ""].filter(Boolean).join(" · ") || (isEn() ? "reply without changes" : "ответ без изменений");
    return { action, opts: { label, filesChanged, claimed: phase !== "tools" && (assistant.goalProgress?.status === "continue" || plan?.status === "active"),
      progress, progressed: progressed ? true : (filesChanged ? true : null), error: lastErr, passive } };
  }

  function workGate(assistant, ctx = {}) {
    try {
      if (!assistant || assistant.error) return false;
      const phase = ctx.phase || "plan";
      const holder = phase === "plan" ? ctx.plan : phase === "goal" ? ctx.goal : assistant.toolCalls;
      if (!holder) return false;
      const d = detectorFor(workKey(ctx.chatId, phase));

      if (holder.stuckPause) { delete holder.stuckPause; d.acknowledge("continue"); }
      if (!d.enabled) return false;
      const { action, opts } = workObservation(assistant, phase, ctx);
      const v = d.observe(action, opts);
      if (v.level === "hint" && phase !== "tools") { ctx.addHint?.(v.modelHint); return false; }
      if (!v.paused) return false;
      holder.stuckPause = { ...toCard(v), phase, chatId: ctx.chatId || "" };

      assistant.progress = isEn() ? "Paused — waiting for your decision" : "Пауза — ждёт вашего решения";
      refresh();
      settleWhenIdle(ctx.chatId || "");
      return true;
    } catch (e) { console.warn("stuck detector", e); return false; }
  }
  function currentWorkPause() {
    if (typeof S === "undefined") return null;
    const chatId = S.currentId || "";
    const plan = S.activeWorkPlan, goal = S.workGoal;

    const lastUser = [...(S.messages || [])].reverse().find((m) => m && m.role === "user" && !m.internal);
    const stale = (card) => card && ((lastUser && +lastUser.createdAt > +card.created) || false);
    if (plan?.stuckPause && (stale(plan.stuckPause) || plan.status !== "active")) { delete plan.stuckPause; detectorFor(chatId).acknowledge("hint"); }
    if (goal?.stuckPause && (stale(goal.stuckPause) || !goal.enabled)) { delete goal.stuckPause; detectorFor(chatId).acknowledge("hint"); }

    const sfx = S.mode === "code" ? "_code" : "";
    if (plan?.stuckPause) return { source: "work", card: plan.stuckPause, holder: plan, area: "plan" + sfx };
    if (goal?.stuckPause) return { source: "work", card: goal.stuckPause, holder: goal, area: "goal" + sfx };
    let msg = (S.messages || []).slice(-12).reverse().find((m) => m && m.toolCalls?.stuckPause);
    if (msg && stale(msg.toolCalls.stuckPause)) {

      delete msg.toolCalls.stuckPause;
      if (msg.toolCalls.status === "pending") msg.toolCalls.status = "cancelled";
      detectorFor(workKey(chatId, "tools")).acknowledge("hint");
      msg = null;
    }
    if (msg) return { source: "work", card: msg.toolCalls.stuckPause, holder: msg.toolCalls, assistant: msg, area: "tools" + sfx, chatId };
    return null;
  }
  function settleTurn() {
    try {
      if (typeof generationJobs !== "undefined" && generationJobs.has(S.currentId)) return;
      (S.messages || []).forEach((m) => { if (m && m.role === "assistant" && m.streaming) { m.streaming = false; m.progress = ""; } });
      if (typeof generationJobs !== "undefined") generating = generationJobs.size > 0;
      if (typeof setSendBusy === "function") setSendBusy(false);
    } catch (e) { console.warn("stuck settle", e); }
  }

  function settleWhenIdle(chatId, tries = 0) {
    setTimeout(() => {
      if (typeof S === "undefined" || (chatId && S.currentId !== chatId) || !currentWorkPause()) return;
      if (typeof generationJobs !== "undefined" && generationJobs.has(S.currentId)) { if (tries < 40) settleWhenIdle(chatId, tries + 1); return; }
      settleTurn();
    }, 150);
  }
  async function resolveWork(cardId, decision, hint) {
    const entry = currentWorkPause();
    if (!entry || entry.card.id !== cardId) throw new Error(isEn() ? "This pause is already over" : "Эта пауза уже закончилась");
    const card = entry.card, phase = card.phase || entry.area;
    delete entry.holder.stuckPause;
    const d = detectorFor(workKey(S.currentId, phase));
    d.acknowledge(decision);
    const note = modelNote(decision, hint, card);
    const save = () => { try { persistChat(); } catch (e) {} try { if (typeof updateWorkUI === "function") updateWorkUI(); } catch (e) {} try { renderMessages(); } catch (e) {} };
    if (decision === "stop") {
      const msg = isEn() ? "Stopped after a stuck pause" : "Остановлено после паузы из-за зацикливания";
      if (phase === "plan" && S.activeWorkPlan) { S.activeWorkPlan.status = "blocked"; S.activeWorkPlan.error = msg; }
      if ((phase === "plan" || phase === "goal") && S.workGoal?.enabled) { S.workGoal.enabled = false; S.workGoal.status = "stopped"; S.workGoal.summary = msg; }
      else if (phase === "tools" && entry.holder) entry.holder.status = "limit";

      if (phase !== "tools") settleTurn();
      save(); refresh();
      if (phase === "tools" && entry.assistant && typeof runHiddenTools === "function") runHiddenTools(entry.assistant);
      return { ok: true };
    }
    save(); refresh();
    if (phase === "plan" && typeof continueWorkPlan === "function") setTimeout(() => continueWorkPlan(note), 60);
    else if (phase === "goal" && typeof continueWorkGoal === "function") {
      if (S.workGoal) S.workGoal.rounds = Math.max(0, (+S.workGoal.rounds || 0) - 1);
      setTimeout(() => continueWorkGoal(note), 60);
    } else if (phase === "tools" && entry.assistant) {
      if (decision === "continue" && typeof runHiddenTools === "function") setTimeout(() => runHiddenTools(entry.assistant), 60);
      else if (typeof resumeAfterAutomation === "function") {

        entry.holder.status = "done";
        setTimeout(() => resumeAfterAutomation("tools", note + "\nThe tool calls requested in the last round were NOT executed.",
          0, { chatId: S.currentId, messages: S.messages, assistant: entry.assistant }), 60);
      }
    }
    return { ok: true };
  }

  function refresh() {
    const entry = currentWorkPause() || automationPauses.browser || automationPauses.computer || null;
    showDock(entry);
  }
  setInterval(() => { if (!document.hidden && document.querySelector(".composer-wrap")) refresh(); }, 800);
  window.addEventListener("memu-language", () => { dockKey = ""; refresh(); });

  window.MemuStuck = {
    Detector, signature, stateKey, sameState, parseDecision, modelNote, toCard, PROFILES, SENSITIVITIES, DECISIONS,
    cardHtml, dfyQuestionHtml, decide, workGate, automation, refresh, settingsHtml, bindSettings, sensitivity,
  };
})();
