"""Round 27 (r27-hunt-ui): screenshots of the REAL MemuAI window (live playground + Proxy + model mock in a
temporary data dir) for every design x ru/en x light/dark: home, a chat with markdown / code / a table,
settings (general and appearance), and the same at a narrow ~720 px window.

Run: python tests/round27_ui_look_shots.py [--designs classic,glass] [--langs ru,en] [--narrow-only]
     -> /workspace/work/shots/r27/r27-hunt-ui/look/*.png (R27_LOOK_SHOTS overrides). Look at them.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = Path(os.environ.get("R27_LOOK_SHOTS", "/workspace/work/shots/r27/r27-hunt-ui/look"))

ANSWER = {"ru": """## План миграции

Переносим сервис **поэтапно**, без простоя:

1. Поднять реплику базы и включить логическую репликацию.
2. Переключить чтение, затем запись.

```python
async def migrate(db, replica):
    await replica.sync(db, mode="logical")
    for table in await db.tables():
        print(f"✓ {table.name}: {table.rows} строк — очень длинная строка кода, чтобы проверить горизонтальную прокрутку блока")
```

| Этап | Время | Риск |
|------|-------|------|
| Реплика | 2 ч | Низкий |
| Переключение | 10 мин | Средний |

> Совет: держите `rollback.sh` под рукой. Подробнее — [в документации](https://example.com).
""", "en": """## Migration plan

Move the service **step by step**, with no downtime:

1. Bring up a replica and turn on logical replication.
2. Switch reads, then writes.

```python
async def migrate(db, replica):
    await replica.sync(db, mode="logical")
    for table in await db.tables():
        print(f"✓ {table.name}: {table.rows} rows — a very long line of code to check the block's horizontal scrolling")
```

| Step | Time | Risk |
|------|------|------|
| Replica | 2 h | Low |
| Switch | 10 min | Medium |

> Tip: keep `rollback.sh` at hand. More in [the docs](https://example.com).
"""}
QUESTION = {"ru": "Как перенести PostgreSQL на новый кластер без простоя?",
            "en": "How do I move PostgreSQL to a new cluster without downtime?"}


def seed_chat(lang: str) -> None:
    from app import pgdata
    now = int(time.time() * 1000)
    pgdata.save_chats([{"id": "c_look1", "title": QUESTION[lang][:40], "created": now - 60000, "updated": now,
                        "mode": "chat", "messages": [
                            {"role": "user", "content": QUESTION[lang], "ts": now - 50000},
                            {"role": "assistant", "content": ANSWER[lang], "model": "demo-gpt-test", "ts": now - 40000}]}])


def shoot(browser, servers, design: str, lang: str, theme: str, width: int, height: int, problems: list) -> None:
    import httpx
    seed_chat(lang)
    with httpx.Client(timeout=20, trust_env=False) as client:
        client.post(servers.pg + "/api/pg/preferences", json={
            "language": lang, "theme": theme, "ui_design": design, "ui_motion": "reduced", "profile_onboarded": True,
            "profile_name": "Alex", "updates_enabled": False})
    ctx = browser.new_context(viewport={"width": width, "height": height}, bypass_csp=True)
    ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); localStorage.setItem('memu_theme', '{theme}');"
                        f" localStorage.setItem('memu_design', '{design}'); }} catch (_) {{}}")
    page = ctx.new_page()
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    tag = f"{design}-{lang}-{theme}" + ("" if width > 1000 else f"-w{width}")
    ready = "document.body.classList.contains('boot-ready') && typeof showSettings === 'function'"

    def step(name: str, code: str = "", wait: float = 0.7) -> None:
        for attempt in (1, 2):
            try:
                page.wait_for_function(ready, timeout=90000)
                if code:
                    page.evaluate(code)
                time.sleep(wait)
                page.screenshot(path=str(OUT / f"{tag}-{name}.png"))
                return
            except Exception as exc:     # the window may reload itself once after a preference change
                if attempt == 2:
                    problems.append(f"{tag} {name}: {str(exc)[:160]}")
                time.sleep(1.5)

    page.goto(servers.pg + "/", wait_until="domcontentloaded")
    step("home", "", 1.6)
    step("chat", "openChat('c_look1'); document.getElementById('input').value = '"
         + ("Покажи пример для 1000+ таблиц" if lang == "ru" else "Show an example for 1000+ tables")
         + "'; document.getElementById('input').dispatchEvent(new Event('input', {bubbles: true}))", 1.0)
    step("settings", "showSettings()", 1.0)
    step("appearance", "openSettingsPage('appearance')", 0.8)
    step("panels", "openSettingsPage('panels')", 1.2)
    # MemuCode start screen (at 720 px the right panel is a drawer and must start closed)
    page.evaluate("typeof closePanel === 'function' && closePanel()")
    step("code", "document.getElementById('nav-code').click()", 1.2)
    page.keyboard.press("Escape")      # the first-run intro
    step("code", "", 0.5)
    if errors:
        problems.append(f"{tag} page errors: {errors[:3]}")
    ctx.close()


def main(argv: list) -> int:
    from ui_lang_scan import Servers, chromium
    from app import themes
    designs = [d["id"] for d in themes.DESIGNS]
    langs, narrow_only = ["ru", "en"], False
    args = iter(argv)
    for arg in args:
        if arg == "--designs":
            designs = next(args).split(",")
        elif arg == "--langs":
            langs = next(args).split(",")
        elif arg == "--narrow-only":
            narrow_only = True
    OUT.mkdir(parents=True, exist_ok=True)
    servers = Servers().start()
    problems: list = []
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            exe = chromium()
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
            for design in designs:
                for lang in langs:
                    for theme in ("light", "dark"):
                        if not narrow_only:
                            shoot(browser, servers, design, lang, theme, 1360, 860, problems)
                        if design in ("classic", "glass", "soft", "grid", "terminal", "brutal"):
                            shoot(browser, servers, design, lang, theme, 720, 820, problems)
            browser.close()
    finally:
        servers.stop()
    print("\n".join(problems) or "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
