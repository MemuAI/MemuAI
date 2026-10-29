"""Round 27: the language scan of every UI — no Cyrillic in English, no stray English in Russian.

Starts the REAL servers on free local ports with a temporary data dir (MemuAI Proxy, the MemuAI
playground, the Telegram Mini App and an offline OpenAI-compatible mock for models), opens every
section, tab, settings page and sub-page, modal and menu in headless Chromium and collects the text
a person can see or hear: rendered text nodes (with the options of rendered selects; hidden markup is
skipped — script fills it in when it opens, so the scan opens the features themselves), ``title``,
``placeholder``, ``aria-label``, ``alt``. The installer is scanned through its
mock page (``installer/ui/index.html?lang=…``).

* ``lang=en``: any Cyrillic letter is a finding (minus ``ALLOW_CYRILLIC``: the language picker's
  own «Русский», user content seeded by the scan itself is English).
* ``lang=ru``: phrases of two or more English words that are not product names or technical terms
  (``RU_ALLOWED_WORDS``) are reported as SUSPECTS for a human to review.

Usage: python tests/ui_lang_scan.py [--only memuai,proxy,tgapp,installer] [--lang en,ru] [--json OUT] [--shots DIR]
The regression test is ``tests/round27_ui_lang_test.py``.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHROME_CANDIDATES = [os.environ.get("MEMU_TEST_CHROMIUM", ""), os.environ.get("MEMUAI_CHROMIUM", ""),
                     "/workspace/work/chromium/chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")

ALLOW_CYRILLIC = {"Русский", "Русский (RU)", "RU · Русский"}

COLLECT_JS = r"""(sel) => {
  const roots = sel ? [...document.querySelectorAll(sel)] : [document.documentElement];
  const out = [], seen = new Set();
  const add = (t, where) => { t = String(t || '').replace(/\s+/g, ' ').trim(); if (!t) return;
    const k = t + '\u0001' + where; if (seen.has(k)) return; seen.add(k); out.push([t, where]); };
  const path = (el) => { const s = []; while (el && el.nodeType === 1 && s.length < 4) {
    s.unshift(el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.classList && el.classList[0] ? '.' + el.classList[0] : ''));
    if (el.id) break; el = el.parentElement; } return s.join('>'); };
  const skip = 'script,style,noscript,template,code,pre,textarea,[data-lang-scan-skip],.msg-content,.md,.cm-content';
  // Only what is rendered: hidden markup is often filled in by script when it opens. The options of a
  // rendered <select> count (the list a click opens).
  const shown = (el) => { if (el.closest('option')) el = el.closest('select') || el;
    return !el.checkVisibility || el.checkVisibility({ checkVisibilityCSS: true }); };
  for (const root of roots) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) { const n = walker.currentNode, p = n.parentElement;
      if (!p || p.closest(skip) || !shown(p)) continue; add(n.nodeValue, path(p)); }
    for (const el of root.querySelectorAll('[title],[placeholder],[aria-label],[alt],[data-tip],[data-tooltip]')) {
      if (el.closest('[data-lang-scan-skip]') || !shown(el)) continue;
      for (const a of ['title', 'placeholder', 'aria-label', 'alt', 'data-tip', 'data-tooltip'])
        if (el.hasAttribute(a)) add(el.getAttribute(a), path(el) + '@' + a); }
  }
  if (!sel) add(document.title, 'document.title');
  return out;
}"""

RU_ALLOWED_WORDS = set("""
memuai memu vision proxy memucode memes mini app apps telegram github google drive calendar gmail api apis
key keys token tokens url urls http https localhost json yaml csv pdf png jpg jpeg webp gif svg mp3 mp4
wav ogg zip md html css js py txt gguf onnx cuda cpu gpu ram vram os windows linux macos mac edge
chrome chromium webview webview2 alt ctrl shift space enter esc tab win cmd del backspace f1 f2 f3 f4
f5 f6 f7 f8 f9 f10 f11 f12 pgup pgdn up down home end ok id ip ssh git cli sdk mcp oauth smtp imap
openai anthropic claude gpt chatgpt gemini qwen deepseek yandex yandexgpt grok xai mistral llama ollama
lm studio gemma phi openrouter genspark kiro antigravity warp cursor copilot perplexity groq together cohere
elevenlabs whisper faster piper silero vosk tts stt live work do for you dfy reflect canvas codex
opus sonnet haiku flash pro max mini nano turbo free plus vl coder thinking reasoning o1 o3 o4 v1 v2
v3 beta alpha preview latest auto default cloudflare cloudflared ngrok tunnel webhook bot botfather
cdn dns tls ssl proxy socks socks5 http2 sse ws wss websocket utc gmt rgb hex kb mb gb tb ms fps hz
khz bit bits byte bytes x64 arm64 aarch64 amd64 exe msi dll bat ps1 powershell cmd.exe npm node
python pip uv venv pytorch transformers huggingface hf hub repo pr prs issue commit branch main
markdown latex mermaid katex regex unicode utf utf-8 ascii emoji wikipedia duckduckgo bing brave
searxng tavily serper exa jina firecrawl kagi you.com usd eur rub byn kzt uah cny jpy gbp btc eth
light dark system classic glass aurora focus grid soft brutal terminal sans serif mono inter
jetbrains roboto segoe ui ux tg pin otp 2fa totp e-mail email nvidia amd intel apple android ios
iphone ipad web vk discord slack notion obsidian trello jira figma youtube twitter x reddit
spotify steam twitch microsoft office word excel powerpoint outlook onedrive dropbox icloud
""".split())

RU_ALLOWED_PHRASES = ("Hugging Face", "function calling", "Programmable Search", "Anthropic Messages", "OpenAI Responses",
                      "Artificial Analysis", "MemuAI Index", "AWS Kiro IDE", "Moonshot AI", "Zhipu AI", "Seed · ByteDance",
                      "EQ-Bench", "LiveBench", "SimpleBench", "Computer Use",
                      "Personal Access Token", "Client secret", "pull request", "Catppuccin Mocha",
                      "Live2D Cubism Core", "Live2D Inc", "Expandable Applications", "AI Fluency", "Open WebUI", "Retry-After")

RU_SKIP_WHERE = ("code-block", "det-visual-pick", "det-claimed", "voice-model-title", "companion-model", "mcs-vs-head",
                 "mcp-command", "github-guide", "google-guide", "#lm-q")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’.+-]*")

def english_suspects(text: str) -> list[str]:
    """Runs of 2+ English words (not product names/terms) inside a Russian UI string."""

    for phrase in RU_ALLOWED_PHRASES:
        text = text.replace(phrase, " ")
    text = re.sub(r"\S*_\S*|\S+://\S+|\S+@\S+\.\S+|[A-Za-z]:\\[^,;«»\n]*|%\w+%\S*|\S*[\\/]\S+|\S+\.(?:exe|json|log|md|py|txt|gguf|zip|png)\b", " ", text)
    found, run = [], []
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'’+-]*|[А-Яа-яЁё]+|[.,;:!?()«»\"—–/]|\d+", text)
    for token in tokens + ["."]:
        if WORD.fullmatch(token):
            run.append(token)
            continue
        if token.isdigit() and run:
            continue
        unknown = [w for w in run if w.lower().strip(".") not in RU_ALLOWED_WORDS]
        if len(run) >= 2 and len(unknown) >= 2:
            found.append(" ".join(run))
        run = []
    return found

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

def chromium() -> str | None:
    return next((c for c in CHROME_CANDIDATES if c and os.path.exists(c)), None)

class Servers:
    """MemuAI Proxy + playground + Mini App + an offline model mock, in this process."""

    TG_TOKEN = "123456:SCAN-token_for-mini-app"
    TG_USER = 4242

    def __init__(self) -> None:
        self.data = os.environ.get("MEMUAI_DATA_DIR") or tempfile.mkdtemp(prefix="memu-langscan-")
        os.environ["MEMUAI_DATA_DIR"] = self.data
        for key in ("NO_PROXY", "no_proxy"):
            os.environ[key] = "127.0.0.1,localhost"
        self.servers = []
        self.threads = []

        from unittest.mock import patch
        home = Path(self.data) / "home"
        home.mkdir(parents=True, exist_ok=True)
        self._home = patch("pathlib.Path.home", return_value=home)
        self._home.start()
        # The language scan uses synthetic Telegram credentials only. Keep its
        # UI available without downloading binaries or opening external tunnels.
        from app import tg_webapp
        self._tunnel = patch.object(tg_webapp.Manager, "_ensure_tunnel")
        self._tunnel.start()
        from app import fsindex
        self._index_env = patch.dict(os.environ, {"MEMUAI_FSINDEX_NOSTART": "1"})
        self._index_env.start()
        self._drives = patch.object(fsindex, "available_drives", return_value=[{
            "path": str(home), "type": "fixed", "label": "Test volume", "fs": ""}])
        self._drives.start()
        self._roots = patch.object(fsindex, "default_roots", return_value=[str(home)])
        self._roots.start()

    def _run(self, app, port):
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        self.threads.append(thread)
        self.servers.append(server)
        for _ in range(400):
            if server.started:
                return
            time.sleep(0.05)
        raise RuntimeError(f"server on {port} did not start")

    def start(self) -> "Servers":
        import httpx
        from tests import mock_custom
        mock, proxy, pg, tg = (free_port() for _ in range(4))
        self.proxy = f"http://127.0.0.1:{proxy}"
        self.pg = f"http://127.0.0.1:{pg}"
        self.tg = f"http://127.0.0.1:{tg}"
        os.environ["MEMUAI_PROXY_URL"] = self.proxy
        os.environ["MEMUAI_TOKEN"] = ""
        try:
            import webview  # noqa: F401
        except ImportError:
            import types
            sys.modules["webview"] = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10, SAVE_DIALOG=30)
        from app.main import create_app
        from app.playground_api import create_app as create_playground
        from app.tgapp_api import create_tgapp
        self._run(mock_custom.app, mock)
        self._run(create_app(), proxy)
        self._run(create_playground(), pg)
        self._run(create_tgapp(), tg)
        with httpx.Client(timeout=30, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get(self.proxy + "/api/admin/settings").status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            client.post(self.proxy + "/api/admin/accounts", json={
                "provider": "custom", "label": "scan",
                "fields": {"name": "Demo", "base_url": f"http://127.0.0.1:{mock}/v1", "api_key": "sk-scan",
                           "models": "gpt-test, claude-test"}})
        return self

    def stop(self) -> None:
        for server in self.servers:
            server.should_exit = True
        for thread in self.threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in self.threads):
            for server in self.servers:
                server.force_exit = True
            for thread in self.threads:
                thread.join(timeout=5)
        if any(thread.is_alive() for thread in self.threads):
            # Retain the isolation patches if a daemon is still shutting down.
            alive = [server.config.port for server, thread in zip(self.servers, self.threads) if thread.is_alive()]
            raise RuntimeError(f"Isolated language-scan server did not stop: {alive}")
        if getattr(self, "_home", None):
            self._home.stop()
            self._home = None
        if getattr(self, "_tunnel", None):
            self._tunnel.stop()
            self._tunnel = None
        for name in ("_index_env", "_drives", "_roots"):
            patcher = getattr(self, name, None)
            if patcher:
                patcher.stop()
                setattr(self, name, None)

def _isolate_network(context) -> None:
    def route(request_route):
        parsed = urllib.parse.urlsplit(request_route.request.url)
        if parsed.scheme not in ("http", "https") or parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            request_route.continue_()
        else:
            request_route.abort()
    context.route("**/*", route)


class Scan:
    def __init__(self, page, product: str, lang: str):
        self.page, self.product, self.lang = page, product, lang
        self.items: list[tuple[str, str, str]] = []
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(f"{product}/{lang}: {e}"))

    def grab(self, state: str, selector: str | None = None) -> None:
        try:
            rows = self.page.evaluate(COLLECT_JS, selector)
        except Exception as exc:
            self.settle()
            try:
                rows = self.page.evaluate(COLLECT_JS, selector)
            except Exception:
                self.errors.append(f"{self.product}/{self.lang} {state}: collect failed: {exc}")
                return
        self.items.extend((state, where, text) for text, where in rows)

    def wait(self, ms: int = 400) -> None:
        self.page.wait_for_timeout(ms)

    READY_JS = ""

    def settle(self) -> None:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            if self.READY_JS:
                self.page.wait_for_function(self.READY_JS, timeout=90000)
        except Exception:
            pass
        self.wait(600)

    def js(self, code: str, state: str | None = None, ms: int = 500, selector: str | None = None):
        for attempt in (1, 2):
            try:
                self.page.evaluate(code)
                break
            except Exception as exc:
                text = str(exc)
                if attempt == 1 and ("context was destroyed" in text or "navigation" in text.lower()):
                    self.settle()
                    continue
                self.errors.append(f"{self.product}/{self.lang} {state or code[:40]}: {text[:160]}")
                return
        self.wait(ms)
        if state:
            self.grab(state, selector)

    def findings(self) -> list[dict]:
        out, seen = [], set()
        for state, where, text in self.items:
            if self.lang == "en":
                if not CYRILLIC.search(text) or text in ALLOW_CYRILLIC:
                    continue
                hits = [text]
            else:
                if any(mark in where for mark in RU_SKIP_WHERE):
                    continue
                hits = english_suspects(text)
            for hit in hits:
                key = (where.split("@")[-1] if "@" in where else "", text)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"product": self.product, "lang": self.lang, "state": state, "where": where,
                            "text": text[:300], "hit": hit[:120]})
        return out

SEED_CHATS_EN = [{"id": "c_scan1", "title": "Database migration", "created": 1, "updated": 2, "mode": "chat",
                  "messages": [{"role": "user", "content": "How do I move PostgreSQL?", "ts": 1},
                               {"role": "assistant", "content": "Step by step.", "model": "demo-gpt-test", "ts": 2}]}]

MEMUAI_SETTINGS_SUBS_JS = r"""() => [...document.querySelectorAll('#panel .sx-shell .settings-tab-pane.active [data-sx-open]')]
  .map((b) => b.dataset.sxOpen)"""

def scan_memuai(browser, servers: Servers, lang: str, *, width: int = 1360, height: int = 860, shots: Path | None = None) -> Scan:
    import httpx
    with httpx.Client(timeout=20, trust_env=False) as client:
        client.post(servers.pg + "/api/pg/preferences", json={"language": lang, "profile_onboarded": False,
                                                              "updates_enabled": False})
    ctx = browser.new_context(viewport={"width": width, "height": height}, bypass_csp=True)
    _isolate_network(ctx)
    ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (_) {{}}")
    page = ctx.new_page()
    scan = Scan(page, "memuai", lang)
    scan.READY_JS = "document.body && document.body.classList.contains('boot-ready')"
    page.goto(servers.pg + "/", wait_until="domcontentloaded")
    page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
    scan.wait(1500)
    scan.grab("onboarding")
    with httpx.Client(timeout=20, trust_env=False) as client:
        client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex"})
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
    scan.wait(1500)
    scan.grab("home")
    shot = (lambda name: page.screenshot(path=str(shots / f"memuai-{lang}-{name}.png"))) if shots else (lambda name: None)
    shot("home")

    for nav in ("nav-chat", "nav-code", "nav-work", "nav-sched", "nav-skills"):
        scan.js(f"document.getElementById('{nav}')?.click()", nav, 900)
        shot(nav)
        scan.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))", None, 200)
        scan.js("typeof closePanel === 'function' && closePanel()", None, 200)
    scan.js("document.getElementById('nav-chat')?.click()", None, 500)

    for state, code in (
        ("model-menu", "document.getElementById('model-dd-btn')?.click()"),
        ("memory", "showMemory()"),
        ("schedule", "showSchedule()"),
        ("skills", "showSkillsModal()"),
        ("shortcuts", "showShortcutsModal()"),
        ("export", "showExportModal()"),
        ("profile", "showProfileSettings()"),
        ("meme-create", "openMemeCreateChoice()"),
        ("meme-wizard", "openMemeWizard()"),
        ("about-import", "openAboutImportPanel()"),
        ("mcp", "showMcpPanel()"),
        ("live-personality", "showLivePersonalitySettings()"),
        ("work-access", "openWorkAccessChooser()"),
        ("temp-chat", "document.getElementById('btn-temp')?.click()"),
        ("temp-chat-off", "document.getElementById('btn-temp')?.click()"),
        ("image-viewer", "openImagePreview('/icon.png', 'icon.png')"),
        ("pin", "promptPinUnlock({name: 'Alex'}, () => {}); setTimeout(() => document.getElementById('pin-cancel')?.click(), 600)"),
        ("live", "openLiveCall(); setTimeout(() => document.getElementById('live-close')?.click(), 600)"),
        ("code-palette", "document.getElementById('nav-code')?.click(); setTimeout(() => document.getElementById('code-palette-btn')?.click(), 300)"),

        ("search", "document.getElementById('nav-chat')?.click(); window.MemuSearch && MemuSearch.open('memu')"),
        ("search-empty", "window.MemuSearch && MemuSearch.open('')"),
        ("schedsend-menu", "window.MemuSchedSend && MemuSchedSend.open()"),
        ("schedsend-all", "window.MemuSchedSend && MemuSchedSend.showAll()"),
        ("memory-inbox", "window.MemuMemory && MemuMemory.open({tab: 'inbox'})"),
        ("memory-rules", "window.MemuMemory && MemuMemory.open({tab: 'rules'})"),
        ("memory-settings", "window.MemuMemory && MemuMemory.open({tab: 'settings'})"),
        ("memory-trash", "window.MemuMemory && MemuMemory.open({tab: 'trash'})"),
        ("table-editor", "window.MemuTables && MemuTables.openEditor({data: {columns: ['A', 'B'], rows: [['1', '2']]}})"),
        ("table-studio", "window.MemuTables && MemuTables.openStudio('| A | B |\\n|---|---|\\n| 1 | 2 |')"),
    ):
        scan.js(code, state, 700)
        scan.js("typeof closePanel === 'function' && closePanel()", None, 150)
        page.keyboard.press("Escape")
        scan.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))", None, 150)

    scan.js("showSettings()", None, 1200)
    try:
        pages = page.evaluate("settingsPages().map((p) => p.id)")
    except Exception:
        scan.settle()
        scan.js("showSettings()", None, 1200)
        pages = page.evaluate("settingsPages().map((p) => p.id)")
    for pid in pages:
        scan.js(f"openSettingsPage('{pid}'); document.querySelectorAll('#panel details').forEach((d) => d.open = true)",
                f"settings:{pid}", 1100 if pid in ("modules", "panels", "docs", "reflect") else 600, selector="#panel")
        shot(f"settings-{pid}")
        for sub in page.evaluate(MEMUAI_SETTINGS_SUBS_JS):
            scan.js(f"MemuSettingsKit.openSub({json.dumps(sub)}); document.querySelectorAll('#panel details').forEach((d) => d.open = true)",
                    f"settings:{pid}/{sub}", 350, selector="#panel")
            scan.js("MemuSettingsKit.closeSub()", None, 100)
    scan.js("typeof closePanel === 'function' && closePanel()", None, 300)
    ctx.close()
    return scan

PROXY_TABS = ("home", "accounts", "local", "public", "chat", "detector", "settings", "logs")

def scan_proxy(browser, servers: Servers, lang: str, *, theme: str = "dark", shots: Path | None = None) -> Scan:
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True)
    _isolate_network(ctx)
    ctx.add_init_script(f"try {{ localStorage.setItem('oh_lang', '{lang}'); localStorage.setItem('oh_theme', '{theme}'); }} catch (_) {{}}")
    page = ctx.new_page()
    scan = Scan(page, "proxy", lang)
    page.goto(servers.proxy + "/", wait_until="networkidle")
    scan.wait(800)
    scan.grab("start")
    for tab in PROXY_TABS:
        scan.js(f"document.querySelector('[data-tab=\"{tab}\"]')?.click()", f"tab:{tab}", 1200 if tab in ("settings", "accounts") else 700)
        if shots:
            page.screenshot(path=str(shots / f"proxy-{lang}-{theme}-{tab}.png"))
        if tab == "settings":
            for sec in page.evaluate("[...document.querySelectorAll('[data-sg-nav]')].map((b) => b.dataset.sgNav)"):
                scan.js(f"document.querySelector('[data-sg-nav=\"{sec}\"]')?.click()", f"settings:{sec}", 600)
                if shots:
                    page.screenshot(path=str(shots / f"proxy-{lang}-{theme}-settings-{sec}.png"), full_page=False)

    scan.js("document.querySelector('[data-tab=\"accounts\"]')?.click()", None, 600)
    scan.js("document.querySelector('#add-account-btn, [data-action=\"add-account\"], #acc-add')?.click()", "add-account", 800)
    scan.js("document.getElementById('companion-open')?.click()", "companion", 900)
    ctx.close()
    return scan

def scan_tgapp(browser, servers: Servers, lang: str) -> Scan:
    from app import pgdata, tg_webapp
    from app import panels
    pgdata.save_preferences({"telegram_bot_token": servers.TG_TOKEN, "telegram_chat_id": str(servers.TG_USER),
                             "language": lang})
    panels.update_settings({"lang": lang})
    tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
    init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-scan",
                                     "user": {"id": servers.TG_USER, "first_name": "Yono", "language_code": lang}},
                                    servers.TG_TOKEN)
    hash_part = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "7.10", "tgWebAppPlatform": "android",
                                        "tgWebAppThemeParams": json.dumps({"bg_color": "#17212b"})})
    ctx = browser.new_context(viewport={"width": 390, "height": 760}, bypass_csp=True)
    _isolate_network(ctx)
    page = ctx.new_page()
    scan = Scan(page, "tgapp", lang)
    page.goto(f"{servers.tg}/tgapp/#{hash_part}")
    try:
        page.wait_for_selector("#app:not([hidden]) .tab", timeout=20000)
    except Exception as exc:
        scan.errors.append(f"tgapp/{lang}: did not open: {exc}")
    scan.wait(800)
    for tab in page.evaluate("[...document.querySelectorAll('.tab[data-tab]')].map((b) => b.dataset.tab)"):
        scan.js(f"document.querySelector('.tab[data-tab=\"{tab}\"]').click()", f"tab:{tab}", 800)
    ctx.close()
    return scan

INSTALLER_CASES = ("screen=welcome", "screen=edition", "screen=location", "screen=options", "screen=review",
                   "screen=express", "screen=progress&pct=47", "screen=done&speed=0.2", "screen=notes&speed=0.2",
                   "screen=error", "screen=apps", "kind=upgrade&screen=welcome", "kind=reinstall&screen=welcome",
                   "kind=downgrade&screen=welcome", "product=proxy&screen=welcome", "product=proxy&screen=options",
                   "mode=uninstall", "mode=uninstall&screen=keepoff", "mode=uninstall&screen=un-done&speed=0.2")

def scan_installer(browser, lang: str) -> Scan:
    ctx = browser.new_context(viewport={"width": 960, "height": 620}, bypass_csp=True)
    _isolate_network(ctx)
    page = ctx.new_page()
    scan = Scan(page, "installer", lang)
    ui = (ROOT / "installer" / "ui" / "index.html").as_uri()
    for case in INSTALLER_CASES:
        page.goto(f"{ui}?mock=1&shot=1&lang={lang}&{case}")
        scan.wait(700)
        scan.grab(case)
    ctx.close()
    return scan

def run(only=("memuai", "proxy", "tgapp", "installer"), langs=("en", "ru"), shots: Path | None = None):
    from playwright.sync_api import sync_playwright
    exe = chromium()
    servers = Servers().start() if set(only) & {"memuai", "proxy", "tgapp"} else None
    findings, errors = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
            for lang in langs:
                scans = []
                if "installer" in only:
                    scans.append(scan_installer(browser, lang))
                if "proxy" in only:
                    scans.append(scan_proxy(browser, servers, lang, shots=shots))
                if "tgapp" in only:
                    scans.append(scan_tgapp(browser, servers, lang))
                if "memuai" in only:
                    scans.append(scan_memuai(browser, servers, lang, shots=shots))
                for scan in scans:
                    findings.extend(scan.findings())
                    errors.extend(scan.errors)
            browser.close()
    finally:
        if servers:
            servers.stop()
    return findings, errors

def main(argv: list[str]) -> int:
    only, langs, out, shots = ["memuai", "proxy", "tgapp", "installer"], ["en", "ru"], None, None
    args = iter(argv)
    for arg in args:
        if arg == "--only":
            only = next(args).split(",")
        elif arg == "--lang":
            langs = next(args).split(",")
        elif arg == "--json":
            out = Path(next(args))
        elif arg == "--shots":
            shots = Path(next(args))
            shots.mkdir(parents=True, exist_ok=True)
    findings, errors = run(tuple(only), tuple(langs), shots)
    for f in findings:
        print(f"[{f['product']}/{f['lang']}] {f['state']} | {f['where']} | {f['text']}")
    if errors:
        print("\nPAGE ERRORS:\n" + "\n".join(errors[:80]))
    print(f"\n{len(findings)} findings, {len(errors)} errors")
    if out:
        out.write_text(json.dumps({"findings": findings, "errors": errors}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if findings else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
