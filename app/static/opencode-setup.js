/* OpenCode connection guide. No downloads/processes without the Install action. */
(function () {
  "use strict";
  function mount(prefix, { api, lang, accountId = "" }) {
    const mode = document.getElementById(prefix + "mode");
    if (!mode) return;
    const L = (ru, en) => lang === "en" ? en : ru;
    const host = document.createElement("section"); host.className = "oc-setup";
    host.setAttribute("aria-label", L("Подключение OpenCode", "OpenCode setup"));
    mode.closest(".field").after(host);
    let timer = 0, epoch = 0;
    const key = document.getElementById(prefix + "api_key");
    const family = () => mode.value === "gateway" ? "gateway" : mode.value === "free" || !mode.value ? "free" : "paid";
    let previous = family(); const keys = { [previous]: key?.value || "" };
    const add = (tag, value, parent = host) => { const el = document.createElement(tag); el.textContent = value; parent.append(el); return el; };
    const link = (label, url) => { const el = add("a", label); el.href = url; el.target = "_blank"; el.rel = "noopener noreferrer"; return el; };
    function render() {
      clearTimeout(timer); const current = ++epoch; host.replaceChildren();
      const selected = mode.value || "free";
      if (key && family() !== previous) {
        keys[previous] = key.value; previous = family(); key.value = keys[previous] || "";
      }
      if (selected === "free") {
        let busy = false;
        add("h4", L("1. Установите шлюз · 2. Добавьте аккаунт", "1. Install gateway · 2. Add account"));
        add("p", L("Бесплатный доступ через opencode2api. Сайт, регистрация, API-ключ, Node.js и Docker не нужны. Proxy запускает шлюз в фоне только на этом компьютере и закрывает вместе с собой.", "Free access through opencode2api. No website, registration, API key, Node.js or Docker needed. Proxy runs it in the background on this computer only and stops it on exit."));
        add("p", L("Кнопка скачает отдельную программу jasonxu114514/opencode2api (~4 МБ, GPL-3.0). Лимиты и доступность определяет сервис: обещания безлимита нет.", "The button downloads the separate jasonxu114514/opencode2api program (~4 MB, GPL-3.0). Limits and availability are set by the service; access is not guaranteed unlimited."));
        add("p", L("Free — анонимное публичное подключение, без входа в ваш аккаунт OpenCode. Ошибка 429 не доказывает, что вы потратили личную квоту: возможны ограничения клиента или сети. Вторая запись Free и переустановка не создают новую квоту. Список моделей не подтверждает работу генерации.", "Free is anonymous public access, not your signed-in OpenCode account. A 429 does not prove personal quota exhaustion: client or network restrictions are possible. Another Free entry or reinstall does not create a new quota. Listing models does not verify generation."));
        link(L("Исходники и лицензия", "Source and license"), "https://github.com/jasonxu114514/opencode2api");
        const status = add("p", L("Проверяю установку…", "Checking installation…")); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
        const progress = document.createElement("progress"); progress.max = 100; progress.hidden = true; host.append(progress);
        const row = document.createElement("div"); row.className = "oc-actions"; host.append(row);
        const install = add("button", L("Установить шлюз", "Install gateway"), row); install.type = "button"; install.className = "btn primary small"; install.disabled = true;
        const cancel = add("button", L("Отменить загрузку", "Cancel download"), row); cancel.type = "button"; cancel.className = "btn small"; cancel.hidden = true;
        const probeRow = document.createElement("div"); probeRow.className = "oc-actions"; host.append(probeRow);
        const probe = add("button", L("Проверить генерацию Free", "Test Free generation"), probeRow); probe.type = "button"; probe.className = "btn small"; probe.disabled = true;
        add("p", accountId
          ? L("Проверка отправит один короткий запрос только к подтверждённой бесплатной модели. Повтор в течение минуты покажет тот же результат без нового запроса. Используются сохранённые настройки подключения.", "The test sends one tiny request to a verified zero-price model only. Repeating within a minute returns the same result without another request. Saved connection settings are used.")
          : L("Для проверки генерации сначала добавьте подключение, затем откройте его настройки. Проверка списка моделей сама не расходует генерации MemuAI.", "To test generation, save the connection first, then open its settings. MemuAI's catalog check itself does not send a generation request."));
        const diagnostic = add("p", ""); diagnostic.setAttribute("role", "status"); diagnostic.setAttribute("aria-live", "polite");
        probe.onclick = async () => {
          probe.disabled = true; diagnostic.textContent = L("Один тестовый запрос…", "Sending one test request…");
          try {
            const data = await api(`/api/admin/accounts/${encodeURIComponent(accountId)}/opencode-probe`, { method: "POST", body: { confirmed: true } });
            if (!host.isConnected || current !== epoch) return;
            const message = (lang === "en" ? data.message_en : data.message) || data.message || L("Проверка ещё выполняется.", "Test is still running.");
            diagnostic.textContent = (data.http_status ? `HTTP ${data.http_status} · ` : "") + message
              + (data.cached ? L(` · Сохранённый результат; следующая попытка через ${data.retry_after} с.`, ` · Cached result; next attempt in ${data.retry_after}s.`) : "");
          } catch (error) {
            if (host.isConnected && current === epoch) diagnostic.textContent = error.message;
          } finally {
            if (host.isConnected && current === epoch) probe.disabled = false;
          }
        };
        async function refresh(action) {
          if (busy || !host.isConnected || current !== epoch) return;
          busy = true; install.disabled = true; cancel.disabled = true;
          try {
            const data = await api("/api/admin/opencode/runtime", action ? { method: "POST", body: { action, confirmed: action === "install" } } : undefined);
            if (!host.isConnected || current !== epoch) return;
            cancel.hidden = !data.installing; cancel.disabled = false;
            install.disabled = data.installing || data.installed || !data.supported;
            probe.disabled = !accountId || !data.installed || data.installing;
            progress.hidden = !data.installing;
            if (Number.isFinite(data.progress)) progress.value = data.progress; else progress.removeAttribute("value");
            const mb = Math.max(0, Number(data.bytes_downloaded) || 0) / 1048576;
            status.textContent = data.installing ? L(`Загрузка/установка: ${mb.toFixed(1)} МБ${Number.isFinite(data.progress) ? ` · ${data.progress}%` : ""}`, `Downloading/installing: ${mb.toFixed(1)} MB${Number.isFinite(data.progress) ? ` · ${data.progress}%` : ""}`)
              : data.installed ? L(`Готово · ${data.version}. Теперь нажмите «Добавить аккаунт» или «Сохранить», затем «Проверить» на карточке.`, `Ready · ${data.version}. Now press Add account or Save, then Check on the account card.`)
              : data.error ? L("Не удалось установить: ", "Installation failed: ") + data.error
              : !data.supported ? L("Автоустановка доступна для Windows x64/ARM64. На другой системе подключите свой шлюз.", "Automatic setup supports Windows x64/ARM64. Connect your own gateway on other systems.")
              : data.stage === "cancelled" ? L("Загрузка отменена. Можно начать снова.", "Download cancelled. You can retry.")
              : L("Шлюз ещё не установлен. Нажмите кнопку ниже; ключ вводить не нужно.", "Gateway is not installed. Press the button below; no key is needed.");
            if (data.installing) timer = setTimeout(() => refresh(), 750);
          } catch (error) {
            if (host.isConnected && current === epoch) { status.textContent = error.message; install.disabled = false; cancel.disabled = false; }
          } finally { busy = false; }
        }
        install.onclick = () => refresh("install"); cancel.onclick = () => refresh("cancel"); refresh();
      } else if (selected === "gateway") {
        add("h4", L("Подключение уже запущенного сервера", "Connect an existing server"));
        add("p", L("jason: http://127.0.0.1:8080/v1 · ключ из server_keys. Порт 8081 — WebUI, он сюда не подходит.", "jason: http://127.0.0.1:8080/v1 · key from server_keys. Port 8081 is the WebUI and is not an API URL."));
        add("p", L("Tiara: http://127.0.0.1:10000/v1 · ключ API_KEY. Этот вариант требует отдельного OpenCode runtime и Node.js либо Docker.", "Tiara: http://127.0.0.1:10000/v1 · key from API_KEY. This option needs a separate OpenCode runtime and Node.js or Docker."));
        add("p", L("MemuAI не запускает ваш внешний сервер. Для простого старта выберите «Бесплатные модели» выше.", "MemuAI does not start your external server. For a simple setup, select Free models above."));
        link(L("Инструкция jason (отдельный exe)", "jason guide (standalone executable)"), "https://github.com/jasonxu114514/opencode2api#quick-start");
        link(L("Инструкция Tiara (Node.js/Docker)", "Tiara guide (Node.js/Docker)"), "https://github.com/TiaraBasori/OpenCode2API/blob/main/README.en.md");
      } else {
        add("h4", selected === "go" ? L("Ваша подписка OpenCode Go", "Your OpenCode Go subscription") : L("Ваш платный доступ OpenCode Zen", "Your paid OpenCode Zen access"));
        add("ol", "").append(...[
          L("Откройте ссылку входа ниже и войдите в свой аккаунт.", "Open the sign-in link below and sign in to your account."),
          L("Выберите рабочее пространство и создайте/скопируйте API-ключ.", "Select your workspace and create/copy an API key."),
          selected === "go" ? L("Убедитесь, что подписка Go активна в этом рабочем пространстве.", "Make sure Go is active in this workspace.") : L("Для платных моделей нужен пополненный баланс Zen.", "Paid models require a funded Zen balance."),
          L("Вставьте ключ в поле ниже, добавьте аккаунт и нажмите «Проверить».", "Paste the key below, add the account and press Check.")
        ].map(text => { const li = document.createElement("li"); li.textContent = text; return li; }));
        link(L("Войти в OpenCode", "Sign in to OpenCode"), "https://opencode.ai/auth");
        link(selected === "go" ? "OpenCode Go" : "OpenCode Zen", selected === "go" ? "https://opencode.ai/go" : "https://opencode.ai/zen");
        add("p", L("Для второго настоящего аккаунта нажмите «Добавить аккаунт → OpenCode», выберите Zen/Go и вставьте API-ключ нужного рабочего пространства. Чтобы получить ключ другого аккаунта, смените пользователя на сайте OpenCode. Ключи одного пространства могут иметь общие лимиты. Free не связан с этими входами; создавать вторую запись Free для сброса лимита бессмысленно.", "For another actual account, use Add account → OpenCode, choose Zen/Go and paste its workspace API key. Switch the signed-in user on the OpenCode website to obtain another account's key. Keys from one workspace may share limits. Anonymous Free is unrelated to these logins; duplicating a Free entry does not reset its limits."));
      }
    }
    mode.addEventListener("change", render); render();
  }
  window.MemuOpenCodeSetup = { mount };
})();
