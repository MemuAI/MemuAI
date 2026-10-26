

(function () {
  "use strict";
  const VERSION = "1.0-r33";
  const attempted = new Set();
  const inflight = new Map();
  const dataCache = new Map();
  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en";
  const t = (ru, english) => (en() ? english : ru);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ico = (name) => (typeof icon === "function" ? icon(name) : "");

  const isLocal = (u) => /^\/api\/pg\/media\/file\/[0-9a-f]{64}\.(png|jpg|webp|gif)(\?|$)/.test(String(u || ""));
  const isRemote = (u) => /^https?:\/\//i.test(String(u || ""));
  const isExpired = (m, u) => !!(m && (m.expiredImages || []).includes(u));

  function expiredHtml(i, u) {
    return `<div class="msg-images"><div class="mc-expired" role="group" aria-label="${esc(t("Изображение истекло", "Image expired"))}" data-mc-expired="${i}" data-url="${esc(u)}">
      <div class="mc-expired-ico" aria-hidden="true">${ico("image")}</div>
      <div class="mc-expired-text"><b>${esc(t("Изображение истекло", "Image expired"))}</b>
        <small>${esc(t("Сервис хранил его по временной ссылке, и она больше не работает.", "The service kept it behind a temporary link that no longer works."))}</small></div>
      <button type="button" class="mm-btn primary mc-regen" data-mc-regen="${i}">${ico("retry")}<span>${esc(t("Сгенерировать заново", "Regenerate"))}</span></button>
    </div></div>`;
  }

  function swapUrl(m, from, to) {
    m.images = (m.images || []).map((u) => (u === from ? to : u));
    for (const h of (m.media && m.media.history) || []) if (Array.isArray(h.urls)) h.urls = h.urls.map((u) => (u === from ? to : u));
  }
  function markExpired(m, u) {
    if (isExpired(m, u)) return false;
    m.expiredImages = [...new Set([...(m.expiredImages || []), u])];
    return true;
  }
  function stillHere(chatId, m) {
    return typeof S !== "undefined" && S.currentId === chatId && (S.messages || []).includes(m);
  }
  function redraw(chatId, m) {
    if (!stillHere(chatId, m)) return;
    if (typeof renderMessages === "function") renderMessages();
    if (typeof persistChat === "function") persistChat();
  }

  async function check(m, u) {
    const chatId = typeof S !== "undefined" ? S.currentId : "";
    attempted.add(u);
    if (!inflight.has(u)) {
      inflight.set(u, fetch("/api/pg/media/cache", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: u }) })
        .then((r) => r.json().catch(() => ({})))
        .catch(() => ({}))
        .finally(() => setTimeout(() => inflight.delete(u), 0)));
    }
    const result = await inflight.get(u);
    if (result && result.url && isLocal(result.url)) { swapUrl(m, u, result.url); redraw(chatId, m); }
    else if (result && result.expired) { if (markExpired(m, u)) redraw(chatId, m); }

  }

  function scan() {
    if (typeof S === "undefined" || !Array.isArray(S.messages)) return;
    for (const m of S.messages) {
      if (!m || m.role !== "assistant" || m.streaming) continue;
      for (const u of m.images || []) {
        if (!isRemote(u) || attempted.has(u) || isExpired(m, u) || (m.blockedImages || []).includes(u)) continue;
        check(m, u);
      }
    }
  }

  function regenerate(index) {
    const m = S.messages[index];
    if (!m) return;
    const gone = new Set(m.expiredImages || []);
    if (m.media && typeof MemuMedia !== "undefined") {
      m.images = (m.images || []).filter((u) => !gone.has(u));
      m.expiredImages = [];
      MemuMedia.again(index);
      return;
    }
    let u = index - 1;
    while (u >= 0 && S.messages[u] && S.messages[u].role !== "user") u--;
    const user = S.messages[u];
    const imageModels = (S.imageModels || []);
    if (user && user.content && typeof sendImage === "function" && imageModels.length && !generationJobs.has(S.currentId)) {
      const model = imageModels.includes(m.model) ? m.model : imageModels[0];
      sendImage(String(user.content), model, (user.attachments || []).filter((a) => String(a.data || "").startsWith("data:image")));
      return;
    }
    if (typeof retryAnswerAt === "function") retryAnswerAt(index);
  }

  function hydrate(box) {
    if (!box) return;
    box.querySelectorAll("[data-mc-regen]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); regenerate(+b.dataset.mcRegen); });

    box.querySelectorAll(".msg-assistant .imgwrap img[data-original]").forEach((img) => {
      if (img.dataset.mcBound) return;
      img.dataset.mcBound = "1";
      const fail = () => {
        const row = img.closest("[data-msg]"); const m = row && S.messages[+row.dataset.msg];
        const u = img.dataset.original;
        if (!m || m.streaming) return;
        if (isLocal(u)) { if (markExpired(m, u)) redraw(S.currentId, m); }
        else if (isRemote(u) && !(m.blockedImages || []).includes(u) && !inflight.has(u)) check(m, u);
      };
      if (img.complete && img.naturalWidth === 0 && img.getAttribute("src")) fail();
      else img.addEventListener("error", fail, { once: true });
    });
    scan();
  }

  async function toDataUrl(u) {
    if (dataCache.has(u)) return dataCache.get(u);
    const resp = await fetch(u);
    if (!resp.ok) throw new Error(String(resp.status));
    const blob = await resp.blob();
    const value = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(blob); });
    if (dataCache.size > 12) dataCache.delete(dataCache.keys().next().value);
    dataCache.set(u, value);
    return value;
  }

  async function forModel(list, m) {
    const out = [];
    for (const u of list || []) {
      if (isExpired(m, u)) continue;
      if (/^data:image/i.test(u) || isRemote(u)) out.push(u);
      else if (isLocal(u)) { try { out.push(await toDataUrl(u)); } catch (e) {} }
    }
    return out;
  }

  window.MemuMediaCache = Object.freeze({ VERSION, isLocal, isRemote, isExpired, expiredHtml, hydrate, scan, regenerate, forModel });
})();
