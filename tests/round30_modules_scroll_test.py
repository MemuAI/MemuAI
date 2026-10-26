# -*- coding: utf-8 -*-
"""Round 30: no needless scrollbars and no horizontal overflow in the built-in modules (headless Chromium).

Runs the real playground (app/pgstatic + app/playground_api) on a free local port with the isolated test data
dir, opens every built-in module in the real module window (document ?chrome=host, the single header) and walks
the «Characters» screens (home, the character editor and each of its tabs, a chat, every drawer tab and the
modals behind the header actions). For 1366×768, 1920×1080 and a narrow 800×600 window, light and dark, floating
and expanded, it fails when:

- an element shows a scrollbar while its content (almost) fits — scrollHeight/scrollWidth within 4 px of the box
  (a stray padding/outline pixel used to give the tab strip and the tag bar a bar of their own), a one-row strip
  (under 64 px) shows a vertical bar at all or a visible horizontal one (ribbons hide it — the bridge scrolls them
  with the wheel), or cuts its own row (overflow hidden + squeezed by a flex parent);
- something sticks out past the right edge of the module page or of the module window / top bar of MemuAI.

Scrollbars are real here: Playwright hides them by default (--hide-scrollbars), the test turns that off.
Skipped when Playwright or a Chromium build is not available (MEMU_TEST_CHROMIUM may point at a binary).

Manual run with screenshots of every checked state:  python3 tests/round30_modules_scroll_test.py /tmp/shots
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = ("characters", "translator", "catalog", "api-models", "benchmarks", "design", "studio")
SIZES = {"1366": (1366, 768), "1920": (1920, 1080), "narrow": (800, 600)}
SHOTS = os.environ.get("MEMU_SCROLL_SHOTS", "")

def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""

CHECK = r"""(scope) => {
  const out = [];
  const W = document.documentElement.clientWidth;
  const name = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
    (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/).slice(0, 3).join(".") : "");
  const path = (el) => { const parts = []; for (let n = el, i = 0; n && n.nodeType === 1 && i < 4; n = n.parentElement, i += 1) parts.unshift(name(n)); return parts.join(" > "); };
  const roots = scope ? scope.flatMap((sel) => Array.from(document.querySelectorAll(sel))) : [document.body];
  const seen = new Set();
  const shown = (el) => { for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const cs = getComputedStyle(n); if (cs.display === "none" || cs.visibility === "hidden" || Number(cs.opacity) === 0) return false; } return true; };
  const clipped = (el) => {
    for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflowX !== "visible") return true;
      if (cs.position === "fixed") return false;
    }
    return false;
  };
  for (const root of roots) {
    for (const el of [root, ...root.querySelectorAll("*")]) {
      if (seen.has(el) || el === document.documentElement || el === document.body) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height || cs.display === "none" || cs.visibility === "hidden") continue;
      if (cs.overflowX !== "visible" && cs.overflowX !== "clip" || cs.overflowY !== "visible" && cs.overflowY !== "clip") {
        const b = (side) => parseFloat(cs["border" + side + "Width"]) || 0;
        // scrollbar-gutter: stable only reserves an empty gutter (no bar is painted) so the layout never jumps.
        const vbar = /stable/.test(cs.scrollbarGutter || "") && el.scrollHeight <= el.clientHeight ? 0 :
          el.offsetWidth - el.clientWidth - b("Left") - b("Right");
        const hbar = el.offsetHeight - el.clientHeight - b("Top") - b("Bottom");
        if (vbar > 0 && el.scrollHeight - el.clientHeight <= 4 && shown(el))
          out.push(`vertical scrollbar, content fits (${el.scrollHeight}/${el.clientHeight}px): ${path(el)}`);
        if (hbar > 0 && el.scrollWidth - el.clientWidth <= 4 && shown(el))
          out.push(`horizontal scrollbar, content fits (${el.scrollWidth}/${el.clientWidth}px): ${path(el)}`);
        // A one-row strip (tabs, chips, a toolbar) never scrolls up and down: a bar there is a few stray pixels.
        if (vbar > 0 && el.clientHeight < 64 && shown(el))
          out.push(`vertical scrollbar on a one-row strip (${el.scrollHeight}/${el.clientHeight}px): ${path(el)}`);
        // A horizontal strip hides its bar (scrollbar-width:none, the bridge turns the wheel into a sideways scroll).
        if (hbar > 0 && el.clientHeight < 64 && shown(el))
          out.push(`visible scrollbar under a horizontal strip (${el.scrollWidth}/${el.clientWidth}px): ${path(el)}`);
        // …and must not cut its own row either (a flex parent squeezed the tab strip of screen №2).
        if (cs.overflowY === "hidden" && el.clientHeight < 64 && el.scrollHeight > el.clientHeight + 2 &&
            el.firstElementChild && cs.textOverflow !== "ellipsis" && shown(el))
          out.push(`one-row strip cuts its content (${el.scrollHeight}/${el.clientHeight}px): ${path(el)}`);
      }
      if (r.right > W + 1 && r.left < W - 1 && cs.position !== "fixed" && !clipped(el) && shown(el))
        out.push(`sticks out past the right edge (${Math.round(r.right)} > ${W}): ${path(el)}`);
    }
  }
  if (!scope && document.documentElement.scrollWidth > W + 1 && getComputedStyle(document.documentElement).overflowX !== "hidden")
    out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${W})`);
  return out;
}"""

HEAD_OVERLAP = r"""() => {
  const out = [];
  const heads = [["#module-headbar:not([hidden])", ".mhb-id", ".mhb-actions > :not([hidden]), .mhb-tools > :not([hidden])"],
                 [".module-window:not([hidden]) .module-window-bar", "[data-win=title]", ".module-window-actions > :not([hidden]), .module-window-tools > :not([hidden])"]];
  for (const [headSel, nameSel, partSel] of heads) {
    for (const head of document.querySelectorAll(headSel)) {
      if (!head.getClientRects().length || getComputedStyle(head).display === "none") continue;
      const name = head.querySelector(nameSel);
      if (!name || !name.getClientRects().length) continue;
      const a = name.getBoundingClientRect();
      if (name.scrollWidth > name.clientWidth + 1 && getComputedStyle(name).textOverflow !== "ellipsis")
        out.push(`module name cut (${name.scrollWidth}/${name.clientWidth}px) in ${headSel}`);
      for (const part of head.querySelectorAll(partSel)) {
        const b = part.getBoundingClientRect();
        if (!b.width || getComputedStyle(part).display === "none") continue;
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)
          out.push(`module name under ${part.className || part.tagName} in ${headSel}`);
      }
    }
  }
  return out;
}"""

HOST_SCOPE = [".module-window:not([hidden])", "#module-headbar:not([hidden])", "#module-taskbar", ".topbar.appbar"]

def _wait(target, expression: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            if target.evaluate(f"() => !!({expression})"):
                return
        except Exception:
            pass
        if time.time() > deadline:
            raise AssertionError(f"timed out waiting for: {expression}")
        time.sleep(0.05)

class Walker:
    """Opens the modules in the real MemuAI page and collects scrollbar/overflow problems."""

    def __init__(self, browser, port: int, shots: str = ""):
        self.browser, self.port, self.shots = browser, port, shots
        self.problems: list = []

    def page(self, size, theme: str, lang: str):
        from app import pgdata
        pgdata.save_preferences({"profile_onboarded": True, "profile_name": "Test", "theme": theme, "language": lang})
        page = self.browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors: list = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{self.port}/", wait_until="networkidle")
        _wait(page, "window.MemuModules && document.body.classList.contains('boot-ready')", 30)
        page.wait_for_timeout(350)
        page.evaluate("(t) => { document.documentElement.dataset.theme = t; }", theme)
        page.errors = errors
        return page

    def frame(self, page, module_id: str):
        handle = page.query_selector(f".module-window[data-module='{module_id}'] iframe")
        return handle.content_frame() if handle else None

    def open(self, page, module_id: str):
        page.evaluate("(id) => { MemuModules.closeAll(); return MemuModules.refresh().then(() => MemuModules.open(id)); }", module_id)
        _wait(page, f"(() => {{ const w = document.querySelector(\".module-window[data-module='{module_id}']\"); return w && !w.hidden; }})()")
        frame = None
        deadline = time.time() + 15
        while time.time() < deadline:
            frame = self.frame(page, module_id)
            if frame and "chrome=host" in frame.url:
                try:
                    if frame.evaluate("() => document.readyState === 'complete' && !!window.Memu"):
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        page.wait_for_timeout(900)
        return frame

    def expand(self, page, module_id: str, on: bool):
        page.evaluate("([id, on]) => { const w = document.querySelector(`.module-window[data-module='${id}']`);"
                      " const full = w && w.dataset.full === '1'; if (on !== full) MemuModules.snap(id, on ? 'max' : 'restore'); }",
                      [module_id, on])
        page.wait_for_timeout(500)

    def check(self, page, frame, label: str) -> None:
        found = [f"{label} [host] {p}" for p in page.evaluate(CHECK, HOST_SCOPE) + page.evaluate(HEAD_OVERLAP)]
        if frame:
            found += [f"{label} [module] {p}" for p in frame.evaluate(CHECK, None)]
        self.problems.extend(found)
        if self.shots:
            Path(self.shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(self.shots) / (label.replace("/", "_").replace(" ", "_") + ".png")))

    def module_states(self, page, module_id: str, label: str) -> None:
        frame = self.open(page, module_id)
        for expanded in (False, True):
            self.expand(page, module_id, expanded)
            self.check(page, frame, f"{label}/{module_id}/{'expanded' if expanded else 'floating'}")
            if module_id == "characters" and expanded:
                self.characters(page, frame, f"{label}/characters")
        self.expand(page, module_id, False)

    @staticmethod
    def close_modals(frame) -> None:

        frame.evaluate("() => document.querySelectorAll('.ch-modal-head > button:last-child').forEach((b) => b.click())")
        frame.wait_for_timeout(300)

    def characters(self, page, frame, label: str) -> None:
        fe = frame.evaluate

        fe("() => { const M = window.MemuCharacters; M.S.view = 'home'; M.S.char = null; M.S.chat = null; M.render(); }")
        frame.wait_for_timeout(300)

        fe("() => { const b = Array.from(document.querySelectorAll('button')).find((x) => /Новый персонаж|New character/.test(x.textContent)); b && b.click(); }")
        _wait(frame, "document.querySelector('.ch-editor')")
        frame.wait_for_timeout(300)
        tabs = fe("() => document.querySelectorAll('.ch-modal .mu-tab').length")
        for index in range(max(1, tabs)):
            fe("(i) => { const t = document.querySelectorAll('.ch-modal .mu-tab')[i]; t && t.click(); }", index)
            frame.wait_for_timeout(200)
            self.check(page, frame, f"{label}/editor-tab{index}")
        self.close_modals(frame)

        fe("""() => { const M = window.MemuCharacters; const tags = ["fantasy", "witch", "magic", "tower", "apprentice", "kind",
                 "mentor", "slow burn", "adventure", "female", "original", "lore", "school", "drama"];
            const c = M.newCharacter({ name: "Alice", description: "{{char}} is a kind witch.", first_mes: "Welcome, {{user}}! " +
              "This is a long greeting line that wraps across the bubble so the layout gets a real message to show.", tags });
            // One Alice for all passes: a list that grows by one card per pass ends up a few px taller than the sidebar.
            const known = (M.S.index || []).find((row) => row.name === "Alice");
            if (known) return M.openCharacter(known.id);
            return M.saveCharacter(c).then(() => { M.render(); return M.openCharacter(c.id); }); }""")
        _wait(frame, "window.MemuCharacters.S.view === 'chat' && document.querySelectorAll('.ch-msg').length >= 1")
        fe("""() => { const M = window.MemuCharacters; M.S.chat.messages.push({ id: "m-user-1", role: "user", name: "User", date: Date.now(),
            mes: "Hi Alice, teach me a spell. A second sentence makes this bubble wrap onto another line as well." }); M.render(); }""")
        frame.wait_for_timeout(500)
        self.check(page, frame, f"{label}/chat")
        for tab in ("preset", "connection", "worlds", "personas", "appearance", "extras"):
            fe("(t) => window.MemuCharacters.openDrawer(t)", tab)
            frame.wait_for_timeout(250)
            self.check(page, frame, f"{label}/drawer-{tab}")

        for action in ("chats", "note", "search"):

            if not page.evaluate("(a) => { const b = document.querySelector(`#module-headbar:not([hidden]) [data-mtb='${a}']:not([hidden])`);"
                                 " if (b) b.click(); return !!b; }", action):
                continue
            frame.wait_for_timeout(350)
            self.check(page, frame, f"{label}/action-{action}")
            self.close_modals(frame)
        fe("() => { const M = window.MemuCharacters; M.S.view = 'home'; M.S.char = null; M.S.chat = null; M.render(); }")
        frame.wait_for_timeout(300)
        self.check(page, frame, f"{label}/home-with-tags")

    def run(self, size_name: str, themes=("light", "dark"), modules=MODULES) -> list:
        for theme in themes:
            lang = "ru" if theme == "light" else "en"
            page = self.page(SIZES[size_name], theme, lang)
            try:
                for module_id in modules:
                    self.module_states(page, module_id, f"{size_name}/{theme}/{lang}")
                self.problems.extend(f"{size_name}/{theme} page error: {e}" for e in page.errors)
            finally:
                page.close()
        return self.problems

class _Server:
    def __init__(self):
        import uvicorn
        from app import modules_host
        from app.playground_api import create_app
        root = modules_host.default_root()
        root.mkdir(parents=True, exist_ok=True)
        for module_id in MODULES:
            try:
                modules_host.configure_module(root, module_id, enabled=True)
            except Exception:
                pass
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 60
        while not self.server.started and time.time() < deadline:
            time.sleep(0.1)
        if not self.server.started:  # pragma: no cover
            raise unittest.SkipTest("playground server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)

@unittest.skipUnless(_chromium(), "Chromium is not installed")
class ModulesScrollbarsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright unavailable: {exc}")
        cls.srv = _Server()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"],
                                              ignore_default_args=["--hide-scrollbars"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.srv.stop()

    def walk(self, size_name: str):
        problems = Walker(self.browser, self.srv.port, SHOTS).run(size_name)
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_1366(self):
        self.walk("1366")

    def test_1920(self):
        self.walk("1920")

    def test_narrow_window(self):
        self.walk("narrow")

if __name__ == "__main__":
    import tempfile
    from playwright.sync_api import sync_playwright
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-scroll-"))
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/memu-scroll-shots"
    sizes = sys.argv[2:] or list(SIZES)
    srv = _Server()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"], ignore_default_args=["--hide-scrollbars"])
        for size in sizes:
            for problem in Walker(browser, srv.port, out).run(size):
                print(problem)
        browser.close()
    srv.stop()
