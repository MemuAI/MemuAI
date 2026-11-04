/* Telegram automations in Planning: "when this happens in Telegram, do that".
   A list with ready-made ideas and a step-by-step builder. Rules are stored in the Telegram Use
   settings and run by the Telegram Use client of the MemuAI window. */
(function () {
  "use strict";
  const en = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const Q = (value) => (en() ? `“${value}”` : `«${value}»`);
  const I = (name) => (typeof icon === "function" ? icon(name) : "");
  const $q = (sel, root = document) => root.querySelector(sel);
  const API = "/api/pg/integrations/telegram-use";
  const TAB_KEY = "memuai.plan.tab.v1";
  /** Server messages come as "русский / English". */
  const say = (value) => { const text = String(value ?? ""); const parts = text.split(" / "); return parts.length === 2 ? parts[en() ? 1 : 0] : text; };
  const note = (text, type) => { if (typeof toast === "function") toast(text, type); };
  const pad = (n) => String(n).padStart(2, "0");
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const plural = (n, one, few, many) => { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; };
  const newId = () => { const bytes = new Uint8Array(6); (window.crypto || {}).getRandomValues?.(bytes); const id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""); return /^0+$/.test(id) ? Math.random().toString(36).slice(2, 14).padEnd(8, "0") : id; };

  const S = { status: null, error: "", loading: null, poll: 0, tests: {}, log: null, logRule: "", logOpen: false, logBusy: false, justSaved: "",
    d: null, step: "when", editing: false, left: new Set(), reviewSeen: false, picks: {}, dialogs: {}, test: null, testing: false, saving: false };

  /* ---------- catalog ---------- */
  const EVENTS = () => [
    { group: T("Сообщения", "Messages"), items: [
      ["message", "chat", T("Любое сообщение", "Any message"), T("Каждое новое сообщение: текст, голосовое, фото, файл.", "Every new message: text, voice, photo, file.")],
      ["text", "text", T("Текстовое сообщение", "Text message"), T("Только сообщения без вложений.", "Only messages without attachments.")],
      ["voice", "mic", T("Голосовое или кружок", "Voice or video note"), T("Голосовые сообщения и видеокружки — например, чтобы получать их текстом.", "Voice messages and round video notes — for example, to get them as text.")],
      ["photo", "image", T("Фото", "Photo"), T("Сообщения с фотографиями. Альбом считается одним сообщением.", "Messages with photos. An album counts as one message.")],
      ["video", "video", T("Видео", "Video"), T("Сообщения с видео.", "Messages with a video.")],
      ["file", "file", T("Файл или аудио", "File or audio"), T("Документы, архивы, музыка и другие файлы.", "Documents, archives, music and other files.")],
      ["media", "attach", T("Любое вложение", "Any attachment"), T("Фото, видео, файлы, голосовые, стикеры, опросы, геопозиции.", "Photos, videos, files, voice, stickers, polls, locations.")],
      ["link", "link", T("Сообщение со ссылкой", "Message with a link"), T("Сообщения, в которых есть ссылка.", "Messages that contain a link.")],
    ] },
    { group: T("Каналы и группы", "Channels and groups"), items: [
      ["post", "megaphone", T("Новый пост в канале", "New channel post"), T("Публикации каналов, на которые вы подписаны.", "Posts of the channels you follow.")],
      ["mention", "at", T("Меня упомянули", "I am mentioned"), T("Когда вас упоминают через @ или отвечают на ваше сообщение.", "When someone @mentions you or replies to your message.")],
      ["keyword", "search", T("Ключевые слова", "Keywords"), T("Сообщения, где встречается одно из ваших слов или фраз.", "Messages that contain one of your words or phrases.")],
    ] },
    { group: T("Время", "Time"), items: [
      ["schedule", "clock", T("По расписанию", "On a schedule"), T("Каждый день или в выбранные дни в заданное время.", "Every day or on chosen days at set times.")],
    ] },
  ];
  const eventMeta = (id) => { for (const g of EVENTS()) for (const it of g.items) if (it[0] === id) return it; return [id, "zap", id, ""]; };
  const SCOPES = () => [
    ["chats", "list", T("Выбранные чаты", "Chosen chats"), T("Только в отмеченных чатах, группах, каналах или переписках с людьми.", "Only in the chats, groups, channels or conversations you tick.")],
    ["private", "user", T("Все личные чаты", "All private chats"), T("Переписки с людьми и ботами.", "Conversations with people and bots.")],
    ["groups", "users", T("Все группы", "All groups"), T("Все группы и супергруппы.", "All groups and supergroups.")],
    ["channels", "megaphone", T("Все каналы", "All channels"), T("Все каналы, на которые вы подписаны.", "All channels you follow.")],
    ["any", "globe", T("Везде", "Everywhere"), T("Все чаты, кроме Избранного.", "All chats except Saved Messages.")],
  ];
  const scopeMeta = (id) => SCOPES().find((s) => s[0] === id) || SCOPES()[0];
  const ACTIONS = () => ({
    voice: ["mic", T("Расшифровать голосовое", "Transcribe the voice message"), T("MemuAI распознаёт речь и отправляет текст. Можно попросить ИИ дополнительно обработать расшифровку.", "MemuAI recognizes the speech and sends the text. The AI can also polish the transcript.")],
    answer: ["sparkle", T("Ответить с помощью ИИ", "Reply with AI"), T("ИИ читает переписку и пишет ответ от вашего имени по вашей инструкции. Если ответ не нужен — промолчит.", "The AI reads the conversation and replies as you, following your instruction. If no reply is needed, it stays silent.")],
    summary: ["list", T("Пересказать", "Summarize"), T("Короткий пересказ сообщения или поста: суть, важные цифры, даты и ссылки.", "A short summary of the message or post: the gist, key numbers, dates and links.")],
    command: ["terminal", T("Выполнить команду !//", "Run a !// command"), T("Ваша команда из Telegram Use выполняется для нового сообщения, а в чат уходит только её результат.", "Your Telegram Use command runs on the new message; only its result goes to the chat.")],
    forward: ["share", T("Переслать", "Forward"), T("Пересылает сообщение (или весь альбом) в Избранное или в другой чат.", "Forwards the message (or the whole album) to Saved Messages or another chat.")],
    send: ["send", T("Отправить текст", "Send a text"), T("Отправляет заранее написанный текст. В него можно подставить отправителя, чат и дату.", "Sends a text you wrote in advance. It may include the sender, the chat and the date.")],
    notify: ["bell", T("Уведомить меня", "Notify me"), T("Уведомление во «Входящих» MemuAI и, по желанию, всплывающее уведомление Windows. В Telegram ничего не отправляется.", "A notification in the MemuAI Inbox and, optionally, a Windows pop-up. Nothing is sent to Telegram.")],
    read: ["eye", T("Отметить прочитанным", "Mark as read"), T("Сообщения сразу становятся прочитанными. Ничего не отправляется.", "Messages become read right away. Nothing is sent.")],
    react: ["smile", T("Поставить реакцию", "Add a reaction"), T("Ставит выбранную реакцию на новое сообщение.", "Adds the chosen reaction to the new message.")],
    digest: ["archive", T("Сводка чатов", "Chat digest"), T("ИИ собирает новые сообщения выбранных чатов с прошлого запуска и пишет одну короткую сводку.", "The AI gathers new messages of the chosen chats since the previous run and writes one short digest.")],
    generate: ["sparkle", T("Сообщение от ИИ", "AI-written message"), T("ИИ пишет текст по вашему заданию — например, план дня или мысль дня — и отправляет его.", "The AI writes a text from your brief, such as a plan or a thought for the day, and sends it.")],
  });
  const actionMeta = (id) => ACTIONS()[id] || ["zap", id, ""];
  /** The action as a short label; a command shows its trigger, e.g. "Command !//voice". */
  const actionLabel = (a) => (a.action === "command" && a.command ? T(`Команда ${a.command}`, `Command ${a.command}`) : actionMeta(a.action)[1]);
  const EVENT_ACTIONS = ["voice", "answer", "summary", "command", "forward", "send", "notify", "read", "react"];
  const SPEECH_EVENTS = new Set(["voice", "message", "media", "file"]);
  const RESULT = new Set(["voice", "answer", "summary", "command", "digest", "generate"]);
  const DEFAULT_ACTION = { voice: "voice", post: "summary", schedule: "digest", keyword: "notify", mention: "notify", photo: "forward", video: "forward", file: "forward", media: "forward", link: "summary" };
  const CONTEXT_DEFAULT = { answer: 20, digest: 300 };
  const TARGETS = () => ({
    reply: ["reply", T("В этот же чат", "The same chat"), T("Ответом на сообщение в той же переписке — собеседник увидит результат.", "As a reply in the same conversation — the other person sees the result.")],
    saved: ["bookmark", T("В Избранное", "Saved Messages"), T("В ваше «Избранное» в Telegram — видите только вы.", "To your Saved Messages in Telegram — only you see it.")],
    chat: ["send", T("В другой чат", "Another chat"), T("В выбранный чат, группу или канал.", "To a chosen chat, group or channel.")],
    memuai: ["chat", T("В чат MemuAI", "A MemuAI chat"), T("Новый чат в MemuAI с результатом. В Telegram ничего не отправляется.", "A new MemuAI chat with the result. Nothing is sent to Telegram.")],
    notify: ["bell", T("Уведомлением", "As a notification"), T("Во «Входящие» MemuAI и всплывающим уведомлением. В Telegram ничего не отправляется.", "To the MemuAI Inbox and as a pop-up. Nothing is sent to Telegram.")],
  });
  const KIND_LABELS = () => ({ user: T("Личный чат", "Private chat"), bot: T("Бот", "Bot"), group: T("Группа", "Group"), channel: T("Канал", "Channel"), saved: T("Ваше Избранное", "Your Saved Messages") });
  const AVATAR_HUES = [4, 24, 262, 146, 188, 212, 328];
  const REACTIONS = ["👍", "❤", "🔥", "👏", "😁", "🤔", "🎉", "🙏", "👌", "😍", "💯", "🤝", "👀", "⚡", "🏆", "😢"];
  const HINTS = () => ({
    answer: T("По умолчанию: отвечай коротко и вежливо; если нужно моё решение или не хватает данных — скажи, что я отвечу позже.", "By default: reply briefly and politely; if it needs my decision or facts you lack, say I will answer later."),
    summary: T("По умолчанию: главное в 1–3 предложениях, с важными цифрами, датами, именами и ссылками.", "By default: the main point in 1–3 sentences with key numbers, dates, names and links."),
    digest: T("По умолчанию: главные темы, решения, вопросы ко мне и что требует ответа — по чатам, коротко.", "By default: main topics, decisions, questions to me and what needs a reply — by chat, briefly."),
  });
  const IDEAS = () => [
    { icon: "mic", title: T("Голосовые → текст ответом", "Voice → text reply"), desc: T("Когда выбранный человек присылает голосовое, в чат уходит его расшифровка.", "When a chosen person sends a voice message, its transcript goes to the chat."),
      step: "where", make: () => ({ when: { type: "voice", scope: "chats" }, do: { action: "voice", to: "reply" } }) },
    { icon: "bookmark", title: T("Голосовые — текстом в Избранное", "Voice as text in Saved"), desc: T("Расшифровки голосовых из личных чатов приходят в ваше Избранное.", "Transcripts of voice messages from private chats arrive in your Saved Messages."),
      step: "review", make: () => ({ name: T("Расшифровки в Избранное", "Transcripts to Saved"), when: { type: "voice", scope: "private" }, do: { action: "voice", to: "saved" } }) },
    { icon: "terminal", title: T("Голосовое → команда !//voice", "Voice → the !//voice command"), desc: T("Отвечает результатом команды, сама команда в чат не отправляется.", "Replies with the command's result; the command itself is not sent."),
      step: "where", make: () => ({ when: { type: "voice", scope: "chats" }, do: { action: "command", command: firstCommand("voice"), to: "reply" } }) },
    { icon: "megaphone", title: T("Пересказ канала в Избранное", "Channel summaries to Saved"), desc: T("Каждый новый пост выбранного канала — коротким пересказом в Избранное.", "Every new post of a chosen channel as a short summary in Saved Messages."),
      step: "where", make: () => ({ when: { type: "post", scope: "chats" }, do: { action: "summary", to: "saved" } }) },
    { icon: "moon", title: T("Автоответчик на ночь", "Night auto-reply"), desc: T("С 23:00 до 8:00 ИИ вежливо отвечает в личных чатах, что вы ответите утром.", "From 23:00 to 8:00 the AI politely replies in private chats that you will answer in the morning."),
      step: "do", make: () => ({ name: T("Ночной автоответчик", "Night auto-reply"), when: { type: "message", scope: "private" }, only: { from: "23:00", to: "08:00", days: [] },
        do: { action: "answer", to: "reply", context: 10, prompt: T("Сейчас ночь, я сплю. Коротко и вежливо ответь, что я отвечу утром. Если сообщение срочное — предложи позвонить.", "It is night and I am asleep. Briefly and politely reply that I will answer in the morning. If the message is urgent, suggest calling.") },
        limits: { cooldown: 3600 } }) },
    { icon: "search", title: T("Ключевые слова → уведомление", "Keywords → notification"), desc: T("Узнавайте, когда в группах пишут о важном: «срочно», ваше имя, проект.", "Know when groups mention something important: “urgent”, your name, a project."),
      step: "when", make: () => ({ when: { type: "keyword", scope: "groups" }, do: { action: "notify" } }) },
    { icon: "at", title: T("Упоминания в группах", "Mentions in groups"), desc: T("Уведомление MemuAI, когда вас упомянули или ответили вам в группе.", "A MemuAI notification when someone mentions you or replies to you in a group."),
      step: "review", make: () => ({ name: T("Упоминания в группах", "Group mentions"), when: { type: "mention", scope: "groups" }, do: { action: "notify" } }) },
    { icon: "archive", title: T("Утренняя сводка чатов", "Morning chat digest"), desc: T("По будням в 9:00 ИИ присылает в Избранное сводку новых сообщений выбранных чатов.", "On weekdays at 9:00 the AI sends a digest of new messages from chosen chats to Saved Messages."),
      step: "where", make: () => ({ name: T("Утренняя сводка", "Morning digest"), when: { type: "schedule", times: ["09:00"], days: [0, 1, 2, 3, 4] }, do: { action: "digest", to: "saved" } }) },
    { icon: "image", title: T("Фото и файлы → в Избранное", "Photos and files → Saved"), desc: T("Пересылать вложения из выбранных чатов, чтобы они не потерялись.", "Forward attachments from chosen chats so they do not get lost."),
      step: "where", make: () => ({ when: { type: "media", scope: "chats" }, do: { action: "forward", to: "saved" } }) },
    { icon: "eye", title: T("Каналы — сразу прочитанными", "Mark channels as read"), desc: T("Новые посты выбранных каналов отмечаются прочитанными.", "New posts of chosen channels are marked as read."),
      step: "where", make: () => ({ when: { type: "post", scope: "chats" }, do: { action: "read" } }) },
    { icon: "send", title: T("Сообщение по расписанию", "Scheduled message"), desc: T("Каждый день в 8:00 отправлять ваш текст выбранному человеку или в чат.", "Send your text to a chosen person or chat every day at 8:00."),
      step: "do", make: () => ({ when: { type: "schedule", times: ["08:00"], days: [] }, do: { action: "send", to: "chat", text: T("Доброе утро!", "Good morning!") } }) },
    { icon: "sparkle", title: T("Сообщение от ИИ по расписанию", "AI message on a schedule"), desc: T("ИИ пишет текст по вашему заданию, например мысль дня, и присылает его.", "The AI writes a text from your brief, such as a thought for the day, and sends it."),
      step: "do", make: () => ({ when: { type: "schedule", times: ["09:00"], days: [] }, do: { action: "generate", to: "saved", prompt: T("Напиши одну короткую мотивирующую мысль на день.", "Write one short motivating thought for the day.") } }) },
    { icon: "smile", title: T("Реакция на сообщения", "React to messages"), desc: T("Ставить выбранную реакцию на сообщения выбранных людей или каналов.", "Add a chosen reaction to messages of chosen people or channels."),
      step: "where", make: () => ({ when: { type: "message", scope: "chats" }, do: { action: "react", emoji: "❤" } }) },
  ];

  /* ---------- data ---------- */
  async function request(action, data) {
    const response = await fetch(API, action ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, ...(data || {}) }) } : { cache: "no-store" });
    const out = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(say(out.error) || `HTTP ${response.status}`);
    return out;
  }
  function load() {
    if (S.loading) return S.loading;
    S.loading = request().then((status) => { S.status = status; S.error = ""; return status; })
      .catch((error) => { S.error = error.message || String(error); return null; })
      .finally(() => { S.loading = null; });
    return S.loading;
  }
  const config = () => S.status?.config || {};
  const rules = () => config().automations || [];
  const accounts = () => S.status?.accounts || [];
  const multi = () => accounts().length > 1;
  const accountMissing = (id) => accounts().length > 0 && !accounts().some((a) => a.id === id);
  function accountName(id) {
    const a = accounts().find((x) => x.id === id);
    if (!a) return id === "main" ? T("Основной аккаунт", "Main account") : id;
    return a.label || a.account?.name || (a.account?.username ? "@" + a.account.username : "") || (a.main ? T("Основной аккаунт", "Main account") : T("Новый аккаунт", "New account"));
  }
  const firstCommand = (kind) => ((config().commands || []).find((c) => c.kind === kind && c.enabled !== false) || (config().commands || []).find((c) => c.kind === kind) || {}).trigger || "";
  const writesTelegram = (rule) => ["forward", "read", "react", "send"].includes(rule.do.action) || (RESULT.has(rule.do.action) && ["reply", "saved", "chat"].includes(rule.do.to));
  function view() { const st = S.status || {}; return [rules(), st.automation_stats, accounts().map((a) => [a.id, a.connected, a.has_session, a.label]), config().enabled, config().allow_writes, config().automations_enabled, st.configured]; }
  function count() { return S.status ? rules().length : null; }
  function syncCount() {
    const tab = $q('#pln-root .pln-tab[data-tab="telegram"]');
    if (!tab || count() === null) return;
    let em = tab.querySelector("em");
    if (!em) { em = document.createElement("em"); tab.appendChild(em); }
    em.textContent = String(count());
  }
  function prefetch() { if (!S.status && !S.loading) load().then(syncCount); }
  function openSettings() { if (typeof openSettingsPage === "function") openSettingsPage("integrations", { sub: "int-telegram-use" }); }
  function backToList() {
    S.d = null;
    try { localStorage.setItem(TAB_KEY, "telegram"); } catch (e) { /* storage may be off */ }
    if (window.MemuPlan?.open) window.MemuPlan.open({ tab: "telegram" });
  }

  /* ---------- words ---------- */
  function whenText(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(d) - day(now)) / 86400000);
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (diff === 0) return T(`сегодня в ${time}`, `today at ${time}`);
    if (diff === -1) return T(`вчера в ${time}`, `yesterday at ${time}`);
    return `${d.toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" })} ${T("в", "at")} ${time}`;
  }
  const chatName = (c) => (c.kind === "saved" ? T("Избранное", "Saved Messages") : c.name || String(c.id));
  function names(list, max = 2) {
    const items = (list || []).map((c) => Q(chatName(c)));
    if (items.length <= max) return items.join(", ");
    const rest = items.length - max;
    return `${items.slice(0, max).join(", ")} ${T(`и ещё ${rest}`, `+${rest} more`)}`;
  }
  const DAYS = () => (en() ? ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] : ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]);
  function daysText(days) {
    const d = (days || []).slice().sort();
    if (!d.length || d.length === 7) return "";
    if (d.join() === "0,1,2,3,4") return T("по будням", "on weekdays");
    if (d.join() === "5,6") return T("по выходным", "on weekends");
    return d.map((i) => DAYS()[i]).join(", ");
  }
  function scheduleText(w) {
    const times = (w.times || []).filter(Boolean);
    return `${daysText(w.days) || T("каждый день", "every day")} ${T("в", "at")} ${times.join(", ") || "—"}`;
  }
  function capital(text) { return text ? text[0].toUpperCase() + text.slice(1) : text; }
  function whereText(d) {
    const w = d.when;
    if (w.scope !== "chats") return scopeMeta(w.scope)[2];
    return w.chats.length ? names(w.chats) : T("чаты не выбраны", "no chats chosen");
  }
  function actionText(d) {
    const a = d.do;
    const title = actionMeta(a.action)[1];
    if (a.action === "command") return a.command ? `${T("Команда", "Command")} ${a.command}` : title;
    if (a.action === "react") return `${title} ${a.emoji || ""}`.trim();
    if (a.action === "digest") return d.when.chats.length ? `${title}: ${names(d.when.chats)}` : title;
    return title;
  }
  function targetText(d) {
    const a = d.do;
    if (a.to === "chat") return a.to_chat ? Q(chatName(a.to_chat)) : T("чат не выбран", "no chat chosen");
    return (TARGETS()[a.to] || ["", a.to])[1];
  }
  function sentence(d) {
    const w = d.when, parts = [];
    if (w.type === "schedule") parts.push(capital(scheduleText(w)));
    else {
      parts.push(eventMeta(w.type)[2]);
      parts.push(whereText(d));
      if ((w.senders || []).length) parts.push(`${T("от", "from")} ${names(w.senders)}`);
      if (w.type === "keyword" && w.keywords.length) parts.push(`${w.keywords.slice(0, 3).map(Q).join(", ")}${w.keywords.length > 3 ? "…" : ""}`);
    }
    return [parts.join(" · "), actionText(d), targetsFor(d).length ? targetText(d) : ""].filter(Boolean).join(" → ");
  }
  function autoName(d) {
    const w = d.when;
    const where = w.type === "schedule" ? scheduleText(w) : w.scope === "chats" ? (w.chats.length ? names(w.chats, 1) : "") : scopeMeta(w.scope)[2].toLowerCase();
    return [actionLabel(d.do), where].filter(Boolean).join(" · ").slice(0, 120);
  }

  /* ---------- rule model ---------- */
  function blank() {
    const first = accounts().find((a) => a.connected) || accounts()[0];
    return { id: newId(), name: "", enabled: true, account: first?.id || "main",
      when: { type: "message", scope: "chats", chats: [], senders: [], include_own: false, keywords: [], exclude: [], skip_forwarded: false, skip_bots: true, times: ["09:00"], days: [] },
      only: { from: "", to: "", days: [] },
      do: { action: "answer", to: "reply", to_chat: null, prompt: "", text: "", model: "", command: "", emoji: "👍", context: 20, header: true, silent: false, toast: true, media: false, always: false, images: [] },
      limits: { per_hour: 60, per_day: 600, cooldown: 30 } };
  }
  function draftFrom(seed) {
    const b = blank(), s = seed || {};
    const d = { ...b, ...s, when: { ...b.when, ...(s.when || {}) }, only: { ...b.only, ...(s.only || {}) }, do: { ...b.do, ...(s.do || {}) }, limits: { ...b.limits, ...(s.limits || {}) } };
    if (!/^[a-z0-9]{6,24}$/.test(String(d.id || ""))) d.id = newId();
    for (const key of ["chats", "senders", "keywords", "exclude", "times", "days"]) if (!Array.isArray(d.when[key])) d.when[key] = [];
    if (!Array.isArray(d.do.images)) d.do.images = [];
    if (!d.when.times.length) d.when.times = ["09:00"];
    if (!s.do || s.do.context === undefined) d.do.context = CONTEXT_DEFAULT[d.do.action] ?? 0;
    if (!s.limits || s.limits.cooldown === undefined) d.limits.cooldown = d.do.action === "answer" ? 30 : 0;
    if (!s.do || !s.do.action) d.do.action = DEFAULT_ACTION[d.when.type] || d.do.action;
    if (!s.do || !s.do.to) d.do.to = defaultTarget(d);
    return fix(d);
  }
  function actionsFor(event) {
    if (event === "schedule") return ["digest", "send", "generate"];
    return EVENT_ACTIONS.filter((a) => a !== "voice" || SPEECH_EVENTS.has(event));
  }
  const defaultTarget = (d) => (d.when.type === "schedule" || d.do.action === "forward" || d.when.type === "post" || d.when.scope === "channels" ? "saved" : "reply");
  function targetsFor(d) {
    const act = d.do.action;
    if (["notify", "read", "react"].includes(act)) return [];
    if (act === "forward") return ["saved", "chat"];
    const noReply = d.when.type === "schedule" || d.when.type === "post" || d.when.scope === "channels";
    const list = ["reply", "saved", "chat", "memuai", "notify"].filter((t) => !(noReply && t === "reply"));
    return act === "send" ? list.filter((t) => t !== "memuai" && t !== "notify") : list;
  }
  /** The same corrections the server makes, so the builder never shows an impossible combination. */
  function fix(d) {
    const w = d.when, a = d.do;
    if (w.type === "post" && !["chats", "channels"].includes(w.scope)) w.scope = "channels";
    if (w.type === "schedule") w.scope = "chats";
    const allowed = actionsFor(w.type);
    if (!allowed.includes(a.action)) a.action = DEFAULT_ACTION[w.type] && allowed.includes(DEFAULT_ACTION[w.type]) ? DEFAULT_ACTION[w.type] : allowed[0];
    const targets = targetsFor(d);
    if (!targets.length) a.to = "";
    else if (!targets.includes(a.to)) a.to = targets.includes(defaultTarget(d)) ? defaultTarget(d) : targets[0];
    if (a.to !== "chat") a.to_chat = null;
    if (a.action === "react" && !a.emoji) a.emoji = "👍";
    return d;
  }
  function setEvent(type) {
    const d = S.d, before = d.do.action;
    d.when.type = type;
    if (type === "schedule" && !(d.when.times || []).length) d.when.times = ["09:00"];
    // Until the user picks an action, the action follows the event: a voice message → transcribe, a post → summarize.
    const allowed = actionsFor(type), preferred = DEFAULT_ACTION[type] || "answer";
    if (allowed.includes(preferred) && (!S.actionTouched || !allowed.includes(before) || type === "schedule")) setAction(preferred);
    S.prevEvent = type;
    fix(d);
  }
  function setAction(action) {
    const d = S.d, before = d.do.action;
    if (d.do.context === (CONTEXT_DEFAULT[before] ?? 0)) d.do.context = CONTEXT_DEFAULT[action] ?? 0;
    if (d.limits.cooldown === (before === "answer" ? 30 : 0)) d.limits.cooldown = action === "answer" ? 30 : 0;
    d.do.action = action;
    fix(d);
    if (targetsFor(d).length && d.do.to !== defaultTarget(d) && before !== action && !S.editing && !S.targetTouched) {
      if (targetsFor(d).includes(defaultTarget(d))) d.do.to = defaultTarget(d);
    }
  }
  function serverRule(d) {
    const rule = clone(d);
    rule.when.times = Array.from(new Set((rule.when.times || []).filter((t) => /^\d{2}:\d{2}$/.test(t)))).sort();
    rule.name = String(rule.name || "").trim();
    return rule;
  }
  function problems(d = S.d) {
    const w = d.when, a = d.do, out = [];
    if (accountMissing(d.account)) out.push({ step: "when", text: T("Выберите подключённый аккаунт Telegram", "Choose a connected Telegram account") });
    if (w.type === "schedule" && !(w.times || []).some((t) => /^\d{2}:\d{2}$/.test(t))) out.push({ step: "when", text: T("Укажите время запуска", "Set a run time") });
    if (w.type === "keyword" && !w.keywords.length) out.push({ step: "when", text: T("Добавьте хотя бы одно ключевое слово", "Add at least one keyword") });
    if (w.scope === "chats" && !w.chats.length && !(w.type === "schedule" && ["send", "generate"].includes(a.action)))
      out.push({ step: "where", text: w.type === "schedule" ? T("Выберите чаты для сводки", "Choose the chats for the digest") : T("Выберите хотя бы один чат", "Choose at least one chat") });
    if (a.action === "send" && !String(a.text || "").trim() && !(a.images || []).length) out.push({ step: "do", text: T("Напишите текст сообщения или добавьте изображение", "Write the message text or add an image") });
    if (a.action === "generate" && !String(a.prompt || "").trim()) out.push({ step: "do", text: T("Опишите, что должен написать ИИ", "Describe what the AI should write") });
    if (a.action === "command" && !/^!\/\/[A-Za-zА-Яа-яЁё0-9_-]{1,32}$/.test(a.command || "")) out.push({ step: "do", text: T("Выберите команду", "Choose a command") });
    if (a.to === "chat" && !a.to_chat) out.push({ step: "to", text: T("Выберите чат, куда отправлять результат", "Choose the chat that receives the result") });
    return out;
  }
  function steps(d = S.d) {
    const sched = d.when.type === "schedule";
    return [
      { id: "when", title: T("Когда", "When"), hint: T("Что должно произойти в Telegram, чтобы автоматизация сработала.", "What should happen in Telegram to start the automation.") },
      ...(sched ? [] : [{ id: "where", title: T("Где", "Where"), hint: T("В каких чатах следить за этим.", "Which chats to watch.") }]),
      { id: "do", title: T("Что сделать", "What to do"), hint: sched ? T("Что MemuAI сделает в назначенное время.", "What MemuAI does at the set time.") : T("Что MemuAI сделает с новым сообщением.", "What MemuAI does with the new message.") },
      ...(sched && d.do.action === "digest" ? [{ id: "where", title: T("Какие чаты", "Which chats"), hint: T("Из каких чатов собирать сводку.", "Which chats go into the digest.") }] : []),
      ...(targetsFor(d).length ? [{ id: "to", title: T("Куда отправить", "Where to send"), hint: T("Куда попадёт результат.", "Where the result goes.") }] : []),
      ...(sched ? [] : [{ id: "filters", title: T("Условия", "Conditions"), optional: true, hint: T("Необязательно: от кого, какие слова, в какое время.", "Optional: from whom, which words, at what time.") }]),
      { id: "review", title: T("Проверка", "Review"), hint: T("Проверьте правило, при желании запустите пробу и сохраните.", "Check the rule, try it if you like and save.") },
    ];
  }
  const stepIds = () => steps().map((s) => s.id);
  function filtersUsed(d = S.d) {
    const w = d.when, o = d.only;
    return !!(w.senders.length || (w.type !== "keyword" && w.keywords.length) || w.exclude.length || (o.from && o.to) || o.days.length || w.include_own || w.skip_forwarded);
  }
  function stepSummary(id) {
    const d = S.d, w = d.when;
    if (id === "when") return w.type === "schedule" ? capital(scheduleText(w)) : eventMeta(w.type)[2] + (w.type === "keyword" && w.keywords.length ? `: ${w.keywords.slice(0, 3).join(", ")}` : "");
    if (id === "where") return w.type === "schedule" ? (w.chats.length ? names(w.chats) : T("чаты не выбраны", "no chats chosen")) : whereText(d);
    if (id === "do") return actionText(d) + ((d.do.images || []).length ? ` · 🖼 ${(d.do.images || []).length}` : "");
    if (id === "to") return targetText(d);
    if (id === "filters") {
      const o = d.only, parts = [];
      if (w.senders.length) parts.push(`${T("от", "from")} ${names(w.senders, 1)}`);
      if (w.type !== "keyword" && w.keywords.length) parts.push(`${T("слова", "words")}: ${w.keywords.slice(0, 2).join(", ")}`);
      if (w.exclude.length) parts.push(`${T("без", "without")}: ${w.exclude.slice(0, 2).join(", ")}`);
      if (o.from && o.to) parts.push(`${o.from}–${o.to}`);
      if (o.days.length) parts.push(daysText(o.days));
      return parts.join(" · ") || T("Без условий", "No conditions");
    }
    if (id === "review") return problems().length ? T("Нужно исправить", "Needs a fix") : T("Всё готово", "All set");
    return "";
  }

  /* ---------- list ---------- */
  function paneHtml() {
    return `<div class="plt" id="plt-root">${S.status ? listHtml() : S.error ? errorHtml(S.error) : `<div class="pln-nl-wait"><span class="pln-spin"></span>${T("Загружаю автоматизации Telegram…", "Loading Telegram automations…")}</div>`}</div>`;
  }
  function errorHtml(text) {
    return `<div class="pln-note warn">${I("info")}<span>${H(text)}</span><button type="button" class="pln-link" data-plt="retry">${I("refresh")}${T("Повторить", "Retry")}</button></div>`;
  }
  function noteHtml(text, action = "", warn = true) {
    return `<div class="pln-note ${warn ? "warn" : ""} plt-note">${I("info")}<span>${H(text)}</span>${action}</div>`;
  }
  function listHtml() {
    const st = S.status, c = config(), list = rules(), accs = accounts();
    const ready = !!st.configured && accs.some((a) => a.has_session);
    const master = list.length ? `<div class="plt-master"><span>${T("Все автоматизации", "All automations")}</span><label class="switch" title="${H(T("Включить или поставить на паузу все автоматизации Telegram", "Turn all Telegram automations on or pause them"))}"><input type="checkbox" data-plt-master ${c.automations_enabled !== false ? "checked" : ""} aria-label="${H(T("Все автоматизации", "All automations"))}"><span class="sl"></span></label></div>` : "";
    const top = `<div class="plt-top"><div class="plt-title"><span class="plt-mark">${I("telegram")}</span><div><b>${T("Автоматизации Telegram", "Telegram automations")}</b>
        <p class="sub">${T("Когда в Telegram что-то происходит, MemuAI сам делает нужное: расшифрует голосовое, перескажет пост, ответит по вашей инструкции или пришлёт сводку.", "When something happens in Telegram, MemuAI handles it: transcribes a voice message, summarizes a post, replies by your instruction or sends a digest.")}</p></div></div>
      <div class="plt-top-actions">${master}<button type="button" class="pbtn" data-plt="new">${I("plus")}<span>${T("Новая автоматизация", "New automation")}</span></button></div></div>`;
    const strip = accs.length ? `<div class="plt-accounts">${accs.map((a) => `<span class="plt-acc ${a.connected ? "on" : ""}"><span class="plt-dot" aria-hidden="true"></span><b>${H(accountName(a.id))}</b>${a.account?.username ? `<small>@${H(a.account.username)}</small>` : ""}<em>${H(a.connected ? T("подключён", "connected") : a.has_session ? T("нет связи", "offline") : T("не выполнен вход", "signed out"))}</em></span>`).join("")}
      <button type="button" class="pln-link" data-plt="settings">${I("settings")}${T("Аккаунты и настройки", "Accounts and settings")}</button></div>` : "";
    const notes = [];
    if (ready) {
      if (!c.enabled) notes.push(noteHtml(T("Telegram Use выключен — автоматизации не работают.", "Telegram Use is off — automations do not run."), `<button type="button" class="pln-link" data-plt="settings">${T("Открыть настройки", "Open settings")}</button>`));
      else if (!accs.some((a) => a.connected)) notes.push(noteHtml(T("Telegram сейчас не на связи — автоматизации продолжат работу, когда соединение восстановится.", "Telegram is offline right now — automations resume once the connection is back."), `<button type="button" class="pln-link" data-plt="settings">${T("Проверить подключение", "Check the connection")}</button>`, false));
      if (c.automations_enabled === false && list.length) notes.push(noteHtml(T("Все автоматизации на паузе.", "All automations are paused."), `<button type="button" class="pln-link" data-plt="master-on">${T("Включить", "Turn on")}</button>`));
      if (!c.allow_writes && list.some((r) => r.enabled && writesTelegram(r))) notes.push(noteHtml(T("Отправка в Telegram выключена в настройках — автоматизации, которые пишут в Telegram, завершатся ошибкой.", "Telegram writes are off in settings — automations that write to Telegram will fail."), `<button type="button" class="pln-link" data-plt="allow-writes">${T("Разрешить отправку", "Allow writes")}</button>`));
    }
    const body = !ready ? startHtml(st) : list.length
      ? `<div class="pln-list-head"><b>${T("Ваши автоматизации", "Your automations")}</b><span class="sub">${H(T(`${list.length} ${plural(list.length, "правило", "правила", "правил")}`, `${list.length} ${list.length === 1 ? "rule" : "rules"}`))}</span></div>
        <div class="pln-timeline plt-list">${list.map(cardHtml).join("")}</div>`
      : `<div class="pln-empty plt-empty">${I("telegram")}<div><b>${T("Пока нет ни одной автоматизации", "No automations yet")}</b><span>${T("Начните с готовой идеи ниже или соберите правило по шагам.", "Start from a ready-made idea below or build a rule step by step.")}</span></div><button type="button" class="pbtn ghost" data-plt="new">${I("plus")}<span>${T("Собрать по шагам", "Build step by step")}</span></button></div>`;
    const ideas = IDEAS().map((x, i) => `<button type="button" class="pln-tpl" data-plt-idea="${i}">${I(x.icon)}<span><b>${H(x.title)}</b><small>${H(x.desc)}</small></span></button>`).join("");
    const ideasHtml = list.length
      ? `<details class="plt-more"${S.ideasOpen ? " open" : ""}><summary>${I("sparkle")}<b>${T("Ещё идеи", "More ideas")}</b><span class="sub">${T("Готовые правила — откроются в конструкторе", "Ready rules — they open in the builder")}</span></summary><div class="pln-templates plt-ideas">${ideas}</div></details>`
      : `<section class="plv-sec"><div class="plv-sec-head"><b>${T("Готовые идеи", "Ready-made ideas")}</b></div><div class="pln-templates plt-ideas">${ideas}</div></section>`;
    const log = list.length ? `<details class="plt-more plt-log"${S.logOpen ? " open" : ""}><summary>${I("list")}<b>${T("Журнал срабатываний", "Run log")}</b><span class="sub">${S.logRule ? H(ruleTitle(S.logRule)) : T("Что и когда сделали автоматизации", "What the automations did and when")}</span></summary><div class="plt-log-body" id="plt-log-body">${logHtml()}</div></details>` : "";
    const html = `${top}${strip}${notes.join("")}${body}${ideasHtml}${log}`;
    return html;
  }
  function startHtml(st) {
    const text = !st.configured ? T("Подключите личный аккаунт в настройках Telegram Use: получите API ID и войдите по QR-коду — это займёт пару минут.", "Connect your personal account in the Telegram Use settings: get an API ID and sign in with a QR code — it takes a couple of minutes.")
      : T("Данные приложения сохранены — осталось войти в аккаунт по QR-коду или номеру телефона.", "App credentials are saved — sign in with a QR code or a phone number.");
    return `<section class="plv-hero plt-start"><div class="plv-hero-art" aria-hidden="true"><span>${I("mic")}</span><span>${I("telegram")}</span><span>${I("bell")}</span></div>
      <h3>${T("Автоматизируйте свой Telegram", "Automate your Telegram")}</h3><p>${H(text)}</p>
      <button type="button" class="pbtn" data-plt="settings">${I("telegram")}<span>${T("Подключить Telegram", "Connect Telegram")}</span></button></section>`;
  }
  function ruleTitle(id) { const r = rules().find((x) => x.id === id); return r ? r.name || autoName(draftFrom(r)) : id; }
  function cardHtml(rule) {
    const d = draftFrom(clone(rule)), stat = (S.status.automation_stats || {})[rule.id];
    const accountGone = accountMissing(rule.account);
    const off = !rule.enabled || config().automations_enabled === false;
    const status = stat
      ? `<span class="pln-status ${stat.ok ? (stat.skipped ? "st-waiting" : "st-active") : "st-error"}"><span class="pln-dot ${stat.ok ? (stat.skipped ? "wait" : "on") : "bad"}"></span>${H(stat.ok ? (stat.skipped ? T("Пропустила", "Skipped") : T("Сработала", "Ran")) : T("Ошибка", "Error"))}</span><span class="pln-status-text">${H(whenText(stat.last))}</span>`
        + (stat.today ? `<span class="pln-chip">${H(T("сегодня", "today"))}: ${H(stat.today)}</span>` : "") + (stat.errors_today ? `<span class="pln-chip plt-bad">${H(T("ошибок", "errors"))}: ${H(stat.errors_today)}</span>` : "")
      : `<span class="pln-status-text">${H(rule.enabled ? T("Ещё не срабатывала", "Has not run yet") : T("На паузе", "Paused"))}</span>`;
    const fresh = S.justSaved === rule.id ? " plv-new" : "";
    return `<article class="pln-card plt-card ${off ? "is-off" : ""}${fresh}" data-plt-rule="${H(rule.id)}">
      <div class="pln-card-ico">${I(eventMeta(rule.when.type)[1])}</div>
      <div class="pln-card-body">
        <div class="pln-card-title"><b>${H(rule.name || autoName(d))}</b>${multi() || accountGone ? `<span class="pln-chip">${I("user")}${H(accountGone ? T("Аккаунт отключён", "Account disconnected") : accountName(rule.account))}</span>` : ""}</div>
        <div class="pln-card-sum"><span class="pln-chip kind">${H(actionLabel(rule.do))}</span>${(rule.do.images || []).length ? `<span class="pln-chip">🖼 ${(rule.do.images || []).length}</span>` : ""}<span>${H(sentence(d))}</span></div>
        <div class="pln-card-status">${status}</div>
        ${testHtml(S.tests[rule.id], true)}
      </div>
      <div class="pln-card-actions">
        <button type="button" class="mini-action" data-plt="test" ${accountGone ? "disabled" : ""} title="${H(T("Проверить на последнем подходящем сообщении — ничего не отправится", "Test on the latest matching message — nothing is sent"))}" aria-label="${H(T("Проверить", "Test"))}">${I("play")}</button>
        <button type="button" class="mini-action" data-plt="edit" title="${H(T("Изменить", "Edit"))}" aria-label="${H(T("Изменить", "Edit"))}">${I("edit")}</button>
        <button type="button" class="mini-action" data-plt="more" title="${H(T("Ещё: журнал, копия, удаление", "More: log, copy, delete"))}" aria-label="${H(T("Ещё", "More"))}" aria-haspopup="menu">${I("more")}</button>
        <label class="switch" title="${H(accountGone ? T("Выберите другой аккаунт в настройках правила", "Choose another account in the rule settings") : rule.enabled ? T("Включена — нажмите, чтобы поставить на паузу", "On — click to pause") : T("На паузе — нажмите, чтобы включить", "Paused — click to turn on"))}"><input type="checkbox" data-plt-toggle ${rule.enabled ? "checked" : ""} ${accountGone ? "disabled" : ""} aria-label="${H(T("Включена", "Enabled"))}"><span class="sl"></span></label>
      </div></article>`;
  }
  function targetName(id) { return (TARGETS()[id] || ["", id])[1]; }
  function testHtml(t, closable) {
    if (!t) return "";
    const x = closable ? `<button type="button" class="plv-x" data-plt="test-close" aria-label="${H(T("Скрыть", "Hide"))}">${I("close")}</button>` : "";
    if (t.busy) return `<div class="plt-test"><div class="plt-test-head"><span class="pln-spin"></span><b>${T("Проверяю на последних сообщениях… Ответ ИИ может занять до минуты.", "Testing on recent messages… The AI may need up to a minute.")}</b></div></div>`;
    if (t.error) return `<div class="plt-test bad"><div class="plt-test-head">${I("info")}<b>${H(t.error)}</b>${x}</div></div>`;
    const r = t.result || {};
    const head = !r.found ? T("Подходящих сообщений пока нет", "No matching messages yet") : !r.ok ? T("Проверка не удалась", "The test failed")
      : r.skipped ? T("Правило пропустило бы это сообщение", "The rule would skip this message") : T("Так сработает правило — ничего не отправлено", "This is what the rule would do — nothing was sent");
    const m = r.message;
    const msg = m ? `<div class="plt-test-msg">${I("chat")}<div><small>${H([m.chat, m.sender && m.sender !== m.chat ? m.sender : "", whenText(m.date)].filter(Boolean).join(" · "))}</small><span>${H(String(m.text || "").slice(0, 400))}</span></div></div>` : "";
    const out = r.result ? `<div class="plt-test-result">${r.target ? `<small>${H(T("Куда", "Where"))}: ${H(targetName(r.target))}</small>` : ""}${(r.images || []).length || r.images_missing ? `<small>🖼 ${H(T("Изображения", "Images"))}: ${H((r.images || []).join(", ") || "—")}${r.images_missing ? ` · ${H(T("не найдены", "missing"))}: ${H(r.images_missing)}` : ""}</small>` : ""}<div>${H(String(r.result).slice(0, 4000))}</div></div>` : "";
    return `<div class="plt-test ${r.ok === false ? "bad" : r.skipped || !r.found ? "skip" : "ok"}"><div class="plt-test-head">${I(r.ok === false ? "info" : r.skipped || !r.found ? "info" : "check")}<b>${H(head)}</b>${x}</div>${msg}${out}</div>`;
  }
  function logHtml() {
    if (S.logBusy && !Array.isArray(S.log)) return `<div class="pln-nl-wait"><span class="pln-spin"></span>${T("Загружаю журнал…", "Loading the log…")}</div>`;
    if (S.log && S.log.error) return errorHtml(S.log.error);
    if (!Array.isArray(S.log)) return "";
    const all = S.logRule ? `<button type="button" class="pln-link" data-plt="log-all">${I("list")}${T("Показать все автоматизации", "Show all automations")}</button>` : "";
    if (!S.log.length) return `${all}<p class="sub empty-note">${T("Срабатываний пока нет.", "No runs yet.")}</p>`;
    return `${all}<div class="pln-results">${S.log.slice(0, 100).map((row) => {
      const mark = row.ok ? (row.skipped ? "minus" : "check") : "close";
      const chips = [row.test ? T("проба", "test") : "", row.skipped ? T("пропуск", "skipped") : "", multi() ? accountName(row.account) : ""].filter(Boolean);
      return `<details class="pln-res ${row.ok ? (row.skipped ? "skip" : "ok") : "err"}"><summary><span class="pln-res-mark">${I(mark)}</span><span class="pln-res-name">${H(row.name || ruleTitle(row.rule))}</span>${row.chat ? `<span class="pln-chip">${H(row.chat)}</span>` : ""}${chips.map((ch) => `<span class="pln-chip">${H(ch)}</span>`).join("")}<span class="pln-res-time">${H(whenText(row.ts))}</span></summary>
        <div class="pln-res-text">${row.sender ? `${H(row.sender)}: ` : ""}${row.preview ? `${Q(H(row.preview))}\n\n` : ""}${H(row.result || "")}</div></details>`;
    }).join("")}</div>`;
  }
  function paintList() {
    const host = $q("#plt-root");
    if (!host) return;
    host.innerHTML = S.status ? listHtml() : S.error ? errorHtml(S.error) : "";
    S.justSaved = "";
    syncCount();
  }
  function paintCard(id) {
    const card = $q(`#plt-root [data-plt-rule="${CSS.escape(id)}"]`), rule = rules().find((r) => r.id === id);
    if (card && rule) card.outerHTML = cardHtml(rule);
  }
  function paintLog() { const box = $q("#plt-log-body"); if (box) box.innerHTML = logHtml(); }
  async function openLog(rule) {
    S.logOpen = true; S.logRule = rule || ""; S.logBusy = true; S.log = null;
    const details = $q("#plt-root .plt-log");
    if (details && !details.open) details.open = true;
    paintLog();
    try { S.log = (await request("automations_log", { rule: S.logRule, limit: 100 })).log || []; } catch (e) { S.log = { error: e.message }; }
    S.logBusy = false;
    const sub = $q("#plt-root .plt-log > summary .sub");
    if (sub) sub.textContent = S.logRule ? ruleTitle(S.logRule) : T("Что и когда сделали автоматизации", "What the automations did and when");
    paintLog();
    if (rule) $q("#plt-root .plt-log")?.scrollIntoView?.({ block: "nearest" });
  }
  async function testRule(rule) {
    S.tests[rule.id] = { busy: true };
    paintCard(rule.id);
    try { S.tests[rule.id] = { result: await request("automation_test", { rule }) }; } catch (e) { S.tests[rule.id] = { error: e.message }; }
    paintCard(rule.id);
    if (S.logOpen) openLog(S.logRule);
  }
  async function setMaster(on, input) {
    try { S.status = await request("settings", { automations_enabled: !!on }); paintList(); note(on ? T("Автоматизации включены", "Automations are on") : T("Автоматизации на паузе", "Automations are paused")); }
    catch (e) { if (input) input.checked = !on; note(e.message, "err"); }
  }
  async function allowWrites() {
    try { S.status = await request("settings", { allow_writes: true }); note(T("Отправка в Telegram разрешена", "Telegram writes are allowed")); if (S.d) paint(); else paintList(); }
    catch (e) { note(e.message, "err"); }
  }
  function moreMenu(anchor, rule) {
    if (!window.MemuPlanUI) return;
    const items = [
      { icon: I("list"), title: T("Журнал срабатываний", "Run log"), run: () => openLog(rule.id) },
      { icon: I("copy"), title: T("Дублировать", "Duplicate"), desc: T("Копия создаётся на паузе", "The copy starts paused"), run: async () => {
        try { S.status = await request("automation_save", { rule: { ...clone(rule), id: newId(), name: `${rule.name || autoName(draftFrom(rule))} ${T("(копия)", "(copy)")}`.slice(0, 120), enabled: false, created: 0 } }); paintList(); note(T("Копия создана (на паузе)", "Copy created (paused)")); }
        catch (e) { note(e.message, "err"); }
      } },
      { icon: I("trash"), title: T("Удалить", "Delete"), danger: true, run: async () => {
        const ok = typeof askConfirm === "function" ? await askConfirm(T("Удалить автоматизацию?", "Delete the automation?"), rule.name || autoName(draftFrom(rule))) : true;
        if (!ok) return;
        try { S.status = await request("automation_delete", { id: rule.id }); delete S.tests[rule.id]; paintList(); note(T("Автоматизация удалена", "Automation deleted")); }
        catch (e) { note(e.message, "err"); }
      } },
    ];
    const r = anchor.getBoundingClientRect(), box = (anchor.closest("#panel") || document.body).getBoundingClientRect();
    MemuPlanUI.menu(anchor, items, { lang: en() ? "en" : "ru", align: r.left + r.width / 2 > box.left + box.width / 2 ? "end" : "start" });
  }
  function bindList(host) {
    if (host.dataset.pltBound) return;
    host.dataset.pltBound = "1";
    host.addEventListener("click", async (event) => {
      const el = event.target.closest("[data-plt], [data-plt-idea]");
      if (!el || !host.contains(el)) return;
      if (el.dataset.pltIdea !== undefined) { const idea = IDEAS()[+el.dataset.pltIdea]; if (idea) openBuilder(idea.make(), { step: idea.step }); return; }
      const act = el.dataset.plt;
      if (act === "new") return openBuilder();
      if (act === "settings") return openSettings();
      if (act === "allow-writes") return allowWrites();
      if (act === "master-on") return setMaster(true);
      if (act === "log-all") return openLog("");
      if (act === "retry") { await load(); paintList(); return; }
      const card = el.closest("[data-plt-rule]"), rule = card && rules().find((r) => r.id === card.dataset.pltRule);
      if (!rule) return;
      if (act === "edit") return openBuilder(clone(rule));
      if (act === "test") return testRule(rule);
      if (act === "test-close") { delete S.tests[rule.id]; paintCard(rule.id); return; }
      if (act === "more") return moreMenu(el, rule);
    });
    host.addEventListener("change", async (event) => {
      const el = event.target;
      if (el.matches("[data-plt-master]")) return setMaster(el.checked, el);
      if (!el.matches("[data-plt-toggle]")) return;
      const id = el.closest("[data-plt-rule]")?.dataset.pltRule;
      try { S.status = await request("automation_toggle", { id, enabled: el.checked }); paintCard(id); }
      catch (e) { el.checked = !el.checked; note(e.message, "err"); }
    });
    host.addEventListener("toggle", (event) => {
      const el = event.target;
      if (el.matches?.(".plt-log")) { S.logOpen = el.open; if (el.open && !Array.isArray(S.log) && !S.logBusy) openLog(S.logRule); }
      else if (el.matches?.(".plt-more")) S.ideasOpen = el.open;
    }, true);
  }
  async function mount(host) {
    if (!host) return;
    bindList(host);
    const before = S.status ? JSON.stringify(view()) : "";
    await load();
    if (!host.isConnected) return;
    if (!S.status || before !== JSON.stringify(view()) || !host.querySelector(".plt-top")) paintList();
    else { S.justSaved = ""; syncCount(); }
    clearInterval(S.poll);
    S.poll = setInterval(refresh, 15000);
  }
  async function refresh() {
    const host = $q("#plt-root");
    if (!host) { clearInterval(S.poll); S.poll = 0; return; }
    if (document.visibilityState === "hidden" || Object.values(S.tests).some((t) => t.busy)) return;
    if (host.contains(document.activeElement) && document.activeElement.matches("input, textarea, select")) return;
    if (document.querySelector(".pln-pop")) return;
    const before = JSON.stringify(view());
    await load();
    if ($q("#plt-root") === host && before !== JSON.stringify(view())) paintList();
  }

  /* ---------- builder ---------- */
  async function openBuilder(seed, opts = {}) {
    if (!S.status) await load();
    const existing = seed && seed.id ? rules().find((r) => r.id === seed.id) : null;
    S.editing = !!existing;
    S.d = draftFrom(existing ? clone(existing) : seed || {});
    S.prevEvent = S.d.when.type;
    S.test = null; S.testing = false; S.saving = false; S.picks = {}; S.left = new Set(); S.targetTouched = false;
    S.actionTouched = S.editing || !!(seed && seed.do && seed.do.action);
    const ids = stepIds();
    S.step = ids.includes(opts.step) ? opts.step : S.editing ? "review" : "when";
    S.reviewSeen = S.step === "review";
    const conn = S.status && accounts().some((a) => a.has_session) ? "" : noteHtml(T("Telegram ещё не подключён: правило можно собрать, но выбрать чаты и проверить его получится после входа в аккаунт.", "Telegram is not connected yet: you can build the rule, but picking chats and testing need a signed-in account."), `<button type="button" class="pln-link" data-plt="settings">${T("Подключить", "Connect")}</button>`);
    panel(`<div class="pln-root pln-builder plw plt-builder" id="plt-builder">
      <button type="button" class="panel-return" data-plt="back">${I("back")}${T("Планирование", "Planning")}</button>
      <div class="plw-head"><h2>${S.editing ? T("Изменить автоматизацию Telegram", "Edit Telegram automation") : T("Новая автоматизация Telegram", "New Telegram automation")}</h2>
        <input class="pinput pln-name" data-f="name" value="${H(S.d.name)}" maxlength="120" placeholder="${H(T("Название — необязательно, подставится само", "Name — optional, filled in automatically"))}" aria-label="${H(T("Название", "Name"))}"></div>
      ${conn}
      <div class="plw-grid"><nav class="plw-rail" id="plt-rail" aria-label="${H(T("Шаги", "Steps"))}"></nav>
        <div class="plw-stage"><section class="pln-block" id="plt-pane" aria-live="polite"></section></div></div>
      <div class="pln-summary" id="plt-summary"></div></div>`, "pln-panel");
    bindBuilder();
    paint();
    setTimeout(() => $q('#plt-rail .plw-step[aria-current="step"]')?.focus({ preventScroll: true }), 0);
  }
  function railHtml() {
    const errs = problems();
    return steps().map((s, i) => {
      const bad = s.id !== S.step && errs.some((p) => p.step === s.id) && (S.reviewSeen || S.left.has(s.id));
      return `<button type="button" class="plw-step ${bad ? "bad" : ""}" data-plt-step="${s.id}" ${s.id === S.step ? 'aria-current="step"' : ""}><span class="plw-num">${s.id === "review" ? I("check") : i + 1}</span><span class="plw-step-text"><b>${H(s.title)}${s.optional ? `<em>${T("необязательно", "optional")}</em>` : ""}</b><small>${H(stepSummary(s.id))}</small></span></button>`;
    }).join("");
  }
  function tile(kind, id, ico, title, desc, on) {
    return `<button type="button" class="pln-kind ${on ? "on" : ""}" data-plt-${kind}="${id}" aria-pressed="${on}" title="${H(desc)}"><span class="pln-kind-ico">${I(ico)}</span><span class="pln-kind-txt"><b>${H(title)}</b><small>${H(desc)}</small></span></button>`;
  }
  const check = (path, label, on, extra = "") => `<label class="pln-check"><input type="checkbox" data-f="${path}" ${on ? "checked" : ""} ${extra}><span>${H(label)}</span></label>`;
  const timeIn = (path, value, label) => `<input class="pinput compact time" type="text" data-time="1" data-f="${path}" value="${H(value || "")}" aria-label="${H(label || T("Время", "Time"))}">`;
  const numIn = (path, value, min, max, label) => `<input class="pinput compact num" type="number" data-f="${path}" data-kind="int" min="${min}" max="${max}" step="1" value="${H(value ?? "")}" aria-label="${H(label)}">`;
  function wordsIn(path, value, placeholder, label) {
    return `<textarea class="ptexta plt-words" rows="2" data-f="${path}" data-kind="list" maxlength="5000" placeholder="${H(placeholder)}" aria-label="${H(label)}">${H((value || []).join(", "))}</textarea>`;
  }
  function daysHtml(path, days) {
    const set = new Set(days || []), all = set.size === 0;
    return `<div class="pln-days" role="group" aria-label="${H(T("Дни недели", "Weekdays"))}">${DAYS().map((n, i) => `<button type="button" class="${all || set.has(i) ? "on" : ""}" data-plt-day="${i}" data-path="${path}" aria-pressed="${all || set.has(i)}">${n}</button>`).join("")}
      <span class="pln-days-presets"><button type="button" data-plt-days="all" data-path="${path}">${T("все", "all")}</button><button type="button" data-plt-days="work" data-path="${path}">${T("будни", "weekdays")}</button><button type="button" data-plt-days="end" data-path="${path}">${T("выходные", "weekends")}</button></span></div>`;
  }
  function accountLine() {
    const missing = accountMissing(S.d.account);
    if (!multi() && !missing) return "";
    const unavailable = missing ? `<option value="${H(S.d.account)}" selected disabled>${T("Аккаунт отключён", "Account disconnected")}</option>` : "";
    return `<div class="pln-line plt-acc-line"><span>${T("Аккаунт Telegram", "Telegram account")}</span><select class="pinput compact" data-f="account" aria-label="${H(T("Аккаунт Telegram", "Telegram account"))}">${unavailable}${accounts().map((a) => `<option value="${H(a.id)}" ${a.id === S.d.account ? "selected" : ""}>${H(accountName(a.id))}${a.account?.username ? ` · @${H(a.account.username)}` : ""}${a.connected ? "" : H(T(" (не на связи)", " (offline)"))}</option>`).join("")}</select></div>`;
  }
  function whenBody() {
    const w = S.d.when, meta = eventMeta(w.type);
    const groups = EVENTS().map((g) => `<div class="plt-group"><span class="plt-group-label">${H(g.group)}</span><div class="pln-kinds">${g.items.map(([id, ico, title, desc]) => tile("event", id, ico, title, desc, w.type === id)).join("")}</div></div>`).join("");
    let extra = `<p class="pln-kind-desc">${I("info")}<span>${H(meta[3])}</span></p>`;
    if (w.type === "schedule") {
      const times = w.times.length ? w.times : ["09:00"];
      extra += `<div class="pln-line"><span>${T("Время", "Time")}</span>${times.map((t, i) => `<span class="plt-time">${timeIn(`when.times.${i}`, t)}${times.length > 1 ? `<button type="button" class="pln-x-sm" data-plt="time-del" data-i="${i}" aria-label="${H(T("Убрать это время", "Remove this time"))}">${I("close")}</button>` : ""}</span>`).join("")}${times.length < 12 ? `<button type="button" class="pln-add-sm" data-plt="time-add">${I("plus")}${T("Ещё время", "Add a time")}</button>` : ""}</div>
        <div class="pln-line"><span>${T("Дни", "Days")}</span>${daysHtml("when.days", w.days)}</div>`;
    }
    if (w.type === "keyword") extra += `<label class="field-label">${T("Слова или фразы — через запятую или с новой строки", "Words or phrases — comma or line separated")}</label>${wordsIn("when.keywords", w.keywords, T("срочно, созвон, мой проект", "urgent, call, my project"), T("Ключевые слова", "Keywords"))}<p class="pln-explain">${T("Регистр не важен; слово ищется внутри текста сообщения. Звёздочка «*» заменяет любое окончание: «счёт*» подойдёт и для «счёта», и для «счёту».", "Case does not matter; the word is searched inside the message text. An asterisk “*” stands for any ending: “счёт*” matches “счёта” and “счёту” alike.")}</p>`;
    return `${accountLine()}${groups}<div class="pln-kind-body">${extra}</div>`;
  }
  function whereBody() {
    const w = S.d.when;
    if (w.type === "schedule") return `${pickerHtml("when.chats", "all", true)}<p class="pln-explain">${I("info")}<span>${T("В сводку попадут новые сообщения этих чатов с прошлого запуска, в первый раз — за последние сутки.", "The digest takes the new messages of these chats since the previous run; the first time, the last 24 hours.")}</span></p>`;
    const scopes = SCOPES().filter(([id]) => w.type !== "post" || id === "chats" || id === "channels");
    const tiles = scopes.map(([id, ico, title, desc]) => tile("scope", id, ico, w.type === "post" && id === "chats" ? T("Выбранные каналы", "Chosen channels") : title, desc, w.scope === id)).join("");
    const body = w.scope === "chats" ? pickerHtml("when.chats", w.type === "post" ? "channel" : "all", true)
      : `<p class="pln-kind-desc">${I("info")}<span>${H(scopeMeta(w.scope)[3])}</span></p>${check("when.skip_bots", T("Не реагировать на сообщения ботов", "Ignore messages from bots"), w.skip_bots)}`;
    return `<div class="pln-kinds">${tiles}</div><div class="pln-kind-body">${body}</div>`;
  }
  function modelSelect() {
    const rows = window.MemuModelPicker?.rows?.() || [];
    const value = S.d.do.model || "";
    const list = rows.slice();
    if (value && !list.some((m) => m.id === value)) list.push({ id: value });
    return `<select class="pinput compact wide" data-f="do.model" data-plt-model aria-label="${H(T("Модель", "Model"))}"><option value="">${H(T("Модель из настроек Telegram Use", "The Telegram Use model"))}</option>${list.map((m) => `<option value="${H(m.id)}" ${m.id === value ? "selected" : ""}>${H(window.MemuModelPicker?.name?.(m.id) || m.id)}</option>`).join("")}</select>`;
  }
  function doBody() {
    const d = S.d, a = d.do;
    const tiles = actionsFor(d.when.type).map((id) => { const [ico, title, desc] = actionMeta(id); return tile("action", id, ico, title, desc, a.action === id); }).join("");
    return `<div class="pln-kinds">${tiles}</div><div class="pln-kind-body"><p class="pln-kind-desc">${I("info")}<span>${H(actionMeta(a.action)[2])}</span></p>${actionFields()}</div>`;
  }
  /* ---------- images attached to a rule ---------- */
  const imgUrl = (im) => `${API}/automation-image/${im.hash}.${im.ext}`;
  function imagesField() {
    const imgs = S.d.do.images || [];
    const thumbs = imgs.map((im, i) => `<span class="plt-img"><img src="${H(imgUrl(im))}" alt=""><span class="plt-img-name">${H(im.name || "")}</span><span class="plt-img-btns"><button type="button" data-plt-img-left="${i}" ${i === 0 ? "disabled" : ""} aria-label="${H(T("Влево", "Left"))}">←</button><button type="button" data-plt-img-right="${i}" ${i === imgs.length - 1 ? "disabled" : ""} aria-label="${H(T("Вправо", "Right"))}">→</button><button type="button" data-plt-img-del="${i}" aria-label="${H(T("Убрать", "Remove"))}">✕</button></span></span>`).join("");
    return `<div class="plt-sec"><b>${T("Изображения", "Images")}</b>
      <p class="sub">${T("До 10 изображений, каждое до 10 МБ. Отправятся вместе с текстом — одним альбомом.", "Up to 10 images, each up to 10 MB. They go out with the text as one album.")}</p>
      <div class="plt-imgs">${thumbs}</div>
      <input type="file" data-plt-img-pick accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden>
      <button type="button" class="pbtn ghost" data-plt-img-add ${imgs.length >= 10 || S.uploading ? "disabled" : ""}>${I("plus")}${T("Добавить изображения", "Add images")}</button>
      ${S.uploading ? `<span class="pln-hint">${T("Загружаю…", "Uploading…")}</span>` : ""}</div>`;
  }
  function imagesPreview() {
    const imgs = (S.d.do.images || []);
    if (!imgs.length) return "";
    return `<div class="plt-sec"><b>${T("Изображения", "Images")}</b><div class="plt-imgs">${imgs.map((im) => `<span class="plt-img"><img src="${H(imgUrl(im))}" alt=""><span class="plt-img-name">${H(im.name || "")}</span></span>`).join("")}</div></div>`;
  }
  async function uploadImages(files) {
    if (!S.d || !files || !files.length || S.uploading) return;
    const imgs = S.d.do.images || (S.d.do.images = []);
    S.uploading = true; paint();
    try {
      for (const file of [...files]) {
        if (imgs.length >= 10) { note(T("Больше 10 изображений нельзя", "No more than 10 images"), "err"); break; }
        if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) { note(T(`«${file.name}» — не изображение`, `“${file.name}” is not an image`), "err"); continue; }
        if (file.size > 10 * 1024 * 1024) { note(T(`«${file.name}» больше 10 МБ`, `“${file.name}” is over 10 MB`), "err"); continue; }
        const dataUrl = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => reject(new Error("read")); r.readAsDataURL(file); });
        const res = await fetch(`${API}/automation-image`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: file.name, data: dataUrl }) });
        const out = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(out.error || `HTTP ${res.status}`);
        imgs.push(out.image);
      }
    } catch (e) { note(e.message, "err"); }
    S.uploading = false; paint();
  }

  function actionFields() {
    const d = S.d, a = d.do, act = a.action, sched = d.when.type === "schedule";
    const prompt = (label, placeholder, rows = 3) => `<label class="field-label">${H(label)}</label><textarea class="ptexta" rows="${rows}" data-f="do.prompt" maxlength="8000" placeholder="${H(placeholder)}" aria-label="${H(label)}">${H(a.prompt)}</textarea>`;
    const model = `<div class="pln-line"><span>${T("Модель", "Model")}</span>${modelSelect()}</div>`;
    if (act === "voice") return `${prompt(T("Обработать расшифровку — необязательно", "Process the transcript — optional"), T("Пусто — придёт точный текст. Например: исправь ошибки и раздели на абзацы", "Empty — the exact text. For example: fix typos and split into paragraphs"), 2)}${model}
      <p class="pln-explain">${I("mic")}<span>${T("Распознавание речи настраивается в Telegram Use → «Распознавание речи и очистка диктовки».", "Speech recognition is set up in Telegram Use → Speech recognition and transcript cleanup.")}</span></p>`;
    if (act === "answer") return `${prompt(T("Как отвечать", "How to reply"), HINTS().answer, 4)}
      <div class="pln-line"><span>${T("Учитывать прошлых сообщений", "Previous messages to consider")}</span>${numIn("do.context", a.context, 0, 2000, T("Сколько прошлых сообщений учитывать", "Previous messages to consider"))}<span class="pln-hint">${T("0–2000", "0–2000")}</span></div>
      ${check("do.media", T("Учитывать фото и видео — ИИ их опишет", "Consider photos and videos — the AI describes them"), a.media)}${model}
      <p class="pln-explain">${I("info")}<span>${T("Голосовые расшифровываются автоматически. Если ответ не нужен, ИИ промолчит.", "Voice messages are transcribed automatically. If no reply is needed, the AI stays silent.")}</span></p>`;
    if (act === "summary") return `${prompt(T("Как пересказывать — необязательно", "How to summarize — optional"), HINTS().summary)}${check("do.media", T("Описывать фото и видео", "Describe photos and videos"), a.media)}${model}`;
    if (act === "command") {
      const cmds = config().commands || [];
      if (!cmds.length) return noteHtml(T("Команд пока нет — создайте их в настройках Telegram Use.", "No commands yet — create them in the Telegram Use settings."), `<button type="button" class="pln-link" data-plt="settings">${T("Открыть настройки", "Open settings")}</button>`, false);
      const options = [["", T("— выберите команду —", "— choose a command —")], ...cmds.map((c) => [c.trigger, `${c.trigger} · ${c.name}${c.enabled === false ? T(" (выключена)", " (off)") : ""}`])];
      if (a.command && !cmds.some((c) => c.trigger === a.command)) options.push([a.command, a.command]);
      return `<div class="pln-line"><span>${T("Команда", "Command")}</span><select class="pinput compact wide" data-f="do.command" aria-label="${H(T("Команда", "Command"))}">${options.map(([v, l]) => `<option value="${H(v)}" ${v === a.command ? "selected" : ""}>${H(l)}</option>`).join("")}</select></div>
        <p class="pln-explain">${I("info")}<span>${T("Команда выполняется так, будто вы ответили ею на новое сообщение: !//voice расшифрует голосовое, !//summary перескажет переписку. Сама команда в чат не отправляется.", "The command runs as if you replied to the new message with it: !//voice transcribes a voice message, !//summary summarizes the chat. The command itself is not sent.")}</span></p>`;
    }
    if (act === "send") {
      const vars = (sched ? ["date", "time"] : ["sender", "chat", "text", "link", "date", "time"]).map((v) => `<button type="button" class="plt-var" data-plt-var="${v}" title="${H(T("Вставить", "Insert"))}">{${v}}</button>`).join("");
      return `<label class="field-label">${T("Текст сообщения", "Message text")}</label><textarea class="ptexta" rows="3" data-f="do.text" maxlength="4000" placeholder="${H(sched ? T("Например: Доброе утро! Сегодня {date}.", "For example: Good morning! Today is {date}.") : T("Например: Спасибо, {sender}! Получил, отвечу чуть позже.", "For example: Thanks, {sender}! Got it, I'll answer a bit later."))}" aria-label="${H(T("Текст сообщения", "Message text"))}">${H(a.text)}</textarea>
        <div class="plt-vars"><span>${T("Подстановки:", "Placeholders:")}</span>${vars}</div>
        ${imagesField()}`;
    }
    if (act === "notify") return `${check("do.toast", T("Показывать всплывающее уведомление Windows", "Show a Windows pop-up notification"), a.toast)}${check("do.media", T("Расшифровывать голосовые прямо в уведомлении", "Transcribe voice messages in the notification"), a.media)}
      <p class="pln-explain">${I("bell")}<span>${T("Уведомление появится во «Входящих» MemuAI со ссылкой на сообщение.", "The notification appears in the MemuAI Inbox with a link to the message.")}</span></p>`;
    if (act === "read") return `<p class="pln-explain">${I("eye")}<span>${T("Ничего настраивать не нужно.", "Nothing to set up.")}</span></p>`;
    if (act === "react") return `<label class="field-label">${T("Реакция", "Reaction")}</label><div class="plt-emojis" role="radiogroup" aria-label="${H(T("Реакция", "Reaction"))}">${REACTIONS.map((e) => `<button type="button" class="${a.emoji === e ? "on" : ""}" data-plt-emoji="${H(e)}" role="radio" aria-checked="${a.emoji === e}">${H(e)}</button>`).join("")}</div>
      <p class="pln-explain">${I("info")}<span>${T("В некоторых чатах разрешены не все реакции — тогда Telegram вернёт ошибку, и она появится в журнале.", "Some chats allow only certain reactions; Telegram then returns an error, which appears in the log.")}</span></p>`;
    if (act === "digest") return `${prompt(T("Как составить сводку — необязательно", "How to write the digest — optional"), HINTS().digest)}
      <div class="pln-line"><span>${T("Максимум сообщений из каждого чата", "Max messages per chat")}</span>${numIn("do.context", a.context, 0, 2000, T("Максимум сообщений из каждого чата", "Max messages per chat"))}<span class="pln-hint">${T("0 — до 300", "0 — up to 300")}</span></div>
      ${check("do.always", T("Присылать, даже если новых сообщений нет", "Send even when there are no new messages"), a.always)}${model}`;
    if (act === "generate") return `${prompt(T("Что должен написать ИИ", "What the AI should write"), T("Например: напиши короткий план дня и одну мысль для мотивации", "For example: write a short plan for the day and one motivating thought"))}${model}`;
    return "";
  }
  function toBody() {
    const d = S.d, a = d.do;
    const tiles = targetsFor(d).map((id) => { const [ico, title, desc] = TARGETS()[id]; return tile("target", id, ico, title, desc, a.to === id); }).join("");
    const options = [];
    if (a.to === "chat") options.push(pickerHtml("do.to_chat", "all", false));
    if (["saved", "chat"].includes(a.to) && RESULT.has(a.action)) options.push(check("do.header", T("Добавлять заголовок: что это и откуда", "Add a header: what it is and where it came from"), a.header));
    if (["reply", "saved", "chat"].includes(a.to)) options.push(check("do.silent", a.action === "forward" ? T("Пересылать без звука", "Forward silently") : T("Отправлять без звука", "Send silently"), a.silent));
    if (a.to === "notify") options.push(check("do.toast", T("Показывать всплывающее уведомление Windows", "Show a Windows pop-up notification"), a.toast));
    if (a.to === "reply" && ["answer", "send", "voice", "command", "summary"].includes(a.action) && d.when.scope !== "chats")
      options.push(`<p class="pln-explain">${I("info")}<span>${T("Ответ уйдёт от вашего имени во все подходящие чаты. Начните с узкого выбора чатов или проверьте правило пробным запуском.", "Replies go out as you to every matching chat. Start with a narrow set of chats or try the rule with a test run.")}</span></p>`);
    return `<div class="pln-kinds">${tiles}</div><div class="pln-kind-body"><p class="pln-kind-desc">${I("info")}<span>${H((TARGETS()[a.to] || ["", "", ""])[2])}</span></p>${options.join("")}</div>`;
  }
  function filtersBody() {
    const d = S.d, w = d.when, o = d.only, windowOn = !!(o.from && o.to);
    return `<div class="plt-sec"><b>${T("От кого", "From whom")}</b><p class="sub">${T("Только сообщения этих людей — например, одного участника группы. Пусто — от всех.", "Only messages from these people, e.g. one member of a group. Empty means everyone.")}</p>${pickerHtml("when.senders", "people", true)}
        <div class="pln-line">${check("when.include_own", T("Учитывать и мои сообщения", "Include my own messages"), w.include_own)}</div>
        <p class="pln-explain">${I("info")}<span>${T("Без этой галочки ваши собственные сообщения правило не запускают.", "Without this, your own messages never start the rule.")}</span></p></div>
      ${w.type === "keyword" ? "" : `<div class="plt-sec"><b>${T("Содержит слова", "Contains words")}</b><p class="sub">${T("Сработает, только если в сообщении есть хотя бы одно из слов. Звёздочка «*» заменяет любое окончание: «счёт*» подойдёт и для «счёта», и для «счёту».", "Runs only when the message contains at least one of the words. An asterisk “*” stands for any ending: “счёт*” matches “счёта” and “счёту” alike.")}</p>${wordsIn("when.keywords", w.keywords, T("например: счёт, оплата", "e.g. invoice, payment"), T("Содержит слова", "Contains words"))}</div>`}
      <div class="plt-sec"><b>${T("Не содержит слов", "Does not contain")}</b><p class="sub">${T("Сообщения с этими словами пропускаются.", "Messages with these words are skipped.")}</p>${wordsIn("when.exclude", w.exclude, T("например: реклама, розыгрыш", "e.g. ad, giveaway"), T("Не содержит слов", "Does not contain"))}</div>
      <div class="plt-sec"><b>${T("Время работы", "Active hours")}</b>
        <div class="pln-line">${check("only.window", T("Только в это время", "Only at these hours"), windowOn)}${windowOn ? `<span>${T("с", "from")}</span>${timeIn("only.from", o.from, T("С", "From"))}<span>${T("до", "to")}</span>${timeIn("only.to", o.to, T("До", "To"))}` : ""}</div>
        <div class="pln-line"><span>${T("Дни", "Days")}</span>${daysHtml("only.days", o.days)}</div>
        ${windowOn && o.from > o.to ? `<p class="pln-explain">${I("moon")}<span>${T("Окно переходит через полночь.", "The window spans midnight.")}</span></p>` : ""}</div>
      <div class="plt-sec"><b>${T("Дополнительно", "More")}</b>
        ${check("when.skip_forwarded", T("Пропускать пересланные сообщения", "Skip forwarded messages"), w.skip_forwarded)}
        ${w.scope !== "chats" ? check("when.skip_bots", T("Не реагировать на сообщения ботов", "Ignore messages from bots"), w.skip_bots) : ""}
        <p class="pln-explain">${I("shield")}<span>${T("Сообщения, которые отправила сама автоматизация, никогда не запускают её снова.", "Messages sent by the automation itself never start it again.")}</span></p></div>`;
  }
  function reviewBody() {
    const d = S.d, errs = problems(), c = config();
    const w = d.when, sched = w.type === "schedule";
    const rows = steps().filter((s) => s.id !== "review").map((s, i) => `<li><span class="plw-num">${i + 1}</span><div><b>${H(s.title)}</b><span>${H(stepSummary(s.id))}</span></div><button type="button" class="pln-link" data-plt-step="${s.id}">${I("edit")}${T("Изменить", "Edit")}</button></li>`).join("");
    const warn = [];
    if (S.status && !c.enabled) warn.push(noteHtml(T("Telegram Use выключен — правило заработает после включения.", "Telegram Use is off — the rule starts working once it is on."), `<button type="button" class="pln-link" data-plt="settings">${T("Открыть настройки", "Open settings")}</button>`));
    if (S.status && !c.allow_writes && writesTelegram(d)) warn.push(noteHtml(T("Это правило пишет в Telegram, а отправка выключена в настройках Telegram Use.", "This rule writes to Telegram, but writes are off in the Telegram Use settings."), `<button type="button" class="pln-link" data-plt="allow-writes">${T("Разрешить отправку", "Allow writes")}</button>`));
    if (c.automations_enabled === false) warn.push(noteHtml(T("Все автоматизации сейчас на паузе — включите их в списке.", "All automations are paused — turn them on in the list."), "", false));
    if (!sched && d.when.type === "keyword" && !w.include_own) warn.push(noteHtml(T("Правило не реагирует на ваши собственные сообщения — если хотите, чтобы оно срабатывало и на них, включите «Учитывать и мои сообщения» на шаге «Условия».", "The rule ignores your own messages — if you want it to react to yours too, turn on “Include my own messages” in the Conditions step."), `<button type="button" class="pln-link" data-plt-step="filters">${I("edit")}${T("Изменить", "Edit")}</button>`));
    return `<div class="plw-sentence">${H(sentence(d))}</div>
      ${imagesPreview()}
      ${errs.map((p) => noteHtml(p.text, `<button type="button" class="pln-link" data-plt-step="${p.step}">${I("edit")}${T("Исправить", "Fix")}</button>`)).join("")}${warn.join("")}
      <ol class="plw-review">${rows}</ol>
      <div class="plt-sec"><b>${T("Ограничения", "Limits")}</b><p class="sub">${T("Защищают от лавины сообщений и лишних трат на ИИ. При превышении запуск пропускается и попадает в журнал.", "They protect from message floods and extra AI spend. Over the limit a run is skipped and logged.")}</p>
        <div class="plt-limits">
          ${sched ? "" : `<label class="pln-line"><span>${T("Пауза в одном чате, с", "Pause per chat, s")}</span>${numIn("limits.cooldown", d.limits.cooldown, 0, 86400, T("Пауза в одном чате, с", "Pause per chat, s"))}</label>`}
          <label class="pln-line"><span>${T("Не больше в час", "At most per hour")}</span>${numIn("limits.per_hour", d.limits.per_hour, 1, 1000, T("Не больше в час", "At most per hour"))}</label>
          <label class="pln-line"><span>${T("Не больше в сутки", "At most per day")}</span>${numIn("limits.per_day", d.limits.per_day, 1, 20000, T("Не больше в сутки", "At most per day"))}</label></div></div>
      ${accountLine()}
      ${check("enabled", S.editing ? T("Автоматизация включена", "The automation is on") : T("Включить сразу после сохранения", "Turn on right after saving"), d.enabled)}
      <div class="plt-sec"><b>${T("Пробный запуск", "Test run")}</b><p class="sub">${sched ? T("MemuAI выполнит правило прямо сейчас и покажет результат, но ничего не отправит.", "MemuAI runs the rule right now and shows the result, but sends nothing.") : T("MemuAI возьмёт последнее подходящее сообщение, покажет результат, но ничего не отправит.", "MemuAI takes the latest matching message and shows the result, but sends nothing.")}</p>
        <div><button type="button" class="pbtn ghost" data-plt="dry" ${S.testing || errs.length ? "disabled" : ""}>${I("play")}<span>${T("Проверить", "Test")}</span></button></div>
        ${testHtml(S.testing ? { busy: true } : S.test, false)}</div>`;
  }
  function paneBody(id) {
    const s = steps().find((x) => x.id === id) || steps()[0];
    const body = ({ when: whenBody, where: whereBody, do: doBody, to: toBody, filters: filtersBody, review: reviewBody })[s.id]();
    return `<header><div><b>${H(s.title)}</b><small>${H(s.hint)}</small></div></header><div class="pln-block-body">${body}</div>`;
  }
  function paintSummary() {
    const box = $q("#plt-summary");
    if (!box || !S.d) return;
    const ids = stepIds(), at = Math.max(0, ids.indexOf(S.step)), last = at === ids.length - 1, errs = problems();
    const skip = S.step === "filters" && !filtersUsed();
    const blocked = errs.length > 0 || S.saving;
    box.innerHTML = `<div class="pln-sum-main"><span class="pln-sum-label">${T("Итог", "Summary")}</span><div class="pln-sentence">${H(sentence(S.d))}</div></div>
      <div class="pln-sum-actions"><button type="button" class="pbtn ghost" data-plt="back">${T("Отмена", "Cancel")}</button>
        ${at > 0 ? `<button type="button" class="pbtn ghost" data-plt="prev">${I("back")}${T("Назад", "Back")}</button>` : ""}
        ${last ? "" : `<button type="button" class="pbtn ${S.editing ? "ghost" : ""} plw-next-btn" data-plt="next">${skip ? T("Пропустить", "Skip") : T("Далее", "Next")}${I("forward")}</button>`}
        ${S.editing || last ? `<button type="button" class="pbtn" data-plt="save" ${blocked ? "disabled" : ""} ${errs.length ? `title="${H(errs[0].text)}"` : ""}>${I("check")}${S.editing ? T("Сохранить", "Save") : T("Создать автоматизацию", "Create the automation")}</button>` : ""}</div>`;
  }
  function enhance(root) {
    root.querySelectorAll("[data-plt-model]").forEach((s) => window.MemuModelPicker?.enhanceSelect?.(s));
    if (typeof enhanceSelects === "function") enhanceSelects(root);
    window.MemuPlanUI?.enhanceTimes?.(root);
  }
  function paint(focus) {
    const root = $q("#plt-builder");
    if (!root || !S.d) return;
    const ids = stepIds();
    if (!ids.includes(S.step)) S.step = ids[ids.length - 1];
    $q("#plt-rail", root).innerHTML = railHtml();
    const pane = $q("#plt-pane", root);
    pane.innerHTML = paneBody(S.step);
    enhance(pane);
    paintSummary();
    loadPickers(pane);
    if (focus) $q(focus, pane)?.focus({ preventScroll: true });
  }
  function paintMeta() {
    const rail = $q("#plt-rail");
    if (rail) rail.innerHTML = railHtml();
    paintSummary();
    const sentenceBox = $q("#plt-pane .plw-sentence");
    if (sentenceBox) sentenceBox.textContent = sentence(S.d);
    const dry = $q('#plt-pane [data-plt="dry"]');
    if (dry) dry.disabled = S.testing || problems().length > 0;
  }
  function go(id) {
    if (!stepIds().includes(id)) return;
    if (S.step !== id) S.left.add(S.step);
    S.step = id;
    if (id === "review") S.reviewSeen = true;
    paint();
    for (let el = $q("#plt-builder"); el; el = el.parentElement) { if (el.scrollTop) el.scrollTop = 0; if (el.id === "panel") break; }
    $q('#plt-rail .plw-step[aria-current="step"]')?.focus({ preventScroll: true });
  }

  /* chat picker */
  function picked(path) {
    if (path === "do.to_chat") return S.d.do.to_chat ? [S.d.do.to_chat] : [];
    return path === "when.senders" ? S.d.when.senders : S.d.when.chats;
  }
  function pickerHtml(path, kinds, many) {
    const p = S.picks[path] || (S.picks[path] = { q: "", rows: null, busy: false, error: "" });
    p.kinds = kinds; p.many = many;
    const list = picked(path);
    const chips = list.length ? list.map((c) => `<span class="plt-chip">${avatar(c, true)}<span>${H(chatName(c))}</span><button type="button" data-plt-unpick="${H(c.id)}" aria-label="${H(T("Убрать", "Remove"))} ${H(chatName(c))}" title="${H(T("Убрать", "Remove"))}">${I("close")}</button></span>`).join("")
      : `<span class="plt-picked-none">${H(many ? (kinds === "people" ? T("Никто не выбран — подойдут сообщения от всех", "Nobody chosen — messages from everyone fit") : T("Ничего не выбрано — отметьте чаты в списке ниже", "Nothing chosen — tick chats in the list below")) : T("Чат не выбран — выберите его в списке ниже", "No chat chosen — pick one in the list below"))}</span>`;
    const placeholder = kinds === "channel" ? T("Найти канал…", "Find a channel…") : kinds === "people" ? T("Найти человека…", "Find a person…") : T("Найти чат, канал или человека…", "Find a chat, channel or person…");
    return `<div class="plt-picker" data-picker="${path}">
      <div class="plt-picked">${chips}</div>
      <div class="pln-search plt-search">${I("search")}<input class="pinput" data-plt-q value="${H(p.q)}" maxlength="100" autocomplete="off" spellcheck="false" placeholder="${H(placeholder)}" aria-label="${H(placeholder)}">
        <button type="button" class="plv-x" data-plt="dialogs-refresh" title="${H(T("Обновить список чатов", "Refresh the chat list"))}" aria-label="${H(T("Обновить список чатов", "Refresh the chat list"))}">${I("refresh")}</button></div>
      <div class="plt-chats" role="listbox" aria-multiselectable="${many}" aria-label="${H(T("Чаты", "Chats"))}">${chatsHtml(path)}</div></div>`;
  }
  function avatar(c, small) {
    let hash = 0;
    for (const ch of String(c.id ?? c.name ?? "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
    const hue = AVATAR_HUES[hash % AVATAR_HUES.length];
    const first = (String(c.name || "").match(/[\p{L}\p{N}]/u) || ["#"])[0].toUpperCase();
    return `<span class="plt-ava${small ? " sm" : ""}${c.kind === "saved" ? " saved" : ""}" style="--h:${hue}" aria-hidden="true">${c.kind === "saved" ? I("bookmark") : H(first)}</span>`;
  }
  function chatsHtml(path) {
    const p = S.picks[path];
    if (p.error) return noteHtml(p.error, `<button type="button" class="pln-link" data-plt="settings">${T("Настройки Telegram Use", "Telegram Use settings")}</button>`);
    if (!p.rows) return `<div class="pln-nl-wait"><span class="pln-spin"></span>${T("Загружаю чаты…", "Loading chats…")}</div>`;
    const rows = p.rows.filter((r) => p.kinds === "channel" ? r.kind === "channel" : p.kinds === "people" ? r.kind === "user" || r.kind === "bot" : true);
    if (!rows.length) return `<p class="sub empty-note">${p.q ? T("Ничего не найдено", "Nothing found") : T("Подходящих чатов нет", "No suitable chats")}</p>`;
    const on = new Set(picked(path).map((c) => String(c.id)));
    const labels = KIND_LABELS();
    return rows.slice(0, 100).map((r) => {
      const sel = on.has(String(r.id));
      return `<button type="button" class="plt-chat ${sel ? "on" : ""}" role="option" aria-selected="${sel}" data-plt-pick="${H(r.id)}">${avatar(r)}<span class="plt-chat-text"><b>${H(chatName(r))}</b><small>${H(labels[r.kind] || "")}${r.username ? ` · @${H(r.username)}` : ""}${r.archived ? ` · ${H(T("в архиве", "archived"))}` : ""}</small></span>${r.unread ? `<em class="plt-unread">${r.unread > 999 ? "999+" : H(r.unread)}</em>` : ""}<span class="plt-chat-mark">${I("check")}</span></button>`;
    }).join("");
  }
  function paintPicker(path, listOnly) {
    const box = $q(`#plt-pane .plt-picker[data-picker="${path}"]`);
    if (!box) return;
    if (!listOnly) {
      const fresh = document.createElement("div");
      fresh.innerHTML = pickerHtml(path, S.picks[path].kinds, S.picks[path].many);
      $q(".plt-picked", box).innerHTML = $q(".plt-picked", fresh).innerHTML;
    }
    $q(".plt-chats", box).innerHTML = chatsHtml(path);
  }
  function loadPickers(root) {
    root.querySelectorAll(".plt-picker").forEach((box) => { const p = S.picks[box.dataset.picker]; if (p && !p.rows && !p.busy && !p.error) fetchDialogs(box.dataset.picker); });
  }
  async function fetchDialogs(path, fresh = false) {
    const p = S.picks[path];
    if (!p) return;
    const key = `${S.d.account}|${p.q.trim().toLowerCase()}`;
    const seq = (p.seq = (p.seq || 0) + 1);
    p.busy = true; p.error = "";
    if (!p.rows) paintPicker(path, true);
    try {
      let rows = fresh ? null : S.dialogs[key];
      if (!rows) {
        const url = `${API}/dialogs?account=${encodeURIComponent(S.d.account)}&limit=300&query=${encodeURIComponent(p.q.trim())}${fresh ? "&fresh=1" : ""}`;
        const response = await fetch(url, { cache: "no-store" });
        const out = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(say(out.error) || `HTTP ${response.status}`);
        rows = out.chats || [];
        S.dialogs[key] = rows;
      }
      if (seq !== p.seq) return;
      p.rows = rows;
    } catch (e) {
      if (seq !== p.seq) return;
      p.error = e.message || String(e); p.rows = null;
    } finally { if (seq === p.seq) p.busy = false; }
    paintPicker(path, true);
  }
  function togglePick(path, id) {
    const p = S.picks[path];
    const row = (p?.rows || []).find((r) => String(r.id) === String(id));
    if (!row) return;
    const ref = { id: row.id, name: row.kind === "saved" ? T("Избранное", "Saved Messages") : row.name, kind: row.kind, username: row.username || "" };
    if (path === "do.to_chat") S.d.do.to_chat = String(S.d.do.to_chat?.id) === String(row.id) ? null : ref;
    else {
      const list = picked(path), at = list.findIndex((c) => String(c.id) === String(row.id));
      if (at >= 0) list.splice(at, 1); else if (list.length < (path === "when.senders" ? 100 : 200)) list.push(ref);
    }
    paintPicker(path, false);
    paintMeta();
  }
  function unpick(path, id) {
    if (path === "do.to_chat") S.d.do.to_chat = null;
    else { const list = picked(path), at = list.findIndex((c) => String(c.id) === String(id)); if (at >= 0) list.splice(at, 1); }
    paintPicker(path, false);
    paintMeta();
  }

  function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    keys.slice(0, -1).forEach((k) => { if (o[k] == null) o[k] = /^\d+$/.test(k) ? [] : {}; o = o[k]; });
    o[keys[keys.length - 1]] = value;
  }
  function toggleDay(path, day) {
    let days = (getPath(S.d, path) || []).slice();
    if (!days.length) days = [0, 1, 2, 3, 4, 5, 6];
    days = days.includes(day) ? days.filter((x) => x !== day) : [...days, day];
    if (!days.length) return;
    setPath(S.d, path, days.length === 7 ? [] : days.sort());
  }
  function insertVar(name) {
    const area = $q('#plt-pane textarea[data-f="do.text"]');
    if (!area) return;
    const at = area.selectionStart ?? area.value.length, end = area.selectionEnd ?? at, text = `{${name}}`;
    area.value = area.value.slice(0, at) + text + area.value.slice(end);
    area.focus();
    area.setSelectionRange(at + text.length, at + text.length);
    area.dispatchEvent(new Event("input", { bubbles: true }));
  }
  async function save() {
    const d = S.d, errs = problems();
    if (errs.length) { go(errs[0].step); return; }
    if (!String(d.name || "").trim()) d.name = autoName(d);
    S.saving = true; paintSummary();
    try {
      const out = await request("automation_save", { rule: serverRule(d) });
      S.status = out;
      S.justSaved = (out.automation || {}).id || d.id;
      delete S.tests[S.justSaved];
      note(S.editing ? T("Автоматизация сохранена", "Automation saved") : T("Автоматизация создана", "Automation created"));
      S.saving = false;
      backToList();
    } catch (e) {
      S.saving = false; paintSummary();
      note(e.message, "err");
    }
  }
  async function dryRun() {
    if (problems().length || S.testing) return;
    S.testing = true; S.test = null;
    if (S.step === "review") paint();
    try { S.test = { result: await request("automation_test", { rule: serverRule(S.d) }) }; } catch (e) { S.test = { error: e.message }; }
    S.testing = false;
    if (S.d && S.step === "review") paint();
  }
  function bindBuilder() {
    const root = $q("#plt-builder");
    if (!root) return;
    let searchTimer = 0;
    root.addEventListener("click", (event) => {
      const el = event.target.closest("[data-plt], [data-plt-step], [data-plt-event], [data-plt-scope], [data-plt-action], [data-plt-target], [data-plt-pick], [data-plt-unpick], [data-plt-day], [data-plt-days], [data-plt-emoji], [data-plt-var], [data-plt-img-add], [data-plt-img-del], [data-plt-img-left], [data-plt-img-right]");
      if (!el || !root.contains(el) || !S.d) return;
      const d = S.d, ds = el.dataset;
      if (ds.pltStep) return go(ds.pltStep);
      if (ds.pltEvent) { setEvent(ds.pltEvent); return paint(`[data-plt-event="${ds.pltEvent}"]`); }
      if (ds.pltScope) { d.when.scope = ds.pltScope; fix(d); return paint(`[data-plt-scope="${ds.pltScope}"]`); }
      if (ds.pltAction) { S.actionTouched = true; setAction(ds.pltAction); return paint(`[data-plt-action="${ds.pltAction}"]`); }
      if (ds.pltTarget) { d.do.to = ds.pltTarget; S.targetTouched = true; fix(d); return paint(`[data-plt-target="${ds.pltTarget}"]`); }
      if (ds.pltPick) return togglePick(el.closest("[data-picker]").dataset.picker, ds.pltPick);
      if (ds.pltUnpick) return unpick(el.closest("[data-picker]").dataset.picker, ds.pltUnpick);
      if (ds.pltDay !== undefined) { toggleDay(ds.path, +ds.pltDay); return paint(); }
      if (ds.pltDays) { setPath(d, ds.path, ds.pltDays === "work" ? [0, 1, 2, 3, 4] : ds.pltDays === "end" ? [5, 6] : []); return paint(); }
      if (ds.pltEmoji) { d.do.emoji = ds.pltEmoji; return paint(`[data-plt-emoji="${ds.pltEmoji}"]`); }
      if (ds.pltVar) return insertVar(ds.pltVar);
      if (ds.pltImgAdd !== undefined) { const inp = root.querySelector("[data-plt-img-pick]"); if (inp) inp.click(); return; }
      if (ds.pltImgDel !== undefined) { (S.d.do.images || []).splice(+ds.pltImgDel, 1); return paint(); }
      if (ds.pltImgLeft !== undefined) { const a = S.d.do.images || [], i = +ds.pltImgLeft; if (i > 0) [a[i - 1], a[i]] = [a[i], a[i - 1]]; return paint(); }
      if (ds.pltImgRight !== undefined) { const a = S.d.do.images || [], i = +ds.pltImgRight; if (i < a.length - 1) [a[i + 1], a[i]] = [a[i], a[i + 1]]; return paint(); }
      const act = ds.plt;
      const ids = stepIds(), at = ids.indexOf(S.step);
      if (act === "back") return backToList();
      if (act === "next") return go(ids[Math.min(ids.length - 1, at + 1)]);
      if (act === "prev") return go(ids[Math.max(0, at - 1)]);
      if (act === "time-add") { d.when.times.push(d.when.times.length ? "18:00" : "09:00"); return paint(); }
      if (act === "time-del") { d.when.times.splice(+ds.i, 1); return paint(); }
      if (act === "dialogs-refresh") { const path = el.closest("[data-picker]").dataset.picker; S.dialogs = {}; return fetchDialogs(path, true); }
      if (act === "save") return save();
      if (act === "dry") return dryRun();
      if (act === "settings") return openSettings();
      if (act === "allow-writes") return allowWrites();
    });
    const edited = (event) => {
      const el = event.target;
      if (!S.d || !root.contains(el)) return;
      if (el.matches("[data-plt-img-pick]")) { uploadImages(el.files); el.value = ""; return; }
      if (el.matches("[data-plt-q]")) {
        if (event.type !== "input") return;
        const path = el.closest("[data-picker]").dataset.picker, p = S.picks[path];
        p.q = el.value;
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => fetchDialogs(path), 250);
        return;
      }
      const path = el.dataset.f;
      if (!path) return;
      if (path === "account") {
        if (event.type === "change" && S.d.account !== el.value) {
          S.d.account = el.value;
          S.d.when.chats = [];
          S.d.when.senders = [];
          S.d.do.to_chat = null;
          S.picks = {};
          S.test = null;
          paint();
        }
        return;
      }
      if (path === "only.window") { S.d.only.from = el.checked ? S.d.only.from || "22:00" : ""; S.d.only.to = el.checked ? S.d.only.to || "08:00" : ""; return paint(); }
      let value = el.type === "checkbox" ? el.checked : el.value;
      if (el.dataset.kind === "int") {
        if (String(value).trim() === "") return;
        const n = Math.round(Number(value));
        if (!Number.isFinite(n)) return;
        value = Math.max(Number(el.min || 0), Math.min(Number(el.max || n), n));
        if (event.type === "change" && String(value) !== el.value) el.value = String(value);
      }
      if (el.dataset.kind === "list") value = String(value).split(/[,\n]/).map((s) => s.trim()).filter(Boolean).slice(0, 50);
      setPath(S.d, path, value);
      if (path === "do.command" || path === "do.model") { paintMeta(); return; }
      if (el.type === "checkbox" && (path === "do.media" || path === "do.toast" || path === "do.always" || path === "do.header" || path === "do.silent" || path === "enabled")) { paintMeta(); return; }
      if (path.startsWith("when.times.") || path === "only.from" || path === "only.to") { if (event.type === "change") paint(); return; }
      paintMeta();
    };
    root.addEventListener("input", edited);
    root.addEventListener("change", edited);
  }

  window.MemuPlanTelegram = Object.freeze({ paneHtml, mount, count, prefetch, openBuilder, refresh: async () => { await load(); paintList(); } });
})();
