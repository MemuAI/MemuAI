// Message reactions of an agentic workspace: the user reacts to the
// companion's messages (every bubble on its own), the companion may react to
// the user's latest message. Reactions live on the message
// (m.reactions = [{ p, e, by, t }]: bubble index, emoji, "user" | "ai", time)
// and travel to the model as a short note after that message, so the
// companion sees all of them.
(function () {
  "use strict";
  if (window.MemuReactions) return;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : "ru") === "en";
  const L = (ru, english) => (en() ? english : ru);
  const h = (value) => (typeof esc === "function" ? esc(value == null ? "" : String(value)) : String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const ui = (name) => (typeof icon === "function" ? icon(name) : "");
  const reduced = () => document.documentElement.dataset.motion === "off" || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  const QUICK = [
    ["❤️", "Нравится", "Love"], ["😂", "Смешно", "Funny"], ["👍", "Согласен", "Agree"], ["🔥", "Огонь", "Fire"],
    ["😮", "Ого", "Wow"], ["😢", "Грустно", "Sad"], ["🙏", "Спасибо", "Thanks"],
  ];
  const GRID = [
    ["Эмоции", "Feelings", "😀 😁 😅 🤣 😊 😍 🥰 😘 😎 🤔 😏 😴 🥺 😭 😡 🤯 😱 🤗 🙃 😇 🤩 😬 😋 😌"],
    ["Жесты", "Gestures", "👍 👎 👏 🙌 🤝 🙏 💪 ✌️ 👌 🤞 🤟 👀"],
    ["Символы", "Symbols", "❤️ 🧡 💛 💚 💙 💜 🖤 💔 🔥 ✨ ⭐ 💯 ✅ ❌ ⚡ 🎉 🏆 💡 🎵 🤖"],
  ];
  const RECENT_KEY = "memu_rx_recent";
  const fresh = new WeakMap();
  let pop = null;

  // ── Data ──────────────────────────────────────────────────────────────────
  const EMOJI_RE = /\p{Extended_Pictographic}/u;
  function normEmoji(raw) {
    const text = String(raw == null ? "" : raw).trim().replace(/^["'{\s]+|["'}\s]+$/g, "");
    if (!text || text.length > 16 || !EMOJI_RE.test(text) || /[A-Za-zА-Яа-я0-9<>]/.test(text)) return "";
    let first = text;
    try { first = [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(text)][0].segment; } catch (e) { first = [...text].slice(0, 2).join(""); }
    return EMOJI_RE.test(first) ? first : "";
  }
  const list = (m) => (m && Array.isArray(m.reactions) ? m.reactions.filter((r) => r && r.e && (r.by === "user" || r.by === "ai")) : []);
  function spaces() { return globalThis.MemuSpaces || null; }
  function enabled() {
    const sp = spaces();
    return !!(sp && sp.isAgent && sp.isAgent() && sp.settings().reactions !== false);
  }
  function canReact(m) {
    return enabled() && m?.role === "assistant" && !m.streaming && !m.localOnly && !m.error && !spaces().isArchiving?.(m);
  }
  function set(m, part, emoji, by = "user") {
    if (!m) return false;
    const e = normEmoji(emoji);
    const p = Math.max(0, Math.trunc(+part) || 0);
    const rows = list(m);
    const same = rows.find((r) => r.by === by && (+r.p || 0) === p);
    const next = rows.filter((r) => !(r.by === by && (+r.p || 0) === p));
    let added = false;
    if (e && !(same && same.e === e)) { next.push({ p, e, by, t: Date.now() }); added = true; }
    m.reactions = next.slice(-60);
    if (!m.reactions.length) delete m.reactions;
    if (added) fresh.set(m, { key: `${p}|${by}|${e}`, at: Date.now() });
    return added;
  }

  // ── What the companion sees ─────────────────────────────────────────────────
  function partTexts(m) {
    const sp = spaces();
    try { if (sp && sp.isBubbleMessage && sp.isBubbleMessage(m)) return sp.bubbleParts({ ...m, streaming: false }).done; } catch (e) {}
    return [typeof visibleAnswer === "function" ? visibleAnswer(String(m.content || "")) : String(m.content || "")];
  }
  function excerpt(text) {
    const flat = String(text || "").replace(/```[\s\S]*?```/g, " [code] ").replace(/[*_`>#|]/g, "").replace(/\s+/g, " ").trim();
    return flat.length > 70 ? flat.slice(0, 68).trimEnd() + "…" : flat;
  }
  function historyNote(m) {
    const rows = list(m);
    if (!rows.length) return "";
    if (m.role === "user") {
      const own = rows.filter((r) => r.by === "ai");
      return own.length ? `\n[reactions: you reacted ${own.map((r) => r.e).join(" ")}]` : "";
    }
    const mine = rows.filter((r) => r.by === "user").sort((a, b) => (+a.p || 0) - (+b.p || 0));
    if (!mine.length) return "";
    const parts = partTexts(m);
    if (parts.length <= 1) return `\n[reactions: the user reacted ${mine.map((r) => r.e).join(" ")}]`;
    return `\n[reactions: the user reacted ${mine.map((r) => `${r.e} to message ${(+r.p || 0) + 1} «${excerpt(parts[+r.p || 0] || "")}»`).join("; ")}]`;
  }

  // ── Markup ──────────────────────────────────────────────────────────────────
  function cls(m) { return list(m).length ? " has-rx" : ""; }
  function avatarDot(by) {
    if (by !== "ai") return "";
    const sp = spaces();
    const c = sp && sp.companion ? sp.companion() : null;
    return c && sp.avatarHtml ? `<span class="rx-who">${sp.avatarHtml(c, 16, "rx-ava")}</span>` : "";
  }
  function chipsHtml(m, i, part = 0) {
    if (!m || !enabled()) return "";
    const p = +part || 0;
    const rows = list(m).filter((r) => (+r.p || 0) === p);
    const isAssistant = m.role === "assistant";
    const canAdd = canReact(m);
    if (!rows.length && !canAdd) return "";
    const groups = new Map();
    rows.forEach((r) => { const g = groups.get(r.e) || []; g.push(r); groups.set(r.e, g); });
    const sp = spaces();
    const name = sp && sp.whoName ? sp.whoName() : "MemuAI";
    const recent = fresh.get(m);
    const chips = [...groups.entries()].map(([e, who]) => {
      const mine = who.some((r) => r.by === "user");
      const isNew = recent && Date.now() - recent.at < 1400 && who.some((r) => recent.key === `${p}|${r.by}|${r.e}`);
      const label = who.map((r) => (r.by === "user" ? L("Вы", "You") : name)).join(", ");
      const title = mine && isAssistant ? L(`${label} · нажмите, чтобы убрать`, `${label} · click to remove`) : label;
      return `<button type="button" class="rx-chip${mine ? " mine" : ""}${isNew ? " rx-new" : ""}" data-rx-chip="${h(e)}" data-rx-msg="${i}" data-rx-part="${p}" title="${h(title)}" aria-label="${h(e + " — " + label)}"${mine && isAssistant ? "" : ' aria-disabled="true"'}><span class="rx-e">${h(e)}</span>${who.map((r) => avatarDot(r.by)).join("")}${who.length > 1 ? `<span class="rx-n">${who.length}</span>` : ""}</button>`;
    }).join("");
    const add = canAdd ? `<button type="button" class="rx-add" data-rx-add="${i}" data-rx-part="${p}" title="${h(L("Реакция", "React"))}" aria-label="${h(L("Добавить реакцию", "Add a reaction"))}">${ui("smile")}</button>` : "";
    return `<div class="rx-row${rows.length ? "" : " empty"}" data-rx-row>${chips}${add}</div>`;
  }

  // ── Picker ──────────────────────────────────────────────────────────────────
  function recent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter((e) => normEmoji(e)).slice(0, 6); } catch (e) { return []; } }
  function remember(e) {
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([e, ...recent().filter((x) => x !== e)].slice(0, 6))); } catch (err) {}
  }
  function quickRow() {
    const row = QUICK.map(([e, ru, enName]) => ({ e, name: L(ru, enName) }));
    const extra = recent().find((e) => !row.some((r) => r.e === e));
    if (extra) row[row.length - 1] = { e: extra, name: extra };
    return row;
  }
  function close() {
    if (!pop) return;
    const anchor = pop._anchor;
    pop.remove();
    pop = null;
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", close);
    document.removeEventListener("scroll", onScroll, true);
    anchor?.closest(".sp-b, .msg")?.classList.remove("rx-open");
    if (anchor && anchor.isConnected) anchor.focus({ preventScroll: true });
  }
  function onScroll(event) { if (pop && !(event.target instanceof Node && pop.contains(event.target))) close(); }
  function onOutside(event) { if (pop && !pop.contains(event.target) && !event.target.closest?.("[data-rx-add]")) close(); }
  function onKey(event) {
    if (!pop) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      const items = [...pop.querySelectorAll("button:not([hidden])")].filter((b) => b.offsetParent);
      const at = items.indexOf(document.activeElement);
      const next = items[(at + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length];
      if (next) { event.preventDefault(); next.focus(); }
    }
  }
  function place(anchor) {
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, hgt = pop.offsetHeight, pad = 8;
    // Stay inside the conversation column when it is wide enough.
    const box = (document.getElementById("chat-view-wrap") || document.body).getBoundingClientRect();
    const minLeft = box.width >= w + pad * 2 ? Math.max(pad, box.left + pad) : pad;
    const maxLeft = box.width >= w + pad * 2 ? Math.min(innerWidth, box.right) - w - pad : innerWidth - w - pad;
    let left = Math.min(Math.max(minLeft, r.left + r.width / 2 - w / 2), maxLeft);
    let top = r.top - hgt - 8;
    pop.dataset.side = "top";
    if (top < pad) { top = r.bottom + 8; pop.dataset.side = "bottom"; }
    if (top + hgt > innerHeight - pad) top = Math.max(pad, innerHeight - hgt - pad);
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
  }
  function open(anchor, index, part) {
    close();
    const m = (typeof S !== "undefined" && S.messages) ? S.messages[index] : null;
    if (!anchor.isConnected || !canReact(m)) return;
    const current = list(m).find((r) => r.by === "user" && (+r.p || 0) === part);
    pop = document.createElement("div");
    pop.className = "rx-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", L("Реакция на сообщение", "React to the message"));
    pop._anchor = anchor;
    const btn = (e, name) => `<button type="button" class="rx-opt${current && current.e === e ? " on" : ""}" data-rx-pick="${h(e)}" title="${h(name)}" aria-label="${h(name)}">${h(e)}</button>`;
    pop.innerHTML = `<div class="rx-quick">${quickRow().map((r) => btn(r.e, r.name)).join("")}<button type="button" class="rx-more" data-rx-more aria-expanded="false" title="${h(L("Все реакции", "All reactions"))}" aria-label="${h(L("Все реакции", "All reactions"))}">${ui("plus")}</button></div>
      <div class="rx-grid" hidden>${GRID.map(([ru, enName, set]) => `<div class="rx-sec"><small>${h(L(ru, enName))}</small><div class="rx-sec-row">${set.split(" ").map((e) => btn(e, e)).join("")}</div></div>`).join("")}</div>`;
    document.body.appendChild(pop);
    place(anchor);
    anchor.closest(".sp-b, .msg")?.classList.add("rx-open");
    pop.addEventListener("click", (event) => {
      const more = event.target.closest("[data-rx-more]");
      if (more) {
        const grid = pop.querySelector(".rx-grid");
        grid.hidden = !grid.hidden;
        more.setAttribute("aria-expanded", String(!grid.hidden));
        pop.classList.toggle("expanded", !grid.hidden);
        place(anchor);
        if (!grid.hidden) grid.querySelector("button")?.focus();
        return;
      }
      const pick = event.target.closest("[data-rx-pick]");
      if (!pick) return;
      const e = pick.dataset.rxPick;
      close();
      if (!anchor.isConnected || S.messages[index] !== m || !canReact(m)) return;
      react(index, part, e);
      if (!QUICK.some((q) => q[0] === e)) remember(e);
    });
    document.addEventListener("pointerdown", onOutside, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", close);
    document.addEventListener("scroll", onScroll, { capture: true, passive: true });
    (pop.querySelector(".rx-opt.on") || pop.querySelector(".rx-opt"))?.focus({ preventScroll: true });
  }

  // ── Actions ─────────────────────────────────────────────────────────────────
  function persist(m) {
    if (typeof S === "undefined") return;
    let chat = (S.messages || []).includes(m) ? (S.chats || []).find((c) => c.id === S.currentId) : null;
    if (!chat) chat = (S.chats || []).find((c) => (c.messages || []).includes(m)) || null;
    if (chat && typeof persistChatSnapshot === "function") {
      spaces()?.threadEdited?.(chat, m);
      persistChatSnapshot(chat.id, chat.id === S.currentId ? S.messages : chat.messages);
    }
  }
  function react(index, part, emoji) {
    const m = S.messages[index];
    if (!canReact(m)) return;
    set(m, part, emoji, "user");
    persist(m);
    if (typeof renderMessages === "function") renderMessages();
  }
  function onClick(event) {
    const add = event.target.closest("[data-rx-add]");
    if (add) {
      event.preventDefault(); event.stopPropagation();
      if (pop && pop._anchor === add) { close(); return; }
      open(add, +add.dataset.rxAdd, +add.dataset.rxPart || 0);
      return;
    }
    const chip = event.target.closest("[data-rx-chip]");
    if (chip && chip.getAttribute("aria-disabled") !== "true") {
      event.preventDefault(); event.stopPropagation();
      react(+chip.dataset.rxMsg, +chip.dataset.rxPart || 0, chip.dataset.rxChip);
    }
  }
  document.addEventListener("click", onClick);
  document.addEventListener("memu:chat-open", close);

  window.MemuReactions = Object.freeze({ enabled, canReact, normEmoji, set, list, cls, chipsHtml, historyNote, open, close, QUICK: QUICK.map((q) => q[0]), reduced });
})();
