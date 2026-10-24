// MemuFiles · dialogs: properties, batch rename, settings (Round 35; conflicts and the operations flyout moved to ops.js in Round 40).
import { t } from "./i18n.js";
import { api, $, $$, h, esc, icon, fileIcon, fmtSize, fmtNum, fmtDate, itemsWord, baseName, parentPath, extOf,
  toast, showError, modal, debounce, isWin } from "./core.js";
import { typeLabel } from "./pane.js";

// ------------------------------------------------------------------ properties
export async function properties(app, paths) {
  paths = (paths || []).filter(Boolean);
  if (!paths.length) return;
  if (paths.length > 1) return multiProperties(app, paths);
  let info;
  try { info = await api("props", { path: paths[0] }); } catch (err) { showError(err); return; }
  const body = h("div", {});
  const top = h("div", { class: "props-top" });
  top.innerHTML = `<span class="big-ico">${fileIcon(info.name, info.is_dir)}</span>`;
  const nameInput = h("input", { class: "input", value: info.name, spellcheck: "false" });
  if (info.protected) nameInput.disabled = true;
  top.append(nameInput);
  body.append(top);
  const kv = h("div", { class: "kv" });
  const row = (k, v) => { kv.append(h("span", { text: k }), h("span", { text: v })); };
  row(t("props.type"), info.is_dir ? t("type.folder") : typeLabel({ n: info.name, d: 0 }));
  row(t("props.location"), info.parent);
  const sizeEl = h("span", { text: info.is_dir ? t("props.calculating") : fmtSize(info.size, true) });
  kv.append(h("span", { text: t("props.size") }), sizeEl);
  let contentsEl = null;
  if (info.is_dir) { contentsEl = h("span", { text: "…" }); kv.append(h("span", { text: t("props.contains") }), contentsEl); }
  if (info.image) row(t("props.dimensions"), `${info.image.width} × ${info.image.height} px · ${info.image.format || ""} ${info.image.mode || ""}`);
  if (info.is_link && info.target) row(t("props.target"), info.target);
  body.append(kv, h("div", { class: "props-sep" }));
  const kv2 = h("div", { class: "kv" });
  const row2 = (k, v) => { if (v) kv2.append(h("span", { text: k }), h("span", { text: v })); };
  row2(t("props.created"), fmtDate(info.created, true));
  row2(t("props.modified"), fmtDate(info.modified, true));
  row2(t("props.accessed"), fmtDate(info.accessed, true));
  if (info.owner) row2(t("props.owner"), `${info.owner}:${info.group || ""} · ${info.mode}`);
  if (info.disk) row2(t("props.disk"), t("drive.free", { free: fmtSize(info.disk.free), total: fmtSize(info.disk.total) }));
  body.append(kv2, h("div", { class: "props-sep" }));
  const attrs = h("div", { style: { display: "flex", gap: "18px", flexWrap: "wrap", alignItems: "center" } });
  const ro = h("input", { type: "checkbox" }); ro.checked = !!info.readonly;
  const hid = h("input", { type: "checkbox" }); hid.checked = !!info.hidden;
  attrs.append(h("span", { class: "muted", text: t("props.attributes") }), h("label", { class: "check" }, ro, t("props.readonly")), h("label", { class: "check" }, hid, t("props.hidden")));
  if (info.protected) attrs.append(h("span", { class: "pill", html: icon("lock", "sm") + esc(t("props.protected")) }));
  body.append(attrs);   // r40-files-max: hashes moved to their own tab (feat40.propertiesTabs) with «verify»
  const winBtn = isWin() ? h("button", { class: "btn ghost sm", html: icon("windows") + esc(t("ctx.win_props")) }) : null;
  if (winBtn) winBtn.addEventListener("click", () => api("shellverb", { path: info.path, verb: "properties" }).catch(showError));
  const { propertiesTabs } = await import("./feat40.js");
  const dlg = modal({ title: t("props.title", { name: info.name }), icon: "info", body: propertiesTabs(info, body), wide: false, left: winBtn,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("common.apply"), cls: "primary", primary: true, run: async () => {
      try {
        const changes = {};
        if (ro.checked !== !!info.readonly) changes.readonly = ro.checked;
        if (hid.checked !== !!info.hidden) changes.hidden = hid.checked;
        if (Object.keys(changes).length) await api("attrs", { path: info.path, ...changes });
        const newName = nameInput.value.trim();
        if (newName && newName !== info.name) await api("rename", { path: info.path, name: newName });
        if (Object.keys(changes).length || (newName && newName !== info.name)) app.pane().reload(true);
      } catch (err) { showError(err); return false; }
      return true;
    } }] });
  if (info.is_dir) {
    const res = await runJob("size", { paths: [info.path] }, (p) => { sizeEl.textContent = t("props.calculating") + " " + fmtSize(p.bytes_now || 0); });
    if (res && document.body.contains(dlg.el)) {
      sizeEl.textContent = fmtSize(res.size, true);
      contentsEl.textContent = t("props.contents", { files: fmtNum(res.files), folders: fmtNum(res.folders) });
    }
  }
}

async function multiProperties(app, paths) {
  const body = h("div", {});
  const kv = h("div", { class: "kv" });
  const sizeEl = h("span", { text: t("props.calculating") });
  const contentsEl = h("span", { text: "…" });
  kv.append(h("span", { text: t("props.selected") }), h("span", { text: itemsWord(paths.length) }),
    h("span", { text: t("props.location") }), h("span", { text: parentPath(paths[0]) }),
    h("span", { text: t("props.size") }), sizeEl, h("span", { text: t("props.contains") }), contentsEl);
  body.append(kv);
  modal({ title: t("props.multi"), icon: "info", body, buttons: [{ label: t("common.close"), cls: "primary", primary: true }] });
  const res = await runJob("size", { paths });
  if (res) { sizeEl.textContent = fmtSize(res.size, true); contentsEl.textContent = t("props.contents", { files: fmtNum(res.files), folders: fmtNum(res.folders) }); }
}

/** Start a backend job and poll it; resolves with its result (null on error/cancel). */
export async function runJob(kind, args, onProgress, holder) {
  let job;
  try { job = (await api("jobs/start", { kind, args })).job; } catch (err) { showError(err); return null; }
  if (holder) holder.id = job.id;
  for (;;) {
    if (job.status === "done") return job.result;
    if (job.status === "error") { toast(job.error, "err"); return null; }
    if (job.status === "cancelled") return null;
    if (onProgress) onProgress(job.progress || {});
    await new Promise((r) => setTimeout(r, 200));
    try { job = (await api("jobs/get?id=" + job.id)).job; } catch (err) { return null; }
  }
}

// ------------------------------------------------------------------ batch rename
export function batchRename(app, paths) {
  const rule = { pattern: "{name}", start: 1, step: 1, find: "", replace: "", regex: false, match_case: false, case: "", ext_case: "" };
  const body = h("div", {});
  const pattern = h("input", { class: "input mono", value: rule.pattern, spellcheck: "false" });
  const tokens = h("div", { class: "br-tokens" });
  for (const tok of ["{name}", "{n}", "{n:3}", "{ext}", "{date}", "{time}", "{parent}", "{created}", "{exif}", "{width}x{height}"]) {   // r39: +created/exif/size
    const b = h("button", { text: tok, title: t("br.tok." + tok.replace(/[{}:]/g, "")) });
    b.addEventListener("click", () => { const p = pattern.selectionStart ?? pattern.value.length; pattern.value = pattern.value.slice(0, p) + tok + pattern.value.slice(pattern.selectionEnd ?? p); pattern.focus(); update(); });
    tokens.append(b);
  }
  const start = h("input", { class: "input", type: "number", value: "1" });
  const step = h("input", { class: "input", type: "number", value: "1" });
  const find = h("input", { class: "input", spellcheck: "false" });
  const replace = h("input", { class: "input", spellcheck: "false" });
  const regex = h("input", { type: "checkbox" });
  const matchCase = h("input", { type: "checkbox" });
  const caseSel = h("select", { class: "input" });
  for (const [v, k] of [["", "br.case_keep"], ["lower", "br.case_lower"], ["upper", "br.case_upper"], ["title", "br.case_title"], ["sentence", "br.case_sentence"]]) caseSel.append(h("option", { value: v, text: t(k) }));
  const extSel = h("select", { class: "input" });
  for (const [v, k] of [["", "br.case_keep"], ["lower", "br.case_lower"], ["upper", "br.case_upper"]]) extSel.append(h("option", { value: v, text: t(k) }));
  const table = h("div", { class: "br-table" });
  const summary = h("span", { class: "muted small" });
  body.append(
    h("div", { class: "field" }, h("span", { text: t("br.pattern") }), pattern), tokens,
    h("div", { class: "grid3" }, h("div", { class: "field" }, h("span", { text: t("br.start") }), start), h("div", { class: "field" }, h("span", { text: t("br.step") }), step),
      h("div", { class: "field" }, h("span", { text: t("br.case") }), caseSel)),
    h("div", { class: "grid3" }, h("div", { class: "field" }, h("span", { text: t("br.find") }), find), h("div", { class: "field" }, h("span", { text: t("br.replace") }), replace),
      h("div", { class: "field" }, h("span", { text: t("br.ext_case") }), extSel)),
    h("div", { style: { display: "flex", gap: "16px", margin: "-4px 0 12px" } }, h("label", { class: "check" }, regex, t("br.regex")), h("label", { class: "check" }, matchCase, t("br.match_case"))),
    table,
  );
  let lastPreview = null;
  const update = debounce(async () => {
    Object.assign(rule, { pattern: pattern.value || "{name}", start: +start.value || 0, step: +step.value || 1, find: find.value, replace: replace.value,
      regex: regex.checked, match_case: matchCase.checked, case: caseSel.value, ext_case: extSel.value });
    try {
      const res = await api("rename/preview", { paths, rule });
      lastPreview = res;
      table.innerHTML = res.items.slice(0, 500).map((r) => `<div class="br-row${r.error ? " err" : r.changed ? "" : " same"}"><span title="${esc(r.old)}">${esc(r.old)}</span>${icon("right", "sm")}<span class="new" title="${esc(r.error ? t("br.err." + r.error) : r.new)}">${esc(r.new)}${r.error ? " — " + esc(t("br.err." + r.error) !== "br.err." + r.error ? t("br.err." + r.error) : r.error) : ""}</span></div>`).join("") +
        (res.items.length > 500 ? `<div class="br-row same"><span>… ${res.items.length - 500}</span></div>` : "");
      summary.textContent = t("br.summary", { changed: res.changed, total: res.items.length, errors: res.errors });
      okBtn.disabled = !!res.errors || !res.changed;
    } catch (err) { summary.textContent = err.message; okBtn.disabled = true; }
  }, 120);
  for (const el of [pattern, start, step, find, replace]) el.addEventListener("input", update);
  for (const el of [regex, matchCase, caseSel, extSel]) el.addEventListener("change", update);
  const dlg = modal({ title: t("br.title", { n: itemsWord(paths.length) }), icon: "rename", body, wide: true, left: summary,
    buttons: [{ label: t("common.cancel"), cls: "ghost" }, { label: t("br.apply"), cls: "primary", primary: true, run: async () => {
      if (!lastPreview || lastPreview.errors) return false;
      try { const res = await api("rename/batch", { paths, rule }); if (res.op) app.trackOp(res.op); } catch (err) { showError(err); return false; }
      return true;
    } }] });
  const okBtn = $$(".btn.primary", dlg.foot).pop();
  update();
  setTimeout(() => { pattern.focus(); pattern.select(); }, 30);
}

// ------------------------------------------------------------------ settings
/** r41-files: the settings window lives in feat41.js now (built from the schema, every change applied at once). */
export function settingsDialog(app, group) { return import("./feat41.js").then((m) => m.settingsDialog(app, group)); }
