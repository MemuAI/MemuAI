/* MemuAI · Settings → Voice → «Озвучивание ответов» (Round 25).

   One clear choice «Чем озвучивать» instead of four sources plus a raw Base URL:
   Windows, downloaded Piper voices, cloud services (OpenAI, Google Gemini,
   ElevenLabs, Azure) with their own models/voices/key links, any other
   OpenAI-compatible API, or a local TTS server. Cloud services are stored in the
   existing preferences (voice_tts_provider = "api", the service is the host of
   voice_tts_api_url — the backend detects it the same way, app/tts_providers.py),
   so older saved settings keep working. «Проверить» saves and plays a sample with
   the real backend path and shows the short error the backend produced.

   window.MemuVoiceSettings = { ttsHtml(ctx), bindTts(ctx), sttPresetHtml(ctx), bindStt(ctx), sourceLabel(ctx) }
   ctx: { en, K, esc, pref, catalog, ttsModelCards, localEngines, savePreferences, api, toast, rerender } */
(function () {
  "use strict";

  const CUSTOM = "__custom";
  const STT_PRESETS = [
    { id: "openai", label: "OpenAI", url: "https://api.openai.com/v1", model: "gpt-4o-mini-transcribe", key_url: "https://platform.openai.com/api-keys" },
    { id: "groq", label: "Groq", url: "https://api.groq.com/openai/v1", model: "whisper-large-v3-turbo", key_url: "https://console.groq.com/keys" },
  ];

  function providers(ctx) { return (ctx.catalog && ctx.catalog.tts_api_providers) || []; }

  function providerForUrl(url) {
    let host = "";
    try { host = new URL(String(url || "").trim()).hostname.toLowerCase(); } catch (e) { return "custom"; }
    if (host === "api.openai.com") return "openai";
    if (host === "generativelanguage.googleapis.com") return "gemini";
    if (host === "elevenlabs.io" || host.endsWith(".elevenlabs.io")) return "elevenlabs";
    if (host.endsWith(".tts.speech.microsoft.com") || host.endsWith(".tts.speech.azure.us")) return "azure";
    return "custom";
  }

  function currentSource(pref) {
    const provider = pref("voice_tts_provider", "windows");
    return provider === "api" ? "api:" + providerForUrl(pref("voice_tts_api_url", "")) : provider;
  }

  // A key pasted from another service is the most common reason for 401: say so before the request.
  function keyMismatch(kind, key, en) {
    const k = String(key || "").trim();
    if (!k) return "";
    const owner = /^AIza/.test(k) ? "gemini" : /^sk_/.test(k) ? "elevenlabs" : /^sk-/.test(k) ? "openai" : "";
    if (!owner || owner === kind || kind === "custom") return "";
    const names = { openai: "OpenAI", gemini: "Google", elevenlabs: "ElevenLabs" };
    return en ? `This looks like a ${names[owner]} key — the selected service needs its own key.`
      : `Похоже, это ключ ${names[owner]} — выбранному сервису нужен свой ключ.`;
  }

  // A voice of another service (the reported «Alloy» with Gemini) is flagged right under the field.
  function voiceMismatch(ctx, kind, voice) {
    const v = String(voice || "").trim().toLowerCase();
    if (!v || kind === "custom" || kind === "azure") return "";
    const list = providers(ctx);
    const own = list.find((p) => p.id === kind);
    if (!own || (own.voices || []).some((x) => x.id.toLowerCase() === v)) return "";
    const owner = list.find((p) => p.id !== kind && p.id !== "custom" && (p.voices || []).some((x) => x.id.toLowerCase() === v));
    if (!owner) return "";
    const sample = (own.voices || []).slice(0, 4).map((x) => x.id).join(", ");
    return ctx.en ? `“${voice}” is a ${owner.label} voice — ${own.label} does not have it. Pick one from the list, e.g. ${sample}.`
      : `«${voice}» — голос ${owner.label}, у ${own.label} его нет. Выберите голос из списка, например ${sample}.`;
  }

  function sourceOptions(ctx) {
    const t = (ru, en) => (ctx.en ? en : ru);
    const cloud = providers(ctx).filter((p) => p.id !== "custom").map((p) => ["api:" + p.id, p.label, t("Облачные сервисы · нужен ключ", "Cloud services · key required")]);
    return [
      ["windows", t("Голоса Windows", "Windows voices"), t("Без интернета", "Offline")],
      ["downloaded", t("Скачанный голос Piper", "Downloaded Piper voice"), t("Без интернета", "Offline")],
      ...cloud,
      ["api:custom", t("Другой OpenAI-совместимый API", "Other OpenAI-compatible API"), t("Свой адрес", "Your own endpoint")],
      ["local", t("Свой локальный сервер (Kokoro, XTTS…)", "Your local server (Kokoro, XTTS…)"), t("Свой адрес", "Your own endpoint")],
    ];
  }

  function sourceHint(source, ctx) {
    const t = (ru, en) => (ctx.en ? en : ru);
    const hints = {
      windows: t("Голоса, установленные в Windows. Бесплатно, без ключей и интернета.", "Voices installed in Windows. Free, no keys, works offline."),
      downloaded: t("Нейросетевые голоса Piper: скачайте один раз — дальше работают без интернета.", "Piper neural voices: download once, then they work offline."),
      "api:openai": t("Естественные голоса OpenAI. Платно по символам, нужен API-ключ.", "Natural OpenAI voices. Paid per character, needs an API key."),
      "api:gemini": t("Голоса Gemini из Google AI Studio: 30 голосов, понимают русский. Нужен ключ AI Studio.", "Gemini voices from Google AI Studio: 30 voices, multilingual. Needs an AI Studio key."),
      "api:elevenlabs": t("Очень реалистичные голоса. Голос задаётся voice_id из вашей библиотеки ElevenLabs.", "Very realistic voices. The voice is a voice_id from your ElevenLabs library."),
      "api:azure": t("Microsoft Azure Speech: сотни нейроголосов. Укажите регион ресурса в адресе и ключ ресурса.", "Microsoft Azure Speech: hundreds of neural voices. Put your resource region in the URL and use the resource key."),
      "api:custom": t("Любой сервис с OpenAI-форматом /audio/speech (прокси, OpenRouter-подобные шлюзы и т. п.).", "Any service with the OpenAI /audio/speech format (proxies, gateways, etc.)."),
      local: t("Сервер, который вы запускаете сами на этом компьютере (Kokoro-FastAPI, XTTS…).", "A server you run yourself on this computer (Kokoro-FastAPI, XTTS…)."),
    };
    return hints[source] || "";
  }

  // <select> of known values + «Другое…» that reveals a free-text input (names change; any value is allowed).
  function pickControl(ctx, id, value, options, placeholder) {
    const { esc } = ctx;
    const t = (ru, en) => (ctx.en ? en : ru);
    const known = options.some((o) => o.id === value);
    const custom = !known && !!value;
    const label = id.includes("voice") ? t("Голос", "Voice") : t("Модель", "Model");
    return `<div class="vs-pick"><select class="pinput compact sx-select" id="${id}-pick" aria-label="${label}">${options.map((o) => `<option value="${esc(o.id)}" ${o.id === value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}<option value="${CUSTOM}" ${custom || (!options.length) ? "selected" : ""}>${t("Другое — ввести вручную…", "Other — type it…")}</option></select>
      <input class="pinput sx-input sx-mono" id="${id}" aria-label="${label} · ID" value="${esc(value)}" placeholder="${esc(placeholder)}" ${custom || !options.length ? "" : "hidden"}></div>`;
  }

  function apiHtml(ctx, kind) {
    const { K, esc, pref } = ctx;
    const t = (ru, en) => (ctx.en ? en : ru);
    const meta = providers(ctx).find((p) => p.id === kind) || { models: [], voices: [], key_url: "", label: kind };
    const url = pref("voice_tts_api_url", "");
    const model = pref("voice_tts_api_model", "") || meta.default_model || "";
    const voice = pref("voice_tts_api_voice", "") || meta.default_voice || "";
    const key = pref("voice_tts_api_key", "");
    const mismatch = keyMismatch(kind, key, ctx.en);
    const keyLink = meta.key_url ? `<a href="${esc(meta.key_url)}" target="_blank" rel="noopener" class="vs-key-link">${t("Где взять ключ →", "Get a key →")}</a>` : "";
    const showUrl = kind === "custom" || kind === "azure";
    const urlHelp = kind === "azure" ? t("Замените westeurope на регион вашего ресурса Speech.", "Replace westeurope with your Speech resource region.")
      : t("Корень API, к нему добавляется /audio/speech. Например: https://my-proxy.example/v1", "API root; /audio/speech is appended. Example: https://my-proxy.example/v1");
    const wrongVoice = voiceMismatch(ctx, kind, voice);
    const voiceHelp = `<span id="st-tts-voice-warn" class="vs-warn" ${wrongVoice ? "" : "hidden"}>${esc(wrongVoice)}</span>` + (wrongVoice ? "" : kind === "elevenlabs" ? t("voice_id из Voices → «ID» в ElevenLabs; можно вставить любой.", "voice_id from Voices → “ID” in ElevenLabs; any id works.")
      : kind === "gemini" ? t("Голоса Gemini: у OpenAI-голосов (alloy, nova…) здесь нет аналога.", "Gemini voices; OpenAI voices (alloy, nova…) do not exist here.") : "");
    return K.section({ title: meta.label, body:
      (showUrl ? K.field({ label: t("Адрес API (Base URL)", "API address (Base URL)"), forId: "st-tts-url", help: urlHelp,
        control: `<input class="pinput sx-mono" id="st-tts-url" value="${esc(url)}" placeholder="${esc(meta.base_url || "https://…/v1")}">` }) : "")
      + K.field({ label: `${kind === "custom" ? t("API-ключ (если нужен)", "API key (if required)") : t("API-ключ", "API key")} ${keyLink}`, forId: "st-tts-key",
        help: `<span id="st-tts-key-warn" class="vs-warn" ${mismatch ? "" : "hidden"}>${esc(mismatch)}</span>`,
        control: K.secret("st-tts-key", key, { placeholder: kind === "gemini" ? "AIza…" : kind === "openai" ? "sk-…" : "" }) })
      + `<div class="sx-grid2 vs-grid">${meta.models.length || kind !== "azure" ? K.field({ label: t("Модель", "Model"), forId: "st-tts-model",
        control: pickControl(ctx, "st-tts-model", model, meta.models.map((m) => ({ id: m, label: m })), meta.default_model || "tts-1") }) : ""}`
      + K.field({ label: t("Голос", "Voice"), forId: "st-tts-api-voice", help: voiceHelp,
        control: pickControl(ctx, "st-tts-api-voice", voice, meta.voices || [], meta.default_voice || "") }) + `</div>` });
  }

  function localHtml(ctx) {
    const { K, esc, pref } = ctx;
    const t = (ru, en) => (ctx.en ? en : ru);
    return K.section({ title: t("Локальный TTS-сервер", "Local TTS server"), desc: t("Запустите OpenAI-совместимый сервер модели сами — MemuAI подключится к нему без консоли.", "Start the model's OpenAI-compatible server yourself — MemuAI connects without a console."), body:
      K.field({ label: t("Профиль движка", "Engine profile"), forId: "st-tts-local-engine", control: `<select class="pinput" id="st-tts-local-engine">${ctx.localEngines.map((item) => `<option value="${item.id}" ${pref("voice_tts_local_engine", "kokoro") === item.id ? "selected" : ""}>${item.name} · ${item.hint}</option>`).join("")}</select>` })
      + K.field({ label: "Base URL", forId: "st-tts-local-url", control: `<input class="pinput sx-mono" id="st-tts-local-url" value="${esc(pref("voice_tts_local_url", "http://127.0.0.1:8880/v1"))}">` })
      + `<div class="sx-grid2">${K.field({ label: t("Модель", "Model"), forId: "st-tts-local-model", control: `<input class="pinput" id="st-tts-local-model" value="${esc(pref("voice_tts_local_model", "kokoro"))}">` })}${K.field({ label: t("Голос", "Voice"), forId: "st-tts-local-voice", control: `<input class="pinput" id="st-tts-local-voice" value="${esc(pref("voice_tts_local_voice", "af_heart"))}">` })}</div>`
      + K.field({ label: t("API-ключ (необязательно)", "API key (optional)"), forId: "st-tts-local-key", control: K.secret("st-tts-local-key", pref("voice_tts_local_api_key", "")) }) });
  }

  function speedSupported(source, ctx) {
    if (source === "windows" || source === "local") return true;
    if (!source.startsWith("api:")) return false;
    const meta = providers(ctx).find((p) => p.id === source.slice(4));
    return !meta || !!meta.speed;
  }

  function ttsHtml(ctx) {
    const { K, esc, pref } = ctx;
    const t = (ru, en) => (ctx.en ? en : ru);
    const source = currentSource(pref);
    const options = sourceOptions(ctx).map(([value, label, group]) => ({ value, label, group }));
    const top = K.section({ body:
      K.row({ label: t("Чем озвучивать", "Speak with"), desc: esc(sourceHint(source, ctx)), keywords: "tts source источник голоса windows piper api openai gemini google elevenlabs azure",
        control: K.select("st-tts-source", options, source, { width: 300 }) })
      + K.row({ label: t("Проверка", "Test"), desc: `<span id="st-tts-test-status" class="vs-status" role="status" aria-live="polite">${t("Сохраняет настройки и проигрывает короткую фразу.", "Saves the settings and plays a short phrase.")}</span>`, keywords: "test проверить пример sample",
        control: `<button type="button" class="pbtn ghost sx-btn-sm" id="st-tts-test">${t("▶ Проверить", "▶ Test")}</button>` }) });
    let detail = "";
    if (source === "windows") {
      detail = K.section({ body: K.row({ label: t("Голос Windows", "Windows voice"), desc: t("Установленные голоса; загрузка и ключ не нужны", "Installed voices; no download or key"), keywords: "windows voice голос",
        control: `<select class="pinput compact" id="st-tts-voice" style="max-width:280px;margin:0"><option value="">${t("Системный по умолчанию", "System default")}</option>${((ctx.catalog && ctx.catalog.windows_voices) || []).map((v) => `<option value="${esc(v.name)}" ${pref("voice_tts_voice", "") === v.name ? "selected" : ""}>${esc(v.name)} · ${esc(v.culture || "")}</option>`).join("")}</select>` }) });
    } else if (source === "downloaded") {
      detail = K.section({ title: t("Скачанные голоса Piper", "Downloaded Piper voices"), card: false, aside: `<button class="pbtn ghost sx-btn-sm" id="st-voice-change-folder-tts">${t("Сменить папку", "Change folder")}</button><button class="pbtn ghost sx-btn-sm" id="st-voice-open-folder">${t("Открыть папку", "Open folder")}</button><button class="pbtn ghost sx-btn-sm" id="st-tts-add-custom">＋ ${t("Свой", "Custom")}</button>`,
        desc: `${t("Нажмите на скачанный голос, чтобы выбрать его. Язык сообщения определяется автоматически.", "Click a downloaded voice to select it. The message language is detected automatically.")} <span class="voice-model-path">${t("Папка: ", "Folder: ")}${esc((ctx.catalog && ctx.catalog.models_dir) || "—")}</span>`,
        body: `<div class="voice-model-list">${ctx.ttsModelCards || K.note(t("Каталог TTS временно недоступен.", "TTS catalog is temporarily unavailable."), "warn")}</div>` });
    } else if (source === "local") {
      detail = localHtml(ctx);
    } else {
      detail = apiHtml(ctx, source.slice(4));
    }
    const playback = K.section({ title: t("Воспроизведение", "Playback"), body:
      (speedSupported(source, ctx) ? K.row({ label: t("Скорость речи", "Speech rate"), desc: t("0 — обычная скорость голоса", "0 is the voice's normal speed"), key: "voice_tts_rate", def: 0, keywords: "rate скорость speed", control: K.slider("st-tts-rate", { min: -10, max: 10, step: 1, signed: true, value: pref("voice_tts_rate", 0), outputId: "st-tts-rate-value", attrs: 'class="voice-rate"' }) }) : "")
      + K.row({ label: t("Громкость", "Playback volume"), key: "voice_tts_volume", def: 100, keywords: "volume громкость", control: K.slider("st-tts-volume", { min: 0, max: 100, step: 1, value: pref("voice_tts_volume", 100), unit: "%", outputId: "st-tts-volume-value", attrs: 'class="voice-rate"' }) }) });
    return top + detail + playback;
  }

  function sourceLabel(ctx) {
    const t = (ru, en) => (ctx.en ? en : ru);
    const source = currentSource(ctx.pref);
    if (source.startsWith("api:")) {
      const meta = providers(ctx).find((p) => p.id === source.slice(4));
      return source === "api:custom" ? t("OpenAI-совместимый API", "OpenAI-compatible API") : (meta ? meta.label : "API");
    }
    return { windows: t("Голос Windows", "Windows voice"), downloaded: "Piper", local: t("Локальный сервер", "Local server") }[source] || source;
  }

  let testAudio = null;

  function bindTts(ctx) {
    const $ = (sel) => document.querySelector(sel);
    const t = (ru, en) => (ctx.en ? en : ru);
    const source = $("#st-tts-source");
    if (!source) return;
    const kind = currentSource(ctx.pref).replace(/^api:/, "");
    source.onchange = async () => {
      const value = source.value;
      if (value.startsWith("api:")) {
        const id = value.slice(4);
        const meta = providers(ctx).find((p) => p.id === id) || {};
        const patch = { voice_tts_provider: "api" };
        const sameService = ctx.pref("voice_tts_provider", "") === "api" && providerForUrl(ctx.pref("voice_tts_api_url", "")) === id;
        if (!sameService) {
          // A service switch resets model/voice to that service's defaults: an OpenAI voice
          // sent to Gemini was exactly the reported «Alloy + gemini-…-tts» failure.
          const keepUrl = id === "custom" && providerForUrl(ctx.pref("voice_tts_api_url", "")) === "custom";
          patch.voice_tts_api_url = keepUrl ? ctx.pref("voice_tts_api_url", "") : (meta.base_url || "");
          patch.voice_tts_api_model = meta.default_model || "";
          patch.voice_tts_api_voice = meta.default_voice || "";
        }
        await ctx.savePreferences(patch);
      } else {
        await ctx.savePreferences({ voice_tts_provider: value });
      }
      ctx.rerender();
    };
    const pickValue = (id) => {
      const pick = $(`#${id}-pick`), input = $(`#${id}`);
      if (!input) return undefined;
      return pick && pick.value !== CUSTOM ? pick.value : input.value.trim();
    };
    const saveApi = () => {
      const patch = { voice_tts_api_key: ($("#st-tts-key")?.value || "").trim() };
      if ($("#st-tts-url")) patch.voice_tts_api_url = $("#st-tts-url").value.trim();
      const model = pickValue("st-tts-model"), voice = pickValue("st-tts-api-voice");
      if (model !== undefined) patch.voice_tts_api_model = model;
      if (voice !== undefined) patch.voice_tts_api_voice = voice;
      const voiceWarn = $("#st-tts-voice-warn");
      if (voiceWarn && voice !== undefined) { const text = voiceMismatch(ctx, kind, voice); voiceWarn.textContent = text; voiceWarn.hidden = !text; }
      const warn = $("#st-tts-key-warn");
      if (warn) { const text = keyMismatch(kind, patch.voice_tts_api_key, ctx.en); warn.textContent = text; warn.hidden = !text; }
      return ctx.savePreferences(patch);
    };
    ["st-tts-model", "st-tts-api-voice"].forEach((id) => {
      const pick = $(`#${id}-pick`), input = $(`#${id}`);
      if (!pick || !input) return;
      pick.onchange = () => {
        input.hidden = pick.value !== CUSTOM;
        if (pick.value === CUSTOM) { input.value = ""; input.focus(); } else input.value = pick.value;
        saveApi();
      };
      input.onchange = saveApi;
    });
    if ($("#st-tts-key")) $("#st-tts-key").onchange = saveApi;
    if ($("#st-tts-url")) $("#st-tts-url").onchange = async () => {
      await saveApi();
      // A typed address of a known service switches the form to that service's fields.
      if (providerForUrl($("#st-tts-url").value) !== kind) ctx.rerender();
    };

    const saveLocal = () => ctx.savePreferences({ voice_tts_local_engine: $("#st-tts-local-engine")?.value || "custom", voice_tts_local_url: $("#st-tts-local-url")?.value.trim() || "", voice_tts_local_api_key: $("#st-tts-local-key")?.value.trim() || "", voice_tts_local_model: $("#st-tts-local-model")?.value.trim() || "", voice_tts_local_voice: $("#st-tts-local-voice")?.value.trim() || "" });
    ["#st-tts-local-url", "#st-tts-local-key", "#st-tts-local-model", "#st-tts-local-voice"].forEach((id) => { if ($(id)) $(id).onchange = saveLocal; });
    if ($("#st-tts-local-engine")) $("#st-tts-local-engine").onchange = async (e) => {
      const engine = ctx.localEngines.find((item) => item.id === e.target.value); if (!engine) return;
      $("#st-tts-local-url").value = engine.url; $("#st-tts-local-model").value = engine.model; $("#st-tts-local-voice").value = engine.voice;
      await saveLocal();
    };
    if ($("#st-tts-voice")) $("#st-tts-voice").onchange = (e) => ctx.savePreferences({ voice_tts_voice: e.target.value });
    if ($("#st-tts-rate")) $("#st-tts-rate").oninput = (e) => { const n = +e.target.value; $("#st-tts-rate-value").textContent = n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0"; ctx.savePreferences({ voice_tts_rate: +e.target.value }); };
    if ($("#st-tts-volume")) $("#st-tts-volume").oninput = (e) => { $("#st-tts-volume-value").textContent = `${e.target.value}%`; ctx.savePreferences({ voice_tts_volume: +e.target.value }); };

    const test = $("#st-tts-test"), status = $("#st-tts-test-status");
    if (test) test.onclick = async () => {
      if (testAudio) { testAudio.pause(); testAudio = null; }
      test.disabled = true;
      status.classList.remove("vs-ok", "vs-bad");
      status.textContent = t("Готовлю голос…", "Preparing the voice…");
      try {
        if ($("#st-tts-key")) await saveApi();
        else if ($("#st-tts-local-url")) await saveLocal();
        const result = await ctx.api("/api/voice/speak", { method: "POST", body: { text: t("Привет! Так MemuAI будет читать ответы вслух.", "Hi! This is how MemuAI will read replies aloud.") } });
        testAudio = new Audio(result.audio);
        testAudio.volume = Math.max(0, Math.min(1, (+ctx.pref("voice_tts_volume", 100) || 0) / 100));
        await testAudio.play();
        status.textContent = t("✓ Работает — так будут звучать ответы.", "✓ Works — replies will sound like this.");
        status.classList.add("vs-ok");
      } catch (error) {
        status.textContent = (error && error.message) || t("Не удалось озвучить", "Speech failed");
        status.classList.add("vs-bad");
      } finally { test.disabled = false; }
    };
  }

  function sttProviders(ctx) { return (ctx.catalog && ctx.catalog.stt_api_providers) || STT_PRESETS.map(p => ({...p, base_url:p.url, models:[p.model], default_model:p.model})); }
  function sttProviderForUrl(url, ctx) {
    let host = ""; try { host = new URL(String(url || "").trim()).hostname.toLowerCase(); } catch (_) { return "custom"; }
    if (host === "api.groq.com") return "groq";
    if (host === "api.deepgram.com") return "deepgram";
    if (host === "api.mistral.ai") return "mistral";
    if (/\.(cognitiveservices\.azure\.com|api\.cognitive\.microsoft\.com|stt\.speech\.microsoft\.com)$/.test(host)) return "azure";
    return providerForUrl(url);
  }
  function sttPresetHtml(ctx) {
    const { K, esc, pref } = ctx, t = (ru,en) => ctx.en ? en : ru;
    const kind = sttProviderForUrl(pref("voice_stt_api_url", ""), ctx);
    const meta = sttProviders(ctx).find(p => p.id === kind) || {};
    return K.field({label:t("Сервис распознавания", "Recognition service"), forId:"st-stt-preset",
      help:t("Запись отправляется выбранному сервису. У каждого сервиса свой API-ключ; настройки сохраняются отдельно.", "The recording is sent to the selected service. Each service uses its own API key; settings are kept separately."),
      control:K.select("st-stt-preset", sttProviders(ctx).map(p=>[p.id, p.id === "custom" ? t("Другой OpenAI-совместимый API", "Other OpenAI-compatible API") : p.label]), kind) });
  }
  function sttApiHtml(ctx) {
    const {K,esc,pref} = ctx, t = (ru,en) => ctx.en ? en : ru;
    const kind = sttProviderForUrl(pref("voice_stt_api_url", ""),ctx);
    const meta = sttProviders(ctx).find(p=>p.id===kind) || {models:[]};
    const current = pref("voice_stt_api_model","") || meta.default_model || "";
    const mismatch = keyMismatch(kind,pref("voice_stt_api_key",""),ctx.en);
    const reuse = providerForUrl(pref("voice_tts_api_url","")) === kind && !!pref("voice_tts_api_key","") && kind !== "custom";
    return sttPresetHtml(ctx)
      + K.field({label:t("Адрес сервиса", "Service address"), forId:"st-stt-url",
        help:kind === "azure" ? t("Адрес вашего ресурса Azure Speech, например https://my-speech.cognitiveservices.azure.com", "Your Azure Speech resource address, e.g. https://my-speech.cognitiveservices.azure.com") : kind === "custom" ? t("Корень API: к нему добавляется /audio/transcriptions. Локальный сервер может работать без ключа.", "API root: /audio/transcriptions is appended. A local server may not require a key.") : t("Заполнен автоматически. Меняйте только для собственного шлюза.", "Filled automatically. Change only when using your own gateway."),
        control:`<input class="pinput sx-mono" id="st-stt-url" value="${esc(pref("voice_stt_api_url",meta.base_url || ""))}" spellcheck="false">`})
      + K.field({label:t("API-ключ", "API key") + (meta.key_url ? ` <a class="vs-key-link" href="${esc(meta.key_url)}" target="_blank" rel="noopener">${t("Где взять ключ →","Get a key →")}</a>` : ""), forId:"st-stt-key",
        help:`<span id="st-stt-key-warn" class="vs-warn" ${mismatch ? "" : "hidden"}>${esc(mismatch)}</span>`,
        control:K.secret("st-stt-key",pref("voice_stt_api_key","")) + (reuse ? `<button type="button" id="st-stt-reuse-key" class="pbtn ghost sx-btn-sm">${t("Использовать ключ озвучивания", "Use the speech synthesis key")}</button>` : "")})
      + (kind === "azure" ? K.note(t("Azure автоматически выбирает модель распознавания. При автоматическом языке включено определение языка сервисом.", "Azure chooses its recognition model automatically. Automatic language lets the service detect it.")) :
        K.field({label:t("Модель распознавания", "Recognition model"), forId:"st-stt-model",
          help:t("Выберите предложенную модель или введите другой ID. Модель чата MemuAI для распознавания не требуется.", "Choose a suggested model or enter another ID. Recognition does not require the MemuAI chat model."),
          control:pickControl(ctx,"st-stt-model",current,(meta.models || []).map(id=>({id,label:id})),meta.default_model || "whisper-1")}))
      + K.row({label:t("Проверить распознавание", "Test recognition"), desc:`<span id="st-stt-test-status" class="vs-status" role="status" aria-live="polite">${t("Выберите короткую аудиозапись. Облачный сервис может списать оплату за распознавание.", "Choose a short recording. Your cloud service may charge for transcription.")}</span>`,
        control:`<button type="button" id="st-stt-test" class="pbtn ghost sx-btn-sm">${t("Выбрать аудио…", "Choose audio…")}</button><input type="file" id="st-stt-test-file" accept="audio/*" hidden>`});
  }
  function sttSourceLabel(ctx) {
    if (ctx.pref("voice_stt_provider","local") !== "api") {
      const id = ctx.pref("voice_stt_local_model","small");
      return ((ctx.catalog.stt_models || []).find(m=>m.id===id) || {}).name || id;
    }
    const kind = sttProviderForUrl(ctx.pref("voice_stt_api_url",""),ctx);
    return ((sttProviders(ctx).find(p=>p.id===kind) || {}).label || "API") + (ctx.pref("voice_stt_api_model","") ? " · " + ctx.pref("voice_stt_api_model","") : "");
  }
  function bindStt(ctx) {
    const $ = sel => document.querySelector(sel), t = (ru,en) => ctx.en ? en : ru;
    const select = $("#st-stt-preset"); if (!select) return;
    const kind = sttProviderForUrl(ctx.pref("voice_stt_api_url",""),ctx);
    const read = () => ({url:$("#st-stt-url")?.value.trim() || "",key:$("#st-stt-key")?.value.trim() || "",
      model:$("#st-stt-model-pick") && $("#st-stt-model-pick").value !== CUSTOM ? $("#st-stt-model-pick").value : $("#st-stt-model")?.value.trim() || ""});
    const profilesFor = value => {
      const profiles = {...ctx.pref("voice_stt_api_profiles",{})};
      const saved = {url:ctx.pref("voice_stt_api_url",""),key:ctx.pref("voice_stt_api_key",""),model:ctx.pref("voice_stt_api_model","")};
      // A manual URL edit may switch services before the dropdown changes.
      // Preserve the last service's settings; pending fields belong to the
      // URL that will actually receive them.
      if (saved.url) profiles[sttProviderForUrl(saved.url,ctx)] = saved;
      profiles[sttProviderForUrl(value.url,ctx)] = value;
      return profiles;
    };
    const save = async () => {
      const value = read(), profiles = profilesFor(value);
      const result = await ctx.savePreferences({voice_stt_api_url:value.url,voice_stt_api_key:value.key,voice_stt_api_model:value.model,voice_stt_api_profiles:profiles});
      const warn = $("#st-stt-key-warn"); if (warn) { const text = keyMismatch(sttProviderForUrl(value.url,ctx),value.key,ctx.en); warn.textContent=text; warn.hidden=!text; }
      ctx.applyVoicePreferencesUI?.(); return result;
    };
    select.onchange = async () => {
      const previous = read(), next = select.value, meta = sttProviders(ctx).find(p=>p.id===next) || {};
      const profiles = profilesFor(previous);
      const value = profiles[next] || {url:meta.base_url || "",key:"",model:meta.default_model || ""};
      const result = await ctx.savePreferences({voice_stt_api_url:value.url,voice_stt_api_key:value.key,voice_stt_api_model:value.model,voice_stt_api_profiles:profiles});
      if (result === null) { select.value=kind; return; }
      ctx.rerender(); ctx.applyVoicePreferencesUI?.();
    };
    const pick = $("#st-stt-model-pick"), input = $("#st-stt-model");
    if (pick && input) pick.onchange = async () => { input.hidden=pick.value!==CUSTOM; if (pick.value===CUSTOM) {input.value="";input.focus();} else {input.value=pick.value;await save();} };
    if (input) input.onchange=save;
    if ($("#st-stt-key")) $("#st-stt-key").onchange=save;
    if ($("#st-stt-url")) $("#st-stt-url").onchange=async()=>{const result=await save();if(result!==null && sttProviderForUrl(read().url,ctx)!==kind)ctx.rerender();};
    if ($("#st-stt-reuse-key")) $("#st-stt-reuse-key").onclick=async()=>{$("#st-stt-key").value=ctx.pref("voice_tts_api_key","");await save();};
    const test=$("#st-stt-test"), file=$("#st-stt-test-file"), status=$("#st-stt-test-status");
    if (test && file) {
      test.onclick=()=>file.click();
      file.onchange=async()=>{
        const audio=file.files?.[0]; if(!audio)return;
        test.disabled=true;status.classList.remove("vs-ok","vs-bad");status.textContent=t("Распознаю запись…", "Transcribing recording…");
        try {
          if(audio.size>25*1024*1024)throw new Error(t("Выберите запись меньше 25 МБ", "Choose a recording smaller than 25 MB"));
          if(await save()===null)throw new Error(t("Сначала сохраните настройки", "Save the settings first"));
          const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(audio);});
          const result=await ctx.api("/api/voice/transcribe",{method:"POST",body:{audio:data,raw:true}});
          status.textContent=result.text;status.classList.add("vs-ok");
        }catch(error){status.textContent=error.message || t("Не удалось распознать запись", "Transcription failed");status.classList.add("vs-bad");}
        finally{test.disabled=false;file.value="";}
      };
    }
  }
  function downloadLabel(row,en) {
    const t=(ru,eng)=>en?eng:ru, mb=value=>(Number(value||0)/1048576).toFixed(1);
    if(row.stage==="preparing")return t("Проверяю файлы…", "Checking files…");
    if(row.stage==="installing")return t("Устанавливаю…", "Installing…");
    let text=t("Скачивание", "Downloading");
    if(typeof row.progress==="number")text+=" "+Math.round(row.progress)+"%";
    if(row.total_bytes)text+=" · "+mb(row.bytes_downloaded)+" / "+mb(row.total_bytes)+t(" МБ", " MB");
    else if(row.bytes_downloaded)text+=" · "+mb(row.bytes_downloaded)+t(" МБ", " MB");
    return text;
  }
  function downloadHtml(ctx,row,kind) {
    const label=downloadLabel(row,ctx.en), key=kind+":"+row.id;
    return `<div class="vs-download" data-voice-job="${ctx.esc(key)}"><span data-voice-progress-label role="status" aria-live="polite">${ctx.esc(label)}</span><progress max="100" ${typeof row.progress === "number" ? `value="${row.progress}"` : ""} aria-label="${ctx.esc(label)}"></progress><button type="button" data-voice-cancel="${ctx.esc(row.id)}" data-voice-kind="${kind}" aria-label="${ctx.en ? "Cancel download" : "Отменить скачивание"}">${ctx.en ? "Cancel" : "Отменить"}</button></div>`;
  }
  function updateDownloads(catalog,en) {
    for(const kind of ["stt","tts"])for(const row of catalog[kind+"_models"] || []) {
      const node=document.querySelector(`[data-voice-job="${CSS.escape(kind+":"+row.id)}"]`);if(!node)continue;
      const label=downloadLabel(row,en);node.querySelector("[data-voice-progress-label]").textContent=label;
      const progress=node.querySelector("progress");if(typeof row.progress === "number")progress.value=row.progress;else progress.removeAttribute("value");
      progress.setAttribute("aria-label",label);
    }
  }

  window.MemuVoiceSettings = { ttsHtml, bindTts, sttPresetHtml, sttApiHtml, bindStt, sourceLabel, providerForUrl, sttProviderForUrl, sttSourceLabel, downloadHtml, updateDownloads };
})();
