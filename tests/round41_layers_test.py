"""Round 41 (r41-ui): every pop-up is on top and whole — an automatic crawler of menus, selects and «⋯».

The bug: in Settings → Modules the «⋯ More» menu opened UNDER the settings dialog, and in Settings → Web search the
«Service» list opened under the fields above it. Causes (fixed): a dialog's «over-modules» class (z 350) outlived the
dialog and stayed on the settings, above the context menu (z 320); the searchable list (mpick) lived inside its row,
in that row's stacking context. Now every pop-up is in the shared top layer (MemuLayers.float / z 100100).

The crawler opens, one by one, every trigger of a pop-up it finds (pretty-select, searchable list, model picker,
[aria-haspopup=menu|listbox|true], module «⋯», buttons titled «Ещё/More») — the trigger scrolled to the BOTTOM of
its scroll box (so lists flip upwards, the case of the bug) and to the top — and checks the new pop-up:
  * covered: document.elementFromPoint at 9 points of the pop-up must hit the pop-up itself;
  * clipped: an overflow ancestor or the window must not cut it.
Pages: the main window (every settings page, the chat window), Quick Access, the Proxy panel (every tab), and — read
only, their code is not ours this round — MemuFiles and MemuNote (their findings are printed, R41_STRICT_APPS=1 fails).
Screenshots of findings: R41_SHOTS=<dir>.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    sync_playwright = None

CHROMIUM = next((c for c in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", shutil.which("chromium") or "")
                 if c and Path(c).exists()), "")
SHOTS = os.environ.get("R41_SHOTS", "")
PY = sys.executable

# -------------------------------------------------------------------------------- the crawler (runs in the page)
CRAWL_JS = r"""
(() => {
  if (window.__r41) return;
  const POP = '[role=menu],[role=listbox],[role=dialog]:not(.panel):not(#panel),.context-menu,.pretty-select-menu,.mpick-pop,.mmp-pop,.menu,.cmp-picker,[data-memu-layer],[class*="-menu"],[class*="-pop"],[class*="popover"],[class*="dropdown"]';
  const TRIG = '.pretty-select-button,.mpick-btn,.mmp-trigger,[aria-haspopup=menu],[aria-haspopup=listbox],[aria-haspopup=true],[data-module-more],button[aria-label="Ещё"],button[aria-label="More"],button[title="Ещё"],button[title="More"],button[class*="more"]:not([class*="more-"]),button[class*="menu-btn"],button[class*="menu-toggle"],.ql-more,[data-a="export"],[data-a="ai:menu"],[data-act="pick"]';
  const shown = (e) => {
    if (!e.isConnected) return false;
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false;
    // a panel that opens IN the flow (Quick Access: the «Actions» menu replaces the answer) counts when the page asks for it
    if (cs.position !== 'fixed' && cs.position !== 'absolute' && !(window.__r41inflow && e.matches(window.__r41inflow))) return false;
    const r = e.getBoundingClientRect();
    return r.width > 24 && r.height > 16;
  };
  const visiblePops = () => [...document.querySelectorAll(POP)].filter(shown);
  const name = (e) => !e ? 'null' : (e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') + (typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''));
  const label = (b) => (b.getAttribute('aria-label') || b.title || b.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
  function triggers(rootSel, skipSel) {
    const root = document.querySelector(rootSel) || document.body;
    return [...root.querySelectorAll(TRIG)].filter((b) => {
      if (skipSel && b.closest(skipSel)) return false;
      if (b.disabled || b.closest('[hidden], .mpick-pop, .pretty-select-menu, .context-menu')) return false;
      const r = b.getBoundingClientRect();
      return r.width > 4 && r.height > 4 && b.getClientRects().length;
    });
  }
  function closeAll() {
    try { window.MemuLayers && MemuLayers.closeAll('api'); } catch (e) {}
    try { typeof hideContextMenu === 'function' && hideContextMenu(); } catch (e) {}
    try { typeof closePrettySelects === 'function' && closePrettySelects(); } catch (e) {}
    try { typeof closeFloatingMenus === 'function' && closeFloatingMenus(); } catch (e) {}
    document.querySelectorAll('.mpick.open').forEach((w) => w.classList.remove('open'));
  }
  function check(pop) {
    const r = pop.getBoundingClientRect();
    const out = { pop: name(pop), rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], problems: [] };
    const vw = innerWidth, vh = innerHeight;
    if (r.left < -1 || r.top < -1 || r.right > vw + 1 || r.bottom > vh + 1) out.problems.push('off-screen');
    // overflow ancestors (an absolute child of an overflow:hidden/auto box is cut by it)
    let vis = { l: Math.max(0, r.left), t: Math.max(0, r.top), r: Math.min(vw, r.right), b: Math.min(vh, r.bottom) };
    if (getComputedStyle(pop).position !== 'fixed') {
      for (let a = pop.parentElement; a && a !== document.body; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible' || cs.contain.includes('paint')) {
          const ar = a.getBoundingClientRect();
          vis = { l: Math.max(vis.l, ar.left), t: Math.max(vis.t, ar.top), r: Math.min(vis.r, ar.right), b: Math.min(vis.b, ar.bottom) };
        }
      }
    }
    const area = Math.max(0, vis.r - vis.l) * Math.max(0, vis.b - vis.t);
    if (area < r.width * r.height * 0.97) out.problems.push('clipped');
    const hits = [];
    const xs = [vis.l + 6, (vis.l + vis.r) / 2, vis.r - 6], ys = [vis.t + 6, (vis.t + vis.b) / 2, vis.b - 6];
    for (const y of ys) for (const x of xs) {
      if (vis.r - vis.l < 14 || vis.b - vis.t < 14) continue;
      const e = document.elementFromPoint(x, y);
      if (e && !pop.contains(e)) hits.push(name(e));
    }
    if (hits.length) out.problems.push('covered by ' + [...new Set(hits)].join(', '));
    return out;
  }
  window.__r41 = {
    count: (rootSel, skipSel) => triggers(rootSel, skipSel).length,
    // step 1: scroll the trigger (bottom / top of its scroll box), remember what was open, click
    open(rootSel, skipSel, i, block) {
      closeAll();
      const b = triggers(rootSel, skipSel)[i];
      if (!b) return null;
      b.scrollIntoView({ block, inline: 'nearest' });
      window.__r41before = new Set(visiblePops());
      window.__r41trig = b;
      b.click();
      return label(b) || name(b);
    },
    // step 2: the new pop-up(s) — the outermost of what became visible — and their checks
    result() {
      const b = window.__r41trig;
      const fresh = visiblePops().filter((p) => !window.__r41before.has(p) && !p.contains(b));
      const top = fresh.filter((p) => !fresh.some((q) => q !== p && q.contains(p)));
      const res = top.map(check);
      return { trigger: b ? (label(b) || name(b)) : '', found: top.length, res };
    },
    closeAll,
  };
})();
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 150) -> None:
    import httpx
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=3, trust_env=False).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


@unittest.skipUnless(sync_playwright and CHROMIUM, "needs playwright and Chromium")
class LayersCrawlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.procs = []
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), NO_PROXY="127.0.0.1,localhost", HOME=str(root),
                   MEMUFILES_TRASH_DIR=str(root / "trash"))
        cls.proxy_port, cls.pg_port = _free_port(), _free_port()
        env["MEMUAI_PROXY_URL"] = f"http://127.0.0.1:{cls.proxy_port}"
        subprocess.run([PY, "-c", "import sys; sys.path.insert(0, %r); from app import pgdata; "
                        "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'})" % str(ROOT)], env=env, check=True)
        for code in (
            "import sys, uvicorn; sys.path.insert(0, %r); from app.main import create_app; "
            "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')" % (str(ROOT), cls.proxy_port),
            "import sys, uvicorn; sys.path.insert(0, %r); from app.playground_api import create_app; "
            "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')" % (str(ROOT), cls.pg_port),
        ):
            cls.procs.append(subprocess.Popen([PY, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        # MemuFiles and MemuNote: their own small HTTP servers; a line «PORT TOKEN» on stdout
        cls.apps = {}
        for key, mod, cls_name in (("files", "app.memufiles.server", "FilesApp"), ("note", "app.memunote.server", "NoteApp")):
            code = ("import sys, time; sys.path.insert(0, %r); import %s as s; a = s.%s(); h, _ = s.start(a); "
                    "print(a.port, a.token, flush=True); time.sleep(100000)") % (str(ROOT), mod, cls_name)
            proc = subprocess.Popen([PY, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            cls.procs.append(proc)
            cls.apps[key] = proc
        cls.pg = f"http://127.0.0.1:{cls.pg_port}"
        cls.proxy = f"http://127.0.0.1:{cls.proxy_port}"
        _wait_http(cls.pg + "/api/pg/modules")
        _wait_http(cls.proxy + "/health")
        import httpx
        http = httpx.Client(trust_env=False, timeout=30)
        for row in http.get(cls.pg + "/api/pg/modules").json().get("modules", []):
            http.post(cls.pg + "/api/pg/modules/configure", json={"id": row.get("id"), "enabled": True})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])
        cls.findings = {}
        cls.opened = {}

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
            cls.pw.stop()
        finally:
            for proc in cls.procs:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            cls.tmp.cleanup()
        print("\n[round41 layers] pop-ups opened: " + json.dumps(cls.opened, ensure_ascii=False))
        if cls.findings:
            print("\n[round41 layers] findings:\n" + json.dumps(cls.findings, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ helpers
    def page(self, url, viewport=(1280, 800)):
        ctx = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.add_init_script(CRAWL_JS)
        page.goto(url)
        return page

    @staticmethod
    def ready(page, timeout=60.0):
        """The main window booted (polled from Python: the page CSP has no 'unsafe-eval' for wait_for_function)."""
        end = time.time() + timeout
        while time.time() < end:
            if page.evaluate("() => typeof showSettings === 'function' && document.body.classList.contains('boot-ready')"):
                return
            time.sleep(0.2)
        raise TimeoutError("boot-ready")

    def crawl(self, page, area, root="body", skip="", reopen=None, blocks=("end", "start")):
        """Opens every trigger in `root` (minus `skip`) once per scroll position; returns the problems."""
        page.evaluate(CRAWL_JS)
        problems, opened = [], 0
        for block in blocks:
            n = page.evaluate("([r, s]) => __r41.count(r, s)", [root, skip])
            for i in range(n):
                if reopen and not reopen():
                    break
                label = page.evaluate("([r, s, i, b]) => __r41.open(r, s, i, b)", [root, skip, i, block])
                if label is None:
                    continue
                page.wait_for_timeout(160)
                res = page.evaluate("() => __r41.result()")
                for r in res["res"]:
                    opened += 1
                    if r["problems"]:
                        problems.append({"area": area, "trigger": res["trigger"], "scroll": block, **r})
                        if SHOTS:
                            Path(SHOTS).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(SHOTS) / f"{area}-{block}-{i}.png"))
                page.evaluate("() => __r41.closeAll()")
                if page.evaluate("() => !!(window.MemuLayers && MemuLayers.isOpen())"):
                    page.keyboard.press("Escape")
        return problems, opened

    def record(self, key, problems, opened=None):
        if opened is not None:
            self.opened[key] = opened
        if problems:
            self.findings[key] = problems

    # ------------------------------------------------------------------ the main window: settings
    def test_settings_every_page(self):
        page = self.page(self.pg + "/")
        self.ready(page)
        page.evaluate("() => showSettings()")
        page.wait_for_selector("#panel .sx-shell", timeout=20000)
        ids = page.evaluate("() => settingsPages().map((p) => p.id).filter((id) => id !== 'docs')")
        self.assertGreater(len(ids), 15)
        problems, opened = [], 0
        for pid in ids:
            def reopen(pid=pid):
                if not page.evaluate("() => !!document.querySelector('#panel .sx-shell') && document.getElementById('panel-back').classList.contains('visible')"):
                    page.evaluate("(id) => { currentSettingsTab = id; showSettings(); }", pid)
                    page.wait_for_timeout(500)
                if page.evaluate("() => document.querySelector('#panel .sx-shell')?.dataset.page") != pid:
                    page.evaluate("(id) => openSettingsPage(id)", pid)
                    page.wait_for_timeout(400)
                return True
            reopen()
            page.wait_for_timeout(500)
            found, n = self.crawl(page, "settings-" + pid, root=f"#pane-{pid}", reopen=reopen)
            problems += found
            opened += n
            # every sub-page of the page (Settings kit «›» rows): its own selects and menus
            subs = page.evaluate("(id) => [...document.querySelectorAll(`#pane-${id} .sx-sub[data-sx-sub]`)].map((n) => n.dataset.sxSub)", pid)
            for sub in subs:
                def reopen_sub(pid=pid, sub=sub):
                    reopen(pid)
                    if page.evaluate("(s) => { const n = document.querySelector(`.settings-tab-pane.active .sx-sub[data-sx-sub=\"${s}\"]`); return !!n && !n.hidden; }", sub):
                        return True
                    page.evaluate("(s) => MemuSettingsKit.openSub(s, { focus: false, animate: false })", sub)
                    page.wait_for_timeout(250)
                    return True
                reopen_sub()
                found, n = self.crawl(page, f"settings-{pid}-{sub}", root=f'#pane-{pid} .sx-sub[data-sx-sub="{sub}"]', reopen=reopen_sub)
                problems += found
                opened += n
                page.evaluate("() => MemuSettingsKit.closeSub && MemuSettingsKit.closeSub()")
        self.record("settings", problems, opened)
        self.assertGreater(opened, 40, "the crawler found almost no pop-ups — the selectors are stale?")
        self.assertEqual(problems, [], json.dumps(problems, ensure_ascii=False, indent=1))

    def test_modules_more_after_a_dialog(self):
        """The reported case: ⋯ → «Change icon…» → Esc → ⋯ again — the menu must be above the settings."""
        page = self.page(self.pg + "/")
        self.ready(page)
        page.evaluate("() => { currentSettingsTab = 'modules'; showSettings(); }")
        page.wait_for_selector("[data-module-more]", timeout=20000)
        page.wait_for_timeout(1500)   # the catalog re-renders once the module list arrives
        page.locator("[data-module-more]").nth(1).click()
        page.locator("#context-menu button", has_text="Сменить иконку").click()
        page.wait_for_selector(".mico-dialog", timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        self.assertNotIn("over-modules", page.evaluate("() => document.getElementById('panel-back').className"))
        page.evaluate(CRAWL_JS)
        for i in (0, 1):
            page.evaluate("(i) => __r41.open('#pane-modules', '', -1, 'center')", i)   # closes any layer
            page.locator("[data-module-more]").nth(i).click()
            page.wait_for_timeout(200)
            res = page.evaluate("""() => { const m = document.getElementById('context-menu'); const r = m.getBoundingClientRect();
                const pts = [[r.left + 8, r.top + 8], [r.left + r.width / 2, r.top + r.height / 2], [r.right - 8, r.bottom - 8]];
                return pts.every(([x, y]) => m.contains(document.elementFromPoint(x, y))); }""")
            self.assertTrue(res, f"⋯ #{i}: the menu is covered")
            # opens next to its button, never over it
            over = page.evaluate("""(i) => { const b = document.querySelectorAll('[data-module-more]')[i].getBoundingClientRect();
                const m = document.getElementById('context-menu').getBoundingClientRect();
                return !(m.bottom <= b.top + 1 || m.top >= b.bottom - 1) ? [b.top, b.bottom, m.top, m.bottom, innerHeight] : null; }""", i)
            self.assertIsNone(over, f"⋯ #{i}: the menu covers its own button")
            page.evaluate("() => hideContextMenu()")

    def test_web_search_service_list(self):
        """The reported case: Web search → «Service» near the bottom opens upwards — above the fields, not under them."""
        page = self.page(self.pg + "/")
        self.ready(page)
        page.evaluate("() => { currentSettingsTab = 'search'; showSettings(); }")
        page.wait_for_selector(".mpick:has(#st-search-provider)", state="attached", timeout=20000)
        page.wait_for_timeout(500)
        page.evaluate("() => { const w = document.getElementById('st-search-provider').closest('.mpick, .pretty-select'); w.scrollIntoView({ block: 'end' }); w.querySelector('button').click(); }")
        page.wait_for_timeout(300)
        info = page.evaluate("""() => { const m = document.querySelector('body > .mpick-pop'); if (!m) return null;
            const r = m.getBoundingClientRect(), b = document.getElementById('st-search-provider').closest('.mpick').getBoundingClientRect();
            const pts = []; for (const fx of [.1, .5, .9]) for (const fy of [.1, .5, .9]) pts.push([r.left + r.width * fx, r.top + r.height * fy]);
            return { up: r.bottom <= b.top + 1, covered: pts.filter(([x, y]) => !m.contains(document.elementFromPoint(x, y))).length,
                     z: getComputedStyle(m).zIndex }; }""")
        self.assertIsNotNone(info, "the list is not in the top layer")
        self.assertTrue(info["up"])
        self.assertEqual(info["covered"], 0)
        # picking still works from the top layer, and the list goes back home
        page.locator("body > .mpick-pop .mpick-item").nth(1).click()
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("() => document.querySelectorAll('body > .mpick-pop').length"), 0)

    # ------------------------------------------------------------------ the main window: chat, sidebar, composer
    def test_main_window(self):
        page = self.page(self.pg + "/")
        self.ready(page)
        page.wait_for_timeout(800)
        problems, opened = self.crawl(page, "main", root="body", skip="#panel-back, .module-layer", blocks=("nearest",))
        self.record("main", problems, opened)
        self.assertEqual(problems, [], json.dumps(problems, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ Quick Access
    def test_quick_access(self):
        page = self.page(self.pg + "/quick/", (760, 560))
        page.wait_for_timeout(3000)
        page.evaluate("() => { window.__r41inflow = '.qa-menu, .qa-picker, .qa-chats'; }")
        problems, opened = self.crawl(page, "quick", blocks=("nearest",))
        self.record("quick", problems, opened)
        self.assertEqual(problems, [], json.dumps(problems, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ MemuAI Proxy panel (app/static)
    def test_proxy_panel(self):
        page = self.page(self.proxy + "/", (1440, 900))
        page.wait_for_timeout(2500)
        tabs = page.evaluate("() => [...document.querySelectorAll('[data-tab]')].filter((b) => b.getClientRects().length).map((b) => b.dataset.tab)")
        problems, opened = [], 0
        for tab in dict.fromkeys(tabs):
            page.evaluate("(t) => document.querySelector(`[data-tab=\"${t}\"]`).click()", tab)
            page.wait_for_timeout(700)
            found, n = self.crawl(page, "proxy-" + tab, blocks=("end",))
            problems += found
            opened += n
        self.record("proxy", problems, opened)
        self.assertEqual(problems, [], json.dumps(problems, ensure_ascii=False, indent=1))

    # ------------------------------------------------------------------ MemuFiles / MemuNote: read only
    def _app_url(self, key):
        line = self.apps[key].stdout.readline().split()
        return f"http://127.0.0.1:{line[0]}/index.html#t={line[1]}"

    def test_memufiles_and_memunote_read_only(self):
        strict = os.environ.get("R41_STRICT_APPS") == "1"
        for key in ("files", "note"):
            page = self.page(self._app_url(key), (1280, 800))
            page.wait_for_timeout(3500)
            problems, opened = self.crawl(page, key, blocks=("nearest",))
            self.record(key, problems, opened)
            if strict:
                self.assertEqual(problems, [], json.dumps(problems, ensure_ascii=False, indent=1))


class StaticContracts(unittest.TestCase):
    def test_top_layer_wiring(self):
        layers = (ROOT / "app/pgstatic/memu-layers.js").read_text(encoding="utf-8")
        self.assertIn("function float(", layers)
        self.assertIn("function unfloat(", layers)
        app = (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8")
        self.assertIn("MemuLayers.float(pop, button", app)                     # the searchable list
        self.assertIn('host.classList.remove("over-modules")', app)            # a dialog's class never outlives it
        css = (ROOT / "app/pgstatic/style.css").read_text(encoding="utf-8")
        self.assertIn("body > .mpick-pop", css)
        self.assertIn("z-index: 100100", css)


if __name__ == "__main__":
    unittest.main()
