/* MemuAI Proxy — Round 33 (r33-proxy, item #24): ready-made connections in «Добавить аккаунт».

   The data (~100 services) comes from GET /api/admin/providers/presets (app/providers/catalog.py).
   The most important services are tiles on top; «Show all N services» opens the full list in alphabetical
   order with search and region / free filters. Choosing a service opens a short form: the key, the parts of
   the address the service needs (folder / project / region …), the model list and a link to get a key.
   Every connection is an ordinary «Собственный API» account. Uses app.js globals: t, esc, $, $$, LANG, api,
   toast, openModal, closeModal, loadAccounts, accountsCache, modelIdsSnapshot, offerNewModels, copyText. */
"use strict";

window.MemuCatalogR33 = (() => {
  const REGIONS = ["us", "eu", "cn", "ru", "asia", "global", "local", "other"];
  const S = { presets: [], query: "", region: "", free: false, expanded: false };
  const en = () => LANG === "en";
  const fmt = (key, vars) => Object.entries(vars || {}).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), t(key));
  const title = (p) => (en() ? p.title_en : p.title) || p.title;
  const desc = (p) => (en() ? p.desc_en : p.desc) || p.desc || "";
  const hint = (p) => (en() ? p.hint_en : p.hint) || p.hint || "";
  const norm = (v) => String(v || "").toLowerCase().replace(/ё/g, "е");
  const ICON_LINK = '<svg class="i" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M19 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 18.5v-12A1.5 1.5 0 0 1 5.5 5H10"/></svg>';
  const ICON_DOC = '<svg class="i" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 3.5H7A2.5 2.5 0 0 0 4.5 6v12A2.5 2.5 0 0 0 7 20.5h10a2.5 2.5 0 0 0 2.5-2.5V8.5z"/><path d="M14.5 3.5v5h5M8.5 12.5h7M8.5 16h5"/></svg>';
  const ICON_CHEVRON = '<svg class="i" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  const ICON_BACK = '<svg class="i" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M11 18l-6-6 6-6"/></svg>';
  const safeUrl = (u) => /^https:\/\//i.test(String(u || "")) ? String(u) : "";
  /* A brand colour as a CSS custom property — only a validated #rrggbb ever reaches the attribute. */
  const colorVar = (c) => {
    if (!/^#[0-9a-f]{6}$/i.test(String(c || ""))) return "";
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255)
      .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    const light = 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.42;      // yellow / lime brands get dark letters
    return `--mono-bg:${c};--mono-fg:${light ? "#15151b" : "#ffffff"}`;
  };

  function hay(p) {
    return norm([p.title, p.title_en, p.id, p.name, p.desc, p.desc_en, p.aliases, p.country,
      t(`cat.region.${p.region}`), (String(p.base_url).match(/^[a-z]+:\/\/([^/]+)/i) || [])[1]].join(" "));
  }

  function tags(p) {
    const out = [];
    if (p.in_use) out.push(`<span class="cat-tag used">${esc(t("cat.added"))}</span>`);
    if (p.region === "local") out.push(`<span class="cat-tag">${esc(t("cat.nokey"))}</span>`);
    else if (p.free) out.push(`<span class="cat-tag free" title="${esc(t("cat.free.tip"))}">${esc(t("cat.free"))}</span>`);
    if (p.country && p.region !== "local") out.push(`<span class="cat-tag">${esc(p.country)}</span>`);
    return out.join("");
  }

  function tile(p) {
    return `<button class="cat-tile${p.in_use ? " in-use" : ""}" type="button" data-cat="${esc(p.id)}" title="${esc(desc(p))}">
      <span class="cat-mono" style="${colorVar(p.color)}" aria-hidden="true">${esc(p.mono)}</span>
      <span class="cat-body"><span class="cat-name">${esc(title(p))}</span>
        <span class="cat-desc">${esc(desc(p))}</span>
        <span class="cat-tags">${tags(p)}</span></span>
    </button>`;
  }

  function filtered() {
    const words = norm(S.query).split(/\s+/).filter(Boolean);
    const narrowed = words.length || S.region || S.free;
    return S.presets
      .filter((p) => narrowed || !p.top)                 // without a filter the list holds the services below the tiles
      .filter((p) => !S.region || p.region === S.region)
      .filter((p) => !S.free || p.free)
      .filter((p) => { const h = hay(p); return words.every((w) => h.includes(w)); })
      .sort((a, b) => title(a).localeCompare(title(b), en() ? "en" : "ru", { sensitivity: "base" }));
  }

  function renderList(root) {
    const list = root.querySelector(".cat-list");
    if (!list) return;
    const rows = filtered();
    if (!rows.length) { list.innerHTML = `<div class="cat-grid"><div class="cat-empty">${esc(t("cat.empty"))}</div></div>`; return; }
    // One compact A–Z grid: letter headings left most rows half empty with ~90 services.
    list.innerHTML = `<div class="cat-grid">${rows.map(tile).join("")}</div>`;
    // Unfiltered, the list leaves out the popular tiles shown above, so "All 107" next to "Services: 89" read as a bug.
    const narrowed = norm(S.query).trim() || S.region || S.free;
    root.querySelector(".cat-count").textContent = narrowed ? fmt("cat.count", { n: rows.length })
      : fmt("cat.count.rest", { n: rows.length, top: S.presets.length - rows.length });
  }

  function renderRegions(root) {
    const box = root.querySelector(".cat-regions");
    const count = (r) => S.presets.filter((p) => p.region === r).length;
    box.innerHTML = [["", t("cat.region.all"), S.presets.length], ...REGIONS.filter(count).map((r) => [r, t(`cat.region.${r}`), count(r)])]
      .map(([r, label, n]) => `<button type="button" class="chip-btn${S.region === r ? " active" : ""}" data-region="${esc(r)}" aria-pressed="${S.region === r}">${esc(label)} <span class="dim">${n}</span></button>`).join("");
  }

  function render(root, presets) {
    S.presets = (presets || []).slice();
    S.query = ""; S.region = ""; S.free = false; S.expanded = false;
    const top = S.presets.filter((p) => p.top).sort((a, b) => a.top - b.top);
    root.innerHTML = `
      <div class="cat-head"><h4 class="mini-title">${esc(t("cat.title"))}</h4><span class="cat-count-all dim">${esc(fmt("cat.count", { n: S.presets.length }))}</span></div>
      <p class="cat-sub">${esc(t("cat.hint"))}</p>
      <div class="cat-grid cat-top">${top.map(tile).join("")}</div>
      <button type="button" class="btn cat-toggle" aria-expanded="false" aria-controls="cat-all">${ICON_CHEVRON}<span>${esc(fmt("cat.showall", { n: S.presets.length }))}</span></button>
      <div class="cat-all" id="cat-all" hidden>
        <div class="cat-sticky"><div class="cat-tools">
          <input type="search" class="cat-search" placeholder="${esc(t("cat.search"))}" aria-label="${esc(t("cat.search"))}" autocomplete="off" spellcheck="false">
          <label class="cat-free-toggle"><input type="checkbox" class="cat-free"> ${esc(t("cat.filter.free"))}</label>
        </div>
        <div class="cat-regions" role="group" aria-label="${esc(t("cat.region"))}"></div></div>
        <div class="cat-head"><span class="cat-count dim"></span></div>
        <div class="cat-list" tabindex="-1"></div>
      </div>`;
    const toggle = root.querySelector(".cat-toggle");
    const all = root.querySelector(".cat-all");
    const search = root.querySelector(".cat-search");
    toggle.onclick = () => {
      S.expanded = !S.expanded;
      all.hidden = !S.expanded;
      toggle.setAttribute("aria-expanded", String(S.expanded));
      toggle.querySelector("span").textContent = S.expanded ? t("cat.hideall") : fmt("cat.showall", { n: S.presets.length });
      if (S.expanded) {
        renderRegions(root); renderList(root);
        search.focus({ preventScroll: true });
        toggle.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    };
    search.addEventListener("input", () => { S.query = search.value; renderList(root); });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); root.querySelector(".cat-list [data-cat]")?.click(); }
      if (event.key === "Escape" && search.value) { event.preventDefault(); event.stopPropagation(); search.value = ""; S.query = ""; renderList(root); }
    });
    root.querySelector(".cat-free").addEventListener("change", (event) => { S.free = event.target.checked; renderList(root); });
    root.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-region]");
      if (chip) { S.region = chip.dataset.region; renderRegions(root); renderList(root); return; }
      const pick = event.target.closest("[data-cat]");
      if (pick) { const p = S.presets.find((x) => x.id === pick.dataset.cat); if (p) form(p); }
    });
  }

  /* ------------------------------------------------------------ the connection form */
  function uniqueName(base) {
    const used = new Set((typeof accountsCache !== "undefined" ? accountsCache : []).filter((a) => a.provider === "custom")
      .map((a) => String(a.fields?.name || "").toLowerCase()));
    let name = base;
    for (let i = 2; used.has(name.toLowerCase()); i += 1) name = `${base}-${i}`;
    return name;
  }
  const substitute = (text, values) => Object.entries(values).reduce((s, [k, v]) => s.split(k).join(v), String(text || ""));

  function authNote(p) {
    if (p.auth === "none") return t("cat.auth.none");
    if (p.auth === "optional") return t("cat.auth.optional");
    if (p.auth === "header" && p.auth_header) return fmt("cat.auth.header", { h: p.auth_header });
    return t("cat.auth.bearer");
  }

  function form(p) {
    const params = p.params || [];
    const needsKey = p.auth !== "none";
    const keyRequired = p.auth !== "none" && p.auth !== "optional";
    const name = uniqueName(p.name);
    const key = safeUrl(p.key_url), docs = safeUrl(p.docs_url);
    const isLocal = p.region === "local";
    openModal(`
      <div class="cat-form-head">
        <span class="cat-mono" style="${colorVar(p.color)}" aria-hidden="true">${esc(p.mono)}</span>
        <div><h3>${esc(title(p))}</h3><div class="cat-form-meta">${tags(p)}${p.region ? `<span class="cat-tag">${esc(t(`cat.region.${p.region}`))}</span>` : ""}</div></div>
      </div>
      <p class="muted" style="margin:6px 0 0">${esc(desc(p))}</p>
      <p class="muted" style="margin:6px 0 0">${esc(hint(p))}</p>
      ${key || docs ? `<div class="cat-form-links">
        ${key ? `<a class="btn small" href="${esc(key)}" target="_blank" rel="noopener noreferrer">${ICON_LINK}<span>${esc(isLocal ? (en() ? "Download" : "Скачать") : t("cat.where"))}</span></a>` : ""}
        ${docs ? `<a class="btn small" href="${esc(docs)}" target="_blank" rel="noopener noreferrer">${ICON_DOC}<span>${esc(t("cat.docs"))}</span></a>` : ""}
        ${needsKey && !isLocal && window.MemuGuideVideo ? MemuGuideVideo.button("proxy-api-key", { lang: en() ? "en" : "ru", compact: true }) : ""}
      </div>` : ""}
      ${params.map((prm, i) => `<div class="field"><label for="cf-p${i}">${esc((en() ? prm.label_en : prm.label) || prm.key)}</label>
        <input type="text" id="cf-p${i}" data-param="${esc(prm.key)}" value="${esc(prm.default || "")}" placeholder="${esc(prm.placeholder || prm.default || "")}" autocomplete="off" spellcheck="false"></div>`).join("")}
      ${needsKey ? `<div class="field"><label for="cf-key">${esc(t("acc.presets.apikey"))}${keyRequired ? "" : ` <span class="dim">(${esc(t("acc.optional"))})</span>`}
          <button type="button" class="btn small reveal-btn" data-reveal="cf-key" aria-pressed="false">${esc(t("acc.field.show"))}</button></label>
        <input type="text" id="cf-key" class="secret-field" autocomplete="off" spellcheck="false" placeholder="${esc(p.auth === "optional" ? "" : "sk-…")}">
        <div class="cat-auth-note">${esc(authNote(p))}</div></div>`
        : `<div class="cat-auth-note" style="margin:12px 0 4px">${esc(authNote(p))} ${esc(t("cat.local.tip"))}</div>`}
      <div class="field"><label for="cf-name">${esc(t("acc.presets.prefix"))}</label>
        <input type="text" id="cf-name" value="${esc(name)}" autocomplete="off" spellcheck="false">
        <div class="help">${esc(t("acc.presets.prefix.help"))}: <code id="cf-name-example">${esc(name)}-model</code></div></div>
      <div class="field"><label for="cf-url">Base URL</label>
        <input type="text" id="cf-url" value="${esc(p.base_url)}" autocomplete="off" spellcheck="false">
        ${p.edit_url && !params.length ? `<div class="help" style="color:var(--warn)">${esc(t("acc.presets.editurl"))}</div>` : ""}</div>
      <div class="field"><label for="cf-models">${esc(en() ? "Models" : "Модели")} <span class="dim">(${esc(t("acc.optional"))})</span></label>
        <textarea id="cf-models" rows="2" spellcheck="false" placeholder="${esc(en() ? "empty — from /models" : "пусто — из /models")}">${esc((p.models || []).join(", "))}</textarea>
        <div class="help">${esc(p.models && p.models.length ? fmt("cat.models.preset", { m: p.models.length }) : t("cat.models.auto"))}</div></div>
      <div class="field"><label for="cf-label">${esc(t("acc.label"))}</label>
        <input type="text" id="cf-label" placeholder="${esc(title(p))}"></div>
      <div class="row-end">
        <button class="btn" type="button" id="cf-back">${ICON_BACK}<span>${esc(en() ? "Back" : "Назад")}</span></button>
        <div class="grow"></div>
        <button class="btn" type="button" id="m-cancel">${esc(t("acc.cancel"))}</button>
        <button class="btn primary" type="button" id="cf-save">${esc(t("acc.addbtn"))}</button>
      </div>`, true);
    const box = document.getElementById("modal-box");
    const urlInput = $("#cf-url");
    const values = () => Object.fromEntries($$("#modal-box [data-param]").map((i) => [i.dataset.param, i.value.trim()]));
    const modelsInput = $("#cf-models");
    let urlTouched = false, modelsTouched = false;
    urlInput.addEventListener("input", () => { urlTouched = true; });
    modelsInput.addEventListener("input", () => { modelsTouched = true; });
    const syncUrl = () => {
      const filled = Object.fromEntries(Object.entries(values()).filter(([, v]) => v));
      if (!urlTouched) urlInput.value = substitute(p.base_url, filled);
      if (!modelsTouched) modelsInput.value = substitute((p.models || []).join(", "), filled);
    };
    $$("#modal-box [data-param]").forEach((i) => i.addEventListener("input", syncUrl));
    syncUrl();
    $("#cf-name").addEventListener("input", (e) => { $("#cf-name-example").textContent = `${e.target.value.trim() || "name"}-model`; });
    $("#m-cancel").onclick = closeModal;
    $("#cf-back").onclick = () => document.getElementById("acc-add-btn")?.click();
    box.querySelectorAll("[data-reveal]").forEach((btn) => btn.onclick = () => {
      const field = document.getElementById(btn.dataset.reveal);
      const shown = field.classList.toggle("revealed");
      btn.textContent = t(shown ? "acc.field.hide" : "acc.field.show");
      btn.setAttribute("aria-pressed", String(shown));
    });
    $("#cf-save").onclick = async () => {
      const button = $("#cf-save");
      const vals = values();
      const missing = params.find((prm) => !vals[prm.key]);
      if (missing) { toast(`${(en() ? missing.label_en : missing.label) || missing.key}: ${en() ? "fill it in" : "заполните поле"}`, "warn"); document.querySelector(`[data-param="${CSS.escape(missing.key)}"]`)?.focus(); return; }
      const apiKey = needsKey ? $("#cf-key").value.trim() : "";
      if (keyRequired && !apiKey) { toast(t("acc.presets.needkey"), "warn"); $("#cf-key").focus(); return; }
      const baseUrl = substitute(urlInput.value.trim(), vals);
      if (/\b(RESOURCE|PROJECT_ID|ACCOUNT_ID|FOLDER_ID|PRODUCT_ID|REGION|PROJECT)\b/.test(baseUrl)) { toast(t("acc.presets.editurl"), "warn"); urlInput.focus(); return; }
      const headers = Object.fromEntries(Object.entries(p.headers || {}).map(([k, v]) => [k, substitute(v, vals)]));
      button.disabled = true;
      try {
        const before = await modelIdsSnapshot();
        const added = await api("/api/admin/accounts", { method: "POST", body: {
          provider: "custom",
          label: $("#cf-label").value.trim() || title(p),
          fields: {
            name: $("#cf-name").value.trim(), base_url: baseUrl, api_key: apiKey,
            models: substitute($("#cf-models").value.trim(), vals),
            image_models: (p.image_models || []).join(", "),
            headers: Object.keys(headers).length ? JSON.stringify(headers) : "",
          },
        }});
        closeModal(); toast(t("acc.oauth.done")); loadAccounts();
        offerNewModels(before, added && added.account && added.account.id);
      } catch (e) { toast(e.message, "err", 6000); button.disabled = false; }
    };
    queueMicrotask(() => (params.length ? $("#cf-p0") : needsKey ? $("#cf-key") : $("#cf-name"))?.focus());
  }

  return { render, form, _state: S };
})();
