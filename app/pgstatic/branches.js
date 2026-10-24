/* Round 35 (branches): ветки и версии ответов в обычном чате.

   МОДЕЛЬ (обратно совместимая, зеркало — app/chat_tree.py):
   * chat.messages — как и раньше, ЛИНЕЙНЫЙ список, но теперь это АКТИВНЫЙ ПУТЬ (активная ветка).
     Всё, что читает chat.messages (запрос к модели, сжатие, токены, экспорт, Telegram, Mini App,
     поиск), автоматически видит только активную ветку. Старые чаты — просто одна ветка.
   * chat.branches = { v: 1, forks: { <key>: { sel, alts: [ {id, at, m}, … ] } } } — необязательное поле.
     key — id сообщения, ПОСЛЕ которого развилка ("^" — развилка в самом начале чата).
     alts — версии продолжения после key; alts[sel].m === null (активная версия живёт в той
     последовательности, где лежит key), у остальных m — массив сообщений (вся их ветка до конца).
     Вложенные развилки внутри неактивной версии хранятся тем же способом (ключ — id сообщения).
   * id сообщений (m.id) раздаются лениво (msgId), только когда нужны.
   Миграции нет: поле появляется при первой новой версии, normalize() чистит осиротевшие развилки.

   ЯДРО (между маркерами @core-start/@core-end) — чистые функции над {messages, branches}; тестируется в Node. */
(function () {
  "use strict";
  // @core-start
  var ROOT = "^";
  var MAX_ALTS = 60;
  function uid(prefix) { return prefix + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
  function msgId(m) { if (m && typeof m === "object" && !m.id) m.id = uid("m-"); return m ? m.id : ""; }
  function forksOf(chat) {
    var b = chat && chat.branches;
    return b && typeof b === "object" && b.forks && typeof b.forks === "object" ? b.forks : null;
  }
  function ensureStore(chat) {
    if (!forksOf(chat)) chat.branches = { v: 1, forks: {} };
    return chat.branches.forks;
  }
  function keyAt(messages, pos) { return pos <= 0 ? ROOT : msgId(messages[pos - 1]); }
  function indexMap(messages) {
    var map = new Map();
    for (var i = 0; i < messages.length; i++) { var m = messages[i]; if (m && m.id) map.set(m.id, i); }
    return map;
  }
  function posOfKey(map, key) { return key === ROOT ? -1 : (map.has(key) ? map.get(key) : undefined); }
  /* Сохраняет messages[pos:] как неактивную версию и начинает новую (пустую, активную).
     Обрезает chat.messages до pos. Возвращает ключ развилки или "" если сохранять нечего. */
  function beginVersion(chat, pos) {
    var messages = Array.isArray(chat.messages) ? chat.messages : [];
    pos = Math.max(0, Math.min(messages.length, pos | 0));
    var suffix = messages.slice(pos);
    if (!suffix.some(function (m) { return m && !m.internal; })) return "";
    var key = keyAt(messages, pos);
    suffix.forEach(msgId);
    var forks = ensureStore(chat);
    var fork = forks[key];
    if (!fork || !Array.isArray(fork.alts) || !fork.alts.length) {
      fork = forks[key] = { sel: 0, alts: [{ id: uid("v-"), at: +(suffix[0].createdAt || Date.now()), m: null }] };
    }
    fork.sel = Math.max(0, Math.min(fork.alts.length - 1, fork.sel | 0));
    fork.alts[fork.sel].m = suffix;
    fork.alts.push({ id: uid("v-"), at: Date.now(), m: null });
    fork.sel = fork.alts.length - 1;
    while (fork.alts.length > MAX_ALTS) { fork.alts.splice(0, 1); fork.sel -= 1; }
    chat.messages = messages.slice(0, pos);
    return key;
  }
  /* Переключает развилку key (она должна быть на активном пути) на версию j. */
  function switchTo(chat, key, j) {
    var forks = forksOf(chat); var fork = forks && forks[key];
    if (!fork || !Array.isArray(fork.alts)) return false;
    var messages = Array.isArray(chat.messages) ? chat.messages : [];
    var p = posOfKey(indexMap(messages), key);
    if (p === undefined || j < 0 || j >= fork.alts.length || j === fork.sel) return false;
    var next = Array.isArray(fork.alts[j].m) ? fork.alts[j].m : [];
    fork.alts[fork.sel].m = messages.slice(p + 1);
    fork.alts[j].m = null;
    fork.sel = j;
    chat.messages = messages.slice(0, p + 1).concat(next);
    return true;
  }
  function locate(chat, id) {
    var forks = forksOf(chat); if (!forks || !id) return null;
    for (var key in forks) {
      var alts = forks[key].alts || [];
      for (var a = 0; a < alts.length; a++) {
        var arr = alts[a].m;
        if (Array.isArray(arr)) for (var k = 0; k < arr.length; k++) if (arr[k] && arr[k].id === id) return { key: key, alt: a, offset: k };
      }
    }
    return null;
  }
  /* Делает активной ветку, в которой лежит сообщение id (поиск, карта веток). */
  function reveal(chat, id, depth) {
    depth = depth || 0;
    if (depth > 200 || !id) return false;
    if ((chat.messages || []).some(function (m) { return m && m.id === id; })) return true;
    var loc = locate(chat, id);
    if (!loc) return false;
    if (loc.key !== ROOT && !(chat.messages || []).some(function (m) { return m && m.id === loc.key; })) {
      if (!reveal(chat, loc.key, depth + 1)) return false;
    }
    return switchTo(chat, loc.key, loc.alt);
  }
  /* Версии на активном пути: Map(индекс сообщения → {key, n, cur}). O(n). */
  function info(chat) {
    var out = new Map();
    var forks = forksOf(chat); if (!forks) return out;
    var messages = chat.messages || [];
    var map = null;
    for (var key in forks) {
      var fork = forks[key];
      if (!fork || !Array.isArray(fork.alts) || fork.alts.length < 2) continue;
      if (!map) map = indexMap(messages);
      var p = posOfKey(map, key);
      if (p === undefined || p + 1 >= messages.length) continue;
      out.set(p + 1, { key: key, n: fork.alts.length, cur: fork.sel | 0 });
    }
    return out;
  }
  function stats(chat) {
    var forks = forksOf(chat); var res = { forks: 0, versions: 0, hidden: 0 };
    if (!forks) return res;
    for (var key in forks) {
      var alts = forks[key].alts || [];
      if (alts.length < 2) continue;
      res.forks += 1; res.versions += alts.length - 1;
      alts.forEach(function (a) { if (Array.isArray(a.m)) res.hidden += a.m.length; });
    }
    return res;
  }
  /* Все сообщения дерева: [{m, active, key, alt}] — активные первыми. */
  function allMessages(chat) {
    var out = (chat.messages || []).map(function (m, i) { return { m: m, active: true, index: i }; });
    var forks = forksOf(chat); if (!forks) return out;
    for (var key in forks) (forks[key].alts || []).forEach(function (a, ai) {
      if (Array.isArray(a.m)) a.m.forEach(function (m) { out.push({ m: m, active: false, key: key, alt: ai }); });
    });
    return out;
  }
  /* Чистка: осиротевшие развилки, пустые версии, повреждённые sel. Возвращает true, если что-то изменилось. */
  function normalize(chat) {
    var forks = forksOf(chat);
    if (!forks) { if (chat && chat.branches !== undefined && chat.branches !== null) { delete chat.branches; return true; } return false; }
    var changed = false;
    for (var round = 0; round < 4; round++) {
      var ids = new Set();
      (chat.messages || []).forEach(function (m) { if (m && m.id) ids.add(m.id); });
      for (var k0 in forks) (forks[k0].alts || []).forEach(function (a) { if (Array.isArray(a.m)) a.m.forEach(function (m) { if (m && m.id) ids.add(m.id); }); });
      var map = indexMap(chat.messages || []);
      var again = false;
      for (var key in forks) {
        var fork = forks[key];
        if (!fork || !Array.isArray(fork.alts) || (key !== ROOT && !ids.has(key))) { delete forks[key]; changed = again = true; continue; }
        var sel = Math.max(0, Math.min(fork.alts.length - 1, fork.sel | 0));
        if (sel !== fork.sel) { fork.sel = sel; changed = true; }
        // ровно одна «живая» версия (m === null) — выбранная
        fork.alts = fork.alts.filter(function (a, i) {
          if (!a || typeof a !== "object") { changed = true; if (i < fork.sel) fork.sel -= 1; return false; }
          if (i === fork.sel) return true;
          var ok = Array.isArray(a.m) && a.m.some(function (m) { return m && !m.internal; });
          if (!ok) { changed = true; if (i < fork.sel) fork.sel -= 1; }
          return ok;
        });
        if (!fork.alts.length) { delete forks[key]; changed = again = true; continue; }
        fork.sel = Math.max(0, Math.min(fork.alts.length - 1, fork.sel | 0));
        if (fork.alts[fork.sel].m !== null) { fork.alts[fork.sel].m = null; changed = true; }
        var p = posOfKey(map, key);
        // Активная версия опустела (удалили её сообщения) — показываем соседнюю, а пустую убираем.
        if (p !== undefined && p + 1 >= (chat.messages || []).length && fork.alts.length > 1) {
          fork.alts.splice(fork.sel, 1);
          var j = Math.min(fork.sel, fork.alts.length - 1);
          chat.messages = (chat.messages || []).concat(fork.alts[j].m || []);
          fork.alts[j].m = null; fork.sel = j; changed = again = true;
        }
        if (fork.alts.length < 2) { delete forks[key]; changed = again = true; }
      }
      if (!again) break;
    }
    if (!Object.keys(forks).length) { delete chat.branches; changed = true; }
    return changed;
  }
  /* Дерево для «Карты веток». Узлы: {type:"msg", m, active, index} | {type:"fork", key, sel, alts:[{alt, active, at, nodes}]} */
  function tree(chat) {
    var forks = forksOf(chat) || {};
    var guard = 0;
    function seq(list, parentKey, active, skipFirst, base) {
      var nodes = [];
      for (var i = 0; i < list.length; i++) {
        if (++guard > 20000) break;
        var key = i === 0 ? parentKey : (list[i - 1] && list[i - 1].id);
        var fork = key ? forks[key] : null;
        if (fork && Array.isArray(fork.alts) && fork.alts.length > 1 && !(i === 0 && skipFirst)) {
          var rest = list.slice(i);
          nodes.push({ type: "fork", key: key, sel: fork.sel | 0, alts: fork.alts.map(function (a, ai) {
            var isSel = ai === (fork.sel | 0);
            var msgs = isSel ? rest : (Array.isArray(a.m) ? a.m : []);
            return { alt: ai, active: active && isSel, at: a.at || 0, count: msgs.length,
              nodes: seq(msgs, key, active && isSel, true, active && isSel ? base + i : -1) };
          }) });
          return nodes;
        }
        nodes.push({ type: "msg", m: list[i], active: active, index: active && base >= 0 ? base + i : -1 });
      }
      return nodes;
    }
    return seq(chat.messages || [], ROOT, true, false, 0);
  }
  var core = { ROOT: ROOT, uid: uid, msgId: msgId, keyAt: keyAt, beginVersion: beginVersion, switchTo: switchTo,
    locate: locate, reveal: reveal, info: info, stats: stats, allMessages: allMessages, normalize: normalize, tree: tree };
  // @core-end
  if (typeof window === "undefined") { if (typeof module !== "undefined") module.exports = core; return; }

  /* ------------------------------------------------------------------ UI */
  function tr(ru, en) { return (typeof PG_LANG !== "undefined" && PG_LANG === "en") ? en : ru; }
  function escHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  function ic(name) { return typeof icon === "function" ? icon(name) : ""; }
  var SVG_MAP = '<span class="ui-icon"><svg viewBox="0 0 24 24"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M6 7v10M6 9c0 3 4 3 10 3"/></svg></span>';
  var SVG_L = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>';
  var SVG_R = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

  function chatRecord() { try { return typeof currentChat === "function" ? currentChat() : null; } catch (e) { return null; } }
  function busy() { try { return typeof generationJobs !== "undefined" && generationJobs.has(S.currentId); } catch (e) { return false; } }
  // Кэш info() для активного чата: renderMessages зовётся на каждый токен стрима.
  var cache = { chat: null, len: -1, sig: "", value: new Map() };
  function forkSig(c) {
    var f = forksOf(c); if (!f) return "";
    var s = ""; for (var k in f) s += k + ":" + (f[k].sel | 0) + "/" + ((f[k].alts || []).length) + ";";
    return s;
  }
  function currentInfo() {
    var c = chatRecord();
    if (!c || !forksOf(c)) return null;
    var view = { messages: S.messages, branches: c.branches };
    var sig = forkSig(c);
    if (cache.chat === c && cache.len === S.messages.length && cache.sig === sig && cache.first === S.messages[0]) return cache.value;
    cache = { chat: c, len: S.messages.length, sig: sig, first: S.messages[0], value: info(view) };
    return cache.value;
  }
  /* HTML переключателя «‹ 2/3 ›» под сообщением i (пусто, если версий нет). */
  function switchHtml(map, i) {
    if (!map) return "";
    var it = map.get(i); if (!it) return "";
    var role = (S.messages[i] || {}).role === "user" ? "user" : "assistant";
    var label = role === "user" ? tr("Версии сообщения", "Message versions") : tr("Версии ответа", "Answer versions");
    var dis = busy() ? " disabled" : "";
    return '<span class="br-switch" role="group" aria-label="' + escHtml(label) + '" data-br-key="' + escHtml(it.key) + '" data-br-index="' + i + '">' +
      '<button type="button" class="br-step" data-br-step="-1"' + (it.cur <= 0 ? " disabled" : dis) + ' aria-label="' + escHtml(tr("Предыдущая версия", "Previous version")) + '" title="' + escHtml(tr("Предыдущая версия", "Previous version")) + ' (Ctrl+Shift+←)">' + SVG_L + '</button>' +
      '<span class="br-count" title="' + escHtml(label) + '"><b>' + (it.cur + 1) + '</b>/' + it.n + '</span>' +
      '<button type="button" class="br-step" data-br-step="1"' + (it.cur >= it.n - 1 ? " disabled" : dis) + ' aria-label="' + escHtml(tr("Следующая версия", "Next version")) + '" title="' + escHtml(tr("Следующая версия", "Next version")) + ' (Ctrl+Shift+→)">' + SVG_R + '</button>' +
      (role === "assistant" && window.MemuCompare ? window.MemuCompare.switchButton(i) : "") +   // Round 37: «⇆ Сравнить» (compare.js)
      '<button type="button" class="br-step br-mapbtn" data-br-map aria-label="' + escHtml(tr("Карта веток", "Branch map")) + '" title="' + escHtml(tr("Карта веток", "Branch map")) + ' (Ctrl+Shift+B)">' + SVG_MAP + '</button></span>';
  }
  function emit(detail) { try { window.dispatchEvent(new CustomEvent("memu:branch-switch", { detail: detail })); } catch (e) {} }
  /* Сохраняет текущее продолжение с позиции pos как версию (перед перегенерацией/правкой).
     Сам S.messages НЕ обрезает — вызывающий код делает это сам, как и раньше. */
  function keepVersion(pos) {
    var c = chatRecord();
    if (!c || !Array.isArray(S.messages)) return "";
    // Work / MemuCode answers change project files: an old «version» would not bring the files back, so there
    // regenerate keeps replacing the answer as before (versions are a feature of the ordinary chat).
    try { if (typeof isProjectMode === "function" && isProjectMode()) return ""; } catch (e) {}
    // A failed / empty answer («Ответа нет» → «Повторить») is not worth keeping as a version.
    var rest = S.messages.slice(pos).filter(function (m) { return m && !m.internal; });
    if (!rest.length || rest.every(function (m) { return m.role === "assistant" && !String(m.content || "").trim() && !(m.images || []).filter(function (u) { return u && u !== "__skeleton__"; }).length && !m.media; })) return "";
    var view = { messages: S.messages, branches: c.branches };
    var key = beginVersion(view, pos);
    if (key) { c.branches = view.branches; cache.chat = null; }
    return key;
  }
  function anchorTop(index) {
    var node = document.querySelector('#messages [data-msg="' + index + '"]');
    return node ? node.getBoundingClientRect().top : null;
  }
  function restoreAnchor(index, top) {
    if (top == null) return;
    var node = document.querySelector('#messages [data-msg="' + index + '"]');
    var box = node && node.closest(".chat-scroll, #chat-view-wrap, .messages-scroll") || (node && node.parentElement && node.parentElement.parentElement);
    if (!node || !box) return;
    box.scrollTop += node.getBoundingClientRect().top - top;
  }
  function apply(mutator, focusIndex) {
    var c = chatRecord();
    if (!c) return false;
    if (busy()) { if (typeof toast === "function") toast(tr("Дождитесь окончания ответа или нажмите «Стоп»", "Wait for the answer to finish or press Stop"), "warn"); return false; }
    var view = { messages: S.messages, branches: c.branches };
    var before = S.messages.slice();
    var top = focusIndex != null ? anchorTop(focusIndex) : null;
    if (!mutator(view)) return false;
    normalize(view);
    // первый индекс, где путь изменился — сжатая сводка дальше него уже о другой ветке
    var diff = 0; while (diff < before.length && diff < view.messages.length && before[diff] === view.messages[diff]) diff++;
    if ((+S.contextSummaryThrough || 0) > diff) { S.contextSummary = ""; S.contextSummaryThrough = 0; }
    S.messages = view.messages;
    if (view.branches) c.branches = view.branches; else delete c.branches;
    c.messages = S.messages;
    cache.chat = null;
    if (typeof persistChat === "function") persistChat();
    if (typeof renderMessages === "function") renderMessages();
    if (focusIndex != null) restoreAnchor(focusIndex, top);
    emit({ chatId: S.currentId, from: diff });
    return true;
  }
  function step(key, index, dir) {
    var c = chatRecord(); var f = forksOf(c); var fork = f && f[key];
    if (!fork) return;
    var j = (fork.sel | 0) + dir;
    apply(function (view) { return switchTo(view, key, j); }, index);
  }
  function revealMessage(id, opts) {
    var ok = apply(function (view) { return reveal(view, id); }, null);
    var idx = S.messages.findIndex(function (m) { return m && m.id === id; });
    if (idx >= 0 && (!opts || opts.scroll !== false)) scrollToIndex(idx);
    return ok || idx >= 0;
  }
  function scrollToIndex(idx) {
    setTimeout(function () {
      var node = document.querySelector('#messages [data-msg="' + idx + '"]');
      if (!node) return;
      node.scrollIntoView({ block: "center", behavior: "smooth" });
      node.classList.remove("br-flash"); void node.offsetWidth; node.classList.add("br-flash");
      setTimeout(function () { node.classList.remove("br-flash"); }, 1600);
    }, 40);
  }
  /* Перед открытием чата: чистка без сохранения (сохранится с ближайшим persist). */
  function normalizeChat(c) { if (c && forksOf(c)) { var v = { messages: c.messages || [], branches: c.branches }; if (normalize(v)) { c.messages = v.messages; if (v.branches) c.branches = v.branches; else delete c.branches; return true; } } return false; }

  /* --------------------------------------------------------- Карта веток */
  function preview(m, n) {
    var text = String((m && m.content) || "").replace(/```[\s\S]*?```/g, " [код] ").replace(/<[^>]+>/g, " ").replace(/[#*_`>|]/g, "").replace(/\s+/g, " ").trim();
    if (!text && m && (m.attachments || []).length) text = tr("Вложение: ", "Attachment: ") + (m.attachments[0].name || "");
    if (!text && m && (m.images || []).length) text = tr("Изображение", "Image");
    if (!text) text = tr("(пусто)", "(empty)");
    return text.length > n ? text.slice(0, n - 1).trimEnd() + "…" : text;
  }
  function fmtTime(ts) {
    if (!ts) return "";
    try { var d = new Date(+ts); return d.toLocaleString(tr("ru-RU", "en-US"), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (e) { return ""; }
  }
  function msgRow(node) {
    var m = node.m || {};
    if (m.internal) return "";
    var user = m.role === "user";
    var id = msgId(m);
    var cls = "br-node " + (user ? "br-user" : "br-ai") + (node.active ? " br-active" : "");
    var who = user ? tr("Вы", "You") : (m.model && typeof modelDisplayName === "function" ? modelDisplayName(m.model) : "MemuAI");
    return '<button type="button" class="' + cls + '" data-br-goto="' + escHtml(id) + '" title="' + escHtml(tr("Открыть это место", "Open this point")) + '">' +
      '<span class="br-dot" aria-hidden="true"></span><span class="br-who">' + escHtml(who) + '</span>' +
      '<span class="br-text">' + escHtml(preview(m, 140)) + '</span>' +
      (m.createdAt ? '<span class="br-time">' + escHtml(fmtTime(m.createdAt)) + '</span>' : "") + '</button>';
  }
  function renderNodes(nodes, depth) {
    var html = "", run = [];
    function flush() {
      var visible = run.filter(function (n) { return !(n.m && n.m.internal); });
      if (visible.length > 7) {
        html += visible.slice(0, 2).map(msgRow).join("");
        html += '<details class="br-fold"><summary>' + escHtml(tr("ещё сообщений: ", "more messages: ") + (visible.length - 4)) + '</summary>' + visible.slice(2, -2).map(msgRow).join("") + '</details>';
        html += visible.slice(-2).map(msgRow).join("");
      } else html += visible.map(msgRow).join("");
      run = [];
    }
    nodes.forEach(function (n) {
      if (n.type === "msg") { run.push(n); return; }
      flush();
      var head = n.alts[0] && n.alts[0].nodes[0] && n.alts[0].nodes[0].m;
      var kind = head && head.role === "user" ? tr("Версии сообщения", "Message versions") : tr("Версии ответа", "Answer versions");
      html += '<div class="br-fork" style="--d:' + Math.min(depth, 8) + '"><div class="br-fork-head">' + SVG_MAP + '<span>' + escHtml(kind) + ' · ' + n.alts.length + '</span></div><div class="br-alts">';
      n.alts.forEach(function (a) {
        var first = (a.nodes[0] && a.nodes[0].m) || null;
        html += '<div class="br-alt' + (a.active ? " br-alt-active" : "") + (n.sel === a.alt ? " br-alt-sel" : "") + '">' +
          '<div class="br-alt-head"><span class="br-badge">' + escHtml(tr("Версия ", "Version ") + (a.alt + 1)) + '</span>' +
          (a.active ? '<span class="br-tag">' + escHtml(tr("сейчас в чате", "in the chat now")) + '</span>' : (n.sel === a.alt ? '<span class="br-tag br-tag-soft">' + escHtml(tr("выбрана здесь", "selected here")) + '</span>' : "")) +
          '<span class="br-alt-meta">' + escHtml(fmtTime((first && first.createdAt) || a.at)) + ' · ' + escHtml(tr("сообщений: ", "messages: ") + a.count) + '</span></div>' +
          '<div class="br-alt-body">' + renderNodes(a.nodes, depth + 1) + '</div></div>';
      });
      html += '</div></div>';
    });
    flush();
    return html;
  }
  function openMap() {
    var c = chatRecord();
    if (!c || !(S.messages || []).length) { if (typeof toast === "function") toast(tr("В этом чате пока нет сообщений", "This chat has no messages yet"), "warn"); return; }
    normalizeChat(c);
    var view = { messages: S.messages, branches: c.branches };
    var st = stats(view);
    var nodes = tree(view);
    var body = renderNodes(nodes, 0);
    var sub = st.forks
      ? tr("Развилок: " + st.forks + ", других версий: " + st.versions + ". Нажмите на любое сообщение, чтобы перейти в его ветку.", "Forks: " + st.forks + ", other versions: " + st.versions + ". Click any message to switch to its branch.")
      : tr("В этом чате одна ветка. Перегенерация ответа или правка своего сообщения создадут новую версию — старая останется здесь.", "This chat has a single branch. Regenerating an answer or editing your message creates a new version — the old one stays here.");
    panel('<h2>' + SVG_MAP + ' ' + escHtml(tr("Карта веток", "Branch map")) + '</h2><p class="sub br-map-sub">' + escHtml(sub) + '</p>' +
      '<div class="br-legend"><span><i class="br-dot br-dot-on"></i>' + escHtml(tr("активная ветка", "active branch")) + '</span><span><i class="br-dot"></i>' + escHtml(tr("другие версии", "other versions")) + '</span></div>' +
      '<div class="br-map" tabindex="0">' + body + '</div>', "br-map-panel");
    var host = document.querySelector("#panel .br-map");
    if (host) {
      host.addEventListener("click", function (e) {
        var go = e.target.closest("[data-br-goto]"); if (!go) return;
        var id = go.getAttribute("data-br-goto");
        if (busy()) { toast(tr("Дождитесь окончания ответа или нажмите «Стоп»", "Wait for the answer to finish or press Stop"), "warn"); return; }
        if (typeof closePanel === "function") closePanel();
        revealMessage(id);
      });
      var nodesActive = host.querySelectorAll(".br-node.br-active"); var lastActive = nodesActive[nodesActive.length - 1];
      if (lastActive && lastActive.scrollIntoView) setTimeout(function () { lastActive.scrollIntoView({ block: "nearest" }); }, 30);
    }
  }

  /* ------------------------------------------------------------- события */
  document.addEventListener("click", function (e) {
    var stepBtn = e.target.closest && e.target.closest(".br-switch [data-br-step]");
    if (stepBtn) {
      e.preventDefault(); e.stopPropagation();
      var sw = stepBtn.closest(".br-switch");
      step(sw.getAttribute("data-br-key"), +sw.getAttribute("data-br-index"), +stepBtn.getAttribute("data-br-step"));
      return;
    }
    var mapBtn = e.target.closest && e.target.closest("[data-br-map]");
    if (mapBtn) { e.preventDefault(); e.stopPropagation(); var pop = mapBtn.closest(".msg-more"); if (pop) pop.classList.remove("open"); openMap(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented) return;
    var tag = (e.target && e.target.tagName) || "";
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (e.target && e.target.isContentEditable);
    if (e.ctrlKey && e.shiftKey && !e.altKey && (e.code === "KeyB")) {
      if (document.querySelector("#panel-back.visible")) return;
      if (!(S.messages || []).length) return;
      e.preventDefault(); openMap(); return;
    }
    if (e.ctrlKey && e.shiftKey && !e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight") && !typing) {
      if (document.querySelector("#panel-back.visible")) return;
      var map = currentInfo(); if (!map || !map.size) return;
      // последняя развилка на экране (ближайшая к низу видимой области), иначе — последняя в чате
      var idx = -1;
      var vh = window.innerHeight;
      map.forEach(function (v, i) {
        var node = document.querySelector('#messages [data-msg="' + i + '"]');
        if (!node) return;
        var r = node.getBoundingClientRect();
        if (r.top < vh && r.bottom > 0 && i > idx) idx = i;
      });
      if (idx < 0) map.forEach(function (v, i) { if (i > idx) idx = i; });
      var it = map.get(idx); if (!it) return;
      e.preventDefault();
      step(it.key, idx, e.key === "ArrowLeft" ? -1 : 1);
    }
  });

  window.MemuBranches = Object.assign({}, core, {
    /* Контракт для других зон (canvas, mentions, bigdocs, askuser) — см. changelog/round35/branches.md */
    activePath: function () { return S.messages; },
    chatTree: function (chat) { var c = chat || chatRecord(); return c ? { messages: c === chatRecord() ? S.messages : (c.messages || []), branches: c.branches } : null; },
    mapIcon: SVG_MAP, currentInfo: currentInfo, switchHtml: switchHtml, keepVersion: keepVersion, step: step, openMap: openMap,
    revealMessage: revealMessage, normalizeChat: normalizeChat, preview: preview,
    stats: function (chat) { return stats(chat || { messages: S.messages, branches: (chatRecord() || {}).branches }); },
  });
})();
