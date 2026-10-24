/* MemuAI — settings of the Quick Ask «Слово-активатор» plugin (Round 27, r27-wakeword).

   Rendered on its own sub-page of Settings → Quick Ask → «Возможности» through
   MemuQuick.settingsSection("wake", …). Built from the settings kit (window.MemuSettingsKit).
   Talks only to /api/wake/* (app/wake_word_api.py). Settings live in memuai/wake_word.json, not preferences.json:
   the main window saves whole preference snapshots and would overwrite them. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || typeof Q.settingsSection !== "function") return;
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/><path d="M3 5.5c.6-1 1.4-1.9 2.3-2.5M21 5.5c-.6-1-1.4-1.9-2.3-2.5"/></svg>';

  async function call(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.error || "HTTP " + res.status); err.en = data.error_en; throw err; }
    return data;
  }

  function mount(el, ctx) {
    const en = !!(ctx && ctx.en);
    const t = (ru, enText) => (en ? enText : ru);
    const L = (pair) => (pair ? (en ? pair.en || pair.ru : pair.ru || pair.en) : "");
    const K = (ctx && ctx.kit) || window.MemuSettingsKit;
    const mb = (bytes) => Math.round((bytes || 0) / 1048576) + t(" МБ", " MB");
    const toast = (text, kind) => (ctx && typeof ctx.toast === "function" ? ctx.toast(text, kind) : null);
    const S = { data: null, phraseCheck: null, checkTimer: 0, poll: 0, testPoll: 0, testRenew: 0, testing: false, lastHitAt: 0 };

    if (!K) { el.textContent = t("Набор настроек не загружен.", "The settings kit is not loaded."); return; }

    /* ---------------------------------------------------------- status */
    function statusInfo(st) {
      const s = (S.data && S.data.settings) || {};
      if (!st) return { tone: "muted", text: t("Загрузка…", "Loading…") };
      if (st.state === "listening") return st.reason === "command"
        ? { tone: "ok", text: t("Записываю команду", "Recording a command") }
        : st.reason === "test" ? { tone: "info", text: t("Проверка: слушаю", "Test: listening") }
        : { tone: "ok", text: t(`Слушаю «${st.phrase}»`, `Listening for “${st.phrase}”`) };
      if (st.state === "paused") {
        const why = { locked: t("компьютер заблокирован — микрофон отпущен", "the PC is locked — the mic is released"),
          tts: t("MemuAI говорит", "MemuAI is speaking"), live: t("идёт MemuAI Live", "MemuAI Live is on"),
          voice: t("идёт голосовой ввод", "voice input is on"), cooldown: t("пауза после срабатывания", "pause after a trigger") }[st.reason];
        return { tone: "warn", text: t("Пауза", "Paused") + (why ? " · " + why : "") };
      }
      if (st.state === "loading") return st.reason === "downloading"
        ? { tone: "info", text: t(`Модель загружается… ${st.progress || 0}%`, `Downloading the model… ${st.progress || 0}%`) }
        : { tone: "info", text: t("Загружаю модель…", "Loading the model…") };
      if (st.state === "error") {
        const text = st.error ? L(st.error)
          : st.reason === "no_model" ? t("Модель распознавания не скачана", "The speech model is not downloaded")
          : st.reason === "no_engine" ? t("Компонент распознавания не установлен", "The speech component is not installed")
          : t("Ошибка", "Error");
        return { tone: "err", text };
      }
      return { tone: "muted", text: s.enabled ? t("Запускается…", "Starting…") : t("Выключено", "Off") };
    }

    /* ---------------------------------------------------------- render */
    function modelBlock(d) {
      const s = d.settings;
      const m = d.models.find((row) => row.lang === s.lang) || d.models[0];
      const job = m.download || null;
      const busy = job && (job.state === "downloading" || job.state === "unpacking");
      let right;
      if (m.ready) right = `<span class="wks-model-ok">${t("Скачана", "Downloaded")}</span><button type="button" class="pbtn ghost sx-btn-sm" data-wks="model-delete">${t("Удалить", "Delete")}</button>`;
      else if (busy) right = `<span class="wks-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${job.progress}"><i style="width:${job.progress}%"></i></span><span class="wks-pct">${job.state === "unpacking" ? t("распаковка", "unpacking") : job.progress + "%"}</span><button type="button" class="pbtn ghost sx-btn-sm" data-wks="model-cancel">${t("Отмена", "Cancel")}</button>`;
      else right = `<button type="button" class="pbtn sx-btn-sm" data-wks="model-download">${job && job.state === "error" ? t("Повторить", "Retry") : t("Скачать", "Download")} · ${mb(m.size)}</button>`;
      const err = job && job.state === "error" && job.error ? K.note(esc(L(job.error)), "danger") : "";
      return K.row({ label: t("Модель распознавания", "Speech model"), id: "wks-model-row", keywords: "vosk model модель скачать download",
        desc: `Vosk · ${esc(m.id)} · ${mb(m.size)}. ${t("Скачивается один раз и работает без интернета.", "Downloaded once, works offline.")}`,
        control: `<span class="wks-inline">${right}</span>` }) + err;
    }

    function engineBlock(d) {
      if (d.engine.available) return "";
      const inst = d.engine.install || {};
      if (!d.engine.installable) return K.note(t("Компонент распознавания речи (Vosk) не найден. Переустановите MemuAI — он входит в установщик.",
        "The speech component (Vosk) is missing. Reinstall MemuAI — it ships with the installer."), "warn");
      const running = inst.state === "running";
      return K.note(`${t("Для слова-активатора нужен пакет <b>vosk</b> (≈ 7 МБ, работает офлайн).", "The wake word needs the <b>vosk</b> package (≈ 7 MB, works offline).")}
        ${inst.state === "error" ? `<br><span class="wks-err">${esc(inst.error)}</span>` : ""}
        <div class="wks-note-actions"><button type="button" class="pbtn sx-btn-sm" data-wks="engine-install" ${running ? "disabled" : ""}>${running ? t("Устанавливаю…", "Installing…") : t("Установить", "Install")}</button></div>`, "warn");
    }

    function phraseHelp() {
      const c = S.phraseCheck;
      if (!c) return t("2–4 слова. Работает с любой фразой — обучать ничего не нужно.", "2–4 words. Any phrase works — nothing to train.");
      if (c.problem) return `<span class="wks-err">${esc(L(c.problem))}</span>`;
      if (!c.checked) return t("Слова проверятся после загрузки модели.", "Words are checked once the model is downloaded.");
      const chips = c.words.map((w) => w.known
        ? `<span class="wks-word" data-tone="ok">${esc(w.spelling || w.word)}</span>`
        : `<span class="wks-word" data-tone="warn" title="${esc(t("нет в словаре модели", "not in the model's dictionary"))}">${esc(w.word)} → ${esc(w.alias[0] || "?")}</span>`).join(" ");
      const odd = c.words.filter((w) => !w.known);
      const list = odd.map((w) => (en ? "“" + esc(w.word) + "”" : "«" + esc(w.word) + "»")).join(", ");
      const warn = !odd.length ? "" : `<div class="wks-warn">${odd.length === 1
        ? t(`Слова ${list} нет в словаре модели — ловлю его по созвучию.`, `The word ${list} is not in the model's dictionary — it is caught by sound.`)
        : t(`Слов ${list} нет в словаре модели — ловлю их по созвучию.`, `The words ${list} are not in the model's dictionary — they are caught by sound.`)}
        ${t("Если срабатывает плохо, выберите обычные слова.", "If it triggers poorly, pick ordinary words.")}</div>`;
      return `<div class="wks-words">${chips}</div>${warn}`;
    }

    function html(d) {
      const s = d.settings, st = d.status;
      const info = statusInfo(st);
      const defaults = d.defaults || {};
      const phrase = s.phrase || defaults[s.lang] || "";
      const devices = (d.mic && d.mic.devices) || [];
      const devOptions = [["", t("По умолчанию (как в Windows)", "Default (as in Windows)")], ...devices.map((name) => [name, name])];
      if (s.device && !devices.includes(s.device)) devOptions.push([s.device, s.device + t(" — не подключён", " — not connected")]);
      const quickOff = st && st.quick_enabled === false;
      const hero = `<div class="wks-hero" data-tone="${info.tone}">
          <span class="wks-hero-ic" aria-hidden="true">${ICON}</span>
          <div class="wks-hero-text"><b id="wks-status">${esc(info.text)}</b>
            <small>${t("Звук обрабатывается только на этом компьютере и никуда не отправляется. Пока слово-активатор слушает, на значке MemuAI в трее горит красная точка.",
              "Audio is processed on this PC only and never sent anywhere. While the wake word listens, the MemuAI tray icon shows a red dot.")}</small></div>
        </div>`;
      const main = K.section({ id: "wks-main", body:
          K.row({ label: t("Слово-активатор", "Wake word"), keywords: "wake word слово активатор привет мему hey memu hotword",
            desc: t("Скажите фразу — откроется быстрый доступ и сразу начнёт слушать команду. Можно и на одном дыхании: «Привет, Мему, какая погода?»",
              "Say the phrase — Quick Ask opens already listening for a command. One breath works too: “Hey Memu, what's the weather?”"),
            control: K.toggle("wks-enabled", s.enabled) })
        + (quickOff ? K.note(t("Быстрый доступ выключен — фраза ничего не откроет. Включите его в разделе «Основное».", "Quick Ask is off — the phrase will open nothing. Turn it on under Basics."), "warn") : "")
        + engineBlock(d)
        + K.row({ label: t("Язык", "Language"), keywords: "language язык модели", desc: t("Язык фразы и офлайн-распознавания.", "The language of the phrase and of offline recognition."),
            control: K.segmented("wks-lang", d.models.map((m) => [m.lang, L(m.title)]), s.lang) })
        + K.field({ label: t("Фраза", "Phrase"), forId: "wks-phrase", keywords: "phrase фраза", control:
            `<span class="wks-inline wks-phrase-line">${K.input("wks-phrase", phrase, { placeholder: defaults[s.lang] || "", attrs: 'maxlength="60" spellcheck="false" autocomplete="off"' })}
             <button type="button" class="pbtn ghost sx-btn-sm" data-wks="phrase-default" ${s.phrase ? "" : "hidden"}>${t("По умолчанию", "Default")}</button></span>`,
            help: `<div id="wks-phrase-help">${phraseHelp()}</div>` })
        + modelBlock(d) });
      const test = K.section({ id: "wks-test", title: t("Проверка", "Test"), body:
          K.row({ label: t("Проверить микрофон и фразу", "Test the mic and the phrase"), keywords: "test проверить уровень level",
            desc: t("Скажите фразу: полоска показывает уровень звука, ниже — что услышано. Окно при этом не открывается.",
              "Say the phrase: the bar shows the sound level, below is what was heard. The window does not open."),
            control: `<button type="button" class="pbtn sx-btn-sm" data-wks="test" id="wks-test-btn">${t("Проверить", "Test")}</button>` })
        + `<div class="wks-meter-wrap" id="wks-meter-wrap" hidden><div class="wks-meter" aria-hidden="true"><i id="wks-meter"></i></div><div class="wks-heard" id="wks-heard" aria-live="polite"></div></div>` });
      const tune = K.section({ id: "wks-tune", title: t("Микрофон и отклик", "Microphone and response"), body:
          K.row({ label: t("Микрофон", "Microphone"), keywords: "microphone микрофон устройство device",
            desc: d.mic && d.mic.backend ? "" : t("Захват микрофона в этой системе недоступен.", "Microphone capture is unavailable on this system."),
            control: K.select("wks-device", devOptions, s.device, { width: 240 }) })
        + K.row({ label: t("Чувствительность", "Sensitivity"), keywords: "sensitivity чувствительность порог ложные",
            desc: t("Выше — срабатывает охотнее, но чаще ошибается. Ниже — реже ложные срабатывания.", "Higher triggers more easily but errs more often. Lower — fewer false triggers."),
            control: K.slider("wks-sens", { min: 0, max: 100, step: 5, value: s.sensitivity, unit: "" }), key: "wake_sensitivity", def: 50 })
        + K.row({ label: t("Звуковой сигнал", "Sound cue"), keywords: "chime sound звук сигнал", desc: t("Короткий звук, когда фраза услышана.", "A short sound when the phrase is heard."),
            control: K.toggle("wks-chime", s.chime) })
        + K.row({ label: t("Анимация", "Animation"), keywords: "animation glow анимация подсветка", desc: t("Окно мягко подсвечивается, пока слушает.", "The window glows softly while listening."),
            control: K.toggle("wks-glow", s.glow) })
        + K.more({ id: "wks-more", title: t("Дополнительно", "Advanced"), body:
            K.row({ label: t("Пауза между срабатываниями", "Pause between triggers"), keywords: "cooldown пауза кулдаун",
              desc: t("Сколько секунд фраза не срабатывает повторно.", "How long the phrase cannot trigger again."),
              control: K.select("wks-cooldown", [1, 2, 3, 5, 10, 30].map((n) => [String(n), n + t(" с", " s")]), String(s.cooldown), { width: 110 }) })
          + K.note(t("Не срабатывает, пока MemuAI говорит, идёт MemuAI Live или голосовой ввод, и пока компьютер заблокирован.",
              "Never triggers while MemuAI speaks, during MemuAI Live or voice input, or while the PC is locked."), "info") }) });
      return `<div class="wks">${hero}${main}${test}${tune}</div>`;
    }

    function render() {
      if (!S.data) return;
      const focused = document.activeElement && document.activeElement.id;
      const phraseValue = el.querySelector("#wks-phrase") ? el.querySelector("#wks-phrase").value : null;
      const moreOpen = !!(el.querySelector("#wks-more") && el.querySelector("#wks-more").open);
      el.innerHTML = html(S.data);
      if (moreOpen && el.querySelector("#wks-more")) el.querySelector("#wks-more").open = true;
      if (phraseValue !== null && focused === "wks-phrase") { const input = el.querySelector("#wks-phrase"); input.value = phraseValue; input.focus(); }
      if (S.testing) showMeter(true);
    }

    function patchStatus() {
      const d = S.data;
      if (!d) return;
      const info = statusInfo(d.status);
      const node = el.querySelector("#wks-status");
      if (node) node.textContent = info.text;
      const hero = el.querySelector(".wks-hero");
      if (hero) hero.dataset.tone = info.tone;
    }

    /* ---------------------------------------------------------- data */
    async function load() {
      try { S.data = await call("/api/wake/state"); }
      catch (e) { el.innerHTML = K.note(esc(t("Не удалось загрузить настройки слова-активатора: ", "Could not load the wake word settings: ") + (e.message || e)), "danger"); return; }
      render();
      checkPhrase(S.data.settings.phrase || S.data.defaults[S.data.settings.lang], S.data.settings.lang);
      schedule();
    }

    async function save(patch) {
      try {
        S.data = await call("/api/wake/settings", { settings: patch });
        render();
        if (window.MemuSettingsKit && window.MemuSettingsKit.saved) window.MemuSettingsKit.saved(true);
        schedule();
      } catch (e) { toast(t("Не сохранилось: ", "Not saved: ") + (e.message || e), "error"); }
    }

    async function checkPhrase(phrase, lang) {
      try { S.phraseCheck = await call("/api/wake/phrase", { phrase, lang }); }
      catch (e) { S.phraseCheck = null; }
      const help = el.querySelector("#wks-phrase-help");
      if (help) help.innerHTML = phraseHelp();
      return S.phraseCheck;
    }

    function downloading() { return S.data && S.data.models.some((m) => m.download && (m.download.state === "downloading" || m.download.state === "unpacking")); }
    function installing() { return S.data && S.data.engine && S.data.engine.install && S.data.engine.install.state === "running"; }

    function schedule() {
      clearTimeout(S.poll);
      S.poll = setTimeout(tick, downloading() || installing() ? 700 : 2000);
    }
    async function tick() {
      if (!el.isConnected) { stopTest(); return; }
      if (el.offsetParent !== null) {
        try {
          if (downloading() || installing()) {
            const wasDownloading = downloading(), wasInstalling = installing();
            S.data = await call("/api/wake/state");
            if (wasDownloading && !downloading()) {
              render();
              const m = S.data.models.find((row) => row.lang === S.data.settings.lang);
              const input = el.querySelector("#wks-phrase");
              if (m && m.ready) { toast(t("Модель скачана", "Model downloaded")); checkPhrase((input && input.value) || S.data.defaults[S.data.settings.lang], S.data.settings.lang); }
            } else if (wasInstalling && !installing()) render();
            else {
              const row = el.querySelector("#wks-model-row");
              const m = S.data.models.find((x) => x.lang === S.data.settings.lang);
              if (row && m && m.download) {
                const bar = row.querySelector(".wks-progress i"), pct = row.querySelector(".wks-pct");
                if (bar) bar.style.width = m.download.progress + "%";
                if (pct) pct.textContent = m.download.state === "unpacking" ? t("распаковка", "unpacking") : m.download.progress + "%";
              } else render();
            }
          } else if (!S.testing) {
            S.data.status = await call("/api/wake/status");
          }
          patchStatus();
        } catch (e) { /* server restarting */ }
      }
      schedule();
    }

    /* ---------------------------------------------------------- test mode */
    function showMeter(on) {
      const wrap = el.querySelector("#wks-meter-wrap"), btn = el.querySelector("#wks-test-btn");
      if (wrap) wrap.hidden = !on;
      if (btn) btn.textContent = on ? t("Остановить", "Stop") : t("Проверить", "Test");
    }
    async function startTest() {
      S.testing = true; S.lastHitAt = 0;
      showMeter(true);
      const heard = el.querySelector("#wks-heard");
      if (heard) { heard.textContent = t("Скажите фразу…", "Say the phrase…"); heard.dataset.tone = ""; }
      try { S.data.status = await call("/api/wake/test", { on: true }); } catch (e) { stopTest(); toast(e.message || String(e), "error"); return; }
      S.lastHitAt = (S.data.status.last_hit && S.data.status.last_hit.at) || 0;
      clearInterval(S.testRenew);
      S.testRenew = setInterval(() => call("/api/wake/test", { on: true }).catch(() => {}), 10000);
      testLoop();
    }
    async function testLoop() {
      if (!S.testing) return;
      if (!el.isConnected || el.offsetParent === null) { stopTest(); return; }
      try {
        const st = await call("/api/wake/status");
        S.data.status = st;
        patchStatus();
        const bar = el.querySelector("#wks-meter");
        if (bar) bar.style.width = Math.round(Math.min(1, Math.sqrt(st.level || 0) * 3.2) * 100) + "%";
        const heard = el.querySelector("#wks-heard");
        if (heard && st.last_hit && st.last_hit.at > S.lastHitAt) {
          S.lastHitAt = st.last_hit.at;
          heard.dataset.tone = "ok";
          heard.textContent = t(`✓ Фраза услышана: «${st.last_hit.phrase}» · уверенность ${Math.round(st.last_hit.conf * 100)}%`,
            `✓ Heard the phrase “${st.last_hit.phrase}” · confidence ${Math.round(st.last_hit.conf * 100)}%`);
        } else if (heard && st.state === "error") { heard.dataset.tone = "err"; heard.textContent = statusInfo(st).text; }
      } catch (e) { /* keep trying */ }
      S.testPoll = setTimeout(testLoop, 150);
    }
    function stopTest() {
      if (!S.testing) return;
      S.testing = false;
      clearTimeout(S.testPoll); clearInterval(S.testRenew);
      call("/api/wake/test", { on: false }).catch(() => {});
      showMeter(false);
    }

    /* ---------------------------------------------------------- events */
    el.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-wks]");
      if (!btn) return;
      const lang = S.data.settings.lang;
      const act = btn.dataset.wks;
      try {
        if (act === "test") { if (S.testing) stopTest(); else startTest(); return; }
        if (act === "phrase-default") { save({ phrase: "" }).then(() => checkPhrase(S.data.defaults[lang], lang)); return; }
        btn.disabled = true;
        if (act === "model-download") S.data = await call("/api/wake/model", { lang, action: "download" });
        else if (act === "model-cancel") S.data = await call("/api/wake/model", { lang, action: "cancel" });
        else if (act === "model-delete") {
          if (!window.confirm(t("Удалить модель распознавания? Слово-активатор перестанет работать, пока её не скачать снова.", "Delete the speech model? The wake word stops working until it is downloaded again."))) { btn.disabled = false; return; }
          S.data = await call("/api/wake/model", { lang, action: "delete" });
        } else if (act === "engine-install") S.data = await call("/api/wake/engine/install", {});
        render(); schedule();
      } catch (err) { btn.disabled = false; toast(en && err.en ? err.en : (err.message || String(err)), "error"); }
    });
    // Turning it on (or switching the language while on) fetches the model it needs — one less step.
    async function ensureModel() {
      const d = S.data;
      if (!d || !d.settings.enabled || !d.engine.available) return;
      const m = d.models.find((row) => row.lang === d.settings.lang);
      if (!m || m.ready || (m.download && (m.download.state === "downloading" || m.download.state === "unpacking"))) return;
      try { S.data = await call("/api/wake/model", { lang: d.settings.lang, action: "download" }); render(); schedule(); }
      catch (e) { toast(e.message || String(e), "error"); }
    }
    el.addEventListener("change", (e) => {
      const id = e.target.id;
      if (id === "wks-enabled") save({ enabled: e.target.checked }).then(ensureModel);
      else if (id === "wks-chime") save({ chime: e.target.checked });
      else if (id === "wks-glow") save({ glow: e.target.checked });
      else if (id === "wks-device") save({ device: e.target.value });
      else if (id === "wks-cooldown") save({ cooldown: +e.target.value });
      else if (id === "wks-sens") save({ sensitivity: +e.target.value });
      else if (e.target.name === "wks-lang" && e.target.checked) {
        const lang = e.target.value;
        // a custom phrase in the other alphabet cannot work with the other model: back to that language's default
        const own = S.data.settings.phrase || "";
        const foreign = own && (lang === "en" ? /[а-яё]/i.test(own) : /[a-z]/i.test(own) && !/[а-яё]/i.test(own));
        save(foreign ? { lang, phrase: "" } : { lang }).then(() => { checkPhrase(S.data.settings.phrase || S.data.defaults[lang], lang); ensureModel(); });
      } else if (id === "wks-phrase") commitPhrase(e.target.value);
    });
    el.addEventListener("input", (e) => {
      if (e.target.id !== "wks-phrase") return;
      clearTimeout(S.checkTimer);
      const value = e.target.value;
      S.checkTimer = setTimeout(() => checkPhrase(value || S.data.defaults[S.data.settings.lang], S.data.settings.lang), 350);
    });
    el.addEventListener("keydown", (e) => { if (e.target.id === "wks-phrase" && e.key === "Enter") { e.preventDefault(); e.target.blur(); } });
    async function commitPhrase(value) {
      const lang = S.data.settings.lang;
      const clean = String(value || "").trim();
      const fallback = S.data.defaults[lang];
      const check = await checkPhrase(clean || fallback, lang);
      if (check && check.problem) { toast(L(check.problem), "error"); return; }
      const normalized = check ? check.phrase : clean;
      if (normalized === S.data.settings.phrase || (!S.data.settings.phrase && normalized === fallback)) return;
      save({ phrase: normalized === fallback ? "" : normalized });
    }

    load();
  }

  Q.settingsSection("wake", mount, {
    title: { ru: "Слово-активатор", en: "Wake word" }, order: 30, icon: ICON,
    desc: { ru: "«Привет, Мему» — и быстрый доступ слушает. Офлайн", en: "“Hey Memu” — and Quick Ask listens. Offline" },
  });
  window.MemuWakeSettings = { mount };
})();
