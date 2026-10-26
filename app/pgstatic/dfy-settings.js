

(function () {
  "use strict";
  let controller = null;
  let last = { models: [], fallback: "" };

  const F = () => (window.MemuDFY && window.MemuDFY.forms) || null;
  const sees = (m) => m && m.caps && m.caps.vision === true;

  function modelNote(ctx, chosen) {
    const { en, kit: K } = ctx;
    const L = (ru, e) => (en ? e : ru);
    const vision = last.models.filter(sees);
    const fix = L("Как исправить: добавьте модель с Vision в MemuAI Proxy или включите «Зрение» вручную в её карточке (MemuAI Proxy → Модели → «Все сведения о модели» → «Указать вручную»).",
      "How to fix: add a vision model in MemuAI Proxy or turn “Vision” on manually in its card (MemuAI Proxy → Models → “Everything about this model” → “Set manually”).");
    if (!vision.length) {
      return K.note(L("Нет моделей с Vision — DFY смотрит на скриншоты страниц и без такой модели не запустится. ", "No vision models — DFY looks at page screenshots and cannot start without one. ") + fix, "warn");
    }
    if (chosen && !vision.some((m) => m.id === chosen)) {
      const known = last.models.some((m) => m.id === chosen);
      return K.note((known
        ? L("Сохранённая модель больше не поддерживает изображения — DFY с ней не запустится. ", "The saved model no longer supports images — DFY will not start with it. ")
        : L("Сохранённой модели больше нет в списке MemuAI Proxy. ", "The saved model is no longer in the MemuAI Proxy list. "))
        + L("Выберите модель из списка или нажмите «По умолчанию».", "Pick a model from the list or press “Default”.")
        + ` <button type="button" class="pbtn ghost" id="st-dfy-model-reset" style="margin-left:6px">${L("По умолчанию", "Default")}</button>`, "warn");
    }
    return "";
  }

  function modelOptions(ctx, chosen) {
    const { en, esc } = ctx;
    const L = (ru, e) => (en ? e : ru);
    const vision = last.models.filter(sees);
    const stale = chosen && !vision.some((m) => m.id === chosen)
      ? `<option value="${esc(chosen)}" selected>${esc(ctx.modelName(chosen))}${esc(L(" — без Vision", " — no vision"))}</option>` : "";
    return `<option value="">${esc(L("Модель Vision по умолчанию", "Default Vision model"))}${last.fallback ? " · " + esc(ctx.modelName(last.fallback)) : esc(L(" (нет)", " (none)"))}</option>` + stale +
      vision.map((m) => `<option value="${esc(m.id)}" ${m.id === chosen ? "selected" : ""}>${esc(ctx.modelName(m.id))}</option>`).join("");
  }

  function html(ctx) {
    const { en, pref, esc, kit: K } = ctx;
    const L = (ru, e) => (en ? e : ru);
    const models = (ctx.models || []).slice().sort((a, b) => ctx.modelName(a.id).localeCompare(ctx.modelName(b.id)));
    const fallback = ctx.defaultVisionModel ? ctx.defaultVisionModel() : (models.find(sees)?.id || "");
    last = { models, fallback, modelName: ctx.modelName };
    const chosen = pref("dfy_model", "");
    const note = modelNote(ctx, chosen);
    const main = K.section({ id: "dfy-main", title: "Do For You",
      desc: L("ИИ выполняет дела за вас в своём браузере: бронирует, заказывает, регистрируется, заполняет формы. Основная модель чата решает, когда передать задачу, и передаёт её вместе с контекстом переписки; выполняет модель ниже. Также: команда /dfy в чате.",
        "The AI does things for you in its own browser: books, orders, signs up, fills forms. The chat model decides when to hand a task over and passes the conversation along; the model below carries it out. Also: the /dfy command in chat."),
      body:
        K.row({ label: L("Включить DFY", "Enable DFY"), key: "dfy_enabled", def: true, keywords: "dfy do for you агент agent",
          control: K.toggle("st-dfy-enabled", pref("dfy_enabled", true)) })
        + K.row({ label: L("Модель DFY", "DFY model"), when: "st-dfy-enabled", cls: "sx-row--sub", keywords: "dfy model модель vision зрение",
          desc: L("Показаны только модели с Vision: агент смотрит на скриншоты страниц.", "Only vision models are listed: the agent looks at page screenshots."),
          control: `<select class="pinput compact sx-select" id="st-dfy-model" style="max-width:300px;margin:0">${modelOptions(ctx, chosen)}</select>` })
        + `<div class="sx-row sx-row--note" id="st-dfy-model-note"${note ? "" : ' style="display:none"'}>${note}</div>`
        + K.row({ label: L("Лимит шагов", "Step limit"), when: "st-dfy-enabled", cls: "sx-row--sub", key: "dfy_max_steps", def: 30, keywords: "steps шаги лимит limit",
          desc: L("Сколько действий в браузере агент может сделать за одну задачу (5–80). Вопросы к вам не считаются.", "How many browser actions one task may take (5–80). Questions to you do not count."),
          control: K.input("st-dfy-steps", pref("dfy_max_steps", 30), { type: "number", width: 90, attrs: 'min="5" max="80"' }) })
        + K.row({ label: L("Подтверждения", "Confirmations"), when: "st-dfy-enabled", cls: "sx-row--sub", key: "dfy_confirm_mode", def: "irreversible", keywords: "confirm подтверждение оплата payment",
          desc: L("Оплата, бронирование, отправка и регистрация подтверждаются всегда. «Каждое действие» — спрашивать перед любым кликом и вводом.", "Payment, booking, sending and sign-up are always confirmed. “Every action” asks before every click and input."),
          control: K.select("st-dfy-confirm", [["irreversible", L("Перед необратимыми", "Before irreversible steps")], ["every_step", L("Каждое действие", "Every action")]], pref("dfy_confirm_mode", "irreversible"), { width: 220 }) })
        + K.row({ label: L("Показывать окно браузера", "Show the browser window"), when: "st-dfy-enabled", cls: "sx-row--sub", key: "dfy_browser_visible", def: false, keywords: "visible окно фон background headless",
          desc: L("Выкл — браузер работает в фоне, шаги и скриншоты видны в карточке задачи", "Off — the browser runs in the background; steps and screenshots appear in the task card"),
          control: K.toggle("st-dfy-visible", pref("dfy_browser_visible", false)) }) });
    const browser = K.section({ id: "dfy-browser", title: L("Браузер DFY", "DFY browser"),
      desc: L("При разрешённом «DFY в моём Chrome» перед каждой задачей вы выбираете браузер. Отдельный профиль Edge сохраняет свои куки и входы.", "When “DFY in my Chrome” is enabled, choose a browser before each task. The separate Edge profile keeps its own cookies and logins."),
      body: K.row({ label: L("Браузер для планирования", "Browser for scheduled tasks"),
        desc: L("Задачи по расписанию используют этот вариант без вопроса. В самой задаче можно выбрать другой. Chrome требует разрешения DFY и планирования в интеграции.", "Scheduled tasks use this default without asking. Each task can override it. Chrome requires DFY and scheduled access in the integration."),
        control: K.select("st-dfy-schedule-browser", [["isolated", L("Отдельный браузер DFY", "Separate DFY browser")], ["chrome", L("Мой Chrome", "My Chrome")]], pref("dfy_schedule_browser", "isolated"), { width: 240 }) })
      + K.row({ label: L("Очистить сессии", "Clear sessions"), desc: L("Выйти со всех сайтов: удалить куки, кэш и хранилище профиля DFY", "Sign out everywhere: delete cookies, cache and storage of the DFY profile"), keywords: "cookies куки сессии sessions clear очистить",
        control: `<button type="button" class="pbtn ghost danger" id="st-dfy-clear">${esc(L("Очистить", "Clear"))}</button>` }) });

    const proxy = K.section({ id: "dfy-proxy", title: L("Прокси", "Proxy"),
      desc: L("Весь трафик браузера DFY — страницы, загрузки, WebSocket — идёт через ваш прокси. Имена сайтов разрешает прокси (без утечки DNS), WebRTC не раскрывает ваш адрес. Если прокси недоступен, DFY не откроет сайт напрямую.",
        "All DFY browser traffic — pages, downloads, WebSocket — goes through your proxy. Site names are resolved by the proxy (no DNS leak) and WebRTC does not reveal your address. If the proxy is down, DFY never falls back to a direct connection."),
      body: K.row({ label: L("Через прокси", "Use a proxy"), keywords: "proxy прокси socks socks5 http https vpn ip",
          desc: `<span id="st-dfy-proxy-state">${esc(L("Загрузка…", "Loading…"))}</span>`,
          control: K.toggle("st-dfy-proxy-on", false) })
        + `<div class="sx-row sx-row--stack" id="st-dfy-proxy-form" data-sx-k="proxy прокси адрес address port порт логин login пароль password" hidden>
            <form id="st-dfy-proxy-fields" autocomplete="off" novalidate style="width:100%;display:grid;gap:8px">
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                ${K.select("st-dfy-proxy-scheme", [["http", "HTTP"], ["https", "HTTPS"], ["socks5", "SOCKS5"]], "http", { width: 120 })}
                <input class="pinput sx-input" id="st-dfy-proxy-host" placeholder="${esc(L("Адрес: proxy.example.com или socks5://логин:пароль@хост:порт", "Address: proxy.example.com or socks5://user:pass@host:port"))}" spellcheck="false" style="flex:1;min-width:200px;margin:0">
                <input class="pinput sx-input" id="st-dfy-proxy-port" type="number" min="1" max="65535" placeholder="${esc(L("Порт", "Port"))}" style="width:96px;margin:0">
              </div>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <input class="pinput sx-input" id="st-dfy-proxy-user" autocomplete="off" spellcheck="false" placeholder="${esc(L("Логин (если нужен)", "Username (if required)"))}" style="flex:1;min-width:160px;margin:0">
                <input class="pinput sx-input" id="st-dfy-proxy-pass" type="password" autocomplete="new-password" placeholder="${esc(L("Пароль", "Password"))}" style="flex:1;min-width:160px;margin:0">
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <button type="submit" class="pbtn" id="st-dfy-proxy-save">${esc(L("Сохранить", "Save"))}</button>
                <button type="button" class="pbtn ghost" id="st-dfy-proxy-check">${esc(L("Проверить", "Check"))}</button>
                <button type="button" class="pbtn ghost danger" id="st-dfy-proxy-forget" hidden>${esc(L("Забыть логин", "Forget login"))}</button>
                <span class="td" id="st-dfy-proxy-result" role="status" aria-live="polite" style="font-size:12.5px"></span>
              </div>
            </form></div>` });
    const kinds = F() ? F().KINDS : {};
    const kindOptions = Object.entries(kinds).map(([k, v]) => [k, en ? v.l[1] : v.l[0],
      v.group === "personal" ? L("Личные данные", "Personal data") : L("Логины и платежи", "Logins and payments")]);
    const vault = K.section({ id: "dfy-vault", title: L("Личные данные и секреты", "Personal data and secrets"),
      desc: L("Телефон, e-mail, ФИО, адреса, паспорт, реквизиты, логины и карты, которые DFY может вводить на сайтах. ИИ видит только маску (например «+7 •••• ••67» или «Visa •••• 4242»); значение подставляет MemuAI прямо в поле. Логин вводится только на своём сайте.",
        "Phone, email, name, addresses, passport, company details, logins and cards DFY may enter on websites. The AI sees only a mask (e.g. “+1 •••• ••67” or “Visa •••• 4242”); MemuAI types the value into the field itself. A login is entered only on its own site."),
      body: `<div class="sx-row sx-row--stack" data-sx-k="vault хранилище secrets секреты пароли карты телефон phone адрес address паспорт passport"><div id="st-dfy-vault-scheme" class="sx-desc td"></div>
          <div id="st-dfy-vault-list" class="dfy-vault-list" style="width:100%;margin-top:6px">${esc(L("Загрузка…", "Loading…"))}</div></div>
        <div class="sx-row sx-row--stack" data-sx-k="add добавить secret секрет телефон phone"><div class="sx-label tl" id="st-dfy-vault-title">${esc(L("Добавить", "Add"))}</div>
          <form id="st-dfy-vault-form" autocomplete="off" novalidate style="width:100%;display:grid;gap:8px;margin-top:6px">
            <div style="display:flex;gap:8px;flex-wrap:wrap">${K.select("st-dfy-vault-kind", kindOptions, "phone", { width: 260 })}
              <input class="pinput sx-input" id="st-dfy-vault-label" placeholder="${esc(L("Название (необязательно), например «Рабочий»", "Name (optional), e.g. “Work”"))}" style="flex:1;min-width:160px;margin:0">
              <input class="pinput sx-input" id="st-dfy-vault-domain" placeholder="${esc(L("Сайт, например booking.com", "Site, e.g. booking.com"))}" style="flex:1;min-width:160px;margin:0"></div>
            <div id="st-dfy-vault-fields" class="dfy-fields"></div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button type="submit" class="pbtn" id="st-dfy-vault-submit">${esc(L("Сохранить", "Save"))}</button>
              <button type="button" class="pbtn ghost" id="st-dfy-vault-cancel" style="display:none">${esc(L("Отмена", "Cancel"))}</button>
              <span class="td" id="st-dfy-vault-hint" style="font-size:12px"></span></div>
          </form></div>` });

    const machines = window.MemuDFYRemote ? window.MemuDFYRemote.sectionHtml({ ...ctx, L }) : "";
    return `<div id="dfy-settings">${main}${machines}${browser}${proxy}${vault}</div>`;
  }

  async function loadVault(ctx) {
    const { en, esc } = ctx;
    const L = (ru, e) => (en ? e : ru);
    const box = document.getElementById("st-dfy-vault-list"), scheme = document.getElementById("st-dfy-vault-scheme");
    if (!box || !window.MemuDFY) return [];
    try {
      const data = await MemuDFY.vault.list();
      if (scheme) scheme.textContent = (data.scheme === "dpapi" ? "🔒 " : "⚠ ") + ((en ? data.note_en : data.note) || data.note || "");
      const kinds = F() ? F().KINDS : {};
      const kindName = (k) => (kinds[k] ? (en ? kinds[k].l[1] : kinds[k].l[0]) : k);
      const items = (data.items || []).filter((item) => item.kind !== "otp");
      const row = (item) => `<div class="dfy-vault-item" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(127,127,127,.15)">
          <div style="flex:1;min-width:0"><b>${esc(item.label || kindName(item.kind))}</b> <span class="td">${esc(item.mask || "")}</span>
          <div class="td" style="font-size:12px">${esc(kindName(item.kind))}${item.domain ? " · " + esc(item.domain) : ""}${item.persistent === false ? " · " + esc(L("только для текущей задачи", "current task only")) : ""}</div></div>
          ${item.persistent === false ? "" : `<button type="button" class="pbtn ghost" data-dfy-vault-edit="${esc(item.id)}">${esc(L("Изменить", "Edit"))}</button>`}
          <button type="button" class="pbtn ghost danger" data-dfy-vault-del="${esc(item.id)}">${esc(L("Удалить", "Delete"))}</button></div>`;
      const group = (title, rows) => rows.length ? `<div class="td" style="font-size:12px;margin:8px 0 2px;text-transform:uppercase;letter-spacing:.04em">${esc(title)}</div>${rows.map(row).join("")}` : "";
      const personal = items.filter((i) => (kinds[i.kind] || {}).group === "personal");
      const secret = items.filter((i) => (kinds[i.kind] || {}).group !== "personal");
      box.innerHTML = items.length ? group(L("Личные данные", "Personal data"), personal) + group(L("Логины и платежи", "Logins and payments"), secret)
        : `<span class="td">${esc(L("Пока пусто. Добавьте телефон и e-mail ниже — они чаще всего нужны при оформлении. DFY и сам спросит данные, когда они понадобятся, и предложит запомнить.",
          "Empty for now. Add your phone and email below — checkouts ask for them most often. DFY also asks when it needs data and offers to remember it."))}</span>`;
      return items;
    } catch (e) {
      box.textContent = e.message || String(e);
      return [];
    }
  }

  function bind(ctx) {
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    const $ = (id) => document.getElementById(id);
    if (!$("dfy-settings")) return;
    const { en } = ctx;
    const L = (ru, e) => (en ? e : ru);
    const forms = F();
    if (forms) forms.injectStyle();
    const listen = (el, type, fn) => { if (el) el.addEventListener(type, fn, { signal }); };
    const save = async (patch) => {
      try { await ctx.savePreferences(patch); } catch (e) { ctx.toast(e.message || String(e), "err"); }
      window.MemuDFY?.refreshStatus?.();
    };
    listen($("st-dfy-enabled"), "change", (e) => save({ dfy_enabled: !!e.target.checked }));
    listen($("st-dfy-visible"), "change", (e) => save({ dfy_browser_visible: !!e.target.checked }));
    listen($("st-dfy-schedule-browser"), "change", (e) => save({ dfy_schedule_browser: e.target.value === "chrome" ? "chrome" : "isolated" }));

    const view = { ...ctx, kit: window.MemuSettingsKit, modelName: last.modelName || ((id) => id) };
    const setModel = (value) => {
      save({ dfy_model: value });

      const select = $("st-dfy-model"), box = $("st-dfy-model-note");
      if (select) select.innerHTML = modelOptions(view, value);
      if (box) { const note = modelNote(view, value); box.innerHTML = note; box.style.display = note ? "" : "none"; }
    };
    listen($("st-dfy-model"), "change", (e) => setModel(e.target.value));
    listen($("st-dfy-model-note"), "click", (e) => { if (e.target.closest("#st-dfy-model-reset")) setModel(""); });
    listen($("st-dfy-confirm"), "change", (e) => save({ dfy_confirm_mode: e.target.value === "every_step" ? "every_step" : "irreversible" }));
    listen($("st-dfy-steps"), "change", (e) => {
      const n = Math.max(5, Math.min(80, Math.round(+e.target.value || 30)));
      e.target.value = n; save({ dfy_max_steps: n });
    });
    listen($("st-dfy-clear"), "click", async (e) => {
      if (!confirm(L("Выйти со всех сайтов в браузере DFY и удалить его куки?", "Sign out of every site in the DFY browser and delete its cookies?"))) return;
      e.target.disabled = true;
      try { await MemuDFY.clearSessions(); ctx.toast(L("Сессии DFY очищены", "DFY sessions cleared")); }
      catch (err) { ctx.toast(err.message, "err"); }
      finally { e.target.disabled = false; }
    });

    if (window.MemuDFYRemote) window.MemuDFYRemote.bindSection({ ...ctx, L, signal });

    const px = { on: $("st-dfy-proxy-on"), form: $("st-dfy-proxy-form"), fields: $("st-dfy-proxy-fields"), scheme: $("st-dfy-proxy-scheme"),
      host: $("st-dfy-proxy-host"), port: $("st-dfy-proxy-port"), user: $("st-dfy-proxy-user"), pass: $("st-dfy-proxy-pass"),
      state: $("st-dfy-proxy-state"), result: $("st-dfy-proxy-result"), forget: $("st-dfy-proxy-forget") };
    let proxySaved = null;
    const proxyApi = async (method, path, body) => {
      const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined, cache: "no-store" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok && !("ok" in data && method === "POST")) throw new Error(data.error || "HTTP " + r.status);
      return data;
    };
    const paintProxy = (cfg) => {
      if (!px.on || !cfg) return;
      proxySaved = cfg;
      px.on.checked = !!cfg.enabled;
      px.form.hidden = !px.on.checked && !cfg.host;
      if (cfg.host) { px.scheme.value = cfg.scheme; px.host.value = cfg.host; px.port.value = cfg.port || ""; }
      px.user.value = "";
      px.user.placeholder = cfg.auth ? L("Логин сохранён", "Login saved") + (cfg.username ? ` (${cfg.username})` : "") : L("Логин (если нужен)", "Username (if required)");
      px.pass.placeholder = cfg.auth ? L("••• сохранён — пусто, чтобы оставить", "••• saved — leave empty to keep") : L("Пароль", "Password");
      px.forget.hidden = !cfg.auth;
      const err = cfg.status && cfg.status.error;
      px.state.textContent = cfg.enabled ? `${cfg.scheme.toUpperCase()} ${cfg.host}:${cfg.port}${cfg.auth ? L(" · с логином", " · with login") : ""}${err ? " — " + err : ""}`
        : L("Выкл — браузер DFY выходит в сеть напрямую", "Off — the DFY browser connects directly");
    };
    const formBody = () => {
      const body = { scheme: px.scheme.value, host: px.host.value.trim(), port: +px.port.value || 0 };
      if (/:\/\/|@/.test(body.host)) { body.address = body.host; delete body.host; delete body.port; delete body.scheme; }
      if (px.user.value.trim()) body.username = px.user.value.trim();
      if (px.pass.value) body.password = px.pass.value;
      return body;
    };
    const saveProxy = async (patch) => {
      try {
        paintProxy(await proxyApi("PUT", "/api/dfy/proxy", patch));
        px.pass.value = "";
        px.result.textContent = "";
        return true;
      } catch (err) { ctx.toast(err.message, "err"); return false; }
    };
    if (px.on) {
      proxyApi("GET", "/api/dfy/proxy").then(paintProxy).catch((e) => { px.state.textContent = e.message; });
      listen(px.on, "change", async () => {
        px.form.hidden = false;
        if (!px.on.checked) { if (proxySaved && proxySaved.enabled) await saveProxy({ enabled: false }); return; }
        if (proxySaved && proxySaved.host) await saveProxy({ enabled: true });
        else px.host.focus();
      });
      listen(px.fields, "submit", async (e) => {
        e.preventDefault();
        const body = formBody();
        if (!body.host && !body.address) { px.host.focus(); return; }
        if (await saveProxy({ ...body, enabled: true })) ctx.toast(L("Прокси сохранён — DFY использует его со следующего шага", "Proxy saved — DFY uses it from its next step"));
      });
      listen($("st-dfy-proxy-check"), "click", async (e) => {
        const body = formBody();
        const button = e.currentTarget;
        button.disabled = true;
        px.result.textContent = L("Проверяю…", "Checking…");
        try {
          const r = await proxyApi("POST", "/api/dfy/proxy/check", body.host || body.address ? body : {});
          px.result.textContent = r.ok ? L(`Работает: внешний IP ${r.ip} · ${r.ms} мс`, `Works: external IP ${r.ip} · ${r.ms} ms`) : "⚠ " + (r.error || L("Не удалось", "Failed"));
        } catch (err) { px.result.textContent = "⚠ " + err.message; }
        finally { button.disabled = false; }
      });
      listen(px.forget, "click", async () => {
        if (!confirm(L("Удалить сохранённые логин и пароль прокси?", "Delete the saved proxy username and password?"))) return;
        await saveProxy({ clear_auth: true });
      });
    }

    const form = $("st-dfy-vault-form"), fields = $("st-dfy-vault-fields"), kind = $("st-dfy-vault-kind");
    const domain = $("st-dfy-vault-domain"), label = $("st-dfy-vault-label");
    let items = [], editing = null;
    const paintFields = () => {
      if (!fields || !forms) return;
      const k = kind.value, spec = forms.KINDS[k] || forms.KINDS.text;
      fields.innerHTML = spec.fields.map((f) => forms.inputHtml(f, { kind: k, cls: "pinput sx-input",
        placeholder: editing && (editing.fields || []).includes(f) ? L("••• сохранено", "••• saved") : null })).join("");
      if (editing) fields.querySelectorAll("select[data-field]").forEach((s) => { s.insertAdjacentHTML("afterbegin", `<option value="" selected>${L("не менять", "keep")}</option>`); });
      domain.placeholder = spec.domain ? L("Сайт (обязательно), например booking.com", "Site (required), e.g. booking.com")
        : L("Только для сайта (необязательно)", "Only for a site (optional)");
      $("st-dfy-vault-hint").textContent = editing ? L("Пустое поле — оставить сохранённое значение.", "An empty field keeps the saved value.") : k === "phone" ? L("С кодом страны: +7…, +370…, +49…", "With the country code: +1…, +44…, +49…")
        : k === "document" ? L("Серия и номер скрыты в маске; ИИ их не увидит.", "Series and number are hidden in the mask; the AI never sees them.") : "";
    };
    const resetForm = () => {
      editing = null;
      kind.disabled = false;
      label.value = ""; domain.value = "";
      $("st-dfy-vault-title").textContent = L("Добавить", "Add");
      $("st-dfy-vault-cancel").style.display = "none";
      paintFields();
    };
    paintFields();
    listen(kind, "change", paintFields);
    listen(fields, "input", (e) => forms && forms.onInput(e));
    listen($("st-dfy-vault-cancel"), "click", resetForm);
    listen(form, "submit", async (e) => {
      e.preventDefault();
      if (!forms) return;
      const values = forms.collect(fields, kind.value);
      if (!values) { fields.querySelector("[aria-invalid]")?.focus(); return; }
      if (!editing && !Object.keys(values).length) { fields.querySelector("[data-field]")?.focus(); return; }
      fields.querySelectorAll("input[data-field]").forEach((i) => { i.value = ""; });
      try {
        if (editing) await MemuDFY.vault.update(editing.id, { fields: values, label: label.value.trim(), domain: domain.value.trim() });
        else await MemuDFY.vault.add({ kind: kind.value, label: label.value.trim(), domain: domain.value.trim(), fields: values });
        ctx.toast(editing ? L("Изменения сохранены", "Changes saved") : L("Сохранено в хранилище", "Saved to the vault"));
        resetForm();
        items = await loadVault(ctx);
      } catch (err) { ctx.toast(err.message, "err"); }
    });
    listen($("st-dfy-vault-list"), "click", async (e) => {
      const edit = e.target.closest("[data-dfy-vault-edit]");
      if (edit) {
        editing = items.find((i) => i.id === edit.dataset.dfyVaultEdit) || null;
        if (!editing) return;
        kind.value = editing.kind; kind.disabled = true;
        label.value = editing.label || ""; domain.value = editing.domain || "";
        $("st-dfy-vault-title").textContent = L("Изменить: ", "Edit: ") + (editing.label || editing.mask || "");
        $("st-dfy-vault-cancel").style.display = "";
        paintFields();
        form.scrollIntoView({ block: "nearest", behavior: "smooth" });
        fields.querySelector("[data-field]")?.focus({ preventScroll: true });
        return;
      }
      const b = e.target.closest("[data-dfy-vault-del]");
      if (!b) return;
      if (!confirm(L("Удалить эту запись из хранилища?", "Delete this entry from the vault?"))) return;
      try {
        await MemuDFY.vault.remove(b.dataset.dfyVaultDel);
        if (editing && editing.id === b.dataset.dfyVaultDel) resetForm();
        items = await loadVault(ctx);
      } catch (err) { ctx.toast(err.message, "err"); }
    });
    loadVault(ctx).then((rows) => { items = rows; });
  }

  const icon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5 9 17l11-11"/><path d="M14 4h6v6"/></svg>`;
  window.MemuDFYSettings = { html, bind, icon };
})();
