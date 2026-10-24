/* MemuAI — «Сравнить ответы»: версии одного ответа бок о бок (Round 37, r37-extras-2).

   Built on the answer versions of Round 35 (branches.js): every «Повторить» keeps the old answer as a version, and
   «Ответ другой моделью» (here, and in a message's «Ещё» menu) is the same regenerate with a model you pick
   (retryAnswerAt(index, model) in app.js). The «⇆» button of the version switcher «‹ 2/3 ›» opens the versions of that
   answer in columns (2–3 on screen, more scroll sideways): model, time, length, the rendered answer, synchronized
   scrolling. «Выбрать этот» makes a version the answer in the chat — the others stay versions (‹ › still switches).

   Cost: nothing until opened — the button is a few bytes of the switcher's HTML, the dialog is built on demand, its only
   scroll listeners live inside the dialog (passive, rAF) and go away with it.

   window.MemuCompare: open(index) menuItem(i) versions(index) askModel(index, anchor?)
*/
(function () {
  "use strict";
  if (window.MemuCompare) return;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = '<span class="ui-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="7.5" height="16" rx="2"/><rect x="13.5" y="4" width="7.5" height="16" rx="2"/></svg></span>';
  const SYNC_KEY = "memu_compare_sync";
  const busy = () => { try { return generationJobs.has(S.currentId); } catch (e) { return false; } };
  const chatRec = () => { try { return currentChat(); } catch (e) { return null; } };

  /* The versions of the answer at S.messages[index]: [{j, m, active}] (empty when it has none). */
  function versions(index) {
    const m = S.messages[index];
    if (!m || m.role !== "assistant" || typeof MemuBranches === "undefined") return { key: "", sel: 0, rows: m ? [{ j: 0, m, active: true }] : [] };
    const it = MemuBranches.currentInfo()?.get(index);
    const fork = it && chatRec()?.branches?.forks?.[it.key];
    if (!fork) return { key: "", sel: 0, rows: [{ j: 0, m, active: true }] };
    const rows = fork.alts.map((alt, j) => {
      const msg = j === (fork.sel | 0) ? m : (Array.isArray(alt.m) ? alt.m.find((x) => x && !x.internal) : null);
      return msg && msg.role === "assistant" ? { j, m: msg, active: j === (fork.sel | 0) } : null;
    }).filter(Boolean);
    return { key: it.key, sel: fork.sel | 0, rows };
  }
  function textModels() {
    try {
      return (typeof visibleModels === "function" ? visibleModels() : S.models || [])
        .filter((x) => !(typeof isImageModel === "function" && isImageModel(x.id))).map((x) => x.id);
    } catch (e) { return []; }
  }
  function modelName(id) {
    if (!id) return "MemuAI";
    try { return modelDisplayName(id); } catch (e) { return String(id).split("/").pop(); }
  }
  function stats(text) {
    const words = (String(text).match(/[\p{L}\p{N}]+/gu) || []).length;
    return L(`${words} сл.`, `${words} words`);
  }
  function when(ts) {
    if (!ts) return "";
    try { return new Date(+ts).toLocaleString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
  }

  function menuItem(i) {
    const m = S.messages[i];
    if (!m || m.role !== "assistant" || m.media || (typeof isProjectMode === "function" && isProjectMode())) return "";
    return `<button data-cmp-model="${i}">${ICON} ${h(L("Ответ другой моделью…", "Answer with another model…"))}</button>`;
  }
  function switchButton(i) {
    return `<button type="button" class="br-step br-cmpbtn" data-cmp="${i}" aria-label="${h(L("Сравнить версии", "Compare versions"))}" title="${h(L("Сравнить версии бок о бок", "Compare versions side by side"))}">${ICON}</button>`;
  }

  async function askModel(index, anchor) {
    if (busy()) { toast(L("Дождитесь окончания ответа или нажмите «Стоп»", "Wait for the answer to finish or press Stop"), "warn"); return; }
    const ids = textModels();
    if (!ids.length) { toast(L("Нет доступных моделей", "No models available"), "warn"); return; }
    const current = S.messages[index]?.model || "";
    let picked = "";
    if (window.MemuModelPicker && anchor) {
      picked = await MemuModelPicker.open(anchor, { ids, value: current, title: L("Ответить другой моделью", "Answer with another model") });
    } else {
      picked = ids.find((id) => id !== current) || ids[0];
    }
    if (!picked || !S.messages[index]) return;
    if (typeof closePanel === "function" && document.querySelector("#panel-back.visible")) closePanel();
    toast(L(`Отвечает ${modelName(picked)} — сравнить: ⇆ под ответом`, `${modelName(picked)} is answering — compare: ⇆ under the answer`), "ok");
    retryAnswerAt(index, picked);
  }

  function open(index) {
    const v = versions(index);
    if (!v.rows.length) return;
    const many = v.rows.length > 1;
    const sync = localStorage.getItem(SYNC_KEY) !== "0";
    const cols = v.rows.map((r) => {
      const text = typeof visibleAnswer === "function" ? visibleAnswer(r.m.content || "") : String(r.m.content || "");
      const body = typeof md === "function" ? md(text) : h(text);
      return `<section class="cmp-col${r.active ? " cmp-active" : ""}" data-j="${r.j}">
        <header class="cmp-head">
          <span class="cmp-badge">${h(L("Версия ", "Version ") + (r.j + 1))}</span>
          <b class="cmp-model" title="${h(r.m.model || "")}">${h(modelName(r.m.model))}</b>
          ${r.active ? `<span class="cmp-tag">${h(L("в чате", "in the chat"))}</span>` : ""}
          <span class="cmp-meta">${h([when(r.m.createdAt), stats(text)].filter(Boolean).join(" · "))}</span>
        </header>
        <div class="cmp-body answer md">${body || `<p class="cmp-empty">${h(L("(пустой ответ)", "(empty answer)"))}</p>`}</div>
        <footer class="cmp-foot">${r.active
          ? `<span class="cmp-cur">✓ ${h(L("Этот ответ сейчас в чате", "This answer is in the chat now"))}</span>`
          : `<button type="button" class="pbtn sx-btn-sm" data-cmp-pick="${r.j}">${h(L("Выбрать этот ответ", "Use this answer"))}</button>`}</footer>
      </section>`;
    }).join("");
    const sub = many
      ? L(`Версий ответа: ${v.rows.length}. Выберите лучший — он станет ответом в чате, остальные останутся версиями (‹ ›).`, `Answer versions: ${v.rows.length}. Pick the best — it becomes the answer in the chat, the others stay versions (‹ ›).`)
      : L("У этого ответа пока одна версия. Попросите другую модель ответить на тот же вопрос — и сравните.", "This answer has a single version so far. Ask another model to answer the same question — then compare.");
    panel(`<h2>${ICON} ${h(L("Сравнение ответов", "Compare answers"))}</h2>
      <p class="sub cmp-sub">${h(sub)}</p>
      <div class="cmp-grid${many ? "" : " cmp-single"}" style="--cmp-n:${Math.min(3, v.rows.length)}">${cols}</div>
      <div class="cmp-bar">
        <button type="button" class="pbtn ghost sx-btn-sm" data-cmp-another>+ ${h(L("Ответ другой моделью", "Answer with another model"))}</button>
        ${many ? `<label class="cmp-sync"><input type="checkbox" data-cmp-sync ${sync ? "checked" : ""}> ${h(L("Синхронная прокрутка", "Scroll together"))}</label>` : ""}
      </div>`, "cmp-panel");
    const root = document.querySelector("#panel");
    if (!root) return;
    root.querySelectorAll("[data-cmp-pick]").forEach((b) => b.addEventListener("click", () => pick(index, v.key, +b.dataset.cmpPick)));
    root.querySelector("[data-cmp-another]")?.addEventListener("click", (e) => askModel(index, e.currentTarget));
    const syncBox = root.querySelector("[data-cmp-sync]");
    syncBox?.addEventListener("change", () => localStorage.setItem(SYNC_KEY, syncBox.checked ? "1" : "0"));
    // synchronized scrolling: proportional, one frame at a time, ignoring the echo of our own writes
    const bodies = [...root.querySelectorAll(".cmp-body")];
    let lead = null, frame = 0;
    bodies.forEach((el) => el.addEventListener("scroll", () => {
      if (!syncBox?.checked || (lead && lead !== el)) return;
      lead = el;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const max = el.scrollHeight - el.clientHeight;
        const ratio = max > 0 ? el.scrollTop / max : 0;
        bodies.forEach((o) => { if (o !== el) o.scrollTop = ratio * (o.scrollHeight - o.clientHeight); });
        requestAnimationFrame(() => { lead = null; });
      });
    }, { passive: true }));
  }
  function pick(index, key, j) {
    if (!key) return;
    if (busy()) { toast(L("Дождитесь окончания ответа или нажмите «Стоп»", "Wait for the answer to finish or press Stop"), "warn"); return; }
    const fork = chatRec()?.branches?.forks?.[key];
    if (!fork) return;
    if (typeof closePanel === "function") closePanel();
    MemuBranches.step(key, index, j - (fork.sel | 0));
    toast(L(`Выбрана версия ${j + 1}`, `Version ${j + 1} selected`), "ok");
  }

  document.addEventListener("click", (e) => {
    const t = e.target instanceof Element ? e.target : null;
    const cmp = t?.closest("#messages [data-cmp]");
    if (cmp) { e.preventDefault(); e.stopPropagation(); open(+cmp.dataset.cmp); return; }
    const other = t?.closest("#messages [data-cmp-model]");
    if (other) {
      e.preventDefault(); e.stopPropagation();
      const pop = other.closest(".msg-more");
      const anchor = pop?.querySelector(".msg-tool") || other;
      pop?.classList.remove("open");
      askModel(+other.dataset.cmpModel, anchor);
    }
  });

  window.MemuCompare = Object.freeze({ open, menuItem, switchButton, versions, askModel });
})();
