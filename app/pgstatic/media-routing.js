

"use strict";
const MemuMedia = (() => {
  const VERSION = "1.0-r29";
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const VIDEO_SVG = '<svg viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10.5 5-3v9l-5-3z"/></svg>';
  const WAND_SVG = '<svg viewBox="0 0 24 24"><path d="m4 20 11-11"/><path d="m14 4 .7 2.3L17 7l-2.3.7L14 10l-.7-2.3L11 7l2.3-.7z"/><path d="M19 12l.5 1.5L21 14l-1.5.5L19 16l-.5-1.5L17 14l1.5-.5z"/></svg>';

  function ensureIcons() {
    if (typeof ICONS === "undefined") return;
    if (!ICONS.video) ICONS.video = VIDEO_SVG;
    if (!ICONS.wand) ICONS.wand = WAND_SVG;
  }
  document.addEventListener("DOMContentLoaded", ensureIcons);

  function promptBlock(context = {}) {
    const image = available("image"), video = available("video");
    if (!image && !video) {
      return "<media_generation>MemuAI has no image or video generation model configured right now. Never claim that you created, "
        + "drew or attached a picture or video. If the user asks for one, say briefly that a generation model can be chosen in "
        + "Settings → Models (Images, video and decisions) and help with a text answer meanwhile.</media_generation>";
    }
    const kinds = [image ? "images" : "", video ? "video" : ""].filter(Boolean).join(" and ");
    const blocks = [image ? "```memuai-image" : "", video ? "```memuai-video" : ""].filter(Boolean).join(" or ");
    let text = `<media_generation>MemuAI can generate ${kinds} for this chat. When the user's message genuinely asks for a NEW `
      + `${image && video ? "picture or video" : image ? "picture" : "video"} (draw, generate, create, show what something looks like`
      + `${video ? ", animate, make a clip" : ""}), write one short sentence in the user's language about what you are creating `
      + `and append one hidden ${blocks} block containing JSON {"prompt": "..."}. MemuAI generates it and shows it under your message.\n`
      + "The prompt: one self-contained paragraph in English that keeps everything the user specified (subject, details, style, "
      + "mood, format, text to render — quoted, in the user's language) and resolves references to the conversation. "
      + "Describe what is needed concretely; no generic quality words (8k, masterpiece, ultra detailed).\n"
      + "Do NOT generate for: tables, charts, graphs, diagrams, schemes or code (answer those as text — MemuAI renders them), "
      + "questions about how generation works, analysing or describing attached images, finding existing videos. "
      + "At most one block per answer; never say the result is ready or describe it as already made.";
    if (!image) text += " Image generation is not configured: if asked for a picture, say it can be enabled in Settings → Models.";
    if (!video) text += " Video generation is not configured: if asked for a video, say it can be enabled in Settings → Models.";
    if (context && (context.mode === "work" || context.mode === "code")) text += " In Work, generate only assets the project really needs.";
    return text + "</media_generation>";
  }

  const imageList = () => (S.imageModels || []).slice();
  const videoList = () => (S.videoModels || []).slice();
  const listOf = (kind) => (kind === "video" ? videoList() : imageList());
  function kindOf(model) {
    if (!model) return "";
    if ((S.videoModels || []).includes(model)) return "video";
    if ((S.imageModels || []).includes(model) || String(model).startsWith("genspark-image/")) return "image";
    return "";
  }
  function prefKey(kind) { return kind === "video" ? "media_video_model" : "media_image_model"; }

  function pickModel(kind, { explicit = false } = {}) {
    const list = listOf(kind);
    const chosen = String(pref(prefKey(kind), "") || "");
    if (chosen === "off" && !explicit) return "";
    if (chosen && chosen !== "off" && list.includes(chosen)) return chosen;
    if (chosen && chosen !== "off" && !list.includes(chosen) && !explicit) return "";
    const pinned = pref("pinned_models", []) || [];
    return pinned.find((id) => list.includes(id)) || list[0] || "";
  }
  function available(kind) { return !!pickModel(kind); }
  function missingText(kind) {
    const list = listOf(kind), chosen = String(pref(prefKey(kind), "") || "");
    const what = kind === "video" ? t("видео", "video") : t("изображений", "images");
    if (chosen === "off") return kind === "video"
      ? t("Генерация видео выключена в Настройки → Модели.", "Video generation is turned off in Settings → Models.")
      : t("Генерация изображений выключена в Настройки → Модели.", "Image generation is turned off in Settings → Models.");
    if (chosen && !list.includes(chosen)) return t(`Выбранная модель ${what} (${chosen}) сейчас недоступна — выберите другую в Настройки → Модели.`,
      `The chosen ${kind === "video" ? "video" : "image"} model (${chosen}) is unavailable now — pick another one in Settings → Models.`);
    return kind === "video"
      ? t("Нет модели генерации видео. Добавьте в MemuAI Proxy аккаунт Genspark (AI Video) или своё подключение с моделью видео (Sora-подобный /videos, Veo в Google AI Studio).",
        "No video generation model. Add a Genspark account (AI Video) or a custom connection with a video model (Sora-style /videos, Veo in Google AI Studio) in MemuAI Proxy.")
      : t("Нет модели генерации изображений. Добавьте в MemuAI Proxy аккаунт Genspark (AI Image) или своё подключение с моделью картинок (gpt-image, FLUX, Imagen, Gemini image…).",
        "No image generation model. Add a Genspark account (AI Image) or a custom connection with an image model (gpt-image, FLUX, Imagen, Gemini image…) in MemuAI Proxy.");
  }
  function textModels() {
    return (S.models || []).filter((m) => !isImageModel(m.id) && !(typeof isMusicOnlyModel === "function" && isMusicOnlyModel(m.id)));
  }
  function decisionModel() {
    const id = String(pref("decision_model", "") || "");
    return id && textModels().some((m) => m.id === id) ? id : "";
  }

  function thinkingModel() {
    const current = typeof currentModel === "function" ? currentModel() : "";
    if (current && !isImageModel(current)) return current;
    const preferred = pref("default_model", "");
    if (preferred && textModels().some((m) => m.id === preferred)) return preferred;
    return textModels()[0]?.id || "";
  }
  const FAST = /flash|mini|nano|lite|haiku|small|air|turbo|instant|tiny|8b|7b|3b|1\.5b|fast|speed|highspeed/i;

  function plain(text) {
    const value = typeof visibleAnswer === "function" ? visibleAnswer(String(text || "")) : String(text || "");
    return value.replace(/```[\s\S]*?```/g, " ").replace(/\s+/g, " ").trim();
  }
  function contextRows(messages, upto) {
    const rows = [];
    const list = (messages || []).slice(0, upto === undefined ? undefined : upto);
    for (const m of list.slice(-8)) {
      if (!m || m.internal || (m.role !== "user" && m.role !== "assistant")) continue;
      let text = m.role === "assistant" ? plain(m.content) : String(m.content || "");
      if (m.role === "assistant" && m.media?.prompt) text = (text ? text + " " : "") + `[${m.media.kind === "video" ? "video" : "image"} generated: ${m.media.prompt}]`;
      if (text.trim()) rows.push({ role: m.role, text: text.trim().slice(0, 700) });
    }
    return rows.slice(-6);
  }
  function previousPrompt(messages, upto) {
    const list = (messages || []).slice(0, upto === undefined ? undefined : upto);
    for (let i = list.length - 1, seen = 0; i >= 0 && seen < 4; i--, seen++) {
      const m = list[i];
      if (m?.role === "assistant" && m.media?.prompt && m.media.status === "done") return m.media.prompt;
    }
    return "";
  }

  function detectAspect(text, kind) {
    const value = String(text || "").toLowerCase();
    const ratio = value.match(/\b(16:9|9:16|1:1|4:3|3:4|3:2|2:3|21:9)\b/);
    if (ratio) return ratio[1];
    if (/вертикал|portrait|vertical|для (?:сторис|stories|reels|tiktok|тикток)|\bstories\b|\breels\b|\btiktok\b/.test(value)) return kind === "video" ? "9:16" : "3:4";
    if (/квадрат|square|аватар|avatar|иконк|icon\b|логотип|\blogo\b/.test(value)) return "1:1";
    if (/горизонтал|landscape|широкоформат|widescreen|panoram|панорам|обои для (?:рабочего|пк|компьютера)|desktop wallpaper/.test(value)) return "16:9";
    return kind === "video" ? "16:9" : (localStorage.getItem("memu_image_aspect") || "auto");
  }
  function busy() { return typeof generationJobs !== "undefined" && generationJobs.has(S.currentId); }
  function save(chatId, messages) {
    if (S.currentId === chatId && S.messages === messages) persistChat();
    else if (typeof persistChatSnapshot === "function") persistChatSnapshot(chatId, messages);
  }
  function rerender(messages) { if (S.messages === messages) renderMessages(); }
  function setDeciding(on) {
    const btn = document.getElementById("btn-send");
    if (btn) btn.classList.toggle("media-deciding", !!on);
  }
  function openModelsSettings() {
    try {
      if (typeof openSettingsPage === "function" && document.querySelector("#pane-models")) return openSettingsPage("models", { anchor: "#settings-media-models" });
      currentSettingsTab = "models"; showSettings(true);
      setTimeout(() => document.getElementById("settings-media-models")?.scrollIntoView({ block: "start" }), 250);
    } catch (e) {}
  }
  function toastMissing(kind) {
    toast(missingText(kind), "warn", { label: t("Настроить", "Set up"), run: openModelsSettings });
  }

  async function readJson(resp) { return resp.json().catch(() => ({})); }

  async function autoprompt(assistant, { kind, request, rows, previous, signal }) {
    const model = thinkingModel();
    if (!model) return "";
    const media = assistant.media;
    media.status = "prompting";
    const resp = await fetch("/api/pg/media/autoprompt", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ kind, request, model, context: rows, previous_prompt: previous,
        search: pref("media_autoprompt_search", true) !== false, lang: PG_LANG }),
    });
    const data = await readJson(resp);
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    media.autoprompted = true; media.promptModel = model;
    media.searched = data.searched || ""; media.sources = (data.sources || []).slice(0, 5);
    return String(data.prompt || "").trim();
  }

  async function produce(assistant, opts, signal, messages) {
    const media = assistant.media;
    const kind = media.kind;
    const rows = contextRows(messages, messages.indexOf(assistant));
    const previous = opts.previous !== undefined ? opts.previous : previousPrompt(messages, messages.indexOf(assistant));
    let prompt = opts.prompt || "";
    media.promptNote = "";
    if (!prompt && opts.fromModel) prompt = String(opts.request || "").trim();
    if (pref("media_autoprompt", false) && !opts.skipAutoprompt) {
      rerender(messages);
      try {
        const better = await autoprompt(assistant, { kind, request: opts.request, rows, previous, signal });
        if (better) prompt = better;
      } catch (error) {
        if (error.name === "AbortError") throw error;
        media.promptNote = t("Не удалось улучшить промпт — использую исходный: ", "Could not improve the prompt — using the original: ") + (error.message || error);
      }
    }
    media.status = "generating"; media.genStartedAt = Date.now();
    rerender(messages);
    const images = (opts.refs || []).filter((a) => String(a.data || "").startsWith("data:image")).map((a) => a.data);
    const resp = await fetch("/api/pg/media/generate", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify({ kind, model: media.model, prompt, request: prompt ? "" : opts.request, previous_prompt: previous,
        context: rows, images, aspect: media.aspect || detectAspect(opts.request || prompt, kind), lang: PG_LANG }),
    });
    if (!resp.ok || !resp.body) {
      const failure = await readJson(resp);
      throw new Error((PG_LANG === "en" && failure.error_en) || failure.error || `HTTP ${resp.status}`);
    }
    let urls = null, sawDone = false;
    for await (const data of readSSE(resp.body, { idleMs: 180000 })) {
      if (data === "[DONE]") { sawDone = true; continue; }
      let item; try { item = JSON.parse(data); } catch (e) { continue; }
      if (item.status === "prompt" && item.prompt) { media.prompt = String(item.prompt); rerender(messages); }
      else if (item.status === "done") urls = (item.urls || []).filter(Boolean);
      else if (item.status === "error" || item.error) {
        throw new Error(typeof item.error === "object" ? sseErrorText(item.error) : (item.error || t("Генерация не удалась", "Generation failed")));
      }
    }
    if (!urls || !sawDone) throw new Error(t("Поток генерации неожиданно прервался", "The generation stream ended unexpectedly"));
    if (!urls.length) throw new Error(t("Модель не вернула результат", "The model returned no result"));
    media.history = [...(media.history || []), { prompt: media.prompt, urls, at: Date.now() }].slice(-12);
    if (kind === "video") assistant.videos = [...(opts.append ? assistant.videos || [] : []), ...urls];
    else assistant.images = [...(opts.append ? (assistant.images || []).filter((u) => u !== "__skeleton__") : []), ...urls];
    media.status = "done";
  }

  function newAssistant(kind, model, request, source) {
    return { role: "assistant", content: "", streaming: true, model, createdAt: Date.now(),
      images: [], videos: [], generationId: crypto.randomUUID().slice(0, 10),
      media: { kind, model, request, source, status: "starting", startedAt: Date.now(), aspect: detectAspect(request, kind) } };
  }

  async function runJob(chatId, messages, assistant, opts) {
    const job = { controller: new AbortController(), image: true, media: true };
    generationJobs.set(chatId, job);
    if (S.currentId === chatId) controller = job.controller;
    generating = true; setSendBusy(true);
    const t0 = performance.now();
    const timer = setInterval(() => {
      const el = document.querySelector(`[data-media-elapsed="${assistant.generationId}"]`);
      if (el) el.textContent = String(Math.floor((performance.now() - t0) / 1000));
    }, 1000);
    try {
      await produce(assistant, opts, job.controller.signal, messages);
      assistant.error = "";
    } catch (error) {
      const media = assistant.media;
      if (error.name === "AbortError") { media.status = "stopped"; assistant.error = ""; }
      else { media.status = "error"; assistant.error = String(error.message || error); }
    } finally {
      clearInterval(timer);
      assistant.streaming = false;
      assistant.ms = Math.round(performance.now() - t0);
      if (generationJobs.get(chatId) === job) generationJobs.delete(chatId);
      if (controller === job.controller) controller = null;
      generating = generationJobs.size > 0; setSendBusy(false);
      rerender(messages); save(chatId, messages);
    }
  }

  async function start(kind, request, { refs = [], model = "", source = "command", userText } = {}) {
    if (busy()) { toast(t("Дождитесь текущего ответа или нажмите «Стоп»", "Wait for the current answer or press Stop"), "warn"); return false; }
    const chosen = model || pickModel(kind, { explicit: source === "command" });
    if (!chosen) { toastMissing(kind); return false; }
    const chatId = S.currentId, messages = S.messages;
    const user = { role: "user", content: userText !== undefined ? userText : request, attachments: refs, createdAt: Date.now() };
    if (source === "command") user.mediaCommand = kind;
    const assistant = newAssistant(kind, chosen, request, source);
    messages.push(user, assistant);
    const input = document.getElementById("input");
    if (input) { input.value = ""; input.dataset.imagine = ""; input.style.height = "auto"; input.placeholder = pt("message"); }
    document.getElementById("btn-send")?.classList.remove("image-mode");
    renderMessages(); persistChat();
    await runJob(chatId, messages, assistant, { request, refs });
    return true;
  }

  function takeAttachments() {
    const refs = (typeof attachments !== "undefined" ? attachments : []).filter((a) => !a.isVideo).map((a) => ({ name: a.name, data: a.data }));
    if (typeof clearAttachments === "function") clearAttachments();
    return refs;
  }

  async function command(kind, text) {
    if (busy()) {
      const input = document.getElementById("input");
      if (input && !input.value) input.value = `/${kind} ${text}`;
      toast(t("Дождитесь текущего ответа или нажмите «Стоп»", "Wait for the current answer or press Stop"), "warn");
      return false;
    }
    if (typeof isProjectMode === "function" && S.mode === "code") {
      toast(t("Генерация картинок и видео — в обычном чате и Work", "Images and video are generated in a chat or Work"), "warn");
      return false;
    }
    return start(kind, text, { refs: takeAttachments(), source: "command" });
  }

  async function direct(kind, text, refs = [], { model = "" } = {}) {
    return start(kind, text, { refs, model: model && kindOf(model) === kind ? model : "", source: "command" });
  }

  function insertCommand(kind) {
    const input = document.getElementById("input");
    if (!input) return;
    if (!pickModel(kind, { explicit: true })) { toastMissing(kind); return; }
    if (input.dataset.imagine && typeof setImagine === "function") setImagine(false);
    const rest = String(input.value || "").replace(/^\/(?:image|img|video)\b\s*/i, "");
    input.value = `/${kind} ` + rest;
    input.dispatchEvent(new Event("input"));
    const slash = document.getElementById("slash-menu");
    if (slash) slash.hidden = true;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  async function route(text, model) {
    try {
      const deciding = decisionModel();
      if (!deciding || !text) return false;
      if (typeof isProjectMode === "function" && isProjectMode()) return false;
      if (S.chatCaps?.deep_research) return false;
      const atts = typeof attachments !== "undefined" ? attachments : [];
      if (atts.some((a) => a.isVideo || a.pending)) return false;
      const image = available("image"), video = available("video");
      if (!image && !video) return false;
      setDeciding(true);
      let result;
      try {
        result = await api("/api/pg/media/decide", { method: "POST", body: {
          text, model: deciding, context: contextRows(S.messages), attachments: atts.length, image, video, lang: PG_LANG,
          after_media: !!S.messages.slice(-1)[0]?.media && S.messages.slice(-1)[0].media.status === "done" } });
      } finally { setDeciding(false); }
      S.lastMediaDecision = result;
      const kind = result?.kind;
      if ((kind === "image" && image) || (kind === "video" && video)) {
        const refs = takeAttachments();
        return await start(kind, text, { refs, source: result.source === "model" ? "decision" : "heuristic" });
      }
    } catch (error) {
      console.warn("MemuMedia.route", error);
      setDeciding(false);
    }
    return false;
  }
  async function retry(index, userIndex, refs, chosen = "") {
    if (busy()) return toast(t("Ответ уже генерируется", "A response is already generating"), "warn");
    const old = S.messages[index], user = S.messages[userIndex];
    const current = typeof currentModel === "function" ? currentModel() : "";
    const kind = old?.media?.kind || kindOf(current) || "image";

    const wanted = chosen || old?.media?.retryModel || "";
    const model = wanted && listOf(kind).includes(wanted) ? wanted
      : kindOf(current) === kind ? current : pickModel(kind, { explicit: true });
    if (!model) return toastMissing(kind);
    const request = old?.media?.request || user?.content || "";
    const fromModel = old?.media?.source === "model";
    if (typeof MemuBranches !== "undefined") MemuBranches.keepVersion(userIndex + 1);
    S.messages = S.messages.slice(0, userIndex + 1);
    const chat = (S.chats || []).find((item) => item.id === S.currentId);
    if (chat) chat.messages = S.messages;
    const assistant = newAssistant(kind, model, request, old?.media?.source || "command");
    if (old?.content && fromModel) assistant.content = old.content;
    S.messages.push(assistant);
    renderMessages(); persistChat();
    const usable = (refs || user?.attachments || []).map((a) => ({ name: a.name, data: a.data }));
    await runJob(S.currentId, S.messages, assistant, { request, refs: usable, fromModel });
  }

  async function again(index, prompt) {
    if (busy()) return toast(t("Дождитесь текущего ответа или нажмите «Стоп»", "Wait for the current answer or press Stop"), "warn");
    const assistant = S.messages[index];
    if (!assistant?.media) return;
    const kind = assistant.media.kind;
    const model = listOf(kind).includes(assistant.media.model) ? assistant.media.model : pickModel(kind, { explicit: true });
    if (!model) return toastMissing(kind);
    const user = S.messages[index - 1]?.role === "user" ? S.messages[index - 1] : null;
    const edited = prompt !== undefined;
    const finalPrompt = String(edited ? prompt : assistant.media.prompt || assistant.media.request || "").trim();
    if (!finalPrompt) return;
    Object.assign(assistant.media, { model, status: "starting", startedAt: Date.now(), prompt: finalPrompt, edited: edited || assistant.media.edited });
    assistant.model = model; assistant.streaming = true; assistant.error = "";
    renderMessages();
    await runJob(S.currentId, S.messages, assistant, { request: finalPrompt, prompt: finalPrompt, skipAutoprompt: true, append: true,
      refs: (user?.attachments || []).map((a) => ({ name: a.name, data: a.data })) });
  }

  function extract(text) {
    let media = null;
    const cleaned = String(text || "").replace(/```memuai-(image|video)\s*([\s\S]*?)```/gi, (_, kind, raw) => {
      let prompt = "";
      try { prompt = String(JSON.parse(raw.trim()).prompt || "").trim().slice(0, 6000); }
      catch (e) { prompt = String(raw || "").replace(/^\s*\{?\s*"?prompt"?\s*:\s*"?|"?\s*\}?\s*$/g, "").trim().slice(0, 6000); }
      if (prompt && !media) media = { kind: kind.toLowerCase(), prompt };
      return "";
    }).trim();
    return { text: cleaned, prompt: media && media.kind === "image" ? media.prompt : "", media };
  }
  async function runForAssistant(assistant, action, signal, chatState) {
    const kind = action.kind === "video" ? "video" : "image";
    const model = pickModel(kind);
    const messages = (chatState && chatState.messages) || S.messages;
    if (!model) {
      assistant.media = { kind, model: "", request: action.prompt, source: "model", status: "error", startedAt: Date.now() };
      assistant.error = missingText(kind);
      return;
    }
    const user = [...messages.slice(0, Math.max(0, messages.indexOf(assistant)))].reverse().find((m) => m.role === "user");
    assistant.media = { kind, model, request: user?.content || action.prompt, source: "model", status: "starting",
      startedAt: Date.now(), aspect: detectAspect(user?.content || action.prompt, kind) };
    assistant.generationId = assistant.generationId || crypto.randomUUID().slice(0, 10);
    rerender(messages);
    const t0 = performance.now();
    const timer = setInterval(() => {
      const el = document.querySelector(`[data-media-elapsed="${assistant.generationId}"]`);
      if (el) el.textContent = String(Math.floor((performance.now() - t0) / 1000));
    }, 1000);
    try {

      await produce(assistant, { request: action.prompt, fromModel: true, prompt: pref("media_autoprompt", false) ? "" : action.prompt,
        refs: (user?.attachments || []).map((a) => ({ name: a.name, data: a.data })) }, signal, messages);
    } catch (error) {
      if (error.name === "AbortError") { assistant.media.status = "stopped"; throw error; }
      assistant.media.status = "error";
      assistant.error = String(error.message || error);
    } finally { clearInterval(timer); }
  }
  function historyNote(m) {
    if (!m || m.role !== "assistant" || !m.media) return "";
    const kind = m.media.kind === "video" ? "video" : "image";
    if (m.media.status !== "done") return m.media.status === "error" ? `\n[${kind} generation failed]` : "";
    const count = kind === "video" ? (m.videos || []).length : (m.images || []).length;
    return `\n[Generated ${kind}${count > 1 ? ` ×${count}` : ""} with prompt: ${String(m.media.prompt || m.media.request || "").slice(0, 600)}]`;
  }

  function sourceLabel(media) {
    return media.source === "decision" ? t("выбрано моделью решений", "picked by the Decisions model")
      : media.source === "heuristic" ? t("распознано по запросу", "recognised from the request")
      : media.source === "model" ? t("решила модель чата", "decided by the chat model")
      : media.source === "command" ? t("по команде", "by command") : "";
  }
  function videoUrl(url) {
    const value = String(url || "");
    return /^(\/api\/pg\/media\/file\/|https:\/\/|data:video\/|blob:)/.test(value) ? value : "";
  }
  function inlineHtml(m, i) {
    const media = m?.media;
    if (!media || m.role !== "assistant") return "";
    ensureIcons();
    const kind = media.kind === "video" ? "video" : "image";
    const running = m.streaming && ["starting", "prompting", "generating"].includes(media.status);
    const kindLabel = kind === "video" ? t("Видео", "Video") : t("Изображение", "Image");
    const elapsed = Math.floor((Date.now() - (media.startedAt || Date.now())) / 1000);
    let html = "";
    if (running) {
      const step = media.status === "prompting"
        ? (media.searched ? t("Ищу, как это выглядит: ", "Looking up how it looks: ") + esc(media.searched) : t("Продумываю промпт…", "Thinking the prompt through…"))
        : kind === "video" ? t("Создаю видео — обычно это занимает 1–5 минут", "Creating the video — this usually takes 1–5 minutes") : t("Создаю изображение…", "Creating the image…");
      html += `<div class="mm-progress mm-${kind}${media.status === "prompting" ? " mm-prompting" : ""}" role="status">
        <div class="mm-shimmer" aria-hidden="true">${icon(kind === "video" ? "video" : media.status === "prompting" ? "wand" : "image")}</div>
        <div class="mm-progress-text"><b>${step}</b><small>${esc(modelDisplayName(media.model || ""))} · <span data-media-elapsed="${esc(m.generationId || "")}">${elapsed}</span> ${t("с", "s")}</small></div>
        <button type="button" class="mm-btn ghost" data-media-stop="${i}">${icon("stop")}<span>${t("Остановить", "Stop")}</span></button></div>`;
    }
    const videos = (m.videos || []).map(videoUrl).filter(Boolean);
    if (videos.length) {
      html += `<div class="mm-videos">${videos.map((url, n) => `<figure class="mm-video">
        <video src="${esc(url)}" controls preload="metadata" playsinline loop data-media-video="${i}:${n}"></video>
        <figcaption><button type="button" class="mm-btn ghost" data-media-download="${i}:${n}">${icon("download")}<span>${t("Скачать", "Download")}</span></button></figcaption></figure>`).join("")}</div>`;
    }
    if (media.status === "stopped" && !m.streaming) {
      html += `<div class="mm-note">${t("Генерация остановлена. Если сервис уже начал работу, она могла быть оплачена.", "Generation stopped. If the service had already started, it may still have been charged.")}</div>`;
    }
    if (media.promptNote) html += `<div class="mm-note warn">${esc(media.promptNote)}</div>`;
    const prompt = String(media.prompt || "");
    if (!m.streaming || prompt) {
      const sources = (media.sources || []).filter((s) => /^https?:\/\//.test(s.url || ""));
      const versions = (media.history || []).length;
      html += `<details class="mm-card"${media.editing ? " open" : ""}>
        <summary>${icon(media.autoprompted ? "wand" : kind === "video" ? "video" : "image")}<span class="mm-card-title">${media.autoprompted ? t("Улучшенный промпт", "Enhanced prompt") : t("Промпт", "Prompt")}</span>
          <span class="mm-card-meta">${esc(kindLabel)} · ${esc(modelDisplayName(media.model || ""))}${sourceLabel(media) ? " · " + esc(sourceLabel(media)) : ""}${versions > 1 ? " · " + versions + " " + t("версии", "versions") : ""}</span></summary>
        ${media.editing ? `<textarea class="ptexta mm-edit" data-media-edit="${i}" rows="5">${esc(prompt || media.request || "")}</textarea>
          <div class="mm-actions"><button type="button" class="mm-btn primary" data-media-regen="${i}">${icon("retry")}<span>${t("Создать заново", "Regenerate")}</span></button>
          <button type="button" class="mm-btn ghost" data-media-cancel-edit="${i}">${t("Отмена", "Cancel")}</button></div>`
        : `<div class="mm-prompt">${esc(prompt || media.request || "")}</div>
          ${media.searched ? `<div class="mm-search">${icon("globe")}<span>${t("Поиск: ", "Searched: ")}${esc(media.searched)}</span>${sources.length ? ` · ${sources.slice(0, 3).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc((() => { try { return new URL(s.url).hostname.replace(/^www\./, ""); } catch (e) { return "link"; } })())}</a>`).join(", ")}` : ""}</div>` : ""}
          ${m.streaming ? "" : `<div class="mm-actions">
            <button type="button" class="mm-btn" data-media-edit-open="${i}">${icon("edit")}<span>${t("Изменить и создать заново", "Edit and regenerate")}</span></button>
            ${media.status === "done" ? `<button type="button" class="mm-btn" data-media-variation="${i}">${icon("sparkle")}<span>${t("Ещё вариант", "Another variation")}</span></button>` : ""}
            <button type="button" class="mm-btn ghost" data-media-copy="${i}">${icon("copy")}<span>${t("Копировать", "Copy")}</span></button></div>`}`}
      </details>`;
    }
    if (media.status === "error" && !m.streaming && /Settings → Models|Настройки → Модели|MemuAI Proxy/.test(m.error || "")
      && !listOf(media.kind === "video" ? "video" : "image").length) {
      html += `<div class="mm-actions"><button type="button" class="mm-btn" data-media-settings>${icon("settings")}<span>${t("Открыть настройки моделей", "Open model settings")}</span></button></div>`;
    }
    return `<div class="mm-block" data-media-block="${i}">${html}</div>`;
  }

  function retryHtml(m, i) {
    const media = m?.media;
    if (!media || m.role !== "assistant" || media.status !== "error" || m.streaming) return "";
    const kind = media.kind === "video" ? "video" : "image";
    if (!listOf(kind).length) return "";
    const choice = media.retryModel && listOf(kind).includes(media.retryModel) ? media.retryModel : media.model;
    return `<div class="mm-retry"><span class="mm-retry-label">${t("Другая модель", "Another model")}</span>
        <span class="mmp-select"><button type="button" class="mmp-trigger" data-media-retry-pick="${i}" aria-haspopup="dialog" title="${esc(choice || "")}">${retryTrigger(choice)}</button></span>
        <button type="button" class="mm-btn primary" data-media-retry="${i}">${icon("retry")}<span>${choice && choice !== media.model ? t("Повторить с этой моделью", "Try again with this model") : t("Повторить", "Try again")}</span></button>${/Settings → Models|Настройки → Модели|MemuAI Proxy/.test(m.error || "")
          ? `<button type="button" class="mm-btn" data-media-settings>${icon("settings")}<span>${t("Открыть настройки моделей", "Open model settings")}</span></button>` : ""}</div>`;
  }
  function retryTrigger(id) {
    const P = window.MemuModelPicker;
    if (!P || !id) return `<span class="mmp-text"><span class="mmp-name">${esc(id ? modelDisplayName(id) : t("Выберите модель", "Pick a model"))}</span></span>`;
    const row = P.find(id) || { id, caps: {} };
    return `${P.monogram(row)}<span class="mmp-text"><span class="mmp-name">${esc(P.name(id))}</span><span class="mmp-id">${esc(id)}</span></span>${P.svg("chev", "mmp-caret")}`;
  }
  function userIndexBefore(index) {
    let u = index - 1;
    while (u >= 0 && S.messages[u]?.role !== "user") u--;
    return u;
  }
  function hydrate(box, roots) {
    const at = (el, name) => S.messages[+el.dataset[name]];

    const scope = roots ? { querySelectorAll: (sel) => roots.flatMap((node) => [...node.querySelectorAll(sel)]) } : box;

    scope.querySelectorAll("[data-media-retry-pick]").forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      const index = +b.dataset.mediaRetryPick, m = S.messages[index];
      if (!m?.media || !window.MemuModelPicker) return;
      const kind = m.media.kind === "video" ? "video" : "image";
      const picked = await MemuModelPicker.open(b, { kind, ids: listOf(kind), value: m.media.retryModel || m.media.model,
        title: kind === "video" ? t("Модель для видео", "Video model") : t("Модель для изображений", "Image model") });
      if (!picked || !S.messages[index]?.media) return;
      S.messages[index].media.retryModel = picked;
      renderMessages();
    });
    scope.querySelectorAll("[data-media-retry]").forEach((b) => b.onclick = (e) => {
      e.stopPropagation();
      const index = +b.dataset.mediaRetry, m = S.messages[index];
      if (!m?.media) return;
      const userIndex = userIndexBefore(index);
      if (userIndex < 0) return;
      const user = S.messages[userIndex];
      retry(index, userIndex, (user.attachments || []).map((a) => ({ ...a })), m.media.retryModel || m.media.model);
    });
    scope.querySelectorAll("[data-media-stop]").forEach((b) => b.onclick = () => {
      const job = generationJobs.get(S.currentId);
      if (job?.controller) job.controller.abort();
    });
    scope.querySelectorAll("[data-media-variation]").forEach((b) => b.onclick = () => again(+b.dataset.mediaVariation));
    scope.querySelectorAll("[data-media-edit-open]").forEach((b) => b.onclick = () => {
      const m = at(b, "mediaEditOpen"); if (!m?.media) return;
      m.media.editing = true; renderMessages();
      setTimeout(() => { const area = box.querySelector(`[data-media-edit="${b.dataset.mediaEditOpen}"]`); if (area) { area.focus(); area.setSelectionRange(area.value.length, area.value.length); } }, 20);
    });
    scope.querySelectorAll("[data-media-cancel-edit]").forEach((b) => b.onclick = () => {
      const m = at(b, "mediaCancelEdit"); if (m?.media) { m.media.editing = false; renderMessages(); }
    });
    scope.querySelectorAll("[data-media-edit]").forEach((area) => area.onkeydown = (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); box.querySelector(`[data-media-regen="${area.dataset.mediaEdit}"]`)?.click(); }
      if (e.key === "Escape") { e.stopPropagation(); box.querySelector(`[data-media-cancel-edit="${area.dataset.mediaEdit}"]`)?.click(); }
    });
    scope.querySelectorAll("[data-media-regen]").forEach((b) => b.onclick = () => {
      const index = +b.dataset.mediaRegen, m = S.messages[index];
      const area = box.querySelector(`[data-media-edit="${index}"]`);
      const value = String(area?.value || "").trim();
      if (!m?.media || !value) return;
      m.media.editing = false;
      again(index, value);
    });
    scope.querySelectorAll("[data-media-copy]").forEach((b) => b.onclick = async () => {
      const m = at(b, "mediaCopy");
      try { await navigator.clipboard.writeText(String(m?.media?.prompt || m?.media?.request || "")); toast(t("Промпт скопирован", "Prompt copied")); }
      catch (e) { toast(t("Не удалось скопировать", "Could not copy"), "warn"); }
    });
    scope.querySelectorAll("[data-media-download]").forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      const [mi, vi] = String(b.dataset.mediaDownload).split(":").map(Number);
      const url = (S.messages[mi]?.videos || [])[vi];
      if (url) await downloadVideo(url);
    });
    scope.querySelectorAll("[data-media-settings]").forEach((b) => b.onclick = openModelsSettings);
  }
  async function downloadVideo(url) {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const ext = (String(url).match(/\.(mp4|webm|mov)(?:[?#]|$)/i) || [])[1] || (String(url).startsWith("data:video/webm") ? "webm" : "mp4");
    const name = `memuai-video-${stamp}.${ext}`;
    try {
      const saved = String(url).startsWith("/api/pg/media/file/")
        ? await api("/api/pg/media/save", { method: "POST", body: { url, name } })
        : await saveToDownloads({ url: String(url), name });
      if (saved?.ok) return savedToast(saved);
    } catch (error) {
      toast(t("Не удалось сохранить видео: ", "Could not save the video: ") + (error.message || error), "warn");
    }
  }

  function modelOptions(list, value) {
    const groups = new Map();
    for (const id of list) {
      const row = (S.models || []).find((m) => m.id === id) || {};
      const group = String(row.provider || id.split(/[/-]/)[0] || "").replace(/^genspark$/i, "Genspark");
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(id);
    }
    return [...groups.entries()].map(([group, ids]) => `<optgroup label="${esc(group)}">${ids.map((id) =>
      `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(modelDisplayName(id))}</option>`).join("")}</optgroup>`).join("");
  }
  function generatorControl(kind) {
    const list = listOf(kind), value = String(pref(prefKey(kind), "") || "");
    const id = kind === "video" ? "st-media-video" : "st-media-image";
    const missing = value && value !== "off" && !list.includes(value)
      ? `<option value="${esc(value)}" selected>${esc(value)} — ${t("недоступна", "unavailable")}</option>` : "";
    return `<select class="pinput mm-select" id="${id}">
      <option value="" ${value === "" ? "selected" : ""}>${list.length ? t("Автоматически", "Automatic") + " · " + esc(modelDisplayName(list.find((x) => (pref("pinned_models", []) || []).includes(x)) || list[0])) : t("Автоматически (пока нет моделей)", "Automatic (no models yet)")}</option>
      <option value="off" ${value === "off" ? "selected" : ""}>${t("Выключено — только по команде", "Off — only by command")}</option>
      ${missing}${modelOptions(list, value)}</select>`;
  }
  function emptyHint(kind) {
    if (listOf(kind).length) return "";
    return `<div class="mm-empty">${icon(kind === "video" ? "video" : "image")}<div><b>${kind === "video" ? t("Моделей видео пока нет", "No video models yet") : t("Моделей изображений пока нет", "No image models yet")}</b>
      <p>${kind === "video"
        ? t("Подойдёт аккаунт Genspark (AI Video) или своё OpenAI-совместимое подключение, которое умеет /videos (Sora и похожие), а также Veo в Google AI Studio. Если имя модели не распознаётся, укажите её в поле «Модели генерации видео» подключения.",
          "A Genspark account (AI Video) works, as does a custom OpenAI-compatible connection that serves /videos (Sora and similar) or Veo in Google AI Studio. If the model name is not recognised, list it in the connection's «Video generation models» field.")
        : t("Подойдёт аккаунт Genspark (AI Image) или своё подключение с моделью картинок: gpt-image, FLUX, Imagen, Seedream, Gemini image / nano-banana. Если имя не распознаётся, укажите модель в поле «Модели генерации изображений» подключения.",
          "A Genspark account (AI Image) works, as does a custom connection with an image model: gpt-image, FLUX, Imagen, Seedream, Gemini image / nano-banana. If the name is not recognised, list the model in the connection's «Image generation models» field.")}</p></div></div>`;
  }
  function decisionControl() {
    const value = String(pref("decision_model", "") || "");
    const list = textModels().slice().sort((a, b) => (FAST.test(b.id) - FAST.test(a.id)) || modelDisplayName(a.id).localeCompare(modelDisplayName(b.id)));
    const fast = list.filter((m) => FAST.test(m.id)), other = list.filter((m) => !FAST.test(m.id));
    const opt = (m) => `<option value="${esc(m.id)}" ${m.id === value ? "selected" : ""}>${esc(modelDisplayName(m.id))}</option>`;
    const missing = value && !list.some((m) => m.id === value) ? `<option value="${esc(value)}" selected>${esc(value)} — ${t("недоступна", "unavailable")}</option>` : "";
    return `<div class="mm-decision-wrap"><div class="mm-decision"><select class="pinput mm-select" id="st-decision-model">
      <option value="" ${value ? "" : "selected"}>${t("Нет — решает модель чата", "None — the chat model decides")}</option>${missing}
      ${fast.length ? `<optgroup label="${esc(t("Быстрые (рекомендуется)", "Fast (recommended)"))}">${fast.map(opt).join("")}</optgroup>` : ""}
      ${other.length ? `<optgroup label="${esc(t("Остальные", "Others"))}">${other.map(opt).join("")}</optgroup>` : ""}</select>
      <button type="button" class="mm-btn mm-test" id="st-decision-test" ${value ? "" : "disabled"}>${icon("zap")}<span>${t("Проверить скорость", "Test speed")}</span></button></div>
      <div class="mm-test-result" id="st-decision-result" hidden></div></div>`;
  }
  function settingsHtml(ctx) {
    const { K, L2 } = ctx;
    ensureIcons();
    const image = imageList().length, video = videoList().length;
    const status = `<span class="mm-status" title="${esc(t("Моделей изображений / видео", "Image / video models"))}"><span>${icon("image")}${image}</span><span>${icon("video")}${video}</span></span>`;
    return K.section({ id: "settings-media-models", icon: icon("image"), title: L2("Изображения, видео и решения", "Images, video and decisions"), aside: status,
      desc: L2("ИИ сам понимает, что нужно ответить — текстом, картинкой или видео. /image и /video создают сразу, без выбора.",
        "The AI works out whether to answer with text, a picture or a video. /image and /video generate right away, with no decision step."),
      keywords: "image video изображение картинка видео генерация generation decisions решения autoprompt промпт",
      body:
        K.row({ label: L2("Модель для изображений", "Image model"), desc: L2("Рисует, когда об этом просят в чате", "Draws when you ask for it in the chat"), stack: true, key: "media_image_model", def: "",
          keywords: "image model модель изображений картинки gpt-image flux", control: generatorControl("image"), extra: emptyHint("image") })
        + K.row({ label: L2("Модель для видео", "Video model"), desc: L2("Создаёт ролики прямо в чате", "Creates clips right in the chat"), stack: true, key: "media_video_model", def: "",
          keywords: "video model модель видео sora veo kling", control: generatorControl("video"), extra: emptyHint("video") })
        + K.row({ label: L2("Модель решений", "Decisions model"), stack: true, key: "decision_model", def: "",
          desc: L2("Необязательно. Небольшая быстрая модель за долю секунды решает, нужна ли картинка или видео. Без неё это делает модель чата — тоже работает.",
            "Optional. A small fast model decides in a split second whether a picture or video is needed. Without it the chat model does this — that works too."),
          keywords: "decisions model модель решений router маршрутизация fast быстрая", control: decisionControl() })
        + K.row({ label: L2("Автопромпт", "Autoprompt"), key: "media_autoprompt", def: false,
          desc: L2("Перед генерацией модель чата продумывает подробный промпт, сохраняя то, что вы просили. Промпт видно под результатом.",
            "Before generating, the chat model writes a detailed prompt that keeps what you asked for. The prompt is shown under the result."),
          keywords: "autoprompt автопромпт prompt enhance улучшить промпт",
          control: `<label class="switch"><input type="checkbox" role="switch" id="st-media-autoprompt" ${pref("media_autoprompt", false) ? "checked" : ""}><span class="sl"></span></label>` })
        + K.row({ label: L2("Искать в интернете, как выглядит", "Look up appearance on the web"), when: "st-media-autoprompt", key: "media_autoprompt_search", def: true,
          desc: L2("Например, внешность персонажа игры или фильма, здание, товар", "For example a game or film character, a building, a product"),
          keywords: "web search поиск character персонаж",
          control: `<label class="switch"><input type="checkbox" role="switch" id="st-media-autoprompt-search" ${pref("media_autoprompt_search", true) !== false ? "checked" : ""}><span class="sl"></span></label>` }) });
  }
  function bindSettings() {
    const saved = () => { if (typeof pt === "function") toast(pt("saved")); };
    const bindSelect = (id, key) => {
      const el = document.getElementById(id);
      if (el) el.onchange = async () => {
        await savePreferences({ [key]: el.value }); saved();
        if (key === "decision_model") {
          const test = document.getElementById("st-decision-test");
          if (test) test.disabled = !el.value;
          const result = document.getElementById("st-decision-result");
          if (result) result.hidden = true;
        }
      };
    };
    bindSelect("st-media-image", "media_image_model");
    bindSelect("st-media-video", "media_video_model");
    bindSelect("st-decision-model", "decision_model");
    const toggleBind = (id, key) => {
      const el = document.getElementById(id);
      if (el) el.onchange = async () => { await savePreferences({ [key]: el.checked }); saved(); };
    };
    toggleBind("st-media-autoprompt", "media_autoprompt");
    toggleBind("st-media-autoprompt-search", "media_autoprompt_search");
    const test = document.getElementById("st-decision-test");
    if (test) test.onclick = async () => {
      const model = document.getElementById("st-decision-model")?.value;
      const out = document.getElementById("st-decision-result");
      if (!model || !out) return;
      test.disabled = true; out.hidden = false; out.className = "mm-test-result";
      out.textContent = t("Проверяю…", "Checking…");
      const samples = [[t("покажи, как выглядит Шрек", "show me what Shrek looks like"), "image"],
        [t("нарисуй таблицу расходов за месяц", "draw a table of monthly expenses"), "text"],
        [t("сделай короткий ролик с волнами на закате", "make a short clip of waves at sunset"), "video"]];
      const results = [];
      try {
        for (const [text, want] of samples) {
          const r = await api("/api/pg/media/decide", { method: "POST", body: { text, model, force: true, lang: PG_LANG } });
          if (r.error) throw new Error(r.error);
          results.push({ ok: r.kind === want, ms: r.ms, kind: r.kind });
        }
        const avg = Math.round(results.reduce((n, r) => n + r.ms, 0) / results.length);
        const correct = results.filter((r) => r.ok).length;
        out.classList.add(avg < 2000 && correct === results.length ? "ok" : "warn");
        out.innerHTML = `${icon(correct === results.length ? "check" : "info")} <span>${t("Среднее время", "Average time")}: <b>${(avg / 1000).toFixed(2)} ${t("с", "s")}</b> · ${t("верно", "correct")}: <b>${correct}/${results.length}</b>${avg >= 2000 ? " · " + t("медленно — лучше модель попроще", "slow — a smaller model is better") : ""}</span>`;
      } catch (error) {
        out.classList.add("warn");
        out.textContent = t("Проверка не удалась: ", "The check failed: ") + (error.message || error);
      } finally { test.disabled = false; }
    };
  }

  window.MemuMedia = Object.freeze({
    VERSION, ensureIcons, promptBlock, kindOf, pickModel, available, missingText, decisionModel, detectAspect,
    route, direct, command, insertCommand, toastMissing, retry, again, extract, runForAssistant, historyNote, inlineHtml, hydrate, retryHtml,
    settingsHtml, bindSettings, contextRows, previousPrompt,
  });
  return window.MemuMedia;
})();
