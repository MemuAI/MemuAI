/* MemuAI Mini App — in a normal browser, outside Telegram (Round 29).
 *
 * Telegram signs every launch of the Mini App (initData); a browser has nothing like it, so the browser asks
 * the computer for a sign-in request and shows a QR code + code. The user confirms it in the Mini App on
 * the phone (Settings → «Sign in on another device»: scan or type the code, check which browser asks,
 * approve). Then this page receives a normal 12-hour session. A one-time link from the bot (/web) works
 * too: …/tgapp/#login=<nonce>. The session lives in sessionStorage (this tab only) unless «Keep me signed
 * in on this device» is ticked. «Reset sessions» on the computer signs every browser out.
 */
(function () {
  "use strict";
  const KEY = "memu_web_session";
  let kitRef = null;

  function lang() {
    try { const saved = localStorage.getItem("memu_web_lang"); if (saved === "ru" || saved === "en") return saved; } catch (e) { /* private */ }
    return /^(ru|uk|be|kk)/i.test(navigator.language || "") ? "ru" : "en";
  }
  let LANG = lang();
  const tr = (ru, en) => (LANG === "en" ? en : ru);
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function stored() {
    for (const store of [sessionStorage, localStorage]) {
      try {
        const data = JSON.parse(store.getItem(KEY) || "null");
        if (data && data.token && data.expires * 1000 > Date.now() + 60000) return data;
        if (data) store.removeItem(KEY);
      } catch (e) { /* private mode */ }
    }
    return null;
  }
  function remember(session, keep) {
    const text = JSON.stringify({ token: session.token, expires: session.expires });
    try { (keep ? localStorage : sessionStorage).setItem(KEY, text); } catch (e) { /* private mode */ }
  }
  function forget() {
    try { sessionStorage.removeItem(KEY); } catch (e) { /* ignore */ }
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  }
  async function post(name, body) {
    const response = await fetch("api/" + name, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || ("HTTP " + response.status)); error.code = data.code || ""; error.status = response.status; throw error; }
    return data;
  }

  async function redeemLink() {
    const match = /(?:^#|&)login=([A-Za-z0-9_-]{16,64})/.exec(location.hash);
    if (!match) return null;
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { location.hash = ""; }
    try {
      const data = await post("weblogin.redeem", { nonce: match[1] });
      if (data.lang) LANG = data.lang;
      remember(data, false);
      return data;
    } catch (error) {
      return { error: error.message };
    }
  }

  function screen() {
    let root = document.getElementById("web-login");
    if (!root) { root = document.createElement("div"); root.id = "web-login"; root.className = "web-login"; document.body.appendChild(root); }
    return root;
  }
  const LOGO = '<svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true"><defs><linearGradient id="wl" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e89a7e"/><stop offset="1" stop-color="#9b4a8f"/></linearGradient></defs><rect width="64" height="64" rx="18" fill="url(#wl)"/><path d="M17 44V22l15 13 15-13v22" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function deviceFlow(initialError) {
    return new Promise((resolve) => {
      const root = screen();
      let request = null, timer = 0, ticking = 0, stopped = false;
      const keepDefault = (() => { try { return localStorage.getItem("memu_web_keep") === "1"; } catch (e) { return false; } })();
      const draw = (state = "", error = "") => {
        const qr = request && window.MemuQR ? window.MemuQR.svg(request.qr, { ecl: "M" }) : "";
        const left = request ? Math.max(0, Math.round(request.expires - Date.now() / 1000)) : 0;
        root.innerHTML = `<div class="web-card" role="dialog" aria-labelledby="wl-title">${LOGO}
          <h2 id="wl-title">${esc(tr("Вход в MemuAI", "Sign in to MemuAI"))}</h2>
          <p>${esc(tr("Это ваш MemuAI с компьютера. Подтвердите вход из Telegram — так никто, кроме вас, сюда не попадёт.", "This is your MemuAI from your computer. Confirm the sign-in from Telegram — nobody but you gets in."))}</p>
          ${request ? `<div class="qr">${qr}</div><div class="code" aria-label="${esc(tr("Код", "Code"))}">${esc(request.display)}</div>
            <div class="state" id="wl-left">${esc(tr("Код действует ещё", "The code is valid for"))} ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</div>` : `<div class="state">${esc(tr("Готовлю код…", "Preparing a code…"))}</div>`}
          <ol><li>${esc(tr("Откройте MemuAI в Telegram на телефоне", "Open MemuAI in Telegram on your phone"))}</li>
            <li>${esc(tr("Настройки → «Войти на другом устройстве»", "Settings → “Sign in on another device”"))}</li>
            <li>${esc(tr("Отсканируйте QR-код или введите код и подтвердите", "Scan the QR code or type the code and confirm"))}</li></ol>
          <p style="font-size:12.5px">${esc(tr("Или отправьте боту /web — придёт одноразовая ссылка для входа.", "Or send /web to the bot — you get a one-time sign-in link."))}</p>
          <label class="keep"><input type="checkbox" id="wl-keep" ${keepDefault ? "checked" : ""}> ${esc(tr("Не выходить на этом устройстве (12 ч)", "Keep me signed in on this device (12 h)"))}</label>
          <div class="state ${error ? "err" : ""}" id="wl-state" aria-live="polite">${esc(error || state)}</div>
          <div class="row"><button class="btn small secondary" id="wl-new">${esc(tr("Новый код", "New code"))}</button>
            <button class="btn small secondary" id="wl-lang">${LANG === "en" ? "Русский" : "English"}</button></div></div>`;
        document.getElementById("wl-new").onclick = () => begin();
        document.getElementById("wl-lang").onclick = () => { LANG = LANG === "en" ? "ru" : "en"; try { localStorage.setItem("memu_web_lang", LANG); } catch (e) { /* ignore */ } draw(state, error); };
        document.getElementById("wl-keep").onchange = (event) => { try { localStorage.setItem("memu_web_keep", event.target.checked ? "1" : "0"); } catch (e) { /* ignore */ } };
      };
      async function begin() {
        clearTimeout(timer); clearInterval(ticking);
        request = null; draw("", notice);
        try {
          request = await post("weblogin.start", {});
          let chosen = "";
          try { chosen = localStorage.getItem("memu_web_lang") || ""; } catch (e) { /* private */ }
          if (request.lang && !chosen) LANG = request.lang;
          // A used / stale #login link: say so above the fresh code instead of silently showing the QR.
          draw(tr("Ждём подтверждения в Telegram…", "Waiting for the confirmation in Telegram…"), notice);
          notice = "";
          ticking = setInterval(() => {
            const el = document.getElementById("wl-left"); if (!el || !request) return;
            const left = Math.max(0, Math.round(request.expires - Date.now() / 1000));
            el.textContent = `${tr("Код действует ещё", "The code is valid for")} ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
          }, 1000);
          poll();
        } catch (error) {
          const text = error.code === "web_off" ? tr("Вход из браузера выключен на компьютере: MemuAI → Настройки → Telegram → Mini App.", "Browser sign-in is off on the computer: MemuAI → Settings → Telegram → Mini App.")
            : error.code === "disabled" ? tr("Mini App выключен в настройках MemuAI.", "The Mini App is turned off in MemuAI settings.") : error.message;
          draw("", text);
        }
      }
      async function poll() {
        if (stopped || !request) return;
        let data;
        try { data = await post("weblogin.poll", { request_id: request.request_id }); }
        catch (error) { timer = setTimeout(poll, 4000); return; }
        if (data.status === "approved") {
          stopped = true; clearInterval(ticking);
          const keep = !!(document.getElementById("wl-keep") || {}).checked;
          remember(data, keep);
          root.remove();
          resolve(data);
          return;
        }
        if (data.status === "denied") { request = null; clearInterval(ticking); draw("", tr("Вход отклонён на телефоне.", "The sign-in was declined on the phone.")); return; }
        if (data.status === "expired") { begin(); return; }
        timer = setTimeout(poll, 2000);
      }
      let notice = initialError || "";
      begin();
    });
  }

  window.MemuTgWeb = {
    async signIn(kit) {
      kitRef = kit;
      const linked = await redeemLink();
      if (linked && linked.token) return linked;
      const saved = stored();
      if (saved) return saved;
      document.documentElement.lang = LANG;
      if (kit && kit.S) kit.S.lang = LANG;
      const session = await deviceFlow(linked && linked.error ? linked.error : "");
      if (kit && kit.S) kit.S.lang = session.lang || LANG;
      return session;
    },
    expired() {
      forget();
      if (window.MemuTgWeb._reloading) return;
      window.MemuTgWeb._reloading = true;
      try { if (kitRef) kitRef.toast(tr("Сессия закончилась — войдите снова", "The session has ended — sign in again"), "err"); } catch (e) { /* ignore */ }
      setTimeout(() => location.reload(), 1200);
    },
    signOut() { forget(); location.reload(); },
    keptOnDevice() { try { return !!JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return false; } },
  };
})();
