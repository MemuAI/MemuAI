"""Round 21 performance: regression tests for the incremental streaming render.

The streaming frame re-renders only the tail of the answer (``renderStreamingBody``);
blocks before a safe boundary are rendered once and kept. These tests load the REAL UI
(tests/appearance_fixture.py, mocked API) in headless Chromium and check, at every step of
typing several tricky answers character by character, that the incremental DOM is exactly
what a full ``answerHtml()`` render produces. A second test covers the table placeholder fix
(a table directly followed by text used to leave «@@BLOCK0@@» on screen).
Skipped when Playwright or a Chromium build is not installed.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
                      "/workspace/work/chromium/chrome"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    sync_playwright = None

CODE = "```python\nimport json\n\n\ndef load(path):\n    with open(path) as f:\n        return json.load(f)\n```"
TABLE = "| Параметр | Python | Rust |\n|---|---|---|\n| Скорость | средняя | высокая |\n| `async` | asyncio | tokio |"
MATH = r"Формула: $F_n = F_{n-1} + F_{n-2}$ и $$\int_0^1 x^2\,dx = \frac{1}{3}$$ а также $e^{i\pi}+1=0$."
LIST = "1. **Первый** — с [ссылкой](https://example.com).\n2. Второй с `code`.\n   - вложенный\n   - ещё\n3. Третий."
PARA = "Обычный абзац с **жирным**, `кодом` и рассуждением о производительности. " * 2
TEXTS = [
    "\n\n".join(["## Раздел 1", PARA, CODE, TABLE, "## Раздел 2", MATH, LIST, PARA, "```js\nconst a = 1;\n```", PARA]),
    "Шаги:\n\n1. Первый\n\n2. Второй\n\nАбзац после списка.\n\n3. Снова\n\n- маркер\n\n  - вложенный\n\nКонец.",
    "Формула:\n\n$$\na^2\n\nb^2\n$$\n\nТекст после $x$ и \\[\ny\n\n\\]\n\nещё.",
    "Код:\n\n```python\ndef f():\n\n    return 1\n```\n\nПосле кода.\n\n```\nunclosed\n\nstill code",
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n| c | d |\n|---|---|\n| 3 | 4 |\n\n> цитата\n> вторая\n\n> новая\n\n# Заголовок\n\n---\n\nтекст",
    "a\n\n\n\nb\n\n\n\n```js\nx\n```\n\n\n1) один\n\n2) два\n\nтекст [ссылка](https://example.com) и **жирный**\n\n<think>скрыто</think>видимо\n\nконец",
]


@unittest.skipUnless(sync_playwright is not None and _chromium(), "Playwright/Chromium is not installed")
class StreamingRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.appearance_fixture import build_fixture
        cls._tmp = tempfile.mkdtemp(prefix="memu-stream-")
        fixture = build_fixture(Path(cls._tmp))
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 820})
        cls.page.goto(fixture.as_uri(), wait_until="domcontentloaded")
        deadline = time.time() + 90
        while time.time() < deadline:
            if cls.page.evaluate("document.body.classList.contains('boot-ready') && typeof renderStreamingBody === 'function'"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("MemuAI UI did not boot")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            cls._pw.stop()
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_incremental_render_equals_full_render(self):
        result = self.page.evaluate("""(texts) => {
          let steps = 0, committed = 0; const mism = [];
          for (const [ti, text] of texts.entries()) {
            const div = document.createElement('div');
            const m = { role: 'assistant', streaming: true };
            const stride = text.length > 1500 ? 3 : 1;
            for (let k = 1; k <= text.length + stride - 1; k += stride) {
              const visible = visibleAnswer(text.slice(0, Math.min(k, text.length)));
              mdCacheBypass = true;
              try { renderStreamingBody(div, m, visible); } finally { mdCacheBypass = false; }
              const ref = document.createElement('div'); ref.innerHTML = answerHtml(m, visible);
              steps++;
              if (div._memuStream && div._memuStream.source) committed++;
              if (div.innerHTML !== ref.innerHTML && mism.length < 3)
                mism.push({ ti, k, got: div.innerHTML.slice(-240), want: ref.innerHTML.slice(-240) });
            }
          }
          return { steps, committed, mism };
        }""", TEXTS)
        self.assertEqual(result["mism"], [], result["mism"])
        self.assertGreater(result["steps"], 1000)
        # The point of the incremental path: most frames keep already-rendered blocks.
        self.assertGreater(result["committed"], result["steps"] // 3)

    def test_finished_message_uses_full_render(self):
        same = self.page.evaluate("""(text) => {
          const div = document.createElement('div');
          renderStreamingBody(div, { role: 'assistant', streaming: true }, text.slice(0, 200));
          const done = { role: 'assistant', streaming: false };
          renderStreamingBody(div, done, text);
          const ref = document.createElement('div'); ref.innerHTML = answerHtml(done, text);
          return div.innerHTML === ref.innerHTML && div._memuStream === null;
        }""", TEXTS[0])
        self.assertTrue(same)

    def test_table_followed_by_text_has_no_placeholder(self):
        html = self.page.evaluate("""() => [
          md('| a | b |\\n|---|---|\\n| 1 | 2 |\\nтекст сразу под таблицей'),
          md('| a | b |\\n|---|---|\\n| 1 | 2 |\\n| 3 |'),
          md('| a | b |\\n|---|---|\\n| 1 | 2 |'),
        ]""")
        for chunk in html:
            self.assertNotIn("@@BLOCK", chunk)
            self.assertIn("<table>", chunk)
        self.assertIn("текст сразу под таблицей", html[0])


if __name__ == "__main__":
    unittest.main()
