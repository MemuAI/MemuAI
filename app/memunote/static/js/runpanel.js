

import { $, api, esc, icon, t, toast, modal, kbd, basename, dirname, extname, copyText } from "./core.js";

export const RUNNABLE = new Set([".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".mts", ".ps1", ".bat", ".cmd", ".sh", ".bash", ".zsh",
  ".rb", ".php", ".pl", ".lua", ".r", ".go", ".java", ".kts", ".dart", ".jl", ".swift", ".groovy", ".vbs", ".ahk"]);
export const isRunnable = (tab) => !!tab && ["text", "html"].includes(tab.kind) && RUNNABLE.has(extname(tab.path || tab.name));

const MAX_LINES = 20000;
const LINE_CAP = 100000;
const ANSI_COLORS = ["#4d4d4d", "#e5534b", "#57ab5a", "#c69026", "#539bf5", "#b083f0", "#39c5cf", "#d0d0d0"];
const ANSI_BRIGHT = ["#768390", "#ff938a", "#6bc46d", "#daaa3f", "#6cb6ff", "#dcbdfb", "#56d4dd", "#ffffff"];

export class RunPanel {
  constructor(hooks) {
    this.hooks = hooks;
    this.run = null;
    this.el = document.createElement("section");
    this.el.className = "mn-run"; this.el.hidden = true;
    this.el.setAttribute("role", "region");
    const h = +localStorage.getItem("mn_run_h");
    if (h) this.el.style.height = Math.max(120, Math.min(innerHeight * 0.8, h)) + "px";
    this.render();
    const status = $("#status");
    (status?.parentElement || document.body).insertBefore(this.el, status || null);
  }
  render() {
    this.el.setAttribute("aria-label", t("run_output"));
    this.el.innerHTML = `<div class="mn-run-grip" title="${esc(t("run_resize"))}"></div>
      <header class="mn-run-head">
        <span class="mn-run-title">${icon("terminal")}<span>${esc(t("run_output"))}</span></span>
        <span class="mn-run-file"></span>
        <span class="mn-run-state" aria-live="polite"></span>
        <span class="spacer"></span>
        <button class="icon-btn sm" data-r="again" title="${esc(t("run_again"))} (F5)">${icon("retry")}</button>
        <button class="icon-btn sm danger-hover" data-r="stop" title="${esc(t("run_stop"))} (${kbd("Shift+F5")})">${icon("stop")}</button>
        <button class="icon-btn sm" data-r="clear" title="${esc(t("run_clear"))}">${icon("eraser")}</button>
        <button class="icon-btn sm" data-r="copy" title="${esc(t("run_copy"))}">${icon("copy")}</button>
        <button class="icon-btn sm" data-r="close" title="${esc(t("run_close"))} (${kbd("Ctrl+`")})">${icon("close")}</button>
      </header>
      <div class="mn-run-cmd"></div>
      <pre class="mn-run-out" tabindex="0"></pre>
      <form class="mn-run-in" hidden><span class="mn-run-prompt">›</span><input type="text" spellcheck="false" autocomplete="off" placeholder="${esc(t("run_input_ph"))}" aria-label="${esc(t("run_input"))}"><button type="button" class="btn sm ghost" data-r="eof" title="${esc(t("run_eof_hint"))}">EOF</button><button type="submit" class="btn sm">${esc(t("run_send"))}</button></form>`;
    this.out = this.el.querySelector(".mn-run-out");
    this.input = this.el.querySelector(".mn-run-in input");
    this.el.querySelector(".mn-run-head").onclick = (e) => { const b = e.target.closest("[data-r]"); if (b) this.action(b.dataset.r); };
    this.el.querySelector("[data-r=eof]").onclick = () => this.eof();
    this.el.querySelector(".mn-run-in").onsubmit = (e) => { e.preventDefault(); this.send(); };
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "d" && e.ctrlKey) { e.preventDefault(); this.eof(); }
      if (e.key === "c" && e.ctrlKey && !this.input.selectionEnd) { e.preventDefault(); this.stop(); }
      if (e.key === "ArrowUp" && this.history?.length) { e.preventDefault(); this.hIdx = Math.max(0, (this.hIdx ?? this.history.length) - 1); this.input.value = this.history[this.hIdx]; }
      if (e.key === "ArrowDown" && this.history?.length) { e.preventDefault(); this.hIdx = Math.min(this.history.length, (this.hIdx ?? this.history.length) + 1); this.input.value = this.history[this.hIdx] || ""; }
    });
    this.out.addEventListener("click", (e) => { const a = e.target.closest("[data-path]"); if (a) this.hooks.openAt(a.dataset.path, +a.dataset.line || 1, +a.dataset.col || 1); });
    const grip = this.el.querySelector(".mn-run-grip");
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault(); grip.setPointerCapture(e.pointerId); document.body.classList.add("resizing-v");
      const y0 = e.clientY, h0 = this.el.getBoundingClientRect().height;
      const move = (ev) => { this.el.style.height = Math.max(110, Math.min(innerHeight * 0.8, h0 - (ev.clientY - y0))) + "px"; };
      const up = () => { grip.removeEventListener("pointermove", move); document.body.classList.remove("resizing-v"); localStorage.setItem("mn_run_h", Math.round(this.el.getBoundingClientRect().height)); };
      grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up, { once: true });
    });
    grip.addEventListener("dblclick", () => { this.el.style.height = ""; localStorage.removeItem("mn_run_h"); });
    if (this.run) this.paintHead();
  }
  get visible() { return !this.el.hidden; }
  show(on = true) { this.el.hidden = !on; document.body.classList.toggle("run-open", on); if (!on) this.hooks.focusEditor?.(); }
  toggle() { this.show(this.el.hidden); }
  action(a) {
    if (a === "again") { const tab = this.run?.tab && this.hooks.tabAlive(this.run.tab) ? this.run.tab : this.hooks.activeTab(); this.start(tab); }
    if (a === "stop") this.stop();
    if (a === "clear") { this.out.innerHTML = ""; this.cur = null; this.lines = 0; }
    if (a === "copy") copyText(this.out.textContent).then(() => toast(t("copied"), { kind: "ok", ms: 1200 }));
    if (a === "close") this.show(false);
  }

  async start(tab, opts = {}) {
    if (!tab) return;
    if (!isRunnable(tab)) { toast(t("run_not_runnable", { ext: extname(tab.path || tab.name) || t("run_no_ext") }), { kind: "error" }); return; }
    if (this.run && !this.run.done) {
      const ok = await modal({ title: t("run_busy_title"), body: `<p>${esc(t("run_busy_text", { name: this.run.name }))}</p>`,
        buttons: [{ label: t("cancel"), value: null }, { label: t("run_restart"), primary: true, value: true }] });
      if (!ok) return;
      await this.stop(); await this.waitDone(3000);
    }
    if (!tab.path) { toast(t("run_save_first")); if (!(await this.hooks.saveAs(tab)) || !tab.path) return; }
    else if (tab.dirty && !(await this.hooks.saveTab(tab, { silent: true }))) return;
    let info;
    try { info = await api("run/info?path=" + encodeURIComponent(tab.path)); }
    catch (e) { toast(t("run_failed") + ": " + e.message, { kind: "error" }); return; }
    if (!info.runnable) { toast(t("run_not_runnable", { ext: info.ext }), { kind: "error" }); return; }
    if (!info.found) { await this.missing(tab, info); return; }
    let args = info.args || "";
    if (opts.askArgs) {
      const v = await this.hooks.prompt(t("run_with_args"), t("run_args_label"), args);
      if (v === null || v === undefined) return;
      args = v;
    }
    const body = { path: tab.path, args };
    if (!info.trusted || info.internet) {
      const c = await this.confirmRun(tab, info);
      if (!c) return;
      Object.assign(body, { confirmed: true, remember: c.remember });
    }
    let r = await api("run/start", body, { allowError: true });
    if (r.error === "confirm_required") {
      const c = await this.confirmRun(tab, info);
      if (!c) return;
      r = await api("run/start", { ...body, confirmed: true, remember: c.remember }, { allowError: true });
    }
    if (!r.ok) { this.startError(r); return; }
    this.attach(tab, r);
  }
  startError(r) {
    const map = { batch_unsafe: "run_err_batch", no_interpreter: "run_err_nointerp", too_many_runs: "run_err_many", start_failed: "run_err_start" };
    toast((map[r.error] ? t(map[r.error]) : t("run_failed")) + (r.detail ? ": " + r.detail : ""), { kind: "error", ms: 8000 });
  }
  async confirmRun(tab, info) {
    const body = `<div class="mn-run-confirm"><div class="mn-run-confirm-ic ${info.internet ? "warn" : ""}">${icon(info.internet ? "warn" : "terminal", "big")}</div><div>
      <p>${esc(t("run_confirm_text", { name: tab.name }))}</p>
      ${info.internet ? `<p class="mn-run-net">${esc(t("run_confirm_internet"))}</p>` : ""}
      <dl class="mn-run-dl"><dt>${esc(t("run_confirm_cmd"))}</dt><dd><code>${esc(info.cmd || "")}</code></dd><dt>${esc(t("run_confirm_dir"))}</dt><dd><code>${esc(dirname(tab.path))}</code></dd></dl>
      ${info.internet ? "" : `<label class="check"><input type="checkbox" id="run-remember"> ${esc(t("run_confirm_remember"))}</label>`}
      </div></div>`;
    return modal({ title: t("run_confirm_title"), body, cls: "run-confirm",
      buttons: [{ label: t("cancel"), value: null }, { label: t("run_button"), primary: true, value: (b) => ({ remember: !!b.querySelector("#run-remember")?.checked }) }] });
  }
  async missing(tab, info) {
    const r = await modal({ title: t("run_missing_title", { lang: info.lang }),
      body: `<p>${esc(t("run_missing_text", { lang: info.lang, exe: info.missing }))}</p>${info.hint ? `<p class="muted small">${esc(t("run_missing_hint"))} <code>${esc(info.hint)}</code></p>` : ""}`,
      buttons: [{ label: t("cancel"), value: null }, { label: t("run_pick_interp"), value: "pick" }, ...(info.hint && /^https?:/.test(info.hint) ? [{ label: t("run_download"), primary: true, value: "dl" }] : [])] });
    if (r === "dl") api("external", { url: info.hint.split(" ")[0] }).catch(() => {});
    if (r === "pick") { if (await this.pickInterpreter(info.ext)) this.start(tab); }
  }
  async pickInterpreter(ext) {
    const paths = await this.hooks.fileDialog("open", { filters: [t("run_filter_exe") + " (*.exe;*.cmd;*.bat)", t("filter_all") + " (*.*)"], multiple: false });
    let p = paths?.[0];
    if (!p) return false;
    if (/\s/.test(p) && !/^".*"$/.test(p)) p = `"${p}"`;
    const r = await api("run/config", { ext, interpreter: p }, { allowError: true });
    if (!r.ok) { toast(t("run_interp_bad"), { kind: "error" }); return false; }
    toast(t("run_interp_set", { ext }), { kind: "ok" });
    return true;
  }
  async resetInterpreter(ext) { await api("run/config", { ext, interpreter: "" }); toast(t("run_interp_reset"), { kind: "ok" }); }

  attach(tab, r) {
    this.run = { id: r.id, tab, path: tab.path, name: tab.name, since: 0, done: false, code: null, started: performance.now(), cmd: r.cmd, cwd: r.cwd };
    this.out.innerHTML = ""; this.cur = null; this.lines = 0; this.style = {};
    this.show(true);
    this.el.querySelector(".mn-run-in").hidden = false;
    this.input.value = ""; this.history = this.history || []; this.hIdx = undefined;
    this.paintHead();
    this.tick = setInterval(() => this.paintHead(), 200);
    setTimeout(() => this.input.focus({ preventScroll: true }), 30);
    this.hooks.onState?.();
    this.loop(this.run);
  }
  async loop(run) {
    while (this.run === run && !run.done) {
      let r;
      try { r = await api(`run/poll?id=${run.id}&since=${run.since}`); }
      catch (e) { if (e.status === 404) { run.done = true; run.code = null; break; } await new Promise((res) => setTimeout(res, 800)); continue; }
      if (this.run !== run) return;
      this.writeMany(r.chunks);
      run.since = r.seq; run.elapsed = r.elapsed;
      if (r.done) { run.done = true; run.code = r.code; }
    }
    if (this.run !== run) return;
    clearInterval(this.tick);
    this.el.querySelector(".mn-run-in").hidden = true;
    this.paintHead();
    this.hooks.onState?.();
    if (document.activeElement === this.input) this.hooks.focusEditor?.();
  }
  waitDone(ms) { const run = this.run; const t0 = Date.now(); return new Promise((res) => { const i = setInterval(() => { if (!run || run.done || Date.now() - t0 > ms) { clearInterval(i); res(); } }, 50); }); }
  paintHead() {
    const run = this.run; if (!run) return;
    this.el.querySelector(".mn-run-file").textContent = run.name;
    this.el.querySelector(".mn-run-file").title = run.path;
    const sec = run.done && run.elapsed !== undefined ? run.elapsed : (performance.now() - run.started) / 1000;
    const time = sec < 60 ? sec.toFixed(1) + " " + t("run_sec") : Math.floor(sec / 60) + ":" + String(Math.floor(sec % 60)).padStart(2, "0");
    const st = this.el.querySelector(".mn-run-state");
    if (!run.done) { st.className = "mn-run-state running"; st.innerHTML = `<i></i>${esc(t("run_running"))} · ${esc(time)}`; }
    else if (run.stopped) { st.className = "mn-run-state stopped"; st.innerHTML = `${icon("stop")}${esc(t("run_stopped"))} · ${esc(time)}`; }
    else if (run.code === 0) { st.className = "mn-run-state ok"; st.innerHTML = `${icon("check")}${esc(t("run_ok"))} · ${esc(time)}`; }
    else { st.className = "mn-run-state fail"; st.innerHTML = `${icon("warn")}${esc(t("run_code", { code: run.code ?? "?" }))} · ${esc(time)}`; }
    this.el.querySelector("[data-r=stop]").disabled = run.done;
    const cmd = this.el.querySelector(".mn-run-cmd");
    cmd.innerHTML = `<span class="mn-run-dollar">$</span><code>${esc(run.cmd || "")}</code><span class="mn-run-cwd" title="${esc(run.cwd || "")}">${icon("folder")}${esc(basename(run.cwd || "") || run.cwd || "")}</span>`;
  }
  async stop() {
    const run = this.run; if (!run || run.done) return;
    run.stopped = true;
    try { await api("run/stop", { id: run.id }); } catch {  }
  }
  async send() {
    const run = this.run; if (!run || run.done) return;
    const text = this.input.value;
    this.input.value = "";
    if (text) { this.history.push(text); if (this.history.length > 100) this.history.shift(); }
    this.hIdx = undefined;
    this.write(text + "\n", "in");
    try { await api("run/input", { id: run.id, text: text + "\n" }); } catch (e) { this.write(t("run_stdin_closed") + "\n", "sys"); }
  }
  async eof() { const run = this.run; if (!run || run.done) return; try { await api("run/eof", { id: run.id }); this.write("^D\n", "sys"); } catch {  } }

  newLine() { this.cur = document.createElement("span"); this.cur.className = "ln"; this.curLen = 0; this.out.append(this.cur); this.lines++; if (this.lines > MAX_LINES) { this.out.firstChild?.remove(); this.lines--; } }
  writeMany(chunks) {
    if (!chunks?.length) return;
    const atBottom = this.out.scrollHeight - this.out.scrollTop - this.out.clientHeight < 40;

    let need = MAX_LINES;
    for (let i = chunks.length - 1; i >= 0; i--) {
      const text = chunks[i].t;
      let pos = text.length, n = 0;
      while (n < need && pos > 0 && (pos = text.lastIndexOf("\n", pos - 1)) >= 0) n++;
      if (n === need && pos >= 0) {
        chunks = [{ ...chunks[i], t: text.slice(pos + 1) }, ...chunks.slice(i + 1)];
        this.out.textContent = ""; this.cur = null; this.lines = 0;
        break;
      }
      need -= n;
    }
    for (const c of chunks) this.write(c.t, c.s, true);
    if (atBottom) this.out.scrollTop = this.out.scrollHeight;
  }
  write(text, stream, batched = false) {
    const atBottom = !batched && this.out.scrollHeight - this.out.scrollTop - this.out.clientHeight < 40;

    const parts = text.split(/(\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r|\n)/);
    for (const p of parts) {
      if (!p) continue;
      if (p === "\n") { if (!this.cur) this.newLine(); this.cur.append("\n"); this.linkify(this.cur); this.cur = null; continue; }
      if (p === "\r") { if (this.cur) { this.cur.textContent = ""; this.curLen = 0; } continue; }
      if (p.startsWith("\x1b[")) { if (p.endsWith("m")) this.sgr(p.slice(2, -1)); continue; }
      if (p.startsWith("\x1b]")) continue;
      if (!this.cur) this.newLine();
      if (this.curLen >= LINE_CAP) continue;
      const room = LINE_CAP - this.curLen;
      let piece = p;
      if (piece.length > room) {
        piece = piece.slice(0, room);
        const cut = document.createElement("span"); cut.className = "s-sys"; cut.textContent = " " + t("run_line_cut");
        this.curLen = LINE_CAP;
        this.cur.append(Object.assign(document.createElement("span"), { className: "s-" + stream, textContent: piece }), cut);
        continue;
      }
      this.curLen += piece.length;
      const s = document.createElement("span");
      s.className = "s-" + stream + (this.style.b ? " b" : "") + (this.style.i ? " i" : "") + (this.style.u ? " u" : "");
      if (this.style.fg) s.style.color = this.style.fg;
      if (this.style.bg) s.style.background = this.style.bg;
      s.textContent = piece;
      this.cur.append(s);
    }
    if (atBottom) this.out.scrollTop = this.out.scrollHeight;
  }
  sgr(codes) {
    const list = (codes || "0").split(";").map((x) => +x || 0);
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if (c === 0) this.style = {};
      else if (c === 1) this.style.b = true; else if (c === 3) this.style.i = true; else if (c === 4) this.style.u = true;
      else if (c === 22) this.style.b = false; else if (c === 23) this.style.i = false; else if (c === 24) this.style.u = false;
      else if (c >= 30 && c <= 37) this.style.fg = ANSI_COLORS[c - 30]; else if (c >= 90 && c <= 97) this.style.fg = ANSI_BRIGHT[c - 90];
      else if (c === 39) this.style.fg = ""; else if (c === 49) this.style.bg = "";
      else if (c >= 40 && c <= 47) this.style.bg = ANSI_COLORS[c - 40]; else if (c >= 100 && c <= 107) this.style.bg = ANSI_BRIGHT[c - 100];
      else if ((c === 38 || c === 48) && list[i + 1] === 5) { const n = list[i + 2]; const col = n < 8 ? ANSI_COLORS[n] : n < 16 ? ANSI_BRIGHT[n - 8] : xterm256(n); if (c === 38) this.style.fg = col; else this.style.bg = col; i += 2; }
      else if ((c === 38 || c === 48) && list[i + 1] === 2) { const col = `rgb(${list[i + 2] | 0},${list[i + 3] | 0},${list[i + 4] | 0})`; if (c === 38) this.style.fg = col; else this.style.bg = col; i += 4; }
    }
  }
  linkify(line) {
    if ((this.curLen || 0) > 2000) return;
    const text = line.textContent;
    const runDir = this.run?.cwd || "";
    const sep = runDir.includes("\\") ? "\\" : "/";
    let m = /File "([^"]+)", line (\d+)/.exec(text);
    let path, ln, col = 1, idx, len;
    if (m) { path = m[1]; ln = +m[2]; idx = m.index; len = m[0].length; }
    else {
      m = /((?:[A-Za-z]:)?[\w.\\/ ~-]*\.(?:py|js|mjs|cjs|ts|ps1|bat|cmd|sh|rb|php|pl|lua|go|java|kts|dart|jl|swift|r)):(\d+)(?::(\d+))?/i.exec(text);
      if (!m) return;
      path = m[1].trim(); ln = +m[2]; col = +m[3] || 1; idx = m.index + (m[0].length - m[0].trimStart().length); len = m[0].trim().length;
    }
    if (/^<|^node:/.test(path)) return;
    if (!/^([A-Za-z]:[\\/]|\/|\\\\)/.test(path)) path = runDir + sep + path.replace(/^\.[\\/]/, "");

    const before = text.slice(0, idx), link = text.slice(idx, idx + len), after = text.slice(idx + len);
    const cls = line.firstChild?.className || "s-out";
    line.textContent = "";
    if (before) { const s = document.createElement("span"); s.className = cls; s.textContent = before; line.append(s); }
    const a = document.createElement("span"); a.className = cls + " mn-run-link"; a.dataset.path = path; a.dataset.line = ln; a.dataset.col = col; a.title = t("run_goto"); a.textContent = link; line.append(a);
    if (after) { const s = document.createElement("span"); s.className = cls; s.textContent = after; line.append(s); }
  }
}
function xterm256(n) {
  if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})`; }
  n -= 16; const r = Math.floor(n / 36), g = Math.floor((n % 36) / 6), b = n % 6;
  const c = (x) => (x ? 55 + x * 40 : 0);
  return `rgb(${c(r)},${c(g)},${c(b)})`;
}
