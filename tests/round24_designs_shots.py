"""Round 24 design screenshots: the REAL UI (appearance_fixture) with a fixture chat, the settings
window, an open menu and the Home panels page, for every design x light/dark.

Run: python tests/round24_designs_shots.py [design ...]   -> /workspace/work/shots/designs/*.png
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(__import__("os").environ.get("R24_SHOTS", "/workspace/work/shots/designs-r24"))

NOW = int(time.time() * 1000)
ANSWER = """## План миграции

Коротко: переносим сервис **поэтапно**, без простоя. Вот что нужно сделать:

1. Поднять реплику базы и включить логическую репликацию.
2. Переключить чтение, затем запись.
3. Удалить старый кластер через неделю.

```python
async def migrate(db, replica):
    await replica.sync(db, mode="logical")
    for table in await db.tables():
        print(f"✓ {table.name}: {table.rows} строк")
    return True
```

| Этап | Время | Риск |
|------|-------|------|
| Реплика | 2 ч | Низкий |
| Переключение | 10 мин | Средний |
| Очистка | 1 ч | Низкий |

> Совет: держите `rollback.sh` под рукой. Подробнее — [в документации](https://example.com).
"""
CHATS = [
    {"id": "c_fixture1", "title": "Миграция базы данных", "created": NOW - 60000, "updated": NOW, "mode": "chat",
     "messages": [
         {"role": "user", "content": "Как перенести PostgreSQL на новый кластер без простоя?", "ts": NOW - 50000},
         {"role": "assistant", "content": ANSWER, "model": "demo-claude", "ts": NOW - 40000},
         {"role": "user", "content": "Спасибо! А если таблиц больше тысячи?", "ts": NOW - 30000},
     ]},
    {"id": "c_fixture2", "title": "Рецепт борща", "created": NOW - 900000, "updated": NOW - 800000, "mode": "chat",
     "messages": [{"role": "user", "content": "борщ", "ts": NOW - 900000}, {"role": "assistant", "content": "Вот рецепт.", "ts": NOW - 890000}]},
    {"id": "c_fixture3", "title": "Идеи для подарка на день рождения", "created": NOW - 9e7, "updated": NOW - 9e7, "mode": "chat",
     "messages": [{"role": "user", "content": "идеи", "ts": NOW - 9e7}, {"role": "assistant", "content": "Список.", "ts": NOW - 9e7}]},
    {"id": "c_fixture4", "title": "Перевод письма на английский", "created": NOW - 3e8, "updated": NOW - 3e8, "mode": "chat",
     "messages": [{"role": "user", "content": "перевод", "ts": NOW - 3e8}, {"role": "assistant", "content": "Готово.", "ts": NOW - 3e8}]},
]


def panels_snapshot():
    try:
        from tests.panels_r23_browser_test import snapshot, todo_item
        store = {"lists": [{"id": "l_work000001", "name": "Работа", "color": "blue", "created": NOW / 1000, "items": [
            todo_item("t_first00001", "Позвонить в банк", priority=1), todo_item("t_second0001", "Ревью PR", tags=["код"])]}], "seq": 0}
        return snapshot(store)
    except Exception as exc:  # pragma: no cover
        print("panels snapshot unavailable:", exc)
        return {"panels": [], "dismissed": [], "templates": [], "settings": {"enabled": True}}


def build(tmp: Path) -> Path:
    from tests.appearance_fixture import build_fixture
    fixture = build_fixture(tmp)
    html = fixture.read_text(encoding="utf-8")
    html = html.replace("value = {chats:[]}", "value = {chats: " + json.dumps(CHATS, ensure_ascii=False) + "}", 1)
    extra = ("else if (path.startsWith('/api/pg/panels-todo')) value = {lists: []};\n"
             "  else if (path === '/api/pg/panels') value = __PANELS__;\n"
             "  else if (path === '/api/pg/themes')").replace("__PANELS__", json.dumps(panels_snapshot(), ensure_ascii=False))
    html = html.replace("else if (path === '/api/pg/themes')", extra, 1)
    fixture.write_text(html, encoding="utf-8")
    return fixture


def main(designs):
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="memu-r24d-"))
    fixture = build(tmp)
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for design in designs:
            for theme in ("light", "dark"):
                ctx = browser.new_context(viewport={"width": 1360, "height": 860})
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(f"""try {{ localStorage.setItem('memu_theme', '{theme}');
                  localStorage.setItem('memu_design', '{design}');
                  localStorage.setItem('qa_prefs', JSON.stringify({{theme: '{theme}', ui_design: '{design}', ui_motion: 'reduced'}})); }} catch (_) {{}}""")
                page.goto(fixture.as_uri(), wait_until="domcontentloaded")
                page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
                page.evaluate(f"setTheme && setTheme('{theme}', false)")
                page.evaluate(f"document.documentElement.dataset.design = '{design}'")
                tag = f"{design}-{theme}"
                time.sleep(0.6)
                page.screenshot(path=str(OUT / f"{tag}-home.png"))
                page.evaluate("openChat('c_fixture1')")
                time.sleep(0.4)
                page.fill("#input", "Покажи пример для 1000+ таблиц с параллельной синхронизацией")
                page.evaluate("document.querySelector('#input').dispatchEvent(new Event('input', {bubbles:true}))")
                time.sleep(0.4)
                page.screenshot(path=str(OUT / f"{tag}-chat.png"))
                # an open menu: the chat row context menu or the model menu
                try:
                    btn = page.locator("#model-dd-btn").first
                    btn.click(timeout=2000)
                    time.sleep(0.4)
                    page.screenshot(path=str(OUT / f"{tag}-menu.png"))
                    page.keyboard.press("Escape")
                except Exception as exc:
                    problems.append(f"{tag} menu: {exc}")
                for attempt in range(3):
                    try:
                        page.wait_for_function("document.body.classList.contains('boot-ready') && typeof showSettings === 'function'", timeout=60000)
                        page.evaluate("showSettings()")
                        break
                    except Exception as exc:  # the fixture page occasionally reloads itself once
                        print(f"{tag}: retry settings ({exc.__class__.__name__}) url={page.url[:60]}")
                        time.sleep(1.5)
                try:
                    time.sleep(0.6)
                    page.screenshot(path=str(OUT / f"{tag}-settings.png"))
                    page.evaluate("(window.openSettingsPage||window.showSettingsPage||function(){})('appearance')")
                    btn = page.locator("[data-settings-page='appearance'], [data-page='appearance'], [data-tab='appearance']").first
                    if btn.count():
                        btn.click(timeout=2000)
                    time.sleep(0.5)
                    card = page.locator(".design-options").first
                    if card.count():
                        card.scroll_into_view_if_needed()
                        time.sleep(0.3)
                    page.screenshot(path=str(OUT / f"{tag}-appearance.png"))
                except Exception as exc:
                    problems.append(f"{tag} settings: {exc}")
                if errors:
                    problems.append(f"{tag} errors: {errors[:3]}")
                ctx.close()
        browser.close()
    print("\n".join(problems) or "ok")


if __name__ == "__main__":
    main(sys.argv[1:] or ["glass"])
