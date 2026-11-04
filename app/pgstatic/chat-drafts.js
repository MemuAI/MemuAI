

(function () {
  "use strict";
  if (window.MemuDrafts) return;
  const KEY = "memu_drafts_v1", MAX = 60, TTL = 30 * 864e5;
  // B-CHAT-M4: drafts of temporary chats live in sessionStorage — a temp
  // chat is per-tab by nature, and its draft must not leak into another
  // tab or survive as a localStorage row pointing at a chat that no longer
  // exists. Capped to 20 entries.
  const TKEY = "memu_drafts_temp_v1", TMAX = 20;
  const isTemp = (id) => !!id && String(id).startsWith("temp-");
  function loadTemp() {
    try { const v = JSON.parse(sessionStorage.getItem(TKEY) || "{}") || {}; return (typeof v === "object" && !Array.isArray(v)) ? v : {}; }
    catch (_) { return {}; }
  }
  function storeTemp(all) {
    const rows = Object.entries(all).filter(([, d]) => d && d.t)
      .sort((a, b) => (+b[1].at || 0) - (+a[1].at || 0)).slice(0, TMAX);
    try { sessionStorage.setItem(TKEY, JSON.stringify(Object.fromEntries(rows))); } catch (_) {}
  }
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
  const get = (id) => (id && (isTemp(id) ? loadTemp()[id]?.t : load()[id]?.t)) || "";
  // Returns true when the draft was stored (or removed); temp- chats go
  // to sessionStorage, permanent chats to localStorage.
  function set(id, text) {
    if (!id) return false;
    const value = String(text || "");
    if (isTemp(id)) {
      const all = loadTemp();
      if (value.trim()) all[id] = { t: value, at: Date.now() };
      else if (all[id]) delete all[id];
      else return false;
      storeTemp(all);
      return true;
    }
    const all = load();
    if (value.trim()) all[id] = { t: value, at: Date.now() };
    else if (all[id]) delete all[id];
    else return false;
    store();
    return true;
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
    box.dispatchEvent(new Event("input", { bubbles: true }));
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

      if (!text) restore(id, target);
    } else if (fresh && !get(id)) {

      // B-CHAT-M4: save the outgoing draft BEFORE clearing the old
      // one — the old code cleared first, and set() silently dropped
      // temp- targets, so switching from a temp chat to a fresh chat
      // lost the text. Clearing the previous draft only after a
      // successful save keeps the text if the save fails.
      if (text.trim() && set(id, text)) clear(lastId);
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

    if (draft === lastUserText(target?.messages)) { clear(id); return; }
    put(draft);
  }
  function save() {
    clearTimeout(timer); timer = 0;
    const box = input();
    if (box && typeof S !== "undefined" && S.currentId) { set(S.currentId, box.value); lastId = S.currentId; }
  }
  function afterSend() {

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
