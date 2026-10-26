

(function () {
  "use strict";
  const Core = window.MemuCompanionCore;
  if (!Core) return;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function prefs(ctx) {
    const out = {};
    for (const key of Object.keys(Core.PREF_DEFAULTS)) out[key] = ctx.pref(key, Core.PREF_DEFAULTS[key]);
    return Core.normalizePrefs(out);
  }

  function toggle(id, on, title, hint) {
    return `<div class="p-toggle"><div><div class="tl">${esc(title)}</div>${hint ? `<div class="td">${esc(hint)}</div>` : ""}</div>` +
      `<label class="switch"><input type="checkbox" data-companion-pref="${id}" ${on ? "checked" : ""}><span class="sl"></span></label></div>`;
  }
  function select(id, value, title, hint, options) {
    return `<div class="p-toggle"><div><div class="tl">${esc(title)}</div>${hint ? `<div class="td">${esc(hint)}</div>` : ""}</div>` +
      `<select class="pinput compact" data-companion-pref="${id}" style="width:auto;min-width:150px;margin:0">` +
      options.map(([v, label]) => `<option value="${esc(v)}" ${String(value) === String(v) ? "selected" : ""}>${esc(label)}</option>`).join("") + `</select></div>`;
  }

  function html(surface, ctx) {
    const en = ctx.lang === "en";
    const t = (ru, e) => (en ? e : ru);
    const p = prefs(ctx);
    const head = surface === "quick"
      ? toggle("companion_quick_enabled", p.companion_quick_enabled, t("Компаньон в «Быстром доступе»", "Companion in Quick Ask"),
        t("Живой персонаж рядом с ответом: дышит, моргает, говорит вместе с голосом", "A living character beside the answer: breathes, blinks, talks along with the voice")) +
        select("companion_quick_mode", p.companion_quick_mode, t("Где показывать", "Where to show it"),
          t("Перетащите персонажа за край окна — он останется на рабочем столе; булавка закрепляет его.", "Drag the character past the window edge and it stays on the desktop; the pin keeps it there."),
          [["inline", t("В окне «Быстрого доступа»", "Inside the Quick Ask window")], ["desktop", t("На рабочем столе", "On the desktop")], ["both", t("И там, и там", "Both")]])
      : toggle("companion_live_enabled", p.companion_live_enabled, t("Компаньон в MemuAI Live", "Companion in MemuAI Live"),
        t("Вместо сферы — анимированный персонаж, который говорит голосом Live", "An animated character instead of the orb, talking with Live's voice")) +
        select("companion_live_placement", p.companion_live_placement, t("Где показывать", "Where to show it"),
          t("«Над окном» — персонажа можно перетащить и изменить размер за уголок", "“Over the window” — drag the character anywhere and resize it by the corner"),
          [["panel", t("В панели Live", "In the Live panel")], ["window", t("Над окном MemuAI", "Over the MemuAI window")]]);

    return `<div class="companion-settings cs-calm" id="companion-settings-${surface}" data-surface="${surface}">
      <div class="cs-card">${head}</div>
      <div class="cs-group">
        <div class="cs-group-title">${t("Персонаж", "Character")}</div>
        <div class="cs-card cs-card--pad">
          <div class="companion-preview" data-companion-preview>
            <div class="companion-preview-actions">
              <button type="button" data-companion-try="wave">${t("Помахать", "Wave")}</button>
              <button type="button" data-companion-try="nod">${t("Кивнуть", "Nod")}</button>
              <button type="button" data-companion-try="happy">${t("Радость", "Happy")}</button>
              <button type="button" data-companion-try="sad">${t("Грусть", "Sad")}</button>
              <button type="button" data-companion-try="surprised">${t("Удивление", "Surprise")}</button>
              <button type="button" data-companion-try="speak">${t("Сказать фразу", "Say a line")}</button>
            </div>
          </div>
          <label class="field-label cs-label">${t("Модель", "Model")}</label>
          <div class="companion-models" data-companion-models><div class="td">${t("Загружаю библиотеку…", "Loading the library…")}</div></div>
          <div class="companion-row">
            <button type="button" class="pbtn ghost sx-btn-sm" data-companion-import>${t("Импорт .vrm или .zip", "Import .vrm or .zip")}</button>
            <button type="button" class="pbtn ghost sx-btn-sm" data-companion-import-folder>${t("Импорт папки Live2D", "Import a Live2D folder")}</button>
            <input type="file" accept=".vrm,.zip,.glb" data-companion-file hidden>
            <input type="file" webkitdirectory multiple data-companion-folder hidden>
            <span class="td" data-companion-import-status></span>
          </div>
          <p class="companion-legal">${t("VRM работает сразу; Live2D (Cubism 3–5) — после установки среды Live2D в «Дополнительно». Используйте только модели, на которые у вас есть права.", "VRM works out of the box; Live2D (Cubism 3–5) after installing the Live2D runtime under “Advanced”. Use only models you have the rights to.")}</p>
        </div>
      </div>
      <div class="cs-card">
        <div class="p-toggle"><div><div class="tl">${t("Размер", "Size")}</div><div class="td">${t("Также уголок или Ctrl+колесо над персонажем", "Also the corner or Ctrl+wheel over the character")}</div></div><div class="companion-range"><input type="range" min="60" max="200" step="5" value="${p.companion_size}" data-companion-pref="companion_size"><output data-companion-size-out>${p.companion_size}%</output></div></div>
        ${select("companion_framing", p.companion_framing, t("Кадр", "Framing"), "", [["bust", t("По пояс", "Waist up")], ["full", t("В полный рост", "Full body")]])}
      </div>
      <details class="cs-more" data-companion-more>
        <summary><span>${t("Дополнительно", "Advanced")}</span><small>${t("губы, эмоции, жесты, кадры, Live2D", "lip sync, emotions, gestures, frame rate, Live2D")}</small></summary>
        <div class="cs-card">
          ${toggle("companion_lipsync", p.companion_lipsync, t("Движение губ под голос", "Lip sync with the voice"), t("По настоящему звуку; если он недоступен — по тексту", "From the real audio; from the text when it is unavailable"))}
          ${toggle("companion_emotions", p.companion_emotions, t("Эмоции по ответу", "Emotions from the answer"), t("По скрытым меткам модели или словам ответа", "From the model's hidden tags or the answer's words"))}
          ${surface === "live" ? toggle("companion_emotion_tags", p.companion_emotion_tags, t("Просить модель ставить метки эмоций", "Ask the model for emotion tags"), t("Обычный Live: модель начинает ответ скрытой меткой; она вырезается до показа и озвучки", "Classic Live: the model starts a reply with a hidden tag, removed before display and speech")) : ""}
          ${toggle("companion_gestures", p.companion_gestures, t("Жесты", "Gestures"), t("Машет при открытии, думает, пока ждёт, кивает в начале ответа", "Waves on open, thinks while waiting, nods when the answer starts"))}
          ${toggle("companion_look_at", p.companion_look_at, t("Следить за курсором", "Follow the cursor"), "")}
          ${toggle("companion_bubble", p.companion_bubble, t("Облачко с текстом", "Speech bubble"), t("Когда персонаж показан отдельно от ответа", "When the character is shown apart from the answer"))}
          ${toggle("companion_personality_link", p.companion_personality_link, t("Характер из личности", "Character from the personality"), t("Живость движений и улыбка — из выбранной личности Live", "Liveliness and smile follow the chosen Live personality"))}
          ${select("companion_fps", p.companion_fps, t("Частота кадров", "Frame rate"), t("Анимация останавливается, когда персонаж скрыт", "Animation stops while the character is hidden"), [["30", "30 FPS"], ["60", "60 FPS"]])}
          ${toggle("companion_low_power", p.companion_low_power, t("Экономия энергии", "Low power"), t("Без сглаживания, не больше 24 кадров", "No antialiasing, at most 24 frames"))}
          <details class="advanced-settings" data-companion-live2d><summary>${t("Среда Live2D", "Live2D runtime")}</summary>
            <div data-companion-runtime class="td">…</div>
          </details>
        </div>
      </details>
    </div>`;
  }

  const CRC = (() => { const table = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; table[n] = c >>> 0; } return table; })();
  function crc32(bytes) { let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = CRC[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }

  function buildZip(files) {
    const enc = new TextEncoder();
    const parts = [], central = [];
    let offset = 0;
    for (const file of files) {
      const name = enc.encode(file.name), data = file.data, crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
      local.setUint16(8, 0, true); local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
      local.setUint16(26, name.length, true);
      parts.push(new Uint8Array(local.buffer), name, data);
      const entry = new DataView(new ArrayBuffer(46));
      entry.setUint32(0, 0x02014b50, true); entry.setUint16(4, 20, true); entry.setUint16(6, 20, true); entry.setUint16(8, 0x0800, true);
      entry.setUint32(16, crc, true); entry.setUint32(20, data.length, true); entry.setUint32(24, data.length, true);
      entry.setUint16(28, name.length, true); entry.setUint32(42, offset, true);
      central.push(new Uint8Array(entry.buffer), name);
      offset += 30 + name.length + data.length;
    }
    const size = central.reduce((n, a) => n + a.length, 0);
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(8, files.length, true); end.setUint16(10, files.length, true);
    end.setUint32(12, size, true); end.setUint32(16, offset, true);
    return new Blob([...parts, ...central, new Uint8Array(end.buffer)], { type: "application/zip" });
  }

  function bind(root, surface, ctx) {
    if (!root) return null;
    const en = ctx.lang === "en";
    const t = (ru, e) => (en ? e : ru);
    let preview = null;
    const previewBox = root.querySelector("[data-companion-preview]");

    function startPreview() {
      if (preview || !window.MemuCompanion) return;
      preview = MemuCompanion.create({ container: previewBox, surface: surface === "quick" ? "quick" : "live", lang: ctx.lang,
        prefs: () => Object.assign({}, ctx.allPrefs ? ctx.allPrefs() : {}, prefs(ctx)) });
    }

    const visibleObserver = typeof IntersectionObserver !== "undefined" ? new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) startPreview();
    }) : null;
    if (visibleObserver) visibleObserver.observe(previewBox); else startPreview();

    const gone = new MutationObserver(() => {
      if (!document.body.contains(root)) { gone.disconnect(); if (visibleObserver) visibleObserver.disconnect(); if (preview) preview.destroy(); preview = null; }
    });
    gone.observe(document.body, { childList: true, subtree: true });

    root.querySelectorAll("[data-companion-try]").forEach((button) => button.addEventListener("click", () => {
      if (!preview) return;
      const what = button.dataset.companionTry;
      if (what === "wave" || what === "nod") preview.gesture(what);
      else if (what === "speak") {
        const line = t("Привет! Я твой компаньон. Говорю, моргаю и слежу за курсором.", "Hi! I'm your companion. I talk, blink and follow your cursor.");
        preview.emotion("happy", 0.7);
        preview.speak(true, line);
        setTimeout(() => preview && preview.speak(false), Core.textVisemeTrack(line).duration + 200);
      } else preview.emotion(what, 0.95);
    }));

    async function save(patch) {
      await ctx.save(patch);
      if (preview) preview.setPrefs();
      if (window.MemuCompanionLive && MemuCompanionLive.refresh) MemuCompanionLive.refresh();
    }
    root.querySelectorAll("[data-companion-pref]").forEach((input) => {
      const key = input.dataset.companionPref;
      const handler = () => {
        let value = input.type === "checkbox" ? input.checked : input.value;
        if (key === "companion_size" || key === "companion_fps") value = +value;
        if (key === "companion_size") root.querySelector("[data-companion-size-out]").textContent = value + "%";
        save({ [key]: value });
      };
      input.addEventListener(input.type === "range" ? "change" : "change", handler);
      if (input.type === "range") input.addEventListener("input", () => { root.querySelector("[data-companion-size-out]").textContent = input.value + "%"; });
    });

    const list = root.querySelector("[data-companion-models]");
    async function renderModels(force) {
      let lib;
      try { lib = await MemuCompanion.fetchModels(force); }
      catch (error) { list.innerHTML = `<div class="td">${esc(t("Библиотека недоступна: ", "Library unavailable: ") + error.message)}</div>`; return; }
      const active = prefs(ctx).companion_model_id;
      list.innerHTML = (lib.models || []).map((m) => `<button type="button" class="companion-model ${m.id === active ? "active" : ""}" data-model="${esc(m.id)}" aria-pressed="${m.id === active}">
          <b title="${esc(m.name)}">${esc(m.name)}</b>
          <small>${esc(m.kind === "live2d" ? "Live2D" : "VRM " + (m.vrm_version || ""))}${m.builtin ? " · " + t("встроенная", "built-in") : ""}</small>
          <small title="${esc(m.license || "")}">${esc(m.license ? t("Лицензия: ", "License: ") + m.license : t("Лицензия не указана", "No license stated"))}</small>
          ${m.builtin ? "" : `<span class="companion-model-del" role="button" tabindex="0" data-delete="${esc(m.id)}" aria-label="${esc(t("Удалить", "Delete"))}" title="${esc(t("Удалить", "Delete"))}">×</span>`}
        </button>`).join("");
      renderRuntime(lib.runtime);
    }
    list.addEventListener("click", async (event) => {
      const del = event.target.closest("[data-delete]");
      if (del) {
        event.stopPropagation();
        const id = del.dataset.delete;
        if (!confirm(t("Удалить модель из библиотеки?", "Delete the model from the library?"))) return;
        const response = await fetch("/api/companion/models/" + encodeURIComponent(id), { method: "DELETE" });
        if (!response.ok) { ctx.toast(t("Не удалось удалить модель", "Could not delete the model"), "err"); return; }
        MemuCompanion.invalidate();
        if (prefs(ctx).companion_model_id === id) await save({ companion_model_id: "sample" });
        renderModels(true);
        return;
      }
      const card = event.target.closest("[data-model]");
      if (!card) return;
      await save({ companion_model_id: card.dataset.model });
      renderModels();
    });

    const status = root.querySelector("[data-companion-import-status]");
    async function upload(blob, filename) {
      status.textContent = t("Проверяю и импортирую…", "Checking and importing…");
      try {
        const response = await fetch("/api/companion/models/import?filename=" + encodeURIComponent(filename), { method: "POST", body: blob,
          headers: { "Content-Type": "application/octet-stream" } });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(t(data.error, data.error_en || data.error) || "HTTP " + response.status);
        status.textContent = "";
        MemuCompanion.invalidate();
        await save({ companion_model_id: data.model.id });
        ctx.toast(t("Модель добавлена: ", "Model added: ") + data.model.name, "ok");
        if (data.model.kind === "live2d" && !(await MemuCompanion.fetchModels(true)).runtime.installed) {
          ctx.toast(t("Для Live2D нужна среда Live2D — установите её ниже", "Live2D needs the Live2D runtime — install it below"), "warn");
          root.querySelector("[data-companion-live2d]").open = true;
        }
        renderModels(true);
      } catch (error) {
        status.textContent = "";
        ctx.toast(t("Модель не импортирована: ", "Model not imported: ") + error.message, "err");
      }
    }
    const fileInput = root.querySelector("[data-companion-file]");
    root.querySelector("[data-companion-import]").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => { const file = fileInput.files[0]; fileInput.value = ""; if (file) upload(file, file.name); });
    const folderInput = root.querySelector("[data-companion-folder]");
    root.querySelector("[data-companion-import-folder]").addEventListener("click", () => folderInput.click());
    folderInput.addEventListener("change", async () => {
      const files = Array.from(folderInput.files || []);
      folderInput.value = "";
      if (!files.length) return;
      if (!files.some((f) => /\.model3\.json$/i.test(f.name))) { ctx.toast(t("В папке нет файла *.model3.json", "The folder has no *.model3.json file"), "err"); return; }
      const total = files.reduce((n, f) => n + f.size, 0);
      if (total > 200 * 1024 * 1024) { ctx.toast(t("Папка больше 200 МБ", "The folder is larger than 200 MB"), "err"); return; }
      status.textContent = t("Собираю папку…", "Packing the folder…");
      const entries = [];
      for (const f of files) entries.push({ name: (f.webkitRelativePath || f.name).replace(/\\/g, "/"), data: new Uint8Array(await f.arrayBuffer()) });
      const root0 = (files[0].webkitRelativePath || "").split("/")[0] || "live2d";
      upload(buildZip(entries), root0 + ".zip");
    });

    const runtimeBox = root.querySelector("[data-companion-runtime]");
    function renderRuntime(runtime) {
      if (!runtime) return;
      if (runtime.installed) {
        runtimeBox.innerHTML = `<p>${t("Среда Live2D установлена в папку данных MemuAI.", "The Live2D runtime is installed in MemuAI's data folder.")}</p>
          <button type="button" class="pbtn ghost" data-runtime-remove>${t("Удалить среду Live2D", "Remove the Live2D runtime")}</button>`;
        runtimeBox.querySelector("[data-runtime-remove]").onclick = async () => {
          await fetch("/api/companion/runtime", { method: "DELETE" });
          renderModels(true);
        };
        return;
      }
      runtimeBox.innerHTML = `<p class="companion-legal">${t(
        "Live2D Cubism Core — закрытая библиотека Live2D Inc. MemuAI её не содержит и не распространяет. По кнопке ниже MemuAI скачает её с официального сайта Live2D (а также свободные pixi.js и pixi-live2d-display) в вашу папку данных. Используя её, вы принимаете",
        "Live2D Cubism Core is a proprietary library of Live2D Inc. MemuAI does not include or distribute it. The button below downloads it from Live2D's official site (plus the free pixi.js and pixi-live2d-display) into your data folder. By using it you accept the")}
        <a href="${esc(runtime.license_url)}" target="_blank" rel="noopener">${t("лицензионное соглашение Live2D", "Live2D Proprietary Software License")}</a>${t(". Учтите: программы, загружающие произвольные модели, Live2D относит к", ". Note that Live2D classifies apps that load arbitrary models as")}
        <a href="${esc(runtime.expandable_url)}" target="_blank" rel="noopener">«Expandable Applications»</a>.</p>
        <label class="companion-row"><input type="checkbox" data-runtime-consent> ${t("Я прочитал(а) условия Live2D и скачиваю среду для личного использования", "I have read Live2D's terms and download the runtime for personal use")}</label>
        <button type="button" class="pbtn" data-runtime-install disabled>${t("Скачать среду Live2D", "Download the Live2D runtime")}</button>`;
      const consent = runtimeBox.querySelector("[data-runtime-consent]");
      const install = runtimeBox.querySelector("[data-runtime-install]");
      consent.onchange = () => { install.disabled = !consent.checked; };
      install.onclick = async () => {
        install.disabled = true; install.textContent = t("Скачиваю…", "Downloading…");
        try {
          const response = await fetch("/api/companion/runtime/install", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ consent: true }) });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(t(data.error, data.error_en || data.error) || "HTTP " + response.status);
          ctx.toast(t("Среда Live2D установлена", "The Live2D runtime is installed"), "ok");
          if (preview) preview.reload();
        } catch (error) {
          ctx.toast(error.message, "err");
        }
        renderModels(true);
      };
    }
    renderModels(false);
    return { destroy() { if (preview) preview.destroy(); preview = null; gone.disconnect(); } };
  }

  window.MemuCompanionSettings = { html: html, bind: bind, buildZip: buildZip, crc32: crc32 };
})();
