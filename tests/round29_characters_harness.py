# -*- coding: utf-8 -*-
"""Browser harness for the «Characters» module (Round 29).

Builds the real module document (real bridge from modules_host.BRIDGE_JS) and runs it in Chromium
inside a sandboxed iframe whose parent is a small mock of the MemuAI host: an in-memory Memu.store,
Memu.asset reading the shipped assets/, a fake model list and a fake streaming Memu.complete that
records the messages it was sent. No network, no Proxy.

Used by tests/round29_characters_browser_test.py and for manual screenshots:
    python3 tests/round29_characters_harness.py /tmp/shots
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULE = ROOT / "app" / "module_builtins" / "characters"

def chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""

def module_html(lang: str = "en", theme: str = "dark") -> str:
    from app.modules_host import load_package, module_document
    with tempfile.TemporaryDirectory() as temp:
        package = load_package(Path(temp), "characters")
    return module_document(package, {}, env={"lang": lang, "theme": theme})

def assets() -> dict:
    out = {}
    for path in (MODULE / "assets").rglob("*.json"):
        out[path.relative_to(MODULE / "assets").as_posix()] = json.loads(path.read_text(encoding="utf-8"))
    return out

PARENT = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#111}
iframe{border:0;width:100%;height:100%}</style></head><body>
<iframe id="f" sandbox="allow-scripts" src="/module"></iframe>
<script>
window.__store = {};
window.__sent = [];
window.__downloads = [];
window.__reply = "Hello there! *waves* \"Nice to meet you.\"";
window.__assets = __ASSETS__;
const frame = document.getElementById("f");
function reply(id, result, error) { frame.contentWindow.postMessage({ type: "memu-module-result", id, result, error: error || "" }, "*"); }
function event(id, data) { frame.contentWindow.postMessage(Object.assign({ type: "memu-module-event", id }, data), "*"); }
window.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type !== "memu-module") return;
  const p = d.payload || {};
  switch (d.action) {
    case "store": {
      if (p.op === "get") return reply(d.id, { key: p.key, value: window.__store[p.key] ?? null, exists: p.key in window.__store });
      if (p.op === "get_many") { const v = {}; (p.keys || []).forEach((k) => v[k] = window.__store[k] ?? null); return reply(d.id, { values: v }); }
      if (p.op === "set") { window.__store[p.key] = JSON.parse(JSON.stringify(p.value)); return reply(d.id, { ok: true }); }
      if (p.op === "remove") { delete window.__store[p.key]; return reply(d.id, { ok: true }); }
      if (p.op === "list") return reply(d.id, { keys: Object.keys(window.__store).filter((k) => k.startsWith(p.prefix || "")).map((k) => ({ key: k, bytes: 1 })) });
      if (p.op === "usage") return reply(d.id, { bytes: JSON.stringify(window.__store).length, keys: Object.keys(window.__store).length });
      return reply(d.id, null, "bad op");
    }
    case "asset": {
      const v = window.__assets[p.name];
      return v === undefined ? reply(d.id, null, "Asset not found") : reply(d.id, { name: p.name, json: v });
    }
    case "models": return reply(d.id, [{ id: "test/model-a", name: "Model A", context: 128000 }, { id: "img/x", name: "Img", image: true }]);
    case "download": window.__downloads.push(p); return reply(d.id, { ok: true, path: "/tmp/" + p.name });
    case "clipboard": return reply(d.id, { ok: true });
    case "env": case "log": case "resize": case "routes": return reply(d.id, {});
    case "cancel": return reply(d.id, { ok: true });
    case "complete": {
      window.__sent.push(p);
      event(d.id, { event: "status", status: "queued" });
      const text = window.__reply;
      let i = 0;
      event(d.id, { event: "reasoning", delta: "Let me think." });
      const tick = () => {
        if (i < text.length) { event(d.id, { event: "delta", delta: text.slice(i, i + 7) }); i += 7; setTimeout(tick, 5); }
        else event(d.id, { event: "done", result: { text, reasoning: "Let me think.", usage: { prompt_tokens: 1234, completion_tokens: 12 }, finish_reason: "stop" } });
      };
      setTimeout(tick, 5);
      return;
    }
    case "net_import": {
      // Round 29 phase 2: Memu.fetchImport; a test sets window.__netImport(payload) -> result | {error}.
      window.__netCalls = (window.__netCalls || []).concat([p]);
      Promise.resolve(window.__netImport ? window.__netImport(p) : { error: "No network in the harness" }).then((r) => {
        if (r && r.error) reply(d.id, null, r.error); else reply(d.id, r);
      });
      return;
    }
    default: return reply(d.id, null, "Unknown action " + d.action);
  }
});
</script></body></html>"""

def open_page(pw, lang: str = "en", theme: str = "dark", size=(1360, 860)):
    browser = pw.chromium.launch(executable_path=chromium(), args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    doc = module_html(lang, theme)
    parent = PARENT.replace("__ASSETS__", json.dumps(assets(), ensure_ascii=False))
    errors: list = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=parent))
    page.route("http://harness.local/module", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
    page.goto("http://harness.local/")
    return browser, page, errors

def frame_of(page):
    return page.frames[1]

if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/characters-shots")
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, page, errors = open_page(pw)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out / "home.png"))
        print("errors:", errors)
        browser.close()
