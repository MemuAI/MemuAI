/* ====================================================================== r37: AI co-writer
   Inlined into main.js at the «@memu-part:assist.js» marker (after assist-prompts.js), sharing main.js's scope.

   The AI helps, the author writes. Three entry points:
   1. ✨ next to every text field of the character editor and of a lorebook entry (shown on hover / focus, Ctrl+J
      in the field): improve · continue · expand · shorten · fix · livelier / another tone · 3 variants · fill from
      the other fields · own request; with a selection only the selected fragment is rewritten. The answer streams
      into a preview card under the field with a word diff, editable before it is applied; Accept / Insert at the
      cursor / Replace the selection / Another variant / Discard; Esc stops or closes. Applying goes through
      execCommand("insertText"), so Ctrl+Z in the field undoes it.
   2. The «Co-writer» side panel of the editor (and of the lorebook editor): a chat that sees the whole card (and the
      lorebook), whose proposals for a field (<field name="…">) or a new entry (<entry …>) apply in one click through
      the same preview; «Review» — local checks (macros, tokens, format) plus a model review, each issue with «Fix».
   3. Lorebook tools: keyword suggestions as chips, entry from a selected fragment, «find gaps» (things mentioned but
      not described → proposed entries one by one), contradictions between entries.
   The model is Settings → Connection → «AI co-writer» (empty = the chat model). Nothing here runs until used. */

var ASSIST = { active: null, keyBound: false };

function assistModel() {
  var a = S.settings.assist || {};
  if (a.model && modelRow(a.model)) return a.model;
  return S.settings.model || fallbackModel();
}
function assistModelName(id) { var m = modelRow(id); return (m && m.name) || shortModel(id); }
function assistLang() { return (S.settings.assist || {}).lang || "auto"; }
function assistEnabled() { return (S.settings.assist || {}).buttons !== false; }

// Model answer → field text: drop <think>, a code fence around the whole answer, a «Here is …:» preface, wrapping quotes.
function assistClean(text, partial) {
  var s = String(text || "");
  var cut = E.findLeakedToken ? E.findLeakedToken(s) : -1;
  if (cut >= 0) s = s.slice(0, cut);
  s = s.replace(/<think>[\s\S]*?<\/think>/gi, "");
  if (partial) s = s.replace(/<think>[\s\S]*$/i, "");
  s = s.replace(/^\s+/, "");
  var fence = /^```[a-z]*\n([\s\S]*?)(?:\n```\s*)?$/i.exec(s);
  if (fence) s = fence[1];
  s = s.replace(/^(?:here(?:'s| is)[^\n]{0,80}:|вот[^\n]{0,80}:)\s*\n+/i, "");
  if (!partial) {
    s = s.replace(/\s+$/, "");
    var q = /^(["«“])([\s\S]*)(["»”])$/.exec(s);
    if (q && q[2].indexOf(q[1]) < 0 && q[2].indexOf(q[3]) < 0) s = q[2];
  }
  return s;
}

// One streamed, cancellable completion → {promise, cancel}.
function assistRun(messages, opts) {
  opts = opts || {};
  var model = assistModel();
  if (!model) return { promise: Promise.reject(new Error(t("models_unavailable"))), cancel: function () {} };
  var raw = "", cancelled = false, frame = 0;
  var params = { temperature: opts.temperature === undefined ? 0.8 : opts.temperature, max_tokens: opts.max_tokens || 2500, prompt_caching: true };
  // Editing help should feel instant: a reasoning model thinks as little as it allows (live: qwen3.8-flash took
  // ~50 s on «Improve» with its default reasoning). The review asks for a little more.
  var levels = (modelRow(model) || {}).reasoning_levels || [];
  var want = opts.effort || "off";
  if (levels.indexOf(want) >= 0) { params.effort = want; if (want === "off") params.thinking = false; }
  else if (levels.indexOf("low") >= 0) params.effort = "low";
  var job = Memu.complete({ model: model, messages: messages, params: params }, {
    onDelta: function (chunk) {
      raw += chunk;
      if (opts.onText && !frame) frame = requestAnimationFrame(function () { frame = 0; opts.onText(opts.json ? raw : assistClean(raw, true)); });
    }
  });
  var promise = job.then(function (result) {
    var text = result && typeof result.text === "string" && result.text.length >= raw.length ? result.text : raw;
    text = opts.json ? text : assistClean(text);
    if (!String(text).trim()) throw new Error(t("empty_answer"));
    return text;
  }, function (error) { throw cancelled ? new Error(t("ai_stopped")) : error; });
  return { promise: promise, cancel: function () { cancelled = true; try { job.cancel(); } catch (e) { /* finished */ } } };
}

// A JSON answer → the list under `key`. Models sometimes drop the wrapper object and answer with a bare array
// (or a single item), so all three shapes are accepted.
function assistJsonList(raw, key) {
  var s = String(raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "");
  var fence = /```(?:json)?\s*([\s\S]*?)```/i.exec(s);
  if (fence) s = fence[1];
  var a = s.indexOf("["), o = s.indexOf("{");
  if (a >= 0 && (o < 0 || a < o)) {
    try { var arr = JSON.parse(s.slice(a, s.lastIndexOf("]") + 1).replace(/,\s*([}\]])/g, "$1")); if (Array.isArray(arr)) return arr; } catch (e) { /* object below */ }
  }
  var obj = aiParseJson(raw);
  if (Array.isArray(obj[key])) return obj[key];
  if (typeof obj[key] === "string") return aiList(obj[key]);
  return obj.title || obj.content ? [obj] : [];
}

/* ---------------------------------------------------------------- word diff */
function assistTokens(s) { return String(s || "").match(/\s+|[^\s]+/g) || []; }
// → [[op, text]] with op "=", "+", "-"; null when the texts are too long for a quick diff.
function assistDiff(a, b) {
  var x = assistTokens(a), y = assistTokens(b);
  var pre = 0;
  while (pre < x.length && pre < y.length && x[pre] === y[pre]) pre++;
  var suf = 0;
  while (suf < x.length - pre && suf < y.length - pre && x[x.length - 1 - suf] === y[y.length - 1 - suf]) suf++;
  var xs = x.slice(pre, x.length - suf), ys = y.slice(pre, y.length - suf);
  var n = xs.length, m = ys.length;
  if (n * m > 2500000) return null;
  var w = m + 1, dp = new Uint16Array((n + 1) * w);
  for (var i = n - 1; i >= 0; i--) for (var j = m - 1; j >= 0; j--) {
    dp[i * w + j] = xs[i] === ys[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
  }
  var out = [];
  function push(op, s) { if (out.length && out[out.length - 1][0] === op) out[out.length - 1][1] += s; else out.push([op, s]); }
  if (pre) push("=", x.slice(0, pre).join(""));
  i = 0; j = 0;
  while (i < n && j < m) {
    if (xs[i] === ys[j]) { push("=", xs[i]); i++; j++; }
    else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) { push("-", xs[i]); i++; }
    else { push("+", ys[j]); j++; }
  }
  while (i < n) push("-", xs[i++]);
  while (j < m) push("+", ys[j++]);
  if (suf) push("=", x.slice(x.length - suf).join(""));
  return out;
}
function assistDiffNode(a, b) {
  var ops = assistDiff(a, b);
  var box = h("div", { class: "ch-as-diff" });
  if (!ops) { box.textContent = b; return box; }
  ops.forEach(function (op) { box.appendChild(op[0] === "=" ? document.createTextNode(op[1]) : h(op[0] === "+" ? "ins" : "del", { text: op[1] })); });
  return box;
}

/* ---------------------------------------------------------------- applying text to a field (undoable) */
function assistInsert(el, start, end, text) {
  el.focus();
  try { el.setSelectionRange(start, end); } catch (e) { /* number inputs */ }
  var ok = false;
  try { ok = document.execCommand("insertText", false, text); } catch (e) { ok = false; }
  if (!ok || el.value.slice(start, start + text.length) !== text) {
    el.setRangeText(text, start, end, "end");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.classList.remove("ch-as-flash"); void el.offsetWidth; el.classList.add("ch-as-flash");
}

/* ---------------------------------------------------------------- ✨ button next to a field */
// spec = {field, label, ctx() → {card, world, entry}, host?}
function assistButton(el, spec) {
  if (!assistEnabled() || !el) return null;
  el.setAttribute("data-assist", spec.field);
  el.__assist = spec;
  el.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.code === "KeyJ") { e.preventDefault(); assistMenu(btn, el, spec); }
  });
  var btn = h("button", { class: "ch-as-btn", type: "button", title: t("as_btn_title"), "aria-label": t("as_btn_title"), html: icon("sparkle", 14) });
  // The field keeps its selection while the button is pressed.
  btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
  btn.addEventListener("click", function (e) { e.stopPropagation(); assistMenu(btn, el, spec); });
  return btn;
}
function assistSelection(el) {
  var s = el.selectionStart, e = el.selectionEnd;
  if (typeof s !== "number" || s === e) return null;
  var text = el.value.slice(s, e);
  return text.trim() ? { start: s, end: e, text: text } : null;
}
function assistMenu(anchor, el, spec) {
  var sel = assistSelection(el);
  var empty = !el.value.trim();
  function run(action, extra) { assistPreview(el, spec, Object.assign({ action: action, selection: sel }, extra || {})); }
  function item(action, labelKey, extra) {
    var a = ASSIST_ACTIONS[action];
    return { icon: a.icon, label: t(labelKey), disabled: a.needsText && empty, onClick: function () { run(action, extra); } };
  }
  var items = [{ head: sel ? t("as_head_selection", { n: fmtNum(sel.text.length) }) : spec.label }];
  if (empty && !sel) items.push(item("fill", "as_fill"));
  items.push(item("improve", "as_improve"));
  if (!sel) items.push(item("continue", "as_continue"));
  items.push(item("expand", "as_expand"), item("shorten", "as_shorten"), item("fix", "as_fix"));
  items.push({ icon: "palette", label: t("as_tone"), hint: "›", disabled: empty, onClick: function () { assistToneMenu(anchor, run); } });
  items.push(item("variants", "as_variants"));
  items.push({ icon: "edit", label: t("as_custom"), onClick: function () {
    promptBox(t("as_custom_title"), "", { multiline: true, icon: "sparkle", hint: t("as_custom_hint") }).then(function (v) { if (v && v.trim()) run("custom", { instruction: v.trim() }); });
  } });
  if (sel && spec.field !== "wi_content" && spec.field !== "name") {
    items.push({ sep: true });
    items.push({ icon: "book", label: t("as_entry_from_sel"), onClick: function () { assistEntryFromSelection(sel.text, spec); } });
  }
  if (spec.host) { items.push({ sep: true }); items.push({ icon: "chat", label: t("as_open_panel"), hint: "", onClick: function () { assistPanel(spec.host); } }); }
  popMenu(anchor, items);
}
function assistToneMenu(anchor, run) {
  popMenu(anchor, [{ head: t("as_tone") }].concat(ASSIST_TONES.map(function (k) {
    return { icon: k === "tone_vivid" ? "zap" : "palette", label: t("as_" + k), onClick: function () {
      if (k === "tone_vivid") run("vivid"); else run("tone", { instruction: t("as_" + k) });
    } };
  }), [{ sep: true }, { icon: "edit", label: t("as_tone_custom"), onClick: function () {
    promptBox(t("as_tone_custom"), "", { icon: "palette", hint: t("as_tone_custom_hint") }).then(function (v) { if (v && v.trim()) run("tone", { instruction: v.trim() }); });
  } }]));
}

/* ---------------------------------------------------------------- the preview card under a field */
// a = {action, selection, instruction, given?: ready text (from the panel), title?}
function assistPreview(el, spec, a) {
  assistClosePreview();
  var ctx = spec.ctx ? spec.ctx() : {};
  var original = el.value;
  var sel = a.selection && original.slice(a.selection.start, a.selection.end) === a.selection.text ? a.selection : null;
  var caret = typeof el.selectionEnd === "number" ? el.selectionEnd : original.length;
  var base = sel ? sel.text : original;
  var state = { text: "", variants: null, pick: 0, running: false, job: null, view: "diff" };
  var title = a.title || t(a.action === "fix_issue" ? "as_fix_issue" : "as_" + (a.action === "tone" ? "tone" : a.action));
  var card = h("div", { class: "ch-as-pv", role: "region", "aria-label": t("as_preview") });
  var stat = h("span", { class: "ch-as-pv-stat" });
  var stopBtn = h("button", { class: "ch-mini", type: "button", title: t("as_stop"), html: icon("stop", 13), onclick: function () { if (state.job) state.job.cancel(); } });
  card.appendChild(h("div", { class: "ch-as-pv-head" }, [
    h("span", { class: "ch-as-pv-ic", html: icon("sparkle", 14) }),
    h("span", { class: "ch-as-pv-title", text: title + (sel ? " · " + t("as_selection_only") : "") }),
    a.instruction && a.action !== "tone" ? h("span", { class: "ch-as-pv-instr", text: a.instruction, title: a.instruction }) : null,
    h("span", { class: "mu-grow" }), stat, stopBtn,
    h("button", { class: "ch-mini", type: "button", title: t("as_discard") + " (Esc)", html: icon("x", 13), onclick: close })
  ]));
  var variantsRow = h("div", { class: "ch-as-variants", hidden: true });
  var viewBar = h("div", { class: "ch-as-seg", hidden: true });
  var bodyBox = h("div", { class: "ch-as-pv-body" });
  var editBox = h("textarea", { class: "mu-textarea ch-as-pv-edit", rows: "4", hidden: true, spellcheck: "true" });
  var foot = h("div", { class: "ch-as-pv-foot", hidden: true });
  card.appendChild(variantsRow); card.appendChild(viewBar); card.appendChild(bodyBox); card.appendChild(editBox); card.appendChild(foot);
  el.parentNode.insertBefore(card, el.nextSibling);
  var active = { el: el, card: card, close: close, cancel: function () { if (state.job) state.job.cancel(); } };
  ASSIST.active = active;
  assistBindKeys();

  function result() { return state.view === "edit" ? editBox.value : state.text; }
  function setText(s) { state.text = s; editBox.value = s; drawBody(); }
  function drawBody() {
    bodyBox.innerHTML = "";
    if (state.running) { bodyBox.appendChild(h("div", { class: "ch-as-stream", text: state.text || "" })); bodyBox.appendChild(h("span", { class: "ch-as-caret" })); return; }
    editBox.hidden = state.view !== "edit";
    bodyBox.hidden = state.view === "edit";
    if (state.view === "edit") { autosizeField(editBox); return; }
    var shown = a.action === "continue" ? null : base;
    if (shown && state.view === "diff") bodyBox.appendChild(assistDiffNode(shown, state.text));
    else bodyBox.appendChild(h("div", { class: "ch-as-stream", text: state.text }));
  }
  function drawViews() {
    viewBar.innerHTML = "";
    var views = a.action === "continue" || !base.trim() ? [["text", "as_view_text"], ["edit", "as_view_edit"]] : [["diff", "as_view_diff"], ["text", "as_view_text"], ["edit", "as_view_edit"]];
    if (!views.some(function (v) { return v[0] === state.view; })) state.view = views[0][0];
    views.forEach(function (v) {
      viewBar.appendChild(h("button", { class: "ch-as-seg-b" + (state.view === v[0] ? " on" : ""), type: "button", text: t(v[1]), onclick: function () {
        if (state.view === "edit") state.text = editBox.value;
        state.view = v[0]; drawViews(); drawBody(); if (v[0] === "edit") editBox.focus();
      } }));
    });
    viewBar.hidden = false;
  }
  function drawVariants() {
    variantsRow.innerHTML = "";
    if (!state.variants) { variantsRow.hidden = true; return; }
    variantsRow.hidden = false;
    state.variants.forEach(function (v, i) {
      variantsRow.appendChild(h("button", { class: "ch-as-var" + (state.pick === i ? " on" : ""), type: "button", onclick: function () { state.pick = i; setText(v); drawVariants(); } }, [
        h("span", { class: "ch-as-var-n", text: String(i + 1) }), h("span", { class: "ch-as-var-t", text: v.slice(0, 160) })]));
    });
  }
  function drawFoot() {
    foot.innerHTML = "";
    var primary = sel ? ["as_replace_sel", function () { apply(sel.start, sel.end); }] : a.action === "continue" ? ["as_append", function () { var v = el.value; var pad = v && !/\s$/.test(v) ? (/\n/.test(v) ? "\n\n" : " ") : ""; apply(v.length, v.length, pad); }]
      : ["as_accept", function () { apply(0, el.value.length); }];
    foot.appendChild(h("button", { class: "mu-btn small primary", type: "button", html: icon("check", 14) + esc(t(primary[0])), title: "Ctrl+Enter", onclick: primary[1] }));
    if (!sel && a.action !== "continue" && original.trim()) foot.appendChild(h("button", { class: "mu-btn small", type: "button", text: t("as_insert_caret"), onclick: function () { apply(caret, caret); } }));
    if (!a.given) foot.appendChild(h("button", { class: "mu-btn small", type: "button", html: icon("refresh", 14) + esc(t("as_again")), onclick: start }));
    foot.appendChild(h("span", { class: "mu-grow" }));
    foot.appendChild(h("button", { class: "mu-btn small ghost", type: "button", text: t("as_discard"), onclick: close }));
    foot.hidden = false;
  }
  function apply(s, e, pad) {
    var text = result();
    if (!text.trim()) return;
    if (el.value !== original && !sel && a.action !== "continue") { s = 0; e = el.value.length; }
    close();
    assistInsert(el, s, e, (pad || "") + text);
  }
  function close() {
    if (state.job) state.job.cancel();
    if (ASSIST.active === active) ASSIST.active = null;
    card.remove();
  }
  function start() {
    state.running = true; state.text = ""; foot.hidden = true; viewBar.hidden = true; editBox.hidden = true; bodyBox.hidden = false;
    stopBtn.hidden = false;
    stat.innerHTML = '<span class="mu-spinner"></span>';
    stat.appendChild(document.createTextNode(assistModelName(assistModel())));
    drawBody();
    var msgs = assistFieldMessages({ action: a.action, field: spec.field, label: spec.label, text: original, selection: sel, instruction: a.instruction,
      card: ctx.card, world: ctx.world, entry: ctx.entry, lang: assistLang() });
    var isJson = a.action === "variants";
    state.job = assistRun(msgs, { json: isJson, temperature: a.action === "fix" ? 0.2 : isJson ? 1 : 0.8, max_tokens: isJson ? 3500 : 2500,
      onText: function (s) { if (!isJson) { state.text = s; drawBody(); } else stat.lastChild.textContent = t("as_thinking_variants"); } });
    state.job.promise.then(function (text) {
      if (isJson) {
        var list = aiList(assistJsonList(text, "variants")).map(function (v) { return assistClean(v); }).filter(Boolean).slice(0, 3);
        if (!list.length) throw new Error(t("ai_bad_json"));
        state.variants = list; state.pick = 0; text = list[0];
      }
      finish(text);
    }).catch(function (err) {
      state.running = false; stopBtn.hidden = true; stat.textContent = "";
      if (state.text.trim() && /stop/i.test(String(err.message)) === false && err.message !== t("ai_stopped")) toast(err.message, "error");
      if (state.text.trim()) { finish(assistClean(state.text)); stat.textContent = t("ai_stopped"); return; }
      bodyBox.innerHTML = ""; bodyBox.appendChild(h("div", { class: "ch-as-err", text: err.message || String(err) }));
      foot.innerHTML = ""; foot.hidden = false;
      foot.appendChild(h("button", { class: "mu-btn small", type: "button", html: icon("refresh", 14) + esc(t("as_again")), onclick: start }));
      foot.appendChild(h("span", { class: "mu-grow" }));
      foot.appendChild(h("button", { class: "mu-btn small ghost", type: "button", text: t("as_discard"), onclick: close }));
    });
  }
  function finish(text) {
    state.running = false; state.job = null; stopBtn.hidden = true;
    stat.textContent = t("as_tokens", { n: fmtNum(E.estimateTokens(text)) });
    drawVariants(); drawViews(); setText(text); drawFoot();
  }
  editBox.addEventListener("input", function () { autosizeField(editBox); });
  card.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !state.running && !foot.hidden) { e.preventDefault(); foot.querySelector(".primary").click(); }
  });
  if (a.given !== undefined) { stopBtn.hidden = true; finish(a.given); }
  else start();
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return active;
}
function assistClosePreview() { if (ASSIST.active) ASSIST.active.close(); }
// Esc: stop a running answer, or close the preview — before the editor's own Esc (which would close the modal).
function assistBindKeys() {
  if (ASSIST.keyBound) return;
  ASSIST.keyBound = true;
  window.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || $$(".ch-menu").length) return;
    var act = ASSIST.active;
    if (act && document.body.contains(act.card)) { e.preventDefault(); e.stopImmediatePropagation(); act.close(); return; }
    var p = ASSIST.panel;
    if (p && p.job && document.body.contains(p.node)) { e.preventDefault(); e.stopImmediatePropagation(); p.job.cancel(); }
  }, true);
}

/* ---------------------------------------------------------------- lorebook: entries */
function assistAddEntry(world, prop) {
  var uids = Object.keys(world.entries).map(Number).filter(Number.isFinite);
  var uid = uids.length ? Math.max.apply(null, uids) + 1 : 0;
  world.entries[uid] = E.normalizeEntry({ uid: uid, comment: String(prop.title || "").trim() || t("new_entry"), key: aiList(prop.keys), content: aiStr(prop.content).trim() }, uid);
  saveWorld(world);
  return world.entries[uid];
}
// A proposed entry as a card: title / keys / content editable, «Add» puts it into the lorebook.
function assistEntryProposal(prop, opts) {
  opts = opts || {};
  var titleIn = h("input", { class: "mu-input ch-as-ent-title", value: aiStr(prop.title) });
  var keysIn = h("input", { class: "mu-input", value: aiList(prop.keys).join(", "), placeholder: t("wi_keys_ph") });
  var contentIn = h("textarea", { class: "mu-textarea", rows: "4" });
  contentIn.value = aiStr(prop.content);
  setTimeout(function () { autosizeField(contentIn); }, 0);
  contentIn.addEventListener("input", function () { autosizeField(contentIn); });
  var box = h("div", { class: "ch-as-ent" }, [
    h("div", { class: "ch-as-ent-head" }, [h("span", { html: icon("book", 14) }), titleIn]),
    prop.why ? h("div", { class: "ch-hint", text: prop.why }) : null,
    keysIn, contentIn
  ]);
  var actions = h("div", { class: "mu-row ch-as-ent-acts" }, [
    h("button", { class: "mu-btn small primary", type: "button", html: icon("plus", 14) + esc(opts.addLabel || t("as_add_entry")), onclick: function () {
      Promise.resolve(opts.world ? opts.world() : null).then(function (world) {
        if (!world) return;
        var e = assistAddEntry(world, { title: titleIn.value, keys: keysIn.value, content: contentIn.value });
        box.classList.add("done");
        actions.innerHTML = "";
        actions.appendChild(h("span", { class: "ch-as-done", html: icon("check", 14) + esc(t("as_entry_added", { name: e.comment, book: world.name })) }));
        if (opts.onAdded) opts.onAdded(e, world);
      });
    } }),
    opts.skip ? h("button", { class: "mu-btn small ghost", type: "button", text: t("as_skip"), onclick: function () { box.remove(); } }) : null
  ]);
  box.appendChild(actions);
  return box;
}
// The card's lorebook, or a choice: one of the lorebooks / a new one (linked to the card being edited).
function assistPickWorld(anchor, spec) {
  var ctx = spec.ctx ? spec.ctx() : {};
  if (ctx.world) return Promise.resolve(ctx.world);
  return new Promise(function (resolve) {
    var items = [{ head: t("as_pick_book") }];
    S.worldIndex.forEach(function (w) { items.push({ icon: "book", label: w.name, onClick: function () { loadWorld(w.id).then(resolve); } }); });
    items.push({ sep: true }, { icon: "plus", label: t("as_new_book"), onClick: function () {
      var name = (ctx.card && ctx.card.name ? t("char_book_name", { name: ctx.card.name }) : t("as_new_book"));
      var world = E.importWorld({ entries: {} }, name).world;
      world.id = E.uid("w"); world.name = name;
      saveWorld(world);
      if (spec.linkWorld) spec.linkWorld(world);
      resolve(world);
    } });
    popMenu(anchor, items);
  });
}
function assistEntryFromSelection(text, spec) {
  var ctx = spec.ctx ? spec.ctx() : {};
  var listBox = h("div", { class: "ch-as-ent-wrap" }, [h("div", { class: "ch-as-loading", html: '<span class="mu-spinner"></span>' + esc(t("as_working")) })]);
  var job = null;
  var modal = openModal({ title: t("as_entry_from_sel"), icon: "book", wide: true, content: h("div", { class: "ch-as-modal" }, [
    h("blockquote", { class: "ch-as-quote", text: text.length > 600 ? text.slice(0, 600) + "…" : text }), listBox]), onClose: function () { if (job) job.cancel(); } });
  job = assistRun(assistEntryFromTextMessages(text, ctx.card, ctx.world, assistLang()), { json: true, temperature: 0.4, max_tokens: 1500 });
  job.promise.then(function (raw) {
    job = null;
    var prop = aiParseJson(raw);
    listBox.innerHTML = "";
    var anchorBtn = null;
    var card = assistEntryProposal(prop, { world: function () { return assistPickWorld(anchorBtn, spec); }, onAdded: function (e, world) { if (spec.onEntryAdded) spec.onEntryAdded(e, world); setTimeout(function () { modal.close(); }, 700); } });
    listBox.appendChild(card);
    anchorBtn = card.querySelector(".ch-as-ent-acts .primary");
  }).catch(function (err) { listBox.innerHTML = ""; listBox.appendChild(h("div", { class: "ch-as-err", text: err.message })); });
}

// Keyword chips under a lorebook entry's keys field.
function assistSuggestKeys(input, entry, world, mount) {
  var old = mount.querySelector(".ch-as-keys");
  if (old) { old.remove(); return; }
  var box = h("div", { class: "ch-as-keys" }, [h("span", { class: "ch-as-loading", html: '<span class="mu-spinner"></span>' + esc(t("as_keys_working")) })]);
  mount.appendChild(box);
  var job = assistRun(assistKeysMessages(entry, world, assistLang()), { json: true, temperature: 0.5, max_tokens: 600 });
  job.promise.then(function (raw) {
    var have = (entry.key || []).map(function (k) { return k.toLowerCase(); });
    var keys = aiList(assistJsonList(raw, "keys")).filter(function (k) { return have.indexOf(k.toLowerCase()) < 0; });
    box.innerHTML = "";
    if (!keys.length) { box.appendChild(h("span", { class: "mu-muted ch-small", text: t("as_keys_none") })); return; }
    var picked = {};
    var addBtn = h("button", { class: "mu-btn small primary", type: "button", disabled: true, html: icon("plus", 13) + esc(t("as_keys_add")), onclick: function () {
      var add = keys.filter(function (k) { return picked[k]; });
      var cur = input.value.trim();
      assistInsert(input, 0, input.value.length, (cur ? cur.replace(/,\s*$/, "") + ", " : "") + add.join(", "));
      box.remove();
    } });
    keys.forEach(function (k) {
      var chip = h("button", { class: "ch-chip ch-as-chip", type: "button", text: k, onclick: function () {
        picked[k] = !picked[k]; chip.classList.toggle("on", picked[k]);
        addBtn.disabled = !Object.keys(picked).some(function (x) { return picked[x]; });
      } });
      box.appendChild(chip);
    });
    box.appendChild(h("button", { class: "ch-chip ch-as-chip ghost", type: "button", text: t("as_keys_all"), onclick: function () {
      keys.forEach(function (k) { picked[k] = true; }); $$(".ch-as-chip", box).forEach(function (c) { if (!c.classList.contains("ghost")) c.classList.add("on"); }); addBtn.disabled = false;
    } }));
    box.appendChild(addBtn);
  }).catch(function (err) { box.innerHTML = ""; box.appendChild(h("span", { class: "ch-as-err", text: err.message })); });
}

/* ---------------------------------------------------------------- local card checks (no model) */
var ASSIST_MAJOR = ["description", "personality", "scenario", "first_mes"];
function assistLocalChecks(card) {
  var out = [];
  card = card || {};
  var name = String(card.name || "").trim();
  function add(field, severity, titleKey, vars, fix) { out.push({ field: field, severity: severity, title: t(titleKey, vars), detail: "", fix: fix || "", local: true }); }
  if (!String(card.description || "").trim()) add("description", "high", "as_chk_empty", { field: t("f_description") }, "@fill");
  if (!String(card.first_mes || "").trim()) add("first_mes", "high", "as_chk_empty", { field: t("f_first_mes") }, "@fill");
  var fields = ["description", "personality", "scenario", "first_mes", "mes_example", "system_prompt", "post_history_instructions"];
  fields.forEach(function (f) {
    var v = String(card[f] || "");
    if (!v) return;
    var bad = v.match(/\{\{\s*(char|user)\s*\}(?!\})|\{(?:char|user)\}(?!\})|\{\{\s*(?:Char|CHAR|User|USER)\s*\}\}/g);
    if (bad) add(f, "medium", "as_chk_macro", { bad: bad[0] }, "Fix the broken macro " + bad[0] + " to {{char}} / {{user}} (lowercase, double braces).");
    if (name.length > 1 && f !== "mes_example" && v.indexOf("{{char}}") < 0 && new RegExp("(^|[^\\p{L}])" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![\\p{L}])", "u").test(v) && (v.split(name).length - 1) >= 3) {
      add(f, "low", "as_chk_literal", { name: name }, "Replace the literal name «" + name + "» with {{char}} where it refers to the character.");
    }
  });
  var fm = String(card.first_mes || "");
  if (fm && /\{\{user\}\}\s*(says|said|nods|smiles|walks|говорит|сказал|кивает|улыбается|идёт|подходит)/i.test(fm)) add("first_mes", "high", "as_chk_user_acts", {}, "Remove the places where the first message acts or speaks for {{user}}; leave {{user}} room to react.");
  var mx = String(card.mes_example || "").trim();
  if (mx && mx.indexOf("<START>") < 0) add("mes_example", "medium", "as_chk_start", {}, "Format the example dialogue: blocks start with <START>, lines are prefixed with {{user}}: and {{char}}:.");
  var perm = E.estimateTokens(card.description) + E.estimateTokens(card.personality) + E.estimateTokens(card.scenario);
  if (perm > 2500) add("description", "medium", "as_chk_long", { n: fmtNum(perm) }, "Tighten the description: keep every fact but cut repetition and filler so it is about a third shorter.");
  if (E.estimateTokens(fm) > 900) add("first_mes", "low", "as_chk_long_first", { n: fmtNum(E.estimateTokens(fm)) }, "Shorten the first message to about half its length while keeping the scene and the hook.");
  return out;
}

/* ---------------------------------------------------------------- the «Co-writer» side panel */
// host = {kind: "card"|"lorebook", box (modal .ch-modal), card() → data|null, world() → world|null, fieldKeys: [...],
//         label(key), reveal(key) → field element (switches the tab), addEntry?(prop) → Promise<entry>, revealEntry?(title) → element}
function assistPanel(host) {
  if (host.panel && document.body.contains(host.panel.node)) { host.panel.node.classList.toggle("open"); host.box.classList.toggle("ch-as-docked", host.panel.node.classList.contains("open")); return host.panel; }
  var P = { host: host, job: null, chat: host.chatLog || (host.chatLog = []), tab: "chat" };
  host.panel = P;
  ASSIST.panel = P;
  assistBindKeys();
  var node = h("aside", { class: "ch-as-panel open", "aria-label": t("as_panel") });
  P.node = node;
  var modelBtn = h("button", { class: "ch-as-model", type: "button", title: t("as_model_title") });
  function drawModel() { modelBtn.innerHTML = icon("zap", 12) + "<span>" + esc(assistModelName(assistModel())) + "</span>"; }
  drawModel();
  modelBtn.addEventListener("click", function () {
    openModelPicker(modelBtn, { value: assistModel(), noSettings: true, onPick: function (v) { S.settings.assist.model = v === S.settings.model ? "" : v; saveSettings(); drawModel(); } });
  });
  var tabs = host.kind === "card" ? [["chat", "as_tab_chat"], ["review", "as_tab_review"]] : [["chat", "as_tab_chat"], ["tools", "as_tab_tools"]];
  var tabBar = h("div", { class: "ch-as-tabs" });
  var pane = h("div", { class: "ch-as-pane" });
  node.appendChild(h("div", { class: "ch-as-phead" }, [h("span", { class: "ch-as-pv-ic", html: icon("sparkle", 15) }), h("span", { class: "ch-as-ptitle", text: t("as_panel") }),
    h("span", { class: "mu-grow" }), modelBtn,
    h("button", { class: "ch-mini", type: "button", title: t("close"), html: icon("x", 14), onclick: function () { node.classList.remove("open"); host.box.classList.remove("ch-as-docked"); } })]));
  node.appendChild(tabBar); node.appendChild(pane);
  function drawTabs() {
    tabBar.innerHTML = "";
    tabs.forEach(function (tb) {
      tabBar.appendChild(h("button", { class: "ch-as-tab" + (P.tab === tb[0] ? " on" : ""), type: "button", text: t(tb[1]), onclick: function () { P.tab = tb[0]; drawTabs(); } }));
    });
    pane.innerHTML = "";
    if (P.tab === "chat") assistChatPane(P, pane);
    else if (P.tab === "review") assistReviewPane(P, pane);
    else assistToolsPane(P, pane);
  }
  drawTabs();
  host.box.appendChild(node);
  host.box.classList.add("ch-as-docked");
  return P;
}
function assistFieldLabel(host, key) {
  var m = /^alternate_greeting:(\d+)$/.exec(key);
  if (m) return t("alt_greeting_n", { n: Number(m[1]) + 1 });
  return host.label(key) || key;
}
// Opens the field's preview with a ready text (or a fix request) — the one path every proposal takes.
function assistToField(host, key, a) {
  var el = host.reveal(key);
  if (!el) { toast(t("as_no_field", { field: key }), "warn"); return; }
  var spec = el.__assist || { field: key, label: assistFieldLabel(host, key), ctx: function () { return { card: host.card(), world: host.world() }; } };
  setTimeout(function () { el.scrollIntoView({ block: "center", behavior: "smooth" }); assistPreview(el, spec, a); }, 30);
}

function assistChatPane(P, pane) {
  var host = P.host;
  var log = h("div", { class: "ch-as-log" });
  var input = h("textarea", { class: "mu-textarea ch-as-input", rows: "2", placeholder: t("as_chat_ph") });
  var sendBtn = h("button", { class: "mu-btn small primary ch-as-send", type: "button", title: "Enter", html: icon("send", 14) });
  var quick = host.kind === "card" ? ["as_q_missing", "as_q_contra", "as_q_hooks", "as_q_first", "as_q_voice"] : ["as_q_book_missing", "as_q_book_contra", "as_q_book_ideas"];
  var chips = h("div", { class: "ch-as-quick" }, quick.map(function (k) {
    return h("button", { class: "ch-chip", type: "button", text: t(k), onclick: function () { send(t(k + "_full")); } });
  }));
  pane.appendChild(log);
  pane.appendChild(h("div", { class: "ch-as-compose" }, [chips, h("div", { class: "ch-as-inrow" }, [input, sendBtn])]));
  function context() {
    var card = host.card(), world = host.world();
    return [card ? "# The character card\n" + (assistCardBrief(card, null, 5000) || "(empty)") : "", world ? assistBookBrief(world, null, 900) : ""].filter(Boolean).join("\n\n");
  }
  function drawLog() {
    log.innerHTML = "";
    if (!P.chat.length) log.appendChild(h("div", { class: "ch-as-hello" }, [h("div", { class: "ch-as-hello-ic", html: icon("sparkle", 22) }),
      h("div", { class: "ch-as-hello-t", text: t(host.kind === "card" ? "as_hello" : "as_hello_book") }), h("div", { class: "mu-muted ch-small", text: t("as_hello_sub") })]));
    P.chat.forEach(function (m) { log.appendChild(assistChatBubble(P, m)); });
    log.scrollTop = log.scrollHeight;
  }
  function send(text) {
    text = String(text || input.value).trim();
    if (!text || P.job) return;
    input.value = "";
    P.chat.push({ role: "user", content: text });
    var reply = { role: "assistant", content: "", pending: true };
    P.chat.push(reply);
    drawLog();
    var keys = host.fieldKeys.slice();
    var history = P.chat.slice(-13, -1).map(function (m) { return { role: m.role, content: m.content }; });
    var msgs = [{ role: "system", content: assistChatSystem(host.kind, keys) + "\n" + assistLangRule(assistLang(), text) + " (Answer in the language of the author's message.)\n\n" + context() }].concat(history);
    sendBtn.innerHTML = icon("stop", 14);
    var bubble = log.lastChild;
    P.job = assistRun(msgs, { json: true, temperature: 0.7, max_tokens: 3000, onText: function (s) {
      reply.content = s.replace(/<think>[\s\S]*?(<\/think>|$)/gi, "");
      var body = bubble.querySelector(".ch-as-msg-body"); if (body) body.textContent = reply.content;
      log.scrollTop = log.scrollHeight;
    } });
    P.job.promise.then(function (s) { reply.content = assistClean(s); }, function (err) {
      if (!reply.content.trim()) reply.content = ""; reply.error = err.message;
    }).then(function () { delete reply.pending; P.job = null; sendBtn.innerHTML = icon("send", 14); drawLog(); });
  }
  sendBtn.addEventListener("click", function () { if (P.job) P.job.cancel(); else send(); });
  input.addEventListener("keydown", function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  drawLog();
  setTimeout(function () { input.focus(); }, 50);
}
// An assistant message → text with the <field> / <entry> blocks rendered as cards with an «Apply» button.
function assistChatBubble(P, m) {
  var host = P.host;
  var wrap = h("div", { class: "ch-as-msg " + m.role });
  if (m.role === "user" || m.pending) { wrap.appendChild(h("div", { class: "ch-as-msg-body", text: m.content })); if (m.pending) wrap.appendChild(h("span", { class: "ch-as-caret" })); return wrap; }
  var re = /<field\s+name="([^"]+)"\s*>([\s\S]*?)(?:<\/field>|$)|<entry\s+title="([^"]*)"(?:\s+keys="([^"]*)")?\s*>([\s\S]*?)(?:<\/entry>|$)/g;
  var s = m.content, last = 0, mt;
  function textPart(x) { x = x.replace(/^\s+|\s+$/g, ""); if (x) wrap.appendChild(h("div", { class: "ch-as-msg-body", html: assistMiniMd(x) })); }
  while ((mt = re.exec(s))) {
    textPart(s.slice(last, mt.index));
    last = re.lastIndex;
    if (mt[1]) {
      var key = mt[1].trim(), val = mt[2].replace(/^\n+|\s+$/g, "");
      var ok = host.fieldKeys.indexOf(key) >= 0 || /^alternate_greeting:\d+$/.test(key);
      wrap.appendChild(h("div", { class: "ch-as-prop" }, [
        h("div", { class: "ch-as-prop-head" }, [h("span", { html: icon("edit", 13) }), h("span", { text: ok ? assistFieldLabel(host, key) : key })]),
        h("div", { class: "ch-as-prop-body", text: val }),
        ok ? h("button", { class: "mu-btn small primary", type: "button", html: icon("check", 13) + esc(t("as_apply_to", { field: assistFieldLabel(host, key) })), onclick: function () {
          assistToField(host, key, { action: "custom", given: val, title: t("as_from_chat") });
        } }) : null
      ]));
    } else {
      var prop = { title: mt[3], keys: mt[4] || "", content: mt[5].trim() };
      wrap.appendChild(host.addEntryTo ? assistEntryProposal(prop, { world: host.addEntryTo, onAdded: host.onEntryAdded }) : h("div", { class: "ch-as-prop-body", text: prop.content }));
    }
  }
  textPart(s.slice(last));
  if (m.error) wrap.appendChild(h("div", { class: "ch-as-err", text: m.error }));
  return wrap;
}
function assistMiniMd(s) {
  return esc(s).replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>").replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/^(?:[-*•]) (.*)$/gm, '<span class="ch-as-li">$1</span>').replace(/^#{1,4} (.*)$/gm, "<b>$1</b>").replace(/<\/span>\n/g, "</span>").replace(/\n/g, "<br>");
}

function assistIssueRow(P, issue, onFix) {
  var host = P.host;
  var key = String(issue.field || "").trim();
  var known = host.fieldKeys.indexOf(key) >= 0 || /^alternate_greeting:\d+$/.test(key);
  var row = h("div", { class: "ch-as-issue " + (issue.severity || "low") }, [
    h("span", { class: "ch-as-sev", title: t("as_sev_" + (issue.severity || "low")) }),
    h("div", { class: "ch-as-issue-main" }, [
      h("div", { class: "ch-as-issue-t" }, [known ? h("span", { class: "ch-tag", text: assistFieldLabel(host, key) }) : null, h("span", { text: issue.title || "" })]),
      issue.detail ? h("div", { class: "ch-as-issue-d", text: issue.detail }) : null,
      h("div", { class: "mu-row ch-as-issue-acts" }, [
        known && issue.fix ? h("button", { class: "mu-btn small", type: "button", html: icon("sparkle", 13) + esc(t("as_fix_btn")), onclick: function () {
          if (onFix) return onFix(issue);
          assistToField(host, key, issue.fix === "@fill" ? { action: "fill" } : { action: "fix_issue", instruction: issue.fix + (issue.detail ? " (" + issue.detail + ")" : "") });
        } }) : null,
        known ? h("button", { class: "mu-btn small ghost", type: "button", text: t("as_show"), onclick: function () { var el = host.reveal(key); if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); el.focus(); } } }) : null
      ])
    ])
  ]);
  return row;
}
function assistReviewPane(P, pane) {
  var host = P.host;
  var list = h("div", { class: "ch-as-issues" });
  var runBtn = h("button", { class: "mu-btn small primary", type: "button", html: icon("sparkle", 14) + esc(t("as_review_run")) });
  pane.appendChild(h("div", { class: "ch-as-review-top" }, [h("div", { class: "mu-muted ch-small", text: t("as_review_sub") }), runBtn]));
  pane.appendChild(list);
  function draw(issues, loading) {
    list.innerHTML = "";
    issues.forEach(function (i) { list.appendChild(assistIssueRow(P, i)); });
    if (loading) list.appendChild(h("div", { class: "ch-as-loading", html: '<span class="mu-spinner"></span>' + esc(t("as_reviewing")) }));
    else if (!issues.length) list.appendChild(h("div", { class: "ch-as-clean", html: icon("check", 16) + esc(t("as_review_clean")) }));
  }
  function run() {
    if (P.job) { P.job.cancel(); return; }
    var card = host.card();
    var local = assistLocalChecks(card);
    P.review = local.slice();
    draw(P.review, true);
    runBtn.innerHTML = icon("stop", 14) + esc(t("as_stop"));
    P.job = assistRun(assistReviewMessages(card, host.world(), local.map(function (i) { return i.field + ": " + i.title; }), assistLang()), { json: true, temperature: 0.3, max_tokens: 2500, effort: "low" });
    P.job.promise.then(function (raw) {
      var got = assistJsonList(raw, "issues");
      (Array.isArray(got) ? got : []).forEach(function (i) { if (i && i.title) P.review.push({ field: aiStr(i.field), severity: /^(high|medium|low)$/.test(i.severity) ? i.severity : "low", title: aiStr(i.title), detail: aiStr(i.detail), fix: aiStr(i.fix) }); });
    }).catch(function (err) { if (err.message !== t("ai_stopped")) toast(err.message, "error"); }).then(function () {
      P.job = null; runBtn.innerHTML = icon("refresh", 14) + esc(t("as_review_again"));
      var rank = { high: 0, medium: 1, low: 2 };
      P.review.sort(function (a, b) { return rank[a.severity] - rank[b.severity]; });
      draw(P.review, false);
    });
  }
  runBtn.addEventListener("click", run);
  if (P.review) { draw(P.review, false); runBtn.innerHTML = icon("refresh", 14) + esc(t("as_review_again")); }
  else list.appendChild(h("div", { class: "ch-as-hello" }, [h("div", { class: "ch-as-hello-ic", html: icon("check", 22) }), h("div", { class: "ch-as-hello-t", text: t("as_review_hello") })]));
}
// Lorebook tools: gaps, contradictions, an entry from pasted text.
function assistToolsPane(P, pane) {
  var host = P.host;
  var out = h("div", { class: "ch-as-issues" });
  function tool(iconName, key, fn) {
    return h("button", { class: "ch-as-tool", type: "button", onclick: fn }, [h("span", { class: "ch-as-tool-ic", html: icon(iconName, 16) }),
      h("span", { class: "ch-as-tool-t" }, [h("b", { text: t(key) }), h("span", { class: "mu-muted ch-small", text: t(key + "_sub") })])]);
  }
  function busy(label) { out.innerHTML = ""; out.appendChild(h("div", { class: "ch-as-loading", html: '<span class="mu-spinner"></span>' + esc(label) })); }
  function fail(err) { P.job = null; out.innerHTML = ""; out.appendChild(h("div", { class: "ch-as-err", text: err.message })); }
  pane.appendChild(h("div", { class: "ch-as-tools" }, [
    tool("search", "as_gaps", function () {
      if (P.job) return;
      busy(t("as_gaps_working"));
      P.job = assistRun(assistGapsMessages(host.card(), host.world(), assistLang()), { json: true, temperature: 0.4, max_tokens: 3500 });
      P.job.promise.then(function (raw) {
        P.job = null;
        var rows = assistJsonList(raw, "entries").filter(function (e) { return e && e.title; });
        out.innerHTML = "";
        if (!rows.length) { out.appendChild(h("div", { class: "ch-as-clean", html: icon("check", 16) + esc(t("as_gaps_none")) })); return; }
        out.appendChild(h("div", { class: "mu-muted ch-small", text: t("as_gaps_found", { count: rows.length }) }));
        rows.forEach(function (r) { out.appendChild(assistEntryProposal(r, { world: host.addEntryTo, onAdded: host.onEntryAdded, skip: true })); });
      }).catch(fail);
    }),
    tool("alert", "as_conflicts", function () {
      if (P.job) return;
      busy(t("as_conflicts_working"));
      P.job = assistRun(assistConflictsMessages(host.world(), host.card(), assistLang()), { json: true, temperature: 0.3, max_tokens: 2500 });
      P.job.promise.then(function (raw) {
        P.job = null;
        var rows = assistJsonList(raw, "issues").filter(function (e) { return e && e.title; });
        out.innerHTML = "";
        if (!rows.length) { out.appendChild(h("div", { class: "ch-as-clean", html: icon("check", 16) + esc(t("as_conflicts_none")) })); return; }
        rows.forEach(function (r) {
          var first = aiList(r.entries)[0] || "";
          out.appendChild(assistIssueRow(P, { field: "", severity: "medium", title: r.title + (aiList(r.entries).length ? " — " + aiList(r.entries).join(" · ") : ""), detail: aiStr(r.detail), fix: "" }));
          if (first && host.revealEntry && aiStr(r.fix)) out.lastChild.querySelector(".ch-as-issue-acts").appendChild(h("button", { class: "mu-btn small", type: "button", html: icon("sparkle", 13) + esc(t("as_fix_entry", { name: first })), onclick: function () {
            var el = host.revealEntry(first);
            if (!el) { toast(t("as_no_field", { field: first }), "warn"); return; }
            setTimeout(function () { el.scrollIntoView({ block: "center", behavior: "smooth" }); assistPreview(el, el.__assist, { action: "fix_issue", instruction: aiStr(r.fix) + " (" + aiStr(r.detail) + ")" }); }, 30);
          } }));
        });
      }).catch(fail);
    }),
    tool("file", "as_from_text", function () {
      promptBox(t("as_from_text"), "", { multiline: true, icon: "book", hint: t("as_from_text_hint") }).then(function (v) {
        if (!v || !v.trim() || P.job) return;
        busy(t("as_working"));
        P.job = assistRun(assistEntryFromTextMessages(v.trim(), host.card(), host.world(), assistLang()), { json: true, temperature: 0.4, max_tokens: 1500 });
        P.job.promise.then(function (raw) { P.job = null; out.innerHTML = ""; out.appendChild(assistEntryProposal(aiParseJson(raw), { world: host.addEntryTo, onAdded: host.onEntryAdded })); }).catch(fail);
      });
    })
  ]));
  pane.appendChild(out);
}

/* ---------------------------------------------------------------- settings section (Connection panel) */
function assistSettingsSection() {
  var a = S.settings.assist;
  function set(k, v) { a[k] = v; saveSettings(); }
  var models = textModels();
  return section(t("as_settings"), "sparkle", [
    h("div", { class: "ch-hint", text: t("as_settings_hint") }),
    field("as_model", selectEl([["", t("as_model_chat")]].concat(models.map(function (m) { return [m.id, m.name || m.id]; })), a.model || "", function (v) { set("model", v); })),
    field("as_lang", selectEl([["auto", t("as_lang_auto")], ["ru", t("ai_lang_ru")], ["en", t("ai_lang_en")]], a.lang || "auto", function (v) { set("lang", v); })),
    toggle("as_buttons", a.buttons !== false, function (v) { set("buttons", v); }, "as_buttons_hint")
  ]);
}
