

(function () {
  "use strict";
  if (window.MemuVisionGate) return;
  const P = () => window.MemuModelPicker;
  const en = () => { try { return PG_LANG === "en"; } catch (e) { return false; } };
  const t = (ru, eng) => (en() ? eng : ru);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const g = (name) => { try { return typeof window[name] === "function" ? window[name] : null; } catch (e) { return null; } };
  const prefGet = (key, def) => { try { return typeof pref === "function" ? pref(key, def) : def; } catch (e) { return def; } };
  const allRows = () => (P() ? P().rows() : []);
  const nameOf = (id) => { try { return typeof modelDisplayName === "function" ? modelDisplayName(id) : (P() ? P().name(id) : id); } catch (e) { return id; } };
  const isChat = (row) => row && P() && P().kindOf(row) === "chat" && !((row.caps || {}).music === true && (row.caps || {}).text !== true);

  function state(id) {
    if (!P()) return { state: "unknown", src: "none", text: "", hint: "", firm: false };
    return P().vision(id);
  }

  function ranked(exclude = "") {
    const hidden = new Set(prefGet("hidden_models", []) || []);
    const pinned = prefGet("pinned_models", []) || [];
    const recent = P() ? P().recent() : [];
    const def = prefGet("default_model", "");
    const cur = P() ? P().find(exclude) : null;
    const score = (row) => {
      const v = state(row);
      let s = 0;
      if (["probe", "user", "provider", "local"].includes(v.src)) s += 50;
      if (cur && row.account && row.account === cur.account) s += 25;
      if (pinned.includes(row.id)) s += 20 - Math.min(10, pinned.indexOf(row.id));
      if (recent.includes(row.id)) s += 12 - recent.indexOf(row.id);
      if (row.id === def) s += 30;
      if ((row.caps || {}).tools === true) s += 4;
      const pms = g("primaryModelScore");
      if (pms) { try { s += Math.max(-20, Math.min(100, pms(row))) / 4; } catch (e) {} }
      return s;
    };
    return allRows().filter((r) => r && r.id && r.id !== exclude && !hidden.has(r.id) && isChat(r) && state(r).state === "yes")
      .map((r) => [score(r), r]).sort((a, b) => b[0] - a[0]).map((x) => x[1]);
  }
  function suggest(id) { return (ranked(id)[0] || {}).id || ""; }
  function helper(exclude = "") {
    const configured = prefGet("vision_fallback_model", "");
    const row = configured && P() ? P().find(configured) : null;
    if (row && isChat(row) && state(row).state !== "no") return configured;
    return (ranked(exclude)[0] || {}).id || "";
  }

  function modelCard(id) {
    const row = (P() && P().find(id)) || { id, caps: {} };
    return `${P() ? P().monogram(row) : ""}<span class="mmp-text"><span class="mmp-name">${esc(nameOf(id))}</span><span class="mmp-id">${esc(id)}</span></span>${P() ? P().badgesHtml(row, { noFree: true }) : ""}`;
  }
  function pickBtn(id, attr) {
    return `<button type="button" class="mmp-trigger" ${attr} aria-haspopup="dialog">${modelCard(id)}${P() ? P().svg("chev", "mmp-caret") : ""}</button>`;
  }

  function applyAnswer(id, vision, src) {
    const row = P() && P().find(id);
    if (row) {
      row.caps = row.caps || {};
      if (vision === null) { delete row.caps.vision_state; delete row.caps.vision_src; }
      else { row.caps.vision_state = vision ? "yes" : "no"; row.caps.vision_src = src; row.caps.vision = !!vision; }
    }
    try { if (typeof renderModelDD === "function") renderModelDD(); } catch (e) {}
    try { P() && P().refreshAll(); } catch (e) {}
    try { if (typeof refreshStateSignature !== "undefined") refreshStateSignature = ""; } catch (e) {}
    const refresh = g("refreshState");
    if (refresh) setTimeout(() => { try { refresh(); } catch (e) {} }, vision === null ? 50 : 600);
    note();
  }
  async function post(body) {
    const resp = await fetch("/api/pg/model-catalog/vision", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, lang: en() ? "en" : "ru" }) });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok && !("ok" in data)) throw new Error(data.error || `HTTP ${resp.status}`);
    return data;
  }
  async function probe(id) {
    try {
      const data = await post({ model: id, action: "probe" });
      if (data.ok && typeof data.vision === "boolean") applyAnswer(id, data.vision, "probe");
      return data;
    } catch (e) { return { ok: false, vision: null, error: String(e.message || e) }; }
  }
  async function setOverride(id, value) {
    const data = await post(value === null ? { model: id, action: "forget" } : { model: id, action: "set", value });
    if (data.ok === false) throw new Error(data.error || "error");
    applyAnswer(id, value, "user");
    return data;
  }
  function pickVisionModel(anchor, value, title) {
    if (!P()) return Promise.resolve(null);
    return P().open(anchor, { value, kind: "text", title, filter: (r) => state(r).state === "yes", emptyText: t("Нет моделей со зрением — добавьте такую модель в MemuAI Proxy", "No vision models — add one in MemuAI Proxy") });
  }

  function hasImages() {
    try { return Array.isArray(attachments) && attachments.some((a) => a && !a.isVideo && (a.isImg || String(a.data || "").startsWith("data:image/"))); }
    catch (e) { return false; }
  }
  function note() {
    const wrap = document.querySelector(".composer-wrap");
    const composer = wrap && wrap.querySelector(".composer");
    if (!composer) return;
    let box = document.getElementById("vision-note");
    let model = "";
    try { model = typeof currentModel === "function" ? currentModel() : ""; } catch (e) {}
    const row = model && P() ? P().find(model) : null;
    const v = row ? state(row) : null;
    const show = hasImages() && row && isChat(row) && v.state !== "yes" && prefGet("image_send_mode", "ask") !== "fallback";
    if (!show) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement("div");
      box.id = "vision-note";
      box.setAttribute("role", "status");

      composer.insertBefore(box, composer.querySelector("#attach-row") || composer.firstChild);
    }
    const better = suggest(model);
    const unknown = v.state === "unknown";
    const canTest = !["probe", "user"].includes(v.src) && v.src !== "adapter" && v.src !== "local";
    box.className = "mmp-vnote" + (unknown ? " unk" : "");
    box.innerHTML = `${P().svg(unknown ? "eyeQ" : "eyeOff")}<span class="grow">${unknown
      ? t(`Неизвестно, видит ли <b>${esc(nameOf(model))}</b> изображения — перед отправкой предложу варианты.`, `It is not known whether <b>${esc(nameOf(model))}</b> sees images — I will offer options before sending.`)
      : t(`<b>${esc(nameOf(model))}</b> не видит изображения — перед отправкой предложу варианты.`, `<b>${esc(nameOf(model))}</b> cannot see images — I will offer options before sending.`)}</span>
      ${better ? `<button type="button" class="primary" data-vnote-switch="${esc(better)}" title="${esc(better)}">${esc(t("Переключиться на ", "Switch to "))}${esc(nameOf(better))}</button>` : ""}
      ${canTest ? `<button type="button" data-vnote-test>${esc(t("Проверить модель", "Test the model"))}</button>` : ""}`;
    const sw = box.querySelector("[data-vnote-switch]");
    if (sw) sw.onclick = () => { const set = g("setCurrentModel"); if (set) { set(sw.dataset.vnoteSwitch); try { renderCaps(); } catch (e) {} } note(); };
    const test = box.querySelector("[data-vnote-test]");
    if (test) test.onclick = async () => {
      test.disabled = true; test.textContent = t("Проверяю…", "Testing…");
      const result = await probe(model);
      const toastFn = g("toast");
      if (toastFn) {
        if (result.ok && result.vision === true) toastFn(t("Модель видит изображения", "The model sees images"), "ok");
        else if (result.ok && result.vision === false) toastFn(t("Модель не видит изображения", "The model cannot see images"), "warn");
        else toastFn(result.error || t("Проверка не дала ответа", "The test gave no answer"), "warn");
      }
      note();
    };
  }

  function choose(model) {
    const mode = prefGet("image_send_mode", "ask");
    if (mode === "direct" || mode === "fallback") return Promise.resolve(mode);
    const row = P() && P().find(model);
    if (!row || !isChat(row)) return Promise.resolve("direct");
    if (state(row).state === "yes") return Promise.resolve("direct");
    const panelFn = g("panel"), closeFn = g("closePanel");
    if (!panelFn || !closeFn) return Promise.resolve("direct");
    return new Promise((resolve) => {
      let settled = false;
      let target = suggest(model);
      let help = helper(model);
      const done = (value) => {
        if (settled) return;
        settled = true;
        const remember = document.getElementById("vgate-remember");
        if (remember && remember.checked && (value === "direct" || value === "fallback")) {
          try { savePreferences({ image_send_mode: value }); } catch (e) {}
        }
        closeFn(value ? "choice" : "cancel");
        resolve(value);
      };
      const draw = (probeLine = "") => {
        const v = state(model);
        const unknown = v.state === "unknown";
        const canTest = !["probe", "user", "adapter", "local"].includes(v.src);
        const title = unknown ? t("Неизвестно, видит ли модель изображения", "Not known whether this model sees images")
          : t("Эта модель не видит изображения", "This model cannot see images");
        const why = unknown ? t("Сведений о зрении этой модели нет. Выберите, как поступить с картинкой:", "There is no information about this model's vision. Choose what to do with the picture:")
          : t(`Источник: ${esc(v.hint.split(" · ").slice(1).join(" · ") || v.hint)}. Выберите, как поступить с картинкой:`, `Source: ${esc(v.hint.split(" · ").slice(1).join(" · ") || v.hint)}. Choose what to do with the picture:`);
        const html = `<div class="vgate"><div class="image-route-mark">${P().svg(unknown ? "eyeQ" : "eyeOff")}</div><h2>${esc(title)}</h2>
          <p class="sub">${why}</p>
          <div class="vgate-model">${modelCard(model)}</div>
          <div class="vgate-choices">
            <div class="vgate-choice${target ? " recommended" : ""}" role="group">
              ${P().svg("eye")}<div style="flex:1;min-width:0"><b>${esc(t("Переключиться на модель со зрением", "Switch to a vision model"))}</b>
                <span class="d">${target ? esc(t("Сообщение уйдёт выбранной модели вместе с изображением, чат продолжится с ней.", "The message goes to the chosen model with the picture; the chat continues with it."))
                  : esc(t("Нет моделей, которые видят изображения. Добавьте такую в MemuAI Proxy или проверьте модель.", "No model is known to see images. Add one in MemuAI Proxy or test a model."))}</span>
                ${target ? `<div class="vgate-pick">${pickBtn(target, 'id="vgate-target"')}<button type="button" class="pbtn" id="vgate-switch">${esc(t("Переключиться", "Switch"))}</button></div>` : ""}</div>
            </div>
            <div class="vgate-choice" role="group">
              ${P().svg("search")}<div style="flex:1;min-width:0"><b>${esc(t("Описать изображение помощником", "Describe the picture with a helper"))}</b>
                <span class="d">${help ? esc(t(`Помощник скрыто опишет картинку, а «${nameOf(model)}» получит текст. Мелкие детали могут потеряться.`, `The helper privately describes the picture and «${nameOf(model)}» receives the text. Small details can be lost.`))
                  : esc(t("Нет модели-помощника со зрением.", "There is no helper model with vision."))}</span>
                ${help ? `<div class="vgate-pick">${pickBtn(help, 'id="vgate-helper"')}<button type="button" class="pbtn ghost" id="vgate-fallback">${esc(t("Описать", "Describe"))}</button></div>` : ""}</div>
            </div>
          </div>
          <div class="vgate-choices${canTest ? " two" : ""}" style="margin-top:8px">
            ${canTest ? `<button type="button" class="vgate-choice" id="vgate-probe">${P().svg("eyeQ")}<div><b>${esc(t("Проверить модель сейчас", "Test the model now"))}</b>
              <span class="d">${esc(t("Один настоящий запрос с маленькой картинкой. Ответ запомнится.", "One real request with a tiny picture. The answer is remembered."))}</span>
              ${probeLine ? `<span class="vgate-probe-result">${probeLine}</span>` : ""}</div></button>` : (probeLine ? `<div class="vgate-probe-result">${probeLine}</div>` : "")}
            <button type="button" class="vgate-choice" id="vgate-direct">${P().svg("image")}<div><b>${esc(t("Отправить как есть", "Send anyway"))}</b>
              <span class="d">${esc(unknown ? t("Если модель не видит картинки, она ответит, не глядя на них.", "If the model cannot see pictures, it answers without looking at them.")
                : t("Модель, скорее всего, отклонит или проигнорирует изображение.", "The model will most likely reject or ignore the picture."))}</span></div></button>
          </div>
          <div class="vgate-foot"><label class="vgate-remember" title="${esc(t("Настройки → Модели → Отправка изображений", "Settings → Models → Sending images"))}"><input type="checkbox" id="vgate-remember"> ${esc(t("Запомнить «Описать» или «Отправить как есть»", "Remember «Describe» or «Send anyway»"))}</label><button type="button" class="pbtn ghost" id="vgate-vision">${esc(t("Указать зрение вручную…", "Set vision manually…"))}</button><button type="button" class="pbtn ghost" id="vgate-cancel">${esc(t("Отмена", "Cancel"))}</button></div></div>`;
        return html;
      };
      const bind = () => {
        const $id = (x) => document.getElementById(x);
        if ($id("vgate-target")) $id("vgate-target").onclick = async (e) => {
          e.preventDefault(); e.stopPropagation();
          const picked = await pickVisionModel($id("vgate-target"), target, t("Модель со зрением", "A vision model"));
          if (picked) { target = picked; $id("vgate-target").innerHTML = modelCard(picked) + P().svg("chev", "mmp-caret"); }
        };
        if ($id("vgate-helper")) $id("vgate-helper").onclick = async (e) => {
          e.preventDefault(); e.stopPropagation();
          const picked = await pickVisionModel($id("vgate-helper"), help, t("Модель-помощник", "Helper model"));
          if (picked) { help = picked; $id("vgate-helper").innerHTML = modelCard(picked) + P().svg("chev", "mmp-caret"); }
        };
        if ($id("vgate-switch")) $id("vgate-switch").onclick = () => {
          const set = g("setCurrentModel");
          if (set) { set(target); try { renderCaps(); } catch (e) {} }
          done("switch:" + target);
        };
        if ($id("vgate-fallback")) $id("vgate-fallback").onclick = () => {
          if (help && help !== prefGet("vision_fallback_model", "")) { try { savePreferences({ vision_fallback_model: help }); } catch (e) {} }
          done("fallback");
        };
        if ($id("vgate-direct")) $id("vgate-direct").onclick = () => done("direct");
        if ($id("vgate-cancel")) $id("vgate-cancel").onclick = () => done("");
        if ($id("vgate-vision")) $id("vgate-vision").onclick = () => { settled = true; resolve(""); overridePanel(model); };
        if ($id("vgate-probe")) $id("vgate-probe").onclick = async () => {
          const btn = $id("vgate-probe");
          if (btn.dataset.busy) return;
          btn.dataset.busy = "1"; btn.setAttribute("aria-busy", "true");
          const d = btn.querySelector(".d"); if (d) d.textContent = t("Проверяю — это займёт несколько секунд…", "Testing — this takes a few seconds…");
          const result = await probe(model);
          if (settled) return;
          if (result.ok && result.vision === true) {
            const toastFn = g("toast"); if (toastFn) toastFn(t("Модель видит изображения — отправляю напрямую", "The model sees images — sending directly"), "ok");
            done("direct");
            return;
          }
          const line = result.ok && result.vision === false ? esc(t("Проверка: модель не видит изображения.", "Test: the model cannot see images."))
            : esc(t("Проверка не дала ответа: ", "The test gave no answer: ") + (result.error || result.verdict || "—"));
          rerender(line);
        };
      };
      const rerender = (line) => { const host = document.querySelector("#panel .vgate"); if (host) { host.outerHTML = draw(line); bind(); } };
      panelFn(draw(), "image-route-panel vgate-panel", { onClose: () => { if (!settled) { settled = true; resolve(""); } } });
      bind();
      setTimeout(() => { try { (document.getElementById("vgate-switch") || document.getElementById("vgate-fallback") || document.getElementById("vgate-direct"))?.focus(); } catch (e) {} }, 30);
    });
  }

  function overridePanel(id) {
    const panelFn = g("panel"), closeFn = g("closePanel");
    if (!panelFn || !P()) return;
    const draw = (msg = "") => {
      const v = state(id);
      const manual = v.src === "user" ? v.state : "auto";
      const opt = (key, icon, title, desc) => `<button type="button" class="vgate-choice${manual === key ? " recommended" : ""}" data-vset="${key}" aria-pressed="${manual === key}">${P().svg(icon)}<div style="flex:1"><b>${esc(title)}</b><span class="d">${esc(desc)}</span></div>${manual === key ? P().svg("check") : ""}</button>`;
      return `<div class="vgate"><h2>${esc(t("Зрение модели", "Model vision"))}</h2>
        <p class="sub">${esc(t("Сейчас: ", "Now: ") + v.hint)}</p>
        <div class="vgate-model">${modelCard(id)}</div>
        <div class="vgate-choices">
          ${opt("auto", "eyeQ", t("Определять автоматически", "Automatic"), t("Проверка, данные провайдера, каталог и семейство модели.", "A test, the provider, the catalogue and the model family."))}
          ${opt("yes", "eye", t("Видит изображения", "Sees images"), t("Картинки уходят модели напрямую, без вопросов.", "Pictures go to the model directly, without questions."))}
          ${opt("no", "eyeOff", t("Не видит изображения", "Cannot see images"), t("Перед отправкой картинки MemuAI предложит другую модель или описание.", "Before sending a picture MemuAI offers another model or a description."))}
        </div>
        ${msg ? `<div class="vgate-probe-result">${msg}</div>` : ""}
        <div class="vgate-foot"><button type="button" class="pbtn ghost" id="vset-probe">${esc(t("Проверить сейчас", "Test now"))}</button><button type="button" class="pbtn" id="vset-close">${esc(t("Готово", "Done"))}</button></div></div>`;
    };
    const bind = () => {
      document.querySelectorAll("#panel [data-vset]").forEach((b) => b.onclick = async () => {
        const key = b.dataset.vset;
        try { await setOverride(id, key === "auto" ? null : key === "yes"); rerender(esc(t("Сохранено", "Saved"))); }
        catch (e) { rerender(esc(String(e.message || e))); }
      });
      const pr = document.getElementById("vset-probe");
      if (pr) pr.onclick = async () => {
        pr.disabled = true; pr.textContent = t("Проверяю…", "Testing…");
        const r = await probe(id);
        rerender(esc(r.ok && r.vision === true ? t("Проверка: видит изображения.", "Test: sees images.") : r.ok && r.vision === false ? t("Проверка: не видит изображения.", "Test: cannot see images.")
          : t("Проверка не дала ответа: ", "The test gave no answer: ") + (r.error || r.verdict || "—")));
      };
      const cl = document.getElementById("vset-close");
      if (cl) cl.onclick = () => closeFn("done");
    };
    const rerender = (msg) => { const host = document.querySelector("#panel .vgate"); if (host) { host.outerHTML = draw(msg); bind(); } };
    panelFn(draw(), "image-route-panel vgate-panel");
    bind();
  }

  window.MemuVisionGate = Object.freeze({ VERSION: "1.0-r33", state, suggest, helper, choose, note, overridePanel, probe, setOverride });
})();
