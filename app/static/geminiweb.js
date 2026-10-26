

(function () {
  "use strict";
  const en = () => LANG === "en";
  const tr = (ru, eng) => (en() ? eng : ru);

  const svgDevtools = () => `<svg viewBox="0 0 240 92" class="gw-ill" aria-hidden="true">
    <rect x="1" y="1" width="238" height="90" rx="8" class="gw-ill-frame"/>
    <rect x="1" y="1" width="238" height="16" rx="8" class="gw-ill-bar"/>
    <text x="10" y="12" class="gw-ill-t">Elements  Console  Network  <tspan class="gw-ill-hl">Application</tspan></text>
    <rect x="6" y="22" width="64" height="64" rx="4" class="gw-ill-side"/>
    <text x="10" y="34" class="gw-ill-t">▾ Cookies</text>
    <rect x="12" y="38" width="56" height="11" rx="2" class="gw-ill-sel"/>
    <text x="14" y="46" class="gw-ill-t gw-ill-w">gemini.google…</text>
    <text x="78" y="32" class="gw-ill-t gw-ill-dim">Name                 Value</text>
    <rect x="76" y="36" width="158" height="12" rx="2" class="gw-ill-sel"/>
    <text x="80" y="45" class="gw-ill-t gw-ill-w">__Secure-1PSID     g.a000…</text>
    <rect x="76" y="51" width="158" height="12" rx="2" class="gw-ill-sel2"/>
    <text x="80" y="60" class="gw-ill-t">__Secure-1PSIDTS  sidts-…</text>
    <text x="80" y="76" class="gw-ill-t gw-ill-dim">filter: 1PSID</text>
  </svg>`;
  const svgCopy = () => `<svg viewBox="0 0 240 60" class="gw-ill" aria-hidden="true">
    <rect x="1" y="1" width="238" height="58" rx="8" class="gw-ill-frame"/>
    <text x="10" y="18" class="gw-ill-t gw-ill-dim">Value</text>
    <rect x="8" y="24" width="120" height="14" rx="3" class="gw-ill-sel"/>
    <text x="12" y="34" class="gw-ill-t gw-ill-w">g.a000xYz…  (double-click)</text>
    <rect x="140" y="20" width="92" height="32" rx="6" class="gw-ill-menu"/>
    <text x="148" y="33" class="gw-ill-t">Copy value</text>
    <text x="148" y="46" class="gw-ill-t gw-ill-dim">Ctrl+C</text>
  </svg>`;

  function guide() {
    const steps = [
      [tr("Откройте Gemini и войдите", "Open Gemini and sign in"),
        tr("В Chrome, Edge или Firefox откройте gemini.google.com под нужным Google-аккаунтом и отправьте любое сообщение.",
          "In Chrome, Edge or Firefox open gemini.google.com with the Google account you want and send any message."), ""],
      [tr("Откройте куки сайта", "Open the site cookies"),
        tr("Нажмите F12. Chrome/Edge: вкладка Application → Cookies → https://gemini.google.com. Firefox: Storage («Хранилище») → Cookies. В фильтре введите 1PSID.",
          "Press F12. Chrome/Edge: Application → Cookies → https://gemini.google.com. Firefox: Storage → Cookies. Type 1PSID in the filter."), svgDevtools()],
      [tr("Скопируйте два значения", "Copy two values"),
        tr("Нужны только __Secure-1PSID и __Secure-1PSIDTS (домен .google.com). Дважды щёлкните по Value → Ctrl+C. Можно выделить обе строки таблицы и скопировать целиком — Proxy разберётся.",
          "Only __Secure-1PSID and __Secure-1PSIDTS (domain .google.com) are needed. Double-click Value → Ctrl+C. You can also select both table rows and copy them — Proxy will sort it out."), svgCopy()],
      [tr("Вставьте и проверьте", "Paste and check"),
        tr("Вставьте в поле ниже — подойдёт любой формат. Под полем сразу видно, что распознано. Затем «Проверить подключение».",
          "Paste into the field below — any format works. What was recognized shows right under it. Then choose Check connection."), ""],
    ];
    const trouble = [
      [tr("«Нет __Secure-1PSID»", "\"__Secure-1PSID is missing\""), tr("Вы не вошли в Google в этом браузере или копировали куки другого сайта.", "You are not signed in to Google in that browser or copied another site's cookies.")],
      [tr("«Google вернул страницу входа»", "\"Google returned the sign-in page\""), tr("Куки устарели: обновите страницу Gemini и скопируйте заново. Не выходите из аккаунта в браузере — выход обнуляет куки.", "The cookies are stale: reload Gemini and copy again. Do not sign out in the browser — signing out invalidates them.")],
      [tr("Несколько Google-аккаунтов", "Several Google accounts"), tr("Укажите номер N из адреса gemini.google.com/u/N/app в поле «Номер аккаунта».", "Set N from gemini.google.com/u/N/app in the account number field.")],
      [tr("Капча, 429 или ошибка 1096", "Captcha, 429 or error 1096"), tr("Google ограничил ваш IP (часто в гостевом режиме и у VPN). Добавьте куки, смените сеть или задайте прокси.", "Google limited your IP (common in guest mode and on VPNs). Add cookies, switch network or set a proxy.")],
      [tr("Ошибка 1060 / регион", "Error 1060 / region"), tr("Gemini недоступен в вашей стране — укажите прокси/VPN в поле «Прокси» аккаунта.", "Gemini is unavailable in your country — set a proxy/VPN in the account Proxy field.")],
      [tr("Ошибка 1037", "Error 1037"), tr("Закончился лимит модели (обычно Pro). Возьмите Flash или подождите.", "The model limit ran out (usually Pro). Use Flash or wait.")],
    ];
    return `<details class="provider-guide gw-guide" open>
      <summary>${esc(tr("Как подключить Gemini Web", "How to connect Gemini Web"))}</summary>
      <div class="gw-login" hidden>
        <button type="button" class="btn primary small" data-gw-login>${esc(tr("Войти через Google в окне", "Sign in with Google in a window"))}</button>
        <span class="gw-muted">${esc(tr("Куки подставятся сами. Если Google не пускает во встроенном окне — используйте шаги ниже.", "Cookies are filled in automatically. If Google blocks the built-in window, use the steps below."))}</span>
      </div>
      <ol class="gw-steps">${steps.map(([title, body, ill]) => `<li><b>${esc(title)}</b><p>${esc(body)}</p>${ill}</li>`).join("")}</ol>
      <p class="gw-muted">${esc(tr("Без кук работает гостевой режим (только Flash), но Google часто ограничивает его капчей. Вход обновляется сам каждые ~10 минут.", "Without cookies guest mode works (Flash only), but Google often limits it with a captcha. The sign-in refreshes itself every ~10 minutes."))}</p>
      <details class="gw-trouble"><summary>${esc(tr("Если не работает", "If it does not work"))}</summary>
        <dl>${trouble.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      </details>
    </details>`;
  }

  function renderParse(box, data) {
    if (!data) { box.innerHTML = ""; return; }
    const msgs = [...(data.errors || []).map((m) => ["err", m]), ...(data.warnings || []).map((m) => ["warn", m])];
    const head = data.ok
      ? `<span class="gw-ok">✓ ${esc(tr("Распознано", "Recognized"))}: ${esc((data.names || []).filter((n) => n.startsWith("__Secure-1PSID")).join(", "))} · ${esc(tr("всего", "total"))} ${data.names.length}</span>`
      : "";
    box.innerHTML = head + msgs.map(([kind, m]) => `<span class="gw-${kind}">${kind === "err" ? "✕" : "!"} ${esc(en() ? m.en : m.ru)}</span>`).join("");
  }

  function renderSteps(box, result) {
    const icon = { ok: "✓", warn: "!", fail: "✕", skip: "·", run: "…" };
    box.innerHTML = `<ol class="gw-diag">${result.steps.map((s) => `<li class="gw-st-${s.status}"><i>${icon[s.status] || "·"}</i><div><b>${esc(en() ? s.title_en : s.title_ru)}</b><p>${esc(en() ? s.detail_en : s.detail_ru)}</p>${s.fix_ru ? `<p class="gw-fix">${esc(en() ? s.fix_en : s.fix_ru)}</p>` : ""}</div></li>`).join("")}</ol>${renderLimits(result.limits)}${renderModels(result.models)}`;
  }

  function renderLimits(data) {
    if (!data || !Array.isArray(data.rows) || !data.rows.length) return "";
    const when = (ts) => (Number(ts) > 0 ? new Date(Number(ts) * 1000).toLocaleString(en() ? "en-GB" : "ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "");
    const rows = data.rows.map((r) => {
      const name = (en() && r.display_en) || r.display || "—";
      const limited = Number(r.limit) > 0 && r.used !== null && r.used !== undefined;
      const pct = limited ? Math.max(0, Math.min(100, Math.round(Number(r.used) / Number(r.limit) * 100))) : 0;
      const value = limited ? (r.unit === "%" ? `${r.used}%` : `${r.used} / ${r.limit}`) : ((en() && r.unit_en) || r.unit || "—");
      const reset = when(r.reset);
      const note = (en() && r.note_en) || r.note || "";
      const level = pct >= 100 ? "full" : pct >= 80 ? "high" : "";
      return `<li class="${level ? "gw-lim-" + level : ""}"><span class="gw-lim-name" title="${esc(name)}">${esc(name)}</span><span class="gw-lim-val">${esc(value)}</span>${limited ? `<span class="gw-lim-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></span>` : ""}${reset || note ? `<small>${reset ? esc(tr("сброс ", "resets ") + reset) : ""}${reset && note ? " · " : ""}${esc(note)}</small>` : ""}</li>`;
    }).join("");
    const plan = data.subscription ? `<span class="gw-lim-plan">${esc(data.subscription)}</span>` : "";
    return `<div class="gw-limits"><div class="gw-lim-head"><b>${esc(tr("Лимиты аккаунта", "Account limits"))}</b>${plan}</div><ul>${rows}</ul><p class="gw-muted">${esc(tr("Как в окне «Лимиты использования» Gemini. Также — кнопка ✦ на карточке аккаунта.", "As in Gemini's «Usage limits» window. Also — the ✦ button on the account card."))}</p></div>`;
  }

  function renderModels(models) {
    if (!Array.isArray(models) || !models.length) return "";
    const rows = models.map((m) => {
      const state = m.available === false ? tr("только со входом", "sign-in only") : m.available === null ? tr("стандартный список", "standard list") : "";
      return `<li class="${m.available === false ? "gw-off" : ""}"><b>${esc(m.display || m.name)}</b><code>${esc(m.name)}</code>${state ? `<span class="gw-muted">${esc(state)}</span>` : ""}${m.description ? `<p>${esc(m.description)}</p>` : ""}</li>`;
    }).join("");
    return `<div class="gw-models"><b>${esc(tr("Модели этого аккаунта", "This account's models"))}</b><ul>${rows}</ul><p class="gw-muted">${esc(tr("Уровень размышлений (Low / Medium / High) — пикер уровня в чате, reasoning_effort или суффикс @effort=high.", "Thinking level (Low / Medium / High) — the chat level picker, reasoning_effort or the @effort=high suffix."))}</p><p class="gw-muted">${esc(tr("Картинки и файлы: прикрепите к сообщению — уйдут в Gemini. Рисование: «Нарисуй …» в чате или /v1/images/generations; видео Veo — «Сделай видео …», если доступно аккаунту.", "Images and files: attach them to the message — they go to Gemini. Drawing: «Draw …» in the chat or /v1/images/generations; Veo video — «Make a video …» when the account has it."))}</p></div>`;
  }

  function wire(prefix, accountId) {
    const field = document.getElementById(prefix + "cookie");
    if (!field) return;
    const value = (key) => (document.getElementById(prefix + key) || {}).value || "";
    const feedback = document.createElement("div");
    feedback.className = "gw-parse"; feedback.setAttribute("aria-live", "polite");
    const tools = document.createElement("div");
    tools.className = "gw-tools";
    tools.innerHTML = `<button type="button" class="btn small" data-gw-check>${esc(tr("Проверить подключение", "Check connection"))}</button>`;
    const diag = document.createElement("div");
    diag.className = "gw-diag-box";
    field.after(feedback, tools, diag);

    let timer = 0, seq = 0;
    const parse = () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const raw = field.value.trim(), my = ++seq;
        if (!raw) return renderParse(feedback, null);
        try {
          const data = await api("/api/admin/geminiweb/parse", { method: "POST", body: { cookie: raw } });
          if (my === seq) renderParse(feedback, data);
        } catch (e) { feedback.textContent = e.message; }
      }, 350);
    };
    field.addEventListener("input", parse);
    field.addEventListener("blur", async () => {

      const raw = field.value.trim();
      if (!raw) return;
      try {
        const data = await api("/api/admin/geminiweb/parse", { method: "POST", body: { cookie: raw } });
        if (data.ok && data.cookie && data.cookie !== raw) { field.value = data.cookie; renderParse(feedback, data); }
      } catch (e) {  }
    });
    if (field.value.trim()) parse();

    tools.querySelector("[data-gw-check]").onclick = async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      renderSteps(diag, { steps: [{ status: "run", title_ru: "Проверяю…", title_en: "Checking…", detail_ru: "Это займёт до минуты.", detail_en: "This takes up to a minute." }] });
      try {
        const fields = { cookie: field.value, auth_user: value("auth_user"), gemini_bl: value("gemini_bl") };
        const result = await api("/api/admin/geminiweb/diagnose", { method: "POST", body: { account_id: accountId || "", fields, proxy: ((document.getElementById("m-proxy") || document.getElementById("af-proxy") || {}).value || "").trim() } });
        renderSteps(diag, result);
      } catch (e) { diag.textContent = e.message; }
      button.disabled = false;
    };

    const loginBox = document.querySelector("#modal-box .gw-login");
    if (!loginBox) return;
    api("/api/admin/geminiweb/login").then((info) => {
      if (!info.available || !loginBox.isConnected) return;
      loginBox.hidden = false;
      loginBox.querySelector("[data-gw-login]").onclick = async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const { flow_id: flow } = await api("/api/admin/geminiweb/login", { method: "POST", body: {} });
          toast(tr("Войдите в Google в открывшемся окне.", "Sign in to Google in the window that opened."));
          for (let i = 0; i < 450 && loginBox.isConnected; i++) {
            await new Promise((r) => setTimeout(r, 2000));
            const state = await api(`/api/admin/geminiweb/login/${flow}`);
            if (state.state === "done") {
              field.value = state.cookie; field.dispatchEvent(new Event("input", { bubbles: true }));
              toast(tr("Куки получены. Проверьте подключение и сохраните.", "Cookies received. Check the connection and save."));
              break;
            }
            if (state.state !== "waiting") { toast(state.message || tr("Окно закрыто.", "The window was closed."), "warn"); break; }
          }
        } catch (e) { toast(e.message, "warn"); }
        button.disabled = false;
      };
    }).catch(() => {});
  }

  window.GeminiWebSetup = { guide, wire };
})();
