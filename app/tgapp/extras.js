

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, $$, esc, tr, icon, haptic, toast, api, openSheet, openStack, actionSheet, promptSheet, confirmDialog, ago } = kit;

    kit.hooks.settingsRows.push((SET) => {
      const voice = (S.features && S.features.voice) || {};
      return `<div class="section-title">${esc(tr("Инструменты", "Tools"))}</div>
        <div class="group">${SET.nav("schedules", "clock", "#f59e0b", tr("Планирование", "Planning"))}
          ${SET.nav("prompts", "text", "#0ea5e9", tr("Библиотека промптов", "Prompt library"))}
          ${SET.nav("voice", "mic", "#ec4899", tr("Голосовой ввод", "Voice input"), voice.available ? tr("Готов", "Ready") : tr("Не настроен", "Not set up"))}</div>
        <div class="section-title">${esc(tr("Устройства", "Devices"))}</div>
        <div class="group">${S.web ? SET.nav("websession", "monitor", "#64748b", tr("Этот браузер", "This browser"), tr("Вход выполнен", "Signed in"))
          : SET.nav("devices", "monitor", "#6366f1", tr("Войти на другом устройстве", "Sign in on another device"), S.features && S.features.web_login === false ? tr("Выключено", "Off") : "")}</div>`;
    });

    kit.pages.prompts = () => openStack({ title: tr("Библиотека промптов", "Prompt library"), build: (view) => {
      view.style.overflowY = "auto";
      view.innerHTML = `<div class="empty"><div class="typing"><i></i><i></i><i></i></div></div>`;
      api("prompts.list").then((data) => {
        const rows = data.prompts || [];
        let query = "";
        const draw = () => {
          const shown = rows.filter((p) => !query || (p.name + " " + p.description + " " + p.text).toLowerCase().includes(query));
          $("#pl-list", view).innerHTML = shown.map((p, i) => `<button class="cell flat" data-prompt="${rows.indexOf(p)}"><div class="cell-icon" style="background:#0ea5e9">${icon("text")}</div><div class="cell-body"><div class="cell-title">${esc(p.name)}${p.default ? `<span class="tag">${esc(tr("основной", "default"))}</span>` : ""}</div><div class="cell-sub">${esc(p.description || p.text.replace(/<\/?[a-z_][\w-]*[^>]*>/gi, " ").replace(/\s+/g, " ").trim().slice(0, 140))}</div></div><span class="chev">${icon("chev")}</span></button>`).join("") ||
            `<div class="cell flat"><div class="cell-sub">${esc(tr("Ничего не найдено", "Nothing found"))}</div></div>`;
          $$("[data-prompt]", view).forEach((b) => b.onclick = () => promptActions(rows[+b.dataset.prompt]));
        };
        view.innerHTML = `<div style="padding:8px 0 40px"><div class="search">${icon("search")}<input id="pl-q" type="search" placeholder="${esc(tr("Найти промпт", "Find a prompt"))}"></div><div class="group flat" id="pl-list"></div>
          <p class="section-note">${esc(tr("Системные промпты MemuAI. «Основной» уже действует во всех чатах; выбирается и редактируется в MemuAI на компьютере (Настройки → Промпты). Текст любого промпта можно скопировать или вставить в новый чат.", "MemuAI system prompts. The “default” one already applies to every chat; it is chosen and edited in MemuAI on the computer (Settings → Prompts). Any prompt's text can be copied or put into a new chat."))}</p></div>`;
        $("#pl-q", view).oninput = (event) => { query = event.target.value.toLowerCase(); draw(); };
        draw();
      }).catch((error) => { view.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`; });
    } });
    function promptActions(p) {
      openSheet({ title: p.name, full: p.text.length > 600,
        body: `<div class="group"><div class="cell" style="display:block;white-space:pre-wrap;font-size:14px;line-height:1.45;max-height:50vh;overflow:auto">${esc(p.text)}</div></div>`,
        actions: [{ label: tr("Копировать", "Copy"), kind: p.default ? "" : "secondary", run: () => { kit.copyText(p.text); toast(tr("Скопировано", "Copied"), "ok"); } },

                  ...(p.default ? [] : [{ label: tr("Вставить в новый чат", "Put into a new chat"), run: () => {
                    const C = kit.openChat(null);
                    setTimeout(() => { const input = C && C.entry && $("#msg-input", C.entry.view); if (input) { input.value = p.text; input.dispatchEvent(new Event("input")); input.focus(); } }, 120);
                  } }])] });
    }

    kit.pages.voice = () => openStack({ title: tr("Голосовой ввод", "Voice input"), build: (view) => {
      view.style.overflowY = "auto";
      const draw = (info) => {
        S.features.voice = info;
        const engine = { local: tr("Whisper в MemuAI Proxy", "Whisper in MemuAI Proxy"), app: tr("Whisper в окне MemuAI", "Whisper in the MemuAI window"), api: tr("API распознавания речи", "Speech recognition API") }[info.engine] || "—";
        view.innerHTML = `<div style="padding:8px 0 40px"><div class="group flat"><div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Состояние", "Status"))}</div>${info.available ? "" : `<div class="cell-sub" style="-webkit-line-clamp:unset">${esc(info.message || "")}</div>`}</div><div class="cell-value" style="color:${info.available ? "var(--ok)" : "var(--danger)"}">${esc(info.available ? tr("Готов", "Ready") : tr("Недоступен", "Unavailable"))}</div></div>
            ${info.available ? `<div class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Распознавание", "Recognition"))}</div><div class="cell-sub">${esc(engine)}${info.model ? " · " + esc(info.model) : ""}</div></div></div>` : ""}
            <label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Показывать расшифровку перед отправкой", "Show the transcript before sending"))}</div><div class="cell-sub">${esc(tr("Текст попадёт в поле ввода; иначе он сразу уйдёт модели. Действует и для голосовых в боте.", "The text goes into the input; otherwise it is sent at once. Applies to voice messages in the bot too."))}</div></div><span class="switch"><input type="checkbox" id="vo-review" ${S.settings.telegram_voice_review ? "checked" : ""}><span></span></span></label></div>
          <p class="section-note">${esc(tr("Микрофон — в поле ввода чата и MemuCode: нажмите, чтобы записать; удерживайте — чтобы загрузить аудиофайл (до 15 минут, до 20 МБ). Речь распознаётся на вашем компьютере локальной моделью Whisper; модели скачиваются в MemuAI → Настройки → Голос.", "The microphone is in the chat and MemuCode input: tap to record; hold to upload an audio file (up to 15 minutes, 20 MB). Speech is recognised on your computer by a local Whisper model; models are downloaded in MemuAI → Settings → Voice."))}</p>
          <div style="padding:4px 16px 0"><button class="btn secondary small" id="vo-check">${icon("retry")}${esc(tr("Проверить снова", "Check again"))}</button></div></div>`;
        $("#vo-review", view).onchange = async (event) => {
          haptic("select");
          try { const data = await api("settings.save", { values: { telegram_voice_review: event.target.checked } }); Object.assign(S.settings, data.settings || {}); S.features.voice.review = event.target.checked; }
          catch (error) { event.target.checked = !event.target.checked; toast(error.message, "err"); }
        };
        $("#vo-check", view).onclick = () => { view.innerHTML = `<div class="empty"><div class="typing"><i></i><i></i><i></i></div></div>`; api("voice.status").then(draw).catch((error) => toast(error.message, "err")); };
      };
      draw((S.features && S.features.voice) || { available: false });
      api("voice.status").then(draw).catch(() => {});
    } });

    function agentLabel(agent) {
      const ua = String(agent || "");
      const browser = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : /YaBrowser/.test(ua) ? "Yandex Browser" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : tr("Браузер", "Browser");
      const os = /Windows/.test(ua) ? "Windows" : /Mac OS X|Macintosh/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /iPhone|iPad|iOS/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
      return browser + (os ? " · " + os : "");
    }
    async function approveCode(code) {
      let info;
      try { info = await api("weblogin.inspect", { code }); } catch (error) { toast(error.message, "err"); haptic("error"); return; }
      const minutes = Math.max(0, Math.round((info.expires - Date.now() / 1000) / 60));
      openSheet({ title: tr("Разрешить вход?", "Allow the sign-in?"),
        body: `<div class="group flat"><div class="cell flat"><div class="cell-icon" style="background:#6366f1">${icon("monitor")}</div><div class="cell-body"><div class="cell-title">${esc(agentLabel(info.agent))}</div><div class="cell-sub">${esc(tr("Адрес", "Address"))}: ${esc(info.ip || "—")} · ${esc(ago(info.created * 1000))}</div></div></div></div>
          <p class="section-note" style="color:var(--warn)">${esc(tr("Разрешайте, только если вы сами сейчас входите в MemuAI на этом устройстве. Этот браузер получит доступ к вашим чатам, MemuCode и настройкам на 12 часов.", "Allow only if you are signing in to MemuAI on that device yourself right now. That browser gets access to your chats, MemuCode and settings for 12 hours."))}</p>
          <p class="section-note">${esc(tr("Код действует ещё", "The code is valid for"))} ${minutes} ${esc(tr("мин", "min"))}. ${esc(tr("Отозвать все сессии: на компьютере → Telegram → Mini App → «Сбросить сессии».", "Revoke every session on the computer → Telegram → Mini App → “Reset sessions”."))}</p>`,
        actions: [{ label: tr("Отклонить", "Decline"), kind: "secondary", run: () => api("weblogin.approve", { code, approve: false }).then(() => toast(tr("Вход отклонён", "Sign-in declined"))).catch((e) => toast(e.message, "err")) },
                  { label: tr("Разрешить", "Allow"), run: async () => {
                    try { await api("weblogin.approve", { code, approve: true }); haptic("success"); toast(tr("Вход разрешён — браузер откроется через пару секунд", "Allowed — the browser opens in a couple of seconds"), "ok"); }
                    catch (error) { toast(error.message, "err"); }
                  } }] });
    }
    function scanQr() {
      const app = S.tg;
      if (!(app && app.showScanQrPopup && app.isVersionAtLeast && app.isVersionAtLeast("6.4") && app.initData)) return false;
      try {
        app.showScanQrPopup({ text: tr("Наведите камеру на QR-код на экране компьютера", "Point the camera at the QR code on the computer screen") }, (text) => {
          if (!/^memuai-login:/i.test(String(text || ""))) return false;
          setTimeout(() => approveCode(String(text)), 50);
          return true;
        });
        return true;
      } catch (e) { return false; }
    }
    kit.pages.devices = () => {
      if (S.features && S.features.web_login === false) {
        openSheet({ title: tr("Вход из браузера выключен", "Browser sign-in is off"), body: `<p class="section-note" style="margin:0 20px 10px;font-size:15px;color:var(--text)">${esc(tr("Включите его на компьютере: MemuAI → Настройки → Telegram → Mini App → «Вход из браузера».", "Turn it on on the computer: MemuAI → Settings → Telegram → Mini App → “Browser sign-in”."))}</p>` });
        return;
      }
      openStack({ title: tr("Другое устройство", "Another device"), build: (view) => {
        view.style.overflowY = "auto";

        const scan = !!(S.tg && S.tg.showScanQrPopup && S.tg.isVersionAtLeast && S.tg.isVersionAtLeast("6.4") && S.tg.initData)
          && !(window.MemuDevice && window.MemuDevice.isDesktop());
        view.innerHTML = `<div style="padding:8px 0 40px"><div class="convo-hero" style="padding:24px 24px 10px"><div class="avatar" style="--av1:#6366f1;--av2:#a855f7">${icon("monitor")}</div>
            <h2>${esc(tr("MemuAI в браузере", "MemuAI in a browser"))}</h2><p>${esc(tr("На компьютере, планшете или чужом ПК откройте адрес Mini App в браузере — появится QR-код. Подтвердите вход отсюда.", "On a computer, a tablet or someone else's PC open the Mini App address in a browser — a QR code appears. Confirm the sign-in from here."))}</p></div>
          <div style="display:flex;flex-direction:column;gap:10px;padding:0 16px">
            ${scan ? `<button class="btn" id="dv-scan">${icon("target")}${esc(tr("Сканировать QR-код", "Scan the QR code"))}</button>` : ""}
            <button class="btn ${scan ? "secondary" : ""}" id="dv-code">${icon("edit")}${esc(tr("Ввести код", "Enter the code"))}</button>
            <button class="btn secondary" id="dv-link">${icon("copy")}${esc(tr("Скопировать адрес Mini App", "Copy the Mini App address"))}</button></div>
          <p class="section-note">${esc(tr("Ещё способ: отправьте боту /web — он пришлёт одноразовую ссылку для входа (5 минут). Все браузерные сессии отзываются кнопкой «Сбросить сессии» на компьютере.", "Another way: send /web to the bot — it replies with a one-time sign-in link (5 minutes). Every browser session is revoked with “Reset sessions” on the computer."))}</p></div>`;
        const scanButton = $("#dv-scan", view); if (scanButton) scanButton.onclick = () => { if (!scanQr()) toast(tr("Сканер недоступен — введите код", "The scanner is unavailable — enter the code"), "err"); };
        $("#dv-code", view).onclick = async () => {
          const code = await promptSheet(tr("Код с экрана браузера", "The code from the browser"), { placeholder: "ABCD-EFGH", max: 40, ok: tr("Дальше", "Next") });
          if (code) approveCode(code);
        };
        $("#dv-link", view).onclick = () => { const url = location.origin + location.pathname; kit.copyText(url); toast(tr("Адрес скопирован", "Address copied"), "ok"); };
      } });
    };
    kit.pages.websession = () => {
      const kept = window.MemuTgWeb && window.MemuTgWeb.keptOnDevice();
      actionSheet(tr("Этот браузер", "This browser"), [
        { label: tr("Выйти", "Sign out"), sub: kept ? tr("Сессия сохранена на этом устройстве", "The session is kept on this device") : tr("Сессия живёт только в этой вкладке", "The session lives in this tab only"), icon: "close", danger: true,
          run: async () => { if (await confirmDialog(tr("Чтобы войти снова, понадобится подтверждение из Telegram.", "Signing in again needs a confirmation from Telegram."), { title: tr("Выйти из MemuAI?", "Sign out of MemuAI?"), ok: tr("Выйти", "Sign out"), danger: true })) window.MemuTgWeb.signOut(); } },
        { label: tr("Сочетания клавиш", "Keyboard shortcuts"), icon: "list", run: () => kit.showShortcuts && kit.showShortcuts() },
      ]);
    };
  });
})();
