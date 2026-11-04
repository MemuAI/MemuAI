/* jsdom smoke test for the sticky tunnel error panel (app/pgstatic/tgapp-settings.js).
 *
 * Runs the REAL settings card in a jsdom document with a stubbed fetch that
 * returns a fixed desktop_status() payload (tunnel stuck in "starting" with
 * a DNS error from the previous attempt) and asserts what a user would see:
 * the "Last tunnel error" panel is visible even while the next attempt is
 * starting (previously the error only showed in "error"/"failed" and
 * vanished on every start), with the full error text, the next-step advice
 * (including the VPN case), the phase/edge/attempts rows, the raw cloudflared
 * line and the attempt history; "Copy the error" copies the full diagnostic
 * text; "Dismiss" hides the panel until a NEWER error arrives; in English
 * the panel renders the English twins.
 * Exit code 2 means "jsdom is not installed here" — the pytest wrapper
 * turns that into a skip.
 */
"use strict";
const fs = require("fs");
const path = require("path");

let JSDOM;
try {
  JSDOM = require("jsdom").JSDOM;
} catch (e) {
  try {
    JSDOM = require("/tmp/jsdom-test/node_modules/jsdom").JSDOM;
  } catch (e2) {
    console.log("SKIP: jsdom not available");
    process.exit(2);
  }
}

const assert = require("assert/strict");
const ROOT = path.resolve(__dirname, "..");

const DNS_HINT = "Компьютер не может найти серверы Cloudflare (DNS): не резолвится _v2-origintunneld._tcp.argotunnel.com — системный DNS не отдаёт адреса Cloudflare. Смените DNS на 1.1.1.1 или 8.8.8.8 либо используйте свой туннель";
const DNS_HINT_EN = "The computer cannot find the Cloudflare servers (DNS): _v2-origintunneld._tcp.argotunnel.com does not resolve — the system DNS does not return Cloudflare addresses. Switch DNS to 1.1.1.1 or 8.8.8.8, or use your own tunnel";
const RAW_LINE = '2026-11-04T10:00:00Z ERR error="lookup _v2-origintunneld._tcp.argotunnel.com on 1.1.1.1:53: no such host" failed to dial to edge';
const NEXT_STEP = "Что делать: 1) нажмите «Повторить» — обход через DoH подхватывает край Cloudflare автоматически; 2) если раньше с этим же DNS и VPN работало, а теперь нет — отключите VPN на минуту и нажмите «Повторить» (VPN часто перехватывает DNS и не отдаёт SRV-записи argotunnel.com), затем включите VPN обратно; 3) смените DNS на 1.1.1.1 или 8.8.8.8 в настройках сети; 4) если не помогло — «Свой туннель» со своим доменом или «Свой адрес» (ngrok, VPS).";
const NEXT_STEP_EN = "What to do: 1) press “Retry” — the DoH bypass picks up the Cloudflare edge automatically; 2) if it used to work with the same DNS and VPN and stopped — turn the VPN off for a minute and press “Retry” (a VPN often intercepts DNS and does not return the argotunnel.com SRV records), then turn the VPN back on; 3) switch DNS to 1.1.1.1 or 8.8.8.8 in the network settings; 4) if that fails — “Own tunnel” with your domain or “Own URL” (ngrok, VPS).";

function diag() {
  return {
    host: "", error: "", kind: "", resolver: "doh:1.1.1.1", checked_at: 0,
    local_url: "http://127.0.0.1:8641/tgapp/", last_working_url: "", local_fallback: false,
    phase: "starting", edge_source: "doh:1.1.1.1", attempts_left: 4,
    dns_detail: "край Cloudflare через doh:1.1.1.1 (системный DNS не резолвит argotunnel.com)",
    last_error: DNS_HINT, last_error_en: DNS_HINT_EN, last_error_at: 1793688000,
    last_error_raw: RAW_LINE, last_error_kind: "dns", next_step: NEXT_STEP, next_step_en: NEXT_STEP_EN,
    error_history: [
      { at: 1793687940, attempt: 1, error: DNS_HINT, raw: RAW_LINE },
      { at: 1793688000, attempt: 2, error: DNS_HINT, raw: RAW_LINE },
    ],
    recent_lines: [RAW_LINE, "2026-11-04T10:00:01Z INF Tunnel state: starting"],
  };
}

function fixture() {
  const d = diag();
  return {
    config: { enabled: true, mode: "cloudflare", port: 8641, cf_protocol: "auto", cf_edge_ip: "auto", web_login: true, code_control: true, max_age_hours: 24 },
    status: Object.assign({ state: "starting", error: "", error_en: "", url: null, base: "", port: 8641, mode: "cloudflare",
      download: { done: 0, total: 0 }, detail: "", verified: false, retry_at: 0, protocol: "auto",
      failures: 2, local_url: "http://127.0.0.1:8641/tgapp/", diagnostics: d }, d),
    diagnostics: d, problems: [], cloudflared: true, windows: false,
    local_url: "http://127.0.0.1:8641/tgapp/",
  };
}

async function main() {
  const dom = new JSDOM("<!doctype html><html><head></head><body><div id=\"pane-remote\" class=\"active\"><div id=\"tg-settings\"></div></div></body></html>",
    { url: "http://127.0.0.1:8601/", pretendToBeVisual: true, runScripts: "outside-only" });
  const { window } = dom;
  const data = fixture();
  let copied = "";
  window.S = { preferences: { language: "ru" } };
  window.copyText = async (text) => { copied = text; };
  window.fetch = async () => ({ ok: true, json: async () => data });
  const code = fs.readFileSync(path.join(ROOT, "app", "pgstatic", "tgapp-settings.js"), "utf-8");
  window.eval(code);
  const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms || 50));
  await tick(100);

  const panel = () => window.document.querySelector(".tga-tunerr");
  // 1. The panel is visible while the NEXT attempt is starting.
  assert.ok(panel(), "sticky error panel must render in state 'starting'");
  const text = panel().textContent;
  assert.ok(text.includes("Последняя ошибка туннеля"), "panel title (ru)");
  assert.ok(text.includes("не резолвится _v2-origintunneld._tcp.argotunnel.com"), "full error text");
  assert.ok(text.includes("отключите VPN на минуту"), "next step names the VPN case");
  assert.ok(text.includes("Запускаю cloudflared"), "phase row");
  assert.ok(text.includes("обход через DoH (1.1.1.1)"), "edge source row");
  assert.ok(text.includes("осталось попыток: 4"), "attempts left row");
  assert.ok(text.includes("Что именно написал cloudflared"), "raw cloudflared details");
  assert.ok(text.includes(RAW_LINE), "raw cloudflared line");
  assert.ok(text.includes("История попыток (2)"), "attempt history");
  assert.ok(text.includes("попытка 2:"), "latest attempt in history");

  // 2. Copy copies the full diagnostic text.
  window.document.querySelector("#tga-err-copy").click();
  await tick(50);
  assert.ok(copied.includes("не резолвится _v2-origintunneld._tcp.argotunnel.com"), "copied: error");
  assert.ok(copied.includes("Вид: dns"), "copied: kind");
  assert.ok(copied.includes("отключите VPN на минуту"), "copied: next step");
  assert.ok(copied.includes(RAW_LINE), "copied: raw line");
  assert.ok(copied.includes("История попыток:"), "copied: history");
  assert.ok(copied.includes("попытка 2:"), "copied: latest attempt");

  // 3. Dismiss hides the panel; a NEWER error brings it back.
  window.document.querySelector("#tga-err-dismiss").click();
  await tick(50);
  assert.equal(panel(), null, "panel hidden after dismiss");
  assert.equal(window.localStorage.getItem("tga-tunerr-dismissed-at"), "1793688000");
  data.diagnostics.last_error_at = 1793688060;
  data.status.last_error_at = 1793688060;
  data.diagnostics.error_history.push({ at: 1793688060, attempt: 3, error: DNS_HINT, raw: RAW_LINE });
  window.MemuTgAppSettings.refresh();
  await tick(100);
  assert.ok(panel(), "panel returns for a newer error");
  assert.ok(panel().textContent.includes("попытка 3:"), "new attempt in history");

  // 4. English: the panel renders the English twins.
  window.S.preferences.language = "en";
  window.MemuTgAppSettings.refresh();
  await tick(100);
  assert.ok(panel(), "panel still renders in English");
  assert.ok(panel().textContent.includes("Last tunnel error"), "panel title (en)");
  assert.ok(panel().textContent.includes("does not resolve"), "error text (en)");
  assert.ok(panel().textContent.includes("turn the VPN off for a minute"), "next step (en)");

  console.log("tunnel error panel UI smoke OK");
  process.exit(0);
}

main().catch((error) => { console.error(error); process.exit(1); });
