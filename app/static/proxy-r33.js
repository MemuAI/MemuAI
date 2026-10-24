/* MemuAI Proxy — Round 33 (r33-proxy, item #19): accounts list.

   1. Reordering cards is pointer-driven instead of the native HTML5 drag. On Windows the native drag runs
      an OLE modal loop that swallows the mouse wheel, so a long list could not be scrolled while a card was
      held. Here the page scrolls with the wheel as usual, scrolls by itself near the top/bottom edge of the
      window (speed grows towards the edge), and Esc cancels the move. A drop on the left/right half of a card
      puts the moved card(s) before/after it; a selected group moves together.
   2. Selection: the bulk bar shows «N of M selected», an idle state in selection mode, a select-all toggle
      and a visible «Cancel selection» button; Esc (outside fields, menus and dialogs) or a click on empty
      space clears the selection and leaves selection mode.
   Uses app.js globals (classic scripts share one global scope): accountsSelected, accountsSelectMode,
   accountsSort, visibleAccounts, reorderAccounts, syncAccountBulkBar, loadAccounts, closeAccountMenu, t, $, $$. */
"use strict";

window.MemuAccR33 = (() => {
  const INTERACTIVE = "button, input, label, a, select, textarea, .switch";
  const D = { start: null, active: false, cardId: "", ghost: null, x: 0, y: 0, target: null, after: false,
    frame: 0, lastTick: 0, suppressClickUntil: 0 };
  const fmt = (key, vars) => Object.entries(vars || {}).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), t(key));

  /* ------------------------------------------------------------ selection */
  function sync() {
    const bar = document.getElementById("acc-bulk-bar");
    if (!bar) return;
    const total = typeof visibleAccounts === "function" ? visibleAccounts().length : 0;
    const n = accountsSelected.size;
    const count = document.getElementById("acc-bulk-count");
    if (count) count.textContent = n ? fmt("acc.sel.count", { n, total }) : t("acc.sel.none");
    bar.classList.toggle("idle", n === 0);
    bar.classList.add("acc-bulk");
    const all = document.getElementById("acc-select-all");
    if (all) {
      const every = total > 0 && visibleAccounts().every((a) => accountsSelected.has(a.id));
      all.textContent = t(every ? "acc.sel.unall" : "acc.sel.all");
      all.disabled = total === 0;
    }
    const mode = document.getElementById("acc-select-mode");
    if (mode) {
      mode.setAttribute("aria-pressed", String(!!accountsSelectMode));
      mode.classList.toggle("active", !!accountsSelectMode);
      const label = mode.querySelector("span");
      if (label) {
        label.dataset.i18n = accountsSelectMode ? "acc.selectmode.done" : "acc.selectmode.btn";
        label.textContent = t(label.dataset.i18n);
      }
    }
    document.querySelectorAll("#accounts-grid [data-select]").forEach((box) => { box.checked = accountsSelected.has(box.dataset.select); });
  }

  function clearSelection() {
    const had = accountsSelected.size > 0 || accountsSelectMode;
    if (!had) return false;
    const wasMode = accountsSelectMode;
    accountsSelected.clear();
    accountsSelectMode = false;
    if (wasMode) loadAccounts();      // checkboxes leave the cards
    else syncAccountBulkBar();
    return true;
  }

  /* ------------------------------------------------------------ pointer drag */
  const grid = () => document.getElementById("accounts-grid");
  const cardOf = (node) => node && node.closest ? node.closest("#accounts-grid .acc-card") : null;

  function onPointerDown(event) {
    if (event.button !== 0 || event.pointerType === "touch") return;
    const card = cardOf(event.target);
    if (!card || event.target.closest(INTERACTIVE) || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (card.dataset.reorder !== "true") return;
    D.start = { x: event.clientX, y: event.clientY, id: card.dataset.accId, pointerId: event.pointerId };
  }

  function begin(event) {
    const card = grid()?.querySelector(`.acc-card[data-acc-id="${CSS.escape(D.start.id)}"]`);
    if (!card) { D.start = null; return; }
    D.active = true;
    D.cardId = D.start.id;
    closeAccountMenu();
    const moving = accountsSelected.has(D.cardId) && accountsSelected.size > 1
      ? Array.from(accountsSelected) : [D.cardId];
    moving.forEach((id) => grid().querySelector(`.acc-card[data-acc-id="${CSS.escape(id)}"]`)?.classList.add("dragging"));
    const box = card.getBoundingClientRect();
    const ghost = document.createElement("div");
    ghost.className = "acc-drag-ghost";
    const title = card.querySelector(".p-title")?.textContent || "";
    const icon = card.querySelector(".p-icon")?.innerHTML || "";
    ghost.innerHTML = `<span class="g-ico">${icon}</span><span class="g-title"></span>${moving.length > 1 ? `<span class="g-more">${fmt("acc.drag.many", { n: moving.length - 1 })}</span>` : ""}`;
    ghost.querySelector(".g-title").textContent = title;
    D.grab = { dx: Math.min(event.clientX - box.left, 40), dy: 22 };
    document.body.appendChild(ghost);
    D.ghost = ghost;
    document.body.classList.add("acc-dragging");
    try { card.setPointerCapture(D.start.pointerId); } catch (e) { /* ignore */ }
    window.getSelection()?.removeAllRanges();
  }

  function place() {
    if (!D.ghost) return;
    D.ghost.style.transform = `translate(${Math.round(D.x - D.grab.dx)}px, ${Math.round(D.y - D.grab.dy)}px)`;
  }

  function clearTarget() {
    if (D.target) D.target.classList.remove("drop-before", "drop-after", "drop-target");
    D.target = null;
  }

  function findTarget() {
    if (!D.active) return;
    const under = document.elementFromPoint(D.x, D.y);
    let card = cardOf(under);
    if (card && card.classList.contains("dragging")) card = null;
    if (!card) {
      // Between cards (the grid gap) keep the last target: the indicator must not flicker.
      if (under && under.closest && under.closest("#accounts-grid")) return;
      clearTarget();
      return;
    }
    const box = card.getBoundingClientRect();
    const after = D.x > box.left + box.width / 2;
    if (card !== D.target || after !== D.after) {
      clearTarget();
      D.target = card;
      D.after = after;
      card.classList.add("drop-target", after ? "drop-after" : "drop-before");
    }
  }

  /* Edge auto-scroll: the zone starts under the sticky top bar; the speed grows towards (and past) the edge,
     so holding the cursor still at the edge keeps scrolling. */
  function edgeSpeed() {
    const topBar = document.querySelector("header.topbar");
    const top = topBar ? topBar.getBoundingClientRect().bottom : 0;
    const bottom = window.innerHeight;
    const zone = Math.max(48, Math.min(110, (bottom - top) / 5));
    if (D.y < top + zone) return -Math.min(38, Math.ceil(((top + zone - D.y) / zone) ** 1.6 * 22) + 2);
    if (D.y > bottom - zone) return Math.min(38, Math.ceil(((D.y - bottom + zone) / zone) ** 1.6 * 22) + 2);
    return 0;
  }

  function tick(now) {
    if (!D.active) { D.frame = 0; return; }
    const dt = D.lastTick ? Math.min(48, now - D.lastTick) : 16;
    D.lastTick = now;
    const speed = edgeSpeed();
    if (speed) {
      const scroller = document.scrollingElement || document.documentElement;
      const before = scroller.scrollTop;
      scroller.scrollTop += speed * (dt / 16);
      if (scroller.scrollTop !== before) findTarget();
    }
    D.frame = requestAnimationFrame(tick);
  }

  function onPointerMove(event) {
    if (!D.start) return;
    D.x = event.clientX; D.y = event.clientY;
    if (!D.active) {
      if (Math.hypot(event.clientX - D.start.x, event.clientY - D.start.y) < 6) return;
      if (accountsSort !== "order") { D.start = null; return; }
      begin(event);
      if (!D.active) return;
      D.lastTick = 0;
      D.frame = requestAnimationFrame(tick);
    }
    event.preventDefault();
    place();
    findTarget();
  }

  function finish(commit) {
    const wasActive = D.active;
    const target = D.target, after = D.after, source = D.cardId;
    if (D.frame) cancelAnimationFrame(D.frame);
    D.frame = 0;
    clearTarget();
    D.ghost?.remove();
    D.ghost = null;
    document.body.classList.remove("acc-dragging");
    grid()?.querySelectorAll(".acc-card.dragging").forEach((c) => c.classList.remove("dragging"));
    D.start = null; D.active = false; D.cardId = "";
    if (!wasActive) return;
    D.suppressClickUntil = Date.now() + 350;
    if (commit && target && target.dataset.accId && target.dataset.accId !== source) {
      reorderAccounts(source, target.dataset.accId, after);
    }
  }

  function onPointerUp() { if (D.start) finish(true); }
  function onPointerCancel() { if (D.start) finish(false); }

  /* ------------------------------------------------------------ keyboard / clicks */
  function onKeyDown(event) {
    if (event.key !== "Escape") return;
    if (D.active) {
      event.preventDefault(); event.stopPropagation();
      finish(false);
      toast(t("acc.drag.cancelled"), "warn", 1600);
      return;
    }
    if (!document.getElementById("tab-accounts")?.classList.contains("active")) return;
    if (document.getElementById("modal-back")?.classList.contains("visible")) return;
    if (document.getElementById("acc-context-menu")) return;     // app.js closes the menu first
    const focus = document.activeElement;
    if (focus && /^(INPUT|TEXTAREA|SELECT)$/.test(focus.tagName) && focus.id !== "acc-select-mode") return;
    if (document.querySelector(".companion:not([hidden]) :focus")) return;
    if (clearSelection()) event.preventDefault();
  }

  function onPageClick(event) {
    if (!accountsSelected.size) return;
    if (!document.getElementById("tab-accounts")?.classList.contains("active")) return;
    const target = event.target;
    if (!target.closest || !target.closest("#tab-accounts")) return;
    if (target.closest(".acc-card, .bulk-bar, button, input, label, a, select, textarea, .chip-row, .ctx-menu")) return;
    if (Date.now() < D.suppressClickUntil) return;
    accountsSelected.clear();
    syncAccountBulkBar();
  }

  function wire() {
    const g = grid();
    if (!g) return;
    g.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove, { passive: false });
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    window.addEventListener("blur", () => { if (D.start) finish(false); });
    // A drop changes nothing if the wheel moved the page: the target is re-read after every scroll.
    window.addEventListener("scroll", () => { if (D.active) findTarget(); }, { passive: true });
    window.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("click", onPageClick);
    // Native drag must never start from a card (images/links inside could still try).
    g.addEventListener("dragstart", (event) => { if (cardOf(event.target)) event.preventDefault(); });
    const cancel = document.getElementById("acc-select-cancel");
    if (cancel) cancel.addEventListener("click", () => clearSelection());
    sync();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();

  return { sync, clearSelection, justDragged: () => Date.now() < D.suppressClickUntil, _state: D };
})();
