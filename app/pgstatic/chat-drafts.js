/* MemuAI — черновики поля ввода по чатам и «↑ — последнее сообщение» (Round 37, r37-extras).

   Drafts
     The composer used to keep its text when you switched chats: a half-written question «moved» into whatever chat you
     opened next and was gone from the chat it was meant for. Now every chat keeps its own unsent text:
       • opening an existing chat saves the composer text under the chat you left and puts back the one you left there;
       • opening a NEW empty chat (Ctrl+N, «Новый чат») carries the text along — the usual «oh, this should be a new chat»;
       • the drafts survive a restart (localStorage `memu_drafts_v1`, at most 60 chats, 30 days), a sent message clears it.
     Hooks: the `memu:chat-open` event app.js already fires, `input` on the composer (debounced) — no timers, no observers.

   ↑ in an empty composer puts your last message of this chat back into it (to fix a typo and send again).

   window.MemuDrafts: get(chatId) set(chatId, text) clear(chatId)
*/
(function () {
  "use strict";
  if (window.MemuDrafts) return;
  const KEY = "memu_drafts_v1", MAX = 60, TTL = 30 * 864e5;
  const input = () => document.getElementById("input");
  let lastId = null, timer = 0, cache = null;

  function load() {
    if (cache) return cache;
    try { cache = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (_) { cache = {}; }
    if (typeof cache !== "object" || Array.isArray(cache)) cache = {};
    return cache;
  }
  function store() {
    const all = load(), now = Date.now();
    const rows = Object.entries(all).filter(([, d]) => d && d.t && now - (+d.at || 0) < TTL)
      .sort((a, b) => (+b[1].at || 0) - (+a[1].at || 0)).slice(0, MAX);
    cache = Object.fromEntries(rows);
    try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch (_) {}
  }
  const get = (id) => (id && load()[id]?.t) || "";
  function set(id, text) {
    if (!id || String(id).startsWith("temp-")) return;
    const all = load(), value = String(text || "");
    if (value.trim()) all[id] = { t: value, at: Date.now() };
    else if (all[id]) delete all[id];
    else return;
    store();
  }
  const clear = (id) => set(id, "");
  const chat = (id) => (typeof S !== "undefined" ? (S.chats || []).find((c) => c.id === id) : null);
  const lastUserText = (messages) => {
    for (let i = (messages || []).length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m && m.role === "user" && !m.internal && typeof m.content === "string") return m.content;
    }
    return "";
  };
  function put(text) {
    const box = input();
    if (!box || box.value === text) return;
    box.value = text;
    box.dispatchEvent(new Event("input", { bubbles: true }));   // autosize + the send button state
    if (text) { const end = text.length; try { box.setSelectionRange(end, end); } catch (_) {} }
  }

  function onChatOpen(event) {
    const id = event?.detail?.chatId || (typeof S !== "undefined" ? S.currentId : "");
    const box = input();
    if (!box || id === lastId) return;
    clearTimeout(timer);
    const text = box.value;
    const target = chat(id);
    const fresh = !target || !(target.messages || []).length;
    if (lastId === null) {
      // first chat after start: put back what was left unsent before the restart
      if (!text) restore(id, target);
    } else if (fresh && !get(id)) {
      // a new empty chat takes the text along
      clear(lastId);
      if (text.trim()) set(id, text);
    } else {
      set(lastId, text);
      restore(id, target);
      if (!get(id)) put("");
    }
    lastId = id;
  }
  function restore(id, target) {
    const draft = get(id);
    if (!draft) return;
    // sent already (the window closed before the composer change was saved) — not a draft any more
    if (draft === lastUserText(target?.messages)) { clear(id); return; }
    put(draft);
  }
  function save() {
    clearTimeout(timer); timer = 0;
    const box = input();
    if (box && typeof S !== "undefined" && S.currentId) { set(S.currentId, box.value); lastId = S.currentId; }
  }
  function afterSend() {
    // the composer is emptied by send() without an input event
    setTimeout(() => { const box = input(); if (box && !box.value.trim() && typeof S !== "undefined") clear(S.currentId); }, 0);
  }

  function recall(event) {
    if (event.key !== "ArrowUp" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
    const box = event.currentTarget;
    if (box.value !== "" || typeof S === "undefined") return;
    if ((document.getElementById("slash-menu") && !document.getElementById("slash-menu").hidden) || window.MemuMentions?.owns?.()) return;
    const text = lastUserText(S.messages);
    if (!text) return;
    event.preventDefault();
    put(text);
  }

  function boot() {
    const box = input();
    if (!box) return;
    box.addEventListener("input", (event) => {
      if (!event.isTrusted) return;
      clearTimeout(timer); timer = setTimeout(save, 400);
    });
    box.addEventListener("keydown", recall);
    box.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) afterSend(); });
    document.getElementById("btn-send")?.addEventListener("click", afterSend);
    document.addEventListener("memu:chat-open", onChatOpen);
    document.addEventListener("visibilitychange", () => { if (document.hidden && timer) save(); });
    window.addEventListener("pagehide", () => { if (timer) save(); });
    if (typeof S !== "undefined" && S.currentId && document.getElementById("messages")?.childElementCount) onChatOpen({ detail: { chatId: S.currentId } });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true }); else boot();

  window.MemuDrafts = Object.freeze({ get, set, clear });
})();
