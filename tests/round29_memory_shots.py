"""Round 29 (r29-memory): headless Chromium check + screenshots of the Memory v2 panel.

    MEMU_TEST_CHROMIUM=/usr/bin/chromium PYTHONUTF8=1 python3 tests/round29_memory_shots.py OUT_DIR [design ...]

Real servers (tests/ui_lang_scan.Servers) over a temporary data dir; memory is seeded through /api/memory/*
(manual facts, AI facts with sources and confidence, an «Only for» label, pending facts, a rule, a deleted fact).
For every design × light/dark × ru/en the panel is opened on each tab and checked (exit code 1 on a problem):
  * no page errors;
  * nothing inside the panel is wider than the panel (horizontal overflow) and no text overflows its card;
  * in English no Cyrillic letter is visible in the panel;
  * the text colour of fact cards differs enough from their background (a crude contrast check).
Screens mem-<design>-<theme>-<lang>-<tab>.png in OUT_DIR.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DESIGNS = ["default", "aurora", "brutal", "focus", "glass", "grid", "soft", "terminal"]
#: user content (seeded in Russian) may stay Cyrillic in the English UI
USER_TEXT = ["Пишу на Python и TypeScript, люблю краткие ответы", "Работаю фронтенд-разработчиком в небольшой студии",
             "Пишу системные утилиты на Rust", "Предпочитает тёмную тему и моноширинный шрифт JetBrains Mono",
             "Готовится к марафону осенью", "План тренировок на осень", "Я готовлюсь к марафону в октябре",
             "Возможно, переезжает в Лиссабон", "Принимает витамин D по утрам", "думаю о переезде",
             "Не запоминай ничего о финансах", "Временный факт для корзины",
             # Round 40: associations, a contradiction, a moved city and the session diary
             "Не переносит лактозу", "молоко", "кофе", "капучино", "сыр", "Любит кофе по утрам", "Не пьёт кофе",
             "Живёт в Москве", "Переехала в Казань", "Настраивали парсер вакансий hh.ru в Планировании",
             "Парсер вакансий", "Выбирали ноутбук для монтажа видео", "Ноутбук"]
TABS = ["facts", "inbox", "diary", "rules", "settings", "trash"]

CHECK_JS = r"""() => {
  const panel = document.querySelector('#panel.mem2-panel');
  if (!panel) return { open: false };
  const pr = panel.getBoundingClientRect();
  const wide = [];
  panel.querySelectorAll('*').forEach((n) => {
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const cs = getComputedStyle(n);
    if (cs.visibility === 'hidden' || cs.display === 'none') return;
    if (r.right > pr.right + 2 || r.left < pr.left - 2) {
      // inside a horizontally scrollable strip (tabs) is fine
      let p = n.parentElement, scrolls = false;
      while (p && p !== panel) { const s = getComputedStyle(p); if (/(auto|scroll|hidden)/.test(s.overflowX) && p.getBoundingClientRect().right <= pr.right + 2) { scrolls = true; break; } p = p.parentElement; }
      if (!scrolls) wide.push((n.className && String(n.className).slice(0, 40)) || n.tagName);
    }
  });
  const text = panel.innerText || '';
  const lum = (c) => { if (!/^rgb/.test(c)) return null; const m = c.match(/\d+(\.\d+)?/g); if (!m) return 1; const [r, g, b] = m.slice(0, 3).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const bgOf = (n) => { while (n) { const c = getComputedStyle(n).backgroundColor; if (c && !/rgba\(0, 0, 0, 0\)|transparent/.test(c)) { const a = c.match(/rgba\([^)]*,\s*([\d.]+)\)/); if (!a || +a[1] > 0.6) return c; } n = n.parentElement; } return 'rgb(255,255,255)'; };
  const low = [];
  panel.querySelectorAll('.mem2-text, .mem2-fact p, .mem2-fact .mem2-body, .mem2-tab span').forEach((n) => {
    if (!n.innerText || !n.innerText.trim()) return;
    if (/text/.test(getComputedStyle(n).backgroundClip || '')) return;   // gradient text
    const a = lum(getComputedStyle(n).color), b = lum(bgOf(n));
    if (a === null || b === null) return;
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio < 3) low.push(n.innerText.trim().slice(0, 30) + ' ' + ratio.toFixed(2));
  });
  return { open: true, wide: [...new Set(wide)].slice(0, 8), cyr: text.split('\n').map((l) => l.trim()).filter((l) => /[А-Яа-яЁё]/.test(l)), low: low.slice(0, 5) };
}"""


def seed(client, pg: str) -> None:
    post = lambda path, body: client.post(pg + path, json=body).json()  # noqa: E731
    post("/api/memory/items", {"text": "Пишу на Python и TypeScript, люблю краткие ответы", "category": "tech", "pinned": True})
    post("/api/memory/items", {"text": "Работаю фронтенд-разработчиком в небольшой студии", "category": "work"})
    post("/api/memory/items", {"text": "Пишу системные утилиты на Rust", "category": "tech", "scope": ["code"]})
    post("/api/memory/propose", {"service": "chat", "facts": [
        {"fact": "Предпочитает тёмную тему и моноширинный шрифт JetBrains Mono", "category": "preference", "confidence": 0.92},
        {"fact": "Готовится к марафону осенью", "category": "goal", "confidence": 0.7}],
        "source": {"chat_id": "c-demo", "chat_title": "План тренировок на осень", "excerpt": "Я готовлюсь к марафону в октябре"}})
    post("/api/memory/propose", {"service": "telegram", "facts": [
        {"fact": "Возможно, переезжает в Лиссабон", "category": "identity", "confidence": 0.4},
        {"fact": "Принимает витамин D по утрам", "category": "health", "confidence": 0.9}],
        "source": {"chat_title": "Telegram", "excerpt": "думаю о переезде"}})
    post("/api/memory/rules", {"text": "Не запоминай ничего о финансах"})
    out = post("/api/memory/items", {"text": "Временный факт для корзины", "category": "other"})
    doc = client.get(pg + "/api/memory").json()
    for item in doc.get("items", []):
        if item.get("text") == "Временный факт для корзины":
            client.delete(pg + "/api/memory/items/" + item["id"])
    del out
    # Round 40 (r40-memory)
    post("/api/memory/propose", {"service": "chat", "by": "user", "facts": [
        {"fact": "Не переносит лактозу", "category": "health", "concepts": ["молоко", "кофе", "капучино", "сыр", "milk", "dairy"]}]})
    post("/api/memory/items", {"text": "Любит кофе по утрам", "category": "preference"})
    post("/api/memory/propose", {"service": "chat", "facts": [{"fact": "Не пьёт кофе", "category": "preference", "confidence": 0.9}]})
    post("/api/memory/items", {"text": "Живёт в Москве", "category": "identity"})
    post("/api/memory/propose", {"service": "chat", "facts": [{"fact": "Переехала в Казань", "category": "identity", "confidence": 0.9}]})
    from app import memory_v2   # the diary is written by the background worker; seed it directly (same data dir)
    day = 86400_000
    memory_v2.save_diary({"text": "Настраивали парсер вакансий hh.ru в Планировании", "chat_id": "c-hh", "chat_title": "Парсер вакансий",
                          "at": memory_v2.now_ms() - 2 * day, "msgs": 14})
    memory_v2.save_diary({"text": "Выбирали ноутбук для монтажа видео", "chat_id": "c-nb", "chat_title": "Ноутбук",
                          "at": memory_v2.now_ms() - 5 * day, "msgs": 9})


def main(out: Path, designs: list[str]) -> int:
    import httpx
    from playwright.sync_api import sync_playwright
    from ui_lang_scan import Servers
    out.mkdir(parents=True, exist_ok=True)
    servers = Servers().start()
    problems: list[str] = []
    errors: list[str] = []
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex", "language": "ru"})
            seed(client, servers.pg)
            with sync_playwright() as play:
                browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
                for lang in ("ru", "en"):
                    client.post(servers.pg + "/api/pg/preferences", json={"language": lang})
                    page = browser.new_page(viewport={"width": 1280, "height": 860}, bypass_csp=True)
                    page.on("pageerror", lambda e, lang=lang: errors.append(f"{lang}: {e}"))
                    page.goto(servers.pg + "/")
                    page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
                    page.wait_for_timeout(800)
                    for design in designs:
                        for theme in ("light", "dark"):
                            page.evaluate(f"""() => {{ try {{ setTheme('{theme}', false); }} catch (e) {{}}
                              if ('{design}' === 'default') delete document.documentElement.dataset.design; else document.documentElement.dataset.design = '{design}'; }}""")
                            page.wait_for_timeout(1200)    # the theme switch animates body colours
                            for tab in TABS:
                                page.evaluate(f"MemuMemory.open({{tab: '{tab}'}})")
                                page.wait_for_selector("#panel.mem2-panel .mem2-tabs", timeout=15000)
                                page.wait_for_timeout(350)
                                res = page.evaluate(CHECK_JS)
                                tag = f"{design}-{theme}-{lang}-{tab}"
                                if not res.get("open"):
                                    problems.append(f"{tag}: panel not open")
                                    continue
                                if res["wide"]:
                                    problems.append(f"{tag}: overflow {res['wide']}")
                                cyr = [line for line in res["cyr"] if not any(part and part in line for part in USER_TEXT)
                                       and not any(line in part for part in USER_TEXT)]
                                if lang == "en" and cyr:
                                    problems.append(f"{tag}: Cyrillic {cyr[:5]}")
                                if res["low"]:
                                    problems.append(f"{tag}: low contrast {res['low']}")
                                if design in ("default", "glass", "terminal", "brutal") or tab == "facts":
                                    page.screenshot(path=str(out / f"mem-{tag}.png"))
                            page.keyboard.press("Escape")
                    page.close()
                browser.close()
    finally:
        servers.stop()
    for line in problems + errors:
        print("FAIL:", line)
    print(f"done: {len(problems)} problems, {len(errors)} page errors")
    return 1 if problems or errors else 0


if __name__ == "__main__":
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r29-mem-"))
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mem-shots")
    sys.exit(main(target, sys.argv[2:] or DESIGNS))
